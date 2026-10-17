"""Dense embeddings via Gemini's OpenAI-compatible endpoint. Optional: any failure degrades search to BM25 only.

Cost guards: each distinct chunk text is embedded once (vectors cached in DuckDB by sha1(text) + model), requests are
batched, a reindex embeds at most `rag_embed_max` new chunks, query vectors are memoised, and after an API error the
dense layer stays off for `COOLDOWN_S` instead of paying a timeout on every question.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

import httpx

from wellops.settings import settings

BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
BATCH = 100  # Gemini batch limit per embeddings request
MAX_INPUT_CHARS = 6000  # ~1.5k tokens, under the 2k-token input cap
COOLDOWN_S = 120
RETRY_S = 3.0
EmbedFn = Callable[[list[str]], list[list[float]]]

_override: EmbedFn | None = None  # tests install a fake here (no network)
_down_until = 0.0
_qcache: OrderedDict[str, list[float]] = OrderedDict()


class EmbedError(RuntimeError):
    """The embeddings API is unavailable; callers fall back to BM25."""


def model_key() -> str:
    """Vector-cache key part: model and dimension (vectors from different models never mix)."""
    return f"{settings.rag_embed_model}@{settings.rag_embed_dims}" if _override is None else "fake@test"


def set_embedder(fn: EmbedFn | None) -> None:
    global _override, _down_until
    _override, _down_until = fn, 0.0
    _qcache.clear()


def available() -> bool:
    return _override is not None or (settings.rag_embed and bool(settings.gemini_api_key) and time.time() >= _down_until)


def _gemini(texts: list[str], timeout: float) -> list[list[float]]:
    r = httpx.post(f"{BASE}/embeddings", timeout=timeout, headers={"Authorization": f"Bearer {settings.gemini_api_key}"},
                   json={"model": settings.rag_embed_model, "input": texts, "dimensions": settings.rag_embed_dims})
    if r.status_code != 200:
        raise EmbedError(f"embeddings HTTP {r.status_code}: {r.text[:160]}")
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))  # Gemini omits index; order is input order
    if len(data) != len(texts):
        raise EmbedError(f"embeddings returned {len(data)} vectors for {len(texts)} inputs")
    return [d["embedding"] for d in data]


def embed(texts: list[str], timeout: float = 60) -> list[list[float]]:
    """Embed texts in batches; raises EmbedError (and starts the cooldown) on any failure."""
    global _down_until
    if not available():
        raise EmbedError("dense layer off (no key, disabled, or cooling down after an error)")
    fn = _override or (lambda b: _gemini(b, timeout))
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), BATCH):
            batch = [t[:MAX_INPUT_CHARS] for t in texts[i:i + BATCH]]
            try:
                out += fn(batch)
            except (EmbedError, httpx.TransportError):  # one retry for a 429/5xx or a dropped connection
                time.sleep(RETRY_S)
                out += fn(batch)
    except EmbedError:
        _down_until = time.time() + COOLDOWN_S
        raise
    except Exception as e:  # network, JSON shape: same degradation
        _down_until = time.time() + COOLDOWN_S
        raise EmbedError(f"{type(e).__name__}: {e}"[:200]) from e
    return out


def embed_query(q: str) -> list[float]:
    """One memoised query vector (short timeout: the question path must stay fast)."""
    k = f"{model_key()}|{q.strip().lower()}"
    if k in _qcache:
        _qcache.move_to_end(k)
        return _qcache[k]
    v = embed([q], timeout=8)[0]
    _qcache[k] = v
    if len(_qcache) > 512:
        _qcache.popitem(last=False)
    return v
