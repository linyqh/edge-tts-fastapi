from fastapi import FastAPI
from app.tts import router as tts_router
from app.dependencies import get_redis_client
from app.utils import perform_initialization
import os
import uvicorn
from contextlib import asynccontextmanager


# lifespan 事件处理器
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在应用启动时执行的代码
    redis = get_redis_client()
    # 初始化代理池 # TODO: 代理池不稳定，暂时不用代理池
    # perform_initialization(redis)
    yield


app = FastAPI(lifespan=lifespan)

# 注册 TTS 路由
app.include_router(tts_router)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=os.getenv("PORT", 8000))
