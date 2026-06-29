@echo off
REM ── Build ReadMonkeyDoWorker.exe ───────────────────────────────────────────
REM Run on Windows inside the mrmd-worker venv (after setup_env.sh). Produces a
REM single-file system-tray app: dist\ReadMonkeyDoWorker.exe
REM
REM SIZE NOTE: this bundles the GPU transcription engine. faster-whisper (CTranslate2)
REM is the light path; pulling in torch + pyannote (full diarization) makes the exe
REM very large. The hidden-imports below cover the lazy imports in transcribe.py. For
REM a genuinely small download, build the faster-whisper-only engine (no torch /
REM pyannote) and provision the model on first run — see README.

call .venv\Scripts\activate.bat

pyinstaller --noconfirm --onefile --windowed --name ReadMonkeyDoWorker ^
  --hidden-import faster_whisper ^
  --hidden-import ctranslate2 ^
  --hidden-import soundfile ^
  --collect-data faster_whisper ^
  tray_app.py

echo.
echo Built: dist\ReadMonkeyDoWorker.exe
echo Next: copy it to ..\monkey-read-monkey-do\downloads\  (or upload to a GitHub Release
echo       and point the page's download link there).
