"""Create manual-audit sheets with pre-defined samples (chosen before reading any label).

Run from starter/:  python -m scripts.make_audit
Writes (UTF-8 CSV, open in Excel/VS Code):
  results/audit_coherence.csv  10 source positions x {A, B}  (C uses B's chunks -> same label)
       position = middle character of the normalized text of the first 10 content pages
       (PDF pages 251-260, sorted). The chunk containing that offset is shown.
  results/audit_metadata.csv   first 5 test questions (t17-t21) x top-3 x {A, B, C}
       auto_* columns: independent check against the PDF (raw PyMuPDF text of the stated page,
       chapter page range, section headings in the manifest).
  results/audit_grounding.csv  first 5 test questions x {A, B, C}; top-3 texts side by side.
Columns `label` / `reason` are filled by the grader (0/1/2 scale in GUIDE §4).
Existing labels are preserved when the script is re-run.
"""
import csv
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.chunking import build_chunks  # noqa: E402
from src.config import STRATEGIES  # noqa: E402
from src.ingestion import CHAPTERS, PDF_PATH, load_corpus, normalize_for_match  # noqa: E402

STARTER = Path(__file__).resolve().parents[1]
RES = STARTER / "results"
MANIFEST = STARTER.parent / "data" / "manifest.json"


def _keep_labels(path: Path, key_cols: list[str]) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {tuple(r[k] for k in key_cols): r for r in csv.DictReader(f)}


def _write(path: Path, rows: list[dict], key_cols: list[str], label_cols: list[str]) -> None:
    old = _keep_labels(path, key_cols)
    for r in rows:
        prev = old.get(tuple(str(r[k]) for k in key_cols), {})
        for c in label_cols:
            r[c] = prev.get(c, r.get(c, ""))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{path.name}: {len(rows)} rows")


def coherence(pages) -> None:
    sample = sorted(pages, key=lambda p: p.page)[:10]
    chunks = {i: build_chunks(sample, STRATEGIES[i]["chunking"], STRATEGIES[i]["params"]) for i in ("A", "B")}
    rows = []
    for p in sample:
        pos = len(p.text) // 2
        for idx in ("A", "B"):
            c = next(c for c in chunks[idx] if c.page == p.page
                     and c.metadata["char_start"] <= pos < c.metadata["char_end"])
            rows.append({"position_id": f"p{p.page}@{pos}", "index": idx, "applies_to": "A" if idx == "A" else "B,C",
                         "chunk_id": c.chunk_id, "chunk_len": len(c.text),
                         "source_context": p.text[max(0, pos - 80):pos + 80].replace("\n", " "),
                         "chunk_text": c.text, "label": "", "reason": "", "labeled_by": ""})
    _write(RES / "audit_coherence.csv", rows, ["position_id", "index"], ["label", "reason", "labeled_by", "ai_label", "ai_reason"])


def metadata_and_grounding() -> None:
    manifest = {p["page"]: p for p in json.loads(MANIFEST.read_text(encoding="utf-8"))["sources"][0]["pages"]}
    with pymupdf.open(PDF_PATH) as doc:
        raw = {pg: normalize_for_match(doc[pg - 1].get_text()) for pg in manifest}
    ch_range = {c["chapter"]: range(c["first_page"], c["last_page"] + 1) for c in CHAPTERS}
    meta_rows, ground_rows = [], []
    for s in STRATEGIES:
        traces = [json.loads(line) for line in (RES / f"traces_{s}_test.jsonl").read_text(encoding="utf-8").splitlines()]
        for t in sorted(traces, key=lambda t: t["id"])[:5]:
            for h in t["result"]["top"]:
                body = [x for x in h["text"].split("\n\n") if not x.startswith("#")]
                probe = normalize_for_match(max(body or [h["text"]], key=len))[:120]
                page_ok = probe in raw.get(h["page"], "")
                chapter_ok = h["page"] in ch_range.get(h["chapter"], [])
                section_ok = h["section"] in manifest.get(h["page"], {}).get("sections_on_page", []) \
                    or h["section"] == manifest.get(h["page"], {}).get("section")
                filt = t["filter_applied"]
                meta_rows.append({
                    "strategy": s, "id": t["id"], "rank": h["rank"], "chunk_id": h["chunk_id"],
                    "source": h["source"], "page": h["page"], "printed_page": h["printed_page"],
                    "chapter": h["chapter"], "section": h["section"], "filter_applied": json.dumps(filt) if filt else "",
                    "auto_source_ok": h["source"] == "Introduction-Behavioral-Neuroscience_-_WEB.pdf",
                    "auto_page_text_in_pdf_page": page_ok, "auto_chapter_ok": chapter_ok,
                    "auto_section_ok": section_ok,
                    "auto_filter_ok": "" if not filt else all(str(h[k]) == str(v) for k, v in filt.items()),
                    "label": "", "reason": "", "labeled_by": ""})
            ground_rows.append({
                "strategy": s, "id": t["id"], "question": t["question"], "expected_answer": t["expected_answer"],
                "gold_page": t["gold_page"], "filter_applied": json.dumps(t["filter_applied"]) if t["filter_applied"] else "",
                "auto_evidence_hit_at_3": t["result"]["hit_at_3"],
                **{f"top{h['rank']}": f"[p{h['page']} {h['chunk_id'].split(':')[-1]} s={h['score']}] {h['text']}"
                   for h in t["result"]["top"]},
                "label": "", "reason": "", "labeled_by": ""})
    _write(RES / "audit_metadata.csv", meta_rows, ["strategy", "id", "rank"], ["label", "reason", "labeled_by", "ai_label", "ai_reason"])
    _write(RES / "audit_grounding.csv", ground_rows, ["strategy", "id"], ["label", "reason", "labeled_by", "ai_label", "ai_reason"])


if __name__ == "__main__":
    coherence(load_corpus())
    metadata_and_grounding()
