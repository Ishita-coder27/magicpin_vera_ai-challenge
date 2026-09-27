"""Merchant-facing strategies — one per opportunity family.

Each strategy answers, in order: what changed (why now), what proves it, why
this merchant should care, and the smallest action Vera can take for them.
Where the obvious action is wrong for this merchant (Saturday IPL promo,
price-matching a new competitor, ad spend in a seasonal lull, a Diwali push
six months early) the strategy records expected vs adjusted action and says so.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..ctx import Ctx, Draft
from ..formatting import split_sentences, ucfirst, as_float, first_sentence, fmt_date, fmt_time, humanize_key, inr, join_human, num, pct
from ..retrieval import best_digest, matching_seasonal, month_seasonal, top_trend
from .common import (beat_phrase, cite, offer_kind_phrase, active_offer, cohort, cta_draft, ctr_vs_peer, history_commitment, humanize_iso_dates,
                     lapsed, lcfirst, member_base, metric_word, offer_price, offer_service, peer_gap,
                     peer_scope, pick, repeat_share, review_quote, seed, suggested_catalog_offer, trend_phrase)


def _sentences(text: str) -> List[str]:
    return split_sentences(text)


def _src_split(source: str):
    """'JIDA Oct 2026, p.14' -> ('JIDA Oct 2026', 'p.14')."""
    parts = [p.strip() for p in str(source or "").split(",", 1)]
    return parts[0], (parts[1] if len(parts) > 1 else "")


# ---------------------------------------------------------------- knowledge
def knowledge(ctx: Ctx) -> Draft:
    C, M, tp = ctx.category, ctx.merchant, ctx.tp
    if tp.get("query") and not (tp.get("top_item_id") or tp.get("digest_item_id")):
        return demand_signal(ctx)
    item = C.digest_item(tp.get("top_item_id") or tp.get("digest_item_id") or tp.get("item_id"))
    if item is None and isinstance(tp.get("top_item"), dict):
        item = tp["top_item"]
    if item is None:
        item = best_digest(C, [ctx.kind, tp], kinds=("research", "cde", "trend", "tech"), merchant=M)
    if item is None:
        return trend_nudge(ctx)
    ik = str(item.get("kind", "research"))
    if ik in ("compliance", "alert", "supply"):
        return compliance(ctx, item)
    if ik == "cde" or ctx.kind == "cde_opportunity":
        return cde(ctx, item)
    if ik == "trend":
        return trend_item(ctx, item)
    return research(ctx, item)


def demand_signal(ctx: Ctx) -> Draft:
    """Trigger payload carries the demand itself (query + count/delta): lead
    with it, anchor on the merchant's matching live offer, one action."""
    tp, M = ctx.tp, ctx.merchant
    query = str(tp["query"]).strip()
    count = next((tp[k] for k in ("searches_30d", "searches", "search_count", "count", "volume") if tp.get(k) is not None), None)
    delta = as_float(tp.get("delta_yoy", tp.get("delta_pct")))
    where = tp.get("locality") or M.locality or M.city
    if count is not None:
        hook = f"{ctx.sal}, {num(count)} people{' in ' + where if where else ''} searched for '{query}' recently."
    elif delta is not None:
        hook = f"{ctx.sal}, searches for '{query}'{' in ' + where if where else ''} are {'up' if delta > 0 else 'down'} {pct(abs(delta))}."
    else:
        hook = f"{ctx.sal}, '{query}' is trending{' in ' + where if where else ''}."
    qwords = set(re.findall(r"[a-z]{4,}", query.lower())) - {"near", "best", "price", "cost"}
    match = next((o for o in M.active_offer_titles() if qwords & set(re.findall(r"[a-z]{4,}", o.lower()))), None)
    offer = match or active_offer(ctx)
    beats = []
    if offer:
        beats.append(f"You already have {offer} live" + (" — a direct match for that search." if match else " to put in front of them."))
        ask, action, art = f"Want me to draft a Google post that puts {offer} in front of those searches?", "draft demand post", "post"
    else:
        sug = suggested_catalog_offer(ctx)
        beats.append("You don't have an offer live to catch that demand" + (f"; '{sug}' would fit." if sug else "."))
        ask, action, art = (f"Want me to set up '{sug}' for it?", f"set up {sug}", "offer_setup") if sug else \
            (cta_draft(ctx, ctx.artifact_label("post") + " for it"), "draft demand post", "post")
    return Draft(genre="demand_signal", why_now=f"search demand for '{query}'", hook=hook, beats=beats, ask=ask,
                 cta="binary_yes_no", action=action, artifact=art,
                 anchor="matching live offer" if match else ("live offer" if offer else "no live offer"),
                 template="vera_trend_signal_v1", extra={"offer": offer, "topic": query})


def research(ctx: Ctx, item: dict) -> Draft:
    M, P = ctx.merchant, ctx.policy
    title, source = str(item.get("title", "")).rstrip("."), str(item.get("source", ""))
    src_head, src_page = _src_split(source)
    ctx.fact(title, "category.digest.title")
    ctx.fact(source, "category.digest.source")
    sents = _sentences(str(item.get("summary", "")))
    n = item.get("trial_n")
    lead = sents[0].rstrip(".") if sents else ""
    if n and num(n) not in lead and str(n) not in lead:
        lead += f" (n={num(n)})"
    beats = [f"{title}{f' ({src_page})' if src_page else ''}."]
    if lead:
        beats.append(lead + ".")
    caveat = next((s for s in sents[1:] if len(s) <= 70), None)
    coh = cohort(ctx, item.get("patient_segment"), title)
    if coh and caveat:
        beats.append(f"{caveat.rstrip('.')} — so this is really about your {num(coh[0])} {coh[1]}s.".replace("ss.", "s."))
    elif coh:
        beats.append(f"Your {num(coh[0])} {coh[1]}s are exactly that group.".replace("ss ", "s "))
    elif caveat:
        beats.append(caveat)
    # "JIDA Oct 2026" is a journal issue; "ICMR" alone is an organisation.
    is_issue = bool(re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+20\d\d\b|issue|vol", src_head, re.I))
    org = src_head.split()[0] if src_head else "A new source"
    hook = (f"{ctx.sal}, the new {src_head} just landed — one result worth 2 minutes of your time." if is_issue
            else f"{ctx.sal}, {org} has put out new guidance worth 2 minutes of your time.")
    art = ctx.artifact_label("knowledge", "a short note you can share")
    return Draft(
        genre="research_signal", why_now=f"new {src_head} digest item", hook=hook, beats=beats,
        ask=f"Want me to pull the abstract and draft {art}?", cta="open_ended",
        action=f"pull abstract + draft {art}", artifact="knowledge_note",
        anchor=f"{num(coh[0])} {coh[1]}s in roster match the study cohort" if coh else "clinical relevance to practice",
        template="vera_research_digest_v2", extra={"item": item})


def cde(ctx: Ctx, item: dict) -> Draft:
    tp = ctx.tp
    title = str(item.get("title", "")).rstrip(".")
    when = item.get("date")
    date_txt = fmt_date(when, weekday=True) if when else ""
    time_txt = fmt_time(when) if when else None
    credits = tp.get("credits") or item.get("credits")
    fee = str(item.get("actionable") or "")
    fee_txt = ""
    m = re.search(r"free for ([\w\s]+?) members?;\s*(₹[\d,]+) for non-members", fee, flags=re.I)
    if m:
        fee_txt = f"free for {m.group(1).strip()} members, {m.group(2)} otherwise"
    elif str(tp.get("fee", "")).startswith("free"):
        fee_txt = "free for members"
    summary = first_sentence(str(item.get("summary", "")))
    parts = [p for p in (f"{date_txt}{' at ' + time_txt if time_txt else ''}" if date_txt else "",
                         f"{num(credits)} CDE credits" if credits else "", fee_txt) if p]
    org, _, session = title.partition(": ")
    s_main, _, s_sub = session.partition(" — ")
    lead = (f"{org}'s session on {lcfirst(s_main)}{f' ({s_sub})' if s_sub else ''}") if session else title
    beats = [f"{lead} is on {join_human(parts, 'and')}." if parts else f"{lead}."]
    if summary:
        beats.append(summary)
    ask_topic = history_commitment(ctx)
    anchor = "continuing education relevant to the practice"
    if ask_topic and re.search(r"align|scan|digital|implant|crown", ask_topic + title, flags=re.I) and "align" in ask_topic.lower():
        beats.append("Useful context if aligner cases are growing — you asked me to push aligners in your posts.")
        anchor = "merchant recently asked to focus on aligners; session covers digital scanning"
    return Draft(
        genre="cde_invite", why_now=f"CDE session on {date_txt}" if date_txt else "CDE session announced",
        hook=f"{ctx.sal}, a CDE slot worth blocking:", beats=beats,
        ask="Want me to send the registration details?", cta="binary_yes_no",
        action="send registration details", artifact="cde_details", anchor=anchor,
        template="vera_cde_invite_v1", extra={"item": item})


def trend_item(ctx: Ctx, item: dict) -> Draft:
    title = str(item.get("title", "")).rstrip(".")
    src = str(item.get("source", ""))
    sents = _sentences(str(item.get("summary", "")))
    beats = [f"{title} ({src})."] + sents[:2]
    ask_topic = history_commitment(ctx)
    anchor = "category demand shift"
    if ask_topic and set(re.findall(r"[a-z]{5,}", ask_topic.lower())) & set(re.findall(r"[a-z]{5,}", title.lower())):
        beats.append(f"It backs the direction you gave me (\"{ask_topic}\").")
        anchor = "matches the merchant's own stated focus"
    actionable = str(item.get("actionable") or "")
    if re.search(r"gbp|description|listing|profile|menu", actionable, flags=re.I):
        ask, action = "Want me to draft that line for your Google listing?", "draft listing copy"
    else:
        ask, action = cta_draft(ctx, ctx.artifact_label("post")), "draft post"
    return Draft(genre="trend_signal", why_now="category trend moved", hook=f"{ctx.sal}, a demand shift worth acting on:",
                 beats=beats, ask=ask, cta="binary_yes_no", action=action, artifact="post", anchor=anchor,
                 template="vera_trend_signal_v1", extra={"item": item})


def trend_nudge(ctx: Ctx) -> Draft:
    t = top_trend(ctx.category, [ctx.kind, ctx.tp, ctx.merchant.name])
    tp_txt = trend_phrase(ctx, t)
    beats = [f"{tp_txt[0].upper() + tp_txt[1:]}." if tp_txt else "Search demand in your category is moving this week."]
    offer = active_offer(ctx)
    if offer:
        beats.append(f"Your {offer} is the natural thing to put in front of that demand.")
    return Draft(genre="trend_signal", why_now="category search trend", hook=f"{ctx.sal}, quick signal from your category:",
                 beats=beats, ask=cta_draft(ctx, ctx.artifact_label("post") + " around it"), cta="binary_yes_no",
                 action="draft post", artifact="post", anchor="active offer matches demand" if offer else "category demand",
                 template="vera_trend_signal_v1")


# ---------------------------------------------------------------- compliance
def compliance(ctx: Ctx, item: Optional[dict] = None) -> Draft:
    C, M, tp = ctx.category, ctx.merchant, ctx.tp
    item = item or C.digest_item(tp.get("top_item_id") or tp.get("alert_id") or tp.get("digest_item_id"))
    if item is None:
        item = best_digest(C, [ctx.kind, tp], kinds=("compliance", "alert", "supply"), merchant=M) or {}
    if ctx.kind == "supply_alert" or item.get("kind") in ("alert", "supply") or tp.get("affected_batches"):
        return supply_alert(ctx, item)
    title = humanize_iso_dates(str(item.get("title") or humanize_key(ctx.kind))).rstrip(".")
    source = humanize_iso_dates(str(item.get("source", "")))
    sents = _sentences(str(item.get("summary", "")))
    deadline = tp.get("deadline_iso") or tp.get("deadline")
    days = ctx.days_until(deadline) if deadline else None
    hook = f"{ctx.sal}, a compliance date for your diary"
    if deadline:
        hook += f": {fmt_date(deadline, year=True)}"
        if days is not None and days >= 0:
            hook += ctx.fact(f" ({days} days out)", "derived: deadline - now", derived=True, value=days)
    hook += "."
    if deadline:
        title = re.sub(r"\s+effective\s+.*$", "", title)
    beats = [f"{title}{f' ({source})' if source else ''}."] + sents[:2]
    if len(sents) > 2 and len(sents[2]) < 60:
        beats.append(sents[2])
    art = ctx.artifact_label("compliance", "a one-page checklist")
    actionable = str(item.get("actionable") or "")
    if actionable:
        beats.append(f"Practical step: {lcfirst(humanize_iso_dates(actionable, year=False)).rstrip('.')}.")
    return Draft(
        genre="compliance_deadline", why_now=f"regulation effective {fmt_date(deadline, year=True)}" if deadline else "new regulation",
        hook=hook, beats=beats, ask=f"Want me to draft {art} so you're audit-ready?", cta="binary_yes_no",
        action=f"draft {art}", artifact="compliance_checklist", anchor="applies to every practice in the category; deadline-bound",
        template="vera_compliance_alert_v1", extra={"item": item})


def supply_alert(ctx: Ctx, item: dict) -> Draft:
    tp, M = ctx.tp, ctx.merchant
    molecule = tp.get("molecule") or ""
    batches = [str(b) for b in (tp.get("affected_batches") or [])]
    mfr = tp.get("manufacturer")
    src = item.get("source")
    sents = _sentences(str(item.get("summary", "")))
    what = f"{molecule} " if molecule else ""
    batch_txt = f" — batches {join_human(batches)}" if batches else ""
    hook = f"{ctx.sal}, urgent: voluntary {what}recall{batch_txt}{f' ({mfr})' if mfr else ''}."
    beats = []
    reason = next((s for s in sents if re.search(r"potency|contamin|flag|defect", s, flags=re.I)), None)
    safety = next((s for s in sents if re.search(r"safety|risk", s, flags=re.I)), None)
    if reason:
        m = re.search(r"flagged for ([^.;]+)", reason, flags=re.I)
        if m:
            beats.append(f"{src or 'The alert'} flags them for {m.group(1).strip()}; {lcfirst(safety.rstrip('.')) if safety else 'details are in the alert'}.")
            safety = None
        else:
            beats.append(f"{src + ': ' if src else ''}{reason}")
    if safety and safety != reason:
        beats.append(safety)
    if any("inform" in s.lower() for s in sents):
        beats.append("Affected customers need to be told and offered a replacement through the distributor return chain.")
    chronic = as_float(M.agg.get("chronic_rx_count"))
    commitment = history_commitment(ctx)
    if commitment and re.search(r"list", commitment, flags=re.I):
        beats.append(f"On the list you asked for: I'm filtering your {num(chronic)} chronic-Rx customers for anyone dispensed these batches."
                     if chronic else "On the list you asked for: I'm filtering your repeat-Rx customers for these batches.")
        if chronic:
            ctx.fact(f"{num(chronic)} chronic-Rx customers", "merchant.customer_aggregate.chronic_rx_count")
    elif chronic:
        beats.append(ctx.fact(f"Your {num(chronic)} chronic-Rx customers are the list to check first.",
                              "merchant.customer_aggregate.chronic_rx_count"))
    art = ctx.artifact_label("compliance", "the customer note")
    return Draft(
        genre="compliance_alert", why_now=f"{molecule} batch recall" if molecule else "supply alert",
        hook=hook, beats=beats, ask=f"Want me to draft {art} now, so they're ready the moment the list is filtered?",
        ask_hi=f"Customer note aur replacement steps abhi draft kar doon?", cta="binary_yes_no",
        action=f"draft {art}", artifact="recall_note",
        anchor="chronic-Rx roster exposed; merchant already asked for the affected-customer list" if commitment else "chronic-Rx roster exposed",
        expected_action="generic stock alert", adjusted_action="customer-level outreach workflow",
        template="vera_compliance_alert_v1", extra={"item": item, "molecule": molecule, "batches": batches})


# ---------------------------------------------------------------- performance
def _dip_metric(ctx: Ctx):
    """(metric, delta, basis) — from payload, else worst 7d delta, else worst peer gap."""
    tp, M = ctx.tp, ctx.merchant
    if tp.get("metric") and as_float(tp.get("delta_pct")) is not None:
        return tp["metric"], as_float(tp["delta_pct"]), "trigger"
    deltas = {m: M.delta(m) for m in ("calls", "views", "ctr", "leads", "directions")}
    neg = {m: d for m, d in deltas.items() if d is not None and d < 0}
    if neg:
        m = min(neg, key=neg.get)
        return m, neg[m], "delta_7d"
    gaps = {m: g[2] for m in ("calls", "views", "directions") if (g := peer_gap(ctx, m)) and g[2] < 0}
    if gaps:
        m = min(gaps, key=gaps.get)
        return m, gaps[m], "peer_gap"
    return None, None, None


def perf_down(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    metric, delta, basis = _dip_metric(ctx)
    seasonal = ctx.kind == "seasonal_perf_dip" or bool(tp.get("is_expected_seasonal"))
    if seasonal:
        return seasonal_dip(ctx, metric, delta)
    mw = metric_word(metric or "calls")
    if ctx.category.slug == "dentists" and mw == "calls":
        mw = "patient calls"
    beats: List[str] = []
    if basis == "trigger":
        base = as_float(tp.get("vs_baseline"))
        window = "week-on-week" if str(tp.get("window", "7d")) == "7d" else f"over {tp.get('window')}"
        hook = f"{ctx.sal}, {mw} to {M.name} fell {pct(abs(delta))} {window}"
        if base:
            now_val = round(base * (1 + delta))
            ctx.fact(f"{now_val} {mw}", ["trigger.payload.vs_baseline", "trigger.payload.delta_pct"], derived=True, value=now_val)
            hook += f" — about {now_val} vs a baseline of {num(base)}"
        hook += "."
    elif basis == "delta_7d":
        hook = f"{ctx.sal}, {mw} to {M.name} fell {pct(abs(delta))} week-on-week."
    elif basis == "peer_gap":
        g = peer_gap(ctx, metric)
        ctx.fact(f"{num(g[0])} vs {num(g[1])}", [f"merchant.performance.{metric}", f"category.peer_stats.{metric}"])
        up = [f"{metric_word(m)} {pct(d, signed=True)}" for m in ("views", "calls") if (d := M.delta(m)) is not None and d > 0]
        ctr_txt = ctr_vs_peer(ctx)
        ctr_above = (M.metric("ctr") or 0) > (ctx.category.peer_ctr() or 1)
        if ctr_txt and ctr_above:
            hook = (f"{ctx.sal}, people who find {M.name} do tap through (CTR {ctr_txt}), but {mw} lag: "
                    f"{num(g[0])} in 30 days vs about {num(g[1])} for similar {ctx.category.display_name.lower().split(' &')[0]}.")
        else:
            hook = (f"{ctx.sal}, {mw} are the soft spot at {M.name}: {num(g[0])} in 30 days vs about {num(g[1])} for "
                    f"{peer_scope(ctx)}{' — even with ' + join_human(up) + ' this week' if up else ''}.")
    else:
        hook = f"{ctx.sal}, your listing numbers flattened this week."
    # Diagnose: pick the single strongest lever from the merchant's own state.
    lever = _dip_lever(ctx, metric)
    beats += lever["beats"]
    return Draft(
        genre="diagnose_fix", why_now=(f"{mw} {pct(delta, signed=True)} week-on-week" if basis in ("trigger", "delta_7d")
                                    else f"{mw} below peer benchmark" if basis == "peer_gap" else "listing flat"),
        hook=hook, beats=beats, ask=lever["ask"], ask_hi=lever.get("ask_hi"), cta="binary_yes_no",
        action=lever["action"], artifact=lever["artifact"], anchor=lever["anchor"],
        expected_action="discount to recover" if lever["artifact"] != "offer_setup" else "",
        adjusted_action=lever["action"], template="vera_perf_alert_v1", extra={"offer": lever.get("offer")})


def _account_state(ctx: Ctx) -> Optional[str]:
    """Merchant-level facts that change how a performance nudge should land."""
    M = ctx.merchant
    st = str(M.sub.get("status", "")).lower()
    if st == "expired" and M.sub.get("days_since_expiry"):
        return ctx.fact(f"your {M.sub.get('plan') or ''} plan lapsed {num(M.sub['days_since_expiry'])} days ago".replace("  ", " "),
                        "merchant.subscription.days_since_expiry")
    d = as_float(M.sub.get("days_remaining"))
    if st in ("active", "trial") and d is not None and d <= 14:
        return ctx.fact(f"your plan renews in {num(d)} days", "merchant.subscription.days_remaining")
    if M.verified is False:
        return "your Google listing is still unverified"
    return None


def _dip_lever(ctx: Ctx, metric: Optional[str]) -> dict:
    M = ctx.merchant
    ctr_txt = ctr_vs_peer(ctx)
    ctr_below = (M.metric("ctr") or 1) < (ctx.category.peer_ctr() or 0)
    neg = M.top_theme("neg")
    # Gyms: trial-to-paid conversion is the growth lever before any new offer.
    t2p, peer_t2p = as_float(M.agg.get("trial_to_paid_pct")), as_float(ctx.category.peer.get("trial_to_paid_pct"))
    if ctx.category.slug == "gyms" and t2p and peer_t2p and t2p < peer_t2p:
        ctx.fact(f"trial-to-paid {pct(t2p)} vs {pct(peer_t2p)}", ["merchant.customer_aggregate.trial_to_paid_pct",
                                                                   "category.peer_stats.trial_to_paid_pct"])
        return {"beats": [f"The fastest lever isn't a new offer — it's conversion: {pct(t2p)} of your trials turn into paid members, "
                          f"vs {pct(peer_t2p)} for similar gyms. A same-day follow-up after every trial is how you close that gap."],
                "ask": "Want me to draft the post-trial follow-up message your front desk can send?",
                "action": "draft the post-trial follow-up", "artifact": "customer_note",
                "anchor": "trial-to-paid below peer"}
    # 1. No live offer while calls/views sag -> put the category's entry offer live.
    if not M.active_offers:
        offer = suggested_catalog_offer(ctx)
        inactive = M.inactive_offers[0] if M.inactive_offers else None
        expired = inactive.get("title") if inactive else None
        paused = inactive is not None and str(inactive.get("status", "")).lower() == "paused"
        b = ["There's no live offer on your listing right now" + (f", and your CTR is {ctr_txt}." if ctr_txt and ctr_below else ".")]
        state = _account_state(ctx)
        if state:
            b[0] = b[0][:-1] + f" — and {state}."
        if expired:
            b.append(f"Your {expired} is paused — switching it back on is the fastest fix." if paused
                     else f"Your {expired} has expired — relaunching it is the fastest fix.")
            verb = "switch" if paused else "relaunch"
            return {"beats": b, "ask": f"Want me to {verb} {expired} {'back on ' if paused else ''}today?", "ask_hi": f"{expired} aaj hi dobara live kar doon?",
                    "action": f"relaunch {expired}", "artifact": "offer_setup", "offer": expired,
                    "anchor": "no active offer; expired offer available to relaunch"}
        if offer:
            ctx.fact(offer, "category.offer_catalog")
            b.append(pick(seed(ctx, "offerline"), {
                "dentists": [f"A clear entry offer gives new patients a reason to call — '{offer}' is the one I'd start with.",
                             f"New patients need a reason to pick up the phone; '{offer}' is a clean, clinical-sounding starting point."],
                "salons": [f"Browsers need a reason to call — a '{offer}' on the listing usually does it.",
                           f"Give people who find you a reason to book: '{offer}' is an easy one to start with."],
                "restaurants": [f"Give hungry browsers a reason to order — '{offer}' is a simple one to switch on.",
                                f"A no-brainer hook like '{offer}' turns views into orders."],
                "gyms": [f"Give walk-in prospects a reason to call — '{offer}' is the easy first step.",
                         f"A low-risk start like '{offer}' turns interest into trial visits."],
                "pharmacies": [f"A practical hook like '{offer}' gives regulars a reason to order from you, not the next store.",
                               f"'{offer}' is the simplest service to add — it answers the most common customer ask."],
            }.get(ctx.category.slug, [f"A simple offer like '{offer}' gives people a reason to call."])))
            return {"beats": b, "ask": pick(seed(ctx, "offerask"), ["Want me to set it up on your listing today?",
                                                                     "Shall I put it live on your listing today?"]),
                    "ask_hi": "Aaj hi listing par daal doon?",
                    "action": f"set up {offer}", "artifact": "offer_setup", "offer": offer,
                    "anchor": "no active offer on listing" + ("; CTR below peer" if ctr_below else "")}
    # 2. Unverified listing.
    if M.verified is False:
        b = [f"Your Google listing is still unverified{', and your CTR is ' + ctr_txt if ctr_txt and ctr_below else ''}."]
        return {"beats": b, "ask": "Want me to start verification now? It's a postcard or one phone call.",
                "action": "start GBP verification", "artifact": "verification", "anchor": "unverified GBP"}
    # 3. A rising complaint.
    if neg and (as_float(neg.get("occurrences_30d")) or 0) >= 2:
        q = review_quote(neg)
        b = [f"'{humanize_key(neg.get('theme'))}' has come up in {num(neg.get('occurrences_30d'))} reviews in 30 days"
             + (f" (\"{q}\")" if q else "") + " — that's what searchers read first."]
        return {"beats": b, "ask": cta_draft(ctx, "calm, specific replies to those reviews"),
                "action": "draft review replies", "artifact": "review_reply", "anchor": f"negative theme {neg.get('theme')}"}
    # 4. Stale posts.
    stale = M.signals.get("stale_posts") if M.has_signal("stale_posts") else None
    if stale or M.has_signal("no_recent_post"):
        b = [f"Your last Google post was {stale.replace('d', ' days')} ago" if stale else "There's been no recent Google post",
             ]
        b[0] += " — fresh posts are the cheapest way to get back into local results."
        offer = active_offer(ctx)
        return {"beats": b, "ask": cta_draft(ctx, f"a post around your {offer}" if offer else ctx.artifact_label("post")),
                "action": "draft post", "artifact": "post", "offer": offer, "anchor": "stale posts"}
    # 5. CTR gap.
    if ctr_txt and ctr_below:
        return {"beats": [f"Your CTR is {ctr_txt} — photos and the first line of your description decide that click."],
                "ask": "Want me to rewrite your description's opening line?", "action": "rewrite description",
                "artifact": "description", "anchor": "CTR below peer"}
    offer = active_offer(ctx)
    return {"beats": [f"Your {offer} is live — it just needs to be seen." if offer else "A fresh post usually lifts this within the week."],
            "ask": cta_draft(ctx, ctx.artifact_label("post")), "action": "draft post", "artifact": "post", "offer": offer,
            "anchor": "active offer to promote" if offer else "listing freshness"}


def seasonal_dip(ctx: Ctx, metric: Optional[str], delta: Optional[float]) -> Draft:
    C, M, tp = ctx.category, ctx.merchant, ctx.tp
    mw = metric_word(metric or "views")
    beat = matching_seasonal(C, [tp.get("season_note"), "acquisition retention lowest", ctx.kind])
    digest = best_digest(C, [tp.get("season_note"), "acquisition window spend"], kinds=("seasonal",))
    hook = f"{ctx.sal}, {mw} are down {pct(abs(delta))} this week — and that's the calendar, not your listing." if delta is not None \
        else f"{ctx.sal}, this week's softer numbers are the calendar, not your listing."
    beats = []
    if beat:
        beats.append(f"Apr-Jun is the slowest acquisition stretch of the year for {C.display_name.split(' &')[0].lower()} — "
                     f"the play is retention, not new sign-ups." if "retention" in str(beat.get("note", "")).lower()
                     else f"This is the usual pattern: {beat_phrase(beat)}.")
    if digest and digest.get("actionable"):
        beats.append(f"So hold ad spend now: {lcfirst(str(digest['actionable']).rstrip('.'))} ({digest.get('source')}).")
    base = member_base(ctx)
    churn, peer_churn = as_float(M.agg.get("monthly_churn_pct")), as_float(C.peer.get("monthly_churn_pct"))
    if base:
        line = f"Your {num(base[0])} {base[1]} are the asset right now"
        if churn and peer_churn and churn > peer_churn:
            line += ctx.fact(f" — monthly churn is {pct(churn)} vs {pct(peer_churn)} for peers, and that's where a lull really costs you",
                             ["merchant.customer_aggregate.monthly_churn_pct", "category.peer_stats.monthly_churn_pct"])
        beats.append(line + ".")
    thing = f"a summer attendance challenge for your {C.slug == 'gyms' and 'members' or ctx.policy.people}"
    return Draft(
        genre="seasonal_reframe", why_now=f"{mw} {pct(delta, signed=True)} in expected seasonal lull" if delta is not None else "seasonal lull",
        hook=hook, beats=beats, ask=f"Want me to draft {thing} to hold them through the dip?", cta="binary_yes_no",
        action=f"draft {thing}", artifact="retention_campaign",
        anchor=f"{num(base[0])} {base[1]}; churn above peer" if base else "seasonal pattern",
        expected_action="increase ad spend to fix the dip", adjusted_action="pause acquisition, invest in retention",
        template="vera_perf_alert_v1")


def perf_up(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    metric = tp.get("metric")
    delta = as_float(tp.get("delta_pct"))
    if not metric or delta is None:
        deltas = {m: M.delta(m) for m in ("calls", "views", "leads")}
        pos = {m: d for m, d in deltas.items() if d is not None and d > 0}
        metric = max(pos, key=pos.get) if pos else "views"
        delta = pos.get(metric)
    mw = metric_word(metric)
    base = as_float(tp.get("vs_baseline"))
    if not delta or delta <= 0:
        hook = f"{ctx.sal}, a quick look at what's working on {M.name}'s listing"
    elif delta < 0.1 and not tp.get("delta_pct"):
        hook = f"{ctx.sal}, {mw} are ticking up — {pct(delta, signed=True)} week-on-week"
    else:
        hook = f"{ctx.sal}, {mw} are up {pct(delta)} week-on-week"
    hook += f" (baseline: {num(base)})." if base else "."
    beats: List[str] = []
    driver = tp.get("likely_driver")
    plan_ref = _history_proposal(ctx)
    if driver:
        beats.append(f"The timing points to the {humanize_key(driver).replace(' post', '')} post.")
        if plan_ref and set(humanize_key(driver).replace(" post", "").split()) <= set(re.findall(r"[a-z]+", _history_proposal(ctx, full=True).lower())):
            beats.append(f"That's the program we sketched: {plan_ref}")
            ask, action, art = "Want me to publish the full camp post while interest is warm?", "publish program post", "post"
            return Draft(genre="momentum", why_now=f"{mw} +{pct(delta)}", hook=hook, beats=beats, ask=ask, cta="binary_yes_no",
                         action=action, artifact=art, anchor="spike driven by the program merchant is already planning",
                         template="vera_perf_spike_v1", extra={"proposal": plan_ref})
    # No driver: use the listing's strongest gap to convert momentum.
    ctr_txt = ctr_vs_peer(ctx)
    ctr_above = (M.metric("ctr") or 0) > (C.peer_ctr() or 1)
    if ctr_txt and ctr_above:
        beats.append(f"{'And your' if delta and delta > 0 else 'Your'} listing converts well — CTR {ctr_txt}.")
    gap_beat, ask, action, art, offer = None, None, None, "post", None
    if M.has_signal("delivery_not_set_up"):
        dl = C.catalog_offer("delivery")
        t = top_trend(C, ["delivery"])
        tt = trend_phrase(ctx, t) if t and "deliver" in str(t.get("query")) else None
        gap_beat = "The gap is delivery — you don't have it set up" + (f", while {tt}." if tt else ".")
        if dl:
            offer = dl["title"]
            ask, action, art = f"Want me to add '{offer}' to your listing?", f"add {offer}", "offer_setup"
    elif not M.active_offers:
        offer = suggested_catalog_offer(ctx)
        if offer:
            gap_beat = "But there's no live offer to catch that traffic."
            ask, action, art = f"Want me to put '{offer}' live while the traffic is up?", f"set up {offer}", "offer_setup"
    else:
        offer = active_offer(ctx)
        ask, action = cta_draft(ctx, f"a post pushing your {offer}"), "draft post"
    if gap_beat:
        beats.append(gap_beat)
    return Draft(genre="momentum", why_now=f"{mw} {pct(delta, signed=True) if delta else 'up'}", hook=hook, beats=beats,
                 ask=ask or cta_draft(ctx, ctx.artifact_label("post")), cta="binary_yes_no", action=action or "draft post",
                 artifact=art, anchor="convert spike via listing gap", template="vera_perf_spike_v1",
                 expected_action="spend more on ads" if gap_beat else "", adjusted_action=action or "",
                 extra={"offer": offer})


def _history_proposal(ctx: Ctx, full: bool = False):
    """A concrete plan Vera already proposed in conversation history
    ('4-week program, 3 classes/week, age 7-12, ₹2,499'). With full=True,
    the whole Vera message that contained it (or '')."""
    for h in reversed(ctx.merchant.history):
        if str(h.get("from", "")).lower() == "vera":
            body = str(h.get("body", ""))
            m = re.search(r"(?:Suggest|suggest|Proposed|proposed)\s+(.+?)(?:\.\s|$)", body)
            if m:
                return body if full else m.group(1).strip().rstrip(".") + "."
    return "" if full else None


# ---------------------------------------------------------------- milestone
def milestone(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    metric, now_v, target = tp.get("metric"), as_float(tp.get("value_now")), as_float(tp.get("milestone_value"))
    if metric and now_v is not None and target:
        mw = metric_word(metric)
        gap = int(target - now_v)
        if gap > 0:
            ctx.fact(f"{gap} {mw} short", ["trigger.payload.milestone_value", "trigger.payload.value_now"], derived=True, value=gap)
            hook = f"{ctx.sal}, {M.name} is {gap} {mw} away from {num(target)} ({num(now_v)} today)."
        else:
            hook = f"{ctx.sal}, {M.name} just crossed {num(target)} {mw}."
        beats = []
        pos = M.top_theme("pos")
        if pos:
            beats.append(f"Your {humanize_key(pos.get('theme')).replace(' quality', '')} is doing the talking — "
                         f"{num(pos.get('occurrences_30d'))} reviews praised it in the last 30 days.")
        who = "your weekday lunch regulars" if active_offer(ctx, "lunch", "thali") else f"your happiest regular {ctx.policy.people}"
        return Draft(genre="milestone_push", why_now=f"{gap} reviews to {num(target)}" if gap > 0 else "milestone crossed",
                     hook=hook, beats=beats, ask=f"Want me to draft a one-line review request you can send {who}?",
                     ask_hi="Review request ka ek-line draft bana doon?", cta="binary_yes_no",
                     action="draft review request", artifact="review_ask", anchor="positive review momentum",
                     template="vera_milestone_v1")
    # Placeholder milestone: find the achievement the data actually supports.
    ctr, peer = M.metric("ctr"), C.peer_ctr()
    views = M.metric("views")
    if ctr and peer and ctr >= peer * 1.5:
        ratio = ctr / peer
        ctx.fact(f"{ratio:.1f}x", ["merchant.performance.ctr", "category.peer_stats.avg_ctr"], derived=True, value=round(ratio, 1))
        hook = f"{ctx.sal}, a milestone worth knowing: {M.name}'s listing converts at {pct(ctr)} CTR — {'more than double' if ratio >= 2 else f'{ratio:.1f}x'} the {pct(peer)} average for {peer_scope(ctx)}."
        beats = []
        if views and (pg := peer_gap(ctx, "views")) and pg[2] < -0.2:
            beats.append(f"The ceiling now is reach, not conversion: {num(views)} views in 30 days vs about {num(pg[1])} for peers.")
            ask = cta_draft(ctx, "2 Google posts to lift your views")
            action = "draft 2 posts to lift reach"
        else:
            ask, action = cta_draft(ctx, "a Google post that shows off what's working"), "draft post"
        return Draft(genre="milestone_leverage", why_now="CTR milestone vs category", hook=hook, beats=beats, ask=ask,
                     cta="binary_yes_no", action=action, artifact="post", anchor="CTR far above peer; views below",
                     template="vera_milestone_v1")
    base = member_base(ctx)
    if not base:
        from .common import strongest_fact
        fact = strongest_fact(ctx)
        if not fact:
            return Draft(genre="no_evidence", why_now="milestone without data", hook=f"{ctx.sal}.", beats=[], ask="", cta="none",
                         action="none", anchor="no milestone evidence", template="vera_milestone_v1",
                         extra={"blocked": True, "block_reason": "milestone trigger with no milestone data or merchant fact"})
        hook = f"{ctx.sal}, a quick win worth using this week: {lcfirst(fact)}"
    else:
        hook = f"{ctx.sal}, {M.name} has served {num(base[0])} {base[1]} — a real base to build on."
    return Draft(genre="milestone_leverage", why_now="customer-base milestone", hook=hook,
                 beats=["The cheapest growth from here is getting the happy ones to say so publicly."],
                 ask=f"Want me to draft a one-line review request for your recent {ctx.policy.people}?",
                 cta="binary_yes_no", action="draft review request", artifact="review_ask", anchor="customer base size",
                 template="vera_milestone_v1")
