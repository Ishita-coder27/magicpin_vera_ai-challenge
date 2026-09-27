# Message-quality baseline (before the optimisation pass) — 2026-09-27

The saved pre-pass output is `qa/submission_before_optimization.jsonl`.

## Official simulator (`judge_simulator.py` scorer, Gemini)
Only real, non-fallback scores are listed. Cases that hit HTTP 503/429 get the simulator's heuristic fallback score, and are excluded.

| Run | Model | Real scores |
|---|---|---|
| `full_evaluation` (seed triggers) | gemini-3.8-flash | 40, 40, 41, 40, 40 (the other 5 were 503 fallbacks) |
| canonical T01–T05 | gemini-3.8-flash | T01 39 · T02 39 · T03 38 · **T04 31** · T05 39 |

The free-tier key allows **20 requests per day per model**. That quota ran out before a full 30-case baseline could finish.

## Emulated judge (subagents using the simulator's exact prompt; not official)
v1 31.2 → v2 34.3 → v3 35.0 out of 50.

## Confirmed weaknesses going into the pass
1. **Dentist curious-ask:** a review theme was treated as a service ("is doctors' manner your most-asked service?").
2. **Post drafts:** titles were raw labels ("Dental Check Up Near Me at…", "What Sets You Apart at…"). The festival post also dropped the offer the message had used as its hook.
3. **Execution overclaims:**
   - renewal "payment link goes to your registered email"
   - completion lines "Done — posting the replies now"
   - "I'll put a reminder on your WhatsApp"
   - "I request verification"
4. **Search-demand trigger:** "190 searches" was answered with an unrelated research item.
5. **Thin triggers:**
   - an evidence-free seasonal message with a dangling "them"
   - a "heads-up: local news event."
   - "a small milestone on your listing this week"
6. **Repetition:** "Want me to draft…" ended 25 of 77 merchant messages, and the same action (verify listing, same offer) was re-pitched from different triggers.
7. **Gyms:**
   - "387 **members**" was really a total-customers count.
   - Gym research ended with "draft a **patient**-education WhatsApp".
   - The Hindi lapsed-member message was abrupt.
   - The trial-to-paid conversion lever was ignored.
8. **Auto-replies:** a templated greeting ("Hello! Welcome to X. Visit us or call during working hours.") was not recognised as an auto-reply.

## The 10 weakest canonical types (by real + emulated scores)
- Placeholder customer messages: T03, T04, T14, T15
- Placeholder merchant triggers: T10, T12, T17, T19, T23, T25
