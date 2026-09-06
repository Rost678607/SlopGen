"""The frame base: a world told out of a folder of stills it keeps reusing.

Every other footage mode answers one question per scene — what picture goes behind
THIS line — and that is exactly why its output reads as a slideshow. Measured on two
projects doing this well, the picture behaves quite differently:

* it is a STILL, held several seconds, with a linear move of the crop window;
* the move converges OFF-CENTRE, onto something in the picture, and it does not run
  the whole shot — it is held, then travelled, then held again;
* the same rendered picture comes back two to four times in one video, spread out,
  never twice in a row;
* and the changes are **not** aligned to the narration. The two tracks run past each
  other, and a viewer who cannot predict the next cut keeps watching.

None of that fits a per-scene loop, so the picture is planned for the whole video at
once, in two passes that are deliberately kept apart:

**Where to cut** (this module, :func:`plan_cuts`) is decided by the SPEECH alone —
by where the speaker pauses — and by nothing else. It sees no cards, so a delivered
picture can never move a cut that was already made.

**What to show** is decided by an LLM reading each card's description against each
beat (see :mod:`.stages.picture`), and then filtered through :class:`Picker`, which
knows nothing about meaning and only enforces rhythm: never the same card twice in a
row, not too often, not the same move as last time. Meaning and rhythm are different
kinds of judgement and neither is any good at the other's job.

Only at the very end is the result sliced back into the per-scene
:class:`~.job.BgAsset` lists that :mod:`.stages.assemble` already renders. A shot
straddling a scene boundary becomes two pieces of the same picture carrying the same
move and a phase offset — the manoeuvre continuous video mode already makes with
`BgAsset.start`, one clock further out.

The economy is the base itself. A card is bought once — generated, drawn, or paid
for — and then spent several times across a video and again in the next one, which is
why a card carries named CROP TARGETS: one wide still of a widow at a market is four
shots, and the move between any two of them costs nothing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..config.models import CropTarget, FrameCard, KenBurns, MoveKind, Rect
from .job import FrameShot, Scene

# -- where a cut may land --------------------------------------------------
# Safety rails, not the mechanism. What actually decides a shot's length is the
# speech: we cut where the speaker takes a breath. These only stop the absurd —
# a picture flashed for a moment, or one left up long past the point of it.
SHOT_FLOOR_S = 1.4
SHOT_CEIL_S = 9.0
# The pause a word must be preceded by before it counts as a place to cut, at the two
# ends of the sensitivity knob. Low sensitivity waits for a real break in the speech
# and yields long shots; high sensitivity takes almost any gap and yields short ones.
PAUSE_AT_LOW = 0.45
PAUSE_AT_HIGH = 0.04
# A cut sitting right on the seam between two scenes re-synchronises the two tracks
# for one shot, and that one shot is audible. It is not forbidden — the first word of
# a new scene is a perfectly good place to change the picture, and sometimes the only
# one — but its pause is damped so it loses to any decent interior option.
SEAM_NEAR_S = 0.25
SEAM_DAMP = 0.5

# -- the shape of a move ---------------------------------------------------
# From the one shot measured frame by frame: 5.52s long, travelling from 2.15s to
# 3.95s — 39% of it still, 33% travelling, 28% still again. Jittered per shot so
# eight shots do not share one metronome. One measurement is one measurement; if this
# ever looks wrong, more episodes are a browser away.
MOVE_LEAD_FRAC = 0.39
MOVE_SPAN_FRAC = 0.33
MOVE_JITTER = 0.06
MOVE_MIN_S = 0.8
# How much wider than its target a zoom starts. Enough that the target is clearly
# being converged ON rather than merely cropped to.
ZOOM_OUT_FACTOR = 2.2
# A push-in with nothing marked up to push into: the mildest move that is still a
# move, so a base nobody has annotated yet is not simply a slideshow.
PUSH_SCALE = 0.85
# Past this, a shot may not simply be held. A frozen frame is a real and measured
# choice — the source footage holds one for as long as 6.9 seconds — but nothing there
# is ever held longer, and a merged shot easily runs three times that. Twenty-two
# seconds of a motionless picture is not a held shot, it is a stuck video.
HOLD_MAX_S = 7.0

# -- how often a card may come back ----------------------------------------
# Measured: one render appears two to four times in a video, spread out, never twice
# in a row. The cooldown is what does the spreading — at 25s a 90-second video can
# spend the same card three times and no more, which is the observed shape without
# having to count anything.
REUSE_COOLDOWN_S = 25.0
MAX_USES = 4


def active(job, ctx) -> bool:
    """Whether this run's picture comes from the world's frame base.

    All or nothing: the track is planned over the whole video at once, so a chain
    that mixed `frames` with a generator would have no coherent answer for what the
    other half of the timeline is doing. Ad scenes are exempt because their picture is
    not ours to plan — it is bought by somebody else and simply interrupts the track."""
    from ..media.generate import is_frame_model

    beats = [s for s in job.scenes if not s.is_ad]
    return bool(beats) and all(is_frame_model(s.gen_model or "") for s in beats)


def pause_threshold(sensitivity: float) -> float:
    """How long a silence has to be, at this sensitivity, to be worth cutting on."""
    s = min(max(sensitivity, 0.0), 1.0)
    return PAUSE_AT_LOW + (PAUSE_AT_HIGH - PAUSE_AT_LOW) * s


# --------------------------------------------------------------------------
# the timeline
# --------------------------------------------------------------------------


@dataclass
class Region:
    """A stretch of the finished video the picture track may plan over.

    Ad scenes are not in one: an ad carries its own picture, bought by somebody else,
    and a shot may not cross into it. So a video with an ad in the middle is planned
    as two regions, each cut independently."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Cue:
    """A place the picture could change: the moment a word begins, and the silence in
    front of it.

    Word ENDS are deliberately not cues. A cut placed where a word finished leaves the
    new picture arriving into a silence, which reads as a mistake; placed where the
    next word begins, it reads as somebody who meant it. So the cue is always a word's
    left edge, and the pause before it is what says how good a place this is."""

    at: float  # absolute seconds: where the word starts
    pause: float  # silence in front of it
    seam: bool  # the first word of a scene, i.e. right after a narration seam
    scene: int  # which scene the word belongs to
    word: int  # and its index within that scene — together, the shot's lasting identity


def timeline(scenes: list[Scene]) -> tuple[list[Cue], list[Region], float]:
    """Lay the narration on one absolute clock: (cues, plannable regions, total).

    Word timings are stretched and offset exactly the way `stages.subtitles._lay_out`
    does it, and for the same reason: in a beat mode `Scene.words` are relative to the
    voice AS SYNTHESIZED, while the scene runs for however long the picture made it
    (`Scene.duration` against `Scene.audio_src_duration`). Reading them raw is how a
    picture cut ends up half a second from the word it was meant to land on, and —
    worse — disagreeing with the subtitles about where that word is.

    In a pure stills run the factor is 1.0, because a photo has no length of its own
    and nothing is ever retimed. It is computed rather than assumed all the same: a
    line the operator re-voiced at the `tts` breakpoint changes it."""
    cues: list[Cue] = []
    regions: list[Region] = []
    at = 0.0
    open_at: float | None = None
    prev_end: float | None = None
    for si, scene in enumerate(scenes):
        if scene.is_ad:
            if open_at is not None:
                regions.append(Region(open_at, at))
                open_at = None
            prev_end = None
        else:
            if open_at is None:
                open_at = at
            factor = (scene.duration / scene.audio_src_duration) if scene.audio_src_duration else 1.0
            for i, w in enumerate(scene.words):
                start = at + w.start * factor
                gap = start - prev_end if prev_end is not None else 0.0
                cues.append(Cue(at=start, pause=max(gap, 0.0), seam=(i == 0),
                                scene=si, word=i))
                prev_end = at + w.end * factor
        at += scene.duration
    if open_at is not None:
        regions.append(Region(open_at, at))
    cues.sort(key=lambda c: c.at)
    return cues, regions, at


def cut_shots(cues: list[Cue], region: Region, sensitivity: float) -> list[tuple[float, Cue | None]]:
    """Cut one region into shots, as (start, duration).

    The rule is one line: walk forward and take the FIRST breath long enough to be
    worth cutting on. What "long enough" means is the sensitivity knob, and that is
    the whole of shot length — there is no target duration and no randomness here, so
    the same narration always cuts the same way no matter what is in the base or what
    the operator has edited about the pictures.

    A cue sitting on a narration seam is not refused, only damped: the biggest silence
    in any region is the one at the end of a line, and always taking it would tie the
    picture track back to the scenes, which is the one thing this mode exists to
    avoid. Damped, it still wins when it is genuinely the only place to breathe.

    When no cue in the window clears the threshold, the best available breath is taken
    instead, and if there is no cue at all — one very long word, a silent stretch —
    the cut lands on the window's far edge. A shot running past the ceiling looks
    worse than a cut nobody can hear."""
    thr = pause_threshold(sensitivity)
    out: list[tuple[float, Cue | None]] = []
    cursor = region.start
    at_cue: Cue | None = None  # the cue the CURRENT shot starts on; None = region start
    while region.end - cursor > SHOT_FLOOR_S * 2:
        # a cut must leave a viable shot on BOTH sides, which is what keeps the floor
        # honest without having to patch up a runt afterwards
        lo, hi = cursor + SHOT_FLOOR_S, min(cursor + SHOT_CEIL_S, region.end - SHOT_FLOOR_S)
        if hi < lo:
            break
        weighted = [(c, c.pause * (SEAM_DAMP if c.seam else 1.0))
                    for c in cues if lo <= c.at <= hi]
        hit = next((c for c, w in weighted if w >= thr), None)
        if hit is None:
            if region.end - cursor <= SHOT_CEIL_S:
                break  # nobody breathes again and the picture can hold to the end
            # past the ceiling with nothing worth cutting on: take the best breath
            # there is, and failing that the far edge. A shot left up past the rail
            # is worse than a cut nobody can hear.
            hit = max(weighted, key=lambda p: p[1])[0] if weighted else None
        cut = hit.at if hit is not None else hi
        out.append((cursor, at_cue))
        cursor, at_cue = cut, hit
    if region.end - cursor > 1e-6:
        out.append((cursor, at_cue))
    return out


def referents_at(scenes: list[Scene], start: float, end: float) -> list[str]:
    """Who and what the narration is about while a shot is up.

    A shot is asynchronous with the scenes, so it may hear the end of one line and the
    start of the next; both scenes' casts count. Order is preserved and duplicates
    dropped, so the first name is the one that has been on longest.

    "Cast" is not a synonym for "people" here — a world's characters are whatever the
    world is made of, a stump, an egg, a sack — which is why nothing downstream ever
    branches on what kind of thing a name refers to."""
    out: list[str] = []
    at = 0.0
    for scene in scenes:
        if not scene.is_ad and at < end and at + scene.duration > start:
            for name in scene.characters:
                if name not in out:
                    out.append(name)
        at += scene.duration
    return out


def said_at(scenes: list[Scene], start: float, end: float) -> tuple[str, str]:
    """What is being said and what the writer asked to be shown while a shot is up,
    joined across every scene it overlaps. The first is what the matcher reads; the
    second is what an ask is written from."""
    said: list[str] = []
    shown: list[str] = []
    at = 0.0
    for scene in scenes:
        if not scene.is_ad and at < end and at + scene.duration > start:
            if scene.text.strip():
                said.append(scene.text.strip())
            if scene.video_prompt.strip():
                shown.append(scene.video_prompt.strip())
        at += scene.duration
    return " ".join(said), " ".join(shown)


def plan_cuts(scenes: list[Scene], sensitivity: float = 0.5) -> list[FrameShot]:
    """The picture track's skeleton: when it changes, and what is being said each
    time. No cards are consulted and none can be — that is what keeps a delivery from
    moving a cut that was already agreed."""
    cues, regions, _ = timeline(scenes)
    shots: list[FrameShot] = []
    for region in regions:
        cuts = cut_shots(cues, region, sensitivity)
        for k, (start, cue) in enumerate(cuts):
            end = cuts[k + 1][0] if k + 1 < len(cuts) else region.end
            said, shown = said_at(scenes, start, end)
            shots.append(FrameShot(
                start=start, duration=end - start,
                anchor_scene=cue.scene if cue else -1,
                anchor_word=cue.word if cue else -1,
                referents=referents_at(scenes, start, end),
                said=said, prompt=shown,
            ))
    return shots


def reanchor(scenes: list[Scene], shots: list[FrameShot]) -> None:
    """Put the shots back where their anchors say, after the clock has moved.

    Re-voicing one line at the `tts` breakpoint changes that scene's duration, and
    every second after it. The cuts themselves did not change — they were placed on
    words, and those words are still the same words — so the plan is not re-made, it
    is re-measured. A shot whose anchor no longer exists (the operator deleted the
    words it stood on) keeps the seconds it had; the stage above decides whether that
    is worth re-planning."""
    cues, regions, total = timeline(scenes)
    where = {(c.scene, c.word): c.at for c in cues}
    for s in shots:
        if s.anchor_scene >= 0 and (s.anchor_scene, s.anchor_word) in where:
            s.start = where[(s.anchor_scene, s.anchor_word)]
    ordered = sorted(shots, key=lambda s: s.start)
    # every shot runs to the next one, and the last of each region to the region's end
    for s in ordered:
        end = min((r.end for r in regions if r.end > s.start), default=total)
        nxt = min((o.start for o in ordered if o.start > s.start), default=end)
        s.duration = max(min(nxt, end) - s.start, 0.0)


# --------------------------------------------------------------------------
# rhythm: what a shot is ALLOWED to show, given what came before
# --------------------------------------------------------------------------


@dataclass
class Picker:
    """The repetition rules, and nothing else.

    It has no idea what any card is of — that judgement belongs to the matcher, which
    hands over a ranked list of what would FIT. This only says what is allowed to come
    next given what has just been on screen, which is a question about rhythm and not
    about meaning. Keeping the two apart is what stops either from quietly overruling
    the other.

    It is per video and per pass, and a resumed run rebuilds it by replaying the shots
    already decided, so the second half of a video is chosen against the same history
    as the first."""

    usable: set[str]
    last_card: str = ""
    last_kind: str = ""
    used_at: dict[str, float] = field(default_factory=dict)
    uses: dict[str, int] = field(default_factory=dict)

    def pick(self, ranked: list[str], now: float) -> str:
        """The best-fitting card that the rhythm still allows, or "" if the ranked
        list is empty.

        The rules relax in a fixed order when the whole ranking is blocked, because a
        run must degrade rather than stall — but never the first one. Adjacent
        repetition is the single thing the source footage never does, and it is
        precisely what a slideshow looks like."""
        pool = [n for n in ranked if n in self.usable and n != self.last_card]
        for relax in range(3):
            for name in pool:
                if relax < 1 and now - self.used_at.get(name, -1e9) < REUSE_COOLDOWN_S:
                    continue
                if relax < 2 and self.uses.get(name, 0) >= MAX_USES:
                    continue
                return name
        return ""

    def take(self, name: str, kind: str, start: float) -> None:
        self.last_card = name
        self.last_kind = kind
        self.used_at[name] = start
        self.uses[name] = self.uses.get(name, 0) + 1


# --------------------------------------------------------------------------
# giving a shot its move
# --------------------------------------------------------------------------


def _wide_around(target: Rect, floor: float) -> Rect:
    """A window containing `target` with room around it, leaning back toward the
    middle of the picture. This is what a zoom starts from, so the target is visibly
    converged ON rather than merely arrived at."""
    scale = min(1.0, target.scale * ZOOM_OUT_FACTOR)
    lean = (scale - target.scale) / 2 / max(scale, 1e-6)
    return Rect(
        cx=target.cx + (0.5 - target.cx) * lean,
        cy=target.cy + (0.5 - target.cy) * lean,
        scale=scale,
    ).clamped(floor)


def aim(card: FrameCard, referents: list[str], chosen: str = "",
        stale: bool = False) -> list[CropTarget]:
    """The regions of this card worth looking at right now, most relevant first.

    Two ways in, and they cover different ground.

    By NAME: a target whose `of` is one of the referents is about somebody the
    narration is currently on, and `referents` arrives ordered by who has been on
    screen longest, so that order is the ranking. Nothing else is offered — an earlier
    version fell back to "any target at all" when no name matched, which meant a shot
    about one character zooming into another's face while they were being talked
    about. Showing the wrong thing costs more than showing the whole picture.

    By LABEL: `chosen` is a target the matcher named, and it goes first whatever its
    `of` says. This is the only way to reach a region that is not anybody — a hat left
    on a counter, a doorway — because those carry no name to match on, and the
    narration mentioning "the hat" is a fact about the TEXT that no name comparison
    can see."""
    if stale:
        return []  # the file changed under the geometry; it cannot be trusted
    order = {name: i for i, name in enumerate(referents)}
    named = sorted((t for t in card.targets if t.of and t.of in order),
                   key=lambda t: order[t.of])
    if chosen:
        want = chosen.strip().casefold()
        hit = next((t for t in card.targets
                    if t.label.strip().casefold() == want or t.of.strip().casefold() == want), None)
        if hit is not None:
            named = [hit] + [t for t in named if t is not hit]
    return named


def move_for(card: FrameCard, referents: list[str], duration: float, last_kind: str,
             rng: random.Random, min_scale: float = 0.1, stale: bool = False,
             target: str = "") -> KenBurns:
    """Give one shot its crop move.

    What is on offer depends on what the card can currently be aimed at (see
    :func:`aim`). Nothing to aim at — nothing marked, nothing relevant, or the
    geometry gone stale — and the move can only be held, pushed into, or drifted
    across the whole picture. One region buys a zoom; two buy a pan between them.

    The kind is never the same as the previous shot's. Measured: eight consecutive
    shots, eight different trajectories — and two zooms in a row read as a slideshow
    however well each picture is composed. `drift` exists for exactly this reason on
    an un-annotated base: without it a fresh base can only alternate hold and push_in,
    which is its own metronome.

    WHICH region is not a random pick among the eligible ones any more. It is the
    first of the ranked ones, so the move lands on whatever the shot is most about."""
    aimed = aim(card, referents, target, stale)

    kinds: list[MoveKind] = ["push_in", "drift"]
    if duration <= HOLD_MAX_S:
        kinds.insert(0, "hold")
    if aimed:
        kinds += ["zoom_in", "zoom_out"]
    if len(aimed) >= 2:
        kinds.append("pan")
    choices = [k for k in kinds if k != last_kind] or kinds
    kind: MoveKind = rng.choice(choices)

    full = Rect().clamped(min_scale)
    if kind == "hold":
        a = b = (aimed[0].rect.clamped(min_scale) if aimed and rng.random() < 0.5 else full)
    elif kind == "push_in":
        anchor = aimed[0].rect if aimed else Rect()
        a, b = full, Rect(cx=anchor.cx, cy=anchor.cy, scale=PUSH_SCALE).clamped(min_scale)
    elif kind == "drift":
        # a slow slide across the whole picture, needing nothing marked up: the one
        # move a brand-new card can always make
        s = max(PUSH_SCALE, min_scale)
        dx, dy = rng.choice([(-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, 1)])
        span = (1.0 - s) / 2
        a = Rect(cx=0.5 - dx * span, cy=0.5 - dy * span, scale=s).clamped(min_scale)
        b = Rect(cx=0.5 + dx * span, cy=0.5 + dy * span, scale=s).clamped(min_scale)
    elif kind in ("zoom_in", "zoom_out"):
        tgt = aimed[0].rect.clamped(min_scale)
        wide = _wide_around(tgt, min_scale)
        a, b = (wide, tgt) if kind == "zoom_in" else (tgt, wide)
    else:  # pan: two equal windows, so the move is pure translation
        t1, t2 = aimed[0], aimed[1]
        s = min(t1.rect.scale, t2.rect.scale)
        a = Rect(cx=t1.rect.cx, cy=t1.rect.cy, scale=s).clamped(min_scale)
        b = Rect(cx=t2.rect.cx, cy=t2.rect.cy, scale=s).clamped(min_scale)

    lead = duration * (MOVE_LEAD_FRAC + rng.uniform(-MOVE_JITTER, MOVE_JITTER))
    span = max(duration * (MOVE_SPAN_FRAC + rng.uniform(-MOVE_JITTER, MOVE_JITTER)), MOVE_MIN_S)
    lead = max(0.0, min(lead, max(duration - span, 0.0)))
    span = min(span, max(duration - lead, 1e-3))
    return KenBurns(rect_a=a, rect_b=b, move_start=lead, move_end=lead + span, kind=kind)


def assign(shots: list[FrameShot], cards: list[FrameCard], ranked: dict[int, list[str]],
           *, targets: dict[int, str] | None = None,
           min_scales: dict[str, float] | None = None,
           stale: frozenset[str] = frozenset(), seed: str = "") -> None:
    """Fill in the cards and moves the matcher chose, in place.

    `ranked` is per shot index, best fit first — the matcher's opinion, and `targets`
    is the region it named for that shot, if any. A pinned shot
    keeps what it has and is fed to the picker as history BEFORE anything else is
    decided, so the shots around a pin re-plan around it rather than colliding."""
    by_name = {c.name: c for c in cards}
    rng = random.Random(seed or "moves")
    picker = Picker(usable={c.name for c in cards if c.usable})
    scales = min_scales or {}

    for s in shots:
        if s.pinned and s.card:
            picker.take(s.card, s.move.kind if s.move else "hold", s.start)

    for i, s in enumerate(shots):
        if s.pinned and s.card:
            continue
        name = picker.pick(ranked.get(i, []), s.start)
        if not name:
            s.card, s.move = "", None
            continue
        card = by_name[name]
        mv = move_for(card, s.referents, s.duration, picker.last_kind, rng,
                      min_scale=scales.get(name, 0.1), stale=name in stale,
                      target=(targets or {}).get(i, ""))
        picker.take(name, mv.kind, s.start)
        s.card, s.move = name, mv


def merge_adjacent(shots: list[FrameShot]) -> list[FrameShot]:
    """Fuse neighbouring shots that ended up showing the same picture.

    Two shots of one still with a cut between them is not an edit, it is a fault: the
    picture does not change, but its crop move restarts, so the frame SNAPS. Measured
    on the first live run — 28 to 53% of the frame jumping across such a boundary,
    against 0.7 to 2.0% of ordinary travel inside a shot, i.e. twenty to fifty times
    the motion the viewer has been watching, on an image that did not change.

    It happens because the two halves of this mode pull opposite ways. Grouping asks
    by place is what saves the operator work — four stretches in one room are one
    picture, not four — and the moment that picture is delivered it lands on four
    CONSECUTIVE shots. Nothing was wrong with either decision; they simply meet here,
    and here is where one shot has to come out of them.

    The fused shot keeps the first one's start and anchor, everybody either was about,
    and one move across the whole stretch. It may then run longer than `SHOT_CEIL_S`,
    which is correct: that rail governs where the picture may be CUT, and this is the
    picture not being cut.

    The surviving shots are MUTATED in place and the swallowed ones are dropped, so the
    returned list is the only valid view afterwards — hold on to it and let the old one
    go, rather than reading a length off a shot that has since been absorbed."""
    out: list[FrameShot] = []
    for s in sorted(shots, key=lambda x: x.start):
        prev = out[-1] if out else None
        joined = (prev is not None and s.card and prev.card == s.card
                  and abs(prev.start + prev.duration - s.start) < 1e-6)
        if not joined:
            out.append(s)
            continue
        prev.duration += s.duration
        prev.referents += [r for r in s.referents if r not in prev.referents]
        prev.said = " ".join(x for x in (prev.said, s.said) if x)
        prev.pinned = prev.pinned or s.pinned
        prev.move = None  # the move spanned the old, shorter shot; it needs a new one
    return out


def apply_to_scenes(job, cards: list[FrameCard]) -> None:
    """Hang the picture track on the scenes, cutting it where they end.

    A shot spanning a scene boundary becomes two pieces of the same file, the second
    carrying `move_at` — how much of the shot the first one used up. Both render the
    same move against the same shot clock, so the travel crosses the join without a
    seam and `stages.assemble` needs to know nothing about any of this. It is the
    arrangement continuous video mode already uses, one clock further out.

    A CLIP card is laid down without a move and at speed 1.0, which is what makes it
    LOOP to fill its shot instead of being retimed to it: a card is a thing the world
    has, not something cut to measure for one beat, and stretching it would be the
    wrong operation. It also already has motion of its own, and two motions over one
    picture fight.
    """
    from ..media.stock import IMAGE_EXTS
    from .job import BgAsset

    by = {c.name: c for c in cards}
    at = 0.0
    for scene in job.scenes:
        if scene.is_ad:
            at += scene.duration
            continue
        t0, t1 = at, at + scene.duration
        parts: list[BgAsset] = []
        for s in sorted(job.frame_shots, key=lambda x: x.start):
            a, b = max(t0, s.start), min(t1, s.start + s.duration)
            if b - a <= 1e-6:
                continue
            card = by.get(s.card)
            path = card.path if card else None
            if path is None:
                continue
            photo = path.suffix.lower() in IMAGE_EXTS
            parts.append(BgAsset(
                path=path, duration=b - a, is_photo=photo,
                move=s.move if photo else None,
                move_at=(a - s.start) if photo else 0.0,
            ))
        if parts:
            # the last piece absorbs the float residue, so the pieces add up to the
            # scene exactly and the concat never drifts
            drift = scene.duration - sum(p.duration for p in parts)
            parts[-1].duration = max(parts[-1].duration + drift, 1e-3)
        scene.bg_assets = parts
        at += scene.duration
