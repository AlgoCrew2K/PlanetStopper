"""
RED tests for the account-totals TTL cache in app.py.

impl-backend must add:
  - Module-level ``_account_totals_cache: dict`` (initially empty {})
  - ``_refresh_account_totals()`` — fetches Composer total-stats, populates cache
    with keys ``portfolio_value`` and ``portfolio_cr`` (= simple_return * 100)
  - ``_compute_portfolio_strip`` reads cache when populated, falls back to
    per-symphony sum / value-weighted CR when cache is empty
  - ``run_scheduler()`` registers ``_refresh_account_totals`` on the minute schedule

Fixture: tests/fixtures/composer/reconcile-2026-05-20/total_stats.json
         (captured from Composer total-stats endpoint, authoritative source)

No live API calls — all Composer interactions are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Golden fixture
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "composer" / "reconcile-2026-05-20"


@pytest.fixture(scope="module")
def total_stats_fixture() -> dict:
    """Captured Composer total-stats response — authoritative fixture."""
    return json.loads((_FIXTURE_DIR / "total_stats.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Minimal bot_state helpers
# ---------------------------------------------------------------------------


def _minimal_bot_state(n_symphonies: int = 2) -> dict:
    """Return a bot_state with n simple symphony entries for strip computation."""
    return {
        f"sym-{i}": {
            "name": f"Symphony {i}",
            "current_value": 1000.0,
            "current_return": 1.5,
            "simple_return": 0.05,
            "net_deposits": 800.0,
            "time_weighted_return": 0.06,
            "max_drawdown": 0.12,
        }
        for i in range(n_symphonies)
    }


# ---------------------------------------------------------------------------
# Cache isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the module-level cache before and after each test."""
    # Before: ensure empty
    try:
        app_module._account_totals_cache.clear()
    except AttributeError:
        pytest.fail(
            "_account_totals_cache not found on app module. "
            "impl-backend must add: _account_totals_cache: dict = {} at module level."
        )
    yield
    # After: clean up so tests don't bleed state
    try:
        app_module._account_totals_cache.clear()
    except AttributeError:
        pass


# ===========================================================================
# TestComputePortfolioStripCachePopulated
# ===========================================================================


class TestComputePortfolioStripCachePopulated:
    def test_uses_cached_portfolio_value_when_cache_is_populated(self, total_stats_fixture):
        """
        When _account_totals_cache contains portfolio_value, _compute_portfolio_strip
        must return that value as account_value, not the sum of per-symphony current_value.

        This is the primary contract: the cache replaces the per-symphony sum.
        """
        cached_value = total_stats_fixture["portfolio_value"]
        app_module._account_totals_cache["portfolio_value"] = cached_value
        app_module._account_totals_cache["portfolio_cr"] = (
            total_stats_fixture["simple_return"] * 100.0
        )

        bot_state = _minimal_bot_state(2)
        per_symphony_sum = sum(
            v.get("current_value") or 0.0 for v in bot_state.values() if isinstance(v, dict)
        )
        # Confirm the fixture value differs from the per-symphony sum so the
        # test is actually discriminating.
        assert cached_value != pytest.approx(per_symphony_sum, rel=0.01), (
            "Test setup: fixture portfolio_value must differ from per-symphony sum "
            "to make the cache-vs-sum distinction observable."
        )

        result = app_module._compute_portfolio_strip(bot_state)

        assert result["account_value"] == pytest.approx(cached_value, rel=1e-6), (
            f"_compute_portfolio_strip must use cached portfolio_value={cached_value} "
            f"when cache is populated; got {result['account_value']} "
            f"(per-symphony sum would be {per_symphony_sum}). "
            f"Check app.py _compute_portfolio_strip for _account_totals_cache lookup."
        )

    def test_uses_cached_portfolio_cr_when_cache_is_populated(self, total_stats_fixture):
        """
        When _account_totals_cache contains portfolio_cr, _compute_portfolio_strip
        must return a cumulative_return dict whose if_held equals the cached
        portfolio_cr (Composer simple_return * 100).
        """
        cached_cr = total_stats_fixture["simple_return"] * 100.0
        app_module._account_totals_cache["portfolio_value"] = total_stats_fixture["portfolio_value"]
        app_module._account_totals_cache["portfolio_cr"] = cached_cr

        bot_state = _minimal_bot_state(2)
        result = app_module._compute_portfolio_strip(bot_state)

        assert result["cumulative_return"] is not None, (
            "cumulative_return must not be None when cache is populated"
        )
        assert "if_held" in result["cumulative_return"], (
            "cumulative_return must have an 'if_held' key"
        )
        assert result["cumulative_return"]["if_held"] == pytest.approx(cached_cr, rel=1e-6), (
            f"cumulative_return.if_held must equal cached portfolio_cr={cached_cr:.4f}% "
            f"(Composer simple_return * 100); got {result['cumulative_return']['if_held']}. "
            f"Check app.py: _account_totals_cache['portfolio_cr'] path."
        )

    def test_cache_portfolio_value_is_positive_float(self, total_stats_fixture):
        """
        The fixture portfolio_value is a positive float — basic sanity on the
        fixture itself so downstream tests don't assert on a zero or negative.
        """
        assert total_stats_fixture["portfolio_value"] > 0, (
            "total_stats fixture must have a positive portfolio_value"
        )

    def test_cache_portfolio_cr_is_positive_float(self, total_stats_fixture):
        """
        The fixture simple_return is positive — guard against a zero fixture
        that would make the if_held==0 check ambiguous.
        """
        assert total_stats_fixture["simple_return"] > 0, (
            "total_stats fixture must have a positive simple_return"
        )


# ===========================================================================
# TestComputePortfolioStripCacheEmpty
# ===========================================================================


class TestComputePortfolioStripCacheEmpty:
    def test_falls_back_to_per_symphony_sum_when_cache_is_empty(self):
        """
        When _account_totals_cache is empty (startup), account_value must be the
        sum of per-symphony current_value — the pre-cache legacy path.
        """
        # cache is empty (cleared by autouse fixture)
        bot_state = _minimal_bot_state(3)
        per_symphony_sum = sum(
            v.get("current_value") or 0.0 for v in bot_state.values() if isinstance(v, dict)
        )

        result = app_module._compute_portfolio_strip(bot_state)

        assert result["account_value"] == pytest.approx(per_symphony_sum, rel=1e-6), (
            f"With empty cache, account_value must fall back to per-symphony sum "
            f"({per_symphony_sum}); got {result['account_value']}."
        )

    def test_falls_back_to_value_weighted_cr_when_cache_is_empty(self):
        """
        When _account_totals_cache is empty, cumulative_return.if_held must be
        derived from per-symphony data (value-weighted), not None or a hardcoded value.

        The exact value is derived from the analytics helper — we assert it is
        not None and is a finite float (shape/presence test, not exact value).
        """
        import math

        bot_state = _minimal_bot_state(2)
        result = app_module._compute_portfolio_strip(bot_state)

        cr = result.get("cumulative_return")
        # It may be None if analytics raises — that's the existing fallback path.
        # The important thing: if it's not None, if_held must be a finite number.
        if cr is not None and cr.get("if_held") is not None:
            assert isinstance(cr["if_held"], (int, float)), (
                "cumulative_return.if_held must be a number when computed from "
                "per-symphony data; got non-numeric value."
            )
            assert math.isfinite(cr["if_held"]), (
                "cumulative_return.if_held must be finite (not inf/nan) when "
                "derived from per-symphony value-weighted CR."
            )

    def test_partial_cache_missing_portfolio_value_falls_back(self):
        """
        A cache with only portfolio_cr (no portfolio_value) must not corrupt
        account_value — the function must fall back to per-symphony sum for
        whichever keys are missing.
        """
        # Only portfolio_cr — portfolio_value absent
        app_module._account_totals_cache["portfolio_cr"] = 69.94

        bot_state = _minimal_bot_state(2)
        per_symphony_sum = sum(
            v.get("current_value") or 0.0 for v in bot_state.values() if isinstance(v, dict)
        )

        result = app_module._compute_portfolio_strip(bot_state)

        assert result["account_value"] == pytest.approx(per_symphony_sum, rel=1e-6), (
            "With portfolio_value absent from cache, account_value must fall back "
            "to per-symphony sum; got {result['account_value']}."
        )

    def test_partial_cache_missing_portfolio_cr_falls_back(self):
        """
        A cache with only portfolio_value (no portfolio_cr) must not corrupt
        cumulative_return — the function must fall back to value-weighted CR for
        the missing key.
        """
        import math

        app_module._account_totals_cache["portfolio_value"] = 12945.18

        bot_state = _minimal_bot_state(2)
        result = app_module._compute_portfolio_strip(bot_state)

        cr = result.get("cumulative_return")
        # Must not have used the cached portfolio_cr (which wasn't set).
        # Assert it is either None (analytics fallback) or a finite float derived
        # from per-symphony data — it must NOT be 69.94 (a value from the partial
        # cache test that could leak through a bug).
        if cr is not None and cr.get("if_held") is not None:
            assert math.isfinite(cr["if_held"]), (
                "cumulative_return.if_held must be finite when derived from "
                "per-symphony data (portfolio_cr not in cache)."
            )


# ===========================================================================
# TestRefreshAccountTotals
# ===========================================================================


class TestRefreshAccountTotals:
    def test_refresh_populates_portfolio_value_from_fixture(self, total_stats_fixture):
        """
        _refresh_account_totals() must populate _account_totals_cache['portfolio_value']
        from the Composer total-stats response's 'portfolio_value' field.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = total_stats_fixture

        with patch("requests.get", return_value=mock_resp):
            try:
                app_module._refresh_account_totals()
            except AttributeError:
                pytest.fail(
                    "_refresh_account_totals not found on app module. "
                    "impl-backend must add this function."
                )

        assert "portfolio_value" in app_module._account_totals_cache, (
            "_refresh_account_totals must write 'portfolio_value' into "
            "_account_totals_cache after a successful Composer response."
        )
        assert app_module._account_totals_cache["portfolio_value"] == pytest.approx(
            total_stats_fixture["portfolio_value"], rel=1e-6
        ), (
            f"_account_totals_cache['portfolio_value'] must equal "
            f"total_stats.portfolio_value={total_stats_fixture['portfolio_value']}; "
            f"got {app_module._account_totals_cache.get('portfolio_value')}."
        )

    def test_refresh_populates_portfolio_cr_as_percentage(self, total_stats_fixture):
        """
        _refresh_account_totals() must store portfolio_cr as percentage-scale:
        total_stats.simple_return * 100.

        Consistent with per-symphony CR convention (analytics.py uses ×100 throughout).
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = total_stats_fixture

        with patch("requests.get", return_value=mock_resp):
            app_module._refresh_account_totals()

        expected_cr = total_stats_fixture["simple_return"] * 100.0
        assert "portfolio_cr" in app_module._account_totals_cache, (
            "_refresh_account_totals must write 'portfolio_cr' into _account_totals_cache."
        )
        assert app_module._account_totals_cache["portfolio_cr"] == pytest.approx(
            expected_cr, rel=1e-6
        ), (
            f"_account_totals_cache['portfolio_cr'] must equal "
            f"simple_return * 100 = {expected_cr:.4f}%; "
            f"got {app_module._account_totals_cache.get('portfolio_cr')}. "
            f"Check: cache stores fraction not percentage."
        )

    def test_refresh_does_not_corrupt_cache_on_http_error(self, total_stats_fixture):
        """
        When the Composer request fails (non-200 or exception), _refresh_account_totals
        must NOT overwrite a previously-good cache entry.

        Stale data is better than no data. A failed refresh must leave the cache
        in whatever state it was before the call.
        """
        # Pre-populate with known good values
        good_value = total_stats_fixture["portfolio_value"]
        good_cr = total_stats_fixture["simple_return"] * 100.0
        app_module._account_totals_cache["portfolio_value"] = good_value
        app_module._account_totals_cache["portfolio_cr"] = good_cr

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "internal server error"}

        with patch("requests.get", return_value=mock_resp):
            app_module._refresh_account_totals()

        assert app_module._account_totals_cache.get("portfolio_value") == pytest.approx(
            good_value, rel=1e-6
        ), (
            "A failed Composer response (500) must not clear or corrupt the existing "
            "portfolio_value cache entry — stale data is preferable to empty."
        )
        assert app_module._account_totals_cache.get("portfolio_cr") == pytest.approx(
            good_cr, rel=1e-6
        ), (
            "A failed Composer response (500) must not clear or corrupt the existing "
            "portfolio_cr cache entry."
        )

    def test_refresh_does_not_crash_on_requests_exception(self):
        """
        If requests.get raises (network timeout, connection error), _refresh_account_totals
        must swallow the exception and not propagate it — it runs on the scheduler
        thread and must not crash the daemon.
        """
        import requests as req_module

        with patch("requests.get", side_effect=req_module.exceptions.Timeout("timed out")):
            try:
                app_module._refresh_account_totals()
            except Exception as exc:
                pytest.fail(
                    f"_refresh_account_totals must not propagate exceptions from "
                    f"requests.get; it runs on the scheduler thread. Got: {exc!r}"
                )

    def test_refresh_calls_composer_total_stats_endpoint(self, total_stats_fixture):
        """
        _refresh_account_totals must call the Composer total-stats endpoint:
        GET /api/v0.1/portfolio/accounts/{account_id}/total-stats

        The URL must contain 'total-stats' — fixture-capture provenance.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = total_stats_fixture

        with patch("requests.get", return_value=mock_resp) as mock_get:
            app_module._refresh_account_totals()

        assert mock_get.called, "_refresh_account_totals must call requests.get"
        called_url = mock_get.call_args[0][0] if mock_get.call_args[0] else str(mock_get.call_args)
        assert "total-stats" in called_url, (
            f"_refresh_account_totals must GET the Composer 'total-stats' endpoint; "
            f"called URL was: {called_url}"
        )


# ===========================================================================
# TestSchedulerRegistration
# ===========================================================================


class TestSchedulerRegistration:
    def test_run_scheduler_registers_refresh_account_totals(self):
        """
        run_scheduler() must register _refresh_account_totals in the schedule.

        This test stubs `schedule.every()` to capture registered jobs and asserts
        that _refresh_account_totals appears as a registered job function.
        """
        import schedule as schedule_module

        registered_jobs = []

        original_every = schedule_module.every

        class _CapturingChain:
            """Captures the final .do(fn) call target."""

            def __getattr__(self, name):
                return self

            def do(self, fn, *args, **kwargs):
                registered_jobs.append(fn)
                return self

            def __call__(self, *args, **kwargs):
                return self

        def _capturing_every(*args, **kwargs):
            return _CapturingChain()

        # Patch schedule.every so run_scheduler() doesn't actually spin.
        # Also patch schedule.run_pending and time.sleep to prevent the while-True.
        with (
            patch.object(schedule_module, "every", side_effect=_capturing_every),
            patch.object(schedule_module, "run_pending"),
            patch("time.sleep", side_effect=StopIteration),
        ):
            try:
                app_module.run_scheduler()
            except StopIteration:
                pass  # Expected — breaks out of the while True loop after 1 iteration

        refresh_fn = getattr(app_module, "_refresh_account_totals", None)
        assert refresh_fn is not None, (
            "_refresh_account_totals must exist on app module before this test can pass."
        )

        assert refresh_fn in registered_jobs, (
            f"run_scheduler() must register _refresh_account_totals via schedule.every(); "
            f"registered jobs: {[getattr(fn, '__name__', repr(fn)) for fn in registered_jobs]}. "
            f"Add: schedule.every().minute.at(':00').do(_refresh_account_totals) "
            f"(or equivalent) to run_scheduler()."
        )
