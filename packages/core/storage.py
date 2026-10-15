"""Object storage for uploaded documents.

Local filesystem in dev/docker-compose (a shared volume between api and worker);
S3 in the cloud (the bucket is provisioned by infra/terraform). Keys are always
prefixed with the tenant id so one tenant's objects can't be addressed by another.
"""

import asyncio
from pathlib import Path
from typing import Protocol

from packages.core.config import get_settings


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...


class LocalObjectStore:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if self.base not in path.parents:
            raise ValueError("object key escapes the storage root")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)


class S3ObjectStore:  # pragma: no cover - cloud path, needs `pip install boto3` + AWS credentials
    def __init__(self, bucket: str, region: str):
        import boto3  # optional dependency, only needed when OBJECT_STORE=s3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)


def get_object_store() -> ObjectStore:
    settings = get_settings()
    if settings.object_store == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("OBJECT_STORE=s3 requires S3_BUCKET")
        return S3ObjectStore(settings.s3_bucket, settings.aws_region)
    return LocalObjectStore(settings.object_store_dir)
