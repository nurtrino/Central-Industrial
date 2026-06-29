# PyInstaller runtime hook for the Lite build: force Whisper-only (no torch/pyannote).
import os
os.environ["MRMD_LITE"] = "1"
