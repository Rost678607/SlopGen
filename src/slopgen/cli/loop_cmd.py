"""`slopgen loop` — run generation in a loop, and steer one that is already running.

Two things live here, and they are two halves of the same idea.

**The driver.** `--loop` on any mode command hands the run to `pipeline.loop` instead of
to the orchestrator directly: one video at a time, forever or up to a limit, with the
topic coming from you or from the model. It prints where the loop's plan file is, because
that path is the loop's remote control.

**The remote control.** The subcommands below edit that plan while the loop is running,
from anywhere — another terminal, a script, a cron job. Nothing here talks to the loop
process: it re-reads the plan before every iteration, so an edit lands on the next video
and never in the middle of one.

The terminal gets a third way in, which the browser cannot have: when the topic is yours
to give and the loop is standing in a terminal you are looking at, it ASKS. Type a topic
and that is the next video; press Enter and the model takes this one; type a `!command`
and you have changed the loop without leaving it — the same edits the subcommands make.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from ..config import ConfigError, ConfigStore, RunParams
from ..pipeline.loop import (
    ALIASES,
    apply_settings,
    LoopFile,
    LoopPlan,
    LoopRunner,
    QueueItem,
    all_loops,
    check_params,
    clean_overrides,
    describe,
    direct_launcher,
    latest_loop,
    loop_dir_name,
    overridable,
    parse_setting,
    settable_names,
)

app = typer.Typer(add_completion=False, help="steer a running loop (see `--loop` on a mode)")

# What may be typed at the loop's own prompt instead of a topic. Deliberately marked
# with a `!`: a topic is free text, and "stop" is a perfectly good thing to make a video
# about.
PROMPT_HELP = (
    "[dim]enter = let the model pick · !ai · !me · !limit N · !breaks a,b (!breaks none)"
    " · !park hold|go_on · !ahead N · !queue · !set key=value … · !show · !stop[/dim]"
)


# -- finding the loop -------------------------------------------------------


def _output(store: ConfigStore) -> Path:
    return Path(store.global_cfg.paths.output)


def _file(store: ConfigStore, loop_dir: Optional[Path]) -> LoopFile:
    """The loop being steered: the one named, or the one most recently written to."""
    d = loop_dir or latest_loop(_output(store))
    if d is None:
        typer.secho("no loop found — start one with `--loop` on a mode command", fg="yellow")
        raise typer.Exit(1)
    try:
        return LoopFile.open(d)
    except FileNotFoundError as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)


def _echo(plan: LoopPlan, where: Path) -> None:
    who = "the model picks" if plan.source == "ai" else "yours to give"
    limit = plan.limit or "∞"
    ahead = f" · {plan.ahead} kept ahead" if plan.ahead else ""
    rprint(
        f"[bold]{where.name}[/bold] · [cyan]{plan.status}[/cyan] {plan.note}\n"
        f"  made [bold]{plan.made}[/bold] of {plan.started} started, limit {limit}\n"
        f"  topics: {who} · {len(plan.topics)} queued{ahead}\n"
        f"  breaks: {', '.join(plan.breakpoints) or '—'} · when parked: {plan.on_park}"
    )
    for it in plan.iterations[-8:]:
        rprint(f"  [dim]#{it.n}[/dim] {it.status or 'running':7} {it.topic or '(model)'}")


def _over(item: QueueItem) -> str:
    """One queued video's own settings, short enough to sit on the end of its line."""
    return ", ".join(f"{k}={_short(v)}" for k, v in item.over.items())


def _short(value) -> str:
    if isinstance(value, list):
        return "/".join(str(v) for v in value) or "—"
    if isinstance(value, dict):
        return ",".join(f"{k}:{v}" for k, v in value.items()) or "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _queue(plan: LoopPlan) -> None:
    """The videos not made yet, numbered as the other commands name them."""
    if not plan.topics:
        rprint("[dim]nothing queued[/dim]")
        return
    for n, item in enumerate(plan.topics, 1):
        mark = "[magenta]✨[/magenta]" if item.by == "ai" else "  "
        over = _over(item)
        rprint(f"  [bold]{n:>2}[/bold] {mark} {item.topic or '[dim](the model picks)[/dim]'}"
               + (f"  [cyan]{over}[/cyan]" if over else ""))


def _pick(plan: LoopPlan, which: str) -> list[QueueItem]:
    """The queued videos a `1,3,5-7` (or `all`) names, in the order they are queued.

    Positions rather than ids, because a person reading a numbered list types the
    numbers they can see — the ids exist so that two frontends editing at once cannot
    hit the wrong video, and a terminal reading the list a line before it edits it is
    not in that race."""
    if which.strip().lower() in ("all", "*"):
        return list(plan.topics)
    want: list[int] = []
    for part in which.replace(",", " ").split():
        if "-" in part[1:]:
            lo, _, hi = part.partition("-")
            want += list(range(int(lo), int(hi) + 1))
        else:
            want.append(int(part))
    out = []
    for n in want:
        if not 1 <= n <= len(plan.topics):
            raise ValueError(f"there is no video {n} in the queue (1..{len(plan.topics)})")
        out.append(plan.topics[n - 1])
    return out


# -- the remote control -----------------------------------------------------


@app.command("status")
def status(ctx: typer.Context,
           loop_dir: Optional[Path] = typer.Argument(None, help="a loop's folder; omit for the latest")) -> None:
    """What the loop is doing, and what it has made."""
    f = _file(ctx.obj, loop_dir)
    _echo(f.read(), f.dir)


@app.command("list")
def list_loops(ctx: typer.Context) -> None:
    """Every loop under the output folder, newest first."""
    store: ConfigStore = ctx.obj
    found = all_loops(_output(store))
    if not found:
        rprint("[dim]no loops yet[/dim]")
        return
    for d in found:
        plan = LoopFile(d).read()
        rprint(f"[bold]{d.name}[/bold] [cyan]{plan.status}[/cyan] · "
               f"{plan.made}/{plan.started} made · {d}")


@app.command("topic")
def add_topic(ctx: typer.Context,
              topics: list[str] = typer.Argument(..., help="one or more topics, queued in this order"),
              loop_dir: Optional[Path] = typer.Option(None, "--dir"),
              at: Optional[int] = typer.Option(None, "--at", help="put them at this position instead of the end (1 = next)"),
              mine: bool = typer.Option(False, "--me", help="also hand the topics over to you from now on")) -> None:
    """Queue topics for the next videos. Used whoever picks the rest."""
    f = _file(ctx.obj, loop_dir)
    plan = f.add_topics(list(topics), None if at is None else max(0, at - 1))
    if mine:
        plan = f.write_control(source="me")
    rprint(f"queued {len(topics)} · {len(plan.topics)} waiting")


@app.command("queue")
def show_queue(ctx: typer.Context,
               loop_dir: Optional[Path] = typer.Argument(None, help="a loop's folder; omit for the latest")) -> None:
    """The videos this loop has not made yet, with whatever each of them asks for.

    The numbers are what `edit`, `move`, `drop` and `for` take."""
    f = _file(ctx.obj, loop_dir)
    _queue(f.read())


@app.command("edit")
def edit_topic(ctx: typer.Context,
               n: int = typer.Argument(..., help="which queued video (see `slopgen loop queue`)"),
               topic: str = typer.Argument(..., help="what it is about now; \"\" hands it back to the model"),
               loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Retype one queued video's topic, leaving its own settings alone."""
    f = _file(ctx.obj, loop_dir)
    plan = f.read()
    try:
        item = _pick(plan, str(n))[0]
    except ValueError as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    item.topic = topic
    f.set_queue(plan.topics)
    rprint(f"#{n}: [bold]{topic or '(the model picks)'}[/bold]")


@app.command("move")
def move_topic(ctx: typer.Context,
               n: int = typer.Argument(..., help="which queued video"),
               to: int = typer.Argument(..., help="where it goes; 1 is next"),
               loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Put a queued video somewhere else in the queue."""
    f = _file(ctx.obj, loop_dir)
    plan = f.read()
    try:
        item = _pick(plan, str(n))[0]
    except ValueError as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    _queue(f.move(item.id, max(0, to - 1)))


@app.command("drop")
def drop_topics(ctx: typer.Context,
                which: str = typer.Argument(..., help="which queued videos: 2 · 1,3 · 2-5 · all"),
                loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Take videos off the queue. Nothing already being made is touched."""
    f = _file(ctx.obj, loop_dir)
    plan = f.read()
    try:
        gone = _pick(plan, which)
    except ValueError as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    left = f.drop([i.id for i in gone])
    rprint(f"dropped {len(gone)} · {len(left.topics)} left")


@app.command("for")
def set_over(
    ctx: typer.Context,
    which: str = typer.Argument(..., help="which queued videos: 2 · 1,3 · 2-5 · all"),
    settings: Optional[list[str]] = typer.Argument(None, help="key=value, repeatable: duration=90 breaks=script"),
    clear: Optional[list[str]] = typer.Option(None, "--clear", help="give a setting back to the loop, repeatable"),
    loop_dir: Optional[Path] = typer.Option(None, "--dir"),
) -> None:
    """Give queued videos their OWN answer to a setting — one setting, any number of
    videos, and nothing else about them touched.

    That is the whole shape of it, and it is deliberate: an edit that carried a whole
    form would also carry every setting the operator did not mean to change, and six
    videos would quietly come out identical. `--clear duration` hands the setting back
    to the loop, which is the undo.

        slopgen loop for 1,3 duration=90
        slopgen loop for all breaks=script --clear voice
    """
    store: ConfigStore = ctx.obj
    f = _file(store, loop_dir)
    plan = f.read()
    try:
        chosen = _pick(plan, which)
        # typed values, coerced exactly as `loop set` coerces them, so `breaks=a,b` and
        # `fx=crt=40` mean at a queued video what they mean at a whole loop
        parsed = {k: parse_setting(k, v, store)
                  for k, v in _pairs(list(settings or [])).items()}
        clean_overrides(plan.params, parsed)
    except ValueError as e:
        typer.secho(f"error: {e}", fg="red")
        typer.secho(f"per-video settings: {', '.join(overridable())}", fg="yellow")
        raise typer.Exit(1)
    if not parsed and not clear:
        typer.secho("nothing to change — give key=value or --clear key", fg="yellow")
        raise typer.Exit(1)
    plan = f.patch_queue([i.id for i in chosen], parsed, list(clear or []))
    _queue(plan)


@app.command("ahead")
def set_ahead(ctx: typer.Context,
              n: int = typer.Argument(..., min=0, max=50, help="topics to keep waiting; 0 = invent each one as it is needed"),
              loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """How many topics the model keeps ready in the queue.

    It only applies while the model is the one picking. The point is not speed — it is
    that a topic waiting in the queue can be read, rewritten, reordered or thrown away
    before it becomes a video, and one invented at the moment it is needed cannot."""
    _file(ctx.obj, loop_dir).write_control(ahead=n)
    rprint(f"topics kept ahead: [bold]{n or 'none'}[/bold]")


@app.command("source")
def set_source(ctx: typer.Context,
               who: str = typer.Argument(..., help="ai | me"),
               loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Who picks the topic once the queue runs out: the model, or you."""
    if who not in ("ai", "me"):
        typer.secho("source must be `ai` or `me`", fg="red")
        raise typer.Exit(1)
    _file(ctx.obj, loop_dir).write_control(source=who)
    rprint(f"topics from [bold]{who}[/bold] from the next video on")


@app.command("limit")
def set_limit(ctx: typer.Context,
              n: int = typer.Argument(..., min=0, help="videos to make in all; 0 = no limit"),
              loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Cap the loop, or uncap it. Counted from the loop's first video, so a limit
    already passed ends it after the video being made now."""
    _file(ctx.obj, loop_dir).write_control(limit=n)
    rprint(f"limit [bold]{n or '∞'}[/bold]")


@app.command("breaks")
def set_breaks(ctx: typer.Context,
               stages: Optional[list[str]] = typer.Argument(None, help="stage names; none = no breakpoints"),
               loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """Change which stages stop for review. Takes effect on the next video — a run
    already going keeps the breakpoints it was started with."""
    from ..pipeline import review

    f = _file(ctx.obj, loop_dir)
    mode = f.read().params.mode
    wanted = [s.strip() for s in (stages or []) if s.strip() and s.strip() != "none"]
    unknown = [s for s in wanted if s not in review.available(mode)]
    if unknown:
        typer.secho(f"error: unknown stage(s): {', '.join(unknown)} "
                    f"(available for {mode}: {', '.join(review.available(mode))})", fg="red")
        raise typer.Exit(1)
    _file(ctx.obj, loop_dir).write_control(breakpoints=wanted)
    rprint(f"breaks: [bold]{', '.join(wanted) or '—'}[/bold]")


def _pairs(items: list[str]) -> dict[str, str]:
    """`key=value` arguments as a dict, in the order they were typed."""
    edits: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"expected key=value, got {item!r}")
        edits[key.strip()] = value
    return edits


def _apply(f: LoopFile, store: ConfigStore, edits: dict[str, str]) -> tuple[bool, str]:
    """Apply settings edits to a loop, or say why not — for the subcommand and for the
    prompt alike, because refusing an edit differently depending on where it was typed
    is how two doors end up disagreeing about what a setting is.

    Both checks run before anything is written, and both matter for the same reason: a
    loop is unattended by design, so a value it cannot use would otherwise be discovered
    by a video failing hours later rather than by anyone reading an error. The write
    itself re-applies them against the file as it is at that moment, so an edit made
    from somewhere else in between is not quietly thrown away."""
    before = dict(describe(f.read().params))
    try:
        preview = apply_settings(f.read().params, edits, store)
    except ValueError as e:
        return False, str(e)
    problems = check_params(store, preview)
    if problems:
        return False, "; ".join(problems)
    plan = f.set(edits, store)
    changed = [f"{name}: {before.get(name) or '—'} → {value or '—'}"
               for name, value in describe(plan.params) if value != before.get(name)]
    return True, " · ".join(changed) or "nothing changed"


@app.command("set")
def set_settings(
    ctx: typer.Context,
    settings: list[str] = typer.Argument(..., help="key=value, repeatable: duration=90 style=\"16mm\" fx=crt=40,grain=20"),
    loop_dir: Optional[Path] = typer.Option(None, "--dir"),
) -> None:
    """Change the generation settings of a running loop — any of them.

    Everything the run was started with is a setting here: the length, the look, the
    voice, the cast, the world, the ad, where it publishes. Short names work
    (`duration`, `voice`, `style`, `cast`, `fx`, `breaks`, `source`) and so do the full
    field names; `slopgen loop show` lists the lot with what they are set to.
    """
    store: ConfigStore = ctx.obj
    f = _file(store, loop_dir)
    try:
        edits = _pairs(list(settings))
    except ValueError as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    ok, said = _apply(f, store, edits)
    if not ok:
        typer.secho(f"error: {said}", fg="red")
        typer.secho(f"settings: {', '.join(settable_names())}", fg="yellow")
        raise typer.Exit(1)
    for line in said.split(" · "):
        rprint(f"  [cyan]{line}[/cyan]")
    rprint("[dim]lands on the next video; the one being made keeps what it started with[/dim]")


@app.command("show")
def show_settings(
    ctx: typer.Context,
    loop_dir: Optional[Path] = typer.Argument(None, help="a loop's folder; omit for the latest"),
) -> None:
    """Every generation setting this loop is on, and the name to type to change it."""
    f = _file(ctx.obj, loop_dir)
    plan = f.read()
    short = {v: k for k, v in ALIASES.items()}
    rprint(f"[bold]{f.dir.name}[/bold] · {plan.params.mode} · [dim]slopgen loop set key=value[/dim]")
    for name, value in describe(plan.params):
        alias = f" [dim](or {short[name]})[/dim]" if name in short else ""
        rprint(f"  [bold]{name}[/bold]{alias}: {value or '[dim]—[/dim]'}")


@app.command("park")
def set_park(ctx: typer.Context,
             what: str = typer.Argument(..., help="hold | go_on"),
             loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """What the loop does when a video parks — waiting for a review or for hand-made
    clips. `hold` waits for you to deal with it; `go_on` starts the next one anyway."""
    if what not in ("hold", "go_on"):
        typer.secho("must be `hold` or `go_on`", fg="red")
        raise typer.Exit(1)
    _file(ctx.obj, loop_dir).write_control(on_park=what)
    rprint(f"when parked: [bold]{what}[/bold]")


@app.command("stop")
def stop_loop(ctx: typer.Context,
              loop_dir: Optional[Path] = typer.Option(None, "--dir")) -> None:
    """End the loop after the video it is making now. The video is never torn in half."""
    _file(ctx.obj, loop_dir).write_control(stop=True)
    rprint("[yellow]stopping after the current video[/yellow]")


@app.command("go")
def go(ctx: typer.Context,
       loop_dir: Optional[Path] = typer.Argument(None, help="a loop's folder; omit for the latest")) -> None:
    """Pick a stopped loop back up in this terminal, on the plan it already has."""
    store: ConfigStore = ctx.obj
    f = _file(store, loop_dir)
    f.write_control(stop=False)
    drive(store, f)


# -- the prompt -------------------------------------------------------------


def _command(f: LoopFile, line: str, store: ConfigStore | None = None) -> str:
    """Apply a `!command` typed at the loop's prompt. Returns what to print."""
    word, _, rest = line[1:].strip().partition(" ")
    rest = rest.strip()
    if word in ("ai", "me"):
        f.write_control(source=word)
        return f"topics from {word}"
    if word == "limit":
        f.write_control(limit=max(0, int(rest or 0)))
        return f"limit {int(rest or 0) or '∞'}"
    if word in ("breaks", "break"):
        names = [] if rest in ("", "none", "-") else [s.strip() for s in rest.replace(",", " ").split()]
        f.write_control(breakpoints=names)
        return f"breaks: {', '.join(names) or '—'}"
    if word == "park":
        if rest not in ("hold", "go_on"):
            return "park takes `hold` or `go_on`"
        f.write_control(on_park=rest)
        return f"when parked: {rest}"
    if word == "ahead":
        n = max(0, min(int(rest or 0), 50))
        f.write_control(ahead=n)
        return f"topics kept ahead: {n or 'none'}"
    if word == "queue":
        _queue(f.read())
        return "— `slopgen loop for 1,3 duration=90` gives one of them its own settings"
    if word == "stop":
        f.write_control(stop=True)
        return "stopping"
    if word == "set":
        if store is None:
            return "settings can only be changed where the config library is"
        try:
            # shlex, so a look written in words survives: !set style="16mm, sodium light"
            edits = _pairs(shlex.split(rest))
        except ValueError as e:
            return f"no: {e}"
        ok, said = _apply(f, store, edits)
        return said if ok else f"no: {said}"
    if word == "show":
        for name, value in describe(f.read().params):
            if value:
                rprint(f"  [bold]{name}[/bold]: {value}")
        return "— everything else is empty; `slopgen loop show` lists it all"
    return f"unknown command: !{word}"


def _asker(f: LoopFile, store: ConfigStore | None = None):
    """Ask the operator for the next topic, on the terminal the loop is standing in.

    Returns the topic, "" to let the model take this one, or None when the line was a
    command — the loop then makes its decision again, which is what makes `!ai` and
    `!stop` felt immediately rather than at the next video."""

    def ask(plan: LoopPlan) -> str | None:
        left = f"{plan.limit - plan.started} left" if plan.limit else "no limit"
        rprint(f"\n[bold]topic for video #{plan.started + 1}[/bold] [dim]({left})[/dim]")
        rprint(PROMPT_HELP)
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            f.write_control(stop=True)
            rprint("[yellow]stopping[/yellow]")
            return None
        if line.startswith("!"):
            rprint(f"[dim]{_command(f, line, store)}[/dim]")
            return None
        return line  # "" = the model picks this one

    return ask


# -- the driver -------------------------------------------------------------


def _event(state: str, message: str) -> None:
    # `t` is the identity for anything that is not a label key, so a note carrying a
    # run name passes through and a fixed one arrives translated
    from ..labels import t

    colour = {"waiting": "yellow", "held": "yellow", "failed": "red",
              "stopped": "yellow", "done": "green"}.get(state, "cyan")
    rprint(f"[{colour}]loop · {state}[/{colour}] {t(message)}")


def start(store: ConfigStore, params: RunParams, *, source: str, limit: int,
          topics: Optional[list[str]] = None, on_park: str = "hold",
          ahead: int = 0) -> None:
    """Begin a loop on these run settings and drive it from this terminal."""
    if source not in ("ai", "me"):
        typer.secho("--topics must be `ai` or `me`", fg="red")
        raise typer.Exit(1)
    if on_park not in ("hold", "go_on"):
        typer.secho("--on-park must be `hold` or `go_on`", fg="red")
        raise typer.Exit(1)
    loop_dir = _output(store) / loop_dir_name(params)
    f = LoopFile.create(
        loop_dir, params, source=source, limit=limit, on_park=on_park,
        breakpoints=list(params.breakpoints), ahead=max(0, min(ahead, 50)),
        topics=[t for t in (topics or []) if t.strip()],
    )
    rprint(f"[bold]loop[/bold] in [cyan]{loop_dir}[/cyan] · "
           f"{'no limit' if not limit else f'{limit} videos'} · topics from "
           f"{'the model' if source == 'ai' else 'you'}"
           + (f", {ahead} kept ahead" if ahead else ""))
    rprint(f"[dim]steer it from anywhere: slopgen loop status | queue | topic \"...\" | "
           f"for 1,3 duration=90 | source ai|me | limit N | breaks ... | stop[/dim]")
    drive(store, f)


def drive(store: ConfigStore, f: LoopFile) -> None:
    """Run the loop here, printing what every video does as it does it."""
    from ..pipeline.topics import proposer
    from .app import _console_event

    # Asking only makes sense on a terminal a person is standing at. Piped into a file
    # or a cron job, the loop waits on the queue instead — same loop, one fewer way in.
    ask = _asker(f, store) if sys.stdin.isatty() else None
    runner = LoopRunner(
        f,
        launch=direct_launcher(store, on_event=_console_event),
        on_event=_event,
        ask=ask,
        propose=proposer(store),
    )
    try:
        plan = runner.run()
    except KeyboardInterrupt:
        f.write_control(stop=True)
        rprint("\n[yellow]loop stopped[/yellow] — the video being made is left where it is; "
               "`slopgen --resume` picks it up")
        raise typer.Exit(130)
    rprint(f"\n[bold]loop {plan.status}[/bold] — {plan.made} made, {plan.started} started")
    parked = [it for it in plan.iterations if it.status in ("review", "paused")]
    if parked:
        rprint(f"[yellow]{len(parked)} video(s) still waiting on you:[/yellow]")
        for it in parked:
            what = "slopgen review" if it.status == "review" else "slopgen gather"
            rprint(f"  #{it.n}: [bold]{what} {it.run_dir}[/bold]")
