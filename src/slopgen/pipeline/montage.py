"""The montage screen: one fandom video's timeline, cut and cast by hand.

Every other breakpoint shows a LIST — the beats, the shot prompts, the metadata
fields — because a list is what those stages produce. The picture track is not a
list. It is two clocks running past each other on purpose (see :mod:`.framebase`):
the narration, and the stills laid over it, cut where the speaker breathes and
deliberately not where the beats end. Reviewing that as forty rows of
``4.1–8.3s · push_in · рынок`` is reviewing a film by reading its edit decision list,
and the two things an operator actually wants to do to it — *start this shot on that
word*, *put this picture here instead* — cannot be typed into a row at all.

So there is a screen, and this is the model behind it. One document
(:func:`read`) and a small set of operations over a job that is parked. Nothing here
talks to an LLM and nothing here decides anything: every judgement is the operator's,
made by pointing at a word or a picture. What the module owes them in return is that
the clock underneath stays honest — that a re-voiced line moves every cut after it,
that a rewritten line keeps its cuts on the words they were placed on, and that what
the screen shows in seconds is what the render will do in seconds.

**The track is one thing with two halves.** A line's VOICE and a line's TEXT are
edited separately and mean different things. The voice is what is heard and is what
the clock is made of, so re-voicing a line (:func:`voice`, :func:`take_voice`)
changes its length and shifts everything after it. The text is what is READ — the
burned-in subtitle is built from :attr:`Scene.words` — so rewriting it
(:func:`set_text`) re-lays the new words over the span the old ones occupied and
touches neither the audio nor the clock. Both are legitimate and they are not the
same edit: a line said one way and captioned another is a normal thing to want, and
a typo fixed in the caption should not cost a re-synthesis and a re-cut.

**A cut is a word, not a second.** :class:`~.job.FrameShot` has carried its anchor
since it was written (``anchor_scene``/``anchor_word``) precisely so this screen
could exist: the operator points at the word a shot starts on, the previous shot ends
there, and the seconds are derived — and re-derived whenever the clock moves
underneath. :func:`place_cut`, :func:`move_cut` and :func:`drop_cut` are the whole of
it — and the middle one is the same word question asked by dragging the shot instead of
pointing at the word, which is why it snaps: there is nowhere else for a cut to land.

**Beats are added and dropped here too**, which is not true of the beat modes and is
worth saying why. In a drama a beat IS a clip: add one and there is a shot to generate,
a voice to fit to it, and a sync to redo for everything after. Here the picture track
does not know the beats exist — it is cut on words and runs past the narration on
purpose — so adding a line costs exactly one thing: the shot straddling the seam grows
by the new line's length, and the operator cuts it if they do not want that. Every
other shot keeps its card, every anchor keeps its word, and not one line that was
already voiced is re-voiced. Measured on a three-line job: cards identical, anchors on
the same words, audio paths untouched, one duration changed.

DELETION is the half with a real decision in it, and it is written down at
:func:`drop_line`: whatever was anchored inside the removed line has nowhere left to
be, one of them inherits the word the line used to end on, and the rest go with it.
Which one inherits is the whole question — the picture that was up when the line ended,
not the first one to be asked — and getting it wrong cost the picture AFTER the removed
line, silently, on every deletion.

The one thing insertion really does break is the naming. The `tts` stage writes
`tts/scene_NN` by POSITION, so a line pushed from 2 to 3 leaves its take under the name
the new line is about to be voiced into — and the new take would land on top of the old
one. :func:`_settle_takes` restores the invariant by moving the files, which also keeps
the stage's own cache aligned: a take that keeps its index-derived name is a take a
later re-run of `tts` reuses instead of paying for again.
"""

from __future__ import annotations

import logging
import random
import re
import shutil
from pathlib import Path

from ..config.loader import file_sha, frames_dir, write_frame_card
from ..config.models import FrameCard, MoveKey, Rect
from ..media import filters as fxmod
from ..media.ffmpeg import duration_of
from . import effects as effects_mod
from . import framebase
from .context import AppContext
from .job import FrameShot, Scene, VideoJob, Word

log = logging.getLogger(__name__)

# Two cuts closer together than this are the same cut. Word starts are floats that
# have been through a stretch factor, so "the same moment" is never an equality.
SAME_CUT_S = 0.02

MOVE_KINDS: list[str] = ["hold", "push_in", "drift", "zoom_in", "zoom_out", "pan"]


def available(params, job: VideoJob | None) -> bool:
    """Whether this video has a montage room to go back into.

    It used to ask whether there was anything on the track yet, which was the right
    question while the room was only ever reached from a breakpoint and the wrong one
    the moment a video could be STARTED here: a run made by hand is empty by
    definition, so the button that reopens it vanished the first time you pressed
    «назад» and there was no way back in at all.

    So the question is about the RUN, not about how far it has got. A fandom video
    whose picture comes out of the frame base belongs in this room from the moment it
    exists — and it does not stop belonging when it is cut: a finished video is
    reopened by taking the render off it (:func:`reopen`), because the mistakes worth
    fixing are the ones you can only see in the finished thing. One whose picture comes
    from a generator never belongs here: there is no second clock to edit, and the track
    lane would be a row of words that cannot be cut on."""
    from ..media.generate import is_frame_model

    if params.mode != "fandom" or job is None:
        return False
    if job.frame_shots:
        return True  # it has a track, however it got one
    orch = params.manual_orchestration
    stages = list(orch.stages) if orch and orch.stages else []
    return bool(stages) and all(is_frame_model(st.model or "") for st in stages)


# Which stages this module can tell are ALREADY DONE by looking at the job, and how.
#
# The completed list means "this stage's output is on the job", and until now only the
# stage itself could put a name on it. That is a hole the moment the operator does a
# stage's work by hand: a script typed in here left `script` unfinished, so resuming
# the run walked into the writer and threw every typed line away. Measured — five
# stages pressed by hand, and `resume` announced `canon, script, entities, metadata`.
#
# `entities` is deliberately absent: an empty registry is a perfectly ordinary outcome,
# so "the job has none" cannot be read as "it has not run".
SATISFIED = {
    "canon": lambda job: bool(job.canon.strip()),
    "script": lambda job: bool(job.scenes),
    "tts": lambda job: bool(job.scenes) and all(
        s.audio and s.words for s in job.scenes if not s.is_ad),
    "picture": lambda job: bool(job.frame_shots),
    "footage": lambda job: any(s.bg_assets for s in job.scenes),
    "subtitles": lambda job: any(p.ass for p in job.parts),
    "assemble": lambda job: any(p.file for p in job.parts),
    "metadata": lambda job: any(p.metadata for p in job.parts),
}


def completed(job: VideoJob, done: list[str]) -> list[str]:
    """The completed list, re-read off the job for every stage that can be.

    Both directions, which is what makes it self-healing rather than another thing to
    keep in step. A stage whose output is on the job counts as done however it got
    there — typed, dropped in, or run — so a resume walks past it instead of writing
    over the operator's evening. And a stage whose output is no longer there stops
    counting, so adding an unvoiced line puts `tts` back on the table, where it will
    re-synthesize that one line and reuse the cached takes for the rest."""
    out = set(done)
    for name, test in SATISFIED.items():
        if test(job):
            out.add(name)
        else:
            out.discard(name)
    order = list(SATISFIED) + ["entities", "publish"]
    return sorted(out, key=lambda n: order.index(n) if n in order else 99)


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------


def read(job: VideoJob, params) -> dict:
    """The whole timeline, on one absolute clock, as the screen reads it.

    Word positions are the stretched, offset ones :func:`framebase.timeline` computes
    — the same numbers the cuts were placed against and the same ones
    `stages.subtitles` will burn — so a word drawn at 12.4s in the browser is a word
    spoken at 12.4s in the file. Reading `Scene.words` raw would be half a second out
    on any line whose voice was retimed, which is exactly the kind of wrongness a
    montage screen exists to make impossible."""
    cues, regions, total = framebase.timeline(job.scenes)
    by_scene: dict[int, list[dict]] = {}
    for c in cues:
        by_scene.setdefault(c.scene, []).append({"i": c.word, "at": c.at, "pause": c.pause})

    scenes: list[dict] = []
    at = 0.0
    for i, scene in enumerate(job.scenes):
        marks = by_scene.get(i, [])
        scenes.append({
            "i": i,
            "text": scene.text,
            "start": at,
            "duration": scene.duration,
            "is_ad": scene.is_ad,
            "voiced": bool(scene.audio) and bool(scene.words),
            # the speed THIS line was voiced at, when it is not the run's (see
            # `Scene.tts_rate`); null means it follows the run
            "rate": scene.tts_rate,
            # …and which recording of the voice said it, when it is not the run's
            # (`Scene.voice`): on a cloning engine that IS the intonation. "" follows
            # the run, which is what every line does until one is pinned.
            "voice": scene.voice,
            # …and whether the WRITER pinned it rather than the operator, which is worth
            # showing because it is the one pin a later re-run may take back on its own
            # (see `Scene.voice_auto` and `llm/delivery.py`)
            "voice_auto": scene.voice_auto,
            # a stretch of silence the operator put there rather than a line (see
            # `Scene.hush`): it has a length and nothing else, and the room draws it as
            # a block you take by the edge instead of a line you write in
            "hush": scene.hush,
            "part": scene.part,
            "words": [
                {"t": w.text, "start": m["at"],
                 "end": m["at"] + max(w.end - w.start, 0.0) * _factor(scene),
                 "pause": m["pause"]}
                for w, m in zip(scene.words, marks)
            ],
        })
        at += scene.duration

    shots = [_shot_json(i, s) for i, s in enumerate(_ordered(job))]
    return {
        "total": total,
        "regions": [{"start": r.start, "end": r.end} for r in regions],
        "scenes": scenes,
        "shots": shots,
        "filters": fxmod.normalise(dict(params.filters or {})),
        "sensitivity": params.cut_sensitivity,
        # the RUN's speech rate, which is what a line that has never been pinned is
        # voiced at — so the per-line slider starts where the line actually is
        "rate": params.tts_rate,
        "by_hand": bool(params.frame_by_hand),
        "moves": MOVE_KINDS,
        # what still stands between this track and a finished video
        "blocking": blocking(job),
        # …and how many moves go nowhere, which is a different kind of wrong: the
        # video renders, it is just motionless where it should not be
        "frozen": sum(1 for s in job.frame_shots if frozen(s)),
    }


def _factor(scene: Scene) -> float:
    """How much this line's voice was stretched to meet its picture. 1.0 in a pure
    stills run, and not assumed to be: a line re-voiced at another speed changes it."""
    return (scene.duration / scene.audio_src_duration) if scene.audio_src_duration else 1.0


def _shot_json(i: int, s: FrameShot) -> dict:
    return {
        "i": i,
        "start": s.start,
        "duration": s.duration,
        "scene": s.anchor_scene,
        "word": s.anchor_word,
        "card": s.card,
        "pinned": s.pinned,
        "target": s.target,
        "fit": s.fit,
        "said": s.said,
        "referents": list(s.referents),
        "ask": s.ask_id,
        "move": {"kind": s.move.kind,
                 "a": s.move.rect_a.model_dump(), "b": s.move.rect_b.model_dump(),
                 "from": s.move.move_start, "to": s.move.move_end,
                 # the keys as the editor placed them, and the same move as the
                 # MOMENTS it passes through — the second is what the preview draws,
                 # so the two-key form and a run of keys are one thing to it and the
                 # fallback lives in one place (`KenBurns.points`)
                 "keys": [{"at": k.at, "of": k.of, "scale": k.rect.scale,
                           "cx": k.rect.cx, "cy": k.rect.cy} for k in s.move.keys],
                 "points": [{"at": at, "cx": r.cx, "cy": r.cy, "scale": r.scale}
                            for at, r in s.move.points()]} if s.move else None,
    }


# What each stage needs in front of it before pressing it means anything. Not an
# ORDER — the chain is an order and this screen deliberately is not one: the work
# really goes write, voice, look, re-write that line, voice it again, cut, cast, cut
# again. What a stage does need is its input, and a button that would do nothing is
# better greyed than pressed. The names are the chain's (`orchestrator.stages_for`).
def _voiced(job: VideoJob) -> bool:
    return any(s.words for s in job.scenes if not s.is_ad)


READY = {
    "canon": lambda job: True,          # the world, compiled; it needs no video at all
    "script": lambda job: True,         # writes the beats, over whatever is there
    "entities": lambda job: bool(job.scenes),
    "tts": lambda job: bool(job.scenes),
    "picture": _voiced,                 # cuts on words, so it needs words
    "footage": _voiced,
    "subtitles": _voiced,
    "assemble": lambda job: any(s.bg_assets for s in job.scenes),
    "metadata": lambda job: bool(job.scenes),
}


def _spends_llm(name: str, job: VideoJob, params) -> bool:
    """Whether pressing this stage costs model calls, on THIS run.

    Three of them answer differently depending on the run, and those three are the ones
    an operator most wants warned about: the picture track asks a matcher unless the
    operator is casting by hand, the subtitles only call anything when the run is
    cleaning profanity out of them, and the footage stage asks nothing at all where the
    picture comes out of the frame base."""
    if name == "picture":
        return not params.frame_by_hand
    if name == "subtitles":
        return bool(params.clean_subtitles)
    if name == "footage":
        return not framebase.active(job, None)
    return name in ("canon", "script", "entities", "metadata")


def _redo(name: str, job: VideoJob) -> list[dict]:
    """What this stage will write OVER, as facts rather than sentences.

    The half of a confirmation that actually decides it. `script` is the case this
    exists for: pressed on a video whose lines were typed by hand, it replaces every
    one of them, and a row of nine identical chips says nothing about that at all."""
    beats = [s for s in job.scenes if not s.is_ad]
    if name == "canon" and job.canon.strip():
        return [{"what": "canon"}]
    if name == "script" and job.scenes:
        return [{"what": "lines", "n": len(job.scenes)}]
    if name == "entities" and job.entities:
        return [{"what": "entities", "n": len(job.entities)}]
    if name == "tts":
        n = sum(1 for s in beats if s.audio and s.words)
        return [{"what": "takes", "n": n}] if n else []
    if name == "picture":
        n = sum(1 for s in job.frame_shots if s.card and not s.pinned)
        pinned = sum(1 for s in job.frame_shots if s.pinned)
        out = [{"what": "cards", "n": n}] if n else []
        if pinned:
            out.append({"what": "kept", "n": pinned})
        return out
    if name == "footage":
        n = sum(1 for s in job.scenes if s.bg_assets)
        return [{"what": "footage", "n": n}] if n else []
    if name == "subtitles":
        n = sum(1 for p in job.parts if p.ass)
        return [{"what": "subs", "n": n}] if n else []
    if name == "assemble":
        n = sum(1 for p in job.parts if p.file)
        return [{"what": "cut", "n": n}] if n else []
    if name == "metadata":
        n = sum(1 for p in job.parts if p.metadata)
        return [{"what": "meta", "n": n}] if n else []
    return []


def stages(job: VideoJob, params, done: list[str]) -> list[dict]:
    """The pipeline as a row of buttons: what may be pressed, what has been, and — for
    the confirmation — what pressing one would spend and what it would write over.

    Nine identical chips is what the first version was, and it told the operator
    nothing: which of them calls a model, which of them replaces the lines they just
    typed, which of them is free. All three are facts about the stage AND this job, so
    they are answered here rather than guessed at in the browser.

    `done` is the checkpoint's completed list, which is the same thing a resume reads —
    so a stage the operator did by hand counts as run, and a chain resumed afterwards
    walks past it exactly as it would past one the chain had run itself."""
    from .orchestrator import stages_for

    out = []
    for name, _fn in stages_for(params):
        ready = READY.get(name, lambda job: True)
        out.append({"name": name, "done": name in done, "ready": bool(ready(job)),
                    "llm": _spends_llm(name, job, params), "redo": _redo(name, job)})
    return out


def blocking(job: VideoJob) -> list[dict]:
    """Everything that would make this track fail to render, as facts rather than
    sentences: `{"what": id, "n": count}`, translated by whoever shows them.

    It is the montage's answer to "may I press go on". The pipeline would find these
    itself, later and worse — an unvoiced line is a zero-length segment ffmpeg
    declines, and a shot with no picture is a stretch of video that silently belongs
    to its neighbour — so they are counted here, while the operator is looking at the
    thing that has them."""
    out: list[dict] = []
    # A pause is not an unvoiced line: it has a length, it renders as the silence it is,
    # and nothing about it is waiting to be done (see `Scene.hush`).
    beats = [s for s in job.scenes if not s.is_ad and not s.hush]
    silent = sum(1 for s in beats if not (s.audio and s.words))
    if silent:
        out.append({"what": "unvoiced", "n": silent})
    empty = sum(1 for s in job.frame_shots if not s.card)
    if empty:
        out.append({"what": "uncovered", "n": empty})
    return out


def reopen(job: VideoJob) -> int:
    """Take the RENDER off an already-cut video, so it can be edited and cut again.

    A finished video used to be the one thing this room could not touch. The room was
    written as the last stop before the render — get in at the `picture` breakpoint,
    cut, cast, press «собрать и продолжить» — and once the chain had walked past it
    there was no way back in at all. But the mistakes you actually want to fix are the
    ones you can only see in the finished thing: a card that is wrong for what is said
    over it, a cut a beat late, a line the voice mangled. The whole point of the room
    is to overrule the matcher, and it may as well be overruled after watching it as
    before.

    What comes off is the render and nothing else. Three things describe the timeline
    as it WAS rather than as it is, and all three are made again from what survives:
    each scene's background pieces (the picture track compiled onto the scenes, cut
    where they end — `framebase.apply_to_scenes`), each part's subtitle file (the words
    burned in at the timings of the moment) and each part's cut. Everything the video
    is made of — the lines, the takes, the cards, the moves, the effects, the look — is
    exactly what the operator came back to change, and is left where it is.

    The pieces are the half that is easy to forget and the half that matters most,
    because they are what `assemble` actually renders: leave them on and a re-cut track
    changes nothing at all, since nothing downstream ever looks at the shots again. Only
    where the picture comes out of the frame base, mind — there the pieces cost one
    walk of the track to rebuild and the stage that does it asks no model anything. A
    run whose shots were fetched or generated keeps them: re-laying those means fetching
    and generating them again, which is not what taking a render off should mean.

    Nothing is deleted from disk. `assemble` writes each part to the same path it used
    before (`final.mp4`, `part_03.mp4`), so the old cut is overwritten by the new one
    when it is made — and until then it is still there to be watched, which is most of
    why anybody reopens a finished video: to see what they are about to replace.

    The completed list is not touched here, because it does not have to be. `completed`
    re-reads every stage it can off the job, so a part with no subtitles and no cut puts
    `subtitles` and `assemble` back on the table by itself — both in this room's rail
    and for a resume, which is what then re-renders it.

    The title and the upload are deliberately left alone. A re-cut is a new FILE, not a
    new publication: rewriting the metadata would rename something the world has
    already seen, and `publish` staying done is what keeps a resume from sending the
    same episode out twice. Publishing the new cut is a decision, and it is made where
    every other publishing decision is made — the run's settings — rather than
    implicitly by a re-edit.

    Returns how many parts were un-rendered, which is how the caller says what it did.
    """
    if framebase.active(job, None):
        for scene in job.scenes:
            scene.bg_assets = []
    n = 0
    for part in job.parts:
        if part.ass is None and part.file is None:
            continue
        part.ass = None
        part.file = None
        n += 1
    return n


# --------------------------------------------------------------------------
# the voice track, and the text over it
# --------------------------------------------------------------------------


def set_text(job: VideoJob, index: int, text: str) -> None:
    """Rewrite what a line SAYS without touching what it sounds like.

    The new words are laid over the span the old ones occupied, in proportion to
    their length — `stages.subtitles._retime` has done exactly this for the profanity
    rewrite since it was written, and this is the same operation asked for by hand.
    The voice is untouched, so the clock does not move and not one cut has to be
    re-placed; only the captions change.

    Re-voicing afterwards is a separate button and a separate decision (:func:`voice`),
    which is the point: these are two edits, not one edit with a side effect."""
    from .stages.subtitles import respread

    scene = job.scenes[index]
    text = text.strip()
    if text == scene.text.strip():
        return
    before = _anchor_fractions(job, index)
    scene.text = text
    scene.words = respread(list(scene.words), text)
    _rebind(job, index, before)


def anchored(job: VideoJob) -> list:
    """Everything on this video that is fastened to a WORD, in one list.

    Two tracks hang off the narration now — where the picture changes, and where an
    effect goes off — and every structural edit to the lines has to move both of them
    the same way. Written twice, they drift apart at exactly the edit nobody tests: a
    line inserted in the middle shifts the cuts and leaves the arrows pointing into
    the line before. So the edits below walk this instead, and what they need of a
    member is only that it carries `anchor_scene` and `anchor_word`.

    Order matters and is stable: shots first, then cues, both in the order the job
    holds them — :func:`_anchor_fractions` keys by position in this list and
    :func:`_rebind` reads those keys back."""
    return list(job.frame_shots) + list(job.effect_cues)


def _anchor_fractions(job: VideoJob, index: int) -> dict[int, float]:
    """Where in a line each anchor currently sits, as 0..1 of the line.

    Word INDICES do not survive a rewrite — six words become seven and every anchor
    after the third points at the wrong one — while the place in the line does: a cut
    a third of the way through a sentence is still a third of the way through the
    sentence it was rewritten into. So the anchors are taken down as fractions before
    the words move and put back on the nearest word after (see :func:`_rebind`)."""
    scene = job.scenes[index]
    n = max(len(scene.words) - 1, 1)
    return {i: (a.anchor_word / n)
            for i, a in enumerate(anchored(job))
            if a.anchor_scene == index and a.anchor_word >= 0}


def _rebind(job: VideoJob, index: int, fractions: dict[int, float]) -> None:
    """Put everything anchored in one line back on a word, then re-measure the clock."""
    scene = job.scenes[index]
    n = max(len(scene.words) - 1, 1)
    all_of_them = anchored(job)
    for i, frac in fractions.items():
        if i < len(all_of_them):
            all_of_them[i].anchor_word = min(round(frac * n), max(len(scene.words) - 1, 0))
    # a cue also carries the word it fires on, for the screen to show back; after a
    # rewrite that word is a different word
    for q in job.effect_cues:
        if q.anchor_scene == index and 0 <= q.anchor_word < len(scene.words):
            q.word = scene.words[q.anchor_word].text
    retime(job)


def voice(job: VideoJob, ctx: AppContext, index: int, rate: int | None = None,
          with_voice: str | None = None) -> float:
    """Say this line again, now, at `rate` percent (None = whatever it already uses).

    `with_voice` says WHO says it — a voice spec, which on a cloning engine is the same
    question as HOW: `марта:зло` is another recording of the same person, and the model
    copies the delivery it was shown (see `config.models.VoiceConfig`). `""` puts the
    line back on the run's voice, None leaves it where it is.

    The clock moves under everything after it, which is why `retime` follows: the cuts
    themselves are not re-decided — they were placed on words and those words are
    still the same words — they are re-measured (see `framebase.reanchor`)."""
    from .stages import tts as tts_stage

    if job.scenes[index].hush:
        raise ValueError("this is a pause — there is nothing in it to say")
    if not job.scenes[index].text.strip():
        raise ValueError("there is nothing written on this line to say")
    before = _anchor_fractions(job, index)
    seconds = tts_stage.resynth_one(job, ctx, index, rate=rate, voice=with_voice)
    job.scenes[index].duration = seconds
    _rebind(job, index, before)
    return seconds


def take_voice(job: VideoJob, ctx: AppContext, index: int, src: Path) -> float:
    """Take a recording of this line instead of synthesizing it.

    The same road `--tts-source manual` takes for a whole video, opened for one line:
    a microphone emits no word boundaries, so the timings are recovered from the audio
    against the text we already have (see :mod:`slopgen.tts.align`). Which means a
    recording of something OTHER than the line will align badly and look it — that is
    the aligner working, not failing."""
    from ..tts import align as aligner
    from .stages import tts as tts_stage

    scene = job.scenes[index]
    if scene.hush:
        raise ValueError("this is a pause — there is nothing in it to say")
    align_dir = tts_stage._require_aligner(ctx)
    dest = tts_stage.audio_path(job, index, Path(src).suffix.lower() or ".wav")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if Path(src) != dest:
        shutil.copy2(src, dest)
    spoken = tts_stage._spoken(scene.text, tts_stage._pronounce(ctx))
    seconds = duration_of(dest)
    raw = tts_stage._as_written(
        aligner.align(dest, spoken, align_dir, seconds), tts_stage._pronounce(ctx))
    before = _anchor_fractions(job, index)
    scene.audio = dest
    scene.audio_src_duration = seconds
    scene.duration = seconds
    scene.audio_tempo = 1.0
    scene.video_tempo = 1.0
    scene.words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in raw]
    _rebind(job, index, before)
    return seconds


# What the `tts` stage calls a take: `tts/scene_07.mp3` under the run's own folder.
# Anything else a scene's audio may point at — a recording collected by `manual_tts`,
# a file the operator handed over from somewhere — is left where it is, because the
# name it carries was never derived from its position and nothing will write over it.
_TAKE_RE = re.compile(r"scene_\d+$", re.IGNORECASE)


def _is_take(job: VideoJob, path: Path | None) -> bool:
    return bool(path) and Path(path).parent == Path(job.workdir) / "tts" \
        and bool(_TAKE_RE.fullmatch(Path(path).stem))


def _move_take(src: Path, dst: Path) -> None:
    """Move one take and the word timings cached beside it (see `tts._cache_path`)."""
    for a, b in ((src, dst), (src.with_suffix(".json"), dst.with_suffix(".json"))):
        if a.is_file():
            a.replace(b)


def _settle_takes(job: VideoJob) -> None:
    """Put every take back under the name its scene's POSITION gives it.

    The `tts` stage names by index and so does everything that looks a take up again,
    so a list that has had a line put into it or taken out of it is a list where the
    names lie — and the next thing voiced by hand writes over somebody else's audio.

    Through temporary names, in two passes, because the shift goes both ways: an
    insertion moves every take up one and a deletion moves them down one, and either,
    done in the wrong order in place, overwrites the file it is about to move."""
    moves = []
    for i, scene in enumerate(job.scenes):
        src = Path(scene.audio) if scene.audio else None
        if not _is_take(job, src):
            continue
        dst = src.with_name(f"scene_{i:02d}{src.suffix}")
        if dst != src:
            moves.append((scene, src, dst))
    for k, (scene, src, _dst) in enumerate(moves):
        tmp = src.with_name(f".moving_{k}{src.suffix}")
        _move_take(src, tmp)
        scene.audio = tmp
    for scene, _src, dst in moves:
        _move_take(Path(scene.audio), dst)
        scene.audio = dst


def default_slot(ctx: AppContext):
    """The generator slot a line gets when there is no neighbour to take one from.

    The `script` stage stamps every beat it writes with a slot out of the run's chain
    (`stages.beats._assign_slots`), and everything downstream reads it — most sharply
    `framebase.active`, which asks it of EVERY beat and answers no if one of them is
    blank. A video written by hand has a first line with nothing in front of it, so
    without this the very first thing typed in the montage room takes the whole run out
    of frame-base mode: the picture track plans nothing, and the footage stage goes
    looking for stock photographs of the boilerplate. Found exactly that way."""
    from .drama import plan_slots

    return plan_slots(ctx.orchestration, ctx.params.duration_s,
                      ctx.params.clip_seconds)[0]


def add_line(job: VideoJob, after: int, text: str = "", slot=None) -> int:
    """Put a new beat into the video after `after` (-1 puts it at the very front).

    It arrives silent, which is the honest state: a line has no length until somebody
    has said it, so it takes up no time on the clock and nothing moves until it is
    voiced (:func:`voice`). Until then it is one of the things :func:`blocking` counts,
    so the screen says plainly that it is not finished rather than letting the run walk
    into a zero-length segment.

    It inherits the neighbour's slot, and `slot` is what it takes instead when there is
    no neighbour — the first line of a video written from nothing. That matters more
    than it looks: a beat with no `gen_model` is a beat `framebase.active` does not
    recognise as coming from the frame base, and one of those is enough to take the
    whole picture track out of the mode (see :func:`default_slot`)."""
    from . import review

    at = min(max(after + 1, 0), len(job.scenes))
    prev = job.scenes[at - 1] if at else (job.scenes[0] if job.scenes else None)
    scene = review.blank_scene(prev)
    scene.text = text.strip()
    if not scene.gen_model and slot is not None:
        scene.gen_model, scene.key_mode, scene.key = slot.model, slot.key_mode, slot.key
        scene.clip_target_s = scene.clip_target_s or slot.clip_seconds
    for a in anchored(job):
        if a.anchor_scene >= at:
            a.anchor_scene += 1
    job.scenes.insert(at, scene)
    _settle_takes(job)
    retime(job)
    return at


# How long a pause is when nobody has said, and the narrowest one there is. The floor is
# not taste, it is hit area: a pause is dragged by its own right edge, and a block a
# hair wide has no edge to take hold of. The ceiling is taste, and generous — a silence
# longer than this is a different video.
HUSH_S = 0.6
HUSH_FLOOR_S = 0.15
HUSH_CEIL_S = 20.0


def add_hush(job: VideoJob, after: int, seconds: float = HUSH_S) -> int:
    """Put a stretch of SILENCE into the video after `after` (-1 = at the very front).

    Unlike a line, it arrives with a length: the whole of it is a length, so a pause
    with none would be a thing on the timeline that is not on the timeline. Everything
    after it moves back by that much, and the cuts move with the words the way they do
    for any other change to the clock (:func:`retime`).

    **Two in a row is refused.** Two pauses touching are one pause said twice — there is
    no edit you can make to either that you could not make to one of twice the length —
    and a track carrying them is a track where dragging the first one longer looks like
    it did nothing, because the number the operator is reading is the other one. So the
    second is not made, and the caller says so.

    It is not counted among the things :func:`blocking` holds the render for, because
    nothing about it is unfinished: silence is what it is for."""
    from . import review

    # A video of nothing but silence is not a video, and it is also the one case where a
    # pause has no neighbour to take a generator slot from — which is what decides
    # whether the picture track is the frame base at all (see :func:`add_line`).
    if not job.scenes:
        raise ValueError("there is nothing here yet to put a pause in — write a line first")
    at = min(max(after + 1, 0), len(job.scenes))
    if (at and job.scenes[at - 1].hush) or (at < len(job.scenes) and job.scenes[at].hush):
        raise ValueError("there is already a pause here — drag that one longer instead")
    prev = job.scenes[at - 1] if at else (job.scenes[0] if job.scenes else None)
    # the neighbour's slot, exactly as a new line takes it: a beat with no `gen_model` is
    # one `framebase.active` does not recognise, and one of those takes the whole picture
    # track out of the frame base (see `review.blank_scene` and :func:`add_line`)
    scene = review.blank_scene(prev)
    scene.hush = True
    scene.duration = min(max(float(seconds), HUSH_FLOOR_S), HUSH_CEIL_S)
    for a in anchored(job):
        if a.anchor_scene >= at:
            a.anchor_scene += 1
    job.scenes.insert(at, scene)
    _settle_takes(job)
    retime(job)
    return at


def move_line(job: VideoJob, index: int, to: int) -> int:
    """Move one line (or one pause) to another place in the order. Returns where it sits.

    This used to be the one structural edit the room refused, on the grounds that a
    reordered script is a track to be cast from scratch. That was the wrong reading of
    its own design. A cut is a WORD and so is an effect (:func:`anchored`), and the
    words travel with the line they are in — so the anchors are re-numbered through the
    move and every cut comes out still on the syllable it was placed on, still carrying
    the card somebody chose for those words, still with the arrow pointing at the thing
    it was pointing at. Nothing has to be cast again.

    What DOES change is how long each picture stays up, and it can change a great deal.
    A shot runs to the next cut (`framebase.reanchor`), and moving a line moves cuts
    past each other: a shot whose neighbour has just travelled to the far end of the
    video grows to meet whatever is now in front of it. That is honest arithmetic and
    not damage — the seconds have to belong to somebody — but it is the thing to look at
    afterwards, and it is why the room says so rather than pretending a reorder is free.

    Two pauses that meet across the gap the line left are joined (:func:`_fuse_hush`),
    and the takes are renamed to their new positions (:func:`_settle_takes`), which is
    the half that would otherwise corrupt audio: the `tts` stage names a take by its
    line's INDEX, so a permutation leaves every one of those names pointing at somebody
    else's voice."""
    n = len(job.scenes)
    if not 0 <= index < n:
        raise ValueError("there is no such line")
    to = min(max(int(to), 0), n - 1)
    if to == index:
        return index
    moved = job.scenes[index]
    order = list(range(n))
    order.insert(to, order.pop(index))
    # old position -> new position, which is what every anchor is re-numbered through
    where = {old: new for new, old in enumerate(order)}
    job.scenes = [job.scenes[old] for old in order]
    for a in anchored(job):
        if a.anchor_scene in where:
            a.anchor_scene = where[a.anchor_scene]
    _fuse_hush(job)
    _settle_takes(job)
    retime(job)
    # by identity, and not by `to`: a pause dropped beside another pause is joined into
    # it, so the thing that was dragged may no longer be on the list at all — and what
    # the operator should then be looking at is the silence it became part of
    at = next((i for i, s in enumerate(job.scenes) if s is moved), -1)
    return at if at >= 0 else min(max(to - 1, 0), len(job.scenes) - 1)


def _fuse_hush(job: VideoJob) -> int:
    """Two pauses that have ended up touching are one pause. Join them; say how many went.

    :func:`add_hush` refuses to MAKE the second, and that is not enough on its own: drop
    the line between two pauses and they meet without anybody having asked for it. So
    the rule is restored where it can be broken rather than only where it is set, and it
    is restored by ADDING the lengths — the silence the operator is looking at is the
    silence they keep, which is the only join that changes nothing about the video."""
    gone = 0
    i = len(job.scenes) - 1
    while i > 0:
        if job.scenes[i].hush and job.scenes[i - 1].hush:
            job.scenes[i - 1].duration = min(
                job.scenes[i - 1].duration + job.scenes[i].duration, HUSH_CEIL_S)
            del job.scenes[i]
            # a pause holds no words, so nothing was anchored INSIDE the one that went —
            # only the numbering after it moves up
            for a in anchored(job):
                if a.anchor_scene > i:
                    a.anchor_scene -= 1
            gone += 1
        i -= 1
    return gone


def set_hush(job: VideoJob, index: int, seconds: float) -> float:
    """How long one pause runs — the room's drag on its right edge, in seconds.

    The clock moves under everything after it, which is the whole point of the control
    and the reason `retime` follows: the cuts are not re-decided, they are re-measured
    against a video that is now longer or shorter (see `framebase.reanchor`)."""
    if not 0 <= index < len(job.scenes):
        raise ValueError("there is no such line")
    scene = job.scenes[index]
    if not scene.hush:
        raise ValueError("this is a line, not a pause — its length is its voice")
    scene.duration = min(max(float(seconds), HUSH_FLOOR_S), HUSH_CEIL_S)
    retime(job)
    return scene.duration


def drop_line(job: VideoJob, index: int) -> None:
    """Take one line out of the video entirely.

    The cheap structural edit, and the only one offered here: nothing that survives it
    has to be re-made, because a removed line takes only its own seconds with it.

    What is homeless afterwards is whatever was anchored INSIDE it, and there is only
    one place for it to go: the first word of the line that follows (the last word of
    the one before, when the line being removed is the last). One of them may move
    there, and it is the LAST of them — the picture that was up when the removed line
    ended, which is what a viewer would have been looking at at the moment the deletion
    lands on. The ones before it were up only during seconds that no longer exist, so
    they go with the line, and that is the honest outcome.

    Unless a shot already starts at that word. Then the two are competing for one cut
    and the one that was there first wins: its stretch survives the deletion whole,
    while the homeless one's was inside the part being removed.

    It used to be settled by dropping the later of two shots that ended up on the same
    word, and the later one was the shot that had owned that word all along. Deleting a
    line ate the picture AFTER it: four shots went in, three came out, and the survivor
    silently inherited the missing one's seconds. Effects are not thinned out at all,
    for the opposite reason: two of them on one word is an ordinary thing to want."""
    if not 0 <= index < len(job.scenes) or len(job.scenes) <= 1:
        raise ValueError("a video needs at least one line")
    if index + 1 < len(job.scenes):
        landing = (index + 1, 0)
    else:
        before = job.scenes[index - 1]
        landing = (index - 1, max(len(before.words) - 1, 0))
    ordered = _ordered(job)
    taken = {(s.anchor_scene, s.anchor_word) for s in ordered if s.anchor_scene != index}
    homeless = [s for s in ordered if s.anchor_scene == index]
    heir = homeless[-1] if (homeless and landing not in taken) else None
    if heir is not None:
        heir.anchor_scene, heir.anchor_word = landing
    job.frame_shots = [s for s in ordered
                       if s.anchor_scene != index or s is heir]
    for q in job.effect_cues:
        if q.anchor_scene == index:
            q.anchor_scene, q.anchor_word = landing
    # …and everything after the removed line moves up one
    for a in anchored(job):
        if a.anchor_scene > index:
            a.anchor_scene -= 1
    del job.scenes[index]
    # an anchor that ran off the end goes to the last word there is
    last = len(job.scenes) - 1
    for a in anchored(job):
        if a.anchor_scene > last:
            a.anchor_scene, a.anchor_word = last, max(len(job.scenes[last].words) - 1, 0)
    _fuse_hush(job)
    _settle_takes(job)
    # the re-measure collapses whatever this deletion piled onto one moment: it is one
    # of the ways two starts converge, and it is no longer this function's to remember
    # (see `_dedupe`, called from `retime`)
    retime(job)


# --------------------------------------------------------------------------
# where the picture changes
# --------------------------------------------------------------------------


def _ordered(job: VideoJob) -> list[FrameShot]:
    """The track in the order it plays. It is kept sorted on the job, and this is
    what says so — every index the screen sends back is an index into this."""
    job.frame_shots.sort(key=lambda s: s.start)
    return job.frame_shots


def settle(job: VideoJob) -> None:
    """Put the clock back together after a STAGE has written to the job.

    A stage run from the workbench leaves the job in the state the chain would have
    left it in half way through, and half way through is not a state the screen can
    draw. `tts` is the case that matters: in a beat mode it records how long each line
    took to say and deliberately does NOT make that the scene's length, because the
    clip a beat has to fit is not known yet — the `picture` stage is what settles it
    for a stills run. Pressed on its own, that leaves every line voiced and every line
    zero seconds long, which reads as a screen that did nothing.

    So the same rule is applied here: where the picture is the frame base, a still has
    no length of its own and the scene simply spans its narration."""
    from .stages.picture import fix_durations

    if framebase.active(job, None):
        fix_durations(job)
    retime(job)


def retime(job: VideoJob) -> None:
    """Re-measure the whole track against the clock as it stands now.

    Both tracks, because there are two of them: the cuts hang off words, and so do the
    effects (see `pipeline/effects`). A line re-voiced at the top of the video moves
    every cut after it AND every arrow, and an arrow left behind is not a late accent,
    it is one pointing at the wrong word.

    The order below is the whole of it, and it is the order that was wrong. Where the
    shots STAND is the answer to the anchors, so that has to be settled before anything
    may ask a question about the shape of the track — whether a region is missing its
    opening shot, whether two shots have landed on the same moment. Asked first, both
    questions are asked of last pass's seconds: `open_heads` looked at a first shot
    still sitting on the breath before the first word, saw no shot at 0.000, laid one
    there — and then the re-measure moved the other one onto 0.000 as well, because the
    line had been re-voiced without that breath. Two shots over one stretch, and
    nothing downstream could tell: they are drawn exactly on top of each other here and
    the render wrote a piece for each (see `framebase.apply_to_scenes`)."""
    framebase.reanchor(job.scenes, job.frame_shots)
    # …and now the shape, against seconds that are current: one shot per moment, and a
    # shot at the top of every region (`_dedupe`, `open_heads`). Either may change the
    # list, and a shot put back by the second arrives with no length at all, so the
    # measure runs again over whatever they left.
    changed = _dedupe(job) + open_heads(job)
    if changed:
        framebase.reanchor(job.scenes, job.frame_shots)
    effects_mod.reanchor(job)
    # a firing that sits on a picture is inside that picture's shot, and the shot's
    # length is exactly what a cut or a re-voicing changes (see `effects.clip_to_shots`)
    effects_mod.clip_to_shots(job)
    _retell(job)


def _retell(job: VideoJob) -> None:
    """Refresh what each shot is OVER — the narration it hears and who is named in
    it. Both are read off the scenes by span, so both are wrong the moment a cut
    moves, and both are what a later pass of the matcher (or an ask written from a
    shot) would read."""
    for s in _ordered(job):
        said, shown = framebase.said_at(job.scenes, s.start, s.start + s.duration)
        s.said, s.prompt = said, shown
        s.referents = framebase.referents_at(job.scenes, s.start, s.start + s.duration)


def _dedupe(job: VideoJob) -> int:
    """Two shots at the same moment are one shot. Collapse them; say how many went.

    Starts converge for several ordinary reasons: a line dropped and the picture that
    was up re-anchored onto the word after it, a cut placed where a re-measure had
    already put one, and — the one that was missed — a region's opening shot meeting
    the cut on its own first word. Those two are ordinarily distinct, because the
    opening shot sits at 0.000 and the first word is a breath later; re-voice the line
    without that breath and the word lands on 0.000 too.

    Nothing downstream could tell. `framebase.reanchor` gives BOTH of them the seconds
    up to the next start (a twin's start is not GREATER than its twin's, so each reads
    the other as absent), the room draws them exactly on top of each other so the
    operator sees one shot and clicks the one on top, and `framebase.apply_to_scenes`
    writes a background piece per shot — so the render showed that picture twice and
    every cut after it in the scene ran a shot late. Measured on a live run: the first
    line's three pictures came out as four, 1.4s adrift, with the last one chopped from
    2.6s to 1.3s, and the animation the operator had just changed did not appear at all
    because the twin underneath it was still holding the same card.

    Which one survives is decided by what is ON it rather than by where it sits in the
    list. A shot with a picture beats an empty one, because an empty twin is a
    placeholder — an opening shot nobody has cast yet — and letting it win would blank
    a stretch that was covered. Between two that both have one, the LAST wins: the lane
    is drawn in list order, so the later of two coincident blocks is the one on top,
    the one that takes the clicks, and therefore the one carrying whatever was last
    done to that stretch.

    The survivor keeps everything of its own and takes only the earliest start of the
    pile. It does not inherit an anchor: a shot standing at a region's start IS that
    region's opening shot (see :func:`open_heads`), and handing it a word to follow
    would let the next re-voicing walk it off the opening seconds again."""
    kept: list[FrameShot] = []
    gone = 0
    for s in _ordered(job):
        if not kept or s.start - kept[-1].start >= SAME_CUT_S:
            kept.append(s)
            continue
        prev = kept[-1]
        win = s if (s.card or not prev.card) else prev
        win.start = min(prev.start, s.start)
        kept[-1] = win
        gone += 1
    job.frame_shots = kept
    return gone


def open_track(job: VideoJob) -> int:
    """Give a video with no picture track the plainest one there is: one shot holding
    each plannable stretch end to end. Returns how many that came to.

    It exists because of what a CUT is. Cutting splits the shot that is up, so on an
    empty track there is nothing to split, and the one gesture this screen is built
    around — click the word a shot starts on — answered "that word is outside the
    picture track" and left the operator with no way to make a first shot at all. The
    track could only be brought into being by the `picture` stage, which is the thing
    somebody cutting by hand is deliberately not using.

    So the empty track is treated as what it actually is: not an absence, but one
    uncut shot per region, waiting to be cut. Ad stretches are left alone, exactly as
    they are by the planner — their picture is not ours.

    On a track that already exists it does the smaller half of the same job
    (:func:`open_heads`): puts back the opening shot, if it has gone missing."""
    if job.frame_shots:
        return open_heads(job)
    _cues, regions, _total = framebase.timeline(job.scenes)
    job.frame_shots = [
        FrameShot(start=r.start, duration=r.duration, anchor_scene=-1, anchor_word=-1)
        for r in regions if r.duration > 1e-6
    ]
    _retell(job)
    return len(job.frame_shots)


def open_heads(job: VideoJob) -> int:
    """A region OPENS with a shot, always. Put back any opening shot that is missing,
    and say how many that came to.

    The first seconds of a region are not a cut — nobody placed them, the video simply
    begins — and that is exactly why they used to be lost. A cut is a WORD, the first
    word of a video is a few tenths in (the speaker breathes first), so a shot placed
    on it is NOT the opening shot: the opening one sits at 0.000 with no anchor at all,
    in front of it. Take that one away — and it could be taken away, because
    :func:`drop_cut` only recognised an opening shot by its start and this one's start
    was the word's — and the track begins several seconds into the video. Nothing then
    covers the gap: `framebase.lay_assets` writes no piece for seconds no shot owns, the
    scene's last piece silently absorbs them, and the whole picture of the opening line
    slides. The screen could not repair it either, because the one gesture it has is to
    cut the shot that is up, and over those seconds there was none.

    So the opening shot is an invariant rather than an ordinary shot: it is laid here,
    it comes back here if it is ever lost, and the ✕ on it empties it instead of
    removing it (:func:`drop_cut`). Only on a track that already exists — an empty one
    is not an incomplete track but an unplanned one, and the `picture` stage cuts it
    out of the speech (`framebase.plan_cuts`), which a head laid here would stop it
    doing."""
    if not job.frame_shots:
        return 0
    _cues, regions, _total = framebase.timeline(job.scenes)
    made = [FrameShot(start=r.start, duration=0.0, anchor_scene=-1, anchor_word=-1)
            for r in regions if r.duration > 1e-6
            and not any(abs(s.start - r.start) < SAME_CUT_S for s in job.frame_shots)]
    job.frame_shots.extend(made)
    return len(made)


def place_cut(job: VideoJob, scene: int, word: int) -> int:
    """Start a shot on this word; whatever was up until now ends here.

    That is the whole gesture the screen offers, and it is the one the picture track
    is built to take: a cut is a WORD (see :class:`~.job.FrameShot`), so a cut placed
    by hand survives every later re-voicing exactly as a cut placed by the planner
    does. Returns the index of the shot that now starts there.

    On a video with no track yet it lays one down first (:func:`open_track`), because
    the alternative is the gesture the whole screen is built around refusing to work
    until some other button has been pressed."""
    open_track(job)
    cues, regions, _total = framebase.timeline(job.scenes)
    cue = next((c for c in cues if c.scene == scene and c.word == word), None)
    if cue is None:
        raise ValueError("there is no such word")
    if not any(r.start <= cue.at < r.end for r in regions):
        raise ValueError("that word is inside an ad — the picture there is not ours")
    ordered = _ordered(job)
    hit = next((i for i, s in enumerate(ordered) if abs(s.start - cue.at) < SAME_CUT_S), -1)
    if hit >= 0:  # the picture already changes here; say which shot it is
        ordered[hit].anchor_scene, ordered[hit].anchor_word = scene, word
        return hit
    host = next((i for i, s in enumerate(ordered)
                 if s.start <= cue.at < s.start + s.duration), -1)
    if host < 0:
        raise ValueError("that word is outside the picture track")
    # The FIRST word of a region is already where its opening shot begins, as far as
    # anything on screen is concerned: what lies in front of it is the breath the
    # speaker takes before saying it. Splitting there is honest arithmetic and a
    # useless edit — a quarter-second shot nobody asked for, in front of the one they
    # did — so the click is answered with the opening shot itself. It keeps its start
    # at the region's, where it must stay (see `open_heads`), and the lead-in goes with
    # the picture that follows it, which is the only thing a viewer could read it as.
    opens = any(abs(r.start - ordered[host].start) < SAME_CUT_S for r in regions)
    if opens and not any(ordered[host].start - SAME_CUT_S <= c.at < cue.at for c in cues):
        return host
    fresh = FrameShot(start=cue.at, duration=0.0, anchor_scene=scene, anchor_word=word)
    job.frame_shots.insert(host + 1, fresh)
    retime(job)
    # by identity, not by `.index`: a pydantic model compares by VALUE, and looking a
    # shot up by what it holds would find whichever one happens to match
    return next(i for i, s in enumerate(_ordered(job)) if s is fresh)


def move_cut(job: VideoJob, shot: int, scene: int, word: int) -> int:
    """Move one cut to another word: this shot starts there now instead.

    The same edit as taking the cut off and placing it again, and it exists as one
    operation because that is what the gesture is — a shot dragged along the track — and
    because the two-press version loses the shot in between: :func:`drop_cut` folds the
    stretch into its neighbour, which takes the card, the move and the pin with it.
    Dragging keeps all three and changes only WHEN.

    Every refusal below is a case where the drag would quietly destroy something:

    * the shot a region OPENS with has no cut to move — it is where the video (or the
      stretch after an ad) begins, and it is an invariant (see :func:`open_heads`);
    * a word already held by another shot would collapse the two into one
      (:func:`_dedupe`), and the one that loses is the one being dragged;
    * the region's own first word has the opening shot immediately in front of it, so a
      cut there is the sliver :func:`place_cut` declines to make for the same reason;
    * a word inside an ad is not on a stretch whose picture is ours.

    Returns where the shot ended up, which is not where it was: the track is ordered by
    time, so a shot dragged past its neighbour changes its index."""
    ordered = _ordered(job)
    if not 0 <= shot < len(ordered):
        raise ValueError("there is no such shot")
    s = ordered[shot]
    cues, regions, _total = framebase.timeline(job.scenes)
    if any(abs(r.start - s.start) < SAME_CUT_S for r in regions):
        raise ValueError("this shot is where the video begins — there is no cut to move")
    cue = next((c for c in cues if c.scene == scene and c.word == word), None)
    if cue is None:
        raise ValueError("there is no such word")
    if not any(r.start <= cue.at < r.end for r in regions):
        raise ValueError("that word is inside an ad — the picture there is not ours")
    if any(abs(o.start - cue.at) < SAME_CUT_S for k, o in enumerate(ordered) if k != shot):
        raise ValueError("the picture already changes on that word")
    home = next((r for r in regions if r.start <= cue.at < r.end), None)
    if home is not None and not any(home.start - SAME_CUT_S <= c.at < cue.at for c in cues):
        raise ValueError("that is the first word of the video — the shot in front of it "
                         "is the one it opens with")
    s.anchor_scene, s.anchor_word = scene, word
    s.start = cue.at
    retime(job)
    # by identity: a pydantic model compares by VALUE, so `.index` would find whichever
    # shot happens to hold the same numbers
    return next(i for i, o in enumerate(_ordered(job)) if o is s)


def drop_cut(job: VideoJob, shot: int) -> None:
    """Take one cut back: this shot's stretch joins the one before it.

    The shot that OPENS a region has no cut of its own to take back — it begins
    because the video (or the stretch after an ad) begins — and it is not allowed to
    go, because the seconds it holds belong to nobody else (see :func:`open_heads`).
    So the same ✕ does to it the only thing that can be done: takes the PICTURE off
    and leaves the shot standing, empty, over its own seconds.

    Refusing was the other answer and it was the wrong one twice over. It read as a
    button that does nothing, and it protected the opening shot only where the
    protection was not needed: the test is the shot's START, so an opening shot that
    had been cut on the first WORD — a few tenths in, because the speaker breathes
    first — did not look like one, went, and took the head of the video with it."""
    ordered = _ordered(job)
    if not 0 <= shot < len(ordered):
        raise ValueError("there is no such shot")
    _cues, regions, _total = framebase.timeline(job.scenes)
    s = ordered[shot]
    if any(abs(r.start - s.start) < SAME_CUT_S for r in regions):
        cast(job, shot, None)
        return
    del job.frame_shots[shot]
    retime(job)


def recut(job: VideoJob, sensitivity: float) -> int:
    """Throw the track away and cut it out of the speech again.

    Every card goes with it, pins included, and that is not a bug to be worked around:
    a card was chosen FOR a stretch, and the stretches are what this replaces. It is
    the way back from a montage that went wrong, and it is one press because the
    alternative — dropping forty cuts one at a time — is not one."""
    job.frame_shots = framebase.plan_cuts(job.scenes, min(max(sensitivity, 0.0), 1.0))
    job.frame_asks = []
    # The effects survive: they are placed on WORDS, and the words did not move. What
    # cannot survive is an effect pinned to a region of a card that this re-cut has
    # just taken off the screen, and `effects.settle` is what drops exactly those —
    # asked for by the caller, which is the one that knows the base.
    return len(job.frame_shots)


# --------------------------------------------------------------------------
# what is shown
# --------------------------------------------------------------------------


# The shortest a travel may honestly be when somebody sets it by hand. The planner has
# a floor of its own (`framebase.MOVE_MIN_S`) which is about taste — a move under a
# second reads as a twitch — and this one is only about arithmetic: a ramp of zero
# frames is a division by zero in the crop expression.
MOVE_FLOOR_S = 0.1

# The tightest window `zoompan` will still move: below a tenth it caps its own zoom at
# 10 and quietly stops travelling instead of failing (see `Rect.clamped`). It is the
# only floor a hand-placed key gets — the quality one is the automatic matcher's.
MOVE_FLOOR_SCALE = 0.1


def cast(job: VideoJob, shot: int, card: FrameCard | None, *, move: str = "",
         target: str = "", min_scale: float = MOVE_FLOOR_SCALE,
         lead: float | None = None, span: float | None = None) -> None:
    """Put a picture on a shot — or take one off it, with `card` None.

    The shot is PINNED by the act of choosing: `stages.picture` re-plans everything
    unpinned on every pass, so an unpinned choice is a choice that lasts until the
    next re-voicing.

    Everything else here is the shot's ANIMATION, and it is four questions rather than
    one. `move` is what the camera does, `target` is the marked region it converges on,
    and both go back through `framebase.move_for` so a hand-made move obeys the same
    geometry — and the same crop floor — as a planned one. `lead` and `span` are the
    other two: WHEN the travel starts and HOW LONG it runs, in seconds into the shot.
    The planner rolls those off a die around measured fractions (39% still, 33%
    travelling, 28% still), which is the right default and the wrong thing to be stuck
    with: a held shot that starts moving exactly where the line lands is a decision,
    and there was no way to make it.

    The rects are NOT re-rolled when only the timing changes — the seed is the card,
    the kind and the region — so nudging when it moves does not also change where it
    moves to, which would make the two controls impossible to use together.

    `min_scale` defaults to the geometric floor and not to the quality one. That floor
    (`stages.picture.min_scales`) exists so the automatic matcher does not spend a
    small card on a deep zoom nobody asked for, and it is right to keep doing that —
    but a shot cast BY HAND was aimed at a region somebody marked themselves, and
    raising its window to the floor does not merely soften the move, it re-centres it:
    a window that big can only sit in the middle, so the region is lost and the camera
    converges on the room instead of the desk. The screen says which cards are small;
    how close is too close is the operator's call."""
    ordered = _ordered(job)
    if not 0 <= shot < len(ordered):
        raise ValueError("there is no such shot")
    s = ordered[shot]
    if card is None:
        s.card, s.move, s.target, s.fit, s.pinned = "", None, "", "", False
        return
    was, had = s.move, s.card
    s.card, s.pinned, s.target = card.name, True, target.strip()
    s.fit = s.fit or "exact"  # the operator looked at it; that is the strongest verdict
    # `move_for` builds the two-key form, which replaces any keys placed by hand —
    # pressing a preset is asking for the preset, and a run of keys silently surviving
    # under one would make the chips look broken.
    want = move.strip()
    s.move = framebase.move_for(
        card, s.referents, max(s.duration, 0.1), "",
        random.Random(f"{job.index}|{shot}|{card.name}|{move}|{target}"),
        min_scale=min_scale, target=target.strip(), want=want,
    )
    # A card cannot make every move — a zoom needs a region marked on it, a pan needs
    # two — and `move_for` answers a kind it cannot make by rolling the die among the
    # kinds it can. That is right where the CALLER is the matcher and wrong where it is
    # a person: they pressed one chip, and the shot came back doing something neither
    # they nor anybody else had chosen. The room even says so — «этой карточке такое
    # движение не сделать; осталось …» — and the second half of that sentence was not
    # true. It is now: an unmakeable request leaves the move exactly where it was.
    #
    # Only for a kind named by hand, and only while the card is the same one. Choosing
    # a card (or the same card again) sends no kind at all and means "roll me one",
    # which is the die's job and still is.
    if want and s.move.kind != want and was is not None and had == card.name:
        s.move = was
    if lead is not None or span is not None:
        retime_move(s, lead, span)


def retime_move(shot: FrameShot, lead: float | None, span: float | None) -> None:
    """Move the travel inside its shot, clamped so it stays inside it.

    Clamped rather than refused: the shot's length is not the operator's to control
    here — it is the distance to the next cut — so a travel set against a longer shot
    and then squeezed by a cut in front of it has to land somewhere sensible rather
    than throw."""
    if shot.move is None:
        return
    span_was = max(shot.move.move_end - shot.move.move_start, 0.0)
    want_span = span_was if span is None else span
    want_lead = shot.move.move_start if lead is None else lead
    total = max(shot.duration, MOVE_FLOOR_S)
    want_span = min(max(want_span, MOVE_FLOOR_S), total)
    want_lead = min(max(want_lead, 0.0), max(total - want_span, 0.0))
    shot.move.move_start = want_lead
    shot.move.move_end = want_lead + want_span


def set_keys(job: VideoJob, shot: int, card: FrameCard | None, keys: list[dict]) -> None:
    """Replace a shot's crop move with a run of moments placed by hand.

    A key is three answers and no more: WHEN (seconds into the shot), WHERE to look
    (a region marked on the card, or the whole picture), and HOW CLOSE (the window's
    size). Everything the six presets can do is two of these, and everything they
    cannot — hold on the room, come in on the desk, then pan to the door — is three or
    four, which is the whole reason they exist.

    The rect is resolved HERE, from the card, rather than being drawn by the browser:
    a region is a pair of coordinates ON that file and only the card knows them. `of`
    is carried along afterwards so the editor can show which region a key sits on, but
    the rect is what renders — a region later renamed leaves the key where it was
    pointing.

    A key naming a region takes that region **as it was marked**, centre and size
    both. It used to take the centre and then the caller's size, which defaulted to
    the whole picture — and a window of the whole picture has to be centred, so the
    clamp pulled it straight back to the middle and the region was lost entirely. Aim
    at the desk, get the room.

    Nor is the quality floor applied here. `min_scales` exists so the automatic
    matcher does not choose a window that shows more enlargement than picture; in this
    room the operator is the matcher, they marked that region themselves, and how
    close is too close is their call — the screen says which cards are small and the
    readout gives the enlargement as a percentage. Only the geometric floor holds,
    because below it `zoompan` silently stops moving instead of failing.

    Fewer than two moments is not a move, so it puts the shot back on its preset pair
    rather than leaving it with a single frozen key."""
    ordered = _ordered(job)
    if not 0 <= shot < len(ordered):
        raise ValueError("there is no such shot")
    s = ordered[shot]
    if s.move is None:
        raise ValueError("this shot has no picture to move over")
    marked = {t.label.strip(): t for t in (card.targets if card else []) if t.label.strip()}
    out: list[MoveKey] = []
    for k in keys:
        of = str(k.get("of", "")).strip()
        target = marked.get(of)
        base = target.rect if target else Rect()
        size = k.get("scale")
        size = base.scale if size in (None, "") else float(size)
        out.append(MoveKey(
            at=min(max(float(k.get("at", 0.0)), 0.0), max(s.duration, 0.0)),
            rect=Rect(cx=base.cx, cy=base.cy, scale=size).clamped(MOVE_FLOOR_SCALE),
            of=of if target else "",
        ))
    out.sort(key=lambda k: k.at)
    # The preset underneath is left exactly as it was — its pair of rects, its timing
    # AND its kind. That is what makes the choice between the two reversible: going to
    # keys seeds from the preset, coming back restores it untouched, and neither
    # direction costs the operator what they had. `kind` in particular is not
    # overwritten: it is the anti-repetition key the planner reasons with, and a track
    # of shots all calling themselves "keys" would tell it nothing.
    s.move.keys = out if len(out) >= 2 else []


def frozen(shot: FrameShot) -> bool:
    """Whether this shot's move goes nowhere: every moment the same window.

    A `hold` is honestly this and is not a fault. Anything else is — a push-in, a
    zoom or a pan whose ends came out equal is a move that was built against a crop
    floor that forbade one, and it renders as a frozen frame with nothing saying so."""
    if shot.move is None or shot.move.kind == "hold":
        return False
    pts = shot.move.points()
    first = pts[0][1]
    return all(abs(r.cx - first.cx) < 1e-6 and abs(r.cy - first.cy) < 1e-6
               and abs(r.scale - first.scale) < 1e-6 for _at, r in pts)


def refresh_moves(job: VideoJob, cards: list[FrameCard]) -> int:
    """Build every shot's move again, keeping what the operator chose. Returns how
    many actually changed.

    The repair for a track whose moves were decided under a crop floor that has since
    been corrected: they are sitting in a checkpoint with both ends equal, and nothing
    re-plans a PINNED shot — which every hand-cast shot is. Re-clicking forty cards to
    get forty moves back is not an answer.

    It is safe to press on a healthy track. The kind, the region and the travel's
    timing are all read back off the shot and handed to the same builder with the same
    seed, so a move that was already fine is rebuilt identically. Shots carrying
    hand-placed keys are left alone: those are not built from anything and there is
    nothing to recompute."""
    by_name = {c.name: c for c in cards}
    changed = 0
    for i, s in enumerate(_ordered(job)):
        card = by_name.get(s.card)
        if card is None or s.move is None or s.move.keys:
            continue
        before = (s.move.rect_a.model_dump(), s.move.rect_b.model_dump())
        cast(job, i, card, move=s.move.kind, target=s.target,
             lead=s.move.move_start, span=s.move.move_end - s.move.move_start)
        if (s.move.rect_a.model_dump(), s.move.rect_b.model_dump()) != before:
            changed += 1
    return changed


def take_card(world, src: Path, *, description: str = "", prompt: str = "",
              note: str = "") -> FrameCard:
    """Take a picture into the world's base from the montage screen.

    The same filing `stages.picture.file_card` does for a delivered ask, reached from
    the other end: there the pipeline asked for a picture and the operator brought it,
    here the operator is looking at a stretch with nothing on it and has one. The
    checksum test is the same one and matters for the same reason — a picture already
    in the base must not be filed a second time under a second name, or the regions
    somebody drew on the first copy belong to a card nothing uses."""
    from .stages.picture import card_for_file

    already = card_for_file(world, src)
    if already is not None:
        if already.retired:
            already.retired = False
            write_frame_card(already)
        return already
    root = frames_dir(world)
    root.mkdir(parents=True, exist_ok=True)
    name = free_name(description or Path(src).stem, {c.name for c in world.frames})
    dest = root / f"{name}{Path(src).suffix.lower()}"
    shutil.copy2(src, dest)
    card = FrameCard(name=name, file=dest.name, prompt=prompt.strip(),
                     description=description.strip(), note=note.strip(),
                     file_sha=file_sha(dest), root=root)
    write_frame_card(card)
    world.frames.append(card)
    return card


def free_name(text: str, taken: set[str]) -> str:
    """A card name nothing else has, out of whatever the operator typed. The world's
    own alphabet is fine — every other config here is already named in it."""
    base = re.sub(r"[^\w \-]", "", text).strip()[:40].strip() or "кадр"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def card_at(job: VideoJob, seconds: float) -> tuple[FrameShot | None, float]:
    """Which shot is up at this moment of the video, and how far into it we are.

    The pair the preview is drawn from and the pair the true-frame render needs: a
    crop move is laid against the SHOT's clock, so an absolute second is meaningless
    to it until it has been told which shot it belongs to."""
    for s in _ordered(job):
        if s.start <= seconds < s.start + s.duration:
            return s, seconds - s.start
    last = _ordered(job)[-1] if job.frame_shots else None
    return (last, max(seconds - last.start, 0.0)) if last else (None, 0.0)


def voice_pieces(job: VideoJob) -> list[tuple[Path | None, float]]:
    """Every line's audio and the length the timeline gives it, in order — what
    `media.ffmpeg.voice_track` builds the preview's clock out of. A line with no voice
    yet contributes its silence rather than nothing, so the hole is where it will be."""
    return [(scene.audio if scene.audio and Path(scene.audio).is_file() else None,
             max(scene.duration, 0.01)) for scene in job.scenes]
