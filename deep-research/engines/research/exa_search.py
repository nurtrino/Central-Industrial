"""
DRT neural discovery — Exa (exa.ai) search as an agent tool.

Exa is an embeddings/neural search engine. Its edge over keyword engines is
(a) semantic discovery of niche/long-tail pages a keyword query would miss, and
(b) find_similar — given one good page, return conceptually similar ones (source
expansion). Both are surfaced to the Stage-2 browser agent as tools; the URLs Exa
returns still flow through the normal open_page → Chrome → goal-extraction path,
so real Chrome does the actual reading (we keep dodging bot-protection on content).

Exa also retrieves cleaned page text; we use that ONLY as a resilience fallback
when Chrome's open() fails (403 / login wall with no creds) so the text isn't lost.

This module is the ONLY place exa_py is imported. Like Stage 1 (api_search.py) it
degrades gracefully: no key / DRT_EXA off / any API error → empty result, never
raises. Master switch: env DRT_EXA in {1,true,yes,on}; key: env EXA_API_KEY.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def _key() -> str:
    return os.environ.get("EXA_API_KEY", "").strip()


def is_enabled() -> bool:
    """True only when both the master toggle is on AND a key is present."""
    return os.environ.get("DRT_EXA", "").strip().lower() in _TRUTHY and bool(_key())


def _client():
    """Lazily build an Exa client; None if the SDK or key is unavailable."""
    if not _key():
        return None
    try:
        from exa_py import Exa
    except Exception:
        return None
    try:
        return Exa(_key())
    except Exception:
        return None


def _rows(resp) -> list[dict]:
    """Normalize an Exa response object into [{title, url, snippet}]."""
    out: list[dict] = []
    for r in (getattr(resp, "results", None) or []):
        url = (getattr(r, "url", "") or "").strip()
        if not url:
            continue
        out.append({
            "title": (getattr(r, "title", "") or url).strip(),
            "url": url,
            "snippet": (getattr(r, "highlights", None) and " ".join(r.highlights)[:300])
                       or (getattr(r, "text", "") or "")[:300],
        })
    return out


def exa_search(query: str, num: int = 10, log=None) -> list[dict]:
    """Neural web search. Returns [{title, url, snippet}] (empty on any failure)."""
    log = log or (lambda m: None)
    exa = _client()
    if exa is None:
        return []
    try:
        # contents={"highlights": True} → query-relevant excerpts per result (token-efficient,
        # the recommended content mode for agent workflows). Without it, results carry no snippet.
        resp = exa.search(query, num_results=num, type="auto",
                          contents={"highlights": True})
        rows = _rows(resp)
        log(f"[exa] search q={query!r} -> {len(rows)} results")
        return rows
    except Exception as e:  # noqa: BLE001 — degrade gracefully
        log(f"[exa] search failed ({type(e).__name__}): {e}")
        return []


def exa_find_similar(url: str, num: int = 10, log=None) -> list[dict]:
    """Given a seed URL, return conceptually similar pages (source expansion)."""
    log = log or (lambda m: None)
    exa = _client()
    if exa is None or not (url or "").strip():
        return []
    try:
        resp = exa.find_similar(url, num_results=num, contents={"highlights": True})
        rows = _rows(resp)
        log(f"[exa] find_similar {url[:60]!r} -> {len(rows)} results")
        return rows
    except Exception as e:  # noqa: BLE001
        log(f"[exa] find_similar failed ({type(e).__name__}): {e}")
        return []


def exa_contents(url: str, log=None) -> str:
    """Resilience fallback: fetch Exa's cleaned text for a URL Chrome couldn't open.
    Returns '' on any failure."""
    log = log or (lambda m: None)
    exa = _client()
    if exa is None or not (url or "").strip():
        return ""
    try:
        # On /contents, text/highlights are TOP-LEVEL kwargs (not nested in `contents`).
        # Cap length to control token cost (full uncapped text can blow up context).
        resp = exa.get_contents([url], text={"max_characters": 20000})
        for r in (getattr(resp, "results", None) or []):
            txt = (getattr(r, "text", "") or "").strip()
            if txt:
                log(f"[exa] contents fallback {url[:60]!r} -> {len(txt)}c")
                return txt
    except Exception as e:  # noqa: BLE001
        log(f"[exa] contents fallback failed ({type(e).__name__}): {e}")
    return ""


# Manual smoke test:  python -m engines.research.exa_search "your query"
if __name__ == "__main__":
    import sys
    from .agent import _load_env
    _load_env()
    q = " ".join(sys.argv[1:]) or "candid practitioner views on Renaissance Technologies culture"
    print("enabled:", is_enabled(), "| key set:", bool(_key()))
    for r in exa_search(q, num=5, log=lambda m: print(m, flush=True)):
        print("  -", r["title"][:70], "|", r["url"][:70])
