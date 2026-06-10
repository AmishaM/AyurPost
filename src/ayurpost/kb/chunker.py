"""
Chunk the OCR'd Sushruta Samhita into retrieval units for the RAG KB.

Strategy (measured against the raw OCR — see docs/chunking-strategy.md):

  1. Segment a volume into chapters using BOTH boundary signals, because
     each has OCR gaps the other covers:
       - opening headers  "CHAPTER <roman>"
       - closing colophons "Thus ends the <ordinal> chapter"
     (e.g. vol1's opening "XVI" is missing but its closing "sixteenth" is
     present; the opening dup "XI" is covered by closings "eleventh"+"twelfth".)
     A chapter that can be resolved from neither is reported loudly, not dropped.

  2. Clean OCR noise: running page headers, page numbers, margin artifacts,
     line-wrap hyphenation, scan-library stamps.

  3. Hybrid split inside a chapter. Break before the structural markers that
     the measurement showed are reliable in this volume (Authoritative verses /
     Metrical texts / Additional texts / "Now we shall discuss"). Marker-bounded
     spans get a 1200-token cap (one coherent topic); plain prose gets 800.
     Sentence-aware (never mid-sentence), no overlap, <100-token tails merged.

  4. Vol2/vol3 sthana segmentation. These volumes contain multiple sthanas
     (major sections) whose chapter numbering restarts at 1. A sthana pass
     first splits the text by detecting sthana-name headers immediately before
     each "CHAPTER I", then the chapter segmentation runs within each sthana.

Token counts use the tiktoken cl100k tokenizer as a sizing PROXY — Voyage's own
tokenizer differs slightly, but this is only used to bound chunk size.

Usage:
    .venv/bin/python -m ayurpost.kb.chunker --volume 1 --dry-run
    .venv/bin/python -m ayurpost.kb.chunker --volume 1
    .venv/bin/python -m ayurpost.kb.chunker --volume 2 --append
    .venv/bin/python -m ayurpost.kb.chunker --volume 3 --append

Output:
    data/chunks/all_chunks.jsonl   (one JSON object per line)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import tiktoken
from llama_index.core.node_parser import SentenceSplitter

from ayurpost import config

# ── sizing ────────────────────────────────────────────────────────────────
PROSE_CAP = 800       # token cap for plain prose spans
TOPIC_CAP = 1200      # token cap for marker-bounded (coherent-topic) spans
MERGE_MIN = 100       # tail chunks smaller than this are merged into the previous

_enc = tiktoken.get_encoding("cl100k_base")
_tok = _enc.encode
def _tlen(s: str) -> int:
    return len(_enc.encode(s))

# ── reliable structural markers (vol1, data-backed) ─────────────────────────
# "Authoritative" is frequently OCR'd "A u thoritative" / "thoritative"; the
# clean step normalises that before these patterns run.
_VERSE = r"Authoritative verses?|Metrical texts?|Additional texts?"
_OPENER = r"Now we shall (?:discuss|discourse|describe)"
_MARKER_BREAK = re.compile(rf"(?i)(?=(?:{_VERSE}|{_OPENER}))")
_IS_VERSE = re.compile(rf"(?i)^(?:{_VERSE})")
_IS_OPENER = re.compile(rf"(?i)^(?:{_OPENER})")

# ── chapter-boundary detection ──────────────────────────────────────────────
# Case-SENSITIVE "CHAPTER": every real header is upper-case, so this rejects
# lower-case mid-sentence "chapter ..." lines whose letters reduce to a roman.
_OPEN_HDR = re.compile(r"^[•\s]*CHAPTER\s+(.+)$")
_CLOSE = re.compile(r"(?i)thus ends?\s+the\s+([a-z][a-z\- ]+?)\s+chapter")
# Per-page running header "... [Chap. XVI." — a third boundary signal that is
# present on every page, used only to backfill a boundary the exact markers lost.
_RUN_HDR = re.compile(r"\[\s*Chap\.\s*([IVXLC].*)", re.I)


def _roman_to_int(s: str) -> int | None:
    """Parse roman numerals from an OCR'd header token (strips accents + noise)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^IVXLC]", "", s.upper().split(".")[0])
    if not s:
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    if not total:
        return None
    # Round-trip validation: reject OCR garbles like VL→45, VIIL→43 where
    # a period was misread as L. Canonical Roman must equal the input string.
    def _to_roman(n: int) -> str:
        r = ""
        for v, sym in [(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),
                       (5,"V"),(4,"IV"),(1,"I")]:
            while n >= v:
                r += sym; n -= v
        return r
    return total if _to_roman(total) == s else None


def _build_ordinals() -> dict[str, int]:
    ones = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
            6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}
    teens = {10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth",
             14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth",
             18: "eighteenth", 19: "nineteenth"}
    tens = {20: ("twentieth", "twenty"), 30: ("thirtieth", "thirty"),
            40: ("fortieth", "forty")}
    m: dict[str, int] = {}
    for n, w in {**ones, **teens}.items():
        m[w] = n
    for base, (whole, prefix) in tens.items():
        m[whole] = base
        for u, uw in ones.items():
            m[f"{prefix}-{uw}"] = base + u
    return m


_ORDINALS = _build_ordinals()


def _ordinal_to_int(phrase: str) -> int | None:
    p = re.sub(r"\bthe\b", " ", phrase.lower())
    p = re.sub(r"[^a-z\-]", " ", p)
    p = re.sub(r"\s+", " ", p).strip().replace(" ", "-").strip("-")
    return _ORDINALS.get(p)


def _find_body_start(lines: list[str]) -> int:
    """Body begins at the LAST 'CHAPTER I' (front-matter + table-of-contents
    copies come first; the body's chapter 1 is the final occurrence)."""
    firsts = [i for i, ln in enumerate(lines)
              if (m := _OPEN_HDR.match(ln)) and _roman_to_int(m.group(1)) == 1]
    return firsts[-1] if firsts else 0


def _detect_openings(lines: list[str], body_start: int) -> dict[int, int]:
    opens: dict[int, int] = {}
    for i in range(body_start, len(lines)):
        m = _OPEN_HDR.match(lines[i])
        if not m:
            continue
        n = _roman_to_int(m.group(1))
        if n and 1 <= n <= 60 and n not in opens:   # first occurrence wins (drops dup)
            opens[n] = i
    return opens


def _detect_closings(lines: list[str], body_start: int) -> dict[int, int]:
    closes: dict[int, int] = {}
    for i in range(body_start, len(lines)):
        window = lines[i] if i + 1 >= len(lines) else lines[i] + " " + lines[i + 1]
        m = _CLOSE.search(window)
        if not m:
            continue
        n = _ordinal_to_int(m.group(1))
        if n and n not in closes:
            closes[n] = i
    return closes


def _detect_running_headers(lines: list[str], body_start: int) -> dict[int, int]:
    """First body line of each chapter's page run, from "[Chap. <roman>" headers.
    Page-granular (≈ one page of slop), so used only as a last-resort fallback."""
    runs: dict[int, int] = {}
    for i in range(body_start, len(lines)):
        m = _RUN_HDR.search(lines[i])
        if not m:
            continue
        n = _roman_to_int(m.group(1))
        if n and 1 <= n <= 60 and n not in runs:   # first (earliest) page wins
            runs[n] = i
    return runs


def segment_chapters(lines: list[str]) -> tuple[list[tuple[int, int, int]], list[str]]:
    """Resolve each chapter 1..N to (num, start_line, end_line).

    Precedence per boundary (exact signals first, page-granular fallback last):
      start: opening header > previous chapter's closing colophon +1 > running header
      end:   closing colophon > next opening header -1 > next running header -1
    A chapter resolvable from none is skipped and reported; a chapter resolved
    only via the page-granular running header is kept but flagged as approximate.
    Both cases go to warnings (fail loud, never silently drop or fake precision).
    """
    body_start = _find_body_start(lines)
    opens = _detect_openings(lines, body_start)
    closes = _detect_closings(lines, body_start)
    runs = _detect_running_headers(lines, body_start)
    if not (closes or opens or runs):
        return [], ["no chapter-boundary signals found — cannot segment"]

    n_max = max([*closes, *opens, *runs])
    chapters, warnings = [], []
    for n in range(1, n_max + 1):
        start, s_exact = opens.get(n), True
        if start is None:
            prev_close = closes.get(n - 1)
            if prev_close is not None:
                start = prev_close + 1
            else:
                start, s_exact = runs.get(n), False

        end, e_exact = closes.get(n), True
        if end is None:
            nxt_open = opens.get(n + 1)
            if nxt_open is not None:
                end = nxt_open - 1
            else:
                nxt_run = runs.get(n + 1)
                end, e_exact = (nxt_run - 1 if nxt_run is not None else None), False
        # If still unresolved, look two steps ahead (handles OCR-lost chapter header
        # between N and N+2: content of N and N+1 merged under N, N+1 skipped).
        if end is None:
            nxt_close = closes.get(n + 1)
            if nxt_close is not None:
                end, e_exact = nxt_close, False
            else:
                nxt2_open = opens.get(n + 2)
                if nxt2_open is not None:
                    end, e_exact = nxt2_open - 1, False
        # Last-resort: use end of sthana for the final chapter if all signals absent.
        if end is None and n == n_max:
            end, e_exact = len(lines) - 1, False

        if start is None or end is None or start >= end:
            warnings.append(f"chapter {n}: unresolved (open={opens.get(n)}, "
                            f"close={closes.get(n)}, run={runs.get(n)}) — skipped")
            continue
        if not (s_exact and e_exact):
            warnings.append(f"chapter {n}: boundary approximated from page running-"
                            f"headers (±1 page) — exact markers lost to OCR")
        chapters.append((n, start, end))
    return chapters, warnings


# ── cleaning ────────────────────────────────────────────────────────────────
def _is_noise(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    if re.fullmatch(r"[\d\s.,|\[\]ivxlcdIVXLCD•]+", s):          # page nums / stray roman / pipes
        return True
    if re.search(r"\bChap\.", s, re.I):                          # running header "Chap. II."
        return True
    if re.search(r"SUSHRUTA|SUTRASTH|SAM.?IT", s, re.I):         # garbled title / colophon "Samhita"
        return True
    if re.search(r"(?i)thus ends?\s+the", s):                    # closing colophon (structure, not content)
        return True
    if re.match(r"^[•\s]*CHAPTER\s+[IVXLC][IVXLC.,\sЀ-ӿ]*$", s):  # bare/garbled header line
        return True
    if re.search(r"LIBRARY|DIRECTOR OF|UNIVERSITY OF", s):       # scan-library stamp
        return True
    if len(re.sub(r"[^A-Za-z]", "", s)) <= 2:                    # stray margin letters
        return True
    return False


def _clean(lines: list[str]) -> str:
    kept = [ln.strip() for ln in lines if not _is_noise(ln)]
    text = " ".join(kept)
    text = re.sub(r"([a-zà-ÿ])-\s+([a-zà-ÿ])", r"\1\2", text)    # rejoin line-wrap hyphens (lower-case continuation -> one word)
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)               # close space in a wrapped hyphenated compound, keep the hyphen
    text = re.sub(r":\s*-+(?=\s*\S)", ":—", text)                # ":-" / ":--" running into text -> ":—"
    text = re.sub(r"(?<=\w)\s*--\s*(?=\w)", "—", text)           # "--" between words -> em dash
    text = re.sub(r"[*†‡‹›•✓\\|§¶]", " ", text)                  # drop footnote/scan noise glyphs (no legitimate use in this text)
    text = re.sub(r"(?i)(?:a\s*u\s*)?thoritative verse", "Authoritative verse", text)
    text = re.sub(r"^CHAPTER\s+[IVXLC ]+[.,]?\s*", "", text, flags=re.I)  # drop leading hdr token
    text = re.sub(r"\s+", " ", text).strip().lstrip(". ")
    return text


# ── chunking ────────────────────────────────────────────────────────────────
def _role(seg: str) -> str:
    if _IS_VERSE.match(seg):
        return "verse_summary"
    if _IS_OPENER.match(seg):
        return "topic"
    return "prose"


def chunk_chapter(text: str, *, source: str, chapter: int) -> list[dict]:
    text = _MARKER_BREAK.sub("\n\n", text)
    segs = [s.strip() for s in text.split("\n\n") if s.strip()]

    raw: list[tuple[str, str]] = []
    for seg in segs:
        role = _role(seg)
        cap = PROSE_CAP if role == "prose" else TOPIC_CAP
        if _tlen(seg) <= cap:
            parts = [seg]
        else:
            parts = SentenceSplitter(chunk_size=cap, chunk_overlap=0,
                                     tokenizer=_tok).split_text(seg)
        raw.extend((p, role) for p in parts)

    # Merge sub-MERGE_MIN fragments into a neighbour: backward when there is a
    # previous chunk, otherwise forward (so a tiny chapter-opener title rides
    # with the content that follows it instead of stranding alone).
    merged: list[tuple[str, str]] = []
    pending = ""
    for txt, role in raw:
        if pending:
            txt, role, pending = f"{pending} {txt}", _role(f"{pending} {txt}"), ""
        if merged and _tlen(txt) < MERGE_MIN:
            merged[-1] = (merged[-1][0] + " " + txt, merged[-1][1])
        elif not merged and _tlen(txt) < MERGE_MIN:
            pending = txt
        else:
            merged.append((txt, role))
    if pending:                                  # whole chapter was one tiny fragment
        merged.append((pending, _role(pending)))

    return [
        {
            "chunk_id": f"{source}-ch{chapter:02d}-{idx:03d}",
            "source": source,
            "chapter": chapter,
            "chunk_index": idx,
            "section_role": role,
            "n_tokens": _tlen(txt),
            "text": txt,
        }
        for idx, (txt, role) in enumerate(merged)
    ]


def chunk_volume(path: Path, source: str) -> tuple[list[dict], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    chapters, warnings = segment_chapters(lines)
    chunks: list[dict] = []
    for n, start, end in chapters:
        text = _clean(lines[start:end + 1])
        if text:
            chunks.extend(chunk_chapter(text, source=source, chapter=n))
    return chunks, warnings


def chunk_volume_multisthana(path: Path, vol: int) -> tuple[list[dict], list[str]]:
    """Chunk a multi-sthana volume (vol2/vol3): sthana pass then chapter pass."""
    lines = path.read_text(encoding="utf-8").splitlines()
    sthanas, all_warnings = segment_sthanas(lines)
    if not sthanas:
        return [], all_warnings

    all_chunks: list[dict] = []
    for sthana_key, _, sthana_start, sthana_end in sthanas:
        source = f"sushruta-vol{vol}-{sthana_key}"
        label = _STHANA_LABELS.get(sthana_key, sthana_key)
        sthana_lines = lines[sthana_start: sthana_end + 1]
        chapters, warnings = segment_chapters(sthana_lines)
        all_warnings.extend(f"[{label}] {w}" for w in warnings)
        print(f"  {label}: {len(chapters)} chapters", end="")
        sthana_chunks: list[dict] = []
        for n, start, end in chapters:
            text = _clean(sthana_lines[start: end + 1])
            if text:
                sthana_chunks.extend(chunk_chapter(text, source=source, chapter=n))
        print(f" → {len(sthana_chunks)} chunks")
        all_chunks.extend(sthana_chunks)

    return all_chunks, all_warnings


# ── CLI ─────────────────────────────────────────────────────────────────────
_VOLUMES = {1: "sushruta-vol1-sutrasthana"}   # vol2/vol3 need sthana segmentation first


# ── Sthana segmentation (vol2/vol3) ─────────────────────────────────────────
# Each sthana section opens with a sthana-name header immediately before
# "CHAPTER I". Running page headers (e.g. "NIDANA STHANAM.") look similar but
# appear on every page; we distinguish them by proximity to "CHAPTER I".

# Maps a canonical sthana key to its OCR pattern variants (case-insensitive).
_STHANA_PATTERNS: dict[str, list[str]] = {
    "nidanasthana":    ["nidana", "nidána", "nibana"],
    "sharirasthana":   ["sharira", "sarira", "sárira", "s'arira"],
    "chikitsasthana":  ["chikitsita", "chikitsa", "chikitsite"],
    "kalpasthana":     ["kalpa", "kalpasthana", "kalpasthána"],
    "uttaratantra":    ["uttara-tantaram", "uttara-tantra", "uttara tantra", "uttaratantra"],
}

_STHANA_LABELS: dict[str, str] = {
    "nidanasthana":   "Nidana Sthana",
    "sharirasthana":  "Sharira Sthana",
    "chikitsasthana": "Chikitsita Sthana",
    "kalpasthana":    "Kalpa Sthana",
    "uttaratantra":   "Uttara Tantra",
}

_STHANA_RX: dict[str, re.Pattern] = {
    key: re.compile("|".join(re.escape(p) for p in pats), re.I)
    for key, pats in _STHANA_PATTERNS.items()
}


def _identify_sthana(lines: list[str], line_idx: int, lookback: int = 10) -> str | None:
    """Given the line index of 'CHAPTER I', look back up to `lookback` lines
    for a sthana-name header. Returns the canonical sthana key or None."""
    window = lines[max(0, line_idx - lookback): line_idx]
    # Scan backwards — closest match wins (most specific context)
    for ln in reversed(window):
        stripped = ln.strip()
        if not stripped:
            continue
        for key, rx in _STHANA_RX.items():
            if rx.search(stripped):
                return key
    return None


def segment_sthanas(lines: list[str]) -> tuple[list[tuple[str, str, int, int]], list[str]]:
    """Split a multi-sthana volume into (sthana_key, source, start_line, end_line) slices.

    Detects each sthana by finding body 'CHAPTER I' headers preceded by a
    sthana-name header within 10 lines. Table-of-contents 'CHAPTER I' entries
    (preceded by 'End of the Contents of...') are skipped.

    Returns (sthanas_list, warnings). Fails loud if no sthanas found.
    """
    warnings: list[str] = []
    sthana_starts: list[tuple[str, int]] = []   # (sthana_key, line_idx_of_CHAPTER_I)

    for i, ln in enumerate(lines):
        if not _OPEN_HDR.match(ln.strip()):
            continue
        # Only care about "CHAPTER I" (roman numeral 1)
        m = _OPEN_HDR.match(ln.strip())
        if not m or _roman_to_int(m.group(1)) != 1:
            continue
        # Skip table-of-contents Chapter I (preceded by "End of the Contents")
        ctx_back = " ".join(lines[max(0, i - 8): i]).lower()
        if "end of the content" in ctx_back or "contents of" in ctx_back:
            continue
        sthana_key = _identify_sthana(lines, i)
        if sthana_key is None:
            warnings.append(f"line {i + 1}: 'CHAPTER I' found but no sthana header "
                            f"detected in preceding {10} lines — skipped")
            continue
        sthana_starts.append((sthana_key, i))

    if not sthana_starts:
        return [], ["no sthana boundaries detected — check OCR file"]

    # Dedup by key: keep the LAST occurrence of each sthana (earlier ones are
    # table-of-contents references, not actual body starts).
    seen: dict[str, int] = {}
    for key, start in sthana_starts:
        seen[key] = start          # later start overwrites earlier
    sthana_starts = sorted(seen.items(), key=lambda x: x[1])

    # Build (key, source, start, end) slices
    sthanas: list[tuple[str, str, int, int]] = []
    for idx, (key, start) in enumerate(sthana_starts):
        end = sthana_starts[idx + 1][1] - 1 if idx + 1 < len(sthana_starts) else len(lines) - 1
        sthanas.append((key, key, start, end))

    return sthanas, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--volume", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the per-chapter summary, don't write the jsonl")
    parser.add_argument("--append", action="store_true",
                        help="append to existing all_chunks.jsonl (use for vol2/vol3)")
    args = parser.parse_args()

    ocr_path = config.OCR_DIR / f"sushruta-samhita-vol{args.volume}.txt"
    if not ocr_path.exists():
        print(f"ERROR: OCR file not found: {ocr_path}", file=sys.stderr)
        return 1

    if args.volume == 1:
        source = "sushruta-vol1-sutrasthana"
        chunks, warnings = chunk_volume(ocr_path, source)
    else:
        print(f"vol{args.volume}: running sthana segmentation...")
        chunks, warnings = chunk_volume_multisthana(ocr_path, args.volume)

    # ── validation summary ──
    roles: dict[str, int] = {}
    for c in chunks:
        roles[c["section_role"]] = roles.get(c["section_role"], 0) + 1
    print(f"\ntotal chunks: {len(chunks)}")
    print(f"by role: {roles}")
    toks = [c["n_tokens"] for c in chunks]
    if toks:
        print(f"token sizes: min={min(toks)} max={max(toks)} "
              f"mean={sum(toks) // len(toks)}")
    if args.volume == 1:
        by_ch: dict[int, list[dict]] = {}
        for c in chunks:
            by_ch.setdefault(c["chapter"], []).append(c)
        print("\nper-chapter chunk counts:")
        for n in sorted(by_ch):
            cs = by_ch[n]
            print(f"  ch{n:02d}: {len(cs):2d} chunks  ({sum(x['n_tokens'] for x in cs):5d} tok)")
    if warnings:
        print(f"\n⚠️  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("\nno warnings — all chapters resolved cleanly.")

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
    print(f"\n{action} {len(chunks)} chunks -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
