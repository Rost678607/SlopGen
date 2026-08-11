"""VideoJob: the mutable state object passed through pipeline stages."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


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
    audio_tempo: float = 1.0  # atempo factor applied so the voice fits the clip
    video_tempo: float = 1.0  # setpts factor applied to the clip for the same reason
    part: int = 1  # drama: output part number; cuts happen after the last scene in a part


class VideoJob(BaseModel):
    index: int
    workdir: Path
    topic: str = ""
    scenes: list[Scene] = []
    cast_prompts: dict[str, str] = Field(default_factory=dict)  # drama: name → visual_prompt
    entities: list[Entity] = Field(default_factory=list)  # drama: recurring non-cast visuals
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
