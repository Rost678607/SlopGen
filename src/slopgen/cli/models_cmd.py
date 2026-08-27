"""`slopgen models` — the package manager for neural weights.

Weights are not in the repository and never will be (the local voice alone is
2.3 GiB), so they are fetched on demand and can be thrown away again. Everything the
catalogue knows — size, licence, what refuses to run without it — is printed BEFORE a
download starts, because 2.3 GiB is a decision and not a detail.

An interrupted install resumes: run the same command again and it continues from the
byte it stopped at (see `models.store._stream_to`).
"""

from __future__ import annotations

from typing import Optional

import typer
from rich import print as rprint

from ..config import ConfigStore
from ..models import CATALOG, ModelStore, get, human_size

app = typer.Typer(add_completion=False, help="download and remove neural models")


def _store(ctx: typer.Context) -> ModelStore:
    cfg: ConfigStore = ctx.obj
    return ModelStore(cfg.global_cfg.paths.models)


@app.command("list")
def list_models(ctx: typer.Context) -> None:
    """What exists, what is installed, and what it weighs."""
    store = _store(ctx)
    rprint(f"[dim]models live in {store.root}/[/dim]\n")
    for spec in CATALOG.values():
        if store.is_installed(spec.id):
            mark = f"[green]✔ installed[/green] [dim]({human_size(store.disk_size(spec.id))} on disk)[/dim]"
        else:
            mark = f"[dim]— not installed ({human_size(spec.size)})[/dim]"
        rprint(f"[bold]{spec.id}[/bold]  {mark}")
        rprint(f"  {spec.label} · {spec.license}")
        rprint(f"  [dim]{spec.description}[/dim]")
        if spec.used_by:
            rprint(f"  [dim]needed by: {spec.used_by}[/dim]")
        missing = ModelStore.missing_packages(spec)
        if spec.packages:
            state = f"[yellow]missing: {', '.join(missing)}[/yellow]" if missing else "[green]present[/green]"
            rprint(f"  [dim]pip:[/dim] {', '.join(spec.packages)} — {state}")
        rprint("")


@app.command()
def install(
    ctx: typer.Context,
    model_id: str = typer.Argument(..., help="an id from `slopgen models list`"),
    yes: bool = typer.Option(False, "--yes", "-y", help="do not ask before downloading"),
    no_packages: bool = typer.Option(False, "--no-packages", help="weights only; skip pip"),
) -> None:
    """Download a model (resumes an interrupted download)."""
    store = _store(ctx)
    spec = get(model_id)
    missing = ModelStore.missing_packages(spec)
    if store.is_installed(model_id) and not missing:
        rprint(f"[green]{model_id} is already installed[/green] — {store.path(model_id)}")
        raise typer.Exit()
    rprint(f"[bold]{spec.label}[/bold] — {human_size(spec.size)}, {spec.license}")
    rprint(f"  {spec.description}")
    if missing and not no_packages:
        rprint(f"  [yellow]also installs pip packages: {', '.join(missing)}[/yellow]")
    if not yes and not typer.confirm("download?", default=True):
        raise typer.Exit(1)

    width = 44
    state = {"line": ""}

    def progress(label: str, done: int, total: int, note: str) -> None:
        if note:
            typer.echo(f"\r{' ' * (width + 34)}\r  {label}: {note}")
            return
        pct = (done / total * 100) if total else 0.0
        filled = int(width * pct / 100)
        bar = "█" * filled + "░" * (width - filled)
        line = f"  {label[-28:]:<28} {bar} {pct:5.1f}% {human_size(done):>9}"
        state["line"] = line
        typer.echo("\r" + line, nl=False)

    try:
        path = store.install(model_id, progress, with_packages=not no_packages)
    except Exception as e:
        typer.echo("")
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1)
    typer.echo("")
    rprint(f"[green]✔ {model_id}[/green] → {path} ({human_size(store.disk_size(model_id))})")


@app.command()
def remove(
    ctx: typer.Context,
    model_id: str = typer.Argument(..., help="an id from `slopgen models list`"),
    yes: bool = typer.Option(False, "--yes", "-y", help="do not ask"),
) -> None:
    """Delete a model's files. The pip packages it pulled in are left alone —
    they may belong to something else."""
    store = _store(ctx)
    if not store.is_installed(model_id) and not store.path(model_id).exists():
        rprint(f"[yellow]{model_id} is not installed[/yellow]")
        raise typer.Exit()
    size = human_size(store.disk_size(model_id))
    if not yes and not typer.confirm(f"delete {model_id} ({size})?", default=False):
        raise typer.Exit(1)
    rprint(f"[green]removed[/green] {store.remove(model_id)} ({size} freed)")


@app.command()
def path(
    ctx: typer.Context,
    model_id: Optional[str] = typer.Argument(None, help="omit to print the models root"),
) -> None:
    """Where a model lives on disk — for scripts, and for looking inside."""
    store = _store(ctx)
    if not model_id:
        typer.echo(str(store.root))
        return
    get(model_id)
    typer.echo(str(store.path(model_id)))
