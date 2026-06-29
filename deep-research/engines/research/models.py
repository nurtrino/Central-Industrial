"""
DRT per-role model map — the single source of truth for WHICH Claude model handles
WHICH task in the Deep Research pipeline.

Tier-optimized by design: put the expensive model only where reasoning quality reaches
the reader, and keep the high-volume mechanical work cheap.

  - extract    : per-page goal extraction (Pass A) + category classify — HIGHEST volume → Haiku
  - search     : the browser agent tool-use loop + Stage-1 web search — high volume → Sonnet
  - route      : relevance filters, stop-judge, gap-queries — short judgments → Sonnet
  - plan       : the upfront research planner — one call, steers the whole run → Opus
  - synthesize : the cited DD report (Pass B) — THE deliverable → Opus

Override any role in  config/drt_models.json  (edit + restart the server to apply):
    { "models": { "synthesize": "claude-sonnet-4-6", ... } }
Unknown roles / blank values are ignored (fall back to the defaults below).
"""
from __future__ import annotations

import json
import os

_DEFAULT_MODELS = {
    "extract":    "claude-haiku-4-5-20251001",
    "search":     "claude-sonnet-4-6",
    "route":      "claude-sonnet-4-6",
    "plan":       "claude-opus-4-8",
    "synthesize": "claude-opus-4-8",
}

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "drt_models.json")


def _load_overrides() -> dict:
    """Read config/drt_models.json; keep only known roles with non-empty string values."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        m = data.get("models", data) if isinstance(data, dict) else {}
        return {k: v.strip() for k, v in m.items()
                if k in _DEFAULT_MODELS and isinstance(v, str) and v.strip()}
    except Exception:
        return {}


# Loaded once at import; a server restart (in-app "Restart Server") re-reads the file.
_OVERRIDES = _load_overrides()


def get_model(role: str) -> str:
    """Model id for a DRT role, honoring config/drt_models.json overrides."""
    return _OVERRIDES.get(role) or _DEFAULT_MODELS.get(role) or _DEFAULT_MODELS["search"]


def all_models() -> dict:
    """Effective role→model map (defaults + overrides) — for surfacing in the audit."""
    return {**_DEFAULT_MODELS, **_OVERRIDES}


def reload() -> dict:
    """Re-read the override file (without a process restart). Returns the effective map."""
    global _OVERRIDES
    _OVERRIDES = _load_overrides()
    return all_models()
