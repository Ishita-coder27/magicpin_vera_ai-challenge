# Official simulator report

## What could not be run
`judge_simulator.py` needs an LLM provider key for its scorer, set at the top of the file. No key exists in this environment, so **official scores for decision quality, specificity, category fit, merchant fit and engagement were not produced**. No scores are claimed here.

To run it:
```bash
# in judge_simulator.py: BOT_URL, LLM_PROVIDER, LLM_API_KEY (optionally LLM_MODEL)
python judge_simulator.py      # TEST_SCENARIO = "all" or "full_evaluation"
```

## What was run
The simulator's own classes (`JudgeSimulator`, `BotClient`, the scenario functions) were driven with a stub in place of the LLM scorer. Its scores are placeholders and are ignored; the scenario pass/fail logic is the official code. It ran against the production Docker image on :8090.

| Scenario | Result | Observation |
|---|---|---|
| warmup (healthz, metadata, context push) | PASS | all pushes accepted |
| auto_reply_hell (same canned text, new conversation id each turn) | PASS | turn 1 `wait 14400`, turn 2 `end` |
| intent_transition ("Ok lets do it. Whats next?") | PASS | "Done — here's the draft: …", none of the banned re-qualifying phrases |
| hostile ("Stop messaging me. This is useless spam.") | PASS | `end` |
| full_evaluation (25 seed triggers in batches of 5) | runs | 10 actions, 3–5 ms per tick; 0 errors or timeouts |

The 15 skipped `full_evaluation` triggers break down as:
- **5 customer-scoped triggers**: the simulator never pushes customer contexts, so the bot defers them rather than messaging an unknown customer.
- **The rest**: the one-message-per-merchant-per-tick rule, plus the 15-minute attention gap. The simulator sends every batch with the same wall-clock `now`.

## Substitute quality signal (not official)
- Fresh subagents scored the 30 submission messages using the simulator's exact system prompt and scoring prompt, plus fabrication checks against the full dataset.
- Averages across three rounds: 31.2 → 34.3 → 35.0 out of 50.
- These are emulations for development and must not be read as magicpin scores.
