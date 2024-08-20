"""
异步生成语音
"""
import os
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
import edge_tts

app = FastAPI()


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
    audio_stream = generate_tts_stream(text, voice_name, rate_str)
    return StreamingResponse(audio_stream, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=os.getenv("PORT", 8002))
