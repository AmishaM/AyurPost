"""
AyurPost eval runner — runs all eval categories and prints a summary report.

Usage:
    # all evals (hits Qdrant + Anthropic API)
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals

    # structural only (no API calls, fast)
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals --category structural

    # single category
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals --category retrieval
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals --category audit

Exit code: 0 = all pass, 1 = any failure.
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", default="all",
                        choices=["all", "structural", "retrieval", "audit"],
                        help="Which eval category to run (default: all)")
    args = parser.parse_args()

    all_results = []

    if args.category in ("all", "structural"):
        print("\n── Structural Validation (no API calls) ─────────────────────────────")
        from ayurpost.eval.eval_script import run as run_structural
        results = run_structural(verbose=True)
        all_results.extend(results)

    if args.category in ("all", "retrieval"):
        print("\n── Retrieval Quality (Qdrant + Voyage) ──────────────────────────────")
        from ayurpost.eval.eval_retrieval import run as run_retrieval
        results = run_retrieval(verbose=True)
        all_results.extend(results)

    if args.category in ("all", "audit"):
        print("\n── Auditor Accuracy — Compliance (Sonnet 4.6) ───────────────────────")
        from ayurpost.eval.eval_audit import run_compliance, run_groundedness
        c_results = run_compliance(verbose=True)
        all_results.extend(c_results)
        print("\n── Auditor Accuracy — Groundedness (Sonnet 4.6) ─────────────────────")
        g_results = run_groundedness(verbose=True)
        all_results.extend(g_results)

    # Summary
    total  = len(all_results)
    passed = sum(getattr(r, "passed", False) for r in all_results)
    failed = total - passed

    print("\n" + "═" * 70)
    print(f"OVERALL: {passed}/{total} passed", end="")
    if failed:
        print(f"  ── {failed} FAILED")
    else:
        print("  ── ALL PASS ✓")
    print("═" * 70)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
