@echo off
REM ── Build the SELF-PROVISIONING launcher: ReadMonkeyDoWorker.exe ───────────
REM Tiny (~9 MB) console exe — pure stdlib. On first run it fetches uv, builds a
REM venv, pip-installs the pinned faster-whisper stack (+ the CUDA libraries ONLY
REM when an NVIDIA GPU is present), grabs ffmpeg, and downloads the Whisper model
REM (default 'small'), then runs the local worker on 127.0.0.1:5007. No GPU needed.
REM The heavy ML is NOT bundled — it's provisioned at runtime — so this builds fine
REM even on a nearly-full disk.
REM
REM Build venv: only PyInstaller is needed here (no torch, no ML):
REM     uv venv .venv --python 3.12
REM     uv pip install --python .venv\Scripts\python.exe pyinstaller
REM Afterwards copy dist\ReadMonkeyDoWorker.exe to ..\monkey-read-monkey-do\downloads\
REM (served at /download/ReadMonkeyDoWorker.exe) and commit it.
REM
REM Calls PyInstaller through the venv's python directly — no activate.bat, so it
REM works the same from cmd, PowerShell, or a Git Bash `cmd //c`.

.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --console --name ReadMonkeyDoWorker ^
  --add-data "worker_server.py;payload\mrmd-worker" ^
  --add-data "..\monkey-read-monkey-do\transcribe.py;payload\monkey-read-monkey-do" ^
  bootstrap.py

echo.
echo Built: dist\ReadMonkeyDoWorker.exe
