> **2026-11-12 - BRIGHT DATA IS REMOVED.** The owner had it ripped out (build `2026-11-12.no-brightdata`): the
> module `engines\research\brightdata.py`, the Web Unlocker / SERP / Scraping Browser paths, the `BRIGHTDATA_*`
> settings and the `DRT_BRIGHTDATA` switch no longer exist. A page the browser cannot read goes to Tavily extract,
> then Exa contents; an empty engine result goes to Tavily, then Exa. Wherever this older record mentions Bright
> Data, it describes something that is gone. Do not rebuild it.

> **2026-11-12 - BROWSER IS BRAVE.** Deep Research drives the installed Brave (visible by default, headless with
> `DRT_HEADED=0`), profile `.drt_brave_profile`. Wherever this older record says "Chrome", `channel="chrome"` or
> `.drt_chrome_profile`, read Brave. House rule and shared launcher: `_KNOWLEDGE\BROWSER_STANDARD.md`.

# Deep Research — Handoff

_Last updated: 2026-11-12 (build `2026-11-12.signal`). Older entry: 2026-09-20, portable implementation guide, build `2026-09-05.pollretry`._

> ## ⭐⭐ 2026-11-12 — READ `..\DEEP_RESEARCH_REVIEW_2026-11-12.md` FIRST
> Top-down review + rebuild of forum search, page reading, the agent loop and synthesis; new
> source alerts, Pause / Resume / Restart, and "Add/Fix Specific Sources". That file is the
> current truth for: why the "hosted site" banner appeared locally (the hub serves the tool at
> `research.localhost:5050`), what was broken (the sites' own search had returned nothing in
> the last five traced runs), what changed per file, how it was verified, and the open items.
> Where it disagrees with anything below, it wins. Still true from below: the pipeline shape,
> the model roles in `config\drt_models.json`, the provider keys, LOCAL ONLY (nothing is pushed
> outside the house — the hosted Render copy does not have the 2026-11-12 changes).

> ## ⭐ STATE NOW — read this first
> **The ONLY codebase is the repo clone `D:\_______Claude\Central-Industrial\deep-research\`**
> (local :5006 runs from it under the local hub; hosted `research.centralindustrial.ai` deploys
> from it via Render). Build marker `2026-09-05.pollretry` on `/api/health`, which also reports
> `providers` — at last check both instances: `brave_api · tavily · exa · brightdata.unlocker ·
> brightdata.browser` all true, `brightdata.serp` false (zone never created; optional).
> Workflow: edit here → restart local (in-app Restart or kill the :5006 PID and hit the hub) →
> test → bump `build` → `git commit` → `git push` → Render deploys ~90s → poll `/api/health`.
> Gitignored local state in this folder: `.env` (incl. `DRT_REPORTS_DIR`), `config/.drt_vault_key`
> + `config/drt_credentials.enc`, `.drt_chrome_profile/`, `.venv/`, the egg mp3, `traces/`.
> The old `D:\_______Claude\Deep Research\` is archived — never edit it. Render env vars
> (`sync:false` in `render.yaml`) are set by hand in the dashboard; the Anthropic key there is the
> funded `pG9r…` key. Models: lanes/extract/route = `claude-sonnet-5`, plan/dig/synthesize =
> `claude-opus-4-8` (`config/drt_models.json`). Measured cost: a standard run ≈ $0.88 + ~$0.5-1
> for the Odysseus pre-pass (which bypasses the wrapper — see follow-ups).
>
> **Open follow-ups:** (1) route `engines/odysseus/llm_core.py` through the cached/accounted
> wrapper shape; (2) the "Oh, Very Deep" 1-hour tier has never been run end to end; (3) Brave's
> scrape path is unverified (moot while `BRAVE_API_KEY` is set); (4) hub relay hardening lives
> in the hub, not here.
>
> **Part A below is the portable guide** — written to bring a sibling deep-research tool (same
> Flask + Playwright + Claude shape, more rudimentary) up to this tool's level. **Part B** is the
> session-by-session history of THIS tool, kept for detail.

---

# PART A — PORTABLE IMPLEMENTATION GUIDE (for the sibling tool)

_Audience: an agent or engineer upgrading another instance of a deep-research tool on another
machine/network. Framework-relative: adapt names, keep the shapes. Everything here is what was
built and live-verified on the reference tool between 2026-08-12 and 2026-09-05. It assumes the
sibling already has (or first builds) the 2026-08-12 baseline described in
`DEEP_ACTUAL_RESEARCH_UPGRADE_GUIDE.md` (kept beside this file): one wrapped LLM client, a
staged browser pipeline with a tool-use loop, per-page extraction with graded citations, a live
activity feed, time-boxed depth tiers, the editable governance prompt. If the sibling lacks any of
that, do that guide first — this one layers on top of it._

## A0. What this guide adds, and the order to do it

| # | Workstream | Why it earned its place | Depends on |
|---|---|---|---|
| A1 | Credentialed websites done right | The vault was worthless until login detection stopped lying and native search reached gated content | baseline |
| A2 | Native in-forum search (platform adapters) | `site:` engine queries see only what engines indexed; gated forums = nothing | A1 |
| A3 | Web crawling & site behaviour (walls, engines, fallbacks, Bright Data, Tavily/Exa) | Two of three browser engines were returning nothing for days; Cloudflare interstitials were being read as page text | baseline |
| A4 | Odysseus distilled into the main tool (pre-research on every tier) | Grounds planning and the lanes in a fast headless pass; the old "chained" tier proved it, so it became universal | baseline |
| A5 | Parallel research lanes + lead review + sequential digger | The best-evidenced protocol in the field (orchestrator-worker gather, single synthesis); ~N× pages per minute | A3 |
| A6 | Tiers, budgets, 1-hour free-roam | Lanes need a clock that hands time down to the digger and deepening | A5 |
| A7 | Model split, prompt caching, usage accounting | Lanes on Sonnet + caching took a run from an estimated ~$15 to a measured $0.88 | A5 |
| A8 | Titled report files, audit additions | Filenames a human can find | any |
| A9 | Ops: single work tree, poll resilience, no console flashes, env table | The fork between local and hosted copies cost more than any feature | any |
| A10 | Verification protocol | What "done" looked like | all |

Do A1→A2→A3 in the engine as one coherent pass (they share the browser harness and agent
files), then A4 in the server/worker, A5→A6→A7 in the agent + client wrapper, A8/A9 anywhere.

## A1. Credentialed websites

**A1.1 One unified site list.** Replace any split between "sources" and "vault domains" with ONE
list, `config/sources.json`: `[{"url": "avforums.com", "login_required": false|true|null,
"note": "AV / home-theater", "search_url": "https://…/search?q={q}" (optional)}]`.
`login_required` drives everything: `false` = searchable logged out, `true` = needs a login,
`null` = untested (a probe fills it in). The vault is a credential LOOKUP keyed by normalized
domain, not a second list — the old two-list design orphaned vault logins that weren't also in
the sources table. Loader: tolerate a UTF-8 BOM (`encoding="utf-8-sig"`) and both old and new
schemas; normalize `url` to a bare registrable host (`https://www.X.com/forums/` → `x.com`).

**A1.2 Relevance gates every site, every run.** ONE model call over the whole list picks the
topically relevant sites for THIS question ("return ONLY the domains whose subject matter is
appropriate; be selective; at most 18; JSON `{"domains": [...]}`"). Public picks (`login_required
!= true`) go to the open sweep; login-required picks go to the login path. No always-on sites.
Skip the call when the list is small (≤12) — inject it whole.

**A1.3 Honest login detection (the fix that mattered most).** The old check treated "no
password field on the login page" as authenticated, which false-positived four forums as
logged-in while harvesting public content. Verify a login ONLY by (a) a STRONG auth cookie or
(b) a cleared login form + URL no longer containing `login`/`signin`. Strong cookie names:
`xf_user` (XenForo), `user_session` / `logged_in` / `dotcom_user` (GitHub), `reddit_session` /
`token_v2` (Reddit), `auth_token` / `twid` (X), `flarum_remember`, `sapu` (Seeking Alpha),
`li_at` (LinkedIn), `bb_userid` / `vbulletin_user`; substrings `_user`, `auth_token`,
`remember_`, `logged_in`, `loggedin`, `wordpress_logged_in`, `phpbb3_sid`. Deliberately EXCLUDE
guest/session cookies (`xf_session`, bare `*session*`, analytics) — those exist logged-out.
Under-reporting is safe (falls back to public search + reactive login); over-reporting silently
harvests public content while claiming success. "Not logged in — public content only" is a
benign soft state, not a failure: most forums search fine logged out.

**A1.4 Proactive login at the credentialed stage.** `ensure_logged_in(domain, creds)` BEFORE
searching a login-required site: already-authenticated cookie → done; no creds → proceed logged
out; else open `login_url` (or `https://domain/`), autofill, wait networkidle, re-verify per A1.3.
Mark the domain "auth-handled" ONLY on a confirmed login, so a failure doesn't suppress the
reactive handler later. Autofill selectors: username `input[autocomplete='username']`,
`input[type='email']`, `input[name*='user' i]`, `input[name*='email' i]`, `input[id*='user' i]`,
`input[id*='email' i]`, `input[type='text']`; password `input[type='password']`; submit
`button[type='submit']`, `input[type='submit']`, `button:has-text('Log in'|'Sign in'|'Log In'|
'Continue')`, else press Enter. Per-site selector overrides live on the vault entry. Known
failure: Discourse's JS login modal (openwrt) defeats selector autofill — a one-time manual
sign-in in the tool's persistent Chrome profile fixes it permanently (the profile keeps the
session like github/reddit). 2FA/captcha logins are skipped and reported, never stalled on.

**A1.5 Reactive wall handler.** On every `open()`, detect a login/paywall wall (URL contains
`/login`, `/signin`, `/sign-in`, `/sign_in`, `/auth`, `accounts.google`, `/u/login`, `oauth`,
`/account/login`; OR a password field + short page (<700 chars) or a wall keyword; OR short
page + keyword: "log in to continue", "sign in to continue", "please log in", "you must be
logged in", "log in or sign up", "create a free account", "sign up to continue", "subscribe to
read", "subscribers only", "this content is for", "members only", "verify you are human", "are
you a robot", "press & hold", "enable javascript and cookies", "access denied", "you've been
blocked", "unusual traffic", "complete the security check"). Handler order: vault creds →
autofill → re-read the page; no creds → record the domain as a gated candidate for the batched
credential prompt; failed stored creds → a user-facing "stored-login warning" in the report.
Once per domain per run.

**A1.6 Hosted vs local.** The vault (key + encrypted blob) is LOCAL-ONLY and never committed or
pushed; the hosted instance always shows zero logins by design and gets a ONE-TIME ephemeral
credential prompt for any relevant login-required site (held in memory for the run, never
saved). A datacenter-IP headless browser rarely clears gated forums anyway — say so in the
hosted UI. Credentialed deep-forum research is a local capability; document that, don't fight it.

**A1.7 Source-access probe.** A Sources-panel button that runs each site's logged-out native
search from THIS server's own browser environment (throwaway profile, headless) and writes a
✓/✗ verdict onto the site's dot. On the hosted instance that is the true datacenter-IP picture;
from a residential machine it is optimistic.

## A2. Native in-forum search

**A2.1 Dispatcher.** `native_search(domain, query, search_url)` picks the most reliable method
per platform and returns `[]` on failure so the caller falls back to the `site:` engine, then to
Tavily with `include_domains=[domain]`. Platform map (extend as the catalog grows):
XenForo = avforums, rivianforums, wilderssecurity, forum.wiimhome, teslamotorsclub, head-fi,
audiosciencereview; Discourse = forum.openwrt.org, forum.bambulab.com; Reddit; GitHub. Unknown
domains → the `{q}` template if registered → the universal search-box driver.

**A2.2 Adapters (what actually works).**
- **XenForo:** GET `https://{domain}/search/?q={q}&o=relevance` renders results directly on
  most installs; read anchors matching `/threads/` or `/posts/` inside `.p-body-content`,
  `.contentRow-title`, `.block-body`, plus XF1 markup (`.discussionListItem h3.title a`,
  `.titleText a`, `#content a`). If empty, type into the VISIBLE `input[name='keywords']`
  (XenForo renders TWO — the first is hidden) and press Enter. If selectors still find nothing
  but a results container (`.searchResult`, `.discussionListItem`, `.contentRow`) exists, take
  any `/threads/` link. Skip nav paths (`/whats-new/`, `/forums/`, `/members/`, `/search/`,
  `/login`, `/tags/`, `/latest-activity`).
- **Discourse:** try `/search.json?q=` once (clean when not rate-limited; anonymous callers hit
  "performed this action too many times" fast) → else the HTML `/search?q=` page, wait for the
  Ember-rendered rows (`.fps-result a.search-link`, `a[href*="/t/"]`) plus ~1.8s settle.
- **Reddit:** `https://old.reddit.com/search?q={q}&sort=relevance` is server-rendered —
  `a.search-title`. (New reddit is a JS app; don't scrape it.)
- **GitHub:** the public REST API `GET https://api.github.com/search/repositories?q=…&sort=
  stars` — the web `/search` page is a React SPA whose scrape returns nav junk. Anonymous
  limit ~10 req/min → non-200 returns `[]` → `site:` fallback.
- **Universal search box:** load the site root, find `input[type='search']`, `input[name='q']`,
  `input[name='query']`, `input[name='keywords']`, `input[aria-label*='earch' i]`,
  `input[placeholder*='earch' i]`; fill, Enter, then read anchors with ≥15-char text inside
  `main`/`#content`/body.

**A2.3 JS-aware reading.** Many forums render results AFTER load. Before reading the DOM, poll up
to ~8s for known result rows (`.fps-result`, `.search-result`, `.contentRow`, `.block-row`,
`[data-testid="post-container"]`, `.search-results li`, `.SearchResult`) OR for the anchor
count to stop growing across two polls. Reading immediately after `domcontentloaded` was the
reason native search returned 0 on JS forums.

**A2.4 Agent tool.** `forum_search {domain, query}` — "the SITE'S OWN search, run in the
logged-in browser; reaches content engines never indexed, including credential-walled areas;
falls back to engine `site:` search". Log the fallback in the result text so the feed shows it.

## A3. Web crawling & site behaviour

**A3.1 Bot-challenge interstitials are not pages.** Cloudflare's "Just a moment…" / "Performing
security verification" was being captured AS the page text, and the agent burned opens and
extensions re-opening it. In `open()`: after the first read, if the title contains any of
"just a moment", "attention required", "access denied", "please wait", "checking your browser",
"security verification" (and the text is <2500 chars), OR the text contains "performing
security verification", "verify you are human", "verifying you are not a bot", "checking your
browser before accessing", "checking if the site connection is secure", "enable javascript and
cookies to continue", "cf-chl", "ray id:", "needs to review the security of your connection",
"press & hold", "complete the security check", "unusual traffic from your computer network" →
poll every 0.8s for up to 9s for the challenge to clear (headed Chrome usually passes a managed
challenge on its own); if it never clears, return `blocked=True` + an error so the caller's
fetch fallback (A3.3) takes over. Log both outcomes to the feed.

**A3.2 Read the whole forum page.** Forum software wraps EVERY post in its own `<article>`.
"Prefer the first `<article>`" kept only the opening post (494 chars of a 4,000-char thread).
Rule for both the browser text extractor and any HTML→text helper: exactly one `<article>` =
the page's content; several = read `main` / `[role=main]` / body so replies survive. Drop
`script, style, noscript, nav, header, footer, aside, form, svg, button, iframe`.

**A3.3 Fetch fallback chain when Chrome can't read a URL** (blocked, 403, timeout):
Tavily extract → Exa contents. A recovered page enters the harvest
with `via="<stage>+<provider>"`, is listed in the audit under "Pages Chrome could not read,
fetched via unblocking providers". Tool result text says "Opened via tavily (the browser was
blocked: …)".

**A3.4 The engine layer (what each engine actually needs now).**
- **DuckDuckGo:** `https://html.duckduckgo.com/html/?q=` → `a.result__a`; hrefs are wrapped
  `//duckduckgo.com/l/?uddg=<encoded real url>` — unwrap `uddg`.
- **Google:** results are `#rso a:has(h3)` but EVERY href is now an opaque `/goto?url=<token>`
  (the old `/url?q=` is gone), which a junk-host filter drops as google-internal → 0 results
  for days. The token answers a plain **302 whose Location is the real URL** — resolve it
  through the page context's own request client (`page.context.request.get(href,
  max_redirects=0)`, works with session cookies and in remote-browser mode) BEFORE the junk
  filter. Verified 8/8 real URLs in ~2s. Google has no general web API — scrape + an optional
  SERP zone is the right stack.
- **Brave:** the `#results` container is gone; results are Svelte `.snippet` blocks (skip ones
  inside a `video-cluster`; title from `.title, h2, h3, [class*=title]`; description from
  `.snippet-description`). After a few rapid hits Brave serves a CAPTCHA page (`.captcha-
  wrapper`) — detect it in ~1s and return `[]` instead of waiting 8s on a selector. Better:
  **use the official Brave Search API first** (`GET https://api.search.brave.com/res/v1/web/
  search?q=…&count=N`, header `X-Subscription-Token`, parse `web.results[].{title,url,
  description}`; free tier at api-dashboard.search.brave.com) and keep the scrape as fallback.
- **Engine fallback chain** in ONE function the agent tools and the baseline sweep share:
  browser engine → Tavily → Exa. Tell the agent in the
  tool description that an empty result already went through the providers, so "empty means
  the web has nothing for that phrasing — rephrase, don't repeat".

**A3.5 API discovery on by default.** Tavily (`POST https://api.tavily.com/search`
`{query, search_depth:"advanced", max_results, include_domains?}` → `results[].{title,url,
content}`; extract `POST /extract {urls, extract_depth:"advanced", format:"markdown"}` →
`results[].raw_content`; Bearer `tvly-…`) and Exa (`search(type="auto",
contents={"highlights":True})`, `find_similar`, `get_contents(text={"max_characters":20000})`).
Enabled whenever the key is present; `DRT_TAVILY=0` / `DRT_EXA=0` are kill switches. Uses:
(1) two extra baseline-sweep queries each, added to the reranked pool (a different index
each); (2) engine fallbacks; (3) agent tools — `web_search engine="tavily"` (offered only when
keyed) and `exa_search` / `exa_find_similar` when the plan's "API discovery" channel is on
(UI toggle, default on). Don't use the Tavily/Exa MCP servers for the tool — the engine isn't
an MCP client; the REST calls are the same API.

**A3.6 Bright Data - removed 2026-11-12.** The owner had it ripped out. There is no unblocking proxy,
no SERP zone and no remote browser; the fallbacks are Tavily and Exa only (A3.3, A3.4). No UA-spoof or
`navigator.webdriver` shims either: a site that refuses the browser is reported, not disguised past.

**A3.7 Baseline sweep hygiene.** Semantic rerank of the pooled results (fastembed
`BAAI/bge-small-en-v1.5`, identity fallback). Query craft everywhere: compact keyword strings,
2-6 terms, proper nouns first, no question words, `?` stripped; the verbatim-question fallback
is replaced by stopword-stripped compression + `problems`/`review`/`forum` variants.

## A4. Odysseus distilled into the main tool

The "Odysseus" engine is the vendored Alibaba IterResearch loop (plan → per-round query
generation → parallel search → per-page goal extraction → evolving report → stop decision →
final report), headless (DuckDuckGo via `ddgs`/HTML + `curl` page fetch with an SSRF guard).
The sibling has it as a separate A/B sub-tool; the reference tool proved a chained "Odysseus
then browser pipeline" tier, then made it universal:

- **Every tier opens with an Odysseus pre-pass**, first stage in the stage list, sized per tier
  (`standard` 2 rounds / 150s · `deep` 4 / 300s · `exhaustive` 6 / 600s), run BEFORE the research
  clock starts (like document intake), so tier windows are untouched. STOP / Skip-stage cancel
  it cooperatively (`researcher.cancel()` from a watcher thread). Local-model runs skip it.
- **Its report is fed in EXACTLY like a dropped file:** append `"[Odysseus pre-research
  findings]\n" + report` to the run's document parts; the document-intake analyst (one model
  call: "distill THIS file against the question — KEY FACTS/CLAIMS · ENTITIES & TERMS worth
  searching (incl. insider vocabulary) · OPEN QUESTIONS · SEARCH LEADS (2-4); signal only")
  turns it into a brief that grounds the planner, the lane planner and the lane prompts via the
  clarifications channel, while the FULL text reaches synthesis as trusted user material.
- The pre-pass report is also appended to the audit ("Odysseus pre-research (headless pass
  that grounded this run)") and returned as `result["odysseus_report_md"]`.
- Pre-pass failure or an empty/"search unavailable" result degrades to a plain run — it is an
  enhancer, never a blocker. Measured: 2 rounds → ~14K-char report from ~22 URLs → ~3.4K-char
  brief → plan → lanes. Effective durations ≈ standard 7-8 min · deep 15 · 1-hour ~70.
- Run its model calls at the cheap "route" role (extraction/query-gen work).
- **Windows:** the per-page `curl` spawns each flashed a console window under `pythonw` —
  dozens per run. Spawn with `creationflags=CREATE_NO_WINDOW`, `stdin=DEVNULL` (guard
  `os.name == "nt"`). Same for any `git` the server shells out to.
- Remove the separate "Deep + Odysseus" tier/worker once this is in; map its legacy value to
  `deep`.

## A5. Parallel research lanes + lead review + sequential digger

**A5.1 Shape.** After the baseline sweep: ONE planner call decomposes the question into 1..N
lanes → N copies of the existing stage tool-loop run in parallel threads → a lead-review call
picks ONE hot trail → a single sequential "digger" loop follows it on the remaining Stage-2
time → the existing credentialed stage → the existing single synthesis. Breadth is parallel,
depth is sequential (the field's own caveat: multi-agent is poor for sequential, high-
dependency work — which is exactly "chase the hot trail"). N per tier: standard 3 / deep 4 /
1-hour 5 (env override, max 8). A single-lane plan falls back to the classic loop.

**A5.2 Lane planner (system prompt, verbatim):** "You are the LEAD of a parallel deep-research
team. Decompose the user's question into 1 to {N} independent research LANES that separate
researchers can pursue AT THE SAME TIME without overlapping. A lane is a distinct angle,
sub-question, stakeholder, source type, or community whose findings are NEEDED for the SPECIFIC
question — not a generic outline. Do not manufacture lanes: a narrow question may need only 1
or 2. Good lane splits: primary/official evidence vs. practitioner/forum discussion vs.
adversarial (complaints/failures/lawsuits) vs. a specific named entity or version vs. a
specific community to mine. For each lane give: name (3-6 words), mission (2 sentences:
exactly what to find and where it most likely lives), queries (2-3 ENGINE queries — compact
keyword strings, 2-6 terms, proper nouns / product names / insider vocabulary first, no
question words). Respond with ONLY JSON: {"lanes": [{"name","mission","queries"}]}". User
message = question + context + the baseline brief.

**A5.3 Per-lane task suffix:** "YOUR RESEARCH LANE (L{i} of {N}; the other lanes run in parallel
and cover the other angles): {name} / MISSION: {mission} / SEED QUERIES (engine-optimized —
refine as you learn): … / Stay on your lane. Finish when your lane is exhausted — do not tour."
Lane system prompt = the normal stage prompt + a "LANE L{i}: …" focus line.

**A5.4 Sharing and accounting.** Each lane gets its own soft allocation (Stage-2 searches/pages
÷ N, floors 4/5) and draws from the SHARED global pool; the harvest list, seen-URL set, pool,
discovered forums and `curated_searched` are mutated only under one re-entrant lock. Lanes'
`finish` reasons are recorded per lane (`lane_reports`), not into the run's single stop
reason. Feed lines carry the lane tag AFTER the `[kind]` prefix (`[act] L2 · → open_page …`) so
kind detection still works. Stage names/`via` = `stage2/L1`… so the audit shows provenance.
Lanes get ~70% of the Stage-2 time share; the digger gets the rest plus whatever lanes leave.
The user's "skip this stage" flag is cleared by the orchestrator after ALL lanes have seen it.

**A5.5 The browser must live on ONE thread.** Playwright's sync API is greenlet-bound: calls
from a second thread fail. Run the harness on a dedicated single-worker executor; every public
method (`search`, `native_search`, `site_native_search`, `open`, `ensure_logged_in`) is a
decorator that submits to that executor and waits, executing directly when already on the
owner thread. Model turns overlap freely; page actions serialize — since a turn is dominated by
model latency, N lanes still ≈ N× throughput.

**A5.6 Lead review (system prompt, verbatim):** "You are the LEAD reviewing what parallel
research lanes just gathered. Decide whether there is ONE hot trail — a specific thread,
community, document, author, version, or angle where the research was visibly closing in on the
user's SPECIFIC question but stopped short (clock ran out, wall hit, one hop away from the
primary source). If so, describe it concretely so a single researcher can continue it
immediately: where to start (URLs already seen that should be re-read deeper / paginated /
followed), and 2-4 ENGINE queries (compact keyword strings). If the lanes genuinely exhausted
the question, or nothing stands out, set hot_trail to an empty string. Respond with ONLY JSON:
{"hot_trail","why","queries","start_urls"}". Input = each lane's mission, finish reason and
note tail, plus the list of pages harvested. Skip the dig when <45s remain. The digger's task:
"LEAD REVIEW — CONTINUE THE HOT TRAIL … Follow it as far as it goes — pagination, replies, cited
primaries, the author's other posts. Finish when it is exhausted." Observed: the digger noticed
both lanes had opened a Reddit thread, fetched its `.json` view for the full comment tree, and
captured the resolution.

**A5.7 Transcript compaction (required for long lanes).** Each opened page appends ~8K chars
to the lane transcript forever; a 30-minute lane overflows context. Past ~280K chars of
transcript, mechanically replace page bodies in tool results older than the last 6 turns with a
one-line "[… page body elided — full text is stored in the harvest …]" marker (no model call;
the harvest keeps the full page for extraction). Log one feed line per compaction.

## A6. Tiers and budgets

| Tier | Window | Synthesis reserve | Search / page pools | Turns | Lanes | Odysseus pre-pass | Deepening rounds |
|---|---|---|---|---|---|---|---|
| standard (~5 min + pre-pass) | 300s | 100s | 120 / 150 | 120 | 3 | 2 rounds / 150s | 1 |
| deep (~10 min + pre-pass) | 600s | 130s | 240 / 300 | 240 | 4 | 4 / 300s | 2 |
| exhaustive "Oh, Very Deep" (1 h + pre-pass) | 3600s | 300s | 900 / 900 | 400 | 5 | 6 / 600s | 8 |

Time is the governor; pools are runaway ceilings sized so the clock ends the 1-hour tier, not
the pool. Stage 2 gets ~60% of the gathering window (lanes 70% of that, digger the rest); the
credentialed stage the remainder; the batched-credentials stage a fixed 60-90s grace window.
`request_extension` grants +60s (never past the hard window) plus +4 searches / +6 opens
(capped by the pool). **Gap/deepening rounds must receive a deadline** — without one, an
extension there granted "+0s" (observed in a trace); soft share per round 4 min (12 on the
1-hour tier), never past `run_deadline − 75s`. Deepening = stop-judge ("does this DIRECTLY
answer the specific question with well-sourced evidence?") → gap queries ("fill the key gap or
CONTINUE THE HOTTEST TRAIL") → a small gather loop → re-synthesis. Keep the depth aliases
(`quick`→standard, `verydeep`→deep, `hour`/`1h`/`max`→exhaustive, old `odysseus`→deep).

## A7. Model split, prompt caching, usage accounting

**A7.1 Roles → models** (the orchestrator-worker split the field converged on: a strong lead,
cheaper workers): `search` (lane navigators, credentialed/gated stages, gap rounds) =
`claude-sonnet-5`; `extract` (per-page extraction, classify) = Sonnet 5; `route` (relevance
filters, stop-judge, gap queries, titles, baseline query-gen, the Odysseus pre-pass) = Sonnet 5;
`plan` (planner, lane planner, lead review) = `claude-opus-4-8`; `dig` (the hot-trail digger,
NEW role) = Opus 4.8; `synthesize` (the deliverable) = Opus 4.8. The stage loop takes a
`model=` argument; the digger passes the `dig` role. Navigation needs taste and stamina, not
frontier reasoning; the lead review, the dig and the report are where quality shows. Local
models: the machinery works end to end (a 30B tool-calling instruct model drove a full run) but
loses parallelism (one request at a time), context (32-64K usable), and synthesis quality;
extraction is the one role where a local model pays cleanly.

**A7.2 Prompt caching in the wrapper (the single biggest cost lever).** The agent loops resend
an ever-growing transcript each turn, so the prefix is identical turn to turn. In the ONE
client wrapper: (a) send top-level `cache_control: {"type": "ephemeral"}` on every call (via
`extra_body` so it is SDK-version-proof) — caches the last cacheable block; (b) when `system` is
a string over ~4K chars, convert it to `[{"type":"text","text":…,"cache_control":{"type":
"ephemeral"}}]` — the governance + stage prompt is identical across every turn of every lane,
so parallel lanes share that hit even when their messages diverge. Verified: ~9.2K cached
tokens per call both on an extended transcript and across lanes. Raise the SDK `max_retries`
to 5 (parallel lanes draw 429s at lower rate tiers; a lane that exhausts retries aborts while
the others continue).

**A7.3 Usage accounting.** The wrapper records `usage.{input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens}` per model per run; a `usage_summary()`
prices it (list prices: Opus 4.8 $5/$25 per M in/out, Sonnet 5 $2/$10; cache write ≈1.25× in,
cache read ≈0.1× in) and the worker writes one audit line — "**API usage this run ≈ $0.88** —
`claude-sonnet-5` 30 calls, 2K in + 202K cached + 141K cache-write, 14K out ≈ $0.54 ·
`claude-opus-4-8` 4 calls … ≈ $0.35" — plus a feed line. That measured standard run (3 Sonnet
lanes, 2 extensions, 11 pages) compares with an all-Opus, uncached estimate of $10-15; the
1-hour tier is projected at ~$15-25 instead of $70-120. Anything that bypasses the wrapper
(the vendored Odysseus adapter, in our case) is neither cached nor counted — route it through.

**A7.4 Wrapper request-shape guards (unchanged, still required):** inject
`output_config.effort` (env), strip `temperature` and `thinking`, floor `max_tokens` at 4096,
detect `stop_reason == "refusal"` and pause/fall-over rather than propagating an empty response.

## A8. Reports and audit

- **Titled filenames:** one cheap call after synthesis — "You title finished research reports
  for a document library. Reply with ONLY the title: 4-9 words, Title Case, specific to the
  subject and what the report actually established (name the entity/product/version/topic —
  not the question's phrasing), no quotes, no trailing period, no colon, no 'Report on' /
  'Analysis of' filler." Filename = `<Title> — YYYY-MM-DD HHMM.docx` (a non-default tool
  gets ` (Odysseus)` before the dash); slug fallback so a file always exists. The title also
  goes into `result["title"]`, the Save button, and the results header.
- **Audit additions:** models per role; the usage line; "Parallel research lanes (N)" with
  each lane's mission and finish reason plus the digger's; "Pages Chrome could not read,
  fetched via unblocking providers"; the Odysseus pre-research section; discovered forums;
  stored-login warnings; stop reason.
- Keep: only opened-with-signal pages get citation numbers; unread leads render un-numbered;
  each source carries `source_class` (primary-document | official | news | expert-analysis |
  forum-discussion | anecdote | marketing | other) and `content_date`.

## A9. Ops

**A9.1 One work tree, no fork.** If local and hosted are separate copies, END IT: make the
hosted file a strict superset with env-driven differences (auth gate self-disables when
`AUTH_SECRET` is unset; `PORT`, hub URL and reports dir from env with local defaults; UI hub
link as a placeholder the server fills), then run LOCAL from the repo clone with the gitignored
state placed inside it (`.env`, vault key + blob, browser profile, venv, local-only media,
traces) and point the local launcher/supervisor at the clone. Workflow = edit → restart local →
test → bump build marker → commit → push → poll hosted `/api/health` for the marker. Verify
pushes by comparing the remote SHA. Grafting exact edit pairs onto a divergent copy worked but
aborted on anchor mismatches three times in five; not worth keeping.

**A9.2 Environment variables** (all optional except the API key; hosted = dashboard, `sync:false`):
`ANTHROPIC_API_KEY` · `DRT_EFFORT` (low…max; medium) · `DRT_LANES` · `DRT_HEADED` /
`DRT_BROWSER_CHANNEL` · `DRT_SLOWMO` (80) · `DRT_RERANK` · `DRT_TRACE` · `DRT_REPORTS_DIR` ·
`TAVILY_API_KEY` (`DRT_TAVILY=0` off) · `EXA_API_KEY` (`DRT_EXA=0` off) · `BRAVE_API_KEY`
(`DRT_BRAVE_API=0`) · `LMSTUDIO_URL`. `/api/health` reports a
`build` marker and a `providers` block (which keys are active) — health never calls the model
API, so a dead/unfunded key still shows green; the first real run is the proof. Servers read
`.env` once at startup: edits need a restart.

**A9.3 Status polling must survive a blip.** A single failed status fetch (a proxy/relay
dropping a pooled connection) ended a run in the UI with "Failed to fetch" while the server
kept researching and auto-saved the report. Retry transient errors for ~90s with a feed line;
treat HTTP 404 "unknown job" as a genuine server restart; after 90s say the run may still
finish server-side and point at the reports folder. Auto-save every finished report to disk
BEFORE the result reaches the browser. A finished job stays in the registry until its final
status is read — a lost run's result is recoverable with one GET.

**A9.4 Known traps carried forward:** UTF-8 BOM in JSON configs (`utf-8-sig`); PowerShell
`Set-Content -Encoding utf8` writes a BOM; Playwright browser cache is virtualized inside some
sandboxes; a headed run holds the Chrome profile lock (kill leftover `chrome.exe` whose cmdline
contains the profile path before another headed launch — never during a live run); Windows
Hyper-V/WSL WinNAT can reserve the tool's port range after a reboot; local vs hosted config
hashes differ by CRLF/LF only; a safety refusal (HTTP 200, `stop_reason=refusal`) silently
no-ops a pipeline unless the wrapper detects it.

## A10. Verification protocol (what "done" looked like)

1. **Static:** byte-compile every touched module; import the chain; assert the tool registry
   (`web_search`, `site_search`, `forum_search`, `register_forum`, `open_page`,
   `request_extension`, `finish`, `exa_*` when keyed); assert the budget table, the lanes
   table, the role map; parse the single-file UI's script block(s) with Node `new Function`.
2. **Wrapper micro-test (live, cheap):** one Sonnet call passing `temperature` + tiny
   `max_tokens` (guards fire); two identical calls with a ~5K-token system prompt (second shows
   `cache_read_input_tokens > 0`); a third with different messages (shared-system hit);
   `usage_summary()` prices it.
3. **Offline lane harness:** fake client + fake browser: 3 lanes on 3 threads, overlapping
   wall-clock (< 80% of serial), shared-pool accounting exact, a dead engine falls through to
   the SERP/Tavily fallback, a blocked open falls through to the unlocker, lane tags after the
   kind prefix, the digger runs, compaction elides old bodies and leaves the last 6 turns.
4. **Real headless browser:** owner-thread proxy from 3 concurrent threads; challenge detector
   unit cases (Cloudflare title/text positive, forum thread negative).
5. **Provider probes (live):** Tavily search + extract; Exa search; Brave API.
6. **Engine selectors (headed, throwaway profile):** dump the result-page DOM for each engine
   and confirm ≥5 real result URLs each; re-run after any 0-result day.
7. **One real standard run, trace on:** Odysseus pre-pass → intake brief → plan → N lanes with
   tagged feed lines → extensions granted → (deep) lead review + digger → credentialed stage
   logging in via the persistent profile → graded Sources → "How to go deeper" → titled .docx
   on disk → the "API usage ≈ $" audit line. Then one deep run; then, with credits, one 1-hour
   run watched for lane finish reasons and deepening rounds.
8. **Hosted:** gate test-client pass (health open; unauthed page → redirect; unauthed API →
   401; a forged-valid SSO token mints the session; authed page serves the new UI); SHA-verified
   push; health polled for the build marker; `providers` all true.

---

# PART B — THIS TOOL'S SESSION HISTORY (detail; newest first)

## 2026-09-03 → 09-05 sessions — lanes · 1-hour tier · Bright Data · Tavily/Exa · Odysseus-on-every-tier · model split · single work tree (detail)

> _State block as written at the 2026-09-03 close (superseded by STATE NOW above; kept for the specifics):_
> Build marker **`2026-09-03.odyall`** on `/api/health`, which also reports `providers`. At
> close BOTH instances show: `brave_api:true · tavily:true · exa:true · brightdata.unlocker:true
> · brightdata.browser:true · brightdata.serp:false` (SERP zone never created — optional).
>
> **FINAL ARCHITECTURE CHANGE (build `odyall`): EVERY tier opens with an Odysseus pre-research
> pass.** `DR_STAGES` now starts with `"odysseus"`; `_run_odysseus_prepass()` in `dr_server.py`
> runs the headless IterResearch engine sized per tier (`_ODY_BY_DEPTH`: standard 2 rounds/150s ·
> deep 4/300s · exhaustive 6/600s) BEFORE the research clock, and its report is appended to
> `doc_parts`/`doc_context` as `[Odysseus pre-research findings]` — so the intake analyst
> distills it into grounding for the plan + lanes and its full text reaches synthesis as trusted
> user material (exactly the old "Deep + Odysseus" chain, now for all tiers). The report is also
> appended to the audit as "Odysseus pre-research (headless pass…)" and returned as
> `result["odysseus_report_md"]`. STOP or Skip-this-stage cancels the pre-pass cooperatively.
> Local-model runs skip it (Odysseus is wired to the Anthropic key). The separate "Deep +
> Odysseus" chip and `_ody_depth_worker` are GONE; `normalize_depth("odysseus")` → deep.
> **Tier renamed: `exhaustive` = "Oh, Very Deep"** in the UI (aliases ohverydeep/hour/1h/max);
> selecting it plays `/egg.mp3` (`Boonies Basement Tub…mp3`, LOCAL file only — the route 404s
> and the UI stays silent on hosted; the mp3 is not committed). Chip subtitles now say
> "Odysseus pre-research → N min browser run". Verified live (standard): Odysseus 2 rounds →
> 14,659-char report from 22 URLs → distilled to a 3,374-char brief → plan → 3 lanes.
> Effective durations ≈ standard 7-8 min · deep 15 min · Oh, Very Deep ~70 min.
>
> ## ⭐⭐ DONE 2026-09-03 evening: THE FORK IS OVER — local runs FROM this repo clone
> **`D:\_______Claude\Central-Industrial\deep-research\` is now the ONLY Deep Research
> codebase.** The local instance (:5006, supervised by the local hub via `hub/tools.json`
> `local.cwd`/`local.cmd`) runs this folder's `dr_server.py` with this folder's gitignored
> state: `.env` (+ `DRT_REPORTS_DIR=D:\______Documents\___Deep Research Reports`),
> `config/.drt_vault_key` + `drt_credentials.enc`, `.drt_chrome_profile/` (moved, logins
> intact), `.venv/` (moved — relocatable, imports verified), the egg mp3 and `traces/`
> (both now gitignored). The old `D:\_______Claude\Deep Research\` is ARCHIVED under
> `D:\_______Claude\_ARCHIVE\` — do not edit it. **Workflow from now on:** edit here →
> restart local (in-app Restart, or kill the :5006 PID and hit the hub) → test → bump the
> `build` marker → `git commit` → `git push` → Render deploys ~90s → poll `/api/health`.
> No grafting, no parity checks, no Publish button needed (the site list is a committed
> file; local `sources_hash` differs from hosted only by git's CRLF checkout).
> _Historical rationale (kept for context):_ today's five ports were each done by grafting exact edit pairs onto the hosted files, and
> three of them aborted on an anchor mismatch before succeeding. Measured at session close,
> the ENTIRE local-vs-hosted difference is: (1) the auth gate block in `dr_server.py`, which
> is already self-disabling (`GATE_ON = bool(AUTH_SECRET)`); (2) `PORT` (env, default 5006),
> `HOME_URL`/`HUB_URL` (env, default `http://127.0.0.1:5050/`), and `_REPORTS_DIR`
> (env `DRT_REPORTS_DIR`, default `<root>/reports`) — all env-driven with local defaults;
> (3) `index.html`'s `__HUB_URL__` placeholder, which the server fills at serve time. The
> hosted copy is a strict SUPERSET that behaves as the local tool when run without the Render
> env. The other session unified the hub the same way today (hub/ = ONE codebase, local +
> public). **Do the same here:**
> 1. In `D:\_______Claude\Central-Industrial\deep-research\` (the persistent clone), place the
>    gitignored local state: `.env` (with `DRT_REPORTS_DIR=D:\______Documents\___Deep Research
>    Reports` added), `config/.drt_vault_key` + `config/drt_credentials.enc`,
>    `.drt_chrome_profile/` (or let it regenerate + re-login), the egg mp3, and a `.venv`
>    (copy the existing one or point the launcher at `D:\_______Claude\Deep Research\.venv`).
>    All of these are already in the repo's `.gitignore`.
> 2. Point the local hub's `hub/tools.json` deep-research entry (`cwd` + `cmd`) at the clone.
>    `HOME_URL` is already passed by the hub.
> 3. Retire `D:\_______Claude\Deep Research\` (archive it — do NOT keep editing it).
> Then every change is: edit in the clone → restart local (:5006) → test → `git commit` →
> `git push` → Render deploys in ~90s → poll `/api/health` for the build marker. One file
> set, one history, no grafts, no parity drift. Keep the rules that still apply: bump the
> `build` marker per deploy; SHA-verify pushes; keep `.env`/vault/profile out of git;
> Render env vars (`sync:false`) must be set in the dashboard by hand; the Publish button
> becomes redundant (the site list is just a committed file) but harmless.
> **Publish/CRLF note:** local `.sources_hash` differs from hosted purely by CRLF vs LF
> line endings (files are byte-identical after `tr -d '\r'`); the 17:16 Publish DID land
> (`2ac3d5d`). Running from the clone (LF) ends that false alarm too.
>
> **2026-09-05 "Failed to fetch" / "Research could not run" mid-run (build `pollretry`):**
> the SERVER never died (process up since 10:28, 89 polls answered, run finished 11:10 and
> auto-saved 45 pages/45 sources) — the browser's status poll failed ONCE and the UI treated
> any fetch error as fatal. Likely the local hub's per-connection relay (`hub_server.py`
> `Router`/`_relay`: one browser TCP connection pinned to one backend connection, 30s
> pre-request timeout, half-close on backend EOF) racing a pooled/speculative browser socket.
> Fix (UI, both DR + Odysseus poll loops): transient errors retry for ~90s (feed line
> "connection blip … retrying"); HTTP 404 "unknown job" = server actually restarted → clear
> message; after 90s → "lost contact; the run may still finish server-side — check the
> reports folder". ⚠ A finished job stays in `_JOBS` until its done-poll is read — so a
> lost run's result can be recovered with one status GET (that's how the 11:10 report was
> retrieved). Hub relay hardening (keep-alive awareness / retry on backend connect) is the
> hub's job — not touched here.
>
> **Windows console-flash fix (same evening):** dozens of cmd windows popping up during a local
> run were the Odysseus pre-pass's per-page `curl` fetches (`engines/odysseus/search.py`
> `_curl_get`) — under `pythonw` every child console app gets its own window. Now spawned with
> `creationflags=CREATE_NO_WINDOW` (+ `stdin=DEVNULL`); same for the Publish button's `git`
> calls in `dr_server.py`. Guarded by `os.name == "nt"`, no-op on Render. The headed Chrome
> window is intentional and unaffected.
>
> **LAST CHANGE — build `2026-09-03.sonnetlanes`, LIVE-VERIFIED + PUSHED (repo `cca349b`):
> orchestrator-worker MODEL SPLIT + prompt caching + usage accounting.** ⚠ The Anthropic
> account behind the `tFib…` key RAN OUT OF CREDITS during this session (≈6 all-Opus runs);
> local `.env` now carries a NEW key `sk-ant-api03-pG9r…` from a funded org — **the Render
> `deep-research` env still has the OLD key; update `ANTHROPIC_API_KEY` there** or hosted runs
> fail with "credit balance is too low" (health stays green — it never calls the API).
> Verified live: Sonnet 5 through the wrapper (guards fire, effort accepted); caching reads
> ~9.2K tokens per call both on an extended transcript AND across lanes sharing the system
> prompt (explicit `cache_control` breakpoint on `system` when >4K chars + top-level auto
> breakpoint); a full standard run on the split (job c64c88…, 3 Sonnet lanes, 2 extensions,
> 11 pages, 7 cited sources) reported **≈ $0.88 total** in its audit — Sonnet 30 calls with
> 202K cached / 141K cache-write / 14K out ≈ $0.54, Opus 4 calls ≈ $0.35. ⚠ That figure
> EXCLUDES the Odysseus pre-pass: `engines/odysseus/llm_core.py` calls AsyncAnthropic
> directly (no wrapper → no caching, no accounting; ~$0.5-1 on Sonnet for 2 rounds).
> Follow-up: route llm_core through the same cache/accounting shape. Original plan text:
> Per the user (cost
> discussion: a 1-hour run estimated at ~$70-120 on all-Opus, dominated by lanes resending
> growing transcripts): `config/drt_models.json` + `models.py` defaults now = **search/extract/
> route → `claude-sonnet-5`; plan/dig/synthesize → `claude-opus-4-8`** (new `dig` role: the
> hot-trail digger passes `model=get_model("dig")` into `_run_browser_stage(..., model=)`;
> lanes/stage3/stage4/gap rounds use `search`; the Odysseus pre-pass uses `route`; the
> standalone Odysseus sub-tool stays Opus). `ClaudeClient` now (a) sends top-level
> `cache_control: {type: ephemeral}` via `extra_body` on EVERY call (auto-cache the last
> block — the agent loops' prefix is identical turn-to-turn; expected to cut the lanes row
> ~85%), (b) `max_retries=5`, (c) counts usage per model (`client.usage`, `usage_summary()`
> with list prices) → `h.usage` → **"API usage this run ≈ $X"** line in the report audit +
> `[synth]` feed line. Offline lane harness passes. **TO DO when credits are back:** run the
> wrapper micro-test (Sonnet 5 call with temperature+tiny max_tokens; two identical calls
> with a ~5K-token system → 2nd shows `cache_read_input_tokens>0`; `usage_summary()`), then
> a standard run (audit should show Sonnet lanes + the $ line), then graft to hosted
> (models.py, agent.py, llm.py, dr_server.py, config/drt_models.json) + push. If the API
> rejects top-level `cache_control`, drop that one `setdefault` line in `llm.py` — nothing
> else depends on it. The credit exhaustion itself = today's ~6 Opus runs.
>
> Five builds shipped today in order: `lanes` (5143de1) → `bdsessions` (5c844c8) → `engines`
> (fa0363b) → health `brave_api` field (9945784) → `odyall`. Each was ported to `nurtrino/Central-Industrial`
> by grafting (engine files copied whole — the deployed engine was byte-identical to pre-session
> local; exact old→new edit pairs onto the gated `dr_server.py` + `index.html`; `render.yaml`
> declares `TAVILY_API_KEY` / `BRAVE_API_KEY` / `BRIGHTDATA_*` / `DRT_LANES` as `sync:false`),
> gate verified with the Flask test client, push SHA-verified, hosted health polled for the
> marker (~80-100s each). All keys are set in BOTH the local `.env` and the Render `deep-research`
> env. ⚠ This handoff's copy in the repo lags this final edit by one push (docs only).
>
> **Engine layer at close:** DuckDuckGo + Google via the browser (Google's `/goto` tokens
> resolved to real URLs), Brave via its official API (scrape = fallback), Tavily + Exa via API;
> any empty engine result falls through Bright Data SERP (off) → Tavily → Exa. Pages Chrome
> can't read (Cloudflare challenge detected + waited out) fall through Bright Data Web Unlocker
> → Tavily extract → Exa contents. Headless/hosted mode runs on Bright Data's Browser API, one
> session per tab. Stage 2 = parallel lanes + hot-trail digger; tiers Standard 5 min / Deep 10
> min / 1 hour. Reports save as `<generated title> — YYYY-MM-DD HHMM.docx`.
>
> **Verified runs today (local, same Rivian question):** standard = 3 lanes, 11 searches,
> 8 pages, 6 sources; deep = 4 lanes + digger, 19 searches, 20 pages, 17 sources, stop-judge
> YES first pass. Both BEFORE the Google/Brave fixes and the Brave/Tavily keys, so the next run
> should see materially more engine hits and fewer fallbacks. ⚠ The 1-hour tier has NOT been
> run yet. ⚠ Brave scrape-path markup fix is unverified (kept captcha'd during testing; moot
> while the API key is set).
> Driven by a compare/contrast of the tool against the agentic-search techniques report
> (`Deep Research — Report (7).docx`): the report's best-evidenced protocol is
> orchestrator-worker gathering with parallel sub-agents then ONE synthesis step; our own
> report and the 09-03 Rivian trace also showed two of three browser engines returning
> nothing. Four changes shipped, all live-verified in one standard run (job `aaa08668…`,
> 3 lanes, 11 searches, 8 pages, 6 graded sources, report auto-saved):
>
> 1. **Parallel research LANES (Stage 2 = orchestrator-worker).** `agent.py`:
>    `plan_lanes()` (one planner call → 1..N lanes `{name, mission, queries}`, N per tier
>    standard 3 / deep 4 / exhaustive 5, override `DRT_LANES`), `_run_stage2_lanes()` runs
>    one `_run_browser_stage` per lane in a thread (each lane: own soft allocation =
>    cap2/N, shared global pool + `seen_urls` + harvest guarded by `_SHARED_LOCK`, shared
>    clock = 70% of the Stage-2 share, `stage_name`/`via` = `stage2/L<i>`), then
>    `_lead_review()` picks ONE hot trail and a sequential **digger** (`stage2/dig`) follows
>    it on the remaining Stage-2 time (precept 3 — Anthropic's own caveat is that multi-
>    agent is poor for sequential depth, so breadth is parallel and depth is sequential).
>    Feed lines carry the lane tag AFTER the `[kind]` prefix (`[act] L2 · → open_page …`);
>    `HarvestResult.lanes` / `lane_reports` render in the audit; single-lane questions fall
>    back to the classic loop. **Browser owner thread** (`browser.py`): Playwright's sync API
>    is thread-bound, so `DRTBrowser` runs on ONE dedicated executor thread and every public
>    action from another thread is queued to it (`_on_browser_thread`); model turns overlap,
>    page actions serialize. **Transcript compaction** (`_compact_messages`): past ~280K
>    chars of transcript, page bodies older than the last 6 turns are elided mechanically
>    (harvest keeps the full text) — required for the 1-hour tier not to overflow context.
> 2. **1-hour tier** `exhaustive` (`DEPTH_BUDGETS`: 3600s / 300s reserve / 900 searches /
>    900 pages / 400 turns; aliases hour/1h/max; UI chip "1 hour"; `DEEPEN_ROUNDS` 8,
>    time-gated). Intent: lanes free-roam until they call `finish` or the clock ends.
>    `run_gap_round` now takes `deadline=` + `stop_event=` (extensions there used to grant
>    "+0s"); its cap is larger on exhaustive.
> 3. **Bright Data instead of stealth UA** (`engines/research/brightdata.py`): the stealth UA
>    + `_STEALTH_JS` shims are REMOVED. Local headed Chrome stays primary (logins live in
>    the profile). Web Unlocker = fetch path for any page Chrome can't read (`open()` now
>    detects bot-challenge interstitials — "Just a moment…", "Performing security
>    verification" — waits ≤9s for them to clear, else returns `blocked`/`error` and
>    `_fetch_fallback` runs Bright Data → Tavily extract → Exa contents; fetched pages get
>    `via=<stage>+brightdata` and `HarvestResult.fallback_fetches`). SERP API = search
>    fallback inside `_engine_search` (browser engine → Bright Data SERP → Tavily → Exa).
>    Scraping Browser = replaces bundled Chromium in HEADLESS mode when
>    `BRIGHTDATA_BROWSER_WSS` is set (`connect_over_cdp`). ⚠ **Without Bright Data, headless
>    mode (hosted / the source probe) is now MORE blockable than before** — DDG returned 0
>    from a throwaway headless profile in testing. Env: `BRIGHTDATA_API_KEY`,
>    `BRIGHTDATA_UNLOCKER_ZONE`, `BRIGHTDATA_SERP_ZONE`, `BRIGHTDATA_BROWSER_WSS` (all blank
>    = off; `DRT_BRIGHTDATA=0` kill switch). API = `POST https://api.brightdata.com/request`
>    `{zone,url,format:"raw"}`, SERP adds `brd_json=1` (parser is defensive: organic/
>    link|url/title/description). **Web Unlocker LIVE-VERIFIED same day** (zone
>    `web_unlocker1`; the first attempt failed with `zone "<uuid>" not found` because a
>    zone password/ID had been pasted instead of the zone NAME — the name is the short
>    label in the dashboard). It fetched the rivianforums thread Chrome was Cloudflare-
>    blocked on that morning. SERP + Browser API zones not created yet (blank = off).
>    **Multi-post fix (both `brightdata.html_to_page` and `browser._extract_text`):** forum
>    threads wrap EVERY post in its own `<article>`, so "prefer the first article" kept
>    only the opening post (494c); now one article = content, several = read main/body
>    (same thread → 4068c, all replies).
> 4. **Tavily + Exa on by default** (`tavily_search.py` new; `exa_search.is_enabled` now =
>    key present unless `DRT_EXA=0`). Both are baseline-sweep sources (2 queries each, added
>    to the reranked pool), engine fallbacks, and agent tools: `web_search engine="tavily"`
>    (only offered when a key exists) + `exa_search`/`exa_find_similar` when the plan's
>    `neural_search` channel is on (default on; UI toggle "API discovery"). `TAVILY_API_KEY`
>    is still BLANK in `.env` — Exa is live (verified: every dead Brave/Google query fell
>    through to Exa in the shakedown run), Tavily is wired but dormant until a key is added.
>
> 3b. **Bright Data Browser API — LIVE + ported (build `2026-09-03.bdsessions`).** Zone
>    `scraping_browser1`; `BRIGHTDATA_BROWSER_WSS` set locally AND on Render (first attempt
>    failed `407 wrong_password` — the URL had been retyped with a truncated password; copy
>    it with the copy icon on the zone's Playground/Overview "Credentials" line). Their
>    sessions are ONE DOMAIN each (`navigate_domains_limit`), max 60 min, 5-min idle timeout
>    → `new_tab()` opens a fresh CDP session per tab in remote mode (page.close() closes the
>    session); startup only probes the credentials; `_domain_cookies` → [] (never "logged
>    in"). Live headless test through it: the Cloudflare-walled rivianforums thread read in
>    FULL (4580c, no challenge) and DDG returned 5 results — bundled headless Chromium got 0
>    from every engine and was walled on the thread. Per-action latency 10-50s (remote).
>    Native XenForo search through the remote browser timed out (falls back to `site:`).
> 3c. **Google + Brave engines FIXED (build `2026-09-03.engines`).** Both had returned 0 in
>    every mode all day. Causes: **Google** now wraps EVERY result href as an opaque
>    `/goto?url=<token>` (the old `/url?q=` is gone) which the junk-host filter dropped —
>    the token answers a plain 302 → real URL, so `DRTBrowser._resolve_goto` resolves it
>    through `page.context.request` (session cookies; works in remote-session mode) BEFORE
>    the junk filter; verified 8/8 real URLs in 2s. **Brave** dropped the `#results`
>    container: results are Svelte `.snippet` blocks (`extract_js` in `_ENGINES`), and a
>    rate-limited load renders a `.captcha-wrapper` page — now detected in ~1s and returned
>    as empty (fallback chain). Repeated test hits kept Brave captcha'd, so the scrape fix is
>    unverified; moot when keyed: **`brave_search.py` = official Brave Search API**
>    (`BRAVE_API_KEY`, blank until the user gets one at api-dashboard.search.brave.com) is now
>    the PRIMARY `brave` path in `_engine_search`, scrape = fallback. Google has no general
>    web API (Custom Search JSON is capped/thinner) — browser scrape + Bright Data SERP zone
>    (not created) is the right Google stack.
> 5. **Titled report filenames** (same day, user request): `_report_title(query, report_md,
>    client)` = one cheap model call ("4-9 words, Title Case, specific to what the report
>    established"; slug fallback) → `_report_filename(title, label)` =
>    `<Title> — YYYY-MM-DD HHMM.docx` (non-default tools get ` (Odysseus)` before the dash).
>    Used by the auto-save, `POST /api/save_report` (accepts `title`; generates one if
>    absent), and both Save&Open buttons (UI passes `d.title`, which is also shown above
>    the Question line in results). Example: `RivianOS Update Cycle Bugs and Fixes (2026)
>    — 2026-09-03 1351.docx` (was `Deep Research — Rivian R1T owner reports latest software
>    — 2026-09-03_134712.docx`).
>
> **Observed in the shakedown (standard tier):** lanes window was only 54s because planning
> + baseline sweep (now with API extras) + relevance + lane planning consumed ~70s of the
> 197s gathering window; the lanes' own extensions then used up the Stage-2 share, so the
> hot-trail digger and the deepening round were both skipped for lack of time. That is the
> 5-minute tier working as designed — use Deep or 1 hour to see the digger and deepening.
> **Tests:** offline harness (fake client + fake browser: lanes on 3 threads, shared pool
> accounting, SERP fallback on a dead engine, Bright Data fallback on a blocked open, lane
> tags, digger, compaction) and a real headless-Chrome owner-thread test both pass —
> scripts live in the session scratchpad only. **Not done:** porting to the hosted twin
> (`nurtrino/Central-Industrial`) — graft additively as before; the deployed
> `requirements.txt` needs nothing new (requests + bs4 already present).
>
> ---

> ## ⭐ STATE AT 2026-08-29 SESSION CLOSE — read this first
> Local (`127.0.0.1:5006`) and deployed (`research.centralindustrial.ai`) are at **parity**,
> repo `nurtrino/Central-Industrial` commit **`4adc466`**, health build **`2026-08-29.localmodel`**.
> This session shipped **8 commits** (all SHA-verified on origin, all deployed). In order:
>
> 1. **`2207262` — Fable 5 → `claude-opus-4-8` at ALL points.** Fable's elevated cyber classifiers
>    were REFUSING legit LLM-security/jailbreaking research (`stop_reason=refusal, category=cyber`,
>    empty content) and the wrapper didn't detect it → whole pipeline silently no-op'd. Opus 4.8
>    lacks those classifiers; same API shape so the `ClaudeClient` wrapper is unchanged. Model id
>    in `config/drt_models.json` + `models.py` + `odysseus/llm_core.py` + 2 ody workers.
> 2. **`a18f7fc` — STOP button.** During a run: ⏹ Stop → "Assemble report from what's gathered"
>    (synthesize partial harvest) or "Discard & start a new query". Per-job `_DR_STOP` event +
>    `job["stop_mode"]`; `POST /api/deep_research/stop {mode: assemble|abort}`; `stop_event`
>    threaded through `run_search`→`_run_browser_stage` + `run_local_baseline`; status carries `aborted`.
> 3. **`704d0b1` — Refusal safety-net.** If Claude still refuses, the run PAUSES at that call and
>    offers "Switch to local model" (UI confirms via `/local_model` → `ClaudeClient` swaps to LM
>    Studio and RETRIES the refused call, resuming from that point; later calls route local too) or
>    "Stop & assemble". Local-down-at-swap → graceful stop-assemble (never hangs). Keystone
>    `ClaudeClient` in `llm.py` (`refusal_hook`=UI pause, `on_stop`=halt; wrapper owns the stop
>    decision). Server `_DR_REFUSAL` event + `POST /api/deep_research/refusal_choice {mode: local|abort}`
>    + `awaiting_refusal_choice` status.
> 4. **`d954991` — BOM-tolerant model-config loader** (`utf-8-sig`). ⚠ PowerShell
>    `Set-Content -Encoding utf8` writes a UTF-8 BOM → `json.load` throws → `drt_models.json`
>    overrides silently ignored → defaults. Loader now tolerates the BOM; still, write that file
>    via Write/Edit or Python (`open(...,encoding='utf-8')`), never PS `Set-Content -Encoding utf8`.
> 5. **`327f509` — Hosted Sources-panel guardrail.** The vault is LOCAL-ONLY (encrypted vault +
>    key are never committed/pushed; Render disk is ephemeral) → the hosted instance always shows
>    zero logins **by design**, and its datacenter-IP headless browser can't clear gated forums.
>    An amber notice now says so on the hosted Sources panel (`_drIsHosted()` = not localhost).
>    **Credentialed/deep-forum research is a LOCAL-ONLY capability.**
> 6. **`660cc0a` — Honest login detection.** `ensure_logged_in` no longer treats "no password
>    field on the login page" as authenticated (that false-positived avforums/rivian/seekingalpha/x
>    as logged-in). Now verifies a STRONG per-platform auth cookie (`xf_user`, `user_session`/
>    `logged_in`, `reddit_session`/`token_v2`, `auth_token`… — NOT guest `xf_session`) or a cleared
>    login form; "not logged in — public content only" is benign (forums search fine logged out);
>    marks the domain auth-handled ONLY on a confirmed login so failures don't suppress reactive login.
> 7. **`d011392` — JS-aware native forum search + platform adapters.** `site_native_search` now
>    waits for JS-rendered results before reading the live DOM. New `native_search(domain, query,
>    search_url)` dispatcher in `browser.py` picks per-platform: **Discourse** `/search.json` (anon
>    rate-limited → Ember-HTML fallback), **Reddit** old.reddit, **XenForo** GET quick-search else
>    drive the VISIBLE keywords box (XF renders TWO `name=keywords`, 1st hidden; XF1+XF2 markup),
>    **GitHub** GET results URL; empty → falls back to the `site:` engine (no regression).
>    `forum_search` in `agent.py` routes through it. `_PLATFORM_HINTS` domain→platform map — extend
>    as the catalog grows. Live-verified: avforums/rivian/wiim/reddit/seekingalpha 6–8 native
>    results (were ~0); github/openwrt were throttled/rate-limited during testing → `site:`;
>    wilders (XF1) / x (login-gated) → `site:`.
> 8. **`4adc466` — Local-model detection prefers the LOADED model.** `detect_local_model` used
>    `/v1/models` `data[0]` (first DOWNLOADED model, ignoring load state) → could name an unloaded
>    model and lean on JIT, or pick a different one than you loaded. Now queries LM Studio's native
>    `/api/v0/models` (reports `state`+`type`) and returns the first LOADED chat model; if chat
>    models exist but none loaded → raises a clear "no model loaded" error; falls back to first
>    `/v1` chat model only when the state API is unavailable; skips embedding models.
>
> **Later commits the same day:** `1ef6829` neural/Exa search removed as an option ·
> `5e76d82` github search via the public REST API (real repos; the React page scrape returned
> nav-tab/footer junk) · `ccc2dcf` headless-browser stealth (OS-matched UA + webdriver evasion;
> unblocked wiimhome headless) · `df12ed0` **source-access self-probe** (`POST /probe_sources`,
> `GET /probe_status`, Sources-panel "🛰 Test access" — runs each site's logged-out native search
> from THIS server; on Render = the true datacenter-IP picture) · **UNIFIED SITE LIST**
> (`2026-08-29.unified`): `drt_sources.json` is now ONE list `[{url, login_required, note?}]`
> (🟢 false = searchable logged out · 🔴 true = needs a login · ⚪ null = untested); migrated
> 10 sources + 11 vault domains → 13 sites (the 3 orphans join). ONE relevance call over the
> list picks per-query sites → 🟢 to the open sweep, 🔴 to the login path (vault silently on
> local; else Stage 4's prompt, now ONE-TIME/ephemeral — never `vault.set`); `_ALWAYS_ON_DOMAINS`
> emptied so relevance gates everything; the vault is a credential LOOKUP, not a second list.
> Sources panel = URL + dot (click to cycle), 🔑/🔒 only on 🔴 rows; the probe writes verdicts
> onto the dots. ⚠ The self-probe from local headless is OPTIMISTIC vs Render (residential vs
> datacenter IP) — trust the hosted run's numbers. ⚠ Model stays `claude-opus-4-8`: Fable 5.1
> carries the same `cyber` refusal classifier that no-op'd the pipeline on Fable 5.
> **PUBLISH BUTTON** (`2026-08-29.publish`): the hosted site gets its site list from the
> COMMITTED `deep-research/config/drt_sources.json` baked in at deploy, so a local edit reaches
> it only via commit+push. "🚀 Publish to centralindustrial.ai" (Sources panel, LOCAL-ONLY) =
> `POST /api/deep_research/publish_sources`: copies the local list into the persistent clone
> **`D:\_______Claude\Central-Industrial`** (auto-cloned if missing; `DRT_REPO_DIR` overrides),
> `git pull --ff-only` → stage ONLY that file → commit → push → Render auto-deploys (~2 min,
> which restarts the hosted container). Hosted refuses it (403). `/api/health` exposes
> `sources_hash` — compare local vs hosted to confirm a publish landed. Only the list travels;
> the vault never does.
>
> **THE USAGE PATTERN (canonical):** maintain the site list + saved logins on LOCALHOST →
> localhost searches everything silently (vault) → 🚀 Publish pushes the list to hosted →
> hosted searches 🟢 sites and prompts ONCE (ephemeral) for any relevant 🔴 site → in both
> cases the plan's relevance call decides which sites are searched. Edits made ON the hosted
> site are ephemeral (wiped on redeploy) and hosted never stores credentials.
>
> **Per-site credentialed status (verified this session, local vault + Chrome profile, NO bot walls
> hit anywhere):** login+native-search ✓ = reddit, forum.wiimhome, github. Not logged in = openwrt
> (autofill FAILED — Discourse JS modal), avforums/rivian/seekingalpha (guests, but native search
> works), x (guest, login-gated). Logged in but native search ✗ = wilderssecurity (XF1 markup →
> `site:` fallback; candidate for a hand-set `search_url`). Fix path for the guests: a one-time
> manual sign-in in the tool's Chrome profile (`D:\_______Claude\Deep Research\.drt_chrome_profile`)
> makes their session persist like github/reddit.
>
> **⚠ Chrome profile lock:** a headed run holds `.drt_chrome_profile`; before launching another
> headed browser, kill leftover `chrome.exe` whose cmdline contains `drt_chrome_profile` (test
> runs leave them). Killing them DURING a user's live local run aborts it with `TargetClosedError`.
>
> **Open threads (not started):** (a) the **Signal Sources** catalog artifact (50 sites, 10
> categories) awaits the user's review before wiring in as the all-on candidate pool — the LLM
> per-topic selector (`_select_relevant_seed_sources` / `_select_relevant_credentialed`) already
> exists but only activates above 12 catalog sites; (b) "longer deep-dive extension" depth mode
> (user chose it) not yet built; (c) **Skyvern-style AI login** flagged as the one useful idea
> from the user's "agentic tools" brief — would fix selector-based login failures (openwrt/wilders).
>
> ---
>
> **UPDATE 2026-08-29 (3):** **Refusal safety-net.** If a Claude safety refusal degrades a
> run (`stop_reason=='refusal'` — otherwise silently no-ops the pipeline), the tool now PAUSES
> at that exact call and prompts: **"Switch to local model"** (UI confirms a model is loaded via
> the existing `/local_model` probe → confirm → the wrapped `ClaudeClient` swaps to LM Studio and
> RETRIES the refused call, so research resumes from the point of refusal; all later calls route
> local too) or **"Stop & assemble what we have."** Local-chosen-but-LM-Studio-down falls back to
> stop-and-assemble (never hangs). Keystone = `ClaudeClient` in `engines/research/llm.py`
> (`refusal_hook` = blocking UI pause, `on_stop` = halt callback; wrapper owns the stop decision
> so a failed swap can't spin on empties). Server: `_DR_REFUSAL` event + `job["refusal_decision"]`
> + `POST /api/deep_research/refusal_choice {mode: local|abort}` + `awaiting_refusal_choice` in
> status. Build `2026-08-29.refuse`, pushed. ⚠ **Config BOM trap discovered:** PowerShell
> `Set-Content -Encoding utf8` writes a UTF-8 BOM → `json.load` throws → `drt_models.json`
> overrides silently ignored → falls back to defaults. ALWAYS write that file via the Write/Edit
> tool or Python (`open(...,encoding='utf-8')`), never PS `Set-Content -Encoding utf8`.
>
> **UPDATE 2026-08-29:** (1) **Model switched Fable 5 → `claude-opus-4-8` at ALL points** —
> Fable's elevated cyber classifiers were REFUSING legit research topics that brush LLM-
> security/jailbreaking subject matter (reproduced: `stop_reason=refusal, category=cyber`,
> empty content), and the wrapper didn't detect refusals, so the whole pipeline silently
> no-op'd (0 searches/0 pages/"pipeline complete"/empty report). Opus 4.8 lacks those extra
> classifiers; identical API shape so the `ClaudeClient` wrapper is unchanged. Model id lives
> in `config/drt_models.json` + `models.py` + `odysseus/llm_core.py` + the two ody workers.
> Verified live end-to-end. (2) **STOP button added** — during a run the progress view shows
> ⏹ Stop; pressing it shows a two-choice panel: **“Assemble report from what's gathered”**
> (halts gathering, synthesizes the partial harvest) or **“Discard & start a new query”**
> (aborts, no report, back to the form). Mechanism: per-job `_DR_STOP` threading.Event +
> `job["stop_mode"]`; `POST /api/deep_research/stop {mode: assemble|abort}`; `stop_event`
> threaded through `run_search`→`_run_browser_stage` (breaks the turn loop, pipeline-level so
> later stages skip) and `run_local_baseline`; worker branches on mode after `run_search`
> returns the partial harvest; status payload carries `aborted`. Build marker
> **`2026-08-29.opus`**. Both changes pushed to deployed.
>
> **STATE AT PRIOR SESSION CLOSE (2026-08-11 evening):** local AND deployed at parity on
> repo commit `b0bb0b7`, health build marker `2026-08-11.dar3`. The complete rebuild is
> live on both: Fable 5 @ medium via the `ClaudeClient` wrapper · browser-only search ·
> link-crawling · native forum_search (`search_url` templates) · register_forum discovery ·
> time-boxed depths **Standard ~5 min / Deep ~10 min / Deep + Odysseus ~15 min chain** ·
> elastic budgets + `request_extension` · full-granularity live activity feed · doc-intake
> analysis · signal-graded citations · "How to go deeper" · organic length · VVD removed ·
> engine-optimized keyword queries · Save&Open downloads the .docx when the server can't
> launch Word (hosted). First real user test run was in flight on the hosted instance at
> close. Open items: register `search_url` templates for favorite forums; curated source
> list is thin (reddit/x/github); `DRT_TRACE=1` still on locally; local :5006 sometimes runs
> under system python (works, but launcher/restart-helper interplay worth a look); watch for
> concurrent-session push races (verify `git push` landed by comparing remote SHA).

## What this is

**Deep Research** is the browser-driven, multi-stage general-purpose web-research tool — **extracted from the DDDD "Admin Tools" monolith and rebuilt as its own standalone local app** under Special Projects. Enter a research question → it opens a **visible Chrome**, fans out across web search + browser engines + your curated/credentialed sources, and synthesizes a **cited research report** (markdown on screen + a downloadable `.docx`), with a collapsed harvest-audit trail.

- **Folder:** `D:\_______Claude\Deep Research\`
- **URL:** `http://127.0.0.1:5006/`  (served by the tool itself)
- **Front door:** the Special Projects hub (`http://127.0.0.1:5050/`) — click **DEEP RESEARCH**; the hub starts it on demand if it's down.
- Migrated 2026-06-27 from `D:\_______Claude\_______Claude_old_workPC\` (the old work-PC copy of `hedge_fund_dd` + the `deep-dive-due-dilligence` SPA).

## The key architectural change vs the old monolith

| | Old (DDDD platform) | New (standalone) |
|---|---|---|
| Frontend | One slice of a 6,581-line SPA on **GitHub Pages (HTTPS)** | Own `index.html`, **served by the backend** |
| Backend | A ~480-line slice of the 127KB `perf_server.py` (:5002), shared with triage / performance / meeting-notes / Odysseus | Standalone `dr_server.py` (:5006), DRT only |
| Origin | Cross-origin (HTTPS page → localhost), needed CORS | **Same-origin** (page + API both on :5006) |
| Engine | `engines/research/` inside the monolith | **Copied verbatim** — zero code edits |

The engine (`engines/research/`) was already fully self-contained (it imports nothing from sibling tools or the server; it only reads files from `config/`, `prompts/`, the vault, and `.drt_chrome_profile/`, all via `dirname×3(__file__)` → repo root). **Preserving the folder layout** (`engines/research/` two levels under root, with `config/` + `prompts/` as root siblings) makes that path math resolve unchanged — so the engine moved with no modification.

## Files in `D:\_______Claude\Deep Research\`

| File | Role |
|---|---|
| `dr_server.py` | The standalone Flask app on **:5006**. Serves `index.html` + `/api/deep_research*`. Carries the DRT routes/worker + the two shared helpers it needed (`_extract_file_text`, `_memo_to_docx_bytes`) lifted from `perf_server.py`. `.env` guard (only sets missing/empty keys). `no-store` headers. |
| `index.html` | The Deep Research UI, lifted from the SPA's Admin Tools workspace. Same-origin (`PERF_SERVER=''`). Vendored libs (no CDN). Top bar links back to the hub. |
| `index.html` | Two views in one page via a **left sidebar**: Deep Research (default) + **Odysseus** (sub-tool). Phosphor-green terminal theme (shared with Monkey Read Monkey Do). Renders DR into `#ws-main-inner`, Odysseus into `#or-main-inner` (independent views; `setView()` toggles). |
| `engines/research/` | The pipeline, **copied verbatim**: `agent.py` (planner + 4-stage search loop), `api_search.py` (Stage 1 Claude web_search), `browser.py` (headed-Chrome harness), `synthesize.py` (per-page extract + cited report + deepen loop), `login.py` (Fernet vault + autofill), `exa_search.py` (optional neural search), `models.py` (per-role model map). |
| `engines/odysseus/` | The **Odysseus** sub-tool's engine — vendored Alibaba IterResearch loop, **copied verbatim** (self-contained; headless: DuckDuckGo + `curl` page-fetch on the same Anthropic key, `claude-sonnet-4-6`). |
| `config/` | `drt_sources.json` (seed sources), `drt_blocklist.json` (anti-SEO), `drt_models.json` (per-role models), **`.drt_vault_key` + `drt_credentials.enc`** (the encrypted login vault — copied over, decrypts fine). |
| `prompts/deep_research.md` | The governance prompt (Part 1 search strategy / Part 2 output framework) = the editorial guidelines for the output. In-app editable via the **⚙ Preferences** button (top-right of the Deep Research page → `drTogglePrefs`/`drSavePrompt` ↔ `GET`/`POST /api/deep_research/prompt`); rolling `.bak`. Loaded fresh each run. |
| `vendor/marked.min.js` | Markdown renderer (vendored — no CDN). |
| `fonts/` | Inter + JetBrains Mono, vendored locally (`fonts.css` + 47 `.woff2`) so the UI works fully offline. |
| `.venv/` | Per-tool venv (Python 3.14). DRT deps only — **no torch/whisper/audio**. |
| `launch.py` + `Deep Research.vbs` | Idempotent, **server-only** launcher (starts `dr_server.py` via the venv pythonw if :5006 is down; does NOT open a browser — the hub is the front door). |
| `restart_helper.py` | Detached helper for the in-app "Restart Server" (force-frees :5006, relaunches, verifies bind). |
| `.env` | `ANTHROPIC_API_KEY` (+ optional `DRT_EXA`/`EXA_API_KEY`). **Secret, per-machine, gitignored.** |
| `requirements.txt` | DRT-only deps. |

## The pipeline

`Stage1` broad baseline **browser** sweep (wide engine searches in the same visible Chrome, semantic rerank, no page opens) → `Stage2` browser engines (DuckDuckGo/Brave/Google) → `Stage3` already-credentialed sources (vault) → `Stage4` new gated sources (batched in-app credential prompt) → **synthesize** (per-page goal extraction + junk filter, cited report, evolving-report + stop-judge deepening loop) → **report** (+ collapsed harvest audit). Depth = quick / standard / deep. **Models (2026-08-11): EVERY role = `claude-fable-5` at effort `high`** (enforced in `engines/research/llm.py` `ClaudeClient`; level override via `DRT_EFFORT` in `.env`; per-role ids still overridable in `config/drt_models.json` → restart).

## 2026-08-11 session (3) — THE DEEP ACTUAL RESEARCH REBUILD (fleet-built)

Full rebuild of the gathering half around the user's four precepts (now the **Charter** at the
top of `prompts/deep_research.md`): (1) succinct question → crawl/assimilate/crawl → directly
responsive output; (2) mine discussion forums engines can't see, incl. behind credential walls,
and DISCOVER new ones; (3) payoff-weighted iteration — hot trails earn budget; (4) signal-graded
citations. Built by three parallel agents (engine core / synthesis / server+UI), integrated and
live-verified same day. LOCAL ONLY.

- **Crawling**: `open_page` results now include "LINKS ON THIS PAGE" (≤20, filtered/deduped) —
  the agent follows citation trails, thread replies, pagination. Page text to agent 3.5K→8K chars.
- **Native forum search**: sources may carry `search_url` (template with `{q}`; new column in the
  Sources table). New `forum_search` tool drives the site's OWN search in the logged-in Chrome
  (`DRTBrowser.site_native_search`); engine `site:` fallback when no template. The vault finally
  pays off: login → native search → open threads → follow links.
- **Discovery**: `register_forum` tool → `HarvestResult.discovered_forums` → "Discovered
  communities" panel in results with one-click Add-to-sources.
- **Elastic budgets**: `DEPTH_BUDGETS` are now GLOBAL pools (quick 30s/40p, standard 80s/100p,
  deep 200s/250p) with soft stage allocations; `request_extension` tool auto-grants +4s/+6p from
  the pool when the agent is hot (all grants/denials logged/audited). Stage 4 keeps small fixed
  per-source budgets so fresh credentials always get used.
- **Live activity feed**: right-side panel in the UI streams every engine event ([search]/[open]/
  [forum]/[extension]/[note]…) via per-job event buffers in `_JOBS` + `?after=<seq>` cursor on all
  three status endpoints. `[note]` lines (the model's own inter-tool commentary) are highlighted.
- **Signal-graded citations**: extraction (Pass A) also returns `source_class`
  (primary-document/official/news/expert-analysis/forum-discussion/anecdote/marketing/other) +
  `content_date`; Sources render `7. [Title](url) — *forum-discussion · 2026-05*`; single-source
  claims flagged inline; **unopened Stage-1 URLs are no longer numbered/citable** — they render as
  an unnumbered "Further leads (surfaced, not read)" list.
- **Momentum loop**: stop-judge asks "is the SPECIFIC question answered with primary evidence";
  gap queries continue the hottest trail, not just fill coverage gaps.
- **"How to go deeper"**: `synthesize.deeper_assessment()` appends a closing section (hottest
  unexplored trails / locked doors / communities to mine / sharper follow-up queries) to every
  report + `result["go_deeper_md"]` rendered as its own framed panel by the refine CTA.
- **Effort switched high→medium** (`DRT_EFFORT=medium` in `.env`; wrapper + Odysseus adapter
  defaults). Browser pacing `DRT_SLOWMO` (default 80ms, was 250).
- Stage prompts rewritten goal-first (de-prescribed for Fable). Governance §1.3/§1.5/§2.3
  updated (native search + link-following, effort-follows-payoff, graded citations).

**Same-day rework (user directives after shakedown):**
- **Very Very Deep sub-tool REMOVED** (user request): sidebar entry, `#view-vvd`, the whole
  `_vvd*` JS module (~268 lines), the `/api/very_very_deep[/status]` routes, `_vvd_worker`,
  the now-dead `_dr_pipeline` refactor, and the `/vvd-egg.mp3` route (~310 server lines).
  The easter-egg mp3 FILE stays on disk (user's music, just unrouted). Odysseus + Firecrawl
  sub-tools remain. §"Very Very Deep" section below is now HISTORICAL.
- **"Deep + Odysseus" CHAINED depth** (user request, evolved same day; repo commit
  `3696626`, build marker `dar2`): the third depth chip is now a chain. Selecting it
  (`raw_depth == "odysseus"` branch in `/api/deep_research`, before `normalize_depth`)
  runs `_ody_depth_worker`: **Pass 1** = the headless IterResearch engine at its standard
  setting (4 rounds, 300s, clarifications appended to its query; `~ ody` events stream
  "Pass 1 · Odysseus — …"); **Pass 2** = the ody report is appended to `doc_parts`/
  `doc_context` as `[Odysseus pre-research findings]` and the SAME `_dr_worker` runs at
  the **deep** tier — so the document-intake analyst (Fable, standard effort) distills the
  pre-research into search-enhancing grounding for the plan + browser stages, and its
  full text reaches synthesis as trusted user_docs, exactly like a dropped file. One job,
  one event stream, stages = `["odysseus"] + DR_STAGES` (UI locks the list from the start
  response). Ody failure degrades to a plain deep run. ~15 min total. The standalone
  Odysseus sidebar view still exists. The `llm_core` Fable adapter was live-verified.
- **Depth is now TIME-boxed, two tiers**: `standard` (best outcome in ~5 min: 300s total,
  100s synthesis reserve) and `deep` (~10 min: 600s/130s). `DEPTH_BUDGETS` carries
  seconds/reserve + unit pools as safety ceilings; `normalize_depth()` maps legacy
  quick→standard / verydeep→deep. Workers compute `run_deadline`, pass `deadline=` (gathering window)
  into `run_search`; stage time shares (stage2 ≈60% of remaining window) are soft —
  `request_extension` now also grants **+60s on the clock** (never past the hard window);
  every tool-result footer shows `⏱ M:SS left`; deepen rounds only run on leftover time
  (`run_deadline - 75s` guard). Stage 4 gets a 60-90s grace window for fresh credentials.
- **Activity feed granularity**: new event kinds `turn` (agent weighing next move — fires
  every model turn), `act` (every tool call + compact input), `hit` (top-3 search results
  cascading), `extract` (per-page ✓class/✗no-signal verdicts as Pass A lands), `synth`
  (stop-judge/gap/meta lines — workers now pass `log=` into synthesize/stop_judge/
  gap_queries). UI `ACT_KINDS` maps the new kinds; depth chips = Standard (~5 min) /
  Deep (~10 min) in DR + VVD.
- **Uploaded-document ANALYSIS step** (`_analyze_docs` in dr_server, audit + fix): dropzone
  files were extracted/parsed but raw-dumped into the agent's context (planner saw only the
  first 800 chars). Now each file gets one Fable call (wrapped client → standard effort)
  distilling it against the question — key facts, entities/terms, open questions, 2-4 search
  leads — and the BRIEF grounds planning + the browser stages, while the FULL raw text still
  reaches synthesis as trusted `user_docs`. Runs pre-clock (doesn't eat the research window);
  per-file `[docs]` events stream to the activity feed (kind `docs`, glyph ▤); analysis
  failure degrades to a raw excerpt, never blocks the run. DR path only (VVD grounds docs
  via its Odysseus pass as before).

## 2026-08-11 session (2) — Fable 5 High everywhere · organic length · browser-only search

Three user directives. **SYNCED TO DEPLOYED 2026-08-11** (repo commit `6de1862` — the full
rebuild incl. everything in this section and the rework block below was ported to
`deep-research/` with the auth gate + Render adaptations preserved: gate/PORT/HOST/
`DRT_REPORTS_DIR`/`__HUB_URL__` grafts test-verified via Flask test client before push;
`browser.py` now env-driven in BOTH copies — `DRT_HEADED`/`DRT_BROWSER_CHANNEL`;
`fastembed` added to deployed requirements with the rerank model pre-baked in the
Dockerfile; `/api/health` carries a `build` marker, currently `2026-08-11.dar1`):

**1. All Claude calls → `claude-fable-5` at effort `high`.** All five roles in
`models.py`/`drt_models.json`, the clarify pass, and the Odysseus adapter (`llm_core.py`) now
run Fable 5. The keystone is **`ClaudeClient`** in `engines/research/llm.py` — `make_client`
returns it for the claude provider and `dr_server`'s workers now build clients through it. On
every `messages.create` it: injects `output_config={"effort": DRT_EFFORT}` via `extra_body`
(default `high`, override in `.env`); strips `temperature` (400 on Fable 5 — stop-judge and
gap-queries used to pass it); never sends a `thinking` param (always-on for Fable 5, explicit
config rejected); floors `max_tokens` at 4096 (thinking tokens count against the cap on
Fable 5). Verified with a live API call (temperature stripped, effort accepted, floor
engaged). ⚠ Cost: extraction volume that ran on Haiku now runs at Fable pricing.

**2. Organic output length codified.** Governance prime directive + §2.1 rewritten: length
tracks high-signal yield in BOTH directions — near-zero yield → effectively-zero output;
rich detailed findings → 10+ pages. Mirrored in `_SYNTH_SYSTEM`; synthesis `max_tokens`
now 16384 always (was 4096 without user docs).

**3. ALL web search is browser-based.** Stage 1's Anthropic server-side `web_search`
(sandboxed, robots.txt-bound) is RETIRED — `run_local_baseline` (broad Chrome engine
sweeps + fastembed rerank → brief) is now Stage 1 for every provider; the browser starts
before Stage 1. `api_search.py` kept only for `_load_blocklist` + rollback reference.
Plan/audit/UI labels renamed "Claude API search" → "Baseline sweep". Still non-browser by
design: Exa (neural URL *discovery* only — pages are read in Chrome; plus fetch-fallback
when Chrome itself is blocked) and the **Odysseus sub-tool** (vendored ddgs + httpx fetch,
headless comparison engine — flagged to the user, pending decision).

## 2026-08-11 session — Subject-matter neutralization sweep (local + DEPLOYED)

The tool descends from a financial-DD platform; per the standing neutral-framing rule
([[feedback-special-projects-neutral-framing]]) all remaining finance/investment/DD framing was
stripped from prompts, UI, comments, and configs — **both** the local tool and the deployed
`nurtrino/Central-Industrial` `deep-research/` service (commit `8250fc7`, auto-deployed):

- **Query-box placeholders removed** — the deployed DR box still had the old
  "background, track record, and any controversies…" example (local was already blank);
  Odysseus + Very Very Deep placeholders trimmed to their non-example halves in both copies.
- **Governance prompt** (`prompts/deep_research.md`): "Analyst voice" → "Research voice" with a
  neutral (recall-count) example replacing the revenue one; "named, reputable analysts" →
  "…experts"; the Market-row and §1.3 "Independent analysts" wording generalized.
- **Stage-1/local-baseline prompts + Exa tool description**: "paywalled analysts, primary
  filings" → "paywalled specialists, primary documents"; "analyst writeup" → "specialist writeup".
- **Smoke-test defaults**: Renaissance/Medallion queries → JWST / Starlink ones.
- **Vestige comments/identifiers**: DDDD/DD references in `dr_server.py`, `agent.py`,
  `api_search.py`, `synthesize.py`, `models.py`, `odysseus/__init__.py`, both config `_comment`s,
  the `.env` header, and the Firecrawl Extract example ("company… total funding" → page metadata);
  `_memo_to_docx_bytes(manager_name=…)` param renamed `label`.

## 2026-07-18 session — Local-LLM provider switch, Extract, tracing, fixes

Big additions this session (all on the local :5006 tool; some also **ported to the deployed repo** `nurtrino/Central-Industrial` — additively, gate/8h-TTL preserved. Local files are **CRLF**, the repo is **LF** — normalize when porting).

**1. "Extract a source" (scrape/crawl) — local + DEPLOYED.** After a run, a CTA next to "Go deeper?": pick a surfaced URL (or paste one) → **Scrape** one page or **Crawl** the site (capped 1–50). Chain **Firecrawl v2 → Exa contents fallback**. Backend `_firecrawl_scrape`/`_firecrawl_crawl`/`_exa_extract_fallback`/`_extract_worker` + `/api/deep_research/extract[_status]`; frontend `_drExtractPanel()`/`drExtract*`. (Tavily declined — no key.)

**2. Claude ⇄ Local AI provider switch — local + DEPLOYED.** Toggle at the top of the DR page routes EVERY AI call to Claude OR a local model in **LM Studio** (OpenAI-compat `http://127.0.0.1:1234/v1`, override `LMSTUDIO_URL`). Keystone = **`engines/research/llm.py`**: `make_client(provider)` → `anthropic.Anthropic` OR `LMStudioClient` (drop-in `messages.create` translating Anthropic⇄OpenAI incl. tool-use; `model=` ignored locally, uses whatever's loaded via `/v1/models`; strips `<think>` blocks). Threaded: frontend → clarify + `/api/deep_research` → `_dr_worker`/`_dr_clarify` → `run_search(client=, provider=)`. New `GET /api/deep_research/local_model`. **Deployed Local-AI only works if the user tunnels LM Studio (ngrok/cloudflared → :1234) and sets `LMSTUDIO_URL` in the Render `deep-research` env** — else it fails gracefully ("not connected"). Exposing LM Studio publicly = security consideration (no auth on that endpoint).

**3. Local-model findings (from real runs).** Qwen3-Coder-30B-A3B-Instruct (abliterated) drove the whole pipeline end-to-end (plan → tool-calling browser agent → extraction → cited report). Need an **instruction-tuned, tool-calling** model (Qwen2.5/3-Coder-Instruct, Mistral, Llama-3.x) — roleplay/"uncensored-aggressive" fine-tunes fail at faithful extraction ("nothing found"). **Speed:** local runs are SLOW (~20 min/quick) — **memory-bandwidth-bound** because a 256K context reserves ~32 GB KV cache. Fix: **load the model at ~32K context** in LM Studio (this pipeline never needs 256K), keep it fully in VRAM. Use **127.0.0.1** not `localhost` (IPv6 `::1` trap on Windows).

**4. UI fixes (local only — NOT yet synced to deployed).** Removed the long example **placeholder** from the query box. **Stage-label fix:** `stage1` renders "Planning" (not "Claude web search") in Local mode / when the Claude API-search channel is off (`_drProgressHtml`, provider/channel-aware).

**5. Verbatim trace mode (`DRT_TRACE=1`).** `agent.py` `_trace()` writes `traces/<ts>_<provider>.trace.txt`: plan + every browser-agent turn (model reasoning + each tool call/input + result snippet). **Currently ON in `.env` — turn it off when done diagnosing.**

**7. Semantic rerank** (`engines/research/rerank.py`, added this session). Reranks a wide pooled result set by **cosine similarity to the research question** using a small local embedding model (**fastembed** → ONNX/CPU, no torch; default `BAAI/bge-small-en-v1.5`, ~130 MB one-time download; graceful identity fallback on any failure; disable with `DRT_RERANK=0`). Wired into `run_local_baseline` so the local baseline's brief + surfaced-URL list lead with the highest-signal hits (validated: correctly ranks on-topic results above noise). Added `fastembed` to `requirements.txt`. **Candidate for the depth/width redesign:** applying rerank to Stage-2 per-search results too — deferred because reranking narrow sub-searches against the overall question has a breadth-vs-focus tradeoff worth designing deliberately.

**6. Local Stage-1 baseline** (`run_local_baseline`, added this session). Local mode dropped Stage 1 (Claude server-side web_search). Replaced with a local-native equivalent: the local model runs a few **broad** browser searches, collects surfaced URLs, and writes a Stage-1-style brief, returned in the same `{findings_md, sources, used}` shape so the rest of the pipeline is unchanged. Shallow-broad (no page-opens) to complement — not duplicate — Stage 2's deep browse.

### Pending / next
- **`DRT_TRACE=1` is ON** → flip off when done.
- **Local-only UI fixes** (placeholder, stage label) not synced to deployed.
- **BIG OPEN DISCUSSION (user teed up 2026-07-18):** *Is Deep Research actually deep + wide enough?* Concern: not going **wide** enough to surface high-signal sources, and not going **deep** enough on the key facts once found. Design next session — breadth (more/smarter queries, more engines, better source discovery) + depth (multi-hop follow-up + verification on key claims).

## Firecrawl — sub-tool (added 2026-06-28)

A data-driven **console over the Firecrawl v2 REST API**, rebuilt from `D:\_______Claude\Firecrawl\HANDOFF.md` (the original source from the other machine didn't come across — only the handoff did).

- **UI:** fourth left-sidebar item (Deep Research / Odysseus / Very Very Deep / **Firecrawl**), own `#fc-main-inner` view + back button. A tab row of the 11 tools (Scrape · Search · Map · Crawl · Crawl Status · Batch Scrape · Batch Status · Extract · Extract Status · Generate llms.txt · llms.txt Status); each tool's form is generated from the `FC_TOOLS` array (field types: text/number/textarea/lines/json/bool/multi). Results render markdown when present + a collapsible raw-JSON view. Async tools return a job id with a **"Check status →"** button that jumps to the matching Status tool. A **⚡ Test connection** button (top-right) hits `map`. All `_fc*` functions live in the same `index.html`.
- **Backend:** generic proxy **`/api/firecrawl/<path>`** in `dr_server.py` → forwards to `https://api.firecrawl.dev/<path>` with `Authorization: Bearer $FIRECRAWL_API_KEY` injected server-side (the key never reaches the browser). Pass-through JSON + status. Uses `requests` (already in the venv).
- **Key:** `FIRECRAWL_API_KEY` in `Deep Research/.env` (the handoff key, valid as of 2026-06-28). Rotate at the Firecrawl dashboard → update `.env` → restart.
- **Verified 2026-06-28:** live `POST /api/firecrawl/v2/map` returned `success:true`; the console renders all tabs/forms and a live Map result; no console errors.
- **Not included (separate concern):** the handoff's **Firecrawl MCP** registration for Claude Code (`~/.claude.json`) — that's a Claude Code integration, not part of this sub-tool.

## Saving reports (auto-save on completion + Save & open in Word)

**Auto-save (added 2026-07-16):** every finished run is written to disk **immediately on completion**, server-side, before the result even reaches the browser — no click needed. Each worker (`_dr_worker`, `_ody_worker`, `_vvd_worker` — the latter saves **both** documents) calls `_autosave_report(docx_b64, query, label)`, which writes to **`D:\______Documents\___Deep Research Reports\`** with the same dated filename format as the manual save (`<label> — <keyword slug from the query> — YYYY-MM-DD_HHMMSS.docx`), does **not** open Word, and never raises (returns `''` on failure — the in-browser copy and the Save button still work). The saved path is returned in the result (`saved_path`, plus `odysseus_saved_path` for Very Very Deep) and shown as a "💾 auto-saved: …" badge in the results toolbar (`_savedBadge` in `index.html`).

The results "💾 Save & open in Word" buttons (Deep Research, Odysseus, and both Very Very Deep docs) **do not** browser-download — they POST the `.docx` to **`POST /api/save_report`** `{docx_b64, query, label}`, which writes it to **`D:\______Documents\___Deep Research Reports\`** with a topic-keyword filename (`<label> — <keyword slug from the query> — <timestamp>.docx`) and **opens it in Word** via `os.startfile` (the `.docx` association). A toast shows the saved path. If the save endpoint fails (folder not writable, server down), the frontend **falls back to a browser download** so the file is never lost. Helpers: `_slug_from_query` / `_safe_filename` / `_autosave_report` (server), `saveAndOpen` / `_blobDownload` / `_toast` (client). Note: Word opens in the session the server runs in — normally the user's (hub/Startup launch); the file is always saved regardless. Since the auto-save already happened, clicking the button writes a **second copy** (later timestamp) and opens it — the button is now effectively "open in Word".

## What was migrated / what was left behind

**Brought over:** the engine, all DRT config + the governance prompt, the **API key** (`.env`) and the **credential vault** (`.drt_vault_key` + `.enc`, copied as a pair — verified decrypts to 8 saved domains). **Started fresh (by choice):** `.drt_chrome_profile/` — created on first run; log into gated sites again as needed.

**Left behind in the monolith** (not part of Deep Research): Meeting Notes, Slide Capture, Audio Recording, Triage (VC/PC/FSR), Performance/Attribution, 13F. Their heavy deps (torch, faster-whisper, pyannote, soundcard, lameenc, pywin32, yfinance, scipy) were dropped from `requirements.txt`.

## Odysseus Research — sub-tool (migrated 2026-06-27)

Odysseus is a **headless A/B comparison engine** for Deep Research: the vendored Alibaba **IterResearch** loop, run on the **same Claude key** but a different methodology (DuckDuckGo search via the `ddgs` lib, page-fetch via system `curl`, prompt-injection guard, SSRF guard). It can't log into gated sites or be watched in a browser — that's Deep Research's edge; Odysseus is purely for comparing *methodology*.

- **UI:** a sub-tool of Deep Research, **not** its own hub tile. Left-sidebar nav switches Deep Research ⇄ Odysseus; the Odysseus view has a **← Deep Research** back button. Reuses Deep Research's controls/styles (already green). Lives in the same `index.html` (the `_or*` functions + `setView()`).
- **Backend (in `dr_server.py`):** `POST /api/odysseus_research` + `GET /api/odysseus_research/status` → `_ody_worker` runs `engines.odysseus.deep_research.DeepResearcher(...).research(query)` in a thread (its own asyncio loop), reusing `_JOBS`/`_memo_to_docx_bytes`. Depth = rounds (Quick 2 / Standard 4 / Deep 8).
- **Deps:** all already present except **`ddgs`** (added to the venv + `requirements.txt`). `curl` is the system one (Windows ships it). Engine is self-contained (zero cross-package imports, no config/prompt files).
- **Optional env:** `ODYSSEUS_SEARCH_PROVIDER` (default `duckduckgo`), `BRAVE_API_KEY`, `TAVILY_API_KEY` — none required.

## Very Very Deep — sub-tool (added 2026-06-27)

A **chained two-pass** deep dive: **Pass 1** runs an Odysseus pre-research pass on the question; **Pass 2** injects Odysseus's findings (+ the original query + any uploaded docs) as grounding context into the **full Deep Research pipeline**, which verifies/deepens with the browser and writes the final report per the Deep Research governance.

- **UI:** third left-sidebar item (Deep Research / Odysseus / **Very Very Deep**), own `#vvd-main-inner` view + back button. **One query field** at top, then Pass-1 (Odysseus rounds) + Pass-2 (Deep Research depth + channels) + supporting-docs dropzone. Two-phase progress (Pass 1 Odysseus → Pass 2 Deep Research stage list, with a skip-stage button). The `_vvd*` functions live in the same `index.html`.
- **Output = TWO separate documents** (2026-06-27): *Document 1* — the combined/edited Deep Research report (+ its own `.docx`); *Document 2* — the Odysseus pre-research **standalone** (+ its own `.docx`). Backend returns `odysseus_report_md` + `odysseus_docx_b64` alongside the combined `report_md`/`docx_b64`; the combined report no longer embeds the Odysseus dump (it's its own document now).
- **Backend (`dr_server.py`):** `POST /api/very_very_deep` + `/status` → `_vvd_worker` runs Odysseus (`asyncio`), builds a `combined_doc` = labeled Odysseus brief + uploaded docs, then calls the shared **`_dr_pipeline()`** (a refactor-free extraction of `_dr_worker`'s pipeline body — the proven `_dr_worker` is untouched, so plain Deep Research can't regress). The Odysseus pre-pass is appended to the report as a collapsed `<details>` (and a docx section).
- **Unattended:** the Pass-2 DR run **auto-skips new gated-login (Stage 4) sources** (vault Stage-3 logins still apply) so the chain runs end-to-end without pausing. Reuses `/api/deep_research/skip_stage` for the in-progress skip button.
- **Cost/time:** the longest run — two research passes back-to-back, both spend API credits; Pass 2 opens a visible Chrome.
- **Easter egg:** pressing the Very Very Deep sidebar button plays `Boonies Basement Tub (128kbit_AAC)-2.mp3` (served at `/vvd-egg.mp3`; `vvdEgg()` restarts it on each press).

## Run / verify

- **Via the hub:** open `http://127.0.0.1:5050/`, click **DEEP RESEARCH** → starts on demand, opens the tool.
- **Direct:** double-click `Deep Research.vbs`, then browse to `http://127.0.0.1:5006/`.
- **Manual (see errors):** `cd "D:\_______Claude\Deep Research"; .venv\Scripts\python.exe dr_server.py`
- Sanity (no API cost): `/api/health` → `{"ok":true}`, `/api/deep_research/sources`, `/api/deep_research/vault` (lists saved domains), `/api/deep_research/prompt`.

**Verified 2026-06-27:** server boots under the venv via the launcher; UI renders (vendored fonts + marked, phosphor-green theme); sources/vault/prompt endpoints all return; hub shows **DEEP RESEARCH [ ONLINE ]**; the Deep Research quick search works (user-confirmed); the Odysseus sidebar switch / back button / route all verified (engine imports under the venv; `POST /api/odysseus_research` registered). **NOT yet exercised end-to-end:** (1) a deep/standard DR run with the browser stages; (2) a full **Odysseus** run (headless web + API credits) — run both from your own session to A/B them.

## Open items / gotchas

- **Playwright / Chrome:** the engine uses `channel="chrome"` = your installed Google Chrome (present at the standard path), so no chromium download is needed. If Playwright ever asks for `playwright install`, run it in **your own terminal** (its default cache is `%LOCALAPPDATA%\ms-playwright`, which is **virtualized/invisible** to the Claude desktop-app sandbox) — or set `PLAYWRIGHT_BROWSERS_PATH` to a `D:\` location. See [[env-claude-app-sandbox-virtualization]].
- **Needs a funded `ANTHROPIC_API_KEY`** — the agent loop + synthesis are API calls even with channel toggles off. Console/API billing is separate from any Claude.ai/Max plan.
- **Optional polish (your call, run from your own shell):** a `deepresearch://` protocol `.reg`, and/or a `shell:startup` shortcut. Neither is required — the hub starts the tool on demand.
- **Relationship to the old DRT (:5002):** the full DDDD monolith at `D:\_______Claude\hedge_fund_dd` ([[project-drt]]) still exists and is unchanged; this is an independent extraction, not a replacement of it.

## Related memory

`[[project-deep-research-standalone]]`, `[[local-tools-host-convention]]`, `[[project-drt]]`, `[[env-claude-app-sandbox-virtualization]]`.
