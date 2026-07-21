"""
RED tests -- F-026: hist_source silently mislabels shadow_history chart data
as "post_mortem".

ROOT CAUSE (traced through the real function, not asserted from the audit
alone): app.py's `_compute_portfolio_strip` builds its `_strip` dict's
`hist_bot`/`hist_held` from `analytics.get_portfolio_bot_and_held_daily_returns()`
(genuinely shadow_history-sourced -- confirmed via that function's own
docstring: "Build BOTH the portfolio Bot and Held continuous daily-return
series from shadow_history") but NEVER sets a `hist_source` key on the
returned dict at all. `_build_meta` (app.py:1110) then reads
`ps.get("hist_source", "post_mortem")` -- silently defaulting to the WRONG
value for data that is genuinely shadow_history-sourced. The other two
builders in app.py (the EOD-snapshot and aggregate-fallback code paths) DO
set this key explicitly and correctly today -- this file also pins those as
an unchanged regression guard.

CONTRACT (AC-6): hist_source is set explicitly at array-build time to the
true source ("shadow_history" where that's the source); the misleading
"post_mortem" default can never be served for shadow_history data.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import app as app_module

_APP_PY = pathlib.Path(__file__).parent.parent.parent / "app.py"


def _analytics_mock_with_shadow_series() -> MagicMock:
    """A minimal analytics stub whose bot/held series genuinely come from the
    shadow-sourced helper -- the exact shape `_compute_portfolio_strip` reads."""
    dates = [f"2026-06-{d:02d}" for d in range(1, 10)]
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 0.0, "dry_run": 0.0}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 10.0}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 5.0, "dry_run": 4.0}
    m.get_portfolio_daily_returns_from_shadow.return_value = (dates, [0.1] * len(dates))
    m.get_portfolio_bot_and_held_daily_returns.return_value = (
        dates,
        [0.1] * len(dates),
        [0.2] * len(dates),
    )
    m.compute_portfolio_annualized_vol.return_value = 0.1
    m.compute_windowed_portfolio_strip.return_value = {
        "guard_alpha": 1.0,
        "window": "30d",
        "cumulative_return": {"if_held": 10.0, "dry_run": 10.0},
    }
    return m


class TestComputePortfolioStripSetsHistSourceExplicitly:
    def test_hist_source_is_shadow_history_when_bot_and_held_series_are_populated(
        self, monkeypatch
    ):
        monkeypatch.setattr(app_module, "analytics", _analytics_mock_with_shadow_series())
        bot_state = {
            "sym-1": {
                "name": "Symphony One",
                "current_return": 1.0,
                "current_value": 5000.0,
                "simple_return": 0.1,
                "net_deposits": 1000.0,
                "time_weighted_return": 0.1,
                "max_drawdown": 0.05,
            }
        }

        strip = app_module._compute_portfolio_strip(bot_state, trading_day="2026-06-09")

        assert strip.get("hist_bot"), (
            "fixture sanity: strip.hist_bot must be genuinely populated for this "
            "test to be meaningful -- got an empty/falsy value."
        )
        assert strip.get("hist_source") == "shadow_history", (
            f"F-026 FAIL: _compute_portfolio_strip returned hist_source="
            f"{strip.get('hist_source')!r} (or the key is entirely absent) while "
            f"hist_bot/hist_held were populated from "
            f"analytics.get_portfolio_bot_and_held_daily_returns() -- a genuinely "
            f"shadow_history-sourced series. The key must be set explicitly at "
            f"array-build time, not left for _build_meta's "
            f"`ps.get('hist_source', 'post_mortem')` default to silently mislabel."
        )


class TestBuildMetaNeverServesThePostMortemDefaultForAShadowStrip:
    def test_meta_hist_source_is_shadow_history_end_to_end(self, monkeypatch):
        """End-to-end: the ACTUAL observable contract templates/JS consume is
        meta.portfolio.hist_source (app.py:1110's `ps.get('hist_source',
        'post_mortem')` read), not _compute_portfolio_strip's raw return alone.
        This proves the default fallback is never reached for a real
        shadow-sourced strip, not just that the upstream dict happens to carry
        the right key (which _build_meta could still discard/override)."""
        monkeypatch.setattr(app_module, "analytics", _analytics_mock_with_shadow_series())
        with patch.object(app_module, "_dotenv_module") as dotenv_mock:
            dotenv_mock.dotenv_values.return_value = {}
            bot_state = {
                "sym-1": {
                    "name": "Symphony One",
                    "current_return": 1.0,
                    "current_value": 5000.0,
                    "simple_return": 0.1,
                    "net_deposits": 1000.0,
                    "time_weighted_return": 0.1,
                    "max_drawdown": 0.05,
                }
            }
            strip = app_module._compute_portfolio_strip(bot_state, trading_day="2026-06-09")
            meta = app_module._build_meta(bot_state, market_state="open", portfolio_strip=strip)

        portfolio_meta = meta.get("portfolio") or {}
        assert portfolio_meta.get("hist_source") == "shadow_history", (
            f"F-026 FAIL: meta.portfolio.hist_source == "
            f"{portfolio_meta.get('hist_source')!r} -- the dashboard/JS-observable "
            f"field (app.py:1110, meta['portfolio']) must read 'shadow_history' "
            f"for a genuinely shadow-sourced strip, not silently fall through to "
            f"_build_meta's 'post_mortem' default."
        )


class TestOtherHistSourceBuildersUnchangedRegression:
    """Regression source-pin: the two OTHER app.py builders that already set
    hist_source correctly today (the EOD-snapshot path and the aggregate
    fallback path) must remain untouched by the F-026 fix -- AC-6 scopes the
    fix to _compute_portfolio_strip's missing key only."""

    def test_eod_snapshot_path_still_sets_shadow_history_literal(self):
        src = _APP_PY.read_text(encoding="utf-8")
        assert '"hist_source": "shadow_history",' in src, (
            "regression FAIL: the EOD-snapshot hist array builder no longer sets "
            'the literal "hist_source": "shadow_history" -- this pre-existing, '
            "already-correct builder must be untouched by the F-026 fix."
        )

    def test_aggregate_fallback_path_still_sets_post_mortem_literal(self):
        src = _APP_PY.read_text(encoding="utf-8")
        assert '"hist_source": "post_mortem",' in src, (
            "regression FAIL: the aggregate-fallback hist array builder no longer "
            'sets the literal "hist_source": "post_mortem" -- this pre-existing, '
            "already-correct builder must be untouched by the F-026 fix (it is a "
            "genuinely post_mortem-sourced fallback, unlike the _build_meta "
            "DEFAULT this cycle removes)."
        )
