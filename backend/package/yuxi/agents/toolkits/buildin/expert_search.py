"""专家 Skill 的只读检索工具。"""

import json

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.skills.service import is_valid_skill_slug
from yuxi.agents.toolkits.registry import tool
from yuxi.config import get_save_dir
from yuxi.expert.index import search_documents


class ExpertSearchInput(BaseModel):
    """专家材料检索参数。"""

    skill_slug: str = Field(description="要检索的专家 Skill name，例如 amazon-operations-expert")
    query: str = Field(min_length=1, max_length=500, description="用户问题或需要定位的判断主题")
    limit: int = Field(default=6, ge=1, le=12, description="最多返回的候选材料数量")


@tool(
    category="buildin",
    tags=["专家", "检索"],
    display_name="检索专家材料",
    description=(
        "在已激活的专家 Skill 中检索候选原文。先用它定位相关材料，再使用 read_file 读取返回路径的全文；"
        "不能只根据摘要直接作答。"
    ),
    args_schema=ExpertSearchInput,
)
async def expert_search(skill_slug: str, query: str, runtime: ToolRuntime, limit: int = 6) -> dict:
    """检索当前 Agent 可读取的专家 Skill，并返回精读路径。"""

    slug = str(skill_slug or "").strip()
    if not is_valid_skill_slug(slug):
        raise ValueError("无效专家 Skill name")
    if slug not in _readable_skill_slugs(runtime):
        raise ValueError("当前 Agent 无权读取该专家 Skill")

    skills_root = (get_save_dir() / "skills").resolve()
    skill_root = (skills_root / slug).resolve()
    if not skill_root.is_relative_to(skills_root):
        raise ValueError("专家 Skill 路径越界")
    index_path = skill_root / "nav" / "search-index.jsonl"
    if not index_path.is_file():
        raise ValueError("专家 Skill 没有可用检索索引")

    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = search_documents(rows, query, limit)
    for item in results:
        item["file_path"] = f"/home/gem/skills/{slug}/{item.pop('path')}"
    return {
        "skill_slug": slug,
        "query": query,
        "results": results,
        "boundary": "请用 read_file 精读候选原文；无结果时按未覆盖处理。",
    }


def _readable_skill_slugs(runtime: ToolRuntime) -> set[str]:
    """从当前运行上下文读取已经过权限过滤的 Skill name。"""

    context = getattr(runtime, "context", None)
    values = getattr(context, "_readable_skills", None) if context is not None else None
    if not isinstance(values, list):
        return set()
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


__all__ = ["expert_search"]
