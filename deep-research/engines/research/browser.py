"""
DRT browser harness — the visible, persistent Brave the Deep Research tool drives.

First principles (locked with the user):
  * ONE dedicated, persistent Brave profile (logins survive run-to-run).
  * The real, installed Brave (by its executable), HEADED — the user watches every move;
    DRT_HEADED=0 runs the same Brave headless. Never Google Chrome (owner rule).
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
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .login import detect_login_wall, host_of
from . import bmcp as _bmcp

# The OLD research profile (Google Chrome). Kept on disk, used only if DRT_BROWSER=chrome
# is set explicitly; the live profile is .drt_brave_profile (see browser_choice).
_PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".drt_chrome_profile")

# ── Environment-driven browser config ────────────────────────────────────────
# Local desktop default: a HEADED Brave (the user watches it work).
# Headless: DRT_HEADED=0 — same Brave, no window (owner rule 2026-11-12: every app that
# needs a browser must be able to run Brave headless).
# Hosted (Render, Linux container, no Brave installed): DRT_HEADED=0; the choice below
# then falls to Playwright's bundled, containerized Chromium with sandbox disabled.
def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")

_HEADED_DEFAULT = _env_flag("DRT_HEADED", True)
# Legacy setting, kept only so an old .env does not break: it is consulted ONLY when
# DRT_BROWSER=chrome is set explicitly (see browser_choice).
_BROWSER_CHANNEL = os.environ.get("DRT_BROWSER_CHANNEL", "").strip()

# ── which browser the research tool drives: BRAVE ────────────────────────────
# OWNER RULE (standing order 2026-09-26, restated 2026-11-12): every browser session on
# HYPERBOREA is Brave, never Google Chrome. See _KNOWLEDGE\BROWSER_STANDARD.md.
#   nothing set / DRT_BROWSER=brave → the installed Brave, found in its usual places.
#                                     Not installed (the hosted Linux container) →
#                                     Playwright's bundled Chromium. NEVER Google Chrome.
#   DRT_BROWSER_PATH=<exe>          → any Chromium-based browser, by its executable
#   DRT_BROWSER=chromium            → Playwright's bundled Chromium
#   DRT_BROWSER=chrome              → Google Chrome. Against the house rule; exists only so
#                                     a problem can be compared across browsers on request.
# Each browser keeps its OWN research profile folder beside the backend
# (.drt_brave_profile; the old .drt_chrome_profile is kept on disk and no longer used): a
# profile written by one browser is not opened with another. Read at start(), not at
# import, so the .env is already loaded.
_BRAVE_PATHS = (
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser",
                 "Application", "brave.exe"),
    "/usr/bin/brave-browser",
    "/usr/bin/brave",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def hosted_instance() -> bool:
    """True on the hosted deployment (its access gate sets AUTH_SECRET); never on the house PC."""
    return bool(os.environ.get("AUTH_SECRET", "").strip())


def find_brave() -> str:
    """Path of the installed Brave ('' when it is not installed). BRAVE_PATH overrides."""
    env = os.environ.get("BRAVE_PATH", "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    return next((c for c in _BRAVE_PATHS if c and os.path.isfile(c)), "")


def browser_choice() -> dict:
    """{name, label, executable_path, channel, profile_dir, note} for this run."""
    base = os.path.dirname(_PROFILE_DIR)
    want = os.environ.get("DRT_BROWSER", "").strip().lower() or "brave"
    path = os.environ.get("DRT_BROWSER_PATH", "").strip().strip('"')
    note = ""
    if path and not os.path.isfile(path):
        note = f"DRT_BROWSER_PATH does not exist ({path}) — using Brave"
        path, want = "", "brave"
    if not path and want not in ("chrome", "chromium", "bundled"):
        path = find_brave()
        if not path:
            note = ((note + "; ") if note else "") + (
                "Brave is not installed here — using Playwright's bundled Chromium "
                "(never Google Chrome)")
            want = "chromium"
    if path:
        name = "brave" if "brave" in path.lower() else (
            os.path.splitext(os.path.basename(path))[0].lower())
        name = re.sub(r"[^a-z0-9]+", "", name) or "browser"
        return {"name": name, "label": name.capitalize(), "executable_path": path, "channel": "",
                "profile_dir": os.path.join(base, f".drt_{name}_profile"), "note": note}
    if want == "chrome":
        chan = os.environ.get("DRT_BROWSER_CHANNEL", _BROWSER_CHANNEL).strip() or "chrome"
        return {"name": "chrome", "label": "Chrome", "executable_path": "", "channel": chan,
                "profile_dir": _PROFILE_DIR,
                "note": "DRT_BROWSER=chrome — Google Chrome by explicit request (house rule is Brave)"}
    return {"name": "chromium", "label": "Chromium", "executable_path": "", "channel": "",
            "profile_dir": os.path.join(base, ".drt_chromium_profile"), "note": note}

# Per-engine search URLs + the CSS that locates organic result anchors.
# We prefer server-rendered endpoints where possible so extraction is reliable
# even though the window is a full, visible Brave.
_ENGINES = {
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "result_sel": "a.result__a",
        "snippet_sel": "a.result__snippet",
        # Title + SNIPPET per result block (2026-11): the agent chooses what to open
        # from these lines, and a title alone is a weak basis. Empty rows (markup
        # drift) fall back to the plain anchor read below.
        "extract_js": """() => {
            const rows = [];
            for (const r of document.querySelectorAll('.result, .web-result')) {
                const a = r.querySelector('a.result__a');
                if (!a) continue;
                const s = r.querySelector('.result__snippet');
                rows.push({href: a.getAttribute('href') || '',
                           title: ((a.innerText || '').trim().split('\\n')[0] || '').slice(0, 200),
                           snippet: ((s && s.innerText) || '').trim().slice(0, 300)});
            }
            return {captcha: false, rows};
        }""",
    },
    # Brave (2026-09): results are Svelte `.snippet` blocks (no #results container any
    # more); each holds the real href on its anchors and a title element. Rate-limited
    # loads render a `.captcha-wrapper` page instead — detected and returned as empty so
    # the provider fallback chain takes over.
    "brave": {
        "url": "https://search.brave.com/search?q={q}",
        "result_sel": ".snippet, .captcha-wrapper",
        "snippet_sel": ".snippet-description",
        "extract_js": """() => {
            if (document.querySelector('.captcha-wrapper')) return {captcha: true, rows: []};
            const rows = [];
            for (const s of document.querySelectorAll('.snippet')) {
                if (s.closest('.video-cluster-grid, [class*=video-cluster]')) continue;
                const a = Array.from(s.querySelectorAll('a[href^="http"]'))
                               .find(x => !/search\\.brave\\.com|brave\\.com\\/search/.test(x.href));
                if (!a) continue;
                const t = s.querySelector('.title, h2, h3, [class*="title"]');
                const title = ((t && t.innerText) || a.innerText || '').trim().split('\\n')
                              .filter(x => x.trim().length > 3).pop() || a.href;
                const d = s.querySelector('.snippet-description, [class*="description"]');
                rows.push({href: a.href, title: title.slice(0, 200),
                           snippet: ((d && d.innerText) || '').trim().slice(0, 300)});
            }
            return {captcha: false, rows};
        }""",
    },
    # Google (2026-09): result anchors are `a:has(h3)` inside #rso, but every href is an
    # opaque redirect `/goto?url=<token>` (the old /url?q= is gone). The token resolves
    # with a plain 302 → real URL via the session's own request client, so hrefs are
    # resolved BEFORE the junk-host filter (which would otherwise drop google.com/goto).
    "google": {
        "url": "https://www.google.com/search?q={q}&hl=en",
        "result_sel": "#rso a:has(h3), a:has(h3)",
        "snippet_sel": "div[data-sncf] , div.VwiC3b",
        "resolve_goto": True,
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
    date: str = ""       # result date when the source exposes one (forum searches do)


# ── PDFs ─────────────────────────────────────────────────────────────────────
# Primary documents (manuals, filings, court records, papers) are usually PDFs, and
# The browser's PDF viewer exposes no DOM text — they used to come back as an empty "thin →
# screenshot" page that nothing ever read. They are now downloaded through the
# browser session's own request client (same cookies) and parsed locally.
_PDF_MAX_PAGES = 80
_PDF_MAX_CHARS = 400_000


def _looks_like_pdf(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        return urlparse(url).path.lower().endswith(".pdf")
    except Exception:
        return False


def _pdf_text(data: bytes) -> str:
    """Text of a PDF (first _PDF_MAX_PAGES pages), '' on any failure."""
    if not data or data[:5] != b"%PDF-":
        return ""
    import io
    out, n = [], 0
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total = len(pdf.pages)
            for i, pg in enumerate(pdf.pages[:_PDF_MAX_PAGES], 1):
                t = (pg.extract_text() or "").strip()
                if t:
                    out.append(f"[page {i}]\n{t}")
                    n += len(t)
                if n > _PDF_MAX_CHARS:
                    break
            if total > _PDF_MAX_PAGES:
                out.append(f"[… PDF has {total} pages; first {_PDF_MAX_PAGES} read …]")
    except Exception:
        return ""
    return "\n\n".join(out)


def _thread_key(url: str) -> str:
    """Collapse the many URLs of one forum thread (post permalinks, fragments, query
    strings) to a single key so search results list each thread once."""
    u = (url or "").split("#")[0].split("?")[0]
    u = re.sub(r"/(post-\d+|latest|unread)/?$", "/", u)
    return u.rstrip("/").lower()


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
# challenge to clear (a headed browser usually passes a managed challenge on its own
# in a few seconds), and if it doesn't, flag the page as blocked so the caller's
# fallback chain (Tavily → Exa) takes over.
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
    "user_session", "dotcom_user",  # GitHub
    "reddit_session",              # Reddit
    "auth_token",                  # X / Twitter
    "_t",                          # Discourse (the auth token; guests have only _forum_session)
    "flarum_remember",             # Flarum
    "sapu",                        # Seeking Alpha
    "li_at",                       # LinkedIn
    "bb_userid", "vbulletin_user",  # vBulletin
}
# Sites whose sign-in is known exactly: ONLY these cookies prove a session there — the
# generic patterns below are not consulted. Found 2026-11-12 by asking each site directly:
# GitHub sets `logged_in=no` for every visitor and Reddit sets `token_v2` for every
# visitor, and both were being read as "signed in" — so the run reported a GitHub and a
# Reddit session it did not have, never tried the stored login, and never asked the user.
_STRICT_AUTH_COOKIES = {
    "github.com": {"user_session", "dotcom_user"},
    "reddit.com": {"reddit_session"},
    "x.com": {"auth_token"},
    "twitter.com": {"auth_token"},
}
# A cookie that only SAYS whether you are logged in must say yes.
_FALSY_COOKIE_VALUES = {"", "no", "0", "false", "null", "none", "deleted", "guest"}
# After a login form is submitted, landing on one of these means a second step is still
# owed (2FA / device check) — the session does not exist yet.
_MFA_URL_HINTS = ("two-factor", "two_factor", "2fa", "mfa", "otp", "verify", "verification",
                  "challenge", "checkpoint", "device", "confirm")
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
    "community.home-assistant.io": "discourse",
    "forums.tomshardware.com": "xenforo",
    "reddit.com": "reddit",
    "github.com": "github",
    "x.com": "x",
    "twitter.com": "x",
    "news.ycombinator.com": "hn",
    "huggingface.co": "hf",
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
    """Owns one persistent, visible Brave context and the tabs inside it.

    Anti-bot stance (2026-09-03): no UA spoofing / fingerprint shims. The local headed
    Brave is a real browser with its own dedicated research profile; anything it still cannot read
    (Cloudflare challenges, 403s) is fetched by the caller through the page readers
    (Tavily extract, then Exa contents)."""

    def __init__(self, profile_dir: Optional[str] = None, headed: Optional[bool] = None,
                 slow_mo_ms: Optional[int] = None, log=None, login_handler=None):
        self.choice = browser_choice()          # which browser, and its own profile folder
        # HOSTED SITE: a throwaway profile for every run. There a login is typed for one search
        # and must not stay signed in for the next person who uses the site - a profile kept on
        # the server would keep the session cookies. (DRT_GUEST_PROFILE=1 forces it anywhere.)
        self._throwaway_profile = False
        if not profile_dir and (hosted_instance() or _env_flag("DRT_GUEST_PROFILE", False)):
            profile_dir = tempfile.mkdtemp(prefix="drt_guest_")
            self._throwaway_profile = True
        self.profile_dir = profile_dir or self.choice["profile_dir"]
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
        # Why a site's own search last came back empty: no_search_box | blocked | error | empty.
        self.search_fail: dict[str, str] = {}
        self._platform_cache: dict[str, str] = {}   # domain -> detected forum platform
        # Browser MCP bridge (trial): the owner's everyday Brave, for the sites in
        # DRT_BMCP_DOMAINS. Started on first use; see bmcp.py and the methods below.
        self._bmcp = None
        self._bmcp_lock = threading.Lock()
        self._bmcp_down_until = 0.0
        self._bmcp_said: set[str] = set()
        self.bmcp_used: dict[str, dict] = {}     # domain -> {searches, pages}
        self._pw = None
        self._ctx = None
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
        # The installed Brave by its executable; on the hosted container (no Brave) the
        # choice is Playwright's bundled Chromium. Google Chrome only on explicit request.
        if self.choice.get("note"):
            self._log(f"[browser] {self.choice['note']}")
        if self.choice["executable_path"]:
            launch_kwargs["executable_path"] = self.choice["executable_path"]
        elif self.choice["channel"]:
            launch_kwargs["channel"] = self.choice["channel"]
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile_dir, **launch_kwargs)
        # Tabs the agent opens are pages on this context.
        if not self._ctx.pages:
            self._ctx.new_page()
        mode = "headed" if self.headed else "headless"
        chan = self.choice["label"]
        self._log(f"[browser] {chan} up ({mode}) · profile={self.profile_dir}")
        if self._throwaway_profile:
            self._log("[browser] throwaway profile: nothing signed into during this run is kept "
                      "after it")
        if not self.headed:
            # Owner, 2026-11-12: some websites do not respond to a headless browser (on this
            # PC DuckDuckGo's search returned nothing headless and five results visible).
            # The visible window is the default and the right mode for research.
            self._log("[browser] running HEADLESS (DRT_HEADED=0): some sites and search engines "
                      "do not answer a headless browser — use the visible window for research")
        return self

    def close(self):
        try:
            if self._bmcp is not None:
                self._bmcp.close()
        except Exception:
            pass
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
        finally:
            if self._pw:
                self._pw.stop()
        self._ctx = self._pw = None
        if getattr(self, "_throwaway_profile", False):
            # the hosted site's per-run profile: cookies and sessions go with it
            shutil.rmtree(self.profile_dir, ignore_errors=True)

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
            seen = set()
            if cfg.get("extract_js"):
                # Engine-specific DOM extraction (Brave): [{href,title,snippet}] + captcha flag.
                data = page.evaluate(cfg["extract_js"]) or {}
                if data.get("captcha"):
                    self._log(f"[search] {engine}: CAPTCHA page (rate-limited) q={query!r} -> 0 results")
                    return out
                rows = [(r.get("href") or "", r.get("title") or "", r.get("snippet") or "")
                        for r in (data.get("rows") or [])]
            else:
                rows = []
            if not rows:      # no engine-specific extractor, or it found nothing
                for a in page.query_selector_all(cfg["result_sel"]):
                    rows.append(((a.get_attribute("href") or "").strip(),
                                 (a.inner_text() or "").strip().split("\n")[0], ""))
            for href, title, snippet in rows:
                href = _normalize_href(href)
                if cfg.get("resolve_goto") and "/goto?url=" in href:
                    href = self._resolve_goto(page, href)
                if not href or not href.startswith("http"):
                    continue
                if _JUNK_HOST.search(href):
                    continue
                key = href.split("#")[0]          # collapse in-page fragments
                if key in seen:
                    continue
                seen.add(key)
                out.append(SearchResult(title=title or href, url=key, snippet=snippet, engine=engine))
                if len(out) >= limit:
                    break
            self._log(f"[search] {engine} q={query!r} -> {len(out)} results")
        finally:
            page.close()
        return out

    def _resolve_goto(self, page, href: str) -> str:
        """Google's `/goto?url=<opaque token>` → the real URL. The endpoint answers a
        plain 302 whose Location is the destination; ask through the page's own request
        client (same cookies, works in remote-session mode too). '' on any failure."""
        try:
            full = href if href.startswith("http") else "https://www.google.com" + href
            r = page.context.request.get(full, max_redirects=0, timeout=8000)
            loc = (r.headers.get("location") or "").strip()
            if 300 <= r.status < 400 and loc.startswith("http"):
                return loc
        except Exception:
            pass
        return ""

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
            self._settle(page, max_ms=4000, min_chars=300, quiet_ms=600)
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
                    // a[href] + the RESOLVED url: forums write relative hrefs, which the
                    // old a[href^=http] selector never matched.
                    return Array.from(scope.querySelectorAll('a[href]'))
                        .slice(0, 800)
                        .map(a => ({text:(a.innerText||'').trim().slice(0,140), url:a.href}))
                        .filter(a => /^https?:/i.test(a.url));
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

    def _settle(self, page, max_ms: int = 6000, min_chars: int = 600, quiet_ms: int = 900):
        """Wait until the page's text has stopped growing, then return — instead of waiting
        for the NETWORK to go quiet.

        Every page read used to end with `wait_for_load_state("networkidle", 6000)`. Forum
        and news pages keep ads, analytics and live-update sockets talking forever, so that
        wait almost always ran its full six seconds — on every one of a run's page reads,
        with the single browser thread held the whole time. In the second real run that
        was most of the gathering window (43 reads), and it is why nine agents working in
        parallel read no more pages than four had.

        Now: poll the rendered text length; return once it is at least `min_chars` and has
        not changed for `quiet_ms`. A page that stays thin (still rendering, or genuinely
        empty) gets the full `max_ms`, exactly as before. Set DRT_SLOW_SETTLE=1 to go back
        to the old network-idle wait."""
        if os.environ.get("DRT_SLOW_SETTLE", "").strip() or not getattr(self, "fast_settle", True):
            try:
                page.wait_for_load_state("networkidle", timeout=max_ms)
            except Exception:
                pass
            return
        t_end = time.time() + max_ms / 1000.0
        prev, same_since = -1, None
        while time.time() < t_end:
            try:
                n = int(page.evaluate(
                    "() => document.body ? (document.body.innerText || '').length : 0") or 0)
            except Exception:
                n = 0
            now = time.time()
            if n != prev:
                prev, same_since = n, now
            elif n >= min_chars and now - same_since >= quiet_ms / 1000.0:
                return
            time.sleep(0.2)

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
    def native_search(self, domain: str, query: str, search_url: str = "",
                       limit: int = 10) -> list[SearchResult]:
        """The site's own search. A site routed through Browser MCP is searched in the
        owner's everyday Brave when that browser is signed in to it; everything else,
        and any failure, goes to the research browser as before."""
        rows = self._bmcp_search(domain, query, limit)
        if rows:
            return rows
        return self._native_search_pw(domain, query, search_url=search_url, limit=limit)

    @_on_browser_thread
    def _native_search_pw(self, domain: str, query: str, search_url: str = "",
                          limit: int = 10) -> list[SearchResult]:
        """Search a site's OWN search, choosing the most reliable method for its platform:
        Discourse → JSON API; Reddit → server-rendered old.reddit; XenForo → drive the
        search box; else an explicit {q} template (JS-aware) or the search box. Returns []
        on failure; callers fall back to the engine site: operator."""
        dom = (domain or "").lstrip(".").lower()
        self.search_fail.pop(dom, None)
        try:
            r = self._native_search_pw_inner(dom, query, search_url, limit)
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] native_search {dom} ERROR ({type(e).__name__}: {e})")
            self.search_fail[dom] = "error"
            return []
        if not r:
            self.search_fail.setdefault(dom, "empty")
        return r

    def _native_search_pw_inner(self, dom: str, query: str, search_url: str,
                                limit: int) -> list[SearchResult]:
        if True:
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
            elif plat == "x":
                # X has no usable logged-out or engine view: its own search is the only route.
                return self._x_search(dom, query, limit)
            elif plat == "hn":
                return self._hn_search(dom, query, limit)
            elif plat == "hf":
                return self._hf_search(dom, query, limit)
            # Explicit template (JS-aware) next, then the universal search-box driver.
            if search_url and "{q}" in search_url:
                r = self.site_native_search(search_url, query, limit)
                if r:
                    return r
            return self._search_box(dom, query, limit)

    def _platform_of(self, domain: str) -> str:
        """Best-effort forum-platform id from a small domain map plus generic hints."""
        d = domain.lstrip(".").lower()
        if d in _PLATFORM_HINTS:
            return _PLATFORM_HINTS[d]
        if "reddit.com" in d:
            return "reddit"
        if d == "github.com":
            return "github"
        if d not in self._platform_cache:
            self._platform_cache[d] = self._detect_platform(d)
        return self._platform_cache[d]

    def _detect_platform(self, domain: str) -> str:
        """Identify the forum software from the site's own markup (one page load, cached
        per run). The hard-coded map only ever covered a dozen sites, so every other
        forum — including each one the agent DISCOVERS — fell to the generic search-box
        driver, which finds nothing on Discourse or XenForo."""
        page = self.new_tab()
        try:
            page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=20000)
            sig = page.evaluate(
                """() => ({
                    gen: ((document.querySelector('meta[name=generator]') || {}).content || '').toLowerCase(),
                    disc: !!document.querySelector('#data-discourse-setup, meta[name=discourse_theme_id]'),
                    xf: !!document.querySelector('html[data-app], html#XF, html#XenForo, .p-pageWrapper')
                })""") or {}
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] platform detect {domain} failed ({type(e).__name__}) → generic")
            return "generic"
        finally:
            try:
                page.close()
            except Exception:
                pass
        plat = ("discourse" if ("discourse" in sig.get("gen", "") or sig.get("disc"))
                else "xenforo" if sig.get("xf") else "generic")
        self._log(f"[search] platform of {domain}: {plat}")
        return plat

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
                # The matching post's blurb + date ride along as the result snippet.
                blurb = {}
                for p in (data.get("posts") or []):
                    blurb.setdefault(p.get("topic_id"),
                                     ((p.get("blurb") or "").strip()[:300],
                                      str(p.get("created_at") or "")[:10]))
                ordered = [topics.get(p.get("topic_id")) for p in (data.get("posts") or [])]
                ordered += (data.get("topics") or [])
                for t in ordered:
                    if not t or t.get("id") in seen or len(out) >= limit:
                        continue
                    seen.add(t.get("id"))
                    sn, dt = blurb.get(t.get("id"), ("", ""))
                    out.append(SearchResult(title=(t.get("title") or "").strip(),
                               url=f"https://{domain}/t/{t.get('slug','t')}/{t.get('id')}",
                               snippet=sn, engine="discourse",
                               date=dt or str(t.get("created_at") or "")[:10]))
                self._log(f"[search] discourse(json) {domain} q={query!r} -> {len(out)} results")
                return out
            if isinstance(data, dict) and "grouped_search_result" in data \
                    and not data.get("failed") and not data.get("errors"):
                # A well-formed answer with nothing in it: the forum has no match for these
                # words. That is final — do not spend ten more seconds on the HTML search
                # page to learn the same thing (the caller retries with a shorter query).
                self._log(f"[search] discourse(json) {domain} q={query!r} -> 0 results (no match)")
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
        """GitHub search via the REST API — anonymous (no login needed for public search),
        clean JSON. The web /search page is a React SPA whose HTML scrape yields nav
        tabs/footer junk, so we avoid it entirely. Returns REPOSITORIES (by stars) and
        ISSUES / PULL REQUESTS / DISCUSSIONS-as-issues (best match): the repos say what
        exists, the issues are where the candid problem reports live. On a non-200 (anon
        search is ~10 req/min) that half is simply empty; both empty → caller falls back."""
        import requests
        from urllib.parse import quote_plus
        hdr = {"Accept": "application/vnd.github+json", "User-Agent": "deep-research-tool"}
        repos: list[SearchResult] = []
        issues: list[SearchResult] = []
        try:
            r = requests.get(
                f"https://api.github.com/search/repositories?q={quote_plus(query)}"
                f"&sort=stars&per_page={max(limit, 5)}", headers=hdr, timeout=12)
            if r.status_code == 200:
                for it in (r.json().get("items") or [])[:limit]:
                    name = it.get("full_name") or ""
                    desc = (it.get("description") or "").strip()
                    stars = it.get("stargazers_count")
                    label = name + (f" — {desc}" if desc else "")
                    if isinstance(stars, int):
                        label += f"  (★{stars:,})"
                    repos.append(SearchResult(title=label[:180], url=it.get("html_url") or "",
                                              engine="github", snippet=desc[:220],
                                              date=str(it.get("pushed_at") or "")[:10]))
            else:
                self._log(f"[search] github repo api HTTP {r.status_code} (rate-limited?)")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] github repo api ERROR ({type(e).__name__})")
        try:
            r = requests.get(
                f"https://api.github.com/search/issues?q={quote_plus(query)}&per_page={max(limit, 5)}",
                headers=hdr, timeout=12)
            if r.status_code == 200:
                for it in (r.json().get("items") or [])[:limit]:
                    url = it.get("html_url") or ""
                    m = re.search(r"github\.com/([^/]+/[^/]+)/", url)
                    kind = "PR" if it.get("pull_request") else "issue"
                    label = (f"[{kind} · {m.group(1) if m else ''} · {it.get('state', '')} · "
                             f"{it.get('comments', 0)} comments] {(it.get('title') or '').strip()}")
                    body = re.sub(r"\s+", " ", (it.get("body") or ""))[:220]
                    issues.append(SearchResult(title=label[:180], url=url, engine="github",
                                               snippet=body,
                                               date=str(it.get("updated_at") or "")[:10]))
            else:
                self._log(f"[search] github issue api HTTP {r.status_code} (rate-limited?)")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] github issue api ERROR ({type(e).__name__})")
        # Interleave so a result page shows both kinds: repos first, then issues.
        n_repo = min(len(repos), max(limit - min(len(issues), limit // 2), limit // 2))
        out = repos[:n_repo] + issues[:max(0, limit - n_repo)]
        self._log(f"[search] github q={query!r} -> {len(repos[:n_repo])} repos + "
                  f"{len(out) - len(repos[:n_repo])} issues")
        return out

    def _hn_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """Hacker News via its Algolia search API (public JSON, no login): stories ranked
        by relevance, each linked to its HN DISCUSSION page — the comments are the value,
        not the linked article. Stories with no comments are dropped."""
        import requests
        from urllib.parse import quote_plus
        out: list[SearchResult] = []
        try:
            r = requests.get(
                f"https://hn.algolia.com/api/v1/search?query={quote_plus(query)}"
                f"&tags=story&hitsPerPage={max(limit * 2, 10)}",
                headers={"User-Agent": "deep-research-tool"}, timeout=12)
            if r.status_code == 200:
                for h in (r.json().get("hits") or []):
                    oid = h.get("objectID")
                    n_c = h.get("num_comments") or 0
                    if not oid or not n_c:
                        continue
                    title = (h.get("title") or "").strip()
                    link = (h.get("url") or "").strip()
                    out.append(SearchResult(
                        title=f"{title}  ({h.get('points') or 0} points · {n_c} comments)"[:180],
                        url=f"https://news.ycombinator.com/item?id={oid}",
                        snippet=(f"links to {link}" if link else
                                 re.sub(r"<[^>]+>", " ", h.get("story_text") or ""))[:220],
                        engine="hn", date=str(h.get("created_at") or "")[:10]))
                    if len(out) >= limit:
                        break
            else:
                self._log(f"[search] hn api HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] hn api ERROR ({type(e).__name__})")
        self._log(f"[search] hn q={query!r} -> {len(out)} results")
        return out

    def _hf_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """Hugging Face via its public hub API (JSON, no login). The home page's search box is
        drawn by script after load, so the generic search-box driver found no box there and
        every search came back empty (run of 2026-11-13: 0 of 8). The API matches on the
        NAME of a model, space or dataset, so a long query is shortened until something
        matches: the full query, then its first two words, then its first word."""
        import requests
        from urllib.parse import quote_plus
        words = [w for w in re.split(r"\s+", (query or "").strip()) if w]
        tries = []
        for t in (" ".join(words), " ".join(words[:2]), " ".join(words[:1])):
            if t and t not in tries:
                tries.append(t)
        kinds = (("models", "", max(3, (limit * 3) // 5)), ("spaces", "spaces/", max(2, limit // 5)),
                 ("datasets", "datasets/", max(2, limit // 5)))
        out: list[SearchResult] = []
        used = ""
        try:
            for t in tries:
                for kind, prefix, n in kinds:
                    r = requests.get(
                        f"https://huggingface.co/api/{kind}?search={quote_plus(t)}&limit={n}"
                        + ("&sort=downloads&direction=-1" if kind != "spaces" else "&sort=likes&direction=-1"),
                        headers={"User-Agent": "deep-research-tool"}, timeout=12)
                    if r.status_code != 200:
                        self._log(f"[search] hf api {kind} HTTP {r.status_code}")
                        continue
                    for h in (r.json() or []):
                        hid = (h.get("id") or "").strip()
                        if not hid or h.get("private"):
                            continue
                        bits = [kind[:-1]]
                        if h.get("downloads") is not None:
                            bits.append(f"{h.get('downloads'):,} downloads")
                        bits.append(f"{h.get('likes') or 0} likes")
                        if h.get("pipeline_tag"):
                            bits.append(str(h.get("pipeline_tag")))
                        tags = [x for x in (h.get("tags") or []) if isinstance(x, str)][:8]
                        out.append(SearchResult(
                            title=f"{hid}  ({' · '.join(bits)})"[:180],
                            url=f"https://huggingface.co/{prefix}{hid}",
                            snippet=", ".join(tags)[:220], engine="hf",
                            date=str(h.get("lastModified") or h.get("createdAt") or "")[:10]))
                if out:
                    used = t
                    break
        except Exception as e:  # noqa: BLE001
            self.search_fail[domain] = "error"
            self._log(f"[search] hf api ERROR ({type(e).__name__})")
        note = "" if used in ("", tries[0] if tries else "") else f" (shortened to {used!r})"
        self._log(f"[search] hf q={query!r} -> {len(out)} results{note}")
        return out[:limit]

    def _x_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """X (Twitter) search, in the signed-in browser. X shows nothing to a logged-out
        visitor and search engines index almost none of it, so this is the only route:
        the Top tab of x.com/search, read from the rendered post cards. Each result is
        one POST — '@handle: text' with its permalink and date. Returns [] when the
        profile holds no X session (the page bounces to the sign-in flow)."""
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            page.goto(f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=top",
                      wait_until="domcontentloaded", timeout=30000)
            self._human_pause()
            if "/i/flow/login" in (page.url or "") or "/login" in (page.url or ""):
                self._log(f"[search] x q={query!r} -> not signed in (bounced to the sign-in page)")
                return []
            try:
                page.wait_for_selector('article[data-testid="tweet"]', timeout=12000)
            except PWTimeout:
                pass
            grab = """() => Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(a => {
                        const t = a.querySelector('[data-testid="tweetText"]');
                        const tm = a.querySelector('time');
                        const link = tm ? tm.closest('a') : a.querySelector('a[href*="/status/"]');
                        return {text: t ? (t.innerText || '') : '',
                                url: link ? link.href : '',
                                date: tm ? (tm.getAttribute('datetime') || '') : ''};
                    })"""
            rows = page.evaluate(grab) or []
            if len(rows) < limit:
                try:                       # one scroll loads the next batch of cards
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1500)
                    rows += page.evaluate(grab) or []
                except Exception:
                    pass
            seen = set()
            for r in rows:
                href = (r.get("url") or "").split("?")[0]
                text = re.sub(r"\s+", " ", (r.get("text") or "")).strip()
                m = re.search(r"(?:x|twitter)\.com/([^/]+)/status/\d+", href)
                if not m or href in seen or len(text) < 15:
                    continue
                seen.add(href)
                out.append(SearchResult(title=f"@{m.group(1)}: {text[:130]}", url=href,
                                        snippet=text[:300], engine="x",
                                        date=str(r.get("date") or "")[:10]))
                if len(out) >= limit:
                    break
            self._log(f"[search] x q={query!r} -> {len(out)} posts")
        except Exception as e:  # noqa: BLE001
            self._log(f"[search] x ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _reddit_search(self, domain: str, query: str, limit: int) -> list[SearchResult]:
        """Reddit's own search. old.reddit.com is server-rendered and clean, but since
        late 2026 it REQUIRES an account ("Log in to use old Reddit") — logged out it
        redirects to /login and yields nothing, which silently turned every Reddit
        forum_search into an engine site: query. So: old.reddit only when the profile
        holds a Reddit session; otherwise (or if it bounces) the www search page, which
        works logged out and renders post-title anchors."""
        from urllib.parse import quote_plus
        page = self.new_tab()
        out: list[SearchResult] = []
        try:
            anchors = []
            if self._has_auth_cookie("reddit.com"):
                url = f"https://old.reddit.com/search?q={quote_plus(query)}&sort=relevance"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._human_pause()
                if "/login" not in (page.url or ""):
                    anchors = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a.search-title, .search-result-link a.search-title'))
                                .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))"""
                    ) or []
            if not anchors:
                url = f"https://www.reddit.com/search/?q={quote_plus(query)}&sort=relevance"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._human_pause()
                try:
                    page.wait_for_selector('a[href*="/comments/"]', timeout=8000)
                except PWTimeout:
                    pass
                anchors = page.evaluate(
                    """() => {
                        // post-title anchors first, so each thread is listed under its
                        // TITLE rather than under a matching comment snippet
                        const pick = sel => Array.from(document.querySelectorAll(sel));
                        return pick('a[data-testid="post-title"], a[data-testid="post-title-text"]')
                            .concat(pick('a[href*="/comments/"]'))
                            .map(a => ({text:((a.innerText || a.getAttribute('aria-label') || '')).trim().slice(0,160),
                                        url:a.href}))
                            .filter(a => /\\/comments\\//.test(a.url));
                    }"""
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
            # Result rows first (title + the matching snippet + post date), then the
            # looser anchor selectors for XenForo 1.x / exotic skins.
            _extract = ("""() => {
                const rows = [];
                for (const r of document.querySelectorAll('.contentRow, li.searchResult')) {
                    const a = r.querySelector('.contentRow-title a, h3.title a');
                    if (!a || !/\\/(threads|posts)\\//.test(a.href)) continue;
                    const s = r.querySelector('.contentRow-snippet, blockquote.snippet');
                    const t = r.querySelector('time');
                    rows.push({text: (a.innerText || '').trim().slice(0, 160), url: a.href,
                               snippet: ((s && s.innerText) || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
                               date: ((t && t.getAttribute('datetime')) || '').slice(0, 10)});
                }
                if (rows.length) return rows;
                return Array.from(document.querySelectorAll(
                    '.p-body-content a[href*="/threads/"], .contentRow-title a[href*="/threads/"], '
                    + '.contentRow-title a[href*="/posts/"], .block-body a[href*="/threads/"], '
                    + '.discussionListItem h3.title a[href*="/threads/"], '   /* XenForo 1.x */
                    + '.titleText a[href*="/threads/"], #content a[href*="/threads/"]'))
                    .map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}));
            }""")
            anchors = page.evaluate(_extract) or []
            # Only if the GET produced nothing, submit via the VISIBLE keywords input
            # (XenForo renders TWO name=keywords fields; the first is hidden).
            if not anchors:
                try:
                    box = page.locator("input[name='keywords']:visible").first
                    box.fill(query, timeout=5000)
                    before = page.url
                    box.press("Enter")
                    # Wait for the NAVIGATION, not a load state: the form page is already
                    # 'domcontentloaded', so the old wait returned at once and the results
                    # were read off the still-showing search form (→ "native empty").
                    try:
                        page.wait_for_url(lambda u: u != before, timeout=15000)
                    except PWTimeout:
                        pass
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
                key = _thread_key(href)      # one row per thread, not per matching post
                if not href.startswith("http") or key in seen or len(text) < 10:
                    continue
                if any(s in href for s in _skip):
                    continue
                if "/threads/" not in href and "/posts/" not in href:
                    continue
                seen.add(key)
                out.append(SearchResult(title=text, url=href, engine="xenforo",
                                        snippet=(a.get("snippet") or ""),
                                        date=(a.get("date") or "")))
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
            if self._page_is_wall(page):
                self.search_fail[domain] = "blocked"
                self._log(f"[search] searchbox {domain}: the site showed an anti-bot wall")
                return out
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
                self.search_fail[domain] = "no_search_box"
                self._log(f"[search] searchbox {domain}: no search box found on the page")
                return out
            box.fill(query, timeout=4000)
            before = page.url
            box.press("Enter")
            try:        # wait for the navigation itself (see _xenforo_search)
                page.wait_for_url(lambda u: u != before, timeout=10000)
            except PWTimeout:
                pass
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            self._wait_for_results(page)
            base = f"https://{domain}"
            anchors = page.evaluate(
                """() => {
                    const scope = document.querySelector('main')||document.querySelector('#content')||document.body;
                    return Array.from(scope.querySelectorAll('a[href]'))
                        .slice(0,800).map(a => ({text:(a.innerText||'').trim().slice(0,160), url:a.href}))
                        .filter(a => /^https?:/i.test(a.url));
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
            if not out and self._page_is_wall(page):
                self.search_fail[domain] = "blocked"
            self._log(f"[search] searchbox {domain} q={query!r} -> {len(out)} results")
        except Exception as e:  # noqa: BLE001
            self.search_fail[domain] = "error"
            self._log(f"[search] searchbox {domain} ERROR ({type(e).__name__})")
        finally:
            try:
                page.close()
            except Exception:
                pass
        return out

    def _page_is_wall(self, page) -> bool:
        """True when the page on screen is a bot challenge (Cloudflare and the like)."""
        try:
            title = page.title() or ""
            text = page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 3000)") or ""
            return _is_challenge(title, text)
        except Exception:
            return False

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
        d = (domain or "").lstrip(".").lower()
        strict = next((v for k, v in _STRICT_AUTH_COOKIES.items()
                       if d == k or d.endswith("." + k)), None)
        for c in self._domain_cookies(domain):
            name = (c.get("name") or "")
            low = name.lower()
            val = (c.get("value") or "").strip()
            if val.lower() in _FALSY_COOKIE_VALUES:
                continue                    # empty, or a flag that says "no"
            if strict is not None:
                if name in strict:
                    return True
                continue
            if name in _AUTH_COOKIE_NAMES or any(p in low for p in _AUTH_COOKIE_PATTERNS):
                return True
        return False

    # ── manual sign-in (2FA / passkey / captcha sites) ─────────
    # The tool cannot complete these logins itself, but the persistent profile keeps a
    # session once the USER has signed in. These three calls let the run open the site's
    # sign-in page in the visible window, watch for the session cookie, and tidy up.
    _LOGIN_PAGES = {"github.com": "https://github.com/login",
                    "reddit.com": "https://www.reddit.com/login/",
                    "x.com": "https://x.com/i/flow/login"}

    @_on_browser_thread
    def open_login_tab(self, domain: str, login_url: str = ""):
        """Open (and front) a tab on the site's sign-in page for the user. Returns the
        page handle, or None when there is no visible local browser to sign in with."""
        if not self.headed or self._ctx is None:
            return None
        page = self._ctx.new_page()
        d = domain.lstrip(".").lower()
        target = (login_url or "").strip() or self._LOGIN_PAGES.get(d) or f"https://{d}/login"
        try:
            resp = page.goto(target, wait_until="domcontentloaded", timeout=30000)
            if resp is not None and resp.status >= 400:
                page.goto(f"https://{d}/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            try:
                page.goto(f"https://{d}/", wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    def is_signed_in(self, domain: str) -> bool:
        b = self._bmcp_for(domain)
        if b is not None and b.signed_in(domain) is True:
            return True
        return self._is_signed_in_pw(domain)

    @_on_browser_thread
    def _is_signed_in_pw(self, domain: str) -> bool:
        return self._has_auth_cookie(domain)

    def can_verify_sign_in(self, domain: str) -> bool:
        """True where the tool knows this site's session cookie by name, so 'no cookie'
        reliably means 'not signed in' (GitHub, Reddit, X, and Discourse / XenForo forums)."""
        d = (domain or "").lstrip(".").lower()
        if any(d == k or d.endswith("." + k) for k in _STRICT_AUTH_COOKIES):
            return True
        plat = self._platform_cache.get(d) or _PLATFORM_HINTS.get(d) or ""
        return plat in ("discourse", "xenforo")

    @_on_browser_thread
    def close_tab(self, page):
        try:
            page.close()
        except Exception:
            pass

    def mark_auth_handled(self, domain: str):
        self._auth_handled.add(domain)


    # ── Browser MCP bridge (trial, 2026-11-12) ────────────────
    # The research browser is its own Brave profile and has to be signed in site by site
    # (GitHub's 2FA, Reddit). The owner's everyday Brave already is. For the sites in
    # DRT_BMCP_DOMAINS, searching and reading go through that browser — in background
    # tabs, read-only — when DRT_BMCP=1, the Browser MCP extension is connected and
    # allowed, and that browser is signed in to the site. Any miss falls back to the
    # research browser, so turning this on can never make a site less reachable.
    def _bmcp_for(self, domain: str):
        """The bridge, if this domain is routed through it and it is usable now; else None."""
        if not _bmcp.enabled() or not _bmcp.routes(domain):
            return None
        with self._bmcp_lock:
            if time.time() < self._bmcp_down_until:
                return None
            try:
                if self._bmcp is None:
                    self._bmcp = _bmcp.BrowserMCP(log=self._log).start()
                ok, detail = self._bmcp.status()
            except _bmcp.BMCPError as e:
                ok, detail = False, str(e)
            if not ok:
                self._bmcp_down_until = time.time() + 90      # do not ask again on every call
                if detail not in self._bmcp_said:
                    self._bmcp_said.add(detail)
                    self._log(f"[login] Browser MCP is not available ({detail[:200]}) — "
                              f"using the research browser for {domain}")
                return None
            return self._bmcp

    @staticmethod
    def _bmcp_site(domain: str) -> str:
        d = (domain or "").lstrip(".").lower()
        for r in _bmcp.routed_domains():
            if d == r or d.endswith("." + r):
                return r
        return d

    def _bmcp_note(self, domain: str, key: str):
        d = self.bmcp_used.setdefault(self._bmcp_site(domain), {"searches": 0, "pages": 0})
        d[key] += 1

    def _bmcp_search(self, domain: str, query: str, limit: int) -> list:
        dom = (domain or "").lstrip(".").lower()
        b = self._bmcp_for(dom)
        if b is None or b.signed_in(dom) is not True:
            return []
        try:
            rows = b.search(dom, query, limit)
        except _bmcp.BMCPError as e:
            self._log(f"[search] Browser MCP search of {dom} failed ({str(e)[:160]}) — "
                      f"research browser instead")
            return []
        out = [SearchResult(title=r["title"][:180], url=r["url"], snippet=r.get("snippet", ""),
                            engine="everyday-brave", date=r.get("date", "")) for r in rows]
        if dom.endswith("github.com"):
            # Issues and discussions came from the signed-in search page; add repositories
            # from the search API so one result list shows both.
            seen = {r.url for r in out}
            keep = out[:max(3, limit // 2 + 1)]
            for r in self._github_search(dom, query, limit):
                if r.url not in seen and len(keep) < limit:
                    seen.add(r.url)
                    keep.append(r)
            out = keep
        if out:
            self._bmcp_note(dom, "searches")
        self._log(f"[search] {dom} via your everyday Brave q={query!r} -> {len(out)} results")
        return out

    def _bmcp_open(self, url: str, min_chars: int):
        if _looks_like_pdf(url):
            return None
        dom = host_of(url)
        b = self._bmcp_for(dom)
        if b is None or b.signed_in(dom) is not True:
            return None
        try:
            r = b.read_page(url)
        except _bmcp.BMCPError as e:
            self._log(f"[open] Browser MCP could not read {url[:90]} ({str(e)[:140]}) — "
                      f"research browser instead")
            return None
        text = (r.get("text") or "").strip()
        if len(text) < min_chars:
            return None
        self._bmcp_note(dom, "pages")
        self._log(f"[open] {url} -> text {len(text)}c (in your everyday Brave)")
        return PageContent(url=r.get("url") or url, title=(r.get("title") or "").strip(), text=text,
                           links=[x for x in (r.get("links") or []) if isinstance(x, dict)])

    # ── proactive login ───────────────────────────────────────
    def ensure_logged_in(self, domain: str, creds: dict) -> tuple[bool, str]:
        """Signed in? A site routed through Browser MCP counts as signed in when the
        owner's everyday Brave is — no stored login is typed and no 2FA prompt is
        raised. Otherwise the research browser's own session and stored login are used."""
        b = self._bmcp_for(domain)
        if b is not None and b.signed_in(domain) is True:
            self._auth_handled.add(domain)
            return True, "signed in in your everyday Brave (through Browser MCP) — no login needed"
        return self._ensure_logged_in_pw(domain, creds)

    @_on_browser_thread
    def _ensure_logged_in_pw(self, domain: str, creds: dict) -> tuple[bool, str]:
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
            # FIND THE LOGIN FORM. Only 3 of the 12 stored logins carry a login URL; for
            # the rest this used to open the site's home page, find no password field
            # there, and give up without ever trying the stored login. Now: the stored
            # URL, then the site's known sign-in page, then /login, then the home page —
            # the first that shows a password field wins.
            d = (domain or "").lstrip(".").lower()
            targets = []
            for t in ((creds.get("login_url") or "").strip(), self._LOGIN_PAGES.get(d),
                      f"https://{d}/login", f"https://{d}/"):
                if t and t not in targets:
                    targets.append(t)
            has_pw, opened_any, last_err = False, False, ""
            for target in targets:
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    self._human_pause()
                    opened_any = True
                except Exception as e:  # noqa: BLE001
                    last_err = type(e).__name__
                    continue
                # The page load may itself reveal a live session (redirect past the form).
                if self._has_auth_cookie(domain):
                    self._auth_handled.add(domain)
                    return True, "already logged in (session in profile)"
                try:
                    page.wait_for_selector("input[type=password]", timeout=4000)
                    has_pw = True
                except Exception:
                    try:
                        has_pw = page.locator("input[type=password]").count() > 0
                    except Exception:
                        has_pw = False
                if has_pw:
                    break
            if not opened_any:
                return False, f"could not open login page ({last_err or 'error'})"
            if not has_pw:
                # No form anywhere we looked and no auth cookie → NOT authenticated.
                return False, ("not logged in — no login form found (tried "
                               + ", ".join(t.split("//", 1)[-1] for t in targets)
                               + "); add the site's login URL to its stored login")
            # The form is already on screen: fill it here rather than navigating again.
            try_autofill(page, {k: v for k, v in creds.items() if k != "login_url"}, self._log)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            # 3) Verify: a strong auth cookie appeared …
            if self._has_auth_cookie(domain):
                self._auth_handled.add(domain)
                return True, "logged in via stored credentials"
            url_now = (page.url or "").lower()
            path_now = url_now.split("//", 1)[-1].partition("/")[2]     # path only, not the host
            if any(h in path_now for h in _MFA_URL_HINTS):
                return False, ("the stored login was accepted but the site wants a second "
                               "step (2FA / device check) — it needs you to finish signing in")
            # … or the login form is gone and we're off the login page — accepted ONLY for
            #    sites whose session cookie we do not know by name. Where we do know it
            #    (GitHub, Reddit, X, Discourse, XenForo…) the cookie is the only proof.
            still_pw = False
            try:
                still_pw = page.locator("input[type=password]").count() > 0
            except Exception:
                pass
            known = (any(d == k or d.endswith("." + k) for k in _STRICT_AUTH_COOKIES)
                     or self._platform_cache.get(d) in ("discourse", "xenforo")
                     or _PLATFORM_HINTS.get(d) in ("discourse", "xenforo"))
            if not known and not still_pw and "login" not in url_now and "signin" not in url_now:
                self._auth_handled.add(domain)
                return True, "logged in via stored credentials (form cleared)"
            return False, "stored login did not establish a session (2FA, captcha, or selector mismatch)"
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ── open / read ───────────────────────────────────────────
    def open(self, url: str, min_chars: int = 200, timeout_ms: int = 30000) -> PageContent:
        """Open a URL and return its readable text. A page on a site routed through
        Browser MCP is read in the owner's everyday Brave when that browser is signed
        in to the site; everything else, and any failure, uses the research browser."""
        pc = self._bmcp_open(url, min_chars)
        if pc is not None:
            return pc
        return self._open_pw(url, min_chars=min_chars, timeout_ms=timeout_ms)

    @_on_browser_thread
    def _open_pw(self, url: str, min_chars: int = 200, timeout_ms: int = 30000) -> PageContent:
        """Open a URL in a fresh tab and return cleaned readable text.

        A bot-challenge interstitial is waited out; if it never clears the page comes
        back with `blocked=True` + an error so the caller can fetch it another way.
        Falls back to a screenshot (vision) only if text extraction is too thin.
        """
        page = self.new_tab()
        pc = PageContent(url=url)
        try:
            # PDFs: no DOM text in the browser's PDF viewer — download + parse instead.
            if _looks_like_pdf(url):
                txt = self._read_pdf(page, url)
                if len(txt) >= min_chars:
                    pc.title = url.rsplit("/", 1)[-1][:120]
                    pc.text = txt
                    self._log(f"[open] {url} -> PDF, {len(txt)}c")
                    return pc
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                ctype = ((resp.headers or {}).get("content-type") or "").lower() if resp else ""
            except Exception:
                ctype = ""
            if "application/pdf" in ctype:
                txt = self._read_pdf(page, url)
                if len(txt) >= min_chars:
                    pc.title = url.rsplit("/", 1)[-1][:120]
                    pc.text = txt
                    self._log(f"[open] {url} -> PDF, {len(txt)}c")
                    return pc
            self._human_pause()
            self._settle(page)
            pc.title = (page.title() or "").strip()
            pc.text = self._extract_text(page)
            pc.links = self._extract_links(page)
            # Discourse virtualizes long topics (only the posts near the viewport are in
            # the DOM) — read the whole topic from its plain-text /raw endpoint instead.
            full = self._discourse_full_text(page)
            if len(full) > len(pc.text):
                self._log(f"[open] discourse topic read in full via /raw "
                          f"({len(pc.text)}c in DOM -> {len(full)}c)")
                pc.text = full

            # Bot challenge (Cloudflare "Just a moment…" & co.)? Give it time to clear,
            # then re-read. Still walled → blocked (caller falls back to Tavily / Exa).
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
    def _read_pdf(self, page, url: str) -> str:
        """Download a PDF through the session's request client and return its text."""
        try:
            r = page.context.request.get(url, timeout=45000)
            if not r.ok:
                return ""
            return _pdf_text(r.body())
        except Exception as e:  # noqa: BLE001
            self._log(f"[open] PDF fetch failed ({type(e).__name__}) · {url}")
            return ""

    def _discourse_full_text(self, page) -> str:
        """Whole Discourse topic as plain text ('user | date | #n' per post) from
        /raw/<topic_id> — '' when the page is not a Discourse topic or the fetch fails."""
        try:
            m = re.search(r"^(https?://[^/]+)/t/(?:[^/]+/)?(\d+)", page.url or "")
            if not m:
                return ""
            is_disc = page.evaluate(
                """() => /discourse/i.test((document.querySelector('meta[name=generator]') || {}).content || '')
                        || !!document.querySelector('#data-discourse-setup')""")
            if not is_disc:
                return ""
            base, tid = m.group(1), m.group(2)
            parts = []
            for pg in range(1, 6):          # 100 posts per page → up to 500 posts
                r = page.context.request.get(f"{base}/raw/{tid}?page={pg}", timeout=15000)
                t = (r.text() if r.ok else "").strip()
                if not t:
                    break
                parts.append(t)
                if t.count("\n-------------------------\n") < 95:
                    break                   # short page = last page
            if not parts:
                return ""
            title = (page.title() or "").strip()
            return (title + "\n\n" + "\n\n-------------------------\n\n".join(parts)).strip()
        except Exception:
            return ""

    def _extract_text(self, page) -> str:
        """Readable text of the page, read from the LIVE DOM.

        2026-11 rework. The old version cloned a node and read the clone's innerText —
        but a detached clone has no layout, so innerText degrades to textContent and
        returns HIDDEN content too (collapsed menus, a manual's whole table of contents:
        one Denon manual page came back as 14,154 chars of which 2,113 were visible).
        Now page chrome is hidden in place for the read and restored afterwards, so the
        text is what a reader actually sees. Forum threads (several top-level <article>
        posts) are emitted post by post with [post N] markers so per-post attribution
        survives; Reddit threads are rebuilt from their comment elements (author, score,
        date, depth) because the visible page shows only a fraction of the tree."""
        try:
            txt = page.evaluate(
                """() => {
                    // ── Reddit thread: structured post + comment tree ──
                    const sp = document.querySelector('shreddit-post');
                    if (sp) {
                        const g = (el, a) => (el.getAttribute(a) || '');
                        const body = (sp.querySelector('[slot="text-body"]') || {}).innerText || '';
                        const out = [`${g(sp, 'post-title')}\\n[post] u/${g(sp, 'author')} · `
                            + `${g(sp, 'created-timestamp').slice(0, 10)} · score ${g(sp, 'score')} · `
                            + `${g(sp, 'comment-count')} comments\\n${body.trim()}`];
                        for (const c of document.querySelectorAll('shreddit-comment')) {
                            const b = c.querySelector(':scope > [slot="comment"]') || c.querySelector('[slot="comment"]');
                            const t = ((b && b.innerText) || '').trim();
                            if (!t) continue;
                            const d = parseInt(g(c, 'depth') || '0', 10) || 0;
                            const when = g(c, 'created') || ((c.querySelector('time') || {}).dateTime || '');
                            out.push(`${'  '.repeat(Math.min(d, 6))}[comment${d ? ' · reply' : ''}] u/${g(c, 'author')} · `
                                + `${when.slice(0, 10)} · score ${g(c, 'score')}\\n${t}`);
                        }
                        const joined = out.join('\\n\\n');
                        if (joined.length > 200) return joined;
                    }
                    const drop = 'script,style,noscript,nav,header,footer,aside,form,svg,button';
                    const hidden = [];
                    const hide = (root) => root.querySelectorAll(drop).forEach(n => {
                        // keep a <form> that wraps the posts (XenForo 1.x) and a post's own header (date / post no.)
                        if (n.tagName === 'FORM' && n.querySelector('article, .message, .post, .postbody')) return;
                        if (n.tagName === 'HEADER' && n.closest('article')) return;
                        hidden.push([n, n.style.getPropertyValue('display'), n.style.getPropertyPriority('display')]);
                        n.style.setProperty('display', 'none', 'important');
                    });
                    const restore = () => hidden.forEach(([n, v, p]) => {
                        if (v) n.style.setProperty('display', v, p); else n.style.removeProperty('display');
                    });
                    try {
                        if (!document.body) return '';
                        const arts = Array.from(document.querySelectorAll('article'))
                            .filter(a => !(a.parentElement && a.parentElement.closest('article')));
                        const whole = document.querySelector('main') || document.querySelector('[role=main]') || document.body;
                        if (arts.length === 1) {
                            hide(arts[0]);
                            const one = arts[0].innerText || '';
                            if (one.length >= 200) return one;
                        }
                        hide(document.body);
                        const all = whole.innerText || '';
                        if (arts.length > 1) {
                            const posts = arts.map(a => (a.innerText || '').trim()).filter(Boolean);
                            const joined = posts.map((t, i) => `[post ${i + 1}]\\n${t}`).join('\\n\\n');
                            // posts must carry the bulk of the page — otherwise this is a
                            // listing/home page built from article cards, read it whole
                            if (joined.length >= 0.5 * all.length) return joined;
                        }
                        return all;
                    } finally { restore(); }
                }"""
            ) or ""
        except Exception:
            txt = ""
        if len(txt.strip()) < 200:
            # Fallback: the pre-2026-11 detached-clone read (also returns hidden text —
            # better than nothing on pages that render their content invisibly).
            try:
                txt2 = page.evaluate(
                    """() => {
                        const drop = ['script','style','noscript','nav','header','footer',
                                      'aside','form','svg','button'];
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
                txt2 = ""
            if len(txt2.strip()) > len(txt.strip()):
                txt = txt2
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
        return txt.strip()

    def _extract_links(self, page, limit: int = 110) -> list:
        """The page's followable links, as [{text, url, kind}] — the crawling surface.

        2026-11 rework. The old selector was a[href^=http], which matches the href
        ATTRIBUTE as written — and forums write relative hrefs. On an AVForums thread 416
        of 440 links (every thread, post and page link) were invisible, so the agent was
        never offered "next page". Now every anchor is read with its RESOLVED url and
        tagged: next (rel=next / next-page control) · page (pagination) · content (inside
        the post/article/main area) · nav (everything else on the same site)."""
        try:
            links = page.evaluate(
                """() => {
                    const out = [], seen = new Set(), at = new Map();
                    const host = location.host;
                    const here = location.href.split('#')[0];
                    const pagSel = '.pageNav, .pageNavWrapper, .pagination, .pager, .paging, '
                                 + '[class*="pagination" i], nav[aria-label*="pag" i], .page-numbers';
                    const contSel = 'article, main, [role=main], .message-body, .cooked, .post, '
                                  + '.postbody, .bbWrapper, #content, .content, shreddit-comment';
                    for (const a of Array.from(document.querySelectorAll('a[href]')).slice(0, 2500)) {
                        let u = a.href || '';
                        if (!/^https?:/i.test(u)) continue;
                        u = u.split('#')[0];
                        if (!u || u === here) continue;
                        const text = ((a.innerText || a.getAttribute('aria-label') || a.title || '')
                                      .trim().replace(/\\s+/g, ' ')).slice(0, 120);
                        const rel = (a.getAttribute('rel') || '').toLowerCase();
                        const cls = (a.className && a.className.baseVal !== undefined) ? '' : String(a.className || '');
                        if (seen.has(u)) {
                            // the "Next" control usually points at a URL already listed as
                            // "page 2" — promote that entry rather than dropping the signal
                            if (rel.includes('next') || /pageNav-jump--next|next-page|pagination-next/i.test(cls))
                                out[at.get(u)].kind = 'next';
                            continue;
                        }
                        let same = false;
                        try { same = new URL(u).host === host; } catch (e) {}
                        let kind = 'nav';
                        if (same && (rel.includes('next') || /pageNav-jump--next|next-page|pagination-next/i.test(cls)
                                     || /^(next|next page|older|older posts|load more)\\b|^[›»→]$/i.test(text)))
                            kind = 'next';
                        else if (same && a.closest(pagSel) && /^\\d{1,5}$/.test(text)) kind = 'page';
                        else if (a.closest(contSel) && !a.closest('nav, header, footer, aside')) kind = 'content';
                        else if (!same) kind = 'external';
                        if (!text && kind !== 'next') continue;
                        seen.add(u);
                        at.set(u, out.length);
                        out.push({text: text || 'Next page', url: u, kind});
                    }
                    return out;
                }"""
            ) or []
        except Exception:
            links = []
        # Order by usefulness, cap each class so navigation chrome can't crowd out content.
        caps = {"next": 2, "page": 8, "content": 70, "external": 20, "nav": 20}
        out = []
        for kind in ("next", "page", "content", "external", "nav"):
            n = 0
            for l in links:
                if l.get("kind") != kind:
                    continue
                out.append(l)
                n += 1
                if n >= caps[kind]:
                    break
        return out[:limit]

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
