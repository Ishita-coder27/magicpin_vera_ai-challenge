# Determinism report

| Test | Result | Observation |
|---|---|---|
| `compose()` on all 30 canonical pairs, 10 runs each | PASS | Identical body, cta, send_as, suppression_key, rationale, template on every run |
| `/v1/tick` over 40 triggers, 3 fresh engines | PASS | Byte-identical action lists (including conversation ids) |
| `/v1/reply` 4-turn sequence, 3 fresh engines | PASS | Identical actions and bodies |
| preflight: same compose ×5 | PASS | — |
| `submission.jsonl` vs a fresh run of the engine | PASS | preflight fails if they ever diverge |

## Why it is deterministic
- There is no randomness anywhere. Wording variation comes from `strategies/common.pick()`, which hashes the merchant id, trigger kind and trigger id with md5, so the same inputs always give the same phrasing.
- Candidate ranking sorts by (−score, trigger_id), so ties resolve the same way every time.
- Conversation ids are md5 hashes of the trigger id and suppression key.
- LLM mode is off by default. When enabled:
  - Sampling parameters are sent only to models that accept them (Opus 5 and Sonnet 5 reject `temperature`).
  - Outputs are memoised by a hash of the canonical, sorted-key JSON input, so identical inputs return identical text within a process.
  - Strict cross-process determinism with an LLM requires a model that accepts `temperature=0`, such as `claude-haiku-4-5`.

## The one time-dependent input
The judge's `now`, which is part of the request. Identical requests give identical results.
