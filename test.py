"""
异步请求测试
"""
import asyncio
import traceback

import httpx
import time
import random
import string
from statistics import mean

# 接口 URL，确保在测试前替换为实际接口地址
API_URL1 = "http://127.0.0.1:8001/tts"
API_URL2 = "http://127.0.0.1:8002/tts"
API_URL3 = "http://127.0.0.1:8003/tts"
API_URL4 = "http://127.0.0.1:8080/tts"

# 测试数据基础文本
base_text = "这是一个测试文本，用于比较接口在修改前后的性能。"
voice_name = "zh-TW-HsiaoYuNeural"
voice_rate = 1.0

# 并发请求数量
NUM_CONCURRENT_REQUESTS = 1000


def generate_unique_text():
    """生成一个带有随机字符的唯一文本，防止缓存影响"""
    random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"{base_text} 随机字符: {random_suffix}"


async def send_request(client, url):
    unique_text = generate_unique_text()
    params = {
        "text": unique_text,
        "voice_name": voice_name,
        "voice_rate": voice_rate
    }
    start_time = time.perf_counter()
    try:
        response = await client.get(url, params=params)
        duration = time.perf_counter() - start_time
        return duration, response.status_code
    except Exception as err:
        print(f"请求失败: {err} \n {traceback.format_exc()} \n")
        return None, None


async def test_performance(url):
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=10000, max_keepalive_connections=2000)) as client:
        tasks = [send_request(client, url) for _ in range(NUM_CONCURRENT_REQUESTS)]
        results = await asyncio.gather(*tasks)

    # 过滤出成功的请求结果
    valid_results = [result[0] for result in results if result[0] is not None and result[1] == 200]

    # 计算平均响应时间
    if valid_results:
        avg_response_time = mean(valid_results)
        success_rate = len(valid_results) / NUM_CONCURRENT_REQUESTS
        print(f"平均响应时间: {avg_response_time:.2f} 秒")
        print(f"成功率: {success_rate * 100:.2f}%")
    else:
        print("所有请求均失败")


def run_tests():
    print("测试原始接口性能:", API_URL1)
    asyncio.run(test_performance(API_URL1))

    print("\n测试修改后接口性能:", API_URL2)
    asyncio.run(test_performance(API_URL2))

    print("\n测试修改后接口性能:", API_URL2)
    asyncio.run(test_performance(API_URL3))

    print("\n测试修改后接口性能:", API_URL2)
    asyncio.run(test_performance(API_URL4))


if __name__ == "__main__":
    run_tests()
