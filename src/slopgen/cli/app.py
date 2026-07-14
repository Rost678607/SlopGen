"""CLI entrypoint.

A mode is chosen first (before the language), and it shapes the rest of the line:

    slopgen                                     -> launch the TUI
    slopgen info ru story                       -> the minute-of-info clip
    slopgen info en cyber --ad example_vpn --ad-mode overlay --push yt_main -n 5
    slopgen drama ru --scenario "..." --cast example --duration-min 2 --tol 20 --parts 3
    slopgen drama en --orchestration my_chain --ad example_vpn

    slopgen --preset daily_en                   -> everything from a preset (info)
    slopgen --resume output/20260709_...        -> continue a crashed run
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

STATUS_ICON = {"start": "…", "done": "[green]✔[/green]", "error": "[red]✘[/red]", "skip": "[yellow]↷[/yellow]"}


# -- shared output ----------------------------------------------------------


def _console_event(i: int, stage: str, status: str, message: str) -> None:
    from rich import print as rprint

    icon = STATUS_ICON.get(status, "·")
    msg = f" [dim]{message}[/dim]" if message and status != "error" else ""
    if status == "error":
        rprint(f"[red]video {i}: FAILED at {stage}[/red]\n{message}")
    elif status != "start":
        rprint(f"video {i} · {stage} {icon}{msg}")


def _report(jobs, orch) -> None:
    """Print the run summary and, if anything failed, how to resume it."""
    from rich import print as rprint

    ok = [j for j in jobs if j.published]
    rprint(f"\n[bold]{len(ok)}/{len(jobs)} videos done[/bold]")
    for j in ok:
        for line in str(j.published).splitlines():
            rprint(f"  {line}")
    if len(ok) < len(jobs):
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
            chain = " → ".join(f"{s.model}({s.amount:g}{s.metric[:1]})" for s in o.stages) or "—"
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
    dry_run: bool = typer.Option(False, "--dry-run", help="generate everything but skip publishing"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="keep intermediate ffmpeg files"),
) -> None:
    """Generate the minute-of-info clip (idea → script → voiceover → footage)."""
    from rich import print as rprint

    store: ConfigStore = ctx.obj
    try:
        params = store.resolve(
            lang=lang, content_type=content_type, ad=ad, ad_mode=ad_mode,
            visuals=visuals, duration_s=duration, profanity=profanity,
            push=push, count=count, preset=preset, idea=idea or "",
            out=out, dry_run=dry_run, keep_temp=keep_temp, subtitle_style=subs,
            tts_rate=tts_rate or 0,
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
    parts: int = typer.Option(1, "--parts", min=1, help="split one drama into this many cliffhanger parts"),
    voice: Optional[str] = typer.Option(None, "--voice", help="edge-tts narrator voice id (default per language)"),
    ad: Optional[str] = typer.Option(None, "--ad", help="ad contract name from configs/ads/"),
    ad_mode: str = typer.Option("both", "--ad-mode", help="overlay | native | both"),
    profanity: int = typer.Option(0, "--profanity", min=0, max=100, help="swearing level 0-100"),
    push: Optional[str] = typer.Option(None, "--push", help="account from configs/accounts/; omit to save locally"),
    count: int = typer.Option(1, "--count", "-n", help="videos to generate"),
    out: Optional[Path] = typer.Option(None, "--out", help="output dir override"),
    subs: Optional[str] = typer.Option(None, "--subs", help="subtitle style: word_pop | phrases | karaoke"),
    dry_run: bool = typer.Option(False, "--dry-run", help="generate everything but skip publishing"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="keep intermediate ffmpeg files"),
) -> None:
    """Generate an AI web drama: a narrated story with a recurring cast and
    AI-generated shots orchestrated across free generators."""
    from rich import print as rprint

    store: ConfigStore = ctx.obj
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
            parts=max(1, parts),
            profanity=profanity,
            ad=ad or "", ad_mode=ad_mode,
            push=push or "", count=max(1, count),
            voice_override=voice or "",
            out=out, dry_run=dry_run, keep_temp=keep_temp, subtitle_style=subs,
        )
    except Exception as e:
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    rprint(
        f"[bold]slopgen[/bold] drama: {params.count}× {params.lang}"
        f" ~{params.duration_s / 60:.1f}min ±{params.duration_tol_s:.0f}s"
        + (f" parts={params.parts}" if params.parts != 1 else "")
        + f" cast=[{', '.join(names) or '—'}] orch={orchestration or 'default'}"
        f" ad={params.ad or '-'}({params.ad_mode}) push={params.push or 'local'}"
        + (" [yellow]\\[dry-run][/yellow]" if params.dry_run else "")
    )
    _execute(store, params)


def run() -> None:
    app()


if __name__ == "__main__":
    run()
