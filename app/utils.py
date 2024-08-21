from redis import Redis
from app.proxy import reset_proxy


def convert_rate_to_percent(rate: float) -> str:
    if rate == 1.0:
        return "+0%"
    percent = round((rate - 1.0) * 100)
    return f"+{percent}%" if percent > 0 else f"{percent}%"


def perform_initialization(redis_client: Redis):
    """需要在项目启动时执行的初始化方法"""
    # 示例：设置一个初始值到 Redis 中
    reset_proxy(redis_client)
