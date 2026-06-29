"""
Monkey Read Monkey Do — local audio transcription & DD notes engine.

Run:  .venv/Scripts/python.exe app.py   (then open http://127.0.0.1:5005)

Modes:
  1  transcribe only          (media -> diarized transcript)
  2  transcribe + summarize    (media -> transcript -> notes + .docx)
  3  summary from transcript   (text file -> notes + .docx)
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, send_from_directory

import transcribe as T
import notes as N

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# --- privacy hardening -----------------------------------------------------
# Always disable HuggingFace usage telemetry.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# NOTEMAX_OFFLINE=1 forces the local audio stage fully offline: HuggingFace will
# only use already-cached model weights and make NO network calls of any kind
# while transcription/diarization run. (Requires the models to be cached once.)
OFFLINE = os.environ.get("NOTEMAX_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")
if OFFLINE:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")     # transient local media (audio never leaves)
WORK = os.path.join(BASE, "work")           # transcripts, notes, docx
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(WORK, exist_ok=True)

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WHISPER_MODEL = os.environ.get("NOTEMAX_WHISPER_MODEL", "large-v3")

TEXT_EXTS = {".txt", ".md", ".vtt", ".srt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__, static_folder=None)
JOBS = {}   # job_id -> dict(status, progress[], result, error, control, ...)


class StopRequested(Exception):
    """Raised at a checkpoint when the user has pressed Stop."""


class JobControl:
    """Cooperative pause/stop for a running job.

    Workers call wait_if_paused() / check() at safe checkpoints; the HTTP control
    endpoint flips the state. 'stop' is sticky and wins over 'pause'.
    """
    def __init__(self):
        self.state = "run"                     # run | pause | stop
        self._cond = threading.Condition()

    def set_state(self, s):
        with self._cond:
            if self.state == "stop":           # stop is terminal
                return
            self.state = s
            self._cond.notify_all()

    @property
    def stopped(self):
        return self.state == "stop"

    def wait_if_paused(self):
        with self._cond:
            while self.state == "pause":
                self._cond.wait(timeout=0.5)

    def check(self):
        """Block while paused; raise StopRequested if stopped."""
        self.wait_if_paused()
        if self.state == "stop":
            raise StopRequested()


# --------------------------------------------------------------------------- #
def _safe(name: str) -> str:
    keep = "-_.() "
    base = os.path.basename(name)
    return "".join(c for c in base if c.isalnum() or c in keep).strip() or "file"


def _slug(name: str) -> str:
    return _safe(os.path.splitext(name)[0]).replace(" ", "_")[:60] or "notes"


def _read_text(path: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def desktop_dir() -> str:
    """The user's Desktop, accounting for OneDrive redirection."""
    home = os.path.expanduser("~")
    for c in (os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop")):
        if os.path.isdir(c):
            return c
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, check=True).stdout.strip()
        if out and os.path.isdir(out):
            return out
    except Exception:
        pass
    return os.path.join(home, "Desktop")


def _unique_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists(f"{path} ({i})"):
        i += 1
    return f"{path} ({i})"


def run_job(job_id, mode, media_files, text_files, image_files, job_dir, diarize=True):
    job = JOBS[job_id]
    control = job["control"]

    def log(msg):
        job["progress"].append(msg)

    def set_stage(key, frac=None, state=None, label=None):
        for st in job["stages"]:
            if st["key"] != key:
                continue
            if label:
                st["label"] = label
            if frac is None:
                if state:
                    st["state"] = state
                if state == "active":
                    st["pct"] = None            # indeterminate
            else:
                p = max(0.0, min(1.0, frac))
                st["pct"] = round(p * 100)
                st["state"] = "done" if p >= 0.999 else (state or "active")
            break

    # Build the stage list for this mode.
    stages = []
    if mode in ("1", "2"):
        stages.append({"key": "transcribe", "label": "Transcribing audio",
                       "pct": None, "state": "pending"})
        stages.append({"key": "diarize", "label": "Identifying speakers",
                       "pct": None, "state": "pending" if (HF_TOKEN and diarize) else "skipped"})
    if mode in ("2", "3"):
        stages.append({"key": "notes", "label": "Writing notes",
                       "pct": None, "state": "pending"})
        stages.append({"key": "review", "label": "Editorial review",
                       "pct": None, "state": "pending"})
        stages.append({"key": "docx", "label": "Building Word document",
                       "pct": None, "state": "pending"})
    job["stages"] = stages

    image_map = {i + 1: p for i, p in enumerate(image_files)}
    job["images"] = image_map      # index -> local path (served via /api/image)
    image_urls = {str(n): f"/api/image/{job_id}/{n}" for n in image_map}
    title = f"Meeting Notes — {datetime.now():%Y-%m-%d}"

    # Best-so-far artifacts, so Stop can persist whatever is complete.
    art = {"transcript": None, "notes_md": None, "changelog": ""}

    def write_notes_files(notes_md, changelog=""):
        with open(os.path.join(job_dir, "notes.md"), "w", encoding="utf-8") as f:
            f.write(notes_md)
        N.markdown_to_docx(notes_md, os.path.join(job_dir, "notes.docx"),
                           title=title, image_map=image_map)
        if changelog:
            with open(os.path.join(job_dir, "editor_changelog.txt"), "w",
                      encoding="utf-8") as f:
                f.write(changelog)

    def finalize_stop():
        saved = []
        if art["transcript"] is not None:
            with open(os.path.join(job_dir, "transcript.txt"), "w", encoding="utf-8") as f:
                f.write(art["transcript"])
            saved.append("transcript.txt")
        if art["notes_md"]:
            write_notes_files(art["notes_md"], art["changelog"])
            saved.extend(["notes.md", "notes.docx"])
        for st in job["stages"]:               # show where it was halted
            if st["state"] == "active":
                st["state"] = "stopped"
        job["result"] = {
            "transcript": art["transcript"] or "",
            "notes_md": art["notes_md"],
            "docx": os.path.basename(job_dir) if art["notes_md"] else None,
            "images": image_urls,
            "changelog": art["changelog"],
            "partial": True,
        }
        job["status"] = "stopped"
        rel = os.path.relpath(job_dir, BASE)
        log(f"⏹ Stopped by user. Saved partial work to {rel}\\: "
            + (", ".join(saved) if saved else "(nothing completed yet)"))

    try:
        transcript_parts = []

        # ---- Step 1: transcription (modes 1 & 2) ----
        if mode in ("1", "2"):
            if not HF_TOKEN:
                log("WARNING: no HF_TOKEN set — transcribing WITHOUT speaker diarization.")
            elif not diarize:
                log("Diarization turned OFF for this run — transcribing without speaker labels.")
            durations = [T.probe_duration(p) for p in media_files]
            total_dur = sum(d for d in durations if d) or 0.0
            done_dur = 0.0
            n = len(media_files)
            for idx, path in enumerate(media_files):
                name = os.path.basename(path)
                this_dur = durations[idx] or 0.0
                base_done = done_dur
                tlabel = f"Transcribing audio ({idx + 1}/{n})" if n > 1 else "Transcribing audio"

                def on_stage(key, frac, _d=this_dur, _base=base_done):
                    if key == "transcribe":
                        if frac is None:
                            set_stage("transcribe", None, "active", label=tlabel)
                        elif total_dur > 0 and _d > 0:
                            overall = (_base + min(frac, 1.0) * _d) / total_dur
                            set_stage("transcribe", overall, "active", label=tlabel)
                        else:
                            set_stage("transcribe", frac, "active", label=tlabel)
                    elif key == "diarize":
                        set_stage("diarize", frac, "active")

                log(f"Transcribing {name} (Whisper {WHISPER_MODEL}, local)...")
                text = T.transcribe_and_diarize(
                    path, HF_TOKEN, model_size=WHISPER_MODEL,
                    diarize_audio=bool(HF_TOKEN) and diarize, log=log, on_stage=on_stage,
                    control=control)
                done_dur += this_dur
                header = f"=== {name} ===\n\n" if len(media_files) > 1 else ""
                transcript_parts.append(header + text)
                # save each transcript locally
                tpath = os.path.join(job_dir, f"transcript_{_slug(name)}.txt")
                with open(tpath, "w", encoding="utf-8") as f:
                    f.write(text)
                log(f"  saved transcript -> {os.path.relpath(tpath, BASE)}")
                art["transcript"] = "\n\n".join(transcript_parts).strip()
                if control.stopped:
                    raise StopRequested()
            set_stage("transcribe", 1.0, "done")
            if HF_TOKEN:
                set_stage("diarize", 1.0, "done")

        # ---- text-file transcripts (mode 3, or supplied alongside) ----
        for path in text_files:
            transcript_parts.append(_read_text(path))

        transcript = "\n\n".join(p for p in transcript_parts if p.strip()).strip()
        art["transcript"] = transcript

        if not transcript:
            raise RuntimeError("No transcript was produced (no media or text input?).")

        job["transcript"] = transcript
        control.check()

        # ---- Step 1 output for mode 1 ----
        if mode == "1":
            combo = os.path.join(job_dir, "transcript.txt")
            with open(combo, "w", encoding="utf-8") as f:
                f.write(transcript)
            job["result"] = {"transcript": transcript, "notes_md": None,
                             "docx": None}
            job["status"] = "done"
            log("Done — transcription complete.")
            return

        # ---- Step 2: notes (modes 2 & 3) ----
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set — cannot generate notes. "
                               "Add it to .env and restart.")
        if image_files:
            log(f"Including {len(image_files)} visual exhibit(s) in the notes...")
        log("Generating draft notes from transcript (style guide applied)...")
        set_stage("notes", None, "active")
        draft_md = N.generate_notes(
            transcript, ANTHROPIC_API_KEY,
            image_paths=image_files or None, log=log, control=control)
        draft_md = N.normalize_exhibits(draft_md, len(image_files))
        art["notes_md"] = draft_md             # best-so-far = the draft
        if control.stopped:
            raise StopRequested()
        set_stage("notes", 1.0, "done")

        # ---- Step 3: editorial review (second pass) ----
        log("Editorial review pass (error-checking + style refinement)...")
        set_stage("review", None, "active")
        final_md, changelog = N.review_notes(
            draft_md, transcript, ANTHROPIC_API_KEY,
            image_paths=image_files or None, log=log, control=control)
        if final_md:
            final_md = N.normalize_exhibits(final_md, len(image_files))
            art["notes_md"] = final_md         # best-so-far = the reviewed final
            art["changelog"] = changelog
        if control.stopped:
            raise StopRequested()
        set_stage("review", 1.0, "done")
        if changelog:
            log("  editor changelog:")
            for line in changelog.splitlines():
                if line.strip():
                    log("    " + line.strip())

        # ---- Step 4: Word document ----
        log("Rendering Word document...")
        set_stage("docx", None, "active")
        write_notes_files(final_md, changelog)
        set_stage("docx", 1.0, "done")
        log(f"  saved -> {os.path.relpath(os.path.join(job_dir, 'notes.docx'), BASE)}")

        job["result"] = {
            "transcript": transcript,
            "notes_md": final_md,
            "docx": os.path.basename(job_dir),   # used by /api/download
            "images": image_urls,
            "changelog": changelog,
        }
        job["status"] = "done"
        log("Done — reviewed notes ready.")

    except StopRequested:
        finalize_stop()
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["progress"].append("ERROR: " + job["error"])
        job["traceback"] = traceback.format_exc()
        for st in job.get("stages", []):       # flag whichever stage was running
            if st["state"] == "active":
                st["state"] = "error"


# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/mascot")
def mascot():
    # Serve whatever the user saved as mascot.* in the Monkey Read Monkey Do folder.
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        p = os.path.join(BASE, f"mascot.{ext}")
        if os.path.exists(p):
            return send_file(p)
    return ("", 404)


@app.route("/api/restart", methods=["POST"])
def restart():
    """Start a fresh server process and exit this one. In-memory jobs are cleared.

    Uses subprocess (list args) rather than os.execv because execv mangles paths
    containing spaces on Windows (e.g. the 'Monkey Read Monkey Do' folder).
    """
    import subprocess
    script = os.path.abspath(__file__)
    logpath = os.path.join(BASE, "server.log")

    def _do():
        time.sleep(0.4)                       # let the HTTP response flush first
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        out = open(logpath, "a", buffering=1)  # persist logs across restarts
        subprocess.Popen([sys.executable, script], cwd=BASE,
                         creationflags=flags, close_fds=True,
                         stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL)
        os._exit(0)                           # release port 5005 for the new process

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"restarting": True})


@app.route("/api/config")
def config():
    return jsonify({
        "diarization": bool(HF_TOKEN),
        "notes": bool(ANTHROPIC_API_KEY),
        "whisper_model": WHISPER_MODEL,
        "offline": OFFLINE,
    })


@app.route("/api/process", methods=["POST"])
def process():
    mode = request.form.get("mode", "2")
    if mode not in ("1", "2", "3"):
        return jsonify({"error": "invalid mode"}), 400
    diarize = request.form.get("diarize", "1") != "0"   # per-run toggle (UI pill)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files uploaded"}), 400

    job_id = uuid.uuid4().hex[:12]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = os.path.join(WORK, f"{stamp}_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    media_files, text_files, image_files = [], [], []
    for f in files:
        fname = _safe(f.filename)
        ext = os.path.splitext(fname)[1].lower()
        if T.is_media(fname):
            dest = os.path.join(UPLOADS, f"{job_id}_{fname}")
            f.save(dest)
            media_files.append(dest)
        elif ext in TEXT_EXTS:
            dest = os.path.join(job_dir, fname)
            f.save(dest)
            text_files.append(dest)
        elif ext in IMAGE_EXTS:
            dest = os.path.join(job_dir, fname)
            f.save(dest)
            image_files.append(dest)
        # silently ignore unknown types

    # Validate per mode.
    if mode in ("1", "2") and not media_files:
        return jsonify({"error": "selected mode needs an audio/video file"}), 400
    if mode == "3" and not text_files:
        return jsonify({"error": "mode 3 needs a transcript text file"}), 400

    # Friendly name for the Desktop output folder (derived from the first source file).
    first = None
    if media_files:
        first = os.path.basename(media_files[0]).split("_", 1)[-1]   # strip "<jobid>_"
    elif text_files:
        first = os.path.basename(text_files[0])
    save_name = f"{datetime.now():%Y-%m-%d} - {_slug(first)}" if first else \
        f"{stamp}_{job_id}"

    JOBS[job_id] = {"status": "running", "progress": [], "result": None,
                    "error": None, "dir": job_dir, "stages": [],
                    "control": JobControl(), "save_name": save_name}
    threading.Thread(
        target=run_job,
        args=(job_id, mode, media_files, text_files, image_files, job_dir, diarize),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({
        "status": job["status"],
        "progress": job["progress"],
        "stages": job.get("stages", []),
        "result": job["result"],
        "error": job["error"],
    })


@app.route("/api/control/<job_id>", methods=["POST"])
def control(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    ctl = job.get("control")
    if not ctl:
        return jsonify({"error": "no control"}), 400
    action = (request.get_json(silent=True) or {}).get("action") \
        or request.form.get("action")
    if job["status"] not in ("running", "paused"):
        return jsonify({"status": job["status"], "note": "job not running"})
    if action == "pause":
        ctl.set_state("pause")
        if job["status"] == "running":
            job["status"] = "paused"
            job["progress"].append("⏸ Paused.")
    elif action == "resume":
        ctl.set_state("run")
        if job["status"] == "paused":
            job["status"] = "running"
            job["progress"].append("▶ Resumed.")
    elif action == "stop":
        ctl.set_state("stop")               # worker persists partial work, then idles
        job["progress"].append("⏹ Stop requested — saving partial work...")
    else:
        return jsonify({"error": "invalid action"}), 400
    return jsonify({"status": job["status"]})


# Editable prompt files exposed in Preferences (whitelist — no arbitrary paths).
PROMPT_FILES = {
    "style":  {"label": "Notes style guide  ·  1st pass (draft notes)",
               "file": "transcipt and summarize notes.md"},
    "editor": {"label": "Editorial review  ·  2nd pass",
               "file": "notes_editor_review.md"},
}


@app.route("/api/prefs")
def prefs_list():
    return jsonify([{"key": k, "label": v["label"]} for k, v in PROMPT_FILES.items()])


@app.route("/api/prefs/<key>", methods=["GET"])
def prefs_get(key):
    meta = PROMPT_FILES.get(key)
    if not meta:
        return jsonify({"error": "unknown file"}), 404
    path = os.path.join(BASE, meta["file"])
    try:
        content = _read_text(path)
    except FileNotFoundError:
        content = ""
    return jsonify({"key": key, "label": meta["label"], "file": meta["file"],
                    "content": content})


@app.route("/api/prefs/<key>", methods=["POST"])
def prefs_save(key):
    meta = PROMPT_FILES.get(key)
    if not meta:
        return jsonify({"error": "unknown file"}), 404
    content = (request.get_json(silent=True) or {}).get("content")
    if content is None:
        return jsonify({"error": "no content"}), 400
    path = os.path.join(BASE, meta["file"])
    if os.path.exists(path):                    # keep a one-level backup
        try:
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return jsonify({"ok": True, "bytes": len(content.encode("utf-8")), "file": meta["file"]})


@app.route("/api/finish/<job_id>", methods=["POST"])
def finish(job_id):
    """Copy the job's deliverables to <Desktop>/Monkey Read Monkey Do Output/<name>/ and report."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    src = job.get("dir")
    if not src or not os.path.isdir(src):
        return jsonify({"saved": None, "files": []})
    files = [n for n in sorted(os.listdir(src))
             if os.path.isfile(os.path.join(src, n))]
    if not files:
        return jsonify({"saved": None, "files": []})

    outroot = os.path.join(desktop_dir(), "Monkey Read Monkey Do Output")
    os.makedirs(outroot, exist_ok=True)
    dest = _unique_dir(os.path.join(outroot, job.get("save_name") or os.path.basename(src)))
    os.makedirs(dest, exist_ok=True)
    saved = []
    for n in files:
        shutil.copy2(os.path.join(src, n), os.path.join(dest, n))
        saved.append(n)
    return jsonify({"saved": dest, "files": saved})


@app.route("/api/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "not ready"}), 404
    docx_path = os.path.join(job["dir"], "notes.docx")
    if not os.path.exists(docx_path):
        return jsonify({"error": "no docx"}), 404
    return send_file(docx_path, as_attachment=True, download_name="notes.docx")


@app.route("/api/image/<job_id>/<int:idx>")
def image(job_id, idx):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    path = (job.get("images") or {}).get(idx)
    if not path or not os.path.exists(path):
        return jsonify({"error": "no such image"}), 404
    return send_file(path)


if __name__ == "__main__":
    print("Monkey Read Monkey Do running at http://127.0.0.1:5005")
    print(f"  diarization: {'ON' if HF_TOKEN else 'OFF (no HF_TOKEN)'}")
    print(f"  notes LLM:   {'ON' if ANTHROPIC_API_KEY else 'OFF (no ANTHROPIC_API_KEY)'}")
    print(f"  offline mode: {'ON (audio stage fully offline)' if OFFLINE else 'off'}")
    app.run(host="127.0.0.1", port=5005, threaded=True)
