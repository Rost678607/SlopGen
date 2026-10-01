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
