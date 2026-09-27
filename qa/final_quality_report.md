# Vera Final Quality Report — 2026-09-27

## 1. Executive Assessment
**READY WITH RISKS.**

What was verified:
- No critical functional, consent or hallucination defects remain known after the audit.
- Deterministic behaviour, context versioning, suppression, conversations, fallback and latency are all verified by tests.

What remains open:
- Official LLM-scored judging has not been run (no API key in this environment).
- Deployment has not been done.
- Team metadata is unset.

## 2. What Was Tested
| Suite | Count | Result |
|---|---|---|
| Unit + integration tests (`pytest`) | 47 | pass |
| Regression tests from this audit (`tests/regression/`) | 24 | pass |
| Red-team checks (`qa/redteam.py`) | 164 | pass (baseline was 123/141) |
| Synthetic adversarial merchant scenarios | 160 | pass |
| Customer × trigger combinations | 200 | pass |
| Full dataset triggers | 100 | pass |
| Conversation variants (yes / negatives / objections / auto-reply / off-topic) | 46 | pass |
| Performance requests | 60 ticks + 200 replies + 200 context pushes + 50 healthz + 200 concurrent replies | within budget |
| Concurrency (mixed context/tick/reply, 16 threads) | 300 calls | 0 errors |
| Preflight vs production container | 32 checks | all pass (with `TEAM_NAME` set) |
| Official simulator scenarios (official code, stub scorer) | 4 | pass |

## 3. Official Simulator Results
The scenario logic passes. LLM scoring was not executed: no provider key. See official_simulator_report.md.

## 4. Functional Results
| Test | Result | Observation |
|---|---|---|
| Version sequence 1,1,0,2,2,3,1 for all four scopes | PASS | Responses 200,409,409,200,409,200,409; v3 stays stored |
| Merchant v1→v2→replay v1 | PASS | The message reflects v2; the v1 replay gets 409 and output is unchanged |
| Context delta (CTR recovered, offer expired or paused, digest updated, consent revoked) | PASS | Output follows the newest state |
| Suppression (4 ticks of the same trigger) | PASS | Sent once; a new key is sent; a reused key is suppressed; merchant and customer scopes are independent |
| No-send cases | PASS | Unknown trigger, unknown merchant, missing customer context, opted-out merchant, expired trigger, past festival |
| Merchant WAIT hold | PASS | No proactive nudge inside the wait window; resumes afterwards |

## 5. Adversarial Results
- 160 randomised merchants across 22 trigger kinds, including unseen ones, with no/many/expired/paused offers, extreme metrics, expired plans and empty history.
- Result: 0 crashes, 0 ungrounded numbers, 0 inactive offers presented as live, 0 multi-question messages, 0 template leaks.
- Contrarian checks pass: weekend IPL gets no dine-in promo; a competitor gets no price match; the seasonal dip gets "hold ad spend, retain members"; Diwali 188 days out gets no promo; a recovered placeholder dip gets no "fell X%".
- Ranking checks pass in both input orders:
  - compliance beats promotion and research
  - the supply alert beats seasonal content
  - planning intent beats a milestone or a spike
  - a live engaged thread blocks low-urgency nudges

## 6. Conversation Results
- **Execution**: 18 yes-variants in English, Hindi, Hinglish and emoji all go straight to the draft, with no re-qualifying.
- **Negatives**: hard stops end the conversation, deferrals wait, and objections get a direct answer.
- **Auto-replies**: 5 auto-reply forms, plus near-identical and repeated templated greetings, end within 2 turns with no sends.
- **Off-topic**: questions like weather, GST, jokes, Bitcoin and loans get an honest scope limit.
- **Flow**: the curious-ask answer drives the post; a merchant-supplied price is applied; confirm closes the loop; language follows the merchant each turn.

## 7. Grounding / Hallucination Results
- Every number must trace to the contexts or a registered derivation.
- LLM rewrites must keep each number with the same unit word, and may not introduce new proper nouns.
- Ten bait types were rejected: fake price, location, statistic, citation, social proof, slot, URL, multiple asks, taboo word, and empty output.
- Prompt injection through merchant name, conversation history, offer title and customer reply was not followed, and nothing leaked.

## 8. Category Results
- No cross-category vocabulary leakage across all 100 triggers.
- Category policies change decisions, not just nouns:
  - pharmacy and dentist messages weight compliance highest, avoid leading with discounts, and carry extra taboos
  - gyms get retention-first reframing
  - restaurants are event- and delivery-aware
- Remaining weakness: emulated judges still rate category voice around 7/10; the gym voice is flat.

## 9. Performance Results
In-process, milliseconds:

| Endpoint | p50 | p95 | p99 | max |
|---|---|---|---|---|
| tick (100 triggers) | 3.3 | 34.6 | 62.2 | 57.6 |
| reply | 1.2 | 1.6 | 1.6 | 1.7 |
| context | 0.7 | 0.8 | 0.8 | 0.9 |
| healthz | 0.8 | 0.9 | 1.0 | 1.0 |

- 10-way concurrent replies: p95 21.7 ms. Over HTTP to the production container: p95 26 ms.
- Cold start 0.28 s; memory 42 MB.
- Headroom is more than 200× the 15 s simulator timeout.

## 10. Reliability Results
- 0 schema violations and 0 unhandled exceptions across all suites.
- 300 concurrent mixed calls produced no errors, and the newest version won.
- A crash in the tick or reply handler returns `{"actions": []}` or a safe `wait`.

## 11. Security / Privacy Results
- No stack traces, file paths or keys appear in error responses or metadata.
- No secrets in shipped code; keys come only from the environment.
- `/v1/teardown` wipes all state.
- The only external call is the optional LLM API, and it is disabled by default.

## 12. Major Bugs Found
Details are in baseline_report.md.
1. Consent-scope substring bypass
2. LLM guard allowed new facts
3. Template leak ("None days")
4. Past, today and malformed festival dates
5. Number tokenizer reading inside codes
6. Cross-conversation duplicate threshold too strict
7. why/what/off-topic answered with a non-answer
8. Merchant price not applied
9. Mid-sentence "confirm" missed
10. Merchant WAIT ignored by tick
11. Growth claimed on flat metrics
12. CTA not last

## 13. Bugs Fixed
All 12, each with a root-cause fix and a regression test in `tests/regression/test_audit_regressions.py`:
- **Consent**: exact scope matching.
- **LLM guard**: `validate.preserves_facts`.
- **Template leaks**: validator rejection plus null guards.
- **Festival dates**: past events blocked, today/tomorrow wording, unparseable dates suppressed.
- **Number tokenizer**: now boundary-aware.
- **Duplicate threshold**: 0.92 across conversations, 0.8 within one.
- **New intents**: why, what, off-topic fallback.
- **Merchant price**: folded into the draft.
- **Confirm**: matched anywhere in the sentence.
- **WAIT**: a merchant-level `hold_until` that tick respects.
- **Growth claims**: spike copy only claims growth the data shows.
- **CTA**: T16 fixed.

## 14. Remaining Risks
See top_risks.md.

## 15. Why This Implementation Stands Out
Each item has code and tests behind it:
- **Deterministic decision layer with LLM limited to wording.** `planner.py` decides what to send. `llm.py` only rephrases, and its output is re-validated and rejected if it changes facts (`tests/test_llm_guard.py`, regression tests).
- **Hallucination firewall.** An evidence ledger and number/name grounding cover every outbound body, derived numbers included (red-team coverage, 160 adversarial cases).
- **Contrarian, context-adjusted decisions.** Expected vs adjusted action is recorded in the rationale (red-team ranking section).
- **Signal auction with restraint.** One message per merchant per tick; live-thread protection; the merchant WAIT hold; suppression by key (decision tests, red-team).
- **Consent safety per purpose.** Customer outreach is withheld and routed to the owner when consent does not cover it (consent tests).
- **Conversation state machine.** Immediate execution on yes; cross-conversation auto-reply detection; objection handling; honest scope limits (46 variants).
- **Adaptation to fresh context.** Newest version wins, stale versions are rejected, and new digest items and customers are picked up mid-test (local_eval adaptation checks).

## 16. Deployment Readiness
- The Docker image builds and runs in production mode, passing all 32 preflight checks.
- A fresh clone installs from `requirements.txt` and passes 71/71 tests.
- **Not deployed**, as instructed.

## 17. Exact Remaining Actions
1. Set `TEAM_NAME`, `TEAM_MEMBERS` and `CONTACT_EMAIL`.
2. Run `judge_simulator.py` with an LLM key against the local bot and review the weakest cases.
3. Deploy to an always-on single-worker host and run `python preflight.py --url <public url>`.
4. Optionally enable LLM polish, then re-run preflight and `qa/redteam.py`.
