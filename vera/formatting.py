"""Pure formatting helpers. Every number that reaches a message goes through
here, so the validator can recognise the same value in its rendered form."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*\.?\d*", v)
        if m:
            try:
                return float(m.group().replace(",", ""))
            except ValueError:
                return None
    return None


def indian_int(n: float) -> str:
    """1234567 -> 12,34,567 (Indian digit grouping)."""
    n = int(round(n))
    neg, s = n < 0, str(abs(int(round(n))))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def num(n: Any) -> str:
    f = as_float(n)
    if f is None:
        return str(n)
    if abs(f - round(f)) < 1e-9:
        return indian_int(f)
    return f"{f:.1f}".rstrip("0").rstrip(".")


def inr(n: Any) -> str:
    f = as_float(n)
    return f"₹{indian_int(f)}" if f is not None else str(n)


def pct(frac: Any, signed: bool = False) -> str:
    """0.18 -> '18%'; 0.021 -> '2.1%'. Accepts fractions (|x|<=1.5) or whole %."""
    f = as_float(frac)
    if f is None:
        return str(frac)
    val = f * 100 if abs(f) <= 1.5 else f
    txt = f"{abs(val):.1f}".rstrip("0").rstrip(".") if abs(val) < 10 and abs(val - round(val)) > 1e-9 else f"{abs(round(val))}"
    sign = ("+" if val > 0 else "-" if val < 0 else "") if signed else ("-" if val < 0 else "")
    return f"{sign}{txt}%"


def parse_dt(v: Any) -> Optional[datetime]:
    if not v or not isinstance(v, str):
        return None
    s = v.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s[:10], fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except ValueError:
            continue
    return None


def fmt_date(v: Any, weekday: bool = False, year: bool = False) -> str:
    d = parse_dt(v) if not isinstance(v, (date, datetime)) else v
    if d is None:
        return str(v)
    out = f"{d.day} {MONTHS[d.month - 1]}"
    if year:
        out += f" {d.year}"
    if weekday:
        out = f"{WEEKDAYS[d.weekday()]} {out}"
    return out


def fmt_time(v: Any) -> Optional[str]:
    d = parse_dt(v)
    if d is None or (d.hour == 0 and d.minute == 0):
        return None
    h = d.hour % 12 or 12
    suffix = "am" if d.hour < 12 else "pm"
    return f"{h}{':%02d' % d.minute if d.minute else ''}{suffix}"


def days_between(a: Any, b: Any) -> Optional[int]:
    da, db = parse_dt(a), parse_dt(b)
    if not da or not db:
        return None
    return (db - da).days


def months_between(a: Any, b: Any) -> Optional[int]:
    d = days_between(a, b)
    return None if d is None else int(round(d / 30.4))


def humanize_key(s: str) -> str:
    """'6_month_cleaning' -> '6-month cleaning'; 'kids_yoga_summer_camp' -> 'kids yoga summer camp'."""
    s = re.sub(r"(\d)_(month|week|day|year)", r"\1-\2", str(s))
    return s.replace("_", " ").strip()


_ABBREV = re.compile(r"(?:\b(?:Dr|Mr|Mrs|Ms|St|No|vs|approx|p)|\b[A-Z])\.$")


def split_sentences(text: str):
    """Sentence split that doesn't break 'Dr. R. Mehta' or 'p.14'."""
    out, buf = [], ""
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        buf = f"{buf} {part}".strip() if buf else part
        if not _ABBREV.search(buf):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [s for s in out if s]


def ucfirst(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def first_sentence(text: str, max_len: int = 220) -> str:
    sents = split_sentences(text)
    out = sents[0] if sents else ""
    return out if len(out) <= max_len else out[: max_len - 1].rsplit(" ", 1)[0] + "…"


def join_human(items, conj: str = "and") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def add_days(d: datetime, n: int) -> datetime:
    return d + timedelta(days=n)
