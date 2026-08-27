"""`slopgen voices` — the library of cloned voices.

A voice card is a TOML file and an audio sample sitting next to each other under
`configs/voices/`. There is no training and no model artifact: cloning is zero-shot,
so the card IS the voice, and the same card works on the local model and on the cloud
one. Its name goes wherever a voice name goes — `--voice марта` is the same option as
`--voice ru-RU-SvetlanaNeural`, and which kind it is depends only on whether a card of
that name exists.

`add` exists mostly to say no. A clipped or hissy sample does not fail loudly; it
quietly degrades every line of every video made with it, and in the measured worst
case makes the model recite the sample's own words mid-sentence. So the sample is
inspected here, at the one moment when re-recording it is cheap (see `tts.refs`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import tomli_w
import typer
from rich import print as rprint

from ..config import ConfigStore
from ..config.loader import CONFIGS_DIR
from ..tts import refs

app = typer.Typer(add_completion=False, help="manage cloned voices (configs/voices/)")


def _dir() -> Path:
    return CONFIGS_DIR / "voices"


@app.command("list")
def list_voices(ctx: typer.Context) -> None:
    """Every voice card, with the state of its sample."""
    store: ConfigStore = ctx.obj
    if not store.voices:
        rprint(f"[dim]no voice cards yet — {_dir()}/ is empty[/dim]")
        rprint("[dim]add one: slopgen voices add sample.wav --name марта --text \"…\"[/dim]")
        return
    for name, v in store.voices.items():
        ref = v.ref_path
        rprint(f"[bold]{name}[/bold]  [dim]{v.lang}[/dim]"
               + (f" — {v.description}" if v.description else ""))
        if ref and Path(ref).exists():
            rprint(f"  {ref}  [dim]{refs.inspect(Path(ref)).summary()}[/dim]")
        else:
            rprint(f"  [red]sample missing:[/red] {v.ref or '<none>'}")
        rprint(f"  [dim]says:[/dim] «{v.text[:90]}{'…' if len(v.text) > 90 else ''}»"
               if v.text else "  [red]no transcript — cloning will drift[/red]")
        if v.ref_url:
            rprint(f"  [dim]cloud url:[/dim] {v.ref_url}")
        rprint("")


@app.command()
def check(
    ctx: typer.Context,
    sample: Path = typer.Argument(..., help="an audio file, or the name of an existing card"),
    text: Optional[str] = typer.Option(None, "--text", help="what is said in it, for the transcript check (a card supplies its own)"),
    lang: str = typer.Option("ru", "--lang", help="which recognizer to listen with"),
) -> None:
    """Measure a sample and report what would go wrong with it — without adding it."""
    store: ConfigStore = ctx.obj
    path = sample
    card = store.voices.get(str(sample))
    if not path.exists() and card is not None:
        path = Path(card.ref_path or "")
        text, lang = text or card.text, card.lang
    if not path.exists():
        typer.secho(f"error: no such file or voice: {sample}", fg="red")
        raise typer.Exit(1)
    _report(refs.inspect(path), path)
    _report_transcript(_transcript_report(store, path, text or "", lang))


def _transcript_report(store: ConfigStore, path: Path, text: str,
                       lang: str) -> "refs.TranscriptReport | None":
    """The sample judged against its own transcript, or None when nothing can listen.

    Needs the same recognizer the pipeline aligns with, so it is skipped rather than
    installed here: this is a check, and a check that downloads 46 MiB before it will
    answer is one people learn to skip themselves."""
    from ..models import ModelStore
    from ..tts import align as aligner

    if not text.strip():
        return None
    store_ = ModelStore(store.global_cfg.paths.models)
    model_id = aligner.model_for(lang, store.global_cfg.tts)
    if model_id not in store_.installed():
        rprint(f"  [dim]transcript not checked — `slopgen models install {model_id}` "
               "buys the strictest of these checks[/dim]")
        return None
    return refs.check_transcript(path, text, store_.require(model_id))


def _report_transcript(report: "refs.TranscriptReport | None") -> None:
    if report is None:
        return
    rprint(f"[bold]says its transcript?[/bold] — {report.summary()}")
    for level, msg in report.problems or []:
        colour = "red" if level == "error" else "yellow"
        rprint(f"  [{colour}]{'✘' if level == 'error' else '!'}[/{colour}] {msg}")
    if not report.problems:
        rprint("  [green]✔ the sample and its transcript are the same thing[/green]")


def _report(report: refs.SampleReport, path: Path) -> None:
    rprint(f"[bold]{path.name}[/bold] — {report.summary()}")
    for level, msg in report.problems or []:
        colour = "red" if level == "error" else "yellow"
        rprint(f"  [{colour}]{'✘' if level == 'error' else '!'}[/{colour}] {msg}")
    if not report.problems:
        rprint("  [green]✔ nothing to complain about[/green]")


@app.command()
def add(
    ctx: typer.Context,
    sample: Path = typer.Argument(..., help="the recording to clone from (10-20s is ideal)"),
    name: str = typer.Option(..., "--name", help="what to call this voice; used as --voice <name>"),
    text: str = typer.Option(..., "--text", help="EXACTLY what is said in the sample, typed by hand"),
    lang: str = typer.Option("ru", "--lang", help="the sample's language"),
    description: Optional[str] = typer.Option(None, "--description"),
    url: Optional[str] = typer.Option(None, "--url", help="a public URL of the same sample; only the cloud engine needs it"),
    clean: bool = typer.Option(False, "--clean", help="run RNNoise over the sample (needs the rnnoise-sh model)"),
    force: bool = typer.Option(False, "--force", help="add it even if the sample fails a check"),
) -> None:
    """Add a voice card, refusing samples that would spoil the cloning."""
    store: ConfigStore = ctx.obj
    if not sample.exists():
        typer.secho(f"error: no such file: {sample}", fg="red")
        raise typer.Exit(1)
    if not refs.have_ffmpeg():
        typer.secho("error: ffmpeg is not on PATH", fg="red")
        raise typer.Exit(1)
    if not text.strip():
        typer.secho("error: --text cannot be empty — the transcript is what keeps the "
                    "cloning from drifting", fg="red")
        raise typer.Exit(1)

    report = refs.inspect(sample)
    _report(report, sample)
    # …and then the strictest question of all: does the recording say what the
    # transcript claims? A pair that disagrees is not a worse voice, it is a model
    # that finishes the transcript out loud in the middle of the script (see
    # `tts.refs.check_transcript`), and here is where re-recording is still cheap.
    transcript = _transcript_report(store, sample, text, lang)
    _report_transcript(transcript)
    usable = report.usable and (transcript is None or transcript.usable)
    if not usable and not force:
        rprint("[red]not added.[/red] Fix the recording, or pass --force if you know better.")
        raise typer.Exit(1)

    rnnoise = None
    if clean:
        from ..models import ModelStore

        rnnoise = ModelStore(store.global_cfg.paths.models).require("rnnoise-sh") / "sh.rnnn"

    out_dir = _dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"{name}.wav"
    # via a temporary file: the source may BE the destination (re-importing a card's
    # own sample to denoise it), and ffmpeg reading and writing one file gives silence
    tmp = wav.with_name(wav.name + ".importing.wav")
    try:
        refs.convert(sample, tmp, rnnoise=rnnoise)
        tmp.replace(wav)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    after = refs.inspect(wav)
    rprint(f"[dim]written:[/dim] {wav} — {after.summary()}")

    card = out_dir / f"{name}.toml"
    data = {"name": name, "ref": wav.name, "text": text, "lang": lang,
            "description": description or "", "ref_url": url or ""}
    with open(card, "wb") as f:
        tomli_w.dump(data, f)
    rprint(f"[green]✔ voice '{name}'[/green] → {card}")
    rprint(f"[dim]use it:[/dim] slopgen drama {lang} --voice {name} --tts-engine qwen-local")


@app.command()
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="a voice card name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a voice card and its sample."""
    store: ConfigStore = ctx.obj
    v = store.voices.get(name)
    if v is None:
        typer.secho(f"error: no voice '{name}'", fg="red")
        raise typer.Exit(1)
    card = _dir() / f"{name}.toml"
    ref = v.ref_path
    if not yes and not typer.confirm(f"delete {card} and its sample?", default=False):
        raise typer.Exit(1)
    card.unlink(missing_ok=True)
    if ref and Path(ref).exists():
        Path(ref).unlink()
    rprint(f"[green]removed[/green] voice '{name}'")
