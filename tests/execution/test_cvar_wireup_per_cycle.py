"""
RED tests — CVaR per-cycle wire-up (vision-audit Critical Rec #1).

DEFECT under test
-----------------
``alpha_bot_execution.py`` writes all-``None`` sentinels to ``cvar_diagnostic``
at the per-cycle write site (currently lines 1417-1426). The inline comment
("Phase-2 will populate them from forward-path simulation") is stale —
``math_engine.compute_portfolio_cvar`` IS the Phase-1 implementation
(kNN historical regime-match, README §3.2). The forward-path simulation
referenced in the stale comment is the REJECTED CVaR-divergence detector
path (decision-science council §B.9 / project memory
``project_cvar_divergence_validation_wall``).

The wire-up is a real defect, not a deferred feature.

SCOPE — what these tests pin
----------------------------
1. ``math_engine.compute_portfolio_cvar`` is invoked from the per-cycle
   path for each managed symphony, with the right inputs (holdings,
   historical_data, spy_today, cycle_id) — NOT all-None sentinels.
2. ``database.record_cvar_diagnostic`` receives the values that
   ``compute_portfolio_cvar`` returned — NOT a hard-coded None bundle.
3. The fail-safe contract is preserved: when ``compute_portfolio_cvar``
   legitimately returns an insufficient-data ``CVaRAssessment``
   (``cvar_pct=None``, ``tail_obs_count=0``) the per-cycle write
   records that sentinel as-is — it does not invent a value.
4. CVaR remains diagnostic-only — the four canonical exit triggers
   (Trailing Stop, VWAP Breakdown, VWAP Bleed Cut, Take-Profit) do
   NOT inspect the cvar_diagnostic table or any CVaR field.
5. The stale "Phase-2 will populate them from forward-path simulation"
   comment is gone from ``alpha_bot_execution.py``.

Mocking philosophy
------------------
* Math engine is NOT mocked wholesale — we mock only the specific math
  function under observation (``compute_portfolio_cvar``) so we can
  assert the call site without re-deriving every input value.
* Network, time, and the database layer are mocked (per the existing
  ``test_main_pipeline.py`` template).
* No live API calls. No real DB writes. No real sleeps.

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, assertions touching
producer-computed values are derived from the inputs we seed — never from
literal expectations of math the engine produced. The CVaR values flowing
through the assertion path are returned by our patched
``compute_portfolio_cvar`` stub so the test verifies *delegation*, not
*math correctness* (correctness is pinned in tests/engine/test_m2_cvar_*).
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

import alpha_bot_execution
import math_engine


# ---------------------------------------------------------------------------
# Shared fixture helpers (template borrowed from test_main_pipeline.py)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata missing
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


_SYMPHONY_ID = "sym-cvar-wireup-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-cvar-wireup-001"
_ACCOUNT_ID = "acct-cvar-wireup-001"
_SYMPHONY_NAME = "CVaR Wireup Symphony"
_TICKER = "SPY"

_ALPHA_BOT_PATH = pathlib.Path(__file__).parents[2] / "alpha_bot_execution.py"


def _make_symphony_payload(last_percent_change: float = 0.02):
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [
            {"ticker": _TICKER, "allocation": 1.0},
        ],
    }


def _make_minimal_history(current_date_str: str):
    return {
        current_date_str: {
            "SPY": {
                "c": 500.0,
                "daily_ret": 0.001,
                "high": 501.0,
                "low": 499.0,
                "close": 500.0,
            }
        }
    }


def _make_vwap_payload(last_price: float = 500.0):
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state():
    return {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: {
            "high_water_mark": 0.0,
            "shadow_hwm": 0.0,
            "prev_return": 0.0,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "mc_history": [],
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
        },
    }


@pytest.fixture
def patched_environment():
    """Network + DB + clock patched per test_main_pipeline.py template."""
    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym, \
         patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist, \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap, \
         patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state()
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"), "symphonies": {}
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [_make_symphony_payload()]
        mock_fetch_hist.return_value = _make_minimal_history(
            _FIXED_ET.strftime("%Y-%m-%d")
        )
        mock_fetch_vwap.return_value = _make_vwap_payload()

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


# ---------------------------------------------------------------------------
# SCOPE ITEM 1 — compute_portfolio_cvar is invoked from the per-cycle path
# ---------------------------------------------------------------------------


class TestComputePortfolioCvarInvokedPerCycle:
    """The per-cycle path MUST call ``math_engine.compute_portfolio_cvar``
    for each managed symphony. Today it calls ``record_cvar_diagnostic``
    with hard-coded Nones instead — that is the defect under audit.
    """

    def test_compute_portfolio_cvar_is_called_for_each_symphony(self, patched_environment):
        """Per-cycle path delegates the CVaR computation to math_engine.

        RED: the current code never calls compute_portfolio_cvar from the
        per-cycle path — it writes Nones directly via record_cvar_diagnostic.
        """
        env = patched_environment

        # Patch compute_portfolio_cvar so we can observe the call. Return a
        # legitimate insufficient-data sentinel so downstream consumers
        # (none expected — CVaR is diagnostic-only) hit the fail-safe path.
        insufficient = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            insufficient_reason="test-stub: insufficient history",
        )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=insufficient,
        ) as mock_cvar:
            alpha_bot_execution.main()

        assert mock_cvar.called, (
            "compute_portfolio_cvar was never invoked from the per-cycle path. "
            "Defect: the per-cycle write site (alpha_bot_execution.py:1417-1426) "
            "writes hard-coded None sentinels instead of calling the Phase-1 "
            "implementation. compute_portfolio_cvar IS the wire to use — "
            "the stale 'Phase-2 forward-path' comment refers to the REJECTED "
            "CVaR-divergence detector path (project memory: "
            "[[project_cvar_divergence_validation_wall]])."
        )

    def test_compute_portfolio_cvar_receives_the_symphony_holdings_and_history(
        self, patched_environment
    ):
        """The math function must be called with the symphony's own holdings
        and the historical_data dict that drives the kNN regime match.

        Pinned via shape (not literal value) per ``feedback_no_hardcoded_test_values``.
        """
        env = patched_environment
        insufficient = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            insufficient_reason="stub",
        )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=insufficient,
        ) as mock_cvar:
            alpha_bot_execution.main()

        assert mock_cvar.called, "compute_portfolio_cvar must be called"
        call = mock_cvar.call_args_list[0]
        kwargs = call.kwargs
        positional = call.args

        # Inputs may be positional OR keyword — accept either binding.
        def _get(name: str, pos_index: int):
            if name in kwargs:
                return kwargs[name]
            if len(positional) > pos_index:
                return positional[pos_index]
            return None

        holdings = _get("holdings", 1)
        historical_data = _get("historical_data", 2)
        spy_today_return = _get("spy_today_return", 3)
        cycle_id = _get("cycle_id", 0)

        assert holdings is not None, "holdings must be passed to compute_portfolio_cvar"
        assert isinstance(holdings, list), (
            f"holdings must be a list (the symphony's holdings list); got "
            f"{type(holdings).__name__}"
        )
        assert len(holdings) >= 1, "holdings must contain at least one row"
        # Each row is a dict with at minimum 'ticker' and 'allocation' — shape.
        first_row = holdings[0]
        assert isinstance(first_row, dict), "each holding row must be a dict"
        assert "ticker" in first_row, "holding row must carry 'ticker'"
        assert "allocation" in first_row, "holding row must carry 'allocation'"

        assert historical_data is not None, (
            "historical_data must be passed to compute_portfolio_cvar — it is "
            "the kNN regime-match input"
        )
        assert isinstance(historical_data, dict), "historical_data must be a dict"

        assert spy_today_return is not None, (
            "spy_today_return must be passed to compute_portfolio_cvar — it is "
            "the kNN today-feature input"
        )
        assert isinstance(spy_today_return, (int, float)), (
            "spy_today_return must be numeric"
        )

        assert cycle_id is not None, (
            "cycle_id must be passed to compute_portfolio_cvar — it is the "
            "replay-determinism anchor (math_engine docstring §3.7)"
        )
        assert isinstance(cycle_id, str), "cycle_id must be a string"
        assert len(cycle_id) > 0, "cycle_id must be non-empty"


# ---------------------------------------------------------------------------
# SCOPE ITEM 2 — record_cvar_diagnostic receives the computed values
# ---------------------------------------------------------------------------


class TestRecordCvarDiagnosticReceivesComputedValues:
    """The per-cycle write must persist the values returned by
    ``compute_portfolio_cvar`` — not a hard-coded None bundle.
    """

    def test_record_cvar_diagnostic_receives_computed_cvar_pct(self, patched_environment):
        """When compute_portfolio_cvar returns a real value, that value
        flows to record_cvar_diagnostic. The current code throws it away
        and writes None.
        """
        env = patched_environment

        # Stub a deterministic CVaRAssessment with a real, finite value.
        # The cvar_pct here is a test fixture sentinel; we just verify the
        # write site preserves it (not what value compute_portfolio_cvar
        # computes from real history — that is pinned in tests/engine/).
        stub_cvar_pct = -2.5  # any non-None value the stub returns
        stub_tail_count = 8   # any non-zero count
        stub_stub = math_engine.CVaRAssessment(
            cvar_pct=stub_cvar_pct,
            breach=False,
            tail_obs_count=stub_tail_count,
            insufficient_reason=None,
        )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=stub_stub,
        ):
            alpha_bot_execution.main()

        # Find the record_cvar_diagnostic call on the mocked database.
        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, (
            "record_cvar_diagnostic was never invoked — the per-cycle "
            "telemetry write was lost or removed."
        )

        # At least one of the calls must carry the stub's value (not None).
        observed = []
        for call in record_calls:
            kw = call.kwargs
            args = call.args
            observed.append((args, kw))
            # Locate cvar_5pct in kwargs (the H4 helper uses kw-only signature
            # for the named columns).
            v = kw.get("cvar_5pct")
            n = kw.get("cvar_n_tail")
            if v == pytest.approx(stub_cvar_pct, rel=1e-9) and n == stub_tail_count:
                return  # the wire is honest — test passes

        pytest.fail(
            "record_cvar_diagnostic never received the value compute_portfolio_cvar "
            f"returned. Expected cvar_5pct={stub_cvar_pct}, cvar_n_tail={stub_tail_count}; "
            f"observed calls: {observed}. This is the all-None-sentinel defect — the "
            "per-cycle write site is hard-coded to write Nones (vision-audit Critical #1)."
        )

    def test_insufficient_data_sentinel_is_preserved_through_the_write(
        self, patched_environment
    ):
        """The fail-safe contract: when compute_portfolio_cvar returns an
        insufficient-data CVaRAssessment (cvar_pct=None, tail_obs_count=0),
        the per-cycle write records None — not an invented value. The
        post_init guard in CVaRAssessment.__post_init__ enforces the
        invariant on the math side; this test pins the persistence side.
        """
        env = patched_environment
        insufficient = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            insufficient_reason="insufficient eligible history",
        )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=insufficient,
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must still fire on insufficient-data"

        # At least one call must carry None for cvar_5pct (the legitimate sentinel).
        saw_none = False
        for call in record_calls:
            if call.kwargs.get("cvar_5pct") is None:
                saw_none = True
                # tail count: the H4 helper coerces None → 0 for the NOT NULL column.
                # Either the stub-returned 0 or the coerced 0 is acceptable.
                tail = call.kwargs.get("cvar_n_tail")
                assert tail in (0, None), (
                    "on insufficient data, cvar_n_tail must be 0 (or coerced from None); "
                    f"got {tail!r}"
                )
        assert saw_none, (
            "No record_cvar_diagnostic call carried the insufficient-data sentinel "
            "(cvar_5pct=None). On insufficient data the write must preserve None, "
            "not invent a number."
        )

    def test_record_cvar_diagnostic_carries_explicit_mode(self, patched_environment):
        """The H4 helper requires an explicit mode= (no default). The per-cycle
        call site must supply ``mode='live'``.
        """
        env = patched_environment
        insufficient = math_engine.CVaRAssessment(
            cvar_pct=None, breach=False, tail_obs_count=0, insufficient_reason="x",
        )
        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=insufficient,
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        for call in record_calls:
            mode = call.kwargs.get("mode")
            # mode may be positional in some signatures; flatten args+kwargs.
            if mode is None:
                args_str = str(call.args) + str(call.kwargs)
                assert "live" in args_str or "replay" in args_str, (
                    f"record_cvar_diagnostic called without explicit mode= ; "
                    f"args={call.args!r}, kwargs={call.kwargs!r}"
                )
            else:
                assert mode in ("live", "replay"), (
                    f"mode must be 'live' or 'replay'; got {mode!r}"
                )


# ---------------------------------------------------------------------------
# SCOPE ITEM 3 — CVaR remains diagnostic-only (no exit trigger consumes it)
# ---------------------------------------------------------------------------


class TestCvarRemainsDiagnosticOnly:
    """The four canonical exit triggers (Trailing Stop, VWAP Breakdown,
    VWAP Bleed Cut, Take-Profit) must NOT inspect any CVaR field or the
    cvar_diagnostic table. CVaR is operator instrumentation only
    (README §4.4; project memory ``project_eut_cvar_migration_council_verdict``).
    """

    def test_trigger_resolver_does_not_take_a_cvar_argument(self):
        """resolve_trigger_priority's signature must remain
        (is_vwap_broken, is_tp_hit, is_vwap_bleed_broken, is_trailing_stop_hit).
        Adding a cvar_breach flag would mean CVaR became a trigger — a
        binding violation of the diagnostic-only constraint.
        """
        import inspect
        sig = inspect.signature(math_engine.resolve_trigger_priority)
        params = list(sig.parameters.keys())
        expected = [
            "is_vwap_broken",
            "is_tp_hit",
            "is_vwap_bleed_broken",
            "is_trailing_stop_hit",
        ]
        assert params == expected, (
            f"resolve_trigger_priority signature has changed to {params!r}. "
            f"The four canonical triggers must remain exactly {expected!r}; any "
            "new parameter implies a fifth trigger has been wired — a violation "
            "of the diagnostic-only constraint on CVaR (README §4.4)."
        )

    def test_alpha_bot_execution_does_not_read_cvar_diagnostic_table_from_engine_path(self):
        """The engine path must not READ the cvar_diagnostic table — it
        only WRITES it. A SELECT against cvar_diagnostic in
        alpha_bot_execution.py would mean a trigger started consuming the
        diagnostic.
        """
        source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
        # Match SELECT ... FROM cvar_diagnostic (singular table name in the
        # write helper) or cvar_diagnostics (table name in storage).
        select_pattern = re.compile(
            r"SELECT\s+[\s\S]{0,200}FROM\s+cvar_diagnostic",
            re.IGNORECASE,
        )
        matches = select_pattern.findall(source)
        assert not matches, (
            "alpha_bot_execution.py contains a SELECT against cvar_diagnostic — "
            "CVaR must remain diagnostic-only (READ surface = dashboard / advisors, "
            "never the engine path)."
        )


# ---------------------------------------------------------------------------
# SCOPE ITEM 5 — stale 'Phase-2 forward-path' comment removed
# ---------------------------------------------------------------------------


class TestStalePhaseTwoCommentRemoved:
    """The inline comment 'Phase-2 will populate them from forward-path
    simulation' is stale because:
      (a) compute_portfolio_cvar IS the Phase-1 implementation, and
      (b) forward-path simulation is the REJECTED CVaR-divergence path
          (project memory ``project_cvar_divergence_validation_wall``).
    Leaving the comment misleads future readers into rebuilding the
    rejected path.
    """

    def test_stale_phase_2_forward_path_comment_is_absent(self):
        source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
        # Match the canonical phrasing AND a loose variant.
        forbidden_phrases = [
            "Phase-2 will populate them from forward-path simulation",
            "Phase-2 will populate them from forward-path",
            "populate them from forward-path simulation",
            "forward-path simulation",  # generic — any mention is wrong here
        ]
        offending = [p for p in forbidden_phrases if p in source]
        assert not offending, (
            f"alpha_bot_execution.py still contains stale CVaR-deferral comments: "
            f"{offending!r}. Forward-path simulation is the REJECTED CVaR-divergence "
            "path (project memory [[project_cvar_divergence_validation_wall]]); "
            "compute_portfolio_cvar IS the Phase-1 implementation. Remove the comment."
        )

    def test_stale_all_none_sentinel_comment_is_absent(self):
        """The 'all cvar_* values are None sentinels' description in the
        write-site comment block is incorrect post-fix — the cvar_5pct +
        cvar_n_tail columns are populated from compute_portfolio_cvar.
        """
        source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
        forbidden = "all cvar_* values are\n                # None sentinels"
        # Also catch the squashed form on one line.
        forbidden_single = "all cvar_* values are None sentinels"
        assert forbidden not in source and forbidden_single not in source, (
            "alpha_bot_execution.py still describes the per-cycle CVaR write as "
            "'all cvar_* values are None sentinels'. Post-fix this is incorrect — "
            "cvar_5pct and cvar_n_tail are populated from compute_portfolio_cvar."
        )
