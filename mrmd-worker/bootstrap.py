"""
Monkey Read Monkey Do — self-provisioning local worker (the downloadable .exe).

WHAT THIS IS
A small (~8 MB) launcher the user downloads from notes.centralindustrial.ai and
double-clicks. On the FIRST run it sets itself up — with no Python, no installer, no
clicking:

  1. fetch `uv` (a single 15 MB binary)               -> APP_DIR/uv.exe
  2. uv venv (uv downloads CPython 3.12 if needed)     -> APP_DIR/venv
  3. uv pip install the PINNED faster-whisper stack    -> into that venv
     (+ the CUDA cuBLAS/cuDNN wheels ONLY when an NVIDIA GPU is present)
  4. fetch a static ffmpeg/ffprobe                     -> APP_DIR/ffmpeg
  5. download the Whisper model (default: `small`)     -> APP_DIR/hf-cache
  6. launch the local worker on 127.0.0.1:5007

Every later run skips 1-5 (they're cached) and goes straight to 6. The hosted Read
Monkey Do page already polls http://127.0.0.1:5007/health and uploads audio there, so
once this is running the site "just works" — audio is transcribed on THIS machine and
never leaves it.

RUNS ANYWHERE — no GPU required. With an NVIDIA card Whisper runs in VRAM (float16);
without one it runs on the CPU with int8 weights, the lightest setting there is.
FIRST-RUN DOWNLOAD ≈ 1 GB (≈ 3 GB when the GPU libraries are installed), one time.

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
PORT = os.environ.get("MRMD_PORT", "").strip() or "5007"    # MRMD_PORT: tests / clashes

# Where everything we provision lives (survives between runs).
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
VENV_DIR = os.path.join(APP_DIR, "venv")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
UV_EXE = os.path.join(APP_DIR, "uv.exe")
FFMPEG_DIR = os.path.join(APP_DIR, "ffmpeg")
HF_CACHE = os.path.join(APP_DIR, "hf-cache")
SRC_DIR = os.path.join(APP_DIR, "app")          # worker_server.py + transcribe.py live here
MODEL_FILE = os.path.join(APP_DIR, "model.txt")  # remembers the chosen Whisper model
DEVICE_FILE = os.path.join(APP_DIR, "device.txt")  # present = user chose --cpu
VALID_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3")
# The default: small enough to download quickly and run on a CPU, accurate enough for
# meeting notes. Bigger models are opt-in via --model (remembered).
DEFAULT_MODEL = "small"
MODEL_MB = {"tiny": 75, "base": 145, "small": 465, "medium": 1500,
            "large-v2": 3000, "large-v3": 3000}

# Download sources.
UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# The PINNED stack. Lite = faster-whisper only (no torch, no pyannote -> no speaker
# labels). The CUDA wheels (cuBLAS/cuDNN, ~2 GB) are added ONLY when an NVIDIA GPU is
# present — a GPU-less PC gets the ~250 MB CPU stack and runs Whisper with int8 weights.
# These exact CUDA versions are what transcribe on Blackwell (RTX 50xx needs CUDA 12.9 +
# cuDNN 9 + a CTranslate2 build with sm_120 kernels); do not loosen them without
# re-verifying on the target GPU. Order matters: HEAD+CUDA+TAIL must reproduce the
# previous requirements text byte-for-byte so existing GPU installs don't re-install.
_REQ_HEAD = """\
faster-whisper==1.2.1
ctranslate2==4.8.0
"""
_REQ_CUDA = """\
nvidia-cublas-cu12==12.9.2.10
nvidia-cudnn-cu12==9.23.2.1
nvidia-cuda-nvrtc-cu12==12.9.86
"""
_REQ_TAIL = """\
onnxruntime==1.27.0
av==17.1.0
numpy==2.5.0
tokenizers==0.23.1
huggingface-hub==1.21.0
soundfile==0.14.0
flask==3.1.3
python-dotenv==1.2.2
"""


def requirements_text(use_cuda: bool) -> str:
    return _REQ_HEAD + (_REQ_CUDA if use_cuda else "") + _REQ_TAIL


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


def ensure_deps(use_cuda: bool):
    req_path = os.path.join(APP_DIR, "requirements.txt")
    marker = os.path.join(APP_DIR, ".deps-ok")
    wanted = requirements_text(use_cuda)
    # Re-install only when the pinned set changes (a new exe version, or the GPU/CPU
    # choice changed since last time).
    if os.path.isfile(marker) and os.path.isfile(req_path):
        with open(req_path, "r", encoding="utf-8") as f:
            if f.read() == wanted:
                return
    with open(req_path, "w", encoding="utf-8") as f:
        f.write(wanted)
    if use_cuda:
        log("[3/5] Installing faster-whisper + CUDA libraries (~2 GB, one time)...")
    else:
        log("[3/5] Installing faster-whisper (CPU build, ~250 MB, one time)...")
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


def _nvidia_gpu_name():
    """Name of the NVIDIA GPU via nvidia-smi, or None when there isn't one (or no driver)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        return out.strip() or None
    except Exception:
        return None


def _truthy(v):
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def force_cpu() -> bool:
    """--cpu (remembered) or MRMD_FORCE_CPU=1: skip the CUDA libraries and run Whisper on
    the CPU even when an NVIDIA GPU is present. --gpu forgets a remembered --cpu."""
    if _truthy(os.environ.get("MRMD_FORCE_CPU")):
        return True
    argv = sys.argv[1:]
    if "--cpu" in argv:
        with open(DEVICE_FILE, "w", encoding="utf-8") as f:
            f.write("cpu")
        return True
    if "--gpu" in argv:
        try:
            os.remove(DEVICE_FILE)
        except OSError:
            pass
        return False
    return os.path.isfile(DEVICE_FILE)


def _print_help():
    log("Read Monkey Do worker — local transcription helper (GPU optional)")
    log("")
    log("Usage:  ReadMonkeyDoWorker.exe [--model <name>] [--cpu | --gpu] [--no-browser]")
    log("")
    log(f"  --model <name>    Whisper model; default '{DEFAULT_MODEL}'. One of:")
    log("                    " + ", ".join(VALID_MODELS))
    log("  --cpu             run on the CPU even if an NVIDIA GPU is present (remembered)")
    log("  --gpu             forget a remembered --cpu")
    log("  --no-browser      don't open the site when the worker starts")
    log("  --help            show this and exit")
    log("")
    log("Bigger models are more accurate but slower and larger to download:")
    log("  " + ", ".join(f"{m} ≈{MODEL_MB[m]} MB" for m in VALID_MODELS))
    log("Your --model choice is remembered for next time. Example:")
    log("  ReadMonkeyDoWorker.exe --model large-v3")


def resolve_model_selection():
    """Decide which Whisper model to use: CLI --model > saved model.txt > default.
    An explicit choice is persisted so later double-clicks reuse it. Returns
    (selection, concrete_model) where selection is 'auto' (= the default) or a name."""
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
    return sel, (DEFAULT_MODEL if sel == "auto" else sel)


def ensure_model(env, model):
    marker = os.path.join(APP_DIR, f".model-{model}-ok")
    if os.path.isfile(marker):
        return model
    log(f"[5/5] Downloading the Whisper '{model}' model (~{MODEL_MB.get(model, '?')} MB, one time)...")
    code = ("from faster_whisper.utils import download_model; "
            f"download_model('{model}')")
    subprocess.run([VENV_PY, "-c", code], check=True, env=env)
    open(marker, "w").close()
    return model


def worker_env(model, use_cuda: bool):
    env = dict(os.environ)
    env["PATH"] = FFMPEG_DIR + os.pathsep + env.get("PATH", "")
    env["HF_HOME"] = HF_CACHE
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["MRMD_LITE"] = "1"
    env["PORT"] = PORT
    env["MRMD_ALLOWED_ORIGIN"] = HOSTED_URL
    env["NOTEMAX_WHISPER_MODEL"] = model    # load exactly the model we provisioned
    # 'auto' = GPU if CTranslate2 sees one (float16), else CPU int8; 'cpu' pins the CPU.
    env["NOTEMAX_DEVICE"] = "auto" if use_cuda else "cpu"
    env["MRMD_LOG"] = os.path.join(APP_DIR, "worker.log")   # debug log lives next to setup
    return env


def main():
    os.makedirs(APP_DIR, exist_ok=True)
    sel, model = resolve_model_selection()       # CLI --model / saved choice / default
    cpu_only = force_cpu()
    gpu = None if cpu_only else _nvidia_gpu_name()
    use_cuda = gpu is not None
    log("=" * 60)
    log(" Monkey Read Monkey Do — local transcription worker")
    log("=" * 60)
    log(f" Setup folder: {APP_DIR}")
    log(f" Whisper model: {model}" + ("  (default — light, runs on any PC)" if sel == "auto"
                                      else "  (chosen via --model)"))
    if use_cuda:
        log(f" Compute: GPU — {gpu} (float16)")
    elif cpu_only:
        log(" Compute: CPU, int8 (forced with --cpu / MRMD_FORCE_CPU)")
    else:
        log(" Compute: CPU, int8 (no NVIDIA GPU found — that's fine, just slower)")
    log("")

    env = worker_env(model, use_cuda)
    try:
        ensure_uv()
        ensure_venv()
        ensure_deps(use_cuda)
        ensure_ffmpeg()
        runner = install_app_files()
        ensure_model(env, model)
    except subprocess.CalledProcessError as e:
        log("")
        log(f"!! Setup step failed (exit {e.returncode}). Most likely causes:")
        log("   - not enough free disk space (first run needs ~1 GB; ~3 GB with GPU libraries), or")
        log("   - no internet connection.")
        log("   Free up space / reconnect and run this again — it resumes where it left off.")
        input("\nPress Enter to close.")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"\n!! Setup failed: {type(e).__name__}: {e}")
        input("\nPress Enter to close.")
        return 1

    log("")
    log(f" Ready. Whisper {model} on {'GPU' if use_cuda else 'CPU'}.  Open {HOSTED_URL} and transcribe —")
    log(" audio stays on this machine. Keep this window open while you work.")
    log(" Options:  ReadMonkeyDoWorker.exe --model <name> | --cpu | --gpu   (--help for details)")
    log(f" Live progress shows in THIS window; full log: {os.path.join(APP_DIR, 'worker.log')}")
    log("-" * 60)

    # Open the site once, then run the worker in the foreground (closing this window
    # stops the worker).
    if not ("--no-browser" in sys.argv[1:] or _truthy(os.environ.get("MRMD_NO_BROWSER"))):
        try:
            import webbrowser
            webbrowser.open(HOSTED_URL)
        except Exception:
            pass
    time.sleep(0.5)
    return subprocess.call([VENV_PY, runner], cwd=os.path.dirname(runner), env=env)


if __name__ == "__main__":
    sys.exit(main())
