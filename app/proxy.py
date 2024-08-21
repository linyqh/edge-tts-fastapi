import os
import redis
import requests
from app import logger


def load_all_proxies(get_redis_client: redis.Redis):
    try:
        response = requests.get("http://proxy_pool:5010/all/")
        if response.status_code == 200:
            proxies = [proxy.get("proxy") for proxy in response.json()]
            if isinstance(proxies, list):
                for proxy in proxies:
                    get_redis_client.rpush("proxy_pool", proxy)
    except requests.RequestException:
        pass


def get_proxy(get_redis_client: redis.Redis):
    return get_redis_client.lpop("proxy_pool")


def reset_proxy(get_redis_client: redis.Redis):
    load_all_proxies(get_redis_client)
    current_proxy = get_proxy(get_redis_client)
    if current_proxy:
        os.environ["http_proxy"] = current_proxy
        os.environ["https_proxy"] = current_proxy
        logger.info(f"代理已设置: {current_proxy}")
    else:
        os.environ["http_proxy"] = ""
        os.environ["https_proxy"] = ""
        logger.info("代理已置空")
