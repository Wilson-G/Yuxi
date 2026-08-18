from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.buildin.expert_search import expert_search
from yuxi.expert.index import build_document_index


def _write_index(root: Path) -> None:
    index = root / "skills" / "amazon-expert" / "nav" / "search-index.jsonl"
    index.parent.mkdir(parents=True)
    row = build_document_index(
        "ads/conversion.md",
        "# 广告转化诊断\n\n曝光正常但点击率偏低时，先检查关键词相关性和主图。",
    )
    index.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_expert_search_returns_agent_readable_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    _write_index(tmp_path)
    runtime = SimpleNamespace(context=SimpleNamespace(_readable_skills=["amazon-expert"]))

    result = await expert_search.coroutine(
        skill_slug="amazon-expert",
        query="广告点击率低",
        limit=3,
        runtime=runtime,
    )

    assert result["results"][0]["file_path"] == ("/home/gem/skills/amazon-expert/references/ads/conversion.md")
    assert "点击率" in result["results"][0]["matched_terms"]
    assert expert_search._injected_args_keys == {"runtime"}


@pytest.mark.asyncio
async def test_expert_search_rejects_skill_outside_runtime_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    _write_index(tmp_path)
    runtime = SimpleNamespace(context=SimpleNamespace(_readable_skills=[]))

    with pytest.raises(ValueError, match="无权"):
        await expert_search.coroutine(
            skill_slug="amazon-expert",
            query="广告点击率低",
            limit=3,
            runtime=runtime,
        )
