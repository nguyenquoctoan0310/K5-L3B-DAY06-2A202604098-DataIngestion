"""Aggregate audit labels -> results/audit_summary.json (re-run after reviewing labels).

Run from starter/:  python -m scripts.summarize_audit
"""
import csv
import json
from collections import Counter
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "results"


def rows(name: str) -> list[dict]:
    with (RES / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    coh, meta, gr = rows("audit_coherence.csv"), rows("audit_metadata.csv"), rows("audit_grounding.csv")
    missing = [r for r in coh + meta + gr if r["label"] == ""]
    if missing:
        raise SystemExit(f"{len(missing)} rows without label")
    out = {"label_status": dict(Counter(r["labeled_by"] for r in coh + meta + gr))}
    for s in ("A", "B", "C"):
        c = [int(r["label"]) for r in coh if s in r["applies_to"].split(",")]
        m = [r for r in meta if r["strategy"] == s]
        mf = [r for r in m if r["filter_applied"]]
        filt_q = {r["id"] for r in mf}
        filt_ok = sum(all(x["auto_filter_ok"].strip().lower() == "true" for x in mf if x["id"] == q) for q in filt_q)
        g = [int(r["label"]) for r in gr if r["strategy"] == s]
        dist = Counter(g)
        out[s] = {
            "coherence_mean": round(sum(c) / len(c), 2), "coherence_n": len(c),
            "coherence_dist": {k: Counter(c)[k] for k in (0, 1, 2)},
            "metadata_correct": f"{sum(int(r['label']) for r in m)}/{len(m)}",
            "filter_compliance_test_sample": f"{filt_ok}/{len(filt_q)}" if filt_q else "N/A",
            "grounding_dist": {k: dist[k] for k in (0, 1, 2)},
            "grounding_mean": round(sum(g) / len(g), 2), "grounding_n": len(g),
        }
    (RES / "audit_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
