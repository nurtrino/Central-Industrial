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

import concurrent.futures as _cf
import functools
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .login import detect_login_wall, host_of
from . import brightdata as _bd

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
    blocked: bool = False      # a bot-check / challenge page that never cleared


# ── bot-challenge interstitials (Cloudflare "Just a moment…", etc.) ──────────
# These render as a SHORT page whose text is the challenge copy. The login-wall
# detector never matched this wording, so the agent used to receive the
# interstitial AS the page and burn opens on it. We now recognise it, wait for the
# challenge to clear (headed Chrome usually passes a managed challenge on its own
# in a few seconds), and if it doesn't, flag the page as blocked so the caller's
# fallback chain (Bright Data → Tavily → Exa) takes over.
_CHALLENGE_TITLES = ("just a moment", "attention required", "access denied",
                     "please wait", "checking your browser", "security verification")
_CHALLENGE_TEXT = ("performing security verification", "verify you are human",
                   "verifying you are human", "verifying you are not a bot",
                   "checking your browser before accessing", "checking if the site connection is secure",
                   "enable javascript and cookies to continue", "cf-chl", "ray id:",
                   "needs to review the security of your connection", "press & hold",
                   "complete the security check", "unusual traffic from your computer network")
_CHALLENGE_WAIT_MS = 9000


def _is_challenge(title: str, text: str) -> bool:
    t = (title or "").lower()
    x = (text or "").lower()
    if any(k in t for k in _CHALLENGE_TITLES) and len(x) < 2500:
        return True
    return len(x) < 2500 and any(k in x for k in _CHALLENGE_TEXT)


# Cookie names that specifically mean "this visitor is AUTHENTICATED" on common
# forum/platform stacks. Deliberately excludes guest/session cookies (xf_session,
# bare *session*, analytics) — those exist for logged-out visitors too, and counting
# them was the source of the false "already authenticated" verdict.
_AUTH_COOKIE_NAMES = {
    "xf_user",                    # XenForo (avforums, rivian, wilders, wiim)
    "user_session", "logged_in", "dotcom_user",  # GitHub
    "reddit_session", "token_v2",  # Reddit
    "auth_token", "twid",          # X / Twitter
    "flarum_remember",             # Flarum
    "sapu",                        # Seeking Alpha
    "li_at",                       # LinkedIn
    "bb_userid", "vbulletin_user",  # vBulletin
}
# Substrings that reliably indicate an authenticated cookie across stacks. Kept narrow
# on purpose — no bare "session"/"sessionid" (guests have those). Under-reporting a
# login is safe (falls back to public search); over-reporting is the bug we're fixing.
_AUTH_COOKIE_PATTERNS = ("_user", "auth_token", "remember_", "logged_in", "loggedin",
                         "wordpress_logged_in", "phpbb3_sid")

# Forum platform per domain — drives which native-search adapter to use. Unknown domains
# fall through to the {q} template / search-box path. Extend as the catalog grows.
_PLATFORM_HINTS = {
    "avforums.com": "xenforo",
    "rivianforums.com": "xenforo",
    "wilderssecurity.com": "xenforo",
    "forum.wiimhome.com": "xenforo",
    "teslamotorsclub.com": "xenforo",
    "head-fi.org": "xenforo",
    "audiosciencereview.com": "xenforo",
    "forum.openwrt.org": "discourse",
    "forum.bambulab.com": "discourse",
    "reddit.com": "reddit",
    "github.com": "github",
}

# ── browser-owner thread ─────────────────────────────────────────────────────
# Playwright's sync API is thread-bound (greenlet-based): every call must come from
# the thread that started it. The research agent now runs several LANES in parallel
# threads (see agent.py), so the browser owns ONE dedicated thread and every public
# action from any other thread is queued to it and awaited. Model turns overlap
# freely; browser actions serialize. Internal calls made while already on the owner
# thread run directly (no re-queue, no deadlock).
def _on_browser_thread(fn):
    @functools.wraps(fn)
    def _wrapped(self, *a, **kw):
        return self._call(lambda: fn(self, *a, **kw))
    return _wrapped


class DRTBrowser:
    """Owns one persistent, visible Chrome context and the tabs inside it.

    Anti-bot stance (2026-09-03): no UA spoofing / fingerprint shims. The local headed
    Chrome is a real browser with the user's real profile; anything it still cannot read
    (Cloudflare challenges, 403s) is fetched through Bright Data's Web Unlocker by the
    caller, and the headless/hosted mode connects to Bright Data's Scraping Browser
    (BRIGHTDATA_BROWSER_WSS) instead of the bundled Chromium when configured."""

    def __init__(self, profile_dir: str = _PROFILE_DIR, headed: Optional[bool] = None,
                 slow_mo_ms: Optional[int] = None, log=None, login_handler=None):
        self.profile_dir = profile_dir
        self.headed = _HEADED_DEFAULT if headed is None else headed
        # Pacing is env-tunable (DRT_SLOWMO, ms). Default 80 — deep runs open 100-250
        # pages and must stay watchable without taking half a day.
        if slow_mo_ms is None:
            try:
                slow_mo_ms = int(os.environ.get("DRT_SLOWMO", "") or 80)
            except ValueError:
                slow_mo_ms = 80
        self.slow_mo_ms = slow_mo_ms
        self._log = log or (lambda m: None)
        # login_handler(domain:str, page) -> bool ; resolves a login/paywall wall
        # (vault autofill then manual pause). Set by the agent/job layer.
        self.login_handler = login_handler
        self._auth_handled: set[str] = set()   # domains already attempted this run
        self._pw = None
        self._ctx = None
        self._remote = None          # Bright Data Scraping Browser (CDP) when in use
        self.remote_browser = False
        self._exec = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="drt-browser")
        self._thread: Optional[threading.Thread] = None

    def _call(self, fn):
        """Run fn on the browser-owner thread (directly if we're already on it)."""
        if self._thread is None or threading.current_thread() is self._thread:
            return fn()
        return self._exec.submit(fn).result()

    # ── lifecycle ─────────────────────────────────────────────
    def start(self):
        return self._exec.submit(self._start_impl).result()

    def _start_impl(self):
        self._thread = threading.current_thread()
        os.makedirs(self.profile_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        # Headless + Bright Data Scraping Browser configured → connect to the remote,
        # unblocking Chrome over CDP instead of launching the bundled Chromium. (Headed
        # local runs keep the persistent profile — that is where the logins live.)
        if not self.headed and _bd.browser_enabled():
            try:
                self._remote = self._pw.chromium.connect_over_cdp(_bd.browser_wss(), timeout=60000)
                ctxs = self._remote.contexts
                self._ctx = ctxs[0] if ctxs else self._remote.new_context(
                    viewport={"width": 1380, "height": 900})
                if not self._ctx.pages:
                    self._ctx.new_page()
                self.remote_browser = True
                self._log("[browser] Bright Data Scraping Browser connected (CDP, headless)")
                return self
            except Exception as e:  # noqa: BLE001
                self._log(f"[browser] Bright Data Scraping Browser connect failed "
                          f"({type(e).__name__}: {e}) — launching local Chromium instead")
                self._remote = None
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
            self._call(self._close_impl)
        finally:
            try:
                self._exec.shutdown(wait=False)
            except Exception:
                pass

    def _close_impl(self):
        try:
            if self._ctx:
                self._ctx.close()
            if self._remote:
                try:
                    self._remote.close()
                except Exception:
                    pass
        finally:
            if self._pw:
                self._pw.stop()
        self._ctx = self._pw = self._remote = None

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
    @_on_browser_thread
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

    @_on_browser_thread
    def site_native_search(self, search_url: str, query: str,
                           limit: int = 10) -> list[SearchResult]:
        """Search a site with ITS OWN search function (search_url template with {q}).

        Runs inside the logged-in persistent profile, so a gated forum's native search
        reaches content no external engine ever indexed. Extraction is generic — result
        anchors with substantive text, junk/nav filtered. Any failure → [].
        """
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            url = search_url.replace("{q}", quote_plus(query))
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeout:
                pass
            # JS-aware: many forums/SPAs render results AFTER load — wait for result rows
            # to appear (or the anchor set to settle) before reading the LIVE DOM.
            self._wait_for_results(page)
            anchors = page.evaluate(
                """() => {
                    // Prefer a real results container; fall back to main/body.
                    const sel = ['.search-results','.fps-results','#search-results',
                                 '[data-testid="search-results"]','ol.search-results',
                                 '.block-body','.contentRow','main','article'];
                    let scope = null;
                    for (const s of sel) { const el = document.querySelector(s); if (el) { scope = el; break; } }
                    scope = scope || document.body;
                    if (!scope) return [];
                    return Array.from(scope.querySelectorAll('a[href^=http]'))
                        .slice(0, 500)
                        .map(a => ({text:(a.innerText||'').trim().slice(0,140), url:a.href}));
                }"""
            ) or []
            base = url.split("#")[0]
            seen = set()
            for a in anchors:
                href = (a.get("url") or "").strip()
                text = (a.get("text") or "").strip()
                if len(text) < 15 or not href.startswith("http"):
                    continue        # nav chrome / icon links — not results
                if _JUNK_HOST.search(href):
                    continue
                key = href.split("#")[0]        # collapse same-page fragments
                if not key or key == base or key in seen:
                    continue
                seen.add(key)
                out.append(SearchResult(title=text, url=key, engine="native"))
                if len(out) >= limit:
                    break
            self._log(f"[search] native {url[:80]} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001 - native search is best-effort by design
            self._log(f"[search] native search ERROR ({type(e).__name__}: {e}) -> 0 results")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _wait_for_results(self, page, budget_ms: int = 8000):
        """Wait for a JS-rendered results list to appear, OR for the link set to settle.
        Cheap poll — returns as soon as known result rows show up or the DOM stops growing."""
        import time as _t
        deadline = _t.time() + budget_ms / 1000.0
        prev = -1
        stable = 0
        while _t.time() < deadline:
            try:
                found = page.evaluate(
                    """() => {
                        const rows = document.querySelectorAll(
                          '.fps-result, .search-result, li.search-result, .contentRow, '
                          + '.block-row, [data-testid="post-container"], article.search-result, '
                          + '.search-results li, .SearchResult, div[data-testid="search-post-unit"]');
                        const links = document.querySelectorAll('a[href]');
                        return {rows: rows.length, links: links.length};
                    }"""
                ) or {"rows": 0, "links": 0}
            except Exception:
                found = {"rows": 0, "links": 0}
            if found.get("rows", 0) >= 3:
                return
            n = found.get("links", 0)
            stable = stable + 1 if n == prev else 0
            prev = n
            if stable >= 2 and n > 0:   # link set settled → JS likely done
                return
            _t.sleep(0.6)

    # ── smart native search (platform adapters) ───────────────
    @_on_browser_thread
    def native_search(self, domain: str, query: str, search_url: str = "",
                       limit: int = 10) -> list[SearchResult]:
        """Search a site's OWN search, choosing the most reliable method for its platform:
        Discourse → JSON API; Reddit → server-rendered old.reddit; XenForo → drive the
        search box; else an explicit {q} template (JS-aware) or the search box. Returns []
        on failure; callers fall back to the engine site: operator."""
        dom = (domain or "").lstrip(".").lower()
        try:
            plat = self._platform_of(dom)
            if plat == "discourse":
                r = self._discourse_search(dom, query, limit)
                if r:
                    return r
            elif plat == "reddit":
                r = self._reddit_search(dom, query, limit)
                if r:
                    return r
            elif plat == "xenforo":
                r = self._xenforo_search(dom, query, limit)
                if r:
                    return r
            elif plat == "github":
                r = self._github_search(dom, query, limit)
                if r:
                    return r
            # Explicit template (JS-aware) next, then the universal search-box driver.
            if search_url and "{q}" in search_url:
                r = self.site_native_search(search_url, query, limit)
                if r:
                    return r
            return self._search_box(dom, query, limit)
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] native_search {dom} ERROR ({type(e).__name__}: {e})")
            return []

    def _platform_of(self, domain: str) -> str:
        """Best-effort forum-platform id from a small domain map plus generic hints."""
        d = domain.lstrip(".").lower()
        if d in _PLATFORM_HINTS:
            return _PLATFORM_HINTS[d]
        if "reddit.com" in d:
            return "reddit"
        return "generic"

    def _discourse_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """Discourse. The /search.json API is heavily rate-limited for anonymous users
        ('performed this action too many times'), so use the HTML full-page search and read
        the Ember-rendered result links. (Being logged in raises the limit; where login
        failed we still get public results here until the HTML limit bites, then the caller
        falls back to the site: engine.)"""
        import json as _json
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            # Try the JSON API once (clean when not rate-limited)…
            page.goto(f"https://{domain}/search.json?q={quote_plus(query)}",
                      wait_until="domcontentloaded", timeout=30000)
            raw = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            data = {}
            try:
                data = _json.loads(raw)
            except Exception:
                data = {}
            if isinstance(data, dict) and (data.get("posts") or data.get("topics")) and not data.get("failed"):
                topics = {t.get("id"): t for t in (data.get("topics") or [])}
                seen = set()
                ordered = [topics.get(p.get("topic_id")) for p in (data.get("posts") or [])]
                ordered += (data.get("topics") or [])
                for t in ordered:
                    if not t or t.get("id") in seen or len(out) >= limit:
                        continue
                    seen.add(t.get("id"))
                    out.append(SearchResult(title=(t.get("title") or "").strip(),
                               url=f"https://{domain}/t/{t.get('slug','t')}/{t.get('id')}",
                               engine="discourse"))
                self._log(f"[search] discourse(json) {domain} q={query!r} -> {len(out)} results")
                return out
            # …else the HTML search page (Ember renders result rows we then read).
            page.goto(f"https://{domain}/search?q={quote_plus(query)}",
                      wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            self._wait_for_results(page)
            try:
                page.wait_for_timeout(1800)   # Ember renders the result list after settle
            except Exception:
                pass
            anchors = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                        '.fps-result a.search-link, .search-results a[href*="/t/"], '
                        + '.search-results-page a[href*="/t/"], .topic-list-item a.title, '
                        + 'a.search-link, a[href*="/t/"]'))
                        .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))"""
            ) or []
            seen = set()
            for a in anchors:
                href = (a.get("url") or "").split("?")[0]
                text = (a.get("text") or "").strip()
                if "/t/" not in href or href in seen or len(text) < 8:
                    continue
                seen.add(href)
                out.append(SearchResult(title=text, url=href, engine="discourse"))
                if len(out) >= limit:
                    break
            self._log(f"[search] discourse(html) {domain} q={query!r} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] discourse {domain} ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _github_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """GitHub public repo search via the REST API — anonymous (no login needed for
        public search), returns real repos as clean JSON. The web /search page is a React
        SPA whose HTML scrape yields nav tabs/footer junk, so we avoid it entirely. On a
        non-200 (anon search is ~10 req/min) we return [] so the caller falls back to site:."""
        import requests
        from urllib.parse import quote_plus
        out: list[SearchResult] = []
        try:
            r = requests.get(
                f"https://api.github.com/search/repositories?q={quote_plus(query)}"
                f"&sort=stars&per_page={max(limit, 5)}",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "deep-research-tool"},
                timeout=12)
            if r.status_code == 200:
                for it in (r.json().get("items") or [])[:limit]:
                    name = it.get("full_name") or ""
                    desc = (it.get("description") or "").strip()
                    stars = it.get("stargazers_count")
                    label = name + (f" — {desc}" if desc else "")
                    if isinstance(stars, int):
                        label += f"  (★{stars:,})"
                    out.append(SearchResult(title=label[:180],
                                            url=it.get("html_url") or "", engine="github"))
            else:
                self._log(f"[search] github api HTTP {r.status_code} (rate-limited?) -> site: fallback")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] github api ERROR ({type(e).__name__})")
        self._log(f"[search] github q={query!r} -> {len(out)} results")
        return out

    def _reddit_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """old.reddit.com/search is server-rendered — no JS gymnastics."""
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            url = f"https://old.reddit.com/search?q={quote_plus(query)}&sort=relevance"
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            anchors = page.evaluate(
                """() => Array.from(document.querySelectorAll('a.search-title, .search-result-link a.search-title'))
                        .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))"""
            ) or []
            seen = set()
            for a in anchors:
                href = (a.get("url") or "").split("?")[0]
                text = (a.get("text") or "").strip()
                if not href.startswith("http") or href in seen or len(text) < 8:
                    continue
                seen.add(href)
                out.append(SearchResult(title=text, url=href, engine="reddit"))
                if len(out) >= limit:
                    break
            self._log(f"[search] reddit {domain} q={query!r} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] reddit {domain} ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _xenforo_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """XenForo search is POST-driven; the reliable path is to type into the header
        search box and submit, landing on a server-rendered results page."""
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            # 1) The GET quick-search renders results directly on most XenForo installs.
            page.goto(f"https://{domain}/search/?q={quote_plus(query)}&o=relevance",
                      wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            self._wait_for_results(page)
            _extract = ("""() => Array.from(document.querySelectorAll(
                    '.p-body-content a[href*="/threads/"], .contentRow-title a[href*="/threads/"], '
                    + '.contentRow-title a[href*="/posts/"], .block-body a[href*="/threads/"], '
                    + '.discussionListItem h3.title a[href*="/threads/"], '   /* XenForo 1.x */
                    + '.titleText a[href*="/threads/"], #content a[href*="/threads/"]'))
                    .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))""")
            anchors = page.evaluate(_extract) or []
            # Only if the GET produced nothing, submit via the VISIBLE keywords input
            # (XenForo renders TWO name=keywords fields; the first is hidden).
            if not anchors:
                try:
                    box = page.locator("input[name='keywords']:visible").first
                    box.fill(query, timeout=5000)
                    box.press("Enter")
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    self._human_pause()
                    self._wait_for_results(page)
                    anchors = page.evaluate(_extract) or []
                except Exception as e:  # noqa: BLE001
                    self._log(f"[search] xenforo {domain} box-submit failed ({type(e).__name__})")
            # Tier 2 (XenForo 1.x / exotic skins): if the container selectors found nothing
            # but the page clearly IS results (not the empty form), take any thread links.
            if not anchors:
                try:
                    on_results = page.locator(".searchResult, .discussionListItem, .contentRow").count() > 0
                except Exception:
                    on_results = False
                if on_results:
                    anchors = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href*="/threads/"]'))
                                .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))"""
                    ) or []
            seen = set()
            _skip = ("/whats-new/", "/forums/", "/members/", "/search/", "/login", "/tags/",
                     "/latest-activity", "/recent-activity")
            for a in anchors:
                href = (a.get("url") or "").split("#")[0]
                text = (a.get("text") or "").strip()
                if not href.startswith("http") or href in seen or len(text) < 10:
                    continue
                if any(s in href for s in _skip):
                    continue
                if "/threads/" not in href and "/posts/" not in href:
                    continue
                seen.add(href)
                out.append(SearchResult(title=text, url=href, engine="xenforo"))
                if len(out) >= limit:
                    break
            self._log(f"[search] xenforo {domain} q={query!r} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] xenforo {domain} ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _search_box(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """Universal fallback: load the site, find its search input, type + submit, read
        the rendered results. Works on many CMSes where no clean URL/JSON search exists."""
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            box = None
            for sel in ("input[type='search']", "input[name='q']", "input[name='query']",
                        "input[name='keywords']", "input[aria-label*='earch' i]",
                        "input[placeholder*='earch' i]"):
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        box = loc
                        break
                except Exception:
                    continue
            if box is None:
                return out
            box.fill(query, timeout=4000)
            box.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            self._wait_for_results(page)
            base = f"https://{domain}"
            anchors = page.evaluate(
                """() => {
                    const scope = document.querySelector('main')||document.querySelector('#content')||document.body;
                    return Array.from(scope.querySelectorAll('a[href^=http]'))
                        .slice(0,400).map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}));
                }"""
            ) or []
            seen = set()
            for a in anchors:
                href = (a.get("url") or "").split("#")[0]
                text = (a.get("text") or "").strip()
                if not href.startswith("http") or href in seen or len(text) < 15:
                    continue
                if _JUNK_HOST.search(href) or href.rstrip("/") == base:
                    continue
                seen.add(href)
                out.append(SearchResult(title=text, url=href, engine="searchbox"))
                if len(out) >= limit:
                    break
            self._log(f"[search] searchbox {domain} q={query!r} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] searchbox {domain} ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    # ── login-state detection ─────────────────────────────────
    def _domain_cookies(self, domain: str) -> list:
        try:
            cks = self._ctx.cookies()
        except Exception:
            return []
        d = domain.lstrip(".").lower()
        out = []
        for c in cks:
            cd = (c.get("domain") or "").lstrip(".").lower()
            if cd == d or d.endswith("." + cd) or cd.endswith("." + d):
                out.append(c)
        return out

    def _has_auth_cookie(self, domain: str) -> bool:
        """True only if a STRONG 'you are authenticated' cookie exists for this domain.
        Guest/visitor cookies (xf_session, generic *session*, analytics) deliberately do
        NOT count — treating those as 'logged in' is the exact false-positive we fix here.
        Biased to UNDER-report: a missed login just falls back to public search + reactive
        login, whereas a false 'logged in' silently harvests public content while claiming
        success."""
        for c in self._domain_cookies(domain):
            name = (c.get("name") or "")
            low = name.lower()
            if not (c.get("value") or "").strip():
                continue
            if name in _AUTH_COOKIE_NAMES or any(p in low for p in _AUTH_COOKIE_PATTERNS):
                return True
        return False

    # ── proactive login ───────────────────────────────────────
    @_on_browser_thread
    def ensure_logged_in(self, domain: str, creds: dict) -> tuple[bool, str]:
        """Establish a logged-in session for `domain` before searching it. Returns
        (logged_in, detail). `logged_in` is True ONLY when an authenticated session is
        actually verified (a strong auth cookie, or an autofill that cleared the login
        form). A benign 'not logged in — public content only' is a soft state, NOT a
        failure: many forums are fully searchable logged out, and a real wall later
        still triggers the reactive login handler (we only mark the domain auth-handled
        on a confirmed login, so failures don't suppress that retry)."""
        from .login import try_autofill
        creds = creds or {}
        # 1) Already have a live authenticated session in the persistent profile?
        if self._has_auth_cookie(domain):
            self._auth_handled.add(domain)
            return True, "already logged in (session in profile)"
        # 2) Nothing to log in with → proceed logged out; a wall (if any) handled later.
        if not (creds.get("username") or "").strip():
            return False, "not logged in — no stored credentials (public content only)"
        page = self.new_tab()
        try:
            target = (creds.get("login_url") or "").strip() or f"https://{domain}/"
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                self._human_pause()
            except Exception as e:  # noqa: BLE001
                return False, f"could not open login page ({type(e).__name__})"
            try:
                has_pw = page.locator("input[type=password]").count() > 0
            except Exception:
                has_pw = False
            if not has_pw:
                # No form here and no auth cookie → we are NOT authenticated. Don't pretend.
                # (Usually means no login_url is configured, so the form was never reached.)
                return False, "not logged in — no login form at login URL (public content only)"
            try_autofill(page, creds, self._log)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            # 3) Verify: a strong auth cookie appeared …
            if self._has_auth_cookie(domain):
                self._auth_handled.add(domain)
                return True, "logged in via stored credentials"
            # … or the login form is gone and we're off the login page (sites we lack a
            #    known cookie name for).
            still_pw = False
            try:
                still_pw = page.locator("input[type=password]").count() > 0
            except Exception:
                pass
            if not still_pw and "login" not in (page.url or "").lower() \
                    and "signin" not in (page.url or "").lower():
                self._auth_handled.add(domain)
                return True, "logged in via stored credentials (form cleared)"
            return False, "stored login did not establish a session (2FA, captcha, or selector mismatch)"
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ── open / read ───────────────────────────────────────────
    @_on_browser_thread
    def open(self, url: str, min_chars: int = 200, timeout_ms: int = 30000) -> PageContent:
        """Open a URL in a fresh tab and return cleaned readable text.

        A bot-challenge interstitial is waited out; if it never clears the page comes
        back with `blocked=True` + an error so the caller can fetch it another way.
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

            # Bot challenge (Cloudflare "Just a moment…" & co.)? Give it time to clear,
            # then re-read. Still walled → blocked (caller falls back to Bright Data).
            if _is_challenge(pc.title, pc.text):
                dom0 = host_of(url)
                self._log(f"[open] bot challenge on {dom0} — waiting for it to clear…")
                deadline = time.time() + _CHALLENGE_WAIT_MS / 1000.0
                cleared = False
                while time.time() < deadline:
                    time.sleep(0.8)
                    try:
                        t2 = (page.title() or "").strip()
                        x2 = self._extract_text(page)
                    except Exception:
                        continue
                    if not _is_challenge(t2, x2) and len(x2) >= 120:
                        pc.title, pc.text = t2, x2
                        pc.links = self._extract_links(page)
                        cleared = True
                        break
                if not cleared:
                    pc.blocked = True
                    pc.error = "bot challenge did not clear (Cloudflare/anti-bot wall)"
                    self._log(f"[open] BLOCKED {url} :: challenge never cleared")
                    return pc
                self._log(f"[open] challenge cleared on {dom0}")

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
                    // Forum threads wrap EVERY post in its own <article>; one article
                    // = the page's content, several = read main/body so replies survive.
                    const arts = document.querySelectorAll('article');
                    const pick = (arts.length === 1 ? arts[0] : null)
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
        time.sleep(0.15 + (self.slow_mo_ms / 1000.0))


# Manual smoke test:  python -m engines.research.browser "your query"
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "James Webb Space Telescope discoveries"
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
