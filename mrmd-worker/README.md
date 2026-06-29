# Monkey Read Monkey Do — Local Transcription Helper

The **local** half of Monkey Read Monkey Do. The interface is hosted on the web
(`notes.centralindustrial.ai`); this helper runs on the **user's own machine** and does
the Whisper transcription on **that machine's GPU**. The hosted page talks to it on
`http://127.0.0.1:5007`, so **the audio never leaves the computer** — it's written to a
temp file, transcribed, and deleted; only the text transcript goes back.

## How a user gets it (one self-setup exe)
1. On the hosted page, click an audio mode and **Run**. If the helper isn't running,
   the page shows a **"Download the helper"** link → `ReadMonkeyDoWorker.exe` (~8 MB).
2. They run the exe. A console window shows a **one-time setup** (steps 1–5 below), then
   the worker starts and the page opens. Keep that window open while transcribing.
3. Back on the page, **Run** again → audio is transcribed locally on the GPU.

## What the exe does (`bootstrap.py`)
The download is tiny because it bundles **no** ML. On first run it provisions everything
into `%LOCALAPPDATA%\ReadMonkeyDo\`, then caches it for every later run:

1. fetch `uv` (single binary)
2. `uv venv` (downloads CPython 3.12 if needed)
3. `uv pip install` the **pinned** faster-whisper + CTranslate2 + cuBLAS/cuDNN stack
   (the exact versions proven on the RTX 5070 / Blackwell — see `REQUIREMENTS` in
   `bootstrap.py`)
4. fetch a static **ffmpeg/ffprobe**
5. download the **Whisper model** sized to this GPU's VRAM

Then it runs `worker_server.py` on `127.0.0.1:5007` in **Lite** mode (faster-whisper
only — no torch, no speaker labels). First run downloads **~4–5 GB** (CUDA libs + model)
and needs that much free disk; it's a one-time cost.

- **Endpoints:** `POST /transcribe` (multipart `file` → `{transcript, model}`),
  `GET /health`. CORS is locked to `https://notes.centralindustrial.ai`, with the
  Chrome Private-Network-Access header so an HTTPS page may call `127.0.0.1`. No token
  needed in the local model (127.0.0.1-only + CORS).
- **Auto model sizing** to the GPU's VRAM (≥12 GB `large-v3` … down to `tiny`); reuses
  [transcribe.py](../monkey-read-monkey-do/transcribe.py). Needs an NVIDIA GPU.

## Build the exe (on Windows)
```bash
cd mrmd-worker
bash setup_env.sh            # dev venv with PyInstaller (only needed to BUILD)
./build_setup.bat            # -> dist/ReadMonkeyDoWorker.exe  (~8 MB)
```
Then copy `dist/ReadMonkeyDoWorker.exe` to `../monkey-read-monkey-do/downloads/`
(served at `/download/ReadMonkeyDoWorker.exe`) and commit it.

## Advanced: remote/tunnel worker (one shared GPU box, not per-user)
The original model — one machine serves everyone over an HTTPS tunnel. Run
`worker_server.py` directly with a token, expose it via Tailscale Funnel / Cloudflare
Tunnel, and set `MRMD_WORKER_URL` + `MRMD_WORKER_TOKEN` on the hosted service (the page
prefers `cfg.worker_url` when set, else falls back to the local `127.0.0.1` helper).
Set `MRMD_ALLOWED_ORIGIN=https://notes.centralindustrial.ai` on the worker.
