"""
RED tests for EOD/frozen account-basis unification (DE-EOD-BASIS-001).

BUG: The frozen/EOD branch of /api/state (app.py:1758-1834) produces:
  - today_change: raw VW (analytics.get_portfolio_today_change) with no account-basis
    wrap.  Denominator = invested capital (cash EXCLUDED) — different from the live
    path and from Composer.
  - cumulative_return: HALF-converted — if_held = cached portfolio_cr (account basis),
    but dry_run = raw VW dry_run (cash EXCLUDED) — mixed-basis dict.

FIX: Route the frozen branch through the SAME account-basis helpers the live path uses:
  - analytics.get_portfolio_today_change_account_basis(vw_tc, portfolio_tc,
      account_value, symphony_value_sum)   — mirrors app.py:1207-1212
  - analytics.get_portfolio_cumulative_return_account_basis(vw_cr, portfolio_cr,
      account_value, symphony_value_sum)   — mirrors app.py:1183-1190

ACs covered: AC-1, AC-2, AC-3, AC-6, AC-8, AC-9.

Fixture provenance:
  tests/fixtures/dashboard/frozen_portfolio_strip/eod_account_basis_parity.json
  Schema-derived, DE-EOD-BASIS-001, 2026-07-02.
  account_value=100000, symphony_value_sum=80000 (20% cash, invested_frac=0.8).
  VW TC if_held=0.625 vs account TC if_held=0.50 — gap = 0.125pp (unambiguous).
  VW CR if_held=31.25 vs account CR if_held=25.0 — gap = 6.25pp (unambiguous).
  Expected values in all tests DERIVED from fixture inputs + formula — never hardcoded.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

import analytics as real_analytics

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "frozen_portfolio_strip"
)


def _load_parity_fixture() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "eod_account_basis_parity.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def _make_snapshot(symphony_value_sum: float = 80000.0) -> dict:
    """Minimal closed snapshot whose symphonies sum to symphony_value_sum.

    Two accounts: sym-alpha (62.5%) and sym-beta (37.5%) of symphony_value_sum.
    Matches the eod_account_basis_parity.json snapshot_input shape.
    """
    alpha_value = symphony_value_sum * 0.625
    beta_value = symphony_value_sum * 0.375
    return {
        "trading_day": "2026-07-01",
        "captured_at_et": "16:00:01 ET",
        "shadow_divergence": {"by_symphony": {}, "portfolio_today": None},
        "accounts_map": {
            "ACC-INDIVIDUAL": [
                {
                    "id": "sym-alpha",
                    "name": "Alpha Momentum",
                    "account": "ACC-INDIVIDUAL",
                    "current_return": 4.2,
                    "current_value": alpha_value,
                    "simple_return": 0.042,
                    "net_deposits": alpha_value * 0.9,
                    "time_weighted_return": 0.045,
                    "max_drawdown": 0.08,
                }
            ],
            "ACC-ROTH": [
                {
                    "id": "sym-beta",
                    "name": "Beta Defensive",
                    "account": "ACC-ROTH",
                    "current_return": 1.1,
                    "current_value": beta_value,
                    "simple_return": 0.011,
                    "net_deposits": beta_value * 0.93,
                    "time_weighted_return": 0.012,
                    "max_drawdown": 0.03,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------


def _make_db_mock(bot_state: dict) -> MagicMock:
    mock_db = MagicMock()
    mock_db.load_state.return_value = bot_state
    mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock_db.get_triggers.return_value = []
    mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
    mock_db.read_fleet_alert.return_value = None
    return mock_db


# ---------------------------------------------------------------------------
# Flask client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    """Flask test client for the frozen /api/state branch. No live DB or network."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "schedule"):
        with app_module.app.test_client() as client:
            yield client, app_module


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _setup_warm_cache(
    app_module,
    *,
    account_value: float,
    portfolio_tc: float,
    portfolio_cr: float,
) -> None:
    """Populate _account_totals_cache with known account-level totals."""
    app_module._account_totals_cache.clear()
    app_module._account_totals_cache["portfolio_value"] = account_value
    app_module._account_totals_cache["portfolio_tc"] = portfolio_tc
    app_module._account_totals_cache["portfolio_cr"] = portfolio_cr


def _clear_cache(app_module) -> None:
    app_module._account_totals_cache.clear()


# ---------------------------------------------------------------------------
# Route driver helper
# ---------------------------------------------------------------------------


def _drive_frozen_branch(
    client,
    app_module,
    monkeypatch,
    *,
    bot_state: dict,
    vw_tc: dict,
    vw_cr: dict,
    market_state: str = "closed_frozen",
) -> dict:
    """Drive /api/state with the frozen branch and return the parsed response body.

    Mocks the VW analytics functions so they return controlled fixture values.
    Lets the account-basis helpers run for real (via side_effect) so the tests
    observe the real helper output, not a MagicMock.
    """
    with (
        patch.object(app_module, "database", _make_db_mock(bot_state)),
        patch.object(app_module, "analytics") as mock_a,
    ):
        # VW (intermediate) analytics — return fixture-controlled values
        mock_a.get_portfolio_today_change.return_value = vw_tc
        mock_a.get_portfolio_cumulative_return.return_value = vw_cr
        mock_a.get_portfolio_max_drawdown.return_value = {"if_held": -5.0, "dry_run": -5.0}
        mock_a.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_a.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
        mock_a.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        # Shadow/vol helpers: return None so the None-guarded unpack blocks in both
        # _compute_portfolio_strip (app.py:1264-1265) and get_state (app.py:1503-1504)
        # are skipped. A non-None MagicMock causes `a, b = result` to fail with
        # "not enough values to unpack" since MagicMock.__iter__ yields nothing.
        mock_a.get_portfolio_daily_returns_from_shadow.return_value = None
        mock_a.get_portfolio_bot_and_held_daily_returns.return_value = None
        mock_a.compute_portfolio_annualized_vol.return_value = None
        # Account-basis helpers → run the real implementation so value assertions work
        mock_a.get_portfolio_today_change_account_basis.side_effect = (
            real_analytics.get_portfolio_today_change_account_basis
        )
        mock_a.get_portfolio_cumulative_return_account_basis.side_effect = (
            real_analytics.get_portfolio_cumulative_return_account_basis
        )

        monkeypatch.setattr(
            app_module, "get_market_state", lambda dt, _s=market_state: _s, raising=False
        )
        resp = client.get("/api/state")

    assert resp.status_code == 200, (
        f"Frozen /api/state must return 200; got {resp.status_code}"
    )
    return resp.get_json()


# ===========================================================================
# AC-1: Frozen today_change account basis
# ===========================================================================


class TestFrozenTCAccountBasis:
    """
    AC-1: frozen/EOD today_change must use cash-inclusive account basis via
    analytics.get_portfolio_today_change_account_basis, not raw VW.

    Buggy code (app.py:1815-1817):
        "today_change": analytics.get_portfolio_today_change(
            _snap_symphonies_list, _snap_bot_state, trading_day=...
        ),
    No account-basis wrap — denominator is invested capital (cash excluded).

    Fixture: vw_tc.if_held=0.625, account_if_held_tc=0.50 (20% cash → gap=0.125pp).
    """

    def test_frozen_tc_if_held_is_portfolio_tc_not_vw(self, flask_client, monkeypatch):
        """
        AC-1: frozen TC.if_held must equal cached portfolio_tc=0.50 (account basis),
        not raw VW if_held=0.625.
        RED: current code returns raw VW TC entirely → TC.if_held=0.625 ≠ 0.50.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]  # 0.50
        vw_tc = fx["vw_tc"]  # if_held=0.625

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=account_if_held_tc,
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=vw_tc, vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        tc = body["portfolio_strip"]["today_change"]
        assert tc is not None, "portfolio_strip.today_change must not be None"

        assert tc.get("if_held") == pytest.approx(account_if_held_tc, abs=1e-6), (
            f"frozen TC.if_held must equal cached portfolio_tc={account_if_held_tc} "
            f"(account basis, cash-inclusive denominator), not vw_tc.if_held={vw_tc['if_held']} "
            f"(VW basis, cash-excluded denominator). Got {tc.get('if_held')!r}. "
            f"Fix: call analytics.get_portfolio_today_change_account_basis in the frozen branch "
            f"(app.py:1815-1817), mirroring the live path at app.py:1207-1212."
        )

    def test_frozen_tc_dry_run_equals_if_held_when_untriggered(self, flask_client, monkeypatch):
        """
        AC-1: untriggered portfolio (VW dry_run==VW if_held) → frozen TC.dry_run must
        equal TC.if_held (zero phantom alpha on account basis).
        RED: current code returns VW dry_run (0.625) ≠ portfolio_tc (0.50).
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        vw_tc = fx["vw_tc"]

        # Precondition: fixture is untriggered
        guard_delta_vw = float(vw_tc["dry_run"]) - float(vw_tc["if_held"])
        assert guard_delta_vw == pytest.approx(0.0, abs=1e-9), (
            f"Fixture precondition: vw_tc must be untriggered (dry_run==if_held); "
            f"got guard_delta_vw={guard_delta_vw}"
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=vw_tc, vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        tc = body["portfolio_strip"]["today_change"]
        guard_alpha_account = float(tc["dry_run"]) - float(tc["if_held"])

        # Tolerance abs=1e-3: single floating-point multiply path; wider than machine epsilon
        # to allow for any reasonable implementation rounding. A wrong implementation using
        # VW dry_run (0.625) vs account if_held (0.50) produces |0.625 - 0.50| = 0.125 —
        # far above this tolerance.
        assert guard_alpha_account == pytest.approx(0.0, abs=1e-3), (
            f"Untriggered portfolio: frozen TC guard_alpha (dry_run - if_held) must be 0.0, "
            f"not {guard_alpha_account:.6f}. A non-zero value is phantom alpha from the "
            f"cash-denominator mismatch. dry_run={tc['dry_run']!r}, if_held={tc['if_held']!r}. "
            f"Current code: VW dry_run=0.625 vs account-basis if_held=0.50 → phantom 0.125pp."
        )

    def test_frozen_tc_does_not_equal_raw_vw_when_cash_present(
        self, flask_client, monkeypatch
    ):
        """
        AC-1 regression guard: when cash is present (account_value > symphony_value_sum),
        the frozen TC.if_held must NOT equal the raw VW if_held.
        RED: current code returns raw VW → they ARE equal → test fails.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        vw_tc = fx["vw_tc"]
        account_if_held_tc = fx["account_if_held_tc"]

        # Precondition: 20% cash means VW (0.625) and account basis (0.50) differ > 0.1
        assert abs(float(vw_tc["if_held"]) - account_if_held_tc) > 0.1, (
            f"Fixture precondition: VW ({vw_tc['if_held']}) and account ({account_if_held_tc}) "
            f"must differ by >0.1pp; got gap={abs(float(vw_tc['if_held']) - account_if_held_tc):.4f}."
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=account_if_held_tc,
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=vw_tc, vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        tc = body["portfolio_strip"]["today_change"]

        # abs tolerance 0.05: the gap between VW (0.625) and account (0.50) is 0.125pp.
        # If they are within 0.05 the wrap did NOT fire — still returning raw VW.
        assert tc.get("if_held") != pytest.approx(float(vw_tc["if_held"]), abs=0.05), (
            f"frozen TC.if_held ({tc.get('if_held')!r}) must NOT equal raw VW if_held "
            f"({vw_tc['if_held']}) when 20% cash is present (invested_frac=0.8). "
            f"If they are equal, the account-basis wrap did not fire. "
            f"Fix: call analytics.get_portfolio_today_change_account_basis in the frozen branch."
        )


# ===========================================================================
# AC-2: Frozen cumulative_return both legs on account basis
# ===========================================================================


class TestFrozenCRAccountBasis:
    """
    AC-2: frozen/EOD cumulative_return must have BOTH if_held AND dry_run on account
    basis via analytics.get_portfolio_cumulative_return_account_basis.

    Buggy code (app.py:1807-1812):
        _snap_cached_cr = _account_totals_cache.get("portfolio_cr")
        if _snap_cached_cr is not None:
            _snap_cr = {
                "if_held": _snap_cached_cr,                        # account basis ✓
                "dry_run": _snap_cr.get("dry_run") if _snap_cr else None,  # VW basis ✗
            }
    Half-converted: if_held on account basis, dry_run on VW basis — mixed denominators
    make guard_alpha a scope artefact, not a clean guard-effect measure.
    """

    def test_frozen_cr_dry_run_not_vw_when_cache_warm(self, flask_client, monkeypatch):
        """
        AC-2: frozen CR.dry_run must NOT equal raw VW dry_run when cash present.
        RED: current code sets dry_run = _snap_cr.get("dry_run") = VW (31.25).
        Fix puts dry_run on account basis (25.0 for untriggered).
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        vw_cr = fx["vw_cr"]  # dry_run = 31.25
        account_cr = fx["account_cr"]  # 25.0

        # Precondition: VW CR and account CR differ unambiguously
        assert abs(float(vw_cr["dry_run"]) - account_cr) > 1.0, (
            f"Fixture precondition: vw_cr.dry_run ({vw_cr['dry_run']}) and account_cr "
            f"({account_cr}) must differ by >1pp; got gap="
            f"{abs(float(vw_cr['dry_run']) - account_cr):.4f}."
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=account_cr,
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=vw_cr,
            )
        finally:
            _clear_cache(app_module)

        cr = body["portfolio_strip"]["cumulative_return"]
        assert cr is not None, "portfolio_strip.cumulative_return must not be None"

        # abs tolerance 0.5: VW dry_run (31.25) vs account-basis dry_run (25.0) gap = 6.25.
        # Within 0.5 means the wrap did not fire.
        assert cr.get("dry_run") != pytest.approx(float(vw_cr["dry_run"]), abs=0.5), (
            f"frozen CR.dry_run ({cr.get('dry_run')!r}) must NOT equal raw VW dry_run "
            f"({vw_cr['dry_run']}) when 20% cash is present. "
            f"Current half-conversion bug: dry_run left on VW basis. "
            f"Fix: call analytics.get_portfolio_cumulative_return_account_basis for BOTH legs, "
            f"mirroring app.py:1183-1190."
        )

    def test_frozen_cr_dry_run_equals_if_held_when_untriggered(
        self, flask_client, monkeypatch
    ):
        """
        AC-2: untriggered (VW dry_run==VW if_held) → frozen CR.dry_run must equal
        CR.if_held (zero guard alpha on account basis).
        RED: current half-conversion gives dry_run=VW(31.25) vs if_held=account(25.0)
        → guard_alpha = 6.25 (phantom, not from any real guard event).
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        vw_cr = fx["vw_cr"]  # if_held == dry_run == 31.25 (untriggered)
        account_cr = fx["account_cr"]  # 25.0

        # Precondition: fixture is untriggered
        guard_delta_vw = float(vw_cr["dry_run"]) - float(vw_cr["if_held"])
        assert guard_delta_vw == pytest.approx(0.0, abs=1e-9), (
            f"Fixture precondition: vw_cr must be untriggered; got guard_delta_vw={guard_delta_vw}"
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=account_cr,
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=vw_cr,
            )
        finally:
            _clear_cache(app_module)

        cr = body["portfolio_strip"]["cumulative_return"]

        guard_alpha_account = float(cr["dry_run"]) - float(cr["if_held"])
        # Tolerance abs=0.01: current half-conversion bug produces |31.25 - 25.0| = 6.25.
        # A fixed implementation with zero VW guard delta should give exactly 0.0.
        assert guard_alpha_account == pytest.approx(0.0, abs=0.01), (
            f"Untriggered portfolio: frozen CR guard_alpha (dry_run - if_held) must be 0.0, "
            f"not {guard_alpha_account:.4f}. "
            f"cr.dry_run={cr['dry_run']!r}, cr.if_held={cr['if_held']!r}. "
            f"Current half-conversion: dry_run=VW(31.25) vs if_held=account(25.0) "
            f"→ 6.25pp phantom guard alpha (zero guard events happened)."
        )

    def test_frozen_cr_if_held_matches_account_portfolio_cr(
        self, flask_client, monkeypatch
    ):
        """
        AC-2: frozen CR.if_held must equal cached portfolio_cr (Composer simple_return * 100).
        This already works in the current code but must stay correct after the fix.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        account_cr = fx["account_cr"]  # 25.0

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=account_cr,
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        cr = body["portfolio_strip"]["cumulative_return"]

        assert cr.get("if_held") == pytest.approx(account_cr, abs=1e-4), (
            f"frozen CR.if_held must equal cached portfolio_cr={account_cr} "
            f"(Composer simple_return * 100). Got {cr.get('if_held')!r}."
        )


# ===========================================================================
# AC-3: Stale cache policy — last-good retention + honest VW floor
# ===========================================================================


class TestFrozenStaleCachePolicy:
    """
    AC-3: Stale/unavailable cache at frozen render time must never silently flip to VW.

    Tier 1 — last-good retention (primary): stale _account_totals_cache +
      warm _account_totals_last_good → use last-good + stamp account_basis_stale=True
      + account_basis_as_of (timestamp).
    Tier 2 — honest floor: no last-good (fresh restart) → mark basis="value_weighted"
      (or if_held=None). Never an unlabelled value the UI would read as account basis.
    """

    def test_stale_cache_with_last_good_uses_account_values(
        self, flask_client, monkeypatch
    ):
        """
        AC-3 Tier 1: stale _account_totals_cache + warm _account_totals_last_good →
        frozen TC.if_held equals last-good portfolio_tc (not raw VW).
        RED: _account_totals_last_good does not exist on app module yet.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]  # 0.50

        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail(
                "_account_totals_last_good not found on app module. "
                "impl must add: _account_totals_last_good: dict = {} at module level "
                "(a plain dict, NOT a _StaleFlagDict — it must survive mark_stale() calls)."
            )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        # Populate last-good, then mark the primary cache stale (simulates the
        # every-minute _StaleFlagDict.mark_stale() blip or a Composer timeout).
        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = account_if_held_tc
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)
            app_module._account_totals_last_good.clear()

        tc = body["portfolio_strip"]["today_change"]

        assert tc.get("if_held") == pytest.approx(account_if_held_tc, abs=1e-4), (
            f"Stale cache + warm last-good: frozen TC.if_held must equal last-good "
            f"portfolio_tc={account_if_held_tc} (account basis), not raw VW "
            f"{fx['vw_tc']['if_held']}. Got {tc.get('if_held')!r}. "
            f"Fix: frozen branch falls back to _account_totals_last_good when "
            f"_account_totals_cache.get() returns None (stale)."
        )

    def test_stale_cache_with_last_good_stamps_account_basis_stale_true(
        self, flask_client, monkeypatch
    ):
        """
        AC-3 Tier 1: stale + last-good → portfolio_strip["account_basis_stale"] is True.
        RED: the field does not exist on the frozen strip yet.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good not on app module — impl must add it.")

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})

        assert ps.get("account_basis_stale") is True, (
            f"portfolio_strip['account_basis_stale'] must be True when using last-good fallback "
            f"(signals to the operator that the basis data is from a previous fetch, not the "
            f"current cycle). Got {ps.get('account_basis_stale')!r}."
        )

    def test_stale_cache_with_last_good_stamps_account_basis_as_of(
        self, flask_client, monkeypatch
    ):
        """
        AC-3 Tier 1: stale + last-good → portfolio_strip["account_basis_as_of"] present
        and is a string (ET timestamp from _account_totals_last_success_at).
        RED: field does not exist on the frozen strip yet.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good not on app module.")

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})

        assert ps.get("account_basis_as_of") is not None, (
            f"portfolio_strip['account_basis_as_of'] must be present when using last-good "
            f"fallback. Got {ps.get('account_basis_as_of')!r}. "
            f"This field tells the operator when the account totals were last successfully fetched."
        )
        assert isinstance(ps["account_basis_as_of"], str), (
            f"account_basis_as_of must be a string (ET timestamp); "
            f"got {type(ps['account_basis_as_of']).__name__!r}."
        )

    def test_no_last_good_marks_basis_value_weighted(self, flask_client, monkeypatch):
        """
        AC-3 Tier 2: no last-good (fresh restart, never fetched) → frozen strip must mark
        basis='value_weighted' on portfolio_strip OR emit today_change.if_held=None.
        Never an unlabelled value that the UI would read as account basis.
        RED: current code returns raw VW with no marker.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good not on app module.")

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        # Fresh restart: empty primary cache AND no last-good
        _clear_cache(app_module)
        app_module._account_totals_last_good.clear()

        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        ps = body.get("portfolio_strip", {})
        tc = ps.get("today_change")

        # Accept: explicit "value_weighted" marker on portfolio_strip, OR if_held=None
        has_vw_marker = ps.get("basis") == "value_weighted"
        has_null_if_held = tc is None or tc.get("if_held") is None

        assert has_vw_marker or has_null_if_held, (
            f"No last-good (fresh restart): frozen strip must mark basis='value_weighted' "
            f"OR set today_change.if_held=None. Got portfolio_strip keys={list(ps.keys())!r}, "
            f"today_change={tc!r}. "
            f"Current code returns unlabelled VW value — operator cannot distinguish it from "
            f"an account-basis value. Fix: emit explicit honest-floor marker."
        )

    def test_no_last_good_value_differs_from_account_basis_computation(self):
        """
        AC-3 precondition: proves why the honest-floor label matters.
        The VW value returned on fresh restart DIFFERS from what account-basis would give —
        so returning it unlabelled IS a silent mislabel.
        This is a fixture-level assertion, not a route test.
        """
        fx = _load_parity_fixture()

        # What account-basis would give if account totals were available
        expected_account_basis = real_analytics.get_portfolio_today_change_account_basis(
            fx["vw_tc"],
            fx["account_if_held_tc"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        # The raw VW value the honest floor exposes (labelled)
        vw_if_held = float(fx["vw_tc"]["if_held"])

        assert abs(vw_if_held - float(expected_account_basis["if_held"])) > 0.05, (
            f"Fixture precondition: VW if_held ({vw_if_held}) must differ from account-basis "
            f"if_held ({expected_account_basis['if_held']}) by >0.05pp. "
            f"Gap = {abs(vw_if_held - float(expected_account_basis['if_held'])):.4f}. "
            f"Without this gap the honest-floor label makes no difference and the test "
            f"would not catch a silent mislabel."
        )


# ===========================================================================
# AC-6: Golden fixture — frozen == live parity
# ===========================================================================


class TestGoldenFixtureFrozenLiveParity:
    """
    AC-6: For the golden-fixture inputs, frozen-path portfolio_strip.today_change and
    portfolio_strip.cumulative_return must be byte-identical to the live-path result
    from the same analytics helpers.

    Expected values derived from fixture inputs via calling the helpers directly —
    never hardcoded producer-computed literals.
    """

    def test_fixture_has_required_schema(self):
        """AC-6: fixture has all 6 required input keys."""
        fx = _load_parity_fixture()
        required = {
            "account_value",
            "symphony_value_sum",
            "account_if_held_tc",
            "account_cr",
            "vw_tc",
            "vw_cr",
        }
        missing = required - set(fx.keys())
        assert not missing, (
            f"Golden fixture missing required keys: {sorted(missing)}. "
            f"Add them to eod_account_basis_parity.json."
        )

    def test_fixture_has_cash_present_and_gap_is_unambiguous(self):
        """AC-6: fixture must have >5% uninvested cash (invested_frac < 0.95)."""
        fx = _load_parity_fixture()
        invested_frac = fx["symphony_value_sum"] / fx["account_value"]
        assert invested_frac < 0.95, (
            f"Fixture must have >5% uninvested cash (invested_frac={invested_frac:.4f} < 0.95). "
            f"Without meaningful cash the VW/account-basis numbers are too close to catch a bug."
        )

    def test_frozen_tc_equals_live_path_for_fixture_inputs(
        self, flask_client, monkeypatch
    ):
        """
        AC-6: frozen-branch today_change must equal what the live-path analytics helper
        computes for the same inputs.
        Expected is derived by calling the real helper — not hardcoded.
        RED: current frozen branch doesn't call the account-basis helper → mismatch.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        # "Live-path" expected: call the real helper (same one live path uses)
        expected_tc = real_analytics.get_portfolio_today_change_account_basis(
            fx["vw_tc"],
            fx["account_if_held_tc"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        frozen_tc = body["portfolio_strip"]["today_change"]

        # rel=1e-6: single floating-point multiply; any implementation difference
        # larger than a few ULPs indicates a bug, not floating-point noise.
        assert frozen_tc["if_held"] == pytest.approx(expected_tc["if_held"], rel=1e-6), (
            f"Frozen TC.if_held ({frozen_tc['if_held']}) must equal live-path expected "
            f"({expected_tc['if_held']}). Fixture inputs: vw_tc={fx['vw_tc']}, "
            f"account_if_held_tc={fx['account_if_held_tc']}, account_value={fx['account_value']}, "
            f"symphony_value_sum={fx['symphony_value_sum']}. "
            f"Expected derived from analytics.get_portfolio_today_change_account_basis — not hardcoded."
        )
        assert frozen_tc["dry_run"] == pytest.approx(expected_tc["dry_run"], rel=1e-6), (
            f"Frozen TC.dry_run ({frozen_tc['dry_run']}) must equal live-path expected "
            f"({expected_tc['dry_run']})."
        )

    def test_frozen_cr_equals_live_path_for_fixture_inputs(
        self, flask_client, monkeypatch
    ):
        """
        AC-6: frozen-branch cumulative_return must equal what the live-path analytics
        helper computes for the same inputs.
        RED: current half-conversion → CR.dry_run mismatch.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        # "Live-path" expected: call the real helper
        expected_cr = real_analytics.get_portfolio_cumulative_return_account_basis(
            fx["vw_cr"],
            fx["account_cr"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
            )
        finally:
            _clear_cache(app_module)

        frozen_cr = body["portfolio_strip"]["cumulative_return"]

        assert frozen_cr["if_held"] == pytest.approx(expected_cr["if_held"], rel=1e-6), (
            f"Frozen CR.if_held ({frozen_cr['if_held']}) must equal live-path expected "
            f"({expected_cr['if_held']}). Expected derived from "
            f"analytics.get_portfolio_cumulative_return_account_basis — not hardcoded."
        )
        assert frozen_cr["dry_run"] == pytest.approx(expected_cr["dry_run"], rel=1e-6), (
            f"Frozen CR.dry_run ({frozen_cr['dry_run']}) must equal live-path expected "
            f"({expected_cr['dry_run']})."
        )

    def test_expected_tc_derived_not_tautological(self):
        """
        AC-6 anti-tautology: expected TC.if_held differs from raw VW if_held by >0.05pp.
        Proves the parity test above would catch a VW passthrough (non-discriminating test
        would pass even if the frozen branch returned raw VW).
        """
        fx = _load_parity_fixture()
        expected_tc = real_analytics.get_portfolio_today_change_account_basis(
            fx["vw_tc"],
            fx["account_if_held_tc"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        assert abs(float(expected_tc["if_held"]) - float(fx["vw_tc"]["if_held"])) > 0.05, (
            f"Anti-tautology: expected_tc.if_held ({expected_tc['if_held']}) must differ from "
            f"raw VW if_held ({fx['vw_tc']['if_held']}) by >0.05pp to make the parity test "
            f"discriminating. If they are equal, a VW passthrough would pass AC-6 — test is useless."
        )


# ===========================================================================
# AC-8: No new HTTP on the frozen render path
# ===========================================================================


class TestFrozenNoNewHttpOnRenderPath:
    """
    AC-8: The frozen account-basis wrap adds NO new Composer/Alpaca network call.
    Reads in-memory _account_totals_cache (and last-good store) only.
    GREEN now; must stay GREEN after the implementation.
    """

    def test_frozen_render_path_issues_zero_outbound_http(
        self, flask_client, monkeypatch
    ):
        """
        AC-8: requests.get must not be called during /api/state frozen branch render.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=fx["account_if_held_tc"],
            portfolio_cr=fx["account_cr"],
        )
        try:
            with (
                patch.object(app_module, "database", _make_db_mock(bot_state)),
                patch.object(app_module, "analytics") as mock_a,
                patch("requests.get") as mock_get,
            ):
                mock_a.get_portfolio_today_change.return_value = fx["vw_tc"]
                mock_a.get_portfolio_cumulative_return.return_value = fx["vw_cr"]
                mock_a.get_portfolio_max_drawdown.return_value = {
                    "if_held": -5.0, "dry_run": -5.0
                }
                mock_a.get_symphony_today_change.return_value = {
                    "if_held": None, "dry_run": None
                }
                mock_a.get_symphony_cumulative_return.return_value = {
                    "if_held": None, "dry_run": None
                }
                mock_a.get_symphony_max_drawdown.return_value = {
                    "if_held": None, "dry_run": None
                }
                mock_a.get_portfolio_daily_returns_from_shadow.return_value = None
                mock_a.get_portfolio_bot_and_held_daily_returns.return_value = None
                mock_a.compute_portfolio_annualized_vol.return_value = None
                mock_a.get_portfolio_today_change_account_basis.side_effect = (
                    real_analytics.get_portfolio_today_change_account_basis
                )
                mock_a.get_portfolio_cumulative_return_account_basis.side_effect = (
                    real_analytics.get_portfolio_cumulative_return_account_basis
                )

                monkeypatch.setattr(
                    app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
                )
                resp = client.get("/api/state")
        finally:
            _clear_cache(app_module)

        assert resp.status_code == 200
        assert mock_get.call_count == 0, (
            f"requests.get was called {mock_get.call_count} time(s) during frozen /api/state "
            f"render. The account-basis wrap must read only the in-memory cache — no new "
            f"outbound network calls on the request path. "
            f"Called with: {[str(c) for c in mock_get.call_args_list]}"
        )


# ===========================================================================
# AC-9: pre_market parity with closed_frozen
# ===========================================================================


class TestPreMarketParity:
    """
    AC-9: market_state="pre_market" receives identical account-basis treatment as
    closed_frozen — they share the same code branch at app.py:1581.
    Fix must not be gated to one sub-state.
    """

    def test_pre_market_tc_if_held_is_account_basis(self, flask_client, monkeypatch):
        """
        AC-9: pre_market + warm cache → TC.if_held is portfolio_tc (account basis).
        RED: current code returns raw VW for both states.
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}
        _setup_warm_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=account_if_held_tc,
            portfolio_cr=fx["account_cr"],
        )
        try:
            body = _drive_frozen_branch(
                client, app_module, monkeypatch,
                bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
                market_state="pre_market",
            )
        finally:
            _clear_cache(app_module)

        tc = body["portfolio_strip"]["today_change"]

        assert tc.get("if_held") == pytest.approx(account_if_held_tc, abs=1e-4), (
            f"pre_market: frozen TC.if_held must equal portfolio_tc={account_if_held_tc} "
            f"(account basis), not vw_tc.if_held={fx['vw_tc']['if_held']}. "
            f"Got {tc.get('if_held')!r}. "
            f"The account-basis wrap must apply to pre_market as well as closed_frozen."
        )

    def test_pre_market_and_closed_frozen_produce_identical_strip(
        self, flask_client, monkeypatch
    ):
        """
        AC-9: pre_market and closed_frozen with identical inputs must produce identical
        portfolio_strip TC and CR (they share the same branch at app.py:1581).
        """
        client, app_module = flask_client
        fx = _load_parity_fixture()

        snapshot = _make_snapshot(fx["symphony_value_sum"])
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        results: dict = {}
        for state in ("closed_frozen", "pre_market"):
            _setup_warm_cache(
                app_module,
                account_value=fx["account_value"],
                portfolio_tc=fx["account_if_held_tc"],
                portfolio_cr=fx["account_cr"],
            )
            try:
                body = _drive_frozen_branch(
                    client, app_module, monkeypatch,
                    bot_state=bot_state, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"],
                    market_state=state,
                )
                results[state] = body.get("portfolio_strip", {})
            finally:
                _clear_cache(app_module)

        cf_tc = results["closed_frozen"]["today_change"]
        pm_tc = results["pre_market"]["today_change"]

        assert cf_tc["if_held"] == pytest.approx(pm_tc["if_held"], rel=1e-6), (
            f"closed_frozen TC.if_held ({cf_tc['if_held']}) must equal pre_market TC.if_held "
            f"({pm_tc['if_held']}) for identical inputs — they share the same code branch."
        )
        assert cf_tc["dry_run"] == pytest.approx(pm_tc["dry_run"], rel=1e-6), (
            f"closed_frozen TC.dry_run ({cf_tc['dry_run']}) must equal pre_market TC.dry_run "
            f"({pm_tc['dry_run']})."
        )

        cf_cr = results["closed_frozen"]["cumulative_return"]
        pm_cr = results["pre_market"]["cumulative_return"]

        assert cf_cr["if_held"] == pytest.approx(pm_cr["if_held"], rel=1e-6), (
            f"closed_frozen CR.if_held must equal pre_market CR.if_held."
        )
        assert cf_cr["dry_run"] == pytest.approx(pm_cr["dry_run"], rel=1e-6), (
            f"closed_frozen CR.dry_run must equal pre_market CR.dry_run."
        )
