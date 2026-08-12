"""
Semantic rerank of a pooled set of search results by relevance to the research question.

Embeds the query + each item's text with a small local model (fastembed → ONNX, CPU, no
torch, no network at inference after a one-time model download) and sorts by cosine
similarity. This surfaces the highest-signal results out of a wide multi-engine pool so the
agent deep-reads the best ones instead of whatever an engine ranked first.

Bulletproof: any failure (fastembed missing, model download blocked, bad input) returns the
items UNCHANGED — rerank can never break a research run. Disable entirely with DRT_RERANK=0.
"""
import os

_MODEL = None
_TRIED = False


def _enabled() -> bool:
    return os.environ.get("DRT_RERANK", "1").strip().lower() not in ("0", "false", "no", "off")


def _model():
    """Lazily load the embedding model once; None if unavailable/disabled."""
    global _MODEL, _TRIED
    if _TRIED:
        return _MODEL
    _TRIED = True
    if not _enabled():
        return None
    try:
        from fastembed import TextEmbedding
        # Default small English model (BAAI/bge-small-en-v1.5) — fast on CPU, ~130MB one-time.
        _MODEL = TextEmbedding(model_name=os.environ.get("DRT_RERANK_MODEL",
                                                         "BAAI/bge-small-en-v1.5"))
    except Exception:
        _MODEL = None
    return _MODEL


def available() -> bool:
    return _model() is not None


def rerank(query, items, text_of=lambda x: str(x), top_k=None):
    """Return `items` sorted by semantic similarity to `query` (descending).

    text_of(item) → the text to embed for that item (e.g. title + snippet).
    top_k → optionally keep only the top N. Identity (original order) on any failure.
    """
    items = list(items or [])
    if len(items) < 2 or not (query or "").strip():
        return items[:top_k] if top_k else items
    model = _model()
    if model is None:
        return items[:top_k] if top_k else items
    try:
        import numpy as np
        texts = [((text_of(it) or "").strip() or " ")[:800] for it in items]
        vecs = list(model.embed([query.strip()] + texts))
        q = np.asarray(vecs[0], dtype="float32")
        M = np.asarray(vecs[1:], dtype="float32")
        q /= (np.linalg.norm(q) + 1e-8)
        M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
        scores = M @ q
        order = np.argsort(-scores)
        ranked = [items[i] for i in order]
        return ranked[:top_k] if top_k else ranked
    except Exception:
        return items[:top_k] if top_k else items
