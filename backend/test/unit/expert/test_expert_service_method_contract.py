"""_validate_method_contract 对两份 note Prompt 共享块的机械一致性校验测试。"""

from __future__ import annotations

import pytest

from yuxi.expert.contracts import ProjectPromptPack
from yuxi.expert.service import _extract_shared_block, _validate_method_contract

pytestmark = pytest.mark.unit


def _domain_block(tail: str = "") -> str:
    return (
        "- 领域基础（可作为补链依据）：甲体系与归因差异；核心指标的定义与相互关系。\n"
        f"- 样本观察（只属于样本资产，不代表整个材料包）：观察句{tail}。\n"
        "- 项目假设（待全部材料验证，不得写成已确认方法）：假设句。\n"
        "- 材料缺口：缺口句。"
    )


def _label_block(tail: str = "") -> str:
    return (
        "- 原文明说：归属规则。\n"
        "- 领域补链：补链规则。\n"
        "- 候选解释：降调规则。\n"
        f"- 专家质疑：质疑规则{tail}。\n"
        "- 材料未知：未知规则。"
    )


def _note_prompt(domain_tail: str = "", label_tail: str = "") -> str:
    return (
        "你是编译者，先读领域模型再动笔。\n\n"
        "【项目领域推导模型】\n四层边界：\n"
        + _domain_block(domain_tail)
        + "\n\n七条推导链是比较框架，不是内容清单：略。\n"
        "按判断局面组织的七条推导链（每条：先观察 -> 候选判断）：\n1. 略。\n\n"
        "【五类来源标签】\n"
        + _label_block(label_tail)
        + "\n\n一个节点只能有一个标签。\n"
    )


def _prompt_pack(**overrides: str) -> ProjectPromptPack:
    values: dict[str, str] = {
        "note_author": _note_prompt(),
        "note_reviewer": _note_prompt(),
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


def test_identical_shared_blocks_pass() -> None:
    _validate_method_contract("学习方案", "编译底稿", _prompt_pack())


def test_missing_embedded_model_is_rejected() -> None:
    pack = _prompt_pack(note_reviewer="没有内嵌领域模型的审阅者 Prompt")
    with pytest.raises(ValueError, match="note_reviewer 未完整内嵌"):
        _validate_method_contract("学习方案", "编译底稿", pack)


def test_domain_block_drift_is_rejected() -> None:
    pack = _prompt_pack(note_author=_note_prompt(domain_tail="（漂移）"))
    with pytest.raises(ValueError, match="四层边界块不一致"):
        _validate_method_contract("学习方案", "编译底稿", pack)


def test_source_label_block_drift_is_rejected() -> None:
    pack = _prompt_pack(note_reviewer=_note_prompt(label_tail="（漂移）"))
    with pytest.raises(ValueError, match="五类来源标签块不一致"):
        _validate_method_contract("学习方案", "编译底稿", pack)


def test_empty_stage_content_still_rejected_first() -> None:
    with pytest.raises(ValueError, match="缺少阶段内容"):
        _validate_method_contract("学习方案", "编译底稿", _prompt_pack(topic_editor=" "))


def test_extract_shared_block_requires_both_sentinels() -> None:
    text = _note_prompt()
    assert _extract_shared_block(text, "- 原文明说：", "- 材料未知：").startswith("- 原文明说：")
    assert _extract_shared_block(text, "- 不存在的哨兵：", "- 材料未知：") == ""
