import traceback

from fastapi import APIRouter, Query, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from app import logger
from app.utils import convert_rate_to_percent
from app.dependencies import get_redis_client, get_s3_client
import os
import uuid
import redis
import boto3
import edge_tts
import subprocess
from mutagen.mp3 import MP3

router = APIRouter()


async def generate_tts_stream(text: str, voice_name: str, rate_str: str, volume: str):
    communicate = edge_tts.Communicate(text, voice_name, rate=rate_str, volume=volume)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


async def generate_tts_with_duration(text: str, voice_name: str, rate: float, volume: str):
    rate_str = convert_rate_to_percent(rate)
    communicate = edge_tts.Communicate(text, voice_name, rate=rate_str, volume=volume)
    sub_maker = edge_tts.SubMaker()
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            sub_maker.create_sub((chunk["offset"], chunk["duration"]), chunk["text"])
    return audio_data, sub_maker


def get_audio_duration(sub_maker: edge_tts.SubMaker, weight: float = 1.0):
    if not sub_maker.offset:
        return 0.0
    return round(sub_maker.offset[-1][1] / 10000000, 8) + weight     # 这个加的1s是tts生成的音频和实际的音频时长差


async def adjust_rate_for_duration(text: str, voice_name: str, volume: str, target_duration: float, weight: float,
                                   max_iterations: int = 5):
    current_rate = 1
    # 迭代法
    for index in range(max_iterations):
        if index == 0:
            audio_data, sub_maker = await generate_tts_with_duration(text, voice_name, 1, volume)
            current_duration = get_audio_duration(sub_maker, weight)
            current_rate = round(current_duration / target_duration, 2)
        else:
            audio_data, sub_maker = await generate_tts_with_duration(text, voice_name, current_rate, volume)
            current_duration = get_audio_duration(sub_maker, weight)

        if current_duration <= target_duration:
            return audio_data, current_rate, current_duration
        else:
            current_rate += 0.1

    # 如果达到最大迭代次数仍未达到标，返回最接近的结果
    raise Exception("当前语速超出最大语速速率范围")


@router.get("/tts", summary="语音合成", description="将文本转换为语音，并返回语音流")
async def tts_endpoint(
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        max_duration: float = Query(None, description="最大音频时长（秒），精确到秒后两位"),
        weight: float = Query(1.0, description="权重值"),
        redis: redis.Redis = Depends(get_redis_client)
):
    try:
        if max_duration is not None:
            max_duration = round(max_duration, 2)  # 确保最大时长精确到秒后两位

        if max_duration is None:
            rate_str = convert_rate_to_percent(voice_rate)
            audio_stream = generate_tts_stream(text, voice_name, rate_str, voice_volume)
            return StreamingResponse(audio_stream, media_type="audio/mpeg")
        else:
            audio_data, adjusted_rate, tts_duration = await adjust_rate_for_duration(text, voice_name, voice_volume,
                                                                                     max_duration, weight)
            logger.info(f"调整的语速为 {adjusted_rate}, TTS 音频时长为 {tts_duration}")
            if adjusted_rate < 0.1 or adjusted_rate > 2:
                raise HTTPException(status_code=400, detail="当前字数超出最大或最小语速速率范围")

            return StreamingResponse(iter([audio_data]), media_type="audio/mpeg")
    except:
        logger.error(traceback.format_exc())
        return HTTPException(status_code=400, detail="当前字数超出最大或最小语速速率范围")

# 任务相关数据的 Redis 键前缀
TASK_PREFIX = "tts_task:"


async def save_audio_task(
        task_id: str,
        text: str,
        voice_name: str,
        rate_str: str,
        volume: str,
        mp3gain_params: str,
        redis: redis.Redis,
        bucket_name: str,
        directory_name: str,
        weight: float,
        s3_client: boto3.client
):
    try:
        # Mark task as in progress
        logger.info(f"开始处理任务 {task_id}")
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "pending")

        # 使用相对路径
        audio_files_dir = "tmp/audio/files"
        os.makedirs(audio_files_dir, exist_ok=True)

        # 检查是否有最大时长限制
        max_duration = redis.hget(f"{TASK_PREFIX}{task_id}", "max_duration")
        if max_duration:
            max_duration = float(max_duration)
            audio_data, adjusted_rate, tts_duration = await adjust_rate_for_duration(text, voice_name, volume, max_duration, weight)
            logger.info(f"调整后的语速为 {adjusted_rate}, TTS 音频时长为 {tts_duration}")
            redis.hset(f"{TASK_PREFIX}{task_id}", "voice_rate", str(adjusted_rate))
            if adjusted_rate < 0.1 or adjusted_rate > 2:
                raise Exception("当前语速超出最大语速速率范围")
        else:
            # 生成无持续时间限制的 TTS 流
            audio_data = b""
            async for chunk in generate_tts_stream(text, voice_name, rate_str, volume):
                audio_data += chunk

        file_path = os.path.join(audio_files_dir, f"{task_id}.mp3")
        with open(file_path, "wb") as audio_file:
            audio_file.write(audio_data)

        # 使用mp3gain处理音频文件
        logger.info(f"开始处理音频文件 {file_path}")
        command = f"mp3gain {mp3gain_params} {file_path}"
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)

        if result.returncode != 0:
            error_message = f"MP3Gain处理失败: {result.stderr}"
            logger.error(error_message)
            redis.hset(f"{TASK_PREFIX}{task_id}", "status", "failed")
            redis.hset(f"{TASK_PREFIX}{task_id}", "message", error_message)
            raise Exception("MP3Gain处理失败")

        logger.info("MP3Gain处理完成")

        # 上传到S3
        if directory_name is None:
            object_name = f"{task_id}.mp3"
        else:
            object_name = f"{directory_name}/{task_id}.mp3"
        s3_client.upload_file(file_path, bucket_name, object_name)
        logger.info(f"文件 {file_path} 已上传到 S3/R2: {bucket_name} file:{object_name}")

        # Update task status in Redis
        logger.info(f"任务 {task_id} 处理完成")
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "completed")
        redis.hset(f"{TASK_PREFIX}{task_id}", "file_path", file_path)
        redis.hset(f"{TASK_PREFIX}{task_id}", "object_name", object_name)

    except subprocess.CalledProcessError as e:
        error_message = f"MP3Gain处理失败: {e.stderr}"
        logger.error(error_message)
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "failed")
        redis.hset(f"{TASK_PREFIX}{task_id}", "message", error_message)
        raise Exception("MP3Gain处理失败")
    except Exception as e:
        error_message = f"任务 {task_id} 处理失败: {e}"
        logger.error(traceback.format_exc())
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "failed")
        redis.hset(f"{TASK_PREFIX}{task_id}", "message", error_message)


@router.post("/create-audio-task", summary="创建音频任务", description="创建TTS音频生成任务并返回任务ID")
async def create_audio_task(
        background_tasks: BackgroundTasks,
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        mp3gain_params: str = Query("-r -c -d 8", description="MP3Gain参数，默认为'-r -c -d 8'"),
        max_duration: float = Query(None, description="最大音频时长（秒），精确到秒后两位"),
        redis: redis.Redis = Depends(get_redis_client),
        bucket_name: str = Query(..., description="S3桶名称 7mfitness-test"),
        directory_name: str = Query(default=None, description="S3目录名称, 默认为 / 根目录"),
        weight: float = Query(1.0, description="权重值"),
        s3_client: boto3.client = Depends(get_s3_client)
):
    task_id = str(uuid.uuid4())
    rate_str = convert_rate_to_percent(voice_rate)

    # Store initial task information in Redis
    redis.hset(f"{TASK_PREFIX}{task_id}", "status", "pending")
    redis.hset(f"{TASK_PREFIX}{task_id}", "voice_rate", "")
    redis.hset(f"{TASK_PREFIX}{task_id}", "message", "")  # 保存 message
    if max_duration is not None:
        redis.hset(f"{TASK_PREFIX}{task_id}", "max_duration", str(round(max_duration, 2)))

    # Add the TTS task to the background tasks
    background_tasks.add_task(save_audio_task, task_id, text, voice_name, rate_str, voice_volume, mp3gain_params, redis,
                              bucket_name, directory_name, weight, s3_client)

    return JSONResponse({"task_id": task_id, "status": "Task created successfully"})


@router.get("/audio-task/{task_id}", summary="查询任务结果", description="根据任务ID查询TTS生成任务的状态和音频文件")
async def get_audio_task_result(
        task_id: str,
        redis: redis.Redis = Depends(get_redis_client),
        mode: str = Query("url", description="选择模式：url 直接返回下载链接（默认）  stream 直接返回文件流")
):
    task_data = redis.hgetall(f"{TASK_PREFIX}{task_id}")

    if not task_data:
        raise HTTPException(status_code=404, detail="任务未找到")

    status = task_data.get("status", "未知状态")
    voice_rate = task_data.get("voice_rate", "未知语速")
    message = task_data.get("message", "")  # 获取任务的错误信息（如果有）

    if status == "completed":
        file_path = task_data.get("file_path", "")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=500, detail="音频文件丢失")

        # 获取音频时长
        audio = MP3(file_path)
        duration = round(audio.info.length, 2)  # 四舍五入到小数点后两位

        if mode == "stream":
            return StreamingResponse(open(file_path, "rb"), media_type="audio/mpeg")

        # 生成预签名的下载 URL
        object_name = task_data.get("object_name", None)
        if object_name is not None:
            download_url = f"https://amber.7mfitness.com/{object_name}"
        else:
            download_url = f"https://amber.7mfitness.com/{task_id}.mp3"
        return JSONResponse({
            "task_id": task_id,
            "status": status,
            "download_url": download_url,
            "duration": duration,
            "voice_rate": voice_rate,
            "message": message  # 返回错误信息（如果有）
        })

    return JSONResponse({
        "task_id": task_id,
        "status": status,
        "voice_rate": voice_rate,
        "message": message  # 返回错误信息（如果有）
    })
