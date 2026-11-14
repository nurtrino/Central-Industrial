"""
DRT search driver — deterministic 4-stage pipeline (the "brain").

The order of operations is fixed in Python (not left to the model) so the search
always proceeds cheap→expensive, open→gated:

  Stage 1  Broad baseline sweep — wide, shallow engine searches in the SAME visible
           the research browser (no page opens), semantically reranked → a terse baseline brief.
  Stage 2  Browser search engines (DuckDuckGo/Brave/Google) in the visible research browser —
           targets the gaps Stage 1 left, deep-reads, mines the open forums.
  Stage 3  Already-credentialed gated sources (vault has creds) — searched directly.
  Stage 4  New login-required sources worth chasing — collected during 2–3, then the
           APP prompts for credentials (batched), saves them, auto-logs-in, harvests.
           Anything needing more than user/pass (2FA/captcha) is skipped + noted.

Within each browser stage a single Claude tool-use loop drives the browser.
ALL web searching happens through the real browser (Brave) — no server-side /
sandboxed search agents (those respect robots.txt and get blocked; a real browser does
not). SIGNAL-OR-NOTHING throughout: a near-empty harvest is a valid result.
This module is ONLY search/gather — storage, evaluation, synthesis come later.
"""

from __future__ import annotations

import concurrent.futures as _cf
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict

from .browser import DRTBrowser, _JUNK_HOST
from .exa_search import exa_search, exa_find_similar, exa_contents, is_enabled as exa_enabled
from .tavily_search import tavily_search, tavily_extract, is_enabled as tavily_enabled
from .brave_search import brave_search as brave_api_search, is_enabled as brave_api_enabled
from .login import normalize_domain, host_of
from .models import get_model

# ── depth tiers — TIME-boxed: each tier maximizes the outcome within a wall-clock
# window rather than a unit budget. `seconds` = the whole run's target duration;
# `reserve` = time held back for extraction + synthesis + the go-deeper assessment;
# the remainder is the gathering window. The search/page pools remain as generous
# safety ceilings (runaway protection), but TIME is the governor.
#   exhaustive ("Oh, Very Deep"): 30 MINUTES since 2026-11-13 (owner), was one hour. The free-roam tier — parallel lanes iterate until
#   they exhaust themselves or the clock runs out; many deepening rounds; pools sized
#   so that the clock, not the pool, is what ends it.
DEPTH_BUDGETS = {
    "standard":   {"seconds": 300,  "reserve": 100, "searches": 120, "pages": 150, "max_turns": 120},
    "deep":       {"seconds": 600,  "reserve": 130, "searches": 240, "pages": 300, "max_turns": 240},
    "exhaustive": {"seconds": 1800, "reserve": 300, "searches": 900, "pages": 900, "max_turns": 400},
}
_LEGACY_DEPTHS = {"quick": "standard", "verydeep": "deep",
                  "odysseus": "deep",    # the old "Deep + Odysseus" chip — every tier chains Odysseus now
                  "hour": "exhaustive", "1h": "exhaustive", "1hour": "exhaustive",
                  "max": "exhaustive", "ohverydeep": "exhaustive",
                  "oh-very-deep": "exhaustive"}   # old / alias tier names → canonical tier


def normalize_depth(depth: str) -> str:
    d = _LEGACY_DEPTHS.get((depth or "").strip().lower(), (depth or "").strip().lower())
    return d if d in DEPTH_BUDGETS else "standard"

# ── pausable run clock (2026-11) ──────────────────────────────────────────────
# Every deadline in this module is measured on RUN_CLOCK, not the wall clock, so a
# PAUSE freezes the research window instead of eating it. One run at a time by design
# (a headed run already holds the single browser profile). wait_if_paused() is called
# at every step boundary — turn starts and each tool call — so a pause takes effect as
# soon as the in-flight model call or page load returns; nothing gathered is lost.
class _RunClock:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused_at = None
        self._offset = 0.0

    def now(self) -> float:
        with self._lock:
            ref = self._paused_at if self._paused_at is not None else time.time()
            return ref - self._offset

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def pause(self):
        with self._lock:
            if self._paused_at is None:
                self._paused_at = time.time()

    def resume(self):
        with self._lock:
            if self._paused_at is not None:
                self._offset += time.time() - self._paused_at
                self._paused_at = None

    def reset(self):
        with self._lock:
            self._paused_at = None
            self._offset = 0.0

    def credit(self, seconds):
        """Time the run spent waiting on the USER (a sign-in prompt) is not research time."""
        with self._lock:
            self._offset += max(0.0, float(seconds))

    def wait_if_paused(self, stop_event=None, log=None, where=""):
        if self._paused_at is None:
            return
        if log:
            log(f"[pause] {where or 'run'}: paused — everything gathered so far is kept")
        while self._paused_at is not None:
            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(0.4)
        if log:
            log(f"[pause] {where or 'run'}: resumed")


RUN_CLOCK = _RunClock()


def _now() -> float:
    return RUN_CLOCK.now()


_MODEL = get_model("search")   # browser agent tool-use loop (Sonnet); other roles below
_PAGE_TEXT_TO_AGENT = 8000   # chars of page text the agent sees (full text is stored)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Optional verbatim trace of the agent's internal dialogue (enable with DRT_TRACE=1).
# Records the plan + every tool call/result + the model's reasoning to
# traces/<ts>_<provider>.trace.txt. Module-global — intended for sequential, one-run-
# at-a-time diagnostics (Claude vs local A/B), not concurrent load.
_TRACE_FILE = None
def _trace(text):
    if _TRACE_FILE:
        try:
            with open(_TRACE_FILE, "a", encoding="utf-8") as _tf:
                _tf.write((text or "").rstrip("\n") + "\n")
        except Exception:
            pass
_CFG_DIR = os.path.join(_ROOT, "config")
_SOURCES_PATH = os.path.join(_CFG_DIR, "drt_sources.json")
# Central governing principles — editable, loaded FRESH each run (no restart).
_GOVERNANCE_PATH = os.path.join(_ROOT, "prompts", "deep_research.md")


def _load_governance() -> str:
    try:
        with open(_GOVERNANCE_PATH, encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


# ── env / api key (same guard as perf_server: override empty/missing) ──
def _load_env():
    env_path = os.path.join(_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and not os.environ.get(k, "").strip():
                os.environ[k] = v


def _normalize_source(s: dict) -> dict:
    """Normalize one source entry to a single internal shape, accepting BOTH schemas:
      • unified (current):  {"url": "avforums.com", "login_required": bool|null, "note"?: ""}
      • legacy:             {"name","domain","type","description","enabled","search_url"}
    login_required is the site property that drives the 🟢/🔴 dot and the search path:
    False = searchable logged out, True = needs a login, None = untested (probe fills it)."""
    url = (s.get("url") or s.get("domain") or "").strip()
    domain = normalize_domain(url) or url.lower().lstrip("https://").lstrip("http://")
    note = (s.get("note") or s.get("description") or "").strip()
    lr = s.get("login_required", None)
    if isinstance(lr, str):
        lr = lr.strip().lower() in ("1", "true", "yes")
    return {
        "url": url or domain,
        "domain": domain,
        "login_required": lr,                 # True | False | None
        "note": note,
        "description": note,                  # legacy alias for existing callers
        "name": (s.get("name") or domain),
        "type": (s.get("type") or "site"),
        "search_url": (s.get("search_url") or "").strip(),
        "enabled": True,                       # unified list is all-on; selection is by the plan
    }


def load_sources(path: str = _SOURCES_PATH) -> list[dict]:
    """The single site list. Every entry is a candidate; the research plan decides which are
    topically relevant per query. Tolerant of a BOM and of either schema."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        return []
    raw = data.get("sources", []) if isinstance(data, dict) else (data or [])
    out, seen = [], set()
    for s in raw:
        if not isinstance(s, dict):
            continue
        if s.get("enabled") is False:          # legacy explicit-off entries stay excluded
            continue
        n = _normalize_source(s)
        if not n["domain"] or n["domain"] in seen:
            continue
        seen.add(n["domain"])
        out.append(n)
    return out


# ── harvest ───────────────────────────────────────────────────
@dataclass
class HarvestItem:
    url: str
    title: str
    text: str                 # goal-relevant evidence excerpt (post goal-based extraction)
    source_type: str = "web"  # web | forum | substack | api | gated | …
    via: str = ""             # which stage / how it was found
    used_screenshot: bool = False
    retrieved_at: float = 0.0
    summary: str = ""         # one-paragraph goal-relevant summary (from extraction)


@dataclass
class HarvestResult:
    query: str
    depth: str
    items: list = field(default_factory=list)        # list[HarvestItem]
    stage1_sources: list = field(default_factory=list)   # [{title,url}] from Claude's search
    gated_candidates: dict = field(default_factory=dict)  # domain -> reason (login needed)
    skipped_gated: list = field(default_factory=list)     # domains we couldn't get into
    logged_in: list = field(default_factory=list)         # domains newly logged into this run
    login_warnings: list = field(default_factory=list)    # [{domain, detail}] stored-login attempts that failed
    curated_searched: list = field(default_factory=list)  # curated domains actually site_search'd this run
    discovered_forums: list = field(default_factory=list)  # [{domain, reason}] — forums the agent found, not in the curated list
    lanes: list = field(default_factory=list)          # [{name, mission}] parallel Stage-2 lanes this run
    lane_reports: list = field(default_factory=list)   # [{lane, reason}] each lane's / digger's finish reason
    fallback_fetches: list = field(default_factory=list)  # [{url, via, reason}] pages the browser couldn't read, fetched via Tavily/Exa
    planned_sites: list = field(default_factory=list)     # [{domain, login_required, credentialed}] sites the plan selected for THIS query
    source_alerts: list = field(default_factory=list)     # [{domain, kind, detail, credentialed, at}] — see _alert()
    native_stats: dict = field(default_factory=dict)      # domain -> {"ok": n, "empty": n} for the site's own search
    site_sweeps: list = field(default_factory=list)       # [{domain, signed_in, searches, results, pages, note}] the dedicated per-site sweeps
    duplicate_pages: list = field(default_factory=list)   # [{url, same_as}] pages whose text duplicated an already-harvested page
    exa_searches: int = 0     # neural Exa queries run this run
    exa_similar: int = 0      # Exa find_similar calls run this run
    exa_urls: list = field(default_factory=list)   # URLs Exa surfaced (for provenance/A-B)
    searches_used: int = 0
    pages_used: int = 0
    agent_notes: str = ""
    stopped_reason: str = ""
    # synthesis stage (Odysseus-style evolving report + category formatting)
    report: str = ""          # final synthesized, category-formatted report
    evolving_report: str = ""  # pre-final-polish evolving report
    category: str = ""        # detected report category
    plan: dict = field(default_factory=dict)   # upfront research plan (channel allocation)

    def to_dict(self):
        return asdict(self)


# ── tool schemas (per browser stage) ──────────────────────────
def _tool_defs(include_web_search: bool = True, include_site_search: bool = True,
               include_neural: bool = False, include_register_forum: bool = False):
    tools = []
    if include_web_search:
        engines = ["duckduckgo", "brave", "google"]
        desc = ("Run a query on a general search engine and get ranked organic "
                "results (title + url). Use COMPACT KEYWORD queries (2-6 terms — "
                "proper nouns, model names, insider jargon), never full-sentence "
                "questions: engines rank short keyword strings far better. Vary "
                "queries and engines for broad coverage. An engine that returns nothing "
                "is automatically retried through the API providers, so an empty result "
                "means the web has nothing for that phrasing — rephrase, don't repeat.")
        if tavily_enabled():
            engines.append("tavily")
            desc += (" engine=\"tavily\" queries the Tavily search API — a different index "
                     "from the browser engines; good for freshness and long-tail pages.")
        tools.append({
            "name": "web_search",
            "description": desc,
            "input_schema": {"type": "object", "properties": {
                "engine": {"type": "string", "enum": engines},
                "query": {"type": "string"}}, "required": ["engine", "query"]},
        })
    if include_neural:
        tools.append({
            "name": "exa_search",
            "description": ("NEURAL/semantic web search (Exa). Describe the KIND of page you want "
                            "in natural language — it finds conceptually relevant, often niche or "
                            "long-tail pages that keyword engines (web_search) miss. Use it to "
                            "complement web_search, not duplicate it: reach for it on hard-to-phrase, "
                            "specialist, or sentiment-style queries. Returns title + url; open the "
                            "good ones with open_page."),
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string"}}, "required": ["query"]},
        })
        tools.append({
            "name": "exa_find_similar",
            "description": ("Given the URL of a page you've found to be genuinely valuable, return "
                            "other pages that are conceptually SIMILAR to it (Exa neural). Use to "
                            "expand from a strong source — more like this specialist writeup, this "
                            "forum thread, this primary document. Returns title + url."),
            "input_schema": {"type": "object", "properties": {
                "url": {"type": "string", "description": "URL of a strong page already found"}},
                "required": ["url"]},
        })
    if include_site_search:
        tools.append({
            "name": "site_search",
            "description": ("Search WITHIN a specific domain (forum/Substack/site). For the sources "
                            "listed in scope, and any site this browser is signed in to, this runs "
                            "the SITE'S OWN search (same as forum_search); for any other domain it "
                            "is a search-engine site: query. Site searches match ALL the words — "
                            "keep queries to 2-3 distinctive keywords, no question phrasing. A "
                            "query that matches nothing is retried shortened automatically."),
            "input_schema": {"type": "object", "properties": {
                "domain": {"type": "string", "description": "e.g. reddit.com"},
                "query": {"type": "string"}}, "required": ["domain", "query"]},
        })
        tools.append({
            "name": "forum_search",
            "description": ("Search WITHIN a specific forum/site using the SITE'S OWN search "
                            "function (runs in the logged-in browser — reaches content that "
                            "Google/DuckDuckGo never indexed, including credential-walled areas). "
                            "Falls back to engine site: search when the site has no registered "
                            "native search."),
            "input_schema": {"type": "object", "properties": {
                "domain": {"type": "string", "description": "e.g. forum.example.com"},
                "query": {"type": "string"}}, "required": ["domain", "query"]},
        })
    if include_register_forum:
        tools.append({
            "name": "register_forum",
            "description": ("Register a discussion forum/community you discovered that is highly "
                            "relevant to this question but NOT in the curated source list. "
                            "Registering it lets you forum_search/site_search it immediately, and "
                            "surfaces it to the user to add permanently. Give one sentence of "
                            "evidence it is active and on-topic."),
            "input_schema": {"type": "object", "properties": {
                "domain": {"type": "string"},
                "reason": {"type": "string"}}, "required": ["domain", "reason"]},
        })
    tools.append({
        "name": "open_page",
        "description": ("Open a URL in a tab and read its cleaned text plus the links it "
                        "contains. Following promising links from a good page (citation trails, "
                        "thread replies, next-page/pagination, an author's other posts) is often "
                        "BETTER than going back to a search engine. Only opened pages enter the "
                        "harvest."),
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string"},
            "source_type": {"type": "string", "description": "web|forum|substack|news|primary|gated"},
            "offset": {"type": "integer",
                       "description": ("Character offset to start reading from. A long page shows "
                                       "its first chunk and says how many chars remain — call "
                                       "open_page again with the same url and offset=<chars already "
                                       "shown> to keep reading. Re-reading a page already opened "
                                       "costs no budget.")}},
            "required": ["url"]},
    })
    tools.append({
        "name": "request_extension",
        "description": ("Call when your stage allocation is spent but you are close to the "
                        "specific information the user asked for. State what you found and why "
                        "continuing has high expected payoff."),
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}},
                         "required": ["reason"]},
    })
    tools.append({
        "name": "finish",
        "description": ("Call when this stage has gathered the salient material OR there's little of "
                        "value left. State why. A near-empty result is valid — never open weak pages "
                        "just to use budget."),
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}},
                         "required": ["reason"]},
    })
    return tools


def _stage_system(stage: str, governance: str, listing: str, cap: dict, context_brief: str,
                  focus_note: str = "", pool: dict | None = None) -> str:
    """Goal-first stage prompt: state the mission and constraints, name the moves, and
    leave the order and mix to the model — no scripted choreography."""
    role = {
        "engines": ("You are STAGE 2 of the Deep Research Tool — the open-web hunt in a real, "
                    "visible browser. Build on the Stage-1 brief: go after the GAPS, and after "
                    "the candid forum/discussion/primary material a quick engine pass never "
                    "surfaces."),
        "credentialed": ("You are STAGE 3 of the Deep Research Tool — the credentialed sweep. "
                         "Work ONLY within the already-logged-in sources listed below and pull "
                         "the material in them that bears on the question."),
        "gated": ("You are STAGE 4 of the Deep Research Tool. Credentials for the source below "
                  "were just provided. Search it and open the relevant gated material."),
    }[stage]
    moves = {
        "engines": (
            "MOVES AVAILABLE (order and mix are your call):\n"
            "  - web_search: engine queries for breadth and entry points.\n"
            "  - forum_search: a site's OWN search, run in the logged-in browser — reaches\n"
            "    content engines never indexed, including credential-walled areas.\n"
            "  - LINK-FOLLOWING: every opened page lists its links; following the promising\n"
            "    ones (citation trails, thread replies, next pages, an author's other posts)\n"
            "    is often better than another engine query. That is what crawling means here.\n"
            "  - register_forum: when you find a relevant community NOT in the source list,\n"
            "    register it — then search it. Hunt for such communities EARLY (e.g. a query\n"
            "    like \"best forum for <the topic>\").\n"
            "  - request_extension: your allocation is soft — if you are hot on the trail\n"
            "    when it runs out, ask for more."),
        "credentialed": (
            "MOVES AVAILABLE:\n"
            "  - forum_search / site_search the listed sources (forum_search uses the site's\n"
            "    own engine inside the logged-in browser — it sees what external engines\n"
            "    cannot).\n"
            "  - LINK-FOLLOWING: every opened page lists its links; thread replies, pagination\n"
            "    and author histories often hold the answer a search page only hints at.\n"
            "  - request_extension: if you are hot on the trail when the allocation runs out."),
        "gated": (
            "MOVES AVAILABLE: forum_search / site_search the source, open the strong results, "
            "follow in-thread links. Small allocation — spend it on the question, not the tour."),
    }[stage]
    register_note = (
        "REGISTER WHAT YOU DISCOVER: if you find yourself searching or reading a community\n"
        "that is NOT in the curated source list (a dedicated forum, board, or discussion site\n"
        "clearly relevant to this question), call register_forum for it — that is how the\n"
        "user's curated list grows.\n") if stage == "engines" else ""
    ctx = (f"\nSTAGE-1 BRIEF (already established — target the gaps, don't re-prove it):\n"
           f"{context_brief}\n" if context_brief else "")
    focus = f"\n{focus_note}\n" if focus_note else ""
    pool_line = (f" from a shared global pool (currently {pool['searches']} searches / "
                 f"{pool['pages']} opens)" if pool else "")
    return f"""{role}

MISSION: answer the USER'S SPECIFIC QUESTION with primary evidence. This is deep actual
research, not a survey — directly responsive beats comprehensive. One thread where people
address the exact question outweighs ten overview pages.
{focus}
{moves}

NARRATE AS YOU GO: the user watches your activity live. Before each significant pivot —
and whenever a page yields a real finding — write ONE short sentence (what you found /
where you're heading next) before your next tool call. These lines are the highlights of
the live feed; silent tool-chaining leaves the user blind.

{register_note}
You operate a real, visible web browser; opened pages are read as text automatically.
Apply the governing principles below. SIGNAL OVER NOISE — open deliberately.

================ GOVERNING PRINCIPLES ================
{governance}
=====================================================
{ctx}
SOURCES IN SCOPE FOR THIS STAGE:
{listing}

BUDGET: this run is TIME-BOXED — maximize the outcome within the clock shown on every tool
result (⏱). You also have a soft stage allocation of {cap['searches']} searches /
{cap['pages']} page opens, drawn{pool_line}. When your time share or allocation runs out but
the trail is HOT, request_extension buys more; when the window truly closes, finish
immediately with what you have. Prioritize accordingly: with minutes, not hours, spend them
where the specific answer lives. Stop early (finish) when new pages stop adding signal — a
near-empty harvest is a valid result. Do NOT attempt to log in to anything — gated sources
you can't read are handled separately; just keep moving."""


# ── source alerts (2026-11) ───────────────────────────────────
# A site the research plan selected for THIS query must never fail quietly. Three kinds:
#   login_failed — a login-required / credentialed site could not be signed into
#   unreachable  — the site could not be searched or navigated (own search dead, bot wall)
#   skipped      — the plan named it but it was not searched (no stored login, no time, …)
# Each alert is logged with an "[alert]" prefix THE MOMENT it happens (the server lifts
# that into a sticky banner in the live UI) and is repeated at the top of the report.
_ALERT_LABEL = {"login_failed": "LOGIN FAILED", "unreachable": "COULD NOT SEARCH / NAVIGATE",
                "skipped": "SKIPPED"}


# WHY a source could not be used, in one plain phrase (owner, 2026-11-13: "report WHY it was not
# able to access a specified resource - in succinct clear language - i.e. login failed, or couldn't
# find search box, or website blocked agent"). Shown first in the feed, the banner and the report.
_SEARCH_WHY = {
    "no_search_box": "Could not find the site's search box",
    "blocked": "The website blocked the agent",
    "error": "The site's search page did not load",
    "empty": "The site's own search returned no results",
}


def _plain_reason(kind, detail):
    d = (detail or "").lower()
    if "second step" in d or "2fa /" in d or "device check" in d:
        return "Login needs a second step (a code or approval) that only you can do"
    if "no login form" in d:
        return "Login failed: could not find the sign-in form"
    if "could not open login page" in d:
        return "Login failed: the sign-in page did not load"
    if "did not go through" in d or "did not establish a session" in d:
        return "Login failed: the site did not accept the stored login"
    if "no stored credentials" in d or "no login is stored" in d or "no login to use" in d:
        return "Needs a login and none is stored"
    if "no login was supplied" in d:
        return "No login was given when asked"
    if "no way to ask" in d:
        return "Needs a login and the run could not ask for one"
    if "anti-bot" in d or "bot challenge" in d or "captcha" in d:
        return "The website blocked the agent"
    if "stopped first" in d:
        return "The run was stopped before reaching it"
    if "spent the window" in d or "pass ended without" in d:
        return "Ran out of time before reaching it"
    if "search returned nothing" in d:
        return _SEARCH_WHY["empty"]
    return {"login_failed": "Login failed", "unreachable": "Could not search or open the site",
            "skipped": "Not searched"}.get(kind, "Not searched")


def _planned(result, domain):
    """The plan entry for `domain` (suffix-matched), or None if the plan didn't select it."""
    d = normalize_domain(domain or "")
    for s in (result.planned_sites or []):
        sd = s.get("domain", "")
        if d and sd and (d == sd or d.endswith("." + sd) or sd.endswith("." + d)):
            return s
    return None


def _alert(result, log, domain, kind, detail, only_planned=True, why=""):
    """Raise one user-facing alert per (domain, kind). By default only for sites the plan
    selected; credentialed ones are flagged so the UI can rank them first."""
    ps = _planned(result, domain)
    if only_planned and ps is None:
        return
    dom = (ps or {}).get("domain") or normalize_domain(domain or "") or str(domain)
    cred = bool((ps or {}).get("credentialed"))
    with _SHARED_LOCK:
        if any(a["domain"] == dom and a["kind"] == kind for a in result.source_alerts):
            return
        # Say whether the run is SIGNED IN to the site: "could not search" on a site the
        # run is signed into is a search problem, not a login problem, and the reader
        # should be able to tell the two apart at a glance.
        signed = dom in (getattr(result, "logged_in", None) or [])
        reason = why or _plain_reason(kind, detail)
        result.source_alerts.append({"domain": dom, "kind": kind, "detail": detail,
                                     "reason": reason,
                                     "credentialed": cred, "signed_in": signed,
                                     "at": time.strftime("%H:%M:%S")})
    tag = " (signed in)" if signed else (" (credentialed)" if cred else "")
    (log or (lambda m: None))(
        f"[alert] {dom}{tag} — {_ALERT_LABEL.get(kind, kind.upper())}: {reason} — {detail}")


def _clear_alert(result, log, domain, kind, why=""):
    """Withdraw an alert that turned out not to hold — e.g. a site's own search missed
    once and then worked. The server lifts the "[alert-clear]" line into the live banner
    (the alert disappears) and the report no longer lists it."""
    dom = (_planned(result, domain) or {}).get("domain") or normalize_domain(domain or "")
    with _SHARED_LOCK:
        hit = [a for a in result.source_alerts if a["domain"] == dom and a["kind"] == kind]
        if not hit:
            return False
        result.source_alerts = [a for a in result.source_alerts if a not in hit]
    (log or (lambda m: None))(
        f"[alert-clear] {dom} — {_ALERT_LABEL.get(kind, kind.upper())} withdrawn"
        + (f": {why}" if why else ""))
    return True


_Q_STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "how",
           "what", "is", "are", "vs", "best", "using", "use", "from", "by", "at"}


def _relaxed_queries(q):
    """Shorter versions of a site-search query, most specific first. A forum's own search
    matches ALL the words, so a five-word query that an engine would handle returns
    nothing there; the first three, then two, distinctive words usually do."""
    words = [w for w in re.split(r"\s+", (q or "").strip().strip('"')) if w]
    core = [w for w in words if w.lower().strip('"') not in _Q_STOP] or words
    out = []
    for n in (3, 2):
        if len(core) >= n:
            cand = " ".join(core[:n])
            if cand and cand != (q or "").strip() and cand not in out:
                out.append(cand)
    return out


class _AnyEvent:
    """is_set() is true when ANY of the wrapped events is set (None entries ignored)."""

    def __init__(self, *events):
        self._events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self._events)


def _same_site(a, b):
    a, b = normalize_domain(a or ""), normalize_domain(b or "")
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def _split_selected_sites(selected_sites, has_creds):
    """Sort the plan's selected sites into the four lists the run works from.

      public_selected — dot is not 🔴: may be searched logged out by the Stage 2 lanes
      login_selected  — dot is 🔴: only worth searching signed in
      have_login      — a login is stored in the vault, WHATEVER the dot says. These are
                        the credentialed sources: signed into before Stage 2, the user is
                        asked to sign in by hand if that fails, and Stage 3 sweeps them.
      need_prompt     — 🔴 with no stored login: session check, then the one-time prompt

    2026-11-12: a 🟢 site with a stored login used to be signed into but got no sign-in
    prompt on failure and no dedicated sweep — a stored login now always counts."""
    public_selected = [s for s in selected_sites if s.get("login_required") is not True]
    login_selected = [s for s in selected_sites if s.get("login_required") is True]
    have_login = [s for s in selected_sites if has_creds(s["domain"])]
    need_prompt = [s for s in login_selected if not has_creds(s["domain"])]
    return public_selected, login_selected, have_login, need_prompt


def _manual_login_gate_multi(br, log, items, manual_login):
    """Plan-selected sites the run could not sign into by itself (2FA, passkey, captcha, no
    stored login). Do NOT move on quietly — but do not sit there either: open EVERY such
    site's sign-in page at once in the visible research browser and hold the run, research
    clock stopped, for ONE shared wait (the job layer caps it at a minute — owner rule
    2026-11-12; it used to be ten minutes PER SITE). The wait ends when all of them are
    signed in (detected by session cookie), the user confirms or skips, or the minute is up.

    A site still not signed in when the minute is up keeps its sign-in tab OPEN: the user
    can finish there and the rest of the run uses the session (see the re-check at the end
    of run_search). `items` = [(domain, why, creds)]. `manual_login(label, why, check)` is
    supplied by the job layer and blocks; it returns 'signed_in'|'continue'|'skip'|'timeout'.
    Returns {domain: (ok, detail)}."""
    items = [(d, w, (c or {})) for d, w, c in items if d]
    if not items:
        return {}
    t0 = time.monotonic()
    doms = [d for d, _w, _c in items]
    pages = {}
    for d, _w, c in items:
        try:
            pages[d] = br.open_login_tab(d, c.get("login_url", ""))
        except Exception:  # noqa: BLE001
            pages[d] = None
    label = ", ".join(doms)
    why_all = items[0][1] if len(items) == 1 else "; ".join(f"{d}: {w}" for d, w, _c in items)
    log(f"[login] SIGN-IN NEEDED — {label}: {why_all}. "
        + ("A sign-in tab is open" if len(items) == 1 else f"{len(items)} sign-in tabs are open")
        + " in the research browser; the run is waiting for you (research clock stopped).")

    def _signed(d):
        try:
            return bool(br.is_signed_in(d))
        except Exception:  # noqa: BLE001
            return False

    decision = "skip"
    try:
        decision = manual_login(label, why_all, lambda: all(_signed(d) for d in doms))
    except Exception as e:  # noqa: BLE001
        log(f"[login] sign-in prompt failed ({type(e).__name__})")
    out = {}
    try:
        for d, why, _c in items:
            signed = _signed(d)
            # "I've signed in — continue" is taken on trust ONLY where the tool cannot check
            # for itself. On a site whose session cookie it knows (GitHub, Reddit, X,
            # Discourse, XenForo…) no cookie means no session, whatever was clicked — say so
            # plainly rather than report a sign-in that did not happen.
            try:
                verifiable = bool(br.can_verify_sign_in(d))
            except Exception:  # noqa: BLE001
                verifiable = False
            ok = signed or (decision == "signed_in" and len(items) == 1) \
                or (decision == "continue" and not verifiable)
            keep_open = (not ok) and decision not in ("skip", "continue")
            if pages.get(d) is not None and not keep_open:
                try:
                    br.close_tab(pages[d])
                except Exception:  # noqa: BLE001
                    pass
            if ok:
                try:
                    br.mark_auth_handled(d)
                except Exception:  # noqa: BLE001
                    pass
                out[d] = (True, "signed in by hand in the research browser (session kept for future runs)"
                          if signed else
                          "you confirmed the sign-in (this site has no session cookie the tool can verify)")
            elif decision == "continue":
                out[d] = (False, f"{why}; you chose to continue, but {d} still shows no signed-in "
                                 f"session in the research browser — it is being searched logged out")
            elif decision == "skip":
                out[d] = (False, f"{why}; the manual sign-in was skipped")
            else:
                out[d] = (False, f"{why}; the manual sign-in was not completed within the waiting "
                                 f"time (one minute). The sign-in tab is still open — finish there "
                                 f"and the rest of this run, and every later run, uses the session")
    finally:
        RUN_CLOCK.credit(time.monotonic() - t0)
    return out


def _manual_login_gate(br, log, domain, why, manual_login, creds=None):
    """One site's sign-in prompt — see _manual_login_gate_multi. Returns (ok, detail)."""
    return _manual_login_gate_multi(br, log, [(domain, why, creds)], manual_login).get(
        domain, (False, f"{why}; the manual sign-in was skipped"))


# ── URL identity ──────────────────────────────────────────────
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref_src",
                    "share_id", "igshid", "context", "rdt")


def _canon_url(url: str) -> str:
    """Identity key for "have we read this page": scheme/host case, www., fragments,
    tracking parameters and a trailing slash don't make a different page; old./new./np.
    reddit are the same thread. (Exact-string matching let the same page be opened —
    and later cited — several times under cosmetically different URLs.)"""
    from urllib.parse import urlsplit, parse_qsl, urlencode
    u = (url or "").strip()
    try:
        p = urlsplit(u)
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host in ("old.reddit.com", "new.reddit.com", "np.reddit.com"):
            host = "reddit.com"
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not any(k.lower().startswith(t) for t in _TRACKING_PARAMS)]
        path = p.path.rstrip("/") or ""
        return f"{host}{path}" + (f"?{urlencode(q)}" if q else "")
    except Exception:
        return u.split("#")[0]


def _text_fingerprint(text: str) -> str:
    import hashlib
    norm = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest() if len(norm) >= 400 else ""


# ── login handlers (per stage) ────────────────────────────────
def _record_login_warning(result, domain, detail, log=None):
    """Record a user-facing warning that a stored login could not be completed."""
    if not any(w.get("domain") == domain for w in result.login_warnings):
        result.login_warnings.append({"domain": domain, "detail": detail})
    _alert(result, log, domain, "login_failed", detail, only_planned=False)


def _record_skip_handler(result, log):
    """A login/paywall wall → record the domain as a Stage-4 candidate, skip. (Offline/test path.)"""
    def h(domain, page):
        result.gated_candidates.setdefault(domain, "login/paywall hit during open-web search")
        log(f"[stage2] gated {domain} → recorded for Stage 4; skipping")
        return False
    return h


def _curated_login_handler(vault, result, log, label="stage2"):
    """Stage 2: a curated site behind a wall → USE stored creds if we have them (in all cases),
    warn if they exist but fail, and only defer to Stage 4 when no creds are stored at all."""
    from .login import try_autofill

    def h(domain, page):
        creds = vault.get(domain) if vault else None
        if creds:
            if try_autofill(page, creds, log):
                if domain not in result.logged_in:
                    result.logged_in.append(domain)
                return True
            _record_login_warning(result, domain,
                                  "stored login did not go through (wrong password, 2FA, or captcha)",
                                  log)
            log(f"[{label}] {domain}: stored login failed → warning")
            return False
        result.gated_candidates.setdefault(domain, "login/paywall hit during open-web search")
        log(f"[{label}] gated {domain} → recorded for Stage 4; skipping")
        _alert(result, log, domain, "skipped",
               "hit a login wall and no login is stored for it — content behind the wall "
               "was not read (you will be asked for a one-time login at the end of gathering)")
        return False
    return h


def _vault_handler(vault, result, log, label, ephemeral=None):
    """Stages 3/4: try creds — ONE-TIME ephemeral creds first (typed in for this run, never
    saved), then the saved vault; success → True; otherwise record + skip (no stall, no 2FA)."""
    from .login import try_autofill

    def h(domain, page):
        creds = (ephemeral or {}).get(domain) or (vault.get(domain) if vault else None)
        if creds and try_autofill(page, creds, log):
            if domain not in result.logged_in:
                result.logged_in.append(domain)
            return True
        if creds:   # had stored creds but they didn't get us in → warn the user
            _record_login_warning(result, domain,
                                  "stored login did not go through (wrong password, 2FA, or captcha)",
                                  log)
        else:
            _alert(result, log, domain, "skipped",
                   "hit a login wall and there is no login to use — content behind it was not read")
        result.gated_candidates.setdefault(domain, "needs login; stored credentials did not get in")
        if domain not in result.skipped_gated:
            result.skipped_gated.append(domain)
        log(f"[{label}] {domain}: vault login absent/failed → skip (no 2FA/manual)")
        return False
    return h


# Links that are never the trail: account/profile/share/report plumbing.
_LINK_NOISE = re.compile(
    r"(/login|/logout|/register|/signup|/sign-up|/account/|/members/|/member\.php|/user/|/u/[^/]+/?$|"
    r"/misc/|/privacy|/terms|/report|/react|/bookmark|/attachments/|"
    r"mailto:|javascript:|/whats-new|/latest-activity|[?&](do|action)=(report|reply|quote))", re.I)


def _page_links_section(links, page_url, seen_urls, limit: int = 24) -> str:
    """Format an opened page's followable links for the agent — the crawling surface.
    Filters already-seen URLs, junk hosts, and thin anchors; substantive anchors first."""
    nxt, pages, content, other, seen = [], [], [], [], set()
    base = _canon_url(page_url or "")
    for l in links or []:
        u = (l.get("url") or "").split("#")[0]
        t = re.sub(r"\s+", " ", (l.get("text") or "")).strip()
        kind = l.get("kind") or "content"
        key = _canon_url(u)
        if not u or key == base or key in seen or key in seen_urls:
            continue
        if _JUNK_HOST.search(u) or _LINK_NOISE.search(u):
            continue
        if kind == "next":
            seen.add(key); nxt.append(("NEXT PAGE →", u)); continue
        if kind == "page":
            seen.add(key); pages.append((f"page {t}", u)); continue
        if len(t) < 4:
            continue
        seen.add(key)
        (content if kind in ("content", "external") else other).append((t, u))
    # Pagination first — on a forum thread it is THE way deeper — then links from the
    # post/article body (substantive anchor text first), then a little site navigation.
    content.sort(key=lambda p: -min(len(p[0]), 60))
    other.sort(key=lambda p: -min(len(p[0]), 60))
    room = max(0, limit - len(nxt[:1]) - len(pages[:4]))
    picked = nxt[:1] + pages[:4] + content[:max(0, room - 3)]
    picked += other[:max(0, limit - len(picked))]
    if not picked:
        return ""
    lines = "\n".join(f"L{i}. {t[:110]} — {u}" for i, (t, u) in enumerate(picked[:limit], 1))
    return ("\n\nLINKS ON THIS PAGE:\n" + lines +
            "\nFollow any of these with open_page if they smell like the trail — NEXT PAGE "
            "of a thread, replies, citations, an author's other posts.")


_SHARED_LOCK = threading.RLock()   # guards harvest / pool / seen_urls across parallel lanes


def _lane_log(log, tag):
    """Wrap a log callback so every line carries the lane tag AFTER its [kind] prefix
    (the server derives the activity-feed kind from that leading prefix, so the tag
    must not displace it)."""
    def _l(m):
        m = str(m or "")
        mm = re.match(r"\s*(\[[a-z_\-]+\])\s*(.*)", m, re.S | re.I)
        if mm and mm.group(1).lower() in ("[alert]", "[alert-clear]"):
            log(m)        # alerts are about a SITE, not a lane: the banner shows them verbatim
            return
        log(f"{mm.group(1)} {tag} · {mm.group(2)}" if mm else f"[log] {tag} · {m}")
    return _l


def _sr(rows, engine):
    """[{title,url,snippet}] dicts (API providers) → SearchResult objects (harness shape)."""
    from .browser import SearchResult
    out = []
    for r in rows or []:
        u = (r.get("url") or "").strip()
        if u:
            out.append(SearchResult(title=(r.get("title") or u)[:200], url=u,
                                    snippet=(r.get("snippet") or "")[:400], engine=engine))
    return out


def _engine_search(br, engine, query, limit=10, log=None):
    """One engine query with the provider fallback chain. Returns (results, via_label).

    Browser engines first (the real browser, Brave). An EMPTY browser result falls through to
    Tavily → Exa, so a blocked / captcha'd / selector-drifted engine
    never reads as "nothing on the web" (both the 2026-09-03 Rivian trace and the
    agentic-tools report show two of three browser engines returning nothing).
    engine='tavily' / 'exa' go straight to that API."""
    log = log or (lambda m: None)
    engine = (engine or "duckduckgo").lower()
    if engine == "tavily":
        return _sr(tavily_search(query, num=limit, log=log), "tavily"), "tavily"
    if engine == "exa":
        return _sr(exa_search(query, num=limit, log=log), "exa"), "exa"
    # Brave: the official Search API first when keyed (same index, no selector drift,
    # no CAPTCHA page); the browser scrape below remains its fallback.
    if engine == "brave" and brave_api_enabled():
        rows = brave_api_search(query, num=limit, log=log)
        if rows:
            return _sr(rows, "brave"), "brave-api"
    res = []
    if br is not None:
        try:
            res = br.search(engine, query, limit=limit)
        except Exception as e:  # noqa: BLE001
            log(f"[search] {engine} error ({type(e).__name__}: {e}) → fallback chain")
            res = []
    if res:
        return res, engine
    if tavily_enabled():
        rows = tavily_search(query, num=limit, log=log)
        if rows:
            return _sr(rows, "tavily"), f"{engine}→tavily"
    if exa_enabled():
        rows = exa_search(query, num=limit, log=log)
        if rows:
            return _sr(rows, "exa"), f"{engine}→exa"
    return [], engine


def _fetch_fallback(url, log):
    """Page text when the browser cannot read a URL (bot challenge, 403, timeout):
    Tavily extract → Exa contents.
    Returns (page dict {url,title,text,links} | None, provider label)."""
    if tavily_enabled():
        t = tavily_extract(url, log=log)
        if t:
            return {"url": url, "title": url, "text": t, "links": []}, "tavily"
    if exa_enabled():
        t = exa_contents(url, log=log)
        if t:
            return {"url": url, "title": url, "text": t, "links": []}, "exa"
    return None, ""


# ── transcript compaction (long / parallel runs) ─────────────────────────────
# A lane that runs for 30+ minutes appends every opened page's 8K-char body to its
# message list forever; on the 1-hour tier that overflows the context window. Once the
# transcript is big, older page bodies are elided mechanically (no model call) — the
# full text is already stored in the harvest for extraction. Recent turns stay verbatim.
_COMPACT_AT_CHARS = 280_000      # ≈70K tokens
_COMPACT_KEEP_TURNS = 6
_ELIDED = ("\n[… page body elided from the transcript to save context — the full text is "
           "stored in the harvest; open_page the same url again to re-read it (free) …]")


def _transcript_chars(messages) -> int:
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
            continue
        for b in c or []:
            if isinstance(b, dict):
                n += len(str(b.get("content") or b.get("text") or ""))
            else:
                n += len(getattr(b, "text", "") or "")
                try:
                    n += len(json.dumps(getattr(b, "input", None) or {}))
                except Exception:
                    pass
    return n


def _compact_messages(messages, log) -> int:
    if _transcript_chars(messages) < _COMPACT_AT_CHARS:
        return 0
    idx = [i for i, m in enumerate(messages)
           if m.get("role") == "user" and isinstance(m.get("content"), list)]
    old = idx[:-_COMPACT_KEEP_TURNS] if len(idx) > _COMPACT_KEEP_TURNS else []
    n = 0
    for i in old:
        for b in messages[i]["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, str) and len(c) > 1500 \
                        and c.startswith(("Opened", "Re-read")) and _ELIDED not in c:
                    b["content"] = c.split("\n\n", 1)[0] + _ELIDED
                    n += 1
    if n:
        log(f"[turn] transcript compacted — {n} older page bodies elided "
            f"(~{_transcript_chars(messages) // 1000}K chars now)")
    return n


# ── one browser stage (shared loop) ───────────────────────────
def _run_browser_stage(client, br, result, seen_urls, *, stage_name, system, task,
                       tools, cap, progress, log, notes, skip_event=None, question="",
                       sources=None, pool=None, deadline=None, hard_deadline=None,
                       stop_event=None, lane=None, clear_skip=True, model=None,
                       site_only=None, site_stat=None):
    model = model or _MODEL          # navigators = the "search" role; the digger passes "dig"
    # `site_only` = this loop is the DEDICATED SWEEP of one site: searches on any other
    # domain are refused, and finish() is refused until the site has actually been
    # canvassed (see the finish handler). `site_stat` collects what the sweep did.
    if site_only and site_stat is None:
        site_stat = {"domain": site_only, "searches": 0, "results": 0, "pages": 0, "note": ""}
    floor_refusals = [0]
    # Soft stage allocation (grows via request_extension) vs. the hard global pool.
    # No pool passed → private pool equal to the allocation (fixed-cap behavior).
    # `deadline` = this stage's soft time share; `hard_deadline` = the run's gathering
    # window (extensions can push the stage deadline up to it, never past it).
    # `lane` = a parallel-lane label (several of these loops may run concurrently,
    # sharing result/seen_urls/pool — all mutations of those go through _SHARED_LOCK).
    alloc = {"searches": cap["searches"], "pages": cap["pages"]}
    used = {"searches": 0, "pages": 0}
    dl = {"t": deadline}                       # mutable so request_extension can extend
    hard_deadline = hard_deadline or deadline

    def _left():
        if dl["t"] is None:
            return None
        return max(0, int(dl["t"] - _now()))

    def _clock():
        s = _left()
        return "" if s is None else f" · ⏱ {s // 60}:{s % 60:02d} left"
    if pool is None:
        pool = {"searches": cap["searches"], "pages": cap["pages"]}
    srcs = sources or []

    def _budget():
        return (f"[stage {alloc['searches'] - used['searches']}s/"
                f"{alloc['pages'] - used['pages']}p left · "
                f"pool {pool['searches']}s/{pool['pages']}p{_clock()}]")

    def _consume(kind):
        """Spend one search/page against BOTH the stage allocation and the global pool.
        Returns None when spent OK, else the message the agent gets instead. TIME is
        checked first — it is the governor; the unit pools are safety ceilings."""
        if dl["t"] is not None and _now() > dl["t"]:
            if hard_deadline is not None and dl["t"] < hard_deadline - 5:
                return ("STAGE TIME SHARE SPENT. If you are on a genuinely HOT TRAIL toward "
                        "the user's specific question, call request_extension(reason) for "
                        "more time; otherwise finish() now.")
            return ("TIME WINDOW CLOSED — the research clock is spent. Call finish() NOW "
                    "with what you have.")
        with _SHARED_LOCK:
            if pool[kind] <= 0:
                return ("GLOBAL research budget exhausted — no extensions left. Wrap up and "
                        "finish() with what you have.")
            if used[kind] >= alloc[kind]:
                return ("Stage allocation spent. If you are on a genuinely HOT TRAIL toward the "
                        "user's specific question, call request_extension(reason) to keep "
                        "digging; otherwise finish().")
            used[kind] += 1
            pool[kind] -= 1
            if kind == "searches":
                result.searches_used += 1
            else:
                result.pages_used += 1
        return None

    def _harvest(item):
        with _SHARED_LOCK:
            result.items.append(item)
            if site_stat is not None:
                site_stat["pages"] += 1
            return len(result.items)

    def _dup_of(text, url):
        """If this page's text is identical to one already harvested (the same document
        under another URL — region/case variants, cross-posts, mirrors), don't harvest
        it twice: two copies of one page would later read as two corroborating sources.
        Returns the message for the agent, or '' when the page is new."""
        fp = _text_fingerprint(text)
        if not fp:
            return ""
        with _SHARED_LOCK:
            for it in result.items:
                if it.via != "stage1" and _text_fingerprint(it.text) == fp:
                    result.duplicate_pages.append({"url": url, "same_as": it.url})
                    log(f"[open] duplicate content — {url[:80]} is the same page as {it.url[:80]}")
                    return (f"This URL serves the SAME content as a page already harvested "
                            f"({it.url}). Not harvested twice — it is one source, not two.\n\n"
                            f"{_budget()}")
        return ""

    def dispatch(name, inp):
        if name == "finish":
            reason = inp.get("reason", "")
            if site_only:
                # COVERAGE FLOOR: a site the plan selected is not "done" until its own
                # search has been tried with at least two different queries and, where
                # those returned anything, at least one result has been read. Refused at
                # most twice, and never once the clock is nearly out.
                left = _left()
                if floor_refusals[0] < 3 and (left is None or left > 25):
                    need = ""
                    if site_stat["searches"] < 2:
                        need = (f"you have run {site_stat['searches']} search(es) on {site_only}. "
                                f"Run at least two DIFFERENT short queries (2–3 distinctive "
                                f"keywords each) with site_search before finishing")
                    elif site_stat["results"] > 0 and site_stat["pages"] == 0:
                        need = (f"your searches returned {site_stat['results']} result(s) on "
                                f"{site_only} and you have read none. Open the strongest one(s)")
                    elif (left is not None and left > 60 and site_stat["pages"] < 6
                          and site_stat["results"] >= 3 * max(1, site_stat["pages"]) + 6):
                        # 2026-11-12, second real run: the site agents ran 31 searches,
                        # were shown 264 results and read 15 pages; one finished with 3:50
                        # still on the clock. The clock decides when a sweep stops.
                        need = (f"you have {left // 60}:{left % 60:02d} left and have read "
                                f"{site_stat['pages']} of the {site_stat['results']} results "
                                f"{site_only} gave you. Open the next strongest 3-5 now, several "
                                f"in one turn — unless what is left is genuinely off-topic, in "
                                f"which case say so in your finish reason")
                    if need:
                        floor_refusals[0] += 1
                        return (f"Not yet — this sweep exists so that {site_only} is properly "
                                f"canvassed, and {need}. If the site truly has nothing on the "
                                f"question after that, finish and say so. {_budget()}", False)
                site_stat["note"] = (reason or "").strip()[:400]
                return "Stage complete.", True
            with _SHARED_LOCK:
                if lane:
                    result.lane_reports.append({"lane": lane, "reason": reason})
                elif not result.stopped_reason:
                    result.stopped_reason = reason
            return "Stage complete.", True
        if name == "request_extension":
            reason = (inp.get("reason") or "").strip()
            with _SHARED_LOCK:
                gs = min(4, max(0, pool["searches"]))     # grant capped by pool remainder
                gp = min(6, max(0, pool["pages"]))
                # Time grant: push the stage deadline up to +60s, never past the hard window.
                gt = 0
                if dl["t"] is not None and hard_deadline is not None:
                    new_t = min(hard_deadline, max(dl["t"], _now()) + 60)
                    gt = max(0, int(new_t - dl["t"]))
                    dl["t"] = new_t
                if gs <= 0 and gp <= 0 and gt <= 0:
                    log(f"[extension] denied (time window + pool exhausted): {reason[:200]}")
                    return "Denied: the research window is spent — finish with what you have.", False
                alloc["searches"] += gs; alloc["pages"] += gp
            log(f"[extension] granted (+{gt}s time, +{gs} searches/+{gp} opens): {reason[:200]}")
            return (f"Extension granted: +{gt}s on the clock, +{gs} searches / +{gp} page "
                    f"opens. {_budget()}", False)
        if name == "register_forum":
            dom = normalize_domain(inp.get("domain", ""))
            reason = (inp.get("reason") or "").strip()
            if not dom:
                return "Could not parse that domain — give a bare domain like forum.example.com.", False
            with _SHARED_LOCK:
                known = {normalize_domain(s.get("domain", "")) for s in srcs}
                if dom in known or any(f.get("domain") == dom for f in result.discovered_forums):
                    return f"{dom} is already known — forum_search/site_search it directly.", False
                result.discovered_forums.append({"domain": dom, "reason": reason})
            log(f"[forum] discovered: {dom} — {reason}")
            return (f"Registered {dom} — you can forum_search/site_search it now; it will "
                    f"be surfaced to the user to add permanently.", False)
        if name in ("web_search", "site_search", "forum_search"):
            if name != "web_search":
                if site_only and not (inp.get("domain") or "").strip():
                    inp = dict(inp, domain=site_only)
                if site_only and not _same_site(inp.get("domain", ""), site_only):
                    return (f"This sweep covers ONLY {site_only} — another agent has "
                            f"{inp.get('domain')}. Search {site_only}. (Opening a link that "
                            f"leads off-site is fine.)", False)
            denied = _consume("searches")
            if denied:
                return denied, False
            try:
                if name == "web_search":
                    res, via_eng = _engine_search(br, inp.get("engine"), inp["query"],
                                                  limit=10, log=log)
                    via = f"{via_eng}: {inp['query']}"
                else:
                    dom = normalize_domain(inp.get("domain", "")) or \
                          (inp.get("domain") or "").strip().lower()
                    ps = _planned(result, dom)
                    # 2026-11-12: a site the plan selected, or one the run is signed into, is
                    # ALWAYS searched through its own search — whichever tool the agent
                    # picked. The credentialed sweep used to call site_search (a public
                    # search-engine site: query) on GitHub and Reddit while signed in to
                    # both, so the sessions were never used for searching at all.
                    use_native = (name == "forum_search" or ps is not None
                                  or any(_same_site(dom, d) for d in (result.logged_in or [])))
                    if use_native:
                        # The site's OWN search — platform adapter (Discourse/XenForo/Reddit/
                        # GitHub/X/HN…) or a registered {q} template, run in the logged-in
                        # browser so it reaches unindexed/gated threads. A miss is retried
                        # with shorter versions of the query (site searches match ALL the
                        # words), then falls back to the site: engine, then to Tavily.
                        surl = next((str(s.get("search_url") or "").strip() for s in srcs
                                     if normalize_domain(s.get("domain", "")) == dom), "")
                        q0 = inp["query"]
                        with _SHARED_LOCK:
                            st = result.native_stats.setdefault(dom, {"ok": 0, "empty": 0})
                            ok_before, empty_before = st["ok"], st["empty"]
                        # Two full misses and never a hit: this site's own search does not
                        # work here — stop paying a page load for it on every query.
                        dead = ok_before == 0 and empty_before >= 2
                        res, used_q = [], q0
                        if not dead:
                            res = br.native_search(dom, q0, search_url=surl, limit=10)
                            if not res and (ok_before > 0 or empty_before == 0):
                                for q2 in _relaxed_queries(q0):
                                    res = br.native_search(dom, q2, search_url=surl, limit=10)
                                    if res:
                                        used_q = q2
                                        break
                        with _SHARED_LOCK:
                            st["ok" if res else "empty"] += 1
                            n_ok, n_empty = st["ok"], st["empty"]
                        if res:
                            via = f"native:{dom} {used_q}" + (
                                "" if used_q == q0 else
                                f" (the full query {q0!r} matched nothing on this site — "
                                f"shortened; keep site queries to 2–3 words)")
                            _clear_alert(result, log, dom, "unreachable",
                                         "the site's own search is working")
                        else:
                            res = br.site_search(q0, dom, limit=10)
                            via = f"site:{dom} {q0} (the site's own search found nothing — engine fallback)"
                            # The site's own search is the whole point here. A credentialed
                            # site is flagged on the first miss (after the shortened retries);
                            # a public one once it has missed twice without ever working.
                            if n_ok == 0 and ps and (ps.get("credentialed") or n_empty >= 2):
                                _alert(result, log, dom, "unreachable",
                                       f"the site's own search returned nothing ({n_empty}× so far, "
                                       f"shortened queries included; last query: {q0[:60]!r}) — "
                                       f"falling back to a search-engine site: query, which only "
                                       f"sees public, engine-indexed pages"
                                       + ("" if res else "; that returned nothing either"),
                                       why=_SEARCH_WHY.get(
                                           (getattr(br, "search_fail", None) or {}).get(dom), ""))
                        if site_stat is not None and _same_site(dom, site_only):
                            site_stat["searches"] += 1
                            site_stat["results"] += len(res)
                    else:
                        res = br.site_search(inp["query"], dom, limit=10)
                        via = f"site:{dom} {inp['query']}"
                    if not res and tavily_enabled():
                        res = _sr(tavily_search(inp["query"], num=10, include_domains=[dom],
                                                log=log), "tavily")
                        if res:
                            via = f"tavily site:{dom} {inp['query']} (engines empty)"
                    with _SHARED_LOCK:
                        if dom and dom not in result.curated_searched:
                            result.curated_searched.append(dom)
            except Exception as e:  # noqa: BLE001
                return f"Search error: {type(e).__name__}: {e}", False
            if name != "web_search":
                log(f"[forum] {via} -> {len(res)} results")
            progress(stage_name, None, f"searched — {via}")
            with _SHARED_LOCK:
                fresh = [r for r in res if _canon_url(r.url) not in seen_urls]
            for r in fresh[:3]:       # feed: top hits cascading by
                log(f"[hit] {(r.title or r.url)[:80]} → {r.url[:90]}")

            def _row(i, r):
                # Title + url, plus the snippet/date when the source gave one — a title
                # alone is a thin basis for deciding what is worth an open.
                d = getattr(r, "date", "") or ""
                sn = re.sub(r"\s+", " ", (getattr(r, "snippet", "") or "")).strip()
                extra = " · ".join(x for x in (d, sn[:220]) if x)
                return (f"{i + 1}. {r.title[:110]}\n   {r.url}"
                        + (f"\n   ↳ {extra}" if extra else ""))
            lines = [_row(i, r) for i, r in enumerate(fresh)]
            body = "\n".join(lines) if lines else "(no new results)"
            return f"Results for [{via}]:\n{body}\n\n{_budget()}", False
        if name in ("exa_search", "exa_find_similar"):
            denied = _consume("searches")
            if denied:
                return denied, False
            try:
                if name == "exa_search":
                    hits = exa_search(inp["query"], num=10, log=log)
                    via = f"exa: {inp['query']}"
                    with _SHARED_LOCK:
                        result.exa_searches += 1
                else:
                    hits = exa_find_similar(inp.get("url", ""), num=10, log=log)
                    via = f"exa~similar: {inp.get('url', '')[:60]}"
                    with _SHARED_LOCK:
                        result.exa_similar += 1
            except Exception as e:  # noqa: BLE001
                return f"Exa error: {type(e).__name__}: {e}", False
            log(f"[search] {via}")
            progress(stage_name, None, f"searched — {via}")
            lines = []
            with _SHARED_LOCK:
                for hit in hits:
                    u = hit.get("url", "")
                    if not u:
                        continue
                    if u not in result.exa_urls:
                        result.exa_urls.append(u)
                    if _canon_url(u) not in seen_urls:
                        lines.append(f"{len(lines)+1}. {hit.get('title', '')[:90]}\n   {u}")
            body = "\n".join(lines) if lines else "(no new results)"
            return f"Results for [{via}]:\n{body}\n\n{_budget()}", False
        if name == "open_page":
            url = inp["url"]
            key = _canon_url(url)
            try:
                offset = max(0, int(inp.get("offset") or 0))
            except (TypeError, ValueError):
                offset = 0

            def _window(txt):
                """The slice of a page the agent sees, plus how to get the rest."""
                shown = txt[offset:offset + _PAGE_TEXT_TO_AGENT]
                left = len(txt) - (offset + len(shown))
                head = f"[chars {offset}–{offset + len(shown)} of {len(txt)}]\n" if offset else ""
                tail = "" if left <= 0 else (
                    f"\n…[+{left} more chars stored — open_page(url, offset="
                    f"{offset + len(shown)}) reads on, free of charge]")
                return head + shown + tail

            with _SHARED_LOCK:
                already = key in seen_urls
                prior = next((it for it in result.items
                              if it.via != "stage1" and _canon_url(it.url) == key), None) \
                    if already else None
            if already:
                # RE-READ (free): the lead review hands the digger "start here" URLs that a
                # lane already opened, and a long page needs its later chunks read. The old
                # "Already opened this URL. Skip it." made both impossible.
                if prior is None:
                    return ("Already tried this URL this run and it could not be read. "
                            "Move on.", False)
                if offset >= len(prior.text):
                    return f"No more text — that page is {len(prior.text)} chars in total.", False
                return (f"Re-read (already harvested — no budget spent): {prior.title}\n"
                        f"URL: {prior.url}\n\n{_window(prior.text)}\n\n{_budget()}", False)
            denied = _consume("pages")
            if denied:
                return denied, False
            with _SHARED_LOCK:
                seen_urls.add(key)
            progress(stage_name, None, f"reading — {url[:70]}")
            pc = br.open(url)     # br logs the "[open] …" feed line itself
            if not pc.error and pc.used_screenshot:
                # Thin text (JS-only shell, viewer, soft 404): nothing reads the screenshot,
                # so try the fetch providers before settling for an empty page.
                fb, prov = _fetch_fallback(url, log)
                if fb and len(fb.get("text") or "") > max(400, len(pc.text) * 2):
                    pc.error = "page rendered almost no readable text"
                    _thin_fb = (fb, prov)
                else:
                    _thin_fb = None
            else:
                _thin_fb = None
            if pc.blocked:
                _alert(result, log, host_of(url), "unreachable",
                       "pages are behind an anti-bot wall that did not clear in the browser"
                       " — reading through an unblocking provider where possible")
            if pc.error:
                # Resilience: the browser couldn't read it (bot challenge / 403 / timeout).
                # Fetch it through the unblocking providers so the page isn't lost —
                # Tavily extract → Exa contents.
                fb, prov = _thin_fb if _thin_fb else _fetch_fallback(url, log)
                if fb:
                    dup = _dup_of(fb["text"], url)
                    if dup:
                        return dup, False
                    n = _harvest(HarvestItem(
                        url=url, title=fb.get("title") or inp.get("title") or url,
                        text=fb["text"], source_type=inp.get("source_type", "web"),
                        via=f"{stage_name}+{prov}", retrieved_at=_now()))
                    with _SHARED_LOCK:
                        result.fallback_fetches.append({"url": url, "via": prov,
                                                        "reason": pc.error[:80]})
                    with _SHARED_LOCK:
                        links_sec = _page_links_section(fb.get("links"), url, seen_urls)
                    return (f"Opened via {prov} (the browser could not read it: {pc.error}):\n"
                            f"URL: {url}\n\n{_window(fb['text'])}{links_sec}\n\n"
                            f"[harvested {n} total] {_budget()}", False)
                return f"Could not open ({pc.error}) and no unblocking provider could fetch it.", False
            if pc.used_screenshot:
                # Nothing readable and no provider could do better — do NOT harvest an
                # empty shell (it only adds a dead source for extraction to reject).
                return (f"Opened but found almost no readable text ({len(pc.text)} chars): "
                        f"{pc.title or url}\n{pc.text[:300]}\nNot harvested. Try another "
                        f"route to this content (a different URL for it, or search for a "
                        f"copy).\n\n{_budget()}", False)
            dup = _dup_of(pc.text, pc.url)
            if dup:
                return dup, False
            with _SHARED_LOCK:
                seen_urls.add(_canon_url(pc.url))     # the post-redirect URL counts as read too
            # Store the RAW page text — goal-based extraction + junk filtering happens
            # post-hoc in synthesize.py (Pass A), which needs the full page.
            n = _harvest(HarvestItem(
                url=pc.url, title=pc.title, text=pc.text,
                source_type=inp.get("source_type", "web"), via=stage_name,
                used_screenshot=pc.used_screenshot, retrieved_at=_now()))
            with _SHARED_LOCK:
                links_sec = _page_links_section(pc.links, pc.url, seen_urls)
            return (f"Opened: {pc.title}\nURL: {pc.url}\n\n{_window(pc.text)}{links_sec}\n\n"
                    f"[harvested {n} total] {_budget()}", False)
        return f"Unknown tool {name!r}.", False

    def _compact_input(name, inp):
        """One-line description of a tool call for the activity feed."""
        inp = inp or {}
        if name == "open_page":
            return (inp.get("url") or "")[:110]
        if name in ("web_search", "site_search", "forum_search", "exa_search"):
            dom = inp.get("domain") or inp.get("engine") or ""
            return f"{dom + ': ' if dom else ''}{(inp.get('query') or '')[:90]}"
        if name in ("register_forum", "request_extension", "finish"):
            return (inp.get("domain") or inp.get("reason") or "")[:100]
        return json.dumps(inp, ensure_ascii=False)[:100]

    _trace(f"\n{'-' * 64}\n### STAGE {stage_name} — task: {task[:160].strip()}")
    messages = [{"role": "user", "content": task}]
    for _turn in range(cap.get("max_turns", 40)):
        # User pressed STOP → halt the whole run here (do NOT clear — the signal is
        # pipeline-level, so every later stage is skipped too). Harvest is preserved.
        RUN_CLOCK.wait_if_paused(stop_event, log, stage_name)     # user pressed PAUSE
        if stop_event is not None and stop_event.is_set():
            notes.append(f"[{stage_name}] stopped by user.")
            log(f"[stop] {stage_name}: halted by user — preserving harvest")
            break
        # User asked to end this stage early → stop, keep what's harvested, roll forward.
        # (Parallel lanes don't clear the flag — the orchestrator clears it once ALL
        # lanes have seen it.)
        if skip_event is not None and skip_event.is_set():
            if clear_skip:
                skip_event.clear()
            notes.append(f"[{stage_name}] ended early by user.")
            log(f"[{stage_name}] skipped by user")
            break
        # Time governor: when the (possibly extended) stage window is gone, move on.
        if dl["t"] is not None and _now() > max(dl["t"], hard_deadline or dl["t"]):
            notes.append(f"[{stage_name}] time window closed.")
            log(f"[{stage_name}] time window closed — moving on")
            break
        _compact_messages(messages, log)
        log(f"[turn] {stage_name}: agent weighing next move…{_clock()}")
        resp = client.messages.create(model=model, max_tokens=4096, system=system,
                                      tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        for b in resp.content:
            if getattr(b, "type", "") == "text" and b.text.strip():
                t = b.text.strip()
                notes.append(t)
                # Activity feed: the model's inter-tool commentary is the findings flying by.
                log("[note] " + t[:400].replace("\n", " "))
                _trace(f"\n[{stage_name} · turn {_turn + 1}] REASONING:\n{t}")
        if resp.stop_reason != "tool_use":
            _trace(f"[{stage_name} · turn {_turn + 1}] (no tool call — stage ends)")
            break
        trs, fin = [], False
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use":
                RUN_CLOCK.wait_if_paused(stop_event, log, stage_name)
                log(f"[act] → {b.name}: {_compact_input(b.name, b.input)}")
                _trace(f"[{stage_name} · turn {_turn + 1}] TOOL CALL → {b.name}"
                       f"({json.dumps(b.input or {}, ensure_ascii=False)})")
                out, is_fin = dispatch(b.name, b.input or {})
                _snip = (out or "").replace("\n", " ")
                _trace(f"    ↳ result: {_snip[:300]}{'…' if len(_snip) > 300 else ''}")
                trs.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                fin = fin or is_fin
        messages.append({"role": "user", "content": trs})
        if fin:
            _trace(f"[{stage_name}] agent signaled finish")
            break

def _credentialed_sources(sources, vault) -> list[dict]:
    """Credentialed sources to search in Stage 3: a domain must (a) have a stored login in
    the vault AND (b) be ENABLED in the Sources table. The checkbox governs what gets swept —
    a credentialed domain whose checkbox is OFF (or that isn't in the table at all) is NOT
    searched, even though a login exists for it. (`sources` here is already enabled-only,
    since load_sources() filters out disabled entries.)"""
    if not vault:
        return []
    by_dom = {s.get("domain", "").lower(): s for s in sources}
    out = []
    for dom in vault.domains():
        s = by_dom.get(dom)
        if not s:
            continue   # disabled (unticked) or not in the table → don't search it
        out.append({"name": s.get("name", dom), "domain": dom, "type": s.get("type", "gated")})
    return out


def _select_relevant_credentialed(client, query, clarifications, credentialed, log):
    """Pick only the credentialed sources topically worth searching for THIS question —
    so Stage 3 doesn't hit every logged-in site on every run. One cheap call; on any
    failure, fall back to all (current behavior)."""
    if len(credentialed) <= 1:
        return credentialed
    listing = "\n".join(
        f"- {s['domain']} ({s.get('type','site')})" + (f" — {s.get('name','')}" if s.get('name') else "")
        for s in credentialed)
    sys_p = ("Given a research question and the user's logged-in sources, return ONLY the domains "
             "that are topically appropriate to search for THIS question. Omit sources unlikely to "
             "hold relevant material. Respond with ONLY JSON: {\"domains\": [\"...\"]}. "
             "Empty list if none apply.")
    msg = f"QUESTION:\n{query}"
    if clarifications:
        msg += f"\n\nCONTEXT:\n{clarifications[:800]}"
    msg += f"\n\nLOGGED-IN SOURCES:\n{listing}"
    try:
        r = client.messages.create(model=get_model("route"), max_tokens=300, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        picked = set(json.loads(m.group(0)).get("domains", [])) if m else set()
        sel = [s for s in credentialed if s["domain"] in picked]
        log(f"[stage3] relevant credentialed sources: {[s['domain'] for s in sel]} "
            f"(of {len(credentialed)})")
        return sel
    except Exception as e:  # noqa: BLE001
        log(f"[stage3] relevance filter failed ({type(e).__name__}); using all credentialed")
        return credentialed


# ── research planner ──────────────────────────────────────────
# One cheap upfront call that decides how to split effort across the three channels —
# Claude's own API web search, open search engines, and queries against the curated source
# list — plus the report category. It BIASES budgets + stage prompts; it is never a fence
# (the relevance filter still picks the specific sites; the browser agent still self-paces).
_PLAN_CATEGORIES_FALLBACK = ["organization", "person", "regulatory", "market", "factcheck", "general"]
_PLAN_CHANNELS = ("api_search", "web_engines", "site_queries")


def _default_plan(reason: str = "") -> dict:
    """Balanced fallback plan — current fixed behavior (all three channels, even split)."""
    return {
        "category": "general",
        "rationale": reason or "balanced default plan",
        "api_search":   {"use": True, "weight": 0.34},
        "web_engines":  {"use": True, "weight": 0.33},
        "site_queries": {"use": True, "weight": 0.33, "emphasis": []},
        # neural_search (Exa) is an ADDITIVE on/off channel — it injects the neural
        # search tools into Stage 2 without taking weight/budget from the three above.
        # Effective use is always ANDed with exa_enabled() at run time.
        "neural_search": {"use": True},
        "_planned": False,
    }


def _normalize_plan(plan: dict, cats: list) -> dict:
    """Validate/repair a raw planner JSON into a safe, normalized plan dict."""
    out = _default_plan()
    if isinstance(plan.get("category"), str):
        c = plan["category"].strip().lower()
        out["category"] = c if c in cats else "general"
    if isinstance(plan.get("rationale"), str) and plan["rationale"].strip():
        out["rationale"] = plan["rationale"].strip()[:240]
    chans = {}
    for k in _PLAN_CHANNELS:
        src = plan.get(k) if isinstance(plan.get(k), dict) else {}
        use = bool(src.get("use", True))
        try:
            w = float(src.get("weight", out[k]["weight"]))
        except (TypeError, ValueError):
            w = out[k]["weight"]
        chans[k] = {"use": use, "weight": max(0.0, w)}
    # site_queries emphasis (soft preference on source types)
    sq = plan.get("site_queries") if isinstance(plan.get("site_queries"), dict) else {}
    emph = sq.get("emphasis") if isinstance(sq.get("emphasis"), list) else []
    chans["site_queries"]["emphasis"] = [
        str(x).strip().lower() for x in emph
        if str(x).strip().lower() in ("forum", "blog", "substack")]
    # zero-out unused channels; if the planner disabled everything, revert to balanced default
    for k in _PLAN_CHANNELS:
        if not chans[k]["use"]:
            chans[k]["weight"] = 0.0
    if not any(chans[k]["use"] for k in _PLAN_CHANNELS):
        return _default_plan("planner disabled all channels; reverted to balanced")
    total = sum(chans[k]["weight"] for k in _PLAN_CHANNELS) or 1.0
    for k in _PLAN_CHANNELS:
        chans[k]["weight"] = round(chans[k]["weight"] / total, 3)
    out.update(chans)
    # neural_search is a sibling on/off flag (no weight) — preserved separately so it
    # never participates in the 3-way weight normalization above.
    ns = plan.get("neural_search") if isinstance(plan.get("neural_search"), dict) else {}
    out["neural_search"] = {"use": bool(ns.get("use", True))}
    # The topics the user explicitly named — carried through to lane planning, the
    # deepening loop and the report's coverage table (see synthesize._topics_note).
    tp = plan.get("topics") if isinstance(plan.get("topics"), list) else []
    seen_t, topics = set(), []
    for t in tp:
        s = re.sub(r"\s+", " ", str(t or "")).strip(" .;:-")[:120]
        if s and s.lower() not in seen_t:
            seen_t.add(s.lower())
            topics.append(s)
    out["topics"] = topics[:10]
    return out


_TOPIC_LEAD = re.compile(
    r"\b(?:key|main|core|specific|important|priority|primary)\s+(?:topics|areas|questions|aspects|themes|points)\b"
    r"|\btopics?\s*(?:to cover|of interest|include|are|:)"
    r"|\b(?:areas|questions|aspects|themes)\s*(?:to cover|of interest|include|:)", re.I)
_TOPIC_NOISE = {"etc", "e.g", "i.e", "and so on", "and more", "more", "others", "so on",
                "…", "...", "the like", "similar"}


def _explicit_topics(query: str) -> list:
    """The list the user wrote out after a phrase like "Key topics" — taken literally.

    "…Key topics, mic quality, wake words, model comprehension, device control, self
    teaching/learning platform, custom voice models, etc." → those six, in that order.
    In the second real run the planner was left to work the list out and returned ten
    "topics" — the devices in scope and the scoping answers mixed in, and two of the six
    the user wrote ("device control", "self teaching/learning platform") missing. A list
    the user wrote out is not something to interpret. Returns [] when there is no such list."""
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    m = _TOPIC_LEAD.search(q)
    if not m:
        return []
    rest = q[m.end():].lstrip(" ,:;-—–")
    rest = re.sub(r"\b(etc|e\.g|i\.e)\.", r"\1", rest)                      # "etc." is not a sentence end
    rest = re.split(r"(?<=[a-z0-9\)])[.!?](?:\s|$)", rest, maxsplit=1)[0]   # this sentence only
    out, seen = [], set()
    for part in re.split(r"\s*[,;]\s*|\s+and\s+(?=[^,;]*$)", rest):
        t = re.sub(r"^(?:and|or)\s+", "", part.strip(" .:-—–"), flags=re.I).strip()
        if not t or t.lower() in _TOPIC_NOISE:
            continue
        if len(t.split()) > 9 or len(t) > 90:
            return []                      # not a list of short items — do not guess
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:12] if len(out) >= 3 else []


def plan_research(client, query, clarifications, depth, sources, log) -> dict:
    """Decide the channel allocation + report category for THIS question. Returns a
    normalized plan dict (see _default_plan). Graceful: any failure → balanced default."""
    log = log or (lambda m: None)
    try:
        from .synthesize import DR_CATEGORY_PROMPTS
        cats = list(DR_CATEGORY_PROMPTS.keys())
    except Exception:
        cats = _PLAN_CATEGORIES_FALLBACK
    from collections import Counter
    by_type = Counter((s.get("type") or "site") for s in sources if s.get("enabled", True))
    inv = ", ".join(f"{n} {t}" for t, n in by_type.most_common()) or "none"
    sys_p = (
        "You are the PLANNER for a deep web-research tool. BEFORE any searching, "
        "decide how to allocate effort across THREE channels for THIS question:\n"
        "  - api_search  : a fast, broad baseline sweep of open search engines in the browser "
        "(wide net, no page opens). Best for current events and broad factual coverage.\n"
        "  - web_engines : live open search engines in a browser (DuckDuckGo/Brave/Google). Best "
        "for gaps, long-tail pages and primary sources.\n"
        "  - site_queries: targeted queries against a CURATED list of forums/blogs/newsletters. "
        "Best for candid insider/practitioner sentiment and niche specialist opinion.\n"
        f"Curated list inventory available: {inv}.\n"
        "Give each channel a boolean 'use' and a 'weight' in [0,1]; weights should sum to ~1. "
        "Set use=false to SKIP a channel — e.g. skip site_queries for a pure current-events "
        "fact-check; go api-light and forum-heavy for 'what do practitioners really think' "
        "questions; go site-heavy for niche-community topics. For site_queries.emphasis, list the "
        "source TYPES most worth leaning on (any of: forum, blog, substack), or [].\n"
        f"Also classify the question into exactly ONE report category from: {', '.join(cats)}.\n"
        "Finally list 'topics': the distinct topics or sub-questions the user EXPLICITLY asked "
        "to have covered, as short noun phrases in the user's own words and order, at most 10. "
        "Strict rules: (1) a list the user wrote out (after 'key topics', 'including', "
        "'such as'…) IS the topic list — copy every item, add none. (2) Only the QUESTION "
        "defines topics; the CONTEXT (scoping answers) narrows how to research them and adds "
        "none. (3) The devices, products, people or sources that are the SCOPE of the question "
        "are not topics. (4) Qualities the user wants ('low latency', 'recent') are not topics. "
        "(5) Do not invent topics; a single-point question has [].\n"
        "Respond with ONLY JSON: {\"category\":\"..\",\"rationale\":\"one sentence why\","
        "\"api_search\":{\"use\":true,\"weight\":0.3},"
        "\"web_engines\":{\"use\":true,\"weight\":0.3},"
        "\"site_queries\":{\"use\":true,\"weight\":0.4,\"emphasis\":[\"forum\"]},"
        "\"topics\":[\"..\",\"..\"]}")
    msg = f"QUESTION:\n{query}"
    if clarifications:
        msg += f"\n\nCONTEXT:\n{clarifications[:800]}"
    try:
        r = client.messages.create(model=get_model("plan"), max_tokens=800, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            dp = _default_plan("planner returned no JSON")
            dp["topics"] = _explicit_topics(query)
            return dp
        plan = _normalize_plan(json.loads(m.group(0)), cats)
        plan["_planned"] = True
        log(f"[plan] category={plan['category']} · "
            f"api={plan['api_search']} web={plan['web_engines']} site={plan['site_queries']} "
            f"— {plan.get('rationale', '')[:90]}")
        listed = _explicit_topics(query)
        if listed:
            plan["topics"] = listed          # the user's own list wins over the planner's reading
        if plan.get("topics"):
            log(f"[plan] topics you {'listed' if listed else 'named'} ({len(plan['topics'])}): "
                + " · ".join(plan["topics"]))
        return plan
    except Exception as e:  # noqa: BLE001
        log(f"[plan] planner failed ({type(e).__name__}); using balanced default")
        return _default_plan(f"planner error: {type(e).__name__}")


def _apply_channel_overrides(plan: dict, overrides, log=None) -> dict:
    """Apply the user's manual channel toggles as HARD overrides on top of the planner.
    overrides: {api_search|web_engines|site_queries: bool}. A channel set False is forced
    off (use=False, weight=0) regardless of what the planner chose; True is left to the
    planner's decision. Remaining used channels are renormalized to keep their proportions."""
    log = log or (lambda m: None)
    if not isinstance(overrides, dict) or not overrides:
        return plan
    # neural_search is additive/no-weight — handle it explicitly (before the early return
    # below, which only fires for the three weighted channels). False = forced off; True =
    # left as-is (default on, still gated by exa_enabled() at run time).
    if overrides.get("neural_search") is False:
        plan["neural_search"] = {"use": False, "_user_off": True}
        log("[plan] user disabled neural search (Exa)")
    elif overrides.get("neural_search") is True:
        plan.setdefault("neural_search", {"use": True})["use"] = True
    off = [k for k in _PLAN_CHANNELS if overrides.get(k) is False]
    if not off:
        return plan
    for k in off:
        ch = dict(plan.get(k, {}))
        ch["use"] = False
        ch["weight"] = 0.0
        ch["_user_off"] = True
        plan[k] = ch
    used = [k for k in _PLAN_CHANNELS if plan.get(k, {}).get("use", True)]
    total = sum(plan[k].get("weight", 0) for k in used)
    if used and total > 0:
        for k in used:
            plan[k]["weight"] = round(plan[k]["weight"] / total, 3)
    elif used:
        for k in used:
            plan[k]["weight"] = round(1.0 / len(used), 3)
    plan["user_disabled"] = off
    log(f"[plan] user disabled channels: {off}; active: {used or '(none)'}")
    return plan


def _plan_summary_msg(plan: dict) -> str:
    """Short human-readable plan summary for the progress UI / audit."""
    names = {"api_search": "baseline sweep", "web_engines": "web engines", "site_queries": "curated sites"}
    parts = []
    for k in _PLAN_CHANNELS:
        ch = plan.get(k, {})
        w = ch.get("weight", 0)
        if ch.get("use", True) and w > 0:
            parts.append((names[k], w))
    parts.sort(key=lambda t: -t[1])
    mix = " · ".join(f"{n} {int(round(w * 100))}%" for n, w in parts) or "browser only"
    emph = plan.get("site_queries", {}).get("emphasis", [])
    e = f" (favoring {', '.join(emph)})" if emph else ""
    ns = " · + neural (Exa)" if plan.get("neural_search", {}).get("use") else ""
    return f"Plan [{plan.get('category', 'general')}]: {mix}{e}{ns}"


def _stage2_focus(plan: dict, use_sites: bool, use_engines: bool) -> str:
    """Translate the plan's engine/site emphasis into a Stage-2 prompt directive."""
    emph = plan.get("site_queries", {}).get("emphasis", [])
    et = f" Favor these source types: {', '.join(emph)}." if emph else ""
    if use_engines and not use_sites:
        return ("PLAN FOCUS: prioritise the open-web search ENGINES for this question; the curated "
                "source list is de-prioritised — only forum_search/site_search a listed source if "
                "it is clearly on-point.")
    if use_sites and not use_engines:
        return ("PLAN FOCUS: prioritise forum_search/site_search of the CURATED sources listed "
                "below (candid/practitioner material); use open engines sparingly, mainly to "
                "locate specific pages." + et)
    sw = plan.get("site_queries", {}).get("weight", 0)
    ew = plan.get("web_engines", {}).get("weight", 0)
    if sw > ew * 1.3:
        return ("PLAN FOCUS: lean toward forum_search/site_search of the curated sources "
                "(practitioner/insider sentiment) while still using engines to fill gaps." + et)
    if ew > sw * 1.3:
        return ("PLAN FOCUS: lean toward open-web engines for breadth; forum/site-search the "
                "curated sources where they clearly add candid or specialist signal." + et)
    return "PLAN FOCUS: balance open-web engines with forum/site search of the curated sources." + et


# Always kept regardless of the relevance filter — broad, general-purpose sources
# that earn their place on almost any research question.
_ALWAYS_ON_DOMAINS = set()   # relevance gates EVERY site now — nothing is forced into a query
_SEED_FILTER_THRESHOLD = 12   # below this, inject the whole seed list (no filter call)
_SEED_FILTER_LIMIT = 18       # max topical forums injected into a browser stage


def _select_relevant_seed_sources(client, query, clarifications, sources, log,
                                  limit=_SEED_FILTER_LIMIT, emphasis=None):
    """Pick the topically-relevant subset of the (possibly large) seed source table for
    THIS question, using each source's Description. Keeps the Stage-2 prompt focused and
    honors signal-over-noise: with hundreds of seeded forums we must NOT dump them all in.
    Always unions in the broad always-on sources. One cheap call; on failure, fall back to
    always-on + the first N (never the whole list).

    emphasis: optional list of source TYPES (e.g. ['forum','blog']) the planner judged most
    worth leaning on — passed as a soft preference to the selector."""
    if len(sources) <= _SEED_FILTER_THRESHOLD:
        return sources
    always = [s for s in sources if (s.get("domain") or "").lower() in _ALWAYS_ON_DOMAINS]
    pool = [s for s in sources if (s.get("domain") or "").lower() not in _ALWAYS_ON_DOMAINS]
    listing = "\n".join(
        f"- {s['domain']} ({s.get('type','site')})"
        + (f" — {(s.get('description') or s.get('notes') or '').strip()[:200]}"
           if (s.get('description') or s.get('notes')) else "")
        for s in pool)
    emph_note = ""
    if emphasis:
        emph_note = (f" The research plan favors these source TYPES for this question: "
                     f"{', '.join(emphasis)} — prefer them when choosing, all else equal.")
    sys_p = (f"Given a research question and a large list of candidate forums/sites (each with a "
             f"description of what it covers), return ONLY the domains whose subject matter is "
             f"topically appropriate for THIS question. Be selective — most will be irrelevant. "
             f"Return at most {limit} domains, best matches first.{emph_note} "
             f"Respond with ONLY JSON: {{\"domains\": [\"...\"]}}. Empty list if none apply.")
    msg = f"QUESTION:\n{query}"
    if clarifications:
        msg += f"\n\nCONTEXT:\n{clarifications[:800]}"
    msg += f"\n\nCANDIDATE SOURCES:\n{listing}"
    try:
        r = client.messages.create(model=get_model("route"), max_tokens=600, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        picked = set(json.loads(m.group(0)).get("domains", [])) if m else set()
        sel = [s for s in pool if s["domain"] in picked][:limit]
        out = always + sel
        log(f"[seed-filter] {len(out)} relevant seed sources of {len(sources)}: "
            f"{[s['domain'] for s in out]}")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"[seed-filter] relevance filter failed ({type(e).__name__}); "
            f"using always-on + first {limit}")
        return (always + pool)[:limit + len(always)]


def _listing(items) -> str:
    def _blurb(s):
        d = (s.get("description") or s.get("notes") or "").strip()
        return f" — {d[:160]}" if d else ""
    return "\n".join(f"  - {s.get('name') or s.get('domain','')} ({s['domain']}, {s.get('type','site')})"
                     + _blurb(s)
                     for s in items) or "  (none)"


# ── Stage 2 as PARALLEL LANES + a sequential hot-trail digger ─────────────────
# The orchestrator-worker pattern (Anthropic's research system; open_deep_research):
# a lead decomposes the question into independent lanes, parallel workers gather,
# then ONE synthesis step writes from the pooled context. Our twist honors precept 3
# (chase the hot trail): after the lanes finish, a lead review picks the single
# hottest trail and one sequential digger follows it with whatever Stage-2 time is
# left. Model latency dominates a turn, so N lanes ≈ N× pages per minute; the browser
# serializes page actions on its owner thread (see browser.py).
_LANES_BY_DEPTH = {"standard": 3, "deep": 4, "exhaustive": 5}


def _max_lanes(depth: str) -> int:
    try:
        n = int(os.environ.get("DRT_LANES", "") or 0)
    except ValueError:
        n = 0
    return max(1, min(8, n or _LANES_BY_DEPTH.get(depth, 3)))


def plan_lanes(client, query, clarifications, stage1_brief, max_lanes, log,
               topics=None) -> list[dict]:
    """Decompose the question into 1..max_lanes independent research lanes.
    Returns [{name, mission, queries:[...]}] — [] on failure or when one lane suffices.
    `topics` = the topics the user explicitly named: every one must be owned by a lane."""
    log = log or (lambda m: None)
    if max_lanes <= 1:
        return []
    sys_p = (
        "You are the LEAD of a parallel deep-research team. Decompose the user's question "
        f"into 1 to {max_lanes} independent research LANES that separate researchers can "
        "pursue AT THE SAME TIME without overlapping. A lane is a distinct angle, sub-question, "
        "stakeholder, source type, or community whose findings are NEEDED for the SPECIFIC "
        "question — not a generic outline. Do not manufacture lanes: a narrow question may "
        "need only 1 or 2. Good lane splits: primary/official evidence vs. practitioner/forum "
        "discussion vs. adversarial (complaints/failures/lawsuits) vs. a specific named entity "
        "or version vs. a specific community to mine.\n"
        "For each lane give: name (3-6 words), mission (2 sentences: exactly what to find and "
        "where it most likely lives), queries (2-3 ENGINE queries — compact keyword strings, "
        "2-6 terms, proper nouns / product names / insider vocabulary first, no question words).\n"
        "Respond with ONLY JSON: {\"lanes\": [{\"name\": \"..\", \"mission\": \"..\", "
        "\"queries\": [\"..\", \"..\"]}]}")
    msg = f"QUESTION:\n{query}"
    if clarifications:
        msg += f"\n\nCONTEXT:\n{clarifications[:1500]}"
    topics = [str(t).strip() for t in (topics or []) if str(t).strip()][:10]
    if topics:
        # The first real run named six topics; two had no lane and came back near-empty.
        msg += ("\n\nTOPICS THE USER NAMED — every one must be OWNED by a lane (a lane may own "
                "several related ones). Say in each lane's mission which of these it owns, and "
                "give at least one query aimed at each owned topic:\n"
                + "\n".join(f"  {i}. {t}" for i, t in enumerate(topics, 1)))
    if stage1_brief:
        msg += f"\n\nBASELINE BRIEF (what a quick sweep already established):\n{stage1_brief[:3000]}"
    try:
        r = client.messages.create(model=get_model("plan"), max_tokens=1800, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        raw = json.loads(m.group(0)).get("lanes", []) if m else []
    except Exception as e:  # noqa: BLE001
        log(f"[lane] lane planning failed ({type(e).__name__}) — single-lane Stage 2")
        return []
    lanes = []
    for ln in raw:
        if not isinstance(ln, dict):
            continue
        name = str(ln.get("name") or "").strip()[:80]
        mission = str(ln.get("mission") or "").strip()[:600]
        qs = [str(q).replace("?", "").strip()[:120] for q in (ln.get("queries") or [])
              if str(q).strip()][:(5 if topics else 3)]
        if name and mission:
            lanes.append({"name": name, "mission": mission, "queries": qs})
        if len(lanes) >= max_lanes:
            break
    if lanes:
        log(f"[lane] planned {len(lanes)} lane(s): " + " | ".join(l["name"] for l in lanes))
    return lanes


def _lead_review(client, query, result, lanes, lane_notes, log) -> dict:
    """After the lanes: pick the ONE hottest trail worth a dedicated sequential dig.
    Returns {hot_trail, why, queries, start_urls} (hot_trail '' = nothing worth it)."""
    log = log or (lambda m: None)
    parts = []
    reports = {r.get("lane"): r.get("reason", "") for r in (result.lane_reports or [])}
    for i, ln in enumerate(lanes):
        tag = f"L{i + 1}"
        notes = "\n".join(lane_notes[i])[-1200:] if lane_notes[i] else "(no notes)"
        parts.append(f"### {tag} — {ln['name']}\nMISSION: {ln['mission']}\n"
                     f"FINISH REASON: {reports.get(tag, '(did not finish — clock/skip)')}\n"
                     f"NOTES (tail):\n{notes}")
    pages = [it for it in result.items if it.via.startswith("stage2")]
    plist = "\n".join(f"- {(it.title or it.url)[:90]} — {it.url[:100]}" for it in pages[-40:])
    sys_p = (
        "You are the LEAD reviewing what parallel research lanes just gathered. Decide whether "
        "there is ONE hot trail — a specific thread, community, document, author, version, or "
        "angle where the research was visibly closing in on the user's SPECIFIC question but "
        "stopped short (clock ran out, wall hit, one hop away from the primary source). If so, "
        "describe it concretely so a single researcher can continue it immediately: where to "
        "start (URLs already seen that should be re-read deeper / paginated / followed), and "
        "2-4 ENGINE queries (compact keyword strings). If the lanes genuinely exhausted the "
        "question, or nothing stands out, set hot_trail to an empty string.\n"
        "Respond with ONLY JSON: {\"hot_trail\": \"..\", \"why\": \"..\", "
        "\"queries\": [\"..\"], \"start_urls\": [\"..\"]}")
    msg = (f"QUESTION:\n{query}\n\nLANE REPORTS:\n" + "\n\n".join(parts) +
           f"\n\nPAGES HARVESTED IN STAGE 2 ({len(pages)}):\n{plist or '(none)'}")
    try:
        r = client.messages.create(model=get_model("plan"), max_tokens=1200, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:  # noqa: BLE001
        log(f"[lane] lead review failed ({type(e).__name__}) — skipping the hot-trail dig")
        return {}
    return {"hot_trail": str(data.get("hot_trail") or "").strip()[:800],
            "why": str(data.get("why") or "").strip()[:600],
            "queries": [str(q).replace("?", "").strip()[:120] for q in (data.get("queries") or [])
                        if str(q).strip()][:4],
            "start_urls": [str(u).strip() for u in (data.get("start_urls") or [])
                           if str(u).strip().startswith("http")][:6]}


def _run_stage2_lanes(client, br, result, seen_urls, *, query, task, clarifications,
                      governance, listing, focus, tools, cap2, pool, depth, stage1_brief,
                      stage2_end, gather_deadline, progress, log, notes, skip_event,
                      stop_event, sources):
    """Stage 2 orchestration: lane planning → parallel lanes → lead review → hot-trail dig.
    Falls back to the classic single loop when one lane suffices (or planning fails)."""
    max_lanes = _max_lanes(depth)
    lanes = plan_lanes(client, query, clarifications, stage1_brief, max_lanes, log,
                       topics=(getattr(result, "plan", None) or {}).get("topics"))
    if len(lanes) <= 1:
        task2 = task
        if lanes:
            ln = lanes[0]
            task2 += (f"\n\nRESEARCH LANE: {ln['name']}\nMISSION: {ln['mission']}\n"
                      f"SEED QUERIES: {'; '.join(ln.get('queries') or [])}")
        _run_browser_stage(
            client, br, result, seen_urls, stage_name="stage2",
            system=_stage_system("engines", governance, listing, cap2, stage1_brief,
                                 focus_note=focus, pool=pool),
            task=task2, tools=tools, cap=cap2, progress=progress, log=log, notes=notes,
            skip_event=skip_event, question=query, sources=sources, pool=pool,
            deadline=stage2_end, hard_deadline=gather_deadline, stop_event=stop_event)
        return

    n = len(lanes)
    result.lanes = [{"name": l["name"], "mission": l["mission"]} for l in lanes]
    lane_cap = {"searches": max(4, round(cap2["searches"] / n)),
                "pages": max(5, round(cap2["pages"] / n)),
                "max_turns": cap2["max_turns"]}
    # Lanes get ~70% of the Stage-2 share; the digger gets the rest — plus whatever the
    # lanes leave unused when they exhaust themselves early (the 1-hour tier's intent).
    lanes_end = min(stage2_end, _now() + 0.7 * max(0, stage2_end - _now()))
    log(f"[lane] running {n} lanes in parallel · each {lane_cap['searches']}s/"
        f"{lane_cap['pages']}p soft · lanes window {int(lanes_end - _now())}s")
    progress("stage2", None, f"{n} parallel lanes: " + " · ".join(l["name"] for l in lanes)[:120])
    lane_notes = [[] for _ in lanes]

    def _one(i, ln):
        tag = f"L{i + 1}"
        llog = _lane_log(log, tag)
        task_i = (f"{task}\n\nYOUR RESEARCH LANE ({tag} of {n}; the other lanes run in "
                  f"parallel and cover the other angles): {ln['name']}\n"
                  f"MISSION: {ln['mission']}\n"
                  f"SEED QUERIES (engine-optimized — refine as you learn): "
                  + ("; ".join(ln.get("queries") or []) or "(craft your own)") +
                  "\nStay on your lane. Finish when your lane is exhausted — do not tour.")
        sysm = _stage_system("engines", governance, listing, lane_cap, stage1_brief,
                             focus_note=(focus + f"\nLANE {tag}: {ln['name']} — {ln['mission']}"),
                             pool=pool)
        try:
            _run_browser_stage(
                client, br, result, seen_urls, stage_name=f"stage2/{tag}", system=sysm,
                task=task_i, tools=tools, cap=lane_cap, progress=progress, log=llog,
                notes=lane_notes[i], skip_event=skip_event, question=query, sources=sources,
                pool=pool, deadline=lanes_end, hard_deadline=gather_deadline,
                stop_event=stop_event, lane=tag, clear_skip=False)
        except Exception as e:  # noqa: BLE001
            msg = f"[stage2/{tag}] aborted: {type(e).__name__}: {e}"
            lane_notes[i].append(msg)
            llog(msg + " — other lanes continue")

    with _cf.ThreadPoolExecutor(max_workers=n, thread_name_prefix="drt-lane") as ex:
        list(ex.map(lambda p: _one(*p), enumerate(lanes)))
    if skip_event is not None and skip_event.is_set():
        skip_event.clear()
    for i, ln in enumerate(lanes):
        if lane_notes[i]:
            notes.append(f"[stage2/L{i + 1} · {ln['name']}]\n" + "\n\n".join(lane_notes[i]))
    log(f"[lane] lanes complete — {result.pages_used} pages / {result.searches_used} "
        f"searches so far · pool {pool['searches']}s/{pool['pages']}p left")
    if stop_event is not None and stop_event.is_set():
        return

    # ── lead review → sequential hot-trail dig on the remaining Stage-2 time ──
    remaining = stage2_end - _now()
    if remaining < 45:
        log("[lane] no Stage-2 time left for a hot-trail follow-up — moving on")
        return
    rev = _lead_review(client, query, result, lanes, lane_notes, log)
    trail = (rev.get("hot_trail") or "").strip()
    if not trail:
        log("[lane] lead review: no single hot trail worth a dedicated dig — moving on")
        return
    log(f"[lane] HOT TRAIL → {trail[:220]}")
    progress("stage2", None, f"Hot trail: {trail[:80]}")
    dig_cap = {"searches": max(4, min(pool["searches"], round(cap2["searches"] * 0.4))),
               "pages": max(5, min(pool["pages"], round(cap2["pages"] * 0.4))),
               "max_turns": cap2["max_turns"]}
    task_d = (f"{task}\n\nLEAD REVIEW — CONTINUE THE HOT TRAIL. The parallel lanes have "
              f"finished; this is the one line of inquiry closest to the specific answer:\n"
              f"TRAIL: {trail}\nWHY: {rev.get('why', '')}\n"
              f"START HERE: {'; '.join(rev.get('start_urls') or []) or '(use the queries)'}\n"
              f"QUERIES: {'; '.join(rev.get('queries') or []) or '(craft your own)'}\n"
              f"Follow it as far as it goes — pagination, replies, cited primaries, the "
              f"author's other posts. Finish when it is exhausted.")
    _run_browser_stage(
        client, br, result, seen_urls, stage_name="stage2/dig",
        system=_stage_system("engines", governance, listing, dig_cap, stage1_brief,
                             focus_note=(focus + "\nROLE: hot-trail digger (sequential, "
                                         "after the parallel lanes)"), pool=pool),
        task=task_d, tools=tools, cap=dig_cap, progress=progress,
        log=_lane_log(log, "dig"), notes=notes, skip_event=skip_event, question=query,
        sources=sources, pool=pool, deadline=stage2_end, hard_deadline=gather_deadline,
        stop_event=stop_event, lane="dig", model=get_model("dig"))


# ── dedicated per-site sweeps (2026-11-12) ────────────────────
# Stage 3 used to be ONE agent handed every credentialed site at once, in whatever time
# Stage 2 left. In the first real run it signed in to four sites, then re-read GitHub
# pages it already had, searched GitHub and Reddit through a public search engine, ran
# one query on the Home Assistant forum, never opened X at all, and finished in 5 turns.
# Now every site the plan selected gets its OWN agent, in parallel, with one job: search
# that site through its own search and read the best of it. A site can no longer be
# skipped because another site used the time.
_SITE_TIPS = {
    "x": ("Results are single POSTS ('@handle: text'). The useful ones come from builders "
          "and maintainers and carry specifics, numbers or links. Open a post to read its "
          "thread and replies; open the repo / blog / video a post links to (off-site links "
          "are allowed) — that is usually where the substance is. Search by product or "
          "project name plus one topic word; hashtags and from:handle also work."),
    "github": ("site_search returns repositories and issues / pull requests. Open READMEs "
               "for what a project does and its issues for what actually goes wrong. "
               "<repo>/issues, <repo>/discussions and <repo>/releases are worth opening "
               "directly for any project that matters to the question."),
    "hn": ("Results are stories, each linked to its Hacker News DISCUSSION page — the "
           "comments are the value. Open the discussion and read it through."),
    "reddit": ("Results are threads. Open them — the comment tree is read in full. Search "
               "phrasing as people would title a post; add a subreddit name as a keyword "
               "to narrow."),
    "discourse": ("Results are forum topics, with the matching post's excerpt. Long topics "
                  "paginate — follow NEXT PAGE and use open_page offset to read on."),
    "xenforo": ("Results are forum threads. Long threads paginate — follow NEXT PAGE and "
                "use open_page offset to read on."),
}


def _site_tool_defs():
    """Tools for a single-site sweep: the site's own search, page reading, extension, finish."""
    out = []
    for t in _tool_defs(include_web_search=False):
        if t["name"] == "site_search":
            t = dict(t, description=(
                "Search THIS sweep's site through its OWN search, in the signed-in browser. "
                "Site searches match ALL the words: use 2-3 distinctive keywords per query "
                "and run several different queries rather than one long one. `domain` may be "
                "omitted — it is always this sweep's site."),
                input_schema={"type": "object", "properties": {
                    "domain": {"type": "string"}, "query": {"type": "string"}},
                    "required": ["query"]})
            out.append(t)
        elif t["name"] in ("open_page", "request_extension", "finish"):
            out.append(t)
    return out


def _site_sweep_system(dom, signed_in, platform, governance, cap, context_brief,
                       already, pool=None):
    tip = _SITE_TIPS.get(platform or "", "")
    have = ("\nALREADY READ FROM THIS SITE THIS RUN (do not re-open; build on them):\n"
            + "\n".join(f"  - {u}" for u in already[:25]) + "\n") if already else ""
    ctx = (f"\nWHAT THE RUN HAS ESTABLISHED SO FAR (target what is missing):\n"
           f"{context_brief}\n" if context_brief else "")
    pool_line = (f" from a shared global pool (currently {pool['searches']} searches / "
                 f"{pool['pages']} opens)" if pool else "")
    return f"""You are the DEDICATED SWEEP of ONE source for the Deep Research Tool: {dom}.
{"This browser is SIGNED IN to it, so you see what members see." if signed_in else "This browser is NOT signed in to it — you see the public view."}

The research plan selected {dom} for this question. Other agents are covering the open web
and every other site right now; NOBODY ELSE will search {dom}. If you do not find what it
holds on the question, the report goes out without it.

HOW TO WORK
  - site_search runs {dom}'s OWN search. It matches ALL the words, so keep each query to
    2-3 distinctive keywords (a product / project / model name plus one topic word). Cover
    the question's main angles with SEVERAL different queries — never one long one.
  - Issue your first 3-4 queries together in ONE turn, then open the strongest results,
    4-5 per turn. Judge by title, excerpt and date; prefer firsthand detail and recency.
  - READING is the job; searching only finds what to read. Aim to read 6-10 of the
    strongest threads on this site. After a batch of reads, run the new queries the
    reading suggests (a project name, a model number, a term people actually use here).
  - USE THE WHOLE WINDOW. The clock (⏱) decides when you stop, not the allocation: with
    more than a minute left and strong results still unread, keep reading.
  - Read through: NEXT PAGE links, open_page offset for long pages, in-site links to
    related threads. Links that lead off-site may be opened when they carry the substance.
  - Stay on {dom} for searching.{(chr(10) + "  - " + tip) if tip else ""}
  - If several distinct queries show the site has nothing on the question, finish and say
    so plainly — that is a valid, useful result. Do not open weak pages to fill a quota.

NARRATE AS YOU GO: one short sentence before each pivot and whenever a page yields a real
finding — the user watches this feed live.

================ GOVERNING PRINCIPLES ================
{governance}
=====================================================
{ctx}{have}
BUDGET: TIME-BOXED — the clock is on every tool result (⏱). Soft allocation
{cap['searches']} searches / {cap['pages']} page opens, drawn{pool_line}.
request_extension buys more when the trail is hot; when the window closes, finish at once
with what you have. Do NOT attempt to log in to anything."""


def _run_site_sweeps(client, br, result, seen_urls, sites, *, task, governance,
                     context_brief, pool, deadline, hard_deadline, progress, log, notes,
                     skip_event, stop_event, sources, question, stage_prefix="stage3"):
    """One dedicated agent per site, in parallel (see the block comment above). `sites` is
    a list of plan entries ({"domain": ...}). Appends one row per site to
    result.site_sweeps and returns the rows."""
    sites = [s for s in sites if s.get("domain")]
    if not sites:
        return []
    n = len(sites)
    cap = {"searches": 12, "pages": 18, "max_turns": 40}
    tools = _site_tool_defs()
    log(f"[lane] dedicated sweep of {n} site(s) in parallel: "
        f"{', '.join(s['domain'] for s in sites)} · window {max(0, int(deadline - _now()))}s")
    progress(stage_prefix, None, "Sweeping " + ", ".join(s["domain"] for s in sites)[:140])
    rows = [None] * n
    site_notes = [[] for _ in sites]

    def _one(i, s):
        dom = s["domain"]
        signed = any(_same_site(dom, d) for d in (result.logged_in or []))
        stat = {"domain": dom, "signed_in": signed, "searches": 0, "results": 0,
                "pages": 0, "note": ""}
        rows[i] = stat
        slog = _lane_log(log, dom)
        try:
            plat = br._platform_of(dom) if hasattr(br, "_platform_of") else ""
        except Exception:  # noqa: BLE001
            plat = ""
        with _SHARED_LOCK:
            already = [it.url for it in result.items
                       if it.via != "stage1" and _same_site(host_of(it.url), dom)]
        task_i = (f"{task}\n\nYOUR SITE: {dom}. Search it now — start with 3-4 short "
                  f"queries in one turn.")
        try:
            _run_browser_stage(
                client, br, result, seen_urls, stage_name=f"{stage_prefix}/{dom}",
                system=_site_sweep_system(dom, signed, plat, governance, cap, context_brief,
                                          already, pool=pool),
                task=task_i, tools=tools, cap=cap, progress=progress, log=slog,
                notes=site_notes[i], skip_event=skip_event, question=question,
                sources=sources, pool=pool, deadline=deadline, hard_deadline=hard_deadline,
                stop_event=stop_event, clear_skip=False, site_only=dom, site_stat=stat)
        except Exception as e:  # noqa: BLE001
            stat["note"] = f"sweep aborted: {type(e).__name__}: {e}"[:300]
            slog(f"[{stage_prefix}] {stat['note']} — the other sites continue")
        slog(f"[lane] sweep done — {stat['searches']} searches · {stat['results']} results · "
             f"{stat['pages']} pages read")

    with _cf.ThreadPoolExecutor(max_workers=min(n, 6), thread_name_prefix="drt-site") as ex:
        list(ex.map(lambda p: _one(*p), enumerate(sites)))
    if skip_event is not None and skip_event.is_set():
        skip_event.clear()
    for i, s in enumerate(sites):
        if site_notes[i]:
            notes.append(f"[{stage_prefix}/{s['domain']}]\n" + "\n\n".join(site_notes[i]))
    done = [r for r in rows if r]
    with _SHARED_LOCK:
        for r in done:          # a second pass adds to the site's existing row
            prev = next((x for x in result.site_sweeps if x.get("domain") == r["domain"]), None)
            if prev is None:
                result.site_sweeps.append(r)
            else:
                for k in ("searches", "results", "pages"):
                    prev[k] = prev.get(k, 0) + r.get(k, 0)
                prev["signed_in"] = prev.get("signed_in") or r.get("signed_in")
                prev["note"] = r.get("note") or prev.get("note", "")
    return done


# ── orchestrator ──────────────────────────────────────────────
def run_local_baseline(browser, client, query, clarifications="", depth="standard",
                       log=None, stop_event=None) -> dict:
    """Stage 1 for ALL providers: the broad baseline browser sweep. The model generates
    a few BROAD queries, runs them through the browser search engines (the real browser, Brave —
    never a sandboxed/server-side search agent), collects the surfaced URLs, and writes
    a terse baseline brief. Returns {findings_md, sources:[{title,url}], used}.
    Shallow-and-broad (no page opens); Stage 2 still does the deep reading."""
    log = log or (lambda m: None)
    if browser is None or client is None:
        return {"findings_md": "", "sources": [], "used": False}
    n_q = {"standard": 5, "deep": 7}.get(depth, 5)

    # 1) Broad, varied queries from the model (wide net); fall back to simple variants.
    q_sys = (f"You write ENGINE-OPTIMIZED web-search queries for the broad baseline pass of "
             f"a research tool. Output ONLY a JSON array of {n_q} queries that together cast "
             f"a WIDE net over the question — different angles, terms, and stakeholders.\n"
             f"QUERY CRAFT — this matters: engines rank COMPACT KEYWORD STRINGS far better "
             f"than sentences. Each query = 2-6 terms; lead with proper nouns / product or "
             f"model names / insider vocabulary; NO question words or filler (what, how, "
             f"the, of, are); \"quoted phrases\" only where exact wording matters. "
             f"Example shape: [\"acme x200 coupling failure\", \"x200 recall 2024 forum\"]")
    q_user = f"QUESTION:\n{query}" + (f"\n\nCONTEXT:\n{clarifications[:800]}" if clarifications else "")
    queries = []
    try:
        r = client.messages.create(model=get_model("route"), max_tokens=400, system=q_sys,
                                   messages=[{"role": "user", "content": q_user}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", txt, re.S)
        if m:
            queries = [q.strip() for q in json.loads(m.group(0)) if isinstance(q, str) and q.strip()]
    except Exception as e:  # noqa: BLE001
        log(f"[local-baseline] query-gen failed ({type(e).__name__}); using fallbacks")
    if not queries:
        # Keyword-compress the question rather than firing it verbatim at the engines.
        _stop = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "what", "is",
                 "are", "how", "who", "does", "do", "did", "have", "has", "any", "with", "by",
                 "most", "their", "them", "its", "been", "was", "were", "which", "that"}
        words = re.findall(r"[A-Za-z0-9][\w\-.]*", query)
        base = " ".join(w for w in words if w.lower() not in _stop)[:70].strip() or query[:60]
        queries = [base, f"{base} problems", f"{base} review", f"{base} forum"]
    # Hard guard: a question that slipped through gets its punctuation stripped.
    queries = [q.replace("?", "").strip() for q in queries if q.strip()][:n_q]
    _trace(f"\n=== STAGE 1 — BASELINE BROWSER SWEEP ===\nbroad queries: "
           f"{json.dumps(queries, ensure_ascii=False)}")

    # 2) Run them across engines; collect surfaced results (NO page opens).
    try:
        from .api_search import _load_blocklist
        blocked = _load_blocklist()
    except Exception:
        blocked = []
    seen, pool = set(), []

    def _absorb(results, label):
        for rslt in results:
            url = (getattr(rslt, "url", "") or "").strip()
            if not url or url in seen or any(b and b in url for b in blocked):
                continue
            seen.add(url)
            pool.append({"url": url, "title": getattr(rslt, "title", "") or url,
                         "snippet": getattr(rslt, "snippet", "") or ""})
        _trace(f"[local-baseline] {label} → {len(results)} results ({len(pool)} unique so far)")

    for i, q in enumerate(queries):
        RUN_CLOCK.wait_if_paused(stop_event, log, "baseline sweep")
        if stop_event is not None and stop_event.is_set():
            log("[stop] baseline sweep halted by user")
            break
        engine = "brave" if (i % 2) else "duckduckgo"
        # Browser engine first; an empty result falls through the provider chain
        # (Tavily → Exa) inside _engine_search.
        results, via = _engine_search(browser, engine, q, limit=8, log=log)
        _absorb(results, f"{via}: {q!r}")
    # API discovery widens the net with a DIFFERENT index each: Tavily (search API) and
    # Exa (neural). Two queries each — the broadest ones — so the baseline pool isn't
    # hostage to whatever the browser engines happened to rank.
    if not (stop_event is not None and stop_event.is_set()):
        if tavily_enabled():
            for q in queries[:2]:
                _absorb(_sr(tavily_search(q, num=8, log=log), "tavily"), f"tavily: {q!r}")
        if exa_enabled():
            for q in queries[:2]:
                _absorb(_sr(exa_search(q, num=8, log=log), "exa"), f"exa: {q!r}")
    if not pool:
        return {"findings_md": "", "sources": [], "used": False}

    # Rerank the WIDE multi-engine pool by SEMANTIC relevance to the research question so
    # the brief + surfaced-URL list lead with the highest-signal results, not just engine
    # order. (Local embedding rerank; identity fallback if unavailable — see rerank.py.)
    from .rerank import rerank, available as _rr_ok
    pool = rerank(query, pool, text_of=lambda r: f"{r['title']} {r['snippet']}")
    _trace(f"[local-baseline] pooled {len(pool)} unique results; reranked by relevance "
           f"({'semantic' if _rr_ok() else 'identity/off'})")
    sources = [{"url": r["url"], "title": r["title"]} for r in pool]
    snippets = [f"- {r['title']} — {r['url']}\n  {r['snippet']}" for r in pool if r["snippet"]][:40]

    # 3) Terse baseline brief from the surfaced snippets (same intent as Stage 1's brief).
    b_sys = ("You are the fast baseline pass of a deep web-research tool. From the search "
             "results below, write a TERSE findings brief: the key established facts (with "
             "the strongest sources), open questions still unresolved, and any specialist or "
             "gated sources (forums, paywalled specialists, primary documents) a deeper pass "
             "should pursue. Signal only — no filler. These are SEARCH SNIPPETS, not full "
             "pages, so do not overstate; flag what still needs verifying.")
    b_user = (f"RESEARCH QUESTION:\n{query}\n\n"
              + (f"CLARIFICATIONS:\n{clarifications}\n\n" if clarifications else "")
              + "SURFACED SEARCH RESULTS:\n" + "\n".join(snippets[:40]))
    brief = ""
    try:
        r = client.messages.create(model=get_model("plan"), max_tokens=1200, system=b_sys,
                                   messages=[{"role": "user", "content": b_user}])
        brief = "".join(getattr(b, "text", "") for b in r.content
                        if getattr(b, "type", "") == "text").strip()
    except Exception as e:  # noqa: BLE001
        log(f"[local-baseline] brief synthesis failed ({type(e).__name__}: {e})")
    _trace(f"[local-baseline] surfaced {len(sources)} sources; brief {len(brief)} chars")
    return {"findings_md": brief, "sources": sources, "used": True}


def run_search(query: str, depth: str = "standard", clarifications: str = "",
               browser: DRTBrowser | None = None, log=None, progress=None,
               request_credentials=None, vault=None, skip_event=None,
               channel_overrides=None, client=None, provider="claude",
               deadline=None, stop_event=None, manual_login=None) -> HarvestResult:
    """Run the deterministic 4-stage pipeline. Returns a HarvestResult.

    progress(stage, pct, msg): UI hook (stages: stage1..stage4).
    stop_event: a threading.Event the job layer sets when the user presses STOP —
        halts gathering wherever it is and returns the PARTIAL harvest (the worker
        then either synthesizes it or discards it, per the user's choice). Unlike
        skip_event it is pipeline-level: once set, all remaining stages are skipped.
    request_credentials(candidates) -> {domain: {username,password,login_url} | None}:
        provided by the job layer; collects logins in-app (batched). None → Stage 4 skipped.
    skip_event: a threading.Event the job layer sets to end the CURRENT browser stage early
        (the pipeline then rolls forward to the next stage). Cleared on consumption.
    deadline: absolute epoch seconds when GATHERING must end (the job layer computes it
        from the tier's total window minus the synthesis reserve). None → derived here.
    """
    import anthropic
    from .login import CredentialVault

    _load_env()
    log = log or (lambda m: None)
    progress = progress or (lambda *a, **k: None)
    depth = normalize_depth(depth)
    b = DEPTH_BUDGETS[depth]
    sources = load_sources()
    governance = _load_governance()

    if client is None:
        from .llm import make_client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if provider != "local" and not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set. Add it to the .env file.")
        client = make_client(provider, api_key)
    vault = vault or CredentialVault()

    global _TRACE_FILE
    if os.environ.get("DRT_TRACE", "").strip():
        _tdir = os.path.join(_ROOT, "traces")
        os.makedirs(_tdir, exist_ok=True)
        _TRACE_FILE = os.path.join(_tdir, f"{time.strftime('%Y%m%d_%H%M%S')}_{provider}.trace.txt")
        _trace(f"{'=' * 70}\nDEEP RESEARCH TRACE\nquery:    {query}\nprovider: {provider}\n"
               f"depth:    {depth}\nmodel:    {getattr(client, 'model', _MODEL)}\n{'=' * 70}")
    else:
        _TRACE_FILE = None

    result = HarvestResult(query=query, depth=depth)
    seen_urls: set[str] = set()
    notes: list[str] = []

    task = f"USER RESEARCH QUESTION:\n{query}"
    if clarifications:
        task += f"\n\nCLARIFICATIONS FROM USER:\n{clarifications}"

    # ── PLAN — decide channel allocation + category up front ───
    progress("stage1", None, "Planning the research approach…")
    plan = plan_research(client, query, clarifications, depth, sources, log)
    plan = _apply_channel_overrides(plan, channel_overrides, log)   # user toggles win
    # Hard-gate the neural channel on Exa availability ONCE here, so the plan dict is the
    # single source of truth (summary line, audit, and Stage-2 wiring all read from it).
    if not (exa_enabled() or tavily_enabled()):
        if plan.get("neural_search", {}).get("use"):
            log("[plan] API discovery requested but neither EXA_API_KEY nor TAVILY_API_KEY is set — off")
        plan["neural_search"] = {"use": False, "_unavailable": True}
    result.plan = plan
    result.category = plan.get("category", "") or ""
    _trace(f"\n=== PLAN (generated by {provider}) ===\n{json.dumps(plan, indent=2, ensure_ascii=False)}")
    plan_msg = _plan_summary_msg(plan)

    # TIME is the governor: the gathering window ends at `gather_deadline` (the tier's
    # total minus the synthesis reserve). Stage time shares are soft; request_extension
    # pushes a hot stage toward the hard window. The unit pools below are safety ceilings.
    gather_deadline = deadline or (_now() + max(60, b["seconds"] - b["reserve"]))
    log(f"[plan] research window: {max(0, int(gather_deadline - _now()))}s of gathering "
        f"({depth} tier, ~{b['seconds'] // 60} min total)")
    pool = {"searches": b["searches"], "pages": b["pages"]}
    cap2 = {"searches": max(2, round(b["searches"] * 0.6)),
            "pages": max(4, round(b["pages"] * 0.6)), "max_turns": b["max_turns"]}
    cap3 = {"searches": max(1, b["searches"] - cap2["searches"]),
            "pages": max(2, b["pages"] - cap2["pages"]), "max_turns": b["max_turns"]}

    own_browser = browser is None
    br = browser or DRTBrowser(log=log).start()

    def _guarded_stage(stage_name, **kw):
        """Run one browser stage; a stage-level failure (e.g. a transient API
        connection error) is logged and the pipeline rolls forward rather than
        losing the whole run — Stage 1's brief and any prior harvest survive.
        Once the user has pressed STOP, later stages are no-ops."""
        if stop_event is not None and stop_event.is_set():
            return
        try:
            _run_browser_stage(client, br, result, seen_urls,
                               stage_name=stage_name, stop_event=stop_event, **kw)
        except Exception as e:  # noqa: BLE001
            msg = f"[{stage_name}] aborted: {type(e).__name__}: {e}"
            notes.append(msg)
            log(msg + " — continuing to next stage")

    sweep_fut = None                    # the background site sweeps (see `finally`)
    sweep_halt = threading.Event()
    try:
        # ── STAGE 1 — broad baseline browser sweep (ALL providers) ──
        # Runs in the same visible research browser as every other stage. The old Anthropic
        # server-side web_search (sandboxed, robots.txt-bound, blockable) is gone:
        # wide engine sweeps + semantic rerank → terse brief, no page opens.
        api_ch = plan.get("api_search", {})
        if api_ch.get("use", True) and api_ch.get("weight", 0.34) > 0:
            progress("stage1", None, f"{plan_msg} — broad baseline browser sweep…")
            s1 = run_local_baseline(br, client, query, clarifications, depth, log=log,
                                    stop_event=stop_event)
        else:
            log("[plan] Stage 1 (baseline sweep) de-prioritised for this question; skipping")
            s1 = {"findings_md": "", "sources": [], "used": False}
        if s1.get("findings_md"):
            result.items.append(HarvestItem(
                url="(baseline browser sweep)", title="Stage 1 — baseline browser brief",
                text=s1["findings_md"], source_type="api", via="stage1",
                retrieved_at=_now()))
        result.stage1_sources = s1.get("sources", [])
        stage1_brief = s1.get("findings_md", "")

        # ── STAGE 2 — parallel research lanes in the browser + API discovery (plan-biased) ──
        site_ch = plan.get("site_queries", {})
        web_ch = plan.get("web_engines", {})
        use_sites = site_ch.get("use", True)        # hard on/off (planner skip OR user toggle)
        use_engines = web_ch.get("use", True)
        # API discovery (Exa neural + Tavily) — ON by default when a key is present.
        use_neural = bool(plan.get("neural_search", {}).get("use")) and \
            (exa_enabled() or tavily_enabled())
        # One-time (ephemeral) logins collected this run — used for the search, NEVER saved to the vault.
        ephemeral_creds: dict = {}
        # ── UNIFIED per-query site selection: ONE relevance call over the whole site list. The
        # plan's Claude call picks the topically-relevant sites; 🟢 public ones (login_required
        # != True) feed the open sweep, 🔴 login-required ones go through the login path (vault,
        # else a one-time prompt). Identical on hosted and local — the only difference is whether
        # a stored login already exists. This replaces the old two separate selection calls and
        # fixes the vault-orphan bug (a login is no longer required to be in a second list).
        selected_sites = _select_relevant_seed_sources(
            client, query, clarifications, sources, log,
            emphasis=site_ch.get("emphasis")) if use_sites else []

        def _has_creds(dom):
            c = vault.get(dom) if vault else None
            return bool(c and (c.get("username") or "").strip())

        public_selected, login_selected, have_login, need_prompt = _split_selected_sites(
            selected_sites, _has_creds)
        if selected_sites:
            log(f"[plan] selected {len(selected_sites)} relevant site(s): "
                f"{len(public_selected)} public 🟢, {len(login_selected)} login-required 🔴, "
                f"{sum(1 for s in selected_sites if _has_creds(s['domain']))} with a stored login")

        # What the plan committed to for THIS query — the yardstick for source alerts.
        result.planned_sites = [
            {"domain": s["domain"], "login_required": s.get("login_required"),
             "credentialed": bool(s.get("login_required") is True or _has_creds(s["domain"]))}
            for s in selected_sites]
        # SIGN IN FIRST (2026-11): logins used to happen at Stage 3, minutes into the run,
        # so a dead login was discovered late and the parallel lanes searched those sites
        # logged out. Now every selected site with a stored login is signed into before
        # Stage 2, and a failure raises an alert immediately.
        signed_in = []
        # Manual sign-in is possible only with a visible local browser (not headless/hosted).
        _can_nag = manual_login is not None and getattr(br, "headed", False)

        def _halted():
            return stop_event is not None and stop_event.is_set()

        # Pass 1 — try every site by itself, no prompts. Pass 2 — ONE prompt for all the
        # sites that still need the user, sharing one wait (owner rule: a minute at most).
        pending = []          # (site, kind, creds, why)   kind: "stored" | "nologin"
        outcome = {}          # domain -> (ok, detail)
        for s in list(have_login):
            if _halted():
                break
            dom = s["domain"]
            creds = (vault.get(dom) if vault else None) or {}
            progress("stage2", None, f"Signing in to {dom}…")
            _t_si = time.monotonic()
            try:
                ok, detail = br.ensure_logged_in(dom, creds)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"login error: {type(e).__name__}"
            # Signing in is overhead, not research: it does not come out of the window.
            RUN_CLOCK.credit(time.monotonic() - _t_si)
            outcome[dom] = (ok, detail)
            if not ok:
                pending.append((s, "stored", creds, detail))
        for s in list(need_prompt):
            if _halted():
                break
            dom = s["domain"]
            # No stored login — but the profile may already hold a session from an earlier
            # manual sign-in (the normal state for 2FA sites such as GitHub).
            _t_si = time.monotonic()
            try:
                ok, detail = br.ensure_logged_in(dom, {})
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"login error: {type(e).__name__}"
            RUN_CLOCK.credit(time.monotonic() - _t_si)
            outcome[dom] = (ok, detail)
            if not ok:
                pending.append((s, "nologin", {}, "no login is stored for this site and the "
                                "research browser holds no session for it"))
        if pending and _can_nag and not _halted():
            # 2FA / passkey / captcha: the stored login cannot finish the job — the user
            # can. Ask once, for all of them, instead of searching those sites logged out.
            progress("stage2", None, "Waiting for you to sign in to "
                     + ", ".join(p[0]["domain"] for p in pending) + "…")
            outcome.update(_manual_login_gate_multi(
                br, log, [(p[0]["domain"], p[3], p[2]) for p in pending], manual_login))
        for s in list(have_login):
            dom = s["domain"]
            if dom not in outcome:
                continue
            ok, detail = outcome[dom]
            if ok:
                signed_in.append(s)
                if dom not in result.logged_in:
                    result.logged_in.append(dom)
                log(f"[login] signed in: {dom} ({detail})")
            else:
                _record_login_warning(result, dom, detail, log)
                log(f"[login] could NOT sign in to {dom} — {detail}")
        for s in list(need_prompt):
            dom = s["domain"]
            ok, detail = outcome.get(dom, (False, "not attempted (the run was stopped)"))
            if ok:
                need_prompt.remove(s)
                have_login.append(s)
                signed_in.append(s)
                if dom not in result.logged_in:
                    result.logged_in.append(dom)
                log(f"[login] signed in: {dom} ({detail})")
                continue
            result.gated_candidates.setdefault(
                dom, "login-required site selected as relevant to this query")
            _alert(result, log, dom, "skipped",
                   "login-required and not signed in (" + detail + ") — it is NOT being searched "
                   "logged-in this run. Supply a login when prompted at the end of gathering, "
                   "or afterwards with 'Add/Fix Specific Sources'")

        # ── DEDICATED SITE SWEEPS — one agent per plan-selected site, started NOW so they
        # run alongside the Stage 2 lanes for the whole window (model latency, not the
        # browser, is what limits a run: the first real run read one page every 13 s).
        # Signed-in / stored-login sites first, then the public ones. 🔴 sites that are
        # still not signed in are not swept here — they go to the Stage 4 prompt.
        sweep_sites = (list(have_login) + [s for s in public_selected if s not in have_login]
                       ) if use_sites else []
        sweep_exec = None
        sweep_stop = _AnyEvent(stop_event, sweep_halt)

        def _sweep(sites, deadline, prog, prefix="stage3", skip=None):
            return _run_site_sweeps(
                client, br, result, seen_urls, sites, task=task, governance=governance,
                context_brief=stage1_brief, pool=pool, deadline=deadline,
                hard_deadline=deadline + 20, progress=prog, log=log, notes=notes,
                skip_event=skip, stop_event=sweep_stop, sources=sources, question=query,
                stage_prefix=prefix)

        if sweep_sites and not _halted():
            br.login_handler = _curated_login_handler(vault, result, log, "stage2")
            # Never less than a floor, even when Stage 1 ate most of a short window.
            _sw_deadline = max(gather_deadline, _now() + min(150, 45 + 20 * len(sweep_sites)))
            sweep_exec = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="drt-sweeps")
            # Reported under Stage 2 while the lanes are running, so the stage indicator
            # does not flicker between the two.
            sweep_fut = sweep_exec.submit(
                _sweep, sweep_sites, _sw_deadline,
                lambda st, pct, msg: progress("stage2", pct, msg))
        if use_engines or use_sites or use_neural:
            if use_neural:
                log("[plan] API discovery on for Stage 2: "
                    + " + ".join(p for p, on in (("Exa", exa_enabled()), ("Tavily", tavily_enabled())) if on))
            progress("stage2", None, f"{plan_msg} — searching the web in the research browser…")
            # Curated sites with stored logins get used in all cases; uncredentialed walls defer to Stage 4.
            br.login_handler = _curated_login_handler(vault, result, log, "stage2")
            # The plan decides whether to lean on the curated list; the relevance filter still
            # picks WHICH specific sites (by Description), kept signal-dense over the full table.
            if use_sites:
                # 🟢 public sites + any 🔴 site we are now signed into (the lanes mine
                # them in parallel; Stage 3 then does the dedicated sweep).
                seed_for_browser = public_selected + [s for s in signed_in
                                                      if s not in public_selected]
            else:
                seed_for_browser = []
                log("[plan] curated-site queries off for this run")
            focus = _stage2_focus(plan, use_sites, use_engines)
            if sweep_fut is not None:
                focus += (" DEDICATED SITE AGENTS are sweeping these sites through their own "
                          "search, in parallel with you, right now: "
                          + ", ".join(s["domain"] for s in sweep_sites)
                          + ". Do NOT spend your searches inside those sites — follow a link "
                          "into one when a trail leads there, but put your effort into the "
                          "open web, primary sources, and communities NOT on that list.")
            if use_neural:
                extras = []
                if tavily_enabled():
                    extras.append("web_search engine=\"tavily\" (an API search index — "
                                  "different coverage from the browser engines; keyword craft applies)")
                if exa_enabled():
                    extras.append("exa_search (NEURAL — describe the KIND of page in natural "
                                  "language; finds niche/long-tail pages keyword engines miss) and "
                                  "exa_find_similar (expand from one strong page)")
                focus += (" API DISCOVERY is available — " + "; ".join(extras) +
                          ". Use it to COMPLEMENT the browser engines, not duplicate them.")
            tools2 = _tool_defs(include_web_search=(use_engines or use_neural),
                                include_site_search=use_sites,
                                include_neural=(use_neural and exa_enabled()),
                                include_register_forum=True)
            # Stage 2's soft time share: ~60% of whatever gathering window remains. (The
            # last quarter used to be held back for the credentialed sweep; the per-site
            # sweeps now run alongside the lanes, so nothing needs reserving.)
            _win = max(0, gather_deadline - _now())
            stage2_hard = gather_deadline
            stage2_end = min(stage2_hard, _now() + 0.6 * _win)
            if stop_event is None or not stop_event.is_set():
                try:
                    _run_stage2_lanes(
                        client, br, result, seen_urls, query=query, task=task,
                        clarifications=clarifications, governance=governance,
                        listing=_listing(seed_for_browser), focus=focus, tools=tools2,
                        cap2=cap2, pool=pool, depth=depth, stage1_brief=stage1_brief,
                        stage2_end=stage2_end, gather_deadline=stage2_hard,
                        progress=progress, log=log, notes=notes, skip_event=skip_event,
                        stop_event=stop_event, sources=sources)
                except Exception as e:  # noqa: BLE001
                    msg = f"[stage2] aborted: {type(e).__name__}: {e}"
                    notes.append(msg)
                    log(msg + " — continuing to next stage")
        else:
            log("[plan] Stage 2 skipped — open engines, curated sites, and API discovery all off")


        # ── STAGE 3 — the dedicated site sweeps: wait for them, then a second pass for
        # any site that still has not been searched ──
        if not use_sites:
            log("[plan] Stage 3 (site sweeps) skipped — curated sites off")
        elif not sweep_sites and not need_prompt:
            log("[stage3] the plan selected no curated sites for this query; skipping")
        if need_prompt:
            log(f"[stage3] login-required, not signed in → one-time prompt (Stage 4): "
                f"{[s['domain'] for s in need_prompt]}")
        if sweep_fut is not None:
            if not sweep_fut.done():
                progress("stage3", None, "Finishing the dedicated site sweeps…")
            while True:
                try:
                    sweep_fut.result(timeout=1.0)
                    break
                except _cf.TimeoutError:
                    # "Skip stage" while only the sweeps are left ends them; STOP is
                    # already covered (sweep_stop watches stop_event).
                    if skip_event is not None and skip_event.is_set():
                        skip_event.clear()
                        sweep_halt.set()
                        log("[stage3] site sweeps ended early by user")
                except Exception as e:  # noqa: BLE001
                    msg = f"[stage3] site sweeps aborted: {type(e).__name__}: {e}"
                    notes.append(msg)
                    log(msg + " — continuing")
                    break
            sweep_exec.shutdown(wait=False)
            # SECOND PASS: a site whose agent never managed a single search (API error,
            # crash, queue) gets another go with guaranteed time, past the window if need
            # be. This is what makes "selected but never searched" a last resort.
            def _searched(dom):
                return any(r.get("domain") == dom and r.get("searches", 0) > 0
                           for r in result.site_sweeps)
            missed = [s for s in sweep_sites if not _searched(s["domain"])]
            if missed and not _halted() and not sweep_halt.is_set():
                d3 = max(gather_deadline, _now() + min(150, 45 + 20 * len(missed)))
                log(f"[stage3] {len(missed)} site(s) not searched in the parallel sweep — "
                    f"second pass, {int(d3 - _now())}s guaranteed: "
                    f"{', '.join(s['domain'] for s in missed)}")
                br.login_handler = _vault_handler(vault, result, log, "stage3",
                                                  ephemeral=ephemeral_creds)
                try:
                    _sweep(missed, d3, progress, skip=skip_event)
                except Exception as e:  # noqa: BLE001
                    msg = f"[stage3] second pass aborted: {type(e).__name__}: {e}"
                    notes.append(msg)
                    log(msg + " — continuing")
            for r in result.site_sweeps:
                log(f"[stage3] {r['domain']}{' (signed in)' if r.get('signed_in') else ''}: "
                    f"{r.get('searches', 0)} searches on its own search · "
                    f"{r.get('results', 0)} results · {r.get('pages', 0)} pages read")
        # A sign-in finished DURING the run (the tab was left open after the one-minute
        # wait) counts: withdraw that site's "login failed" and record the session.
        for w in list(result.login_warnings):
            dom = w.get("domain")
            try:
                now_in = bool(dom) and bool(br.is_signed_in(dom))
            except Exception:  # noqa: BLE001
                now_in = False
            if now_in:
                result.login_warnings.remove(w)
                if dom not in result.logged_in:
                    result.logged_in.append(dom)
                for r in result.site_sweeps:
                    if r.get("domain") == dom:
                        r["signed_in"] = True
                        r["note"] = ("signed in part-way through the run. " + str(r.get("note") or ""))[:400]
                _clear_alert(result, log, dom, "login_failed",
                             "you signed in during the run; later searches of it used the session")
                log(f"[login] signed in during the run: {dom}")

        # ── STAGE 4 — new gated sources (batched in-app login) ──
        if use_sites and result.gated_candidates and request_credentials:
            cands = [{"domain": d, "reason": r} for d, r in result.gated_candidates.items()]
            progress("stage4", None, f"Awaiting credentials for {len(cands)} gated source(s)…")
            provided = request_credentials(cands) or {}
            br.login_handler = _vault_handler(vault, result, log, "stage4", ephemeral=ephemeral_creds)
            for dom, creds in provided.items():
                if not creds or not (creds.get("username") or "").strip():
                    if dom not in result.skipped_gated:
                        result.skipped_gated.append(dom)
                    _alert(result, log, dom, "skipped",
                           "no login was supplied at the prompt — gated content was not read")
                    continue
                # ONE-TIME credentials: held in memory for this run only, NEVER written to the
                # vault. (The vault is populated separately + locally; hosted stays credential-free.)
                ephemeral_creds[dom] = {
                    "username": creds.get("username", ""), "password": creds.get("password", ""),
                    "login_url": creds.get("login_url", ""),
                }
                result.gated_candidates.pop(dom, None)
                progress("stage4", None, f"logging into {dom} and searching…")
                cap4 = {"searches": 2, "pages": 3, "max_turns": 12}
                _guarded_stage(
                    "stage4",
                    system=_stage_system("gated", governance,
                                         _listing([{"name": dom, "domain": dom, "type": "gated"}]),
                                         cap4, stage1_brief),
                    task=task, tools=_tool_defs(include_web_search=False),
                    cap=cap4, progress=progress, log=log, notes=notes, skip_event=skip_event,
                    question=query, sources=sources,
                    # Own small FIXED pool + a short grace window past the gathering
                    # deadline: credentials the user just typed in must stay usable
                    # even if stages 2-3 drained the clock and the pool.
                    pool={"searches": cap4["searches"], "pages": cap4["pages"]},
                    deadline=_now() + 60, hard_deadline=_now() + 90)
        elif result.gated_candidates:
            log(f"[stage4] {len(result.gated_candidates)} gated candidates but no credential prompt; skipping")
            for dom in list(result.gated_candidates):
                _alert(result, log, dom, "skipped",
                       "needs a login and there was no way to ask for one in this run")
    finally:
        # Never leave the site sweeps running against a browser that is about to be
        # closed or handed back (an exception above can skip the normal wait).
        if sweep_fut is not None and not sweep_fut.done():
            sweep_halt.set()
            try:
                sweep_fut.result(timeout=90)
            except Exception:  # noqa: BLE001
                pass
        if own_browser:
            br.close()

    # Anything the plan selected that was never searched or read → say so, loudly.
    touched = {normalize_domain(d) for d in result.curated_searched}
    touched |= {normalize_domain(host_of(it.url)) for it in result.items if it.via != "stage1"}
    touched.discard("")
    _halted = stop_event is not None and stop_event.is_set()
    for s in result.planned_sites:
        d = s["domain"]
        if not any(t == d or t.endswith("." + d) or d.endswith("." + t) for t in touched):
            _alert(result, log, d, "skipped",
                   "selected by the research plan but never searched or read this run ("
                   + ("the run was stopped first" if _halted else
                      "the agents spent the window on other sources") + ")")

    result.agent_notes = "\n\n".join(notes)
    if not result.stopped_reason:
        result.stopped_reason = "pipeline complete"
    log(f"[pipeline] done: {len(result.items)} items · {result.searches_used} searches · "
        f"{result.pages_used} pages · gated_candidates={list(result.gated_candidates)} · "
        f"logged_in={result.logged_in} · "
        f"discovered_forums={[f['domain'] for f in result.discovered_forums]}")
    return result


def run_gap_round(client, browser, harvest, gaps, governance=None, sources=None,
                  progress=None, log=None, skip_event=None, cap=None, deadline=None,
                  stop_event=None):
    """One light browser (engines) pass to fill specific gaps — for the evolving-report
    deepening loop. Appends new pages to `harvest` (mutates it) and returns the count
    of new items added. Reuses the already-open browser passed in.

    deadline: absolute epoch seconds this round may run until (the job layer passes the
    run's synthesis-reserve boundary). Without it a request_extension here could only
    grant units, never time — the "+0s" the 2026-09-03 trace showed."""
    import anthropic  # noqa: F401 (client is passed in; import kept for parity)
    log = log or (lambda m: None)
    progress = progress or (lambda *a, **k: None)
    governance = governance if governance is not None else _load_governance()
    sources = sources if sources is not None else load_sources()
    seen = {_canon_url(it.url) for it in harvest.items}
    depth = getattr(harvest, "depth", "standard")
    if cap is None:
        cap = ({"searches": len(gaps) + 6, "pages": 14, "max_turns": 40} if depth == "exhaustive"
               else {"searches": len(gaps) + 2, "pages": 6, "max_turns": 20})
    # Gap rounds run on their own small pool (the run's global pool isn't threaded
    # through synthesize) — sized with one extension worth of headroom so a hot trail
    # can still request_extension once. Modest by design (larger on the 1-hour tier).
    pool = {"searches": cap["searches"] + 4, "pages": cap["pages"] + 6}
    use_neural = bool(getattr(harvest, "plan", {}).get("neural_search", {}).get("use")) \
        and exa_enabled()
    browser.login_handler = _record_skip_handler(harvest, log)
    gap_text = "\n".join(gaps)
    seed_for_browser = _select_relevant_seed_sources(client, gap_text, "", sources, log)
    task = ("Fill these specific gaps with targeted web searches, open the best results, "
            "then finish:\n" + "\n".join(f"- {g}" for g in gaps))
    before = len(harvest.items)
    # Soft share for this round: up to 4 min (12 on exhaustive), never past the deadline.
    soft = _now() + (720 if depth == "exhaustive" else 240)
    round_deadline = min(deadline, soft) if deadline else None
    try:
        _run_browser_stage(
            client, browser, harvest, seen, stage_name="synthesize",
            system=_stage_system("engines", governance, _listing(seed_for_browser), cap, "",
                                 pool=pool),
            task=task, tools=_tool_defs(include_web_search=True, include_neural=use_neural,
                                        include_register_forum=True),
            cap=cap, progress=progress, log=log, notes=[], skip_event=skip_event,
            question="\n".join(gaps), sources=sources, pool=pool,
            deadline=round_deadline, hard_deadline=deadline, stop_event=stop_event)
    except Exception as e:  # noqa: BLE001
        log(f"[deepen] gap round failed: {type(e).__name__}: {e}")
    return len(harvest.items) - before


def run_focus_sources(client, browser, harvest, domains, vault=None, ephemeral=None,
                      governance=None, sources=None, prior_report="", progress=None,
                      log=None, skip_event=None, stop_event=None, seconds=None,
                      manual_login=None):
    """"Add/Fix Specific Sources": a NARROW pass over a handful of named sites — the ones
    a finished run could not get into or skipped — after the user has supplied logins.
    No new plan, no baseline sweep, no lanes: sign in to each site, then one sequential
    sweep of ONLY those sites with the existing report as context. New pages are appended
    to `harvest` (mutated); the caller re-synthesizes. Returns the number of pages added."""
    from .login import CredentialVault
    log = log or (lambda m: None)
    progress = progress or (lambda *a, **k: None)
    governance = governance if governance is not None else _load_governance()
    sources = sources if sources is not None else load_sources()
    vault = vault or CredentialVault()
    ephemeral = ephemeral or {}
    doms = []
    for d in domains or []:
        nd = normalize_domain(d)
        if nd and nd not in doms:
            doms.append(nd)
    if not doms:
        return 0

    def _creds(dom):
        return ephemeral.get(dom) or (vault.get(dom) if vault else None) or {}

    harvest.planned_sites = [{"domain": d, "login_required": True,
                              "credentialed": bool((_creds(d).get("username") or "").strip())}
                             for d in doms]
    harvest.source_alerts = []          # this pass reports on ITS sites only
    harvest.native_stats = {}
    harvest.login_warnings = []
    harvest.skipped_gated = []
    harvest.gated_candidates = {}
    before = len(harvest.items)
    seen = {_canon_url(it.url) for it in harvest.items}
    budget_s = seconds or min(600, 150 + 90 * len(doms))
    deadline = _now() + budget_s
    log(f"[plan] focused source pass: {', '.join(doms)} · {budget_s}s window")

    _can_nag = manual_login is not None and getattr(browser, "headed", False)
    for dom in doms:
        if stop_event is not None and stop_event.is_set():
            break
        c = _creds(dom)
        has = bool((c.get("username") or "").strip())
        progress("stage3", None, f"Signing in to {dom}…")
        try:
            ok, detail = browser.ensure_logged_in(dom, c if has else {})
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"login error: {type(e).__name__}"
        if not ok and _can_nag and has:
            # A login was supplied and still did not get in (2FA / captcha) → ask the user
            # to finish it by hand rather than scanning the site logged out.
            progress("stage3", None, f"Waiting for you to sign in to {dom}…")
            ok, detail = _manual_login_gate(browser, log, dom, detail, manual_login, c)
        if ok:
            if dom not in harvest.logged_in:
                harvest.logged_in.append(dom)
            log(f"[login] signed in: {dom} ({detail})")
        elif has:
            _record_login_warning(harvest, dom, detail, log)
            log(f"[login] could NOT sign in to {dom} — {detail}")
        else:
            log(f"[login] {dom}: no login supplied and no session in the browser — "
                f"scanning it logged out")

    listing = _listing([{"name": d, "domain": d, "type": "forum"} for d in doms])
    cap = {"searches": 6 * len(doms) + 4, "pages": 10 * len(doms) + 6, "max_turns": 60}
    pool = {"searches": cap["searches"] + 6, "pages": cap["pages"] + 10}
    task = (f"USER RESEARCH QUESTION:\n{harvest.query}\n\n"
            "FOCUSED SOURCE PASS. A full research run on this question is already finished "
            "(its report is below). Your ONLY job now is to scour the specific sources listed "
            "in scope — they were skipped, walled, or unreadable in that run and have just "
            "been fixed — for material that ADDS TO, CORRECTS, or CONTRADICTS the report. "
            "Use each site's own search (forum_search) with several phrasings, open the "
            "strongest threads, read them through (NEXT PAGE links, open_page offset), and "
            "follow in-site links. Do not re-research what the report already establishes "
            "and do not wander to other sites. Finish when these sources are exhausted.\n\n"
            f"EXISTING REPORT (context — do not repeat it):\n{(prior_report or '')[:6000]}")
    browser.login_handler = _vault_handler(vault, harvest, log, "focus", ephemeral=ephemeral)
    try:
        _run_browser_stage(
            client, browser, harvest, seen, stage_name="focus",
            system=_stage_system("credentialed", governance, listing, cap, "", pool=pool),
            task=task, tools=_tool_defs(include_web_search=False), cap=cap,
            progress=progress, log=log, notes=[], skip_event=skip_event,
            question=harvest.query, sources=sources, pool=pool,
            deadline=deadline, hard_deadline=deadline + 120, stop_event=stop_event)
    except Exception as e:  # noqa: BLE001
        log(f"[focus] source pass failed: {type(e).__name__}: {e}")

    touched = {normalize_domain(d) for d in harvest.curated_searched}
    touched |= {normalize_domain(host_of(it.url)) for it in harvest.items[before:]}
    for d in doms:
        if not any(t and (t == d or t.endswith("." + d) or d.endswith("." + t)) for t in touched):
            _alert(harvest, log, d, "skipped",
                   "the focused pass ended without searching or reading this source")
    added = len(harvest.items) - before
    log(f"[pipeline] focused pass done: {added} new page(s) from {', '.join(doms)}")
    return added


# Manual smoke test:  python -m engines.research.agent "your question" [depth]
if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Background, track record, and any controversies around OpenAI"
    d = sys.argv[2] if len(sys.argv) > 2 else "quick"
    # stub credential prompt: skip all gated sources (no in-app UI here)
    def _stub_creds(cands):
        print(f"[stub] would prompt in-app for: {[c['domain'] for c in cands]} — skipping all")
        return {}
    out = run_search(q, depth=d, log=lambda m: print(m, flush=True),
                     request_credentials=_stub_creds)
    print("\n================ HARVEST ================")
    print(f"query={out.query!r} depth={out.depth} searches={out.searches_used} pages={out.pages_used}")
    print(f"stopped: {out.stopped_reason}")
    print(f"gated_candidates: {out.gated_candidates}")
    print(f"logged_in: {out.logged_in} · skipped_gated: {out.skipped_gated}")
    print(f"discovered_forums: {out.discovered_forums}")
    for i, it in enumerate(out.items, 1):
        print(f"\n[{i}] ({it.via}/{it.source_type}) {it.title[:75]}\n    {it.url}\n    {len(it.text)} chars")
    print("\n--- agent notes (first 1200) ---\n" + out.agent_notes[:1200])
