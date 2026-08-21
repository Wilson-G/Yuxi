"""专家编译方法静态注册与资产合同测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from yuxi.expert import compiler_profiles

pytestmark = pytest.mark.unit


def test_builtin_compiler_profiles_have_stable_public_options_and_assets() -> None:
    options = compiler_profiles.list_expert_compiler_profiles()

    assert options == [
        {
            "profile_id": "apprenticeship",
            "display_name": "教材笔记法",
            "description": "先从代表材料定向，再逐份形成教材化笔记、主题和讲义。",
        },
        {
            "profile_id": "reasoning_assets",
            "display_name": "领域推导资产法",
            "description": "围绕领域推导资产组织材料，保留资产节点目录供后续分组。",
        },
    ]

    for profile in compiler_profiles.COMPILER_PROFILES:
        assert profile.skill_path.is_file()
        assert profile.structure_example_path.is_file()
        assert {path.name for path in profile.templates_dir.glob("*.md")} == set(
            compiler_profiles.MATERIAL_TEMPLATE_NAMES
        )


def test_unknown_or_incomplete_compiler_profile_fails_fast(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="未知专家编译方法"):
        compiler_profiles.get_expert_compiler_profile("not-registered")

    default_profile = compiler_profiles.get_expert_compiler_profile("apprenticeship")
    broken_profile = replace(
        default_profile,
        profile_id="broken",
        skill_path=tmp_path / "missing-SKILL.md",
    )
    monkeypatch.setitem(compiler_profiles._PROFILES_BY_ID, "broken", broken_profile)

    with pytest.raises(ValueError, match="缺少必需资产: 中央 SKILL"):
        compiler_profiles.get_expert_compiler_profile("broken")
