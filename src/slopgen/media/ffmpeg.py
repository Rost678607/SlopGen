"""Thin ffmpeg/ffprobe wrappers: segment building, concat and final composition."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config.models import GlobalConfig


class FFmpegError(Exception):
    pass


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"{' '.join(cmd[:2])} failed:\n{proc.stderr[-2000:]}")


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path}")
    return json.loads(proc.stdout)


def duration_of(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def video_dims(path: Path) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise FFmpegError(f"no video stream in {path}")


VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
AENC = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]


def _vf_fit(cfg: GlobalConfig) -> str:
    v = cfg.video
    return (
        f"scale={v.width}:{v.height}:force_original_aspect_ratio=increase,"
        f"crop={v.width}:{v.height},setsar=1,fps={v.fps}"
    )


def stretch_audio(src: Path, dst: Path, tempo: float) -> None:
    """Time-stretch an audio file by `tempo` (atempo): >1 speeds up, <1 slows down.
    Used by the AI-drama sync to fit a scene's voiceover to its generated clip."""
    _run(["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={tempo:.4f}", "-vn", str(dst)])


def make_video_part(clip: Path, dur: float, out: Path, cfg: GlobalConfig, start: float = 0.0) -> None:
    """Silent background piece: loop the clip to `dur`, crop to vertical.
    `start` seeks into the (looped) clip — continuous mode passes each scene's
    running offset so the action carries over instead of restarting."""
    seek = ["-ss", f"{start:.3f}"] if start > 0 else []
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip), *seek,
        "-vf", _vf_fit(cfg), "-an", *VENC, "-t", f"{dur:.3f}", str(out),
    ])


ZOOM = {"none": 0.0, "subtle": 0.09, "strong": 0.18}


def make_photo_part(img: Path, dur: float, out: Path, cfg: GlobalConfig, motion: str = "subtle", direction: int = 0) -> None:
    """Ken-Burns photo piece: slow zoom in/out (alternating by `direction`)."""
    v = cfg.video
    frames = max(int(dur * v.fps), 1)
    z = ZOOM.get(motion, 0.09)
    if z == 0:
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(img),
            "-vf", _vf_fit(cfg), "-an", *VENC, "-t", f"{dur:.3f}", str(out),
        ])
        return
    zoom = (
        f"min(1+{z}*on/{frames},{1 + z})" if direction % 2 == 0
        else f"max({1 + z}-{z}*on/{frames},1)"
    )
    # upscale 2x before zoompan to avoid sub-pixel jitter
    _run([
        "ffmpeg", "-y", "-i", str(img),
        "-filter_complex",
        f"[0:v]scale={v.width * 2}:{v.height * 2}:force_original_aspect_ratio=increase,"
        f"crop={v.width * 2}:{v.height * 2},"
        f"zoompan=z='{zoom}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
        f":d={frames}:s={v.width}x{v.height}:fps={v.fps},setsar=1[v]",
        "-map", "[v]", "-an", *VENC, "-frames:v", str(frames), str(out),
    ])


def make_scene_segment(
    bg_parts: list[Path],
    audio: Path,
    dur: float,
    out: Path,
    cfg: GlobalConfig,
    fg_inserts: list[tuple[Path, float, float, bool]] = (),  # (path, start, duration, is_video)
    fg_width: int = 840,
    fg_y: str = "(H-h)/2",
    tmp: Path | None = None,
) -> None:
    """Compose one scene: pre-built background parts + voiceover + foreground inserts."""
    if len(bg_parts) == 1:
        bg = bg_parts[0]
    else:
        bg = (tmp or out.parent) / (out.stem + "_bg.mp4")
        concat(bg_parts, bg)

    if not fg_inserts:
        _run([
            "ffmpeg", "-y", "-i", str(bg), "-i", str(audio),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", *AENC,
            "-t", f"{dur:.3f}", str(out),
        ])
        return

    cmd = ["ffmpeg", "-y", "-i", str(bg), "-i", str(audio)]
    for path, _, _, is_video in fg_inserts:
        # loop stills forever; loop short video clips so they fill their window
        cmd += (["-stream_loop", "-1", "-i", str(path)] if is_video
                else ["-loop", "1", "-i", str(path)])
    filters = []
    vtag = "[0:v]"
    for i, (_, start, fdur, _is_video) in enumerate(fg_inserts):
        # white border frame around the insert
        filters.append(f"[{i + 2}:v]scale={fg_width}:-1,pad=iw+16:ih+16:8:8:white,setsar=1[fg{i}]")
        filters.append(
            f"{vtag}[fg{i}]overlay=x=(W-w)/2:y={fg_y}"
            f":enable='between(t,{start:.2f},{start + fdur:.2f})'[v{i}]"
        )
        vtag = f"[v{i}]"
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vtag, "-map", "1:a", *VENC, *AENC,
        "-t", f"{dur:.3f}", str(out),
    ]
    _run(cmd)


def concat(segments: list[Path], out: Path) -> None:
    listfile = out.with_suffix(".txt")
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out)])
    listfile.unlink()


@dataclass
class OverlaySpec:
    asset: Path
    width: int
    position: str  # top_left | top_right | bottom_left | bottom_right
    start_s: float
    duration_s: float
    text: str = ""


def _overlay_xy(position: str, margin: int = 40, top: int = 140, bottom: int = 420) -> tuple[str, str]:
    return {
        "top_left": (f"{margin}", f"{top}"),
        "top_right": (f"W-w-{margin}", f"{top}"),
        "bottom_left": (f"{margin}", f"H-h-{bottom}"),
        "bottom_right": (f"W-w-{margin}", f"H-h-{bottom}"),
    }[position]


def _overlay_input_args(asset: Path) -> list[str]:
    ext = asset.suffix.lower()
    if ext == ".gif":
        return ["-ignore_loop", "0", "-i", str(asset)]
    if ext == ".webm":
        # libvpx decoder keeps the alpha channel
        return ["-stream_loop", "-1", "-c:v", "libvpx-vp9", "-i", str(asset)]
    if ext in (".png", ".jpg", ".jpeg"):
        return ["-loop", "1", "-i", str(asset)]
    return ["-stream_loop", "-1", "-i", str(asset)]


def finalize(
    concat_mp4: Path,
    out: Path,
    cfg: GlobalConfig,
    ass: Path | None = None,
    music: Path | None = None,
    overlay: OverlaySpec | None = None,
    fonts_dir: Path | None = None,
) -> None:
    """Final pass: burn subtitles, mix background music, stamp the ad overlay."""
    cmd = ["ffmpeg", "-y", "-i", str(concat_mp4)]
    n = 1
    music_idx = overlay_idx = -1
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx, n = n, n + 1
    if overlay:
        cmd += _overlay_input_args(overlay.asset)
        overlay_idx, n = n, n + 1

    filters: list[str] = []
    vtag = "[0:v]"
    if ass:
        sub = f"ass={ass}" + (f":fontsdir={fonts_dir}" if fonts_dir else "")
        filters.append(f"{vtag}{sub}[vs]")
        vtag = "[vs]"
    if overlay:
        x, y = _overlay_xy(overlay.position)
        en = f"between(t,{overlay.start_s},{overlay.start_s + overlay.duration_s})"
        filters.append(f"[{overlay_idx}:v]scale={overlay.width}:-1[adov]")
        filters.append(f"{vtag}[adov]overlay=x={x}:y={y}:shortest=1:enable='{en}'[vo]")
        vtag = "[vo]"
        if overlay.text:
            # ad caption pinned under the overlay corner
            aw, ah = video_dims(overlay.asset)
            ty = f"{int(140 + overlay.width * ah / aw + 14)}" if overlay.position.startswith("top") else f"h-{420 - 14}"
            tx = "40" if overlay.position.endswith("left") else "w-text_w-40"
            text = overlay.text.replace("'", r"\'").replace(":", r"\:")
            # expansion=none: literal text ('%' breaks the default expansion mode)
            filters.append(
                f"{vtag}drawtext=text='{text}':expansion=none:font='{cfg.subtitles.font}':fontsize=44:"
                f"fontcolor=white:borderw=3:bordercolor=black:x={tx}:y={ty}:enable='{en}'[vt]"
            )
            vtag = "[vt]"

    filters.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice]")
    atag = "[voice]"
    if music:
        filters.append(f"[{music_idx}:a]volume={cfg.audio.music_volume}[bgm]")
        filters.append(f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0[mix]")
        atag = "[mix]"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vtag, "-map", atag,
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    _run(cmd)
