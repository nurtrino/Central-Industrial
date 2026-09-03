# Monkey Read Monkey Do — Local Transcription Helper

The **local** half of Monkey Read Monkey Do. The interface is hosted on the web
(`notes.centralindustrial.ai`); this helper runs on the **user's own machine** and does
the Whisper transcription there — on the NVIDIA GPU if the PC has one, otherwise on the
CPU. **No GPU is required.** The hosted page talks to it on `http://127.0.0.1:5007`, so
**the audio never leaves the computer** — it's written to a temp file, transcribed, and
deleted; only the text transcript goes back.

## How a user gets it (one self-setup exe)
1. On the hosted page, the status line shows **"Download the helper"** whenever no helper
   is detected → `ReadMonkeyDoWorker.exe` (~9 MB).
2. They run the exe. A console window shows a **one-time setup** (steps 1–5 below), then
   the worker starts and the page opens. Keep that window open while transcribing.
3. The page polls `/health` every 10 s and flips to *"✓ connected — Whisper small on CPU"*
   (or *"… large-v3 on GPU · RTX …"*) by itself; **Run** then transcribes locally.

## What the exe does (`bootstrap.py`)
The download is tiny because it bundles **no** ML. On first run it provisions everything
into `%LOCALAPPDATA%\ReadMonkeyDo\`, then caches it for every later run:

1. fetch `uv` (single binary)
2. `uv venv` (downloads CPython 3.12 if needed)
3. `uv pip install` the **pinned** faster-whisper + CTranslate2 stack — plus the
   cuBLAS/cuDNN CUDA wheels **only when `nvidia-smi` finds an NVIDIA GPU** (those exact
   versions are what work on RTX 50xx / Blackwell — see `_REQ_CUDA` in `bootstrap.py`)
4. fetch a static **ffmpeg/ffprobe**
5. download the **Whisper model** — default **`small`** (~465 MB)

Then it runs `worker_server.py` on `127.0.0.1:5007` in **Lite** mode (faster-whisper
only — no torch, no speaker labels). First run downloads **~1 GB** on a GPU-less PC
(**~3 GB** with the CUDA libraries); it's a one-time cost.

- **Compute** is decided torch-free by `transcribe.py::_pick_ct2_device`: GPU → float16 in
  VRAM with beam search; no GPU → CPU with **int8** weights and greedy decoding (the
  lightest setting). A GPU that CTranslate2 can't actually use falls back to CPU instead
  of failing.
- **Model:** `small` by default everywhere. Bigger is opt-in and remembered:
  `ReadMonkeyDoWorker.exe --model large-v3` (`tiny`…`large-v3`, `--help` lists sizes).
- **Flags / env:** `--cpu` (remembered; `--gpu` forgets it) forces the CPU path even with
  a GPU; `--no-browser` / `MRMD_NO_BROWSER=1` skips opening the site; `MRMD_PORT`
  changes the port (tests); `MRMD_FORCE_CPU=1` = `--cpu` without remembering.
- **Endpoints:** `POST /transcribe` (multipart `file` → streamed progress, then
  `{transcript, model}`), `GET /health` → `{ok, model, device, compute_type, gpu,
  gpu_name, diarization…}`. CORS is locked to `https://notes.centralindustrial.ai`, with
  the Chrome Private-Network-Access header so an HTTPS page may call `127.0.0.1`. No
  token needed in the local model (127.0.0.1-only + CORS).
- Reuses [transcribe.py](../monkey-read-monkey-do/transcribe.py) from the sibling tool.

## Build the exe (on Windows)
Only PyInstaller is needed to build — the exe is pure stdlib (no torch, no ML):
```bash
cd mrmd-worker
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe pyinstaller
./build_setup.bat            # -> dist/ReadMonkeyDoWorker.exe  (~9 MB)
```
Then copy `dist/ReadMonkeyDoWorker.exe` to `../monkey-read-monkey-do/downloads/`
(served at `/download/ReadMonkeyDoWorker.exe`) and commit it. To test a build as a
brand-new PC without touching your real install, point `LOCALAPPDATA` at a scratch
folder and run it with `MRMD_FORCE_CPU=1 MRMD_PORT=5017 MRMD_NO_BROWSER=1`.

(`setup_env.sh` builds the much heavier torch/pyannote venv used only by the advanced
tunnel worker below — not needed for the exe.)

## Advanced: remote/tunnel worker (one shared GPU box, not per-user)
The original model — one machine serves everyone over an HTTPS tunnel. Run
`worker_server.py` directly with a token, expose it via Tailscale Funnel / Cloudflare
Tunnel, and set `MRMD_WORKER_URL` + `MRMD_WORKER_TOKEN` on the hosted service (the page
prefers `cfg.worker_url` when set, else falls back to the local `127.0.0.1` helper).
Set `MRMD_ALLOWED_ORIGIN=https://notes.centralindustrial.ai` on the worker.
