from fastapi import APIRouter, Query, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from app import logger
from app.utils import convert_rate_to_percent
from app.proxy import get_proxy
from app.dependencies import get_redis_client
import os
import uuid
import redis
import edge_tts

router = APIRouter()


async def generate_tts_stream(text: str, voice_name: str, rate_str: str, volume: str):
    communicate = edge_tts.Communicate(text, voice_name, rate=rate_str, volume=volume)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


@router.get("/tts", summary="语音合成", description="将文本转换为语音，并返回语音流")
async def tts_endpoint(
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        redis: redis.Redis = Depends(get_redis_client)
):
    rate_str = convert_rate_to_percent(voice_rate)

    total_proxies = redis.llen("proxy_pool") + 1  # 包含初始代理和代理池中所有代理

    for attempt in range(total_proxies):
        try:
            audio_stream = generate_tts_stream(text, voice_name, rate_str, voice_volume)
            return StreamingResponse(audio_stream, media_type="audio/mpeg")
        except Exception as e:
            logger.info(f"请求失败，尝试更换代理: {e}")
            current_proxy = get_proxy(redis)
            if current_proxy:
                os.environ["http_proxy"] = current_proxy
                os.environ["https_proxy"] = current_proxy
                logger.info(f"已更换代理: {current_proxy}")
            else:
                os.environ["http_proxy"] = ""
                os.environ["https_proxy"] = ""
                logger.info("代理已置空，无法再更换")
                break

    raise HTTPException(status_code=503, detail="无法处理请求，代理池已耗尽")


# 任务相关数据的 Redis 键前缀
TASK_PREFIX = "tts_task:"


async def save_audio_task(task_id: str, text: str, voice_name: str, rate_str: str, volume: str, redis: redis.Redis):
    try:
        # Mark task as in progress
        logger.info(f"开始处理任务 {task_id}")
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "in_progress")

        # Generate TTS stream and save to a file
        audio_data = b""
        async for chunk in generate_tts_stream(text, voice_name, rate_str, volume):
            audio_data += chunk
        # 判断目录 /tmp/audio/files/ 是否存在，不存在则创建
        if not os.path.exists("tmp/audio/files/"):
            os.makedirs("tmp/audio/files/")
        # Save the audio file
        file_path = f"tmp/audio/files/{task_id}.mp3"
        with open(file_path, "wb") as audio_file:
            audio_file.write(audio_data)

        # Update task status and file path in Redis
        logger.info(f"任务 {task_id} 处理完成")
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "completed")
        redis.hset(f"{TASK_PREFIX}{task_id}", "file_path", file_path)

    except Exception as e:
        logger.error(f"任务 {task_id} 处理失败: {e}")
        redis.hset(f"{TASK_PREFIX}{task_id}", "status", "failed")


@router.post("/create-audio-task", summary="创建音频任务", description="创建TTS音频生成任务并返回任务ID")
async def create_audio_task(
        background_tasks: BackgroundTasks,
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率"),
        voice_volume: str = Query("+0%", description="音量百分比, 范围为-100% ~ +100%"),
        redis: redis.Redis = Depends(get_redis_client)
):
    task_id = str(uuid.uuid4())
    rate_str = convert_rate_to_percent(voice_rate)

    # Store initial task information in Redis
    redis.hset(f"{TASK_PREFIX}{task_id}", "status", "pending")

    # Add the TTS task to the background tasks
    background_tasks.add_task(save_audio_task, task_id, text, voice_name, rate_str, voice_volume, redis)

    return JSONResponse({"task_id": task_id, "status": "Task created successfully"})


@router.get("/audio-task/{task_id}", summary="查询任务结果", description="根据任务ID查询TTS生成任务的状态和音频文件")
async def get_audio_task_result(task_id: str, redis: redis.Redis = Depends(get_redis_client)):
    task_data = redis.hgetall(f"{TASK_PREFIX}{task_id}")

    if not task_data:
        raise HTTPException(status_code=404, detail="任务未找到")

    status = task_data.get("status", "未知状态")

    if status == "completed":
        file_path = task_data.get("file_path", "")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=500, detail="音频文件丢失")
        return StreamingResponse(open(file_path, "rb"), media_type="audio/mpeg")

    return JSONResponse({"task_id": task_id, "status": status})
