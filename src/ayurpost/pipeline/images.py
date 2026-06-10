"""
Per-scene image generation with Gemini 2.5 Flash Image (Vertex AI).

One 9:16 image per scene, generated as a CONVERSATIONAL sequence so later scenes keep
the first scene's palette/lighting/materials (the style-consistency pattern proven in
kb/style_test.py). config.IMAGE_STYLE_PREAMBLE (the locked warm-botanical look) is
prepended to the first scene's prompt; subsequent scenes are told to match the series.

Auth is ADC via GOOGLE_APPLICATION_CREDENTIALS (Vertex client), same as style_test.py.
Image bytes arrive at part.inline_data.data.

generate_scene_images() is the importable entry point used by the orchestrator.
"""

from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types

from ayurpost import config


def _client() -> genai.Client:
    return genai.Client(vertexai=True, project=config.GCP_PROJECT_ID,
                        location=config.GCP_LOCATION)


def generate_scene_images(image_prompts: list[str], out_dir: Path) -> list[Path]:
    """Generate one PNG per prompt into out_dir as scene_{i}.png, 9:16, style-locked.

    Returns the saved paths in scene order. Fails loud if any scene returns no image —
    a missing frame must not silently shorten the reel (Rule 13)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _client()
    history: list[types.Content] = []
    paths: list[Path] = []

    for idx, prompt in enumerate(image_prompts):
        if idx == 0:
            user_msg = f"{prompt} {config.IMAGE_STYLE_PREAMBLE}"
        else:
            user_msg = (
                f"Now show: {prompt} Keep the exact same brand aesthetic, same color "
                "palette, same lighting, same materials and style as the previous image "
                "— this is a continuous visual series."
            )
        history.append(types.Content(role="user",
                                     parts=[types.Part.from_text(text=user_msg)]))
        resp = client.models.generate_content(
            model=config.IMAGE_MODEL,
            contents=history,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(aspect_ratio="9:16"),
            ),
        )
        saved = None
        for part in resp.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                out_path = out_dir / f"scene_{idx}.png"
                out_path.write_bytes(part.inline_data.data)
                history.append(resp.candidates[0].content)  # carry style forward
                saved = out_path
                break
        if saved is None:
            kinds = [type(p).__name__ for p in resp.candidates[0].content.parts]
            raise RuntimeError(f"scene {idx}: no image returned (parts={kinds})")
        paths.append(saved)
        print(f"  image scene {idx} -> {saved.name}")

    return paths
