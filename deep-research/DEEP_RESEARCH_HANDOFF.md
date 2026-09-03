# Deep Research — Handoff

_Last updated: 2026-09-03 (parallel lanes / 1-hour tier / Bright Data / Tavily+Exa session)_

> ## ⭐ STATE AT 2026-09-03 SESSION CLOSE — read this first (LOCAL + HOSTED at parity)
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
