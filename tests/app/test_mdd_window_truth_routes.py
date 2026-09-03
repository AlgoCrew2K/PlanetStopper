"""
RED tests — feature-plans/mdd-window-truth.md route/render ACs (AC-1 render,
AC-2 render, AC-4, AC-5). DE-PERF-WINDOW-TRUTH-001.

Covers:
  AC-1 (render): app._compute_portfolio_strip's warm-cache branch must stop
    special-casing `_account_totals_cache["portfolio_mdd"]` (Composer's
    lifetime scalar) as the if_held comparison leg -- if_held must come from
    the (now window-genuine) analytics.get_portfolio_max_drawdown call
    regardless of cache warmth. Mirrors the established
    test_mdd_honest_framing.py pattern: mock app_module.analytics wholesale,
    hit the real "/" route, assert on rendered HTML.
  AC-2 (render): the Composer lifetime scalar remains available as its OWN
    clearly-labelled figure, naming its invested_since start where available
    -- never silently absorbed as a comparison leg.
  AC-4 (rendered disclosure, not a payload key): GET /performance's rendered
    page must have a render target the JS-driven coverage disclosure writes
    into that is NOT the pre-existing 30-observation stability banner (kept
    separate per the plan's Decision table).
  AC-5: GET /api/performance and GET /api/history/<days> gain honest
    actual_days/coverage_days/date_range fields; window_days keeps its
    "requested" meaning (not redefined) per the plan's exact wording.

-n0 only; no live API; database/analytics mocked or DB_FILE-overridden per the
established seams (analytics.DB_FILE, patch.object(app_module, "analytics")).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_SHADOW_SCHEMA = """
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


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clear_account_totals_cache():
    app_module._account_totals_cache.clear()
    yield
    app_module._account_totals_cache.clear()


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_triggers.return_value = []
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        yield db_mock


def _seed_shadow_db(path: Path, symphony_id: str, rows: list[dict]) -> str:
    db_file = str(path)
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA)
    for row in rows:
        trading_day = row.get("trading_day") or (
            date.today() - timedelta(days=row["days_ago"])
        ).isoformat()
        conn.execute(
            "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, current_return, "
            "shadow_return, is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                symphony_id,
                trading_day + "T20:00:00Z",
                trading_day,
                row["current_return"],
                row["shadow_return"],
                row.get("is_post_trigger", 0),
                row.get("position_epoch", "EPOCH_A"),
            ),
        )
    conn.commit()
    conn.close()
    return db_file


def _analytics_mock_sufficient_history(
    *, mdd_if_held: float, mdd_dry_run: float, mdd_if_held_lifetime: float | None = None
) -> MagicMock:
    """A >=30-day shadow history (so the AC-4c sufficiency gate does NOT
    suppress the MDD row -- this file is testing AC-1/AC-2 render content,
    which the sufficiency gate would otherwise hide).

    Return-dict shape matches the CONFIRMED contract redesign
    (docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md): if_held/dry_run are
    the redefined comparable pair; if_held_lifetime is the Composer scalar
    under its own key (AC-2); n_obs is always an int.
    """
    dates = [f"2026-05-{d:02d}" for d in range(1, 31)]  # 30 days
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 0.5, "dry_run": 0.4}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_portfolio_max_drawdown.return_value = {
        "if_held": mdd_if_held,
        "dry_run": mdd_dry_run,
        "if_held_lifetime": mdd_if_held_lifetime,
        "n_obs": 30,
    }
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 12.0, "dry_run": 12.0}
    m.get_symphony_max_drawdown.return_value = {
        "if_held": mdd_if_held,
        "dry_run": mdd_dry_run,
        "if_held_lifetime": mdd_if_held_lifetime,
        "n_obs": 30,
    }
    m.get_portfolio_daily_returns_from_shadow.return_value = (dates, [0.01] * 30)
    m.get_portfolio_bot_and_held_daily_returns.return_value = None
    m.compute_portfolio_annualized_vol.return_value = 0.1
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = (dates, [0.01] * 30, [0.01] * 30)
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


def _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, bot_state: dict) -> None:
    """[Added during review, DE-PERF-WINDOW-TRUTH-001, root-caused by
    mdd-ui]: tests/app/conftest.py's autouse `_stub_get_api_state_dict`
    fixture (pre-existing, predates this cycle -- commit 650b8514) replaces
    `app_module.get_api_state_dict` wholesale with a fixed stub dict that
    has NO "portfolio_strip" key at all, for EVERY test in tests/app/.
    `dashboard()`'s `portfolio_strip = api_state.get("portfolio_strip") or
    {}` has no fallback computation (unlike bot_state, which falls back to
    `database.load_state()`) -- so under that stub, portfolio_strip is
    unconditionally {} regardless of what this test's own `analytics`/
    `database` mocks are configured to return. Confirmed independently
    (read tests/app/conftest.py + app.py:1483 directly) before writing this
    workaround -- not the app.py bug it first looked like (see this
    module's earlier commits' now-superseded diagnosis).

    Fix: a test-body monkeypatch.setattr call executes AFTER the autouse
    fixture's context-managed patch has already applied, so it simply
    overrides get_api_state_dict for the remainder of this test (auto-
    reverts at teardown like any other monkeypatch/fixture interaction).
    Deliberately does NOT call the REAL get_api_state_dict() (that would
    reach engine.exit_authority.get_exit_authority() with no live engine --
    exactly the 500-on-jsonify failure mode the original stub exists to
    prevent, per its own docstring) -- instead reuses the SAME safe stub
    shape for every OTHER key, and computes a genuine portfolio_strip via
    app_module._compute_portfolio_strip(bot_state), which uses the
    already-mocked app_module.analytics/database this test configured.
    """
    real_portfolio_strip = app_module._compute_portfolio_strip(bot_state)
    stub = {
        "bot_state": {},
        "is_locked": False,
        "port_state": {},
        "exit_authority": {},
        "daemon_started_at": None,
        "portfolio_strip": real_portfolio_strip,
    }
    monkeypatch.setattr(app_module, "get_api_state_dict", lambda: stub)


def _minimal_bot_state() -> dict:
    return {
        "sym-x": {
            "name": "Symphony X",
            "account": "ACC1",
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": 1.5,
            "current_value": 10000.0,
            "stop_trigger": -2.0,
            "mc_prob": 40.0,
            "simple_return": 0.12,
            "net_deposits": 1000.0,
            "time_weighted_return": 0.12,
            "max_drawdown": 0.9999,  # deliberately far from mdd_if_held/mdd_dry_run below
        }
    }


# ===========================================================================
# AC-1 (render) — the warm-cache lifetime-scalar override must be gone
# ===========================================================================


class TestAC1WarmCacheNoLongerOverridesIfHeld:
    def test_warm_cache_scalar_does_not_win_over_analytics_windowed_value(
        self, client, mock_database, monkeypatch
    ):
        """AC-1: today, when _account_totals_cache['portfolio_mdd'] is warm,
        app.py sets if_held = abs(cached scalar) UNCONDITIONALLY, ignoring
        analytics.get_portfolio_max_drawdown's if_held entirely. Post-fix,
        if_held must be the analytics (window-genuine) value regardless of
        cache warmth -- the whole POINT of AC-1 is that a lifetime Composer
        scalar must never be the comparison leg."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        # Warm cache: a DIFFERENT value than the analytics mock's if_held, so
        # the two are trivially distinguishable in the rendered output.
        # MUST be set BEFORE _stub_get_api_state_dict_with_real_portfolio_strip
        # below, since that helper calls _compute_portfolio_strip immediately
        # -- the warm-vs-cold branch it takes depends on the cache's state at
        # call time, not at request time.
        stale_composer_scalar_pct = -173.2  # abs() would render 173.20 -- far from 10.59
        app_module._account_totals_cache["portfolio_mdd"] = stale_composer_scalar_pct
        app_module._account_totals_cache["portfolio_value"] = 20000.0

        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200, f"dashboard render failed: {resp.status_code}"
        html = resp.get_data(as_text=True)

        anchor = html.find('data-testid="comp-mdd-held-text"')
        assert anchor != -1, "rendered page must contain the Max DD held text span"
        snippet = html[anchor : anchor + 200]

        assert "173.20" not in snippet and "173.2" not in snippet, (
            f"AC-1 FAIL: the rendered Max DD Held figure still reflects the warm-"
            f"cache Composer lifetime scalar (abs({stale_composer_scalar_pct}) = "
            f"173.20) instead of the genuine windowed if_held (10.59) the "
            f"analytics mock supplied. Snippet: {snippet!r}. The app.py warm-"
            f"cache branch (`if _cached_mdd is not None: max_drawdown = "
            f"{{'if_held': abs(_cached_mdd), ...}}`) must be removed -- if_held "
            f"comes from analytics.get_portfolio_max_drawdown unconditionally."
        )
        assert "10.59" in snippet or "10.58" in snippet or "10.60" in snippet, (
            f"AC-1 FAIL: expected the genuine windowed if_held (~10.5875, "
            f"rendered to 2dp as 10.59/10.58/10.60 depending on rounding) in "
            f"the Held text; got snippet: {snippet!r}"
        )

    def test_cold_cache_still_renders_analytics_value(self, client, mock_database, monkeypatch):
        """Anti-regression: the cold-cache path (no special-casing today)
        must continue to render the analytics value after the warm-cache
        branch is removed."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        # cache left empty by the autouse fixture -> cold path
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        anchor = html.find('data-testid="comp-mdd-held-text"')
        assert anchor != -1
        snippet = html[anchor : anchor + 200]
        assert "10.59" in snippet or "10.58" in snippet or "10.60" in snippet


# ===========================================================================
# AC-2 (render) — Composer lifetime scalar rendered as its OWN figure
# ===========================================================================


class TestAC2LifetimeScalarRenderedSeparately:
    """[Updated, DE-PERF-WINDOW-TRUTH-001, AC-2 AMENDED per team-lead ruling
    2026-09-03]: the separate lifetime figure is labelled GENERICALLY --
    "Lifetime Max Drawdown · since inception" -- NOT with the actual
    invested_since date (not persisted anywhere reachable without violating
    AC-7's alpha_bot_execution.py freeze; see feature-plans/mdd-window-
    truth.md's amended AC-2 + Decisions table)."""

    _RULED_LABEL_FRAGMENTS = ("Lifetime Max Drawdown", "since inception")

    def test_page_renders_a_lifetime_figure_distinct_from_the_vs_row(
        self, client, mock_database, monkeypatch
    ):
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'data-testid="mdd-lifetime-scalar"' in html, (
            "AC-2 FAIL: the page must render a SEPARATE, clearly-labelled "
            "Composer lifetime max_drawdown figure (data-testid="
            "'mdd-lifetime-scalar') -- distinct from the Bot-vs-Held vs-row "
            "comparison legs, which no longer read this field. Not found in "
            "rendered HTML."
        )
        # It must not live INSIDE the vs-row comparison block (which would
        # re-introduce it as a de-facto third comparison leg).
        vs_row_anchor = html.find('data-testid="comp-mdd-bot-text"')
        lifetime_anchor = html.find('data-testid="mdd-lifetime-scalar"')
        next_vs_row = html.find('data-testid="vs-row"', vs_row_anchor + 1)
        mdd_vs_row_end = next_vs_row if next_vs_row != -1 else vs_row_anchor + 1500
        assert not (vs_row_anchor <= lifetime_anchor < mdd_vs_row_end), (
            "AC-2 FAIL: the lifetime-scalar figure must NOT be rendered inside "
            "the Max-DD vs-row comparison block -- it is a separate, clearly-"
            "labelled figure, not a third comparison leg."
        )

    def test_lifetime_figure_uses_the_ruled_generic_label_not_a_date(
        self, client, mock_database, monkeypatch
    ):
        """AC-2's team-lead-amended label text: 'Lifetime Max Drawdown ·
        since inception', with NO invested_since date rendered this cycle
        (per feature-plans/mdd-window-truth.md's Decisions table -- fetching
        invested_since would need either a new Composer call, out of scope,
        or touching alpha_bot_execution.py, forbidden by AC-7)."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        anchor = html.find('data-testid="mdd-lifetime-scalar"')
        assert anchor != -1
        snippet = html[max(0, anchor - 250) : anchor + 250]
        for fragment in self._RULED_LABEL_FRAGMENTS:
            assert fragment in snippet, (
                f"AC-2 FAIL: the ruled label fragment {fragment!r} was not "
                f"found near the lifetime-scalar figure. Ruled copy: "
                f"'Lifetime Max Drawdown · since inception'. Snippet: {snippet!r}"
            )
        # [Corrected during review, self-caught false positive]: this
        # test's first draft checked the WHOLE rendered page for any
        # occurrence of the word "invested_since", which false-failed
        # against a legitimate documentation comment in the template
        # source explaining WHY the date isn't rendered ("No invested_since
        # date rendered this cycle -- not persisted..."). A comment
        # documenting the absence is not the same defect as actually
        # rendering the value. The real signal is whether a DATE VALUE
        # appears in the rendered element's own visible text -- checked
        # here via an ISO-date-shaped pattern in the scoped snippet
        # (a Jinja comment is stripped by the template engine and never
        # reaches the rendered HTML at all, so this scoped check cannot
        # false-positive on it the way the whole-page word search did).
        _iso_date_pattern = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
        date_matches = _iso_date_pattern.findall(snippet)
        assert not date_matches, (
            f"AC-2 explicitly rules OUT rendering the real invested_since date "
            f"this cycle (not persisted anywhere reachable without violating "
            f"AC-7) -- found date-shaped value(s) {date_matches!r} rendered "
            f"near the lifetime-scalar figure. Snippet: {snippet!r}"
        )

    def test_lifetime_scalar_degrades_to_dash_when_meta_portfolio_dict_lacks_the_key_entirely(
        self, client, mock_database, monkeypatch
    ):
        """Regression guard (requested by team-lead via mdd-ui, 2026-09-03)
        for the Jinja Undefined-vs-None crash class fixed at `4800872c`:
        `meta.portfolio.mdd_if_held_lifetime` (bare dot-access) returns
        Jinja's `Undefined` sentinel -- a DISTINCT object from Python
        `None` -- on any path where `meta.portfolio` is a genuine dict that
        simply lacks this key. `is not none` does NOT catch `Undefined`, so
        the `|abs` filter below it crashed with `TypeError`. This crash was
        found only incidentally by `tests/analytics/test_portfolio_vol_
        computation.py` (a test whose actual purpose is unrelated --
        vol computation -- that happens to render `dashboard()` with a
        context predating this cycle's new keys); that test has no
        obligation to keep exercising this shape if it's ever refactored,
        so this is the dedicated tripwire.

        THE EXACT DEFECT SHAPE (per mdd-ui, verified against the fix at
        templates/index.html:1086): `meta.portfolio` must be a REAL dict
        that is simply MISSING the key -- not `None`, not an absent
        `meta`/`meta.portfolio` entirely (those paths are already covered
        by the pre-existing `meta is defined`/`meta.portfolio is defined`
        guards and can't reproduce this class). `_build_meta` itself
        unconditionally sets this key today (possibly to `None`, but never
        omits it) -- so this test intercepts `_build_meta`'s OWN return
        value and surgically pops the key, simulating a caller (a future
        route, a stale cached value, a partial refactor) that predates this
        cycle's schema, rather than trying to coerce today's `_build_meta`
        into producing that shape naturally.
        """
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        _real_build_meta = app_module._build_meta

        def _build_meta_missing_lifetime_key(*args, **kwargs):
            meta = _real_build_meta(*args, **kwargs)
            assert "mdd_if_held_lifetime" in meta["portfolio"], (
                "test precondition: _build_meta must normally set this key "
                "(possibly to None) -- if it's already absent, this test "
                "isn't exercising the intended before/after contrast"
            )
            # The exact defect shape: pop the key so it's genuinely ABSENT
            # from an otherwise-real dict (not set to None).
            meta["portfolio"].pop("mdd_if_held_lifetime")
            return meta

        monkeypatch.setattr(app_module, "_build_meta", _build_meta_missing_lifetime_key)

        resp = client.get("/")
        assert resp.status_code == 200, (
            f"AC-2 regression-guard FAIL: dashboard render raised/500'd when "
            f"meta.portfolio lacks 'mdd_if_held_lifetime' entirely (the "
            f"Jinja Undefined-vs-None crash class fixed at 4800872c) -- got "
            f"{resp.status_code}. Body: {resp.get_data(as_text=True)[:500]!r}"
        )
        html = resp.get_data(as_text=True)
        anchor = html.find('data-testid="mdd-lifetime-scalar"')
        assert anchor != -1, "rendered page must still contain the lifetime-scalar element"
        snippet = html[anchor : anchor + 250]
        assert "&mdash;" in snippet, (
            f"expected the lifetime figure to gracefully degrade to the "
            f"em-dash empty state ('&mdash;') when the key is genuinely "
            f"absent from meta.portfolio, not raise and not fabricate a "
            f"value. Snippet: {snippet!r}"
        )

    def test_percard_lifetime_mdd_none_renders_dash_not_zero_on_ssr(
        self, client, mock_database, monkeypatch
    ):
        """[mdd-review handoff, DE-PERF-WINDOW-TRUTH-001, 2026-09-03, BLOCK
        finding, freeze lifted for this fix] Case 1 of 3: dashboard()'s
        per-symphony `_mdd` build (app.py:~1567-1568) calls
        `_safe_analytics(analytics.get_symphony_max_drawdown, ...)` with
        the DEFAULT `coerce_none=True`, which does
        `{k: (v if v is not None else 0.0) ...}` across the WHOLE returned
        dict -- destroying a genuine `if_held_lifetime=None` (symphony has
        no Composer max_drawdown scalar, a real named plan edge case)
        BEFORE the template's correct `is not none` guard ever sees it.
        `_tc`'s sibling call already opts out (`coerce_none=False`, citing
        this exact bug class as F-016) -- `_mdd` never got the same
        treatment. The guard is correct; the bug is one call earlier."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=None,
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)

        val_anchor = html.find('data-field="mdd-lifetime"')
        assert val_anchor != -1, "per-card mdd-lifetime value span not found in rendered HTML"
        close = html.find(">", val_anchor)
        end = html.find("</span>", close)
        rendered_value = html[close + 1 : end].strip()
        assert rendered_value == "—", (  # em-dash, matches the template's &mdash;
            f"EXPECTED honest em-dash for a None Composer lifetime MDD scalar on "
            f"initial SSR page load; got fabricated rendered value: {rendered_value!r}. "
            f"Root cause: app.py's dashboard() builds _s['_mdd'] via "
            f"_safe_analytics(..., coerce_none=True default), which converts the "
            f"genuine if_held_lifetime=None into 0.0 before the template's correct "
            f"none-guard ever sees it."
        )


# ===========================================================================
# mdd-review BLOCK finding, case 2 of 3 (handoff, 2026-09-03): the MORE
# SEVERE sibling of the lifetime-figure bug above -- the ACTUAL comparison
# legs (mdd-bot/mdd-held), not just the lifetime figure, for a thin-history
# symphony. Own class because it needs a hand-built mock
# (_analytics_mock_sufficient_history hardcodes n_obs=30/non-None legs).
# ===========================================================================


class TestSSRCoerceNoneFabricatesZeroForThinHistorySymphony:
    def test_percard_bot_held_mdd_none_renders_dash_not_zero_for_thin_history_symphony(
        self, client, mock_database, monkeypatch
    ):
        """A thin-history symphony (n_obs=1, e.g. newly added, <2 days of
        shadow_history -- per docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md
        Design section point 3, if_held/dry_run can now genuinely be None
        where if_held previously almost never was) renders the ACTUAL
        Bot/Held comparison-leg cells as a fabricated '0.0%' on initial SSR
        page load, not the plan's mandated honest '--'. Same root cause as
        the lifetime-figure test above (_safe_analytics's coerce_none=True
        default at app.py's dashboard() _mdd build), same fix."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        m = MagicMock()
        m.get_portfolio_today_change.return_value = {"if_held": 0.5, "dry_run": 0.4}
        m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
        m.get_portfolio_max_drawdown.return_value = {
            "if_held": 10.5875, "dry_run": 10.3622, "if_held_lifetime": 12.0, "n_obs": 30,
        }
        m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
        m.get_symphony_cumulative_return.return_value = {"if_held": 12.0, "dry_run": 12.0}
        # KEY: thin-history symphony. if_held_lifetime present (Composer scalar
        # IS available) to isolate this from test 1's scenario above.
        m.get_symphony_max_drawdown.return_value = {
            "if_held": None, "dry_run": None, "if_held_lifetime": 12.0, "n_obs": 1,
        }
        dates30 = [f"2026-05-{d:02d}" for d in range(1, 31)]
        m.get_portfolio_daily_returns_from_shadow.return_value = (dates30, [0.01] * 30)
        m.get_portfolio_bot_and_held_daily_returns.return_value = None
        m.compute_portfolio_annualized_vol.return_value = 0.1
        m.get_history_with_cache_invalidation.return_value = {}
        m.compute_aggregate_returns.return_value = (dates30, [0.01] * 30, [0.01] * 30)
        m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
        monkeypatch.setattr(app_module, "analytics", m)

        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)

        for field in ("mdd-bot", "mdd-held"):
            val_anchor = html.find(f'data-field="{field}"')
            assert val_anchor != -1, f"{field} value span not found in rendered HTML"
            close = html.find(">", val_anchor)
            end = html.find("</span>", close)
            rendered_value = html[close + 1 : end].strip()
            assert rendered_value == "--", (
                f"EXPECTED honest '--' for a None {field} (thin-history symphony, "
                f"n_obs=1) on initial SSR page load; got fabricated rendered "
                f"value: {rendered_value!r}."
            )


# ===========================================================================
# Case 3 of 3 (team-lead-ruled in-scope, 2026-09-03): hero/portfolio-level
# fabricated 0, same root-cause FAMILY as cases 1/2 (a genuinely-stored
# Python None silently becomes a fabricated 0) but a DIFFERENT mechanism and
# call path -- _build_meta's `mdd_data.get("dry_run", 0.0)` (app.py:~1733)
# returns the default ONLY when the key is MISSING, never when the stored
# value is None, so a real None (portfolio_strip["max_drawdown"] =
# {"if_held": None, "dry_run": None, ...}, e.g. get_portfolio_bot_and_held_
# daily_returns returning None for the WHOLE portfolio -- a near-empty
# system) passes straight through into the template, where the hero row's
# `(meta.portfolio.mdd if ... else 0) or 0` (templates/index.html:885-886)
# is ALSO None-blind (None is falsy) and fabricates a 0.
#
# SCOPE BOUNDARY (team-lead ruling): MDD fields ONLY. The identical `or 0`/
# `.get` pattern on tc/cr is pre-existing and NOT newly reachable by this
# cycle -- do not extend this test class to tc/cr.
# ===========================================================================


class TestHeroLevelStoredNoneFabricatedAsZero:
    def test_hero_mdd_stored_none_renders_dash_not_fabricated_zero(
        self, client, mock_database, monkeypatch
    ):
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        # KEY: the underlying dict has if_held/dry_run KEYS PRESENT with
        # value None (not an absent dict, not a missing key) -- the exact
        # .get(key, default)-blind-spot shape. This is what distinguishes
        # this test from a "missing key" bug, which .get(key, 0.0) WOULD
        # correctly catch -- an assertion that merely checked "the cell
        # isn't 0" without pinning this exact stored-None shape could pass
        # for the wrong reason against an unrelated missing-key defect.
        analytics_mock.get_portfolio_max_drawdown.return_value = {
            "if_held": None, "dry_run": None, "if_held_lifetime": 99.99, "n_obs": 0,
        }
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        _stub_get_api_state_dict_with_real_portfolio_strip(monkeypatch, _minimal_bot_state())

        resp = client.get("/")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)

        # has_live_data (templates/index.html:925) is driven by cr_bot/
        # cr_held/tc_bot/tc_held only, NOT mdd_bot/mdd_held -- the mock still
        # supplies real non-zero TC/CR values, so the MDD row is reached via
        # the SAME live-data branch a healthy TC/CR row uses. Confirms the
        # bug isn't coincidentally masked by the unrelated empty-state gate.
        today_anchor = html.find('data-testid="comp-today-bot-text"')
        assert today_anchor != -1
        today_snippet = html[today_anchor : today_anchor + 200]
        assert "&mdash;" not in today_snippet, (
            "test construction error: has_live_data must be True (driven by "
            "TC/CR, which this test leaves genuinely non-zero) so the MDD "
            "row's own render isn't coincidentally suppressed by the "
            "unrelated has_live_data empty-state gate -- that would make "
            "this test pass for the wrong reason regardless of the actual "
            "None-fabrication bug."
        )

        for testid in ("comp-mdd-bot-text", "comp-mdd-held-text"):
            anchor = html.find(f'data-testid="{testid}"')
            assert anchor != -1, f"{testid} not found in rendered HTML"
            snippet = html[anchor : anchor + 150]
            assert "0.00%" not in snippet, (
                f"HERO MDD FABRICATION FAIL: {testid} rendered a fabricated "
                f"'0.00%' for a genuinely-stored None "
                f"(portfolio_strip['max_drawdown'] = {{'if_held': None, "
                f"'dry_run': None, ...}}) -- mdd_data.get('dry_run', 0.0)/"
                f".get('if_held', 0.0) only apply the default when the KEY "
                f"is missing, never when the STORED VALUE is None, so the "
                f"None passed straight through _build_meta into the "
                f"template's `... or 0` fallback, which is ALSO None-blind "
                f"(None is falsy in Jinja same as Python). "
                f"Snippet: {snippet!r}"
            )
            assert "&mdash;" in snippet, (
                f"expected {testid} to degrade to the honest em-dash empty "
                f"state instead of fabricating any numeric value. "
                f"Snippet: {snippet!r}"
            )


# ===========================================================================
# AC-5 — /api/performance and /api/history/<days> gain honest coverage fields
# ===========================================================================


class TestAC5PerformanceRouteCoverageFields:
    def test_api_performance_response_carries_actual_days_and_coverage_days(
        self, client, tmp_path, monkeypatch
    ):
        sym_id = "sym-perf-coverage"
        rows = [{"days_ago": d, "current_return": 0.1 * (d % 3), "shadow_return": 0.1 * (d % 3)}
                for d in range(20, -1, -1)]  # 21 real trading days
        db_file = _seed_shadow_db(tmp_path / "perf_shadow.db", sym_id, rows)
        monkeypatch.setattr(app_module.analytics, "DB_FILE", db_file)

        resp = client.get("/api/performance", query_string={"scope": "aggregate", "days": "60"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()

        assert body["window_days"] == 60, (
            "AC-5: window_days must keep its EXISTING 'requested window' "
            f"meaning (the plan explicitly says do not redefine it); got "
            f"{body['window_days']!r}"
        )
        assert "actual_days" in body, (
            "AC-5 FAIL: /api/performance response is missing 'actual_days' -- "
            "the honest count of trading days actually available (mirrors the "
            "already-computed observation_count)."
        )
        assert body["actual_days"] == body["observation_count"], (
            "actual_days must equal the already-computed observation_count "
            f"({body['observation_count']}); got {body['actual_days']}"
        )
        assert "coverage_days" in body, (
            "AC-5 FAIL: /api/performance response is missing 'coverage_days' "
            "-- mirrors database.get_exit_turnover_stats' established "
            "'coverage_days = min(window, actual_days)' honesty pattern."
        )
        assert body["coverage_days"] == min(60, body["actual_days"]), (
            f"coverage_days must equal min(requested, actual) = "
            f"min(60, {body['actual_days']}); got {body['coverage_days']}"
        )
        assert "date_range" in body, (
            "AC-5 FAIL: /api/performance response is missing 'date_range' -- "
            "the real earliest/latest date actually covered."
        )

    def test_api_performance_oversized_window_reports_honest_coverage_shortfall(
        self, client, tmp_path, monkeypatch
    ):
        """AC-5's core scenario -- the operator's exact complaint: requesting a
        60d window when only ~21 days of history exist must be VISIBLE in the
        response's own numbers (coverage_days < requested), not silently
        collapsed to 'all the data there is' with no signal."""
        sym_id = "sym-perf-shortfall"
        rows = [{"days_ago": d, "current_return": 0.05, "shadow_return": 0.05}
                for d in range(20, -1, -1)]
        db_file = _seed_shadow_db(tmp_path / "perf_shadow2.db", sym_id, rows)
        monkeypatch.setattr(app_module.analytics, "DB_FILE", db_file)

        resp = client.get("/api/performance", query_string={"scope": "aggregate", "days": "60"})
        body = resp.get_json()
        assert body["coverage_days"] < 60, (
            f"AC-5 FAIL: requested a 60d window over ~21 days of real history; "
            f"coverage_days ({body.get('coverage_days')}) must honestly report "
            f"the shortfall (< 60), not silently claim full coverage."
        )

    def test_api_history_response_carries_actual_days_and_date_range(self, client, tmp_path):
        """GET /api/history/<days>: window_days keeps its existing (requested)
        meaning; actual_days/coverage_days/date_range are additive. No
        post_mortem files exist in this isolated tmp base_dir -> actual_days
        must honestly be 0, not fabricated."""
        with patch.object(
            app_module.analytics, "_POST_MORTEMS_DIR", str(tmp_path / "no_post_mortems")
        ):
            resp = client.get("/api/history/60")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["window_days"] == 60
        assert "actual_days" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'actual_days'"
        )
        assert "coverage_days" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'coverage_days'"
        )
        assert "date_range" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'date_range'"
        )
        assert body["actual_days"] == 0, (
            f"an empty post-mortem directory must honestly report actual_days=0, "
            f"not a fabricated non-zero value; got {body['actual_days']}"
        )


# ===========================================================================
# AC-4 — rendered coverage disclosure target exists, distinct from the
# pre-existing 30-observation stability banner (Decision table: keep separate)
# ===========================================================================


class TestAC4RenderedDisclosureTargetIsDistinctFromStabilityBanner:
    def test_performance_page_has_obs_caption_and_separate_stability_banner(self, client):
        resp = client.get("/performance")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'data-testid="obs-caption"' in html, (
            "the JS-populated coverage-disclosure caption element must exist "
            "on the page (renderObsCount's render target)"
        )
        assert 'data-testid="insufficient-banner"' in html, (
            "the pre-existing 30-observation STABILITY banner must remain "
            "(Decision table: 'Keep the existing 30-observation stability "
            "banner SEPARATE — it answers a different question than coverage "
            "disclosure; conflating them is what let this defect hide')."
        )
        # They must be genuinely distinct elements, not the same one relabeled.
        obs_idx = html.find('data-testid="obs-caption"')
        banner_idx = html.find('data-testid="insufficient-banner"')
        assert obs_idx != banner_idx and abs(obs_idx - banner_idx) > 10, (
            "obs-caption and insufficient-banner must be two distinct DOM "
            "elements, not the same element serving both purposes"
        )


# ===========================================================================
# mdd_insufficient re-scoping (relayed from main via mdd-ui, 2026-09-03,
# corroborating a prior-cycle finding already in the template: F-2's comment
# at templates/index.html:887-890 diagnosed the pre-AC-1 basis mismatch
# (bot MDD from a thin shadow trajectory vs held MDD from Composer's full
# lifetime) and mitigated it with a <30-trading-day DATA-DEPTH guard
# (app.py:1702, `_insufficient_history = len(_hist_dates) < 30`) borrowed
# from an UNRELATED statistical-stability threshold (Bailey/de-Prado 2014).
# The mismatch itself is STRUCTURAL, not depth-dependent -- it does not
# shrink at 30/300/3000 days -- so the guard was silently WRONG the whole
# time it happened to read True (thin history) and has been rendering an
# unqualified misleading winner bar since shadow_history crossed 30 days
# (~2026-08-04). AC-1 makes both legs genuinely same-window comparable,
# which retires the BASIS-mismatch concern entirely -- but the STABILITY
# concern (quantstats/peak-to-trough are noisy on <30 observations) is
# real and must survive, re-scoped to read `n_obs` (the exact count the
# same-window computation used) instead of `len(_hist_dates)` (an
# unrelated shadow-history date array that isn't guaranteed to track the
# same window at all).
#
# VERIFIED DIRECTLY (not taken on the relay's word alone) via source read
# at this cycle's HEAD: templates/index.html:1250/:1335 (`_card_mdd_
# insufficient`) already reuse `meta.portfolio.insufficient_history` --
# the SAME flag the hero row reads -- so re-scoping `_build_meta`'s
# computation ONCE fixes both hero and cards for the insufficient-history
# ALPHA-BADGE gating; mdd-ui's "cards have zero guard" framing conflates
# that (already-shared) gate with a SEPARATE, genuinely per-card-only gap:
# `mdd_held`/if_held at templates/index.html:1234/:1319 has NO None-guard
# (`(mdd_d.get("if_held", 0) if mdd_d is mapping else 0) | float` coerces
# a genuine None straight to 0.0), unlike `mdd_bot`/dry_run's existing
# `is not none` guard two lines above it in BOTH card blocks. Both real
# gaps are pinned below as distinct RED cases.
# ===========================================================================

_INDEX_HTML_PATH = Path(__file__).parent.parent.parent / "templates" / "index.html"

# The ORIGINAL F-2 comment, verbatim (pre-cycle source read) -- a genuine
# leftover stale comment would still contain this exact sentence unchanged.
# A rewritten comment may legitimately reference the same underlying facts
# in different (e.g. past-tense/historical) wording without tripping this.
_VERBATIM_STALE_F2_SENTENCE = (
    "The bot MDD is computed from the shadow trajectory (which may be only "
    "a few days), while the held MDD is from Composer's full lifetime."
)


class TestMddInsufficientRescopedToNObs:
    def test_build_meta_insufficient_history_driven_by_n_obs_not_hist_dates_length(self):
        """Behavioral (not just source-regex): drive app._build_meta directly
        with a crafted portfolio_strip -- hist_dates >= 30 (OLD flag would
        say 'sufficient') but max_drawdown.n_obs < 30 (the same-window
        computation actually used few days) must yield insufficient_history
        = True. The reverse combination (hist_dates < 30, n_obs >= 30) must
        yield False. If insufficient_history still tracks hist_dates length,
        BOTH assertions fail (the flag would be inverted from what this test
        expects in at least one direction)."""
        base_strip = {
            "today_change": {"dry_run": 0.1, "if_held": 0.1},
            "cumulative_return": {"dry_run": 1.0, "if_held": 1.0},
            "hist_bot": [0.0],
            "hist_held": [0.0],
            "hist_source": "shadow_history",
            "data_as_of": "09:30 ET",
            "account_value": 10000.0,
        }

        # hist_dates says "plenty of history" (35 >= 30); n_obs says "thin"
        # (5 < 30) -- the same-window computation this cycle introduces.
        strip_thin_n_obs = dict(
            base_strip,
            hist_dates=["2026-01-01"] * 35,
            max_drawdown={"if_held": 5.0, "dry_run": 5.0, "if_held_lifetime": 20.0, "n_obs": 5},
        )
        meta_thin = app_module._build_meta(
            state_data={}, next_run_seconds=0, market_state="closed", portfolio_strip=strip_thin_n_obs
        )
        assert meta_thin["portfolio"]["insufficient_history"] is True, (
            "AC re-scope FAIL: hist_dates has 35 entries (old flag would say "
            "sufficient) but max_drawdown.n_obs=5 (the ACTUAL same-window "
            "computation depth) -- insufficient_history must be True. If "
            "False: the flag is still driven by len(_hist_dates), not n_obs."
        )

        # Reverse: hist_dates says "thin" (5 < 30); n_obs says "plenty" (35).
        strip_healthy_n_obs = dict(
            base_strip,
            hist_dates=["2026-01-01"] * 5,
            max_drawdown={"if_held": 5.0, "dry_run": 5.0, "if_held_lifetime": 20.0, "n_obs": 35},
        )
        meta_healthy = app_module._build_meta(
            state_data={},
            next_run_seconds=0,
            market_state="closed",
            portfolio_strip=strip_healthy_n_obs,
        )
        assert meta_healthy["portfolio"]["insufficient_history"] is False, (
            "AC re-scope FAIL: hist_dates has only 5 entries (old flag would "
            "say insufficient) but max_drawdown.n_obs=35 (the ACTUAL "
            "same-window computation depth is healthy) -- insufficient_history "
            "must be False. If True: the flag is still driven by "
            "len(_hist_dates), not n_obs."
        )

    def test_stale_f2_comment_describing_the_now_fixed_basis_mismatch_is_replaced(self):
        """The F-2 comment (templates/index.html:887-890) diagnosed a basis
        mismatch (bot MDD from shadow trajectory vs held MDD from Composer's
        full lifetime) that AC-1 fixes structurally -- both legs are now
        genuinely same-window. Leaving a comment that CLAIMS this mismatch
        is still true would itself become a new false statement in the
        code -- but a REWRITTEN comment may legitimately reference the same
        words in PAST TENSE, documenting why the mechanism changed (e.g.
        "the original comment described a mismatch that is now fixed").
        [Corrected during review, self-caught false positive]: this test's
        first draft did a blind substring-absence check on fragments of the
        OLD comment's wording, which false-failed against a well-written
        historical-context rewrite that legitimately contains those same
        words while correctly stating, in present tense, that the mismatch
        is fixed. Replaced with a check for the EXACT original sentence
        (verbatim -- a genuine leftover stale comment would still contain
        it unchanged) plus a positive check that the current comment
        affirms the fix in present tense."""
        src = _INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert _VERBATIM_STALE_F2_SENTENCE not in src, (
            f"AC re-scope FAIL: the ORIGINAL F-2 comment sentence is still "
            f"present verbatim in templates/index.html -- it was never "
            f"rewritten. Original sentence: {_VERBATIM_STALE_F2_SENTENCE!r}"
        )
        lowered = src.lower()
        assert "stability" in lowered or "bailey" in lowered or "de-prado" in lowered, (
            "the replacement comment near mdd_insufficient must name the "
            "REMAINING legitimate purpose (statistical stability, Bailey/"
            "de-Prado 2014) -- not leave the rationale unstated."
        )
        assert re.search(r"now\s+(fixed|guards?|reads?)|no longer|superseded", lowered), (
            "the replacement comment must affirmatively state, in present "
            "tense, that the basis mismatch is fixed and what the flag now "
            "guards -- not just silently drop the old wording without "
            "explaining the current state."
        )


class TestPerSymphonyCardHeldNoneGuard:
    """The genuinely per-card-only gap (distinct from the shared
    insufficient_history flag above): mdd_held/if_held has no None-guard,
    unlike mdd_bot/dry_run's existing one, in BOTH card blocks
    (near templates/index.html:1230-1237 and :1315-1322)."""

    # The EXACT current unguarded assignment (both card blocks, byte-identical
    # today at templates/index.html:1234 and :1319) -- a direct pin of the
    # defect line, not a loose "is there some 'is not none' nearby" search
    # (which false-positived by matching mdd_bot's OWN render-time repeat of
    # its guard within the search window, rather than a genuine held-side
    # guard -- caught and fixed before commit).
    _UNGUARDED_MDD_HELD_RE = re.compile(
        r'mdd_held\s*=\s*\(\s*mdd_d\.get\(\s*"if_held"\s*,\s*0\s*\)\s*'
        r"if\s+mdd_d\s+is\s+mapping\s+else\s+0\s*\)\s*\|\s*float"
    )

    def test_active_and_standby_cards_both_guard_mdd_held_against_none(self):
        src = _INDEX_HTML_PATH.read_text(encoding="utf-8")
        matches = list(self._UNGUARDED_MDD_HELD_RE.finditer(src))
        assert not matches, (
            f"AC-1 render gap: found {len(matches)} occurrence(s) of the "
            f"UNGUARDED `mdd_held = (mdd_d.get('if_held', 0) ...) | float` "
            f"assignment -- post-fix, if_held CAN be None (same as dry_run "
            f"already can), and this pattern coerces None straight to a "
            f"fabricated 0.0. Must be guarded the same way mdd_bot/dry_run "
            f"already is (`_mdd_held_raw = mdd_d.get('if_held') if mdd_d is "
            f"mapping else None` + `is not none` check), in BOTH the active-"
            f"section and standby-section card blocks."
        )
