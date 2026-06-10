"""
Reel orchestrator: season → grounded script → Veo clips → TTS → MP4.

End-to-end pipeline for ONE seasonal reel, grounded in the vol1 hybrid index.
Stages:
  1. select_season (calendar date or explicit --season-key)
  2. HybridRetriever — dosha-filtered Qdrant search
  3. Claude Opus structured ReelScript (n_scenes scenes, English voiceovers)
  4. Veo 3.1 Lite — one hyper-realistic video clip per scene
  5. Google TTS — Chirp3-HD English voiceover per scene (random M/F voice)
  6. FFmpeg — xfade concat + optional music bed + smooth fade → MP4

Output is a DRAFT pending clinician sign-off (manifest stamps it) — never published.

Usage:
    # dry-run: print the plan, make NO external calls
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate --dry-run

    # real run, current season, Opus 4.8
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate --model claude-opus-4-8

    # specific season + music
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate \\
        --season-key monsoon --music data/music/music/freemusicforvideo-indian-flute-515569.mp3

Output:
    data/generated/<season>-<dosha>-<timestamp>/
      script.json  clip_hook.mp4  clip_scene_*.mp4
      scene_*.en.mp3  reel.mp4  manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from ayurpost import config
from ayurpost.pipeline.season import select_season, _load_seasons

DRAFT_STAMP = "DRAFT — pending clinician sign-off"


def _load_season_by_key(key: str) -> dict:
    for s in _load_seasons():
        if s["key"] == key:
            return s
    raise ValueError(f"season key {key!r} not found in seasonal YAML")


def build_query(season: dict) -> str:
    return (f"{season['label']} season — {', '.join(season['aggravated_dosha'])} "
            f"aggravation: {', '.join(season['ailments'])}; "
            f"remedy: {season['remedy_angle']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; make NO external calls")
    parser.add_argument("--model", default=config.LLM_MODEL,
                        help=f"generation model (default {config.LLM_MODEL})")
    parser.add_argument("--n-scenes", type=int, default=3,
                        help="number of scenes (default 3; each maps to one Veo clip)")
    parser.add_argument("--season-key",
                        help="season key from seasonal_bengaluru.yaml "
                             "(e.g. monsoon, summer, winter). Overrides --date.")
    parser.add_argument("--date", help="ISO date to pick the season (default today)")
    parser.add_argument("--music",
                        help="path to background music MP3 (optional). "
                             "Ducked to 15%% under voiceover, fades out at end.")
    parser.add_argument("--music-ss", type=int, default=0,
                        help="start offset (seconds) within the music file (default 0)")
    parser.add_argument("--out",
                        help="artifact dir (default data/generated/<slug>-<ts>)")
    args = parser.parse_args()

    # Season selection
    if args.season_key:
        try:
            season = _load_season_by_key(args.season_key)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    else:
        on = date.fromisoformat(args.date) if args.date else date.today()
        try:
            season = select_season(on)
        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    doshas = season["aggravated_dosha"]
    query  = build_query(season)
    slug   = f"{season['key']}-{'-'.join(doshas)}"
    ts     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else config.DATA_DIR / "generated" / f"{slug}-{ts}"
    music   = Path(args.music) if args.music else None

    print(f"season:      {season['key']} ({season['label']})")
    print(f"dosha filter:{doshas}")
    print(f"query:       {query!r}")
    print(f"model:       {args.model}")
    print(f"n_scenes:    {args.n_scenes}")
    print(f"music:       {music or '(none)'}")
    print(f"out_dir:     {out_dir}")

    if args.dry_run:
        print("\nplan:")
        print("  1. HybridRetriever.search(query, doshas, limit=6)  [Qdrant + Voyage]")
        print(f"  2. Claude {args.model} -> ReelScript ({args.n_scenes} scenes, English)")
        print("  3. Veo 3.1 Lite -> hook clip (6s) + scene clips (8s each), hyper-realistic")
        print("  4. Google TTS Chirp3-HD en-IN -> scene_*.en.mp3 (random M/F voice)")
        print("  5. FFmpeg xfade concat + music bed + smooth fade -> reel.mp4")
        print(f"\nartifacts: {out_dir}/")
        print("  script.json  clip_hook.mp4  clip_scene_*.mp4  scene_*.en.mp3  "
              "reel.mp4  manifest.json")
        print(f"\n[dry-run] {DRAFT_STAMP}")
        print("[dry-run] no external calls made, nothing written.")
        return 0

    # Deferred imports so --dry-run has zero SDK overhead.
    from ayurpost.retrieval.search import HybridRetriever
    from ayurpost.pipeline.content import generate_script
    from ayurpost.pipeline.veo import generate_reel_clips
    from ayurpost.pipeline.voice import synthesize_en_chirp
    from ayurpost.pipeline.assemble import build_veo_reel

    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n[1/5] retrieving grounding chunks...")
    hits = HybridRetriever().search(query, limit=6, doshas=doshas)
    if not hits:
        print("ERROR: retrieval returned 0 chunks — cannot ground content", file=sys.stderr)
        return 1
    retrieved = [{"chunk_id": h.payload["chunk_id"], "score": h.score} for h in hits]
    chunks = [h.payload for h in hits]
    print(f"      {len(chunks)} chunks: {[r['chunk_id'] for r in retrieved]}")

    print(f"[2/5] generating script ({args.model}, n_scenes={args.n_scenes})...")
    script = generate_script(season, chunks, model=args.model, n_scenes=args.n_scenes)
    (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    print(f"      script.json written — hook: {script.hook!r}")

    print("[3/5] generating Veo clips (hyper-realistic, 9:16)...")
    clips_dict = generate_reel_clips(script, season["key"], out_dir)
    # Ordered: hook first, then scenes in order.
    clip_paths = [clips_dict["hook"]] + [
        clips_dict[f"scene_{i}"] for i in range(len(script.scenes))
    ]
    print(f"      {len(clip_paths)} clips generated")

    print("[4/5] synthesizing voiceovers (Chirp3-HD en-IN)...")
    # One voiceover per clip: hook text + scene voiceovers in order.
    voiceover_texts = [script.hook] + [s.voiceover_en for s in script.scenes]
    audio_paths = synthesize_en_chirp(voiceover_texts, out_dir)

    print("[5/5] assembling reel...")
    reel = build_veo_reel(
        clips=clip_paths,
        audios=audio_paths,
        out_path=out_dir / "reel.mp4",
        music=music,
        music_ss=args.music_ss,
    )
    print(f"      reel.mp4  ({reel.stat().st_size / 1_048_576:.1f} MB)")

    manifest = {
        "status": DRAFT_STAMP,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": {"key": season["key"], "label": season["label"],
                   "aggravated_dosha": doshas},
        "query": query,
        "retrieved_chunks": retrieved,
        "n_scenes": args.n_scenes,
        "models": {
            "generation": args.model,
            "video": "veo-3.1-lite-generate-001",
            "tts": "en-IN-Chirp3-HD (random M/F)",
            "embedding": config.EMBEDDING_MODEL,
        },
        "music": str(music) if music else None,
        "artifacts": {
            "script": "script.json",
            "clips": [p.name for p in clip_paths],
            "audio": [p.name for p in audio_paths],
            "reel": "reel.mp4",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Auto-audit: run LLM groundedness + compliance check before clinician review.
    print("\n[audit] running LLM auditor (Sonnet 4.6)...")
    from ayurpost.pipeline.audit import audit_script
    audit_report = audit_script(json.loads((out_dir / "script.json").read_text()))
    audit_data = audit_report.model_dump()
    audit_data["audited_at"] = datetime.now(timezone.utc).isoformat()
    audit_data["model"] = "claude-sonnet-4-6"
    (out_dir / "audit_report.json").write_text(json.dumps(audit_data, indent=2), encoding="utf-8")
    manifest["audit"] = {
        "overall_pass": audit_report.overall_pass,
        "audited_at": audit_data["audited_at"],
        "summary": audit_report.summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    audit_verdict = "✓ PASS" if audit_report.overall_pass else "✗ FAIL"
    print(f"      audit: {audit_verdict} — {audit_report.summary[:100]}")

    print(f"\nDone. {DRAFT_STAMP}")
    print(f"artifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
