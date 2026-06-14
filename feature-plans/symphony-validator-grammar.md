# Feature Plan — symphony_schema validator/lint grammar alignment (Cycle A)

**Status:** ready
**Branch:** `pr/symphony-validator-grammar` (worktree `.claude/pr-worktrees/symphony-validator`, forked from origin/main `7765e6d`)
**Classification:** config-change (frozenset allowlist additions) + regression tests. NOT a new codepath — no new functions/branches. Per project CLAUDE.md "config changes" exception, the full Toxic-Pair team is not mandated; executed as gated solo isolated-worktree agents (RED → GREEN → review), PM-gated.

## Summary
`advisors/symphony_schema.py`'s `validate_tree` / `lint_tree` were pinned to a vocabulary from 2 local fixtures (v1 grammar doc). The corpus-validated v2 grammar (`feature-plans/strategy-builder-composer-grammar-v2.md`, merged #15, mined from 10,441 real Composer symphonies) proves three allowlists are too narrow, causing **`validate_tree` to HARD-ERROR on real community symphonies** (a correctness bug that blocks consuming `captplanet.strategies`). This cycle widens the three allowlists to the corpus-verified real grammar. **Scope is the validator/lint allowlists ONLY** — it does NOT change what the constructors (`make_*`) emit, and does NOT add compound ANY/ALL construction (that is Cycle B).

## Acceptance Criteria
- **AC-1 (comparator `gte`):** `validate_tree` returns NO hard error for an `if-child` whose `comparator` is `"gte"`. Provenance: v2 grammar §8, VERIFIED-CORPUS n=39,596. (Enum becomes `{gt, lt, gte, lte}`; `eq`/`neq` remain excluded — 0 corpus occurrences.)
- **AC-2 (rebalance `quarterly`/`yearly`):** `validate_tree` returns NO hard error for a `root` with `rebalance:"quarterly"` and for one with `rebalance:"yearly"`. Provenance: v2 §6, VERIFIED-CORPUS (quarterly n=58, yearly n=27). (Enum becomes `{daily, none, weekly, monthly, quarterly, yearly}`.)
- **AC-3 (indicator-fn lint recognition):** `lint_tree` emits NO "unknown indicator fn" warning for nodes using `exponential-moving-average-price`, `standard-deviation-price`, `percentage-price-oscillator`, `percentage-price-oscillator-signal`, `upper-bollinger`, `lower-bollinger`. Provenance: v2 §4/§4b, all VERIFIED-CORPUS (EMA-price n=45,816; std-dev-price n=5,572; PPO/PPO-signal; bollinger). These were lint-only before (not hard errors) — this removes false lint noise on real grammar.
- **AC-4 (no regression):** All previously-known tokens behave exactly as before; the full existing `symphony_schema` test suite stays green. The three constants remain `frozenset[str]`. Constructors are UNCHANGED (this cycle does not alter emitted trees).
- **AC-5 (source-comment provenance):** The inline source comments above the three constants are updated to cite the v2 corpus evidence (replacing the stale "OQ-2 unconfirmed / hard error until observed" / "VERIFIED-COMMUNITY swagger" notes). Names/counts only — no incident text.

## Architecture
Three one-line frozenset literal edits in `advisors/symphony_schema.py` (`KNOWN_COMPARATORS` ~L80, `KNOWN_REBALANCE` ~L84, `KNOWN_INDICATOR_FNS` ~L65) + comment updates. No logic changes; `validate_tree`/`lint_tree` already iterate these sets.

## Edge Cases
- `gte`/`lte` casing and exact spelling must match corpus tokens exactly (lowercase, no symbol form like `">="`). The `builder_backtests` collection's symbol comparators (`">"`) are a DIFFERENT schema and are explicitly OUT of scope (v2 §10 #10).
- Adding to `KNOWN_INDICATOR_FNS` must not accidentally suppress the existing lint for genuinely-unknown fns (e.g. the abbreviation `"rsi"` must still warn).
- PPO indicators carry `short-window`/`long-window`/`smooth-window` params; this cycle only recognizes the fn names for lint — it does NOT add param-key validation (out of scope; Cycle B/later).

## Security Considerations
None new. Pure read-only validator/lint vocabulary; off-execution-path; no I/O, no secrets, no LIVE_EXECUTION.

## Testing Strategy
RED tests (quant-test-writer) constructed from the v2 grammar doc (the captured-from-corpus provenance) — minimal trees carrying each real token, asserting `validate_tree` returns no hard error (AC-1/2) and `lint_tree` emits no unknown-fn warning (AC-3), plus a regression test that an unknown comparator/rebalance/fn (e.g. `"eq"`, `"hourly"`, `"rsi"`) STILL errors/warns (guards AC-4 — proves the widening is exact, not a blanket allow). Then GREEN = add the tokens. Full `symphony_schema` suite + the `-n0` ai_advisor gate before merge.

## Scope Boundaries
- IN: widen `KNOWN_COMPARATORS`, `KNOWN_REBALANCE`, `KNOWN_INDICATOR_FNS` to corpus-verified tokens + comment provenance + regression tests.
- OUT: compound ANY/ALL construction (GAP-1/GAP-2 → Cycle B); `binary-compound` ticker-broadcast; market-cap weighting (GAP-6); PPO/Bollinger param-key validation (GAP-7); any change to constructor emission; any consumer/ingester of community strategies.
