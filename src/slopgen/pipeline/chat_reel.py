"""The chat room's preview reel: the video as the render would make it, played in the
browser instead of encoded.

Everything that moves in a chat video is decided before ffmpeg is reached. The render
stage draws the states — a PNG each, a stretch of the clock, a window that may travel
down it or across a seam — and lays the moving stickers over them as clips with alpha;
the assembler only crops, ramps and overlays. So the honest preview is not a second
animation written for the browser: it is the very states the render stage paints,
handed to a page that does the crop, the ramp and the overlay itself. The same goes
for the sound — the voiced takes, a voice message's own recording, the pop each
message lands with, the music under it — which are files and moments the assembler
mixes, listed here instead of mixed.

**The clock** is the one thing a conversation being typed does not have yet, and
:func:`timeline` supplies it: the job's own scenes when they are laid, voiced and still
these messages; otherwise the scenes laid afresh (`chat_script.lay`), with every line
that has already been voiced keeping its take and only the rest guessed at from its
length. An edit to one line therefore costs the preview that one line's real voice,
not the whole conversation's.

Nothing here touches the job it is handed. It works on a deep copy, and what it writes
is what the render stage writes — the drawings under the run's `chat/` folder, named by
what is on them — so a preview drawn now is a drawing the render finds already made.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .context import AppContext
from .job import VideoJob

log = logging.getLogger(__name__)


def laid(job: VideoJob) -> bool:
    """Whether the job's scenes are a clock for exactly these messages.

    The room edits conversations without re-laying them (see `chatroom`), so a line
    typed after `script` ran points at nothing, a line retyped points at a scene saying
    something else, and a line laid but not yet voiced is a scene 0s long."""
    msgs, scenes = job.messages, job.scenes
    return bool(msgs) and all(
        0 <= m.scene < len(scenes) and scenes[m.scene].duration > 0
        and scenes[m.scene].text in (m.text, "") for m in msgs) and all(
        a.scene < b.scene for a, b in zip(msgs, msgs[1:]))


def timeline(job: VideoJob, ctx: AppContext) -> tuple[VideoJob, bool]:
    """A copy of the job with a whole clock under it, and whether that clock is the
    real one."""
    from .drama import speech_seconds
    from .stages import chat_script

    copy = job.model_copy(deep=True)
    if laid(job):
        return copy, True
    chat_script.lay(copy, ctx)
    # The takes already made, by what they say — and by who says it where that still
    # agrees, because the delivery pass may have recast a line since it was laid.
    voiced = [s for s in job.scenes if s.audio and s.duration > 0 and not s.hush]
    by_voice = {(s.text, s.voice): s for s in voiced}
    by_text = {s.text: s for s in voiced}
    lang = ctx.params.lang
    rate = int(getattr(ctx.params, "tts_rate", 0) or 0)
    for s in copy.scenes:
        if s.duration > 0 or s.hush:
            continue
        old = by_voice.get((s.text, s.voice)) or by_text.get(s.text)
        if old is not None and Path(old.audio).is_file():
            s.audio, s.duration = old.audio, old.duration
            s.words = list(old.words)
            s.audio_tempo = old.audio_tempo
        else:
            s.duration = max(speech_seconds(s.text, lang, rate), 0.3)
    return copy, False


def key(job: VideoJob, ctx: AppContext) -> str:
    """What a reel is a reel OF: the conversations and their clock, the settings it is
    drawn with and the cards of everybody in it. Equal keys draw equal reels, so the
    room can hand back the last one instead of drawing it again."""
    import hashlib

    h = hashlib.sha1()
    h.update(job.model_dump_json(include={"conversations", "scenes", "chat_title"}).encode())
    h.update(ctx.params.model_dump_json().encode())
    h.update(ctx.chat.model_dump_json().encode())
    for name in sorted({m.persona for m in job.messages}):
        card = ctx.persona(name)
        h.update(card.model_dump_json().encode() if card is not None else name.encode())
    return h.hexdigest()


def build(job: VideoJob, ctx: AppContext) -> dict:
    """Everything the page needs to play the video, as paths and seconds.

    Paths and not URLs: which files a browser may fetch is the web layer's decision,
    and it makes it by serving only what is listed here."""
    from .effects import chat_sounds
    from .stages import chat_render
    from .stages.assemble import music_for

    copy, exact = timeline(job, ctx)
    chat_render.run(copy, ctx)
    cfg = ctx.chat
    v = ctx.g.video
    top = chat_render.chat_height(ctx)
    total = sum(s.duration for s in copy.scenes)

    starts, at = [], 0.0
    for s in copy.scenes:
        starts.append(at)
        at += s.duration
    marks = [starts[m.scene] if 0 <= m.scene < len(starts) else 0.0 for m in copy.messages]
    durs = [b - a for a, b in zip(marks, marks[1:] + [total])]

    states = []
    for st in copy.chat_states:
        if st.path is None:
            continue
        moves = abs(st.to_y - st.from_y) > 0.5
        states.append({
            "path": Path(st.path), "start": st.start, "dur": st.duration, "msg": st.msg,
            "from_y": st.from_y, "to_y": st.to_y, "swipe": st.swipe,
            # how long the window takes to get where it is going, exactly as the
            # assembler is told it (see `chat_render.compile_assets`)
            "travel": (max(0.0, float(cfg.swipe_s)) if st.swipe
                       else (float(cfg.roll_s) if moves else 0.0)),
            "overlay": Path(st.overlay) if st.overlay else None,
            "tiles": [] if st.swipe else [
                {"path": Path(t["path"]), "x": t["x"], "y": t["y"], "w": t["w"],
                 "h": t["h"], "t0": t["t0"], "length": t["length"],
                 "poster": Path(t["poster"]) if t.get("poster") else None}
                for t in st.tiles],
        })

    # What plays under the chat when the screen is split: the pieces the render handed
    # the assembler, run together where the same clip simply carries on.
    under = []
    for i, s in enumerate(copy.scenes):
        clock = starts[i]
        for a in s.bg_assets:
            if a.filler is not None:
                piece = {"path": Path(a.filler), "at": clock, "dur": a.duration,
                         "seek": float(a.start)}
                last = under[-1] if under else None
                if (last and last["path"] == piece["path"]
                        and abs(last["at"] + last["dur"] - clock) < 1e-3
                        and abs(last["seek"] + last["dur"] - piece["seek"]) < 1e-3):
                    last["dur"] += a.duration
                else:
                    under.append(piece)
            clock += a.duration

    # The sound: every line's take — a voice message's own recording is one of these
    # (`chat_script.lay` hands it to the scene as its audio) — and every message's pop.
    sounds = []
    for i, s in enumerate(copy.scenes):
        if s.audio and Path(s.audio).is_file():
            sounds.append({"path": Path(s.audio), "at": starts[i], "dur": s.duration,
                           "rate": float(s.audio_tempo or 1.0), "gain": 1.0,
                           "kind": "voice"})
    for d in chat_sounds(copy, ctx, 0.0, total):
        if d.sound and Path(d.sound).is_file():
            sounds.append({"path": Path(d.sound), "at": d.start, "dur": 0.0, "rate": 1.0,
                           "gain": float(d.volume), "kind": "sfx"})
    music = music_for(ctx.params, ctx.g, job)

    return {
        "exact": exact, "total": total, "width": v.width, "height": v.height,
        "top": top, "marks": marks, "durs": durs,
        "states": states, "under": under, "sounds": sounds,
        "music": ({"path": Path(music), "gain": float(ctx.g.audio.music_volume)}
                  if music else None),
    }
