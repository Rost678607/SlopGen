"""Translating a conversation, and the one rule that makes it hard.

A found conversation is in whatever language it was had in, and the video is in the
operator's. So it has to be translated — but not all of it, and which part is the
whole difficulty. A Russian group chat says `ну всё, я go sleep`, and the `go sleep`
is the joke; a Discord server argues in Russian and quotes an error message in
English, and the error message is evidence. Translate those and the conversation
stops sounding like one.

That rule cannot be given to a translation API. DeepL with auto-detection will
faithfully render every foreign fragment it is handed, because it is being handed
fragments and the judgement needs the conversation. So this goes to the model that is
already configured, one window at a time, with every message visible and only the
ones that need it rewritten — the arrangement `llm/censor.py` arrived at for the same
reason, and for the same reason it is keyed by index: a partial or reordered reply
lands what it did return and loses nothing else.

What comes back is written to `ChatMsg.text`, and what was there goes to
`ChatMsg.source_text`. Both, always, because an operator reviewing a translated chat
is reviewing a translation, and a line they cannot compare against its original is a
line they cannot check. A message the operator has edited themselves is never touched
(`ChatMsg.pinned`), which is what stops a re-entry to the stage from undoing a fix.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are translating a real conversation (a chat log, a comment thread) into "
    "{lang} so it can be read aloud in a video.\n"
    "You get a JSON array of numbered messages with their authors, in order. Translate "
    "the ones that are NOT already in {lang}. Return ONLY the ones you translated.\n"
    "\n"
    "Rules, in order of importance:\n"
    "1. LEAVE ALONE anything that is deliberately in another language. A message "
    "otherwise in {lang} with a foreign word or phrase inside it keeps that word "
    "exactly as it is — the foreignness is the joke, the slang or the quotation, and "
    "translating it destroys the line. The same goes for a whole short message that "
    "is a catchphrase, a meme, a brand, a command, a file name or an error message.\n"
    "2. Keep every nickname, @handle, link, number and emoji exactly as written. "
    "Never translate a person's name.\n"
    "3. Keep the REGISTER. People in a chat are not writing essays: keep it as short, "
    "as rude, as sloppy and as unpunctuated as the original. Do not tidy anything up.\n"
    "4. Keep each message about as long as it was — it has to be read aloud in the "
    "same breath and drawn in the same bubble.\n"
    "5. A message that is already in {lang} is not returned at all, even if you think "
    "it could be phrased better. You are not editing this conversation.\n"
    "\n"
    'Respond with JSON only: {{"messages": [{{"i": 3, "text": "..."}}, ...]}}. '
    "An empty list is the right answer when the conversation is already in {lang}."
)

# Messages per request. A conversation is read as a whole or not at all — the rule
# above needs the surrounding lines to apply — so the window is generous, and the cap
# only exists so that a thousand-message import is not one irrecoverable request.
MAX_PER_CALL = 60
# …and how many messages of the previous window travel with the next one, read-only,
# so the model keeps hearing the conversation across a seam it did not choose.
OVERLAP = 6

LANG_NAMES = {
    "ru": "Russian", "en": "English", "es": "Spanish", "de": "German",
    "fr": "French", "pt": "Portuguese", "it": "Italian", "pl": "Polish",
    "uk": "Ukrainian", "tr": "Turkish", "ja": "Japanese", "zh": "Chinese",
}


def _windows(n: int) -> list[tuple[int, int]]:
    """(first to translate, first beyond this window), overlapping by `OVERLAP`."""
    if n <= MAX_PER_CALL:
        return [(0, n)]
    out = []
    start = 0
    while start < n:
        out.append((start, min(start + MAX_PER_CALL, n)))
        start += MAX_PER_CALL
    return out


def translate(ctx, job) -> int:
    """Translate the job's conversation in place. Returns how many messages changed.

    Never raises. A translation that fails leaves the conversation in the language it
    arrived in, which is a video the operator can still look at and fix — where
    taking the run down over it would lose the fetch that found the conversation in
    the first place."""
    lang = ctx.params.lang
    want = LANG_NAMES.get(lang, lang)
    todo = [i for i, m in enumerate(job.messages) if m.text.strip() and not m.pinned]
    if not todo:
        return 0

    changed = 0
    for lo, hi in _windows(len(job.messages)):
        # the window itself, plus a few before it for context only
        sent = list(range(max(lo - OVERLAP, 0), hi))
        editable = {i for i in range(lo, hi) if i in set(todo)}
        payload = [
            {"i": i, "who": job.messages[i].persona, "text": job.messages[i].text}
            for i in sent
        ]
        try:
            data = ctx.llm.complete_json(
                "chat_translate", _SYSTEM.format(lang=want),
                json.dumps(payload, ensure_ascii=False, indent=1),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("translating %d message(s) failed (%s) — left as they were",
                        len(editable), e)
            continue
        for item in data.get("messages") or []:
            if not isinstance(item, dict):
                continue
            i, text = item.get("i"), str(item.get("text", "")).strip()
            # Only a message we asked about may change. A model that also "improved" a
            # context line would be editing a message it was shown for reference, and
            # on the next window it would be editing its own output.
            if not isinstance(i, int) or i not in editable or not text:
                continue
            msg = job.messages[i]
            if text == msg.text:
                continue
            if not msg.source_text:
                msg.source_text = msg.text
            msg.text = text
            changed += 1
        ctx.progress("translate", min(hi, len(job.messages)), len(job.messages))

    log.info("chat: translated %d of %d messages into %s", changed, len(job.messages), want)
    return changed


# --------------------------------------------------------------------------
# choosing what to tell
# --------------------------------------------------------------------------

_PICK = (
    "You are choosing which threads are worth making a short narrated video out of. "
    "The video reads the thread aloud over a picture of the comments, so what makes a "
    "good one is narrow and is NOT the same as what makes a popular one.\n"
    "Judge each on:\n"
    "  • Does something HAPPEN? A question answered, a plan that goes wrong, somebody "
    "caught out, a thing that turns out not to be what it looked like. A thread of "
    "opinions about a topic is not a story however many people hold them.\n"
    "  • Can it be READ? It has to work as words out loud: no pictures to look at, no "
    "table, no code, no link that is the whole point, no in-joke that needs the "
    "subreddit's history.\n"
    "  • Is it SELF-CONTAINED? It has to land inside a minute or two without a reader "
    "needing to know who anybody is.\n"
    "  • Is it CONCRETE? One specific situation with specific details beats a general "
    "question every time, however many upvotes the general question has.\n"
    "Score is evidence of attention, not of any of the above, and a thread with a "
    "tenth of the votes is the right answer whenever it is the one with a story in it."
    "\n{steer}"
    'Respond with JSON only: {{"pick": [{{"i": <number>, "why": "<six words>"}}, ...]}}, '
    "best first, at most {want}. Return fewer than asked for rather than padding the "
    "list with threads that fail the tests above — an empty list is a legitimate "
    "answer when none of them are stories."
)


def pick(ctx, rows: list[dict], want: int) -> list[int]:
    """Which of these are worth a video, best first.

    Never raises and never returns nonsense: a model that is unavailable, slow or
    unhelpful costs the JUDGEMENT and not the run, and the caller's fallback is the
    listing's own order — which is what a human skimming would start from anyway."""
    if not rows:
        return []
    want = max(1, min(int(want), len(rows)))
    brief = (getattr(ctx.params, "scenario", "") or "").strip()
    steer = (f"The operator has asked for: {brief}\nPrefer threads that fit it, and "
             "say so in `why`. A thread that fits nothing they asked for is not a "
             "candidate however good it is.\n") if brief else ""
    payload = [
        {"i": n, "title": r.get("title", ""), "score": r.get("score", 0),
         "comments": r.get("comments", 0), "text": str(r.get("text", ""))[:400]}
        for n, r in enumerate(rows)
    ]
    try:
        data = ctx.llm.complete_json(
            "chat_pick", _PICK.format(want=want, steer=steer),
            json.dumps(payload, ensure_ascii=False, indent=1))
    except Exception as e:  # noqa: BLE001
        log.warning("choosing threads failed (%s) — taking them in the order they came", e)
        return []
    out: list[int] = []
    for item in data.get("pick") or []:
        if not isinstance(item, dict):
            continue
        i = item.get("i")
        if isinstance(i, int) and 0 <= i < len(rows) and i not in out:
            out.append(i)
            log.info("chat: picked %r — %s", rows[i].get("title", "")[:60],
                     str(item.get("why", ""))[:60])
    return out[:want]


# --------------------------------------------------------------------------
# writing one from nothing
# --------------------------------------------------------------------------

_INVENT = (
    "You are writing a conversation that will be drawn as a messenger screenshot and "
    "read aloud. It has to pass for a real one, and what gives a written one away is "
    "always the same handful of things.\n"
    "\n"
    "People type badly and briefly. Lower case, no full stop at the end, a typo left "
    "where it fell, a word cut short. Most lines are under ten words. Somebody sends "
    "two or three in a row instead of one long one, because they thought of the next "
    "bit after pressing send. Nobody delivers a paragraph and nobody speaks in "
    "complete sentences with commas in the right places.\n"
    "\n"
    "Nobody narrates. There is no description of what anyone is doing, no stage "
    "direction, no asterisks, no emoji standing in for a feeling nobody would type. "
    "What a reader learns, they learn because somebody said it to somebody who needed "
    "telling — and people do not tell each other what they both already know.\n"
    "\n"
    "Something has to HAPPEN, and it has to be ordinary. A plan that goes wrong, a "
    "thing that turns out not to be what it looked like, somebody caught out, a "
    "question whose answer costs more than expected. One concrete situation with "
    "specific details — a number, a place, a time, the name of a thing — not a topic "
    "being discussed. The failure to avoid is a conversation where people exchange "
    "opinions and nothing moves.\n"
    "\n"
    "It opens in the middle. The first message is not a greeting and not a setup; it "
    "is somebody already saying the thing. It ends without a bow: the last line is "
    "short, and it is somebody's reply rather than a conclusion.\n"
    "\n"
    "Write in {lang}. Between {lo} and {hi} messages, {who}.\n"
    "{steer}"
    'Respond with JSON only: {{"title": "<what the chat is called, 1-3 words>", '
    '"messages": [{{"who": "<name>", "text": "...", "reply_to": <index or -1>, '
    '"reactions": [["<emoji>", <count>]]}}, ...]}}. '
    "`reply_to` is the index of an earlier message in this list and is -1 for almost "
    "all of them — a reply is for when somebody answers something two or three lines "
    "back, not the line above. Reactions on at most two messages in the whole "
    "conversation, and only where somebody would actually bother."
)


def invent(ctx, topic: str = "", want: int = 1, cast: list[str] | None = None,
           length: tuple[int, int] = (10, 22)) -> list:
    """Write `want` conversations from nothing, and hand them back as pieces.

    The same shape an export parses into (`chat.exports.Piece`), so everything
    downstream treats a written conversation exactly as a found one — which is the
    point: by the time the room sees it, where it came from is not supposed to matter.

    Raises on failure rather than returning nothing. Unlike choosing, this IS the
    material: a run that cannot write its conversation has no video to make, and
    pretending otherwise would park an empty room in front of the operator with
    nothing saying why."""
    from ..chat.exports import Line, Piece

    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    names = [n for n in (cast or []) if str(n).strip()]
    who = (f"between these people and nobody else: {', '.join(names)}" if names
           else "between two or three people, named as people are actually called")
    steer = (f"What it is about: {topic.strip()}\n" if topic.strip()
             else "Choose the situation yourself. Something mundane and specific — a "
                  "delivery, a shared flat, a shift nobody wants, a thing lent and not "
                  "returned — and not a crisis.\n")
    system = _INVENT.format(lang=lang, lo=length[0], hi=length[1], who=who, steer=steer)
    out = []
    for n in range(max(1, int(want))):
        ask = "Write it." if n == 0 else (
            f"Write another one, unrelated to the {n} before it: other people, another "
            "situation, another shape. Do not continue what you have already written.")
        data = ctx.llm.complete_json("chat_invent", system, ask, attempt=n)
        lines = []
        for item in data.get("messages") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            at = item.get("reply_to")
            lines.append(Line(
                who=str(item.get("who") or "").strip(),
                text=text,
                reply_to=at if isinstance(at, int) and 0 <= at < len(lines) else -1,
                reactions=[(str(e), max(1, int(c))) for e, c in
                           (item.get("reactions") or []) if str(e).strip()],
            ))
        if not lines:
            raise ValueError("the writer came back with no messages")
        out.append(Piece(title=str(data.get("title") or "").strip(),
                         source="written", lines=lines))
        ctx.progress("invent", n + 1, max(1, int(want)))
    log.info("chat: wrote %d conversation(s), %d line(s)",
             len(out), sum(len(p.lines) for p in out))
    return out
