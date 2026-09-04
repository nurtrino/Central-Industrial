# DEEP ACTUAL RESEARCH — Upgrade Guide for Sibling Tools

_Written 2026-08-12, from the completed rebuild of the reference implementation
(“Deep Research”, Flask + Playwright + Claude). Audience: an AI agent (or engineer)
revising **another instance of a deep-research tool built on the same basic
framework** — a Flask-style job server, a Playwright browser harness, a staged
search pipeline, an LLM synthesis stage, and a single-page UI — to bring it up to
this level. Everything here is framework-relative: adapt names, keep the shapes._

---

## 0. The charter — internalize this before touching code

Every change below exists to serve four precepts. Put them (adapted, subject-matter
neutral) at the top of the tool’s editable governance prompt, loaded fresh每 run:

1. **Directly responsive beats comprehensive.** The user gives a reasonably succinct
   topic or question — not a boil-the-ocean think piece. The target is the *specific*
   question; depth on it outranks breadth around it.
2. **The best material is buried.** High-value answers live in active discussion
   communities that engine searches never surface — including behind credential
   walls the tool can log through. Prefer digging where engines can’t see; actively
   *discover* new communities not yet on the curated list.
3. **Chase the hot trail.** Effort follows expected payoff. When most angles return
   little but one line of inquiry is closing in on the answer, that line gets the
   budget. Cold trails stop early; hot trails earn extensions. *Stopping one hop
   short of the answer is the worst outcome the tool can produce.*
4. **Every claim is traceable.** Citations the user can evaluate for relative signal
   strength — source class, freshness, corroboration.

**Meta-principle:** the original framework was built defensively for weaker models —
scripted stages, tiny fixed budgets, choreographed prompts. On a frontier model that
scaffolding *reduces* output quality. The upgrade is largely about **handing the
model goals, evidence, and elastic budgets instead of choreography**, then wiring the
harness so it *can* act on them (links, native search, extensions, time).

---

## 1. Target architecture at a glance

```
UI (single page)                     Server (job registry + workers)          Engine
────────────────                     ───────────────────────────────          ──────
depth chips (time-boxed)      POST → /api/research  ── thread ──►  plan → S1 baseline browser sweep
live activity feed (right)    poll → /status?after=<seq>           → S2 open-web agent loop (crawl,
dropzone (files→analysis)            events: [[seq,kind,text]]        forum_search, register_forum,
discovered-forums panel              per-job ring buffer               request_extension)
go-deeper panel                                                    → S3 credentialed (vault logins)
graded Sources list                                                → S4 new gated (batched creds)
                                                                   → extract (graded) → synthesize
                                                                   → deepen (momentum) → go-deeper
```

Single wrapped LLM client everywhere. TIME is the budget governor. One event stream
per job feeds the UI’s activity panel. All web search happens in the real browser.

---

## 2. Workstreams, in dependency order

### WS-A · One wrapped LLM client (do this first — everything routes through it)

Collapse per-role model tiering: **every call runs the house frontier model** (here:
`claude-fable-5`) at one effort level (default `medium`, env-overridable e.g.
`DRT_EFFORT`). Implement a thin wrapper presenting the provider SDK’s surface
(`client.messages.create(...)`) and enforce the request shape in ONE place:

- inject reasoning effort (for the Claude API: `extra_body={"output_config":
  {"effort": EFFORT}}` — `extra_body` is version-proof against older SDKs);
- **strip `temperature`** (rejected with HTTP 400 on Fable-class models — legacy
  stop-judge/gap calls in the old framework pass it and will break the deepen loop);
- **never send a `thinking` param** (always-on models reject explicit config);
- **floor `max_tokens` at ~4096** — thinking tokens count against the cap on these
  models, so legacy tiny caps (16–400 for classifiers) truncate before any answer.
  A cap costs nothing unless used.

Make `make_client(provider)` return the wrapper; convert every direct SDK
construction in the server/workers to go through it. If the tool has a local-LLM
path (OpenAI-compatible), keep it as a sibling client with the same `.messages.create`
surface. **Verify with one live micro-call** that passes a `temperature` kwarg and a
lowballed `max_tokens` and confirms both were corrected.

### WS-B · Retire server-side web search; browser-only Stage 1

If Stage 1 uses a provider’s server-side `web_search` tool: kill it. Those sandboxed
agents respect robots.txt and get blocked — the exact failure the tool exists to
escape. Replace with a **baseline browser sweep** for every provider:

- model generates N broad queries (see WS-G for query craft) → run them across the
  real browser’s engines (no page opens) → pool unique results → **semantic rerank**
  against the question (fastembed/ONNX-CPU, `BAAI/bge-small-en-v1.5`, graceful
  identity fallback + env kill-switch) → model writes a terse baseline brief in the
  same `{findings_md, sources, used}` contract the old Stage 1 returned, so nothing
  downstream changes.
- Start the browser BEFORE Stage 1 (the old layout started it after).
- Keep the old api-search module only for its blocklist loader; mark it retired.

### WS-C · Teach it to crawl (highest single-change value)

The old harness extracts each page’s links and throws them away — the agent can only
search→open→search. Fix:

- `open_page` tool results append a **“LINKS ON THIS PAGE”** section: ≤20 links,
  filtered (already-seen URLs, junk hosts, anchor text <4 chars), substantive
  anchors first, formatted `L1. <anchor> — <url>`, with a footer inviting follow-ups.
- Raise the page-text window the *navigating* agent sees (3.5K → 8K chars; the full
  text is stored for extraction regardless). The navigator must not be the least-
  informed party in the pipeline.
- Tool description: following promising links from a good page (citation trails,
  thread replies, pagination, an author’s other posts) is often BETTER than another
  engine query.

### WS-D · Native in-forum search (make the credential vault pay off)

`site:domain` engine queries only see what engines indexed — which for login-walled
forums is nothing. The vault login is worthless without this:

- Source entries gain optional `search_url` (template with `{q}`), editable in the
  sources UI.
- Browser harness: `site_native_search(search_url, query)` — substitute `{q}`
  (URL-encoded), navigate **in the logged-in persistent profile**, generically
  extract result anchors (main/article scope, anchor text ≥15 chars, junk/nav
  filtered). Any failure → log + empty list.
- New agent tool `forum_search {domain, query}`: uses the native template when
  registered, falls back to engine `site:` search otherwise (annotate the fallback
  in the result so the feed shows it).
- Keep/add **proactive** vault logins at the credentialed stage (log in up front,
  warn on failure), not just reactive wall-handling.

### WS-E · Forum discovery (the tool gets smarter every run)

- New tool `register_forum {domain, reason}` (open-web stage only): normalize +
  dedup vs curated and already-discovered; append to a `discovered_forums`
  list on the harvest (`[{domain, reason}]`); confirmation tells the agent it can
  search it immediately.
- Stage prompt directive: *“if you find yourself searching or reading a community
  NOT in the curated list, register it — that is how the user’s list grows.”*
  (Without the explicit nudge, frontier models mine the forum but never register it
  — observed in testing.)
- Results UI: “Discovered communities” panel, one-click add-to-sources through the
  existing sources endpoint, dedup + “already in sources” state.

### WS-F · Time-boxed, elastic, payoff-weighted budgets (precept 3’s machinery)

Replace unit-count depth tiers with **wall-clock windows**; keep unit pools only as
runaway ceilings:

- `DEPTH_BUDGETS = { "standard": {seconds: 300, reserve: 100, searches: 120,
  pages: 150, max_turns: 120}, "deep": {seconds: 600, reserve: 130, searches: 240,
  pages: 300, max_turns: 240} }` — `reserve` is held back for extraction/synthesis/
  assessment; the remainder is the gathering window. Provide `normalize_depth()`
  mapping legacy tier names so old clients can’t break.
- The job layer computes `run_deadline = now + seconds`, passes
  `deadline = run_deadline − reserve` into the pipeline. Stage time shares are SOFT
  (open-web stage ≈60% of the remaining window; credentialed stage the rest; the
  batched-credentials stage gets a fixed small grace window of ~60–90s so
  fresh user-typed credentials are always usable even after the clock).
- Every tool-result footer shows the clock (`⏱ M:SS left`) plus stage/pool balances.
- **`request_extension {reason}` tool**: when a stage share or allocation is spent
  but the trail is hot — auto-grant (+~60s on the stage deadline capped at the hard
  window, +4 searches/+6 opens capped by the pool). No judge call: the window and
  pool are the guardrails; log every grant/denial to the feed. When the hard window
  closes: “TIME WINDOW CLOSED — finish NOW with what you have,” and the loop breaks
  before the next model turn, harvest preserved.
- Deepening rounds after synthesis run only on leftover time (`now < run_deadline −
  ~75s`).
- Budget paragraph for the stage prompt (adapt): *“This run is TIME-BOXED — maximize
  the outcome within the clock shown on every tool result. When your time share or
  allocation runs out but the trail is HOT, request_extension buys more; when the
  window truly closes, finish immediately with what you have. With minutes, not
  hours, spend them where the specific answer lives.”*

### WS-G · Query craft: engine queries, not questions

Frontier models naturally emit full-sentence NL questions as search queries; engines
rank compact keyword strings far better. Enforce at EVERY layer that writes a query:

- Query-generation prompts (baseline sweep, gap queries): *“COMPACT KEYWORD STRINGS,
  not sentences — 2-6 terms; lead with proper nouns / product or model names /
  insider vocabulary; no question words or filler; quoted phrases only where exact
  wording matters.”* Include an example shape.
- **Kill the verbatim fallback**: if the old code falls back to firing the raw user
  question at an engine, replace with stopword-stripped keyword compression (+
  `problems` / `review` / `forum` variants) and a hard `?`-strip guard.
- `web_search` / `site_search` tool descriptions carry the same instruction (with
  the why).
- Governance query-craft section: “Write engine queries, not questions” as the FIRST
  rule.

### WS-H · Momentum loop, not coverage loop

- **Stop-judge** prompt: not “is the report comprehensive” but *“Does this report
  DIRECTLY answer the user’s specific question with well-sourced evidence (primary/
  official where they exist)? YES only if answered; NO must carry the single most
  promising direction to push next — partial, weakly sourced, or visibly closing in
  on something it didn’t reach.”*
- **Gap queries** prompt: 2-4 queries that (a) fill the key remaining gap in the
  SPECIFIC answer, or (b) **CONTINUE THE HOTTEST TRAIL** — where the research was
  closest when it stopped. (Keyword-query craft applies — WS-G.)

### WS-I · Signal-graded citations (precept 4)

- Per-page extraction returns JSON `{relevant, nuggets, source_class, content_date}`.
  `source_class` enum: `primary-document | official | news | expert-analysis |
  forum-discussion | anecdote | marketing | other` (describe each briefly in the
  prompt). `content_date` = visible pub/post date, `YYYY-MM` or `YYYY` or "".
  Keep cache backward-compat (legacy bare-string values tolerated).
- **Only opened-with-signal pages get citation numbers.** URLs surfaced but never
  read move to an UNNUMBERED “Further leads (surfaced, not read — NOT citable)”
  block in both the synthesis input and the rendered Sources section. This closes a
  real hallucination surface (the old framework let the model cite unread pages).
- Sources render with grade tags: `7. [Title](url) — *forum-discussion · 2026-05*`.
- Synthesis system prompt: calibrate language to the grade; flag claims resting on a
  SINGLE source inline, e.g. “(single-source: one forum thread)”.

### WS-J · Uploaded documents: extract → parse → ANALYZE

If the tool has a dropzone, odds are files are raw-dumped into context (planner sees
a truncated slice; the agent’s prompt bloats). Add a per-file analysis step in the
worker, BEFORE the research clock starts:

- One model call per file (through the wrapper): *“document-intake analyst … distill
  THIS file against the question: KEY FACTS/CLAIMS (specifics, attribution as the
  file gives it) · ENTITIES & TERMS worth searching (incl. insider vocabulary) ·
  OPEN QUESTIONS it raises · SEARCH LEADS (2-4). Signal only; if irrelevant, say so
  in one line.”* Cap per-file input (~40K chars); failure degrades to a raw excerpt.
- The combined BRIEF grounds planning + the browser stages (goes into the
  clarifications channel); the FULL raw text still reaches synthesis as trusted
  user material. Emit per-file feed events (analyzing… / ✓ distilled).

### WS-K · “How to go deeper” closing assessment

New synthesis-module function `deeper_assessment(client, query, report_md, harvest)`
→ markdown section appended to every report AND returned as its own payload field
(render as a framed panel by the refine CTA). One call, inputs from harvest metadata
(pages/searches, stop reason, gated candidates hit, skipped logins, discovered
forums, unopened-lead count). Sub-parts (only those with real content): **Hottest
unexplored trails · Locked doors** (which credentials would unlock what) ·
**Communities to mine · Sharper follow-up queries** (paste-ready). Returns "" on any
failure; never blocks the run.

### WS-L · Live activity feed (worth more to the human eye than you think)

- Server: per-job `events` ring buffer (`[[seq, kind, text]]`, cap ~3000, text ≤500
  chars) + monotonically increasing `eseq`. Thread-safe `_push_event`; kind derived
  from a leading `[xxx]` prefix on log lines. Status endpoints accept `?after=<seq>`
  and return only newer events (including on the final done-poll).
- Wire the engine’s `log` callback (workers currently pass silent lambdas — that’s
  the gap) into the event pusher, for the pipeline AND the browser harness AND the
  synthesis stage (pass `log=` into synthesize/stop-judge/gap-queries too).
- **Granularity is the point** — emit, at minimum, these kinds:
  `plan` (planner outcome) · `stage` (progress transitions) · `turn` (“agent
  weighing next move…” before EVERY model turn — keeps the feed alive during model
  latency) · `act` (every tool call with compact input) · `hit` (top-3 search
  results per search) · `search`/`open` (engine + page events from the harness) ·
  `forum` (native searches + discoveries) · `ext` (extension grants/denials —
  highlight) · `note` (the model’s inter-tool commentary — the amber highlights) ·
  `extract` (per-page ✓class / ✗no-signal verdicts as they land — the old silent
  extraction gap) · `synth` (stop-judge verdicts, report writing) · `docs`
  (file-intake analysis).
- **Add a narration directive to the stage prompts** — frontier models at medium
  effort chain tools silently, leaving the `note` channel empty (observed): *“NARRATE
  AS YOU GO: the user watches your activity live. Before each significant pivot —
  and whenever a page yields a real finding — write ONE short sentence before your
  next tool call.”*
- UI: fixed right-side panel (~370px), monospace, per-kind glyph + color (notes/
  extensions highlighted), stick-to-bottom unless the user scrolled up, DOM cap
  ~1500 rows, collapse-to-tab, survives view switches, one job owns the feed at a
  time. Poll piggybacks the existing status loop with `&after=`.

### WS-M · Chained pre-research tier (optional but cheap once WS-J exists)

If a second, methodologically different engine exists (here: a headless iterative
searcher), expose it as a chained depth choice (“Deep + <engine>”): Pass 1 runs the
pre-research engine (fixed modest setting); its report is appended to the run’s
document parts — so **the WS-J analyst distills it exactly like a dropped file** and
its full text reaches synthesis — then Pass 2 is the normal deep-tier pipeline. One
job, one event stream; the start response’s `stages` array simply gains the
pre-research row (lock the UI’s stage list from the start response). Pre-pass
failure degrades to a plain deep run.

### WS-N · Governance prompt (the tool’s editable “constitution”)

Loaded fresh each run; codify: the charter (§0 above) · engine-query craft · where
candid signal hides (incl. “communities you haven’t heard of yet — spend a query
finding them”) · **effort-follows-payoff in BOTH directions** (near-empty harvest is
valid; hot trails earn extensions) · **organic output length in BOTH directions**
(near-zero high-signal yield → effectively-zero output, a few honest sentences;
rich detailed findings → ten or more pages; omitting genuine signal is as much a
failure as padding — mirror this in the synthesis system prompt and raise the
synthesis `max_tokens` to ~16K so long is possible) · graded citations (§WS-I) ·
**subject-matter neutrality** — no domain-specific personas or vocabulary anywhere
in prompts, placeholders, or examples; “an expert doing serious research” is the
only persona.

### WS-P · Safety-refusal handling (do NOT let a refusal silently no-op the run)

A frontier model can decline a call on safety grounds: HTTP 200, `stop_reason ==
"refusal"`, empty content, with a `stop_details.category` (e.g. `cyber`). If the wrapper
ignores it, the empty response propagates — the planner sees no JSON, the browser agent gets
no tool calls and stops on turn one — and the WHOLE run silently produces nothing ("pipeline
complete," empty report). This is a real, confusing failure; guard against it.

Two remedies, both belong in the WS-A wrapper (the single choke point):

1. **Simplest — switch the house model.** If a whole *class* of legitimate topics trips the
   classifier (we hit this: a model with elevated cyber classifiers refused legitimate
   LLM-security research), move to a sibling model without those extra classifiers. Same API
   shape usually means a one-line model-id change.
2. **Robust — offer a fall-over at the point of refusal.** Detect `stop_reason=="refusal"` in
   the wrapper; call a blocking `refusal_hook(details)` the job layer provides (pause the run,
   flag the UI, wait for the user); on "use my local/alternate model," swap the wrapper's inner
   client and **retry the same call**, so the pipeline resumes from exactly the refusal point
   with all prior work intact; on "stop," call an `on_stop` callback that halts and assembles
   the partial harvest. Make the wrapper own the stop decision (via `on_stop`), so a fall-over
   that turns out unavailable degrades to stop-and-assemble instead of spinning on empties.
   Serialize with a lock so parallel callers that refuse at once honor the first decision.
   The provider can also do this server-side (`fallbacks` param) — but the in-wrapper version
   gives the *user* the choice and works with any alternate (e.g. a local model).

Whatever you do: **surface the refusal to the user** (category + that the run paused), never
let it read as an empty success.

### WS-O · Ops, deployment, and UX fixes that bit us

- **Env knobs** (all optional, sane defaults): `DRT_EFFORT` (medium) ·
  `DRT_SLOWMO` (80ms browser pacing — the old 250ms makes deep runs take half a
  day) · `DRT_HEADED` / `DRT_BROWSER_CHANNEL` (env-driven browser: headed real
  Chrome locally, bundled headless Chromium + `--no-sandbox --disable-dev-shm-usage`
  in containers — ONE canonical harness file for both) · `DRT_RERANK` ·
  `DRT_TRACE` (verbatim agent trace to a file — invaluable for post-run analysis) ·
  `DRT_REPORTS_DIR`.
- **Health endpoint carries a `build` marker** — bump it every deploy; verifying a
  deploy = polling for the marker. Without it you cannot tell a stale build from a
  failed one.
- **“Save & open in Word”**: `os.startfile` only exists on Windows; on a hosted
  Linux box the file lands on an EPHEMERAL container disk the user can never reach.
  When the server reports it couldn’t open the document, the UI must download the
  .docx blob to the browser so it opens on the user’s machine.
- If the hosted twin carries an auth gate the local copy lacks: **never overwrite
  the gated server file** — port by grafting (take the new content, re-insert the
  gate/PORT/host/reports-dir adaptations), and verify the graft with the web
  framework’s test client before pushing (health open; unauthed page → redirect;
  unauthed API → 401; a forged-valid SSO token mints the session cookie; the authed
  page serves the new UI).
- **Verify every push landed** by comparing the remote SHA — a concurrent push from
  another session silently rejected one of ours (non-fast-forward piped to null).
- Windows host trap: after a reboot, Hyper-V/WSL2 WinNAT can reserve port blocks
  over the tool’s range → bind fails with WinError 10013 and the UI shows misleading
  “X not connected” errors. Diagnose with `netsh int ipv4 show excludedportrange`;
  fix in an elevated shell (`net stop winnat` → persistent excludedportrange for
  the tool’s range → `net start winnat`).
- **Config-file BOM trap:** if a JSON config (model map, sources) is read with
  `json.load(open(..., encoding=”utf-8”))`, a UTF-8 **BOM** makes it throw — and if the
  loader swallows the error and falls back to defaults, your override is silently ignored.
  Windows PowerShell’s `Set-Content -Encoding utf8` writes a BOM; write config via a proper
  editor or `open(..., “w”, encoding=”utf-8”)` (no BOM), or make the loader BOM-tolerant
  (`encoding=”utf-8-sig”`).

---

## 3. Verification protocol (what “done” looked like for us)

1. **Static:** byte-compile every touched module; import the full chain; assert the
   tool registry (`forum_search`, `register_forum`, `request_extension` present),
   the budget table shape, and the analyst/assessment signatures.
2. **Wrapper micro-test (live, cheap):** one real call through the wrapper passing
   `temperature` + tiny `max_tokens`; confirm the reply arrives and the guards fired.
3. **Offline harness:** fake client driving one browser-stage loop — assert
   extension grant/deny against the pool, links-section rendering, forum fallback,
   discovery dedup, deadline expiry messages.
4. **UI sanity:** balanced braces/parens/backticks in the page’s main script +
   presence of key functions (single-file UIs die whole on one syntax error).
5. **One real quick run**, trace on, then read the trace/result for: planner going
   forum-heavy on a community-shaped question · link-follow opens (opens not
   preceded by a search) · native/fallback forum searches firing · extension
   behavior · graded Sources rendering + un-citable leads section · a genuinely
   actionable go-deeper section · organic length tracking the yield. Our shakedown:
   9 minutes, 23 searches, 28 pages, 28 graded sources, ~32K-char report, and the
   agent flagged an AI-generated junk site on its own and excluded it.
6. **Gate test-client pass** (hosted twins) before any push; **SHA-verify** the push.

---

## 4. Lessons paid for (read before you repeat them)

- **De-prescribe.** Goal-first stage prompts (“MISSION … MOVES … BUDGET”) outperform
  step choreography on frontier models. State the target, name the moves, leave the
  order to the model.
- Frontier-model API deltas WILL break legacy call sites silently-ish: `temperature`
  → 400; explicit `thinking` config → 400; thinking tokens inside `max_tokens` →
  legacy tiny caps truncate. Fix once, in the wrapper.
- Silent tool-chaining at medium effort empties your “findings” channel — prompt for
  narration explicitly.
- Models mine a discovered forum happily but won’t *register* it unless told to.
- Engines punish full-sentence queries; the worst offender is usually a fallback
  path firing the user’s question verbatim.
- The navigator seeing 3.5K chars/page while the extractor sees 9K meant the
  *least*-informed component was steering. Give the navigator more.
- Unread-but-numbered sources = invited hallucination. Un-number them.
- An activity feed with real granularity changes how the human relates to the tool.
  Cheap to build once events exist; do not summarize — raw cascade, highlighted
  notes.
- Time beats unit budgets as the governor: users think in minutes, models pace
  honestly against a visible clock, and extensions become a clean payoff mechanism.

---

## 5. Suggested execution order for the upgrading agent

1. Read the whole target codebase first (server, engine, UI). Map its equivalents
   of: job registry, stage loop, tool defs, extraction, synthesis, sources config.
2. WS-A (wrapper) → compile/import → live micro-test.
3. WS-B + WS-C + WS-D + WS-E + WS-F + WS-G in the engine (they share files — one
   coherent pass), WS-H + WS-I + WS-K in synthesis, WS-L in server+UI, WS-J in the
   worker, WS-N in the governance prompt. Parallelize only across disjoint files.
4. Verification protocol §3, including one real run with trace on.
5. WS-O ops items; port to any hosted twin by grafting; SHA-verified push; build-
   marker-verified deploy.
6. Update the tool’s own handoff docs and your memory of it — future sessions
   should be able to pick this up cold in one read.
