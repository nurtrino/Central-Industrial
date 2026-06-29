"""
Search + fetch seam for the vendored Odysseus engine.

The repo's native providers (SearXNG/Brave/Tavily) need a self-hosted stack or API
keys. This adapter implements the no-key **DuckDuckGo** path (httpx + BeautifulSoup,
vendored from Odysseus's own HTML fallback) plus an httpx/bs4 page fetch with an
SSRF guard, exposing exactly the four names deep_research.py imports:
  _get_search_settings, _build_provider_chain, _call_provider, fetch_webpage_content

Brave / Tavily are used automatically IF their API keys are present in the env, so
the user can opt into a stronger backend without code changes.
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 20


# ── provider settings / chain (deep_research imports these) ───────────
def _get_search_settings() -> dict:
    """Force the no-key DuckDuckGo provider by default; honor env opt-ins."""
    prov = (os.environ.get("ODYSSEUS_SEARCH_PROVIDER") or "duckduckgo").strip().lower()
    return {"search_provider": prov, "research_search_provider": prov}


def _build_provider_chain(primary: str) -> list:
    chain = [primary] if primary and primary != "disabled" else []
    for fb in ("duckduckgo",):
        if fb not in chain:
            chain.append(fb)
    return chain


def _call_provider(provider_name: str, query: str, count: int, time_filter: str = None) -> list:
    if provider_name == "brave":
        return _brave(query, count)
    if provider_name == "tavily":
        return _tavily(query, count)
    # default / fallback
    return _duckduckgo(query, count)


# ── DuckDuckGo (no key) — vendored from Odysseus's HTML fallback ──────
def _resolve_ddg_redirect(raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if "duckduckgo.com/l/" in raw or "uddg=" in raw:
        try:
            qs = parse_qs(urlparse(raw).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass
    return raw


def _duckduckgo(query: str, count: int) -> list:
    # Prefer the ddgs library if installed; else the HTML endpoint.
    try:
        from ddgs import DDGS
        out = []
        for item in DDGS().text(query, max_results=count):
            url = item.get("href", "")
            if url:
                out.append({"title": item.get("title", ""), "url": url,
                            "snippet": item.get("body", "")})
        if out:
            return out
    except Exception:
        pass
    try:
        r = httpx.get("https://html.duckduckgo.com/html/",
                      params={"q": query}, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for res in soup.select(".result")[:count]:
            link = res.select_one(".result__a")
            if not link:
                continue
            url = _resolve_ddg_redirect(link.get("href", ""))
            if not url:
                continue
            snip = res.select_one(".result__snippet")
            out.append({"title": link.get_text(" ", strip=True), "url": url,
                        "snippet": snip.get_text(" ", strip=True) if snip else ""})
        return out
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return []


def _brave(query: str, count: int) -> list:
    key = (os.environ.get("DATA_BRAVE_API_KEY") or os.environ.get("BRAVE_API_KEY") or "").strip()
    if not key:
        return []
    try:
        r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                      params={"q": query, "count": count},
                      headers={"X-Subscription-Token": key, "Accept": "application/json"},
                      timeout=_TIMEOUT)
        r.raise_for_status()
        web = (r.json().get("web") or {}).get("results", [])
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("description", "")} for x in web if x.get("url")]
    except Exception as e:
        logger.warning(f"Brave search failed: {e}")
        return []


def _tavily(query: str, count: int) -> list:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return []
    try:
        r = httpx.post("https://api.tavily.com/search",
                       json={"api_key": key, "query": query, "max_results": count},
                       timeout=_TIMEOUT)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("content", "")}
                for x in r.json().get("results", []) if x.get("url")]
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return []


# ── page fetch (httpx + bs4) with SSRF guard ─────────────────────────
_PRIVATE = (
    ipaddress.ip_network("0.0.0.0/8"), ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"), ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_public(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        for fam, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if any(ip in net for net in _PRIVATE):
                return False
        return True
    except Exception:
        return False


def _empty(url: str, err: str) -> dict:
    return {"url": url, "title": "", "content": "", "og_image": "",
            "success": False, "error": err}


def _curl_get(url: str, timeout: int):
    """GET via curl (built into Windows) — its TLS/header fingerprint passes the
    bot-protection (Cloudflare etc.) that 403s headless httpx in this environment.
    Returns (status_code, content_type, body_bytes)."""
    import subprocess, tempfile
    body = tempfile.NamedTemporaryFile(delete=False, suffix=".bin"); body.close()
    hdr = tempfile.NamedTemporaryFile(delete=False, suffix=".hdr"); hdr.close()
    try:
        p = subprocess.run(
            ["curl", "-sL", "--max-time", str(int(timeout)),
             "-A", _UA,
             "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "-H", "Accept-Language: en-US,en;q=0.9",
             "-o", body.name, "-D", hdr.name, "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=int(timeout) + 10)
        status = int((p.stdout or "0").strip() or 0)
        ctype = ""
        try:
            with open(hdr.name, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("content-type:"):
                        ctype = line.split(":", 1)[1].strip().lower()
        except Exception:
            pass
        with open(body.name, "rb") as f:
            content = f.read()
        return status, ctype, content
    finally:
        for fn in (body.name, hdr.name):
            try:
                os.unlink(fn)
            except Exception:
                pass


def fetch_webpage_content(url: str, timeout: int = 10, retry_attempt: int = 0) -> dict:
    """Fetch a URL and return cleaned readable text (dict with success/content/title)."""
    if not _is_public(url):
        return _empty(url, "blocked: non-public or unresolvable host")
    try:
        status, ctype, raw = _curl_get(url, timeout)
    except Exception as e:
        return _empty(url, f"{type(e).__name__}: {e}")
    if status >= 400 or status == 0:
        return _empty(url, f"HTTP {status}")

    path = url.lower().split("?", 1)[0]

    # PDF
    if "application/pdf" in ctype or path.endswith(".pdf"):
        try:
            import io
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                txt = "\n\n".join((p.extract_text() or "") for p in pdf.pages)
            return {"url": url, "title": os.path.basename(path), "content": txt.strip(),
                    "og_image": "", "success": bool(txt.strip()),
                    "error": "" if txt.strip() else "no PDF text"}
        except Exception as e:
            return _empty(url, f"PDF extract failed: {e}")

    text_body = raw.decode("utf-8", errors="replace")

    # Plain text / JSON / markdown
    is_html = "html" in ctype
    if not is_html and (ctype.startswith("text/") or "json" in ctype
                        or path.endswith((".md", ".txt", ".json"))):
        body = text_body.strip()
        return {"url": url, "title": os.path.basename(path) or url, "content": body,
                "og_image": "", "success": bool(body), "error": "" if body else "empty body"}

    # HTML → cleaned text
    try:
        soup = BeautifulSoup(text_body, "html.parser")
    except Exception as e:
        return _empty(url, f"parse failed: {e}")
    title = (soup.title.get_text(strip=True) if soup.title else "") or url
    og = ""
    tag = soup.find("meta", attrs={"property": "og:image"})
    if tag and tag.get("content"):
        og = tag["content"]
    main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    for t in main.find_all(["script", "style", "noscript", "nav", "header", "footer",
                            "aside", "form", "svg", "button"]):
        t.decompose()
    text = main.get_text("\n", strip=True)
    import re as _re
    text = _re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return {"url": url, "title": title, "content": text, "og_image": og,
            "success": bool(text), "error": "" if text else "no readable text"}
