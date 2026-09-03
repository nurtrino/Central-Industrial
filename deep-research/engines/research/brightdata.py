"""
Bright Data seam — the tool's answer to bot walls, CAPTCHAs and dead engines.

Three products, all optional, all env-driven, all degrade to "not available":

  * Web Unlocker (direct API)  — fetch a URL through Bright Data's unblocking stack
        (proxy rotation, CAPTCHA/JS-challenge solving, fingerprinting). Used when the
        local Chrome cannot read a page (Cloudflare interstitial, 403, timeout).
        env: BRIGHTDATA_API_KEY + BRIGHTDATA_UNLOCKER_ZONE
  * SERP API (direct API)      — Google/Bing/DuckDuckGo results as parsed JSON. Used as
        the search fallback when a browser engine returns nothing (blocked/captcha'd/
        selector drift). env: BRIGHTDATA_API_KEY + BRIGHTDATA_SERP_ZONE
  * Scraping Browser (CDP)     — a remote, unblocking Chrome the harness connects to over
        CDP INSTEAD of the bundled headless Chromium (hosted / headless mode only; the
        local headed Chrome keeps the user's logged-in profile).
        env: BRIGHTDATA_BROWSER_WSS (the full wss:// URL from the zone's Overview tab)

Both direct APIs share ONE endpoint:  POST https://api.brightdata.com/request
  headers: Authorization: Bearer <API_KEY>, Content-Type: application/json
  body:    {"zone": "<zone name>", "url": "<target>", "format": "raw"}
The response body is the target's raw response (HTML, or JSON when brd_json=1 is on
the SERP URL).

Never raises: every public function returns None / [] / "" on any failure and logs why.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote_plus

_API = "https://api.brightdata.com/request"
_TIMEOUT = 75

_TRUTHY = {"1", "true", "yes", "on"}


def _off(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "no", "off")


def _key() -> str:
    return os.environ.get("BRIGHTDATA_API_KEY", "").strip()


def unlocker_zone() -> str:
    return os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "").strip()


def serp_zone() -> str:
    return os.environ.get("BRIGHTDATA_SERP_ZONE", "").strip()


def browser_wss() -> str:
    return os.environ.get("BRIGHTDATA_BROWSER_WSS", "").strip()


def unlock_enabled() -> bool:
    return bool(_key() and unlocker_zone()) and not _off("DRT_BRIGHTDATA")


def serp_enabled() -> bool:
    return bool(_key() and serp_zone()) and not _off("DRT_BRIGHTDATA")


def browser_enabled() -> bool:
    return bool(browser_wss()) and not _off("DRT_BRIGHTDATA")


def status() -> dict:
    """For /api/health and the audit: which Bright Data products are configured."""
    return {"unlocker": unlock_enabled(), "serp": serp_enabled(), "browser": browser_enabled()}


def _request(zone: str, url: str, log, label: str):
    """One direct-API call. Returns (status_code, text) or (None, '') on transport failure."""
    import requests
    try:
        r = requests.post(_API, timeout=_TIMEOUT,
                          headers={"Authorization": f"Bearer {_key()}",
                                   "Content-Type": "application/json"},
                          json={"zone": zone, "url": url, "format": "raw"})
        if r.status_code != 200:
            log(f"[brightdata] {label} HTTP {r.status_code}: {r.text[:160]!r}")
            return r.status_code, r.text or ""
        return 200, r.text or ""
    except Exception as e:  # noqa: BLE001
        log(f"[brightdata] {label} transport error ({type(e).__name__}: {e})")
        return None, ""


# ── HTML → clean text + links (mirrors the browser harness's readability-lite) ──
_DROP_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form",
              "svg", "button", "iframe"]


def html_to_page(html: str, base_url: str = "") -> dict:
    """{'title','text','links':[{text,url}]} from raw HTML. Links are absolute http(s)."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return {"title": "", "text": "", "links": []}
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    links, seen = [], set()
    for a in soup.find_all("a", href=True)[:400]:
        href = urljoin(base_url, a["href"]) if base_url else a["href"]
        if not href.startswith("http"):
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()[:120]
        if not text or href in seen:
            continue
        seen.add(href)
        links.append({"text": text, "url": href})
        if len(links) >= 60:
            break
    # Forum threads wrap EVERY post in its own <article> (XenForo, Discourse, phpBB) — taking
    # the first would keep only the opening post. One article = the page's content;
    # several = read the enclosing main/body so replies survive.
    articles = soup.find_all("article")
    main = ((articles[0] if len(articles) == 1 else None)
            or soup.find("main") or soup.find(attrs={"role": "main"})
            or soup.body or soup)
    for t in main.find_all(_DROP_TAGS):
        t.decompose()
    text = main.get_text("\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return {"title": title, "text": text, "links": links}


def unlock_fetch(url: str, log=None) -> dict | None:
    """Fetch `url` through Web Unlocker. Returns {'url','title','text','links'} or None."""
    log = log or (lambda m: None)
    if not unlock_enabled() or not (url or "").startswith("http"):
        return None
    code, body = _request(unlocker_zone(), url, log, f"unlock {url[:70]}")
    if code != 200 or not body.strip():
        return None
    page = html_to_page(body, base_url=url)
    if len(page["text"]) < 120:
        log(f"[brightdata] unlock {url[:70]} -> thin ({len(page['text'])}c)")
        return None
    log(f"[brightdata] unlock {url[:70]} -> {len(page['text'])}c, {len(page['links'])} links")
    return {"url": url, "title": page["title"] or url, "text": page["text"],
            "links": page["links"]}


# ── SERP API ─────────────────────────────────────────────────────────────────
def _serp_url(engine: str, query: str, num: int) -> str:
    q = quote_plus(query)
    e = (engine or "google").lower()
    if e == "bing":
        return f"https://www.bing.com/search?q={q}&count={num}&brd_json=1"
    if e in ("duckduckgo", "ddg"):
        return f"https://duckduckgo.com/html/?q={q}&brd_json=1"
    return f"https://www.google.com/search?q={q}&num={num}&hl=en&brd_json=1"


def serp_search(query: str, engine: str = "google", num: int = 10, log=None) -> list[dict]:
    """Parsed organic results [{title,url,snippet}] via SERP API. [] on any failure."""
    log = log or (lambda m: None)
    if not serp_enabled() or not (query or "").strip():
        return []
    code, body = _request(serp_zone(), _serp_url(engine, query, num), log,
                          f"serp({engine}) {query[:60]!r}")
    if code != 200 or not body.strip():
        return []
    try:
        data = json.loads(body)
    except Exception:
        log("[brightdata] serp: response was not JSON (is brd_json parsing enabled on the zone?)")
        return []
    organic = data.get("organic") or data.get("results") or data.get("organic_results") or []
    out, seen = [], set()
    for it in organic:
        if not isinstance(it, dict):
            continue
        url = (it.get("link") or it.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({"title": (it.get("title") or url).strip(),
                    "url": url,
                    "snippet": (it.get("description") or it.get("snippet") or "").strip()})
        if len(out) >= num:
            break
    log(f"[brightdata] serp({engine}) {query[:60]!r} -> {len(out)} results")
    return out


# Manual smoke test:  python -m engines.research.brightdata "your query" [url]
if __name__ == "__main__":
    import sys
    from .agent import _load_env
    _load_env()
    print("status:", status())
    q = sys.argv[1] if len(sys.argv) > 1 else "James Webb Space Telescope discoveries"
    for r in serp_search(q, log=print)[:5]:
        print("  -", r["title"][:70], "|", r["url"][:70])
    if len(sys.argv) > 2:
        p = unlock_fetch(sys.argv[2], log=print)
        print("unlock:", (p or {}).get("title"), len((p or {}).get("text", "")), "chars")
