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

import httpx

from ..config.models import LLMConfig, LLMProfile

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


def resolve_provider(cfg: LLMSettings) -> tuple[str, str, str]:
    """Effective (base_url, model, key_env): empty config fields fall back to provider defaults."""
    p = PROVIDERS.get(cfg.provider, PROVIDERS["custom"])
    return (cfg.base_url or p["base_url"], cfg.model or p["model"], cfg.key_env or p["key_env"])


class ChatLLM:
    def __init__(self, cfg: LLMSettings):
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
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )

    MAX_TOOL_ROUNDS = 5

    def _post(self, messages: list[dict], tools: list | None) -> dict:
        body: dict = {
            "model": self.model,
            "temperature": self.cfg.temperature,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        else:
            # response_format conflicts with tool use on most providers
            body["response_format"] = {"type": "json_object"}
        r = self.client.post("/chat/completions", json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def _run_tools(self, messages: list[dict], tools: list) -> str:
        """Drive the tool-calling loop: let the model call tools until it answers."""
        from .tools import TOOL_EXECUTORS

        for _ in range(self.MAX_TOOL_ROUNDS):
            msg = self._post(messages, tools)
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
                executor = TOOL_EXECUTORS.get(name)
                result = executor(**args) if executor else f"unknown tool '{name}'"
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": str(result)})
        # ran out of rounds — force a final answer without tools
        return self._post(messages, None).get("content") or ""

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
        body = {"model": self.model, "temperature": self.cfg.temperature, "messages": messages}
        r = self.client.post("/chat/completions", json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""

    ATTEMPTS = 3  # transient transport errors (connection reset) are common on free tiers

    def complete_json(self, kind: str, system: str, user: str, web_search: bool = False) -> dict:
        """One JSON-mode chat completion; retries on bad JSON or transport errors.

        When `web_search` is on, the `web_search` tool is offered to the model
        (standard OpenAI function calling) — the model decides when to call it,
        we execute the search and feed results back before it answers."""
        import time

        from .tools import WEB_SEARCH_TOOL

        tools = [WEB_SEARCH_TOOL] if web_search else None
        last_err: Exception | None = None
        for attempt in range(self.ATTEMPTS):
            try:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                content = self._run_tools(messages, tools) if tools else self._post(messages, None).get("content") or ""
                # some free models wrap JSON in markdown fences despite json mode
                content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
                return json.loads(content)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                # back off before retrying a transport error (reset/timeout); the
                # server may have dropped the pooled connection — the next try
                # reconnects. No backoff needed for the last attempt.
                if attempt < self.ATTEMPTS - 1 and isinstance(e, httpx.TransportError):
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM call '{kind}' failed: {last_err}")
