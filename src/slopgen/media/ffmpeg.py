"""Thin ffmpeg/ffprobe wrappers: segment building, concat and final composition."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config.models import GlobalConfig
from .filters import graph as filter_graph


class FFmpegError(Exception):
    def __init__(self, message: str, signal: int = 0):
        super().__init__(message)
        # non-zero when ffmpeg was killed outright instead of reporting a fault,
        # which is how running out of memory arrives. Callers that can retry
        # smaller (see _fold_segments) tell the two apart by this.
        self.signal = signal


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    detail = proc.stderr[-2000:]
    if proc.returncode < 0:
        # Killed by a signal, so ffmpeg never got to print a reason. Usually the
        # OOM killer on a heavy filtergraph.
        detail = f"killed by signal {-proc.returncode} (out of memory?)\n{detail}"
    raise FFmpegError(f"{' '.join(cmd[:2])} failed:\n{detail}", max(0, -proc.returncode))


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


def make_video_part(clip: Path, dur: float, out: Path, cfg: GlobalConfig, start: float = 0.0,
                    speed: float = 1.0) -> None:
    """Silent background piece: fit the clip to `dur`, crop to vertical.

    `start` seeks into the clip — continuous mode passes each scene's running offset
    so the action carries over instead of restarting. `speed` retimes the clip
    (>1 faster, <1 slower); the drama sync uses it to make a clip and its voiceover
    the same length instead of looping the clip back to its start half way through.
    The stream still loops as a last resort, for whatever the retime could not cover."""
    seek = ["-ss", f"{start:.3f}"] if start > 0 else []
    vf = _vf_fit(cfg)
    if abs(speed - 1.0) > 0.01:
        vf = f"setpts={1 / speed:.4f}*PTS," + vf
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip), *seek,
        "-vf", vf, "-an", *VENC, "-t", f"{dur:.3f}", str(out),
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

    # Pad the voiceover with trailing silence (apad) then bound the whole segment
    # to `dur` with -t, so the audio spans the full scene with no gap and the
    # segment can't run long (looped inserts / apad are otherwise unbounded). The
    # audio may still be a frame off the frame-quantised video here — that residual
    # is re-timed away when finalize concatenates the scenes with the concat filter.
    if not fg_inserts:
        _run([
            "ffmpeg", "-y", "-i", str(bg), "-i", str(audio),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", *AENC,
            "-af", "apad", "-t", f"{dur:.3f}", str(out),
        ])
        return

    cmd = ["ffmpeg", "-y", "-i", str(bg), "-i", str(audio)]
    for path, _, _, is_video in fg_inserts:
        # loop stills forever; loop short video clips so they fill their window
        cmd += (["-stream_loop", "-1", "-i", str(path)] if is_video
                else ["-loop", "1", "-i", str(path)])
    filters = ["[1:a]apad[aout]"]
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
        "-map", vtag, "-map", "[aout]", *VENC, *AENC,
        "-t", f"{dur:.3f}", str(out),
    ]
    _run(cmd)


def concat(segments: list[Path], out: Path) -> None:
    """Stream-copy join of pre-built parts (concat demuxer). Used only for the
    SILENT background pieces of one scene, where copy is exact and cheap. The
    audio-bearing join of whole scenes is done in :func:`finalize` with the concat
    filter instead — copy-concat of separate AAC pieces drifts (see there)."""
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


# --- joining scenes in batches ---------------------------------------------
#
# ffmpeg reads ahead on every input of a filtergraph in parallel, but the concat
# filter consumes them strictly in order, so every scene still waiting its turn
# sits in memory decoded. The bill is one read-ahead buffer per waiting scene,
# which scales with the frame size and not with the encode settings, so joining a
# long video in a single pass needs gigabytes and ends with the OOM killer.
#
# Scenes are therefore folded together in batches, sized from what this machine
# actually has free right now. The estimate below is a starting point, not a
# promise: a pass that gets killed anyway halves the batch and tries again, so a
# wrong guess costs one pass rather than the whole run.

# read-ahead buffered per waiting input, in frames. Measured at ~110 MB per
# 1080x1920 input, i.e. about this many raw yuv420p frames.
CONCAT_READAHEAD_FRAMES = 40
# what a pass needs before any input is counted: filters, encoder, lookahead. The
# delivery pass pays for libass' glyph cache, loudnorm's window and a slower
# x264 preset, so it can afford noticeably fewer open inputs than a fold pass.
FOLD_PASS_OVERHEAD = 1_600_000_000
DELIVERY_PASS_OVERHEAD = 3_400_000_000
# share of free memory a join may claim. Swap is deliberately left out of the
# budget: a filtergraph that spills into it thrashes long before it finishes.
MEMORY_HEADROOM = 0.6
CONCAT_MIN_INPUTS = 2
CONCAT_MAX_INPUTS = 48  # past this the command line, not the memory, is the problem
CONCAT_DEFAULT_INPUTS = 12  # fallback when free memory can't be read


def _free_memory() -> int:
    """Bytes the kernel reports as available right now, 0 if it will not say."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _concat_capacity(cfg: GlobalConfig, overhead: int) -> int:
    """How many inputs one concat pass can hold open here and now: what is left of
    the memory budget once the pass's own filters and encoder are paid for, over
    what one waiting input costs at this frame size."""
    free = _free_memory()
    if not free:
        return CONCAT_DEFAULT_INPUTS
    frame = cfg.video.width * cfg.video.height * 3 // 2  # yuv420p
    spare = int(free * MEMORY_HEADROOM) - overhead
    fits = spare // (frame * CONCAT_READAHEAD_FRAMES)
    return max(CONCAT_MIN_INPUTS, min(CONCAT_MAX_INPUTS, fits))


def _join_total(n: int, batch: int, target: int) -> int:
    """How many join passes it takes to fold `n` files down to `target`, joining
    `batch` of them at a time. Mirrors the loop in :func:`_fold_segments`."""
    total = 0
    while n > target:
        folded = k = 0
        while k < n:
            group = min(batch, n - k)
            if group == 1:  # a lone tail file is carried, not joined
                folded += 1
                break
            total += 1
            folded += 1
            k += group
        n = folded
    return total


def _concat_pass(segments: list[Path], out: Path) -> None:
    """Join `segments` with the concat filter onto one continuous clock. This is
    plumbing between :func:`finalize`'s batches, so it stays visually transparent
    and leaves the audio uncompressed — only finalize encodes for delivery."""
    cmd: list[str] = ["ffmpeg", "-y"]
    for seg in segments:
        cmd += ["-i", str(seg)]
    n = len(segments)
    concat_in = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    _run(cmd + [
        "-filter_complex",
        f"{concat_in}concat=n={n}:v=1:a=1[cv][ca];"
        "[cv]setpts=PTS-STARTPTS[v];[ca]asetpts=PTS-STARTPTS[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "pcm_s16le",
        str(out),
    ])


def _fold_segments(
    segments: list[Path],
    tmp: Path,
    prefix: str,
    batch: int,
    target: int,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[list[Path], int]:
    """Join `segments` `batch` at a time until at most `target` are left for the
    delivery pass to hold open. A fold pass carries none of that pass's filters,
    so it affords a bigger batch — which is the point of the two numbers: folding
    wide keeps the tree one level deep, and every level is a re-encode.

    Every batch is itself a concat-filter join, so the re-timing that
    :func:`finalize` relies on carries through the folding.

    A batch the machine could not afford after all comes back killed rather than
    failed; that halves it and retries, so the fold settles on a size that works
    instead of taking the run down. Returns the files left to join and the batch
    size that survived, for the caller to reuse."""
    current, level = list(segments), 0
    done, total = 0, _join_total(len(segments), batch, target)
    while len(current) > target:
        folded, k = [], 0
        while k < len(current):
            group = current[k:k + batch]
            if len(group) == 1:  # odd one out: carry it to the next round as is
                folded.append(group[0])
                break
            part = tmp / f"{prefix}_join{level}_{len(folded):02d}.mkv"
            while True:
                try:
                    _concat_pass(group, part)
                    break
                except FFmpegError as e:
                    if not e.signal or len(group) <= CONCAT_MIN_INPUTS:
                        raise
                    batch = max(CONCAT_MIN_INPUTS, len(group) // 2)
                    group = current[k:k + batch]
                    total = done + _join_total(len(current) - k + len(folded), batch, target)
            folded.append(part)
            k += len(group)
            done += 1
            if on_progress:
                on_progress("join", done, max(done, total))
        current, level = folded, level + 1
    return current, batch


def _delivery_cmd(
    segments: list[Path],
    out: Path,
    cfg: GlobalConfig,
    ass: Path | None,
    music: Path | None,
    overlay: OverlaySpec | None,
    fonts_dir: Path | None,
    fx: dict[str, int] | None = None,
) -> list[str]:
    """The one delivery pass: join what is left, run the montage filters over the
    whole picture, burn subtitles, mix background music, stamp the ad overlay, encode."""
    cmd: list[str] = ["ffmpeg", "-y"]
    for seg in segments:
        cmd += ["-i", str(seg)]
    n = len(segments)
    music_idx = overlay_idx = -1
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx, n = n, n + 1
    if overlay:
        cmd += _overlay_input_args(overlay.asset)
        overlay_idx, n = n, n + 1

    filters: list[str] = []
    # concat every scene into one continuous, re-timed pair of streams.
    concat_in = "".join(f"[{i}:v][{i}:a]" for i in range(len(segments)))
    filters.append(f"{concat_in}concat=n={len(segments)}:v=1:a=1[cv][ca]")
    # rebase the picture to PTS 0 so it starts exactly with the audio (a B-frame
    # reorder delay otherwise leaves the video a frame behind, which players honour
    # inconsistently and read as a constant A/V offset).
    filters.append("[cv]setpts=PTS-STARTPTS[vbase]")
    # The montage look (grain, CRT, VHS, glitch — see media/filters) goes on HERE,
    # which is both the first moment the video exists as one continuous picture and
    # the last one before anything meant to be READ is drawn onto it. Subtitles and
    # the ad overlay follow, and stay out of the effect: hash over a caption costs
    # legibility for nothing, and a partner's logo is not ours to run through a tube.
    filters.extend(filter_graph(fx or {}, cfg, "[vbase]", "[vfx]"))
    vtag = "[vfx]"
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

    # anchor the audio to PTS 0 so it starts exactly with the picture.
    filters.append("[ca]asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11[voice]")
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
        # end on the shorter stream so the delivered file has audio and video of
        # equal length — no trailing stream that players stretch or free-run.
        "-shortest",
        "-movflags", "+faststart",
        str(out),
    ]
    return cmd


def finalize(
    segments: list[Path],
    out: Path,
    cfg: GlobalConfig,
    ass: Path | None = None,
    music: Path | None = None,
    overlay: OverlaySpec | None = None,
    fonts_dir: Path | None = None,
    fx: dict[str, int] | None = None,
    tmp: Path | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> None:
    """Join the scene ``segments``, lay the montage filters over the picture, burn
    subtitles, mix background music and stamp the ad overlay onto the delivered file.

    ``fx`` is the run's filters as ``{name: dose}`` (see :mod:`.filters`). They are
    applied in this pass and nowhere else, so they cover the finished video end to
    end — and, since a part is finalized on its own, every episode of a serial carries
    the same look for its whole length.

    The join uses the concat *filter*, not the concat demuxer: the demuxer
    stream-copies and merely re-stamps each piece's timestamps, so per-scene
    audio/video length mismatches (frame vs. AAC-frame quantisation) and each
    piece's encoder delay pile up into growing drift and, at a join, an abrupt
    cut where the next scene's sound starts against the previous scene's tail.
    The filter decodes every piece and re-times them onto one continuous clock,
    so audio and video stay locked end-to-end regardless of per-piece rounding.

    Only so many scenes fit in one such pass, though (see :func:`_fold_segments`),
    so a long video is folded down in batches first and the last pass does the
    delivery encode. If even that pass is killed for its size, the fold tightens
    and it is retried, so the join adapts to the machine instead of dying on it."""
    if not segments:
        raise FFmpegError("finalize: no segments to assemble")
    tmp = tmp or out.parent
    target = _concat_capacity(cfg, DELIVERY_PASS_OVERHEAD)
    batch = max(_concat_capacity(cfg, FOLD_PASS_OVERHEAD), target)
    attempt = 0
    while True:
        ready, batch = _fold_segments(segments, tmp, f"{out.stem}_p{attempt}", batch, target, on_progress)
        try:
            _run(_delivery_cmd(ready, out, cfg, ass, music, overlay, fonts_dir, fx))
            return
        except FFmpegError as e:
            if not e.signal or len(ready) <= CONCAT_MIN_INPUTS:
                raise
            # killed for its size after all: fold what is left down further
            segments, attempt = ready, attempt + 1
            target = max(CONCAT_MIN_INPUTS, len(ready) // 2)
            batch = max(batch, target)
