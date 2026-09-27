# Message-quality final report — 2026-09-27

These are internal measurements. **No official magicpin score is claimed.** The official scorer could only run partially (see the Official scoring section).

## Before vs after

| Measure | Before | After |
|---|---|---|
| pytest | 75 | **95** (+20 quality-pass regression tests) |
| red-team checks | 164 | **165**, all pass |
| unseen suite (220 new scenarios) | not built yet; first mid-pass run found 4 auto-reply misses and 1 repetition pair | **0 failures on every metric** |
| execution overclaims in replies | present (renewal email/payment link, "posting now", reminders, "I request verification") | **0**, enforced by a linter |
| canonical 30: anchored messages | 29/30 | 29/30 (the remaining one is a customer message; its detector false positive is fixed) |
| canonical 30: top CTA-frame share | 0.53 | 0.50 |
| canonical 30: messages changed | — | 3 (T12, T19, T22); none regressed |
| dataset merchant messages ending "Want me to draft…" | 25/77 | reduced by artifact-led strategy (inline draft when the same action was already offered) |
| determinism (30 × 10) | pass | pass |
| latency, unseen suite | — | p50 1.5 · p95 2.9 · p99 3.8 · max 7.1 ms |

## Unseen suite (`qa/unseen200.py`, policy A)
There were 240 scenario runs: 50 conflicting-signal, 30 sparse, 30 consent, 20 context-update, 20 active-conversation, 20 auto-reply, 20 obvious-action-wrong, 30 category-specific and 20 repetition sequences. They produced 138 messages, checked against these rates:

| Metric | Result |
|---|---|
| generic | 0% |
| unsupported claims | 0% |
| semantic repetition | 0 pairs |
| CTA conflicts | 0 |
| category mismatch | 0 |
| merchant mismatch | 0 |
| execution overclaims | 0 |
| stale context | 0/20 |
| consent failures | 0/30 |
| conversation-state failures | 0 |
| auto-reply failures | 0/20 |
| obvious action taken | 0/20 |
| live-thread interruptions | 0/20 |
| priority errors | 0 |
| deterministic | yes |

## Defects fixed in this pass
1. **Curious-ask semantics.** Services come only from catalog service names, or allowed vocabulary that also appears in a catalog title or trend query. Review themes are never services; the fallback is a service-bearing trend ("are clear aligners your most-asked treatment?"). A "yes" adopts the guessed service.
2. **Post drafts.**
   - `post_headline` strips query and label noise ("near me", "price"), city and locality, and internal topics, and keeps small words lowercase ("DC vs MI").
   - The hook offer is preserved.
   - Posts use a real review quote.
   - Each category has its own post CTA.
3. **Execution honesty.** Every deliverable is now *produced* (a draft), *queued / kept / noted* (recorded in this system), or a *hand-off* (payment, Google verification). `validate.overclaims` rejects claimed side effects.
4. **Direct trigger evidence.** `demand_signal` uses the payload's own query and count, anchors on the matching live offer, and wins over secondary digest items.
5. **Sparse triggers.**
   - Evidence-free seasonal, event and milestone triggers → no-send.
   - Anchor injection adds the merchant's strongest real fact when a message has none.
   - Category-level knowledge (research, compliance) is exempt, to avoid cosmetic anchors.
6. **Strategy diversity.** `select_strategy` labels every message:
   - question-led, continuation-led, action-led, artifact-led, or proof/metric/opportunity-led.
   - Artifact-led is used for short drafts, or when the *same action* (CTA frame, offer or exact ask) was already offered to this merchant and ignored.
   - A CTA the merchant said yes to counts as continuity, not repetition.
7. **Gyms.** The trial-to-paid conversion lever is used (28% vs 32% peers). "Customers" replaces the mislabeled "members". Knowledge artifacts are category-specific (no "patient" wording outside dentists). Lapsed-member messages are no-shame and give a reason to return.
8. **Other.**
   - Templated greeting auto-replies are now detected.
   - Hinglish approval messages are fully Hinglish.
   - The seasonal-beat sentence keeps its subject ("Oct-Dec brings the wedding whitening peak (…)").
   - Journal issues and organisations get different hooks.

## Policy A vs B (one vs two proactive messages per merchant per tick)
- **Unseen suite:** B added 29 extra same-tick sends, and every quality metric was the same as A's.
- **Seed data:** both of B's extra messages overlapped with the one the merchant had just received. Bharat's renewal message pushed the same offer as his perf-dip message; Suresh's review-theme message repeated the IPL message's late-delivery point.
- **Decision: keep A.** B remains an experiment flag (`engine.POLICY`).

## Official scoring
The free-tier Gemini key allows 20 requests per day per model. `gemini-3.8-flash` and `gemini-3.7-flash` were both exhausted on 2026-09-27 (quotas reset at 00:00 PT = 07:00 UTC).

Real scores so far are all pre-pass (the five `full_evaluation` 40–41s, and T01–T05 above). The before/after official comparison on the 10 target cases (`qa/subset10_before.jsonl` vs the same IDs in `submission.jsonl`) is ready to run as soon as a key with quota is in `.env`.

## Weakest remaining message types
- **Placeholder customer messages** (appointment/recall with no time, service or offer, e.g. T03/T04): honest but thin. The data doesn't contain what would make them specific.
- **Placeholder merchant triggers with no review data:** they rely on peer-gap and account-state anchors.

## Guardrails confirmed unchanged
- Deterministic decision layer.
- Evidence grounding (every number traced).
- Consent (exact scope).
- Suppression.
- Context versioning (stale rejection).
- Cross-merchant no-repeat.
- LLM may only reword, with the validator applied after it.
- No ID-based branching (grep-verified).
