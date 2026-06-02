"""R2 对象存储工具"""
import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

_client = None


def _configured() -> bool:
    required = ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
    return all((os.getenv(key) or "").strip() for key in required)


def _require_configured() -> None:
    if not _configured():
        raise RuntimeError("R2 storage is not configured")

def _get_client():
    global _client
    _require_configured()
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=os.getenv("R2_ENDPOINT"),
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _client

def upload_file(local_path: str, r2_key: str, content_type: str = "") -> str:
    _require_configured()
    client = _get_client()
    extra = {"ContentType": content_type} if content_type else None
    client.upload_file(local_path, os.getenv("R2_BUCKET_NAME"), r2_key, ExtraArgs=extra)
    return r2_key


def download_file(r2_key: str, local_path: str) -> str:
    _require_configured()
    client = _get_client()
    client.download_file(os.getenv("R2_BUCKET_NAME"), r2_key, local_path)
    return local_path


def delete_object(r2_key: str) -> bool:
    if not r2_key:
        return False
    if not _configured():
        return False
    client = _get_client()
    try:
        client.delete_object(Bucket=os.getenv("R2_BUCKET_NAME"), Key=r2_key)
        return True
    except ClientError:
        return False

def get_presigned_url(r2_key: str, expires: int = 3600) -> str:
    _require_configured()
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.getenv("R2_BUCKET_NAME"), "Key": r2_key},
        ExpiresIn=expires,
    )


def object_exists(r2_key: str) -> bool:
    if not r2_key:
        return False
    if not _configured():
        return False
    client = _get_client()
    try:
        client.head_object(Bucket=os.getenv("R2_BUCKET_NAME"), Key=r2_key)
        return True
    except ClientError:
        return False
