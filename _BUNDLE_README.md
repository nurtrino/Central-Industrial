# Special Projects Bundle — Handoff

_Created 2026-06-27 · source machine: the user's RTX 5090 Windows 11 workstation_

This archive contains the **Special Projects C64 launcher hub** and the **three tools it boots**, as source/config (not the heavy or secret bits — see exclusions). It's a portable handoff/backup, not a turnkey runtime — venvs and secrets must be recreated on the target machine.

## Contents

| Folder | Port | What it is | Launcher |
|---|---|---|---|
| `Special Projects/` | **5050** | The Commodore-64-style **launcher hub** (front door). Lists the tools, live up/down status, click to open / start. Stdlib-only server (`hub_server.py`), no venv. | `Special Projects.vbs` |
| `Monkey Read Monkey Do/` | **5005** | Local Whisper transcription + pyannote diarization + DD notes engine (formerly "Note Max"). Audio stays 100% local; needs an NVIDIA GPU (RTX 5090, ~18 GB VRAM). | `Monkey Read Monkey Do.vbs` |
| `Deep Research/` | **5006** | Browser-driven multi-stage DD web research → cited report. Drives a visible Chrome; uses Anthropic API. | `Deep Research.vbs` |
| `Home Assistant/` | **8123** | Launcher only — **Home Assistant Core runs in WSL2**, not in this folder (see its handoff). | `Home Assistant.vbs` |

Each folder keeps its own detailed handoff: `SPECIAL_PROJECTS_HANDOFF.md`, `Monkey Read Monkey Do/README.md`, `DEEP_RESEARCH_HANDOFF.md`, `HOME_ASSISTANT_HANDOFF.md`. **Read those for per-tool detail.**

## ❗ What was EXCLUDED (and why) — must be recreated on restore

- **Python virtual envs** (`.venv/`) — machine-specific, multi-GB (MRMD's torch+CUDA is ~5 GB). Recreate from each tool's deps.
- **Live secrets** — every `.env` file, plus Deep Research's credential vault (`config/.drt_vault_key` + `config/drt_credentials.enc`). **Re-enter your own keys/credentials.**
- **Caches / runtime / large data** — `__pycache__/`, `*.log`, MRMD `uploads/` (your audio) + `work/` (job outputs), Deep Research `.drt_chrome_profile/` (browser session), `.git/`.

Everything else is here: all source, HTML/JS/CSS, launchers (`.vbs`/`launch.py`), `tools.json`, fonts, UI assets/music, `.reg` protocol files, `requirements.txt`, `setup_env.sh`, `.env.example`, and the config JSONs.

## Restore on a new machine

1. **Paths are hard-coded to `D:\_______Claude\<tool>`.** Easiest path: drop these folders back under `D:\_______Claude\`. If you relocate them, update the absolute paths in: each tool's `tools.json` entry (`cwd`/`launch`), the `.vbs` launchers, `launch.py`, and the `.reg` files.
2. **Recreate venvs:**
   - *Special Projects hub* — none (runs on system Python 3.x via `pythonw`).
   - *Monkey Read Monkey Do* — `uv` venv, Python 3.12, `torch 2.x+cu128` for the GPU. See `Monkey Read Monkey Do/setup_env.sh` + `README.md`. Requires a CUDA GPU (it refuses CPU fallback).
   - *Deep Research* — `python -m venv .venv` then `pip install -r requirements.txt`. No GPU. Needs Playwright/Chrome for the browser stage (see its handoff).
3. **Recreate secrets** — copy `.env.example`→`.env` where present and fill in keys (MRMD: `HF_TOKEN`, `ANTHROPIC_API_KEY`, optional `NOTEMAX_*`). Deep Research: its `.env` + re-create the credential vault (see `DEEP_RESEARCH_HANDOFF.md`).
4. **Home Assistant** — install HA Core in WSL2 per `HOME_ASSISTANT_HANDOFF.md`; the Windows-side folder is just the launcher bridge.
5. **Wire into the hub** — the three tools are already registered in `Special Projects/tools.json`. Launch the hub (`Special Projects.vbs`) and bookmark `http://127.0.0.1:5050/`. Optionally re-add Startup shortcuts and run the `.reg` files (registry — must be done in your own terminal/Explorer).

## How the hub works (quick)

`hub_server.py` (stdlib `http.server`, port 5050) serves `index.html` and two APIs: `GET /api/status` (socket-probes each tool's port for live up/down) and `GET /api/launch?id=<id>` (runs a tool's `launch` command on demand). The page is the C64 boot screen → press any key (starts looping music + reveals the menu) → each tool is a clickable row with a live `[ ONLINE ]`/`[ OFFLINE ]` pill. Add a tool = one entry in `tools.json` (`id`, `name`, `url`, `port`, `cwd`, `launch`) and it auto-appears.

## Host convention

One machine, one loopback port per tool, reserve **5000–5099**. Map: 5000 webcrawler · 5002 HFDD/DRT · 5005 Monkey Read Monkey Do · 5006 Deep Research · 5050 hub · 8123 Home Assistant. **GPU VRAM is the shared bottleneck** — only one heavy GPU tool at a time.

## ⚠️ Note on the original environment

These were built/maintained via Claude Code running inside a **packaged (sandboxed) Windows app**, where the agent's writes to `%LOCALAPPDATA%` and the registry are virtualized. Consequence for restore: **registry/`.reg`, PATH/env, and Startup-folder changes must be done from your own terminal/Explorer**, not delegated to a sandboxed agent. Files on `D:\`, the user profile root, and `%APPDATA%\Roaming` are real.
