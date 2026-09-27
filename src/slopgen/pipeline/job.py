"""VideoJob: the mutable state object passed through pipeline stages."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..config.models import KenBurns, Point


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
    # How this picture becomes a frame of the video's shape, copied off the card it
    # came from (`config.models.CardFit`). It travels on the piece rather than being
    # looked up at render time because by then the card is gone: `assemble` is handed
    # a path and a duration, and a path does not know which world it belongs to.
    fit: str = "crop"
    fit_x: float = 0.5
    fit_y: float = 0.5


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
    # WHO says this ONE line, as a voice spec ("" = the run's voice). Its reason for
    # existing is intonation: a cloning engine copies the delivery of the sample it was
    # shown, so a line that has to be shouted is voiced with another recording of the
    # same person shouting — `марта:зло` (see `config.models.VoiceConfig.samples`). A
    # spec and not a resolved voice, for the same reason `tts_rate` is a number and not
    # an audio file: it is the operator's CHOICE, and the stage's re-run must be able
    # to reproduce the take from it.
    voice: str = ""
    # …and whether that spec was the WRITER's idea rather than the operator's (see
    # `llm/delivery.py`). It decides one thing: whether a later pass may replace it. A
    # line the operator pinned themselves is theirs — a re-run of the stage sends it to
    # the model as context and never as a line to recast — while an automatic pin is
    # re-decided whenever the casting runs again, and dropped when the line no longer
    # calls for it. It is also what the montage screen shows the difference by.
    voice_auto: bool = False
    # A line that is not a line: a stretch of SILENCE the operator put on the track
    # from the montage room. It has a `duration` and nothing else — no text, no voice,
    # no words — so nothing can be anchored inside it, nothing is synthesized for it,
    # and it is not one of the things `montage.blocking` counts as unfinished.
    #
    # A scene and not a field on its neighbour, because a pause is a thing on the
    # timeline: it is dragged longer by its own edge, it sits between two lines rather
    # than belonging to either, and every piece of machinery that already walks the
    # scenes in order — the clock, the regions, the picture track compiled onto them —
    # then carries it for free. The one rule about it is that two in a row is one
    # pause said twice, which is why `montage.add_hush` refuses to make the second.
    hush: bool = False
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


class EffectCue(BaseModel):
    """One effect firing on the finished video's clock: which one, and on what word.

    The picture track and the narration run past each other on purpose (see
    :mod:`.framebase`), and an effect is the one thing on that track that does NOT:
    an arrow lands on the word it points at or it lands wrong. So a cue is anchored
    exactly as a cut is — to a WORD — and for the same reason: re-voicing a line moves
    every second after it, and the word is what survives that. The seconds here are
    derived from the anchor and re-derived whenever the clock moves.

    `card` and `hook` say which of a card's ready effects this firing IS — the picture
    it was hung on, and that entry's own label (`config.models.CardEffect`). The
    PLACEMENT is not copied: where on the picture the thing sits is read back off the
    card every time it is drawn, so moving it in the card editor moves it in every
    video that ever fired it, which is the point of a base. What is carried is the
    identity, because a cue outlives the pass that chose it and a card re-cast under
    it should leave the arrow pointing at nothing rather than silently at whatever is
    now in that corner (see `pipeline.effects.settle`).

    `pinned` is the operator's hand, and it means here what it means on a shot: the
    effects pass may not overrule it, and it may not be dropped by the rhythm rails
    either. Somebody looking at the video decided this one."""

    effect: str  # EffectSpec.name
    anchor_scene: int = -1
    anchor_word: int = -1
    start: float = 0.0  # absolute seconds in the finished video, re-derived from above
    duration: float = 0.0
    card: str = ""  # the card it was hung on; "" = it sits in the frame, not in the picture
    hook: str = ""  # which of that card's ready effects, by its label
    # WHERE, when this one firing is not where the card says. Empty is the ordinary
    # case and means "wherever the card put it", so moving it in the card editor moves
    # it in every video that fires it. Dragging it in the montage room fills this in
    # instead — the same effect, nudged for this video only, because a picture that is
    # right everywhere else should not be re-aimed for one line.
    points: list[Point] = []
    # …and how far it is turned in THIS firing, in degrees clockwise, on top of the
    # card's own aim. The same override one step further in: the card says which way
    # the arrow points on that picture, and this says which way it points this once.
    turn: float = 0.0
    # How far this firing sits from the word it hangs on, in seconds — what a free drag
    # along the lane leaves behind (see `pipeline.effects.nudge`).
    #
    # An effect is anchored to a word for the reason a cut is: re-voicing a line moves
    # every second after it, and the word is what survives that. But an accent is not
    # always ON a word — it lands a beat after one, it covers the breath between two —
    # and a drag that dropped the anchor to say so would buy free placement at the cost
    # of the one property that makes the placement last. So the drag re-anchors to the
    # NEAREST word and records the remainder here: the firing keeps its distance from a
    # word that moves, which is the honest reading of "here".
    drift: float = 0.0
    word: str = ""  # the word it fires on, for the screen to show back
    pinned: bool = False  # the operator placed it; nothing automatic may take it back
    # How many times the effect's repeating middle runs in THIS firing (see
    # `config.models.EffectSpec.loops`). 0 is not "none": it means follow the effect,
    # which is what an automatic cue and a freshly placed one both do. The montage room
    # is where it stops being 0 — three pulses here, six over the next line — and the
    # firing's length is derived from it rather than typed.
    loops: int = 0


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
    # the effects laid over that track — arrows, circles, stings — each on the word it
    # is about (see :class:`EffectCue` and pipeline/effects.py). Empty everywhere else.
    effect_cues: list[EffectCue] = Field(default_factory=list)
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
