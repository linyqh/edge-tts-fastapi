import os
from redis import Redis
from fastapi import HTTPException
import boto3
from botocore.exceptions import NoCredentialsError


def get_redis_client():
    """
    创建并返回一个 Redis 客户端，用于缓存。
    """
    return Redis(host=os.getenv("REDIS_HOST", "127.0.0.1"), port=6379, db=1, decode_responses=True)


def get_s3_client():
    """
    创建并返回一个 S3 客户端，用于 R2 上传。
    """
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv("SECRET_ACCESS_KEY"),
            endpoint_url=os.getenv("ENDPOINT_URL")
        )
        return s3_client
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="R2 bucket 凭证错误, 请检查环境变量 ACCESS_KEY_ID； "
                                                    "SECRET_ACCESS_KEY； ENDPOINT_URL 是否正确。")
