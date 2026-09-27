"""Decides HOW to say it (deterministic path): assembles a Draft's beats into
a WhatsApp body, applies the merchant code-mix CTA, and derives the approved
template name + parameters for first-touch sends."""
from __future__ import annotations

import re
from typing import List, Tuple

from .ctx import Ctx, Draft

# Categories whose merchant-facing register stays English even when the
# merchant speaks Hindi (clinical peer tone reads better unmixed).
_NO_MERCHANT_CODEMIX = {"dentists"}


def _clean(s: str) -> str:
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def choose_ask(ctx: Ctx, d: Draft) -> str:
    if (d.send_as == "vera" and ctx.lang.code in ("hinglish_light", "hinglish") and d.ask_hi
            and ctx.category.slug not in _NO_MERCHANT_CODEMIX):
        return d.ask_hi
    return d.ask


def realize(ctx: Ctx, d: Draft) -> Tuple[str, str, List[str]]:
    ask = choose_ask(ctx, d)
    beats = [b for b in d.beats if b and b.strip()]
    multiline = any("\n" in b or b.startswith(("*", "\"", "•")) for b in beats)
    if multiline:
        # Plain sentences stay one paragraph; only draft/list blocks are set apart.
        blocks, run = [], [d.hook]
        for b in beats:
            if "\n" in b or b.startswith(("*", "\"", "•")):
                blocks.append(_clean(" ".join(run)))
                run = []
                blocks.append(b)
            else:
                run.append(b)
        if run:
            blocks.append(_clean(" ".join(run)))
        body = "\n\n".join(x for x in blocks if x) + "\n\n" + ask
    else:
        core = _clean(" ".join([d.hook] + beats))
        sep = "\n\n" if (d.send_as == "vera" and len(core) > 260) else " "
        body = core + sep + ask
    body = "\n".join(_clean(line) if line.strip() else "" for line in body.split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, _template_name(ctx, d), _template_params(ctx, d, body, ask)


def _template_name(ctx: Ctx, d: Draft) -> str:
    return d.template or f"vera_{ctx.spec.family}_v1"


def _template_params(ctx: Ctx, d: Draft, body: str, ask: str) -> List[str]:
    """Template = '{{1}}, {{2}}\\n\\n{{3}}'. WhatsApp template params cannot
    contain newlines, so multi-line drafts are flattened with ' | '."""
    def flat(s: str) -> str:
        return re.sub(r"\s*\n+\s*", " | ", s).strip(" |")
    head = body[: len(body) - len(ask)].rstrip() if body.endswith(ask) else body
    first = ""
    if d.send_as == "vera":
        sal = ctx.sal
        if head.startswith(sal):
            first, head = sal, head[len(sal):].lstrip(" ,—")
    else:
        m = re.match(r"^((?:Hi|Namaste|Vanakkam|Namaskaram|Namaskara|Namaskar)\s+[^,!—]+)[,!—]\s*", head)
        if m:
            first, head = m.group(1), head[m.end():]
    return [flat(first), flat(head), flat(ask)]
