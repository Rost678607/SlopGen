"""Pydantic models for every TOML config kind and for resolved run parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # default script length target; 0 = let the model choose it (see `llm/length`)
    target_duration_s: float = 45.0
    crf: int = 19  # delivery quality, lower is better
    # A ceiling on the delivered bitrate, in Mbit/s (0 turns it off). It exists for the
    # montage filters: grain, VHS hiss and the glitch hash are fresh noise on every
    # frame, which is the one thing no encoder can predict from the frame before, so
    # `crf` alone answers them by spending — a two-minute filtered video measured 78
    # Mbit/s and 1.2 GB. The cap is the honest lever because it does nothing at all to
    # a clean video (a Ken-Burns photo cut runs under 3 Mbit/s and never reaches it)
    # and bites only on the noise, which is also the first thing worth dropping: the
    # platform re-encodes every upload to a third of this anyway.
    max_bitrate_mbps: float = 16.0


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
    # Which model answers which KIND of call, as {call kind: profile name} — the
    # `[llm.stage_profiles]` table. The pipeline's calls are not alike: writing a
    # script off a whole world is what an expensive model is for, while compiling one
    # character's appearance into image tags, naming a video or turning a shot into a
    # stock query is errand work a cheap model does as well. Every call carries a kind
    # label already (the first argument of `complete_json`), so routing needs no new
    # plumbing — an unrouted kind, or one naming a profile that does not exist, simply
    # goes to `profile` as before. Kinds: idea, length, script, drama_outline,
    # drama_script, fandom_outline, fandom_script, fandom_canon, fandom_brief,
    # lore_lookup, drama_entities, drama_shot_fix, char_compile, char_autofill,
    # style_compile, metadata, profanity, censor, lookup, bp_rewrite, bp_scenes,
    # tts_delivery, vision.
    stage_profiles: dict[str, str] = {}
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
    # What this model costs, in USD per MILLION tokens, so a run can price itself
    # (see `llm/usage`). Deliberately configuration and not a shipped table: prices
    # change, differ per provider and are not the same for a cache hit as for a miss,
    # and a stale table quietly reporting the wrong dollar figure is worse than no
    # figure at all. Left at 0, a run still counts every token and simply reports no
    # money. `price_cached` unset means a cache hit is billed like a miss.
    price_in: float = 0.0
    price_cached: float = 0.0
    price_out: float = 0.0


class WebConfig(BaseModel):
    """The browser UI (`slopgen web`).

    Defaults to loopback and no password, which is the only combination that is safe
    without anybody thinking about it. Setting a password is what unlocks binding to
    the network: marking a crop on a picture is a pointing job, and pointing is far
    nicer done with a finger on a tablet than with a mouse — but the same server can
    start runs and spend API quota, so it does not go on the network unlocked.

    `max_parallel` is small on purpose. Runs share more than the machine: one API
    quota, one ffmpeg's worth of CPU, and — for two videos of the same world — one
    folder on disk that both would write to (see web/runs.py, which serialises those
    against each other whatever this says)."""

    host: str = "127.0.0.1"
    port: int = 8770
    password: str = ""  # empty = no login, and then `host` may not leave loopback
    max_parallel: int = 2


class BotConfig(BaseModel):
    """The Telegram front door (`slopgen bot`): a chat, and the browser UI inside it.

    One process holds all three parts, and that is the whole design. The bot serves
    the same FastAPI app `slopgen web` serves, so a run started by a button in the
    chat and a run started in the Mini App are the same run in the same supervisor —
    two servers would have been two lists of runs that disagree about what is going on.

    `public_url` is what the Mini App button opens, and Telegram will only open HTTPS.
    Left empty, the bot raises a Cloudflare quick tunnel and uses the address that
    comes back. That address changes every time the tunnel restarts, which sounds bad
    and costs nothing: the button is rebuilt from whatever the tunnel currently says,
    so the churn is invisible unless you were reading the URL out loud.

    Nobody is served without being on the list. `allow_file` is plain text, one
    Telegram id per line, re-read whenever it changes — adding somebody is an edit,
    not a restart — and the first id on it is the owner, who gets the notices nobody
    asked for (a tunnel that moved, a run that failed)."""

    token_env: str = "TELEGRAM_BOT_TOKEN"
    allow_file: Path = Path("configs/bot_allow.txt")
    # serve the browser UI in-process, which is what the Mini App button opens
    web: bool = True
    # "cloudflared" = raise a free quick tunnel and take whatever address it gives;
    # "off" = no tunnel, and then the Mini App exists only if `public_url` is set
    tunnel: Literal["cloudflared", "off"] = "cloudflared"
    cloudflared: str = "cloudflared"  # the binary, if it is not on PATH
    public_url: str = ""  # your own HTTPS address; set it and no tunnel is raised
    # Push a finished cut into the chat. Telegram refuses uploads over 50 MB from a
    # bot, so a longer video is announced with a link into the Mini App instead.
    deliver_video: bool = True
    max_upload_mb: int = 50
    # how often the bot looks at the supervisor for runs that finished, failed or
    # parked while nobody was watching, in seconds
    poll_s: float = 3.0


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
    web: WebConfig = WebConfig()
    bot: BotConfig = BotConfig()
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

# What a card's unnamed recording is called once every delivery has to have a name.
# Only reached by a card written before they were one level (see
# `VoiceConfig._fold_own_sample`) — nothing creates one under this name today.
DEFAULT_DELIVERY = "обычная"


class VoiceSample(BaseModel):
    """One recording to clone from, and exactly what is said in it.

    Cloning here is zero-shot — there is no training step and no profile living
    inside a model, just a (sample, transcript) pair handed to the synthesizer with
    every line. Which means the sample decides the DELIVERY as much as the timbre:
    the model imitates the reading it was shown. Show it somebody speaking evenly
    and every line comes out even; show it the same person pressing, and the lines
    press. There is no parameter for that on any cloning engine here, and that is
    why a card holds several of these, on one level, with one of them named as its
    default (see :class:`VoiceConfig`).

    `text` is typed by a human on purpose. Lifting it off the sample with a
    recognizer was tried and the errors do not stay put — the model reconciles a
    wrong transcript with the audio by drifting, and in the worst measured case
    spoke words from the SAMPLE in the middle of the synthesized line. Ten seconds
    of typing buys the whole voice.

    The recording lives next to the card (`ref` is relative to configs/voices/)
    because the two are worthless apart. `slopgen voices add` writes both and refuses
    the samples known to break cloning — clipped, too short, too noisy.
    """

    ref: str = ""  # sample filename, relative to the card's own folder
    text: str = ""  # what is said in the sample, exactly, typed by hand
    # cloud engines enrol a voice from a URL and cannot be handed a local file; fill
    # this in only if you want THIS sample to work in the cloud too (see tts/qwen_api)
    ref_url: str = ""
    description: str = ""
    root: Path | None = Field(default=None, exclude=True)  # set by the loader

    @property
    def ref_path(self) -> Path | None:
        if not self.ref:
            return None
        return (self.root / self.ref) if self.root else Path(self.ref)


class VoiceConfig(BaseModel):
    """A cloned voice: one person, and every recording of them there is.

    The card IS the voice — it is a config and not an artifact, portable, and the
    same card works on any engine that clones. What it is NOT is a recording: it
    holds them, all of them on one level, and names one of them
    :attr:`default` — the one a bare ``--voice марта`` speaks with.

    That flatness is the whole shape. A card used to be a recording that also held
    other recordings: its own `ref`/`text` were the default delivery and `samples`
    were "the others", which made the first take somebody cut permanently the
    important one. It is not — an even reading is the usual default and a shouted one
    is occasionally the right default for a whole video, and neither should require
    re-recording anything to become one. So `[samples.<name>]` is every delivery
    there is, `default` is a pointer, and moving the pointer is the whole operation.

    `марта` is the card, `марта:зло` is one delivery of it, and both go wherever a
    voice name is accepted. That is this pipeline's only answer to intonation on a
    cloning engine, and it is an honest one — an emotion nobody recorded cannot be
    asked for. Two things are worth knowing before cutting the takes:

    * They should come out of ONE session, ideally one continuous recording. Timbre
      travels with the delivery, so a sample recorded closer to the microphone or on
      another day clones as a slightly different person — and a video that switches
      between two of those switches narrator mid-sentence.
    * What is bought is per-LINE control, not per-word: a whole line is spoken in the
      delivery of the sample it was voiced with. Leaning on one word inside a line is
      an SSML matter, and SSML belongs to Azure (see `tts/azure.py`).
    """

    name: str
    lang: str = "ru"
    description: str = ""  # the PERSON; each delivery describes itself
    # which delivery a bare `марта` speaks with. Empty = the first one in the table,
    # which is what a card holding exactly one delivery means by it.
    default: str = ""
    samples: dict[str, VoiceSample] = {}
    root: Path | None = Field(default=None, exclude=True)  # set by the loader

    @model_validator(mode="before")
    @classmethod
    def _fold_own_sample(cls, data):
        """Read a card written before the deliveries were one level: its own
        `ref`/`text` become the delivery :data:`DEFAULT_DELIVERY`.

        Kept rather than migrated on disk because a config file is the operator's,
        not ours to rewrite behind their back — the new shape is written the next
        time anything saves the card. It also catches the other legacy writer, code
        that still builds a `VoiceConfig(ref=…, text=…)`, so both arrive in the
        same shape as a card typed by hand today."""
        if not isinstance(data, dict):
            return data
        ref, text = data.get("ref"), data.get("text")
        url = data.get("ref_url")
        if not (ref or text):
            return data
        data = dict(data)
        samples = dict(data.get("samples") or {})
        # the legacy pair IS that card's default recording, so it takes the slot even
        # when a table entry of the same name is already there (a card written by the
        # frozen TUI carries both, and they are the same recording said twice)
        samples[DEFAULT_DELIVERY] = {"ref": ref or "", "text": text or "",
                                     "ref_url": url or ""}
        data["samples"] = samples
        data.setdefault("default", DEFAULT_DELIVERY)
        for key in ("ref", "text", "ref_url"):
            data.pop(key, None)
        return data

    def set_root(self, root: Path) -> None:
        """Where this card's recordings live, told to the card and to every delivery
        in it. One call rather than an assignment, because a sample left without a
        root reads its `ref` as a path relative to the working directory — which is
        not where it is, and the failure is a missing file at synthesis time."""
        self.root = root
        for s in self.samples.values():
            s.root = root

    @property
    def default_name(self) -> str:
        """The delivery a bare card name speaks with. `default` when it names one of
        the table's entries, else the first entry — a card with one delivery needs no
        pointer, and a pointer left behind by a deleted delivery must not take the
        card down with it (`ConfigStore.voice_sample` is where that is complained
        about, once, when the voice is actually used)."""
        if self.default and self.default in self.samples:
            return self.default
        return next(iter(self.samples), "")

    def sample(self, which: str = "") -> VoiceSample | None:
        """The delivery named `which`, the default one when nothing is named, and
        None when this card has no such delivery — the caller says what that means."""
        return self.samples.get(which or self.default_name)

    def as_config(self) -> dict:
        """The card as its TOML file, for whoever is saving it — the browser, the
        terminal or the frozen TUI, so all three write the same shape.

        `default` is written out even when it was only implied, because the file is
        read by people: a card holding three deliveries should say on its face which
        one the video speaks with, rather than leaving it to be worked out from the
        order the table happens to be in."""
        data = self.model_dump(mode="json", exclude={"name", "root"})
        data["default"] = self.default_name
        return data

    @property
    def sample_names(self) -> list[str]:
        """Every delivery, in the order they were written into the card. Not sorted:
        the operator's own order is information — the first one they cut is usually
        the one the rest are variations on, and it is also what `default` falls back
        to."""
        return list(self.samples)


# How well a card has to fit a stretch of narration before it is spent on it. The
# matcher grades every stretch on this scale (see stages/picture), and the operator's
# setting is the cutoff: everything at or above it is used, everything below it is a
# reason to ask for a new picture.
#
#   exact — it shows what is being talked about, in the place it is happening
#   close — the right place or the right people, not both, contradicting nothing
#   loose — of this world and this part of the story, but not about what is said
#   any   — spend whatever is nearest and never ask for anything
FrameFit = Literal["exact", "close", "loose", "any"]
# What each setting will actually accept. "any" holds every grade there is, which is
# what makes it the "stop asking me" position rather than a quality setting.
FIT_ACCEPTS: dict[str, frozenset[str]] = {
    "exact": frozenset({"exact"}),
    "close": frozenset({"exact", "close"}),
    "loose": frozenset({"exact", "close", "loose"}),
    "any": frozenset({"exact", "close", "loose", "wrong"}),
}


# --- crop geometry: where a still is looked at, and how the look travels ---

# What becomes of a card's picture when its shape is not the video's. Until there was
# a choice here there was still an answer — every picture was scaled up until it
# covered the frame and then cut down the middle, silently — and the silence is the
# part that cost something: a wide picture of two people talking came back as one
# person and half of another, and nothing anywhere said that a decision had been made.
#
#   crop — fill the frame and cut away the overspill. Nothing is letterboxed and
#          something is always lost, so WHERE the cut falls is the operator's to
#          place (`FrameCard.fit_x` / `fit_y`).
#   pad  — fit the whole picture in and let black stand where it does not reach. The
#          picture survives entire; the frame is not full.
#
# Not called `FrameFit`: that name is taken, by how tightly a card has to match a
# beat before it may be spent (`RunParams.frame_fit`), which is a different question
# about the same objects.
CardFit = Literal["crop", "pad"]


class Rect(BaseModel):
    """A crop window on a still, in fractions of the picture.

    Three numbers rather than four, because the window's ASPECT is not free: a
    picture is fitted to the video's aspect ratio BEFORE anything is cropped out of
    it (see `media/ffmpeg.make_photo_part`), so a window that is `scale` of the
    width and `scale` of the height is the only shape that can be shown without
    pillars or a second crop. `scale` 1.0 is the whole picture; 0.5 shows a quarter
    of its area.

    The centre is stored rather than a corner because the centre is what an operator
    actually points at — "her face", "the empty cap" — and what a move converges ON.
    Corners would make every zoom an arithmetic puzzle for whoever writes a card by
    hand, which is the fallback whenever the editor is not open."""

    cx: float = 0.5  # window centre, as a fraction of the picture's width
    cy: float = 0.5  # …and of its height
    scale: float = 1.0  # the window's side, as a fraction of the picture (0 < s <= 1)

    def clamped(self, floor: float = 0.1) -> "Rect":
        """The same window, pulled inside the picture and inside what ffmpeg will
        accept. `zoompan` caps its zoom at 10, so a scale under 0.1 silently stops
        moving instead of failing, which is the worst way for this to go wrong."""
        s = min(max(self.scale, floor), 1.0)
        half = s / 2
        return Rect(
            cx=min(max(self.cx, half), 1.0 - half),
            cy=min(max(self.cy, half), 1.0 - half),
            scale=s,
        )


# The move kinds, which are also the anti-repetition key: the source projects never
# ran the same kind on two adjacent shots (eight consecutive shots, eight different
# trajectories), and two zooms in a row is exactly what makes stills read as a
# slideshow however well each one is composed.
# `drift` is the odd one out and earns its place on a base nobody has marked up yet:
# a slow slide across the whole picture needs no crop targets, and without it a fresh
# base can only alternate hold and push_in, which is a metronome of its own.
MoveKind = Literal["hold", "push_in", "drift", "zoom_in", "zoom_out", "pan", "keys"]


class MoveKey(BaseModel):
    """One moment of a crop move: where the window is, and when.

    The presets above are each two of these — a window held, travelled from, held
    again — and that is all a `KenBurns` ever was. Saying so outright is what lets a
    shot carry three of them, or six: hold on the room, come in on the desk, pan to
    the door, and the same arithmetic renders it, because a run of keys is a run of
    the same ramp the two-key form already uses (see `media/ffmpeg._ken_burns`).

    `of` is the card region this window was taken from, kept for the editor's eye the
    way `CropTarget.label` is: nothing renders from it, and a key whose region was
    later renamed still knows where it points, because the rect is the truth."""

    at: float = 0.0  # seconds into the SHOT
    rect: Rect = Rect()
    of: str = ""  # the card region it was placed on, or "" for the whole picture


class KenBurns(BaseModel):
    """One shot's crop move: hold rect A, travel to rect B, hold there.

    Measured off the source footage rather than invented. The move is a straight
    interpolation of the crop RECTANGLE — vertical offset grew 12, 24, 36, 48 px on
    consecutive samples, linear and with a NON-ZERO offset, so the window converges
    off-centre, onto something — and it does not span the shot: one measured shot ran
    20.35s→25.87s with the travel only at 22.5s→24.3s, which is 2.15s of stillness,
    1.8s of travel and 1.57s of stillness. A move that runs the whole shot reads as a
    screensaver; a move that starts and stops reads as somebody who saw something.

    Times are in SHOT seconds, not piece seconds. A shot that straddles a scene
    boundary is rendered as two files (see `pipeline/framebase` and `BgAsset.move_at`)
    and both are laid against this one clock, so the travel crosses the join instead
    of restarting — the same trick continuous video mode plays with `BgAsset.start`,
    one clock further out."""

    rect_a: Rect = Rect()
    rect_b: Rect = Rect()
    move_start: float = 0.0  # seconds into the SHOT where the travel begins
    move_end: float = 0.0  # …and ends; equal to move_start means a pure hold
    kind: MoveKind = "hold"  # anti-repetition key, and the label review shows
    # More than two moments, when the operator has placed them by hand in the montage
    # room. Empty is the ordinary case and means the pair above; two or more of these
    # supersede it entirely (see :meth:`points`). It is additive on purpose — every
    # move ever planned, and every one already sitting in a checkpoint, is the pair,
    # and nothing has to be migrated for a shot that never grew a third moment.
    keys: list[MoveKey] = []

    def points(self) -> list[tuple[float, Rect]]:
        """The move as the moments it actually passes through, in order.

        One reading for both forms, so everything that renders or draws a crop move —
        ffmpeg, the montage preview — walks a list and never asks which kind of move
        it was given. Before the first moment the window holds at it, after the last
        it holds at that; in between it travels straight from each to the next."""
        if len(self.keys) >= 2:
            return [(k.at, k.rect) for k in sorted(self.keys, key=lambda k: k.at)]
        return [(self.move_start, self.rect_a), (self.move_end, self.rect_b)]


# --- configs/effects/ -----------------------------------------------------
#
# The effects base: the arrows, the circles, the stings. A crop move says where to
# LOOK; an effect says LOOK HERE, NOW — it lands on one word, it is over in a second,
# and it is the one thing on the picture track that is deliberately synchronous with
# the narration. (Everything else in this mode runs past the speech on purpose; see
# `pipeline/framebase`. An arrow that arrives half a second after the word it points
# at is not a late arrow, it is a wrong one.)
#
# Global rather than per world, unlike the frame base. A card is a picture OF
# somewhere and belongs to the world it is of; an arrow is an arrow everywhere, and a
# base that had to be copied into each new world would be copied wrong.

# WHERE an effect sits, which is also whether a card is needed to place it at all.
#
#   point  — on a POINT of whatever card is up, put there by hand, and it TRAVELS with
#            the picture: the crop move is a window over the card, so an arrow placed
#            on a doorway is drawn in the card's own coordinates and converges with the
#            zoom exactly as the doorway does.
#
#            A point and not a marked region, which is what it used to be. A crop
#            target exists to be LOOKED at — it is a window the camera can move into,
#            so it is a box of a particular size around a particular thing — and
#            requiring one to put an arrow somewhere meant the base could only point at
#            things somebody had already decided to zoom into. Half of what a video
#            wants to point at is not a thing at all: a corner, a gap in a queue, the
#            empty half of a desk. So the placement is a point, dropped anywhere,
#            answering to nothing; give it a second point and the pair says how BIG it
#            is as well as where (see :class:`CardEffect`).
#   screen — at a fixed place in the frame, whatever is behind it. A flash, a border,
#            a sting, anything with no picture at all. Offered on every shot, because
#            it asks nothing of what is on screen.
#   full   — the whole frame, covered. A flash, a tape glitch, a light leak: material
#            that is not IN the picture but OVER it, so it is scaled to cover rather
#            than placed, and `place` and `width` say nothing about it.
EffectAnchor = Literal["point", "screen", "full"]

# Where a screen-anchored effect sits in the frame. Nine positions and no free
# coordinates, for the same reason the ad overlay has four: an effect placed at
# (0.37, 0.62) is a number nobody can read back, and anything finer than this is what
# the animation keys are for — or, where a free coordinate really is wanted, what a
# `point` placed on the card is.
ScreenPlace = Literal[
    "center", "top", "bottom", "left", "right",
    "top_left", "top_right", "bottom_left", "bottom_right",
]

# What ENDS a video effect, which is the one question a still never raises.
#
#   hold — it runs for as long as its animation says, looping the clip if that is
#          shorter. A sparkle, a pulsing ring: material with no ending of its own.
#   clip — the clip's own end is the effect's end and the animation's clock is
#          ignored. An explosion that resolves, a stamp that lands: material that
#          finishes.
EffectFill = Literal["hold", "clip"]


class EffectKey(BaseModel):
    """One moment of an effect's animation: when, how big, how far off, how solid.

    The same shape as :class:`MoveKey` and for the same reason — everything an effect
    does over its life is a run of moments with straight travel between them, so
    "fades in while growing, sits, drifts up and out" is four numbers at four times
    rather than four switches with four hidden curves. Between keys every value is
    interpolated linearly; before the first and after the last it holds.

    `dx`/`dy` are offsets from wherever the effect sits, in the effect's OWN frame of
    reference — its own drawn width as the unit, and its own axes as the directions —
    and that is the field that makes an arrow work.

    The unit, because an arrow has to hang above the thing it points at, and that is a
    fact about the arrow ("half my own height up"): measured against the video's width,
    the same number would slide the tip off the target the moment the camera came in
    and the target grew. Being self-relative it also cannot be squashed by a 9:16
    frame — one unit right and one unit down move the same number of pixels.

    The axes, because an effect that has been AIMED has to approach along its own line.
    The arrow points down and comes in from above; turn it to point left and it must
    come in from the right, or it lands beside the thing instead of on it. So `rotate`
    (and the aim a card or a cue adds to it) turns the offset with the picture — one
    reading in `pipeline/effects.draw_for` and the same one in both previews.

    `scale` multiplies the effect's own `width`. `alpha` is opacity, 0 to 1.

    An empty key list is not a missing animation. It means the plainest thing there
    is: the effect appears at its full size, holds, and goes — which is exactly right
    for a clip that already animates itself."""

    at: float = 0.0  # seconds on the animation's OWN clock (see `EffectSpec.clock`)
    scale: float = 1.0  # multiplier on the effect's `width`
    dx: float = 0.0  # offset ALONG THE EFFECT, in units of its own drawn width
    dy: float = 0.0  # …and across it, in the same unit, so a diagonal stays square
    alpha: float = 1.0  # 0 = invisible, 1 = solid
    # Turned, in DEGREES clockwise about its own centre. Degrees rather than turns or
    # radians because it is typed by a person and read off a protractor in their head,
    # and about the centre because that is the only pivot that survives being resized.
    #
    # An arrow is the reason it exists: one picture of an arrow points in every
    # direction there is, so a base does not need eight arrows — and a thing that
    # points has to be aimed. It animates like everything else here, so an arrow may
    # also swing in, and a stamp may land crooked.
    rotate: float = 0.0


class EffectSpec(BaseModel):
    """One prepared effect: a picture, a clip, a sound, or a picture with a sound.

    It lives in `configs/effects/<name>.toml` with its material beside it, the way a
    voice lives beside its sample and a card beside its picture. Nothing here is
    per-video: an effect is a TEMPLATE, and what a video does with it is one cue on
    the timeline (see `pipeline.job.EffectCue`).

    `description` is the field that makes the whole thing reachable by a model. The
    effects pass is given this list and the words being spoken, and it fires what
    fits; a line saying WHEN this effect is the right one ("когда в тексте называют
    предмет, который видно на картинке") is what it reads. An effect nobody described
    is still perfectly usable by hand and simply will not be chosen automatically,
    which is the honest outcome — a model cannot guess what an unnamed png means.

    `width` is a fraction of the FRAME's width — 0.4 is four tenths of the screen — and
    it sizes the effect wherever nothing else does: a screen effect, or one dropped on
    a single point of a card. Where a card gives it TWO points it says nothing, because
    the box between them is the size already; and under `full` it says nothing either,
    because the picture is scaled to cover the frame."""

    name: str  # the file's stem, filled in by the loader
    file: str = ""  # the picture or clip beside this file; "" = "<name>.png"
    # a sound played with it, beside this file too. Alone — with no picture — it is a
    # sound effect and nothing is drawn; together they are one effect and fire once.
    sound: str = ""
    description: str = ""  # WHEN to use it, in the operator's language; read by the model
    note: str = ""  # why it is in the base at all, for the operator's eye only
    anchor: EffectAnchor = "screen"
    place: ScreenPlace = "center"  # where it sits, `anchor="screen"` only
    width: float = 0.35  # its size; see above for what it is a fraction OF
    hold: float = 1.2  # the length of the animation's own clock, ONE pass through it
    fill: EffectFill = "hold"  # what ends it, `anchor` aside (see :data:`EffectFill`)
    volume: float = 1.0  # what its sound is mixed in at, 0-2
    keys: list[EffectKey] = []  # the animation; empty = appear, hold, go
    # -- the middle, repeated ----------------------------------------------
    # Two moments on that clock, cutting it into three: what happens on the way IN,
    # the part that REPEATS, and what happens on the way OUT. `loop_to` at or before
    # `loop_from` means there is no repeating part and the clock is played once, which
    # is what every effect written before this did and what most of them want.
    #
    # It is two separators rather than three durations because that is what it is on
    # the screen, and because the moments of the animation are placed on the same clock:
    # a key at 0.4s belongs to the loop or to the entrance depending on where the
    # separator sits, and asking the operator to keep two numbers in agreement about
    # that is asking them to do arithmetic the editor can simply show.
    loop_from: float = 0.0  # where the repeating part begins
    loop_to: float = 0.0  # …and ends; <= loop_from means nothing repeats
    loops: int = 1  # how many times it repeats, unless a cue on the timeline says otherwise
    retired: bool = False  # keep it on disk, stop offering it
    # -- runtime only, filled by the loader; never written back to the TOML --
    root: Path | None = Field(default=None, exclude=True)  # the effects folder

    @property
    def path(self) -> Path | None:
        """The picture or clip, or None for a sound-only effect."""
        if not self.file and self.sound:
            return None
        name = self.file or f"{self.name}.png"
        return (self.root / name) if self.root else Path(name)

    @property
    def sound_path(self) -> Path | None:
        if not self.sound:
            return None
        return (self.root / self.sound) if self.root else Path(self.sound)

    @property
    def usable(self) -> bool:
        """Has something to play and has not been retired. Either half is enough —
        a sting with no picture and a circle with no sound are both whole effects."""
        if self.retired:
            return False
        pic, snd = self.path, self.sound_path
        return bool((pic and pic.is_file()) or (snd and snd.is_file()))

    def moments(self) -> list[EffectKey]:
        """The animation as the moments it passes through, in order — the reading
        every renderer uses, so an effect with no keys and one with four are the same
        thing to it (compare :meth:`KenBurns.points`)."""
        if not self.keys:
            return [EffectKey(at=0.0)]
        return sorted(self.keys, key=lambda k: k.at)

    @property
    def cycles(self) -> bool:
        """Whether this animation has a middle that repeats."""
        return self.loop_to - self.loop_from > 0.02

    def span(self, loops: int = 0) -> float:
        """How long ONE firing lasts: the way in, the middle however many times, the
        way out. `loops` is a cue overriding the effect's own count; 0 means follow the
        effect (see `pipeline.job.EffectCue`).

        An effect that does not cycle simply runs its clock once, which is why this is
        the only place anything asks how long a firing is — the answer used to be
        `hold` and is now derived from it, and every caller was already going through
        one function to get it."""
        if not self.cycles:
            return max(self.hold, 0.1)
        n = max(int(loops or self.loops), 1)
        cycle = self.loop_to - self.loop_from
        tail = max(self.hold - self.loop_to, 0.0)
        return max(self.loop_from + n * cycle + tail, 0.1)

    def clock(self, into: float, loops: int = 0) -> float:
        """Where on the animation's own clock a moment of a FIRING falls.

        The whole of the repeat is this one mapping, and it is deliberately the only
        place that knows about it: before the first separator the two clocks are the
        same, between them the firing's time is folded back into the cycle again and
        again, and after the last repeat it runs on into the exit. Everything
        downstream — the sampling, the preview, the render — reads keys off the clock
        this returns and never learns that anything was repeated."""
        if not self.cycles:
            return into
        n = max(int(loops or self.loops), 1)
        cycle = self.loop_to - self.loop_from
        if into < self.loop_from:
            return into
        ran = into - self.loop_from
        if ran < n * cycle - 1e-9:
            return self.loop_from + (ran % cycle)
        return self.loop_to + (ran - n * cycle)


class Point(BaseModel):
    """A place on a card, in fractions of the fitted picture — the same coordinates a
    :class:`Rect` uses for its centre, so a point and a crop window are measured
    against the same frame and a point placed on the desk is on the desk however the
    card is fitted."""

    cx: float = 0.5
    cy: float = 0.5


class CardEffect(BaseModel):
    """One effect a FRAME CARD has ready, and where on the picture it goes.

    This is the half that cannot live in the effects base: an arrow is an arrow
    everywhere, but "the arrow that points at the husband's cap" is a fact about one
    card, and the coordinates it needs are coordinates on that card's file. So the
    base holds the arrow and the card holds this — the effect's name, where on this
    picture it is placed, and one line saying what firing it MEANS here.

    That line is what the model chooses on. It is given the card that is up, this
    list, and the words being spoken, and it answers with a word — so "круг вокруг
    шапки" is enough and "эффект 3" is not. It is also this entry's NAME: the cue on
    the timeline records which of a card's effects it fired by it.

    `points` is the placement, and how many there are is the whole of the grammar:

    * **none** — nothing on this picture holds it, so it falls back to the effect's
      own `place` in the frame and stops travelling with the crop. A sting over a
      card is a perfectly ordinary thing to want.
    * **one** — it sits there, at the effect's own `width`.
    * **two or more** — their bounding box is where AND how big: the effect is centred
      in it and sized to it. That is what dragging a box around something on the card
      leaves behind, and it is why nothing here needs a marked crop region any more.

    Whatever the count, these are coordinates on the CARD, so the crop move carries
    them: the picture converges on the desk and the arrow converges with it."""

    effect: str = ""  # a name in the effects base
    label: str = ""  # what firing it means here, in the world's own words; its name
    points: list[Point] = []  # where on this picture (see above); empty = in the frame
    width: float = 0.0  # a size override for this card; 0 = the effect's own
    # How far it is turned ON THIS CARD, in degrees clockwise, ADDED to whatever the
    # animation does. Aim and animation are different things: one picture of an arrow
    # points in every direction, and which direction it points HERE is a fact about
    # this picture — while a swing on the way in belongs to the effect and should keep
    # happening whichever way it has been aimed.
    turn: float = 0.0


# --- configs/fandoms/<name>/ ----------------------------------------------


class CropTarget(BaseModel):
    """A named region of a frame card: what is in it, and where.

    `label` is for the operator's eye, in whatever language they think in. `of` names
    the thing the region is OF, spelled as the world spells it — that is what lets a
    move converge on whatever the narration is currently about. Leave it empty for a
    region worth looking at that is not anybody in particular: a doorway, a horizon,
    a pile of something."""

    label: str = ""  # "мужнина шапка, поднятая в руках"
    of: str = ""  # what this region is of, as the world names it; "" = no one thing
    rect: Rect = Rect()


class FrameCard(BaseModel):
    """One reusable still in a world's frame base.

    The base is the whole economy of this mode. A frame is bought once — generated,
    drawn, or paid for — and then spent two to four times across a video and again in
    the next one, so what a card writes down is not "the shot for beat 7" but "a
    picture of this, in this world, with these places worth looking at". That is why
    a card carries crop TARGETS: one wide still of a widow at a market is four shots
    (her face, the empty cap, the crowd behind her, the whole stall) and the move
    between any two of them costs nothing.

    Two texts, and keeping them apart is the point. `prompt` is the English
    generation prompt — what a picture model was, or would be, told to draw, full of
    appearance and lighting and style. `description` is prose for the LLM that
    matches cards to beats, and it deliberately carries NO appearance at all, only
    names: who and what is in the picture, where it is, what is going on. Appearance
    already lives once, in each character's compiled `visual_prompt`; a second copy
    inside every card would drift out of step with it and make matching noisy, since
    what decides whether a picture fits a beat is who is in it and where, never the
    colour of anyone's beard.

    Nothing here is LLM-compiled, so there is no `dirty` flag — unlike a character, a
    card has no structured fields a prompt gets rebuilt from, because the picture IS
    the artifact. What CAN go stale is the geometry: swap the file behind a card and
    every target still points at where something used to be. That is a checksum
    question, exactly like a fandom's `docs_sha`, so `file_sha` is one.

    The file lives beside the card and is named by bare filename, the way a voice
    sample lives beside its card (see :class:`VoiceConfig`). Here the two are bound
    even harder: a crop target is a pair of coordinates ON THAT FILE, so a card and
    its picture separated are both worthless.

    It may also be a CLIP rather than a still, and then it is looped to fill its shot
    rather than retimed to it — a card is a thing the world has, not a thing cut to
    measure for one beat, so stretching it to fit would be the wrong operation. What
    it does not get is a crop move: the clip already has motion of its own, and two
    motions over one picture fight."""

    name: str  # the card's filename stem, filled in by the loader
    file: str = ""  # the picture or clip, beside this card; "" = "<name>.png"
    prompt: str = ""  # the English prompt it was made from, so it can be remade
    # what is in it, in the world's own language, names but never looks (see above)
    description: str = ""
    note: str = ""  # why this card was taken in, in the operator's own language
    targets: list[CropTarget] = []  # named regions; the whole frame is always implied
    # What this picture can DO when it is up: the arrows, circles and stings somebody
    # has hung on it, each aimed at one of the regions above (see :class:`CardEffect`).
    # Empty is the ordinary case — a card is a picture first — and a card with none is
    # never offered any, which is what keeps the effects pass from inventing them.
    effects: list[CardEffect] = []
    file_sha: str = ""  # sha1 of the file when the targets were last written
    retired: bool = False  # keep the card on disk, stop spending it
    # What becomes of this picture where its shape is not the video's (see `CardFit`).
    # The default is what the pipeline always did, so a base written before there was
    # a choice keeps behaving exactly as it did.
    fit: CardFit = "crop"
    # WHERE the frame sits in the picture when cropping, as fractions of the overspill:
    # 0 is hard left / top, 1 is hard right / bottom, 0.5 the middle. Only the axis
    # that actually overspills does anything, which is why there are two of them and
    # not one — a picture wider than the frame is placed across, a taller one down,
    # and a card does not know which it is until somebody looks at the file.
    #
    # Ignored under `pad`, and kept rather than cleared when the operator switches to
    # it: switching back should not lose the placement they chose.
    fit_x: float = 0.5
    fit_y: float = 0.5
    # -- runtime only, filled by the loader; never written back to the TOML --
    root: Path | None = Field(default=None, exclude=True)  # the frames/ folder

    @property
    def path(self) -> Path | None:
        name = self.file or f"{self.name}.png"
        return (self.root / name) if self.root else Path(name)

    @property
    def usable(self) -> bool:
        """Has its file on disk and has not been retired. A card whose picture went
        missing is not an error anywhere — a world is a folder people move around —
        it simply stops being spent."""
        p = self.path
        return not self.retired and p is not None and p.is_file()

    def targets_for(self, referents: set[str]) -> list[CropTarget]:
        """The targets that are OF one of `referents`. Regions of nothing in
        particular are never in here: they are worth looking at, but they are not
        what is being said."""
        return [t for t in self.targets if t.of and t.of in referents]


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
    # WHO the `usher` narrator is speaking to, in this world's own words — "you are a
    # new carrier, handed a two-part satchel and the winter path". Only that voice uses
    # it, and it fixes the ADDRESSEE, never the subject: a piece told to a carrier may
    # still be about the sealed bag nobody lets them open, told as what reaches them.
    # Empty is not a missing setting. The canon compiler works the same fact out of the
    # records on its own (`llm/lore` — "WHO A NEWCOMER HERE BECOMES"), and the writer
    # infers it in-window where there is no sheet, so a world nobody has annotated
    # still gets a consistent "you". This field is the override for when it guesses a
    # role the operator did not want, and a per-run one sits on `RunParams`.
    viewer_role: str = ""
    # offer the writer the `lore_lookup` tool (a librarian LLM that reads the whole
    # document and answers questions). Off = the canon sheet is all it ever sees.
    lore_tool: bool = True
    # which catalogue of PIECE SHAPES this world's videos are planned out of, by name
    # under `configs/shapes/` (see :class:`ShapesConfig`). Empty = the shipped
    # `default` one. A world is the right place for this: the kinds of short piece a
    # place affords — duty rosters, warnings, recipes, creatures, choices — are a fact
    # about the place, and switching the whole set is one word here.
    shapes: str = ""
    # -- LLM-compiled, rebuilt when `docs_sha` stops matching (see above) --
    canon: str = ""  # the canon sheet: rules, glossary, factions, timeline, taboos
    docs_sha: str = ""  # sha1 of the documents `canon` was compiled from
    # -- runtime only, filled by the loader; never written back to the TOML --
    root: Path | None = Field(default=None, exclude=True)  # the fandom's folder
    cast: list[CharacterConfig] = Field(default_factory=list, exclude=True)
    # the world's frame base: stills it can be told out of, spent again and again
    # (see :class:`FrameCard`). Empty is not a broken world, it is one nobody has
    # drawn yet — the footage stage asks the operator for what it is missing.
    frames: list[FrameCard] = Field(default_factory=list, exclude=True)


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


class ShapeSpec(BaseModel):
    """One KIND of short piece a fandom video may be, and how a piece of that kind
    ends.

    The mode's planner used to hold four of these as a closed list inside a prompt,
    and closed it was on purpose: asked to choose a form freely, a model chooses "an
    atmospheric exploration of", which is the survey the whole spine exists to stop.
    But four was the wrong number, and worse, the list was the wrong PLACE — the
    shapes a world wants are a property of the world. A place with a duty roster and
    a place with a bestiary want different ones, and neither is a code change.

    `ends` is what makes this more than a label. A piece that offers a choice ends on
    the list of options and not on a warning; a vignette ends unfinished; a mechanism
    ends on the price. Those are contradictory endings, so the general rule ("one turn
    late, then a line that stops") has to be overridable per shape rather than
    universal — which is exactly what a closed list in a prompt could not express."""

    name: str  # what the planner answers with, and what the operator sees
    use_when: str = ""  # when this shape fits, in one line
    ends: str = ""  # how a piece of this kind ENDS; overrides the default close rule
    turn: str = ""  # what its turn is, where the default (a hidden price) is wrong


class ShapesConfig(BaseModel):
    """A named CATALOGUE of shapes, picked per world (`FandomConfig.shapes`) or per
    run. One file under `configs/shapes/`, like an orchestration or a visuals profile.

    A catalogue rather than a flat list of shapes because switching the whole set at
    once is the operation that gets used: one world's pieces are duty rosters, oaths
    and warnings, another's are recipes and creatures, and moving between them should
    be one field and not a re-tick of eight checkboxes."""

    name: str
    shapes: list[ShapeSpec] = []

    def get(self, name: str) -> ShapeSpec | None:
        """The shape by name, case- and space-insensitively — it arrives from an LLM
        answer, an operator's typing and a TOML, and none of the three can be relied
        on to agree about capitals."""
        key = name.strip().casefold()
        return next((s for s in self.shapes if s.name.strip().casefold() == key), None)


# --- resolved parameters of a single run ----------------------------------


Mode = Literal["info", "drama", "fandom"]
# fandom mode: WHO is telling it, all of them from inside the world.
#   resident   — a person who lives there, first person, the world as daily life
#   chronicler — a chronicler/researcher/theorist of that world, no "I" protagonist,
#                building theories out of its records as if they were real documents
#   usher      — speaks TO you, second person, and the "you" is a person in the world:
#                a new hand being told how things are done here, warned, or offered a
#                choice. Not an address to a viewer, which stays forbidden
FandomVoice = Literal["resident", "chronicler", "usher"]

# fandom mode: what the writer may do where the world's RECORDS stop. Three answers
# rather than two, because the middle one is the answer most worlds actually want and
# a checkbox could not hold it (see stages/fandom_script.GAP_NONE/GAP_GAPS/GAP_FREE).
#   no   — the records are the whole world. A gap is a thing nobody knows, said as a
#          fact about the place, and never filled with a specific of the writer's own.
#   gaps — only where the piece is otherwise unwritable, and only the smallest
#          ordinary texture that unblocks it: never a subject, never a proper noun,
#          never an answer to something the records leave open.
#   free — the records are merely what somebody wrote down, and the writer furnishes
#          the rest of the world at will, in its own grain.
InventLevel = Literal["no", "gaps", "free"]

# What the switch used to be, and the spellings a loop or a chat may be steered with.
# `True` lands on `gaps` and not on `free` deliberately: an unbounded licence is what
# the checkbox actually meant and what made the mode unusable, so the runs that stored
# one come back under the bounded reading rather than the one that misbehaved.
_INVENT_WORDS = {
    "": "no", "0": "no", "false": "no", "off": "no", "n": "no", "never": "no",
    "нет": "no", "нельзя": "no",
    "1": "gaps", "true": "gaps", "on": "gaps", "y": "gaps", "yes": "gaps",
    "да": "gaps", "sometimes": "gaps", "по ситуации": "gaps",
    "always": "free", "any": "free", "в любом случае": "free",
}


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
    # How long the finished video runs. ZERO means the operator bought no length and
    # the model chooses one from the material (see `llm/length` and `free_length`).
    duration_s: float = 45.0
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
    # Write a title, a description and tags for the finished video. OFF by default,
    # and the only stage in the chain that is: everything else produces something the
    # next stage reads, while this produces text for a person to paste into an upload
    # form — so a run that is only being looked at on this machine pays a model for
    # nothing. A run that is actually uploading gets metadata anyway, switch or no
    # switch, because a video cannot go to a platform without a title (see
    # `pipeline/stages/metadata.wanted`).
    write_metadata: bool = False
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
    # WHICH track plays under the voice, by its path under `assets/music/`. Four
    # answers, and the empty one is the oldest: "" means the pipeline takes one out of
    # the folder itself, which is what every run did before there was a choice — but it
    # takes it by a ROLL SEEDED ON THE RUN rather than at random, so the same video
    # always gets the same track and the montage room can play the one the cut will
    # carry. `none` is silence. A value ending in "/" is a FOLDER: the same roll over
    # everything under that shelf, which is how a run gets one KIND of music without
    # giving up the draw. Anything else is one track — `sweden.m4a` at the root, or
    # `эпичное/opening.m4a` below it, so a choice stored before there were folders
    # still names the same file.
    music: str = ""
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
    # Let the writer cast the INTONATIONS: with a cloned voice whose card holds more
    # than one delivery, a pass over the finished script pins the lines that are shouted
    # or whispered to the recording that is (see `llm/delivery.py`). Off by default, and
    # not because it is expensive — it is one call per video — but because it is an
    # opinion about somebody's voice: a card with two takes is not necessarily a card
    # that wants them alternated, and the operator pinning three lines by hand at the
    # breakpoint is the other perfectly good way to use one.
    tts_deliveries: bool = False
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
    # WHICH catalogue of piece shapes this run plans out of, overriding the world's
    # own `FandomConfig.shapes`. Empty = the world's, or the shipped `default`.
    fandom_shapes: str = ""
    # The shape of THIS piece, forced. Empty — the ordinary case — means the spine
    # pass reads the brief and the records and picks one out of the catalogue, which
    # is what a topic queue wants. Naming one here is how a series of alike videos is
    # made, and how an operator overrules a planner that keeps reaching for the same
    # form (see `stages.fandom_script.plan_spine`).
    fandom_shape: str = ""
    # How far the writer may ADD to this world where its records stop: not at all,
    # only where a beat cannot otherwise be written, or freely. See `InventLevel`
    # above and stages/fandom_script, which is where the whole of it lives.
    fandom_invent: InventLevel = "no"
    # This run's answer to "who is being spoken to", overriding the world's own
    # `FandomConfig.viewer_role` and the sheet's inference. One world, many positions
    # in it: the same records make a video for a new clerk and a video for the person
    # who signs their pass, and which one this is is a property of the RUN.
    viewer_role: str = ""
    # What the picture is made of, when the operator has said. Empty = whatever each
    # source produces, decided per shot where that is a question (a search brief picks
    # a still or a clip per beat; see llm/lookup). Set, it binds: the operator asked
    # for a slideshow, so a search looks for photographs and a hand-made shot is a
    # still, not merely a clip that happens to be short.
    medium: Literal["", "video", "photo"] = ""
    manual_orchestration: OrchestrationConfig | None = None  # ad-hoc chain from the TUI
    # -- frame base (generator "frames"; see pipeline/framebase) ------------
    # How close a card has to be to what is being said before it is spent instead of
    # a new one being asked for. It is a band and not a number because what stands
    # behind it is a model's verdict, and a model naming its own confidence as 0.62
    # is naming nothing that stays put between models or between weeks.
    frame_fit: FrameFit = "close"
    # How eager the picture is to change: 0 waits for a real break in the speech and
    # gives long shots, 1 takes almost any gap and gives short ones. It is not a
    # length, because length is not the thing being decided — where the speaker
    # breathes is (see framebase.cut_shots).
    cut_sensitivity: float = 0.35
    # The operator cuts and casts the picture track themselves, in the montage screen.
    # The matcher is never asked and nothing is asked FOR: the `picture` stage lays
    # the cuts out of the speech, leaves every shot empty, and the run parks on its
    # breakpoint with a track waiting to be filled in by hand (see pipeline/montage).
    #
    # It is a switch and not another `frame_fit` band, because it is not an answer to
    # "how close is close enough" — that question stops being asked at all when nobody
    # is matching. A run with this on still has the whole screen available to it; a run
    # with it off can open the same screen at the same breakpoint and argue with what
    # the matcher decided, which is the ordinary way to use it.
    frame_by_hand: bool = False
    # Let a model fire the effects the cards have ready — the arrows, the circles, the
    # stings — onto the words they are about (see `pipeline/effects`). On by default
    # and costing nothing where nothing is prepared: a base with no effects in it, or
    # a run whose cards carry none, never makes the call at all.
    #
    # Off is not "no effects": what it stops is the CHOOSING. Cues placed by hand in
    # the montage room stay exactly where they were put, which is what the switch is
    # for — a video whose effects were decided by somebody looking at it must not have
    # a second opinion laid over them on the next pass of the picture stage.
    frame_effects: bool = True

    @field_validator("fandom_invent", mode="before")
    @classmethod
    def _invent_level(cls, v: object) -> object:
        """Read the gap answer off whatever spelling it arrived in.

        It was a checkbox until it became three positions, so every checkpoint on
        disk, every preset and every loop plan still holds a bool — and a run resumed
        into a validation error would be a settings change eating somebody's evening.
        A word a person typed at a loop (`invent=off`, `invent=always`) lands here
        too, for the same reason the other aliases do."""
        if isinstance(v, bool):
            return "gaps" if v else "no"
        if isinstance(v, str):
            return _INVENT_WORDS.get(v.strip().casefold(), v)
        return v

    @property
    def free_length(self) -> bool:
        """Nobody bought a length: the model chooses one from the material.

        Every mode accepts it and they arrive at it differently, which is why this is a
        question rather than a number (see `llm/length`). An info clip needs no length
        at all — its video is as long as its narration turned out — so its writer is
        simply told to choose. A beat mode has to know before it writes, because the
        length is what the shot list is cut from, so the script stage asks for one and
        writes the answer back onto `duration_s`."""
        return self.duration_s <= 0
