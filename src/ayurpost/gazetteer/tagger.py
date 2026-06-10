"""
Tag each KB chunk with controlled-vocabulary entities from the gazetteer.

This is the deterministic CODE pass (Rule 6): a term lookup, not a judgment call.
It reads the gazetteer's surface forms, matches them against each chunk's text,
and writes four metadata fields back into all_chunks.jsonl:

    doshas_mentioned     canonical doshas present      (the HARD retrieval filter)
    dosha_combinations   multi-dosha conditions        vata-pitta | ... | tridosha
    herbs                medicinal substances present  (soft signal)
    diseases             roga present                  (soft signal)

Matching contract (honors gazetteer.yaml's header):
  - normalize text AND terms: NFKD diacritic strip + lowercase  (vāta -> vata)
  - word-boundary match of each listed surface form, with an OPTIONAL trailing
    's' (English plural: vrana -> vrana/vranas) — applied uniformly to all categories
  - NO wildcard stemming: derived stems (vataja, pittaja) match only because the
    gazetteer lists them explicitly
  - the canonical KEY is stored (meha -> "prameha", amalaki -> "amalaka"), not the
    surface form that happened to match

dosha_combinations extraction (per gazetteer.yaml's rule):
  1. explicit pair token        -> that pair
  2. tridosha-classifier token  -> "tridosha"
  3. dual-classifier token      -> the pair resolved from doshas_mentioned, but
                                   ONLY when exactly 2 doshas are mentioned

Idempotent: overwrites only these four fields, preserves everything else in each
record, so it can be re-run after the chunker regenerates all_chunks.jsonl.

Usage:
    PYTHONPATH=src .venv/bin/python -m ayurpost.gazetteer.tagger --dry-run
    PYTHONPATH=src .venv/bin/python -m ayurpost.gazetteer.tagger
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import yaml

from ayurpost import config

_GAZETTEER = Path(__file__).with_name("gazetteer.yaml")
_DOSHA_ORDER = ("vata", "pitta", "kapha")   # canonical order used to name pairs


def _fold(s: str) -> str:
    """NFKD diacritic strip + lowercase. Hyphens/spaces are preserved — the
    gazetteer enumerates both hyphenated and concatenated forms explicitly."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _compile(forms: list[str]) -> re.Pattern:
    """One regex matching any folded surface form on word boundaries, with an
    optional trailing plural 's'. Alternation backtracks, so listing both a stem
    and its longer variant (kapha, kapham) is safe regardless of order."""
    alts = "|".join(re.escape(_fold(f)) for f in forms)
    return re.compile(rf"\b(?:{alts})s?\b")


class Tagger:
    def __init__(self, gz: dict):
        self.dosha_rx = {c: _compile(f) for c, f in gz["doshas"].items()}
        self.herb_rx = {c: _compile(f) for c, f in gz["herbs"].items()}
        self.disease_rx = {c: _compile(f) for c, f in gz["diseases"].items()}
        combos = gz["dosha_combinations"]
        self.pair_rx = {p: _compile(f) for p, f in combos["pairs"].items()}
        self.dual_rx = _compile(combos["dual_classifiers"])
        self.tri_rx = _compile(combos["tridosha_classifiers"])

    def tag(self, text: str) -> dict:
        f = _fold(text)
        doshas = [c for c in _DOSHA_ORDER if self.dosha_rx[c].search(f)]

        combos: set[str] = {p for p, rx in self.pair_rx.items() if rx.search(f)}
        if self.tri_rx.search(f):
            combos.add("tridosha")
        if self.dual_rx.search(f) and len(doshas) == 2:
            combos.add("-".join(doshas))   # doshas are in canonical order -> "vata-pitta"

        return {
            "doshas_mentioned": doshas,
            "dosha_combinations": sorted(combos),
            "herbs": sorted(c for c, rx in self.herb_rx.items() if rx.search(f)),
            "diseases": sorted(c for c, rx in self.disease_rx.items() if rx.search(f)),
        }


def _summary(records: list[dict], tagger: Tagger) -> None:
    n = len(records)
    dosha_ct, combo_ct, herb_ct, disease_ct = Counter(), Counter(), Counter(), Counter()
    untagged = 0
    for r in records:
        for d in r["doshas_mentioned"]:
            dosha_ct[d] += 1
        for c in r["dosha_combinations"]:
            combo_ct[c] += 1
        for h in r["herbs"]:
            herb_ct[h] += 1
        for d in r["diseases"]:
            disease_ct[d] += 1
        if not (r["doshas_mentioned"] or r["dosha_combinations"]
                or r["herbs"] or r["diseases"]):
            untagged += 1

    print(f"chunks: {n}")
    print(f"\ndoshas_mentioned  (chunks with >=1: {sum(1 for r in records if r['doshas_mentioned'])})")
    for d in _DOSHA_ORDER:
        print(f"  {d:6}: {dosha_ct[d]}")
    print(f"\ndosha_combinations:")
    for c, k in combo_ct.most_common():
        print(f"  {c:12}: {k}")
    if not combo_ct:
        print("  (none)")

    print(f"\nherbs  (chunks with >=1: {sum(1 for r in records if r['herbs'])}, "
          f"distinct used: {len(herb_ct)}/{len(tagger.herb_rx)})")
    for h, k in herb_ct.most_common(12):
        print(f"  {h:11}: {k}")
    unused_h = sorted(set(tagger.herb_rx) - set(herb_ct))
    if unused_h:
        print(f"  unused (0 chunks): {', '.join(unused_h)}")

    print(f"\ndiseases  (chunks with >=1: {sum(1 for r in records if r['diseases'])}, "
          f"distinct used: {len(disease_ct)}/{len(tagger.disease_rx)})")
    for d, k in disease_ct.most_common(12):
        print(f"  {d:11}: {k}")
    unused_d = sorted(set(tagger.disease_rx) - set(disease_ct))
    if unused_d:
        print(f"  unused (0 chunks): {', '.join(unused_d)}")

    print(f"\nchunks with NO tags at all: {untagged}  ({untagged / n:.0%})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the tag distribution, don't write the jsonl")
    args = parser.parse_args()

    chunks_path = config.CHUNKS_DIR / "all_chunks.jsonl"
    if not chunks_path.exists():
        print(f"ERROR: chunks file not found: {chunks_path}", file=sys.stderr)
        return 1

    gz = yaml.safe_load(_GAZETTEER.read_text(encoding="utf-8"))
    tagger = Tagger(gz)

    records = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines()]
    for r in records:
        r.update(tagger.tag(r["text"]))

    _summary(records, tagger)

    if args.dry_run:
        print("\n[dry-run] no file written.")
        return 0

    with chunks_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote tags into {len(records)} chunks -> {chunks_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
