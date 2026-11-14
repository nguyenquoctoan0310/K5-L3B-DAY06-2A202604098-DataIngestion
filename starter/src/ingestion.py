"""Read and normalize the OpenStax PDF corpus with PyMuPDF.

Why PyMuPDF instead of pypdf: pypdf returns one line per visual line with no paragraph
breaks, while PyMuPDF returns text *blocks* (≈ paragraphs) together with font size and
position. That lets us (1) keep paragraph boundaries for structure-aware chunking,
(2) recognise headings by font size and (3) drop running headers/footers by position.

Observed layout of this book (checked on the selected pages):
    size ≈ 22.4  chapter title          size ≈ 13.0  section heading "6.2 The Retina"
    size ≈ 10.8  sub-heading            size ≈  9.0  body text
    size ≈  7.5  figure caption, running header (y < 45) and footer (y > 745)
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf

from .models import DocumentPage

# ---------------------------------------------------------------------------
# Corpus definition: which physical PDF pages are counted as content.
# Excluded per chapter: Section Summary, Key Terms, References, Multiple Choice,
# Fill in the Blank (summary / lists / exercises, not explanatory content).
# ---------------------------------------------------------------------------
PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "Introduction-Behavioral-Neuroscience_-_WEB.pdf"
DOCUMENT_ID = "openstax-ibn"
SOURCE = "Introduction-Behavioral-Neuroscience_-_WEB.pdf"
CHAPTERS = [
    {"chapter": "6", "chapter_title": "Vision", "first_page": 251, "last_page": 294,
     "excluded": "295-302 (Section Summary, Key Terms, References, Multiple Choice, Fill in the Blank)"},
    {"chapter": "7", "chapter_title": "Hearing and Balance", "first_page": 303, "last_page": 338,
     "excluded": "339-346 (Section Summary, Key Terms, References, Multiple Choice, Fill in the Blank)"},
]

HEADER_MAX_Y = 45      # running header block ends above this
FOOTER_MIN_Y = 745     # footer block starts below this
SECTION_SIZE = 13.0
SUBHEADING_SIZE = 10.8
CHAPTER_TITLE_MIN_SIZE = 20.0

# Safety net in case a header/footer is not at the expected position.
BOILERPLATE_PATTERNS = [
    re.compile(r"^Access for free at openstax\.org$"),
    re.compile(r"^\d{1,4} \d{1,2} • .+$"),                    # "240 6 • Vision"
    re.compile(r"^\d{1,2}\.\d{1,2} • .+ \d{1,4}$"),           # "6.1 • An Overview ... 241"
    re.compile(r"^\d{1,2} • Section Summary\s+\d{1,4}$"),
]
# Link-only lines on chapter-opening pages; the URL is often wrapped onto the next line.
LINK_PATTERNS = [
    re.compile(r"Meet the author \(https?://[^)]*\)", re.S),
    re.compile(r"Access multimedia content \(https?://[^)]*\)", re.S),
]
SECTION_RE = re.compile(r"^(\d{1,2}\.\d{1,2}) (.+)$")
PRINTED_PAGE_RE = [re.compile(r"^(\d{1,4}) \d{1,2} • "), re.compile(r" (\d{1,4})$")]

_CHAR_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "„": '"',
                           "­": "", "​": "", "﻿": "", "\xa0": " "})


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace without discarding useful structure.

    - NFKC: unify compatibility characters (ligatures "ﬁ" -> "fi", micro sign -> mu).
    - Fold curly quotes to straight quotes; drop soft hyphens / zero-width chars.
    - Collapse runs of spaces inside a line, strip line ends.
    - Keep blank lines (paragraph breaks) but collapse 3+ newlines to 2.
    """
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    """Matching form used by benchmark verification AND hit evaluation:
    normalize_text + lowercase + every whitespace run (incl. paragraph breaks) -> one space."""
    return re.sub(r"\s+", " ", normalize_text(text)).strip().lower()


def _join_line(out: str, part: str) -> str:
    """A line ending in '-' followed by a lowercase letter is a compound word in this book
    (on-center, high-acuity): keep the hyphen and drop the space."""
    if out.endswith("-") and part[:1].islower():
        return out + part
    return f"{out} {part}" if out else part


def _block_text(block: dict[str, Any]) -> str:
    """Join spans (no separator) and lines (space)."""
    out = ""
    for line in block["lines"]:
        part = "".join(span["text"] for span in line["spans"]).strip()
        if part:
            out = _join_line(out, part)
    return out


def _size_class(size: float) -> str:
    if size >= CHAPTER_TITLE_MIN_SIZE:
        return "chapter"
    if abs(size - SECTION_SIZE) < 0.5:
        return "section"
    if abs(size - SUBHEADING_SIZE) < 0.5:
        return "sub"
    return "body"


def _block_segments(block: dict[str, Any]) -> list[tuple[str, str]]:
    """Split a block into consecutive (size_class, text) segments, classified PER LINE.

    PyMuPDF sometimes returns a heading and the paragraph after it as one block
    (e.g. p.331 "NEUROSCIENCE IN THE LAB" + body). Classifying the whole block by its
    largest font turned 1,300 chars of body text into a heading, so we classify lines.
    """
    segments: list[list[str]] = []
    for line in block["lines"]:
        spans = [s for s in line["spans"] if s["text"].strip()]
        part = "".join(s["text"] for s in line["spans"]).strip()
        if not spans or not part:
            continue
        cls = _size_class(max(s["size"] for s in spans))
        if segments and segments[-1][0] == cls:
            segments[-1][1] = _join_line(segments[-1][1], part)
        else:
            segments.append([cls, part])
    return [(c, t) for c, t in segments]


def _is_boilerplate(text: str) -> bool:
    return any(p.match(text) for p in BOILERPLATE_PATTERNS)


def _printed_page(edge_texts: list[str]) -> int | None:
    for text in edge_texts:
        for pattern in PRINTED_PAGE_RE:
            m = pattern.search(text)
            if m and _is_boilerplate(text):
                return int(m.group(1))
    return None


def load_pdf(path: Path, document_id: str, source: str, chapter: dict[str, Any],
             log: list[dict[str, Any]], current_section: str | None = None) -> list[DocumentPage]:
    """Extract each physical page of one chapter; keep page number, source and metadata.

    Page text format: paragraphs separated by a blank line; section headings prefixed
    "# ", sub-headings "## " so the structure-aware chunker can find them.
    metadata["section"] is the section active at the *start* of the page;
    metadata["sections_on_page"] lists every section that has text on the page.
    Pages with no extractable text are logged (OCR would be a separate step).
    """
    if not path.exists():
        raise FileNotFoundError(f"Corpus PDF not found: {path}")
    # Text before the first numbered section (chapter opener) belongs to "X.0 Introduction".
    current_section = current_section or f"{chapter['chapter']}.0 Introduction"
    pages: list[DocumentPage] = []
    with pymupdf.open(path) as doc:
        for pno in range(chapter["first_page"], chapter["last_page"] + 1):
            page = doc[pno - 1]
            blocks = [b for b in page.get_text("dict", sort=True)["blocks"] if b["type"] == 0]
            section_at_start = current_section
            sections_on_page: list[str] = []
            paragraphs: list[str] = []
            edge_texts: list[str] = []
            removed: list[str] = []
            for b in blocks:
                text = _block_text(b)
                if not text:
                    continue
                y0, y1 = b["bbox"][1], b["bbox"][3]
                if y1 < HEADER_MAX_Y or y0 > FOOTER_MIN_Y or _is_boilerplate(text):
                    edge_texts.append(text)
                    removed.append(text)
                    continue
                for cls, seg in _block_segments(b):
                    for pattern in LINK_PATTERNS:
                        seg, n = pattern.subn("", seg)
                        if n:
                            removed.append(pattern.pattern.split(" \\(")[0])
                    seg = seg.strip()
                    if not seg or re.fullmatch(r"CHAPTER \d+", seg):   # duplicate of chapter title
                        continue
                    if cls == "chapter":
                        title = re.sub(r"^CHAPTER \d+\s*", "", seg)
                        paragraphs.append(f"# Chapter {chapter['chapter']}: {title}")
                        continue
                    m = SECTION_RE.match(seg)
                    if cls == "section" and m:
                        current_section = f"{m.group(1)} {m.group(2)}"
                        paragraphs.append(f"# {current_section}")
                        continue
                    if cls == "sub":
                        paragraphs.append(f"## {seg}")
                        continue
                    if current_section and current_section not in sections_on_page:
                        sections_on_page.append(current_section)
                    paragraphs.append(seg)

            raw = "\n\n".join(paragraphs)
            text = normalize_text(raw)
            if not text:
                log.append({"level": "ERROR", "page": pno, "issue": "no extractable text (needs OCR?)"})
            elif len(text) < 300:
                log.append({"level": "WARN", "page": pno, "issue": f"short page: {len(text)} chars"})
            if removed:
                log.append({"level": "INFO", "page": pno, "issue": "removed boilerplate", "lines": removed})

            pages.append(DocumentPage(
                document_id=document_id, source=source, page=pno, text=text,
                metadata={
                    "chapter": chapter["chapter"],
                    "chapter_title": chapter["chapter_title"],
                    "section": section_at_start,
                    "sections_on_page": sections_on_page,
                    "printed_page": _printed_page(edge_texts),
                    "char_count": len(text),
                },
            ))
    return pages


def load_corpus(log: list[dict[str, Any]] | None = None) -> list[DocumentPage]:
    """Load every selected chapter in page order."""
    log = log if log is not None else []
    pages: list[DocumentPage] = []
    for chapter in CHAPTERS:
        pages.extend(load_pdf(PDF_PATH, DOCUMENT_ID, SOURCE, chapter, log))
    return pages
