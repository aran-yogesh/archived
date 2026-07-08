"""Optional semantic embeddings for archived.

Embeddings come from fastembed (model BAAI/bge-small-en-v1.5), installed
via the [semantic] extra. When fastembed is missing, embed_text and
embed_query return None and search silently stays keyword-only. This
module is the only place that knows whether embeddings are available.
"""

import math
from array import array

MODEL_NAME = "BAAI/bge-small-en-v1.5"

_model = None
_unavailable = False


def _get_model():
    """Load the fastembed model once; returns None when it can't load."""
    global _model, _unavailable
    if _model is None and not _unavailable:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(MODEL_NAME)
        except Exception:
            _unavailable = True
    return _model


def embed_text(text):
    """Turn memory text into float32 embedding bytes, or None when
    embeddings are unavailable (callers then stay keyword-only)."""
    model = _get_model()
    if model is None or not text or not text.strip():
        return None
    return array("f", next(iter(model.embed([text])))).tobytes()


def embed_query(text):
    """Embed a search query (bge models use a special query prompt),
    or None when embeddings are unavailable."""
    model = _get_model()
    if model is None or not text or not text.strip():
        return None
    return array("f", next(iter(model.query_embed([text])))).tobytes()


def to_vector(blob):
    """Decode embedding bytes back into an array of floats."""
    return array("f", blob)


def cosine(vec_a, vec_b):
    """Cosine similarity between two decoded vectors (0.0 on mismatch)."""
    if len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm = math.sqrt(sum(a * a for a in vec_a)) * math.sqrt(sum(b * b for b in vec_b))
    return dot / norm if norm else 0.0
