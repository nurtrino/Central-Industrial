"""
Provider abstraction: route AI calls to Claude (Anthropic) OR the local model.

The Deep Research engine calls `client.messages.create(model=..., system=..., messages=...,
tools=..., max_tokens=...)` throughout and reads Anthropic-shaped responses (`resp.content`
= list of blocks with `.type`/`.text`/`.id`/`.name`/`.input`, plus `resp.stop_reason`).

LOCAL AI = Flash-Next on Strata (2026-11-16, owner: "make local AI use Flash-Next on Strata
instead"; it was whatever model LM Studio had loaded). Strata (~/strata, port 8182) runs
orcarouter Qwen3.8-Flash-Next-Uncensored Q5_K_M and speaks the Anthropic Messages API
natively, so `StrataClient` is the real Anthropic SDK pointed at it: tool use, stop_reason
and usage come back in Claude's shape with no translation. Every call goes to that one model
whatever role it is for (config/drt_models.json does not apply to local runs). Strata takes
one request at a time behind a FIFO; parallel lanes queue there.

House rules this follows (LOCAL LLMs\\STATE.md): exactly one local model loaded at a time,
so starting Strata first unloads any LM Studio chat model, the way `claude-local` does; and
Strata is closed by ~/bin/strata-watchdog.ps1 once nothing is using it. Deep Research tells
the watchdog it is using Strata through a heartbeat file in ~/.local-servers/strata-clients/
(refreshed every 15 s while a run holds the client or a call is in flight).
"""
import os
import subprocess
import sys
import threading
import time


class LocalLLMUnavailable(RuntimeError):
    """The local model (Strata) cannot be reached or started."""


_LOCAL_ALIASES = ("local", "strata", "local_ai", "localai", "lmstudio")


def is_local(provider) -> bool:
    return (provider or "claude").strip().lower() in _LOCAL_ALIASES


# ── Strata: where it is, how it is started ───────────────────────────────────
# 127.0.0.1, not "localhost" (Windows may try ::1 first).
STRATA_URL = (os.environ.get("DRT_STRATA_URL") or "http://127.0.0.1:8182").rstrip("/")
STRATA_MODEL = (os.environ.get("DRT_STRATA_MODEL")
                or "orcarouter-qwen3.8-flash-next-uncensored-q5_k_m")
STRATA_LABEL = "Flash-Next (Strata)"
_HOME = os.path.expanduser("~")
_SERVERS_PS1 = os.environ.get("DRT_STRATA_SERVERS") or os.path.join(_HOME, "bin", "local-servers.ps1")
_WATCHDOG_PS1 = os.path.join(_HOME, "bin", "strata-watchdog.ps1")
_LMS = os.path.join(_HOME, ".lmstudio", "bin", "lms.exe")
_STATE_DIR = os.path.join(_HOME, ".local-servers")
_HB_DIR = os.path.join(_STATE_DIR, "strata-clients")      # read by strata-watchdog.ps1
_START_LOG = os.path.join(_STATE_DIR, "deep-research-strata-start.log")
_START_TIMEOUT_S = 900          # local-servers.ps1 itself allows 900 s; a load is 1-3 min
_HB_EVERY_S = 15                # the watchdog counts a file touched in the last 60 s as a user
_MAX_TOKENS_FLOOR_LOCAL = 8192  # Flash-Next thinks first; the pipeline's small caps (16-400) would starve it
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _hosted() -> bool:
    # Same test as dr_server / browser.hosted_instance(): the hosted (Render) copy has AUTH_SECRET.
    return bool(os.environ.get("AUTH_SECRET", "").strip())


def strata_up(timeout=3) -> bool:
    """True when the Strata server answers on its port."""
    import requests
    try:
        return requests.get(f"{STRATA_URL}/v1/models", timeout=timeout).ok
    except Exception:  # noqa: BLE001
        return False


_START_LOCK = threading.Lock()
_STARTING = threading.Event()


def strata_status() -> dict:
    """For the page's Claude / Local AI switch: can a local run use Strata, and is it up."""
    running = strata_up()
    out = {"ok": True, "running": running, "starting": _STARTING.is_set() and not running,
           "model": STRATA_LABEL, "key": STRATA_MODEL, "url": STRATA_URL, "error": None}
    if not running:
        if _hosted():
            out.update(ok=False, error="Local AI runs only on the home PC, not on the hosted site.")
        elif not (sys.platform == "win32" and os.path.isfile(_SERVERS_PS1)):
            out.update(ok=False, error=f"Strata is not running and its starter was not found ({_SERVERS_PS1}).")
    return out


def _unload_lmstudio(log):
    """One local model at a time: unload every LM Studio chat model (embedding models stay),
    as claude-local does before it starts Strata."""
    if not os.path.isfile(_LMS):
        return
    import json as _json
    try:
        r = subprocess.run([_LMS, "ps", "--json"], capture_output=True, text=True, timeout=30,
                           creationflags=_NO_WINDOW)
        loaded = _json.loads(r.stdout or "[]")
    except Exception:  # noqa: BLE001  (LM Studio not running: nothing to unload)
        return
    for m in loaded if isinstance(loaded, list) else []:
        k = (m.get("identifier") or m.get("modelKey") or "") if isinstance(m, dict) else ""
        if k and "embed" not in k.lower():
            log(f"[local] unloading {k} from LM Studio (one local model at a time)")
            try:
                subprocess.run([_LMS, "unload", k], capture_output=True, timeout=60,
                               creationflags=_NO_WINDOW)
            except Exception:  # noqa: BLE001
                pass


def _start_watchdog():
    """~/bin/strata-watchdog.ps1 (single instance) closes Strata once no client is using it."""
    if not os.path.isfile(_WATCHDOG_PS1):
        return
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", _WATCHDOG_PS1],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW, close_fds=True)
    except Exception:  # noqa: BLE001
        pass


def ensure_strata(log=None):
    """Make sure Strata is serving. If it is down, unload LM Studio's model, start it with
    ~/bin/local-servers.ps1 strata (1-3 min; locks ~70 GB of RAM) and start the watchdog.
    A Strata someone else started is used as it is (and left to their watchdog)."""
    log = log or (lambda m: None)
    if strata_up():
        return
    with _START_LOCK:
        if strata_up():                       # another caller started it while we waited
            return
        st = strata_status()
        if not st["ok"]:
            raise LocalLLMUnavailable(st["error"])
        _STARTING.set()
        try:
            log(f"[local] starting {STRATA_LABEL} - 1-3 minutes; the PC may stall while it "
                f"locks ~70 GB of RAM")
            _unload_lmstudio(log)
            HEARTBEAT.set_keep(_START_TIMEOUT_S + 120)   # the watchdog must not close it mid-load
            os.makedirs(_STATE_DIR, exist_ok=True)
            # Output to a FILE, never a pipe: the server inherits the launcher's handles and a
            # pipe would block until Strata exits (LOCAL LLMs handoff 11l, lesson 3).
            with open(_START_LOG, "w", encoding="utf-8", errors="replace") as fh:
                proc = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                     _SERVERS_PS1, "strata"],
                    stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                    creationflags=_NO_WINDOW)
            _start_watchdog()
            t0 = time.time()
            next_note = t0 + 30
            while True:
                rc = proc.poll()
                if rc is not None:
                    try:
                        with open(_START_LOG, encoding="utf-8", errors="replace") as fh:
                            out = fh.read()
                    except Exception:  # noqa: BLE001
                        out = ""
                    if rc == 0 and "READY" in out and strata_up(timeout=10):
                        log(f"[local] {STRATA_LABEL} is ready ({int(time.time() - t0)} s)")
                        HEARTBEAT.set_keep(0)    # the caller holds it from here (a run: hold=True)
                        return
                    tail = " | ".join(l.strip() for l in out.strip().splitlines()[-4:])
                    raise LocalLLMUnavailable(f"Strata did not start (exit {rc}): {tail or 'no output'} "
                                              f"- see {_START_LOG} and ~/.local-servers/strata.log")
                if time.time() - t0 > _START_TIMEOUT_S + 60:
                    raise LocalLLMUnavailable(f"Strata was still not ready after {_START_TIMEOUT_S} s "
                                              f"- see ~/.local-servers/strata.log")
                if time.time() >= next_note:
                    log(f"[local] still loading {STRATA_LABEL}… {int(time.time() - t0)} s")
                    next_note += 30
                time.sleep(2)
        except BaseException:
            HEARTBEAT.set_keep(0)             # a failed start holds nothing
            raise
        finally:
            _STARTING.clear()


class _Heartbeat:
    """Tells strata-watchdog.ps1 that Deep Research is using Strata: one file per server
    process in ~/.local-servers/strata-clients/, touched every 15 s while anything holds it
    (a run, a call in flight, or a keep_for window) and deleted when nothing does."""

    def __init__(self):
        self._lock = threading.Lock()
        self._n = 0
        self._until = 0.0
        self._thread = None
        self._path = os.path.join(_HB_DIR, f"deep-research-{os.getpid()}")

    def _touch(self):
        try:
            os.makedirs(_HB_DIR, exist_ok=True)
            with open(self._path, "w", encoding="ascii") as fh:
                fh.write(f"{time.time():.0f}\n")
        except Exception:  # noqa: BLE001
            pass

    def _alive(self):
        return self._n > 0 or time.time() < self._until

    def _loop(self):
        while True:
            with self._lock:
                if not self._alive():
                    self._thread = None
                    try:
                        os.remove(self._path)
                    except Exception:  # noqa: BLE001
                        pass
                    return
                self._touch()
            time.sleep(_HB_EVERY_S)

    def _kick(self):                       # call with the lock held
        self._touch()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="drt-strata-heartbeat",
                                            daemon=True)
            self._thread.start()

    def acquire(self):
        with self._lock:
            self._n += 1
            self._kick()

    def release(self):
        with self._lock:
            self._n = max(0, self._n - 1)

    def keep_for(self, seconds):
        """Hold for at least `seconds` more (extends, never shortens)."""
        with self._lock:
            self._until = max(self._until, time.time() + seconds)
            self._kick()

    def set_keep(self, seconds):
        """Set the hold window to exactly `seconds` from now (0 = none)."""
        with self._lock:
            self._until = time.time() + seconds
            if seconds > 0:
                self._kick()


HEARTBEAT = _Heartbeat()


class _StrataMessages:
    def __init__(self, owner):
        self._o = owner

    def create(self, **kw):
        kw["model"] = STRATA_MODEL           # one model for every role
        kw.pop("temperature", None)          # Strata uses the model card's sampling
        kw.pop("extra_body", None)
        # A thinking budget is passed through only when a caller sets one on purpose (the lean
        # citation check: {"type": "enabled", "budget_tokens": N}); otherwise Strata's default.
        th = kw.pop("thinking", None)
        if isinstance(th, dict) and th.get("type") in ("enabled", "disabled"):
            kw["thinking"] = th
        if int(kw.get("max_tokens") or 0) < _MAX_TOKENS_FLOOR_LOCAL:
            kw["max_tokens"] = _MAX_TOKENS_FLOOR_LOCAL
        HEARTBEAT.acquire()
        try:
            resp = self._o._inner.messages.create(**kw)
        finally:
            HEARTBEAT.release()
        self._o._account(getattr(resp, "usage", None))
        return resp


class StrataClient:
    """Drop-in for anthropic.Anthropic that talks to Flash-Next on Strata.

    start=True starts Strata when it is down (see ensure_strata); hold=True keeps the
    watchdog heartbeat alive until close() - a run holds it from start to finish, so
    browser-only stretches and a Pause cannot let the watchdog close Strata mid-run."""

    def __init__(self, log=None, hold=False, start=True):
        import anthropic
        import httpx
        self.log = log or (lambda m: None)
        self.model = STRATA_MODEL
        self.label = STRATA_LABEL
        # One queued sequence: the citation check runs in its lean form (synthesize.py).
        self.lean_checks = True
        if start:
            ensure_strata(self.log)
        elif not strata_up():
            raise LocalLLMUnavailable(f"{STRATA_LABEL} is not running.")
        # Long timeout: requests queue behind each other on Strata's single sequence.
        self._inner = anthropic.Anthropic(base_url=STRATA_URL, api_key="strata-local",
                                          max_retries=2,
                                          timeout=httpx.Timeout(3600.0, connect=15.0))
        self.messages = _StrataMessages(self)
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._lock = threading.Lock()
        self._held = False
        if hold:
            HEARTBEAT.acquire()
            self._held = True

    def _account(self, usage):
        with self._lock:
            self.usage["calls"] += 1
            if usage is not None:
                self.usage["input"] += int(getattr(usage, "input_tokens", 0) or 0)
                self.usage["output"] += int(getattr(usage, "output_tokens", 0) or 0)
                self.usage["cache_read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    def usage_summary(self) -> dict:
        """Same shape as ClaudeClient's, at $0 (the model runs on this PC)."""
        with self._lock:
            u = dict(self.usage)
        return {"by_model": {STRATA_LABEL: {**u, "usd": 0.0}}, "totals": u, "total_usd": 0.0,
                "local": True}

    def close(self):
        """End of a run: release the hold, and any keep-alive window the scoping questions
        opened, so the watchdog can close Strata about a minute later."""
        if self._held:
            self._held = False
            HEARTBEAT.release()
            HEARTBEAT.set_keep(0)


# ── Claude wrapper: one model + one effort on EVERY call ─────────────────────
# The model id lives in config/drt_models.json + models.py (currently
# claude-opus-4-8 — switched from claude-fable-5 on 2026-08-29 because Fable's
# elevated cyber classifiers were refusing legitimate research topics, silently
# no-op'ing the whole pipeline; Opus 4.8 lacks those extra classifiers). This
# wrapper is the single choke point that normalizes the request shape for the
# Opus-4.x / Fable-class API (all identical on these points):
#   * injects output_config.effort (via extra_body — version-proof) unless the
#     caller set one; override the level with DRT_EFFORT in .env
#   * strips `temperature` (removed on these models — the API returns a 400)
#   * never sends a `thinking` param (rejected / not needed here)
#   * floors max_tokens at 4096 — cheap insurance so tiny caps (16–400) written
#     for older models can't truncate before any answer. A cap costs nothing
#     unless used.
DRT_EFFORT = (os.environ.get("DRT_EFFORT") or "medium").strip().lower()
_MAX_TOKENS_FLOOR = 4096


def _refusal_details(resp) -> dict:
    """Pull the category/explanation off a Claude safety refusal for the UI."""
    sd = getattr(resp, "stop_details", None)
    cat = getattr(sd, "category", None) if sd else None
    expl = getattr(sd, "explanation", None) if sd else None
    return {"category": cat or "safety", "explanation": (expl or "")[:400]}


class _ClaudeMessages:
    def __init__(self, owner):        # owner = ClaudeClient (so it can fall over to local)
        self._o = owner

    def create(self, **kw):
        # Once this run has fallen over to the local model (after a refusal the user
        # chose to route around), EVERY subsequent call goes to local for consistency.
        if self._o._local is not None:
            return self._o._local.messages.create(**kw)
        kw.pop("temperature", None)
        kw.pop("thinking", None)
        if int(kw.get("max_tokens") or 0) < _MAX_TOKENS_FLOOR:
            kw["max_tokens"] = _MAX_TOKENS_FLOOR
        extra_body = dict(kw.pop("extra_body", None) or {})
        extra_body.setdefault("output_config", {"effort": DRT_EFFORT})
        # Prompt caching (2026-09-03): top-level auto-cache = "cache the last cacheable
        # block". The agent loops resend an ever-growing transcript every turn, so the
        # prefix is identical turn-to-turn — cached input is ~10% of full price and
        # counts far less against rate limits. Sent via extra_body (SDK-version-proof).
        extra_body.setdefault("cache_control", {"type": "ephemeral"})
        kw["extra_body"] = extra_body
        # Second, explicit breakpoint on the system prompt: the governance + stage prompt
        # (~4-5K tokens) is identical across every turn of every lane, so it hits even
        # when the messages diverge (parallel lanes share it).
        sysm = kw.get("system")
        if isinstance(sysm, str) and len(sysm) > 4000:
            kw["system"] = [{"type": "text", "text": sysm, "cache_control": {"type": "ephemeral"}}]
        resp = self._o._inner.messages.create(**kw)
        self._o._account(kw.get("model"), getattr(resp, "usage", None))
        # Claude safety refusal → HTTP 200 with stop_reason 'refusal' and empty content.
        # Left unhandled these silently no-op the whole pipeline.
        if getattr(resp, "stop_reason", "") == "refusal":
            # Auto-escalate FIRST (2026-10-21): a per-call refusal on a cheap worker
            # model (Sonnet 5's `cyber` classifier tripping on legitimate hardware /
            # security research is the recurring culprit — same failure that retired
            # Fable) should NOT take down the whole run or demand a human choice.
            # Retry the exact call on the fallback Claude model (Opus-class, which
            # lacks those extra classifiers) before touching the pause/local/stop path.
            # Keeps cheap Sonnet for the calls that never refuse; only pays Opus on the
            # ones that do. If the fallback ALSO refuses, fall through to the old flow.
            cur = str(kw.get("model") or "")
            fb = self._o._fallback_model
            if (fb and cur != fb and self._o._local is None
                    and not self._o._fallback_disabled):
                self._o._note_fallback(cur, fb)
                kw2 = dict(kw); kw2["model"] = fb
                resp = self._o._inner.messages.create(**kw2)
                self._o._account(fb, getattr(resp, "usage", None))
                if getattr(resp, "stop_reason", "") != "refusal":
                    return resp                                # rescued on the fallback
                # fallback refused too → the user-choice path below still applies
            # Give the owner a chance to pause, ask the user, and (if they confirm)
            # swap to the local model, then RESUME from the refused call.
            self._o._on_refusal(resp)
            if self._o._local is not None:
                return self._o._local.messages.create(**kw)   # resume on local
        return resp


class ClaudeClient:
    """Drop-in for anthropic.Anthropic with the house request shape enforced, plus
    optional refusal-driven fall-over to the local model.

    refusal_hook(details) -> "local" | "abort":  provided by the job layer. Called
    (blocking) the first time Claude refuses; on "local" this client swaps to a
    StrataClient (Flash-Next on Strata) for the rest of the run. Parallel callers that refuse at the same
    time honor the first decision rather than re-prompting."""

    # Parallel lanes can draw 429s at the org's rate tier; the SDK retries with backoff,
    # and 5 retries (default 2) rides out a burst instead of aborting a lane.
    _MAX_RETRIES = 5

    def __init__(self, api_key=None, refusal_hook=None, on_stop=None, log=None):
        import anthropic
        self._inner = (anthropic.Anthropic(api_key=api_key, max_retries=self._MAX_RETRIES)
                       if api_key else anthropic.Anthropic(max_retries=self._MAX_RETRIES))
        self._refusal_hook = refusal_hook   # pause + return "local"|"abort" (UI only)
        self._on_stop = on_stop             # called to halt the run (abort / local-unavailable)
        self._log = log or (lambda m: None)
        self._local = None            # set to a StrataClient after a confirmed switch
        self._decision = None         # remembered so repeats don't re-prompt
        # On a per-call safety refusal, silently retry on this model before prompting
        # the user (set DRT_FALLBACK_MODEL="" to disable and keep the old pause-first flow).
        _fb = os.environ.get("DRT_FALLBACK_MODEL", "claude-opus-4-8").strip()
        self._fallback_model = _fb
        self._fallback_disabled = (_fb == "")
        self._fallback_noted = set()  # models already logged as escalated (log once each)
        self._lock = __import__("threading").Lock()
        self.messages = _ClaudeMessages(self)
        # Per-run token accounting, by model: {model: {calls, input, output, cache_read,
        # cache_write}} — surfaced in the report audit so every run shows its real spend.
        self.usage = {}

    def _account(self, model, usage):
        if usage is None:
            return
        with self._lock:
            u = self.usage.setdefault(str(model or "?"),
                                      {"calls": 0, "input": 0, "output": 0,
                                       "cache_read": 0, "cache_write": 0})
            u["calls"] += 1
            u["input"] += int(getattr(usage, "input_tokens", 0) or 0)
            u["output"] += int(getattr(usage, "output_tokens", 0) or 0)
            u["cache_read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            u["cache_write"] += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

    # Anthropic first-party list prices, $ per 1M tokens (cached: 2026-06-24 skill table);
    # cache write ≈ 1.25× input, cache read ≈ 0.1× input.
    _PRICES = {"claude-opus-4-8": (5.0, 25.0), "claude-opus-5": (5.0, 25.0),
               "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
               "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
               "claude-haiku-4-5": (1.0, 5.0), "claude-haiku-5-5": (0.10, 0.50),
               "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
               "claude-fable-5-2": (8.0, 40.0)}

    def usage_summary(self) -> dict:
        """{'by_model': {...}, 'total_usd': float, 'totals': {...}} for the audit."""
        by, tot = {}, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        total_usd = 0.0
        with self._lock:
            items = {m: dict(u) for m, u in self.usage.items()}
        for m, u in items.items():
            pin, pout = self._PRICES.get(m, (5.0, 25.0))
            usd = (u["input"] * pin + u["cache_write"] * pin * 1.25
                   + u["cache_read"] * pin * 0.1 + u["output"] * pout) / 1e6
            by[m] = {**u, "usd": round(usd, 2)}
            total_usd += usd
            for k in tot:
                tot[k] += u[k]
        return {"by_model": by, "totals": tot, "total_usd": round(total_usd, 2)}

    def _note_fallback(self, cur, fb):
        """Log the first auto-escalation per worker model (surfaced in the activity feed)."""
        with self._lock:
            if cur in self._fallback_noted:
                return
            self._fallback_noted.add(cur)
        try:
            self._log(f"[refusal] {cur or 'worker'} declined (cyber/safety) — "
                      f"auto-retrying this call on {fb}")
        except Exception:
            pass

    def _stop(self):
        if self._on_stop:
            try:
                self._on_stop()
            except Exception:
                pass

    def _on_refusal(self, resp):
        with self._lock:
            if self._local is not None:
                return "local"                       # already switched by another call
            if self._decision == "abort":
                self._stop()                         # keep the run halted; no re-prompt
                return "abort"
            details = _refusal_details(resp)
            pref = self._refusal_hook(details) if self._refusal_hook else "abort"
            if pref == "local":
                try:
                    # Starts Strata if it is down (1-3 min); holds it until close().
                    self._local = StrataClient(log=self._log, hold=True)
                    self._decision = "local"
                    return "local"                   # resume on local; run is NOT stopped
                except Exception as e:               # could not reach or start Strata
                    self._local = None
                    try:
                        self._log(f"[refusal] local model unavailable: {e}")
                    except Exception:
                        pass
            # abort, or a local swap that failed → halt and assemble what we have.
            self._decision = "abort"
            self._stop()
            return "abort"

    def close(self):
        """End of run: release the local model if the run fell over to it."""
        loc = self._local
        if loc is not None and hasattr(loc, "close"):
            loc.close()


def switch_client_to_local(client) -> bool:
    """Force a ClaudeClient over to the local model outside the refusal path (unused
    by the pipeline today, but handy for tests/tools). True on success."""
    try:
        client._local = StrataClient(hold=True)
        return True
    except Exception:
        return False


def make_client(provider="claude", api_key=None, log=None, base_url=None,
                refusal_hook=None, on_stop=None, hold=False):
    """Return an AI client for the chosen provider.

    local  → StrataClient - Flash-Next on Strata, started if it is down (raises
             LocalLLMUnavailable when it cannot be reached or started). hold=True keeps
             Strata from being closed by its watchdog until client.close(). base_url is
             ignored (DRT_STRATA_URL sets Strata's address).
    claude → ClaudeClient (house model + effort enforced; refusal_hook lets the job
             layer offer a local-model fall-over on a safety refusal, on_stop halts
             the run when the user aborts or local is unavailable), or None when no
             api_key (callers guard on `if client`).
    """
    if is_local(provider):
        return StrataClient(log=log, hold=hold)
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    return ClaudeClient(api_key=api_key, refusal_hook=refusal_hook, on_stop=on_stop, log=log)
