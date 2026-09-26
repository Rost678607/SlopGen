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
    EffectSpec,
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
    VoiceSample,
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
# The effects base: one folder for every world, holding each effect's TOML and the
# picture, clip or sound beside it. `_load_dir` globs *.toml only, so the material
# sitting in the same folder is ignored for free — and it has to sit there, the way a
# card's picture does, because an effect separated from its file is nothing at all.
EFFECTS_DIR = "effects"


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


def effects_dir() -> Path:
    """Where the effects base lives. Created on demand by whoever writes into it,
    never on load — an empty folder appearing in every checkout would only say that
    slopgen has been run."""
    return CONFIGS_DIR / EFFECTS_DIR


def write_effect(spec: EffectSpec) -> Path:
    """Persist one effect to `configs/effects/<name>.toml`.

    The material beside it is not touched: it was put there by whoever brought it,
    exactly as a card's picture is. Runtime-only `root` is excluded by the model."""
    if not spec.name or "/" in spec.name or "\\" in spec.name or spec.name.startswith("."):
        raise ConfigError(f"unusable effect name: {spec.name!r}")
    root = spec.root or effects_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{spec.name}.toml"
    body = {k: v for k, v in spec.model_dump().items() if k != "name"}
    path.write_bytes(tomli_w.dumps(body).encode())
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
        # cloned voices: the card and its audio samples live side by side, so each one
        # is told where it was loaded from and resolves `ref` against that folder. Each
        # of the card's extra recordings too — they are the same kind of thing in the
        # same folder, and a sample that cannot find its file clones nothing.
        self.voices: dict[str, VoiceConfig] = _load_dir("voices", VoiceConfig)
        for v in self.voices.values():
            v.set_root(CONFIGS_DIR / "voices")
        self.orchestrations: dict[str, OrchestrationConfig] = _load_dir("orchestration", OrchestrationConfig)
        self.shapes: dict[str, ShapesConfig] = _load_dir("shapes", ShapesConfig)
        # the effects base: arrows, circles, stings. Like a cloned voice, each entry
        # is told where it was loaded from, because its material lies beside it.
        self.effects: dict[str, EffectSpec] = _load_dir(EFFECTS_DIR, EffectSpec)
        for e in self.effects.values():
            e.root = CONFIGS_DIR / EFFECTS_DIR
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

    # Which store each config kind lives in, by the folder it is loaded from. The folder
    # name IS the kind everywhere else (`_load_dir`, `write_config`, the browser's
    # `/api/configs/<kind>`), so this table is the only place the two have to be paired.
    RENAMEABLE = {
        "llm": "llm_profiles",
        "presets": "presets",
        "ads": "ads",
        "accounts": "accounts",
        "visuals": "visuals",
        "characters": "characters",
        "content": "content_types",
        "orchestration": "orchestrations",
    }

    # Where a name of one kind is written down in ANOTHER config: the preset is the one
    # config that is mostly references, so it is most of this table. A name also appears
    # in runs — `RunParams`, a checkpoint, a command someone typed — and those are
    # records of a particular video rather than settings, so a rename deliberately does
    # not chase them (see `rename_voice`, which says the same thing at more length).
    _PRESET_FIELDS = {"content": "content_type", "ads": "ad", "visuals": "visuals",
                      "accounts": "push"}

    def _repoint_presets(self, kind: str, old: str, new: str) -> list[str]:
        field = self._PRESET_FIELDS.get(kind)
        if not field:
            return []
        notes = []
        for name, preset in self.presets.items():
            if getattr(preset, field, "") != old:
                continue
            setattr(preset, field, new)
            write_config("presets", name, preset.model_dump(mode="json"))
            notes.append(f"preset '{name}' now points at '{new}'")
        return notes

    def _repoint_llm(self, old: str, new: str) -> list[str]:
        """The active profile and the per-kind routes, both in `[llm]` of the global
        file. A profile renamed without this keeps working by accident — an unroutable
        kind falls back to the active profile — which is the worst of the three possible
        outcomes: the run is fine, the routing is silently gone, and nothing says so."""
        cfg, notes, values = self.global_cfg.llm, [], {}
        if cfg.profile == old:
            cfg.profile = new
            values["profile"] = new
            notes.append(f"the active profile is now '{new}'")
        routes = {k: (new if v == old else v) for k, v in (cfg.stage_profiles or {}).items()}
        if routes != (cfg.stage_profiles or {}):
            moved = [k for k, v in (cfg.stage_profiles or {}).items() if v == old]
            cfg.stage_profiles = routes
            values["stage_profiles"] = routes
            notes.append(f"{', '.join(moved)} now go to '{new}'")
        if values:
            update_global("llm", values)
        return notes

    def rename_config(self, kind: str, name: str, new: str) -> list[str]:
        """Rename one config of any list-shaped kind, and repoint what named it.

        The file is MOVED rather than rewritten. A config's name is its filename and
        never appears in the body, so moving it is both lossless — comments somebody
        wrote in the file survive — and the only operation that cannot half-succeed.

        What is repointed is the other CONFIGS: presets naming a content type, an ad, a
        visuals profile or an account, and `[llm]` when a profile is renamed. What is
        not is a run: `RunParams` inside a checkpoint records which video was made with
        what, and rewriting that would be editing history rather than a setting."""
        store = getattr(self, self.RENAMEABLE[kind], None) if kind in self.RENAMEABLE else None
        if store is None:
            raise ConfigError(f"configs of kind '{kind}' cannot be renamed")
        new = (new or "").strip()
        if not new or "/" in new or "\\" in new or new.startswith("."):
            raise ConfigError(f"unusable name: {new!r}")
        cfg = store.get(name)
        if cfg is None:
            raise ConfigError(f"there is no '{name}' among the {kind}")
        if new == name:
            return []
        if new in store:
            raise ConfigError(f"there is already a {kind} entry called '{new}'")
        path = CONFIGS_DIR / kind / f"{name}.toml"
        dest = path.with_name(f"{new}.toml")
        if dest.exists():
            raise ConfigError(f"{dest} is already there")
        if path.is_file():
            path.replace(dest)
            # A body that spells its own name out loud wins over the filename when the
            # file is read back (`_load_dir` keys on `data["name"]`), so a moved file
            # would come back under the old name and the rename would have done nothing
            # visible. Rewritten only in that case: `write_config` drops the key, which
            # is where every writer here already leaves it — the filename IS the name,
            # and a file that does not repeat it keeps its comments through a rename.
            body = _read_toml(dest)
            if body.get("name") and body["name"] != new:
                write_config(kind, new, body)
        cfg.name = new
        store.pop(name, None)
        store[new] = cfg
        return self._repoint_presets(kind, name, new) + (
            self._repoint_llm(name, new) if kind == "llm" else [])

    # -- cloned voices: cards and their recordings, in one namespace -------

    def voice_sample(self, spec: str) -> tuple[VoiceConfig, VoiceSample, str] | None:
        """Resolve a voice name into (card, delivery, the delivery's name), or None
        when no card of that name exists — which is what makes a name a catalogue voice.

        `марта` is whichever delivery the card names as its default; `марта:зло` is
        that one by name. Both come back with the delivery RESOLVED, so the caller
        never has to ask a second time and the name it gets back is the recording it
        will actually be spoken with — which is what the voiced-line cache is keyed on
        (see `tts.base.Voice.cache_key`). The consequence is the intended one: move a
        card's default and every line that was following it is re-voiced, while the
        lines pinned to a delivery by name do not move.

        The colon is read as a separator only when what stands before it IS a card,
        because a catalogue name can contain one of its own —
        `ru-RU-Svetlana:DragonHDOmniLatestNeural` is a single voice and not a delivery
        of a card called `ru-RU-Svetlana`. A card whose own name contains a colon
        therefore wins over the split, which is the same rule said once more.

        Raises when the card exists and the delivery does not. Falling back to another
        delivery there would be worse than failing: the run would finish, the operator
        would have asked forty lines to be whispered, and every one of them would come
        out announced."""
        if not spec:
            return None
        card = self.voices.get(spec)
        which = ""
        if card is None:
            base, _, which = spec.rpartition(":")
            card = self.voices.get(base) if which else None
            if card is None:
                return None
            sample = card.sample(which)
            if sample is None:
                known = ", ".join(card.sample_names) or "nothing at all"
                raise ConfigError(
                    f"voice '{base}' has no delivery called '{which}' — it has {known}. "
                    f"Add one: `slopgen voices record {base} <sample.wav> --as {which} "
                    "--text \u2026`"
                )
            return card, sample, which
        # a bare card name: whichever delivery it calls its default
        sample = card.sample()
        if sample is None:
            raise ConfigError(
                f"voice '{card.name}' holds no recording at all — the card is there and "
                f"there is nothing in it to clone from. Add one: "
                f"`slopgen voices record {card.name} <sample.wav> --as \u2026 --text \u2026`"
            )
        return card, sample, card.default_name

    def voice_specs(self) -> list[str]:
        """Every cloned voice a run can be pointed at: each card, and `card:delivery`
        for each delivery in it. One flat list on purpose — a picker offering these
        offers exactly what `--voice` accepts, and the two kinds are resolved from the
        one namespace.

        The bare card name is in there because it is not a synonym for its default
        delivery but a different instruction: "whatever this card's default is", which
        keeps following the card after the editor moves it (see `voice_catalogue` for
        the shape a picker wants instead of this)."""
        out: list[str] = []
        for name in sorted(self.voices):
            out.append(name)
            out.extend(f"{name}:{s}" for s in self.voices[name].sample_names)
        return out

    def voice_catalogue(self) -> list[dict]:
        """The same voices, grouped the way they are meant to be READ: one entry per
        card, holding its deliveries on one level with the default marked.

        A flat list of specs says nothing about which of two names is one person's two
        readings and which is two people, and it cannot show which delivery a bare
        `марта` currently means. Both interfaces draw from this — the card is a folder,
        its deliveries are its contents, and the one marked `default` is what the whole
        video speaks with until somebody moves it."""
        return [
            {"name": name,
             "lang": v.lang,
             "description": v.description,
             "default": v.default_name,
             "deliveries": [
                 {"which": which,
                  "spec": f"{name}:{which}",
                  "description": v.samples[which].description,
                  "is_default": which == v.default_name}
                 for which in v.sample_names
             ]}
            for name, v in sorted(self.voices.items())
        ]

    def names_recording(self, path: Path, *, except_: tuple[str, str] | None = None) -> bool:
        """Does any voice card still name this audio file — ignoring one (card, delivery)?

        Asked before a recording is deleted. Two deliveries CAN point at one file, and
        nothing stops two cards from doing it either: a `ref` is a filename typed into a
        config, so the same wav is reachable from as many cards as name it. Deleting one
        of them used to take the file with it unconditionally, which is a silent way to
        empty out a card nobody was editing — measured the hard way, on a card that
        borrowed another's sample.

        `except_` is the entry being removed, which must not count as a reason to keep
        the file."""
        want = Path(path)
        for name, card in self.voices.items():
            for which, smp in card.samples.items():
                if except_ is not None and (name, which) == except_:
                    continue
                ref = smp.ref_path
                if ref and Path(ref) == want:
                    return True
        return False

    def _check_voice_name(self, name: str, *, what: str) -> str:
        name = (name or "").strip()
        if not name or "/" in name or ":" in name or name.startswith("."):
            raise ConfigError(
                f"unusable {what} name: {name!r} — it cannot be empty, start with a dot, "
                "or contain / or :, because the colon is what separates a card from one "
                "of its deliveries everywhere a voice is named")
        return name

    def _move_recording(self, card: str, which: str, sample: VoiceSample,
                        to_card: str, to_which: str) -> str | None:
        """Give a delivery's file the name its new address implies, and return the note
        to show for it — or None when the file is left where it is.

        Left alone in two cases, both deliberate. A recording ANOTHER card also names is
        not this rename's to move (see :meth:`names_recording`); and a file that does not
        follow the `<card>.<delivery>.wav` convention was put there by hand or by an
        older version, so its name is information of somebody's own and renaming it would
        be tidying up after a person who did not ask."""
        ref = sample.ref_path
        if ref is None or not Path(ref).is_file():
            return None
        old = Path(ref)
        if self.names_recording(old, except_=(card, which)):
            return f"{old.name} is named by another card too, so it stays where it is"
        want = f"{card}.{which}"
        if old.stem != want:
            return None
        new = old.with_name(f"{to_card}.{to_which}{old.suffix}")
        if new == old:
            return None
        if new.exists():
            return f"{new.name} already exists, so {old.name} keeps its name"
        old.replace(new)
        sample.ref = new.name
        return f"{old.name} → {new.name}"

    def _revoice_content_types(self, old: str, new: str) -> list[str]:
        """Point every content type that named this voice at its new name.

        The only place in the configs where a voice name is written down other than the
        card itself (`ContentTypeConfig.voices`), and the one a rename can actually fix:
        a run's own checkpoint also holds voice specs, but that is the record of a video
        already being made and not ours to edit (see the note in `rename_voice`)."""
        notes: list[str] = []
        for name, ct in self.content_types.items():
            hit = {lang: v for lang, v in ct.voices.items()
                   if v == old or v.startswith(old + ":")}
            if not hit:
                continue
            for lang, v in hit.items():
                ct.voices[lang] = new + v[len(old):]
            write_config("content", name, ct.model_dump(mode="json"))
            notes.append(f"content type '{name}' now says {', '.join(ct.voices[l] for l in hit)}")
        return notes

    def rename_voice(self, name: str, new: str) -> list[str]:
        """Rename a card, and everything that follows from it: the file, the recordings
        named after it, and the content types pointing at it. Returns what moved.

        What it CANNOT follow is a pin inside a run — `Scene.voice` in a checkpoint, or
        a `--voice` typed into a command. Those are records of a particular video and not
        settings, so they are left alone and fail loudly if that video is resumed: the
        resolver says which deliveries the card has rather than quietly voicing forty
        lines in the wrong one. Rename before you pin, not after."""
        new = self._check_voice_name(new, what="voice")
        card = self.voices.get(name)
        if card is None:
            raise ConfigError(f"no voice named '{name}'")
        if new == name:
            return []
        if new in self.voices:
            raise ConfigError(f"there is already a voice called '{new}'")
        notes = [n for which, smp in card.samples.items()
                 if (n := self._move_recording(name, which, smp, new, which))]
        card.name = new
        self.voices.pop(name, None)
        self.voices[new] = card
        write_config("voices", new, card.as_config())
        delete_config("voices", name)
        notes += self._revoice_content_types(name, new)
        return notes

    def rename_delivery(self, name: str, which: str, new: str) -> list[str]:
        """Rename one delivery inside its card, keeping the table's order.

        The order is information — the first take cut is usually the one the rest are
        variations on, and it is what `default` falls back to — so the entry is renamed
        in place rather than removed and appended. The pointer follows it, and a pin in a
        run does not (see :meth:`rename_voice`)."""
        new = self._check_voice_name(new, what="delivery")
        card = self.voices.get(name)
        if card is None:
            raise ConfigError(f"no voice named '{name}'")
        if which not in card.samples:
            known = ", ".join(card.sample_names) or "nothing at all"
            raise ConfigError(
                f"voice '{name}' has no delivery called '{which}' — it has {known}")
        if new == which:
            return []
        if new in card.samples:
            raise ConfigError(f"'{name}' already has a delivery called '{new}'")
        sample = card.samples[which]
        # asked BEFORE the table is rebuilt: once the old key is gone `default` names
        # nothing, `default_name` falls back to the first entry, and the pointer would
        # quietly land on a delivery nobody chose
        was_default = card.default_name == which
        notes = [n for n in [self._move_recording(name, which, sample, name, new)] if n]
        card.samples = {(new if k == which else k): v for k, v in card.samples.items()}
        if was_default:
            card.default = new
        write_config("voices", name, card.as_config())
        notes += self._revoice_content_types(f"{name}:{which}", f"{name}:{new}")
        return notes

    def set_default_delivery(self, name: str, which: str) -> VoiceConfig:
        """Point a card at another of its deliveries, and hand the card back to be
        written. The one operation this whole shape exists for, so it lives next to the
        resolution rule rather than in whichever interface asked for it — the browser,
        the terminal and an agent editing configs all mean the same thing by it."""
        card = self.voices.get(name)
        if card is None:
            raise ConfigError(f"no voice named '{name}'")
        if which not in card.samples:
            known = ", ".join(card.sample_names) or "nothing at all"
            raise ConfigError(
                f"voice '{name}' has no delivery called '{which}' — it has {known}")
        card.default = which
        return card

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
