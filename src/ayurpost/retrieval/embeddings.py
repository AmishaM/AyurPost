"""
Embedding helpers for the hybrid RAG index — shared by build (index.py) and
search (search.py) so ingest and query go through the SAME code path.

Two vector spaces per chunk:
  dense   Voyage voyage-4-large (1024-dim, semantic, multilingual)
  sparse  fastembed BM25 (Qdrant/bm25) — exact-term, for rare Sanskrit drug/disease names

Two contract decisions live here:
  - Dense embeds the RAW chunk text (diacritics preserved): Voyage is multilingual and
    the accents carry Sanskrit drug-name signal. We never mutate stored text.
  - Sparse folds the text first via gazetteer._fold (NFKD strip + lowercase), applied
    SYMMETRICALLY at ingest and query, so accent-insensitive matching holds regardless
    of fastembed's internal tokenizer. This is the only place folding touches retrieval.

Voyage caps a request at 1000 texts AND 120K tokens for voyage-4-large; the token cap
binds for our corpus, so dense calls are packed into batches by the per-chunk n_tokens
(a deterministic CODE pass, not a guess — Rule 6).
"""

from __future__ import annotations

from functools import lru_cache

import voyageai
from fastembed import SparseTextEmbedding
from qdrant_client import models

from ayurpost import config
from ayurpost.gazetteer.tagger import _fold

# Named-vector keys — must match exactly across collection create / upsert / query.
DENSE = "dense"
SPARSE = "sparse"

BM25_MODEL = "Qdrant/bm25"

# voyage-4-large per-request limit is 120K tokens; n_tokens is a word-count proxy.
# Sanskrit text expands up to 7× in Voyage's BPE tokenizer, so use a conservative
# margin AND cap texts per batch to stay safely under the 120K limit.
VOYAGE_TOKEN_LIMIT = 120_000
VOYAGE_BATCH_MARGIN = 0.15
VOYAGE_MAX_TEXTS = 20      # hard cap per batch regardless of token count


@lru_cache(maxsize=1)
def _voyage() -> voyageai.Client:
    return voyageai.Client(api_key=config.VOYAGE_API_KEY)


@lru_cache(maxsize=1)
def _bm25() -> SparseTextEmbedding:
    """BM25 sparse model. Downloads a small ONNX artifact from HuggingFace on first
    use (bake into the GCP image later)."""
    return SparseTextEmbedding(model_name=BM25_MODEL)


def batch_by_tokens(n_tokens: list[int],
                    limit: int = VOYAGE_TOKEN_LIMIT,
                    margin: float = VOYAGE_BATCH_MARGIN,
                    max_texts: int = VOYAGE_MAX_TEXTS) -> list[list[int]]:
    """Greedily pack chunk indices into batches whose summed n_tokens stays under
    limit*margin AND whose text count stays under max_texts. Order is preserved."""
    budget = int(limit * margin)
    batches: list[list[int]] = []
    current: list[int] = []
    running = 0
    for i, tok in enumerate(n_tokens):
        flush = (running + tok > budget and current) or len(current) >= max_texts
        if flush:
            batches.append(current)
            current, running = [], 0
        current.append(i)
        running += tok
    if current:
        batches.append(current)
    return batches


def embed_dense_documents(texts: list[str], n_tokens: list[int]) -> list[list[float]]:
    """Voyage dense vectors for ingest (input_type='document'), token-batched.
    Embeds RAW text — diacritics preserved. Returns one 1024-float vector per text,
    in input order."""
    if len(texts) != len(n_tokens):
        raise ValueError(f"texts ({len(texts)}) and n_tokens ({len(n_tokens)}) differ")
    out: list[list[float]] = []
    for batch in batch_by_tokens(n_tokens):
        resp = _voyage().embed([texts[i] for i in batch],
                               model=config.EMBEDDING_MODEL, input_type="document")
        out.extend(resp.embeddings)
    if len(out) != len(texts):
        raise ValueError(f"got {len(out)} embeddings for {len(texts)} texts")
    for v in out:
        if len(v) != config.EMBEDDING_DIM:
            raise ValueError(f"embedding dim {len(v)} != {config.EMBEDDING_DIM}")
    return out


def embed_dense_query(text: str) -> list[float]:
    """Voyage dense vector for a search query (input_type='query'). RAW text."""
    return _voyage().embed([text], model=config.EMBEDDING_MODEL,
                           input_type="query").embeddings[0]


def to_sparse_vector(emb) -> models.SparseVector:
    """fastembed SparseEmbedding (numpy indices/values) -> Qdrant SparseVector."""
    return models.SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())


def embed_sparse_documents(texts: list[str]) -> list[models.SparseVector]:
    """BM25 sparse vectors for ingest, over _fold(text) (folding only the BM25 input,
    never the stored text). Returns one SparseVector per text, in input order."""
    embs = _bm25().embed([_fold(t) for t in texts])
    return [to_sparse_vector(e) for e in embs]


def embed_sparse_query(text: str) -> models.SparseVector:
    """BM25 sparse vector for a query, folded identically to ingest (symmetry)."""
    emb = next(iter(_bm25().query_embed(_fold(text))))
    return to_sparse_vector(emb)
