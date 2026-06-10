"""
Chunker for Charaka Samhita and Ashtanga Hridayam.

Handles the mixed Arabic/Roman chapter format and running-header sthana labels
that differ from the Sushruta chunker. No sthana segmentation — chapters only.

Usage:
    # dry-run (check chapter detection, no file written)
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.chunk_book \\
        --book charaka-samhita --volume 1 --dry-run

    # write (first volume — creates all_chunks.jsonl or appends)
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.chunk_book \\
        --book charaka-samhita --volume 1

    # append subsequent volumes / books
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.chunk_book \\
        --book charaka-samhita --volume 2 --append
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.chunk_book \\
        --book ashtanga-hridayam --append

Output:
    data/chunks/all_chunks.jsonl  (same format as Sushruta chunks)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from llama_index.core.node_parser import SentenceSplitter

from ayurpost import config

# ── Constants ─────────────────────────────────────────────────────────────────

CHUNK_SIZE   = 1024   # tokens
CHUNK_OVERLAP = 128

# Regex: "CHAPTER" (optionally no space) then Arabic or Roman digits
# Handles: CHAPTER 1, CHAPTER II, CHAPTER IX, CHAPTERI, CHAPTER 1V
_CH_HDR = re.compile(
    r"^CHAPTER\s*([IVXLC\d]{1,6})\s*[-.\s]",
    re.IGNORECASE,
)
# Also matches "CHAPTER I" at end of line (no trailing separator)
_CH_HDR_EOL = re.compile(r"^CHAPTER\s*([IVXLC\d]{1,6})\s*$", re.IGNORECASE)

# Running header / noise patterns
_STHANA_NAMES = [
    "sutrasthana", "sutrasth", "nidanasthana", "nidana sthana",
    "vimanasthana", "vimana sthana", "shareerasthana", "sharirasthana",
    "indriyasthana", "chikitsasthana", "chikitsa-sthana", "chikitsasthanam",
    "kalpasthana", "kalpasthanam", "siddhisthana", "siddhisthanam",
    "uttarasthana", "uttaratantra",
]
_STHANA_RX = re.compile("|".join(re.escape(s) for s in _STHANA_NAMES), re.I)

# Sthana canonical labels
_STHANA_LABELS = {
    "sutra": "Sutrasthana", "nidana": "Nidanasthana",
    "vimana": "Vimanasthana", "sharira": "Shareerasthana",
    "indriya": "Indriyasthana", "chikitsa": "Chikitsasthana",
    "kalpa": "Kalpasthana", "siddhi": "Siddhisthana",
    "uttara": "Uttarasthana",
}

# ── Roman / Arabic parsing ────────────────────────────────────────────────────

_ROMAN_VALS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

def _ocr_fix(s: str) -> str:
    """Fix common OCR substitutions: 1→I at start of Roman, 0→O, etc."""
    # leading digit 1 before V/X/I/L/C → treat as I
    s = re.sub(r"^1([VXILC])", r"I\1", s, flags=re.I)
    # IV misread as 1V
    s = re.sub(r"1V", "IV", s, flags=re.I)
    # IX misread as 1X
    s = re.sub(r"1X", "IX", s, flags=re.I)
    return s.upper()


def _roman_to_int(s: str) -> int | None:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^IVXLC\d]", "", s.upper())
    if not s:
        return None
    # pure Arabic
    if s.isdigit():
        n = int(s)
        return n if 1 <= n <= 200 else None
    # apply OCR fix then validate Roman round-trip
    s = _ocr_fix(s)
    s = re.sub(r"\d", "", s)   # strip any remaining digits
    if not s:
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_VALS.get(ch, 0)
        if not v:
            return None
        total += v if v >= prev else -v
        prev = max(prev, v)
    if not total or total > 200:
        return None
    # round-trip check
    def to_roman(n: int) -> str:
        r = ""
        for val, sym in [(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),
                         (9,"IX"),(5,"V"),(4,"IV"),(1,"I")]:
            while n >= val:
                r += sym; n -= val
        return r
    return total if to_roman(total) == s else None


def _parse_chapter_num(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        n = int(raw)
        return n if 1 <= n <= 200 else None
    return _roman_to_int(raw)


# ── Noise / running-header detection ─────────────────────────────────────────

def _is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    # pure page numbers / stray roman / pipes
    if re.fullmatch(r"[\d\s.,|\[\]ivxlcdIVXLCD•]+", s):
        return True
    # sthana running header (standalone line)
    if _STHANA_RX.fullmatch(s.rstrip(".")):
        return True
    # very short garble lines
    if len(s) < 4 and not s[0].isalpha():
        return True
    return False


def _sthana_hint(line: str) -> str | None:
    """Return canonical sthana label if this line is a sthana running header."""
    s = line.strip().lower().rstrip(".")
    if not _STHANA_RX.search(s):
        return None
    for key, label in _STHANA_LABELS.items():
        if key in s:
            return label
    return None


# ── Chapter segmentation ──────────────────────────────────────────────────────

def segment_chapters(lines: list[str]) -> tuple[list[tuple[int, int, int, str]], list[str]]:
    """Return [(chapter_num, start_line, end_line, sthana), ...] + warnings.

    Tracks sthana running-header changes to reset chapter numbering per sthana.
    This handles Charaka-style texts where chapter numbers restart in each sthana.
    """
    # find body start = first CHAPTER line
    body_start = 0
    for i, ln in enumerate(lines):
        if _CH_HDR.match(ln.strip()) or _CH_HDR_EOL.match(ln.strip()):
            body_start = i
            break

    # First pass: collect (line_idx, chapter_num, sthana) events
    # A new sthana resets the per-sthana chapter-number dedup set.
    # Once a sthana is left, it cannot be re-entered (handles OCR back-matter).
    events: list[tuple[int, int, str]] = []   # (line_idx, chapter_num, sthana)
    current_sthana = "sutrasthana"             # default for start of volume
    seen_in_sthana: set[int] = set()
    completed_sthanas: set[str] = set()        # sthanas we've already left

    for i in range(body_start, len(lines)):
        ln = lines[i].strip()
        hint = _sthana_hint(ln)
        if hint:
            canonical = hint.lower().replace(" ", "")
            if canonical != current_sthana and canonical not in completed_sthanas:
                completed_sthanas.add(current_sthana)
                current_sthana = canonical
                seen_in_sthana = set()
            continue
        m = _CH_HDR.match(ln) or _CH_HDR_EOL.match(ln)
        if not m:
            continue
        n = _parse_chapter_num(m.group(1))
        if n is None or n in seen_in_sthana:
            continue
        seen_in_sthana.add(n)
        events.append((i, n, current_sthana))

    if not events:
        return [], ["no chapter headers found"]

    # Second pass: assign boundaries
    warnings: list[str] = []
    chapters: list[tuple[int, int, int, str]] = []

    for idx, (start, n, sthana) in enumerate(events):
        end = events[idx + 1][0] - 1 if idx + 1 < len(events) else len(lines) - 1
        if start >= end:
            warnings.append(f"[{sthana}] chapter {n}: zero-length range — skipped")
            continue
        chapters.append((n, start, end, sthana))

    return chapters, warnings


# ── Chunking ──────────────────────────────────────────────────────────────────

_splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)


def _clean(text: str) -> str:
    # strip noise lines
    lines = [ln for ln in text.splitlines() if not _is_noise(ln)]
    # collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    # drop leading CHAPTER header token
    text = re.sub(r"^CHAPTER\s*[IVXLC\d]+\s*[-–.]?\s*", "", text,
                  flags=re.I | re.MULTILINE)
    return text.strip()


def chunk_chapter_text(text: str, *, source: str, chapter: int,
                       sthana: str) -> list[dict]:
    text = _clean(text)
    if not text:
        return []
    parts = _splitter.split_text(text)
    chunks = []
    for idx, t in enumerate(parts):
        t = t.strip()
        if not t:
            continue
        n_tok = len(t.split())
        role = ("verse_summary" if re.search(r"\d+[-–]\d+\s*$", t[:200])
                else "prose" if n_tok < 120 else "topic")
        chunk_id = f"{source}-{sthana}-ch{chapter:02d}-{idx:03d}" if sthana else f"{source}-ch{chapter:02d}-{idx:03d}"
        chunks.append({
            "chunk_id":    chunk_id,
            "source":      source,
            "chapter":     chapter,
            "sthana":      sthana,
            "chunk_index": idx,
            "section_role": role,
            "n_tokens":    n_tok,
            "text":        t,
            "doshas_mentioned":   [],
            "dosha_combinations": [],
            "herbs":              [],
            "diseases":           [],
        })
    return chunks


def chunk_volume(path: Path, source: str) -> tuple[list[dict], list[str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    chapters, warnings = segment_chapters(lines)
    all_chunks: list[dict] = []
    for n, start, end, sthana in chapters:
        text = "\n".join(lines[start: end + 1])
        all_chunks.extend(chunk_chapter_text(text, source=source,
                                             chapter=n, sthana=sthana))
    return all_chunks, warnings


# ── CLI ───────────────────────────────────────────────────────────────────────

_BOOKS = {
    "charaka-samhita": {
        "volumes": [1, 2, 3, 4],
        "ocr_name": lambda v: f"charaka-samhita-vol{v}.txt",
        "source":   lambda v: f"charaka-samhita-vol{v}",
    },
    "ashtanga-hridayam": {
        "volumes": [None],
        "ocr_name": lambda v: "ashtanga-hridayam-vol1.txt",
        "source":   lambda v: "ashtanga-hridayam",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--book", required=True, choices=list(_BOOKS))
    parser.add_argument("--volume", type=int, default=None,
                        help="Volume number (charaka only). Omit for ashtanga.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing all_chunks.jsonl")
    args = parser.parse_args()

    book = _BOOKS[args.book]
    vol = args.volume

    if args.book == "charaka-samhita" and vol not in book["volumes"]:
        print(f"ERROR: --volume must be one of {book['volumes']}", file=sys.stderr)
        return 1

    ocr_file = config.OCR_DIR / book["ocr_name"](vol)
    source   = book["source"](vol)

    if not ocr_file.exists():
        print(f"ERROR: OCR file not found: {ocr_file}", file=sys.stderr)
        return 1

    print(f"book:   {args.book}  vol={vol}")
    print(f"source: {source}")
    print(f"ocr:    {ocr_file}  ({ocr_file.stat().st_size // 1024}KB)")

    chunks, warnings = chunk_volume(ocr_file, source)

    # summary
    roles: dict[str, int] = {}
    for c in chunks:
        roles[c["section_role"]] = roles.get(c["section_role"], 0) + 1
    by_ch: dict[int, list[dict]] = {}
    for c in chunks:
        by_ch.setdefault(c["chapter"], []).append(c)
    print(f"\ntotal chunks: {len(chunks)}  chapters: {len(by_ch)}")
    print(f"by role: {roles}")
    toks = [c["n_tokens"] for c in chunks]
    if toks:
        print(f"token sizes: min={min(toks)} max={max(toks)} mean={sum(toks)//len(toks)}")
    if warnings:
        print(f"\n⚠️  {len(warnings)} warning(s):")
        for w in warnings[:20]:
            print(f"  - {w}")
    else:
        print("\nno warnings.")

    if args.dry_run:
        print("\n[dry-run] no file written.")
        return 0

    config.CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CHUNKS_DIR / "all_chunks.jsonl"
    mode = "a" if args.append else "w"
    with out.open(mode, encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    action = "appended" if args.append else "wrote"
    print(f"\n{action} {len(chunks)} chunks → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
