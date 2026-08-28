"""What every LLM call actually cost — measured, not guessed.

A run's bill is invisible until somebody counts it, and the counting has to happen
where the calls are made: a stage cannot report tokens it never sees, and a provider's
dashboard reports a day, not a video. So every call through :class:`llm.client.ChatLLM`
lands here as one :class:`Call` — which stage asked, which prompt, which model, how
many tokens went in, how many of them the provider served out of its prompt cache, how
many came back, how long it took, and whether it was a first attempt or a retry.

Retries are recorded as calls of their own, deliberately. A stage that re-asks because
the answer came back unusable pays the whole context again, and a bill that folds the
second attempt into the first hides exactly the thing worth seeing: the fandom script's
first measured run spent more than half its tokens re-sending a 43k-character cast
sheet the writer never used.

The cache column is the other reason this exists. Prompt caching is a property of the
prompt PREFIX — the provider matches from the first token forward and stops at the
first difference — so it is not something to be believed in, it is something to be
read off `cached_tokens` per call. A stage whose invariant material (a world's canon
sheet, its cast sheet) sits behind a per-window arc rule caches nothing, and the number
here is what says so.

Money is deliberately NOT built in. Prices change, differ per provider and are not the
same for a cache hit as for a miss, so they are configuration (`LLMProfile.price_in` /
`price_cached` / `price_out`, USD per million tokens) rather than a table that quietly
goes stale. Unpriced profiles still report every token; `cost_usd` is simply None.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Prices:
    """USD per MILLION tokens, as the operator configured them (0 = unknown)."""

    inp: float = 0.0  # input tokens the provider had to read (cache miss)
    cached: float = 0.0  # input tokens served from its prompt cache
    out: float = 0.0  # generated tokens

    @property
    def known(self) -> bool:
        return any((self.inp, self.cached, self.out))

    def cost(self, prompt: int, cached: int, completion: int) -> float | None:
        """What one call cost, or None when this profile carries no prices.

        `prompt` is the whole input as the provider counts it, `cached` the part of it
        that was a cache hit — so the miss is the difference, never a separate field.
        Providers disagree about which of the two they report; the caller normalises
        that (see `client._usage`), and this only does the arithmetic."""
        if not self.known:
            return None
        miss = max(prompt - cached, 0)
        return (miss * self.inp + cached * self.cached + completion * self.out) / 1e6


@dataclass
class Call:
    """One request/response round trip, as it happened."""

    stage: str  # pipeline stage that was running ("script", "canon", …)
    kind: str  # the caller's own label ("fandom_script", "char_compile", …)
    profile: str  # LLM profile that answered
    model: str
    attempt: int  # 0 = first try; >0 = a retry, and a retry costs full price
    prompt_tokens: int = 0
    cached_tokens: int = 0  # of `prompt_tokens`, the part served from the cache
    completion_tokens: int = 0
    seconds: float = 0.0
    ok: bool = True
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _blank() -> dict:
    return {
        "calls": 0, "retries": 0, "failed": 0,
        "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "seconds": 0.0, "cost_usd": None,
    }


def _add(acc: dict, call: Call) -> None:
    acc["calls"] += 1
    acc["retries"] += 1 if call.attempt else 0
    acc["failed"] += 0 if call.ok else 1
    acc["prompt_tokens"] += call.prompt_tokens
    acc["cached_tokens"] += call.cached_tokens
    acc["completion_tokens"] += call.completion_tokens
    acc["total_tokens"] += call.total_tokens
    acc["seconds"] = round(acc["seconds"] + call.seconds, 2)
    if call.cost_usd is not None:
        acc["cost_usd"] = round((acc["cost_usd"] or 0.0) + call.cost_usd, 6)


@dataclass
class UsageLedger:
    """Every call this run made, and the roll-ups worth writing into the checkpoint.

    One ledger per run, shared by every client the router holds (see `llm.router`), so
    a run that splits its stages across two models still adds up to one bill. `stage`
    is set by the orchestrator around each stage and is the only piece of pipeline the
    ledger knows about — the alternative was threading a stage name through every
    prompt module, which is a lot of plumbing to learn one string."""

    stage: str = ""
    calls: list[Call] = field(default_factory=list)

    def reset(self, stage: str = "") -> None:
        """Start a fresh bill. Called between the videos of a batch: the checkpoint
        stores the ledger per JOB, so the second video of a `--count 3` run must not
        inherit the first one's tokens."""
        self.calls = []
        self.stage = stage

    def record(self, call: Call) -> Call:
        call.stage = call.stage or self.stage
        self.calls.append(call)
        return call

    # -- roll-ups ----------------------------------------------------------

    def totals(self) -> dict:
        acc = _blank()
        for c in self.calls:
            _add(acc, c)
        return acc

    def _grouped(self, key) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.calls:
            _add(out.setdefault(key(c) or "(none)", _blank()), c)
        return out

    def summary(self, calls: bool = True) -> dict:
        """The whole bill as plain JSON, for the checkpoint and for `slopgen usage`.

        `by_stage` answers "where did the run's money go", `by_kind` answers "which
        prompt is expensive" — they differ, because one stage makes several kinds of
        call and one kind (a character compile) is made from several stages."""
        data: dict = {
            "totals": self.totals(),
            "by_stage": self._grouped(lambda c: c.stage),
            "by_kind": self._grouped(lambda c: c.kind),
        }
        if calls:
            data["calls"] = [asdict(c) for c in self.calls]
        return data

    def cache_rate(self) -> float:
        """Share of input tokens the provider served out of its prompt cache, 0..1.

        The one number that says whether the expensive invariant material — a world's
        canon sheet and cast sheet — is actually sitting in a stable prefix."""
        t = self.totals()
        return t["cached_tokens"] / t["prompt_tokens"] if t["prompt_tokens"] else 0.0


def format_summary(data: dict) -> str:
    """The bill as a table for a terminal. Reads a `summary()` dict rather than a
    ledger, so it also prints one loaded back out of a finished run's checkpoint."""
    rows = [("STAGE", "CALLS", "RETRY", "IN", "CACHED", "OUT", "COST")]
    for name, acc in sorted(
        data.get("by_stage", {}).items(), key=lambda kv: -kv[1]["total_tokens"]
    ):
        rows.append((
            name, str(acc["calls"]), str(acc["retries"]),
            f"{acc['prompt_tokens']:,}", f"{acc['cached_tokens']:,}",
            f"{acc['completion_tokens']:,}",
            "—" if acc["cost_usd"] is None else f"${acc['cost_usd']:.4f}",
        ))
    t = data.get("totals") or _blank()
    rows.append((
        "TOTAL", str(t["calls"]), str(t["retries"]),
        f"{t['prompt_tokens']:,}", f"{t['cached_tokens']:,}",
        f"{t['completion_tokens']:,}",
        "—" if t["cost_usd"] is None else f"${t['cost_usd']:.4f}",
    ))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                  for i, cell in enumerate(row))
        for row in rows
    )
