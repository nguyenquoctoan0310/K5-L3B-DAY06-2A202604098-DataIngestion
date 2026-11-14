"""Store and search vectors with PostgreSQL + pgvector.

Design
- One table `chunks`, keyed by (index_name, chunk_id). Index "A" holds fixed-size chunks,
  index "B" holds paragraph chunks; strategy C searches index "B" with a filter.
- Filterable metadata are real columns (chapter, section) so filters are plain SQL
  predicates; the full metadata is also kept as JSONB for traceability.
- Search is EXACT: ORDER BY cosine distance (<=>) with no ANN index (a few hundred rows,
  so a sequential scan is fast and results are not approximate).
- Re-ingest: upsert by stable chunk_id; rows whose content_hash and model are unchanged
  are left alone, changed rows are updated, and chunks that no longer exist for the
  document are deleted -> no duplicates, no stale chunks.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Chunk

FILTER_COLUMNS = {"chapter": "chapter", "section": "section"}   # whitelist: key -> column


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be > 0")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)

    def create_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    index_name      TEXT    NOT NULL,
                    chunk_id        TEXT    NOT NULL,
                    document_id     TEXT    NOT NULL,
                    source          TEXT    NOT NULL,
                    page            INTEGER NOT NULL,
                    chapter         TEXT    NOT NULL,
                    section         TEXT,
                    text            TEXT    NOT NULL,
                    metadata        JSONB   NOT NULL,
                    content_hash    TEXT    NOT NULL,
                    embedding_model TEXT    NOT NULL,
                    embedding       vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (index_name, chunk_id)
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_filter_idx ON chunks (index_name, chapter, section)")
            # Fail loudly if an existing table was created with another dimension.
            dim = conn.execute("""SELECT atttypmod FROM pg_attribute
                                  WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'""").fetchone()[0]
            if dim != self.dimensions:
                raise RuntimeError(f"table chunks has vector({dim}) but model has {self.dimensions} dims")

    def existing(self, index_name: str) -> dict[str, tuple[str, str]]:
        """chunk_id -> (content_hash, embedding_model); lets the caller skip re-embedding."""
        with self._connect() as conn:
            rows = conn.execute("SELECT chunk_id, content_hash, embedding_model FROM chunks WHERE index_name = %s",
                                (index_name,)).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def upsert(self, index_name: str, chunks: list[Chunk], vectors: list[list[float]],
               embedding_model: str, prune_document_ids: set[str] | None = None,
               keep_ids: set[str] | None = None) -> dict[str, int]:
        """Insert/update chunks with stable IDs; delete obsolete chunks of the given documents.

        `vectors[i]` belongs to `chunks[i]`; `chunks` may contain only new/changed chunks.
        `keep_ids` = ALL current chunk IDs of the re-chunked documents (defaults to the IDs in
        `chunks`); rows of `prune_document_ids` not in keep_ids are deleted as obsolete.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        bad = [c.chunk_id for c, v in zip(chunks, vectors) if len(v) != self.dimensions]
        if bad:
            raise ValueError(f"wrong vector dimension for {bad[:3]}")
        if len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("duplicate chunk_id in batch")
        before = self.existing(index_name)
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        with self._connect() as conn, conn.transaction():
            for c, v in zip(chunks, vectors):
                old = before.get(c.chunk_id)
                h = c.metadata["content_hash"]
                if old == (h, embedding_model):
                    stats["unchanged"] += 1
                    continue
                conn.execute("""
                    INSERT INTO chunks (index_name, chunk_id, document_id, source, page, chapter, section,
                                        text, metadata, content_hash, embedding_model, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (index_name, chunk_id) DO UPDATE SET
                        document_id = EXCLUDED.document_id, source = EXCLUDED.source, page = EXCLUDED.page,
                        chapter = EXCLUDED.chapter, section = EXCLUDED.section, text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata, content_hash = EXCLUDED.content_hash,
                        embedding_model = EXCLUDED.embedding_model, embedding = EXCLUDED.embedding""",
                    (index_name, c.chunk_id, c.document_id, c.source, c.page, c.metadata["chapter"],
                     c.metadata.get("section"), c.text, Jsonb(c.metadata), h, embedding_model, _vec(v)))
                stats["updated" if old else "inserted"] += 1
            if prune_document_ids:
                keep = sorted(keep_ids if keep_ids is not None else {c.chunk_id for c in chunks})
                cur = conn.execute("""DELETE FROM chunks WHERE index_name = %s AND document_id = ANY(%s)
                                      AND NOT (chunk_id = ANY(%s))""",
                                   (index_name, list(prune_document_ids), keep))
                stats["deleted"] = cur.rowcount
        return stats

    def count(self, index_name: str) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT count(*) FROM chunks WHERE index_name = %s", (index_name,)).fetchone()[0]

    def search(self, index_name: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Filter in SQL, rank by cosine distance, return top-k with score = 1 - distance."""
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector has {len(query_vector)} dims, expected {self.dimensions}")
        where, params = ["index_name = %s"], [index_name]
        for key, value in (filters or {}).items():
            if key not in FILTER_COLUMNS:
                raise ValueError(f"filter on '{key}' not allowed; allowed: {sorted(FILTER_COLUMNS)}")
            where.append(f"{FILTER_COLUMNS[key]} = %s")        # column name from whitelist only
            params.append(str(value))
        qv = _vec(query_vector)
        sql = f"""SELECT chunk_id, document_id, source, page, chapter, section, text, metadata,
                         1 - (embedding <=> %s::vector) AS score
                  FROM chunks WHERE {' AND '.join(where)}
                  ORDER BY embedding <=> %s::vector, chunk_id
                  LIMIT %s"""
        with self._connect() as conn:
            rows = conn.execute(sql, [qv, *params, qv, top_k]).fetchall()
        cols = ["chunk_id", "document_id", "source", "page", "chapter", "section", "text", "metadata", "score"]
        return [dict(zip(cols, r)) for r in rows]
