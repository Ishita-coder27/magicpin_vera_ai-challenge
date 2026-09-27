"""Tick-level decisions: suppression, consent, expiry, restraint, adaptation."""
import copy

from conftest import push

NOW = "2026-04-26T10:30:00Z"


def tick(client, ids, now=NOW):
    return client.post("/v1/tick", json={"now": now, "available_triggers": ids}).json()["actions"]


def test_suppression_no_resend(loaded, data):
    t = data["triggers"]["trg_002_compliance_dci_radiograph"]
    push(loaded, "trigger", t["id"], t)
    first = tick(loaded, [t["id"]])
    assert len(first) == 1
    assert tick(loaded, [t["id"]], "2026-04-26T11:30:00Z") == []


def test_one_message_per_merchant_per_tick(loaded, data):
    ids = ["trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph", "trg_022_cde_webinar_dentists",
           "trg_023_competitor_opened_dentist"]
    for i in ids:
        push(loaded, "trigger", i, data["triggers"][i])
    acts = tick(loaded, ids)
    assert len([a for a in acts if a["merchant_id"] == "m_001_drmeera_dentist_delhi" and a["send_as"] == "vera"]) == 1
    # Highest-value opportunity wins: the deadline-bound compliance item.
    assert acts[0]["trigger_id"] == "trg_002_compliance_dci_radiograph"


def test_customer_without_consent_never_messaged(loaded, data):
    cust = copy.deepcopy(data["customers"]["c_001_priya_for_m001"])
    cust["consent"] = {"opted_in_at": None, "scope": []}
    cust["preferences"]["reminder_opt_in"] = False
    push(loaded, "customer", cust["customer_id"], cust, 2)
    t = data["triggers"]["trg_003_recall_due_priya"]
    push(loaded, "trigger", t["id"], t)
    acts = tick(loaded, [t["id"]])
    assert not any(a["send_as"] == "merchant_on_behalf" for a in acts)


def test_customer_context_missing_defers_then_sends(client, data):
    push(client, "category", "dentists", data["categories"]["dentists"])
    push(client, "merchant", "m_001_drmeera_dentist_delhi", data["merchants"]["m_001_drmeera_dentist_delhi"])
    t = data["triggers"]["trg_003_recall_due_priya"]
    push(client, "trigger", t["id"], t)
    assert tick(client, [t["id"]]) == []
    push(client, "customer", "c_001_priya_for_m001", data["customers"]["c_001_priya_for_m001"])
    acts = tick(client, [t["id"]], "2026-04-26T10:35:00Z")
    assert len(acts) == 1 and acts[0]["send_as"] == "merchant_on_behalf"
    assert "Priya" in acts[0]["body"] and "Wed 5 Nov" in acts[0]["body"]


def test_expired_trigger_skipped_when_clock_trusted(loaded, data):
    live = data["triggers"]["trg_002_compliance_dci_radiograph"]      # expires Dec 2026
    push(loaded, "trigger", live["id"], live)
    dead = copy.deepcopy(data["triggers"]["trg_004_perf_dip_bharat"])
    dead["expires_at"] = "2026-04-01T00:00:00Z"
    push(loaded, "trigger", dead["id"], dead)
    acts = tick(loaded, [dead["id"]])
    assert acts == []


def test_clock_skew_does_not_drop_everything(loaded, data):
    ids = list(data["triggers"])[:25]
    for i in ids:
        push(loaded, "trigger", i, data["triggers"][i])
    acts = tick(loaded, ids, "2026-09-27T00:00:00Z")   # wall clock far past most expiries
    assert len(acts) >= 5


def test_new_context_version_changes_the_message(loaded, data):
    m = copy.deepcopy(data["merchants"]["m_002_bharat_dentist_mumbai"])
    t = data["triggers"]["trg_004_perf_dip_bharat"]
    push(loaded, "trigger", t["id"], t)
    import bot
    from vera.composer import compose_full
    before = compose_full(data["categories"]["dentists"], m, t).body
    m["offers"] = [{"id": "o_new", "title": "Dental Cleaning @ ₹249", "status": "active"}]
    m["performance"]["ctr"] = 0.034
    push(loaded, "merchant", m["merchant_id"], m, 2)
    acts = tick(loaded, [t["id"]])
    assert acts and acts[0]["body"] != before
    assert "no live offer" not in acts[0]["body"].lower()


def test_opted_out_merchant_gets_no_proactive(loaded, data):
    t = data["triggers"]["trg_002_compliance_dci_radiograph"]
    push(loaded, "trigger", t["id"], t)
    loaded.post("/v1/reply", json={"conversation_id": "cx", "merchant_id": t["merchant_id"], "from_role": "merchant",
                                   "message": "Not interested, stop messaging", "turn_number": 2})
    assert tick(loaded, [t["id"]]) == []
