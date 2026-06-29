#!/usr/bin/env bash
# Monkey Read Monkey Do — one-time GPU stack provisioning.
# Builds an isolated Python 3.12 venv (uv) with a CUDA 12.8 PyTorch for the RTX 5090,
# then faster-whisper / openai-whisper / pyannote / flask / anthropic / python-docx.
set -e
cd "D:/_______Claude/Monkey Read Monkey Do"

echo "[1/6] Installing Python 3.12 via uv..."
uv python install 3.12

echo "[2/6] Creating venv (.venv)..."
uv venv --python 3.12 .venv

VPY=".venv/Scripts/python.exe"

echo "[3/6] Installing CUDA 12.8 PyTorch (Blackwell sm_120)..."
uv pip install --python "$VPY" \
  torch torchaudio --index-url https://download.pytorch.org/whl/cu128

echo "[4/6] Installing transcription + diarization stack..."
uv pip install --python "$VPY" \
  faster-whisper openai-whisper "pyannote.audio>=3.1" "numpy<2.3"

echo "[5/6] Installing web + notes + docx stack..."
uv pip install --python "$VPY" \
  flask anthropic python-docx python-dotenv

echo "[6/6] Verifying GPU is visible to torch..."
"$VPY" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"

echo "DONE: Monkey Read Monkey Do environment ready."
