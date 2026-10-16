"""Chat stage 1: turn a conversation into a timeline.

Three things happen here and they are kept apart on purpose.

**Casting** decides who is read aloud and by whom. A persona carries a voice on its
card, the run may overrule every card at once (`RunParams.chat_voice`), and `none`
overrules them the other way — nobody is read and the video is a silent chat. That
last one is not a degenerate case to be guarded against: a conversation people read
off the screen, over music, is a format, and the clock below handles it without a
branch because an unread message is simply a line with no voice (`Scene.silent`).

**Laying** turns messages into scenes, which is the whole reason this mode gets the
montage room, the subtitle pass and the assembler for nothing. One message is one
scene — same text, same voice spec, same word timings once it is voiced — and the
pause after it is a scene too (`Scene.hush`), because that is what a stretch of the
clock with nothing spoken over it already is here. Two scenes per message rather than
one field, so the operator can drag one gap longer without the setting moving every
other gap in the video.

**Translation** is the one pass that costs a model, and it is in `llm/chat.py`
because the rule it has to follow cannot be written as a setting: a Russian chat with
the odd English word in it, where the English is the joke, must come out untouched,
and only something reading the whole conversation can tell that from a message that
merely happens to be foreign.

What is NOT here is the breaking of long messages into the pieces they are revealed
in. That is a question about word timings, which do not exist until the voicing has
happened, so it lives one stage further down (see `chat_render`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...chat import media as media_mod
from ..context import AppContext
from ..job import ChatMsg, Scene, VideoJob

log = logging.getLogger(__name__)

# `RunParams.chat_voice` spelled as "nobody", the way the music select spells silence
# (`stages.assemble.MUSIC_NONE`): a reserved value in the same field rather than a
# second one, because the question has three answers — the cards decide, everybody
# gets this voice, nobody is read — and a select with three entries is how it is asked.
VOICE_NONE = "none"


def voice_for(msg: ChatMsg, ctx: AppContext) -> str:
    """The voice spec this message is read with, or `""` for not read at all.

    The run's override wins over the card, because it is the later and more specific
    statement: somebody sat down to make THIS video with one narrator, and re-carding
    eight personas to say so would be the settings eating the evening. And the
    message's own voice wins over both, for the same reason one step further: it is
    somebody choosing how this one line is said."""
    own = (getattr(msg, "voice", "") or "").strip()
    if own == VOICE_NONE:
        return ""   # this one line is read by nobody, whoever its person is
    if own:
        return own
    want = (ctx.params.chat_voice or "").strip()
    if want == VOICE_NONE:
        return ""
    if want:
        return want
    return ctx.persona(msg.persona).voice.strip()


def lay(job: VideoJob, ctx: AppContext, carry: bool = False) -> None:
    """Rebuild the scene list out of the messages, and point each message at its scene.

    Destructive by design, and safe for exactly the reason the stage can be re-entered
    at all: everything a scene carries that is worth keeping — the text, who says it,
    how fast — is carried by the MESSAGE, which is the document the operator edits.
    What is thrown away is the derived half: durations and audio paths that belong to
    a conversation that has since changed.

    `carry` keeps the takes already synthesized. The voicing stage files a take by the
    INDEX of its scene (`tts/scene_07.mp3`, with its timings beside it) and finds it
    again only there, so a message inserted above line 7 used to make every take below
    it a stranger in its own slot, and the next voicing paid for the whole tail of the
    conversation again. With `carry` each take moves to wherever its line now is (see
    :func:`_carry_takes`). Only the stage itself asks for it: a preview lays a COPY of
    the job, and must not move files the real one is pointing at."""
    was = list(job.scenes)
    cfg = ctx.chat
    gap = max(0.0, float(cfg.gap_s))
    # The reactions get the clock they were promised, ON TOP of the gap rather than
    # out of it. They pop once the message has been read, which is exactly where the
    # gap is — and squeezed into it, a two-second flurry over a 1.2s gap came out as
    # pops a fifth of a second apart with the last one up for a single frame before
    # the next bubble covered it, which on screen is every reaction at once and
    # whatever `react_s` said. So a message that carries reactions holds for the
    # flurry first and then for the gap, and the breath before the next message is
    # the same length whether anybody reacted or not.
    flurry = (max(0.0, float(cfg.react_s))
              if cfg.reactions and not ctx.chat_skin.votes else 0.0)
    # How long a message that is ONLY a picture holds, on top of the gap: there is
    # nothing to read and the picture still has to be looked at — and a sticker that
    # loops wants to be seen doing it at least once.
    look = max(0.0, float(getattr(cfg, "media_s", 0.0)))
    root = media_mod.root(ctx.g.paths.assets)
    scenes: list[Scene] = []
    # Across the conversations in order, because the video is all of them one after
    # another: the timeline is the video's and the seams are drawn by the renderer
    # (see `chat.scroll`), not carved into the clock — except where a native ad sits
    # between two of them, which is a stretch of the clock of its own (`_ad_scene`).
    last = len(job.conversations) - 1
    for msg, ci, end in ((m, ci, k == len(c.messages) - 1)
                         for ci, c in enumerate(job.conversations)
                         for k, m in enumerate(c.messages)):
        spec = voice_for(msg, ctx)
        extra = flurry if msg.reactions else 0.0
        scene = Scene(text=msg.text)
        heard = _heard(msg, root)
        if heard is not None:
            # A voice message, or a round video: its OWN sound is the line. Nobody
            # synthesizes it — the scene is silent to the voicing stage and carries
            # its audio already, which the assembler mixes like any voiced take — and
            # it is as long as the recording, then the breath after it like any line.
            scene.text = ""
            scene.silent = True
            scene.audio, scene.duration = heard
            msg.scene = len(scenes)
            scenes.append(scene)
            if gap + extra > 0:
                scenes.append(Scene(text="", hush=True, duration=gap + extra))
            _ad_after(job, ctx, scenes, ci, end, last)
            continue
        if msg.media is not None and not msg.text.strip():
            # a picture with nothing written under it: nobody reads it, and it holds
            # for the gap and for the look
            scene.silent = True
            scene.duration = gap + look + extra
            msg.scene = len(scenes)
            scenes.append(scene)
            _ad_after(job, ctx, scenes, ci, end, last)
            continue
        if spec:
            scene.voice = spec
            # cast by the run rather than by a person pointing at this line, so a
            # later pass may recast it — the same promise `voice_auto` makes
            # everywhere else (see `llm/delivery.py`). Unless somebody DID point at
            # this line: a voice chosen on the message is theirs to keep.
            scene.voice_auto = not (msg.voice or "").strip()
        else:
            # Nobody reads it, so its length is the only thing that says how long it
            # is up — and that is the gap, which is what the operator set the gap FOR.
            scene.silent = True
            scene.duration = gap + extra
        msg.scene = len(scenes)
        scenes.append(scene)
        # The breath after a line that was actually spoken. An unread message has
        # already held for the gap above, and giving it a second one would make the
        # silent half of a mixed conversation run at half the pace of the spoken half.
        if spec and gap + extra > 0:
            scenes.append(Scene(text="", hush=True, duration=gap + extra))
        _ad_after(job, ctx, scenes, ci, end, last)
    if carry:
        _carry_takes(was, scenes)
    job.scenes = scenes


def _ad_after(job: VideoJob, ctx: AppContext, scenes: list, ci: int, end: bool,
              last: int) -> None:
    """Close conversation `ci` with the native ad, when it asked for one and is not
    the last: the ad goes BETWEEN chats."""
    if end and ci < last and job.conversations[ci].ad_after:
        ad = ad_scene(job, ctx, ci)
        if ad is not None:
            scenes.append(ad)


def ad_clip(job: VideoJob, ctx: AppContext, ci: int) -> Path | None:
    """Which of the contract's native clips plays after conversation `ci` — seeded on
    the run and the place, so the preview and the cut pick the same one, and two ads
    in one video need not be the same clip twice."""
    import random

    if not ctx.native_ad_on:
        return None
    root = ctx.ad.native.assets_dir
    clips = sorted(p for p in root.iterdir()
                   if p.suffix.lower() in media_mod.VIDEO_EXTS) if root.is_dir() else []
    if not clips:
        log.warning("chat: the native ad has no clips in %s — skipped", root)
        return None
    return random.Random(f"ad|{job.workdir}|{ci}").choice(clips)


def ad_scene(job: VideoJob, ctx: AppContext, ci: int) -> Scene | None:
    """The native ad as a stretch of the clock: as long as its clip, heard with the
    clip's own sound, read by nobody. The picture is the render stage's (`chat_render`
    lays the clip between the two chats and swipes in and out of it)."""
    clip = ad_clip(job, ctx, ci)
    if clip is None:
        return None
    _w, _h, dur, _moves = media_mod.facts(clip)
    scene = Scene(text="", is_ad=True, silent=True, clip=clip, duration=max(dur, 0.5))
    heard = media_mod.audio_of(clip)
    if heard is not None:
        scene.audio, scene.duration = heard[0], max(heard[1], 0.5)
    return scene


def _carry_takes(was: list[Scene], now: list[Scene]) -> None:
    """Move each take already voiced to the index its line has now.

    A take is matched by what it says and who says it, which is what the voicing
    stage's cache is keyed on, so a moved take is found there and not paid for again.
    Every file of it moves — the take, its timings, its raw and dry derivations, all
    `scene_NN.*` — and in two steps, out of the way first and into place second,
    because two lines that swapped places are each other's source and target."""
    import shutil

    takes: dict[tuple[str, str], Path] = {}
    for s in was:
        if s.audio and not s.hush and Path(s.audio).is_file():
            takes.setdefault((s.text, s.voice), Path(s.audio))
    moves: list[tuple[int, Path]] = []
    for k, s in enumerate(now):
        if s.hush or s.silent or not s.voice:
            continue
        src = takes.get((s.text, s.voice))
        if src is not None and src.stem != f"scene_{k:02d}":
            moves.append((k, src))
    if not moves:
        return
    hold = moves[0][1].parent / ".carry"
    shutil.rmtree(hold, ignore_errors=True)
    staged: list[tuple[int, Path, list[Path]]] = []
    for k, src in moves:
        box = hold / str(k)
        box.mkdir(parents=True, exist_ok=True)
        files = [f for f in src.parent.glob(f"{src.stem}.*") if f.is_file()]
        for f in files:
            shutil.copy2(f, box / f.name)
        staged.append((k, src, files))
    for k, src, files in staged:
        for f in files:
            # `scene_03.raw.mp3` -> `scene_07.raw.mp3`: the stem changes, the rest stays
            rest = f.name[len(src.stem):]
            shutil.move(str(hold / str(k) / f.name), str(src.parent / f"scene_{k:02d}{rest}"))
    shutil.rmtree(hold, ignore_errors=True)
    log.info("chat: %d voiced take(s) followed their lines to new places", len(moves))


def revoice(job: VideoJob, ctx: AppContext, msg: ChatMsg, spec: str, synth=None) -> None:
    """Say one laid message again with `spec`, and keep the clock true around it.

    The take's words come back on the LINE's clock (`tts.resynth_one`); a chat keeps
    them on the whole video's, as an info video does, so they go to where the line
    starts — and every line after it moves by however much longer or shorter the new
    take is, or the subtitles and the reveal of each later message would run against
    the wrong voice. The message's current words are what is said: voicing it again
    is exactly what makes a retyped message and its scene agree. `synth` is the
    synthesis, swappable so the bookkeeping can be checked without a voice."""
    from . import tts as tts_stage

    synth = synth or tts_stage.resynth_one
    idx = msg.scene
    scene = job.scenes[idx]
    start = sum(s.duration for s in job.scenes[:idx])
    old = scene.duration
    scene.text = msg.text
    scene.silent = False
    synth(job, ctx, idx, None, spec)
    scene.words = [w.model_copy(update={"start": w.start + start, "end": w.end + start})
                   for w in scene.words]
    delta = scene.duration - old
    if abs(delta) > 1e-6:
        for later in job.scenes[idx + 1:]:
            later.words = [w.model_copy(update={"start": w.start + delta,
                                                "end": w.end + delta})
                           for w in later.words]


def guess(job: VideoJob, ctx: AppContext) -> list[float]:
    """How long each message will be up, before anything has been voiced to say so.

    The rules of :func:`lay`, with the one number it cannot know yet — how long a
    voice takes over a line — guessed from the line's length at the run's language
    and rate. It is what the room plays a conversation by while it is still being
    typed, so it lives beside the clock it imitates: a change to how `lay` paces a
    message that does not reach here is a preview that lies about the pace."""
    from ..drama import speech_seconds

    cfg = ctx.chat
    gap = max(0.0, float(cfg.gap_s))
    flurry = (max(0.0, float(cfg.react_s))
              if cfg.reactions and not ctx.chat_skin.votes else 0.0)
    look = max(0.0, float(getattr(cfg, "media_s", 0.0)))
    lang, rate = ctx.params.lang, int(getattr(ctx.params, "tts_rate", 0) or 0)
    out: list[float] = []
    for msg in (m for c in job.conversations for m in c.messages):
        tail = gap + (flurry if msg.reactions else 0.0)
        m = msg.media
        if m is not None and m.kind in media_mod.AUDIBLE and m.duration > 0:
            out.append(m.duration + tail)
        elif m is not None and not msg.text.strip():
            out.append(tail + look)
        elif voice_for(msg, ctx):
            out.append(speech_seconds(msg.text, lang, rate) + tail)
        else:
            out.append(tail)
    # never nothing: a zero-length message is one the playhead could not stop on
    return [max(d, 0.1) for d in out]


def _heard(msg: ChatMsg, root):
    """(the sound, how long) of a message whose own recording is what is heard, or
    None. A voice message whose file is gone is not one — it falls back to being a
    silent bubble with a waveform, which is better than a scene of nothing."""
    m = msg.media
    if m is None or m.kind not in media_mod.AUDIBLE:
        return None
    src = media_mod.resolve(root, m.file)
    if src is None:
        return None
    got = media_mod.audio_of(src)
    if got is None:
        return None
    return got


def run(job: VideoJob, ctx: AppContext) -> None:
    if not job.messages:
        raise ValueError(
            "there is no conversation to make a video out of — build one in the chat "
            "room, or point the run at a source that fetches one"
        )
    log.info("chat: %d conversation(s)", len(job.conversations))
    if ctx.chat.translate:
        from ...llm.chat import translate

        translate(ctx, job)
    lay(job, ctx, carry=True)
    spoken = sum(1 for s in job.scenes if not s.unvoiced)
    log.info("chat: %d messages, %d of them read aloud", len(job.messages), spoken)
    ctx.progress("script", len(job.messages), len(job.messages))
