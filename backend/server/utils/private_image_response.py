"""私有解析图片的 MinIO 流式响应。"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from yuxi.storage.minio.client import MinIOClient, StorageError, get_minio_client
from yuxi.utils.logging_config import logger


async def create_private_image_response(object_name: str) -> StreamingResponse:
    """从私有图片 bucket 流式读取一个解析产物。"""

    try:
        minio_response = await get_minio_client().adownload_response(
            bucket_name=MinIOClient.MATERIAL_BUCKETS["images"],
            object_name=object_name,
        )
    except StorageError as error:
        if "不存在" in str(error):
            raise HTTPException(status_code=404, detail="图片不存在") from error
        logger.error(f"读取私有解析图片失败 {object_name}: {error}")
        raise HTTPException(status_code=500, detail="读取图片失败") from error

    content_type = minio_response.getheader("Content-Type") or "application/octet-stream"

    async def image_stream():
        try:
            while chunk := await asyncio.to_thread(minio_response.read, 8192):
                yield chunk
        finally:
            minio_response.close()
            minio_response.release_conn()

    return StreamingResponse(
        image_stream(),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


__all__ = ["create_private_image_response"]
