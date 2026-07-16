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


@pytest.fixture(autouse=True)
def _no_live_atlas_calls():
    """AC-3 landed (1b3e9fb) mid-cycle: _run_build_for_symphony now calls
    _gather_atlas_frontrunner_patterns once per cascade, which calls the REAL
    community_strats.load_community_strategies unless patched — that in turn
    can attempt a genuine MongoClient connection (serverSelectionTimeoutMS,
    atlas_cache.cached_pull's fetch_fn) when no fresh cache row exists.
    DISCOVERED THE HARD WAY: a test with 40+ synthetic cascades
    (test_fable_calls_per_symphony_are_bounded_by_a_budget_cap) without this
    guard took minutes and multiple hundred MB of growing memory — up to 40
    real, slow, unmocked network attempts, once per cascade. Autouse so every
    test in this file (present and future) is protected without needing to
    remember the patch per-test; scoped to THIS file only, not conftest.py
    (test_frontrunner_atlas_patterns.py has its own explicit, deliberate
    community_strats mocking per test and must not be affected by this).

    EXTENDED 2026-07-16 (fr-engine's Cluster D wiring, 95dac72c): the SAME
    exposure class now exists for a SECOND Atlas collection —
    _run_build_for_symphony's new unconditional
    frontrunner_signals.load_frontrunner_signals() call, which routes through
    atlas_cache.cached_pull with a DIFFERENT collection name
    (captplanet.frontrunners.fr_checks vs community_strats' captplanet.strategies)
    but the identical live-Mongo-on-cache-miss risk. atlas_cache.py itself has
    NO pytest sentinel (unlike database._db_file/lens_warehouse._warehouse_db_file,
    both of which raise under pytest) — this file's 10 _run_build_for_symphony
    call sites were silently exposed the moment the wiring landed, protected
    only by an incidentally-warm local cache on this machine, not by any test
    guard. Patched here at the same seam-of-origin the mongo-guard/no-trade-
    boundary idiom this project already uses.
    """
    with (
        patch(
            "advisors.community_strats.load_community_strategies",
            return_value={"available": False, "candidates": [], "stats": {}, "source": "captplanet"},
        ),
        patch(
            "advisors.frontrunner_signals.load_frontrunner_signals",
            return_value={
                "available": False,
                "reason": "TestGuardNoLiveAtlas",
                "signals": [],
                "stats": {},
                "source": "captplanet",
            },
        ),
    ):
        yield


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


def _make_shaped_result(shape_pct: list, n_days: int = 100):
    """Build a BacktestResult from an explicit repeating daily-return shape.

    Unlike ``_make_fake_result`` (a single cyclical multiplier around one
    base_return — degenerate for gate-significance testing, see the
    docstring on ``test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics``
    for why), this lets a test hand-place both up AND down days so the
    series' Sortino ratio is genuinely computable (a real strategy's return
    series is never all-positive or all-negative).

    ``shape_pct`` values are PERCENT (e.g. 0.60 = +0.60%/day); tiled to
    ``n_days`` via ``shape_pct[i % len(shape_pct)]``.  Converted to FRACTION
    form for the BacktestResult (mirrors _make_fake_result's own contract —
    _gate_and_accept_candidate multiplies daily_returns by 100.0 to recover
    percent).
    """
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = shape_pct[i % len(shape_pct)] / 100.0
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
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
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
            {
                "kind": "weight",
                "scheme": "equal",
                "children": [{"kind": "asset", "ticker": vix_ticker}],
            }
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
    return patch.object(fbld_module, "_build_client", return_value=_mocked_fable_overlay_client())


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


def test_a_weak_candidate_that_clears_the_fdr_veto_is_still_rejected_on_oos_alpha(
    fbld, incumbent_symphony
):
    """ADVERSARIAL SAFETY NET (team-lead directive, 2026-07-11): guards
    against frimpl's upcoming panel-score-construction fix (the fix for
    test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics's
    documented root cause) accidentally making the gate too permissive — a
    fix that makes GOOD candidates adoptable must NOT also make BAD
    candidates adoptable. This test exercises the SAME depth of the accept
    path as the positive-path test (a candidate that clears Stage-1 vetoes,
    i.e. reaches the Stage-2 discretionary-panel decision point) but with a
    candidate that is genuinely too weak to beat the incumbent's baseline —
    it must remain REJECTED regardless of how the panel-score construction
    is resolved, because the oos_alpha-superiority check
    (acceptance_gate.py:257, `oos_alpha <= fallback_oos_alpha or
    oos_alpha <= default_oos_alpha`) is evaluated BEFORE the panel
    comparison and is untouched by that fix.

    Fixture design: a clean, low-relative-variance, but MODEST-magnitude
    candidate (mostly +0.10%/day with small -0.02%/day dips) — significant
    enough to clear BHY/FDR (probe-verified vetoes_passed=True,
    winner_p_adj≈0.017 < HARVEY_LIU_FDR_Q=0.05) but too small in absolute
    terms to beat the incumbent's full-100-day baseline from just its
    20-day validation fold (probe-verified oos_alpha=1.52 vs incumbent
    full-period=1.7 → KEEP_INCUMBENT). Directly probed BOTH against the
    current (pre-fix) empty-params construction AND a simulated post-fix
    tied-panel construction (identical non-empty candidate_params/
    incumbent_params/theory_prior_params, panel_score=1.0 for both sides)
    — decision is KEEP_INCUMBENT in BOTH cases, confirming this reject is
    genuinely panel-independent, not an artifact of the current bug.
    """
    incumbent_shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    candidate_shape_pct = [0.10, 0.10, 0.10, 0.10, -0.02, 0.10, 0.10, 0.10, 0.10, -0.02]

    incumbent_result = _make_shaped_result(incumbent_shape_pct, n_days=100)
    candidate_result = _make_shaped_result(candidate_shape_pct, n_days=100)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert,
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    mock_insert.assert_not_called()


def test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics(fbld, incumbent_symphony):
    """The positive path: a candidate that survives the overfitting gate AND
    improves Calmar must be queued via database.insert_frontrunner_proposal,
    and the persisted metrics_json must carry the incumbent-vs-candidate
    Calmar/CAGR/MDD/node-count deltas (AC-8 dashboard need).

    FIXTURE DESIGN NOTE (why _make_fake_result cannot exercise this path):
    _make_fake_result's single cyclical multiplier around one base_return
    (`base_return * (1 + (i % 5) * 0.1 - 0.2)`) is ALL-POSITIVE whenever
    base_return > 0 (the multiplier only ranges 0.8-1.2). Verified directly
    (advisors.backtest_gate_engine.evaluate_candidate_batch probe, both
    series all-positive): autotuner.compute_sortino_ratio hits the +1e6
    sentinel (zero downside deviation) on the RAW series, and EVERY
    bootstrap resample (autotuner.compute_sortino_se_bootstrap) draws from
    the same all-positive population, so every resampled Sortino also hits
    the sentinel and gets filtered — `len(sortinos) < _BOOTSTRAP_MIN_VALID_RESAMPLES`
    (0 < 100) makes SE None, which makes compute_sortino_tstat fall back to
    0.0 (autotuner.py:904), giving p=0.5 for BOTH candidates — neither ever
    clears the BHY/Yekutieli FDR gate (HARVEY_LIU_FDR_Q=0.05), regardless of
    how large the return gap is. `_make_shaped_result` fixes this by hand-
    placing genuine down days into BOTH series.

    ROOT-CAUSE FINDING #2 (SEPARATE from the fixture-realism issue above;
    confirmed via a direct evaluate_candidate_batch probe against the
    CURRENT candidate-only-batch code — commit f51cffe fixed a real n=2
    BHY-competition bug where the incumbent competed with the candidate for
    the single winner slot, but did not touch this one): even with THIS
    fixture, which clears BOTH the BHY/FDR gate cleanly (probe-verified
    winner_p_adj ≈ 0.013, well under HARVEY_LIU_FDR_Q=0.05, vetoes_passed=True)
    and the OOS-alpha-beats-both-baselines check, the STAGE-2 discretionary
    panel in acceptance_gate.evaluate_acceptance_gate still structurally
    blocks ADOPT_CANDIDATE, because _gate_and_accept_candidate's sole batch
    member (advisors/frontrunner_builder.py:986-994,
    ``BacktestCandidate(candidate_id="candidate", ...)``) always passes
    candidate_params={}, incumbent_params={}, theory_prior_params={} (there
    is no Optuna-style parameter vector for a tree-splice candidate). With
    all three empty:
      - acceptance_gate._compute_parameter_stability_score(candidate_params={},
        incumbent_params={}) returns the neutral prior 0.5 (empty input
        short-circuit, acceptance_gate.py — mirrored in
        backtest_gate_engine.py:762-764's cand_stability call).
      - backtest_gate_engine.py:773 hardcodes inc_stability = 1.0 ("the
        incumbent is, by definition, stable against itself") — NOT derived
        from empty params, so it does not degrade to 0.5 the way the
        candidate's does.
      - candidate_panel_score = 0.5*0.5 + 0.5*0.5 = 0.5 (acceptance_gate.py:230-232).
      - incumbent_panel_score = 0.5*1.0 + 0.5*0.5 = 0.75 (acceptance_gate.py:233-235,
        inc_stability=1.0, inc_prior_anchor=0.5 neutral).
      - The margin check (acceptance_gate.py:259,
        `candidate_panel_score >= incumbent_panel_score + PANEL_ADOPT_MARGIN_THRESHOLD`)
        requires 0.5 >= 0.75 — mathematically IMPOSSIBLE for any candidate
        with empty params, no matter how much better its returns are.
        Decision is KEEP_INCUMBENT every time (confirmed via probe:
        vetoes_passed=True, panel_score=0.5, decision=KEEP_INCUMBENT).
    This test is therefore genuinely RED for a STRUCTURAL reason, not merely
    a fixture-realism gap: _gate_and_accept_candidate needs to pass
    non-empty, non-structurally-disadvantaged params (e.g. identical
    non-empty candidate_params/incumbent_params/theory_prior_params so the
    panel's parameter-distance criteria — designed for Optuna parameter
    search, not tree-splice candidates — become a neutral tie rather than an
    unwinnable 0.5-vs-0.75 gap) so ADOPT_CANDIDATE is reachable when the
    returns genuinely warrant it. This assertion is NOT weakened to route
    around the finding — a worse implementation (one that never fixes the
    panel-score construction) must continue to fail this test.
    """
    incumbent_shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    # Strong, mostly-positive candidate with genuine (not near-zero-float-noise)
    # down days every 5th day — clears BHY/FDR significance (probe-verified
    # winner_p_adj ≈ 0.037 < HARVEY_LIU_FDR_Q=0.05) and its 20-day validation-fold
    # sum comfortably beats the incumbent's full 100-day sum (probe-verified
    # oos_alpha ≈ 9.4 vs incumbent full-period ≈ 1.7).
    candidate_shape_pct = [0.60, 0.60, 0.60, 0.60, -0.05, 0.60, 0.60, 0.60, 0.60, -0.05]

    incumbent_result = _make_shaped_result(incumbent_shape_pct, n_days=100)
    candidate_result = _make_shaped_result(candidate_shape_pct, n_days=100)

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
        "a candidate that clears the BHY/FDR significance gate AND the "
        "OOS-alpha-beats-both-baselines check was not queued — see this "
        "test's docstring for the confirmed root cause (the discretionary "
        "panel's parameter-distance criteria structurally floor the "
        "candidate at 0.5 vs the incumbent's hardcoded 1.0 stability when "
        "candidate_params/incumbent_params/theory_prior_params are empty)"
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
        pathlib.Path(__file__).resolve().parents[2] / "advisors" / "frontrunner_builder.py"
    ).read_text(encoding="utf-8")
    assert "dof" in source.lower() or "degrees_of_freedom" in source.lower(), (
        "frontrunner_builder.py has no reference to a DoF ledger anywhere — "
        "AC-6 requires the builder's search breadth to be recorded there"
    )


def test_search_breadth_is_recorded_to_the_dof_ledger_with_the_correct_kwargs(
    fbld, incumbent_symphony
):
    """AC-6 behavioral (not just source-grep): database.insert_dof_ledger_row
    must actually be CALLED during a build run, with kwargs matching the
    real signature (database.py:2047-2057) and its accepted enum values
    (database.py:2006-2029): facet_category in
    _VALID_DOF_FACET_CATEGORIES={"specification","parameter"}, decision_type
    in _VALID_DOF_DECISION_TYPES={"FIXED","SEARCHED","REVISED","OOS_PEEK"}.

    evidence_source="OVERLAY_BACKTEST_SELECTION" (NOT the autotuner's own
    "BACKTEST_SELECTION" literal — team-lead-ratified 2026-07-11, cff1264c).
    HISTORY: this test originally asserted "BACKTEST_SELECTION" (mirroring
    autotuner.py's own NN1-violation call site literally), which was
    CORRECT for the "search breadth recorded" half of AC-6 but created a
    real cross-subsystem contamination bug — every autotuner-side consumer
    that aggregates the ledger for the REAL N_effective/FDR haircut
    (database.get_researcher_dof_ledger_for_run, the production feed at
    autotuner.py:2487, and database.count_dof_backtest_selections) filters
    on that exact literal string with no producer/subsystem distinction, so
    a frontrunner row sharing it inflated N_effective for EVERY symphony's
    real autotune run, indefinitely (append-only ledger, never pruned). Full
    consumer audit + the isolation mechanism (a distinct evidence_source,
    since every polluting consumer keys on the literal string and none use
    IN/!=) is proven against the REAL production queries in
    tests/advisors/test_frontrunner_dof_isolation.py — read that file for
    the complete isolation proof; this test only pins the WRITE-SIDE
    contract (which value gets written), not the read-side isolation.

    spec_bundle_id is KEPT as a distinct sentinel too (belt-and-suspenders +
    audit legibility for count_dof_backtest_selections(spec_bundle_id=...)
    scoped queries and get_dof_ledger_for_bundle) — the evidence_source
    distinction is the mechanism that actually isolates the autotuner's
    N_effective; the sentinel alone was never sufficient (see git history on
    this docstring / test_frontrunner_dof_isolation.py for why).

    This must fire regardless of whether the candidate is ultimately
    accepted or rejected — the DoF ledger tracks SEARCH EXPOSURE, not
    outcomes (a rejected candidate still cost a look at the data). This test
    reuses the gate-rejected fixture shape (candidate underperforms, same
    idiom as test_a_calmar_rejected_candidate_is_not_queued) precisely to
    prove the ledger write does not depend on acceptance.
    """
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
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row") as mock_dof,
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    assert mock_dof.called, (
        "database.insert_dof_ledger_row was never called during a build run "
        "that generated and backtested a candidate — AC-6 requires search "
        "breadth to be recorded regardless of the accept/reject outcome"
    )
    _, call_kwargs = mock_dof.call_args
    assert isinstance(call_kwargs.get("facet_name"), str) and call_kwargs.get("facet_name"), (
        "facet_name must be a non-empty descriptive string"
    )
    assert call_kwargs.get("decision_type") == "SEARCHED", (
        f"expected decision_type='SEARCHED' (this is a search-breadth "
        f"record, not a FIXED/REVISED/OOS_PEEK spec decision), got "
        f"{call_kwargs.get('decision_type')!r}"
    )
    assert call_kwargs.get("evidence_source") == "OVERLAY_BACKTEST_SELECTION", (
        f"expected evidence_source='OVERLAY_BACKTEST_SELECTION' — a DISTINCT "
        f"value from the autotuner's own 'BACKTEST_SELECTION' literal "
        f"(team-lead-ratified isolation fix, cff1264c; see this test's "
        f"docstring + test_frontrunner_dof_isolation.py). Reusing the "
        f"autotuner's exact literal would re-pollute its N_effective/FDR "
        f"haircut for every symphony, got {call_kwargs.get('evidence_source')!r}"
    )
    assert call_kwargs.get("facet_category") == "specification", (
        f"expected facet_category='specification' "
        f"(_VALID_DOF_FACET_CATEGORIES has no 'candidate_search' option — "
        f"'specification' is the closest fit), got "
        f"{call_kwargs.get('facet_category')!r}"
    )
    spec_bundle_id = call_kwargs.get("spec_bundle_id")
    assert isinstance(spec_bundle_id, str) and spec_bundle_id, (
        f"spec_bundle_id must be a distinct non-empty sentinel string, NOT "
        f"None — a bare None row is indistinguishable from a real "
        f"pre-bundle-era autotuner row in any future audit query, got "
        f"{spec_bundle_id!r} (see this test's docstring for the isolation "
        f"caveat — a distinct sentinel is a real but PARTIAL floor, not a "
        f"guarantee against N_effective cross-subsystem pollution)"
    )
    n_configs = call_kwargs.get("n_configs_searched")
    assert isinstance(n_configs, int) and n_configs >= 1, (
        f"expected n_configs_searched to be a positive int reflecting how "
        f"many candidate overlays were generated+backtested this run, got "
        f"{n_configs!r}"
    )


# ---------------------------------------------------------------------------
# AC-11: rejected candidates are recorded (reason + deltas), not just logged.
# ---------------------------------------------------------------------------


def test_a_gate_rejected_candidate_is_recorded_as_a_rejected_advisor_observation_with_reason_and_deltas(
    fbld, incumbent_symphony
):
    """AC-11: 'fails gates ... → rejected item w/ reason+deltas.' A candidate
    that fails the overfitting gate (backtest_gate_engine.evaluate_candidate_batch)
    must still be recorded — via database.insert_advisor_observation, NOT
    database.insert_frontrunner_proposal (that path stays reserved for the
    operator approval QUEUE; a rejected candidate is never queued — see
    test_a_calmar_rejected_candidate_is_not_queued, which this test does not
    weaken or duplicate) — mirroring advisors/strategy_builder_engine.py's
    own insert_advisor_observation call site (~line 679), which
    unconditionally records BOTH survivors and rejects for every candidate
    it evaluates.

    'deltas' means the incumbent-vs-candidate Calmar/CAGR/max_drawdown
    figures must be present even on a REJECT — not just on accept (AC-8's
    metrics_json requirement is accept-only; AC-11's is universal). Expected
    values are derived from the SAME analytics.compute_quantstats_metrics
    call the production code itself makes on this fixture's own return
    series — never hardcoded.
    """
    from analytics import compute_quantstats_metrics

    incumbent_shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    # Weak/negative candidate — clearly gate-rejected (both on BHY significance
    # and on the OOS-alpha-beats-both-baselines check), reused here specifically
    # to prove the REJECT path now persists an audit record with reason+deltas.
    candidate_shape_pct = [-0.20, -0.30, -0.25, -0.35, -0.15, -0.40, -0.20, -0.30, -0.25, -0.10]

    incumbent_result = _make_shaped_result(incumbent_shape_pct, n_days=100)
    candidate_result = _make_shaped_result(candidate_shape_pct, n_days=100)

    expected_incumbent_metrics = compute_quantstats_metrics(
        [r * 100.0 for r in incumbent_result.daily_returns.values()]
    )
    expected_candidate_metrics = compute_quantstats_metrics(
        [r * 100.0 for r in candidate_result.daily_returns.values()]
    )

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert_proposal,
        patch("database.insert_advisor_observation") as mock_insert_obs,
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")

    mock_insert_proposal.assert_not_called()  # never queued — a reject is not a proposal
    assert mock_insert_obs.called, (
        "a gate-rejected candidate was not recorded via "
        "database.insert_advisor_observation — AC-11 requires a rejected "
        "item to carry its reason+deltas for operator/audit visibility, "
        "not silently disappear into a log line"
    )
    _, call_kwargs = mock_insert_obs.call_args
    assert call_kwargs.get("advisor_role") == "FRONTRUNNER_BUILDER"
    assert call_kwargs.get("subject_type") == "frontrunner_candidate"
    verdict = call_kwargs.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        "verdict must be a non-empty reason string, not silently omitted"
    )
    raw_response = call_kwargs.get("raw_response")
    assert isinstance(raw_response, dict), "raw_response must be a dict carrying reason+deltas"
    assert (
        "reject_reason" in raw_response or "gate" in verdict.lower() or "reject" in verdict.lower()
    ), (
        f"raw_response/verdict does not surface WHY the candidate was "
        f"rejected: verdict={verdict!r}, raw_response keys="
        f"{sorted(raw_response.keys())}"
    )
    for key, expected in (
        ("candidate_cagr", expected_candidate_metrics.get("annualized_return")),
        ("candidate_max_drawdown", expected_candidate_metrics.get("max_drawdown")),
        ("incumbent_cagr", expected_incumbent_metrics.get("annualized_return")),
        ("incumbent_max_drawdown", expected_incumbent_metrics.get("max_drawdown")),
    ):
        assert key in raw_response, (
            f"raw_response is missing {key!r} — AC-11's 'deltas' requirement "
            f"means the reject record must carry the same incumbent-vs-"
            f"candidate figures the accept path already persists "
            f"(_gate_and_accept_candidate's metrics dict, AC-8); keys found: "
            f"{sorted(raw_response.keys())}"
        )
        assert raw_response[key] == pytest.approx(expected, rel=1e-6, abs=1e-9), (
            f"{key}={raw_response[key]!r} does not match the value derived "
            f"from analytics.compute_quantstats_metrics on this fixture's "
            f"own return series ({expected!r}) — deltas must be real, not "
            f"placeholder/hardcoded"
        )
    assert "node_count_delta" in raw_response, (
        "raw_response is missing 'node_count_delta' — AC-11's deltas "
        "include the node-count comparison, not just Calmar/CAGR/MDD"
    )


# ---------------------------------------------------------------------------
# AC-11: no-incumbent / Fable-exhausted skips persist nothing, never crash.
# ---------------------------------------------------------------------------


def test_no_incumbent_detected_persists_neither_a_proposal_nor_an_observation(
    fbld, incumbent_symphony
):
    """AC-11: 'no incumbent FR → skip w/ reason.' When
    frontrunner_detector.detect_frontrunner_cascades finds no cascade, the
    orchestration must not call EITHER database.insert_frontrunner_proposal
    (nothing to queue) OR database.insert_advisor_observation (nothing was
    generated/gated/rejected — there is no candidate to record a reason
    against), and must not crash (D-1)."""
    from advisors import frontrunner_detector

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(
                cascades=[], skip_reason="no incumbent frontrunner"
            ),
        ),
        patch("database.insert_frontrunner_proposal") as mock_insert_proposal,
        patch("database.insert_advisor_observation") as mock_insert_obs,
        patch("database.insert_dof_ledger_row") as mock_dof,
    ):
        fbld._run_build_for_symphony("test-symphony-id")  # must not raise

    mock_insert_proposal.assert_not_called()
    mock_insert_obs.assert_not_called()
    mock_dof.assert_not_called()


def test_fable_generation_exhausted_persists_nothing_for_that_cascade(fbld, incumbent_symphony):
    """AC-11: 'Fable returns no-VIX/invalid candidate → reject + bounded
    retry → skip.' generate_candidate_overlay already enforces the bounded
    retry + VIX/invalid rejection at the unit level (test_frontrunner_builder.py);
    this is the ORCHESTRATION-level completeness check — when generation is
    exhausted (candidate=None), _run_build_for_symphony must skip that
    cascade without calling insert_frontrunner_proposal or
    insert_advisor_observation (there is no candidate tree to persist a
    reject record against — only a generation failure, distinct from a
    gate/Calmar rejection of a real candidate), and without crashing."""
    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch.object(
            fbld,
            "generate_candidate_overlay",
            return_value=fbld.GenerationResult(
                candidate=None, error="candidate fire branch contains no VIX-family ticker"
            ),
        ),
        patch("database.insert_frontrunner_proposal") as mock_insert_proposal,
        patch("database.insert_advisor_observation") as mock_insert_obs,
    ):
        fbld._run_build_for_symphony("test-symphony-id")  # must not raise

    mock_insert_proposal.assert_not_called()
    mock_insert_obs.assert_not_called()


# ---------------------------------------------------------------------------
# AC-12: Fable candidate-generation budget cap per symphony.
# ---------------------------------------------------------------------------


def test_fable_calls_per_symphony_are_bounded_by_a_budget_cap(fbld, incumbent_symphony):
    """AC-12: 'N candidates/symphony with a Fable API budget cap.' A
    symphony whose detector finds many parallel-sub-strategy cascades (a
    real large tree can have dozens of parallel sleeves) must not fan out
    into an unbounded number of Fable calls in one build — each Fable call
    is a billed API request. This asserts the call count is BOUNDED, not
    unbounded: strictly less than the number of cascades supplied (proving
    a cap exists) and at or under a documented sane ceiling.

    40 is the team-lead-ratified ceiling (2026-07-11, replacing this test's
    original 10): the real maximum qualifying-cascade count across the
    operator's captured trees was independently verified at 26 — a ceiling
    of 10 would have TRUNCATED, and silently dropped candidate generation
    for, 2 of the operator's legitimately complex live symphonies (the exact
    failure mode a budget cap must not itself cause). 40 gives real headroom
    above the verified real-world max while still bounding a malformed or
    adversarial detection result (e.g. hundreds of spurious cascades) from
    triggering unbounded Fable spend. The implementer's named constant is
    MAX_CASCADES_PER_SYMPHONY_RUN.
    """
    from advisors import frontrunner_detector

    _CEILING = 40
    n_cascades_supplied = _CEILING + 5  # comfortably more than any reasonable cap

    many_cascades = [
        frontrunner_detector.Cascade(overlay_tree={"id": f"cascade-node-{i}"})
        for i in range(n_cascades_supplied)
    ]

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.frontrunner_detector.detect_frontrunner_cascades",
            return_value=frontrunner_detector.DetectionResult(cascades=many_cascades),
        ),
        _patched_fable(fbld) as mock_build_client,
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("test-symphony-id")
        fable_client = mock_build_client.return_value
        call_count = fable_client.messages.create.call_count

    assert call_count < n_cascades_supplied, (
        f"Fable was called {call_count} times for {n_cascades_supplied} "
        f"detected cascades — every cascade triggered a generation call "
        f"with no budget cap; AC-12 requires an explicit per-symphony ceiling"
    )
    assert call_count <= _CEILING, (
        f"Fable call count ({call_count}) exceeds the documented sane "
        f"ceiling ({_CEILING}) — the per-symphony budget cap is too "
        f"generous or absent"
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
