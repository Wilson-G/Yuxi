"""解析产物图片的授权读取 URL 选择。"""

from __future__ import annotations

from yuxi.storage.minio.client import build_document_image_proxy_url


class ParsedImageStorageError(RuntimeError):
    """解析产物图片无法保存或生成授权 URL。"""


def build_parsed_image_proxy_url(object_name: str) -> str:
    """按对象归属返回旧知识库或共享文档图片代理 URL。"""

    parts = object_name.strip("/").split("/")
    if len(parts) >= 3 and parts[1] == "kb-images":
        from yuxi.knowledge.utils.kb_utils import build_kb_image_proxy_url

        return build_kb_image_proxy_url(object_name)
    return build_document_image_proxy_url(object_name)


__all__ = ["ParsedImageStorageError", "build_parsed_image_proxy_url"]
