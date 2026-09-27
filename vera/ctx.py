"""Composition context: everything a strategy needs, plus the evidence ledger.

Strategies never read raw dicts directly and never format numbers by hand:
they call `ctx.fact(...)`, which records provenance and registers derived
values with the FactIndex so the validator can trace every number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .evidence import Evidence, FactIndex
from .formatting import as_float, parse_dt
from .kinds import KindSpec, is_placeholder, spec_for
from .language import LangPlan
from .policies import CategoryPolicy, policy_for
from .views import CategoryView, CustomerView, MerchantView


@dataclass
class Draft:
    """Output of a strategy: WHAT to say, as ordered beats (English unless the
    strategy composed for a customer language directly)."""
    genre: str
    why_now: str
    hook: str
    beats: List[str]
    ask: str
    cta: str
    action: str                      # short label of what Vera offers to do
    artifact: str = ""               # key for the follow-up artifact builder
    ask_hi: Optional[str] = None     # Hinglish CTA variant (merchant light code-mix)
    anchor: str = ""                 # why this merchant, for the rationale
    expected_action: str = ""        # contrarian bookkeeping
    adjusted_action: str = ""
    send_as: str = "vera"
    template: str = ""
    lang_applied: str = "en"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    category: CategoryView
    merchant: MerchantView
    trigger: dict
    customer: Optional[CustomerView]
    now: Optional[datetime] = None
    clock_trusted: bool = False
    lang: LangPlan = field(default_factory=lambda: LangPlan("en"))
    conversation: Optional[dict] = None
    ledger: List[Evidence] = field(default_factory=list)
    facts: FactIndex = field(default_factory=FactIndex)

    def __post_init__(self) -> None:
        self.tp: dict = self.trigger.get("payload") if isinstance(self.trigger.get("payload"), dict) else {}
        self.kind: str = str(self.trigger.get("kind") or "").lower()
        self.scope: str = str(self.trigger.get("scope") or ("customer" if self.customer else "merchant"))
        self.spec: KindSpec = spec_for(self.kind, self.scope, self.tp)
        self.policy: CategoryPolicy = policy_for(self.category.slug or self.merchant.category_slug)
        self.placeholder: bool = is_placeholder(self.tp)
        self.facts = FactIndex(self.category.raw, self.merchant.raw, self.trigger,
                               self.customer.raw if self.customer else None)

    # -- evidence ------------------------------------------------------------
    def fact(self, text: str, source: Any, derived: bool = False, value: Any = None) -> str:
        self.ledger.append(Evidence(text, source, "derived" if derived else "direct", as_float(value)))
        if derived:
            if value is not None:
                self.facts.register(value)
            self.facts.register(text)
        return text

    # -- convenience -----------------------------------------------------------
    @property
    def sal(self) -> str:
        return self.merchant.salutation(self.category.slug)

    @property
    def urgency(self) -> int:
        try:
            return max(1, min(5, int(self.trigger.get("urgency") or 2)))
        except (TypeError, ValueError):
            return 2

    def expires_at(self) -> Optional[datetime]:
        return parse_dt(self.trigger.get("expires_at"))

    def days_until(self, iso: Any) -> Optional[int]:
        """Days from trusted `now` to a date; None when the clock is untrusted."""
        d = parse_dt(iso)
        if not d or not self.now or not self.clock_trusted:
            return None
        return (d - self.now).days

    def artifact_label(self, key: str, default: str = "") -> str:
        return self.policy.artifact.get(key, default or self.policy.artifact.get("post", "a Google post"))
