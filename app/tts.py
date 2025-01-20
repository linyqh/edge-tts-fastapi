import traceback
import aiofiles
import asyncio
from fastapi import APIRouter, Query, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from app import logger
from app.utils import convert_rate_to_percent
from app.dependencies import get_redis_client, get_s3_client_ctx, get_sync_redis_client
import os
import uuid
from redis import Redis, asyncio as aioredis
import aioboto3
import edge_tts
from mutagen.mp3 import MP3
from dotenv import load_dotenv

load_dotenv()

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
    """
    根据字幕时间计算音频时长
    :param sub_maker: SubMaker对象
    :param weight: 时长权重
    :return: 音频时长（秒）
    """
    if not sub_maker.subs:
        return 0.0
    
    # 获取最后一个字幕的结束时间
    last_sub = sub_maker.subs[-1]
    duration = last_sub.end / 10000000  # 转换为秒
    return round(duration * weight, 2)


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


async def process_mp3gain(file_path: str, mp3gain_params: str) -> None:
    """
    异步处理MP3Gain
    """
    command = f"mp3gain {mp3gain_params} {file_path}"
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise Exception(f"MP3Gain处理失败: {stderr.decode()}")

    return None


async def upload_to_s3(file_path: str, bucket_name: str, object_name: str, s3_client) -> None:
    """
    异步上传文件到S3
    """
    logger.info(f"开始上传文件到S3: bucket={bucket_name}, object={object_name}")
    try:
        async with aiofiles.open(file_path, 'rb') as f:
            data = await f.read()
            logger.info(f"文件读取成功，大小: {len(data)} bytes")
        
        await s3_client.put_object(
            Bucket=bucket_name,
            Key=object_name,
            Body=data
        )
        logger.info("S3上传成功")
    except Exception as e:
        logger.error(f"S3上传失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise


async def save_audio_task(
        task_id: str,
        text: str,
        voice_name: str,
        voice_rate: str,
        voice_volume: str,
        mp3gain_params: str,
        redis: aioredis.Redis,
        bucket_name: str,
        directory_name: str,
        weight: float,
        s3_client_ctx
):
    """
    异步保存音频任务
    """
    file_path = f"/tmp/{task_id}.mp3"
    error_message = None

    try:
        # 检查是否有最大时长限制
        max_duration = await redis.hget(f"{TASK_PREFIX}{task_id}", "max_duration")
        if max_duration:
            max_duration = float(max_duration)
            audio_data, adjusted_rate, tts_duration = await adjust_rate_for_duration(text, voice_name, voice_volume,
                                                                                     max_duration, weight)
            logger.info(f"调整后的语速为 {adjusted_rate}, TTS 音频时长为 {tts_duration}")
            await redis.hset(f"{TASK_PREFIX}{task_id}", "voice_rate", str(adjusted_rate))
            await redis.hset(f"{TASK_PREFIX}{task_id}", "duration", str(tts_duration))
            if adjusted_rate < 0.1 or adjusted_rate > 2:
                raise Exception(f"无法调整语速到合适的范围内。当前语速为 {adjusted_rate}")
        else:
            communicate = edge_tts.Communicate(text, voice_name, rate=voice_rate, volume=voice_volume)
            sub_maker = edge_tts.SubMaker()
            async with aiofiles.open(file_path, "wb") as file:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        await file.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        sub_maker.process_bound(chunk)
            
            # 计算音频时长
            duration = get_audio_duration(sub_maker, weight)
            await redis.hset(f"{TASK_PREFIX}{task_id}", "duration", str(duration))

        # 使用异步MP3Gain处理音频文件
        logger.info(f"开始处理音频文件 {file_path}")
        try:
            # await process_mp3gain(file_path, mp3gain_params)
            logger.info("MP3Gain处理完成")
        except Exception as e:
            error_message = str(e)
            logger.error(f"MP3Gain处理失败: {error_message}")
            raise

        # 上传到S3/R2
        logger.info(f"开始上传文件到 S3/R2")
        if not directory_name:
            object_name = f"{task_id}.mp3"
        else:
            object_name = f"{directory_name}/{task_id}.mp3"

        try:
            async with s3_client_ctx() as s3_client:
                await upload_to_s3(file_path, bucket_name, object_name, s3_client)
            logger.info(f"文件 {file_path} 已上传到 S3/R2: {bucket_name} file:{object_name}")
        except Exception as e:
            error_message = str(e)
            logger.error(f"S3上传失败: {error_message}")
            raise

        # 更新任务状态
        await redis.hset(f"{TASK_PREFIX}{task_id}", mapping={
            "status": "completed",
            "object_name": object_name,
            "message": "处理成功"
        })

    except Exception as e:
        if not error_message:
            error_message = str(e)
        logger.error(f"处理音频任务失败: {error_message}")
        await redis.hset(f"{TASK_PREFIX}{task_id}", mapping={
            "status": "failed",
            "error": error_message,
            "message": error_message
        })
        raise Exception(error_message)

    finally:
        # 清理临时文件
        if os.path.exists(file_path):
            os.remove(file_path)


@router.get("/tts", summary="语音合成", description="将文本转换为语音，并返回语音流")
async def tts_endpoint(
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        max_duration: float = Query(None, description="最大音频时长（秒），精确到秒后两位"),
        weight: float = Query(1.0, description="权重值"),
        redis: aioredis.Redis = Depends(get_redis_client)
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


@router.post("/create-audio-task", summary="创建音频任务", description="创建TTS音频生成任务并返回任务ID")
async def create_audio_task(
        background_tasks: BackgroundTasks,
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        mp3gain_params: str = Query("-r -c -d 8", description="MP3Gain参数，默认为'-r -c -d 8'"),
        max_duration: float = Query(None, description="最大音频时长（秒），精确到秒后两位"),
        redis: aioredis.Redis = Depends(get_redis_client),
        bucket_name: str = Query(..., description="S3桶名称测试：7mfitness-test"),
        directory_name: str = Query(default=None, description="S3目录名称, 默认为 / 根目录"),
        weight: float = Query(1.0, description="权重值"),
        s3_client_ctx=Depends(get_s3_client_ctx)
):
    task_id = str(uuid.uuid4())
    rate_str = convert_rate_to_percent(voice_rate)
    directory_name = directory_name if directory_name is None else directory_name.strip("/")

    # Store initial task information in Redis
    await redis.hset(f"{TASK_PREFIX}{task_id}", "status", "pending")
    await redis.hset(f"{TASK_PREFIX}{task_id}", "voice_rate", "")
    await redis.hset(f"{TASK_PREFIX}{task_id}", "message", "")
    await redis.hset(f"{TASK_PREFIX}{task_id}", "bucket_name", bucket_name)
    if max_duration is not None:
        await redis.hset(f"{TASK_PREFIX}{task_id}", "max_duration", str(round(max_duration, 2)))

    # Add the TTS task to the background tasks
    background_tasks.add_task(save_audio_task, task_id, text, voice_name, rate_str, voice_volume, mp3gain_params, redis,
                              bucket_name, directory_name, weight, s3_client_ctx)

    return JSONResponse({"task_id": task_id, "status": "Task created successfully"})


@router.get("/audio-task/{task_id}", summary="获取任务结果", description="获取语音合成任务的结果")
async def get_audio_task_result(
        task_id: str,
        redis: aioredis.Redis = Depends(get_redis_client),
        s3_client_ctx=Depends(get_s3_client_ctx),
        mode: str = Query("url", description="选择模式：url 直接返回下载链接（默认）  stream 直接返回文件流")
):
    task_key = f"{TASK_PREFIX}{task_id}"
    task_exists = await redis.exists(task_key)

    if not task_exists:
        raise HTTPException(status_code=404, detail="Task not found")

    # 获取所有任务相关信息
    status = await redis.hget(task_key, "status")
    voice_rate = await redis.hget(task_key, "voice_rate") or ""
    message = await redis.hget(task_key, "message") or ""
    duration = await redis.hget(task_key, "duration") or 0
    
    if status == "failed":
        error = await redis.hget(task_key, "error")
        return {
            "task_id": task_id,
            "status": "failed",
            "download_url": "",
            "complete_download_url": "",
            "duration": float(duration),
            "voice_rate": voice_rate,
            "message": error or message
        }

    if status != "completed":
        return {
            "task_id": task_id,
            "status": status,
            "download_url": "",
            "complete_download_url": "",
            "duration": float(duration),
            "voice_rate": voice_rate,
            "message": message
        }

    object_name = await redis.hget(task_key, "object_name")
    if not object_name:
        raise HTTPException(status_code=404, detail="Audio file not found")

    # 从Redis中获取bucket_name
    bucket_name = await redis.hget(task_key, "bucket_name")
    if not bucket_name:
        raise HTTPException(status_code=500, detail="Bucket name not found in task data")

    if mode == "url":
        try:
            async with s3_client_ctx() as s3_client:
                url = await s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': bucket_name, 'Key': object_name},
                    ExpiresIn=3600
                )
                base_url = os.getenv("R2_BASE_URL", "").rstrip('/')
                download_url = object_name
                complete_download_url = f"{base_url}/{object_name}" if base_url else url
                
                return {
                    "task_id": task_id,
                    "status": "completed",
                    "download_url": download_url,
                    "complete_download_url": complete_download_url,
                    "duration": float(duration),
                    "voice_rate": voice_rate,
                    "message": message
                }
        except Exception as e:
            logger.error(f"生成预签名URL失败: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate presigned URL: {str(e)}")
    else:
        file_path = f"/tmp/{task_id}.mp3"
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="音频文件不存在")

        async with aiofiles.open(file_path, "rb") as f:
            audio_data = await f.read()
        return StreamingResponse(iter([audio_data]), media_type="audio/mpeg")
