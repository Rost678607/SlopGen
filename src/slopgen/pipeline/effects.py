"""The effects track: what is pointed at, and when.

The picture track and the narration deliberately run past each other (see
:mod:`.framebase`) — that asynchrony is most of what makes a wall of stills read as
edited footage. An effect is the exception, and it is the exception on purpose: an
arrow, a circle or a sting lands on ONE word, the word it is about, or it lands
wrong. So everything here is anchored to a word the way a cut is, and for the same
reason — a re-voiced line moves every second after it, and the word does not move.

Three things live in this module and they are kept apart the way the frame base keeps
its own two halves apart:

**What may fire where** (:func:`menu`) is a question about the BASE. A card carries
the effects somebody hung on it — "the circle around the cap", aimed at a region of
that one picture — and the effects base carries the ones that need no picture at all,
a flash or a sting, which are offered on every stretch. Nothing is invented here: an
effect nobody prepared can never be chosen.

**When it fires** (:func:`plan`) is a question about MEANING, so a model answers it:
given the picture that is up, what that picture can do, and the words being spoken,
it names a word. What it may NOT decide is rhythm — how often an effect may go off,
and how soon the same one may come back — which is :func:`rails`, and which knows
nothing about meaning. Exactly the split `stages.picture` makes between the matcher
and `framebase.Picker`, for exactly the reason.

**Where it is drawn** (:func:`render`) is a question about GEOMETRY, and the only
part that is hard. A region-anchored effect is pinned to something IN the picture,
and the picture is being cropped and travelled over — so the circle around the cap
has to converge with the zoom exactly as the cap does, or it is a circle around
nothing. The crop window is known here as arithmetic (`KenBurns.points`), so the
effect's path across the FRAME is sampled out of it and handed to ffmpeg as a path,
rather than being drawn in screen coordinates and left to drift.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from ..config.models import (
    CardEffect,
    EffectKey,
    EffectSpec,
    FrameCard,
    KenBurns,
    Point,
    Rect,
)
from ..media.ffmpeg import EffectDraw as Draw
from . import framebase
from .job import EffectCue, FrameShot, VideoJob

log = logging.getLogger(__name__)

# -- rhythm ----------------------------------------------------------------
# None of this is about what an effect MEANS; it is about how often the video is
# allowed to poke the viewer. The failure being prevented is specific and was
# predictable from the first prompt written: told it may fire effects, a model fires
# one per sentence, and a video with an arrow on every noun is a video nobody watches
# to the end.
MIN_GAP_S = 2.0  # two effects closer together than this are one interruption
COOLDOWN_S = 12.0  # the same effect again inside this reads as a tic, not an accent
PER_MINUTE = 8.0  # an upper bound on the whole track, whatever the model asked for

# How finely a travelling effect's path is sampled, in seconds. The crop move is
# piecewise linear but the mapping from the picture to the frame is a RATIO of two
# linear functions, so it is not — sampling is what keeps that honest. At 0.08s a
# one-second effect costs a dozen points, which is nothing next to what the same
# second costs anywhere else.
SAMPLE_S = 0.08
# Samples this close in every value are the same sample. A held shot yields an
# identical point every time it is asked, and an expression repeating one number
# thirty times is thirty ways to write a constant.
SAME = 1e-4

# Where a screen-anchored effect sits, as fractions of the frame. The margins are
# generous because the two edges of a vertical video are where the platform draws its
# own furniture — the caption at the bottom, the account at the top.
PLACES: dict[str, tuple[float, float]] = {
    "center": (0.5, 0.5),
    "top": (0.5, 0.22),
    "bottom": (0.5, 0.76),
    "left": (0.24, 0.5),
    "right": (0.76, 0.5),
    "top_left": (0.24, 0.22),
    "top_right": (0.76, 0.22),
    "bottom_left": (0.24, 0.76),
    "bottom_right": (0.76, 0.76),
}


# --------------------------------------------------------------------------
# what is available
# --------------------------------------------------------------------------


@dataclass
class Option:
    """One effect a stretch could fire, as the model is offered it.

    `key` is what the model answers with and what the operator sees: the label
    somebody gave this effect ON THIS CARD ("круг вокруг шапки"), or the effect's own
    name where it is not hung on anything. One namespace, because two would mean a
    reply naming "arrow" being ambiguous between the base's arrow and the three
    different things three cards call by that name."""

    key: str
    spec: EffectSpec
    hung: CardEffect | None = None  # the card's own entry, where it came from one

    @property
    def source(self) -> str:
        """Where this option comes from, which is the one thing the operator has to be
        able to tell at a glance:

        * `card` — the picture that is up has it ready, aimed at something on it. This
          is the whole of what the automatic pass may fire.
        * `frame` — it needs no picture (a flash, a sting) and goes wherever the effect
          says it goes.
        * `free` — it sits on a picture, and nobody has hung it on THIS one, so firing
          it drops it in the middle of the frame for somebody to drag where they meant.

        The pass is offered the first two and the room all three: a model has no hands
        to place anything with, and an operator does."""
        if self.hung is not None:
            return "card"
        return "frame" if self.spec.anchor != "point" else "free"

    @property
    def placed(self) -> bool:
        """Whether the card actually says WHERE this one goes. A hook with no points
        is a perfectly good entry — it falls back to the effect's own place in the
        frame — but the editor shows the difference, because a arrow nobody has
        dropped anywhere is usually an arrow somebody forgot to drop."""
        return bool(self.hung and self.hung.points)

    @property
    def width(self) -> float:
        """The size to draw it at: the card's override where there is one, else the
        effect's own. Zero on a card entry means "whatever the effect says", which is
        what an operator who never touched the field meant."""
        if self.hung and self.hung.width > 0:
            return self.hung.width
        return self.spec.width


def usable(specs: dict[str, EffectSpec]) -> dict[str, EffectSpec]:
    return {n: s for n, s in specs.items() if s.usable}


def menu(card: FrameCard | None, specs: dict[str, EffectSpec],
         free: bool = False) -> list[Option]:
    """Everything one stretch could fire, in the order it should be offered in: what
    its card has READY first, then what needs no picture, then — only when asked —
    the rest of the base, to be placed by hand.

    A screen effect asks nothing of what is on screen — it is a flash, a border, a
    sound — so it is offered everywhere, including over a stretch with no picture at
    all. An effect that sits on a picture is coordinates on one file, so as an offer it
    belongs to that file's card; `free` is the room saying it has an operator who can
    drop one anywhere, which the automatic pass has not.

    A card entry naming an effect the base no longer has is skipped rather than
    raising: a base is a folder people tidy, and a stale line in a card's TOML must
    not take a run down."""
    out: list[Option] = []
    for hung in (card.effects if card else []):
        spec = specs.get(hung.effect)
        if spec is None or not spec.usable:
            continue
        out.append(Option(key=(hung.label.strip() or spec.name), spec=spec, hung=hung))
    taken = {o.key for o in out}
    for spec in specs.values():
        if spec.usable and spec.anchor == "screen" and spec.name not in taken:
            out.append(Option(key=spec.name, spec=spec))
    if free:
        offered = {o.spec.name for o in out}
        for spec in specs.values():
            if spec.usable and spec.name not in offered and spec.name not in taken:
                out.append(Option(key=spec.name, spec=spec))
    return out


def hook_of(card: FrameCard | None, spec: EffectSpec | None, label: str) -> CardEffect | None:
    """Which of a card's ready effects a cue means, by the label it recorded.

    The label is the entry's NAME — it is what the model was offered and what the
    operator pressed — so it is what a cue records and what is matched back on here.
    Where the label is empty (an entry nobody titled) the effect's own name stands in,
    exactly as :func:`menu` spells it, so the two can never mean different things by
    the same string.

    Nothing is raised when it does not resolve: an entry the operator deleted since
    leaves a cue that falls back to the frame, which is a visible outcome and not a
    broken run."""
    if card is None or spec is None:
        return None
    want = (label or "").strip().casefold()
    for h in card.effects:
        if h.effect != spec.name:
            continue
        key = (h.label.strip() or spec.name).casefold()
        if key == want or not want:
            return h
    return None


def aim(hung: CardEffect | None, cue: EffectCue | None = None) -> float:
    """How far this firing is turned before its animation turns it any further: the
    card's aim plus whatever this one cue adds to it.

    They ADD rather than override, and that is the whole reason there are two. A card
    says which way the arrow points on THAT picture — a fact about the picture, true in
    every video. A cue says which way it points this once, which is a fact about one
    line. Made to override, the second would silently throw the first away the moment
    anybody nudged it."""
    return (hung.turn if hung else 0.0) + (cue.turn if cue else 0.0)


def cover_for(spec: EffectSpec, aspect: float) -> float:
    """How wide a full-frame effect has to be DRAWN, in frame widths, to cover the
    frame — 1.0 for anything portrait enough, more for a landscape picture over a
    vertical video.

    It is arithmetic on the file's own shape and nothing else: to cover, the drawn
    picture must be at least as wide as the frame and at least as tall, and a
    landscape one hits the second bound long after the first. A file that cannot be
    probed covers by width alone, which is what it did before there was a full-frame
    anchor at all."""
    if spec.anchor != "full" or spec.path is None or not spec.path.is_file():
        return 1.0
    from ..media.ffmpeg import video_dims

    try:
        w, h = video_dims(spec.path)
    except Exception:
        return 1.0
    if not w or not h:
        return 1.0
    return max(1.0, (w / h) / max(aspect, 1e-6))


def anything(cards: list[FrameCard], specs: dict[str, EffectSpec]) -> bool:
    """Whether this world has any effect to fire at all — asked before the model is,
    because a run whose base is empty must not pay for a call that can only answer
    "nothing"."""
    live = usable(specs)
    if any(s.anchor == "screen" and s.description.strip() for s in live.values()):
        return True
    return any(h.effect in live for c in cards for h in c.effects)


# --------------------------------------------------------------------------
# when it fires
# --------------------------------------------------------------------------


PLAN_SYSTEM = (
    "You are the effects editor of a short vertical video narrated from inside a fictional "
    "world. The picture is a still from the world's own collection, held and slowly travelled "
    "over. Your job is the layer above it: the arrows, the circles and the stings that land on "
    "ONE WORD and are gone.\n"
    "YOU GET the video's stretches — which picture is up, what that picture has READY (each "
    "with a line saying when it is the right one), and the words being spoken, numbered.\n"
    "FOR EACH EFFECT YOU FIRE, name the stretch, the effect exactly as it is spelled in that "
    "stretch's list, and the NUMBER of the word it lands on.\n"
    "THE WORD IS THE WHOLE POINT. An effect exists to make the listener look where the "
    "narration already is: the circle goes off ON the word that names the thing it circles, "
    "not on the word before it and not on the sentence around it. If the thing an effect "
    "points at is never named in that stretch, the effect does not belong there.\n"
    "FIRE FEW. Most stretches get nothing at all. A video with something going off every "
    "sentence is a video nobody finishes — three or four accents in a minute is a lot, and an "
    "effect that would merely be nice is an effect that costs the next one its force. Never "
    "fire two on one stretch.\n"
    "An effect you were not offered on a stretch cannot be fired there, whatever it would have "
    "been good for. Copy the spelling exactly.\n"
    'Respond with JSON only: {"cues": [{"n": <stretch>, "effect": "...", "word": <number>}, ...]}. '
    'An empty list is a perfectly good answer.'
)


def _words_block(words: list[tuple[int, str]]) -> str:
    return " ".join(f"{n}·{w}" for n, w in words) or "—"


def _shots_block(job: VideoJob, offers: dict[int, list[Option]],
                 words: dict[int, list[tuple[int, str]]]) -> str:
    out: list[str] = []
    for i, s in enumerate(job.frame_shots):
        opts = offers.get(i) or []
        if not opts:
            continue
        lines = [f"{i + 1}. [{s.start:.1f}s–{s.start + s.duration:.1f}s]"
                 f" picture: {s.card or '—'}"]
        lines.append("   can fire:")
        for o in opts:
            note = (o.hung.label.strip() if o.hung else "") or o.spec.description.strip()
            said = o.spec.description.strip()
            tail = f" — {said}" if said and said != note else ""
            lines.append(f"     · {o.key}{tail}")
        lines.append(f"   heard: {_words_block(words.get(i, []))}")
        out.append("\n".join(lines))
    return "\n".join(out)


def numbered(job: VideoJob) -> tuple[dict[int, list[tuple[int, str]]], dict[int, tuple[int, int, float, str]]]:
    """The video's words, numbered once end to end and grouped by the stretch they
    are heard over.

    One numbering for the whole video rather than one per stretch: the model answers
    with a number and that number has to mean exactly one word, whatever else it got
    wrong about which stretch it belongs to. The second half of the pair is how a
    number is read back — the word's scene, its index in that scene, when it is
    spoken and what it says."""
    cues, _regions, _total = framebase.timeline(job.scenes)
    scenes = job.scenes
    by_shot: dict[int, list[tuple[int, str]]] = {}
    where: dict[int, tuple[int, int, float, str]] = {}
    for n, c in enumerate(cues, start=1):
        scene = scenes[c.scene] if 0 <= c.scene < len(scenes) else None
        text = scene.words[c.word].text if scene and c.word < len(scene.words) else ""
        where[n] = (c.scene, c.word, c.at, text)
        for i, s in enumerate(job.frame_shots):
            if s.start <= c.at < s.start + s.duration:
                by_shot.setdefault(i, []).append((n, text))
                break
    return by_shot, where


def plan(job: VideoJob, ctx, cards: list[FrameCard],
         specs: dict[str, EffectSpec]) -> list[EffectCue]:
    """Ask a model what to fire and where, and keep what the rhythm allows.

    Cues the operator placed by hand are never re-decided and never dropped: they go
    in as history, exactly as a pinned shot does, so the automatic ones fall around
    them instead of colliding with them."""
    live = usable(specs)
    by_name = {c.name: c for c in cards}
    offers = {i: menu(by_name.get(s.card), live) for i, s in enumerate(job.frame_shots)}
    if not any(offers.values()):
        return [q for q in job.effect_cues if q.pinned]

    words, where = numbered(job)
    block = _shots_block(job, offers, words)
    if not block.strip():
        return [q for q in job.effect_cues if q.pinned]
    data = ctx.llm.complete_json("frame_effects", PLAN_SYSTEM, f"The video:\n{block}")

    fresh: list[EffectCue] = []
    for row in data.get("cues", []):
        if not isinstance(row, dict):
            continue
        try:
            i, n = int(row.get("n", 0)) - 1, int(row.get("word", 0))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(job.frame_shots) or n not in where:
            continue
        key = str(row.get("effect", "")).strip()
        opt = next((o for o in offers.get(i, []) if o.key.casefold() == key.casefold()), None)
        if opt is None:
            log.info("effects: %r is not on offer over stretch %d — dropped", key, i + 1)
            continue
        scene, word, at, text = where[n]
        shot = job.frame_shots[i]
        fresh.append(EffectCue(
            effect=opt.spec.name, anchor_scene=scene, anchor_word=word,
            start=at, duration=length(opt.spec, at, shot, 0),
            card=shot.card if opt.hung is not None else "",
            hook=opt.key, word=text,
        ))
    kept = rails([q for q in job.effect_cues if q.pinned], fresh, job.total_duration)
    log.info("effects: %d fired out of %d the model asked for", len(kept), len(fresh))
    return kept


def length(spec: EffectSpec, at: float, shot: FrameShot | None, loops: int = 0) -> float:
    """How long one firing of this effect lasts.

    Two answers and the spec says which: a clip whose own end is the effect's end is
    measured off the file, everything else runs for as long as its animation says —
    the way in, the repeating middle however many times, the way out
    (:meth:`EffectSpec.span`). A clip that cannot be probed falls back to the animation
    rather than failing: an effect is not worth a run.

    It is then cut short by the picture: an effect pinned to something IN the card
    cannot outlive the card, because the moment the shot changes it is pointing at a
    picture that no longer has the thing in it."""
    from ..media.ffmpeg import duration_of

    want = spec.span(loops)
    src = spec.path if spec.path and spec.path.is_file() else spec.sound_path
    if spec.fill == "clip" and src is not None:
        try:
            probed = duration_of(src)
        except Exception:
            probed = 0.0
        if probed > 0.05:
            want = probed
    if spec.anchor == "region" and shot is not None:
        want = min(want, max(shot.start + shot.duration - at, 0.1))
    return want


def rails(pinned: list[EffectCue], fresh: list[EffectCue], total: float) -> list[EffectCue]:
    """The rhythm rules, over what the model asked for.

    Walked in time order, so an effect is judged against what the viewer has already
    been shown rather than against the list's order — and the operator's own cues are
    in that history from the start, which is what makes a hand-placed accent push the
    automatic ones away from it instead of the other way round."""
    cap = max(int(PER_MINUTE * max(total, 1.0) / 60.0), 1)
    out = sorted(pinned, key=lambda q: q.start)
    for q in sorted(fresh, key=lambda q: q.start):
        if len(out) >= cap + len(pinned):
            break
        if any(abs(q.start - p.start) < MIN_GAP_S for p in out):
            continue
        if any(p.effect == q.effect and abs(q.start - p.start) < COOLDOWN_S for p in out):
            continue
        out.append(q)
        out.sort(key=lambda x: x.start)
    return out


def reanchor(job: VideoJob) -> None:
    """Put every cue back where its word is now.

    The same operation :func:`framebase.reanchor` does for the cuts, and it has to
    happen at the same moments: re-voicing a line, rewriting one, adding one. A cue
    whose word is gone keeps the seconds it had, because the alternative is dropping
    something the operator may have placed themselves."""
    cues, _regions, total = framebase.timeline(job.scenes)
    where = {(c.scene, c.word): c.at for c in cues}
    for q in job.effect_cues:
        at = where.get((q.anchor_scene, q.anchor_word))
        if at is not None:
            # …plus however far the operator dragged it off that word (`EffectCue.drift`).
            # Zero for everything the passes place, which is the ordinary case: an
            # automatic cue lands ON its word or it lands wrong.
            q.start = at + q.drift
        # …and the word it SAYS it is on is read off the anchor too, every time. It is
        # shown to the operator and used for nothing else, which is exactly why it goes
        # stale unnoticed: a line rewritten under a cue, or a cue pushed onto the next
        # line by a deletion, left it naming a word that is no longer there.
        scene = job.scenes[q.anchor_scene] if 0 <= q.anchor_scene < len(job.scenes) else None
        if scene is not None and 0 <= q.anchor_word < len(scene.words):
            q.word = scene.words[q.anchor_word].text
        q.start = min(max(q.start, 0.0), max(total, 0.0))
        q.duration = max(min(q.duration, total - q.start), 0.05)
    job.effect_cues.sort(key=lambda q: q.start)


def place(job: VideoJob, scene: int, word: int, opt: Option) -> int:
    """Fire one effect on one word, by hand. Returns its index on the track.

    The cue is pinned by the act of placing it, for the reason a cast shot is: the
    effects pass re-decides everything it did not pin, and an unpinned hand-made cue
    would last exactly until the next press of `picture`."""
    cues, _regions, _total = framebase.timeline(job.scenes)
    cue = next((c for c in cues if c.scene == scene and c.word == word), None)
    if cue is None:
        raise ValueError("there is no such word")
    shot = shot_at(job.frame_shots, cue.at)
    needs_picture = opt.hung is not None or opt.spec.anchor == "point"
    if needs_picture and (shot is None or not shot.card):
        raise ValueError("this effect needs a picture to sit on, and there is none here")
    # one nobody hung on this card is dropped in the MIDDLE OF THE FRAME rather than in
    # the middle of the card: the two are the same thing only when the camera is showing
    # the whole picture, and an effect that arrives somewhere off-screen reads as an
    # effect that did not arrive (see `EffectCue.points`)
    own: list[Point] = []
    if opt.hung is None and opt.spec.anchor == "point" and shot is not None:
        win = crop_at(shot.move, cue.at - shot.start)
        # …and as a BOX sized to the effect's own width AS SEEN, not as a bare point.
        # A point takes its size from the card, and the card is being cropped: dropped
        # onto a shot zoomed to a fifth of the picture, a ring asking for a third of
        # the frame would arrive one and a half screens wide. The box carries the size
        # with it (see :func:`spot`), so what lands is what the base says it is — and it
        # still travels with the picture afterwards, because the box is on the card.
        half = max(opt.spec.width, 0.01) * win.scale / 2
        clamp = lambda v: min(max(v, 0.0), 1.0)
        own = [Point(cx=clamp(win.cx - half), cy=clamp(win.cy - half)),
               Point(cx=clamp(win.cx + half), cy=clamp(win.cy + half))]
    text = ""
    if 0 <= scene < len(job.scenes) and word < len(job.scenes[scene].words):
        text = job.scenes[scene].words[word].text
    fresh = EffectCue(
        effect=opt.spec.name, anchor_scene=scene, anchor_word=word,
        start=cue.at, duration=length(opt.spec, cue.at, shot, 0),
        card=(shot.card if (shot and needs_picture) else ""),
        hook=opt.key, word=text, pinned=True, points=own,
    )
    job.effect_cues.append(fresh)
    job.effect_cues.sort(key=lambda q: q.start)
    return next(i for i, q in enumerate(job.effect_cues) if q is fresh)


# --------------------------------------------------------------------------
# where it is drawn
# --------------------------------------------------------------------------


def shot_at(shots: list[FrameShot], at: float) -> FrameShot | None:
    """Which still is up at this moment of the video."""
    for s in sorted(shots, key=lambda x: x.start):
        if s.start <= at < s.start + s.duration:
            return s
    return None


def crop_at(move: KenBurns | None, into: float) -> Rect:
    """The crop window at `into` seconds into a shot.

    The same reading `media.ffmpeg._ken_burns` renders and the same one the montage
    preview draws: a run of moments with straight travel between them, held at the
    first before it and at the last after it. Without a move the window is the whole
    picture, which is what a shot nobody gave a move to actually shows."""
    if move is None:
        return Rect()
    pts = [(at, r.clamped()) for at, r in move.points()]
    if into <= pts[0][0]:
        return pts[0][1]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if into <= t1:
            span = max(t1 - t0, 1e-6)
            p = min(max((into - t0) / span, 0.0), 1.0)
            return Rect(cx=a.cx + (b.cx - a.cx) * p, cy=a.cy + (b.cy - a.cy) * p,
                        scale=a.scale + (b.scale - a.scale) * p)
    return pts[-1][1]


def _key_at(keys: list[EffectKey], into: float) -> EffectKey:
    """The animation's state at `into` seconds into the effect — interpolated between
    the moments it was given, held at the ends."""
    if not keys:
        return EffectKey()
    if into <= keys[0].at:
        return keys[0]
    for a, b in zip(keys, keys[1:]):
        if into <= b.at:
            span = max(b.at - a.at, 1e-6)
            p = min(max((into - a.at) / span, 0.0), 1.0)
            return EffectKey(
                at=into,
                scale=a.scale + (b.scale - a.scale) * p,
                dx=a.dx + (b.dx - a.dx) * p,
                dy=a.dy + (b.dy - a.dy) * p,
                alpha=a.alpha + (b.alpha - a.alpha) * p,
                rotate=a.rotate + (b.rotate - a.rotate) * p,
            )
    return keys[-1]


def _dedupe(path: list[tuple[float, float, float, float, float]]):
    """Drop a sample that says exactly what the one before it did, keeping the last.

    A held shot answers the same four numbers however often it is asked, and an
    expression built out of forty copies of one number is forty ways to spell a
    constant — which costs nothing at render time and everything when somebody has to
    read the filtergraph that came out."""
    out: list[tuple[float, float, float, float, float, float]] = []
    for p in path:
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            same = all(abs(b[i] - a[i]) < SAME and abs(p[i] - b[i]) < SAME
                       for i in (1, 2, 3, 4, 5))
            if same:
                out[-1] = p
                continue
        out.append(p)
    if len(out) == 2 and all(abs(out[1][i] - out[0][i]) < SAME for i in (1, 2, 3, 4, 5)):
        out = out[:1]
    return out


def spot(points: list, width: float,
         aspect: float = 9 / 16) -> tuple[float, float, float] | None:
    """Where on the CARD a hung effect sits and how wide it is, before any camera:
    `(cx, cy, w)` in fractions of the fitted picture, or None where it is not on the
    picture at all.

    The whole grammar of :attr:`CardEffect.points` is here and it is three lines long —
    and it is the same grammar a CUE's own points use, because a firing nudged in the
    montage room is the same kind of answer said for one video (`EffectCue.points`).
    No points and it belongs to the frame, not to the card. One point and it sits
    there at the effect's own width (a fraction of the frame). Two or more and their
    bounding box is the placement — centre and size BOTH, with `width` saying nothing
    — which is exactly what dragging a box around something leaves behind, and why
    nothing here needs a marked crop region.

    A box that is taller than it is wide still yields a WIDTH, because that is what an
    effect is measured by everywhere else (`EffectSpec.width`); the height follows from
    the picture's own shape. The frame's aspect converts one into the other."""
    pts = list(points or [])
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0].cx, pts[0].cy, width
    xs = [min(max(p.cx, 0.0), 1.0) for p in pts]
    ys = [min(max(p.cy, 0.0), 1.0) for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # the box in frame-WIDTH units: its own width, or its height converted, whichever
    # is the bigger — so a tall box gets an effect big enough to span it
    span = max(max(xs) - min(xs), (max(ys) - min(ys)) * aspect)
    # and `width` says nothing here. The box IS the size: somebody dragged it around
    # the thing, and a multiplier on top of a direct manipulation is a second control
    # for one decision — wanting it wider is one drag, not a number in a field.
    return cx, cy, max(span, 0.01)


def draw_for(cue: EffectCue, spec: EffectSpec, card: FrameCard | None,
             hung: CardEffect | None, shots: list[FrameShot], width: float,
             aspect: float = 9 / 16, cover: float = 1.0, turn: float = 0.0) -> Draw | None:
    """One cue's whole geometry, sampled.

    Three placements and only the first one is hard. A cue hung on a POINT of the card
    is followed through the crop move: at each sample the window is computed and the
    point is mapped into the frame through it, so an arrow dropped on a doorway
    converges with the zoom exactly as the doorway does. A `screen` cue sits where it
    was told to in the frame and needs no sampling beyond its own animation. A `full`
    one covers the frame, and `cover` is how much wider than the frame its picture has
    to be drawn to do that, which only the render knows (see :func:`render`).

    A point cue ends early where the picture does. The coordinates are a fact about ONE
    card, so the moment the shot changes there is nothing left to point at — and an
    arrow left hanging over the next picture is worse than one that was never fired."""
    out = Draw(name=spec.name, start=cue.start, duration=max(cue.duration, 0.05),
               loop=(spec.fill == "hold"), volume=max(min(spec.volume, 2.0), 0.0),
               cover=(spec.anchor == "full"))
    out.asset = spec.path if (spec.path and spec.path.is_file()) else None
    out.sound = spec.sound_path if (spec.sound_path and spec.sound_path.is_file()) else None
    if out.asset is None and out.sound is None:
        return None

    keys = spec.moments()
    # the cue's own placement wins over the card's, which is what the room's drag
    # writes; with neither it falls back to the frame
    placed = list(cue.points) or list(hung.points if hung else [])
    on_card = spec.anchor == "point" and card is not None and spot(placed, width, aspect)
    if spec.anchor == "full":
        base, size0 = (0.5, 0.5), cover
    elif not on_card:
        base, size0 = PLACES.get(spec.place, (0.5, 0.5)), width
    else:
        base, size0 = (0.5, 0.5), width  # re-derived per sample from the crop window

    # the moments worth asking about: the ends, every key, and — where the picture
    # itself moves under it — a steady beat between them
    times = {0.0, out.duration}
    times |= {min(max(k.at, 0.0), out.duration) for k in keys}
    if on_card or len(keys) > 1 or spec.cycles:
        n = int(out.duration / SAMPLE_S)
        times |= {i * SAMPLE_S for i in range(n + 1)}
    if spec.cycles:
        # the separators land exactly on a sample only by luck, and a cycle's seam is
        # the one moment the animation jumps: sampled past it, the jump is drawn as a
        # slide from one end of the loop to the other
        cycle = spec.loop_to - spec.loop_from
        n = max(int(cue.loops or spec.loops), 1)
        for i in range(n + 1):
            seam = spec.loop_from + i * cycle
            if seam <= out.duration:
                times |= {max(seam - 1e-3, 0.0), min(seam + 1e-3, out.duration)}

    path: list[tuple[float, float, float, float, float, float]] = []
    for into in sorted(times):
        at = cue.start + into
        # through the animation's own clock, which is where a repeating middle is
        # folded back on itself (see `EffectSpec.clock`) — nothing below this line
        # knows that anything repeated
        k = _key_at(keys, spec.clock(into, cue.loops))
        if on_card:
            shot = shot_at(shots, at)
            if shot is None or shot.card != cue.card:
                out.duration = max(into, 0.05)  # the picture changed under it
                break
            win = crop_at(shot.move, at - shot.start)
            px, py, pw = on_card
            cx = (px - (win.cx - win.scale / 2)) / max(win.scale, 1e-6)
            cy = (py - (win.cy - win.scale / 2)) / max(win.scale, 1e-6)
            w = pw / max(win.scale, 1e-6)
        else:
            cx, cy = base
            w = size0
        # the offset is in the effect's OWN widths (see `EffectKey`), which is what
        # keeps an arrow's tip on its target while the camera converges on it. `cx` is
        # a fraction of the frame's width and so takes it directly; `cy` is a fraction
        # of its HEIGHT, and the same number of PIXELS down is that many widths times
        # the frame's aspect (0.5625 on a 1080x1920 video).
        size = max(w * k.scale, 0.01)
        # the animation TURNS and the card (or this one cue) AIMS: the two add, so an
        # arrow that swings in still swings whichever way it has been pointed
        spin = k.rotate + turn
        # …and the OFFSET turns with it. `dx`/`dy` are in the effect's own frame of
        # reference — its own widths and its own axes — which is the only reading that
        # keeps an arrow's tip on its target once the arrow has been aimed: the picture
        # points down and approaches from above, so pointed left it must approach from
        # the right. Measured in the video's axes instead, a turned arrow keeps flying
        # in from the top and lands beside the thing it is pointing at.
        rad = math.radians(spin)
        cos, sin = math.cos(rad), math.sin(rad)
        ox = k.dx * cos - k.dy * sin
        oy = k.dx * sin + k.dy * cos
        path.append((at, cx + ox * size, cy + oy * size * aspect, size,
                     max(min(k.alpha, 1.0), 0.0), spin))
    out.path = _dedupe([p for p in path if p[0] <= out.start + out.duration + 1e-6])
    return out if out.path else None


def options_at(job: VideoJob, at: float, cards: list[FrameCard],
               specs: dict[str, EffectSpec], free: bool = True) -> list[Option]:
    """What could be fired at this moment of the video.

    The same list the model is offered for that stretch — so the room and the pass
    never disagree about what a picture can do — plus, for the room, the rest of the
    base to drop in by hand (see :func:`menu`)."""
    shot = shot_at(job.frame_shots, at)
    card = next((c for c in cards if shot is not None and c.name == shot.card), None)
    return menu(card, usable(specs), free=free)


def ordered(job: VideoJob) -> list[EffectCue]:
    """The track's effects in the order they go off. Kept sorted on the job, and this
    is what says so — every index the screen sends back is an index into this, exactly
    as `montage._ordered` is for the cuts."""
    job.effect_cues.sort(key=lambda q: q.start)
    return job.effect_cues


def drop(job: VideoJob, i: int) -> None:
    """Take one effect off the track."""
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    del job.effect_cues[i]


def hold_for(job: VideoJob, i: int, seconds: float) -> None:
    """How long one firing stays up, set by hand.

    Clamped rather than refused, exactly as a crop move's travel is: a region-pinned
    effect cannot outlive the picture it is pinned to, and the picture's length is not
    the operator's to set from here — it is the distance to the next cut."""
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    _fit(job, cues[i], max(float(seconds), 0.1))


def nudge(job: VideoJob, i: int, at: float, cards: list[FrameCard] | None = None,
          specs: dict[str, EffectSpec] | None = None) -> None:
    """Move one firing along the clock, to wherever it was dropped.

    FREE, unlike a cut. A cut is a word and nothing else — the picture changes on a
    syllable or it changes wrong — but an accent is aimed at the speech rather than
    fastened to it: it leads a word by a fifth of a second, it covers the pause between
    two, it runs across three of them. Snapping that to word starts would make half the
    placements the operator can see unreachable.

    What it is NOT is unanchored. The dropped second is written down as the nearest word
    plus the remainder (:attr:`~.job.EffectCue.drift`), so a later re-voicing carries the
    firing with the speech it was aimed at instead of leaving it on a second that now
    belongs to another line — the same bargain :func:`reanchor` has always made, with
    room in it for an answer between two words.

    Placing it by hand PINS it, for the reason :func:`place` does: the effects pass
    re-decides everything it did not pin, and a dragged cue would otherwise last
    exactly until the next press of `picture`.

    `cards` and `specs` are the base, and they are here to answer one question: whether
    this firing is one the PICTURE carries or one dropped in over whatever happens to be
    there (:func:`hook_of`). The two may not be dragged the same distance, and the cue
    alone cannot tell them apart — it records a hook either way, and whether that hook
    resolves is a fact about the card.
    """
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    q = cues[i]
    marks, _regions, total = framebase.timeline(job.scenes)
    if not marks:
        raise ValueError("this video has no words to hang an effect near")
    # …and the far end is where the firing FITS, not where it starts: clamped to
    # `total` the drag would park a one-second effect on the last frame and `_fit` would
    # crush it to nothing, which a drag back would not undo. Against its own length it
    # simply comes to rest flush with the end of the video.
    room = max(total - max(q.duration, 0.05), 0.0)
    at = min(max(float(at), 0.0), room)
    # A firing the PICTURE carries — one of that card's own ready effects (`hook`) — may
    # move inside its still and nowhere else. Its coordinates are a fact about that one
    # card, so the moment the shot changes there is nothing left under it and the render
    # simply cuts it off (see :func:`draw_for`): a drag past the cut would not move the
    # effect, it would delete it and leave the block sitting where nothing happens. So it
    # is clamped to its host rather than refused — a drag that stops at the edge of the
    # picture says what the rule is without a sentence.
    by_name = {c.name: c for c in (cards or [])}
    hung = hook_of(by_name.get(q.card), (specs or {}).get(q.effect), q.hook)
    host = next((sh for sh in job.frame_shots
                 if hung is not None and sh.card == q.card
                 and sh.start - 1e-6 <= q.start < sh.start + sh.duration), None)
    if host is not None:
        at = min(max(at, host.start),
                 max(host.start + host.duration - max(q.duration, 0.05), host.start))
    near = min(marks, key=lambda c: abs(c.at - at))
    q.anchor_scene, q.anchor_word, q.drift = near.scene, near.word, round(at - near.at, 3)
    q.start = at
    scene = job.scenes[near.scene] if 0 <= near.scene < len(job.scenes) else None
    if scene is not None and 0 <= near.word < len(scene.words):
        q.word = scene.words[near.word].text
    q.pinned = True
    # An effect dropped in from the BASE by hand — one whose hook no card answers to —
    # is not that card's effect,
    # it merely sits on whatever picture is there — so dragged onto another one it is
    # re-hung, because the alternative is a firing the render draws for a twentieth of a
    # second (`draw_for` ends a point cue where its picture does). Its own placement goes
    # with the old card: those coordinates were measured on a picture that is no longer
    # under it, and keeping them would put the arrow somewhere nobody aimed. Back to the
    # middle of the frame, which is where one arrives before it is dragged.
    if q.card and hung is None:
        landed = shot_at(job.frame_shots, q.start)
        card = landed.card if landed is not None else ""
        if card != q.card:
            q.card, q.points = card, []
    _fit(job, q, q.duration)
    job.effect_cues.sort(key=lambda x: x.start)


def repeat(job: VideoJob, i: int, times: int, specs: dict[str, EffectSpec]) -> None:
    """How many times this firing's middle repeats.

    The LENGTH is not typed here, it follows: an effect that pulses three times is
    three pulses long, and a number saying otherwise beside it would be a second
    answer to one question. Which is also why this refuses an effect with nothing to
    repeat rather than quietly doing nothing — the control is not offered for those,
    so reaching it means something else is wrong."""
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    q = cues[i]
    spec = specs.get(q.effect)
    if spec is None or not spec.cycles:
        raise ValueError("this effect has no repeating part")
    q.loops = min(max(int(times), 1), 99)
    _fit(job, q, spec.span(q.loops))


def _fit(job: VideoJob, q: EffectCue, want: float) -> None:
    """Give a cue a length, cut to what the picture and the video allow."""
    if q.card:
        shot = shot_at(job.frame_shots, q.start)
        if shot is not None:
            want = min(want, max(shot.start + shot.duration - q.start, 0.1))
    q.duration = min(want, max(job.total_duration - q.start, 0.1))


def put(job: VideoJob, i: int, points: list, cards: list[FrameCard],
        specs: dict[str, EffectSpec]) -> None:
    """Move one firing on the picture, for this video only.

    The card keeps saying where the effect goes everywhere else; this is the same
    answer given again for one cue, which is why it is stored on the cue and why
    clearing it (an empty list) puts the firing back where the card says. Coordinates
    are on the CARD, so the crop move carries them exactly as it carries the card's
    own: what the room hands over is the point the operator dropped it on, worked back
    through the window that was up at that moment."""
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    q = cues[i]
    spec = specs.get(q.effect)
    if spec is None or spec.anchor != "point":
        raise ValueError("this effect does not sit on the picture")
    if not q.card:
        raise ValueError("this firing is not on a picture")
    q.points = [Point(cx=min(max(float(p.get("cx", 0.5)), 0.0), 1.0),
                      cy=min(max(float(p.get("cy", 0.5)), 0.0), 1.0))
                for p in (points or [])[:8]]


def turn_to(job: VideoJob, i: int, degrees: float, specs: dict[str, EffectSpec]) -> None:
    """Aim one firing, in degrees clockwise on top of whatever the card aims it at.

    Wrapped into (-180, 180] rather than clamped: an angle is a direction and 190
    degrees is a real answer that means -170, while a clamp would quietly refuse the
    short way round."""
    cues = ordered(job)
    if not 0 <= i < len(cues):
        raise ValueError("there is no such effect")
    spec = specs.get(cues[i].effect)
    if spec is None or spec.path is None:
        raise ValueError("this effect has no picture to turn")
    cues[i].turn = round((float(degrees) % 360 + 540) % 360 - 180, 1)


def rows(job: VideoJob, cards: list[FrameCard], specs: dict[str, EffectSpec],
         aspect: float = 9 / 16) -> list[dict]:
    """The track's effects as the screen reads them, geometry included.

    The path is computed HERE rather than in the browser and it is the same call the
    render makes (:func:`draw_for`). A preview that worked the crop transform out for
    itself would be a second implementation of the one piece of arithmetic in this
    feature that is actually difficult, and the two would disagree about exactly the
    case somebody opened the room to check."""
    by_name = {c.name: c for c in cards}
    out: list[dict] = []
    for i, q in enumerate(ordered(job)):
        spec = specs.get(q.effect)
        card = by_name.get(q.card)
        draw = None
        hung = hook_of(card, spec, q.hook) if spec is not None else None
        if spec is not None and spec.usable:
            width = hung.width if (hung and hung.width > 0) else spec.width
            draw = draw_for(q, spec, card, hung, job.frame_shots, width, aspect,
                            cover_for(spec, aspect), aim(hung, q))
        # what the room offers for this one: a repeat count where there is something to
        # repeat, a plain length where there is not
        cycles = bool(spec and spec.cycles)
        out.append({
            "i": i,
            "effect": q.effect,
            "cycles": cycles,
            "loops": (q.loops or (spec.loops if spec else 1)) if cycles else 0,
            # one pass through the middle, so the room can say what a repeat costs
            "cycle_s": (spec.loop_to - spec.loop_from) if cycles else 0.0,
            "start": q.start,
            "duration": draw.duration if draw else q.duration,
            "scene": q.anchor_scene,
            "word": q.anchor_word,
            "said": q.word,
            "card": q.card,
            "hook": q.hook,
            # whether the card actually says where this one goes — a hook with no
            # points falls back to the frame, which is worth showing rather than
            # leaving the operator to wonder why it is not on the thing
            "placed": bool(q.points or (hung and hung.points)),
            # …and whether it was moved for this video only, which is a thing to be
            # able to undo without hunting for the card it came from
            "moved": bool(q.points),
            "points": [{"cx": pt.cx, "cy": pt.cy} for pt in q.points],
            # the aim, as the two halves it is made of: what the card says and what
            # this firing adds — the room turns the second and shows the sum
            "turn": q.turn,
            "turn_card": hung.turn if hung else 0.0,
            "pinned": q.pinned,
            # an effect whose file somebody removed from the base: still on the track,
            # still the operator's to drop, and drawn by nothing
            "known": bool(spec is not None and spec.usable),
            # BOUND to the picture that is up — one of the card's own ready effects,
            # fired from the shot and living inside it — as against one added by hand
            # over whatever happens to be there. The two are different acts and the
            # timeline draws them in different places (see `web/static/montage.js`).
            "bound": hung is not None,
            "anchor": spec.anchor if spec else "screen",
            "silent": bool(spec is None or spec.sound_path is None),
            "path": [{"at": t, "cx": cx, "cy": cy, "w": w, "a": a, "r": r}
                     for t, cx, cy, w, a, r in (draw.path if draw else [])],
        })
    return out


def clip_to_shots(job: VideoJob) -> None:
    """Cut every firing that sits on a picture back inside the shot it sits on.

    A bound effect cannot outlive its picture — the moment the shot changes there is
    nothing left under it — and the shot's length is not fixed: a cut placed in front
    of it, a line re-voiced, a re-plan, and it is shorter than it was. So the clip is
    re-applied wherever the clock is re-measured rather than only where a cue is made."""
    for q in job.effect_cues:
        if not q.card:
            continue
        shot = shot_at(job.frame_shots, q.start)
        if shot is None:
            continue
        q.duration = max(min(q.duration, shot.start + shot.duration - q.start), 0.05)


def settle(job: VideoJob, cards: list[FrameCard]) -> int:
    """Re-measure the track and drop the cues the picture has left behind.

    Two different kinds of wrong are cleaned up here and only one of them is about
    time. A cue whose word moved is simply re-measured (:func:`reanchor`). A cue
    pinned to a REGION of a card that is no longer up at that moment is pointing at a
    picture nobody can see any more — a re-plan put another still there, or the
    operator cast one by hand — and an arrow over the wrong picture is worse than no
    arrow. The operator's own cues are kept whatever happens to the picture under
    them: they may well be fixing it next. Returns how many were dropped."""
    reanchor(job)
    clip_to_shots(job)
    by_name = {c.name: c for c in cards}
    kept: list[EffectCue] = []
    for q in job.effect_cues:
        if q.pinned or not q.card:
            kept.append(q)
            continue
        shot = shot_at(job.frame_shots, q.start)
        card = by_name.get(q.card)
        if shot is not None and shot.card == q.card and card is not None:
            kept.append(q)
    dropped = len(job.effect_cues) - len(kept)
    job.effect_cues = kept
    return dropped


# Where the chat mode's send sounds live, and how the choice is spelled. The same
# three answers the music select has (`stages.assemble.MUSIC_NONE`): "" is a roll over
# the folder, `none` is silence, anything else is one file.
CHAT_SFX_DIR = "chat_sfx"
SOUND_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def for_part(job: VideoJob, ctx, scenes: list) -> list[Draw]:
    """Everything that goes on ONE episode, on that episode's own clock.

    A serial is cut a part at a time and each part's video starts at zero, so the cues
    belonging to it are found by span and shifted back. Looked up here rather than in
    the assembling stage because what an effect needs — the world's cards and the
    effects base — is knowledge about configs, and `stages.assemble` is knowledge
    about ffmpeg."""
    start, end = span_of(job, scenes)
    out: list[Draw] = chat_sounds(job, ctx, start, end)
    if not job.effect_cues:
        return out
    world = ctx.store.fandoms.get(ctx.params.fandom) if ctx.params.mode == "fandom" else None
    cards = list(world.frames) if world else []
    specs = dict(getattr(ctx.store, "effects", {}) or {})
    if not specs:
        return out
    v = ctx.g.video
    return out + render(job, cards, specs, start, end,
                        aspect=(v.width / v.height) if v.height else 9 / 16)


def chat_sounds(job: VideoJob, ctx, start: float, end: float) -> list[Draw]:
    """The pop a message makes as it lands, one per message, on the part's own clock.

    A sound with no picture is already a whole effect here (`ffmpeg.EffectDraw`: "a
    `sound` with no `asset` is a whole effect and draws nothing"), and the delivery
    pass already knows how to delay one to the moment it goes off. So the chat's send
    sounds are not a second audio path — they are effects that happen to be inaudible
    to the eye, and everything about mixing them was written years before this mode
    existed.

    One per MESSAGE and not per state: a long message arrives in pieces and a reaction
    pops after it, and a phone makes its noise once, when the message lands."""
    if getattr(ctx.params, "mode", "") != "chat" or not job.chat_states:
        return []
    cfg = ctx.chat
    want = (cfg.sfx or "").strip()
    if want == "none":
        return []
    root = ctx.g.paths.assets / CHAT_SFX_DIR
    pool = sorted((p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in SOUND_EXTS),
                  key=lambda p: p.relative_to(root).as_posix()) if root.is_dir() else []
    if not pool:
        return []
    if want:
        named = [p for p in pool if p.name == want or p.stem == want]
        if named:
            pool = named
        else:
            log.warning("chat: the send sound %r is not in assets/%s any more — "
                        "rolling instead", want, CHAT_SFX_DIR)
    sound = random.Random(f"chat_sfx|{job.workdir}").choice(pool)
    volume = min(max(float(cfg.sfx_volume), 0.0), 2.0)
    seen: set[int] = set()
    out: list[Draw] = []
    for st in job.chat_states:
        if st.msg in seen or not (start - 1e-6 <= st.start <= end + 1e-6):
            seen.add(st.msg)
            continue
        seen.add(st.msg)
        out.append(Draw(name="chat_sfx", sound=sound, start=st.start - start,
                        duration=0.0, volume=volume))
    return out


def span_of(job: VideoJob, scenes: list) -> tuple[float, float]:
    """Where one run of scenes begins and ends on the whole video's clock. By
    IDENTITY, because a part's scenes are the job's own objects and two beats of the
    same length saying the same thing are equal without being the same beat."""
    want = {id(s) for s in scenes}
    start: float | None = None
    end = 0.0
    at = 0.0
    for s in job.scenes:
        if id(s) in want:
            if start is None:
                start = at
            end = at + s.duration
        at += s.duration
    return (start if start is not None else 0.0,
            end if start is not None else job.total_duration)


def render(job: VideoJob, cards: list[FrameCard], specs: dict[str, EffectSpec],
           start: float = 0.0, end: float | None = None,
           aspect: float = 9 / 16) -> list[Draw]:
    """Every effect that goes on this stretch of the video, with its clock rebased.

    `start`/`end` are how a serial's episodes are handled: each part is finalized on
    its own and its clock starts at zero, so a cue is offered to the part it falls in
    and its seconds are shifted back by where that part began. A cue is not split
    across a cut — one that would straddle the join is simply cut short by the end of
    the episode it started in."""
    by_name = {c.name: c for c in cards}
    stop = job.total_duration if end is None else end
    out: list[Draw] = []
    for cue in sorted(job.effect_cues, key=lambda q: q.start):
        if not (start - 1e-6 <= cue.start < stop - 1e-6):
            continue
        spec = specs.get(cue.effect)
        if spec is None or not spec.usable:
            continue
        card = by_name.get(cue.card)
        hung = hook_of(card, spec, cue.hook)
        width = hung.width if (hung and hung.width > 0) else spec.width
        d = draw_for(cue, spec, card, hung, job.frame_shots, width, aspect,
                     cover_for(spec, aspect), aim(hung, cue))
        if d is None:
            continue
        d.duration = min(d.duration, stop - cue.start)
        if d.duration <= 0.05:
            continue
        d.start -= start
        d.path = [(t - start, cx, cy, w, a, r) for t, cx, cy, w, a, r in d.path]
        out.append(d)
    return out
