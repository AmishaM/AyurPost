"""
Assemble per-scene images + voiceover MP3s into a single vertical reel MP4 (FFmpeg).

For each scene: hold its still image for exactly the duration of that scene's audio
(measured with ffprobe), scaled/padded to 1080x1920. Per-scene clips are then joined
with the concat demuxer and the audio muxed in. Output is H.264 (yuv420p) + AAC.

The two language reels share the same images but use their own audio (so a scene's
duration differs slightly between en and kn). build_reel() builds ONE reel; the
orchestrator calls it once per language.

Simple hard cuts between scenes (no crossfade) — xfade is a noted later enhancement.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

W, H = 1080, 1920

# scale to fit inside WxH preserving aspect, then pad to exactly WxH (letterbox).
_SCALE_PAD = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1")


def _run(cmd: list[str]) -> None:
    """Run a command, raising with stderr on failure (fail loud)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
                           f"{proc.stderr.strip()}")


def audio_duration(path: Path) -> float:
    """Audio length in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _scene_clip(image: Path, audio: Path, out_path: Path) -> None:
    """One scene: still image held for the audio's duration, 1080x1920, h264+aac."""
    dur = audio_duration(audio)
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-t", f"{dur:.3f}",
        "-vf", _SCALE_PAD,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out_path),
    ])


def build_reel(images: list[Path], audios: list[Path], out_path: Path) -> Path:
    """Build one reel MP4 from paired scene images + audios (must be equal length)."""
    if len(images) != len(audios):
        raise ValueError(f"images ({len(images)}) and audios ({len(audios)}) differ")
    if not images:
        raise ValueError("no scenes to assemble")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        clips: list[Path] = []
        for i, (img, aud) in enumerate(zip(images, audios)):
            clip = tmpdir / f"clip_{i}.mp4"
            _scene_clip(img, aud, clip)
            clips.append(clip)

        # concat demuxer needs a list file of absolute paths.
        listing = tmpdir / "clips.txt"
        listing.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy",
            str(out_path),
        ])
    return out_path


# ── Veo-based assembly (production pipeline) ─────────────────────────────────

def _video_duration(path: Path) -> float:
    """Video stream duration in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["streams"][0]["duration"])


def _xfade_concat(clips: list[Path], out: Path, xfade_dur: float = 0.5) -> Path:
    """Concat video clips with crossfade dissolves; re-encodes to h264."""
    if len(clips) == 1:
        import shutil
        shutil.copy2(clips[0], out)
        return out
    durs = [_video_duration(c) for c in clips]
    parts: list[str] = []
    prev, offset = "[0:v]", 0.0
    for i in range(1, len(clips)):
        offset += durs[i - 1] - xfade_dur
        label = f"[v{i}]" if i < len(clips) - 1 else "[vout]"
        parts.append(
            f"{prev}[{i}:v]xfade=transition=fade:"
            f"duration={xfade_dur}:offset={offset:.3f}{label}"
        )
        prev = f"[v{i}]"
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(out)]
    _run(cmd)
    return out


def build_veo_reel(
    clips: list[Path],
    audios: list[Path],
    out_path: Path,
    music: Path | None = None,
    music_ss: int = 0,
    xfade_dur: float = 0.5,
) -> Path:
    """Build one reel from Veo video clips + TTS audios + optional music bed.

    Voice plays over the opening clips; music (if supplied, ducked to 15%) continues
    after the voice ends and fades out as the last clip finishes. No abrupt cuts.

    Args:
        clips:     Ordered Veo MP4 clips (hook, scene_0, scene_1, ...).
        audios:    Ordered TTS MP3s aligned with clips.
        out_path:  Destination MP4.
        music:     Optional background music track.
        music_ss:  Start offset (seconds) within the music file.
        xfade_dur: Crossfade duration between clips (seconds).
    """
    if len(clips) != len(audios):
        raise ValueError(f"clips ({len(clips)}) and audios ({len(audios)}) must match")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # 1. Crossfade-concat video clips
        joined_vid = tmpdir / "joined.mp4"
        _xfade_concat(clips, joined_vid, xfade_dur)
        vid_dur = _video_duration(joined_vid)

        # 2. Concat TTS audios
        al = tmpdir / "aud.txt"
        al.write_text("".join(f"file '{a}'\n" for a in audios), encoding="utf-8")
        vo = tmpdir / "vo.mp3"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", str(al), "-c", "copy", str(vo)])
        vo_dur = audio_duration(vo)

        out_path.parent.mkdir(parents=True, exist_ok=True)

        if music is None:
            # Voice only — extend video to full audio duration if needed, then fade out.
            out_dur = max(vid_dur, vo_dur)
            fade_st = max(0.0, out_dur - 2.5)
            _run([
                "ffmpeg", "-y",
                "-i", str(joined_vid),
                "-i", str(vo),
                "-filter_complex",
                "[0:v]tpad=stop_mode=clone:stop_duration=10[vext];"
                f"[1:a]afade=t=out:st={fade_st:.2f}:d=2.5[aout]",
                "-map", "[vext]", "-map", "[aout]",
                "-t", f"{out_dur:.3f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
                "-c:a", "aac", "-b:a", "128k",
                str(out_path),
            ])
        else:
            # Voice + music bed: voice leads, music continues to end of video, then fades.
            out_dur  = max(vid_dur, vo_dur)
            fade_st  = max(0.0, vo_dur - 0.5)
            fade_dur = max(2.0, out_dur - fade_st)
            _run([
                "ffmpeg", "-y",
                "-i", str(joined_vid),
                "-i", str(vo),
                "-ss", str(music_ss), "-t", str(int(out_dur) + 4), "-i", str(music),
                "-filter_complex",
                "[0:v]tpad=stop_mode=clone:stop_duration=10[vext];"
                "[2:a]volume=0.15[bed];"
                "[1:a][bed]amix=inputs=2:duration=longest:dropout_transition=2[mix];"
                f"[mix]afade=t=out:st={fade_st:.2f}:d={fade_dur:.2f}[aout]",
                "-map", "[vext]", "-map", "[aout]",
                "-t", f"{out_dur:.3f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
                "-c:a", "aac", "-b:a", "128k",
                str(out_path),
            ])
    return out_path
