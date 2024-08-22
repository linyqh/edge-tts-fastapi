import os
from redis import Redis


# 初始化 Redis 客户端
def get_redis_client():
    return Redis(host=os.getenv("REDIS_HOST", "127.0.0.1"), port=6379, db=1, decode_responses=True)
