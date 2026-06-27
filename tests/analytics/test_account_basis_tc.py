"""
RED tests for analytics.get_portfolio_today_change_account_basis (today-change B-2 fix).

BUG (operator-reported, PM-verified on live droplet 2026-06-26):
The dashboard hero "Today's Change" bot-vs-held shows phantom divergence even when no
guard has fired. Root cause: app.py _compute_portfolio_strip sets

    today_change = {
        "if_held": _cached_tc,                    # account basis (portfolio_tc, cash-INCLUSIVE)
        "dry_run": analytics.get_portfolio_today_change(...).get("dry_run"),  # VW (cash-EXCLUDED)
    }

Different denominators → phantom alpha from arithmetic, not guard events. With 11
untriggered symphonies, the hero showed bot +0.54 vs held +0.46 (false divergence of 0.08pp).

FIX: mirror the cumulative-return B-1 fix with a new helper
`get_portfolio_today_change_account_basis` that scales the VW guard divergence by
`invested_frac = symphony_value_sum / account_value` before applying it to the
account-level Held return. When no guard has fired, guard_delta_vw == 0 and the
account-basis dry_run == account_if_held_tc (no phantom alpha).

Fixture provenance:
    tests/fixtures/math/today_change_account_basis_basic.json
    Inputs derived from captured Composer fixtures (2026-06-04).
    Expected values are DERIVED from inputs using the formula — never hardcoded
    from producer-computed outputs.

No live API calls; no pytest.mark.live.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

import analytics

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "math" / "today_change_account_basis_basic.json"
)
_ACCT_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "composer" / "account-basis" / "total_stats.json"
)
_SYM_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "composer" / "v3-audit" / "symphony_stats_meta.json"
)


@pytest.fixture(scope="module")
def basis_fixture() -> dict:
    """Golden-fixture inputs for today_change_account_basis tests."""
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def total_stats_fixture() -> dict:
    """Captured Composer total-stats response (account-level, cash-inclusive)."""
    return json.loads(_ACCT_FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def symphony_stats_fixture() -> list[dict]:
    """Captured Composer per-symphony stats."""
    data = json.loads(_SYM_FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["symphonies"]


# ---------------------------------------------------------------------------
# Helpers: derive constants from fixtures — never assert hardcoded producer values
# ---------------------------------------------------------------------------


def _invested_frac(fix: dict) -> float:
    """Fraction of account value deployed in symphonies (< 1.0 when cash present)."""
    return fix["symphony_value_sum"] / fix["account_value"]


def _expected_dry_run_account(
    account_if_held_tc: float,
    guard_delta_vw: float,
    invested_frac: float,
) -> float:
    """Formula: dry_run_account = account_if_held_tc + guard_delta_vw * invested_frac."""
    return account_if_held_tc + guard_delta_vw * invested_frac


# ---------------------------------------------------------------------------
# TestAccountBasisTCMath — unit tests for the new helper function
# ---------------------------------------------------------------------------


class TestAccountBasisTCMath:
    """
    Unit tests for analytics.get_portfolio_today_change_account_basis.

    These tests are RED until the function is implemented in analytics.py.
    The formula mirrors get_portfolio_cumulative_return_account_basis (B-1 fix).
    """

    def test_untriggered_portfolio_no_phantom_alpha(self, basis_fixture: dict) -> None:
        """
        Zero guard divergence (vw_tc.dry_run == vw_tc.if_held) must produce
        result.dry_run == account_if_held_tc.

        This is the invariant that fixes the operator-reported bug: all 11 symphonies
        untriggered → bot should equal held exactly on account basis.
        """
        fix = basis_fixture
        scenario = fix["scenario_untriggered"]
        vw_tc = scenario["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]
        account_value = fix["account_value"]
        symphony_value_sum = fix["symphony_value_sum"]

        # Precondition: fixture encodes zero guard divergence
        guard_delta_vw = vw_tc["dry_run"] - vw_tc["if_held"]
        assert guard_delta_vw == pytest.approx(0.0, abs=1e-9), (
            f"Scenario precondition: vw_tc must be untriggered (dry_run == if_held); "
            f"guard_delta_vw={guard_delta_vw}"
        )

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        # When guard_delta_vw == 0, dry_run_account == account_if_held_tc (no phantom alpha)
        expected_dry_run = account_if_held_tc  # derived from fixture, not hardcoded
        assert result["dry_run"] == pytest.approx(expected_dry_run, rel=1e-9), (
            f"With zero guard divergence (untriggered), result.dry_run must equal "
            f"account_if_held_tc={account_if_held_tc:.6f}. "
            f"Got {result['dry_run']}. "
            f"This is the phantom-alpha bug: dry_run was on VW basis, if_held on account basis."
        )

    def test_untriggered_portfolio_bot_equals_held(self, basis_fixture: dict) -> None:
        """
        Zero guard divergence: result.dry_run - result.if_held must be exactly 0.0.

        Magnitude sanity check: the phantom alpha (bot - held) from the bug was ~0.08pp
        on live data. A correct implementation must give ≈0 here.
        """
        fix = basis_fixture
        scenario = fix["scenario_untriggered"]
        vw_tc = scenario["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], fix["symphony_value_sum"]
        )

        guard_alpha_account = result["dry_run"] - result["if_held"]
        assert guard_alpha_account == pytest.approx(0.0, abs=1e-9), (
            f"Untriggered portfolio: guard_alpha (dry_run - if_held) must be 0.0, "
            f"not {guard_alpha_account:.6f}. "
            f"A non-zero value here is phantom alpha from basis mismatch."
        )

    def test_if_held_is_account_portfolio_tc(self, basis_fixture: dict) -> None:
        """
        result.if_held must always be account_if_held_tc (Composer account-level
        today-change), not the VW symphony-basis if_held.
        """
        fix = basis_fixture
        vw_tc = fix["scenario_untriggered"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], fix["symphony_value_sum"]
        )

        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"result.if_held must be account_if_held_tc={account_if_held_tc:.6f} "
            f"(Composer todays_percent_change * 100), not the VW symphony-basis value. "
            f"Got {result['if_held']}."
        )

    def test_real_guard_divergence_scales_by_invested_fraction(self, basis_fixture: dict) -> None:
        """
        With a real guard divergence (symphony exited, bot outperformed if-held on VW
        basis), the account-basis guard alpha is the VW guard delta scaled by
        invested_frac = symphony_value_sum / account_value.

        Derives the expected from fixture inputs — never hardcodes the expected dollar/pp value.
        """
        fix = basis_fixture
        scenario = fix["scenario_real_guard"]
        vw_tc = scenario["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]
        account_value = fix["account_value"]
        symphony_value_sum = fix["symphony_value_sum"]

        invested_frac = _invested_frac(fix)
        guard_delta_vw = float(vw_tc["dry_run"]) - float(vw_tc["if_held"])

        # Derive expected from fixture inputs + formula
        expected_dry_run = _expected_dry_run_account(
            account_if_held_tc, guard_delta_vw, invested_frac
        )

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        assert result["dry_run"] == pytest.approx(expected_dry_run, rel=1e-6), (
            f"With guard_delta_vw={guard_delta_vw:.4f}pp, account-basis dry_run must equal "
            f"account_if_held_tc + guard_delta_vw * invested_frac. "
            f"Expected {expected_dry_run:.6f}, got {result['dry_run']}. "
            f"invested_frac={invested_frac:.6f} (fixture: account_value={account_value}, "
            f"symphony_value_sum={symphony_value_sum})"
        )

    def test_cash_attenuates_guard_alpha_vs_vw_basis(self, basis_fixture: dict) -> None:
        """
        When uninvested cash is present (invested_frac < 1.0), the account-basis guard
        alpha (result.dry_run - result.if_held) must be strictly less than the VW guard
        delta (vw_tc.dry_run - vw_tc.if_held).

        Rationale: the guard effect occurred on invested capital only. Expressing it on
        the full account basis (including cash) attenuates it by invested_frac.
        """
        fix = basis_fixture
        vw_tc = fix["scenario_real_guard"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        invested_frac = _invested_frac(fix)
        # Precondition: fixture has uninvested cash
        assert invested_frac < 1.0, (
            f"Test precondition: fixture must have uninvested cash (invested_frac < 1.0); "
            f"got {invested_frac}. Check the fixture values."
        )

        guard_delta_vw = float(vw_tc["dry_run"]) - float(vw_tc["if_held"])
        assert guard_delta_vw > 0, (
            f"Scenario precondition: real_guard scenario requires guard_delta_vw > 0; "
            f"got {guard_delta_vw}"
        )

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], fix["symphony_value_sum"]
        )

        guard_alpha_account = result["dry_run"] - result["if_held"]
        assert guard_alpha_account < guard_delta_vw, (
            f"Account-basis guard alpha ({guard_alpha_account:.6f}pp) must be strictly less "
            f"than VW guard delta ({guard_delta_vw:.6f}pp) when uninvested cash is present "
            f"(invested_frac={invested_frac:.6f})."
        )

    def test_result_dry_run_is_finite(self, basis_fixture: dict) -> None:
        """
        result.dry_run must be a finite float (not inf, nan, or None) for normal inputs.
        """
        fix = basis_fixture
        vw_tc = fix["scenario_real_guard"]["vw_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, fix["portfolio_tc"], fix["account_value"], fix["symphony_value_sum"]
        )

        assert result["dry_run"] is not None, "dry_run must not be None for normal inputs."
        assert math.isfinite(result["dry_run"]), (
            f"dry_run must be a finite float; got {result['dry_run']}."
        )

    def test_meaningful_cash_guard_alpha_attenuated_proportionally(
        self, basis_fixture: dict
    ) -> None:
        """
        With 30% uninvested cash (invested_frac=0.70), the account-basis guard alpha
        must equal exactly guard_delta_vw * invested_frac — a clearly measurable 30%
        attenuation, not just a marginal one.

        The top-level fixture has invested_frac~0.998 (0.16% cash). A wrong
        implementation using invested_frac=1.0 would produce guard_delta_account=0.5
        vs correct 0.4992 — a 0.0008pp gap that is easy to miss. With invested_frac=0.70
        the gap is 0.15pp (0.5 vs 0.35) — unambiguous.

        Expected values DERIVED from scenario_meaningful_cash fixture fields — never
        hardcoded from what the producer returns.
        Tolerance abs=1e-9: single-multiply floating-point arithmetic, no accumulated
        rounding; rel tolerance would hide near-misses when result is small.
        """
        fix_s = basis_fixture["scenario_meaningful_cash"]
        vw_tc = fix_s["vw_tc"]
        account_if_held_tc = fix_s["account_if_held_tc"]
        account_value = fix_s["account_value"]
        symphony_value_sum = fix_s["symphony_value_sum"]

        # Derive from fixture inputs — not from what the function returns
        invested_frac = symphony_value_sum / account_value  # == fix_s["_invested_frac"]
        guard_delta_vw = fix_s["_guard_delta_vw"]
        expected_guard_delta_account = guard_delta_vw * invested_frac

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        guard_delta_account = float(result["dry_run"]) - float(result["if_held"])

        # Exact proportional attenuation (single-multiply, abs tolerance adequate)
        assert guard_delta_account == pytest.approx(expected_guard_delta_account, abs=1e-9), (
            f"With invested_frac={invested_frac:.4f} (30% cash), account-basis guard "
            f"delta must equal guard_delta_vw * invested_frac = "
            f"{guard_delta_vw:.4f} * {invested_frac:.4f} = {expected_guard_delta_account:.6f}. "
            f"Got {guard_delta_account:.6f}."
        )
        # Attenuation is unambiguous at 30% cash: gap is 0.15, not 0.0008
        assert guard_delta_account < guard_delta_vw, (
            f"With 30% uninvested cash, account-basis guard delta ({guard_delta_account:.6f}) "
            f"must be strictly less than VW guard delta ({guard_delta_vw:.6f}). "
            f"A bug using invested_frac=1.0 would produce guard_delta_account={guard_delta_vw:.6f}, "
            f"easily detectable with this fixture (gap={guard_delta_vw - expected_guard_delta_account:.6f})."
        )


# ---------------------------------------------------------------------------
# TestAccountBasisTCEdgeCases — guard invariants and None propagation
# ---------------------------------------------------------------------------


class TestAccountBasisTCEdgeCases:
    """
    Edge case tests: division guards, None propagation. These must never raise.
    All follow the same contract as get_portfolio_cumulative_return_account_basis.
    """

    def test_none_dry_run_returns_account_if_held_with_none_dry_run(
        self, basis_fixture: dict
    ) -> None:
        """
        vw_tc["dry_run"] is None (no shadow history today) → result must be
        {"if_held": account_if_held_tc, "dry_run": None} without raising.
        """
        fix = basis_fixture
        vw_tc = fix["scenario_none_dry_run"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        assert vw_tc["dry_run"] is None, "Fixture precondition: dry_run is None"

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], fix["symphony_value_sum"]
        )

        assert result["dry_run"] is None, (
            "dry_run=None must propagate as None (no shadow history today)."
        )
        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            "if_held must be account_if_held_tc even when dry_run=None."
        )

    def test_zero_account_value_returns_account_if_held_basis(self, basis_fixture: dict) -> None:
        """
        account_value == 0 (division guard): must return account_if_held_tc on both
        sides, not vw_tc (that would be a basis swap), not raise.

        Contract: {"if_held": account_if_held_tc, "dry_run": account_if_held_tc}
        No account value -> no guard-effect scaling is meaningful; report the
        account-level Held return for both sides (bot==held, no phantom alpha).
        """
        fix = basis_fixture
        vw_tc = fix["scenario_untriggered"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, 0.0, fix["symphony_value_sum"]
        )

        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With account_value=0, result.if_held must equal account_if_held_tc="
            f"{account_if_held_tc:.6f}, not vw_tc['if_held']={vw_tc['if_held']:.6f} "
            f"(that is a basis swap). Got {result.get('if_held')}."
        )
        assert result["dry_run"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With account_value=0, result.dry_run must equal account_if_held_tc="
            f"{account_if_held_tc:.6f} (bot==held, no phantom divergence). "
            f"Got {result.get('dry_run')}."
        )

    def test_negative_account_value_returns_account_if_held_basis(self, basis_fixture: dict) -> None:
        """
        Negative account_value is nonsensical (division guard): must return
        account_if_held_tc on both sides, not vw_tc (basis swap), not raise.
        """
        fix = basis_fixture
        vw_tc = fix["scenario_untriggered"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, -500.0, fix["symphony_value_sum"]
        )

        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With account_value<0, result.if_held must equal account_if_held_tc="
            f"{account_if_held_tc:.6f}. Got {result.get('if_held')}."
        )
        assert result["dry_run"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With account_value<0, result.dry_run must equal account_if_held_tc="
            f"{account_if_held_tc:.6f} (no guard effect). Got {result.get('dry_run')}."
        )

    def test_zero_symphony_value_sum_returns_account_if_held_basis(self, basis_fixture: dict) -> None:
        """
        symphony_value_sum == 0 (all-cash / no invested positions): no guard effect
        is possible (invested_frac=0). Must return account_if_held_tc on both sides,
        not vw_tc (which is defined over empty positions, not account-level), not raise.

        Contract: {"if_held": account_if_held_tc, "dry_run": account_if_held_tc}
        """
        fix = basis_fixture
        vw_tc = fix["scenario_untriggered"]["vw_tc"]
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], 0.0
        )

        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With symphony_value_sum=0, result.if_held must equal account_if_held_tc="
            f"{account_if_held_tc:.6f} (account-level Held basis), not vw_tc['if_held']="
            f"{vw_tc['if_held']:.6f} (VW basis swap). Got {result.get('if_held')}."
        )
        assert result["dry_run"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            f"With symphony_value_sum=0, result.dry_run must equal account_if_held_tc="
            f"{account_if_held_tc:.6f} (bot==held, no phantom divergence). "
            f"Got {result.get('dry_run')}."
        )

    def test_none_vw_if_held_returns_account_if_held_with_none_dry_run(
        self, basis_fixture: dict
    ) -> None:
        """
        vw_tc["if_held"] is None (missing data) → guard delta undefined.
        Must return {"if_held": account_if_held_tc, "dry_run": None} without raising.
        """
        fix = basis_fixture
        vw_tc = {"if_held": None, "dry_run": -2.0}
        account_if_held_tc = fix["portfolio_tc"]

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, fix["account_value"], fix["symphony_value_sum"]
        )

        assert result["dry_run"] is None, (
            "dry_run must be None when vw_tc['if_held'] is None (can't compute guard delta)."
        )
        assert result["if_held"] == pytest.approx(account_if_held_tc, rel=1e-9), (
            "if_held must be account_if_held_tc even when vw if_held is None."
        )

    def test_all_cash_portfolio_guard_stays_on_account_basis(self) -> None:
        """
        All-cash portfolio (symphony_value_sum=0, vw_tc.if_held=0.0): the VW if_held
        is 0.0 (no invested capital changed), but the account-level Held may be non-zero
        (e.g. cash in a money-market accruing interest). Returning vw_tc would report
        if_held=0.0 — a basis swap that hides real account-level movement.

        Contract: {"if_held": account_if_held_tc, "dry_run": account_if_held_tc}

        This is the DE-TODAY-BASIS-001 scenario: all-cash / unpopulated symphonies,
        warm cache returns account_if_held_tc=0.46 (real account-level change) but
        vw_tc.if_held=0.0 (empty-position VW basis). The difference is not guard alpha
        — it is a pure basis artefact that must NOT appear as phantom divergence.
        """
        # Inline values — boundary scenario not representable by the shared fixture
        account_if_held_tc = 0.46  # real account-level change from warm cache
        vw_tc = {"if_held": 0.0, "dry_run": 0.0}  # all-cash VW: no invested-capital change
        account_value = 12893.7
        symphony_value_sum = 0.0  # no invested positions

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        assert result["if_held"] == pytest.approx(account_if_held_tc, abs=1e-9), (
            f"All-cash portfolio: result.if_held must equal account_if_held_tc="
            f"{account_if_held_tc} (account basis), not vw_tc['if_held']=0.0 "
            f"(VW basis swap). Got {result.get('if_held')}."
        )
        assert result["dry_run"] == pytest.approx(account_if_held_tc, abs=1e-9), (
            f"All-cash portfolio: no guard position exists; result.dry_run must equal "
            f"account_if_held_tc={account_if_held_tc} (bot==held, no phantom divergence). "
            f"Got {result.get('dry_run')}."
        )

    def test_over_invested_snapshot_does_not_amplify_guard_delta(self) -> None:
        """
        Stale snapshot where symphony_value_sum > account_value produces an unclamped
        invested_frac > 1.0. Without a clamp, the account-basis guard delta EXCEEDS the
        VW guard delta — contradicting the cash-attenuation contract (cash should dilute,
        never amplify, the guard effect).

        Contract: invested_frac is clamped to min(invested_frac, 1.0).
        Assertion: result["dry_run"] - result["if_held"] <= guard_delta_vw

        Without clamp: invested_frac=1.25, guard_delta_account=0.625 > guard_delta_vw=0.5.
        With clamp:    invested_frac=1.00, guard_delta_account=0.500 <= guard_delta_vw=0.5.
        """
        account_value = 12000.0
        symphony_value_sum = 15000.0  # stale snapshot: sv_sum > acct_val
        account_if_held_tc = 0.46
        vw_tc = {"if_held": -1.0, "dry_run": -0.5}

        # Derive guard_delta_vw from inputs — do not hardcode 0.5
        guard_delta_vw = float(vw_tc["dry_run"]) - float(vw_tc["if_held"])

        # Preconditions: stale snapshot scenario
        assert symphony_value_sum > account_value, (
            "Precondition: this test requires a stale snapshot (symphony_value_sum > account_value)"
        )
        assert guard_delta_vw > 0, "Precondition: positive guard delta is required."

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        guard_delta_account = float(result["dry_run"]) - float(result["if_held"])
        assert guard_delta_account <= guard_delta_vw, (
            f"Stale-snapshot amplification: account-basis guard delta ({guard_delta_account:.6f}) "
            f"must not exceed VW guard delta ({guard_delta_vw:.6f}). "
            f"Unclamped invested_frac={symphony_value_sum / account_value:.4f} amplifies the "
            f"guard effect, violating the cash-attenuation contract. "
            f"Fix: clamp invested_frac to min(symphony_value_sum / account_value, 1.0)."
        )

    def test_none_account_if_held_tc_returns_none_pair(self) -> None:
        """
        account_if_held_tc=None (warm cache miss — Composer account-level today-change
        not yet available). Must return {"if_held": None, "dry_run": None} without
        raising TypeError.

        The formula path would reach `None + guard_delta_vw * invested_frac`, causing
        TypeError if account_if_held_tc is not guarded before the computation.
        This mirrors the existing vw_tc None-guards: when any required input is None,
        propagate None cleanly rather than raise.
        """
        account_if_held_tc = None  # warm cache miss
        vw_tc = {"if_held": -1.0, "dry_run": -0.5}  # finite VW — not the trigger
        account_value = 12000.0
        symphony_value_sum = 10000.0

        # Must not raise TypeError
        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc, account_value, symphony_value_sum
        )

        assert result["if_held"] is None, (
            "account_if_held_tc=None must propagate: result.if_held must be None."
        )
        assert result["dry_run"] is None, (
            "account_if_held_tc=None must propagate: result.dry_run must be None "
            "(formula path `None + float * invested_frac` raises TypeError without this guard)."
        )


# ---------------------------------------------------------------------------
# TestComputePortfolioStripTodayChange — integration: _compute_portfolio_strip
# ---------------------------------------------------------------------------


class TestComputePortfolioStripTodayChange:
    """
    Integration tests: _compute_portfolio_strip must use
    get_portfolio_today_change_account_basis when portfolio_tc is cached.

    These tests are RED against the current (buggy) code which sets:
        today_change = {
            "if_held": _cached_tc,          # account basis
            "dry_run": vw_call.get("dry_run")  # VW basis — different denominator
        }

    After the fix they become GREEN by wiring the new helper.
    """

    def test_strip_today_change_no_phantom_alpha_when_untriggered(
        self, basis_fixture: dict, symphony_stats_fixture: list[dict]
    ) -> None:
        """
        With an untriggered portfolio (no guard has fired), today_change.dry_run must
        equal today_change.if_held on the account basis.

        Setup:
        - _account_totals_cache["portfolio_tc"] = portfolio_tc (account-level, cash-inclusive)
        - _account_totals_cache["portfolio_value"] = account_value (has uninvested cash)
        - Mock analytics.get_portfolio_today_change to return zero VW guard divergence
          (untriggered: dry_run == if_held on VW basis, but DIFFERENT from portfolio_tc
          because cash dilutes the account denominator)
        - Expect today_change["dry_run"] == today_change["if_held"] after fix

        With the BUG: dry_run = vw_dry_run (different from portfolio_tc due to cash) →
            today_change["dry_run"] ≠ today_change["if_held"] (phantom alpha)
        After the FIX: dry_run scaled to account basis → guard_delta_vw=0 →
            dry_run_account = portfolio_tc → today_change["dry_run"] == today_change["if_held"]
        """
        import app as app_module

        fix = basis_fixture
        account_value = fix["account_value"]
        portfolio_tc = fix["portfolio_tc"]  # Composer account-level today-change

        # The VW today-change is numerically DIFFERENT from portfolio_tc due to cash
        # (same dollar change / smaller denominator = larger %-change in magnitude).
        # Using the fixture's VW value from the untriggered scenario.
        vw_if_held = fix["scenario_untriggered"]["vw_tc"]["if_held"]
        # Untriggered: VW dry_run == VW if_held (no guard has fired)
        mocked_vw_tc = {"if_held": vw_if_held, "dry_run": vw_if_held}

        # Confirm precondition: VW value differs from account-level value (cash present)
        assert abs(vw_if_held - portfolio_tc) > 1e-6, (
            f"Test precondition: VW if_held ({vw_if_held}) must differ from portfolio_tc "
            f"({portfolio_tc}) to expose the basis mismatch. If they are identical the bug "
            f"does not manifest. Check fixture values."
        )

        # Build bot_state from symphony fixture (values provide symphony_value_sum)
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

        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = account_value
        app_module._account_totals_cache["portfolio_tc"] = portfolio_tc
        # Do NOT set portfolio_cr — leaves cumulative on VW-only path to isolate today_change

        try:
            with patch(
                "analytics.get_portfolio_today_change",
                return_value=mocked_vw_tc,
            ):
                result = app_module._compute_portfolio_strip(bot_state)
        finally:
            app_module._account_totals_cache.clear()

        tc = result.get("today_change")
        assert tc is not None, "today_change must not be None with populated portfolio_tc cache"

        # Guard invariant: untriggered → bot must equal held (no phantom alpha)
        assert tc.get("if_held") == pytest.approx(portfolio_tc, rel=1e-6), (
            f"today_change.if_held must equal cached portfolio_tc={portfolio_tc:.6f}; "
            f"got {tc.get('if_held')}."
        )
        assert tc.get("dry_run") is not None, (
            "today_change.dry_run must not be None when VW dry_run is available."
        )
        assert tc["dry_run"] == pytest.approx(portfolio_tc, rel=1e-4), (
            f"Untriggered portfolio: today_change.dry_run={tc['dry_run']:.6f} must equal "
            f"today_change.if_held={portfolio_tc:.6f} (both on account basis). "
            f"A non-zero bot-held delta is phantom alpha from the cash-denominator mismatch. "
            f"This is the operator-reported bug: bot +0.54 vs held +0.46 with 11 untriggered symphonies."
        )

    def test_strip_today_change_if_held_is_cached_portfolio_tc(
        self, basis_fixture: dict, symphony_stats_fixture: list[dict]
    ) -> None:
        """
        today_change.if_held must always be the cached portfolio_tc
        (Composer account-level todays_percent_change * 100).
        """
        import app as app_module

        fix = basis_fixture
        portfolio_tc = fix["portfolio_tc"]
        vw_if_held = fix["scenario_untriggered"]["vw_tc"]["if_held"]
        mocked_vw_tc = {"if_held": vw_if_held, "dry_run": vw_if_held}

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

        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = fix["account_value"]
        app_module._account_totals_cache["portfolio_tc"] = portfolio_tc

        try:
            with patch("analytics.get_portfolio_today_change", return_value=mocked_vw_tc):
                result = app_module._compute_portfolio_strip(bot_state)
        finally:
            app_module._account_totals_cache.clear()

        tc = result.get("today_change")
        assert tc is not None
        assert tc["if_held"] == pytest.approx(portfolio_tc, rel=1e-6), (
            f"today_change.if_held must equal cached portfolio_tc={portfolio_tc:.6f}; "
            f"got {tc['if_held']}."
        )


# ---------------------------------------------------------------------------
# TestCumulativeReturnConsistency — AC-9 regression guard
# ---------------------------------------------------------------------------


class TestCumulativeReturnConsistency:
    """
    AC-9: The existing cumulative B-1 fix (get_portfolio_cumulative_return_account_basis)
    must still yield dry_run == if_held when untriggered (zero VW guard divergence).

    If this test fails it means the B-1 fix itself has regressed — flag immediately,
    do NOT silently carry a pre-existing failure.
    """

    def test_cumulative_return_no_phantom_alpha_when_untriggered(
        self, total_stats_fixture: dict, symphony_stats_fixture: list[dict]
    ) -> None:
        """
        With VW CR dry_run == if_held (zero guard divergence), account-basis dry_run
        must equal account_if_held (Composer simple_return * 100).

        Regression guard on the B-1 fix already in production.
        """
        acct_val = float(total_stats_fixture["portfolio_value"])
        acct_cr = float(total_stats_fixture["simple_return"]) * 100.0
        sym_sum = sum(float(s.get("value") or 0.0) for s in symphony_stats_fixture)

        # Untriggered: VW dry_run == VW if_held (zero guard delta on VW basis)
        vw_if_held = 68.5  # hypothetical VW value; differs from acct_cr due to cash
        vw_cr_no_divergence = {"if_held": vw_if_held, "dry_run": vw_if_held}

        result = analytics.get_portfolio_cumulative_return_account_basis(
            vw_cr_no_divergence, acct_cr, acct_val, sym_sum
        )

        assert result["dry_run"] == pytest.approx(acct_cr, rel=1e-9), (
            f"B-1 regression: with zero VW guard divergence, account-basis dry_run must equal "
            f"account_if_held={acct_cr:.4f}. Got {result['dry_run']}. "
            f"This indicates the B-1 cumulative fix has regressed — do NOT carry this."
        )
        assert result["dry_run"] - result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"B-1 regression: guard_alpha must be 0.0 for untriggered portfolio. "
            f"Got {result['dry_run'] - result['if_held']}."
        )
