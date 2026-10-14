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
    eight personas to say so would be the settings eating the evening."""
    want = (ctx.params.chat_voice or "").strip()
    if want == VOICE_NONE:
        return ""
    if want:
        return want
    return ctx.persona(msg.persona).voice.strip()


def lay(job: VideoJob, ctx: AppContext) -> None:
    """Rebuild the scene list out of the messages, and point each message at its scene.

    Destructive by design, and safe for exactly the reason the stage can be re-entered
    at all: everything a scene carries that is worth keeping — the text, who says it,
    how fast — is carried by the MESSAGE, which is the document the operator edits.
    What is thrown away is the derived half: durations and audio paths that belong to
    a conversation that has since changed. A take already synthesized is not lost with
    them, because the voicing stage caches on the line and not on its index."""
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
    # (see `chat.scroll`), not carved into the clock.
    for msg in (m for c in job.conversations for m in c.messages):
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
            continue
        if msg.media is not None and not msg.text.strip():
            # a picture with nothing written under it: nobody reads it, and it holds
            # for the gap and for the look
            scene.silent = True
            scene.duration = gap + look + extra
            msg.scene = len(scenes)
            scenes.append(scene)
            continue
        if spec:
            scene.voice = spec
            # cast by the run rather than by a person pointing at this line, so a
            # later pass may recast it — the same promise `voice_auto` makes
            # everywhere else (see `llm/delivery.py`).
            scene.voice_auto = True
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
    job.scenes = scenes


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
    lay(job, ctx)
    spoken = sum(1 for s in job.scenes if not s.unvoiced)
    log.info("chat: %d messages, %d of them read aloud", len(job.messages), spoken)
    ctx.progress("script", len(job.messages), len(job.messages))
