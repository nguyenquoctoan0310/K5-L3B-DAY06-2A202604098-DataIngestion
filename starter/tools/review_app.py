"""Local web UI to review audit labels against the source PDF and to try retrieval.

Run from starter/:   python -m tools.review_app        then open http://127.0.0.1:8765
Local only (binds 127.0.0.1). Reads/writes results/audit_*.csv and results/review_log.json.

- Coherence / grounding: BLIND review. The AI label is hidden until you submit your own;
  if yours differs, a reason is required. The original AI label is kept in `ai_label`.
- Metadata: check each row against the rendered PDF page (chunk text highlighted).
- Search: query index A/B (C = B + chapter filter); needs the Docker database running.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymupdf

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))
from scripts.apply_draft_labels import COHERENCE, GROUNDING  # noqa: E402  (original AI reasons)
from src.config import STRATEGIES  # noqa: E402
from src.ingestion import PDF_PATH, normalize_for_match  # noqa: E402

RES = STARTER / "results"
UI = Path(__file__).with_name("review_ui.html")
BENCHMARK = STARTER.parent / "benchmark" / "questions.jsonl"
SHEETS = {
    "coherence": ("audit_coherence.csv", ["position_id", "index"]),
    "grounding": ("audit_grounding.csv", ["strategy", "id"]),
    "metadata": ("audit_metadata.csv", ["strategy", "id", "rank"]),
}
UNREVIEWED = ("AI draft", "LLM judge")
LOCK = threading.Lock()
_doc = pymupdf.open(PDF_PATH)
_png_cache: dict[int, bytes] = {}
_search = {}


# ---------------------------------------------------------------- CSV helpers
def _load(sheet: str) -> list[dict]:
    with (RES / SHEETS[sheet][0]).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:                                   # keep the original AI label/reason
        if r.get("ai_label"):
            continue
        key = tuple(r[k] for k in SHEETS[sheet][1])
        original = (COHERENCE if sheet == "coherence" else GROUNDING if sheet == "grounding" else {}).get(key)
        m = re.search(r"changed AI draft (\d)->", r["labeled_by"])
        r["ai_label"] = m.group(1) if m else (str(original[0]) if original else r["label"])
        r["ai_reason"] = original[1] if original else (r["reason"] if r["labeled_by"].startswith(UNREVIEWED) else "")
    return rows


def _save(sheet: str, rows: list[dict]) -> None:
    path = RES / SHEETS[sheet][0]
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _reviewed(r: dict) -> bool:
    return bool(r["labeled_by"]) and not r["labeled_by"].startswith(UNREVIEWED)


def _rebuild_log() -> dict:
    log: dict = {}
    for sheet in ("coherence", "grounding"):
        rows = [r for r in _load(sheet) if _reviewed(r)]
        log[sheet] = {"reviewed": len(rows), "agree": sum(r["label"] == r["ai_label"] for r in rows),
                      "changed": [{"key": {k: r[k] for k in SHEETS[sheet][1]}, "ai": r["ai_label"],
                                   "author": r["label"]} for r in rows if r["label"] != r["ai_label"]]}
    meta = [r for r in _load("metadata") if _reviewed(r)]
    log["metadata"] = {"reviewed": len(meta), "correct": sum(r["label"] == "1" for r in meta),
                       "errors": [f"{r['strategy']}/{r['id']}/rank{r['rank']}" for r in meta if r["label"] != "1"]}
    (RES / "review_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return log


_extra: dict = {}


def _context() -> dict:
    """Chunk texts of test traces (for metadata rows) and benchmark evidence (for grounding)."""
    if not _extra:
        _extra["evidence"] = {q["id"]: q for q in map(json.loads, BENCHMARK.read_text(encoding="utf-8").splitlines())}
        texts = {}
        for s in STRATEGIES:
            for line in (RES / f"traces_{s}_test.jsonl").read_text(encoding="utf-8").splitlines():
                t = json.loads(line)
                for h in t["result"]["top"]:
                    texts[(s, t["id"], str(h["rank"]))] = h["text"]
        _extra["texts"] = texts
    return _extra


def _public(sheet: str, r: dict) -> dict:
    """Row as sent to the browser: AI label/reason hidden until the row is reviewed (blind)."""
    out = dict(r)
    out["reviewed"] = _reviewed(r)
    ctx = _context()
    if sheet == "metadata":
        out["chunk_text"] = ctx["texts"].get((r["strategy"], r["id"], r["rank"]), "")
    if sheet == "grounding":
        out["evidence_quote"] = ctx["evidence"][r["id"]]["evidence_quote"]
    if sheet != "metadata" and not out["reviewed"]:
        for k in ("label", "reason", "ai_label", "ai_reason", "auto_evidence_hit_at_3"):
            out.pop(k, None)
    return out


# ---------------------------------------------------------------- PDF helpers
def _page_png(page: int) -> bytes:
    if page not in _png_cache:
        _png_cache[page] = _doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).tobytes("png")
    return _png_cache[page]


def _highlight(page: int, text: str) -> dict:
    """Locate `text` on the PDF page with sliding 6-word phrases (robust to line breaks)."""
    pg = _doc[page - 1]
    clean = re.sub(r"^#+ ", "", text, flags=re.M).replace('"', " ").replace("'", " ")
    words = [w for w in clean.split() if w]
    rects, i, found, W = [], 0, 0, 6
    while i < len(words):
        phrase = " ".join(words[i:i + W])
        hits = pg.search_for(phrase) if len(phrase) > 8 else []
        if hits:
            rects += hits
            found += min(W, len(words) - i)
            i += W
        else:
            i += 1
    w, h = pg.rect.width, pg.rect.height
    return {"width": w, "height": h, "coverage": round(found / max(1, len(words)), 2),
            "rects": [[r.x0 / w, r.y0 / h, (r.x1 - r.x0) / w, (r.y1 - r.y0) / h] for r in rects]}


# ---------------------------------------------------------------- search
def _test_overlap(q: str) -> str | None:
    qs = set(normalize_for_match(q).split())
    for line in BENCHMARK.read_text(encoding="utf-8").splitlines():
        b = json.loads(line)
        if b["split"] != "test":
            continue
        bs = set(normalize_for_match(b["question"]).split())
        if qs and len(qs & bs) / len(qs | bs) >= 0.5:
            return b["id"]
    return None


def _do_search(q: str, strategy: str, chapter: str, k: int) -> dict:
    if "store" not in _search:
        from dotenv import load_dotenv
        from src.embeddings import MyEmbedder
        from src.store import PgVectorStore
        load_dotenv(STARTER / ".env")
        emb = MyEmbedder()
        _search.update(emb=emb, store=PgVectorStore(os.environ["DATABASE_URL"], emb.dimensions))
    cfg = STRATEGIES[strategy]
    filt = {"chapter": chapter} if (cfg["use_filter"] and chapter) else None
    hits = _search["store"].search(cfg["index"], _search["emb"].embed_query(q), k, filt)
    return {"filter": filt, "index": cfg["index"], "test_warning": _test_overlap(q),
            "hits": [{k2: (round(v, 4) if k2 == "score" else v) for k2, v in h.items() if k2 != "metadata"}
                     | {"printed_page": (h.get("metadata") or {}).get("printed_page")} for h in hits]}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):            # quiet console
        pass

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/rows":
                sheet = q["sheet"]
                with LOCK:
                    return self._send(200, [_public(sheet, r) for r in _load(sheet)])
            if u.path == "/api/page.png":
                return self._send(200, _page_png(int(q["page"])), "image/png")
            if u.path == "/api/log":
                with LOCK:
                    return self._send(200, _rebuild_log())
            if u.path == "/api/search":
                return self._send(200, _do_search(q["q"], q.get("strategy", "B"), q.get("chapter", ""),
                                                  int(q.get("k", 3))))
            return self._send(404, {"error": "not found"})
        except Exception as exc:                  # surface errors to the UI, never fake data
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/highlight":
                return self._send(200, _highlight(int(body["page"]), body["text"]))
            if self.path == "/api/label":
                return self._send(*self._label(body))
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    @staticmethod
    def _label(b: dict):
        sheet, key = b["sheet"], b["key"]
        label, reason, reviewer = str(b["label"]), b.get("reason", "").strip(), b.get("reviewer", "").strip()
        if not reviewer:
            return 400, {"error": "Nhập tên người chấm trước."}
        if label not in ({"0", "1"} if sheet == "metadata" else {"0", "1", "2"}):
            return 400, {"error": "Nhãn không hợp lệ."}
        with LOCK:
            rows = _load(sheet)
            r = next(x for x in rows if [x[k] for k in SHEETS[sheet][1]] == [str(key[k]) for k in SHEETS[sheet][1]])
            ai = r["ai_label"]
            if sheet == "metadata":
                if label == "0" and not reason:
                    return 200, {"need_reason": True, "message": "Ghi rõ metadata sai ở đâu."}
                r["label"], r["reason"] = label, reason or r["reason"]
                r["labeled_by"] = f"{reviewer} (web review, checked against rendered PDF page)"
            else:
                if label != ai and not reason:
                    return 200, {"need_reason": True, "ai_label": ai, "ai_reason": r["ai_reason"],
                                 "message": f"Nhãn của bạn ({label}) khác AI ({ai}). Ghi lý do rồi lưu lại."}
                r["label"] = label
                r["reason"] = reason or r["ai_reason"] or r["reason"]
                r["labeled_by"] = (f"{reviewer} (blind review, agrees with AI draft)" if label == ai else
                                   f"{reviewer} (blind review, changed AI draft {ai}->{label})")
            _save(sheet, rows)
            _rebuild_log()
            return 200, {"ok": True, "row": _public(sheet, r)}


def main():
    host, port = "127.0.0.1", int(os.environ.get("REVIEW_PORT", "8765"))
    print(f"Review UI: http://{host}:{port}   (Ctrl+C to stop)")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
