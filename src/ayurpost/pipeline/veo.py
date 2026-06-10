"""
Veo video clip generation for the AyurPost reel pipeline.

Generates one short Veo clip per script scene (hook + N scenes). Each clip uses
a single hyper-realistic cinematic style; the subject comes entirely from the
image_prompt Opus wrote for that scene — no style detection or switching needed.

Supported durations: 4, 6, 8 seconds (Veo 3.x text-to-video constraint).
Hook clips use 6 s; scene clips use 8 s.

Auth: Vertex AI ADC via GOOGLE_APPLICATION_CREDENTIALS.

Usage (importable):
    from ayurpost.pipeline.veo import generate_reel_clips
    clips = generate_reel_clips(script, season_key, reel_dir)
    # clips = {"hook": Path, "scene_0": Path, "scene_1": Path, ...}
"""
from __future__ import annotations

import time
from pathlib import Path

from google import genai
from google.genai import types

from ayurpost import config
from ayurpost.pipeline.content import ReelScript

MODEL      = "veo-3.1-lite-generate-001"
ASPECT     = "9:16"
DUR_HOOK   = 6    # seconds — hook clip
DUR_SCENE  = 8    # seconds — each scene clip
POLL_S     = 10
TIMEOUT_S  = 360

# Single visual style applied to every clip.
# Subject (what appears in frame) is driven by the image_prompt from the script.
STYLE = (
    "Hyper-realistic cinematic photography: photorealistic render of the described "
    "scene with rich natural textures and colours, soft warm golden-hour backlight, "
    "shallow depth of field with creamy bokeh, high-end commercial cinematography "
    "quality. No people, no text."
)

CARTOON_STYLE = (
    "Soft 2D Indian illustration style: gentle muted pastel tones inspired by "
    "traditional Pattachitra and Madhubani art, delicate clean outlines, warm earthy "
    "palette of dusty terracotta, sage green and soft ochre, hand-painted watercolour "
    "texture, calm and meditative mood. Stylized illustrated characters are welcome; "
    "no text overlays."
)

# Season-level establishing-shot subjects for the hook clip.
# These are intentionally scene/mood descriptions, not herb lists.
HOOK_SUBJECT: dict[str, str] = {
    "summer":      ("Bright sunny Bengaluru rooftop with terracotta pots and flowering "
                    "trees, heat shimmer in golden afternoon light, dry dusty air"),
    "monsoon":     ("Rain-soaked Bengaluru garden, dark monsoon clouds overhead, "
                    "wet green leaves glistening, soft grey diffused light"),
    "winter":      ("Cool misty Bengaluru morning, dew drops on a banana leaf, "
                    "soft blue-white early light, still and quiet"),
    "spring":      ("Bengaluru park in early spring, pale pink blossoms on trees, "
                    "soft warm morning light, gentle breeze"),
    "post_monsoon": ("Bengaluru street after rain, clean wet pavements reflecting "
                     "golden late-afternoon light, fresh clear air"),
    # dosha education hooks
    "vata":        ("Dry autumn leaves swirling in wind over a Bengaluru street, "
                    "movement and lightness, cool crisp air, golden-brown tones"),
    "pitta":       ("Bright midday sun over a South Indian courtyard, vivid colours, "
                    "intense golden light, heat shimmer above stone floor"),
    "kapha":       ("Lush green Bengaluru garden after morning mist, heavy dewy leaves, "
                    "cool still air, soft diffused white light"),
    # clinic service hooks
    "abhyanga":    ("Ayurvedic massage table with warm sesame oil in a brass bowl, "
                    "soft oil lamp glow, clean white linen, serene treatment room"),
    "shirodhara":  ("Shirodhara vessel with golden oil dripping in a thin stream, "
                    "soft candlelight, polished copper, peaceful clinic interior"),
    "virechana":   ("Neatly arranged Ayurvedic herbal preparations in terracotta bowls, "
                    "warm earthy tones, soft natural light through a clinic window"),
    "vamana":      ("Steam rising from a traditional Ayurvedic herbal decoction vessel, "
                    "dried herbs and roots on a wooden surface, warm amber light"),
}


def _poll(op, client) -> object:
    """Poll a GenerateVideosOperation until done; fail loud on error or timeout."""
    waited = 0
    while not op.done:
        if waited >= TIMEOUT_S:
            raise TimeoutError(f"Veo op not done after {TIMEOUT_S}s: {op.name}")
        time.sleep(POLL_S)
        waited += POLL_S
        op = client.operations.get(op)
        print(f"      ...{waited}s  done={op.done}")
    if op.error:
        raise RuntimeError(f"Veo operation failed: {op.error}")
    resp = op.response
    if getattr(resp, "rai_media_filtered_count", 0):
        raise RuntimeError(f"Veo clip filtered by safety: {resp.rai_media_filtered_reasons}")
    vids = resp.generated_videos or []
    if not vids:
        raise RuntimeError("Veo returned no video")
    return vids[0]


def generate_clip(subject: str, duration: int, out: Path, client=None,
                  style: str = STYLE) -> Path:
    """Generate one Veo clip: subject + style → MP4 at out.

    Args:
        subject:  The visual scene description (from image_prompt or HOOK_SUBJECT).
        duration: Clip length in seconds. Must be 4, 6, or 8.
        out:      Output path for the MP4.
        client:   Optional pre-built genai.Client. Created from config if None.
        style:    Visual style string. Defaults to STYLE (cinematic). Pass
                  CARTOON_STYLE for illustrated look.
    """
    if duration not in (4, 6, 8):
        raise ValueError(f"duration must be 4, 6 or 8 — got {duration}")
    if client is None:
        client = genai.Client(vertexai=True, project=config.GCP_PROJECT_ID,
                              location=config.GCP_LOCATION)
    prompt = f"{subject}. {style} Subtle natural motion. No people, no text."
    print(f"    veo ({duration}s): {prompt[:90]}...")
    op = client.models.generate_videos(
        model=MODEL,
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio=ASPECT,
            duration_seconds=duration,
            number_of_videos=1,
            generate_audio=False,
        ),
    )
    print(f"    operation submitted: {op.name}")
    video = _poll(op, client)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video.video.video_bytes)
    print(f"    wrote {out.name} ({out.stat().st_size:,} bytes)")
    return out


def generate_reel_clips(script: ReelScript, season_key: str,
                        reel_dir: Path,
                        style: str = STYLE) -> dict[str, Path]:
    """Generate all Veo clips for one reel (hook + all scenes).

    Skips clips that already exist on disk (safe to resume after failures).

    Returns:
        dict with keys "hook", "scene_0", "scene_1", ... mapping to clip paths.
    """
    if season_key not in HOOK_SUBJECT:
        raise ValueError(f"no hook subject defined for season {season_key!r} — "
                         f"add it to veo.HOOK_SUBJECT")
    client = genai.Client(vertexai=True, project=config.GCP_PROJECT_ID,
                          location=config.GCP_LOCATION)
    clips: dict[str, Path] = {}

    # Hook clip
    hook_path = reel_dir / "clip_hook.mp4"
    if hook_path.exists():
        print(f"    skip (exists): {hook_path.name}")
    else:
        generate_clip(HOOK_SUBJECT[season_key], DUR_HOOK, hook_path, client, style)
    clips["hook"] = hook_path

    # Scene clips
    for i, scene in enumerate(script.scenes):
        path = reel_dir / f"clip_scene_{i}.mp4"
        if path.exists():
            print(f"    skip (exists): {path.name}")
        else:
            generate_clip(scene.image_prompt, DUR_SCENE, path, client, style)
        clips[f"scene_{i}"] = path

    return clips
