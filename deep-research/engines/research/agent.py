"""
DRT search driver — deterministic 4-stage pipeline (the "brain").

The order of operations is fixed in Python (not left to the model) so the search
always proceeds cheap→expensive, open→gated:

  Stage 1  Broad baseline sweep — wide, shallow engine searches in the SAME visible
           Chrome (no page opens), semantically reranked → a terse baseline brief.
  Stage 2  Browser search engines (DuckDuckGo/Brave/Google) in visible Chrome —
           targets the gaps Stage 1 left, deep-reads, mines the open forums.
  Stage 3  Already-credentialed gated sources (vault has creds) — searched directly.
  Stage 4  New login-required sources worth chasing — collected during 2–3, then the
           APP prompts for credentials (batched), saves them, auto-logs-in, harvests.
           Anything needing more than user/pass (2FA/captcha) is skipped + noted.

Within each browser stage a single Claude tool-use loop drives the browser.
ALL web searching happens through the real Chrome instance — no server-side /
sandboxed search agents (those respect robots.txt and get blocked; Chrome does
not). SIGNAL-OR-NOTHING throughout: a near-empty harvest is a valid result.
This module is ONLY search/gather — storage, evaluation, synthesis come later.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict

from .browser import DRTBrowser, _JUNK_HOST
from .exa_search import exa_search, exa_find_similar, exa_contents, is_enabled as exa_enabled
from .login import normalize_domain
from .models import get_model

# ── depth tiers — TIME-boxed: each tier maximizes the outcome within a wall-clock
# window rather than a unit budget. `seconds` = the whole run's target duration;
# `reserve` = time held back for extraction + synthesis + the go-deeper assessment;
# the remainder is the gathering window. The search/page pools remain as generous
# safety ceilings (runaway protection), but TIME is the governor.
DEPTH_BUDGETS = {
    "standard": {"seconds": 300, "reserve": 100, "searches": 120, "pages": 150, "max_turns": 120},
    "deep":     {"seconds": 600, "reserve": 130, "searches": 240, "pages": 300, "max_turns": 240},
}
_LEGACY_DEPTHS = {"quick": "standard", "verydeep": "deep"}   # old tier names → nearest new tier


def normalize_depth(depth: str) -> str:
    d = _LEGACY_DEPTHS.get((depth or "").strip().lower(), (depth or "").strip().lower())
    return d if d in DEPTH_BUDGETS else "standard"

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


def load_sources(path: str = _SOURCES_PATH) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [s for s in data.get("sources", []) if s.get("enabled", True)]
    except Exception:
        return []


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
        tools.append({
            "name": "web_search",
            "description": ("Run a query on a general search engine and get ranked organic "
                            "results (title + url). Use COMPACT KEYWORD queries (2-6 terms — "
                            "proper nouns, model names, insider jargon), never full-sentence "
                            "questions: engines rank short keyword strings far better. Vary "
                            "queries and engines for broad coverage."),
            "input_schema": {"type": "object", "properties": {
                "engine": {"type": "string", "enum": ["duckduckgo", "brave", "google"]},
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
            "description": ("Search WITHIN a specific domain (forum/Substack/site) via the site: "
                            "operator. Compact keyword queries here too — 2-5 terms, no question "
                            "phrasing. Use for the listed sources and any relevant site you discover. "
                            "Prefer forum_search when the site may hold unindexed or gated material."),
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
            "source_type": {"type": "string", "description": "web|forum|substack|news|primary|gated"}},
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
                    "visible Chrome. Build on the Stage-1 brief: go after the GAPS, and after "
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
You operate a real, visible Chrome browser; opened pages are read as text automatically.
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


# ── login handlers (per stage) ────────────────────────────────
def _record_login_warning(result, domain, detail):
    """Record a user-facing warning that a stored login could not be completed."""
    if not any(w.get("domain") == domain for w in result.login_warnings):
        result.login_warnings.append({"domain": domain, "detail": detail})


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
                                  "stored login did not go through (wrong password, 2FA, or captcha)")
            log(f"[{label}] {domain}: stored login failed → warning")
            return False
        result.gated_candidates.setdefault(domain, "login/paywall hit during open-web search")
        log(f"[{label}] gated {domain} → recorded for Stage 4; skipping")
        return False
    return h


def _vault_handler(vault, result, log, label):
    """Stages 3/4: try stored creds; success → True; otherwise record + skip (no stall, no 2FA)."""
    from .login import try_autofill

    def h(domain, page):
        creds = vault.get(domain) if vault else None
        if creds and try_autofill(page, creds, log):
            if domain not in result.logged_in:
                result.logged_in.append(domain)
            return True
        if creds:   # had stored creds but they didn't get us in → warn the user
            _record_login_warning(result, domain,
                                  "stored login did not go through (wrong password, 2FA, or captcha)")
        result.gated_candidates.setdefault(domain, "needs login; stored credentials did not get in")
        if domain not in result.skipped_gated:
            result.skipped_gated.append(domain)
        log(f"[{label}] {domain}: vault login absent/failed → skip (no 2FA/manual)")
        return False
    return h


def _page_links_section(links, page_url, seen_urls, limit: int = 20) -> str:
    """Format an opened page's followable links for the agent — the crawling surface.
    Filters already-seen URLs, junk hosts, and thin anchors; substantive anchors first."""
    picked, seen = [], set()
    base = (page_url or "").split("#")[0]
    for l in links or []:
        u = (l.get("url") or "").split("#")[0]
        t = re.sub(r"\s+", " ", (l.get("text") or "")).strip()
        if not u or u == base or u in seen or u in seen_urls:
            continue
        if len(t) < 4 or _JUNK_HOST.search(u):
            continue
        seen.add(u)
        picked.append((t, u))
    if not picked:
        return ""
    picked.sort(key=lambda p: -min(len(p[0]), 60))   # substantive anchor text first
    lines = "\n".join(f"L{i}. {t[:110]} — {u}" for i, (t, u) in enumerate(picked[:limit], 1))
    return ("\n\nLINKS ON THIS PAGE:\n" + lines +
            "\nFollow any of these with open_page if they smell like the trail — thread "
            "replies, citations, pagination, an author's other posts.")


# ── one browser stage (shared loop) ───────────────────────────
def _run_browser_stage(client, br, result, seen_urls, *, stage_name, system, task,
                       tools, cap, progress, log, notes, skip_event=None, question="",
                       sources=None, pool=None, deadline=None, hard_deadline=None,
                       stop_event=None):
    # Soft stage allocation (grows via request_extension) vs. the hard global pool.
    # No pool passed → private pool equal to the allocation (fixed-cap behavior).
    # `deadline` = this stage's soft time share; `hard_deadline` = the run's gathering
    # window (extensions can push the stage deadline up to it, never past it).
    alloc = {"searches": cap["searches"], "pages": cap["pages"]}
    used = {"searches": 0, "pages": 0}
    dl = {"t": deadline}                       # mutable so request_extension can extend
    hard_deadline = hard_deadline or deadline

    def _left():
        if dl["t"] is None:
            return None
        return max(0, int(dl["t"] - time.time()))

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
        if dl["t"] is not None and time.time() > dl["t"]:
            if hard_deadline is not None and dl["t"] < hard_deadline - 5:
                return ("STAGE TIME SHARE SPENT. If you are on a genuinely HOT TRAIL toward "
                        "the user's specific question, call request_extension(reason) for "
                        "more time; otherwise finish() now.")
            return ("TIME WINDOW CLOSED — the research clock is spent. Call finish() NOW "
                    "with what you have.")
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

    def dispatch(name, inp):
        if name == "finish":
            if not result.stopped_reason:
                result.stopped_reason = inp.get("reason", "")
            return "Stage complete.", True
        if name == "request_extension":
            reason = (inp.get("reason") or "").strip()
            gs = min(4, max(0, pool["searches"]))     # grant capped by pool remainder
            gp = min(6, max(0, pool["pages"]))
            # Time grant: push the stage deadline up to +60s, never past the hard window.
            gt = 0
            if dl["t"] is not None and hard_deadline is not None:
                new_t = min(hard_deadline, max(dl["t"], time.time()) + 60)
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
            known = {normalize_domain(s.get("domain", "")) for s in srcs}
            if dom in known or any(f.get("domain") == dom for f in result.discovered_forums):
                return f"{dom} is already known — forum_search/site_search it directly.", False
            result.discovered_forums.append({"domain": dom, "reason": reason})
            log(f"[forum] discovered: {dom} — {reason}")
            return (f"Registered {dom} — you can forum_search/site_search it now; it will "
                    f"be surfaced to the user to add permanently.", False)
        if name in ("web_search", "site_search", "forum_search"):
            denied = _consume("searches")
            if denied:
                return denied, False
            try:
                if name == "web_search":
                    res = br.search(inp["engine"], inp["query"], limit=10)
                    via = f"{inp['engine']}: {inp['query']}"
                else:
                    dom = normalize_domain(inp.get("domain", "")) or \
                          (inp.get("domain") or "").strip().lower()
                    if name == "forum_search":
                        # The site's OWN search — platform adapter (Discourse/XenForo/Reddit/…)
                        # or a registered {q} template, run in the logged-in browser so it
                        # reaches unindexed/gated threads. Falls back to the site: engine when
                        # native search yields nothing (JS-blocked, rate-limited, exotic CMS).
                        surl = next((str(s.get("search_url") or "").strip() for s in srcs
                                     if normalize_domain(s.get("domain", "")) == dom), "")
                        res = br.native_search(dom, inp["query"], search_url=surl, limit=10)
                        if res:
                            via = f"native:{dom} {inp['query']}"
                        else:
                            res = br.site_search(inp["query"], dom, limit=10)
                            via = f"site:{dom} {inp['query']} (native empty — engine fallback)"
                    else:
                        res = br.site_search(inp["query"], dom, limit=10)
                        via = f"site:{dom} {inp['query']}"
                    if dom and dom not in result.curated_searched:
                        result.curated_searched.append(dom)
            except Exception as e:  # noqa: BLE001
                return f"Search error: {type(e).__name__}: {e}", False
            if name == "forum_search":
                log(f"[forum] {via} -> {len(res)} results")
            progress(stage_name, None, f"searched — {via}")
            fresh = [r for r in res if r.url not in seen_urls]
            for r in fresh[:3]:       # feed: top hits cascading by
                log(f"[hit] {(r.title or r.url)[:80]} → {r.url[:90]}")
            lines = [f"{i+1}. {r.title[:90]}\n   {r.url}" for i, r in enumerate(fresh)]
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
                    result.exa_searches += 1
                else:
                    hits = exa_find_similar(inp.get("url", ""), num=10, log=log)
                    via = f"exa~similar: {inp.get('url', '')[:60]}"
                    result.exa_similar += 1
            except Exception as e:  # noqa: BLE001
                return f"Exa error: {type(e).__name__}: {e}", False
            log(f"[search] {via}")
            progress(stage_name, None, f"searched — {via}")
            lines = []
            for hit in hits:
                u = hit.get("url", "")
                if not u:
                    continue
                if u not in result.exa_urls:
                    result.exa_urls.append(u)
                if u not in seen_urls:
                    lines.append(f"{len(lines)+1}. {hit.get('title', '')[:90]}\n   {u}")
            body = "\n".join(lines) if lines else "(no new results)"
            return f"Results for [{via}]:\n{body}\n\n{_budget()}", False
        if name == "open_page":
            url = inp["url"]
            if url in seen_urls:
                return "Already opened this URL. Skip it.", False
            denied = _consume("pages")
            if denied:
                return denied, False
            seen_urls.add(url)
            progress(stage_name, None, f"reading — {url[:70]}")
            pc = br.open(url)     # br logs the "[open] …" feed line itself
            if pc.error:
                # Resilience: Chrome couldn't fetch (403 / wall / timeout). Fall back to
                # Exa's cleaned contents so the page text isn't lost. Only when Exa is on.
                fb = exa_contents(url, log=log) if exa_enabled() else ""
                if fb:
                    result.items.append(HarvestItem(
                        url=url, title=inp.get("title") or url, text=fb,
                        source_type=inp.get("source_type", "web"), via=f"{stage_name}+exa",
                        retrieved_at=time.time()))
                    if url not in result.exa_urls:
                        result.exa_urls.append(url)
                    shown = fb[:_PAGE_TEXT_TO_AGENT]
                    tail = "" if len(fb) <= _PAGE_TEXT_TO_AGENT else \
                           f"\n…[+{len(fb) - _PAGE_TEXT_TO_AGENT} more chars stored]"
                    return (f"Opened (Chrome failed: {pc.error} — recovered via Exa):\n"
                            f"URL: {url}\n\n{shown}{tail}\n\n"
                            f"[harvested {len(result.items)} total] {_budget()}", False)
                return f"Could not open ({pc.error}).", False
            # Store the RAW page text — goal-based extraction + junk filtering happens
            # post-hoc in synthesize.py (Pass A), which needs the full page.
            result.items.append(HarvestItem(
                url=pc.url, title=pc.title, text=pc.text,
                source_type=inp.get("source_type", "web"), via=stage_name,
                used_screenshot=pc.used_screenshot, retrieved_at=time.time()))
            shown = pc.text[:_PAGE_TEXT_TO_AGENT]
            tail = "" if len(pc.text) <= _PAGE_TEXT_TO_AGENT else \
                   f"\n…[+{len(pc.text)-_PAGE_TEXT_TO_AGENT} more chars stored]"
            note = " (thin → screenshot)" if pc.used_screenshot else ""
            links_sec = _page_links_section(pc.links, pc.url, seen_urls)
            return (f"Opened: {pc.title}{note}\nURL: {pc.url}\n\n{shown}{tail}{links_sec}\n\n"
                    f"[harvested {len(result.items)} total] {_budget()}", False)
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
        if stop_event is not None and stop_event.is_set():
            notes.append(f"[{stage_name}] stopped by user.")
            log(f"[stop] {stage_name}: halted by user — preserving harvest")
            break
        # User asked to end this stage early → stop, keep what's harvested, roll forward.
        if skip_event is not None and skip_event.is_set():
            skip_event.clear()
            notes.append(f"[{stage_name}] ended early by user.")
            log(f"[{stage_name}] skipped by user")
            break
        # Time governor: when the (possibly extended) stage window is gone, move on.
        if dl["t"] is not None and time.time() > max(dl["t"], hard_deadline or dl["t"]) :
            notes.append(f"[{stage_name}] time window closed.")
            log(f"[{stage_name}] time window closed — moving on")
            break
        log(f"[turn] {stage_name}: agent weighing next move…{_clock()}")
        resp = client.messages.create(model=_MODEL, max_tokens=4096, system=system,
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
    return out


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
        "Respond with ONLY JSON: {\"category\":\"..\",\"rationale\":\"one sentence why\","
        "\"api_search\":{\"use\":true,\"weight\":0.3},"
        "\"web_engines\":{\"use\":true,\"weight\":0.3},"
        "\"site_queries\":{\"use\":true,\"weight\":0.4,\"emphasis\":[\"forum\"]}}")
    msg = f"QUESTION:\n{query}"
    if clarifications:
        msg += f"\n\nCONTEXT:\n{clarifications[:800]}"
    try:
        r = client.messages.create(model=get_model("plan"), max_tokens=400, system=sys_p,
                                   messages=[{"role": "user", "content": msg}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return _default_plan("planner returned no JSON")
        plan = _normalize_plan(json.loads(m.group(0)), cats)
        plan["_planned"] = True
        log(f"[plan] category={plan['category']} · "
            f"api={plan['api_search']} web={plan['web_engines']} site={plan['site_queries']} "
            f"— {plan.get('rationale', '')[:90]}")
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
_ALWAYS_ON_DOMAINS = {"reddit.com", "substack.com"}
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


# ── orchestrator ──────────────────────────────────────────────
def run_local_baseline(browser, client, query, clarifications="", depth="standard",
                       log=None, stop_event=None) -> dict:
    """Stage 1 for ALL providers: the broad baseline browser sweep. The model generates
    a few BROAD queries, runs them through the browser search engines (real Chrome —
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
    for i, q in enumerate(queries):
        if stop_event is not None and stop_event.is_set():
            log("[stop] baseline sweep halted by user")
            break
        engine = "brave" if (i % 2) else "duckduckgo"
        try:
            results = browser.search(engine, q, limit=8)
        except Exception as e:  # noqa: BLE001
            log(f"[local-baseline] {engine} search failed ({q!r}): {e}")
            continue
        for rslt in results:
            url = (getattr(rslt, "url", "") or "").strip()
            if not url or url in seen or any(b and b in url for b in blocked):
                continue
            seen.add(url)
            pool.append({"url": url, "title": getattr(rslt, "title", "") or url,
                         "snippet": getattr(rslt, "snippet", "") or ""})
        _trace(f"[local-baseline] {engine}: {q!r} → {len(results)} results "
               f"({len(pool)} unique so far)")
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
               deadline=None, stop_event=None) -> HarvestResult:
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
    if not exa_enabled():
        if plan.get("neural_search", {}).get("use"):
            log("[plan] neural search requested but Exa not configured (DRT_EXA/EXA_API_KEY) — off")
        plan["neural_search"] = {"use": False, "_unavailable": True}
    result.plan = plan
    result.category = plan.get("category", "") or ""
    _trace(f"\n=== PLAN (generated by {provider}) ===\n{json.dumps(plan, indent=2, ensure_ascii=False)}")
    plan_msg = _plan_summary_msg(plan)

    # TIME is the governor: the gathering window ends at `gather_deadline` (the tier's
    # total minus the synthesis reserve). Stage time shares are soft; request_extension
    # pushes a hot stage toward the hard window. The unit pools below are safety ceilings.
    gather_deadline = deadline or (time.time() + max(60, b["seconds"] - b["reserve"]))
    log(f"[plan] research window: {max(0, int(gather_deadline - time.time()))}s of gathering "
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

    try:
        # ── STAGE 1 — broad baseline browser sweep (ALL providers) ──
        # Runs in the same visible Chrome as every other stage. The old Anthropic
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
                retrieved_at=time.time()))
        result.stage1_sources = s1.get("sources", [])
        stage1_brief = s1.get("findings_md", "")

        # ── STAGE 2 — browser engines + curated-site queries (plan-biased) ──
        site_ch = plan.get("site_queries", {})
        web_ch = plan.get("web_engines", {})
        use_sites = site_ch.get("use", True)        # hard on/off (planner skip OR user toggle)
        use_engines = web_ch.get("use", True)
        use_neural = False   # neural/Exa search removed as an option (2026-08-29)
        if use_engines or use_sites or use_neural:
            if use_neural:
                log("[plan] neural search (Exa) enabled for Stage 2")
            progress("stage2", None, f"{plan_msg} — searching the web in Chrome…")
            # Curated sites with stored logins get used in all cases; uncredentialed walls defer to Stage 4.
            br.login_handler = _curated_login_handler(vault, result, log, "stage2")
            # The plan decides whether to lean on the curated list; the relevance filter still
            # picks WHICH specific sites (by Description), kept signal-dense over the full table.
            if use_sites:
                seed_for_browser = _select_relevant_seed_sources(
                    client, query, clarifications, sources, log, emphasis=site_ch.get("emphasis"))
            else:
                seed_for_browser = []
                log("[plan] curated-site queries off for this run")
            focus = _stage2_focus(plan, use_sites, use_engines)
            if use_neural:
                focus += (" Exa NEURAL search (exa_search) and find-similar (exa_find_similar) are "
                          "available — use them for niche/long-tail discovery and to expand from a "
                          "strong source, COMPLEMENTING (not duplicating) keyword web_search.")
            _guarded_stage(
                "stage2",
                system=_stage_system("engines", governance, _listing(seed_for_browser), cap2,
                                      stage1_brief, focus_note=focus, pool=pool),
                task=task,
                tools=_tool_defs(include_web_search=use_engines, include_site_search=use_sites,
                                 include_neural=use_neural, include_register_forum=True),
                cap=cap2, progress=progress, log=log, notes=notes, skip_event=skip_event,
                sources=sources, pool=pool,
                # Stage 2's soft time share: ~60% of whatever gathering window remains.
                deadline=min(gather_deadline,
                             time.time() + 0.6 * max(0, gather_deadline - time.time())),
                hard_deadline=gather_deadline)
        else:
            log("[plan] Stage 2 skipped — open engines, curated sites, and neural all off (API-only run)")

        # ── STAGE 3 — already-credentialed sources (topically filtered) ──
        # Credentialed sources ARE site queries — gated by the curated-sites channel.
        credentialed = _credentialed_sources(sources, vault) if use_sites else []
        if not use_sites:
            log("[plan] Stage 3 (credentialed sites) skipped — curated sites off")
        if credentialed:
            relevant = _select_relevant_credentialed(client, query, clarifications, credentialed, log)
            if relevant:
                doms = [s["domain"] for s in relevant]
                # ── Proactive login: use the stored credentials for every relevant credentialed
                # site up front (not only when a wall is hit). Warn on any that fail. Sites with no
                # stored login_url fall back to the reactive handler set below.
                progress("stage3", None, f"Preparing credentialed sources: {', '.join(doms)}")
                for dom in doms:
                    creds = vault.get(dom) if vault else None
                    # Always check — ensure_logged_in is cheap (verifies the profile's auth
                    # cookie first, no navigation) and honest about the outcome.
                    try:
                        ok, detail = br.ensure_logged_in(dom, creds or {})
                    except Exception as e:  # noqa: BLE001
                        ok, detail = False, f"login error: {type(e).__name__}"
                    if ok:
                        if dom not in result.logged_in:
                            result.logged_in.append(dom)
                        log(f"[stage3] logged in: {dom} ({detail})")
                    elif "public content only" in detail:
                        # Not a failure — many forums are fully searchable logged out, and a
                        # real wall later still triggers the reactive vault login handler.
                        log(f"[stage3] {dom}: searching public content (not logged in) — "
                            f"reactive login will fire on any wall")
                    else:
                        # A genuine login attempt that couldn't complete — worth surfacing.
                        _record_login_warning(result, dom, detail)
                        log(f"[stage3] login could not be completed: {dom} — {detail}")
                progress("stage3", None, f"Searching credentialed sources: {', '.join(doms)}")
                br.login_handler = _vault_handler(vault, result, log, "stage3")
                _guarded_stage(
                    "stage3",
                    system=_stage_system("credentialed", governance, _listing(relevant), cap3,
                                         stage1_brief, pool=pool),
                    task=task, tools=_tool_defs(include_web_search=False), cap=cap3,
                    progress=progress, log=log, notes=notes, skip_event=skip_event,
                    question=query, sources=sources, pool=pool,
                    deadline=gather_deadline, hard_deadline=gather_deadline)
            else:
                log("[stage3] no credentialed sources topically relevant to this query; skipping")
        else:
            log("[stage3] no credentialed sources in vault; skipping")

        # ── STAGE 4 — new gated sources (batched in-app login) ──
        if use_sites and result.gated_candidates and request_credentials:
            cands = [{"domain": d, "reason": r} for d, r in result.gated_candidates.items()]
            progress("stage4", None, f"Awaiting credentials for {len(cands)} gated source(s)…")
            provided = request_credentials(cands) or {}
            br.login_handler = _vault_handler(vault, result, log, "stage4")
            for dom, creds in provided.items():
                if not creds or not (creds.get("username") or "").strip():
                    if dom not in result.skipped_gated:
                        result.skipped_gated.append(dom)
                    continue
                vault.set(dom, creds.get("username", ""), creds.get("password", ""),
                          login_url=creds.get("login_url", ""))
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
                    deadline=time.time() + 60, hard_deadline=time.time() + 90)
        elif result.gated_candidates:
            log(f"[stage4] {len(result.gated_candidates)} gated candidates but no credential prompt; skipping")
    finally:
        if own_browser:
            br.close()

    result.agent_notes = "\n\n".join(notes)
    if not result.stopped_reason:
        result.stopped_reason = "pipeline complete"
    log(f"[pipeline] done: {len(result.items)} items · {result.searches_used} searches · "
        f"{result.pages_used} pages · gated_candidates={list(result.gated_candidates)} · "
        f"logged_in={result.logged_in} · "
        f"discovered_forums={[f['domain'] for f in result.discovered_forums]}")
    return result


def run_gap_round(client, browser, harvest, gaps, governance=None, sources=None,
                  progress=None, log=None, skip_event=None, cap=None):
    """One light browser (engines) pass to fill specific gaps — for the evolving-report
    deepening loop. Appends new pages to `harvest` (mutates it) and returns the count
    of new items added. Reuses the already-open browser passed in."""
    import anthropic  # noqa: F401 (client is passed in; import kept for parity)
    log = log or (lambda m: None)
    progress = progress or (lambda *a, **k: None)
    governance = governance if governance is not None else _load_governance()
    sources = sources if sources is not None else load_sources()
    seen = {it.url for it in harvest.items}
    cap = cap or {"searches": len(gaps) + 2, "pages": 6, "max_turns": 20}
    # Gap rounds run on their own small pool (the run's global pool isn't threaded
    # through synthesize) — sized with one extension worth of headroom so a hot trail
    # can still request_extension once. Modest by design.
    pool = {"searches": cap["searches"] + 4, "pages": cap["pages"] + 6}
    use_neural = False   # neural/Exa search removed as an option (2026-08-29)
    browser.login_handler = _record_skip_handler(harvest, log)
    gap_text = "\n".join(gaps)
    seed_for_browser = _select_relevant_seed_sources(client, gap_text, "", sources, log)
    task = ("Fill these specific gaps with targeted web searches, open the best results, "
            "then finish:\n" + "\n".join(f"- {g}" for g in gaps))
    before = len(harvest.items)
    try:
        _run_browser_stage(
            client, browser, harvest, seen, stage_name="synthesize",
            system=_stage_system("engines", governance, _listing(seed_for_browser), cap, "",
                                 pool=pool),
            task=task, tools=_tool_defs(include_web_search=True, include_neural=use_neural,
                                        include_register_forum=True),
            cap=cap, progress=progress, log=log, notes=[], skip_event=skip_event,
            question="\n".join(gaps), sources=sources, pool=pool)
    except Exception as e:  # noqa: BLE001
        log(f"[deepen] gap round failed: {type(e).__name__}: {e}")
    return len(harvest.items) - before


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
