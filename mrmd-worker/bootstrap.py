"""
Monkey Read Monkey Do — self-provisioning local worker (the downloadable .exe).

WHAT THIS IS
A small (~10 MB) launcher the user downloads from notes.centralindustrial.ai and
double-clicks. On the FIRST run it sets itself up — with no Python, no installer, no
clicking — by recreating the exact GPU stack we proved works on the RTX 5070:

  1. fetch `uv` (a single 15 MB binary)               -> APP_DIR/uv.exe
  2. uv venv (uv downloads CPython 3.12 if needed)     -> APP_DIR/venv
  3. uv pip install the PINNED faster-whisper + CUDA   -> into that venv
     wheels (cuBLAS/cuDNN — the bits that make the GPU work)
  4. fetch a static ffmpeg/ffprobe                     -> APP_DIR/ffmpeg
  5. download the right Whisper model for this GPU      -> APP_DIR/hf-cache
  6. launch the local worker on 127.0.0.1:5007

Every later run skips 1-5 (they're cached) and goes straight to 6. The hosted Read
Monkey Do page already polls http://127.0.0.1:5007/health and uploads audio there, so
once this is running the site "just works" — audio is transcribed on THIS machine's
GPU and never leaves it.

FIRST-RUN DOWNLOAD is large (~4-5 GB: CUDA libs + the Whisper model) and needs that
much free disk. It's a one-time cost; it's simply what local GPU Whisper requires.

Built by build_setup.bat into ReadMonkeyDoWorker.exe. Pure stdlib so the launcher
itself stays tiny and dependency-free — all the heavy ML lives in the provisioned venv.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

APP_NAME = "ReadMonkeyDo"
HOSTED_URL = "https://notes.centralindustrial.ai"
PORT = "5007"

# Where everything we provision lives (survives between runs).
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
VENV_DIR = os.path.join(APP_DIR, "venv")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
UV_EXE = os.path.join(APP_DIR, "uv.exe")
FFMPEG_DIR = os.path.join(APP_DIR, "ffmpeg")
HF_CACHE = os.path.join(APP_DIR, "hf-cache")
SRC_DIR = os.path.join(APP_DIR, "app")          # worker_server.py + transcribe.py live here
MODEL_FILE = os.path.join(APP_DIR, "model.txt")  # remembers the chosen Whisper model
VALID_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")

# Download sources.
UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# The PINNED stack — these exact versions are what transcribe on the RTX 5070 (Blackwell
# needs recent CUDA 12.9 + cuDNN 9 + a CTranslate2 build with sm_120 kernels). Do not
# loosen these without re-verifying on the target GPU. Lite = faster-whisper only
# (no torch, no pyannote -> no speaker labels), which is why this stays a few-GB install.
REQUIREMENTS = """\
faster-whisper==1.2.1
ctranslate2==4.8.0
nvidia-cublas-cu12==12.9.2.10
nvidia-cudnn-cu12==9.23.2.1
nvidia-cuda-nvrtc-cu12==12.9.86
onnxruntime==1.27.0
av==17.1.0
numpy==2.5.0
tokenizers==0.23.1
huggingface-hub==1.21.0
soundfile==0.14.0
flask==3.1.3
python-dotenv==1.2.2
"""

# Tiny entry point we drop next to worker_server.py so it binds to 127.0.0.1 (local only),
# runs in Lite mode, and answers the hosted page's origin — same as the old tray app did,
# but without needing pystray/Pillow in the venv.
RUN_WORKER = """\
import os
os.environ.setdefault("MRMD_ALLOWED_ORIGIN", "https://notes.centralindustrial.ai")
os.environ.setdefault("PORT", "5007")
os.environ.setdefault("MRMD_LITE", "1")
import worker_server as W
print("Read Monkey Do worker ready on http://127.0.0.1:" + os.environ["PORT"], flush=True)
W.app.run(host="127.0.0.1", port=int(os.environ["PORT"]), threaded=True)
"""


# --------------------------------------------------------------------------- #
def log(msg=""):
    print(msg, flush=True)


def _payload_base():
    """Dir holding the bundled worker_server.py / transcribe.py payload."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def download(url, dest, label):
    log(f"  downloading {label} ...")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "ReadMonkeyDo-setup"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f"\r    {done/1e6:6.0f} / {total/1e6:.0f} MB  ({pct}%)",
                      end="", flush=True)
    if total:
        print()
    os.replace(tmp, dest)


def _extract_named(zip_path, names, dest_dir):
    """Extract just the members whose basename is in `names`, flat into dest_dir."""
    os.makedirs(dest_dir, exist_ok=True)
    got = {}
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            base = os.path.basename(member)
            if base in names and base not in got:
                with z.open(member) as src, open(os.path.join(dest_dir, base), "wb") as out:
                    shutil.copyfileobj(src, out)
                got[base] = True
    return got


# --------------------------------------------------------------------------- #
def ensure_uv():
    if os.path.isfile(UV_EXE):
        return
    log("[1/5] Fetching uv (Python/dependency manager)...")
    zp = os.path.join(APP_DIR, "uv.zip")
    download(UV_URL, zp, "uv")
    _extract_named(zp, {"uv.exe"}, APP_DIR)
    os.remove(zp)
    if not os.path.isfile(UV_EXE):
        raise RuntimeError("uv.exe not found in the downloaded archive")


def ensure_venv():
    if os.path.isfile(VENV_PY):
        return
    log("[2/5] Creating the Python environment (downloads CPython 3.12 if needed)...")
    subprocess.run([UV_EXE, "venv", VENV_DIR, "--python", "3.12"], check=True)
    if not os.path.isfile(VENV_PY):
        raise RuntimeError("venv python not created")


def ensure_deps():
    req_path = os.path.join(APP_DIR, "requirements.txt")
    marker = os.path.join(APP_DIR, ".deps-ok")
    # Re-install only when the pinned set changes (e.g. a new exe version ships).
    if os.path.isfile(marker) and os.path.isfile(req_path):
        with open(req_path, "r", encoding="utf-8") as f:
            if f.read() == REQUIREMENTS:
                return
    with open(req_path, "w", encoding="utf-8") as f:
        f.write(REQUIREMENTS)
    log("[3/5] Installing faster-whisper + CUDA libraries (~2 GB, one time)...")
    subprocess.run([UV_EXE, "pip", "install", "--python", VENV_PY,
                    "-r", req_path], check=True)
    open(marker, "w").close()


def ensure_ffmpeg():
    ffmpeg = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if os.path.isfile(ffmpeg):
        return
    log("[4/5] Fetching ffmpeg (audio/video decoding)...")
    zp = os.path.join(APP_DIR, "ffmpeg.zip")
    download(FFMPEG_URL, zp, "ffmpeg")
    got = _extract_named(zp, {"ffmpeg.exe", "ffprobe.exe"}, FFMPEG_DIR)
    os.remove(zp)
    if "ffmpeg.exe" not in got:
        raise RuntimeError("ffmpeg.exe not found in the downloaded archive")


def install_app_files():
    """Drop worker_server.py, transcribe.py and the runner into APP_DIR\\app, mirroring
    the repo layout so worker_server's sibling import of `transcribe` resolves."""
    base = _payload_base()
    pairs = [
        (os.path.join(base, "payload", "mrmd-worker", "worker_server.py"),
         os.path.join(SRC_DIR, "mrmd-worker", "worker_server.py")),
        (os.path.join(base, "payload", "monkey-read-monkey-do", "transcribe.py"),
         os.path.join(SRC_DIR, "monkey-read-monkey-do", "transcribe.py")),
    ]
    for src, dst in pairs:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    runner = os.path.join(SRC_DIR, "mrmd-worker", "run_worker.py")
    with open(runner, "w", encoding="utf-8") as f:
        f.write(RUN_WORKER)
    return runner


def _pick_model():
    """Same VRAM->model rule the worker uses, so we pre-fetch what it will load."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        gb = int(out) / 1024.0
    except Exception:
        return "large-v3"
    if gb >= 12:
        return "large-v3"
    if gb >= 8:
        return "medium"
    if gb >= 5:
        return "small"
    if gb >= 3:
        return "base"
    return "tiny"


def _print_help():
    log("Read Monkey Do worker — local GPU transcription helper")
    log("")
    log("Usage:  ReadMonkeyDoWorker.exe [--model <name>]")
    log("")
    log("  --model auto      pick automatically from this GPU's VRAM (default)")
    log("  --model <name>    force a Whisper model; one of:")
    log("                    " + ", ".join(VALID_MODELS))
    log("  --help            show this and exit")
    log("")
    log("Bigger = more accurate but more VRAM/time: large-v3 is best, tiny/base fastest.")
    log("Your choice is remembered for next time. Example:")
    log("  ReadMonkeyDoWorker.exe --model large-v3")


def resolve_model_selection():
    """Decide which Whisper model to use: CLI --model > saved model.txt > 'auto'.
    An explicit choice is persisted so later double-clicks reuse it. Returns
    (selection, concrete_model) where selection is 'auto' or a model name."""
    argv = sys.argv[1:]
    sel = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h", "/?"):
            _print_help()
            raise SystemExit(0)
        if a == "--model":
            sel = (argv[i + 1] if i + 1 < len(argv) else "").strip().lower()
            i += 2
            continue
        if a.startswith("--model="):
            sel = a.split("=", 1)[1].strip().lower()
            i += 1
            continue
        i += 1
    if sel is not None:                          # explicit choice on the command line
        if sel != "auto" and sel not in VALID_MODELS:
            log(f"!! Unknown model '{sel}'. Valid: auto, " + ", ".join(VALID_MODELS))
            raise SystemExit(2)
        with open(MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(sel)
    else:                                        # no flag → reuse the remembered choice
        sel = "auto"
        try:
            with open(MODEL_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip().lower()
            if saved == "auto" or saved in VALID_MODELS:
                sel = saved
        except OSError:
            pass
    return sel, (_pick_model() if sel == "auto" else sel)


def ensure_model(env, model):
    marker = os.path.join(APP_DIR, f".model-{model}-ok")
    if os.path.isfile(marker):
        return model
    log(f"[5/5] Downloading the Whisper '{model}' model for this GPU (one time)...")
    code = ("from faster_whisper.utils import download_model; "
            f"download_model('{model}')")
    subprocess.run([VENV_PY, "-c", code], check=True, env=env)
    open(marker, "w").close()
    return model


def worker_env(model):
    env = dict(os.environ)
    env["PATH"] = FFMPEG_DIR + os.pathsep + env.get("PATH", "")
    env["HF_HOME"] = HF_CACHE
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["MRMD_LITE"] = "1"
    env["PORT"] = PORT
    env["MRMD_ALLOWED_ORIGIN"] = HOSTED_URL
    env["NOTEMAX_WHISPER_MODEL"] = model    # load exactly the model we provisioned
    return env


def main():
    os.makedirs(APP_DIR, exist_ok=True)
    sel, model = resolve_model_selection()       # CLI --model / saved choice / auto
    log("=" * 60)
    log(" Monkey Read Monkey Do — local transcription worker")
    log("=" * 60)
    log(f" Setup folder: {APP_DIR}")
    log(f" Whisper model: {model}" + ("  (auto-picked for this GPU)" if sel == "auto"
                                      else "  (forced via --model)"))
    log("")

    env = worker_env(model)
    try:
        ensure_uv()
        ensure_venv()
        ensure_deps()
        ensure_ffmpeg()
        runner = install_app_files()
        ensure_model(env, model)
    except subprocess.CalledProcessError as e:
        log("")
        log(f"!! Setup step failed (exit {e.returncode}). Most likely causes:")
        log("   - not enough free disk space (first run needs ~5 GB), or")
        log("   - no internet connection.")
        log("   Free up space / reconnect and run this again — it resumes where it left off.")
        input("\nPress Enter to close.")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"\n!! Setup failed: {type(e).__name__}: {e}")
        input("\nPress Enter to close.")
        return 1

    log("")
    log(f" Ready. Whisper model: {model}.  Open {HOSTED_URL} and transcribe —")
    log(" audio stays on this machine. Keep this window open while you work.")
    log(" Change the model:  ReadMonkeyDoWorker.exe --model <name>   (--help for options)")
    log("-" * 60)

    # Open the site once, then run the worker in the foreground (closing this window
    # stops the worker).
    try:
        import webbrowser
        webbrowser.open(HOSTED_URL)
    except Exception:
        pass
    time.sleep(0.5)
    return subprocess.call([VENV_PY, runner], cwd=os.path.dirname(runner), env=env)


if __name__ == "__main__":
    sys.exit(main())
