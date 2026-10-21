"""Words ↔ seconds, for a machinima block.

Every key in a direction is anchored to a word of the narration (see `direction.
Anchor`), and this is where a word becomes a time. The clock is the picture track's
own (`framebase.timeline`): words are stretched and offset exactly the way the cuts
and the subtitles are, so a nod anchored on «кивнула» lands where the subtitle says
«кивнула» — not half a second from it, which is what reading `Scene.words` raw does
in a beat mode.

Times out of here are SECONDS FROM THE BLOCK'S START, because a block is filmed on
its own clock and laid onto the video at its start (see `framebase.apply_to_scenes`).
"""

from __future__ import annotations

from dataclasses import dataclass

from .direction import Anchor, Direction, WordRef


@dataclass(frozen=True)
class ClockWord:
    scene: int
    i: int
    text: str
    start: float  # absolute seconds in the video
    end: float


class Clock:
    """The narration's words on the video's clock."""

    def __init__(self, words: list[ClockWord]):
        self.words = sorted(words, key=lambda w: w.start)
        self._by = {(w.scene, w.i): w for w in self.words}

    @classmethod
    def of(cls, scenes) -> "Clock":
        """From a job's scenes, with the same stretch `framebase.timeline` applies."""
        out: list[ClockWord] = []
        at = 0.0
        for si, scene in enumerate(scenes):
            if not scene.is_ad:
                factor = (scene.duration / scene.audio_src_duration) if scene.audio_src_duration else 1.0
                for i, w in enumerate(scene.words):
                    out.append(ClockWord(si, i, w.text, at + w.start * factor, at + w.end * factor))
            at += scene.duration
        return cls(out)

    def word(self, ref: WordRef) -> ClockWord | None:
        return self._by.get((ref.scene, ref.i))

    def span(self, start: float, end: float) -> list[ClockWord]:
        """The words that BEGIN inside [start, end) — what a block over that stretch
        can anchor to."""
        return [w for w in self.words if start - 1e-6 <= w.start < end - 1e-6]

    def ref(self, w: ClockWord) -> WordRef:
        return WordRef(scene=w.scene, i=w.i, text=w.text)


class AnchorError(ValueError):
    pass


def resolve(a: Anchor, clock: Clock, block_start: float) -> float:
    """Seconds from the block's start. Raises `AnchorError` for a word the clock does
    not have — a line rewritten under the direction — rather than guess."""
    form = a.form()
    if form == "sec":
        return float(a.sec)
    if form == "word":
        w = clock.word(a.word)
        if w is None:
            raise AnchorError(f"word {a.word.text!r} ({a.word.scene}:{a.word.i}) is gone")
        return w.start + a.offset - block_start
    if form == "between":
        w1, w2 = clock.word(a.between[0]), clock.word(a.between[1])
        if w1 is None or w2 is None:
            missing = a.between[0] if w1 is None else a.between[1]
            raise AnchorError(f"word {missing.text!r} ({missing.scene}:{missing.i}) is gone")
        return w1.start + (w2.start - w1.start) * a.frac - block_start
    raise AnchorError("an anchor is exactly one of `word`, `between` or `sec`")


def stale_words(d: Direction, clock: Clock) -> list[WordRef]:
    """Anchored words that are no longer the words they were.

    A re-voiced line keeps its words and only moves them, which `resolve` absorbs on
    its own. A REWRITTEN line is different: index 5 is now some other word, and an
    anchor that silently followed the index would nod on the wrong one. The text
    carried in the anchor is what catches it."""
    out = []
    for ref in _refs(d):
        w = clock.word(ref)
        if w is None or (ref.text and _norm(ref.text) != _norm(w.text)):
            out.append(ref)
    return out


def _refs(d: Direction):
    for sc in d.scenes:
        anchors = [sc.at] if sc.at else []
        for a in sc.action:
            anchors += [a.at] + ([a.until] if a.until else []) + [t.until for t in a.turns if t.until]
        anchors += [s.at for s in sc.shots]
        for an in anchors:
            if an.word is not None:
                yield an.word
            if an.between is not None:
                yield from an.between


def _norm(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def timing_hash(d: Direction, clock: Clock, block_start: float) -> str:
    """A fingerprint of every resolved key time, rounded to a tick. Recorded with a
    take, it says whether re-voicing moved anything the actors PLAY — which means
    playing again — as opposed to merely where the block starts."""
    import hashlib

    times = []
    for sc in d.scenes:
        anchors = ([sc.at] if sc.at else []) + [a.at for a in sc.action] + \
                  [a.until for a in sc.action if a.until] + \
                  [t.until for a in sc.action for t in a.turns if t.until]
        for an in anchors:
            try:
                times.append(round(resolve(an, clock, block_start) * 20))
            except AnchorError:
                times.append(None)
    return hashlib.sha1(repr(times).encode()).hexdigest()[:16]
