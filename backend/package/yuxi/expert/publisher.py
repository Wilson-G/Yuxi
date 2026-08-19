"""把正式讲义和精选证据组装为独立可加载的专家 Skill。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from yuxi.agents.skills.service import parse_skill_dir_metadata
from yuxi.expert.index import build_search_row
from yuxi.expert.storage import get_project_root, read_material_text

FINAL_SKILL_ENTRIES = {"SKILL.md", "nav", "references", "wiki", "search", "sources.jsonl"}


def get_project_skill_slug(project: Any) -> str:
    """返回专家项目稳定的发布 Skill slug。"""

    return project.skill_slug or f"expert-{project.project_id[:8]}"


def build_candidate_skill(
    *,
    project: Any,
    materials: list[Any],
    topics: list[Any],
    navigation: str,
    skill_body: str,
) -> str:
    """生成通过结构和引用检查的版本化候选 Skill，并原子晋升。"""

    project_root = get_project_root(project.project_id)
    release_root = project_root / "release"
    release_root.mkdir(exist_ok=True)
    target = release_root / f"v{project.organization_version}"
    temp = Path(tempfile.mkdtemp(prefix=".candidate-", dir=release_root))
    previous: Path | None = None
    try:
        _write_candidate_files(
            temp,
            project=project,
            materials=materials,
            topics=topics,
            navigation=navigation,
            skill_body=skill_body,
        )
        validate_candidate_skill(temp)
        if target.exists():
            previous = temp.with_name(f"{temp.name}.previous")
            os.replace(target, previous)
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if previous is not None and previous.exists() and not target.exists():
            os.replace(previous, target)
        raise
    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)
    return target.relative_to(project_root).as_posix()


def validate_candidate_skill(skill_root: Path) -> None:
    """检查发布 allowlist、来源映射和现有 Skill 加载合同。"""

    entries = {path.name for path in skill_root.iterdir()}
    if entries != FINAL_SKILL_ENTRIES:
        raise ValueError("候选 Skill 包含非运行时文件或缺少正式成品")
    if any(path.is_symlink() for path in skill_root.rglob("*")):
        raise ValueError("候选 Skill 不允许包含符号链接")
    for directory in ("nav", "references", "wiki", "search"):
        if not (skill_root / directory).is_dir():
            raise ValueError(f"候选 Skill 缺少 {directory}/")
    if not list((skill_root / "wiki").glob("*.md")):
        raise ValueError("候选 Skill 没有正式讲义")

    sources = []
    for line in (skill_root / "sources.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            sources.append(json.loads(line))
    if not sources:
        raise ValueError("候选 Skill 没有精选证据来源映射")
    for source in sources:
        reference = (skill_root / str(source["reference_path"])).resolve()
        if not reference.is_file() or not reference.is_relative_to((skill_root / "references").resolve()):
            raise ValueError("候选 Skill 的来源映射指向无效证据")

    parse_skill_dir_metadata(skill_root)


def _write_candidate_files(
    root: Path,
    *,
    project: Any,
    materials: list[Any],
    topics: list[Any],
    navigation: str,
    skill_body: str,
) -> None:
    for directory in ("nav", "references", "wiki", "search"):
        (root / directory).mkdir()

    material_by_id = {material.material_id: material for material in materials}
    passed_materials = [material for material in materials if material.status == "passed"]
    aliases = {material.material_id: f"N{index:03d}" for index, material in enumerate(passed_materials, start=1)}
    material_by_alias = {alias: material_by_id[material_id] for material_id, alias in aliases.items()}

    evidence_uses: dict[tuple[str, int, int], dict[str, Any]] = {}
    topic_evidence_keys: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for topic in topics:
        for citation in (topic.handout_contract or {}).get("citations") or []:
            alias = citation["note_alias"]
            material = material_by_alias.get(alias)
            if material is None:
                raise ValueError("讲义引用了当前项目范围外的材料")
            key = (material.material_id, int(citation["start_line"]), int(citation["end_line"]))
            item = evidence_uses.setdefault(
                key,
                {
                    "material": material,
                    "supports": [],
                    "topic_keys": [],
                },
            )
            if citation["supports"] not in item["supports"]:
                item["supports"].append(citation["supports"])
            if topic.topic_key not in item["topic_keys"]:
                item["topic_keys"].append(topic.topic_key)
            topic_evidence_keys[topic.topic_key].append(key)

    evidence_ids = {key: f"E{index:04d}" for index, key in enumerate(evidence_uses, start=1)}
    source_rows = []
    search_rows = []
    for key, details in evidence_uses.items():
        material_id, start_line, end_line = key
        material = details["material"]
        evidence_id = evidence_ids[key]
        lines = read_material_text(project.project_id, material.text_path).splitlines()
        if start_line < 1 or start_line > end_line or end_line > len(lines):
            raise ValueError("讲义引用行号超出可靠正文范围")
        excerpt = "\n".join(
            f"L{line_number}: {lines[line_number - 1]}" for line_number in range(start_line, end_line + 1)
        )
        reference_path = f"references/{evidence_id.lower()}.md"
        reference = (
            f"# {evidence_id} · {material.title}\n\n"
            f"来源：{material.source_path}\n\n"
            f"位置：L{start_line}-L{end_line}\n\n"
            f"支撑判断：{'；'.join(details['supports'])}\n\n"
            f"{excerpt}\n"
        )
        (root / reference_path).write_text(reference, encoding="utf-8")
        search_rows.append(build_search_row(published_path=reference_path, title=material.title, text=reference))
        source_rows.append(
            {
                "evidence_id": evidence_id,
                "reference_path": reference_path,
                "source_title": material.title,
                "source_path": material.source_path,
                "start_line": start_line,
                "end_line": end_line,
                "supports": details["supports"],
                "handout_topic_keys": details["topic_keys"],
            }
        )

    wiki_links = []
    for topic in sorted(topics, key=lambda item: (item.position, item.topic_key)):
        filename = f"{topic.position:02d}-{_safe_slug(topic.topic_key, fallback=f'topic-{topic.position}')}.md"
        wiki_path = f"wiki/{filename}"
        citations = list(dict.fromkeys(topic_evidence_keys.get(topic.topic_key, [])))
        evidence_links = "\n".join(
            f"- [{evidence_ids[key]}](../references/{evidence_ids[key].lower()}.md)" for key in citations
        )
        handout = (topic.handout or "").rstrip()
        if evidence_links:
            handout = f"{handout}\n\n## 关键依据\n\n{evidence_links}"
        handout += "\n"
        (root / wiki_path).write_text(handout, encoding="utf-8")
        wiki_links.append(f"- [{topic.title}](../{wiki_path})：{topic.reader_question}")
        search_rows.append(build_search_row(published_path=wiki_path, title=topic.title, text=handout))

    nav_text = f"# {project.name} 导航\n\n{navigation.strip()}\n\n## 讲义目录\n\n" + "\n".join(wiki_links) + "\n"
    (root / "nav" / "index.md").write_text(nav_text, encoding="utf-8")
    search_rows.append(build_search_row(published_path="nav/index.md", title=f"{project.name} 导航", text=nav_text))

    slug = get_project_skill_slug(project)
    description = project.description.strip() or f"依据 {project.name} 正式讲义和精选证据提供条件化专家判断。"
    frontmatter = yaml.safe_dump(
        {
            "slug": slug,
            "name": project.name,
            "description": description,
            "tool_dependencies": ["expert_search"],
        },
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    skill_text = (
        f"---\n{frontmatter}\n---\n\n"
        f"# {project.name}\n\n{skill_body.strip()}\n\n"
        "## 使用正式知识\n\n"
        "先阅读 `nav/index.md` 判断当前问题应进入哪篇讲义；需要定位候选成品时使用 `expert_search`。"
        "回答关键判断时读取相应 `wiki/` 讲义和其中链接的 `references/` 精选证据。"
        "资料不足或条件不明时保持停止线，不访问或猜测编译项目中的原始材料。\n"
    )
    (root / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (root / "sources.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in source_rows),
        encoding="utf-8",
    )
    (root / "search" / "index.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in search_rows),
        encoding="utf-8",
    )


def _safe_slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return slug or fallback


__all__ = ["build_candidate_skill", "get_project_skill_slug", "validate_candidate_skill"]
