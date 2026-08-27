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
    # downloaded neural weights (see slopgen.models). Gitignored like the other
    # three: a single local voice is 2.3 GiB, so nothing here is ever committed.
    models: Path = Path("models")


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


class AzureTTSConfig(BaseModel):
    """[tts.azure]. Azure Speech is edge-tts' paid sibling and speaks the same word
    boundaries, so it is the one alternative that costs the pipeline nothing
    architecturally — the whole 700-voice catalogue, with timings, no aligner.

    `region` is not cosmetic: the Dragon HD Omni voices are a preview and simply do
    not appear in the catalogue outside the regions that host it, so a wrong region
    reads as "that voice does not exist"."""

    region: str = "eastus"
    key_env: str = "AZURE_SPEECH_KEY"
    region_env: str = "AZURE_SPEECH_REGION"  # overrides `region` when set in .env
    # Dragon HD Omni knobs; they do nothing on the classic neural voices
    temperature: float = 1.0
    enhance_pronunciation: bool = True


class QwenTTSConfig(BaseModel):
    """[tts.qwen] — Alibaba's DashScope. The international endpoint is the default
    because the mainland one refuses keys minted outside it, which surfaces as an
    unhelpful 401."""

    key_env: str = "DASHSCOPE_API_KEY"
    base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    model: str = "qwen3-tts-flash"


class QwenLocalConfig(BaseModel):
    """[tts.qwen_local] — the same voice on this machine, no key and no network.

    The two defaults below are measured, not conventional: bfloat16 with one thread
    per PHYSICAL core ran 3.3x faster than float32 with one per hardware thread
    (RTF 16.45 -> 5.05 on a Ryzen 7840U). Autoregressive decoding here is bound by
    memory bandwidth, which SMT does not add any of, so the second thread on a core
    only steals cache. `threads = 0` means "count the physical cores"."""

    model_id: str = "qwen3-tts-0.6b"  # an id in slopgen.models.registry
    dtype: str = "bfloat16"
    threads: int = 0
    # How much the model is allowed to improvise. Left at the value the weights ship
    # with, because lowering it was tried and measured and did NOT help: five takes of
    # one line at 0.9, 0.7 and 0.5 came out with the same median length once the dead
    # air was collapsed (1.58x, 1.57x, 1.57x), and the lowest setting produced the most
    # takes that ran all the way into the token cap. The instability is not entropy.
    temperature: float = 0.9


class AlignConfig(BaseModel):
    """[tts.align] — how word timings are recovered for engines that give none.

    A recognizer per language, from the model catalogue. This is NOT transcription:
    the words are already known, so the recognizer is only ever asked *when* each one
    was said, and its mistakes are corrected against the script (see tts/align.py)."""

    models: dict[str, str] = {"ru": "vosk-ru-small", "en": "vosk-en-small"}


class TTSConfig(BaseModel):
    """[tts] in slopgen.toml. `pronounce` is a per-language table of words the voice
    says wrong, mapped to a spelling that makes it say them right.

    It has to be an explicit list, because the failure cannot be detected by rule:
    edge-tts reads a Cyrillic acronym as a word whenever the letters happen to form
    a pronounceable syllable, and that is correct for «ВУЗ» and wrong for «НЛО» —
    two strings a regex cannot tell apart. Everything else its Russian normalizer
    already handles (measured: «Лада-2107» and «18-летие» both come out fully
    expanded), so this table stays short and is yours to extend.

    Separate the parts with SPACES. Hyphens do not work: measured in running speech,
    «эн-эл-о» takes 0.26s — exactly as long as the broken «НЛО» — because the
    normalizer collapses a hyphenated run back into one syllable, while the spaced
    «эн эл о» takes 0.62s. Which spaced form reads best is per-word (bare letters
    «Н Л О» run 1.10s here, but beat the phonetic names on other acronyms), so it is
    worth trying both. The voice returns several word boundaries where the script had
    one word; stages/tts.py merges them back so the subtitles show the original
    spelling with exact timings.

    `engine` picks who speaks (see slopgen.tts.ENGINES). The table above is engine-
    independent by design — it fixes what a synthesizer does with a spelling, and
    every synthesizer here is fed the same respelled text."""

    engine: str = "edge"
    # A cloning run listens to its own reference sample before it voices anything
    # (`tts.refs.check_transcript`), and when the sample scores badly it voices ONE
    # short line to find out whether that matters, refusing only if the model will
    # not say it (`stages/tts._probe_voice`). The measurement alone is not grounds to
    # refuse — a recognizer that cannot make out a child or a whisper scores a
    # perfectly good sample badly — so the probe, not the score, is the gate. Turning
    # this off skips both, and the first sign of trouble then arrives an hour into a
    # render.
    check_reference: bool = True
    pronounce: dict[str, dict[str, str]] = {}  # lang -> {as written: as spoken}
    azure: AzureTTSConfig = AzureTTSConfig()
    qwen: QwenTTSConfig = QwenTTSConfig()
    qwen_local: QwenLocalConfig = QwenLocalConfig()
    align: AlignConfig = AlignConfig()


class GlobalConfig(BaseModel):
    paths: PathsConfig = PathsConfig()
    video: VideoConfig = VideoConfig()
    subtitles: SubtitlesConfig = SubtitlesConfig()
    audio: AudioConfig = AudioConfig()
    llm: LLMConfig = LLMConfig()
    ui: UIConfig = UIConfig()
    footage: FootageConfig = FootageConfig()
    defaults: DefaultsConfig = DefaultsConfig()
    tts: TTSConfig = TTSConfig()


# --- configs/content/*.toml -----------------------------------------------


class ContentTypeConfig(BaseModel):
    name: str
    description: str = ""
    # per-language creative briefs injected into the JSON-schema prompts
    idea_brief: dict[str, str]  # lang -> text
    script_brief: dict[str, str]  # lang -> text
    voices: dict[str, str]  # lang -> voice name, read by whichever engine speaks
    #   (a catalogue id like "ru-RU-SvetlanaNeural", or the name of a configs/voices/ clone)
    # stock search fallbacks when scene keywords return nothing (English only,
    # stock APIs are English-indexed)
    fallback_keywords: list[str] = []


# --- configs/visuals/*.toml -----------------------------------------------


BgSource = Literal["stock_video", "stock_photo", "local_video", "local_photo", "ai_video", "ai_photo"]
FgSource = Literal["stock_photo", "stock_video", "local_photo", "local_video", "ai_photo", "ai_video"]
Motion = Literal["none", "subtle", "strong"]


# Who actually fetches the material is a property ORTHOGONAL to what kind of material
# it is, not a source of its own. The operator can step in on either family, and what
# they do differs accordingly:
#
#   stock_video / stock_photo  + manual  ->  USER-ASSISTED SEARCH: slopgen writes what
#       to look for and hands over ready search queries; the operator finds the file.
#   ai_video / ai_photo        + manual  ->  USER-ASSISTED GENERATION: slopgen writes
#       the prompt; the operator makes the clip in an external web tool.
#
# Both land in the same manual manifest and the same gather screen (see
# pipeline/manual.py); only the instructions differ. `local_*` ignores the flag —
# those files are already on disk.


class VisualsBackground(BaseModel):
    source: BgSource = "stock_video"
    linkage: Literal["narration", "neutral"] = "narration"
    assets_dir: Path = Path("assets/footage")  # for local_* sources
    # the operator supplies this material by hand (see the note above)
    manual: bool = False
    # which AI generator to use for ai_video/ai_photo sources (name from
    # generate.VIDEO_MODELS / PHOTO_MODELS); empty = provider/config default.
    # Ignored when `manual` is set — there is no generator to name.
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
    manual: bool = False  # the operator supplies each insert by hand (see above)
    ai_model: str = ""  # AI generator for ai_photo/ai_video inserts; empty = default
    # Inserts are NOT placed on a fixed cadence — the LLM decides which spoken
    # phrases deserve a picture, and each insert shows exactly while that phrase
    # is spoken (timing derived from edge-tts word timings) and disappears after.
    width_pct: int = 78
    position: Literal["center", "top", "bottom"] = "center"


def manual_kind(source: str, manual: bool) -> str:
    """What the operator is being asked to do for this source: ``"search"`` (find
    existing stock material), ``"generate"`` (make it in an external tool), or ``""``
    when slopgen fetches it itself. See the note above VisualsBackground."""
    if not manual:
        return ""
    if source.startswith("stock"):
        return "search"
    if source.startswith("ai"):
        return "generate"
    return ""  # local_*: the files are already there


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


# What a character IS, in the fandom mode only. A drama casts actors, so each of its
# characters is one person; a world contains no such guarantee. Most of what appears
# on screen in a world is not an individual at all — it is the postmen of the winter
# carry, the wardens, the elves, the machines: many bodies wearing one look, and the
# look is the whole of what needs writing down. Squeezing those into one-person
# entries costs twice — the operator writes "Warden #1" through "Warden #4" to say
# a thing that has no number, and the writer, handed four individuals, dutifully
# distinguishes between them.
#
#   one   — a specific, single character: this face, in every shot it appears in.
#   many  — a group of visually identical (or near-identical) faceless figures; the
#           look belongs to every one of them and to none of them in particular.
#   class — a whole kind: a race, a species, a caste, a model of machine. Broader
#           than `many` in that its members vary in incidentals but share a type.
Plurality = Literal["one", "many", "class"]


class CharacterConfig(BaseModel):
    """A reusable cast member. All descriptive fields are optional and may be left
    for the LLM to invent. `visual_prompt` is an LLM-optimized English descriptor
    (NOT a literal translation) rebuilt lazily from the structured fields whenever
    `dirty` is set — kept separate so generation injects the ready prompt without
    paying tokens on every edit.

    The two modes fill this in differently, and only the fandom mode uses
    `plurality` (see above). A drama's character is a person the drama invents, so
    it carries `age` and its personality is the drama's business. A WORLD's
    character is only ever a LOOK: what exists in that world and how it is
    recognised on screen. Who they are, what they want and how they behave is
    written in the world's lore documents — in prose, where it belongs — or is not
    written anywhere, which is also fine: nothing in the pipeline reads a
    personality off a character, it reads a picture."""

    name: str
    # drama only — free text ("17", "late 20s"), folded into the compiled prompt. A
    # world's character never has one: the loader strips it, the fandom editor has no
    # field for it, and age that shows on a thing shows in its looks.
    age: str = ""
    appearance: str = ""  # looks: hair, eyes, build, clothing → every image/video prompt
    plurality: Plurality = "one"  # fandom only: one / many alike / a whole class
    # LLM-compiled, generation-ready English (rebuilt when dirty, see above)
    visual_prompt: str = ""  # token-dense txt2img/txt2vid descriptor (appearance + age)
    dirty: bool = True  # structured fields changed since last compile


# --- configs/voices/*.toml ------------------------------------------------


class VoiceConfig(BaseModel):
    """A cloned voice: a sample of somebody speaking, plus what they say in it.

    Cloning here is zero-shot — there is no training step and no profile living
    inside a model, just a (sample, transcript) pair handed to the synthesizer with
    every line. That is why this is a config and not an artifact: the card IS the
    voice, it is portable, and the same card works on any engine that clones.

    `text` is typed by a human on purpose. Lifting it off the sample with a
    recognizer was tried and the errors do not stay put — the model reconciles a
    wrong transcript with the audio by drifting, and in the worst measured case
    spoke words from the SAMPLE in the middle of the synthesized line. Ten seconds
    of typing buys the whole voice.

    The sample lives next to the card (`ref` is relative to configs/voices/) because
    the two are worthless apart. `slopgen voices add` writes both and refuses the
    samples known to break cloning — clipped, too short, too noisy."""

    name: str
    ref: str = ""  # sample filename, relative to the card's own folder
    text: str = ""  # what is said in the sample, exactly, typed by hand
    # cloud engines enrol a voice from a URL and cannot be handed a local file; fill
    # this in only if you want THIS card to work in the cloud too (see tts/qwen_api)
    ref_url: str = ""
    lang: str = "ru"
    description: str = ""
    root: Path | None = Field(default=None, exclude=True)  # set by the loader

    @property
    def ref_path(self) -> Path | None:
        if not self.ref:
            return None
        return (self.root / self.ref) if self.root else Path(self.ref)


# --- configs/fandoms/<name>/ ----------------------------------------------


class FandomConfig(BaseModel):
    """A fictional world the fandom mode narrates from the INSIDE, and the folder
    that holds it: `configs/fandoms/<name>/` with `fandom.toml`, one or more lore
    documents in markdown, and the world's own cast under `characters/`.

    The split between those last two is the whole of what a world is made of: the
    documents hold everything that is TRUE of the world, the characters hold only
    what things LOOK like (see :class:`CharacterConfig`). A person's history, temper
    and motives are lore; their face is a character entry, and so is the shared face
    of the four hundred wardens nobody has ever named.

    `canon` is to the lore documents what `visual_prompt` is to a character (see
    :class:`CharacterConfig`): an LLM-compiled, generation-ready digest, built once
    and injected into every window of the script so the writer never pays tokens on
    the raw documents. Freshness is a CHECKSUM rather than the character's `dirty`
    flag, because lore is comfortably written in an outside markdown editor and
    nothing there would raise a flag — `docs_sha` not matching the documents on disk
    is what triggers a rebuild, however they were edited."""

    name: str
    # markdown files inside the fandom folder, in reading order. Empty = every *.md
    # in the folder, sorted by name.
    docs: list[str] = []
    tone: str = ""  # optional register/delivery note for the writer
    # offer the writer the `lore_lookup` tool (a librarian LLM that reads the whole
    # document and answers questions). Off = the canon sheet is all it ever sees.
    lore_tool: bool = True
    # -- LLM-compiled, rebuilt when `docs_sha` stops matching (see above) --
    canon: str = ""  # the canon sheet: rules, glossary, factions, timeline, taboos
    docs_sha: str = ""  # sha1 of the documents `canon` was compiled from
    # -- runtime only, filled by the loader; never written back to the TOML --
    root: Path | None = Field(default=None, exclude=True)  # the fandom's folder
    cast: list[CharacterConfig] = Field(default_factory=list, exclude=True)


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
    # average length of one clip from THIS generator, in seconds. 0 = the model's
    # nominal (generate.MODEL_CLIP_SECONDS). Overrides the run-level average — set it
    # when a stage's clips are longer than the rest (a hand-made Kling/Veo shot next
    # to 5-second Space clips, say).
    clip_seconds: float = 0.0


class OrchestrationConfig(BaseModel):
    """A reusable, ordered AI-generator pipeline for AI-drama video."""

    name: str
    stages: list[OrchestrationStage] = []


# --- resolved parameters of a single run ----------------------------------


Mode = Literal["info", "drama", "fandom"]
# fandom mode: WHO is telling it, both of them from inside the world.
#   resident   — a person who lives there, first person, the world as daily life
#   chronicler — a chronicler/researcher/theorist of that world, no "I" protagonist,
#                building theories out of its records as if they were real documents
FandomVoice = Literal["resident", "chronicler"]


class RunParams(BaseModel):
    """Everything the orchestrator needs, after CLI/preset/account/global merge."""

    lang: str
    content_type: str
    # what to generate: "info" = the minute-of-info clip; "drama" = the AI web
    # drama (a narrated story with a recurring cast + AI-generated shots);
    # "fandom" = the same shape as a drama, but set in a world the operator wrote
    # down, narrated from INSIDE it as fact. The mode selects the stage chain in
    # the orchestrator.
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
    # stage names (see pipeline.review) after which the run parks for operator
    # review instead of walking on. Each fires once per video.
    breakpoints: list[str] = []
    subtitle_style: SubtitleStyle | None = None  # override global default
    # swap profanity out of the BURNED-IN subtitles while the voice keeps every word
    # (platforms moderate what they can read). See llm/censor.py.
    clean_subtitles: bool = False
    # free-form constraints for the PICTURE only, never for the story: "all weapons
    # are toy ones", "no logos", "no blood". Handed to the writer and appended to
    # every generated shot prompt.
    visual_notes: str = ""
    # free-form description of the LOOK — "аниме", or three paragraphs about grainy
    # 16mm and sodium street light. Compiled once per run into English prompt tags
    # (llm/style.py) and appended to every generated shot prompt, in every mode and
    # whether the shots are clips or stills. It binds the picture only, like
    # `visual_notes`, and says nothing about what is IN it.
    visual_style: str = ""
    # the montage look, as {effect name: dose 0-100} — grain, crt, vhs, glitch and the
    # rest of media/filters. Unlike `visual_style` this is not asked of a generator but
    # applied to the finished picture in the delivery pass, so it holds in every mode,
    # from every source, for the whole length of the video (and of every episode of a
    # serial). Unknown names and out-of-range doses are dropped where the graph is
    # built (media/filters.normalise), not here — a typo in a filter costs the effect,
    # never the run.
    filters: dict[str, int] = {}
    # a catalogue voice for the active engine, or the name of a configs/voices/ clone;
    # empty = whatever the content config (or the mode's default) picks
    voice_override: str = ""
    tts_engine: str = ""  # overrides [tts].engine for this run; empty = the config's
    # WHO speaks the lines. "engine" synthesizes them; "manual" hands the script back
    # and waits for wav files, exactly as `manual` does for footage — the operator may
    # be reading them aloud, or using a service with no API at all. Word timings are
    # then recovered by the aligner, since a microphone emits no WordBoundary events.
    tts_source: Literal["engine", "manual"] = "engine"
    tts_rate: int = 0  # speech rate offset in percent (-50 = half speed, +50 = 50% faster)
    # -- drama mode --------------------------------------------------------
    scenario: str = ""  # the drama's premise/plot; empty = the LLM invents one
    parts: int = 1  # drama only: split one drama into this many cliffhanger parts
    # WHEN an episode is finished, once there is more than one of them. On: as soon as
    # its OWN clips are in — it is cut, subtitled, described and published while the
    # later episodes are still being made, and the run parks between them. That is what
    # the user-assisted path wants, since free daily generator limits run out long
    # before a story does. Off: nothing is cut until every episode's clips are in, then
    # all of them at once. A one-part video is the same either way.
    parts_iterative: bool = True
    # average length of ONE generated clip, in seconds (0 = each generator's nominal).
    # It sets how many clips the story is cut into and how much narration each carries;
    # long clips are written as multi-shot sequences instead of a single framing.
    clip_seconds: float = 0.0
    manual_cast: list[CharacterConfig] = []  # resolved cast for the run (TUI/CLI)
    orchestration: str = ""  # orchestration profile name from configs/orchestration/
    # -- fandom mode -------------------------------------------------------
    # Everything the drama block above means the same here: `scenario` is the brief
    # (what to tell about this world, or which theory to build out of its lore),
    # and parts/clip_seconds/orchestration/cast work identically.
    fandom: str = ""  # folder name under configs/fandoms/; the world being narrated
    fandom_voice: FandomVoice = "resident"  # who is telling it (see FandomVoice)
    # What the picture is made of, when the operator has said. Empty = whatever each
    # source produces, decided per shot where that is a question (a search brief picks
    # a still or a clip per beat; see llm/lookup). Set, it binds: the operator asked
    # for a slideshow, so a search looks for photographs and a hand-made shot is a
    # still, not merely a clip that happens to be short.
    medium: Literal["", "video", "photo"] = ""
    manual_orchestration: OrchestrationConfig | None = None  # ad-hoc chain from the TUI
