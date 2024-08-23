import os
# import aioredis
from redis import Redis


def get_redis_client():
    # redis = await aioredis.from_url(f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:6379/1")
    # return redis
    return Redis(host=os.getenv("REDIS_HOST", "127.0.0.1"), port=6379, db=1, decode_responses=True)

# async def get_redis_client() -> Redis:
#     redis = aioredis.from_url("redis://localhost:6379/1", decode_responses=True)
#     try:
#         yield redis
#     finally:
#         await redis.close()