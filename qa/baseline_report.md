# Baseline (before audit fixes) — 2026-09-27

The project was measured exactly as it stood at the start of the audit.

## API
All 6 endpoints respond. Cold start 0.28 s, resident memory 42 MB. Malformed context returned 400, stale version returned 409.

## Functional tests
- pytest: 47/47 pass.
- local_eval.py: PASS (95 sends for 102 triggers, 0 schema errors, 0 ungrounded numbers).
- preflight.py: everything passed except `TEAM_NAME` not configured.

## Official simulator (`judge_simulator.py`)
- The scenario logic was run through the official code, with a stub standing in for the LLM scorer.
- auto_reply, intent and hostile scenarios all PASS; `full_evaluation` produced 10 actions at 3–5 ms per tick.
- LLM scoring was **not run**: no provider key exists in this environment (`LLM_API_KEY` / `ANTHROPIC_API_KEY` unset).

## Latency (in-process)
context p95 0.7 ms · tick p95 11.6 ms · reply p95 2.5 ms.

## Red-team baseline (`qa/redteam.py`, first run)
141 checks, **123 pass / 18 fail**. Three of the failures turned out to be harness artifacts; 15 were real defects:

| Area | Defect |
|---|---|
| Consent | Consent scopes were substring-matched: a scope named `promotional_offers_x` authorised customer outreach. |
| Hallucination | LLM rewrites containing a fake citation, fake social proof ("12 salons in your area") or a fake appointment slot passed validation. |
| Template leak | "your Pro plan ends in **None** days" (expired subscription). |
| Dates | Past, same-day and malformed festival dates rendered as "-1 days away", "0 days away" and "31/13/2026". |
| Grounding | The number tokenizer read inside codes ("AT2025-0001" → "025"), which blocked a genuine new batch recall. |
| Suppression | The cross-conversation near-duplicate threshold (0.8) blocked legitimately new messages. |
| Conversation | "why?", "what exactly?", weather and Bitcoin all got a generic "I don't have that detail". |
| Conversation | A merchant-supplied price after a draft was acknowledged but never applied. |
| Conversation | "Please confirm and post the replies" was not recognised as a confirmation. |
| CTA | T16 ended with a statement after the question. |

A second wave of attacks found two more real defects:
- A merchant who asked for time on one thread was nudged from another trigger an hour later.
- "Views are climbing" was claimed with a 0% change.

## Major risks at baseline
Consent bypass via scope names, and an LLM path able to add unverified facts. Both were fixed; see final_quality_report.md.
