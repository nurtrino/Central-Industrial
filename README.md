# Central Industrial

A small suite of personal tools fronted by a Commodore-64–style launcher. This
repo is set up to deploy to **[Render](https://render.com)** from GitHub via a
Blueprint (`render.yaml`).

```
central-industrial/
├── render.yaml              ← Render Blueprint (cloud services)
├── hub/                     ← C64 landing page  (Render: web service, stdlib Python)
├── deep-research/           ← browser-driven research  (Render: Docker + headless Chromium)
├── trivia/                  ← WedgeQuest: 2–6 player trivia board game (Render: Python)
└── monkey-read-monkey-do/   ← local-only transcription+notes (runs on your GPU; NOT deployed)
```

## What runs where, and why

| Tool | Where | Notes |
|---|---|---|
| **Central Industrial hub** | Render (Python) | The C64 boot screen / front door. Links to each tool; shows live status. |
| **Deep Research** | Render (Docker) | Headless Chromium via Playwright + Claude API → cited report. |
| **WedgeQuest** | Render (Python) | Multiplayer wheel-of-wedges trivia at `trivia.centralindustrial.ai`. **Not** behind the access gate — guests join with a 4-letter room code. Questions: Open Trivia DB. See [trivia/README.md](trivia/README.md). |
| **Monkey Read Monkey Do** | **Your local GPU box** | Whisper + pyannote need a CUDA GPU, and the design keeps **audio 100% local**. Hosting it would mean uploading confidential audio to the cloud — so it stays local. The hub shows it tagged `[ LOCAL ]`. |
| ~~Home Assistant~~ | dropped | Was a Windows→WSL2 launcher; not portable to Render. Folder kept locally but git-ignored. |

> **Why no GPU service on Render?** Render does not offer GPU instances. Deep
> Research needs no GPU; MRMD's transcription does, which is the main reason MRMD
> stays on your local machine (an RTX-class GPU).

## Deploy to Render

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → pick the repo. It reads `render.yaml` and creates
   two services: `deep-research` (Docker) and `central-industrial-hub` (Python).
3. After the first deploy, set environment variables:
   - **deep-research** → `ANTHROPIC_API_KEY` (required); optional `EXA_API_KEY`+`DRT_EXA=1`, `FIRECRAWL_API_KEY`.
   - **central-industrial-hub** → `HUB_URL_MONKEYREADMONKEYDO` = the URL where your
     local MRMD is reachable (e.g. a [Tailscale](https://tailscale.com) address).
     `HUB_URL_DEEP_RESEARCH` is wired automatically from the Deep Research service.
4. Open the hub's URL. Deep Research shows `[ ONLINE ]`; MRMD shows `[ LOCAL ]`.

**Cost:** Deep Research is set to the `standard` plan (Chromium wants RAM); the hub
to `starter`. Lower these in `render.yaml` if you like (Chromium may OOM on 512 MB).

## Run locally

**Hub** (no dependencies):
```bash
cd hub && python hub_server.py        # → http://127.0.0.1:5050
```

**Deep Research** (needs Python 3.12 + Google Chrome for the headed local mode):
```bash
cd deep-research
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env                   # then set ANTHROPIC_API_KEY
python dr_server.py                    # → http://127.0.0.1:5006
```
Locally it defaults to **visible Chrome** (`DRT_HEADED=1`, `DRT_BROWSER_CHANNEL=chrome`).
The Docker image flips both to headless Chromium.

**Monkey Read Monkey Do** (local GPU): see [monkey-read-monkey-do/README.md](monkey-read-monkey-do/README.md).
Requires a CUDA GPU, an `HF_TOKEN`, and `ANTHROPIC_API_KEY`.

## Environment variables

| Service | Var | Purpose |
|---|---|---|
| deep-research | `ANTHROPIC_API_KEY` | **Required.** Claude API. |
| deep-research | `DRT_HEADED` | `1` visible (local), `0` headless (Render). |
| deep-research | `DRT_BROWSER_CHANNEL` | `chrome` (local) or `""` (bundled Chromium). |
| deep-research | `DRT_EXA`, `EXA_API_KEY`, `FIRECRAWL_API_KEY` | Optional integrations. |
| deep-research | `DRT_REPORTS_DIR` | Optional `.docx` save dir (default `./reports`). |
| hub | `HUB_URL_DEEP_RESEARCH` | Deep Research URL (auto-wired on Render). |
| hub | `HUB_URL_MONKEYREADMONKEYDO` | Your local/Tailscale MRMD URL. |
| both | `PORT`, `HOST` | Injected by Render; default `5050`/`5006`, host `0.0.0.0`. |
