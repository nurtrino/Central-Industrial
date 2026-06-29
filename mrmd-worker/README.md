# Monkey Read Monkey Do — Local Transcription Helper

The **local** half of Monkey Read Monkey Do. The interface is hosted on the web
(`notes.centralindustrial.ai`); this helper runs on the **user's own machine** and does
the Whisper transcription on **that machine's GPU**. The hosted page talks to it on
`http://127.0.0.1:5007`, so **the audio never leaves the computer** — it's written to a
temp file, transcribed, and deleted; only the text transcript goes back.

## How a user gets it (the tray app)
1. On the hosted page, click an audio mode and **Run**. If the helper isn't running,
   the page shows a **"Download it"** link → `ReadMonkeyDoWorker.exe`.
2. They run the exe. It lives in the **system tray**, starts on login, and serves the
   local transcription endpoint. Right-click the tray icon for status / open the app /
   start-on-login toggle / quit.
3. Back on the page, **Run** again → audio is transcribed locally, notes are written by
   the hosted app.

`tray_app.py` is the entry point; it runs `worker_server.py` (the Flask transcription
server) on `127.0.0.1` and adds the tray UI + start-on-login.

- **Endpoints:** `POST /transcribe` (multipart `file` → `{transcript, model}`),
  `GET /health`. CORS is locked to `https://notes.centralindustrial.ai`, with the
  Chrome Private-Network-Access header so an HTTPS page may call `127.0.0.1`. No token
  needed in the local model (127.0.0.1-only + CORS).
- **Auto model sizing** to the GPU's VRAM (≥12 GB `large-v3` … down to `tiny`); reuses
  [transcribe.py](../monkey-read-monkey-do/transcribe.py). Needs an NVIDIA GPU.
- **Diarization** (speaker labels) is optional — set `HF_TOKEN` (pyannote). It's the
  heavy part; the default is fast Whisper-only.

## Build the exe (on Windows)
```bash
cd mrmd-worker
bash setup_env.sh            # venv: faster-whisper + (optional) pyannote + pystray + pyinstaller
./build_exe.bat             # -> dist/ReadMonkeyDoWorker.exe
```
Then host it: copy `dist/ReadMonkeyDoWorker.exe` to
`../monkey-read-monkey-do/downloads/` (served at `/download/ReadMonkeyDoWorker.exe`),
**or** upload it to a GitHub Release and point the page's link there (better for a
large binary).

> **Size:** the tray app is tiny, but the GPU engine isn't. faster-whisper (CTranslate2)
> is the light path; bundling torch + pyannote (full diarization) makes the exe large.
> For a genuinely small download, build the faster-whisper-only engine and fetch the
> model on first run.

## Run from source (dev / testing)
```bash
./.venv/Scripts/python.exe tray_app.py     # tray helper on 127.0.0.1:5007
```

## Advanced: remote/tunnel worker (one shared GPU box, not per-user)
The original model — one machine serves everyone over an HTTPS tunnel. Run
`worker_server.py` directly with a token, expose it via Tailscale Funnel / Cloudflare
Tunnel, and set `MRMD_WORKER_URL` + `MRMD_WORKER_TOKEN` on the hosted service (the page
prefers `cfg.worker_url` when set, else falls back to the local `127.0.0.1` helper).
Set `MRMD_ALLOWED_ORIGIN=https://notes.centralindustrial.ai` on the worker.
