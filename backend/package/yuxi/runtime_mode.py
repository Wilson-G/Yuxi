"""运行模式开关。"""

from __future__ import annotations

import os


def expert_mode_enabled() -> bool:
    """返回是否启用以专家 Skill 为主的轻量运行模式。"""

    return os.getenv("YUXI_EXPERT_MODE", "true").strip().lower() in {"1", "true", "yes"}


def legacy_knowledge_enabled() -> bool:
    """返回旧知识库与图谱运行链路是否显式启用。"""

    lite_mode = os.getenv("LITE_MODE", "true").strip().lower() in {"1", "true", "yes"}
    return not expert_mode_enabled() and not lite_mode


__all__ = ["expert_mode_enabled", "legacy_knowledge_enabled"]
