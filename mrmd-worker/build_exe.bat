@echo off
REM ── Build the FULL helper: ReadMonkeyDoWorker-Full.exe ─────────────────────
REM Whisper + pyannote speaker diarization. LARGE (bundles torch + CUDA). Run on
REM Windows inside the mrmd-worker venv (after setup_env.sh). The pyannote/torch
REM collects can need tweaking per environment.
call .venv\Scripts\activate.bat

pyinstaller --noconfirm --onefile --windowed --name ReadMonkeyDoWorker-Full ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all torch ^
  --collect-all pyannote ^
  --hidden-import soundfile ^
  tray_app.py

echo.
echo Built: dist\ReadMonkeyDoWorker-Full.exe  (large — has speaker diarization)
