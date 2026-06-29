"""
Monkey Read Monkey Do — GPU transcription worker (runs on the HOME machine).

WHY THIS EXISTS
The Monkey Read Monkey Do *interface* is hosted on the web (Render), but Whisper +
diarization must run on a local GPU AND the audio must never touch the cloud. So the
hosted UI has the browser upload audio DIRECTLY to this worker over a private HTTPS
tunnel (e.g. Tailscale Funnel / Cloudflare Tunnel). This worker transcribes locally
and returns ONLY the text transcript. Audio bytes stay on this machine (written to a
temp file, deleted immediately after).

RUN (needs a CUDA GPU + the worker venv from setup_env.sh):
    cd mrmd-worker
    ./.venv/Scripts/python.exe worker_server.py      # Windows
    ./.venv/bin/python worker_server.py              # Linux
Then expose it on an HTTPS tunnel and point the hosted UI's MRMD_WORKER_URL at it.

ENV (see .env.example):
  MRMD_WORKER_TOKEN     shared secret; the hosted UI must send it (REQUIRED to accept jobs)
  MRMD_ALLOWED_ORIGIN   CORS origin of the hosted UI, e.g. https://mrmd.onrender.com (default *)
  HF_TOKEN              HuggingFace token for pyannote diarization
  NOTEMAX_WHISPER_MODEL force a Whisper model (else auto-picked from available VRAM)
  PORT                  default 5007
  NOTEMAX_OFFLINE       1 = fully offline audio stage (cached models only)
  MRMD_MAX_UPLOAD_MB    max upload size, default 4096
"""
import hmac
import os
import sys
import tempfile
import traceback

from flask import Flask, request, jsonify

# Reuse the existing local transcription pipeline from the sibling tool. Only its
# light top-level imports (os/subprocess/tempfile) load here; torch/whisper/pyannote
# are imported lazily inside the functions we call.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MRMD = os.path.join(os.path.dirname(_HERE), "monkey-read-monkey-do")
sys.path.insert(0, _MRMD)
import transcribe as T  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_HERE, ".env"))
except Exception:
    pass

# Privacy / offline hardening (mirror the main tool).
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
if os.environ.get("NOTEMAX_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on"):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"

PORT = int(os.environ.get("PORT", "5007"))
WORKER_TOKEN = os.environ.get("MRMD_WORKER_TOKEN", "").strip()
ALLOWED_ORIGIN = os.environ.get("MRMD_ALLOWED_ORIGIN", "*").strip() or "*"
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
MODEL_OVERRIDE = os.environ.get("NOTEMAX_WHISPER_MODEL", "").strip()
MAX_UPLOAD_MB = int(os.environ.get("MRMD_MAX_UPLOAD_MB", "4096"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def pick_model_for_vram():
    """Auto-select a Whisper model that fits the GPU. An explicit env override wins.

    Implements the "use a smaller model if the GPU doesn't meet spec" requirement:
    large-v3 wants plenty of VRAM (it shares the GPU with pyannote), so on smaller
    cards we step down. Returns (model_size, vram_gb_or_None)."""
    if MODEL_OVERRIDE:
        return MODEL_OVERRIDE, None
    try:
        import torch
        if not torch.cuda.is_available():
            return "large-v3", None     # require_gpu() will raise a clear error later
        gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    except Exception:
        return "large-v3", None
    if gb >= 12:
        model = "large-v3"
    elif gb >= 8:
        model = "medium"
    elif gb >= 5:
        model = "small"
    elif gb >= 3:
        model = "base"
    else:
        model = "tiny"
    return model, round(gb, 1)


def _authed(req) -> bool:
    """Constant-time check of the shared worker token. No token configured = refuse
    (we will not run an open, unauthenticated transcription endpoint)."""
    if not WORKER_TOKEN:
        return False
    sent = (req.headers.get("X-Worker-Token")
            or req.headers.get("Authorization", "").replace("Bearer ", "", 1)).strip()
    return bool(sent) and hmac.compare_digest(sent, WORKER_TOKEN)


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, X-Worker-Token, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    resp.headers["Vary"] = "Origin"
    return resp


@app.route("/health")
def health():
    model, gb = pick_model_for_vram()
    gpu, name = False, None
    try:
        import torch
        gpu = torch.cuda.is_available()
        if gpu:
            name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return jsonify({"ok": True, "tool": "mrmd-worker", "gpu": gpu, "gpu_name": name,
                    "vram_gb": gb, "model": model, "diarization": bool(HF_TOKEN),
                    "token_required": bool(WORKER_TOKEN)})


@app.route("/transcribe", methods=["POST", "OPTIONS"])
def transcribe_route():
    if request.method == "OPTIONS":          # CORS preflight
        return ("", 204)
    if not _authed(request):
        return jsonify({"error": "unauthorized — missing or invalid worker token"}), 401

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no audio file uploaded (form field 'file')"}), 400
    diarize = request.form.get("diarize", "1") != "0"
    try:
        num_speakers = int(request.form["num_speakers"]) if request.form.get("num_speakers") else None
    except (ValueError, KeyError):
        num_speakers = None

    model, gb = pick_model_for_vram()
    logs = []

    def log(m):
        logs.append(str(m))

    ext = os.path.splitext(f.filename)[1] or ".bin"
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        f.save(tmp)                          # audio bytes land on THIS machine only
        log(f"transcribing '{f.filename}' with Whisper {model} "
            f"(diarize={bool(HF_TOKEN) and diarize})")
        transcript = T.transcribe_and_diarize(
            tmp, HF_TOKEN, model_size=model,
            diarize_audio=bool(HF_TOKEN) and diarize, num_speakers=num_speakers, log=log)
        return jsonify({"transcript": transcript, "model": model, "vram_gb": gb,
                        "filename": f.filename, "log": logs})
    except Exception as e:                    # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}",
                        "traceback": traceback.format_exc(), "log": logs}), 500
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    m, vram = pick_model_for_vram()
    print(f"Monkey Read Monkey Do — GPU worker on http://0.0.0.0:{PORT}")
    print(f"  whisper model:  {m}" + (f"  (auto-picked for {vram} GB VRAM)" if vram else "  (forced)"))
    print(f"  diarization:    {'ON' if HF_TOKEN else 'OFF (no HF_TOKEN)'}")
    print(f"  token required: {'yes' if WORKER_TOKEN else 'NO — set MRMD_WORKER_TOKEN before exposing!'}")
    print(f"  allowed origin: {ALLOWED_ORIGIN}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
