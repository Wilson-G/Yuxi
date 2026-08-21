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
    "note_reviser",
    "topic_researcher",
    "topic_editor",
    "handout_editor",
    "handout_reviewer",
    "handout_reviser",
    "book_editor",
    "navigation_author",
    "skill_author",
)
SOURCE_LABELS = ("原文明说", "领域补链", "专家质疑", "材料未知")
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
    assert all("项目定向" in content and "最终专家" in content for content in templates.values())
    assert "补链" in "\n".join(templates.values())
    assert all(
        "八字" not in content and "Amazon" not in content and "亚马逊" not in content for content in templates.values()
    )


def test_reasoning_assets_profile_designs_all_existing_prompt_pack_fields():
    skill, example = _read_assets()

    for field in PROMPT_PACK_FIELDS:
        assert f"`{field}`" in skill
        assert f"`{field}`" in example or f"\n{field}\n" in example

    assert "MaterialNoteResult.note_markdown" in skill
    assert all(field in skill for field in ("evidence", "routing_labels", "candidate_questions"))


def test_reasoning_assets_profile_keeps_domain_knowledge_and_material_sources_separate():
    skill, example = _read_assets()

    assert "先激活领域知识" in skill
    assert "再用代表材料校准" in skill
    assert "不能写成“材料认为”" in skill
    assert "原文明说" in skill and "不代表内容正确" in skill
    assert "一个节点只能有一个来源标签" in skill
    assert "混在同一句时必须拆成多个节点" in skill
    assert all(label in example for label in SOURCE_LABELS)

    assert "亚马逊" not in skill
    assert "Amazon" not in skill
    assert "八字" not in skill


def test_reasoning_assets_example_has_extractable_node_catalog_and_matching_bodies():
    _, example = _read_assets()
    catalog_match = re.search(
        r"## 资产节点目录\n(?P<catalog>.*?)(?=\n\n## 资产正文)",
        example,
        flags=re.DOTALL,
    )
    assert catalog_match is not None

    catalog_nodes = re.findall(
        r"^- (N\d{2}) \[([^\]]+)\]\[(原文明说|领域补链|专家质疑|材料未知)\] .+$",
        catalog_match.group("catalog"),
        flags=re.MULTILINE,
    )
    body_nodes = re.findall(
        r"^### (N\d{2}) \[([^\]]+)\]\[(原文明说|领域补链|专家质疑|材料未知)\]$",
        example,
        flags=re.MULTILINE,
    )

    assert catalog_nodes
    assert catalog_nodes == body_nodes
    assert len({node_id for node_id, _, _ in catalog_nodes}) == len(catalog_nodes)
    assert "材料没有反馈就不创建反馈节点" in _read_assets()[0]
    assert "没有预测就不创建预测节点" in _read_assets()[0]


def test_reasoning_assets_profile_limits_evidence_to_material_statements():
    skill, example = _read_assets()

    assert "只锚定当前材料直接明说的内容或材料事实" in skill
    assert "不得为 `领域补链`、`专家质疑` 或 `材料未知` 伪造原文行号" in skill
    assert "只引用支持 N01、N03、N04、N05、N07" in example
    assert "不为 N02、N06、N08、N09 伪造原文依据" in example


def test_reasoning_assets_example_uses_real_amazon_materials_only_for_calibration():
    _, example = _read_assets()

    assert "《每天一个亚马逊运营技巧ACOS太高怎么办？》" in example
    assert "《亚马逊产品转化差怎么排查问题》" in example
    assert "只示范结构、来源拆分、补链尺度和 Review 深度" in example
    assert "不是其他领域或其他 Amazon 材料的检查清单" in example
    assert "ACOS 等于广告花费除以广告销售额" in example
    assert "Listing 九要素 -> 广告优化 -> 产品竞争力与止损" in example
    assert "不能静默删掉数字后把结论继续写成已证事实" in example


def test_reasoning_assets_reviews_and_products_reject_long_but_unsupported_chains():
    skill, example = _read_assets()
    reviewer = skill.split("### `note_reviewer`", maxsplit=1)[1].split("### `note_reviser`", maxsplit=1)[0]
    product = skill.split("### `handout_editor`", maxsplit=1)[1]
    requirements = (
        "链更长不等于质量更高",
        "尊重用户输入事实",
        "反领域关系",
        "候选解释不支撑强动作",
    )

    assert all(requirement in reviewer for requirement in requirements)
    assert all(requirement in product for requirement in requirements)
    assert all(requirement in example for requirement in requirements)


def test_reasoning_assets_final_answers_hide_compilation_views():
    skill, example = _read_assets()
    forbidden_views = ("节点 ID", "来源标签", "Thought", "Action", "审计路径", "文件路径", "编译说明")

    assert "最终用户回答必须自然完整" in skill
    assert "最终用户回答自然完整" in example
    assert all(value in skill for value in forbidden_views)
    assert all(value in example for value in forbidden_views)
    assert "CoT 是这些资产交给运行时模型学习判断顺序的主要视图" in skill
    assert "不是最终用户回答格式" in skill
