"""Drawing a line of chat text, which is never only text.

Two problems, and the second is the one that bites.

**Emoji are a different font.** A messenger's typeface has no 🔥 in it, so a string
drawn with one face comes out with holes where half the message was. The text has to
be cut into runs — letters here, emoji there — and each run drawn with its own face.

**Colour emoji have exactly one size.** Noto Color Emoji is a bitmap font with a
single 109px strike, and Pillow refuses outright to open it at any other size
("invalid pixel size"). So an emoji is drawn into a scratch image at 109 and scaled
down to the line's height. That is slower than drawing a glyph, which is why
:func:`emoji_image` is cached: a conversation uses the same dozen emoji again and
again, and each is rasterised once for the whole video.

Everything here measures in the same pass it would draw in, because the layout needs
widths before it can wrap and the wrap needs to agree with the drawing to the pixel —
two code paths that both compute a width are two code paths that eventually disagree.
"""

from __future__ import annotations

import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from . import fonts

# What gets drawn with the emoji face. Deliberately broad and deliberately not a
# grapheme segmenter: the ranges below cover the pictographs, the dingbats, the
# flags and the modifiers, and anything they catch that is not strictly an emoji is
# still something the text face almost certainly cannot draw.
_EMOJI = re.compile(
    "([\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE0F\U0001F3FB-\U0001F3FF"
    "\U000020E3\U0000200D]+)"
)


def runs(text: str) -> list[tuple[str, bool]]:
    """The string cut into (piece, is_emoji) runs, in order."""
    out: list[tuple[str, bool]] = []
    for i, piece in enumerate(_EMOJI.split(text)):
        if piece:
            out.append((piece, bool(i % 2)))
    return out


@lru_cache(maxsize=512)
def emoji_image(char: str, px: int) -> Image.Image | None:
    """One emoji, rasterised at its only legal size and scaled to `px`.

    None when there is no colour emoji font on this machine; the caller then draws
    the character with the text face and gets whatever outline it has."""
    face = fonts.emoji_font()
    if face is None:
        return None
    big = Image.new("RGBA", (fonts.EMOJI_PX + 8, fonts.EMOJI_PX + 8), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(big).text((4, 4), char, font=face, embedded_color=True)
    except Exception:  # noqa: BLE001 — a glyph the font does not have
        return None
    box = big.getbbox()
    if box is None:
        return None
    return big.crop(box).resize((px, px), Image.LANCZOS)


def width(draw: ImageDraw.ImageDraw, text: str, font, px: int) -> float:
    """How wide this string will be when :func:`draw_line` draws it."""
    total = 0.0
    for piece, is_emoji in runs(text):
        if is_emoji:
            total += px * len(_graphemes(piece))
        else:
            total += draw.textlength(piece, font=font)
    return total


def _graphemes(piece: str) -> list[str]:
    """Emoji split into things that get one square each.

    A zero-width joiner welds what surrounds it into one picture (👨‍👩‍👧), and a skin
    tone or a variation selector modifies the character before it. Neither may be
    split off, so the run is broken only where neither applies."""
    out: list[str] = []
    for ch in piece:
        if out and (ch in "‍️" or out[-1].endswith("‍")
                    or "\U0001F3FB" <= ch <= "\U0001F3FF" or ch == "⃣"):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def draw_line(img: Image.Image, draw: ImageDraw.ImageDraw, xy: tuple[int, int],
              text: str, font, fill: str, px: int) -> float:
    """Draw one line of mixed text and emoji. Returns how wide it came out.

    `xy` is the TOP-left, not the baseline: every box here is laid out from its top,
    and an emoji square is pasted by its corner, so a baseline would have to be
    converted back to a top in two places instead of none."""
    x, y = xy
    for piece, is_emoji in runs(text):
        if not is_emoji:
            draw.text((x, y), piece, font=font, fill=fill)
            x += draw.textlength(piece, font=font)
            continue
        for ch in _graphemes(piece):
            tile = emoji_image(ch, px)
            if tile is None:
                draw.text((x, y), ch, font=font, fill=fill)
                x += draw.textlength(ch, font=font)
            else:
                img.paste(tile, (int(x), int(y)), tile)
                x += px
    return x - xy[0]


def wrap(draw: ImageDraw.ImageDraw, text: str, font, px: int, limit: float) -> list[str]:
    """Break a message into lines no wider than `limit`.

    Greedy by word, and by CHARACTER when one word is wider than the whole bubble —
    which is not a corner case in a chat, where a pasted link or a keyboard-mash is
    an ordinary message. The newlines people type are honoured as themselves: a chat
    message is not a paragraph and re-flowing it would lose the shape somebody gave
    it on purpose."""
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}".strip()
            if not line or width(draw, trial, font, px) <= limit:
                line = trial
                continue
            lines.append(line)
            line = word
            while width(draw, line, font, px) > limit and len(line) > 1:
                cut = len(line) - 1
                while cut > 1 and width(draw, line[:cut], font, px) > limit:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines
