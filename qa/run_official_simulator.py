#!/usr/bin/env python3
"""Run the unmodified official judge_simulator.py with configuration taken
from environment variables, so no API key is ever written to disk.

    SIM_PROVIDER=gemini SIM_MODEL=gemini-3.8-flash SIM_API_KEY=... \
    BOT_URL=http://localhost:8081 python qa/run_official_simulator.py all
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load KEY=value lines from ROOT/.env (gitignored) without printing them.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _k, _, _v = _line.strip().partition("=")
        if _k and _v and not _k.startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import judge_simulator as js  # noqa: E402

js.BOT_URL = os.environ.get("BOT_URL", "http://localhost:8081")
js.LLM_PROVIDER = os.environ.get("SIM_PROVIDER", "gemini")
js.LLM_API_KEY = os.environ.get("SIM_API_KEY", js.LLM_API_KEY)
js.LLM_MODEL = os.environ.get("SIM_MODEL", "gemini-3.8-flash")
js.TEST_SCENARIO = sys.argv[1] if len(sys.argv) > 1 else js.TEST_SCENARIO

# Free-tier providers return transient 503/429s; without a retry the simulator
# silently substitutes a heuristic fallback score. Wrap every provider's
# complete() with bounded retry + backoff (the official scoring prompt is unchanged).
import time as _t
from urllib import error as _ue


def _with_retry(fn):
    def wrapped(self, prompt, system=None):
        for attempt in range(6):
            try:
                out = fn(self, prompt, system)
                _t.sleep(float(os.environ.get("SIM_PACE_S", "2")))
                return out
            except _ue.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    _t.sleep(4 * (attempt + 1))
                    continue
                raise
    return wrapped


# The official Gemini adapter caps maxOutputTokens at 1500. Current Gemini
# models spend most of that on hidden reasoning ("thoughtsTokenCount"), so the
# JSON verdict is truncated and the simulator silently falls back to a
# heuristic score. Same prompt, same temperature — only a larger output budget.
def _gemini_complete(self, prompt, system=None):
    import json as _j
    from urllib import request as _r
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    body = _j.dumps({"contents": [{"parts": [{"text": full_prompt}]}],
                     "generationConfig": {"temperature": 0.2,
                                          "maxOutputTokens": int(os.environ.get("SIM_MAX_TOKENS", "8192"))}}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
    resp = _r.urlopen(_r.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=js.TIMEOUT_LLM * 3)
    data = _j.loads(resp.read().decode("utf-8"))
    parts = data["candidates"][0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


js.GeminiProvider.complete = _gemini_complete

for _cls in (js.GeminiProvider, js.OpenAIProvider, js.AnthropicProvider, js.GroqProvider, js.DeepSeekProvider,
             js.OpenRouterProvider, js.OllamaProvider):
    _cls.complete = _with_retry(_cls.complete)


if __name__ == "__main__":
    js.main()
