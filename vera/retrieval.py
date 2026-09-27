"""Deterministic lexical retrieval over category knowledge (digest items,
seasonal beats, trend signals). Reproducible, fast, and it always returns the
item with its source citation intact — no embeddings needed at this scale."""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from .views import CategoryView, MerchantView

_STOP = {"the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "with", "your", "is", "are",
         "at", "by", "vs", "from", "this", "that", "per", "new", "now", "2026", "category"}


def tokens(*texts: object) -> List[str]:
    out: List[str] = []
    for t in texts:
        if t is None:
            continue
        if isinstance(t, dict):
            out += tokens(*t.values())
            continue
        if isinstance(t, (list, tuple)):
            out += tokens(*t)
            continue
        for w in re.findall(r"[a-z0-9]+", str(t).lower().replace("_", " ")):
            if w not in _STOP and len(w) > 1:
                out.append(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _score(item_text: Sequence[str], query: Sequence[str]) -> float:
    if not item_text or not query:
        return 0.0
    q = set(query)
    return sum(1.0 for w in set(item_text) if w in q)


def best_digest(category: CategoryView, query: Iterable[object],
                kinds: Optional[Sequence[str]] = None, merchant: Optional[MerchantView] = None,
                min_score: float = 1.0) -> Optional[dict]:
    q = tokens(*list(query))
    if merchant is not None:
        q += tokens(merchant.signals_raw, list(merchant.agg.keys()))
    best, best_s = None, 0.0
    for it in category.digest:
        if kinds and it.get("kind") not in kinds:
            continue
        s = _score(tokens(it.get("title"), it.get("summary"), it.get("patient_segment"), it.get("kind")), q)
        if s > best_s:
            best, best_s = it, s
    return best if best_s >= min_score else None


def matching_seasonal(category: CategoryView, query: Iterable[object]) -> Optional[dict]:
    q = tokens(*list(query))
    best, best_s = None, 0.0
    for b in category.seasonal:
        s = _score(tokens(b.get("note"), b.get("month_range")), q)
        if s > best_s:
            best, best_s = b, s
    return best if best_s >= 1 else None


def month_seasonal(category: CategoryView, month: int) -> List[dict]:
    """Seasonal beats whose month_range covers the given month (1-12)."""
    names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    out = []
    for b in category.seasonal:
        rng = str(b.get("month_range", "")).lower()
        parts = re.findall(r"[a-z]{3}", rng)
        idx = [names.index(p) + 1 for p in parts if p in names]
        if not idx:
            continue
        lo, hi = idx[0], idx[-1]
        inside = lo <= month <= hi if lo <= hi else (month >= lo or month <= hi)
        if inside:
            out.append(b)
    return out


def top_trend(category: CategoryView, query: Iterable[object] = ()) -> Optional[dict]:
    q = tokens(*list(query))
    trends = list(category.trends)
    if not trends:
        return None
    if q:
        scored = sorted(trends, key=lambda t: (-_score(tokens(t.get("query")), q), -(t.get("delta_yoy") or 0)))
        if _score(tokens(scored[0].get("query")), q) > 0:
            return scored[0]
    return max(trends, key=lambda t: t.get("delta_yoy") or 0)
