from __future__ import annotations

from pathlib import Path

import pytest


ASSET_ROOT = Path(__file__).parents[3] / "package" / "yuxi" / "expert" / "prompt_designer"
pytestmark = pytest.mark.unit


def test_prompt_designer_teaches_project_specific_prompt_creation():
    skill = (ASSET_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "不是给每个项目套同一组角色卡" in skill
    assert "项目定向报告" in skill
    assert "项目专属编译底稿" in skill
    assert "项目专属 Prompt 包" in skill
    assert "教材化" in skill
    assert "不是材料审计报告" in skill
    assert "用真实小样验证" in skill
    assert "谁在说话" not in skill
    assert "同一老师" not in skill


def test_prompt_designer_keeps_machine_metadata_outside_prompts():
    forbidden = ("task_id", "output_path", "sha256", "manifest.json", "sources.jsonl", "search-index.jsonl")

    for asset in ASSET_ROOT.rglob("*.md"):
        content = asset.read_text(encoding="utf-8").lower()
        assert not any(value in content for value in forbidden), asset


def test_prompt_designer_has_material_type_starting_points():
    templates = {path.name for path in (ASSET_ROOT / "templates").glob("*.md")}

    assert templates == {
        "mentor-oral-teaching.md",
        "operations-decision.md",
        "tutorial-procedure.md",
        "interview-multi-source.md",
    }


def test_prompt_designer_uses_bazi_full_stage_example():
    skill = (ASSET_ROOT / "SKILL.md").read_text(encoding="utf-8")
    example = (ASSET_ROOT / "examples" / "bazi-full-stage-prompt-pack.md").read_text(encoding="utf-8")

    assert "examples/bazi-full-stage-prompt-pack.md" in skill
    for stage in (
        "材料侦察",
        "项目学习方案",
        "学艺笔记",
        "笔记 Review",
        "笔记修订",
        "主题候选研究",
        "总编模型主题地图",
        "总编模型讲义",
        "讲义 Review 与修订",
        "全书收口、导航与最终 Skill",
    ):
        assert stage in example

    assert "个案断语改成通则" in example
    assert "反转与边界" in example
    assert "Prime 总编" not in example
    assert "amazon" not in example.lower()
    assert "asin" not in example.lower()
    assert "listing" not in example.lower()
