"""Customer-facing strategies (send_as = merchant_on_behalf).

Consent is checked before anything is composed. If it doesn't cover the
purpose, or the trigger carries too little data to write something true for
this customer, the strategy routes to the merchant instead (send_as = vera):
Vera asks the owner rather than guessing at a customer.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .. import consent
from ..ctx import Ctx, Draft
from ..formatting import as_float, days_between, fmt_date, humanize_key, inr, join_human, num
from ..kinds import kind_fits_category
from .common import offer_price, offer_service

_SLOT_WORDS = {
    "weekday_evening": ("weekday evening", "weekday shaam ke"),
    "weekday_after_3pm": ("weekday afternoon", "weekday dopahar 3 baje ke baad ke"),
    "saturday_morning": ("Saturday morning", "Saturday subah ke"),
    "saturday_afternoon": ("Saturday afternoon", "Saturday dopahar ke"),
    "saturday": ("Saturday", "Saturday ke"),
    "weekday_lunch": ("weekday lunch", "weekday lunch ke"),
    "morning_6am": ("early morning", "subah ke"),
    "weekday_7am": ("weekday 7am", "weekday subah 7 baje ke"),
    "evening": ("evening", "shaam ke"),
    "fri_sat_night": ("Friday/Saturday night", "Friday/Saturday raat ke"),
    "sunday_brunch": ("Sunday brunch", "Sunday brunch ke"),
    "morning_delivery": ("morning", "subah"),
}


def _L(ctx: Ctx, en: str, hinglish: str, hindi: Optional[str] = None) -> str:
    code = ctx.lang.code
    if code == "hindi":
        return hindi or hinglish
    if code == "hinglish":
        return hinglish
    return en


def _who(ctx: Ctx) -> Tuple[str, str, str]:
    """(greeting, customer_ref, self_intro) respecting relatives/guardians."""
    C, M = ctx.customer, ctx.merchant
    first = C.first_name if not C.is_anonymous else ""
    hindiish = ctx.lang.hindiish
    greet_word = ctx.lang.greeting or ("Namaste" if hindiish else "Hi")
    honor = " ji" if hindiish and first and not first.lower().startswith(("mr", "mrs", "ms")) else ""
    place = f", {M.locality}" if M.locality else ""
    owner_voiced = (ctx.category.slug in ("salons", "gyms", "restaurants") and M.owner_bare
                    and M.owner_bare.lower() not in M.name.lower())
    doctor = ctx.category.slug == "dentists" and M.owner_bare and not M.name.lower().startswith("dr")
    if owner_voiced:
        intro = _L(ctx, f"{M.owner_bare} from {M.name}{place}", f"{M.owner_bare}, {M.name}{place} se")
    elif doctor:
        intro = _L(ctx, f"Dr. {M.owner_bare} at {M.name}{place}", f"Dr. {M.owner_bare}, {M.name}{place} se")
    else:
        intro = _L(ctx, f"{M.name}{place} here", f"{M.name}{place} se")
    if C.via_relative:                          # e.g. messages go to the son's WhatsApp
        ref = f"{first}{' ji' if hindiish and not first.lower().startswith('mr') else ''}".strip() or "aap"
        ref = re.sub(r"^Mr\.?\s+(\w+)$", r"\1 ji", ref) if hindiish else ref
        return greet_word, ref, intro
    if C.guardian:                              # child customer, parent reads it
        return f"{greet_word} {C.guardian}", first, intro
    return (f"{greet_word} {first}{honor}" if first else greet_word), first, intro


def _opening(ctx: Ctx, text: str) -> str:
    e = _emoji(ctx)
    return f"{text}{e}" if e else f"{text}."


def _emoji(ctx: Ctx) -> str:
    if "yoga" in ctx.merchant.name.lower():
        return " 🧘"
    e = ctx.policy.customer_emoji
    return f" {e}" if e else ""


def _slot_pref(ctx: Ctx) -> Optional[str]:
    words = _SLOT_WORDS.get(ctx.customer.slot_pref)
    if not words:
        return None
    return words[1] if ctx.lang.hindiish else words[0]


def _returning_offer(ctx: Ctx, service_hint: str = "") -> Optional[str]:
    """An active merchant offer suitable for an existing customer: match the
    service if possible; skip offers the catalog marks as new-user only."""
    titles = ctx.merchant.active_offer_titles()
    if not titles:
        return None
    new_only = {str(o.get("title")).lower() for o in ctx.category.catalog if o.get("audience") == "new_user"}
    hint = set(re.findall(r"[a-z]{4,}", service_hint.lower()))
    for t in titles:
        if hint & set(re.findall(r"[a-z]{4,}", t.lower())):
            return t
    for t in titles:
        if t.lower() not in new_only:
            return t
    return titles[0] if ctx.customer.state in ("new",) else None


# ---------------------------------------------------------------- entry
def customer_message(ctx: Ctx) -> Draft:
    C, M = ctx.customer, ctx.merchant
    purpose = ctx.spec.consent_purpose or "followup"
    elig = consent.check(C, M.id, purpose)
    if not elig.ok:
        return consent_gap(ctx, elig.reason)
    ctx.fact(f"consent: {elig.reason}", "customer.consent")
    if ctx.placeholder and not kind_fits_category(ctx.kind, ctx.category.slug, "customer", ctx.tp):
        return approval_route(ctx)
    k = ctx.kind
    if "refill" in k:
        return refill(ctx)
    if "appointment" in k:
        return appointment(ctx)
    if "recall" in k:
        return recall(ctx)
    if "wedding" in k or "bridal" in k:
        return bridal(ctx)
    if "trial" in k or "followup" in k:
        return trial_followup(ctx)
    if "lapse" in k or "winback" in k or "churn" in k:
        return winback(ctx)
    return recall(ctx)


# ---------------------------------------------------------------- service
def recall(ctx: Ctx) -> Draft:
    C, M, tp = ctx.customer, ctx.merchant, ctx.tp
    greet, ref, intro = _who(ctx)
    service = humanize_key(tp.get("service_due", "")) if tp.get("service_due") else ""
    last = tp.get("last_service_date") or C.rel.get("last_visit")
    slots = [s.get("label") for s in (tp.get("available_slots") or []) if isinstance(s, dict) and s.get("label")]
    pref = _slot_pref(ctx)
    place = f", {M.locality}" if M.locality else ""
    em = _emoji(ctx)
    parts: List[str] = []
    if service:
        parts.append(_L(ctx, f"{greet}{em} — time for your {service} (the last one was on {fmt_date(last)}).",
                        f"{greet}{em} — aapki {service} ka time ho gaya hai (pichli {fmt_date(last)} ko hui thi)."))
    else:
        visits = as_float(C.rel.get("visits_total"))
        parts.append(_L(ctx, f"{greet}{em} — {intro}. Your last visit was on {fmt_date(last)}"
                             + (f", after {num(visits)} visits with us." if visits and visits >= 3 else "."),
                        f"{greet}{em} — {intro}. Aapki pichli visit {fmt_date(last)} ko thi"
                             + (f", {num(visits)} visits ke baad." if visits and visits >= 3 else ".")))
        reason = _care_interval(ctx)
        if reason:
            parts.append(reason)
    if slots:
        n_en, n_hi = ("two", "do") if len(slots) > 1 else ("one", "ek")
        pref_txt = f" {pref}" if pref else ""
        slot_txt = _L(ctx, join_human(slots[:2], "or"), join_human(slots[:2], "ya"))
        who = f"{M.name}{place}" if service else "We"
        parts.append(_L(ctx, f"{who} {'has' if service else 'have'} {n_en}{pref_txt} slot{'s' if len(slots) > 1 else ''} open: {slot_txt}.",
                        f"{M.name}{place} mein {n_hi}{pref_txt} slot{'s' if len(slots) > 1 else ''} khaali hain: {slot_txt}."))
    elif service:
        parts.append(_L(ctx, f"{M.name}{place} can fit you in this week.", f"{M.name}{place} mein is hafte slot mil jayega."))
    offer = _returning_offer(ctx, service or "check")
    if offer and offer_price(offer):
        svc = offer_service(offer)
        parts.append(_L(ctx, f"{svc} is {offer_price(offer)}.", f"{svc} {offer_price(offer)} mein."))
    elif offer:
        parts.append(_L(ctx, f"A {offer.replace('Free ', 'free ')} is an easy way to restart.",
                        f"Restart ke liye {offer} bilkul free."))
    if slots and len(slots) > 1:
        ask = _L(ctx, "Which one should we hold — 1 or 2? (Or send a time that suits you.)",
                 "Kaunsa rakh dein — 1 ya 2? (Ya apna time bata dijiye.)")
        cta = "multi_choice_slot"
    elif slots:
        ask = _L(ctx, "Reply YES and we'll hold it for you.", "YES reply kijiye, hum slot rakh denge.")
        cta = "binary_yes_no"
    else:
        ask = _L(ctx, "Reply YES and we'll suggest a couple of times.", "YES reply kijiye, hum do-teen time suggest kar denge.")
        cta = "binary_yes_no"
    return Draft(genre="customer_recall", why_now=f"{service or 'visit'} due", hook=parts[0], beats=parts[1:], ask=ask, cta=cta,
                 action="book recall slot", artifact="booking", send_as="merchant_on_behalf",
                 anchor=f"{C.state} customer; {C.language_pref} language; slot pref {C.slot_pref or 'n/a'}",
                 template="merchant_recall_reminder_v1", lang_applied=ctx.lang.code, extra={"slots": slots, "offer": offer})


def _care_interval(ctx: Ctx) -> Optional[str]:
    """A category-grounded reason to come back (e.g. 'every 6 months' from the
    category's own patient-education content). No outcome claims."""
    from ..formatting import MONTHS, parse_dt
    for c in ctx.category.content:
        m = re.search(r"(scaling|check-?up|cleaning)[^.]{0,20}every (\d+) months", str(c.get("body", "")), flags=re.I)
        if m:
            n = int(m.group(2))
            last = parse_dt(ctx.customer.rel.get("last_visit")) if ctx.customer else None
            if last:
                mo = MONTHS[(last.month - 1 + n) % 12]
                ctx.fact(f"due around {mo}", "derived: customer.relationship.last_visit + interval", derived=True)
                return _L(ctx, f"On the usual {n}-month rhythm, a routine check-up would fall around {mo} — worth booking ahead.",
                          f"{n} mahine ke normal routine ke hisaab se aapka agla check-up {mo} ke aas-paas hai.")
            return _L(ctx, f"A check-up and {m.group(1).lower()} every {n} months is the usual rhythm.",
                      f"Har {n} mahine mein check-up aur {m.group(1).lower()} normal routine hai.")
    return None


def appointment(ctx: Ctx) -> Draft:
    C, M, tp = ctx.customer, ctx.merchant, ctx.tp
    greet, ref, intro = _who(ctx)
    t = tp.get("time_label") or tp.get("slot_label") or tp.get("time")
    svc = humanize_key(tp.get("service")) if tp.get("service") else ""
    parts = [_opening(ctx, f"{greet}, {intro}")]
    when_en = f"tomorrow{f' at {t}' if t else ''}{f' for your {svc}' if svc else ''}"
    when_hi = f"kal{f' {t} baje' if t else ''}{f' ({svc})' if svc else ''}"
    parts.append(_L(ctx, f"Just a reminder that your appointment is {when_en}.",
                    f"Yaad dilana tha ki {when_hi} aapka appointment hai."))
    visits = as_float(C.rel.get("visits_total"))
    last = C.rel.get("last_visit")
    if C.state in ("lapsed_hard", "lapsed_soft", "churned"):
        parts.append(_L(ctx, "It's been a while — good to have you back.", "Kaafi time baad aa rahe hain — aapka phir se swagat hai."))
    elif visits:
        nth = int(visits) + 1
        suf = "th" if 10 <= nth % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(nth % 10, "th")
        ctx.fact(f"visit {nth}", "derived: customer.relationship.visits_total + 1", derived=True, value=nth)
        parts.append(_L(ctx, f"It'll be your {nth}{suf} visit with us — thank you for coming back.",
                        f"Ye aapki {nth}{suf} visit hogi — dobara aane ke liye shukriya."))
    ask = _L(ctx, "Reply YES to confirm, or tell us if you'd like to move it.",
             "Confirm karne ke liye YES reply kijiye, ya time badalna ho toh bata dijiye.")
    return Draft(genre="customer_appointment", why_now="appointment tomorrow", hook=parts[0], beats=parts[1:], ask=ask,
                 cta="binary_confirm_cancel", action="confirm appointment", artifact="booking", send_as="merchant_on_behalf",
                 anchor=f"{C.state} customer; {C.language_pref} language", template="merchant_appointment_reminder_v1",
                 lang_applied=ctx.lang.code)


def refill(ctx: Ctx) -> Draft:
    C, M, tp, Cat = ctx.customer, ctx.merchant, ctx.tp, ctx.category
    greet, ref, intro = _who(ctx)
    mols = [str(m) for m in (tp.get("molecule_list") or [])]
    runs_out = tp.get("stock_runs_out_iso") or tp.get("due_date")
    parts = []
    who_en = f"{ref}'s" if C.via_relative else "Your"
    who_hi = f"{ref} ki" if C.via_relative else "Aapki"
    if mols:
        n = len(mols)
        n_en = {2: "two", 3: "three", 4: "four"}.get(n, str(n))
        n_hi = {2: "do", 3: "teen", 4: "chaar"}.get(n, str(n))
        date_en = f" run out on {fmt_date(runs_out)}" if runs_out else " are due for a refill"
        date_hi = f" {fmt_date(runs_out)} tak khatam ho jayengi" if runs_out else " ka refill due hai"
        parts.append(_L(ctx, f"{greet} — a refill reminder: {who_en.lower() if who_en == 'Your' else who_en} {join_human(mols)}{date_en}.",
                        f"{greet} — refill reminder: {who_hi} {join_human(mols, 'aur')}{date_hi}."))
    else:
        parts.append(_L(ctx, f"{greet} — {who_en.lower() if who_en == 'Your' else who_en} regular medicines are due for a refill{f' by {fmt_date(runs_out)}' if runs_out else ''}.",
                        f"{greet} — {who_hi} regular dawaiyon ka refill due hai{f' — {fmt_date(runs_out)} tak' if runs_out else ''}."))
    perks_en, perks_hi = [], []
    senior = next((o for o in M.active_offer_titles() if "senior" in o.lower()), None)
    if senior and C.senior:
        p = re.search(r"\d+%", senior)
        perks_en.append(f"the senior-citizen {p.group() if p else ''} discount applies".replace("  ", " "))
        perks_hi.append(f"senior citizen {p.group() if p else ''} discount lagega".replace("  ", " "))
    delivery = next((o for o in M.active_offer_titles() if "deliver" in o.lower()), None)
    if delivery:
        thr = offer_price(delivery)
        saved = tp.get("delivery_address_saved") or C.prefs.get("delivery_address") == "saved"
        when = _slot_pref(ctx) if "deliver" in C.slot_pref else None
        perks_en.append(f"{when + ' ' if when else ''}delivery to the saved address{f' (free above {thr})' if thr else ''}" if saved
                        else f"free home delivery{f' above {thr}' if thr else ''}")
        perks_hi.append(f"saved address par {when + ' ' if when else ''}delivery{f' ({thr} se upar free)' if thr else ''}" if saved
                        else f"{f'{thr} se upar ' if thr else ''}free home delivery")
    if perks_en:
        parts.append(_L(ctx, f"{M.name}{', ' + M.locality if M.locality else ''} can send the same pack — {join_human(perks_en)}.",
                        f"{M.name}{', ' + M.locality if M.locality else ''} wahi pack bhej sakta hai — {join_human(perks_hi, 'aur')}."))
    else:
        parts.append(_L(ctx, f"{M.name} can keep the same pack ready.", f"{M.name} wahi pack ready rakh sakta hai."))
    # Cross-context safety: an open recall alert on one of these molecules.
    for item in Cat.digest:
        if item.get("kind") in ("alert", "supply") and "recall" in str(item.get("title", "")).lower():
            hit = next((m for m in mols if m.lower() in str(item.get("title", "")).lower()), None)
            if hit:
                parts.append(_L(ctx, f"Given the recent {hit} batch recall, we'll check the batch before it goes out.",
                                f"{hit.capitalize()} ke recent batch recall ko dekhte hue, hum batch check karke hi bhejenge."))
                ctx.fact(f"{hit} recall alert", "category.digest.alert")
                break
    ask = _L(ctx, "Shall we send it? Reply YES — and if the dose has changed, tell us before we pack.",
             "Bhej dein? YES likhiye — dose badli ho toh pack karne se pehle bata dijiye.")
    return Draft(genre="customer_refill", why_now=f"stock runs out {fmt_date(runs_out)}" if runs_out else "refill due",
                 hook=parts[0], beats=parts[1:], ask=ask, cta="binary_yes_no", action="dispatch refill",
                 artifact="booking", send_as="merchant_on_behalf",
                 anchor=f"{'senior; ' if C.senior else ''}{C.language_pref}; channel {C.prefs.get('channel')}",
                 template="merchant_refill_reminder_v1", lang_applied=ctx.lang.code, extra={"molecules": mols})


def trial_followup(ctx: Ctx) -> Draft:
    C, M, tp = ctx.customer, ctx.merchant, ctx.tp
    greet, ref, intro = _who(ctx)
    services = C.rel.get("services_received") or []
    trial = humanize_key(next((s for s in services if "trial" in str(s)), "trial")).replace(" trial", "")
    tdate = tp.get("trial_date") or C.rel.get("last_visit")
    opts = [s.get("label") for s in (tp.get("next_session_options") or tp.get("available_slots") or []) if isinstance(s, dict)]
    pref = _slot_pref(ctx)
    subject = ref if C.guardian else ""
    parts = [_opening(ctx, f"{greet}! {intro}")]
    if subject:
        parts.append(_L(ctx, f"Hope {subject} enjoyed the {trial} trial on {fmt_date(tdate)}.",
                        f"Umeed hai {subject} ko {fmt_date(tdate)} wala {trial} trial accha laga."))
    else:
        parts.append(_L(ctx, f"Hope you enjoyed your {trial} trial on {fmt_date(tdate)}.",
                        f"Umeed hai {fmt_date(tdate)} wala {trial} trial aapko accha laga."))
    if opts:
        pref_en = f" — a {pref} slot, like you prefer" if pref else ""
        parts.append(_L(ctx, f"The next session is {opts[0]}{pref_en}.", f"Agla session {opts[0]} ko hai."))
    spot = f"{subject}'s spot" if subject else "your spot"
    ask = _L(ctx, f"Reply YES and we'll hold {spot}.", f"YES reply kijiye, hum {subject + ' ki' if subject else 'aapki'} seat rakh denge.")
    return Draft(genre="customer_trial_followup", why_now="post-trial window", hook=parts[0], beats=parts[1:], ask=ask,
                 cta="binary_yes_no", action="book next session", artifact="booking", send_as="merchant_on_behalf",
                 anchor=f"new customer after trial; {C.language_pref}", template="merchant_trial_followup_v1",
                 lang_applied=ctx.lang.code, extra={"slots": opts})


def bridal(ctx: Ctx) -> Draft:
    C, M, tp = ctx.customer, ctx.merchant, ctx.tp
    greet, ref, intro = _who(ctx)
    wdate = tp.get("wedding_date") or C.prefs.get("wedding_date")
    days = tp.get("days_to_wedding")
    step = humanize_key(tp.get("next_step_window_open", "")).replace("30day", "30-day") if tp.get("next_step_window_open") else "the next prep step"
    trial = tp.get("trial_completed")
    parts = [f"{greet}{_emoji(ctx)} {intro}."]
    line = _L(ctx, f"Your wedding is on {fmt_date(wdate)}" + (f" — {num(days)} days away" if days else "") + f", so this is the right window to start your {step}.",
              f"Aapki shaadi {fmt_date(wdate)} ko hai" + (f" — {num(days)} din baaki" if days else "") + f", toh {step} shuru karne ka yahi sahi time hai.")
    parts.append(line)
    if trial:
        parts.append(_L(ctx, f"We'll build it around what we saw at your bridal trial on {fmt_date(trial)}.",
                        f"Aapke {fmt_date(trial)} wale bridal trial ke hisaab se plan karenge."))
    pref = _slot_pref(ctx)
    ask = _L(ctx, f"Reply YES and I'll share the open {pref + ' ' if pref else ''}slots for a first consult.",
             f"YES reply kijiye, hum {pref + ' ' if pref else ''}slots bhej denge.")
    return Draft(genre="customer_bridal", why_now=f"{num(days)} days to wedding" if days else "bridal prep window",
                 hook=parts[0], beats=parts[1:], ask=ask, cta="binary_yes_no", action="book skin-prep consult",
                 artifact="booking", send_as="merchant_on_behalf", anchor="bride-to-be after trial; Saturday preference",
                 template="merchant_bridal_followup_v1", lang_applied=ctx.lang.code)


# ---------------------------------------------------------------- winback
def winback(ctx: Ctx) -> Draft:
    C, M, tp = ctx.customer, ctx.merchant, ctx.tp
    greet, ref, intro = _who(ctx)
    slug = ctx.category.slug
    last = C.rel.get("last_visit")
    days = as_float(tp.get("days_since_last_visit"))
    parts = [_opening(ctx, f"{greet}, {intro}")]
    if days:
        weeks = int(round(days / 7))
        ctx.fact(f"about {weeks} weeks", "derived: trigger.payload.days_since_last_visit / 7", derived=True, value=weeks)
        gap_en, gap_hi = f"It's been about {weeks} weeks since we last saw you", f"Aapko aakhri baar dekhe hue lagbhag {weeks} hafte ho gaye"
    elif last:
        gap_en, gap_hi = f"We haven't seen you since {fmt_date(last)}", f"{fmt_date(last)} ke baad aap nahi aaye"
    else:
        gap_en, gap_hi = "It's been a while since we saw you", "Kaafi time ho gaya aapse mile hue"
    visits = as_float(C.rel.get("visits_total"))
    if slug == "gyms":
        focus = C.prefs.get("training_focus") or tp.get("previous_focus")
        months = as_float(tp.get("previous_membership_months"))
        if months and focus:
            parts.append(_L(ctx, f"You trained with us for {num(months)} months on {humanize_key(focus)} — that's a solid base to restart from.",
                            f"Aapne {num(months)} mahine {humanize_key(focus)} par mehnat ki hai — woh momentum mat khoiye."))
            focus = None
        else:
            parts.append(_L(ctx, f"{gap_en}.", f"{gap_hi}."))
        offer = _returning_offer(ctx, "trial class")
        restart = []
        if not focus and months:
            restart.append(_L(ctx, "Easy way back in", "Wapas aane ka aasaan tareeka"))
        if focus:
            restart.append(_L(ctx, f"If {humanize_key(focus)} is still the goal, we'd love to help you restart",
                              f"Agar {humanize_key(focus)} abhi bhi goal hai, toh hum restart mein madad karenge"))
        if offer:
            o = offer.replace("3 FREE Trial Classes", "3 free classes on us").replace("FREE", "free")
            restart.append(_L(ctx, o, o))
        pref = _slot_pref(ctx)
        if not restart:
            restart.append(_L(ctx, "Whenever you're ready, we'll ease you back in at your own pace",
                              "Jab bhi aap ready hon, hum aapko aaram se wapas routine mein le aayenge"))
        line = ": ".join(restart) if len(restart) > 1 else restart[0]
        if line:
            line += _L(ctx, f", on the {pref}s you used to come." if pref else ".", f", aapke {pref} time par." if pref else ".")
            parts.append(line[0].upper() + line[1:])
        pref = _slot_pref(ctx)
        ask = _L(ctx, f"Reply YES and I'll keep a {pref + ' ' if pref else ''}spot for you this week — nothing to sign.",
                 f"YES reply kijiye, hum is hafte {pref + ' ' if pref else ''}ek spot rakh denge — kuch sign nahi karna.")
    elif slug == "pharmacies":
        loyal = visits and visits >= 4
        parts.append(_L(ctx, ("You've been a regular with us, but " if loyal else "") + "we haven't seen an order from you in a while — just checking all is well.",
                        ("Aap hamare purane customer hain, par " if loyal else "") + "kaafi dino se aapka order nahi aaya — bas haal-chaal poochna tha."))
        ask = _L(ctx, "If you're on any regular medicines, reply YES and we'll keep them ready for you.",
                 "Koi regular dawai chal rahi ho toh YES reply kijiye — hum ready rakh denge.")
    else:
        fav = C.rel.get("favourite_dish")
        soft = C.state == "churned"
        parts.append(_L(ctx, gap_en + ".", gap_hi + "."))
        offer = _returning_offer(ctx)
        if slug == "restaurants" and fav:
            parts.append(_L(ctx, f"Your {fav} is still on the menu" + (f" — and {offer} is on right now." if offer else "."),
                            f"Aapka {fav} abhi bhi menu par hai" + (f" — aur abhi {offer} chal raha hai." if offer else ".")))
        elif offer:
            parts.append(_L(ctx, f"If you'd like to come back, {offer} is on right now.", f"Wapas aana ho toh abhi {offer} chal raha hai."))
        elif slug == "dentists":
            reason = _care_interval(ctx)
            if reason:
                parts.append(reason)
            parts.append(_L(ctx, "We'd be glad to see you again.", "Aapka swagat hai."))
        else:
            parts.append(_L(ctx, "We'd be glad to see you again" + (" — no pressure either way." if soft else "."),
                            "Aapko phir se dekh kar khushi hogi" + (" — koi pressure nahi." if soft else ".")))
        ask = _L(ctx, "Reply YES and we'll share a couple of times that suit you.", "YES reply kijiye, hum aapke hisaab se time bata denge.")
    return Draft(genre="customer_winback", why_now=f"customer {C.state or 'lapsed'}", hook=parts[0], beats=parts[1:], ask=ask,
                 cta="binary_yes_no", action="re-book lapsed customer", artifact="booking", send_as="merchant_on_behalf",
                 anchor=f"{C.state} customer; no-shame framing; {C.language_pref}", template="merchant_winback_v1",
                 lang_applied=ctx.lang.code)


# ---------------------------------------------------------------- routes to merchant
_PURPOSE_NOUN = {"recall": "recall reminder", "appointment": "appointment reminder", "refill": "refill reminder",
                 "followup": "follow-up", "winback": "win-back message", "promotional": "offer message"}


def consent_gap(ctx: Ctx, reason: str) -> Draft:
    C, M = ctx.customer, ctx.merchant
    first = C.first_name if C and not C.is_anonymous else "this customer"
    purpose = _PURPOSE_NOUN.get(ctx.spec.consent_purpose, "message")
    hook = f"{ctx.sal}, {first}'s {purpose} is due, but I haven't sent it — {reason}."
    beats = []
    if C and C.rel.get("visits_total"):
        beats.append(f"{first} has visited {num(C.rel.get('visits_total'))} times (last on {fmt_date(C.rel.get('last_visit'))}).")
    return Draft(genre="consent_gap", why_now=f"{purpose} due; consent missing", hook=hook, beats=beats,
                 ask=f"Want me to draft a one-line consent request you can send {first} yourself?",
                 cta="binary_yes_no", action="draft consent request", artifact="consent_request",
                 anchor="customer outreach blocked by consent scope", template="vera_customer_consent_v1",
                 extra={"blocked": True, "block_reason": reason})


def approval_route(ctx: Ctx) -> Draft:
    C, M = ctx.customer, ctx.merchant
    first = C.first_name if not C.is_anonymous else "a customer"
    purpose = _PURPOSE_NOUN.get(ctx.spec.consent_purpose, "follow-up")
    ltv = as_float(C.rel.get("lifetime_value"))
    visits = as_float(C.rel.get("visits_total"))
    facts = join_human([f"{num(visits)} visits" if visits else "", f"{inr(ltv)} lifetime with you" if ltv else ""])
    hook = f"{ctx.sal}, {first}{f' ({facts})' if facts else ''} has a {purpose} due."
    noun = {"dentists": "dental practice", "salons": "salon", "restaurants": "restaurant", "gyms": "gym"}.get(ctx.category.slug, "business")
    beats = [f"{purpose.split()[0].capitalize()}s aren't routine for a {noun}, so I'd rather check with you than message {first} cold."]
    return Draft(genre="approval_route", why_now=f"{purpose} due", hook=hook, beats=beats,
                 ask=f"If it's something you prescribed, reply YES and I'll send {first} a short reminder from {M.name} — otherwise I'll drop it.",
                 cta="binary_yes_no", action=f"send {purpose} with owner approval", artifact="customer_note",
                 anchor="customer-scoped trigger with too little data; owner approval instead of guessing",
                 template="vera_customer_approval_v1", extra={"approval_route": True})
