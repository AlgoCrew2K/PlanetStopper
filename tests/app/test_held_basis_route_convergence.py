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
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module

_ET = ZoneInfo("America/New_York")


def _frozen_datetime_class(instant: datetime) -> type[datetime]:
    """Builds a ``datetime`` subclass whose ``.now(tz)`` always resolves to
    ``instant`` -- the established no-freezegun clock-freeze technique this
    repo already uses (see tests/analytics/test_bl6_today_change_et_
    fallback.py's ``_FrozenDatetime``). A proper subclass, so every OTHER
    datetime behavior (construction, arithmetic, .strftime, comparisons)
    stays real -- only .now() is overridden.

    KNOWN DUPLICATION (code-review round 1, F5, accepted as low-priority):
    this factory is structurally identical to test_bl6_today_change_et_
    fallback.py's module-level ``_FrozenDatetime`` class (parametrized here
    as a factory instead of a single fixed instant, since this module needs
    several different frozen instants across its tests). Deliberately NOT
    hoisted to a shared tests/conftest.py util this cycle -- that touches a
    file every test in the tree imports, a broader blast radius than this
    single-file, test-only hardening cycle's scope justifies for a
    low-priority dedup.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return instant.astimezone(tz)
            # DEVIATION FROM REAL datetime.now() (code-review round 2, F3):
            # real now() with no tz returns the LOCAL system time; this
            # returns `instant`'s naive wall-clock value regardless of
            # `instant`'s own tzinfo (UTC here) -- i.e. NOT converted to any
            # local timezone. Currently inert: nothing in this module calls
            # app_module.datetime.now() without a tz arg. A future no-tz
            # caller would get this UTC-wall value, not "local now" -- a trap
            # if such a call is ever added without updating this comment's
            # assumption.
            return instant.replace(tzinfo=None)

    return _Frozen


# HELD-BASIS HARDENING (residual of #127): a hardcoded _TRADING_DAY detonates
# the day after it's written (the original #127 bug); computing it from the
# LIVE ET wall clock at MODULE-IMPORT time fixed that but introduced a
# narrower race -- app.py's live-branch routes (_dash_today app.py:1222,
# _today_et app.py:2726, _compute_portfolio_strip's own fallback app.py:1502)
# each resolve datetime.now(_ET) FRESH at REQUEST time, so a CI run whose
# import happens pre-midnight-ET and whose HTTP requests happen post-
# midnight-ET desyncs this module's seeded _TRADING_DAY from what the route
# actually resolves. Fix: freeze app.py's clock to a FIXED instant (see
# _frozen_route_clock below) and derive _TRADING_DAY from that SAME instant
# -- both sides are now the same static value by construction, for every
# test in this module, regardless of real wall-clock time. This also makes
# _TRADING_DAY a genuinely static constant (no more real-time dependency to
# age out at all).
#
# Chosen deliberately DISTINCT from BOTH TestClockBoundaryDeterminism boundary
# days ("2026-08-20"/"2026-08-21", code-review round 2 F2): this default MUST
# NOT coincidentally match either boundary day, or that boundary test's own
# per-test freeze override becomes redundant -- the assertion would still
# pass even if the override silently no-op'd, since the (unrelated) default
# freeze would independently produce the same day. 2026-08-15 ET has no such
# collision.
_FROZEN_INSTANT = datetime(
    2026, 8, 15, 18, 30, 0, tzinfo=UTC
)  # arbitrary, far from any ET midnight boundary AND from both boundary-test days
_TRADING_DAY = _FROZEN_INSTANT.astimezone(_ET).strftime("%Y-%m-%d")
_FrozenDatetime = _frozen_datetime_class(_FROZEN_INSTANT)


@pytest.fixture(autouse=True)
def _frozen_route_clock(monkeypatch):
    """Freezes datetime.now() calls inside app.py to _FROZEN_INSTANT for the
    duration of each test in this module.

    SCOPE (code-review round 2, F1 -- corrected from an earlier overclaim):
    this patches ONLY the module-level name ``app_module.datetime`` (app.py
    does ``from datetime import ... datetime`` at module scope, so that name
    is directly patchable). It does NOT freeze:
      - function-local ``from datetime import datetime`` re-imports elsewhere
        in app.py (e.g. force_eod() app.py:3939, resend_discord() app.py:4000)
        -- those re-bind a fresh, unpatched reference at call time.
      - analytics.py's own request-time datetime fallback (analytics.py:
        565-568) -- a function-local import there too.

    INVARIANT this harness relies on: every route this test file drives
    (``/``, ``/api/state``, ``/api/strip/<window>``) threads an explicit
    ``trading_day`` end-to-end (confirmed via tests/analytics/test_bl6_
    today_change_et_fallback.py's TestExplicitTradingDaySitesUnaffected AST
    scan), so analytics.py's own fallback is dead code on every path this
    file exercises -- freezing app_module.datetime alone is therefore
    sufficient. A FUTURE test added to this file that drives a route NOT
    covered by that invariant (e.g. one of the un-threaded call sites above,
    or a genuinely new route) must either (a) confirm that route also
    threads trading_day explicitly, or (b) extend this freeze to cover that
    route's actual clock source -- do not assume this fixture alone
    guarantees determinism for an untested code path.
    """
    monkeypatch.setattr(app_module, "datetime", _FrozenDatetime)


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
    db_path: str,
    symphony_id: str,
    shadow_return: float,
    current_return: float,
    trading_day: str | None = None,
) -> None:
    """trading_day defaults to the module's frozen _TRADING_DAY -- all
    existing call sites are byte-unchanged. TestClockBoundaryDeterminism
    passes an explicit trading_day to seed a DIFFERENT boundary-instant day.
    """
    _day = trading_day if trading_day is not None else _TRADING_DAY
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shadow_history "
        "(ts_utc, ts_et, trading_day, symphony_id, current_return, shadow_return) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"{_day}T13:30:02Z",
            f"{_day}T13:30:02",
            _day,
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

    ORDERING PRECONDITION (code-review round 2, F4): reads app_module.
    datetime.now(_ET) below, so it picks up whichever clock is ACTIVE at
    CALL TIME -- the module-default _FROZEN_INSTANT (installed by the
    autouse _frozen_route_clock fixture before every test body runs), or a
    TestClockBoundaryDeterminism test's own per-test monkeypatch override.
    Callers that install a per-test clock override (e.g.
    ``monkeypatch.setattr(app_module, "datetime", ...)``) MUST do so BEFORE
    calling this helper, or last_successful_cycle_at will be stamped from
    the wrong (still-default) clock. Using this test module's own unfrozen
    `datetime` here instead would contradict the static-clock invariant the
    rest of this module relies on.
    """
    state = database_module.load_state() or {}
    state["last_successful_cycle_at"] = app_module.datetime.now(_ET).isoformat()
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
        """app.py:2169 (frozen/closed-market snapshot branch), marker key
        ABSENT case. Same differential-value technique via /api/state's
        "state" JSON key (which carries the frozen branch's mutated
        per-symphony _tc dict -- see this file's module docstring for the
        JSON-shape trace).

        This covers only the marker-ABSENT half of the class's "(marker
        False/absent)" scope -- _seed_frozen_snapshot_symphony deliberately
        never sets current_return_is_reconstructed at all, so this test only
        exercises get_symphony_today_change's absent-key fallback branch
        (analytics.py:585-586). See
        test_frozen_snapshot_branch_card_if_held_unchanged_for_explicit_false_marker
        below for the companion marker-PRESENT-but-False case (HELD-BASIS
        HARDENING: confirmed via mutation testing that this test alone
        cannot catch a bug isolated to the key-present branch, analytics.py:
        583-584 -- the two tests are non-redundant).
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

    def test_frozen_snapshot_branch_card_if_held_unchanged_for_explicit_false_marker(
        self, frozen_client
    ):
        """app.py:2169 (frozen/closed-market snapshot branch), marker key
        PRESENT-BUT-FALSE case (HELD-BASIS HARDENING re-arm). Companion to
        the ABSENT-key test above -- uses
        _seed_frozen_snapshot_symphony_with_marker(is_reconstructed=False,
        triggered=False) so current_return_is_reconstructed is explicitly
        present with value False, exercising analytics.py:583-584's
        key-presence branch (an explicit False must be respected, never
        silently treated the same as "absent, fall through to sym_dict") --
        the branch F4's whole "key-presence check, not truthiness" design
        exists for, and which the ABSENT-key test above structurally cannot
        reach (confirmed via mutation testing: forcing key-present branch to
        always-True left the absent-key test green).
        """
        sym_id = "frozen-card-explicit-false-marker"
        _seed_frozen_snapshot_symphony_with_marker(
            sym_id,
            name="Frozen Card Explicit False Marker",
            value=10000.0,
            current_return=3.5,
            is_reconstructed=False,
            triggered=False,
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=3.5, current_return=8.5)

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

        assert tc.get("if_held") == pytest.approx(3.5, abs=1e-6), (
            f"AC-2 FAIL: app.py:2169 (frozen snapshot branch) with an EXPLICIT False marker "
            f"must render the snapshot's bot_state-derived if_held (3.5), never the shadow "
            f"row's differing current_return (8.5); got if_held={tc.get('if_held')} -- an "
            f"explicit False on the key-present branch (analytics.py:583-584) must be "
            f"respected exactly like the absent-key fallback, not silently overridden"
        )

    def test_live_poll_aggregate_and_card_if_held_unchanged_for_untriggered_symphony(self, client):
        """app.py:1508-1514->:1601 (aggregate strip) and app.py:2624-2630->:2675
        (live poll card) -- BOTH threading sites, marker explicitly False.
        Both must render the bot_state value, matching pre-fix behavior,
        proving Option B's marker-gating (not an always-prefer-shadow swap).

        Caches cleared (matching AC-3/AC-5's convention) so the direct
        if_held/dry_run pins below deterministically hit the Tier-2 VW floor
        rather than a warm account-basis branch left over from a preceding
        test in this same class/module.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

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
        # HELD-BASIS HARDENING re-arm (was a vacuous `dry_run == if_held` relative
        # check): this fixture seeds shadow_return (4.0) coincidentally EQUAL to
        # bot_state.current_return (4.0), so _value_weighted_portfolio's paired
        # guard_delta_vw is mathematically forced to exactly 0 regardless of
        # whether app.py's Tier-2-floor re-derivation (app.py:1657-1662) is
        # implemented correctly -- 0 times any (even a badly-scaled) coefficient
        # is still 0. Confirmed via mutation testing: scaling _floor_guard_delta
        # by 2x in that formula left the old relative assertion green. Pin the
        # DIRECT expected value instead (mirrors this file's own F8-fix precedent
        # of replacing a weak relative/existence check with an exact pin) -- this
        # also independently proves app.py:1508-1514's aggregate call site
        # (a DIFFERENT call site than the per-symphony card asserted above)
        # correctly suppresses the marker=False override too.
        assert today_change.get("if_held") == pytest.approx(4.0, abs=1e-6), (
            f"AC-2 FAIL: the aggregate today_change.if_held (app.py:1508-1514 -> :1601 call "
            f"site) must remain the bot_state-derived value (4.0, marker=False), never leak "
            f"the shadow row's differing current_return (9.0); got {today_change}"
        )
        assert today_change.get("dry_run") == pytest.approx(4.0, abs=1e-6), (
            f"AC-2 sanity: with a single untriggered symphony and no divergence, the aggregate "
            f"today_change.dry_run (sourced from shadow_return, independent of the marker) "
            f"must also be 4.0; got {today_change}"
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
          - the aggregate portfolio_strip.today_change converges to the
            EXACT weighted-average shadow-basis Held (F8 fix -- was a
            vacuous `is not None` check that passed identically against the
            pre-fix code; now genuinely RED-pinning)
          - the triggered symphony's own live-poll card (tc_held) equals the
            raw shadow current_return, not the reconstructed value
        proving marker-threading reaches the seam through the real route.

        F8 fix: account-totals caches are explicitly cleared (established
        pattern, tests/dashboard/test_eod_account_basis.py) to force the
        deterministic Tier-2 "no cached account totals" floor branch, so
        today_change.if_held is the plain full-membership VW weighted
        average (same basis + tolerance rationale as tests/analytics/
        test_held_basis_convergence.py's TestPortfolioAggregateGoldenFixture)
        -- without this, the test's expected value would depend on
        cross-test global cache state left over from other tests in the
        same pytest session.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

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
        # F8 FIX (PR #125 review): the prior assertion here was `if_held is not None`,
        # which is vacuous -- it passes IDENTICALLY against the pre-fix code (both the
        # reconstructed-basis +0.192476pp and the shadow-basis +0.209465pp aggregates are
        # non-None). Pin the EXACT converged value, derived from the fixture the same way
        # tests/analytics/test_held_basis_convergence.py's TestPortfolioAggregateGoldenFixture
        # does (never a hardcoded literal the fixture can't itself reproduce). Because
        # both account-totals caches were cleared above, this is the Tier-2 floor branch --
        # if_held is the plain full-membership VW weighted average of shadow_history.
        # current_return (AC-6 contract: full membership even post the F2 coverage-scaling
        # fix, which affects only dry_run).
        expected_if_held = sum(s["value"] * s["shadow_history_current_return"] for s in rows) / sum(
            s["value"] for s in rows
        )
        assert today_change.get("if_held") == pytest.approx(expected_if_held, abs=1e-6), (
            f"AC-3/F8 FAIL: the route-level aggregate today_change.if_held must converge to "
            f"the exact weighted-average shadow basis ({expected_if_held}), not just be "
            f"non-None; got {today_change.get('if_held')} -- a value near +0.1959 (the "
            f"unmarked bot_state basis) means the marker never reached this seam through the "
            f"real route despite AC-1's unit-level proof that the seam itself is correct"
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

    def test_coverage_gap_with_real_divergence_renders_coverage_scaled_delta_not_paired_mean(
        self, client
    ):
        """F2 route-level proof. Same 2-of-3 coverage shape as tests/analytics/
        test_held_basis_convergence.py's unit-level F2 fix, but through the
        real /api/state seam -- specifically targeting the trap _compute_
        portfolio_strip's Tier-2 "no cached account totals" floor branch
        creates: THREE separate get_portfolio_today_change call sites inside
        _compute_portfolio_strip (the Tier-0 warm-cache branch, Tier-1
        last-good branch, and this Tier-2 floor branch) each independently
        need include_paired_guard_delta=True after F6 makes the function's
        own default False -- missing even ONE of the three silently
        reintroduces FINDING-2's bug in exactly that branch. Caches are
        cleared to deterministically exercise the Tier-2 branch (app.py:
        1626-1644), which ALSO does its own guard_delta_vw presence check
        (:1637) -- a second place F6's default-False change could silently
        break F2's fix if the call site isn't updated to opt in.

        Two covered symphonies at +1.0pp divergence each (dry_run=2.0,
        if_held=1.0), one coverage-gap symphony with a 5.0pp if_held outlier
        and no shadow row today -> honest coverage-scaled delta = 1.0 *
        (2000/3000) = 0.6667pp (not the paired-mean 1.0pp, not the OLD
        mismatched-denominator ~-0.333pp).
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        _seed_live_bot_state_symphony(
            "route-f2-a", name="Route F2 A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-f2-a", shadow_return=2.0, current_return=2.0
        )

        _seed_live_bot_state_symphony(
            "route-f2-b", name="Route F2 B", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-f2-b", shadow_return=2.0, current_return=2.0
        )

        # Coverage-gap symphony: real bot_state entry, deliberately NO shadow_history row today.
        _seed_live_bot_state_symphony(
            "route-f2-c",
            name="Route F2 C (no shadow row today, 5.0pp outlier)",
            value=1000.0,
            current_return=5.0,
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
        assert delta == pytest.approx(2.0 / 3.0, abs=1e-4), (
            f"F2 FAIL: coverage-scaled Today-row delta over 2-of-3 coverage with +1.0pp "
            f"paired-mean divergence must be exactly +0.6667pp; got delta={delta} "
            f"(today_change={today_change}). A value near +1.0 means the paired MEAN leaked "
            f"through unscaled (F2's exact bug); a value near -0.333 means the pre-this-PR "
            f"mismatched-denominator formula is in effect (guard_delta_vw wasn't opted into "
            f"at this call site at all -- F6's default-False regressed F2's fix here)."
        )

    def test_tier0_warm_cache_branch_applies_coverage_scaled_delta_not_paired_mean(self, client):
        """Revise-cycle addition (post-GREEN, held-imp mutation-check finding
        2026-08-13): the same F2 coverage-scaled-delta proof as the test
        above, but forcing the Tier-0 WARM-CACHE branch (app.py:~1611-1619)
        instead of the Tier-2 floor. This is the branch
        test_account_basis_tc.py's F9-updated seam mocks structurally
        CANNOT exercise for this specific regression class -- those tests
        `patch("analytics.get_portfolio_today_change", return_value=...)`,
        which replaces the real function entirely, so whether the call site
        actually passes include_paired_guard_delta=True is never tested end
        to end. held-imp's mutation-check (disabling this one call site's
        opt-in, one at a time, full 3-file suite re-run) confirmed this
        produced 52/52 STILL GREEN -- this test closes that gap.

        account_value is seeded equal to symphony_value_sum (no cash) so
        invested_frac == 1.0 by construction, making the account-basis
        delta numerically identical to the Tier-2 floor's +0.6667pp
        expectation (see get_portfolio_today_change_account_basis's
        docstring: dry_run_account = account_if_held_tc + guard_delta_vw *
        invested_frac).
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()
        app_module._account_totals_cache["portfolio_value"] = 3000.0
        app_module._account_totals_cache["portfolio_tc"] = 5.0

        _seed_live_bot_state_symphony(
            "route-t0-a", name="Route Tier0 A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-t0-a", shadow_return=2.0, current_return=2.0
        )

        _seed_live_bot_state_symphony(
            "route-t0-b", name="Route Tier0 B", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-t0-b", shadow_return=2.0, current_return=2.0
        )

        # Coverage-gap symphony: real bot_state entry, deliberately NO shadow_history row today.
        _seed_live_bot_state_symphony(
            "route-t0-c",
            name="Route Tier0 C (no shadow row today, 5.0pp outlier)",
            value=1000.0,
            current_return=5.0,
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        portfolio_strip = data.get("portfolio_strip") or {}
        today_change = portfolio_strip.get("today_change") or {}

        assert (
            today_change.get("if_held") is not None and today_change.get("dry_run") is not None
        ), f"expected a real non-degraded today_change; got {today_change}"
        assert today_change["if_held"] == pytest.approx(5.0, abs=1e-6), (
            f"sanity: if_held must pass through the cached account_if_held_tc (5.0) "
            f"unchanged; got {today_change['if_held']}"
        )
        delta = float(today_change["dry_run"]) - float(today_change["if_held"])
        assert delta == pytest.approx(2.0 / 3.0, abs=1e-4), (
            f"F2/Tier-0 FAIL: the Tier-0 warm-cache branch's Today-row delta over 2-of-3 "
            f"coverage with +1.0pp paired-mean divergence must be exactly +0.6667pp "
            f"(invested_frac=1.0 by construction); got delta={delta} "
            f"(today_change={today_change}). A value near +1.0 means "
            f"include_paired_guard_delta=True was not passed to get_portfolio_today_change "
            f"at the Tier-0 call site (app.py:~1611-1619) -- the legacy "
            f"vw_tc['dry_run']-vw_tc['if_held'] fallback leaked the raw unscaled paired mean "
            f"through instead."
        )

    def test_tier1_last_good_branch_applies_coverage_scaled_delta_not_paired_mean(self, client):
        """Same F2 coverage-scaled-delta proof as the two tests above, forcing
        the Tier-1 LAST-GOOD fallback branch (app.py:~1628-1636) -- the
        SECOND branch with no dedicated end-to-end coverage until now.
        held-imp's mutation-check confirmed disabling this call site's
        opt-in also produced 52/52 STILL GREEN.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()
        app_module._account_totals_last_good["portfolio_value"] = 3000.0
        app_module._account_totals_last_good["portfolio_tc"] = 5.0

        _seed_live_bot_state_symphony(
            "route-t1-a", name="Route Tier1 A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-t1-a", shadow_return=2.0, current_return=2.0
        )

        _seed_live_bot_state_symphony(
            "route-t1-b", name="Route Tier1 B", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "route-t1-b", shadow_return=2.0, current_return=2.0
        )

        _seed_live_bot_state_symphony(
            "route-t1-c",
            name="Route Tier1 C (no shadow row today, 5.0pp outlier)",
            value=1000.0,
            current_return=5.0,
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
        assert delta == pytest.approx(2.0 / 3.0, abs=1e-4), (
            f"F2/Tier-1 FAIL: the Tier-1 last-good branch's Today-row delta over 2-of-3 "
            f"coverage with +1.0pp paired-mean divergence must be exactly +0.6667pp; got "
            f"delta={delta} (today_change={today_change}). A value near +1.0 means "
            f"include_paired_guard_delta=True was not passed to get_portfolio_today_change "
            f"at the Tier-1 call site (app.py:~1628-1636)."
        )


# ---------------------------------------------------------------------------
# F6 -- guard_delta_vw must never leak into a public JSON response shape.
# ---------------------------------------------------------------------------


class TestF6NoInternalKeyLeakage:
    def test_api_strip_today_change_is_exactly_two_keys(self, client):
        """GET /api/strip/<window> is F1's fifth consumer -- it must never
        surface the internal guard_delta_vw intermediate. Response shape
        must stay {"if_held", "dry_run"} regardless of coverage state.
        """
        _seed_live_bot_state_symphony(
            "f6-strip-a", name="F6 Strip A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "f6-strip-a", shadow_return=1.0, current_return=1.0
        )

        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        today_change = data.get("today_change") or {}
        assert set(today_change.keys()) == {"if_held", "dry_run"}, (
            f"F6 FAIL: /api/strip/<window>'s today_change must be exactly "
            f"{{'if_held', 'dry_run'}} -- got keys {sorted(today_change.keys())}. A leaked "
            f"'guard_delta_vw' key here means include_paired_guard_delta wasn't defaulted "
            f"to False, or the route-level compute_windowed_portfolio_strip caller opted in "
            f"without stripping it before jsonify."
        )

    def test_api_state_tier2_floor_today_change_is_exactly_two_keys(self, client):
        """The live Tier-2 "no cached account totals" floor branch (app.py:
        1626-1644) internally NEEDS guard_delta_vw for its own paired
        re-derivation (F2) -- but the FINAL today_change dict it constructs
        and returns must still be the public 2-key shape, never leaking the
        internal key through to the JSON response.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        _seed_live_bot_state_symphony(
            "f6-state-a", name="F6 State A", value=1000.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "f6-state-a", shadow_return=2.0, current_return=1.0
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        today_change = (data.get("portfolio_strip") or {}).get("today_change") or {}
        assert set(today_change.keys()) == {"if_held", "dry_run"}, (
            f"F6 FAIL: /api/state's Tier-2-floor today_change must be exactly "
            f"{{'if_held', 'dry_run'}} -- got keys {sorted(today_change.keys())}. The Tier-2 "
            f"branch's own internal re-derivation (F2/F5) must construct a fresh 2-key dict, "
            f"never return the raw guard_delta_vw-carrying dict it computed from."
        )

    def test_api_state_tier2_floor_zero_paired_coverage_strips_guard_delta_vw_key(self, client):
        """DE-FR-SIMPLIFY-002 (gdvw leak): the sibling test above only exercises
        the Tier-2 floor's IF branch (app.py:1658 -- paired coverage present,
        `_floor_guard_delta is not None`), which already builds a clean fresh
        2-key dict. It never reaches the ELSE branch (app.py:1664), which does
        `today_change = _vw_tc_floor` -- a verbatim passthrough of the raw
        analytics dict, still carrying the internal `guard_delta_vw` key.

        Zero paired coverage (no symphony has a same-trading-day shadow_history
        row) makes analytics.get_portfolio_today_change's dry_run_weight == 0.0
        (analytics.py:1178/1188-1190), so guard_delta_vw resolves None and the
        route falls into the ELSE branch -- reproducing the leak.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        # ONE symphony, valid weight + if_held (bot_state alone is enough --
        # app.py:1504-1521 builds symphonies_list straight from bot_state) --
        # but deliberately NO _insert_shadow_row call for it, and no other
        # symphony seeded, so the portfolio-wide dry_run_weight is exactly 0.0.
        _seed_live_bot_state_symphony(
            "gdvw-tier2-else-a", name="GDVW Tier2 Else A", value=1000.0, current_return=1.5
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        today_change = (data.get("portfolio_strip") or {}).get("today_change") or {}
        assert set(today_change.keys()) == {"if_held", "dry_run"}, (
            f"gdvw leak FAIL: /api/state's Tier-2-floor today_change must be exactly "
            f"{{'if_held', 'dry_run'}} on the ZERO-PAIRED-COVERAGE else branch too -- got "
            f"keys {sorted(today_change.keys())}. app.py's else branch (`today_change = "
            f"_vw_tc_floor`) is a raw passthrough of the analytics dict, which still carries "
            f"the internal 'guard_delta_vw' key when paired coverage is zero."
        )
        assert today_change["dry_run"] is None, (
            "gdvw leak FAIL: zero paired coverage must propagate dry_run=None honestly, "
            f"never fabricate a value -- got {today_change['dry_run']!r}."
        )
        # 1.5 is this test's OWN seeded current_return input (not a producer-computed
        # value) -- with a single symphony, the VW if_held average collapses to that
        # one input exactly; approx is defensive float-division slop only.
        assert today_change["if_held"] == pytest.approx(1.5, abs=1e-9), (
            f"gdvw leak FAIL: if_held must be preserved honestly from the floor's real "
            f"number -- got {today_change['if_held']!r}, expected ~1.5."
        )


# ---------------------------------------------------------------------------
# F1 -- GET /api/strip/<window> is a fifth, never-enumerated consumer of
# get_symphony_today_change; its sym_dict build must thread the marker (via
# the F4 pivot, this requires zero app.py diff since it already passes the
# real bot_state through).
# ---------------------------------------------------------------------------


class TestF1ApiStripConsumer:
    def test_windowed_strip_today_change_converges_for_triggered_marked_symphony(self, client):
        """Reviewer's failure scenario: /api/state renders the corrected
        Held; the operator clicks a hero window button -> /api/strip/<window>
        -> the UI overwrites the Today row with PRE-FIX values (oscillation).
        Seeds ONE triggered+marked symphony with a shadow row (raw if-held
        DIFFERENT from the reconstructed bot_state value), hits BOTH /api/
        state and /api/strip/30d, and asserts they render the IDENTICAL
        if_held -- proving no oscillation across the two endpoints.
        """
        sym_id = "f1-strip-triggered"
        _seed_live_bot_state_symphony(
            sym_id,
            name="F1 Strip Triggered Symphony",
            value=10000.0,
            current_return=2.5,  # the reconstructed bot_state value
            is_reconstructed=True,
            triggered=True,
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], sym_id, shadow_return=4.0, current_return=6.0
        )  # raw shadow if-held (6.0) DIFFERENT from reconstructed (2.5)

        state_resp = client.get("/api/state")
        assert state_resp.status_code == 200
        state_data = state_resp.get_json()
        state_symphonies = {s.get("id"): s for s in (state_data.get("symphonies") or [])}
        assert sym_id in state_symphonies

        strip_resp = client.get("/api/strip/30d")
        assert strip_resp.status_code == 200, (
            f"unexpected status: {strip_resp.status_code} {strip_resp.get_data()!r}"
        )
        strip_data = strip_resp.get_json()
        strip_today_change = strip_data.get("today_change") or {}

        assert strip_today_change.get("if_held") == pytest.approx(6.0, abs=1e-6), (
            f"F1 FAIL: /api/strip/30d's today_change.if_held must render the raw shadow "
            f"basis (6.0) for the triggered+marked symphony, not the reconstructed bot_state "
            f"value (2.5); got {strip_today_change.get('if_held')} -- this is the fifth, "
            f"never-enumerated consumer of get_symphony_today_change (app.py:3043 sym_dict "
            f"build -> compute_windowed_portfolio_strip), missed by the audit's original "
            f"caller enumeration and the DECISIONS.md '2 sites, not 4' analysis"
        )

        # Anti-oscillation: /api/strip's single-symphony VW if_held must match /api/state's
        # per-symphony card, since they're both single-symphony 100%-weight aggregates here.
        assert strip_today_change.get("if_held") == pytest.approx(
            state_symphonies[sym_id].get("tc_held"), abs=1e-6
        ), (
            f"F1 FAIL (oscillation): /api/strip's if_held ({strip_today_change.get('if_held')}) "
            f"must match /api/state's per-symphony tc_held "
            f"({state_symphonies[sym_id].get('tc_held')}) -- a mismatch here is exactly the "
            f"operator-visible oscillation the reviewer's failure scenario describes: /api/"
            f"state shows the corrected value, then a hero window-picker click flips it back."
        )


# ---------------------------------------------------------------------------
# F3 (via F4 pivot) -- dashboard() SSR route's per-card build must now
# converge for a triggered+marked symphony (zero app.py diff needed: _s IS
# the real bot_state entry, already carrying BL-9's marker).
# ---------------------------------------------------------------------------


class TestF3DashboardSSRTriggeredCard:
    def test_ssr_card_converges_to_shadow_basis_for_triggered_marked_symphony(self, client):
        """Reviewer's failure scenario: page load during market hours on a
        trigger day -- the hero strip (already threaded) shows the corrected
        Held while the SAME symphony's own card shows the reconstructed
        value -- a same-page contradiction. Post-F4, both must agree.
        """
        sym_id = "f3-ssr-triggered"
        _seed_live_bot_state_symphony(
            sym_id,
            name="F3 SSR Triggered Symphony",
            value=10000.0,
            current_return=2.5,  # reconstructed bot_state value
            is_reconstructed=True,
            triggered=True,
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], sym_id, shadow_return=4.0, current_return=6.0
        )  # raw shadow if-held (6.0), different from reconstructed (2.5)

        resp = client.get("/")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code}"
        html = resp.get_data(as_text=True)

        assert ">+6.0%<" in html, (
            "F3 FAIL: dashboard() SSR route's per-card tc-held cell must render the raw shadow "
            "basis (+6.0%) for a triggered+marked symphony, not the reconstructed bot_state "
            "value (+2.5%) -- app.py:1229-1238 builds _sym_dict without the marker key, but "
            "_s (passed as bot_state_entry) IS the real bot_state entry and already carries "
            "BL-9's marker; F4's pivot must read from it. Full response follows for triage:\n"
            + html[:4000]
        )
        assert ">+2.5%<" not in html, (
            "F3 FAIL: the reconstructed bot_state value (+2.5%) must not appear on the "
            "SSR-rendered card for this triggered+marked symphony -- that would reproduce "
            "the reviewer's same-page contradiction (hero strip shadow-basis vs. card "
            "reconstructed-basis)"
        )


# ---------------------------------------------------------------------------
# F5 -- frozen/closed-market branch: (a) Tier-2 floor needs the same paired
# re-derivation as its live twin, (b) the hand-built _snap_bot_state needs
# the marker key added, (c) a per-symphony frozen card converges via F4 with
# zero additional threading, (d) the frozen AC-4-equivalent fallback.
# ---------------------------------------------------------------------------


def _seed_frozen_snapshot_symphony_with_marker(
    sym_id: str,
    *,
    name: str,
    value: float,
    current_return: float,
    is_reconstructed: bool,
    triggered: bool = True,
) -> None:
    """F5(b)/(d) fixture: unlike _seed_frozen_snapshot_symphony (which
    deliberately never sets the marker, documenting that the EOD capture
    site's OWN reset loop always zeroes it first), this variant explicitly
    sets current_return_is_reconstructed -- reproducing the reviewer's F5
    EOD-fetch-failure gap: alpha_bot_execution.py's marker-reset loop
    (:1044-1057) iterates symphony_data_cache, but the snapshot builder
    (:1066/:1120) iterates ALL of bot_state -- a symphony whose Composer
    fetch failed at the EOD run is skipped by the reset loop but still
    captured by the snapshot builder with its LEFTOVER marker value from
    earlier in the day. This is therefore a real, reachable production
    shape for a frozen snapshot entry, not a synthetic/unreachable one.

    triggered defaults to True (all 3 original F5 call sites' behavior is
    byte-unchanged) -- HELD-BASIS HARDENING passes triggered=False to build
    an honest "untriggered symphony, marker explicitly False" fixture for
    AC-2's frozen-branch coverage gap (see
    test_frozen_snapshot_branch_card_if_held_unchanged_for_explicit_false_marker).
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
            "triggered": triggered,
            "current_return": current_return,
            "current_return_is_reconstructed": is_reconstructed,
            "current_value": value,
            "simple_return": 0.02,
            "net_deposits": 100.0,
            "time_weighted_return": 0.021,
            "max_drawdown": 0.05,
        }
    )
    state["last_market_close_snapshot"] = snapshot
    database_module.save_state(state)


class TestF5FrozenBranchConvergence:
    def test_frozen_per_symphony_card_converges_for_triggered_marked_snapshot_entry(
        self, frozen_client
    ):
        """F5(c). The per-symphony frozen loop (app.py:2170-2213) passes
        _sym (the real snapshot entry) as bot_state_entry -- F4's pivot means
        this converges with ZERO additional app.py threading at this specific
        loop, since _sym already carries whatever marker the snapshot
        captured (see _seed_frozen_snapshot_symphony_with_marker's docstring
        for why marker=True is a real, reachable snapshot shape).
        """
        sym_id = "f5-frozen-card-triggered"
        _seed_frozen_snapshot_symphony_with_marker(
            sym_id,
            name="F5 Frozen Card Triggered",
            value=10000.0,
            current_return=2.5,
            is_reconstructed=True,
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=4.0, current_return=6.0)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen"
        state = data.get("state") or {}
        assert sym_id in state
        tc = (state[sym_id] or {}).get("_tc") or {}

        assert tc.get("if_held") == pytest.approx(6.0, abs=1e-6), (
            f"F5(c) FAIL: the frozen per-symphony card for a triggered+marked snapshot entry "
            f"must render the raw shadow basis (6.0), not the reconstructed value (2.5); "
            f"got if_held={tc.get('if_held')}"
        )

    def test_frozen_per_symphony_card_marker_true_no_shadow_row_falls_back(self, frozen_client):
        """F5(d): the frozen AC-4 equivalent. Marker True on the snapshot
        entry, but no same-day shadow row exists -> falls back to the
        snapshot's own bot_state-derived value, never crashes.
        """
        sym_id = "f5-frozen-card-fallback"
        _seed_frozen_snapshot_symphony_with_marker(
            sym_id,
            name="F5 Frozen Card Fallback",
            value=10000.0,
            current_return=2.5,
            is_reconstructed=True,
        )
        # Deliberately NO shadow row inserted for sym_id today.

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        state = data.get("state") or {}
        assert sym_id in state
        tc = (state[sym_id] or {}).get("_tc") or {}

        assert tc.get("if_held") == pytest.approx(2.5, abs=1e-6), (
            f"F5(d) FAIL: marker True + no shadow row on the frozen per-symphony card must "
            f"fall back to the snapshot's own current_return (2.5), never crash or fabricate; "
            f"got if_held={tc.get('if_held')}"
        )

    def test_frozen_aggregate_converges_for_triggered_marked_snapshot_entry(self, frozen_client):
        """F5(b): the frozen PORTFOLIO-LEVEL aggregate (_snap_vw_tc, built
        from the hand-built 4-key _snap_bot_state at app.py:2277) needs the
        marker key explicitly added -- unlike the per-symphony loop, this
        aggregate does NOT get F4's fix for free, since _snap_bot_state is
        NOT the real bot_state entry.
        """
        sym_id = "f5-frozen-agg-triggered"
        _seed_frozen_snapshot_symphony_with_marker(
            sym_id,
            name="F5 Frozen Aggregate Triggered",
            value=10000.0,
            current_return=2.5,
            is_reconstructed=True,
        )
        _insert_shadow_row(os.environ["DB_PATH"], sym_id, shadow_return=4.0, current_return=6.0)

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen"
        today_change = (data.get("portfolio_strip") or {}).get("today_change") or {}

        assert today_change.get("if_held") == pytest.approx(6.0, abs=1e-6), (
            f"F5(b) FAIL: the frozen portfolio-level aggregate today_change.if_held (single "
            f"100%-weight symphony) must render the raw shadow basis (6.0), not the "
            f"reconstructed value (2.5); got {today_change.get('if_held')} -- this requires "
            f"adding current_return_is_reconstructed to _snap_bot_state (app.py:2277), the "
            f"ONE hand-built bot_state shape F4's pivot does not cover for free"
        )

    def test_frozen_tier2_floor_uses_coverage_scaled_delta_not_raw_vw_passthrough(
        self, frozen_client
    ):
        """F5(a). The frozen Tier-2 fallback (app.py:2358-2362, `_snap_tc_
        final = _snap_vw_tc`) currently surfaces the RAW VW dict directly --
        no account-basis conversion, no paired re-derivation at all -- unlike
        its live twin (app.py:1626-1644) which DOES re-derive dry_run from
        the paired guard_delta_vw. Two covered symphonies at +1.0pp
        divergence each, one coverage-gap symphony with a 5.0pp if_held
        outlier and no shadow row -> honest coverage-scaled delta = 1.0 *
        (2000/3000) = 0.6667pp, matching F2's live-branch fix exactly.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        state = database_module.load_state() or {}
        snapshot = {
            "captured_at_et": "16:00:00 ET",
            "data_as_of": "16:00 ET",
            "trading_day": _TRADING_DAY,
            "accounts_map": {"ACC1": []},
        }
        for sym_id, cr in (("f5a-cov-a", 1.0), ("f5a-cov-b", 1.0), ("f5a-gap-c", 5.0)):
            snapshot["accounts_map"]["ACC1"].append(
                {
                    "id": sym_id,
                    "name": sym_id,
                    "account": "ACC1",
                    "armed": False,
                    "tp_armed": False,
                    "para_armed": False,
                    "triggered": False,
                    "current_return": cr,
                    "current_return_is_reconstructed": False,
                    "current_value": 1000.0,
                    "simple_return": 0.02,
                    "net_deposits": 100.0,
                    "time_weighted_return": 0.021,
                    "max_drawdown": 0.05,
                }
            )
        state["last_market_close_snapshot"] = snapshot
        database_module.save_state(state)

        _insert_shadow_row(
            os.environ["DB_PATH"], "f5a-cov-a", shadow_return=2.0, current_return=1.0
        )
        _insert_shadow_row(
            os.environ["DB_PATH"], "f5a-cov-b", shadow_return=2.0, current_return=1.0
        )
        # f5a-gap-c deliberately has NO shadow row.

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen"
        today_change = (data.get("portfolio_strip") or {}).get("today_change") or {}

        assert (
            today_change.get("if_held") is not None and today_change.get("dry_run") is not None
        ), f"expected a real non-degraded frozen today_change; got {today_change}"
        delta = float(today_change["dry_run"]) - float(today_change["if_held"])
        assert delta == pytest.approx(2.0 / 3.0, abs=1e-4), (
            f"F5(a) FAIL: the frozen Tier-2 floor's Today-row delta over 2-of-3 coverage with "
            f"+1.0pp paired-mean divergence must be exactly +0.6667pp (matching the live "
            f"branch's F2 fix), not the raw unscaled/mismatched-denominator VW passthrough; "
            f"got delta={delta} (today_change={today_change})"
        )

    def test_frozen_tier2_floor_zero_paired_coverage_strips_guard_delta_vw_key(
        self, frozen_client
    ):
        """DE-FR-SIMPLIFY-002 follow-up (code-review finding): the frozen twin
        of the live-branch else-branch leak. The sibling test above
        (test_frozen_tier2_floor_uses_coverage_scaled_delta_not_raw_vw_passthrough)
        only exercises the frozen Tier-2 floor's IF branch (app.py:2403-2410 --
        partial paired coverage present, `_snap_floor_guard_delta is not None`).
        It never reaches the ELSE branch (app.py:2412), which does
        `_snap_tc_final = _snap_vw_tc` -- a verbatim passthrough of the raw
        analytics dict, still carrying the internal `guard_delta_vw` key,
        structurally identical to the now-fixed live-branch bug at app.py:1664.

        Zero paired coverage (no symphony has a same-trading-day shadow_history
        row) makes analytics.get_portfolio_today_change's dry_run_weight == 0.0
        (analytics.py:1178/1188-1190), so guard_delta_vw resolves None and the
        frozen route falls into the ELSE branch -- reproducing the leak via
        market_state == "closed_frozen", a routine after-hours state.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        # ONE snapshot symphony, valid weight + if_held -- but deliberately NO
        # _insert_shadow_row call for it, and no other symphony seeded, so the
        # portfolio-wide dry_run_weight is exactly 0.0 (mirrors the live-branch
        # RED test's fixture, against the frozen snapshot shape instead).
        _seed_frozen_snapshot_symphony(
            "gdvw-frozen-tier2-else-a", name="GDVW Frozen Tier2 Else A", value=1000.0,
            current_return=1.5,
        )

        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        assert data.get("market_state") == "closed_frozen"
        today_change = (data.get("portfolio_strip") or {}).get("today_change") or {}
        assert set(today_change.keys()) == {"if_held", "dry_run"}, (
            f"gdvw leak FAIL (frozen twin): /api/state's frozen Tier-2-floor today_change must "
            f"be exactly {{'if_held', 'dry_run'}} on the ZERO-PAIRED-COVERAGE else branch too -- "
            f"got keys {sorted(today_change.keys())}. app.py's frozen else branch "
            f"(`_snap_tc_final = _snap_vw_tc`) is a raw passthrough of the analytics dict, which "
            f"still carries the internal 'guard_delta_vw' key when paired coverage is zero."
        )
        assert today_change["dry_run"] is None, (
            "gdvw leak FAIL (frozen twin): zero paired coverage must propagate dry_run=None "
            f"honestly, never fabricate a value -- got {today_change['dry_run']!r}."
        )
        # 1.5 is this test's OWN seeded current_return input (not a producer-computed
        # value) -- with a single symphony, the VW if_held average collapses to that
        # one input exactly; approx is defensive float-division slop only.
        assert today_change["if_held"] == pytest.approx(1.5, abs=1e-9), (
            f"gdvw leak FAIL (frozen twin): if_held must be preserved honestly from the "
            f"floor's real number -- got {today_change['if_held']!r}, expected ~1.5."
        )


# ---------------------------------------------------------------------------
# HELD-BASIS HARDENING -- proves the _frozen_route_clock mechanism holds at
# the LITERAL ET midnight boundary (the exact instant #127's race could
# occur), not just "usually" for whatever the real wall clock happens to be
# when the suite runs. Only the LIVE branch (/api/state via `client`) is
# vulnerable to this race -- the frozen branch's trading_day is read from
# the stored snapshot dict, never live-resolved (see module docstring).
#
# Revise (code-review round 1, F1 CRITICAL): the original version of these
# 2 tests seeded a marker-FALSE symphony and asserted tc_held == the seeded
# bot_state value -- but for marker=False, tc_held is sourced ENTIRELY from
# bot_state.current_return regardless of the shadow row or trading_day
# resolution, so both tests passed identically even with the freeze
# removed entirely (vacuous, proven via mutation testing below). Redesign:
# seed a TRIGGERED+MARKED (is_reconstructed=True) symphony whose bot_state.
# current_return is a deliberately WRONG value, and a shadow_history row at
# the boundary day carrying a DIFFERENT value -- the marker-gated override
# (analytics.py:599-600, DE-HELD-BASIS-001) only renders the shadow value
# when a matching-trading_day row is found, which only happens when the
# route resolves "today" == the frozen boundary day. This makes the
# assertion genuinely depend on the freeze succeeding.
# ---------------------------------------------------------------------------


class TestClockBoundaryDeterminism:
    def test_route_converges_when_clock_frozen_at_235959_et(self, client, monkeypatch):
        """Freezes app.py's clock to 23:59:59 ET on a fixed day D, seeds a
        TRIGGERED+MARKED symphony's shadow row dated D with a value DIFFERENT
        from its bot_state reconstructed value, and proves /api/state renders
        the shadow-basis value -- reachable ONLY when the route resolves
        "today" == D via the frozen clock -- one tick before the boundary
        #127's race could straddle.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        boundary_instant = datetime(2026, 8, 20, 23, 59, 59, tzinfo=_ET)
        boundary_day = "2026-08-20"
        assert boundary_instant.strftime("%Y-%m-%d") == boundary_day  # fixture sanity

        monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(boundary_instant))

        sym_id = "boundary-235959"
        _seed_live_bot_state_symphony(
            sym_id,
            name="Boundary 23:59:59 ET",
            value=1000.0,
            current_return=2.5,  # reconstructed bot_state value -- must NOT render
            is_reconstructed=True,
            triggered=True,
        )
        _insert_shadow_row(
            os.environ["DB_PATH"],
            sym_id,
            shadow_return=6.0,
            current_return=6.0,  # shadow-basis value -- renders ONLY on a trading_day match
            trading_day=boundary_day,
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        by_id = {s.get("id"): s for s in (data.get("symphonies") or []) if isinstance(s, dict)}
        assert sym_id in by_id, (
            f"expected {sym_id!r} among symphonies at the 23:59:59 ET boundary; got "
            f"{sorted(by_id.keys())!r}"
        )
        assert by_id[sym_id].get("tc_held") == pytest.approx(6.0, abs=1e-6), (
            f"boundary determinism FAIL at 23:59:59 ET: expected tc_held=6.0 (the shadow-basis "
            f"override, reachable ONLY when the route resolves 'today' == {boundary_day!r} and "
            f"finds the matching shadow_history row); got tc_held="
            f"{by_id[sym_id].get('tc_held')} -- 2.5 would mean the route resolved a DIFFERENT "
            f"day than the frozen clock intends (the exact #127 race), falling back to the "
            f"un-overridden reconstructed bot_state value"
        )

    def test_route_converges_when_clock_frozen_at_000001_et_next_day(self, client, monkeypatch):
        """Same proof, frozen 2 seconds later at 00:00:01 ET the NEXT calendar
        day -- the other side of the exact boundary #127's race straddled.
        """
        app_module._account_totals_cache.clear()
        app_module._account_totals_last_good.clear()

        boundary_instant = datetime(2026, 8, 21, 0, 0, 1, tzinfo=_ET)
        boundary_day = "2026-08-21"
        assert boundary_instant.strftime("%Y-%m-%d") == boundary_day  # fixture sanity

        monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(boundary_instant))

        sym_id = "boundary-000001"
        _seed_live_bot_state_symphony(
            sym_id,
            name="Boundary 00:00:01 ET",
            value=1000.0,
            current_return=3.5,  # reconstructed bot_state value -- must NOT render
            is_reconstructed=True,
            triggered=True,
        )
        _insert_shadow_row(
            os.environ["DB_PATH"],
            sym_id,
            shadow_return=7.0,
            current_return=7.0,  # shadow-basis value -- renders ONLY on a trading_day match
            trading_day=boundary_day,
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.get_data()!r}"
        data = resp.get_json()
        by_id = {s.get("id"): s for s in (data.get("symphonies") or []) if isinstance(s, dict)}
        assert sym_id in by_id, (
            f"expected {sym_id!r} among symphonies at the 00:00:01 ET (next-day) boundary; got "
            f"{sorted(by_id.keys())!r}"
        )
        assert by_id[sym_id].get("tc_held") == pytest.approx(7.0, abs=1e-6), (
            f"boundary determinism FAIL at 00:00:01 ET (next day): expected tc_held=7.0 (the "
            f"shadow-basis override, reachable ONLY when the route resolves 'today' == "
            f"{boundary_day!r} and finds the matching shadow_history row); got tc_held="
            f"{by_id[sym_id].get('tc_held')} -- 3.5 would mean the route resolved a DIFFERENT "
            f"day than the frozen clock intends (the exact #127 race), falling back to the "
            f"un-overridden reconstructed bot_state value"
        )
