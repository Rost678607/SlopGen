"""`slopgen minecraft` — the fandom's world in Minecraft: the mod, the game, the markup.

The game is the operator's: they start it, and slopgen connects to the `slopcam`
mod inside it. These commands install that mod into the fandom's Prism instance,
say whether the game is reachable and with which world, and print what is marked up
in the world — from the game when it is open, from the save when it is not.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from ..config import ConfigStore
from ..config.models import FandomMinecraft
from ..minecraft import paths
from ..minecraft.client import DEFAULT_PORT, Game, GameUnavailable
from ..minecraft.registry import Area, Camera, Line, Point, Registry

app = typer.Typer(add_completion=False, help="the fandom's Minecraft world: the slopcam mod, the game, the markup")


def _world(ctx: typer.Context, fandom: str | None, instance: str | None, world: str | None) -> FandomMinecraft:
    cfg: ConfigStore = ctx.obj
    mc = FandomMinecraft()
    if fandom:
        f = cfg.fandoms.get(fandom)
        if f is None:
            raise typer.BadParameter(f"no fandom {fandom!r}; there are: {', '.join(cfg.fandoms) or 'none'}")
        if f.minecraft is not None:
            mc = f.minecraft.model_copy()
    if instance:
        mc.instance = instance
    if world:
        mc.world = world
    return mc


@app.command()
def install(ctx: typer.Context,
            fandom: Optional[str] = typer.Option(None, "--fandom", "-f", help="take the instance from this fandom's [minecraft]"),
            instance: Optional[str] = typer.Option(None, "--instance", help="the Prism instance (overrides the fandom's)"),
            build: bool = typer.Option(True, "--build/--no-build", help="build the mod first (nix-shell + gradle)")) -> None:
    """Build slopcam and put it into the instance's mods folder, replacing an older one."""
    mc = _world(ctx, fandom, instance, None)
    mods = paths.mods_dir(mc)
    if not mods.is_dir():
        raise typer.BadParameter(f"no mods folder at {mods}")
    src = paths.repo_mod_dir()
    if build:
        rprint(f"[dim]building {src} …[/dim]")
        r = subprocess.run(["nix-shell", "shell.nix", "--run", "gradle build --console=plain -q"], cwd=src)
        if r.returncode:
            raise typer.Exit(r.returncode)
    jars = sorted(p for p in (src / "build" / "libs").glob("slopcam-*.jar") if not p.name.endswith("-sources.jar"))
    if not jars:
        raise typer.BadParameter("nothing built — run without --no-build")
    jar = jars[-1]
    for old in mods.glob("slopcam-*.jar"):
        old.unlink()
    shutil.copy2(jar, mods / jar.name)
    rprint(f"[green]✔[/green] {jar.name} → {mods}")
    rprint("[dim]restart the game for it to load[/dim]")


@app.command()
def status(ctx: typer.Context,
           fandom: Optional[str] = typer.Option(None, "--fandom", "-f"),
           port: int = typer.Option(DEFAULT_PORT, "--port")) -> None:
    """Is the game reachable, with which mods, and with which world open."""
    try:
        with Game(port=port) as g:
            h = g.hello()
    except GameUnavailable as e:
        rprint(f"[yellow]⏸[/yellow] {e}")
        raise typer.Exit(1)
    rprint(f"[green]✔[/green] slopcam {h.get('version')} · Minecraft {h.get('minecraft')} · "
           f"Flashback {h.get('flashback') or '[red]missing[/red]'} · Easy NPC {h.get('easy_npc') or '[red]missing[/red]'}")
    if not h.get("in_world"):
        rprint("[yellow]no world is open[/yellow]")
        return
    rprint(f"world: [bold]{h.get('world')}[/bold]  ({h.get('save_dir')})" + ("  [cyan]· markup mode[/cyan]" if h.get("markup") else ""))
    if fandom:
        mc = _world(ctx, fandom, None, None)
        if mc.world and h.get("world") != mc.world:
            rprint(f"[yellow]the fandom is built in {mc.world!r}, but {h.get('world')!r} is open[/yellow]")


def load_registry(mc: FandomMinecraft, port: int = DEFAULT_PORT) -> tuple[Registry, str]:
    """The markup, from the game if it has this world open, else from the save."""
    try:
        with Game(port=port, timeout=2.0) as g:
            h = g.hello()
            if h.get("in_world") and (not mc.world or h.get("world") == mc.world):
                return Registry.model_validate(g.call("registry.get")), "game"
    except GameUnavailable:
        pass
    return Registry.load(paths.save_dir(mc)), "save"


@app.command()
def places(ctx: typer.Context,
           fandom: Optional[str] = typer.Option(None, "--fandom", "-f"),
           instance: Optional[str] = typer.Option(None, "--instance"),
           world: Optional[str] = typer.Option(None, "--world"),
           port: int = typer.Option(DEFAULT_PORT, "--port")) -> None:
    """Print what is marked up: places and their elements, characters and faces, props."""
    mc = _world(ctx, fandom, instance, world)
    reg, src = load_registry(mc, port)
    rprint(f"[dim]markup read from the {src}[/dim]")
    if not reg.places:
        rprint("[yellow]nothing is marked up yet — enter the markup mode from the pause menu[/yellow]")
    kinds = {Area: "zone", Point: "point", Line: "line", Camera: "camera"}
    for p in reg.places:
        rprint(f"\n[bold]{p.name}[/bold]" + (f" — {p.description}" if p.description else ""))
        for e in reg.elements(p):
            kind = next(v for k, v in kinds.items() if isinstance(e, k))
            roles = ", ".join(getattr(e, "roles", []) or [])
            rprint(f"  {kind:6} [cyan]{e.name}[/cyan]" + (f" [dim]({roles})[/dim]" if roles else "")
                   + (f" — {e.description}" if e.description else ""))
    if reg.characters:
        rprint("\n[bold]characters[/bold]")
        for c in reg.characters:
            rprint(f"  {c.name}: " + ", ".join(f"{v.name} [dim]({v.body})[/dim]" for v in c.variants))
    if reg.props:
        rprint("\n[bold]props[/bold]  " + ", ".join(p.name for p in reg.props))


@app.command()
def shoot(ctx: typer.Context,
          direction: Path = typer.Argument(..., exists=True, help="a direction file (anchors in `sec` — there is no narration here)"),
          fandom: Optional[str] = typer.Option(None, "--fandom", "-f"),
          instance: Optional[str] = typer.Option(None, "--instance"),
          world: Optional[str] = typer.Option(None, "--world"),
          length: float = typer.Option(8.0, "--length", help="seconds of the block"),
          final: bool = typer.Option(False, "--final/--draft", help="with shaders at full size, or a quick draft"),
          out: Path = typer.Option(Path("output/machinima"), "--out"),
          port: int = typer.Option(DEFAULT_PORT, "--port")) -> None:
    """Play, film and join one direction file — the whole machinima path, without a run."""
    import json as _json

    from ..minecraft import film, library
    from ..minecraft.anchors import Clock
    from ..minecraft.compile import compile_block
    from ..minecraft.direction import Direction, validate

    mc = _world(ctx, fandom, instance, world)
    reg, src = load_registry(mc, port)
    d = Direction.model_validate(_json.loads(direction.read_text(encoding="utf-8")))
    lib = library.load(paths.save_dir(mc))
    errs = validate(d, reg, None, set(lib.gestures), set(lib.poses))
    if errs:
        for e in errs:
            rprint(f"[red]✘[/red] {e}")
        raise typer.Exit(1)
    out.mkdir(parents=True, exist_ok=True)
    stem = direction.stem
    with Game(port=port, timeout=10.0) as g:
        h = g.hello()
        if not h.get("in_world"):
            rprint("[yellow]open the world in the game first[/yellow]")
            raise typer.Exit(1)
        # crowds are placed on the world around their place, so it is scanned first
        worlds = film.scan_places(g, reg, {sc.place for sc in d.scenes if sc.crowd})
        plans = compile_block(d, reg, lib, Clock([]), 0.0, length, worlds=worlds)
        takes, jobs = [], []
        for plan in plans:
            for w in plan.warnings:
                rprint(f"[yellow]![/yellow] scene {plan.index + 1}: {w}")
            name = f"{stem}_s{plan.index + 1}"
            rprint(f"[dim]playing scene {plan.index + 1} ({plan.script['ticks'] / 20:.1f}s)…[/dim]")
            take = film.perform(g, plan, (out / name).with_suffix(".zip").resolve(), name)
            film.save_report(take, out / f"{name}.report.json")
            for i in take.issues:
                rprint(f"[yellow]![/yellow] {i}")
            ks, issues = film.keys(plan, take, reg)
            for i in issues:
                rprint(f"[yellow]![/yellow] {i}")
            takes.append(take)
            jobs.append(film.job(plan, take, ks, (out / f"{name}.{'final' if final else 'draft'}.mp4").resolve(), final))
        rprint(f"[dim]rendering {len(jobs)} scene(s) {'with shaders' if final else 'as a draft'} — the game leaves the world and comes back…[/dim]")
        results = film.render(g, jobs, final)
    bad = [r for r in results if not r.get("ok")]
    for r in bad:
        rprint(f"[red]✘[/red] {r.get('out')}: {r.get('error', 'not written')}")
    clips = [Path(r["out"]) for r in results if r.get("ok")]
    if clips:
        joined = film.join(clips, out / f"{stem}.{'final' if final else 'draft'}.mp4", length)
        rprint(f"[green]✔[/green] {joined}")
