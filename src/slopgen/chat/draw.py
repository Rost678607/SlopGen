"""Drawing the conversation: one tall canvas, and bands cut out of it.

The shape of this module follows from one fact about the format. The picture changes
every time anything on screen changes — a message arriving, a piece of a long one
being revealed, a reaction popping — and a minute of conversation is a couple of
hundred such moments. Redrawing the whole conversation for each would be quadratic in
the number of messages and would spend most of its time on bubbles that are miles
above the visible window.

So the canvas is built ONCE, incrementally, and only the last block is ever rebuilt —
which is all that a reveal or a reaction can touch, since everything else has already
been said. :meth:`Canvas.append` lays a message out and remembers its geometry;
:meth:`Canvas.amend` replaces that geometry when the same message grows; and
:meth:`Canvas.band` renders a horizontal slice of the result, painting only the blocks
that reach into it. A state is a band, and the scroll is which band (see
:mod:`.scroll`).

Laying out and painting are kept apart for the same reason the band exists: the
layout of every message has to be known to decide where the window is, and painting
it is the expensive half. A :class:`Block` is the result of the cheap half and carries
everything the expensive half needs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts, media as media_mod, richtext
from .exports import split_reaction
from .skins import Skin, column as skins_column

# How much bigger than life the bubble outline is drawn before being shrunk
# back down. ImageDraw has no antialiasing of its own, and a tail is thin
# enough that without this it is more staircase than curve.
SS = 4

log = logging.getLogger(__name__)


@dataclass
class Person:
    """Who a message is from, as the drawing needs them: a name, a picture, a colour,
    and whether this is the account the chat is being watched FROM."""

    name: str
    avatar: Path | None = None
    colour: str = "#65aadd"
    mine: bool = False
    # What they are called besides their name — `Куратор`, `админ` — printed at the
    # right of the name line, which is where Telegram prints an admin's title.
    role: str = ""
    # What the initials disc says when there is no picture. Separate from `name`
    # because the name printed on a comment may carry the platform's own prefix —
    # `u/костя` — and a disc reading "U" for everybody is a disc that identifies
    # nobody. Empty falls back to the name, which is right for the messengers.
    initials: str = ""


@dataclass
class Shown:
    """What a message carries, laid out: where its picture goes and how it is cut.

    `box` is in canvas coordinates like the block's own. `bare` is the sticker and the
    round video, which a client draws with no bubble around them at all — the picture
    IS the message, and a white box behind a sticker is the first thing that would give
    the screenshot away."""

    kind: str
    path: Path | None
    box: tuple[int, int, int, int]
    shape: tuple = ()
    bare: bool = False
    emoji: str = ""
    duration: float = 0.0


@dataclass
class Moving:
    """A moving thing as painted onto ONE band: what it is, and where in the band's own
    pixels its top-left landed. The render stage turns these into the clips laid over
    the state (see `chat_render`); the band itself holds each one's first frame."""

    path: Path
    x: int
    y: int
    w: int
    h: int
    shape: tuple = ()
    msg: int = -1
    # A still to lay over the clip rather than a clip: the label on a moving picture,
    # which the clip would otherwise cover. `path` is then meaningless.
    image: Image.Image | None = None
    # The first frame of a see-through moving thing, which was NOT painted into the
    # band (see `media.hollow`) — kept for whatever needs one picture of it: the
    # preview, and the swipe that slides the whole screen away.
    poster: Image.Image | None = None


@dataclass
class Block:
    """One message, laid out: where it sits on the canvas and what to paint there.

    Height is the whole of what the canvas needs to go on stacking, which is why the
    layout pass can run far ahead of the painting and why only this object has to be
    rebuilt when a message grows."""

    msg: int  # index into the conversation
    top: int  # canvas y of the block's first pixel
    height: int
    lines: list[str] = field(default_factory=list)
    person: Person = field(default_factory=lambda: Person(name=""))
    # the bubble's box in canvas coordinates (x0, y0, x1, y1); the flat skins still
    # fill one in, since it is also where the text goes
    box: tuple[int, int, int, int] = (0, 0, 0, 0)
    show_head: bool = True  # the author's name/avatar, or a run of theirs continuing
    # The day this message starts, when it starts one: a centred pill above it, which
    # is how every client says the conversation has crossed midnight. Empty is the
    # ordinary case — only the first message of a day carries one.
    separator: str = ""
    # The little spur at the bottom corner. It marks the END of somebody's run of
    # messages, not the start of it, so it is a separate question from `show_head` and
    # is answered one message later — which costs nothing, because the spur is drawn
    # outside the bubble's box and taking it away moves no geometry.
    show_tail: bool = True
    stamp: str = ""
    reply: tuple[str, str] | None = None  # (who is being answered, one line of what)
    reactions: list[tuple[str, int]] = field(default_factory=list)
    score: int | None = None  # reddit karma; None = this skin does not vote
    depth: int = 0  # reddit: how deep in the comment tree
    media: Shown | None = None
    # the custom emoji of this message's text, in order, as (fallback, file)
    custom: list[tuple[str, Path]] = field(default_factory=list)

    @property
    def bottom(self) -> int:
        return self.top + self.height


class Canvas:
    """A conversation being laid out, and the renderer of what has been laid out."""

    def __init__(self, skin: Skin, width: int, *, wallpaper: Path | None = None,
                 top_inset: int = 0, media_root: Path | None = None):
        self.skin = skin
        self.width = width
        # How wide the conversation itself is, and where it starts. The whole frame on
        # a phone; a centred column with the ground either side on anything wide (see
        # `skins.column`). Everything about a message is laid out against these two
        # numbers and nothing against the frame — except the header bar, which spans
        # it, as it does in every desktop client.
        self.column, self.left = skins_column(skin, width)
        self.wallpaper = wallpaper
        # The header is painted over the band and does not scroll, exactly as it does
        # not in a real client — so the conversation has to start below it or its
        # first message is one that nobody can read.
        self.top_inset = top_inset
        self.blocks: list[Block] = []
        self.height = skin.gap + top_inset  # a breath above the first message
        # one scratch image, because measuring needs a draw context and making one per
        # call is the single most expensive thing a layout pass can do
        self._probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        self._ground: Image.Image | None = None
        self.f_text = fonts.load(skin.family, skin.text_px)
        self.f_name = fonts.load(skin.family, skin.name_px, "bold")
        self.f_meta = fonts.load(skin.family, skin.meta_px)
        # Where a message's pictures are, by the names messages store (`chat.media`).
        # None draws every one of them as what stands in for a missing file.
        self.media_root = media_root
        # What moved on the band being painted, collected as it is painted — the one
        # moment its position on the band is known (see :class:`Moving`).
        self._moving: list[Moving] | None = None
        self._msg = -1  # the block being painted, which is whose a moving thing is

    # -- laying out ---------------------------------------------------------

    def append(self, **kw) -> Block:
        """Lay one more message under the ones already there."""
        block = self._lay(top=self.height, **kw)
        self.blocks.append(block)
        self.height = block.bottom + self.skin.gap
        return block

    def amend(self, **kw) -> Block:
        """Re-lay the LAST message in place — it has grown a piece, or a reaction.

        The only mutation this canvas allows, and the reason the whole arrangement is
        cheap: nothing above the last message can change, because a conversation is
        only ever added to."""
        if not self.blocks:
            return self.append(**kw)
        old = self.blocks.pop()
        self.height = old.top
        return self.append(**kw)

    def clear(self) -> None:
        """Start the screen again. The blocks already drawn are forgotten rather than
        scrolled away, which is what `ChatMsg.clear_before` means."""
        self.blocks = []
        self.height = self.skin.gap + self.top_inset

    def _file(self, name: str) -> Path | None:
        if not name or self.media_root is None:
            return None
        return media_mod.resolve(self.media_root, name)

    def _lay(self, *, top: int, msg: int, person: Person, text: str,
             stamp: str = "", reply: tuple[str, str] | None = None,
             reactions: list[tuple[str, int]] | None = None,
             score: int | None = None, depth: int = 0,
             show_head: bool = True, separator: str = "",
             media=None, custom: list[tuple[str, str]] | None = None) -> Block:
        s = self.skin
        reactions = list(reactions or [])
        carried = media if (media is not None and media.kind in media_mod.KINDS) else None
        # A sticker or a round video with nothing written under it is drawn BARE —
        # no bubble, no name over it, no quoted strip — which is what every client
        # does with them. With words under it, it is a picture in a bubble like any.
        if carried is not None and carried.kind in ("sticker", "round") and not text.strip():
            return self._lay_bare(top=top, msg=msg, person=person, stamp=stamp,
                                  reactions=reactions, score=score, depth=depth,
                                  show_head=show_head, separator=separator,
                                  media=carried)
        indent = s.indent * depth if s.tree else 0
        # Where the avatar sits differs in kind, not in size. A messenger stands it in
        # a lane of its own to the left of the bubble; reddit puts a small one on the
        # author line and runs the comment's text under it at full width. So only the
        # first reserves a lane.
        avatar_lane = (s.avatar + s.pad_x) if (s.avatar and not s.tree) else 0
        # …and the tree draws no quoted strip at all: the indent already says what is
        # being answered, and saying it twice is how a comment ends up quoting its own
        # parent directly above its own parent.
        if s.tree:
            reply = None
        limit = int(self.column * s.max_w) - indent - avatar_lane
        inner = limit - 2 * s.bubble_pad_x
        # A picture with nothing under it is a message with no lines at all, not with
        # one empty one: the bubble closes under the picture.
        lines = (richtext.wrap(self._probe, text, self.f_text, s.text_px, inner)
                 if text.strip() or carried is None else [])

        line_h = int(s.text_px * s.line_h)
        sep_h = self._sep_h() if separator else 0
        top += sep_h
        head = bool(show_head and (s.bubbles and not person.mine or not s.bubbles))
        h = s.bubble_pad_y * 2 + (max(len(lines), 1) if carried is None else len(lines)) * line_h
        if head:
            h += self._head_h()
        if reply:
            h += self._reply_h()
        # The picture: its own shape, as wide as the bubble may be (a client fills
        # the bubble with it), and no taller than a share of the screen.
        pic_w = pic_h = 0
        if carried is not None:
            pic_w, pic_h = self._pic_size(carried, limit)
            h += pic_h + (self._inset() if s.bubbles and lines else 0)
            if s.bubbles and not head and not reply:
                # the photo sits a hair inside the bubble's own top edge, and the
                # padding above it would be a stripe of bubble over the picture
                h -= s.bubble_pad_y - self._inset()
            if s.bubbles and not lines:
                h -= s.bubble_pad_y - self._inset()
            if not s.bubbles and lines:
                h += self._flat_gap()
        if reactions and not s.votes:
            h += self._react_h()
        if s.votes and score is not None:
            h += int(s.meta_px * 1.9)

        # the box is as wide as the widest line, never wider than the limit, and never
        # so narrow that the timestamp hangs off the end of it
        widest = max((richtext.width(self._probe, ln, self.f_text, s.text_px)
                      for ln in lines), default=0)
        if pic_w:
            widest = max(widest, pic_w + (2 * self._inset() - 2 * s.bubble_pad_x
                                          if s.bubbles else 0))
        if show_head and s.bubbles and not person.mine:
            widest = max(widest, self._probe.textlength(person.name, font=self.f_name))
        # Telegram sets the time INSIDE the bubble, on the last line when it fits
        # there and on a line of its own when it does not. Deciding that here is what
        # keeps it off the words: the bubble is either widened to make room beside the
        # last line, or made taller to put the time under it.
        # …and a picture with no words under it wears the time ON the picture, on a
        # dark pill in the corner, so it needs no room of its own.
        if stamp and s.bubbles and not (reactions and not s.votes) and lines:
            stamp_w = self._probe.textlength(stamp, font=self.f_meta)
            last = richtext.width(self._probe, lines[-1], self.f_text, s.text_px) if lines else 0
            need = last + stamp_w + s.bubble_pad_x
            if need <= inner:
                widest = max(widest, need)
            else:
                h += int(s.meta_px * 1.25)
        w = int(min(max(widest + 2 * s.bubble_pad_x, s.radius * 3), limit))
        if person.mine and s.sides:
            x1 = self.left + self.column - s.pad_x
            x0 = x1 - w
        else:
            x0 = self.left + s.pad_x + indent + avatar_lane
            x1 = x0 + w
        shown = None
        if carried is not None:
            # where the picture goes inside the box: under the name and the quote,
            # above the words, flush with the bubble's inset (or the text's left edge
            # in a flat skin, where there is no bubble to be inside)
            py = top + (self._inset() if s.bubbles and not head and not reply
                        else s.bubble_pad_y)
            if head:
                py += self._head_h()
            if reply:
                py += self._reply_h()
            if s.bubbles:
                px = x0 + self._inset()
                pic_w = min(pic_w, w - 2 * self._inset())
            else:
                # a flat skin writes the words first and hangs the attachment under
                # them, which is how Discord and reddit both show one
                px = x0
                py += len(lines) * line_h + (self._flat_gap() if lines else 0)
            shown = self._shown(carried, (px, py, px + pic_w, py + pic_h))
        return Block(msg=msg, top=top - sep_h, height=h + sep_h, lines=lines,
                     person=person, box=(x0, top, x1, top + h), show_head=show_head,
                     stamp=stamp, reply=reply, reactions=reactions, score=score,
                     depth=depth, show_tail=True, separator=separator, media=shown,
                     custom=self._customs(custom))

    def _customs(self, custom) -> list[tuple[str, Path]]:
        out = []
        for alt, name in custom or []:
            at = self._file(name)
            if at is not None:
                out.append((alt, at))
        return out

    def _flat_gap(self) -> int:
        """Between the words and the attachment under them, in a flat skin."""
        return int(self.skin.text_px * 0.35)

    def _inset(self) -> int:
        """How far inside the bubble's edge a picture sits. A few pixels, which is the
        bubble showing as a thin frame round the photo, as it does in Telegram."""
        return max(2, int(self.skin.bubble_pad_y * 0.3))

    def _pic_size(self, m, limit: int) -> tuple[int, int]:
        """How big a picture is drawn: its own shape, scaled to the room there is.

        A sticker is a fixed share of the column whatever its file says, because
        that is how a client sizes one; a round video is a fixed circle; a voice
        message is a control of fixed size. Everything else fills the bubble's width
        and gives up width rather than run taller than about two thirds of a phone
        screen's width — a portrait photo at full width would be the whole screen."""
        s = self.skin
        c = self.column
        if m.kind == "voice":
            return int(c * 0.56), int(s.text_px * 2.1)
        if m.kind == "round":
            d = int(c * 0.52)
            return d, d
        aspect = (m.w / m.h) if (m.w and m.h) else (1.0 if m.kind == "sticker" else 4 / 3)
        if m.kind == "sticker":
            side = int(c * (0.42 if s.bubbles else 0.3))
            return (side, max(1, int(side / aspect))) if aspect >= 1 else (int(side * aspect), side)
        room = (limit - 2 * self._inset()) if s.bubbles else int(c * 0.66)
        tallest = int(c * (0.78 if s.bubbles else 0.6))
        w = room
        h = int(w / aspect)
        if h > tallest:
            h = tallest
            w = int(h * aspect)
        # never a sliver: a very tall picture still gets a bubble you can see
        w = max(w, int(c * 0.32))
        return w, h

    def _shown(self, m, box) -> Shown:
        s = self.skin
        r = max(2, s.radius - self._inset()) if s.bubbles else max(4, s.radius // 2)
        shape = (("round",) if m.kind == "round" else
                 () if m.kind in ("sticker", "voice") else ("rounded", r))
        return Shown(kind=m.kind, path=self._file(m.file), box=box, shape=shape,
                     bare=m.kind in ("sticker", "round"), emoji=m.emoji,
                     duration=float(m.duration or 0.0))

    def _lay_bare(self, *, top: int, msg: int, person: Person, stamp: str,
                  reactions: list, score, depth: int, show_head: bool,
                  separator: str, media) -> Block:
        """A sticker or a round video: the picture itself, on the ground, with the
        time on a pill at its foot and the reactions under it."""
        s = self.skin
        sep_h = self._sep_h() if separator else 0
        top += sep_h
        indent = s.indent * depth if s.tree else 0
        avatar_lane = (s.avatar + s.pad_x) if (s.avatar and not s.tree) else 0
        w, h = self._pic_size(media, int(self.column * s.max_w))
        head = not s.bubbles and show_head
        full = h + (self._head_h() if head else 0)
        if reactions and not s.votes:
            full += self._react_h() + int(s.gap * 0.5)
        if s.votes and score is not None:
            full += int(s.meta_px * 1.9)
        if person.mine and s.sides:
            x1 = self.left + self.column - s.pad_x
            x0 = x1 - w
        else:
            x0 = self.left + s.pad_x + indent + avatar_lane
            x1 = x0 + w
        py = top + (self._head_h() if head else 0)
        return Block(msg=msg, top=top - sep_h, height=full + sep_h, lines=[],
                     person=person, box=(x0, top, x1, top + full),
                     show_head=show_head, stamp=stamp, reply=None,
                     reactions=reactions, score=score, depth=depth, show_tail=True,
                     separator=separator,
                     media=self._shown(media, (x0, py, x0 + w, py + h)))

    def _sep_h(self) -> int:
        """The band a date pill occupies, above the first message of its day."""
        return int(self.skin.meta_px * 2.7)

    def _head_h(self) -> int:
        """The author line. In a tree skin it also carries the little avatar, so it is
        at least as tall as that — otherwise the picture hangs down into the comment's
        own first line."""
        s = self.skin
        h = int(s.name_px * s.line_h)
        if s.tree and s.avatar:
            h = max(h, int(s.avatar * 1.12))
        return h

    def _reply_h(self) -> int:
        """Tall enough for the author line AND a full line of what they said — the
        quoted line is set in the message face, so it costs a message line, not a
        meta one."""
        s = self.skin
        return int(s.meta_px * 1.2 + s.text_px * s.line_h + s.gap * 0.6)

    def _react_h(self) -> int:
        return int(self.skin.meta_px * 1.72)

    # -- painting -----------------------------------------------------------

    def header_image(self, title: str, avatar: Path | None) -> Image.Image:
        """The bar at the top, on its own transparent strip.

        Separate from the band because it does not scroll, and the band does. Painted
        into the drawing it would travel up and off the screen with the conversation
        under it; what it actually is, in every client, is a fixed overlay on the
        frame — so that is what it is here too, composited by `make_chat_part`."""
        img = Image.new("RGBA", (self.width, self.skin.header_h), (0, 0, 0, 0))
        self._paint_header(img, ImageDraw.Draw(img, "RGBA"), title, avatar)
        return img

    def band(self, y0: int, height: int, *, header: tuple[str, Path | None] | None = None,
             reveal: dict[int, int] | None = None, pin: int | None = None,
             moving: list[Moving] | None = None) -> Image.Image:
        """Render the canvas from `y0` for `height` pixels.

        `reveal` caps how many LINES of a block are painted, by message index — which
        is how a message that is being read out arrives a piece at a time without the
        bubble around it jumping to its final size on the first word. The bubble is
        laid out for what is revealed so far (see the caller), so this only has to
        stop painting.

        The header is painted last and does not scroll, because in a real client it
        does not.

        `moving`, given, is filled with every moving thing that landed in the band
        and where (see :class:`Moving`) — the band itself holds their first frames."""
        s = self.skin
        self._moving = moving
        img = self._ground_for(height).copy() if self.wallpaper else Image.new(
            "RGB", (self.width, height), s.bg)
        draw = ImageDraw.Draw(img, "RGBA")
        top_guard = s.header_h if header else 0
        for block in self.blocks:
            if block.bottom < y0 or block.top > y0 + height:
                continue
            self._paint(img, draw, block, -y0, cap=(reveal or {}).get(block.msg),
                        hide_pic=(pin is not None and block.msg == pin))
        if header:
            self._paint_header(img, draw, *header)
        del top_guard
        self._moving = None
        return img

    def avatar_of(self, msg: int) -> tuple[Person, int, int, int] | None:
        """Whose picture rides on this block and where it sits, or None.

        `(person, x, bottom, size, msg)` in canvas coordinates. Handed out because of what
        the picture does while a new message arrives: it belongs to the foot of
        somebody's run, so each new message of theirs moves it down the canvas by
        exactly as much as the view then rolls up — and the two cancel out, which is
        why in a real client the picture does not appear to move at all. Painted into
        the scrolling picture it cannot cancel: it is drawn where it will END, so it
        comes into view from below with the new bubble and the eye reads that as the
        picture jumping. Lifting it out of the band and laying it on top at its
        resting place puts the cancellation back.

        A messenger only: a comment tree stands the picture on the author's line,
        where it scrolls with the comment and belongs to nothing else."""
        s = self.skin
        if not s.bubbles or s.tree or not s.avatar or not self.blocks:
            return None
        # The LAST block and no other. This used to walk back up the canvas until it
        # found any block wearing a tail, which quietly picked the wrong one: the
        # account the chat is read from draws no picture of itself, so as soon as the
        # bottom message was theirs the search fell through to the run ABOVE and
        # pinned that person's face — a face that belongs to finished history and has
        # to scroll away with it. It was then pinned at the place it will rest while
        # also being painted into the band, so the two copies slid apart.
        b = self.blocks[-1]
        if not b.show_tail or (b.person.mine and s.sides):
            return None
        return b.person, self.left + s.pad_x, b.bottom, s.avatar, b.msg

    def paint_avatar(self, img: Image.Image, person: Person, xy: tuple[int, int],
                     size: int) -> None:
        """Put one picture onto an image that is not the band — the overlay that does
        not scroll. The same disc, so a pinned picture and a scrolled one cannot come
        out looking different."""
        self._avatar(img, ImageDraw.Draw(img, "RGBA"), person, xy, size)

    def _ground_for(self, height: int) -> Image.Image:
        """The wallpaper, scaled to cover the frame once and reused for every band.

        Cached on the canvas because it is the same picture in all two hundred states
        and decoding it two hundred times is the difference between a render that
        takes a minute and one that takes ten."""
        if self._ground is None or self._ground.height < height:
            base = Image.new("RGB", (self.width, height), self.skin.bg)
            if self.wallpaper and self.wallpaper.is_file():
                try:
                    pic = Image.open(self.wallpaper).convert("RGB")
                    k = max(self.width / pic.width, height / pic.height)
                    pic = pic.resize((int(pic.width * k) + 1, int(pic.height * k) + 1),
                                     Image.LANCZOS)
                    base.paste(pic, (0, 0))
                except OSError as e:
                    log.warning("the chat wallpaper %s will not open (%s)", self.wallpaper, e)
            self._ground = base
        return self._ground

    def _paint(self, img: Image.Image, draw: ImageDraw.ImageDraw, b: Block, dy: int,
               cap: int | None = None, hide_pic: bool = False) -> None:
        s = self.skin
        self._msg = b.msg
        x0, _, x1, _ = b.box
        y = b.box[1] + dy          # the bubble's own top; the block may start higher
        bottom = b.bottom + dy
        if b.separator:
            self._paint_separator(img, draw, b.separator, b.top + dy)

        if s.tree and b.depth:
            # the thread guides: one vertical rule per level, which is the whole of
            # how a reddit comment says what it is answering
            for level in range(b.depth):
                gx = self.left + s.pad_x + s.indent * level + s.indent // 2
                draw.line([(gx, y - s.gap), (gx, bottom)], fill=s.divider, width=max(2, s.indent // 18))

        bare = b.media is not None and b.media.bare
        if s.bubbles and not bare:
            fill = s.bubble_out if b.person.mine else s.bubble_in
            self._bubble(img, b, y, bottom, fill)

        cx = x0 + (s.bubble_pad_x if s.bubbles else 0)
        cy = y + (s.bubble_pad_y if s.bubbles else 0)
        text_colour = s.text_out if b.person.mine else s.text_in

        head_x = cx
        # The picture goes with the TAIL and not with the name: a run of messages from
        # one person carries the name on the first and the little spur on the last, and
        # the avatar sits beside the spur, at the foot of the run. Drawn at the top it
        # floats beside a bubble the person is still in the middle of saying.
        show_pic = (b.show_tail if s.bubbles else b.show_head) and not hide_pic
        if show_pic and s.avatar and not (b.person.mine and s.sides):
            ax = self.left + s.pad_x + (s.indent * b.depth if s.tree else 0)
            ay = y if (s.tree or not s.bubbles) else max(y, bottom - s.avatar)
            self._avatar(img, draw, b.person, (ax, ay), s.avatar)
            if s.tree:
                # reddit stands the picture ON the author line and runs the comment
                # underneath it at full width, so only this line steps aside
                head_x = ax + s.avatar + int(s.pad_x * 0.5)
        if b.show_head and (not s.bubbles or not b.person.mine) and not (bare and s.bubbles):
            name = b.person.name + (f"  {b.stamp}" if (b.stamp and not s.bubbles) else "")
            head_h = self._head_h()
            ny = cy + (head_h - s.name_px * s.line_h) / 2
            richtext.draw_line(img, draw, (head_x, int(ny)), name, self.f_name,
                               b.person.colour, s.name_px)
            # What they are besides their name, at the other end of the line: Telegram
            # prints an admin's title there, and it reads as a label precisely because
            # it is not next to the name.
            if b.person.role and s.bubbles:
                rw = self._probe.textlength(b.person.role, font=self.f_meta)
                draw.text((x1 - s.bubble_pad_x - rw, ny + s.name_px * 0.18),
                          b.person.role, font=self.f_meta, fill=s.meta)
            cy += head_h

        if b.reply:
            cy = self._paint_reply(img, draw, b, cx, cy, x1)

        # In a bubble the picture comes first and the words under it; a flat skin
        # writes first and hangs the picture under the words (see `_lay`), and the
        # box laid out for it already says which.
        if b.media is not None and (s.bubbles or bare):
            self._paint_media(img, draw, b, dy)
            if not bare:
                cy = b.media.box[3] + dy + self._inset()

        line_h = int(s.text_px * s.line_h)
        shown = b.lines if cap is None else b.lines[:max(cap, 0)]
        swaps = richtext.Swaps(b.custom, self._paste_custom) if b.custom else None
        for line in shown:
            richtext.draw_line(img, draw, (cx, cy), line, self.f_text, text_colour,
                               s.text_px, swaps=swaps)
            cy += line_h
        if b.media is not None and not s.bubbles and not bare:
            self._paint_media(img, draw, b, dy)

        if s.votes and b.score is not None:
            self._paint_votes(draw, b, cx, bottom - int(s.meta_px * 1.7))
        elif b.reactions:
            rx = b.media.box[0] if bare else cx
            self._paint_reactions(img, draw, b, rx, bottom - self._react_h() + int(s.meta_px * 0.3))
        pinned_stamp = s.bubbles and b.media is not None and b.media.kind != "voice" \
            and not b.lines
        if b.stamp and pinned_stamp:
            # on the picture, on a dark pill — the only place a client puts the time
            # of a message that is nothing but a picture
            self._stamp_pill(img, b, dy)
        elif b.stamp and s.bubbles:
            w = self._probe.textlength(b.stamp, font=self.f_meta)
            draw.text((x1 - s.bubble_pad_x - w, bottom - s.bubble_pad_y - s.meta_px * 1.05),
                      b.stamp, font=self.f_meta, fill=s.meta)

    # -- what a message carries ---------------------------------------------

    def _paste_custom(self, img: Image.Image, xy: tuple[int, int], px: int,
                      path: Path) -> bool:
        """One custom emoji into the square its fallback would have taken. False when
        the file will not open, and the fallback is drawn instead."""
        tile = media_mod.still(path, px, px)
        if tile is None:
            return False
        if not self._moves(path, xy[0], xy[1], px, px, poster=tile):
            img.paste(tile, xy, tile)
        return True

    def _moves(self, path: Path, x: int, y: int, w: int, h: int,
               shape: tuple = (), image: Image.Image | None = None,
               poster: Image.Image | None = None) -> bool:
        """Note a moving thing on the band being painted, if anybody is collecting.

        True when the caller must NOT paint its still into the band: it is
        see-through and will be the clip alone (see `media.hollow`). Nobody
        collecting — a preview — always paints, since nothing will move there."""
        if self._moving is None:
            return False
        if image is None and not media_mod.facts(path)[3]:
            return False
        empty = image is None and poster is not None and media_mod.hollow(path)
        self._moving.append(Moving(path=path, x=int(x), y=int(y), w=int(w), h=int(h),
                                   shape=shape, msg=self._msg, image=image,
                                   poster=poster if empty else None))
        return empty

    def _paint_media(self, img: Image.Image, draw: ImageDraw.ImageDraw, b: Block,
                     dy: int) -> None:
        m = b.media
        x0, y0, x1, y1 = m.box
        y0, y1 = y0 + dy, y1 + dy
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        if m.kind == "voice":
            self._paint_voice(img, draw, b, (x0, y0, x1, y1))
            return
        pic = media_mod.still(m.path, w, h, m.shape) if m.path is not None else None
        if pic is not None:
            if not self._moves(m.path, x0, y0, w, h, m.shape, poster=pic):
                img.paste(pic, (x0, y0), pic)
        else:
            self._stand_in(img, draw, m, (x0, y0, x1, y1))
        if m.kind == "video" and m.duration:
            # the length in the corner, as a client marks a video it is autoplaying
            self._pill(img, _clock_of(m.duration), (x0, y0), corner="tl",
                       moving=pic is not None and media_mod.facts(m.path)[3])

    def _stand_in(self, img: Image.Image, draw: ImageDraw.ImageDraw, m: Shown,
                  box: tuple[int, int, int, int]) -> None:
        """What is drawn where a file is missing: a sticker's own emoji, else a box of
        the picture's shape in the skin's quiet colour — never nothing, because the
        layout made room for it and an empty gap reads as a rendering fault."""
        x0, y0, x1, y1 = box
        if m.kind == "sticker" and m.emoji:
            side = min(x1 - x0, y1 - y0)
            tile = richtext.emoji_image(richtext._graphemes(m.emoji)[0], int(side * 0.8))
            if tile is not None:
                img.paste(tile, (x0 + (x1 - x0 - tile.width) // 2,
                                 y0 + (y1 - y0 - tile.height) // 2), tile)
                return
        if m.shape and m.shape[0] == "round":
            draw.ellipse([x0, y0, x1, y1], fill=self.skin.divider)
        else:
            r = m.shape[1] if len(m.shape) > 1 else self.skin.radius // 2
            draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=self.skin.divider)

    def _paint_voice(self, img: Image.Image, draw: ImageDraw.ImageDraw, b: Block,
                     box: tuple[int, int, int, int]) -> None:
        """A voice message as a client draws one: the round play button, the bars of
        its waveform, and its length under them."""
        s = self.skin
        m = b.media
        x0, y0, x1, y1 = box
        h = y1 - y0
        accent = ("#4fae4e" if (b.person.mine and s.sides) else
                  (s.react_ink or "#5865f2"))
        d = int(h * 0.86)
        cy = y0 + (h - d) // 2
        draw.ellipse([x0, cy, x0 + d, cy + d], fill=accent)
        t = d * 0.22
        mx, my = x0 + d * 0.54, cy + d / 2
        draw.polygon([(mx - t * 0.8, my - t), (mx - t * 0.8, my + t), (mx + t, my)],
                     fill="#ffffff")
        bx = x0 + d + int(d * 0.28)
        bar = max(2, s.text_px // 10)
        step = bar * 2
        count = max(8, (x1 - bx) // step)
        peaks = (media_mod.waveform(m.path, count) if m.path is not None
                 else tuple([0.3] * count))
        base = y0 + int(h * 0.52)
        tall = h * 0.42
        for i, k in enumerate(peaks[:count]):
            bh = max(bar, int(tall * k))
            x = bx + i * step
            draw.rounded_rectangle([x, base - bh, x + bar, base], radius=bar // 2,
                                   fill=accent)
        draw.text((bx, base + int(h * 0.08)), _clock_of(m.duration), font=self.f_meta,
                  fill=s.meta)

    def _stamp_pill(self, img: Image.Image, b: Block, dy: int) -> None:
        x0, y0, x1, y1 = b.media.box
        moving = b.media.path is not None and media_mod.facts(b.media.path)[3]
        self._pill(img, b.stamp, (x1, y1 + dy), corner="br", moving=moving)

    def _pill(self, img: Image.Image, label: str, at: tuple[int, int], corner: str,
              moving: bool = False) -> None:
        """A label on a dark see-through pill over a picture: the time on a photo, the
        length on a video. When the picture under it moves, the pill is ALSO handed
        to the render as a still to lay over the clip — otherwise the clip, laid over
        the drawing, would cover it."""
        s = self.skin
        px = int(s.meta_px * 0.9)
        face = fonts.load(s.family, px)
        tw = int(self._probe.textlength(label, font=face))
        pad = int(px * 0.5)
        pw, ph = tw + 2 * pad, int(px * 1.5)
        margin = int(px * 0.45)
        x, y = at
        if corner == "br":
            x, y = x - pw - margin, y - ph - margin
        else:
            x, y = x + margin, y + margin
        pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill, "RGBA")
        pd.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=ph // 2, fill=(0, 0, 0, 110))
        pd.text((pad, int(ph * 0.14)), label, font=face, fill="#ffffff")
        img.paste(pill, (int(x), int(y)), pill)
        if moving:
            self._moves(Path(""), x, y, pw, ph, image=pill)

    def _paint_separator(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                         text: str, top: int) -> None:
        """The date, as the centred pill every client draws between two days.

        A pill and not a rule, because that is what it is: a little ground with the
        day on it, floating over the wallpaper."""
        s = self.skin
        px = int(s.meta_px * 1.02)
        face = fonts.load(s.family, px, "medium")
        w = richtext.width(self._probe, text, face, px)
        pad = int(px * 0.9)
        cx = self.left + self.column // 2
        y = top + int(px * 0.5)
        box = (cx - w / 2 - pad, y, cx + w / 2 + pad, y + px * 1.75)
        # over a wallpaper the pill is the bubble's own ground at half strength, which
        # is what Telegram does; on a flat skin it is simply the divider
        draw.rounded_rectangle(box, radius=int(px), fill=(
            _rgba(s.bubble_in, 150) if s.wallpaper else s.divider))
        richtext.draw_line(img, draw, (int(cx - w / 2), int(y + px * 0.34)),
                           text, face, s.meta, px)

    def _bubble(self, img: Image.Image, b: Block, y: int, bottom: int,
                fill: str) -> None:
        """The bubble and its tail, as one shape, drawn through a mask.

        Two things here are the difference between a picture that reads as Telegram
        and one that reads as a rounded rectangle with a spike glued on.

        The first is that the tail is not a triangle. Telegram's bubble goes *square*
        at the corner the tail grows from — all the rounding is on the other three —
        and the tail is a hook whose outer edge curves back **inwards** to the tip.
        Drawing a sharp wedge from a point inside a rounded corner, which is what this
        did before, leaves a notch where the two shapes fail to meet and a spur that
        points the wrong way.

        The second is that `ImageDraw` does not antialias anything. A circle's quadrant
        at this size is a visible staircase, and a thin tail is mostly staircase. So
        the whole outline is drawn into an `L` mask at :data:`SS` times the size and
        shrunk with LANCZOS, which is how the avatars have always been cut out; the
        colour then arrives through the mask and can sit on a wallpaper as happily as
        on a flat ground."""
        s = self.skin
        x0, _, x1, _ = b.box
        right_side = b.person.mine and s.sides
        t = int(s.radius * 0.62) if b.show_tail else 0
        left = x0 - (t if not right_side else 0)
        edge = x1 + (t if right_side else 0)
        w, h = edge - left, bottom - y
        if w <= 0 or h <= 0:
            return

        # a new run starts here: either nobody of theirs is above, or a day pill is
        joins_above = not b.show_head and not b.separator
        img.paste(fill, (left, y), _bubble_mask(
            w, h, x0 - left, x1 - left, t, bool(right_side), s.radius,
            joins_above, not b.show_tail))

    def _paint_reply(self, img: Image.Image, draw: ImageDraw.ImageDraw, b: Block,
                     cx: int, cy: int, x1: int) -> int:
        """The quoted strip above a reply: a coloured rule, who is being answered, and
        as much of what they said as fits on one line."""
        s = self.skin
        who, said = b.reply
        h = self._reply_h() - int(s.gap * 0.5)
        draw.rounded_rectangle([cx, cy, cx + max(4, s.radius // 7), cy + h],
                               radius=2, fill=b.person.colour)
        tx = cx + max(4, s.radius // 7) + int(s.bubble_pad_x * 0.5)
        draw.text((tx, cy), who, font=self.f_meta, fill=b.person.colour)
        room = x1 - tx - s.bubble_pad_x
        lines = richtext.wrap(self._probe, said, self.f_text, s.text_px, room)
        # One line of it, and said to be one line of it. A quote cut off mid-word with
        # nothing to show for it reads as a rendering fault; the ellipsis is what every
        # client puts there, and it is re-wrapped to make room for itself rather than
        # being appended over the bubble's edge.
        if len(lines) > 1:
            dots = richtext.width(self._probe, "…", self.f_text, s.text_px)
            lines = richtext.wrap(self._probe, said, self.f_text, s.text_px, room - dots)
            lines[0] += "…"
        if lines:
            richtext.draw_line(img, draw, (tx, cy + int(s.meta_px * 1.15)),
                               lines[0], self.f_text, s.meta, s.text_px)
        return cy + self._reply_h()

    def _paint_reactions(self, img: Image.Image, draw: ImageDraw.ImageDraw, b: Block,
                         cx: int, cy: int) -> None:
        """The pills under a message: one per emoji, with its count beside it."""
        s = self.skin
        px = int(s.meta_px * 0.92)
        pad = int(px * 0.34)
        ground = s.react_bg or s.divider
        ink = s.react_ink or s.meta
        x = cx
        for emoji, count in b.reactions:
            alt, ref = split_reaction(emoji)
            custom = self._file(ref) if ref else None
            label = str(count) if count > 1 else ""
            label_w = (self._probe.textlength(label, font=self.f_meta) + px * 0.32) if label else 0
            pill = int(pad * 2 + px + label_w)
            draw.rounded_rectangle([x, cy, x + pill, cy + int(px * 1.55)],
                                   radius=int(px * 0.78), fill=ground)
            if custom is None or not self._paste_custom(
                    img, (int(x + pad), int(cy + px * 0.22)), px, custom):
                richtext.draw_line(img, draw, (x + pad, int(cy + px * 0.22)),
                                   alt, self.f_meta, ink, px)
            if label:
                draw.text((x + pad + px + px * 0.32, cy + px * 0.3), label,
                          font=self.f_meta, fill=ink)
            x += pill + int(px * 0.4)

    def _paint_votes(self, draw: ImageDraw.ImageDraw, b: Block, cx: int, cy: int) -> None:
        """Reddit's score line: the arrows and the number between them."""
        s = self.skin
        a = int(s.meta_px * 0.62)
        draw.polygon([(cx + a, cy), (cx, cy + a), (cx + a * 2, cy + a)], fill=s.meta)
        label = str(b.score)
        draw.text((cx + a * 2.8, cy - a * 0.25), label, font=self.f_meta, fill=s.meta)
        dx = cx + a * 3.4 + self._probe.textlength(label, font=self.f_meta)
        draw.polygon([(dx + a, cy + a * 2), (dx, cy + a), (dx + a * 2, cy + a)], fill=s.meta)

    # -- the bits with pictures in them -------------------------------------

    def _avatar(self, img: Image.Image, draw: ImageDraw.ImageDraw, person: Person,
                xy: tuple[int, int], size: int) -> None:
        """Somebody's round picture, or the initials disc every client draws when
        there is none — which is also what an export gives us, since an export carries
        no pictures at all."""
        x, y = xy
        pic = _round_photo(person.avatar, size) if person.avatar else None
        if pic is not None:
            img.paste(pic, (x, y), pic)
            return
        draw.ellipse([x, y, x + size, y + size], fill=person.colour)
        who = person.initials or person.name
        initials = "".join(w[:1] for w in who.split()[:2]).upper() or "?"
        px = int(size * 0.44)
        face = fonts.load(self.skin.family, px, "medium")
        w = richtext.width(self._probe, initials, face, px)
        richtext.draw_line(img, draw, (int(x + (size - w) / 2), int(y + size * 0.26)),
                           initials, face, "#ffffff", px)

    def _paint_header(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                      title: str, avatar: Path | None) -> None:
        s = self.skin
        draw.rectangle([0, 0, self.width, s.header_h], fill=s.header_bg)
        pad = int(s.pad_x * 1.2)
        size = int(s.header_h * 0.62)
        y = (s.header_h - size) // 2
        back = int(size * 0.3)
        draw.line([(pad + back, y + size // 2 - back), (pad, y + size // 2),
                   (pad + back, y + size // 2 + back)], fill=s.header_text,
                  width=max(2, size // 20), joint="curve")
        x = pad + back + int(s.pad_x * 1.4)
        # The picture the bar was handed, and the initials disc only when there is
        # none. This took the argument and dropped it on the floor, which is why the
        # chat's own photo never appeared in the header however it was set — the one
        # avatar on the screen that could not be made to show.
        self._avatar(img, draw, Person(name=title, avatar=avatar, colour=s.divider),
                     (x, y), size)
        face = fonts.load(s.family, int(s.name_px * 1.12), "medium")
        px = int(s.name_px * 1.12)
        # …through the mixed-run drawer, because a chat is as likely as not to be
        # called `ИС-23 🏔❤️🏔` and the text face has none of those: drawn with
        # `draw.text` they come out as three empty boxes, which is what this did.
        richtext.draw_line(img, draw, (x + size + pad, int((s.header_h - px * 1.3) / 2)),
                           title, face, s.header_text, px)
        dots = self.width - pad - int(size * 0.2)
        for i in range(3):
            r = max(2, size // 26)
            cy = y + size // 2 - int(size * 0.22) + i * int(size * 0.22)
            draw.ellipse([dots - r, cy - r, dots + r, cy + r], fill=s.header_text)


def _corners(md: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
             radii: tuple[int, int, int, int]) -> None:
    """A filled rectangle with four independently rounded corners.

    `ImageDraw.rounded_rectangle` takes one radius for all four and a flag per corner
    saying round-or-square, which is one answer short: Telegram's bubbles want a large
    radius on the outside and a small one where they meet the next message. So the box
    is filled, each corner is cut back to a square, and a quarter circle of that
    corner's own radius is put back in it."""
    md.rectangle([x0, y0, x1, y1], fill=255)
    tl, tr, br, bl = radii
    for r, box, centre, arc in (
        (tl, (x0, y0, x0 + tl, y0 + tl), (x0 + tl, y0 + tl), (180, 270)),
        (tr, (x1 - tr, y0, x1, y0 + tr), (x1 - tr, y0 + tr), (270, 360)),
        (br, (x1 - br, y1 - br, x1, y1), (x1 - br, y1 - br), (0, 90)),
        (bl, (x0, y1 - bl, x0 + bl, y1), (x0 + bl, y1 - bl), (90, 180)),
    ):
        if r <= 0:
            continue
        md.rectangle(list(box), fill=0)
        cx, cy = centre
        md.pieslice([cx - r, cy - r, cx + r, cy + r], arc[0], arc[1], fill=255)


def _clock_of(seconds: float) -> str:
    """`0:07`, `1:23` — a length the way every client writes one."""
    n = max(0, int(round(seconds or 0)))
    return f"{n // 60}:{n % 60:02d}"


def _rgba(colour: str, alpha: int) -> tuple[int, int, int, int]:
    """A `#rrggbb` with an alpha on it, for the one thing here drawn see-through."""
    c = colour.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def _round_photo(path: Path, size: int) -> Image.Image | None:
    """A picture cropped square from the centre and masked to a circle."""
    try:
        age = Path(path).stat().st_mtime_ns
    except OSError:
        age = 0
    return _round_photo_of(str(path), age, size)


# Cached on the file's age as well as its name, so a picture replaced under the same
# name is cut again. The result is only ever pasted FROM, never drawn on.
@lru_cache(maxsize=256)
def _round_photo_of(path_s: str, age: int, size: int) -> Image.Image | None:
    path = Path(path_s)
    try:
        pic = Image.open(path).convert("RGBA")
    except OSError as e:
        log.warning("avatar %s will not open (%s)", path, e)
        return None
    side = min(pic.size)
    left = (pic.width - side) // 2
    top = (pic.height - side) // 2
    pic = pic.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    pic.putalpha(mask.resize((size, size), Image.LANCZOS))
    return pic


# The same few bubble outlines come up in every state of a video — one per size of
# message, side and place in a run — and the supersampled mask shrunk with LANCZOS
# was a third of the drawing time, spent two hundred times on the same dozen shapes.
@lru_cache(maxsize=1024)
def _bubble_mask(w: int, h: int, x0: int, x1: int, t: int, right_side: bool,
                 radius: int, joins_above: bool, joins_below: bool) -> Image.Image:
    """The outline of one bubble and its tail, antialiased, at (w, h) — `x0`/`x1` are
    where the bubble proper sits inside that box; see `Canvas._bubble`."""
    mask = Image.new("L", (w * SS, h * SS), 0)
    md = ImageDraw.Draw(mask)
    bx0, bx1 = x0 * SS, x1 * SS - 1
    by1 = h * SS - 1

    # The four corners, each with its own radius, because Telegram gives them
    # four. Within a run of messages from one person the corners that FACE the
    # neighbouring bubble — the ones down the avatar's side — are pulled in tight,
    # so the run reads as one block with seams in it rather than as three separate
    # bubbles that happen to be close. Only the outer side stays fully round.
    big, tight = radius * SS, int(radius * 0.3) * SS
    near_top = tight if joins_above else big
    near_bot = 0 if t else (tight if joins_below else big)
    if right_side:
        radii = (big, near_top, near_bot, big)      # tl, tr, br, bl
    else:
        radii = (near_top, big, big, near_bot)
    _corners(md, bx0, 0, bx1, by1, radii)
    if t:
        r = t * SS
        # a quarter circle centred level with the foot of the bubble's edge and
        # one tail out from it: the arc runs from that edge down to the tip, and
        # is concave because the centre is on the far side of it
        cy = by1 - r
        cx = bx0 - r if not right_side else bx1 + r
        steps = max(8, r // 2)
        arc = []
        for i in range(steps + 1):
            a = math.pi / 2 * i / steps
            dx = math.cos(a) * r
            arc.append((cx + dx if not right_side else cx - dx, cy + math.sin(a) * r))
        md.polygon(arc + [(bx0 if not right_side else bx1, by1)], fill=255)
    return mask.resize((w, h), Image.LANCZOS)
