"""
RED tests -- closed-market cumulative bounce (fix/closed-market-cumulative-bounce).

Root cause (independently verified against pre-fix code, file:line):
  - app.py:2461-2506 (closed_frozen/pre_market branch's inline `_portfolio_strip`,
    both the happy path and its except-fallback) never sets `guard_alpha` or
    `window`, unlike the open path's `_compute_portfolio_strip` (app.py:1863-1879),
    which sets both from a compute_windowed_portfolio_strip(..., window=_DEFAULT_
    HERO_WINDOW) call.
  - static/index.js:159,169-170 `renderGuardAlpha`: `guard_alpha === null` (always
    true on the closed branch pre-fix) unconditionally fires an un-awaited
    fetchWindowedStrip('30d') (index.js:1472), which resolves later and calls
    updateComparisonRows(wrapped) AGAIN (index.js:1481-1482) with the windowed
    strip's cumulative_return -- overwriting the correct render moments after
    the synchronous /api/state poll already painted it. analytics.py:1998-2000
    documents that windowed cumulative_return is deliberately VW (cash-excluded)
    basis, not account basis -- the two bases render genuinely different numbers.

This file pins the SERVER-SIDE precondition that makes the client-side bug
unreachable: once the closed branch carries a non-null guard_alpha (mirroring
the open path), index.js never issues the fallback fetch that clobbers the
row. The client-side race itself (and proof the DOM no longer bounces) is
verified by bounce-ux's live Playwright gate, not this suite.

Pattern: mirrors tests/app/test_held_basis_route_convergence.py -- real Flask
test client, real /api/state route, seeded temp SQLite DB (the project's
autouse _isolate_db conftest fixture), get_market_state monkeypatched to force
closed_frozen. NEVER whole-module-mocks analytics or database (the documented
false-green trap for this project -- see that file's module docstring).
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "dashboard"
    / "closed_market_cumulative_bounce"
)


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _seed_closed_market_snapshot(fixture: dict) -> None:
    """Seeds last_market_close_snapshot.accounts_map from the fixture's symphony
    rows, mirroring app.py's own snapshot-symphony shape (app.py:2242-2258 /
    2330-2348 both read current_return, current_value, simple_return,
    net_deposits, time_weighted_return, max_drawdown, id, name, account off
    each accounts_map entry) -- and test_held_basis_route_convergence.py's
    _seed_frozen_snapshot_symphony helper for the same shape.
    """
    state = database_module.load_state() or {}
    account_id = fixture["account_id"]
    accounts_map: dict = {account_id: []}
    for sym in fixture["symphonies"]:
        accounts_map[account_id].append(
            {
                "id": sym["id"],
                "name": sym["name"],
                "account": account_id,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "current_return": sym["current_return"],
                "current_value": sym["current_value"],
                "simple_return": sym["simple_return"],
                "net_deposits": sym["net_deposits"],
                "time_weighted_return": sym["time_weighted_return"],
                "max_drawdown": sym["max_drawdown"],
            }
        )
    state["last_market_close_snapshot"] = {
        "captured_at_et": "16:00:00 ET",
        "data_as_of": "16:00 ET",
        "trading_day": fixture["trading_day"],
        "accounts_map": accounts_map,
    }
    database_module.save_state(state)


def _seed_windowed_shadow_history(fixture: dict) -> None:
    """Seeds shadow_history rows so analytics.compute_windowed_symphony_guard_alpha
    (analytics.py:1939-1971, called via compute_windowed_portfolio_strip inside the
    fix) resolves a REAL number instead of None -- the documented AC-8b
    conservatism floor when a symphony has fewer than 2 in-window trading days
    of recorded divergence (analytics.py:1953-1959). The closed-market snapshot
    alone (accounts_map) carries no shadow_history rows, so without this seed
    the portfolio-level guard_alpha is honestly None regardless of whether the
    fix is correct -- a fixture gap, not a code defect (see fixture's own
    "shadow_history" provenance note).

    trading_day is resolved to a REAL calendar date relative to
    datetime.now(UTC) AT TEST-RUN TIME, never a fixed calendar date --
    analytics._window_cutoff_date (analytics.py:1840-1864) resolves its cutoff
    from the real wall clock at call time, so a hardcoded date would silently
    age out of the 30d window as real time passes (the exact trap
    tests/analytics/test_windowed_strip.py's TestNeverTriggeredEveryWindow class
    documents and works around).

    Column set mirrors tests/app/test_held_basis_route_convergence.py's proven-
    working _insert_shadow_row helper (ts_utc/ts_et/trading_day/symphony_id/
    current_return/shadow_return; position_epoch/is_post_trigger left at their
    schema defaults -- NULL groups every row into one contiguous epoch, which
    is what this fixture's 2-row-per-symphony shape wants).
    """
    today = datetime.now(UTC).date()
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        for sym_id, rows in fixture["shadow_history"].items():
            if sym_id.startswith("_"):
                continue  # skip the "_provenance"/"description" metadata keys
            for row in rows:
                trading_day = (today - timedelta(days=row["trading_day_offset_days"])).isoformat()
                conn.execute(
                    "INSERT INTO shadow_history "
                    "(ts_utc, ts_et, trading_day, symphony_id, current_return, shadow_return) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"{trading_day}T20:00:00Z",
                        f"{trading_day}T20:00:00",
                        trading_day,
                        sym_id,
                        row["current_return"],
                        row["shadow_return"],
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _recompute_vw_if_held(fixture: dict) -> float:
    """Recomputes the raw VW-basis portfolio if_held directly from the fixture's
    symphony rows via the real analytics function -- the non-hardcoded
    comparator tests use to prove account-basis and VW-basis are genuinely
    distinguishable (never a literal producer number)."""
    symphonies = [
        {
            "id": s["id"],
            "value": s["current_value"],
            "last_percent_change": s["current_return"] / 100.0,
            "simple_return": s["simple_return"],
            "net_deposits": s["net_deposits"],
            "time_weighted_return": s["time_weighted_return"],
            "max_drawdown": s["max_drawdown"],
            "trading_day": fixture["trading_day"],
        }
        for s in fixture["symphonies"]
    ]
    bot_state = {s["id"]: {} for s in fixture["symphonies"]}
    vw_cr = analytics_module.get_portfolio_cumulative_return(
        symphonies,
        bot_state,
        trading_day=fixture["trading_day"],
        db_path=os.environ["DB_PATH"],
    )
    assert vw_cr["if_held"] is not None, "fixture must produce a real VW if_held comparator"
    return vw_cr["if_held"]


@pytest.fixture()
def _clean_account_totals_cache():
    """Code-review fix (PR #136 revise round, finding #4): clear the
    module-global app_module._account_totals_cache / _account_totals_last_good
    dicts BEFORE and AFTER every test, not just inside the two tests that
    explicitly set them. Without this, a test that sets the cache (e.g.
    TestClosedFrozenCumulativeReturnBasis's warm-cache test) can leak state
    into a LATER test in the same pytest session that never touches the
    cache itself (e.g. the guard_alpha presence/null tests) -- an
    order-dependent flake risk the reviewer flagged. Composed into
    frozen_client/open_client below so every test using either fixture gets
    this for free without needing its own explicit clear() calls (the
    per-test clear() calls that already exist stay -- harmless redundancy,
    and they still matter for setting deterministic values within a test).
    """
    app_module._account_totals_cache.clear()
    app_module._account_totals_last_good.clear()
    yield
    app_module._account_totals_cache.clear()
    app_module._account_totals_last_good.clear()


@pytest.fixture()
def frozen_client(monkeypatch, _clean_account_totals_cache):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def open_client(monkeypatch, _clean_account_totals_cache):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# RED: closed_frozen branch must carry guard_alpha + window (the actual defect)
# ---------------------------------------------------------------------------


class TestClosedFrozenGuardAlphaPresence:
    def test_closed_frozen_portfolio_strip_carries_non_null_guard_alpha(self, frozen_client):
        """RED: app.py:2461-2506 never sets guard_alpha on the closed branch.
        This is the exact server-side precondition that fires index.js's
        fetchWindowedStrip fallback (index.js:169-170) on EVERY closed-market
        poll -- fixing this alone makes the client-side bounce unreachable.

        Seeds shadow_history (via _seed_windowed_shadow_history) so the
        windowed guard-alpha computation has >=2 in-window trading days per
        symphony to resolve a REAL number -- without it, a correct fix would
        still legitimately return None (AC-8b's documented conservatism
        floor, analytics.py:1953-1959), which this test deliberately treats
        as insufficient: a bare "None or numeric" check (matching the
        open-path regression test below) would also pass a SHALLOW fix that
        stubs the key without wiring real computation, so this test demands
        proof the real windowed-analytics path is reached end-to-end.
        """
        fixture = _load("closed_market_account_basis_divergence")
        _seed_closed_market_snapshot(fixture)
        _seed_windowed_shadow_history(fixture)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen", (
            f"expected the closed_frozen branch to be exercised; got "
            f"market_state={data.get('market_state')!r}"
        )
        ps = data.get("portfolio_strip") or {}
        ga = ps.get("guard_alpha")
        assert isinstance(ga, (int, float)), (
            f"portfolio_strip.guard_alpha must be a real number on the closed_frozen branch, "
            f"mirroring the open path's _compute_portfolio_strip (app.py:1863-1879); got "
            f"{ga!r} (type {type(ga).__name__}). A null guard_alpha here is exactly what "
            f"triggers index.js's fetchWindowedStrip('30d') fallback (index.js:169-170) on "
            f"every closed-market /api/state poll -- the bounce's root cause."
        )

    def test_closed_frozen_portfolio_strip_carries_window_label(self, frozen_client):
        """RED: companion to the guard_alpha test -- app.py:2461-2506 also never
        sets `window`. Expected value derived from app.py's own
        _DEFAULT_HERO_WINDOW constant, never a hardcoded '30d' literal.
        """
        fixture = _load("closed_market_account_basis_divergence")
        _seed_closed_market_snapshot(fixture)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        ps = data.get("portfolio_strip") or {}
        window = ps.get("window")
        assert isinstance(window, str) and window, (
            f"portfolio_strip.window must be a non-empty string on the closed_frozen branch, "
            f"mirroring the open path; got {window!r}"
        )
        assert window == app_module._DEFAULT_HERO_WINDOW, (
            f"portfolio_strip.window must echo app.py's own _DEFAULT_HERO_WINDOW "
            f"({app_module._DEFAULT_HERO_WINDOW!r}), the same default the open path's "
            f"_compute_portfolio_strip uses (app.py:1864-1871); got {window!r}"
        )


# ---------------------------------------------------------------------------
# Negative-space companion (team-lead ruling, post-fixture-gap trace): a
# production-realistic None guard_alpha (sparse/no shadow_history -- new
# symphony, early in its window) must not break anything else on the strip.
# This is legitimate server-side behavior, not a bug -- the client-side
# splice-guard (tests/dashboard/test_closed_market_bounce_js.py) is what
# protects the DOM when renderGuardAlpha's fallback fires on a genuinely-
# null guard_alpha; see bounce-ux's visual-gate scenario #3 for that half.
# ---------------------------------------------------------------------------


class TestClosedFrozenNullGuardAlphaIsLegitimate:
    def test_closed_frozen_null_guard_alpha_does_not_break_portfolio_strip_contract(
        self, frozen_client
    ):
        """A real closed-market snapshot can legitimately have < 2 in-window
        shadow_history rows per symphony -- compute_windowed_symphony_guard_
        alpha's AC-8b conservatism floor (analytics.py:1953-1959) then returns
        None, and the portfolio-level VW guard_alpha (analytics.py:2055) is
        honestly None too. This is NOT the bug: guard_alpha=None is a state
        the server contract must tolerate without degrading any sibling field
        or crashing the route -- it mirrors the open path's own pre-existing
        "None or numeric" contract (TestOpenMarketNoRegression above).
        """
        fixture = _load("closed_market_account_basis_divergence")
        _seed_closed_market_snapshot(fixture)
        # Deliberately NOT calling _seed_windowed_shadow_history -- zero
        # shadow_history rows reproduces the legitimate sparse-history state.

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, (
            f"a None guard_alpha (insufficient shadow_history) must not crash the "
            f"route; got {resp.status_code} {resp.get_data()!r}"
        )
        data = resp.get_json()
        ps = data.get("portfolio_strip") or {}
        assert ps.get("guard_alpha") is None, (
            f"sanity: this scenario is meant to reproduce the genuine AC-8b "
            f"None-floor (0 shadow_history rows); got "
            f"guard_alpha={ps.get('guard_alpha')!r} -- if this is no longer None, "
            f"the fixture/seeding assumptions this test relies on have changed "
            f"and the test needs revisiting, not the assertion loosened."
        )
        assert ps.get("window") == app_module._DEFAULT_HERO_WINDOW, (
            f"window must still echo even when guard_alpha is None (the windowed "
            f"strip call doesn't raise just because one internal value is None); "
            f"got {ps.get('window')!r}"
        )
        cr = ps.get("cumulative_return") or {}
        assert cr.get("if_held") is not None, (
            f"cumulative_return.if_held must stay populated when only guard_alpha "
            f"degrades -- a None guard_alpha must not collaterally null out an "
            f"independently-computed sibling field; got cumulative_return={cr!r}"
        )


# ---------------------------------------------------------------------------
# Regression-guard PINS: the closed branch's cumulative_return basis-selection
# contract (app.py:2449-2459 already does this correctly pre-fix -- written
# honestly as pins, not fabricated RED, per the approved plan).
# ---------------------------------------------------------------------------


class TestClosedFrozenCumulativeReturnBasis:
    def test_closed_frozen_cumulative_return_uses_account_basis_when_cache_warm(
        self, frozen_client
    ):
        fixture = _load("closed_market_account_basis_divergence")
        _seed_closed_market_snapshot(fixture)
        account_cr = fixture["account_totals"]["portfolio_cr"]

        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()
        app_module._account_totals_cache["portfolio_cr"] = account_cr
        app_module._account_totals_cache["portfolio_value"] = fixture["account_totals"][
            "portfolio_value"
        ]

        vw_if_held = _recompute_vw_if_held(fixture)
        # Non-vacuity guard: the two bases must actually be distinguishable in this
        # fixture, or the assertion below can't tell a correct fix from a lucky
        # coincidence (see feedback_verify_unit_comparability_on_threshold_constants).
        assert abs(account_cr - vw_if_held) > 1.0, (
            f"fixture is not distinguishable: account-basis ({account_cr}) and VW-basis "
            f"({vw_if_held}) if_held are too close to prove basis selection; fixture needs "
            f"a larger cash fraction or wider per-symphony simple_return spread"
        )

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        ps = data.get("portfolio_strip") or {}
        cr = ps.get("cumulative_return") or {}
        assert cr.get("if_held") == pytest.approx(account_cr, abs=1e-6), (
            f"closed_frozen cumulative_return.if_held must be the account-basis value "
            f"({account_cr}) when _account_totals_cache is warm, not the VW-basis value "
            f"({vw_if_held}); got {cr.get('if_held')}"
        )

    def test_closed_frozen_falls_back_to_vw_when_no_account_basis_cache(self, frozen_client):
        fixture = _load("closed_market_account_basis_divergence")
        _seed_closed_market_snapshot(fixture)
        # Deliberately leave both caches empty -- forces the honest VW fallback
        # (app.py:2456-2459).
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        vw_if_held = _recompute_vw_if_held(fixture)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        ps = data.get("portfolio_strip") or {}
        cr = ps.get("cumulative_return") or {}
        assert cr.get("if_held") == pytest.approx(vw_if_held, abs=1e-6), (
            f"closed_frozen cumulative_return.if_held must honestly fall back to the raw "
            f"VW-basis value ({vw_if_held}) when no account-basis cache is warm; got "
            f"{cr.get('if_held')}"
        )
        assert ps.get("basis") == "value_weighted", (
            f"the Tier-2 honest-floor 'basis' marker must be set when cumulative_return has "
            f"no account-basis backing; got portfolio_strip.basis={ps.get('basis')!r}"
        )


# ---------------------------------------------------------------------------
# Regression guard: the fix must not diverge the open path's existing,
# already-correct guard_alpha/window contract.
# ---------------------------------------------------------------------------


class TestOpenMarketNoRegression:
    def test_open_market_still_carries_guard_alpha_and_window(self, open_client):
        state = {
            "date": "2026-08-20",
            "bounce-open-a": {
                "name": "Bounce Open A",
                "account": "ACC1",
                "current_return": 2.0,
                "current_value": 5000.0,
                "current_return_is_reconstructed": False,
                "simple_return": 0.02,
                "net_deposits": 100.0,
                "time_weighted_return": 0.021,
                "max_drawdown": 0.05,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }
        database_module.save_state(state)

        resp = open_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "open"
        ps = data.get("portfolio_strip") or {}
        # guard_alpha may legitimately be None (e.g. insufficient windowed history)
        # -- the regression this guards is the KEY existing at all with the right
        # shape, not a specific numeric value.
        assert "guard_alpha" in ps, (
            "open-path portfolio_strip must carry a guard_alpha key (no regression)"
        )
        assert ps.get("guard_alpha") is None or isinstance(ps.get("guard_alpha"), (int, float)), (
            f"guard_alpha must be None or numeric; got {ps.get('guard_alpha')!r}"
        )
        assert ps.get("window") == app_module._DEFAULT_HERO_WINDOW, (
            f"open-path portfolio_strip.window must remain {app_module._DEFAULT_HERO_WINDOW!r}; "
            f"got {ps.get('window')!r}"
        )
