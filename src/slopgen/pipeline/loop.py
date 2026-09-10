"""Generation in a loop: keep making videos until told to stop, steerable while it runs.

A batch (``--count 5``) decides everything before it starts and then goes: five videos
on one set of settings, five topics from one source, and nothing to be said to it after
the first one begins. A loop is the other shape of the same work — one video at a time,
with the decisions kept OPEN. Who picks the topic, how many are still to be made, which
stages stop for review: all three are read again before every iteration, off a file
anyone may edit, so changing your mind costs an edit instead of a restart.

That file is ``<loop dir>/loop.json``, and it has two halves — which is what makes two
writers safe. The operator owns the CONTROL half — source, topics, limit, breakpoints,
on_park, max_fails, stop, and the whole settings template with them — while the runner
owns the LOG half (what has been made, where it went, what the loop is doing now). Each writes back only its own keys over whatever the
file says at that moment, so a topic typed while an iteration is being recorded cannot
lose the record, and the record cannot lose the topic. The one crossing is `take_next`:
consuming a queued topic is a control write, because taking it off the queue IS what
consuming it means.

**A topic in the queue is always used; the source decides what happens when the queue
runs out.** Under ``ai`` the model invents one and the loop never waits; under ``me`` the
loop waits for you. That is the whole of the switch, and it is why typing topics into a
loop set to ``ai`` is not a contradiction — the first video keeps the idea it was started
with either way.

**A queued entry is a whole video, not a line of text.** It carries the topic and, when
the operator wants this one video different, its OWN answers to the settings — a longer
length, another voice, a breakpoint just this once (`QueueItem.over`). They are folded
over the template when that entry comes up (`params_for`), so a queue of ten is ten
videos already decided rather than ten reminders to come back and retune between them.
That is the whole point of the thing: the operator fills it, starts it, and leaves.

``ahead`` is the same idea pointed at the model. Set it, and a loop picking its own
topics keeps that many of them WAITING in the queue instead of inventing each one at the
moment it is needed — which is what makes an invented topic something you can read,
rewrite, reorder or throw away before it becomes a video.

The runs themselves are ORDINARY runs. They land in the output folder beside every other
one, with their own checkpoints, and every existing way of picking a parked run back up —
``slopgen review``, ``slopgen gather``, the runs list in the browser — reaches them
without knowing a loop exists. The loop folder holds the plan and nothing else.

Waiting is a state, not a stall, and there are two of them: ``waiting`` (the topic is
yours to give and the queue is empty) and ``held`` (the last iteration parked on a
breakpoint or for hand-made clips). Holding is the default because the alternative is ten
parked runs and nobody's attention on any of them — starting the next video is exactly
what stops the operator from finishing the one that asked them a question. Both states
poll the plan, so both are answered by an edit: a topic, a switch to ``ai``,
``on_park=go_on``, or ``stop``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, ValidationError, model_validator

from ..config import RunParams
from .checkpoint import outcome

PLAN_NAME = "loop.json"

# How often a waiting loop reads its plan again. Nothing expensive is being polled — one
# small file — and two seconds is what makes a topic typed in another window feel like it
# arrived rather than like it was queued.
POLL_S = 2.0

# How many iterations may fail back to back before the loop gives up. A loop with no
# limit and a dead API key is a loop that spends the night failing in a tight ring; the
# streak is what turns that into a stop with a reason. Any iteration that does not fail
# resets it.
MAX_FAILS = 3

# How long a writer waits for another writer, and how old a lock has to be before it is
# assumed to belong to something that is no longer running.
LOCK_WAIT_S = 5.0
LOCK_STALE_S = 30.0

# How long a loop leaves the model alone after it failed to think of a topic. Stocking
# the queue ahead is a convenience, never a reason for anything to stop: a dead key or a
# rate limit must cost the lookahead and nothing else, and retrying it every two seconds
# would turn one broken minute into a thousand refused requests.
STOCK_RETRY_S = 60.0

Source = Literal["ai", "me"]
OnPark = Literal["hold", "go_on"]

# The operator's half of the plan file; everything else in it is the runner's log.
# `params` is in it — the settings every next video is built from are the operator's,
# not the runner's, which is what lets them be rewritten between two videos without the
# next `write_log` putting the old ones back.
CONTROL = ("params", "source", "topics", "limit", "breakpoints", "on_park", "max_fails",
           "ahead", "stop")

# Loop states that mean it is still going. The two idle ones are deliberate: a loop that
# is waiting for a topic or holding on a parked run has not stopped, it has asked.
LIVE = ("queued", "running", "waiting", "held")

# What a parked run is parked ON. Both hold the loop, and both are released by the
# operator dealing with the run itself, in whichever interface they are standing in.
PARKED = ("review", "paused")


# What a loop may NOT be told to change about itself, and where each of them lives
# instead. Everything else in `RunParams` is a setting and may be rewritten between two
# videos — the look, the length, the voice, the cast, the world, the ad, the account.
PINNED = {
    "mode",      # a different chain, a different writer, other breakpoints: another loop
    "count",     # always one; the loop is the thing that repeats
    "out",       # where the loop writes — moving it mid-way splits its own output
    "idea",      # the topic, which is the queue's business (see `topic_field`)
    "scenario",
}

# Shorter names for the same settings, because a loop is steered by typing at it and
# `duration=90` is what a person types. The full field name always works too.
ALIASES = {
    "duration": "duration_s", "length": "duration_s", "tol": "duration_tol_s",
    "clip": "clip_seconds", "clip_s": "clip_seconds",
    "voice": "voice_override", "engine": "tts_engine", "rate": "tts_rate",
    "subs": "subtitle_style", "clean_subs": "clean_subtitles",
    "style": "visual_style", "notes": "visual_notes", "fx": "filters",
    "cast": "manual_cast", "narrator": "fandom_voice", "world": "fandom",
    "role": "viewer_role",
    "invent": "fandom_invent",
    "breaks": "breakpoints", "source": "manual_orchestration",
    "type": "content_type", "swearing": "profanity",
}

# Naming a config drops the ad-hoc one standing in front of it. Both fields exist for a
# reason — the wizard builds a whole chain that no folder holds — but the ad-hoc one
# WINS wherever both are set (see `AppContext.orchestration`), so a loop told to use a
# named profile and left with an ad-hoc one would go on ignoring the instruction.
_SHADOWS = {"visuals": "manual_visuals", "ad": "manual_ad",
            "orchestration": "manual_orchestration"}

_TRUE = {"1", "true", "yes", "on", "y", "да"}
_FALSE = {"0", "false", "no", "off", "n", "нет", "-", ""}


def topic_field(mode: str) -> str:
    """Which run parameter a topic goes into: an info clip is given an IDEA, a drama or a
    fandom a SCENARIO. They are the same thing under two names — what this video is about
    — down to what an empty one means: the model invents it."""
    return "idea" if mode == "info" else "scenario"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _locked(path: Path):
    """Hold the plan file for one read-modify-write, across processes.

    A lock file created exclusively, because that is the one primitive every platform
    this runs on agrees about. Two rules keep it from being worse than the race it
    prevents: a lock left behind by something that died is broken after `LOCK_STALE_S`,
    and a writer that has waited `LOCK_WAIT_S` goes ahead anyway. A loop must never stop
    running because of a file somebody forgot to delete — the worst a broken lock costs
    is one lost edit, and the worst waiting forever costs is the loop."""
    lock = path.with_name(path.name + ".lock")
    started = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > LOCK_STALE_S
            except OSError:  # it went away between the two calls; try again at once
                continue
            if stale:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() - started > LOCK_WAIT_S:
                break
            time.sleep(0.02)
        except OSError:  # a directory that is not there yet, a filesystem that will not
            break        # hold locks: the write itself is still atomic
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


class QueueItem(BaseModel):
    """One video the loop has not made yet: what it is about, and what it wants done
    differently from the loop's own settings.

    `over` is the second half and the reason this is a model rather than a string. A
    queue of plain topics is a queue that has to be watched — every video that wanted a
    different length or one breakpoint meant coming back between two videos and retuning
    the loop by hand. With the settings on the ENTRY, the queue is the plan: ten entries
    are ten decided videos, and the operator can go to bed.

    The id is what an edit addresses. Positions move — that is what reordering IS — so
    editing entry number three would mean editing whatever had drifted into third place
    by the time the request arrived."""

    id: str = ""
    topic: str = ""  # empty = the writer invents this one, under these settings
    by: Source = "me"  # who put it here; `ai` is one the model thought of ahead
    # per-video settings, folded over the template when this entry comes up. Names are
    # `RunParams` field names; what may be in here is `overridable()`.
    over: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data):
        """A bare string is an entry with nothing but a topic — which is what every
        queue held before entries had settings, and what a person types at a terminal or
        a chat. Both spellings arrive here and both mean the same entry."""
        return {"topic": data} if isinstance(data, str) else data

    def model_post_init(self, _context) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]

    @property
    def empty(self) -> bool:
        """Nothing was asked for at all. An entry with no topic but with settings is NOT
        empty — "the model picks it, but make this one ninety seconds" is a real
        instruction, and the commonest thing to queue behind a topic that took."""
        return not self.topic.strip() and not self.over


class Iteration(BaseModel):
    """One video the loop made, or is making."""

    n: int
    topic: str = ""  # empty = the model was left to invent one
    source: Source = "ai"
    over: dict[str, Any] = {}  # what this one was asked to do differently, if anything
    run_dir: str = ""
    status: str = ""  # done | failed | review | paused | stopped (empty = still going)
    at: str = ""


class LoopPlan(BaseModel):
    """Everything a loop is: the template it repeats, the controls, and what it has done."""

    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    title: str = ""
    # The settings every iteration is built from. The topic field in it is ignored — the
    # queue and the source decide that per video (see `params_for`).
    params: RunParams

    # -- control (the operator's half; may change while the loop runs) ------
    source: Source = "ai"
    topics: list[QueueItem] = []  # the queue, taken from the front
    limit: int = 0  # 0 = no limit
    breakpoints: list[str] = []
    on_park: OnPark = "hold"
    max_fails: int = MAX_FAILS
    # how many topics the model keeps WAITING in the queue when it is the one picking
    # them. 0 = none, and each is invented at the moment it is needed, unseen.
    ahead: int = 0
    stop: bool = False

    # -- log (the runner's half) -------------------------------------------
    status: str = "queued"
    note: str = ""
    fails: int = 0  # consecutive failures, reset by anything else
    iterations: list[Iteration] = []

    @property
    def started(self) -> int:
        """Videos this loop has begun — what the limit counts. A failed one counts too:
        it was an iteration, and a limit that only counted successes would make a loop
        that cannot succeed run forever."""
        return len(self.iterations)

    @property
    def made(self) -> int:
        return sum(1 for it in self.iterations if it.status == "done")

    @property
    def live(self) -> bool:
        return self.status in LIVE

    def params_for(self, item: "QueueItem") -> RunParams:
        """One iteration's parameters: the template, with this video's topic in whichever
        field its mode calls it, the breakpoints as they stand right NOW, and a count of
        one — the loop is the thing that repeats, so the batch inside it never does.

        The entry's own settings go on LAST, over all of that, because that is what an
        override is for: it is this video's answer where the loop's answer would
        otherwise stand, breakpoints included."""
        fresh = self.params.model_copy(update={
            topic_field(self.params.mode): item.topic.strip(),
            "breakpoints": list(self.breakpoints),
            "count": 1,
        })
        return with_overrides(fresh, item.over)


# -- the settings, rewritten while it runs -----------------------------------
#
# A loop is a set of settings repeated, so changing the settings is most of what
# steering one means: the look this week, the voice tomorrow, the world after that. The
# rules are two. Everything but `PINNED` may change, and a change lands on the NEXT
# video — the one being made was launched on the settings that stood when it started,
# which is the only way a setting can mean one thing for a whole video.


def editable() -> list[str]:
    """Every setting of a loop, in the order `RunParams` declares them."""
    return [n for n in RunParams.model_fields if n not in PINNED]


def settable_names() -> list[str]:
    """The names that may be TYPED at a loop: every setting plus its short name, minus
    the two ad-hoc configs a wizard builds and a keyboard cannot (`manual_visuals`,
    `manual_ad` — their named counterparts are `visuals` and `ad`)."""
    plain = [n for n in editable() if n not in ("manual_visuals", "manual_ad")]
    return sorted(set(plain) | set(ALIASES))


# Settings a whole loop has and one queued video may NOT be given its own answer to.
# All four are whole configs rather than values — a cast of character cards, a generator
# chain, a visuals profile, an ad contract — and a queue entry is a line in a list. They
# stay the loop's, which is where they are already edited, in the form that builds them.
ADHOC = ("manual_cast", "manual_visuals", "manual_ad", "manual_orchestration")


def overridable() -> list[str]:
    """Every setting one queued video may answer for itself, in `RunParams` order."""
    return [n for n in editable() if n not in ADHOC]


def clean_overrides(base: RunParams, over: dict) -> dict[str, Any]:
    """These per-video settings, checked against the settings they will be folded into.

    Checked HERE, at the moment the operator types them, and not when the video comes up
    — which may be tomorrow night with nobody watching. A refused override leaves the
    queue exactly as it was; an accepted one is stored already coerced, so what the queue
    shows is what the run will get."""
    out: dict[str, Any] = {}
    for key, value in over.items():
        name = ALIASES.get(key, key)
        if name not in overridable():
            where = ("it is the loop's own — one queued video cannot have a different one"
                     if name in ADHOC or name in PINNED else "no such setting")
            raise ValueError(f"`{key}` is not a per-video setting — {where}")
        out[name] = value
    if out:
        # Folded into a real copy of the settings, because the only honest test of a
        # value is the model that will have to hold it — and then read back OFF that
        # copy, so what the queue stores is the coerced value rather than whatever
        # spelling it arrived in. `duration=90` typed at a terminal and `90` sent by a
        # browser have to end up the same entry, or the two doors show different queues.
        dump = with_overrides(base, out).model_dump(mode="json")
        out = {k: dump[k] for k in out}
    return out


def with_overrides(params: RunParams, over: dict) -> RunParams:
    """`params` with one video's own answers laid over it. Raises `ValueError` on a value
    the settings cannot hold, in the same short shape `apply_settings` reports."""
    if not over:
        return params
    data = params.model_dump(mode="json")
    data.update(over)
    try:
        return RunParams.model_validate(data)
    except ValidationError as e:
        raise ValueError("; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )) from e


def as_items(raw: Iterable) -> list[QueueItem]:
    """A queue out of whatever was handed over — entries, dicts, or the bare strings
    every other door still speaks.

    Blank entries are KEPT here, because a blank entry means something: "one more video,
    the model picks what it is about". Only the doors where a blank is a slip rather than
    an instruction throw them away, and that is exactly one door — typing nothing into
    the add-a-topic box (see `add_topics`)."""
    return [x if isinstance(x, QueueItem) else QueueItem.model_validate(x) for x in raw]


def retune(current: RunParams, incoming: RunParams) -> RunParams:
    """The new settings, with what a loop may not change taken back off them.

    Whole-template edits come from a form that also carries a mode and a count, because
    it is the same form that starts a run. What it says about those is not an
    instruction — it is the shape of the loop it is editing."""
    return incoming.model_copy(update={f: getattr(current, f) for f in PINNED})


def _one_stage_chain(model: str):
    """A picture source named as one generator, as the pipeline wants it: a chain of a
    single stage. It is how the browser starts a fandom run, and typing `source=flux` at
    a loop has to mean the same thing or the two interfaces disagree about a word."""
    from ..config.models import OrchestrationConfig, OrchestrationStage
    from ..media.generate import model_clip_seconds

    return OrchestrationConfig(
        name=model,
        stages=[OrchestrationStage(model=model, metric="percent", amount=100.0,
                                   clip_seconds=model_clip_seconds(model))],
    )


def parse_setting(field: str, text: str, store=None):
    """Turn one `key=value` typed at a terminal into the value that field holds.

    Only the shapes a string cannot reach on its own are handled here — a list, a dose
    map, a cast of characters, a generator named where a chain is wanted. Everything
    else is handed to pydantic as it was typed, because that is the same coercion the
    config files get and a second one would drift from it."""
    name = ALIASES.get(field, field)
    if name not in RunParams.model_fields:
        raise ValueError(f"no such setting: {field}")
    if name in PINNED:
        where = {"mode": "start another loop", "count": "the loop repeats instead",
                 "out": "it is where this loop writes",
                 "idea": "topics are the queue's (`loop topic ...`)",
                 "scenario": "topics are the queue's (`loop topic ...`)"}[name]
        raise ValueError(f"`{name}` is not a setting of a running loop — {where}")
    text = text.strip()
    ann = RunParams.model_fields[name].annotation

    if ann is bool:
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"{field}: expected yes or no, got {text!r}")
    if name == "filters":
        from ..media.filters import parse as parse_filters

        return parse_filters([p for p in text.replace(",", " ").split() if p])
    if name == "manual_cast":
        if store is None:
            raise ValueError("the cast can only be set where the character library is")
        names = [n.strip() for n in text.split(",") if n.strip()]
        missing = [n for n in names if n not in store.characters]
        if missing:
            raise ValueError(f"unknown character(s): {', '.join(missing)}")
        return [store.characters[n] for n in names]
    if name == "manual_orchestration":
        return _one_stage_chain(text) if text else None
    if name in ("manual_visuals", "manual_ad"):
        plain = {"manual_visuals": "visuals", "manual_ad": "ad"}[name]
        raise ValueError(f"`{name}` is built by the wizard — set `{plain}` by name instead")
    if name == "breakpoints" or ann is list[str]:
        return [b.strip() for b in text.replace(",", " ").split() if b.strip()]
    if name == "subtitle_style" and text.lower() in ("", "-", "none"):
        return None
    return text  # pydantic coerces it exactly as it coerces the config files


def apply_settings(current: RunParams, edits: dict[str, str], store=None) -> RunParams:
    """`current` with these `key=value` edits folded in. Raises `ValueError` on a name
    or a value the settings do not have — before anything is written, because a typo
    accepted here surfaces three videos later as a run that fails for no visible
    reason."""
    data = current.model_dump()
    for key, raw in edits.items():
        name = ALIASES.get(key, key)
        data[name] = parse_setting(key, raw, store)
        shadowed = _SHADOWS.get(name)
        if shadowed:
            data[shadowed] = None
        if name == "manual_orchestration" and data[name] is not None:
            data["orchestration"] = ""  # the ad-hoc chain IS the answer now
    try:
        return RunParams.model_validate(data)
    except ValidationError as e:
        # pydantic's own report is a paragraph per field, complete with a docs URL.
        # What is wanted at a prompt is the field and what is wrong with it.
        raise ValueError("; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )) from e


def check_params(store, params: RunParams) -> list[str]:
    """Everything these settings name that the configs do not have.

    Worth doing at the moment of the edit and nowhere else: a loop is unattended by
    design, so a misspelt account or ad contract would otherwise be discovered by a
    video failing at its last stage, hours later, with the operator asleep."""
    from ..media.filters import KEYS as FILTER_KEYS
    from ..tts import ENGINES

    bad: list[str] = []
    named = [
        ("content type", params.content_type, store.content_types),
        ("visuals profile", params.visuals, store.visuals),
        ("ad contract", params.ad, store.ads),
        ("account", params.push, store.accounts),
        ("orchestration", params.orchestration, store.orchestrations),
        ("world", params.fandom, store.fandoms),
    ]
    for what, value, have in named:
        if value and value not in have:
            bad.append(f"no {what} named {value!r} (have: {', '.join(have) or 'none'})")
    if params.tts_engine and params.tts_engine not in ENGINES:
        bad.append(f"no voice engine named {params.tts_engine!r} "
                   f"(have: {', '.join(ENGINES)})")
    unknown_fx = [k for k in params.filters if k not in FILTER_KEYS]
    if unknown_fx:
        bad.append(f"no filter named {', '.join(unknown_fx)} (have: {', '.join(FILTER_KEYS)})")
    if params.mode == "fandom" and not params.fandom:
        bad.append("a fandom loop needs a world")
    return bad


def describe(params: RunParams) -> list[tuple[str, str]]:
    """Every setting and what it is set to, for an operator asking what they are on.

    The ad-hoc configs are shown as the one thing about them that can be read at a
    glance and typed back: a cast is its names, a chain is its first generator."""
    out: list[tuple[str, str]] = []
    for name in editable():
        value = getattr(params, name)
        if name == "manual_cast":
            value = ", ".join(c.name for c in value)
        elif name == "manual_orchestration":
            value = value.stages[0].model if value and value.stages else ""
        elif name in ("manual_visuals", "manual_ad"):
            value = value.name if value else ""
        elif name == "filters":
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        elif isinstance(value, bool):
            value = "yes" if value else "no"  # and `set dry_run=yes` types straight back
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        out.append((name, "" if value is None else str(value)))
    return out


@dataclass
class LaunchResult:
    """What one iteration left behind. `status` is the run's own vocabulary — done,
    failed, paused, review, stopped — so the loop needs no verdict of its own."""

    run_dir: Path | None
    status: str
    message: str = ""


class LoopFile:
    """The plan on disk: read by everyone, written by two halves that never overwrite
    each other's keys (see the module docstring)."""

    def __init__(self, loop_dir: Path | str):
        self.dir = Path(loop_dir)
        self.path = self.dir / PLAN_NAME

    # -- construction ------------------------------------------------------

    @classmethod
    def create(cls, loop_dir: Path | str, params: RunParams, **control) -> "LoopFile":
        f = cls(loop_dir)
        f.dir.mkdir(parents=True, exist_ok=True)
        plan = LoopPlan(params=params, created_at=_now(),
                        **{k: v for k, v in control.items() if v is not None})
        # the topic the run was started with is the first video's topic, whoever picks
        # the rest — a loop begun with an idea in hand does not throw it away
        seed = getattr(params, topic_field(params.mode), "").strip()
        if seed and seed not in [i.topic for i in plan.topics]:
            plan.topics.insert(0, QueueItem(topic=seed))
        f._write(plan.model_dump(mode="json"))
        return f

    @classmethod
    def open(cls, loop_dir: Path | str) -> "LoopFile":
        f = cls(loop_dir)
        if not f.path.is_file():
            raise FileNotFoundError(f"no loop at {f.path}")
        return f

    # -- reading -----------------------------------------------------------

    def read(self) -> LoopPlan:
        text = self.path.read_text(encoding="utf-8")
        plan = LoopPlan.model_validate_json(text)
        self._stamp(plan, text)
        return plan

    def _stamp(self, plan: LoopPlan, text: str) -> None:
        """Give the queue ids if it is one written before entries had them.

        Once, and back into the file, because an id is what every edit addresses: ids
        invented afresh on each read would name a different video every time they were
        read, and the bulk edit sent from a page would land on nothing. Best-effort — a
        loop whose folder went away is not worth an exception on the way out of a
        read."""
        try:
            raw = json.loads(text).get("topics") or []
            if any(not (isinstance(t, dict) and t.get("id")) for t in raw):
                self.write_control(topics=[i.model_dump(mode="json") for i in plan.topics])
        except Exception:
            pass

    def _raw(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # -- writing -----------------------------------------------------------
    #
    # Every write is read-modify-write, and the whole point of this file is that several
    # things write it: the runner recording a video, the browser retuning the settings,
    # a terminal queueing a topic. So each one takes the lock, re-reads what is there
    # NOW, changes its own keys and puts it back atomically. Skipping the lock is not a
    # theoretical race — two writers sharing one temp file interleave inside it, and
    # what lands is a file that is no longer JSON.

    def _write(self, data: dict) -> None:
        data["updated_at"] = _now()
        self.dir.mkdir(parents=True, exist_ok=True)
        # named for this process: the lock covers writers that cooperate, and a unique
        # temp name covers whatever does not
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _update(self, change: Callable[[dict], None]) -> LoopPlan:
        """Apply `change` to the file's contents as they are right now.

        `change` may raise — a refused setting does — and then nothing is written at
        all, which is what makes a rejected edit leave the loop exactly as it was."""
        with _locked(self.path):
            data = self._raw()
            change(data)
            self._write(data)
        return LoopPlan.model_validate(data)

    def _merge(self, values: dict) -> LoopPlan:
        """Put these keys into what the file says right now, and nothing else."""
        return self._update(lambda data: data.update(values))

    def write_log(self, plan: LoopPlan) -> LoopPlan:
        """Store the runner's half of `plan`, keeping whatever the control half says on
        disk — it may have been edited while the iteration this records was running."""
        dump = plan.model_dump(mode="json")
        return self._merge({k: v for k, v in dump.items() if k not in CONTROL})

    def restart(self) -> LoopPlan:
        """Clear the reasons this loop ended, so it may be run again on the same plan.

        Every one of them lives in the file rather than in whoever was driving, which
        is what makes a loop resumable at all — but it also means simply starting a
        thread again would end it on the first pass, since `LoopRunner._over` reads the
        same three things it wrote when it stopped. So a restart is exactly the act of
        forgetting them: the stop flag the operator (or a shutdown) set, and the run of
        failures that tripped `max_fails`. The QUEUE, the limit and the tally of what
        has been made are deliberately untouched — a resumed loop carries on through its
        remaining topics rather than starting the series over, and a loop that reached
        its limit stays finished until the limit is raised."""
        def change(data: dict) -> None:
            data["stop"] = False
            data["fails"] = 0
            data["status"] = "queued"
            data["note"] = ""

        return self._update(change)

    def write_control(self, **fields) -> LoopPlan:
        """Store an operator's edit. Unknown and `None` values are dropped rather than
        refused: this is called from a CLI, an HTTP body and a text prompt, and a loop
        must not stop being steerable because one of them sent a spare key."""
        vals = {k: v for k, v in fields.items() if k in CONTROL and v is not None}
        if "topics" in vals:
            vals["topics"] = [i.model_dump(mode="json") for i in as_items(vals["topics"])]
        if "breakpoints" in vals:
            vals["breakpoints"] = [str(b) for b in vals["breakpoints"]]
        if not vals:
            return self.read()

        def change(data: dict) -> None:
            data.update(vals)
            # the live list and the template's own must never disagree: the loop reads
            # one of them (`params_for`) and everything that SHOWS the settings reads
            # the other, and a breakpoint that is on in one and off in the other is a
            # loop that stops where the operator was told it would not
            if "breakpoints" in vals and isinstance(data.get("params"), dict):
                data["params"]["breakpoints"] = list(vals["breakpoints"])

        return self._update(change)

    def write_params(self, params: RunParams) -> LoopPlan:
        """Replace the settings every next video is built from.

        The live breakpoint list is written with them: the form that sends a whole
        template carries breakpoints too, and two lists that both claim to say which
        stages stop is one list too many (`params_for` reads this one)."""

        def change(data: dict) -> None:
            current = RunParams.model_validate(data["params"])
            fresh = retune(current, params)
            data["params"] = fresh.model_dump(mode="json")
            data["breakpoints"] = list(fresh.breakpoints)

        return self._update(change)

    def set(self, edits: dict[str, str], store=None) -> LoopPlan:
        """Fold `key=value` edits into the settings, against the settings as they are at
        this moment. Raises `ValueError` on a name or a value the settings do not have,
        and then nothing is written."""

        def change(data: dict) -> None:
            current = RunParams.model_validate(data["params"])
            fresh = apply_settings(current, edits, store)
            data["params"] = fresh.model_dump(mode="json")
            data["breakpoints"] = list(fresh.breakpoints)

        return self._update(change)

    # -- the queue ---------------------------------------------------------
    #
    # Every one of these is a read-modify-write of the whole list against the file as it
    # stands, and every one of them addresses entries by ID rather than by position. Both
    # follow from the same fact: the runner takes entries off the front while the
    # operator edits the middle, so a position typed a second ago may already mean a
    # different video, and a list read a second ago may already be one shorter.

    @staticmethod
    def _queue(data: dict) -> list[QueueItem]:
        return as_items(data.get("topics") or [])

    @staticmethod
    def _store(data: dict, items: list[QueueItem]) -> None:
        data["topics"] = [i.model_dump(mode="json") for i in items]

    def add_topics(self, topics: Iterable, at: int | None = None) -> LoopPlan:
        """Put entries in the queue: at the end, or at `at` when somewhere else is meant.

        Takes bare strings as happily as entries — a topic typed at a terminal, a chat
        or the browser's one-line box is a topic and nothing more, and having to build an
        object around it at three call sites would be three ways to forget a field. It is
        also the one door where a blank is a slip rather than an instruction: pressing
        Enter on an empty box must queue nothing at all."""
        fresh = [i for i in as_items(topics) if not i.empty]

        def change(data: dict) -> None:
            queue = self._queue(data)
            cut = len(queue) if at is None else max(0, min(int(at), len(queue)))
            self._store(data, queue[:cut] + fresh + queue[cut:])

        return self._update(change)

    def set_queue(self, items: Iterable) -> LoopPlan:
        """Replace the queue with this list — which is what reordering, retyping and
        removing all are, once entries are addressed by id: the browser sends the list it
        is showing, in the order it is showing it.

        Overrides are validated against the settings on disk, so a queue carrying a value
        the settings cannot hold is refused whole and nothing is written."""

        def change(data: dict) -> None:
            base = RunParams.model_validate(data["params"])
            fresh = as_items(items)
            for it in fresh:
                it.over = clean_overrides(base, it.over)
            self._store(data, fresh)

        return self._update(change)

    def patch_queue(self, ids: Iterable[str], sets: dict | None = None,
                    clear: Iterable[str] = ()) -> LoopPlan:
        """Change ONE thing about several queued videos at once, leaving everything else
        about each of them alone.

        This is the whole of bulk editing, and the shape is deliberate. A form filled in
        for many entries can only say what all of them are to become, so it flattens the
        differences between them — the operator who wanted six of ten videos two minutes
        long would also, silently, have given all six the same voice. A field and a value
        cannot do that: it touches the field it names and no other."""
        want = [str(i) for i in ids]
        gone = [ALIASES.get(str(c), str(c)) for c in clear]

        def change(data: dict) -> None:
            base = RunParams.model_validate(data["params"])
            edits = clean_overrides(base, sets or {})
            for name in gone:
                if name not in overridable():
                    raise ValueError(f"`{name}` is not a per-video setting")
            queue = self._queue(data)
            for it in queue:
                if it.id not in want:
                    continue
                it.over = {k: v for k, v in {**it.over, **edits}.items() if k not in gone}
            self._store(data, queue)

        return self._update(change)

    def move(self, item_id: str, to: int) -> LoopPlan:
        """Put one queued video at position `to` (0-based), pulling it out of where it
        was. Out-of-range lands it at whichever end it was reaching for."""

        def change(data: dict) -> None:
            queue = self._queue(data)
            at = next((n for n, i in enumerate(queue) if i.id == item_id), None)
            if at is None:
                return
            it = queue.pop(at)
            queue.insert(max(0, min(int(to), len(queue))), it)
            self._store(data, queue)

        return self._update(change)

    def drop(self, ids: Iterable[str]) -> LoopPlan:
        want = {str(i) for i in ids}

        def change(data: dict) -> None:
            self._store(data, [i for i in self._queue(data) if i.id not in want])

        return self._update(change)

    def take_next(self) -> QueueItem | None:
        """Take the next queued video, or None when the queue is empty.

        The runner's one write into the control half, and it has to be one: an entry that
        is used but left on the queue is a video that gets made twice."""
        taken: list[QueueItem] = []

        def change(data: dict) -> None:
            queue = self._queue(data)
            if not queue:
                return
            taken.append(queue[0])
            self._store(data, queue[1:])

        self._update(change)
        return taken[0] if taken else None


class LoopRunner:
    """Drives a loop: decide, launch, record, decide again.

    It does not know HOW a video is made. `launch(params, n) -> LaunchResult` is handed
    in — the terminal runs the orchestrator on the spot, the web server puts an ordinary
    run into its pool and waits for it — so there is one loop policy and two ways of
    obeying it.

    `ask` is the terminal's answer to "the topic is yours and the queue is empty": rather
    than poll a file the operator cannot see, it asks them on their own screen. It
    returns the topic, "" to let the model take this one, or None when it changed the
    plan instead (switched the source, set a limit, stopped) — after which the loop
    simply decides again.

    `propose` is handed in for the same reason `launch` is: thinking of topics needs the
    config library and an LLM, and neither belongs in a plan file's policy. It is
    `(plan, n) -> [topic, ...]`, and it is what fills the queue ahead (see `_stock`).
    """

    def __init__(self, file: LoopFile, launch: Callable[[RunParams, int], LaunchResult],
                 on_event: Callable[[str, str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 ask: Callable[[LoopPlan], str | None] | None = None,
                 propose: Callable[[LoopPlan, int], list[str]] | None = None):
        self.file = file
        self.launch = launch
        self.on_event = on_event or (lambda *a: None)
        self.should_stop = should_stop or (lambda: False)
        self.ask = ask
        self.propose = propose
        self._last = ("", "")  # last event said, so a poll does not repeat itself
        self._retry_at = 0.0   # when the model may be asked for topics again

    # -- the loop ----------------------------------------------------------

    def run(self) -> LoopPlan:
        self._say("running", self.file.read().title)
        while True:
            plan = self.file.read()
            over = self._over(plan)
            if over:
                return self._end(plan, *over)
            if not self._released(plan):
                continue  # held on a parked run; the plan is read again
            plan = self._stock(plan)
            item = self._next(plan)
            if item is None:
                continue  # waiting for one, or the plan changed under us
            self._iteration(item)

    def _over(self, plan: LoopPlan) -> tuple[str, str] | None:
        """Whether this is the end, and why."""
        if plan.stop or self.should_stop():
            return ("stopped", "asked to stop")
        if plan.limit and plan.started >= plan.limit:
            return ("done", f"{plan.made}/{plan.started} made, limit {plan.limit}")
        if plan.fails >= max(1, plan.max_fails):
            return ("failed", f"{plan.fails} iterations failed in a row")
        return None

    def _released(self, plan: LoopPlan) -> bool:
        """Whether the previous iteration lets the next one start.

        A parked run is a question addressed to the operator, and the answer is given in
        the run itself — reviewed in the browser, gathered in the terminal — so this
        watches the run's own checkpoint rather than anything the loop keeps. Which also
        means the hold ends by itself the moment the run walks on."""
        last = plan.iterations[-1] if plan.iterations else None
        if last is None or plan.on_park == "go_on" or not last.run_dir:
            return True
        state = outcome(Path(last.run_dir))
        if not state:
            # no readable checkpoint: the folder was thrown away, or never got far
            # enough to write one. Either way there is nothing left to wait for, and a
            # loop holding on a run that no longer exists holds forever.
            return True
        if state != last.status:  # it moved while we waited — the log is stale
            last.status = state
            self.file.write_log(plan)
        if state not in PARKED:
            return True
        self._say("held", f"#{last.n} · {state} · {Path(last.run_dir).name}")
        time.sleep(POLL_S)
        return False

    def _stock(self, plan: LoopPlan) -> LoopPlan:
        """Keep `ahead` topics waiting in the queue, when the model is the one picking.

        Asked for BEFORE the video that needs them, which is the entire point: a topic
        invented at the moment it is used is a topic nobody ever sees, and one waiting in
        the queue can be read, rewritten, reordered or thrown out first. Everything here
        is best-effort — a model that will not answer costs the lookahead and nothing
        else, and the loop goes on to invent this one at the writing stage as it always
        did."""
        if self.propose is None or plan.source != "ai" or plan.ahead <= 0:
            return plan
        want = plan.ahead - len(plan.topics)
        if want <= 0 or time.monotonic() < self._retry_at:
            return plan
        self._say("running", "js.loop.thinking")
        try:
            fresh = [str(t).strip() for t in self.propose(plan, want)]
        except Exception as e:
            self._retry_at = time.monotonic() + STOCK_RETRY_S
            self.on_event("waiting", f"topics: {type(e).__name__}: {e}")
            return plan
        fresh = [t for t in fresh if t]
        if not fresh:
            self._retry_at = time.monotonic() + STOCK_RETRY_S
            return plan
        return self.file.add_topics([QueueItem(topic=t, by="ai") for t in fresh])

    def _next(self, plan: LoopPlan) -> QueueItem | None:
        """The next video: from the queue, from the operator, or an entry with no topic
        at all (which is what tells the writer to invent one). None means "decide
        again"."""
        queued = self.file.take_next()
        if queued is not None:
            return queued
        if plan.source == "ai":
            return QueueItem()
        if self.ask is not None:
            said = self.ask(plan)
            return None if said is None else QueueItem(topic=said)
        # a label key rather than a sentence: this note is fixed, it is the one an
        # operator sits looking at, and both frontends translate what they are given
        # (see labels.t, which is the identity for anything that is not a key)
        self._say("waiting", "js.loop.needtopic")
        time.sleep(POLL_S)
        return None

    def _iteration(self, item: QueueItem) -> None:
        plan = self.file.read()
        n = plan.started + 1
        topic = item.topic.strip()
        plan.iterations.append(
            Iteration(n=n, topic=topic, source=item.by if topic else "ai",
                      over=dict(item.over), at=_now()))
        plan.status, plan.note = "running", topic or "the model picks the topic"
        self.file.write_log(plan)
        self._say("video", f"#{n} · {topic or 'the model picks the topic'}")

        try:
            res = self.launch(plan.params_for(item), n)
        except Exception as e:  # a broken iteration is a state, not the end of the loop
            res = LaunchResult(None, "failed", f"{type(e).__name__}: {e}")

        plan = self.file.read()  # the control half may have changed while it ran
        for rec in plan.iterations:
            if rec.n == n:
                rec.run_dir, rec.status = str(res.run_dir or ""), res.status
        plan.fails = plan.fails + 1 if res.status == "failed" else 0
        plan.note = res.message
        self.file.write_log(plan)
        self._say("video", f"#{n} {res.status} {res.message or res.run_dir or ''}".strip())

    # -- reporting ---------------------------------------------------------

    def _end(self, plan: LoopPlan, status: str, note: str) -> LoopPlan:
        plan.status, plan.note = status, note
        self.file.write_log(plan)
        self.on_event(status, note)
        return plan

    def _say(self, state: str, message: str) -> None:
        """Report a state, once. A held or waiting loop asks the same question every two
        seconds and saying so every two seconds is how a log stops being readable."""
        if (state, message) == self._last:
            return
        self._last = (state, message)
        if state in LIVE:
            plan = self.file.read()
            if plan.status != state or plan.note != message:
                plan.status, plan.note = state, message
                self.file.write_log(plan)
        self.on_event(state, message)


# -- launchers --------------------------------------------------------------


def direct_launcher(store, on_event=None, on_progress=None, should_stop=None):
    """Make an iteration the way the terminal does: build the context, run the
    orchestrator here, and report through the same event stream a single run uses."""
    from .context import AppContext
    from .orchestrator import Orchestrator

    def launch(params: RunParams, n: int) -> LaunchResult:
        ctx = AppContext(store=store, params=params, on_progress=on_progress)
        orch = Orchestrator(ctx, on_event=on_event, should_stop=should_stop)
        orch.run()
        state = outcome(orch.run_dir) if orch.run_dir else "failed"
        return LaunchResult(orch.run_dir, state or "done")

    return launch


# -- finding loops ----------------------------------------------------------


def loop_dir_name(params: RunParams) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"loop_{stamp}_{params.content_type or params.mode}_{params.lang}"


def all_loops(output: Path, limit: int = 40) -> list[Path]:
    """Loop folders under the output dir, newest first."""
    if not Path(output).is_dir():
        return []
    plans = sorted(Path(output).glob(f"*/{PLAN_NAME}"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.parent for p in plans[:limit]]


def latest_loop(output: Path) -> Path | None:
    """The loop a bare `slopgen loop ...` means: the one most recently written to.

    Not the newest by name — a loop started this morning and steered all day is the one
    being steered, and a loop folder's own file moves whenever anything happens to it."""
    found = all_loops(output, limit=1)
    return found[0] if found else None
