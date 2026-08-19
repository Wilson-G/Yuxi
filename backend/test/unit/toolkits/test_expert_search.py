"""专家 Skill 检索工具的线程投影、兼容索引与门控测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.skills.service import get_thread_skills_root_dir
from yuxi.agents.toolkits.buildin import expert_search
from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.service import get_tool_instances_by_category

pytestmark = pytest.mark.unit


def _runtime(*, thread_id: str, slug: str, skills_thread_id: str | None = None):
    context = {
        "thread_id": thread_id,
        "_readable_skills": [slug],
        "_runtime_skill_dependency_map": {
            slug: {"tools": ["expert_search"], "mcps": [], "skills": []},
        },
        "tools": [],
        "mcps": [],
    }
    if skills_thread_id:
        context["skills_thread_id"] = skills_thread_id
    return SimpleNamespace(context=SimpleNamespace(**context))


def _index_row(path: str, term: str, title: str) -> dict:
    return {
        "path": path,
        "title": title,
        "headings": [title],
        "preview": f"{title} preview",
        "chars": 20,
        "term_weights": {term: 8},
    }


def _write_index(skill_root: Path, relative_path: str, rows: list[dict]) -> Path:
    index_path = skill_root / relative_path
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return index_path


@pytest.mark.asyncio
async def test_expert_search_reads_legacy_index_from_current_skill_projection(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    slug = "legacy-expert"
    skills_thread_id = "parent-skill-scope"
    skill_root = get_thread_skills_root_dir(skills_thread_id) / slug
    reference = skill_root / "references" / "rollback.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("错误率超过阈值时回滚。", encoding="utf-8")
    _write_index(
        skill_root,
        "nav/search-index.jsonl",
        [_index_row("references/rollback.md", "回滚", "回滚条件")],
    )

    global_skill = tmp_path / "skills" / slug
    global_reference = global_skill / "references" / "global.md"
    global_reference.parent.mkdir(parents=True)
    global_reference.write_text("不应读取的全局版本", encoding="utf-8")
    _write_index(
        global_skill,
        "nav/search-index.jsonl",
        [_index_row("references/global.md", "回滚", "全局版本")],
    )

    result = await expert_search.coroutine(
        skill_slug=slug,
        query="回滚",
        limit=3,
        runtime=_runtime(thread_id="child-thread", skills_thread_id=skills_thread_id, slug=slug),
    )

    assert result["results"][0]["title"] == "回滚条件"
    assert result["results"][0]["file_path"] == f"/home/gem/skills/{slug}/references/rollback.md"


@pytest.mark.asyncio
async def test_expert_search_prefers_new_index_and_reads_only_selected_index(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    slug = "new-expert"
    skill_root = get_thread_skills_root_dir("thread-1") / slug
    wiki = skill_root / "wiki" / "diagnosis.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text("正式诊断讲义", encoding="utf-8")
    reference = skill_root / "references" / "legacy.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("旧索引材料", encoding="utf-8")
    new_index = _write_index(
        skill_root,
        "search/index.jsonl",
        [_index_row("wiki/diagnosis.md", "诊断", "诊断讲义")],
    )
    old_index = _write_index(
        skill_root,
        "nav/search-index.jsonl",
        [_index_row("references/legacy.md", "诊断", "旧索引")],
    )

    original_read_text = Path.read_text
    read_indexes = []

    def tracked_read_text(path: Path, *args, **kwargs):
        if path in {new_index.resolve(), old_index.resolve()}:
            read_indexes.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    result = await expert_search.coroutine(
        skill_slug=slug,
        query="诊断",
        limit=3,
        runtime=_runtime(thread_id="thread-1", slug=slug),
    )

    assert read_indexes == [new_index.resolve()]
    assert result["results"][0]["file_path"] == f"/home/gem/skills/{slug}/wiki/diagnosis.md"


def test_expert_search_is_registered_only_as_a_skill_dependency_tool():
    metadata = get_extra_metadata("expert_search")

    assert metadata is not None
    assert metadata.category == "expert"
    assert "expert_search" not in {tool.name for tool in get_tool_instances_by_category("buildin")}
    assert "runtime" in expert_search._injected_args_keys


@pytest.mark.asyncio
@pytest.mark.parametrize(("activated", "expected"), [([], set()), (["expert-skill"], {"expert_search"})])
async def test_expert_search_is_hidden_until_its_skill_is_activated(activated, expected):
    runtime = _runtime(thread_id="thread-1", slug="expert-skill")

    class Request:
        def __init__(self, tools):
            self.runtime = runtime
            self.state = {"activated_skills": activated}
            self.tools = tools

        def override(self, *, tools):
            return Request(tools)

    captured = set()

    async def handler(request):
        captured.update(tool.name for tool in request.tools)
        return "ok"

    await SkillsMiddleware(enable_skills_prompt=False).awrap_model_call(Request([expert_search]), handler)

    assert captured == expected


@pytest.mark.asyncio
async def test_expert_search_rejects_skill_outside_runtime_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    runtime = SimpleNamespace(context=SimpleNamespace(thread_id="thread-1", _readable_skills=[]))

    with pytest.raises(ValueError, match="无权"):
        await expert_search.coroutine(
            skill_slug="private-expert",
            query="诊断",
            limit=3,
            runtime=runtime,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("result_path", ["../outside.md", "/tmp/outside.md", "source/raw.md"])
async def test_expert_search_rejects_index_results_outside_final_skill(result_path, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    slug = "bounded-expert"
    skill_root = get_thread_skills_root_dir("thread-1") / slug
    source = skill_root / "source" / "raw.md"
    source.parent.mkdir(parents=True)
    source.write_text("不允许发布的项目材料", encoding="utf-8")
    _write_index(
        skill_root,
        "search/index.jsonl",
        [_index_row(result_path, "边界", "越界结果")],
    )

    with pytest.raises(ValueError, match="无效结果路径"):
        await expert_search.coroutine(
            skill_slug=slug,
            query="边界",
            limit=3,
            runtime=_runtime(thread_id="thread-1", slug=slug),
        )
