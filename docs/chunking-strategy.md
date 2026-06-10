# AyurPost — Chunking Strategy

## Source Texts

| Book | Volumes | OCR Tool | Chapter Format |
|---|---|---|---|
| Sushruta Samhita | 3 | Google Document AI | `CHAPTER <Roman>.` with sthana running headers |
| Charaka Samhita | 4 | Google Document AI | Mixed Arabic/Roman: `CHAPTER 1-title`, `CHAPTER II` |
| Ashtanga Hridayam | 1 | Google Document AI | `CHAPTER N` (Arabic) |

All PDFs are scanned images; no clean digital editions exist. OCR run in 15-page chunks (Document AI inline limit), stitched per volume.

---

## Chunk Schema

```python
{
    "chunk_id":           "sushruta-vol1-sutrasthana-ch06-002",  # unique, stable
    "source":             "sushruta-vol1-sutrasthana",
    "chapter":            6,
    "sthana":             "Sutrasthana",           # sthana label (Charaka/Sushruta)
    "chunk_index":        2,                        # position within chapter
    "section_role":       "topic" | "verse_summary" | "prose",
    "n_tokens":           312,                      # approximate word count
    "text":               "...",
    # gazetteer-filled:
    "doshas_mentioned":   ["vata"],
    "dosha_combinations": ["vata-pitta"],
    "herbs":              ["triphala", "ashwagandha"],
    "diseases":           ["prameha"],
}
```

---

## Segmentation Approach

### Sthana Detection (Sushruta vol2/3, Charaka all vols)

Each volume contains multiple sthanas with **restarting chapter numbers**. Running page headers (e.g., `SUTRASTHANA`, `CHIKITSASTHANAM`) identify the current sthana. Sthana transitions reset the chapter-number dedup set so chapter 1 of each sthana is captured independently.

Key rules:
- Once a sthana is left, it cannot be re-entered (prevents back-matter/index false triggers)
- Canonical sthana label normalised from OCR variants (`CHIKITSA-STHAHA`, `CHIKITSASTHANAM` → `Chikitsasthana`)

### Chapter Detection

Roman numeral parser with **round-trip validation** — `VL` → 45 would fail (`to_roman(45) = "XLV" ≠ "VL"`) and is rejected as an OCR garble. Handles:
- Pure Roman: `CHAPTER IX`
- Arabic: `CHAPTER 1`, `CHAPTER 10`  
- OCR substitutions: `CHAPTER 1V` → normalised to `CHAPTER IV`
- No-space joins: `CHAPTERI` → matched via `\s*` in regex

### Text Splitting

`LlamaIndex SentenceSplitter` at **1,024-token target** with 128-token overlap. Chapter text is cleaned before splitting:
- Noise lines removed (page numbers, stray Roman digits, running headers)
- Leading chapter header tokens stripped

### Metadata Tagging

Deterministic gazetteer pass (`tagger.py`) — exact surface-form match against a controlled vocabulary of ~45 herbs, 3 doshas, 23 diseases. Word-boundary match with optional trailing `s`. Canonical key stored (not surface form). **`doshas_mentioned` is the hard retrieval filter** in Qdrant.

---

## Output

| Source | Chunks |
|---|---|
| Sushruta vol1-3 | 836 |
| Charaka vol1-4 | 3,795 |
| Ashtanga Hridayam | 154 |
| **Total** | **4,785** |

~42% of chunks have no gazetteer tags (general sutras / theory sections) — these are still retrievable via dense + BM25 search, just not dosha-filtered.

---

## OCR Error Patterns Fixed

| Pattern | Example | Fix |
|---|---|---|
| Period → `L` | `CHAPTER VL` (= VI.), `[Chap. LIL` (= LIII.) | Round-trip Roman validation |
| Digit → Roman | `CHAPTER 1V` (= IV) | Pre-normalise `1V→IV`, `1X→IX` |
| No space | `CHAPTERI` | `\s*` in regex |
| Sthana misspell | `CHIKITSA-STHAHA` | Fuzzy substring match on sthana key |
| Duplicate sthana | Siddhisthana header in back-matter | One-way sthana transition guard |
