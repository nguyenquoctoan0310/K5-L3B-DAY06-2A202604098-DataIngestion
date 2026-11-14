"""Verify benchmark/questions.jsonl against the normalized corpus BEFORE any retrieval run.

Run from starter/:  python -m scripts.verify_benchmark
Checks: schema, unique ids, split sizes (>=15 dev / >=5 test), >=3 filter questions with
>=1 in test, evidence_quote present on a gold_page, filter value consistent with the gold
page metadata, and lists any OTHER pages that also contain the quote (alternative gold).
Exit code 1 if any check fails.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingestion import load_corpus, normalize_for_match  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BENCHMARK = REPO / "benchmark" / "questions.jsonl"
REQUIRED = ["id", "split", "type", "question", "expected_answer", "gold_source_id",
            "gold_page", "evidence_quote", "filter"]


def main() -> int:
    questions = [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]
    pages = {(p.document_id, p.page): p for p in load_corpus()}
    norm = {k: normalize_for_match(p.text) for k, p in pages.items()}
    errors: list[str] = []
    rows = []

    ids = Counter(q.get("id") for q in questions)
    errors += [f"duplicate id {i}" for i, n in ids.items() if n > 1]

    for q in questions:
        missing = [f for f in REQUIRED if f not in q]
        if missing:
            errors.append(f"{q.get('id')}: missing fields {missing}")
            continue
        quote = normalize_for_match(q["evidence_quote"])
        found_on = [pg for (doc, pg) in norm if doc == q["gold_source_id"] and quote in norm[(doc, pg)]]
        on_gold = [pg for pg in q["gold_page"] if pg in found_on]
        status = "OK"
        if not on_gold:
            status = "NOT FOUND on gold page"
            errors.append(f"{q['id']}: evidence not found on gold_page {q['gold_page']} (found on {found_on})")
        if q["filter"]:
            for key, val in q["filter"].items():
                for pg in q["gold_page"]:
                    meta = pages[(q["gold_source_id"], pg)].metadata
                    if str(meta.get(key)) != str(val):
                        errors.append(f"{q['id']}: filter {key}={val} but gold page {pg} has {key}={meta.get(key)}")
        alt = sorted(set(found_on) - set(q["gold_page"]))
        rows.append((q["id"], q["split"], q["type"], q["gold_page"], q["filter"] or "-", status, alt or "-"))

    split = Counter(q["split"] for q in questions)
    filt = Counter(q["split"] for q in questions if q.get("filter"))
    if split["dev"] < 15 or split["test"] < 5:
        errors.append(f"split sizes dev={split['dev']} test={split['test']} (need >=15/>=5)")
    if sum(filt.values()) < 3 or filt["test"] < 1:
        errors.append(f"filter questions {dict(filt)} (need >=3 total, >=1 test)")

    print(f"{'id':4} {'split':5} {'type':13} {'gold':6} {'filter':18} {'status':24} also_on")
    for r in rows:
        print(f"{r[0]:4} {r[1]:5} {r[2]:13} {str(r[3]):6} {str(r[4]):18} {r[5]:24} {r[6]}")
    print(f"\nquestions={len(questions)} split={dict(split)} filter={dict(filt)} "
          f"types={dict(Counter(q['type'] for q in questions))}")
    if errors:
        print("\nFAILED:\n  " + "\n  ".join(errors))
        return 1
    print("\nAll evidence verified on gold pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
