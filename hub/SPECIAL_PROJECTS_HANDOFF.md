# Special Projects — Handoff

_Last updated: 2026-06-27_

## What this is

**Special Projects** is a Commodore-64-style **launcher hub** — a single "front door" web page that lists the user's local tools, shows each one's **live up/down status**, and links straight to it (starting it on demand if it's down). It is the home page for a growing suite of personal/local tools that all run on **one host (this machine), one loopback port each**.

The user grew up programming on a C64; the splash screen is a deliberate callback.

- **Folder:** `D:\_______Claude\Special Projects\`
- **Hub URL / bookmark:** `http://127.0.0.1:5050/`
- **Auto-starts at login** (Startup shortcut), and serves fresh from disk on every request.

## Files in `D:\_______Claude\Special Projects\`

| File | Role |
|---|---|
| `index.html` | The C64 page (boot screen → keypress → welcome → live tool menu). All UI + JS. |
| `hub_server.py` | **Stdlib-only** HTTP server on `127.0.0.1:5050`. No venv/pip — runs on system Python 3.14. Serves the page + `/api/status` + `/api/launch`. Sends `Cache-Control: no-store` on all responses. |
| `tools.json` | The tool **registry**. Add a tool = add one entry here; it appears on the page automatically with live status. |
| `launch.py` | Idempotent launcher: starts the hub server hidden if `:5050` isn't up, then opens the browser. |
| `Special Projects.vbs` | One-click/no-console launcher → runs `launch.py` via `pythonw`. Also what the Startup shortcut points at. |
| `fonts/C64_Pro_Mono-STYLE.woff2` (+ `.woff`) | The real **Style64 "C64 Pro Mono"** font, vendored locally (unmodified, original filename — required by its license). |
| `10-future_sound_of_london-central_industrial-eos.mp3` | Background music (loops, starts on first keypress). |
| `SPECIAL_PROJECTS_HANDOFF.md` | This file. |

## How the hub works

**`hub_server.py`** (system Python, stdlib `http.server`):
- `GET /` → `index.html`
- `GET /api/status` → `{"tools":[{id,name,url,up}, ...]}` — socket-probes each tool's port (`127.0.0.1:<port>`, 0.5s timeout) for true up/down.
- `GET /api/launch?id=<id>` → runs that tool's `launch` command (detached/windowless) so the hub can start a tool on demand.
- Overrides `end_headers` to send `Cache-Control: no-store, must-revalidate` + `Expires: 0` on everything → the browser never shows a stale page.

**`tools.json`** entry shape:
```json
{
  "id": "monkeyreadmonkeydo",
  "name": "Monkey Read Monkey Do",
  "url": "http://127.0.0.1:5005/",
  "port": 5005,
  "cwd": "D:\\_______Claude\\Monkey Read Monkey Do",
  "launch": ["wscript.exe", "D:\\_______Claude\\Monkey Read Monkey Do\\Monkey Read Monkey Do.vbs"]
}
```

**`index.html`** behaviour:
1. Boot screen: `**** COMMODORE 64 BASIC V2 ****` / `64K RAM SYSTEM …` / `READY.` / `LOAD"SPECIAL PROJECTS",8,1` with a blinking block cursor + a dim "PRESS ANY KEY" hint.
2. On the **first keypress / click / tap** (a real user gesture — required for audio autoplay): the background MP3 starts (loops forever, volume 0.7), the load sequence prints (`SEARCHING FOR SPECIAL PROJECTS` / `LOADING` / `READY.`), then `WELCOME TO CENTRAL INDUSTRIAL. WE ARE THE FUTURE.`, then the **live tool menu**.
3. Each tool row: `> <NAME> ....... [ ONLINE ]` — both the name and the status pill are **clickable links** to the tool. Status is **live** (polled): green `[ ONLINE ]` (C64 green `#9AD284`) when the port is up, red `[ OFFLINE ]` (`#9A6759`) when down. Clicking an offline tool calls `/api/launch`, shows `[STARTING ]`, polls, then opens it when up.
4. **`[MUTE]` button**, lower-right of the screen: appears once music starts; toggles `[MUTE]` ⇄ `[UNMUTE]` (pause/resume the MP3).
5. Status polling: initial fetch on boot, a quick re-check at 0.7s, then every 1.5s — so the pill flips to ONLINE within ~1s of a tool binding its port.

**Colors (Pepto C64 palette):** screen `#352879` (blue), text/border `#6C5EB5` (light blue), online `#9AD284`, offline `#9A6759`. Subtle CRT scanlines + glow. Text is `text-transform:uppercase`.

## Launch / auto-start

- **Bookmark** `http://127.0.0.1:5050/` is the front door.
- **Auto-start at login:** `Special Projects.lnk` in `shell:startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) → `wscript "…\Special Projects.vbs"`.
- **On demand:** double-click `Special Projects.vbs`.
- The launcher is idempotent (never double-starts).

## Registered tools

| Tool | Port | Notes |
|---|---|---|
| **Monkey Read Monkey Do** | 5005 | Local Whisper transcription + pyannote diarization + DD notes engine. **Formerly "Note Max"** — fully renamed 2026-06-27 (folder `D:\_______Claude\Monkey Read Monkey Do`, launcher `Monkey Read Monkey Do.vbs`, protocol `monkeyreadmonkeydo://`, output folder "Monkey Read Monkey Do Output"). The `NOTEMAX_*` env-var keys in its `.env` were intentionally kept. Audio stays 100% local; needs the RTX 5090 (loads ~18 GB VRAM). |
| **Deep Research** | 5006 | Browser-driven multi-stage DD web research → cited report. **Migrated 2026-06-27** out of the DDDD monolith into its own standalone home (`D:\_______Claude\Deep Research`, `dr_server.py` serves its own UI same-origin). Engine (`engines/research/`) copied verbatim; `.env` key + credential vault carried over; own venv (no torch/GPU). Opens a **visible Chrome** + uses Anthropic API credits. Launcher `Deep Research.vbs`. **Handoff: `D:\_______Claude\Deep Research\DEEP_RESEARCH_HANDOFF.md`.** |

## Host / port convention (one machine, one port per tool)

Reserve **5000–5099** for "my tools." Current map:

| Port | Tool |
|---|---|
| 5000 | `webcrawler` (`D:\_______Claude\webcrawler`) |
| 5002 | HFDD / DRT (Flask backend, `D:\_______Claude\hedge_fund_dd`) |
| 5005 | Monkey Read Monkey Do |
| 5006 | Deep Research (standalone DRT migration) |
| 5050 | **Special Projects hub** (the front door) |
| 8765 | heos-control (demo) |
| 5006+ | future tools |

**GPU VRAM is the one shared bottleneck** — design GPU tools to lazy-load models and release when idle so only one does heavy work at a time. Keep **per-tool venvs** (torch/CUDA versions diverge). Web/CPU tools coexist freely.

## ▶ Adding / migrating a new tool (the next task)

The user is about to **migrate a tool built on a different machine** into Special Projects. Steps:

1. **Get the tool running locally** on this machine on a free port in the 5000–5099 block (its own venv if it has Python deps). Verify it serves on `127.0.0.1:<port>`.
2. **Register it** — add one object to the `tools` array in `tools.json`:
   - `id` (kebab/lower, internal), `name` (display), `url` (`http://127.0.0.1:<port>/`), `port`, `cwd`, `launch` (argv list for its launcher, e.g. `["wscript.exe","<path-to>.vbs"]` or `["<venv pythonw>","<app>.py"]`).
3. It then **auto-appears** on the hub with a live status pill — no page/code change needed.
4. Optional: give it the same launch conveniences as MRMD (a `*.vbs` idempotent launcher + a Startup shortcut + a `<name>://` protocol `.reg`).
5. Add it to the port map above and to memory.

## ⚠️ Critical environment gotcha — Claude desktop app sandbox

This Claude Code app runs as a **packaged Windows app** (`Claude_pzs8sxrjxfjjc`). **Filesystem writes under `%LOCALAPPDATA%` and registry/`setx`/User-PATH writes from the agent's shell are VIRTUALIZED** into the app's private store and are **invisible to the user's real environment**. Therefore:
- **Installers that pipe to shell, registry/protocol `.reg`, PATH/env changes, and anything under `%LOCALAPPDATA%` must be run by the USER in their own terminal**, not via the agent's Bash/PowerShell.
- **NOT virtualized (agent writes are real):** the user profile root (`C:\Users\crouchingyeti\…`), `%APPDATA%` Roaming (incl. npm globals and the **Startup** folder), and other drives like `D:\`. That's why the hub, MRMD, the Startup shortcuts, and the `.reg` files (written to `D:\`) all work — but the `.reg` still has to be **double-clicked by the user** to merge into the real registry.

## 🛠️ Dev/verify trick used this session

The user's hub holds port **5050**, so the Claude **preview** tool can't bind it. To screenshot/verify changes: temporarily set `PORT = 5051` in `hub_server.py` **and** `"port": 5051` in `.claude/launch.json` → `preview_start special-projects-hub` → eval `dispatchEvent(new KeyboardEvent('keydown'))` to boot → screenshot → then **revert both back to 5050**. To simulate a tool being ONLINE for a screenshot, run a throwaway `python -m http.server 5005` (kill it after). The relocatable venv (MRMD) works after a folder rename because the launcher invokes `.venv\Scripts\pythonw.exe` directly (verified).

## State at handoff

- Hub running on `:5050` (restarted with no-cache headers; new PID at the time was 2264, pythonw).
- Monkey Read Monkey Do running on `:5005` and showing **ONLINE** on the hub.
- `.claude/launch.json` has a `special-projects-hub` config (port 5050) for the preview tool.
- All temp 5051 edits reverted; ports clean (5050 hub, 5005 MRMD).

## Related memory (agent auto-memory)

`[[local-tools-host-convention]]`, `[[project-notemax]]` (Monkey Read Monkey Do), `[[env-claude-app-sandbox-virtualization]]`.
