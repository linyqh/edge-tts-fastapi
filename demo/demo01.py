"""
同步生成语音
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


@app.get("/tts")
async def tts_endpoint(
        text: str = Query(..., description="要转换的文本"),
        voice_name: str = Query("zh-TW-HsiaoYuNeural", description="语音名称"),
        voice_rate: float = Query(1.0, description="语速倍率")
):
    rate_str = convert_rate_to_percent(voice_rate)
    audio_stream = edge_tts.Communicate(text=text, voice=voice_name, rate=rate_str)
    # 将音频流作为响应返回
    with open("OUTPUT_FILE/test.mp3", "wb") as file:
        for chunk in audio_stream.stream_sync():
            if chunk["type"] == "audio":
                file.write(chunk["data"])
    # 将mp3文件转换为StreamingResponse输出
    audio_stream = open("OUTPUT_FILE/test.mp3", "rb")
    return StreamingResponse(audio_stream, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=os.getenv("PORT", 8001))
