# Monkey Read Monkey Do — GPU Worker

The **local** half of Monkey Read Monkey Do. The interface is hosted on the web
(Render); this worker runs on your **home GPU machine** and does all the Whisper +
diarization work. **Audio never leaves this machine** — the browser uploads it
straight here (over a private HTTPS tunnel), the worker transcribes locally, and
returns only the **text transcript**.

```
Browser ──audio──▶ this worker (Whisper on your GPU)        [audio stays local]
   │                    │ transcript (text)
   └─▶ hosted UI ◀──────┘
        └─ writes notes via Claude, serves the result
```

## What it does
- `POST /transcribe` — multipart `file` (audio/video) → `{ transcript, model, ... }`.
  Token-protected; CORS-locked to the hosted UI's origin. Audio is written to a temp
  file and **deleted immediately** after transcription.
- `GET /health` — `{ gpu, gpu_name, vram_gb, model, diarization, token_required }`.
- **Auto model sizing** — picks a Whisper model that fits your VRAM (≥12 GB
  `large-v3`, ≥8 GB `medium`, ≥5 GB `small`, ≥3 GB `base`, else `tiny`). Override with
  `NOTEMAX_WHISPER_MODEL`. Reuses [transcribe.py](../monkey-read-monkey-do/transcribe.py).

> Needs a CUDA GPU — it refuses CPU. This machine's GPU determines the model: e.g. an
> 8 GB card auto-selects `medium`; the RTX 5090 gets `large-v3`.

## Setup (home GPU machine)
```bash
cd mrmd-worker
bash setup_env.sh                       # builds .venv with cu128 torch + whisper + pyannote
cp .env.example .env                    # then fill in MRMD_WORKER_TOKEN, HF_TOKEN, MRMD_ALLOWED_ORIGIN
./.venv/Scripts/python.exe worker_server.py     # Windows  (Linux: ./.venv/bin/python …)
```
Also needs **ffmpeg/ffprobe** on PATH (same as the original tool).

## Expose it (so the hosted UI can reach it)
The hosted page is HTTPS, so the worker needs an HTTPS URL. Easiest options:

- **Tailscale Funnel** (free, no domain):
  ```bash
  tailscale funnel 5007
  ```
  → gives `https://<machine>.<tailnet>.ts.net`.
- **Cloudflare Tunnel** (free, with a domain):
  ```bash
  cloudflared tunnel --url http://localhost:5007
  ```

Then on the hosted UI (Render `monkey-read-monkey-do` service) set:
- `MRMD_WORKER_URL` = that HTTPS URL
- `MRMD_WORKER_TOKEN` = the same token you put in this worker's `.env`

…and set this worker's `MRMD_ALLOWED_ORIGIN` to the UI's origin (e.g.
`https://monkey-read-monkey-do.onrender.com`).

## Security
- The token is **required** — with none set, every job is refused (no open
  transcription endpoint).
- Keep `MRMD_ALLOWED_ORIGIN` pinned to your UI's origin (not `*`) once deployed.
- The tunnel exposes only this worker; it serves nothing but `/health` and
  `/transcribe`.
