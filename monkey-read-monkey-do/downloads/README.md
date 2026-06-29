# downloads/

The hosted Read Monkey Do page serves the local transcription helper from here at
`/download/ReadMonkeyDoWorker.exe` (see the `/download/<fn>` route in `app.py`).

`ReadMonkeyDoWorker.exe` is the small (~8 MB) self-provisioning launcher built from
`../../mrmd-worker/` via `build_setup.bat`. It bundles no ML — on first run it fetches
`uv`, builds a venv with the pinned faster-whisper + CUDA stack, grabs ffmpeg, and
downloads the Whisper model, then serves the worker on `127.0.0.1:5007`.

Rebuild and drop the new exe here whenever `bootstrap.py` (or the pinned versions)
change. At ~8 MB it's small enough to commit; Render serves it straight from this folder.
