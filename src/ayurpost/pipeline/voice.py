"""
Per-scene Text-to-Speech for the reel voiceover (Google Cloud TTS).

Two modes:
  - Legacy (Wavenet/Neural2): synthesize_scenes() — used by the old image-slideshow
    pipeline. Verifies configured voice at runtime; fails loud if absent.
  - Production (Chirp3-HD): synthesize_en_chirp() — used by the Veo pipeline.
    Applies phonetic substitutions for Ayurvedic terms (Chirp3-HD does NOT support
    SSML phoneme tags — words tagged with <phoneme> are silently dropped).

Auth is ADC via GOOGLE_APPLICATION_CREDENTIALS.

resolve_voice() and synthesize() are the importable entry points for the orchestrator.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from google.cloud import texttospeech as tts

from ayurpost import config

# language_code per voice key, used for list_voices() and the synthesis request.
LANG_CODE = {"en": "en-IN", "kn": "kn-IN"}
_CONFIGURED = {"en": config.TTS_VOICE_EN, "kn": config.TTS_VOICE_KN}


def resolve_voice(client: tts.TextToSpeechClient, lang: str) -> str:
    """Return the configured voice name for `lang` after confirming it exists.

    Fails loud (listing available voices) if the configured name is absent — handles
    the kn-IN risk explicitly instead of producing a wrong-voice or silent fallback."""
    code = LANG_CODE[lang]
    configured = _CONFIGURED[lang]
    available = [v.name for v in client.list_voices(language_code=code).voices]
    if configured not in available:
        raise RuntimeError(
            f"configured {lang} voice {configured!r} not available for {code}. "
            f"Available: {available}")
    return configured


def synthesize(client: tts.TextToSpeechClient, text: str, lang: str, voice_name: str,
               out_path: Path) -> Path:
    """Synthesize `text` in `lang` with `voice_name` to an MP3 at out_path."""
    resp = client.synthesize_speech(
        input=tts.SynthesisInput(text=text),
        voice=tts.VoiceSelectionParams(language_code=LANG_CODE[lang], name=voice_name),
        audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
    )
    out_path.write_bytes(resp.audio_content)
    return out_path


def synthesize_scenes(texts: list[str], lang: str, out_dir: Path) -> list[Path]:
    """Synthesize one MP3 per text into out_dir as scene_{i}.{lang}.mp3, in order.

    Resolves (and verifies) the voice once up front, then synthesizes each scene."""
    out_dir.mkdir(parents=True, exist_ok=True)
    client = tts.TextToSpeechClient()
    voice_name = resolve_voice(client, lang)
    paths: list[Path] = []
    for idx, text in enumerate(texts):
        out_path = synthesize(client, text, lang, voice_name,
                              out_dir / f"scene_{idx}.{lang}.mp3")
        paths.append(out_path)
        print(f"  tts {lang} scene {idx} -> {out_path.name}")
    return paths


# ── Chirp3-HD production path (Veo pipeline) ────────────────────────────────

VOICE_FEMALE_EN = "en-IN-Chirp3-HD-Aoede"
VOICE_MALE_EN   = "en-IN-Chirp3-HD-Alnilam"

# Phonetic substitutions for Ayurvedic terms.
# Chirp3-HD does not support SSML <phoneme> tags — they silently drop the word.
_PHONETIC: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bvatas\b",  re.I), "vaatas"),
    (re.compile(r"\bvata\b",   re.I), "vaata"),
    (re.compile(r"\bpittas\b", re.I), "pittas"),
    (re.compile(r"\bpitta\b",  re.I), "pitta"),
    (re.compile(r"\bkaphas\b", re.I), "kaafas"),
    (re.compile(r"\bkapha\b",  re.I), "kaafa"),
    (re.compile(r"\bdoshas\b", re.I), "doshas"),
    (re.compile(r"\bdosha\b",  re.I), "dosha"),
    (re.compile(r"\bagni\b",   re.I), "aagni"),
]


def fix_pronunciation(text: str) -> str:
    """Replace Ayurvedic terms with phonetic spellings for Chirp3-HD TTS."""
    for pat, rep in _PHONETIC:
        text = pat.sub(rep, text)
    return text


def synthesize_en_chirp(texts: list[str], out_dir: Path,
                         voice: str | None = None) -> list[Path]:
    """Synthesize English voiceovers with Chirp3-HD and phonetic fixes.

    Randomly picks male or female voice if none specified (one gender per call,
    consistent across all scenes). Returns one MP3 per text as scene_{i}.en.mp3.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if voice is None:
        voice = random.choice([VOICE_FEMALE_EN, VOICE_MALE_EN])
    print(f"  tts en (Chirp3-HD, voice={voice.split('-')[-1]})")
    client = tts.TextToSpeechClient()
    paths: list[Path] = []
    for idx, text in enumerate(texts):
        fixed = fix_pronunciation(text)
        resp = client.synthesize_speech(
            input=tts.SynthesisInput(text=fixed),
            voice=tts.VoiceSelectionParams(language_code="en-IN", name=voice),
            audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
        )
        p = out_dir / f"scene_{idx}.en.mp3"
        p.write_bytes(resp.audio_content)
        print(f"    scene {idx} -> {p.name}")
        paths.append(p)
    return paths
