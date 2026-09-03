"""
Brave Search API seam — the official JSON API for the `brave` engine.

Why an API here when the charter says "browser only": that rule targeted Anthropic's
sandboxed server-side search tool (robots.txt-bound, blocked). Brave's own Search API
is the same index the browser scrape reads, with no selector drift and no CAPTCHA
page; scraping it buys nothing. When BRAVE_API_KEY is set, `engine="brave"` goes
through the API first and the browser scrape is the fallback (see agent._engine_search).

API: GET https://api.search.brave.com/res/v1/web/search?q=...&count=N
     headers: X-Subscription-Token: <key>, Accept: application/json
     -> {"web": {"results": [{"title","url","description"}]}}
Enabled whenever BRAVE_API_KEY is set; DRT_BRAVE_API=0 turns it off. Never raises.
"""

from __future__ import annotations

import os

_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = 20


def _key() -> str:
    return (os.environ.get("BRAVE_API_KEY") or os.environ.get("DATA_BRAVE_API_KEY") or "").strip()


def is_enabled() -> bool:
    return bool(_key()) and os.environ.get("DRT_BRAVE_API", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def brave_search(query: str, num: int = 10, log=None) -> list[dict]:
    """[{title,url,snippet}] — [] on any failure."""
    log = log or (lambda m: None)
    if not is_enabled() or not (query or "").strip():
        return []
    import requests
    try:
        r = requests.get(_URL, timeout=_TIMEOUT,
                         params={"q": query.strip(), "count": max(1, min(int(num or 10), 20))},
                         headers={"X-Subscription-Token": _key(), "Accept": "application/json"})
        if r.status_code != 200:
            log(f"[brave-api] HTTP {r.status_code}: {r.text[:140]!r}")
            return []
        rows = ((r.json() or {}).get("web") or {}).get("results") or []
    except Exception as e:  # noqa: BLE001
        log(f"[brave-api] failed ({type(e).__name__}: {e})")
        return []
    out, seen = [], set()
    for it in rows:
        url = (it.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({"title": (it.get("title") or url).strip(), "url": url,
                    "snippet": (it.get("description") or "").strip()[:400]})
    log(f"[brave-api] {query[:60]!r} -> {len(out)} results")
    return out


# Manual smoke test:  python -m engines.research.brave_search "your query"
if __name__ == "__main__":
    import sys
    from .agent import _load_env
    _load_env()
    q = " ".join(sys.argv[1:]) or "James Webb Space Telescope discoveries"
    print("enabled:", is_enabled())
    for r in brave_search(q, num=5, log=print):
        print("  -", r["title"][:70], "|", r["url"][:70])
