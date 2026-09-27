"""Deterministic post-composition validation.

Runs on every outbound body, whether it came from the template realizer or an
LLM. Any hard failure means: regenerate (LLM) or fall back (deterministic).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .evidence import FactIndex
from .language import detect
from .policies import GLOBAL_TABOOS

_URL = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|in|org|net|io|co)\b/?)", re.I)
_NUM = re.compile(r"(?<![A-Za-z0-9\-])[-+]?₹?\s?\d[\d,]*(?:\.\d+)?(?![A-Za-z]*\d)")
_QUALIFYING = ("would you", "do you", "can you tell", "what if", "how about", "are you interested",
               "would it help", "have you considered")
_WHITELIST = {
    "vera", "google", "whatsapp", "magicpin", "yes", "no", "stop", "confirm", "go", "reply", "hi", "namaste",
    "vanakkam", "namaskaram", "namaskara", "namaskar", "dr", "doctor", "doc", "mon", "tue", "wed", "thu", "fri",
    "sat", "sun", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "jan", "feb",
    "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "january", "february", "march",
    "april", "june", "july", "august", "september", "october", "november", "december", "ctr", "gbp", "ok",
    "cde", "pt", "sop", "insta", "instagram", "yoy", "rx", "ji", "abstract", "step", "offer", "shows", "runs",
    "record", "action", "plan", "draft", "done", "hindi", "english", "ist", "am", "pm", "diwali", "holi", "ipl",
    "vs", "cancel", "hook", "channel", "what", "for", "who", "price", "summer", "leaderboard", "call",
}


@dataclass
class Validation:
    ok: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues, "warnings": self.warnings}


def _strip_quotes(text: str) -> str:
    return re.sub(r"\"[^\"]*\"|“[^”]*”", " ", text)


def ungrounded_numbers(body: str, facts: FactIndex) -> List[str]:
    bad = []
    for m in _NUM.finditer(body):
        raw = m.group().replace("₹", "").replace(",", "").replace(" ", "").lstrip("+")
        try:
            v = float(raw)
        except ValueError:
            continue
        if not facts.has_number(v):
            bad.append(m.group().strip())
    return bad


def ungrounded_names(body: str, facts: FactIndex, extra_ok: Iterable[str] = ()) -> List[str]:
    ok = _WHITELIST | {w.lower() for w in extra_ok}
    bad = []
    for m in re.finditer(r"[A-Z][a-zA-Z]{2,}", body):
        # Sentence-initial words are capitalised by grammar, not because they are names.
        before = body[:m.start()].rstrip()
        if not before or before[-1] in ".!?:\"'()—•*\n-“" or ord(before[-1]) > 0x2000:
            continue
        word = m.group()
        wl = word.lower()
        if wl in ok or facts.has_text(wl) or facts.has_text(wl.rstrip("s")):
            continue
        bad.append(word)
    return bad


def validate(body: str, *, facts: FactIndex, taboos: Iterable[str] = (), audience: str = "merchant",
             cta: str = "open_ended", lang_code: str = "en", prior_bodies: Iterable[str] = (),
             mode: str = "propose", merchant_name: str = "", strict_names: bool = True,
             near_dup: float = 0.8) -> Validation:
    issues: List[str] = []
    warnings: List[str] = []
    text = (body or "").strip()
    if not text:
        return Validation(False, ["empty body"])
    if len(text) > 1400:
        issues.append(f"body too long ({len(text)} chars)")
    if _URL.search(text):
        issues.append("URL in body")
    from .untrusted import is_instruction_like
    if is_instruction_like(_strip_quotes(text)) or is_instruction_like(text):
        issues.append("instruction-like (injected) content in body")
    if re.search(r"\bNone\b|\bnan\b|\bnull\b|\{\w*\}", text):
        issues.append("template leak (None/null/{placeholder})")
    low = text.lower()
    for t in list(taboos) + GLOBAL_TABOOS:
        t = str(t).lower().strip()
        if t and re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
            issues.append(f"taboo phrase: '{t}'")
    nums = ungrounded_numbers(text, facts)
    if nums:
        issues.append(f"ungrounded numbers: {nums[:5]}")
    if strict_names:
        names = ungrounded_names(text, facts)
        if names:
            (issues if len(names) > 1 else warnings).append(f"unverified names: {sorted(set(names))[:6]}")
    unquoted = _strip_quotes(text)
    q = unquoted.count("?")
    if audience == "merchant" and q > 1:
        issues.append(f"{q} questions — more than one ask")
    if re.search(r"reply\s+\w+\s+for\s+.+reply\s+\w+\s+for", low) and cta != "multi_choice_slot":
        issues.append("multiple reply-keyword CTAs")
    over = overclaims(text) if audience == "merchant" else []
    if over:
        issues.append(f"execution overclaim: {over[:2]}")
    if mode == "execute":
        hits = [p for p in _QUALIFYING if p in unquoted.lower()]
        if hits:
            issues.append(f"re-qualifying after explicit intent: {hits}")
    for p in prior_bodies:
        if not p:
            continue
        if p.strip() == text:
            issues.append("verbatim repeat of an earlier message")
            break
        if _jaccard(p, text) > near_dup and _facts(p) == _facts(text):
            # Same wording AND same facts. New batch numbers, dates or counts
            # make it a new message even when the sentences barely change.
            issues.append("near-duplicate of an earlier message")
            break
    detected = detect(text)
    if lang_code in ("hinglish", "hindi") and detected == "en":
        warnings.append("expected Hindi/Hinglish but body reads English")
    if audience == "customer" and merchant_name and merchant_name.split()[0].lower() not in low:
        issues.append("customer message does not identify the merchant")
    return Validation(not issues, issues, warnings)


def _facts(text: str) -> frozenset:
    """Numbers, prices and codes (AT2024-1102) in a message."""
    return frozenset(re.findall(r"[A-Z]{1,4}\d[\w-]*|₹?\d[\d,.]*%?", text))


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[a-z0-9₹]+", a.lower())), set(re.findall(r"[a-z0-9₹]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def similarity(a: str, b: str) -> float:
    return _jaccard(a, b)


_NUM_TOKEN = re.compile(r"₹?\d[\d,]*(?:\.\d+)?%?(?::\d\d)?(?:am|pm)?", re.I)


def _num_pairs(text: str):
    """(number, following word) pairs — '12 reviews' and '12 salons' differ."""
    out = set()
    for m in _NUM_TOKEN.finditer(text):
        nxt = re.match(r"\s*([A-Za-z]+)", text[m.end():])
        out.add((m.group().lower().replace(",", ""), (nxt.group(1).lower() if nxt else "")))
    return out


def preserves_facts(draft: str, rewrite: str) -> bool:
    """A rewrite may reorder and rephrase, but every number must appear in the
    draft with the same unit word, and every mid-sentence capitalised word must
    already appear in the draft. Blocks fake citations, slots, social proof."""
    d_pairs = _num_pairs(draft)
    d_nums = {n for n, _ in d_pairs}
    d_vocab = set(re.findall(r"[a-z]+", draft.lower()))
    for n, w in _num_pairs(rewrite):
        if n not in d_nums:
            return False
        # A number re-attached to a *new* noun ("12 reviews" -> "12 salons") is a
        # new claim; an ordinary word the draft already uses ("2026 is") is not.
        if w and w not in d_vocab and (n, w) not in d_pairs:
            return False
    # ...and nothing load-bearing may be dropped: every number, day/month and
    # offer title in the draft must survive (a rewrite that loses "it's a
    # Sunday game" changes the decision it explains).
    r_nums = {n for n, _ in _num_pairs(rewrite)}
    if not d_nums <= r_nums:
        return False
    days = r"\b(mon|tues|wednes|thurs|fri|satur|sun)day\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    if {m.group().lower() for m in re.finditer(days, draft, re.I)} - {m.group().lower() for m in re.finditer(days, rewrite, re.I)}:
        return False
    for offer in re.findall(r"[A-Z][\w+ ]+ @ ₹[\d,]+(?:/\w+)?", draft):
        if offer not in rewrite:
            return False
    d_words = set(re.findall(r"[a-z]+", draft.lower()))
    for m in re.finditer(r"[A-Z][a-zA-Z]{2,}", rewrite):
        before = rewrite[:m.start()].rstrip()
        if before and before[-1] not in ".!?:\"'()—•*\n-“" and ord(before[-1]) <= 0x2000:
            if m.group().lower() not in d_words:
                return False
    return True


_OVERCLAIM = [
    r"\bi(?:'ve| have) (?:sent|posted|published|updated|activated|scheduled|emailed|charged|booked|launched|called)\b",
    r"\b(?:has|have) been (?:sent|posted|published|updated|activated|scheduled|emailed)\b",
    r"\bpayment link\b", r"\bsent to your (?:email|registered)\b", r"\bis (?:now )?live on\b",
    r"\bgoes live\b", r"\bit's live\b", r"\bposting (?:the|them|it) now\b", r"\bpublishing (?:the|it) now\b",
]


def overclaims(text: str) -> List[str]:
    """Claims of an external side effect this backend does not perform."""
    low = _strip_quotes(text).lower()
    return [p for p in _OVERCLAIM if re.search(p, low)]
