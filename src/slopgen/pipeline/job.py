"""VideoJob: the mutable state object passed through pipeline stages."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..config.models import KenBurns


class Word(BaseModel):
    text: str
    start: float  # absolute seconds in the final video
    end: float


class BgAsset(BaseModel):
    """One background piece of a scene: a video clip or a Ken-Burns photo slice."""

    path: Path
    duration: float
    is_photo: bool = False
    start: float = 0.0  # seek offset into the clip (continuous background mode)
    # playback speed for a video piece (>1 faster, <1 slower). The drama sync splits
    # a clip/voice length mismatch between this and the voice's atempo instead of
    # looping the clip back to its start mid-scene.
    speed: float = 1.0
    # -- frame-base stills (see pipeline/framebase.py) ----------------------
    # The crop move of the SHOT this piece belongs to, and how far into that shot the
    # piece begins. Two fields rather than one, because the picture track no longer
    # changes where the scenes do: a still that is up for 5.5s across a scene boundary
    # is rendered as two pieces, and both have to describe the same travel — `move`
    # says what the travel is, `move_at` says where in it this piece starts. It is the
    # manoeuvre `start` already makes for continuous video, one clock further out:
    # there the offset is into the SOURCE, here into the shot's own timeline.
    move: KenBurns | None = None
    move_at: float = 0.0


class InsertCue(BaseModel):
    """LLM-authored foreground cue: show `query` while `phrase` is being spoken."""

    query: str
    phrase: str = ""  # exact words from the scene text to anchor the insert to


class FgInsert(BaseModel):
    """A foreground insert popping over the background, scene-relative timing."""

    path: Path
    start: float
    duration: float
    is_video: bool = False  # video insert (looped clip) vs still image


class Entity(BaseModel):
    """One recurring thing the shots must keep looking the same — deliberately
    UNTYPED.

    The cast covers the people the operator wrote down. Everything else a story
    reuses has no such anchor: a transforming robot-house, a specific car, the
    kitchen, a nameless recurring soldier, a crowd with home-made placards. Named
    once in one shot and once in another, a generator draws each from scratch, and
    "robot-house" comes back as a plain robot because nothing ever said what one
    looks like.

    The registry is whatever the model decides is worth pinning: there is no schema
    of allowed sorts, and `kind` is a free label it writes for the operator's eye
    only — nothing branches on it. What matters is `name`, which must be the exact
    string the shot prompts use, because that is what footage substitutes on.
    """

    name: str  # exactly as the shot prompts spell it — substitution matches on this
    kind: str = ""  # free-form label from the model (object/location/crowd/…), cosmetic
    note: str = ""  # what it is, in the content language, so the operator can review it
    visual_prompt: str = ""  # English tag descriptor injected into every shot naming it


class Part(BaseModel):
    """One publishable episode of a drama, and the unit the pipeline finishes work in.

    WHICH scenes belong to an episode is written on the scenes themselves
    (``Scene.part``), because that is what the writer authors and what the operator
    moves around at a breakpoint. What lives here is everything the pipeline
    *derives* per episode — its subtitle file, its cut video, its metadata, where it
    was published — and it lives here rather than on the job because each of these
    appears at a different moment: a drama shot with hand-made clips is finished one
    episode at a time, so part 1 can be cut and published while part 2 is still
    waiting for its clips.

    An info clip is simply a drama with one part, so the whole pipeline has one shape.
    """

    number: int
    ass: Path | None = None  # burned-in subtitles for this episode alone
    file: Path | None = None  # the cut video
    metadata: dict = Field(default_factory=dict)  # title/description/tags of this episode
    published: str = ""  # URL or local path once it has gone out


class Scene(BaseModel):
    text: str  # narration / voiceover (spoken); in drama it may quote characters
    keywords: list[str] = []
    visual_queries: list[str] = []  # narration-synced beat queries from the LLM
    insert_cues: list["InsertCue"] = []  # phrase-anchored foreground cues from the LLM
    is_ad: bool = False
    audio: Path | None = None
    duration: float = 0.0
    clip: Path | None = None  # kept for the ad-scene path
    bg_assets: list[BgAsset] = []
    fg_inserts: list[FgInsert] = []
    words: list[Word] = []
    # -- drama mode --------------------------------------------------------
    video_prompt: str = ""  # English shot description for the AI generator
    characters: list[str] = []  # cast names present in this shot (→ visual_prompt)
    gen_model: str = ""  # assigned generator (generate.VIDEO_MODELS / PHOTO_MODELS)
    key_mode: str = "rotate"  # rotate | single — how to consume API keys
    key: str = ""  # pinned key index for key_mode="single" (label); "" = first
    clip_target_s: float = 0.0  # planned shot length (drives word budget + stretch)
    audio_src_duration: float = 0.0  # natural TTS length before the atempo stretch
    # speech rate this ONE line is voiced at, in percent (None = the run's rate). Set
    # when the operator re-voices a fragment at another speed from the TTS breakpoint,
    # so a later re-run of the stage reproduces that take instead of the run's.
    tts_rate: int | None = None
    audio_tempo: float = 1.0  # atempo factor applied so the voice fits the clip
    video_tempo: float = 1.0  # setpts factor applied to the clip for the same reason
    part: int = 1  # drama: output part number; cuts happen after the last scene in a part


class FrameShot(BaseModel):
    """One still on the picture track: which card is up, when, and how it moves.

    The track belongs to the VIDEO and not to any scene, which is the whole point of
    the frame-base mode. Measured on the source projects: the picture changes are not
    aligned to the narration at all — the two tracks run past each other — and that
    asynchrony is most of what makes stills read as edited footage rather than as a
    slideshow. So shots are planned end to end over the finished timeline, cut at
    WORD boundaries (see :mod:`.framebase`), and only then sliced at the scene
    boundaries into the per-scene :class:`BgAsset`s that assemble already renders.

    It is persisted on the job for the reason `Scene.tts_rate` is: it holds the
    operator's choices — a pinned card, a card they supplied themselves — and a
    re-run of the footage stage must reproduce them rather than re-roll them."""

    start: float  # absolute seconds in the finished video
    duration: float
    # WHERE this shot begins, said in a way that survives the clock moving. The seconds
    # above are derived: they come out of the scene durations, and re-voicing one line
    # at the `tts` breakpoint shifts every one of them. The anchor does not move,
    # because a cut is always placed on a word (see framebase.Cue) — so the honest
    # identity of a shot is which word it starts on, and the seconds are recomputed
    # from it. -1 marks a shot that begins a region rather than a word.
    anchor_scene: int = -1
    anchor_word: int = -1
    card: str = ""  # FrameCard.name; "" = the base does not cover this shot yet
    said: str = ""  # the narration heard over this shot — what the matcher reads
    prompt: str = ""  # what the writer asked to be shown — what an ask is written from
    referents: list[str] = []  # who or what is being talked about while it is up
    target: str = ""  # the region of the card to look at, as the matcher named it
    fit: str = ""  # the matcher's grade of the chosen card (see config.FrameFit)
    move: KenBurns | None = None
    pinned: bool = False  # the operator chose this card; selection may not overrule it
    ask_id: str = ""  # the manual-manifest id when this shot is (or was) an ask


class FrameAsk(BaseModel):
    """One picture the base is missing, and every shot it would cover.

    Asks are grouped rather than counted one per shot, because a card is bought once
    and spent several times: three stretches about the same person in the same place
    are one purchase, and asking three times for what is one picture is exactly the
    operator's time this mode exists to save. What may NOT be grouped is the same
    person in two places — that is two rooms and two pictures — and telling those
    apart is a judgement about the text, which is why the matcher makes it (see
    stages/picture) instead of a rule about referents doing it badly."""

    id: str  # the manual-manifest id, and the inbox filename stem: "frame_00"
    prompt: str = ""  # English, for whatever draws it
    description: str = ""  # the draft of the new card's own description, to be edited
    shots: list[int] = []  # indices into VideoJob.frame_shots this one picture covers
    card: str = ""  # the card it became, once delivered and filed


class ScriptPlan(BaseModel):
    """What one fandom video is ABOUT, and the order it comes apart in — decided by
    one pass before a beat is written (`stages.fandom_script.plan_spine`).

    It lives on the job rather than on the writer that made it for two reasons, and
    the second is the important one. A resumed run must write against the plan it
    started on, like the canon sheet beside it. And the plan is the half of the run
    the operator most wants to argue with: a bad turn costs one field here and six
    beats downstream, so the `script` breakpoint shows it, and re-writing the script
    from an edited plan is one button rather than six rewrites.

    Empty is normal, not broken: only fandom mode plans, and only where the brief is
    short enough that it is not already the piece (`SPINE_MAX_BRIEF`)."""

    subject: str = ""  # the ONE thing this video is about, in the world's own words
    shape: str = ""  # which form, by name, out of the run's shape catalogue
    opens: str = ""  # the fact the piece starts inside
    steps: list[str] = Field(default_factory=list)  # how it works, in causal order
    turn: str = ""  # what follows from the last step and is worse than it sounded
    close: str = ""  # what the last line does
    # How a piece of this SHAPE ends, copied off the catalogue entry when the plan
    # was made (`config.models.ShapeSpec.ends`). Carried rather than looked up again,
    # so the writer's copy and the plan the operator was shown cannot disagree.
    ends: str = ""

    @property
    def usable(self) -> bool:
        """A subject and an order are the two things a writer cannot supply for
        itself; a plan missing either is a topic said twice."""
        return bool(self.subject.strip() and len([s for s in self.steps if s.strip()]) >= 2)


class VideoJob(BaseModel):
    index: int
    workdir: Path
    topic: str = ""
    scenes: list[Scene] = []
    # fandom: what this video is about and in what order (see :class:`ScriptPlan`).
    # None everywhere else, and on a fandom run whose brief was already the piece.
    plan: ScriptPlan | None = None
    # frame-base mode: the picture track, planned over the whole video rather than
    # per scene (see pipeline/framebase.py). Empty in every other mode.
    frame_shots: list[FrameShot] = Field(default_factory=list)
    frame_asks: list[FrameAsk] = Field(default_factory=list)  # pictures still to be made
    cast_prompts: dict[str, str] = Field(default_factory=dict)  # drama: name → visual_prompt
    entities: list[Entity] = Field(default_factory=list)  # drama: recurring non-cast visuals
    # fandom: the world's compiled canon sheet, carried here so a resumed run writes
    # against the same world the first pass did — even if the lore was edited since.
    canon: str = ""
    parts: list[Part] = Field(default_factory=list)  # the episodes, in order (see pipeline/parts.py)
    # episodes still short of a hand-made clip, so the tail stages must skip them and
    # run again on the next resume. Recomputed by the footage stage on every pass.
    pending_parts: list[int] = Field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    # -- what the run produced, read across the parts -----------------------

    @property
    def final_paths(self) -> list[Path]:
        return [p.file for p in self.parts if p.file]

    @property
    def final_path(self) -> Path | None:
        files = self.final_paths
        return files[0] if files else None

    @property
    def published(self) -> str:
        """Where the finished episodes went, one per line — empty until one has."""
        return "\n".join(p.published for p in self.parts if p.published)
