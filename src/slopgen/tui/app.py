"""Textual TUI: configure everything first, press GENERATE, walk away.

Layout conventions:
  - no Footer; a custom TopBar docks on top with "<-" (back) left of "Palette"
  - screens with sections use a vertical tab list on the left (arrow keys work),
    content is centered in the remaining space
  - every label goes through the I18N table; the RU/EN button in the TopBar
    switches the interface language and persists it to configs/slopgen.toml
  - custom "minecraft" theme is registered; the chosen theme persists across runs
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tomllib
import zlib
from pathlib import Path

import tomli_w
from dotenv import load_dotenv
from textual import events, on
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
    TextArea,
)

from ..config import ConfigError, ConfigStore, RunParams, VisualsConfig
from ..config.envfile import set_env_var
from ..config.loader import fandom_docs, lore_sha, read_lore, write_fandom
from ..config.models import (
    AdConfig,
    AdDescriptionConfig,
    AdNativeConfig,
    AdOverlayConfig,
    CharacterConfig,
    FandomConfig,
    LLMProfile,
    OrchestrationConfig,
    OrchestrationStage,
    VisualsBackground,
    VisualsForeground,
)
from ..llm import MODEL_PRESETS, PROVIDERS, ChatLLM, resolve_provider
from ..llm import characters as char_ai
from ..llm import lore as lore_ai
from ..llm import rewrite as bp_ai
from ..media.generate import PHOTO_MODELS, VIDEO_MODELS
from ..media.generate import env_keys as gen_keys
from ..media.generate import key_var_for_model
from ..pipeline import Orchestrator, VideoJob, manual, parts, review
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.context import AppContext
from ..pipeline.stages import tts as tts_stage
from .forms import (
    Choice,
    FieldTextArea,
    Form,
    Group,
    Heading,
    Note,
    NumStep,
    Number,
    Range,
    Text,
    Toggle,
    resize_text_field,
)

# slider bucket captions (threshold -> i18n key)
PROFANITY_LABELS = {0: "prof_none", 1: "prof_mild", 26: "prof_mod", 51: "prof_heavy", 76: "prof_max"}
TTS_RATE_LABELS = {-50: "rate_very_slow", -25: "rate_slow", 0: "rate_normal", 25: "rate_fast", 50: "rate_very_fast"}

# Curated voice lists per content language (label, edge-tts voice id).
# Labels are language-neutral proper names so they read correctly in any UI language.
EDGE_TTS_VOICES: dict[str, list[tuple[str, str]]] = {
    "ru": [
        ("Dmitry ♂", "ru-RU-DmitryNeural"),
        ("Svetlana ♀", "ru-RU-SvetlanaNeural"),
    ],
    "en": [
        ("Guy ♂ (US)", "en-US-GuyNeural"),
        ("Jenny ♀ (US)", "en-US-JennyNeural"),
        ("Aria ♀ (US)", "en-US-AriaNeural"),
        ("Davis ♂ (US)", "en-US-DavisNeural"),
        ("Ryan ♂ (GB)", "en-GB-RyanNeural"),
        ("Sonia ♀ (GB)", "en-GB-SoniaNeural"),
        ("Natasha ♀ (AU)", "en-AU-NatashaNeural"),
        ("William ♂ (AU)", "en-AU-WilliamNeural"),
    ],
}

LOGO = r"""
 ███████╗██╗      ██████╗ ██████╗  ██████╗ ███████╗███╗   ██╗
 ██╔════╝██║     ██╔═══██╗██╔══██╗██╔════╝ ██╔════╝████╗  ██║
 ███████╗██║     ██║   ██║██████╔╝██║  ███╗█████╗  ██╔██╗ ██║
 ╚════██║██║     ██║   ██║██╔═══╝ ██║   ██║██╔══╝  ██║╚██╗██║
 ███████║███████╗╚██████╔╝██║     ╚██████╔╝███████╗██║ ╚████║
 ╚══════╝╚══════╝ ╚═════╝ ╚═╝      ╚═════╝ ╚══════╝╚═╝  ╚═══╝
"""

NONE = "__none__"
MANUAL = "__manual__"
CUSTOM = "__custom__"

# (key, {lang: (label, description)}) — English label is sent to the LLM
DRAMA_TROPES: list[tuple[str, dict[str, tuple[str, str]]]] = [
    ("system",      {
        "en": ("System / Gacha (MC gets a game-like interface or system)",
               "MC gets a game-like HUD: quests, stats, rewards, penalties"),
        "ru": ("Система", "ГГ получает игровой интерфейс: задания, характеристики, штрафы"),
    }),
    ("rebirth",     {
        "en": ("Reincarnation / Transmigration",
               "MC dies and wakes up in another body or world, with memories intact"),
        "ru": ("Перерождение", "ГГ умирает и возрождается — в чужом теле, мире или прошлом"),
    }),
    ("revenge",     {
        "en": ("Revenge (betrayal → cold comeback)",
               "MC was betrayed or humiliated and returns to settle the score"),
        "ru": ("Месть", "ГГ предали или унизили — теперь холодный расчётливый возврат"),
    }),
    ("family",      {
        "en": ("Family betrayal / Hidden heir",
               "Family schemes, fake children, lost heirs, blood secrets"),
        "ru": ("Семья", "Предательство родных, подменённые дети, тайное наследство"),
    }),
    ("reputation",  {
        "en": ("Reputation / Public humiliation → reversal",
               "Everyone thinks MC is worthless — until a single moment flips it all"),
        "ru": ("Репутация", "Все считают ГГ ничтожеством — один момент переворачивает всё"),
    }),
    ("artifact",    {
        "en": ("Magic artifact / Portal / Gate to another world",
               "An object (vase, mirror, ring…) connects worlds or grants forbidden power"),
        "ru": ("Артефакт (связь миров)", "Предмет (ваза, зеркало, кольцо…) связывает миры или даёт запретную силу"),
    }),
    ("cultivation", {
        "en": ("Cultivation / Power levels / Spiritual roots",
               "MC rises through ranked power tiers via training, breakthroughs, or rare talent"),
        "ru": ("Культивация", "Уровни силы, духовные корни, прорывы — рост через практику"),
    }),
    ("apocalypse",  {
        "en": ("Apocalypse / End of the world",
               "The world is ending or already collapsed; survival and hope are the core"),
        "ru": ("Конец света", "Мир гибнет или уже рухнул — выживание и надежда в центре"),
    }),
    ("trial",       {
        "en": ("Trial / Hidden test (e.g. billionaire's inheritance challenge)",
               "A powerful figure sets a secret test; passing it brings life-changing reward"),
        "ru": ("Проверка / Испытание", "Тайное испытание от влиятельного лица — приз меняет жизнь ГГ"),
    }),
]

MINECRAFT_THEME = Theme(
    name="minecraft",
    primary="#5EBB2B",  # grass
    secondary="#825432",  # dirt
    accent="#4AEDD9",  # diamond
    foreground="#E0E0E0",
    background="#1D1D21",  # deepslate
    surface="#2B2B2E",  # stone
    panel="#3C3C3F",
    success="#5EBB2B",
    warning="#FFAA00",  # gold
    error="#FF5555",  # redstone
    dark=True,
    # The identity palette — colours that mean NOTHING, used to tell peers apart (see
    # "Identity colour" below). It lives with the theme because a colour that sits well
    # beside grass and deepslate sits badly beside something else. These six are ore and
    # block colours chosen to satisfy `palette_faults` against this theme: none of them
    # comes near grass, gold, redstone or diamond, none of them looks like another, and
    # each lifts into readable text without going chalky. Change one and run
    # `palette_faults` — it is what stops a pretty colour that reads as a warning.
    variables={
        "identity": "#3B62C4,#B26A3C,#CBB47A,#B9BCC6,#B5486B,#8A6A8C",
        # lapis · terracotta · sandstone · iron · crimson stem · mushroom stem
    },
)

# --------------------------------------------------------------------------
# Identity colour
# --------------------------------------------------------------------------
#
# A stable colour per name, meaning NOTHING. It is not decoration for its own sake:
# a colour the eye can rely on turns a list of peers into things you recognise
# instead of read — "Марта is the blue one" works on a cast of fifteen even though
# blue says nothing about her. Avatar colours, essentially.
#
# The palette BELONGS TO THE THEME (`Theme.variables["identity"]`), because a colour
# that is right next to grass and deepslate is wrong next to something else — and
# because a theme that ships no palette should fall back, not look broken.
#
# The rules below are code rather than prose, so a palette cannot quietly drift out
# of them: `palette_faults` returns what is wrong with one, and the test at the bottom
# of this module's docstring is simply that it returns nothing for every theme we ship.


def _lab(color: Color) -> tuple[float, float, float]:
    """CIE L*a*b* for a colour, so "how different do these look" is a distance rather
    than a difference in numbers that happen to be stored next to each other."""
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_lin(v / 255) for v in (color.r, color.g, color.b))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def _f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a: str | Color, b: str | Color) -> float:
    """How far apart two colours look (CIE76). Under ~20 most people call them the
    same colour at a glance; under ~10 they are the same colour."""
    la, aa, ba = _lab(Color.parse(a))
    lb, ab, bb = _lab(Color.parse(b))
    return ((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2) ** 0.5


def _contrast(a: str | Color, b: str | Color) -> float:
    """WCAG contrast ratio, 1 (identical) to 21 (black on white)."""
    def _l(c: Color) -> float:
        def _lin(v: float) -> float:
            v /= 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (_lin(v) for v in (c.r, c.g, c.b))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    x, y = sorted((_l(Color.parse(a)), _l(Color.parse(b))), reverse=True)
    return (x + 0.05) / (y + 0.05)


# The rules, as numbers. Each one is a way an earlier attempt went wrong: a palette
# that borrowed the theme's gold made a decorative button look like a warning, one
# picked by eye gave two neighbours in a cast the same blue, and one picked to be
# readable as text came out pastel and had nothing to do with a world of grass and
# deepslate.
#
# That last failure is why a palette entry is a BASE colour that `identity_ink` lifts
# until it reads, rather than a colour used as-is. Deep lapis is a fine base and
# illegible as a word on deepslate; requiring the base itself to be readable is what
# forced the palette pale in the first place. The rules below are therefore checked
# against both the base and the ink derived from it.
SEMANTIC_GAP = 30.0   # from any colour the theme uses to MEAN something
PEER_GAP = 25.0       # from every other colour in the palette, in BOTH roles
TEXT_CONTRAST = 4.0   # what `identity_ink` lifts a colour until it reaches
MAX_LIFT = 0.86       # ...but never past this lightness, or every hue turns to chalk


def identity_ink(color: str | Color, ground: str | Color = "#1D1D21") -> str:
    """The palette colour as TEXT on `ground`: the same hue, moved until it reads.

    AWAY from the ground, which is the whole point and the thing the first version got
    wrong: it always lightened, so on a light theme it walked every colour towards the
    background it was supposed to stand out from and the palette came back empty.

    Returns the original when it already reads. A hue that cannot reach the contrast
    before it runs out of room stops there — `palette_faults` is what catches those,
    rather than letting them ship as unreadable."""
    c = Color.parse(color)
    if _contrast(c, ground) >= TEXT_CONTRAST:
        return c.hex
    h, sat, lum = c.hsl
    _, _, ground_lum = Color.parse(ground).hsl
    step = -0.02 if ground_lum > 0.5 else 0.02  # dark ink on a light ground, and back
    limit = 1.0 - MAX_LIFT if step < 0 else MAX_LIFT
    while (lum > limit) if step < 0 else (lum < limit):
        lum = min(max(lum + step, 0.0), 1.0)
        c = Color.from_hsl(h, sat, lum)
        if _contrast(c, ground) >= TEXT_CONTRAST:
            break
    return c.hex


# The bevel, measured off the app's own buttons rather than guessed. A `success`
# button renders body #63BC34 between a top edge of #89E555 and a bottom of #0C7E00 —
# which is `lighten(0.15)` and `darken(0.25)`, Textual's own colour maths. Using the
# same two calls is what makes an identity-coloured button look like it was cut from
# the same sheet as the rest, instead of merely beveled by something.
#
# Textual applies this to its VARIANTS. It does not apply it to a border colour set
# directly: an inline `border: tall <colour>` paints both edges in that one colour,
# which is a flat rectangle. Hence computing it here — and only for top and bottom,
# because that is all the real buttons have. Adding left and right (the obvious
# spelling of "give it a border") turns the edges into a frame, which is the other
# half of why these looked wrong.
BEVEL_LIGHTEN, BEVEL_DARKEN = 0.15, 0.25


def identity_bevel(color: str | Color) -> tuple[str, str, str, str]:
    """A button face in this colour: (body, text, top edge, bottom edge)."""
    c = Color.parse(color)
    return (c.hex, c.get_contrast_text().hex,
            c.lighten(BEVEL_LIGHTEN).hex, c.darken(BEVEL_DARKEN).hex)


def palette_faults(palette: list[str], theme: Theme) -> list[str]:
    """Everything wrong with `palette` under this theme — empty when it is sound."""
    faults: list[str] = []
    ground = theme.background or "#000000"
    semantic = {"primary": theme.primary, "success": theme.success,
                "warning": theme.warning, "error": theme.error, "accent": theme.accent}
    inks = [identity_ink(c, ground) for c in palette]
    for c, ink in zip(palette, inks):
        # the base is not the only thing that reaches the screen: a filled surface shows
        # a LIT edge too, and a lifted teal lands squarely on the diamond accent — which
        # is how a decorative button came to wear the colour that means "focused"
        _, _, lit, _ = identity_bevel(c)
        for label, shown in (("", c), (" lit", lit)):
            for name, sem in semantic.items():
                if sem and color_distance(shown, sem) < SEMANTIC_GAP:
                    faults.append(
                        f"{c}{label} reads as {name} ({sem}): {color_distance(shown, sem):.0f}"
                    )
        if _contrast(ink, ground) < TEXT_CONTRAST:
            faults.append(f"{c} cannot be lifted into readable text on {ground}")
        for surf in (theme.surface,):
            if surf and _contrast(ink, surf) < TEXT_CONTRAST - 0.7:
                faults.append(f"{c} is thin on {surf}: {_contrast(ink, surf):.1f}:1")
    for role, values in (("as bases", palette), ("as text", inks)):
        for i, a in enumerate(values):
            for b in values[i + 1:]:
                if color_distance(a, b) < PEER_GAP:
                    faults.append(f"{a} and {b} look alike {role}: {color_distance(a, b):.0f}")
    return faults


IDENTITY_COUNT = 6  # more than a list usually needs; enough that peers rarely collide


def derive_identity(theme: Theme, count: int = IDENTITY_COUNT) -> list[str]:
    """Build an identity palette out of the theme itself.

    A hardcoded list is the same list under every theme, which is exactly the thing an
    identity colour must not be: it is supposed to live inside the theme's world, and a
    theme changes that world. So the hues are spread around the wheel and dressed in
    the theme's OWN saturation and lightness — taken from its primary, the colour it
    considers normal — and then put through `palette_faults`, which throws out whatever
    lands on a colour that already means something here. What survives is a palette in
    the theme's key that cannot be mistaken for its semantics.

    Walking the wheel in a wide, odd-numbered step rather than in order is what keeps
    the first few (the ones a short list actually uses) from being neighbours."""
    base = Color.parse(theme.primary or "#808080")
    start, sat, lum = base.hsl
    sat = min(max(sat, 0.35), 0.75)   # a grey theme still needs to tell peers apart
    lum = min(max(lum, 0.35), 0.62)   # and a very pale or very dark one still needs ink
    wheel = 24
    out: list[str] = []
    # Two passes. The first holds every rule; the second, only if the theme is so
    # crowded that the first could not fill the palette, relaxes how far a colour must
    # sit from the theme's semantics — a cramped palette is a real cost, while a
    # slightly-near colour is a small one, and an EMPTY palette is not an option at all
    # (identity_colors would have nothing to hand out).
    for relax in (1.0, 0.6):
        global SEMANTIC_GAP
        keep, SEMANTIC_GAP = SEMANTIC_GAP, SEMANTIC_GAP * relax
        try:
            for i in range(wheel):
                if len(out) >= count:
                    break
                hue = (start + 0.5 + (i * 11 / wheel)) % 1.0  # opposite primary, wide stride
                cand = Color.from_hsl(hue, sat, lum).hex
                if not palette_faults(out + [cand], theme):
                    out.append(cand)
        finally:
            SEMANTIC_GAP = keep
        if len(out) >= max(3, count // 2):
            break
    return out or [Color.from_hsl((start + 0.5) % 1.0, sat, lum).hex]


def theme_identity(theme: Theme | None) -> list[str]:
    """This theme's identity palette: the one it declares, else one derived from it.

    A theme that has an opinion states it in `variables["identity"]` — the shipped one
    does, because ore colours say "Minecraft" in a way an evenly-spaced wheel cannot.
    Everything else gets a palette built from its own primary."""
    if theme is None:
        return derive_identity(MINECRAFT_THEME)
    raw = (theme.variables or {}).get("identity", "")
    declared = [c.strip() for c in raw.split(",") if c.strip()]
    return declared or derive_identity(theme)


def identity_colors(keys: list[str], palette: list[str] | None = None) -> dict[str, str]:
    """Identity colours for a whole list, with collisions pushed apart.

    A plain hash is stable but clumps: five characters over six colours will routinely
    give three of them the same one, and a colour two neighbours share is worse than no
    colour at all — it says "these two go together" when nothing does. So each key takes
    its own colour when free and the next free one otherwise.

    `crc32` rather than `hash()`: Python randomizes string hashing per process, so the
    obvious spelling would give a character a different colour on every launch, which is
    precisely the one property this must not have."""
    pal = [c for c in (palette or []) if c] or ["#808080"]
    used: set[str] = set()
    out: dict[str, str] = {}
    for key in keys:
        start = zlib.crc32(str(key).encode("utf-8")) % len(pal)
        for step in range(len(pal)):
            colour = pal[(start + step) % len(pal)]
            if colour not in used or step == len(pal) - 1:
                out[key] = colour
                used.add(colour)
                break
    return out


I18N: dict[str, dict[str, str]] = {
    "en": {
        "subtitle": "industrial neuroslop pipeline",
        "menu.generate": "⛏  Generate videos",
        "menu.config": "⚙  Configuration",
        "menu.quit": "✖  Quit",
        "step.content": "Content",
        "step.characters": "Story",
        "step.fandom": "World",
        "step.visuals": "Visuals",
        "step.ads": "Ads",
        "step.publish": "Publish",
        "step.summary": "Summary",
        "mode_head": "What are we generating?",
        "fandom_duration_s": "Length, sec",
        "fandom_medium": "Made of",
        "fandom_medium_video": "🎬 video clips",
        "fandom_medium_photo": "🖼 photo slideshow",
        "help.fandom_duration_s": "How long the finished video runs, in seconds. There is no tolerance and no clip-length field: the writer sizes every shot itself to fit this, whatever the shots are made of.",
        "help.fandom_medium": "What the picture is made of. Clips play; stills are held and slowly panned. It also decides what can make them — a slideshow is drawn by an image generator, found as photographs, or drawn by hand as images — so the list below changes with it.",
        "fandom_source_note": "🙋 and 🔍 are you: with the first slopgen writes a prompt per shot and you generate it in an external tool, with the second it says what to find and hands you ready-made search queries. Either way the run pauses at the footage stage and `slopgen gather` resumes it once the files are in the inbox. `flux` and `turbo` make stills instead of clips; a still is held and panned to length, and one you supply yourself counts the same — deliver a .jpg or .png and it is treated as a photo.",
        "fandom_source_head": "— Where the shots come from —",
        "fandom_source": "Source",
        "help.fandom_source": "Where every shot in this video comes from. AI generation is unattended and free but slow and often off-model. Generating them yourself means pasting each prompt into Kling/Veo/Pika and handing the file back — the best picture, at the cost of your evening. Finding them yourself means slopgen briefs you per shot and you bring back real footage of real things, which is what a world of ordinary places usually wants.",
        "help.fandom_clip_s": "How long ONE shot runs on average. It decides how many shots the piece is cut into and how much narration each carries.",
        "bg_manual": "I supply the background myself",
        "fg_manual": "I supply the inserts myself",
        "help.bg_manual": "You provide the material instead of slopgen fetching it, and what that means follows from the source above. A stock source becomes a SEARCH: slopgen tells you what each shot needs and hands you ready-made queries, you find the file. An AI source becomes GENERATION: slopgen writes the prompt, you make the clip in an external tool. Either way the run pauses at the footage stage and `slopgen gather` picks it up.",
        "help.fg_manual": "Same as for the background, applied to each narration-anchored insert: you find or generate it, slopgen tells you what it needs.",
        "gather.kind.search": "find it yourself",
        "gather.want.photo": "photo",
        "gather.want.video": "video",
        "gather.queries": "Search queries — try them in order:",
        "gather.drop_hint_search": "Drop what you find into the inbox as {id}.<ext> — a photo (.jpg/.png) is as good as a clip; a still is held and panned to length.",
        "fandom_add_person": "＋ Add a character",
        "fandom_person_head": "— A character of this world —",
        "fandom_person_blank": "(nothing written down yet)",
        "fandom_remove_person": "🗑 Remove from the world",
        "fandom_fill_person": "✨ AI: fill in from the world",
        "help.fandom_world": "The world this video happens in. Pick a fandom, edit its characters (they belong to the world, so an edit here changes the world itself — as it should: a character is in it or is not), and write its lore. The canon sheet under the lore is what the writer actually holds; it is rebuilt whenever the lore changes.",
        "fandom_soon_note": "Ready to generate: a video from inside this world, with its own characters.",
        "fandom_brief_none": "The model returned nothing usable — your text is untouched.",
        "fandom_brief_written": "Brief written from the world.",
        "fandom_write_brief": "✨ AI: write the brief",
        "cast_st_world": "world",
        "fandom_step_brief": "Brief",
        "help.fandom_brief": "What this video is about. Name the thing in this world worth an account of its own — a custom, a place, someone or something, an event nobody has explained — or set out a theory you want argued from its records. Thin or empty is fine: the writer will pick something itself. Directions to the writer belong here too (\"break it off without an answer\", \"don't explain the ninth marker\") — they are obeyed and never spoken aloud.",
        "fandom_plot_head": "— What to tell —",
        "fandom_ai_head": "— AI help —",
        "fandom_prompt_ph": "optional: how to change the brief — 'a custom, not an event', 'argue the opposite'",
        "fandom_cast_head": "The world's characters",
        "fandom_cast_hint2": "Everyone and everything this world can put on screen — people, but also a creature, a machine, a ship, a place that behaves like one. They are edited where they live, in the world's own folder: click one to edit it on the right, 💾 writes it into the world, 🗑 takes it out. A character is in the world or is not; there is no adding one for a single video. Empty fields are improvised at generation time.",
        "help.fandom_duration_min": "Target length in minutes. The piece may run a little over or under (see Tolerance).",
        "help.fandom_scenario": "What to tell about this world, or which theory to argue from its records. Empty or thin is fine — the writer picks something itself.",
        "help.fandom_visuals": "The AI-generator pipeline for this video. Stages run top→bottom; each produces its share, then hands off. Move stages with ▲/▼, click one to configure it.",
        "help.fandom_visuals_step": "Which neural nets draw this world, and in what order. Each stage produces its share of the video, then hands off to the next.",
        "help.fandom_tts_rate": "Speech speed. ←/→ to adjust: −50 = slowest … 0 = normal … +50 = fastest. The writer counts on it: a faster voice fits more into a clip of the same length, so each beat is written longer. A single fragment can be re-voiced at another speed later, at the voiceover breakpoint.",
        "mode_info": "⚡  Minute of useless info",
        "mode_info_desc": "the current mode — narrated facts over stock / AI b-roll",
        "mode_drama": "🎭  AI drama",
        "mode_drama_desc": "narrated anime-style story with a recurring cast + AI-generated shots",
        "mode_fandom": "🌍  Fandom",
        "mode_fandom_desc": "a narrated story set in a world you wrote down — told from inside it as fact",
        # --- fandom mode: the world, its lore documents and its canon sheet ---
        "fandom_pick": "Fandom",
        "fandom_none": "no fandoms yet — create one in Config → Fandoms",
        "fandom_pick_first": "pick a fandom first",
        "fandom_lore_head": "— Lore —",
        "fandom_doc": "Document",
        "fandom_edit": "✎ Edit",
        "fandom_preview": "👁 Preview",
        "fandom_canon_head": "— Canon sheet —",
        "fandom_canon_stale": "the lore changed since this sheet was compiled",
        "fandom_recompile": "Recompile canon",
        "fandom_compiling": "Compiling the canon sheet…",
        "fandom_compiled": "canon sheet rebuilt",
        "fandom_compile_err": "Could not compile the canon sheet — the lore is saved, the sheet is stale",
        "fandom_saved": "lore saved",
        "fandom_voice": "Narrator",
        "fandom_voice_resident": "someone who lives there",
        "fandom_voice_chronicler": "a chronicler of that world",
        "fandom_tone": "Tone / register note (optional)",
        "fandom_docs": "Documents, in reading order (comma-separated; empty = every *.md)",
        "fandom_lore_tool": "Let the writer query the full lore (librarian tool)",
        "fandom_new": "New fandom",
        "fandom_summary_head": "Fandom — ready:",
        "fandom_soon": "Starting fandom generation — world: {name}, cast: {n}.",
        "help.step.fandom": "The world this story happens in. Pick a fandom, edit its lore documents, and choose who is telling it. The canon sheet below is what the writer actually holds while writing — rebuild it after you change the lore.",
        "help.fandom_pick": "Which world to narrate. Fandoms are folders under configs/fandoms/ — a fandom.toml, one or more markdown lore documents, and the world's own cast.",
        "help.fandom_lore": "The world's lore, in markdown. This is the source of truth: the canon sheet is compiled from it, and the librarian tool reads it. Save to write the file and rebuild the sheet.",
        "help.fandom_voice": "Who is telling it. A resident speaks in first person and treats the world as daily life; a chronicler researches its records and builds theories out of them. Either way the world is real to them — never a story, never someone's invention.",
        "drama_cast_head": "Cast",
        "drama_add": "＋ Add character",
        "drama_plot_head": "— Plot —",
        "drama_dur_hint": "AI recommends: ~{min:.1f} min",
        "drama_ai_head": "— AI story polish —",
        "drama_protagonist": "Protagonist",
        "drama_protagonist_none": "— let AI decide —",
        "drama_tropes_btn": "🎭 Tropes",
        "drama_tropes_head": "— Story tropes —",
        "drama_tropes_done": "✓ Done",
        "drama_prompt_ph": "optional: fill/rewrite plot, create characters, or add saved ones",
        "drama_cast_hint2": "Click a character to edit it on the right. Empty fields are improvised at generation time.",
        "cast_st_local": "Not saved",
        "cast_st_global": "Global",
        "cast_st_global_dirty": "Global*",
        "cast_age": "age",
        "drama_summary_head": "AI drama — ready:",
        "drama_soon_note": "Ready to generate: narrated story with your cast + AI-generated shots.",
        "drama_soon": "Starting drama generation — cast: {n}.",
        "drama_duration_min": "Length, min",
        "drama_duration_tol": "Tolerance, sec",
        "drama_clip_s": "Average AI clip, sec",
        "drama_clip_auto": "per generator",
        "help.drama_duration_min": "Target length of the drama, in minutes. The story may run a little over/under (see Tolerance).",
        "help.drama_duration_tol": "How many seconds the finished video may run over or under the target when the story calls for it.",
        "help.drama_clip_s": "How long ONE generated clip runs on average. It decides how many clips the story is cut into and how much narration each carries — and a long clip (8s+) is written as a sequence of several scenes instead of one framing. 0 = each generator's own length.",
        # right-panel help + character editor
        "insp_help_head": "— Help —",
        "insp_keys": "Keys:\n  ↑ / ↓   move between steps\n  Tab     next field\n  Enter   open / confirm\n  Esc     back",
        "help.step.content": "Language, voice, topic and tone of the video. Leave the idea empty to let the LLM pick one.",
        "help.step.characters": "The drama's cast. Add characters (new or from your library), toggle who appears, and edit each on the right. AI can fill everyone at once, create missing characters, add saved ones, or edit one at a time.",
        "help.step.visuals": "How the video looks: background source (stock / AI / local) and optional narration-linked inserts.",
        "help.step.ads": "Optional sponsor: pick a saved ad contract or fill a manual one.",
        "help.step.publish": "Where the result goes (a saved account or local), how many, and the subtitle style.",
        "help.step.summary": "Review everything, then GENERATE.",
        # per-field descriptions (shown in the inspector when the field is focused)
        "help.lang": "Language of the narration and subtitles.",
        "help.voice": "The edge-tts voice used for this language.",
        "help.ctype": "Which content template shapes the script (facts, story, …).",
        "help.idea": "Your own topic. Leave empty and the LLM invents one.",
        "help.profanity": "How much swearing in the narration. ←/→ to adjust: 0 = clean … 100 = constant.",
        "help.vprofile": "A ready visuals preset. Picking one prefills the fields below; edit any to customise.",
        "help.duration": "Target spoken length, in seconds (a soft target, not a hard cap).",
        "help.bg_src": "Where the background comes from: stock video/photo, AI-generated, or your local files.",
        "help.bg_link": "narration = match the spoken words; neutral = generic footage.",
        "help.bg_dir": "Folder with your own clips/images (used by the local_* sources).",
        "help.bg_int": "Seconds each photo stays on screen before the next (photo backgrounds).",
        "help.bg_motion": "Ken Burns zoom/pan strength on photo backgrounds.",
        "help.bg_cont": "Play one clip straight through the whole video (gameplay) instead of restarting per scene.",
        "help.ai_model": "Which neural net generates the visuals for ai_* sources.",
        "help.fg_on": "Pop pictures over the background when the narration names something concrete.",
        "help.fg_src": "Where the foreground inserts come from.",
        "help.fg_width": "Insert width as a percentage of the frame.",
        "help.fg_pos": "Where inserts appear on screen.",
        "help.ad_src": "No ads, a manual ad, or a saved ad contract.",
        "help.ad_mode": "overlay = corner banner; native = spoken mention; both.",
        "help.push": "A saved account to publish to, or just save the file locally.",
        "help.count": "How many videos to generate in this run.",
        "help.parts": "How many publishable parts to ask the writer to split one AI drama into. Parts end on script-planned cliffhangers, and the boundaries are yours to move at the script and cut breakpoints. Each part is cut, described and published on its own, as soon as its clips are in.",
        "help.subs": "Subtitle animation style: word-pop, phrases, or karaoke.",
        "help.drama_scenario": "The drama's premise/plot. Empty or thin is fine — it's improvised at generation.",
        "help.drama_prompt": "Optional steer for '✨ AI fill / add cast': fill or rewrite the plot, create local characters, or add saved global characters.",
        "help.char_name": "Character name — also the file name in the global library.",
        "help.char_age": "Age (e.g. 17, late 20s). Optional; folded into the visual prompt.",
        "help.char_appearance": "Looks, build and clothing — injected into every image/video prompt for consistency.",
        "help.char_prompt": "Optional: tell the AI how to fill or rewrite THIS character (may overwrite filled fields).",
        "help.char_photo": "Path to a reference photo — a vision model turns it into an appearance description.",
        # --- orchestration (drama Visuals step) ---
        "help.drama_visuals": "The AI-generator pipeline for the drama's video. Stages run top→bottom; each produces its share, then hands off. Move stages with ▲/▼, click one to configure it.",
        "orch_head": "— Video orchestration —",
        "orch_profile": "Orchestration profile",
        "orch_custom": "— custom —",
        "orch_add": "＋ Add stage",
        "orch_up": "▲ Up",
        "orch_down": "▼ Down",
        "orch_save_prof": "★ Save profile",
        "orch_hint": "Generators run top→bottom; each fills its share of the video, then hands off to the next.",
        "orch_stage_head": "— Stage —",
        "orch_model": "Generator",
        "orch_key_mode": "On key limit",
        "orch_km_rotate": "rotate keys",
        "orch_km_single": "one key, then skip stage",
        "orch_key": "Key",
        "orch_key_auto": "auto (first key)",
        "orch_metric": "Hand off after",
        "orch_m_clips": "clips",
        "orch_m_seconds": "seconds",
        "orch_m_percent": "% of video",
        "orch_amount": "Amount",
        "orch_clip_s": "Clip length, sec (0 = auto)",
        "orch_clip_badge": "clip {s:g}s",
        "orch_remove": "🗑 Remove stage",
        "orch_pick_first": "select a stage first",
        "orch_empty": "add a stage first",
        "orch_name": "Profile name:",
        "help.orch_profile": "Load a saved orchestration profile, or build a custom one below.",
        "help.orch_model": "Which neural net this stage uses to generate its share of the video.",
        "help.orch_key_mode": "When a key hits its provider limit: rotate to the next key and keep going, or (single) use one key then skip this stage.",
        "help.orch_key": "In 'one key' mode: which of your keys to pin (managed in Config → Footage keys).",
        "help.orch_metric": "Unit of the hand-off amount: clips, seconds, or % of the final video.",
        "help.orch_amount": "How much this stage produces before handing off to the next one.",
        "help.orch_clip_s": "Average length of one clip from THIS generator. Overrides the run-level average — use it when a stage's clips are longer (a hand-made Kling/Veo shot next to 5-second Space clips). 0 = the generator's own length.",
        "pick_head": "Add a character",
        "pick_new": "＋ Create new",
        "pick_from_lib": "…or pick one from the library:",
        "char_new_name": "New character",
        "char_edit_head": "— Character —",
        "char_prompt_ph": "optional: tell the AI how to fill/rewrite this character",
        "char_autofill_all": "✨ AI fill / add cast",
        "char_cfg_note": "Manual editor. AI help (photo → description, autofill) lives in the drama and fandom wizards — and, for a world's own people, in Fandoms.",
        "cast_save_global": "★ Save to library",
        "cast_remove": "🗑 Remove",
        "cast_empty": "add a character, write a plot, or enter an AI prompt first",
        "lang": "Content language",
        "voice": "Voice",
        "ctype": "Content type",
        "ctype_auto": "Any — no fixed type (LLM picks freely)",
        "idea": "Your idea",
        "idea_ph": "leave empty — the LLM invents a topic",
        "profanity": "Profanity level",
        "prof_none": "clean",
        "prof_mild": "mild",
        "prof_mod": "moderate",
        "prof_heavy": "heavy",
        "prof_max": "constant f-bombs",
        "tts_rate": "Speech rate (←/→ to adjust)",
        "rate_very_slow": "very slow",
        "rate_slow": "slow",
        "rate_normal": "normal",
        "rate_fast": "fast",
        "rate_very_fast": "very fast",
        "help.tts_rate": "Speech speed. ←/→ to adjust: −50 = slowest … 0 = normal … +50 = fastest. In a drama the writer counts on it: a faster voice fits more story into a clip of the same length, so each beat is written longer. A single fragment can be re-voiced at another speed later, at the voiceover breakpoint.",
        "vis_profile": "Visuals profile",
        "duration": "Duration",
        "bg_head": "— Background —",
        "bg_source": "Background source",
        "ai_model": "AI generator",
        "bg_link": "Background linkage",
        "bg_dir": "Local assets folder",
        "bg_int": "Photo interval",
        "bg_motion": "Photo motion",
        "bg_cont": "Continuous clip",
        "fg_head": "— Foreground inserts —",
        "fg_on": "Enable narration inserts",
        "fg_source": "Insert source",
        "fg_auto_note": "inserts appear automatically when the narration mentions something concrete",
        "fg_width": "Insert width",
        "fg_pos": "Insert position",
        "vis_custom_note": "fields differ from the profile — a custom profile will be used",
        "ad_source": "Ad source",
        "ad_none": "— no ads —",
        "ad_manual": "✍ manual (fill fields below)",
        "ad_mode": "Ad mode",
        "ad_url": "Landing URL",
        "ov_text": "Overlay caption",
        "ov_pos": "Overlay position",
        "ov_start": "Overlay start, s",
        "ov_dur": "Overlay duration, s",
        "talking": "Native talking points (for the LLM)",
        "manual_note": "assets for manual ads go to assets/ads/manual/{overlay,native}",
        "push": "Publish to",
        "push_local": "— save locally —",
        "count": "Videos count",
        "parts": "Drama parts",
        "parts_iterative": "Finish parts one at a time",
        "parts_batch": "all at the end",
        "help.parts_iterative": "ON: a part is cut, described and published as soon as ITS OWN clips are in, while the later parts are still being made — the run parks between them and `slopgen gather` picks it up where it left off. OFF: nothing is cut until the whole drama's clips are in, then every part goes at once (what it did before). One part behaves the same either way.",
        "subs": "Subtitle style",
        "next": "Next  →",
        "prev": "←  Prev",
        "start": "⛏  G E N E R A T E",
        "summary_head": "Everything is set:",
        "cfg.llm": "LLM profiles",
        "cfg.footage": "Footage API keys",
        "cfg.characters": "Characters",
        "cfg.fandoms": "Fandoms",
        "cfg.ads": "Ad contracts",
        "cfg.accounts": "Accounts",
        "cfg.presets": "Presets",
        "f.age": "Age",
        "f.appearance": "Appearance",
        "char_ai_note": "All fields are optional — leave them for the AI. The description is compiled into a model-optimized English prompt at generation time.",
        "char_photo_ph": "path to a reference photo (jpg/png)",
        "char_describe": "📷 Describe from photo",
        "char_autofill": "✨ AI fill / rewrite",
        "char_need_path": "enter a photo path first",
        "char_no_file": "file not found",
        "char_working": "asking the LLM…",
        "ai_thinking": "Thinking",
        "char_ai_err": "AI fill failed (network hiccup, or check the active LLM key)",
        "char_photo_err": "Photo description failed (needs a vision-capable model + key)",
        "char_described": "appearance filled from the photo",
        "char_filled": "updated by AI",
        "char_nothing": "AI made no changes",
        "web_search": "Web search tool (ground the script in real facts)",
        "web_search_note": "gives the model a web_search tool so it verifies facts instead of inventing names/events; needs a tool-calling model",
        "footage_note": "Stock keys (Pexels/Pixabay) for stock_* visuals, plus optional AI-generator tokens for ai_* visuals. All optional; local assets and Pollinations work with no key.",
        "pexels_key": "Pexels API key",
        "pixabay_key": "Pixabay API key",
        "hf_key": "Hugging Face tokens — one per line; rotated on limit",
        "pollinations_key": "Pollinations tokens — one per line; rotated on limit",
        "multikey_note": "one API key per line — orchestration rotates through them when a key hits its limit",
        "provider": "Provider",
        "model_preset": "Model preset",
        "model": "Model (editable)",
        "base_url": "Base URL (empty = provider default)",
        "temp": "Temperature",
        "api_key": "API key (saved to .env)",
        "key_saved_ph": "••• key already saved — type to replace",
        "key_empty_ph": "paste the key here",
        "key_ok": "✔ key found",
        "key_no": "✘ key NOT set",
        "active_now": "active",
        "activate": "★  Make active",
        "save": "💾  Save",
        "delete": "🗑  Delete",
        "confirm_del": "Delete '{name}' permanently?",
        "yes": "Yes, delete",
        "no": "Cancel",
        "new_tab": "+ new",
        "saved": "saved",
        "deleted": "deleted",
        "name_req": "name is required",
        "f.name": "Name",
        "f.url": "Landing URL",
        "f.snippet": "Description snippet ({url} is substituted)",
        "f.platform": "Platform (youtube/local)",
        "f.privacy": "Privacy (public/unlisted/private)",
        "f.category": "YouTube category id",
        "f.def_lang": "Default language (optional)",
        "f.def_ctype": "Default content type (optional)",
        "f.def_ad": "Default ad (optional)",
        "f.ad": "Ad contract (optional)",
        "f.ad_mode": "Ad mode (overlay/native/both)",
        "f.visuals": "Visuals profile (optional)",
        "f.duration": "Target duration, s (optional)",
        "f.push": "Account to publish to (optional)",
        "f.count": "Videos per run",
        "run.finished": "batch finished",
        "col.video": "video",
        "col.stage": "stage",
        "col.status": "status",
        "col.info": "info",
        "row.queued": "queued",
        "run.vis": "visuals",
        "run.subs": "subs",
        "run.local": "local",
        "err.startup": "startup failed",
        "err.save": "save failed",
        "keys.saved_n": "key(s) → .env",
        # user-assisted (manual) clip gathering
        "gather.title": "Manual clips",
        "gather.paused": "paused — needs manual clips",
        "gather.needed": "This run needs hand-made clips — opening the gather screen.",
        "gather.attach": "＋ Attach clip",
        "gather.inflight": "⏳ Mark sent",
        "gather.rescan": "⟳ Rescan inbox",
        "gather.finish": "▶ Finish & resume",
        "gather.col.shot": "shot",
        "gather.col.status": "status",
        "gather.col.target": "target",
        "gather.col.prompt": "prompt",
        "gather.delivered": "delivered",
        "gather.inbox": "inbox",
        "gather.clip": "clip",
        "gather.drop_hint": "drop a clip into the inbox as",
        "gather.none": "No shots awaiting manual clips.",
        "gather.attach_prompt": "Path to the clip file:",
        "gather.bad_clip": "not a readable video file",
        "gather.incomplete": "no part has all of its clips yet — finish one and it can be cut",
        "gather.col.part": "part",
        "gather.part_ready": "part {n} ✔ ready to cut",
        "gather.part_left": "part {n}: {k} left",
        "gather.will_cut": "Continuing cuts and publishes the ready part(s); the rest waits for you here.",
        "gather.wait_all": "This run cuts every part at the end, so all of the clips are needed before it can go on.",
        # breakpoints: picking them (Summary step) and the review screen
        "bp_head": "— Breakpoints —",
        "bp_hint": "Pause the run after these stages to check — and edit — what came out.",
        "bp.stage.idea": "Idea (the chosen topic)",
        "bp.stage.canon": "Canon sheet (the world, as the writer will hold it)",
        "bp.stage.script": "Script (raw text the LLM wrote)",
        "bp.stage.entities": "Visual registry (recurring things and how they look)",
        "bp.stage.tts": "Voiceover (line-by-line narration)",
        "bp.stage.cut": "Episodes (where each part ends)",
        "bp.stage.footage": "Footage (shot prompts / search queries)",
        "bp.stage.subtitles": "Subtitles (the .ass files)",
        "bp.stage.assemble": "Assembly (the rendered file)",
        "bp.stage.metadata": "Metadata (title, description, tags)",
        "bp.title": "Breakpoint",
        "bp.needed": "The run stopped at a breakpoint — opening the review screen.",
        "bp.paused": "breakpoint — waiting for review",
        "bp.head": "video {i} · stage: [b]{stage}[/b] · {n} entries",
        "bp.left": "{n} more video(s) waiting after this one",
        "bp.add": "＋ Add",
        "bp.remove": "✖ Drop",
        "bp.continue": "▶ Continue the run",
        "bp.discard": "↺ Revert edits",
        "bp.ai": "✨ Ask AI",
        "bp.ai_ph": "tell the AI what to change in the lines above",
        "bp.ai_need": "write what to change first",
        "bp.ai_working": "AI is editing…",
        "bp.ai_done": "AI applied its edit — check it before continuing.",
        "bp.ai_nothing": "the AI returned nothing usable",
        "bp.ai_err": "AI edit failed",
        "bp.saved": "Saved. The run continues.",
        "bp.rerun": "Saved — the {stage} stage will run again for the changed lines.",
        "bp.none": "Nothing is waiting at a breakpoint.",
        "bp.readonly": "This stage is inspect-only — nothing here can be edited.",
        "bp.field.text": "voiceover",
        "bp.field.name": "name in the prompts",
        "bp.field.note": "what it is",
        "bp.field.look": "how it looks (English, for the generator)",
        "bp.field.prompt": "shot",
        "bp.field.keywords": "search",
        "bp.field.cast": "who is in it",
        "bp.field.model": "generator",
        "bp.field.clip_s": "clip length, sec",
        "bp.chip_pick": "Add to this shot:",
        "bp.chip_none": "the whole cast is already in this shot",
        "bp.cast_known": "Cast of this run",
        "clean_subs": "Clean the subtitles (voice keeps the words)",
        "help.clean_subs": "Swap profanity out of the burned-in subtitles — including words that merely look profane, like the first part of a name. The voiceover is untouched: platforms moderate what they can read.",
        "visual_notes": "Visual constraints",
        "visual_notes_ph": "binds the picture only, not the plot: \"all weapons are toy ones\", \"no blood\", \"no logos\"",
        "help.visual_notes": "Constraints on what the shots may SHOW. The story is written as if they did not exist — only the picture obeys. English reaches the generator verbatim; other languages go through the writer.",
        "bp.scene": "Scene",
        "bp.regen": "🔊 Re-voice",
        "bp.play": "▶ Listen",
        "bp.rate": "Voicing speed (←/→; applies to the fragment you re-voice)",
        "bp.regen_working": "voicing this line…",
        "bp.regen_done": "new take: {s:.1f}s at {r:+d}%",
        "bp.regen_err": "could not voice it",
        "bp.play_none": "this line has no audio yet — re-voice it first",
        "bp.play_err": "ffplay not found (it ships with ffmpeg)",
        "bp.ai_ph_script": "anything: rewrite, reorder, merge, split, add or drop scenes, recast, change generators",
        "unit.outline": "story outline",
        "unit.script": "script windows",
        "unit.entities": "registry passes",
        "unit.tts": "voiceover fragments",
        "unit.footage": "video fragments",
        "unit.assemble": "scenes assembled",
        "unit.join": "scenes joined",
        "unit.finalize": "files rendered",
        "unit.cut": "parts",
        "unit.metadata": "parts described",
        "bp.up": "▲",
        "bp.down": "▼",
        "bp.f.topic": "topic",
        "bp.f.canon": "canon sheet",
        "bp.f.title": "title",
        "bp.f.description": "description",
        "bp.f.tags": "tags (comma-separated)",
        "bp.note.idea": "The topic the whole script is written from.",
        "bp.note.canon": "The world's canon sheet, as compiled from your lore. Fix anything the compiler got wrong or missed — the writer holds this sheet for every scene, and what is not here effectively does not exist in the world.",
        "bp.note.script": "Cards are the scenes; open one to edit its spoken line, its shot, who is in it, the generator and the clip length. This is the ONLY place to fix a shot before it is generated.",
        "bp.note.entities": "Things that recur across shots and are not cast — a machine, a location, a prop, a nameless regular, an unusual crowd. Each card is one thing: the name the shot prompts use for it, a note, and the English description the generator gets. Editing a description restyles every shot showing it at once; the name must stay spelled exactly as the prompts spell it, or nothing is substituted.",
        "bp.note.tts": "One card per voiced fragment, with the length of what was synthesized. Editing a line re-voices exactly that one; adding, dropping or reordering cards changes the fragments themselves. The speed slider is one for the whole screen and applies only to the fragment you re-voice with it — that line then keeps the speed, the rest of the video keeps the run's.",
        "bp.note.footage": "What each scene is rendered/searched from. Changed scenes get their footage remade.",
        "bp.note.subtitles": "The generated ASS files, as text. Edits are written straight to disk.",
        "bp.note.assemble": "The rendered file(s) — play them, then continue or press Esc to abandon the run.",
        "bp.note.metadata": "What gets published with the video.",
        "bp.note.cut": "Drag the part markers to decide where each episode ends — every part becomes a video of its own, published separately. Add a marker to split it further, drop one to merge two episodes. The scenes are already voiced, so the seconds on each are what it really runs to. This is the last free moment to re-cut: after it, clips are generated (or hand-made) against these boundaries.",
        "bp.f.part": "Part",
        "bp.field.part": "part break",
        "bp.cut": "＋ Part break",
        "bp.sep": "── part {n} starts here ──",
        "bp.sep_hint": "Everything below this marker belongs to part {n}, until the next marker. Move it with ▲▼, drop it to merge this part into the one above.",
        "bp.cut_min": "a video needs at least one part",
        "bp.cut_locked": "only the part markers can be moved at this breakpoint",
    },
    "ru": {
        "subtitle": "промышленный конвейер нейрослопа",
        "menu.generate": "⛏  Генерация видео",
        "menu.config": "⚙  Конфигурация",
        "menu.quit": "✖  Выход",
        "step.content": "Контент",
        "step.characters": "Сюжет",
        "step.fandom": "Мир",
        "step.visuals": "Видеоряд",
        "step.ads": "Реклама",
        "step.publish": "Публикация",
        "step.summary": "Итог",
        "mode_head": "Что генерируем?",
        "fandom_duration_s": "Длина, сек",
        "fandom_medium": "Из чего",
        "fandom_medium_video": "🎬 видеоклипы",
        "fandom_medium_photo": "🖼 слайд-шоу из фото",
        "help.fandom_duration_s": "Сколько идёт готовое видео, в секундах. Допуска и поля длины клипа нет: сценарист сам подбирает длину каждого кадра под это число, из чего бы кадры ни были.",
        "help.fandom_medium": "Из чего складывается картинка. Клипы играют, неподвижные кадры держатся с медленным наездом. От этого же зависит, чем их делать — слайд-шоу рисует генератор картинок, или ты находишь фотографии, или рисуешь их сам, — поэтому список ниже меняется вместе с выбором.",
        "fandom_source_note": "🙋 и 🔍 — это ты: в первом случае слопген пишет промпт на каждый кадр, и ты генерируешь его во внешнем сервисе, во втором — говорит, что найти, и выдаёт готовые поисковые запросы. И там и там прогон встаёт на этапе видеоряда, а `slopgen gather` продолжает, когда файлы окажутся в инбоксе. `flux` и `turbo` делают неподвижные кадры вместо клипов; кадр растягивается на нужную длину с лёгким наездом, и принесённый тобой считается так же — положи .jpg или .png, и он будет обработан как фото.",
        "fandom_source_head": "— Откуда берутся кадры —",
        "fandom_source": "Источник",
        "help.fandom_source": "Откуда берётся каждый кадр этого видео. ИИ-генерация идёт без тебя, бесплатно, но медленно и часто мимо. Генерировать самому — вставлять каждый промпт в Kling/Veo/Pika и приносить файл: лучшая картинка ценой вечера. Искать самому — слопген пишет задание на каждый кадр, а ты приносишь настоящие съёмки настоящих вещей, чего мир обычных мест обычно и просит.",
        "help.fandom_clip_s": "Сколько в среднем длится ОДИН кадр. От этого зависит, на сколько кадров нарезан ролик и сколько текста несёт каждый.",
        "bg_manual": "Фон беру на себя",
        "fg_manual": "Вставки беру на себя",
        "help.bg_manual": "Материал даёшь ты, а не слопген, и что именно это значит — следует из источника выше. Сток превращается в ПОИСК: слопген говорит, что нужно на каждый кадр, и выдаёт готовые запросы, ты находишь файл. ИИ превращается в ГЕНЕРАЦИЮ: слопген пишет промпт, ты делаешь клип во внешнем сервисе. И там и там прогон встаёт на этапе видеоряда, а `slopgen gather` его подхватывает.",
        "help.fg_manual": "То же, что и для фона, но на каждую вставку, привязанную к словам: находишь или генерируешь её сам, а слопген говорит, что нужно.",
        "gather.kind.search": "найти самому",
        "gather.want.photo": "фото",
        "gather.want.video": "видео",
        "gather.queries": "Поисковые запросы — пробуй по порядку:",
        "gather.drop_hint_search": "Положи найденное в инбокс как {id}.<расш> — фото (.jpg/.png) годится не хуже клипа: неподвижный кадр растянется на нужную длину с лёгким наездом.",
        "fandom_add_person": "＋ Добавить персонажа",
        "fandom_person_head": "— Персонаж этого мира —",
        "fandom_person_blank": "(пока ничего не записано)",
        "fandom_remove_person": "🗑 Убрать из мира",
        "fandom_fill_person": "✨ ИИ: дописать по миру",
        "help.fandom_world": "Мир, в котором происходит это видео. Выбери фандом, отредактируй его персонажей (они принадлежат миру, поэтому правка здесь меняет сам мир — так и должно быть: персонаж в нём либо есть, либо нет) и напиши его лор. Канон-справка под лором — это то, что реально держит перед собой сценарист; она пересобирается при изменении лора.",
        "fandom_soon_note": "Готово к генерации: видео изнутри этого мира, с его персонажами.",
        "fandom_brief_none": "Модель не вернула ничего пригодного — твой текст не тронут.",
        "fandom_brief_written": "Замысел написан по миру.",
        "fandom_write_brief": "✨ ИИ: написать замысел",
        "cast_st_world": "мира",
        "fandom_step_brief": "Замысел",
        "help.fandom_brief": "О чём это видео. Назови то в этом мире, что стоит отдельного рассказа, — обычай, место, кого-то или что-то, случай, который никто не объяснил, — или изложи теорию, которую надо доказать по его записям. Можно пусто или в двух словах: сценарист выберет сам. Указания сценаристу тоже сюда (\"оборви без ответа\", \"не объясняй девятую вешку\") — их исполнят и вслух не произнесут.",
        "fandom_plot_head": "— О чём рассказать —",
        "fandom_ai_head": "— Помощь ИИ —",
        "fandom_prompt_ph": "опционально: как изменить замысел — «про обычай, а не про случай», «докажи обратное»",
        "fandom_cast_head": "Персонажи мира",
        "fandom_cast_hint2": "Все, кого и что этот мир может показать: люди, но и существо, машина, корабль, место, которое ведёт себя как персонаж. Правятся там, где живут, — в папке самого мира: клик по персонажу открывает правку справа, 💾 записывает его в мир, 🗑 убирает оттуда. Персонаж в мире либо есть, либо его нет; добавить кого-то на одно видео нельзя. Пустые поля додумываются при генерации.",
        "help.fandom_duration_min": "Целевая длина в минутах. Может немного выйти за рамки (см. Допуск).",
        "help.fandom_scenario": "О чём рассказать в этом мире или какую теорию доказать по его записям. Можно пусто или частично — сценарист выберет сам.",
        "help.fandom_visuals": "Конвейер ИИ-генераторов для этого видео. Этапы идут сверху вниз; каждый делает свою долю и передаёт дальше. Двигай этапы ▲/▼, клик по этапу — настройка.",
        "help.fandom_visuals_step": "Какие нейронки рисуют этот мир и в каком порядке. Каждый этап делает свою долю видео и передаёт следующему.",
        "help.fandom_tts_rate": "Скорость речи. ←/→ для настройки: −50 = медленно … 0 = норма … +50 = быстро. Сценарист на неё рассчитывает: чем быстрее голос, тем больше влезает в клип той же длины, поэтому реплики пишутся длиннее. Отдельный фрагмент можно переозвучить на другой скорости позже, на брейкпоинте озвучки.",
        "mode_info": "⚡  Минута бесполезной инфы",
        "mode_info_desc": "текущий режим — факты под сток/ИИ-видеоряд",
        "mode_drama": "🎭  ИИ-дорама",
        "mode_drama_desc": "озвученная аниме-история с постоянными персонажами + ИИ-кадры",
        "mode_fandom": "🌍  Фандом",
        "mode_fandom_desc": "озвученная история в мире, который ты описал — рассказанная изнутри него как факт",
        # --- режим фандома: мир, его документы лора и канон-справка ---
        "fandom_pick": "Фандом",
        "fandom_none": "фандомов пока нет — заведи его в Конфигурации → Фандомы",
        "fandom_pick_first": "сначала выбери фандом",
        "fandom_lore_head": "— Лор —",
        "fandom_doc": "Документ",
        "fandom_edit": "✎ Правка",
        "fandom_preview": "👁 Просмотр",
        "fandom_canon_head": "— Канон-справка —",
        "fandom_canon_stale": "лор изменился с момента сборки справки",
        "fandom_recompile": "Перекомпилировать канон",
        "fandom_compiling": "Собираю канон-справку…",
        "fandom_compiled": "канон-справка пересобрана",
        "fandom_compile_err": "Не удалось собрать канон-справку — лор сохранён, справка устарела",
        "fandom_saved": "лор сохранён",
        "fandom_voice": "Рассказчик",
        "fandom_voice_resident": "житель этого мира",
        "fandom_voice_chronicler": "летописец этого мира",
        "fandom_tone": "Тон / манера речи (опционально)",
        "fandom_docs": "Документы в порядке чтения (через запятую; пусто = все *.md)",
        "fandom_lore_tool": "Разрешить сценаристу запрашивать полный лор (инструмент-архивариус)",
        "fandom_new": "Новый фандом",
        "fandom_summary_head": "Фандом — готово:",
        "fandom_soon": "Запускаю генерацию фандома — мир: {name}, каст: {n}.",
        "help.step.fandom": "Мир, в котором происходит история. Выбери фандом, поправь его документы лора и реши, кто рассказывает. Канон-справка ниже — это то, что реально держит перед собой сценарист; пересобери её после правок лора.",
        "help.fandom_pick": "Какой мир рассказываем. Фандом — это папка в configs/fandoms/: fandom.toml, один или несколько markdown-документов лора и собственный каст мира.",
        "help.fandom_lore": "Лор мира в markdown. Это первоисточник: из него собирается канон-справка, его же читает инструмент-архивариус. Сохранение записывает файл и пересобирает справку.",
        "help.fandom_voice": "Кто рассказывает. Житель говорит от первого лица и воспринимает мир как быт; летописец копается в записях и строит из них теории. В обоих случаях мир для них настоящий — не история и не чья-то выдумка.",
        "drama_cast_head": "Каст",
        "drama_add": "＋ Добавить персонажа",
        "drama_plot_head": "— Сюжет —",
        "drama_dur_hint": "ИИ рекомендует: ~{min:.1f} мин",
        "drama_ai_head": "— ИИ-доработка сюжета —",
        "drama_protagonist": "Главный герой",
        "drama_protagonist_none": "— ИИ сам решит —",
        "drama_tropes_btn": "🎭 Клише",
        "drama_tropes_head": "— Клише сюжета —",
        "drama_tropes_done": "✓ Готово",
        "drama_prompt_ph": "опционально: переписать сюжет, создать персонажей или добавить сохранённых",
        "drama_cast_hint2": "Клик по персонажу — редактирование справа. Пустые поля додумываются при генерации.",
        "cast_st_local": "Не сохранён",
        "cast_st_global": "Глобальный",
        "cast_st_global_dirty": "Глобальный*",
        "cast_age": "возраст",
        "drama_summary_head": "ИИ-дорама — готово:",
        "drama_soon_note": "Готово к генерации: озвученная история с твоим кастом + ИИ-кадры.",
        "drama_soon": "Запускаю генерацию дорамы — каст: {n}.",
        "drama_duration_min": "Длина, мин",
        "drama_duration_tol": "Допуск, сек",
        "drama_clip_s": "Средний ИИ-клип, сек",
        "drama_clip_auto": "по генератору",
        "help.drama_duration_min": "Целевая длина дорамы в минутах. История может немного выйти за рамки (см. Допуск).",
        "help.drama_duration_tol": "На сколько секунд готовое видео может превысить/недотянуть цель, если этого требует сюжет.",
        "help.drama_clip_s": "Сколько в среднем длится ОДИН сгенерированный клип. От этого зависит, на сколько клипов режется история и сколько озвучки достаётся каждому — а длинный клип (от 8с) пишется как последовательность нескольких сцен, а не один кадр. 0 = длина самого генератора.",
        # помощь в правой панели + редактор персонажа
        "insp_help_head": "— Помощь —",
        "insp_keys": "Клавиши:\n  ↑ / ↓   переход между шагами\n  Tab     следующее поле\n  Enter   открыть / подтвердить\n  Esc     назад",
        "help.step.content": "Язык, голос, тема и тон видео. Оставь идею пустой — тему придумает LLM.",
        "help.step.characters": "Каст дорамы. Добавляй персонажей (новых или из библиотеки), включай/выключай участие, редактируй каждого справа. ИИ может заполнить всех сразу, создать недостающих персонажей, добавить сохранённых или редактировать по одному.",
        "help.step.visuals": "Как выглядит видео: источник фона (сток / ИИ / локальный) и опциональные вставки под нарратив.",
        "help.step.ads": "Опциональный спонсор: готовый контракт или ручной ввод.",
        "help.step.publish": "Куда идёт результат (аккаунт или локально), сколько штук и стиль субтитров.",
        "help.step.summary": "Проверь всё и жми ГЕНЕРАЦИЯ.",
        # описания полей (показываются в инспекторе при фокусе на поле)
        "help.lang": "Язык озвучки и субтитров.",
        "help.voice": "Голос edge-tts для выбранного языка.",
        "help.ctype": "Шаблон контента, задающий стиль сценария (факты, история, …).",
        "help.idea": "Своя тема. Оставь пустым — LLM придумает сама.",
        "help.profanity": "Сколько мата в озвучке. ←/→ для настройки: 0 = чисто … 100 = постоянно.",
        "help.vprofile": "Готовый пресет видеоряда. Выбор предзаполняет поля ниже; любое можно поправить.",
        "help.duration": "Целевая длина озвучки в секундах (ориентир, не жёсткий лимит).",
        "help.bg_src": "Откуда берётся фон: сток видео/фото, генерация ИИ или твои локальные файлы.",
        "help.bg_link": "narration = под смысл слов; neutral = обобщённый футаж.",
        "help.bg_dir": "Папка с твоими клипами/картинками (для источников local_*).",
        "help.bg_int": "Сколько секунд держится каждое фото до смены (фото-фон).",
        "help.bg_motion": "Сила зума/панорамы Ken Burns на фото-фоне.",
        "help.bg_cont": "Один клип на всё видео насквозь (геймплей) вместо перезапуска на каждой сцене.",
        "help.ai_model": "Какая нейросеть генерирует видеоряд для ai_*-источников.",
        "help.fg_on": "Всплывающие картинки поверх фона, когда в озвучке названо что-то конкретное.",
        "help.fg_src": "Откуда берутся вставки переднего плана.",
        "help.fg_width": "Ширина вставки в процентах от кадра.",
        "help.fg_pos": "Где вставки появляются на экране.",
        "help.ad_src": "Без рекламы, ручная реклама или сохранённый контракт.",
        "help.ad_mode": "overlay = баннер в углу; native = устное упоминание; both — оба.",
        "help.push": "Аккаунт для публикации или просто локальное сохранение файла.",
        "help.count": "Сколько видео сгенерировать за этот прогон.",
        "help.parts": "На сколько публикуемых частей просить сценариста разбить ИИ-дораму. Обрывы планируются как клиффхэнгеры, а границы потом двигаются на брейкпоинтах script и cut. Каждая часть монтируется, описывается и публикуется сама по себе — как только собраны её клипы.",
        "help.subs": "Стиль субтитров: word-pop, phrases или karaoke.",
        "help.drama_scenario": "Замысел/сюжет дорамы. Можно пусто или частично — додумается при генерации.",
        "help.drama_prompt": "Опциональная подсказка для «✨ ИИ заполнит / добавит каст»: как заполнить или переписать сюжет, создать локальных персонажей или добавить сохранённых глобальных.",
        "help.char_name": "Имя персонажа — оно же имя файла в глобальной библиотеке.",
        "help.char_age": "Возраст (напр. 17, ~25). Опционально; вшивается в визуальный промпт.",
        "help.char_appearance": "Вид, телосложение и одежда — вшивается в каждый промпт кадра для консистентности.",
        "help.char_prompt": "Опционально: как ИИ заполнить/переписать ЭТОГО персонажа (может менять непустые поля).",
        "help.char_photo": "Путь к фото-референсу — vision-модель превратит его в описание внешности.",
        # --- оркестрация (шаг «Видеоряд» дорамы) ---
        "help.drama_visuals": "Конвейер ИИ-генераторов для видео дорамы. Этапы идут сверху вниз; каждый делает свою долю и передаёт дальше. Двигай этапы ▲/▼, клик по этапу — настройка.",
        "orch_head": "— Оркестрация видео —",
        "orch_profile": "Профиль оркестрации",
        "orch_custom": "— свой —",
        "orch_add": "＋ Добавить этап",
        "orch_up": "▲ Вверх",
        "orch_down": "▼ Вниз",
        "orch_save_prof": "★ Сохранить профиль",
        "orch_hint": "Генераторы идут сверху вниз; каждый заполняет свою долю видео и передаёт следующему.",
        "orch_stage_head": "— Этап —",
        "orch_model": "Генератор",
        "orch_key_mode": "При лимите ключа",
        "orch_km_rotate": "ротация ключей",
        "orch_km_single": "один ключ, потом скип этапа",
        "orch_key": "Ключ",
        "orch_key_auto": "авто (первый ключ)",
        "orch_metric": "Передать после",
        "orch_m_clips": "клипов",
        "orch_m_seconds": "секунд",
        "orch_m_percent": "% видео",
        "orch_amount": "Значение",
        "orch_clip_s": "Длина клипа, сек (0 = авто)",
        "orch_clip_badge": "клип {s:g}с",
        "orch_remove": "🗑 Удалить этап",
        "orch_pick_first": "сначала выбери этап",
        "orch_empty": "сначала добавь этап",
        "orch_name": "Имя профиля:",
        "help.orch_profile": "Загрузи сохранённый профиль оркестрации или собери свой ниже.",
        "help.orch_model": "Какая нейросеть на этом этапе генерирует свою долю видео.",
        "help.orch_key_mode": "Когда ключ упёрся в лимит провайдера: ротация на следующий ключ и продолжать, или (один ключ) — использовать один и скипнуть этап.",
        "help.orch_key": "В режиме «один ключ»: какой из твоих ключей закрепить (управление в Конфиг → Ключи футажа).",
        "help.orch_metric": "Единица объёма передачи: клипы, секунды или % финального видео.",
        "help.orch_amount": "Сколько этот этап производит перед передачей следующему.",
        "help.orch_clip_s": "Средняя длина одного клипа ИМЕННО этого генератора. Перебивает общее значение прогона — пригодится, когда клипы этапа длиннее (ручной кадр из Kling/Veo рядом с пятисекундными клипами Spaces). 0 = длина самого генератора.",
        "pick_head": "Добавить персонажа",
        "pick_new": "＋ Создать нового",
        "pick_from_lib": "…или выбери из библиотеки:",
        "char_new_name": "Новый персонаж",
        "char_edit_head": "— Персонаж —",
        "char_prompt_ph": "опционально: как ИИ должен заполнить/переписать персонажа",
        "char_autofill_all": "✨ ИИ заполнит / добавит каст",
        "char_cfg_note": "Ручной редактор. ИИ-помощь (фото → описание, автозаполнение) — в визардах дорамы и фандома, а для людей мира — в разделе Фандомы.",
        "cast_save_global": "★ Сохранить в библиотеку",
        "cast_remove": "🗑 Убрать",
        "cast_empty": "сначала добавь персонажа, впиши сюжет или промпт для ИИ",
        "lang": "Язык контента",
        "voice": "Голос",
        "ctype": "Тип контента",
        "ctype_auto": "Любой — без фиксированного типа (нейронка выбирает сама)",
        "idea": "Своя идея",
        "idea_ph": "оставь пустым — нейронка придумает тему",
        "profanity": "Уровень мата",
        "prof_none": "чисто",
        "prof_mild": "лёгкий",
        "prof_mod": "умеренный",
        "prof_heavy": "жёсткий",
        "prof_max": "сплошной мат",
        "tts_rate": "Скорость речи (←/→ регулировка)",
        "rate_very_slow": "очень медленно",
        "rate_slow": "медленно",
        "rate_normal": "нормально",
        "rate_fast": "быстро",
        "rate_very_fast": "очень быстро",
        "help.tts_rate": "Скорость речи. ←/→ для настройки: −50 = медленно … 0 = норма … +50 = быстро. В дораме сценарист на неё рассчитывает: чем быстрее голос, тем больше сюжета влезает в клип той же длины, поэтому реплики пишутся длиннее. Отдельный фрагмент потом можно переозвучить на другой скорости — на брейкпоинте озвучки.",
        "vis_profile": "Профиль видеоряда",
        "duration": "Длительность",
        "bg_head": "— Фон —",
        "bg_source": "Источник фона",
        "ai_model": "ИИ-генератор",
        "bg_link": "Привязка фона",
        "bg_dir": "Локальная папка",
        "bg_int": "Интервал фото",
        "bg_motion": "Движение фото",
        "bg_cont": "Непрерывный клип",
        "fg_head": "— Вставки на переднем плане —",
        "fg_on": "Включить вставки по тексту",
        "fg_source": "Источник вставок",
        "fg_auto_note": "вставки появляются сами, когда в озвучке упомянуто что-то конкретное",
        "fg_width": "Ширина вставки",
        "fg_pos": "Позиция вставки",
        "vis_custom_note": "поля отличаются от профиля — будет использован кастомный профиль",
        "ad_source": "Источник рекламы",
        "ad_none": "— без рекламы —",
        "ad_manual": "✍ вручную (поля ниже)",
        "ad_mode": "Режим рекламы",
        "ad_url": "Ссылка (лендинг)",
        "ov_text": "Текст оверлея",
        "ov_pos": "Позиция оверлея",
        "ov_start": "Старт оверлея, с",
        "ov_dur": "Длительность оверлея, с",
        "talking": "Тезисы нативки (для нейронки)",
        "manual_note": "ассеты ручной рекламы клади в assets/ads/manual/{overlay,native}",
        "push": "Куда публиковать",
        "push_local": "— сохранить локально —",
        "count": "Количество роликов",
        "parts": "Частей в дораме",
        "parts_iterative": "Доводить части по одной",
        "parts_batch": "все в конце",
        "help.parts_iterative": "ВКЛ: часть монтируется, описывается и публикуется, как только собраны клипы именно ЕЁ, пока следующие ещё делаются, — прогон встаёт между ними, а `slopgen gather` подхватывает с того же места. ВЫКЛ: ничего не режется, пока не собраны клипы всей дорамы, потом всё уходит разом (как было раньше). С одной частью разницы нет.",
        "subs": "Стиль субтитров",
        "next": "Далее  →",
        "prev": "←  Назад",
        "start": "⛏  С Г Е Н Е Р И Р О В А Т Ь",
        "summary_head": "Всё настроено:",
        "cfg.llm": "Профили нейронок",
        "cfg.footage": "Ключи API футажа",
        "cfg.characters": "Персонажи",
        "cfg.fandoms": "Фандомы",
        "cfg.ads": "Рекламные контракты",
        "cfg.accounts": "Аккаунты",
        "cfg.presets": "Пресеты",
        "f.age": "Возраст",
        "f.appearance": "Внешность",
        "char_ai_note": "Все поля опциональны — можно доверить ИИ. Описание компилируется в оптимизированный под нейросети английский промпт при запуске генерации.",
        "char_photo_ph": "путь к фото-референсу (jpg/png)",
        "char_describe": "📷 Описать по фото",
        "char_autofill": "✨ ИИ заполнит / перепишет",
        "char_need_path": "сначала укажи путь к фото",
        "char_no_file": "файл не найден",
        "char_working": "спрашиваю LLM…",
        "ai_thinking": "Думаю",
        "char_ai_err": "ИИ-заполнение не удалось (сбой сети или проверь ключ активной LLM)",
        "char_photo_err": "Не удалось описать фото (нужна vision-модель и ключ)",
        "char_described": "внешность заполнена по фото",
        "char_filled": "ИИ обновил каст/сюжет",
        "char_nothing": "ИИ ничего не изменил",
        "web_search": "Инструмент веб-поиска (опора на реальные факты)",
        "web_search_note": "даёт модели инструмент web_search — она проверяет факты, а не выдумывает имена/события; нужна модель с tool-calling",
        "footage_note": "Ключи стоков (Pexels/Pixabay) для stock_*-видеоряда и опциональные токены ИИ-генераторов для ai_*-видеоряда. Все необязательны: локальным ассетам и Pollinations ключ не нужен.",
        "pexels_key": "API-ключ Pexels",
        "pixabay_key": "API-ключ Pixabay",
        "hf_key": "Токены Hugging Face — по одному на строку; ротация при лимите",
        "pollinations_key": "Токены Pollinations — по одному на строку; ротация при лимите",
        "multikey_note": "по одному API-ключу на строку — оркестрация ротирует их при упоре в лимит",
        "provider": "Провайдер",
        "model_preset": "Пресет модели",
        "model": "Модель (можно править)",
        "base_url": "Base URL (пусто = дефолт провайдера)",
        "temp": "Температура",
        "api_key": "API-ключ (сохранится в .env)",
        "key_saved_ph": "••• ключ уже сохранён — введи, чтобы заменить",
        "key_empty_ph": "вставь ключ сюда",
        "key_ok": "✔ ключ найден",
        "key_no": "✘ ключа НЕТ",
        "active_now": "активен",
        "activate": "★  Сделать активным",
        "save": "💾  Сохранить",
        "delete": "🗑  Удалить",
        "confirm_del": "Удалить '{name}' безвозвратно?",
        "yes": "Да, удалить",
        "no": "Отмена",
        "new_tab": "+ новый",
        "saved": "сохранено",
        "deleted": "удалено",
        "name_req": "нужно имя",
        "f.name": "Имя",
        "f.url": "Ссылка (лендинг)",
        "f.snippet": "Сниппет описания ({url} подставится)",
        "f.platform": "Платформа (youtube/local)",
        "f.privacy": "Приватность (public/unlisted/private)",
        "f.category": "Категория YouTube (id)",
        "f.def_lang": "Язык по умолчанию (опц.)",
        "f.def_ctype": "Тип контента по умолчанию (опц.)",
        "f.def_ad": "Реклама по умолчанию (опц.)",
        "f.ad": "Рекламный контракт (опц.)",
        "f.ad_mode": "Режим рекламы (overlay/native/both)",
        "f.visuals": "Профиль видеоряда (опц.)",
        "f.duration": "Целевая длительность, с (опц.)",
        "f.push": "Аккаунт публикации (опц.)",
        "f.count": "Роликов за запуск",
        "run.finished": "батч завершён",
        "col.video": "видео",
        "col.stage": "стадия",
        "col.status": "статус",
        "col.info": "инфо",
        "row.queued": "в очереди",
        "run.vis": "видеоряд",
        "run.subs": "субт.",
        "run.local": "локально",
        "err.startup": "ошибка запуска",
        "err.save": "ошибка сохранения",
        "keys.saved_n": "ключ(ей) → .env",
        # user-assisted (manual) clip gathering
        "gather.title": "Ручные клипы",
        "gather.paused": "пауза — нужны ручные клипы",
        "gather.needed": "Этому запуску нужны клипы, сделанные вручную — открываю экран сбора.",
        "gather.attach": "＋ Прикрепить клип",
        "gather.inflight": "⏳ Отметить «отправлен»",
        "gather.rescan": "⟳ Пересканировать inbox",
        "gather.finish": "▶ Завершить и продолжить",
        "gather.col.shot": "кадр",
        "gather.col.status": "статус",
        "gather.col.target": "длит.",
        "gather.col.prompt": "промпт",
        "gather.delivered": "готово",
        "gather.inbox": "inbox",
        "gather.clip": "клип",
        "gather.drop_hint": "положи клип в inbox под именем",
        "gather.none": "Нет кадров, ожидающих ручных клипов.",
        "gather.attach_prompt": "Путь к файлу клипа:",
        "gather.bad_clip": "не читается как видеофайл",
        "gather.incomplete": "ни одна часть ещё не собрана целиком — добей любую, и её можно монтировать",
        "gather.col.part": "часть",
        "gather.part_ready": "часть {n} ✔ можно монтировать",
        "gather.part_left": "часть {n}: осталось {k}",
        "gather.will_cut": "Продолжение смонтирует и опубликует готовые части; остальные подождут тебя здесь.",
        "gather.wait_all": "Этот прогон режет все части в конце, так что дальше он пойдёт только со всеми клипами.",
        # брейкпоинты: выбор (шаг «Итог») и экран разбора
        "bp_head": "— Брейкпоинты —",
        "bp_hint": "Остановить конвейер после этих этапов, чтобы проверить и поправить результат.",
        "bp.stage.idea": "Идея (выбранная тема)",
        "bp.stage.canon": "Канон-справка (мир таким, каким его увидит сценарист)",
        "bp.stage.script": "Сценарий (сырой текст от нейронки)",
        "bp.stage.entities": "Реестр визуала (что повторяется и как выглядит)",
        "bp.stage.tts": "Озвучка (построчно, по фрагментам)",
        "bp.stage.cut": "Части (где кончается каждая серия)",
        "bp.stage.footage": "Видеоряд (промпты кадров / поисковые запросы)",
        "bp.stage.subtitles": "Субтитры (файлы .ass)",
        "bp.stage.assemble": "Сборка (готовый файл)",
        "bp.stage.metadata": "Метаданные (заголовок, описание, теги)",
        "bp.title": "Брейкпоинт",
        "bp.needed": "Конвейер встал на брейкпоинте — открываю экран разбора.",
        "bp.paused": "брейкпоинт — ждёт проверки",
        "bp.head": "видео {i} · этап: [b]{stage}[/b] · позиций: {n}",
        "bp.left": "после этого в очереди ещё видео: {n}",
        "bp.add": "＋ Добавить",
        "bp.remove": "✖ Удалить",
        "bp.continue": "▶ Продолжить конвейер",
        "bp.discard": "↺ Откатить правки",
        "bp.ai": "✨ Спросить ИИ",
        "bp.ai_ph": "напиши, что поменять в строках выше",
        "bp.ai_need": "сначала напиши, что менять",
        "bp.ai_working": "ИИ правит…",
        "bp.ai_done": "ИИ внёс правки — проверь их перед продолжением.",
        "bp.ai_nothing": "ИИ не вернул ничего пригодного",
        "bp.ai_err": "ИИ не смог поправить",
        "bp.saved": "Сохранено. Конвейер идёт дальше.",
        "bp.rerun": "Сохранено — этап {stage} переделает изменённые строки.",
        "bp.none": "Никто не ждёт на брейкпоинте.",
        "bp.readonly": "Этот этап только для просмотра — править тут нечего.",
        "bp.field.text": "озвучка",
        "bp.field.name": "имя в промптах",
        "bp.field.note": "что это",
        "bp.field.look": "как выглядит (по-английски, для генератора)",
        "bp.field.prompt": "кадр",
        "bp.field.keywords": "поиск",
        "bp.field.cast": "кто в кадре",
        "bp.field.model": "нейронка",
        "bp.field.clip_s": "длина клипа, сек",
        "bp.chip_pick": "Добавить в кадр:",
        "bp.chip_none": "весь каст уже в этом кадре",
        "bp.cast_known": "Каст этого прогона",
        "clean_subs": "Чистить субтитры (в озвучке мат остаётся)",
        "help.clean_subs": "Заменяет мат в вожжённых субтитрах — включая слова, которые лишь похожи на мат, вроде первой части имени. Озвучка не трогается: платформы модерируют то, что могут прочитать.",
        "visual_notes": "Ограничения картинки",
        "visual_notes_ph": "связывают только картинку, не сюжет: «всё оружие игрушечное», «без крови», «без логотипов»",
        "help.visual_notes": "Ограничения на то, что можно ПОКАЗЫВАТЬ. Сюжет пишется так, будто их нет — подчиняется только картинка. Английский уходит в генератор дословно, остальные языки — через сценариста.",
        "bp.scene": "Сцена",
        "bp.regen": "🔊 Переозвучить",
        "bp.play": "▶ Прослушать",
        "bp.rate": "Скорость озвучки (←/→; применится к переозвучиваемому фрагменту)",
        "bp.regen_working": "озвучиваю эту строку…",
        "bp.regen_done": "новый дубль: {s:.1f}с на {r:+d}%",
        "bp.regen_err": "не смог озвучить",
        "bp.play_none": "у строки ещё нет озвучки — сначала переозвучь",
        "bp.play_err": "ffplay не найден (он идёт вместе с ffmpeg)",
        "bp.ai_ph_script": "что угодно: переписать, переставить, склеить, разбить, добавить или убрать сцены, сменить каст и нейронки",
        "unit.outline": "план истории",
        "unit.script": "окон сценария",
        "unit.entities": "проходов по реестру",
        "unit.tts": "фрагментов озвучки",
        "unit.footage": "видеофрагментов",
        "unit.assemble": "сцен смонтировано",
        "unit.join": "сцен склеено",
        "unit.finalize": "файлов собрано",
        "unit.cut": "частей",
        "unit.metadata": "частей описано",
        "bp.up": "▲",
        "bp.down": "▼",
        "bp.f.topic": "тема",
        "bp.f.canon": "канон-справка",
        "bp.f.title": "заголовок",
        "bp.f.description": "описание",
        "bp.f.tags": "теги (через запятую)",
        "bp.note.idea": "Тема, из которой пишется весь сценарий.",
        "bp.note.canon": "Канон-справка мира, собранная из твоего лора. Поправь всё, что компилятор понял неверно или упустил: сценарист держит эту справку перед собой в каждой сцене, и чего здесь нет — того в мире фактически не существует.",
        "bp.note.script": "Карточки — это сцены; открой любую, чтобы поправить реплику, кадр, кто в нём, нейронку и длину клипа. Это единственное место, где кадр правится ДО генерации.",
        "bp.note.entities": "То, что повторяется в кадрах и не входит в каст: техника, локация, реквизит, безымянный завсегдатай, необычная массовка. Карточка — одна вещь: имя, которым её называют промпты, заметка и английское описание, которое уходит генератору. Правка описания меняет вид вещи сразу во всех кадрах; имя должно остаться написанным ровно так, как в промптах, иначе подстановки не будет.",
        "bp.note.tts": "Карточка на озвученный фрагмент, с длительностью того, что синтезировалось. Правка реплики переозвучивает только её; добавление, удаление и перестановка карточек меняют сами фрагменты. Ползунок скорости один на весь экран и применяется только к тому фрагменту, который ты им переозвучил — эта строка дальше живёт со своей скоростью, остальное видео остаётся на скорости запуска.",
        "bp.note.footage": "Из чего рисуется/ищется каждая сцена. Изменённым сценам видеоряд соберут заново.",
        "bp.note.subtitles": "Сгенерированные ASS-файлы как текст. Правки пишутся прямо на диск.",
        "bp.note.assemble": "Готовые файлы — посмотри их и продолжай, либо Esc, чтобы бросить запуск.",
        "bp.note.metadata": "То, с чем видео уйдёт в публикацию.",
        "bp.note.cut": "Двигай маркеры частей, решая, где кончается каждая серия — часть становится отдельным видео и публикуется сама по себе. Добавь маркер, чтобы разрезать дальше, убери — чтобы склеить две серии. Сцены уже озвучены, так что секунды у каждой настоящие. Это последний бесплатный момент для перекройки: дальше клипы генерируются (или делаются руками) уже под эти границы.",
        "bp.f.part": "Часть",
        "bp.field.part": "разрыв части",
        "bp.cut": "＋ Разрыв части",
        "bp.sep": "── здесь начинается часть {n} ──",
        "bp.sep_hint": "Всё ниже этого маркера относится к части {n} — до следующего маркера. Двигай его на ▲▼, убери — и часть склеится с предыдущей.",
        "bp.cut_min": "в видео должна быть хотя бы одна часть",
        "bp.cut_locked": "на этом брейкпоинте двигаются только маркеры частей",
    },
}

# status badges reuse localized words where useful; keep them compact/ASCII-safe.


def _label(app: "SlopgenApp", key: str) -> str:
    return I18N[app.ui_lang].get(key, key)


def _update_global_toml(section: str, values: dict) -> None:
    """Merge values into a section of configs/slopgen.toml (comments not preserved)."""
    path = Path("configs/slopgen.toml")
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    data.setdefault(section, {}).update(values)
    path.write_bytes(tomli_w.dumps(data).encode())


class TopBar(Horizontal):
    """Replaces Header+Footer: title on the left; RU/EN, '<-' and Palette on the right."""

    def __init__(self, title: str = ""):
        super().__init__(id="topbar")
        self._title = title

    def compose(self) -> ComposeResult:
        app: SlopgenApp = self.app  # type: ignore[assignment]
        yield Static(f" ⛏ slopgen — {self._title}" if self._title else " ⛏ slopgen", id="tb-title")
        yield Button("RU" if app.ui_lang == "en" else "EN", id="tb-lang")
        yield Button("<-", id="tb-back")
        yield Button("Palette", id="tb-palette")


class ConfirmModal(ModalScreen[bool]):
    """Tiny yes/no confirmation dialog."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        with Vertical(id="confirm-box"):
            yield Static(self._text, id="confirm-text")
            with Horizontal(id="confirm-row"):
                yield Button(t("yes"), id="confirm-yes", variant="error")
                yield Button(t("no"), id="confirm-no", variant="primary")

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        self.dismiss(False)


class NameModal(ModalScreen[str | None]):
    """Tiny 'enter a name' dialog; dismisses with the entered name or None."""

    def __init__(self, title: str):
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-text")
            yield Input(id="name-input")
            with Horizontal(id="confirm-row"):
                yield Button(t("save"), id="nm-ok", variant="success")
                yield Button(t("no"), id="nm-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    @on(Button.Pressed, "#nm-ok")
    @on(Input.Submitted, "#name-input")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#name-input", Input).value.strip() or None)

    @on(Button.Pressed, "#nm-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------


class PickModal(ModalScreen[str | None]):
    """Pick one of a list of options; dismisses with the choice or None. Used by the
    ＋ on set-valued fields, where typing a name would only invite typos."""

    def __init__(self, title: str, options: list[str]):
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-text")
            yield ListView(
                *[ListItem(Label(o), id=f"pick-{i}") for i, o in enumerate(self._options)],
                id="pick-list",
            )
            with Horizontal(id="confirm-row"):
                yield Button(_label(self.app, "no"), id="pick-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#pick-list", ListView).focus()

    @on(ListView.Selected, "#pick-list")
    def _picked(self, event: ListView.Selected) -> None:
        i = int(event.item.id.rsplit("-", 1)[1])
        self.dismiss(self._options[i] if 0 <= i < len(self._options) else None)

    @on(Button.Pressed, "#pick-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class HomeScreen(Screen):
    def on_key(self, event) -> None:
        # arrow keys cycle focus between the big menu buttons; Enter activates
        if event.key == "down":
            self.focus_next()
            event.stop()
        elif event.key == "up":
            self.focus_previous()
            event.stop()

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield TopBar()
        with Center(id="home-center"):
            with Vertical(id="home-inner"):
                yield Static(LOGO, id="logo")
                yield Static(t("subtitle"), id="logo-sub")
                with Vertical(id="home-menu"):
                    yield Button(t("menu.generate"), id="go-generate", variant="success")
                    yield Button(t("menu.config"), id="go-config", variant="primary")
                    yield Button(t("menu.quit"), id="go-quit", variant="error")

    def on_mount(self) -> None:
        self.query_one("#go-generate", Button).focus()

    @on(Button.Pressed, "#go-generate")
    def _generate(self) -> None:
        self.app.push_screen(ModeSelectScreen())

    @on(Button.Pressed, "#go-config")
    def _config(self) -> None:
        self.app.push_screen(ConfigScreen())

    @on(Button.Pressed, "#go-quit")
    def _quit(self) -> None:
        self.app.exit()


# --------------------------------------------------------------------------
# Generation wizard: vertical step list on the left, one step at a time
# --------------------------------------------------------------------------

STEP_KEYS = ["step.content", "step.visuals", "step.ads", "step.publish", "step.summary"]
DRAMA_STEP_KEYS = ["step.content", "step.characters", "step.visuals", "step.ads", "step.publish", "step.summary"]
# fandom = the drama wizard with the world in front of the story: which fandom, its
# lore, and who is telling it are settled before a plot is written for that world.
FANDOM_STEP_KEYS = ["step.content", "step.fandom", "step.characters", "step.visuals",
                    "step.ads", "step.publish", "step.summary"]

# widget id -> i18n key for the field's description, shown in the inspector top
# when that setting is focused. Fields absent here fall back to the step blurb.
FIELD_HELP = {
    "w-lang": "help.lang", "w-voice": "help.voice", "w-ctype": "help.ctype",
    "w-idea": "help.idea", "w-profanity": "help.profanity", "w-tts_rate": "help.tts_rate",
    "w-duration_min": "help.drama_duration_min", "w-duration_tol": "help.drama_duration_tol",
    "w-clip_s": "help.drama_clip_s", "w-visual_notes": "help.visual_notes",
    "w-clean_subs": "help.clean_subs",
    "w-vprofile": "help.vprofile", "w-duration": "help.duration",
    "w-bg-src": "help.bg_src", "w-bg-link": "help.bg_link", "w-bg-dir": "help.bg_dir",
    "w-bg-int": "help.bg_int", "w-bg-motion": "help.bg_motion", "w-bg-cont": "help.bg_cont",
    "w-bg-ai-vmodel": "help.ai_model", "w-bg-ai-pmodel": "help.ai_model",
    "w-fg-on": "help.fg_on", "w-fg-src": "help.fg_src", "w-fg-width": "help.fg_width",
    "w-fg-pos": "help.fg_pos", "w-fg-ai-vmodel": "help.ai_model", "w-fg-ai-pmodel": "help.ai_model",
    "w-ad-src": "help.ad_src", "w-ad-mode": "help.ad_mode",
    "w-push": "help.push", "w-count": "help.count", "w-parts": "help.parts", "w-subs": "help.subs",
    "w-parts_iterative": "help.parts_iterative",
    "drama-scenario": "help.drama_scenario", "drama-prompt": "help.drama_prompt",
    "wf-fandom": "help.fandom_pick", "wf-voice": "help.fandom_voice",
    "wlore-area": "help.fandom_lore", "wlore-doc": "help.fandom_lore",
    "e-characters-name": "help.char_name", "e-characters-age": "help.char_age",
    "e-characters-appearance": "help.char_appearance",
    "char-prompt": "help.char_prompt", "char-photo-path": "help.char_photo",
    "orch-profile": "help.orch_profile",
    "ws-medium": "help.fandom_medium", "ws-vsrc": "help.fandom_source",
    "ws-psrc": "help.fandom_source", "w-duration_s": "help.fandom_duration_s",
    "w-bg-manual": "help.bg_manual", "w-fg-manual": "help.fg_manual",
    "e-orch-model": "help.orch_model", "e-orch-key_mode": "help.orch_key_mode",
    "e-orch-key": "help.orch_key", "e-orch-metric": "help.orch_metric",
    "e-orch-amount": "help.orch_amount", "e-orch-clip_seconds": "help.orch_clip_s",
}

BG_SOURCES = ["stock_video", "stock_photo", "local_video", "local_photo", "ai_video", "ai_photo"]
FG_SOURCES = ["stock_photo", "stock_video", "local_photo", "local_video", "ai_photo", "ai_video"]

# friendlier labels for a few generator keys; everything else shows its raw key.
MODEL_LABELS = {"manual": "🙋 you generate it", "search": "🔍 you find it"}
# Not generators: the material comes from the operator (see media/generate). In the
# visuals profile that is the `manual` toggle, so they must not appear in the
# ai_model picker next to it; a chain stage names them like any other source.
OPERATOR_SOURCES = ("manual", "search")


def _model_opt(m: str) -> tuple[str, str]:  # (label, value) for a Select
    return (MODEL_LABELS.get(m, m), m)

AI_VIDEO_MODELS = [_model_opt(m) for m in VIDEO_MODELS if m not in OPERATOR_SOURCES]
AI_PHOTO_MODELS = [_model_opt(m) for m in PHOTO_MODELS if m not in OPERATOR_SOURCES]
ALL_SOURCES = list(VIDEO_MODELS) + list(PHOTO_MODELS)
# A drama's chain may NOT search. Its beats are scripted moments with named characters
# doing specific things — "Марта отталкивает Ефима у сортировочного стола" — which no
# stock library holds, and the cast machinery (name→appearance substitution, the entity
# registry) means nothing for found footage. Generating it by hand still makes sense,
# so `manual` stays.
ORCH_MODEL_OPTS = [_model_opt(m) for m in ALL_SOURCES if m != "search"]
# every source a mode may offer, split by what it puts on screen (see FandomScreen)
VIDEO_SOURCES = [_model_opt(m) for m in VIDEO_MODELS]
PHOTO_SOURCES = [_model_opt(m) for m in list(PHOTO_MODELS) + list(OPERATOR_SOURCES)]
ORCH_FIELDS = ("model", "key_mode", "key", "metric", "amount", "clip_seconds")


def _handle_number_step(host, event: NumStep.Pressed) -> bool:
    try:
        inp = host.query_one(f"#{event.field_id}", Input)
    except Exception:
        return False
    raw = inp.value.strip()
    try:
        current = float(raw) if raw else 0.0
    except ValueError:
        current = 0.0
    current += event.delta
    itype = getattr(inp, "type", "")
    inp.value = str(int(current)) if itype == "integer" or current.is_integer() else str(current)
    inp.focus()
    event.stop()
    return True


def _visuals_values(prof: VisualsConfig) -> dict:
    """Profile → form-field values (keys match the visuals Form field keys)."""
    # the AI-model pick lives in one field but two dropdowns (video vs photo);
    # prefill both from the profile so whichever the source reveals is correct.
    bg_ai = prof.background.ai_model
    fg_ai = prof.foreground.ai_model
    return {
        "bg-src": prof.background.source,
        "bg-manual": prof.background.manual,
        "bg-link": prof.background.linkage,
        "bg-dir": str(prof.background.assets_dir),
        "bg-ai-vmodel": bg_ai if bg_ai in VIDEO_MODELS else "auto",
        "bg-ai-pmodel": bg_ai if bg_ai in PHOTO_MODELS else "flux",
        "bg-int": prof.background.interval_s,
        "bg-motion": prof.background.motion,
        "bg-cont": prof.background.continuous,
        "fg-on": prof.foreground.enabled,
        "fg-src": prof.foreground.source,
        "fg-manual": prof.foreground.manual,
        "fg-ai-vmodel": fg_ai if fg_ai in VIDEO_MODELS else "auto",
        "fg-ai-pmodel": fg_ai if fg_ai in PHOTO_MODELS else "flux",
        "fg-width": prof.foreground.width_pct,
        "fg-pos": prof.foreground.position,
    }


class GenerateScreen(Screen):
    """Step wizard for the "minute of useless info" mode. Every pane's controls
    are declared as a :class:`Form`, so the layout lives in ``_make_forms`` and
    read/prefill/visibility are generic. Subclasses (DramaScreen) extend STEPS +
    ``_pane_body`` to insert steps."""

    STEPS = STEP_KEYS  # ordered step keys; the last one is always the summary
    MODE = "info"  # which stage chain this wizard launches (drives the breakpoint list)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._insp_lock = asyncio.Lock()  # serialize inspector rebuilds (avoid dup ids)

    def _content_form(self, store: ConfigStore) -> Form:
        """The Content step's fields. DramaScreen overrides to drop content-type /
        idea (a drama's premise lives in the Story step instead)."""
        init_lang = "en"
        voice_opts = EDGE_TTS_VOICES.get(init_lang, [])
        return Form("w", [
            Choice("lang", "lang", options=[(l, l) for l in store.languages()], value=init_lang),
            Choice("voice", "voice", options=voice_opts,
                   value=voice_opts[0][1] if voice_opts else None),
            Choice("ctype", "ctype",
                   options=[(_label(self.app, "ctype_auto"), "")]
                   + [(f"{n} — {c.description}", n) for n, c in store.content_types.items()],
                   value=""),
            Text("idea", "idea", placeholder="idea_ph"),
            Range("profanity", "profanity", value=store.global_cfg.defaults.profanity,
                  lo=0, hi=100, step=5, labels=PROFANITY_LABELS),
            Range("tts_rate", "tts_rate", value=0,
                  lo=-50, hi=50, step=5, labels=TTS_RATE_LABELS),
        ])

    def _make_forms(self, t, store: ConfigStore, vis0: VisualsConfig) -> None:
        bg, fg = vis0.background, vis0.foreground
        self.f_content = self._content_form(store)
        self.f_visuals = Form("w", [
            Choice("vprofile", "vis_profile",
                   options=[(f"{n} — {v.description}", n) for n, v in store.visuals.items()],
                   value="classic" if "classic" in store.visuals else None),
            Number("duration", "duration",
                   value=f"{store.global_cfg.video.target_duration_s:.0f}", default=45.0),
            Heading("bg_head"),
            Choice("bg-src", "bg_source", options=[(s, s) for s in BG_SOURCES], value=bg.source),
            # what it MEANS depends on the source above: find it yourself for stock_*,
            # generate it yourself for ai_* (see config.models.manual_kind)
            Group("bg-man", [
                Toggle("bg-manual", "bg_manual", value=bg.manual),
            ], visible_when=lambda v: str(v["bg-src"]).startswith(("stock", "ai"))),
            Group("bg-ai-vid", [
                Choice("bg-ai-vmodel", "ai_model", options=AI_VIDEO_MODELS, value="auto"),
            ], visible_when=lambda v: v["bg-src"] == "ai_video" and not v["bg-manual"]),
            Group("bg-ai-img", [
                Choice("bg-ai-pmodel", "ai_model", options=AI_PHOTO_MODELS, value="flux"),
            ], visible_when=lambda v: v["bg-src"] == "ai_photo" and not v["bg-manual"]),
            Choice("bg-link", "bg_link",
                   options=[("narration", "narration"), ("neutral", "neutral")], value=bg.linkage),
            Text("bg-dir", "bg_dir", value=str(bg.assets_dir)),
            Number("bg-int", "bg_int", value=str(bg.interval_s), default=3.5),
            Choice("bg-motion", "bg_motion",
                   options=[(m, m) for m in ("none", "subtle", "strong")], value=bg.motion),
            Toggle("bg-cont", "bg_cont", value=bg.continuous),
            Heading("fg_head"),
            Toggle("fg-on", "fg_on", value=fg.enabled),
            Group("fg-box", [
                Note("fg_auto_note"),
                Choice("fg-src", "fg_source", options=[(s, s) for s in FG_SOURCES], value=fg.source),
                Toggle("fg-manual", "fg_manual", value=fg.manual),
                Number("fg-width", "fg_width", value=str(fg.width_pct), default=78, integer=True),
                Choice("fg-pos", "fg_pos",
                       options=[(p, p) for p in ("center", "top", "bottom")], value=fg.position),
            ], visible_when=lambda v: v["fg-on"]),
            # AI-model pickers for the insert source (top-level so their visibility
            # is driven by the form engine; nested groups aren't toggled).
            Group("fg-ai-vid", [
                Choice("fg-ai-vmodel", "ai_model", options=AI_VIDEO_MODELS, value="auto"),
            ], visible_when=lambda v: v["fg-on"] and v["fg-src"] == "ai_video"),
            Group("fg-ai-img", [
                Choice("fg-ai-pmodel", "ai_model", options=AI_PHOTO_MODELS, value="flux"),
            ], visible_when=lambda v: v["fg-on"] and v["fg-src"] == "ai_photo"),
        ])
        self.f_ads = Form("w", [
            Choice("ad-src", "ad_source",
                   options=[(_label(self.app, "ad_none"), NONE), (_label(self.app, "ad_manual"), MANUAL)]
                   + [(n, n) for n in store.ads],
                   value=NONE),
            Group("ad-common", [
                Choice("ad-mode", "ad_mode",
                       options=[(m, m) for m in ("both", "overlay", "native")], value="both"),
            ], visible_when=lambda v: v["ad-src"] != NONE),
            Group("ad-manual", [
                Text("ad-url", "ad_url", placeholder="https://"),
                Text("ov-text", "ov_text"),
                Choice("ov-pos", "ov_pos",
                       options=[(p, p) for p in ("top_right", "top_left", "bottom_right", "bottom_left")],
                       value="top_right"),
                Number("ov-start", "ov_start", value="6", default=6.0),
                Number("ov-dur", "ov_dur", value="8", default=8.0),
                Text("talking", "talking"),
                Note("manual_note"),
            ], visible_when=lambda v: v["ad-src"] == MANUAL),
        ])
        self.f_publish = Form("w", [
            Choice("push", "push",
                   options=[(_label(self.app, "push_local"), NONE)]
                   + [(f"{n} ({a.platform})", n) for n, a in store.accounts.items()],
                   value=NONE),
            Number("count", "count", value="1", default=1, integer=True),
            Choice("subs", "subs",
                   options=[(s, s) for s in ("word_pop", "phrases", "karaoke")],
                   value=store.global_cfg.subtitles.style),
            Toggle("clean_subs", "clean_subs"),
        ])
        # Summary step: one switch per stage that can hold a breakpoint
        self.f_breaks = Form("w", [
            Toggle(f"bp-{name}", f"bp.stage.{name}")
            for name in review.available(self.MODE)
        ])

    def _breakpoints(self) -> list[str]:
        """Stages the operator ticked on the Summary step, in pipeline order."""
        values = self.f_breaks.read(self)
        return [n for n in review.available(self.MODE) if values.get(f"bp-{n}")]

    def _nav_buttons(self, step: int):
        t = self._t
        with Horizontal(classes="nav-row"):
            yield Button(t("prev"), id=f"w-prev-{step}", classes="nav-btn")
            yield Button(t("next"), id=f"w-next-{step}", classes="nav-btn", variant="primary")

    def _pane_body(self, key: str, t):
        """Inner widgets for one wizard step (nav buttons are added by compose).
        Keyed by step name so subclasses can add steps by overriding STEPS +
        contributing a branch here."""
        if key == "step.content":
            yield from self.f_content.compose(t)
        elif key == "step.visuals":
            yield from self.f_visuals.compose(t)
        elif key == "step.ads":
            yield from self.f_ads.compose(t)
        elif key == "step.publish":
            yield from self.f_publish.compose(t)
        elif key == "step.summary":
            yield Static("", id="w-summary")
            yield Static("", id="w-cmd")
            yield Static(t("bp_head"), classes="group-head")
            yield Static(t("bp_hint"), classes="hint")
            yield from self.f_breaks.compose(t)
            yield Button(t("start"), id="w-start", variant="success")

    def compose(self) -> ComposeResult:
        t = self._t
        store: ConfigStore = self.app.store
        vis0 = store.visuals.get("classic") or VisualsConfig(name="classic")
        self._make_forms(t, store, vis0)
        yield TopBar(t("menu.generate"))
        last = len(self.STEPS) - 1
        with Horizontal(id="wizard"):
            yield ListView(
                *[ListItem(Label(f"{i + 1} · {t(k)}"), id=f"nav-{i}") for i, k in enumerate(self.STEPS)],
                id="wizard-nav",
            )
            with ContentSwitcher(initial="pane-0", id="wizard-body"):
                for i, key in enumerate(self.STEPS):
                    with VerticalScroll(id=f"pane-{i}", classes="pane"):
                        yield from self._pane_body(key, t)
                        if i == last:  # summary supplies its own start button
                            continue
                        if i == 0:  # first step: forward only
                            yield Button(t("next"), id="w-next-0", classes="nav-btn", variant="primary")
                        else:
                            yield from self._nav_buttons(i)
            # right inspector: help by default, sub-settings on demand (per step)
            yield VerticalScroll(id="wizard-inspector")

    # -- inspector (right panel) --------------------------------------------

    _insp_mode = "help"  # help | picker | editor | stage — only 'help' shows field descriptions

    # Keys this mode says differently. The wizard is one screen shared by three
    # modes, so most of its labels are written once — but a handful name what is
    # being made ("Drama parts", "the drama's cast"), and a fandom operator reading
    # about a drama they are not making is exactly the kind of seam that makes a
    # bolted-on mode feel bolted on. A subclass remaps those keys to its own; every
    # other label resolves untouched.
    LABELS: dict[str, str] = {}

    def _t(self, key: str) -> str:
        """Localized label for this mode. Use instead of `_label(self.app, …)`
        anywhere inside the wizard, so LABELS above is honoured."""
        return _label(self.app, self.LABELS.get(key, key))

    def _step_help_key(self, step_key: str) -> str:
        """i18n key for a step's blurb; DramaScreen overrides where a step differs."""
        return f"help.{step_key}"

    def _inspector_help(self, step_key: str):
        """Default right-panel content: description of the focused setting (top) and
        the keyboard controls (bottom)."""
        t = self._t
        yield Static(t("insp_help_head"), classes="group-head")
        yield Static(t(self._step_help_key(step_key)), id="insp-desc", classes="insp-desc")
        yield Static(t("insp_keys"), id="insp-keys", classes="insp-keys")

    async def _set_inspector(self, widgets: list) -> None:
        async with self._insp_lock:  # serialize: concurrent rebuilds duplicated ids
            insp = self.query_one("#wizard-inspector", VerticalScroll)
            await insp.remove_children()
            if widgets:
                await insp.mount(*widgets)

    async def _show_help(self, step_key: str) -> None:
        self._insp_mode = "help"
        self._help_step = step_key
        await self._set_inspector(list(self._inspector_help(step_key)))

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """When a setting gains focus, show its description in the inspector top
        (only in help mode — picker/editor own the panel otherwise)."""
        if self._insp_mode != "help":
            return
        key = FIELD_HELP.get(event.widget.id or "")
        step = getattr(self, "_help_step", self.STEPS[0])
        text = self._t(key) if key else self._t(self._step_help_key(step))
        try:
            self.query_one("#insp-desc", Static).update(text)
        except Exception:
            pass

    def on_mount(self) -> None:
        self.f_ads.refresh_visibility(self)
        # the visuals form may not be composed (DramaScreen swaps that step for the
        # orchestration editor) — refreshing its groups would query missing widgets
        if "step.visuals" in self.STEPS and self._visuals_form_mounted():
            self.f_visuals.refresh_visibility(self)
        self.query_one("#wizard-nav", ListView).focus()
        self.run_worker(self._show_help(self.STEPS[0]))

    def _visuals_form_mounted(self) -> bool:
        try:
            self.query_one("#w-bg-src")
            return True
        except Exception:
            return False

    # -- step navigation ----------------------------------------------------

    def _goto(self, step: int) -> None:
        self.query_one("#wizard-nav", ListView).index = step

    @on(ListView.Highlighted, "#wizard-nav")
    def _nav(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        step = int(event.item.id.split("-")[1])
        self._on_leave_step()  # let subclasses persist step state before switching
        if step == len(self.STEPS) - 1:
            self._render_summary()
        self.query_one("#wizard-body", ContentSwitcher).current = f"pane-{step}"
        self.run_worker(self._show_help(self.STEPS[step]))

    def _on_leave_step(self) -> None:
        """Hook: called before switching steps (subclasses persist inspector state)."""

    @on(Button.Pressed, ".nav-btn")
    def _nav_btn(self, event: Button.Pressed) -> None:
        kind, cur = event.button.id.split("-")[1:]
        self._goto(int(cur) + (1 if kind == "next" else -1))

    @on(NumStep.Pressed)
    def _num_step(self, event: NumStep.Pressed) -> None:
        _handle_number_step(self, event)

    # -- dynamic visibility (data-driven from the Group predicates) ---------

    @on(Select.Changed, "#w-lang")
    def _lang_changed(self, event: Select.Changed) -> None:
        lang = str(event.value)
        opts = EDGE_TTS_VOICES.get(lang, [])
        voice_sel = self.query_one("#w-voice", Select)
        voice_sel.set_options(opts)
        if opts:
            voice_sel.value = opts[0][1]

    @on(Select.Changed, "#w-ad-src")
    def _ad_src(self, event: Select.Changed) -> None:
        self.f_ads.refresh_visibility(self)

    @on(Switch.Changed, "#w-fg-on")
    @on(Switch.Changed, "#w-bg-manual")
    @on(Switch.Changed, "#w-fg-manual")
    def _fg_on(self, event: Switch.Changed) -> None:
        # the manual toggles matter here too: material the operator supplies has no
        # generator to name, so the ai_model picker goes away with them
        self.f_visuals.refresh_visibility(self)

    @on(Select.Changed, "#w-bg-src")
    @on(Select.Changed, "#w-fg-src")
    def _src_changed(self, event: Select.Changed) -> None:
        # reveal/hide the AI-model picker when an ai_* source is chosen
        self.f_visuals.refresh_visibility(self)

    @on(Select.Changed, "#w-vprofile")
    def _vprofile(self, event: Select.Changed) -> None:
        prof = self.app.store.visuals.get(str(event.value))
        if not prof:
            return
        self.f_visuals.fill(self, _visuals_values(prof))
        self.f_visuals.refresh_visibility(self)

    # -- gathering ----------------------------------------------------------

    @staticmethod
    def _ai_model(v: dict, src: str, prefix: str) -> str:
        """The relevant AI-generator pick for a source (blank for non-AI sources)."""
        if src == "ai_video":
            return v.get(f"{prefix}-ai-vmodel", "")
        if src == "ai_photo":
            return v.get(f"{prefix}-ai-pmodel", "")
        return ""

    def _build_visuals(self, v: dict) -> VisualsConfig:
        bg_src = v["bg-src"] or "stock_video"
        fg_src = v["fg-src"] or "stock_photo"
        return VisualsConfig(
            name="custom",
            background=VisualsBackground(
                source=bg_src,
                linkage=v["bg-link"] or "narration",
                assets_dir=Path(v["bg-dir"] or "assets/footage"),
                manual=bool(v.get("bg-manual")),
                ai_model=self._ai_model(v, bg_src, "bg"),
                interval_s=v["bg-int"],
                motion=v["bg-motion"] or "subtle",
                continuous=v["bg-cont"],
            ),
            foreground=VisualsForeground(
                enabled=v["fg-on"],
                source=fg_src,
                manual=bool(v.get("fg-manual")),
                ai_model=self._ai_model(v, fg_src, "fg"),
                width_pct=int(v["fg-width"]),
                position=v["fg-pos"] or "center",
            ),
        )

    def _visuals_selection(self) -> tuple[str, VisualsConfig | None]:
        """(profile_name, manual_override_or_None): manual when fields diverge."""
        v = self.f_visuals.read(self)
        name = v.get("vprofile") or "classic"
        built = self._build_visuals(v)
        prof = self.app.store.visuals.get(name)
        skip = {"name", "description"}
        if prof and built.model_dump(exclude=skip) == prof.model_dump(exclude=skip):
            return name, None
        return name, built

    def _gather(self) -> dict:
        c = self.f_content.read(self)
        v = self.f_visuals.read(self)
        a = self.f_ads.read(self)
        p = self.f_publish.read(self)
        return {
            "lang": c["lang"],
            "voice": c["voice"],
            "ctype": c.get("ctype", ""),  # absent in the drama wizard's content form
            "idea": c.get("idea", ""),
            "profanity": c["profanity"],
            "tts_rate": c.get("tts_rate", 0),  # absent in the drama wizard's content form
            "duration": v["duration"],
            "ad_src": a["ad-src"],
            "ad_mode": a["ad-mode"] or "both",
            "push": "" if p["push"] == NONE else p["push"],
            "subs": p["subs"],
            "clean_subs": bool(p.get("clean_subs")),
            "count": max(1, int(p["count"])),
            "breakpoints": self._breakpoints(),
            "visual_notes": c.get("visual_notes", ""),
        }

    def _command(self, g: dict, vis_name: str, vis_manual: VisualsConfig | None) -> str:
        cmd = f"slopgen info {g['lang']} {g['ctype']}"
        if g["idea"]:
            cmd += f' --idea "{g["idea"]}"'
        cmd += f" --visuals {vis_name} --duration {g['duration']:.0f}"
        if g["profanity"]:
            cmd += f" --profanity {g['profanity']}"
        if g["tts_rate"]:
            cmd += f" --tts-rate {g['tts_rate']}"
        manual_notes = []
        if vis_manual:
            manual_notes.append("custom visuals")
        if g["ad_src"] == MANUAL:
            cmd += f" --ad-mode {g['ad_mode']}"
            manual_notes.append("manual ad")
        elif g["ad_src"] != NONE:
            cmd += f" --ad {g['ad_src']} --ad-mode {g['ad_mode']}"
        if g["push"]:
            cmd += f" --push {g['push']}"
        if g["count"] != 1:
            cmd += f" -n {g['count']}"
        cmd += f" --subs {g['subs']}"
        if g["clean_subs"]:
            cmd += " --clean-subs"
        for name in g["breakpoints"]:
            cmd += f" --break {name}"
        if manual_notes:
            cmd += f"  # + {', '.join(manual_notes)} (TUI only)"
        return cmd

    def _render_summary(self) -> None:
        t = self._t
        g = self._gather()
        vis_name, vis_manual = self._visuals_selection()
        ad_label = {NONE: t("ad_none"), MANUAL: t("ad_manual")}.get(g["ad_src"], g["ad_src"])
        vis_label = vis_name + (" *" if vis_manual else "")
        lines = [
            f"[b]{t('summary_head')}[/b]",
            "",
            f"  {t('lang')}: [b]{g['lang']}[/b]  {t('voice')}: [b]{g['voice']}[/b]      {t('ctype')}: [b]{g['ctype']}[/b]",
            f"  {t('idea')}: {g['idea'] or '—'}",
            f"  {t('profanity')}: [b]{g['profanity']}%[/b]      {t('tts_rate').split(' (')[0]}: [b]{g['tts_rate']:+d}%[/b]",
            f"  {t('vis_profile').split(' (')[0]}: [b]{vis_label}[/b]      {t('duration')}: ~{g['duration']:.0f}s",
            f"  {t('ad_source')}: {ad_label}"
            + (f"  ({g['ad_mode']})" if g["ad_src"] != NONE else ""),
            f"  {t('push')}: {g['push'] or t('push_local')}",
            f"  {t('count')}: {g['count']}      {t('subs')}: {g['subs']}",
        ]
        if vis_manual:
            lines.append(f"  [dim]{t('vis_custom_note')}[/dim]")
        self.query_one("#w-summary", Static).update("\n".join(lines))
        self.query_one("#w-cmd", Static).update(f"$ {self._command(g, vis_name, vis_manual)}")

    # -- launch ---------------------------------------------------------------

    def _manual_ad_config(self, ad_src: str) -> AdConfig | None:
        """Build an ad-hoc AdConfig from the Ads form (MANUAL source), else None.
        Shared by the info and drama launch paths."""
        if ad_src != MANUAL:
            return None
        a = self.f_ads.read(self)
        ov_dir = Path("assets/ads/manual/overlay")
        nat_dir = Path("assets/ads/manual/native")
        ov_dir.mkdir(parents=True, exist_ok=True)
        nat_dir.mkdir(parents=True, exist_ok=True)
        return AdConfig(
            name="manual",
            url=a["ad-url"],
            modes=["overlay", "native"],
            overlay=AdOverlayConfig(
                assets_dir=ov_dir,
                text=a["ov-text"],
                position=a["ov-pos"] or "top_right",
                start_s=a["ov-start"],
                duration_s=a["ov-dur"],
            ),
            native=AdNativeConfig(assets_dir=nat_dir, talking_points=a["talking"]),
            description=AdDescriptionConfig(snippet="🔗 {url}"),
        )

    @on(Button.Pressed, "#w-start")
    def _start_pressed(self) -> None:
        self._start()

    def _start(self) -> None:
        g = self._gather()
        vis_name, vis_manual = self._visuals_selection()
        manual_ad = self._manual_ad_config(g["ad_src"])
        try:
            params = self.app.store.resolve(
                lang=g["lang"],
                content_type=g["ctype"],
                ad=g["ad_src"] if g["ad_src"] not in (NONE, MANUAL) else None,
                ad_mode=g["ad_mode"] if g["ad_src"] != NONE else None,
                visuals=vis_name,
                duration_s=g["duration"],
                profanity=g["profanity"],
                push=g["push"] or None,
                count=g["count"],
                idea=g["idea"],
                manual_ad=manual_ad,
                manual_visuals=vis_manual,
                subtitle_style=g["subs"],
                voice_override=g["voice"],
                tts_rate=g["tts_rate"],
                breakpoints=g["breakpoints"],
                clean_subtitles=g["clean_subs"],
                visual_notes=g["visual_notes"],
            )
        except ConfigError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self.app.push_screen(ProgressScreen(params))


CHAR_FIELD_KEYS = ("name", "age", "appearance")


def _write_character(
    store: ConfigStore,
    name: str,
    vals: dict,
    *,
    directory: Path | None = None,
    existing: CharacterConfig | None = None,
) -> Path:
    """Persist a character to configs/characters/<name>.toml. Preserves any
    previously compiled prompts but marks it dirty (structured fields changed).

    `directory`/`existing` point the same writer at a fandom's own cast folder
    (configs/fandoms/<world>/characters/), which is a separate library — the world's
    people are not in the global one."""
    if existing is None:
        existing = store.characters.get(name)
    # the file name IS the identity — the loader fills `name` from the stem, so we
    # don't duplicate it inside the file (avoids filename/inner-name divergence).
    data = {
        "age": vals.get("age", ""),
        "appearance": vals.get("appearance", ""),
        "visual_prompt": existing.visual_prompt if existing else "",
        "dirty": True,
    }
    path = (directory or Path("configs/characters")) / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    return path


FANDOMS_DIR = Path("configs/fandoms")


class LoreEditor(Vertical):
    """A fandom's lore documents, edited in place — used by both the wizard's World
    step and Config → Fandoms, so the world is authored wherever the operator
    happens to be standing.

    Markdown is the format the lore is written in, so the editor shows it twice: as
    highlighted source while typing, and as rendered markdown behind the ✎/👁 toggle,
    where heading levels are actual sizes instead of `#` marks. Saving is two steps
    on purpose — the FILE is written first and the canon sheet recompiled after, so a
    failed LLM call costs the sheet's freshness and never the operator's text."""

    def __init__(self, fandom: str = "", *, prefix: str = "lore", **kwargs):
        super().__init__(**kwargs)
        self._fandom = fandom
        self._prefix = prefix  # id prefix: two editors may live in one app
        self._doc = ""  # file name of the document being edited — the single source
        # of truth for "which document is open", because the picker's Changed events
        # are POSTED: repopulating it emits a blank and then the value, both of which
        # arrive long after the flag that was supposed to hide them was lowered
        self._preview = False

    # -- data ---------------------------------------------------------------

    def _cfg(self) -> FandomConfig | None:
        return self.app.store.fandoms.get(self._fandom)

    def _docs(self) -> list[Path]:
        cfg = self._cfg()
        return fandom_docs(cfg) if cfg else []

    def _path(self) -> Path | None:
        return next((p for p in self._docs() if p.name == self._doc), None)

    def _llm(self):
        return ChatLLM(self.app.store.active_llm_profile())

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        p = self._prefix
        yield Static(t("fandom_lore_head"), classes="group-head")
        yield Label(t("fandom_doc"), classes="lore-doc-label")
        yield Select([], id=f"{p}-doc", classes="lore-doc")
        with Horizontal(classes="entity-actions"):
            yield Button(t("fandom_preview"), id=f"{p}-toggle", classes="lore-toggle")
            yield Button(t("save"), id=f"{p}-save", classes="lore-save", variant="success")
            yield Button(t("fandom_recompile"), id=f"{p}-recompile",
                         classes="lore-recompile", variant="primary")
        yield TextArea(text="", language="markdown", id=f"{p}-area", classes="lore-area")
        # Markdown lays its blocks out but does not scroll them; taller-than-the-box
        # lore was clipped with no way to reach the rest. It scrolls in a container,
        # the same way the canon sheet below does.
        yield VerticalScroll(Markdown("", id=f"{p}-view"),
                             id=f"{p}-viewbox", classes="lore-viewbox")
        yield Static(t("fandom_canon_head"), classes="group-head")
        # a compiled sheet runs to a couple of hundred lines; it scrolls in its own
        # box rather than pushing everything else off the bottom of the pane
        yield VerticalScroll(Static("", id=f"{p}-canon", classes="lore-canon"),
                             classes="lore-canon-box")

    def on_mount(self) -> None:
        self.reload()

    # -- state --------------------------------------------------------------

    def set_fandom(self, name: str) -> None:
        """Point the editor at another world (a tab click, or the wizard's picker)."""
        if name == self._fandom:
            return
        self._write_file(if_changed=True)  # the world being left keeps its edits
        self._fandom = name
        self._doc = ""
        self.reload()

    def reload(self) -> None:
        """Repopulate everything from the store: document list, text, canon sheet."""
        try:
            sel = self.query_one(".lore-doc", Select)
        except Exception:  # not composed yet — on_mount will call us again
            return
        names = [p.name for p in self._docs()]
        # set `_doc` FIRST: the Changed events this repopulation posts are matched
        # against it when they arrive, and that is what tells them apart from a real
        # pick by the operator
        self._doc = self._doc if self._doc in names else (names[0] if names else "")
        sel.set_options([(n, n) for n in names])
        if self._doc:
            sel.value = self._doc
        # one document is the common case; a picker over a list of one is noise
        many = len(names) > 1
        sel.display = many
        self.query_one(".lore-doc-label", Label).display = many
        self._load_text()
        self._show_canon()

    def _load_text(self) -> None:
        path = self._path()
        text = ""
        if path is not None:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
        self.query_one(".lore-area", TextArea).text = text
        self._apply_mode()

    def _apply_mode(self) -> None:
        area = self.query_one(".lore-area", TextArea)
        box = self.query_one(".lore-viewbox", VerticalScroll)  # the scroller, not the
        view = box.query_one(Markdown)                         # widget inside it
        area.display = not self._preview
        box.display = self._preview
        self.query_one(".lore-toggle", Button).label = _label(
            self.app, "fandom_edit" if self._preview else "fandom_preview"
        )
        if self._preview:
            view.update(area.text)
            box.scroll_home(animate=False)  # a re-render starts at the top, not where
            box.can_focus = True            # the last document happened to be left

    def _show_canon(self) -> None:
        """The compiled sheet as it stands, plus a warning when the lore has moved
        under it — a stale sheet is what the writer would otherwise be handed."""
        cfg = self._cfg()
        canon = (cfg.canon if cfg else "").strip()
        text = canon or "—"
        if cfg and canon and cfg.docs_sha != lore_sha(read_lore(cfg)):
            text = f"[yellow]⚠ {_label(self.app, 'fandom_canon_stale')}[/yellow]\n\n{text}"
        try:
            self.query_one(".lore-canon", Static).update(text)
        except Exception:  # not composed yet
            pass

    # -- writing ------------------------------------------------------------

    def _write_file(self, *, if_changed: bool = False) -> Path | None:
        """Write the open document to disk. Returns the path, or None when there is
        no world to write into (or nothing changed, under ``if_changed``)."""
        cfg = self._cfg()
        if cfg is None or cfg.root is None:
            return None
        try:
            text = self.query_one(".lore-area", TextArea).text
        except Exception:  # not mounted yet
            return None
        path = self._path()
        if path is None:
            # a world with no document yet — an explicit save starts one, but an
            # incidental flush must never invent a file (and never out of a text area
            # that has already been cleared for another world)
            if if_changed:
                return None
            path = cfg.root / "lore.md"
        elif if_changed:
            try:
                if path.read_text(encoding="utf-8") == text:
                    return None
            except OSError:
                pass
        created = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self._doc = path.name
        if created:  # the document list grew — the store has to see the new file
            self.app.store = ConfigStore()
            self.reload()
        return path

    def flush(self) -> None:
        """Write pending edits out (leaving the step/tab), without touching the LLM."""
        self._write_file(if_changed=True)

    @on(Select.Changed, ".lore-doc")
    def _doc_changed(self, event: Select.Changed) -> None:
        # a blank is only ever the picker being repopulated — the operator cannot
        # choose one — and a value equal to `_doc` is this widget catching up with a
        # switch it has already made
        if event.value is Select.BLANK:
            return
        name = str(event.value)
        if name == self._doc:
            return
        self._write_file(if_changed=True)  # the document being left keeps its edits
        self._doc = name
        self._load_text()

    @on(Button.Pressed, ".lore-toggle")
    def _toggle(self, event: Button.Pressed) -> None:
        event.stop()
        self._preview = not self._preview
        self._apply_mode()

    @on(Button.Pressed, ".lore-save")
    def _save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        path = self._write_file()
        if path is None:
            self.notify(_label(self.app, "fandom_pick_first"), severity="warning")
            return
        self.notify(f"{_label(self.app, 'fandom_saved')}: {path}", timeout=5)
        self._compile(force=False)

    @on(Button.Pressed, ".lore-recompile")
    def _recompile_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self._write_file() is None:
            self.notify(_label(self.app, "fandom_pick_first"), severity="warning")
            return
        self._compile(force=True)

    # -- compiling the canon sheet (LLM; off the UI thread) -----------------

    def _compile(self, *, force: bool) -> None:
        if self._cfg() is None:
            self.notify(_label(self.app, "fandom_pick_first"), severity="warning")
            return
        self.notify(_label(self.app, "fandom_compiling"), timeout=6)
        name, lang = self._fandom, self.app.store.global_cfg.ui.lang
        self.run_worker(lambda: self._compile_worker(name, force, lang),
                        thread=True, exclusive=False)

    def _compile_worker(self, name: str, force: bool, lang: str) -> None:
        try:
            # re-read from disk: the markdown was written a moment ago, and the
            # checksum has to be taken over what is really there now
            cfg = ConfigStore().fandoms.get(name)
            if cfg is None:
                raise ConfigError(f"fandom '{name}' not found")
            if force:  # a forced rebuild = pretend nothing was ever compiled
                cfg = cfg.model_copy(update={"canon": "", "docs_sha": ""})
            fresh = lore_ai.recompile_if_stale(self._llm(), cfg, read_lore(cfg), lang)
            if fresh is not cfg:
                write_fandom(fresh)
        except Exception as e:  # LLM/network/disk — the markdown is already saved
            self.app.call_from_thread(self._compile_done, str(e))
            return
        self.app.call_from_thread(self._compile_done, None)

    def _compile_done(self, err: str | None) -> None:
        self.app.store = ConfigStore()
        self._show_canon()
        if err is not None:
            self.notify(f"{_label(self.app, 'fandom_compile_err')}: {err}",
                        severity="error", timeout=12)
        else:
            self.notify(_label(self.app, "fandom_compiled"), timeout=5)


class _CharEditAI:
    """Shared '📷 describe from photo' behaviour for the drama character editor.
    The host supplies the active :class:`Form` via ``_char_form`` and wires a thin
    @on handler to ``do_describe``. Blocking LLM calls run in a thread; the result
    lands via the host's ``_apply``."""

    def _char_form(self) -> Form | None:  # overridden by hosts
        raise NotImplementedError

    def _llm(self):
        return ChatLLM(self.app.store.active_llm_profile())

    def do_describe(self) -> None:
        path = self.query_one("#char-photo-path", Input).value.strip()
        if not path:
            self.notify(_label(self.app, "char_need_path"), severity="warning")
            return
        p = Path(path)
        if not p.is_file():
            self.notify(f"{_label(self.app, 'char_no_file')}: {p}", severity="error")
            return
        self.notify(_label(self.app, "char_working"), timeout=3)
        self.run_worker(lambda: self._describe_worker(p), thread=True, exclusive=False)

    def _describe_worker(self, path: Path) -> None:
        try:
            text = char_ai.photo_to_appearance(self._llm(), path)
        except Exception as e:  # LLMError / no vision / http — surface it
            self.app.call_from_thread(
                self.notify, f"{_label(self.app, 'char_photo_err')}: {e}", severity="error", timeout=10
            )
            return
        self.app.call_from_thread(self._apply, {"appearance": text}, "char_described")

    def _apply(self, values: dict, msg_key: str) -> None:
        form = self._char_form()
        if form:
            form.fill(self, values)
        self.notify(_label(self.app, msg_key), timeout=5)


class DramaScreen(_CharEditAI, GenerateScreen):
    """AI-drama wizard. The Characters step holds the drama's plot (scenario) and
    the cast LIST in the middle (name · age · ★global); '＋ Add' opens a picker in
    the right inspector (create new, or pull one from the global library), and
    clicking a member opens its fields there. Members live in the run by default;
    each can be saved to the global library or removed from the drama. AI can fill
    the whole cast (reading everyone + the plot, and rewriting the plot only when
    the prompt asks) or one member (reading only it, steered by its prompt).
    AI-filled fields are tinted. Empty/partial cast or plot is fine — the compiler
    and scriptwriter improvise at generation time. Nothing is saved to the library
    unless you press save."""

    STEPS = DRAMA_STEP_KEYS
    MODE = "drama"
    # what the Summary step calls itself, and the toast on GENERATE — the fandom
    # wizard is this same screen with another world in front of it
    SUMMARY_HEAD = "drama_summary_head"
    START_MSG = "drama_soon"
    # whether this mode is published as a serial. Episodes are a drama's device: the
    # story is cut where it hurts most and each piece goes out on its own. A mode with
    # nothing to hang a cliffhanger on turns the whole apparatus off (field, toggle,
    # summary line, CLI flags, the `cut` stage and its breakpoint).
    HAS_PARTS = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cast: list[dict] = []  # each: fields + glob + ai(set of ai-filled keys)
        self._sel: int | None = None  # index of the member open in the inspector
        self._cast_form: Form | None = None
        self._rev = 0  # bumps each list rebuild so item ids stay unique across async clear()
        self._scenario_ai_val: str | None = None  # AI-set plot, for un-tint on manual edit
        self._think_timers: dict = {}  # prompt-field id -> animation Timer while the AI works
        self._think_orig: dict = {}  # prompt-field id -> its original placeholder
        self._tropes: set[str] = set()  # selected trope keys
        self._protagonist: str = ""  # selected protagonist name (empty = let AI decide)
        # video orchestration (the drama Visuals step): ordered generator stages
        self._stages: list[dict] = []
        self._stage_sel: int | None = None
        self._stage_form: Form | None = None
        self._orch_rev = 0

    def _make_forms(self, t, store: ConfigStore, vis0: VisualsConfig) -> None:
        super()._make_forms(t, store, vis0)
        if self.HAS_PARTS:
            self.f_publish.fields.insert(2, Number("parts", "parts", value="1", default=1, integer=True))
            self.f_publish.fields.insert(3, Toggle("parts_iterative", "parts_iterative", value=True))

    def _content_form(self, store: ConfigStore) -> Form:
        # drama: no content-type / idea — the premise lives in the Story step
        init_lang = "en"
        voice_opts = EDGE_TTS_VOICES.get(init_lang, [])
        return Form("w", [
            Choice("lang", "lang", options=[(l, l) for l in store.languages()], value=init_lang),
            Choice("voice", "voice", options=voice_opts,
                   value=voice_opts[0][1] if voice_opts else None),
            Range("profanity", "profanity", value=store.global_cfg.defaults.profanity,
                  lo=0, hi=100, step=5, labels=PROFANITY_LABELS),
            # speed is authored BEFORE the script here: the writer sizes each beat's
            # narration to how many words fit one clip at this rate
            Range("tts_rate", "tts_rate", value=0,
                  lo=-50, hi=50, step=5, labels=TTS_RATE_LABELS),
            *self._timing_fields(),
            Text("visual_notes", "visual_notes", placeholder="visual_notes_ph", large=True),
        ])

    def _timing_fields(self) -> list:
        """How long, and out of how many clips. A drama is authored in MINUTES with a
        tolerance (it runs long and the story decides where it lands) and with an
        average clip length, because the operator is buying that length from a
        generator whose free tier they are rationing. A mode that buys neither asks
        neither — see FandomScreen."""
        return [
            Number("duration_min", "drama_duration_min", value="2", default=2.0),
            Number("duration_tol", "drama_duration_tol", value="15", default=15.0),
            Number("clip_s", "drama_clip_s", value="0", default=0.0),
        ]

    def _timing(self, c: dict) -> dict:
        """The gathered timing, in the units the pipeline wants (seconds)."""
        return {
            "duration": float(c.get("duration_min") or 2.0) * 60.0,
            "duration_tol": float(c.get("duration_tol") or 15.0),
            "clip_s": max(float(c.get("clip_s") or 0.0), 0.0),
        }

    def _step_help_key(self, step_key: str) -> str:
        if step_key == "step.visuals":  # drama Visuals step = orchestration, not stock/insert settings
            return "help.drama_visuals"
        return super()._step_help_key(step_key)

    # -- required by _CharEditAI --------------------------------------------
    def _char_form(self) -> Form | None:
        return self._cast_form

    def on_mount(self) -> None:
        super().on_mount()
        self._refresh_cast_list()
        if not self._stages:
            self._stages = self._default_stages()
        self._refresh_orch_list()

    # -- orchestration (drama Visuals step) ---------------------------------
    def _orch_profile_opts(self, t):
        return [(t("orch_custom"), CUSTOM)] + [(n, n) for n in self.app.store.orchestrations]

    @staticmethod
    def _default_stages() -> list[dict]:
        return [{"model": "wan2.1", "key_mode": "rotate", "key": "",
                 "metric": "percent", "amount": 100.0, "clip_seconds": 0.0}]

    @staticmethod
    def _new_stage() -> dict:
        return {"model": "wan2.1", "key_mode": "rotate", "key": "",
                "metric": "percent", "amount": 50.0, "clip_seconds": 0.0}

    def _refresh_orch_list(self) -> None:
        try:
            lv = self.query_one("#orch-list", ListView)
        except Exception:
            return
        self._orch_rev += 1
        lv.clear()
        for i, s in enumerate(self._stages):
            km = _label(self.app, "orch_km_rotate" if s["key_mode"] == "rotate" else "orch_km_single")
            metric = _label(self.app, f"orch_m_{s['metric']}")
            clip = s.get("clip_seconds") or 0.0
            clip_badge = f"  ·  {_label(self.app, 'orch_clip_badge').format(s=clip)}" if clip else ""
            item = ListItem(
                Horizontal(
                    Vertical(
                        Static(f"{i + 1}. {s['model']}", classes="cast-name"),
                        Static(km, classes="cast-line cast-dim"),
                        Static(f"→ {s['amount']:g} {metric}{clip_badge}", classes="cast-line"),
                        classes="cast-info",
                    ),
                    classes="cast-row",
                ),
                id=f"orchitem-{self._orch_rev}-{i}", classes="cast-item",
            )
            lv.append(item)

    def _build_stage_form(self, t, s: dict) -> Form:
        nkeys = len(gen_keys(key_var_for_model(s["model"])))
        key_opts = [(t("orch_key_auto"), "")] + [(f"{t('orch_key')} {i + 1}", str(i)) for i in range(nkeys)]
        return Form("e-orch", [
            Choice("model", "orch_model", options=ORCH_MODEL_OPTS, value=s["model"]),
            Choice("key_mode", "orch_key_mode",
                   options=[(t("orch_km_rotate"), "rotate"), (t("orch_km_single"), "single")],
                   value=s["key_mode"]),
            Choice("key", "orch_key", options=key_opts, value=s.get("key", ""), allow_blank=False),
            Choice("metric", "orch_metric",
                   options=[(t("orch_m_clips"), "clips"), (t("orch_m_seconds"), "seconds"),
                            (t("orch_m_percent"), "percent")], value=s["metric"]),
            Number("amount", "orch_amount", value=str(s["amount"]), default=100.0),
            Number("clip_seconds", "orch_clip_s", value=str(s.get("clip_seconds", 0.0)), default=0.0),
        ])

    async def _show_stage_editor(self, idx: int) -> None:
        t = self._t
        self._insp_mode = "stage"
        self._stage_sel = idx
        s = self._stages[idx]
        self._stage_form = self._build_stage_form(t, s)
        widgets = [Static(t("orch_stage_head"), classes="group-head")]
        widgets += self._stage_form.build(t)
        widgets.append(Horizontal(Button(t("orch_remove"), id="orch-remove", variant="error"),
                                  classes="entity-actions"))
        await self._set_inspector(widgets)
        self._stage_form.fill(self, s)

    def _save_stage_editor(self) -> None:
        if self._stage_sel is None or self._stage_form is None:
            return
        try:
            vals = self._stage_form.read(self)
        except Exception:
            return
        s = self._stages[self._stage_sel]
        s["model"] = vals.get("model") or "wan2.1"
        s["key_mode"] = vals.get("key_mode") or "rotate"
        s["key"] = vals.get("key", "")
        s["metric"] = vals.get("metric") or "percent"
        for key, default in (("amount", 100.0), ("clip_seconds", 0.0)):
            try:
                s[key] = max(float(vals.get(key, s.get(key, default))), 0.0)
            except (TypeError, ValueError):
                pass
        self._refresh_orch_list()

    def _set_profile_custom(self) -> None:
        try:
            self.query_one("#orch-profile", Select).value = CUSTOM
        except Exception:
            pass

    @on(ListView.Selected, "#orch-list")
    async def _orch_select(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        idx = int(event.item.id.rsplit("-", 1)[1])
        if idx != self._stage_sel:
            self._save_stage_editor()
        await self._show_stage_editor(idx)

    @on(Button.Pressed, "#orch-add")
    async def _orch_add(self) -> None:
        self._save_stage_editor()
        self._stages.append(self._new_stage())
        self._set_profile_custom()
        self._refresh_orch_list()
        await self._show_stage_editor(len(self._stages) - 1)

    @on(Button.Pressed, "#orch-remove")
    async def _orch_remove(self) -> None:
        if self._stage_sel is None:
            return
        del self._stages[self._stage_sel]
        self._stage_sel, self._stage_form = None, None
        self._set_profile_custom()
        self._refresh_orch_list()
        await self._show_help("step.visuals")

    @on(Button.Pressed, "#orch-up")
    def _orch_up(self) -> None:
        self._move_stage(-1)

    @on(Button.Pressed, "#orch-down")
    def _orch_down(self) -> None:
        self._move_stage(1)

    def _move_stage(self, delta: int) -> None:
        if self._stage_sel is None:
            self.notify(_label(self.app, "orch_pick_first"), severity="warning")
            return
        self._save_stage_editor()
        i = self._stage_sel
        j = i + delta
        if not (0 <= j < len(self._stages)):
            return
        self._stages[i], self._stages[j] = self._stages[j], self._stages[i]
        self._stage_sel = j
        self._set_profile_custom()
        self._refresh_orch_list()

    @on(Select.Changed, "#e-orch-model")
    async def _stage_model_changed(self, event: Select.Changed) -> None:
        if self._stage_sel is None or self._stage_form is None:
            return
        new = str(event.value)
        if new == self._stages[self._stage_sel]["model"]:
            return  # programmatic fill or no real change
        self._save_stage_editor()
        self._stages[self._stage_sel]["key"] = ""  # key indices are provider-specific
        await self._show_stage_editor(self._stage_sel)

    @on(Select.Changed, "#orch-profile")
    def _orch_profile_changed(self, event: Select.Changed) -> None:
        name = str(event.value)
        if name == CUSTOM:
            return
        prof = self.app.store.orchestrations.get(name)
        if not prof:
            return
        self._stages = [st.model_dump() for st in prof.stages] or self._default_stages()
        self._stage_sel, self._stage_form = None, None
        self._refresh_orch_list()
        self.run_worker(self._show_help("step.visuals"))

    @on(Button.Pressed, "#orch-save-prof")
    def _orch_save_profile(self) -> None:
        self._save_stage_editor()
        if not self._stages:
            self.notify(_label(self.app, "orch_empty"), severity="warning")
            return

        def _named(name: str | None) -> None:
            if name:
                self._write_orchestration(name)

        self.app.push_screen(NameModal(_label(self.app, "orch_name")), _named)

    def _write_orchestration(self, name: str) -> None:
        data = {"stages": [{k: s[k] for k in ORCH_FIELDS if k in s} for s in self._stages]}
        path = Path("configs/orchestration") / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        self.app.store = ConfigStore()
        sel = self.query_one("#orch-profile", Select)
        sel.set_options(self._orch_profile_opts(lambda k: _label(self.app, k)))
        sel.value = name
        self.notify(f"{_label(self.app, 'saved')}: {path}", timeout=6)

    @staticmethod
    def _new_member(name: str) -> dict:
        # `ai` maps an AI-filled field -> the value the AI set, so a later manual
        # edit (value diverges) clears the highlight while programmatic fills don't.
        return {"name": name, "age": "", "appearance": "", "glob": False, "ai": {}}

    @staticmethod
    def _member_from_global(c: CharacterConfig) -> dict:
        member = {"name": c.name, "age": c.age, "appearance": c.appearance, "glob": True, "ai": {}}
        member["saved"] = {k: member[k] for k in CHAR_FIELD_KEYS}
        return member

    # -- middle pane: plot + cast list --------------------------------------
    def _pane_body(self, key: str, t):
        if key == "step.visuals":  # drama: the Visuals step is the orchestration editor
            yield Static(t("orch_head"), classes="group-head")
            yield Label(t("orch_profile"))
            yield Select(self._orch_profile_opts(t), id="orch-profile", value=CUSTOM, allow_blank=False)
            with Horizontal(classes="entity-actions"):
                yield Button(t("orch_add"), id="orch-add", variant="success")
                yield Button(t("orch_up"), id="orch-up")
                yield Button(t("orch_down"), id="orch-down")
                yield Button(t("orch_save_prof"), id="orch-save-prof", variant="primary")
            yield ListView(id="orch-list")
            yield Static(t("orch_hint"), classes="hint")
            return
        if key != "step.characters":
            yield from super()._pane_body(key, t)
            return
        yield from self._plot_block(t)
        yield from self._ai_block(t)
        yield from self._cast_block(t)

    # The story step is three blocks, kept separate because the fandom wizard wants
    # them in two different steps: a world's cast belongs with the world, not with
    # the plot written for it.
    def _plot_block(self, t):
        yield Static(t("drama_plot_head"), classes="group-head")
        yield from Text("scenario", "", large=True).build("drama", t)  # id: drama-scenario

    def _ai_block(self, t):
        """AI story polish. Protagonist and tropes are dorama furniture and are the
        drama's alone — see FandomScreen for what a world gets instead."""
        yield Static(t("drama_ai_head"), classes="group-head")
        yield Label(t("drama_protagonist"))
        yield Select(
            [(t("drama_protagonist_none"), "")],
            id="drama-protagonist", allow_blank=False, value="",
        )
        with Horizontal(classes="entity-actions"):
            yield Button(t("drama_tropes_btn"), id="drama-tropes-btn")
        yield from self._ai_prompt_block(t)

    def _ai_prompt_block(self, t):
        yield from Text("prompt", "", placeholder="drama_prompt_ph").build("drama", t)
        with Horizontal(classes="entity-actions"):
            yield Button(t("char_autofill_all"), id="cast-fill-all", variant="primary")

    def _cast_block(self, t):
        yield Static(t("drama_cast_head"), classes="group-head")
        with Horizontal(classes="entity-actions"):
            yield Button(t("drama_add"), id="cast-add", variant="success")
        yield ListView(id="cast-list")
        yield Static(t("drama_cast_hint2"), classes="hint")

    def _member_status(self, m: dict) -> tuple[str, str]:
        """(status label key, css class) for a cast item: world / local / global / global*."""
        if m.get("world"):  # came with the fandom, not assembled by hand
            return ("cast_st_world", "st-global")
        if not m.get("glob"):
            return ("cast_st_local", "st-local")
        fields = {k: m.get(k, "") for k in CHAR_FIELD_KEYS}
        if m.get("saved") != fields:  # edited since it was pulled from / saved to the library
            return ("cast_st_global_dirty", "st-dirty")
        return ("cast_st_global", "st-global")

    def _refresh_protagonist_select(self) -> None:
        try:
            sel = self.query_one("#drama-protagonist", Select)
        except Exception:
            return
        t = self._t
        opts = [(t("drama_protagonist_none"), "")] + [
            (m["name"], m["name"]) for m in self._cast if m.get("name")
        ]
        sel.set_options(opts)
        cur = self._protagonist
        if cur and any(m["name"] == cur for m in self._cast):
            sel.value = cur
        else:
            sel.value = ""
            self._protagonist = ""

    def _refresh_cast_list(self) -> None:
        try:
            lv = self.query_one("#cast-list", ListView)
        except Exception:
            return
        self._refresh_protagonist_select()
        self._rev += 1  # unique id prefix so appends don't clash with the async clear()
        lv.clear()
        for i, m in enumerate(self._cast):
            st_key, st_cls = self._member_status(m)
            look = (m.get("appearance") or "").replace("\n", " ").strip()
            look = (look[:32] + "…") if len(look) > 32 else (look or "—")
            age = m.get("age", "").strip() or "—"
            item = ListItem(
                Horizontal(
                    Vertical(
                        Static(m["name"] or "—", classes="cast-name"),
                        Static(f"{_label(self.app, 'cast_age')}: {age}", classes="cast-line"),
                        Static(look, classes="cast-line cast-dim"),
                        classes="cast-info",
                    ),
                    Static(_label(self.app, st_key), classes=f"cast-status {st_cls}"),
                    classes="cast-row",
                ),
                id=f"castitem-{self._rev}-{i}",
                classes="cast-item",
            )
            lv.append(item)

    # -- duration hint (shown as placeholder in the prompt field after AI rewrites scenario) ---
    def _set_duration_hint(self, minutes: float | None) -> None:
        try:
            ta = self.query_one("#drama-prompt")
        except Exception:
            return
        if minutes and minutes > 0:
            ta.placeholder = _label(self.app, "drama_dur_hint").format(min=minutes)
        else:
            ta.placeholder = _label(self.app, "drama_prompt_ph")

    # -- tropes panel -------------------------------------------------------
    async def _show_tropes_panel(self) -> None:
        t = self._t
        self._insp_mode = "tropes"
        lang = self.app.store.global_cfg.ui.lang
        widgets: list = [Static(t("drama_tropes_head"), classes="group-head")]
        for key, labels in DRAMA_TROPES:
            label, desc = labels.get(lang, labels["en"])
            widgets.append(
                Horizontal(
                    Switch(value=(key in self._tropes), id=f"trope-{key}"),
                    Vertical(Label(label), Static(desc, classes="trope-desc"), classes="trope-text"),
                    classes="trope-row",
                )
            )
        widgets.append(Button(t("drama_tropes_done"), id="tropes-done", variant="primary"))
        await self._set_inspector(widgets)

    @on(Button.Pressed, "#drama-tropes-btn")
    async def _open_tropes(self) -> None:
        await self._show_tropes_panel()

    @on(Button.Pressed, "#tropes-done")
    async def _tropes_done(self) -> None:
        await self._show_help("step.characters")

    @on(Switch.Changed)
    def _trope_switch(self, event: Switch.Changed) -> None:
        wid = event.switch.id or ""
        if not wid.startswith("trope-"):
            return
        key = wid[len("trope-"):]
        if event.value:
            self._tropes.add(key)
        else:
            self._tropes.discard(key)

    @on(Select.Changed, "#drama-protagonist")
    def _protagonist_changed(self, event: Select.Changed) -> None:
        v = event.value
        self._protagonist = "" if v is Select.BLANK else str(v)

    # -- inspector: picker + editor -----------------------------------------
    async def _show_picker(self) -> None:
        t = self._t
        self._insp_mode = "picker"
        self._cast_form = None
        items = [ListItem(Label(n), id=f"pg-{i}") for i, n in enumerate(self.app.store.characters)]
        widgets = [
            Static(t("pick_head"), classes="group-head"),
            Button(t("pick_new"), id="pick-new", variant="success"),
            Static(t("pick_from_lib"), classes="hint"),
            ListView(*items, id="pick-global"),
        ]
        await self._set_inspector(widgets)

    async def _show_editor(self, idx: int) -> None:
        t = self._t
        self._insp_mode = "editor"
        self._sel = idx
        m = self._cast[idx]
        self._cast_form = _entity_form("characters")
        widgets = [Static(t("char_edit_head"), classes="group-head")]
        widgets += self._cast_form.build(t)
        widgets.append(Horizontal(Input(placeholder=t("char_photo_ph"), id="char-photo-path"),
                                  Button(t("char_describe"), id="char-describe", variant="primary"),
                                  id="char-photo-row"))
        widgets.extend(Text("prompt", "", placeholder="char_prompt_ph").build("char", t))
        widgets.append(Horizontal(Button(t("char_autofill"), id="char-autofill", variant="primary"),
                                  classes="entity-actions"))
        widgets.append(Horizontal(Button(t("cast_save_global"), id="cast-save-global", variant="success"),
                                  Button(t("cast_remove"), id="cast-remove", variant="error"),
                                  classes="entity-actions"))
        await self._set_inspector(widgets)
        self._cast_form.fill(self, m)
        self._highlight_ai(m["ai"])

    def _highlight_ai(self, keys) -> None:
        for k in char_ai.FILLABLE:
            try:
                self.query_one(f"#e-characters-{k}").set_class(k in keys, "ai-filled")
            except Exception:
                pass

    def _maybe_unhighlight(self, wid: str | None, value: str) -> None:
        """Drop the AI tint from a field once the user edits it away from the
        value the AI set (programmatic fills leave value == ai value → no change)."""
        if self._sel is None or not wid or not wid.startswith("e-characters-"):
            return
        key = wid[len("e-characters-"):]
        ai = self._cast[self._sel].get("ai", {})
        if key in ai and value != ai[key]:
            del ai[key]
            try:
                self.query_one(f"#{wid}").remove_class("ai-filled")
            except Exception:
                pass

    # -- "thinking…" indicator on a prompt field while the AI works ---------
    def _start_thinking(self, input_id: str) -> None:
        """Clear a prompt field, disable it, and animate a 'Thinking…' placeholder."""
        try:
            inp = self.query_one(f"#{input_id}", Input)
        except Exception:
            try:
                inp = self.query_one(f"#{input_id}", TextArea)
            except Exception:
                return
        self._think_orig[input_id] = (
            getattr(inp, "placeholder", None) if hasattr(inp, "placeholder") else getattr(inp, "tooltip", "")
        ) or ""
        if isinstance(inp, Input):
            inp.value = ""
        else:
            inp.text = ""
        inp.disabled = True
        base = _label(self.app, "ai_thinking")
        state = {"n": 0}

        def tick() -> None:
            state["n"] = (state["n"] + 1) % 4
            try:
                if hasattr(inp, "placeholder"):
                    inp.placeholder = base + "." * state["n"]
                else:
                    inp.tooltip = base + "." * state["n"]
            except Exception:
                pass

        if hasattr(inp, "placeholder"):
            inp.placeholder = base
        else:
            inp.tooltip = base
        self._think_timers[input_id] = self.set_interval(0.4, tick)

    def _stop_thinking(self, input_id: str) -> None:
        timer = self._think_timers.pop(input_id, None)
        if timer is not None:
            timer.stop()
        try:
            inp = self.query_one(f"#{input_id}", Input)
        except Exception:
            try:
                inp = self.query_one(f"#{input_id}", TextArea)
            except Exception:
                return
        try:
            inp.disabled = False
            if hasattr(inp, "placeholder"):
                inp.placeholder = self._think_orig.get(input_id, "")
            else:
                inp.tooltip = self._think_orig.get(input_id, "")
        except Exception:
            pass

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        super().on_descendant_focus(event)
        if (event.widget.id or "") == "drama-prompt":
            self._set_duration_hint(None)

    @on(Input.Changed)
    def _inp_changed(self, event: Input.Changed) -> None:
        self._maybe_unhighlight(event.input.id, event.value)

    @on(TextArea.Changed)
    def _ta_changed(self, event: TextArea.Changed) -> None:
        # (auto-resize is handled app-wide in SlopgenApp)
        if event.text_area.id == "drama-scenario":  # the drama plot has its own tint
            if self._scenario_ai_val is not None and event.text_area.text != self._scenario_ai_val:
                self._scenario_ai_val = None
                event.text_area.remove_class("ai-filled")
                self._set_duration_hint(None)
            return
        self._maybe_unhighlight(event.text_area.id, event.text_area.text)

    def _save_editor(self) -> None:
        """Persist the open editor back into the cast member (auto-save in run)."""
        if self._sel is None or self._cast_form is None:
            return
        try:
            vals = self._cast_form.read(self)
        except Exception:
            return  # editor not mounted anymore
        m = self._cast[self._sel]
        for k in CHAR_FIELD_KEYS:
            m[k] = vals.get(k, m.get(k, ""))
        self._refresh_cast_list()

    def _on_leave_step(self) -> None:
        self._save_editor()
        self._save_stage_editor()
        self._sel = None
        self._cast_form = None
        self._stage_sel = None
        self._stage_form = None

    # -- inspector actions --------------------------------------------------
    @on(Button.Pressed, "#cast-add")
    async def _add(self) -> None:
        self._save_editor()
        await self._show_picker()

    @on(Button.Pressed, "#pick-new")
    async def _pick_new(self) -> None:
        self._cast.append(self._new_member(_label(self.app, "char_new_name")))
        self._refresh_cast_list()
        await self._show_editor(len(self._cast) - 1)

    @on(ListView.Selected, "#pick-global")
    async def _pick_global(self, event: ListView.Selected) -> None:
        i = int(event.item.id.split("-")[1])
        names = list(self.app.store.characters)
        if i >= len(names):
            return
        c = self.app.store.characters[names[i]]
        self._cast.append(self._member_from_global(c))
        self._refresh_cast_list()
        await self._show_editor(len(self._cast) - 1)

    @on(ListView.Selected, "#cast-list")
    async def _cast_select(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        idx = int(event.item.id.rsplit("-", 1)[1])
        if idx != self._sel:
            self._save_editor()
        await self._show_editor(idx)

    @on(Button.Pressed, "#cast-remove")
    async def _remove(self) -> None:
        if self._sel is None:
            return
        del self._cast[self._sel]
        self._sel, self._cast_form = None, None
        self._refresh_cast_list()
        await self._show_help("step.characters")

    @on(Button.Pressed, "#cast-save-global")
    def _save_global(self) -> None:
        if self._sel is None:
            return
        self._save_editor()
        m = self._cast[self._sel]
        name = m["name"].strip()
        if not name:
            self.notify(_label(self.app, "name_req"), severity="warning")
            return
        try:
            path = _write_character(self.app.store, name, m)
        except Exception as e:
            self.notify(f"{_label(self.app, 'err.save')}: {e}", severity="error", timeout=8)
            return
        self.app.store = ConfigStore()
        m["glob"] = True
        m["saved"] = {k: m.get(k, "") for k in CHAR_FIELD_KEYS}  # snapshot: now in sync with the library
        self._refresh_cast_list()
        self.notify(f"{_label(self.app, 'saved')}: {path}", timeout=6)

    # -- AI fill ------------------------------------------------------------
    @on(Button.Pressed, "#char-describe")
    def _bd(self) -> None:
        self.do_describe()  # from _CharEditAI; fills appearance via _apply

    def _apply(self, values: dict, msg_key: str) -> None:
        """Override _CharEditAI._apply: also persist into the cast member + tint."""
        if self._sel is not None:
            m = self._cast[self._sel]
            m.update(values)
            m["ai"].update({k: values[k] for k in values if k in char_ai.FILLABLE})  # before fill()
        if self._cast_form:
            try:
                self._cast_form.fill(self, values)
            except Exception:
                pass
        if self._sel is not None:
            self._highlight_ai(self._cast[self._sel]["ai"])
            self._refresh_cast_list()
        self.notify(_label(self.app, msg_key), timeout=5)

    @on(Button.Pressed, "#char-autofill")
    def _bf(self) -> None:
        if self._sel is None:
            return
        self._save_editor()
        member = dict(self._cast[self._sel])
        prompt = ""
        try:
            prompt = self.query_one("#char-prompt", TextArea).text.strip()
        except Exception:
            pass
        lang = self.app.store.global_cfg.ui.lang
        idx = self._sel
        self._start_thinking("char-prompt")
        self.run_worker(lambda: self._one_worker(idx, member, lang, prompt), thread=True, exclusive=False)

    def _one_worker(self, idx: int, member: dict, lang: str, prompt: str) -> None:
        try:
            changed = char_ai.autofill_one(self._llm(), member, lang, prompt)
        except Exception as e:
            self.app.call_from_thread(self._one_done, idx, None, str(e))
            return
        self.app.call_from_thread(self._one_done, idx, changed, None)

    def _one_done(self, idx: int, changed: dict | None, err: str | None) -> None:
        self._stop_thinking("char-prompt")
        if err is not None:
            self.notify(f"{_label(self.app, 'char_ai_err')}: {err}", severity="error", timeout=10)
        elif not changed:
            self.notify(_label(self.app, "char_nothing"), timeout=5)
        else:
            self._apply_changes({idx: changed}, "char_filled")

    def _scenario_text(self) -> str:
        try:
            return self.query_one("#drama-scenario").text
        except Exception:
            return ""

    @on(Button.Pressed, "#cast-fill-all")
    def _fill_all(self) -> None:
        self._save_editor()
        scenario = self._scenario_text()
        prompt = ""
        try:
            prompt = self.query_one("#drama-prompt", TextArea).text.strip()
        except Exception:
            pass
        if not self._cast and not scenario.strip() and not prompt:
            self.notify(self._t("cast_empty"), severity="warning")
            return
        lang = self.app.store.global_cfg.ui.lang
        cast_copy = [dict(m) for m in self._cast]
        library = [
            {"name": c.name, "age": c.age, "appearance": c.appearance}
            for c in self.app.store.characters.values()
        ]
        tropes = [labels["en"][0] for key, labels in DRAMA_TROPES if key in self._tropes]
        protagonist = self._protagonist
        self._start_thinking("drama-prompt")
        self.run_worker(
            lambda: self._all_worker(cast_copy, lang, scenario, prompt, tropes,
                                     protagonist, library),
            thread=True, exclusive=False,
        )

    def _all_worker(
        self, cast: list[dict], lang: str, scenario: str, prompt: str,
        tropes: list[str] | None = None, protagonist: str = "",
        library: list[dict] | None = None,
    ) -> None:
        try:
            res = char_ai.autofill_all(
                self._llm(), cast, lang, scenario, prompt,
                tropes=tropes or [], protagonist=protagonist, library=library or [],
            )
        except Exception as e:
            self.app.call_from_thread(self._all_done, None, str(e))
            return
        self.app.call_from_thread(self._all_done, res, None)

    def _all_done(self, res: dict | None, err: str | None) -> None:
        self._stop_thinking("drama-prompt")
        if err is not None:
            self.notify(f"{_label(self.app, 'char_ai_err')}: {err}", severity="error", timeout=10)
            return
        by_idx = {i: ch for i, ch in enumerate(res.get("cast", [])) if ch}
        scen = res.get("scenario")
        rec_dur = res.get("recommended_duration_min")
        add_global = res.get("add_global") if isinstance(res.get("add_global"), list) else []
        new_characters = res.get("new_characters") if isinstance(res.get("new_characters"), list) else []
        msg = "char_filled" if (by_idx or scen or add_global or new_characters) else "char_nothing"
        self._apply_changes(
            by_idx, msg, scen, add_global=add_global, new_characters=new_characters,
            recommended_duration_min=rec_dur,
        )

    def _apply_changes(
        self,
        by_idx: dict[int, dict],
        msg_key: str,
        scenario_new: str | None = None,
        add_global: list[str] | None = None,
        new_characters: list[dict] | None = None,
        recommended_duration_min: float | None = None,
    ) -> None:
        """Merge AI changes into members / the plot, tint them, refresh the editor."""
        for idx, changed in by_idx.items():
            if idx >= len(self._cast):
                continue
            m = self._cast[idx]
            m.update(changed)
            m["ai"].update({k: changed[k] for k in changed if k in char_ai.FILLABLE})
        existing = {m.get("name", "").casefold() for m in self._cast if m.get("name")}
        global_by_name = {name: c for name, c in self.app.store.characters.items()}
        global_by_fold = {name.casefold(): c for name, c in self.app.store.characters.items()}
        global_names = {c.name.casefold() for c in self.app.store.characters.values()}
        for raw_name in add_global or []:
            name = str(raw_name).strip()
            c = global_by_name.get(name) or global_by_fold.get(name.casefold())
            if not c or c.name.casefold() in existing:
                continue
            self._cast.append(self._member_from_global(c))
            existing.add(c.name.casefold())
        for row in new_characters or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name or name.casefold() in existing or name.casefold() in global_names:
                continue
            member = self._new_member(name)
            changed = {
                k: str(row.get(k, "")).strip()
                for k in char_ai.FILLABLE
                if str(row.get(k, "")).strip()
            }
            member.update(changed)
            member["ai"].update(changed)
            self._cast.append(member)
            existing.add(name.casefold())
        if scenario_new:  # only returned when the prompt asked to rewrite the plot
            self._scenario_ai_val = scenario_new
            try:
                ta = self.query_one("#drama-scenario")
                ta.text = scenario_new
                ta.add_class("ai-filled")
            except Exception:
                pass
        self._set_duration_hint(recommended_duration_min if scenario_new else None)
        if self._sel is not None and self._sel in by_idx and self._cast_form:
            try:
                self._cast_form.fill(self, by_idx[self._sel])
            except Exception:
                pass
            self._highlight_ai(self._cast[self._sel]["ai"])
        self._refresh_cast_list()
        self.notify(_label(self.app, msg_key), timeout=5)

    # -- summary / launch ---------------------------------------------------
    def _selected_cast(self) -> list[str]:
        return [m["name"] for m in self._cast]

    def _gather(self) -> dict:
        # the drama wizard has no visuals form (orchestration replaces it), so
        # gather straight from the forms it DOES have + defaults. Length is authored
        # in minutes (+ a seconds tolerance) on the Content step.
        c = self.f_content.read(self)
        a = self.f_ads.read(self)
        p = self.f_publish.read(self)
        return {
            "lang": c["lang"], "voice": c["voice"], "ctype": "", "idea": "",
            "profanity": c["profanity"],
            "tts_rate": c.get("tts_rate", 0),
            **self._timing(c),
            "ad_src": a["ad-src"], "ad_mode": a["ad-mode"] or "both",
            "push": "" if p["push"] == NONE else p["push"],
            "subs": p["subs"], "count": max(1, int(p["count"])),
            "clean_subs": bool(p.get("clean_subs")),
            "visual_notes": c.get("visual_notes", ""),
            "parts": max(1, int(p.get("parts", 1) or 1)),
            "parts_iterative": bool(p.get("parts_iterative", True)),
            "breakpoints": self._breakpoints(),
        }

    def _render_summary(self) -> None:
        t = self._t
        self._save_editor()
        self._save_stage_editor()
        g = self._gather()
        cast = ", ".join(self._selected_cast()) or "—"
        glob = ", ".join(m["name"] for m in self._cast if m["glob"]) or "—"
        plot = self._scenario_text().strip().replace("\n", " ")
        plot = (plot[:80] + "…") if len(plot) > 80 else (plot or "—")
        stages = " → ".join(s["model"] for s in self._stages) or "—"
        clip = f"{g['clip_s']:g}s" if g["clip_s"] else t("drama_clip_auto")
        lines = [
            f"[b]{t(self.SUMMARY_HEAD)}[/b]",
            "",
            *self._summary_timing_lines(t, g, clip),
            f"  {t('drama_plot_head')}: {plot}",
            *self._summary_cast_lines(t, cast, glob),
            *self._summary_source_lines(t, stages),
            *self._summary_extra(g),
            "",
            f"  [dim]{t('drama_soon_note')}[/dim]",
        ]
        self.query_one("#w-summary", Static).update("\n".join(lines))
        self.query_one("#w-cmd", Static).update(f"$ {self._drama_command(g)}")

    def _summary_timing_lines(self, t, g: dict, clip: str) -> list[str]:
        """How the summary reports length. Mirrors what the Content step asked for, so
        a mode that never asked about tolerance or clip length does not report them."""
        return [
            f"  {t('lang')}: [b]{g['lang']}[/b]      {t('duration')}: "
            f"~{g['duration'] / 60:.1f} min ±{g['duration_tol']:.0f}s"
            + (f"      {t('parts')}: [b]{g['parts']}[/b]"
               + ("" if g["parts_iterative"] else f" ({t('parts_batch')})")
               if g["parts"] != 1 else ""),
            f"  {t('drama_clip_s').split(',')[0]}: [b]{clip}[/b]"
            f"      {t('tts_rate').split(' (')[0]}: [b]{g['tts_rate']:+d}%[/b]",
        ]

    def _summary_source_lines(self, t, stages: str) -> list[str]:
        """How the summary names where the picture comes from — a chain, or one source."""
        return [f"  {t('orch_head')}: [b]{stages}[/b]"]

    def _summary_cast_lines(self, t, cast: str, glob: str) -> list[str]:
        """How the summary reports who is in it. The drama names the run cast and,
        separately, which of them came from the shared library — a distinction that
        exists because its members are ad-hoc unless saved. A world's people have no
        such split (see FandomScreen), so it overrides this with one line."""
        return [f"  {t('drama_cast_head')}: [b]{cast}[/b]",
                f"  ★ {t('cfg.characters')}: {glob}"]

    def _summary_extra(self, g: dict) -> list[str]:
        """Extra summary lines a mode adds (the fandom wizard names its world)."""
        return []

    def _mode_flags(self, g: dict) -> str:
        """Extra CLI flags a mode contributes to the previewed command."""
        return ""

    def _timing_flags(self, g: dict) -> str:
        return (f" --duration-min {g['duration'] / 60:g} --tol {g['duration_tol']:g}"
                + (f" --clip-s {g['clip_s']:g}" if g["clip_s"] else ""))

    def _drama_command(self, g: dict) -> str:
        cmd = f"slopgen {self.MODE} {g['lang']}"
        cmd += self._mode_flags(g)
        cmd += self._timing_flags(g)
        if g["clean_subs"]:
            cmd += " --clean-subs"
        if g["visual_notes"]:
            note = g["visual_notes"].replace("\n", " ")[:40]
            cmd += f' --visual-notes "{note}…"'
        if g["parts"] != 1:
            cmd += f" --parts {g['parts']}"
            if not g["parts_iterative"]:
                cmd += " --parts-at-once"
        glob = [m["name"] for m in self._cast if m["glob"]]
        if glob:
            cmd += f" --cast {','.join(glob)}"
        if g["ad_src"] not in (NONE, MANUAL):
            cmd += f" --ad {g['ad_src']} --ad-mode {g['ad_mode']}"
        if g["profanity"]:
            cmd += f" --profanity {g['profanity']}"
        if g["tts_rate"]:
            cmd += f" --tts-rate {g['tts_rate']}"
        if g["push"]:
            cmd += f" --push {g['push']}"
        if g["count"] != 1:
            cmd += f" -n {g['count']}"
        for name in g["breakpoints"]:
            cmd += f" --break {name}"
        notes = []
        if any(not m["glob"] for m in self._cast):
            notes.append("ad-hoc cast")
        if g["ad_src"] == MANUAL:
            notes.append("manual ad")
        notes.append("orchestration")  # ad-hoc chain is TUI-only
        return cmd + f"  # + {', '.join(notes)} (TUI only)"

    def _start(self) -> None:
        self._save_editor()
        self._save_stage_editor()
        g = self._gather()
        cast = [
            CharacterConfig(
                name=m["name"], age=m.get("age", ""), appearance=m.get("appearance", ""), dirty=True,
            )
            for m in self._cast if m.get("name")
        ]
        orch = OrchestrationConfig(
            name="manual",
            stages=[
                OrchestrationStage(
                    model=s["model"], key_mode=s["key_mode"], key=s.get("key", ""),
                    metric=s["metric"], amount=float(s["amount"]),
                    clip_seconds=float(s.get("clip_seconds") or 0.0),
                )
                for s in self._stages
            ],
        )
        try:
            params = RunParams(
                lang=g["lang"], content_type="", mode=self.MODE,
                scenario=self._scenario_text().strip(),
                manual_cast=cast,
                manual_orchestration=orch,
                duration_s=g["duration"], duration_tol_s=g["duration_tol"],
                clip_seconds=g["clip_s"], parts=g["parts"],
                parts_iterative=g["parts_iterative"],
                profanity=g["profanity"],
                ad=g["ad_src"] if g["ad_src"] not in (NONE, MANUAL) else "",
                manual_ad=self._manual_ad_config(g["ad_src"]),
                ad_mode=g["ad_mode"],
                push=g["push"], count=g["count"],
                voice_override=g["voice"], tts_rate=g["tts_rate"],
                subtitle_style=g["subs"],
                breakpoints=g["breakpoints"],
                clean_subtitles=g["clean_subs"],
                visual_notes=g["visual_notes"],
                **self._extra_params(g),
            )
        except Exception as e:  # pydantic validation / bad field
            self.notify(str(e), severity="error", timeout=8)
            return
        self.notify(
            _label(self.app, self.START_MSG).format(n=len(cast), name=g.get("fandom", "")),
            timeout=4,
        )
        self.app.push_screen(ProgressScreen(params))

    def _extra_params(self, g: dict) -> dict:
        """RunParams fields only some modes have (the fandom's world + narrator)."""
        return {}


class FandomScreen(DramaScreen):
    """Fandom wizard. It shares the drama's plumbing — length, orchestration, ads,
    publishing — and nothing of its content model, because the two are not the same
    kind of thing.

    A drama's cast is assembled per run: members are ad-hoc unless you save them,
    pulled from a shared library, toggled in and out for one video. A world's people
    are not like that. They are IN the world or they are not; nobody appears for one
    video and vanishes after it. So the World step edits the world's own character
    files directly (the same editor as Configuration → Fandoms) — there is no run
    cast, no global library to borrow from, and the run carries no `manual_cast` at
    all: the pipeline reads the world's people straight off the fandom.

    Episodes are gone for the same reason: a serial is cut where it hurts most, and an
    account of a world has no cliffhanger to hang a break on (see HAS_PARTS).
    """

    STEPS = FANDOM_STEP_KEYS
    MODE = "fandom"
    SUMMARY_HEAD = "fandom_summary_head"
    START_MSG = "fandom_soon"
    HAS_PARTS = False

    # Where this mode says something the drama's wording does not fit. Most of the
    # wizard is genuinely the same machine, but a fandom operator should never be
    # told about a drama they are not making — nor offered dorama furniture.
    LABELS = {
        "step.characters": "fandom_step_brief",
        "help.step.characters": "help.fandom_brief",
        "help.step.fandom": "help.fandom_world",
        "help.step.visuals": "help.fandom_visuals_step",
        "drama_plot_head": "fandom_plot_head",
        "drama_ai_head": "fandom_ai_head",
        "drama_cast_head": "fandom_cast_head",
        "help.drama_duration_min": "help.fandom_duration_min",
        "help.drama_scenario": "help.fandom_scenario",
        "help.drama_visuals": "help.fandom_visuals",
        "help.tts_rate": "help.fandom_tts_rate",
        "drama_soon_note": "fandom_soon_note",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fandom = ""  # folder name of the picked world
        self._person: int | None = None  # index into the world's people, or None
        self._person_form: Form | None = None
        self.f_fandom: Form | None = None
        self.f_voice: Form | None = None

    # -- forms ---------------------------------------------------------------

    def _make_forms(self, t, store: ConfigStore, vis0: VisualsConfig) -> None:
        super()._make_forms(t, store, vis0)
        self._fandom = self._fandom or next(iter(store.fandoms), "")
        self.f_fandom = Form("wf", [
            Choice("fandom", "fandom_pick", options=[(n, n) for n in store.fandoms],
                   value=self._fandom or None, allow_blank=True),
        ])
        # the narrator belongs with the brief, not with the world: it is a choice
        # about THIS video (who tells it), while the world is the same whoever does
        self.f_voice = Form("wv", [
            Choice("voice", "fandom_voice",
                   options=[(t("fandom_voice_resident"), "resident"),
                            (t("fandom_voice_chronicler"), "chronicler")],
                   value="resident"),
        ])
        # Where the shots come from: ONE list, the same one a drama's chain stage picks
        # from. Splitting it into "source" + "generator" made the operator answer two
        # questions to say one thing, and hid the photo generators behind a branch —
        # `flux`/`turbo` are how you ask for stills, and they belong in plain sight next
        # to the video ones. The two operator-supplied entries sit in the same list
        # because that is what they are: another answer to "who makes this shot".
        self.f_source = Form("ws", [
            # What the video is MADE OF comes first, because it decides what can make
            # it: a slideshow of stills and a run of clips are different pieces of work
            # with different sources, and offering all of them at once was how the
            # photo generators ended up invisible.
            Choice("medium", "fandom_medium", options=[
                (t("fandom_medium_video"), "video"),
                (t("fandom_medium_photo"), "photo"),
            ], value="video"),
            Group("ws-vid", [
                Choice("vsrc", "fandom_source", options=VIDEO_SOURCES, value="wan2.1"),
            ], visible_when=lambda v: v["medium"] != "photo"),
            Group("ws-img", [
                Choice("psrc", "fandom_source", options=PHOTO_SOURCES, value="flux"),
            ], visible_when=lambda v: v["medium"] == "photo"),
            Note("fandom_source_note"),
        ])

    # -- the two steps that are this mode's own ------------------------------

    def _pane_body(self, key: str, t):
        if key == "step.fandom":
            if not self.app.store.fandoms:
                yield Static(t("fandom_none"), classes="hint")
            yield from self.f_fandom.compose(t)
            # The world's people, edited as what they are: files in the world's
            # folder. Same widget as Configuration → Fandoms, so there is one way to
            # author a world and it does not matter where you are standing.
            yield Static(t("fandom_cast_head"), classes="group-head")
            with Horizontal(classes="entity-actions"):
                yield Button(t("fandom_add_person"), id="world-add", variant="success")
            yield ListView(id="world-list")
            yield Static(t("fandom_cast_hint2"), classes="hint")
            yield LoreEditor(self._fandom, prefix="wlore", id="wizard-lore")
            return
        if key == "step.visuals":
            yield Static(t("fandom_source_head"), classes="group-head")
            yield from self.f_source.compose(t)
            return
        if key == "step.characters":
            yield from self.f_voice.compose(t)
            yield from self._plot_block(t)
            yield Static(t("drama_ai_head"), classes="group-head")
            yield from Text("prompt", "", placeholder="fandom_prompt_ph").build("drama", t)
            with Horizontal(classes="entity-actions"):
                yield Button(t("fandom_write_brief"), id="fandom-brief-ai", variant="primary")
            return
        yield from super()._pane_body(key, t)

    # -- keeping the world's editors pointed at the picked world -------------

    def on_mount(self) -> None:
        super().on_mount()
        self.call_after_refresh(self._point_at_world)
        # this mode adds a form of its own, and a Group's visible_when is only ever
        # evaluated when something asks (see GenerateScreen.on_mount)
        self.call_after_refresh(self._refresh_source_form)

    def _refresh_source_form(self) -> None:
        try:
            self.f_source.refresh_visibility(self)
        except Exception:  # the step never composed
            pass

    @on(Select.Changed, "#ws-medium")
    def _medium_changed(self, event: Select.Changed) -> None:
        """Stills and clips are made by different things; show only the right ones."""
        self._refresh_source_form()

    def _point_at_world(self) -> None:
        self._person = None
        self._person_form = None
        self._refresh_people()

    # -- the world's people: a list here, the editor in the inspector --------
    #
    # The same shape every other wizard step has, and for the same reason: the middle
    # column is forty characters wide and the panel on the right is otherwise showing
    # nothing but help. What is NOT shared with the drama is what an edit means —
    # every save here writes the world's own character file, because these people
    # belong to the world and there is no run cast for them to live in.

    def _people(self) -> list[CharacterConfig]:
        cfg = self.app.store.fandoms.get(self._fandom)
        return list(cfg.cast) if cfg else []

    def _people_dir(self) -> Path:
        return FANDOMS_DIR / self._fandom / "characters"

    def _refresh_people(self) -> None:
        try:
            lv = self.query_one("#world-list", ListView)
        except Exception:
            return
        self._rev += 1
        lv.clear()
        palette = theme_identity(self.app.current_theme)
        colours = identity_colors([c.name for c in self._people()], palette)
        ground = (self.app.current_theme.background if self.app.current_theme else "#1D1D21")
        for i, c in enumerate(self._people()):
            look = " ".join((c.appearance or "").split())
            look = (look[:44] + "…") if len(look) > 44 else look
            age = (c.age or "").strip()
            sub = f"{age} · {look}" if age and look else (age or look)
            name = Static(c.name or "—", classes="world-name")
            # as TEXT, so the palette colour is lifted until it reads on the panel
            name.styles.color = identity_ink(colours.get(c.name, palette[0]), ground)
            lv.append(ListItem(
                Vertical(
                    name,
                    Static(sub or _label(self.app, "fandom_person_blank"), classes="world-line"),
                    classes="world-info",
                ),
                id=f"wp-{self._rev}-{i}",
                classes="world-item",
            ))

    async def _show_person(self, idx: int) -> None:
        t = self._t
        people = self._people()
        self._insp_mode = "editor"
        self._person = idx
        cur = people[idx] if 0 <= idx < len(people) else None
        self._person_form = _entity_form("characters")
        widgets = [Static(t("fandom_person_head"), classes="group-head")]
        widgets += self._person_form.build(t)
        widgets.append(Horizontal(Input(placeholder=t("char_photo_ph"), id="char-photo-path"),
                                  Button(t("char_describe"), id="char-describe", variant="primary"),
                                  id="char-photo-row"))
        widgets.extend(Text("prompt", "", placeholder="char_prompt_ph").build("char", t))
        widgets.append(Horizontal(Button(t("fandom_fill_person"), id="world-fill", variant="primary"),
                                  classes="entity-actions"))
        widgets.append(Horizontal(Button(t("save"), id="world-save", variant="success"),
                                  Button(t("fandom_remove_person"), id="world-del", variant="error"),
                                  classes="entity-actions"))
        await self._set_inspector(widgets)
        self._person_form.fill(self, {
            "name": cur.name if cur else "", "age": cur.age if cur else "",
            "appearance": cur.appearance if cur else "",
        })

    def _char_form(self) -> Form | None:
        """What the shared photo→appearance helper writes into. On the World step it
        is the person editor; the drama's own editor is never open here."""
        return self._person_form

    @on(ListView.Selected, "#world-list")
    async def _person_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        await self._show_person(int(event.item.id.rsplit("-", 1)[1]))

    @on(Button.Pressed, "#world-add")
    async def _person_add(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._fandom:
            self.notify(self._t("fandom_none"), severity="warning")
            return
        await self._show_person(-1)  # a blank editor; the file appears on save

    @on(Button.Pressed, "#world-save")
    def _person_save(self, event: Button.Pressed) -> None:
        """Write the person into the world. There is no 'save to library' step and no
        unsaved run copy: the world's folder is the only place they exist."""
        event.stop()
        form = self._person_form
        if form is None or not self._fandom:
            return
        vals = form.read(self)
        name = str(vals.get("name", "")).strip()
        if not name:
            self.notify(_label(self.app, "name_req"), severity="warning")
            return
        people = self._people()
        existing = people[self._person] if self._person is not None and 0 <= self._person < len(people) else None
        try:
            path = _write_character(self.app.store, name, vals,
                                    directory=self._people_dir(), existing=existing)
        except Exception as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self.app.store = ConfigStore()
        self._refresh_people()
        names = [c.name for c in self._people()]
        self._person = names.index(name) if name in names else None
        self.notify(f"{_label(self.app, 'saved')}: {path}", timeout=5)

    @on(Button.Pressed, "#world-del")
    async def _person_del(self, event: Button.Pressed) -> None:
        event.stop()
        people = self._people()
        if self._person is None or not (0 <= self._person < len(people)):
            return
        victim = people[self._person]

        async def go(ok: bool) -> None:
            if not ok:
                return
            path = self._people_dir() / f"{victim.name}.toml"
            try:
                path.unlink()
            except OSError as e:
                self.notify(str(e), severity="error", timeout=8)
                return
            self.app.store = ConfigStore()
            self._person, self._person_form = None, None
            self._refresh_people()
            await self._show_help("step.fandom")

        self.app.push_screen(ConfirmModal(_label(self.app, "confirm_del").format(name=victim.name)), go)

    @on(Button.Pressed, "#world-fill")
    def _person_fill(self, event: Button.Pressed) -> None:
        """Fill this person's age and looks from the WORLD. The drama's cast fill
        invents an ensemble to fit a plot; this one may invent only what this place
        could already contain."""
        event.stop()
        form = self._person_form
        if form is None or not self._fandom:
            return
        member = form.read(self)
        if not str(member.get("name", "")).strip():
            self.notify(_label(self.app, "name_req"), severity="warning")
            return
        try:
            prompt = self.query_one("#char-prompt", TextArea).text.strip()
        except Exception:
            prompt = ""
        lang = self.app.store.global_cfg.ui.lang
        world = self._world_text()
        self.notify(_label(self.app, "char_working"), timeout=3)
        self.run_worker(lambda: self._person_worker(member, lang, prompt, world),
                        thread=True, exclusive=False)

    def _person_worker(self, member: dict, lang: str, prompt: str, world: str) -> None:
        try:
            changed = char_ai.autofill_one(self._llm(), member, lang, prompt, world=world)
        except Exception as e:
            self.app.call_from_thread(
                self.notify, f"{_label(self.app, 'char_ai_err')}: {e}",
                severity="error", timeout=10)
            return
        if not changed:
            self.app.call_from_thread(self.notify, _label(self.app, "char_nothing"), timeout=5)
            return
        self.app.call_from_thread(self._apply, changed, "char_filled")

    @on(Select.Changed, "#wf-fandom")
    def _fandom_changed(self, event: Select.Changed) -> None:
        name = "" if event.value is Select.BLANK else str(event.value)
        if name == self._fandom:
            return
        self._fandom = name
        try:
            self.query_one("#wizard-lore", LoreEditor).set_fandom(name)
        except Exception:
            pass
        self._point_at_world()

    @on(ListView.Highlighted, "#wizard-nav")
    def _world_required(self, event: ListView.Highlighted) -> None:
        """Nothing past the World step means anything without a world, so bounce back
        to it. ``prevent_default`` is what stops the base handler (further along the
        MRO) from switching the pane under us."""
        if event.item is None or self._fandom:
            return
        first = self.STEPS.index("step.fandom")
        if int(event.item.id.split("-")[1]) <= first:
            return
        event.prevent_default()
        self.notify(self._t("fandom_none"), severity="warning", timeout=8)
        self.call_after_refresh(self._goto, first)

    # -- AI help: the brief, and only the brief ------------------------------

    def _world_text(self) -> str:
        """The world as the AI sees it: the compiled sheet when there is one, else the
        lore itself (a small world is never compiled — fandom_canon.SMALL_LORE_CHARS)."""
        cfg = self.app.store.fandoms.get(self._fandom)
        if not cfg:
            return ""
        return (cfg.canon or "").strip() or read_lore(cfg)

    @on(Button.Pressed, "#fandom-brief-ai")
    def _write_brief(self, event: Button.Pressed) -> None:
        """Propose or rewrite what this video is about. It never touches the world's
        people — they are the world's, and inventing one here would be inventing a
        person into a place that does not have them."""
        event.stop()
        world = self._world_text()
        if not world:
            self.notify(self._t("fandom_none"), severity="warning")
            return
        current = self._scenario_text().strip()
        try:
            instruction = self.query_one("#drama-prompt", TextArea).text.strip()
        except Exception:
            instruction = ""
        lang = self.app.store.global_cfg.ui.lang
        self._start_thinking("drama-prompt")
        self.run_worker(
            lambda: self._brief_worker(world, current, instruction, lang),
            thread=True, exclusive=False,
        )

    def _brief_worker(self, world: str, current: str, instruction: str, lang: str) -> None:
        try:
            brief = lore_ai.write_brief(self._llm(), world, current, instruction, lang)
        except Exception as e:
            self.app.call_from_thread(self._brief_done, "", str(e))
            return
        self.app.call_from_thread(self._brief_done, brief, "")

    def _brief_done(self, brief: str, err: str) -> None:
        self._stop_thinking("drama-prompt")
        if err or not brief:
            self.notify(err or self._t("fandom_brief_none"), severity="error", timeout=8)
            return
        try:
            area = self.query_one("#drama-scenario", TextArea)
            area.text = brief
            area.add_class("ai-filled")
        except Exception:
            pass
        self.notify(self._t("fandom_brief_written"), timeout=4)

    # -- gathering / launch --------------------------------------------------

    def _selected_cast(self) -> list[str]:
        """The world's people, for the summary. There is no run cast to select from."""
        cfg = self.app.store.fandoms.get(self._fandom)
        return [c.name for c in cfg.cast] if cfg else []

    def _gather(self) -> dict:
        g = super()._gather()
        try:
            f = self.f_fandom.read(self)
        except Exception:  # the World step never mounted
            f = {}
        try:
            v = self.f_voice.read(self)
        except Exception:
            v = {}
        g["fandom"] = f.get("fandom") or self._fandom
        g["fandom_voice"] = v.get("voice") or "resident"
        # the drama's stage editor never mounts here, so the chain the pipeline runs on
        # is built from the single source this mode asks about instead
        self._stages = [self._source_stage().model_dump()]
        return g

    # -- step 1: how long ----------------------------------------------------

    def _timing_fields(self) -> list:
        """Seconds, and nothing else. Minutes-with-a-tolerance is a drama's unit: a
        story runs as long as it runs and the writer needs room to land it. A piece
        about a world is cut to a length. And there is no average-clip field because
        in this mode the writer sizes every shot itself, whatever the shots are made
        of — see stages/fandom_script.SHOT_RULE."""
        return [Number("duration_s", "fandom_duration_s", value="120", default=120.0)]

    def _timing(self, c: dict) -> dict:
        return {
            "duration": max(float(c.get("duration_s") or 120.0), 5.0),
            "duration_tol": 0.0,   # the length is the length
            "clip_s": 0.0,         # the writer's to choose, per shot
        }

    # -- step 4: where the footage comes from --------------------------------
    #
    # The drama's step here is an ORCHESTRATION: an ordered chain of generators, each
    # taking a share of the video, with API-key rotation per stage. That exists because
    # a feature-length drama burns through free daily limits and has to hop between
    # services mid-run. A fandom video is one piece of a few minutes; a chain of
    # generators with key-rotation policies is machinery it does not need and framing
    # that says nothing about what it is making. So it asks the one question that
    # matters — where do the shots come from — and builds the chain itself.

    def _source_stage(self) -> OrchestrationStage:
        """The single source, as the one-stage chain the pipeline runs on. Everything
        downstream (beats.plan_slots, drama_footage) speaks chains, so this is the only
        place the fandom's simpler question is translated."""
        try:
            v = self.f_source.read(self)
        except Exception:  # the step never mounted
            v = {}
        photo = v.get("medium") == "photo"
        model = (v.get("psrc") or "flux") if photo else (v.get("vsrc") or "wan2.1")
        return OrchestrationStage(model=model, key_mode="rotate", key="",
                                  metric="percent", amount=100.0, clip_seconds=0.0)

    def _medium(self) -> str:
        try:
            return self.f_source.read(self).get("medium") or "video"
        except Exception:
            return "video"

    def _extra_params(self, g: dict) -> dict:
        return {"fandom": g["fandom"], "fandom_voice": g["fandom_voice"],
                "medium": self._medium()}

    def _summary_timing_lines(self, t, g: dict, clip: str) -> list[str]:
        medium = t(f"fandom_medium_{self._medium()}")
        return [f"  {t('lang')}: [b]{g['lang']}[/b]      {t('fandom_duration_s')}: "
                f"[b]{g['duration']:.0f}s[/b]      {t('fandom_medium')}: [b]{medium}[/b]"
                f"      {t('tts_rate').split(' (')[0]}: [b]{g['tts_rate']:+d}%[/b]"]

    def _summary_source_lines(self, t, stages: str) -> list[str]:
        label = dict(ORCH_MODEL_OPTS + PHOTO_SOURCES).get(stages, stages)
        return [f"  {t('fandom_source_head').strip('— ')}: [b]{label}[/b]"]

    def _summary_cast_lines(self, t, cast: str, glob: str) -> list[str]:
        return [f"  {t('fandom_cast_head')}: [b]{cast}[/b]"]

    def _summary_extra(self, g: dict) -> list[str]:
        t = self._t
        voice = t(f"fandom_voice_{g['fandom_voice']}")
        return [f"  {t('fandom_pick')}: [b]{g['fandom'] or '—'}[/b]"
                f"      {t('fandom_voice')}: {voice}"]

    def _timing_flags(self, g: dict) -> str:
        return f" --duration {g['duration']:.0f}"

    def _mode_flags(self, g: dict) -> str:
        # the world is the command's SECOND POSITIONAL argument, not a flag:
        # `slopgen fandom <lang> <fandom>` (see cli/app.py). _mode_flags is appended
        # straight after the language, which is exactly where it belongs.
        if not g["fandom"]:
            return ""
        name = g["fandom"]
        quoted = f'"{name}"' if " " in name else name
        st = self._source_stage()
        return (f" {quoted} --narrator {g['fandom_voice']}"
                f" --medium {self._medium()} --source {st.model}")


def _paint_mode_button(btn: Button, color: str) -> None:
    """Give one mode button its identity colour and the app's own bevel."""
    body, ink, lit, shade = identity_bevel(color)
    btn.styles.background = body
    btn.styles.color = ink
    # top and bottom only — see identity_bevel
    btn.styles.border_top = ("tall", lit)
    btn.styles.border_bottom = ("tall", shade)


class ModeSelectScreen(Screen):
    """Pick what to generate after pressing GENERATE: the minute-of-info clip, an AI
    drama, or a fandom — a video set inside a world the operator wrote down. Each
    opens its own settings wizard."""

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield TopBar(t("menu.generate"))
        with Center(id="home-center"):
            with Vertical(id="home-inner"):
                yield Static(t("mode_head"), id="logo-sub")
                # Three peers, so none of them is the special one. They were `success`,
                # `primary` and `warning`, which said nothing and lied twice over: the
                # first two are the SAME colour in this theme, so the modes read as
                # "two of these and one odd", and the odd one wore the theme's warning
                # gold. Identity colour says the true thing instead — three different,
                # none privileged — and each button keeps the theme's own bevel, which
                # is the part an inline border colour silently drops.
                colours = identity_colors(
                    ["info", "drama", "fandom"], theme_identity(self.app.current_theme)
                )
                with Vertical(id="home-menu"):
                    for mode in ("info", "drama", "fandom"):
                        btn = Button(t(f"mode_{mode}"), id=f"mode-{mode}",
                                     classes="mode-btn")
                        _paint_mode_button(btn, colours[mode])
                        yield btn
                        yield Static(t(f"mode_{mode}_desc"), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#mode-info", Button).focus()

    def _repaint(self) -> None:
        """Re-colour from the current theme. The palette is the theme's (see
        `theme_identity`), so switching themes has to actually move these — otherwise
        the buttons are the one thing on screen that ignores the theme, which is worse
        than not colouring them at all."""
        colours = identity_colors(
            ["info", "drama", "fandom"], theme_identity(self.app.current_theme)
        )
        for mode, colour in colours.items():
            try:
                _paint_mode_button(self.query_one(f"#mode-{mode}", Button), colour)
            except Exception:
                pass

    def watch_theme(self, *_) -> None:  # noqa: D401 - Textual reactive hook name
        self._repaint()

    @on(events.Mount)
    def _follow_theme(self) -> None:
        self.watch(self.app, "theme", lambda *_: self._repaint(), init=False)

    @on(Button.Pressed, "#mode-info")
    def _info(self) -> None:
        self.app.push_screen(GenerateScreen())

    @on(Button.Pressed, "#mode-drama")
    def _drama(self) -> None:
        self.app.push_screen(DramaScreen())

    @on(Button.Pressed, "#mode-fandom")
    def _fandom(self) -> None:
        self.app.push_screen(FandomScreen())


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class ProgressScreen(Screen):
    def __init__(self, params: RunParams, resume_dir: Path | None = None):
        super().__init__()
        self.params = params
        self.resume_dir = resume_dir
        self.run_dir: Path | None = resume_dir
        self._video: int | None = None  # video the progress line is currently about

    def compose(self) -> ComposeResult:
        yield TopBar(_label(self.app, "step.summary"))
        yield Static("", id="run-summary")
        with Horizontal(id="run-progress"):
            yield Static("", id="run-stage")
            yield ProgressBar(total=100, show_eta=False, id="run-bar")
            yield Static("", id="run-count")
        yield DataTable(id="queue")
        yield RichLog(id="log", wrap=True, highlight=True)

    def on_mount(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        p = self.params
        ad = f"{(p.manual_ad.name if p.manual_ad else p.ad) or '—'} ({p.ad_mode})"
        push = f"push: {p.push or t('run.local')} · {t('run.subs')}: {p.subtitle_style}"
        if p.mode in ("drama", "fandom"):
            # a fandom is the drama pipeline set in a named world — say which one
            parts = f" · {t('parts')}: {p.parts}" if p.parts != 1 else ""
            icon = "🌍" if p.mode == "fandom" else "🎭"
            world = f" · {p.fandom}" if p.mode == "fandom" and p.fandom else ""
            head = (f" {p.count}× {icon} {p.lang}{world} · "
                    f"~{p.duration_s / 60:.1f} min ±{p.duration_tol_s:.0f}s{parts} · ad: ")
        else:
            vis = (p.manual_visuals.name + "*") if p.manual_visuals else p.visuals
            head = f" {p.count}× {p.lang}/{p.content_type or 'auto'} · {t('run.vis')}: {vis} ~{p.duration_s:.0f}s · ad: "
        self.query_one("#run-summary", Static).update(f"{head}{ad} · {push}")
        table = self.query_one("#queue", DataTable)
        # explicit widths: the info cell carries the running tally ("18/18"), which
        # the auto-width (set from the header alone) truncated to "18/1"
        for name, width in ((t("col.video"), 7), (t("col.stage"), 12),
                            (t("col.status"), 7), (t("col.info"), 60)):
            table.add_column(name, width=width)
        for i in range(p.count):
            table.add_row(f"#{i}", t("row.queued"), "…", "")
        self.run_worker(self._run_pipeline, thread=True, exclusive=True)

    def _run_pipeline(self) -> None:
        try:
            ctx = AppContext(store=self.app.store, params=self.params,
                             on_progress=self._on_progress_threadsafe)
        except Exception as e:
            self.app.call_from_thread(self._log, f"[red]{_label(self.app, 'err.startup')}: {e}")
            return
        orch = Orchestrator(ctx, on_event=self._on_event_threadsafe)
        jobs = orch.run(resume_dir=self.resume_dir)
        self.run_dir = orch.run_dir
        # a drama parked between episodes has published something and is still not done
        done = [j for j in jobs if j.published and not j.pending_parts]
        self.app.call_from_thread(self._finish, done, len(jobs))

    def _on_event_threadsafe(self, i: int, stage: str, status: str, message: str) -> None:
        self.app.call_from_thread(self._on_event, i, stage, status, message)

    # -- progress ----------------------------------------------------------

    def _on_progress_threadsafe(self, unit: str, done: int, total: int) -> None:
        self.app.call_from_thread(self._on_progress, unit, done, total)

    def _on_progress(self, unit: str, done: int, total: int) -> None:
        """A stage reporting from inside its own loop (voiced lines, generated
        clips, assembled scenes) — the only signal that a long stage is moving."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        self.query_one("#run-bar", ProgressBar).update(total=max(total, 1), progress=done)
        self.query_one("#run-count", Static).update(f"{done}/{total} {t(f'unit.{unit}')}")
        # the queue row carries it too, so a finished video keeps the final tally
        if self._video is not None:
            table = self.query_one("#queue", DataTable)
            table.update_cell_at(Coordinate(self._video, 3), f"{done}/{total}")

    def _set_stage(self, i: int, stage: str) -> None:
        """Open a fresh progress line for a stage that just started."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        self._video = i
        self.query_one("#run-stage", Static).update(
            f" {t('col.video')} {i} · [b]{t(f'bp.stage.{stage}').split(' (')[0]}[/b]"
        )
        self.query_one("#run-bar", ProgressBar).update(total=100, progress=0)
        self.query_one("#run-count", Static).update("")

    def _on_event(self, i: int, stage: str, status: str, message: str) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        table = self.query_one("#queue", DataTable)
        icons = {"start": "⏳", "done": "✔", "error": "✘", "skip": "↷", "paused": "⏸", "review": "⏸"}
        table.update_cell_at(Coordinate(i, 1), stage)
        table.update_cell_at(Coordinate(i, 2), icons.get(status, "·"))
        if status != "done" or message:  # keep an item tally the stage just reported
            table.update_cell_at(Coordinate(i, 3), message[:60])
        if status == "start":
            self._set_stage(i, stage)
        elif status in ("done", "skip"):
            self.query_one("#run-bar", ProgressBar).update(total=100, progress=100)
        if status == "error":
            self._log(f"[red]{t('col.video')} {i} — {stage}:[/red]\n{message}")
        elif status == "paused":
            self._log(f"[yellow]{t('col.video')} {i} · {t('gather.paused')}[/yellow] — {message}")
        elif status == "review":
            self._log(f"[yellow]{t('col.video')} {i} · {stage} · {t('bp.paused')}[/yellow]")
        elif status == "done" and message:
            self._log(f"{t('col.video')} {i} · {stage} ✔ {message}")

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def _finish(self, done: list, total: int) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        # a run parked on a breakpoint -> straight to the review screen
        if self.run_dir and _review_jobs(self.run_dir):
            self._log(f"[yellow]{t('bp.needed')}[/yellow]")
            self.notify(t("bp.needed"), timeout=8)
            self.app.push_screen(BreakpointScreen(self.run_dir))
            return
        # a run that parked jobs for manual clips -> jump straight to the gather screen
        if self.run_dir and _paused_jobs(self.run_dir):
            self._log(f"[yellow]{t('gather.needed')}[/yellow]")
            self.notify(t("gather.needed"), timeout=8)
            self.app.push_screen(ManualGatherScreen(self.run_dir))
            return
        self._log(f"[bold green]{t('run.finished')}: {len(done)}/{total}[/bold green]")
        for j in done:
            for line in str(j.published).splitlines():
                self._log(f"  → {line}")
        self.notify(f"{t('run.finished')}: {len(done)}/{total}", timeout=10)


# --------------------------------------------------------------------------
# User-assisted ("manual") clip gathering
# --------------------------------------------------------------------------

_STATUS_BADGE = {"pending": "☐ pending", "in_flight": "⏳ sent", "delivered": "✔ done"}


def _jobs_with_status(run_dir: Path, status: str) -> list[int]:
    """Job indices of a run currently in `status`, in order."""
    try:
        cp = Checkpoint.load(run_dir)
    except (FileNotFoundError, Exception):
        return []
    return [i for i in range(cp.params.count) if cp.status(i) == status]


def _paused_jobs(run_dir: Path) -> list[int]:
    """Job indices parked awaiting manual clips."""
    return _jobs_with_status(run_dir, "paused")


def _review_jobs(run_dir: Path) -> list[int]:
    """Job indices parked on a breakpoint, awaiting operator review."""
    return _jobs_with_status(run_dir, "review")


class ManualGatherScreen(Screen):
    """Fill hand-made clips for a paused run: browse the shotlist, read each
    prompt, drop clips into the inbox or attach by path, then resume."""

    BINDINGS = [
        ("a", "attach", "Attach clip"),
        ("i", "inflight", "Mark sent"),
        ("r", "rescan", "Rescan inbox"),
        ("f", "finish", "Finish & resume"),
    ]

    def __init__(self, run_dir: Path):
        super().__init__()
        self.run_dir = Path(run_dir)
        self.manifests: dict[int, manual.ManualManifest] = {}
        self.rows: list[tuple[int, str]] = []  # (job_index, shot_id) parallel to the table
        self.show_parts = False  # the part column earns its width only on a real serial
        self.iterative = True  # whether one finished episode is enough to resume on

    # -- data --------------------------------------------------------------

    def _workdir(self, job_index: int) -> Path:
        return self.run_dir / f"{job_index:02d}"

    def _load(self) -> None:
        self.manifests = {}
        for i in _paused_jobs(self.run_dir):
            self.manifests[i] = manual.ManualManifest.load(self._workdir(i))
        try:  # the run decided at launch whether an episode may go out on its own
            self.iterative = parts.iterative(Checkpoint.load(self.run_dir).params)
        except Exception:
            self.iterative = True

    def _shot(self, job_index: int, shot_id: str) -> manual.ManualShot | None:
        m = self.manifests.get(job_index)
        return next((s for s in m.shots if s.id == shot_id), None) if m else None

    def _totals(self) -> tuple[int, int]:
        shots = [s for m in self.manifests.values() for s in m.shots]
        return sum(1 for s in shots if s.status == "delivered"), len(shots)

    def _multi_part(self) -> bool:
        return any(s.part != 1 for m in self.manifests.values() for s in m.shots)

    def _ready_parts(self) -> list[tuple[int, int]]:
        """(job index, part) for every episode whose clips are all in — what pressing
        Continue would actually cut. Empty when the run was told to cut all parts at
        the end: a finished episode buys nothing there, so offering to resume on one
        would only park the job straight back."""
        if not self.iterative:
            return []
        return [(i, n) for i, m in self.manifests.items() for n in m.parts_ready()]

    def _part_status(self) -> str:
        """One line per episode: ready to cut, or how many clips it is still short."""
        bits: list[str] = []
        t = lambda k: _label(self.app, k)  # noqa: E731
        for m in self.manifests.values():
            pending = m.pending_parts()
            for n in sorted({s.part for s in m.shots}):
                bits.append(
                    t("gather.part_left").format(n=n, k=m.shots_left(n)) if n in pending
                    else t("gather.part_ready").format(n=n)
                )
        return "  ·  ".join(bits)

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TopBar(_label(self.app, "gather.title"))
        with Horizontal(id="gather-body"):
            yield DataTable(id="shots")
            yield Static("", id="shot-detail")
        yield Static("", id="gather-progress")
        with Horizontal(id="gather-row"):
            yield Button(_label(self.app, "gather.attach"), id="g-attach", variant="success")
            yield Button(_label(self.app, "gather.inflight"), id="g-inflight")
            yield Button(_label(self.app, "gather.rescan"), id="g-rescan", variant="primary")
            yield Button(_label(self.app, "gather.finish"), id="g-finish", variant="warning")

    def on_mount(self) -> None:
        table = self.query_one("#shots", DataTable)
        table.cursor_type = "row"
        self._load()  # the part column only exists when the drama actually has parts
        self.show_parts = self._multi_part()
        cols = [_label(self.app, "gather.col.shot")]
        if self.show_parts:
            cols.append(_label(self.app, "gather.col.part"))
        cols += [
            _label(self.app, "gather.col.status"), _label(self.app, "gather.col.target"),
            _label(self.app, "gather.col.prompt"),
        ]
        table.add_columns(*cols)
        self.action_rescan()  # loads, scans the inbox once, and paints

    def on_screen_resume(self) -> None:
        """Coming back from a run: clips may have been delivered while it went."""
        if self.is_mounted:
            self.action_rescan()

    # -- rendering ---------------------------------------------------------

    def _refresh(self) -> None:
        table = self.query_one("#shots", DataTable)
        prev = table.cursor_row
        table.clear()
        self.rows = []
        for job_index, m in self.manifests.items():
            for s in m.shots:
                self.rows.append((job_index, s.id))
                prompt = (s.prompt[:48] + "…") if len(s.prompt) > 49 else s.prompt
                if s.kind == "search":
                    mark = _label(self.app, f"gather.want.{s.want}") if s.want else "?"
                    prompt = f"[{mark}] {prompt}"
                cells = [s.id] + ([str(s.part)] if self.show_parts else [])
                cells += [_STATUS_BADGE.get(s.status, s.status), f"{s.target_s:.0f}s", prompt]
                table.add_row(*cells)
        done, total = self._totals()
        t = lambda k: _label(self.app, k)  # noqa: E731
        line = (
            f"[bold]{done}/{total}[/bold] {t('gather.delivered')} · {t('gather.inbox')}: "
            f"{manual.inbox_dir(self._workdir(self.rows[0][0])) if self.rows else self.run_dir}"
        )
        if self.show_parts:
            # a drama is cut part by part, so what matters is not the grand total but
            # whether any ONE episode is complete — that is what Continue would render
            line += f"\n{self._part_status()}"
            line += f"\n[dim]{t('gather.will_cut' if self.iterative else 'gather.wait_all')}[/dim]"
        self.query_one("#gather-progress", Static).update(line)
        if self.rows:
            table.move_cursor(row=min(prev, len(self.rows) - 1))
            self._show_detail()
        else:
            self.query_one("#shot-detail", Static).update(t("gather.none"))

    def _selected(self) -> manual.ManualShot | None:
        table = self.query_one("#shots", DataTable)
        if not self.rows or table.cursor_row is None or table.cursor_row >= len(self.rows):
            return None
        return self._shot(*self.rows[table.cursor_row])

    def _show_detail(self) -> None:
        shot = self._selected()
        if shot is None:
            return
        t = lambda k: _label(self.app, k)  # noqa: E731
        clip = f"\n\n[dim]{t('gather.clip')}: {shot.clip}[/dim]" if shot.clip else ""
        part = f"  ·  {t('gather.col.part')} {shot.part}" if self.show_parts else ""
        head = (
            f"[b]{shot.id}[/b]{part}  ·  {_STATUS_BADGE.get(shot.status, shot.status)}  ·  "
            f"~{shot.target_s:.0f}s  ·  {shot.width}×{shot.height}"
        )
        if shot.kind == "search":
            # A search task is not a prompt to paste; it is an errand. The brief says
            # what to come back with, and the queries are the words to type — kept on
            # their own lines because they are copied one at a time until one hits.
            want = t(f"gather.want.{shot.want}") if shot.want else ""
            head += f"  ·  [b]{t('gather.kind.search')}[/b]" + (f" ({want})" if want else "")
            body = f"{shot.prompt}\n\n[b]{t('gather.queries')}[/b]\n" + "\n".join(
                f"  {q}" for q in shot.queries
            ) if shot.queries else shot.prompt
            hint = t("gather.drop_hint_search").format(id=shot.id)
        else:
            body = shot.prompt
            hint = f"{t('gather.drop_hint')} {shot.id}.mp4"
        self.query_one("#shot-detail", Static).update(
            f"{head}\n\n{body}{clip}\n\n[dim]{hint}[/dim]"
        )

    @on(DataTable.RowHighlighted, "#shots")
    def _row_changed(self) -> None:
        self._show_detail()

    # -- actions -----------------------------------------------------------

    @on(Button.Pressed, "#g-rescan")
    def action_rescan(self) -> None:
        self._load()
        for i, m in self.manifests.items():
            if manual.scan_inbox(m, self._workdir(i)):
                m.save(self._workdir(i))
        self._refresh()

    @on(Button.Pressed, "#g-inflight")
    def action_inflight(self) -> None:
        shot = self._selected()
        if shot is None or shot.status == "delivered":
            return
        job_index = self.rows[self.query_one("#shots", DataTable).cursor_row][0]
        shot.status = "in_flight"
        self.manifests[job_index].save(self._workdir(job_index))
        self._refresh()

    @on(Button.Pressed, "#g-attach")
    def action_attach(self) -> None:
        if self._selected() is None:
            return

        def _got(path: str | None) -> None:
            if not path:
                return
            p = Path(path).expanduser()
            shot = self._selected()
            if shot is None:
                return
            if not manual._valid_asset(p):
                self.notify(_label(self.app, "gather.bad_clip"), severity="error", timeout=6)
                return
            job_index = self.rows[self.query_one("#shots", DataTable).cursor_row][0]
            manual.attach(shot, p)
            self.manifests[job_index].save(self._workdir(job_index))
            self._refresh()

        self.app.push_screen(NameModal(_label(self.app, "gather.attach_prompt")), _got)

    @on(Button.Pressed, "#g-finish")
    def action_finish(self) -> None:
        """Resume the run. It does not need every clip — only one complete episode,
        which it cuts and publishes while the rest stays parked here."""
        done, total = self._totals()
        if total == 0 or (done < total and not self._ready_parts()):
            self.notify(_label(self.app, "gather.incomplete"), severity="warning", timeout=6)
            return
        try:
            params = Checkpoint.load(self.run_dir).params
        except Exception as e:
            self.notify(f"{e}", severity="error", timeout=8)
            return
        self.app.push_screen(ProgressScreen(params, resume_dir=self.run_dir))


# --------------------------------------------------------------------------
# Breakpoints: inspect (and edit) what a stage produced, then let the run go on
# --------------------------------------------------------------------------


class BreakpointScreen(Screen):
    """One parked video at a time (see pipeline/review.py), as master-detail: the
    stage's items are cards on the left — reorder, add and drop them there — and the
    open card's fields are edited on the right. A scene has more to it than text
    (who is in it, which generator, how long), which is unreadable as one flat list.

    The rows are the screen's state; only the open card's fields exist as widgets, so
    values are synced back out of them before every rebuild and before saving."""

    BINDINGS = [("ctrl+s", "continue_run", "Continue")]

    def __init__(self, run_dir: Path):
        super().__init__()
        self.run_dir = Path(run_dir)
        self.queue: list[int] = _review_jobs(run_dir)
        self.cp = Checkpoint.load(run_dir)
        self.job: VideoJob | None = None
        self.doc = review.Doc(stage="")
        self._rev = 0  # bumps per rebuild so field ids stay unique across async removal
        self._list_rev = 0  # same, for card ids
        self._sel: int | None = None  # open card
        self._quiet = False  # suppress selection handling while we repaint the list
        # an in-place edit or re-voicing already applied to the job; Continue must
        # still mark the stage for a re-run, which the final apply can no longer tell
        self._forced_rerun = False
        self._ai_anchors: list[int | None] = []  # part markers held across an AI restructure
        # Speed of the next take at the voiceover breakpoint. ONE slider for the whole
        # screen, not a field of every card: it is a knob on the ACTION, applying to
        # whichever fragment is re-voiced next — which then keeps that speed while the
        # rest of the video stays at the run's. Starts where the run stands.
        self._rate = int(self.cp.params.tts_rate)

    # -- data --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.cp.params.mode

    def _load(self) -> None:
        """Read the first still-parked job and the document of its breakpoint stage."""
        self.queue = _review_jobs(self.run_dir)
        if not self.queue:
            self.job = None
            return
        i = self.queue[0]
        self.job = self.cp.load_job(i)
        stage = self.cp.review_stage(i)
        self.doc = review.read(stage, self.job, self.mode) if self.job else review.Doc(stage=stage)

    def _is_sep(self, group: review.Group) -> bool:
        return group.head.field == review.PART_FIELD

    def _item_label(self, head: review.Row) -> str:
        if head.field == review.PART_FIELD:  # a part marker, not an item of its own
            return f"{_label(self.app, 'bp.f.part')} {head.value}"
        if head.label.startswith("bp."):  # a named field (title, topic, …)
            return _label(self.app, head.label)
        # "#3" / "#3 · AD" — a scene; say so, the number alone reads as nothing
        return f"{_label(self.app, 'bp.scene')} {head.label}" if head.label.startswith("#") else head.label

    def _sync(self) -> None:
        """Pull what is on screen back into the rows. Only the open card's fields are
        mounted, so the rest simply keep the value they already hold."""
        self._sync_rate()
        for i, row in enumerate(self.doc.rows):
            if row.readonly or row.kind == "chips":  # chips are edited on the row itself
                continue
            wid = f"#bp-val-{self._rev}-{i}"
            try:
                if row.kind == "number":
                    row.value = self.query_one(wid, Input).value.strip()
                elif row.kind == "choice":
                    value = self.query_one(wid, Select).value
                    row.value = "" if value is Select.BLANK else str(value)
                else:
                    row.value = self.query_one(wid, TextArea).text
            except Exception:  # not mounted (another card is open) — keep it as is
                pass

    def _groups(self) -> list[review.Group]:
        return review.group_rows(self.doc.rows)

    def _first_index(self, gi: int) -> int:
        """Flat row index the given card starts at (row ids stay positional)."""
        return sum(len(g.rows) for g in self._groups()[:gi])

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield TopBar(t("bp.title"))
        yield Static("", id="bp-head")
        with Horizontal(id="bp-body"):
            with Vertical(id="bp-list-pane"):
                # two rows on purpose: the pane is a fixed 48 columns and five
                # labelled buttons do not fit across it — what is edited above, how
                # it is arranged below
                with Horizontal(id="bp-row-item", classes="entity-actions"):
                    yield Button(t("bp.add"), id="bp-add", variant="success")
                    yield Button(t("bp.remove"), id="bp-del", variant="error")
                with Horizontal(id="bp-row-order", classes="entity-actions"):
                    yield Button(t("bp.up"), id="bp-up")
                    yield Button(t("bp.down"), id="bp-down")
                    yield Button(t("bp.cut"), id="bp-cut", variant="success")
                yield ListView(id="bp-list")
            yield VerticalScroll(id="bp-detail")
        with Horizontal(id="bp-ai-box"):
            yield FieldTextArea(id="bp-ai-prompt", placeholder=t("bp.ai_ph"),
                                single_line=True, classes="text-field text-field-short")  # placeholder set per stage
            yield Button(t("bp.ai"), id="bp-ai-go", variant="primary")
        yield Static("", id="bp-note")
        with Horizontal(id="bp-actions"):
            yield Button(t("bp.discard"), id="bp-discard")
            yield Button(t("bp.continue"), id="bp-continue", variant="primary")

    def on_mount(self) -> None:
        self._load()
        self.run_worker(self._rebuild())

    # -- the card list (left) ----------------------------------------------

    def _card_summary(self, group: review.Group) -> str:
        """The one-line "what else is set here" under a card's opening words."""
        bits = []
        for row in group.extras:
            value = " ".join(row.value.split())
            if not value:
                continue
            name = _label(self.app, f"bp.field.{row.field}")
            bits.append(f"{name}: {value[:28]}" + ("…" if len(value) > 28 else ""))
        return "  ·  ".join(bits)

    async def _refresh_list(self, keep: int | None = None) -> None:
        """Repaint the cards. `keep` is the card to leave selected. The mounts are
        awaited before the selection is set: assigning an index the list has not
        built yet is silently clamped to 0, which then snaps the field pane back to
        the first card."""
        lv = self.query_one("#bp-list", ListView)
        self._list_rev += 1
        self._quiet = True  # our own clear/append must not fire selection handling
        await lv.clear()
        groups = self._groups()
        items = []
        for gi, group in enumerate(groups):
            if self._is_sep(group):
                head = _label(self.app, "bp.sep").format(n=group.head.value)
            else:
                head = " ".join(group.head.value.split())
            items.append(ListItem(
                Vertical(
                    Static(f"[b]{self._item_label(group.head)}[/b]  [dim]{group.head.info}[/dim]",
                           classes="cast-name"),
                    Static(head[:70] + ("…" if len(head) > 70 else "") or "—", classes="cast-line"),
                    Static(self._card_summary(group), classes="cast-line cast-dim"),
                    classes="cast-info",
                ),
                id=f"bpitem-{self._list_rev}-{gi}", classes="cast-item",
            ))
        if items:
            await lv.extend(items)
        self._quiet = False
        if groups:
            self._sel = min(keep if keep is not None else (self._sel or 0), len(groups) - 1)
            lv.index = self._sel
        else:
            self._sel = None

    # -- the field pane (right) --------------------------------------------

    def _field_widgets(self, index: int, row: review.Row) -> list:
        """One field, rendered the way its kind wants to be edited."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        ns = f"bp-val-{self._rev}"  # Field.wid() then yields our positional id
        # a row that names ITSELF (the canon sheet, the topic, a metadata field) is
        # its own caption; only the rows of a repeated item — a scene's shot, cast,
        # generator — are captioned by which field of that item they are
        key = str(index)
        label = row.label if row.label.startswith("bp.f.") else f"bp.field.{row.field}"
        if row.kind == "number":
            return Number(key, label, value=row.value, default=0.0).build(ns, t)
        if row.kind == "choice":
            return Choice(key, label, options=[(o, o) for o in row.options],
                          value=row.value or None).build(ns, t)
        if row.kind == "chips":
            return self._chip_widgets(index, row)
        large = row.value.count("\n") > 1 or len(row.value) > 300
        return [
            Static(t(label), classes="bp-field-label"),
            FieldTextArea(
                text=row.value, id=f"{ns}-{key}", read_only=row.readonly, single_line=not large,
                classes="text-field " + ("text-field-large" if large else "text-field-short"),
            ),
        ]

    def _chip_widgets(self, index: int, row: review.Row) -> list:
        """A set-valued field: one button per member (press to drop it) plus ＋."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        names = [n.strip() for n in row.value.split(",") if n.strip()]
        chips: list = [
            Button(f"{name} ✖", id=f"bp-chip-{self._rev}-{index}-{k}", classes="bp-chip")
            for k, name in enumerate(names)
        ]
        chips.append(Button("＋", id=f"bp-chipadd-{self._rev}-{index}", classes="bp-chip-add"))
        return [
            Static(t(f"bp.field.{row.field}"), classes="bp-field-label"),
            Horizontal(*chips, classes="bp-chips"),
        ]

    async def _show_detail(self) -> None:
        pane = self.query_one("#bp-detail", VerticalScroll)
        await pane.remove_children()
        groups = self._groups()
        if self._sel is None or self._sel >= len(groups):
            return
        group = groups[self._sel]
        widgets: list = [Static(f"[b]{self._item_label(group.head)}[/b]", classes="group-head")]
        if self._is_sep(group):
            # a marker has nothing to type into: where it SITS is its whole content
            widgets.append(Static(
                _label(self.app, "bp.sep_hint").format(n=group.head.value),
                classes="bp-field-label",
            ))
            await pane.mount(*widgets)
            return
        start = self._first_index(self._sel)
        for n, row in enumerate(group.rows):
            widgets.extend(self._field_widgets(start + n, row))
        widgets.extend(self._card_actions())
        await pane.mount(*widgets)

    def _card_actions(self) -> list:
        """Stage-specific buttons for the open card. At the voiceover breakpoint the
        operator can re-voice this one line and listen to it without leaving — at the
        speed the slider above the buttons is set to."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        if self.doc.stage != "tts" or not self.doc.variable:
            return []
        return self._rate_field().build(self._rate_ns, t) + [Horizontal(
            Button(t("bp.regen"), id="bp-regen", variant="primary"),
            Button(t("bp.play"), id="bp-play"),
            classes="entity-actions",
        )]

    # -- re-voicing one line -----------------------------------------------

    @property
    def _rate_ns(self) -> str:
        return f"bp-{self._rev}"  # same revision dance as the field pane's ids

    def _rate_field(self) -> Range:
        return Range("rate", "bp.rate", value=self._rate,
                     lo=-50, hi=50, step=5, labels=TTS_RATE_LABELS)

    def _sync_rate(self) -> None:
        """Remember where the speed slider stands. It is mounted with the open card, so
        it has to be read back before the pane is torn down — the value belongs to the
        screen, not to the card that happened to be showing it."""
        try:
            self._rate = int(self._rate_field().read(self, self._rate_ns))
        except Exception:  # not mounted (another stage's breakpoint) — keep it as is
            pass

    def _scene_index(self) -> int | None:
        """The open card's scene, once the pending edits are folded into the job so
        cards and scenes line up one to one."""
        if self.job is None or self._sel is None:
            return None
        self._sync()
        if review.apply(self.doc.stage, self.job, self.doc.rows, self.mode):
            self._forced_rerun = True  # the edit outdated the stage; remember for Continue
        groups = self._groups()
        if self._sel >= len(groups) or self._is_sep(groups[self._sel]):
            return None
        # cards and scenes only line up one to one once the part markers are discounted
        index = sum(1 for g in groups[:self._sel] if not self._is_sep(g))
        return index if index < len(self.job.scenes) else None

    @on(Button.Pressed, "#bp-regen")
    def _regen(self) -> None:
        index = self._scene_index()  # syncs the pane, so the slider is read by now
        if index is None:
            return
        self.notify(_label(self.app, "bp.regen_working"), timeout=4)
        rate = self._rate
        self.run_worker(lambda: self._regen_worker(index, rate), thread=True, exclusive=False)

    def _regen_worker(self, index: int, rate: int) -> None:
        try:
            ctx = AppContext(store=self.app.store, params=self.cp.params)
            secs = tts_stage.resynth_one(self.job, ctx, index, rate=rate)
        except Exception as e:
            self.app.call_from_thread(self._regen_done, index, None, str(e))
            return
        self.app.call_from_thread(self._regen_done, index, secs, None)

    def _regen_done(self, index: int, secs: float | None, err: str | None) -> None:
        if err is not None:
            self.notify(f"{_label(self.app, 'bp.regen_err')}: {err}", severity="error", timeout=10)
            return
        # a fresh take means the stage must lay the timeline out again on resume;
        # it costs nothing — the sidecar cache we just wrote is what it will read
        self._forced_rerun = True
        self.doc = review.read(self.doc.stage, self.job, self.mode)  # picks up the new length
        self.notify(_label(self.app, "bp.regen_done").format(s=secs or 0.0, r=self._rate),
                    timeout=6)
        self.run_worker(self._rebuild(keep=index))

    @on(Button.Pressed, "#bp-play")
    def _play(self) -> None:
        index = self._scene_index()
        if index is None:
            return
        audio = self.job.scenes[index].audio
        if not audio or not Path(audio).exists():
            self.notify(_label(self.app, "bp.play_none"), severity="warning", timeout=5)
            return
        try:
            subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, FileNotFoundError):
            self.notify(_label(self.app, "bp.play_err"), severity="error", timeout=8)

    async def _rebuild(self, keep: int | None = None) -> None:
        """Repaint everything: header, cards, the open card's fields, notes."""
        t = lambda k: _label(self.app, k)  # noqa: E731
        self._rev += 1
        if self.job is None:  # nothing parked — the run is free to continue
            self.query_one("#bp-head", Static).update(t("bp.none"))
            self.query_one("#bp-note", Static).update("")
            self.query_one("#bp-body", Horizontal).display = False
            self.query_one("#bp-ai-box", Vertical).display = False
            return
        await self._refresh_list(keep)
        await self._show_detail()
        head = t("bp.head").format(
            i=self.queue[0], stage=t(f"bp.stage.{self.doc.stage}").split(" (")[0],
            n=len(self._groups()),
        )
        if len(self.queue) > 1:
            head += f"  [dim]{t('bp.left').format(n=len(self.queue) - 1)}[/dim]"
        self.query_one("#bp-head", Static).update(head)
        note = t(self.doc.note_key) if self.doc.note_key else ""
        if self.doc.note_extra:
            note += f"\n{t('bp.cast_known')}: {self.doc.note_extra}"
        if not self.doc.editable and not self.doc.cuttable:
            note = f"{note}\n{t('bp.readonly')}" if note else t("bp.readonly")
        self.query_one("#bp-note", Static).update(note)
        ai_box = self.query_one("#bp-ai-box", Horizontal)
        ai_box.display = bool(self.doc.subject and self.doc.editable)
        self.query_one("#bp-ai-prompt", FieldTextArea).placeholder = t(
            "bp.ai_ph_script" if self.doc.stage == "script" else "bp.ai_ph"
        )
        # a cuttable document has movable part markers even when its items are fixed
        actionable = self.doc.variable or self.doc.cuttable
        self.query_one("#bp-add", Button).display = self.doc.variable
        self.query_one("#bp-cut", Button).display = self.doc.cuttable
        for wid in ("#bp-up", "#bp-down", "#bp-del"):
            self.query_one(wid, Button).display = actionable
        # hide the rows themselves as well: each is a fixed three lines tall, and an
        # inspect-only stage would otherwise show two empty bars above the list
        for wid in ("#bp-row-item", "#bp-row-order"):
            self.query_one(wid, Horizontal).display = actionable

    # -- editing -----------------------------------------------------------

    @on(ListView.Highlighted, "#bp-list")
    async def _card_changed(self, event: ListView.Highlighted) -> None:
        """Open the highlighted card. Arrow keys browse, so the panel follows."""
        if self._quiet or event.item is None or not event.item.id:
            return
        # clearing the list emits a highlight of its own, which arrives after we have
        # already selected a card — the id carries the list revision, so ignore any
        # message left over from the list we just replaced.
        _, rev, index = event.item.id.split("-")
        if int(rev) != self._list_rev:
            return
        gi = int(index)
        if gi == self._sel:
            return
        self._sync()  # the card being left keeps its edits
        self._sel = gi
        await self._show_detail()

    def _proto(self) -> review.Group | None:
        """The card a NEW item is shaped like: the first real item of the document.

        Never a part separator. A separator is not an item of the stage's own kind —
        it is one read-only marker row — so a new scene cut from it comes out as
        another part break instead of a scene, which is what the drama script
        document (it always opens with the marker of part 1) used to do to every
        scene the AI invented."""
        return next((g for g in self._groups() if not self._is_sep(g)), None)

    def _blank_group(self, label: str) -> list[review.Row]:
        """The rows one empty item is made of, shaped like the items already there.

        A card is more than its opening line — the shot prompt, the cast, the
        generator and the clip length are rows of the SAME scene — so an added scene
        has to carry them all, or it lands in the document as a line with nothing to
        say how it is filmed."""
        proto = self._proto()
        if proto is None:
            return [review.Row(label=label, value="")]
        return [
            review.Row(label=label, value="", field=r.field, kind=r.kind,
                       options=list(r.options), readonly=r.readonly)
            for r in proto.rows
        ]

    @on(Button.Pressed, "#bp-add")
    async def _add_item(self) -> None:
        self._sync()
        groups = self._groups()
        self.doc.rows.extend(self._blank_group(f"#{len(groups) + 1}"))
        await self._rebuild(keep=len(groups))

    @on(Button.Pressed, "#bp-cut")
    async def _add_cut(self) -> None:
        """Start a new part at the open card: everything from here down moves into it.

        The marker is inserted BEFORE the selected card because that is what the card
        list shows — a marker followed by the scenes it owns."""
        self._sync()
        groups = self._groups()
        if self._sel is None or self._sel >= len(groups):
            return
        if self._is_sep(groups[self._sel]):  # already a boundary here
            return
        groups.insert(self._sel, review.Group(head=review.part_row(0)))
        self.doc.rows = review.flatten(groups)
        self._renumber_seps()
        await self._rebuild(keep=self._sel)

    def _renumber_seps(self) -> None:
        """Number the markers by their order, so the cards read 1, 2, 3 after a move."""
        n = 0
        for row in self.doc.rows:
            if row.field == review.PART_FIELD:
                n += 1
                row.value = str(n)

    @on(Button.Pressed, "#bp-del")
    async def _del_item(self) -> None:
        """Remove the open card — its line, its shot, its cast, all of it. On a part
        marker it removes the boundary instead, merging that part into the one above."""
        self._sync()
        groups = self._groups()
        if self._sel is None or self._sel >= len(groups):
            return
        sep = self._is_sep(groups[self._sel])
        if sep and not self.doc.cuttable:  # markers are read-only on this breakpoint
            return
        if sep and sum(1 for g in groups if self._is_sep(g)) <= 1:
            self.notify(_label(self.app, "bp.cut_min"), severity="warning", timeout=5)
            return
        if not sep and not self.doc.variable:
            self.notify(_label(self.app, "bp.cut_locked"), severity="warning", timeout=5)
            return
        gone = self._sel
        del groups[gone]
        self.doc.rows = review.flatten(groups)
        self._renumber_seps()
        await self._rebuild(keep=max(0, gone - 1))

    @on(Button.Pressed, "#bp-up")
    async def _move_up(self) -> None:
        await self._move(-1)

    @on(Button.Pressed, "#bp-down")
    async def _move_down(self) -> None:
        await self._move(1)

    async def _move(self, delta: int) -> None:
        if self._sel is None:
            return
        self._sync()
        groups = self._groups()
        if self._sel >= len(groups):
            return
        sep = self._is_sep(groups[self._sel])
        if sep and not self.doc.cuttable:  # markers are read-only on this breakpoint
            return
        # on a cut-only document the scenes are fixed; the boundaries are the edit
        if not sep and not self.doc.variable:
            self.notify(_label(self.app, "bp.cut_locked"), severity="warning", timeout=5)
            return
        moved = review.move_group(self.doc.rows, self._sel, delta)
        if moved is self.doc.rows:  # already at the end
            return
        self.doc.rows = moved
        self._renumber_seps()
        await self._rebuild(keep=self._sel + delta)

    @on(Button.Pressed, ".bp-chip")
    async def _chip_remove(self, event: Button.Pressed) -> None:
        _, _, _rev, index, k = (event.button.id or "").split("-")
        row = self.doc.rows[int(index)]
        names = [n.strip() for n in row.value.split(",") if n.strip()]
        if 0 <= int(k) < len(names):
            del names[int(k)]
            row.value = ", ".join(names)
        await self._rebuild(keep=self._sel)

    @on(Button.Pressed, ".bp-chip-add")
    def _chip_add(self, event: Button.Pressed) -> None:
        index = int((event.button.id or "").rsplit("-", 1)[1])
        row = self.doc.rows[index]
        names = [n.strip() for n in row.value.split(",") if n.strip()]
        free = [o for o in row.options if o not in names]
        if not free:
            self.notify(_label(self.app, "bp.chip_none"), timeout=4)
            return

        def _picked(name: str | None) -> None:
            if not name:
                return
            row.value = ", ".join(names + [name])
            self.run_worker(self._rebuild(keep=self._sel))

        self.app.push_screen(PickModal(_label(self.app, "bp.chip_pick"), free), _picked)

    @on(Button.Pressed, "#bp-discard")
    async def _discard(self) -> None:
        self._forced_rerun = False
        self._load()
        await self._rebuild(keep=0)
        self.notify(_label(self.app, "bp.discard"), timeout=3)

    # -- AI edit line ------------------------------------------------------

    def _scene_payload(self) -> list[dict]:
        """The whole document as structured scenes for the AI (id + every field).

        Part markers are left out: they are structure, not content, and a model asked
        to rewrite them would happily invent some. They are put back afterwards by
        :meth:`_restore_seps`."""
        payload = []
        for group in self._groups():
            if self._is_sep(group):
                continue
            item: dict = {"id": group.head.src}
            for row in group.rows:
                item[row.field] = (
                    [v.strip() for v in row.value.split(",") if v.strip()]
                    if row.kind == "chips" else row.value
                )
            payload.append(item)
        return payload

    def _sep_anchors(self) -> list[int | None]:
        """For each part marker, the source scene it currently sits in front of.

        That scene is the marker's anchor: wherever the AI moves the scene to, the
        boundary follows it, which is the only reading of "this part starts here" that
        survives a restructure. A marker anchored to a scene the AI dropped is lost
        with it, and one that trailed the whole list (``None``) is dropped too — an
        episode with no scenes is not an episode."""
        anchors: list[int | None] = []
        groups = self._groups()
        for gi, group in enumerate(groups):
            if not self._is_sep(group):
                continue
            after = next((g.head.src for g in groups[gi + 1:] if not self._is_sep(g)), None)
            anchors.append(after)
        return anchors

    def _restore_seps(self, rows: list[review.Row], anchors: list[int | None]) -> list[review.Row]:
        """Put the markers back in front of the scenes they were anchored to."""
        wanted = [a for a in anchors if a is not None]
        if not wanted:
            return rows
        out: list[review.Row] = []
        placed: set[int] = set()
        for group in review.group_rows(rows):
            src = group.head.src
            if src in wanted and src not in placed:
                placed.add(src)
                out.append(review.part_row(0))
            out.extend(group.rows)
        if not any(r.field == review.PART_FIELD for r in out):  # nothing anchored survived
            out.insert(0, review.part_row(0))
        return out

    def _rows_from_scenes(self, items: list[dict]) -> list[review.Row]:
        """Rebuild the rows from what the AI returned. A scene carrying a known id
        keeps that source — and with it the audio and clip already made for it — while
        one with a null id becomes new. The field set, kinds and options are taken from
        the scene it came from, or from the first existing SCENE for a brand-new one
        (see :meth:`_proto` — taking the first card would take the part marker)."""
        groups = self._groups()
        by_src = {g.head.src: g for g in groups if g.head.src is not None}
        proto = self._proto()  # never the part marker groups[0] usually is
        rows: list[review.Row] = []
        for n, item in enumerate(items):
            sid = item.get("id")
            base = by_src.get(sid) if isinstance(sid, int) else None
            template = base or proto
            if template is None:
                continue
            for old in template.rows:
                raw = item.get(old.field, old.value if base else "")
                value = ", ".join(str(v) for v in raw) if isinstance(raw, list) else str(raw)
                rows.append(review.Row(
                    label=f"#{n + 1}", value=value,
                    src=base.head.src if base else None,
                    info=old.info if base else "",
                    field=old.field, kind=old.kind, options=list(old.options),
                    readonly=old.readonly,
                ))
        return rows

    @on(Button.Pressed, "#bp-ai-go")
    def _ai_edit(self) -> None:
        try:
            instruction = self.query_one("#bp-ai-prompt", TextArea).text.strip()
        except Exception:
            return
        if not instruction:
            self.notify(_label(self.app, "bp.ai_need"), severity="warning")
            return
        self._sync()
        if self.doc.stage == "script":  # structured: the AI may restructure the whole list
            self.notify(_label(self.app, "bp.ai_working"), timeout=3)
            self._ai_anchors = self._sep_anchors()
            payload = self._scene_payload()
            self.run_worker(
                lambda: self._ai_scenes_worker(payload, instruction), thread=True, exclusive=False
            )
            return
        # only free-text fields go to the model: a cast chip set, a generator choice or
        # a clip length are not prose and must not be "rewritten"
        editable = [r for r in self.doc.rows if not r.readonly and r.kind == "text"]
        lines = [r.value for r in editable]
        if not lines:
            return
        # a mixed document (narration + shot prompts) tells the model what each line is
        fields = [r.field for r in editable]
        kinds = fields if any(f != "text" for f in fields) else None
        self.notify(_label(self.app, "bp.ai_working"), timeout=3)
        self.run_worker(
            lambda: self._ai_worker(lines, instruction, kinds), thread=True, exclusive=False
        )

    def _ai_scenes_worker(self, payload: list[dict], instruction: str) -> None:
        try:
            out = bp_ai.rewrite_scenes(
                ChatLLM(self.app.store.active_llm_profile()), payload, instruction,
                lang=self.cp.params.lang,
                roster=list(self.job.cast_prompts) if self.job else [],
                models=review.generator_names(),
            )
        except Exception as e:
            self.app.call_from_thread(self._ai_scenes_done, None, str(e))
            return
        self.app.call_from_thread(self._ai_scenes_done, out, None)

    def _ai_scenes_done(self, items: list[dict] | None, err: str | None) -> None:
        if err is not None:
            self.notify(f"{_label(self.app, 'bp.ai_err')}: {err}", severity="error", timeout=10)
            return
        rows = self._rows_from_scenes(items or [])
        if not rows:
            self.notify(_label(self.app, "bp.ai_nothing"), timeout=5)
            return
        self.doc.rows = self._restore_seps(rows, getattr(self, "_ai_anchors", []))
        self._renumber_seps()
        self.notify(_label(self.app, "bp.ai_done"), timeout=6)
        self.run_worker(self._rebuild(keep=0))

    def _ai_worker(self, lines: list[str], instruction: str, kinds: list[str] | None) -> None:
        try:
            out = bp_ai.rewrite(
                ChatLLM(self.app.store.active_llm_profile()), lines, instruction,
                lang=self.cp.params.lang, subject=self.doc.subject,
                variable=self.doc.variable, kinds=kinds,
            )
        except Exception as e:
            self.app.call_from_thread(self._ai_done, None, str(e))
            return
        self.app.call_from_thread(self._ai_done, out, None)

    def _ai_done(self, lines: list[str] | None, err: str | None) -> None:
        if err is not None:
            self.notify(f"{_label(self.app, 'bp.ai_err')}: {err}", severity="error", timeout=10)
            return
        if not lines:
            self.notify(_label(self.app, "bp.ai_nothing"), timeout=5)
            return
        # rows keep their identity by position; anything past the original count is a
        # line the AI added, so it carries no source scene. Rows the model never saw
        # (read-only, and every non-text field) stay exactly where they are.
        editable = [r for r in self.doc.rows if not r.readonly and r.kind == "text"]
        for row, text in zip(editable, lines):
            row.value = text
        added = [
            review.Row(label=f"#{len(self.doc.rows) + i + 1}", value=text)
            for i, text in enumerate(lines[len(editable):])
        ]
        dropped = {id(r) for r in editable[len(lines):]}
        self.doc.rows = [r for r in self.doc.rows if id(r) not in dropped] + added
        self.notify(_label(self.app, "bp.ai_done"), timeout=6)
        self.run_worker(self._rebuild(keep=self._sel))

    # -- continue ----------------------------------------------------------

    @on(Button.Pressed, "#bp-continue")
    def _continue_pressed(self) -> None:
        self.action_continue_run()

    def action_continue_run(self) -> None:
        """Save the reviewed job, release this breakpoint, then move to the next
        parked video — or resume the run when none are left."""
        if self.job is None:
            self.app.push_screen(ProgressScreen(self.cp.params, resume_dir=self.run_dir))
            return
        self._sync()
        index = self.queue[0]
        stage = self.doc.stage
        try:
            rerun = review.apply(stage, self.job, self.doc.rows, self.mode) or self._forced_rerun
            self.cp.review_done(self.job, self.cp.completed(index), stage, rerun)
        except Exception as e:
            self.notify(f"{_label(self.app, 'err.save')}: {e}", severity="error", timeout=10)
            return
        self.notify(
            _label(self.app, "bp.rerun").format(stage=stage) if rerun
            else _label(self.app, "bp.saved"),
            timeout=6,
        )
        self._forced_rerun = False
        self._load()
        if self.job is None:  # every parked video reviewed — let the pipeline run on
            self.app.push_screen(ProgressScreen(self.cp.params, resume_dir=self.run_dir))
            return
        self.run_worker(self._rebuild())


# --------------------------------------------------------------------------
# Configuration: vertical sections on the left; entity sections get a top
# tab-button row, one tab per existing config file plus "+ new"
# --------------------------------------------------------------------------

CFG_SECTIONS = ["cfg.llm", "cfg.footage", "cfg.characters", "cfg.fandoms",
                "cfg.ads", "cfg.accounts", "cfg.presets"]

def _entity_form(kind: str) -> Form:
    """Declarative field list for one config-entity kind (ns keeps the e-{kind}-{key} ids)."""
    fields = {
        "characters": [
            Text("name", "f.name"),
            Text("age", "f.age"),
            Text("appearance", "f.appearance", large=True),
        ],
        "fandoms": [
            Text("name", "f.name"),
            Text("tone", "fandom_tone"),
            Text("docs", "fandom_docs"),
            Toggle("lore_tool", "fandom_lore_tool", value=True),
        ],
        "ads": [
            Text("name", "f.name"),
            Text("url", "f.url"),
            Text("ov_text", "ov_text"),
            Choice("ov_pos", "ov_pos",
                   options=[(p, p) for p in ("top_right", "top_left", "bottom_right", "bottom_left")]),
            Number("ov_start", "ov_start", default=6.0),
            Number("ov_dur", "ov_dur", default=8.0),
            Text("talking", "talking"),
            Text("snippet", "f.snippet"),
        ],
        "accounts": [
            Text("name", "f.name"),
            Choice("platform", "f.platform", options=[("youtube", "youtube"), ("local", "local")]),
            Choice("privacy", "f.privacy", options=[(p, p) for p in ("public", "unlisted", "private")]),
            Text("category", "f.category"),
            Text("def_lang", "f.def_lang"),
            Text("def_ctype", "f.def_ctype"),
            Text("def_ad", "f.def_ad"),
        ],
        "presets": [
            Text("name", "f.name"),
            Text("lang", "lang"),
            Text("ctype", "ctype"),
            Text("ad", "f.ad"),
            Choice("ad_mode", "f.ad_mode", options=[(m, m) for m in ("both", "overlay", "native")]),
            Text("visuals", "f.visuals"),
            Text("duration", "f.duration"),
            Range("profanity", "profanity", value=0, lo=0, hi=100, step=5, labels=PROFANITY_LABELS),
            Text("push", "f.push"),
            Number("count", "f.count", default=1, integer=True),
        ],
    }[kind]
    return Form(f"e-{kind}", fields)


def _entity_values(store: ConfigStore, kind: str, name: str | None) -> dict[str, str]:
    """Current values of an existing entity for form prefill; defaults when name is None."""
    if kind == "characters":
        c = store.characters.get(name) if name else None
        return {
            "name": c.name if c else "",
            "age": c.age if c else "",
            "appearance": c.appearance if c else "",
        }
    if kind == "fandoms":
        f = store.fandoms.get(name) if name else None
        return {
            "name": f.name if f else "",
            "tone": f.tone if f else "",
            # authored as one line; the loader keeps them as a list in reading order
            "docs": ", ".join(f.docs) if f else "",
            "lore_tool": f.lore_tool if f else True,
        }
    if kind == "ads":
        ad = store.ads.get(name) if name else None
        return {
            "name": ad.name if ad else "",
            "url": ad.url if ad else "https://",
            "ov_text": ad.overlay.text if ad and ad.overlay else "",
            "ov_pos": ad.overlay.position if ad and ad.overlay else "top_right",
            "ov_start": str(ad.overlay.start_s if ad and ad.overlay else 6),
            "ov_dur": str(ad.overlay.duration_s if ad and ad.overlay else 8),
            "talking": ad.native.talking_points if ad and ad.native else "",
            "snippet": ad.description.snippet if ad else "🔗 {url}",
        }
    if kind == "accounts":
        acc = store.accounts.get(name) if name else None
        yt = acc.youtube if acc else None
        return {
            "name": acc.name if acc else "",
            "platform": acc.platform if acc else "youtube",
            "privacy": yt.privacy if yt else "public",
            "category": yt.category_id if yt else "24",
            "def_lang": acc.defaults.lang if acc else "",
            "def_ctype": acc.defaults.content_type if acc else "",
            "def_ad": acc.defaults.ad if acc else "",
        }
    p = store.presets.get(name) if name else None
    return {
        "name": p.name if p else "",
        "lang": p.lang if p else "en",
        "ctype": p.content_type if p else "",
        "ad": p.ad if p else "",
        "ad_mode": (p.ad_mode or "both") if p else "both",
        "visuals": p.visuals if p else "",
        "duration": str(p.duration_s) if p and p.duration_s else "",
        "profanity": p.profanity if p and p.profanity else 0,
        "push": p.push if p else "",
        "count": str(p.count if p and p.count else 1),
    }


class EntityPane(Vertical):
    """One config section: a tab-button row on top, a form below, save/delete buttons."""

    def __init__(self, kind: str, **kwargs):
        super().__init__(**kwargs)
        self.kind = kind
        self._names: list[str | None] = []
        self._current: str | None = None
        self._form: Form | None = None

    def _store_dict(self) -> dict:
        store: ConfigStore = self.app.store
        return {
            "characters": store.characters,
            "fandoms": store.fandoms,
            "ads": store.ads,
            "accounts": store.accounts,
            "presets": store.presets,
            "llm": store.llm_profiles,
        }[self.kind]

    def _config_dir(self) -> Path:
        return Path("configs") / self.kind

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Horizontal(id=f"tabs-{self.kind}", classes="tabbar")
        yield VerticalScroll(id=f"form-{self.kind}", classes="entity-form")
        with Horizontal(classes="entity-actions"):
            yield Button(t("save"), id=f"save-{self.kind}", variant="success")
            yield Button(t("delete"), id=f"del-{self.kind}", variant="error")

    async def on_mount(self) -> None:
        await self._rebuild_tabs()

    def _tab_label(self, name: str | None) -> str:
        return name if name else _label(self.app, "new_tab")

    async def _rebuild_tabs(self, active: str | None = None) -> None:
        self._names = list(self._store_dict()) + [None]  # None = "+ new"
        # NB: None is also the "+ new" sentinel — an unset `active` must not match it
        idx = self._names.index(active) if active is not None and active in self._names else 0
        bar = self.query_one(f"#tabs-{self.kind}", Horizontal)
        await bar.remove_children()
        for i, n in enumerate(self._names):
            btn = Button(
                self._tab_label(n),
                id=f"t-{self.kind}-{i}",
                classes="tab-btn" + (" tab-active" if i == idx else ""),
            )
            await bar.mount(btn)
        await self._fill_form(self._names[idx])

    @on(Button.Pressed, ".tab-btn")
    async def _tab(self, event: Button.Pressed) -> None:
        kind, idx = event.button.id.split("-")[1:]
        if kind != self.kind:
            return
        for b in self.query(".tab-btn"):
            b.set_class(b.id == event.button.id, "tab-active")
        await self._fill_form(self._names[int(idx)])

    async def _fill_form(self, name: str | None) -> None:
        self._current = name
        form = self.query_one(f"#form-{self.kind}", VerticalScroll)
        await form.remove_children()
        self._form = _entity_form(self.kind)
        await form.mount(*self._form.build(lambda k: _label(self.app, k)))
        self._form.fill(self, self._values(name))

    def _values(self, name: str | None) -> dict:
        """Prefill for the form. Overridden where the entities do not live in the
        store dict this kind normally reads (a fandom's own cast)."""
        return _entity_values(self.app.store, self.kind, name)

    def _val(self, fid: str) -> str:
        return str(self._form.read(self).get(fid, "")).strip() if self._form else ""

    @on(NumStep.Pressed)
    def _num_step(self, event: NumStep.Pressed) -> None:
        _handle_number_step(self, event)

    @on(Button.Pressed)
    async def _actions(self, event: Button.Pressed) -> None:
        if event.button.id == f"save-{self.kind}":
            await self._save()
        elif event.button.id == f"del-{self.kind}":
            self._delete_ask()

    async def _save(self) -> None:
        vals = self._form.read(self) if self._form else {}
        name = str(vals.get("name", "")).strip()
        if not name:
            self.notify(_label(self.app, "name_req"), severity="error")
            return
        try:
            path = self._write(name, vals)
        except Exception as e:
            self.notify(f"{_label(self.app, 'err.save')}: {e}", severity="error", timeout=8)
            return
        # rename: we were editing an existing entity under a different name — drop
        # the old file so a rename moves it instead of leaving a duplicate behind.
        if self._current and self._current != name:
            (self._config_dir() / f"{self._current}.toml").unlink(missing_ok=True)
        self.app.store = ConfigStore()
        await self._rebuild_tabs(active=name)
        self.notify(f"{_label(self.app, 'saved')}: {path}", timeout=6)

    def _delete_ask(self) -> None:
        name = self._current
        if not name:  # "+ new" tab
            return

        def _confirmed(ok: bool | None) -> None:
            if ok:
                self.run_worker(self._delete(name), exclusive=False)

        self.app.push_screen(
            ConfirmModal(_label(self.app, "confirm_del").format(name=name)), _confirmed
        )

    async def _delete(self, name: str) -> None:
        path = self._config_dir() / f"{name}.toml"
        path.unlink(missing_ok=True)
        self.app.store = ConfigStore()
        await self._rebuild_tabs()
        self.notify(f"{_label(self.app, 'deleted')}: {path}", timeout=6)

    def _write(self, name: str, vals: dict) -> Path:
        if self.kind == "characters":
            return _write_character(self.app.store, name, vals)
        if self.kind == "ads":
            ov_dir = Path("assets/ads") / name / "overlay"
            nat_dir = Path("assets/ads") / name / "native"
            ov_dir.mkdir(parents=True, exist_ok=True)
            nat_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "name": name,
                "url": vals["url"],
                "modes": ["overlay", "native"],
                "overlay": {
                    "assets_dir": str(ov_dir),
                    "text": vals["ov_text"],
                    "position": vals["ov_pos"] or "top_right",
                    "start_s": float(vals["ov_start"]),
                    "duration_s": float(vals["ov_dur"]),
                },
                "native": {"assets_dir": str(nat_dir), "talking_points": vals["talking"]},
                "description": {"snippet": vals["snippet"]},
            }
        elif self.kind == "accounts":
            data = {
                "name": name,
                "platform": vals["platform"] or "youtube",
                "youtube": {
                    "client_secret": "secrets/client_secret.json",
                    "token": f"secrets/{name}_token.json",
                    "privacy": vals["privacy"] or "public",
                    "category_id": vals["category"] or "24",
                },
                "defaults": {
                    k: v
                    for k, v in {
                        "lang": vals["def_lang"],
                        "content_type": vals["def_ctype"],
                        "ad": vals["def_ad"],
                    }.items()
                    if v
                },
            }
        else:  # presets
            data = {
                "name": name,
                "lang": vals["lang"],
                "content_type": vals["ctype"],
                "ad": vals["ad"],
                "ad_mode": vals["ad_mode"] or "both",
                "visuals": vals["visuals"],
                "profanity": int(vals["profanity"]),
                "push": vals["push"],
                "count": int(vals["count"] or 1),
            }
            if vals["duration"]:
                data["duration_s"] = float(vals["duration"])
        path = self._config_dir() / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        return path


class LLMPane(EntityPane):
    """LLM profiles: provider + model presets + API key input (persisted to .env)."""

    def __init__(self, **kwargs):
        super().__init__("llm", **kwargs)

    def _text_value(self, wid: str) -> str:
        try:
            return self.query_one(f"#{wid}", Input).value.strip()
        except Exception:
            return self.query_one(f"#{wid}", TextArea).text.strip()

    def _set_text_value(self, wid: str, value: str) -> None:
        try:
            self.query_one(f"#{wid}", Input).value = value
            return
        except Exception:
            pass
        area = self.query_one(f"#{wid}", TextArea)
        area.text = value
        resize_text_field(area)

    def _tab_label(self, name: str | None) -> str:
        if name and name == self.app.store.global_cfg.llm.profile:
            return f"★ {name}"
        return super()._tab_label(name)

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Static("", id="llm-status")
        yield Horizontal(id="tabs-llm", classes="tabbar")
        yield VerticalScroll(id="form-llm", classes="entity-form")
        with Horizontal(classes="entity-actions"):
            yield Button(t("save"), id="save-llm", variant="success")
            yield Button(t("activate"), id="activate-llm", variant="primary")
            yield Button(t("delete"), id="del-llm", variant="error")

    async def _fill_form(self, name: str | None) -> None:
        self._current = name
        t = lambda k: _label(self.app, k)  # noqa: E731
        prof = self.app.store.llm_profiles.get(name) if name else LLMProfile(name="")
        prof = prof or LLMProfile(name="")
        form = self.query_one("#form-llm", VerticalScroll)
        await form.remove_children()
        _, eff_model, _ = resolve_provider(prof)
        presets = MODEL_PRESETS.get(prof.provider, [])
        preset_val = eff_model if eff_model in presets else CUSTOM
        self._form = Form("e-llm", [
            Text("name", "f.name", value=prof.name),
            Choice("provider", "provider", options=[(p, p) for p in PROVIDERS], value=prof.provider),
            Choice("preset", "model_preset",
                   options=[(m, m) for m in presets] + [("✍ custom", CUSTOM)], value=preset_val),
            Text("model", "model", value=prof.model or eff_model),
            Text("base", "base_url", value=prof.base_url,
                 placeholder=PROVIDERS.get(prof.provider, {}).get("base_url", "")),
            Number("temp", "temp", value=str(prof.temperature), default=1.2),
            Toggle("web", "web_search", value=prof.web_search),
            Note("web_search_note"),
            Text("key", "api_key", value="", password=True),
        ])
        await form.mount(*self._form.build(t))
        self._refresh_key_status()

    def _profile_from_form(self) -> LLMProfile:
        return LLMProfile(
            name=self._val("name"),
            provider=str(self.query_one("#e-llm-provider", Select).value),
            base_url=self._text_value("e-llm-base"),
            model=self._text_value("e-llm-model"),
            key_env="",
            temperature=float(self.query_one("#e-llm-temp", Input).value or 1.2),
            web_search=self.query_one("#e-llm-web", Switch).value,
        )

    def _refresh_key_status(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        try:
            prof = self._profile_from_form()
        except Exception:
            return
        _, eff_model, key_env = resolve_provider(prof)
        has_key = bool(os.environ.get(key_env))
        key_input = self.query_one("#e-llm-key", Input)
        key_input.placeholder = t("key_saved_ph") if has_key else t("key_empty_ph")
        active = self.app.store.global_cfg.llm.profile or "—"
        mark = f"[green]{t('key_ok')}[/green]" if has_key else f"[red]{t('key_no')}[/red]"
        self.query_one("#llm-status", Static).update(
            f" {t('active_now')}: [b]{active}[/b] · {prof.provider} · {eff_model} · {mark} [dim]({key_env})[/dim]"
        )

    @on(Select.Changed, "#e-llm-provider")
    def _provider_changed(self, event: Select.Changed) -> None:
        provider = str(event.value)
        presets = MODEL_PRESETS.get(provider, [])
        preset_sel = self.query_one("#e-llm-preset", Select)
        preset_sel.set_options([(m, m) for m in presets] + [("✍ custom", CUSTOM)])
        default_model = PROVIDERS.get(provider, {}).get("model", "")
        preset_sel.value = default_model if default_model in presets else CUSTOM
        self._set_text_value("e-llm-model", default_model)
        try:
            self.query_one("#e-llm-base", TextArea).tooltip = PROVIDERS.get(provider, {}).get("base_url", "")
        except Exception:
            self.query_one("#e-llm-base", Input).placeholder = PROVIDERS.get(provider, {}).get("base_url", "")
        self._refresh_key_status()

    @on(Select.Changed, "#e-llm-preset")
    def _preset_changed(self, event: Select.Changed) -> None:
        if str(event.value) != CUSTOM:
            self._set_text_value("e-llm-model", str(event.value))
            self._refresh_key_status()

    # save/del buttons are handled by the inherited EntityPane._actions
    # (ids save-llm / del-llm match its f-string patterns)
    @on(Button.Pressed, "#activate-llm")
    async def _activate(self) -> None:
        name = self._val("name")
        if name and name in self.app.store.llm_profiles:
            _update_global_toml("llm", {"profile": name})
            self.app.store = ConfigStore()
            await self._rebuild_tabs(active=name)
            self.notify(f"{_label(self.app, 'active_now')}: {name}", timeout=6)

    def _write(self, name: str, vals: dict) -> Path:
        prof = self._profile_from_form()
        key = self.query_one("#e-llm-key", Input).value.strip()
        if key:
            _, _, key_env = resolve_provider(prof)
            set_env_var(key_env, key)
        data = {
            "name": name,
            "provider": prof.provider,
            "base_url": prof.base_url,
            "model": prof.model,
            "key_env": "",
            "temperature": prof.temperature,
            "web_search": prof.web_search,
        }
        path = self._config_dir() / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        return path


class FootagePane(Vertical):
    """Footage keys — single stock keys (Pexels, Pixabay) as password inputs, and
    the AI-generator tokens (Hugging Face, Pollinations) as multi-key lists (one
    key per line) so orchestration can rotate through them. Saved to .env."""

    SINGLE_KEYS = [("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY")]
    MULTI_KEYS = [("hf", "HF_TOKEN"), ("pollinations", "POLLINATIONS_TOKEN")]

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Static("", id="footage-status")
        yield Static(t("footage_note"), classes="hint")
        for fid, env in self.SINGLE_KEYS:
            yield Label(f"{t(fid + '_key')}  ({env})")
            yield from Text(fid, "", password=True).build("fk", t)
        for fid, env in self.MULTI_KEYS:
            yield Label(f"{t(fid + '_key')}  ({env})")
            yield from Text(fid, "", large=True).build("mk", t)
            yield Static(t("multikey_note"), classes="hint")
        yield Button(t("save"), id="save-footage", variant="success")

    def on_mount(self) -> None:
        for fid, env in self.MULTI_KEYS:  # prefill existing keys, one per line
            area = self.query_one(f"#mk-{fid}", TextArea)
            area.text = "\n".join(gen_keys(env))
            resize_text_field(area, large=True)
        self._refresh()

    def _refresh(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        parts = []
        for fid, env in self.SINGLE_KEYS:
            ok = bool(os.environ.get(env))
            mark = "[green]✔[/green]" if ok else "[red]✘[/red]"
            self.query_one(f"#fk-{fid}", Input).placeholder = t("key_saved_ph") if ok else t("key_empty_ph")
            parts.append(f"{fid} {mark}")
        for fid, env in self.MULTI_KEYS:
            n = len(gen_keys(env))
            parts.append(f"{fid} [green]{n}[/green]" if n else f"{fid} [red]0[/red]")
        self.query_one("#footage-status", Static).update(" " + " · ".join(parts))

    @on(Button.Pressed, "#save-footage")
    def _save(self) -> None:
        saved = 0
        for fid, env in self.SINGLE_KEYS:
            val = self.query_one(f"#fk-{fid}", Input).value.strip()
            if val:
                set_env_var(env, val)
                self.query_one(f"#fk-{fid}", Input).value = ""
                saved += 1
        for fid, env in self.MULTI_KEYS:
            keys = [k.strip() for k in self.query_one(f"#mk-{fid}", TextArea).text.splitlines() if k.strip()]
            set_env_var(env, ",".join(keys))
            os.environ[env] = ",".join(keys)  # reflect immediately so the count refreshes
            saved += 1 if keys else 0
        self._refresh()
        self.notify(f"{_label(self.app, 'saved')}: {saved} {_label(self.app, 'keys.saved_n')}", timeout=6)


class CharacterPane(EntityPane):
    """Global reusable character library — a plain manual editor. AI assistance
    (photo→description, autofill) lives in the AI-drama wizard, not here. Compiled
    prompts are rebuilt lazily at generation time, so edits just mark it dirty."""

    def __init__(self, **kwargs):
        super().__init__("characters", **kwargs)

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Horizontal(id="tabs-characters", classes="tabbar")
        yield VerticalScroll(id="form-characters", classes="entity-form")
        yield Static(t("char_cfg_note"), classes="hint")
        with Horizontal(classes="entity-actions"):
            yield Button(t("save"), id="save-characters", variant="success")
            yield Button(t("delete"), id="del-characters", variant="error")


class FandomCharacterPane(_CharEditAI, CharacterPane):
    """The cast that belongs to ONE world, written to
    ``configs/fandoms/<world>/characters/`` instead of the global library.

    Same editor, different shelf: a world's people are part of the world, so a run
    set in it gets them without the operator adding them, and they never clutter the
    library other modes pick from. The photo→appearance helper comes along because
    this is where a world's cast is actually authored — there is no wizard step for
    it to live in."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fandom = ""

    async def set_fandom(self, name: str) -> None:
        self._fandom = name
        await self._rebuild_tabs()

    # -- pointed at the fandom's folder rather than the global library ------
    def _store_dict(self) -> dict:
        cfg = self.app.store.fandoms.get(self._fandom)
        return {c.name: c for c in cfg.cast} if cfg else {}

    def _config_dir(self) -> Path:
        return FANDOMS_DIR / self._fandom / "characters"

    def _values(self, name: str | None) -> dict:
        c = self._store_dict().get(name) if name else None
        return {"name": c.name if c else "", "age": c.age if c else "",
                "appearance": c.appearance if c else ""}

    def _write(self, name: str, vals: dict) -> Path:
        return _write_character(self.app.store, name, vals, directory=self._config_dir(),
                                existing=self._store_dict().get(name))

    async def _save(self) -> None:
        if not self._fandom:  # the "+ new" fandom tab: there is no folder to write to
            self.notify(_label(self.app, "fandom_pick_first"), severity="warning")
            return
        await super()._save()

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Horizontal(id="tabs-characters", classes="tabbar")
        yield VerticalScroll(id="form-characters", classes="entity-form")
        yield Horizontal(Input(placeholder=t("char_photo_ph"), id="char-photo-path"),
                         Button(t("char_describe"), id="char-describe", variant="primary"),
                         id="char-photo-row")
        yield from Text("prompt", "", placeholder="char_prompt_ph").build("char", t)
        with Horizontal(classes="entity-actions"):
            yield Button(t("fandom_fill_person"), id="world-autofill", variant="primary")
        with Horizontal(classes="entity-actions"):
            yield Button(t("save"), id="save-characters", variant="success")
            yield Button(t("delete"), id="del-characters", variant="error")

    # -- required by _CharEditAI --------------------------------------------
    def _char_form(self) -> Form | None:
        return self._form

    @on(Button.Pressed, "#char-describe")
    def _describe(self, event: Button.Pressed) -> None:
        event.stop()
        self.do_describe()

    # -- filling a person in, from the world they live in --------------------

    def _world_text(self) -> str:
        cfg = self.app.store.fandoms.get(self._fandom)
        if not cfg:
            return ""
        return (cfg.canon or "").strip() or read_lore(cfg)

    @on(Button.Pressed, "#world-autofill")
    def _fill_person(self, event: Button.Pressed) -> None:
        """Fill this person's age and looks. Unlike the drama's cast fill, which
        invents an ensemble to fit a plot, this reads the WORLD: whatever it makes up
        has to be something this place could contain."""
        event.stop()
        form = self._char_form()
        if form is None or not self._fandom:
            self.notify(_label(self.app, "fandom_pick_first"), severity="warning")
            return
        member = form.read(self)
        if not str(member.get("name", "")).strip():
            self.notify(_label(self.app, "name_req"), severity="warning")
            return
        try:
            prompt = self.query_one("#char-prompt", TextArea).text.strip()
        except Exception:
            prompt = ""
        lang = self.app.store.global_cfg.ui.lang
        world = self._world_text()
        self.notify(_label(self.app, "char_working"), timeout=3)
        self.run_worker(lambda: self._person_worker(member, lang, prompt, world),
                        thread=True, exclusive=False)

    def _person_worker(self, member: dict, lang: str, prompt: str, world: str) -> None:
        try:
            changed = char_ai.autofill_one(self._llm(), member, lang, prompt, world=world)
        except Exception as e:
            self.app.call_from_thread(
                self.notify, f"{_label(self.app, 'char_ai_err')}: {e}",
                severity="error", timeout=10,
            )
            return
        if not changed:
            self.app.call_from_thread(self.notify, _label(self.app, "char_nothing"), timeout=5)
            return
        self.app.call_from_thread(self._apply, changed, "char_filled")


class FandomPane(EntityPane):
    """Config → Fandoms: one tab per world folder under ``configs/fandoms/``.

    A fandom is a DIRECTORY, not a file (see ``config.loader._load_fandoms``), so
    this pane departs from :class:`EntityPane` exactly where that matters — creating
    one lays out the folder (``fandom.toml``, an empty ``lore.md``, ``characters/``),
    renaming MOVES it, and deleting removes the tree. Everything the world is made of
    is edited here: its settings, its lore (with the canon sheet compiled from it),
    and its own cast."""

    def __init__(self, **kwargs):
        super().__init__("fandoms", **kwargs)

    def _config_dir(self) -> Path:
        return FANDOMS_DIR

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield Horizontal(id="tabs-fandoms", classes="tabbar")
        yield VerticalScroll(id="form-fandoms", classes="entity-form")
        with Horizontal(classes="entity-actions"):
            yield Button(t("save"), id="save-fandoms", variant="success")
            yield Button(t("delete"), id="del-fandoms", variant="error")
        yield LoreEditor(prefix="clore", id="cfg-lore")
        yield Static(t("drama_cast_head"), classes="group-head")
        yield FandomCharacterPane(id="cfg-fandom-cast")

    async def on_mount(self) -> None:
        await super().on_mount()
        # the children compose after us, so the first tab is applied once laid out
        self.call_after_refresh(lambda: self._retarget(self._current or ""))

    def _retarget(self, name: str) -> None:
        """Point the lore editor and the cast editor at the tab's world."""
        for editor in self.query(LoreEditor):
            editor.set_fandom(name)
        for pane in self.query(FandomCharacterPane):
            self.run_worker(pane.set_fandom(name), exclusive=False)

    async def _fill_form(self, name: str | None) -> None:
        await super()._fill_form(name)
        self._retarget(name or "")

    def _write(self, name: str, vals: dict) -> Path:
        root = self._config_dir() / name
        (root / "characters").mkdir(parents=True, exist_ok=True)
        # the compiled sheet survives a settings edit or a rename: it belongs to the
        # lore, and neither the tone nor the folder's name changed a word of it
        prev = self.app.store.fandoms.get(self._current or name) or self.app.store.fandoms.get(name)
        docs = [d.strip() for d in str(vals.get("docs", "")).replace("\n", ",").split(",") if d.strip()]
        if not any(root.glob("*.md")):  # a brand-new world starts with a blank page
            (root / "lore.md").write_text("", encoding="utf-8")
        return write_fandom(FandomConfig(
            name=name,
            docs=docs,
            tone=str(vals.get("tone", "")).strip(),
            lore_tool=bool(vals.get("lore_tool", True)),
            canon=prev.canon if prev else "",
            docs_sha=prev.docs_sha if prev else "",
            root=root,
        ))

    async def _save(self) -> None:
        """Like :meth:`EntityPane._save`, except a rename moves the whole folder —
        the lore, the cast and the compiled sheet come with the world."""
        vals = self._form.read(self) if self._form else {}
        name = str(vals.get("name", "")).strip()
        if not name:
            self.notify(_label(self.app, "name_req"), severity="error")
            return
        old = self._current
        try:
            if old and old != name:
                src, dst = self._config_dir() / old, self._config_dir() / name
                if dst.exists():
                    raise ConfigError(f"fandom '{name}' already exists")
                if src.is_dir():
                    src.rename(dst)
            path = self._write(name, vals)
        except Exception as e:
            self.notify(f"{_label(self.app, 'err.save')}: {e}", severity="error", timeout=8)
            return
        self.app.store = ConfigStore()
        await self._rebuild_tabs(active=name)
        self.notify(f"{_label(self.app, 'saved')}: {path}", timeout=6)

    async def _delete(self, name: str) -> None:
        path = self._config_dir() / name
        shutil.rmtree(path, ignore_errors=True)
        self.app.store = ConfigStore()
        await self._rebuild_tabs()
        self.notify(f"{_label(self.app, 'deleted')}: {path}", timeout=6)


class ConfigScreen(Screen):
    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield TopBar(t("menu.config"))
        with Horizontal(id="cfg"):
            yield ListView(
                *[ListItem(Label(t(k)), id=f"sec-{i}") for i, k in enumerate(CFG_SECTIONS)],
                id="cfg-nav",
            )
            with ContentSwitcher(initial="cpane-0", id="cfg-body"):
                yield LLMPane(id="cpane-0", classes="pane")
                yield FootagePane(id="cpane-1", classes="pane")
                yield CharacterPane(id="cpane-2", classes="pane")
                yield FandomPane(id="cpane-3", classes="pane")
                yield EntityPane("ads", id="cpane-4", classes="pane")
                yield EntityPane("accounts", id="cpane-5", classes="pane")
                yield EntityPane("presets", id="cpane-6", classes="pane")

    def on_mount(self) -> None:
        self.query_one("#cfg-nav", ListView).focus()

    @on(ListView.Highlighted, "#cfg-nav")
    def _nav(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.item.id.split("-")[1]
        self.query_one("#cfg-body", ContentSwitcher).current = f"cpane-{idx}"


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------


class SlopgenApp(App):
    TITLE = "slopgen"
    BINDINGS = [("escape", "back", "")]
    # Textual 8.x in-app text selection crashes on mouse-down over some list
    # items (container.parent resolves to None in screen._forward_event). We don't
    # need in-app selection — the terminal (Konsole) handles native selection — so
    # disable it to avoid the crash.
    ALLOW_SELECT = False
    CSS = """
    #topbar { dock: top; height: 3; background: $panel; }
    #tb-title { width: 1fr; content-align: left middle; height: 3; color: $primary; text-style: bold; }
    #topbar Button { min-width: 8; }
    #tb-back { min-width: 6; }

    #home-center { align: center middle; height: 100%; }
    #home-inner { width: auto; height: auto; align: center middle; }
    #logo { color: $primary; text-align: center; width: auto; }
    #logo-sub { color: $secondary; text-align: center; width: 100%; margin-bottom: 2; }
    #home-menu { width: 56; height: auto; align: center middle; }
    #home-menu Button {
        width: 100%; height: 3; margin-bottom: 1;
        content-align: center middle; text-style: bold;
    }

    #wizard, #cfg { height: 1fr; }
    #wizard-nav, #cfg-nav { width: 28; border-right: tall $secondary; background: $surface; }
    #wizard-nav ListItem, #cfg-nav ListItem { padding: 1 2; }
    /* ContentSwitcher defaults to height:auto — pin it to the row so the pane
       inside can take a real height and scroll instead of overflowing */
    #wizard-body, #cfg-body { width: 1fr; height: 1fr; align: center top; }

    /* right inspector panel: help by default, sub-settings on demand */
    #wizard-inspector {
        width: 46; height: 1fr; padding: 1 2;
        border-left: tall $secondary; background: $surface;
    }
    .insp-desc { margin-top: 1; height: 1fr; }
    .insp-keys {
        dock: bottom; height: auto; color: $text-muted;
        border-top: tall $secondary; padding-top: 1;
    }
    /* AI-filled fields are tinted so edits from the model stand out */
    .ai-filled { border: round $accent; background: $accent 15%; }
    #cast-list { height: 27; min-height: 27; margin-top: 1; border: round $secondary; }
    /* the world's people, on the fandom wizard's World step. An explicit height for
       the same reason #cast-list has one: a ListView defaults to height:1fr, and the
       wizard pane sizes itself to its content, so 1fr of nothing collapses to a
       single row. Shorter than the drama's list — the lore editor shares the step. */
    #world-list { height: 18; min-height: 18; margin-top: 1; border: round $secondary; }
    /* A character card of the fandom's own: two fixed lines, name over "age · look".
       Every element is pinned, because a Static left to size itself inside a Vertical
       that has no height makes the container claim 1fr and one card swallows the
       whole list. Denser than the drama's three-line rows — there is no status
       column to make room for, and the lore editor shares this step. */
    .world-item { height: 2; padding: 0 1; margin: 0 1; }
    .world-item.-highlight { background: $accent 18%; }
    .world-info { height: 2; width: 1fr; }
    .world-name { height: 1; text-style: bold; color: $primary; }
    .world-line { height: 1; color: $text-muted; }
    /* cast rows: bordered 3-line cards, name/age/look left, status right at a fixed column */
    .cast-item { height: auto; padding: 0; margin: 0 1 0 1; border: round $secondary; }
    .cast-item.-highlight { border: round $accent; background: $accent 12%; }
    .cast-row { height: 3; width: 1fr; padding: 0 1; }
    .cast-info { width: 1fr; height: 3; }
    .cast-name { text-style: bold; color: $primary; }
    .cast-line { height: 1; }
    .cast-dim { color: $text-muted; }
    .cast-status { width: 14; height: 3; content-align: left middle; text-align: left; }
    .cast-status.st-global { color: $success; }
    .cast-status.st-dirty { color: $warning; }
    .cast-status.st-local { color: $text-muted; }
    #pick-global { height: auto; max-height: 60%; margin-top: 1; border: round $secondary; }
    #char-prompt { margin-top: 1; }

    /* three modes + their blurbs do not fit the home menu's spacing */
    ModeSelectScreen #logo-sub { margin-bottom: 1; }
    ModeSelectScreen #home-menu Button { margin-bottom: 0; }
    /* an identity-coloured button paints its own body and edges, so it has to say
       where the keyboard is itself — the variants used to carry that */
    .mode-btn { text-style: bold; }
    .mode-btn:focus { text-style: bold underline; }
    .mode-btn:hover { tint: $foreground 10%; }
    /* identity-coloured buttons set their own background, so pin a dark foreground
       that reads on every colour in the palette (they are all light-to-mid) */
    ModeSelectScreen #home-menu .hint { margin-top: 0; margin-bottom: 1; }

    /* the lore editor: source and rendered markdown share one slot, one visible
       at a time; the compiled sheet sits under them as read-only text */
    LoreEditor { height: auto; }
    .lore-area, .lore-viewbox {
        width: 100%; height: 24; margin: 0 1 1 1;
        border: round $secondary; background: $surface;
    }
    .lore-area:focus { background: $panel; }
    .lore-viewbox { overflow-y: auto; padding: 0 1; }
    .lore-viewbox:focus { border: round $accent; }
    .lore-viewbox Markdown { height: auto; background: transparent; }
    .lore-canon-box {
        width: 100%; height: 16; margin: 0 1 1 1;
        border: round $secondary; background: $surface;
    }
    .lore-canon { height: auto; color: $text-muted; padding: 0 1; }
    #cfg-fandom-cast { height: auto; }
    FandomCharacterPane #char-photo-row { height: 3; margin-top: 1; }
    FandomCharacterPane #char-photo-row Input { width: 1fr; }

    /* panes fill the body height so the VerticalScroll actually scrolls
       (auto/max-height + center alignment clipped the overflow instead) */
    .pane { width: 1fr; max-width: 76; height: 100%; padding: 1 2; }
    .pane Label { margin-top: 1; color: $text-muted; }
    .pane Select, .pane Input, .pane TextArea { width: 100%; }
    /* the one text field: no border; a background-coloured pad row above & below
       the text; height == rows+2 (set in code). Grows 1..5 then scrolls. */
    .text-field {
        width: 100%; border: none; padding: 1 1; margin: 0 1 1 1;
        background: $surface; scrollbar-size-vertical: 1;
    }
    .text-field:focus { background: $panel; }
    /* numeric field: no border; same background & horizontal inset as .text-field
       (padding 0 1 → bg pad row top/bottom via height 3 + centered content). A
       narrow ▲/▼ stepper column on the left carries a full-height thin divider. */
    .number-row {
        height: 3; width: 100%; margin: 0 1 1 1;
        padding: 0 1; background: $surface;
    }
    .number-row:focus-within { background: $panel; }
    /* value on the middle row (margin → bg pad row above/below, matching text) */
    .number-row Input {
        width: 1fr; border: none; height: 1; margin: 1 0 1 1;
        background: transparent;
    }
    /* full-height stepper column: ▲ docked to the top row, ▼ to the bottom,
       thin vkey divider on the right spanning all three rows */
    .num-steps {
        width: 2; height: 3; margin: 0; padding: 0;
        border-right: vkey $secondary;
    }
    .num-step {
        width: 2; min-width: 2; height: 1; margin: 0; padding: 0;
        border: none; background: transparent; color: $primary; text-style: bold;
        content-align: center middle;
    }
    .num-step-up { dock: top; }
    .num-step-down { dock: bottom; }
    .num-step:hover { background: $primary; color: $background; }
    .form-group { height: auto; }
    .hint { color: $secondary; margin-top: 1; }
    .group-head { color: $accent; margin-top: 2; text-style: bold; }
    .nav-row { height: auto; margin-top: 2; }
    .nav-btn { margin-top: 2; margin-right: 2; }
    .nav-row .nav-btn { margin-top: 0; }
    .switch-row { height: auto; margin-top: 1; }
    .switch-row Label { margin-top: 1; margin-left: 2; }
    .drama-sep { color: $secondary; margin: 1 0; }

    .trope-row { height: auto; margin-bottom: 1; }
    .trope-row Switch { margin-top: 1; }
    .trope-text { width: 1fr; height: auto; margin-left: 1; }
    .trope-text Label { margin-top: 1; color: $text; }
    .trope-desc { color: $text-muted; }

    #w-summary { margin-top: 1; }
    #w-cmd {
        margin-top: 2; padding: 1 2;
        background: $surface; color: $success; border: round $primary;
    }
    #w-start { margin-top: 2; width: 100%; }

    #llm-status { height: 1; background: $surface; margin-bottom: 1; }
    .tabbar { height: 3; margin-bottom: 1; }
    .tab-btn { min-width: 10; margin-right: 1; background: $surface; }
    .tab-btn.tab-active { background: $primary; color: $background; text-style: bold; }
    .entity-form { height: auto; max-height: 60%; }
    .entity-actions { height: auto; margin-top: 1; }
    .entity-actions Button { margin-right: 2; }

    ConfirmModal { align: center middle; }
    #confirm-box { width: 60; height: auto; padding: 2 3; background: $panel; border: thick $error; }
    #confirm-text { margin-bottom: 2; }
    #confirm-row { height: auto; }
    #confirm-row Button { margin-right: 2; }

    #run-summary { padding: 0 2; height: 1; background: $surface; }
    #run-progress { height: 1; padding: 0 2; }
    #run-stage { width: 34; }
    #run-bar { width: 1fr; }
    #run-count { width: 26; text-align: right; color: $text-muted; }
    #queue { height: 40%; margin: 1 2; }
    #log { height: 1fr; margin: 0 2 1 2; border: round $primary; }

    #gather-body { height: 1fr; margin: 1 2 0 2; }
    #shots { width: 1fr; height: 100%; }
    #shot-detail {
        width: 52; height: 100%; padding: 1 2; margin-left: 2;
        border: round $primary; background: $surface;
    }
    /* auto, not 1: a drama adds a line per episode's readiness under the tally */
    #gather-progress { height: auto; padding: 0 2; background: $surface; }
    #gather-row { height: 3; align: center middle; padding: 0 2; }
    #gather-row Button { margin: 0 1; }

    #bp-head { padding: 0 2; height: 1; background: $surface; }
    #bp-body { height: 1fr; margin: 0 2; }
    #bp-list-pane { width: 48; }
    #bp-list-pane .entity-actions { height: 3; margin-top: 0; }
    #bp-list-pane .entity-actions Button { margin-right: 1; }
    #bp-add, #bp-cut { width: auto; }
    #bp-up, #bp-down, #bp-del { width: auto; min-width: 5; }
    #bp-list { height: 1fr; }
    #bp-detail { width: 1fr; padding: 0 2; border-left: solid $primary 30%; }
    #bp-detail Select, #bp-detail .number-row { margin-bottom: 1; }
    #bp-note { padding: 0 2; color: $text-muted; }
    #bp-ai-box { height: auto; padding: 0 2; align: left middle; }
    #bp-ai-prompt { width: 1fr; }
    #bp-ai-go { margin-left: 2; }
    .bp-field-label { color: $text-muted; padding: 1 1 0 1; }
    .bp-chips { height: auto; margin-bottom: 1; }
    .bp-chip, .bp-chip-add { height: 1; min-width: 6; border: none; margin-right: 1; }
    #bp-actions { height: 3; align: center middle; padding: 0 2; }
    #bp-actions Button { margin: 0 1; }
    #pick-list { height: auto; max-height: 14; }
    """

    def __init__(self, store: ConfigStore | None = None, open_dir: Path | None = None):
        super().__init__()
        load_dotenv()
        self.store = store or ConfigStore()
        self.ui_lang = self.store.global_cfg.ui.lang
        self._theme_ready = False
        # a parked run to open straight away: whichever screen it is waiting on
        self._open_dir = open_dir

    def on_mount(self) -> None:
        self.register_theme(MINECRAFT_THEME)
        saved = self.store.global_cfg.ui.theme
        try:
            self.theme = saved
        except Exception:
            self.theme = "minecraft"
        self._theme_ready = True
        self.theme_changed_signal.subscribe(self, self._persist_theme)
        self.push_screen(HomeScreen())
        if self._open_dir is None:
            return
        if _review_jobs(self._open_dir):  # parked on a breakpoint
            self.push_screen(BreakpointScreen(self._open_dir))
        elif _paused_jobs(self._open_dir):  # parked for hand-made clips
            self.push_screen(ManualGatherScreen(self._open_dir))

    def _persist_theme(self, _theme) -> None:
        if self._theme_ready:
            _update_global_toml("ui", {"lang": self.ui_lang, "theme": self.theme})

    def action_back(self) -> None:
        if len(self.screen_stack) > 2:
            self.pop_screen()

    @on(Button.Pressed, "#tb-back")
    def _tb_back(self) -> None:
        self.action_back()

    # -- shared field behaviour (any screen) --------------------------------
    @on(TextArea.Changed)
    def _grow_text_field(self, event: TextArea.Changed) -> None:
        if event.text_area.has_class("text-field"):  # our unified fields self-size
            resize_text_field(event.text_area)
            # scroll cursor into view after the new height is laid out
            event.text_area.call_after_refresh(event.text_area.scroll_cursor_visible)

    @on(Button.Pressed, ".num-step")
    def _num_step(self, event: Button.Pressed) -> None:
        """The ↑/↓ steppers on a Number field: ±1 on the sibling input."""
        bid = event.button.id or ""
        wid = bid.rsplit("-", 1)[0]  # strip -inc / -dec
        try:
            inp = self.screen.query_one(f"#{wid}", Input)
        except Exception:
            return
        try:
            val = float(inp.value)
        except (TypeError, ValueError):
            val = 0.0
        val += 1 if bid.endswith("-inc") else -1
        inp.value = str(int(val)) if inp.type == "integer" else f"{val:g}"

    @on(Button.Pressed, "#tb-palette")
    def _tb_palette(self) -> None:
        self.action_command_palette()

    @on(Button.Pressed, "#tb-lang")
    def _tb_lang(self) -> None:
        self.ui_lang = "en" if self.ui_lang == "ru" else "ru"
        _update_global_toml("ui", {"lang": self.ui_lang, "theme": self.theme})
        # rebuild the whole UI in the new language
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(HomeScreen())
