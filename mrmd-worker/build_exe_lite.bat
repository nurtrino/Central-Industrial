@echo off
REM ── Build the LITE helper: ReadMonkeyDoWorker-Lite.exe ─────────────────────
REM faster-whisper only (CTranslate2). NO torch, NO pyannote, NO speaker labels.
REM Small, fast. The runtime hook forces MRMD_LITE=1 so diarization stays off.
call .venv\Scripts\activate.bat

pyinstaller --noconfirm --onefile --windowed --name ReadMonkeyDoWorker-Lite ^
  --runtime-hook _lite_hook.py ^
  --paths "..\monkey-read-monkey-do" ^
  --hidden-import transcribe ^
  --hidden-import worker_server ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all nvidia ^
  --exclude-module torch ^
  --exclude-module pyannote ^
  --exclude-module whisper ^
  --hidden-import soundfile ^
  tray_app.py

echo.
echo Built: dist\ReadMonkeyDoWorker-Lite.exe  (small — no speaker diarization)
