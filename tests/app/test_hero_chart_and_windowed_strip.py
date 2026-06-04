"""
RED tests — AC-4b (real held chart series) + AC-3 (picker windows every metric).

AC-4b (HIGH) — the hero chart's "If held" line is the "Bot" line by construction.
  /api/hero-chart/<window> returns ``hist_held = bot_series`` (the SAME list object,
  app.py:1659). After any window-picker click the dashed If-held line traces Bot exactly,
  so the chart shows ZERO visual guard alpha regardless of the real divergence. The fix:
  /api/hero-chart must return a REAL held series distinct from bot wherever the guard
  diverged. These tests pin hist_held != hist_bot for a fixture whose bot/held daily
  returns differ.

AC-3 (HIGH) — the time picker only re-windows the chart + relabels; the headline guard
  alpha VALUE is window-independent all-time. The fix: a windowed strip endpoint whose
  guard-alpha (and CR/MDD/vol) value is computed FOR the selected window — 30/60/90/125,
  YTD, 1Y, and a NEW "All Time" (token "ALL") — value + label reflect the window. These
  tests pin: (1) the windowed-strip route exists and accepts all tokens incl ALL; (2) the
  returned guard-alpha value actually CHANGES across windows for a fixture whose per-window
  divergence differs; (3) the window token echoes back so the label can match the value.

Both intentionally FAIL on cdfa088 (held==bot; no windowed-value route). Render/route
correctness is necessary but NOT sufficient — the PM owns the live picker gate.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_WINDOW_TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "all"]


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        yield db_mock


# ---------------------------------------------------------------------------
# A distinct bot/held daily-return series — the guard diverged (held != bot).
# Returns are pct/day; the held path is what the position would have done WITHOUT
# the guard, the bot path is WITH the guard. They differ on the trigger days.
# ---------------------------------------------------------------------------

def _divergent_daily_series():
    dates = [f"2026-05-{d:02d}" for d in range(18, 23)] + [
        "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-06-01",
    ]
    bot_pct = [1.0, 0.5, -2.0, 0.3, 0.8, -1.0, 0.4, 1.2, -0.6, 0.9]
    held_pct = [1.0, 0.5, -2.0, 2.1, 0.8, -1.0, 0.4, 1.2, -0.6, 0.9]  # day 4 diverges
    return dates, bot_pct, held_pct


# ===========================================================================
# AC-4b — /api/hero-chart returns a REAL held series distinct from bot
# ===========================================================================

class TestHeroChartHeldSeriesIsDistinct:
    """hist_held must NOT be the bot series. For a divergent fixture the two compounded
    series must differ at the diverging step and at the terminal value.
    """

    @pytest.mark.parametrize("window", ["30d", "90d", "1y"])
    def test_hero_chart_held_not_identical_to_bot(self, client, mock_database, window):
        dates, bot_pct, held_pct = _divergent_daily_series()

        analytics_mock = MagicMock()
        # The NEW sibling contract risk-engine committed: a real (dates, bot, held) source.
        analytics_mock.get_portfolio_bot_and_held_daily_returns.return_value = (
            dates, bot_pct, held_pct
        )
        # Legacy single-series source still present; must NOT be used to fake held=bot.
        analytics_mock.get_portfolio_daily_returns_from_shadow.return_value = (dates, bot_pct)

        with patch.object(app_module, "analytics", analytics_mock):
            resp = client.get(f"/api/hero-chart/{window}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "hist_bot" in body and "hist_held" in body, (
            f"hero-chart must return hist_bot + hist_held; got {list(body.keys())}"
        )
        hist_bot = body["hist_bot"]
        hist_held = body["hist_held"]
        assert hist_bot and hist_held, "both series must be non-empty for a divergent fixture"
        assert hist_held != hist_bot, (
            "AC-4b FAIL: /api/hero-chart hist_held is identical to hist_bot (app.py:1659 "
            "returns hist_held = bot_series). The 'If held' line traces Bot exactly -> zero "
            "visual guard alpha by construction. Held must be a REAL distinct series."
        )

    def test_hero_chart_held_terminal_differs_when_guard_diverged(self, client, mock_database):
        """The compounded terminal held value must differ from the terminal bot value for
        a series where held outperformed bot on the trigger day (guard exited early)."""
        dates, bot_pct, held_pct = _divergent_daily_series()
        analytics_mock = MagicMock()
        analytics_mock.get_portfolio_bot_and_held_daily_returns.return_value = (
            dates, bot_pct, held_pct
        )
        analytics_mock.get_portfolio_daily_returns_from_shadow.return_value = (dates, bot_pct)

        with patch.object(app_module, "analytics", analytics_mock):
            resp = client.get("/api/hero-chart/90d")
        body = resp.get_json()
        # The fixture's held path compounds higher than bot (it didn't exit early on day 4).
        assert body["hist_held"][-1] != pytest.approx(body["hist_bot"][-1], abs=1e-9), (
            "AC-4b FAIL: terminal held value equals terminal bot value despite a divergent "
            "daily series — held line is fabricated from bot."
        )


# ===========================================================================
# AC-3 — a windowed strip endpoint whose guard-alpha VALUE changes per window
# ===========================================================================

class TestWindowedStripValueRespectsPicker:
    """The headline guard alpha must be recomputed FOR the selected window. A route that
    returns a window-independent all-time value (today's bug) fails the cross-window
    change assertion and the ALL-token support.
    """

    def _windowed_analytics(self):
        """analytics mock whose windowed strip returns a DIFFERENT guard alpha per window —
        so the test can prove the route threads the window through to the value."""
        per_window_alpha = {
            "30d": 2.1, "60d": 1.7, "90d": 1.4, "125d": 1.1,
            "ytd": 0.9, "1y": 0.6, "all": 1.685,
        }

        def _strip(symphonies, bot_state, *, window, db_path=None):
            wkey = str(window).lower()
            alpha = per_window_alpha.get(wkey, 0.0)
            return {
                "today_change": {"if_held": 0.0, "dry_run": 0.0},
                "cumulative_return": {"if_held": 10.0, "dry_run": 10.0 + alpha},
                "max_drawdown": {"if_held": 5.0, "dry_run": 5.0},
                "vol_bot": None, "vol_held": None,
                "guard_alpha": alpha,
                "insufficient_history": False,
                "window": wkey,
            }

        m = MagicMock()
        m.compute_windowed_portfolio_strip.side_effect = _strip
        return m, per_window_alpha

    def _resolve_windowed_strip(self, client, window):
        """Hit the windowed-strip surface and return its JSON. The route shape is the
        flask-locked contract; this helper tolerates either /api/strip/<window> or a
        ?window= param so the test pins BEHAVIOR (value changes), not a brittle path."""
        resp = client.get(f"/api/strip/{window}")
        if resp.status_code == 404:
            resp = client.get(f"/api/state?window={window}")
        return resp

    @pytest.mark.parametrize("window", _WINDOW_TOKENS)
    def test_windowed_strip_route_serves_every_token_via_compute_windowed_strip(
        self, client, mock_database, window
    ):
        """The DEDICATED windowed-strip route must exist (no /api/state fallback) AND
        actually call compute_windowed_portfolio_strip with the requested window. A
        generic /api/state that ignores the window must NOT satisfy this — so we assert
        the dedicated route 200s and that the windowed strip fn received this window."""
        analytics_mock, _ = self._windowed_analytics()
        with patch.object(app_module, "analytics", analytics_mock), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            resp = client.get(f"/api/strip/{window}")
        assert resp.status_code == 200, (
            f"AC-3 FAIL: the dedicated /api/strip/{window} route is missing or rejected the "
            f"token (status {resp.status_code}). It must accept 30d/60d/90d/125d/ytd/1y AND "
            "the NEW 'all' (All Time) token — a window-ignoring /api/state does not count."
        )
        # The route must thread THIS window into the windowed strip computation.
        called_windows = [
            str(c.kwargs.get("window", c.args[2] if len(c.args) > 2 else None)).lower()
            for c in analytics_mock.compute_windowed_portfolio_strip.call_args_list
        ]
        assert window.lower() in called_windows, (
            f"AC-3 FAIL: /api/strip/{window} did not call compute_windowed_portfolio_strip "
            f"with window={window!r} (saw {called_windows}). The route must compute the "
            "strip FOR the requested window, not return an all-time value."
        )

    def test_windowed_guard_alpha_value_changes_across_windows(self, client, mock_database):
        """The returned guard-alpha value must DIFFER across windows for a fixture whose
        per-window divergence differs. A window-independent value (the current bug) yields
        the same number for every window and fails here."""
        analytics_mock, per_window_alpha = self._windowed_analytics()

        observed = {}
        with patch.object(app_module, "analytics", analytics_mock), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            for window in ["30d", "90d", "all"]:
                resp = self._resolve_windowed_strip(client, window)
                assert resp.status_code == 200, f"window {window} -> {resp.status_code}"
                body = resp.get_json()
                # Pull the guard alpha however the strip surfaces it (explicit field or
                # cumulative dry_run - if_held). Derived from the response, not hardcoded.
                if isinstance(body, dict) and "guard_alpha" in body:
                    observed[window] = body["guard_alpha"]
                else:
                    cr = (body.get("portfolio_strip") or body).get("cumulative_return", {})
                    observed[window] = cr.get("dry_run", 0) - cr.get("if_held", 0)

        assert len(set(round(v, 6) for v in observed.values())) > 1, (
            "AC-3 FAIL: the windowed guard-alpha value is identical across 30d/90d/all "
            f"({observed}). The picker is not threading the window into the headline value "
            "— it stays the window-independent all-time figure (the F1 bug)."
        )

    def test_windowed_strip_echoes_selected_window_for_honest_label(
        self, client, mock_database
    ):
        """The response must echo the resolved window so the label matches the value
        (the '30d' label on an all-time value is the F1 dishonesty)."""
        analytics_mock, _ = self._windowed_analytics()
        with patch.object(app_module, "analytics", analytics_mock), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            resp = self._resolve_windowed_strip(client, "all")
        assert resp.status_code == 200
        body = resp.get_json()
        window_field = (
            body.get("window")
            if isinstance(body, dict) and "window" in body
            else (body.get("portfolio_strip") or {}).get("window")
        )
        assert window_field is not None and str(window_field).lower() == "all", (
            f"AC-3 FAIL: windowed strip did not echo the resolved window ('all'); got "
            f"{window_field!r}. The label must reflect the actual window driving the value."
        )
