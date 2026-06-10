"""
LLM auditor for AyurPost generated reel scripts.

Runs automatically after reel generation as a pre-screening layer before
clinician review. Uses Claude Sonnet 4.6 as a structured judge to check:

  1. Groundedness — does each scene's voiceover stay within what its cited
     KB chunks actually say? Flags fabricated or extrapolated Ayurvedic claims.
  2. Compliance — does any voiceover contain regulated health language?
     (cure/reversal claims, quantified outcome guarantees, disease treatment ads)
  3. Tone — is it general wellness education or does it slide into treatment advice?

Input:  an artifact directory containing script.json (with grounded_chunk_ids)
Output: audit_report.json written to the same dir; manifest.json updated with
        audit summary and overall pass/fail.

Does NOT replace clinician sign-off — catches LLM blind spots but shares them
too. An auditor pass means "no obvious red flags found", not "safe to publish".

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.audit \\
        data/generated/monsoon-vata-20260610-123456

    # quiet: just print PASS/FAIL and exit code (0=pass, 1=fail)
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.audit --quiet \\
        data/generated/monsoon-vata-20260610-123456
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny

from ayurpost import config

MODEL = "claude-sonnet-4-6"    # Sonnet 4.6 — capable judge, cheaper than Opus

# Qdrant collection (via alias — matches config.QDRANT_COLLECTION)
_COLLECTION = config.QDRANT_COLLECTION

DRAFT_STAMP = "DRAFT — pending clinician sign-off"

# ── Compliance phrases the judge watches for ─────────────────────────────────
# Informational only — the LLM makes the final call; this list steers its focus.
_REGULATED_PATTERNS = [
    "cures", "cure", "reverses", "reversal", "treats", "treatment of",
    "lose X kg", "lose weight in", "in 10 days", "in N days", "guaranteed",
    "100%", "clinically proven", "FDA", "scientifically proven",
    "eliminates diabetes", "reverse diabetes", "reverse diabetics",
]


# ── Structured output schema ─────────────────────────────────────────────────

class SceneVerdict(BaseModel):
    scene_index: int = Field(description="0-based scene index (0 = hook).")
    grounded: bool = Field(
        description="True if the voiceover's claims are faithfully supported by "
                    "the provided chunk texts. False if claims go beyond or contradict them.")
    compliance_ok: bool = Field(
        description="True if the voiceover contains NO regulated language: no cure/"
                    "reversal/treatment claims, no quantified outcome guarantees, no "
                    "disease-treatment advertising.")
    issues: list[str] = Field(
        description="Specific issues found. Empty list if none. Each issue is one "
                    "short sentence naming the problem and quoting the offending phrase.")
    note: str = Field(
        description="One sentence of context or reassurance. If no issues, confirm "
                    "what makes this scene compliant.")


class AuditReport(BaseModel):
    overall_pass: bool = Field(
        description="True only if EVERY scene is both grounded=True AND compliance_ok=True.")
    scenes: list[SceneVerdict] = Field(
        description="One verdict per scene (hook is scene_index=0).")
    summary: str = Field(
        description="2-3 sentence overall assessment. State the pass/fail verdict, "
                    "the most important finding, and the recommended action.")


# ── Qdrant chunk fetch ───────────────────────────────────────────────────────

def _fetch_chunks(chunk_ids: list[str]) -> dict[str, str]:
    """Return {chunk_id: text} for the given ids from Qdrant."""
    if not chunk_ids:
        return {}
    client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
    results, _ = client.scroll(
        collection_name=_COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="chunk_id", match=MatchAny(any=chunk_ids))
        ]),
        with_payload=True,
        limit=len(chunk_ids) + 5,
    )
    return {p.payload["chunk_id"]: p.payload["text"] for p in results}


# ── Audit logic ──────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a compliance and groundedness auditor for Ayurvedic wellness content \
published by a clinic on social media.

For each scene you receive:
  - The voiceover text the clinic will publish
  - The KB passages (with chunk_ids) the author cited as its grounding

Your job — two checks per scene:

1. GROUNDEDNESS: Does the voiceover stay faithfully within what the cited passages say?
   - Minor stylistic framing is fine ("Ayurveda recommends..." = OK).
   - Asserting a specific fact NOT present in or contradicted by the chunks = NOT grounded.
   - If no chunks are cited, the scene cannot be grounded — flag it.

2. COMPLIANCE: Does the voiceover contain regulated health advertising language?
   Flag any of these (India Drugs & Magic Remedies Act / ASCI standards):
   - Claims of cure, reversal, or elimination of a named disease
   - Specific quantified outcome guarantees ("lose X kg", "in N days", "100% effective")
   - Language implying the service is a medical treatment for a disease
   General wellness education ("may support", "traditionally used to", "described in \
classical texts as") is fine and should NOT be flagged.

Be strict on compliance. Be fair on groundedness — penalise fabrication, not paraphrase.\
"""


def audit_script(script: dict) -> AuditReport:
    """Run the Sonnet judge on a script dict loaded from script.json."""
    hook = script.get("hook", "")
    scenes = script.get("scenes", [])

    # Collect all chunk_ids referenced across all scenes
    all_ids: list[str] = []
    for s in scenes:
        all_ids.extend(s.get("grounded_chunk_ids", []))
    chunk_texts = _fetch_chunks(list(set(all_ids)))

    # Build the user message: one block per scene (hook + numbered scenes)
    blocks: list[str] = []

    # Hook (scene_index 0) — hooks are general framing, not claim-grounded.
    # Exempt from groundedness check; still audited for compliance only.
    blocks.append(
        f"SCENE 0 (hook — general framing, NOT required to be chunk-grounded)\n"
        f"Voiceover: {hook!r}\n"
        f"Note: for the hook, only check COMPLIANCE. Mark grounded=True automatically.\n"
        f"Cited chunks: (none by design)"
    )

    for i, scene in enumerate(scenes, 1):
        vo = scene.get("voiceover_en", "")
        cids = scene.get("grounded_chunk_ids", [])
        texts = "\n".join(
            f"  [{cid}]: {chunk_texts.get(cid, '(chunk not found in KB)')}"
            for cid in cids
        ) or "  (no chunk_ids cited)"
        blocks.append(
            f"SCENE {i}\n"
            f"Voiceover: {vo!r}\n"
            f"Cited chunk_ids: {cids}\n"
            f"Chunk texts:\n{texts}"
        )

    user = (
        f"Audit the following {len(blocks)} scenes (0=hook) from an Ayurvedic reel.\n\n"
        + "\n\n---\n\n".join(blocks)
        + f"\n\nReturn one SceneVerdict per scene (scene_index 0 through {len(blocks)-1})."
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                  base_url=config.ANTHROPIC_BASE_URL)
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=AuditReport,
    )
    report = resp.parsed_output
    if report is None:
        raise RuntimeError(f"auditor returned no parsed output (stop_reason={resp.stop_reason})")
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("artifact_dir",
                        help="path to the generated artifact directory containing script.json")
    parser.add_argument("--quiet", action="store_true",
                        help="print only PASS/FAIL; exit 0 on pass, 1 on fail")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    script_path  = artifact_dir / "script.json"
    manifest_path = artifact_dir / "manifest.json"
    report_path  = artifact_dir / "audit_report.json"

    if not script_path.exists():
        print(f"ERROR: script.json not found in {artifact_dir}", file=sys.stderr)
        return 2

    script = json.loads(script_path.read_text(encoding="utf-8"))

    if not args.quiet:
        print(f"auditing: {artifact_dir}")
        print(f"model:    {MODEL}")
        print(f"scenes:   {len(script.get('scenes', []))} + hook")
        print()

    report = audit_script(script)

    # Write audit_report.json
    report_data = report.model_dump()
    report_data["audited_at"] = datetime.now(timezone.utc).isoformat()
    report_data["model"] = MODEL
    report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    # Update manifest.json with audit summary
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["audit"] = {
            "overall_pass": report.overall_pass,
            "audited_at": report_data["audited_at"],
            "model": MODEL,
            "summary": report.summary,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if args.quiet:
        print("PASS" if report.overall_pass else "FAIL")
        return 0 if report.overall_pass else 1

    # Verbose output
    verdict_str = "✓ PASS" if report.overall_pass else "✗ FAIL"
    print(f"Overall: {verdict_str}")
    print(f"Summary: {report.summary}")
    print()
    for sv in report.scenes:
        g = "✓" if sv.grounded     else "✗"
        c = "✓" if sv.compliance_ok else "✗"
        label = "hook" if sv.scene_index == 0 else f"scene {sv.scene_index}"
        print(f"  [{label}]  grounded={g}  compliance={c}")
        for issue in sv.issues:
            print(f"    ⚠ {issue}")
        if not sv.issues:
            print(f"    {sv.note}")

    print()
    print(f"report written: {report_path}")
    if manifest_path.exists():
        print(f"manifest updated: {manifest_path}")

    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
