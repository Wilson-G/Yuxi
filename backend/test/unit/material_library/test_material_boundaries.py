"""资料库 Markdown 结构候选与模型边界确认测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from yuxi.material_library import boundary

pytestmark = pytest.mark.unit


class FakeModel:
    """按顺序返回边界确认结果并保留完整调用输入。"""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=json.dumps(self.responses.pop(0), ensure_ascii=False))


def _tasks(monkeypatch: pytest.MonkeyPatch, responses: list[dict], *, context_length: int = 100_000):
    model = FakeModel(responses)
    monkeypatch.setattr(
        boundary.model_cache,
        "get_model_info",
        lambda _model_spec: SimpleNamespace(context_length=context_length),
    )
    return boundary.MaterialBoundaryModelTasks(lambda *_args, **_kwargs: model), model


def test_markdown_parser_preserves_visible_heading_structure_and_source_lines() -> None:
    markdown = "卷一\n===\n总述\n\n## 内部主题\n细节\n\n卷二\n===\n结论\n"

    headings, candidates = boundary.extract_structure_candidates(markdown)

    assert [(heading.level, heading.title, heading.start_line, heading.end_line) for heading in headings] == [
        (1, "卷一", 1, 2),
        (2, "内部主题", 5, 5),
        (1, "卷二", 8, 9),
    ]
    assert [(candidate.title, candidate.start_line, candidate.end_line) for candidate in candidates] == [
        ("卷一", 1, 7),
        ("卷二", 8, 10),
    ]


async def test_nested_heading_candidate_keeps_parent_subtree_for_model_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks, model = _tasks(
        monkeypatch,
        [
            {
                "decision": "ready",
                "units": [
                    {"title": "子主题一", "start_line": 1, "end_line": 3},
                    {"title": "子主题二", "start_line": 4, "end_line": 5},
                ],
            },
            {
                "decision": "ready",
                "units": [{"title": "另一个主题", "start_line": 6, "end_line": 7}],
            },
        ],
    )
    markdown = "## 父标题\n### 子标题一\n子一正文\n### 子标题二\n子二正文\n## 另一个父标题\n另一个正文\n"

    units = await tasks.confirm_units(model_spec="provider:model", source_path="books/nested.md", markdown=markdown)

    assert [(unit.title, unit.start_line, unit.end_line) for unit in units] == [
        ("子主题一", 1, 3),
        ("子主题二", 4, 5),
        ("另一个主题", 6, 7),
    ]
    first_prompt = model.calls[0][-1].content
    assert "L1: ## 父标题" in first_prompt
    assert "L2: ### 子标题一" in first_prompt
    assert "L3: 子一正文" in first_prompt
    assert "L4: ### 子标题二" in first_prompt
    assert "L5: 子二正文" in first_prompt
    assert len(model.calls) == 2


async def test_short_markdown_without_heading_is_confirmed_as_complete_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, model = _tasks(
        monkeypatch,
        [
            {
                "decision": "ready",
                "units": [{"title": "完整短文", "start_line": 1, "end_line": 3}],
            }
        ],
    )
    markdown = "第一段\n第二段\n结语\n"

    units = await tasks.confirm_units(model_spec="provider:model", source_path="notes/short.md", markdown=markdown)

    assert units == [boundary.MaterialRange(title="完整短文", start_line=1, end_line=3)]
    prompt = model.calls[0][-1].content
    assert "无可见 Markdown 标题" in prompt
    assert "L1: 第一段" in prompt
    assert "L3: 结语" in prompt


async def test_model_can_merge_previous_candidate_and_split_current_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, model = _tasks(
        monkeypatch,
        [
            {
                "decision": "ready",
                "units": [{"title": "甲", "start_line": 1, "end_line": 2}],
            },
            {
                "decision": "ready",
                "units": [{"title": "甲乙合题", "start_line": 1, "end_line": 4}],
            },
            {
                "decision": "ready",
                "units": [
                    {"title": "丙之一", "start_line": 5, "end_line": 5},
                    {"title": "丙之二", "start_line": 6, "end_line": 6},
                ],
            },
        ],
    )
    markdown = "# 甲\n甲正文\n# 乙\n乙正文\n# 丙\n丙正文\n"

    units = await tasks.confirm_units(model_spec="provider:model", source_path="books/book.md", markdown=markdown)

    assert [(unit.title, unit.start_line, unit.end_line) for unit in units] == [
        ("甲乙合题", 1, 4),
        ("丙之一", 5, 5),
        ("丙之二", 6, 6),
    ]
    middle_prompt = model.calls[1][-1].content
    assert "前一已确认相邻范围" in middle_prompt
    assert "当前候选" in middle_prompt
    assert "后一候选" in middle_prompt
    for line_number in range(1, 7):
        assert f"L{line_number}:" in middle_prompt


async def test_unstructured_markdown_over_context_enters_needs_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, model = _tasks(monkeypatch, [], context_length=1)

    with pytest.raises(boundary.MaterialBoundaryNeedsAttention, match="超过默认模型"):
        await tasks.confirm_units(
            model_spec="provider:small-model",
            source_path="books/unstructured.md",
            markdown="没有标题的完整正文\n第二行\n",
        )

    assert model.calls == []


async def test_model_can_explicitly_mark_boundary_as_needs_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks, _model = _tasks(
        monkeypatch,
        [
            {
                "decision": "needs_attention",
                "reason": "正文在主题结论前中断，无法形成完整单元",
                "units": [],
            }
        ],
    )

    with pytest.raises(boundary.MaterialBoundaryNeedsAttention, match="正文在主题结论前中断"):
        await tasks.confirm_units(
            model_spec="provider:model",
            source_path="books/incomplete.md",
            markdown="# 未完成主题\n只有开头\n",
        )


@pytest.mark.parametrize(
    "units,error",
    [
        (
            [
                {"title": "重叠一", "start_line": 1, "end_line": 2},
                {"title": "重叠二", "start_line": 2, "end_line": 3},
            ],
            "重叠或缺口",
        ),
        ([{"title": "越界", "start_line": 1, "end_line": 4}], "行范围越界"),
    ],
)
async def test_model_ranges_must_not_overlap_or_exceed_source(
    monkeypatch: pytest.MonkeyPatch,
    units: list[dict],
    error: str,
) -> None:
    tasks, _model = _tasks(monkeypatch, [{"decision": "ready", "units": units}])

    with pytest.raises(boundary.MaterialBoundaryNeedsAttention, match=error):
        await tasks.confirm_units(
            model_spec="provider:model",
            source_path="notes/ranges.md",
            markdown="第一行\n第二行\n第三行\n",
        )
