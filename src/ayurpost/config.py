from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CHUNKS_DIR = DATA_DIR / "chunks"
OCR_DIR = DATA_DIR / "ocr"

# ── Anthropic (caption generation + eval judge) ───────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL = "claude-sonnet-4-6"
# This env globally sets ANTHROPIC_BASE_URL to an internal gateway that rejects
# personal sk-ant keys. Hardcode the public endpoint as a LITERAL (not os.getenv,
# so the shell var is ignored) and pass base_url=ANTHROPIC_BASE_URL whenever this
# project builds an Anthropic client. Scope: AyurPost only — the shell var stays
# untouched, so other projects in this env keep using the gateway.
ANTHROPIC_BASE_URL = "https://api.anthropic.com"

# ── Google Cloud (OCR, embeddings, image gen, TTS) ────────────────────────
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# Document AI (OCR for Sushruta volumes)
DOC_AI_LOCATION = os.getenv("DOC_AI_LOCATION", "asia-south1")
DOC_AI_PROCESSOR_ID = os.getenv("DOC_AI_PROCESSOR_ID", "")

# Embeddings — Voyage AI voyage-3-large
# 32K input window (handles even the longest treatment protocols as a single chunk)
# Newest general-purpose Voyage model; best-rated multilingual retrieval. Default
# dim 1024. 200M tokens free (covers our one-time KB ingest). Switched from
# voyage-3-large 2026-06-09 — it lost its free allowance (now an "older model").
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "voyage-4-large")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")

# Image generation — locked in after style test comparison
# Winner: Gemini 2.5 Flash Image + warm_illustration preset
# (Beat Imagen 3, clay diorama, vintage botanical, macro photo, original
# illustration on style consistency + brand fit for AyurPost.)
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
IMAGE_STYLE_PREAMBLE = (
    "Rich warm botanical illustration in a palette of deep golden amber, "
    "terracotta, burnt sienna and copper with subtle olive accents, glowing "
    "golden-hour lighting that bathes the scene in honeyed warmth, hand-drawn "
    "quality with visible textured brush strokes on aged cream paper background, "
    "no faces, no human figures, cozy and inviting composition with rich warm "
    "shadows that draw the eye in."
)

# Text-to-Speech (reel voiceover — English-IN + Kannada)
TTS_VOICE_EN = os.getenv("TTS_VOICE_EN", "en-IN-Wavenet-D")
TTS_VOICE_KN = os.getenv("TTS_VOICE_KN", "kn-IN-Wavenet-A")

# ── Qdrant (vector DB) ────────────────────────────────────────────────────
QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = "ayurvedic_kb"

# ── OpenWeatherMap (roadmap input — current weather/season for clinic) ────
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")

# ── Clinic config ─────────────────────────────────────────────────────────
CLINIC_CITY = os.getenv("CLINIC_CITY", "Bengaluru")
CLINIC_COUNTRY_CODE = os.getenv("CLINIC_COUNTRY_CODE", "IN")
