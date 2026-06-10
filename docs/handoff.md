# AyurPost — Agent Handoff Document

**Date:** 2026-06-10  
**Outgoing agent:** Claude Sonnet 4.6  
**Repo:** https://github.com/AmishaM/AyurPost  
**Deployed app:** https://ayurpost-app-258153711042.us-central1.run.app  
**GCP project:** `ayurpost`

---

## What AyurPost Is

AI-powered Ayurvedic reel generator for a clinic in Bengaluru. It retrieves grounding passages from classical Ayurvedic texts (Sushruta, Charaka, Ashtanga), generates a voiceover script via Claude Opus, creates video clips via Veo 3.1 Lite, synthesises audio via Chirp3-HD TTS, and assembles them into a 9:16 MP4. A Streamlit app lets the clinic doctor review reels on a monthly calendar and iterate voiceovers via chat.

---

## Environment

**Working directory:** `/home/user/ai-ml-course/capstone-project`  
**Python venv:** `.venv/` — activate with `source .venv/bin/activate` or prefix `PYTHONPATH=src .venv/bin/python`  
**Env vars:** `.env` (gitignored) — contains all API keys. Always `set -a && source .env && set +a` before running pipelines.

**Critical gotcha:** The global shell `ANTHROPIC_BASE_URL` points to an internal gateway. AyurPost forces `base_url="https://api.anthropic.com"` in `config.py`. Every Anthropic client call passes `base_url=config.ANTHROPIC_BASE_URL`.

### Key credentials (all in `.env`)
- `ANTHROPIC_API_KEY` — Claude Opus + Sonnet
- `VOYAGE_API_KEY` — voyage-4-large embeddings
- `QDRANT_URL` + `QDRANT_API_KEY` — Qdrant Cloud (Frankfurt cluster)
- `GCP_PROJECT_ID=ayurpost`, `GOOGLE_APPLICATION_CREDENTIALS` — TTS + Veo + GCS
- `DOC_AI_PROCESSOR_ID=32ca12b4e23a6470`, `DOC_AI_LOCATION=us` — Document AI OCR

---

## Architecture

```
src/ayurpost/
  config.py                    # all paths + API key loading
  app.py                       # Streamlit doctor review UI
  kb/
    chunker.py                 # Sushruta (3 vol) chunker — sthana-aware
    chunk_book.py              # Charaka (4 vol) + Ashtanga chunker
    ocr_book.py                # Generic PDF → Document AI OCR
    ocr_sushruta.py            # Legacy Sushruta OCR (already done)
  gazetteer/
    tagger.py                  # Deterministic dosha/herb/disease tagger
    gazetteer.yaml             # Controlled vocabulary (45 herbs, 23 diseases)
  retrieval/
    embeddings.py              # Voyage dense + BM25 sparse batching
    index.py                   # Build Qdrant collection (currently v3)
    search.py                  # HybridRetriever — RRF + dosha hard-filter
  pipeline/
    season.py                  # Date → season YAML lookup
    content.py                 # Opus → ReelScript (seasonal)
    generate.py                # Seasonal reel orchestrator CLI
    generate_dosha.py          # Dosha education reel orchestrator CLI
    generate_services.py       # Clinic services reel orchestrator CLI
    veo.py                     # Veo 3.1 Lite clip generation
    voice.py                   # Chirp3-HD TTS + phonetic substitutions
    assemble.py                # FFmpeg xfade + music + smooth fade
    audit.py                   # Sonnet 4.6 groundedness + compliance judge
  roadmap/
    seasonal_bengaluru.yaml    # 5 seasons with dosha/ailment/remedy metadata
    dosha_education.yaml       # 3 doshas × 5 topics = 15 entries
    clinic_services.yaml       # publishable: abhyanga, shirodhara, virechana, vamana
    calendar_engine.py         # 20-slot cycle assignment
  eval/
    cases.py                   # Test fixtures (retrieval, compliance, groundedness)
    eval_script.py             # Structural validation (no API)
    eval_retrieval.py          # Qdrant dosha-filter quality
    eval_audit.py              # Auditor accuracy (mocked chunk fetch)
    run_evals.py               # CLI runner
```

---

## Knowledge Base

**Qdrant collection:** `ayurvedic_kb` (alias → `ayurvedic_kb_v3`)  
**Total chunks:** 4,785  
**Sources:**
| Source | Chunks |
|---|---|
| Sushruta Samhita vol1-3 | 836 |
| Charaka Samhita vol1-4 | 3,795 |
| Ashtanga Hridayam | 154 |

**Payload indexes:** `chunk_id` (keyword), `doshas_mentioned` (keyword), `herbs` (keyword), `diseases` (keyword), `source` (keyword), `section_role` (keyword), `chapter` (integer)

**To re-index from scratch (bump version first):**
```bash
# Edit COLLECTION_VERSION in src/ayurpost/retrieval/index.py (currently "v3")
PYTHONPATH=src .venv/bin/python -m ayurpost.retrieval.index
# Then recreate chunk_id index:
PYTHONPATH=src .venv/bin/python -c "
from ayurpost import config
from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSchemaType
client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
client.create_payload_index(config.QDRANT_COLLECTION, 'chunk_id', PayloadSchemaType.KEYWORD)
"
```

---

## Deployed Streamlit App

**URL:** https://ayurpost-app-258153711042.us-central1.run.app  
**Cloud Run service:** `ayurpost-app` (region: `us-central1`)  
**Image:** `us-central1-docker.pkg.dev/ayurpost/ayurpost/app:latest`  
**GCS mount:** `ayurpost-artifacts` bucket → `/mnt/gcs` inside container  
**GENERATED_DIR:** `/mnt/gcs/generated` (set as Cloud Run env var)

**To redeploy after code changes:**
```bash
# 1. Build
docker build -t us-central1-docker.pkg.dev/ayurpost/ayurpost/app:latest .

# 2. Auth (service account)
gcloud auth activate-service-account \
    --key-file=ayurpost-8302072a7764.json

# 3. Push
docker push us-central1-docker.pkg.dev/ayurpost/ayurpost/app:latest

# 4. Deploy (fill ENV_VARS from .env)
ENV_VARS="ANTHROPIC_API_KEY=...,ANTHROPIC_BASE_URL=https://api.anthropic.com,..."
gcloud run deploy ayurpost-app \
  --image us-central1-docker.pkg.dev/ayurpost/ayurpost/app:latest \
  --region us-central1 --platform managed \
  --service-account ayur-post@ayurpost.iam.gserviceaccount.com \
  --add-volume name=gcs-artifacts,type=cloud-storage,bucket=ayurpost-artifacts \
  --add-volume-mount volume=gcs-artifacts,mount-path=/mnt/gcs \
  --set-env-vars "$ENV_VARS" \
  --memory 1Gi --allow-unauthenticated --project ayurpost
```

**GitHub push pattern** (PAT required each time, clear after push):
```bash
git remote set-url origin "https://AmishaM:<PAT>@github.com/AmishaM/AyurPost.git"
git push origin main
git remote set-url origin "https://github.com/AmishaM/AyurPost.git"
```

---

## Reel Generation

**Output dir:** `data/generated/<slug>-<timestamp>/`  
Each artifact dir contains: `script.json`, `clip_hook.mp4`, `clip_scene_*.mp4`, `tts_hook.mp3`, `tts_scene_*.mp3`, `reel.mp4`, `manifest.json`, `audit_report.json`

**Generate one seasonal reel:**
```bash
set -a && source .env && set +a
PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate \
    --season-key monsoon --model claude-opus-4-8 \
    --music data/music/music/freemusicforvideo-indian-flute-515569.mp3
```

**Generate one dosha reel:**
```bash
PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_dosha \
    --dosha-key vata --topic-type prakriti --model claude-opus-4-8 \
    --music data/music/music/alex-morgan-sunrise-yoga-flow-537475.mp3
```

**Generate one service reel:**
```bash
PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.generate_services \
    --service-key abhyanga --model claude-opus-4-8 \
    --music data/music/music/alex-morgan-sunrise-yoga-flow-537475.mp3
```

**Music files:**
| File | Use for |
|---|---|
| `alex-morgan-sunrise-yoga-flow-537475.mp3` | Dosha + services (neutral) |
| `freemusicforvideo-indian-flute-515569.mp3` | Monsoon, post-monsoon |
| `india_happy-indian-classical-indian-music-494847.mp3` | Summer, spring |

---

## Uploading Reels to GCS (for Streamlit app to see them)

After generating locally, upload to GCS:
```bash
gcloud auth activate-service-account --key-file=ayurpost-8302072a7764.json
gsutil -m cp -r data/generated/<artifact-dir> gs://ayurpost-artifacts/generated/
```

**Spread reels across calendar dates** (update `generated_at` in manifests before upload):
```python
import json, glob
from datetime import datetime, timezone, timedelta

dirs = sorted(glob.glob("data/generated/*/manifest.json"))
start = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
for i, p in enumerate(dirs):
    m = json.loads(open(p).read())
    m["generated_at"] = (start + timedelta(days=i)).isoformat()
    open(p, "w").write(json.dumps(m, indent=2))
    print(f"Set {p} → {m['generated_at'][:10]}")
```

---

## Pending / Known Issues

1. **Reel generation in progress** — batches running at handoff time. Check `data/generated/` for completion. Upload to GCS when done (see above).

2. **Structural eval failures** — 4 of 5 sample reels fail the 15-word voiceover limit. These were generated before the cap was added to the Opus prompt. New reels use the cap and should pass. Run: `PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals --category structural`

3. **Cloud Run unauthenticated access** — `allow-unauthenticated` IAM binding requires manual toggle in GCP Console (Cloud Run → ayurpost-app → Security → Allow unauthenticated invocations) after each deploy.

4. **Veo high-load (code 8)** — Veo 3.1 Lite occasionally returns code 8 under high load. The pipeline has skip-existing-clips logic so retrying a failed run picks up where it left off.

5. **Spring + post-monsoon** — no entries in `seasonal_bengaluru.yaml` for spring/post-monsoon date ranges yet. The calendar engine maps dates to seasons by month. You may need to add month ranges for these seasons.

6. **Chunkers for Charaka vol3/4** — Charaka vol3/4 are Sharma's English commentary (not original text). The current chunker treats them the same as vols 1/2. A future improvement: tag commentary chunks separately so retrieval can distinguish canonical text from commentary.

---

## Evals

```bash
# Fast — no API calls
PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals --category structural

# Full suite (Qdrant + Anthropic)
PYTHONPATH=src .venv/bin/python -m ayurpost.eval.run_evals
```

---

## Key Gotchas

| Gotcha | Detail |
|---|---|
| Voyage batch size | Sanskrit BPE expansion is 4-7×. `VOYAGE_MAX_TEXTS=20`, `VOYAGE_BATCH_MARGIN=0.15`. If batches still fail, lower `VOYAGE_MAX_TEXTS`. |
| Qdrant upsert size | Batched at 200 points/request (33MB payload limit). |
| Chirp3-HD phonetics | Voiceovers use plain-text substitutions: vata→vaata, kapha→kaafa, agni→aagni. SSML phoneme tags silently drop words. |
| No ffmpeg in PATH | Pipeline uses `ffmpeg` system binary. Verify with `ffmpeg -version`. |
| chunk_id index | Must be manually created after each Qdrant collection version bump. See re-index command above. |
| Sthana re-entry guard | `chunk_book.py` uses one-way sthana transitions to prevent back-matter false triggers (Charaka vol2 Siddhisthana was duplicating). |
