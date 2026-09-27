"""Multi-turn behaviour: auto-reply, intent transition, hostility, off-topic,
objections, waits, customer booking, anti-repetition."""
from conftest import push

MID = "m_001_drmeera_dentist_delhi"
QUALIFYING = ["would you", "do you", "can you tell", "what if", "how about"]


def reply(client, conv, msg, mid=MID, role="merchant", cid=None, turn=2):
    return client.post("/v1/reply", json={"conversation_id": conv, "merchant_id": mid, "customer_id": cid,
                                          "from_role": role, "message": msg, "turn_number": turn}).json()


def test_auto_reply_across_conversations_ends_fast(loaded):
    auto = "Thank you for contacting us! Our team will respond shortly."
    actions = [reply(loaded, f"conv_auto_{i}", auto)["action"] for i in range(1, 5)]
    assert actions[0] == "wait" and actions[1] == "end"


def test_repeated_identical_text_detected_as_auto_reply(loaded):
    msg = "We are happy to serve you, visit our store today"
    first = reply(loaded, "c_rep", msg)
    second = reply(loaded, "c_rep", msg, turn=3)
    assert second["action"] in ("wait", "end")


def test_intent_transition_goes_to_action(loaded):
    r = reply(loaded, "conv_intent_1", "Ok lets do it. Whats next?")
    assert r["action"] == "send"
    low = r["body"].lower()
    assert any(w in low for w in ["done", "draft", "here"]) and not any(q in low for q in QUALIFYING)


def test_intent_after_tick_delivers_artifact_then_confirms(loaded, data):
    t = data["triggers"]["trg_001_research_digest_dentists"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    r1 = reply(loaded, a["conversation_id"], "Yes please send it")
    assert r1["action"] == "send" and "draft" in r1["body"].lower()
    assert not any(q in r1["body"].lower() for q in QUALIFYING)
    r2 = reply(loaded, a["conversation_id"], "Confirm", turn=3)
    assert r2["action"] == "send" and r2["body"] != r1["body"]
    r3 = reply(loaded, a["conversation_id"], "thanks", turn=4)
    assert r3["action"] == "end"


def test_hostile_and_stop(loaded):
    assert reply(loaded, "h1", "Stop messaging me. This is useless spam.")["action"] == "end"
    r = reply(loaded, "h2", "You people are useless, always bothering", mid="m_002_bharat_dentist_mumbai")
    assert r["action"] in ("end", "send")
    if r["action"] == "send":
        assert "sorry" in r["body"].lower()


def test_off_topic_gst_is_declined_honestly(loaded):
    r = reply(loaded, "o1", "Btw can you also help me file my GST this month?")
    assert r["action"] == "send"
    low = r["body"].lower()
    assert "ca" in low and "outside" in low


def test_abuse_then_gst_stays_polite(loaded):
    reply(loaded, "o2", "This is bakwas, useless", mid="m_003_studio11_salon_hyderabad")
    r = reply(loaded, "o2", "can you file my GST?", mid="m_003_studio11_salon_hyderabad", turn=3)
    assert r["action"] == "send" and "outside" in r["body"].lower()


def test_later_waits(loaded):
    r = reply(loaded, "l1", "Busy right now, talk tomorrow")
    assert r["action"] == "wait" and r["wait_seconds"] >= 3600


def test_hinglish_reply_gets_hinglish(loaded):
    r = reply(loaded, "hi1", "haan theek hai, kar do", mid="m_005_pizzajunction_restaurant_delhi")
    low = r["body"].lower()
    assert r["action"] == "send" and ("ho gaya" in low or "ye raha" in low) and "kijiye" in low


def test_customer_slot_choice_books(loaded, data):
    t = data["triggers"]["trg_003_recall_due_priya"]
    push(loaded, "trigger", t["id"], t)
    a = loaded.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [t["id"]]}).json()["actions"][0]
    r = reply(loaded, a["conversation_id"], "2", role="customer", cid="c_001_priya_for_m001")
    assert r["action"] == "send" and "Thu 6 Nov" in r["body"]


def test_no_verbatim_repeats_in_a_conversation(loaded):
    bodies = []
    for i, msg in enumerate(["hmm", "what?", "hmm", "ok", "ok", "ok"]):
        r = reply(loaded, "rep1", msg, mid="m_006_southindiancafe_restaurant_bangalore", turn=i + 2)
        if r["action"] == "send":
            bodies.append(r["body"])
    assert len(bodies) == len(set(bodies))


def test_objections(loaded):
    for msg in ["too expensive", "we already do this", "tried it, didn't work", "I don't have time"]:
        r = reply(loaded, f"ob_{msg[:5]}", msg, mid="m_007_powerhouse_gym_bangalore")
        assert r["action"] in ("send", "end")
        if r["action"] == "send":
            assert not any(q in r["body"].lower() for q in ["would you be interested"])
