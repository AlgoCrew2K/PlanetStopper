## Gate Fix — 9 test failures fixed on fix/test-memory-blowup

Commit SHA: 2037865

### Changes

**tests/fixtures/math/bhy_byte_identical_pin.json** (Group A, 2 failures fixed)

Restored `yekutieli_c_n_pins.values.c_500` from `6.79282342999052` to
`6.792823429990524`. The strategy-builder merge overwrote this pin with a
different summation's last-bit result. The autotuner `_yekutieli_c_n` code was
not changed by the merge — only the fixture was corrupted. The canonical value
is `sum(1.0/j for j in range(1, 501))` computed with ascending IEEE-754
left-to-right summation. The improved provenance note text added by the merge
was retained.

**tests/ai_advisor/test_cycle1_multilens_foundation.py** (Group B, 7 failures fixed)

Added `CYCLE1_STUB_LENSES = ["technicals", "derivatives"]` constant and
narrowed 4 stub-contract parametrized tests from `CYCLE1_LENSES` to
`CYCLE1_STUB_LENSES`:

- `test_cycle1_stub_lens_returns_available_false`
- `test_cycle1_stub_lens_reason_is_non_empty_and_names_source`
- `test_stub_lens_emits_no_payload_when_available_false`
- `test_stub_lens_called_with_non_none_arg_still_returns_available_false`

Sentiment (GDELT), macro (FRED), and fundamentals (SEC EDGAR) were promoted to
real producers in commit 960d544 (Cycle 2). These lenses return `available=True`
when their data source is reachable, which correctly fails the Cycle-1
`available=False` stub assertions. Real producer coverage is in
`tests/ai_advisor/test_cycle2_lens_producers.py`. Fundamentals was not failing
(returns `available=False` when called without a ticker) but is excluded from
stub-contract tests for forward-correctness.

Tests iterating all 5 lenses for shape/contract checks (helper exists,
required keys, lens-name match, env-credentials, sources-empty-when-unavailable
with self-guard) are unchanged at `CYCLE1_LENSES`.

### GREEN counts (all -n0)

| Target file(s) | Result |
|---|---|
| test_perf006_harmonic_sum.py + test_m1_bhy_haircut_preservation.py | 24 passed |
| test_cycle1_multilens_foundation.py (full file) | 59 passed, 2 skipped |
| test_cycle2_lens_producers.py | 39 passed, 3 deselected (live-marked) |
| test_ai_advisor.py | 35 passed |

The 2 skips in cycle1 are `test_lens_block_available_false_sources_must_be_empty_or_absent`
for sentiment and macro — these are self-guarding (`pytest.skip` when `available != False`)
and correctly skip when GDELT/FRED return live data.

### Not done by this fix

Full-tree gate run is PM-owned. This commit fixes only the 9 targeted failures
on branch fix/test-memory-blowup. No merge to main.
