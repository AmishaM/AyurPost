"""
Grounded reel-script generation with Claude (Opus 4.8, structured output).

Takes a season dict (from season.py) and the KB chunks retrieved for that season
(from HybridRetriever) and produces a ReelScript: a hook, an English caption with
hashtags, N scenes (each with English + Kannada voiceover, an image prompt, and the
chunk_ids it is grounded in), and a wellness disclaimer.

Grounding + compliance are enforced two ways:
  - System prompt: claims must come ONLY from the supplied chunks; no fabricated
    remedies; no "cure"/regulated/quantified-outcome claims; seasonal education only.
  - Code check (fail loud): every scene's grounded_chunk_ids must be a subset of the
    chunk_ids we actually passed in — a scene citing an id we never supplied means the
    model invented a source, which must not slip into a draft silently (Rule 13).

The Anthropic client is built with base_url=config.ANTHROPIC_BASE_URL (the public
endpoint literal) so the env's internal-gateway ANTHROPIC_BASE_URL is bypassed.

Output is a DRAFT pending clinician sign-off — never auto-published.

Usage (debug self-test — retrieves + generates for the current season, prints JSON):
    PYTHONPATH=src .venv/bin/python -m ayurpost.pipeline.content
"""

from __future__ import annotations

import sys

import anthropic
from pydantic import BaseModel, Field

from ayurpost import config


class Scene(BaseModel):
    voiceover_en: str = Field(description="English voiceover narration for this scene. "
                              "1-2 flowing sentences, MAX 25 words. "
                              "Must fit inside a 10-second clip read at natural pace.")
    voiceover_kn: str = Field(description="Faithful Kannada (ಕನ್ನಡ) translation of "
                              "voiceover_en, in Kannada script. Same meaning, natural phrasing.")
    image_prompt: str = Field(description="A concrete visual scene to illustrate this "
                              "narration: objects, herbs, foods, preparation steps. No "
                              "people, no faces, no text in the image.")
    grounded_chunk_ids: list[str] = Field(description="The chunk_id(s) from the supplied "
                              "context that this scene's claims are grounded in. Use only "
                              "ids that appear in the provided context.")


class ReelScript(BaseModel):
    hook: str = Field(description="A short opening line (the first thing the viewer hears/"
                      "reads) that frames the seasonal topic and draws attention.")
    caption_en: str = Field(description="The English social-media caption for the post.")
    hashtags: list[str] = Field(description="5-10 relevant hashtags, each starting with #.")
    scenes: list[Scene] = Field(description="The ordered scenes of the reel.")
    disclaimer: str = Field(description="A short wellness disclaimer: general Ayurvedic "
                            "seasonal education, not medical advice; consult a qualified "
                            "practitioner. No cure or guaranteed-outcome language.")


_SYSTEM = """You write short Instagram/YouTube reel scripts for an Ayurvedic clinic in \
Bengaluru, India. Your job is SEASONAL WELLNESS EDUCATION only.

Hard rules:
- Ground every Ayurvedic claim ONLY in the numbered CONTEXT passages provided in the \
user message. Do not add remedies, herbs, or claims that are not supported by the context.
- For each scene, list in grounded_chunk_ids the chunk_id(s) of the passage(s) you used. \
Use only chunk_ids that appear in the provided context.
- Do NOT make medical claims of cure, reversal, or guaranteed/quantified outcomes \
(e.g. "cures arthritis", "lose X kg"). This is general wellness education, not treatment advice.
- Keep it warm, practical, and accessible to a general audience. Avoid heavy Sanskrit \
unless the context uses it; if you do, gloss it plainly.
- Write the hook and all scenes as ONE continuous narrative arc. Each scene must \
explicitly continue from the previous — use bridging language ("This is why...", \
"So this season...", "That's why Ayurveda says..."). The listener should hear one \
flowing story, not a list of disconnected seasonal tips.
- The Kannada voiceover must be a faithful translation of the English voiceover for the \
same scene, in Kannada script.
- Always include the wellness disclaimer."""


def _format_context(chunks: list[dict]) -> str:
    """Render retrieved chunk payloads as chunk_id-labelled context.

    Only the chunk_id is shown as an identifier (no competing ordinal number), so the
    model has exactly one token to cite in grounded_chunk_ids."""
    blocks = []
    for c in chunks:
        text = " ".join(c["text"].split())
        blocks.append(f"chunk_id={c['chunk_id']}\n{text}")
    return "\n\n".join(blocks)


def generate_script(season: dict, chunks: list[dict], *, model: str,
                    n_scenes: int = 5, feedback: str = "") -> ReelScript:
    """One structured Opus call -> a validated ReelScript grounded in `chunks`.

    Fails loud if the model returns the wrong scene count or cites a chunk_id that was
    not supplied (groundedness violation)."""
    if not chunks:
        raise ValueError("no chunks supplied — refusing to generate ungrounded content")

    context = _format_context(chunks)
    user = (
        f"SEASON: {season['label']} (key={season['key']}).\n"
        f"Aggravated dosha: {season['aggravated_dosha']}.\n"
        f"Common ailments this season: {', '.join(season['ailments'])}.\n"
        f"Remedy angle: {season['remedy_angle']}.\n\n"
        f"CONTEXT (ground all claims only in these passages):\n{context}\n\n"
        f"Write a reel script with EXACTLY {n_scenes} scenes for this season. "
        f"Each scene needs an English voiceover, a faithful Kannada voiceover, an image "
        f"prompt (no people/faces/text), and the grounded_chunk_ids it relies on."
        + (f"\n\nDoctor feedback to incorporate: {feedback}" if feedback else "")
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                 base_url=config.ANTHROPIC_BASE_URL)
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=ReelScript,
    )
    script = resp.parsed_output
    if script is None:
        raise RuntimeError(f"model returned no parsed output (stop_reason="
                           f"{resp.stop_reason})")

    if len(script.scenes) != n_scenes:
        raise ValueError(f"expected {n_scenes} scenes, model returned {len(script.scenes)}")

    supplied = {c["chunk_id"] for c in chunks}
    for i, scene in enumerate(script.scenes, 1):
        bad = [cid for cid in scene.grounded_chunk_ids if cid not in supplied]
        if bad:
            raise ValueError(f"scene {i} cites chunk_ids not in supplied context: {bad}")
    return script


def main() -> int:
    # Debug self-test: full retrieve -> generate for the current season.
    from datetime import date

    from ayurpost.pipeline.season import select_season
    from ayurpost.retrieval.search import HybridRetriever

    season = select_season(date.today())
    query = (f"{season['label']} season — {season['aggravated_dosha']} aggravation: "
             f"{', '.join(season['ailments'])}; remedy: {season['remedy_angle']}")
    print(f"season={season['key']}  query={query!r}")
    hits = HybridRetriever().search(query, limit=6, doshas=season["aggravated_dosha"])
    chunks = [h.payload for h in hits]
    print(f"retrieved {len(chunks)} chunks: {[c['chunk_id'] for c in chunks]}")

    script = generate_script(season, chunks, model=config.LLM_MODEL, n_scenes=5)
    print(script.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
