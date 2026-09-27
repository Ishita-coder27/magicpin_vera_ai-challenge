# Vera Decision Engine

A merchant-engagement bot for the magicpin Vera challenge that decides **whether** to message, **what** single opportunity matters most, and **how** to say it — then proves every fact before it sends.

## Architecture

```
/v1/context → versioned store (409 on stale; atomic replace; delta per version)
/v1/tick    → for each available trigger: views → evidence ledger → strategy (WHAT)
              → value-of-action score → one message per merchant per tick
              → realizer (HOW) → hard validation → optional LLM polish (re-validated)
/v1/reply   → intent classifier → state machine (PROPOSE/DISCOVER/EXECUTE/OBJECTION/ANSWER/WAIT/END)
```

- `vera/strategies/` — one strategy per opportunity family (knowledge, compliance, perf dip/spike, milestone, event, competition, reputation, account, dormancy, curious-ask, planning, customer recall/refill/winback). Each answers: *what changed, what proves it, why this merchant, smallest action Vera can take*.
- `vera/policies.py` — category judgment as data (vocabulary, what customers are called, family weights, sensitivity, what Vera can deliver). A new vertical is data, not code.
- `vera/kinds.py` — trigger registry; unseen kinds are inferred from the kind string and payload shape.
- `vera/validate.py` — every number must trace to the contexts or a registered derivation; unknown proper nouns, taboo words, URLs, >1 question, re-qualifying after a "yes", and repeats are rejected. Failing beats are dropped; the fallback is always grounded.
- `vera/conversation.py` — deterministic reply handling. `bot.py` exposes the API and `compose(category, merchant, trigger, customer)`.

## Decision strategy

- **Contrarian where the data says so.** Saturday/Sunday IPL: skip the dine-in promo, save the Tue–Thu BOGO for weeknight matches, push delivery (and warn about the late-delivery reviews). New competitor at ₹199: don't price-match, lead with the reviews. Seasonal gym dip: hold ad spend, protect the 245 members. Diwali 188 days out: no discount yet, capture the April–May bridal window that's open now.
- **Merchant state beats trigger payload.** Many triggers carry placeholder payloads; the engine then reasons from the merchant's own numbers (peer gaps, subscription state, verification, lapsed customers, review themes) and never invents the missing event.
- **Thread continuity.** An open merchant request in history ("focus on whitening and aligners") is acknowledged; a planning intent ("what would it look like?") gets the artifact, not another question.
- **Restraint.** One message per merchant per tick, 15-minute attention gap, suppression keys honoured, no sends to opted-out or auto-replying numbers, consent checked per purpose, customer-scoped triggers deferred until the customer context exists. Expired triggers are dropped only when the judge's clock agrees with the trigger timeline (the local simulator sends wall-clock time).

## Conversations

Explicit yes → the draft itself in the next message, then one CONFIRM, then a status close. Canned or repeated auto-replies (tracked per merchant across conversations) → wait once, then end. STOP / not interested → end silently and mark do-not-contact. Hostile → one-line apology and close. Off-topic (GST, loans) → honest scope limit with a one-line redirect. Objections (cost, already doing it, tried before, no time) get a direct answer. Language follows the merchant's own messages; customer messages follow `language_pref` (Hinglish/Hindi in Roman script, regional greetings otherwise).

## Model choice and tradeoffs

The engine is **deterministic by default** — no LLM is needed and none is configured in this submission, so outputs are reproducible and fast (tick p95 ≈ 11 ms). An LLM (`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`; default `claude-opus-5` at low effort) can polish wording only: it receives the finished plan and evidence, and its output must pass the same validator or the deterministic text is used. Tradeoff: template realization is less varied than an LLM, in exchange for zero hallucination risk and no latency tail. Additional context that would help most: real appointment times/services and a consent-scope taxonomy.

## Run

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
python generate_submission.py          # writes submission.jsonl (30 lines, from the engine)
pytest -q                              # 71 tests incl. audit regressions (tests/regression)
python local_eval.py                   # simulated 60-min window + injection + personas (local heuristics)
python preflight.py [--url https://…]  # PASS/FAIL pre-submission report
python qa/redteam.py                   # 164-check red-team audit (see qa/ reports)
# official simulator: set BOT_URL / LLM_* at the top of judge_simulator.py, then
python judge_simulator.py
```

**Deploy** (Render): push the repo, create a web service from `render.yaml` (always-on plan; single worker because state is in memory), set `TEAM_NAME`, `TEAM_MEMBERS`, `CONTACT_EMAIL` (and optionally `LLM_*`), and verify with `python preflight.py --url https://<service>.onrender.com`. A `Dockerfile` and `Procfile` are included for other hosts. `POST /v1/teardown` wipes all state.
