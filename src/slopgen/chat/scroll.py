"""When each drawing is up, and where the view is looking while it is.

The conversation is a canvas and the video is a window onto it, and everything in
this module is one of two questions: WHEN does the picture change, and WHERE is the
window when it does.

**When** is answered by the speech, never by a timer. A message appears when its line
begins; a piece of a long message appears on the first word of that piece, by the
word timings the voicing stage recovered; a reaction lands when the message it is on
has been read to the end. The one thing on a fixed clock is the gap between messages,
which is what the operator asked for in place of a reading-speed model — and it is
also the whole length of a message nobody reads aloud.

**Where** is the scroll, and it has three answers because the operator asked for
three (`ChatConfig.scroll`): follow the newest message by travelling, follow it in one
frame, or empty the screen and start again at the top. The first two also take the
third as a per-message MARK (`ChatMsg.clear_before`), because a conversation that
rolls throughout still wants a clean screen at the places somebody chooses.

What comes out is a list of :class:`~..pipeline.job.ChatState` — a PNG, a stretch of
the clock, and a window that may be moving — and :func:`compile_assets` slices those
onto the scenes as ordinary :class:`~..pipeline.job.BgAsset`s. Past that line nothing
knows this mode exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .draw import Canvas, Person

log = logging.getLogger(__name__)

# Nothing here imports the pipeline, and that is deliberate rather than incidental.
# `slopgen.chat` is the layer that knows how to draw a conversation; the pipeline is
# the layer that knows there is a video being made. Importing upward would make the
# drawing unusable anywhere else — the room's preview, a test, a one-off script — and
# would close a cycle, since the stage that calls this is itself reached through
# `slopgen.pipeline`. So messages and scenes arrive as whatever has the right
# attributes, and what goes back is this module's own :class:`State`, which the stage
# turns into the job's record of it.

# The shortest a state may be. Two drawings a frame apart are not two pictures, they
# are a flicker — and they cost two ffmpeg invocations to produce it.
MIN_STATE_S = 0.08


@dataclass
class State:
    """One drawing and the stretch of clock it is up for.

    The planner's own result type. `pipeline.job.ChatState` is the same thing written
    down on the job so a resumed run does not redraw it, and the stage copies one into
    the other — which is the usual price of keeping a layer from importing upward, and
    a small one: a handful of fields that have not changed since they were written."""

    start: float
    msg: int
    from_y: float
    to_y: float
    duration: float = 0.0
    anchor_scene: int = -1
    path: Path | None = None
    # This state is the first of a new conversation, so the picture does not cut to it:
    # it slides in from the right over the one before, which is what every messenger
    # does when you open the next chat (see `ffmpeg.make_chat_part`).
    swipe: bool = False
    # The header bar laid over this state, filled in by whoever painted it. The
    # drawing layer neither knows nor cares what it is — it is a path the stage puts
    # here on its way to the assembler — but it belongs to the state rather than to
    # the run, because which bar is up is a property of which conversation is.
    overlay: object = None


def scene_starts(scenes: list) -> list[float]:
    """Where each scene begins on the finished video's clock.

    Accumulated rather than read off `Word.start`, because a scene nobody reads aloud
    has no words to read it off — and those are half of a silent conversation."""
    out, t = [], 0.0
    for scene in scenes:
        out.append(t)
        t += scene.duration
    return out


def chunk_lines(lines: list[str], chunk: int, chunk_min: int) -> list[int]:
    """How many lines are showing at each reveal, as a running total.

    `[len(lines)]` — one reveal, the whole message — is the answer for a short
    message however small `chunk` is. A two-word line arriving in two pieces does not
    read as somebody typing, it reads as a stutter, and `chunk_min` is where the
    operator draws that line."""
    whole = sum(len(ln) for ln in lines)
    if chunk <= 0 or whole < max(chunk_min, 1) or len(lines) <= 1:
        return [len(lines)]
    out, run = [], 0
    for i, line in enumerate(lines, start=1):
        run += len(line)
        if run >= chunk and i < len(lines):
            out.append(i)
            run = 0
    out.append(len(lines))
    return out


def reveal_times(scene, lines: list[str], caps: list[int],
                 start: float, end: float) -> list[float]:
    """The moment each reveal fires, one per entry in `caps`.

    A reveal lands on the first WORD of the piece it uncovers, which is the only
    anchor that survives the line being re-voiced at another speed. Where the word
    timings are missing or disagree with the drawn text — a synthesizer that merged
    two words, a message with no voice at all — the piece is placed proportionally
    across the line's span instead, which is wrong by a fraction of a second and
    never wrong by a message."""
    if not caps:
        return []
    per_line = [max(len(ln.split()), 1) for ln in lines]
    firsts = []  # the word index each cap begins at
    for i, cap in enumerate(caps):
        firsts.append(0 if i == 0 else sum(per_line[:caps[i - 1]]))
    words = scene.words
    out = []
    for i, w in enumerate(firsts):
        if i == 0:
            out.append(start)
        elif 0 <= w < len(words):
            out.append(max(words[w].start, start))
        else:
            out.append(start + (end - start) * (w / max(sum(per_line), 1)))
    # a reveal may never precede the one before it, however the timings came out
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + MIN_STATE_S)
    return [min(t, end) for t in out]


# Where a message nobody reads aloud counts as "read": a share of the way through the
# time it is up. There is no last word to wait for, and hanging the reactions on the
# very end would put them on screen for the one frame before the next message lands.
SILENT_READ = 0.45


def react_times(read_end: float, room: float, count: int, span: float) -> list[float]:
    """When each reaction pops, once its message has been read.

    They land one after another and faster the more of them there are: eight reactions
    must not take eight times as long as one, which is the operator's rule and also
    what a real client looks like. `span` is the whole flurry, `room` is how much of
    the clock there actually is before the next message arrives, and the flurry is
    squeezed into whichever is smaller."""
    if count <= 0:
        return []
    usable = max(min(span, room - MIN_STATE_S), 0.0)
    step = usable / count
    return [read_end + step * (i + 1) for i in range(count)]


class Planner:
    """Walks the conversation once, laying the canvas out and recording the states."""

    def __init__(self, ctx, job, width: int, height: int,
                 *, people: dict[str, Person], wallpaper: Path | None = None,
                 top_inset: int = 0, paint=None):
        self.ctx = ctx
        self.cfg = ctx.chat
        self.skin = ctx.chat_skin
        self.job = job
        self.height = height
        self.people = people
        self.canvas = Canvas(self.skin, width, wallpaper=wallpaper, top_inset=top_inset)
        self.states: list[State] = []
        self.window = 0.0  # where the view is looking right now, in canvas pixels
        # Called with (canvas, state) the moment a state is recorded, which is the
        # only moment it can be drawn: the canvas is walked forward and never
        # rewound, so a state painted afterwards would be painted from a conversation
        # that has since grown. Planning without painting is a legitimate use (the
        # room's timeline wants the clock and not the pictures), so it is optional.
        self.paint = paint

    # -- the window ---------------------------------------------------------

    def _walk(self) -> list[tuple[int, int, object]]:
        """Every message in the video as (ordinal, which conversation, the message).

        The ordinal is the one `ChatMsg.scene` was laid against and the one a state
        reports, so it is the video's own numbering; the conversation index is what
        the seams are read off. Computed once per pass rather than zipped at each use,
        because three callers need the same three numbers and a fourth derivation of
        them is a fourth place for them to disagree."""
        out, n = [], 0
        for ci, conv in enumerate(self.job.conversations):
            for msg in conv.messages:
                out.append((n, ci, msg))
                n += 1
        return out

    def _rest(self) -> float:
        """Where the view belongs with the canvas as it stands: at the bottom of it,
        or at the top while the conversation is still shorter than the screen."""
        return max(0.0, self.canvas.height - self.height)

    def _push(self, at: float, msg: int, swipe: bool = False) -> State:
        """Record one state beginning at `at`, with the window travelling to wherever
        the canvas now says it belongs."""
        if self.states:
            at = max(at, self.states[-1].start + MIN_STATE_S)
        was, now = self.window, self._rest()
        if self.cfg.scroll != "roll" or now <= was:
            was = now  # a jump, or nothing to follow: the view is simply there
        self.window = now
        state = State(start=at, msg=msg, from_y=was, to_y=now, swipe=swipe,
                      anchor_scene=self._walk_scene(msg))
        self.states.append(state)
        if self.paint is not None:
            self.paint(self.canvas, state)
        return state

    # -- the walk -----------------------------------------------------------

    def plan(self) -> list[State]:
        cfg = self.cfg
        starts = scene_starts(self.job.scenes)
        walk = self._walk()
        for i, ci, msg in walk:
            si = msg.scene
            if not (0 <= si < len(self.job.scenes)):
                continue
            scene = self.job.scenes[si]
            start = starts[si]
            end = start + scene.duration
            # When the reading of this message is over, and how much clock there is
            # between then and the next message. For a spoken line that is the pause
            # after it, which is a scene of its own (see `stages.chat_script.lay`);
            # for one nobody reads, the message's own hold is all there is.
            if scene.silent:
                read_end = start + scene.duration * SILENT_READ
                room = end - read_end
            else:
                read_end = end
                room = (self.job.scenes[si + 1].duration
                        if si + 1 < len(self.job.scenes) and self.job.scenes[si + 1].hush
                        else 0.0)

            seam = self._seam(walk, i, ci)
            if seam or msg.clear_before or self._must_clear():
                self.canvas.clear()
                self.window = 0.0

            person = self._person(msg)
            # whoever was last to speak stops being the end of their own run the
            # moment they say something else (see `Block.show_tail`)
            if self.canvas.blocks and self.canvas.blocks[-1].msg != i:
                prev_block = self.canvas.blocks[-1]
                prev_block.show_tail = prev_block.person.name != person.name
            block = self._lay(msg, i, person, [], walk=walk)
            lines = list(block.lines)
            caps = chunk_lines(lines, cfg.chunk, cfg.chunk_min)
            for n, (cap, at) in enumerate(zip(caps, reveal_times(scene, lines, caps, start, end))):
                self._lay(msg, i, person, [], text="\n".join(lines[:cap]), walk=walk)
                # The swipe belongs to the FIRST state of a new conversation and to no
                # other: that is the moment the screen stops being one chat and starts
                # being the next, and a transition on any later state would be a swipe
                # into a picture the viewer is already looking at.
                self._push(at, i, swipe=bool(seam and n == 0))
            if cfg.reactions and msg.reactions and not self.skin.votes:
                flurry = react_times(read_end, room, len(msg.reactions), cfg.react_s)
                for n, at in enumerate(flurry, start=1):
                    self._lay(msg, i, person, msg.reactions[:n], walk=walk)
                    self._push(at, i)
        self._settle()
        return self.states

    def lay_upto(self, last: int) -> Canvas:
        """Lay the conversation as far as message `last` and hand the canvas back.

        The planner's walk with the clock left out: every message whole, no reveals,
        no states. That is exactly what a preview is — there are no word timings to
        break a long message on until something has voiced it, and the operator asking
        "what will this look like" is asking about the arrangement, not the timing. It
        is a method here rather than a loop in the web layer so that the preview and
        the render agree by construction: a preview computed some cheaper second way
        is a preview that disagrees with the render exactly where it matters."""
        show_reactions = self.cfg.reactions and not self.skin.votes
        walk = self._walk()
        for i, ci, msg in walk[:max(last, 0) + 1]:
            if self._seam(walk, i, ci) or msg.clear_before or self._must_clear():
                self.canvas.clear()
            person = self._person(msg)
            if self.canvas.blocks and self.canvas.blocks[-1].msg != i:
                prev_block = self.canvas.blocks[-1]
                prev_block.show_tail = prev_block.person.name != person.name
            self._lay(msg, i, person, msg.reactions if show_reactions else [], walk=walk)
        return self.canvas

    def _walk_scene(self, ordinal: int) -> int:
        """Which scene the message at this ordinal was laid onto, or -1 before any
        were. Looked up rather than carried, because a state is pushed from several
        places and all of them have the ordinal and none of them have the message."""
        flat = self.job.messages
        return flat[ordinal].scene if 0 <= ordinal < len(flat) else -1

    def _person(self, msg) -> Person:
        """Who this message is from, as the drawing needs them. A name with no card
        behind it is still somebody — a blank person carrying that name — because an
        import names people nobody has carded and the conversation is not wrong."""
        return self.people.get(msg.persona) or Person(name=msg.persona)

    def _seam(self, walk: list, i: int, ci: int) -> bool:
        """Whether this message opens a new conversation — the place a swipe goes.

        Two unrelated pieces of conversation sharing a screen read as one conversation
        that stopped making sense, so a seam always clears, in every scroll mode. The
        first message of the video is not a seam: there is nothing to swipe away
        from."""
        return bool(i) and walk[i - 1][1] != ci

    def _must_clear(self) -> bool:
        """Whether the screen has to be emptied because it has filled up.

        Only in `clear` mode, which is what "the screen fills and starts again" means.
        The other two scroll instead, and the operator's own mark on a message
        (`ChatMsg.clear_before`) is handled beside this rather than inside it, because
        one is a setting about the format and the other is a judgement about a line."""
        return self.cfg.scroll == "clear" and self.canvas.height > self.height

    def _lay(self, msg, i: int, person: Person,
             reactions: list[tuple[str, int]], text: str | None = None,
             walk: list | None = None):
        """Lay (or re-lay) this message as the last block on the canvas.

        Re-laying rather than painting it with a cap, because a bubble is drawn around
        what it currently holds: a message revealed to its first line is a one-line
        bubble, not a four-line bubble with three empty ones in it. The prefix is
        handed over already wrapped, and re-wrapping it gives the same lines back —
        the prefix of a greedy wrap is the greedy wrap of that prefix."""
        walk = walk if walk is not None else self._walk()
        here_c = walk[i][1] if i < len(walk) else 0
        # the message before this one, but only if it is in the SAME conversation: a
        # run of messages from one person cannot continue across a swipe
        prev = walk[i - 1][2] if i and walk[i - 1][1] == here_c else None
        conv = (self.job.conversations[here_c]
                if here_c < len(self.job.conversations) else None)
        kw = dict(
            msg=i, person=person, text=msg.text if text is None else text,
            stamp=msg.stamp, reactions=reactions,
            score=msg.score if self.skin.votes else None,
            depth=self._depth(conv, msg),
            # A messenger folds a run of messages from one person into a block with a
            # single name on it; a comment tree does not, because each comment is its
            # own thing with its own score and its own place in the thread.
            show_head=self.skin.tree or not (
                prev and prev.persona == msg.persona and not msg.clear_before),
            reply=self._reply(conv, msg),
        )
        here = bool(self.canvas.blocks) and self.canvas.blocks[-1].msg == i
        return self.canvas.amend(**kw) if here else self.canvas.append(**kw)

    def _depth(self, conv, msg) -> int:
        """How deep in the comment tree this message sits — reddit only, and zero
        everywhere else, where a reply is a quoted strip rather than an indent.

        Walked inside the conversation, because that is what a reply points into: a
        thread's shape is a fact about that thread."""
        if not self.skin.tree or conv is None:
            return 0
        depth, seen = 0, set()
        at = msg.reply_to
        while 0 <= at < len(conv.messages) and at not in seen and depth < 8:
            seen.add(at)
            depth += 1
            at = conv.messages[at].reply_to
        return depth

    def _reply(self, conv, msg) -> tuple[str, str] | None:
        at = msg.reply_to
        if conv is None or not (0 <= at < len(conv.messages)):
            return None
        parent = conv.messages[at]
        return (parent.nick or parent.persona, parent.text)

    def _settle(self) -> None:
        """Give every state its length, now that the next one's start is known.

        The last one runs to the end of the video rather than to the end of its own
        message: whatever is on screen when the talking stops stays on screen, which
        is what a real client does and what a cut to black would not."""
        total = sum(s.duration for s in self.job.scenes)
        for a, b in zip(self.states, self.states[1:]):
            a.duration = max(b.start - a.start, MIN_STATE_S)
        if self.states:
            last = self.states[-1]
            last.duration = max(total - last.start, MIN_STATE_S)
