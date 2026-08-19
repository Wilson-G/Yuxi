"""统一读取资料库材料单元和旧项目可靠正文。"""

from __future__ import annotations

from typing import Any

from yuxi.expert.storage import read_material_text as read_legacy_material_text
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager


async def load_expert_material_source_text(material: Any) -> str:
    """读取材料对应的完整来源正文；旧项目继续读取项目内正文。"""

    if not material.material_unit_id:
        if not material.text_path:
            raise ValueError("材料缺少可靠正文")
        return read_legacy_material_text(material.project_id, material.text_path)

    if not material.source_file_id:
        raise ValueError("资料库材料缺少来源文件关联")
    async with pg_manager.get_async_session_context() as db:
        source_file = await MaterialLibraryRepository(db).get_source_file(material.source_file_id)
    if (
        source_file is None
        or source_file.status != "ready"
        or source_file.unit_status != "ready"
        or not source_file.markdown_bucket
        or not source_file.markdown_object
    ):
        raise ValueError("资料库材料来源正文尚未就绪")
    payload = await get_minio_client().adownload_file(source_file.markdown_bucket, source_file.markdown_object)
    return payload.decode("utf-8")


def extract_expert_material_text(material: Any, source_text: str) -> str:
    """从完整来源正文中取出当前自然材料单元。"""

    if not material.material_unit_id:
        return source_text
    start_line, end_line = _source_bounds(material)
    lines = source_text.splitlines(keepends=True)
    if end_line > len(lines):
        raise ValueError("资料库材料来源行范围已失效")
    return "".join(lines[start_line - 1 : end_line])


async def read_expert_material_text(material: Any) -> str:
    """读取模型当前任务应完整看到的一份自然材料单元。"""

    source_text = await load_expert_material_source_text(material)
    return extract_expert_material_text(material, source_text)


async def load_expert_material_source_texts(materials: list[Any]) -> dict[str, str]:
    """按来源去重读取一组材料的完整正文。"""

    source_cache: dict[tuple[str, str], str] = {}
    result: dict[str, str] = {}
    for material in materials:
        if material.material_unit_id:
            source_key = ("library", str(material.source_file_id or ""))
        else:
            source_key = (material.project_id, str(material.text_path or ""))
        if source_key not in source_cache:
            source_cache[source_key] = await load_expert_material_source_text(material)
        result[material.material_id] = source_cache[source_key]
    return result


def format_expert_material_excerpt(
    material: Any,
    source_text: str,
    *,
    start_line: int,
    end_line: int,
    source_line_numbers: bool = False,
) -> str:
    """按材料局部行号读取证据，可在最终发布时映射为来源行号。"""

    if start_line < 1 or start_line > end_line or end_line > material.line_count:
        raise ValueError("材料证据引用超出可靠正文范围")

    source_start = _source_bounds(material)[0] if material.material_unit_id else 1
    absolute_start = source_start + start_line - 1
    absolute_end = source_start + end_line - 1
    lines = source_text.splitlines()
    if absolute_end > len(lines):
        raise ValueError("材料证据引用的来源行范围已失效")

    return "\n".join(
        f"L{absolute_line if source_line_numbers else local_line}: {lines[absolute_line - 1]}"
        for local_line, absolute_line in enumerate(range(absolute_start, absolute_end + 1), start=start_line)
    )


def map_expert_material_source_lines(material: Any, *, start_line: int, end_line: int) -> tuple[int, int]:
    """把材料局部证据行号映射为规范化来源 Markdown 行号。"""

    if start_line < 1 or start_line > end_line or end_line > material.line_count:
        raise ValueError("材料证据引用超出可靠正文范围")
    source_start = _source_bounds(material)[0] if material.material_unit_id else 1
    return source_start + start_line - 1, source_start + end_line - 1


def _source_bounds(material: Any) -> tuple[int, int]:
    start_line = material.source_start_line
    end_line = material.source_end_line
    if not isinstance(start_line, int) or not isinstance(end_line, int) or start_line < 1 or end_line < start_line:
        raise ValueError("资料库材料缺少有效来源行范围")
    return start_line, end_line


__all__ = [
    "extract_expert_material_text",
    "format_expert_material_excerpt",
    "load_expert_material_source_text",
    "load_expert_material_source_texts",
    "map_expert_material_source_lines",
    "read_expert_material_text",
]
