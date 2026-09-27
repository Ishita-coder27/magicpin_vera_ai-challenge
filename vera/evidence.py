"""Evidence ledger + fact index.

Every factual element the planner puts into a message is recorded as an
Evidence item with its source path. The FactIndex holds every number and
string present in the four contexts plus registered derivations; the validator
rejects any message containing a number or proper noun that isn't in it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Set, Union

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass
class Evidence:
    text: str
    source: Union[str, List[str]]
    confidence: str = "direct"   # direct | derived
    value: Optional[float] = None

    def as_dict(self) -> dict:
        return {"text": self.text, "source": self.source, "confidence": self.confidence}


def _walk(obj: Any, nums: Set[float], strings: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            strings.append(str(k))
            for m in _NUM_RE.findall(str(k)):
                _add_num(nums, float(m.replace(",", "")))
            _walk(v, nums, strings)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, nums, strings)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, (int, float)):
        _add_num(nums, float(obj))
    else:
        s = str(obj)
        strings.append(s)
        for m in _NUM_RE.findall(s):
            try:
                _add_num(nums, float(m.replace(",", "")))
            except ValueError:
                pass


def _add_num(nums: Set[float], f: float) -> None:
    f = abs(f)
    nums.add(round(f, 2))
    if f <= 1.5:
        nums.add(round(f * 100, 1))   # fraction -> percent
        nums.add(round(f * 100))
    nums.add(round(f))


class FactIndex:
    def __init__(self, *contexts: Any):
        self.nums: Set[float] = set()
        self._strings: List[str] = []
        for c in contexts:
            if c:
                _walk(c, self.nums, self._strings)
        self._corpus: Optional[str] = None

    def register(self, value: Any) -> None:
        """Register a derived number (e.g. 12 * 50% = 6) or string."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _add_num(self.nums, float(value))
        elif value is not None:
            self._strings.append(str(value))
            _walk(str(value), self.nums, [])
            self._corpus = None

    def register_many(self, values: Iterable[Any]) -> None:
        for v in values:
            self.register(v)

    @property
    def corpus(self) -> str:
        if self._corpus is None:
            text = " \n ".join(self._strings).lower().replace("_", " ")
            self._corpus = text
        return self._corpus

    def has_number(self, v: float) -> bool:
        v = abs(v)
        if v <= 3:           # list counters, "Reply 1 or 2", "one of 2 slots"
            return True
        for a in (round(v, 2), round(v, 1), float(round(v))):
            if a in self.nums:
                return True
        tol = max(0.051, v * 0.004)
        return any(abs(v - a) <= tol for a in self.nums)

    def has_text(self, token: str) -> bool:
        return token.lower().replace("_", " ") in self.corpus
