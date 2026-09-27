"""Multi-turn conversation handling.

classify() turns a reply into an intent with deterministic rules (Hindi,
Hinglish and English cues). respond() is a small state machine over explicit
modes. Guiding rules:
  * explicit yes -> EXECUTE immediately: deliver the artifact, never re-qualify
  * canned auto-reply -> back off once, then end (tracked per merchant, across
    conversations, because a WA Business auto-reply fires on every thread)
  * stop / not interested -> end, no message, merchant marked do-not-contact
  * off-topic -> say honestly it's outside Vera's scope, one-line redirect
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import artifacts, language
from .ctx import Ctx
from .formatting import fmt_date, humanize_key, inr, num
from .policies import VERA_CAPABILITIES
from .validate import similarity, validate

# ---------------------------------------------------------------- state
MODES = ("DISCOVER", "PROPOSE", "EXECUTE", "HANDLE_OBJECTION", "ANSWER", "WAIT", "END")


@dataclass
class Conversation:
    id: str
    merchant_id: Optional[str]
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    send_as: str = "vera"
    mode: str = "PROPOSE"
    plan: Dict[str, Any] = field(default_factory=dict)   # family, genre, artifact, action, extra, cta
    turns: List[Dict[str, Any]] = field(default_factory=list)
    bot_bodies: List[str] = field(default_factory=list)
    executed: bool = False       # artifact delivered, awaiting CONFIRM
    completed: bool = False      # action confirmed and done
    objections: int = 0
    auto_replies: int = 0
    ended: bool = False
    end_reason: str = ""
    lang: str = "en"
    created: float = field(default_factory=time.time)

    def add(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text, "ts": time.time()})
        if len(self.turns) > 40:
            self.turns = self.turns[-40:]

    @property
    def audience(self) -> str:
        return "customer" if self.send_as == "merchant_on_behalf" else "merchant"


@dataclass
class MerchantMemory:
    merchant_id: str
    reply_texts: Dict[str, int] = field(default_factory=dict)   # normalised text -> count
    auto_reply_strikes: int = 0
    auto_reply_last: float = 0.0
    do_not_contact: bool = False
    dnc_reason: str = ""
    declined_families: set = field(default_factory=set)
    sent_bodies: List[str] = field(default_factory=list)
    last_proactive: Optional[str] = None      # ISO 'now' of last proactive send
    hold_until: Optional[str] = None          # merchant asked for time: no proactive sends before this
    engaged: bool = False
    agreed: List[str] = field(default_factory=list)
    lang: Optional[str] = None


# ---------------------------------------------------------------- classify
_AUTO = [
    r"thank(s| you) for (contacting|reaching|your message|messaging)", r"(our|the) team will (get back|respond|contact|reach)",
    r"will (get back|respond|revert) (to you )?(shortly|soon|asap)", r"respond shortly", r"(this is|i am|i'm) an? (automated|auto)",
    r"auto[- ]?reply", r"automated (message|response|assistant)", r"out of (the )?office", r"currently (unavailable|away|closed)",
    r"business hours", r"we are closed", r"away from (the )?(phone|desk)", r"aapki jaankari ke liye", r"team tak pahuncha",
    r"hum jald hi (aapse )?sampark", r"message (has been )?received", r"we have received your (message|query)",
    r"welcome to .{2,40}[.!]\s*(visit|call|book|order|reach)", r"(during|within) (our )?(working|business|opening) hours",
]
_OPT_OUT = [r"\bstop\b", r"unsubscribe", r"don'?t (message|text|contact|send)", r"do not (message|text|contact|send)",
            r"not interested", r"no interest", r"band karo", r"mat bhejo", r"remove me", r"leave me alone",
            r"never (message|contact)", r"block (you|this)"]
_HOSTILE = [r"\bspam\b", r"useless", r"bothering", r"waste of (my )?time", r"\bfraud\b", r"\bscam\b", r"idiot", r"stupid",
            r"bakwas", r"pagal", r"nonsense", r"shut up", r"get lost", r"irritat", r"annoying", r"\bdamn\b", r"bloody",
            r"\bf+u+c+k", r"\bshit\b", r"harass"]
_OFF_TOPIC = {
    r"\bgst\b|\bitr\b|income tax|tax (filing|return)|file (my )?tax": ("GST/tax filing", "your CA"),
    r"\bloan\b|credit card|\bemi\b": ("loans and credit", "your bank"),
    r"insurance": ("insurance", "your insurance advisor"),
    r"legal|lawyer|court|notice from": ("legal matters", "a lawyer"),
    r"electricity|water bill|rent|landlord": ("utility and rent issues", "your provider"),
    r"salary|payroll|staff hiring|hire staff|recruit": ("hiring and payroll", "a staffing service"),
    r"website (development|banana)|build (me )?an app|develop (an )?app": ("building websites or apps", "a web developer"),
    r"visa|passport|train ticket|flight": ("travel bookings", "a travel service"),
    r"weather|temperature today|rain today": ("weather updates", "a weather app"),
    r"\bjoke\b|funny|entertain": ("jokes and chit-chat", "someone better at it than me"),
    r"bitcoin|crypto|share market|stock market|\bstocks?\b|mutual fund|invest": ("investment questions", "a financial advisor"),
    r"cricket score|match score|who won|election|politics|news today": ("news and scores", "a news app"),
    r"recipe|cook (for|at) home|homework|poem|write (me )?an essay": ("things outside your business", "a general assistant"),
}
_DOMAIN = re.compile(r"offer|listing|google|profile|post|review|customer|patient|client|member|campaign|call|view|ctr|"
                     r"order|booking|slot|price|draft|message|whatsapp|vera|magicpin|plan|renew|verif|this|it|that", re.I)
_LATER = [r"\blater\b", r"\bbusy\b", r"not now", r"baad mein", r"baad me", r"\bkal\b", r"tomorrow", r"next week", r"in a meeting",
          r"call (you|me) back", r"give me (some )?time", r"abhi nahi", r"thodi der", r"after some time", r"will (check|see|revert)",
          r"let me think", r"sochta|soch ke", r"get back to you"]
_ACCEPT = [r"^(yes|yeah|yep|yup|ya|haan|han|ha|ji|ji haan|ok|okay|okk|k|sure|done|confirm|confirmed|go|go ahead|proceed|chalo|chalega|theek hai|thik hai|accha|acha|fine)\b",
           r"let'?s do (it|this)", r"lets do (it|this)", r"\bdo it\b", r"go ahead", r"please (do|proceed|send|go)", r"send (it|me|the)",
           r"kar do", r"kardo", r"bhej do", r"bhejo", r"karo", r"start (it|now)", r"\bjoin\b", r"activate", r"book (it|me|a)",
           r"what'?s next", r"whats next", r"sounds good", r"\bperfect\b", r"\bagreed\b", r"i want (to|it)", r"mujhe .*(chahiye|judna|karna)",
           r"\bpublish\b", r"\bschedule it\b", r"\bupdate it\b", r"\bcreate it\b", r"\brun it\b",
           r"\bconfirm(ed)?\b", r"\bpost (it|them|the)\b", r"\bgo live\b", r"\bmake it live\b", r"\blive kar\b"]
_DECLINE = [r"^(no|nope|nah|nahi|nahin|na)\b", r"no thanks", r"not needed", r"don'?t need", r"we'?re (fine|good|ok)", r"zarurat nahi",
            r"nahi chahiye", r"skip (it|this)", r"not required"]
_OBJ_COST = [r"expensive", r"costly", r"mehenga", r"mahenga", r"budget", r"too much", r"can'?t afford", r"paisa nahi"]
_OBJ_DONE = [r"already (doing|done|have|did|running|posted|live)", r"we (already )?do (this|that)", r"pehle se", r"did that already"]
_OBJ_TRIED = [r"\btried\b", r"didn'?t work", r"doesn'?t work", r"no (results|use|benefit)", r"kaam nahi (kiya|karta)", r"koi fayda nahi"]
_OBJ_TIME = [r"no time", r"time nahi", r"don'?t have time", r"too busy"]
_Q_PRICE = [r"how much", r"price", r"\bcost", r"charge", r"\bfee", r"kitna", r"kitne", r"paisa", r"rate\b"]
_Q_SHOW = [r"show me", r"can you show", r"example", r"sample", r"send (the )?details", r"what would it look like", r"look like",
           r"preview", r"dikhao", r"bhejo details", r"send me (the )?(draft|details|abstract|list)"]
_Q_HOW = [r"^how\b", r"what do i need", r"what (all )?is needed", r"steps", r"kaise", r"process", r"what do you need"]
_Q_WHO = [r"who (are|is) (you|this)", r"kaun ho", r"who r u", r"what is vera"]
_Q_SOURCE = [r"where (did|do) you get", r"how do you know", r"source of", r"data kahan"]
_Q_WHAT = [r"^what exactly", r"what (is|are) (this|that|it)\b", r"what do you mean", r"explain", r"matlab", r"what will you do",
           r"what('s| is) the plan", r"details\?"]
_Q_WHY = [r"^why\b", r"\bwhy (should|would|do|is)\b", r"kyun", r"kyon", r"what for"]
_Q_TRUST = [r"\btrust\b", r"why should i", r"\blegit\b", r"genuine", r"bharosa", r"is this real", r"are you real"]
_THANKS = [r"^(thanks|thank you|thx|ty|shukriya|dhanyavad|dhanyawad)\b", r"\bthank(s| you)\b"]
_GREET = [r"^(hi|hello|hey|namaste|hii+|helo)\b\W*$"]


def _any(patterns, text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9ऀ-ॿ ]+", "", (text or "").lower()).strip()


@dataclass
class Intent:
    label: str
    detail: Any = None


def classify(text: str, conv: Conversation, mem: MerchantMemory) -> Intent:
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return Intent("info")
    norm = normalise(t)
    words = len(norm.split())
    canned = _any(_AUTO, low)
    repeated = words >= 4 and mem.reply_texts.get(norm, 0) >= 1
    if canned or repeated:
        return Intent("auto_reply", "canned" if canned else "repeated")
    if _any(_OPT_OUT, low):
        return Intent("opt_out")
    if _any(_HOSTILE, low):
        return Intent("hostile")
    for pat, (topic, who) in _OFF_TOPIC.items():
        if re.search(pat, low):
            return Intent("off_topic", (topic, who))
    if conv.audience == "customer" and conv.plan.get("extra", {}).get("slots"):
        choice = _slot_choice(low, conv.plan["extra"]["slots"])
        if choice:
            return Intent("slot_choice", choice)
    if _any(_OBJ_TIME, low):
        return Intent("objection_time")
    if _any(_LATER, low) and not _any(_ACCEPT[1:], low):
        return Intent("later", _wait_seconds(low))
    if _any(_Q_SHOW, low):
        return Intent("show")
    if _any(_OBJ_COST, low):
        return Intent("objection_cost")
    if _any(_OBJ_DONE, low):
        return Intent("objection_done")
    if _any(_OBJ_TRIED, low):
        return Intent("objection_tried")
    is_q = "?" in t or bool(re.match(r"^(what|how|why|when|which|who|where|can|could|is|are|do|does|kya|kaise|kitna|kab|kyun|kaun)\b", low))
    if _any(_ACCEPT, low):
        if is_q and _any(_Q_PRICE, low):
            return Intent("question_price")
        return Intent("accept")
    if _any(_DECLINE, low):
        return Intent("decline")
    if _any(_THANKS, low) and words <= 6:
        return Intent("thanks")
    if _any(_GREET, low):
        return Intent("greeting")
    if is_q:
        if _any(_Q_WHO, low):
            return Intent("question_who")
        if _any(_Q_SOURCE, low):
            return Intent("question_source")
        if _any(_Q_TRUST, low):
            return Intent("question_trust")
        if _any(_Q_WHY, low):
            return Intent("question_why")
        if _any(_Q_WHAT, low):
            return Intent("question_what")
        if not _DOMAIN.search(low):
            return Intent("off_topic", ("that", "someone better placed"))
        if _any(_Q_PRICE, low):
            return Intent("question_price")
        if _any(_Q_HOW, low):
            return Intent("question_how")
        return Intent("question")
    return Intent("info", t)


def _extract_service(answer: str, ctx: Optional[Ctx]) -> str:
    """'Mostly keratin these days' -> 'keratin' (category vocab / catalog words
    first, then the answer minus filler words)."""
    low = answer.lower()
    if ctx is not None:
        vocab = list(ctx.category.vocab_allowed) + [str(o.get("title", "")).split(" @")[0] for o in ctx.category.catalog]
        hits = sorted({v for v in vocab if v and v.lower() in low}, key=len, reverse=True)
        if hits:
            return hits[0].lower()
    filler = {"mostly", "these", "days", "this", "week", "the", "is", "are", "people", "asking", "for", "most", "i", "think",
              "probably", "yes", "our", "we", "lot", "of", "a", "and", "hai", "zyada", "sabse", "log"}
    words = [w for w in re.findall(r"[a-zA-Z]+", low) if w not in filler]
    return " ".join(words[:3]) or "this week's top service"


def _slot_choice(low: str, slots: List[str]) -> Optional[str]:
    m = re.match(r"^\s*(\d)\b", low)
    if m and 1 <= int(m.group(1)) <= len(slots):
        return slots[int(m.group(1)) - 1]
    for s in slots:
        day = s.split()[0].lower()[:3]
        if re.search(rf"\b{day}", low):
            return s
    return None


def _wait_seconds(low: str) -> int:
    if re.search(r"next week|agle hafte", low):
        return 7 * 86400
    if re.search(r"tomorrow|\bkal\b", low):
        return 86400
    m = re.search(r"(\d+)\s*(hour|hr|ghant)", low)
    if m:
        return min(int(m.group(1)), 48) * 3600
    m = re.search(r"(\d+)\s*(min|minute)", low)
    if m:
        return max(300, min(int(m.group(1)), 600) * 60)
    if re.search(r"evening|shaam|tonight|raat", low):
        return 4 * 3600
    if re.search(r"meeting|busy|thodi der", low):
        return 2 * 3600
    return 3 * 3600


# ---------------------------------------------------------------- respond
def _L(lang: str, en: str, hi: str) -> str:
    return hi if lang in ("hinglish", "hindi") else en


def _send(body: str, cta: str, rationale: str) -> dict:
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _wait(seconds: int, rationale: str) -> dict:
    return {"action": "wait", "wait_seconds": int(seconds), "rationale": rationale}


def _end(rationale: str) -> dict:
    return {"action": "end", "rationale": rationale}


def respond(conv: Conversation, mem: MerchantMemory, text: str, ctx: Optional[Ctx]) -> dict:
    """Pure-ish decision for one inbound message. `ctx` is a composition
    context rebuilt from the latest stored contexts (may be None if the
    merchant is unknown)."""
    detected = language.detect(text)
    if detected in ("hindi", "hinglish"):
        conv.lang = "hinglish"
    elif len((text or "").split()) >= 3:
        conv.lang = "en"
    lang = conv.lang
    intent = classify(text, conv, mem)
    norm = normalise(text)
    if norm:
        mem.reply_texts[norm] = mem.reply_texts.get(norm, 0) + 1
    name = ""
    if ctx:
        name = ctx.sal if conv.audience == "merchant" else (ctx.customer.first_name if ctx.customer else "")

    # A merchant-initiated message after we ended re-opens the thread politely
    # (except auto-replies and repeated opt-outs).
    if conv.ended and intent.label not in ("auto_reply", "opt_out", "hostile", "off_topic", "question_who"):
        if mem.do_not_contact:
            return _end("Conversation closed at the merchant's request; not re-engaging")
        conv.ended = False

    handler = _HANDLERS.get(intent.label, _handle_info)
    out = handler(conv, mem, text, ctx, intent, lang, name)
    if out["action"] == "send":
        out = _guard(conv, ctx, out, lang)
    if out["action"] == "end":
        conv.ended, conv.end_reason, conv.mode = True, out["rationale"], "END"
    elif out["action"] == "wait":
        conv.mode = "WAIT"
    return out


def _guard(conv: Conversation, ctx: Optional[Ctx], out: dict, lang: str) -> dict:
    """Validate a reply: grounding, taboos, no re-qualifying in EXECUTE, no
    repeats. On failure, fall back to a minimal safe message or end."""
    body = out["body"]
    if ctx is not None:
        v = validate(body, facts=ctx.facts, taboos=ctx.category.taboos + ctx.policy.extra_taboos,
                     audience=conv.audience, cta=out.get("cta", "open_ended"), lang_code=lang,
                     prior_bodies=conv.bot_bodies, mode="execute" if conv.mode == "EXECUTE" else "reply",
                     strict_names=False)
        if not v.ok:
            repeat = any("repeat" in i or "duplicate" in i for i in v.issues)
            if repeat and conv.executed:
                return _end("Nothing new to add without repeating myself; closing politely")
            safe = _L(lang, "Noted — I'll take it from here and keep you posted on this thread.",
                      "Theek hai — main aage sambhal leti hoon, isi chat par update dungi.")
            if safe in conv.bot_bodies:
                return _end("Avoiding a repeated message; nothing new to add")
            out = _send(safe, "none", out["rationale"] + f" (reply repaired: {v.issues[:2]})")
    conv.bot_bodies.append(out["body"])
    return out


# ---------------------------------------------------------------- handlers
def _handle_auto(conv, mem, text, ctx, intent, lang, name):
    conv.auto_replies += 1
    mem.auto_reply_strikes += 1
    mem.auto_reply_last = time.time()
    if mem.auto_reply_strikes == 1 and conv.auto_replies == 1:
        return _wait(4 * 3600, f"Detected WhatsApp Business auto-reply ({intent.detail}); backing off 4h for the owner "
                               "instead of spending a turn arguing with a bot")
    return _end(f"Auto-reply seen {mem.auto_reply_strikes}x for this merchant with no human reply; closing gracefully "
                "and pausing proactive sends to this number")


def _handle_opt_out(conv, mem, text, ctx, intent, lang, name):
    mem.do_not_contact, mem.dnc_reason = True, "merchant opted out"
    return _end("Merchant asked to stop / not interested; ending without further messages and suppressing future sends")


def _handle_hostile(conv, mem, text, ctx, intent, lang, name):
    mem.do_not_contact, mem.dnc_reason = True, "merchant hostile"
    if conv.ended:
        return _end("Merchant still frustrated after close; staying silent")
    body = _L(lang, f"Sorry for the bother{', ' + name if name else ''} — I'll stop these updates. If you ever want help with your listing, just message 'Hi Vera'.",
              f"Maaf kijiye{', ' + name if name else ''} — main ye updates band kar rahi hoon. Kabhi listing mein madad chahiye ho, bas 'Hi Vera' likh dijiye.")
    out = _send(body, "none", "Merchant frustrated; one-line apology with an opt-back-in path, then closing")
    conv.ended = True
    return out


def _handle_off_topic(conv, mem, text, ctx, intent, lang, name):
    topic, who = intent.detail
    base_en = f"{topic.capitalize()} is outside what I can help with — {who} is the right person for that."
    base_hi = f"{topic.capitalize()} mere scope ke bahar hai — iske liye {who} sahi rahenge."
    if mem.do_not_contact or conv.ended:
        body = _L(lang, base_en + " I've paused my updates as you asked; message 'Hi Vera' anytime.",
                  base_hi + " Aapke kehne par maine updates rok diye hain; kabhi bhi 'Hi Vera' likh dijiye.")
        return _send(body, "none", "Off-topic ask after close: honest scope limit, no pitch")
    action = conv.plan.get("action")
    if action and not conv.completed:
        redirect_en = f" Meanwhile, I can still {action} for you — just say YES."
        redirect_hi = f" Waise, main abhi bhi ye kar sakti hoon: {action} — bas YES bol dijiye."
    else:
        redirect_en = f" What I can do: {VERA_CAPABILITIES[0].split(' (')[0]}, offers, and review replies."
        redirect_hi = " Main Google profile, offers aur review replies mein madad kar sakti hoon."
    return _send(_L(lang, base_en + redirect_en, base_hi + redirect_hi), "binary_yes_no" if action else "open_ended",
                 f"Off-topic ask ({topic}) declined honestly without inventing capabilities; one-line redirect to the open thread")


def _handle_later(conv, mem, text, ctx, intent, lang, name):
    return _wait(intent.detail, f"Merchant asked for time; backing off {intent.detail // 60} min without another nudge")


def _handle_decline(conv, mem, text, ctx, intent, lang, name):
    fam = conv.plan.get("family")
    if fam:
        mem.declined_families.add(fam)
    if conv.bot_bodies and conv.objections == 0 and conv.audience == "merchant":
        conv.objections += 1
        body = _L(lang, f"No problem{', ' + name if name else ''} — I'll leave this one. I won't bring it up again.",
                  f"Koi baat nahi{', ' + name if name else ''} — ise yahin chhod dete hain, dobara nahi poochungi.")
        out = _send(body, "none", "Merchant declined; acknowledging and closing this topic (suppressed for future sends)")
        conv.ended = True
        return out
    return _end("Merchant declined; closing gracefully")


def _handle_thanks(conv, mem, text, ctx, intent, lang, name):
    if conv.completed or conv.executed or not conv.plan.get("action"):
        return _end("Merchant said thanks after the task; nothing further needed — closing")
    return _handle_accept(conv, mem, text, ctx, intent, lang, name) if conv.mode == "PROPOSE" else _end("Closing on thanks")


def _handle_accept(conv, mem, text, ctx, intent, lang, name):
    mem.engaged = True
    if conv.audience == "customer":
        return _customer_accept(conv, ctx, lang)
    if conv.completed:
        return _end("Task already completed and confirmed; closing instead of re-pitching")
    if conv.plan.get("genre") == "curious_ask" and not conv.executed and (conv.plan.get("extra") or {}).get("guess"):
        return _execute(conv, mem, ctx, lang, "Merchant confirmed the guessed top service; turning it into the promised post",
                        topic_override=conv.plan["extra"]["guess"])
    if conv.executed:
        conv.completed, conv.mode = True, "END"
        label = conv.plan.get("next_step", "proceed")
        mem.agreed.append(label)
        body = _L(lang, f"Approved — {_done_phrase(label)}. Nothing else needed from you on this one.",
                  f"Approve ho gaya — {_done_phrase(label, hi=True)}. Aapko ab kuch nahi karna.")
        return _send(body, "none", f"Merchant confirmed; recorded the approval ({label}) — no external side effect claimed")
    return _execute(conv, mem, ctx, lang, "Merchant gave explicit go-ahead; switching straight to execution (no re-qualifying)")


_ING = {"set": "setting", "put": "putting", "run": "running", "get": "getting"}


def _done_phrase(label: str, hi: bool = False) -> str:
    """State after approval, in terms of what this system actually did:
    queued / kept / noted — never 'sent', 'posted' or 'live'."""
    words = label.split(" ", 1)
    verb, obj = words[0], (words[1] if len(words) > 1 else "it")
    past = {"queue": "queued", "keep": "kept ready", "note": "noted", "track": "tracking"}.get(verb, "recorded")
    if hi:
        hi_past = {"queue": "queue kar diya hai", "keep": "ready rakh diya hai", "note": "note kar liya hai",
                   "track": "track kar rahi hoon"}.get(verb, "record kar liya hai")
        return f"maine {hi_past} ({obj})"
    if verb == "track":
        return f"I'm tracking {obj}"
    be = "are" if re.search(r"(replies|posts|steps|notes|messages)$", obj) else "is"
    return f"{obj} {be} {past}"


def _gerund(phrase: str) -> str:
    """'post review replies' -> 'posting the review replies'; 'activate offer' -> 'activating the offer'."""
    words = phrase.split()
    if not words:
        return "on it"
    v = words[0].lower()
    ing = _ING.get(v) or (v[:-1] + "ing" if v.endswith("e") and not v.endswith("ee") else v + "ing")
    rest = " ".join(words[1:])
    if rest and not re.match(r"(the|a|an|your|it|them)\b", rest):
        rest = "the " + rest
    return f"{ing} {rest}".strip()


def _execute(conv, mem, ctx, lang, rationale, topic_override: Optional[str] = None):
    conv.mode = "EXECUTE"
    if ctx is None:
        body = _L(lang, "Done — I'm on it. Here's what happens next: I prepare the draft, share it here, and you approve with one reply.",
                  "Ho gaya — main shuru kar rahi hoon. Draft yahin bhejungi, aap bas ek reply mein approve kar dijiye.")
        conv.executed = True
        return _send(body, "binary_confirm_cancel", rationale)
    key = conv.plan.get("artifact") or "post"
    if topic_override:
        conv.plan.setdefault("extra", {})["topic"] = topic_override
        conv.plan["extra"]["ask_price"] = True      # the curious-ask promised "with your price"
    extra = dict(conv.plan.get("extra") or {})
    text, ask, next_step = artifacts.deliverable(ctx, key, extra)
    conv.plan["next_step"] = next_step
    conv.executed = True
    summary_keys = {"renewal", "verification", "cde_details", "offer_setup"}
    intro = (_L(lang, "Here's the summary:", "Ye raha summary:") if key in summary_keys
             else _L(lang, "Done — here's the draft:", "Ho gaya — ye raha draft:"))
    ask_txt = ask if lang == "en" else "Theek lage toh CONFIRM reply kijiye — main aage badha dungi."
    return _send(f"{intro}\n\n{text}\n\n{ask_txt}", "binary_confirm_cancel", rationale)


def _customer_accept(conv, ctx, lang):
    slots = conv.plan.get("extra", {}).get("slots") or []
    mname = ctx.merchant.name if ctx else "us"
    if conv.completed:
        return _end("Customer already confirmed; closing")
    if len(slots) == 1:
        return _book(conv, ctx, slots[0], lang)
    if len(slots) > 1:
        body = _L(lang, f"Great! Which one works — 1) {slots[0]} or 2) {slots[1]}? Just reply 1 or 2.",
                  f"Badhiya! Kaunsa slot theek rahega — 1) {slots[0]} ya 2) {slots[1]}? Bas 1 ya 2 reply kijiye.")
        return _send(body, "multi_choice_slot", "Customer said yes but two slots are open; asking which (needed to book)")
    conv.completed = True
    genre = conv.plan.get("genre", "")
    if "refill" in genre:
        body = _L(lang, f"Thank you — {mname} will pack the same medicines and send them to the saved address. We'll message when it's out for delivery.",
                  f"Dhanyavaad — {mname} wahi dawaiyan pack karke saved address par bhej dega. Delivery nikalte hi message karenge.")
    elif "appointment" in genre:
        body = _L(lang, f"Confirmed ✅ See you tomorrow at {mname}.", f"Confirm ho gaya ✅ Kal milte hain, {mname} par.")
    else:
        body = _L(lang, f"Lovely — {mname} will message you shortly with a couple of times to pick from.",
                  f"Badhiya — {mname} jaldi hi aapko do-teen time bhejega.")
    return _send(body, "none", "Customer accepted; confirming the next concrete step")


def _book(conv, ctx, slot, lang):
    mname = ctx.merchant.name if ctx else "us"
    conv.completed = True
    body = _L(lang, f"Booked: {slot} at {mname}. We'll send a reminder the day before — reply here if anything changes.",
              f"Book ho gaya: {slot}, {mname} par. Ek din pehle reminder bhejenge — kuch badle toh yahin bata dijiye.")
    return _send(body, "none", f"Customer picked a slot ({slot}); confirming the booking")


def _handle_slot(conv, mem, text, ctx, intent, lang, name):
    return _book(conv, ctx, intent.detail, lang)


def _handle_show(conv, mem, text, ctx, intent, lang, name):
    if conv.audience == "customer":
        return _handle_question(conv, mem, text, ctx, intent, lang, name)
    return _execute(conv, mem, ctx, lang, "Merchant asked to see it; delivering the concrete draft instead of describing it")


def _handle_cost(conv, mem, text, ctx, intent, lang, name):
    conv.objections += 1
    if conv.objections > 2:
        return _end("Repeated objections; closing respectfully")
    plan_name = ctx.merchant.sub.get("plan") if ctx else None
    active = ctx and str(ctx.merchant.sub.get("status", "")).lower() in ("active", "trial")
    if conv.plan.get("family") == "account" and ctx:
        amt = ctx.tp.get("renewal_amount")
        calls, leads = ctx.merchant.metric("calls"), ctx.merchant.metric("leads")
        if amt and (calls or leads):
            body = _L(lang, f"Fair question. The plan is {inr(amt)}; in the last 30 days your listing brought {num(calls)} calls and {num(leads)} leads. If that isn't worth it to you, skipping is a fine call.",
                      f"Sahi sawaal. Plan {inr(amt)} ka hai; pichle 30 din mein listing se {num(calls)} calls aur {num(leads)} leads aaye. Agar worth nahi lagta, toh skip karna bilkul theek hai.")
            return _send(body, "none", "Cost objection on renewal: honest numbers, no pressure")
    body = _L(lang, ("There's no extra cost for this — preparing it is part of your " + (f"{plan_name} plan" if active and plan_name else "Vera support") +
                     ". You only approve what goes out. Say YES and I'll draft it."),
              ("Iska koi extra charge nahi hai — ye aapke " + (f"{plan_name} plan" if active and plan_name else "Vera support") +
               " mein shamil hai. Jo jaayega woh aap approve karenge. YES boliye, main draft kar deti hoon."))
    conv.mode = "HANDLE_OBJECTION"
    return _send(body, "binary_yes_no", "Cost objection: clarified there's no added cost for the draft; single low-friction ask")


def _handle_done(conv, mem, text, ctx, intent, lang, name):
    conv.objections += 1
    fam = conv.plan.get("family")
    if fam:
        mem.declined_families.add(fam)
    body = _L(lang, f"Good — then you're ahead of it{', ' + name if name else ''}. I'll drop this one and only come back when there's something new.",
              f"Badhiya — aap pehle se kar rahe hain{', ' + name if name else ''}. Main ise chhod deti hoon, kuch naya hoga tabhi aaungi.")
    out = _send(body, "none", "Merchant already doing it; no redundant pitch — closing the topic")
    conv.ended = True
    return out


def _handle_tried(conv, mem, text, ctx, intent, lang, name):
    conv.objections += 1
    if conv.objections > 2:
        return _end("Repeated objections; closing respectfully")
    anchor = conv.plan.get("anchor_short") or "your own numbers"
    body = _L(lang, f"Fair — a lot of generic versions don't. This one is built on {anchor}, and it's small enough to test for a week with no risk. Say YES and I'll set it up as a trial.",
              f"Sahi baat — generic cheezein aksar kaam nahi karti. Ye {anchor} par based hai, aur ek hafte ka chhota test hai. YES boliye, main trial set kar deti hoon.")
    conv.mode = "HANDLE_OBJECTION"
    return _send(body, "binary_yes_no", "'Tried before' objection: distinguished this from generic attempts; offered a small reversible test")


def _handle_time(conv, mem, text, ctx, intent, lang, name):
    conv.objections += 1
    body = _L(lang, "That's exactly why I'll do it — it needs one reply from you, nothing else. Say YES and I'll send the finished draft here.",
              "Isiliye main kar deti hoon — aapka bas ek reply chahiye, aur kuch nahi. YES boliye, main tayyar draft yahin bhej dungi.")
    conv.mode = "HANDLE_OBJECTION"
    return _send(body, "binary_yes_no", "No-time objection: externalised the effort to a single reply")


def _handle_price(conv, mem, text, ctx, intent, lang, name):
    if conv.audience == "customer" and ctx:
        offer = (conv.plan.get("extra") or {}).get("offer")
        if offer:
            return _send(_L(lang, f"It's {offer} at {ctx.merchant.name}. Reply YES and we'll book you in.",
                            f"{offer} hai, {ctx.merchant.name} par. YES reply kijiye, hum book kar denge."), "binary_yes_no",
                         "Customer asked price; answered from the merchant's live offer")
        return _send(_L(lang, f"The team at {ctx.merchant.name} will confirm the exact price for you — reply YES and they'll call.",
                        f"{ctx.merchant.name} ki team exact price bata degi — YES reply kijiye, woh call karenge."), "binary_yes_no",
                     "Customer asked price but none on file; no guessing")
    return _handle_cost(conv, mem, text, ctx, intent, lang, name)


def _handle_how(conv, mem, text, ctx, intent, lang, name):
    action = conv.plan.get("action") or "prepare it"
    body = _L(lang, f"Nothing from your side except a YES: I'll {action}, show it to you here first, and nothing is published without your OK.",
              f"Aapki taraf se bas ek YES: main ye kar dungi ({action}), pehle yahin dikhaungi, aur aapke OK ke bina kuch live nahi hoga.")
    conv.mode = "ANSWER"
    return _send(body, "binary_yes_no", "Merchant asked what's needed; answered concretely with a single next step")


def _handle_who(conv, mem, text, ctx, intent, lang, name):
    body = _L(lang, "I'm Vera, magicpin's assistant for merchants — I help with your Google profile, offers, reviews and customer messages.",
              "Main Vera hoon, magicpin ki merchant assistant — Google profile, offers, reviews aur customer messages mein madad karti hoon.")
    return _send(body, "none", "Identity question answered plainly")


def _handle_source(conv, mem, text, ctx, intent, lang, name):
    body = _L(lang, "From your own listing data on magicpin and Google (views, calls, reviews) plus the category updates I track. Nothing outside that.",
              "Aapki magicpin aur Google listing ke data se (views, calls, reviews) aur category updates se. Uske bahar kuch nahi.")
    return _send(body, "none", "Data-source question answered transparently")


def _handle_trust(conv, mem, text, ctx, intent, lang, name):
    action = conv.plan.get("action")
    body = _L(lang, "Fair to ask. Every number I send comes from your own listing and magicpin data, and nothing is published without your OK — you see the draft first."
                    + (f" If it helps, I'll {action} and show you before anything changes." if action else ""),
              "Sahi sawaal. Jo bhi number bhejti hoon woh aapki listing aur magicpin data se hai, aur aapke OK ke bina kuch live nahi hota — pehle draft aap dekhte hain."
                    + (" Chahein toh pehle draft dikha deti hoon." if action else ""))
    conv.mode = "ANSWER"
    return _send(body, "binary_yes_no" if action else "none", "Trust question: transparent about data source and approval control")


def _handle_why(conv, mem, text, ctx, intent, lang, name):
    reason = conv.plan.get("why_now") or ""
    anchor = conv.plan.get("anchor_short") or ""
    action = conv.plan.get("action")
    if not (reason or anchor):
        return _handle_question(conv, mem, text, ctx, intent, lang, name)
    body = _L(lang, f"Because of {reason}{' — and ' + anchor if anchor else ''}. That's the one thing I'd act on this week."
                    + (f" Say YES and I'll {action}." if action and not conv.executed else ""),
              f"Kyunki {reason}{' — aur ' + anchor if anchor else ''}. Is hafte bas yahi ek kaam karne layak hai."
                    + (" YES boliye, main kar deti hoon." if action and not conv.executed else ""))
    conv.mode = "ANSWER"
    return _send(body, "binary_yes_no" if action and not conv.executed else "none",
                 "Merchant asked why: gave the concrete trigger and merchant-specific reason")


def _handle_what(conv, mem, text, ctx, intent, lang, name):
    if conv.audience == "merchant" and conv.plan.get("artifact") and not conv.executed:
        return _execute(conv, mem, ctx, lang, "Merchant asked what exactly: showing the concrete draft instead of describing it")
    return _handle_how(conv, mem, text, ctx, intent, lang, name)


def _handle_question(conv, mem, text, ctx, intent, lang, name):
    action = conv.plan.get("action")
    body_en = "Good question — I don't have that detail in front of me, so I won't guess."
    body_hi = "Accha sawaal — ye detail abhi mere paas nahi hai, isliye andaaza nahi lagaungi."
    if action and not conv.executed:
        body_en += f" What I can do right now: {action} — say YES and it's done."
        body_hi += f" Abhi main ye kar sakti hoon: {action} — YES boliye."
        cta = "binary_yes_no"
    else:
        cta = "open_ended"
    conv.mode = "ANSWER"
    return _send(_L(lang, body_en, body_hi), cta, "Question outside known context: honest non-answer, no fabrication, one redirect")


def _handle_greeting(conv, mem, text, ctx, intent, lang, name):
    action = conv.plan.get("action")
    if action and not conv.executed:
        body = _L(lang, f"Hi{', ' + name if name else ''}! Picking up where we left off — I can {action} whenever you're ready. Say YES and it's done.",
                  f"Namaste{', ' + name if name else ''}! Wahin se shuru karte hain — {action}, bas aapke YES ka intezaar hai.")
        return _send(body, "binary_yes_no", "Greeting: resumed the open thread with one ask")
    return _send(_L(lang, f"Hi{', ' + name if name else ''}! What would you like help with — your Google profile, an offer, or customer messages?",
                    f"Namaste{', ' + name if name else ''}! Kis cheez mein madad karun — Google profile, offer ya customer messages?"),
                 "open_ended", "Greeting with no open thread: short menu of what Vera can do")


def _handle_info(conv, mem, text, ctx, intent, lang, name):
    """Free-text reply. In DISCOVER (curious ask) it's the answer we asked
    for: use it immediately. Otherwise treat substantive info as go-ahead
    context and move to the artifact."""
    answer = (intent.detail or "").strip() if isinstance(intent.detail, str) else ""
    if conv.plan.get("genre") == "curious_ask" and answer and not conv.executed:
        topic = _extract_service(answer, ctx)
        conv.plan["artifact"] = "post"
        return _execute(conv, mem, ctx, lang, "Merchant answered the curious-ask; turning the answer straight into the promised post",
                        topic_override=topic)
    if conv.executed and not conv.completed:
        price = re.search(r"(?:₹|rs\.?|inr)\s?(\d[\d,]*)|^\s*(\d{2,6})\s*(?:rs|rupees|/-)?\s*$", answer, flags=re.I)
        if price and ctx is not None:
            amount = (price.group(1) or price.group(2)).replace(",", "")
            conv.plan.setdefault("extra", {})["price"] = int(amount)
            ctx.facts.register(int(amount))
            conv.executed = False
            return _execute(conv, mem, ctx, lang, "Merchant supplied the price; updated the draft with it (same single ask)")
        body = _L(lang, "Got it — I'll fold that in. Reply CONFIRM when you're happy and it goes out.",
                  "Samajh gayi — ye add kar deti hoon. Theek lage toh CONFIRM reply kijiye.")
        return _send(body, "binary_confirm_cancel", "Merchant added detail to the draft; acknowledging, same single ask")
    if conv.audience == "customer":
        body = _L(lang, "Thanks! We'll pass this on and get back to you shortly.", "Dhanyavaad! Hum team ko bata dete hain, jaldi jawab denge.")
        return _send(body, "none", "Customer free-text reply acknowledged")
    return _execute(conv, mem, ctx, lang, "Merchant engaged with detail; advancing to a concrete draft rather than another question")


_HANDLERS = {
    "auto_reply": _handle_auto, "opt_out": _handle_opt_out, "hostile": _handle_hostile, "off_topic": _handle_off_topic,
    "later": _handle_later, "decline": _handle_decline, "thanks": _handle_thanks, "accept": _handle_accept,
    "slot_choice": _handle_slot, "show": _handle_show, "objection_cost": _handle_cost, "objection_done": _handle_done,
    "objection_tried": _handle_tried, "objection_time": _handle_time, "question_price": _handle_price,
    "question_how": _handle_how, "question_who": _handle_who, "question_source": _handle_source,
    "question_trust": _handle_trust, "question_why": _handle_why, "question_what": _handle_what,
    "question": _handle_question, "greeting": _handle_greeting, "info": _handle_info,
}
