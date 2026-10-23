"""
RED tests for DE-EOD-BASIS-001 — _refresh_account_totals hardening (AC-4)
and live-path stale policy (AC-10, PM Gate Directive).

AC-4: _refresh_account_totals must:
  - Use a named constant _ACCOUNT_TOTALS_HTTP_TIMEOUT_S instead of the
    magic literal `timeout=10` at app.py:769.
  - Write _account_totals_last_success_at on successful refresh (the
    "as-of" timestamp surfaced as portfolio_strip["account_basis_as_of"]
    when last-good fallback fires).

AC-10 (PM Gate Directive): The live path (_compute_portfolio_strip) has the
SAME silent-VW-flip bug when the cache is stale: if _cached_tc is None it
falls back to raw VW with no label (app.py:1202-1216).
  Tier 1: stale + last-good → live TC.if_held = last-good portfolio_tc (account basis).
  Tier 2: stale + no last-good → live TC has explicit basis="value_weighted" marker
    or if_held=None. Never unlabelled VW.

Fixture provenance: inline scalar inputs derived from eod_account_basis_parity.json.
Expected values in assertions DERIVED from inputs — never hardcoded producer outputs.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import threading
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest

import analytics as real_analytics

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "frozen_portfolio_strip"
)


def _load_parity_fixture() -> dict:
    return json.loads((_FIXTURE_DIR / "eod_account_basis_parity.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Minimal bot-state factory
# ---------------------------------------------------------------------------


def _minimal_bot_state() -> dict:
    return {"date": "2026-07-01", "holdings": {}}


def _realistic_bot_state(symphony_value_sum: float) -> dict:
    """Flat live-path bot_state with real symphony entries summing to
    symphony_value_sum. Needed wherever a test must avoid the account_value==0.0
    fallback that _minimal_bot_state() produces (the live path's account_value
    derivation, app.py ~1173-1179, sums bot_state's per-symphony current_value
    when the cache is stale — an empty bot_state makes that sum 0.0, which
    spuriously triggers the CR account-basis helper's account_value<=0 division
    guard (analytics.py:1065-1066, intentionally returns vw_cr unchanged — a
    documented, in-scope-frozen design choice, not a bug). Tests isolating CR's
    OWN last-good wiring (independent of that guard) must use this factory."""
    alpha_value = symphony_value_sum * 0.625
    beta_value = symphony_value_sum * 0.375
    return {
        "date": "2026-07-01",
        "sym-alpha": {
            "name": "Alpha Momentum",
            "current_return": 4.2,
            "current_value": alpha_value,
            "simple_return": 0.042,
            "net_deposits": alpha_value * 0.9,
            "time_weighted_return": 0.045,
            "max_drawdown": 0.08,
        },
        "sym-beta": {
            "name": "Beta Defensive",
            "current_return": 1.1,
            "current_value": beta_value,
            "simple_return": 0.011,
            "net_deposits": beta_value * 0.93,
            "time_weighted_return": 0.012,
            "max_drawdown": 0.03,
        },
    }


# ===========================================================================
# AC-4: Named timeout constant + last-success timestamp
# ===========================================================================


class TestRefreshAccountTotalsHardening:
    """
    AC-4: _refresh_account_totals must expose a named constant for the HTTP timeout
    (rather than a bare literal) and must write _account_totals_last_success_at on
    each successful fetch so the stale-fallback path can surface the as-of time.
    RED: both artefacts are absent from the current module.
    """

    def test_timeout_constant_exists_on_module(self):
        """
        AC-4: app._ACCOUNT_TOTALS_HTTP_TIMEOUT_S must be a positive finite number.
        RED: the constant does not exist; the literal `timeout=10` is used directly.
        Fix: add `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 10` (or similar value) to app.py
        and replace `timeout=10` with `timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`.
        """
        import app as app_module

        if not hasattr(app_module, "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S"):
            pytest.fail(
                "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S not found on app module. "
                "impl must promote the timeout=10 literal (app.py:769) to a named module-level "
                "constant: `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 10` (or calibrated value)."
            )

        value = app_module._ACCOUNT_TOTALS_HTTP_TIMEOUT_S

        assert isinstance(value, (int, float)), (
            f"_ACCOUNT_TOTALS_HTTP_TIMEOUT_S must be numeric; got {type(value).__name__!r}."
        )
        assert value > 0, (
            f"_ACCOUNT_TOTALS_HTTP_TIMEOUT_S must be positive (a timeout of 0 or negative "
            f"makes no sense); got {value!r}."
        )

    def test_timeout_constant_used_by_refresh_fn(self):
        """
        AC-4: _refresh_account_totals must forward _ACCOUNT_TOTALS_HTTP_TIMEOUT_S to
        requests.get — not a bare literal.
        Verified by patching requests.get and inspecting keyword args on the actual call.
        RED: current code passes `timeout=10` (literal), not the constant.
        """
        import app as app_module

        if not hasattr(app_module, "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S"):
            pytest.skip("_ACCOUNT_TOTALS_HTTP_TIMEOUT_S absent — fails in test above.")

        expected_timeout = app_module._ACCOUNT_TOTALS_HTTP_TIMEOUT_S

        # Fake Composer /portfolio/accounts/{id}/total-stats response — matches the REAL
        # schema consumed by _refresh_account_totals (app.py:770-794):
        #   data["portfolio_value"]          (line 777, UNGUARDED — KeyError if absent)
        #   data["simple_return"] * 100      (line 782) → portfolio_cr
        #   data["todays_percent_change"]    (line 787, guarded by key-presence check)
        #   data.get("metrics")["max_drawdown"] (lines 790-792, guarded)
        # Values derived from eod_account_basis_parity.json:
        #   portfolio_value = account_value = 100000.0
        #   simple_return * 100 = account_cr = 25.0 → 0.25
        #   todays_percent_change * 100 = account_if_held_tc = 0.50 → 0.005
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "portfolio_value": 100000.0,
            "simple_return": 0.25,
            "todays_percent_change": 0.005,
            "metrics": {"max_drawdown": 0.08},
        }

        with patch.object(app_module, "_account_totals_cache") as mock_cache:
            with patch("requests.get", return_value=fake_response) as mock_get:
                try:
                    app_module._refresh_account_totals()
                except Exception:
                    # If the fn throws (DB, env, etc.) we still check the call args
                    pass

        if mock_get.call_count == 0:
            pytest.skip(
                "_refresh_account_totals made zero requests.get calls (likely skipped early "
                "due to missing env; cannot verify timeout forwarding)."
            )

        # Check that EVERY call used the named constant value for the `timeout` kwarg
        for i, c in enumerate(mock_get.call_args_list):
            actual_timeout = c.kwargs.get("timeout", c.args[1] if len(c.args) > 1 else None)
            assert actual_timeout == expected_timeout, (
                f"Call #{i}: requests.get timeout={actual_timeout!r} must equal "
                f"_ACCOUNT_TOTALS_HTTP_TIMEOUT_S={expected_timeout!r}. "
                f"Replace the literal `timeout=10` at app.py:769 with the named constant."
            )

    def test_last_success_at_module_variable_exists(self):
        """
        AC-4: app._account_totals_last_success_at must exist as a module-level variable
        (initially None).
        RED: the variable does not exist; it is needed for account_basis_as_of stamping.
        """
        import app as app_module

        if not hasattr(app_module, "_account_totals_last_success_at"):
            pytest.fail(
                "_account_totals_last_success_at not found on app module. "
                "impl must add `_account_totals_last_success_at: str | None = None` at "
                "module level and set it to an ET-format timestamp string inside "
                "_refresh_account_totals on a successful 200 response."
            )

    def test_last_success_at_written_on_successful_refresh(self):
        """
        AC-4: after a successful _refresh_account_totals call, _account_totals_last_success_at
        must be a non-None string (ET timestamp).
        RED: the variable exists only after the fix; after a mocked 200 it must be written.
        """
        import app as app_module

        if not hasattr(app_module, "_account_totals_last_success_at"):
            pytest.skip("_account_totals_last_success_at absent — tested above.")

        # Reset before test
        original = app_module._account_totals_last_success_at
        app_module._account_totals_last_success_at = None

        # Real Composer schema — same fix as test_timeout_constant_used_by_refresh_fn.
        # data["portfolio_value"] at app.py:777 is UNGUARDED; wrong field name → KeyError
        # → fn exits without writing anything → timestamp never set (test would false-PASS).
        # Values derived from eod_account_basis_parity.json:
        #   portfolio_value = 100000.0, simple_return=0.25 → cr=25.0,
        #   todays_percent_change=0.005 → tc=0.50.
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "portfolio_value": 100000.0,
            "simple_return": 0.25,
            "todays_percent_change": 0.005,
            "metrics": {"max_drawdown": 0.08},
        }

        try:
            with patch("requests.get", return_value=fake_response):
                with patch.object(app_module, "database") as mock_db:
                    mock_db.load_state.return_value = {"date": "2026-07-01"}
                    app_module._refresh_account_totals()
        except Exception:
            pass  # May throw on env gaps; we check the write regardless
        finally:
            result = app_module._account_totals_last_success_at
            app_module._account_totals_last_success_at = original

        if result is None:
            pytest.fail(
                "_account_totals_last_success_at was not written after a mocked 200 response. "
                "impl must set `_account_totals_last_success_at = <ET timestamp string>` inside "
                "_refresh_account_totals after a successful Composer fetch. "
                "This timestamp is surfaced as portfolio_strip['account_basis_as_of'] when "
                "the stale last-good fallback fires (AC-3)."
            )
        assert isinstance(result, str), (
            f"_account_totals_last_success_at must be a string (ET timestamp); "
            f"got {type(result).__name__!r}: {result!r}."
        )

    def test_last_success_at_not_written_on_failed_refresh(self):
        """
        AC-4: a failed refresh (non-200 or exception) must NOT update
        _account_totals_last_success_at. The as-of timestamp must reflect the last
        SUCCESSFUL fetch, not a failed attempt.
        """
        import app as app_module

        if not hasattr(app_module, "_account_totals_last_success_at"):
            pytest.skip("_account_totals_last_success_at absent — tested above.")

        sentinel = "2026-06-30T14:00:00 ET"
        original = app_module._account_totals_last_success_at
        app_module._account_totals_last_success_at = sentinel

        fake_response = MagicMock()
        fake_response.status_code = 503
        fake_response.json.side_effect = ValueError("bad response")

        try:
            with patch("requests.get", return_value=fake_response):
                app_module._refresh_account_totals()
        except Exception:
            pass
        finally:
            result = app_module._account_totals_last_success_at
            app_module._account_totals_last_success_at = original

        assert result == sentinel, (
            f"A failed refresh (503) must NOT update _account_totals_last_success_at. "
            f"Expected sentinel {sentinel!r}, got {result!r}. "
            f"Only a successful 200 response must advance the timestamp."
        )


# ===========================================================================
# AC-10: Live path stale policy (PM Gate Directive)
# ===========================================================================


class TestLivePathStaleCachePolicy:
    """
    AC-10 (PM Gate Directive): the live path (_compute_portfolio_strip, app.py:1202-1216)
    has the same silent-VW-flip when cache is stale:

        _cached_tc = _account_totals_cache.get("portfolio_tc")
        if _cached_tc is not None:
            ...  # account basis
        else:
            # silently uses raw VW — same bug as frozen branch, same fix required

    Two tiers (mirroring AC-3):
      Tier 1 — last-good present: use last-good + stamp account_basis_stale=True.
      Tier 2 — no last-good: mark basis="value_weighted" or if_held=None.
    """

    @pytest.fixture()
    def live_client(self):
        """Flask test client targeting the live path (market_open)."""
        import app as app_module

        app_module.app.config["TESTING"] = True
        with patch.object(app_module, "schedule"):
            with app_module.app.test_client() as client:
                yield client, app_module

    def _make_db_mock_live(self, bot_state: dict) -> MagicMock:
        mock_db = MagicMock()
        mock_db.load_state.return_value = bot_state
        mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        mock_db.get_triggers.return_value = []
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.read_fleet_alert.return_value = None
        return mock_db

    def _drive_live_branch(
        self,
        client,
        app_module,
        monkeypatch,
        *,
        bot_state: dict,
        vw_tc: dict,
        vw_cr: dict,
    ) -> dict:
        """Drive /api/state via the LIVE branch (market_open) with mocked analytics.

        Returns None for shadow/vol helpers so the live path skips its None-guarded
        blocks (`if _shadow_result is not None:`) instead of failing to unpack a
        MagicMock returned by the default mock stub.
        """
        with (
            patch.object(app_module, "database", self._make_db_mock_live(bot_state)),
            patch.object(app_module, "analytics") as mock_a,
        ):
            mock_a.get_portfolio_today_change.return_value = vw_tc
            mock_a.get_portfolio_cumulative_return.return_value = vw_cr
            mock_a.get_portfolio_max_drawdown.return_value = {"if_held": -5.0, "dry_run": -5.0}
            mock_a.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
            mock_a.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
            mock_a.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
            # Shadow/vol helpers: return None so the None-guarded unpack blocks are
            # skipped. A non-None MagicMock causes `a, b = result` to fail with
            # "not enough values to unpack" since MagicMock.__iter__ yields nothing.
            mock_a.get_portfolio_daily_returns_from_shadow.return_value = None
            mock_a.get_portfolio_bot_and_held_daily_returns.return_value = None
            mock_a.compute_portfolio_annualized_vol.return_value = None
            # Account-basis helpers: run the real implementation so value assertions work
            mock_a.get_portfolio_today_change_account_basis.side_effect = (
                real_analytics.get_portfolio_today_change_account_basis
            )
            mock_a.get_portfolio_cumulative_return_account_basis.side_effect = (
                real_analytics.get_portfolio_cumulative_return_account_basis
            )

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "market_open", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200, f"Live /api/state must return 200; got {resp.status_code}"
        return resp.get_json()

    def _setup_warm_live_cache(
        self,
        app_module,
        *,
        account_value: float,
        portfolio_tc: float,
        portfolio_cr: float,
    ) -> None:
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = account_value
        app_module._account_totals_cache["portfolio_tc"] = portfolio_tc
        app_module._account_totals_cache["portfolio_cr"] = portfolio_cr

    def _clear_live_cache(self, app_module) -> None:
        app_module._account_totals_cache.clear()

    def test_last_good_attribute_exists_on_module(self):
        """
        AC-10 precondition: _account_totals_last_good must exist on app module.
        RED: absent — needed for Tier 1 live-path fallback.
        """
        import app as app_module

        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail(
                "_account_totals_last_good not found on app module. "
                "impl must add: `_account_totals_last_good: dict = {}` at module level "
                "(a plain dict, NOT _StaleFlagDict — must survive mark_stale() calls)."
            )

    def test_live_path_stale_tier1_uses_last_good_tc(self, live_client, monkeypatch):
        """
        AC-10 Tier 1: stale primary cache + warm last-good → live TC.if_held equals
        last-good portfolio_tc (account basis).
        RED: current live code silently flips to raw VW when cache is stale.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent — see precondition test.")

        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]  # 0.50

        bot_state = {"date": "2026-07-01", "holdings": {}}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = account_if_held_tc
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()  # triggers silent-VW-flip in current code

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        tc = body["portfolio_strip"]["today_change"]

        # Tolerance abs=1e-4: single multiply; wrong impl returns VW (0.625) not account (0.50)
        assert tc.get("if_held") == pytest.approx(account_if_held_tc, abs=1e-4), (
            f"Live path stale Tier 1: TC.if_held must equal last-good portfolio_tc="
            f"{account_if_held_tc} (account basis), not raw VW if_held={fx['vw_tc']['if_held']}. "
            f"Got {tc.get('if_held')!r}. "
            f"Fix: live path (_compute_portfolio_strip) must fall back to "
            f"_account_totals_last_good when _account_totals_cache.get() returns None (stale), "
            f"mirroring AC-3 Tier 1."
        )

    def test_live_path_stale_tier1_uses_last_good_cr(self, live_client, monkeypatch):
        """
        AC-10 Tier 1 (CR leg): stale primary cache + warm last-good → live CR.if_held
        equals last-good portfolio_cr (account basis).

        Companion to test_live_path_stale_tier1_uses_last_good_tc — CR's independent
        code path through the live-path stale-cache logic was previously untested; a
        CR-isolated regression in the Tier-1 fallback would have passed all 33 prior
        tests.

        Uses _realistic_bot_state (not _minimal_bot_state) deliberately: an empty
        bot_state makes the live path's account_value fall back to 0.0 (see
        test_live_path_stale_tier1_account_value_uses_last_good below, which isolates
        that separate, newly-discovered gap), which would spuriously trigger the CR
        helper's account_value<=0 division guard and confound THIS assertion (if_held
        is guard-blind to account_value's magnitude once the guard doesn't fire, so a
        nonzero bot_state cleanly isolates whether CR's own last-good VALUE is picked
        up, independent of the account_value question).
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent — see precondition test.")

        fx = _load_parity_fixture()
        account_cr = fx["account_cr"]  # 25.0

        bot_state = _realistic_bot_state(fx["symphony_value_sum"])

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = account_cr
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        cr = body["portfolio_strip"]["cumulative_return"]

        assert cr.get("if_held") == pytest.approx(account_cr, abs=1e-4), (
            f"Live path stale Tier 1 (CR leg): CR.if_held must equal last-good "
            f"portfolio_cr={account_cr} (account basis), not raw VW if_held="
            f"{fx['vw_cr']['if_held']}. Got {cr.get('if_held')!r}. "
            f"Fix: live path's CR wrap must fall back to _account_totals_last_good "
            f"when _account_totals_cache.get('portfolio_cr') returns None (stale), "
            f"mirroring the TC leg."
        )

    def test_live_path_stale_tier1_account_value_uses_last_good(self, live_client, monkeypatch):
        """
        F6 (DISCOVERED 2026-07-02, CONFIRMED reachable by eodreview — routine per-minute
        stale window x triggered guard event, the MOST reachable finding of this cycle):
        the live path's `account_value` derivation must fall back to
        _account_totals_last_good.get("portfolio_value") when the primary cache read is
        stale — mirroring the frozen path's equivalent fallback.

        STRENGTHENED per eodreview's post-GREEN critique: the original version of this
        test used an EMPTY bot_state, which drives the buggy pre-fix fallback to
        account_value=0.0 and trips the analytics division guard, returning a SAFE
        degenerate result ({"if_held": account_if_held_tc, "dry_run": account_if_held_tc}
        for TC; vw_cr unchanged for CR) — that only proves the fallback is ABSENT, not
        that its absence causes real harm. This version uses a REALISTIC non-empty
        bot_state (per-symphony sum = fixture's symphony_value_sum, matching the
        fixture's real 20%-cash relationship) so the buggy fallback would land on
        account_value == symphony_value_sum (both positive — the division guard does
        NOT fire) instead of 0.0, silently producing invested_frac=1.0 (wrongly
        fully-invested, ignoring the 20% cash) instead of the correct 0.8. It also uses
        a TRIGGERED guard-delta pair (dry_run != if_held) — the fixture's own vw_tc/vw_cr
        are deliberately untriggered for the AC-1/AC-2 zero-phantom-alpha tests
        elsewhere, which makes invested_frac's magnitude irrelevant (guard_delta_vw=0
        nullifies the scaling term regardless of which account_value was used) — exactly
        the masking eodreview flagged. The guard-delta offsets below are synthetic guard
        events layered on the fixture's real if_held values (not hardcoded producer
        literals); both the CORRECT and WRONG expecteds are derived via the real
        analytics helper, and an anti-tautology precondition proves they differ
        meaningfully so this test can actually discriminate a regression.

        Expected: PASSES against 8e8c5d9 (GREEN added the last-good fallback at
        app.py:1173-1188). If it FAILS, the fix under-covers this realistic path.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()

        # Realistic bot_state: per-symphony sum == fixture's symphony_value_sum (80000,
        # 20% cash relative to the true last-good account_value of 100000). Positive, so
        # the buggy pre-fix fallback would NOT trip the division guard.
        bot_state = _realistic_bot_state(fx["symphony_value_sum"])

        # Synthetic TRIGGERED guard-delta pair, layered on the fixture's real if_held
        # values (not a hardcoded producer literal) — needed because the fixture's own
        # vw_tc/vw_cr are untriggered and would mask invested_frac's magnitude entirely.
        guard_delta_vw_tc = 0.20  # pp offset, today-change leg
        guard_delta_vw_cr = 4.0  # pp offset, cumulative-return leg
        triggered_vw_tc = {
            "if_held": fx["vw_tc"]["if_held"],
            "dry_run": fx["vw_tc"]["if_held"] + guard_delta_vw_tc,
        }
        triggered_vw_cr = {
            "if_held": fx["vw_cr"]["if_held"],
            "dry_run": fx["vw_cr"]["if_held"] + guard_delta_vw_cr,
        }

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=triggered_vw_tc,
                vw_cr=triggered_vw_cr,
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        # CORRECT expected: account_value = last-good 100000, symphony_value_sum = 80000
        # (the fixture's real relationship) -> invested_frac = 0.8.
        expected_correct_tc = real_analytics.get_portfolio_today_change_account_basis(
            triggered_vw_tc,
            fx["account_if_held_tc"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )
        expected_correct_cr = real_analytics.get_portfolio_cumulative_return_account_basis(
            triggered_vw_cr,
            fx["account_cr"],
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        # WRONG expected: simulates the pre-fix buggy fallback, where account_value would
        # have landed on the per-symphony sum (== symphony_value_sum) -> invested_frac ==
        # 1.0 (wrongly fully-invested, ignoring the 20% cash) — the exact defect F6 pins.
        wrong_tc = real_analytics.get_portfolio_today_change_account_basis(
            triggered_vw_tc,
            fx["account_if_held_tc"],
            fx["symphony_value_sum"],
            fx["symphony_value_sum"],
        )
        wrong_cr = real_analytics.get_portfolio_cumulative_return_account_basis(
            triggered_vw_cr,
            fx["account_cr"],
            fx["symphony_value_sum"],
            fx["symphony_value_sum"],
        )

        # Anti-tautology precondition: correct (invested_frac=0.8) and wrong
        # (invested_frac=1.0) must differ meaningfully, else this test could not
        # discriminate a regression back to the buggy fallback.
        assert abs(expected_correct_tc["dry_run"] - wrong_tc["dry_run"]) > 0.01, (
            "Precondition: correct (invested_frac=0.8) and wrong (invested_frac=1.0) TC "
            "dry_run must differ meaningfully, else this test cannot discriminate a "
            "regression to the buggy fallback."
        )
        assert abs(expected_correct_cr["dry_run"] - wrong_cr["dry_run"]) > 0.01, (
            "Precondition: correct and wrong CR dry_run must differ meaningfully."
        )

        strip = body["portfolio_strip"]
        tc = strip.get("today_change") or {}
        cr = strip.get("cumulative_return") or {}

        assert strip.get("account_value") == pytest.approx(fx["account_value"], rel=1e-6), (
            f"Live path stale Tier 1: portfolio_strip['account_value'] must equal "
            f"last-good portfolio_value={fx['account_value']} (true cash-inclusive "
            f"account total), not the per-symphony-sum fallback "
            f"({fx['symphony_value_sum']!r}). Got {strip.get('account_value')!r}."
        )
        assert tc.get("dry_run") == pytest.approx(expected_correct_tc["dry_run"], rel=1e-6), (
            f"Live path stale Tier 1 (realistic, triggered): today_change.dry_run must "
            f"use the CORRECT cash-inclusive invested_frac=0.8 (last-good "
            f"account_value={fx['account_value']}, symphony_value_sum="
            f"{fx['symphony_value_sum']}), giving {expected_correct_tc['dry_run']!r}. "
            f"Got {tc.get('dry_run')!r}. A regression to the buggy per-symphony-sum "
            f"fallback (account_value==symphony_value_sum -> invested_frac==1.0, "
            f"WRONGLY ignoring the 20% cash) would give {wrong_tc['dry_run']!r} instead "
            f"— silently over-scaling guard_alpha."
        )
        assert cr.get("dry_run") == pytest.approx(expected_correct_cr["dry_run"], rel=1e-6), (
            f"Live path stale Tier 1 (realistic, triggered): cumulative_return.dry_run "
            f"must use the CORRECT invested_frac=0.8, giving "
            f"{expected_correct_cr['dry_run']!r}. Got {cr.get('dry_run')!r}. The buggy "
            f"fallback (invested_frac==1.0) would give {wrong_cr['dry_run']!r} instead."
        )

    def test_live_path_cr_unlabelled_when_only_tc_is_warm_and_cr_is_absent(
        self, live_client, monkeypatch
    ):
        """
        Cross-confirmed finding (eodtest + eodreview independent review, 2026-07-02):
        the live-path Tier-2 honest-floor marker checks ONLY _cached_tc / last-good TC,
        never CR's own state. When TC is warm (cache) but CR is absent from BOTH cache
        and last-good, CR independently falls to raw VW with NO signal at all on the
        strip — a direct AC-3 violation (never silently present VW as account basis).

        Two-sided contract (per PM directive, mirroring the frozen-path analogue in
        TestFrozenIndependentFieldGating): (1) a missing CR must not collaterally break
        a warm TC — asserted here as a regression guard, since the live path's TC/CR
        gating is already independent (unlike the frozen path's combined gate) so this
        half is expected to already pass; (2) CR must carry an honesty signal.
        RED (half 2 only, on current code): none of basis=='value_weighted',
        account_basis_stale=True, or cumulative_return.if_held=None fire in this
        scenario against current code.
        """
        client, app_module = live_client
        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]
        bot_state = {"date": "2026-07-01", "holdings": {}}

        expected_tc = real_analytics.get_portfolio_today_change_account_basis(
            fx["vw_tc"],
            account_if_held_tc,
            fx["account_value"],
            fx["symphony_value_sum"],
        )

        # Warm TC only (via primary cache); CR absent from both cache and last-good.
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = fx["account_value"]
        app_module._account_totals_cache["portfolio_tc"] = account_if_held_tc
        app_module._account_totals_last_good.clear()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})
        tc = ps.get("today_change") or {}
        cr = ps.get("cumulative_return") or {}

        # Half 1: warm TC must NOT be collaterally affected by CR's absence (regression
        # guard — the live path's TC/CR gating is already independent).
        assert tc.get("if_held") == pytest.approx(expected_tc["if_held"], rel=1e-6), (
            f"Live path: warm TC / absent CR (no last-good) — today_change.if_held must "
            f"remain the correctly account-basis-wrapped TC value "
            f"({expected_tc['if_held']!r}). Got {tc.get('if_held')!r}. A missing CR must "
            f"not collaterally break a warm TC."
        )

        # Half 2: CR (legitimately on VW basis) must be honestly signalled.
        has_vw_marker = ps.get("basis") == "value_weighted"
        has_stale_marker = ps.get("account_basis_stale") is True
        has_null_if_held = cr.get("if_held") is None

        assert has_vw_marker or has_stale_marker or has_null_if_held, (
            f"Live path: warm TC / absent CR (no last-good) must signal CR is on VW "
            f"basis via basis=='value_weighted', account_basis_stale=True, or "
            f"cumulative_return.if_held=None. Got basis={ps.get('basis')!r}, "
            f"account_basis_stale={ps.get('account_basis_stale')!r}, "
            f"cumulative_return={cr!r}. "
            f"Current code's Tier-2 marker checks only _cached_tc/last-good-TC, missing "
            f"this CR-only-degraded case."
        )

    def test_live_path_stale_tier1_stamps_account_basis_as_of_is_string(
        self, live_client, monkeypatch
    ):
        """
        AC-10 Tier 1: account_basis_as_of must be a non-None STRING when Tier 1 fires —
        mirroring the frozen path's explicit `_account_totals_last_success_at or
        datetime.now(_ET).strftime(...)` fallback. The live path assigns
        _account_totals_last_success_at directly with no such fallback.

        This test resets the module-level timestamp to None BEFORE populating last-good
        directly — the same test-construction pattern the existing Tier-1 live tests
        already use (bypassing _refresh_account_totals, which never advances the real
        timestamp in that construction) — to prove the stamp is robust even when the
        timestamp was never set by a real fetch.
        RED: current live-path code has no fallback, so account_basis_as_of is None.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        original_last_success_at = app_module._account_totals_last_success_at
        app_module._account_totals_last_success_at = None

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()
            app_module._account_totals_last_success_at = original_last_success_at

        ps = body.get("portfolio_strip", {})

        assert ps.get("account_basis_stale") is True, (
            f"Precondition: Tier 1 must have fired (account_basis_stale True). "
            f"Got {ps.get('account_basis_stale')!r}."
        )
        assert ps.get("account_basis_as_of") is not None, (
            f"Live Tier 1: account_basis_as_of must be present (non-None) even when "
            f"_account_totals_last_success_at was never set by a real refresh. "
            f"Got {ps.get('account_basis_as_of')!r}. "
            f"Fix: mirror the frozen path's fallback — "
            f"`_account_totals_last_success_at or datetime.now(_ET).strftime(...)`."
        )
        assert isinstance(ps["account_basis_as_of"], str), (
            f"account_basis_as_of must be a string (ET timestamp); got "
            f"{type(ps['account_basis_as_of']).__name__!r}: {ps['account_basis_as_of']!r}."
        )

    def test_live_path_stale_tier1_stamps_account_basis_stale(self, live_client, monkeypatch):
        """
        AC-10 Tier 1: stale + last-good → live portfolio_strip["account_basis_stale"] is True.
        RED: field does not exist on the live strip.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx["account_if_held_tc"]
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})
        assert ps.get("account_basis_stale") is True, (
            f"Live stale Tier 1: portfolio_strip['account_basis_stale'] must be True. "
            f"Got {ps.get('account_basis_stale')!r}."
        )

    def test_live_path_tier2_marker_not_fooled_by_zero_last_good_tc(self, live_client, monkeypatch):
        """
        Finding 1 (/review PR #89): the Tier-2 honest-floor marker
        (app.py:1388-1395) uses a FALSY check (`not (...)`) on last-good
        portfolio_tc, not an `is None` check. When last-good portfolio_tc == 0.0
        (a real flat-day value — the account genuinely had zero today-change) and
        the primary cache is stale, the Tier-1 wrap block a few lines up correctly
        treats 0.0 as present (`if _lg_tc is not None:`) and wraps today_change to
        account basis + sets account_basis_stale=True. But the marker's
        `not (0.0)` == True fires the SAME as a genuinely-absent value, falsely
        labelling a correctly-wrapped account-basis number as basis='value_weighted'.
        The frozen path (app.py:1895/1901/1977) uses `is None` throughout and does
        NOT have this bug — this is live-path only.

        portfolio_cr is set to a non-missing fixture value so this test isolates
        the TC leg specifically (see the CR-leg symmetric test immediately below).

        RED: current code stamps basis='value_weighted' even though today_change
        was genuinely wrapped via the Tier-1 last-good fallback.
        Fix: the marker must use `is None`, not falsy-check, on last-good values.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = 0.0  # real flat-day value
        app_module._account_totals_last_good["portfolio_cr"] = fx["account_cr"]  # non-missing
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})

        assert ps.get("basis") != "value_weighted", (
            f"Live Tier 1 (last-good portfolio_tc==0.0, a real flat-day value): the "
            f"strip must NOT be marked basis='value_weighted' — today_change was "
            f"genuinely wrapped to account basis via the Tier-1 last-good fallback. "
            f"Got basis={ps.get('basis')!r}. "
            f"Fix: the Tier-2 marker must use `is None`, not a falsy `not (...)` "
            f"check, on last-good portfolio_tc (mirrors the frozen path)."
        )
        assert ps.get("account_basis_stale") is True, (
            f"Companion: account_basis_stale must be True (the Tier-1 last-good "
            f"fallback genuinely fired for this scenario). "
            f"Got {ps.get('account_basis_stale')!r}."
        )

    def test_live_path_tier2_marker_not_fooled_by_zero_last_good_cr(self, live_client, monkeypatch):
        """
        Finding 1 (/review PR #89), CR-leg symmetric case: last-good
        portfolio_cr == 0.0 (a real flat-day cumulative-return value). The same
        falsy-check bug (`_cr_fully_missing`, app.py:1391-1393) fires the Tier-2
        marker despite cumulative_return being genuinely wrapped via the Tier-1
        last-good fallback a few lines up.

        portfolio_tc is set to a non-missing fixture value so this test isolates
        the CR leg specifically (companion to the TC-leg test above).
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        app_module._account_totals_last_good["portfolio_value"] = fx["account_value"]
        app_module._account_totals_last_good["portfolio_tc"] = fx[
            "account_if_held_tc"
        ]  # non-missing
        app_module._account_totals_last_good["portfolio_cr"] = 0.0  # real flat-day value
        app_module._account_totals_cache.mark_stale()

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        ps = body.get("portfolio_strip", {})

        assert ps.get("basis") != "value_weighted", (
            f"Live Tier 1 (last-good portfolio_cr==0.0, a real flat-day value): the "
            f"strip must NOT be marked basis='value_weighted' — cumulative_return "
            f"was genuinely wrapped to account basis via the Tier-1 last-good "
            f"fallback. Got basis={ps.get('basis')!r}. "
            f"Fix: the Tier-2 marker must use `is None`, not a falsy `not (...)` "
            f"check, on last-good portfolio_cr (mirrors the frozen path)."
        )
        assert ps.get("account_basis_stale") is True, (
            f"Companion: account_basis_stale must be True (the Tier-1 last-good "
            f"fallback genuinely fired for this scenario). "
            f"Got {ps.get('account_basis_stale')!r}."
        )

    def test_live_path_stale_tier2_marks_basis_value_weighted(self, live_client, monkeypatch):
        """
        AC-10 Tier 2: stale + no last-good → live TC must be labelled basis='value_weighted'
        OR today_change.if_held=None. Never an unlabelled VW value.
        RED: current live code returns raw VW with no marker.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        # No last-good, stale primary cache
        app_module._account_totals_last_good.clear()
        self._clear_live_cache(app_module)

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)

        ps = body.get("portfolio_strip", {})
        tc = ps.get("today_change")

        has_vw_marker = ps.get("basis") == "value_weighted"
        has_null_if_held = tc is None or tc.get("if_held") is None

        assert has_vw_marker or has_null_if_held, (
            f"Live stale Tier 2 (no last-good): portfolio_strip must mark "
            f"basis='value_weighted' OR today_change.if_held=None. "
            f"Got ps keys={list(ps.keys())!r}, today_change={tc!r}. "
            f"Current code returns raw VW value without any marker — mirroring the same "
            f"silent-VW-flip bug present in the frozen branch. "
            f"Fix: mirror AC-3 Tier 2 (honest floor) on the live path."
        )

    def test_live_path_stale_tier2_cr_stays_vw_with_marker(self, live_client, monkeypatch):
        """
        AC-10 Tier 2 (CR leg): stale/absent cache, no last-good (both TC and CR absent
        together) → live CR stays raw VW AND the strip carries basis='value_weighted'.
        Companion to test_live_path_stale_tier2_marks_basis_value_weighted (TC leg) —
        CR's Tier-2 honest-floor output was previously unchecked. This scenario has
        BOTH fields absent together, so the existing TC-scoped marker legitimately
        fires here — distinct from test_live_path_cr_unlabelled_when_only_tc_is_warm_
        and_cr_is_absent above, which isolates CR-only degradation.
        """
        client, app_module = live_client
        if not hasattr(app_module, "_account_totals_last_good"):
            pytest.fail("_account_totals_last_good absent.")

        fx = _load_parity_fixture()
        bot_state = {"date": "2026-07-01", "holdings": {}}

        app_module._account_totals_last_good.clear()
        self._clear_live_cache(app_module)

        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)

        ps = body.get("portfolio_strip", {})
        cr = ps.get("cumulative_return") or {}

        assert cr.get("if_held") == pytest.approx(float(fx["vw_cr"]["if_held"]), rel=1e-6), (
            f"Live Tier 2 (no cache, no last-good): CR.if_held must equal raw VW if_held "
            f"({fx['vw_cr']['if_held']}) — the honest-floor value. "
            f"Got {cr.get('if_held')!r}."
        )
        assert ps.get("basis") == "value_weighted", (
            f"Live Tier 2 (both TC and CR absent): portfolio_strip['basis'] must be "
            f"'value_weighted'. Got {ps.get('basis')!r}."
        )

    def test_live_path_warm_cache_still_uses_account_basis_unaffected(
        self, live_client, monkeypatch
    ):
        """
        AC-10 regression guard: warm primary cache (not stale) must still produce
        account-basis TC (same as today). The stale-policy fix must not regress the
        normal (warm-cache) case.
        GREEN if current behavior is correct; must STAY GREEN after the fix.
        """
        client, app_module = live_client
        fx = _load_parity_fixture()
        account_if_held_tc = fx["account_if_held_tc"]

        bot_state = {"date": "2026-07-01", "holdings": {}}
        self._setup_warm_live_cache(
            app_module,
            account_value=fx["account_value"],
            portfolio_tc=account_if_held_tc,
            portfolio_cr=fx["account_cr"],
        )
        # Explicitly NOT marking stale — cache is warm
        try:
            body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)

        tc = body["portfolio_strip"]["today_change"]

        # Warm cache: if_held must be on account basis (0.50), not raw VW (0.625)
        assert tc.get("if_held") == pytest.approx(account_if_held_tc, abs=1e-4), (
            f"Live warm-cache: TC.if_held must equal portfolio_tc={account_if_held_tc} "
            f"(account basis). Got {tc.get('if_held')!r}. "
            f"The stale-policy change must not break the normal warm-cache path."
        )

    def _drive_frozen_minimal(
        self, client, app_module, monkeypatch, *, vw_tc: dict, vw_cr: dict
    ) -> dict:
        """Drive the frozen /api/state branch with the SAME mocking scaffolding as
        _drive_live_branch, so the two branches are compared under identical mocked
        conditions. Self-contained (does not import tests.dashboard.test_eod_account_basis)
        to avoid cross-test-module import fragility.
        """
        fx = _load_parity_fixture()
        alpha_value = fx["symphony_value_sum"] * 0.625
        beta_value = fx["symphony_value_sum"] * 0.375
        snapshot = {
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
        bot_state = {"date": "2026-07-01", "last_market_close_snapshot": snapshot}

        mock_db = MagicMock()
        mock_db.load_state.return_value = bot_state
        mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        mock_db.get_triggers.return_value = []
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.read_fleet_alert.return_value = None

        with (
            patch.object(app_module, "database", mock_db),
            patch.object(app_module, "analytics") as mock_a,
        ):
            mock_a.get_portfolio_today_change.return_value = vw_tc
            mock_a.get_portfolio_cumulative_return.return_value = vw_cr
            mock_a.get_portfolio_max_drawdown.return_value = {"if_held": -5.0, "dry_run": -5.0}
            mock_a.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
            mock_a.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
            mock_a.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
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

        assert resp.status_code == 200, f"Frozen /api/state must return 200; got {resp.status_code}"
        return resp.get_json()

    def test_frozen_tc_if_held_matches_live_when_tc_fully_missing(self, live_client, monkeypatch):
        """
        Finding 3 (/review PR #89): for an IDENTICAL fully-missing-TC state (no
        cache, no last-good for portfolio_tc — CR is kept warm via cache so this
        test isolates the TC leg specifically), the frozen path currently returns
        today_change.if_held=None while the live path returns the real raw-VW
        if_held — a cross-path inconsistency. The plan's documented DEFAULT for
        the honest floor is raw-VW + basis marker (the live behavior); frozen's
        TC Tier-2 branch (app.py:1938-1941, `{**_snap_vw_tc, "if_held": None}`)
        deviates from it. Frozen's own CR-leg Tier-2 branch (app.py:1950-1953)
        does NOT null — this asymmetry is TC-only.

        RED: frozen today_change.if_held (None) != live today_change.if_held
        (a real number, fx["vw_tc"]["if_held"]).
        Fix: eodimpl changes the frozen TC Tier-2 branch to surface raw
        _snap_vw_tc unchanged, matching the frozen CR branch + live path + plan.
        """
        client, app_module = live_client
        fx = _load_parity_fixture()

        # Fully-missing TC (no cache, no last-good for TC); CR warm via cache so
        # only the TC leg is under test.
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = fx["account_value"]
        app_module._account_totals_cache["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_last_good.clear()

        live_bot_state = {"date": "2026-07-01", "holdings": {}}
        try:
            live_body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=live_bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)
            app_module._account_totals_last_good.clear()

        # Re-establish the IDENTICAL cache state for the frozen branch (shared
        # module-level cache; re-populate since the live drive cleared it above).
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_value"] = fx["account_value"]
        app_module._account_totals_cache["portfolio_cr"] = fx["account_cr"]
        app_module._account_totals_last_good.clear()
        try:
            frozen_body = self._drive_frozen_minimal(
                client, app_module, monkeypatch, vw_tc=fx["vw_tc"], vw_cr=fx["vw_cr"]
            )
        finally:
            self._clear_live_cache(app_module)

        live_tc_if_held = live_body["portfolio_strip"]["today_change"].get("if_held")
        frozen_tc_if_held = frozen_body["portfolio_strip"]["today_change"].get("if_held")

        assert live_tc_if_held is not None, (
            "Precondition: live path's fully-missing-TC today_change.if_held must "
            "be a real raw-VW number (the plan's documented Tier-2 default), not "
            "None — if this fails, the live-path behavior itself has regressed "
            "and the cross-path comparison below is meaningless."
        )
        assert frozen_tc_if_held == pytest.approx(live_tc_if_held, rel=1e-6), (
            f"Cross-path consistency: for an identical fully-missing-TC state, "
            f"frozen today_change.if_held ({frozen_tc_if_held!r}) must match live "
            f"today_change.if_held ({live_tc_if_held!r}) — both should be the raw "
            f"VW if_held value (the plan's documented Tier-2 default), not None. "
            f"Frozen currently nulls if_held on this branch; live does not."
        )

    def test_live_path_and_frozen_path_stale_tier2_are_consistent(self, live_client, monkeypatch):
        """
        AC-10 consistency: both the live and frozen paths' Tier 2 honest floor must use
        the SAME marker convention when both cache and last-good are fully absent —
        both must set portfolio_strip['basis'] == 'value_weighted'. Mixed conventions
        between the two paths would confuse the UI consumer (one path could render a
        staleness warning the other never triggers for the identical failure condition).

        Drives BOTH the live (/api/state via market_open) and frozen (/api/state via
        closed_frozen) branches with identical empty-cache/no-last-good inputs and
        asserts the SAME marker fires on both.

        Replaces a prior placeholder `assert True` (an assertion-free tautology that
        could never fail regardless of implementation, providing zero real coverage —
        a violation of the no-tautology testing rule).
        """
        client, app_module = live_client
        fx = _load_parity_fixture()

        # --- Live branch (market_open), no cache, no last-good ---
        live_bot_state = {"date": "2026-07-01", "holdings": {}}
        app_module._account_totals_last_good.clear()
        self._clear_live_cache(app_module)
        try:
            live_body = self._drive_live_branch(
                client,
                app_module,
                monkeypatch,
                bot_state=live_bot_state,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)

        # --- Frozen branch (closed_frozen), no cache, no last-good ---
        app_module._account_totals_last_good.clear()
        self._clear_live_cache(app_module)
        try:
            frozen_body = self._drive_frozen_minimal(
                client,
                app_module,
                monkeypatch,
                vw_tc=fx["vw_tc"],
                vw_cr=fx["vw_cr"],
            )
        finally:
            self._clear_live_cache(app_module)

        live_basis = live_body.get("portfolio_strip", {}).get("basis")
        frozen_basis = frozen_body.get("portfolio_strip", {}).get("basis")

        assert live_basis == "value_weighted", (
            f"Live Tier 2 (no cache, no last-good) must set basis='value_weighted'. "
            f"Got {live_basis!r}."
        )
        assert frozen_basis == "value_weighted", (
            f"Frozen Tier 2 (no cache, no last-good) must set basis='value_weighted'. "
            f"Got {frozen_basis!r}."
        )
        assert live_basis == frozen_basis, (
            f"Live and frozen Tier 2 marker conventions must match: live={live_basis!r}, "
            f"frozen={frozen_basis!r}."
        )


# ===========================================================================
# Live-branch account-basis grace: the stale flag follows the age of the last
# successful refresh, never merely "the cache is masked while a refresh runs".
#
# Seams: app._monotonic (the clock for ages; replaced by the monotonic_clock
# fixture), the requests.get boundary (the composer_total_stats fixture serves
# the captured Composer body), and app._account_totals_cache.mark_stale() for
# the mask the engine cycle applies.  Expected strip values are recomputed from
# the captured body (percent scale: Composer fractions times 100, as the
# refresh stores them).
# ===========================================================================

_ET = ZoneInfo("America/New_York")
_ASOF_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ET$")


def _frozen_datetime_class(instant: datetime) -> type[datetime]:
    """datetime subclass whose .now(tz) resolves to `instant` (for app.datetime)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    return _Frozen


def _refresh_succeeds(app_module, composer) -> None:
    composer.mode = "ok"
    app_module._refresh_account_totals()


def _refresh_fails(app_module, composer, mode: str) -> None:
    composer.mode = mode
    app_module._refresh_account_totals()


class TestLiveBranchAccountBasisGrace:
    """Live /api/state: account_basis_stale is raised by a real outage, not by
    the routine post-cycle mask."""

    @pytest.fixture()
    def live_client(self):
        import app as app_module

        app_module.app.config["TESTING"] = True
        with patch.object(app_module, "schedule"):
            with app_module.app.test_client() as client:
                yield client, app_module

    def _poll_strip(self, client, app_module, monkeypatch, *, masked: bool = True) -> dict:
        """One live /api/state poll; `masked` applies the engine cycle's mask first."""
        if masked:
            app_module._account_totals_cache.mark_stale()
        fx = _load_parity_fixture()
        body = TestLivePathStaleCachePolicy()._drive_live_branch(
            client,
            app_module,
            monkeypatch,
            bot_state=_minimal_bot_state(),
            vw_tc=fx["vw_tc"],
            vw_cr=fx["vw_cr"],
        )
        return body["portfolio_strip"]

    def test_masked_read_inside_grace_serves_last_good_totals_without_stale_flag(
        self, live_client, monkeypatch, composer_total_stats, monotonic_clock
    ):
        client, app_module = live_client
        _refresh_succeeds(app_module, composer_total_stats)
        monotonic_clock.advance(40.0)

        strip = self._poll_strip(client, app_module, monkeypatch)

        expected_tc_pct = composer_total_stats.payload["todays_percent_change"] * 100.0
        assert "account_basis_stale" not in strip, (
            "A masked read 40 s after a successful refresh is the routine post-cycle gap; "
            f"account_basis_stale must be absent. Got {strip.get('account_basis_stale')!r}."
        )
        assert strip.get("basis") != "value_weighted", (
            "Inside the grace the last good account totals still apply (Tier 1), "
            "not the value-weighted floor."
        )
        assert strip["today_change"]["if_held"] == pytest.approx(expected_tc_pct, abs=1e-6), (
            "Tier-1 figures must still come from the last good Composer totals "
            f"(todays_percent_change x 100 = {expected_tc_pct})."
        )

    @pytest.mark.parametrize(
        ("failure_mode", "failed_refresh_offsets_s", "poll_offset_s"),
        [
            pytest.param(
                "status_503", (60.0, 120.0), 130.0, id="two_503_replies_130s_after_success"
            ),
            pytest.param("read_timeout", (60.0, 90.0), 95.0, id="read_timeout_95s_after_success"),
            pytest.param(
                "connection_error", (60.0, 100.0), 110.0, id="connection_error_110s_after_success"
            ),
        ],
    )
    def test_outage_raises_stale_flag_only_after_grace_and_names_the_last_success(
        self,
        live_client,
        monkeypatch,
        composer_total_stats,
        monotonic_clock,
        failure_mode,
        failed_refresh_offsets_s,
        poll_offset_s,
    ):
        client, app_module = live_client
        success_instant = datetime(2026, 10, 21, 10, 15, 30, tzinfo=_ET)
        with patch.object(app_module, "datetime", _frozen_datetime_class(success_instant)):
            _refresh_succeeds(app_module, composer_total_stats)
        expected_as_of = success_instant.strftime(app_module._ACCOUNT_BASIS_TS_FMT)
        t0 = monotonic_clock.now

        # First failed minute: the outage is real but young, so no chip yet.
        first_offset = failed_refresh_offsets_s[0]
        monotonic_clock.now = t0 + first_offset
        _refresh_fails(app_module, composer_total_stats, failure_mode)
        monotonic_clock.now = t0 + first_offset + 1.0
        young = self._poll_strip(client, app_module, monkeypatch)
        assert "account_basis_stale" not in young, (
            f"One failed refresh ({failure_mode}) with the last success {first_offset + 1.0}s old "
            "is inside the 90 s grace; the chip must not show."
        )

        # Later failures: the last success is now past the grace.
        for offset in failed_refresh_offsets_s[1:]:
            monotonic_clock.now = t0 + offset
            _refresh_fails(app_module, composer_total_stats, failure_mode)
        monotonic_clock.now = t0 + poll_offset_s
        old = self._poll_strip(client, app_module, monkeypatch)

        assert old.get("account_basis_stale") is True, (
            f"A {failure_mode} outage with the last success {poll_offset_s}s old must still "
            f"raise the stale flag. Got {old.get('account_basis_stale')!r}."
        )
        assert old.get("account_basis_as_of") == expected_as_of, (
            "account_basis_as_of must name the last SUCCESSFUL refresh "
            f"({expected_as_of!r}), not a failed attempt or the poll time."
        )
        assert _ASOF_RE.match(old["account_basis_as_of"])

    def test_stale_flag_appears_exactly_when_last_success_reaches_the_grace_age(
        self, live_client, monkeypatch, composer_total_stats, monotonic_clock
    ):
        client, app_module = live_client
        _refresh_succeeds(app_module, composer_total_stats)
        t0 = monotonic_clock.now

        monotonic_clock.now = t0 + 89.9
        just_inside = self._poll_strip(client, app_module, monkeypatch)
        monotonic_clock.now = t0 + 90.0
        at_boundary = self._poll_strip(client, app_module, monkeypatch)

        assert "account_basis_stale" not in just_inside, (
            "89.9 s after the last success is still inside the grace."
        )
        assert at_boundary.get("account_basis_stale") is True, (
            "A last success exactly 90 s old is past the grace: the flag must be true."
        )

    @pytest.mark.parametrize(
        ("age_s", "expected_past_grace"),
        [
            pytest.param(None, True, id="no_success_in_this_process"),
            pytest.param(0.0, False, id="just_succeeded"),
            pytest.param(89.9, False, id="just_inside_grace"),
            pytest.param(90.0, True, id="exactly_at_grace"),
            pytest.param(91.0, True, id="past_grace"),
        ],
    )
    def test_past_grace_reads_the_age_of_the_last_success(
        self, monkeypatch, monotonic_clock, age_s, expected_past_grace
    ):
        import app as app_module

        helper = getattr(app_module, "_account_basis_past_grace", None)
        if helper is None:
            pytest.fail(
                "app._account_basis_past_grace() is missing: it must be true when "
                "_account_totals_last_success_mono is None or at least 90 s old, "
                "measured with app._monotonic()."
            )
        stamp = None if age_s is None else monotonic_clock.now - age_s
        monkeypatch.setattr(app_module, "_account_totals_last_success_mono", stamp)

        assert helper() is expected_past_grace

    def test_cold_process_reads_value_weighted_floor_and_counts_as_past_grace(
        self, live_client, monkeypatch, monotonic_clock
    ):
        client, app_module = live_client
        helper = getattr(app_module, "_account_basis_past_grace", None)
        if helper is None:
            pytest.fail(
                "app._account_basis_past_grace() is missing: with no successful refresh in "
                "this process it must read True (nothing recent to excuse a masked cache)."
            )
        assert helper() is True

        strip = self._poll_strip(client, app_module, monkeypatch, masked=False)

        assert strip.get("basis") == "value_weighted", (
            "With no refresh ever succeeded and no last-good totals the strip reads the "
            f"value-weighted floor (Tier 2). Got basis={strip.get('basis')!r}."
        )
        assert "account_basis_stale" not in strip

    def test_successful_refresh_stamps_monotonic_time_and_serves_fresh_totals(
        self, live_client, monkeypatch, composer_total_stats, monotonic_clock
    ):
        client, app_module = live_client
        stamped_at = monotonic_clock.now
        _refresh_succeeds(app_module, composer_total_stats)
        monotonic_clock.advance(75.0)

        strip = self._poll_strip(client, app_module, monkeypatch, masked=False)

        expected_tc_pct = composer_total_stats.payload["todays_percent_change"] * 100.0
        assert app_module._account_totals_last_success_mono == stamped_at, (
            "A successful refresh must stamp _account_totals_last_success_mono with "
            "app._monotonic() read at success time."
        )
        assert strip["today_change"]["if_held"] == pytest.approx(expected_tc_pct, abs=1e-6)
        assert "account_basis_stale" not in strip
        assert strip.get("basis") != "value_weighted"

    @pytest.mark.parametrize(
        "failure_mode",
        [
            pytest.param("status_503", id="non_200_reply"),
            pytest.param("read_timeout", id="read_timeout"),
            pytest.param("connection_error", id="connection_error"),
            pytest.param("cache_write_fault", id="fault_inside_the_cache_write"),
        ],
    )
    def test_failed_refresh_never_advances_the_success_stamp(
        self, live_client, monkeypatch, composer_total_stats, monotonic_clock, failure_mode
    ):
        _client, app_module = live_client
        success_stamp = monotonic_clock.now
        _refresh_succeeds(app_module, composer_total_stats)
        monotonic_clock.advance(60.0)

        if failure_mode == "cache_write_fault":
            composer_total_stats.mode = "ok"

            def _boom():
                raise RuntimeError("simulated fault after the cache writes")

            monkeypatch.setattr(app_module._account_totals_cache, "refresh_written", _boom)
            app_module._refresh_account_totals()
        else:
            _refresh_fails(app_module, composer_total_stats, failure_mode)

        assert app_module._account_totals_last_success_mono == success_stamp, (
            f"A failed refresh ({failure_mode}) must leave the success stamp at the last "
            "real success; advancing it would hide a genuine outage."
        )

    def test_flush_resync_refresh_in_flight_keeps_grace_until_last_success_is_90s_old(
        self, live_client, monkeypatch, tmp_path, composer_total_stats, monotonic_clock
    ):
        client, app_module = live_client
        monkeypatch.setattr(real_analytics, "_POST_MORTEMS_DIR", str(tmp_path / "post_mortems"))
        _refresh_succeeds(app_module, composer_total_stats)
        t0 = monotonic_clock.now
        monotonic_clock.now = t0 + 40.0
        app_module._account_totals_cache.mark_stale()
        composer_total_stats.mode = "hold"
        resync: dict[str, int] = {}

        def _post_flush_resync() -> None:
            with app_module.app.test_client() as other_client:
                resync["status"] = other_client.post("/api/settings/flush-resync").status_code

        poster = threading.Thread(
            target=_post_flush_resync, name="flush-resync-poster", daemon=True
        )
        poster.start()
        try:
            assert composer_total_stats.started.wait(timeout=5.0), (
                "flush-resync must start its Composer refresh (the held request)."
            )
            inside = self._poll_strip(client, app_module, monkeypatch)
            monotonic_clock.now = t0 + 130.0
            past = self._poll_strip(client, app_module, monkeypatch)
        finally:
            composer_total_stats.release.set()
            poster.join(timeout=20.0)

        assert "account_basis_stale" not in inside, (
            "While the flush-resync refresh is in flight and the last success is 40 s old, "
            "the live strip must not raise the stale flag."
        )
        assert past.get("account_basis_stale") is True, (
            "An in-flight refresh must not hide a last success that is 130 s old."
        )
        assert resync.get("status") == 200


class TestServerRenderedAccountBasisGrace:
    """GET / in market hours reads the same live strip through the real
    get_api_state_dict; the template receives portfolio_strip from it."""

    @pytest.fixture(autouse=True)
    def _stub_get_api_state_dict(self):
        """Override tests/app/conftest.py's autouse stub of the same name: this class
        needs the real get_api_state_dict so GET / reaches the real strip computation."""
        yield

    @pytest.fixture()
    def seeded_live_client(self, monkeypatch, captured_symphony_entry):
        import app as app_module
        import database

        monkeypatch.setattr(real_analytics, "DB_FILE", os.environ["DB_PATH"])
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
        state = database.load_state() or {}
        state["last_successful_cycle_at"] = datetime.now(_ET).isoformat()
        state["iaSOOUsmnCJHiZvbrWfs"] = captured_symphony_entry
        database.save_state(state)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            yield client, app_module

    def test_server_render_inside_grace_hands_the_template_no_stale_flag(
        self, seeded_live_client, monkeypatch, composer_total_stats, monotonic_clock
    ):
        client, app_module = seeded_live_client
        rendered: dict = {}
        monkeypatch.setattr(
            app_module,
            "render_template",
            lambda _tpl, **context: rendered.update(context) or "<html></html>",
        )
        _refresh_succeeds(app_module, composer_total_stats)
        t0 = monotonic_clock.now
        monotonic_clock.now = t0 + 40.0
        app_module._account_totals_cache.mark_stale()
        composer_total_stats.mode = "hold"
        post_cycle_refresh = threading.Thread(
            target=app_module._refresh_account_totals, name="cycle-refresh", daemon=True
        )
        post_cycle_refresh.start()
        try:
            assert composer_total_stats.started.wait(timeout=5.0)

            inside_response = client.get("/")
            inside_strip = dict(rendered.get("portfolio_strip") or {})
            monotonic_clock.now = t0 + 130.0
            past_response = client.get("/")
            past_strip = dict(rendered.get("portfolio_strip") or {})
        finally:
            composer_total_stats.release.set()
            post_cycle_refresh.join(timeout=10.0)

        assert inside_response.status_code == 200
        assert "today_change" in inside_strip, (
            "Precondition: GET / must hand the template a real computed strip."
        )
        assert "account_basis_stale" not in inside_strip, (
            "GET / during the post-cycle refresh, 40 s after a success, must render no stale chip."
        )
        assert past_response.status_code == 200
        assert past_strip.get("account_basis_stale") is True, (
            "GET / with the last success 130 s old must still carry the stale flag."
        )


class TestFrozenBranchStaleFlagIsOutsideTheLiveGrace:
    """After the close the stale flag belongs to the frozen-branch rules, not to
    the live 90 s grace: a masked cache with a recent success stays flagged."""

    def test_frozen_branch_flags_masked_cache_even_when_last_success_is_40s_old(
        self, monkeypatch, composer_total_stats, monotonic_clock, captured_symphony_entry
    ):
        import app as app_module
        import database

        monkeypatch.setattr(real_analytics, "DB_FILE", os.environ["DB_PATH"])
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen")
        trading_day = datetime.now(_ET).strftime("%Y-%m-%d")
        sym_id = "iaSOOUsmnCJHiZvbrWfs"
        state = database.load_state() or {}
        state["last_market_close_snapshot"] = {
            "trading_day": trading_day,
            "captured_at_et": "16:00:01 ET",
            "shadow_divergence": {"by_symphony": {}, "portfolio_today": None},
            "accounts_map": {
                captured_symphony_entry["account"]: [{"id": sym_id, **captured_symphony_entry}]
            },
        }
        database.save_state(state)
        _refresh_succeeds(app_module, composer_total_stats)
        monotonic_clock.advance(40.0)
        app_module._account_totals_cache.mark_stale()
        app_module.app.config["TESTING"] = True

        with app_module.app.test_client() as client:
            resp = client.get("/api/state")

        assert resp.status_code == 200
        strip = resp.get_json()["portfolio_strip"]
        assert strip.get("account_basis_stale") is True, (
            "The frozen branch's stale flag follows its own rules (masked cache falling "
            "back to last-good); the live 90 s grace must not apply after the close."
        )
        assert isinstance(strip.get("account_basis_as_of"), str)
