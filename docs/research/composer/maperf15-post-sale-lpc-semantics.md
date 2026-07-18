# Research Report: MAPERF-15 — Composer `last_percent_change` Semantics After a Guard-Triggered Go-To-Cash Sale

**Researcher:** composer-api-researcher (maperf15-probe)
**Date:** 2026-07-17
**Confidence Summary:** `[High]` for the observed behavior (post-trigger `last_percent_change` keeps moving with substantial, bidirectional, symphony-specific divergence), `[Medium-High]` for the interpretation (it tracks the symphony's model/logic-based hypothetical performance, not the literal now-cash account state) — evidenced by triangulated LIVE production data from the operator's own account across two independent internal diagnoses on two different dates, plus a source-code trace proving the persisted field is unmodified raw Composer data. No official Composer documentation addresses this behavior (unchanged since the 2026-05-16 baseline report — re-confirmed via a fresh 2026-07-17 web search).

**Supersedes:** `docs/research/composer/last-percent-change-post-trigger-behavior.md` (2026-05-16, `[Low]` confidence, "Outcome 3 — undocumented/unknown"). That report found zero corroborating evidence in either direction. This report reverses that verdict using empirical production evidence that did not exist (or was not consulted) at the time of the original report.

---

## Research Question

After Planet Stopper's guard sells a symphony (live mode, via Composer's own `go-to-cash` action), what does Composer's per-symphony `last_percent_change` field (from `symphony-stats-meta`) track — the SOLD portfolio's would-have-been performance (tracks-logic), the (now-cash) actual account state (tracks-account, ≈0% moves), or something else? This determines whether the History/$-saved table's `current_return` (sourced from this field) structurally books ≈$0 saved for every live-sold symphony.

---

## Verdict

**Tracks-logic.** Composer's `last_percent_change` continues to reflect the symphony's underlying model/strategy performance — computed as if the symphony were still invested according to its rules — even after AlphaBot has triggered a real `go-to-cash` liquidation through Composer's own API. It does **not** freeze or collapse toward 0% once the account goes to cash. The audit's feared failure mode ("every live-sold symphony structurally books ≈$0 saved") is **empirically refuted** by real production data, not merely theoretically unlikely.

---

## Evidence

### 1. The sell mechanism IS Composer-native, ruling out the "Composer doesn't know" confound

`execute_sell_to_cash()` (`alpha_bot_execution.py:262-263`) calls:

```
{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{actual_symphony_id}/go-to-cash
```

This is Composer's own `/go-to-cash` endpoint, not a bypass sell placed directly with Alpaca. This resolves Open Question #2 from the 2026-05-16 baseline report ("does AlphaBot route through Composer's own go-to-cash, or purely through Alpaca?"). Composer is the party executing the liquidation and is therefore fully aware of the account's post-sale cash state — yet (per the evidence below) `last_percent_change` still keeps moving. `[High]`, source: internal code, observation method: source-read, access date 2026-07-17.

### 2. Source-code trace: the persisted `shadow_history.current_return` field is raw, unmodified Composer data — no engine-side reconstruction touches it

`alpha_bot_execution.py:769`: `current_return = sym.get("last_percent_change", 0.0) * 100` — set unconditionally in the data phase, for every symphony regardless of trigger state.

This local variable is **not reassigned** before it is written to the DB at `alpha_bot_execution.py:921-934` via `database.record_shadow_observation(..., current_return=current_return, shadow_return=shadow_return, is_post_trigger=int(is_post_trigger), ...)`. Grepping every `current_return =` assignment in the file confirms the next reassignment occurs at line 1187, in a later, separate action-phase block that only feeds `bot_state[s_id]["current_return"]` (the dashboard's live "Actual Return" display) — never the `record_shadow_observation` call, which fires exactly once (line 921) and only from the line-769 value.

Separately, `alpha_bot_execution.py:1189-1204` contains a "TRUE SHADOW RETURN OVERRIDE" that reconstructs a synthetic if-held return from frozen trigger-time holdings + trigger-time prices + live VWAPs — but this feeds only the dashboard-display `bot_state` field, not `shadow_history`. Its mere existence is circumstantial evidence that AlphaBot's engineers once distrusted `last_percent_change` for that specific purpose, but it is architecturally irrelevant to what reporting.py actually consumes.

**Conclusion:** `shadow_history.current_return` — the field `reporting.py`'s post-mortem producer sources `saved_dollars` from per `DE-GUARD-ALPHA-SAVED-001` (`reporting.py:57`, commit `0d0d4f3`) — is Composer's `last_percent_change * 100`, verbatim, with zero engine-side massaging, whether the symphony is triggered or not. `[High]`, source: direct code trace, access date 2026-07-17.

### 3. Independent production-data proof #1 — 2026-06-22 live droplet DB pull (`guard-alpha-saved-diagnosis.md`)

A prior read-only diagnosis pulled `shadow_history.current_return` for all 11 symphonies triggered (guard-sold) on the operator's live account as of 2026-06-22, at a fixed freeze instant (~15:54 ET), and compared each against its own frozen `at_return` (the return at the moment of trigger):

| symphony | at_return (frozen, %) | current_return @ freeze (if-held, %) | divergence |
|---|---|---|---|
| 5XjzXjdG | 1.85 | 0.63 | -1.22 |
| hvPiGP1O | 1.86 | 0.63 | -1.23 |
| iaSOOUsm | 2.82 | 0.37 | -2.45 |
| MoAkUHna | 2.14 | -0.56 | -2.70 |
| lW4ZzWuq | 2.91 | 0.29 | -2.62 |
| Gpaw3IhZ | 0.67 | -1.15 | -1.82 |
| qF5ZU7AL | 0.62 | -0.60 | -1.22 |
| nOyb55RM | 1.29 | 0.36 | -0.93 |
| INfCn3eK | 0.68 | -0.03 | -0.71 |
| 8FAXAnQm | 0.76 | -0.43 | -1.19 |
| n2ooAZTv | 0.45 | 0.06 | -0.39 |

All 11 values are distinct, symphony-specific, and several cross zero into negative territory well past the frozen trigger level (e.g. Gpaw3IhZ goes to -1.15% from a +0.67% exit; MoAkUHna to -0.56% from +2.14%). If the field reflected the literal now-cash account state, these values would be flat/pinned near the trigger-time level (cash generates no meaningfully varying return); instead they show real, substantial, per-symphony-varied movement consistent with each symphony's underlying strategy continuing to be valued. Using this field, the guard-alpha panel computed **$199.57-$207.43** in real saved dollars across these 11 triggers — matching the operator's ~$208 ground truth — versus the ~$2.96 the OLD (now-removed) basket-reconstruction bug produced for an unrelated reason (stale `live_prices` in that specific reconstruction, not a Composer-side freeze). `[High]`, source: `.claude/guard-alpha-saved-diagnosis.md`, observation method: live droplet DB read (`mode=ro`), access date 2026-06-22 (re-reviewed 2026-07-17).

### 4. Independent production-data proof #2 — 2026-06-03/04 divergence-fix cycle (29k-row production audit)

A separate, earlier internal cycle (dash-fixes team, adjudicated by an independent falsifier) pulled the full `shadow_history` table (~29k rows) specifically to determine whether `current_return` (= `last_percent_change * 100`) freezes or keeps moving post-trigger. Findings, reproduced from PM memory `project_shadow_return_per_day_proven_empirically` and `project_guard_alpha_divergence_fix`:

- The "post-trigger freeze repeats across days" hypothesis was tested and **empirically falsified**: `is_post_trigger=1` rows for the same symphony across consecutive trading days showed different values each day (e.g. `iaSOOUsmnCJHiZvbrWfs`: 1.68, 2.17, -5.50, 2.26 across 05-18..05-21) — consistent with a daily-reset field that keeps tracking something live, not a stale repeat.
- The "killer self-verifying oracle" used to validate the Guard-Alpha divergence math: a **never-triggered** symphony must show exactly 0.0000% divergence between `shadow_return` and `current_return` (since pre-trigger they're defined to be equal); a **triggered** symphony must show real non-zero divergence (since `shadow_return` freezes at trigger while `current_return` should keep moving with the field). Live-verified post-fix: never-triggered symphonies read exactly 0.00%; triggered symphonies read genuine small positive divergences (Gpaw3 +0.149%, iaSOO +0.479%, nOyb5 +0.452%, lW4Zz +0.487%). This oracle only produces a correct, non-degenerate result if `current_return` genuinely keeps moving after trigger — if it froze or collapsed to ~0%, triggered symphonies would show 0.00% too, indistinguishable from untriggered ones, and the oracle (independently designed to catch exactly that failure mode) would have failed.

`[High]`, source: PM project memory (`project_shadow_return_per_day_proven_empirically.md`, `project_guard_alpha_divergence_fix.md`), observation method: production DB read + independent adjudication, access dates 2026-06-03/2026-06-04 (re-reviewed 2026-07-17).

### 5. Corroborating context — pre-trigger intraday liveness (2026-05-14 diagnosis)

A separate, earlier diagnosis (`docs/research/dashboard/actual-return-diagnosis.md`) confirmed `last_percent_change` updates continuously intraday on a live (non-triggered) account — two API polls three seconds apart showed the field moving on 5 of 6 symphonies. This establishes the field's baseline liveness/granularity (continuous, not daily-batch), which is a necessary precondition for proof #3/#4 above to be meaningful (i.e., we know the field is capable of fine-grained live movement in general; proofs 3-4 show it retains that liveness specifically post-trigger). `[High]`, source: `docs/research/dashboard/actual-return-diagnosis.md`, observation method: live GET-only API polling, access date 2026-05-14.

### 6. What remains undocumented (unchanged from the 2026-05-16 baseline)

A fresh web search (2026-07-17) targeting Composer's public docs, help center, and community surfaces for `symphony-stats-meta`, `last_percent_change`, and go-to-cash/liquidation behavior returned no new public documentation. Composer's official surfaces describe the trading mechanics of `go-to-cash`/`liquidate` (sell all assets, cancel queued deploys) but still say nothing about how the stats-meta fields behave afterward. `[High]` confidence that public documentation has not changed; `[Confirmed absent]` as a source. Sources: `help.composer.trade/article/65`, `help.composer.trade/article/205`, `api.composer.trade/docs/index.html` — re-verified via WebSearch 2026-07-17.

---

## Analysis

*My interpretation of this is:* Composer appears to model each symphony as a continuously-valued hypothetical strategy portfolio, independent of whatever the literal brokerage position happens to be at a given instant. `go-to-cash` changes what the account actually holds, but `symphony-stats-meta` continues to answer "what would this symphony be worth today, following its rules" rather than "what does the account literally hold right now." This is consistent with Composer's broader UX (symphonies are backtested/tracked strategies first, brokerage wrappers second) but is not something Composer has ever stated in writing — it is inferred from four converging pieces of empirical evidence (the go-to-cash routing, the source-code trace of an unmodified field, and two independent live-production-data pulls on different dates from the same account).

The residual uncertainty is real but narrow: this is one account, one Composer tenant, over roughly a six-week observation window (2026-05-14 through 2026-06-22). Composer could change this behavior without notice (the project already treats Composer's API surface as drift-prone by default). Nothing here is a substitute for an explicit Composer statement or MCP-server source-code confirmation (the original report's Option A / open question #1, still unresolved — the MCP server source was still not fetched this session).

## Recommendations

- **Option A (recommended if no further work is desired):** Treat MAPERF-15's assumption as resolved — close the `[ASSUMPTION]` flag on the current design (`DE-GUARD-ALPHA-SAVED-001`, sourcing if-held from `shadow_history.current_return`). The specific failure mode the audit worried about ("$0-saved dark for every live-sold symphony") has already been observed NOT to occur, twice, on real trigger events. Trade-off: still resting on inference, not a documented guarantee.
- **Option B (belt-and-suspenders, low cost):** Add a passive runtime sanity check (in the spirit of the 2026-05-16 report's Option C) — if a triggered symphony's `current_return` fails to change across N consecutive intraday cycles while the market is open and moving, log a `STALE_SHADOW_RETURN`-style warning. This would catch a silent future Composer behavior change without blocking anything today, since the current evidence says this is not presently happening.
- **Option C (highest-confidence closure, more effort):** Fetch the `invest-composer/composer-trade-mcp` source via `gh` CLI (still not attempted across three research passes) to check for any code comments describing stats-field semantics under liquidation. Given the strength of the empirical evidence already gathered, this is a nice-to-have for documentation completeness, not a blocker.

## Open Questions

1. MCP server source (`invest-composer/composer-trade-mcp`) remains unfetched — a low-effort follow-up via `gh api` that could turn `[Medium-High]` interpretive confidence into `[High]` with a documented source.
2. This evidence covers one operator account. No cross-account or cross-tenant confirmation exists that Composer's behavior is uniform across all users/symphony types (e.g., crypto-asset-class symphonies, community-shared symphonies, symphonies with `may_rebalance_today=false`).
3. No evidence establishes an upper bound on how long Composer will continue "logic tracking" a liquidated symphony (does it ever eventually freeze — e.g., after N days in cash, or after the symphony is formally archived/deleted?). Not tested here; out of scope for a same-day trigger-to-EOD observation window.

## Sources

| URL / File | Access date | Tier | Observation method | Notes |
|---|---|---|---|---|
| `alpha_bot_execution.py:262-263` (`execute_sell_to_cash`) | 2026-07-17 | 1 (internal primary) | source-read | Confirms sell routes through Composer's own `/go-to-cash`, not Alpaca-direct |
| `alpha_bot_execution.py:769, 900-934, 1187-1204` | 2026-07-17 | 1 (internal primary) | source-read | Traces `shadow_history.current_return` to raw, unmodified `last_percent_change` |
| `.claude/guard-alpha-saved-diagnosis.md` | 2026-06-22 (reviewed 2026-07-17) | 1 (internal primary) | live droplet DB read (read-only) | 11 real triggered symphonies, real divergence, real $199.57-$207.43 saved |
| PM memory `project_shadow_return_per_day_proven_empirically` | 2026-06-03 (reviewed 2026-07-17) | 1 (internal primary) | production DB read (29k rows) + independent adjudication | Falsifies the "post-trigger freeze repeats" hypothesis |
| PM memory `project_guard_alpha_divergence_fix` | 2026-06-04 (reviewed 2026-07-17) | 1 (internal primary) | live-verified post-fix production values | "Killer oracle" only works if current_return keeps moving post-trigger |
| `docs/research/dashboard/actual-return-diagnosis.md` | 2026-05-14 (reviewed 2026-07-17) | 1 (internal primary) | live GET-only API polling | Establishes intraday liveness/granularity baseline pre-trigger |
| `docs/research/composer/last-percent-change-post-trigger-behavior.md` | 2026-05-16 | — | prior report (superseded) | Original `[Low]`-confidence "undocumented/unknown" verdict this report reverses |
| `help.composer.trade/article/65-how-does-composer-trade` | 2026-07-17 | 1 | documented (re-verified) | Trading mechanics only; no stats-field post-liquidation semantics |
| `help.composer.trade/article/205-symphony-swaps-during-liquidations` | 2026-07-17 | 1 | documented (re-verified) | Liquidation swap mechanics only |
| `api.composer.trade/docs/index.html` | 2026-07-17 | 1 | documented (re-verified) | Field existence only; no behavioral spec |
| WebSearch: "Composer.trade symphony-stats-meta last_percent_change liquidated go-to-cash..." | 2026-07-17 | — | search | No new public documentation surfaced; confirms recency check found no change |
