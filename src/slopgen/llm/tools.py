"""Tools the LLM can call via OpenAI-style function calling.

Unlike a provider-side RAG plugin, these are *real* tools: their JSON schema is
sent in the request's ``tools`` field, the model decides when to invoke one, and
slopgen executes it and feeds the result back. This works on any provider that
supports tool use (OpenAI, DeepSeek, OpenRouter, Gemini's compat endpoint, …),
not just OpenRouter.

A tool that needs no run-specific state (``web_search``) lives in the module-level
``TOOLS`` / ``TOOL_EXECUTORS`` registry — add one by writing an executor and appending
its schema there. A tool bound to ONE run's data (``lore_lookup``, which answers out
of this fandom's documents) cannot: it is built per run by a factory and handed to
``ChatLLM.complete_json`` as the ``tools`` argument.
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


LORE_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lore_lookup",
        "description": (
            "Ask the archivist of this world a question about it. They have every "
            "surviving document in front of them and answer with the exact wording, "
            "names, numbers and dates the records use. Call this BEFORE naming any "
            "specific person, place, date, number, custom or rule that the canon "
            "sheet does not already spell out — never to invent what is missing. "
            "Ask follow-up questions freely; one question at a time, in full."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "A specific question about the world, e.g. 'What exactly is "
                        "the winter carry and who does it?' Vague questions get "
                        "vague answers."
                    ),
                },
            },
            "required": ["question"],
        },
    },
}

# The archivist speaks from inside the world too: an answer that says "the author
# never specified" hands the writer the one framing the whole mode exists to avoid
# (see stages/fandom_script.WORLD_RULE). Silence in the records is silence in the
# world — a gap the writer may treat as unknown, disputed, or its own to fill.
_LORE_SYSTEM = (
    "You are the archivist of the world described in the documents below. Answer the "
    "question using ONLY those documents, quoting their exact wording, names, numbers "
    "and dates. Be specific and brief — a few sentences, plus the quoted fragments the "
    "answer rests on.\n"
    "The world is real and the documents are its records. Never call it fiction, a "
    "story, a setting, a canon or someone's work, and never mention an author.\n"
    "If the records do not answer the question, say exactly what they DO say nearby and "
    "state plainly that the rest is not recorded — never invent a fact and never "
    "speculate beyond flagging it as your own guess.\n\n"
    "THE RECORDS:\n{lore}"
)


def make_lore_lookup(llm, lore: str):
    """Build the ``lore_lookup`` executor for one run: a librarian LLM call that reads
    the WHOLE lore document and answers one question out of it.

    The whole document per call is the cost of this tool, which is exactly why it is
    the last resort rather than the only channel — the compiled canon sheet and the
    outline's per-stretch detail lists carry the bulk of the world for free (see
    stages/fandom_script)."""

    def lore_lookup(question: str = "", **_) -> str:
        q = (question or "").strip()
        if not q:
            return "ask a specific question about the world"
        try:
            return llm.complete_text(
                "lore_lookup", _LORE_SYSTEM.format(lore=lore), q
            ).strip() or "the records say nothing about that"
        except Exception as e:  # a failed lookup must not take the script down
            return f"the archives could not be reached ({e}); write around this detail"

    return lore_lookup


# name -> (schema, executor); stateless tools only (see the module docstring)
TOOLS = {"web_search": WEB_SEARCH_TOOL}
TOOL_EXECUTORS = {"web_search": run_web_search}
