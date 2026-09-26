"""`slopgen voices` — the library of cloned voices.

A voice card is a TOML file and an audio sample sitting next to each other under
`configs/voices/`. There is no training and no model artifact: cloning is zero-shot,
so the card IS the voice, and the same card works on the local model and on the cloud
one. Its name goes wherever a voice name goes — `--voice марта` is the same option as
`--voice ru-RU-SvetlanaNeural`, and which kind it is depends only on whether a card of
that name exists.

A card is a CATALOGUE of recordings, not a recording that owns others: `[samples.зло]`,
`[samples.шёпот]` and `[samples.обычная]` sit on one level and `default` names the one a
bare `--voice марта` speaks with. `record` adds another — `--as зло` — which is how a
cloned voice is given an intonation, and the only way there is: the model imitates the
reading of the sample it was shown, and no engine here has a parameter for anger or for
a whisper. `default` moves the pointer, and moving it is what changes what a whole video
sounds like; `--voice марта:зло` pins a run (or one line) to a delivery instead.

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
from ..config.models import DEFAULT_DELIVERY, VoiceConfig, VoiceSample
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
        if not v.sample_names:
            rprint("  [red]no recordings in this card — it can clone nothing[/red]")
        # every delivery on one level, with the default marked: `марта` speaks with
        # the starred one, `марта:зло` addresses any of them by name
        for which in v.sample_names:
            star = "[green]★[/green] " if which == v.default_name else "  "
            rprint(f"  {star}[bold]{name}:{which}[/bold]"
                   + (f" — {v.samples[which].description}"
                      if v.samples[which].description else ""))
            _show_sample(v.samples[which], indent="      ")
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


def _delivery_name(which: str) -> str:
    """A delivery's name, or an exit. One word, and never with a colon in it: the
    colon is what separates a card from a delivery everywhere a voice is named, so a
    delivery carrying one would be unaddressable."""
    which = (which or "").strip()
    if not which or "/" in which or ":" in which:
        typer.secho("error: a delivery's name cannot be empty or contain / or : — it is "
                    "one word naming how the line is read, and the colon already "
                    "separates it from the card", fg="red")
        raise typer.Exit(1)
    return which


def _write_card(v: VoiceConfig) -> Path:
    """The card back to its TOML, every delivery and the pointer at the default one.
    Written from the model rather than from a dict, so a field added to `VoiceConfig`
    lands here without being remembered (see `VoiceConfig.as_config`)."""
    path = _dir() / f"{v.name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(v.as_config(), f)
    return path


@app.command()
def add(
    ctx: typer.Context,
    sample: Path = typer.Argument(..., help="the recording to clone from (10-20s is ideal)"),
    name: str = typer.Option(..., "--name", help="what to call this voice; used as --voice <name>"),
    text: str = typer.Option(..., "--text", help="EXACTLY what is said in the sample, typed by hand"),
    lang: str = typer.Option("ru", "--lang", help="the sample's language"),
    as_: str = typer.Option(DEFAULT_DELIVERY, "--as", help=f"what this delivery is called ('{DEFAULT_DELIVERY}' unless you say otherwise); it becomes the card's default"),
    description: Optional[str] = typer.Option(None, "--description"),
    url: Optional[str] = typer.Option(None, "--url", help="a public URL of the same sample; only the cloud engine needs it"),
    clean: bool = typer.Option(False, "--clean", help="run RNNoise over the sample (needs the rnnoise-sh model)"),
    force: bool = typer.Option(False, "--force", help="add it even if the sample fails a check"),
) -> None:
    """Start a voice card, refusing samples that would spoil the cloning.

    The recording it takes in is a DELIVERY like any other — named, sitting in the
    card's table, and pointed at by `default` because it is the first one there. Which
    is why `--as` exists here at all: a card whose first take is a shout is a perfectly
    good card, and `--as зло` says so on the card rather than leaving the shout filed
    under a name meaning "the plain one"."""
    store: ConfigStore = ctx.obj
    if "/" in name or ":" in name:
        typer.secho("error: a voice name cannot contain / or : — the colon is what "
                    "separates a card from one of its recordings", fg="red")
        raise typer.Exit(1)
    old = store.voices.get(name)
    which = _delivery_name(as_ or (old.default_name if old else "") or DEFAULT_DELIVERY)
    wav = _dir() / f"{name}.{which}.wav"
    _ingest(store, sample, text, lang, wav, clean, force)
    # Re-importing over an existing card replaces the delivery of that name and keeps
    # the others: they are separate files of the same person, and nothing about
    # replacing one says anything about the rest.
    v = old or VoiceConfig(name=name, lang=lang, root=_dir())
    v.lang = lang
    if description is not None:
        v.description = description
    v.samples[which] = VoiceSample(ref=wav.name, text=text, ref_url=url or "",
                                   root=_dir())
    v.default = which if old is None else v.default_name
    card = _write_card(v)
    rprint(f"[green]✔ voice '{name}:{which}'[/green] → {card}")
    if v.default_name == which:
        rprint(f"[dim]it is this card's default — `--voice {name}` speaks with it[/dim]")
    else:
        rprint(f"[dim]this card still speaks with '{v.default_name}' by default — "
               f"`slopgen voices default {name} {which}` moves it[/dim]")
    rprint(f"[dim]use it:[/dim] slopgen drama {lang} --voice {name} --tts-engine qwen-local")


@app.command()
def record(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="the voice card this recording belongs to"),
    sample: Path = typer.Argument(..., help="another recording of the SAME person (10-20s)"),
    as_: str = typer.Option(..., "--as", help="what this delivery is called: зло, шёпот, устало — used as --voice <card>:<name>"),
    text: str = typer.Option(..., "--text", help="EXACTLY what is said in THIS recording, typed by hand"),
    make_default: bool = typer.Option(False, "--default", help="and make it the card's default, so the whole video speaks with it"),
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

    It lands beside the card's other deliveries and not under them: nothing here is a
    lesser take than the first one cut. `--default` says so outright and points the
    card at it, which is how a whole video comes out shouted without a single line
    being pinned.

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
    which = _delivery_name(as_)
    # `марта.зло.wav`, beside the card's other takes: one folder, and a filename that
    # says which card a recording belongs to and which delivery of it this is
    wav = _dir() / f"{name}.{which}.wav"
    _ingest(store, sample, text, v.lang or "ru", wav, clean, force)
    v.samples[which] = VoiceSample(ref=wav.name, text=text,
                                   description=description or "", ref_url=url or "",
                                   root=_dir())
    if make_default:
        v.default = which
    card = _write_card(v)
    rprint(f"[green]✔ '{name}:{which}'[/green] → {card}")
    if v.default_name == which:
        rprint(f"[dim]…and it is now this card's default: `--voice {name}` speaks with "
               "it, and so does every line of a run that names no other[/dim]")
    rprint(f"[dim]use it:[/dim] slopgen drama {v.lang or 'ru'} --voice {name}:{which}")
    rprint("[dim]…or pin one line to it at the voiceover breakpoint, which is what it "
           "is for[/dim]")


@app.command("default")
def set_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="the voice card"),
    which: str = typer.Argument(..., help="which of its deliveries the whole video should speak with"),
) -> None:
    """Point a card at another of its deliveries.

    The one operation the flat shape exists for. Every recording in a card is a
    delivery of one person and none of them is the important one by birth; this names
    the one a bare `--voice марта` means, which is what an unpinned line — that is,
    almost every line — is voiced with.

    It re-voices: the recording is part of the voiced-line cache key, so a resumed run
    speaks its unpinned lines again in the new delivery, while the lines pinned to a
    delivery BY NAME (`марта:зло`) do not move. That asymmetry is the point of having
    both spellings."""
    store: ConfigStore = ctx.obj
    try:
        card = store.set_default_delivery(name, which)
    except Exception as e:  # noqa: BLE001 — no such card, or no such delivery in it
        typer.secho(f"error: {e}", fg="red")
        raise typer.Exit(1) from e
    _write_card(card)
    rprint(f"[green]✔ '{name}' now speaks with '{which}'[/green]")
    rprint(f"[dim]…in every run that says `--voice {name}`, and on every line that "
           "names no delivery of its own[/dim]")


@app.command()
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="a voice card name, or `card:recording` for one delivery"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a voice card and every delivery in it — or just one of its deliveries.

    Deleting the delivery a card points at is allowed and leaves the card standing: the
    pointer falls back to the first delivery still there, and the command says which
    one that now is. A card emptied of every delivery is also allowed and says so —
    it is a name reserved for a person whose recordings you are about to re-cut."""
    store: ConfigStore = ctx.obj
    found = store.voice_sample(name)
    if found is None:
        typer.secho(f"error: no voice '{name}'", fg="red")
        raise typer.Exit(1)
    v, rec, which = found
    # `марта:зло` names one delivery; a bare `марта` is the whole card, deliveries and
    # all — NOT its default recording, which would leave a card behind that no longer
    # holds the voice it is named after
    if ":" in name and which:
        if not yes and not typer.confirm(f"delete the '{which}' delivery of "
                                         f"'{v.name}'?", default=False):
            raise typer.Exit(1)
        # the file goes only if nothing else names it: a `ref` is a filename in a
        # config, so two deliveries — or two cards — can point at one recording
        if (rec.ref_path and Path(rec.ref_path).exists()
                and not store.names_recording(rec.ref_path, except_=(v.name, which))):
            Path(rec.ref_path).unlink()
        v.samples.pop(which, None)
        _write_card(v)
        rprint(f"[green]removed[/green] '{v.name}:{which}'")
        if v.sample_names:
            rprint(f"[dim]'{v.name}' now speaks with '{v.default_name}'[/dim]")
        else:
            rprint(f"[yellow]'{v.name}' is now a card with nothing in it[/yellow]")
        return
    card = _dir() / f"{v.name}.toml"
    files = [(which, smp.ref_path) for which, smp in v.samples.items()
             if smp.ref_path
             and not store.names_recording(smp.ref_path, except_=(v.name, which))]
    if not yes and not typer.confirm(
            f"delete {card} and {len(files)} recording(s)?", default=False):
        raise typer.Exit(1)
    card.unlink(missing_ok=True)
    for _which, ref in files:
        if Path(ref).exists():
            Path(ref).unlink()
    rprint(f"[green]removed[/green] voice '{v.name}'")
