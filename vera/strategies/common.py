"""Shared evidence helpers for strategies. Each returns grounded phrases and
records provenance via ctx.fact(); none of them invent values."""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Sequence, Tuple

from ..ctx import Ctx
from ..formatting import as_float, fmt_date, humanize_key, num, pct
from ..retrieval import tokens

METRIC_WORDS = {"calls": "calls", "views": "views", "ctr": "CTR", "leads": "leads",
                "directions": "direction requests", "review_count": "reviews", "orders": "orders"}
PEER_KEYS = {"calls": "avg_calls_30d", "views": "avg_views_30d", "ctr": "avg_ctr",
             "directions": "avg_directions_30d"}


def pick(seed: str, options: Sequence[str]) -> str:
    """Deterministic variation: same inputs -> same phrasing; different
    merchants/triggers -> different phrasings."""
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def seed(ctx: Ctx, salt: str = "") -> str:
    return f"{ctx.merchant.id}|{ctx.kind}|{ctx.trigger.get('id', '')}|{salt}"


def metric_word(m: str) -> str:
    return METRIC_WORDS.get(m, humanize_key(m))


def lcfirst(s: str) -> str:
    if len(s) > 1 and s[0].isupper() and not s[1].isupper():
        return s[0].lower() + s[1:]
    return s


def humanize_iso_dates(text: str, year: bool = True) -> str:
    """'effective 2026-12-15' -> 'effective 15 Dec 2026'."""
    return re.sub(r"\b(20\d\d-\d\d-\d\d)\b", lambda m: fmt_date(m.group(1), year=year), text or "")


def ctr_vs_peer(ctx: Ctx) -> Optional[str]:
    m, p = ctx.merchant.metric("ctr"), ctx.category.peer_ctr()
    if m is None or p is None:
        return None
    return ctx.fact(f"{pct(m)} vs {pct(p)} for {peer_scope(ctx)}",
                    ["merchant.performance.ctr", "category.peer_stats.avg_ctr"])


def peer_scope(ctx: Ctx) -> str:
    scope = str(ctx.category.peer.get("scope") or "")
    scope = re.sub(r"_?20\d\d$", "", scope)
    noun = {"dentists": "clinics", "salons": "salons", "restaurants": "restaurants", "gyms": "gyms",
            "pharmacies": "pharmacies"}.get(ctx.category.slug, ctx.category.display_name.lower() or "businesses")
    return f"similar {noun} in metros" if "metro" in scope else f"similar {noun}"


def peer_gap(ctx: Ctx, metric: str) -> Optional[Tuple[float, float, float]]:
    mv = ctx.merchant.metric(metric)
    pv = as_float(ctx.category.peer.get(PEER_KEYS.get(metric, ""), None))
    if mv is None or not pv:
        return None
    return mv, pv, (mv / pv) - 1.0


def active_offer(ctx: Ctx, *keywords: str) -> Optional[str]:
    titles = ctx.merchant.active_offer_titles()
    if keywords:
        for t in titles:
            tl = t.lower()
            if any(k.lower() in tl for k in keywords if k):
                return t
        return None
    # Prefer service @ price over discounts (brief §3 pain point 3).
    titles.sort(key=lambda t: (0 if "₹" in t and "%" not in t else 1, len(t)))
    return titles[0] if titles else None


def suggested_catalog_offer(ctx: Ctx) -> Optional[str]:
    """A catalog offer that fits this merchant, for *suggesting* (never claimed
    as already live). Prefers service@price whose words match the merchant's
    name/offers/reviews (a pizza place gets the pizza offer)."""
    cat = [o for o in ctx.category.catalog if o.get("title")]
    if not cat:
        return None
    mtoks = set(tokens(ctx.merchant.name, [t.get("theme") for t in ctx.merchant.review_themes],
                       ctx.merchant.active_offer_titles()))

    def score(i_o) -> Tuple[int, int, int]:
        i, o = i_o
        overlap = len(set(tokens(o.get("title"))) & mtoks)
        is_price = 1 if o.get("type") == "service_at_price" else 0
        if str(o.get("audience", "")) == "repeat_user":
            is_price -= 2
        return (overlap, is_price, -i)   # catalog order = curator's priority

    ranked = sorted(enumerate(cat), key=score, reverse=True)
    item_words = {"pizza", "thali", "brunch", "combo", "starter", "cake", "biryani", "pediatric", "bridal", "keratin",
                  "root", "aligner", "whitening", "glucometer", "yoga", "couple"}
    for i, o in ranked:
        otoks = set(tokens(o.get("title")))
        if otoks & item_words and not otoks & mtoks:
            continue          # names a specific dish/service this merchant shows no sign of
        if o.get("type") == "percentage_discount":
            continue          # service+price or add-ons beat "X% off" (brief pain point 3)
        return str(o["title"])
    return str(ranked[0][1]["title"])


def offer_kind_phrase(ctx: Ctx, title: str) -> str:
    o = next((o for o in ctx.category.catalog if o.get("title") == title), {})
    return {"service_at_price": "a service-and-price offer", "free_addon": "a simple add-on offer",
            "free_service": "a free-service offer", "free_trial": "a free-trial offer",
            "membership": "a membership offer"}.get(str(o.get("type")), "an offer")


def lapsed(ctx: Ctx) -> Optional[Tuple[int, str]]:
    for k, v in ctx.merchant.agg.items():
        if k.startswith("lapsed") and as_float(v):
            m = re.search(r"(\d+)d", k)
            label = f"{m.group(1)}+ days" if m else "a while"
            ctx.fact(f"{num(v)} lapsed {ctx.policy.people}", f"merchant.customer_aggregate.{k}")
            return int(as_float(v)), label
    return None


def cohort(ctx: Ctx, *hint_texts: object) -> Optional[Tuple[int, str]]:
    """Find a customer_aggregate count whose name matches the hint (e.g. a
    digest item's patient_segment 'high_risk_adults' -> high_risk_adult_count)."""
    want = set(tokens(*hint_texts)) - {"count", "patient", "customer", "total"}
    best, best_s = None, 0
    for k, v in ctx.merchant.agg.items():
        if as_float(v) is None or as_float(v) < 1:
            continue
        ktoks = set(tokens(k)) - {"count", "total", "pct"}
        s = len(want & ktoks)
        if s > best_s and "pct" not in k:
            best, best_s = (k, v), s
    if not best or best_s < 1:
        return None
    k, v = best
    label = humanize_key(re.sub(r"_(count|total)$", "", k))
    ctx.fact(f"{num(v)} {label}", f"merchant.customer_aggregate.{k}")
    return int(as_float(v)), label


def member_base(ctx: Ctx) -> Optional[Tuple[int, str]]:
    agg = ctx.merchant.agg
    for key, label in (("total_active_members", "active members"), ("chronic_rx_count", "chronic-Rx customers"),
                       ("total_unique_ytd", "customers this year")):
        v = as_float(agg.get(key))
        if v:
            ctx.fact(f"{num(v)} {label}", f"merchant.customer_aggregate.{key}")
            return int(v), label
    return None


def repeat_share(ctx: Ctx) -> Optional[str]:
    v = as_float(ctx.merchant.agg.get("repeat_customer_pct"))
    return ctx.fact(pct(v), "merchant.customer_aggregate.repeat_customer_pct") if v else None


def review_quote(theme: Optional[dict]) -> Optional[str]:
    if not theme:
        return None
    q = str(theme.get("common_quote") or "").strip()
    return q or None


def history_commitment(ctx: Ctx) -> Optional[str]:
    """What the merchant last asked Vera for, if still open (thread continuity)."""
    pend = ctx.merchant.pending_merchant_intent()
    if not pend:
        return None
    mturn, _ = pend
    body = str(mturn.get("body", "")).strip()
    return body or None


def trend_phrase(ctx: Ctx, t: Optional[dict]) -> Optional[str]:
    if not t or t.get("delta_yoy") is None:
        return None
    return ctx.fact(f"'{t.get('query')}' searches are up {pct(t['delta_yoy'])} YoY",
                    "category.trend_signals")


def offer_price(title: str) -> Optional[str]:
    m = re.search(r"₹\s?[\d,]+", title or "")
    return m.group().replace(" ", "") if m else None


def offer_service(title: str) -> str:
    return re.split(r"\s*@\s*|\s*\(", title or "")[0].strip()


def cta_draft(ctx: Ctx, thing: str, salt: str = "") -> str:
    return pick(seed(ctx, "cta" + salt), [
        f"Want me to draft {thing}?",
        f"Shall I draft {thing}?",
    ])


def open_request_topic(ctx: Ctx) -> Optional[str]:
    """'Yes please, focus on whitening and aligners' (after Vera offered posts)
    -> 'whitening and aligner posts you asked for'."""
    pend = ctx.merchant.pending_merchant_intent()
    if not pend:
        return None
    mturn, vturn = pend
    m = str(mturn.get("body", "")).lower()
    v = str((vturn or {}).get("body", "")).lower()
    when = fmt_date(mturn.get("ts")) if mturn.get("ts") else ""
    verb = ("you asked about" if "?" in m or "what" in m else "you asked for") + (f" on {when}" if when else "")
    focus = re.search(r"focus on ([a-z ,&]+)", m)
    if "post" in v:
        f = focus.group(1).strip() if focus else ""
        f = re.sub(r"s\b", "", f) if f else ""
        return f"{f + ' ' if f else ''}posts {verb}".strip()
    add = re.search(r"add (?:a|an) ([a-z\- ]+?)(?: version)?\?", v)
    if add:
        return f"{add.group(1).strip()} plan {verb}"
    if "list" in v:
        return f"customer list {verb}"
    return None


def ignored_note_overlap(ctx: Ctx, draft_text: str) -> Optional[str]:
    last = ctx.merchant.last_from("vera")
    if not last or not ctx.merchant.ignored_last_vera():
        return None
    body = str(last.get("body", ""))
    generic = {"want", "would", "google", "listing", "there", "which", "about", "check", "quick", "spotted", "draft",
               "week", "weeks", "push", "extra"}
    own = set(re.findall(r"[a-z]{5,}", f"{ctx.merchant.name} {ctx.merchant.owner} {ctx.merchant.locality}".lower()))
    words = set(re.findall(r"[a-z]{5,}", body.lower())) - generic - own
    if len(words & set(re.findall(r"[a-z]{5,}", draft_text))) < 2:
        return None
    core = re.sub(r"^(spotted|quick check|heads[- ]up|fyi|update)\s*[:—-]\s*", "", body.strip(), flags=re.I)
    clause = re.split(r"(?<=[.?])\s", core)[0].strip()
    if not clause or clause.endswith("?"):
        return None
    subject = re.split(r"\s+(?:in|at|on|for|are|is|were|up|down)\s+|\s*[+\-]?\d", clause)[0].strip(" ,")
    if len(subject.split()) < 2 or len(subject) > 40:
        return None
    return f"(Same trend as the {lcfirst(subject)} I flagged on {fmt_date(last.get('ts'))}.)"


_THEME_WORDS = {"stylist skill": "stylists", "doctor manner": "doctors' manner", "thali quality": "thali",
                "pizza quality": "pizza", "instructor quality": "instructors", "equipment quality": "equipment"}


def theme_phrase(theme: str) -> str:
    """'delivery_late' -> 'late delivery'; 'wait_time' -> 'wait time'."""
    h = humanize_key(str(theme or ""))
    m = re.match(r"(\w+) (late|slow|delay)$", h)
    if m:
        return f"{m.group(2)} {m.group(1)}"
    return _THEME_WORDS.get(h, h)


def beat_phrase(beat: dict) -> str:
    """'Oct-Dec' + 'primary wedding season — bridal bookings 4x baseline'
    -> 'around Oct-Dec, bridal bookings 4x baseline' (uses the stat half)."""
    note = str(beat.get("note", "")).strip()
    rng = str(beat.get("month_range", "")).strip()
    if " — " in note:
        subj, stat = (x.strip().rstrip(".") for x in note.split(" — ", 1))
        return f"{rng} brings the {lcfirst(subj)} ({stat})"
    return f"{rng} brings the {lcfirst(note).rstrip('.')}"


def cite(source) -> str:
    """Only cite sources a merchant could look up (named + dated)."""
    s = str(source or "").strip()
    return f" ({s})" if s and re.search(r"\d", s) else ""


def service_terms(ctx: Ctx) -> List[str]:
    """What this category actually sells: catalog service names, plus allowed
    vocabulary that also appears in a catalog title or a trend query. Review
    themes ('doctor manner', 'wait time') are never services."""
    titles = [str(o.get("title", "")).lower() for o in ctx.category.catalog]
    queries = [str(t.get("query", "")).lower() for t in ctx.category.trends]
    terms = set()
    for t in titles:
        name = re.split(r"\s*@\s*|\s*\(|:", t)[0].strip()
        name = re.sub(r"^(free|annual|first)\s+", "", name)
        if name and not re.match(r"^(flat|buy|\d)", name):
            terms.add(name)
    for v in ctx.category.vocab_allowed:
        vl = v.lower()
        if any(vl in t for t in titles) or any(vl.rstrip("s") in q for q in queries):
            terms.add(vl)
    return sorted(terms, key=len, reverse=True)


def find_service(ctx: Ctx, text: str) -> Optional[str]:
    low = (text or "").lower()
    for term in service_terms(ctx):
        stem = term.rstrip("s")
        if re.search(r"(?<![a-z])" + re.escape(stem), low):
            return term
    return None


def strongest_fact(ctx: Ctx) -> Optional[str]:
    """One real, merchant-specific sentence to anchor a message that has none.
    Priority: praised review theme > positive momentum > live offer > customer base."""
    M = ctx.merchant
    pos = M.top_theme("pos")
    if pos and (as_float(pos.get("occurrences_30d")) or 0) >= 3:
        q = review_quote(pos)
        n = num(pos.get("occurrences_30d"))
        return (f"{n} reviews this month praise your {theme_phrase(pos.get('theme'))}" + (f" (\"{q}\")" if q else "") + ".")
    ups = [(k, M.delta(k)) for k in ("calls", "views") if (M.delta(k) or 0) >= 0.1]
    if ups:
        k, d = max(ups, key=lambda x: x[1])
        return f"Your {metric_word(k)} are up {pct(d)} week-on-week, so the demand is there."
    offer = active_offer(ctx)
    if offer:
        return f"Your {offer} is already live to build on."
    total = as_float(M.agg.get("total_unique_ytd"))
    if total:
        return f"You've served {num(total)} customers this year — that's the base to build on."
    return None
