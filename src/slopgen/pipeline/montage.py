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
underneath. :func:`place_cut` and :func:`drop_cut` are the whole of it.

**Beats are added and dropped here too**, which is not true of the beat modes and is
worth saying why. In a drama a beat IS a clip: add one and there is a shot to generate,
a voice to fit to it, and a sync to redo for everything after. Here the picture track
does not know the beats exist — it is cut on words and runs past the narration on
purpose — so adding a line costs exactly one thing: the shot straddling the seam grows
by the new line's length, and the operator cuts it if they do not want that. Every
other shot keeps its card, every anchor keeps its word, and not one line that was
already voiced is re-voiced. Measured on a three-line job: cards identical, anchors on
the same words, audio paths untouched, one duration changed.

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
    exists to the moment it is cut. One whose picture comes from a generator does not:
    there is no second clock there to edit, and the track lane would be a row of words
    that cannot be cut on."""
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
    beats = [s for s in job.scenes if not s.is_ad]
    silent = sum(1 for s in beats if not (s.audio and s.words))
    if silent:
        out.append({"what": "unvoiced", "n": silent})
    empty = sum(1 for s in job.frame_shots if not s.card)
    if empty:
        out.append({"what": "uncovered", "n": empty})
    return out


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


def _anchor_fractions(job: VideoJob, index: int) -> dict[int, float]:
    """Where in a line each shot anchored there currently sits, as 0..1 of the line.

    Word INDICES do not survive a rewrite — six words become seven and every anchor
    after the third points at the wrong one — while the place in the line does: a cut
    a third of the way through a sentence is still a third of the way through the
    sentence it was rewritten into. So the anchors are taken down as fractions before
    the words move and put back on the nearest word after (see :func:`_rebind`)."""
    scene = job.scenes[index]
    n = max(len(scene.words) - 1, 1)
    return {i: (s.anchor_word / n)
            for i, s in enumerate(job.frame_shots)
            if s.anchor_scene == index and s.anchor_word >= 0}


def _rebind(job: VideoJob, index: int, fractions: dict[int, float]) -> None:
    """Put the shots anchored in one line back on a word, then re-measure the clock."""
    scene = job.scenes[index]
    n = max(len(scene.words) - 1, 1)
    for i, frac in fractions.items():
        if i < len(job.frame_shots):
            job.frame_shots[i].anchor_word = min(round(frac * n), max(len(scene.words) - 1, 0))
    retime(job)


def voice(job: VideoJob, ctx: AppContext, index: int, rate: int | None = None) -> float:
    """Say this line again, now, at `rate` percent (None = whatever it already uses).

    The clock moves under everything after it, which is why `retime` follows: the cuts
    themselves are not re-decided — they were placed on words and those words are
    still the same words — they are re-measured (see `framebase.reanchor`)."""
    from .stages import tts as tts_stage

    if not job.scenes[index].text.strip():
        raise ValueError("there is nothing written on this line to say")
    before = _anchor_fractions(job, index)
    seconds = tts_stage.resynth_one(job, ctx, index, rate=rate)
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
    for s in job.frame_shots:
        if s.anchor_scene >= at:
            s.anchor_scene += 1
    job.scenes.insert(at, scene)
    _settle_takes(job)
    retime(job)
    return at


def drop_line(job: VideoJob, index: int) -> None:
    """Take one line out of the video entirely.

    The cheap structural edit, and the only one offered here: nothing that survives it
    has to be re-made, because a removed line takes only its own seconds with it.
    Shots anchored INSIDE it go to the first word of whatever follows, which is where
    a picture that covered the removed line now begins, and any two shots that land on
    the same word after that are one shot — the picture cannot change twice at once."""
    if not 0 <= index < len(job.scenes) or len(job.scenes) <= 1:
        raise ValueError("a video needs at least one line")
    for s in job.frame_shots:
        if s.anchor_scene == index:
            s.anchor_scene, s.anchor_word = index + 1, 0
        if s.anchor_scene > index:
            s.anchor_scene -= 1
    del job.scenes[index]
    # an anchor that ran off the end goes to the last word there is
    last = len(job.scenes) - 1
    for s in job.frame_shots:
        if s.anchor_scene > last:
            s.anchor_scene, s.anchor_word = last, max(len(job.scenes[last].words) - 1, 0)
    _settle_takes(job)
    retime(job)
    _dedupe(job)


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
    """Re-measure the whole track against the clock as it stands now."""
    framebase.reanchor(job.scenes, job.frame_shots)
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


def _dedupe(job: VideoJob) -> None:
    """Two shots at the same moment are one shot. It happens after a line is dropped
    and after a cut is placed where a re-measure had already put one; the later of the
    two loses, because the earlier one is the one that was there first."""
    kept: list[FrameShot] = []
    for s in _ordered(job):
        if kept and s.start - kept[-1].start < SAME_CUT_S:
            continue
        kept.append(s)
    job.frame_shots = kept
    retime(job)


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
    they are by the planner — their picture is not ours."""
    if job.frame_shots:
        return 0
    _cues, regions, _total = framebase.timeline(job.scenes)
    job.frame_shots = [
        FrameShot(start=r.start, duration=r.duration, anchor_scene=-1, anchor_word=-1)
        for r in regions if r.duration > 1e-6
    ]
    _retell(job)
    return len(job.frame_shots)


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
    fresh = FrameShot(start=cue.at, duration=0.0, anchor_scene=scene, anchor_word=word)
    job.frame_shots.insert(host + 1, fresh)
    retime(job)
    # by identity, not by `.index`: a pydantic model compares by VALUE, and looking a
    # shot up by what it holds would find whichever one happens to match
    return next(i for i, s in enumerate(_ordered(job)) if s is fresh)


def drop_cut(job: VideoJob, shot: int) -> None:
    """Take one cut back: this shot's stretch joins the one before it.

    The first shot of a region has no cut of its own to take back — it begins because
    the video (or the stretch after an ad) begins — so it is refused rather than
    quietly doing nothing."""
    ordered = _ordered(job)
    if not 0 <= shot < len(ordered):
        raise ValueError("there is no such shot")
    _cues, regions, _total = framebase.timeline(job.scenes)
    s = ordered[shot]
    if any(abs(r.start - s.start) < SAME_CUT_S for r in regions):
        raise ValueError("this shot opens the video — there is no cut in front of it")
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
    s.card, s.pinned, s.target = card.name, True, target.strip()
    s.fit = s.fit or "exact"  # the operator looked at it; that is the strongest verdict
    # `move_for` builds the two-key form, which replaces any keys placed by hand —
    # pressing a preset is asking for the preset, and a run of keys silently surviving
    # under one would make the chips look broken.
    s.move = framebase.move_for(
        card, s.referents, max(s.duration, 0.1), "",
        random.Random(f"{job.index}|{shot}|{card.name}|{move}|{target}"),
        min_scale=min_scale, target=target.strip(), want=move.strip(),
    )
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
