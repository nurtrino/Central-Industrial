"""
LLM seam for the vendored Odysseus engine → routed to our Anthropic key.

Odysseus's real llm_core targets an OpenAI-compatible / Anthropic endpoint with a
huge provider-detection layer. The engine only needs `llm_call_async(...) -> str`,
so this adapter implements exactly that against the Anthropic SDK, using
claude-sonnet-4-6 so an Odysseus-vs-DRT comparison isolates methodology, not model.
"""

import os
from typing import Dict, List, Optional


async def llm_call_async(
    url: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
    messages: Optional[List[Dict]] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    headers: Optional[Dict] = None,
    timeout: int = 60,
    **kwargs,
) -> str:
    """Async LLM call → Anthropic. `url`/`headers` are ignored (we use the env key)."""
    messages = messages or []
    # Hoist any system messages; keep user/assistant turns in order.
    sys_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    conv = [{"role": m["role"], "content": str(m.get("content") or "")}
            for m in messages if m.get("role") in ("user", "assistant")]
    if not conv:
        conv = [{"role": "user", "content": "\n\n".join(sys_parts) or "(empty)"}]
        sys_parts = []
    # Anthropic requires the first message to be 'user'.
    if conv[0]["role"] != "user":
        conv = [{"role": "user", "content": "Continue."}] + conv

    mdl = model if str(model or "").startswith("claude") else "claude-sonnet-4-6"
    kw = dict(
        model=mdl,
        max_tokens=max(16, min(int(max_tokens or 4096), 8192)),
        messages=conv,
    )
    if sys_parts:
        kw["system"] = "\n\n".join(sys_parts)
    if temperature is not None:                 # sonnet-4-6 accepts temperature
        kw["temperature"] = max(0.0, min(1.0, float(temperature)))

    # Fresh client per call — each research job runs its own asyncio loop, and an
    # AsyncAnthropic/httpx client is bound to the loop it was created in.
    from anthropic import AsyncAnthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to the .env file.")
    async with AsyncAnthropic(api_key=key) as client:
        resp = await client.with_options(timeout=float(timeout or 60)).messages.create(**kw)
    return "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text")
