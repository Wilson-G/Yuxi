from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.expert import concurrency


def _fake_options(values: dict):
    async def get():
        return values

    return SimpleNamespace(get=get)


@pytest.mark.asyncio
async def test_concurrency_reads_configured_value(monkeypatch):
    monkeypatch.setattr(
        concurrency, "system_options", _fake_options({"expert_material_parse_concurrency": 6})
    )
    assert await concurrency.get_expert_concurrency("expert_material_parse_concurrency", 8) == 6


@pytest.mark.asyncio
async def test_concurrency_falls_back_to_default_when_missing(monkeypatch):
    monkeypatch.setattr(concurrency, "system_options", _fake_options({}))
    assert await concurrency.get_expert_concurrency("expert_material_parse_concurrency", 8) == 8


@pytest.mark.asyncio
async def test_concurrency_is_at_least_one(monkeypatch):
    monkeypatch.setattr(concurrency, "system_options", _fake_options({"expert_material_parse_concurrency": 0}))
    assert await concurrency.get_expert_concurrency("expert_material_parse_concurrency", 8) == 1
