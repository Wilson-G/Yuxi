"""资料库解析图片失败合同测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from yuxi.knowledge.parser import zip_utils
from yuxi.knowledge.parser.image_urls import ParsedImageStorageError

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_image_upload_failure_stops_document_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zip_path = tmp_path / "material.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("full.md", "正文\n\n![图](images/diagram.png)")
        archive.writestr("images/diagram.png", b"not-a-real-image")

    class FailingMinio:
        def ensure_bucket_exists(self, bucket_name: str) -> None:
            return None

        async def aupload_file(self, **_kwargs):
            raise RuntimeError("minio unavailable")

    monkeypatch.setattr(zip_utils, "get_minio_client", lambda: FailingMinio())

    with pytest.raises(ParsedImageStorageError, match="diagram.png"):
        await zip_utils.process_zip_file(
            str(zip_path),
            image_bucket="kb-images",
            image_prefix="material-libraries/library-1/source-files/file-1/images",
        )
