"""Concrete deliverables Vera produces when the merchant says yes (or has
already asked "what would it look like?").

Everything here is assembled from context facts; proposal parameters that are
not facts (a minimum order size, a posting slot) are derived from facts and
registered with the ledger (e.g. min order = free-delivery threshold / price).
"""
from __future__ import annotations

import math
import re
from typing import Optional, Tuple

from .ctx import Ctx, Draft
from .formatting import as_float, first_sentence, fmt_date, humanize_key, inr, join_human, num
from .retrieval import tokens
from .strategies.common import theme_phrase, active_offer, lcfirst, offer_price, offer_service, review_quote
from .strategies.merchant import _history_proposal


_SMALL = {"vs", "and", "of", "at", "for", "the", "in", "on", "a", "an", "to", "with"}


def _title(s: str) -> str:
    words = s.split()
    return " ".join(w if w.isupper() else (w.lower() if i and w.lower() in _SMALL else w.capitalize())
                    for i, w in enumerate(words))


# ---------------------------------------------------------------- planning trigger
def planning(ctx: Ctx) -> Draft:
    tp, M, C = ctx.tp, ctx.merchant, ctx.category
    topic_raw = tp.get("intent_topic") or ""
    topic = humanize_key(topic_raw) or "the plan you asked about"
    last = tp.get("merchant_last_message") or (M.last_from("merchant") or {}).get("body")
    ttoks = set(tokens(topic))
    offer = next((t for t in M.active_offer_titles() if ttoks & set(tokens(t))), None)
    proposal = _history_proposal(ctx)
    if offer and offer_price(offer) and re.search(r"bulk|corporate|office|catering|party|group", topic):
        return _bulk_package(ctx, topic, offer)
    if proposal:
        return _program_post(ctx, topic, proposal)
    if re.search(r"\bposts?\b", topic):
        return _post_series(ctx, topic)
    return _outline(ctx, topic, last)


def _focus_terms(topic: str):
    m = re.search(r"\bon (.+)$", topic)
    if not m:
        return []
    return [t.strip() for t in re.split(r",|&|\band\b", m.group(1)) if t.strip()]


def _post_for(ctx: Ctx, term: str) -> Optional[str]:
    """One grounded post line for a focus term, from the category's patient
    content, seasonal beats or trends (never a price the merchant doesn't offer)."""
    C, M = ctx.category, ctx.merchant
    root = term.rstrip("s")[:6].lower()
    for c in C.content:
        if root in (str(c.get("title", "")) + str(c.get("body", ""))).lower():
            return f"{c.get('title')}: {first_sentence(str(c.get('body', '')))}"
    for b in C.seasonal:
        if root in str(b.get("note", "")).lower():
            return f"{_title(term)} season is here ({b.get('month_range')}) — book early for the dates you want."
    for t in C.trends:
        if root in str(t.get("query", "")).lower():
            return f"More people nearby are asking about {term} — here's what to know before you book."
    return None


def _post_series(ctx: Ctx, topic: str) -> Draft:
    M = ctx.merchant
    place = f", {M.locality}" if M.locality else ""
    posts = []
    for term in _focus_terms(topic)[:2]:
        line = _post_for(ctx, term)
        if line:
            posts.append(f"\"{line} Book a consult at {M.name}{place}.\"")
    offer = active_offer(ctx)
    if offer:
        posts.append(f"\"{offer} at {M.name}{place} — call or WhatsApp to book.\"")
    block = "\n".join(f"{i}) {p}" for i, p in enumerate(posts, 1)) or f"\"{M.name}{place} — call or WhatsApp to book.\""
    return Draft(genre="planning_artifact", why_now="merchant asked for posts", 
                 hook=f"{ctx.sal}, here are the posts you asked for — edit anything:", beats=[block],
                 ask="Want me to schedule them across this week?", ask_hi="Is hafte ke liye schedule kar doon?",
                 cta="binary_yes_no", action="schedule the posts", artifact="publish_post",
                 anchor="continues merchant's own request", template="vera_planning_followup_v1",
                 extra={"artifact_text": block, "topic": topic})


def _bulk_package(ctx: Ctx, topic: str, offer: str) -> Draft:
    M, C = ctx.merchant, ctx.category
    price = as_float(offer_price(offer))
    svc = offer_service(offer)
    lines = [f"• {svc} @ {inr(price)} per plate — same as your menu price"]
    fd = C.catalog_offer("free delivery")
    thr = as_float(offer_price(fd["title"])) if fd else None
    if thr and price:
        n = math.ceil((thr + 1) / price)
        ctx.fact(f"{n}+ {svc.lower()}s", ["category.offer_catalog.free_delivery", "merchant.offers"], derived=True, value=n)
        ctx.fact(inr(n * price), "derived", derived=True, value=n * price)
        lines.append(f"• Free delivery on {n}+ plates ({inr(n * price)}) — suggest adding magicpin's catalog 'Free Delivery > {inr(thr)}' offer to cover it")
    lines += ["• Order by the previous evening; delivered for lunch the next day",
              "• Monthly billing for offices that order daily"]
    vol = None
    for h in M.history:
        m = re.search(r"(\d+)\s*orders?/day", str(h.get("body", "")))
        if m:
            vol = m.group(1)
    where = f" ({M.locality})" if M.locality else ""
    block = f"*Office Lunch Plan — {M.name}{where}*\n" + "\n".join(lines)
    beats = [block]
    if vol:
        beats.append(f"You're at {vol} {svc.lower().split()[-1]}s a day right now, so even one regular office order moves the needle.")
    return Draft(genre="planning_artifact", why_now="merchant asked what the package would look like",
                 hook=f"{ctx.sal}, you asked what the {topic.replace(' package', '')} would look like — here's a version built on your current menu price:", beats=beats,
                 ask="Want me to turn this into a WhatsApp message you can send to office admins nearby?",
                 ask_hi="Ise office admins ko bhejne layak WhatsApp message bana doon?", cta="binary_yes_no",
                 action="turn the plan into an outreach message", artifact="outreach_message",
                 anchor=f"built on live offer {offer}", template="vera_planning_followup_v1",
                 extra={"artifact_text": block, "offer": offer, "topic": topic})


def _program_post(ctx: Ctx, topic: str, proposal: str) -> Draft:
    M = ctx.merchant
    spec = " · ".join(p.strip().rstrip(".") for p in re.split(r",\s+", proposal) if p.strip())
    pos = [t for t in M.review_themes if str(t.get("sentiment", "")).startswith("pos")]
    proof = ""
    if pos:
        names = [humanize_key(t.get("theme")) for t in pos[:2]]
        proof = f" Known for our {join_human(names)}."
    emoji = " 🧘" if "yoga" in M.name.lower() else ""
    post = f"\"{_title(topic)} at {M.name}{', ' + M.locality if M.locality else ''}{emoji} — {spec}.{proof} Call or WhatsApp us to book a spot.\""
    when = ""
    for h in M.history:
        if "suggest" in str(h.get("body", "")).lower():
            when = fmt_date(h.get("ts"))
    return Draft(genre="planning_artifact", why_now="merchant's open planning request",
                 hook=f"{ctx.sal}, picking up the {topic} — here's a Google post built on the format I suggested{f' on {when}' if when else ''} (change anything):",
                 beats=[post], ask="Reply YES and I'll publish it today, or tell me what to change.", ask_hi="YES likhiye toh aaj hi publish kar dungi — ya bataiye kya badalna hai.", cta="binary_yes_no",
                 action="publish the program post", artifact="publish_post", anchor="continues merchant's own planning thread",
                 template="vera_planning_followup_v1", extra={"artifact_text": post, "topic": topic})


def _outline(ctx: Ctx, topic: str, last: Optional[str]) -> Draft:
    M = ctx.merchant
    offer = active_offer(ctx)
    lines = [f"• What: {topic}", f"• For: your {ctx.policy.people}{' in ' + M.locality if M.locality else ''}"]
    if offer:
        lines.append(f"• Hook: your {offer}")
    lines.append("• Channel: Google post + a WhatsApp note to your regulars")
    block = "\n".join(lines)
    return Draft(genre="planning_artifact", why_now="merchant's open planning request",
                 hook=f"{ctx.sal}, here's a first cut for the {topic} — edit anything:", beats=[block],
                 ask="Want me to write the post and the WhatsApp note from this?", cta="binary_yes_no",
                 action="write the post and WhatsApp note", artifact="post", anchor="continues merchant's planning thread",
                 template="vera_planning_followup_v1", extra={"artifact_text": block, "topic": topic})


# ---------------------------------------------------------------- post drafts
_POST_CTA = {
    "dentists": "Call or WhatsApp us to book a check-up.",
    "salons": "Call or WhatsApp us to book your slot.",
    "restaurants": "Order in, or call us to reserve a table.",
    "gyms": "WhatsApp us to book a session — come train with us.",
    "pharmacies": "WhatsApp us your prescription and we'll keep it ready.",
}
_LABEL_WORDS = re.compile(r"\b(near me|nearby|prices?|cost|best|cheap|top)\b", re.I)
_INTERNAL_TOPICS = {"what sets you apart", "thank you", "this week's top service", "next step", "the plan you asked about"}


def post_headline(ctx: Ctx, topic: str) -> str:
    """Turn a trigger/topic label into a headline a customer would read.
    Never adds a claim: it only removes query/label noise and re-cases."""
    t = (topic or "").strip().rstrip("?").strip()
    low = t.lower()
    fest = ctx.tp.get("festival")
    if fest and fest.lower() in low:
        return f"This {fest}"
    if not t or low in _INTERNAL_TOPICS:
        return ""
    t = _LABEL_WORDS.sub("", t)
    for w in (ctx.merchant.city, ctx.merchant.locality):
        if w:
            t = re.sub(r"\b" + re.escape(w) + r"\b", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -—,")
    return _title(t)


def _post(ctx: Ctx, topic: str, extra: dict, offer: Optional[str]):
    M = ctx.merchant
    place = f", {M.locality}" if M.locality else ""
    head = post_headline(ctx, topic)
    hook_offer = extra.get("offer")                       # the offer the proactive message used as its hook
    if extra.get("price") and head:
        hook_offer = f"{head} @ {inr(extra['price'])}"
    elif not hook_offer and head:
        hook_offer = next((o for o in M.active_offer_titles() if head.split()[0].lower() in o.lower()), None)
    lead = f"{head} at {M.name}{place}" if head else f"{M.name}{place}"
    parts = [lead + (f" — {hook_offer}." if hook_offer else ".")]
    pos = M.top_theme("pos")
    q = review_quote(pos)
    if q:
        parts.append(f"'{q}' — from a recent review.")
    elif pos:
        parts.append(f"{num(pos.get('occurrences_30d'))} recent reviews praise our {theme_phrase(pos.get('theme'))}.")
    parts.append(_POST_CTA.get(ctx.category.slug, "Call or WhatsApp us to book."))
    txt = "\"" + " ".join(parts) + "\""
    if head and not hook_offer and extra.get("ask_price"):
        return (txt, f"Send me your {head.lower()} price and I'll add it — or reply CONFIRM to queue it as is.", "queue the post")
    return (txt, "Reply CONFIRM and I'll queue it for your Google profile.", "queue the post")


# ---------------------------------------------------------------- execute-mode deliverables
def deliverable(ctx: Ctx, key: str, extra: dict) -> Tuple[str, str, str]:
    """Returns (artifact_text, completion_ask, next_step_label). The ask never
    re-qualifies; it only confirms the action Vera is about to take."""
    M, C, P = ctx.merchant, ctx.category, ctx.policy
    offer = extra.get("offer") or active_offer(ctx)
    item = extra.get("item") or {}
    topic = extra.get("topic") or ""
    place = f", {M.locality}" if M.locality else ""
    if key == "knowledge_note":
        summ = first_sentence(str(item.get("summary", ""))) if item else ""
        txt = (f"\"A quick update from {M.name}: {lcfirst(summ)} If you've had cavities in the last year, ask us at your "
               f"next visit whether a shorter recall makes sense for you.\"") if "caries" in summ.lower() or "cavit" in summ.lower() \
            else f"\"A quick update from {M.name}: {summ} Reply here if you'd like to know whether it applies to you.\""
        src = item.get("source")
        return (txt + (f"\n\nAbstract: {item.get('title')} — {src}." if src else ""),
                "Reply CONFIRM and I'll queue it to go to your patients.", "queue the patient note")
    if key == "compliance_checklist":
        acts = [s.strip() for s in re.split(r";|\.", str(item.get("actionable", ""))) if s.strip()]
        summ = [s.strip() for s in re.split(r"(?<=\.)\s+", str(item.get("summary", ""))) if s.strip()][:2]
        lines = [f"• {s}" for s in summ] + [f"• Action: {a}" for a in acts] + ["• Record: date checked, equipment type, signed by"]
        return (f"*{item.get('title', 'Compliance checklist')}*\n" + "\n".join(lines),
                "Reply CONFIRM and I'll keep it as your SOP draft.", "keep the SOP draft")
    if key == "recall_note":
        mol = extra.get("molecule") or "your medicine"
        b = join_human(extra.get("batches") or [])
        txt = (f"\"Namaste from {M.name}. The manufacturer has voluntarily recalled some {mol} batches{f' ({b})' if b else ''} "
               f"for lower strength — there is no safety risk, but we'd like to replace yours. Please bring your strip in, "
               f"or reply here and we'll arrange a replacement.\"")
        return (txt, "Reply CONFIRM and I'll queue it for each affected customer once the list is filtered.", "queue the recall note")
    if key in ("post", "publish_post") and not (extra.get("artifact_text") and key == "publish_post") \
            and extra.get("channel") != "delivery":
        return _post(ctx, topic, extra, offer)
    if key in ("post", "publish_post"):
        if extra.get("artifact_text") and key == "publish_post":
            return (extra["artifact_text"], "Reply CONFIRM and I'll queue it for your Google profile.", "queue the post")
        if extra.get("channel") == "delivery":
            late = M.theme("delivery_late")
            lines = f"\"{_title(topic) if topic else 'Tonight'}? Order in from {M.name}{place} — we'll deliver to your door so you don't miss a ball.\""
            note = (" (I kept it delivery-only and left out any promise on delivery time, given the late-delivery reviews.)"
                    if late else " (Delivery-only, as discussed.)")
            return (lines + note, "Reply CONFIRM and I'll queue it for your Google profile this afternoon.", "queue the post")
        live = next((t for t in M.active_offer_titles() if topic and topic.split()[0].lower() in t.lower()), None)
        hook = f"{_title(topic)} at {M.name}{place}" if topic else f"{M.name}{place}"
        if extra.get("price") and topic:
            live = f"{_title(topic)} @ {inr(extra['price'])}"
            hook = f"{M.name}{place}"
        body = f"{live} — " if live else (f"{offer} — " if offer and not topic else "")
        pos = M.top_theme("pos")
        proof = f" {num(pos.get('occurrences_30d'))} reviews this month praise our {theme_phrase(pos.get('theme'))}." if pos else ""
        txt = f"\"{hook}: {body}walk in or call to book.{proof}\""
        if topic and not live and extra.get("ask_price"):
            return (txt, f"Send me your {topic} price and I'll add it — or reply CONFIRM to post it as is.", "publish post")
        return (txt, "Reply CONFIRM and I'll queue it for your Google profile.", "queue the post")
    if key == "offer_setup":
        o = extra.get("offer") or offer or "the offer"
        return (f"Offer: {o}\nShows on: your Google profile + magicpin listing\nRuns: until you pause it",
                "Reply CONFIRM and I'll queue it for your listing.", "queue the offer")
    if key == "review_reply":
        theme = theme_phrase(extra.get("theme") or (M.top_theme("neg") or {}).get("theme", "your experience"))
        txt = (f"1) \"Thank you for flagging this — {theme} isn't the experience we want for you, and we're working on it. "
               f"We'd love another chance.\"\n2) \"Sorry about the {theme}. Please message us directly next time and we'll make it right.\"")
        return (txt, "Reply CONFIRM and I'll queue them against the matching reviews.", "queue the review replies")
    if key == "review_ask":
        txt = f"\"Thanks for coming to {M.name}! If you enjoyed it, a quick Google review helps us a lot — it only takes a moment.\""
        return (txt, "Reply CONFIRM and I'll queue it for your recent customers.", "queue the review request")
    if key in ("winback_note", "customer_note", "outreach_message"):
        if key == "outreach_message" and extra.get("artifact_text"):
            txt = (f"\"Hi! {M.name}{place} here. We now do a corporate lunch for offices nearby — "
                   f"{offer_service(offer or '') or 'our lunch'} at {offer_price(offer or '') or 'menu price'} per plate, "
                   f"delivered for lunch if you order the evening before. Want a trial order for your team this week?\"")
            return (txt, "Reply CONFIRM and I'll keep it ready for you to forward to office admins.", "keep the outreach message ready")
        hook = f"It's been a while — we'd love to see you back at {M.name}." if key == "winback_note" else f"A quick note from {M.name}{place}."
        txt = f"\"{hook}{f' {offer} is on right now.' if offer else ''} Reply here to book.\""
        return (txt, f"Reply CONFIRM and I'll queue it for your opted-in {P.people}.", "queue the customer note")
    if key == "retention_campaign":
        ctx.facts.register_many([12, 4])   # proposal parameters, not claims
        txt = ("*Summer attendance challenge*\n• Show up 12 times in 4 weeks → a free PT session\n"
               "• Leaderboard at the front desk, updated weekly\n• WhatsApp nudge to anyone who misses a week")
        return (txt, "Reply CONFIRM and I'll queue the launch message for your members.", "queue the challenge launch")
    if key == "verification":
        return ("1) On your Google Business Profile, tap 'Get verified'.\n2) Google sends a postcard or calls your listed number with a code.\n3) Enter the code on the profile — or share it here and I'll walk you through the last step.",
                "Reply once you've tapped 'Get verified' and I'll keep an eye out for the code step with you.", "track the verification steps")
    if key == "description":
        pos = M.top_theme("pos")
        txt = f"\"{M.name}{place} — {offer + '. ' if offer else ''}{'Known for ' + humanize_key(pos.get('theme')) + '.' if pos else ''}\""
        return (txt, "Reply CONFIRM and I'll queue it as your new Google description.", "queue the description")
    if key == "cde_details":
        return (f"{item.get('title', 'Session')}\n{item.get('source', '')}\n{item.get('actionable', '')}",
                "Reply CONFIRM if you plan to attend and I'll note it.", "note your attendance")
    if key == "renewal":
        plan = M.sub.get("plan") or ctx.tp.get("plan") or "current"
        amt = ctx.tp.get("renewal_amount")
        return (f"Renewal summary: {plan} plan{f', {inr(amt)}' if amt else ''}. I can't take payment in this chat — "
                f"renewal is completed from your magicpin account.",
                "Reply CONFIRM and I'll note that you want to renew.", "note the renewal request")
    if key == "consent_request":
        return (f"\"Hi! This is {M.name}. Can we send you reminders and updates on WhatsApp? Reply YES to opt in.\"",
                "Reply CONFIRM and I'll keep it ready for you to forward.", "keep the consent message ready")
    return (f"Draft for {M.name}: {topic or 'the next step we discussed'}.", "Reply CONFIRM and I'll queue it.", "queue it")
