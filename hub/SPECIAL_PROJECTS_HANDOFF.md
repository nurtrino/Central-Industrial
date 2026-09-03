# Special Projects / Central Industrial — MASTER HANDOFF

_Last updated: 2026-09-03 (one hub codebase + single local port — see the block below; prior: 2026-08-29 Deep Research, 2026-07-16 consolidation)_

> **This is the single master handoff** for the Special Projects suite — both the
> **local** C64 hub (`127.0.0.1:5050`) and the **public** site (**https://centralindustrial.ai**).
> It consolidates and supersedes as entry point: the old `SPECIAL_PROJECTS_HANDOFF.md` (hub-only,
> 2026-06-27), `Special Projects Main.md` (local-era master, 2026-06-27), and the
> deployment sections scattered across the per-tool handoffs. The per-tool handoffs remain
> the deep-dive references (see §9 Document map). When you materially change a tool, update
> (a) its own handoff, (b) this master, (c) agent memory.

> **UPDATE 2026-08-29 — Deep Research (hosted `research.centralindustrial.ai` ↔ local `:5006`).**
> Full detail in `D:\_______Claude\Deep Research\DEEP_RESEARCH_HANDOFF.md` (read its top block).
> Hub-relevant facts: (1) model is `claude-opus-4-8` everywhere — Fable's `cyber` refusal
> classifier silently no-op'd the pipeline, so Fable 5/5.1 are OUT; a refusal safety-net
> (pause → local model / stop) and a Stop button exist. (2) **Credentialed research is
> LOCAL-ONLY by design**: the encrypted vault + key are never committed/pushed and Render's
> disk is ephemeral, so the hosted site never stores logins and shows a guardrail saying so.
> (3) **One unified site list** (`deep-research/config/drt_sources.json`: `url` + 🟢/🔴
> `login_required`) drives both instances; the research plan's relevance call decides which
> sites a query searches; 🔴 sites use the vault silently on local and a ONE-TIME ephemeral
> prompt on hosted. (4) The list reaches the hosted site ONLY via commit+push (baked in at
> deploy) — the local-only **🚀 Publish** button does that (persistent clone at
> `D:\_______Claude\Central-Industrial`); edits made on the hosted site are wiped on redeploy.
> (5) Secrets: `ANTHROPIC_API_KEY`/`AUTH_SECRET` live only in the Render dashboard (`sync:
> false`) and the local `.env`; the repo is PUBLIC and holds none (verified). Latest deployed
> build `2026-08-29.publish`.

> **UPDATE 2026-09-03 — ONE HUB CODEBASE, ONE LOCAL PORT (commits `915faef` + follow-up).**
> The separate local hub (`D:\_______Claude\Special Projects`) is RETIRED → archived at
> `D:\_______Claude\_ARCHIVE\Special Projects (old local hub, retired 2026-09-03)`. THIS file
> (in the repo's `hub/`) is now the master handoff. `hub/` serves BOTH: hosted (unchanged:
> gate, SSO, HTTP probes) and **local mode** (`CI_LOCAL=1`, run from the clone
> `D:\_______Claude\Central-Industrial\hub`):
> (1) **Single port** `http://127.0.0.1:5050` — a TCP router routes by Host header and
> relays whole connections (streaming + WebSockets intact): `notes.localhost:5050` → MRMD
> :5005, `research.localhost:5050` → Deep Research :5006, `jeopardy.localhost:5050` → Hyper
> Jeopardy :5008 (Node/Next, built locally; phones on the LAN use `http://<LAN-IP>:5008/`
> since `*.localhost` is browser-only), `home.localhost:5050` → Home Assistant :8123; the hub
> serves `/cave` `/twixtle` `/crate` itself in both modes. The hub's own HTTP is on :5051
> (loopback). `*.localhost` resolves inside browsers only — scripts must send a `Host:`
> header to 127.0.0.1:5050 and use a fresh connection per host.
> (2) **The hub owns the processes**: `tools.json` `local{cmd,cwd,env,log,autostart}` entries are
> spawned hidden as children in a Windows Job Object (KILL_ON_JOB_CLOSE) — kill the hub and
> every tool (and Deep Research's Chrome) dies; a down tool is auto-launched the moment its
> hostname is hit (C64 LOADING page refreshes until it answers) or from the menu click. The
> hosted-site GPU worker (`ReadMonkeyDoWorker.exe --no-browser`, :5007) is a `hidden`
> supervised entry. Home Assistant stays `detached` (WSL2, launched via its .vbs).
> (3) **ONE Startup entry**: `Startup\Central Industrial.vbs` → `launch_local.py --startup`
> (the per-tool Startup .vbs files were removed). Double-click `hub\Central Industrial.vbs`
> to open the C64 screen. Tools' own `.vbs` launchers still work standalone (idempotent by
> port; the hub then just proxies to the already-running instance).
> (4) Tiles: local now shows all seven (Deep Research, MRMD, Hyper Jeopardy, Cave Map,
> Twixtle, Crate, Home Automation); hosted shows Home Automation as [ LOCAL ]. Public-only
> tools would show [ HOSTED ] locally. The old Flask Twixtle (`D:\_______Claude\twixtle`,
> :5077) is superseded by the hub's static page (same as prod) and no longer runs.
> (5) Hyper Jeopardy's hosted URL is `https://central-industrial.onrender.com/` — the
> `jeopardy.centralindustrial.ai` domain in render.yaml has NO DNS record (fix in DNS/Render
> if wanted). Local build recipe: `cd hyper-jeopardy && npm ci && npm run build`; the hub
> runs `node --import tsx server.ts` with `PORT=5008 NODE_ENV=production DATA_DIR=.data`.

---

## 0. The lay of the land — two deployments, one concept

The user grew up programming on a C64; the boot-screen splash is a deliberate callback.
There are **two parallel C64 hubs**:

| | LOCAL suite | PUBLIC site |
|---|---|---|
| Front door | `http://127.0.0.1:5050/` (ONE port; tools at `<name>.localhost:5050`) | `https://centralindustrial.ai` |
| Source | `D:\_______Claude\Central-Industrial\hub\` (the clone; `CI_LOCAL=1`) — same code as the public hub since 2026-09-03 | GitHub **`nurtrino/Central-Industrial`** (monorepo) |
| Runs on | This machine (system Python 3.14, stdlib only) | **Render** (Blueprint `render.yaml`, `autoDeploy: true` on `main`) |
| Purpose | Launch/monitor local tools (GPU, private) | Public/hosted versions + self-contained pages |
| Gate | None (loopback only) | **Access code** (C64 "PROTECTED PROGRAM" prompt) |

They share the visual language (Pepto C64 palette `#352879`/`#6C5EB5`, Style64 "C64 Pro Mono"
font, CRT scanlines, FSOL "Central Industrial" MP3 on first keypress, "WELCOME TO CENTRAL
INDUSTRIAL. WE ARE THE FUTURE.") but are **separate codebases** — the public hub was rebuilt
for Render (0.0.0.0:$PORT, HTTP URL probes instead of socket port-probes, the auth gate).

**GitHub access:** `bbryndal` is a **write collaborator** on `nurtrino/Central-Industrial`
(accepted 2026-07-13) — `gh`/`git` from this machine push directly. This repo is where the
once-"lost" publish work actually lives (recovery snapshot: `Special Projects\_deployed_recovery\`).

---

## 1. PUBLIC SITE — centralindustrial.ai

### 1.1 Repo layout (`nurtrino/Central-Industrial`, branch `main`)

```
├── render.yaml              ← Render Blueprint — canonical reference (services were created
│                              via the Render API; this file mirrors them)
├── hub/                     ← C64 home + access gate (stdlib Python hub_server.py)
├── deep-research/           ← Docker + headless Chromium research service
├── monkey-read-monkey-do/   ← hosted MRMD UI (transcription stays on a LOCAL GPU worker)
├── mrmd-worker/             ← the local GPU helper (NOT deployed; users download the exe)
└── hyper-jeopardy/          ← Next.js + socket.io multiplayer party game (Docker)
```
(The repo root `README.md` is **stale** — it predates the MRMD-hosted + Hyper Jeopardy
services. Trust `render.yaml` and this doc.)

### 1.2 Render services

| Service | rootDir | Domain | Plan | Notes |
|---|---|---|---|---|
| `central-industrial-hub` | `hub/` | centralindustrial.ai (+www) | starter | `python hub_server.py`; **persistent disk** `hub-data` 1 GB → `/var/data` (`DATA_DIR`) for the shared Twixtle store |
| `deep-research` | `deep-research/` | research.centralindustrial.ai | **standard** (Chromium RAM; starter OOMs) | Docker; `DRT_HEADED=0` |
| `monkey-read-monkey-do` | `monkey-read-monkey-do/` | notes.centralindustrial.ai | starter | gunicorn; `MRMD_HOSTED=1`; GPU work happens client-side via the worker |
| `hyper-jeopardy` | `hyper-jeopardy/` | jeopardy.centralindustrial.ai (also central-industrial.onrender.com) | starter | Docker (custom Node server = Next + socket.io); **persistent disk** `hyper-jeopardy-data` 1 GB → `/var/data` (accounts/leaderboard/game snapshot) |

**Deploy loop:** push to `main` → Render auto-deploys (~1–2 min). ⚠ The hub and
hyper-jeopardy have persistent disks → **single-instance → brief downtime on each deploy**.
Apex `centralindustrial.ai` 301s to `www.` — use `curl -L` when probing.

**Secrets** (Render dashboard, `sync: false`): `ACCESS_CODE`, `ANTHROPIC_API_KEY`,
`EXA_API_KEY`/`DRT_EXA`, `FIRECRAWL_API_KEY`, `MRMD_WORKER_URL`, `MRMD_WORKER_TOKEN`.
`AUTH_SECRET` is generated once in the env-var group `central-industrial-auth` and shared
by all four services.

### 1.3 The access gate (single sign-on)

- Gate is ON when both `ACCESS_CODE` + `AUTH_SECRET` are set. The C64 home page shows
  `PROTECTED PROGRAM - ENTER ACCESS CODE:` → `POST /api/login {code}` → signed `ci_auth`
  cookie scoped to **`.centralindustrial.ai`** (`COOKIE_DOMAIN`), TTL 8 h. The hub
  re-prompts on every load by design — the cookie exists to carry auth to the tool subdomains.
- The menu polls `GET /api/status` (401 until authed) which returns tool up/down **and a
  fresh 2-min SSO token**; every tool link gets `?t=<token>` appended so a click proves it
  came through the hub. MRMD and Hyper Jeopardy (via `proxy.ts`) verify it with the shared
  `AUTH_SECRET`, then set their own host cookie. Tools that ignore `?t=` (static pages,
  external links like Crate-app-on-Pages) are unaffected.
- **Gated:** `/api/status`, Twixtle puzzle `POST` (add/delete). **Open:** the static pages
  (`/cave`, `/twixtle`, `/crate`), `GET /api/twixtle/puzzles`, `/tools.json` (any file in
  `hub/` is statically served — remember that before committing anything private there),
  Hyper Jeopardy's `/api/socket` + `/api/health`.

### 1.4 The C64 menu registry — `hub/tools.json`

Current entries (2026-07-16): **Deep Research** (127.0.0.1:5006 — overridden in prod by
`HUB_URL_DEEP_RESEARCH`=research.…), **Monkey Read Monkey Do** (5005 → `HUB_URL_MONKEYREADMONKEYDO`=notes.…),
**Hyper Jeopardy** (central-industrial.onrender.com), **Cave Map** (/cave), **Twixtle**
(/twixtle), **Crate** (/crate). URLs are overridable per-service via `HUB_URL_<ID>`;
entries with `"local": true` show `[ LOCAL ]` and are never probed.

### 1.5 The two patterns for adding an app

1. **Self-contained static page** (Cave Map, Twixtle, Crate): one HTML file in `hub/`
   (all data/JS/CSS inlined), a route in `hub_server.py`'s `do_GET`
   (`elif parsed.path in ("/x", "/x/"): self.path = "/x.html"`), a `tools.json` entry.
   No new Render service, no cost.
2. **Full Render service** (Hyper Jeopardy model): own rootDir + Dockerfile/start command,
   subdomain, `render.yaml` service block with `fromGroup: central-industrial-auth` (turns
   the gate on), `HOME_URL=https://centralindustrial.ai` (bounce target), `tools.json` entry.

### 1.6 The deployed apps

**Cave Map — /cave** (2026-07-02). Ohio caves Leaflet map; `hub/cave_map.html` is the
**canonical source** (edit in repo, not the deprecated Desktop copy); `cave_map_data.csv` =
hand-synced human-readable mirror. Self-contained data; external calls are public APIs
(unpkg Leaflet, Esri/USGS tiles, ODNR karst layers). Ref: repo `hub/CAVE_MAP_README.md`.

**Twixtle — /twixtle** (2026-07-13). Self-hosted clone of the twixtle.games 4-move word
puzzle (anagram/verb/homophone/compound). `hub/twixtle.html` self-contained (~1.2 MB,
validator substrate + 57 official puzzles inlined). Play / Archive / Create views;
client-side generator + build-your-own. **Shared server-side puzzle store**:
`GET/POST /api/twixtle/puzzles` backed by `/var/data/twixtle_puzzles.json` on the hub disk
(POST gated by the access code; atomic write under a lock; dedup by `start>end`; 5000 cap).
Difficulty tiers easy<50 / med 50–59 / hard≥60 — set identically in local
`build_bundle.py` and the page's `gradeChain`; keep in sync. Build/rebuild workflow + solver
live in `D:\_______Claude\twixtle\` → see **`twixtle\TWIXTLE_HANDOFF.md`** (deploy kit in
`twixtle\deploy\`).

**Crate archive — /crate** (2026-07-16, v2). The user's Spotify library as a browsable
self-contained page: `hub/crate.html` = `crate-archive-embedded-v2.html` verbatim
(198 playlists + Liked, **5,608 tracks**, 195 covers baked in as data: URIs — immune to
scdn.co referrer-blocking). v2 (commit `0f88494`) added the GDPR merge over v1's 4,967:
the >100-track tails the web crawl capped, plus 241 `spotify:local:` file tracks (e.g.
Ultra Chilled 0→168). C64 menu's "Crate" points here. Served **openly** — anyone with the
URL can browse playlist names/tracks (flagged; gate on request). To refresh: rebuild the
local archive → copy over `hub/crate.html` → push. The Crate **app** (search overlay;
login/IndexedDB are per-origin) stays at https://nurtrino.github.io/Bryndal-App/spotify-crate/
— untouched. Ref: **`spotfiy_crate\HANDOFF.md`** (source of truth; §2b deploy, §2c v2 merge).

**Hyper Jeopardy — jeopardy.centralindustrial.ai** . Multiplayer party game (Next.js +
socket.io, custom Node `server.ts`, 5.7 MB clue seed, mini-games/Invaders, accounts +
leaderboard on the persistent disk). Behind the gate via `proxy.ts` (trusts hub `?t=`
handoff → host-only `ci_sess` cookie; bounces cookieless visits to the hub; socket +
health stay open). Menu links the `.onrender.com` URL.

**Deep Research — research.centralindustrial.ai**. The hosted twin of the local :5006 tool
(same engine, Docker + headless Chromium, `HOME_URL` bounce when not authed). Uses API
credits; standard plan.

**Monkey Read Monkey Do — notes.centralindustrial.ai**. Hosted UI only; **audio never goes
to the cloud**. The page transcribes via a **local GPU helper**: user downloads
`ReadMonkeyDoWorker.exe` (~8 MB, served at `/download/…`) which self-provisions uv + CPython
3.12 + pinned faster-whisper/CTranslate2/cuBLAS into `%LOCALAPPDATA%\ReadMonkeyDo\` (~4–5 GB
one-time) and serves `127.0.0.1:5007` (`POST /transcribe`, `GET /health`; CORS locked to
notes.… + PNA header). Auto model sizing by VRAM. Alternative: one shared GPU box over an
HTTPS tunnel (`MRMD_WORKER_URL` + `MRMD_WORKER_TOKEN` on the service). Build: `mrmd-worker\build_setup.bat`
→ commit the exe to `monkey-read-monkey-do/downloads/`. Ref: repo `mrmd-worker/README.md`.

---

## 2. LOCAL SUITE — the Special Projects hub (127.0.0.1:5050)

**Folder:** `D:\_______Claude\Special Projects\` · auto-starts at login (`Special Projects.lnk`
in `shell:startup` → `Special Projects.vbs` → `launch.py`, idempotent).

| File | Role |
|---|---|
| `hub_server.py` | **Stdlib-only** server on `127.0.0.1:5050` (system Python 3.14, no venv). `GET /` page · `GET /api/status` (socket-probes each tool's port, 0.5 s) · `GET /api/launch?id=` (runs the tool's launcher detached). `Cache-Control: no-store` on everything. |
| `tools.json` | Registry. One entry `{id, name, url, port, cwd, launch}` per tool → auto-appears with a live `[ ONLINE ]`/`[ OFFLINE ]` pill; clicking an offline tool launches it, polls, opens it. |
| `index.html` | All UI/JS: boot screen → first keypress starts MP3 + load sequence → welcome → live menu. `[MUTE]` toggle. Poll: boot, 0.7 s, then every 1.5 s. |
| `launch.py` / `Special Projects.vbs` | Idempotent launcher (starts hub hidden if :5050 down, opens browser). |
| `fonts/` | Style64 "C64 Pro Mono" (vendored unmodified per license). |
| `_deployed_recovery/` | 2026-07-13 snapshot of the then-live public site (recovery artifacts). |

**Registered local tools** (in `tools.json`): Monkey Read Monkey Do (5005), Deep Research
(5006), Home Automation (8123). *(HEOS Suck Less (5008) was retired and archived 2026-09-03;
its Deco keys/secrets were moved to `LAN COMMANDER\deco_assets`.)*

**Adding/migrating a local tool:** get it serving on a free 5000–5099 port (own venv) →
add one `tools.json` entry → it auto-appears. Optional: `.vbs` launcher + Startup shortcut +
`<name>://` protocol `.reg` (user must double-click the .reg — see §4).

### Port map (one host, one port per tool)

| Port | Tool |
|---|---|
| 5000 | `webcrawler` (`D:\_______Claude\webcrawler`) |
| 5002 | HFDD / DRT — full DDDD monolith (`D:\_______Claude\hedge_fund_dd`, Flask) — separate project, not on the hub |
| 5005 | **Monkey Read Monkey Do** |
| 5006 | **Deep Research** (standalone) |
| 5007 | MRMD local GPU worker (`ReadMonkeyDoWorker`) |
| 5050 | **Special Projects hub** |
| 5077 | Twixtle local dev (`twixtle\run.bat`) |
| 8123 | **Home Automation** (HA Core in WSL2) |
| 8765 | heos-control (demo) |

**GPU VRAM is the one shared bottleneck** — GPU tools lazy-load and release; per-tool venvs
(torch/CUDA versions diverge); web/CPU tools coexist freely.

---

## 3. LOCAL TOOLS — one-paragraph summaries (deep dives in their own handoffs)

**Monkey Read Monkey Do (5005)** — local audio/video → transcript → DD meeting notes.
Whisper `large-v3` + pyannote **100% local on the RTX 5090** (~18 GB VRAM); only the text
goes to Claude for the two-pass notes step. Flask; uv venv (py3.12, torch cu128). Formerly
"Note Max" (renamed 2026-06-27; `NOTEMAX_*` env keys kept). ⚠ `app.py` reads
`ANTHROPIC_API_KEY` **once at import** and the launcher is idempotent → after any `.env`
edit you must kill :5005 and relaunch (in-page ⟳ Restart, or `Stop-Process` on the port
owner). Verify: `curl 127.0.0.1:5005/api/config` → `"notes":true`.
Ref: **`Monkey Read Monkey Do\HANDOFF_2026-07-13.md`** + `README.md`.

**Deep Research (5006)** — browser-driven multi-stage research → cited report (md + docx).
Standalone Flask `dr_server.py` serving its own UI same-origin; engine copied verbatim from
the monolith; own venv (py3.14, no torch). Opens **visible Chrome**; spends API credits.
Sub-tools in the same page: **Odysseus** (headless IterResearch A/B engine), **Very Very
Deep** (chained Odysseus→DR two-pass, two output docs), **Firecrawl console** (proxy
`/api/firecrawl/<path>`, key server-side). Auto-saves every finished report to
`D:\______Documents\___Deep Research Reports\`. Credential vault: `config/.drt_vault_key` +
`drt_credentials.enc`. 2026-08-11: finance/DD framing stripped from prompts/UI/comments —
local AND the deployed `deep-research/` service (repo commit `8250fc7`). Also 2026-08-11
(LOCAL only): all Claude calls → `claude-fable-5` @ effort high (`ClaudeClient` wrapper in
`engines/research/llm.py`, `DRT_EFFORT` env); organic output length codified in governance +
synth prompts; Stage-1 server-side web_search RETIRED — all web search now runs in the
visible Chrome. Same day: the **DEEP ACTUAL RESEARCH rebuild** — link-crawling, native
in-forum search (`search_url` on sources), forum discovery, elastic payoff-weighted
time-boxed budgets (**Standard ~5 min / Deep ~10 min / Deep + Odysseus ~15 min chain**),
live activity feed, signal-graded citations, doc-intake analysis, "How to go deeper";
VVD sub-tool removed; engine-optimized keyword queries; Save&Open downloads the .docx
when the server can't launch Word; effort default **medium** (`DRT_EFFORT`). **FULLY
SYNCED TO DEPLOYED** (final commit `b0bb0b7`, gate preserved + test-verified every push;
`/api/health` build marker `2026-08-11.dar3`). ⚠ Concurrent-session push races happened
once — always verify a push landed by comparing the remote SHA.
Ref: **`Deep Research\DEEP_RESEARCH_HANDOFF.md`** (session-3 block + state-at-close banner).

**Home Automation (8123)** — HA Core 2026.2.3 in **WSL2 Ubuntu 24.04** (no native cp313
wheels — the one suite exception). Venv + config in the WSL home fs (NOT /mnt/d — SQLite
recorder misbehaves on DrvFs). Launcher gotchas: use `python -m homeassistant` (not `hass`),
run in wsl.exe **foreground** (backgrounding races WSL shutdown). Windows reaches it via
WSL2 localhost-forwarding. **Onboarding still not done.** Dropped from the cloud deploy
(not portable). Ref: **`Home Assistant\HOME_ASSISTANT_HANDOFF.md`**.

---

## 4. Cross-cutting environment gotchas

- **Claude desktop-app sandbox virtualization (critical):** agent-shell writes under
  `%LOCALAPPDATA%`, registry, `setx`/PATH are **virtualized** (invisible to the real
  environment). Real: profile root, `%APPDATA%` Roaming (incl. Startup folder), `D:\`.
  So installers, `.reg` merges, `playwright install` → **user's own terminal** (or redirect
  caches to `D:\`). Memory: `[[env-claude-app-sandbox-virtualization]]`.
- **Stale-server .env trap:** local tools read `.env` once at startup and launchers are
  idempotent → `.env` edits require kill + relaunch. Verify via each tool's config/probe
  endpoint. Memory: `[[project-stale-server-env-trap]]`.
- **Anthropic key state (2026-07-16):** ONE key shared across MRMD / Deep Research / HFDD
  `.env`s — canonical = the `tFib…` key (the `qf2J…` rotation was reversed). **Do not revoke
  `tFib…`**; `qf2J…` is the spare. Memory: `[[project-anthropic-key-state]]`.
- **AF_UNIX/Selector block:** the agent shell can't open Java NIO selectors (Gradle/Netty
  builds fail) — user runs those in their own terminal.
- **Hub dev/verify trick:** the real hub owns :5050, so to preview changes set `PORT=5051`
  temporarily (both `hub_server.py` and `.claude/launch.json`) → preview → revert. Simulate
  an ONLINE tool with a throwaway `python -m http.server <port>`. Browser-pane screenshots
  **time out on large self-contained pages** (twixtle/crate) — verify via DOM/JS instead.
- **Phosphor terminal theme** (tool pages, not the hub): bg `#04100A`, text `#3BE859`,
  accents `#74FB80`/`#34D24F`, dim `#173A22`–`#2A7A3A`, amber warn `#e0b341`, red err
  `#ff6b6b`; defined inline per tool page — keep in sync when tweaking.

---

## 5. Standard workflows

**Update a static page on the public site** (cave/twixtle/crate): edit/rebuild the
self-contained HTML locally → copy into `hub/<name>.html` in the repo → commit + push
`main` → auto-deploy ~1–2 min (hub blips) → verify at the live URL (fetch + decode, don't
trust a 200 alone; remember apex→www 301).

**Add a menu entry:** edit `hub/tools.json`, push. External URLs work as-is (probe = HTTP GET).

**Add a gated service:** copy the Hyper Jeopardy pattern — `render.yaml` block with
`fromGroup: central-industrial-auth` + `HOME_URL`, verify the hub `?t=` handoff, add domain +
`tools.json` entry.

**Work on the local hub:** edit files in `Special Projects\` (served fresh from disk — no
restart needed for `index.html`/`tools.json`; restart `hub_server.py` for server changes).

---

## 6. State at handoff (2026-07-16)

- Public site live: menu = Deep Research · Monkey Read Monkey Do · Hyper Jeopardy · Cave Map ·
  Twixtle · Crate. Latest commits: `0f88494` (Crate v2 archive, 5,608 tracks — live/verified),
  `47baf3b` (Crate page + route + menu).
- 2026-08-11: hub boot MP3 (`hub/10-future_sound_of_london-central_industrial-eos.mp3`)
  replaced several times, same filename throughout (page `<audio>` src unchanged). Current
  version (commit `370f0ce` — live/verified 122,988 bytes): the UNEDITED
  `C:\Users\crouchingyeti\Desktop\centralindustrial intro.mp3` — the day's Audacity edit
  chain (snare/clang removal, tail smoothing, 5.45s slowdown) was reverted as over-edited;
  another editing pass planned later. LOCAL hub copy in `Special Projects\` matches. The
  edited candidate is kept at
  `C:\Users\crouchingyeti\Desktop\centralindustrial welcome final.mp3` (was commit
  `3392dbf`); earlier versions (original FSOL, `09efdc2`, `e3e6d9e`) in git history.
- Local hub on :5050 (auto-start), MRMD on :5005, Deep Research on :5006 on demand, HA on
  demand (onboarding pending).
- **Open items:** decide whether `/crate` should go behind the access gate; HA
  onboarding; Twixtle "possible next" items (bake a Claude-created starter set, tune easy
  tier); repo root README refresh (stale service list).

---

## 7. Document map

| Doc | Scope | Status |
|---|---|---|
| **This file** (`Special Projects\SPECIAL_PROJECTS_HANDOFF.md`) | Master — local suite + public site | **Entry point** |
| `Special Projects\Special Projects Main.md` | Old local-era master (2026-06-27) | Superseded by this file (kept for history) |
| `twixtle\TWIXTLE_HANDOFF.md` | Twixtle: puzzle rules, solver, generator, deploy detail | Current |
| `spotfiy_crate\HANDOFF.md` | Crate archive: embed, GDPR v2 merge, /crate deploy, app internals | Current (source of truth for Crate) |
| `Deep Research\DEEP_RESEARCH_HANDOFF.md` | Deep Research local tool + sub-tools | Current |
| `Monkey Read Monkey Do\HANDOFF_2026-07-13.md` | MRMD session notes (key rotation saga, restart recipe) | Current (+ `README.md` for full docs) |
| `Home Assistant\HOME_ASSISTANT_HANDOFF.md` | HA-in-WSL2 deep dive | Current |
| `Firecrawl\HANDOFF.md` | Source doc the DR Firecrawl sub-tool was rebuilt from | Reference |
| Repo `hub/CAVE_MAP_README.md`, `hub/TWIXTLE_README.md`, `mrmd-worker/README.md` | Per-app repo docs | Current |
| Repo `hub/SPECIAL_PROJECTS_HANDOFF.md`, `hub/Special Projects Main.md`, root `README.md` | Copies committed 2026-06-27/07-06 | **Stale** — trust this file + `render.yaml` |

**Agent memory mirrors:** `[[local-tools-host-convention]]`, `[[project-notemax]]`,
`[[project-deep-research-standalone]]`, `[[project-home-assistant]]`, `[[project-crate-spotify]]`,
`[[project-twixtle]]`, `[[env-claude-app-sandbox-virtualization]]`, `[[env-wsl2-ubuntu]]`,
`[[project-anthropic-key-state]]`, `[[project-stale-server-env-trap]]`.
