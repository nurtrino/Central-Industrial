# downloads/

The hosted Read Monkey Do page serves the local transcription helper from here at
`/download/ReadMonkeyDoWorker.exe` (see the `/download/<fn>` route in `app.py`).

`ReadMonkeyDoWorker.exe` is the small (~9 MB) self-provisioning launcher built from
`../../mrmd-worker/` via `build_setup.bat`. It bundles no ML — on first run it fetches
`uv`, builds a venv with the pinned faster-whisper stack (adding the CUDA libraries only
when an NVIDIA GPU is present), grabs ffmpeg, and downloads the Whisper `small` model,
then serves the worker on `127.0.0.1:5007`. No GPU required: ~1 GB one-time on a
GPU-less PC, ~3 GB with the CUDA libraries.

Rebuild and drop the new exe here whenever `bootstrap.py`, `worker_server.py`,
`transcribe.py` or the pinned versions change. At ~9 MB it's small enough to commit;
Render serves it straight from this folder.
