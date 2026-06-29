#!/usr/bin/env bash
# Monkey Read Monkey Do — GPU worker: one-time environment provisioning.
# Builds an isolated Python 3.12 venv (uv) with CUDA 12.8 PyTorch + the transcription
# / diarization stack. NO notes/docx stack here — the worker only transcribes; the
# hosted UI does the Claude notes. Run on the HOME GPU machine.
set -e
cd "$(dirname "$0")"

echo "[1/5] Installing Python 3.12 via uv..."
uv python install 3.12

echo "[2/5] Creating venv (.venv)..."
uv venv --python 3.12 .venv

# Pick the venv python for this OS.
if [ -f ".venv/Scripts/python.exe" ]; then VPY=".venv/Scripts/python.exe"; else VPY=".venv/bin/python"; fi

echo "[3/5] Installing CUDA 12.8 PyTorch (Blackwell sm_120)..."
uv pip install --python "$VPY" \
  torch torchaudio --index-url https://download.pytorch.org/whl/cu128

echo "[4/5] Installing transcription + diarization + server + tray/build stack..."
uv pip install --python "$VPY" \
  faster-whisper openai-whisper "pyannote.audio>=3.1" "numpy<2.3" \
  soundfile flask python-dotenv \
  pystray pillow pyinstaller

echo "[5/5] Verifying GPU is visible to torch..."
"$VPY" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"

echo "DONE: worker environment ready. Now: copy .env.example -> .env, fill it in, then run worker_server.py"
