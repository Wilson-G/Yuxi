"""领域推导资产编译方法的提示词资产合同检查。

只保留三类真验证：结构解析（示例与生产解析器同构）、属性检查（领域中性、
资产完备）、跨层合同（提示词引用的契约字段与 pydantic 模型一致）。
纯措辞哨兵已移除；机械产出合同由 organization_service.validate_asset_note_structure
在运行时执法，发布物内部视图泄漏由 publisher.lint_compiled_view_leaks 拦截。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


PROFILE_ROOT = (
    Path(__file__).parents[3] / "package" / "yuxi" / "expert" / "prompt_designer_profiles" / "reasoning_assets"
)
SKILL_PATH = PROFILE_ROOT / "SKILL.md"
EXAMPLE_PATH = PROFILE_ROOT / "examples" / "apprentice-stage-prompt-pack.md"
PROMPT_PACK_FIELDS = (
    "note_author",
    "note_reviewer",
    "topic_researcher",
    "topic_editor",
    "handout_editor",
    "handout_reviewer",
    "book_editor",
    "navigation_author",
    "skill_author",
)
SOURCE_LABELS = ("原文明说", "领域补链", "候选解释", "专家质疑", "材料未知")
TEMPLATE_NAMES = {
    "interview-multi-source.md",
    "mentor-oral-teaching.md",
    "operations-decision.md",
    "tutorial-procedure.md",
}

pytestmark = pytest.mark.unit


def _read_assets() -> tuple[str, str]:
    """读取 reasoning_assets profile 的两个显式加载资源。"""

    return SKILL_PATH.read_text(encoding="utf-8"), EXAMPLE_PATH.read_text(encoding="utf-8")


def test_reasoning_assets_profile_has_valid_skill_frontmatter_and_minimal_files():
    skill, _ = _read_assets()
    frontmatter_match = re.match(r"\A---\n(.*?)\n---\n", skill, flags=re.DOTALL)

    assert frontmatter_match is not None
    frontmatter = yaml.safe_load(frontmatter_match.group(1))
    assert frontmatter["name"] == "expert-reasoning-assets-prompt-designer"
    assert "显式选择" in frontmatter["description"]
    assert "不用于默认编译方法" in frontmatter["description"]

    files = {path.relative_to(PROFILE_ROOT).as_posix() for path in PROFILE_ROOT.rglob("*") if path.is_file()}
    assert files == {
        "SKILL.md",
        "examples/apprentice-stage-prompt-pack.md",
        *(f"templates/{name}" for name in TEMPLATE_NAMES),
    }


def test_reasoning_assets_profile_has_domain_neutral_material_templates():
    templates = {path.name: path.read_text(encoding="utf-8") for path in (PROFILE_ROOT / "templates").glob("*.md")}

    assert set(templates) == TEMPLATE_NAMES
    assert all(
        "八字" not in content and "Amazon" not in content and "亚马逊" not in content for content in templates.values()
    )


def test_reasoning_assets_profile_designs_all_existing_prompt_pack_fields():
    skill, example = _read_assets()

    for field in PROMPT_PACK_FIELDS:
        assert field in skill
        assert field in example

    # 提示词引用的结构化字段必须与 pydantic 契约真实存在，防止文档与代码漂移。
    assert "MaterialNoteResult.note_markdown" in skill
    assert all(field in skill for field in ("evidence", "routing_labels"))


def test_reasoning_assets_example_demonstrates_all_source_labels_and_stays_domain_neutral():
    skill, example = _read_assets()

    assert all(label in example for label in SOURCE_LABELS)

    assert "亚马逊" not in skill
    assert "Amazon" not in skill
    assert "八字" not in skill


def test_reasoning_assets_requires_embedding_of_project_domain_model():
    """运行时看不到编译底稿：推导模型全文必须内嵌进两份笔记阶段 Prompt。

    这是有事故史的硬合同（两次 fix commit 的主题），保留哨兵。
    """

    skill, example = _read_assets()

    assert "编译底稿、中央 SKILL 和项目定向报告对下游任务不可见" in skill
    assert "项目领域推导模型的全文只保证进入 `note_author`\n与 `note_reviewer` 的 Prompt 本身" in skill
    assert "下游任务实际拿不到模型" in skill
    assert "`## 项目领域推导模型`" in skill

    assert "全文内嵌，不能只写“依据模型”" in example
    assert "`note_reviewer` 同样必须内嵌" in example


def test_reasoning_assets_example_has_extractable_node_catalog_and_matching_bodies():
    """示例必须能被生产解析器同构的正则完整提取，且目录与正文同序一一对应。"""

    _, example = _read_assets()
    catalog_match = re.search(
        r"## 资产节点目录\n(?P<catalog>.*?)(?=\n\n## 资产正文)",
        example,
        flags=re.DOTALL,
    )
    assert catalog_match is not None

    catalog_nodes = re.findall(
        r"^- (N\d{2}) \[([^\]]+)\]\[(原文明说|领域补链|候选解释|专家质疑|材料未知)\] .+$",
        catalog_match.group("catalog"),
        flags=re.MULTILINE,
    )
    body_nodes = re.findall(
        r"^### (N\d{2}) \[([^\]]+)\]\[(原文明说|领域补链|候选解释|专家质疑|材料未知)\]$",
        example,
        flags=re.MULTILINE,
    )

    assert catalog_nodes
    assert catalog_nodes == body_nodes
    assert len({node_id for node_id, _, _ in catalog_nodes}) == len(catalog_nodes)
    assert "## CoT 推导示范" in example
    assert "Input" in example and "Goal / Constraints" in example
    assert "Domain Relation" in example and "Next Check" in example
    assert "Update" in example and "Transfer" in example


def test_reasoning_assets_example_keeps_calibration_demo_and_scope_discipline():
    """示例允许用真实领域材料示范校准深度，但必须写明只示范结构不外溢。"""

    _, example = _read_assets()

    assert "## 第 0 阶段：领域激活与材料校准" in example
    assert "只示范结构、来源拆分、补链尺度和 Review 深度" in example
    assert "不是其他领域或其他 Amazon 材料的检查清单" in example
