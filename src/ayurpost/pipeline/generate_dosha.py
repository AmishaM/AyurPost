"""
Dosha education reel orchestrator — one topic from dosha_education.yaml → reel MP4.

Stages (same pipeline as seasonal):
  1. Load topic from dosha_education.yaml by --dosha-key and --topic-type
  2. HybridRetriever with dosha HARD filter
  3. Claude Opus — dosha-education-specific system prompt
  4. Veo 3.1 Lite — hyper-realistic clips
  5. Chirp3-HD TTS — English voiceover
  6. FFmpeg — xfade + music + smooth fade → reel.mp4

Usage:
    # dry-run
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_dosha --dry-run \\
        --dosha-key vata --topic-type prakriti

    # real run
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_dosha \\
        --dosha-key pitta --topic-type remedy --model claude-opus-4-8 \\
        --music data/music/music/india_happy-indian-classical-indian-music-494847.mp3

Output:
    data/generated/dosha-<key>-<topic>-<timestamp>/
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
from ayurpost.pipeline.season import _load_seasons   # reuse yaml loader pattern

DRAFT_STAMP = "DRAFT — pending clinician sign-off"
_DOSHA_YAML = Path(__file__).parent.parent / "roadmap" / "dosha_education.yaml"

TOPIC_TYPES = ["prakriti", "personality", "ailment", "seasonal", "remedy"]

_SYSTEM = """You write short educational Instagram/YouTube reel scripts about Ayurvedic \
doshas for a wellness clinic in Bengaluru, India.

Hard rules:
- Ground every Ayurvedic claim ONLY in the numbered CONTEXT passages provided. Do not \
add characteristics, symptoms, or remedies not supported by the context.
- For each scene, list in grounded_chunk_ids the chunk_id(s) you used.
- Do NOT make medical claims of cure, reversal, or guaranteed outcomes. This is general \
Ayurvedic education, not treatment advice.
- Keep it warm, accessible, and engaging. If you use Sanskrit terms, gloss them plainly.
- Write the hook and all scenes as ONE continuous narrative arc — each scene must bridge \
naturally from the previous one. The listener should hear a single flowing story.
- Always include the wellness disclaimer."""


def _load_dosha_topic(dosha_key: str, topic_type: str) -> tuple[dict, dict]:
    """Return (dosha_dict, topic_dict) from dosha_education.yaml."""
    data = yaml.safe_load(_DOSHA_YAML.read_text(encoding="utf-8"))
    for dosha in data["doshas"]:
        if dosha["key"] == dosha_key:
            for topic in dosha["topics"]:
                if topic["topic_type"] == topic_type:
                    return dosha, topic
            keys = [t["topic_type"] for t in dosha["topics"]]
            raise ValueError(f"topic_type {topic_type!r} not in {dosha_key} — "
                             f"available: {keys}")
    keys = [d["key"] for d in data["doshas"]]
    raise ValueError(f"dosha_key {dosha_key!r} not found — available: {keys}")


def generate_dosha_script(dosha: dict, topic: dict, chunks: list[dict],
                           model: str, n_scenes: int, feedback: str = "") -> ReelScript:
    """One Opus call -> ReelScript grounded in chunks, dosha-education system prompt."""
    import anthropic
    from ayurpost.pipeline.content import _format_context

    if not chunks:
        raise ValueError("no chunks supplied — refusing to generate ungrounded content")

    context = _format_context(chunks)
    user = (
        f"DOSHA: {dosha['label']} ({dosha['key']})\n"
        f"TOPIC: {topic['label']} (type={topic['topic_type']})\n"
        f"EDUCATIONAL ANGLE: {topic['query_angle'].strip()}\n\n"
        f"CONTEXT (ground all claims only in these passages):\n{context}\n\n"
        f"Write a reel script with EXACTLY {n_scenes} scenes educating viewers about "
        f"this topic. Each scene needs an English voiceover and an image prompt. "
        f"Voiceover: 1-2 flowing sentences, MAX 25 words. "
        f"Image prompts: body parts (arms, legs, hands, skin texture) are acceptable "
        f"and encouraged for prakriti/personality topics — no faces, no full body, no text overlays."
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
        raise ValueError(f"expected {n_scenes} scenes, model returned {len(script.scenes)}")
    supplied = {c["chunk_id"] for c in chunks}
    for i, scene in enumerate(script.scenes, 1):
        bad = [cid for cid in scene.grounded_chunk_ids if cid not in supplied]
        if bad:
            raise ValueError(f"scene {i} cites chunk_ids not in supplied context: {bad}")
    return script


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dosha-key", required=True, choices=["vata", "pitta", "kapha"])
    parser.add_argument("--topic-type", required=True, choices=TOPIC_TYPES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=config.LLM_MODEL)
    parser.add_argument("--n-scenes", type=int, default=3)
    parser.add_argument("--music")
    parser.add_argument("--music-ss", type=int, default=0)
    parser.add_argument("--out")
    args = parser.parse_args()

    dosha, topic = _load_dosha_topic(args.dosha_key, args.topic_type)
    slug    = f"dosha-{dosha['key']}-{topic['topic_type']}"
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else config.DATA_DIR / "generated" / f"{slug}-{ts}"
    music   = Path(args.music) if args.music else None

    print(f"dosha:    {dosha['label']}")
    print(f"topic:    {topic['label']} ({topic['topic_type']})")
    print(f"query:    {topic['query_angle'].strip()[:80]}...")
    print(f"model:    {args.model}  n_scenes={args.n_scenes}")
    print(f"music:    {music or '(none)'}")
    print(f"out_dir:  {out_dir}")

    if args.dry_run:
        print("\nplan:")
        print(f"  1. HybridRetriever (dosha filter={dosha['dosha_filter']})")
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
    hits = HybridRetriever().search(
        topic["query_angle"], limit=6, doshas=dosha["dosha_filter"])
    if not hits:
        print("ERROR: 0 chunks retrieved", file=sys.stderr)
        return 1
    chunks = [h.payload for h in hits]
    print(f"      {len(chunks)} chunks: {[c['chunk_id'] for c in chunks]}")

    print(f"[2/5] generating script ({args.model})...")
    script = generate_dosha_script(dosha, topic, chunks, args.model, args.n_scenes)
    (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    print(f"      hook: {script.hook!r}")

    print("[3/5] generating Veo clips...")
    clips_dict = generate_reel_clips(script, dosha["key"], out_dir)
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
        "pillar": "dosha_education",
        "dosha": dosha["key"], "topic": topic["topic_type"],
        "label": topic["label"],
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
