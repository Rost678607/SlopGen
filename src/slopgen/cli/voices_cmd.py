"""`slopgen voices` — the library of cloned voices.

A voice card is a TOML file and an audio sample sitting next to each other under
`configs/voices/`. There is no training and no model artifact: cloning is zero-shot,
so the card IS the voice, and the same card works on the local model and on the cloud
one. Its name goes wherever a voice name goes — `--voice марта` is the same option as
`--voice ru-RU-SvetlanaNeural`, and which kind it is depends only on whether a card of
that name exists.

`record` adds another recording of the same person to an existing card — `--as зло` —
which is how a cloned voice is given an intonation, and the only way there is: the
model imitates the reading of the sample it was shown, and no engine here has a
parameter for anger or for a whisper. `--voice марта:зло` then addresses it.

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
from ..config.models import VoiceConfig, VoiceSample
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
        rprint(f"[bold]{name}[/bold]  [dim]{v.lang}[/dim]"
               + (f" — {v.description}" if v.description else ""))
        _show_sample(v)
        # the card's other deliveries, each addressed as `card:recording` wherever a
        # voice name goes — which is what makes them worth listing here at all
        for which in v.sample_names:
            rprint(f"  [bold]{name}:{which}[/bold]"
                   + (f" — {v.samples[which].description}"
                      if v.samples[which].description else ""))
            _show_sample(v.samples[which], indent="    ")
        rprint("")


def _show_sample(s, indent: str = "  ") -> None:
    """One recording's file, how it measures, and what it says."""
    ref = s.ref_path
    if ref and Path(ref).exists():
        rprint(f"{indent}{ref}  [dim]{refs.inspect(Path(ref)).summary()}[/dim]")
    else:
        rprint(f"{indent}[red]sample missing:[/red] {s.ref or '<none>'}")
    rprint(f"{indent}[dim]says:[/dim] «{s.text[:90]}{'…' if len(s.text) > 90 else ''}»"
           if s.text else f"{indent}[red]no transcript — cloning will drift[/red]")
    if s.ref_url:
        rprint(f"{indent}[dim]cloud url:[/dim] {s.ref_url}")


@app.command()
def check(
    ctx: typer.Context,
    sample: Path = typer.Argument(..., help="an audio file, or an existing voice: `марта`, or `марта:зло` for one of its other recordings"),
    text: Optional[str] = typer.Option(None, "--text", help="what is said in it, for the transcript check (a card supplies its own)"),
    lang: str = typer.Option("ru", "--lang", help="which recognizer to listen with"),
) -> None:
    """Measure a sample and report what would go wrong with it — without adding it."""
    store: ConfigStore = ctx.obj
    path = sample
    found = store.voice_sample(str(sample)) if not path.exists() else None
    if found is not None:
        card, rec, _ = found
        path = Path(rec.ref_path or "")
        text, lang = text or rec.text, card.lang
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


def _ingest(store: ConfigStore, sample: Path, text: str, lang: str,
            wav: Path, clean: bool, force: bool) -> None:
    """Every refusal, and then the file: the shared half of `add` and `record`.

    A card's own sample and one of its other deliveries are the same kind of recording
    and are held to the same standard — clipping, hiss and a transcript that disagrees
    with the audio spoil a whispered take exactly as they spoil the default one."""
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

    wav.parent.mkdir(parents=True, exist_ok=True)
    # via a temporary file: the source may BE the destination (re-importing a card's
    # own sample to denoise it), and ffmpeg reading and writing one file gives silence
    tmp = wav.with_name(wav.name + ".importing.wav")
    try:
        refs.convert(sample, tmp, rnnoise=rnnoise)
        tmp.replace(wav)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    rprint(f"[dim]written:[/dim] {wav} — {refs.inspect(wav).summary()}")


def _write_card(v: VoiceConfig) -> Path:
    """The card back to its TOML, samples and all. Written from the model rather than
    from a dict, so a field added to `VoiceConfig` lands here without being remembered."""
    path = _dir() / f"{v.name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # a card with no other recordings is written without the table rather than with an
    # empty one — the model fills it back in, and the file stays a file a person reads
    drop = {"name", "root"} | (set() if v.samples else {"samples"})
    with open(path, "wb") as f:
        tomli_w.dump(v.model_dump(mode="json", exclude=drop), f)
    return path


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
    if "/" in name or ":" in name:
        typer.secho("error: a voice name cannot contain / or : — the colon is what "
                    "separates a card from one of its recordings", fg="red")
        raise typer.Exit(1)
    wav = _dir() / f"{name}.wav"
    _ingest(store, sample, text, lang, wav, clean, force)
    # Re-importing over an existing card replaces its default recording and keeps the
    # others: they are separate files of the same person, and nothing about replacing
    # this one says anything about them.
    old = store.voices.get(name)
    v = VoiceConfig(name=name, ref=wav.name, text=text, lang=lang,
                    description=description or "", ref_url=url or "",
                    samples=old.samples if old else {})
    card = _write_card(v)
    rprint(f"[green]✔ voice '{name}'[/green] → {card}")
    rprint(f"[dim]use it:[/dim] slopgen drama {lang} --voice {name} --tts-engine qwen-local")


@app.command()
def record(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="the voice card this recording belongs to"),
    sample: Path = typer.Argument(..., help="another recording of the SAME person (10-20s)"),
    as_: str = typer.Option(..., "--as", help="what this delivery is called: зло, шёпот, устало — used as --voice <card>:<name>"),
    text: str = typer.Option(..., "--text", help="EXACTLY what is said in THIS recording, typed by hand"),
    description: Optional[str] = typer.Option(None, "--description"),
    url: Optional[str] = typer.Option(None, "--url", help="a public URL of the same recording; only the cloud engine needs it"),
    clean: bool = typer.Option(False, "--clean", help="run RNNoise over it (needs the rnnoise-sh model)"),
    force: bool = typer.Option(False, "--force", help="add it even if it fails a check"),
) -> None:
    """Add another recording of the same person to a card — one delivery each.

    This is how a cloned voice is taught an intonation, and the only way there is: the
    model imitates the reading of the sample it was shown, and no engine here has a
    parameter for anger or for a whisper. So `--as зло` is a take of the same person
    shouting, `марта:зло` addresses it wherever a voice is named, and a line pinned to
    it at the voiceover breakpoint comes out shouted.

    Cut the takes out of ONE recording session if you can. Timbre travels with the
    delivery — a sample recorded on another day, or closer to the microphone, clones as
    a slightly different person, and a video that switches between two of those changes
    narrator mid-sentence."""
    store: ConfigStore = ctx.obj
    v = store.voices.get(name)
    if v is None:
        known = ", ".join(sorted(store.voices)) or "none yet"
        typer.secho(f"error: no voice '{name}' — there is {known}", fg="red")
        raise typer.Exit(1)
    which = as_.strip()
    if not which or "/" in which or ":" in which:
        typer.secho("error: --as cannot be empty or contain / or : — it is one word "
                    "naming the delivery, and the colon already separates it from the "
                    "card", fg="red")
        raise typer.Exit(1)
    # `марта.зло.wav`, beside `марта.wav`: one folder, and a filename that says which
    # card a recording belongs to
    wav = _dir() / f"{name}.{which}.wav"
    _ingest(store, sample, text, v.lang or "ru", wav, clean, force)
    v.samples[which] = VoiceSample(ref=wav.name, text=text,
                                   description=description or "", ref_url=url or "",
                                   root=_dir())
    card = _write_card(v)
    rprint(f"[green]✔ '{name}:{which}'[/green] → {card}")
    rprint(f"[dim]use it:[/dim] slopgen drama {v.lang or 'ru'} --voice {name}:{which}")
    rprint("[dim]…or pin one line to it at the voiceover breakpoint, which is what it "
           "is for[/dim]")


@app.command()
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="a voice card name, or `card:recording` for one delivery"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a voice card and every recording in it — or just one of its deliveries."""
    store: ConfigStore = ctx.obj
    found = store.voice_sample(name)
    if found is None:
        typer.secho(f"error: no voice '{name}'", fg="red")
        raise typer.Exit(1)
    v, rec, which = found
    if which:
        if not yes and not typer.confirm(f"delete the '{which}' recording of "
                                         f"'{v.name}'?", default=False):
            raise typer.Exit(1)
        if rec.ref_path and Path(rec.ref_path).exists():
            Path(rec.ref_path).unlink()
        v.samples.pop(which, None)
        _write_card(v)
        rprint(f"[green]removed[/green] '{v.name}:{which}'")
        return
    card = _dir() / f"{v.name}.toml"
    files = [s.ref_path for s in (v, *v.samples.values()) if s.ref_path]
    if not yes and not typer.confirm(
            f"delete {card} and {len(files)} recording(s)?", default=False):
        raise typer.Exit(1)
    card.unlink(missing_ok=True)
    for ref in files:
        if Path(ref).exists():
            Path(ref).unlink()
    rprint(f"[green]removed[/green] voice '{v.name}'")
