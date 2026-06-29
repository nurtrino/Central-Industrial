"""
Text cleaning + quality filtering for the Odysseus deep-research engine.

`is_low_quality` + LOW_QUALITY_MARKERS are vendored verbatim from Odysseus.
`strip_thinking` is a self-contained reimplementation of its text_helpers.strip_think
(strips <think>…</think> reasoning blocks and prompt-echo) so we don't have to
vendor the whole text_helpers module.
"""

import re

_THINK_BLOCK = re.compile(
    r"<\s*(think|thinking|reasoning|thought)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# An unclosed opening tag (model got cut off mid-think) — drop everything after it.
_THINK_OPEN_UNCLOSED = re.compile(
    r"<\s*(think|thinking|reasoning|thought)\s*>.*$",
    re.IGNORECASE | re.DOTALL,
)


def strip_thinking(text):
    """Strip <think>…</think>-style reasoning blocks from LLM output.

    Preserves None passthrough — callers pass Optional[str] and expect None back
    when the underlying LLM call failed.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    out = _THINK_BLOCK.sub("", text)
    # A leftover lone opening tag means the closing tag was lost — strip the rest.
    if re.search(r"<\s*(think|thinking|reasoning|thought)\s*>", out, re.IGNORECASE):
        out = _THINK_OPEN_UNCLOSED.sub("", out)
    return out.strip()


# Markers indicating extracted content is boilerplate, error text, or empty.
LOW_QUALITY_MARKERS = [
    "insufficient to",
    "content is insufficient",
    "no substantive data",
    "does not contain",
    "not relevant to",
    "no relevant information",
    "unable to extract",
    "completely unrelated",
    "boilerplate",
    "footer text",
    "cookie consent",
    "cookie banner",
    "cookie notice",
    "copyright notice",
    "copyright footer",
    "all rights reserved",
]


def is_low_quality(summary: str) -> bool:
    """True if a finding summary indicates useless or irrelevant content."""
    try:
        if not isinstance(summary, str) or not summary:
            return True
        low = summary.lower()
        return any(marker in low for marker in LOW_QUALITY_MARKERS)
    except Exception:
        return False  # fail open
