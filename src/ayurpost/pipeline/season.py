"""
Deterministic month -> season lookup for the seasonal content pillar.

Reads roadmap/seasonal_bengaluru.yaml and returns the season dict whose `months`
list contains the given date's month. This is a pure CODE pass (Rule 6): the
calendar month picks the season, no LLM and no live weather. The yaml's
`weather_signature` (OpenWeatherMap confirm/shift) is intentionally NOT consulted
here for the sample reel — that lives in a later roadmap-engine pass.

For Bengaluru, June (6) -> monsoon (SW Monsoon, aggravated_dosha: [vata]).

The mapping is PROVISIONAL (doctor_reviewed: false in the yaml); reels built from
it are drafts pending clinician sign-off.

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.season            # today
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.season 2026-06-09 # a date
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml

_SEASONAL_YAML = Path(__file__).parent.parent / "roadmap" / "seasonal_bengaluru.yaml"


def _load_seasons() -> list[dict]:
    if not _SEASONAL_YAML.exists():
        raise FileNotFoundError(f"seasonal map not found: {_SEASONAL_YAML}")
    data = yaml.safe_load(_SEASONAL_YAML.read_text(encoding="utf-8"))
    seasons = data.get("seasons")
    if not seasons:
        raise ValueError(f"no `seasons` list in {_SEASONAL_YAML}")
    return seasons


def select_season(on: date | None = None) -> dict:
    """Return the season dict whose `months` contains `on`'s month (default today).

    Fails loud if the month maps to zero or to more than one season — both mean the
    yaml's `months` coverage is broken and the sample must not silently pick one."""
    on = on or date.today()
    matches = [s for s in _load_seasons() if on.month in s.get("months", [])]
    if len(matches) != 1:
        keys = [s.get("key") for s in matches]
        raise ValueError(
            f"month {on.month} maps to {len(matches)} seasons ({keys}); "
            f"expected exactly 1 — fix `months` coverage in {_SEASONAL_YAML.name}")
    return matches[0]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    on = date.fromisoformat(arg) if arg else date.today()
    try:
        season = select_season(on)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"{on.isoformat()}  ->  {season['key']} ({season['label']})")
    print(f"  aggravated_dosha: {season['aggravated_dosha']}")
    print(f"  ailments: {season['ailments']}")
    print(f"  remedy_angle: {season['remedy_angle']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
