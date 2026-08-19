"""专家 Skill 最终发布边界与运行时检索测试。"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from yuxi.agents.skills.service import parse_skill_dir_metadata
from yuxi.expert import publisher, storage

pytestmark = pytest.mark.unit

PROJECT_ID = "5" * 32


@pytest.mark.asyncio
async def test_candidate_skill_contains_only_runtime_outputs_selected_evidence_and_valid_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    project_root = storage.create_project_workspace(PROJECT_ID)
    text_path = "text/material-1.md"
    reliable_text = "\n".join(
        [
            "RAW-LINE-NOT-CITED-FIRST",
            "条件反转信号出现时应重新判断",
            "证据不足时必须停止",
            "RAW-LINE-NOT-CITED-LAST",
        ]
    )
    (project_root / text_path).write_text(reliable_text, encoding="utf-8")

    project = SimpleNamespace(
        project_id=PROJECT_ID,
        organization_version=1,
        skill_slug="decision-method-expert",
        name="判断方法专家",
        description="依据正式讲义说明判断条件和边界",
        prompt_pack={"PRIVATE-PROMPT": "不应发布"},
        message="PRIVATE-PROGRESS",
        release_hash="PRIVATE-HASH",
        manifest="PRIVATE-MANIFEST",
    )
    material = SimpleNamespace(
        material_id="material-1",
        material_unit_id=None,
        title="老师口述案例",
        source_path="series/source-1.md",
        text_path=text_path,
        line_count=4,
        status="passed",
        note="PRIVATE-NOTE",
        review="PRIVATE-REVIEW",
    )
    citation = {
        "note_alias": "N001",
        "start_line": 2,
        "end_line": 3,
        "supports": "条件反转与停止线",
    }
    topic = SimpleNamespace(
        topic_key="diagnosis",
        title="识别条件与停止线",
        section="判断方法",
        reader_question="什么时候需要反转判断或停止？",
        purpose="帮助读者识别反转信号",
        boundaries="资料不足时停止",
        position=1,
        material_ids=["material-1"],
        status="passed",
        handout="# 识别条件与停止线\n\n先识别条件，再判断是否需要反转或停止。",
        handout_contract={
            "topic_key": "diagnosis",
            "handout_markdown": "正式讲义",
            "citations": [citation],
        },
        review={"decision": "pass", "summary": "PRIVATE-REVIEW"},
    )

    relative = publisher.build_candidate_skill(
        project=project,
        materials=[material],
        topics=[topic],
        navigation="遇到条件变化时进入《识别条件与停止线》。",
        skill_body="这位专家解释判断顺序、适用条件与停止边界。",
    )
    skill_root = project_root / relative

    assert {path.name for path in skill_root.iterdir()} == publisher.FINAL_SKILL_ENTRIES
    assert publisher.FINAL_SKILL_ENTRIES == {
        "SKILL.md",
        "nav",
        "references",
        "wiki",
        "search",
        "sources.jsonl",
    }
    assert not any(path.name in {"manifest.json", "metadata.json"} for path in skill_root.rglob("*"))

    references = list((skill_root / "references").glob("*.md"))
    assert len(references) == 1
    assert not (skill_root / "references" / "images").exists()
    reference_text = references[0].read_text(encoding="utf-8")
    assert "L2: 条件反转信号出现时应重新判断" in reference_text
    assert "L3: 证据不足时必须停止" in reference_text
    assert "RAW-LINE-NOT-CITED-FIRST" not in reference_text
    assert "RAW-LINE-NOT-CITED-LAST" not in reference_text

    [source_row] = [
        json.loads(line)
        for line in (skill_root / "sources.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert source_row == {
        "evidence_id": "E0001",
        "reference_path": references[0].relative_to(skill_root).as_posix(),
        "source_title": "老师口述案例",
        "source_path": "series/source-1.md",
        "start_line": 2,
        "end_line": 3,
        "supports": ["条件反转与停止线"],
        "handout_topic_keys": ["diagnosis"],
    }

    [wiki] = list((skill_root / "wiki").glob("*.md"))
    wiki_text = wiki.read_text(encoding="utf-8")
    nav_text = (skill_root / "nav" / "index.md").read_text(encoding="utf-8")
    assert f"../{references[0].relative_to(skill_root).as_posix()}" in wiki_text
    assert f"../{wiki.relative_to(skill_root).as_posix()}" in nav_text

    search_rows = [
        json.loads(line)
        for line in (skill_root / "search" / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["path"] for row in search_rows} == {
        references[0].relative_to(skill_root).as_posix(),
        wiki.relative_to(skill_root).as_posix(),
        "nav/index.md",
    }
    assert all((skill_root / row["path"]).is_file() for row in search_rows)

    metadata = parse_skill_dir_metadata(skill_root)
    assert metadata["slug"] == "decision-method-expert"
    assert metadata["tool_dependencies"] == ["expert_search"]
    publisher.validate_candidate_skill(skill_root)

    published_text = "\n".join(path.read_text(encoding="utf-8") for path in skill_root.rglob("*") if path.is_file())
    for private_marker in (
        "PRIVATE-PROMPT",
        "PRIVATE-REVIEW",
        "PRIVATE-PROGRESS",
        "PRIVATE-MANIFEST",
        "PRIVATE-HASH",
        "PRIVATE-NOTE",
        "RAW-LINE-NOT-CITED-FIRST",
        "RAW-LINE-NOT-CITED-LAST",
    ):
        assert private_marker not in published_text

    search_module = importlib.import_module("yuxi.agents.toolkits.buildin.expert_search")
    projection_root = tmp_path / "thread-skills"
    projection_root.mkdir()
    shutil.copytree(skill_root, projection_root / project.skill_slug)
    monkeypatch.setattr(search_module, "get_thread_skills_root_dir", lambda _thread_id: projection_root)
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _readable_skills=[project.skill_slug],
            skills_thread_id="thread-test",
        )
    )

    result = await search_module.expert_search.coroutine(
        skill_slug=project.skill_slug,
        query="条件反转与停止",
        limit=6,
        runtime=runtime,
    )

    assert result["results"]
    for item in result["results"]:
        relative_result = item["file_path"].removeprefix(f"/home/gem/skills/{project.skill_slug}/")
        assert relative_result.split("/", 1)[0] in {"wiki", "nav", "references"}
        assert (skill_root / relative_result).is_file()

    source_row["reference_path"] = f"references/../{wiki.relative_to(skill_root).as_posix()}"
    (skill_root / "sources.jsonl").write_text(
        json.dumps(source_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="指向无效证据"):
        publisher.validate_candidate_skill(skill_root)


@pytest.mark.parametrize(
    ("material_fields", "image_object"),
    [
        (
            {
                "material_unit_id": "unit-1",
                "source_file_id": "source-1",
                "source_start_line": 1,
                "source_end_line": 3,
            },
            "material-libraries/library-1/source-files/source-1/images/diagram 1.png",
        ),
        (
            {
                "material_unit_id": None,
                "source_file_id": None,
                "source_start_line": None,
                "source_end_line": None,
            },
            f"expert-projects/{PROJECT_ID}/material-1/diagram 1.png",
        ),
    ],
    ids=["material-library", "legacy-project"],
)
def test_candidate_skill_packages_only_selected_private_evidence_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    material_fields: dict,
    image_object: str,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    project_root = storage.create_project_output_workspace(PROJECT_ID)
    project = SimpleNamespace(
        project_id=PROJECT_ID,
        organization_version=1,
        skill_slug="image-evidence-expert",
        name="含图证据专家",
        description="依据含图精选证据回答",
    )
    material = SimpleNamespace(
        material_id="material-1",
        title="含图材料",
        source_path="books/illustrated.md",
        text_path=None,
        line_count=3,
        status="passed",
        **material_fields,
    )
    topic = SimpleNamespace(
        topic_key="illustrated-evidence",
        title="含图判断",
        reader_question="图片如何支撑判断？",
        position=1,
        handout="# 含图判断",
        handout_contract={
            "citations": [
                {
                    "note_alias": "N001",
                    "start_line": 2,
                    "end_line": 3,
                    "supports": "图片中的关键判断",
                }
            ]
        },
    )
    uncited_object = image_object.replace("diagram 1.png", "uncited.png")
    source_text = "\n".join(
        [
            f"![未选中图片](/api/document-images/{quote(uncited_object, safe='/')})",
            "图示中的条件发生变化",
            f"![证据图](/api/document-images/{quote(image_object, safe='/')})",
        ]
    )

    class FakeMinio:
        """记录候选 Skill 实际读取的私有图片对象。"""

        def __init__(self) -> None:
            self.downloaded: list[tuple[str, str]] = []

        def download_file(self, bucket_name: str, object_name: str) -> bytes:
            self.downloaded.append((bucket_name, object_name))
            return b"selected-image-bytes"

    minio = FakeMinio()
    monkeypatch.setattr(publisher, "get_minio_client", lambda: minio)

    relative = publisher.build_candidate_skill(
        project=project,
        materials=[material],
        material_source_texts={"material-1": source_text},
        topics=[topic],
        navigation="进入含图判断讲义。",
        skill_body="按精选证据中的文字和图片回答。",
    )
    skill_root = project_root / relative

    assert {path.name for path in skill_root.iterdir()} == publisher.FINAL_SKILL_ENTRIES
    [reference] = list((skill_root / "references").glob("*.md"))
    [image] = list((skill_root / "references" / "images").iterdir())
    assert image.read_bytes() == b"selected-image-bytes"
    assert f"![证据图](images/{image.name})" in reference.read_text(encoding="utf-8")
    assert minio.downloaded == [("kb-images", image_object)]

    published_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in skill_root.rglob("*")
        if path.is_file() and path.suffix in {".md", ".jsonl"}
    )
    assert "/api/document-images/" not in published_text
    assert "uncited.png" not in published_text
    publisher.validate_candidate_skill(skill_root)


def test_candidate_skill_rebuilds_existing_version_from_current_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    project_root = storage.create_project_workspace(PROJECT_ID)
    text_path = "text/material-1.md"
    (project_root / text_path).write_text("一条可靠证据", encoding="utf-8")
    project = SimpleNamespace(
        project_id=PROJECT_ID,
        organization_version=1,
        skill_slug="decision-method-expert",
        name="判断方法专家",
        description="依据正式讲义说明判断条件和边界",
    )
    material = SimpleNamespace(
        material_id="material-1",
        material_unit_id=None,
        title="老师口述案例",
        source_path="series/source-1.md",
        text_path=text_path,
        line_count=1,
        status="passed",
    )
    topic = SimpleNamespace(
        topic_key="diagnosis",
        title="识别条件",
        reader_question="如何识别条件？",
        position=1,
        handout="# 旧讲义",
        handout_contract={
            "citations": [
                {
                    "note_alias": "N001",
                    "start_line": 1,
                    "end_line": 1,
                    "supports": "条件判断",
                }
            ]
        },
    )

    relative = publisher.build_candidate_skill(
        project=project,
        materials=[material],
        topics=[topic],
        navigation="旧导航",
        skill_body="旧专家入口",
    )

    topic.handout_contract["citations"][0]["end_line"] = 2
    with pytest.raises(ValueError, match="材料证据引用超出可靠正文范围"):
        publisher.build_candidate_skill(
            project=project,
            materials=[material],
            topics=[topic],
            navigation="未通过验证的导航",
            skill_body="未通过验证的专家入口",
        )
    skill_root = project_root / relative
    assert "旧导航" in (skill_root / "nav" / "index.md").read_text(encoding="utf-8")

    topic.handout_contract["citations"][0]["end_line"] = 1
    topic.handout = "# 新讲义"
    rebuilt_relative = publisher.build_candidate_skill(
        project=project,
        materials=[material],
        topics=[topic],
        navigation="新导航",
        skill_body="新专家入口",
    )

    skill_root = project_root / rebuilt_relative
    assert rebuilt_relative == relative == "release/v1"
    assert "新导航" in (skill_root / "nav" / "index.md").read_text(encoding="utf-8")
    assert "旧导航" not in (skill_root / "nav" / "index.md").read_text(encoding="utf-8")
    assert "# 新讲义" in next((skill_root / "wiki").glob("*.md")).read_text(encoding="utf-8")
    assert "新专家入口" in (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert not any(path.name.startswith(".candidate-") for path in (project_root / "release").iterdir())
