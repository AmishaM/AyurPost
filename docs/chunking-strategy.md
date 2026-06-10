# AyurPost — Per-Book Chunking Strategy

> Derived from sampling and pattern analysis of all 8 source PDFs.
> Each book has a distinct structure; chunking strategy adapts per source.

## Summary of source structures

| Book | OCR source | English quality | Sanskrit quality | Primary structural markers |
|------|-----------|-----------------|------------------|---------------------------|
| Charaka Vol 1 | Adobe Paper Capture | Good | Garbled | `[ N ]` verse end, `(N)` list items, `CHAPTER N` |
| Charaka Vol 2 | Adobe Paper Capture | Good | Garbled | `[ N ]` verse end, `[ N-M ]` ranges, `CHAPTER N` |
| Charaka Vol 3 | Adobe Paper Capture | Good | N/A (commentary) | `N. M-K` verse-refs, `CHAPTER N` |
| Charaka Vol 4 | Adobe Paper Capture | Good | N/A (commentary) | `N. M-K` verse-refs, `CHAPTER N` |
| Ashtanga Hridayam | ABBYY FineReader | Good | Garbled | `X ADHYAYA` chapter, `Topic:` colon-terminated section headers |
| Sushruta Vol 1 | Google Document AI | Excellent | Clean | `Chap. N`, `. N.` verse end, `STHANAM` |
| Sushruta Vol 2 | Google Document AI | Excellent | Clean | Same as Vol 1 |
| Sushruta Vol 3 | Google Document AI | Excellent | Clean | `Chap. N`, `. N.` verse end |

## Critical realization: Charaka Vol 3 & 4 are different

P.V. Sharma's Charaka Samhita is a 4-volume set where:
- **Vol 1 & 2** = Original text (Sanskrit shloka + English translation, interleaved)
- **Vol 3 & 4** = Sharma's English commentary on the verses (referencing them by `chapter.verse-range`)

This means:
- Vol 1 & 2: chunk on `[N]` verse boundaries
- Vol 3 & 4: chunk on commentary section boundaries (which usually align with verse-range references like `4. 30-32`)

Both produce useful KB content — original gives canonical claims, commentary gives clarifications and clinical context.

---

## Common metadata schema (all chunks)

```python
{
    "chunk_id": "charaka_v1_sutra_ch6_verses18-20",   # unique
    "source": "Charaka Samhita" | "Ashtanga Hridayam" | "Sushruta Samhita",
    "volume": 1-4 | None,
    "sthana": "Sutra" | "Nidana" | "Vimana" | "Sharira" | "Chikitsa" |
              "Kalpa" | "Siddhi" | "Indriya" | "Uttara" | None,
    "chapter_num": 6,
    "chapter_title": "Tasyasitiya (On one's diet etc.)",
    "verse_range": "18-20" | "commentary on 4.30-32" | "section: Kala (Time)",
    "page_in_pdf": 124,                # for traceability back to PDF
    "text": "...",                     # the chunk's English text content
    "text_sanskrit_raw": "..." | None, # garbled Sanskrit if any (kept for completeness)

    # Enriched topical metadata — TWO provenance classes (see note below)
    # -------------------------------------------------------------------
    # (a) Haiku LLM pass — the genuine judgment call:
    # topic_types: 1–3 content categories, ORDERED BY DOMINANCE (index 0 = primary).
    #              Soft retrieval filter — boost by position, do NOT hard-gate.
    "topic_types": ["seasonal", "remedy"],    # list, max 3, most-central topic first
    #   each value ∈ the 11-enum:
    #   "prakriti"      # body constitution identification (build, skin, digestion, sleep)
    #   "personality"   # mental/emotional qualities of a dosha type
    #   "vikriti"       # current imbalance state vs. baseline constitution
    #   "dual_dosha"    # Vata-Pitta, Pitta-Kapha, Vata-Kapha combinations
    #   "age_dosha"     # age-phase dominance (Kapha childhood, Pitta adult, Vata elder)
    #   "ailment"       # what goes wrong when a dosha aggravates
    #   "seasonal"      # seasonal vulnerability and ritucharya guidance
    #   "remedy"        # treatment / pacification approach
    #   "procedure"     # clinical procedure (Panchakarma, Shirodhara, Abhyanga etc.)
    #   "herb"          # herb / food / oil properties
    #   "education"     # general Ayurvedic theory (doshas, dhatus, agni etc.)
    "topic_tags": ["ritucharya", "summer", "pitta"],   # 2–6 free-form keywords

    # (b) Deterministic gazetteer pass (CODE, not LLM) — exact-term extraction:
    "doshas_mentioned": ["pitta"],            # all doshas referenced — HARD filter
    "prakriti_types": ["vata-pitta"],         # dual-dosha types mentioned, if any
    "age_phase": ["adult"] | [],              # childhood | adult | elder, if mentioned
    "treatments_mentioned": ["abhyanga"],
    "herbs_mentioned": ["coconut", "sandalwood"],
    "diseases_mentioned": ["heatstroke"],
    "procedures_mentioned": [],
}
```

The enriched fields are filled in **two passes**:
- **(a) Deterministic gazetteer pass (code, not LLM):** matches a controlled Ayurvedic vocabulary to fill the entity fields (`doshas_mentioned`, `herbs_mentioned`, `diseases_mentioned`, `treatments_mentioned`, `procedures_mentioned`, `prakriti_types`, `age_phase`). These are exact-term lookups, so code does them more reliably and cheaply than an LLM — and the **hard** retrieval filter rides on `doshas_mentioned`.
- **(b) Claude Haiku 4.5 LLM pass (~$0.50 total):** fills only the genuine judgment fields — `topic_types` (constrained to the 11-enum via strict structured output; max 3; ordered by dominance) and `topic_tags`. `topic_types` is used as a **soft** filter (boost by list position, not a hard gate).

Together they enable metadata-filtered retrieval and exact-term BM25 matching.

### Why topic_type matters for the roadmap engine

The roadmap engine uses `topic_types` to construct targeted queries per content day. A "Dosha education" week for Vata is not one reel — it's a 5-day sub-cycle, each day pulling a different `topic_types` membership filter (a chunk matches if the value is anywhere in its list, so a multi-topic chunk surfaces on every day it belongs to):

| Day | topic_types filter | Reel angle |
|-----|-------------------|------------|
| 1 | `prakriti` + `doshas_mentioned: vata` | How to identify if you are Vata dominant (body frame, skin, digestion, sleep) |
| 2 | `personality` + `doshas_mentioned: vata` | Vata personality — creative, anxious, quick-thinking, irregular |
| 3 | `ailment` + `doshas_mentioned: vata` | Common Vata ailments — joint pain, insomnia, bloating, dry skin |
| 4 | `seasonal` + `doshas_mentioned: vata` | When Vata flares — monsoon and late autumn vulnerability |
| 5 | `remedy` + `doshas_mentioned: vata` | How to pacify Vata — warm oil massage, warm foods, routine |

Additional dosha education angles supported by the schema:

| topic_types value | Reel angle |
|------------|------------|
| `dual_dosha` | "Are you Vata-Pitta? Here's what that means for your health" |
| `age_dosha` | "Why children get more Kapha colds, adults get more Pitta stress, elders get more Vata joint pain" |
| `vikriti` | "Your Prakriti is fixed — but your current imbalance (Vikriti) can be corrected" |

---

## Per-book chunking strategy

### Charaka Vol 1 & Vol 2 (original text)

**Chunk boundary**: each `[ N ]` or `[ N-M ]` verse marker.

**Algorithm**:
1. Extract text page-by-page using PyMuPDF
2. Detect chapter headers (`CHAPTER\s+[IVX]+`)
3. Drop garbled Sanskrit lines (heuristic: line with >50% non-ASCII chars), keep English
4. Drop page headers/footers (running header like "SUTRASTHANA 49")
5. Split on `[ N ]` or `[ N-M ]` markers — each preceding block is a verse chunk
6. **Coalesce**: if a chunk is < 100 words, merge with next; if > 600 words, leave (rare)

**Expected output**: ~600-800 chunks per volume, avg ~150-300 words each.

### Charaka Vol 3 & Vol 4 (commentary)

**Chunk boundary**: each commentary block keyed by verse reference `N. M-K`.

**Algorithm**:
1. Extract text page-by-page  
2. Detect chapter headers
3. Split on commentary references (`^\d+\.\s+\d+(-\d+)?\.`)
4. Each split = one commentary chunk, with metadata `commentary_on_verses: "X.M-K"`
5. Drop garbled footnotes (lines with mostly garbled chars)

**Expected output**: ~400-500 chunks per volume.

### Ashtanga Hridayam (Vidyanath translation)

**Chunk boundary**: colon-terminated topic headers (e.g., `Kala (Time):`).

**Algorithm**:
1. Extract text page-by-page
2. Detect `X ADHYAYA` chapter markers to track chapter
3. Detect topic headers via regex `^([A-Z][^:\n]{3,60}):\s*$`
4. Each topic header opens a chunk; chunk continues until next topic header or chapter end
5. Drop garbled Sanskrit lines
6. **Split oversize chunks**: if topic section > 800 words, split on bullet-points (`*` or `•`)

**Expected output**: ~400-600 chunks.

**Why this beats verse-based**: Vidyanath's edition uses topic-based sections, not numbered verses as the primary navigation. The colon-terminated headers are the natural unit.

### Sushruta Vol 1, 2, 3 (Bhishagratna, Document AI OCR)

**Chunk boundary**: each `. N.` or `. N-M.` verse marker.

**Algorithm**:
1. Use the per-chunk OCR `.txt` files we already generated (preserves page boundaries)
2. Detect chapter headers (`Chap\.\s*[IVXLDCM]+` or `CHAPTER\s+[IVXLDCM]+`)
3. Detect sthana headers (`SUTRASTHANAM`, `NIDANA STHANAM`, `CHIKITSA STHANAM`, etc.)
4. Split on `. N.` verse markers — each preceding paragraph + verse marker = one chunk
5. **Preserve footnotes** as separate chunks tagged `type: editor_footnote` — they have valuable clinical context (e.g., the syphilis comparison on the Upadansa page)
6. **No Sanskrit cleanup needed** — the Document AI OCR kept Ayurvedic terms clean

**Expected output**: ~800-1,200 chunks per volume (Sushruta is the most chunk-dense).

---

## Pipeline order

1. **Build extractors** — one per book type (Charaka original, Charaka commentary, Ashtanga, Sushruta). Output: list of `Chunk` dicts.
2. **Run extractors on all 8 books** → ~3,000-5,000 chunks total in `data/chunks/all_chunks.jsonl`
3. **Quick validation pass** — sample 20 random chunks per book, eyeball them, fix extractors if needed
4. **Metadata enrichment** — two passes: **(a)** deterministic gazetteer (code) fills the entity fields (`doshas_mentioned`, `herbs_mentioned`, etc.); **(b)** Claude Haiku 4.5 LLM pass fills `topic_types` (strict enum, max 3, dominance-ordered) + `topic_tags` (~$0.50, ~10 min)
5. **Embed** — Voyage AI `voyage-4-large` (1024-dim, 32K input window — no chunk splitting required), ingest into Qdrant Cloud with hybrid search index (BM25 + dense)
6. **Test retrieval** — 5 manual queries, verify top-3 results are sensible

---

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Verse marker regex misses edge cases (OCR errors, missing brackets) | Validation pass after extraction; flag pages where 0 markers found |
| Chunks too small (< 50 words) for meaningful embedding | Coalesce step in algorithm; small chunks merge into next |
| Chunks too large (> 800 words) exceed context | Topic-split fallback for oversize chunks |
| Footnotes vs body text confusion in Charaka Vol 3/4 | Sanskrit-heavy lines are footnotes — heuristically drop or tag |
| Ashtanga Hridayam topic headers not always cleanly formatted | Fallback: split on chapter, then split on word-count (500-800) |
| Cross-volume chapter numbering collisions (each volume restarts at I) | Always namespace `chunk_id` with `<source>_v<N>_<sthana>_ch<M>` |
