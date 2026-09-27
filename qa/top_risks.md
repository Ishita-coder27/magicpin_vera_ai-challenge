# Top 10 failure risks

Ranked by impact × likelihood, with how hard each would be to spot from a remote judge run.

| # | Risk | Level | Evidence | Mitigation / action |
|---|---|---|---|---|
| 1 | **Official LLM judging never executed.** Quality is known only via emulation. | High | No provider key in the environment; see official_simulator_report.md | Run `judge_simulator.py` with a key before submitting and review the weakest cases. |
| 2 | **Placeholder-payload triggers get weaker messages.** 15 of 30 test pairs carry `{"placeholder": true}`. | High | Emulated scores 23–32 on these vs 38–44 on data-rich triggers | The engine reasons from merchant data and never invents the event. The remaining gap is data, not a bug. |
| 3 | **State is in memory only.** A restart or a second worker loses contexts and conversations. | High (operational) | By design (`ContextStore`, `Engine`) | Deploy on an always-on single-worker instance. `render.yaml` and the Dockerfile set `--workers 1`; free tiers sleep and would fail healthz. |
| 4 | **Template realisation reads repetitive across many messages.** | Medium | Judges flagged repeated sentence frames (offer line, dormant opener) | Deterministic variants via `pick()`. An optional validated LLM polish exists but has not been tried live. |
| 5 | **Restraint may cost points if the judge sums scores instead of averaging.** | Medium | One send per merchant per tick; a 15-min gap; customer triggers deferred | Intended per the brief ("restraint is rewarded"). The thresholds are one constant each in `engine.py`. |
| 6 | **The clock-skew heuristic** could let genuinely expired triggers through if most stored triggers look expired. | Medium | `Engine.clock_trusted` | Needed because the local simulator sends wall-clock time against an April dataset. The judge's `available_triggers` remains the authority. |
| 7 | **Consent interpretation.** `promotional_offers` authorises recall and win-back; `reminder_opt_in: true` authorises transactional reminders. | Medium | `consent._PURPOSE_SCOPES` | A documented policy; exact-scope matching is now enforced. The judge may read consent more strictly. |
| 8 | **Merchant language policy.** Messages are English unless the merchant's own messages are Hindi. | Medium | Judges disagreed: one penalised tacked-on Hinglish, another wanted more code-mix | One rule in `language.for_merchant`. |
| 9 | **Rule-based intent classifier** can misroute unusual phrasing, e.g. "yes but not this week". | Medium | 18 yes-variants, 10 negatives, 8 objections, 5 auto-reply forms, 5 off-topic all pass | Unknown text falls back to safe paths: execute on engagement, an honest non-answer, or an off-topic scope message. |
| 10 | **The LLM path has only been tested with mocks.** | Low (disabled by default) | Tests prove hallucinated or failed output falls back | Only enable after a live run passes preflight. |

Also outstanding: `TEAM_NAME`, `TEAM_MEMBERS` and `CONTACT_EMAIL` are unset. This is Low risk, but preflight fails until they are set.
