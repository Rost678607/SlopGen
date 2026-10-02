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
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts, richtext
from .skins import Skin, column as skins_column

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

    @property
    def bottom(self) -> int:
        return self.top + self.height


class Canvas:
    """A conversation being laid out, and the renderer of what has been laid out."""

    def __init__(self, skin: Skin, width: int, *, wallpaper: Path | None = None,
                 top_inset: int = 0):
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

    def _lay(self, *, top: int, msg: int, person: Person, text: str,
             stamp: str = "", reply: tuple[str, str] | None = None,
             reactions: list[tuple[str, int]] | None = None,
             score: int | None = None, depth: int = 0,
             show_head: bool = True, separator: str = "") -> Block:
        s = self.skin
        reactions = list(reactions or [])
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
        lines = richtext.wrap(self._probe, text, self.f_text, s.text_px, inner)

        line_h = int(s.text_px * s.line_h)
        sep_h = self._sep_h() if separator else 0
        top += sep_h
        h = s.bubble_pad_y * 2 + max(len(lines), 1) * line_h
        if show_head and (s.bubbles and not person.mine or not s.bubbles):
            h += self._head_h()
        if reply:
            h += self._reply_h()
        if reactions and not s.votes:
            h += self._react_h()
        if s.votes and score is not None:
            h += int(s.meta_px * 1.9)

        # the box is as wide as the widest line, never wider than the limit, and never
        # so narrow that the timestamp hangs off the end of it
        widest = max((richtext.width(self._probe, ln, self.f_text, s.text_px)
                      for ln in lines), default=0)
        if show_head and s.bubbles and not person.mine:
            widest = max(widest, self._probe.textlength(person.name, font=self.f_name))
        # Telegram sets the time INSIDE the bubble, on the last line when it fits
        # there and on a line of its own when it does not. Deciding that here is what
        # keeps it off the words: the bubble is either widened to make room beside the
        # last line, or made taller to put the time under it.
        if stamp and s.bubbles and not (reactions and not s.votes):
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
        return Block(msg=msg, top=top - sep_h, height=h + sep_h, lines=lines,
                     person=person, box=(x0, top, x1, top + h), show_head=show_head,
                     stamp=stamp, reply=reply, reactions=reactions, score=score,
                     depth=depth, show_tail=True, separator=separator)

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
             reveal: dict[int, int] | None = None) -> Image.Image:
        """Render the canvas from `y0` for `height` pixels.

        `reveal` caps how many LINES of a block are painted, by message index — which
        is how a message that is being read out arrives a piece at a time without the
        bubble around it jumping to its final size on the first word. The bubble is
        laid out for what is revealed so far (see the caller), so this only has to
        stop painting.

        The header is painted last and does not scroll, because in a real client it
        does not."""
        s = self.skin
        img = self._ground_for(height).copy() if self.wallpaper else Image.new(
            "RGB", (self.width, height), s.bg)
        draw = ImageDraw.Draw(img, "RGBA")
        top_guard = s.header_h if header else 0
        for block in self.blocks:
            if block.bottom < y0 or block.top > y0 + height:
                continue
            self._paint(img, draw, block, -y0, cap=(reveal or {}).get(block.msg))
        if header:
            self._paint_header(img, draw, *header)
        del top_guard
        return img

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
               cap: int | None = None) -> None:
        s = self.skin
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

        if s.bubbles:
            fill = s.bubble_out if b.person.mine else s.bubble_in
            draw.rounded_rectangle([x0, y, x1, bottom], radius=s.radius, fill=fill)
            self._tail(draw, b, dy, fill)

        cx = x0 + (s.bubble_pad_x if s.bubbles else 0)
        cy = y + (s.bubble_pad_y if s.bubbles else 0)
        text_colour = s.text_out if b.person.mine else s.text_in

        head_x = cx
        # The picture goes with the TAIL and not with the name: a run of messages from
        # one person carries the name on the first and the little spur on the last, and
        # the avatar sits beside the spur, at the foot of the run. Drawn at the top it
        # floats beside a bubble the person is still in the middle of saying.
        show_pic = (b.show_tail if s.bubbles else b.show_head)
        if show_pic and s.avatar and not (b.person.mine and s.sides):
            ax = self.left + s.pad_x + (s.indent * b.depth if s.tree else 0)
            ay = y if (s.tree or not s.bubbles) else max(y, bottom - s.avatar)
            self._avatar(img, draw, b.person, (ax, ay), s.avatar)
            if s.tree:
                # reddit stands the picture ON the author line and runs the comment
                # underneath it at full width, so only this line steps aside
                head_x = ax + s.avatar + int(s.pad_x * 0.5)
        if b.show_head and (not s.bubbles or not b.person.mine):
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

        line_h = int(s.text_px * s.line_h)
        shown = b.lines if cap is None else b.lines[:max(cap, 0)]
        for line in shown:
            richtext.draw_line(img, draw, (cx, cy), line, self.f_text, text_colour, s.text_px)
            cy += line_h

        if s.votes and b.score is not None:
            self._paint_votes(draw, b, cx, bottom - int(s.meta_px * 1.7))
        elif b.reactions:
            self._paint_reactions(img, draw, b, cx, bottom - self._react_h() + int(s.meta_px * 0.3))
        if b.stamp and s.bubbles:
            w = self._probe.textlength(b.stamp, font=self.f_meta)
            draw.text((x1 - s.bubble_pad_x - w, bottom - s.bubble_pad_y - s.meta_px * 1.05),
                      b.stamp, font=self.f_meta, fill=s.meta)

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

    def _tail(self, draw: ImageDraw.ImageDraw, b: Block, dy: int, fill: str) -> None:
        """Telegram's little spur at the bottom corner of a bubble. It is two
        triangles' worth of pixels and most of what makes the picture read as
        Telegram rather than as a rounded rectangle."""
        if not self.skin.bubbles or not b.show_tail:
            return
        s = self.skin
        x0, _, x1, _ = b.box
        foot = b.bottom + dy
        t = int(s.radius * 0.58)
        if b.person.mine and s.sides:
            draw.polygon([(x1 - 2, foot - t * 2), (x1 + t, foot), (x1 - 2, foot)], fill=fill)
        else:
            draw.polygon([(x0 + 2, foot - t * 2), (x0 - t, foot), (x0 + 2, foot)], fill=fill)

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
            label = str(count) if count > 1 else ""
            label_w = (self._probe.textlength(label, font=self.f_meta) + px * 0.32) if label else 0
            pill = int(pad * 2 + px + label_w)
            draw.rounded_rectangle([x, cy, x + pill, cy + int(px * 1.55)],
                                   radius=int(px * 0.78), fill=ground)
            richtext.draw_line(img, draw, (x + pad, int(cy + px * 0.22)),
                               emoji, self.f_meta, ink, px)
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
        self._avatar(img, draw, Person(name=title, colour=s.divider), (x, y), size)
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


def _rgba(colour: str, alpha: int) -> tuple[int, int, int, int]:
    """A `#rrggbb` with an alpha on it, for the one thing here drawn see-through."""
    c = colour.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def _round_photo(path: Path, size: int) -> Image.Image | None:
    """A picture cropped square from the centre and masked to a circle."""
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
