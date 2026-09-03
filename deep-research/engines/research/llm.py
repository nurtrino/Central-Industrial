"""
Provider abstraction: route AI calls to Claude (Anthropic) OR a local model in LM Studio.

The Deep Research engine calls `client.messages.create(model=..., system=..., messages=...,
tools=..., max_tokens=...)` throughout and reads Anthropic-shaped responses (`resp.content`
= list of blocks with `.type`/`.text`/`.id`/`.name`/`.input`, plus `resp.stop_reason`).

`make_client("local")` returns an `LMStudioClient` that presents the SAME `.messages.create`
surface but talks to LM Studio's OpenAI-compatible API (`/v1/chat/completions`). It translates
both directions — Anthropic tool defs → OpenAI `tools`, and OpenAI `tool_calls` → Anthropic
`tool_use` blocks — so the existing tool-use loop in agent.py works unchanged. The `model=`
argument is IGNORED for local (LM Studio uses whatever model is loaded).

Claude's server-side web_search tool cannot be mapped to a local model; the caller skips
Stage 1 in local mode (see run_search).
"""
import json
import os
import re


def _strip_think(s):
    """Remove <think>…</think> reasoning blocks (some local models, e.g. Qwen3 'thinking'
    variants, emit them inline in the message content). Tool calls arrive separately in
    `tool_calls`, so this only cleans the visible text we surface to the pipeline."""
    if not s:
        return s
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)   # closed blocks
    s = re.sub(r"^.*?</think>", "", s, flags=re.S)          # leading/unclosed thinking
    s = re.sub(r"<think>.*$", "", s, flags=re.S)            # trailing unclosed thinking
    return s.strip()

# LM Studio's default OpenAI-compatible endpoint; override with LMSTUDIO_URL in .env.
# Use 127.0.0.1 (not "localhost") — on Windows "localhost" can resolve to IPv6 ::1 first,
# which LM Studio's IPv4-only server refuses.
LMSTUDIO_URL = (os.environ.get("LMSTUDIO_URL") or "http://127.0.0.1:1234/v1").rstrip("/")

_LOCAL_ALIASES = ("local", "lmstudio", "local_ai", "localai")


class LocalLLMUnavailable(RuntimeError):
    """LM Studio isn't reachable or has no model loaded."""


def is_local(provider) -> bool:
    return (provider or "claude").strip().lower() in _LOCAL_ALIASES


def _is_embedding_model(mid: str, mtype: str = "") -> bool:
    return "embed" in (mid or "").lower() or (mtype or "").lower().startswith("embed")


def detect_local_model(base_url=None, timeout=6) -> str:
    """Return the id of the LM Studio CHAT model to use — preferring one that is actually
    LOADED in memory, not merely downloaded.

    LM Studio's OpenAI-compatible /v1/models lists every *downloaded* model regardless of
    whether it's loaded, so the old 'take the first one' logic would silently name an
    unloaded model and lean on just-in-time loading (and could pick a different model than
    the one you loaded). This now:
      1. asks LM Studio's native /api/v0/models (which reports state) and returns the first
         LOADED chat model;
      2. if that API is reachable and shows chat models but NONE loaded, raises a clear
         'no model loaded' error rather than guessing;
      3. falls back to the first available chat model from /v1/models only when the
         state-aware API is unavailable (older LM Studio);
      4. raises LocalLLMUnavailable when LM Studio is unreachable or has no chat model.
    """
    import requests
    base = (base_url or LMSTUDIO_URL).rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base   # strip /v1 for the native API

    # 1) State-aware path: LM Studio's native REST API reports loaded/not-loaded + type.
    try:
        r = requests.get(f"{root}/api/v0/models", timeout=timeout)
        if r.ok:
            models = (r.json() or {}).get("data") or []
            chat = [m for m in models if not _is_embedding_model(m.get("id", ""), m.get("type", ""))]
            loaded = [m for m in chat if (m.get("state") or "").lower() in ("loaded", "loading")]
            if loaded:
                return loaded[0].get("id") or "local-model"
            if chat:
                raise LocalLLMUnavailable(
                    f"LM Studio is running at {base} but no model is loaded — "
                    f"load a model in LM Studio (Developer tab → select a model), then retry. "
                    f"(available but unloaded: {', '.join(m.get('id','?') for m in chat[:3])})")
    except LocalLLMUnavailable:
        raise
    except Exception:
        pass  # older LM Studio without /api/v0 — fall through to the /v1 list

    # 2) Fallback (no state info): first available chat model from the OpenAI-compatible API.
    try:
        r = requests.get(f"{base}/models", timeout=timeout)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        raise LocalLLMUnavailable(
            f"LM Studio not reachable at {base}. Start LM Studio, load a model, and turn on "
            f"its local server (Developer tab). [{type(e).__name__}: {e}]")
    chat = [m for m in data if not _is_embedding_model(m.get("id", ""))]
    if not chat:
        raise LocalLLMUnavailable(f"LM Studio is running at {base} but no chat model is loaded.")
    return chat[0].get("id") or "local-model"


# ── Anthropic-shaped response objects ────────────────────────────────────────
class _Block:
    __slots__ = ("type", "text", "id", "name", "input")

    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type; self.text = text; self.id = id; self.name = name; self.input = input


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content; self.stop_reason = stop_reason


def _blk_get(b, attr, default=None):
    if isinstance(b, dict):
        return b.get(attr, default)
    return getattr(b, attr, default)


def _to_openai_messages(system, messages):
    """Translate Anthropic (system + messages) into an OpenAI chat message list.

    Handles: string content; assistant content as a list of blocks (text + tool_use);
    user content as a list of tool_result dicts.
    """
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts, tool_calls = [], []
            for b in content:
                bt = _blk_get(b, "type")
                if bt == "text":
                    t = _blk_get(b, "text") or ""
                    if t:
                        texts.append(t)
                elif bt == "tool_use":
                    tool_calls.append({
                        "id": _blk_get(b, "id"),
                        "type": "function",
                        "function": {"name": _blk_get(b, "name"),
                                     "arguments": json.dumps(_blk_get(b, "input") or {})},
                    })
            msg = {"role": "assistant", "content": "\n".join(texts)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:  # user turn — may carry tool_result blocks
            leftover = []
            for b in content:
                if _blk_get(b, "type") == "tool_result":
                    tc = _blk_get(b, "content")
                    if isinstance(tc, list):
                        tc = "\n".join(_blk_get(x, "text", "") if not isinstance(x, str) else x
                                       for x in tc)
                    out.append({"role": "tool", "tool_call_id": _blk_get(b, "tool_use_id"),
                                "content": tc if isinstance(tc, str) else json.dumps(tc)})
                else:
                    leftover.append(_blk_get(b, "text", "") or "")
            if leftover:
                out.append({"role": "user", "content": "\n".join(p for p in leftover if p)})
    return out


def _to_openai_tools(tools):
    """Anthropic tool defs → OpenAI function tools. Server-side tools (no input_schema,
    e.g. Claude's web_search) are dropped — they have no local equivalent."""
    if not tools:
        return None
    out = []
    for t in tools:
        schema = t.get("input_schema") if isinstance(t, dict) else None
        if not schema:
            continue
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""), "parameters": schema}})
    return out or None


class _Messages:
    def __init__(self, client):
        self._c = client

    def create(self, model=None, max_tokens=1024, system=None, messages=None,
               tools=None, temperature=None, **kw):
        return self._c._create(max_tokens, system, messages or [], tools, temperature)


class LMStudioClient:
    """Drop-in stand-in for anthropic.Anthropic that talks to LM Studio."""

    def __init__(self, base_url=None, log=None):
        self.base_url = (base_url or LMSTUDIO_URL).rstrip("/")
        self.log = log or (lambda m: None)
        self.model = detect_local_model(self.base_url)
        self.messages = _Messages(self)

    def _create(self, max_tokens, system, messages, tools, temperature):
        import requests
        payload = {
            "model": self.model,
            "messages": _to_openai_messages(system, messages),
            "max_tokens": int(max_tokens) if max_tokens else 1024,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        otools = _to_openai_tools(tools)
        if otools:
            payload["tools"] = otools
            payload["tool_choice"] = "auto"
        r = requests.post(self.base_url + "/chat/completions", json=payload, timeout=600)
        r.raise_for_status()
        choice = ((r.json() or {}).get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        blocks = []
        content = _strip_think(msg.get("content") or "")
        if content:
            blocks.append(_Block("text", text=content))
        tcs = msg.get("tool_calls") or []
        for tc in tcs:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            blocks.append(_Block("tool_use", id=tc.get("id") or os.urandom(6).hex(),
                                 name=fn.get("name"), input=args))
        if not blocks:
            blocks.append(_Block("text", text=""))
        return _Resp(blocks, "tool_use" if tcs else "end_turn")


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
        # Left unhandled these silently no-op the whole pipeline. Give the owner a chance
        # to pause, ask the user, and (if they confirm) swap to the local model, then
        # RETRY this exact call so the pipeline continues from the point of refusal.
        if getattr(resp, "stop_reason", "") == "refusal":
            self._o._on_refusal(resp)
            if self._o._local is not None:
                return self._o._local.messages.create(**kw)   # resume on local
        return resp


class ClaudeClient:
    """Drop-in for anthropic.Anthropic with the house request shape enforced, plus
    optional refusal-driven fall-over to the local model.

    refusal_hook(details) -> "local" | "abort":  provided by the job layer. Called
    (blocking) the first time Claude refuses; on "local" this client swaps to an
    LMStudioClient for the rest of the run. Parallel callers that refuse at the same
    time honor the first decision rather than re-prompting."""

    # Parallel lanes can draw 429s at the org's rate tier; the SDK retries with backoff,
    # and 5 retries (default 2) rides out a burst instead of aborting a lane.
    _MAX_RETRIES = 5

    def __init__(self, api_key=None, refusal_hook=None, on_stop=None):
        import anthropic
        self._inner = (anthropic.Anthropic(api_key=api_key, max_retries=self._MAX_RETRIES)
                       if api_key else anthropic.Anthropic(max_retries=self._MAX_RETRIES))
        self._refusal_hook = refusal_hook   # pause + return "local"|"abort" (UI only)
        self._on_stop = on_stop             # called to halt the run (abort / local-unavailable)
        self._local = None            # set to an LMStudioClient after a confirmed switch
        self._decision = None         # remembered so repeats don't re-prompt
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
               "claude-haiku-4-5": (1.0, 5.0)}

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
                    self._local = LMStudioClient()   # the user confirmed it's up
                    self._decision = "local"
                    return "local"                   # resume on local; run is NOT stopped
                except Exception:                    # went away between confirm and swap
                    self._local = None
            # abort, or a local swap that failed → halt and assemble what we have.
            self._decision = "abort"
            self._stop()
            return "abort"


def switch_client_to_local(client) -> bool:
    """Force a ClaudeClient over to the local model outside the refusal path (unused
    by the pipeline today, but handy for tests/tools). True on success."""
    try:
        client._local = LMStudioClient()
        return True
    except Exception:
        return False


def make_client(provider="claude", api_key=None, log=None, base_url=None,
                refusal_hook=None, on_stop=None):
    """Return an AI client for the chosen provider.

    local  → LMStudioClient (raises LocalLLMUnavailable if LM Studio is down).
    claude → ClaudeClient (house model + effort enforced; refusal_hook lets the job
             layer offer a local-model fall-over on a safety refusal, on_stop halts
             the run when the user aborts or local is unavailable), or None when no
             api_key (callers guard on `if client`).
    """
    if is_local(provider):
        return LMStudioClient(base_url=base_url, log=log)
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    return ClaudeClient(api_key=api_key, refusal_hook=refusal_hook, on_stop=on_stop)
