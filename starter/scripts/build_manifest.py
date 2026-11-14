"""Build data/manifest.json and results/ingest_log.json from the actual PDF.

Run from starter/:  python -m scripts.build_manifest
All counts are computed from the extracted, normalized text; nothing is typed by hand.
"""
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingestion import CHAPTERS, DOCUMENT_ID, PDF_PATH, SOURCE, load_corpus  # noqa: E402

STARTER = Path(__file__).resolve().parents[1]
REPO = STARTER.parent
ACCESSED_AT = "2026-11-14"   # date the PDF was downloaded from openstax.org


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    log: list[dict] = []
    pages = load_corpus(log)
    with pymupdf.open(PDF_PATH) as doc:
        physical_pages, pdf_meta = doc.page_count, doc.metadata

    chapters = []
    for ch in CHAPTERS:
        ch_pages = [p for p in pages if p.metadata["chapter"] == ch["chapter"]]
        chapters.append({
            "chapter": ch["chapter"],
            "chapter_title": ch["chapter_title"],
            "pdf_page_range": f"{ch['first_page']}-{ch['last_page']}",
            "pages_counted": len(ch_pages),
            "excluded_pages": ch["excluded"],
            "char_count": sum(p.metadata["char_count"] for p in ch_pages),
        })

    manifest = {
        "corpus_version": "v1",
        "sources": [{
            "source_id": DOCUMENT_ID,
            "title": "Introduction to Behavioral Neuroscience (OpenStax)",
            "publisher": "OpenStax, Rice University",
            "source_path_or_url": "https://openstax.org/books/introduction-behavioral-neuroscience",
            "local_file": f"starter/data/{SOURCE}",
            "license_or_permission": "CC BY-NC-SA 4.0 (stated on PDF page 4: '©2024 Rice University. "
                                     "Textbook content produced by OpenStax is licensed under a Creative Commons "
                                     "Attribution Non-Commercial ShareAlike 4.0 International License')",
            "usage_note": "Non-commercial educational use with attribution; PDF not committed to the repo, "
                          "download it from the URL above.",
            "accessed_at": ACCESSED_AT,
            "snapshot": {"pdf_creation_date": pdf_meta.get("creationDate"),
                         "physical_pages_in_pdf": physical_pages,
                         "sha256": sha256(PDF_PATH)},
            "page_counting_rule": "A page is counted if it is a physical PDF page inside a chapter's explanatory "
                                  "text (chapter opener + numbered sections). Excluded: front matter, Section "
                                  "Summary, Key Terms, References, Multiple Choice, Fill in the Blank. `page` = "
                                  "1-based physical PDF page; `printed_page` = number printed in header/footer.",
            "chapters": chapters,
            "total_pages_counted": len(pages),
            "total_char_count": sum(p.metadata["char_count"] for p in pages),
            "pages": [{"page_id": f"{DOCUMENT_ID}:p{p.page}", "page": p.page,
                       "printed_page": p.metadata["printed_page"], "chapter": p.metadata["chapter"],
                       "section": p.metadata["section"], "sections_on_page": p.metadata["sections_on_page"],
                       "char_count": p.metadata["char_count"]} for p in pages],
        }],
        "normalization": "PyMuPDF text blocks; NFKC; curly quotes folded; soft hyphen/zero-width removed; "
                         "running header/footer and author/multimedia link lines removed; line-end "
                         "hyphen kept for compound words (all 15 cases checked are compounds).",
    }
    out = REPO / "data" / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    levels = Counter(e["level"] for e in log)
    results = STARTER / "results"
    results.mkdir(exist_ok=True)
    (results / "ingest_log.json").write_text(
        json.dumps({"generated": str(date.today()), "summary": dict(levels), "entries": log},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pages counted: {len(pages)} | chars: {manifest['sources'][0]['total_char_count']}")
    for c in chapters:
        print(f"  ch{c['chapter']} {c['chapter_title']}: {c['pages_counted']} pages, {c['char_count']} chars")
    print("log:", dict(levels), "->", results / "ingest_log.json")
    print("manifest ->", out)


if __name__ == "__main__":
    main()
