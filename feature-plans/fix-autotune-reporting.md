# Feature: Autotune-Day Reporting Reliability
Status: ready
Created: 2026-07-19

## Summary
The end-of-day Discord report **crashes on every autotune day** (confidence-audit F-015): `reporting.py` iterates the autotune "changes" dict and unconditionally indexes `vals["old"]`, but `autotuner.py` writes an `eval_window_days` entry that is NOT a `{old,new}`-shaped delta, and `KeyError` is not in the surrounding `except` — so the entire EOD push dies with "Execution failed: exit status 1", delivering no summary, no autotune results, no error detail. Separately, silent autotune aborts (empty/short history) return bare `None` from two of three abort paths (F-004), so an abort renders as "No optimization changes" — indistinguishable from a healthy no-op. This cycle makes autotune-day reporting **reliable** (never crashes on any result shape) and **honest** (an abort renders as an explicit aborted state, distinct from a genuine no-change).

## Acceptance Criteria
- [ ] AC-1: When an autotune run's changes dict contains a non-`{old,new}`-shaped entry (e.g. `eval_window_days`), the EOD report/Discord payload builds successfully — no `KeyError`, no crash — and still renders the real `{old,new}` deltas.
- [ ] AC-2: The changes-iteration code handles ANY entry that is not a `{old,new}` delta dict (skip it or render it safely), not just the two currently hard-coded skip-listed keys (`_baseline_chosen`, `_selection_stats`).
- [ ] AC-3: The surrounding `except` around the payload build catches `KeyError` (and the malformed-entry failure class) so a single bad entry degrades gracefully to a still-delivered report rather than crashing the whole push.
- [ ] AC-4: ALL autotune abort paths in `autotuner.py` (currently the bare-`None` returns) return the structured abort marker (`{aborted: True, ...}` shape, matching the one path that already does) — no abort path returns bare `None`.
- [ ] AC-5: The rendered report distinguishes an **aborted** autotune (with the reason) from a **genuine no-change** result — two visibly different messages, never the same "No optimization changes" for both.
- [ ] AC-6 (regression guard): a normal autotune day with real `{old,new}` deltas renders byte-for-byte as before (golden-output unchanged) — the fix adds robustness without altering the happy path.

## Architecture
- `reporting.py` (~:479–540): the changes-iteration loop that does `if vals["old"]` after skip-listing only 2 keys, and the `except (...)` tuple at ~:540 that omits `KeyError`. Guard the value shape before indexing; widen the except.
- `autotuner.py`: `:2855` writes `eval_window_days` as a non-`{old,new}` dict unconditionally (source of the malformed entry); abort returns at `:2367` and `:2378` return bare `None` while `:2364` returns the structured marker (F-004) — make all three structured.
- Test seams: the payload/embed-building function in `reporting.py` (callable with a fixture changes-dict, no live Discord) + the autotuner abort-path functions (callable to assert return shape).

## Edge Cases
- changes entry that is a dict WITHOUT `old`/`new` keys (the `eval_window_days` case).
- changes entry that is a scalar / list / None.
- changes dict containing the `{aborted: True}` marker.
- empty changes dict; `None` changes.
- multiple malformed entries in one dict (must not crash on the first).
- a normal all-`{old,new}` day (happy path — must be unchanged).

## Security Considerations
- No new external input: the changes dict is internally produced by `autotuner.py`; the Discord webhook payload SHAPE is unchanged. No injection surface added.
- Do not leak raw exception strings into the Discord embed (keep error handling to a safe static message if a degrade path renders anything).

## Testing Strategy
- **RED (quant-test-writer, adversarial):**
  1. Reproduce F-015: build the payload from a fixture changes-dict containing an `eval_window_days`-style non-delta entry → assert it returns/completes (no `KeyError` raised, real deltas still present). This test must FAIL on `origin/main`.
  2. F-004: call each autotuner abort path → assert it returns the structured `{aborted: True, ...}` marker, not `None`.
  3. AC-5: render an aborted result vs a no-change result → assert the two messages differ and the aborted one carries the reason.
  4. AC-6 golden: a normal `{old,new}` changes-dict renders identically to the pre-fix happy-path output.
- Fixtures derive shape from the real `autotuner.py` writes (captured/schema-derived, not hand-invented producer values). No live Discord; no live DB; `-n0` only.
- Load `tests/` conventions; DB isolation via `conftest.py` if any DB touched (the abort-path test may need it).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Bundle F-015 (crash) + F-004 (silent abort) in one cycle | Same subsystem (autotune-day reporting honesty); both small, coherent as one hardening pass. |
| Defer F-019 (autotune OOM) to a separate cycle | Different nature — a memory/infra concern (`_AUTOTUNE_REPLAY_N_JOBS`, peak RSS), not a payload-shape bug; deserves its own scoped cycle. |
| Guard-the-shape over enumerate-more-skip-keys | AC-2: robust to ANY future non-delta key, not a brittle growing skip-list — the root cause is unguarded indexing, not this one key. |

## Scope Boundaries
- **IN:** F-015 (crash on non-delta entry), F-004 (structured abort marker from all paths), and the distinct aborted-vs-no-change rendering (AC-5).
- **OUT:** F-019 (autotune OOM — separate cycle); any change to autotune MATH, search space, or trigger/trading behavior; F-008 post-mortem data contamination (separate cycle); F-018 guard-alpha basis (separate cycle); any live-Discord or live-DB write.
