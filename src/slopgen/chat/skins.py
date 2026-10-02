"""What each client looks like, as numbers.

A skin is the design of ONE messenger, written down at a reference width of 1080px
and scaled to whatever the frame actually is (:meth:`Skin.at`). Reference-and-scale
rather than fractions everywhere, because these numbers were read off real
screenshots and a number you can compare to a screenshot is a number you can correct;
`0.0259 of the width` is not.

What a skin is NOT is a theme switch. Each one is the look that is recognisable at a
glance on a phone screen — Telegram's light chat on a wallpaper, Discord's dark flat
list, Reddit's white thread with a vote arrow — because being recognised in the first
half-second is the entire reason this mode draws an interface instead of a text card.

Reddit is the one that is shaped differently rather than coloured differently. Its
unit is a post with a comment TREE, it indents instead of taking sides, and it scores
with karma where the messengers react with emoji. The two do not convert, in either
direction, so a conversation is drawn by a compatible skin or by none
(:func:`compatible`) — which is the whole of what the operator meant by keeping the
two apart.
"""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass, replace

REFERENCE_W = 1080  # every number below is in pixels at this frame width


@dataclass(frozen=True)
class Skin:
    key: str
    family: str  # the typeface, asked of fontconfig by name (see `chat.fonts`)

    # -- the ground --------------------------------------------------------
    bg: str
    # Telegram's wallpaper is the only one of the three that is ever a picture, and
    # `ChatConfig.background` is where the operator says which. This is what stands
    # in its place, and what the other two always use.
    bubble_in: str
    bubble_out: str
    text_in: str
    text_out: str
    meta: str  # timestamps, karma, the small grey print
    divider: str  # tree guides, header rule, reaction pill ground

    # -- the chrome --------------------------------------------------------
    header_bg: str
    header_text: str
    header_h: int

    # -- geometry ----------------------------------------------------------
    pad_x: int  # the frame's own side margin
    gap: int  # between one message and the next
    avatar: int  # the round picture's diameter; 0 = this skin draws none
    radius: int  # bubble corner
    bubble_pad_x: int
    bubble_pad_y: int
    max_w: float  # the widest a bubble may be, as a share of the frame
    indent: int  # reddit: one level of the comment tree
    text_px: int
    name_px: int
    meta_px: int
    line_h: float  # line spacing as a multiple of the text size

    # -- shape -------------------------------------------------------------
    # What a reaction pill sits on and what its count is written in. Telegram tints
    # them rather than greying them — a reaction is something somebody pressed, and the
    # pill says so. Empty falls back to the divider and the meta colour, which is what
    # a flat skin wants.
    react_bg: str = ""
    react_ink: str = ""
    bubbles: bool = True  # False = flat rows (Discord, Reddit)
    sides: bool = True  # False = everything on the left, nobody is "me" (Discord, Reddit)
    tree: bool = False  # True = indent by reply depth and draw guides (Reddit)
    votes: bool = False  # True = karma instead of reactions (Reddit)
    wallpaper: bool = False  # True = a picture may stand behind the chat (Telegram)

    def at(self, width: int, height: int = 0) -> "Skin":
        """The same design at this frame's size. Colours and flags are untouched; every
        length is scaled and rounded, so a 720px render is the same picture and not a
        different one.

        Scaled by the SHORT side and not by the width, which matters the moment the
        frame is not a phone's. These numbers were read off a 1080-wide portrait
        screenshot, so scaling a 1920×1080 frame by its width makes every glyph 1.78×
        and fits three messages on screen — a chat nobody has ever seen. The short side
        keeps a message the size a message is, and the room left over goes to the
        margins, which is what a desktop client does with it too."""
        k = (min(width, height) if height else width) / REFERENCE_W
        if abs(k - 1.0) < 1e-6:
            return self
        px = {f: int(round(getattr(self, f) * k)) for f in (
            "header_h", "pad_x", "gap", "avatar", "radius", "bubble_pad_x",
            "bubble_pad_y", "indent", "text_px", "name_px", "meta_px")}
        return replace(self, **px)


TELEGRAM = Skin(
    key="telegram", family="Roboto",
    bg="#cfd9e3", bubble_in="#ffffff", bubble_out="#effdde",
    text_in="#000000", text_out="#000000", meta="#8a9199", divider="#e4e8eb",
    header_bg="#527da3", header_text="#ffffff", header_h=132,
    pad_x=20, gap=13, avatar=76, radius=24, bubble_pad_x=26, bubble_pad_y=15,
    max_w=0.80, indent=0, text_px=42, name_px=35, meta_px=29, line_h=1.20,
    react_bg="#e3f0fa", react_ink="#3b8ad1",
    bubbles=True, sides=True, wallpaper=True,
)

DISCORD = Skin(
    key="discord", family="Inter",
    bg="#313338", bubble_in="#313338", bubble_out="#313338",
    text_in="#dbdee1", text_out="#dbdee1", meta="#949ba4", divider="#3f4147",
    header_bg="#2b2d31", header_text="#f2f3f5", header_h=124,
    pad_x=28, gap=26, avatar=80, radius=16, bubble_pad_x=0, bubble_pad_y=0,
    max_w=0.88, indent=0, text_px=42, name_px=40, meta_px=28, line_h=1.3,
    bubbles=False, sides=False,
)

REDDIT = Skin(
    key="reddit", family="IBM Plex Sans",
    bg="#ffffff", bubble_in="#ffffff", bubble_out="#ffffff",
    text_in="#1c1c1c", text_out="#1c1c1c", meta="#7c7c7c", divider="#e2e4e6",
    header_bg="#ffffff", header_text="#1c1c1c", header_h=120,
    pad_x=26, gap=28, avatar=52, radius=12, bubble_pad_x=0, bubble_pad_y=0,
    max_w=0.96, indent=42, text_px=40, name_px=30, meta_px=28, line_h=1.34,
    bubbles=False, sides=False, tree=True, votes=True,
)

SKINS = {s.key: s for s in (TELEGRAM, DISCORD, REDDIT)}


def get(key: str, width: int = REFERENCE_W, height: int = 0) -> Skin:
    return SKINS.get(key, TELEGRAM).at(width, height)


def column(skin: Skin, width: int) -> tuple[int, int]:
    """How wide the conversation itself is, and where it starts.

    A phone's chat is the whole screen. A wide one is a COLUMN down the middle with
    the wallpaper either side, because that is what every desktop client does and
    because a bubble stretched across 1920 pixels stops reading as a message. The
    column is the design's own width at this scale, so the messages are the size they
    would be on a phone however wide the frame is."""
    want = int(REFERENCE_W * (skin.text_px / TELEGRAM.text_px)) if skin.text_px else width
    want = min(width, max(want, REFERENCE_W // 2))
    return want, (width - want) // 2


def compatible(a: str, b: str) -> bool:
    """Whether a conversation that came from `a` may be drawn in `b`.

    The messengers are interchangeable with each other — a Telegram export rendered
    as a Discord server is an ordinary thing to want, and nothing in either is lost
    doing it. Reddit is interchangeable with nothing: give it a messenger's skin and
    the comment tree flattens into a chat where half the replies no longer answer
    anything, and send a messenger the other way and every bubble sprouts a karma
    score that was never there."""
    island = {"reddit"}
    return (a in island) == (b in island)


# The palette real clients derive a person's colour from, so that the same person is
# the same colour in every screenshot. Telegram's eight and Discord's role colours are
# not these exactly — they could not be, since the real ones come out of an account id
# nobody here has — but the PROPERTY that matters is reproducibility, and a hash of
# the name has it.
_TINTS = ("#e17076", "#7bc862", "#65aadd", "#a695e7", "#ee7aae",
          "#6ec9cb", "#faa774", "#d4a24c")


def tint(name: str, skin: Skin) -> str:
    """This person's accent colour: their name's hash, pinned to a readable band.

    Derived rather than stored because an operator should not have to pick eight
    colours before they can make a video, and stable because somebody who changes
    colour between two messages reads as two people. A persona may still overrule it
    (`PersonaConfig.colour`), which is the case this cannot serve: a real person whose
    real colour everybody watching already knows."""
    digest = hashlib.sha1(name.strip().casefold().encode("utf-8")).digest()
    base = _TINTS[digest[0] % len(_TINTS)]
    if skin.bg == "#ffffff":  # a light ground wants the darker half of each hue
        r, g, b = (int(base[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h, light, s = colorsys.rgb_to_hls(r, g, b)
        r, g, b = colorsys.hls_to_rgb(h, min(light, 0.42), s)
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
    return base
