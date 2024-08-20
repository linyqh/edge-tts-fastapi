"""
异步+代理池 生成语音
"""
import os
import requests
import redis
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import edge_tts

app = FastAPI()

# 初始化 Redis 连接
redis_client = redis.Redis(host='127.0.0.1', port=6379, db=1, decode_responses=True)


def load_all_proxies():
    """从代理池加载所有可用代理到 Redis"""
    try:
        response = requests.get("http://127.0.0.1:5010/all/")
        if response.status_code == 200:
            proxies = [proxy.get("proxy") for proxy in response.json()]
            if proxies:
                redis_client.delete("proxy_pool")  # 清空现有的代理池
                for proxy in proxies:
                    redis_client.rpush("proxy_pool", proxy)
    except requests.RequestException:
        print("无法加载代理池数据")


def get_proxy():
    """从 Redis 中获取下一个代理"""
    return redis_client.lpop("proxy_pool")


def reset_proxy():
    """加载代理池并设置初始代理"""
    load_all_proxies()
    current_proxy = get_proxy()
    if current_proxy:
        os.environ["http_proxy"] = current_proxy
        os.environ["https_proxy"] = current_proxy
        print(f"代理已设置: {current_proxy}")
    else:
        os.environ["http_proxy"] = ""
        os.environ["https_proxy"] = ""
        print("代理已置空")


def convert_rate_to_percent(rate: float) -> str:
    if rate == 1.0:
        return "+0%"
    percent = round((rate - 1.0) * 100)
    return f"+{percent}%" if percent > 0 else f"{percent}%"


async def generate_tts_stream(text: str, voice_name: str, rate_str: str):
    communicate = edge_tts.Communicate(text, voice_name, rate=rate_str)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


@app.get("/tts")
async def tts_endpoint(
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率")
):
    rate_str = convert_rate_to_percent(voice_rate)

    # 设置重试次数
    total_proxies = redis_client.llen("proxy_pool") + 1  # 包含初始代理和代理池中所有代理

    for attempt in range(total_proxies):
        try:
            # audio_stream = generate_tts_stream(text, voice_name, rate_str)
            return "hello"
            # return StreamingResponse(audio_stream, media_type="audio/mpeg")
        except Exception as e:
            print(f"请求失败，尝试更换代理: {e}")

            # 请求失败时，更换代理
            current_proxy = get_proxy()

            if current_proxy:
                os.environ["http_proxy"] = current_proxy
                os.environ["https_proxy"] = current_proxy
                print(f"已更换代理: {current_proxy}")
            else:
                # 代理池已耗尽，置空代理
                os.environ["http_proxy"] = ""
                os.environ["https_proxy"] = ""
                print("代理已置空，无法再更换")
                break

    # 如果代理池用完且仍然失败，返回服务不可用错误
    raise HTTPException(status_code=503, detail="无法处理请求，代理池已耗尽")


if __name__ == "__main__":
    reset_proxy()  # 启动时加载代理
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=os.getenv("PORT", 8003))
