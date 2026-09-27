"""Optional LLM realization layer.

The LLM never decides what to send. It receives the finished MessagePlan (the
deterministic draft + the evidence it may use) and may only rephrase it. Its
output is re-validated; on any failure the deterministic draft is used.

Configured purely by env (LLM_PROVIDER / LLM_API_KEY / LLM_MODEL). Results are
memoised by input hash so identical inputs always return identical output,
even on models that don't accept temperature.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Optional
from urllib import request as urlrequest

from .config import CONFIG

log = logging.getLogger("vera.llm")
_cache: dict = {}
_lock = threading.Lock()

SYSTEM_PROMPT = """ROLE
You are the wording layer of Vera, magicpin's WhatsApp assistant for Indian merchants. A deterministic planner has already decided WHAT to say. You only improve HOW it reads.

MISSION
Rewrite the draft so it reads like a sharp, human colleague wrote it: specific, short, peer-to-peer. Keep the same decision, the same facts, the same single ask.

INPUT CONTRACT
You get JSON: audience, send_as, category voice, language, salutation, the draft body, the evidence list, taboo words and the CTA shape.

EVIDENCE RULES
- Every number, price, date, name, source and offer in your output MUST already appear in the draft or the evidence list. Never add a new one.
- Never invent competitors, research, offers, slots, customer counts or capabilities. Never turn an estimate into a fact.

CATEGORY / MERCHANT / CUSTOMER RULES
- Match the category voice given. Clinical and pharmacy messages: precise, no hype, no outcome promises.
- Customer-facing messages are sent from the merchant's number: keep the merchant identification, respect the customer's language, no shame or pressure.

CTA RULES
- Exactly one ask, and it must be the last sentence. Keep reply keywords (YES, CONFIRM, 1/2) exactly as in the draft.

WHATSAPP RULES
- No URLs. No preambles ("I hope you're doing well"). Don't re-introduce Vera.

LANGUAGE RULES
- Keep the draft's language. Hinglish stays Hinglish (Roman script); English stays English.

ANTI-REPETITION
- Don't repeat a sentence or an idea twice.

OUTPUT SCHEMA
Return ONLY a JSON object: {"body": "<rewritten message>"}"""

# Models that reject sampling parameters (400 on temperature).
_NO_SAMPLING = re.compile(r"claude-(opus-5|sonnet-5|fable|mythos|opus-4-[78])")


def _key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{CONFIG.llm_provider}|{CONFIG.llm_model}|{raw}".encode()).hexdigest()


def _anthropic(user: str) -> str:
    import anthropic  # optional dependency

    model = CONFIG.llm_model or "claude-opus-5"
    client = anthropic.Anthropic(api_key=CONFIG.llm_api_key, timeout=CONFIG.llm_timeout_s, max_retries=0)
    kwargs = dict(model=model, max_tokens=1200, system=SYSTEM_PROMPT,
                  messages=[{"role": "user", "content": user}])
    if _NO_SAMPLING.search(model):
        kwargs["output_config"] = {"effort": "low"}
    else:
        kwargs["temperature"] = CONFIG.temperature
    resp = client.messages.create(**kwargs)
    if getattr(resp, "stop_reason", None) == "refusal":
        return ""
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


_OPENAI_COMPAT = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


def _openai_compatible(user: str) -> str:
    base = CONFIG.llm_base_url or _OPENAI_COMPAT.get(CONFIG.llm_provider, "")
    if CONFIG.llm_provider == "ollama":
        base = CONFIG.llm_base_url or "http://localhost:11434/v1"
    body = json.dumps({"model": CONFIG.llm_model, "temperature": CONFIG.temperature,
                       # reasoning models spend output budget on hidden thinking; leave room for the JSON
                       "max_tokens": 1200 if CONFIG.llm_provider != "gemini" else 8192,
                       "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": user}]}).encode()
    req = urlrequest.Request(f"{base.rstrip('/')}/chat/completions", data=body,
                             headers={"Authorization": f"Bearer {CONFIG.llm_api_key}",
                                      "Content-Type": "application/json"})
    with urlrequest.urlopen(req, timeout=CONFIG.llm_timeout_s) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def polish(payload: dict) -> Optional[str]:
    """Return a rewritten body, or None on any failure (caller falls back)."""
    if not CONFIG.llm_enabled:
        return None
    k = _key(payload)
    with _lock:
        if k in _cache:
            return _cache[k]
    user = json.dumps(payload, ensure_ascii=False)
    try:
        raw = _anthropic(user) if CONFIG.llm_provider == "anthropic" else _openai_compatible(user)
        m = re.search(r"\{[\s\S]*\}", raw or "")
        body = json.loads(m.group())["body"].strip() if m else None
    except Exception as e:  # timeout, malformed JSON, provider error — all fall back
        log.warning("llm polish failed: %s", type(e).__name__)
        body = None
    with _lock:
        _cache[k] = body
    return body
