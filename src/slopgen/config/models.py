"""Pydantic models for every TOML config kind and for resolved run parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

AdMode = Literal["overlay", "native", "both"]
SubtitleStyle = Literal["word_pop", "phrases", "karaoke"]


# --- configs/slopgen.toml -------------------------------------------------


class PathsConfig(BaseModel):
    assets: Path = Path("assets")
    output: Path = Path("output")
    state: Path = Path("state")


class VideoConfig(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    target_duration_s: float = 45.0  # default script length target (informational, not a hard cap)


class SubtitlesConfig(BaseModel):
    style: SubtitleStyle = "word_pop"
    font: str = "DejaVu Sans"
    font_size: int = 110
    # ASS colors are &HAABBGGRR
    primary_color: str = "&H00FFFFFF"
    accent_color: str = "&H0000D7FF"
    outline: int = 8


class AudioConfig(BaseModel):
    music_volume: float = 0.12


class LLMConfig(BaseModel):
    """[llm] in slopgen.toml. `profile` picks a configs/llm/*.toml profile;
    the inline fields remain as a legacy fallback when no profiles exist."""

    profile: str = ""
    # legacy inline settings (deepseek | gemini | openrouter | custom)
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    key_env: str = ""
    temperature: float = 1.2
    web_search: bool = False


class LLMProfile(BaseModel):
    """configs/llm/*.toml — a named LLM connection. Empty base_url/model/key_env
    fall back to provider defaults (llm.client.PROVIDERS). API keys are NOT
    stored here — they live in .env under `key_env`."""

    name: str
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    key_env: str = ""
    temperature: float = 1.2
    # give the model live web access while writing the script (OpenRouter web
    # plugin). Grounds the narration in real, current facts. OpenRouter only —
    # other providers silently ignore it.
    web_search: bool = False


class UIConfig(BaseModel):
    lang: Literal["en", "ru"] = "en"  # TUI interface language
    theme: str = "minecraft"  # persisted Textual theme name


class FootageConfig(BaseModel):
    # order matters: first provider to return an asset wins. Besides stock
    # (pexels/pixabay) and `local`, two free AI generators can be added:
    #   pollinations — text-to-image, no key (photo sources / find_image)
    #   wan          — text-to-video via HF Spaces, slow (video sources / find_clip)
    providers: list[str] = ["pexels", "pixabay", "local"]
    # AI-generation knobs (used only when pollinations/wan are in `providers`)
    pollinations_model: str = "flux"  # pollinations.ai model: flux | turbo | …
    # portrait text-to-video HF Spaces tried in order for `wan`; first that works
    # wins. Override when a Space goes offline. Empty = built-in reserve chain.
    video_gen_spaces: list[str] = []
    gen_style_suffix: str = ""  # appended to every generated prompt (e.g. "cinematic")


class DefaultsConfig(BaseModel):
    count: int = 1
    ad_mode: AdMode = "both"
    profanity: int = 0  # 0 = clean … 100 = constant swearing


class GlobalConfig(BaseModel):
    paths: PathsConfig = PathsConfig()
    video: VideoConfig = VideoConfig()
    subtitles: SubtitlesConfig = SubtitlesConfig()
    audio: AudioConfig = AudioConfig()
    llm: LLMConfig = LLMConfig()
    ui: UIConfig = UIConfig()
    footage: FootageConfig = FootageConfig()
    defaults: DefaultsConfig = DefaultsConfig()


# --- configs/content/*.toml -----------------------------------------------


class ContentTypeConfig(BaseModel):
    name: str
    description: str = ""
    # per-language creative briefs injected into the JSON-schema prompts
    idea_brief: dict[str, str]  # lang -> text
    script_brief: dict[str, str]  # lang -> text
    voices: dict[str, str]  # lang -> edge-tts voice name
    # stock search fallbacks when scene keywords return nothing (English only,
    # stock APIs are English-indexed)
    fallback_keywords: list[str] = []


# --- configs/visuals/*.toml -----------------------------------------------


BgSource = Literal["stock_video", "stock_photo", "local_video", "local_photo", "ai_video", "ai_photo"]
FgSource = Literal["stock_photo", "stock_video", "local_photo", "local_video", "ai_photo", "ai_video"]
Motion = Literal["none", "subtle", "strong"]


class VisualsBackground(BaseModel):
    source: BgSource = "stock_video"
    linkage: Literal["narration", "neutral"] = "narration"
    assets_dir: Path = Path("assets/footage")  # for local_* sources
    # which AI generator to use for ai_video/ai_photo sources (name from
    # generate.VIDEO_MODELS / PHOTO_MODELS); empty = provider/config default
    ai_model: str = ""
    interval_s: float = 3.5  # photo change cadence (photo sources only)
    motion: Motion = "subtle"  # Ken Burns strength (photo sources only)
    # ONE long clip playing straight through the whole video instead of a fresh
    # clip (re-)starting every scene. Meant for gameplay loops behind narration:
    # each scene reads the NEXT slice of the same clip, so the action is
    # continuous. Video sources only (stock_video / local_video); ignored otherwise.
    continuous: bool = False


class VisualsForeground(BaseModel):
    enabled: bool = False
    source: FgSource = "stock_photo"
    assets_dir: Path = Path("assets/images")  # for local_photo/local_video
    ai_model: str = ""  # AI generator for ai_photo/ai_video inserts; empty = default
    # Inserts are NOT placed on a fixed cadence — the LLM decides which spoken
    # phrases deserve a picture, and each insert shows exactly while that phrase
    # is spoken (timing derived from edge-tts word timings) and disappears after.
    width_pct: int = 78
    position: Literal["center", "top", "bottom"] = "center"


def _wants_query(source: str) -> bool:
    """Sources whose asset is chosen/generated from a narration query — stock
    search and AI generation both benefit from the LLM's per-beat visual queries."""
    return source.startswith(("stock", "ai"))


class VisualsConfig(BaseModel):
    name: str
    description: str = ""
    background: VisualsBackground = VisualsBackground()
    foreground: VisualsForeground = VisualsForeground()

    @property
    def needs_narration_queries(self) -> bool:
        return (
            self.background.linkage == "narration"
            and _wants_query(self.background.source)
        ) or (self.foreground.enabled and _wants_query(self.foreground.source))


# --- configs/ads/*.toml ---------------------------------------------------


class AdOverlayConfig(BaseModel):
    assets_dir: Path
    text: str = ""
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    start_s: float = 6.0
    duration_s: float = 8.0
    width: int = 340


class AdNativeConfig(BaseModel):
    assets_dir: Path
    talking_points: str


class AdDescriptionConfig(BaseModel):
    snippet: str = ""  # may contain {url}


class AdConfig(BaseModel):
    name: str
    url: str = ""
    modes: list[str] = ["overlay", "native"]
    overlay: AdOverlayConfig | None = None
    native: AdNativeConfig | None = None
    description: AdDescriptionConfig = AdDescriptionConfig()


# --- configs/accounts/*.toml ----------------------------------------------


class YouTubeAccountConfig(BaseModel):
    client_secret: Path = Path("secrets/client_secret.json")
    token: Path = Path("secrets/token.json")
    privacy: Literal["public", "unlisted", "private"] = "public"
    category_id: str = "24"  # Entertainment


class AccountDefaults(BaseModel):
    lang: str = ""
    content_type: str = ""
    ad: str = ""
    ad_mode: AdMode | None = None
    visuals: str = ""
    duration_s: float | None = None
    profanity: int | None = None


class AccountConfig(BaseModel):
    name: str
    platform: Literal["youtube", "tiktok", "local"]
    youtube: YouTubeAccountConfig | None = None
    defaults: AccountDefaults = AccountDefaults()


# --- configs/presets/*.toml -----------------------------------------------


class PresetConfig(BaseModel):
    name: str
    lang: str = ""
    content_type: str = ""
    ad: str = ""
    ad_mode: AdMode | None = None
    visuals: str = ""
    duration_s: float | None = None
    profanity: int | None = None
    push: str = ""  # account name; empty = save locally
    count: int | None = None


# --- configs/characters/*.toml --------------------------------------------


class CharacterConfig(BaseModel):
    """A reusable cast member for the AI-drama mode. All descriptive fields are
    optional and may be left for the LLM to invent. The two `*_compiled` fields
    are LLM-optimized English descriptors (NOT literal translations) rebuilt
    lazily from the structured fields whenever `dirty` is set — kept separate so
    generation injects the ready prompt without paying tokens on every edit."""

    name: str
    age: str = ""  # free text ("17", "late 20s"); folded into the visual prompt
    appearance: str = ""  # looks: hair, eyes, build, clothing → every image/video prompt
    # LLM-compiled, generation-ready English (rebuilt when dirty, see above)
    visual_prompt: str = ""  # token-dense txt2img/txt2vid descriptor (appearance + age)
    dirty: bool = True  # structured fields changed since last compile


# --- configs/orchestration/*.toml -----------------------------------------


OrchMetric = Literal["clips", "seconds", "percent"]


class OrchestrationStage(BaseModel):
    """One AI generator in the drama's video pipeline. The pipeline walks the
    stages in order, each producing up to `amount` of the video (measured in
    `metric`), then hands off to the next stage. `key_mode` decides what happens
    when a key hits its provider limit before the stage is done: `rotate` = switch
    to the next key and keep going; `single` = use the pinned `key` only and, on
    its limit, skip the rest of this stage and move to the next."""

    model: str = "wan2.1"  # a generate.VIDEO_MODELS / PHOTO_MODELS name
    key_mode: Literal["rotate", "single"] = "rotate"
    key: str = ""  # key_mode="single": which key label to pin; empty = the first available
    metric: OrchMetric = "percent"  # unit of `amount`
    amount: float = 100.0  # produce up to this much on this stage before moving on


class OrchestrationConfig(BaseModel):
    """A reusable, ordered AI-generator pipeline for AI-drama video."""

    name: str
    stages: list[OrchestrationStage] = []


# --- resolved parameters of a single run ----------------------------------


Mode = Literal["info", "drama"]


class RunParams(BaseModel):
    """Everything the orchestrator needs, after CLI/preset/account/global merge."""

    lang: str
    content_type: str
    # what to generate: "info" = the minute-of-info clip; "drama" = the AI web
    # drama (a narrated story with a recurring cast + AI-generated shots). The
    # mode selects the stage chain in the orchestrator.
    mode: Mode = "info"
    idea: str = ""  # user-provided topic; empty = the LLM invents one
    visuals: str = "classic"  # visuals profile name from configs/visuals/
    manual_visuals: VisualsConfig | None = None  # ad-hoc profile from TUI overrides
    duration_s: float = 45.0  # target spoken length (informational for the LLM)
    # drama only: the model may run the finished video over/under `duration_s` by
    # up to this many seconds when the story calls for it (0 = aim exactly).
    duration_tol_s: float = 0.0
    profanity: int = 0  # 0 = clean … 100 = constant swearing
    ad: str = ""  # ad config name, empty = no ads
    manual_ad: AdConfig | None = None  # ad-hoc contract built in the TUI wizard
    ad_mode: AdMode = "both"
    push: str = ""  # account name, empty = local save only
    count: int = 1
    out: Path | None = None  # output dir override
    dry_run: bool = False  # skip the publish stage
    keep_temp: bool = False
    subtitle_style: SubtitleStyle | None = None  # override global default
    voice_override: str = ""  # edge-tts voice id; empty = use content config default
    tts_rate: int = 0  # speech rate offset in percent (-50 = half speed, +50 = 50% faster)
    # -- drama mode --------------------------------------------------------
    scenario: str = ""  # the drama's premise/plot; empty = the LLM invents one
    parts: int = 1  # drama only: split one drama into this many cliffhanger parts
    manual_cast: list[CharacterConfig] = []  # resolved cast for the run (TUI/CLI)
    orchestration: str = ""  # orchestration profile name from configs/orchestration/
    manual_orchestration: OrchestrationConfig | None = None  # ad-hoc chain from the TUI
