"""CLI entrypoint.

A mode is chosen first (before the language), and it shapes the rest of the line:

    slopgen                                     -> launch the TUI
    slopgen info ru story                       -> the minute-of-info clip
    slopgen info en cyber --ad example_vpn --ad-mode overlay --push yt_main -n 5
    slopgen drama ru --scenario "..." --cast example --duration-min 2 --tol 20 --parts 3
    slopgen drama ru --parts 3 --parts-at-once   -> cut all three only when every clip is in
    slopgen drama en --orchestration my_chain --ad example_vpn

    slopgen info ru facts --break script --break tts   -> stop for review after those stages

    slopgen --preset daily_en                   -> everything from a preset (info)
    slopgen --resume output/20260709_...        -> continue a crashed run
    slopgen gather [output/2026...]             -> add user-assisted clips, then resume
                                                   (one finished part is enough — it gets cut and published,
                                                    the rest of the drama waits for its clips)
    slopgen review [output/2026...]             -> inspect/edit a breakpoint, then resume
    slopgen --list-types / --list-ads / --list-accounts / --list-presets
            / --list-visuals / --list-characters / --list-orchestrations
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from ..config import ConfigError, ConfigStore, RunParams
from ..pipeline import Orchestrator
from ..pipeline.context import AppContext

app = typer.Typer(add_completion=False, rich_markup_mode="rich")

STATUS_ICON = {
    "start": "…", "done": "[green]✔[/green]", "error": "[red]✘[/red]",
    "skip": "[yellow]↷[/yellow]", "paused": "[yellow]⏸[/yellow]", "review": "[yellow]⏸[/yellow]",
}


def _check_breakpoints(names: Optional[list[str]], mode: str) -> list[str]:
    """Validate --break stage names against the chain this mode actually runs."""
    from ..pipeline import review

    wanted = [n.strip() for n in (names or []) if n.strip()]
    unknown = [n for n in wanted if n not in review.available(mode)]
    if unknown:
        typer.secho(
            f"error: unknown breakpoint stage(s): {', '.join(unknown)} "
            f"(available for {mode}: {', '.join(review.available(mode))})",
            fg="red",
        )
        raise typer.Exit(1)
    return wanted


# -- shared output ----------------------------------------------------------


def _console_event(i: int, stage: str, status: str, message: str) -> None:
    from rich import print as rprint

    icon = STATUS_ICON.get(status, "·")
    msg = f" [dim]{message}[/dim]" if message and status != "error" else ""
    if status == "error":
        rprint(f"[red]video {i}: FAILED at {stage}[/red]\n{message}")
    elif status != "start":
        rprint(f"video {i} · {stage} {icon}{msg}")


def _indices_with_status(run_dir: Optional[Path], status: str) -> list[int]:
    if run_dir is None:
        return []
    from ..pipeline.checkpoint import Checkpoint

    try:
        cp = Checkpoint.load(run_dir)
    except Exception:
        return []
    return [i for i in range(cp.params.count) if cp.status(i) == status]


def _paused_indices(run_dir: Optional[Path]) -> list[int]:
    """Job indices parked awaiting manual clips (see pipeline/manual.py)."""
    return _indices_with_status(run_dir, "paused")


def _review_indices(run_dir: Optional[Path]) -> list[int]:
    """Job indices parked on a breakpoint (see pipeline/review.py)."""
    return _indices_with_status(run_dir, "review")


def _paused_note(run_dir: Optional[Path], index: int) -> str:
    """What a parked job is waiting for, as the checkpoint recorded it."""
    if run_dir is None:
        return ""
    from ..pipeline.checkpoint import Checkpoint

    try:
        return Checkpoint.load(run_dir).manual_msg(index)
    except Exception:
        return ""


def _report(jobs, orch) -> None:
    """Print the run summary; point at `gather` for paused jobs and `--resume` for
    failed ones."""
    from rich import print as rprint

    # a drama that has published its first episode is NOT done — it still owes the
    # ones whose clips have not arrived. Its links are printed all the same.
    ok = [j for j in jobs if j.published and not j.pending_parts]
    rprint(f"\n[bold]{len(ok)}/{len(jobs)} videos done[/bold]")
    for j in jobs:
        for line in str(j.published).splitlines():
            rprint(f"  {line}")

    parked = _review_indices(orch.run_dir)
    if parked:
        rprint(
            f"\n[yellow]{len(parked)} video(s) stopped at a breakpoint.[/yellow] "
            f"Check them, then it resumes: [bold]slopgen review {orch.run_dir}[/bold]"
        )
    paused = _paused_indices(orch.run_dir)
    if paused:
        rprint(
            f"\n[yellow]{len(paused)} video(s) need hand-made clips.[/yellow] "
            f"Add them, then it resumes: [bold]slopgen gather {orch.run_dir}[/bold]"
        )
        # a drama parks between episodes too, and what it already cut is listed above
        for i in paused:
            note = _paused_note(orch.run_dir, i)
            if note:
                rprint(f"  [dim]video {i}: {note}[/dim]")
    failed = len(jobs) - len(ok) - len(paused) - len(parked)
    if failed > 0:
        if orch.run_dir is not None:
            rprint(f"\n[yellow]to resume the unfinished videos:[/yellow] slopgen --resume {orch.run_dir}")
        raise typer.Exit(2)


def _execute(store: ConfigStore, params: RunParams) -> None:
    try:
        ctx = AppContext(store=store, params=params)
    except (ConfigError, Exception) as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    orch = Orchestrator(ctx, on_event=_console_event)
    jobs = orch.run()
    _report(jobs, orch)


# -- lists ------------------------------------------------------------------


def _print_lists(store: ConfigStore, **flags: bool) -> None:
    from rich import print as rprint

    if flags.get("types"):
        rprint("[bold]content types:[/bold]")
        for name, ct in store.content_types.items():
            rprint(f"  {name} ({', '.join(ct.voices)}) — {ct.description}")
    if flags.get("characters"):
        rprint("[bold]characters:[/bold]")
        for name, c in store.characters.items():
            rprint(f"  {name} (age {c.age or '?'}) — {(c.appearance or '—')[:60]}")
    if flags.get("orchestrations"):
        rprint("[bold]orchestrations:[/bold]")
        for name, o in store.orchestrations.items():
            chain = " → ".join(
                f"{s.model}({s.amount:g}{s.metric[:1]}"
                + (f", {s.clip_seconds:g}s clips" if s.clip_seconds else "") + ")"
                for s in o.stages
            ) or "—"
            rprint(f"  {name}: {chain}")
    if flags.get("ads"):
        rprint("[bold]ad contracts:[/bold]")
        for name, ad in store.ads.items():
            rprint(f"  {name} (modes: {', '.join(ad.modes)}) — {ad.url}")
    if flags.get("accounts"):
        rprint("[bold]accounts:[/bold]")
        for name, acc in store.accounts.items():
            rprint(f"  {name} ({acc.platform})")
    if flags.get("presets"):
        rprint("[bold]presets:[/bold]")
        for name, p in store.presets.items():
            rprint(f"  {name}: {p.lang} {p.content_type} ad={p.ad or '-'} push={p.push or 'local'} count={p.count}")
    if flags.get("visuals"):
        rprint("[bold]visuals profiles:[/bold]")
        for name, v in store.visuals.items():
            fg = "+fg" if v.foreground.enabled else ""
            rprint(f"  {name}: bg={v.background.source}/{v.background.linkage} {fg} — {v.description}")


# -- top-level callback: TUI / lists / resume, then hand off to a subcommand -


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    resume: Optional[Path] = typer.Option(None, "--resume", help="continue a crashed run from its output dir (the folder with checkpoint.json)"),
    list_types: bool = typer.Option(False, "--list-types"),
    list_ads: bool = typer.Option(False, "--list-ads"),
    list_accounts: bool = typer.Option(False, "--list-accounts"),
    list_presets: bool = typer.Option(False, "--list-presets"),
    list_visuals: bool = typer.Option(False, "--list-visuals"),
    list_characters: bool = typer.Option(False, "--list-characters"),
    list_orchestrations: bool = typer.Option(False, "--list-orchestrations"),
) -> None:
    load_dotenv()
    try:
        store = ConfigStore()
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red")
        raise typer.Exit(1)
    ctx.obj = store

    lists = dict(
        types=list_types, ads=list_ads, accounts=list_accounts, presets=list_presets,
        visuals=list_visuals, characters=list_characters, orchestrations=list_orchestrations,
    )
    if any(lists.values()):
        _print_lists(store, **lists)
        raise typer.Exit()

    if resume:
        from rich import print as rprint

        from ..pipeline.checkpoint import Checkpoint

        try:
            cp = Checkpoint.load(resume)
            actx = AppContext(store=store, params=cp.params)
        except (FileNotFoundError, ConfigError, Exception) as e:
            typer.secho(f"error: {e}", fg="red")
            raise typer.Exit(1)
        rprint(f"[bold]slopgen[/bold]: resuming [cyan]{resume}[/cyan]")
        orch = Orchestrator(actx, on_event=_console_event)
        jobs = orch.run(resume_dir=resume)
        _report(jobs, orch)
        raise typer.Exit()

    # no subcommand and nothing else to do -> interactive TUI
    if ctx.invoked_subcommand is None:
        from ..tui.app import SlopgenApp

        SlopgenApp(store).run()
        raise typer.Exit()


# -- info mode --------------------------------------------------------------


@app.command()
def info(
    ctx: typer.Context,
    lang: Optional[str] = typer.Argument(None, help="content language, e.g. ru / en"),
    content_type: Optional[str] = typer.Argument(None, help="content type, e.g. story / cyber / psych / facts; omit for any topic"),
    idea: Optional[str] = typer.Option(None, "--idea", help="your own topic; omit to let the LLM invent one"),
    ad: Optional[str] = typer.Option(None, "--ad", help="ad contract name from configs/ads/"),
    ad_mode: Optional[str] = typer.Option(None, "--ad-mode", help="overlay | native | both"),
    visuals: Optional[str] = typer.Option(None, "--visuals", help="visuals profile from configs/visuals/"),
    duration: Optional[float] = typer.Option(None, "--duration", help="target spoken length, seconds"),
    profanity: Optional[int] = typer.Option(None, "--profanity", min=0, max=100, help="swearing level 0 (clean) - 100 (constant)"),
    push: Optional[str] = typer.Option(None, "--push", help="account from configs/accounts/; omit to save locally"),
    count: Optional[int] = typer.Option(None, "--count", "-n", help="videos to generate"),
    preset: Optional[str] = typer.Option(None, "--preset", help="preset from configs/presets/"),
    out: Optional[Path] = typer.Option(None, "--out", help="output dir override"),
    subs: Optional[str] = typer.Option(None, "--subs", help="subtitle style: word_pop | phrases | karaoke"),
    tts_rate: Optional[int] = typer.Option(None, "--tts-rate", min=-50, max=50, help="speech rate offset in percent (-50 = slowest, 0 = normal, +50 = fastest)"),
    breaks: Optional[list[str]] = typer.Option(None, "--break", "-b", help="stop for review after this stage (repeatable): idea | script | tts | footage | subtitles | assemble | metadata"),
    clean_subs: bool = typer.Option(False, "--clean-subs", help="swap profanity out of the burned-in subtitles; the voiceover keeps every word"),
    dry_run: bool = typer.Option(False, "--dry-run", help="generate everything but skip publishing"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="keep intermediate ffmpeg files"),
) -> None:
    """Generate the minute-of-info clip (idea → script → voiceover → footage)."""
    from rich import print as rprint

    store: ConfigStore = ctx.obj
    breakpoints = _check_breakpoints(breaks, "info")
    try:
        params = store.resolve(
            lang=lang, content_type=content_type, ad=ad, ad_mode=ad_mode,
            visuals=visuals, duration_s=duration, profanity=profanity,
            push=push, count=count, preset=preset, idea=idea or "",
            out=out, dry_run=dry_run, keep_temp=keep_temp, subtitle_style=subs,
            tts_rate=tts_rate or 0, breakpoints=breakpoints, clean_subtitles=clean_subs,
        )
    except (ConfigError, Exception) as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    rprint(
        f"[bold]slopgen[/bold]: {params.count}× {params.lang}/{params.content_type or 'auto'}"
        f" visuals={params.visuals} ~{params.duration_s:.0f}s"
        f" ad={params.ad or '-'}({params.ad_mode}) push={params.push or 'local'}"
        + (" [yellow]\\[dry-run][/yellow]" if params.dry_run else "")
    )
    _execute(store, params)


# -- drama mode -------------------------------------------------------------


@app.command()
def drama(
    ctx: typer.Context,
    lang: str = typer.Argument(..., help="narration language, e.g. ru / en"),
    scenario: Optional[str] = typer.Option(None, "--scenario", help="the plot/premise; omit to let the LLM invent one"),
    cast: Optional[str] = typer.Option(None, "--cast", help="comma-separated character names from configs/characters/"),
    orchestration: Optional[str] = typer.Option(None, "--orchestration", help="generator chain from configs/orchestration/ (default: one wan2.1 stage)"),
    duration_min: float = typer.Option(2.0, "--duration-min", help="target length in minutes"),
    tol: float = typer.Option(15.0, "--tol", help="allowed over/under-run, seconds"),
    clip_s: float = typer.Option(0.0, "--clip-s", help="average length of ONE generated clip, seconds (0 = each generator's own); clips of 8s+ are written as multi-scene sequences"),
    parts: int = typer.Option(1, "--parts", min=1, help="ask the writer for this many cliffhanger parts; the boundaries are then movable at the script/cut breakpoints"),
    parts_at_once: bool = typer.Option(False, "--parts-at-once", help="cut every part together at the end instead of finishing each one as soon as its own clips are in"),
    voice: Optional[str] = typer.Option(None, "--voice", help="edge-tts narrator voice id (default per language)"),
    tts_rate: Optional[int] = typer.Option(None, "--tts-rate", min=-50, max=50, help="speech rate offset in percent (-50 = slowest, 0 = normal, +50 = fastest); the writer sizes each beat's narration to it"),
    ad: Optional[str] = typer.Option(None, "--ad", help="ad contract name from configs/ads/"),
    ad_mode: str = typer.Option("both", "--ad-mode", help="overlay | native | both"),
    profanity: int = typer.Option(0, "--profanity", min=0, max=100, help="swearing level 0-100"),
    push: Optional[str] = typer.Option(None, "--push", help="account from configs/accounts/; omit to save locally"),
    count: int = typer.Option(1, "--count", "-n", help="videos to generate"),
    out: Optional[Path] = typer.Option(None, "--out", help="output dir override"),
    subs: Optional[str] = typer.Option(None, "--subs", help="subtitle style: word_pop | phrases | karaoke"),
    breaks: Optional[list[str]] = typer.Option(None, "--break", "-b", help="stop for review after this stage (repeatable): script | entities | tts | cut | footage | subtitles | assemble | metadata"),
    clean_subs: bool = typer.Option(False, "--clean-subs", help="swap profanity out of the burned-in subtitles; the voiceover keeps every word"),
    visual_notes: Optional[str] = typer.Option(None, "--visual-notes", help="constraints on what the shots may SHOW, never on the story: \"all weapons are toy ones\", \"no blood\""),
    dry_run: bool = typer.Option(False, "--dry-run", help="generate everything but skip publishing"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="keep intermediate ffmpeg files"),
) -> None:
    """Generate an AI web drama: a narrated story with a recurring cast and
    AI-generated shots orchestrated across free generators."""
    from rich import print as rprint

    store: ConfigStore = ctx.obj
    breakpoints = _check_breakpoints(breaks, "drama")
    # resolve the cast by name
    names = [n.strip() for n in (cast or "").split(",") if n.strip()]
    missing = [n for n in names if n not in store.characters]
    if missing:
        typer.secho(
            f"error: unknown character(s): {', '.join(missing)} "
            f"(available: {', '.join(store.characters) or 'none'})",
            fg="red",
        )
        raise typer.Exit(1)
    if orchestration and orchestration not in store.orchestrations:
        typer.secho(
            f"error: orchestration '{orchestration}' not found "
            f"(available: {', '.join(store.orchestrations) or 'none'})",
            fg="red",
        )
        raise typer.Exit(1)
    if ad and ad not in store.ads:
        typer.secho(f"error: ad contract '{ad}' not found (available: {', '.join(store.ads)})", fg="red")
        raise typer.Exit(1)

    try:
        params = RunParams(
            lang=lang, content_type="", mode="drama",
            scenario=scenario or "",
            manual_cast=[store.characters[n] for n in names],
            orchestration=orchestration or "",
            duration_s=max(duration_min, 0.1) * 60.0,
            duration_tol_s=max(tol, 0.0),
            clip_seconds=max(clip_s, 0.0),
            parts=max(1, parts),
            parts_iterative=not parts_at_once,
            profanity=profanity,
            ad=ad or "", ad_mode=ad_mode,
            push=push or "", count=max(1, count),
            voice_override=voice or "", tts_rate=tts_rate or 0,
            out=out, dry_run=dry_run, keep_temp=keep_temp, subtitle_style=subs,
            breakpoints=breakpoints, clean_subtitles=clean_subs,
            visual_notes=visual_notes or "",
        )
    except Exception as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    rprint(
        f"[bold]slopgen[/bold] drama: {params.count}× {params.lang}"
        f" ~{params.duration_s / 60:.1f}min ±{params.duration_tol_s:.0f}s"
        + (f" clip~{params.clip_seconds:g}s" if params.clip_seconds else "")
        + (f" parts={params.parts}" if params.parts != 1 else "")
        + ("" if params.parts_iterative else " [dim](all at the end)[/dim]")
        + f" cast=[{', '.join(names) or '—'}] orch={orchestration or 'default'}"
        f" ad={params.ad or '-'}({params.ad_mode}) push={params.push or 'local'}"
        + (" [yellow]\\[dry-run][/yellow]" if params.dry_run else "")
    )
    _execute(store, params)


# -- fandom mode ------------------------------------------------------------


@app.command()
def fandom(
    ctx: typer.Context,
    lang: str = typer.Argument(..., help="narration language, e.g. ru / en"),
    world: str = typer.Argument(..., metavar="FANDOM", help="folder name under configs/fandoms/"),
    scenario: Optional[str] = typer.Option(None, "--scenario", help="what to tell about this world, or which theory to argue; omit to let the LLM pick"),
    narrator: str = typer.Option("resident", "--narrator", help="who tells it: resident (lives there, first person) | chronicler (studies its records, builds theories)"),
    medium: str = typer.Option("video", "--medium", help="what the picture is made of: video (clips) | photo (a slideshow of stills, held and slowly panned)"),
    source: Optional[str] = typer.Option(None, "--source", help="what makes the shots: a generator (wan2.1 | ltx-video | animatediff for video, flux | turbo for photo), `manual` (you generate them) or `search` (you find them; slopgen briefs you per shot). Default: wan2.1 for video, flux for photo"),
    orchestration: Optional[str] = typer.Option(None, "--orchestration", help="a full chain from configs/orchestration/, overriding --source when you want to mix"),
    duration: float = typer.Option(120.0, "--duration", help="length of the finished video, in seconds"),
    voice: Optional[str] = typer.Option(None, "--voice", help="edge-tts narrator voice id (default per language)"),
    tts_rate: Optional[int] = typer.Option(None, "--tts-rate", min=-50, max=50, help="speech rate offset in percent (-50 = slowest, 0 = normal, +50 = fastest); the writer sizes each beat's narration to it"),
    ad: Optional[str] = typer.Option(None, "--ad", help="ad contract name from configs/ads/"),
    ad_mode: str = typer.Option("both", "--ad-mode", help="overlay | native | both"),
    profanity: int = typer.Option(0, "--profanity", min=0, max=100, help="swearing level 0-100"),
    push: Optional[str] = typer.Option(None, "--push", help="account from configs/accounts/; omit to save locally"),
    count: int = typer.Option(1, "--count", "-n", help="videos to generate"),
    out: Optional[Path] = typer.Option(None, "--out", help="output dir override"),
    subs: Optional[str] = typer.Option(None, "--subs", help="subtitle style: word_pop | phrases | karaoke"),
    breaks: Optional[list[str]] = typer.Option(None, "--break", "-b", help="stop for review after this stage (repeatable): canon | script | entities | tts | footage | subtitles | assemble | metadata"),
    clean_subs: bool = typer.Option(False, "--clean-subs", help="swap profanity out of the burned-in subtitles; the voiceover keeps every word"),
    visual_notes: Optional[str] = typer.Option(None, "--visual-notes", help="constraints on what the shots may SHOW, never on the story: \"no logos\", \"no blood\""),
    dry_run: bool = typer.Option(False, "--dry-run", help="generate everything but skip publishing"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="keep intermediate ffmpeg files"),
) -> None:
    """Generate a video set inside a world you wrote down: the narrator treats that
    world as the real one they live in, never as fiction being described.

    One video, not a serial — episodes are the drama's device."""
    from rich import print as rprint

    store: ConfigStore = ctx.obj
    breakpoints = _check_breakpoints(breaks, "fandom")
    if world not in store.fandoms:
        typer.secho(
            f"error: fandom '{world}' not found "
            f"(available: {', '.join(store.fandoms) or 'none'}) — "
            f"a fandom is a folder at configs/fandoms/<name>/ with markdown lore in it",
            fg="red",
        )
        raise typer.Exit(1)
    if narrator not in ("resident", "chronicler"):
        typer.secho(f"error: --narrator must be 'resident' or 'chronicler', not '{narrator}'", fg="red")
        raise typer.Exit(1)
    if orchestration and orchestration not in store.orchestrations:
        typer.secho(
            f"error: orchestration '{orchestration}' not found "
            f"(available: {', '.join(store.orchestrations) or 'none'})",
            fg="red",
        )
        raise typer.Exit(1)
    if ad and ad not in store.ads:
        typer.secho(f"error: ad contract '{ad}' not found (available: {', '.join(store.ads)})", fg="red")
        raise typer.Exit(1)
    if medium not in ("video", "photo"):
        typer.secho(f"error: --medium must be video or photo, not '{medium}'", fg="red")
        raise typer.Exit(1)
    # The chain is what the pipeline runs on; --source is the one question this mode
    # asks instead of authoring one (see tui FandomScreen._source_stage). A named
    # profile still wins, for the operator who does want to mix sources.
    manual_orch = None
    if not orchestration:
        from ..config.models import OrchestrationConfig, OrchestrationStage
        from ..media.generate import PHOTO_MODELS, VIDEO_MODELS

        allowed = (set(PHOTO_MODELS) if medium == "photo" else set(VIDEO_MODELS)) | {"manual", "search"}
        src = source or ("flux" if medium == "photo" else "wan2.1")
        if src not in allowed:
            typer.secho(
                f"error: '{src}' cannot make {medium} (available: {', '.join(sorted(allowed))})",
                fg="red",
            )
            raise typer.Exit(1)
        manual_orch = OrchestrationConfig(name="source", stages=[OrchestrationStage(
            model=src, metric="percent", amount=100.0,
        )])

    try:
        params = RunParams(
            lang=lang, content_type="", mode="fandom",
            manual_orchestration=manual_orch, medium=medium,
            fandom=world, fandom_voice=narrator,
            scenario=scenario or "",
            orchestration=orchestration or "",
            duration_s=max(duration, 5.0),
            duration_tol_s=0.0,   # the length is the length
            clip_seconds=0.0,     # the writer sizes every shot (fandom_script.SHOT_RULE)
            profanity=profanity,
            ad=ad or "", ad_mode=ad_mode,
            push=push or "", count=max(1, count),
            voice_override=voice or "", tts_rate=tts_rate or 0,
            out=out, dry_run=dry_run, keep_temp=keep_temp, subtitle_style=subs,
            breakpoints=breakpoints, clean_subtitles=clean_subs,
            visual_notes=visual_notes or "",
        )
    except Exception as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    fandom_cfg = store.fandoms[world]
    rprint(
        f"[bold]slopgen[/bold] fandom '{world}': {params.count}× {params.lang}"
        f" {params.duration_s:.0f}s {medium}"
        + f" narrator={narrator}"
        f" people=[{', '.join(c.name for c in fandom_cfg.cast) or '—'}]"
        + (f" orch={orchestration}" if orchestration
           else f" source={manual_orch.stages[0].model}")
        + f" ad={params.ad or '-'}({params.ad_mode}) push={params.push or 'local'}"
        + (" [yellow]\\[dry-run][/yellow]" if params.dry_run else "")
    )
    _execute(store, params)


# -- user-assisted clip gathering -------------------------------------------


def _latest_run_with(store: ConfigStore, indices) -> Optional[Path]:
    """Most recently touched run under the output dir that has a job in that state."""
    base = Path(store.global_cfg.paths.output)
    if not base.is_dir():
        return None
    runs = [d for d in base.iterdir() if d.is_dir() and indices(d)]
    return max(runs, key=lambda d: d.stat().st_mtime, default=None)


def _open_parked(store: ConfigStore, run_dir: Optional[Path], indices, what: str) -> None:
    """Open the TUI on a parked run (the screen follows what it is waiting for)."""
    rd = run_dir or _latest_run_with(store, indices)
    if rd is None:
        typer.secho(f"no run is waiting for {what}", fg="yellow")
        raise typer.Exit(1)
    if not indices(rd):
        typer.secho(f"{rd} has no jobs waiting for {what}", fg="yellow")
        raise typer.Exit(1)
    from ..tui.app import SlopgenApp

    SlopgenApp(store, open_dir=rd).run()


@app.command()
def gather(
    ctx: typer.Context,
    run_dir: Optional[Path] = typer.Argument(None, help="a run's output dir; omit for the latest run awaiting manual clips"),
) -> None:
    """Fill hand-made clips for a paused run, then resume it (opens the TUI)."""
    _open_parked(ctx.obj, run_dir, _paused_indices, "manual clips")


@app.command()
def review(
    ctx: typer.Context,
    run_dir: Optional[Path] = typer.Argument(None, help="a run's output dir; omit for the latest run stopped at a breakpoint"),
) -> None:
    """Inspect and edit what a stage produced at a breakpoint, then resume the run
    (opens the TUI)."""
    _open_parked(ctx.obj, run_dir, _review_indices, "a breakpoint review")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
