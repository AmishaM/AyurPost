"""
AyurPost posting calendar engine.

Reads posting_calendar.yaml to determine the current content slot in the 4-week
macro-cycle, resolves the specific topic (dosha + sub-type / season / service),
and either reports it or fires the appropriate reel generator.

State (dosha cursor + cycle start) is persisted in data/calendar_state.json.

Cycle: 20 weekday posts per 4-week cycle
  slots  0-9  → dosha_education (10 posts, cursor-based)
  slots 10-14 → seasonal        (5 posts, current season)
  slots 15-19 → clinic_services (5 posts, round-robin publishable)

Usage:
    # What would be generated today?
    PYTHONPATH=src .venv/bin/python -m ayurpost.roadmap.calendar_engine

    # Preview next N weekday posts
    PYTHONPATH=src .venv/bin/python -m ayurpost.roadmap.calendar_engine --preview 20

    # Generate today's reel (fires the pillar-specific CLI)
    PYTHONPATH=src .venv/bin/python -m ayurpost.roadmap.calendar_engine --generate \\
        --model claude-opus-4-8

    # Reset cycle start to today (use when starting a new cycle)
    PYTHONPATH=src .venv/bin/python -m ayurpost.roadmap.calendar_engine --reset-cycle
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from ayurpost import config
from ayurpost.pipeline.season import select_season

_CAL_YAML    = Path(__file__).parent / "posting_calendar.yaml"
_DOSHA_YAML  = Path(__file__).parent / "dosha_education.yaml"
_SVC_YAML    = Path(__file__).parent / "clinic_services.yaml"
_STATE_FILE  = config.DATA_DIR / "calendar_state.json"

DOSHA_ORDER  = ["vata", "pitta", "kapha"]
TOPIC_ORDER  = ["prakriti", "personality", "ailment", "seasonal", "remedy"]
CYCLE_POSTS  = 20          # weekday posts per 4-week cycle
DOSHA_POSTS  = 10          # slots 0-9
SEASONAL_POSTS = 5         # slots 10-14
SERVICE_POSTS  = 5         # slots 15-19


# ── Helpers ──────────────────────────────────────────────────────────────────

def _weekdays_since(start: date, end: date) -> int:
    """Count weekdays (Mon–Fri) from start (inclusive) to end (exclusive)."""
    if end <= start:
        return 0
    count, cur = 0, start
    while cur < end:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def _next_weekday(d: date) -> date:
    """Return d if it is a weekday, else the next Monday."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _load_publishable_services() -> list[dict]:
    data = yaml.safe_load(_SVC_YAML.read_text(encoding="utf-8"))
    return [s for s in data["services"] if s.get("publishable", False)]


def _load_dosha_label(dosha_key: str, topic_type: str) -> str:
    data = yaml.safe_load(_DOSHA_YAML.read_text(encoding="utf-8"))
    for d in data["doshas"]:
        if d["key"] == dosha_key:
            for t in d["topics"]:
                if t["topic_type"] == topic_type:
                    return t["label"]
    return f"{dosha_key} — {topic_type}"


# ── State management ─────────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        "cycle_start": date.today().isoformat(),
        "dosha_cursor": 0,   # absolute count of dosha posts ever generated
    }


def _load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    return _default_state()


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Core: resolve topic for a given post slot ────────────────────────────────

def resolve_topic(post_in_cycle: int, dosha_cursor: int,
                  on: date) -> dict:
    """Given a 0-based slot in the 20-post cycle, return the topic descriptor."""
    if post_in_cycle < DOSHA_POSTS:
        # Dosha education — advance cursor by post position within block
        abs_pos   = (dosha_cursor + post_in_cycle) % 15
        dosha_key = DOSHA_ORDER[abs_pos // 5]
        topic_type = TOPIC_ORDER[abs_pos % 5]
        return {
            "pillar":     "dosha_education",
            "dosha_key":  dosha_key,
            "topic_type": topic_type,
            "label":      _load_dosha_label(dosha_key, topic_type),
            "slot":       post_in_cycle,
        }
    elif post_in_cycle < DOSHA_POSTS + SEASONAL_POSTS:
        season = select_season(on)
        return {
            "pillar":      "seasonal",
            "season_key":  season["key"],
            "label":       f"{season['label']} seasonal — {', '.join(season['aggravated_dosha'])} aggravation",
            "slot":        post_in_cycle,
        }
    else:
        services = _load_publishable_services()
        idx = (post_in_cycle - DOSHA_POSTS - SEASONAL_POSTS) % len(services)
        svc = services[idx]
        if len(services) < SERVICE_POSTS:
            note = (f"  ⚠ only {len(services)} publishable services for {SERVICE_POSTS} slots "
                    f"— repeating after index {len(services)-1}")
        else:
            note = None
        return {
            "pillar":      "clinic_services",
            "service_key": svc["key"],
            "label":       svc["label"],
            "slot":        post_in_cycle,
            "note":        note,
        }


def get_topic_for_date(on: date, state: dict) -> dict | None:
    """Return topic for a specific date, or None if it is not a posting day."""
    on = _next_weekday(on)
    if on.weekday() >= 5:
        return None
    cycle_start    = date.fromisoformat(state["cycle_start"])
    posts_elapsed  = _weekdays_since(cycle_start, on)
    post_in_cycle  = posts_elapsed % CYCLE_POSTS
    topic = resolve_topic(post_in_cycle, state["dosha_cursor"], on)
    topic["date"] = on.isoformat()
    topic["cycle_post"] = post_in_cycle + 1   # 1-indexed for display
    return topic


# ── Generator dispatch ───────────────────────────────────────────────────────

def _run_generator(topic: dict, model: str, music: str | None) -> int:
    """Fire the appropriate reel generator for the topic. Returns exit code."""
    base = ["python", "-m"]
    music_args = ["--music", music] if music else []

    if topic["pillar"] == "dosha_education":
        cmd = [
            *base, "ayurpost.pipeline.generate_dosha",
            "--dosha-key", topic["dosha_key"],
            "--topic-type", topic["topic_type"],
            "--model", model,
            *music_args,
        ]
    elif topic["pillar"] == "seasonal":
        cmd = [
            *base, "ayurpost.pipeline.generate",
            "--season-key", topic["season_key"],
            "--model", model,
            *music_args,
        ]
    elif topic["pillar"] == "clinic_services":
        cmd = [
            *base, "ayurpost.pipeline.generate_services",
            "--service-key", topic["service_key"],
            "--model", model,
            *music_args,
        ]
    else:
        print(f"ERROR: unknown pillar {topic['pillar']!r}", file=sys.stderr)
        return 1

    print(f"running: PYTHONPATH=src {' '.join(cmd)}")
    result = subprocess.run(["python", "-m", "ayurpost.pipeline.generate_dosha"] if False
                            else cmd,
                            env={**__import__("os").environ, "PYTHONPATH": "src"})
    return result.returncode


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_topic(topic: dict, verbose: bool = True) -> None:
    date_str = topic.get("date", "?")
    slot     = topic.get("cycle_post", "?")
    print(f"  {date_str}  [cycle slot {slot:>2}/20]  {topic['pillar']:<20}  {topic['label']}")
    if verbose and topic.get("note"):
        print(f"  {topic['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="ISO date to query (default today)")
    parser.add_argument("--preview", type=int, metavar="N",
                        help="show the next N posting days")
    parser.add_argument("--generate", action="store_true",
                        help="generate today's reel (fires the pillar CLI)")
    parser.add_argument("--advance", action="store_true",
                        help="advance dosha cursor by 1 (call after generating a dosha post)")
    parser.add_argument("--reset-cycle", action="store_true",
                        help="reset cycle_start to today")
    parser.add_argument("--model", default="claude-opus-4-8",
                        help="generation model (used with --generate)")
    parser.add_argument("--music", help="music file path (used with --generate)")
    args = parser.parse_args()

    state = _load_state()

    if args.reset_cycle:
        state["cycle_start"] = date.today().isoformat()
        _save_state(state)
        print(f"cycle reset — new start: {state['cycle_start']}")
        return 0

    if args.advance:
        state["dosha_cursor"] = (state["dosha_cursor"] + 1) % 15
        _save_state(state)
        print(f"dosha cursor advanced to {state['dosha_cursor']} "
              f"({DOSHA_ORDER[state['dosha_cursor']//5]} — "
              f"{TOPIC_ORDER[state['dosha_cursor']%5]})")
        return 0

    on = date.fromisoformat(args.date) if args.date else date.today()
    on = _next_weekday(on)

    if args.preview:
        print(f"Next {args.preview} posting days from {on.isoformat()}:")
        print(f"  cycle_start={state['cycle_start']}  dosha_cursor={state['dosha_cursor']}")
        print()
        cur = on
        shown = 0
        while shown < args.preview:
            if cur.weekday() < 5:
                topic = get_topic_for_date(cur, state)
                _print_topic(topic, verbose=False)
                shown += 1
            cur += timedelta(days=1)
        return 0

    # Default: show today's topic
    topic = get_topic_for_date(on, state)
    print(f"Today ({on.isoformat()}) — cycle_start={state['cycle_start']}")
    print()
    _print_topic(topic)
    print()
    print(f"  pillar:  {topic['pillar']}")
    for k, v in topic.items():
        if k not in ("pillar", "label", "date", "slot", "cycle_post", "note"):
            print(f"  {k}: {v}")

    if args.generate:
        print("\ngenerating reel...")
        rc = _run_generator(topic, args.model, args.music)
        if rc == 0 and topic["pillar"] == "dosha_education":
            state["dosha_cursor"] = (state["dosha_cursor"] + 1) % 15
            _save_state(state)
            print(f"dosha cursor advanced to {state['dosha_cursor']}")
        return rc

    return 0


if __name__ == "__main__":
    sys.exit(main())
