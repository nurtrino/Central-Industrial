"""
DRT Stage 1 — Claude's own web search.

Before the visible browser does anything, Claude runs the Anthropic API's
server-side web_search tool: a fast, broad, headless baseline pass. It returns
a short findings summary plus the source URLs it cited. Later stages use this to
target gaps, avoid re-treading the same ground, and decide which gated/specialist
sources are worth chasing.

Server-side tool: the API executes each search and returns the results inline
(web_search_tool_result blocks) — no client tool-result loop needed. If the
key/tier lacks web_search, we degrade gracefully (empty result → pipeline skips
to the browser stages).
"""

from __future__ import annotations

from .models import get_model
_MODEL = get_model("search")   # Stage-1 web search (Sonnet by default)
# How many server-side searches Stage 1 may run, by depth tier.
# Raised per the "max_uses 15-20 for research" guidance — Stage 1 is a cheap,
# fast baseline, so let it dig harder on multi-entity DD questions.
STAGE1_USES = {"quick": 5, "standard": 10, "deep": 18}

# Prefer the 2026 tool (dynamic result filtering — Claude filters results before
# they enter context: better accuracy, fewer tokens). Fall back to the older
# version if the key/tier doesn't expose it.
_WEB_SEARCH_VERSIONS = ["web_search_20260209", "web_search_20250305"]

import os as _os
_BLOCKLIST_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__)))), "config", "drt_blocklist.json")


def _load_blocklist() -> list:
    """Editable anti-SEO blocklist applied to Stage-1 web search (config/drt_blocklist.json)."""
    try:
        import json
        with open(_BLOCKLIST_PATH, encoding="utf-8") as fh:
            doms = json.load(fh).get("blocked_domains", [])
        return [d for d in doms if isinstance(d, str) and d.strip()]
    except Exception:
        return []


def _collect(response, sources: list, seen: set, text_parts: list):
    """Pull text + cited/returned source URLs out of one API response."""
    for block in response.content:
        btype = getattr(block, "type", "")
        if btype == "text":
            if getattr(block, "text", "").strip():
                text_parts.append(block.text)
            for cit in (getattr(block, "citations", None) or []):
                url = getattr(cit, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": getattr(cit, "title", "") or url})
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None) or []
            for r in content:
                url = getattr(r, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": getattr(r, "title", "") or url})


def run_api_search(query: str, clarifications: str = "", depth: str = "standard",
                   client=None, log=None, max_uses_override: int | None = None) -> dict:
    """Stage 1. Returns {findings_md, sources:[{title,url}], used} (used=False if
    web_search unavailable).

    max_uses_override: when the research planner has decided how API-heavy this question
    should be, it passes the planned search count here (overrides the per-tier default).
    """
    import anthropic
    import os

    log = log or (lambda m: None)
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return {"findings_md": "", "sources": [], "used": False}
        client = anthropic.Anthropic(api_key=api_key)

    max_uses = max_uses_override if (max_uses_override and max_uses_override > 0) \
        else STAGE1_USES.get(depth, 10)
    blocked = _load_blocklist()
    system = (
        "You are the fast baseline pass of a deep web-research tool. "
        "Use web search to establish what is reliably known about the user's question. "
        "Be broad but efficient. Then write a TERSE findings brief: the key established "
        "facts (with the strongest sources), open questions still unresolved, and any "
        "specialist or gated sources (forums, paywalled analysts, primary filings) that a "
        "deeper pass should pursue. Signal only — no filler. If little is found, say so plainly."
    )
    user = f"RESEARCH QUESTION:\n{query}"
    if clarifications:
        user += f"\n\nCLARIFICATIONS:\n{clarifications}"

    for vi, ver in enumerate(_WEB_SEARCH_VERSIONS):
        sources: list = []
        seen: set = set()
        text_parts: list = []
        tool_def = {"type": ver, "name": "web_search", "max_uses": max_uses}
        if blocked:
            tool_def["blocked_domains"] = blocked
        try:
            messages = [{"role": "user", "content": user}]
            guard = 0
            while True:
                guard += 1
                resp = client.messages.create(model=_MODEL, max_tokens=4096, system=system,
                                              tools=[tool_def], messages=messages)
                _collect(resp, sources, seen, text_parts)
                # web_search is server-side; only 'pause_turn' needs us to continue the turn.
                if resp.stop_reason == "pause_turn" and guard < 6:
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break
            findings = "\n\n".join(text_parts).strip()
            log(f"[stage1] {ver} -> {len(findings)}c findings, {len(sources)} sources "
                f"(blocked {len(blocked)} domains)")
            return {"findings_md": findings, "sources": sources, "used": True, "tool_version": ver}
        except Exception as e:  # noqa: BLE001 — degrade gracefully
            msg = str(e).lower()
            # Newer tool version not available on this key/tier → try the older one.
            if vi < len(_WEB_SEARCH_VERSIONS) - 1 and (
                    "not_found" in msg or "invalid" in msg or ver in msg or "tool" in msg):
                log(f"[stage1] {ver} unavailable ({type(e).__name__}); falling back")
                continue
            log(f"[stage1] web_search unavailable ({type(e).__name__}); skipping Stage 1")
            return {"findings_md": "", "sources": [], "used": False}
    return {"findings_md": "", "sources": [], "used": False}


# Manual smoke test:  python -m engines.research.api_search "your question"
if __name__ == "__main__":
    import sys
    from .agent import _load_env
    _load_env()
    q = " ".join(sys.argv[1:]) or "Renaissance Technologies Medallion fund returns and secrecy"
    out = run_api_search(q, depth="quick", log=lambda m: print(m, flush=True))
    print("used:", out["used"], "| sources:", len(out["sources"]))
    for s in out["sources"][:10]:
        print("  -", (s["title"] or "")[:60], "|", s["url"][:70])
    print("\n--- findings_md ---\n" + out["findings_md"][:1800])
