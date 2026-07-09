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
import tomllib
from pathlib import Path

import tomli_w
from dotenv import load_dotenv
from textual import events, on
from textual.app import App, ComposeResult
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
    RichLog,
    Select,
    Static,
    Switch,
    TextArea,
)

from ..config import ConfigError, ConfigStore, RunParams, VisualsConfig
from ..config.envfile import set_env_var
from ..config.models import (
    AdConfig,
    AdDescriptionConfig,
    AdNativeConfig,
    AdOverlayConfig,
    CharacterConfig,
    LLMProfile,
    OrchestrationConfig,
    OrchestrationStage,
    VisualsBackground,
    VisualsForeground,
)
from ..llm import MODEL_PRESETS, PROVIDERS, ChatLLM, resolve_provider
from ..llm import characters as char_ai
from ..media.generate import PHOTO_MODELS, VIDEO_MODELS
from ..media.generate import env_keys as gen_keys
from ..media.generate import key_var_for_model
from ..pipeline import Orchestrator
from ..pipeline.context import AppContext
from .forms import Choice, Form, Group, Heading, Note, NumStep, Number, Range, Text, Toggle, resize_text_field

# slider bucket captions (threshold -> i18n key)
PROFANITY_LABELS = {0: "prof_none", 1: "prof_mild", 26: "prof_mod", 51: "prof_heavy", 76: "prof_max"}

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
)

I18N: dict[str, dict[str, str]] = {
    "en": {
        "subtitle": "industrial neuroslop pipeline",
        "menu.generate": "⛏  Generate videos",
        "menu.config": "⚙  Configuration",
        "menu.quit": "✖  Quit",
        "step.content": "Content",
        "step.characters": "Story",
        "step.visuals": "Visuals",
        "step.ads": "Ads",
        "step.publish": "Publish",
        "step.summary": "Summary",
        "mode_head": "What are we generating?",
        "mode_info": "⚡  Minute of useless info",
        "mode_info_desc": "the current mode — narrated facts over stock / AI b-roll",
        "mode_drama": "🎭  AI drama",
        "mode_drama_desc": "narrated anime-style story with a recurring cast + AI-generated shots",
        "drama_cast_head": "Cast",
        "drama_add": "＋ Add character",
        "drama_plot_head": "— Plot —",
        "drama_prompt_ph": "optional: tell the AI how to fill the cast / rewrite the plot",
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
        "help.drama_duration_min": "Target length of the drama, in minutes. The story may run a little over/under (see Tolerance).",
        "help.drama_duration_tol": "How many seconds the finished video may run over or under the target when the story calls for it.",
        # right-panel help + character editor
        "insp_help_head": "— Help —",
        "insp_keys": "Keys:\n  ↑ / ↓   move between steps\n  Tab     next field\n  Enter   open / confirm\n  Esc     back",
        "help.step.content": "Language, voice, topic and tone of the video. Leave the idea empty to let the LLM pick one.",
        "help.step.characters": "The drama's cast. Add characters (new or from your library), toggle who appears, and edit each on the right. AI can fill everyone at once (reading the premise) or one at a time.",
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
        "help.subs": "Subtitle animation style: word-pop, phrases, or karaoke.",
        "help.drama_scenario": "The drama's premise/plot. Empty or thin is fine — it's improvised at generation.",
        "help.drama_prompt": "Optional steer for '✨ AI fill cast': how to fill the cast, or ask it to rewrite the plot.",
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
        "pick_head": "Add a character",
        "pick_new": "＋ Create new",
        "pick_from_lib": "…or pick one from the library:",
        "char_new_name": "New character",
        "char_edit_head": "— Character —",
        "char_prompt_ph": "optional: tell the AI how to fill/rewrite this character",
        "char_autofill_all": "✨ AI fill cast",
        "char_cfg_note": "Manual editor. AI help (photo → description, autofill) lives in the AI-drama wizard.",
        "cast_save_global": "★ Save to library",
        "cast_remove": "🗑 Remove",
        "cast_empty": "add a character or write a plot first",
        "lang": "Content language",
        "voice": "Voice",
        "ctype": "Content type",
        "idea": "Your idea",
        "idea_ph": "leave empty — the LLM invents a topic",
        "profanity": "Profanity level",
        "prof_none": "clean",
        "prof_mild": "mild",
        "prof_mod": "moderate",
        "prof_heavy": "heavy",
        "prof_max": "constant f-bombs",
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
        "subs": "Subtitle style",
        "next": "Next  →",
        "prev": "←  Prev",
        "start": "⛏  G E N E R A T E",
        "summary_head": "Everything is set:",
        "cfg.llm": "LLM profiles",
        "cfg.footage": "Footage API keys",
        "cfg.characters": "Characters",
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
        "char_filled": "empty fields filled by AI",
        "char_nothing": "nothing to fill — all fields already set",
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
    },
    "ru": {
        "subtitle": "промышленный конвейер нейрослопа",
        "menu.generate": "⛏  Генерация видео",
        "menu.config": "⚙  Конфигурация",
        "menu.quit": "✖  Выход",
        "step.content": "Контент",
        "step.characters": "Сюжет",
        "step.visuals": "Видеоряд",
        "step.ads": "Реклама",
        "step.publish": "Публикация",
        "step.summary": "Итог",
        "mode_head": "Что генерируем?",
        "mode_info": "⚡  Минута бесполезной инфы",
        "mode_info_desc": "текущий режим — факты под сток/ИИ-видеоряд",
        "mode_drama": "🎭  ИИ-дорама",
        "mode_drama_desc": "озвученная аниме-история с постоянными персонажами + ИИ-кадры",
        "drama_cast_head": "Каст",
        "drama_add": "＋ Добавить персонажа",
        "drama_plot_head": "— Сюжет —",
        "drama_prompt_ph": "опционально: как ИИ заполнить каст / переписать сюжет",
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
        "help.drama_duration_min": "Целевая длина дорамы в минутах. История может немного выйти за рамки (см. Допуск).",
        "help.drama_duration_tol": "На сколько секунд готовое видео может превысить/недотянуть цель, если этого требует сюжет.",
        # помощь в правой панели + редактор персонажа
        "insp_help_head": "— Помощь —",
        "insp_keys": "Клавиши:\n  ↑ / ↓   переход между шагами\n  Tab     следующее поле\n  Enter   открыть / подтвердить\n  Esc     назад",
        "help.step.content": "Язык, голос, тема и тон видео. Оставь идею пустой — тему придумает LLM.",
        "help.step.characters": "Каст дорамы. Добавляй персонажей (новых или из библиотеки), включай/выключай участие, редактируй каждого справа. ИИ может заполнить всех сразу (читая замысел) или по одному.",
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
        "help.subs": "Стиль субтитров: word-pop, phrases или karaoke.",
        "help.drama_scenario": "Замысел/сюжет дорамы. Можно пусто или частично — додумается при генерации.",
        "help.drama_prompt": "Опциональная подсказка для «✨ ИИ заполнит каст»: как заполнить каст или переписать сюжет.",
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
        "pick_head": "Добавить персонажа",
        "pick_new": "＋ Создать нового",
        "pick_from_lib": "…или выбери из библиотеки:",
        "char_new_name": "Новый персонаж",
        "char_edit_head": "— Персонаж —",
        "char_prompt_ph": "опционально: как ИИ должен заполнить/переписать персонажа",
        "char_autofill_all": "✨ ИИ заполнит каст",
        "char_cfg_note": "Ручной редактор. ИИ-помощь (фото → описание, автозаполнение) — в визарде ИИ-дорам.",
        "cast_save_global": "★ Сохранить в библиотеку",
        "cast_remove": "🗑 Убрать",
        "cast_empty": "сначала добавь персонажа или впиши сюжет",
        "lang": "Язык контента",
        "voice": "Голос",
        "ctype": "Тип контента",
        "idea": "Своя идея",
        "idea_ph": "оставь пустым — нейронка придумает тему",
        "profanity": "Уровень мата",
        "prof_none": "чисто",
        "prof_mild": "лёгкий",
        "prof_mod": "умеренный",
        "prof_heavy": "жёсткий",
        "prof_max": "сплошной мат",
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
        "subs": "Стиль субтитров",
        "next": "Далее  →",
        "prev": "←  Назад",
        "start": "⛏  С Г Е Н Е Р И Р О В А Т Ь",
        "summary_head": "Всё настроено:",
        "cfg.llm": "Профили нейронок",
        "cfg.footage": "Ключи API футажа",
        "cfg.characters": "Персонажи",
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
        "char_filled": "пустые поля заполнены ИИ",
        "char_nothing": "нечего заполнять — все поля уже заданы",
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
    },
}


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

# widget id -> i18n key for the field's description, shown in the inspector top
# when that setting is focused. Fields absent here fall back to the step blurb.
FIELD_HELP = {
    "w-lang": "help.lang", "w-voice": "help.voice", "w-ctype": "help.ctype",
    "w-idea": "help.idea", "w-profanity": "help.profanity",
    "w-duration_min": "help.drama_duration_min", "w-duration_tol": "help.drama_duration_tol",
    "w-vprofile": "help.vprofile", "w-duration": "help.duration",
    "w-bg-src": "help.bg_src", "w-bg-link": "help.bg_link", "w-bg-dir": "help.bg_dir",
    "w-bg-int": "help.bg_int", "w-bg-motion": "help.bg_motion", "w-bg-cont": "help.bg_cont",
    "w-bg-ai-vmodel": "help.ai_model", "w-bg-ai-pmodel": "help.ai_model",
    "w-fg-on": "help.fg_on", "w-fg-src": "help.fg_src", "w-fg-width": "help.fg_width",
    "w-fg-pos": "help.fg_pos", "w-fg-ai-vmodel": "help.ai_model", "w-fg-ai-pmodel": "help.ai_model",
    "w-ad-src": "help.ad_src", "w-ad-mode": "help.ad_mode",
    "w-push": "help.push", "w-count": "help.count", "w-subs": "help.subs",
    "drama-scenario": "help.drama_scenario", "drama-prompt": "help.drama_prompt",
    "e-characters-name": "help.char_name", "e-characters-age": "help.char_age",
    "e-characters-appearance": "help.char_appearance",
    "char-prompt": "help.char_prompt", "char-photo-path": "help.char_photo",
    "orch-profile": "help.orch_profile",
    "e-orch-model": "help.orch_model", "e-orch-key_mode": "help.orch_key_mode",
    "e-orch-key": "help.orch_key", "e-orch-metric": "help.orch_metric",
    "e-orch-amount": "help.orch_amount",
}

BG_SOURCES = ["stock_video", "stock_photo", "local_video", "local_photo", "ai_video", "ai_photo"]
FG_SOURCES = ["stock_photo", "stock_video", "local_photo", "local_video", "ai_photo", "ai_video"]
AI_VIDEO_MODELS = [(m, m) for m in VIDEO_MODELS]  # (label, value) for the picker
AI_PHOTO_MODELS = [(m, m) for m in PHOTO_MODELS]
ORCH_MODEL_OPTS = [(m, m) for m in list(VIDEO_MODELS) + list(PHOTO_MODELS)]  # orchestration stages
ORCH_FIELDS = ("model", "key_mode", "key", "metric", "amount")


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
        "bg-link": prof.background.linkage,
        "bg-dir": str(prof.background.assets_dir),
        "bg-ai-vmodel": bg_ai if bg_ai in VIDEO_MODELS else "auto",
        "bg-ai-pmodel": bg_ai if bg_ai in PHOTO_MODELS else "flux",
        "bg-int": prof.background.interval_s,
        "bg-motion": prof.background.motion,
        "bg-cont": prof.background.continuous,
        "fg-on": prof.foreground.enabled,
        "fg-src": prof.foreground.source,
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
                   options=[(f"{n} — {c.description}", n) for n, c in store.content_types.items()]),
            Text("idea", "idea", placeholder="idea_ph"),
            Range("profanity", "profanity", value=store.global_cfg.defaults.profanity,
                  lo=0, hi=100, step=5, labels=PROFANITY_LABELS),
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
            Group("bg-ai-vid", [
                Choice("bg-ai-vmodel", "ai_model", options=AI_VIDEO_MODELS, value="auto"),
            ], visible_when=lambda v: v["bg-src"] == "ai_video"),
            Group("bg-ai-img", [
                Choice("bg-ai-pmodel", "ai_model", options=AI_PHOTO_MODELS, value="flux"),
            ], visible_when=lambda v: v["bg-src"] == "ai_photo"),
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
        ])

    def _nav_buttons(self, step: int):
        t = lambda k: _label(self.app, k)  # noqa: E731
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
            yield Button(t("start"), id="w-start", variant="success")

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
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

    def _step_help_key(self, step_key: str) -> str:
        """i18n key for a step's blurb; DramaScreen overrides where a step differs."""
        return f"help.{step_key}"

    def _inspector_help(self, step_key: str):
        """Default right-panel content: description of the focused setting (top) and
        the keyboard controls (bottom)."""
        t = lambda k: _label(self.app, k)  # noqa: E731
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
        text = _label(self.app, key) if key else _label(self.app, self._step_help_key(step))
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
    def _fg_on(self, event: Switch.Changed) -> None:
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
                ai_model=self._ai_model(v, bg_src, "bg"),
                interval_s=v["bg-int"],
                motion=v["bg-motion"] or "subtle",
                continuous=v["bg-cont"],
            ),
            foreground=VisualsForeground(
                enabled=v["fg-on"],
                source=fg_src,
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
            "duration": v["duration"],
            "ad_src": a["ad-src"],
            "ad_mode": a["ad-mode"] or "both",
            "push": "" if p["push"] == NONE else p["push"],
            "subs": p["subs"],
            "count": max(1, int(p["count"])),
        }

    def _command(self, g: dict, vis_name: str, vis_manual: VisualsConfig | None) -> str:
        cmd = f"slopgen info {g['lang']} {g['ctype']}"
        if g["idea"]:
            cmd += f' --idea "{g["idea"]}"'
        cmd += f" --visuals {vis_name} --duration {g['duration']:.0f}"
        if g["profanity"]:
            cmd += f" --profanity {g['profanity']}"
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
        if manual_notes:
            cmd += f"  # + {', '.join(manual_notes)} (TUI only)"
        return cmd

    def _render_summary(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        g = self._gather()
        vis_name, vis_manual = self._visuals_selection()
        ad_label = {NONE: t("ad_none"), MANUAL: t("ad_manual")}.get(g["ad_src"], g["ad_src"])
        vis_label = vis_name + (" *" if vis_manual else "")
        lines = [
            f"[b]{t('summary_head')}[/b]",
            "",
            f"  {t('lang')}: [b]{g['lang']}[/b]  {t('voice')}: [b]{g['voice']}[/b]      {t('ctype')}: [b]{g['ctype']}[/b]",
            f"  {t('idea')}: {g['idea'] or '—'}",
            f"  {t('profanity')}: [b]{g['profanity']}%[/b]",
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

    @on(Button.Pressed, "#w-start")
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
            )
        except ConfigError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self.app.push_screen(ProgressScreen(params))


CHAR_FIELD_KEYS = ("name", "age", "appearance")


def _write_character(store: ConfigStore, name: str, vals: dict) -> Path:
    """Persist a character to configs/characters/<name>.toml. Preserves any
    previously compiled prompts but marks it dirty (structured fields changed)."""
    existing = store.characters.get(name)
    # the file name IS the identity — the loader fills `name` from the stem, so we
    # don't duplicate it inside the file (avoids filename/inner-name divergence).
    data = {
        "age": vals.get("age", ""),
        "appearance": vals.get("appearance", ""),
        "visual_prompt": existing.visual_prompt if existing else "",
        "dirty": True,
    }
    path = Path("configs/characters") / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    return path


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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cast: list[dict] = []  # each: fields + glob + ai(set of ai-filled keys)
        self._sel: int | None = None  # index of the member open in the inspector
        self._cast_form: Form | None = None
        self._rev = 0  # bumps each list rebuild so item ids stay unique across async clear()
        self._scenario_ai_val: str | None = None  # AI-set plot, for un-tint on manual edit
        self._think_timers: dict = {}  # prompt-field id -> animation Timer while the AI works
        self._think_orig: dict = {}  # prompt-field id -> its original placeholder
        # video orchestration (the drama Visuals step): ordered generator stages
        self._stages: list[dict] = []
        self._stage_sel: int | None = None
        self._stage_form: Form | None = None
        self._orch_rev = 0

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
            Number("duration_min", "drama_duration_min", value="2", default=2.0),
            Number("duration_tol", "drama_duration_tol", value="15", default=15.0),
        ])

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
        return [{"model": "wan2.1", "key_mode": "rotate", "key": "", "metric": "percent", "amount": 100.0}]

    @staticmethod
    def _new_stage() -> dict:
        return {"model": "wan2.1", "key_mode": "rotate", "key": "", "metric": "percent", "amount": 50.0}

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
            item = ListItem(
                Horizontal(
                    Vertical(
                        Static(f"{i + 1}. {s['model']}", classes="cast-name"),
                        Static(km, classes="cast-line cast-dim"),
                        Static(f"→ {s['amount']:g} {metric}", classes="cast-line"),
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
        ])

    async def _show_stage_editor(self, idx: int) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
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
        try:
            s["amount"] = float(vals.get("amount", s["amount"]))
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
        data = {"stages": [{k: s[k] for k in ORCH_FIELDS} for s in self._stages]}
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
        yield Static(t("drama_plot_head"), classes="group-head")
        yield from Text("scenario", "", large=True).build("drama", t)  # id: drama-scenario
        yield from Text("prompt", "", placeholder="drama_prompt_ph").build("drama", t)
        with Horizontal(classes="entity-actions"):
            yield Button(t("char_autofill_all"), id="cast-fill-all", variant="primary")
        yield Static(t("drama_cast_head"), classes="group-head")
        with Horizontal(classes="entity-actions"):
            yield Button(t("drama_add"), id="cast-add", variant="success")
        yield ListView(id="cast-list")
        yield Static(t("drama_cast_hint2"), classes="hint")

    def _member_status(self, m: dict) -> tuple[str, str]:
        """(status label key, css class) for a cast item: local / global / global*."""
        if not m.get("glob"):
            return ("cast_st_local", "st-local")
        fields = {k: m.get(k, "") for k in CHAR_FIELD_KEYS}
        if m.get("saved") != fields:  # edited since it was pulled from / saved to the library
            return ("cast_st_global_dirty", "st-dirty")
        return ("cast_st_global", "st-global")

    def _refresh_cast_list(self) -> None:
        try:
            lv = self.query_one("#cast-list", ListView)
        except Exception:
            return
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

    # -- inspector: picker + editor -----------------------------------------
    async def _show_picker(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
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
        t = lambda k: _label(self.app, k)  # noqa: E731
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
        member = {"name": c.name, "age": c.age, "appearance": c.appearance, "glob": True, "ai": {}}
        member["saved"] = {k: member[k] for k in CHAR_FIELD_KEYS}  # snapshot from the library
        self._cast.append(member)
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
        if not self._cast and not scenario.strip():
            self.notify(_label(self.app, "cast_empty"), severity="warning")
            return
        prompt = ""
        try:
            prompt = self.query_one("#drama-prompt", TextArea).text.strip()
        except Exception:
            pass
        lang = self.app.store.global_cfg.ui.lang
        cast_copy = [dict(m) for m in self._cast]
        self._start_thinking("drama-prompt")
        self.run_worker(
            lambda: self._all_worker(cast_copy, lang, scenario, prompt), thread=True, exclusive=False)

    def _all_worker(self, cast: list[dict], lang: str, scenario: str, prompt: str) -> None:
        try:
            res = char_ai.autofill_all(self._llm(), cast, lang, scenario, prompt)
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
        msg = "char_filled" if (by_idx or scen) else "char_nothing"
        self._apply_changes(by_idx, msg, scen)

    def _apply_changes(self, by_idx: dict[int, dict], msg_key: str, scenario_new: str | None = None) -> None:
        """Merge AI changes into members / the plot, tint them, refresh the editor."""
        for idx, changed in by_idx.items():
            if idx >= len(self._cast):
                continue
            m = self._cast[idx]
            m.update(changed)
            m["ai"].update({k: changed[k] for k in changed if k in char_ai.FILLABLE})
        if scenario_new:  # only returned when the prompt asked to rewrite the plot
            self._scenario_ai_val = scenario_new
            try:
                ta = self.query_one("#drama-scenario")
                ta.text = scenario_new
                ta.add_class("ai-filled")
            except Exception:
                pass
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
        dur_min = float(c.get("duration_min") or 2.0)
        return {
            "lang": c["lang"], "voice": c["voice"], "ctype": "", "idea": "",
            "profanity": c["profanity"],
            "duration": dur_min * 60.0,
            "duration_tol": float(c.get("duration_tol") or 15.0),
            "ad_src": a["ad-src"], "ad_mode": a["ad-mode"] or "both",
            "push": "" if p["push"] == NONE else p["push"],
            "subs": p["subs"], "count": max(1, int(p["count"])),
        }

    def _render_summary(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        self._save_editor()
        self._save_stage_editor()
        g = self._gather()
        cast = ", ".join(self._selected_cast()) or "—"
        glob = ", ".join(m["name"] for m in self._cast if m["glob"]) or "—"
        plot = self._scenario_text().strip().replace("\n", " ")
        plot = (plot[:80] + "…") if len(plot) > 80 else (plot or "—")
        stages = " → ".join(s["model"] for s in self._stages) or "—"
        lines = [
            f"[b]{t('drama_summary_head')}[/b]",
            "",
            f"  {t('lang')}: [b]{g['lang']}[/b]      {t('duration')}: "
            f"~{g['duration'] / 60:.1f} min ±{g['duration_tol']:.0f}s",
            f"  {t('drama_plot_head')}: {plot}",
            f"  {t('drama_cast_head')}: [b]{cast}[/b]",
            f"  ★ {t('cfg.characters')}: {glob}",
            f"  {t('orch_head')}: [b]{stages}[/b]",
            "",
            f"  [dim]{t('drama_soon_note')}[/dim]",
        ]
        self.query_one("#w-summary", Static).update("\n".join(lines))
        self.query_one("#w-cmd", Static).update(f"$ {self._drama_command(g)}")

    def _drama_command(self, g: dict) -> str:
        cmd = f"slopgen drama {g['lang']}"
        cmd += f" --duration-min {g['duration'] / 60:g} --tol {g['duration_tol']:g}"
        glob = [m["name"] for m in self._cast if m["glob"]]
        if glob:
            cmd += f" --cast {','.join(glob)}"
        if g["ad_src"] not in (NONE, MANUAL):
            cmd += f" --ad {g['ad_src']} --ad-mode {g['ad_mode']}"
        if g["profanity"]:
            cmd += f" --profanity {g['profanity']}"
        if g["push"]:
            cmd += f" --push {g['push']}"
        if g["count"] != 1:
            cmd += f" -n {g['count']}"
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
                )
                for s in self._stages
            ],
        )
        try:
            params = RunParams(
                lang=g["lang"], content_type="", mode="drama",
                scenario=self._scenario_text().strip(),
                manual_cast=cast,
                manual_orchestration=orch,
                duration_s=g["duration"], duration_tol_s=g["duration_tol"],
                profanity=g["profanity"],
                ad=g["ad_src"] if g["ad_src"] not in (NONE, MANUAL) else "",
                manual_ad=self._manual_ad_config(g["ad_src"]),
                ad_mode=g["ad_mode"],
                push=g["push"], count=g["count"],
                voice_override=g["voice"], subtitle_style=g["subs"],
            )
        except Exception as e:  # pydantic validation / bad field
            self.notify(str(e), severity="error", timeout=8)
            return
        self.notify(_label(self.app, "drama_soon").format(n=len(cast)), timeout=4)
        self.app.push_screen(ProgressScreen(params))


class ModeSelectScreen(Screen):
    """Pick what to generate after pressing GENERATE: the existing minute-of-info
    clip, or an AI drama (each opens its own settings wizard)."""

    def compose(self) -> ComposeResult:
        t = lambda k: _label(self.app, k)  # noqa: E731
        yield TopBar(t("menu.generate"))
        with Center(id="home-center"):
            with Vertical(id="home-inner"):
                yield Static(t("mode_head"), id="logo-sub")
                with Vertical(id="home-menu"):
                    yield Button(t("mode_info"), id="mode-info", variant="success")
                    yield Static(t("mode_info_desc"), classes="hint")
                    yield Button(t("mode_drama"), id="mode-drama", variant="primary")
                    yield Static(t("mode_drama_desc"), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#mode-info", Button).focus()

    @on(Button.Pressed, "#mode-info")
    def _info(self) -> None:
        self.app.push_screen(GenerateScreen())

    @on(Button.Pressed, "#mode-drama")
    def _drama(self) -> None:
        self.app.push_screen(DramaScreen())


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class ProgressScreen(Screen):
    def __init__(self, params: RunParams):
        super().__init__()
        self.params = params

    def compose(self) -> ComposeResult:
        yield TopBar(_label(self.app, "step.summary"))
        yield Static("", id="run-summary")
        yield DataTable(id="queue")
        yield RichLog(id="log", wrap=True, highlight=True)

    def on_mount(self) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        p = self.params
        ad = f"{(p.manual_ad.name if p.manual_ad else p.ad) or '—'} ({p.ad_mode})"
        push = f"push: {p.push or t('run.local')} · {t('run.subs')}: {p.subtitle_style}"
        if p.mode == "drama":
            head = f" {p.count}× 🎭 {p.lang} · ~{p.duration_s / 60:.1f} min ±{p.duration_tol_s:.0f}s · ad: "
        else:
            vis = (p.manual_visuals.name + "*") if p.manual_visuals else p.visuals
            head = f" {p.count}× {p.lang}/{p.content_type} · {t('run.vis')}: {vis} ~{p.duration_s:.0f}s · ad: "
        self.query_one("#run-summary", Static).update(f"{head}{ad} · {push}")
        table = self.query_one("#queue", DataTable)
        table.add_columns(t("col.video"), t("col.stage"), t("col.status"), t("col.info"))
        for i in range(p.count):
            table.add_row(f"#{i}", t("row.queued"), "…", "")
        self.run_worker(self._run_pipeline, thread=True, exclusive=True)

    def _run_pipeline(self) -> None:
        try:
            ctx = AppContext(store=self.app.store, params=self.params)
        except Exception as e:
            self.app.call_from_thread(self._log, f"[red]{_label(self.app, 'err.startup')}: {e}")
            return
        jobs = Orchestrator(ctx, on_event=self._on_event_threadsafe).run()
        done = [j for j in jobs if j.published]
        self.app.call_from_thread(self._finish, done, len(jobs))

    def _on_event_threadsafe(self, i: int, stage: str, status: str, message: str) -> None:
        self.app.call_from_thread(self._on_event, i, stage, status, message)

    def _on_event(self, i: int, stage: str, status: str, message: str) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        table = self.query_one("#queue", DataTable)
        icons = {"start": "⏳", "done": "✔", "error": "✘", "skip": "↷"}
        table.update_cell_at(Coordinate(i, 1), stage)
        table.update_cell_at(Coordinate(i, 2), icons.get(status, "·"))
        table.update_cell_at(Coordinate(i, 3), message[:60])
        if status == "error":
            self._log(f"[red]{t('col.video')} {i} — {stage}:[/red]\n{message}")
        elif status == "done" and message:
            self._log(f"{t('col.video')} {i} · {stage} ✔ {message}")

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def _finish(self, done: list, total: int) -> None:
        t = lambda k: _label(self.app, k)  # noqa: E731
        self._log(f"[bold green]{t('run.finished')}: {len(done)}/{total}[/bold green]")
        for j in done:
            self._log(f"  → {j.published}")
        self.notify(f"{t('run.finished')}: {len(done)}/{total}", timeout=10)


# --------------------------------------------------------------------------
# Configuration: vertical sections on the left; entity sections get a top
# tab-button row, one tab per existing config file plus "+ new"
# --------------------------------------------------------------------------

CFG_SECTIONS = ["cfg.llm", "cfg.footage", "cfg.characters", "cfg.ads", "cfg.accounts", "cfg.presets"]

def _entity_form(kind: str) -> Form:
    """Declarative field list for one config-entity kind (ns keeps the e-{kind}-{key} ids)."""
    fields = {
        "characters": [
            Text("name", "f.name"),
            Text("age", "f.age"),
            Text("appearance", "f.appearance", large=True),
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
        self._form.fill(self, _entity_values(self.app.store, self.kind, name))

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
                yield EntityPane("ads", id="cpane-3", classes="pane")
                yield EntityPane("accounts", id="cpane-4", classes="pane")
                yield EntityPane("presets", id="cpane-5", classes="pane")

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
    #cast-list { height: 1fr; min-height: 8; margin-top: 1; border: round $secondary; }
    /* cast rows: bordered 3-line cards, name/age/look left, status right at a fixed column */
    .cast-item { height: auto; padding: 0; margin: 0 1 1 1; border: round $secondary; }
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
    #queue { height: 40%; margin: 1 2; }
    #log { height: 1fr; margin: 0 2 1 2; border: round $primary; }
    """

    def __init__(self, store: ConfigStore | None = None):
        super().__init__()
        load_dotenv()
        self.store = store or ConfigStore()
        self.ui_lang = self.store.global_cfg.ui.lang
        self._theme_ready = False

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
