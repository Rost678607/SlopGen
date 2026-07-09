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


class VideoJob(BaseModel):
    index: int
    workdir: Path
    topic: str = ""
    scenes: list[Scene] = []
    cast_prompts: dict[str, str] = Field(default_factory=dict)  # drama: name → visual_prompt
    ass_path: Path | None = None
    final_path: Path | None = None
    metadata: dict = Field(default_factory=dict)
    published: str = ""  # URL or local path after publish

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)
