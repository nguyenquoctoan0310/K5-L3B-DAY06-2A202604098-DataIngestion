"""Chunk statistics for each index (A, B) -> results/chunk_stats.json.

Run from starter/:  python -m scripts.chunk_stats
Evidence-intact check uses DEV questions only (test is not looked at while tuning).
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.chunking import build_chunks  # noqa: E402
from src.config import STRATEGIES  # noqa: E402
from src.ingestion import load_corpus, normalize_for_match  # noqa: E402

STARTER = Path(__file__).resolve().parents[1]
BENCHMARK = STARTER.parent / "benchmark" / "questions.jsonl"


def main() -> None:
    pages = load_corpus()
    dev = [q for q in map(json.loads, BENCHMARK.read_text(encoding="utf-8").splitlines())
           if q["split"] == "dev"]
    report = {}
    for index in sorted({s["index"] for s in STRATEGIES.values()}):
        cfg = next(s for s in STRATEGIES.values() if s["index"] == index)
        t0 = time.perf_counter()
        chunks = build_chunks(pages, cfg["chunking"], cfg["params"])
        elapsed = time.perf_counter() - t0
        lengths = [len(c.text) for c in chunks]
        ids = [c.chunk_id for c in chunks]
        again = [c.chunk_id + c.metadata["content_hash"] for c in build_chunks(pages, cfg["chunking"], cfg["params"])]
        intact, split_ev = [], []
        for q in dev:
            quote = normalize_for_match(q["evidence_quote"])
            on_gold = [c for c in chunks if c.page in q["gold_page"]]
            (intact if any(quote in normalize_for_match(c.text) for c in on_gold) else split_ev).append(q["id"])
        report[index] = {
            "chunking": cfg["chunking"], "params": cfg["params"],
            "chunks": len(chunks), "total_chars": sum(lengths),
            "approx_tokens": round(sum(lengths) / 3.97),        # measured chars/token for e5 on this corpus
            "len_min": min(lengths), "len_median": statistics.median(lengths),
            "len_mean": round(statistics.mean(lengths), 1), "len_max": max(lengths),
            "len_lt_200": sum(n < 200 for n in lengths),
            "unique_ids": len(set(ids)) == len(ids),
            "deterministic": again == [c.chunk_id + c.metadata["content_hash"] for c in chunks],
            "chunking_seconds": round(elapsed, 3),
            "dev_evidence_intact": f"{len(intact)}/{len(dev)}",
            "dev_evidence_split": split_ev,
        }
    out = STARTER / "results" / "chunk_stats.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
