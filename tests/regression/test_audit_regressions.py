"""Regression tests for every defect found in the red-team audit (qa/).
Each test names the original failure it guards against."""
import copy
import dataclasses

import pytest

from conftest import push
from vera import consent
from vera.composer import compose, compose_full
from vera.validate import _NUM, preserves_facts
from vera.views import CustomerView

MID = "m_001_drmeera_dentist_delhi"


def _reply(client, conv, msg, mid=MID, role="merchant", cid=None, turn=2, received_at=None):
    body = {"conversation_id": conv, "merchant_id": mid, "customer_id": cid, "from_role": role, "message": msg,
            "turn_number": turn}
    if received_at:
        body["received_at"] = received_at
    return client.post("/v1/reply", json=body).json()


# B4 — consent scopes were substring-matched ("promotional_offers_x" passed)
def test_consent_scope_is_exact(data):
    c = copy.deepcopy(data["customers"]["c_001_priya_for_m001"])
    c["consent"] = {"opted_in_at": "2025-01-01", "scope": ["promotional_offers_x"]}
    c["preferences"]["reminder_opt_in"] = False
    assert not consent.check(CustomerView(c), c["merchant_id"], "recall").ok
    c["consent"]["scope"] = ["recall_reminders"]
    assert consent.check(CustomerView(c), c["merchant_id"], "recall").ok


# B3 — LLM rewrites with fake citation / social proof / slot passed validation
@pytest.mark.parametrize("rewrite", [
    "Lakshmi, per Vogue India May 2026, balayage demand doubled — want me to draft a post?",
    "Lakshmi, 12 salons in your area already did this — want me to draft a post?",
    "Lakshmi, I booked Tue 9 Jun 4:30pm for your shoot — want me to confirm?",
])
def test_llm_rewrite_cannot_add_facts(monkeypatch, data, rewrite):
    import vera.composer as composer
    import vera.llm as llm
    monkeypatch.setattr(composer, "CONFIG", dataclasses.replace(composer.CONFIG, llm_provider="anthropic", llm_api_key="x"))
    monkeypatch.setattr(llm, "polish", lambda p: rewrite)
    m, t = data["merchants"]["m_003_studio11_salon_hyderabad"], data["triggers"]["trg_008_curious_ask_studio11"]
    base = composer.compose_full(data["categories"]["salons"], m, t, use_llm=False).body
    out = composer.compose_full(data["categories"]["salons"], m, t)
    assert out.body == base and not out.audit["llm_used"]


def test_preserves_facts_allows_pure_rephrase():
    d = "Suresh, 22 reviews praise your thali. Want me to draft a review request?"
    assert preserves_facts(d, "Suresh, your thali has 22 reviews praising it. Shall I draft a review request?")
    assert not preserves_facts(d, "Suresh, 22 restaurants copied your thali. Shall I draft a review request?")


# B1 — "your Pro plan ends in None days"
@pytest.mark.parametrize("sub", [{"status": "expired", "plan": "Pro", "days_since_expiry": 20}, {}, {"status": "active"}])
def test_renewal_never_leaks_none(data, sub):
    m = copy.deepcopy(data["merchants"]["m_002_bharat_dentist_mumbai"])
    m["subscription"] = sub
    t = {"id": "t", "scope": "merchant", "kind": "renewal_due", "merchant_id": m["merchant_id"], "payload": {}, "urgency": 4}
    out = compose_full(data["categories"]["dentists"], m, t)
    assert "None" not in out.body and out.validation["ok"]


def test_validator_rejects_template_leaks():
    from vera.evidence import FactIndex
    from vera.validate import validate
    assert not validate("Hi, your plan ends in None days. Want me to renew?", facts=FactIndex()).ok


# B2 — festivals in the past / today / malformed dates rendered as "-1 days away"
@pytest.mark.parametrize("days,date,forbidden", [(-1, "2026-04-25", "-1"), (0, "2026-04-26", "0 days"), (None, "31/13/2026", "31/13")])
def test_festival_dates(data, days, date, forbidden):
    p = {"festival": "Ugadi", "date": date}
    if days is not None:
        p["days_until"] = days
    t = {"id": "t", "scope": "merchant", "kind": "festival_upcoming", "merchant_id": "m_003_studio11_salon_hyderabad",
         "payload": p, "urgency": 2}
    out = compose_full(data["categories"]["salons"], data["merchants"]["m_003_studio11_salon_hyderabad"], t)
    assert forbidden not in out.body
    if days is not None and days < 0:
        assert out.blocked            # past events are never sent


# Tokenizer read inside codes ("AT2025-0001" -> "025") and blocked a real recall
def test_number_tokenizer_skips_codes():
    assert [m.group() for m in _NUM.finditer("batches AT2025-0001 and AT2024-1108")] == []
    assert [m.group() for m in _NUM.finditer("₹1,499 and 38%")] == ["₹1,499", "38"]


def test_new_recall_with_new_batches_is_sent(loaded, data):
    t = data["triggers"]["trg_018_supply_atorvastatin_recall"]
    push(loaded, "trigger", t["id"], t)
    assert len(loaded.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": [t["id"]]}).json()["actions"]) == 1
    t2 = {**copy.deepcopy(t), "id": "trg_018b", "suppression_key": "alert:new:2026-05"}
    t2["payload"]["affected_batches"] = ["AT2025-0001"]
    push(loaded, "trigger", t2["id"], t2)
    acts = loaded.post("/v1/tick", json={"now": "2026-04-26T16:00:00Z", "available_triggers": [t2["id"]]}).json()["actions"]
    assert len(acts) == 1 and "AT2025-0001" in acts[0]["body"]


# Merchant asked for time on one thread but got a nudge from another trigger
def test_wait_is_a_merchant_level_hold(loaded, data):
    for i in ("trg_001_research_digest_dentists", "trg_023_competitor_opened_dentist"):
        push(loaded, "trigger", i, data["triggers"][i])
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["trg_001_research_digest_dentists"]}).json()["actions"][0]
    r = _reply(loaded, a["conversation_id"], "busy today, talk tomorrow", received_at="2026-04-26T10:40:00Z")
    assert r["action"] == "wait"
    held = loaded.post("/v1/tick", json={"now": "2026-04-26T11:30:00Z", "available_triggers": ["trg_023_competitor_opened_dentist"]}).json()
    assert held["actions"] == []
    later = loaded.post("/v1/tick", json={"now": "2026-04-27T12:00:00Z", "available_triggers": ["trg_023_competitor_opened_dentist"]}).json()
    assert len(later["actions"]) == 1


# "why?" / "what exactly?" / weather / bitcoin got a generic "I don't have that detail"
@pytest.mark.parametrize("msg", ["why?", "what exactly?", "What's the weather?", "What is Bitcoin?", "Tell me a joke."])
def test_no_generic_non_answer(loaded, data, msg):
    t = data["triggers"]["trg_004_perf_dip_bharat"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    r = _reply(loaded, a["conversation_id"], msg, mid=a["merchant_id"])
    assert r["action"] == "send" and "I don't have that detail" not in r["body"]


# Merchant's price after a draft was acknowledged but never applied
def test_merchant_price_is_applied(loaded, data):
    t = data["triggers"]["trg_008_curious_ask_studio11"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    _reply(loaded, a["conversation_id"], "Mostly keratin these days", mid=a["merchant_id"])
    r = _reply(loaded, a["conversation_id"], "₹2,200", mid=a["merchant_id"], turn=3)
    assert "Keratin @ ₹2,200" in r["body"]


# "Please confirm and post the replies" was treated as extra info
def test_confirm_mid_sentence_is_acceptance(loaded, data):
    t = data["triggers"]["trg_011_review_theme_late_delivery"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    # The first message already carries the drafted replies (artifact-led), so
    # a mid-sentence confirmation must approve them straight away.
    r = _reply(loaded, a["conversation_id"], "Please confirm and post the replies.", mid=a["merchant_id"])
    assert r["body"].startswith("Approved") and "queued" in r["body"]


# Paused offers were described as "lapsed"
def test_paused_offer_wording(data):
    m = copy.deepcopy(data["merchants"]["m_002_bharat_dentist_mumbai"])
    m["offers"] = [{"id": "o", "title": "Dental Cleaning @ ₹299", "status": "paused"}]
    t = data["triggers"]["trg_004_perf_dip_bharat"]
    b = compose(data["categories"]["dentists"], m, t)["body"]
    assert "is paused" in b and "has expired" not in b


# Spike copy claimed "views are climbing" with a 0% change
def test_flat_metrics_no_growth_claim(data):
    m = copy.deepcopy(data["merchants"]["m_010_sunrisepharm_pharmacy_lucknow"])
    m["performance"]["delta_7d"] = {"views_pct": 0.0, "calls_pct": 0.0}
    t = {"id": "t", "scope": "merchant", "kind": "perf_spike", "merchant_id": m["merchant_id"], "payload": {"placeholder": True}, "urgency": 1}
    assert "climbing" not in compose(data["categories"]["pharmacies"], m, t)["body"]


# Test A finding: a near-duplicate was "repaired" by stripping its justification and sent anyway
def test_repeat_with_no_new_facts_is_not_sent(data):
    m, t = data["merchants"]["m_002_bharat_dentist_mumbai"], data["triggers"]["trg_004_perf_dip_bharat"]
    first = compose_full(data["categories"]["dentists"], m, t)
    # Second time: same offer again -> advance to the draft itself, don't re-ask.
    second = compose_full(data["categories"]["dentists"], m, {**t, "suppression_key": "o2"}, prior_bodies=[first.body])
    assert not second.blocked and second.draft.extra.get("strategy") == "artifact-led" and second.body != first.body
    # Third time with nothing new: no send, never a degraded copy.
    third = compose_full(data["categories"]["dentists"], m, {**t, "suppression_key": "o3"}, prior_bodies=[first.body, second.body])
    assert third.blocked and "no new information" in third.draft.extra["block_reason"]


# Test B finding: the "delivery-only" match-night draft said "walk in" and asked for a price
def test_delivery_only_promise_is_kept_in_the_draft(loaded, data):
    t = data["triggers"]["trg_010_ipl_match_delhi"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    r = _reply(loaded, a["conversation_id"], "ok do it", mid=a["merchant_id"])
    low = r["body"].lower()
    assert "deliver" in low and "walk in" not in low and "price" not in low and "Vs MI" not in r["body"]


# Test C finding: a demand trigger carrying "190 searches" was answered with an unrelated digest item
def test_demand_trigger_uses_its_own_numbers(data):
    m = data["merchants"]["m_001_drmeera_dentist_delhi"]
    t = {"id": "t", "scope": "merchant", "kind": "category_trend_movement", "merchant_id": m["merchant_id"], "urgency": 3,
         "payload": {"query": "dental check up near me", "searches_30d": 190}}
    b = compose(data["categories"]["dentists"], m, t)["body"]
    assert "190" in b and "dental check up near me" in b and "Dental Cleaning @ ₹299" in b and "JIDA" not in b


# Test A finding: merchants were messaged with no CategoryContext (no taboo list, no peer data)
def test_tick_defers_until_category_context_exists(client, data):
    m, t = data["merchants"]["m_002_bharat_dentist_mumbai"], data["triggers"]["trg_004_perf_dip_bharat"]
    push(client, "merchant", m["merchant_id"], m)
    push(client, "trigger", t["id"], t)
    assert client.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"] == []
    push(client, "category", "dentists", data["categories"]["dentists"])
    assert len(client.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": [t["id"]]}).json()["actions"]) == 1



# Live LLM finding: an accepted rewrite dropped "but it's a Sunday game" (the decision's premise)
def test_rewrite_may_not_drop_load_bearing_facts():
    d = "Suresh, DC vs MI is on tonight (7:30pm) — but it's a Sunday game. Saturday games cut covers by 12%. Want me to draft a delivery post?"
    assert not preserves_facts(d, "Suresh, DC vs MI is on tonight at 7:30pm. Saturday games cut covers by 12%. Want me to draft a delivery post?")
    assert not preserves_facts(d, "Suresh, DC vs MI is tonight, a Sunday game. Saturday games cut covers. Want me to draft a delivery post?")
    assert preserves_facts(d, "Suresh, tonight's DC vs MI (7:30pm) is a Sunday game, and Saturday games cut covers by 12%. Shall I draft a delivery post?")
