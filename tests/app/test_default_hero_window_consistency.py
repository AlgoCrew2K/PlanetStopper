"""
RED — the DEFAULT hero render must equal the windowed DEFAULT-window strip (PM live-gate bug).

THE BUG (PM live gate, 2026-06-04): on page load the hero shows the OLD un-windowed value with
a STATIC "30d" label, inconsistent with the picker:
  - default SSR/poll hero: CUMULATIVE Bot ~+67% (account-lifetime), label hard-coded "30d"
    (templates/index.html:791, button window-30d hard-coded class="active" :801).
  - /api/strip/30d (compute_windowed_portfolio_strip): Bot ~+29% (VW/shadow basis, ~18-day span).
  -> "30D" (default) shows Bot 67% while "All Time" shows Bot 29% — BACKWARDS (a 30-day window
     cannot exceed all-time). The default is the un-windowed number mislabeled "30d"; the first
     picker click jumps the hero.

ROOT (file:line):
  - DEFAULT _compute_portfolio_strip (app.py:688-705) puts CR on the ACCOUNT basis via
    get_portfolio_cumulative_return_account_basis (if_held = Composer lifetime simple_return ~66%).
  - WINDOWED compute_windowed_portfolio_strip (analytics.py:1496-1499) puts CR on the VW basis
    (if_held = get_portfolio_cumulative_return ~28%, cash-excluded, bounded to the shadow span) +
    the windowed guard alpha. Docstring analytics.py:1448-1450 states Bot/Held are BOTH VW within a
    window and the account rescale applies ONLY to the all-time hero scalar.
  So the two paths use DIFFERENT if_held BASES -> different headline numbers on the same control.

THE CONTRACT (whatever basis is finally chosen): the DEFAULT hero strip the page renders on load
(SSR + the regular /api/state poll) MUST equal the windowed strip for the DEFAULT window — same
Bot, Held, guard alpha, and the label must name that window. So the page loads consistent with the
picker and the first click does not jump.

These FAIL today (default != windowed-default). They pin consistency; the BASIS decision (account
vs VW within a window) is a separate product call escalated to the PM/user — but EITHER basis must
be applied CONSISTENTLY to both the default and the picker.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import analytics
import app as app_module
import database as _db

# The picker's default window (templates/index.html:791 label + :801 active button).
_DEFAULT_WINDOW = "30d"

_SCHEMA = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symphony_id TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        trading_day TEXT NOT NULL,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        position_epoch TEXT
    )
"""


def _make_db(tmp_path: Path) -> str:
    """A shadow_history DB with one triggered symphony whose divergence is within the window
    (so the windowed default strip is well-defined and non-trivial)."""
    db_file = str(Path(tmp_path) / "default_hero_shadow.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    # Recent days (so a 30d window includes them); one trigger-day divergence.
    rows = [
        ("2026-05-26", -2.0, -2.0, "E1"),
        ("2026-05-27", 1.0, 1.0, "E1"),
        ("2026-05-28", 0.5, 0.5, "E1"),
        ("2026-05-29", 2.0, 0.3, "E1"),   # guard diverged here
        ("2026-06-01", 0.4, 0.4, "E1"),
    ]
    for day, shadow, current, epoch in rows:
        conn.execute(
            "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, current_return, "
            "shadow_return, is_post_trigger, position_epoch) VALUES (?,?,?,?,?,?,?)",
            ("sym-x", day + "T20:00:00Z", day, current, shadow, 0, epoch),
        )
    conn.commit()
    conn.close()
    _db._shadow_cr_cache.clear()
    return db_file


def _symphonies():
    return [{
        "id": "sym-x", "value": 10000.0, "last_percent_change": 0.015,
        "simple_return": 0.30, "net_deposits": 1000.0, "time_weighted_return": 0.30,
        "max_drawdown": 0.08, "trading_day": "2026-06-01",
    }]


# ===========================================================================
# Analytics-level: default-window windowed strip must be the canonical default
# ===========================================================================

class TestWindowedDefaultIsWellDefined:
    def test_default_window_strip_has_a_guard_alpha(self, tmp_path):
        """The windowed strip for the DEFAULT window must produce a defined guard alpha for a
        symphony that diverged inside the window — this is the value the default hero must show."""
        db_file = _make_db(tmp_path)
        strip = analytics.compute_windowed_portfolio_strip(
            _symphonies(), {}, window=_DEFAULT_WINDOW, db_path=db_file
        )
        assert strip["window"] == _DEFAULT_WINDOW, (
            f"windowed strip must echo the default window; got {strip['window']!r}"
        )
        assert strip["guard_alpha"] is not None, (
            "the default-window strip must define a guard alpha (the symphony diverged in-window)"
        )


# ===========================================================================
# Route-level: the SSR/default-poll hero must match /api/strip/<default>
# ===========================================================================

@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _live_bot_state():
    return {
        "sym-x": {
            "name": "Symphony X", "account": "ACC1", "current_value": 10000.0,
            "current_return": 1.5, "simple_return": 0.30, "net_deposits": 1000.0,
            "time_weighted_return": 0.30, "max_drawdown": 0.08,
            "armed": True, "tp_armed": False, "para_armed": False, "triggered": False,
        }
    }


class TestDefaultHeroMatchesWindowedDefault:
    def test_default_poll_hero_cr_equals_windowed_default_cr(self, client, monkeypatch):
        """/api/state (the default poll the page renders on load) hero cumulative_return must
        EQUAL /api/strip/<default-window> cumulative_return — same Bot (dry_run), Held (if_held),
        and guard alpha. A mismatch is the load-vs-click jump the PM saw.

        Populates _account_totals_cache (as the LIVE daemon does via _refresh_account_totals)
        so the default strip takes the ACCOUNT-basis branch (app.py:689-705) — the exact live
        condition where the default hero diverges from the VW windowed strip."""
        from unittest.mock import patch
        # The live account cache: portfolio_cr ~66% (Composer lifetime), value > symphony sum
        # (cash present). This is what makes the default hero ~67% while /api/strip/30d is ~29%.
        monkeypatch.setattr(
            app_module, "_account_totals_cache",
            {"portfolio_cr": 66.0, "portfolio_tc": 0.5, "portfolio_mdd": 24.47,
             "portfolio_value": 12945.18},
            raising=False,
        )
        with patch.object(app_module.database, "load_state", return_value=_live_bot_state()), \
             patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            state = client.get("/api/state").get_json()
            strip = client.get(f"/api/strip/{_DEFAULT_WINDOW}").get_json()

        # The default poll exposes the portfolio strip under portfolio_strip (poll path).
        ps = state.get("portfolio_strip") or {}
        default_cr = (ps.get("cumulative_return") or {})
        windowed_cr = (strip.get("cumulative_return") or {})
        assert default_cr and windowed_cr, (
            f"both strips must carry cumulative_return; default={default_cr} windowed={windowed_cr}"
        )

        # abs=1e-6: the default hero and the default-window strip must be the SAME number on
        # the SAME basis. (They differ today: account-basis default ~67% vs VW windowed ~29%.)
        assert default_cr.get("dry_run") == pytest.approx(windowed_cr.get("dry_run"), abs=1e-6), (
            f"DEFAULT-HERO FAIL: default poll Bot (dry_run) {default_cr.get('dry_run')} != "
            f"windowed default Bot {windowed_cr.get('dry_run')}. The page loads an un-windowed "
            "value, then the first picker click jumps it. Default and picker must share ONE basis."
        )
        assert default_cr.get("if_held") == pytest.approx(windowed_cr.get("if_held"), abs=1e-6), (
            f"DEFAULT-HERO FAIL: default Held {default_cr.get('if_held')} != windowed default Held "
            f"{windowed_cr.get('if_held')} — incommensurable bases on the same hero control."
        )

    def test_default_hero_window_label_matches_default_window(self):
        """The hero window label must NAME the default window the value is computed for (not a
        static literal divorced from the value). If the default value is the 30d window, the label
        is '30d'; if the chosen default is all-time, the label must say so — never a fixed lie."""
        html = Path(app_module.__file__).parent.joinpath("templates", "index.html").read_text(
            encoding="utf-8"
        )
        # The label span must be driven by the SAME default-window token as the active picker
        # button — pin that the active button's window and the label are the same token, so they
        # cannot drift (a hard-coded label that disagrees with the active button is the F1 lie).
        import re
        active = re.search(r'data-window="([^"]+)"[^>]*class="[^"]*\bactive\b', html) or \
            re.search(r'class="[^"]*\bactive\b[^"]*"[^>]*data-window="([^"]+)"', html)
        assert active, "an active picker button with a data-window token must exist"
        active_window = active.group(1)
        # The label span's default text must match the active window token (case-insensitive).
        label = re.search(r'id="guard-alpha-window-label"[^>]*>([^<]+)<', html)
        assert label, "the guard-alpha-window-label span must exist"
        assert label.group(1).strip().lower() == active_window.lower(), (
            f"DEFAULT-LABEL FAIL: hero label '{label.group(1).strip()}' != active picker window "
            f"'{active_window}'. The default label must name the default window the value reflects."
        )
