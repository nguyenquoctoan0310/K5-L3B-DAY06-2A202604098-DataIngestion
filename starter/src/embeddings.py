"""Real embedding provider: local intfloat/multilingual-e5-base via sentence-transformers."""
import os
from typing import Protocol

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "intfloat/multilingual-e5-base"
# Pinned Hugging Face commit so every run (and the grader) uses identical weights.
DEFAULT_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Local E5 adapter.

    - E5 was trained with prefixes: "passage: " for documents, "query: " for questions.
      Omitting them noticeably degrades retrieval, so only E5 models are accepted.
    - Vectors are L2-normalized, so cosine similarity == dot product (pgvector <=>).
    - Input longer than max_seq_length (512 tokens) is silently cut by the model; this
      class counts such texts in `self.truncated` so the report can state it.
      Measured on this corpus: ~4 chars/token, 2000 chars ≈ 500 tokens.
    - If the model cannot load, it raises. It never falls back to mock vectors.
    """

    def __init__(self, model_name: str | None = None, revision: str | None = None,
                 batch_size: int = 16, device: str | None = None) -> None:
        self.model_name = model_name or os.environ.get("EMBEDDING_MODEL") or DEFAULT_MODEL
        if "e5" not in self.model_name.lower():
            raise ValueError(f"{self.model_name}: this adapter adds E5 'query:'/'passage:' prefixes; "
                             "write a separate adapter for other models")
        self.revision = revision or (DEFAULT_REVISION if self.model_name == DEFAULT_MODEL else None)
        self.batch_size = batch_size
        self.model = SentenceTransformer(self.model_name, revision=self.revision, device=device)
        self.dimensions = self.model.get_embedding_dimension()
        self.max_seq_length = self.model.max_seq_length
        self.device = str(self.model.device)
        self.truncated = 0          # number of document texts longer than max_seq_length

    def _count_truncated(self, texts: list[str]) -> int:
        lengths = [len(ids) for ids in self.model.tokenizer(texts)["input_ids"]]
        return sum(n > self.max_seq_length for n in lengths)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        inputs = [f"passage: {t}" for t in texts]
        self.truncated += self._count_truncated(inputs)
        vecs = self.model.encode(inputs, batch_size=self.batch_size,
                                 normalize_embeddings=True, show_progress_bar=len(texts) > 50)
        if len(vecs) != len(texts) or vecs.shape[1] != self.dimensions:
            raise RuntimeError(f"Expected {len(texts)}x{self.dimensions}, got {vecs.shape}")
        return vecs.tolist()

    def embed_query(self, question: str) -> list[float]:
        if not question.strip():
            raise ValueError("empty query")
        return self.model.encode(f"query: {question}", normalize_embeddings=True).tolist()

    def info(self) -> dict:
        """Settings to record in results/ and the report."""
        return {"provider": "local sentence-transformers", "model": self.model_name,
                "revision": self.revision, "dimensions": self.dimensions,
                "max_seq_length": self.max_seq_length, "device": self.device,
                "query_prefix": "query: ", "document_prefix": "passage: ",
                "normalized": True, "truncated_documents": self.truncated, "cost_usd": 0}