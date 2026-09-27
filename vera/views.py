"""Normalized, never-raising views over raw context dicts.

The judge can push partial or unfamiliar payloads (and uses both `taboos` and
`vocab_taboo` across documents). Everything downstream reads through these
views so a missing field degrades a message instead of crashing a request.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .formatting import as_float
from .untrusted import clean, clean_name, is_instruction_like


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


class CategoryView:
    def __init__(self, raw: Optional[dict]):
        self.raw = _d(raw)
        self.slug: str = str(self.raw.get("slug") or "")
        v = _d(self.raw.get("voice"))
        self.tone: str = str(v.get("tone") or "")
        self.register: str = str(v.get("register") or "")
        self.code_mix: str = str(v.get("code_mix") or "")
        self.vocab_allowed: List[str] = [str(x) for x in _l(v.get("vocab_allowed"))]
        taboo = _l(v.get("vocab_taboo")) + _l(v.get("taboos"))
        # "FDA-approved (use only when actually applicable)" -> "FDA-approved"
        self.taboos: List[str] = sorted({re.sub(r"\s*\(.*?\)\s*", "", str(t)).strip() for t in taboo if t})
        self.peer: Dict[str, Any] = _d(self.raw.get("peer_stats"))
        self.digest: List[dict] = []
        for x in _l(self.raw.get("digest")):
            if not isinstance(x, dict):
                continue
            x = {**x, **{k: clean(str(x[k])) for k in ("title", "summary", "actionable", "source") if x.get(k)}}
            if x.get("title") or x.get("summary"):
                self.digest.append(x)
        self.catalog: List[dict] = [x for x in _l(self.raw.get("offer_catalog")) if isinstance(x, dict)]
        self.seasonal: List[dict] = [x for x in _l(self.raw.get("seasonal_beats")) if isinstance(x, dict)]
        self.trends: List[dict] = [x for x in _l(self.raw.get("trend_signals")) if isinstance(x, dict)]
        self.content: List[dict] = [x for x in _l(self.raw.get("patient_content_library")) if isinstance(x, dict)]
        self.display_name: str = str(self.raw.get("display_name") or self.slug)

    def digest_item(self, item_id: Optional[str]) -> Optional[dict]:
        if not item_id:
            return None
        for it in self.digest:
            if it.get("id") == item_id:
                return it
        return None

    def peer_ctr(self) -> Optional[float]:
        return as_float(self.peer.get("avg_ctr"))

    def catalog_offer(self, *keywords: str) -> Optional[dict]:
        for o in self.catalog:
            title = str(o.get("title", "")).lower()
            if all(k.lower() in title for k in keywords):
                return o
        return None


class MerchantView:
    def __init__(self, raw: Optional[dict]):
        self.raw = _d(raw)
        self.id: str = str(self.raw.get("merchant_id") or "")
        self.category_slug: str = str(self.raw.get("category_slug") or "")
        ident = _d(self.raw.get("identity"))
        self.name: str = clean_name(str(ident.get("name") or "").strip())
        self.owner: str = clean_name(str(ident.get("owner_first_name") or "").strip())
        self.city: str = str(ident.get("city") or "")
        self.locality: str = str(ident.get("locality") or "")
        self.verified = ident.get("verified")
        self.languages: List[str] = [str(x).lower() for x in _l(ident.get("languages"))] or ["en"]
        self.sub: dict = _d(self.raw.get("subscription"))
        self.perf: dict = _d(self.raw.get("performance"))
        self.delta7: dict = _d(self.perf.get("delta_7d"))
        self.offers: List[dict] = [o for o in _l(self.raw.get("offers"))
                                   if isinstance(o, dict) and not is_instruction_like(str(o.get("title", "")))]
        self.history: List[dict] = [{**h, "body": clean(str(h.get("body", "")))} for h in _l(self.raw.get("conversation_history"))
                                    if isinstance(h, dict)]
        self.agg: dict = _d(self.raw.get("customer_aggregate"))
        self.signals_raw: List[str] = [str(s) for s in _l(self.raw.get("signals"))]
        self.review_themes: List[dict] = [{**r, "common_quote": clean(str(r["common_quote"]))} if r.get("common_quote") else r
                                          for r in _l(self.raw.get("review_themes")) if isinstance(r, dict)]

    # -- identity -----------------------------------------------------------
    @property
    def owner_bare(self) -> str:
        return re.sub(r"^(dr\.?\s+)", "", self.owner, flags=re.I).strip()

    def salutation(self, category_slug: str = "") -> str:
        slug = category_slug or self.category_slug
        if slug == "dentists" or self.owner.lower().startswith("dr"):
            return f"Dr. {self.owner_bare}" if self.owner_bare else "Doctor"
        return self.owner_bare or f"{self.name} team" if self.name else "there"

    @property
    def place(self) -> str:
        return self.locality or self.city

    # -- offers ---------------------------------------------------------------
    @property
    def active_offers(self) -> List[dict]:
        return [o for o in self.offers if str(o.get("status", "")).lower() == "active"]

    @property
    def inactive_offers(self) -> List[dict]:
        return [o for o in self.offers if str(o.get("status", "")).lower() in ("expired", "paused", "inactive")]

    def active_offer_titles(self) -> List[str]:
        return [str(o.get("title")) for o in self.active_offers if o.get("title")]

    # -- performance ----------------------------------------------------------
    def metric(self, name: str) -> Optional[float]:
        return as_float(self.perf.get(name))

    def delta(self, name: str) -> Optional[float]:
        return as_float(self.delta7.get(f"{name}_pct", self.delta7.get(name)))

    # -- signals --------------------------------------------------------------
    @property
    def signals(self) -> Dict[str, Optional[str]]:
        out: Dict[str, Optional[str]] = {}
        for s in self.signals_raw:
            k, _, v = s.partition(":")
            out[k.strip()] = v.strip() or None
        return out

    def has_signal(self, *prefixes: str) -> bool:
        return any(k.startswith(p) for k in self.signals for p in prefixes)

    # -- conversation history ---------------------------------------------------
    def last_from(self, who: str) -> Optional[dict]:
        for h in reversed(self.history):
            if str(h.get("from", "")).lower() == who:
                return h
        return None

    def pending_merchant_intent(self) -> Optional[Tuple[dict, Optional[dict]]]:
        """Last merchant turn tagged with an action/planning intent that Vera
        has not yet fulfilled, plus the Vera turn it answered (if any)."""
        for i in range(len(self.history) - 1, -1, -1):
            h = self.history[i]
            if str(h.get("from", "")).lower() == "merchant":
                tag = str(h.get("engagement", "")).lower()
                if tag.startswith("intent") or re.search(r"\b(yes|please|go ahead|want)\b", str(h.get("body", "")).lower()):
                    prev = self.history[i - 1] if i > 0 else None
                    return h, prev
                return None
        return None

    def recent_vera_bodies(self, ignored_only: bool = False) -> List[str]:
        out = []
        for h in self.history:
            if str(h.get("from", "")).lower() != "vera":
                continue
            if ignored_only and str(h.get("engagement", "")).lower() not in ("merchant_no_reply", "ignored"):
                continue
            out.append(str(h.get("body", "")))
        return out

    def ignored_last_vera(self) -> bool:
        last = self.last_from("vera")
        return bool(last and str(last.get("engagement", "")).lower() in ("merchant_no_reply", "ignored"))

    # -- reviews ----------------------------------------------------------------
    def top_theme(self, sentiment: str) -> Optional[dict]:
        themes = [t for t in self.review_themes if str(t.get("sentiment", "")).lower().startswith(sentiment)]
        themes.sort(key=lambda t: -(as_float(t.get("occurrences_30d")) or 0))
        return themes[0] if themes else None

    def theme(self, name: str) -> Optional[dict]:
        for t in self.review_themes:
            if t.get("theme") == name:
                return t
        return None


class CustomerView:
    def __init__(self, raw: Optional[dict]):
        self.raw = _d(raw)
        self.id: str = str(self.raw.get("customer_id") or "")
        self.merchant_id: str = str(self.raw.get("merchant_id") or "")
        ident = _d(self.raw.get("identity"))
        self.name_raw: str = clean_name(str(ident.get("name") or "").strip())
        self.language_pref: str = str(ident.get("language_pref") or "en").lower()
        self.age_band: str = str(ident.get("age_band") or "")
        self.senior: bool = bool(ident.get("senior_citizen")) or self.age_band.startswith(("60", "65", "70"))
        self.rel: dict = _d(self.raw.get("relationship"))
        self.state: str = str(self.raw.get("state") or "").lower()
        self.prefs: dict = _d(self.raw.get("preferences"))
        self.consent: dict = _d(self.raw.get("consent"))

    @property
    def is_anonymous(self) -> bool:
        return not self.name_raw or self.name_raw.startswith("(")

    @property
    def first_name(self) -> str:
        """'Aanya (parent: Sneha)' -> 'Aanya'; 'Mr. Sharma' -> 'Mr. Sharma'."""
        return re.sub(r"\s*\(.*?\)\s*", "", self.name_raw).strip()

    @property
    def guardian(self) -> Optional[str]:
        m = re.search(r"\((?:parent|guardian|son|daughter)\s*:\s*([^)]+)\)", self.name_raw, flags=re.I)
        return m.group(1).strip() if m else None

    @property
    def via_relative(self) -> Optional[str]:
        ch = str(self.prefs.get("channel", "")).lower()
        m = re.search(r"via_(\w+)", ch)
        return m.group(1) if m else None

    @property
    def consent_scope(self) -> List[str]:
        return [str(s).lower() for s in _l(self.consent.get("scope"))]

    @property
    def slot_pref(self) -> str:
        return str(self.prefs.get("preferred_slots") or "")
