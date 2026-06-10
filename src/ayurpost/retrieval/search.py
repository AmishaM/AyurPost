"""
Hybrid retriever over the Ayurvedic KB — Voyage dense + BM25 sparse, RRF-fused.

HybridRetriever is the importable entry point the content-generation pipeline will
reuse; running this module is just a smoke test over a few canned clinic-themed queries.

Each query is embedded into the same two spaces as the documents (dense via Voyage
input_type='query', sparse via BM25 over _fold). Qdrant runs one prefetch per vector
and fuses the two ranked lists with Reciprocal Rank Fusion. The doshas_mentioned HARD
filter is applied at the top level, so it constrains the FUSED result set — every
returned point is guaranteed to carry the requested dosha.

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.retrieval.search
"""

from __future__ import annotations

import sys

from qdrant_client import QdrantClient, models

from ayurpost import config
from ayurpost.retrieval import embeddings as emb


class HybridRetriever:
    def __init__(self, collection: str = config.QDRANT_COLLECTION,
                 client: QdrantClient | None = None) -> None:
        # collection defaults to the ALIAS, so re-index swaps are transparent here.
        self.collection = collection
        self.client = client or QdrantClient(url=config.QDRANT_URL,
                                             api_key=config.QDRANT_API_KEY)

    def search(self, query: str, *, limit: int = 5, doshas: list[str] | None = None,
               prefetch_limit: int = 20) -> list[models.ScoredPoint]:
        dense = emb.embed_dense_query(query)
        sparse = emb.embed_sparse_query(query)

        query_filter = None
        if doshas:
            # MatchAny on the list field = "doshas_mentioned contains ANY of these".
            query_filter = models.Filter(must=[models.FieldCondition(
                key="doshas_mentioned", match=models.MatchAny(any=doshas))])

        return self.client.query_points(
            collection_name=self.collection,
            prefetch=[
                models.Prefetch(query=dense, using=emb.DENSE, limit=prefetch_limit),
                models.Prefetch(query=sparse, using=emb.SPARSE, limit=prefetch_limit),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        ).points


# (query, dosha hard-filter) — grounded in the clinic's actual themes (roadmap).
# #3 is a rare exact Sanskrit drug (bhallataka, in 7 chunks) to showcase BM25 sparse.
_SMOKE_QUERIES: list[tuple[str, list[str] | None]] = [
    ("Virechana purgation therapy for excess Pitta", ["pitta"]),
    ("Vamana therapeutic emesis procedure and indications", None),
    ("bhallataka", None),
    ("winter Kapha dry skin and joint stiffness oil massage Abhyanga", ["kapha"]),
    ("Shirodhara oil pouring treatment for the head", None),
]


def main() -> int:
    retriever = HybridRetriever()
    for query, doshas in _SMOKE_QUERIES:
        flt = f"  [filter doshas={doshas}]" if doshas else ""
        print(f"\n=== {query}{flt}")
        try:
            hits = retriever.search(query, limit=5, doshas=doshas)
        except Exception as e:  # fail loud — a broken query shouldn't look like 0 hits
            print(f"  ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not hits:
            print("  (no hits)")
        for h in hits:
            p = h.payload
            snippet = " ".join(p["text"].split())[:120]
            print(f"  {h.score:.4f}  ch{p['chapter']:>2} {p['chunk_id']}  "
                  f"doshas={p['doshas_mentioned']}")
            print(f"          {snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
