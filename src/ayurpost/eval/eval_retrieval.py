"""
Retrieval quality evals — hits Qdrant + Voyage.

For each case in cases.RETRIEVAL_CASES:
  - Runs HybridRetriever.search() with the case's query and dosha filter
  - Checks that no returned chunk violates the dosha constraint
  - Checks that expected chunk prefix appears in results (if specified)
  - Checks keyword presence in chunk text (if specified)

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.eval_retrieval
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from ayurpost.eval.cases import RETRIEVAL_CASES


@dataclass
class RetrievalResult:
    case_id: str
    desc: str
    passed: bool
    failures: list[str]
    hits: int
    retrieved_ids: list[str]


def run_case(case: dict) -> RetrievalResult:
    from ayurpost.retrieval.search import HybridRetriever

    failures: list[str] = []
    retrieved_ids: list[str] = []

    try:
        hits = HybridRetriever().search(
            case["query"],
            limit=case["top_k"],
            doshas=case.get("doshas"),
        )
    except Exception as e:
        return RetrievalResult(case["id"], case["desc"], False,
                               [f"retrieval error: {e}"], 0, [])

    for h in hits:
        payload = h.payload
        retrieved_ids.append(payload.get("chunk_id", "?"))

        # Hard-filter check: must_not_have_dosha
        must_not = case.get("must_not_have_dosha")
        if must_not:
            doshas_present = payload.get("doshas_mentioned", [])
            # only flag if the chunk has ONLY the forbidden dosha (not mixed)
            if doshas_present == [must_not]:
                failures.append(
                    f"chunk {payload.get('chunk_id')} has only {must_not} "
                    f"— should be filtered by dosha={case.get('doshas')}"
                )

    # Prefix check
    prefix = case.get("must_contain_prefix")
    if prefix and not any(cid.startswith(prefix) for cid in retrieved_ids):
        failures.append(f"expected chunk with prefix {prefix!r} not in top-{case['top_k']}")

    # Keyword check
    keyword = case.get("must_contain_keyword")
    if keyword:
        texts = [h.payload.get("text", "") for h in hits]
        if not any(keyword.lower() in t.lower() for t in texts):
            failures.append(f"keyword {keyword!r} not found in any retrieved chunk text")

    return RetrievalResult(
        case_id=case["id"],
        desc=case["desc"],
        passed=len(failures) == 0,
        failures=failures,
        hits=len(hits),
        retrieved_ids=retrieved_ids,
    )


def run(verbose: bool = True) -> list[RetrievalResult]:
    results = []
    for case in RETRIEVAL_CASES:
        r = run_case(case)
        results.append(r)
        if verbose:
            status = "✓ PASS" if r.passed else "✗ FAIL"
            print(f"  RETRIEVAL   {r.case_id} ({r.desc[:50]:<52}) {status}  hits={r.hits}")
            for f in r.failures:
                print(f"              ⚠ {f}")
    return results


if __name__ == "__main__":
    results = run()
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} retrieval evals passed")
    sys.exit(1 if failed else 0)
