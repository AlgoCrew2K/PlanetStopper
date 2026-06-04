"""
Tests for analytics.get_portfolio_cumulative_return_account_basis (B-1 fix).

B-1 audit finding: "Bot" (dry_run) uses VW per-symphony denominator (cash excluded)
while "Held" (if_held) uses Composer account simple_return (cash-inclusive).
Subtracting them is a scope artefact, not guard alpha.

Fix: scale the guard delta by (symphony_value_sum / account_value) so both Bot
and Held share the account-value denominator.

Fixture provenance: tests/fixtures/composer/account-basis/total_stats.json
  Derived from tests/fixtures/composer/v3-audit/total_stats.json
  (captured-from-producer 2026-06-04).

No live API calls.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import analytics


_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "composer" / "account-basis"
_V3_AUDIT_DIR = Path(__file__).parent.parent / "fixtures" / "composer" / "v3-audit"


@pytest.fixture(scope="module")
def total_stats_fixture() -> dict:
    """Captured Composer total-stats response (account-level, cash-inclusive)."""
    return json.loads((_FIXTURE_DIR / "total_stats.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def symphony_stats_fixture() -> list[dict]:
    """Captured Composer per-symphony stats (cash excluded from values)."""
    data = json.loads((_V3_AUDIT_DIR / "symphony_stats_meta.json").read_text(encoding="utf-8"))
    return data["symphonies"]


# ---------------------------------------------------------------------------
# Helper: compute fixture-derived constants
# ---------------------------------------------------------------------------


def _account_value(ts: dict) -> float:
    return float(ts["portfolio_value"])


def _if_held_account(ts: dict) -> float:
    return float(ts["simple_return"]) * 100.0


def _symphony_value_sum(syms: list[dict]) -> float:
    return sum(float(s.get("value") or 0.0) for s in syms)


def _invested_frac(ts: dict, syms: list[dict]) -> float:
    return _symphony_value_sum(syms) / _account_value(ts)


# ---------------------------------------------------------------------------
# TestAccountBasisMath — the core formula
# ---------------------------------------------------------------------------


class TestAccountBasisMath:
    def test_guard_delta_scaled_by_invested_fraction(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        guard_delta_account = guard_delta_vw * invested_frac

        A guard effect measured on VW symphony basis must be scaled down by the
        invested fraction (symphony_values / account_value) to be comparable to
        the account-level "Held" return.

        With 99.84% invested, a 2.20pp guard on VW basis becomes ~2.196pp
        on account basis — a 0.004pp difference that proves the scaling is applied.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        invested_frac = sym_sum / acct_val
        if_held_acct = _if_held_account(total_stats_fixture)

        # Hypothetical VW CR: a 2.20pp guard delta on VW (symphony) basis.
        # vw_cr uses the VW if_held (symphony denominator) for the guard delta.
        vw_if_held = 68.0  # hypothetical VW if_held (symphony basis)
        guard_delta_vw = 2.20  # pp, VW symphony-basis
        vw_cr = {
            "if_held": vw_if_held,
            "dry_run": vw_if_held + guard_delta_vw,
        }

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        expected_guard_account = guard_delta_vw * invested_frac
        expected_dry_run = if_held_acct + expected_guard_account

        assert result["dry_run"] == pytest.approx(expected_dry_run, rel=1e-6), (
            f"dry_run on account basis must equal account_if_held + guard_delta_vw * invested_frac; "
            f"expected {expected_dry_run:.6f}, got {result['dry_run']}. "
            f"invested_frac={invested_frac:.6f}, guard_delta_vw={guard_delta_vw}"
        )

    def test_if_held_is_account_basis(self, total_stats_fixture, symphony_stats_fixture):
        """
        if_held in the result must be the account_if_held argument (Composer
        simple_return * 100), not the VW symphony-basis if_held.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)

        vw_cr = {"if_held": 68.0, "dry_run": 70.0}  # VW basis
        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        assert result["if_held"] == pytest.approx(if_held_acct, rel=1e-9), (
            "if_held in result must equal account_if_held (Composer simple_return * 100); "
            f"Expected {if_held_acct}, got {result['if_held']}."
        )

    def test_untriggered_symphony_zero_guard_alpha(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        When no guard has fired (dry_run == if_held on VW symphony basis), the
        account-basis result must also have dry_run == account_if_held (zero phantom alpha).

        This is the untriggered invariant: guard_delta_vw = 0 → no phantom alpha
        regardless of the invested fraction and the cash spread.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)

        # VW basis: zero guard delta (dry_run == if_held on VW basis)
        vw_if_held = 68.5  # hypothetical VW if_held (differs from account by cash spread)
        vw_cr = {"if_held": vw_if_held, "dry_run": vw_if_held}
        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        # Account-basis: dry_run must equal account if_held (zero guard alpha)
        assert result["dry_run"] == pytest.approx(if_held_acct, rel=1e-9), (
            "When guard_delta_vw == 0 (untriggered), account-basis dry_run must equal "
            "account_if_held (no phantom alpha). "
            f"Got dry_run={result['dry_run']}, expected account_if_held={if_held_acct}."
        )
        assert result["dry_run"] - result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            "guard_alpha (dry_run - if_held) must be exactly 0.0 for untriggered symphonies."
        )

    def test_scaling_is_strictly_less_than_unscaled_when_cash_present(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        When cash > 0, the invested fraction < 1.0, so the account-basis guard
        delta must be strictly less than the VW symphony-basis guard delta.

        Confirms the scaling is not a no-op when the account holds uninvested cash.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        invested_frac = sym_sum / acct_val

        # The v3-audit fixture has 20.97 unallocated cash → invested_frac < 1.0
        assert invested_frac < 1.0, (
            f"Test precondition: fixture must have cash (invested_frac < 1.0); "
            f"got {invested_frac}. Check the account-basis fixture."
        )

        if_held_acct = _if_held_account(total_stats_fixture)
        vw_if_held = 68.5
        guard_delta_vw = 2.20
        vw_cr = {"if_held": vw_if_held, "dry_run": vw_if_held + guard_delta_vw}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        guard_alpha_account = result["dry_run"] - result["if_held"]
        assert guard_alpha_account < guard_delta_vw, (
            f"Account-basis guard alpha ({guard_alpha_account:.6f}) must be strictly less "
            f"than VW symphony-basis guard delta ({guard_delta_vw}) when uninvested cash "
            f"is present (invested_frac={invested_frac:.6f})."
        )


# ---------------------------------------------------------------------------
# TestAccountBasisEdgeCases — guard invariants and None propagation
# ---------------------------------------------------------------------------


class TestAccountBasisEdgeCases:
    def test_none_dry_run_propagated_as_none(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        When vw_cr["dry_run"] is None (no shadow history), the function must
        return {"if_held": account_if_held, "dry_run": None} without crashing.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)

        vw_cr = {"if_held": 68.5, "dry_run": None}
        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        assert result["dry_run"] is None, (
            "dry_run=None must propagate as None when there is no shadow history."
        )
        assert result["if_held"] == pytest.approx(if_held_acct, rel=1e-9), (
            "if_held must be account_if_held even when dry_run=None."
        )

    def test_zero_account_value_returns_vw_cr_unchanged(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        When account_value <= 0 (division guard), the function must return
        vw_cr unchanged rather than raise ZeroDivisionError.
        """
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)
        vw_cr = {"if_held": 68.0, "dry_run": 70.0}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, 0.0, sym_sum
        )

        assert result == vw_cr, (
            "With account_value=0, the function must return vw_cr unchanged (division guard)."
        )

    def test_negative_account_value_returns_vw_cr_unchanged(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        Negative account_value is nonsensical — must return vw_cr unchanged.
        """
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)
        vw_cr = {"if_held": 68.0, "dry_run": 70.0}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, -100.0, sym_sum
        )

        assert result == vw_cr, (
            "With account_value < 0, the function must return vw_cr unchanged."
        )

    def test_zero_symphony_value_sum_returns_vw_cr_unchanged(
        self, total_stats_fixture
    ):
        """
        When symphony_value_sum == 0 (no invested positions), no scaling is possible.
        Return vw_cr unchanged.
        """
        acct_val = _account_value(total_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)
        vw_cr = {"if_held": 68.0, "dry_run": 70.0}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, 0.0
        )

        assert result == vw_cr, (
            "With symphony_value_sum=0, the function must return vw_cr unchanged."
        )

    def test_none_if_held_returns_account_if_held_with_none_dry_run(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        When vw_cr["if_held"] is None (missing data sentinel), the function must
        return {"if_held": account_if_held, "dry_run": None}.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)
        vw_cr = {"if_held": None, "dry_run": 70.0}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        assert result["if_held"] == pytest.approx(if_held_acct, rel=1e-9), (
            "if_held in result must be account_if_held even when vw if_held is None."
        )
        assert result["dry_run"] is None, (
            "dry_run must be None when vw_cr if_held is None (missing-data guard)."
        )

    def test_result_is_finite(self, total_stats_fixture, symphony_stats_fixture):
        """
        The dry_run result must be a finite float — not inf or nan.
        """
        acct_val = _account_value(total_stats_fixture)
        sym_sum = _symphony_value_sum(symphony_stats_fixture)
        if_held_acct = _if_held_account(total_stats_fixture)

        vw_cr = {"if_held": 68.5, "dry_run": 70.5}
        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr, if_held_acct, acct_val, sym_sum
        )

        assert result["dry_run"] is not None
        assert math.isfinite(result["dry_run"]), (
            f"dry_run must be a finite float; got {result['dry_run']}."
        )


# ---------------------------------------------------------------------------
# TestComputePortfolioStripAccountBasis — integration: app._compute_portfolio_strip
# ---------------------------------------------------------------------------


class TestComputePortfolioStripAccountBasis:
    """
    Integration tests: _compute_portfolio_strip must use get_portfolio_cumulative_return_account_basis
    when both portfolio_cr and portfolio_value are cached.

    The guard invariant: when dry_run == if_held on symphony-level (untriggered),
    account-basis dry_run must equal account-level if_held (no phantom alpha).
    """

    def test_strip_dry_run_equals_if_held_when_no_guard_effect(
        self, total_stats_fixture, symphony_stats_fixture
    ):
        """
        With an untriggered portfolio (no shadow history → dry_run == if_held on
        symphony basis), _compute_portfolio_strip must return dry_run equal to
        the cached portfolio_cr (account-basis if_held).

        This is the most important invariant: zero guard effect → zero guard alpha
        on the account basis too.
        """
        import app as app_module
        from unittest.mock import patch, MagicMock

        acct_val = total_stats_fixture["portfolio_value"]
        acct_cr = total_stats_fixture["simple_return"] * 100.0

        # Populate cache with account-level values
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = acct_val
        app_module._account_totals_cache["portfolio_cr"] = acct_cr

        # Build bot_state from the symphony fixture — these symphonies have
        # simple_return, net_deposits, etc.
        bot_state: dict = {}
        for s in symphony_stats_fixture:
            sym_id = s.get("id") or s.get("name", "")
            bot_state[sym_id] = {
                "name": s.get("name", ""),
                "current_value": s.get("value") or 0.0,
                "current_return": (s.get("last_percent_change") or 0.0) * 100.0,
                "simple_return": s.get("simple_return"),
                "net_deposits": s.get("net_deposits"),
                "time_weighted_return": s.get("time_weighted_return"),
                "max_drawdown": s.get("max_drawdown"),
            }

        # Patch _get_shadow_divergence_trajectory to return None (no shadow history)
        # so dry_run == if_held on symphony basis.
        with patch(
            "analytics._get_shadow_divergence_trajectory",
            return_value=None,
        ):
            result = app_module._compute_portfolio_strip(bot_state)

        try:
            app_module._account_totals_cache.clear()
        except Exception:
            pass

        cr = result.get("cumulative_return")
        assert cr is not None, "cumulative_return must not be None with populated cache"

        # Guard invariant: no shadow → dry_run must equal if_held (account basis)
        assert cr.get("dry_run") is not None, (
            "dry_run must not be None when per-symphony CR is computable"
        )
        assert cr["dry_run"] == pytest.approx(acct_cr, rel=1e-4), (
            f"With no guard effect (untriggered), dry_run={cr['dry_run']:.4f} must equal "
            f"account-level if_held={acct_cr:.4f}. "
            f"Check that _compute_portfolio_strip uses get_portfolio_cumulative_return_account_basis."
        )

    def test_strip_if_held_equals_cached_portfolio_cr(self, total_stats_fixture, symphony_stats_fixture):
        """
        The cumulative_return.if_held must be the cached portfolio_cr (Composer
        simple_return * 100) regardless of per-symphony data.
        """
        import app as app_module
        from unittest.mock import patch

        acct_val = total_stats_fixture["portfolio_value"]
        acct_cr = total_stats_fixture["simple_return"] * 100.0

        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = acct_val
        app_module._account_totals_cache["portfolio_cr"] = acct_cr

        bot_state: dict = {}
        for s in symphony_stats_fixture:
            sym_id = s.get("id") or s.get("name", "")
            bot_state[sym_id] = {
                "name": s.get("name", ""),
                "current_value": s.get("value") or 0.0,
                "current_return": (s.get("last_percent_change") or 0.0) * 100.0,
                "simple_return": s.get("simple_return"),
                "net_deposits": s.get("net_deposits"),
                "time_weighted_return": s.get("time_weighted_return"),
                "max_drawdown": s.get("max_drawdown"),
            }

        with patch("analytics._get_shadow_divergence_trajectory", return_value=None):
            result = app_module._compute_portfolio_strip(bot_state)

        try:
            app_module._account_totals_cache.clear()
        except Exception:
            pass

        cr = result.get("cumulative_return")
        assert cr is not None
        assert cr["if_held"] == pytest.approx(acct_cr, rel=1e-6), (
            f"cumulative_return.if_held must equal cached portfolio_cr={acct_cr:.4f}; "
            f"got {cr['if_held']}."
        )
