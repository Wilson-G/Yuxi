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
    assert "说话人结构" in skill
    assert "结构/信号 -> 作者的转换口径或判断" in skill
    assert "材料结构/信号 -> 作者如何把它转成判断或表现" in skill
    assert "见某信号，判什么" in skill
    assert "不预设每篇都要经过资料、原局、取用、运年等固定流程" in skill
    assert "不重写整本书" in skill
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


def test_prompt_designer_uses_apprentice_stage_example():
    skill = (ASSET_ROOT / "SKILL.md").read_text(encoding="utf-8")
    example = (ASSET_ROOT / "examples" / "apprentice-stage-prompt-pack.md").read_text(encoding="utf-8")

    assert "examples/apprentice-stage-prompt-pack.md" in skill
    headings = {line.removeprefix("## ") for line in example.splitlines() if line.startswith("## ")}
    for stage in (
        "材料侦察",
        "项目学习方案",
        "单篇学艺",
        "笔记 Review",
        "笔记修订",
        "主题候选研究",
        "主题地图",
        "讲义",
        "讲义 Review 与修订",
        "全书收口、导航与最终 Skill",
    ):
        assert stage in headings

    assert "学艺者" in example
    assert "独立复核者" in example
    assert "动作链" in example
    assert "术语、口诀、经验口径和特殊关系" in example
    assert "普通常识或可否证明来否定" in example
    assert "案例过程和原文细节留给证据" in example
    assert "内部列出" in example
    assert "不输出内部知识账本" in example
    assert "JSON、JSONL 或对象" in example
    assert "不要为不影响下游的措辞打回" in example
    assert "分型轴" in example
    assert "不要求本篇出现固定步骤" in example
    assert "作者的转换口径或判断" in example
    assert "全书收口只检查" in example
    assert "发现一个 blocker 不得停止" in example
    assert "替总编" not in example
    assert "交给总编" not in example
    assert "八字" not in example
