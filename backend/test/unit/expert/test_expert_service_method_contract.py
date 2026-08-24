"""_validate_method_contract 的最小完整性合同测试。"""

from __future__ import annotations

import pytest

from yuxi.expert.contracts import ProjectPromptPack
from yuxi.expert.service import _validate_method_contract

pytestmark = pytest.mark.unit


def _prompt_pack(**overrides: str) -> ProjectPromptPack:
    values: dict[str, str] = {
        "note_author": "笔记作者 Prompt。",
        "note_reviewer": "笔记审阅 Prompt。",
        "topic_researcher": "研究 Prompt。",
        "topic_editor": "总编 Prompt。",
        "handout_editor": "编讲义 Prompt。",
        "handout_reviewer": "审讲义 Prompt。",
        "book_editor": "收口 Prompt。",
        "navigation_author": "导航 Prompt。",
        "skill_author": "技能 Prompt。",
    }
    values.update(overrides)
    return ProjectPromptPack.model_validate(values)


def test_empty_stage_content_rejected() -> None:
    with pytest.raises(ValueError, match="缺少阶段内容"):
        _validate_method_contract("学习方案", "编译底稿", _prompt_pack(topic_editor=" "))


def test_blank_learning_plan_or_brief_rejected() -> None:
    with pytest.raises(ValueError, match="缺少学习方案或编译底稿"):
        _validate_method_contract("  ", "编译底稿", _prompt_pack())
    with pytest.raises(ValueError, match="缺少学习方案或编译底稿"):
        _validate_method_contract("学习方案", "", _prompt_pack())


def test_prompt_pack_without_embedded_shared_blocks_passes() -> None:
    """方法合同只要求内容齐备：笔记 Prompt 不再被要求内嵌任何逐字哨兵块，
    共用块的忠实性交由小样阶段的 Review 与修订把关。"""

    _validate_method_contract("学习方案", "编译底稿", _prompt_pack())
