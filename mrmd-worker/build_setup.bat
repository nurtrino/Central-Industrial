@echo off
REM ── Build the SELF-PROVISIONING launcher: ReadMonkeyDoWorker.exe ───────────
REM Tiny (~10 MB) console exe — pure stdlib. On first run it fetches uv, builds a
REM venv, pip-installs the pinned faster-whisper + CUDA stack, grabs ffmpeg, and
REM downloads the Whisper model, then runs the local worker on 127.0.0.1:5007.
REM The heavy ML is NOT bundled — it's provisioned at runtime — so this builds fine
REM even on a nearly-full disk.
call .venv\Scripts\activate.bat

pyinstaller --noconfirm --onefile --console --name ReadMonkeyDoWorker ^
  --add-data "worker_server.py;payload\mrmd-worker" ^
  --add-data "..\monkey-read-monkey-do\transcribe.py;payload\monkey-read-monkey-do" ^
  bootstrap.py

echo.
echo Built: dist\ReadMonkeyDoWorker.exe
