"""
RED tests — second-window CVaR (cvar_5pct_long / cvar_n_tail_long) gating.

SCOPE — item 7 of the vision-audit Critical Rec #1 dispatch brief
-----------------------------------------------------------------
The cvar_diagnostic schema has TWO CVaR columns:
  * cvar_5pct + cvar_n_tail        — the primary (short) kNN window
  * cvar_5pct_long + cvar_n_tail_long — the operator-optional second window

The second window is gated on the ``SECOND_WINDOW_CVAR_ENABLED`` environment
flag. Per ``advisors/divergence_explainer.py:19-22``:

    > Feature flag (SECOND_WINDOW_CVAR_ENABLED):
    >   §B is NOT YET ENABLED in production. Default: off.
    >   When off: writes one NOT_APPLICABLE row for the audit trail.
    >   When on:  writes one INFORMATIONAL row with both window values.

When the flag is OFF (default), the per-cycle ``record_cvar_diagnostic``
write must persist None for ``cvar_5pct_long`` and ``cvar_n_tail_long`` —
the cells stay empty so the Divergence Explainer's NOT_APPLICABLE row
remains the source of truth.

When the flag is ON, the engine path must populate the long-window
columns by calling ``compute_portfolio_cvar`` (or an equivalent
configured-window invocation) for the second window — NOT by inventing
a value or by reading from the existing short-window result.

Hard wall preserved
-------------------
Even with the flag ON, no signed divergence between the two windows is
ever computed or persisted. The divergence detector idea is permanently
REJECTED (decision-science council §B.9; memory
``project_cvar_divergence_validation_wall``).

Mocking philosophy
------------------
Same as ``test_cvar_wireup_per_cycle.py`` — patch math_engine.compute_portfolio_cvar
to observe call patterns; patch the database wholesale to inspect writes.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

import alpha_bot_execution
import math_engine


# ---------------------------------------------------------------------------
# Shared fixture (cloned from test_cvar_wireup_per_cycle.py)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)
except Exception:  # pragma: no cover
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-cvar-sw-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-cvar-sw-001"
_ACCOUNT_ID = "acct-cvar-sw-001"
_SYMPHONY_NAME = "CVaR SecondWindow Symphony"
_TICKER = "SPY"


def _make_symphony_payload():
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": 0.01,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(current_date_str: str):
    return {
        current_date_str: {
            "SPY": {"c": 500.0, "daily_ret": 0.001, "high": 501.0, "low": 499.0, "close": 500.0}
        }
    }


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
def patched_env_factory():
    """Returns a context-manager-like factory so individual tests can
    set environment-flag values before main() runs.
    """

    def _enter(second_window_flag: "str | None"):
        cm = _PatchedEnv(second_window_flag)
        return cm

    return _enter


class _PatchedEnv:
    def __init__(self, second_window_flag: "str | None"):
        self._second_window_flag = second_window_flag
        self._patches: list = []
        self.env: dict = {}

    def __enter__(self):
        import os

        # Apply the env-flag manipulation FIRST.
        self._prev_flag = os.environ.get("SECOND_WINDOW_CVAR_ENABLED")
        if self._second_window_flag is None:
            os.environ.pop("SECOND_WINDOW_CVAR_ENABLED", None)
        else:
            os.environ["SECOND_WINDOW_CVAR_ENABLED"] = self._second_window_flag

        patches = [
            patch.object(alpha_bot_execution, "database"),
            patch.object(alpha_bot_execution, "reporting"),
            patch.object(alpha_bot_execution, "fetch_symphony_stats"),
            patch.object(alpha_bot_execution, "fetch_alpaca_history"),
            patch.object(alpha_bot_execution, "fetch_intraday_vwaps"),
            patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET),
            patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]),
            patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test"),
            patch.object(alpha_bot_execution, "ALPACA_KEY", "test"),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
            patch.object(alpha_bot_execution.time, "sleep"),
            patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]),
        ]
        self._started = [p.start() for p in patches]
        self._patches = patches

        mock_db, mock_reporting, mock_fetch_sym, mock_fetch_hist, mock_fetch_vwap, *_ = (
            self._started
        )

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state()
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"),
            "symphonies": {},
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [_make_symphony_payload()]
        mock_fetch_hist.return_value = _make_minimal_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {_TICKER: {"vwap": 500.0, "last_price": 500.0}}

        self.env = {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }
        return self.env

    def __exit__(self, exc_type, exc, tb):
        import os

        for p in self._patches:
            p.stop()
        if self._prev_flag is None:
            os.environ.pop("SECOND_WINDOW_CVAR_ENABLED", None)
        else:
            os.environ["SECOND_WINDOW_CVAR_ENABLED"] = self._prev_flag


# ---------------------------------------------------------------------------
# Flag OFF — long-window columns stay None
# ---------------------------------------------------------------------------


class TestSecondWindowFlagOff:
    """When SECOND_WINDOW_CVAR_ENABLED is unset or "0", the per-cycle write
    leaves the long-window columns as None.
    """

    @pytest.mark.parametrize("flag_value", [None, "0"])
    def test_long_window_columns_are_none_when_flag_off(self, patched_env_factory, flag_value):
        insufficient = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason="x",
        )
        with (
            patched_env_factory(flag_value) as env,
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_portfolio_cvar",
                return_value=insufficient,
            ),
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        for call in record_calls:
            long_cvar = call.kwargs.get("cvar_5pct_long")
            long_tail = call.kwargs.get("cvar_n_tail_long")
            assert long_cvar is None, (
                f"cvar_5pct_long must be None when SECOND_WINDOW_CVAR_ENABLED is "
                f"{flag_value!r}; got {long_cvar!r}. The long-window cell stays "
                "empty so the Divergence Explainer NOT_APPLICABLE row remains "
                "the source of truth (advisors/divergence_explainer.py:99-109)."
            )
            assert long_tail is None, (
                f"cvar_n_tail_long must be None when SECOND_WINDOW_CVAR_ENABLED is "
                f"{flag_value!r}; got {long_tail!r}."
            )


# ---------------------------------------------------------------------------
# Flag ON — long-window columns populated; short-window result not re-used
# ---------------------------------------------------------------------------


class TestSecondWindowFlagOn:
    """When SECOND_WINDOW_CVAR_ENABLED='1', the per-cycle write populates
    cvar_5pct_long + cvar_n_tail_long. The long-window value MUST come from
    an independent compute_portfolio_cvar call — never copied from the
    short-window result.
    """

    def test_long_window_columns_populated_when_flag_on(self, patched_env_factory):
        # Two distinct CVaRAssessment values so the test can prove
        # short ≠ long (long-window must NOT be a copy of short-window).
        # stderr values are arbitrary positive finites — not asserted here;
        # the pairing rule (finite cvar_pct REQUIRES finite stderr) is what
        # the constructor enforces.
        short = math_engine.CVaRAssessment(
            cvar_pct=-1.5,
            breach=False,
            tail_obs_count=8,
            stderr=0.001,
            insufficient_reason=None,
        )
        long_ = math_engine.CVaRAssessment(
            cvar_pct=-3.25,
            breach=False,
            tail_obs_count=12,
            stderr=0.002,
            insufficient_reason=None,
        )
        # Return distinct values by call count.
        side_effects = [short, long_, short, long_, short, long_]  # generous

        with (
            patched_env_factory("1") as env,
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_portfolio_cvar",
                side_effect=side_effects,
            ) as mock_cvar,
        ):
            alpha_bot_execution.main()

        # The math function must be called at least twice when the flag is on
        # (once for short, once for long).
        assert mock_cvar.call_count >= 2, (
            f"compute_portfolio_cvar must be called at least twice per symphony "
            f"when SECOND_WINDOW_CVAR_ENABLED='1' (once for the short window, "
            f"once for the long window); was called {mock_cvar.call_count} time(s). "
            "Re-using the short-window result as the long-window value would "
            "violate the second-window contract (two independent windows, each "
            "rendered under its own four-part S-3 contract)."
        )

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        # At least one call must carry a non-None long-window value AND
        # the long-window value must NOT equal the short-window value
        # (that would be a copy, not an independent compute).
        saw_populated = False
        for call in record_calls:
            short_v = call.kwargs.get("cvar_5pct")
            long_v = call.kwargs.get("cvar_5pct_long")
            if long_v is not None:
                saw_populated = True
                if short_v is not None:
                    assert short_v != long_v, (
                        f"cvar_5pct_long must be independently computed, not copied "
                        f"from cvar_5pct (both = {short_v}). The two windows are "
                        "two independent CVaR estimates; equality at the write site "
                        "is a category error."
                    )
        assert saw_populated, (
            "cvar_5pct_long is None on every per-cycle write even though "
            "SECOND_WINDOW_CVAR_ENABLED='1'. Long-window must be populated when "
            "the flag is on."
        )


# ---------------------------------------------------------------------------
# Tripwire — no signed divergence is computed or persisted, ever.
# ---------------------------------------------------------------------------


class TestNoSignedDivergenceEverComputed:
    """Hard wall, BINDING: no arithmetic divergence between the two windows
    is computed or persisted at the engine write site, regardless of flag.
    """

    def test_record_cvar_diagnostic_kwargs_never_carry_a_divergence_key(self, patched_env_factory):
        short = math_engine.CVaRAssessment(
            cvar_pct=-1.0,
            breach=False,
            tail_obs_count=5,
            stderr=0.001,
            insufficient_reason=None,
        )
        long_ = math_engine.CVaRAssessment(
            cvar_pct=-2.5,
            breach=False,
            tail_obs_count=7,
            stderr=0.002,
            insufficient_reason=None,
        )
        with (
            patched_env_factory("1") as env,
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_portfolio_cvar",
                side_effect=[short, long_, short, long_],
            ),
        ):
            alpha_bot_execution.main()

        forbidden_keys = {
            "divergence",
            "signed_divergence",
            "cvar_divergence",
            "cvar_diff",
            "cvar_delta",
            "window_divergence",
            "divergence_pct",
            "delta",
            "spread",
            "gap",
            "difference",
            "diff",
        }
        for call in env["db"].record_cvar_diagnostic.call_args_list:
            for key in call.kwargs.keys():
                assert key.lower() not in forbidden_keys, (
                    f"record_cvar_diagnostic was called with forbidden divergence "
                    f"kwarg {key!r}. The CVaR-divergence detector is permanently "
                    "REJECTED (project memory [[project_cvar_divergence_validation_wall]]; "
                    "DECISIONS.md §DE-S3-005). No signed divergence number is ever "
                    "computed at the engine write site."
                )
