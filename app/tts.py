from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import StreamingResponse
from app import logger
from app.utils import convert_rate_to_percent
from app.proxy import get_proxy
from app.dependencies import get_redis_client
import os
import redis
import edge_tts

router = APIRouter()


async def generate_tts_stream(text: str, voice_name: str, rate_str: str, volume: str):
    communicate = edge_tts.Communicate(text, voice_name, rate=rate_str, volume=volume)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


@router.get("/tts")
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
