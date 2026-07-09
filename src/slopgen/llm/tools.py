"""Tools the LLM can call via OpenAI-style function calling.

Unlike a provider-side RAG plugin, these are *real* tools: their JSON schema is
sent in the request's ``tools`` field, the model decides when to invoke one, and
slopgen executes it and feeds the result back. This works on any provider that
supports tool use (OpenAI, DeepSeek, OpenRouter, Gemini's compat endpoint, …),
not just OpenRouter.

Add a tool by writing an executor and appending its schema to ``TOOLS`` /
``TOOL_EXECUTORS``.
"""

from __future__ import annotations

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current, factual information — real events, people, "
            "companies, dates, numbers. Call this to verify facts BEFORE writing so you "
            "never invent names or events."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "description": "How many results to return (1-8).",
                },
            },
            "required": ["query"],
        },
    },
}


def run_web_search(query: str, max_results: int = 5) -> str:
    """Execute a DuckDuckGo web search (no API key needed) and return a text digest."""
    n = max(1, min(int(max_results or 5), 8))
    try:
        from ddgs import DDGS  # current package name
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older name
        except ImportError:
            return "web search unavailable: install the 'ddgs' package"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=n))
    except Exception as e:  # network/rate-limit — degrade gracefully
        return f"web search failed: {e}"
    if not results:
        return f"no web results for '{query}'"
    lines = []
    for r in results:
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()
        href = (r.get("href") or r.get("url") or "").strip()
        lines.append(f"- {title}\n  {body}\n  {href}")
    return f"Web results for '{query}':\n" + "\n".join(lines)


# name -> (schema, executor)
TOOLS = {"web_search": WEB_SEARCH_TOOL}
TOOL_EXECUTORS = {"web_search": run_web_search}
