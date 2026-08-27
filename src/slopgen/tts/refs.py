"""Judging and repairing the audio samples that cloned voices are built from.

A zero-shot cloner has no training step in which a bad sample could be averaged away:
the reference is handed to the model with every single line, so its every defect is
copied into every line. The failures are specific and were measured on this project's
own recordings —

* **clipping** is the worst of them. A sample peaking at 0.0 dB did not merely sound
  harsh: the model began inserting words FROM THE SAMPLE into the synthesized text.
* **a high noise floor** gets cloned as a hiss under the narration, and no amount of
  cleaning the output undoes it.
* **too short** gives the model nothing to characterise the voice with. **Too long**
  is not the defect it is usually said to be: measured here on five clean references
  built from one voice at 6.1s, 12.2s, 24.1s, 43.4s and 61.3s, thirty takes came out
  indistinguishable (median score 0.81-0.82, not one leaked word at any length). What
  grows with length is the bill — one line costs about 14s plus 0.4s per second of
  reference on this machine, so a minute-long sample adds some twenty seconds to every
  line of the video. Length is a cost, not a quality.

So the checks below run when a voice is added, not when a video is rendered — the
whole point is to refuse the sample before it has quietly ruined forty lines.

Cleaning is RNNoise (`arnndn`) and nothing else, which is the second measured thing
here. The obvious chain — spectral denoising plus loudness normalisation — made the
recordings measurably worse: `afftdn` at a useful strength leaves musical-noise
artifacts that a cloner imitates faithfully, and `loudnorm` lifts the noise floor
along with the voice (measured: -75 dB -> -49 dB). RNNoise is trained on speech,
leaves no such artifacts, and gained +11 dB of floor on a phone recording. The gain
afterwards is PEAK normalisation only: nothing here compresses, because the dynamics
are part of the voice being copied.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# what the samples are converted to: the rate the cloners want, mono, uncompressed
SAMPLE_RATE = 24000

# thresholds, all measured rather than conventional (see the module docstring)
MIN_SECONDS = 3.0
# Not a limit on what works — see the module docstring, where 61s cloned as cleanly as
# 6s — but the length past which the sample starts charging real time for every line
# of every video made with it.
MAX_SECONDS = 30.0
CLIPPING_DB = -0.5  # a peak this close to full scale means the recording was clipped
NOISE_FLOOR_DB = -35.0
MIN_SNR_DB = 20.0

CLEAN_CHAIN = "highpass=f=80,arnndn=m={model}"


@dataclass
class SampleReport:
    duration: float = 0.0
    peak_db: float | None = None
    rms_db: float | None = None
    floor_db: float | None = None
    problems: list[tuple[str, str]] = None  # [("error"|"warn", message)]

    @property
    def usable(self) -> bool:
        return not any(level == "error" for level, _ in self.problems or [])

    def summary(self) -> str:
        def f(v):
            if v is None:
                return "?"
            return "silent" if v == float("-inf") else f"{v:.1f} dB"
        return (f"{self.duration:.1f}s · peak {f(self.peak_db)} · RMS {f(self.rms_db)} "
                f"· noise floor {f(self.floor_db)}")


def _astats(path: Path) -> dict[str, float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "astats", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out: dict[str, float] = {}
    for key, name in (("Peak level dB", "peak"), ("RMS level dB", "rms"),
                      ("Noise floor dB", "floor")):
        # `-inf` is a real answer here, not a parse failure: it is what astats reports
        # for digital silence, i.e. a sample with no noise under the voice at all.
        m = re.search(rf"{key}:\s*(-?(?:inf|nan|\d+\.?\d*))", proc.stderr)
        if m:
            out[name] = float(m.group(1))
    return out


def inspect(path: Path) -> SampleReport:
    """Measure a sample and say what would go wrong with it. Never raises for a file
    ffmpeg cannot read — that is reported as a problem like any other."""
    from ..media.ffmpeg import FFmpegError, duration_of

    report = SampleReport(problems=[])
    try:
        report.duration = duration_of(path)
    except (FFmpegError, OSError) as e:
        report.problems.append(("error", f"ffmpeg cannot read this file: {e}"))
        return report
    stats = _astats(path)
    report.peak_db = stats.get("peak")
    report.rms_db = stats.get("rms")
    report.floor_db = stats.get("floor")

    if report.duration < MIN_SECONDS:
        report.problems.append((
            "error",
            f"only {report.duration:.1f}s — a cloner needs at least {MIN_SECONDS:.0f}s "
            "to characterise a voice at all (6s was measured cloning perfectly well)",
        ))
    elif report.duration > MAX_SECONDS:
        report.problems.append((
            "warn",
            f"{report.duration:.0f}s of reference is used in full and paid for in "
            f"full — about {report.duration * 0.41:.0f}s of extra synthesis on every "
            "line of every video. It will clone fine; a shorter one clones as well "
            "and faster.",
        ))
    if report.peak_db is not None and report.peak_db >= CLIPPING_DB:
        report.problems.append((
            "error",
            f"peaks at {report.peak_db:.1f} dB — this recording is clipped, and a "
            "clipped reference has been measured making the model speak words from "
            "the SAMPLE in the middle of a synthesized line. Re-record it quieter.",
        ))
    if report.floor_db is not None and report.floor_db > NOISE_FLOOR_DB:
        report.problems.append((
            "warn",
            f"noise floor {report.floor_db:.1f} dB — the hiss gets cloned along with "
            "the voice; try --clean",
        ))
    if (report.rms_db is not None and report.floor_db is not None
            and report.floor_db != float("-inf")):
        snr = report.rms_db - report.floor_db
        if snr < MIN_SNR_DB:
            report.problems.append((
                "warn",
                f"only {snr:.0f} dB between the voice and the noise under it; "
                f"{MIN_SNR_DB:.0f} dB is where cloning starts sounding clean",
            ))
    return report


# -- the sample against its own transcript ---------------------------------
#
# Everything above measures the sample as SOUND. This measures it as evidence: a
# cloner is handed the pair, and what it does with a pair that disagrees is not
# degrade gracefully — it finishes the transcript. Measured on this project's own
# card (a 33s montage of cartoon lines): asked for «В Тлени водицу горячую
# запретили», three takes out of three came back saying «я совсем промок, я скоро
# утону, я ёжик, я упал в реку», which is the END of the transcript and no part of
# the script at all.
#
# The recognizer that recovers word timings answers this too, and for free: run it
# over the sample and ask how much of the transcript is actually in there. On that
# card it places 5 of the 51 words the card claims — and ffmpeg finds nine seconds of
# silence sitting in the middle of it. Neither number is audible as a defect, and
# both are fatal to what the sample is FOR.

MIN_HEARD = 0.5  # of the transcript's words; below this the pair is not a pair
MAX_GAP_S = 2.5  # a cut between two clips, rather than a breath — not one person talking
MAX_SILENT = 0.3  # of the sample's length; past this it is an edit, not a recording


@dataclass
class TranscriptReport:
    duration: float = 0.0
    words: int = 0
    found: int = 0
    gap: float = 0.0       # longest single silence in the sample
    gap_at: float = 0.0    # where it starts
    silent: float = 0.0    # share of the sample that is silence at all
    problems: list[tuple[str, str]] = None

    @property
    def heard(self) -> float:
        return self.found / self.words if self.words else 0.0

    @property
    def usable(self) -> bool:
        """Nothing here predicts a failure. NOT a verdict on the recording: the one
        error this report can raise is a suspicion (see `check_transcript`), and the
        caller is expected to settle it by voicing a line rather than by refusing."""
        return not any(level == "error" for level, _ in self.problems or [])

    def summary(self) -> str:
        return (f"{self.found}/{self.words} words of the transcript heard in the "
                f"sample · {self.silent:.0%} silence · longest {self.gap:.1f}s")


# How far under a recording's own RMS a stretch has to sit before it counts as
# silence rather than as quiet talking. Relative, and that is the whole point: an
# absolute -35 dBFS threshold accused a quiet sample of this project's own of being
# 60% silence, when what it actually contains is a soft voice — the same file reads
# 28% at RMS-20, 7% at RMS-25 and 0% at RMS-30, so the absolute number was measuring
# the recording's level, not its pauses. On recordings that DO have real silence
# between sentences the choice barely matters (three clean references came out at
# 16-18% under every threshold from -35 dBFS to RMS-30), which is what makes the
# relative one safe to adopt: it changes the answer only where the answer was wrong.
QUIET_UNDER_RMS = 25.0


def _silences(path: Path, duration: float, floor_db: float | None = None,
              min_s: float = 0.4) -> list[tuple[float, float]]:
    """Every silent stretch in a file as (start, length), longest first."""
    if floor_db is None:
        rms = _astats(path).get("rms")
        floor_db = -35.0 if rms is None or rms != rms else rms - QUIET_UNDER_RMS
    proc = subprocess.run(
        # silencedetect reports on the log, at info level — quieting ffmpeg the way
        # the rest of this module does would silence the answer along with the banner
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af",
         f"silencedetect=n={floor_db}dB:d={min_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out: list[tuple[float, float]] = []
    start: float | None = None
    for m in re.finditer(r"silence_(start|end): *(-?[\d.]+)", proc.stderr):
        if m.group(1) == "start":
            start = float(m.group(2))
        elif start is not None:
            out.append((start, float(m.group(2)) - start))
            start = None
    if start is not None:  # silence running to the end of the file
        out.append((start, max(0.0, duration - start)))
    return sorted(out, key=lambda g: -g[1])


def check_transcript(path: Path, text: str, model_dir: Path) -> TranscriptReport:
    """Does this sample say what its card claims it says?

    Never raises: a recognizer that will not load leaves an empty report rather than
    stopping an import, because this is the strictest of the checks and the least
    certain — a whispered sample, or one in a language the installed model does not
    cover, can be perfectly good and still score badly here. So it warns where
    :func:`inspect` refuses."""
    from . import align

    report = TranscriptReport(problems=[])
    tokens = text.split()
    report.words = len(tokens)
    if not tokens:
        return report
    try:
        from ..media.ffmpeg import duration_of

        duration = report.duration = duration_of(path)
        heard = align.recognize(path, model_dir)
    except Exception as e:  # noqa: BLE001 — see the docstring
        report.problems.append(("warn", f"could not listen to the sample ({e})"))
        return report

    ref = [align._bare(t) for t in tokens]
    hyp = [align._bare(w["text"]) for w in heard]
    report.found = sum(
        1 for op, i, j in align._ops(ref, hyp)
        if op == "match" or (op == "sub" and align._same_word(ref[i], hyp[j]))
    )
    # The holes are measured on the WAVEFORM, not on what the recognizer managed to
    # make of it, and the two must not be confused. A voice it cannot decode still
    # registers as sound; treating every unrecognized second as a hole accused this
    # project's own sample of a 13s gap starting at 15s AND of a 4.7s one at the
    # front, where ffmpeg finds ordinary speech. Only one of those is real.
    quiet = _silences(path, duration)
    report.gap_at, report.gap = quiet[0] if quiet else (0.0, 0.0)
    report.silent = sum(g for _at, g in quiet) / duration if duration else 0.0

    if report.heard < MIN_HEARD:
        report.problems.append((
            "error",
            f"only {report.found} of the transcript's {report.words} words can be "
            f"heard in this sample. Usually that means the transcript is not what the "
            "recording says, which is the defect that makes a model finish the "
            "transcript OUT LOUD in the middle of your script — but it can also mean "
            "a voice the recognizer cannot make out (a child, a whisper, something "
            "heavily processed), so it is a suspicion and not a verdict",
        ))
    if report.gap > MAX_GAP_S:
        report.problems.append((
            "warn",
            f"{report.gap:.0f}s of this sample ({report.gap_at:.0f}s-"
            f"{report.gap_at + report.gap:.0f}s) is silence. A reference wants one "
            "person talking without stopping; a pause that long is a cut between two "
            "clips, and the model has to invent something to fill it.",
        ))
    if report.silent > MAX_SILENT:
        report.problems.append((
            "warn",
            f"{report.silent:.0%} of this {report.duration:.0f}s sample is silence, "
            f"so it carries about {report.duration * (1 - report.silent):.0f}s of "
            "actual voice. Cut it down to the longest stretch of continuous talking.",
        ))
    return report


def _peak_gain(src: Path, chain: str, target_db: float = -1.0) -> float:
    """How much gain brings the peak to `target_db` AFTER `chain` — measured on the
    filtered signal, since the filtering changes it.

    Measured with `astats` on a FLOAT stream, and that detail is the whole function.
    The obvious tool, `volumedetect`, builds an integer histogram: a filtered signal
    that overshoots 0 dBFS — which RNNoise routinely produces, +0.65 dB on this
    project's own test sample — is clipped before it is counted, and reported as a
    tidy "0.0 dB". Trusting that number under-attenuates by exactly the overshoot,
    and the surplus is then clipped again on the way to disk. Which would mean this
    module introducing clipping into the very reference it was cleaning, and then
    :func:`inspect` refusing its own output. `astats` in float sees past 0 dBFS."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-af",
         f"{chain},aformat=sample_fmts=fltp,astats", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"Peak level dB:\s*(-?(?:inf|nan|\d+\.?\d*))", proc.stderr)
    if not m:
        return 0.0
    peak = float(m.group(1))
    return 0.0 if peak in (float("-inf"), float("inf")) or peak != peak else target_db - peak


def convert(src: Path, dst: Path, rnnoise: Path | None = None,
            start: float | None = None, end: float | None = None) -> Path:
    """Write the sample as the cloners want it: mono, 24 kHz, peak-normalised, and
    optionally denoised. `rnnoise` is the path to an `.rnnn` model (see the
    `rnnoise-sh` entry in the model catalogue); without one only the rumble cut and
    the normalisation are applied."""
    chain = "highpass=f=80"
    if rnnoise:
        # ffmpeg's filter parser eats backslashes and colons in paths
        chain = CLEAN_CHAIN.format(model=str(rnnoise).replace("\\", "/").replace(":", r"\:"))
    gain = _peak_gain(src, chain)
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-af", f"{chain},volume={gain:.2f}dB",
            "-ar", str(SAMPLE_RATE), "-ac", "1"]
    if start is not None:
        args += ["-ss", f"{start:.2f}"]
    if end is not None:
        args += ["-to", f"{end:.2f}"]
    args.append(str(dst))
    subprocess.run(args, check=True, capture_output=True)
    return dst


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
