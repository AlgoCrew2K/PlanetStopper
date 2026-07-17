"""RED tests -- Math Remediation R0, AC-1 + AC-2 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md MA-3 (+M2) -- the advisor-side overfitting-cull PBO
veto is computed on PERCENT-scale returns against math_engine.compute_pbo's DECIMAL
contract (math_engine.py:1939-1941). advisors/backtest_gate_engine.py:658-661 builds
_dated_configs as a raw pass-through of BacktestCandidate.dated_returns; every real
producer writes that field in percent scale (r * 100.0):
  strategy_builder_engine.py:997, asset_swap_engine.py:954,
  logic_change_engine.py:677, frontrunner_builder.py:1605.
A single -2% day scores U=-6.908 (wealth-floor saturation) instead of the correct
-0.0202 -- the veto verdict is unit-corrupted (VERDICT.md: 40/60 and 7/20 seeded
flips; PROBE-4: decimal PBO=0.8714 (veto) vs percent PBO=0.1714 (pass) on identical
data).

AC-1: advisors/backtest_gate_engine.py converts dated_returns percent->decimal at
its boundary (the _dated_configs build site) before calling math_engine.compute_pbo.

AC-2: _BATCH_PBO_GAMMA (currently 1.0, citing a nonexistent "autotuner.py: GAMMA =
1.0") is aligned to the frozen THEORY gamma -- database.py:1913
PHASE1_THEORY_GAMMA = "2.0" -- mirroring autotuner.py:1592's float() cast pattern.

NO HARDCODED PBO LITERALS: every PBO value asserted in this file is produced by a
REAL, un-mocked call to math_engine.compute_pbo on the golden fixture -- never a
number copied from the audit doc. compute_pbo is NEVER mocked; only observed
(monkeypatch spy) or called directly. The one relationship this file assumes as
ground truth is a threshold CROSSING derived from the fixture at test time, not a
literal PBO value.

Golden fixture: tests/fixtures/math/pbo_unit_boundary_flip.json -- a materialized
(not re-derived) reproduction of the audit's PROBE-4 construction
(ma-stats-findings.md / probes_stats.py, seed=7, 64 dates, 5 configs), decimal-scale.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from math_engine import PBO_REJECT_THRESHOLD, compute_pbo

PROJECT_ROOT = Path(__file__).parent.parent.parent
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "math" / "pbo_unit_boundary_flip.json"


@pytest.fixture(scope="module")
def gate_engine():
    import advisors.backtest_gate_engine as _g  # noqa: PLC0415

    return _g


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def configs_decimal(fixture_data) -> list[dict[str, float]]:
    """The golden fixture's per-date returns, DECIMAL scale (ground truth)."""
    return [dict(cfg) for cfg in fixture_data["configs_decimal"]]


@pytest.fixture(scope="module")
def configs_percent(configs_decimal) -> list[dict[str, float]]:
    """The SAME data at PERCENT scale -- exactly the real producers' convention
    (r * 100.0). This is what a real BacktestCandidate.dated_returns dict looks
    like in production today."""
    return [{d: r * 100.0 for d, r in cfg.items()} for cfg in configs_decimal]


@pytest.fixture(scope="module")
def date_keys(fixture_data) -> list[str]:
    return list(fixture_data["date_keys"])


def _make_candidate(gate_engine, cid: str, dated_returns_percent: dict[str, float]):
    """Build a BacktestCandidate exactly as production does: dated_returns carries
    PERCENT-scale values (the real producer convention), and daily_returns_pct is
    the chronologically-ordered value list of the SAME percent-scale data (so the
    existing fold-transform machinery, unaffected by AC-1, keeps working)."""
    BacktestCandidate = gate_engine.BacktestCandidate
    ordered_dates = sorted(dated_returns_percent)
    value_list = [dated_returns_percent[d] for d in ordered_dates]
    return BacktestCandidate(
        candidate_id=cid,
        daily_returns_pct=value_list,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
        nn1_compliant=True,
        purge_integrity_ok=True,
        dated_returns=dict(dated_returns_percent),
    )


# ===========================================================================
# Self-guards (anti-hollow): prove the fixture reproduces the audit's flip
# mechanism via the REAL compute_pbo -- never a hardcoded PBO literal.
# ===========================================================================


def test_fixture_decimal_scale_exceeds_pbo_reject_threshold_via_real_compute_pbo(
    configs_decimal, date_keys
):
    """Ground truth: on the CORRECT (decimal) scale, this fixture's PBO is a
    genuine overfitting veto (pbo > PBO_REJECT_THRESHOLD) -- computed for real,
    not asserted from a stored number."""
    pbo = compute_pbo(configs_decimal, date_keys, 1.0)
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"golden fixture must veto at decimal scale via real compute_pbo; got {pbo}"
    )


def test_fixture_percent_scale_does_not_exceed_threshold_via_real_compute_pbo(
    configs_percent, date_keys
):
    """The SAME data at percent scale (today's un-converted gate-engine input)
    does NOT cross the veto threshold -- this is the flip the audit found, proven
    fresh via the real compute_pbo (not copied from the audit doc)."""
    pbo = compute_pbo(configs_percent, date_keys, 1.0)
    assert pbo <= PBO_REJECT_THRESHOLD, (
        f"percent-scale (unconverted) PBO must NOT veto -- this is the bug the "
        f"fixture reproduces; got {pbo}"
    )


def test_fixture_flip_is_gamma_robust_at_the_frozen_theory_gamma(configs_decimal, date_keys):
    """The decimal-scale veto also fires at gamma=2.0 (the frozen THEORY gamma,
    AC-2's target) -- so AC-1's fix is not accidentally masked by AC-2's fix."""
    pbo = compute_pbo(configs_decimal, date_keys, 2.0)
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"golden fixture must veto at decimal scale, gamma=2.0 (THEORY gamma); got {pbo}"
    )


# ===========================================================================
# AC-1 -- the gate-engine boundary must convert dated_returns percent->decimal
# before calling math_engine.compute_pbo.
# ===========================================================================


def test_ac1_gate_boundary_calls_compute_pbo_with_decimal_scale_configs(
    gate_engine, monkeypatch, configs_percent, configs_decimal, date_keys
):
    """WIRING + UNIT proof: evaluate_candidate_batch is fed PERCENT-scale
    dated_returns (the real production shape). We spy math_engine.compute_pbo
    (the real math still runs -- result not replaced) and assert the
    configs_date_returns it receives match the fixture's DECIMAL values -- proving
    the boundary converts, not a hardcoded expectation.

    MUST FAIL at 98901abf: today _dated_configs is `dict(c.dated_returns)`, a raw
    pass-through, so compute_pbo receives the PERCENT values unchanged (100x too
    large for every date)."""
    import math_engine as _me

    calls: list[tuple] = []
    real_compute_pbo = _me.compute_pbo

    def _spy(configs_date_returns, eligible_dates, gamma, S=None):
        calls.append((configs_date_returns, eligible_dates, gamma))
        return real_compute_pbo(configs_date_returns, eligible_dates, gamma, S)

    # evaluate_candidate_batch does `import math_engine as _math_engine` LOCALLY
    # (a fresh bind to the same sys.modules singleton each call) -- patching the
    # compute_pbo attribute on the shared module object is what the local import
    # sees too, so no separate patch of the gate_engine module is needed.
    monkeypatch.setattr(_me, "compute_pbo", _spy)

    candidates = [
        _make_candidate(gate_engine, f"c{i}", cfg) for i, cfg in enumerate(configs_percent)
    ]
    gate_engine.evaluate_candidate_batch(candidates)

    assert calls, "evaluate_candidate_batch must call compute_pbo for a >=2 date-keyed batch"
    configs_arg, dates_arg, _gamma_arg = calls[0]

    # Every date in every received config must match the fixture's DECIMAL value
    # (within float tolerance) -- NOT the percent value (100x too large).
    mismatches = []
    for cfg_idx, received_cfg in enumerate(configs_arg):
        expected_cfg = configs_decimal[cfg_idx]
        for d in dates_arg:
            if d not in received_cfg or d not in expected_cfg:
                continue
            received = received_cfg[d]
            expected_decimal = expected_cfg[d]
            expected_percent = expected_decimal * 100.0
            # If the received value is much closer to the PERCENT value than the
            # DECIMAL value, the boundary conversion is missing (today's bug).
            if abs(received - expected_percent) < abs(received - expected_decimal):
                mismatches.append((cfg_idx, d, received, expected_decimal, expected_percent))

    assert not mismatches, (
        "compute_pbo received PERCENT-scale values (no boundary conversion) -- "
        f"AC-1 requires the gate engine to divide by 100 before calling compute_pbo. "
        f"First mismatch (config_idx, date, received, expected_decimal, expected_percent): "
        f"{mismatches[0] if mismatches else None}"
    )


def test_ac1_high_pbo_percent_scale_batch_still_vetoes_post_fix(
    gate_engine, monkeypatch, configs_percent
):
    """BEHAVIORAL proof: the golden fixture, fed as PERCENT-scale dated_returns
    (the real production shape) through the REAL evaluate_candidate_batch, must
    thread a pbo > PBO_REJECT_THRESHOLD into evaluate_acceptance_gate -- i.e. the
    veto FIRES on real overfitting despite the percent-scale input, because the
    boundary converts to decimal first.

    MUST FAIL at 98901abf: today the unconverted percent-scale PBO is
    approximately 0.17 (<= threshold), so the veto does not fire."""
    import acceptance_gate as _ag

    seen_pbos: list = []
    real_gate = _ag.evaluate_acceptance_gate

    def _spy(*args, **kwargs):
        seen_pbos.append(kwargs.get("pbo"))
        return real_gate(*args, **kwargs)

    monkeypatch.setattr(gate_engine, "evaluate_acceptance_gate", _spy, raising=False)
    monkeypatch.setattr(_ag, "evaluate_acceptance_gate", _spy)

    candidates = [
        _make_candidate(gate_engine, f"pv{i}", cfg) for i, cfg in enumerate(configs_percent)
    ]
    gate_engine.evaluate_candidate_batch(candidates)

    non_none = [p for p in seen_pbos if p is not None]
    assert non_none, "the gate must receive a non-None pbo for this >=2 date-keyed batch"
    assert all(p > PBO_REJECT_THRESHOLD for p in non_none), (
        f"a genuinely overfit batch (percent-scale input) must thread pbo > "
        f"{PBO_REJECT_THRESHOLD} into the gate post-fix; got {non_none}"
    )


# ===========================================================================
# AC-2 -- _BATCH_PBO_GAMMA aligned to the frozen THEORY gamma (2.0), and the
# false "autotuner.py: GAMMA = 1.0" citation is removed.
# ===========================================================================


def test_ac2_batch_pbo_gamma_matches_frozen_theory_gamma(gate_engine):
    """_BATCH_PBO_GAMMA must equal database.PHASE1_THEORY_GAMMA (the frozen THEORY
    gamma, "2.0" as a str -- database.py:1913), not the current 1.0.

    MUST FAIL at 98901abf: _BATCH_PBO_GAMMA == 1.0."""
    import database

    expected = float(database.PHASE1_THEORY_GAMMA)
    assert expected == gate_engine._BATCH_PBO_GAMMA, (
        f"_BATCH_PBO_GAMMA must mirror the frozen THEORY gamma "
        f"(database.PHASE1_THEORY_GAMMA={database.PHASE1_THEORY_GAMMA!r} -> {expected}); "
        f"got {gate_engine._BATCH_PBO_GAMMA}"
    )


def test_ac2_false_autotuner_gamma_citation_is_removed_from_source():
    """The comment above _BATCH_PBO_GAMMA cites a nonexistent constant
    ("autotuner.py: GAMMA = 1.0") -- VERDICT.md M2, lead-grep-verified absent.
    Source-level pin: the false citation string must not appear near the constant.

    MUST FAIL at 98901abf: the comment at backtest_gate_engine.py:169 reads
    'Mirrors the autotuner's gamma (autotuner.py: GAMMA = 1.0...)'."""
    src_path = PROJECT_ROOT / "advisors" / "backtest_gate_engine.py"
    content = src_path.read_text(encoding="utf-8")
    # Locate the _BATCH_PBO_GAMMA declaration and inspect the preceding comment block.
    match = re.search(r"_BATCH_PBO_GAMMA\s*:\s*float\s*=", content)
    assert match is not None, "_BATCH_PBO_GAMMA declaration not found in backtest_gate_engine.py"
    preceding = content[max(0, match.start() - 600) : match.start()]
    assert "GAMMA = 1.0" not in preceding, (
        "the comment above _BATCH_PBO_GAMMA still cites the nonexistent "
        "'autotuner.py: GAMMA = 1.0' constant (VERDICT.md M2) -- update the "
        "citation to reference database.PHASE1_THEORY_GAMMA instead."
    )


def test_ac2_gate_threads_theory_gamma_into_compute_pbo(gate_engine, monkeypatch, configs_percent):
    """WIRING proof: the gamma actually passed to compute_pbo at the batch call
    site is the frozen THEORY gamma (2.0), not 1.0 -- observed via spy, real math
    still runs.

    MUST FAIL at 98901abf: the gate calls compute_pbo with gamma=1.0."""
    import math_engine as _me

    gammas_seen: list[float] = []
    real_compute_pbo = _me.compute_pbo

    def _spy(configs_date_returns, eligible_dates, gamma, S=None):
        gammas_seen.append(gamma)
        return real_compute_pbo(configs_date_returns, eligible_dates, gamma, S)

    monkeypatch.setattr(_me, "compute_pbo", _spy)

    candidates = [
        _make_candidate(gate_engine, f"g{i}", cfg) for i, cfg in enumerate(configs_percent)
    ]
    gate_engine.evaluate_candidate_batch(candidates)

    assert gammas_seen, "compute_pbo must have been called for this >=2 date-keyed batch"
    assert all(g == pytest.approx(2.0) for g in gammas_seen), (
        f"AC-2: the gate must call compute_pbo with the frozen THEORY gamma (2.0); "
        f"got {gammas_seen}"
    )
