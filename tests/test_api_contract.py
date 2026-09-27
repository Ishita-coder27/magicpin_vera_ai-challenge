"""HTTP contract: endpoints, status codes, version semantics, schemas."""
from conftest import push

VALID_CTA = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}
ACTION_FIELDS = {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
                 "template_params", "body", "cta", "suppression_key", "rationale"}


def test_healthz_and_metadata(client):
    h = client.get("/v1/healthz").json()
    assert h["status"] == "ok" and set(h["contexts_loaded"]) == {"category", "merchant", "customer", "trigger"}
    m = client.get("/v1/metadata").json()
    for k in ("team_name", "team_members", "model", "approach", "contact_email", "version", "submitted_at"):
        assert k in m


def test_context_version_semantics(client, data):
    m = data["merchants"]["m_001_drmeera_dentist_delhi"]
    r = push(client, "merchant", m["merchant_id"], m, 1)
    assert r.status_code == 200 and r.json()["accepted"]
    r = push(client, "merchant", m["merchant_id"], m, 1)          # same version -> 409
    assert r.status_code == 409 and r.json() == {"accepted": False, "reason": "stale_version", "current_version": 1}
    r = push(client, "merchant", m["merchant_id"], {**m, "performance": {"views": 1}}, 3)
    assert r.status_code == 200
    r = push(client, "merchant", m["merchant_id"], m, 2)          # lower -> 409 with current
    assert r.status_code == 409 and r.json()["current_version"] == 3
    import bot
    assert bot.ENGINE.store.payload("merchant", m["merchant_id"])["performance"]["views"] == 1
    assert client.get("/v1/healthz").json()["contexts_loaded"]["merchant"] == 1


def test_context_malformed(client):
    assert client.post("/v1/context", json={"scope": "planet", "context_id": "x", "version": 1, "payload": {}}).status_code == 400
    assert client.post("/v1/context", json={"scope": "merchant"}).status_code == 400
    assert client.post("/v1/context", content=b"not json", headers={"content-type": "application/json"}).status_code == 400


def test_warmup_counts(loaded):
    c = loaded.get("/v1/healthz").json()["contexts_loaded"]
    assert c == {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}


def test_tick_schema(loaded, data):
    ids = []
    for tid, t in list(data["triggers"].items())[:25]:
        push(loaded, "trigger", tid, t)
        ids.append(tid)
    r = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ids})
    assert r.status_code == 200
    actions = r.json()["actions"]
    assert actions, "expected some sends"
    convs = set()
    for a in actions:
        assert ACTION_FIELDS <= a.keys()
        assert a["body"].strip() and a["cta"] in VALID_CTA and a["send_as"] in ("vera", "merchant_on_behalf")
        assert a["suppression_key"] and a["rationale"] and isinstance(a["template_params"], list)
        assert all("\n" not in p for p in a["template_params"])
        assert "http" not in a["body"].lower() and "www." not in a["body"].lower()
        assert a["conversation_id"] not in convs
        convs.add(a["conversation_id"])


def test_tick_empty_and_unknown(client):
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": []}).json() == {"actions": []}
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["nope"]}).json() == {"actions": []}


def test_reply_schema(loaded):
    r = loaded.post("/v1/reply", json={"conversation_id": "c1", "merchant_id": "m_001_drmeera_dentist_delhi",
                                       "from_role": "merchant", "message": "hello", "received_at": "2026-04-26T10:00:00Z",
                                       "turn_number": 2}).json()
    assert r["action"] in ("send", "wait", "end") and r["rationale"]
    if r["action"] == "send":
        assert r["body"].strip() and r["cta"] in VALID_CTA


def test_teardown_wipes(loaded):
    assert loaded.post("/v1/teardown").json()["ok"]
    assert loaded.get("/v1/healthz").json()["contexts_loaded"]["merchant"] == 0
