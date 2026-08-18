from __future__ import annotations

from yuxi.runtime_mode import expert_mode_enabled, legacy_knowledge_enabled


def test_expert_mode_is_default(monkeypatch):
    monkeypatch.delenv("YUXI_EXPERT_MODE", raising=False)
    monkeypatch.delenv("LITE_MODE", raising=False)

    assert expert_mode_enabled() is True
    assert legacy_knowledge_enabled() is False


def test_legacy_knowledge_requires_two_explicit_switches(monkeypatch):
    monkeypatch.setenv("YUXI_EXPERT_MODE", "false")
    monkeypatch.setenv("LITE_MODE", "false")

    assert expert_mode_enabled() is False
    assert legacy_knowledge_enabled() is True
