"""Versioned, in-memory context store.

Semantics (testing brief §2.1, api-call-examples 1.5/1.6):
  * first version for (scope, context_id)      -> store, 200
  * same or lower version                      -> reject, 409 stale_version
  * higher version                             -> atomic replace, 200

Every replacement records a structural delta so the composer can tell that
"the problem changed" (e.g. CTR recovered) instead of reusing stale framing.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SCOPES = ("category", "merchant", "customer", "trigger")


def context_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        # Lists of dicts with ids are compared by id; others as whole values.
        if obj and all(isinstance(x, dict) and ("id" in x or "title" in x) for x in obj):
            for x in obj:
                key = x.get("id") or x.get("title")
                out.update(_flatten(x, f"{prefix}[{key}]"))
        else:
            out[prefix] = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    else:
        out[prefix] = obj
    return out


def compute_delta(old: Optional[dict], new: dict) -> Dict[str, List[str]]:
    """Paths added / removed / changed between two payload versions."""
    if not old:
        return {"added": [], "removed": [], "changed": []}
    fo, fn = _flatten(old), _flatten(new)
    return {
        "added": sorted(k for k in fn if k not in fo),
        "removed": sorted(k for k in fo if k not in fn),
        "changed": sorted(k for k in fn if k in fo and fo[k] != fn[k]),
    }


@dataclass
class StoredContext:
    scope: str
    context_id: str
    version: int
    payload: dict
    hash: str
    stored_at: str
    delivered_at: Optional[str] = None
    previous_payload: Optional[dict] = None
    delta: Dict[str, List[str]] = field(default_factory=dict)


class ContextStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[Tuple[str, str], StoredContext] = {}

    # -- writes -----------------------------------------------------------
    def put(self, scope: str, context_id: str, version: int, payload: dict,
            delivered_at: Optional[str] = None) -> Tuple[bool, StoredContext]:
        """Returns (accepted, current_record)."""
        key = (scope, context_id)
        with self._lock:
            cur = self._data.get(key)
            if cur is not None and version <= cur.version:
                return False, cur
            payload = copy.deepcopy(payload)
            rec = StoredContext(
                scope=scope, context_id=context_id, version=version, payload=payload,
                hash=context_hash(payload),
                stored_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                delivered_at=delivered_at,
                previous_payload=cur.payload if cur else None,
                delta=compute_delta(cur.payload if cur else None, payload),
            )
            self._data[key] = rec
            return True, rec

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # -- reads ------------------------------------------------------------
    def get(self, scope: str, context_id: Optional[str]) -> Optional[StoredContext]:
        if not context_id:
            return None
        with self._lock:
            return self._data.get((scope, context_id))

    def payload(self, scope: str, context_id: Optional[str]) -> Optional[dict]:
        rec = self.get(scope, context_id)
        return rec.payload if rec else None

    def all(self, scope: str) -> List[StoredContext]:
        with self._lock:
            return [v for (s, _), v in self._data.items() if s == scope]

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in SCOPES}
        with self._lock:
            for (s, _) in self._data:
                out[s] = out.get(s, 0) + 1
        return out

    def versions(self, *keys: Tuple[str, Optional[str]]) -> Dict[str, Any]:
        """Audit helper: {scope:id -> {version, hash}} for the given keys."""
        out: Dict[str, Any] = {}
        for scope, cid in keys:
            rec = self.get(scope, cid)
            if rec:
                out[f"{scope}:{cid}"] = {"version": rec.version, "hash": rec.hash}
        return out
