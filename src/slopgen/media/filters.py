"""The look laid over the FINISHED video: grain, CRT, VHS, glitch and the rest.

This is the montage end of the same wish `llm/style` serves at the prompt end, and
the two are not interchangeable. A style tag asks a generator to draw the picture a
certain way and gets a different answer every time, if it is honoured at all; nothing
asks stock footage or a local folder anything. A filter is applied to the frames that
came back, so it looks the same whatever made them and works in every mode and from
every source — which is why it lives here, in ffmpeg, and not in a prompt.

It is deliberately a property of the WHOLE video and not of a shot. Half the effects
here are a story about the thing the video is supposedly playing on — a tape, a tube,
a projector — and a tube that appears for one shot and leaves is not that story, it is
a transition. So the chain is built once, in the delivery pass (`ffmpeg._delivery_cmd`),
and covers every frame from the first to the last. A drama cut into episodes gets it on
each of them for their full length: the episodes are separate videos on separate pages,
so what has to hold is that no episode is ever half filtered.

Where it sits in the pass matters as much: the effects run on the PICTURE, before the
subtitles are burned in and before the ad overlay is stamped. Both of those are read,
not watched, and the platform reads them too — noise over a caption costs legibility
for nothing, and a partner's logo is not ours to put a tube in front of.

Each effect is a dose, 0-100, not a switch: 20 is a suggestion and 100 is the joke.
They stack, and they always stack in CATALOGUE order rather than the order they were
switched on, because that order is a pipeline — grade the picture, then its optics,
then the medium carrying it, then the transport breaking up. A grain added before a
blur is a blurred grain, which is not what anybody meant by either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config.models import GlobalConfig

# One statement of a filtergraph, e.g. "[vin]noise=alls=8[vout]".
Statement = str
# (dose 0..1, config, input label, output label) -> the statements to add
Build = Callable[[float, GlobalConfig, str, str], list[Statement]]


def _lerp(lo: float, hi: float, d: float) -> float:
    """Where a dose lands between "barely there" (1) and "the whole joke" (100)."""
    return lo + (hi - lo) * d


def _chain(make: Callable[[float, GlobalConfig], str]) -> Build:
    """An effect that is a plain filter chain — the common case: one statement,
    one video in, one video out."""

    def build(d: float, cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
        return [f"{vin}{make(d, cfg)}{vout}"]

    return build


@dataclass(frozen=True)
class Effect:
    key: str
    note: str  # one line of English for `--filter`'s help; the TUI has its own labels
    build: Build


# --- the effects ----------------------------------------------------------
#
# Every chain below is written in terms of the dose, so that a slider means the same
# thing everywhere: at 1 the effect is a hint, at 100 it is the point of the video.
# Nothing here is allowed to change the frame SIZE or the frame RATE — the delivery
# pass hands these frames straight to the encoder, and a filter that resized them
# would quietly undo the vertical format the whole pipeline is built around.


def _bw(d: float, cfg: GlobalConfig) -> str:
    # a dose of black-and-white is a colour drained part of the way, which is the
    # useful reading: 40 is a washed-out memory, 100 is monochrome.
    return f"hue=s={1 - d:.2f},eq=contrast={_lerp(1.0, 1.25, d):.2f}"


def _film(d: float, cfg: GlobalConfig) -> str:
    # Old stock: blacks that never reach black, a warm cast through the highlights,
    # a blue channel that stops short of the top, the projector's flicker, grain and
    # a soft corner. The curve is warm rather than the green-olive one "faded" usually
    # gets, because olive reads as a colour mistake and amber reads as age.
    lift = _lerp(0.0, 0.09, d)
    flicker = _lerp(0.0, 0.05, d)
    return (
        f"curves=r='0/{lift * 0.6:.3f} 0.5/{0.5 + _lerp(0, 0.05, d):.3f} 1/1'"
        f":g='0/{lift * 0.7:.3f} 0.5/0.5 1/{_lerp(1.0, 0.985, d):.3f}'"
        f":b='0/{lift:.3f} 0.5/{0.5 - _lerp(0, 0.035, d):.3f} 1/{_lerp(1.0, 0.91, d):.3f}',"
        f"eq=saturation={_lerp(1.0, 0.85, d):.2f}"
        f":brightness='{flicker:.3f}*sin(n/2.7)+{flicker:.3f}*sin(n/1.1)':eval=frame,"
        f"noise=alls={int(_lerp(2, 22, d))}:allf=t+u,"
        f"vignette=angle={_lerp(0.15, 0.80, d):.3f}"
    )


def _bloom(d: float, cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
    # Glow: a heavily blurred copy screened back over the picture. It is the one
    # effect that needs the frame twice, so it is the one that is not a plain chain.
    sigma = _lerp(6.0, 30.0, d)
    op = _lerp(0.10, 0.55, d)
    return [
        f"{vin}split[fxbl0][fxbl1]",
        f"[fxbl1]gblur=sigma={sigma:.1f}[fxblur]",
        f"[fxbl0][fxblur]blend=all_mode=screen:all_opacity={op:.2f}{vout}",
    ]


def _vignette(d: float, cfg: GlobalConfig) -> str:
    # `angle` is how wide the dark corner reaches; PI/2 is the maximum the filter takes.
    return f"vignette=angle={_lerp(0.20, 1.15, d):.3f}"


def _grain(d: float, cfg: GlobalConfig) -> str:
    # t+u: temporal (a fresh pattern each frame, so it crawls the way real grain does)
    # and uniform rather than gaussian, which survives the encoder better.
    return f"noise=alls={int(_lerp(3, 34, d))}:allf=t+u"


def _vhs(d: float, cfg: GlobalConfig) -> str:
    # Tape: bandwidth lost across the scanline and almost none down it — hence a
    # horizontal-only blur (sigmaV=0) — colour that does not sit where it should,
    # a flatter picture and the hiss of a fiftieth-generation copy. Done in 4:4:4 so
    # the chroma has somewhere to slide before it is subsampled back.
    shift = int(_lerp(2, 12, d))
    return (
        "format=yuv444p,"
        f"gblur=sigma={_lerp(0.6, 3.6, d):.2f}:sigmaV=0,"
        f"chromashift=cbh={shift}:crh=-{shift},"
        f"eq=saturation={_lerp(1.0, 0.78, d):.2f}:contrast={_lerp(1.0, 1.10, d):.2f}"
        f":brightness={_lerp(0.0, 0.03, d):.3f},"
        f"noise=alls={int(_lerp(3, 22, d))}:allf=t,"
        "format=yuv420p"
    )


# The hum bar: how tall it is (share of the frame) and how long one pass down the
# picture takes. It is one flat band with hard edges, because that is what the beat
# between a tube's refresh and a camera's shutter actually looks like — not a glow.
CRT_BAR_HEIGHT = 0.18
CRT_BAR_SWEEP_S = 6.6


def _crt_bar(d: float, cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
    """The bright band sliding down the picture, and the reason it is built the way
    it is.

    The obvious way — `drawbox` on a moving `y` expression — does not move: drawbox
    evaluates its geometry once when the filter is configured and never looks at `t`
    again (measured: a box on `y='mod(t*300,1900)'` sits at the same rows at 0 s and
    at 3 s). `overlay` is the filter that does re-read its position every frame, so
    the bar has to BE something overlaid.

    What gets overlaid is a flat white band with a fixed alpha, made out of the
    picture's own top rows run through `lutyuv` — a constant, so it costs one crop of
    a few hundred rows and no second input to the command. Because the band carries
    no picture of its own, it can hang off either edge of the frame and overlay simply
    clips it, which is how it slides in at the top and out at the bottom instead of
    jumping back to the start."""
    w, h = cfg.video.width, cfg.video.height
    bar = max(8, round(h * CRT_BAR_HEIGHT))
    speed = (h + bar) / CRT_BAR_SWEEP_S
    alpha = _lerp(0.05, 0.20, d)
    # from just above the frame to just below it, then round again
    y = f"mod(t*{speed:.1f},{h + bar})-{bar}"
    tag = vin.strip("[]")
    return [
        f"{vin}split[{tag}a][{tag}b]",
        f"[{tag}b]crop={w}:{bar}:0:0,lutyuv=y=255:u=128:v=128,format=yuva444p,"
        f"colorchannelmixer=aa={alpha:.3f}[{tag}bar]",
        f"[{tag}a][{tag}bar]overlay=x=0:y='{y}':eval=frame:format=yuv444{vout}",
    ]


def _crt(d: float, cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
    """Tube: scanlines, a mask that never registered its colours perfectly, the
    contrast a phosphor gives you, a bar of light sliding down the picture and the
    curve of the glass showing at the corners.

    The scanlines are a `drawgrid` with no vertical lines rather than a per-pixel
    expression: `geq` would cost more than the delivery encode itself, while a grid
    is a handful of blended rows. The whole tube is drawn in 4:4:4 because at 4:2:0
    each dark row drags the colour of its neighbour with it and the picture greys out.

    Order inside the tube matters: the bar goes on AFTER the scanlines, because it is
    light coming off the phosphor rather than paint on the glass, and before the
    vignette, which IS the glass and falls off over everything."""
    pitch = max(2, round(cfg.video.height / 640))  # ~3 rows at 1920, and it scales
    shift = int(_lerp(1, 5, d))
    return [
        f"{vin}format=yuv444p,"
        f"eq=contrast={_lerp(1.0, 1.12, d):.2f}:saturation={_lerp(1.0, 1.18, d):.2f},"
        f"chromashift=cbh={shift}:crh=-{shift},"
        f"drawgrid=w=0:h={pitch}:t=1:c=black@{_lerp(0.10, 0.45, d):.2f}[fxcrt]",
        *_crt_bar(d, cfg, "[fxcrt]", "[fxcrtl]"),
        f"[fxcrtl]vignette=angle={_lerp(0.15, 0.75, d):.3f},format=yuv420p{vout}",
    ]


# How many rows tear sideways at once. Each band costs a crop and two overlays, and
# both are skipped outright on the frames where the band is not firing (timeline
# `enable` bypasses a filter, it does not run it on a no-op), so the bill is paid
# only while something is actually broken.
GLITCH_BANDS = 6
# Per-band character: (height in 1/1000 of the frame, twitches per second, how much
# of each twitch it is visible, sideways reach as a share of the frame width). Thin
# fast rows and slow fat blocks in one list — a tear that is always the same size
# reads as a filter, and a picture that is never quiet reads as static.
GLITCH_ROWS = [
    (4, 2.7, 0.30, 0.09),
    (9, 1.9, 0.22, 0.16),
    (18, 1.3, 0.18, 0.05),
    (35, 0.9, 0.14, 0.11),
    (7, 3.3, 0.26, 0.20),
    (70, 0.5, 0.10, 0.07),
]


def _twitch(rate: float, seed: float) -> str:
    """A number that holds still for one twitch and jumps to a new one at the next.

    `floor(t*rate)` is which twitch we are in; running that through a sine with an
    unrelated multiplier gives a value in -1..1 that looks random and is not: the same
    second of the same video tears the same way twice, which is what makes a re-cut
    comparable and a bug reproducible."""
    return f"sin(floor(t*{rate:.2f})*{seed:.3f})"


def _glitch(d: float, cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
    """The signal failing — and failing in several ways at once, on clocks that do
    not divide into each other, so the faults land apart, together, apart again
    instead of pulsing on one beat.

    Two layers. The frame-wide faults come first: the channels come apart (twice
    over, hard and soft), the picture fills with hash, the colour drops out, the hue
    swings, and once in a while a single frame inverts. Then rows of pixels tear
    sideways over the top of it, each row torn out of the picture, split into its
    colour channels and put back a step to the side, wrapping around the frame edge
    the way a broken scanline actually does.

    A dose is BOTH how hard a fault hits and how often one comes: at 10 the video
    breaks up twice a minute, at 100 it can barely hold a picture together."""
    w, h = cfg.video.width, cfg.video.height
    split = int(_lerp(4, 40, d))
    period = _lerp(9.0, 1.7, d)
    burst = _lerp(0.05, 0.20, d)
    # the frame-wide faults, one chain, each on its own clock
    frame_faults = ",".join([
        f"rgbashift=rh={split}:bh=-{split}:gv={split // 3}"
        f":enable='lt(mod(t,{period:.2f}),{burst:.2f})'",
        # a second, softer channel split on a much faster clock: the picture is never
        # quite settled between the big faults
        f"rgbashift=rh={max(2, split // 3)}:bh=-{max(2, split // 4)}"
        f":enable='lt(mod(t+0.4,{period * 0.29:.2f}),{burst * 0.45:.2f})'",
        f"noise=alls={int(_lerp(20, 70, d))}:allf=t"
        f":enable='lt(mod(t+1.7,{period * 0.61:.2f}),{burst * 0.5:.2f})'",
        f"eq=contrast={_lerp(1.2, 2.0, d):.2f}:saturation=0.2:brightness=0.05"
        f":enable='lt(mod(t+3.9,{period * 0.83:.2f}),{burst * 0.4:.2f})'",
        # the colour swinging off its axis — a dropout that is not a dropout
        f"hue=h={int(_lerp(20, 120, d))}"
        f":enable='lt(mod(t+2.3,{period * 1.13:.2f}),{burst * 0.35:.2f})'",
        # and, rarely, one inverted frame. It is two frames long at most on purpose:
        # long enough to register, too short to look like a choice.
        f"negate=enable='lt(mod(t+5.1,{period * 2.7:.2f}),{min(0.07, burst * 0.3):.3f})'",
    ])
    nodes = [f"{vin}{frame_faults}[fxg]"]

    n = GLITCH_BANDS
    nodes.append("[fxg]split=" + str(n + 1) + "".join(f"[fxgs{i}]" for i in range(n + 1)))
    base = "[fxgs0]"
    for i, (mille, rate_mul, duty, reach) in enumerate(GLITCH_ROWS[:n], start=1):
        bh = max(2, round(h * mille / 1000))
        rate = _lerp(0.5, 4.5, d) * rate_mul  # twitches per second
        on = _lerp(0.35, 1.6, d) * duty  # ...and how much of each one it is visible for
        amp = w * reach * _lerp(0.25, 1.0, d)
        # a new row each twitch, walking the frame in steps that never repeat the
        # same tear twice in a row
        # crop evaluates its x/y expression per frame on its own; overlay has to be
        # told to (`eval=frame`), and both must land on the same row or the picture
        # tears in one place and is put back in another
        y = f"mod(floor(t*{rate:.2f})*{97 + 40 * i},{h - bh})"
        x = f"{amp:.0f}*{_twitch(rate, 12.9898 + 3.7 * i)}"
        en = f"lt(mod(t*{rate:.2f},1),{min(0.9, on):.2f})"
        shift = max(2, int(_lerp(3, 26, d) * (0.6 + reach * 3)))
        nodes.append(
            f"[fxgs{i}]crop={w}:{bh}:0:'{y}',"
            f"rgbashift=rh={shift}:bh=-{shift}:gh={max(1, shift // 3)}[fxgb{i}]"
        )
        # the row put back beside itself, and again a frame-width away so the part
        # that ran off one edge comes back in at the other
        nodes.append(f"[fxgb{i}]split[fxgc{i}][fxgd{i}]")
        nodes.append(
            f"{base}[fxgc{i}]overlay=x='{x}':y='{y}':eval=frame:enable='{en}'[fxgo{i}]"
        )
        wrap = f"if(gte({x},0),{x}-{w},{x}+{w})"
        last = i == n
        out = vout if last else f"[fxgw{i}]"
        nodes.append(
            f"[fxgo{i}][fxgd{i}]overlay=x='{wrap}':y='{y}':eval=frame:enable='{en}'{out}"
        )
        base = out
    return nodes


# Order is the pipeline, not the menu: grade, optics, medium, transport (see the
# module docstring). The TUI lists them in this order too, so what the operator reads
# top to bottom is what the frame goes through.
CATALOGUE: list[Effect] = [
    Effect("bw", "drain the colour — a dose, so 40 is faded and 100 is monochrome", _chain(_bw)),
    Effect("film", "old film stock: milky blacks, warm cast, projector flicker, grain", _chain(_film)),
    Effect("bloom", "hazy glow around the highlights", _bloom),
    Effect("vignette", "darkened corners", _chain(_vignette)),
    Effect("grain", "moving film grain", _chain(_grain)),
    Effect("vhs", "tape: horizontal softness, colour bleed, hiss", _chain(_vhs)),
    Effect("crt", "tube: scanlines, misregistered colour, a band of light sliding down, curved glass", _crt),
    Effect("glitch", "the signal failing: torn rows, channel split, hash, colour dropout", _glitch),
]

KEYS: list[str] = [e.key for e in CATALOGUE]
BY_KEY: dict[str, Effect] = {e.key: e for e in CATALOGUE}


def normalise(spec: dict) -> dict[str, int]:
    """Only the effects that exist, only the doses that mean anything: unknown keys
    are dropped and everything else is clamped into 1..100 (0 IS "off", so it is
    dropped too). Configs and presets are hand-editable and a run must not die in
    ffmpeg over a typo — it just goes out without that effect."""
    out: dict[str, int] = {}
    for key in KEYS:  # catalogue order, so the stored dict reads like the pipeline
        if key not in spec:
            continue
        try:
            dose = int(round(float(spec[key])))
        except (TypeError, ValueError):
            continue
        if dose > 0:
            out[key] = min(100, dose)
    return out


def parse(items: list[str]) -> dict[str, int]:
    """The CLI form: `["crt", "grain=30"]` -> `{"crt": 60, "grain": 30}`. A bare
    name means the middle of the range, which is the dose worth defaulting to — an
    effect you asked for by name should be visible without also being the video."""
    spec: dict[str, int] = {}
    for item in items or []:
        for part in str(item).split(","):
            part = part.strip()
            if not part:
                continue
            key, _, dose = part.partition("=")
            key = key.strip().lower()
            if key not in BY_KEY:
                raise ValueError(f"unknown filter '{key}' (have: {', '.join(KEYS)})")
            spec[key] = dose.strip() or 60
    return normalise(spec)


def describe(spec: dict[str, int]) -> str:
    """`{"crt": 60, "grain": 20}` -> "crt 60, grain 20", in catalogue order."""
    ordered = normalise(spec)
    return ", ".join(f"{k} {v}" for k, v in ordered.items())


def graph(spec: dict[str, int], cfg: GlobalConfig, vin: str, vout: str) -> list[Statement]:
    """The filtergraph statements taking `vin` to `vout` through every effect asked
    for, in catalogue order. An empty (or entirely unknown) spec returns the one
    statement that renames the label, so the caller can splice this in unconditionally
    without growing a branch — `null` costs a frame reference and nothing else."""
    active = normalise(spec)
    if not active:
        return [f"{vin}null{vout}"]
    out: list[Statement] = []
    keys = list(active)
    label = vin
    for i, key in enumerate(keys):
        last = i == len(keys) - 1
        nxt = vout if last else f"[fx{i}]"
        out.extend(BY_KEY[key].build(active[key] / 100.0, cfg, label, nxt))
        label = nxt
    return out
