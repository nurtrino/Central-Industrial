"""
DRT per-role model map — the single source of truth for WHICH Claude model handles
WHICH task in the Deep Research pipeline.

Per user directive (2026-08-11): EVERY role runs Claude Fable 5 at medium reasoning
effort (override via DRT_EFFORT in .env) — no tier-splitting. The effort level is injected client-side in
engines/research/llm.py (ClaudeClient wrapper), not here; this map only carries
the model id.

  - extract    : per-page goal extraction (Pass A) + category classify
  - search     : the browser agent tool-use loop + Stage-1 baseline sweep
  - route      : relevance filters, stop-judge, gap-queries
  - plan       : the upfront research planner
  - synthesize : the cited report (Pass B) — THE deliverable

Override any role in  config/drt_models.json  (edit + restart the server to apply):
    { "models": { "synthesize": "claude-opus-4-8", ... } }
Unknown roles / blank values are ignored (fall back to the defaults below).
"""
from __future__ import annotations

import json
import os

# 2026-09-03: orchestrator-worker split (Anthropic's research system: Opus lead, Sonnet
# subagents). Navigators + high-volume extraction/routing on Sonnet 5; the judgment
# steps (plan / lane planning / lead review, the hot-trail digger) and the deliverable
# (synthesis) on Opus 4.8.
_DEFAULT_MODELS = {
    "extract":    "claude-sonnet-5",
    "search":     "claude-sonnet-5",
    "route":      "claude-sonnet-5",
    "plan":       "claude-opus-4-8",
    "dig":        "claude-opus-4-8",
    "synthesize": "claude-opus-4-8",
}

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "drt_models.json")


def _load_overrides() -> dict:
    """Read config/drt_models.json; keep only known roles with non-empty string values."""
    try:
        # utf-8-sig tolerates a BOM (e.g. from a PowerShell `Set-Content -Encoding utf8`
        # edit) — without it json.load throws and every override is silently ignored.
        with open(_CONFIG_PATH, encoding="utf-8-sig") as fh:
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
