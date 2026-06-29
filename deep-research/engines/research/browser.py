"""
DRT browser harness — the visible, persistent Chrome the Deep Research tool drives.

First principles (locked with the user):
  * ONE dedicated, persistent Chrome profile (logins survive run-to-run).
  * Real Chrome (channel="chrome"), HEADED — the user watches every move.
  * Multi-tab: open as many pages as the agent wants, in one context.
  * Perception = clean extracted text per page; screenshot is a fallback only.
  * Engines: DuckDuckGo + Brave + Google (Google handled gently).

This module is the "hands". It exposes plain, synchronous browser actions
(search / open / screenshot). The agent "brain" (agent.py) calls these as tools.
No Claude / API code lives here — keep the mechanics isolated and testable.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .login import detect_login_wall, host_of

# Persistent profile lives beside the backend, gitignored. Logins persist here.
_PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".drt_chrome_profile")

# ── Environment-driven browser config ────────────────────────────────────────
# Local desktop default: HEADED real Chrome (the user watches it work).
# Hosted/headless (Render): set DRT_HEADED=0 and DRT_BROWSER_CHANNEL="" so it
# uses Playwright's bundled, containerized Chromium with sandbox disabled.
def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")

_HEADED_DEFAULT = _env_flag("DRT_HEADED", True)
# "chrome" = the locally installed Google Chrome; "" = Playwright's bundled Chromium.
_BROWSER_CHANNEL = os.environ.get("DRT_BROWSER_CHANNEL", "chrome").strip()

# Per-engine search URLs + the CSS that locates organic result anchors.
# We prefer server-rendered endpoints where possible so extraction is reliable
# even though the window is a full, visible Chrome.
_ENGINES = {
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "result_sel": "a.result__a",
        "snippet_sel": "a.result__snippet",
    },
    "brave": {
        "url": "https://search.brave.com/search?q={q}",
        "result_sel": "#results a:has(.title), #results .snippet a[href^='http']",
        "snippet_sel": ".snippet-description",
    },
    "google": {
        "url": "https://www.google.com/search?q={q}",
        "result_sel": "a:has(h3)",
        "snippet_sel": "div[data-sncf] , div.VwiC3b",
    },
}

# Result links the engines themselves emit that aren't real results.
_JUNK_HOST = re.compile(
    r"(duckduckgo\.com|google\.|gstatic|brave\.com/search|search\.brave|"
    r"youtube\.com/redirect|/aclk|/url\?|bing\.com/ck)", re.I)


def _normalize_href(href: str) -> str:
    """Resolve engine redirect/relative hrefs to a real absolute URL.

    DuckDuckGo's HTML results wrap the target in a redirect:
        //duckduckgo.com/l/?uddg=<percent-encoded real url>&rut=...
    Google occasionally uses /url?q=<real>. Protocol-relative //host -> https.
    """
    if not href:
        return ""
    from urllib.parse import urlparse, parse_qs, unquote
    # DDG / generic redirect params
    if "uddg=" in href or href.startswith("/l/") or "/l/?" in href:
        try:
            qs = parse_qs(urlparse(href if href.startswith("http") else "https:" + href.lstrip(":")).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass
    if href.startswith("/url?") or "/url?q=" in href:
        try:
            qs = parse_qs(urlparse("https://google.com" + href if href.startswith("/") else href).query)
            if "q" in qs:
                return qs["q"][0]
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""


@dataclass
class PageContent:
    url: str
    title: str = ""
    text: str = ""
    links: list = field(default_factory=list)   # [{text, url}]
    used_screenshot: bool = False
    screenshot_b64: str = ""
    error: str = ""


class DRTBrowser:
    """Owns one persistent, visible Chrome context and the tabs inside it."""

    def __init__(self, profile_dir: str = _PROFILE_DIR, headed: bool = None,
                 slow_mo_ms: int = 250, log=None, login_handler=None):
        self.profile_dir = profile_dir
        self.headed = _HEADED_DEFAULT if headed is None else headed
        self.slow_mo_ms = slow_mo_ms
        self._log = log or (lambda m: None)
        # login_handler(domain:str, page) -> bool ; resolves a login/paywall wall
        # (vault autofill then manual pause). Set by the agent/job layer.
        self.login_handler = login_handler
        self._auth_handled: set[str] = set()   # domains already attempted this run
        self._pw = None
        self._ctx = None

    # ── lifecycle ─────────────────────────────────────────────
    def start(self):
        os.makedirs(self.profile_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        # Container/headless hosts (Render) need --no-sandbox and a non-/dev/shm
        # temp dir; --start-maximized only matters when headed.
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        if self.headed:
            args.append("--start-maximized")
        launch_kwargs = dict(
            headless=not self.headed,
            slow_mo=self.slow_mo_ms,
            viewport={"width": 1380, "height": 900},
            args=args,
        )
        # Use the locally installed Chrome only when a channel is configured;
        # on Render DRT_BROWSER_CHANNEL="" → Playwright's bundled Chromium.
        if _BROWSER_CHANNEL:
            launch_kwargs["channel"] = _BROWSER_CHANNEL
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile_dir, **launch_kwargs)
        # Tabs the agent opens are pages on this context.
        if not self._ctx.pages:
            self._ctx.new_page()
        mode = "headed" if self.headed else "headless"
        chan = _BROWSER_CHANNEL or "chromium"
        self._log(f"[browser] {chan} up ({mode}) · profile={self.profile_dir}")
        return self

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
        self._ctx = self._pw = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ── tabs ──────────────────────────────────────────────────
    def new_tab(self):
        return self._ctx.new_page()

    @property
    def tab_count(self) -> int:
        return len(self._ctx.pages) if self._ctx else 0

    # ── search ────────────────────────────────────────────────
    def search(self, engine: str, query: str, limit: int = 10) -> list[SearchResult]:
        """Run one query on one engine in a fresh tab; return organic results."""
        engine = engine.lower()
        if engine not in _ENGINES:
            raise ValueError(f"unknown engine {engine!r}; have {list(_ENGINES)}")
        cfg = _ENGINES[engine]
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            from urllib.parse import quote_plus
            page.goto(cfg["url"].format(q=quote_plus(query)),
                      wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            self._maybe_dismiss_consent(page, engine)
            try:
                page.wait_for_selector(cfg["result_sel"], timeout=8000)
            except PWTimeout:
                self._log(f"[search] {engine}: no results selector (blocked/captcha?) q={query!r}")
            anchors = page.query_selector_all(cfg["result_sel"])
            seen = set()
            for a in anchors:
                href = _normalize_href((a.get_attribute("href") or "").strip())
                title = (a.inner_text() or "").strip().split("\n")[0]
                if not href or not href.startswith("http"):
                    continue
                if _JUNK_HOST.search(href):
                    continue
                key = href.split("#")[0]          # collapse in-page fragments
                if key in seen:
                    continue
                seen.add(key)
                out.append(SearchResult(title=title or href, url=key, engine=engine))
                if len(out) >= limit:
                    break
            self._log(f"[search] {engine} q={query!r} -> {len(out)} results")
        finally:
            page.close()
        return out

    def site_search(self, query: str, domain: str, engine: str = "duckduckgo",
                    limit: int = 10) -> list[SearchResult]:
        """Search WITHIN a domain via the engine's site: operator."""
        return self.search(engine, f"site:{domain} {query}", limit=limit)

    # ── proactive login ───────────────────────────────────────
    def ensure_logged_in(self, domain: str, creds: dict) -> tuple[bool, str]:
        """Proactively log into `domain` with stored creds BEFORE searching it (rather than
        waiting to hit a wall). Returns (ok, detail). Marks the domain auth-handled so open()
        won't re-attempt. If the login page shows no password field we treat the persistent
        profile as already authenticated (success) rather than a failure."""
        from .login import try_autofill
        if not creds:
            return False, "no stored credentials"
        page = self.new_tab()
        try:
            target = (creds.get("login_url") or "").strip() or f"https://{domain}/"
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                self._human_pause()
            except Exception as e:  # noqa: BLE001
                self._auth_handled.add(domain)
                return False, f"could not open login page ({type(e).__name__})"
            self._auth_handled.add(domain)
            try:
                has_pw = page.locator("input[type=password]").count() > 0
            except Exception:
                has_pw = True
            if not has_pw:
                # No login form on the login URL → already signed in via the persistent profile.
                return True, "already authenticated (persistent profile)"
            ok = try_autofill(page, creds, self._log)
            return (True, "logged in via stored credentials") if ok else \
                   (False, "stored login did not go through (wrong password, 2FA, or captcha)")
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ── open / read ───────────────────────────────────────────
    def open(self, url: str, min_chars: int = 200, timeout_ms: int = 30000) -> PageContent:
        """Open a URL in a fresh tab and return cleaned readable text.

        Falls back to a screenshot (vision) only if text extraction is too thin.
        """
        page = self.new_tab()
        pc = PageContent(url=url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            self._human_pause()
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeout:
                pass
            pc.title = (page.title() or "").strip()
            pc.text = self._extract_text(page)
            pc.links = self._extract_links(page)

            # Login / paywall / bot-check wall? Try to resolve it (vault → pause),
            # then re-read the now-unlocked page. Once per domain per run.
            wall, why = detect_login_wall(page, pc.text)
            dom = host_of(url)
            if wall and self.login_handler and dom and dom not in self._auth_handled:
                self._auth_handled.add(dom)
                self._log(f"[open] login wall on {dom} ({why}) — invoking handler")
                try:
                    resolved = self.login_handler(dom, page)
                except Exception as e:  # noqa: BLE001
                    resolved = False
                    self._log(f"[open] login handler error: {e}")
                if resolved:
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except PWTimeout:
                        pass
                    pc.title = (page.title() or "").strip() or pc.title
                    pc.text = self._extract_text(page)
                    pc.links = self._extract_links(page) or pc.links
                    self._log(f"[open] post-login re-read {dom}: {len(pc.text)}c")

            if len(pc.text) < min_chars:
                # Text too thin — capture a screenshot for the vision fallback.
                import base64
                shot = page.screenshot(full_page=False)
                pc.screenshot_b64 = base64.b64encode(shot).decode()
                pc.used_screenshot = True
                self._log(f"[open] thin text ({len(pc.text)}c) -> screenshot fallback · {url}")
            else:
                self._log(f"[open] {url} -> {len(pc.text)}c, {len(pc.links)} links")
        except Exception as e:  # noqa: BLE001 - surface to agent as tool error text
            pc.error = f"{type(e).__name__}: {e}"
            self._log(f"[open] ERROR {url} :: {pc.error}")
        finally:
            page.close()
        return pc

    # ── internals ─────────────────────────────────────────────
    def _extract_text(self, page) -> str:
        """Readability-lite: prefer <article>/<main>, strip chrome, collapse ws."""
        try:
            txt = page.evaluate(
                """() => {
                    const drop = ['script','style','noscript','nav','header','footer',
                                  'aside','form','svg','button'];
                    const pick = document.querySelector('article')
                              || document.querySelector('main')
                              || document.querySelector('[role=main]')
                              || document.body;
                    if (!pick) return '';
                    const clone = pick.cloneNode(true);
                    drop.forEach(t => clone.querySelectorAll(t).forEach(n => n.remove()));
                    return clone.innerText || '';
                }"""
            ) or ""
        except Exception:
            txt = ""
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
        return txt.strip()

    def _extract_links(self, page, limit: int = 60) -> list:
        try:
            links = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href^=http]'))
                        .slice(0, 300)
                        .map(a => ({text:(a.innerText||'').trim().slice(0,120), url:a.href}))
                        .filter(l => l.text)"""
            ) or []
        except Exception:
            links = []
        # dedup by url
        seen, out = set(), []
        for l in links:
            if l["url"] in seen:
                continue
            seen.add(l["url"])
            out.append(l)
            if len(out) >= limit:
                break
        return out

    def _maybe_dismiss_consent(self, page, engine: str):
        """Best-effort click of Google/EU consent walls so results render."""
        if engine != "google":
            return
        for sel in ["button:has-text('Accept all')",
                    "button:has-text('I agree')",
                    "button#L2AGLb",
                    "div[role=none] button:has-text('Accept')"]:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    self._human_pause()
                    break
            except Exception:
                continue

    def _human_pause(self):
        time.sleep(0.4 + (self.slow_mo_ms / 1000.0))


# Manual smoke test:  python -m engines.research.browser "your query"
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Renaissance Technologies Medallion fund returns"
    br = DRTBrowser(log=print).start()
    try:
        for eng in ("duckduckgo", "brave", "google"):
            print(f"\n===== {eng.upper()} =====")
            res = br.search(eng, q, limit=5)
            for i, r in enumerate(res, 1):
                print(f" {i}. {r.title[:80]}\n    {r.url}")
            if res:
                print(f"\n  --- opening top result from {eng} ---")
                pc = br.open(res[0].url)
                print(f"  title: {pc.title[:90]}")
                print(f"  text[:400]: {pc.text[:400]!r}")
                print(f"  links: {len(pc.links)} · screenshot_fallback={pc.used_screenshot} · err={pc.error}")
                break
        print(f"\n[done] tabs open: {br.tab_count}")
        time.sleep(2)
    finally:
        br.close()
