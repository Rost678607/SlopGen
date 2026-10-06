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
# (dose 0..1, config, input label, output label, namespace) -> the statements to add.
#
# The namespace is a prefix for every label an effect invents INSIDE itself. It used to
# be unnecessary because the whole video got one chain, so `[fxbl0]` could only ever
# occur once; a look that changes partway through the video builds the same chain twice
# in one filtergraph (see :func:`graph_spans`), and two statements claiming one label is
# an error that names neither the effect nor the span it came from.
Build = Callable[[float, GlobalConfig, str, str, str], list[Statement]]


def _lerp(lo: float, hi: float, d: float) -> float:
    """Where a dose lands between "barely there" (1) and "the whole joke" (100)."""
    return lo + (hi - lo) * d


def _chain(make: Callable[[float, GlobalConfig], str]) -> Build:
    """An effect that is a plain filter chain — the common case: one statement,
    one video in, one video out."""

    def build(d: float, cfg: GlobalConfig, vin: str, vout: str, ns: str) -> list[Statement]:
        return [f"{vin}{make(d, cfg)}{vout}"]

    return build


@dataclass(frozen=True)
class Effect:
    key: str
    note: str  # one line of English for `--filter`'s help; the TUI has its own labels
    build: Build
    # How dark a corner this effect wants, as a `vignette` angle at this dose — or None
    # for the effects that want none. It is declared rather than applied, because a
    # vignette is the GLASS and there is one piece of glass: three effects each adding
    # their own multiplied together, and with the film, the tube and the vignette slider
    # all up the delivered video came out at 56% of the light it went in with while the
    # room's sketch — which took the strongest of the three rather than all of them —
    # looked fine. :func:`graph` takes the strongest and emits exactly one.
    veil: Callable[[float], float] | None = None


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
        f"noise=alls={int(_lerp(2, 22, d))}:allf=t+u"
    )


def _film_veil(d: float) -> float:
    return _lerp(0.15, 0.80, d)


def _bloom(d: float, cfg: GlobalConfig, vin: str, vout: str, ns: str = "") -> list[Statement]:
    # Glow: a heavily blurred copy screened back over the picture. It is the one
    # effect that needs the frame twice, so it is the one that is not a plain chain.
    sigma = _lerp(6.0, 30.0, d)
    op = _lerp(0.10, 0.55, d)
    return [
        f"{vin}split[{ns}fxbl0][{ns}fxbl1]",
        f"[{ns}fxbl1]gblur=sigma={sigma:.1f}[{ns}fxblur]",
        f"[{ns}fxbl0][{ns}fxblur]blend=all_mode=screen:all_opacity={op:.2f}{vout}",
    ]


def _vignette_veil(d: float) -> float:
    # `angle` is how wide the dark corner reaches; PI/2 is the maximum the filter takes.
    return _lerp(0.20, 1.15, d)


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


def _crt(d: float, cfg: GlobalConfig, vin: str, vout: str, ns: str = "") -> list[Statement]:
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
    # one row in `pitch` is blacked out at this much, so this much of the light goes
    dim = _lerp(0.10, 0.45, d) / pitch
    gain = 1.0 / max(1.0 - dim, 0.05)
    return [
        f"{vin}format=yuv444p,"
        f"eq=contrast={_lerp(1.0, 1.12, d):.2f}:saturation={_lerp(1.0, 1.18, d):.2f},"
        f"chromashift=cbh={shift}:crh=-{shift},"
        f"drawgrid=w=0:h={pitch}:t=1:c=black@{_lerp(0.10, 0.45, d):.2f}[{ns}fxcrt]",
        *_crt_bar(d, cfg, f"[{ns}fxcrt]", f"[{ns}fxcrtl]"),
        # The grid above blacks out one row in `pitch` at `alpha`, which is light the
        # tube did not ask to lose: a dose is how a thing LOOKS, and nobody reaching for
        # a tube is asking for a seventh of their exposure. Put back as a GAIN and not as
        # a brightness lift — `eq=brightness` adds a constant, so it would recover a
        # mid-grey exactly and wash the shadows out on a dark picture, while the loss it
        # is undoing is a multiplication. `colorchannelmixer` with equal diagonal terms
        # is a plain linear gain on all three channels.
        f"[{ns}fxcrtl]colorchannelmixer=rr={gain:.4f}:gg={gain:.4f}:bb={gain:.4f},"
        f"format=yuv420p{vout}",
    ]


def _crt_veil(d: float) -> float:
    return _lerp(0.15, 0.75, d)


# How many rows tear sideways at once. Each band costs a crop and two overlays, and
# both are skipped outright on the frames where the band is not firing (timeline
# `enable` bypasses a filter, it does not run it on a no-op), so the bill is paid
# only while something is actually broken.
GLITCH_BANDS = 10
# Per-band character: (height in 1/1000 of the frame, chances per second, how likely
# each chance is to fire inside a storm, sideways reach as a share of the frame width,
# how many times a second it re-aims while it is firing, which way the channels go).
#
# TEN of them, spread from three thousandths of the frame to a seventh of it, because
# `crop` sizes itself once when the filtergraph is configured and never again: a band's
# height is the one thing here that cannot be made to vary within a band, so the only
# place thickness variety can come from is the number of bands. Six of them meant six
# stripe thicknesses for the whole video, every video.
#
# The reaches are deliberately not ordered with the heights — a hairline that flies half
# the frame and a fat block that barely shifts are both things a broken decoder does,
# and pairing size with distance is what makes a set of tears look like one effect
# running at different scales.
# `wide` is the one that changes what the effect IS rather than how much of it there is.
# A band of 1.0 is the classic full-width tear: a strip of the picture slid sideways,
# wrapping at the edge. Anything less is a BLOCK — a rectangle cut out of the picture
# and put down somewhere else, moved on both axes.
#
# The difference matters because of what the eye gets. A strip that slides a twentieth
# of the frame shows you one thing: the seam where it parted, a thin line across the
# whole width. Ten of those is ten lines, and ten lines is what «однотипные полосы»
# means however varied their heights and timing are. A block that moves is legible as a
# piece of the picture in the wrong place, which is both what digital corruption
# actually looks like and the thing that cannot be mistaken for a stripe. So half the
# table is blocks now, and the strips that stayed strips slide far enough to read as
# displacement rather than as a cut.
GLITCH_ROWS = [
    # ‰     /s   chance  reach  aim/s  channels  wide
    (3,    3.1,   0.55,  0.34,   24,   "h+",     1.00),
    (5,    2.4,   0.48,  0.52,   20,   "h-",     0.38),
    (8,    2.9,   0.50,  0.44,   18,   "v",      1.00),
    (12,   1.8,   0.42,  0.30,   15,   "h+",     0.55),
    (18,   2.2,   0.40,  0.62,   12,   "h-",     1.00),
    (26,   1.4,   0.34,  0.24,   10,   "hv",     0.27),
    (40,   1.1,   0.30,  0.40,    7,   "h+",     0.70),
    (60,   0.8,   0.24,  0.18,    5,   "v",      0.44),
    (90,   0.6,   0.20,  0.50,    4,   "h-",     1.00),
    (130,  0.45,  0.16,  0.22,    3,   "hv",     0.60),
]

# How the channels come apart on a torn row. `rgbashift`'s offsets are integers and not
# expressions, so a band cannot vary its own split per firing — which is why the TABLE
# varies it instead: red leading, red trailing, the pair separated up and down, and both
# at once. Four ways of coming apart across ten bands reads as a decoder losing
# different things, where one way across six read as a setting.
def _rgbsplit(kind: str, s: int) -> str:
    if kind == "v":
        return f"rgbashift=rv={s}:bv=-{s}:gh={max(1, s // 4)}:{RGB_EDGE}"
    if kind == "hv":
        return (f"rgbashift=rh={s}:bh=-{s}:rv={max(1, s // 2)}:bv=-{max(1, s // 2)}"
                f":{RGB_EDGE}")
    if kind == "h-":
        return f"rgbashift=rh=-{s}:bh={s}:gh={max(1, s // 3)}:{RGB_EDGE}"
    return f"rgbashift=rh={s}:bh=-{s}:gh=-{max(1, s // 3)}:{RGB_EDGE}"


# What a shifted channel does with the strip it vacates at the frame edge. `smear`, the
# default, holds the edge pixel across it — which paints a solid coloured column down
# the side of the picture on EVERY channel-split event, always the same width, always in
# the same two places. Measured by looking: it was the most repeated thing on the screen
# and it was not even a fault, it was the filter running out of pixels. `wrap` takes
# them from the opposite edge instead, so what fills the strip is real picture and what
# the eye reads is a channel that has slid.
RGB_EDGE = "edge=wrap"

# How long one weather window lasts. Everything that can break reads the same window
# (see :func:`_glitch`), so the faults agree about when the signal is having a moment
# instead of each keeping its own private schedule.
GLITCH_STORM_S = 1.6


def _hash(n: str, seed: float) -> str:
    """A number in 0..1 that looks random and is not.

    `n` is an expression for WHICH chance this is — `floor(t*4.2)` for a row that gets
    four chances a second, `floor(t/1.6)` for the weather window. Running an integer
    through a sine and keeping the fraction of a large multiple of it is the standard
    cheap hash, and the property that matters here is that it is a FUNCTION of the
    index: the same second of the same video breaks the same way twice, so a re-cut is
    comparable and a bug is reproducible.

    It replaced a plain `sin(n*seed)` used as a value and a `mod(t*rate,1) < duty` used
    as a gate. The gate was the problem: a duty cycle fires on a metronome, and measured
    over a minute the intervals between tears took exactly TWO distinct lengths per row
    while 73% of all frames had something happening on them. That is not a signal
    failing, it is a texture — and a texture is what «однотипно» means.
    """
    return f"mod(abs(sin(({n})*{seed:.4f})*43758.5453),1)"


def _glitch(d: float, cfg: GlobalConfig, vin: str, vout: str, ns: str = "") -> list[Statement]:
    """The signal failing — and failing in several ways at once, on clocks that do
    not divide into each other, so the faults land apart, together, apart again
    instead of pulsing on one beat.

    **It breaks in WEATHER, not on a clock.** Everything below reads one slowly
    changing number — `storm`, a hash of which 1.6-second window we are in — and
    nothing fires outside a window except the odd stray. So the shape of it is: fine,
    fine, fine, the picture falls apart for a second and a half, fine again. That is
    what a failing signal does, and it is the half that was missing: every fault used
    to run on its own strictly periodic duty cycle, which measured out at exactly two
    distinct gap lengths per row and something happening on 73% of all frames. Nothing
    was ever quiet, so nothing ever read as a fault — only as a texture.

    Two layers inside a storm. The frame-wide faults come first: the channels come
    apart (twice over, hard and soft), the picture fills with hash, the colour drops
    out, the hue swings, the whole frame jumps its hold and wraps round, and once in a
    while a single frame inverts. Then rows of pixels tear sideways over the top of it,
    each row torn out of the picture, split into its colour channels and put back a
    step to the side, wrapping around the frame edge the way a broken scanline does.

    Every one of those is gated on a hash of its own chance index rather than on a duty
    cycle (:func:`_hash`), so the intervals are irregular, and on the same storm, so
    they arrive together. Where the row tears and how far it slides are hashes too — of
    the same index, with different seeds — so no two firings of one row are the same
    tear.

    A dose is THREE things: how often a storm comes, how dense the faults are inside
    one, and how hard each hits. At 10 the picture breaks up for a moment twice a
    minute; at 100 it is in a storm more often than out of one."""
    w, h = cfg.video.width, cfg.video.height
    split = int(_lerp(4, 40, d))
    # how much of the running time is stormy, and how thick the faults are once it is
    weather = _lerp(0.06, 0.72, d)
    density = _lerp(0.30, 1.0, d)
    storm = _hash(f"floor(t/{GLITCH_STORM_S})", 7.1337)
    # 1 inside a storm, and a small number outside it — not zero, because a single
    # stray tear in a quiet minute is what tells the viewer the video is capable of it
    gust = f"(0.05+0.95*lt({storm},{weather:.3f}))"

    def fires(rate: float, chance: float, seed: float) -> str:
        """Whether this fault is firing on this frame: one chance every `1/rate`
        seconds, taken `chance` of the time while the weather is bad."""
        return f"lt({_hash(f'floor(t*{rate:.2f})', seed)},{min(0.95, chance * density):.3f}*{gust})"

    # The only two faults that touch the WHOLE frame, and the two that can afford to:
    # neither of them moves a pixel sideways, so neither has an edge to run out of.
    # Everything else is confined to a block below, which is not tidiness — see there.
    frame_faults = ",".join([
        f"noise=alls={int(_lerp(20, 70, d))}:allf=t"
        f":enable='{fires(7.0, 0.38, 41.117)}'",
        # …and, rarely, one inverted frame. Its chance is taken often and taken seldom,
        # so it is long enough to register and too short to look like a choice. Whole
        # frame on purpose: an inverted strip reads as a mistake in the effect, an
        # inverted frame reads as the signal.
        f"negate=enable='{fires(14.0, 0.05, 90.773)}'",
    ])
    nodes = [f"{vin}{frame_faults}[{ns}fxb0]"]

    # --- the colour faults, confined to a BLOCK of the picture --------------
    #
    # These were whole-frame, and that was the other half of «однотипно»: every event
    # the eye actually caught was the entire picture turning magenta, or grey, or
    # white. Three transforms repainting everything is three looks, however irregularly
    # they are timed — and none of them looks like a picture breaking, they look like a
    # picture being graded.
    #
    # Confined to a band they read as corruption instead: part of the image is wrong and
    # the rest is fine, which is what a dropped block of data actually does, and WHERE it
    # is wrong changes every firing. The band's height is fixed per fault (crop decides
    # its size once, at configuration, and only re-reads x/y per frame), so the variety
    # has to come from the position — which is a hash, like everything else here.
    def _block(src: str, out: str, tag: str, share: float, make: str,
               rate: float, chance: float, seed: float,
               wide: float = 1.0) -> list[Statement]:
        bh = max(8, int(h * share))
        bw = w if wide >= 0.999 else max(32, int(w * wide))
        idx = f"floor(t*{rate:.2f})"
        y = f"floor({_hash(idx, seed + 0.77)}*{h - bh})"
        x = "0" if bw == w else f"floor({_hash(idx, seed + 1.93)}*{w - bw})"
        en = fires(rate, chance, seed)
        # The transform carries the same `enable` as the overlay that lands it. Without
        # it the block is computed on every frame of the video and thrown away on all
        # but a few of them: `crop` has no timeline switch and must run regardless, but
        # it is cheap and the thing after it is not.
        return [
            f"{src}split[{ns}{tag}k][{ns}{tag}t]",
            f"[{ns}{tag}t]crop={bw}:{bh}:'{x}':'{y}',{make}:enable='{en}'[{ns}{tag}b]",
            f"[{ns}{tag}k][{ns}{tag}b]overlay=x='{x}':y='{y}':eval=frame:enable='{en}'{out}",
        ]

    nodes += _block(f"[{ns}fxb0]", f"[{ns}fxb1]", "fxc1", _lerp(0.40, 0.22, d),
                    f"eq=contrast={_lerp(1.2, 2.0, d):.2f}:saturation=0.2:brightness=0.05",
                    4.0, 0.30, 58.209)
    # the colour swinging off its axis — a dropout that is not a dropout
    nodes += _block(f"[{ns}fxb1]", f"[{ns}fxb2]", "fxc2", _lerp(0.30, 0.15, d),
                    f"hue=h={int(_lerp(20, 120, d))}", 3.0, 0.26, 73.551)
    # …and two blocks where the CHANNELS come apart rather than the colour changing,
    # which is the fault this effect is named after and the one worth having most of.
    # Regional for the same reason the colour ones are — a whole frame with its red
    # pushed sideways is a look, a band of it is a decoder dropping a plane — and in two
    # directions, because red leading blue across a strip and the pair pulled apart up
    # and down are two different kinds of broken, not two doses of one.
    #
    # They are the one pair that is NARROWER than the frame, and that is not decoration.
    # Shifting a channel always leaves something where it ran out of pixels, and a
    # full-width band puts that fringe exactly on the frame's own edge — the same two
    # coloured bars, the same width, on every firing, which was the most repeated thing
    # on the screen and was not even a fault. Inside a block the fringe falls on an edge
    # of the PICTURE, where it reads as what it is, and the block's corner moves every
    # time (`wide`, and an x that is a hash like everything else here).
    hard = int(_lerp(8, 64, d))
    nodes += _block(f"[{ns}fxb2]", f"[{ns}fxb3]", "fxs1", _lerp(0.34, 0.20, d),
                    f"rgbashift=rh={hard}:bh=-{hard}:gh={max(1, hard // 5)}:{RGB_EDGE}",
                    4.5, 0.34, 21.907, wide=_lerp(0.78, 0.42, d))
    nodes += _block(f"[{ns}fxb3]", f"[{ns}fxb4]", "fxs2", _lerp(0.22, 0.11, d),
                    f"rgbashift=rv={max(2, hard // 2)}:bv=-{max(2, hard // 2)}"
                    f":gh={max(2, hard // 3)}:{RGB_EDGE}",
                    3.5, 0.26, 64.183, wide=_lerp(0.62, 0.30, d))
    # …and the big one, which is as close to a whole-frame channel split as this effect
    # is allowed to get. It covers most of the picture, which is what makes it read as
    # the SIGNAL coming apart rather than as a block of it — but not the edges, because
    # a shifted channel has nothing to fill the columns it vacates there, and whatever
    # it fills them with is a coloured line down the side of the frame, the same width
    # in the same place on every firing. That line was the most repeated thing on the
    # screen and it was not a fault at all, it was the filter running out of picture.
    nodes += _block(f"[{ns}fxb4]", f"[{ns}fxv]", "fxs3", 0.86,
                    f"rgbashift=rh={split}:bh=-{split}:gv={max(1, split // 3)}:{RGB_EDGE}",
                    5.0, 0.42, 12.9898, wide=0.90)

    # The vertical hold going, which is the one fault here that is not a dose of the
    # others: the whole picture jumps and what leaves the top comes back at the bottom.
    # It is two full-frame overlays and therefore the expensive one, which is why it is
    # also the rarest — and `enable` skips both outright on the frames it is not on.
    jump = f"({_hash(f'floor(t*6.0)', 33.19)}*2-1)*{int(h * _lerp(0.05, 0.38, d))}"
    roll = fires(6.0, 0.14, 33.19)
    nodes.append(f"[{ns}fxv]split[{ns}fxr0][{ns}fxr1]")
    nodes.append(f"[{ns}fxr1]split[{ns}fxr2][{ns}fxr3]")
    nodes.append(f"[{ns}fxr0][{ns}fxr2]overlay=x=0:y='{jump}':eval=frame"
                 f":enable='{roll}'[{ns}fxrj]")
    nodes.append(f"[{ns}fxrj][{ns}fxr3]overlay=x=0"
                 f":y='if(gte({jump},0),({jump})-{h},({jump})+{h})':eval=frame"
                 f":enable='{roll}'[{ns}fxg]")

    n = GLITCH_BANDS
    nodes.append(f"[{ns}fxg]split=" + str(n + 1) + "".join(f"[{ns}fxgs{i}]" for i in range(n + 1)))
    base = f"[{ns}fxgs0]"
    for i, (mille, rate_mul, chance, reach, aim, kind, wide) in enumerate(
            GLITCH_ROWS[:n], start=1):
        bh = max(2, round(h * mille / 1000))
        bw = w if wide >= 0.999 else max(48, int(w * wide))
        # WHETHER it is tearing is asked often and answered seldom, which is what makes
        # the gaps between tears land anywhere instead of on multiples of one slot: the
        # intervals used to quantise to 1/rate per band, six rates in the whole effect,
        # and that is what «очень похожие интервалы» was.
        gate = _lerp(6.0, 26.0, d) * rate_mul
        # …and WHERE it is tearing is asked much faster still, on a clock of its own.
        # One tear used to hold its row for as long as it was on — up to two thirds of a
        # second on the fat bands, measured — so it read as a stripe somebody parked
        # there rather than as a picture coming apart. Now the place, the distance and
        # the direction are all re-rolled several times inside one firing, so a tear
        # flickers across the frame the way a decoder losing sync actually does.
        move = max(aim * _lerp(0.45, 1.3, d), 2.0)
        amp = w * reach * _lerp(0.3, 1.0, d)
        idx = f"floor(t*{move:.2f})"
        en = fires(gate, chance * 0.22, 4.7591 + 11.3 * i)
        shift = max(2, int(_lerp(3, 26, d) * (0.6 + reach * 2)))
        last = i == n
        out = vout if last else f"[{ns}fxgw{i}]"
        # crop evaluates its x/y expression per frame on its own; overlay has to be told
        # to (`eval=frame`), and the two must agree about where the piece came from or
        # the picture tears in one place and is put back in another.
        sy = f"floor({_hash(idx, 12.9898 + 3.7 * i)}*{h - bh})"
        if bw == w:
            # A full-width STRIP: slid sideways, and put down a second time a frame-width
            # away so what ran off one edge comes back in at the other. The wrap is only
            # drawn where the slide is big enough for it to be worth an overlay.
            x = f"({_hash(idx, 78.233 + 5.1 * i)}*2-1)*{amp:.0f}"
            nodes.append(
                f"[{ns}fxgs{i}]crop={w}:{bh}:0:'{sy}',"
                f"{_rgbsplit(kind, shift)}:enable='{en}'[{ns}fxgb{i}]"
            )
            nodes.append(f"[{ns}fxgb{i}]split[{ns}fxgc{i}][{ns}fxgd{i}]")
            nodes.append(
                f"{base}[{ns}fxgc{i}]overlay=x='{x}':y='{sy}':eval=frame"
                f":enable='{en}'[{ns}fxgo{i}]"
            )
            wrap = f"if(gte({x},0),{x}-{w},{x}+{w})"
            nodes.append(
                f"[{ns}fxgo{i}][{ns}fxgd{i}]overlay=x='{wrap}':y='{sy}':eval=frame"
                f":enable='{en}'{out}"
            )
        else:
            # A BLOCK: cut out of somewhere and put down somewhere else, moved on both
            # axes. No wrap — a piece of picture that has been moved has not run off an
            # edge, it is simply in the wrong place, which is the whole reading.
            sx = f"floor({_hash(idx, 33.711 + 7.3 * i)}*{w - bw})"
            dx = f"({_hash(idx, 78.233 + 5.1 * i)}*2-1)*{amp:.0f}"
            dy = f"({_hash(idx, 95.157 + 6.9 * i)}*2-1)*{h * reach * _lerp(0.12, 0.45, d):.0f}"
            nodes.append(
                f"[{ns}fxgs{i}]crop={bw}:{bh}:'{sx}':'{sy}',"
                f"{_rgbsplit(kind, shift)}:enable='{en}'[{ns}fxgb{i}]"
            )
            nodes.append(
                f"{base}[{ns}fxgb{i}]overlay=x='({sx})+({dx})':y='({sy})+({dy})'"
                f":eval=frame:enable='{en}'{out}"
            )
        base = out
    return nodes


# Order is the pipeline, not the menu: grade, optics, medium, transport (see the
# module docstring). The TUI lists them in this order too, so what the operator reads
# top to bottom is what the frame goes through.
CATALOGUE: list[Effect] = [
    Effect("bw", "drain the colour — a dose, so 40 is faded and 100 is monochrome", _chain(_bw)),
    Effect("film", "old film stock: milky blacks, warm cast, projector flicker, grain",
           _chain(_film), veil=_film_veil),
    Effect("bloom", "hazy glow around the highlights", _bloom),
    Effect("vignette", "darkened corners", _chain(lambda d, cfg: "null"),
           veil=_vignette_veil),
    Effect("grain", "moving film grain", _chain(_grain)),
    Effect("vhs", "tape: horizontal softness, colour bleed, hiss", _chain(_vhs)),
    Effect("crt", "tube: scanlines, misregistered colour, a band of light sliding down, curved glass",
           _crt, veil=_crt_veil),
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


def graph(spec: dict[str, int], cfg: GlobalConfig, vin: str, vout: str,
          ns: str = "") -> list[Statement]:
    """The filtergraph statements taking `vin` to `vout` through every effect asked
    for, in catalogue order. An empty (or entirely unknown) spec returns the one
    statement that renames the label, so the caller can splice this in unconditionally
    without growing a branch — `null` costs a frame reference and nothing else.

    `ns` prefixes every label this chain invents, so the same chain can be built more
    than once in one filtergraph (see :func:`graph_spans`). The default is the empty
    namespace, which produces exactly the graph this returned when a video had one
    look from end to end."""
    active = normalise(spec)
    if not active:
        return [f"{vin}null{vout}"]
    # The glass, once. Each effect says how dark a corner it wants (`Effect.veil`) and
    # the widest of them is what gets drawn, at the very end — because a vignette is a
    # lens and a picture is behind one lens, not behind three multiplied together.
    angle = max((BY_KEY[k].veil(active[k] / 100.0)
                 for k in active if BY_KEY[k].veil is not None), default=0.0)
    out: list[Statement] = []
    keys = list(active)
    label = vin
    for i, key in enumerate(keys):
        last = i == len(keys) - 1 and angle <= 0.001
        nxt = vout if last else f"[{ns}fx{i}]"
        out.extend(BY_KEY[key].build(active[key] / 100.0, cfg, label, nxt, ns))
        label = nxt
    if angle > 0.001:
        out.append(f"{label}vignette=angle={angle:.3f}{vout}")
    return out


# One stretch of the finished video and the look over it: (seconds, spec).
Span = tuple[float, dict]


def merge(spans: list[Span]) -> list[Span]:
    """Fold consecutive stretches that ask for the SAME look into one.

    This is what makes a look that covers six lines one application rather than six.
    It matters for the same reason it matters to a background sound: half of these
    effects are on a clock of their own — the tube's hum bar sliding down the picture,
    the glitch's torn rows, the film's flicker — and a chain restarted at every line
    puts all of them back to their first frame on every line. A bar that slides a
    sixth of the way down and jumps back to the top is not a tube, it is a stutter.

    Zero-length stretches are dropped on the way, so a line with no voice yet cannot
    split a run in two by sitting between its halves with nothing in it."""
    out: list[Span] = []
    for seconds, spec in spans:
        clean = normalise(spec or {})
        if seconds <= 0.0005:
            continue
        if out and out[-1][1] == clean:
            out[-1] = (out[-1][0] + seconds, clean)
        else:
            out.append((seconds, clean))
    return out


def graph_spans(spans: list[Span], cfg: GlobalConfig, vin: str,
                vout: str) -> list[Statement]:
    """The statements taking `vin` to `vout` through a look that CHANGES partway.

    One span is the ordinary case and produces exactly what :func:`graph` always
    produced — no split, no trim, no concat, the same single chain over the whole
    picture. Everything below only happens to a video whose operator asked for
    something else on some of its lines.

    Several spans are cut apart, filtered one at a time and joined back:

        [vin]split=N[s0][s1]…;
        [s0]trim=0:10.5,setpts=PTS-STARTPTS,<chain A>[q0];
        [s1]trim=start=10.5,setpts=PTS-STARTPTS,<chain B>[q1];
        [q0][q1]concat=n=2:v=1:a=0[vout]

    Three details are load-bearing. The intervals are HALF-OPEN and share their
    boundaries, so every frame lands in exactly one span and the join has the same
    frames as the input — no length is lost or gained, which matters because the
    delivery pass checks its own output against the sum of its inputs. The LAST span
    is trimmed with a start and no end, so the final frame cannot be rounded off the
    end. And each span is rebased to zero (`setpts`), which is the whole point of
    merging consecutive identical looks: an effect's own clock then runs once across
    the stretch it covers instead of restarting at every line inside it.

    Audio is not in here at all (`a=0`): the look is a thing done to the picture, and
    the voice track has already been built by the time this is spliced in.
    """
    spans = merge(spans)
    if not spans:
        return [f"{vin}null{vout}"]
    if len(spans) == 1:
        return graph(spans[0][1], cfg, vin, vout)
    n = len(spans)
    out: list[Statement] = [f"{vin}split={n}" + "".join(f"[fs{i}]" for i in range(n))]
    at = 0.0
    for i, (seconds, spec) in enumerate(spans):
        last = i == n - 1
        cut = f"trim=start={at:.4f}" if last else f"trim={at:.4f}:{at + seconds:.4f}"
        out.append(f"[fs{i}]{cut},setpts=PTS-STARTPTS[fc{i}]")
        out.extend(graph(spec, cfg, f"[fc{i}]", f"[fq{i}]", ns=f"s{i}"))
        at += seconds
    out.append("".join(f"[fq{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0{vout}")
    return out
