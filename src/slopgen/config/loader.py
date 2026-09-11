"""Discovery and loading of TOML configs from the configs/ tree."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import tomli_w

from .models import (
    AccountConfig,
    AdConfig,
    CharacterConfig,
    ContentTypeConfig,
    FandomConfig,
    FrameCard,
    GlobalConfig,
    LLMProfile,
    OrchestrationConfig,
    PresetConfig,
    RunParams,
    ShapesConfig,
    VisualsConfig,
    VoiceConfig,
)

CONFIGS_DIR = Path("configs")


class ConfigError(Exception):
    pass


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}")


def _load_dir(subdir: str, model):
    out = {}
    d = CONFIGS_DIR / subdir
    if d.is_dir():
        for p in sorted(d.glob("*.toml")):
            data = _read_toml(p)
            data.setdefault("name", p.stem)
            out[data["name"]] = model.model_validate(data)
    return out


FANDOM_TOML = "fandom.toml"  # the config file inside a fandom's folder
FRAMES_DIR = "frames"  # the frame base inside a fandom's folder: cards + their pictures


def _load_fandoms(subdir: str = "fandoms") -> dict[str, FandomConfig]:
    """Load every fandom folder under configs/fandoms/.

    A fandom is a DIRECTORY rather than a single file (unlike every other config
    kind, hence not :func:`_load_dir`): the lore documents and the world's own cast
    live next to its TOML. The folder name is the fandom's identity, and the TOML
    itself is optional — a folder holding nothing but markdown is a valid fandom
    with every setting left at its default.

    A world's cast is loaded through the same `CharacterConfig` as the global
    library, but a world's character has no AGE (see the model): it is a look, and
    if age shows on it, it shows in the looks. The field is dropped here rather than
    merely left unwritten, so a file that predates the rule — or one hand-edited
    with an `age` in it — cannot smuggle a value into a mode that has no field for
    it and would never show it back to the operator."""
    out: dict[str, FandomConfig] = {}
    d = CONFIGS_DIR / subdir
    if not d.is_dir():
        return out
    for path in sorted(x for x in d.iterdir() if x.is_dir()):
        toml = path / FANDOM_TOML
        data = _read_toml(toml) if toml.exists() else {}
        data["name"] = path.name  # the folder names it, whatever the TOML says
        data["root"] = path
        cast = _load_dir(f"{subdir}/{path.name}/characters", CharacterConfig).values()
        data["cast"] = [c.model_copy(update={"age": ""}) if c.age else c for c in cast]
        # the frame base. `_load_dir` globs *.toml only, so the pictures sitting in
        # the same folder are ignored for free — and they have to sit there, because
        # a card's crop targets are coordinates on one specific file.
        frames = _load_dir(f"{subdir}/{path.name}/{FRAMES_DIR}", FrameCard).values()
        data["frames"] = [c.model_copy(update={"root": path / FRAMES_DIR}) for c in frames]
        out[path.name] = FandomConfig.model_validate(data)
    return out


def fandom_docs(cfg: FandomConfig) -> list[Path]:
    """The fandom's lore documents, in reading order: the ones `docs` names, else
    every markdown file in the folder. Names that point at nothing are skipped —
    a stale entry must not take the whole world down."""
    if not cfg.root:
        return []
    if cfg.docs:
        return [cfg.root / name for name in cfg.docs if (cfg.root / name).is_file()]
    return sorted(cfg.root.glob("*.md"))


def read_lore(cfg: FandomConfig) -> str:
    """Every lore document concatenated, each under its own filename heading so the
    writer (and the librarian tool) can tell one document from another."""
    parts = []
    for path in fandom_docs(cfg):
        try:
            parts.append(f"=== {path.name} ===\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return "\n\n".join(parts)


# Bumped whenever `llm.lore.SYSTEM` changes what a canon sheet is supposed to CONTAIN.
# The sheet is cached against the checksum below, so without this a fix to the compiler
# would reach only the worlds whose lore happens to be edited afterwards — every sheet
# already on disk would keep the flaw it was compiled with, and the operator would have
# no way of knowing which. Folding the version in retires every sheet at once; the
# rebuild is one call per world, and the TUI already flags a stale sheet.
#   2 — keep two same-named institutions of different factions apart (see llm/lore)
#   3 — a `sayings` section: the omens, proverbs and set phrases a world repeats,
#       copied word for word. Not a rescue of material the sheet was losing — measured
#       on a world holding 21 omens and 12 sayings, every one of them was already in
#       the sheet verbatim, scattered through `rules` and `glossary`. What they lacked
#       was a HEADING, because the writer is told the sheet is reference and not to be
#       recited (stages/fandom_script.CANON_RULE), and that instruction is right for
#       facts and exactly wrong for these. A section of their own is what lets the
#       rule address them separately, and separating it is what stopped the finished
#       videos coming out flatter than the records they were written from
CANON_COMPILER_VERSION = 3


def lore_sha(lore: str) -> str:
    """The checksum that decides whether the compiled canon sheet is still current.

    It covers the documents' TEXT, so a rename, a reorder or a deletion invalidates the
    sheet exactly like an edit does (see :class:`FandomConfig`) — and the version of the
    compiler that built it, so improving the compile prompt invalidates it too."""
    return hashlib.sha1(
        f"v{CANON_COMPILER_VERSION}\n{lore}".encode("utf-8")
    ).hexdigest()


def write_fandom(cfg: FandomConfig) -> Path:
    """Persist a fandom's settings back to its `fandom.toml` (comments not preserved,
    same caveat as the TUI's global-config writer). Runtime-only fields (`root`,
    `cast`) are excluded by the model itself; the cast lives in its own files."""
    if not cfg.root:
        raise ConfigError(f"fandom '{cfg.name}' has no folder to write to")
    cfg.root.mkdir(parents=True, exist_ok=True)
    path = cfg.root / FANDOM_TOML
    path.write_bytes(tomli_w.dumps(cfg.model_dump()).encode())
    return path


def write_config(kind: str, name: str, data: dict) -> Path:
    """Write one named config into `configs/<kind>/<name>.toml`.

    The generic half of config writing, for the kinds whose file IS their model dump:
    LLM profiles, presets, ad contracts, accounts, visuals profiles. `name` is the
    file's identity — the loader fills it back in from the stem — so it is dropped
    from the body rather than written twice and left to disagree with itself.

    Deliberately unvalidated here: the caller has a validated pydantic model and
    hands over its dump. Taking a raw dict and validating inside would mean this
    module knowing every kind, which is the thing being avoided."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ConfigError(f"unusable config name: {name!r}")
    path = CONFIGS_DIR / kind / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in data.items() if k != "name" and v is not None}
    path.write_bytes(tomli_w.dumps(body).encode())
    return path


def delete_config(kind: str, name: str) -> bool:
    """Remove one named config. Returns whether there was anything to remove."""
    path = CONFIGS_DIR / kind / f"{name}.toml"
    if not path.is_file():
        return False
    path.unlink()
    return True


def update_global(section: str, values: dict) -> Path:
    """Merge values into one section of `configs/slopgen.toml`.

    Comments are not preserved — same caveat the terminal's writer carries, and for
    the same reason: this is a round trip through a parser that does not keep them.
    Merging rather than replacing is what keeps a form that shows three fields from
    wiping the other nine in the same section."""
    path = CONFIGS_DIR / "slopgen.toml"
    data = _read_toml(path) if path.exists() else {}
    data.setdefault(section, {}).update(values)
    path.write_bytes(tomli_w.dumps(data).encode())
    return path


def write_character(path: Path, cfg: CharacterConfig) -> Path:
    """Persist one character card.

    Lives here rather than in a frontend because there are two of them now, and a
    second copy of "which fields a character file holds" is a second place for it to
    drift. The compiled `visual_prompt` is written along with everything else: it is
    a cache, and dropping it on every edit would make a run pay to rebuild a
    descriptor that has not changed — `dirty` is what says whether it must.

    `age` is written only when it has a value, because a world's character has none
    (the loader strips it) and an empty key in the file invites somebody to fill it
    in for a mode that would never show it back."""
    data: dict = {
        "appearance": cfg.appearance,
        "plurality": cfg.plurality,
        "visual_prompt": cfg.visual_prompt,
        "dirty": cfg.dirty,
    }
    if cfg.age:
        data["age"] = cfg.age
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(data).encode())
    return path


def frames_dir(cfg: FandomConfig) -> Path:
    """Where a world keeps its frame base. Not created on load: a world with no base
    is one nobody has drawn yet, not a broken one, and an empty folder appearing in
    every fandom would only say that slopgen has been run."""
    if not cfg.root:
        raise ConfigError(f"fandom '{cfg.name}' has no folder to write to")
    return cfg.root / FRAMES_DIR


def file_sha(path: Path) -> str:
    """The checksum a card's crop targets were measured against.

    Full contents rather than size and mtime: a card and its picture travel between
    machines together, because a world is one folder somebody copies, and every copy
    would otherwise read as an edit and throw away geometry that is still correct. A
    base of a hundred 2 MB stills hashes in well under a second, once per run.

    A missing file hashes to "" rather than raising — a card whose file went
    missing is already handled as not usable (see :meth:`FrameCard.usable`), and a
    checksum is not the place to discover it."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def card_is_stale(card: FrameCard) -> bool:
    """Whether the file behind a card is not the one its crop targets were drawn on.
    Never fatal: a stale card is still a picture, it just cannot be trusted to know
    where anything in it is, so selection stops offering it a targeted move and holds
    or pushes in instead (see `pipeline/framebase`).

    A card that has never recorded a checksum is NOT stale — hand-written cards are
    expected, and the editor is what fills `file_sha` in."""
    path = card.path
    if not card.file_sha or path is None:
        return False
    return file_sha(path) != card.file_sha


def write_frame_card(card: FrameCard) -> Path:
    """Persist one frame card to `<fandom>/frames/<name>.toml`.

    The file is not touched: it is already beside the card, and whoever put it there
    is who names it. Runtime-only `root` is excluded by the model, exactly as
    a fandom's is."""
    if not card.root:
        raise ConfigError(f"frame card '{card.name}' has no folder to write to")
    card.root.mkdir(parents=True, exist_ok=True)
    path = card.root / f"{card.name}.toml"
    path.write_bytes(tomli_w.dumps(card.model_dump()).encode())
    return path


def cache_visual_prompt(path: Path, prompt: str) -> bool:
    """Store a freshly compiled `visual_prompt` back into an EXISTING character file.

    `CharacterConfig.visual_prompt` is a cache — rebuilt from the structured fields
    whenever `dirty` is set — but nothing ever wrote it down, so every run recompiled
    the whole cast from scratch. That is a full LLM round trip per character, in
    series: a world of 44 people cost about twenty-four minutes before the writer had
    even started, on every single run.

    Only the two cache fields are touched and only in a file that already exists, so
    an operator's edit cannot be overwritten by a background write, and a character
    that lives only in memory (a drama's ad-hoc cast member) is not given a file it
    was never meant to have. A failure is ignored: the compile still happened, and
    paying for it again is better than taking a run down over a cache."""
    if not prompt.strip() or not path.is_file():
        return False
    try:
        data = _read_toml(path)
        if data.get("visual_prompt") == prompt and data.get("dirty") is False:
            return False
        data["visual_prompt"] = prompt
        data["dirty"] = False
        path.write_bytes(tomli_w.dumps(data).encode())
        return True
    except (ConfigError, OSError, ValueError):
        return False


class ConfigStore:
    """All configs, loaded once. Reload by constructing a new instance."""

    def __init__(self, root: Path | None = None):
        global CONFIGS_DIR
        if root:
            CONFIGS_DIR = root
        gpath = CONFIGS_DIR / "slopgen.toml"
        self.global_cfg = (
            GlobalConfig.model_validate(_read_toml(gpath)) if gpath.exists() else GlobalConfig()
        )
        self.content_types: dict[str, ContentTypeConfig] = _load_dir("content", ContentTypeConfig)
        self.ads: dict[str, AdConfig] = _load_dir("ads", AdConfig)
        self.accounts: dict[str, AccountConfig] = _load_dir("accounts", AccountConfig)
        self.presets: dict[str, PresetConfig] = _load_dir("presets", PresetConfig)
        self.visuals: dict[str, VisualsConfig] = _load_dir("visuals", VisualsConfig)
        self.llm_profiles: dict[str, LLMProfile] = _load_dir("llm", LLMProfile)
        self.characters: dict[str, CharacterConfig] = _load_dir("characters", CharacterConfig)
        # cloned voices: the card and its audio sample live side by side, so each one
        # is told where it was loaded from and resolves `ref` against that folder.
        self.voices: dict[str, VoiceConfig] = _load_dir("voices", VoiceConfig)
        for v in self.voices.values():
            v.root = CONFIGS_DIR / "voices"
        self.orchestrations: dict[str, OrchestrationConfig] = _load_dir("orchestration", OrchestrationConfig)
        self.shapes: dict[str, ShapesConfig] = _load_dir("shapes", ShapesConfig)
        self.fandoms: dict[str, FandomConfig] = _load_fandoms()

    def active_llm_profile(self) -> LLMProfile:
        """Profile named in [llm].profile, else first profile, else a legacy
        profile synthesized from the inline [llm] fields."""
        llm = self.global_cfg.llm
        if llm.profile and llm.profile in self.llm_profiles:
            return self.llm_profiles[llm.profile]
        if self.llm_profiles:
            return next(iter(self.llm_profiles.values()))
        return LLMProfile(
            name="legacy",
            provider=llm.provider,
            base_url=llm.base_url,
            model=llm.model,
            key_env=llm.key_env,
            temperature=llm.temperature,
        )

    def languages(self) -> list[str]:
        langs: set[str] = set()
        for ct in self.content_types.values():
            langs.update(ct.voices.keys())
        return sorted(langs)

    # -- parameter resolution: CLI > preset > account defaults > global ----

    def resolve(
        self,
        lang: str | None = None,
        content_type: str | None = None,
        ad: str | None = None,
        ad_mode: str | None = None,
        visuals: str | None = None,
        duration_s: float | None = None,
        profanity: int | None = None,
        push: str | None = None,
        count: int | None = None,
        preset: str | None = None,
        **extra,
    ) -> RunParams:
        p = self.presets.get(preset) if preset else None
        if preset and not p:
            raise ConfigError(f"preset '{preset}' not found")

        push_val = push if push is not None else (p.push if p else "")
        acc = self.accounts.get(push_val) if push_val else None
        if push_val and not acc:
            raise ConfigError(f"account '{push_val}' not found")
        ad_def = acc.defaults if acc else None

        def pick(cli, preset_v, acc_v, default):
            for v in (cli, preset_v, acc_v):
                if v not in (None, ""):
                    return v
            return default

        g = self.global_cfg.defaults
        params = RunParams(
            lang=pick(lang, p.lang if p else None, ad_def.lang if ad_def else None, ""),
            content_type=pick(
                content_type,
                p.content_type if p else None,
                ad_def.content_type if ad_def else None,
                "",
            ),
            ad=pick(ad, p.ad if p else None, ad_def.ad if ad_def else None, ""),
            ad_mode=pick(ad_mode, p.ad_mode if p else None, ad_def.ad_mode if ad_def else None, g.ad_mode),
            visuals=pick(
                visuals, p.visuals if p else None, ad_def.visuals if ad_def else None, "classic"
            ),
            duration_s=pick(
                duration_s,
                p.duration_s if p else None,
                ad_def.duration_s if ad_def else None,
                self.global_cfg.video.target_duration_s,
            ),
            profanity=pick(
                profanity,
                p.profanity if p else None,
                ad_def.profanity if ad_def else None,
                g.profanity,
            ),
            push=push_val,
            count=pick(count, p.count if p else None, None, g.count),
            **extra,
        )

        if not params.lang:
            raise ConfigError(
                "language is required (pass as an argument, or via --preset / account defaults)"
            )
        # Empty content_type = "auto": no niche, the LLM picks any topic. Only
        # validate the type (and its voice for this language) when one is set.
        if params.content_type:
            if params.content_type not in self.content_types:
                raise ConfigError(
                    f"unknown content type '{params.content_type}' "
                    f"(available: {', '.join(self.content_types)})"
                )
            ct = self.content_types[params.content_type]
            if params.lang not in ct.voices:
                raise ConfigError(
                    f"content type '{params.content_type}' has no voice for language "
                    f"'{params.lang}' (available: {', '.join(ct.voices)})"
                )
        if params.ad and params.ad not in self.ads:
            raise ConfigError(f"ad contract '{params.ad}' not found (available: {', '.join(self.ads)})")
        if (
            not params.manual_visuals
            and params.visuals
            and params.visuals not in self.visuals
        ):
            raise ConfigError(
                f"visuals profile '{params.visuals}' not found (available: {', '.join(self.visuals)})"
            )
        return params
