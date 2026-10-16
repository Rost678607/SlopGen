"""Chat stage 3: draw the conversation, and lay the drawings on the clock.

This is where the mode stops being a list of messages and becomes a picture. It runs
AFTER the voicing, and it has to: when a piece of a long message appears is a question
about word timings, and word timings do not exist until something has said the words.

What it produces is ordinary. Every state is a PNG and a stretch of seconds, sliced
onto the scenes as the `BgAsset`s the assembler has always rendered, so nothing
downstream of here knows that a chat is different from a slideshow. The one thing it
needed from the assembler is a scroll that is not a Ken-Burns move, and that is one
branch in `stages.assemble._segment` and one filtergraph in `media.ffmpeg`.

The stage is re-enterable and skips the drawing it has already done: a state is named
after its own content, so a second pass over an unchanged conversation re-uses every
file, and a pass after one line was edited redraws that line and the ones under it.
"""

from __future__ import annotations

import hashlib
import logging
import random
from pathlib import Path

from PIL import Image

from ...chat import media as media_mod, skins
from ...chat.draw import Canvas, Moving, Person
from ...chat.scroll import Planner, State, scene_starts
from ..context import AppContext
from ..job import BgAsset, ChatState, ChatTile, VideoJob

log = logging.getLogger(__name__)

# Where the operator's own pictures live. Plain folders rather than config kinds, the
# way `assets/music/` is one: an avatar has no properties to write down, it is a
# square and it is shown, and a TOML per picture would be a file to maintain in
# exchange for nothing.
AVATARS_DIR = "avatars"
BACKGROUNDS_DIR = "chat_bg"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def asset(ctx: AppContext, folder: str, name: str) -> Path | None:
    """One picture out of `assets/<folder>/`, by the name a config stored.

    A name that no longer resolves costs the picture and not the run: the initials
    disc stands in for a missing avatar and the skin's plain ground for a missing
    wallpaper, which is what those fallbacks are for."""
    if not name.strip():
        return None
    root = ctx.g.paths.assets / folder
    direct = root / name
    if direct.is_file():
        return direct
    for ext in IMAGE_EXTS:
        if (root / f"{name}{ext}").is_file():
            return root / f"{name}{ext}"
    log.warning("chat: %s/%s is not there any more", folder, name)
    return None


FOOTAGE_DIR = "footage"
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def split_on(ctx: AppContext) -> bool:
    """Whether something plays under the chat. Only in a frame taller than it is wide:
    the chat's window is 16:9, and in a 16:9 frame that window is the whole frame."""
    v = ctx.g.video
    return bool(ctx.chat.split) and v.height > v.width


def chat_height(ctx: AppContext) -> int:
    """How tall the chat is. The whole frame unless something plays under it, and then
    a 16:9 window at the frame's width — 608 of 1920 in a 1080-wide video — with
    everything below it going to the clip.

    Even, because an odd height is a frame half of x264's encoders refuse outright,
    and the one place to round it is the one place that decides it."""
    v = ctx.g.video
    if not split_on(ctx):
        return v.height
    return max(2, round(v.width * 9 / 16 / 2) * 2)


def fillers(ctx: AppContext) -> list[Path]:
    """The clips the lower half may play, named the way a music track is.

    A file under `assets/footage/`, a FOLDER of them with a trailing slash, or "" for
    everything there. Sorted, because the order is what the roll indexes into and a
    folder listed in whatever order the filesystem hands back would give one machine a
    different clip from another for the same video."""
    root = ctx.g.paths.assets / FOOTAGE_DIR
    if not root.is_dir():
        return []
    want = (ctx.chat.split_clip or "").strip()
    pool = sorted((p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
                  key=lambda p: p.relative_to(root).as_posix())
    if not want:
        return pool
    if want.endswith("/"):
        under = [p for p in pool if p.relative_to(root).as_posix().startswith(want)]
        if under:
            return under
        log.warning("chat: %s holds no clips any more — rolling over all of them", want)
        return pool
    named = [p for p in pool if p.relative_to(root).as_posix() == want or p.name == want]
    if named:
        return named
    log.warning("chat: %r is not in assets/footage any more — rolling instead", want)
    return pool


# The months, in the two languages this program speaks. A table and not a library
# call, because `strftime` gives whatever the machine's locale is — which on a server
# is `C`, and `17 September` in a Russian video is the one word nobody wrote.
MONTHS = {
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"),
    "en": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
}


def day_label(iso: str, lang: str) -> str:
    """`2024-09-17` as the pill says it: `17 сентября`, `17 September`.

    The year is left off unless it is not this one — which is what a messenger does,
    and for the reason it does it: a date needs a year only when somebody might think
    it was recent."""
    try:
        y, m, d = (int(x) for x in str(iso).split("-"))
        month = MONTHS.get(lang, MONTHS["en"])[m - 1]
    except (ValueError, IndexError):
        return str(iso)
    from datetime import date

    out = f"{d} {month}" if lang != "en" else f"{d} {month}"
    return out if y == date.today().year else f"{out} {y}"


def people_of(job: VideoJob, ctx: AppContext) -> dict[str, Person]:
    """Everybody in the conversation, as the drawing needs them.

    Built from the cards, then overruled per message: a persona is the standing answer
    to who somebody is, and `ChatMsg.nick`/`ChatMsg.avatar` is the operator saying
    that THIS message is from somebody who has no card, or from somebody who had
    changed their picture by then."""
    skin = ctx.chat_skin
    me = ctx.chat.me.strip()
    out: dict[str, Person] = {}
    for msg in job.messages:
        if msg.persona in out:
            continue
        card = ctx.persona(msg.persona)
        name = msg.nick or card.name
        if skin.tree and card.handle:
            name = card.handle if card.handle.startswith("u/") else f"u/{card.handle}"
        out[msg.persona] = Person(
            name=name,
            avatar=asset(ctx, AVATARS_DIR, msg.avatar or card.avatar),
            colour=card.colour.strip() or skins.tint(card.name, skin),
            initials=card.name,
            role=card.role.strip(),
            # only Telegram takes sides, and only when somebody was named as the
            # account the conversation is being watched from
            mine=bool(skin.sides and me and card.name == me),
        )
    return out


def _face(person) -> str:
    """Everything about a person that shows on their bubbles, for a file name: the
    picture by its contents' age as well as its name, so a picture replaced under the
    same name is a new file too."""
    pic = person.avatar
    try:
        age = Path(pic).stat().st_mtime_ns if pic else 0
    except OSError:
        age = 0
    return (f"{person.name}|{pic}|{age}|{person.colour}|{person.role}"
            f"|{person.initials}|{person.mine}")


def _stamp(canvas: Canvas, state: ChatState, height: int, pinned=None) -> str:
    """A name for this drawing that changes when the drawing does.

    The whole visible conversation goes into it, every block's geometry and text, the
    window and the skin, so an unchanged state keeps its file across a re-entry to the
    stage and a changed one cannot collide with the file it replaces."""
    h = hashlib.sha1()
    lo, hi = min(state.from_y, state.to_y), max(state.from_y, state.to_y) + height
    h.update(f"{canvas.skin.key}|{canvas.width}|{canvas.column}|{height}"
             f"|{state.from_y:.1f}|{state.to_y:.1f}"
             f"|{state.swipe}|{state.msg}"
             # whether the bottom run's picture was lifted out of this band: the same
             # messages drawn with and without it are two different pictures, and a
             # name that could not tell them apart would hand back the wrong one
             f"|{'pinned' if pinned else 'inline'}".encode())
    for b in canvas.blocks:
        if b.bottom < lo or b.top > hi:
            continue
        h.update(f"|{b.msg}|{b.top}|{b.height}|{b.show_head}|{b.depth}|{b.stamp}|".encode())
        h.update("".join(b.lines).encode())
        # The whole face and not just the name. With the name alone, a picture changed
        # on somebody's card drew the same file name as before, and the old drawing was
        # handed back with the old face on it — everywhere except at the foot of a run,
        # where the picture is on the overlay, whose name did carry it.
        h.update(f"{b.reactions}|{b.score}|{_face(b.person)}".encode())
        if b.media is not None:
            h.update(f"|{b.media.kind}|{b.media.path}|{b.media.box}|{b.media.shape}".encode())
        h.update(f"|{[(a, str(p)) for a, p in b.custom]}".encode())
    return h.hexdigest()[:16]


def _tiles(moving: list[Moving], out_dir: Path, fps: float, at: float,
           born: dict) -> list[dict]:
    """The moving things one state's band holds, as the clips to lay over it.

    Each one's clip is made once for its file, size and outline, and shared by every
    state and every message that shows the same thing at the same size. `born` is when
    each moving thing was first on screen, keyed by whose it is and where in that
    message: a sticker plays on from wherever it had got to when the next message
    lands, and restarting it at every state would make it twitch on every line."""
    out: list[dict] = []
    seen: dict = {}
    for mv in moving:
        if mv.image is not None:
            # a label riding on a moving picture: a still, laid above its clip
            key = hashlib.sha1(mv.image.tobytes()).hexdigest()[:16]
            at_png = out_dir / f"deco_{key}.png"
            if not at_png.is_file():
                mv.image.save(at_png)
            out.append({"path": str(at_png), "x": mv.x, "y": mv.y, "w": mv.w,
                        "h": mv.h, "t0": at, "length": 0.0})
            continue
        made = media_mod.clip(mv.path, mv.w, mv.h, fps, out_dir / "tiles", mv.shape)
        if made is None:
            continue
        poster = ""
        if mv.poster is not None:
            key = hashlib.sha1(mv.poster.tobytes()).hexdigest()[:16]
            at_png = out_dir / f"poster_{key}.png"
            if not at_png.is_file():
                mv.poster.save(at_png)
            poster = str(at_png)
        clip, length = made
        # the n-th moving thing of this message at this size: a custom emoji used
        # twice in one line is two of them, and each keeps its own clock
        ident = (mv.msg, str(mv.path), mv.w, mv.h)
        seen[ident] = seen.get(ident, 0) + 1
        key = (*ident, seen[ident])
        t0 = born.setdefault(key, at)
        out.append({"path": str(clip), "x": mv.x, "y": mv.y, "w": mv.w, "h": mv.h,
                    "t0": t0, "length": length, "poster": poster})
    return out


def with_posters(img: "Image.Image", tiles: list[dict], dx: float = 0.0,
                 dy: float = 0.0) -> "Image.Image":
    """The drawing with the first frames of its see-through moving things put back —
    for whatever shows ONE picture of a state: the preview, and the swipe. `dx`/`dy`
    is where the picture's own origin sits on the drawing the tiles were placed on."""
    posters = [t for t in tiles if t.get("poster")]
    if not posters:
        return img
    out = img.copy()
    for t in posters:
        try:
            with Image.open(t["poster"]) as p:
                p = p.convert("RGBA")
                out.paste(p, (int(t["x"] - dx), int(t["y"] - dy)), p)
        except OSError:
            continue
    return out


def _overlay(canvas: "Canvas", out_dir: Path, width: int, height: int,
             bar: Path | None, pinned, window: float) -> Path | None:
    """The layer that does not scroll: the header bar, and the picture at the foot of
    the run that is currently at the bottom.

    One image the size of the whole frame rather than the strip the bar used to be,
    because the two things on it belong at opposite ends of it. Nothing downstream
    minds the new shape — `make_chat_part` composites it with `overlay=0:0` and the
    room pastes it at the origin — so this is the same single layer it always was,
    with more on it.

    The picture is placed where the window will COME TO REST, which is where it would
    have been anyway once the roll finished. That is the whole trick: through the roll
    it therefore does not move at all, and a new message from the same person slides up
    past a face that stays put, which is what a client looks like."""
    if bar is None and pinned is None:
        return None
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    key = hashlib.sha1()
    key.update(f"{width}x{height}|{bar}".encode())
    if bar is not None and Path(bar).is_file():
        with Image.open(bar) as strip:
            strip = strip.convert("RGBA")
            img.paste(strip, (0, 0), strip)
    if pinned is not None:
        person, ax, foot, size, _msg = pinned
        ay = int(round(foot - window - size))
        top_guard = canvas.skin.header_h if bar is not None else 0
        ay = max(top_guard, min(ay, height - size))
        canvas.paint_avatar(img, person, (ax, ay), size)
        key.update(f"|{_face(person)}|{ax}|{ay}|{size}".encode())
    at = out_dir / f"over_{key.hexdigest()[:16]}.png"
    if not at.is_file():
        img.save(at)
    return at


def _barred(view: "Image.Image", bar: Path | None) -> "Image.Image":
    """One screen with its header bar on it, for a picture the bar has to travel in."""
    if bar is None or not Path(bar).is_file():
        return view
    out = view.copy()
    with Image.open(bar) as strip:
        strip = strip.convert("RGBA")
        out.paste(strip, (0, 0), strip)
    return out


def compile_assets(job: VideoJob, states: list[ChatState], roll_s: float,
                   swipe_s: float = 0.35, width: int = 1080,
                   pool: list[Path] | None = None, chat_h: int = 0,
                   change_s: float = 0.0, seed: str = "") -> None:
    """Slice the states onto the scenes, as the background every scene already has.

    A state that straddles a scene boundary becomes two pieces of the same drawing,
    the second carrying the remainder of the travel: the manoeuvre the frame base
    already makes with `BgAsset.move_at`, and for the same reason. The assembler
    renders one scene at a time and knows nothing about the clock outside it."""
    starts = scene_starts(job.scenes)
    for scene in job.scenes:
        scene.bg_assets = []
    for st in states:
        if st.path is None:
            continue
        for i, scene in enumerate(job.scenes):
            lo = max(st.start, starts[i])
            hi = min(st.start + st.duration, starts[i] + scene.duration)
            if hi - lo <= 1e-3:
                continue
            moves = abs(st.to_y - st.from_y) > 0.5
            # What plays underneath, and where in it. One clip held for the whole
            # video is the ordinary answer and the one the gameplay loops want — the
            # seek is the piece's own place on the clock, so the action carries across
            # every cut instead of restarting on each message. A change interval rolls
            # a different clip every so often, and then the seek restarts with it.
            under = seek = None
            if pool:
                turn = int(lo / change_s) if change_s > 0 else 0
                under = pool[random.Random(f"{seed}|{turn}").randrange(len(pool))]
                seek = (lo - turn * change_s) if change_s > 0 else lo
            scene.bg_assets.append(BgAsset(
                path=st.path, duration=hi - lo, is_photo=True, scroll=True,
                scroll_from=st.from_y, scroll_to=st.to_y,
                # a swipe travels sideways and holds still vertically: it is the seam
                # between two conversations, not a scroll inside one
                scroll_from_x=0.0 if st.swipe else 0.0,
                scroll_to_x=float(width) if st.swipe else 0.0,
                # the travel begins when the STATE does, so a piece starting later
                # into it begins partway through — a negative offset the ramp reads
                # as "this already happened", which is exactly what it is
                scroll_at=st.start - lo,
                scroll_s=swipe_s if st.swipe else (roll_s if moves else 0.0),
                overlay=st.overlay,
                ground=None if st.swipe else st.ground,
                tiles=[] if st.swipe else [
                    ChatTile(path=Path(t["path"]), x=t["x"], y=t["y"], w=t["w"],
                             h=t["h"], length=t["length"],
                             seek=((lo - t["t0"]) % t["length"]) if t["length"] > 0 else 0.0)
                    for t in st.tiles],
                filler=under, start=float(seek or 0.0),
                chat_h=chat_h if under is not None else 0,
            ))


def run(job: VideoJob, ctx: AppContext) -> None:
    if not job.messages:
        raise ValueError("there is no conversation to draw")
    cfg = ctx.chat
    skin = ctx.chat_skin
    came_from = cfg.source if cfg.source in skins.SKINS else skin.key
    if not skins.compatible(came_from, skin.key):
        raise ValueError(
            f"a {came_from} conversation cannot be drawn in the {skin.key} interface: "
            "reddit scores with karma and threads by indent, and the messengers do "
            "neither, so pick a skin on the same side of that line"
        )

    video = ctx.g.video
    # The chat is drawn at the height it will OCCUPY, which is the whole frame unless
    # something is playing under it. Everything about the layout follows from that
    # number — how much fits before the view has to travel, where it rests — so it is
    # decided once, here, and handed to the planner as the screen's height.
    width, height = video.width, chat_height(ctx)
    pool = fillers(ctx) if split_on(ctx) else []
    if split_on(ctx) and not pool:
        log.warning("chat: the split is on and assets/footage holds no clips — "
                    "the lower half would be black, so it is skipped")
    out_dir = Path(job.workdir) / "chat"
    out_dir.mkdir(parents=True, exist_ok=True)

    wallpaper = asset(ctx, BACKGROUNDS_DIR, cfg.background) if skin.wallpaper else None
    layered = wallpaper is not None
    ground_png: Path | None = None
    tally = {"drawn": 0, "reused": 0}
    # What the last state left on screen, kept so that the next conversation can slide
    # in over it. An image rather than a path: the swipe needs the two screens in ONE
    # drawing, and re-opening the previous PNG to crop the same window out of it again
    # would be the same pixels at the cost of a decode per seam.
    seen: dict[str, object] = {"view": None, "bar": None, "msg": None}
    born: dict = {}  # when each moving thing was first on screen (see `_tiles`)

    def paint(canvas: Canvas, state: State) -> None:
        y0 = int(min(state.from_y, state.to_y))
        band_h = int(max(state.from_y, state.to_y) - y0) + height
        # The picture at the foot of the bottom run comes OUT of the scrolling band
        # and onto the overlay that does not scroll (see `Canvas.avatar_of`). A swipe
        # is the exception and keeps it in the picture: there the whole screen moves,
        # and a face that stayed put while the chat slid out from under it would be
        # the one thing giving the shot away.
        #
        # And only a picture that was ALREADY on screen: one carried down from the
        # message above (the run goes on) or from this message's own earlier state (a
        # reveal, a reaction). The first message of a run brings its picture with it —
        # pinned, the face stood waiting at its resting place while the bubble it
        # belongs to was still rolling up from below. Left in the band, it rides in
        # beside the bubble, which is what a client does.
        pinned = None if state.swipe else canvas.avatar_of(state.msg)
        if pinned is not None and canvas.blocks[-1].show_head \
                and seen["msg"] != state.msg:
            pinned = None
        # the block whose picture was lifted, which is not always the state's own
        # message: it is whichever block the picture actually belongs to
        moving: list[Moving] = []
        # With a wallpaper the band is drawn see-through and the wallpaper goes UNDER
        # the window as a layer of its own: painted in, it travelled up with the
        # bubbles on every roll and jumped back on the next state.
        band = canvas.band(y0, band_h, pin=None if pinned is None else pinned[4],
                           moving=moving, ground=not layered)
        # the window as it will rest at the END of this state, which is what the next
        # one slides away from
        rest = int(min(max(state.to_y - y0, 0), max(band_h - height, 0)))
        # what the screen looks like at rest, wallpaper and stickers and all: the
        # swipe slides THIS away, and a swipe has no clips laid over it
        view = band.crop((0, rest, width, rest + height))
        if layered:
            under = canvas.ground(height).copy()
            under.paste(view, (0, 0), view)
            view = under
        for mv in moving:
            if mv.poster is not None:
                view.paste(mv.poster, (mv.x, mv.y - rest), mv.poster)

        ci = where[state.msg] if 0 <= state.msg < len(where) else 0
        picture, name = band, _stamp(canvas, state, height, pinned)
        if layered:
            # a see-through drawing is another file than the same screen painted on
            # its wallpaper, and must not be handed back for it
            name = f"lay_{name}"
        if state.swipe and seen["view"] is not None:
            # the old screen and the new one side by side, in one drawing. The window
            # then travels across the join, which is a swipe — and is the same crop
            # with a ramp on the other axis (see `ffmpeg.make_chat_part`). The bars go
            # INTO the picture here: moving to another chat moves the whole screen, and
            # a bar that stayed put while everything under it slid would be the one
            # thing that gave the screenshot away.
            picture = Image.new("RGB", (width * 2, height), skin.bg)
            picture.paste(_barred(seen["view"], seen["bar"]), (0, 0))
            picture.paste(_barred(view, bars.get(ci)), (width, 0))
            name = f"swipe_{name}"
            state.from_y = state.to_y = 0.0
        else:
            state.swipe = False  # nothing to slide away from: the first screen of all
            state.overlay = _overlay(canvas, out_dir, width, height, bars.get(ci),
                                     pinned, y0 + rest)
            state.ground = ground_png
            # the window is recorded against the BAND, whose top is where it began
            state.from_y -= y0
            state.to_y -= y0
            # …and so is everything moving on it, which is exactly where the band
            # painted it
            state.tiles = _tiles(moving, out_dir, float(video.fps), state.start, born)

        path = out_dir / f"state_{name}.png"
        if path.is_file():
            tally["reused"] += 1
        else:
            picture.save(path, compress_level=1)
            tally["drawn"] += 1
        state.path = path
        seen["view"], seen["bar"], seen["msg"] = view, bars.get(ci), state.msg
        ctx.progress("render", tally["drawn"] + tally["reused"], len(job.messages))

    planner = Planner(
        ctx, job, width, height, people=people_of(job, ctx), wallpaper=wallpaper,
        top_inset=skin.header_h if cfg.header else 0, paint=paint,
        day_label=lambda iso: day_label(iso, ctx.params.lang),
    )
    if layered:
        # one screen of wallpaper, drawn once, under every state that is not a swipe
        key = hashlib.sha1(f"{wallpaper}|{wallpaper.stat().st_mtime_ns}|{width}x{height}"
                           .encode()).hexdigest()[:12]
        ground_png = out_dir / f"ground_{key}.png"
        if not ground_png.is_file():
            planner.canvas.ground(height).save(ground_png)
    # which conversation each message belongs to, by its place in the video — the one
    # thing `paint` needs that a state does not carry
    where = [ci for ci, conv in enumerate(job.conversations) for _ in conv.messages]
    # One bar per conversation, drawn once and laid over every state of it. The title
    # is the conversation's own, and the preset's or the run's only where it does not
    # name itself — three threads in one video are three different chats.
    bars: dict[int, Path] = {}
    if cfg.header:
        for ci, conv in enumerate(job.conversations):
            title = conv.title or cfg.title or job.chat_title or ""
            # the conversation's own picture first: the one Telegram handed over with
            # the chat, which is the whole point of fetching it. The run's setting is
            # the fallback for conversations that arrived without one — an export, or
            # something typed by hand — and the initials disc is the fallback for that.
            pic = asset(ctx, AVATARS_DIR, conv.avatar or cfg.header_avatar)
            key = f"{title}\x00{pic or ''}"
            at = out_dir / f"header_{hashlib.sha1(key.encode()).hexdigest()[:12]}.png"
            if not at.is_file():
                planner.canvas.header_image(title, pic).save(at)
            bars[ci] = at
    job.chat_states = [
        ChatState(path=s.path, start=s.start, duration=s.duration, msg=s.msg,
                  anchor_scene=s.anchor_scene, from_y=s.from_y, to_y=s.to_y,
                  swipe=s.swipe, overlay=s.overlay, ground=s.ground,
                  tiles=list(s.tiles))
        for s in planner.plan()
    ]
    compile_assets(job, job.chat_states, cfg.roll_s,
                   swipe_s=max(0.0, float(cfg.swipe_s)), width=width,
                   pool=pool, chat_h=height,
                   change_s=max(0.0, float(cfg.split_change_s)),
                   # seeded on the run, so the same video draws the same clips every
                   # time and on every machine, as the music roll already does
                   seed=str(job.workdir))
    log.info("chat: %d states (%d drawn, %d already there)",
             len(job.chat_states), tally["drawn"], tally["reused"])
    ctx.progress("render", len(job.chat_states), len(job.chat_states))
