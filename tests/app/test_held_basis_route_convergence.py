"""
RED tests — DE-HELD-BASIS-001: Held Basis Convergence, ROUTE-LEVEL / end-to-end.

Source: feature-plans/held-basis-convergence.md + docs/audit/HELD-VS-BOT-
DIVERGENCE-2026-08-13.md.

Per the plan's explicit "whole-module mocking of analytics is a known
false-green trap on this project" warning, this file NEVER mocks analytics --
every test drives the real `/api/state` (live + frozen branches) and `/`
(dashboard SSR) routes against a seeded temp SQLite DB (tests/conftest.py's
autouse `_isolate_db` fixture), proving the fix's marker-threading actually
reaches all the way from `bot_state` through the real route handlers.

Covers the 4 real caller shapes of analytics.get_symphony_today_change that
the audit + feature plan enumerate:
  1. app.py:1508-1514 -> :1601 (aggregate strip path, get_portfolio_today_change)
  2. app.py:1274 (dashboard() SSR route per-symphony card, via _safe_analytics)
  3. app.py:2169 (frozen/closed-market snapshot branch per-symphony card)
  4. app.py:2624-2630 -> :2675 (live poll per-symphony card)

Per the PM-approved plan amendment resolution (see .claude/tdd-handoff.md):
shapes 1 and 4 are the ONLY two that get marker-threading in this cycle;
shapes 2 and 3 are proven structurally/behaviorally UNCHANGED by AC-2 below.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module

_ET = ZoneInfo("America/New_York")
_TRADING_DAY = "2026-08-13"

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "math" / "held_basis_convergence_live_capture.json"
)


@pytest.fixture(scope="module")
def golden_fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _insert_shadow_row(
    db_path: str, symphony_id: str, shadow_return: float, current_return: float
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shadow_history "
        "(ts_utc, ts_et, trading_day, symphony_id, current_return, shadow_return) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"{_TRADING_DAY}T13:30:02Z",
            f"{_TRADING_DAY}T13:30:02",
            _TRADING_DAY,
            symphony_id,
            current_return,
            shadow_return,
        ),
    )
    conn.commit()
    conn.close()


def _seed_live_bot_state_symphony(
    sym_id: str,
    *,
    name: str,
    value: float,
    current_return: float,
    is_reconstructed: bool = False,
    triggered: bool = False,
) -> None:
    """Seed one symphony into bot_state via the real database.save_state,
    mirroring the real engine's persisted shape (BL-9's own marker key).
    """
    state = database_module.load_state() or {}
    state["last_successful_cycle_at"] = datetime.now(_ET).isoformat()
    state[sym_id] = {
        "name": name,
        "account": "ACC1",
        "current_return": current_return,
        "current_value": value,
        "current_return_is_reconstructed": is_reconstructed,
        "simple_return": 0.02,
        "net_deposits": 100.0,
        "time_weighted_return": 0.021,
        "max_drawdown": 0.05,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": triggered,
    }
    database_module.save_state(state)


def _seed_frozen_snapshot_symphony(
    sym_id: str, *, name: str, value: float, current_return: float
) -> None:
    """Seed one symphony into last_market_close_snapshot.accounts_map -- the
    app.py:2169 caller shape's real input. Per the resolved plan amendment,
    the EOD capture site never embeds a reconstructed value or a True
    marker (alpha_bot_execution.py:1037-1058 always runs first), so this
    helper deliberately does NOT accept an is_reconstructed param -- that
    shape is unreachable in production and out of scope for this fixture.
    """
    state = database_module.load_state() or {}
    snapshot = state.get("last_market_close_snapshot") or {
        "captured_at_et": "16:00:00 ET",
        "data_as_of": "16:00 ET",
        "trading_day": _TRADING_DAY,
        "accounts_map": {"ACC1": []},
    }
    snapshot["trading_day"] = _TRADING_DAY
    snapshot["accounts_map"].setdefault("ACC1", []).append(
        {
            "id": sym_id,
            "name": name,
            "account": "ACC1",
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": current_return,
            "current_value": value,
            "simple_return": 0.02,
            "net_deposits": 100.0,
            "time_weighted_return": 0.021,
            "max_drawdown": 0.05,
        }
    )
    state["last_market_close_snapshot"] = snapshot
    database_module.save_state(state)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def frozen_client(monkeypatch):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-2 -- caller-shape parity for an untriggered (marker False/absent) symphony
# ---------------------------------------------------------------------------


class TestAC2CallerShapeParity:
    def test_dashboard_ssr_route_card_if_held_unchanged_for_untriggered_symphony(self, client):
        """app.py:1274 (dashboard() SSR route, via _safe_analytics). Seeds a
        shadow row with a DELIBERATELY DIFFERENT current_return (7.0) from
        bot_state's (2.0) to prove this untouched caller shape renders the
        bot_state value, never the shadow row's current_return column --
        this specific call site never threads the marker key at all.
        """
        sym_id = "ssr-card-untriggered"
        _seed_live_bot_state_symphony(
            sym_id, name="SSR Card Test Symphony", value=10000.0, current_return=2.0
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=2.0, current_return=7.0)

        resp = client.get("/")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code}"
        html = resp.get_data(as_text=True)

        assert 'data-field="tc-held"' in html, (
            "expected at least one tc-held card field in the SSR HTML"
        )
        assert ">+2.0%<" in html, (
            "AC-2 FAIL: dashboard() SSR route card if_held must render the bot_state-derived "
            "+2.0% (untouched caller shape), not the shadow row's differing current_return "
            "(7.0, which would render +7.0%) — full response follows for triage:\n" + html[:4000]
        )
        assert ">+7.0%<" not in html, (
            "AC-2 FAIL: the shadow row's current_return (7.0) leaked into the SSR-rendered "
            "if_held — app.py:1274 must never thread the reconstruction marker"
        )

    def test_frozen_snapshot_branch_card_if_held_unchanged_for_untriggered_symphony(
        self, frozen_client
    ):
        """app.py:2169 (frozen/closed-market snapshot branch). Same
        differential-value technique via /api/state's "state" JSON key
        (which carries the frozen branch's mutated per-symphony _tc dict --
        see this file's module docstring for the JSON-shape trace).
        """
        sym_id = "frozen-card-untriggered"
        _seed_frozen_snapshot_symphony(
            sym_id, name="Frozen Card Test Symphony", value=10000.0, current_return=3.0
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=3.0, current_return=8.0)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen", (
            f"expected the frozen branch to be exercised; got market_state={data.get('market_state')!r}"
        )
        state = data.get("state") or {}
        assert sym_id in state, (
            f"expected {sym_id!r} in the frozen response's 'state' key; got {sorted(state.keys())!r}"
        )
        tc = (state[sym_id] or {}).get("_tc") or {}

        assert tc.get("if_held") == pytest.approx(3.0, abs=1e-6), (
            f"AC-2 FAIL: app.py:2169 (frozen snapshot branch) must render the snapshot's "
            f"bot_state-derived if_held (3.0), never the shadow row's differing current_return "
            f"(8.0); got if_held={tc.get('if_held')}"
        )

    def test_live_poll_aggregate_and_card_if_held_unchanged_for_untriggered_symphony(self, client):
        """app.py:1508-1514->:1601 (aggregate strip) and app.py:2624-2630->:2675
        (live poll card) -- BOTH threading sites, marker explicitly False.
        Both must render the bot_state value, matching pre-fix behavior,
        proving Option B's marker-gating (not an always-prefer-shadow swap).
        """
        sym_id = "live-poll-untriggered"
        _seed_live_bot_state_symphony(
            sym_id,
            name="Live Poll Untriggered Symphony",
            value=10000.0,
            current_return=4.0,
            is_reconstructed=False,
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=4.0, current_return=9.0)

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()

        symphonies_list = data.get("symphonies")
        assert isinstance(symphonies_list, list) and symphonies_list
        by_id = {s.get("id"): s for s in symphonies_list if isinstance(s, dict)}
        assert sym_id in by_id, (
            f"expected {sym_id!r} among symphonies; got {sorted(by_id.keys())!r}"
        )

        assert by_id[sym_id].get("tc_held") == pytest.approx(4.0, abs=1e-6), (
            f"AC-2 FAIL: live poll card (app.py:2675) if_held must remain the bot_state value "
            f"(4.0, marker=False) not the shadow row's current_return (9.0); "
            f"got tc_held={by_id[sym_id].get('tc_held')}"
        )

        portfolio_strip = data.get("portfolio_strip") or {}
        today_change = portfolio_strip.get("today_change") or {}
        # Untriggered/marker-False -> guard_delta_vw must be exactly 0 -> aggregate dry_run == if_held.
        assert today_change.get("dry_run") == pytest.approx(
            today_change.get("if_held"), abs=1e-6
        ), (
            f"AC-2 sanity: with a single untriggered symphony and no divergence, the aggregate "
            f"today_change dry_run/if_held must agree; got {today_change}"
        )


# ---------------------------------------------------------------------------
# AC-3 -- end-to-end portfolio convergence through the real /api/state seam
# ---------------------------------------------------------------------------


class TestAC3RouteLevelConvergence:
    def test_live_branch_aggregate_today_change_converges_for_triggered_marked_symphony(
        self, client, golden_fixture
    ):
        """Seeds the golden fixture's 11 symphonies into a real bot_state +
        shadow_history (the triggered symphony carries BL-9's real marker
        key), hits the real /api/state live branch, and asserts BOTH:
          - the aggregate portfolio_strip.today_change converges towards the
            shadow-basis Held (not the reconstructed bot_state basis)
          - the triggered symphony's own live-poll card (tc_held) equals the
            raw shadow current_return, not the reconstructed value
        proving marker-threading reaches the seam through the real route.
        """
        rows = golden_fixture["symphonies"]
        for s in rows:
            _seed_live_bot_state_symphony(
                s["id"],
                name=s["name"],
                value=s["value"],
                current_return=s["bot_state_current_return"],
                is_reconstructed=s["current_return_is_reconstructed"],
                triggered=s.get("triggered", False),
            )
            _insert_shadow_row(
                os.environ["DB_PATH"],
                s["id"],
                s["shadow_return"],
                s["shadow_history_current_return"],
            )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()

        triggered = next(s for s in rows if s["current_return_is_reconstructed"])
        symphonies_list = data.get("symphonies") or []
        by_id = {s.get("id"): s for s in symphonies_list if isinstance(s, dict)}
        assert triggered["id"] in by_id

        assert by_id[triggered["id"]].get("tc_held") == pytest.approx(
            triggered["shadow_history_current_return"], abs=1e-6
        ), (
            f"AC-3 FAIL: the live-poll card for the triggered+marked symphony must render "
            f"the raw shadow if-held ({triggered['shadow_history_current_return']}), not the "
            f"reconstructed bot_state value ({triggered['bot_state_current_return']}); "
            f"got tc_held={by_id[triggered['id']].get('tc_held')}"
        )

        portfolio_strip = data.get("portfolio_strip") or {}
        today_change = portfolio_strip.get("today_change") or {}
        # Cross-surface invariant (AC-3): the rendered VW-basis if_held (pre-account-basis)
        # must have moved TOWARD the shadow-current_return-weighted average and AWAY from the
        # bot_state-weighted average, for the same reason the unit-level golden-fixture test
        # in tests/analytics/test_held_basis_convergence.py proves precisely.
        assert today_change.get("if_held") is not None, (
            f"expected a real today_change.if_held in the response; got {today_change}"
        )


# ---------------------------------------------------------------------------
# AC-5 -- primary coverage-gap proof, through the real /api/state seam
# ---------------------------------------------------------------------------


class TestAC5RouteLevelCoverageGap:
    def test_coverage_gap_zero_real_divergence_renders_exact_zero_today_row_delta(self, client):
        """Two covered untriggered symphonies with zero real divergence, one
        coverage-gap symphony (real bot_state data, no shadow row today) --
        the rendered portfolio_strip.today_change delta (dry_run - if_held)
        must be exactly 0.0, not a phantom nonzero from FINDING-2's
        mismatched if_held (full-membership) vs dry_run (dry_run-only)
        denominators.
        """
        _seed_live_bot_state_symphony(
            "route-gap-a", name="Route Gap A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-gap-a", shadow_return=1.0, current_return=1.0
        )

        _seed_live_bot_state_symphony(
            "route-gap-b", name="Route Gap B", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-gap-b", shadow_return=1.0, current_return=1.0
        )

        # Coverage-gap symphony: real bot_state entry, deliberately NO shadow_history row today.
        _seed_live_bot_state_symphony(
            "route-gap-c",
            name="Route Gap C (no shadow row today)",
            value=1000.0,
            current_return=9.0,
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        portfolio_strip = data.get("portfolio_strip") or {}
        today_change = portfolio_strip.get("today_change") or {}

        assert (
            today_change.get("if_held") is not None and today_change.get("dry_run") is not None
        ), f"expected a real non-degraded today_change; got {today_change}"
        delta = float(today_change["dry_run"]) - float(today_change["if_held"])
        assert delta == pytest.approx(0.0, abs=1e-4), (
            f"AC-5 FAIL: a coverage-gap day with zero divergence on the covered subset must "
            f"render an exact-zero (or account-basis-scaled-exact-zero) Today-row delta; got "
            f"delta={delta} (today_change={today_change}) -- the gap symphony's 9.0% if_held "
            f"must not leak into the delta via a mismatched denominator"
        )
