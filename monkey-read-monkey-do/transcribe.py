"""
Monkey Read Monkey Do — local transcription + diarization.

CRITICAL: all audio processing happens on this machine. No audio bytes are ever
sent over the network. Only the resulting TEXT transcript may later be sent to the
notes LLM (see notes.py).

Pipeline:
  1. ffmpeg -> 16kHz mono wav (handles audio AND video containers)
  2. faster-whisper large-v3 (Whisper "high" fidelity)  [fallback: openai-whisper]
  3. pyannote speaker-diarization-3.1 -> speaker turns
  4. assign each transcript segment to the max-overlap speaker
  5. emit a clean, timestamp-free, speaker-labeled transcript
"""

import os
import subprocess
import tempfile

# Audio + video extensions we will run through Whisper.
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".oga", ".aac", ".wma",
              ".opus", ".aiff", ".aif", ".amr"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
              ".mpeg", ".mpg", ".3gp"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

_WHISPER_MODEL = None      # cached faster-whisper model
_OPENAI_MODEL = None       # cached openai-whisper model (fallback)
_DIARIZER = None           # cached pyannote pipeline


def is_media(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in MEDIA_EXTS


def require_gpu(log=print):
    """Confirm a CUDA GPU is present and return its torch.device.

    Monkey Read Monkey Do is designed to run Whisper + diarization in GPU VRAM, never on
    system DDR. If no GPU is visible we say so loudly rather than silently
    grinding on CPU.
    """
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError(
            "No CUDA GPU visible to PyTorch — Monkey Read Monkey Do expects the audio models to "
            "run in GPU VRAM. Refusing to fall back to CPU/system memory.")
    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    log(f"  GPU: {name} ({total:.0f} GB VRAM)")
    return torch.device("cuda")


def _vram_used(log):
    """Report true GPU memory in use via nvidia-smi.

    (torch's own counters don't see CTranslate2/faster-whisper's CUDA allocator,
    so we ask the driver for the real, process-inclusive number.)
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
        used, total = (int(x) for x in out.split(","))
        log(f"  VRAM in use: {used/1024:.2f} GB / {total/1024:.0f} GB (GPU total)")
    except Exception:
        pass


def probe_duration(path: str) -> float:
    """Audio/video duration in seconds via ffprobe (0.0 if unknown)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def to_wav(src_path: str, log=print) -> str:
    """Decode any audio/video container to 16kHz mono wav using local ffmpeg."""
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    log(f"  ffmpeg: extracting 16kHz mono audio from {os.path.basename(src_path)}")
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ac", "1", "-ar", "16000", "-vn",
        "-loglevel", "error", wav_path,
    ]
    subprocess.run(cmd, check=True)
    return wav_path


# --------------------------------------------------------------------------- #
# Whisper transcription
# --------------------------------------------------------------------------- #
def _load_faster_whisper(model_size: str, log=print):
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        require_gpu(log)
        from faster_whisper import WhisperModel
        log(f"  loading faster-whisper '{model_size}' into GPU VRAM (float16)...")
        # device="cuda" + float16 keeps weights and compute in VRAM (CTranslate2).
        _WHISPER_MODEL = WhisperModel(model_size, device="cuda", compute_type="float16")
        _vram_used(log)
    return _WHISPER_MODEL


def _transcribe_faster(wav_path: str, model_size: str, language, log,
                       on_progress=None, control=None):
    model = _load_faster_whisper(model_size, log)
    segments, info = model.transcribe(
        wav_path,
        language=language,
        vad_filter=True,                       # drop long silences
        beam_size=5,                           # higher-fidelity decoding
        word_timestamps=True,                  # enables word-level speaker assignment
    )
    duration = getattr(info, "duration", 0) or 0
    out = []
    for seg in segments:                       # streams as decoding proceeds
        if control is not None:
            control.wait_if_paused()           # block here while paused
            if control.stopped:                # stop -> keep partial, exit early
                break
        text = seg.text.strip()
        if text:
            words = [{"start": w.start, "end": w.end, "word": w.word}
                     for w in (seg.words or []) if w.start is not None]
            out.append({"start": seg.start, "end": seg.end, "text": text, "words": words})
        if on_progress and duration:
            on_progress(min(seg.end / duration, 1.0))
    if on_progress and not (control is not None and control.stopped):
        on_progress(1.0)
    return out, info.language


def _transcribe_openai(wav_path: str, model_size: str, language, log, on_progress=None):
    """Pure-PyTorch fallback — guaranteed to run on Blackwell via cu128 torch."""
    global _OPENAI_MODEL
    import whisper
    if _OPENAI_MODEL is None:
        log(f"  loading openai-whisper '{model_size}' on GPU...")
        _OPENAI_MODEL = whisper.load_model(model_size, device="cuda")
    if on_progress:
        on_progress(None)                      # indeterminate (no per-segment hook here)
    result = _OPENAI_MODEL.transcribe(
        wav_path, language=language, verbose=False, fp16=True, word_timestamps=True,
    )
    if on_progress:
        on_progress(1.0)
    out = []
    for seg in result["segments"]:
        text = seg["text"].strip()
        if not text:
            continue
        words = [{"start": w["start"], "end": w["end"], "word": w["word"]}
                 for w in seg.get("words", []) if w.get("start") is not None]
        out.append({"start": seg["start"], "end": seg["end"], "text": text, "words": words})
    return out, result.get("language")


def transcribe(wav_path: str, model_size: str = "large-v3", language=None, log=print,
               on_progress=None, control=None):
    """Return (segments, detected_language). Tries faster-whisper, falls back."""
    try:
        log("  transcribing with faster-whisper...")
        return _transcribe_faster(wav_path, model_size, language, log, on_progress, control)
    except Exception as e:
        log(f"  faster-whisper failed ({type(e).__name__}: {e}); "
            f"falling back to openai-whisper.")
        return _transcribe_openai(wav_path, model_size, language, log, on_progress)


# --------------------------------------------------------------------------- #
# Diarization
# --------------------------------------------------------------------------- #
def _load_diarizer(hf_token: str, log=print):
    global _DIARIZER
    if _DIARIZER is None:
        device = require_gpu(log)
        from pyannote.audio import Pipeline
        log("  loading pyannote speaker-diarization-3.1 into GPU VRAM...")
        _DIARIZER = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", token=hf_token,
        )
        _DIARIZER.to(device)          # move ALL diarization models to VRAM
        _vram_used(log)
    return _DIARIZER


def diarize(wav_path: str, hf_token: str, num_speakers=None, log=print):
    """Return a list of (start, end, raw_speaker_label) turns."""
    import soundfile as sf
    import torch
    pipeline = _load_diarizer(hf_token, log)
    kwargs = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers
    # Preload the wav in-memory (avoids pyannote's torchcodec/ffmpeg-DLL path on Windows).
    data, sr = sf.read(wav_path, dtype="float32", always_2d=True)   # (time, channels)
    waveform = torch.from_numpy(data.T).contiguous()                # (channels, time)
    diar = pipeline({"waveform": waveform, "sample_rate": sr}, **kwargs)
    # pyannote 4.x returns a DiarizeOutput wrapper; <4 returns the Annotation directly.
    annotation = getattr(diar, "speaker_diarization", diar)
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append((turn.start, turn.end, speaker))
    return turns


def _overlap(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _speaker_at(start, end, turns):
    """Return the raw speaker label with the most time-overlap for [start, end]."""
    best_raw, best_ov = None, 0.0
    mid = (start + end) / 2.0
    for (t_start, t_end, raw) in turns:
        ov = _overlap(start, end, t_start, t_end)
        if ov > best_ov:
            best_ov, best_raw = ov, raw
    if best_raw is None:                       # no overlap: snap to nearest turn
        best_dist = None
        for (t_start, t_end, raw) in turns:
            dist = 0.0 if t_start <= mid <= t_end else min(abs(mid - t_start), abs(mid - t_end))
            if best_dist is None or dist < best_dist:
                best_dist, best_raw = dist, raw
    return best_raw


def assign_speakers(segments, turns):
    """Attach a speaker label to each transcript segment by max time-overlap.

    Returns segments with a 'speaker' key holding friendly labels ('Speaker 1' ...),
    numbered by order of first appearance.
    """
    label_map = {}        # raw pyannote label -> "Speaker N"
    next_n = 1

    def friendly(raw):
        nonlocal next_n
        if raw not in label_map:
            label_map[raw] = f"Speaker {next_n}"
            next_n += 1
        return label_map[raw]

    for seg in segments:
        best_raw, best_ov = None, 0.0
        for (t_start, t_end, raw) in turns:
            ov = _overlap(seg["start"], seg["end"], t_start, t_end)
            if ov > best_ov:
                best_ov, best_raw = ov, raw
        seg["speaker"] = friendly(best_raw) if best_raw is not None else None
    return segments, label_map


# --------------------------------------------------------------------------- #
# Transcript assembly
# --------------------------------------------------------------------------- #
def build_transcript_from_words(segments, turns):
    """Word-level speaker assignment -> clean speaker-grouped transcript.

    Far tighter speaker boundaries than segment-level when word timings exist.
    Returns (transcript_str, num_speakers).
    """
    label_map = {}
    next_n = 1

    def friendly(raw):
        nonlocal next_n
        if raw not in label_map:
            label_map[raw] = f"Speaker {next_n}"
            next_n += 1
        return label_map[raw]

    # Flatten to words; fall back to a segment as one "word" if it has no word timings.
    items = []
    for seg in segments:
        if seg.get("words"):
            for w in seg["words"]:
                items.append((w["start"], w["end"], w["word"]))
        else:
            items.append((seg["start"], seg["end"], seg["text"] + " "))

    lines, cur, buf = [], None, []

    def flush():
        if buf:
            lines.append(f"{cur}: {''.join(buf).strip()}")

    for (start, end, text) in items:
        raw = _speaker_at(start, end, turns)
        spk = friendly(raw) if raw is not None else "Unknown speaker"
        if spk != cur:
            flush()
            buf = []
            cur = spk
        buf.append(text)
    flush()
    return "\n\n".join(lines).strip(), len(label_map)


def build_transcript(segments, diarized: bool) -> str:
    """Render a clean, timestamp-free transcript.

    Diarized: group consecutive same-speaker segments under a 'Speaker N:' label.
    Single-presenter / no diarization: just flowing text.
    """
    if not diarized:
        return " ".join(s["text"] for s in segments).strip()

    lines, cur_speaker, buf = [], None, []

    def flush():
        if buf:
            label = cur_speaker or "Unknown speaker"
            lines.append(f"{label}: {' '.join(buf).strip()}")

    for seg in segments:
        spk = seg.get("speaker") or "Unknown speaker"
        if spk != cur_speaker:
            flush()
            buf = []
            cur_speaker = spk
        buf.append(seg["text"])
    flush()
    return "\n\n".join(lines).strip()


def transcribe_and_diarize(src_path, hf_token, model_size="large-v3",
                           diarize_audio=True, num_speakers=None, log=print,
                           on_stage=None, control=None):
    """Full local pipeline for one media file -> transcript string.

    on_stage(stage_key, frac): progress callback. stage_key is "transcribe" or
    "diarize"; frac is 0..1, or None for indeterminate (work started, no %).
    control: optional pause/stop controller (duck-typed: .wait_if_paused(), .stopped).
    """
    def stage(k, frac):
        if on_stage:
            on_stage(k, frac)

    stopped = lambda: control is not None and control.stopped

    wav_path = to_wav(src_path, log)
    try:
        stage("transcribe", None)              # active, indeterminate until 1st segment
        segments, lang = transcribe(
            wav_path, model_size=model_size, log=log,
            on_progress=lambda f: stage("transcribe", f), control=control)
        if not stopped():
            stage("transcribe", 1.0)
        if not segments:
            return "(No speech detected in this file.)"
        # On stop, skip diarization — emit the partial, un-diarized transcript now.
        if diarize_audio and hf_token and not stopped():
            try:
                stage("diarize", None)         # active, indeterminate (fast step)
                log("  diarizing speakers...")
                turns = diarize(wav_path, hf_token, num_speakers=num_speakers, log=log)
                stage("diarize", 1.0)
                if turns:
                    text, n = build_transcript_from_words(segments, turns)
                    log(f"  detected {n} speaker(s).")
                    if n > 1:
                        return text
                    # single speaker -> no labels needed
            except Exception as e:
                stage("diarize", 1.0)
                log(f"  diarization failed ({type(e).__name__}: {e}); "
                    f"emitting un-diarized transcript.")
        return build_transcript(segments, diarized=False)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
