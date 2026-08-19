"""已启用专家 Skill 的只读检索工具。"""

import json
from pathlib import Path, PurePosixPath

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.skills.service import get_thread_skills_root_dir, is_valid_skill_slug
from yuxi.agents.toolkits.registry import tool
from yuxi.expert.index import search_documents
from yuxi.utils.paths import VIRTUAL_SKILLS_PATH, ensure_within_root

SEARCH_INDEX_PATHS = (Path("search") / "index.jsonl", Path("nav") / "search-index.jsonl")
SEARCH_RESULT_ROOTS = {"wiki", "nav", "references"}


class ExpertSearchInput(BaseModel):
    """专家 Skill 检索参数。"""

    skill_slug: str = Field(description="要检索的专家 Skill name")
    query: str = Field(min_length=1, max_length=500, description="用户问题或需要定位的判断主题")
    limit: int = Field(default=6, ge=1, le=12, description="最多返回的候选内容数量")


@tool(
    category="expert",
    tags=["专家", "检索"],
    display_name="检索专家 Skill",
    description="在当前已激活的专家 Skill 中检索讲义、导航和精选证据。",
    args_schema=ExpertSearchInput,
)
async def expert_search(skill_slug: str, query: str, runtime: ToolRuntime, limit: int = 6) -> dict:
    """从当前线程的只读 Skill 投影中检索专家成品。"""

    slug = str(skill_slug or "").strip()
    if not is_valid_skill_slug(slug):
        raise ValueError("无效专家 Skill name")

    context = getattr(runtime, "context", None)
    if slug not in _readable_skill_slugs(context):
        raise ValueError("当前 Agent 无权读取该专家 Skill")

    projection_root = get_thread_skills_root_dir(_skills_thread_id(context)).resolve()
    skill_root = ensure_within_root(
        (projection_root / slug).resolve(),
        projection_root,
        error_message="专家 Skill 路径越界",
    )
    if not skill_root.is_dir():
        raise ValueError("当前线程没有该专家 Skill 的可读投影")

    index_path = _select_search_index(skill_root)
    rows = _load_search_rows(index_path, skill_root)
    results = search_documents(rows, query, limit)
    for item in results:
        item["file_path"] = f"{VIRTUAL_SKILLS_PATH}/{slug}/{item.pop('path')}"

    return {
        "skill_slug": slug,
        "query": query,
        "results": results,
        "boundary": "请阅读候选讲义或精选证据；没有相关结果时明确说明当前专家 Skill 未覆盖。",
    }


def _skills_thread_id(context) -> str:
    """返回当前运行使用的 Skill 投影线程标识。"""

    for field_name in ("skills_thread_id", "thread_id"):
        value = getattr(context, field_name, None) if context is not None else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("当前运行缺少 Skill 投影线程标识")


def _readable_skill_slugs(context) -> set[str]:
    """读取经过权限和 Agent 配置过滤的 Skill name。"""

    values = getattr(context, "_readable_skills", None) if context is not None else None
    if not isinstance(values, list):
        return set()
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


def _select_search_index(skill_root: Path) -> Path:
    """选择新索引；仅在新索引不存在时读取真实存在的旧包索引。"""

    for relative_path in SEARCH_INDEX_PATHS:
        candidate = ensure_within_root(
            (skill_root / relative_path).resolve(),
            skill_root,
            error_message="专家 Skill 索引路径越界",
        )
        if candidate.is_file():
            return candidate
    raise ValueError("专家 Skill 没有可用检索索引")


def _load_search_rows(index_path: Path, skill_root: Path) -> list[dict]:
    """读取一次索引并校验所有候选路径都属于当前 Skill 成品。"""

    rows = []
    for line_number, line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("term_weights"), dict):
            raise ValueError(f"专家 Skill 索引第 {line_number} 行格式无效")
        row["path"] = _validate_result_path(skill_root, row.get("path"))
        rows.append(row)
    return rows


def _validate_result_path(skill_root: Path, value: object) -> str:
    """校验索引结果只能指向当前 Skill 的讲义、导航或精选证据。"""

    raw_path = value.strip() if isinstance(value, str) else ""
    relative_path = PurePosixPath(raw_path)
    if (
        not raw_path
        or relative_path.is_absolute()
        or ".." in relative_path.parts
        or not relative_path.parts
        or relative_path.parts[0] not in SEARCH_RESULT_ROOTS
    ):
        raise ValueError("专家 Skill 索引包含无效结果路径")

    target = ensure_within_root(
        (skill_root / Path(*relative_path.parts)).resolve(),
        skill_root,
        error_message="专家 Skill 索引结果路径越界",
    )
    if not target.is_file():
        raise ValueError(f"专家 Skill 索引结果不存在: {relative_path.as_posix()}")
    return relative_path.as_posix()


__all__ = ["expert_search"]
