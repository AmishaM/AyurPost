# AyurPost — Final Specification

## Problem Definition

Small Ayurvedic clinics in India lack the time and expertise to maintain a consistent, credible social media presence. AyurPost automates the creation of short-form video reels (Instagram/YouTube) grounded exclusively in classical Ayurvedic texts, reducing content production from hours to minutes while keeping claims authentic and clinician-reviewed.

---

## Data Processing

**Source texts:** Sushruta Samhita (3 vols), Charaka Samhita (4 vols), Ashtanga Hridayam (1 vol) — all scanned PDFs with no clean digital editions available.

**OCR:** Google Document AI (Document OCR processor) applied to 15-page PDF chunks (API inline limit); output stitched per volume. Stray symbols, running page headers, and OCR garbles (e.g. `CHAPTER XIL` → 59, `VL` → 45) cleaned deterministically via round-trip Roman numeral validation before chunking.

### Chunking Strategy

Each source text has a distinct OCR structure requiring a tailored segmentation approach:

| Book | Chapter format | Structural challenge | Strategy |
|---|---|---|---|
| **Sushruta Samhita** (3 vols) | `CHAPTER I.` (Roman) with sthana running headers | Chapter numbers restart in each sthana (Sutrasthana ch1 ≠ Chikitsasthana ch1) | 3-signal segmentation: open header + closing colophon + page running header; sthana-aware dedup resets per sthana boundary |
| **Charaka Samhita** (4 vols) | Mixed Arabic/Roman: `CHAPTER 1-title`, `CHAPTER II` | OCR joins (`CHAPTERI`), digit substitutions (`CHAPTER 1V` for IV); multi-sthana per volume; duplicate sthana headers in back-matter | Running header change detection for sthana transitions; one-way transition guard prevents re-entry after back-matter false triggers |
| **Ashtanga Hridayam** (1 vol) | `CHAPTER N` (Arabic, clean) | No sthana markers; 30 chapters appear late in file (lines 19507+); first 19K lines are dense commentary | Simple body-start detection; no sthana needed |

All three use **LlamaIndex SentenceSplitter** at a 1,024-token target with 128-token overlap after chapter text is extracted and noise-cleaned.

### Chunk Metadata

Each chunk carries two layers of metadata written back to `all_chunks.jsonl`:

**Structural** (from chunker):
```python
{
    "chunk_id":     "sushruta-vol1-sutrasthana-ch06-002",  # stable, unique
    "source":       "sushruta-vol1-sutrasthana",
    "chapter":      6,
    "sthana":       "Sutrasthana",      # sthana label, empty for Ashtanga
    "chunk_index":  2,                  # position within chapter
    "section_role": "topic",            # topic | verse_summary | prose
    "n_tokens":     312,                # approximate word count
    "text":         "...",
}
```

**Semantic** (from `tagger.py` — deterministic gazetteer, no LLM):
```python
{
    "doshas_mentioned":   ["vata"],           # HARD retrieval filter
    "dosha_combinations": ["vata-pitta"],     # tridosha / dual-dosha patterns
    "herbs":              ["triphala"],       # 45 herbs tracked
    "diseases":           ["prameha"],        # 23 disease terms tracked
}
```

Gazetteer matching uses word-boundary exact match with optional trailing `s`, NFKD diacritic normalisation, and canonical key storage (surface forms like `meha` → canonical `prameha`). `doshas_mentioned` is the **hard filter** applied at Qdrant query time — pitta chunks are never retrieved for a vata-season reel.

**Stack:** Google Document AI · LlamaIndex SentenceSplitter · Voyage AI `voyage-4-large` (dense, 1024-dim) · FastEmbed BM25 (sparse) · Qdrant Cloud (hybrid index, 4,785 points)

---

## Retrieval

Three complementary signals are required:

| Signal | Why |
|---|---|
| **Dense (Voyage)** | Semantic match across paraphrase and translation variation in 8 volumes |
| **BM25 sparse** | Exact Sanskrit term recall (`agni`, `triphala`, specific roga names that dense embeddings may miss) |
| **Dosha hard-filter** | Prevents pitta content leaking into vata-season reels; enforced at query time via Qdrant payload index |

RRF (Reciprocal Rank Fusion) merges dense + sparse scores; dosha filter is then applied as a post-filter on the fused result.

---

## System Design

### Diagram 1 — Knowledge Base Ingestion

```mermaid
flowchart LR
    A[📄 Scanned PDFs\nSushruta · Charaka · Ashtanga]:::source -->|Split into 15-page chunks\nDocument AI inline limit| B[🔍 Google Document AI\nDocument OCR processor]:::ocr
    B -->|Stitch per volume| C[📝 OCR Text Files\n8 volumes]:::ocr
    C -->|Sthana-aware chapter segmentation\nRoman numeral round-trip validation| D[⚙️ chunker.py / chunk_book.py\nSentenceSplitter 1024 tok]:::process
    D -->|4785 chunks with structural metadata| E[🗃️ all_chunks.jsonl]:::store
    E -->|Deterministic gazetteer\nword-boundary exact match| F[🏷️ tagger.py\ndosha · herb · disease tags]:::process
    F -->|Batch embed\n20 texts per request| G[🚀 Voyage AI\nvoyage-4-large dense]:::embed
    F -->|Sparse token weights| H[📊 FastEmbed BM25\nsparse vectors]:::embed
    G & H -->|Upsert 200 points/batch\n33MB payload limit| I[(🔷 Qdrant Cloud\nayurvedic_kb_v3\nhybrid collection)]:::vector
    I -->|Enable O1 filter speed\nfor dosha hard-filter| J[🔑 Payload indexes\nchunk_id · dosha · herb · disease]:::vector

    classDef source fill:#f3e8ff,stroke:#9333ea,color:#4c1d95
    classDef ocr fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef process fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef embed fill:#ffe4e6,stroke:#e11d48,color:#881337
    classDef vector fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
```

### Diagram 2 — Content Generation & Review

```mermaid
flowchart LR
    subgraph Schedule["📅 Schedule"]
        A[📋 Roadmap YAMLs\nSeasonal · Dosha · Services]:::yaml -->|Date → slot mapping\n20-slot cycle| B[🗓️ calendar_engine.py\nDosha cursor + season picker]:::sched
    end

    subgraph Generation["⚙️ Generation"]
        B -->|Pillar + topic descriptor| C[🔧 generate.py\ngenerate_dosha.py\ngenerate_services.py]:::gen
        C -->|Query + dosha filter| D[🔍 HybridRetriever\nRRF fusion · dosha hard-filter]:::retrieval
        D -->|Top-6 grounding chunks| E[🧠 Claude Opus 4.8\nStructured ReelScript\n3 scenes · MAX 15 words each]:::llm
        E -->|Image prompt per scene| F[🎬 Veo 3.1 Lite\nHyper-realistic 9:16 · 8s clips]:::media
        E -->|Voiceover text per scene| G[🎙️ Chirp3-HD TTS\nen-IN · phonetic substitutions]:::media
        F & G -->|xfade + music bed\nsmooth fade-out| H[🎞️ FFmpeg assembly]:::media
        H -->|DRAFT — pending sign-off| I[📦 reel.mp4]:::artifact
        I -->|Per-scene groundedness\n+ compliance check| J[✅ audit.py\nSonnet 4.6 structured judge]:::audit
    end

    subgraph Review["👩‍⚕️ Doctor Review"]
        I -->|GCS volume mount| K[🖥️ Streamlit App\nCloud Run · GCS]:::app
        K -->|Tiles by generation date| L[📆 Monthly calendar\ninline video player]:::app
        L --> M{Edit?}:::decision
        M -- Yes -->|Free-text prompt| N[💬 Doctor feedback\nin chat panel]:::feedback
        N -->|Inject feedback into Opus\nkeep existing Veo clips| O[🔄 New script + TTS\nreassemble reel]:::gen
        O --> K
        M -- No --> P[✅ Ready to publish]:::publish
    end

    classDef yaml fill:#f3e8ff,stroke:#9333ea,color:#4c1d95
    classDef sched fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef gen fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef retrieval fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef llm fill:#ffe4e6,stroke:#e11d48,color:#881337
    classDef media fill:#fce7f3,stroke:#db2777,color:#831843
    classDef artifact fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef audit fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef app fill:#ecfdf5,stroke:#059669,color:#064e3b
    classDef feedback fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef decision fill:#fff7ed,stroke:#ea580c,color:#7c2d12
    classDef publish fill:#dcfce7,stroke:#16a34a,color:#14532d
```

---

## Evals

**Task-specific (LLM-as-judge):** `audit.py` uses Claude Sonnet 4.6 as a structured judge after every generation. Checks: (1) groundedness — each scene's voiceover must be supported by its cited `chunk_id` passages; (2) compliance — no cure/reversal/quantified-outcome language (India Drugs & Magic Remedies Act / ASCI standards). Hook scene (scene 0) is exempt from groundedness; compliance applies to all scenes. Output: `AuditReport` with per-scene `SceneVerdict` and `overall_pass` boolean written to `audit_report.json`.

**Error handling:** Veo high-load (code 8) handled via skip-existing-clips logic; Voyage batch token limits managed with 20-text cap + 0.15 word-count margin (Sanskrit BPE expansion up to 7×); Qdrant upserts batched at 200 points to stay under 33 MB payload limit; OCR garbled Roman numerals rejected via round-trip validation (`VL→45` fails because `to_roman(45)="XLV"≠"VL"`).

**Cost (per reel):** ~$0.40 Veo (4 clips × 8s × $0.05/s) + ~$0.03 Opus script + ~$0.001 Voyage embeddings + ~$0.01 TTS. Total ≈ **$0.44/reel**.

**Latency:** Script generation ~30s (Opus + adaptive thinking) · Veo clip generation ~90–120s each (parallel-safe) · TTS ~5s · FFmpeg assembly ~10s. End-to-end ≈ **4–6 min** per reel. Voiceover-only edits (no new Veo) ≈ **45s**.
