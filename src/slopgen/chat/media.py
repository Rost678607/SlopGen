"""What a message carries besides its words: pictures, stickers, clips, voices.

A chat is not only text, and the export proves it — half of what people send each
other is a sticker, a photo with nothing under it, a voice message, a round video of
their own face. This module is everything the rest of the package needs to know about
those files, and the one place that knows how to OPEN each of them, which is the hard
part: they come in six containers and three of them move.

**One base, not a file per run.** Whatever arrives — out of an export folder or
downloaded off a live account — is copied into `assets/chat_media/` under a name made
from where it came from, and a message stores that NAME. The same sticker sent forty
times in a year of somebody's chat is one file; the run that cuts the same export
three ways next month finds every picture already in place; and a job never points at
an export folder that its owner has since moved.

**A still and a clip, never one thing pretending to be the other.** The drawing of a
conversation is a PNG per state, and that is not going to change for a sticker — so
everything here can be asked for its FIRST frame (:func:`still`), which is what goes
into the drawing and what the preview shows. What moves is made separately, once, as
a short clip of exactly the size it is drawn at (:func:`clip`), and laid over the
still by the assembler at the same place and with the same scroll. Under the clip is
its own first frame, so a frame the clip has not reached yet is still the right
picture.

The three ways to open a moving thing are the three decoders the files actually
need. A `.tgs` is a gzipped Lottie animation — vectors, not pixels — and only a
Lottie renderer can draw it (rlottie, imported lazily so that a machine without it
still draws everything else, with the sticker's emoji in its place). A GIF, an
animated WebP or an APNG is Pillow's. Everything with a video codec in it is
ffmpeg's — and a video sticker is VP9 WITH AN ALPHA CHANNEL, which ffmpeg's own VP9
decoder silently drops, so those are opened through libvpx explicitly or every
sticker arrives on a black square.

Nothing here imports the pipeline, for the reason the rest of this package does not.
"""

from __future__ import annotations

import array
import hashlib
import io
import json
import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# The folder under `assets/` everything lands in.
DIR = "chat_media"

# What a message may carry. Exactly Telegram's own distinctions, because they are
# DRAWN differently, not merely named differently: a sticker has no bubble, a round
# video is a circle, a voice message is a waveform, and a GIF and a video are the same
# rectangle that one client loops and the other plays once.
KINDS = ("photo", "sticker", "animation", "video", "voice", "round")
# …and the ones that are a picture on the screen. A voice message is drawn too, but as
# a control rather than as what it holds.
VISUAL = {"photo", "sticker", "animation", "video", "round"}
# The ones heard rather than seen, whose own sound is the message (see
# `stages.chat_script.lay`). A round video is both, which is what it is on a phone.
AUDIBLE = {"voice", "round"}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
AUDIO_EXTS = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav"}
LOTTIE_EXTS = {".tgs"}

# The longest a moving thing is rendered for, before it loops. A sticker is three
# seconds and loops anyway; a ten-minute video attached to a message is on screen for
# four seconds of the video and has no business being transcoded whole.
CLIP_CAP_S = 12.0

# Where an export or a download says a file is not there. Telegram writes these
# sentences into the very field the path would have been in (see tdesktop's
# `export_output_json.cpp`), so they are the first thing a reader has to rule out.
NOT_INCLUDED = "(File "


def root(assets: Path) -> Path:
    return assets / DIR


def kind_of_path(path: str | Path) -> str:
    """A best guess at what a file is from its extension alone — for the formats
    that say nothing else, which is Telegram's HTML export and a hand-dropped file."""
    ext = Path(str(path)).suffix.lower()
    if ext in LOTTIE_EXTS:
        return "sticker"
    if ext in AUDIO_EXTS:
        return "voice"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "photo"
    return ""


def adopt(src: Path, base: Path) -> str:
    """Copy one file into the base and return the name it is stored under.

    The name is derived from WHERE it came from and how big it is, not from its
    bytes: hashing a two-hundred-megabyte video to look at a list of messages would
    make opening an export the slowest thing in the program. The same file read twice
    lands on the same name and is copied once."""
    if src.parent.resolve() == base.resolve():
        return src.name  # already in the base: a live download lands there directly
    st = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode())
    name = f"m_{key.hexdigest()[:16]}{src.suffix.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    at = base / name
    if not at.is_file():
        shutil.copyfile(src, at)
    return name


def adopt_pieces(pieces, base: Path) -> None:
    """Copy every file these pieces carry into the base, and fill in what the export
    did not say about it — its size and its length — by looking.

    In place, on the pieces a reader returned (`chat.exports.Piece`), and before any of
    them become a job's messages: past this point a message's media is a name in the
    base and nothing else. A file that will not copy costs that message its picture;
    a message left with no words and no picture is taken out, as the reader would have
    done had the file never been named."""
    for piece in pieces:
        keep = []
        for ln in piece.lines:
            m = ln.media
            if m is not None and not m.file:
                src = Path(m.path)
                try:
                    m.file = adopt(src, base)
                except OSError as e:
                    log.warning("chat: %s will not copy (%s)", src.name, e)
                    ln.media = m = None
            if m is not None and (not m.w or not m.h or not m.duration):
                w, h, dur, _moves = facts(base / m.file)
                m.w, m.h = m.w or w, m.h or h
                m.duration = m.duration or dur
            if ln.text.strip() or ln.media is not None:
                keep.append(ln)
        if len(keep) != len(piece.lines):
            # the replies point into the list by place, so a dropped line moves them
            where = {id(ln): n for n, ln in enumerate(keep)}
            old = piece.lines
            for ln in keep:
                target = old[ln.reply_to] if 0 <= ln.reply_to < len(old) else None
                ln.reply_to = where.get(id(target), -1) if target is not None else -1
            piece.lines = keep


def resolve(base: Path, name: str) -> Path | None:
    """A stored name as a file, inside the base and nowhere else. The name may come
    off a form, and a message is not a place to read `../../.env` from."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    at = base / name
    return at if at.is_file() else None


# --------------------------------------------------------------------------
# what a file is
# --------------------------------------------------------------------------


def _decoder(path: Path) -> str:
    """Which of the three ways this file is opened: `lottie`, `pillow` or `ffmpeg`."""
    ext = path.suffix.lower()
    if ext in LOTTIE_EXTS:
        return "lottie"
    if ext in IMAGE_EXTS:
        return "pillow"
    return "ffmpeg"


def _ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
         "-show_streams", str(path)], capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError:
        return {}


def _video_stream(info: dict) -> dict:
    return next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"
                 and not (s.get("disposition") or {}).get("attached_pic")), {})


def _lottie(path: Path):
    """The animation, or None where there is no renderer — which costs the sticker
    its movement and its picture, and leaves its emoji (see :func:`still`)."""
    try:
        import rlottie_python
    except ImportError:
        log.warning("chat: rlottie-python is not installed — animated stickers "
                    "will be drawn as their emoji")
        return None
    try:
        if path.suffix.lower() == ".tgs":
            return rlottie_python.LottieAnimation.from_tgs(str(path))
        return rlottie_python.LottieAnimation.from_file(str(path))
    except Exception as e:  # noqa: BLE001 — a broken sticker is one sticker
        log.warning("chat: %s will not open as Lottie (%s)", path.name, e)
        return None


@lru_cache(maxsize=1024)
def _facts(path_s: str, mtime: float) -> tuple[int, int, float, bool]:
    path = Path(path_s)
    how = _decoder(path)
    if how == "lottie":
        anim = _lottie(path)
        if anim is None:
            return 512, 512, 0.0, False
        w, h = anim.lottie_animation_get_size()
        return int(w), int(h), float(anim.lottie_animation_get_duration()), True
    if how == "pillow":
        try:
            with Image.open(path) as im:
                moving = bool(getattr(im, "is_animated", False))
                dur = 0.0
                if moving:
                    for n in range(getattr(im, "n_frames", 1)):
                        im.seek(n)
                        dur += float(im.info.get("duration", 100) or 100) / 1000
                return im.width, im.height, dur, moving
        except OSError:
            return 0, 0, 0.0, False
    info = _ffprobe(path)
    vs = _video_stream(info)
    try:
        dur = float((info.get("format") or {}).get("duration") or 0.0)
    except ValueError:
        dur = 0.0
    if not vs:
        return 0, 0, dur, False
    w, h = int(vs.get("width") or 0), int(vs.get("height") or 0)
    # a phone films upright and writes the picture sideways with a note saying so
    rot = abs(int(float((vs.get("tags") or {}).get("rotate", 0) or 0)))
    for side in vs.get("side_data_list") or []:
        if "rotation" in side:
            rot = abs(int(float(side["rotation"])))
    if rot in (90, 270):
        w, h = h, w
    return w, h, dur, True


def facts(path: Path) -> tuple[int, int, float, bool]:
    """(width, height, seconds, moves) — what the layout and the clock need to know.

    Cached on the file's own mtime, because the layout asks for every message on
    every state and an ffprobe per question would be most of the render."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0, 0, 0.0, False
    return _facts(str(path), mtime)


def hollow(path: Path) -> bool:
    """Whether this moves AND is see-through — a sticker, an animated emoji.

    Those cannot be laid over their own first frame the way a video is: wherever the
    clip goes transparent, the frame under it shows through, and a sticker that moves
    drags a ghost of where it started. So the drawing leaves their place empty and the
    clip is all there is; a video, a GIF, a round video are opaque inside their outline
    and keep their still under them."""
    if not facts(path)[3]:
        return False
    return _decoder(path) in ("lottie", "pillow") or path.suffix.lower() == ".webm"


# --------------------------------------------------------------------------
# the first frame
# --------------------------------------------------------------------------


def _vp_decoder(path: Path) -> list[str]:
    """The decoder flag a VP8/VP9 file has to be opened with to keep its alpha.

    ffmpeg's built-in VP9 decoder reads the colour planes and ignores the alpha that
    WebM carries beside them; libvpx reads both. Every Telegram video sticker is
    exactly that file, so without this every one of them is a black square."""
    codec = _video_stream(_ffprobe(path)).get("codec_name", "")
    if codec == "vp9":
        return ["-c:v", "libvpx-vp9"]
    if codec == "vp8":
        return ["-c:v", "libvpx"]
    return []


def _frame0(path: Path) -> Image.Image | None:
    how = _decoder(path)
    if how == "lottie":
        anim = _lottie(path)
        if anim is None:
            return None
        w, h = anim.lottie_animation_get_size()
        return anim.render_pillow_frame(frame_num=0, width=int(w), height=int(h))
    if how == "pillow":
        try:
            with Image.open(path) as im:
                im.seek(0)
                return im.convert("RGBA")
        except OSError as e:
            log.warning("chat: %s will not open (%s)", path.name, e)
            return None
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", *_vp_decoder(path), "-i", str(path),
         "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-pix_fmt", "rgba", "-"],
        capture_output=True)
    if proc.returncode or not proc.stdout:
        log.warning("chat: no first frame out of %s", path.name)
        return None
    return Image.open(io.BytesIO(proc.stdout)).convert("RGBA")


@lru_cache(maxsize=256)
def _still(path_s: str, mtime: float, w: int, h: int, shape: tuple) -> Image.Image | None:
    first = _frame0(Path(path_s))
    if first is None:
        return None
    pic = _cover(first, w, h)
    m = mask(w, h, shape)
    if m is not None:
        alpha = pic.getchannel("A")
        pic.putalpha(Image.composite(alpha, Image.new("L", (w, h), 0), m))
    return pic


def still(path: Path, w: int, h: int, shape: tuple = ()) -> Image.Image | None:
    """The first frame, at exactly (w, h), through `shape` (see :func:`mask`).

    None when the file will not open, and the caller draws what stands in for it."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return _still(str(path), mtime, max(1, int(w)), max(1, int(h)), tuple(shape))


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scaled to fill (w, h) and cropped from the centre — the layout already chose a
    box of the picture's own shape, so this crops nothing but the rounding."""
    if img.size == (w, h):
        return img
    k = max(w / max(img.width, 1), h / max(img.height, 1))
    big = img.resize((max(w, round(img.width * k)), max(h, round(img.height * k))),
                     Image.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


def mask(w: int, h: int, shape: tuple) -> Image.Image | None:
    """The outline a picture is cut to: `("round",)` for the circle of a round video,
    `("rounded", r)` for the corners of a photo in a bubble, `()` for none.

    Drawn big and shrunk, as the bubbles are, because `ImageDraw` does not antialias
    and a circle's edge at this size is a staircase otherwise."""
    if not shape:
        return None
    ss = 4
    big = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    if shape[0] == "round":
        d.ellipse([0, 0, w * ss - 1, h * ss - 1], fill=255)
    else:
        r = int(shape[1]) if len(shape) > 1 else 0
        d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r * ss, fill=255)
    return big.resize((w, h), Image.LANCZOS)


# --------------------------------------------------------------------------
# the moving version
# --------------------------------------------------------------------------


def clip(path: Path, w: int, h: int, fps: float, out_dir: Path,
         shape: tuple = ()) -> tuple[Path, float] | None:
    """The moving version of this file at exactly (w, h): a short clip WITH ALPHA,
    looped by whoever lays it, and its length.

    VP9 with its alpha plane, in WebM — the format Telegram's own video stickers come
    in, and for the same reason: it is the compact one that keeps transparency. PNG
    frames in a QuickTime box were the first answer and the plain one, and were thirty
    megabytes for two seconds of a round video; this is one. Whoever reads it back must
    open it through libvpx (see :func:`_vp_decoder`), or the alpha is dropped. It is
    made once: the name is the file, the size, the frame rate and the outline, so the
    second render of the same video, and every message that sends the same sticker,
    finds it already there.

    None for anything that does not move, or will not open — the still under it is
    then the whole picture, which is what it was going to be anyway."""
    w, h = max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
    _w, _h, dur, moves = facts(path)
    if not moves:
        return None
    key = hashlib.sha1(f"{path.name}|{path.stat().st_size}|{w}x{h}|{fps}|{shape}".encode())
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tile_{key.hexdigest()[:16]}.webm"
    if out.is_file():
        return out, _clip_len(out, dur)
    how = _decoder(path)
    try:
        if how == "lottie":
            ok = _lottie_clip(path, w, h, fps, out, shape)
        elif how == "pillow":
            ok = _pillow_clip(path, w, h, fps, out, shape)
        else:
            ok = _ffmpeg_clip(path, w, h, fps, out, shape)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("chat: %s will not move (%s)", path.name, e)
        ok = False
    if not ok:
        out.unlink(missing_ok=True)
        return None
    return out, _clip_len(out, dur)


def _clip_len(out: Path, fallback: float) -> float:
    try:
        return float((_ffprobe(out).get("format") or {}).get("duration") or fallback)
    except ValueError:
        return fallback


_ENC = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "24", "-b:v", "0",
        "-auto-alt-ref", "0", "-deadline", "realtime", "-cpu-used", "8", "-f", "webm"]


def _pipe(frames, w: int, h: int, fps: float, out: Path) -> bool:
    """Raw RGBA frames into the clip. One writer for the two decoders that hand back
    pictures, so the clip they make is the same kind of file."""
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{w}x{h}", "-r", f"{fps:.4f}", "-i", "-", *_ENC, str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        for frame in frames:
            proc.stdin.write(frame.tobytes())
            n += 1
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
    err = proc.stderr.read().decode("utf-8", "replace")
    proc.wait()
    if proc.returncode or not n:
        log.warning("chat: writing a clip failed: %s", err[-400:])
        return False
    return True


def _shaped(img: Image.Image, m: Image.Image | None) -> Image.Image:
    if m is None:
        return img
    alpha = img.getchannel("A")
    img.putalpha(Image.composite(alpha, Image.new("L", img.size, 0), m))
    return img


def _lottie_clip(path: Path, w: int, h: int, fps: float, out: Path, shape: tuple) -> bool:
    anim = _lottie(path)
    if anim is None:
        return False
    total = int(anim.lottie_animation_get_totalframe())
    src_fps = float(anim.lottie_animation_get_framerate()) or 30.0
    length = min(total / src_fps, CLIP_CAP_S)
    m = mask(w, h, shape)

    def frames():
        for n in range(max(1, int(round(length * fps)))):
            pos = min(int(n / fps * src_fps), total - 1)
            yield _shaped(anim.render_pillow_frame(frame_num=pos, width=w, height=h)
                          .convert("RGBA"), m)

    return _pipe(frames(), w, h, fps, out)


def _pillow_clip(path: Path, w: int, h: int, fps: float, out: Path, shape: tuple) -> bool:
    m = mask(w, h, shape)
    with Image.open(path) as im:
        # every frame with the moment it ENDS at, so an output frame can be matched
        # to whichever source frame is up at its time — GIFs keep their own uneven
        # clock and the clip is laid at the video's even one
        stops, pics, t = [], [], 0.0
        for n in range(getattr(im, "n_frames", 1)):
            im.seek(n)
            t += max(float(im.info.get("duration", 100) or 100), 20.0) / 1000
            stops.append(t)
            pics.append(_cover(im.convert("RGBA"), w, h))
            if t >= CLIP_CAP_S:
                break
    length = min(stops[-1], CLIP_CAP_S)

    def frames():
        k = 0
        for n in range(max(1, int(round(length * fps)))):
            at = n / fps
            while k < len(stops) - 1 and stops[k] <= at:
                k += 1
            yield _shaped(pics[k].copy(), m)

    return _pipe(frames(), w, h, fps, out)


def _ffmpeg_clip(path: Path, w: int, h: int, fps: float, out: Path, shape: tuple) -> bool:
    # The length is the SOURCE's, capped, and written out rather than left to the
    # graph: the outline is a looped still, and a graph with an endless input in it
    # runs to whatever limit it is given.
    dur = facts(path)[2] or CLIP_CAP_S
    length = min(dur, CLIP_CAP_S)
    args = ["ffmpeg", "-y", "-v", "error", *_vp_decoder(path), "-i", str(path)]
    graph = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{h},fps={fps:.4f},format=rgba")
    m = mask(w, h, shape)
    if m is not None:
        # the outline as a second input and `alphamerge`: a video has no alpha of its
        # own to keep, so the mask simply becomes it
        at = out.with_name(out.stem + ".mask.png")
        m.save(at)
        args += ["-loop", "1", "-i", str(at)]
        graph += f"[v];[1:v]format=gray,scale={w}:{h}[m];[v][m]alphamerge"
    graph += "[o]"
    proc = subprocess.run(
        [*args, "-filter_complex", graph, "-map", "[o]", "-an",
         "-t", f"{length:.3f}", *_ENC, str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if m is not None:
        out.with_name(out.stem + ".mask.png").unlink(missing_ok=True)
    if proc.returncode:
        log.warning("chat: %s will not transcode: %s", path.name, proc.stderr[-400:])
        return False
    return True


# --------------------------------------------------------------------------
# the sound
# --------------------------------------------------------------------------


def audio_of(path: Path) -> tuple[Path, float] | None:
    """The sound of a voice message or a round video, as a file the assembler mixes
    like any voiced line, and its length.

    Extracted once, beside the original, because a voice message is opus in an ogg
    and a round video is a whole film — neither is what a scene's `audio` has ever
    held, and the assembler should not have to learn that it might be."""
    out = path.with_name(f"{path.stem}.voice.m4a")
    if not out.is_file():
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "2",
             "-ar", "48000", "-c:a", "aac", "-b:a", "160k", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode or not out.is_file():
            log.warning("chat: no sound out of %s: %s", path.name, proc.stderr[-300:])
            out.unlink(missing_ok=True)
            return None
    try:
        dur = float((_ffprobe(out).get("format") or {}).get("duration") or 0.0)
    except ValueError:
        dur = 0.0
    return (out, dur) if dur > 0 else None


@lru_cache(maxsize=256)
def _wave(path_s: str, mtime: float, bars: int) -> tuple[float, ...]:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path_s, "-vn", "-ac", "1", "-ar", "4000",
         "-f", "s16le", "-"], capture_output=True)
    if proc.returncode or not proc.stdout:
        return tuple([0.3] * bars)
    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = max(1, len(pcm) // bars)
    peaks = [max((abs(v) for v in pcm[i:i + step]), default=0)
             for i in range(0, step * bars, step)]
    top = max(peaks) or 1
    return tuple(max(0.12, p / top) for p in peaks)


def waveform(path: Path, bars: int) -> tuple[float, ...]:
    """The bars a voice message is drawn with, each 0..1, out of its own sound.

    An export carries no waveform — the client computes one on the fly — so this
    does the same, coarsely: the peak of each slice. Flat bars would read as a
    placeholder, and a waveform that matches what is heard is the detail that makes
    the bubble look like it was recorded rather than drawn."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return tuple([0.3] * bars)
    return _wave(str(path), mtime, bars)

