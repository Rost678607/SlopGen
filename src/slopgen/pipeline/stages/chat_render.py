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
from pathlib import Path

from ...chat import skins
from ...chat.draw import Canvas, Person
from ...chat.scroll import Planner, State, scene_starts
from ..context import AppContext
from ..job import BgAsset, ChatState, VideoJob

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
            # only Telegram takes sides, and only when somebody was named as the
            # account the conversation is being watched from
            mine=bool(skin.sides and me and card.name == me),
        )
    return out


def _stamp(canvas: Canvas, state: ChatState, height: int) -> str:
    """A name for this drawing that changes when the drawing does.

    The whole visible conversation goes into it, every block's geometry and text, the
    window and the skin, so an unchanged state keeps its file across a re-entry to the
    stage and a changed one cannot collide with the file it replaces."""
    h = hashlib.sha1()
    lo, hi = min(state.from_y, state.to_y), max(state.from_y, state.to_y) + height
    h.update(f"{canvas.skin.key}|{canvas.width}|{height}|{state.from_y:.1f}|{state.to_y:.1f}".encode())
    for b in canvas.blocks:
        if b.bottom < lo or b.top > hi:
            continue
        h.update(f"|{b.msg}|{b.top}|{b.height}|{b.show_head}|{b.depth}|{b.stamp}|".encode())
        h.update("".join(b.lines).encode())
        h.update(f"{b.reactions}|{b.score}|{b.person.name}|{b.person.mine}".encode())
    return h.hexdigest()[:16]


def compile_assets(job: VideoJob, states: list[ChatState], roll_s: float,
                   header: Path | None = None) -> None:
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
            scene.bg_assets.append(BgAsset(
                path=st.path, duration=hi - lo, is_photo=True, scroll=True,
                scroll_from=st.from_y, scroll_to=st.to_y,
                # the travel begins when the STATE does, so a piece starting later
                # into it begins partway through — a negative offset the ramp reads
                # as "this already happened", which is exactly what it is
                scroll_at=st.start - lo,
                scroll_s=roll_s if abs(st.to_y - st.from_y) > 0.5 else 0.0,
                overlay=header,
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
    width, height = video.width, video.height
    out_dir = Path(job.workdir) / "chat"
    out_dir.mkdir(parents=True, exist_ok=True)

    wallpaper = asset(ctx, BACKGROUNDS_DIR, cfg.background) if skin.wallpaper else None
    tally = {"drawn": 0, "reused": 0}

    def paint(canvas: Canvas, state: State) -> None:
        y0 = int(min(state.from_y, state.to_y))
        band = int(max(state.from_y, state.to_y) - y0) + height
        path = out_dir / f"state_{_stamp(canvas, state, height)}.png"
        if path.is_file():
            tally["reused"] += 1
        else:
            canvas.band(y0, band).save(path, compress_level=1)
            tally["drawn"] += 1
        state.path = path
        # the window is recorded against the BAND, whose top is where the travel began
        state.from_y -= y0
        state.to_y -= y0
        ctx.progress("render", tally["drawn"] + tally["reused"], len(job.messages))

    planner = Planner(
        ctx, job, width, height, people=people_of(job, ctx), wallpaper=wallpaper,
        top_inset=skin.header_h if cfg.header else 0, paint=paint,
    )
    header = None
    if cfg.header:
        header = out_dir / "header.png"
        planner.canvas.header_image(cfg.title or job.chat_title or "",
                                    asset(ctx, AVATARS_DIR, cfg.header_avatar)).save(header)
    job.chat_states = [
        ChatState(path=s.path, start=s.start, duration=s.duration, msg=s.msg,
                  anchor_scene=s.anchor_scene, from_y=s.from_y, to_y=s.to_y)
        for s in planner.plan()
    ]
    compile_assets(job, job.chat_states, cfg.roll_s, header=header)
    log.info("chat: %d states (%d drawn, %d already there)",
             len(job.chat_states), tally["drawn"], tally["reused"])
    ctx.progress("render", len(job.chat_states), len(job.chat_states))
