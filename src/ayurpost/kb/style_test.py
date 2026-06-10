"""
Style consistency test: compare Google's image gen models for AyurPost.

Generates the same image sequence with:
  - Imagen 3 (diffusion model, separate calls with style-locked prompts)
  - Gemini 2.5 Flash Image (autoregressive multimodal, conversational sequence)

Scenarios:
  turmeric  — 3-image turmeric prep sequence (sanity check)
  abhyanga  — 4-image Abhyanga oil prep sequence (production-style)

Styles (visual aesthetic):
  illustration  — original hand-drawn botanical illustration
  clay          — polymer clay diorama, miniature scale
  vintage       — 18th-century apothecary plate, scientific botanical
  macro         — premium macro nature photography

Output:
  data/style-test-{scenario}-{style}/{model}/0N.png

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=path/to/key.json \\
  GCP_PROJECT_ID=258153711042 \\
  python -m ayurpost.kb.style_test --scenario abhyanga --style vintage --models gemini

To run multiple styles in one shot, pass --style multiple times or use a loop wrapper.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from google import genai
from google.genai import types


STYLE_PREAMBLES = {
    "illustration": (
        "Warm botanical illustration in soft ochre and sage palette, gentle natural "
        "lighting from above, hand-drawn quality with subtle texture, no faces, "
        "no human figures, minimalist composition with negative space."
    ),
    "warm_illustration": (
        "Rich warm botanical illustration in a palette of deep golden amber, "
        "terracotta, burnt sienna and copper with subtle olive accents, glowing "
        "golden-hour lighting that bathes the scene in honeyed warmth, hand-drawn "
        "quality with visible textured brush strokes on aged cream paper background, "
        "no faces, no human figures, cozy and inviting composition with rich warm "
        "shadows that draw the eye in."
    ),
    "clay": (
        "Polymer clay diorama, miniature scale, soft natural studio lighting from "
        "above, shallow depth of field with cinematic bokeh, charming tactile "
        "handmade materials, warm earth tones with terracotta and sage accents, "
        "no faces, no human figures, premium handcrafted aesthetic, "
        "Pinterest-friendly composition."
    ),
    "vintage": (
        "Vintage botanical scientific illustration in the style of an 18th-century "
        "apothecary plate, intricate ink linework with delicate watercolor wash, "
        "rich botanical detail showing leaf veins and root textures, sepia and "
        "ochre tones with subtle sage and rust accents, aged parchment paper "
        "texture in the background, scholarly Ayurvedic manuscript feeling, "
        "no faces, no human figures, museum-quality composition."
    ),
    "macro": (
        "Premium macro nature photography, extreme close-up detail showing leaf "
        "veins, root textures, and natural surface character, soft warm natural "
        "lighting from a window, shallow depth of field with cinematic bokeh, "
        "editorial magazine quality, arranged on a natural linen or weathered "
        "wood surface, rich warm color palette of golden ochres and deep greens, "
        "no faces, no human figures, photorealistic with film-grain texture."
    ),
}

SCENES_BY_SCENARIO = {
    "turmeric": [
        "Fresh whole turmeric roots arranged on a weathered wooden surface, with a few green leaves nearby.",
        "Turmeric roots being ground with a stone mortar and pestle on a wooden surface, with bright yellow paste visible.",
        "A small ceramic bowl holding finished turmeric paste, surrounded by a few intact turmeric roots and a wooden spoon.",
    ],
    "abhyanga": [
        "A glass bottle of warm sesame oil beside fresh herbs — bala root, ashwagandha root, gotu kola leaves — arranged on a polished wooden surface, traditional Ayurvedic apothecary feeling.",
        "A small copper vessel on a low warm flame, fresh herbs steeping in golden oil, gentle wisps of steam rising, surrounded by clay pots and a few dried herbs.",
        "Strained warm herbal oil pouring slowly from a copper vessel into a small ceramic bowl, golden droplets mid-air, with used herb residue resting on a wooden surface nearby.",
        "A finished bowl of warm herbal oil ready for abhyanga massage, with a folded cotton cloth beside it, a small clay diya lamp burning softly, and a few intact herbs as garnish, in a serene meditative setting.",
    ],
}


def init_clients(project_id: str, location: str = "us-central1") -> genai.Client:
    """Vertex AI client (for Imagen) — uses ADC from GOOGLE_APPLICATION_CREDENTIALS."""
    return genai.Client(vertexai=True, project=project_id, location=location)


def gen_imagen(client: genai.Client, out_dir: Path, scenes: list[str], style_preamble: str) -> None:
    """Imagen 3: each image generated independently with style-locked prompt."""
    import time
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Imagen 3 (diffusion, independent calls) ===")
    for idx, scene in enumerate(scenes, 1):
        if idx > 1:
            print(f"  ... sleeping 70s to avoid rate limit ...")
            time.sleep(70)
        prompt = f"{scene} {style_preamble}"
        print(f"  [{idx}/{len(scenes)}] generating: {scene[:60]}...")
        try:
            response = client.models.generate_images(
                model="imagen-3.0-generate-002",
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="1:1",
                ),
            )
            img_bytes = response.generated_images[0].image.image_bytes
            out_path = out_dir / f"{idx:02d}.png"
            out_path.write_bytes(img_bytes)
            print(f"        saved → {out_path}")
        except Exception as e:
            print(f"        FAILED: {type(e).__name__}: {e}")


def gen_gemini(client: genai.Client, out_dir: Path, scenes: list[str], style_preamble: str) -> None:
    """Gemini 2.5 Flash Image: conversational sequence for style consistency."""
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Gemini 2.5 Flash Image (autoregressive, conversational) ===")
    model_name = "gemini-2.5-flash-image"

    history = []

    for idx, scene in enumerate(scenes, 1):
        if idx == 1:
            user_msg = f"{scene} {style_preamble}"
        else:
            user_msg = (
                f"Now show: {scene} Keep the exact same brand aesthetic, "
                "same color palette, same lighting, same materials and style "
                "as the previous image — this is a continuous visual series."
            )
        print(f"  [{idx}/{len(scenes)}] generating: {scene[:60]}...")
        try:
            history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))
            response = client.models.generate_content(
                model=model_name,
                contents=history,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            saved = False
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    out_path = out_dir / f"{idx:02d}.png"
                    out_path.write_bytes(part.inline_data.data)
                    print(f"        saved → {out_path}")
                    history.append(response.candidates[0].content)
                    saved = True
                    break
            if not saved:
                print(f"        FAILED: no image returned. Response parts: "
                      f"{[type(p).__name__ for p in response.candidates[0].content.parts]}")
        except Exception as e:
            print(f"        FAILED: {type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENES_BY_SCENARIO.keys()),
        default="turmeric",
        help="Which scene set to generate.",
    )
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_PREAMBLES.keys()),
        default="illustration",
        help="Which visual style preset to apply.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=["imagen", "gemini"],
        default=["imagen", "gemini"],
        help="Which models to run (default: both).",
    )
    args = parser.parse_args()

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        print("ERROR: GCP_PROJECT_ID env var required.", file=sys.stderr)
        return 1
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print("WARNING: GOOGLE_APPLICATION_CREDENTIALS not set. Will use default auth.")

    scenes = SCENES_BY_SCENARIO[args.scenario]
    style_preamble = STYLE_PREAMBLES[args.style]
    output_base = Path(f"data/style-test-{args.scenario}-{args.style}")
    output_base.mkdir(parents=True, exist_ok=True)
    print(f"Scenario: {args.scenario} ({len(scenes)} scenes)")
    print(f"Style:    {args.style}")
    print(f"Output base: {output_base}")

    client = init_clients(project_id, "us-central1")

    if "imagen" in args.models:
        gen_imagen(client, output_base / "imagen", scenes, style_preamble)
    if "gemini" in args.models:
        gen_gemini(client, output_base / "gemini", scenes, style_preamble)

    print(f"\nDone. Output in {output_base}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
