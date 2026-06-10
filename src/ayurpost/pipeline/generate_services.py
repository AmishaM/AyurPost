"""
Clinic services reel orchestrator — one publishable service → reel MP4.

Stages (same pipeline as seasonal):
  1. Load service from clinic_services.yaml by --service-key
  2. HybridRetriever (no dosha filter — service content is treatment-focused)
  3. Claude Opus — services-specific system prompt
  4. Veo 3.1 Lite — hyper-realistic clips
  5. Chirp3-HD TTS — English voiceover
  6. FFmpeg — xfade + music + smooth fade → reel.mp4

Usage:
    # dry-run
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_services --dry-run \\
        --service-key shirodhara

    # real run
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_services \\
        --service-key abhyanga --model claude-opus-4-8 \\
        --music data/music/music/alex-morgan-sunrise-yoga-flow-537475.mp3

Output:
    data/generated/service-<key>-<timestamp>/
      script.json  clip_*.mp4  scene_*.en.mp3  reel.mp4  manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ayurpost import config
from ayurpost.pipeline.content import ReelScript

DRAFT_STAMP = "DRAFT — pending clinician sign-off"
_SERVICES_YAML = Path(__file__).parent.parent / "roadmap" / "clinic_services.yaml"

_SYSTEM = """You write short educational Instagram/YouTube reel scripts about Ayurvedic \
clinic services for a wellness clinic in Bengaluru, India.

Hard rules:
- Ground every claim about the service ONLY in the numbered CONTEXT passages provided. \
Do not add benefits or mechanisms not supported by the context.
- For each scene, list in grounded_chunk_ids the chunk_id(s) you used.
- Do NOT make medical claims of cure, reversal, guaranteed/quantified outcomes, or disease \
treatment. This is general wellness education about a traditional Ayurvedic practice.
- Keep it warm, trustworthy, and accessible. Explain what the therapy involves and why \
Ayurveda recommends it — not what disease it cures.
- Write the hook and all scenes as ONE continuous narrative arc bridging naturally between \
scenes. The viewer should hear a single flowing, engaging story.
- Always include the wellness disclaimer."""


def _load_service(service_key: str) -> dict:
    data = yaml.safe_load(_SERVICES_YAML.read_text(encoding="utf-8"))
    for svc in data["services"]:
        if svc["key"] == service_key:
            if not svc.get("publishable", False):
                raise ValueError(
                    f"service {service_key!r} is NOT publishable "
                    f"(compliance_flag={svc.get('compliance_flag')}). "
                    f"Doctor must approve wording before generating content.")
            return svc
    keys = [s["key"] for s in data["services"] if s.get("publishable", False)]
    raise ValueError(f"service {service_key!r} not found — publishable services: {keys}")


def generate_services_script(service: dict, chunks: list[dict],
                              model: str, n_scenes: int, feedback: str = "") -> ReelScript:
    """One Opus call -> ReelScript grounded in chunks, services system prompt."""
    import anthropic
    from ayurpost.pipeline.content import _format_context

    if not chunks:
        raise ValueError("no chunks supplied — refusing to generate ungrounded content")

    context = _format_context(chunks)
    user = (
        f"SERVICE: {service['label']} (category={service['category']})\n\n"
        f"CONTEXT (ground all claims only in these passages):\n{context}\n\n"
        f"Write a reel script with EXACTLY {n_scenes} scenes about this Ayurvedic service. "
        f"Explain what it is, why Ayurveda recommends it, and who benefits from it — "
        f"without making cure or outcome claims. Each scene needs an English voiceover "
        f"and an image prompt. Voiceover: 1-2 flowing sentences, MAX 25 words. "
        f"Image prompts: close-up of the treatment area (hands, oil on forehead, back) "
        f"is acceptable — no full faces, no identifiable people, no text overlays."
        + (f"\n\nDoctor feedback to incorporate: {feedback}" if feedback else "")
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                  base_url=config.ANTHROPIC_BASE_URL)
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=ReelScript,
    )
    script = resp.parsed_output
    if script is None:
        raise RuntimeError(f"model returned no parsed output (stop_reason={resp.stop_reason})")
    if len(script.scenes) != n_scenes:
        raise ValueError(f"expected {n_scenes} scenes, got {len(script.scenes)}")
    supplied = {c["chunk_id"] for c in chunks}
    for i, scene in enumerate(script.scenes, 1):
        bad = [cid for cid in scene.grounded_chunk_ids if cid not in supplied]
        if bad:
            raise ValueError(f"scene {i} cites chunk_ids not in context: {bad}")
    return script


def main() -> int:
    data = yaml.safe_load(_SERVICES_YAML.read_text(encoding="utf-8"))
    publishable_keys = [s["key"] for s in data["services"] if s.get("publishable", False)]

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service-key", required=True, choices=publishable_keys,
                        help=f"publishable service key ({', '.join(publishable_keys)})")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=config.LLM_MODEL)
    parser.add_argument("--n-scenes", type=int, default=3)
    parser.add_argument("--music")
    parser.add_argument("--music-ss", type=int, default=0)
    parser.add_argument("--out")
    args = parser.parse_args()

    service = _load_service(args.service_key)
    slug    = f"service-{service['key']}"
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else config.DATA_DIR / "generated" / f"{slug}-{ts}"
    music   = Path(args.music) if args.music else None

    print(f"service:  {service['label']} ({service['category']})")
    print(f"model:    {args.model}  n_scenes={args.n_scenes}")
    print(f"music:    {music or '(none)'}")
    print(f"out_dir:  {out_dir}")

    if args.dry_run:
        print("\nplan:")
        print("  1. HybridRetriever (no dosha filter — service-focused retrieval)")
        print(f"  2. Claude {args.model} -> ReelScript ({args.n_scenes} scenes)")
        print("  3. Veo 3.1 Lite — hyper-realistic clips")
        print("  4. Chirp3-HD TTS en-IN")
        print("  5. FFmpeg xfade + music + fade -> reel.mp4")
        print(f"\n[dry-run] {DRAFT_STAMP}")
        return 0

    from ayurpost.retrieval.search import HybridRetriever
    from ayurpost.pipeline.veo import generate_reel_clips
    from ayurpost.pipeline.voice import synthesize_en_chirp
    from ayurpost.pipeline.assemble import build_veo_reel

    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n[1/5] retrieving chunks...")
    query = f"{service['label']} Ayurvedic {service['category']} — procedure, benefits, classical description"
    hits = HybridRetriever().search(query, limit=6)   # no dosha filter for services
    if not hits:
        print("ERROR: 0 chunks retrieved", file=sys.stderr)
        return 1
    chunks = [h.payload for h in hits]
    print(f"      {len(chunks)} chunks: {[c['chunk_id'] for c in chunks]}")

    print(f"[2/5] generating script ({args.model})...")
    script = generate_services_script(service, chunks, args.model, args.n_scenes)
    (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    print(f"      hook: {script.hook!r}")

    print("[3/5] generating Veo clips...")
    clips_dict = generate_reel_clips(script, service["key"], out_dir)
    clip_paths = [clips_dict["hook"]] + [
        clips_dict[f"scene_{i}"] for i in range(len(script.scenes))]

    print("[4/5] TTS (Chirp3-HD)...")
    texts = [script.hook] + [s.voiceover_en for s in script.scenes]
    audio_paths = synthesize_en_chirp(texts, out_dir)

    print("[5/5] assembling...")
    reel = build_veo_reel(clip_paths, audio_paths, out_dir / "reel.mp4",
                          music=music, music_ss=args.music_ss)
    print(f"      {reel.name} ({reel.stat().st_size / 1_048_576:.1f} MB)")

    manifest = {
        "status": DRAFT_STAMP,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pillar": "clinic_services",
        "service": service["key"], "label": service["label"],
        "models": {"generation": args.model, "video": "veo-3.1-lite-generate-001",
                   "tts": "en-IN-Chirp3-HD"},
        "artifacts": {"script": "script.json",
                      "clips": [p.name for p in clip_paths],
                      "audio": [p.name for p in audio_paths],
                      "reel": "reel.mp4"},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n[audit] running LLM auditor (Sonnet 4.6)...")
    from ayurpost.pipeline.audit import audit_script
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    audit_report = audit_script(_json.loads((out_dir / "script.json").read_text()))
    audit_data = audit_report.model_dump()
    audit_data["audited_at"] = _dt.now(_tz.utc).isoformat()
    audit_data["model"] = "claude-sonnet-4-6"
    (out_dir / "audit_report.json").write_text(_json.dumps(audit_data, indent=2), encoding="utf-8")
    manifest["audit"] = {"overall_pass": audit_report.overall_pass,
                         "audited_at": audit_data["audited_at"],
                         "summary": audit_report.summary}
    (out_dir / "manifest.json").write_text(_json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"      {'✓ PASS' if audit_report.overall_pass else '✗ FAIL'} — {audit_report.summary[:100]}")

    print(f"\nDone. {DRAFT_STAMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
