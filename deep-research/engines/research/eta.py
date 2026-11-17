"""
Stage countdowns for the progress view (2026-11-16, owner: "a small countdown timer for each
section of the job ... even if it's just kind of an estimate").

Two kinds of number:
  * EXACT where a stage is time-boxed: the pre-research pass has its own limit, and the
    gathering stages end at the run's gathering deadline (on the pausable research clock,
    so a Pause freezes the countdown).
  * ESTIMATED elsewhere, from the owner's own past runs: every finished run stores how long
    each stage and each part of the write-up took (in its history entry), and the estimate
    is the median of the last runs with the same model (Claude or Local AI). Report writing
    is scaled by page count. Defaults cover the first runs.

The worker calls mark() on every progress update (with the jobs lock held); the status
route calls view(); the finished run's summary() goes into its history entry.
"""
import re
import statistics
import time

# A synthesis progress message -> the part of the write-up it starts.
_SYNTH_PARTS = (("Extracting findings", "extract"), ("Writing the synthesized", "write"),
                ("Folding", "write"), ("Deepening", "deepen"), ("Checking every citation", "check"))
_EXTRACT_N = re.compile(r"from (\d+) page")

# Defaults before there is history (seconds; extract = per page).
_DEFAULTS = {
    "claude": {"stage1": 60, "connect": 0, "stage3": 30, "stage4": 60, "extract": 1.5,
               "write": 90, "check": 45, "report": 20, "synthesize": 240},
    "local":  {"stage1": 90, "connect": 115, "stage3": 45, "stage4": 60, "extract": 30,
               "write": 300, "check": 120, "report": 60, "synthesize": 1200},
}
_LEARN_FROM = 10          # most recent matching runs used for a median


def new(provider: str, depth: str, history=None) -> dict:
    """The timing record a job starts with (learned estimates frozen at the start)."""
    prov = "local" if provider == "local" else "claude"
    return {"provider": prov, "depth": depth or "", "marks": [], "stages": {}, "parts": {},
            "pages": 0, "cur": None, "est": learned(prov, history or []),
            "gather_end": None, "run_end": None, "deepen": False, "ody_end": None,
            "t0": time.time()}


def learned(provider: str, history: list) -> dict:
    """Median durations from the owner's recent runs on the same model; defaults fill gaps."""
    est = dict(_DEFAULTS[provider])
    runs = [h.get("timing") for h in history
            if isinstance(h, dict) and not h.get("focus") and not h.get("failed")
            and isinstance(h.get("timing"), dict) and h["timing"].get("provider") == provider]
    runs = runs[:_LEARN_FROM]
    if not runs:
        return est

    def med(vals):
        vals = [v for v in vals if isinstance(v, (int, float)) and v > 0]
        return statistics.median(vals) if vals else None

    for k in ("stage3", "report", "synthesize"):
        m = med([r.get("stages", {}).get(k) for r in runs])
        if m:
            est[k] = m
    for k in ("write", "check", "connect"):
        m = med([r.get("parts", {}).get(k) for r in runs])
        if m:
            est[k] = m
    # stage 1 without the local model's start-up (that depends on whether Strata was warm)
    m = med([(r.get("stages", {}).get("stage1") or 0) - (r.get("parts", {}).get("connect") or 0)
             for r in runs])
    if m:
        est["stage1"] = m
    m = med([(r.get("parts", {}).get("extract") or 0) / r["pages"]
             for r in runs if r.get("pages")])
    if m:
        est["extract"] = m
    return est


def mark(t: dict, stage: str, message: str, now=None):
    """Record a progress update. Closes the running stage / part when a new one starts."""
    if not isinstance(t, dict):
        return
    now = now or time.time()
    msg = message or ""
    part = None
    if stage == "synthesize":
        for pre, key in _SYNTH_PARTS:
            if msg.startswith(pre):
                part = key
                break
        if part == "extract":
            m = _EXTRACT_N.search(msg)
            if m:
                t["pages"] = t.get("pages", 0) + int(m.group(1))
    elif stage == "stage1" and msg.startswith("Connecting to the local model"):
        part = "connect"
    cur = t.get("cur")
    if cur and cur[0] == stage:
        if part == cur[1]:
            return                               # same stage, same part: nothing to close
        if part is None and stage == "synthesize":
            return                               # other write-up messages stay in their part
    _close(t, now)
    t["cur"] = [stage, part, now]


def _close(t, now):
    cur = t.get("cur")
    if not cur:
        return
    dur = max(0.0, now - cur[2])
    t["stages"][cur[0]] = t["stages"].get(cur[0], 0.0) + dur
    if cur[1]:
        t["parts"][cur[1]] = t["parts"].get(cur[1], 0.0) + dur


def summary(t: dict) -> dict:
    """What a finished run stores in its history entry (the current stage closed now)."""
    if not isinstance(t, dict):
        return {}
    _close(t, time.time())
    t["cur"] = None
    r = lambda d: {k: round(v, 1) for k, v in d.items()}  # noqa: E731
    return {"provider": t.get("provider"), "depth": t.get("depth"), "stages": r(t["stages"]),
            "parts": r(t["parts"]), "pages": t.get("pages", 0)}


def view(t: dict, stages: list, rc_now: float, paused: bool, strata_warm=None) -> dict:
    """For the page: seconds left in the current stage, estimates for the stages to come, how
    long finished stages took. rc_now = the research clock now (it stands still while paused)."""
    if not isinstance(t, dict):
        return {}
    now = time.time()
    est, prov = t.get("est") or {}, t.get("provider")
    cur = t.get("cur") or [None, None, now]
    stage, part, since = cur
    in_stage = now - since
    gather_left = (t["gather_end"] - rc_now) if t.get("gather_end") else None
    deepen_left = (max(0.0, t["run_end"] - 75 - rc_now)
                   if (t.get("deepen") and t.get("run_end")) else 0.0)
    pages = t.get("pages") or 0

    def synth_left(part_now, elapsed):
        ext = pages * est["extract"]
        order = ["extract", "write", "deepen", "check"]
        if part_now not in order:                     # just entered: everything ahead
            return ext + est["write"] + deepen_left + est["check"]
        left = 0.0
        for p in order[order.index(part_now):]:
            full = {"extract": ext, "write": est["write"], "deepen": deepen_left,
                    "check": est["check"]}[p]
            if p == part_now and p != "deepen":
                full = max(0.0, full - elapsed)
            left += full
        return left

    def stage_left(s, active):
        if s == "odysseus":
            if t.get("ody_end"):
                return max(0.0, t["ody_end"] - now) if active else 0.0
            return None
        if s == "stage1":
            base = est["stage1"]
            if prov == "local" and strata_warm is False:
                base += est["connect"]
            if active:
                if part == "connect":
                    return max(0.0, est["connect"] - in_stage) + est["stage1"]
                return max(0.0, est["stage1"] - in_stage)
            return base
        if s == "stage2":
            if gather_left is not None:
                return max(0.0, gather_left)
            return None
        if s == "stage3":
            if active:
                return max(0.0, est["stage3"] - in_stage)
            return est["stage3"]
        if s == "stage4":
            return max(0.0, est["stage4"] - in_stage) if active else 0.0
        if s == "synthesize":
            if active:
                return synth_left(part, in_stage)
            return est["synthesize"]
        if s == "report":
            return max(0.0, est["report"] - in_stage) if active else est["report"]
        return None

    out = {"cur": stage, "left": None, "est": {}, "took": {}, "skipped": [],
           "paused": bool(paused), "over": False}
    idx = stages.index(stage) if stage in stages else -1
    for i, s in enumerate(stages):
        if i < idx:
            took = t["stages"].get(s, 0.0)
            if took < 1.0:
                out["skipped"].append(s)
            else:
                out["took"][s] = round(took)
        elif i == idx:
            left = stage_left(s, True)
            out["left"] = None if left is None else round(left)
            if left is not None and left <= 0:
                out["over"] = True                    # the page shows "running long"
        else:
            if s == "odysseus" and prov == "local":
                out["skipped"].append(s)
                continue
            e = stage_left(s, False)
            if s == "stage2" and e is None:
                from .agent import DEPTH_BUDGETS, normalize_depth
                b = DEPTH_BUDGETS[normalize_depth(t.get("depth") or "standard")]
                e = max(0.0, b["seconds"] - b["reserve"] - est["stage1"])
            if s == "stage4":
                continue                              # rarely runs; never counted ahead
            if e is not None:
                out["est"][s] = round(e)
    total = (out["left"] or 0) + sum(out["est"].values())
    out["total_left"] = round(total)
    return out

