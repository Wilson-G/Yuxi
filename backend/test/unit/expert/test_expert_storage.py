"""专家项目材料存储与正文转换测试。"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from yuxi.expert import storage

pytestmark = pytest.mark.unit

PROJECT_ID = "a" * 32


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """创建隔离的专家项目目录。"""

    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    return storage.create_project_workspace(PROJECT_ID)


def _write_upload(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_project_workspace_and_material_reads_stay_inside_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="无效的专家项目 ID"):
        storage.create_project_workspace("../outside")

    project_root = storage.create_project_workspace(PROJECT_ID)
    assert project_root == (tmp_path / "expert_projects" / PROJECT_ID).resolve()
    assert (project_root / "raw").is_dir()
    assert (project_root / "text").is_dir()

    material_path = project_root / "text" / "material.md"
    material_path.write_text("完整正文", encoding="utf-8")
    assert storage.read_material_text(PROJECT_ID, "text/material.md") == "完整正文"

    for invalid_path in ("../outside.md", "/tmp/outside.md"):
        with pytest.raises(ValueError, match="专家项目文件路径无效"):
            storage.read_material_text(PROJECT_ID, invalid_path)

    outside = tmp_path / "outside.md"
    outside.write_text("不属于项目", encoding="utf-8")
    (project_root / "text" / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="专家项目文件路径越界"):
        storage.read_material_text(PROJECT_ID, "text/linked.md")

    storage.delete_project_workspace(PROJECT_ID)
    assert not project_root.exists()


def test_supported_uploads_are_stored_as_independent_natural_materials(
    project_root: Path,
    tmp_path: Path,
) -> None:
    first = _write_upload(tmp_path, "first-upload.md", b"first material")
    second = _write_upload(tmp_path, "second-upload.pdf", b"second material")

    sources = storage.store_uploaded_sources(
        PROJECT_ID,
        [
            ("series/first.md", first),
            ("second.pdf", second),
        ],
    )

    assert len(sources) == 2
    assert len({source.material_id for source in sources}) == 2
    assert [source.source_path for source in sources] == ["series/first.md", "second.pdf"]
    assert [source.title for source in sources] == ["first", "second"]
    assert [source.file_type for source in sources] == [".md", ".pdf"]
    assert [(project_root / source.raw_path).read_bytes() for source in sources] == [
        b"first material",
        b"second material",
    ]


def test_zip_rejects_path_traversal(project_root: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.md", "outside")

    with pytest.raises(ValueError, match="上传内容包含不安全路径"):
        storage.store_uploaded_sources(PROJECT_ID, [("materials.zip", archive_path)])

    assert not (project_root.parent / "outside.md").exists()


def test_zip_rejects_symbolic_links(project_root: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("linked.md")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, "target.md")

    with pytest.raises(ValueError, match="ZIP 不允许包含符号链接"):
        storage.store_uploaded_sources(PROJECT_ID, [("materials.zip", archive_path)])

    assert list((project_root / "raw").iterdir()) == []


def test_duplicate_paths_and_unsupported_types_fail_fast(project_root: Path, tmp_path: Path) -> None:
    first = _write_upload(tmp_path, "first.md", b"first")
    second = _write_upload(tmp_path, "second.md", b"second")

    with pytest.raises(ValueError, match="上传内容包含重复路径"):
        storage.store_uploaded_sources(
            PROJECT_ID,
            [("Topic/Note.md", first), ("topic/note.MD", second)],
        )

    unsupported = _write_upload(tmp_path, "payload.exe", b"binary")
    with pytest.raises(ValueError, match="不支持的材料文件类型"):
        storage.store_uploaded_sources(PROJECT_ID, [("payload.exe", unsupported)])

    assert not any(path.suffix == ".exe" for path in (project_root / "raw").iterdir())


@pytest.mark.asyncio
async def test_parse_material_persists_complete_text_without_truncation(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _write_upload(tmp_path, "source.md", b"raw source")
    [source] = storage.store_uploaded_sources(PROJECT_ID, [("source.md", upload)])
    markdown = "FIRST-LINE\n" + ("middle line\n" * 4000) + "LAST-LINE"
    calls: list[tuple[str, dict]] = []

    async def fake_parse_document(path: str, params: dict) -> str:
        calls.append((path, params))
        return markdown

    monkeypatch.setattr(storage, "parse_document", fake_parse_document)

    parsed = await storage.parse_material(PROJECT_ID, source)

    assert calls == [
        (
            str(project_root / source.raw_path),
            {"image_prefix": f"expert-projects/{PROJECT_ID}/{source.material_id}"},
        )
    ]
    assert storage.read_material_text(PROJECT_ID, parsed.text_path) == markdown
    assert (project_root / parsed.text_path).read_text(encoding="utf-8") == markdown
    assert parsed.line_count == len(markdown.splitlines())
    assert parsed.token_count > 0


@pytest.mark.asyncio
async def test_parse_material_rejects_empty_body(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _write_upload(tmp_path, "empty.md", b"raw source")
    [source] = storage.store_uploaded_sources(PROJECT_ID, [("empty.md", upload)])

    async def fake_parse_document(_path: str, params: dict) -> str:
        del params
        return " \n\t"

    monkeypatch.setattr(storage, "parse_document", fake_parse_document)

    with pytest.raises(ValueError, match="材料未能转换出可读正文"):
        await storage.parse_material(PROJECT_ID, source)

    assert not (project_root / "text" / f"{source.material_id}.md").exists()
