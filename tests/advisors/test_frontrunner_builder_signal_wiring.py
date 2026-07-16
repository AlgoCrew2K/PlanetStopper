"""RED tests — AC-5 WIRING: builder signal-consumption gating actually
invoked from the real _run_build_for_symphony orchestration path.

Module under test: advisors.frontrunner_builder._run_build_for_symphony
(EXISTING function, currently MISSING this wiring). Implementer: fr-engine.

WHY THIS FILE EXISTS (team-lead, 2026-07-16, URGENT dispatch): fr-review
verified by reading _run_build_for_symphony end-to-end (frontrunner_builder.py:
1339-1502, pre-this-cycle) that every AC-5 function
(filter_positive_edge_signal_keys, candidate_contains_tier1_remove_key,
build_signal_provenance, resolve_signals_unavailable_marker,
persist_classification_run) is correct in ISOLATION
(tests/advisors/test_frontrunner_builder_signal_gating.py,
tests/advisors/test_frontrunner_signals_warehouse.py — both GREEN) and NOTHING
calls them from production: no classification, no edge stats in
signal_context, no Tier-1 gate, no provenance in metrics_json, and
persist_classification_run invoked NOWHERE. The warehouse tables and the
AC-7 dashboard tab would stay permanently empty in production. This is the
"route-level-RED" lesson recurring at the builder level (memory:
feedback_route_level_red_for_mocked_analytics_fns) — unit-testing the pieces
can't catch missing wiring; only an integration-level test that drives the
REAL orchestration path can.

MOCKING STRATEGY: mirrors tests/advisors/test_frontrunner_gate_wiring.py's
established idiom exactly (same incumbent_symphony construction pattern, same
_patched_fable/_make_fake_result helpers, same "patch collaborators at their
ORIGIN module" philosophy). Only external seams are mocked: symphony_logic.
fetch_symphony_score, the Fable client factory (_build_client),
composer_backtest_client.run_backtest, advisors.community_strats.
load_community_strategies (Atlas — autouse, mirrors that file's own
_no_live_atlas_calls guard), and advisors.frontrunner_signals.
load_frontrunner_signals (the ONE new external seam this file adds — the
Atlas frontrunner-signals collection read). extract_fr_checks,
classify_fr_checks, and persist_classification_run/get_latest_classifications/
get_latest_run_marker are the REAL functions, run for real, so a genuinely
wired implementation is provably exercised end-to-end — not spied-and-stubbed
into passing.

WAREHOUSE DB ROUTING: advisors.frontrunner_signals._warehouse_db_file enforces
a pytest sentinel (RuntimeError on db_path=None under pytest — mirrors
lens_warehouse._warehouse_db_file). _run_build_for_symphony's current
signature has no db_path parameter, and this file deliberately does NOT
prescribe whether fr-engine's wiring threads an explicit db_path kwarg through
from run_frontrunner_build or relies on the default. Patching
_warehouse_db_file directly (the lowest common seam every persist/read call
funnels through) routes every real warehouse call to a temp file regardless
of which shape fr-engine picks — forcing the OUTCOME (rows land somewhere
inspectable) without over-pinning the call-site signature, per team-lead's
explicit instruction ("force that shape without over-pinning line numbers").

Team-lead's 6-point ask (URGENT dispatch, 2026-07-16T17:17:22.800Z) + the
explicit negative pin, each mapped to one test below:
  1. extract_fr_checks -> classify_fr_checks actually execute on the real run
  2. persist_classification_run called + warehouse contains the rows after
  3. generation prompt carries the positive-edge signal's stat lines
  4. a Tier-1-remove-watching candidate is rejected before any backtest call
  5. metrics_json on a persisted proposal carries signal_provenance
  6. signals-unavailable degrades to structural-only AND persists the marker
  7. (negative pin) a healthy-signals run does NOT show the empty-state
     condition on get_latest_classifications/get_latest_run_marker
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures — mirror test_frontrunner_gate_wiring.py's established idiom
# ---------------------------------------------------------------------------


@pytest.fixture
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


@pytest.fixture(autouse=True)
def _no_live_atlas_calls():
    """Same guard as test_frontrunner_gate_wiring.py — _gather_atlas_frontrunner_patterns
    calls the real community_strats.load_community_strategies unless patched,
    which can attempt a genuine MongoClient connection with no fresh cache row."""
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value={"available": False, "candidates": [], "stats": {}, "source": "captplanet"},
    ):
        yield


@pytest.fixture
def warehouse_db_path(tmp_path) -> str:
    return str(tmp_path / "test_frontrunner_wiring_warehouse.db")


@pytest.fixture(autouse=True)
def _route_warehouse_to_temp_db(warehouse_db_path):
    """Every real frontrunner_signals persistence/read call in this file must
    land in a temp file, never the production warehouse DB, and never trip
    the pytest sentinel — see module docstring's WAREHOUSE DB ROUTING note."""
    with patch("advisors.frontrunner_signals._warehouse_db_file", return_value=warehouse_db_path):
        yield


@pytest.fixture
def incumbent_symphony() -> dict:
    """A minimal real incumbent symphony with a genuine, extractable FR-check
    (RSI(SPY,10) gt 31 -> VIXY) — directly verified (not assumed) against both
    extract_fr_checks and detect_frontrunner_cascades before writing this
    fixture: extract_fr_checks(tree) returns exactly one FRCheck with
    fr_key='SPY:10:31', and detect_frontrunner_cascades(tree) reports exactly
    one cascade. Same symphony_schema construction pattern as
    test_frontrunner_gate_wiring.py's own incumbent_symphony fixture (proven
    detectable there), with threshold=31 instead of 80 so the fr_key
    ('SPY:10:31') matches this cycle's canonical genuine-signal example
    throughout the feature plan and fixtures."""
    from advisors import symphony_schema

    incumbent_cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            31,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root("Wiring Test Symphony", "daily", [incumbent_cascade])


def _make_fake_result(n_days: int = 100, base_return: float = 0.001):
    """Verbatim from test_frontrunner_gate_wiring.py."""
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)

    return BacktestResult(stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns)


def _make_shaped_result(shape_pct: list, n_days: int = 100):
    """Verbatim from test_frontrunner_gate_wiring.py — see that file's
    docstring on test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics
    for why a hand-shaped (genuinely two-sided) return series is required to
    clear the BHY/FDR + Calmar acceptance gate at all."""
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = shape_pct[i % len(shape_pct)] / 100.0
        d += timedelta(days=1)

    return BacktestResult(stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns)


def _mocked_fable_overlay_client(
    ticker: str = "SPY", window: int = 10, threshold: float = 31, vix_ticker: str = "UVXY"
) -> MagicMock:
    """Parameterized version of test_frontrunner_gate_wiring.py's
    _mocked_fable_overlay_client — same DSL shape, configurable condition so
    the generated candidate's watched fr_key can be made to match (or not
    match) a specific classification row across different tests. Directly
    verified before writing this file: this exact shape compiles cleanly via
    the real generate_candidate_overlay -> plan_tree_compiler.compile_plan
    path (no candidate=None / compile-failure surprise)."""
    overlay = {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": ticker,
            "window": window,
            "comparator": "gt",
            "rhs": {"fixed": threshold},
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


def _patched_fable(fbld_module, **overlay_kwargs):
    return patch.object(
        fbld_module, "_build_client", return_value=_mocked_fable_overlay_client(**overlay_kwargs)
    )


def _healthy_signals(fr_key: str = "SPY:10:31", cagr: float = 0.06, sharpe: float = 1.0) -> dict:
    return {
        "available": True,
        "reason": None,
        "signals": [
            {
                "fr_key": fr_key,
                "cagr": cagr,
                "sharpe": sharpe,
                "rsi_live": 55.0,
                "rsi_live_at": "2026-07-16T07:01:00Z",
                "sortino": None,
                "calmar": None,
                "max_drawdown": None,
                "fetch_ts": "2026-07-16T15:00:00Z",
            }
        ],
        "stats": {},
        "source": "captplanet",
    }


# ---------------------------------------------------------------------------
# 1. extract_fr_checks -> classify_fr_checks actually execute
# ---------------------------------------------------------------------------


def test_extract_fr_checks_and_classify_fr_checks_actually_execute_on_the_real_run(
    fbld, incumbent_symphony
):
    """AC-5 wiring floor: extract_fr_checks and classify_fr_checks must be
    CALLED during a real _run_build_for_symphony run — not just importable,
    not just unit-tested in isolation. Spied via wraps=<real function> so the
    REAL implementation still runs (proving genuine execution, not a stub
    swallowing the call and returning a canned value)."""
    from advisors import frontrunner_detector, frontrunner_signals

    fake_signals = _healthy_signals()
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        patch.object(
            frontrunner_detector, "extract_fr_checks", wraps=frontrunner_detector.extract_fr_checks
        ) as spy_extract,
        patch.object(
            frontrunner_signals, "classify_fr_checks", wraps=frontrunner_signals.classify_fr_checks
        ) as spy_classify,
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-test-symphony")

    assert spy_extract.called, (
        "extract_fr_checks was never called during a real build run — AC-5's "
        "signal-consumption gating requires the orchestration to extract the "
        "incumbent's own FR-checks, not just have the function available"
    )
    extract_args = spy_extract.call_args[0]
    assert extract_args and extract_args[0] == incumbent_symphony, (
        f"extract_fr_checks was called with something other than the fetched "
        f"incumbent symphony tree; call_args={spy_extract.call_args!r}"
    )

    assert spy_classify.called, "classify_fr_checks was never called during a real build run"
    classify_call = spy_classify.call_args
    signal_rows_arg = (
        classify_call.args[1] if len(classify_call.args) > 1 else classify_call.kwargs.get("signal_rows")
    )
    assert signal_rows_arg == fake_signals["signals"], (
        f"classify_fr_checks was not given the signal rows load_frontrunner_signals "
        f"returned — got {signal_rows_arg!r}"
    )


# ---------------------------------------------------------------------------
# 2. persist_classification_run called + warehouse contains the rows after
# ---------------------------------------------------------------------------


def test_persist_classification_run_is_called_and_warehouse_contains_the_rows_after_the_run(
    fbld, incumbent_symphony, warehouse_db_path
):
    """AC-5 wiring: the classified rows must actually be persisted, not just
    computed in memory — proven by reading them back via
    get_latest_classifications after the run, through the temp db_path this
    file routes _warehouse_db_file to (see module docstring)."""
    from advisors import frontrunner_signals

    fake_signals = _healthy_signals()
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-test-symphony")

    rows = frontrunner_signals.get_latest_classifications(
        "wiring-test-symphony", db_path=warehouse_db_path
    )
    assert rows, (
        "get_latest_classifications returned no rows after a real build run — "
        "persist_classification_run was never actually called, so the "
        "warehouse tables (fr-data's Cluster A) and the AC-7 dashboard tab "
        "(fr-fe's Cluster E) would stay permanently empty in production"
    )
    fr_keys = {r["fr_key"] for r in rows}
    assert "SPY:10:31" in fr_keys, (
        f"expected the incumbent's own classified fr_key 'SPY:10:31' among "
        f"the persisted rows, got {sorted(fr_keys)}"
    )


# ---------------------------------------------------------------------------
# 3. generation prompt carries the positive-edge signal's stat lines
# ---------------------------------------------------------------------------


def test_generation_prompt_carries_positive_edge_signal_stat_lines(fbld, incumbent_symphony):
    """AC-5: 'generation prompt/context receives the edge stat lines for the
    signals it may use.' Spies on _build_generation_prompt (the seam that
    turns signal_context into the actual SDK prompt string, already patched
    for exactly this kind of proof elsewhere in this suite) and asserts on
    the RETURNED PROMPT TEXT — the objectively observable artifact Fable
    actually sees — rather than guessing at an intermediate signal_context
    key name fr-engine hasn't chosen yet (module docstring: 'force that shape
    without over-pinning')."""
    from advisors import frontrunner_builder as fbld_module

    fake_signals = _healthy_signals(fr_key="SPY:10:31", cagr=0.06, sharpe=1.0)
    fake_result = _make_fake_result()

    # patch.object(..., wraps=...) does not retain per-call return values
    # (there is no real `.spy_return` on a plain unittest.mock wraps-mock —
    # that's a pytest-mock `mocker.spy` feature, not available here); capture
    # the real return explicitly via a side_effect wrapper instead. The real
    # function is saved to a local name BEFORE patching — calling
    # fbld_module._build_generation_prompt(...) from inside the wrapper would
    # resolve to the PATCHED attribute at call time (Python looks up module
    # attributes dynamically), causing infinite self-recursion.
    _real_build_prompt = fbld_module._build_generation_prompt
    captured: dict = {}

    def _capture_and_call(*args, **kwargs):
        result = _real_build_prompt(*args, **kwargs)
        captured["prompt"] = result
        return result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        patch.object(
            fbld_module, "_build_generation_prompt", side_effect=_capture_and_call
        ) as spy_prompt,
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-test-symphony")

    assert spy_prompt.called, "the generation prompt was never built during a real run"
    rendered_prompt = captured.get("prompt")
    assert isinstance(rendered_prompt, str) and rendered_prompt, (
        "_build_generation_prompt returned no usable prompt string"
    )
    assert "SPY:10:31" in rendered_prompt, (
        "the rendered generation prompt does not mention the positive-edge "
        "signal's own key ('SPY:10:31') anywhere — AC-5 requires the prompt "
        "to actually surface the edge stat lines to the model, not just "
        "carry them in an unrendered signal_context dict"
    )


# ---------------------------------------------------------------------------
# 4. a Tier-1-remove-watching candidate is rejected before any backtest call
# ---------------------------------------------------------------------------


def test_run_build_rejects_a_tier1_remove_candidate_before_any_backtest_spend(
    fbld, incumbent_symphony
):
    """AC-5(b) WIRING: when the incumbent's own extracted+classified FR-check
    is Tier-1 'remove' and Fable's generated candidate watches that SAME
    ticker/window/threshold, the orchestration must veto it BEFORE calling
    run_backtest at all — proven by patching run_backtest to explode (mirrors
    test_frontrunner_gate_wiring.py's own no-trade-boundary explode-on-call
    idiom) and confirming it is never reached."""
    fake_signals = _healthy_signals(fr_key="SPY:10:31", cagr=-0.37, sharpe=-0.42)  # Tier 1: remove

    def _explode(*_a, **_k):
        raise AssertionError("run_backtest was called on a Tier-1-remove-watching candidate")

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        _patched_fable(fbld, ticker="SPY", window=10, threshold=31),
        patch(
            "advisors.composer_backtest_client.run_backtest", side_effect=_explode
        ) as mock_backtest,
        patch("database.insert_frontrunner_proposal") as mock_insert,
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-test-symphony")  # must not raise (D-1)

    assert not mock_backtest.called, (
        "run_backtest was called despite the generated candidate watching a "
        "Tier-1 'remove' fr_key ('SPY:10:31', cagr=-0.37, sharpe=-0.42) — "
        "AC-5(b) requires the veto to fire BEFORE any backtest spend"
    )
    mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# 5. metrics_json on a persisted proposal carries signal_provenance
# ---------------------------------------------------------------------------


def test_persisted_proposal_metrics_json_carries_signal_provenance(fbld, incumbent_symphony):
    """AC-5(c): provenance on each candidate records the signal keys + their
    edge stats — WIRING proof that this actually lands in the persisted
    metrics_json (the pure build_signal_provenance function is already
    unit-tested in test_frontrunner_builder_signal_gating.py; this proves the
    orchestration actually calls it and attaches the result). Reuses the
    proven gate/Calmar-clearing fixture shapes from
    test_frontrunner_gate_wiring.py's
    test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics verbatim
    (see that test's own extensively-documented root-cause finding on why
    these specific shapes are required to reach ADOPT_CANDIDATE) so this test
    exercises the real accept path rather than re-deriving gate-clearance
    math independently."""
    fake_signals = _healthy_signals(fr_key="SPY:10:31", cagr=0.06, sharpe=1.0)  # keep
    incumbent_shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
    candidate_shape_pct = [0.60, 0.60, 0.60, 0.60, -0.05, 0.60, 0.60, 0.60, 0.60, -0.05]

    incumbent_result = _make_shaped_result(incumbent_shape_pct, n_days=100)
    candidate_result = _make_shaped_result(candidate_shape_pct, n_days=100)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return incumbent_result if call_count["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
        patch("database.insert_frontrunner_proposal") as mock_insert,
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-test-symphony")

    assert mock_insert.called, (
        "expected the surviving candidate to be queued — this test reuses "
        "the exact fixture shapes proven to clear the gate/Calmar accept "
        "path in test_frontrunner_gate_wiring.py; if this fails, the accept "
        "path itself regressed, not signal_provenance wiring specifically"
    )
    _, call_kwargs = mock_insert.call_args
    metrics = call_kwargs.get("metrics_json")
    assert isinstance(metrics, dict) and "signal_provenance" in metrics, (
        f"metrics_json is missing 'signal_provenance' — AC-5(c) requires the "
        f"persisted proposal to carry the signal keys + edge stats used, "
        f"got keys={sorted((metrics or {}).keys())}"
    )
    provenance = metrics["signal_provenance"]
    assert isinstance(provenance, dict) and provenance, (
        f"signal_provenance must be a non-empty dict, got {provenance!r}"
    )


# ---------------------------------------------------------------------------
# 6. signals-unavailable degrades to structural-only AND persists the marker
# ---------------------------------------------------------------------------


def test_signals_unavailable_run_proceeds_structural_only_and_persists_the_marker(
    fbld, incumbent_symphony, warehouse_db_path
):
    """AC-5(d): 'When signals are unavailable (D-1 degraded), generation
    degrades to structural-only WITH an explicit signals_unavailable marker
    on the run — never silently.' Proves BOTH halves: (1) the run still
    proceeds to generate/backtest a candidate structurally (never aborts the
    whole symphony just because Atlas is down), and (2) get_latest_run_marker
    reflects signals_unavailable=True with the D-1 reason string, not
    silently absent."""
    from advisors import frontrunner_signals

    degraded_signals = {
        "available": False,
        "reason": "AtlasFetchTimeout",
        "signals": [],
        "stats": {},
        "source": "captplanet",
    }
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=degraded_signals),
        _patched_fable(fbld),
        patch(
            "advisors.composer_backtest_client.run_backtest", return_value=fake_result
        ) as mock_backtest,
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-degraded-symphony")

    assert mock_backtest.called, (
        "the build aborted entirely when signals were unavailable — AC-5(d) "
        "requires degrading to structural-only generation, never silently "
        "skipping the whole symphony just because Atlas is down"
    )

    marker = frontrunner_signals.get_latest_run_marker(
        "wiring-degraded-symphony", db_path=warehouse_db_path
    )
    assert marker is not None, (
        "no run marker was persisted for a symphony run with signals "
        "unavailable — AC-5(d) requires an explicit marker, never silence"
    )
    assert marker["signals_unavailable"] is True
    assert marker["reason"] == "AtlasFetchTimeout", (
        f"the D-1 reason must be propagated onto the persisted run marker, "
        f"not swallowed; got {marker.get('reason')!r}"
    )


# ---------------------------------------------------------------------------
# 7. Negative pin: a healthy-signals run does NOT show the empty-state
# ---------------------------------------------------------------------------


def test_signals_available_run_does_not_show_the_empty_state_condition(
    fbld, incumbent_symphony, warehouse_db_path
):
    """Negative pin (team-lead's explicit ask): the healthy-signals case must
    NOT show the degraded/empty-state condition on get_latest_classifications
    or get_latest_run_marker — proving the wiring is genuinely conditional on
    real signal availability, not a heuristic that always reports one state
    regardless of input (the exact failure mode a mock that always returns a
    canned 'degraded' or always returns a canned 'healthy' result would
    produce without anyone noticing)."""
    from advisors import frontrunner_signals

    fake_signals = _healthy_signals(fr_key="SPY:10:31", cagr=0.06, sharpe=1.0)
    fake_result = _make_fake_result()

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
        _patched_fable(fbld),
        patch("advisors.composer_backtest_client.run_backtest", return_value=fake_result),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld._run_build_for_symphony("wiring-healthy-symphony")

    rows = frontrunner_signals.get_latest_classifications(
        "wiring-healthy-symphony", db_path=warehouse_db_path
    )
    assert rows, "expected classification rows to be persisted for a healthy-signals run"
    assert not all(r["classification"] == "no_edge_data" for r in rows), (
        f"every persisted row is no_edge_data even though signals were "
        f"available and a positive-edge key ('SPY:10:31', cagr=0.06, "
        f"sharpe=1.0) was supplied — the empty-state condition is showing "
        f"up when it should not; rows={rows!r}"
    )

    marker = frontrunner_signals.get_latest_run_marker(
        "wiring-healthy-symphony", db_path=warehouse_db_path
    )
    assert marker is not None, "expected a run marker to be persisted even for a healthy run"
    assert marker["signals_unavailable"] is False, (
        f"expected signals_unavailable=False for a healthy run, got marker={marker}"
    )
