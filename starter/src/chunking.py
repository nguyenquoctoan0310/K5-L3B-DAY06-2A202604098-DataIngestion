"""Fixed-size (strategy A) and structure-aware (strategy B) chunking.

Both strategies:
- chunk within one page, so every chunk has an exact `page` (limitation: evidence that
  continues onto the next page is split; this is logged by the stats script);
- keep the same metadata fields: chapter, chapter_title, section (section active at the
  chunk's start offset), printed_page, char_start/char_end (offsets in the page text) and
  content_hash (detects changed text on re-ingest);
- produce stable IDs "<document_id>:p<page>:<kind><index>" so re-ingesting the same
  corpus version overwrites instead of duplicating.
Strategy C reuses B's chunks and only adds a metadata filter at query time.
"""
from __future__ import annotations

import bisect
import hashlib
import re

from .models import Chunk, DocumentPage

SECTION_HEADING_RE = re.compile(r"^# (\d{1,2}\.\d{1,2} .+)$", re.M)
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


class _SectionLocator:
    """Maps a character offset in the page text to the section active at that point."""

    def __init__(self, page: DocumentPage) -> None:
        self.offsets = [0]
        self.sections = [page.metadata["section"]]
        for m in SECTION_HEADING_RE.finditer(page.text):
            self.offsets.append(m.start())
            self.sections.append(m.group(1))

    def at(self, offset: int) -> str:
        return self.sections[bisect.bisect_right(self.offsets, offset) - 1]


def _make_chunk(page: DocumentPage, kind: str, index: int, text: str, start: int, end: int,
                locator: _SectionLocator) -> Chunk:
    meta = {
        "chapter": page.metadata["chapter"],
        "chapter_title": page.metadata["chapter_title"],
        "section": locator.at(start),
        "printed_page": page.metadata.get("printed_page"),
        "char_start": start,
        "char_end": end,
        "content_hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
    }
    return Chunk(chunk_id=f"{page.document_id}:p{page.page}:{kind}{index:02d}",
                 document_id=page.document_id, source=page.source, page=page.page,
                 text=text, metadata=meta)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Strategy A: overlapping character windows over the page text.

    Deliberately ignores structure (may cut mid-word / mid-sentence): this is the
    baseline. The last window is dropped if it lies entirely inside the previous one.
    """
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError(f"need size > 0 and 0 <= overlap < size, got {size=}, {overlap=}")
    text, step = page.text, size - overlap
    locator = _SectionLocator(page)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end]
        if piece.strip():
            chunks.append(_make_chunk(page, "f", len(chunks), piece, start, end, locator))
        if end == len(text):
            break
        start += step
    return chunks


def _split_long(text: str, start: int, max_size: int) -> list[tuple[str, int, int]]:
    """Split one oversized paragraph at sentence boundaries; hard-cut a single sentence
    longer than max_size (rare). Returns (text, start, end) with page offsets."""
    pieces: list[tuple[str, int, int]] = []
    bounds = [0] + [m.end() for m in SENTENCE_END_RE.finditer(text)] + [len(text)]
    cur_s = 0
    for i in range(1, len(bounds)):
        if bounds[i] - cur_s > max_size and bounds[i - 1] > cur_s:
            pieces.append((text[cur_s:bounds[i - 1]].strip(), start + cur_s, start + bounds[i - 1]))
            cur_s = bounds[i - 1]
    while len(text) - cur_s > max_size:                       # single giant sentence
        pieces.append((text[cur_s:cur_s + max_size], start + cur_s, start + cur_s + max_size))
        cur_s += max_size
    pieces.append((text[cur_s:].strip(), start + cur_s, start + len(text)))
    return [p for p in pieces if p[0]]


def by_paragraph(page: DocumentPage, max_size: int, min_size: int = 0) -> list[Chunk]:
    """Strategy B: group whole paragraphs up to max_size characters of body text.

    - A section heading ("# 6.2 ...") or sub-heading ("## ...") always starts a new chunk,
      and the heading stays attached to the text that follows it (so a chunk can be
      max_size + heading length, at most ~100 extra chars in this corpus).
    - Paragraphs longer than max_size are split at sentence boundaries.
    - A heading left alone at the bottom of a page is merged into the previous chunk.
    - A chunk shorter than min_size that does not start with a heading (short caption,
      one-line paragraph) is merged into the previous chunk on the same page, because on
      its own it has no context ("Everything seemed backward!").
    """
    if max_size <= 0:
        raise ValueError("max_size must be > 0")
    locator = _SectionLocator(page)
    units: list[tuple[str, int, int, bool]] = []          # text, start, end, is_heading
    for m in re.finditer(r"[^\n](?:.|\n(?!\n))*", page.text):
        para, s, e = m.group(0).strip(), m.start(), m.end()
        is_heading = para.startswith("#")
        if not is_heading and len(para) > max_size:
            units.extend((t, ps, pe, False) for t, ps, pe in _split_long(para, s, max_size))
        else:
            units.append((para, s, e, is_heading))

    groups: list[list[tuple[str, int, int, bool]]] = []
    current: list[tuple[str, int, int, bool]] = []

    def has_body(group: list) -> bool:
        return any(not u[3] for u in group)

    for unit in units:
        text, _, _, is_heading = unit
        size_if_added = sum(len(u[0]) + 2 for u in current) + len(text)
        if current and ((is_heading and has_body(current)) or
                        (not is_heading and has_body(current) and size_if_added > max_size)):
            groups.append(current)
            current = []
        current.append(unit)
    if current:
        if not has_body(current) and groups:
            groups[-1].extend(current)                   # dangling heading at page bottom
        else:
            groups.append(current)

    merged: list[list[tuple[str, int, int, bool]]] = []
    for group in groups:
        length = sum(len(u[0]) + 2 for u in group)
        if merged and length < min_size and not group[0][3]:
            merged[-1].extend(group)
        else:
            merged.append(group)
    if len(merged) > 1 and sum(len(u[0]) for u in merged[0]) < min_size and not merged[0][0][3]:
        merged[1] = merged[0] + merged[1]              # tiny first chunk: merge forward
        merged.pop(0)
    groups = merged

    chunks: list[Chunk] = []
    for group in groups:
        text = "\n\n".join(u[0] for u in group)
        chunks.append(_make_chunk(page, "p", len(chunks), text, group[0][1], group[-1][2], locator))
    return chunks


def build_chunks(pages: list[DocumentPage], strategy: str, params: dict) -> list[Chunk]:
    """Apply one strategy to all pages."""
    out: list[Chunk] = []
    for page in pages:
        if strategy == "fixed":
            out.extend(fixed_size(page, params["size"], params["overlap"]))
        elif strategy == "paragraph":
            out.extend(by_paragraph(page, params["max_size"], params.get("min_size", 0)))
        else:
            raise ValueError(f"unknown chunking strategy {strategy}")
    return out
