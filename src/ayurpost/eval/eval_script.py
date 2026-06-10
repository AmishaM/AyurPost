"""
Structural validation evals — deterministic, no API calls.

Validates every script.json in data/generated/ against structural rules:
  - hook non-empty
  - scene count matches manifest n_scenes (if manifest exists)
  - each scene voiceover_en ≤ 15 words
  - each scene has non-empty grounded_chunk_ids
  - disclaimer field present

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.eval.eval_script
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass

from ayurpost import config

GENERATED_DIR = config.DATA_DIR / "generated"
VOICEOVER_WORD_LIMIT = 15


@dataclass
class ScriptResult:
    artifact_dir: str
    passed: bool
    failures: list[str]


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def validate_script(artifact_dir: Path) -> ScriptResult:
    failures: list[str] = []

    script_p   = artifact_dir / "script.json"
    manifest_p = artifact_dir / "manifest.json"

    if not script_p.exists():
        return ScriptResult(artifact_dir.name, False, ["script.json missing"])

    try:
        script = json.loads(script_p.read_text())
    except Exception as e:
        return ScriptResult(artifact_dir.name, False, [f"script.json parse error: {e}"])

    # hook
    hook = script.get("hook", "")
    if not hook or not hook.strip():
        failures.append("hook is empty")

    # scenes
    scenes = script.get("scenes", [])
    if not scenes:
        failures.append("no scenes found")

    # scene count vs manifest
    if manifest_p.exists():
        try:
            manifest = json.loads(manifest_p.read_text())
            expected_n = manifest.get("n_scenes")
            if expected_n and len(scenes) != expected_n:
                failures.append(f"scene count {len(scenes)} ≠ manifest n_scenes {expected_n}")
        except Exception:
            pass

    # per-scene checks
    for i, scene in enumerate(scenes, 1):
        vo = scene.get("voiceover_en", "")
        if not vo.strip():
            failures.append(f"scene {i}: voiceover_en empty")
        else:
            wc = _word_count(vo)
            if wc > VOICEOVER_WORD_LIMIT:
                failures.append(f"scene {i}: voiceover_en {wc} words > {VOICEOVER_WORD_LIMIT} limit: {vo[:60]!r}")

        cids = scene.get("grounded_chunk_ids", [])
        if not cids:
            failures.append(f"scene {i}: grounded_chunk_ids empty")

    # disclaimer
    if not script.get("disclaimer", "").strip():
        failures.append("disclaimer missing or empty")

    return ScriptResult(artifact_dir.name, len(failures) == 0, failures)


def run(verbose: bool = True) -> list[ScriptResult]:
    results = []
    if not GENERATED_DIR.exists():
        if verbose:
            print("  (no data/generated/ directory found)")
        return results

    dirs = sorted(d for d in GENERATED_DIR.iterdir()
                  if d.is_dir() and (d / "script.json").exists())
    if not dirs:
        if verbose:
            print("  (no script.json files found in data/generated/)")
        return results

    for d in dirs:
        r = validate_script(d)
        results.append(r)
        if verbose:
            status = "✓ PASS" if r.passed else "✗ FAIL"
            print(f"  STRUCTURAL  {r.artifact_dir:<45} {status}")
            for f in r.failures:
                print(f"              ⚠ {f}")
    return results


if __name__ == "__main__":
    import sys
    results = run()
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} structural checks passed")
    sys.exit(1 if failed else 0)
