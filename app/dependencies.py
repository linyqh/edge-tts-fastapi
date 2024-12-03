import os
from redis import Redis, asyncio as aioredis
from fastapi import HTTPException
import boto3
import aioboto3
from botocore.exceptions import NoCredentialsError
import logging
import traceback
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

async def get_redis_client():
    """
    创建并返回一个异步Redis客户端
    """
    redis = aioredis.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=6379,
        db=1,
        decode_responses=True
    )
    return redis

def get_sync_redis_client():
    """
    创建并返回一个同步Redis客户端，用于不支持异步的场景
    """
    return Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"), 
        port=6379, 
        db=1, 
        decode_responses=True
    )

# 创建一个全局的 aioboto3 session
_session = aioboto3.Session()

@asynccontextmanager
async def get_s3_client():
    """
    创建并返回一个异步S3客户端
    """
    try:
        access_key = os.getenv('ACCESS_KEY_ID')
        secret_key = os.getenv('SECRET_ACCESS_KEY')
        endpoint_url = os.getenv('ENDPOINT_URL')
        
        logger.info(f"正在创建S3客户端，endpoint: {endpoint_url}")
        if not all([access_key, secret_key, endpoint_url]):
            logger.error("S3凭证缺失:")
            logger.error(f"ACCESS_KEY_ID: {'已设置' if access_key else '未设置'}")
            logger.error(f"SECRET_ACCESS_KEY: {'已设置' if secret_key else '未设置'}")
            logger.error(f"ENDPOINT_URL: {'已设置' if endpoint_url else '未设置'}")
            raise HTTPException(status_code=500, detail="S3凭证未完全配置")

        async with _session.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url
        ) as client:
            logger.info("S3客户端创建成功")
            yield client

    except Exception as e:
        logger.error(f"创建S3客户端失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"创建S3客户端失败: {str(e)}")

def get_s3_client_ctx():
    """
    返回S3客户端上下文管理器
    """
    return get_s3_client

def get_sync_s3_client():
    """
    创建并返回一个同步S3客户端，用于不支持异步的场景
    """
    try:
        return boto3.client(
            's3',
            aws_access_key_id=os.getenv('ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv("SECRET_ACCESS_KEY"),
            endpoint_url=os.getenv("ENDPOINT_URL")
        )
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="R2 bucket 凭证错误, 请检查环境变量 ACCESS_KEY_ID； "
                                                    "SECRET_ACCESS_KEY； ENDPOINT_URL 是否正确。")
