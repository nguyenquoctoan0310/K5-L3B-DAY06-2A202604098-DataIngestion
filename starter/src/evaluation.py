"""Evaluate retrieved evidence and preserve per-query traces.

Hit definition (GUIDE §4.1): a retrieved chunk is a hit when
    document_id == gold_source_id  AND  page in gold_page  AND
    normalize_for_match(evidence_quote) is a substring of normalize_for_match(chunk text).
Hit@1 = top-1 is a hit; Evidence Hit@3 = at least one hit among top-3.
`page_match` (right page, evidence not fully contained) is recorded as a diagnostic only:
it is NOT counted as a hit. Coherence and grounding are labelled by a human, never
inferred from string matching.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .ingestion import normalize_for_match


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    quote = normalize_for_match(question["evidence_quote"])
    gold_pages = set(question["gold_page"])
    records = []
    for rank, h in enumerate(hits, start=1):
        same_doc = h["document_id"] == question["gold_source_id"]
        page_match = same_doc and h["page"] in gold_pages
        is_hit = page_match and quote in normalize_for_match(h["text"])
        filt = question.get("filter") or {}
        records.append({
            "rank": rank, "chunk_id": h["chunk_id"], "score": round(float(h["score"]), 4),
            "document_id": h["document_id"], "source": h["source"], "page": h["page"],
            "chapter": h["chapter"], "section": h["section"],
            "printed_page": (h.get("metadata") or {}).get("printed_page"),
            "page_match": page_match, "evidence_hit": is_hit,
            "filter_ok": all(str(h.get(k)) == str(v) for k, v in filt.items()) if filt else None,
            "text": h["text"],
        })
    first_hit = next((r["rank"] for r in records if r["evidence_hit"]), None)
    return {
        "hit_at_1": bool(records and records[0]["evidence_hit"]),
        "hit_at_3": first_hit is not None and first_hit <= 3,
        "first_hit_rank": first_hit,
        "page_at_3": any(r["page_match"] for r in records),
        "top": records,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate by strategy and split; counts are reported as n/N, not only %."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in traces:
        groups[(t["strategy"], t["split"])].append(t)
    out: dict[str, Any] = {}
    for (strategy, split), items in sorted(groups.items()):
        n = len(items)
        h1 = sum(t["result"]["hit_at_1"] for t in items)
        h3 = sum(t["result"]["hit_at_3"] for t in items)
        p3 = sum(t["result"]["page_at_3"] for t in items)
        # Compliance is measured on every question that REQUIRES a filter, for every strategy
        # (A/B do not apply it, so this shows how often unfiltered top-3 leaves the scope).
        filt = [t for t in items if t.get("question_filter")]
        filt_ok = sum(all(r["filter_ok"] for r in t["result"]["top"]) for t in filt)
        out.setdefault(strategy, {})[split] = {
            "N": n,
            "hit_at_1": f"{h1}/{n}", "hit_at_1_pct": round(100 * h1 / n, 1),
            "evidence_hit_at_3": f"{h3}/{n}", "evidence_hit_at_3_pct": round(100 * h3 / n, 1),
            "page_at_3": f"{p3}/{n}",
            "filter_compliance": f"{filt_ok}/{len(filt)}" if filt else "N/A",
            "misses_at_3": [t["id"] for t in items if not t["result"]["hit_at_3"]],
            "misses_at_1": [t["id"] for t in items if not t["result"]["hit_at_1"]],
        }
    return out
