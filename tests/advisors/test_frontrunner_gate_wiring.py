"""RED tests — AC-6 gate-wiring inside advisors/frontrunner_builder.py.

Module under test: advisors.frontrunner_builder._run_build_for_symphony (the
per-symphony orchestration seam). As of f1592a2/24c96c3, this function
detects -> generates -> splices -> queues EVERY generated candidate
unconditionally (see the module's own comment: "Independent re-backtest +
gate + Calmar acceptance are wired in a follow-on cycle ... this seam
intentionally stops short of that for now"). These tests are the RED signal
for that follow-on cycle — AC-6 (independent re-backtest + mandatory
backtest_gate_engine.evaluate_candidate_batch attach point) + AC-7 (Calmar
acceptance, via advisors.frontrunner_acceptance, already GREEN as its own
unit) wired INTO the orchestration loop so only gate-surviving AND
Calmar-accepted candidates are queued.

CONTRACT SOURCES:
  - AC-6: "Incumbent and candidate symphonies are independently
    re-backtested and run through backtest_gate_engine.evaluate_candidate_batch
    (mandatory attach point) as the overfitting guardrail ... the builder's
    search breadth is recorded to the DoF ledger."
  - AC-7: Calmar acceptance (advisors.frontrunner_acceptance.evaluate_calmar_acceptance,
    already implemented/GREEN) gates the final queue decision alongside the
    overfitting-guardrail gate.

PATCH-TARGET STRATEGY: frontrunner_builder.py currently lazy-imports
symphony_logic/database per-call (CC-2 pattern) rather than importing them
at module scope, and does not yet import run_backtest/evaluate_candidate_batch
at all (that's the whole gap these tests pin). Patching
advisors.frontrunner_builder.<name> would therefore silently no-op if
fb-eng's real implementation keeps these as lazy imports or imports them
under a different local name. These tests instead patch the collaborators
at their ORIGIN module (symphony_logic.fetch_symphony_score,
advisors.composer_backtest_client.run_backtest,
advisors.backtest_gate_engine.evaluate_candidate_batch,
database.insert_frontrunner_proposal) — this works regardless of HOW
frontrunner_builder.py imports them (lazy per-call or module-level), since a
lazy `import symphony_logic` inside a function still binds the same shared
module object that patch() mutates in place.

MOCKING STRATEGY (mirrors tests/advisors/test_strategy_builder_engine.py's
established idiom): a REALISTIC BacktestResult (n_days>=65, the
FOLD_TRANSFORM_MIN_TOTAL_DAYS floor) so the REAL evaluate_candidate_batch
(backtest_gate_engine) and the REAL evaluate_calmar_acceptance
(frontrunner_acceptance) run end-to-end — never mock the gate/acceptance
functions themselves except in the one dedicated "reachability" test, which
mocks evaluate_candidate_batch specifically to PROVE it's reached (mirrors
the no-trade-boundary explode-on-call pattern). Only Fable (candidate
generation) and the HTTP-level backtest call are otherwise mocked; the
math/gate engine is exercised for real per the project's
math-engine-never-mocked convention.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import guard — the gate-wiring itself is RED (the module
# imports fine today; what's RED is the BEHAVIOR these tests assert).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Realistic BacktestResult fixture builder — verbatim pattern from
# test_strategy_builder_engine.py's _make_fake_result (n_days>=65 is the
# FOLD_TRANSFORM_MIN_TOTAL_DAYS floor so the REAL gate produces a valid
# purge-respecting fold rather than a thin-window WITHHOLD).
# ---------------------------------------------------------------------------


def _make_fake_result(n_days: int = 100, base_return: float = 0.001):
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)

    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08},
        data_warnings=[],
        daily_returns=returns,
    )


def _make_error_result():
    from advisors.composer_backtest_client import BacktestResult

    return BacktestResult(stats=None, data_warnings=[], daily_returns={}, error="network timeout")


# ---------------------------------------------------------------------------
# Shared fixture: a minimal real incumbent symphony + a mocked Fable overlay,
# built via the REAL symphony_schema/frontrunner_detector, mirroring
# test_frontrunner_builder.py's incumbent_symphony fixture exactly (same
# construction, same detectability guarantee already verified there).
# ---------------------------------------------------------------------------


@pytest.fixture
def incumbent_symphony() -> dict:
    from advisors import symphony_schema

    incumbent_cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])
        ],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root("Incumbent Test Symphony", "daily", [incumbent_cascade])


def _mocked_fable_overlay_client(vix_ticker: str = "UVXY") -> MagicMock:
    """A mock Fable client returning one valid VIX-bearing flat-if overlay
    candidate (same DSL shape as test_frontrunner_builder.py's fixtures)."""
    overlay = {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": 81},
        },
        "then": [
            {"kind": "weight", "scheme": "equal", "children": [{"kind": "asset", "ticker": vix_ticker}]}
        ],
        "else": [
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [{"kind": "asset", "ticker": "CORE_ASSET_0001"}],
            }
        ],
    }
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"overlay": overlay}
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patched_fable(fbld_module):
    """Patch frontrunner_builder's own documented _build_client seam (this
    one IS a real, confirmed module attribute — see test_frontrunner_builder.py)."""
    return patch.object(
        fbld_module, "_build_client", return_value=_mocked_fable_overlay_client()
    )


# ---------------------------------------------------------------------------
# AC-6: independent re-backtest — run_backtest called for BOTH incumbent and
# candidate (never trusting a single/shared backtest for both).
# ---------------------------------------------------------------------------


def test_run_build_for_symphony_backtests_both_incumbent_and_candidate(fbld, incumbent_symphony):
    """The orchestration must call run_backtest at least twice per candidate
    cascade — once for the incumbent tree, once for the spliced candidate
    tree — never reusing one backtest result for both (AC-6: 'independently
    re-backtested')."""
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch(
            "advisors.composer_backtest_client.run_backtest", return_value=fake_result
        ) as mock_backtest,
        patch("database.insert_frontrunner_proposal"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_backtest.call_count >= 2, (
        f"expected at least 2 run_backtest calls (incumbent + candidate), "
        f"got {mock_backtest.call_count} — AC-6 requires independent re-backtest "
        f"of BOTH symphonies"
    )


def test_run_build_for_symphony_calls_the_real_evaluate_candidate_batch(fbld, incumbent_symphony):
    """The REAL backtest_gate_engine.evaluate_candidate_batch must be reached
    (not bypassed) — asserted by patching it to explode and confirming the
    orchestration REACHES that call site.

    The orchestration is D-1 (never-raises): an exception from the gate call
    is caught and degrades to a logged skip, same as any other internal
    failure — it does NOT propagate out of _run_build_for_symphony. So the
    correct proof of reachability is mock_gate.called (the call site was hit),
    not an escaped exception — pytest.raises would be the wrong tool here
    given the module's own documented D-1 contract."""
    fake_result = _make_fake_result()

    def _explode(*_a, **_k):
        raise AssertionError("evaluate_candidate_batch was reached")

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch", side_effect=_explode
        ) as mock_gate,
    ):
        # Must not raise all the way out (D-1) — the orchestration's own
        # try/except is expected to catch the injected explosion.
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_gate.called, (
        "evaluate_candidate_batch was never called — the gate/wiring is "
        "bypassing the mandatory overfitting-guardrail attach point (AC-6)"
    )


# ---------------------------------------------------------------------------
# AC-6/7: only gate-surviving AND Calmar-accepted candidates are queued —
# never every generated candidate unconditionally.
# ---------------------------------------------------------------------------


def test_a_backtest_failure_on_the_candidate_never_queues_a_proposal(fbld, incumbent_symphony):
    """If the candidate's independent re-backtest fails (network/API error),
    the orchestration must NOT queue a proposal for approval — a failed
    backtest can't be gated or Calmar-scored, so it must be skipped, not
    silently admitted."""
    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        # Incumbent backtest succeeds (first call); candidate backtest (and
        # any subsequent retry) fails.
        return _make_fake_result() if call_count["n"] == 1 else _make_error_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert,
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    mock_insert.assert_not_called()


def test_a_calmar_rejected_candidate_is_not_queued(fbld, incumbent_symphony):
    """A candidate that survives the overfitting gate but is REJECTED by
    Calmar acceptance (worse Calmar, no simplification) must not be queued —
    AC-7 gates the final admission alongside AC-6's overfitting guardrail."""
    # Incumbent gets a strong positive return series; candidate gets a
    # WORSE (near-zero/negative-trending) series so its CAGR is clearly
    # lower at a comparable or worse drawdown -> Calmar rejects.
    incumbent_result = _make_fake_result(n_days=100, base_return=0.002)
    candidate_result = _make_fake_result(n_days=100, base_return=-0.0015)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert,
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    mock_insert.assert_not_called()


def test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics(fbld, incumbent_symphony):
    """The positive path: a candidate that survives the overfitting gate AND
    improves Calmar must be queued via database.insert_frontrunner_proposal,
    and the persisted metrics_json must carry the incumbent-vs-candidate
    Calmar/CAGR/MDD/node-count deltas (AC-8 dashboard need)."""
    incumbent_result = _make_fake_result(n_days=100, base_return=0.0005)
    candidate_result = _make_fake_result(n_days=100, base_return=0.003)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert,
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_insert.called, (
        "a candidate with a materially better return series (higher CAGR, "
        "comparable drawdown) was not queued — the gate/Calmar wiring is "
        "over-rejecting or not calling insert_frontrunner_proposal at all"
    )
    _, call_kwargs = mock_insert.call_args
    metrics = call_kwargs.get("metrics_json")
    assert metrics is not None and metrics != {}, (
        "insert_frontrunner_proposal was called without metrics_json — AC-8 "
        "requires the Calmar/CAGR/MDD/node-count deltas to be persisted for "
        "the Advisor-tab card"
    )


# ---------------------------------------------------------------------------
# AC-6: search breadth recorded to the DoF ledger.
# ---------------------------------------------------------------------------


def test_search_breadth_is_recorded_to_the_dof_ledger():
    """AC-6: 'the builder's search breadth is recorded to the DoF ledger.'
    This is a structural presence check (the orchestration references
    whatever the real DoF-ledger write call is) — mirrors the pattern used
    for verify_undeployed in test_frontrunner_no_trade_boundary.py (a floor
    check, not a full behavioral proof). The exact ledger call site is
    resolved from whatever backtest_gate_engine/autotuner/database exposes
    for this purpose — this test greps the module source for a DoF-ledger-
    shaped reference rather than assuming a specific function name, since
    the concrete API wasn't nailed down in the plan."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "advisors"
        / "frontrunner_builder.py"
    ).read_text(encoding="utf-8")
    assert "dof" in source.lower() or "degrees_of_freedom" in source.lower(), (
        "frontrunner_builder.py has no reference to a DoF ledger anywhere — "
        "AC-6 requires the builder's search breadth to be recorded there"
    )


# ---------------------------------------------------------------------------
# Never raises — a gate/acceptance-layer exception must not crash the batch.
# ---------------------------------------------------------------------------


def test_a_gate_engine_exception_does_not_crash_the_whole_symphony_batch(fbld, incumbent_symphony):
    """D-1: if evaluate_candidate_batch (or the Calmar acceptance call) raises
    unexpectedly, the orchestration must degrade to a skip for this
    candidate/symphony, never propagate — run_frontrunner_build's per-symphony
    try/except already covers the OUTER loop; this confirms an inner-layer
    surprise doesn't defeat that outer safety net."""
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch(
            "advisors.backtest_gate_engine.evaluate_candidate_batch",
            side_effect=RuntimeError("unexpected gate failure"),
        ),
    ):
        try:
            fbld.run_frontrunner_build(symphony_ids=["test-symphony-id"])
        except RuntimeError:
            pytest.fail(
                "a gate-engine RuntimeError propagated all the way out of "
                "run_frontrunner_build — D-1 requires this to degrade to a "
                "logged skip, never crash the batch"
            )
