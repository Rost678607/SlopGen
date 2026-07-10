"""Stage 5: build an ASS subtitle file from TTS word timings.

Three styles:
  word_pop — one big word at a time, popping in sync with the voice (default)
  phrases  — classic 3-5 word blocks at the bottom
  karaoke  — full phrase visible, words highlighted as spoken (\\k tags)
"""

from __future__ import annotations

import re

from ..context import AppContext
from ..job import VideoJob, Word
from ..parts import part_start_offsets, requested_parts, scenes_by_part

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


def _shift_words(words: list[Word], offset: float) -> list[Word]:
    return [
        Word(text=w.text, start=w.start - offset, end=w.end - offset)
        for w in words
    ]


def run(job: VideoJob, ctx: AppContext) -> None:
    sc = ctx.g.subtitles
    style = ctx.params.subtitle_style or sc.style
    words = [w for scene in job.scenes for w in scene.words]

    path = job.workdir / "subs.ass"
    path.write_text(_ass_text(words, ctx, style), encoding="utf-8")
    job.ass_path = path

    parts = requested_parts(ctx.params)
    job.part_ass_paths = []
    if not ctx.is_drama or parts <= 1:
        return

    starts = part_start_offsets(job.scenes, parts)
    for i, scenes in enumerate(scenes_by_part(job.scenes, parts), start=1):
        if not scenes:
            continue
        part_words = [w for scene in scenes for w in scene.words]
        part_path = job.workdir / f"subs_part_{i:02d}.ass"
        part_path.write_text(_ass_text(_shift_words(part_words, starts[i - 1]), ctx, style), encoding="utf-8")
        job.part_ass_paths.append(part_path)
