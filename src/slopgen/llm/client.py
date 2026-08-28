"""LLM chat client for any OpenAI-compatible provider.

Supported providers (all speak the OpenAI chat-completions dialect):
  deepseek   — api.deepseek.com
  gemini     — Google's OpenAI-compatibility endpoint
  openrouter — openrouter.ai (has free-tier models, handy for testing)
  custom     — any OpenAI-compatible server (set base_url/model/key_env yourself)
"""

from __future__ import annotations

import base64
import json
import os
import time

import httpx

from ..config.models import LLMConfig, LLMProfile
from .usage import Call, Prices, UsageLedger

LLMSettings = LLMConfig | LLMProfile  # both carry provider/base_url/model/key_env/temperature


class LLMError(Exception):
    pass


PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "key_env": "GEMINI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat-v3.1:free",
        "key_env": "OPENROUTER_API_KEY",
    },
    "custom": {"base_url": "", "model": "", "key_env": "LLM_API_KEY"},
}

# popular model choices per provider, offered as presets in the TUI
MODEL_PRESETS: dict[str, list[str]] = {
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    "openrouter": [
        "deepseek/deepseek-chat-v3.1:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat-v3.1",
        "google/gemini-2.5-flash",
        "anthropic/claude-haiku-4.5",
    ],
    "custom": [],
}


def _usage(raw: dict) -> tuple[int, int, int]:
    """(prompt, cached, completion) tokens out of a provider's ``usage`` block.

    Every OpenAI-compatible provider reports the first and the last the same way and
    the middle one differently, which is exactly the number that matters here (see
    `llm/usage`): DeepSeek splits the input into `prompt_cache_hit_tokens` /
    `prompt_cache_miss_tokens`, OpenAI and its imitators nest a `cached_tokens` under
    `prompt_tokens_details`, and a provider with no prompt cache reports neither.
    Whatever the shape, `prompt_tokens` is always the WHOLE input and `cached` a part
    of it — a provider reporting only the miss is turned back into that here, so the
    two numbers never mean different things depending on who answered."""
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    details = raw.get("prompt_tokens_details") or {}
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is None and raw.get("prompt_cache_miss_tokens") is not None:
        cached = prompt - int(raw["prompt_cache_miss_tokens"])
    return prompt, max(min(int(cached or 0), prompt), 0), completion


def resolve_provider(cfg: LLMSettings) -> tuple[str, str, str]:
    """Effective (base_url, model, key_env): empty config fields fall back to provider defaults."""
    p = PROVIDERS.get(cfg.provider, PROVIDERS["custom"])
    return (cfg.base_url or p["base_url"], cfg.model or p["model"], cfg.key_env or p["key_env"])


class ChatLLM:
    def __init__(self, cfg: LLMSettings, ledger: UsageLedger | None = None):
        base_url, self.model, key_env = resolve_provider(cfg)
        key = os.environ.get(key_env, "")
        if not key:
            raise LLMError(
                f"{key_env} is not set (put it in .env), or pick another provider "
                "in configs/slopgen.toml [llm] / TUI Config → LLM"
            )
        if not base_url:
            raise LLMError("llm.base_url is empty for the 'custom' provider")
        self.cfg = cfg
        # what this run is spending, and what it is spending it on (see llm/usage).
        # Optional: a client built for a one-off errand (the TUI compiling a character
        # while the operator edits it) belongs to no run and has nothing to bill.
        self.ledger = ledger
        self.profile = getattr(cfg, "name", "") or cfg.provider
        # a cache hit costs less than a miss on every provider that has a cache; where
        # the operator priced only the input, a hit is priced the same as a miss rather
        # than as free, which is the conservative way to be wrong.
        self.prices = Prices(
            inp=float(getattr(cfg, "price_in", 0.0) or 0.0),
            cached=float(getattr(cfg, "price_cached", 0.0) or getattr(cfg, "price_in", 0.0) or 0.0),
            out=float(getattr(cfg, "price_out", 0.0) or 0.0),
        )
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )

    MAX_TOOL_ROUNDS = 5

    def _bill(self, kind: str, attempt: int, raw: dict, seconds: float,
              ok: bool = True) -> None:
        """Write one round trip into the run's ledger. Never raises: an unparseable
        usage block is a missing number, not a failed stage."""
        if self.ledger is None:
            return
        try:
            prompt, cached, completion = _usage(raw if isinstance(raw, dict) else {})
            self.ledger.record(Call(
                stage="", kind=kind, profile=self.profile, model=self.model,
                attempt=attempt, prompt_tokens=prompt, cached_tokens=cached,
                completion_tokens=completion, seconds=round(seconds, 2), ok=ok,
                cost_usd=self.prices.cost(prompt, cached, completion),
            ))
        except Exception:  # noqa: BLE001 — accounting never kills a run
            pass

    def _post(self, messages: list[dict], tools: list | None, json_mode: bool = True,
              kind: str = "", attempt: int = 0) -> dict:
        body: dict = {
            "model": self.model,
            "temperature": self.cfg.temperature,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        elif json_mode:
            # response_format conflicts with tool use on most providers
            body["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        try:
            r = self.client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        except Exception:
            # a call that never came back still cost time, and a run whose bill shows
            # eight failures is diagnosing itself
            self._bill(kind, attempt, {}, time.monotonic() - t0, ok=False)
            raise
        self._bill(kind, attempt, data.get("usage") or {}, time.monotonic() - t0)
        return data["choices"][0]["message"]

    def _run_tools(self, messages: list[dict], tools: list, bound: dict | None = None,
                   kind: str = "", attempt: int = 0) -> str:
        """Drive the tool-calling loop: let the model call tools until it answers.

        `bound` holds this call's own executors (a tool closed over run-specific data,
        such as one fandom's lore); they take precedence over the stateless registry."""
        from .tools import TOOL_EXECUTORS

        for _ in range(self.MAX_TOOL_ROUNDS):
            msg = self._post(messages, tools, kind=kind, attempt=attempt)
            calls = msg.get("tool_calls")
            if not calls:
                return msg.get("content") or ""
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                executor = (bound or {}).get(name) or TOOL_EXECUTORS.get(name)
                if executor is None:
                    result = f"unknown tool '{name}'"
                else:
                    # a model that mis-spells an argument must not take the stage down:
                    # the error goes back as the tool's result, which is the one form of
                    # feedback it can actually act on. Free-tier models do this often,
                    # and a whole mode's script now depends on tool calls succeeding.
                    try:
                        result = executor(**args)
                    except Exception as e:
                        result = f"tool '{name}' failed: {e}. Check the arguments and try again."
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": str(result)})
        # ran out of rounds — force a final answer without tools
        return self._post(messages, None, kind=kind, attempt=attempt).get("content") or ""

    def describe_image(self, prompt: str, image: bytes, mime: str = "image/jpeg") -> str:
        """Vision call: send an image + prompt, return the model's plain-text answer.
        Needs a vision-capable model (Gemini, most OpenRouter models); text-only
        providers like plain DeepSeek raise, which the caller surfaces."""
        b64 = base64.b64encode(image).decode()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }]
        return self._post(messages, None, json_mode=False, kind="vision").get("content") or ""

    ATTEMPTS = 3  # transient transport errors (connection reset) are common on free tiers

    def complete_text(self, kind: str, system: str, user: str) -> str:
        """One plain-prose chat completion — no JSON, no tools. For the callers whose
        answer IS prose (the `lore_lookup` archivist), where JSON mode would only add
        a wrapper to strip and one more way to fail."""
        import time

        last_err: Exception | None = None
        for attempt in range(self.ATTEMPTS):
            try:
                return self._post(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    None,
                    json_mode=False,
                    kind=kind,
                    attempt=attempt,
                ).get("content") or ""
            except (httpx.HTTPError, KeyError) as e:
                last_err = e
                if i < self.ATTEMPTS - 1 and isinstance(e, httpx.TransportError):
                    time.sleep(1.5 * (i + 1))
        raise LLMError(f"LLM call '{kind}' failed: {last_err}")

    def complete_json(
        self,
        kind: str,
        system: str,
        user: str,
        web_search: bool = False,
        tools: dict | None = None,
        attempt: int = 0,
    ) -> dict:
        """One JSON-mode chat completion; retries on bad JSON or transport errors.

        When `web_search` is on, the `web_search` tool is offered to the model
        (standard OpenAI function calling) — the model decides when to call it,
        we execute the search and feed results back before it answers.

        `tools` adds this call's own function tools, as `{name: (schema, executor)}`,
        for a tool that has to be closed over run-specific data (see
        `llm.tools.make_lore_lookup`).

        `attempt` is the caller's own retry counter, for a stage that re-asks because
        the ANSWER was unusable rather than because the request failed (the script
        stage re-asks a window that came back off its length budget). It only labels
        the call in the run's ledger, so a bill can say how much of itself went on
        re-asking — the request is otherwise identical.

        Careful: offering ANY tool costs JSON mode — `response_format` conflicts with
        tool use on most providers (see `_post`), so the model is merely *asked* for
        JSON. The retry loop below, with its fence-stripping, is what makes that safe;
        a prompt used this way must state "JSON only" explicitly."""
        import time

        from .tools import WEB_SEARCH_TOOL

        schemas = [WEB_SEARCH_TOOL] if web_search else []
        bound = {name: ex for name, (_, ex) in (tools or {}).items()}
        schemas += [schema for schema, _ in (tools or {}).values()]
        last_err: Exception | None = None
        for i in range(self.ATTEMPTS):
            attempt = attempt + i if i else attempt
            try:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                content = (
                    self._run_tools(messages, schemas, bound, kind=kind, attempt=attempt)
                    if schemas
                    else self._post(messages, None, kind=kind, attempt=attempt).get("content") or ""
                )
                # some free models wrap JSON in markdown fences despite json mode
                content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
                return json.loads(content)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                # back off before retrying a transport error (reset/timeout); the
                # server may have dropped the pooled connection — the next try
                # reconnects. No backoff needed for the last attempt.
                if i < self.ATTEMPTS - 1 and isinstance(e, httpx.TransportError):
                    time.sleep(1.5 * (i + 1))
        raise LLMError(f"LLM call '{kind}' failed: {last_err}")
