"""Vera Decision Engine — HTTP API + compose() entry point.

Run:  uvicorn bot:app --host 0.0.0.0 --port ${PORT:-8080}

Endpoints (challenge-testing-brief §2): /v1/healthz, /v1/metadata,
/v1/context, /v1/tick, /v1/reply, plus optional /v1/teardown.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from vera.composer import compose as _compose
from vera.config import COMPOSER_VERSION, CONFIG
from vera.engine import Engine
from vera.store import SCOPES

logging.basicConfig(level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vera.api")

app = FastAPI(title="Vera Decision Engine", version=COMPOSER_VERSION)
ENGINE = Engine()
MAX_CONTEXT_BYTES = 600_000  # brief caps pushes at 500 KB; small margin


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    """Challenge entry point (brief §7.1): returns body, cta, send_as,
    suppression_key, rationale (+ template_name, template_params)."""
    return _compose(category, merchant, trigger, customer)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------ models
class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickBody(BaseModel):
    now: Optional[str] = None
    available_triggers: List[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: Optional[int] = None


# ------------------------------------------------------------------ errors
@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    path = request.url.path
    if path.endswith("/tick"):
        return JSONResponse(status_code=400, content={"actions": [], "error": "invalid_request"})
    if path.endswith("/reply"):
        return JSONResponse(status_code=400, content={"action": "wait", "wait_seconds": 1800,
                                                      "rationale": "malformed reply payload"})
    return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload",
                                                  "details": str(exc.errors()[:3])[:500]})


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    path = request.url.path
    if path.endswith("/tick"):
        return JSONResponse(status_code=200, content={"actions": []})
    if path.endswith("/reply"):
        return JSONResponse(status_code=200, content={"action": "wait", "wait_seconds": 1800,
                                                      "rationale": "internal error; backing off safely"})
    return JSONResponse(status_code=500, content={"error": "internal_error"})


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return {"service": "Vera Decision Engine", "version": COMPOSER_VERSION,
            "endpoints": ["GET /v1/healthz", "GET /v1/metadata", "POST /v1/context", "POST /v1/tick",
                          "POST /v1/reply", "POST /v1/teardown"], "docs": "/docs"}


@app.get("/v1/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - ENGINE.started),
            "contexts_loaded": ENGINE.store.counts()}


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": CONFIG.team_name,
        "team_members": CONFIG.team_members,
        "model": CONFIG.model_label,
        "approach": ("Vera Decision Engine: versioned context store -> per-trigger opportunity strategies "
                     "(signal auction with category policies, contrarian checks, evidence ledger) -> "
                     "deterministic realization -> hard validation (grounded numbers/names, taboos, one CTA, "
                     "no repeats) -> optional LLM polish re-validated; deterministic reply state machine "
                     "with auto-reply, intent, objection and consent handling"),
        "contact_email": CONFIG.contact_email,
        "version": COMPOSER_VERSION,
        "submitted_at": CONFIG.submitted_at or datetime.fromtimestamp(ENGINE.started, timezone.utc).isoformat(),
    }


@app.post("/v1/context")
async def push_context(request: Request):
    raw = await request.body()
    if len(raw) > MAX_CONTEXT_BYTES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "payload_too_large"})
    try:
        body = ContextBody.model_validate_json(raw)
    except Exception as e:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload",
                                                      "details": str(e)[:300]})
    if body.scope not in SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope",
                                                      "details": f"scope must be one of {list(SCOPES)}"})
    if not body.context_id.strip():
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_context_id"})
    accepted, rec = ENGINE.store.put(body.scope, body.context_id, body.version, body.payload, body.delivered_at)
    if not accepted:
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version",
                                                      "current_version": rec.version})
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}", "stored_at": rec.stored_at}


@app.post("/v1/tick")
def tick(body: TickBody):
    actions = ENGINE.tick(body.now or _now_iso(), body.available_triggers)
    return {"actions": actions}


@app.post("/v1/reply")
def reply(body: ReplyBody):
    return ENGINE.reply(body.model_dump())


@app.post("/v1/teardown")
def teardown():
    ENGINE.teardown()
    return {"ok": True, "wiped_at": _now_iso()}
