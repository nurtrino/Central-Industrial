"""
Tavily seam — the standard search API behind most open-source deep-research agents.

Two uses in this tool:
  * `tavily_search`  — an agent tool (web_search engine="tavily") and a baseline-sweep
        source, and the automatic fallback when a browser engine returns nothing.
  * `tavily_extract` — page-text fallback when Chrome (and Bright Data) cannot read a URL.

Enabled by default whenever TAVILY_API_KEY is set; DRT_TAVILY=0 turns it off.
Never raises: [] / "" on any failure.

API (docs.tavily.com):
  POST https://api.tavily.com/search   {query, search_depth, max_results, include_domains}
       -> {results:[{title,url,content,score}]}
  POST https://api.tavily.com/extract  {urls, extract_depth, format}
       -> {results:[{url, raw_content}], failed_results:[...]}
  Auth: Authorization: Bearer tvly-...
"""

from __future__ import annotations

import os

_SEARCH = "https://api.tavily.com/search"
_EXTRACT = "https://api.tavily.com/extract"
_TIMEOUT = 45


def _key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def is_enabled() -> bool:
    return bool(_key()) and os.environ.get("DRT_TAVILY", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def tavily_search(query: str, num: int = 10, include_domains=None, depth: str = "advanced",
                  topic: str = "general", log=None) -> list[dict]:
    """[{title,url,snippet}] — [] on any failure."""
    log = log or (lambda m: None)
    if not is_enabled() or not (query or "").strip():
        return []
    import requests
    body = {"query": query.strip(), "search_depth": depth,
            "max_results": max(1, min(int(num or 10), 20)), "topic": topic}
    if include_domains:
        body["include_domains"] = [d for d in include_domains if d][:300]
    try:
        r = requests.post(_SEARCH, headers=_headers(), json=body, timeout=_TIMEOUT)
        if r.status_code != 200:
            log(f"[tavily] search HTTP {r.status_code}: {r.text[:140]!r}")
            return []
        rows = (r.json() or {}).get("results") or []
    except Exception as e:  # noqa: BLE001
        log(f"[tavily] search failed ({type(e).__name__}: {e})")
        return []
    out, seen = [], set()
    for it in rows:
        url = (it.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({"title": (it.get("title") or url).strip(), "url": url,
                    "snippet": (it.get("content") or "").strip()[:400]})
    log(f"[tavily] search {query[:60]!r} -> {len(out)} results")
    return out


def tavily_extract(url: str, log=None) -> str:
    """Clean page text (markdown) for one URL — '' on any failure."""
    log = log or (lambda m: None)
    if not is_enabled() or not (url or "").startswith("http"):
        return ""
    import requests
    try:
        r = requests.post(_EXTRACT, headers=_headers(), timeout=_TIMEOUT,
                          json={"urls": [url], "extract_depth": "advanced", "format": "markdown"})
        if r.status_code != 200:
            log(f"[tavily] extract HTTP {r.status_code}: {r.text[:140]!r}")
            return ""
        for it in (r.json() or {}).get("results") or []:
            txt = (it.get("raw_content") or "").strip()
            if txt:
                log(f"[tavily] extract {url[:70]} -> {len(txt)}c")
                return txt
    except Exception as e:  # noqa: BLE001
        log(f"[tavily] extract failed ({type(e).__name__}: {e})")
    return ""


# Manual smoke test:  python -m engines.research.tavily_search "your query"
if __name__ == "__main__":
    import sys
    from .agent import _load_env
    _load_env()
    q = " ".join(sys.argv[1:]) or "James Webb Space Telescope discoveries"
    print("enabled:", is_enabled())
    for r in tavily_search(q, num=5, log=print):
        print("  -", r["title"][:70], "|", r["url"][:70])
