"""
rag_service.py — RAG (Retrieval-Augmented Generation) for SmartMart
====================================================================
Provides vector-based semantic search over the product catalogue using
lightweight in-process embeddings via sentence-transformers (or a fast
TF-IDF fallback when sentence-transformers is not installed).

Usage
-----
    from smart_mart.services.rag_service import rag_search, build_index

    # Build / rebuild the index (called automatically on first use)
    build_index()

    # Semantic search
    chunks = rag_search("sugar-free cashew gift box")
    # → [{product_id, name, category, price, description, score}, ...]

    # RAG-augmented context string for Claude
    context = rag_context_for_query("do you have gluten-free snacks?")

Architecture
------------
* Uses an in-memory inverted-index + TF-IDF cosine similarity as the
  default backend (zero extra dependencies, fast, good enough for <500
  products).
* When 'sentence-transformers' is installed it automatically upgrades to
  a proper dense-vector model ('all-MiniLM-L6-v2', 22 MB) for much better
  semantic understanding.
* The index is rebuilt every RAG_INDEX_TTL seconds (default 10 minutes)
  so new products appear quickly without a manual rebuild.
* All caching goes through the shared cache_service.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
RAG_INDEX_TTL      = int(os.environ.get("RAG_INDEX_TTL", 600))   # seconds
RAG_TOP_K          = int(os.environ.get("RAG_TOP_K", 5))
RAG_MIN_SCORE      = float(os.environ.get("RAG_MIN_SCORE", 0.05))
USE_DENSE_EMBEDDINGS = os.environ.get("RAG_DENSE", "auto").lower()  # auto|yes|no

# ── Index state ───────────────────────────────────────────────────────────────
_index: dict[str, Any] = {}
_last_built: float = 0.0
_dense_model = None  # sentence-transformers model (lazy-loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> list[str]:
    """Lowercase, remove punctuation, split on whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [t for t in text.split() if len(t) > 1]


def _build_tfidf_doc(product) -> str:
    """Build a rich text document for a product."""
    parts = [
        product.name or "",
        product.category or "",
        product.description or "",
        product.pack_size or "",
        product.sku or "",
        f"price {float(product.selling_price):.0f} NPR" if product.selling_price else "",
        f"unit {product.unit}" if product.unit else "",
    ]
    # Nepali-aware: duplicate common aliases so they match better
    ALIASES = {
        "almond": "badam badaam",
        "cashew": "kaju kew",
        "walnut": "okhar akhrot",
        "pistachio": "pista",
        "raisin": "kismis kishmish munakka",
        "date": "khajur khajoor",
        "fig": "anjeer",
        "apricot": "khubani",
        "peanut": "mungphali groundnut",
        "coconut": "nariyal nariwal",
    }
    name_lower = (product.name or "").lower()
    for eng, nep in ALIASES.items():
        if eng in name_lower:
            parts.append(nep)
        for n in nep.split():
            if n in name_lower:
                parts.append(eng)

    return " ".join(p for p in parts if p)


# ═══════════════════════════════════════════════════════════════════════════════
# Dense embedding backend (sentence-transformers, optional)
# ═══════════════════════════════════════════════════════════════════════════════

def _try_load_dense_model():
    """Try to load sentence-transformers. Returns model or None."""
    global _dense_model
    if _dense_model is not None:
        return _dense_model
    if USE_DENSE_EMBEDDINGS == "no":
        return None
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _dense_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("RAG: using sentence-transformers dense embeddings")
        return _dense_model
    except ImportError:
        if USE_DENSE_EMBEDDINGS == "yes":
            logger.warning("RAG: sentence-transformers not installed, falling back to TF-IDF")
        return None
    except Exception as exc:
        logger.warning("RAG: failed to load dense model (%s), using TF-IDF", exc)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-10)


# ═══════════════════════════════════════════════════════════════════════════════
# TF-IDF backend (always available)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_tfidf_index(docs: list[tuple[int, str]]) -> dict:
    """Build an inverted index + document vectors for TF-IDF cosine search."""
    # IDF
    df: dict[str, int] = defaultdict(int)
    tokenized = []
    for _pid, text in docs:
        tokens = set(_tokenize(text))
        for tok in tokens:
            df[tok] += 1
        tokenized.append(tokens)

    N = len(docs)
    idf = {tok: math.log((N + 1) / (cnt + 1)) + 1 for tok, cnt in df.items()}

    # TF-IDF vectors
    vectors: list[dict[str, float]] = []
    for tokens_set in tokenized:
        tf: dict[str, float] = {}
        for tok in _tokenize(" ".join(tokens_set)):
            tf[tok] = tf.get(tok, 0) + 1
        total = sum(tf.values()) or 1
        vec = {tok: (cnt / total) * idf.get(tok, 1) for tok, cnt in tf.items()}
        # L2 normalise
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1
        vectors.append({tok: v / norm for tok, v in vec.items()})

    return {"type": "tfidf", "idf": idf, "vectors": vectors, "docs": docs}


def _tfidf_search(query: str, index: dict, top_k: int) -> list[tuple[int, float]]:
    """Return [(product_index, score)] sorted descending."""
    idf  = index["idf"]
    vecs = index["vectors"]
    tokens = _tokenize(query)
    if not tokens:
        return []
    # Query vector
    tf: dict[str, float] = {}
    for tok in tokens:
        tf[tok] = tf.get(tok, 0) + 1
    total = len(tokens)
    qvec = {tok: (cnt / total) * idf.get(tok, 1) for tok, cnt in tf.items()}
    norm = math.sqrt(sum(v * v for v in qvec.values())) or 1
    qvec = {tok: v / norm for tok, v in qvec.items()}

    scores: list[tuple[int, float]] = []
    for i, vec in enumerate(vecs):
        score = sum(qvec.get(tok, 0) * vec.get(tok, 0) for tok in qvec)
        if score > 0:
            scores.append((i, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def build_index(force: bool = False) -> int:
    """
    Build (or rebuild) the in-memory product search index.
    Returns the number of products indexed.
    Automatically called on first `rag_search` call.
    """
    global _index, _last_built

    now = time.monotonic()
    if not force and _index and (now - _last_built) < RAG_INDEX_TTL:
        return len(_index.get("products", []))

    try:
        from ..extensions import db
        from ..models.product import Product

        products = db.session.execute(
            db.select(Product)
            .where(Product.is_active.isnot(False))
            .order_by(Product.name)
        ).scalars().all()

        if not products:
            _index = {"products": [], "type": "empty"}
            _last_built = now
            return 0

        # Build text docs
        docs = [(p.id, _build_tfidf_doc(p)) for p in products]

        # Try dense embeddings first
        model = _try_load_dense_model()
        if model:
            try:
                texts = [doc for _, doc in docs]
                embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
                _index = {
                    "type": "dense",
                    "products": products,
                    "product_ids": [p.id for p in products],
                    "embeddings": embeddings,
                    "model": model,
                }
                _last_built = now
                logger.info("RAG index built: %d products (dense)", len(products))
                return len(products)
            except Exception as exc:
                logger.warning("RAG dense build failed (%s), falling back to TF-IDF", exc)

        # TF-IDF fallback
        tfidf = _build_tfidf_index(docs)
        _index = {
            "type": "tfidf",
            **tfidf,
            "products": products,
            "product_ids": [p.id for p in products],
        }
        _last_built = now
        logger.info("RAG index built: %d products (TF-IDF)", len(products))
        return len(products)

    except Exception as exc:
        logger.error("RAG build_index failed: %s", exc)
        return 0


def rag_search(
    query: str,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_MIN_SCORE,
    in_stock_only: bool = True,
) -> list[dict]:
    """
    Semantic product search.

    Returns list of dicts:
        {product_id, name, category, price, pack_size, description, score, in_stock}
    Sorted by score descending.
    """
    if not query or len(query.strip()) < 2:
        return []

    build_index()  # no-op if index is fresh

    if not _index or not _index.get("products"):
        return []

    products  = _index["products"]
    idx_type  = _index.get("type", "tfidf")

    results: list[tuple[int, float]] = []  # (index_into_products, score)

    if idx_type == "dense":
        try:
            model      = _index["model"]
            embeddings = _index["embeddings"]
            q_emb      = model.encode([query], show_progress_bar=False)[0]
            scores = [
                (i, float(_cosine(q_emb.tolist(), emb.tolist())))
                for i, emb in enumerate(embeddings)
            ]
            scores.sort(key=lambda x: x[1], reverse=True)
            results = scores[:top_k * 2]
        except Exception as exc:
            logger.warning("RAG dense search failed (%s), falling back to TF-IDF", exc)

    if not results and idx_type in ("tfidf", "dense"):
        results = _tfidf_search(query, _index, top_k * 2)

    output: list[dict] = []
    for idx, score in results:
        if score < min_score:
            continue
        if idx >= len(products):
            continue
        p = products[idx]
        if in_stock_only and p.quantity <= 0:
            continue
        output.append({
            "product_id":  p.id,
            "name":        p.name,
            "category":    p.category or "",
            "price":       float(p.selling_price),
            "pack_size":   p.pack_size or "",
            "description": (p.description or "")[:200],
            "score":       round(score, 4),
            "in_stock":    p.quantity > 0,
            "quantity":    p.quantity,
            "slug":        p.slug or "",
            "image":       p.image_filename or "",
        })
        if len(output) >= top_k:
            break

    return output


def rag_context_for_query(query: str, top_k: int = 5) -> str:
    """
    Build a compact context string for inclusion in Claude's system prompt.
    Retrieves the most semantically relevant products.
    """
    results = rag_search(query, top_k=top_k, in_stock_only=False)
    if not results:
        return "No matching products found in catalogue."

    lines = ["Relevant products from catalogue:"]
    for r in results:
        stock_str = f"Qty:{r['quantity']}" if r["in_stock"] else "OUT OF STOCK"
        size = f" | {r['pack_size']}" if r["pack_size"] else ""
        desc = f" | {r['description'][:80]}" if r["description"] else ""
        lines.append(
            f"- {r['name']} ({r['category']}){size} | NPR {r['price']:.0f} | {stock_str}{desc}"
        )
    return "\n".join(lines)


def invalidate_index() -> None:
    """Force a full index rebuild on next search (call after bulk product updates)."""
    global _last_built
    _last_built = 0.0
    logger.info("RAG index invalidated — will rebuild on next search")


def index_stats() -> dict:
    """Return basic stats about the current index state."""
    build_index()
    return {
        "type":        _index.get("type", "empty"),
        "product_count": len(_index.get("products", [])),
        "last_built":  _last_built,
        "age_seconds": round(time.monotonic() - _last_built, 1) if _last_built else None,
        "ttl_seconds": RAG_INDEX_TTL,
    }
