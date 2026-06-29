# Deep Research — Handoff

_Last updated: 2026-06-27_

## What this is

**Deep Research** is the browser-driven, multi-stage due-diligence web-research tool — **extracted from the DDDD "Admin Tools" monolith and rebuilt as its own standalone local app** under Special Projects. Enter a research question → it opens a **visible Chrome**, fans out across web search + browser engines + your curated/credentialed sources, and synthesizes a **cited DD report** (markdown on screen + a downloadable `.docx`), with a collapsed harvest-audit trail.

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

## The pipeline (unchanged from the original)

`Stage1` Claude API web_search → `Stage2` browser engines (DuckDuckGo/Brave/Google) → `Stage3` already-credentialed sources (vault) → `Stage4` new gated sources (batched in-app credential prompt) → **synthesize** (per-page goal extraction + junk filter [Haiku], cited report [Opus], evolving-report + stop-judge deepening loop) → **report** (+ collapsed harvest audit). Depth = quick / standard / deep. Models: plan+synthesize = `claude-opus-4-8`, search+route = `claude-sonnet-4-6`, extract = `claude-haiku-4-5` (override in `config/drt_models.json` → restart).

## Firecrawl — sub-tool (added 2026-06-28)

A data-driven **console over the Firecrawl v2 REST API**, rebuilt from `D:\_______Claude\Firecrawl\HANDOFF.md` (the original source from the other machine didn't come across — only the handoff did).

- **UI:** fourth left-sidebar item (Deep Research / Odysseus / Very Very Deep / **Firecrawl**), own `#fc-main-inner` view + back button. A tab row of the 11 tools (Scrape · Search · Map · Crawl · Crawl Status · Batch Scrape · Batch Status · Extract · Extract Status · Generate llms.txt · llms.txt Status); each tool's form is generated from the `FC_TOOLS` array (field types: text/number/textarea/lines/json/bool/multi). Results render markdown when present + a collapsible raw-JSON view. Async tools return a job id with a **"Check status →"** button that jumps to the matching Status tool. A **⚡ Test connection** button (top-right) hits `map`. All `_fc*` functions live in the same `index.html`.
- **Backend:** generic proxy **`/api/firecrawl/<path>`** in `dr_server.py` → forwards to `https://api.firecrawl.dev/<path>` with `Authorization: Bearer $FIRECRAWL_API_KEY` injected server-side (the key never reaches the browser). Pass-through JSON + status. Uses `requests` (already in the venv).
- **Key:** `FIRECRAWL_API_KEY` in `Deep Research/.env` (the handoff key, valid as of 2026-06-28). Rotate at the Firecrawl dashboard → update `.env` → restart.
- **Verified 2026-06-28:** live `POST /api/firecrawl/v2/map` returned `success:true`; the console renders all tabs/forms and a live Map result; no console errors.
- **Not included (separate concern):** the handoff's **Firecrawl MCP** registration for Claude Code (`~/.claude.json`) — that's a Claude Code integration, not part of this sub-tool.

## Saving reports (Save & open in Word)

The results "💾 Save & open in Word" buttons (Deep Research, Odysseus, and both Very Very Deep docs) **do not** browser-download — they POST the `.docx` to **`POST /api/save_report`** `{docx_b64, query, label}`, which writes it to **`D:\______Documents\___Deep Research Reports\`** with a topic-keyword filename (`<label> — <keyword slug from the query> — <timestamp>.docx`) and **opens it in Word** via `os.startfile` (the `.docx` association). A toast shows the saved path. If the save endpoint fails (folder not writable, server down), the frontend **falls back to a browser download** so the file is never lost. Helpers: `_slug_from_query` / `_safe_filename` (server), `saveAndOpen` / `_blobDownload` / `_toast` (client). Note: Word opens in the session the server runs in — normally the user's (hub/Startup launch); the file is always saved regardless.

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
