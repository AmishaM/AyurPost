"""
Auditor accuracy evals — hits Anthropic (Sonnet 4.6).

Tests compliance and groundedness verdicts from audit.py against known cases.

Compliance: wraps each test voiceover as scene 1 of a minimal script
  (scene 0 = hook, exempt from groundedness; scene 1 = the voiceover under test).
  Patches _fetch_chunks to return empty (compliance doesn't need chunk texts).

Groundedness: patches _fetch_chunks to return fixture chunk texts keyed by
  synthetic chunk_ids, so no Qdrant call is needed.

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.eval_audit
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from unittest.mock import patch

from ayurpost.eval.cases import COMPLIANCE_CASES, GROUNDEDNESS_CASES
from ayurpost.pipeline.audit import audit_script


@dataclass
class AuditResult:
    case_id: str
    desc: str
    category: str   # "compliance" | "groundedness"
    expected: bool
    actual: bool
    passed: bool
    auditor_note: str


# ── helpers ───────────────────────────────────────────────────────────────────

def _minimal_script(scene1_voiceover: str,
                    chunk_ids: list[str] | None = None) -> dict:
    """Build a minimal script dict with hook (scene 0) + one test scene (scene 1)."""
    return {
        "hook": "Opening hook for eval — not assessed for groundedness.",
        "scenes": [
            {
                "voiceover_en": scene1_voiceover,
                "grounded_chunk_ids": chunk_ids or ["eval-chunk-001"],
            }
        ],
        "disclaimer": "General wellness information only. Consult a qualified practitioner.",
    }


# ── Compliance evals ──────────────────────────────────────────────────────────

def run_compliance(verbose: bool = True) -> list[AuditResult]:
    results = []
    # Patch _fetch_chunks to return empty text (compliance doesn't need chunk content)
    with patch("ayurpost.pipeline.audit._fetch_chunks", return_value={"eval-chunk-001": ""}):
        for case in COMPLIANCE_CASES:
            script = _minimal_script(case["text"])
            try:
                report = audit_script(script)
                # scene index 1 is our test scene (scene 0 = hook, auto-pass groundedness)
                verdict = report.scenes[1] if len(report.scenes) > 1 else report.scenes[0]
                actual = verdict.compliance_ok
                note = verdict.note or ("; ".join(verdict.issues) if verdict.issues else "")
            except Exception as e:
                actual = not case["expected"]   # force fail
                note = f"error: {e}"

            passed = actual == case["expected"]
            r = AuditResult(case["id"], case["desc"], "compliance",
                            case["expected"], actual, passed, note)
            results.append(r)
            if verbose:
                exp = "PASS" if case["expected"] else "FAIL"
                got = "PASS" if actual else "FAIL"
                status = "✓" if passed else "✗"
                print(f"  COMPLIANCE  {case['id']}  expected={exp}  got={got}  {status}  "
                      f"({case['desc'][:50]})")
                if not passed:
                    print(f"              auditor note: {note[:120]}")
    return results


# ── Groundedness evals ────────────────────────────────────────────────────────

def run_groundedness(verbose: bool = True) -> list[AuditResult]:
    results = []
    for case in GROUNDEDNESS_CASES:
        chunk_id = f"eval-g-{case['id'].lower()}"
        fake_chunks = {chunk_id: " ".join(case["chunks"])}

        with patch("ayurpost.pipeline.audit._fetch_chunks", return_value=fake_chunks):
            script = _minimal_script(case["voiceover"], chunk_ids=[chunk_id])
            try:
                report = audit_script(script)
                verdict = report.scenes[1] if len(report.scenes) > 1 else report.scenes[0]
                actual = verdict.grounded
                note = verdict.note or ("; ".join(verdict.issues) if verdict.issues else "")
            except Exception as e:
                actual = not case["expected"]
                note = f"error: {e}"

        passed = actual == case["expected"]
        r = AuditResult(case["id"], case["desc"], "groundedness",
                        case["expected"], actual, passed, note)
        results.append(r)
        if verbose:
            exp = "grounded" if case["expected"] else "ungrounded"
            got = "grounded" if actual else "ungrounded"
            status = "✓" if passed else "✗"
            print(f"  GROUNDEDNESS {case['id']}  expected={exp:<10}  got={got:<10}  {status}  "
                  f"({case['desc'][:50]})")
            if not passed:
                print(f"               auditor note: {note[:120]}")
    return results


def run(verbose: bool = True) -> list[AuditResult]:
    results = []
    results.extend(run_compliance(verbose))
    results.extend(run_groundedness(verbose))
    return results


if __name__ == "__main__":
    results = run()
    failed = [r for r in results if not r.passed]
    comp_results = [r for r in results if r.category == "compliance"]
    gnd_results  = [r for r in results if r.category == "groundedness"]
    print(f"\nCompliance:    {sum(r.passed for r in comp_results)}/{len(comp_results)} passed")
    print(f"Groundedness:  {sum(r.passed for r in gnd_results)}/{len(gnd_results)} passed")
    sys.exit(1 if failed else 0)
