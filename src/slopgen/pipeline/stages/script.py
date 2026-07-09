"""Stage 2: write the voiceover script as scenes with stock-search keywords.

When native ad mode is on, the LLM weaves an ad mention into the script and
marks that scene with is_ad=true; its visuals are later taken from the ad's
pre-made clips instead of stock footage.
"""

from __future__ import annotations

import json
import re

from ..context import AppContext
from ..job import InsertCue, Scene, VideoJob
from .idea import LANG_NAMES

SYSTEM = (
    "You write voiceover scripts for viral vertical short videos. "
    "Target spoken length: about {duration:.0f} seconds ≈ {words:.0f} words total. "
    "Respond with JSON only:\n"
    '{{"scenes": [{{"text": "<1-2 spoken sentences>", "keywords": ["<2-4 words>", ...]}}, ...]}}\n'
    "Rules: scale the number of scenes to the target length (a scene is 5-9 seconds of speech); "
    "the first scene is a shocking hook; short punchy sentences; "
    "no scene numbering, no stage directions — only spoken words in \"text\". "
    "\"keywords\" are ALWAYS in English: concrete, visual stock-footage search phrases matching the scene."
)

VISUALS_RULES = (
    '\nAdditionally give every scene a "visuals" array: one short English photo-search query '
    "per ~{beat:.0f} seconds of that scene's speech (so 1-3 per scene), each tied to what is being said "
    "at that exact moment — name the concrete subject (a place, a person type, an object). "
    "Example: narration mentions Switzerland then a young couple then a puppy → "
    '"visuals": ["bern switzerland city", "young couple talking", "small puppy"].'
)

# Foreground inserts are event-driven, NOT on a timer: the model chooses which
# spoken phrases deserve a picture; each insert shows only while that phrase is
# spoken and vanishes afterwards (timing comes from edge-tts word timings).
INSERTS_RULES = (
    '\nAlso give every scene an "inserts" array (may be empty) of foreground picture cues. '
    "Add a cue ONLY when the narration names a concrete, showable thing worth flashing on screen "
    "(a specific place, person, object, event); skip abstract or filler sentences. "
    'Each cue is {{"query": "<short English image search>", "phrase": "<the EXACT words, copied '
    'verbatim from THIS scene\'s text, during which the picture should be visible>"}}. '
    "The phrase must be a contiguous substring of the scene text. Use 0-2 cues per scene. "
    'Example for text "the vandal sprayed graffiti on a train": '
    '"inserts": [{{"query": "graffiti on train car", "phrase": "graffiti on a train"}}].'
)

WEB_FACTS_RULES = (
    "\nIMPORTANT: you have a `web_search` tool. Before writing, call it to verify the real facts of the "
    "topic — names, dates, numbers, who actually did what. Base every factual claim strictly on those "
    "search results; do NOT invent people, nicknames, companies or events. If the real story differs from "
    "the topic's premise, tell the REAL, verified version rather than a made-up one."
)

# Swear-word vocabulary anchors per language so the model knows EXACTLY what is wanted.
_SWEAR_VOCAB: dict[str, str] = {
    "ru": "хуй/нахуй/похуй/хуйня, пизда/пиздец/пиздато/в пизду, ёбаный/заебись/ёб твою мать, блядь/блять, сука, мудак/мудила",
    "en": "fuck/fucking/fucked, shit/bullshit/shitty, ass/asshole/dumbass, bitch/son of a bitch, bastard, damn, cunt",
}

# Regex stems for post-generation profanity counting (used to trigger a retry).
_PROFANITY_STEMS: dict[str, str] = {
    "ru": r"блядь|блять|хуй|пизд|ёбан|заеб|наеб|сука|мудак|залуп|хуяр|ёбн|ёб[её]",
    "en": r"\bfuck|\bshit|\bass\b|\bbitch|\bbastard|\bdamn\b|\bcunt\b",
}


def profanity_rule(level: int, lang: str = "en") -> str:
    """Scale swearing in the narration from clean (0) to constant (100)."""
    vocab = _SWEAR_VOCAB.get(lang, _SWEAR_VOCAB["en"])
    if level <= 0:
        return "\nKeep the language clean — no profanity or swearing at all."
    if level <= 25:
        return (
            f"\nTone: mostly clean, but drop in a mild swear word 1-2 times for emphasis. "
            f"Use words like: {vocab}."
        )
    if level <= 50:
        return (
            f"\nTone: casual and edgy — include profanity roughly every other scene, "
            f"where it adds punch. Use words like: {vocab}."
        )
    if level <= 75:
        return (
            f"\nTone: crude and heavily profane — at least one swear word per scene, "
            f"most sentences should contain one. "
            f"Use these words and their derivatives: {vocab}. Weave them in naturally."
        )
    return (
        f"\nMANDATORY STYLE — relentlessly vulgar: EVERY sentence must contain at least one "
        f"profane word. No clean sentences allowed (except unavoidable proper nouns/technical terms). "
        f"Use these words and their derivatives: {vocab}. "
        "After writing each sentence, verify it has a swear word — if not, add one. "
        "The script should read like an angry foul-mouthed commentator who cannot stop swearing."
    )


def _count_profanity_text(text: str, lang: str) -> int:
    pattern = _PROFANITY_STEMS.get(lang, _PROFANITY_STEMS["en"])
    return len(re.findall(pattern, text.lower()))


def _count_profanity(scenes: list[Scene], lang: str) -> int:
    """Count profane word hits across all scene texts."""
    return _count_profanity_text(" ".join(s.text for s in scenes), lang)


# A dedicated, single-purpose rewrite pass. Asking for profanity inside the big
# script prompt (which also demands facts, keywords, visuals, inserts) is
# unreliable — the constraint gets buried and models often drop it entirely,
# especially with web-search grounding pushing a "professional" tone. Isolating
# it as its own focused task makes compliance far more consistent.
#
# The critical part is NATURAL placement: the profanity must be woven into the
# grammar (as verbs, adjectives, nouns, intensifiers), NOT dumped in as loose
# interjections. So the pass gives the model freedom to reword/restructure and
# shows concrete good-vs-bad examples — forcing "same length, don't restructure"
# is exactly what produces the tacked-on, grammar-breaking result.
_INJECT_SYSTEM = (
    "You are a foul-mouthed {lang} blogger rewriting voiceover narration to be crude and vulgar. "
    "You are given a JSON array of scene texts. Rewrite EACH one so the swearing is woven "
    "NATURALLY into the grammar — as verbs, adjectives, nouns and intensifiers that fit the "
    "sentence like a native speaker cursing — never as a loose word dumped in the middle. "
    "Keep the same facts, names and numbers, but you may reword and restructure freely. "
    "Keep the array in the SAME order and count (one rewritten text per input text).\n"
    "Swear vocabulary to draw from (use these and their grammatical forms): {vocab}.\n"
    "GOOD, natural (do this):\n"
    "  • «31 марта 2026 случится полный пиздец — распиздяи из Anthropic случайно выложили весь код.»\n"
    "  • «Энтузиасты сразу начали пиздить код и вайбкодить open-source форки нахуй.»\n"
    "BAD, tacked-on (never do this — it breaks the grammar):\n"
    "  • «Виновник — пиздец, файл sourcemap попал в npm.»\n"
    "  • «Anthropic попыталась заткнуть эту пизда утечку.»\n"
    '(Above examples are Russian; apply the same principle in {lang}.) '
    'Respond with JSON only: {{"texts": ["<rewritten text 1>", ...]}}.'
)


def _inject_intensity(level: int) -> str:
    if level <= 25:
        return "Sprinkle a mild curse into about a quarter of the texts — subtle, for emphasis only."
    if level <= 50:
        return "Work a swear word naturally into roughly every other text, where it lands."
    if level <= 75:
        return "Most texts should carry a swear word or two, baked into the phrasing — crude but readable."
    return "Every text should be loaded with strong profanity, woven right through the phrasing — a relentless foul-mouthed rant."


def _inject_profanity(texts: list[str], level: int, lang: str, llm) -> list[str] | None:
    """Focused rewrite pass that injects profanity at `level`. Returns rewritten
    texts of identical length/order, or None if the model didn't cooperate."""
    vocab = _SWEAR_VOCAB.get(lang, _SWEAR_VOCAB["en"])
    lang_name = LANG_NAMES.get(lang, lang)
    system = _INJECT_SYSTEM.format(lang=lang_name, vocab=vocab)
    user = (
        f"Profanity level: {level}/100. {_inject_intensity(level)}\n"
        f"Sentences:\n{json.dumps(texts, ensure_ascii=False)}"
    )
    try:
        data = llm.complete_json("profanity", system, user, web_search=False)
    except Exception:
        return None
    out = data.get("texts")
    if not isinstance(out, list) or len(out) != len(texts):
        return None
    return [str(t).strip() or orig for t, orig in zip(out, texts)]


AD_RULES = (
    '\nAdditionally insert EXACTLY ONE extra scene with "is_ad": true placed at roughly 60-70% of the video. '
    "In it, naturally weave a short spoken ad mention (1-2 sentences, same narration voice and mood, "
    'mention "link in the description") based on these talking points: {points}. '
    'Give it "keywords": [].'
)


def run(job: VideoJob, ctx: AppContext) -> None:
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    brief = ctx.content.script_brief.get(ctx.params.lang) or next(iter(ctx.content.script_brief.values()))
    duration = ctx.params.duration_s
    system = SYSTEM.format(duration=duration, words=duration * 2.4)
    vis = ctx.visuals
    # background photo slideshow (stock or AI) → per-beat "visuals" queries
    if (
        vis.background.linkage == "narration"
        and vis.background.source.startswith(("stock", "ai"))
        and not vis.background.source.endswith("video")
    ):
        system += VISUALS_RULES.format(beat=max(vis.background.interval_s, 2.5))
    # foreground → phrase-anchored "inserts"
    if vis.foreground.enabled:
        system += INSERTS_RULES
    # web search on → force real facts instead of invented drama
    if ctx.llm_web_search:
        system += WEB_FACTS_RULES
    system += profanity_rule(ctx.params.profanity, ctx.params.lang)
    if ctx.native_ad_on:
        system += AD_RULES.format(points=ctx.ad.native.talking_points)
    user = (
        f"Topic: {job.topic}\n"
        f"Style brief: {brief}\n"
        f"Write all \"text\" in {lang}. Keywords stay in English."
    )
    # Reinforce profanity requirement in the user turn for high levels — models weight
    # the user message heavily and this prevents the instruction from being buried.
    if ctx.params.profanity > 50:
        vocab = _SWEAR_VOCAB.get(ctx.params.lang, _SWEAR_VOCAB["en"])
        user += (
            f"\n\nReminder: profanity level is {ctx.params.profanity}/100 — "
            f"every sentence MUST contain at least one swear word from: {vocab}."
        )

    data = ctx.llm.complete_json("script", system, user, web_search=ctx.llm_web_search)
    scenes = _parse_scenes(data)
    if not scenes:
        raise ValueError("LLM returned an empty script")

    # Guarantee the requested swearing level: if the model under-delivered (it
    # very often does — the constraint gets buried in the main prompt), run a
    # dedicated profanity-injection pass that rewrites the scene texts. Inserts
    # anchor on shared content words, so added swearing doesn't break them.
    level = ctx.params.profanity
    if level > 0:
        expected = -(-len(scenes) * level // 100)  # ceil: fraction of scenes that should swear
        if _count_profanity(scenes, ctx.params.lang) < expected:
            rewritten = _inject_profanity([s.text for s in scenes], level, ctx.params.lang, ctx.llm)
            if rewritten:
                for s, t in zip(scenes, rewritten):
                    s.text = t

    # keep at most one ad scene even if the model over-delivers
    seen_ad = False
    for s in scenes:
        if s.is_ad and seen_ad:
            s.is_ad = False
        seen_ad = seen_ad or s.is_ad
    job.scenes = scenes


def _parse_scenes(data: dict) -> list[Scene]:
    return [
        Scene(
            text=s["text"].strip(),
            keywords=[k.strip() for k in s.get("keywords", [])],
            visual_queries=[q.strip() for q in s.get("visuals", []) if q.strip()],
            insert_cues=[
                InsertCue(query=c["query"].strip(), phrase=c.get("phrase", "").strip())
                for c in s.get("inserts", [])
                if isinstance(c, dict) and c.get("query", "").strip()
            ],
            is_ad=bool(s.get("is_ad")),
        )
        for s in data.get("scenes", [])
        if s.get("text", "").strip()
    ]
