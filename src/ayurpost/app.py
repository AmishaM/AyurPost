"""
AyurPost — Doctor Review App

Monthly calendar of generated DRAFT reels. Click a day to open the reel
in a side panel with a chat box for voiceover edits (script only, no new Veo).
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

import os

from ayurpost import config

GENERATED_DIR = Path(os.environ.get("GENERATED_DIR", str(config.DATA_DIR / "generated")))
DRAFT_MARKER = "DRAFT"

PILLAR_COLOURS = {
    "seasonal":        "#2e7d32",   # green
    "dosha_education": "#1565c0",   # blue
    "clinic_services": "#e65100",   # orange
}

# ── Artifact discovery ────────────────────────────────────────────────────────

def _reel_path(artifact_dir: Path) -> Path | None:
    for name in ("reel.mp4",):
        p = artifact_dir / name
        if p.exists():
            return p
    return None


def load_drafts() -> list[dict]:
    drafts = []
    if not GENERATED_DIR.exists():
        return drafts
    for d in sorted(GENERATED_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest_p = d / "manifest.json"
        script_p   = d / "script.json"
        reel_p     = _reel_path(d)
        if not (manifest_p.exists() and script_p.exists() and reel_p):
            continue
        try:
            manifest = json.loads(manifest_p.read_text())
        except Exception:
            continue
        if DRAFT_MARKER not in manifest.get("status", ""):
            continue
        gen_at = manifest.get("generated_at", "")
        try:
            gen_date = datetime.fromisoformat(gen_at).date()
        except Exception:
            continue
        pillar = manifest.get("pillar", "seasonal")
        # derive label
        if pillar == "seasonal":
            label = manifest.get("season", {}).get("label", d.name)
        elif pillar == "dosha_education":
            label = manifest.get("label", d.name)
        elif pillar == "clinic_services":
            label = manifest.get("label", d.name)
        else:
            label = d.name
        drafts.append({
            "date":     gen_date,
            "pillar":   pillar,
            "label":    label,
            "dir":      d,
            "reel":     reel_p,
            "script":   script_p,
            "manifest": manifest,
            "id":       d.name,
        })
    return drafts


# ── Calendar render ───────────────────────────────────────────────────────────

def render_calendar(year: int, month: int, by_date: dict[date, list[dict]]) -> None:
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cols = st.columns(7)
    for i, name in enumerate(day_names):
        cols[i].markdown(f"**{name}**")

    first_weekday, n_days = calendar.monthrange(year, month)
    # pad to Monday-start
    cells = [None] * first_weekday + list(range(1, n_days + 1))
    # pad to full rows
    while len(cells) % 7:
        cells.append(None)

    for row_start in range(0, len(cells), 7):
        week = cells[row_start: row_start + 7]
        cols = st.columns(7)
        for col, day in zip(cols, week):
            if day is None:
                col.write("")
                continue
            d = date(year, month, day)
            drafts_today = by_date.get(d, [])
            if drafts_today:
                draft = drafts_today[0]
                colour = PILLAR_COLOURS.get(draft["pillar"], "#555")
                label  = draft["label"][:20]
                col.markdown(
                    f'<div style="background:{colour};border-radius:6px 6px 0 0;'
                    f'padding:3px 6px;color:white;font-size:11px">'
                    f'<b>{day}</b> · {label}</div>',
                    unsafe_allow_html=True,
                )
                col.video(str(draft["reel"]))
                if col.button("💬 Edit", key=f"chat_{draft['id']}_{day}",
                              use_container_width=True):
                    st.session_state.selected = draft["id"]
            else:
                col.markdown(
                    f'<div style="border:1.5px dashed #ddd;border-radius:6px;'
                    f'padding:8px 6px 6px 6px;min-height:140px;'
                    f'display:flex;flex-direction:column;justify-content:space-between">'
                    f'<span style="color:#bbb;font-size:12px;font-weight:600">{day}</span>'
                    f'<div style="text-align:center;color:#ccc;font-size:11px;'
                    f'padding:20px 0">🎬<br>Coming soon</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ── Side panel ────────────────────────────────────────────────────────────────

def render_panel(draft: dict) -> None:
    st.subheader(draft["label"])
    st.caption(f"{draft['pillar']} · {draft['date']}")

    # script summary
    try:
        script = json.loads(draft["script"].read_text())
    except Exception:
        st.warning("Could not load script.json")
        return

    with st.expander("Current script", expanded=True):
        st.markdown(f"**Hook:** {script.get('hook','')}")
        for i, scene in enumerate(script.get("scenes", []), 1):
            st.markdown(f"**Scene {i}:** {scene.get('voiceover_en','')}")

    # chat
    st.divider()
    st.markdown("**Doctor notes / edits**")
    chat_key = f"chat_{draft['id']}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    prompt = st.text_area("Request a change to the voiceover…",
                          key=f"input_{draft['id']}", height=80, label_visibility="collapsed",
                          placeholder="e.g. Make the tone warmer and more personal")
    if st.button("Update voiceover", key=f"submit_{draft['id']}", use_container_width=True,
                 type="primary") and prompt:
        st.session_state[chat_key].append({"role": "user", "content": prompt})
        with st.spinner("Rewriting voiceover…"):
            try:
                result = _regenerate(draft, script, prompt)
                st.session_state[chat_key].append(
                    {"role": "assistant", "content": result})
            except Exception as exc:
                st.session_state[chat_key].append(
                    {"role": "assistant", "content": f"Error: {exc}"})
        st.rerun()


# ── Regeneration ─────────────────────────────────────────────────────────────

def _regenerate(draft: dict, script: dict, feedback: str) -> str:
    """Re-run script generation with feedback, keep Veo clips, new TTS, reassemble."""
    from ayurpost.pipeline.voice import synthesize_en_chirp
    from ayurpost.pipeline.assemble import build_veo_reel

    manifest = draft["manifest"]
    pillar   = draft["pillar"]
    n_scenes = len(script.get("scenes", []))
    model    = manifest.get("models", {}).get("generation", config.LLM_MODEL)

    # Fetch the same chunks used originally
    retrieved = manifest.get("retrieved_chunks", [])
    chunk_ids = [r["chunk_id"] for r in retrieved]

    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchAny
    qclient = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
    results, _ = qclient.scroll(
        collection_name=config.QDRANT_COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="chunk_id", match=MatchAny(any=chunk_ids))
        ]),
        with_payload=True, limit=len(chunk_ids) + 5,
    )
    chunks = [r.payload for r in results]
    if not chunks:
        raise ValueError("could not reload original chunks from Qdrant")

    # Generate new script with feedback
    if pillar == "seasonal":
        from ayurpost.pipeline.content import generate_script
        from ayurpost.pipeline.season import _load_seasons
        season_key = manifest.get("season", {}).get("key", "")
        season = next((s for s in _load_seasons() if s["key"] == season_key),
                      manifest.get("season", {}))
        new_script = generate_script(season, chunks, model=model,
                                     n_scenes=n_scenes, feedback=feedback)
    elif pillar == "dosha_education":
        from ayurpost.pipeline.generate_dosha import generate_dosha_script, _load_dosha_topic
        dosha_key  = manifest.get("dosha")
        topic_type = manifest.get("topic")
        dosha, topic = _load_dosha_topic(dosha_key, topic_type)
        new_script = generate_dosha_script(dosha, topic, chunks, model, n_scenes, feedback)
    elif pillar == "clinic_services":
        from ayurpost.pipeline.generate_services import generate_services_script, _load_service
        service = _load_service(manifest.get("service"))
        new_script = generate_services_script(service, chunks, model, n_scenes, feedback)
    else:
        raise ValueError(f"unknown pillar: {pillar}")

    artifact_dir = draft["dir"]

    # Persist new script.json
    artifact_dir.joinpath("script.json").write_text(
        new_script.model_dump_json(indent=2), encoding="utf-8")

    # New TTS (keep existing Veo clips)
    texts = [new_script.hook] + [s.voiceover_en for s in new_script.scenes]
    audio_paths = synthesize_en_chirp(texts, artifact_dir)

    # Resolve existing Veo clip paths from manifest
    clip_names = manifest.get("artifacts", {}).get("clips", [])
    clip_paths = [artifact_dir / name for name in clip_names
                  if (artifact_dir / name).exists()]
    if not clip_paths:
        raise ValueError("no Veo clips found in artifact dir — cannot reassemble")

    # Reassemble
    music_path = manifest.get("music")
    music = Path(music_path) if music_path and Path(music_path).exists() else None
    build_veo_reel(clip_paths, audio_paths, artifact_dir / "reel.mp4",
                   music=music, music_ss=0)

    # Update manifest with chat history
    manifest.setdefault("chat_history", []).append({
        "feedback":    feedback,
        "updated_at":  datetime.now(timezone.utc).isoformat(),
        "model":       model,
    })
    artifact_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    return (f"Done. New voiceover:\n\n"
            f"**Hook:** {new_script.hook}\n\n"
            + "\n\n".join(f"**Scene {i}:** {s.voiceover_en}"
                          for i, s in enumerate(new_script.scenes, 1)))


# ── App ───────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(page_title="AyurPost — Review", layout="wide")
    st.title("AyurPost · Doctor Review")

    # Month navigation
    today = date.today()
    if "cal_year"  not in st.session_state:
        st.session_state.cal_year  = today.year
    if "cal_month" not in st.session_state:
        st.session_state.cal_month = today.month
    if "selected"  not in st.session_state:
        st.session_state.selected  = None

    nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
    with nav_col1:
        if st.button("← Prev"):
            m = st.session_state.cal_month - 1
            if m < 1:
                m, st.session_state.cal_year = 12, st.session_state.cal_year - 1
            st.session_state.cal_month = m
            st.session_state.selected  = None
    with nav_col3:
        if st.button("Next →"):
            m = st.session_state.cal_month + 1
            if m > 12:
                m, st.session_state.cal_year = 1, st.session_state.cal_year + 1
            st.session_state.cal_month = m
            st.session_state.selected  = None
    with nav_col2:
        st.markdown(
            f"<h3 style='text-align:center'>"
            f"{calendar.month_name[st.session_state.cal_month]} "
            f"{st.session_state.cal_year}</h3>",
            unsafe_allow_html=True,
        )

    drafts = load_drafts()
    by_date: dict[date, list[dict]] = {}
    for d in drafts:
        by_date.setdefault(d["date"], []).append(d)

    # Layout: calendar left, panel right
    selected_id = st.session_state.selected
    selected    = next((d for d in drafts if d["id"] == selected_id), None)

    if selected:
        cal_col, panel_col = st.columns([2, 1])
    else:
        cal_col = st.container()
        panel_col = None

    with cal_col:
        render_calendar(st.session_state.cal_year,
                        st.session_state.cal_month, by_date)

    if selected and panel_col:
        with panel_col:
            if st.button("✕ Close"):
                st.session_state.selected = None
                st.rerun()
            render_panel(selected)

    # legend
    st.divider()
    leg = st.columns(3)
    for col, (pillar, colour) in zip(leg, PILLAR_COLOURS.items()):
        col.markdown(
            f'<span style="background:{colour};color:white;padding:2px 8px;'
            f'border-radius:4px;font-size:12px">{pillar.replace("_"," ")}</span>',
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
