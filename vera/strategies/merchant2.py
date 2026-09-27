"""Merchant-facing strategies, part 2: events, competition, reputation,
account health, dormancy, curiosity, planning, and unseen kinds."""
from __future__ import annotations

import re
from typing import List, Optional

from ..ctx import Ctx, Draft
from ..formatting import (ucfirst, as_float, first_sentence, fmt_date, fmt_time, humanize_key, inr, join_human, num,
                          parse_dt, pct, WEEKDAYS)
from ..retrieval import best_digest, matching_seasonal, top_trend, tokens
from .common import (find_service, beat_phrase, cite, theme_phrase, active_offer, cta_draft, ctr_vs_peer, lapsed, lcfirst, member_base, metric_word,
                     offer_price, offer_service, peer_gap, peer_scope, pick, repeat_share, review_quote, seed,
                     suggested_catalog_offer, trend_phrase)
from .merchant import _dip_lever, _dip_metric

_PLACE_NOUN = {"dentists": "clinic", "salons": "salon", "restaurants": "restaurant", "gyms": "gym",
               "pharmacies": "pharmacy"}


def _noun(ctx: Ctx) -> str:
    return _PLACE_NOUN.get(ctx.category.slug, "business")


# ---------------------------------------------------------------- events
def event(ctx: Ctx) -> Draft:
    k, tp = ctx.kind, ctx.tp
    if "ipl" in k or "match" in k or tp.get("match"):
        return match_day(ctx)
    if "festival" in k or tp.get("festival"):
        return festival(ctx)
    if k == "category_seasonal" or tp.get("trends"):
        return seasonal_shift(ctx)
    return generic_event(ctx)


def _pct_in(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text or "", flags=re.I)
    return m.group(1) if m else None


def match_day(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    match, venue = tp.get("match") or "Tonight's match", tp.get("venue")
    t_iso = tp.get("match_time_iso")
    when = fmt_time(t_iso)
    dt = parse_dt(t_iso)
    weeknight = tp.get("is_weeknight")
    if weeknight is None and dt:
        weeknight = dt.weekday() < 4
    day = WEEKDAYS[dt.weekday()] if dt else ""
    d = best_digest(C, ["ipl", "match", "saturday", "weeknight", "covers"], kinds=("seasonal", "trend"))
    summary = str(d.get("summary", "")) if d else ""
    down = _pct_in(summary, r"down (\d+%)")
    up = _pct_in(summary, r"\+(\d+%)")
    src = d.get("source") if d else None
    where = ", ".join(p for p in (when, venue) if p)
    beats: List[str] = []
    offer = active_offer(ctx)
    if weeknight is False:
        full_day = {"Sat": "Saturday", "Sun": "Sunday", "Fri": "Friday"}.get(day, "weekend")
        hook = f"{ctx.sal}, {match} is on tonight{f' ({where})' if where else ''} — but it's a {full_day} game."
        if down:
            which = "Saturday matches have been cutting" if full_day == "Saturday" else "Saturday games have cut"
            beats.append(f"Weekend IPL nights pull people home — {which} restaurant covers by {down}"
                         f"{f' ({src})' if src else ''} — so I'd skip a dine-in match promo tonight.")
        weekday_offer = active_offer(ctx, "tue", "wed", "thu", "weekday")
        if weekday_offer and up:
            beats.append(f"Your {weekday_offer} fits the weeknight matches instead — those lift covers {up}.")
        dl, di = as_float(M.agg.get("delivery_orders_30d")), as_float(M.agg.get("dine_in_orders_30d"))
        if dl and di:
            tot = int(dl + di)
            ctx.fact(f"{num(dl)} of {num(tot)} orders", ["merchant.customer_aggregate.delivery_orders_30d",
                                                         "merchant.customer_aggregate.dine_in_orders_30d"], derived=True, value=tot)
            late = M.theme("delivery_late")
            caution = (f" Keep the delivery promise realistic though — {num(late.get('occurrences_30d'))} reviews this month flag late delivery."
                       if late and (as_float(late.get("occurrences_30d")) or 0) >= 2 else "")
            beats.append(f"Tonight belongs to delivery — it's already {num(dl)} of your last {num(tot)} orders.{caution}")
        return Draft(genre="contrarian_event", why_now=f"{match} today ({full_day})", hook=hook, beats=beats,
                     ask="Want me to draft a delivery-only match-night post for this evening?",
                     ask_hi="Aaj shaam ke liye delivery-only match-night post draft kar doon?", cta="binary_yes_no",
                     action="draft delivery-only match-night post", artifact="post",
                     anchor="weekend match hurts dine-in; merchant's offer is weekday-only; delivery-heavy order mix",
                     expected_action="push a match-night dine-in promo", adjusted_action="delivery push; save offer for weeknight matches",
                     template="vera_event_v1", extra={"topic": f"{match} match night", "channel": "delivery", "offer": None})
    hook = f"{ctx.sal}, {match} tonight{f' ({where})' if where else ''} — a weeknight game" + (f", and those have been lifting covers {up}{f' ({src})' if src else ''}." if up else ".")
    combo = active_offer(ctx, "match", "combo") or offer
    if combo:
        beats.append(f"Your {combo} is the hook — put it in front of people before the toss.")
        ask = "Want me to put a match-night post live this afternoon?"
    else:
        sug = C.catalog_offer("match")
        sug_t = sug["title"] if sug else suggested_catalog_offer(ctx)
        beats.append(f"You don't have a match-night offer live; a '{sug_t}' is the usual format." if sug_t else "")
        ask = "Want me to set it up with a post for this afternoon?"
    return Draft(genre="event_push", why_now=f"{match} today (weeknight)", hook=hook, beats=[b for b in beats if b], ask=ask,
                 ask_hi="Aaj dopahar tak match-night post live kar doon?", cta="binary_yes_no", action="draft a match-night post",
                 artifact="post", anchor="weeknight matches lift covers", template="vera_event_v1",
                 extra={"topic": f"{match} match night", "offer": combo})


def festival(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    fest, date = tp.get("festival"), tp.get("date")
    if date and parse_dt(date) is None:
        date = None                      # never echo an unparseable date
    days = as_float(tp.get("days_until"))
    if days is None and date:
        days = ctx.days_until(date)
    if fest and days is not None and days < 0:
        return Draft(genre="event_past", why_now=f"{fest} already passed",
                     hook=f"{ctx.sal}, {fest} has already gone by{f' ({fmt_date(date)})' if date else ''}, so there's nothing to push for it now.",
                     beats=[], ask=f"Want me to note it for next year's plan instead?", cta="binary_yes_no",
                     action=f"note {fest} for next year", artifact="post", anchor="event already passed — no send",
                     template="vera_festival_v1", extra={"blocked": True, "block_reason": "event already passed"})
    beat_f = matching_seasonal(C, [fest or "", "festival", "festive", "wedding"])
    offer = active_offer(ctx)
    if fest and days is not None and days > 45:
        hook = f"{ctx.sal}, {fest} is on {fmt_date(date)} — {num(days)} days out, so a {fest} discount now would just be wasted margin."
        beats: List[str] = []
        now_item = best_digest(C, ["window", "season", "opener", "now", "missed"], kinds=("seasonal",))
        if now_item and not re.search(fest, str(now_item.get("title")), flags=re.I):
            lead = lcfirst(first_sentence(str(now_item.get('summary', '')))).rstrip(".")
            beats.append(f"The window that's actually open now is the {lead}{cite(now_item.get('source'))}.")
            cat_offer = next((o["title"] for o in C.catalog if str(o.get("title")) in str(now_item.get("actionable", ""))), None)
            if cat_offer and cat_offer not in M.active_offer_titles():
                beats.append(f"I'll bring {fest} back closer to the date.")
                return Draft(genre="contrarian_redirect", why_now=f"{fest} in {num(days)} days", hook=hook, beats=beats,
                             ask=f"Want me to set up a '{cat_offer}' offer with a Google post this week?",
                             ask_hi=f"'{cat_offer}' is hafte live kar doon?",
                             cta="binary_yes_no", action=f"set up {cat_offer} + post", artifact="offer_setup",
                             anchor="festival too far out; a seasonal window is open now",
                             expected_action=f"run a {fest} promo now", adjusted_action="capture the window open today; schedule festival later",
                             template="vera_festival_v1", extra={"offer": cat_offer, "topic": str(now_item.get("title"))})
        elif beat_f:
            beats.append(f"The part worth planning for: {beat_phrase(beat_f)}.")
        return Draft(genre="contrarian_redirect", why_now=f"{fest} in {num(days)} days", hook=hook, beats=beats,
                     ask=f"Want me to line up the {fest} campaign now, ready to go live 3 weeks before?",
                     ask_hi=f"{fest} campaign abhi se ready kar doon, 3 hafte pehle live karenge?",
                     cta="binary_yes_no", action=f"prepare {fest} campaign", artifact="post",
                     anchor="festival far out; plan instead of promo", expected_action=f"run a {fest} promo now",
                     adjusted_action="plan now, launch closer to the date", template="vera_festival_v1",
                     extra={"offer": offer, "topic": fest})
    if fest:
        if days == 0:
            hook = f"{ctx.sal}, {fest} is today."
        elif days == 1:
            hook = f"{ctx.sal}, {fest} is tomorrow{f' ({fmt_date(date, weekday=True)})' if date else ''}."
        else:
            when = f"on {fmt_date(date, weekday=True)}" if date else "coming up"
            hook = f"{ctx.sal}, {fest} is {when}" + (f" — {num(days)} days away." if days is not None else ".")
        beats = []
        if beat_f:
            beats.append(f"For you, {beat_phrase(beat_f)}.")
        if offer:
            beats.append(f"Your {offer} is the natural festive hook.")
        else:
            sug = suggested_catalog_offer(ctx)
            if sug:
                beats.append(f"You don't have an offer live — '{sug}' is a clean festive hook.")
        return Draft(genre="event_push", why_now=f"{fest} in {num(days)} days" if days is not None else f"{fest} upcoming",
                     hook=hook, beats=beats, ask=f"Want me to draft the {fest} post and customer WhatsApp around it?",
                     ask_hi=f"{fest} ka post aur customer WhatsApp draft kar doon?", cta="binary_yes_no",
                     action=f"draft {fest} post + WhatsApp", artifact="post", anchor="festival timing + live offer",
                     template="vera_festival_v1", extra={"offer": offer, "topic": fest})
    # Placeholder festival trigger: no name/date. Anchor on the category's festive beat.
    if beat_f:
        hook = f"{ctx.sal}, festive season is on the way — {beat_phrase(beat_f)}."
    else:
        hook = f"{ctx.sal}, festive season is on the way — the one stretch when past {ctx.policy.people} come back on their own."
    beats = []
    total = as_float(M.agg.get("total_unique_ytd"))
    base = member_base(ctx)
    if total:
        ctx.fact(f"{num(total)} customers", "merchant.customer_aggregate.total_unique_ytd")
        beats.append(f"{M.name} has seen {num(total)} customers this year; the ones who haven't been in lately are the easiest to bring back.")
    elif base:
        beats.append(f"{M.name} has {num(base[0])} {base[1]} — the ones who've drifted are the easiest to bring back.")
    return Draft(genre="event_push", why_now="festive window approaching", hook=hook, beats=beats,
                 ask=f"Want me to draft a 'get festival-ready' WhatsApp for them?",
                 ask_hi="Purane members ke liye 'festivals se pehle wapas aao' WhatsApp draft kar doon?",
                 cta="binary_yes_no", action="draft festive win-back WhatsApp", artifact="winback_note",
                 anchor="festive seasonal beat + customer base", template="vera_festival_v1")


def _trend_items(trends: list):
    out = []
    for t in trends:
        m = re.match(r"(.+?)_demand_([+-]?\d+)", str(t))
        if m:
            name = m.group(1)
            name = {"cold_cough": "cold & cough"}.get(name.lower(), humanize_key(name))
            out.append((name, int(m.group(2))))
    return out


def seasonal_shift(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    items = _trend_items(tp.get("trends") or [])
    season = humanize_key(re.sub(r"_?20\d\d", "", str(tp.get("season") or "seasonal")))
    if not items:
        d = best_digest(C, [season, "demand", "shift", "season"], kinds=("seasonal",))
        if d:
            src = f" ({d.get('source')})" if d.get("source") else ""
            beats = [f"{d['actionable'].rstrip('.')}." ] if d.get("actionable") else []
            return Draft(genre="seasonal_shift", why_now="seasonal demand shift",
                         hook=f"{ctx.sal}, a seasonal shift worth acting on: {lcfirst(first_sentence(str(d.get('summary') or d.get('title'))))}{src}".rstrip(".") + ".",
                         beats=beats, ask=cta_draft(ctx, ctx.artifact_label("post") + " for it"), cta="binary_yes_no",
                         action="draft seasonal post", artifact="post", anchor="category seasonal digest",
                         template="vera_seasonal_v1", extra={"topic": str(d.get("title", ""))[:40]})
        return Draft(genre="no_evidence", why_now="seasonal trigger without data", hook=f"{ctx.sal}.", beats=[], ask="",
                     cta="none", action="none", anchor="no seasonal evidence", template="vera_seasonal_v1",
                     extra={"blocked": True, "block_reason": "seasonal trigger carries no demand data and no matching digest item"})
    ups = sorted([i for i in items if i[1] > 0], key=lambda x: -x[1])
    downs = [i for i in items if i[1] < 0]
    hook = f"{ctx.sal}, the {season} shift is showing up in demand"
    if ups:
        hook += ": " + join_human([f"{n} +{v}%" for n, v in ups[:3]])
        if downs:
            hook += f", while {downs[0][0]} is down {abs(downs[0][1])}%"
    hook += "."
    beats = []
    d = best_digest(C, [season, "demand", "shift", "shelf"] + [n for n, _ in items], kinds=("seasonal",))
    if d and d.get("source"):
        hook = hook[:-1] + f" ({d['source']})."
    if d and d.get("actionable"):
        beats.append(f"Shelf move for this week: {lcfirst(str(d['actionable']).rstrip('.'))}.")
    rep = repeat_share(ctx)
    total = as_float(M.agg.get("total_unique_ytd"))
    if rep and total:
        n_rep = int(round(total * (as_float(M.agg.get("repeat_customer_pct")) or 0) / 10.0) * 10)
        ctx.fact(f"about {num(n_rep)} repeat customers", ["merchant.customer_aggregate.total_unique_ytd",
                                                          "merchant.customer_aggregate.repeat_customer_pct"], derived=True, value=n_rep)
        beats.append(f"Your ~{num(n_rep)} repeat customers are the fastest way to move that stock.")
    delivery = active_offer(ctx, "delivery")
    thing = f"a '{season} essentials' WhatsApp for them" + (f" with your {delivery}" if delivery else "")
    return Draft(genre="seasonal_shift", why_now=f"{season} demand shift", hook=hook, beats=beats,
                 ask=f"Want me to draft {thing}?", ask_hi=f"Unke liye '{season} essentials' WhatsApp draft kar doon?",
                 cta="binary_yes_no", action=f"draft {season} essentials WhatsApp", artifact="customer_note",
                 anchor="high repeat base + live delivery offer" if delivery else "repeat base",
                 template="vera_seasonal_v1", extra={"offer": delivery, "topic": f"{season} essentials"})


def generic_event(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    desc_keys = ("headline", "title", "event", "name", "description", "summary")
    desc = next((str(tp[k]) for k in desc_keys if tp.get(k)), None)
    if not desc and not any(tp.get(k) for k in ("temp_c", "temperature_c", "temperature", "date")):
        return Draft(genre="no_evidence", why_now=humanize_key(ctx.kind), hook=f"{ctx.sal}.", beats=[], ask="", cta="none",
                     action="none", anchor="event trigger without details", template="vera_event_v1",
                     extra={"blocked": True, "block_reason": "event trigger has no description, place or date to ground on"})
    desc = desc or humanize_key(ctx.kind)
    extras = []
    for k in ("temp_c", "temperature_c", "temperature"):
        if tp.get(k) is not None:
            extras.append(f"{num(tp[k])}°C")
    if tp.get("city") and str(tp["city"]).lower() not in desc.lower():
        extras.append(str(tp["city"]))
    hook = f"{ctx.sal}, heads-up: {lcfirst(desc)}{f' ({join_human(extras)})' if extras else ''}."
    beats = []
    rel = best_digest(C, [ctx.kind, tp], merchant=M)
    if rel:
        beats.append(f"Relevant for you: {lcfirst(first_sentence(str(rel.get('summary') or rel.get('title'))))}")
    else:
        sb = matching_seasonal(C, [ctx.kind, tp])
        if sb:
            beats.append(f"For {C.display_name.lower()} that usually means {lcfirst(str(sb.get('note')))}.")
    offer = active_offer(ctx)
    if offer:
        beats.append(f"Your {offer} is the right thing to put in front of people today.")
    return Draft(genre="event_push", why_now=humanize_key(ctx.kind), hook=hook, beats=beats,
                 ask=cta_draft(ctx, ctx.artifact_label("post") + " for today"), cta="binary_yes_no",
                 action="draft event post", artifact="post", anchor="local event + live offer" if offer else "local event",
                 template="vera_event_v1", extra={"offer": offer, "topic": desc})


# ---------------------------------------------------------------- competition
def competition(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    name, dist, their, opened = tp.get("competitor_name"), tp.get("distance_km"), tp.get("their_offer"), tp.get("opened_date")
    pos = M.top_theme("pos")
    beats: List[str] = []
    if name or dist:
        hook = (f"{ctx.sal}, a new {_noun(ctx)}{f' — {name} —' if name else ''} opened "
                f"{f'{num(dist)} km from you' if dist else 'near you'}{f' on {fmt_date(opened)}' if opened else ''}")
        if their:
            hook += f", leading with {their}"
        hook += "."
        if their:
            ours = active_offer(ctx, *tokens(offer_service(their))[:2])
            tp_, op_ = as_float(offer_price(their) or ""), as_float(offer_price(ours or "") or "")
            if ours and tp_ and op_ and tp_ < op_:
                gap = int(op_ - tp_)
                ctx.fact(f"{inr(gap)} under", ["trigger.payload.their_offer", "merchant.offers"], derived=True, value=gap)
                beats.append(f"That's {inr(gap)} under your {ours}.")
    else:
        hook = f"{ctx.sal}, heads-up: a new competitor has opened near {M.place or 'you'}."
    if their and offer_price(their):
        beats.append(pick(seed(ctx, "nomatch"), ["I wouldn't match that price.", "Matching that price would be the wrong fight."]))
    if pos and review_quote(pos):
        beats.append(f"Your reviews keep praising {humanize_key(pos.get('theme')).replace('doctor manner', 'the way you explain things')} "
                     f"(\"{review_quote(pos)}\" — {num(pos.get('occurrences_30d'))} mentions this month); a price-led newcomer can't copy that.")
        ask = "Want me to draft a Google post built around that strength?"
        anchor = f"strong positive review theme ({pos.get('theme')})"
    else:
        rep = repeat_share(ctx)
        total = as_float(M.agg.get("total_unique_ytd"))
        if rep and total:
            line = f"No need to react on price — your moat is your regulars: {rep} of your {num(total)} customers this year come back"
            if pos:
                line += f", and {num(pos.get('occurrences_30d'))} reviews this month praise your {humanize_key(pos.get('theme')).replace(' quality', '')}"
            beats.append(line + ".")
        elif pos:
            beats.append(f"Your edge is {humanize_key(pos.get('theme'))} — {num(pos.get('occurrences_30d'))} reviews praised it this month.")
        offer = active_offer(ctx)
        ask = f"Want me to draft a thank-you WhatsApp for your regulars{f' with your {offer} up front' if offer else ''}?"
        anchor = "repeat-customer base as moat"
        return Draft(genre="competitive_response", why_now="new competitor nearby", hook=hook, beats=beats, ask=ask,
                     ask_hi="Regulars ke liye thank-you WhatsApp draft kar doon?", cta="binary_yes_no",
                     action="draft a thank-you note for your regulars", artifact="customer_note", anchor=anchor,
                     expected_action="price-match the competitor", adjusted_action="retain regulars, not discount",
                     template="vera_competitor_v1", extra={"offer": offer, "topic": "thank you"})
    return Draft(genre="competitive_response", why_now="new competitor nearby", hook=hook, beats=beats, ask=ask,
                 ask_hi="Iske liye post draft kar doon?", cta="binary_yes_no", action="draft a Google post on what sets you apart",
                 artifact="post", anchor=anchor, expected_action="price-match the competitor",
                 adjusted_action="compete on reputation/relationship, not price", template="vera_competitor_v1",
                 extra={"topic": "what sets you apart"})


# ---------------------------------------------------------------- reputation
def reputation(ctx: Ctx) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    theme_key = tp.get("theme")
    t = M.theme(theme_key) if theme_key else None
    t = t or (M.top_theme("neg") if not theme_key else None)
    if not theme_key and not t:
        pos = M.top_theme("pos")
        if pos:
            hook = f"{ctx.sal}, a review pattern worth using: {num(pos.get('occurrences_30d'))} reviews this month praise your {humanize_key(pos.get('theme'))}."
            return Draft(genre="reputation_leverage", why_now="positive review theme", hook=hook, beats=[],
                         ask=cta_draft(ctx, "a Google post built around it"), cta="binary_yes_no", action="draft post",
                         artifact="post", anchor="positive review theme", template="vera_review_theme_v1")
        return perf_fallback(ctx)
    theme = theme_phrase(theme_key or t.get("theme"))
    occ = tp.get("occurrences_30d") or (t or {}).get("occurrences_30d")
    quote = tp.get("common_quote") or review_quote(t)
    trend = tp.get("trend")
    hook = f"{ctx.sal}, {theme} has come up in {num(occ)} reviews in the last 30 days" + (" — and it's rising." if trend == "rising" else ".")
    beats = []
    if quote:
        beats.append(f"One reads: \"{quote}\".")
    pos = M.top_theme("pos")
    if pos and pos.get("theme") != theme_key:
        beats.append(f"Meanwhile {num(pos.get('occurrences_30d'))} reviews praise your {theme_phrase(pos.get('theme'))}"
                     f" — the product isn't the problem, the {theme.split()[-1]} is.")
    return Draft(genre="reputation_fix", why_now=f"'{theme}' review theme {trend or 'emerging'}", hook=hook, beats=beats,
                 ask=f"Want me to draft short, non-defensive replies to those {num(occ)} reviews?",
                 ask_hi=f"Un {num(occ)} reviews ke liye short replies draft kar doon?", cta="binary_yes_no",
                 action="draft review replies", artifact="review_reply", anchor=f"'{theme}' now the top complaint",
                 template="vera_review_theme_v1", extra={"theme": theme, "quote": quote})


# ---------------------------------------------------------------- account
def account(ctx: Ctx) -> Draft:
    k = ctx.kind
    if "renew" in k or "subscription" in k:
        return renewal(ctx)
    if "verif" in k or "gbp" in k:
        return gbp_unverified(ctx)
    return merchant_winback(ctx)


def renewal(ctx: Ctx) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    days = as_float(tp.get("days_remaining", M.sub.get("days_remaining")))
    plan, amount = tp.get("plan") or M.sub.get("plan") or "", tp.get("renewal_amount")
    lapsed_days = as_float(M.sub.get("days_since_expiry"))
    price = f" ({inr(amount)} to renew)" if amount else ""
    if days is not None and days > 0:
        hook = f"{ctx.sal}, your {plan + ' ' if plan else ''}plan ends in {num(days)} days{price}."
    elif str(M.sub.get("status", "")).lower() == "expired" and lapsed_days:
        hook = f"{ctx.sal}, your {plan + ' ' if plan else ''}plan lapsed {num(lapsed_days)} days ago{price}."
    else:
        hook = f"{ctx.sal}, your {plan + ' ' if plan else ''}plan is up for renewal{price}."
    metric, delta, basis = _dip_metric(ctx)
    if basis in ("delta_7d", "trigger") and delta is not None and delta <= -0.2 and not M.active_offers:
        offer = suggested_catalog_offer(ctx)
        beats = [f"Before you decide, the honest picture: {metric_word(metric)} are down {pct(abs(delta))} this week and there's no live offer — renewing alone won't fix that."]
        ask = f"Want me to put '{offer}' live first, so you judge the plan on better numbers?" if offer else "Want me to fix the listing first, so you judge the plan on better numbers?"
        return Draft(genre="honest_renewal", why_now="plan renewal due", hook=hook, beats=beats, ask=ask,
                     cta="binary_yes_no", action=f"set up {offer}" if offer else "fix listing", artifact="offer_setup",
                     anchor="performance dip + no offer; renewal pitch alone would ring hollow",
                     expected_action="push renewal", adjusted_action="fix performance first, then renew",
                     template="vera_renewal_v1", extra={"offer": offer})
    v, c, l = M.metric("views"), M.metric("calls"), M.metric("leads")
    recap = join_human([f"{num(v)} views" if v else "", f"{num(c)} calls" if c else "", f"{num(l)} leads" if l else ""])
    beats = [f"What the plan delivered in the last 30 days: {recap}."] if recap else []
    return Draft(genre="renewal", why_now="plan renewal due", hook=hook, beats=beats,
                 ask="Want me to send the renewal details so there's no gap in your listing?",
                 ask_hi="Renewal details bhej doon, taaki listing mein gap na aaye?",
                 cta="binary_yes_no", action="send renewal details", artifact="renewal", anchor="plan value recap",
                 template="vera_renewal_v1")


def gbp_unverified(ctx: Ctx) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    uplift = as_float(tp.get("estimated_uplift_pct"))
    path = humanize_key(str(tp.get("verification_path") or "")).replace(" or ", " or a ")
    hook = f"{ctx.sal}, {M.name} is still unverified on Google."
    beats = []
    if uplift:
        beats.append(f"Our estimate for your profile: about a {pct(uplift)} uplift once verified.")
    ctr_txt = ctr_vs_peer(ctx)
    if ctr_txt and (M.metric("ctr") or 0) > (ctx.category.peer_ctr() or 1):
        beats.append(f"Your listing already converts well (CTR {ctr_txt}) — verification puts it in front of more people.")
    if path:
        beats.append(f"It takes one {path}.".replace("one postcard", "one postcard").replace("one a ", "one "))
    return Draft(genre="account_fix", why_now="listing unverified", hook=hook, beats=beats,
                 ask="Want me to start verification now and walk you through it?",
                 ask_hi="Verification abhi shuru kar doon? Main step-by-step bata dungi.",
                 cta="binary_yes_no", action="start GBP verification", artifact="verification",
                 anchor="unverified listing; estimated visibility uplift", template="vera_gbp_v1")


def merchant_winback(ctx: Ctx) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    days = tp.get("days_since_expiry", M.sub.get("days_since_expiry"))
    dip = as_float(tp.get("perf_dip_pct"))
    added = tp.get("lapsed_customers_added_since_expiry")
    hook = f"{ctx.sal}, it's been {num(days)} days since {M.name}'s plan lapsed — no pitch, just what's changed since." if days \
        else f"{ctx.sal}, a quick look at {M.name} since we last spoke — no pitch."
    beats = []
    bits = []
    if dip:
        bits.append(f"calls are down {pct(abs(dip))}")
    if added:
        bits.append(f"{num(added)} more {ctx.policy.people} have slipped into lapsed")
    if bits:
        beats.append(f"{ucfirst(join_human(bits))}.")
    lp = lapsed(ctx)
    if lp:
        beats.append(f"That's {num(lp[0])} {ctx.policy.people} now who haven't been back in {lp[1]}.")
    who = f"those {num(added)}" if added else "them"
    return Draft(genre="winback_merchant", why_now=f"{num(days)} days since plan lapsed" if days else "merchant lapsed",
                 hook=hook, beats=beats, ask=f"Want me to draft a win-back WhatsApp for {who}?",
                 ask_hi="Unke liye win-back WhatsApp draft kar doon?", cta="binary_yes_no",
                 action="draft customer win-back note", artifact="winback_note",
                 anchor="lapsed customers growing since expiry", expected_action="renewal pitch",
                 adjusted_action="show value first (customer win-back)", template="vera_winback_v1")


# ---------------------------------------------------------------- dormant
def dormant(ctx: Ctx) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    days_quiet = tp.get("days_since_last_merchant_message")
    if days_quiet:
        hook = f"{ctx.sal}, it's been {num(days_quiet)} days since we last spoke — here's where {M.name} stands, briefly."
    elif M.history:
        hook = f"{ctx.sal}, it's been a while since we last spoke — here's where {M.name} stands, briefly."
    else:
        hook = f"{ctx.sal}, a quick check-in on {M.name}'s numbers."
    metric, delta, basis = _dip_metric(ctx)
    beats = []
    if basis in ("delta_7d", "trigger") and delta is not None:
        line = f"{ucfirst(metric_word(metric))} fell {pct(abs(delta))} week-on-week"
        g = peer_gap(ctx, metric) if metric in ("calls", "views") else None
        if g and g[2] < 0:
            line += f", and over 30 days you're at {num(g[0])} against about {num(g[1])} for {peer_scope(ctx)}"
        beats.append(line + ".")
    elif basis == "peer_gap":
        g = peer_gap(ctx, metric)
        beats.append(f"{M.name} got {num(g[0])} {metric_word(metric)} in 30 days vs about {num(g[1])} for {peer_scope(ctx)}.")
    lp = lapsed(ctx)
    if lp:
        beats.append(f"And {num(lp[0])} of your {ctx.policy.people} haven't been back in {lp[1]}.")
        return Draft(genre="dormant_value", why_now=f"no reply in {num(tp.get('days_since_last_merchant_message'))} days" if tp.get('days_since_last_merchant_message') else "merchant dormant",
                     hook=hook, beats=beats, ask="Want me to draft a win-back note for them — you'd just approve it?",
                     ask_hi="Unke liye win-back note draft kar doon? Aapko bas approve karna hai.",
                     cta="binary_yes_no", action="draft win-back note", artifact="winback_note",
                     anchor="lapsed customers + performance dip", expected_action="repeat the last (ignored) pitch",
                     adjusted_action="lead with a merchant-specific number, low-effort ask", template="vera_dormant_v1")
    lever = _dip_lever(ctx, metric)
    beats += lever["beats"]
    return Draft(genre="dormant_value", why_now="merchant dormant", hook=hook, beats=beats, ask=lever["ask"],
                 ask_hi=lever.get("ask_hi"), cta="binary_yes_no", action=lever["action"], artifact=lever["artifact"],
                 anchor=lever["anchor"], expected_action="repeat the last (ignored) pitch",
                 adjusted_action="lead with a merchant-specific number, low-effort ask", template="vera_dormant_v1",
                 extra={"offer": lever.get("offer")})


# ---------------------------------------------------------------- curious ask
def curious(ctx: Ctx) -> Draft:
    M, C = ctx.merchant, ctx.category
    pos = M.top_theme("pos")
    guess, evidence = None, []
    if pos:
        q = review_quote(pos) or ""
        theme_h = theme_phrase(str(pos.get("theme")))
        # A service guess needs a service in the review itself (quote or theme
        # name); a theme like "doctor manner" is praise, not a service.
        guess = find_service(ctx, q) or find_service(ctx, str(pos.get("theme", "")).replace("_", " "))
        if guess:
            n = num(pos.get("occurrences_30d"))
            evidence.append(f"{n} reviews this month praise your {theme_h}" + (f" — one says \"{q}\"" if q else "")
                            if find_service(ctx, q) else f"{n} reviews this month praise your {guess}")
    if not guess:
        # No service in the reviews: use a trend whose query names a catalog service.
        for t in sorted(C.trends, key=lambda t: -(t.get("delta_yoy") or 0)):
            s = find_service(ctx, str(t.get("query", "")))
            if s:
                q = str(t.get("query", "")).lower()
                for w in (M.city, M.locality, "near me", "price", "cost"):
                    q = q.replace(str(w).lower(), "") if w else q
                guess = re.sub(r"\s{2,}", " ", q).strip() or s
                evidence.append(trend_phrase(ctx, t))
                break
    else:
        t = top_trend(C, [guess])
        if t and set(tokens(guess)) & set(tokens(t.get("query"))):
            evidence.append(trend_phrase(ctx, t))
    service_word = {"restaurants": "dish", "pharmacies": "product", "gyms": "class", "dentists": "treatment"}.get(C.slug, "service")
    if guess and C.slug == "restaurants":
        hook = f"{ctx.sal}, one question for this week: besides the {guess}, which {service_word} are people asking for most?"
        evidence = [e.replace("mention it", f"are about the {guess}") + " — I'd like to find your second hero dish"
                    for e in evidence[:1]]
    elif guess:
        verb = "are" if guess.endswith("s") and not guess.endswith("ss") else "is"
        hook = f"{ctx.sal}, quick question for this week: {verb} {guess} your most-asked {service_word} right now?"
    else:
        hook = f"{ctx.sal}, quick question for this week: which {service_word} are {ctx.policy.people} asking about most?"
    beats = [f"{ucfirst(join_human(evidence))}."] if evidence else []
    ask = {
        "restaurants": "Reply with the dish and I'll feature it in this week's Google post.",
        "salons": "Reply with the service and I'll build this week's Google post around it, with your price.",
        "gyms": "Reply with the class and I'll make it the focus of this week's post.",
        "pharmacies": "Reply with the product and I'll make sure it's easy to find on your listing this week.",
        "dentists": "Reply with the treatment and I'll draft a short patient-education post on it.",
    }.get(C.slug, "Reply in one line and I'll build this week's Google post around it.")
    return Draft(genre="curious_ask", why_now="weekly demand check-in", hook=hook, beats=beats, ask=ask,
                 ask_hi="Ek line mein bata dijiye — is hafte ka Google post usi par banaungi.",
                 cta="open_ended", action="turn your answer into this week's post", artifact="post",
                 anchor="grounded guess from review themes/trends", template="vera_curious_ask_v1",
                 extra={"guess": guess})


# ---------------------------------------------------------------- fallback
def perf_fallback(ctx: Ctx) -> Draft:
    metric, delta, _ = _dip_metric(ctx)
    lever = _dip_lever(ctx, metric)
    hook = f"{ctx.sal}, one thing on {ctx.merchant.name}'s listing worth fixing this week."
    return Draft(genre="listing_fix", why_now=humanize_key(ctx.kind) or "listing check", hook=hook, beats=lever["beats"],
                 ask=lever["ask"], ask_hi=lever.get("ask_hi"), cta="binary_yes_no", action=lever["action"],
                 artifact=lever["artifact"], anchor=lever["anchor"], template="vera_listing_v1",
                 extra={"offer": lever.get("offer")})


def general(ctx: Ctx) -> Draft:
    tp = {k: v for k, v in ctx.tp.items() if k not in ("placeholder", "metric_or_topic")}
    if not tp:
        return perf_fallback(ctx)
    bits = []
    for k, v in list(tp.items())[:3]:
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            bits.append(f"{humanize_key(k)}: {v if isinstance(v, str) else num(v)}")
    hook = f"{ctx.sal}, an update on {ctx.merchant.name} — {humanize_key(ctx.kind)}" + (f" ({'; '.join(bits)})." if bits else ".")
    offer = active_offer(ctx)
    beats = [f"Your {offer} is the easiest thing to pair with it."] if offer else []
    return Draft(genre="general_update", why_now=humanize_key(ctx.kind), hook=hook, beats=beats,
                 ask=cta_draft(ctx, ctx.artifact_label("post")), cta="binary_yes_no", action="draft post",
                 artifact="post", anchor="trigger payload", template="vera_update_v1", extra={"offer": offer})
