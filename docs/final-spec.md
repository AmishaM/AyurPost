# AyurPost — Final Specification

## Problem Definition

Small Ayurvedic clinics in India lack the time and expertise to maintain a consistent, credible social media presence. AyurPost automates the creation of short-form video reels (Instagram/YouTube) grounded exclusively in classical Ayurvedic texts, reducing content production from hours to minutes while keeping claims authentic and clinician-reviewed.

---

## Data Processing

**Source texts:** Sushruta Samhita (3 vols), Charaka Samhita (4 vols), Ashtanga Hridayam (1 vol) — all scanned PDFs with no clean digital editions available.

**OCR:** Google Document AI (Document OCR processor) applied to 15-page PDF chunks; output stitched per volume. Stray symbols, diacritics, running page headers, and OCR garbles (e.g. `CHAPTER XIL`, `VL`) cleaned deterministically before chunking.

**Chunking:** 3-signal chapter segmentation (open header → closing colophon → page running header) with sthana-aware restart detection; SentenceSplitter at 1024-token target. Gazetteer entity-tagging (dosha, herb, disease) written back as payload metadata for hard-filter retrieval.

**Stack:** Google Document AI · LlamaIndex SentenceSplitter · Voyage AI `voyage-4-large` (dense) · BM25 (sparse) · Qdrant Cloud (hybrid index)

---

## Retrieval

Three complementary signals are required:

| Signal | Why |
|---|---|
| **Dense (Voyage)** | Semantic match across paraphrase and translation variation |
| **BM25 sparse** | Exact Sanskrit term recall (`agni`, `triphala`, specific roga names) |
| **Dosha hard-filter** | Prevents pitta content leaking into vata-season reels; enforced at query time via Qdrant payload index |

RRF (Reciprocal Rank Fusion) merges dense + sparse scores before the dosha filter is applied.

---

## System Design

### Diagram 1 — Knowledge Base Ingestion

```mermaid
flowchart TD
    A[Scanned PDFs\nSushruta · Charaka · Ashtanga] --> B[Google Document AI\n15-page chunks]
    B --> C[OCR Text Files\nper volume]
    C --> D[chunker.py / chunk_book.py\nsthana segmentation\n+ SentenceSplitter]
    D --> E[all_chunks.jsonl\n4785 chunks]
    E --> F[tagger.py\nGazetteer entity tagging\ndosha · herb · disease]
    F --> G[Voyage AI\nvoyage-4-large\ndense embeddings]
    F --> H[BM25 sparse\nFastEmbed]
    G & H --> I[Qdrant Cloud\nhybrid collection\nawaiturvedic_kb_v3]
    I --> J[chunk_id keyword index\ndosha · herb · disease\npayload indexes]
```

### Diagram 2 — Content Generation & Review

```mermaid
flowchart TD
    subgraph Schedule
        A[seasonal_bengaluru.yaml\ndosha_education.yaml\nclinic_services.yaml] --> B[calendar_engine.py\n20-slot posting cycle]
    end

    subgraph Generation
        B --> C[generate.py / generate_dosha.py\ngenerate_services.py]
        C --> D[HybridRetriever\nRRF + dosha hard-filter]
        D --> E[Claude Opus 4.8\ngrounded ReelScript\n3 scenes · MAX 15 words/scene]
        E --> F[Veo 3.1 Lite\nhyper-realistic 9:16 clips]
        E --> G[Chirp3-HD TTS\nen-IN · phonetic substitutions]
        F & G --> H[FFmpeg\nxfade · music bed · smooth fade]
        H --> I[reel.mp4\nDRAFT — pending sign-off]
        I --> J[audit.py\nSonnet 4.6 groundedness\n+ compliance judge]
    end

    subgraph Review
        I --> K[Streamlit App\nCloud Run · GCS mount]
        K --> L[Doctor monthly calendar\ninline video preview]
        L --> M{Edit needed?}
        M -- Yes --> N[Doctor types feedback\nin chat panel]
        N --> O[Re-run Opus\nsame Veo clips\nnew TTS + reassemble]
        O --> K
        M -- No --> P[Reel approved\nready to publish]
    end
```

---

## Evals

**Task-specific (LLM-as-judge):** `audit.py` uses Claude Sonnet 4.6 as a structured judge after every generation. Checks: (1) groundedness — each scene's voiceover must be supported by its cited `chunk_id` passages; (2) compliance — no cure/reversal/quantified-outcome language (India Drugs & Magic Remedies Act / ASCI standards). Hook scene (scene 0) is exempt from groundedness; compliance applies to all scenes. Output: `AuditReport` with per-scene `SceneVerdict` and `overall_pass` boolean written to `audit_report.json`.

**Error handling:** Veo high-load (code 8) handled via skip-existing-clips logic; Voyage batch limits managed with 20-text cap + 0.15 margin; Qdrant upserts batched at 200 points to stay under 33 MB payload limit; OCR garbled Roman numerals rejected via round-trip validation.

**Cost (per reel):** ~$0.40 Veo (4 clips × 8s × $0.05/s) + ~$0.03 Opus script + ~$0.001 Voyage embeddings + ~$0.01 TTS. Total ≈ **$0.44/reel**.

**Latency:** Script generation ~30s (Opus + adaptive thinking) · Veo clip generation ~90–120s each (parallel-safe) · TTS ~5s · FFmpeg assembly ~10s. End-to-end ≈ **4–6 min** per reel. Voiceover-only edits (no new Veo) ≈ **45s**.
