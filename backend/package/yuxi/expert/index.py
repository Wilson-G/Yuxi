"""专家 Skill 的轻量词项检索。"""

from __future__ import annotations

import math
import re
from collections import Counter

ASCII_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]{1,}", re.IGNORECASE)
CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
MAX_INDEX_TERMS = 320


def tokenize_for_search(text: str) -> list[str]:
    """把中英文查询转换为专家 Skill 索引使用的词项。"""

    value = str(text or "").casefold()
    terms = ASCII_TERM_RE.findall(value)
    for run in CJK_RUN_RE.findall(value):
        if len(run) <= 8:
            terms.append(run)
        for size in (2, 3):
            terms.extend(run[index : index + size] for index in range(len(run) - size + 1))
    return terms


def build_search_row(*, published_path: str, title: str, text: str) -> dict:
    """为最终 Skill 内的一份讲义、导航或精选证据生成检索行。"""

    headings = [heading.strip() for heading in HEADING_RE.findall(text) if heading.strip()]
    weighted_terms: Counter[str] = Counter(tokenize_for_search(text))
    weighted_terms.update({term: count * 4 for term, count in Counter(tokenize_for_search(title)).items()})
    weighted_terms.update({term: count * 3 for term, count in Counter(tokenize_for_search(" ".join(headings))).items()})
    top_terms = sorted(weighted_terms.items(), key=lambda item: (-item[1], item[0]))[:MAX_INDEX_TERMS]
    preview_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return {
        "path": published_path,
        "title": title,
        "headings": headings,
        "preview": " ".join(preview_lines)[:280],
        "chars": len(text),
        "term_weights": {term: weight for term, weight in top_terms},
    }


def search_documents(rows: list[dict], query: str, limit: int = 6) -> list[dict]:
    """按索引词项召回一个专家 Skill 内的候选成品。"""

    query_terms = list(dict.fromkeys(tokenize_for_search(query)))
    if not query_terms:
        return []

    document_frequency = Counter()
    for row in rows:
        document_frequency.update(set(row["term_weights"]))

    query_text = str(query or "").strip().casefold()
    total = max(1, len(rows))
    results = []
    for row in rows:
        weights = row["term_weights"]
        matched = [term for term in query_terms if term in weights]
        if not matched:
            continue

        score = sum(
            float(weights[term]) * (math.log((total + 1) / (document_frequency[term] + 1)) + 1) for term in matched
        )
        title_and_headings = " ".join([str(row.get("title") or ""), *(row.get("headings") or [])]).casefold()
        if query_text and query_text in title_and_headings:
            score += 20.0

        results.append(
            {
                "path": row["path"],
                "title": row.get("title"),
                "headings": row.get("headings") or [],
                "preview": row.get("preview") or "",
                "chars": int(row.get("chars") or 0),
                "score": round(score, 3),
                "matched_terms": sorted(matched, key=lambda term: (-len(term), term))[:12],
            }
        )

    results.sort(key=lambda item: (-item["score"], item["path"]))
    return results[: max(1, min(int(limit), 12))]


__all__ = ["build_search_row", "search_documents", "tokenize_for_search"]
