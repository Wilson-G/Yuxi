from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from yuxi.expert.index import search_documents
from yuxi.expert.prime_acp import PrimeAcpResponse
from yuxi.expert.service import (
    compile_expert_package,
    compile_expert_package_with_prime,
    create_expert_package,
    get_expert_package,
)


class FakeTaskContext:
    def __init__(self) -> None:
        self.task_id = "task-1"
        self.progress: list[tuple[float, str | None]] = []

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        self.progress.append((progress, message))

    async def raise_if_cancelled(self) -> None:
        return None


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_expert_package_compiles_searchable_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    package = create_expert_package(
        name="亚马逊运营专家",
        description="根据亚马逊运营材料诊断广告和库存问题。",
        filename="materials.zip",
        content=_zip_bytes(
            {
                "ads/conversion.md": "# 广告转化诊断\n\n曝光正常但点击率低时，先检查主图和关键词相关性。",
                "inventory/stock.md": "# 库存与补货\n\n库存不足时需要结合补货周期控制销量。",
            }
        ),
        created_by="admin",
    )

    result = await compile_expert_package(package["id"], FakeTaskContext())

    metadata = get_expert_package(package["id"])
    skill_root = tmp_path / "expert_packages" / package["id"] / "compiled"
    rows = [
        json.loads(line)
        for line in (skill_root / "nav" / "search-index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    hits = search_documents(rows, "广告点击率低怎么排查", limit=3)

    assert result["status"] == "compiled"
    assert result["version"] == 1
    assert metadata["version"] == 1
    assert metadata["release_hash"] == result["release_hash"]
    assert metadata["active_version"] is None
    assert metadata["indexed_documents"] == 2
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "references" / "ads" / "conversion.md").is_file()
    assert "expert_search" in (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert hits[0]["path"] == "references/ads/conversion.md"

    second = await compile_expert_package(package["id"], FakeTaskContext())
    assert second["version"] == 2
    assert get_expert_package(package["id"])["version"] == 2


@pytest.mark.asyncio
async def test_prime_compile_adds_method_and_refreshes_release_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    package = create_expert_package(
        name="prime-test",
        description="Prime 编译测试",
        filename="source.md",
        content="# 发布规则\n\n错误率超过 3% 时回滚。".encode(),
        created_by="admin",
    )

    async def fake_prime(**_kwargs):
        return PrimeAcpResponse(text="# Prime 方法\n\n先读取回滚条件，再回到原文核对。", session_id="session-test")

    monkeypatch.setattr("yuxi.expert.service.run_prime_acp", fake_prime)
    result = await compile_expert_package_with_prime(package["id"], FakeTaskContext())

    metadata = get_expert_package(package["id"])
    compiled = tmp_path / "expert_packages" / package["id"] / "compiled"
    manifest = json.loads((compiled / "manifest.json").read_text(encoding="utf-8"))
    assert result["compiler"] == "prime-acp-v1"
    assert metadata["compiler"] == "prime-acp-v1"
    assert metadata["prime_session_id"] == "session-test"
    assert (compiled / "method.md").read_text(encoding="utf-8").startswith("# Prime 方法")
    assert manifest["compiler"] == "prime-acp-v1"
    assert manifest["release_hash"] == metadata["release_hash"]


def test_expert_package_rejects_archive_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="不安全路径"):
        create_expert_package(
            name="invalid",
            description="",
            filename="materials.zip",
            content=_zip_bytes({"../outside.md": "# outside"}),
            created_by="admin",
        )

    assert not (tmp_path / "outside.md").exists()


def test_expert_package_requires_utf8_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="UTF-8"):
        create_expert_package(
            name="invalid-encoding",
            description="",
            filename="source.md",
            content=b"\xff\xfe\x00",
            created_by="admin",
        )
