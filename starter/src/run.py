"""Day 06 pipeline entry point.

    python -m src.run ingest      # load corpus -> chunk A/B -> embed changed chunks -> upsert
    python -m src.run evaluate    # (next step) run benchmark for A/B/C, save traces + metrics

Run from starter/. Writes results/ingest_run.json.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .chunking import build_chunks
from .config import STRATEGIES
from .embeddings import MyEmbedder
from .ingestion import load_corpus
from .store import PgVectorStore

STARTER = Path(__file__).resolve().parents[1]
RESULTS = STARTER / "results"


def _env() -> str:
    load_dotenv(STARTER / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL missing in starter/.env")
    return url


def ingest() -> dict:
    url = _env()
    t0 = time.perf_counter()
    log: list[dict] = []
    pages = load_corpus(log)
    if len(pages) < 30:
        raise RuntimeError(f"only {len(pages)} content pages (need >= 30)")
    t_load = time.perf_counter() - t0

    embedder = MyEmbedder()
    store = PgVectorStore(url, embedder.dimensions)
    store.create_schema()

    report = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "pages": len(pages), "load_seconds": round(t_load, 2), "indexes": {}}
    for index in sorted({s["index"] for s in STRATEGIES.values()}):
        cfg = next(s for s in STRATEGIES.values() if s["index"] == index)
        t1 = time.perf_counter()
        chunks = build_chunks(pages, cfg["chunking"], cfg["params"])
        existing = store.existing(index)
        changed = [c for c in chunks
                   if existing.get(c.chunk_id) != (c.metadata["content_hash"], embedder.model_name)]
        t2 = time.perf_counter()
        vectors = embedder.embed_documents([c.text for c in changed])
        t3 = time.perf_counter()
        stats = store.upsert(index, changed, vectors, embedder.model_name,
                             prune_document_ids={c.document_id for c in chunks},
                             keep_ids={c.chunk_id for c in chunks})
        t4 = time.perf_counter()
        stats["unchanged"] = len(chunks) - len(changed)
        rows = store.count(index)
        if rows != len(chunks):
            raise RuntimeError(f"index {index}: {rows} rows in DB but {len(chunks)} chunks -> duplicates/stale rows")
        report["indexes"][index] = {
            "chunking": cfg["chunking"], "params": cfg["params"], "chunks": len(chunks), "rows_in_db": rows,
            **stats, "embedded": len(changed),
            "chunk_seconds": round(t2 - t1, 2), "embed_seconds": round(t3 - t2, 2),
            "upsert_seconds": round(t4 - t3, 2), "total_seconds": round(t4 - t1, 2),
        }
        print(f"[{index}] {report['indexes'][index]}")
    report["embedding"] = embedder.info()
    report["warnings"] = [e for e in log if e["level"] != "INFO"]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "ingest_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


BENCHMARK = STARTER.parent / "benchmark" / "questions.jsonl"


def _load_questions() -> list[dict]:
    return [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(split: str) -> dict:
    """Run every strategy on one split; save full top-3 traces and metrics.

    Order is enforced: `--split test` refuses to run before results/selection.json
    (the choice made on dev with the pre-registered criterion) exists.
    """
    from .config import TOP_K
    from .evaluation import evaluate_query, summarize

    url = _env()
    selection_file = RESULTS / "selection.json"
    if split == "test" and not selection_file.exists():
        raise RuntimeError("run `evaluate --split dev` first: the strategy must be chosen on dev before test")
    questions = [q for q in _load_questions() if q["split"] == split]
    embedder = MyEmbedder()
    store = PgVectorStore(url, embedder.dimensions)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    traces: list[dict] = []
    for name, cfg in STRATEGIES.items():
        for q in questions:
            filt = q["filter"] if (cfg["use_filter"] and q.get("filter")) else None
            t0 = time.perf_counter()
            qv = embedder.embed_query(q["question"])          # question text only: no gold, no answer
            hits = store.search(cfg["index"], qv, TOP_K, filt)
            latency = time.perf_counter() - t0
            traces.append({
                "strategy": name, "index": cfg["index"], "split": split, "id": q["id"], "type": q["type"],
                "question": q["question"], "question_filter": q.get("filter"), "filter_applied": filt,
                "gold_source_id": q["gold_source_id"], "gold_page": q["gold_page"],
                "evidence_quote": q["evidence_quote"], "expected_answer": q["expected_answer"],
                "latency_ms": round(latency * 1000, 1), "result": evaluate_query(q, hits),
            })
    metrics = summarize(traces)
    for name in STRATEGIES:
        lat = [t["latency_ms"] for t in traces if t["strategy"] == name]
        metrics[name][split]["mean_query_ms"] = round(sum(lat) / len(lat), 1)

    RESULTS.mkdir(exist_ok=True)
    for name in STRATEGIES:
        with (RESULTS / f"traces_{name}_{split}.jsonl").open("w", encoding="utf-8") as f:
            for t in traces:
                if t["strategy"] == name:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
    out = {"run_at": run_at, "split": split, "top_k": TOP_K, "embedding": embedder.info(),
           "strategies": {k: {kk: vv for kk, vv in v.items()} for k, v in STRATEGIES.items()},
           "metrics": {k: v[split] for k, v in metrics.items()}}
    (RESULTS / f"metrics_{split}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    if split == "dev":
        # Pre-registered criterion (benchmark/README.md): Hit@3, then Hit@1; a remaining tie is
        # broken by manual coherence, then by resources (fewer chunks).
        def key(name: str) -> tuple[int, int]:
            m = metrics[name]["dev"]
            return (int(m["evidence_hit_at_3"].split("/")[0]), int(m["hit_at_1"].split("/")[0]))
        ranked = sorted(STRATEGIES, key=key, reverse=True)
        best = [n for n in ranked if key(n) == key(ranked[0])]
        selection = {"decided_at": run_at, "criterion": "dev Evidence Hit@3 > dev Hit@1 > coherence > chunk count",
                     "dev_scores": {n: {"hit_at_3": metrics[n]["dev"]["evidence_hit_at_3"],
                                        "hit_at_1": metrics[n]["dev"]["hit_at_1"]} for n in STRATEGIES},
                     "leaders_after_hit_metrics": best,
                     "selected": best[0] if len(best) == 1 else None,
                     "note": "tie -> resolve with manual coherence labels (results/audit_coherence.csv)"
                             if len(best) > 1 else "decided by retrieval metrics"}
        selection_file.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
        print("selection:", selection)
    for name in STRATEGIES:
        m = metrics[name][split]
        print(f"[{name}/{split}] Hit@1 {m['hit_at_1']}  Hit@3 {m['evidence_hit_at_3']}  page@3 {m['page_at_3']}  "
              f"filter {m['filter_compliance']}  miss@3 {m['misses_at_3']}  miss@1 {m['misses_at_1']}")
    return out


def recompute_metrics() -> None:
    """Recompute metrics_<split>.json from the saved traces (no new retrieval, test not re-run)."""
    from .evaluation import summarize
    for split in ("dev", "test"):
        path = RESULTS / f"metrics_{split}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        traces = [json.loads(line) for name in STRATEGIES
                  for line in (RESULTS / f"traces_{name}_{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        new = summarize(traces)
        for name in STRATEGIES:
            new[name][split]["mean_query_ms"] = data["metrics"][name]["mean_query_ms"]
            data["metrics"][name] = new[name][split]
        data["recomputed_from_traces_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in STRATEGIES:
            m = data["metrics"][name]
            print(f"[{name}/{split}] Hit@1 {m['hit_at_1']}  Hit@3 {m['evidence_hit_at_3']}  filter {m['filter_compliance']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 06 ingestion pipeline")
    parser.add_argument("command", choices=["ingest", "evaluate", "metrics"])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()
    if args.command == "ingest":
        ingest()
    elif args.command == "evaluate":
        evaluate(args.split)
    else:
        recompute_metrics()


if __name__ == "__main__":
    main()
