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


def detect_local_model(base_url=None, timeout=6) -> str:
    """Return the id of the model currently loaded in LM Studio (first one)."""
    import requests
    base = (base_url or LMSTUDIO_URL).rstrip("/")
    try:
        r = requests.get(base + "/models", timeout=timeout)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        raise LocalLLMUnavailable(
            f"LM Studio not reachable at {base}. Start LM Studio, load a model, and turn on "
            f"its local server (Developer tab). [{type(e).__name__}: {e}]")
    if not data:
        raise LocalLLMUnavailable(f"LM Studio is running at {base} but no model is loaded.")
    return data[0].get("id") or "local-model"


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


def make_client(provider="claude", api_key=None, log=None, base_url=None):
    """Return an AI client for the chosen provider.

    local  → LMStudioClient (raises LocalLLMUnavailable if LM Studio is down).
    claude → anthropic.Anthropic, or None when no api_key (callers guard on `if client`).
    """
    if is_local(provider):
        return LMStudioClient(base_url=base_url, log=log)
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)
