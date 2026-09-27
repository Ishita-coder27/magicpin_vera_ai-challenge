"""Property-style checks of compose() over the whole dataset, including
combinations the test set never uses (trigger x other merchants)."""
import copy
import json
import re

import pytest

from vera.composer import compose, compose_full
from vera.evidence import FactIndex
from vera.validate import ungrounded_numbers

TABOO_ALL = ["guaranteed", "100% safe", "miracle", "i hope you're doing well", "as an ai"]


def _cases(data):
    for p in data["pairs"]:
        t = data["triggers"][p["trigger_id"]]
        m = data["merchants"][p["merchant_id"]]
        c = data["customers"].get(p["customer_id"]) if p.get("customer_id") else None
        yield p["test_id"], data["categories"][m["category_slug"]], m, t, c


def test_all_test_pairs_valid_and_deterministic(data):
    for tid, cat, m, t, c in _cases(data):
        a = compose_full(cat, m, t, c)
        b = compose(cat, m, t, c)
        assert a.validation["ok"], (tid, a.validation)
        assert a.public() == b, f"{tid} not deterministic"
        low = a.body.lower()
        assert not any(x in low for x in TABOO_ALL), tid
        assert "http" not in low, tid
        if a.send_as == "vera":
            assert re.sub(r'"[^"]*"', "", a.body).count("?") <= 1, (tid, a.body)
        assert a.rationale and len(a.rationale) < 600


def test_customer_scope_send_as(data):
    for tid, cat, m, t, c in _cases(data):
        a = compose_full(cat, m, t, c)
        if a.send_as == "merchant_on_behalf":
            assert m["identity"]["name"].split()[0] in a.body, tid


def test_every_trigger_with_every_category_merchant(data):
    """Each trigger composed for 3 other merchants of any category must still be
    grounded (no invented numbers) and never crash."""
    merchants = list(data["merchants"].values())
    for i, t in enumerate(data["triggers"].values()):
        for m in (merchants[i % 50], merchants[(i * 7 + 3) % 50], merchants[(i * 13 + 11) % 50]):
            cat = data["categories"][m["category_slug"]]
            t2 = copy.deepcopy(t)
            t2["merchant_id"] = m["merchant_id"]
            cust = next((c for c in data["customers"].values() if c["merchant_id"] == m["merchant_id"]), None)
            comp = compose_full(cat, m, t2, cust if t2.get("scope") == "customer" else None)
            assert comp.body.strip()
            facts = FactIndex(cat, m, t2, cust)
            facts.register_many([e.value for e in comp.ctx.ledger if e.value is not None])
            facts.register_many([e.text for e in comp.ctx.ledger])
            assert not ungrounded_numbers(comp.body, facts), (t["id"], m["merchant_id"], comp.body)


@pytest.mark.parametrize("kind", ["weather_heatwave", "local_news_event", "brand_new_kind_x", "category_trend_movement",
                                  "unplanned_slot_open", "gst_deadline_reminder"])
def test_unseen_trigger_kinds(data, kind):
    m = data["merchants"]["m_009_apollo_pharmacy_jaipur"]
    t = {"id": f"t_{kind}", "scope": "merchant", "kind": kind, "merchant_id": m["merchant_id"],
         "payload": {"city": "Jaipur", "temp_c": 44, "headline": "Heatwave alert for Jaipur"}, "urgency": 3,
         "suppression_key": f"{kind}:x", "expires_at": "2026-12-01T00:00:00Z"}
    comp = compose_full(data["categories"]["pharmacies"], m, t)
    assert comp.body.strip() and comp.validation["ok"], comp.validation


def test_degenerate_inputs_do_not_crash():
    for args in [({}, {}, {}), (None, None, None), ({"slug": "x"}, {"merchant_id": "m"}, {"kind": "perf_dip"}),
                 ({}, {"identity": {"name": "A"}}, {"kind": "recall_due", "scope": "customer"})]:
        out = compose(*args)
        assert out["body"].strip() and out["send_as"] in ("vera", "merchant_on_behalf")


def test_new_digest_item_is_used(data):
    cat = copy.deepcopy(data["categories"]["dentists"])
    cat["digest"].append({"id": "d_new_x", "kind": "research", "title": "Silver diamine fluoride halts 81% of early caries in children",
                          "source": "IJDR Nov 2026, p.3", "trial_n": 640, "patient_segment": "children",
                          "summary": "Two-year trial across 6 cities. Best results with twice-yearly application."})
    m = data["merchants"]["m_001_drmeera_dentist_delhi"]
    t = {"id": "t_new", "scope": "merchant", "kind": "research_digest", "merchant_id": m["merchant_id"],
         "payload": {"top_item_id": "d_new_x"}, "urgency": 2, "suppression_key": "r:new"}
    comp = compose_full(cat, m, t)
    assert "81%" in comp.body and "IJDR" in comp.body and "640" in comp.body
    assert comp.validation["ok"]


def test_expired_offer_never_described_as_live(data):
    m = copy.deepcopy(data["merchants"]["m_001_drmeera_dentist_delhi"])
    m["offers"] = [{"id": "o1", "title": "Deep Cleaning @ ₹499", "status": "expired"}]
    t = {"id": "t", "scope": "merchant", "kind": "perf_dip", "merchant_id": m["merchant_id"],
         "payload": {"metric": "calls", "delta_pct": -0.3, "window": "7d"}, "urgency": 3, "suppression_key": "x"}
    comp = compose_full(data["categories"]["dentists"], m, t)
    assert "no live offer" in comp.body.lower() or "lapsed" in comp.body.lower()
    assert "your deep cleaning @ ₹499 is live" not in comp.body.lower()


def test_submission_file_shape():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "submission.jsonl"
    if not p.exists():
        pytest.skip("submission.jsonl not generated yet")
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    assert len(rows) == 30 and len({r["test_id"] for r in rows}) == 30
    for r in rows:
        assert {"test_id", "body", "cta", "send_as", "suppression_key", "rationale"} <= r.keys()


def test_placeholder_dip_does_not_claim_a_dip_the_data_contradicts(data):
    m = data["merchants"]["m_023_sushma_salon_pune"]           # views +8%, calls +2% week-on-week
    t = data["triggers"]["trg_031_perf_dip_m_023_sushma_salon_p"]
    body = compose(data["categories"]["salons"], m, t)["body"].lower()
    assert "fell" not in body and "dropped" not in body and "down" not in body


def test_open_merchant_request_is_acknowledged(data):
    m = data["merchants"]["m_001_drmeera_dentist_delhi"]       # asked for whitening + aligner posts
    t = data["triggers"]["trg_002_compliance_dci_radiograph"]
    out = compose(data["categories"]["dentists"], m, t)
    assert "open request" in out["rationale"] and "you asked" not in out["body"]


def test_consent_gap_routes_to_owner_not_customer(data):
    c = copy.deepcopy(data["customers"]["c_001_priya_for_m001"])
    c["consent"] = {"opted_in_at": None, "scope": []}
    c["preferences"]["reminder_opt_in"] = False
    out = compose_full(data["categories"]["dentists"], data["merchants"]["m_001_drmeera_dentist_delhi"],
                       data["triggers"]["trg_003_recall_due_priya"], c)
    assert out.send_as == "vera" and out.blocked
