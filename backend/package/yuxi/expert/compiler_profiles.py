"""专家编译方法的静态资产注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_COMPILER_PROFILE_ID = "apprenticeship"
REASONING_ASSETS_PROFILE_ID = "reasoning_assets"

_EXPERT_ROOT = Path(__file__).parent
_PROMPT_DESIGNER_ROOT = _EXPERT_ROOT / "prompt_designer"
_REASONING_ASSETS_ROOT = _EXPERT_ROOT / "prompt_designer_profiles" / "reasoning_assets"

MATERIAL_TEMPLATE_NAMES = (
    "mentor-oral-teaching.md",
    "operations-decision.md",
    "tutorial-procedure.md",
    "interview-multi-source.md",
)


@dataclass(frozen=True)
class ExpertCompilerProfile:
    """描述一套内置专家编译方法及其中央资产路径。"""

    profile_id: str
    display_name: str
    description: str
    skill_path: Path
    structure_example_path: Path
    templates_dir: Path
    includes_asset_node_catalog: bool = False

    def public_dict(self) -> dict[str, str]:
        """返回前端创建方法选择所需的公开信息。"""

        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "description": self.description,
        }


COMPILER_PROFILES = (
    ExpertCompilerProfile(
        profile_id=DEFAULT_COMPILER_PROFILE_ID,
        display_name="教材笔记法",
        description="先从代表材料定向，再逐份形成教材化笔记、主题和讲义。",
        skill_path=_PROMPT_DESIGNER_ROOT / "SKILL.md",
        structure_example_path=_PROMPT_DESIGNER_ROOT / "examples" / "apprentice-stage-prompt-pack.md",
        templates_dir=_PROMPT_DESIGNER_ROOT / "templates",
    ),
    ExpertCompilerProfile(
        profile_id=REASONING_ASSETS_PROFILE_ID,
        display_name="领域推导资产法",
        description="围绕领域推导资产组织材料，保留资产节点目录供后续分组。",
        skill_path=_REASONING_ASSETS_ROOT / "SKILL.md",
        structure_example_path=_REASONING_ASSETS_ROOT / "examples" / "apprentice-stage-prompt-pack.md",
        templates_dir=_REASONING_ASSETS_ROOT / "templates",
        includes_asset_node_catalog=True,
    ),
)

_PROFILES_BY_ID = {profile.profile_id: profile for profile in COMPILER_PROFILES}


def get_expert_compiler_profile(profile_id: str, *, validate_assets: bool = True) -> ExpertCompilerProfile:
    """按稳定 ID 读取内置编译方法，并按需校验其资产合同。"""

    clean_profile_id = str(profile_id or "").strip()
    profile = _PROFILES_BY_ID.get(clean_profile_id)
    if profile is None:
        raise ValueError(f"未知专家编译方法: {clean_profile_id or '<empty>'}")
    if validate_assets:
        _validate_profile_assets(profile)
    return profile


def list_expert_compiler_profiles() -> list[dict[str, str]]:
    """返回当前可供创建项目选择的内置编译方法。"""

    for profile in COMPILER_PROFILES:
        _validate_profile_assets(profile)
    return [profile.public_dict() for profile in COMPILER_PROFILES]


def _validate_profile_assets(profile: ExpertCompilerProfile) -> None:
    required_paths = (
        ("中央 SKILL", profile.skill_path),
        ("结构示例", profile.structure_example_path),
        ("材料类型模板目录", profile.templates_dir),
    )
    for label, path in required_paths:
        if not path.exists():
            raise ValueError(f"专家编译方法 {profile.profile_id} 缺少必需资产: {label}")

    if not profile.skill_path.is_file() or not profile.structure_example_path.is_file():
        raise ValueError(f"专家编译方法 {profile.profile_id} 缺少必需资产文件")
    if not profile.templates_dir.is_dir():
        raise ValueError(f"专家编译方法 {profile.profile_id} 缺少材料类型模板目录")
    missing_templates = [name for name in MATERIAL_TEMPLATE_NAMES if not (profile.templates_dir / name).is_file()]
    if missing_templates:
        raise ValueError(f"专家编译方法 {profile.profile_id} 缺少材料类型模板: {', '.join(missing_templates)}")


__all__ = [
    "COMPILER_PROFILES",
    "DEFAULT_COMPILER_PROFILE_ID",
    "ExpertCompilerProfile",
    "MATERIAL_TEMPLATE_NAMES",
    "REASONING_ASSETS_PROFILE_ID",
    "get_expert_compiler_profile",
    "list_expert_compiler_profiles",
]
