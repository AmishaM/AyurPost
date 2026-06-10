"""
Build the hybrid RAG index in Qdrant from the tagged chunks.

Reads data/chunks/all_chunks.jsonl (305 vol1 chunks), embeds each into a dense
(Voyage) and a sparse (BM25) vector, and upserts them as points into a versioned
Qdrant collection, then atomically points the stable alias at it.

Collection scheme (safe re-index swaps):
    physical  ayurvedic_kb_v1   <- the real collection (named by version)
    alias     ayurvedic_kb      <- what search.py queries (= config.QDRANT_COLLECTION)
To re-index, bump COLLECTION_VERSION, rebuild, swap the alias; the old collection
stays as an instant rollback.

Point ids are a UUID5 of chunk_id (Qdrant ids must be uint/UUID, and UUID5 is stable
so re-runs overwrite the same point — no duplicates). chunk_id is also kept in payload.
The whole chunk record (text + all tags) is stored as the payload; doshas_mentioned
gets a keyword index because it is the one HARD retrieval filter.

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.retrieval.index --dry-run
    PYTHONPATH=src .venv/bin/python -m ayurpost.retrieval.index
    PYTHONPATH=src .venv/bin/python -m ayurpost.retrieval.index --force

Output:
    Qdrant collection ayurvedic_kb_v<version> (305 points) + alias ayurvedic_kb.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from qdrant_client import QdrantClient, models

from ayurpost import config
from ayurpost.retrieval import embeddings as emb

COLLECTION_VERSION = "v3"
PHYSICAL_NAME = f"{config.QDRANT_COLLECTION}_{COLLECTION_VERSION}"  # ayurvedic_kb_v1
ALIAS_NAME = config.QDRANT_COLLECTION                              # ayurvedic_kb

# payload fields to index: keyword for filters (doshas_mentioned is the HARD one),
# integer for chapter.
INDEXED_KEYWORD_FIELDS = ["doshas_mentioned", "herbs", "diseases", "source", "section_role"]
INDEXED_INTEGER_FIELDS = ["chapter"]

_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ayurpost/chunk")


def load_chunks() -> list[dict]:
    path = config.CHUNKS_DIR / "all_chunks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"chunks file not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def point_id(chunk_id: str) -> str:
    """Stable UUID5 of chunk_id — deterministic, so re-runs overwrite, never duplicate."""
    return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def _client() -> QdrantClient:
    return QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)


def ensure_collection(client: QdrantClient, *, force: bool) -> None:
    """Create PHYSICAL_NAME with named dense+sparse vectors and payload indexes.
    Idempotent: leaves an existing collection in place unless force=True."""
    if client.collection_exists(PHYSICAL_NAME):
        if not force:
            return
        client.delete_collection(PHYSICAL_NAME)

    client.create_collection(
        collection_name=PHYSICAL_NAME,
        vectors_config={
            emb.DENSE: models.VectorParams(size=config.EMBEDDING_DIM,
                                           distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            emb.SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
    )
    for field in INDEXED_KEYWORD_FIELDS:
        client.create_payload_index(PHYSICAL_NAME, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
    for field in INDEXED_INTEGER_FIELDS:
        client.create_payload_index(PHYSICAL_NAME, field_name=field,
                                    field_schema=models.PayloadSchemaType.INTEGER)


def build_points(records: list[dict], dense: list[list[float]],
                 sparse: list[models.SparseVector]) -> list[models.PointStruct]:
    return [
        models.PointStruct(
            id=point_id(r["chunk_id"]),
            vector={emb.DENSE: dense[i], emb.SPARSE: sparse[i]},
            payload=r,  # full record: text + all tags + structural fields
        )
        for i, r in enumerate(records)
    ]


def swap_alias(client: QdrantClient) -> None:
    """Atomically point ALIAS_NAME at PHYSICAL_NAME. Aborts loudly if a real collection
    (not an alias) already owns the alias name — Qdrant forbids the collision."""
    real = {c.name for c in client.get_collections().collections}
    if ALIAS_NAME in real:
        raise RuntimeError(
            f"a real collection named {ALIAS_NAME!r} exists; the alias scheme needs "
            f"that name free for an alias. Drop/rename that collection, then re-run.")

    ops: list = []
    have_alias = any(a.alias_name == ALIAS_NAME for a in client.get_aliases().aliases)
    if have_alias:
        ops.append(models.DeleteAliasOperation(
            delete_alias=models.DeleteAlias(alias_name=ALIAS_NAME)))
    ops.append(models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=PHYSICAL_NAME,
                                        alias_name=ALIAS_NAME)))
    client.update_collection_aliases(change_aliases_operations=ops)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the build plan, make no API calls / no writes")
    parser.add_argument("--force", action="store_true",
                        help="delete and recreate the physical collection")
    args = parser.parse_args()

    try:
        records = load_chunks()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    n_tokens = [r["n_tokens"] for r in records]
    batches = emb.batch_by_tokens(n_tokens)

    print(f"chunks: {len(records)}  (total {sum(n_tokens)} tokens)")
    print(f"physical: {PHYSICAL_NAME}   alias: {ALIAS_NAME}")
    print(f"dense batches (Voyage, <= {int(emb.VOYAGE_TOKEN_LIMIT * emb.VOYAGE_BATCH_MARGIN)} tok each): {len(batches)}")
    for i, b in enumerate(batches):
        print(f"  batch {i}: {len(b)} chunks, {sum(n_tokens[j] for j in b)} tokens")
    print(f"keyword indexes: {', '.join(INDEXED_KEYWORD_FIELDS)}")
    print(f"integer indexes: {', '.join(INDEXED_INTEGER_FIELDS)}")

    if args.dry_run:
        print("\n[dry-run] no collection written.")
        return 0

    client = _client()
    ensure_collection(client, force=args.force)

    texts = [r["text"] for r in records]
    dense = emb.embed_dense_documents(texts, n_tokens)
    sparse = emb.embed_sparse_documents(texts)
    empty_sparse = sum(1 for s in sparse if not s.indices)
    print(f"\nembedded: {len(dense)} dense, {len(sparse)} sparse "
          f"({empty_sparse} sparse with no BM25 terms)")

    points = build_points(records, dense, sparse)
    # Qdrant has a 33MB payload limit per request — upsert in batches of 200
    upsert_batch = 200
    for i in range(0, len(points), upsert_batch):
        client.upsert(collection_name=PHYSICAL_NAME,
                      points=points[i: i + upsert_batch], wait=True)
        print(f"  upserted {min(i + upsert_batch, len(points))}/{len(points)}")
    swap_alias(client)

    count = client.count(PHYSICAL_NAME, exact=True).count
    if count != len(records):
        print(f"ERROR: upserted {count} points, expected {len(records)}", file=sys.stderr)
        return 1
    print(f"upserted {count} points into {PHYSICAL_NAME}, alias {ALIAS_NAME} -> {PHYSICAL_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
