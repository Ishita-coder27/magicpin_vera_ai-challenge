"""Deterministic language selection.

Merchant-facing: the category's code-mix norm + the merchant's languages set
the default; the merchant's own last reply overrides it (they switched to
Hindi -> we follow). Customer-facing: the customer's `language_pref` wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .views import CategoryView, CustomerView, MerchantView

_HINGLISH = {
    "hai", "hain", "kya", "nahi", "nahin", "haan", "han", "karo", "kar", "karna", "kijiye",
    "dijiye", "mujhe", "mera", "meri", "mere", "aap", "aapka", "aapki", "apka", "apki", "chahiye",
    "theek", "thik", "accha", "acha", "achha", "bhai", "ji", "abhi", "baad", "mein", "kal", "bolo",
    "bataiye", "batao", "chalega", "kaise", "kitna", "kitne", "kab", "kyun", "hum", "humein",
    "wala", "wali", "bhejo", "bhej", "lekin", "toh", "sab", "yeh", "ye", "woh", "matlab",
    "shukriya", "dhanyavad", "namaste", "karenge", "karein", "zaroor", "jaldi", "ho", "gaya", "raha",
}
_REGIONAL_GREETING = {"ta": "Vanakkam", "te": "Namaskaram", "kn": "Namaskara", "mr": "Namaskar"}


@dataclass(frozen=True)
class LangPlan:
    code: str            # "en" | "hinglish" | "hindi"
    greeting: str = ""   # optional regional greeting word
    reason: str = ""

    @property
    def hindiish(self) -> bool:
        return self.code in ("hinglish", "hindi")


def detect(text: str) -> str:
    """'hindi' (Devanagari), 'hinglish' (Roman Hindi), or 'en'."""
    if not text:
        return "en"
    if re.search(r"[ऀ-ॿ]", text):
        return "hindi"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "en"
    hits = sum(1 for w in words if w in _HINGLISH)
    if hits >= 2 or (hits >= 1 and len(words) <= 3 and words[0] in ("haan", "han", "nahi", "theek", "thik", "accha", "acha", "ji")):
        return "hinglish"
    return "en"


def for_merchant(merchant: MerchantView, category: CategoryView,
                 last_merchant_text: Optional[str] = None) -> LangPlan:
    if last_merchant_text:
        d = detect(last_merchant_text)
        if d in ("hindi", "hinglish"):
            return LangPlan("hinglish", reason="merchant replied in Hindi/Hinglish")
        if d == "en" and len(last_merchant_text.split()) >= 3:
            return LangPlan("en", reason="merchant replied in English")
    own = [str(h.get("body", "")) for h in merchant.history if str(h.get("from", "")).lower() == "merchant"]
    if own and detect(own[-1]) in ("hindi", "hinglish"):
        return LangPlan("hinglish", reason="merchant's own recent messages are Hindi/Hinglish")
    return LangPlan("en", reason="merchant's recorded messages are English" if own else "English default (no merchant messages on record)")


def for_customer(customer: CustomerView) -> LangPlan:
    pref = customer.language_pref.replace("_", "-")
    if pref in ("hi", "hindi"):
        return LangPlan("hindi", reason="customer language_pref=hi")
    if "hi" in pref and ("en" in pref or "mix" in pref):
        return LangPlan("hinglish", reason="customer language_pref=hi-en mix")
    m = re.match(r"(ta|te|kn|mr)\b", pref)
    if m:
        return LangPlan("en", _REGIONAL_GREETING.get(m.group(1), ""), reason=f"customer language_pref={pref}: English body with regional greeting")
    return LangPlan("en", reason="customer language_pref=en")
