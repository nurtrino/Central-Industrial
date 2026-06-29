# Special Projects — Main (master handoff)

_Last updated: 2026-06-27_

> **This is the master index** for everything under Special Projects. Read it first when
> resuming. It summarizes the shared architecture and each tool at a glance, then points to
> each tool's own detailed handoff. **Compaction model:** each tool can be worked on and its
> own handoff compacted independently; this master is the durable entry point that ties them
> all together. When you add or materially change a tool, update (a) its own handoff, (b) the
> one-line summary + port map here, and (c) agent memory.

---

## 0. What "Special Projects" is

A growing suite of the user's **personal, local-only web tools**, all on **one machine, one
`127.0.0.1` port each**, fronted by a single Commodore-64-style **launcher hub** (the "splash
page"). The user grew up programming on a C64; the splash screen is a deliberate callback.

- **Root:** `D:\_______Claude\Special Projects\` (the hub) + one sibling folder per tool.
- **Front door / bookmark:** `http://127.0.0.1:5050/`
- **Design tenet across tools:** local-first, signal-over-noise, one bookmark to rule them all.

---

## 1. Cross-cutting architecture (applies to every tool)

**One host, one port.** Reserve **5000–5099** for "my tools." Each tool binds `127.0.0.1:<port>`,
is its own self-contained app (its own venv when it has Python deps), and registers with the hub
by adding **one entry** to `tools.json`. GPU **VRAM is the only shared bottleneck** — GPU tools
lazy-load models and release when idle; CPU/web tools coexist freely.

**Port map:**

| Port | Tool |
|---|---|
| 5000 | `webcrawler` (`D:\_______Claude\webcrawler`) |
| 5002 | HFDD / DRT — the full DDDD monolith (`D:\_______Claude\hedge_fund_dd`, Flask) |
| 5005 | **Monkey Read Monkey Do** |
| 5006 | **Deep Research** (standalone DRT migration) |
| 5050 | **Special Projects hub** (the front door) |
| 8123 | **Home Automation** (Home Assistant Core, runs in WSL2 Ubuntu) |
| 8765 | heos-control (demo) |
| 5007+ | future tools |

**⚠ Claude desktop-app sandbox gotcha (critical).** This Claude Code runs as a *packaged* Windows
app. **Agent-shell writes under `%LOCALAPPDATA%`, and registry / `setx` / User-PATH changes, are
VIRTUALIZED** into the app's private store — invisible to the user's real environment. **Real
(agent writes stick):** the user profile root, `%APPDATA%\Roaming` (incl. `shell:startup`), and
`D:\`. So: build tools, venvs, and `pip install` on `D:\` freely; but **installers, `.reg`
protocol files, PATH/env changes, and `playwright install` browser caches must be run by the USER
in their own terminal** (or redirected onto `D:\`). This is why every tool's launcher, Startup
shortcut, and `.reg` lives on `D:\` but the `.reg` still has to be double-clicked by the user.

**Launcher pattern (per tool).** A `<Tool>.vbs` (no console) → idempotent `launch.py` that starts
the tool's server (via its own `.venv\Scripts\pythonw.exe`) only if the port is down. The hub's
`/api/launch` runs this on demand, so a Startup shortcut is optional. Optional per-tool niceties:
a `<name>://` URL protocol `.reg` and a `shell:startup` shortcut.

**Shared visual theme — "phosphor terminal" (green-on-black CRT).** The tool pages (Monkey Read
Monkey Do, Deep Research) share one palette so the suite reads as one: bg `#04100A`, primary text
`#3BE859`, bright accent `#74FB80`/`#34D24F`, dim/borders `#173A22`–`#2A7A3A`, with monospace font
+ subtle CRT scanlines + green text-glow; warnings amber `#e0b341`, errors red `#ff6b6b`. It's
defined inline in each tool's `index.html` (a labeled `:root` palette block + a "phosphor terminal
polish" block) — **keep the two in sync when tweaking.** (The hub itself stays its own C64 blue —
it's the marquee, not a tool.) Set 2026-06-27.

---

## 2. The splash page — Special Projects hub  (port 5050)

**Folder:** `D:\_______Claude\Special Projects\`
**Detailed handoff:** [`SPECIAL_PROJECTS_HANDOFF.md`](./SPECIAL_PROJECTS_HANDOFF.md)

A single "front door" web page that lists the local tools, shows each one's **live up/down
status**, and links straight to it — starting it on demand if it's down.

- **`hub_server.py`** — **stdlib-only** HTTP server on `127.0.0.1:5050` (system Python 3.14, **no
  venv/pip**). Serves the page + `GET /api/status` (socket-probes each tool's port for true
  up/down) + `GET /api/launch?id=` (runs a tool's launcher server-side). Sends
  `Cache-Control: no-store` on everything → never a stale page.
- **`tools.json`** — the registry. Add a tool = add one `{id, name, url, port, cwd, launch}`
  entry; it appears on the page automatically with a live status pill.
- **`index.html`** — all UI/JS. Boot screen (`**** COMMODORE 64 BASIC V2 ****` … `LOAD"SPECIAL
  PROJECTS",8,1`) → first keypress starts the looping FSOL "Central Industrial" MP3 + load
  sequence → "WELCOME TO CENTRAL INDUSTRIAL. WE ARE THE FUTURE." → **live tool menu** (clickable
  name + `[ ONLINE ]`/`[ OFFLINE ]` pill; clicking an offline tool calls `/api/launch`, polls,
  then opens it). `[MUTE]` toggle. Pepto C64 palette, CRT scanlines, Style64 **"C64 Pro Mono"**
  font vendored in `fonts/` (unmodified per its license).
- **Launch:** `launch.py` + `Special Projects.vbs`; auto-starts at login via `Special
  Projects.lnk` in `shell:startup`.

---

## 3. Monkey Read Monkey Do  (port 5005)

**Folder:** `D:\_______Claude\Monkey Read Monkey Do\`
**Docs:** `README.md` in that folder · agent memory `[[project-notemax]]`

Local **meeting-transcription + DD-notes** engine. Audio→transcript→notes, **100% local** (audio
never leaves the machine).

- **Stack:** Flask on `127.0.0.1:5005`; **uv** venv (Python 3.12, **torch cu128 on the RTX
  5090**). Loads ~18 GB VRAM when active — the heaviest GPU consumer in the suite.
- **Pipeline:** local **Whisper** (faster-whisper) transcription + **pyannote** speaker
  diarization → Claude-written DD notes. Output → "Monkey Read Monkey Do Output".
- **Launch:** `Monkey Read Monkey Do.vbs`; protocol `monkeyreadmonkeydo://`.
- **History:** formerly **"Note Max"** — fully renamed 2026-06-27 (folder, launcher, protocol,
  output all updated). The `NOTEMAX_*` env-var keys in its `.env` were intentionally kept. Built
  2026-06-19.

---

## 4. Deep Research  (port 5006)

**Folder:** `D:\_______Claude\Deep Research\`
**Detailed handoff:** [`../Deep Research/DEEP_RESEARCH_HANDOFF.md`](../Deep%20Research/DEEP_RESEARCH_HANDOFF.md) · memory `[[project-deep-research-standalone]]`

Browser-driven, multi-stage **due-diligence web research** → a **cited DD report** (markdown +
downloadable `.docx`). **Migrated 2026-06-27** out of the DDDD "Admin Tools" monolith into its own
standalone home.

- **Stack:** standalone Flask **`dr_server.py`** on `127.0.0.1:5006` that **serves its own
  `index.html`** → **same-origin** (the old version was an HTTPS GitHub-Pages SPA calling
  localhost:5002, which needed CORS; this is simpler). Own `.venv` (Python 3.14) — **DRT deps
  only, no torch/GPU**, so it coexists freely with MRMD.
- **Engine:** `engines/research/` copied **verbatim** from the monolith (it was already
  self-contained; keeping the folder layout makes its `dirname×3 → root + config/ + prompts/`
  path math resolve unchanged — zero code edits).
- **Pipeline:** Stage1 Claude API web_search → Stage2 browser engines → Stage3 credentialed
  sources (encrypted vault) → Stage4 new gated sources (batched in-app login prompt) →
  synthesize (per-page extract + cited report + stop-judge deepening) → report (+ collapsed
  harvest audit). Depth quick/standard/deep. Models: plan+synthesize Opus 4.8, search+route
  Sonnet 4.6, extract Haiku 4.5 (override in `config/drt_models.json`). Opens a **visible
  Chrome** (`channel="chrome"`) and uses **Anthropic API credits**.
- **Frontend libs vendored locally** (marked.js + Inter/JetBrains Mono fonts) → offline-capable.
- **Secrets carried over** from the old work-PC copy: the `ANTHROPIC_API_KEY` (`.env`) and the
  credential **vault** (`.drt_vault_key` + `drt_credentials.enc`, as a pair). Chrome profile
  started fresh.
- **Launch:** `Deep Research.vbs` → server-only `launch.py` (no browser open — the hub navigates
  to the tool).
- **Odysseus Research (sub-tool, 2026-06-27):** a headless A/B **comparison engine** (vendored
  Alibaba IterResearch loop; DuckDuckGo via `ddgs` + system `curl` page-fetch, same Claude key).
  **Not its own hub tile** — reached from a **left-sidebar link inside Deep Research** with a
  "← Deep Research" back button. Same `index.html` (`_or*` functions + `setView()`, rendered into a
  separate `#or-main-inner`); backend `/api/odysseus_research[/status]` in `dr_server.py`; engine in
  `engines/odysseus/` (copied verbatim, self-contained).
- **Very Very Deep (sub-tool, 2026-06-27):** a **chained two-pass** deep dive — third left-sidebar item.
  One query → **Pass 1** Odysseus pre-research → its findings + the query are injected as grounding context
  into **Pass 2**, the full Deep Research pipeline, which verifies/deepens and writes the DR-styled report
  (Odysseus pass appended as a collapsed section). Backend `/api/very_very_deep[/status]` + `_vvd_worker`,
  which reuses a shared `_dr_pipeline()` (the proven `_dr_worker` left untouched). Runs unattended — the
  Pass-2 DR run auto-skips new gated-login sources (vault logins still apply).
- **Firecrawl (sub-tool, 2026-06-28):** a data-driven **console over the Firecrawl v2 REST API** —
  fourth left-sidebar item. 11 tools (scrape/search/map/crawl/batch/extract/llms.txt + status),
  forms generated from an `FC_TOOLS` array; results render markdown + raw JSON; async tools give a
  job id → "Check status →". Backend = generic proxy `/api/firecrawl/<path>` in `dr_server.py`
  (injects `FIRECRAWL_API_KEY` server-side; key never in the browser). Rebuilt from
  `D:\_______Claude\Firecrawl\HANDOFF.md` (original source didn't transfer). The handoff's Firecrawl
  **MCP** registration is a separate Claude Code integration, not part of this sub-tool.
- **Relationship to the old DRT:** the full monolith at `D:\_______Claude\hedge_fund_dd` (:5002,
  memory `[[project-drt]]`) is unchanged and independent; this is an extraction, not a
  replacement.

---

## 5. Home Automation — Home Assistant Core  (port 8123)

**Folder:** `D:\_______Claude\Home Assistant\` (launcher only) · **app lives in WSL2:** `~/homeassistant/`
**Detailed handoff:** [`../Home Assistant/HOME_ASSISTANT_HANDOFF.md`](../Home%20Assistant/HOME_ASSISTANT_HANDOFF.md) · memory `[[project-home-assistant]]`

A local **[Home Assistant](https://www.home-assistant.io/) Core** instance — open-source home
automation (devices, dashboards, integrations, automations). Tile name on the hub is **"Home
Automation"**. Stood up 2026-06-27.

- **The one architectural exception in the suite: HA runs inside WSL2, not native Windows.** HA
  Core has C-extension deps with **no Windows wheels for Python 3.13** (e.g. `lru-dict`), so native
  `pip install` dead-ends on "MSVC required". It runs cleanly in **WSL2 Ubuntu 24.04** where all
  wheels exist. (HAOS-in-a-VM and HA-Container/Docker were the other options; Core-in-WSL was
  chosen to keep the lightweight one-host/one-port feel without Docker Desktop or a hypervisor.)
- **Install:** `~/homeassistant/.venv` — a **uv-managed Python 3.13** venv in the WSL home fs (NOT
  on `/mnt/d` — HA's SQLite recorder misbehaves on the DrvFs mount). `uv pip install homeassistant`
  → **HA 2026.2.3**. Required a one-time `sudo apt install build-essential python3-dev` in WSL (the
  user ran it) so `lru-dict` compiles. Config + DB live in `~/homeassistant/config/`.
- **Networking:** HA binds `0.0.0.0:8123` in WSL; **WSL2 localhost-forwarding** maps Windows
  `127.0.0.1:8123` → the hass process, so the hub's port-probe, the tile link, and the browser all
  just work — no special config.
- **Launch (suite pattern, WSL-adapted):** `Home Assistant.vbs` → `pythonw` → `launch.py`.
  `launch.py` port-checks Windows-side and, if down, `Popen`s `wsl.exe -d Ubuntu -- bash -lc 'exec
  <venv>/python -m homeassistant -c <config>'` **detached on the Windows side**. Two gotchas baked
  in: (1) invoke as **`python -m homeassistant`**, not the `hass` wrapper (its shebang exec
  misbehaves under this uv venv); (2) run HA in the **foreground** of wsl.exe (NOT `nohup … &`) —
  backgrounding makes bash exit instantly and WSL2 races to kill the distro before HA spawns.
- **Cosmetic boot warnings (non-blocking):** `libturbojpeg` missing (camera-snapshot perf only) and
  `libpcap` missing (the `dhcp` discovery integration only). Optional fix: `sudo apt install
  libturbojpeg0 libpcap0.8` in WSL. Neither affects onboarding or core use.
- **Status:** running; **onboarding not yet completed** — first visit to `127.0.0.1:8123` walks
  through creating the owner account. Auto-start at login is **not** wired yet (the hub launches it
  on demand; a `shell:startup` shortcut could be added later like the other tools).

---

## 6. Document map (how these files relate)

| Doc | Scope | Compact independently? |
|---|---|---|
| **`Special Projects Main.md`** (this file) | Master index + shared architecture | No — the durable entry point |
| `SPECIAL_PROJECTS_HANDOFF.md` | The hub (splash page) deep dive | Yes |
| `../Deep Research/DEEP_RESEARCH_HANDOFF.md` | Deep Research deep dive | Yes |
| `../Home Assistant/HOME_ASSISTANT_HANDOFF.md` | Home Assistant (Home Automation) deep dive | Yes |
| `../Monkey Read Monkey Do/README.md` | MRMD reference | Yes |

**Agent memory** mirrors this: `[[local-tools-host-convention]]` (the one-host/one-port
convention + port map + hub), `[[project-deep-research-standalone]]`, `[[project-notemax]]`,
`[[project-drt]]`, `[[project-home-assistant]]`, `[[env-claude-app-sandbox-virtualization]]`,
`[[env-wsl2-ubuntu]]`.

When resuming: read **this file** for the lay of the land, then open the specific tool's handoff
for depth.
