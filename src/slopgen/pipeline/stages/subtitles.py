"""Stage 5: build an ASS subtitle file from TTS word timings.

Three styles:
  word_pop — one big word at a time, popping in sync with the voice (default)
  phrases  — classic 3-5 word blocks at the bottom
  karaoke  — full phrase visible, words highlighted as spoken (\\k tags)

One file per part, because a part is one publishable video and its subtitles must
start at 0:00. This is also the stage that lays the drama's timeline out: the tts
stage voices each line at its natural speed and times the words relative to that
line, since the clip the line has to fit is not known yet, so the stretch onto the
clip (``duration`` against ``audio_src_duration``) is applied here. Doing it here
rather than in the footage stage is what makes it repeatable — a drama is subtitled
one episode at a time, over several resumes, and rewriting positions in place would
compound the stretch on the second pass.
"""

from __future__ import annotations

import re

from ...llm import censor
from ..context import AppContext
from ..job import Part, VideoJob, Word
from ..parts import ready, scenes_by_part, sync

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{font},{size},{primary},{accent},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,5,60,60,0,1
Style: Phrase,{font},{psize},{primary},{accent},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{poutline},2,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(sec: float) -> str:
    sec = max(sec, 0)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", text).strip()


def _phrases(words: list[Word], max_words: int = 4) -> list[list[Word]]:
    """Split into chunks on punctuation or every max_words."""
    out: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.!?,;:—]$", w.text):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _events_word_pop(words: list[Word], accent: str) -> list[str]:
    events = []
    for i, w in enumerate(words):
        end = words[i + 1].start if i + 1 < len(words) else w.end + 0.25
        end = max(end, w.start + 0.10)
        color = f"\\c{accent}" if re.search(r"[!?]$", w.text) else ""
        tags = (
            "\\an5\\pos(540,1430)\\fscx70\\fscy70"
            "\\t(0,110,\\fscx106\\fscy106)\\t(110,220,\\fscx100\\fscy100)" + color
        )
        events.append(
            f"Dialogue: 0,{_ts(w.start)},{_ts(end)},Word,,0,0,0,,{{{tags}}}{_clean(w.text).upper()}"
        )
    return events


def _events_phrases(words: list[Word]) -> list[str]:
    events = []
    for chunk in _phrases(words):
        start, end = chunk[0].start, chunk[-1].end + 0.15
        text = " ".join(_clean(w.text) for w in chunk)
        events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Phrase,,0,0,0,,{text}")
    return events


def _events_karaoke(words: list[Word]) -> list[str]:
    events = []
    for chunk in _phrases(words, max_words=5):
        start, end = chunk[0].start, chunk[-1].end + 0.15
        parts = []
        for i, w in enumerate(chunk):
            w_end = chunk[i + 1].start if i + 1 < len(chunk) else w.end
            dur_cs = max(int((w_end - w.start) * 100), 1)
            parts.append(f"{{\\k{dur_cs}}}{_clean(w.text)}")
        events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Phrase,,0,0,0,,{' '.join(parts)}")
    return events


def _ass_text(words: list[Word], ctx: AppContext, style: str) -> str:
    sc = ctx.g.subtitles
    header = HEADER.format(
        w=ctx.g.video.width,
        h=ctx.g.video.height,
        font=sc.font,
        size=sc.font_size,
        psize=int(sc.font_size * 0.62),
        primary=sc.primary_color,
        accent=sc.accent_color,
        outline=sc.outline,
        poutline=max(sc.outline - 3, 2),
    )
    if style == "word_pop":
        events = _events_word_pop(words, sc.accent_color)
    elif style == "phrases":
        events = _events_phrases(words)
    else:
        events = _events_karaoke(words)
    return header + "\n".join(events) + "\n"


def _lay_out(scenes: list, per_scene: list[list[Word]]) -> list[Word]:
    """Put one part's scene-relative word timings onto its own timeline.

    Each scene contributes its words stretched by however much the voice was retimed
    to meet its clip, placed after everything before it IN THIS PART. The part starts
    at 0:00 because it is a video of its own."""
    out: list[Word] = []
    at = 0.0
    for scene, words in zip(scenes, per_scene):
        factor = (scene.duration / scene.audio_src_duration) if scene.audio_src_duration else 1.0
        out += [Word(text=w.text, start=at + w.start * factor, end=at + w.end * factor)
                for w in words]
        at += scene.duration
    return out


def _retime(words: list[Word], text: str) -> list[Word]:
    """Lay a rewritten line back over the span its original words occupied.

    A contextual rewrite does not preserve the word count — "Съебал нахуй с моей пары
    пидорас блять" becomes seven words where there were six — so the line's total span
    is re-divided among the new words in proportion to their length. The line still
    starts and ends exactly with the speech; only the word-to-word boundaries inside it
    are approximate, and they were never exact for a rewritten word anyway."""
    new = text.split()
    if not words or not new:
        return words
    if len(new) == len(words):  # nothing moved — keep the real timings
        return [Word(text=t, start=w.start, end=w.end) for t, w in zip(new, words)]
    start, end = words[0].start, words[-1].end
    weights = [len(t) + 1 for t in new]
    total = sum(weights)
    out: list[Word] = []
    at = start
    for t, weight in zip(new, weights):
        share = (end - start) * weight / total
        out.append(Word(text=t, start=at, end=at + share))
        at += share
    return out


def _cleaned_words(job: VideoJob, ctx: AppContext, wanted: set[int]) -> list[list[Word]]:
    """Per-scene subtitle words, with the profane lines rewritten when the run asks
    for it. The job itself is left alone: the voice keeps every word it was given, and
    only what gets burned onto the picture is sanitised.

    Only the scenes of `wanted` are sent to the rewriter. The stage is re-entered once
    per batch of finished episodes, and cleaning the whole drama every time would pay
    for the same lines again on each pass."""
    per_scene = [list(scene.words) for scene in job.scenes]
    if not ctx.params.clean_subtitles:
        return per_scene
    idx = sorted(wanted)
    lines = [" ".join(w.text for w in per_scene[i]) for i in idx]
    for i, old, new in zip(idx, lines, censor.clean_lines(ctx.llm, lines, ctx.params.lang)):
        if new != old:
            per_scene[i] = _retime(per_scene[i], new)
    return per_scene


def _ass_path(job: VideoJob, part: Part, multi: bool):
    return job.workdir / (f"subs_part_{part.number:02d}.ass" if multi else "subs.ass")


def run(job: VideoJob, ctx: AppContext) -> None:
    style = ctx.params.subtitle_style or ctx.g.subtitles.style
    sync(job)
    # an episode already subtitled keeps its file: re-entering the stage is how the
    # LATER episodes get theirs, not a reason to rewrite what is already cut
    todo = [p for p in ready(job) if p.ass is None]
    at = {id(scene): i for i, scene in enumerate(job.scenes)}
    groups = [(part, scenes_by_part(job.scenes, part.number)) for part in todo]
    per_scene = _cleaned_words(
        job, ctx, {at[id(s)] for _, scenes in groups for s in scenes})

    multi = len(job.parts) > 1
    for part, scenes in groups:
        if not scenes:
            continue
        per = [per_scene[at[id(s)]] for s in scenes]
        # info already has its timeline: a scene there simply lasts as long as its
        # voice, so tts could place the words outright — and the foreground anchoring
        # reads those absolute positions. Drama could not: a line's length only
        # settles once it has been stretched onto the clip it has to fit.
        words = _lay_out(scenes, per) if ctx.is_beats else [w for ws in per for w in ws]
        path = _ass_path(job, part, multi)
        path.write_text(_ass_text(words, ctx, style), encoding="utf-8")
        part.ass = path
