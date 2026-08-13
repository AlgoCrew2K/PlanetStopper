"""
RED tests — DE-HELD-BASIS-001: Held Basis Convergence (Today-Row If-Held +
VW Denominator Parity).

Source: feature-plans/held-basis-convergence.md + docs/audit/HELD-VS-BOT-
DIVERGENCE-2026-08-13.md.

FINDING-1 (AC-1/AC-2/AC-4): analytics.get_symphony_today_change's if_held
currently ALWAYS reads sym_dict["last_percent_change"] (which app.py sources
from bot_state.current_return). For a triggered symphony, bot_state.current_
return is the TRUE SHADOW RETURN OVERRIDE's VWAP-based basket reconstruction
(alpha_bot_execution.py:1264-1274/:1660), NOT the raw Composer if-held
trajectory that shadow_history.current_return already carries (the same row
already fetched for the Bot/dry_run side). The fix (Option B, marker-gated):
when sym_dict.get("current_return_is_reconstructed") is truthy AND a same-day
shadow_history row exists, if_held prefers row["current_return"] (already
percent-scale in the DB -- NO further *100 scaling, unlike last_percent_change
which is Composer-decimal and DOES need *100). This is a UNIT-SCALE-SENSITIVE
fix -- a wrong-scale implementation (e.g. multiplying row["current_return"] by
100 again) is a plausible defect class this file specifically guards against.

FINDING-2 (AC-5/AC-6): analytics._value_weighted_portfolio divides if_held by
FULL membership (total_weight) but dry_run by DRY-RUN-ONLY membership
(dry_run_weight) -- a symphony with no same-day shadow row is silently
retained on the if_held side but dropped from dry_run, producing a phantom
guard_delta_vw = dry_run - if_held on a genuinely zero-exit coverage-gap day.
The fix must compute the delta over PAIRED membership (both sides restricted
to the same symphony set) while preserving the existing FULL-membership
if_held average for the Tier-2 "basis=value_weighted" floor consumer
(DE-EOD-BASIS-001) -- two distinct contracts, tested separately below.

Golden fixture: tests/fixtures/math/held_basis_convergence_live_capture.json
(the audit's §3 live capture, verbatim 11-symphony 2dp display table -- never
invented numbers). This file NEVER hardcodes a literal portfolio-aggregate
value that the fixture itself cannot reproduce via an obviously-correct
weighted-average -- see _weighted_average() below and the comment on
TestPortfolioAggregateGoldenFixture for the documented ~0.0034pp precision-
loss gap between the audit's own reported aggregates (computed from
higher-precision internal current_value figures) and what re-deriving from
the audit's own 2dp display table produces.

Route-level / end-to-end coverage (all 4 real caller shapes, AC-2 parity
sweep, AC-3 end-to-end, AC-5 primary coverage-gap proof) lives in
tests/app/test_held_basis_route_convergence.py -- per the plan's explicit
"whole-module mocking of analytics is a known false-green trap" warning,
this file never mocks analytics itself; it calls the real functions.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

import analytics

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "math" / "held_basis_convergence_live_capture.json"
)


@pytest.fixture(scope="module")
def golden_fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _weighted_average(rows: list[dict], weight_key: str, value_key: str) -> float:
    """Test-file-local pure helper -- an obviously-correct value-weighted
    average, NOT a reimplementation of analytics._value_weighted_portfolio's
    internals. Used to derive expected aggregates FROM the golden fixture's
    own numbers rather than hardcoding a literal the fixture can't reproduce.
    """
    total_weight = sum(r[weight_key] for r in rows)
    total = sum(r[weight_key] * r[value_key] for r in rows)
    return total / total_weight


# ---------------------------------------------------------------------------
# Shadow DB helpers (self-contained -- mirrors tests/analytics/test_m1_helpers.py's
# pattern, not imported from it, per this project's per-file-local-helper convention)
# ---------------------------------------------------------------------------

_TRADING_DAY = "2026-08-13"


def _make_shadow_db(tmp_path: Path, rows: list[tuple[str, float, float]], suffix: str = "") -> str:
    """rows: list of (symphony_id, shadow_return, current_return) for _TRADING_DAY."""
    db_file = str(tmp_path / f"shadow_held_basis{suffix}.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            current_return REAL NOT NULL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")
    for sym_id, shadow_return, current_return in rows:
        conn.execute(
            """INSERT INTO shadow_history
               (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                sym_id,
                f"{_TRADING_DAY}T13:30:02Z",
                f"{_TRADING_DAY}T13:30:02",
                _TRADING_DAY,
                current_return,
                shadow_return,
            ),
        )
    conn.commit()
    conn.close()
    return db_file


def _make_empty_shadow_db(tmp_path: Path, suffix: str = "") -> str:
    return _make_shadow_db(tmp_path, [], suffix=suffix)


def _make_cr_trajectory_shadow_db(
    tmp_path: Path, trajectories: dict[str, list[tuple[float, float]]], suffix: str = ""
) -> str:
    """Multi-day shadow_history rows keyed by symphony_id, each value a list
    of (shadow_return, current_return) pairs for consecutive trading days
    starting 2026-08-12 (the day before _TRADING_DAY). Symphony ids NOT
    present in `trajectories` get zero rows (the CR-path coverage-gap
    case). No position_epoch column -- _get_shadow_divergence_trajectory's
    fallback path treats the whole per-symphony history as one legacy
    epoch, which is exactly what these tests want (a clean 2-day trajectory,
    no epoch-boundary complexity).
    """
    db_file = str(tmp_path / f"shadow_held_basis_cr_traj{suffix}.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            current_return REAL NOT NULL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")
    _days = ["2026-08-12", _TRADING_DAY]
    for sym_id, pairs in trajectories.items():
        for day, (shadow_r, current_r) in zip(_days[: len(pairs)], pairs):
            conn.execute(
                """INSERT INTO shadow_history
                   (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    sym_id,
                    f"{day}T13:30:02Z",
                    f"{day}T13:30:02",
                    day,
                    current_r,
                    shadow_r,
                ),
            )
    conn.commit()
    conn.close()
    return db_file


def _make_shadow_db_with_null_current_return(
    tmp_path: Path, symphony_id: str, shadow_return: float, suffix: str = ""
) -> str:
    """A row exists for (symphony_id, _TRADING_DAY) but its current_return
    column is NULL -- a legacy/tampered-row degradation mode distinct from
    "no row at all" (_make_empty_shadow_db). Production's schema declares
    current_return REAL NOT NULL from inception (migrations/008_shadow_
    history.sql) -- this test-only schema deliberately omits that
    constraint so a NULL can be written at all; the point is proving the
    READ path degrades safely, not reproducing how a real NULL could get
    there in production.
    """
    db_file = str(tmp_path / f"shadow_held_basis_null_cr{suffix}.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            current_return REAL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")
    conn.execute(
        """INSERT INTO shadow_history
           (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return)
           VALUES (?, ?, ?, ?, NULL, ?)""",
        (
            symphony_id,
            f"{_TRADING_DAY}T13:30:02Z",
            f"{_TRADING_DAY}T13:30:02",
            _TRADING_DAY,
            shadow_return,
        ),
    )
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# AC-1 / AC-2 (unit) / AC-4 -- per-symphony marker-gated if_held
# ---------------------------------------------------------------------------


class TestPerSymphonyMarkerGatedIfHeld:
    def test_reconstructed_triggered_symphony_if_held_uses_shadow_current_return_not_bot_state(
        self, golden_fixture, tmp_path
    ):
        """AC-1 (FINDING-1 core). Golden single-symphony case from the audit:
        weight 1757.54, reconstructed bot_state-derived +0.39571, shadow
        current_return +0.53000 (raw if-held), frozen shadow_return +0.67000.
        Marker True + same-day shadow row present -> if_held must render
        +0.53000, NOT the reconstructed +0.39571.

        Unit-scale guard: row["current_return"] is ALREADY percent-scale in
        the DB (same convention as shadow_return, which the pre-existing
        dry_run computation already consumes unscaled at analytics.py:557) --
        this assertion would also fail a *100-double-scaled implementation
        (which would render 53.0, not 0.53), catching that specific defect.
        """
        sym = next(s for s in golden_fixture["symphonies"] if s["id"] == "little_trolling_v1_4")
        assert sym["current_return_is_reconstructed"] is True  # sanity on the fixture itself

        db_file = _make_shadow_db(
            tmp_path,
            [(sym["id"], sym["shadow_return"], sym["shadow_history_current_return"])],
        )
        sym_dict = {
            "id": sym["id"],
            "last_percent_change": sym["bot_state_current_return"] / 100.0,
            "trading_day": _TRADING_DAY,
            "current_return_is_reconstructed": True,
        }

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert result["if_held"] == pytest.approx(sym["shadow_history_current_return"], abs=1e-6), (
            f"AC-1 FAIL: marker True + shadow row present must prefer "
            f"shadow_history.current_return ({sym['shadow_history_current_return']}) over the "
            f"reconstructed bot_state-derived value ({sym['bot_state_current_return']}); "
            f"got if_held={result['if_held']}"
        )
        assert result["if_held"] != pytest.approx(sym["bot_state_current_return"], abs=1e-6), (
            "AC-1 FAIL: if_held must NOT equal the reconstructed bot_state-derived value "
            "once the marker-gated fix is in place"
        )

    def test_untriggered_symphony_marker_false_if_held_unchanged(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-2 (unit pin). marker explicitly False -> if_held is byte-identical
        to the pre-fix formula (last_percent_change*100), regardless of what
        the shadow row's current_return column says.
        """
        db_file = _make_shadow_db(
            tmp_path,
            [
                (normal_symphony_dict["id"], -3.33, 99.0)
            ],  # 99.0 sentinel: must NOT leak into if_held
        )
        sym_dict = dict(normal_symphony_dict, current_return_is_reconstructed=False)

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = sym_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-2 FAIL: marker False must leave if_held == last_percent_change*100 "
            f"({expected}) unconditionally; got {result['if_held']} "
            f"(sentinel current_return=99.0 must never leak in)"
        )

    def test_symphony_dict_missing_marker_key_defaults_to_no_override(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-2 (unit pin, the real-world shape). The two untouched real
        callers (app.py:1274 dashboard() SSR route, app.py:2169 frozen
        snapshot branch) never add the marker key to their sym_dicts at all
        -- proven safe-by-construction only if a wholly-absent key behaves
        identically to an explicit False, not merely "falsy who cares".
        """
        db_file = _make_shadow_db(
            tmp_path,
            [(normal_symphony_dict["id"], -3.33, 99.0)],
        )
        sym_dict = dict(normal_symphony_dict)  # no current_return_is_reconstructed key at all
        assert "current_return_is_reconstructed" not in sym_dict

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = sym_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-2 FAIL: a sym_dict with the marker key entirely absent must behave "
            f"identically to marker=False; expected if_held={expected}, got {result['if_held']}"
        )

    def test_marker_true_but_no_shadow_row_falls_back_to_bot_state_value(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-4 (fallback honesty). Marker True but no same-trading-day shadow
        row exists (engine died before first record_shadow_observation) ->
        fall back to the existing bot_state-derived value. Never None-crash,
        never fabricate, never silently read a cross-day row.
        """
        db_file = _make_empty_shadow_db(tmp_path)
        sym_dict = dict(normal_symphony_dict, current_return_is_reconstructed=True)

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = sym_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-4 FAIL: marker True + no shadow row must fall back to the bot_state-derived "
            f"value ({expected}), never None/crash/fabricated; got if_held={result['if_held']}"
        )
        assert result["dry_run"] is None, (
            "AC-4 FAIL: dry_run must remain the honest None sentinel when no shadow row exists "
            "-- unaffected by the if_held fallback"
        )

    def test_marker_true_shadow_row_present_but_current_return_column_null_falls_back_to_bot_state_value(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-4 extension (PM-requested, 2026-08-13): marker True + a same-day
        shadow_history row EXISTS but its current_return column is NULL (a
        legacy/tampered row -- production's schema is NOT NULL on this
        column from inception, but a pre-migration or manually-edited row
        could still carry a NULL). if_held must fall back to the bot_state-
        derived value, never crash with a `float(None)` TypeError. Distinct
        from the prior test (no row at all vs. a row with a null column) --
        both are real-world degradation modes the implementation must guard.
        """
        db_file = _make_shadow_db_with_null_current_return(
            tmp_path, normal_symphony_dict["id"], shadow_return=-3.33
        )
        sym_dict = dict(normal_symphony_dict, current_return_is_reconstructed=True)

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = sym_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-4-EXT FAIL: marker True + a shadow row whose current_return column is NULL "
            f"must fall back to the bot_state-derived if_held ({expected}), never crash on "
            f"float(None) and never fabricate a value; got if_held={result['if_held']}"
        )
        assert result["dry_run"] == pytest.approx(-3.33, abs=1e-6), (
            f"AC-4-EXT FAIL: dry_run must be unaffected -- it still reads the row's "
            f"(non-null) shadow_return column normally; got dry_run={result['dry_run']}"
        )

    def test_dry_run_untouched_by_marker_still_reads_shadow_return_not_current_return(
        self, golden_fixture, tmp_path
    ):
        """Regression guard: the fix touches ONLY if_held. dry_run must keep
        reading shadow_history.shadow_return (the frozen exit value), never
        accidentally swapped to shadow_history.current_return.
        """
        sym = next(s for s in golden_fixture["symphonies"] if s["id"] == "little_trolling_v1_4")
        db_file = _make_shadow_db(
            tmp_path,
            [(sym["id"], sym["shadow_return"], sym["shadow_history_current_return"])],
        )
        sym_dict = {
            "id": sym["id"],
            "last_percent_change": sym["bot_state_current_return"] / 100.0,
            "trading_day": _TRADING_DAY,
            "current_return_is_reconstructed": True,
        }

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert result["dry_run"] == pytest.approx(sym["shadow_return"], abs=1e-6), (
            f"REGRESSION GUARD FAIL: dry_run must still be shadow_history.shadow_return "
            f"({sym['shadow_return']}), not current_return ({sym['shadow_history_current_return']}); "
            f"got dry_run={result['dry_run']}"
        )


@pytest.fixture()
def normal_symphony_dict() -> dict:
    """A plain untriggered symphony sym_dict shape -- distinct id/value from the
    golden fixture's 11 symphonies so tests in this class never collide on id.
    """
    return {
        "id": "normal-sym-held-basis-test",
        "last_percent_change": -0.0229,  # -2.29%
        "trading_day": _TRADING_DAY,
    }


# ---------------------------------------------------------------------------
# AC-3 -- portfolio aggregate golden-fixture convergence
# ---------------------------------------------------------------------------


class TestPortfolioAggregateGoldenFixture:
    """Precision-loss note (documented on the fixture's own _provenance field
    too): re-summing the audit's own 2dp-display weights (13892.84) does not
    exactly reproduce the audit's own quoted symphony_value_sum (13892.28) --
    the audit script evidently used higher-precision internal current_value
    figures than its 2dp markdown table shows. Consequently this file derives
    its PRIMARY expected values directly from the fixture's own numbers via
    _weighted_average() (never a hardcoded literal the fixture can't itself
    reproduce), and separately sanity-checks against the audit's headline
    figures with a generous abs=4e-3 tolerance (the observed drift is a
    ~0.003403pp constant offset, confirmed via manual reproduction of the
    audit's own formula against its own display table -- 4e-3 gives ~15%
    headroom above that measured drift) -- tight enough to catch a real
    regression, loose enough to not spuriously fail on this known,
    explained, bounded reproducibility gap.
    """

    def test_marker_threaded_portfolio_vw_held_converges_to_shadow_basis(
        self, golden_fixture, tmp_path
    ):
        """AC-3 (post-fix, RED today). All 11 symphonies, ONLY the triggered
        one carries the marker. Portfolio VW if_held must converge to the
        weighted average of shadow_history.current_return (the hero-chart /
        Performance-tab basis) -- matching FINDING-1's fix scaled to the
        whole portfolio, and matching the audit's own headline convergence
        claim (+0.209465pp, sanity-checked below).
        """
        rows = golden_fixture["symphonies"]
        shadow_rows = [
            (s["id"], s["shadow_return"], s["shadow_history_current_return"]) for s in rows
        ]
        db_file = _make_shadow_db(tmp_path, shadow_rows, suffix="_ac3_marked")

        symphonies_list = [
            {
                "id": s["id"],
                "value": s["value"],
                "last_percent_change": s["bot_state_current_return"] / 100.0,
                "trading_day": _TRADING_DAY,
                "current_return_is_reconstructed": s["current_return_is_reconstructed"],
            }
            for s in rows
        ]

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected_if_held = _weighted_average(rows, "value", "shadow_history_current_return")
        expected_dry_run = _weighted_average(rows, "value", "shadow_return")

        assert vw_tc["if_held"] == pytest.approx(expected_if_held, abs=1e-6), (
            f"AC-3 FAIL: marker-threaded portfolio VW if_held must equal the weighted average "
            f"of shadow_history.current_return ({expected_if_held}); got {vw_tc['if_held']}"
        )
        assert vw_tc["dry_run"] == pytest.approx(expected_dry_run, abs=1e-6), (
            f"dry_run must be unaffected by the if_held fix; expected {expected_dry_run}, "
            f"got {vw_tc['dry_run']}"
        )

        # Sanity anchor against the audit's own headline figure (see class docstring
        # for the documented ~0.0034pp precision-loss rationale for this looser bound).
        audit = golden_fixture["audit_reported_aggregates"]
        assert vw_tc["if_held"] == pytest.approx(
            audit["vw_held_shadow_current_return_post_fix_pct"], abs=4e-3
        ), (
            f"AC-3 sanity FAIL: converged VW if_held ({vw_tc['if_held']}) drifted further than "
            f"expected from the audit's reported +0.209465pp headline"
        )

    def test_unmarked_portfolio_vw_held_reproduces_pre_fix_bot_state_basis(
        self, golden_fixture, tmp_path
    ):
        """Regression trap (permanent, not RED-by-definition): the SAME 11
        symphonies with NO marker threaded anywhere must still reproduce the
        documented PRE-fix (buggy/overstated) basis -- proves the old
        bot_state-sourced codepath stays reachable for any caller that never
        threads the marker (the two untouched real callers, app.py:1274 and
        app.py:2169), and gives a concrete trap against silently regressing
        back to always-override behavior.
        """
        rows = golden_fixture["symphonies"]
        shadow_rows = [
            (s["id"], s["shadow_return"], s["shadow_history_current_return"]) for s in rows
        ]
        db_file = _make_shadow_db(tmp_path, shadow_rows, suffix="_ac3_unmarked")

        symphonies_list = [
            {
                "id": s["id"],
                "value": s["value"],
                "last_percent_change": s["bot_state_current_return"] / 100.0,
                "trading_day": _TRADING_DAY,
                # current_return_is_reconstructed deliberately omitted for every symphony.
            }
            for s in rows
        ]

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected_if_held = _weighted_average(rows, "value", "bot_state_current_return")
        assert vw_tc["if_held"] == pytest.approx(expected_if_held, abs=1e-6), (
            f"REGRESSION TRAP FAIL: unmarked portfolio VW if_held must reproduce the pre-fix "
            f"bot_state-sourced basis ({expected_if_held}) -- the marker-gated fix must be "
            f"opt-in per symphony, never a blanket always-override"
        )

        audit = golden_fixture["audit_reported_aggregates"]
        assert vw_tc["if_held"] == pytest.approx(
            audit["vw_held_bot_state_basis_pre_fix_pct"], abs=4e-3
        ), (
            f"REGRESSION TRAP sanity FAIL: unmarked VW if_held ({vw_tc['if_held']}) drifted "
            f"further than expected from the audit's reported pre-fix +0.192476pp headline"
        )


# ---------------------------------------------------------------------------
# AC-5 -- guard_delta_vw pairwise membership (unit-level, best-effort; the
# primary/authoritative AC-5 proof is route-level in
# tests/app/test_held_basis_route_convergence.py, called through the real
# /api/state seam so it can't be invalidated by an internal signature change).
# ---------------------------------------------------------------------------


class TestGuardDeltaVWMembershipParity:
    def test_coverage_gap_zero_divergence_yields_exact_zero_account_basis_delta(self, tmp_path):
        """A day with a coverage-gap symphony (no shadow row) where every
        COVERED symphony has zero real divergence (dry_run == if_held) must
        render an exact 0.0 today-row delta -- not a phantom nonzero from
        FINDING-2's mismatched if_held (full-membership) vs dry_run
        (dry_run-only-membership) denominators.
        """
        covered = [
            {
                "id": "gap-a",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
            {
                "id": "gap-b",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        # gap-c has real Composer data (if_held is real) but NO shadow row today.
        gap_symphony = {
            "id": "gap-c",
            "value": 1000.0,
            "last_percent_change": 0.09,  # deliberately far from covered's 0.01 -> 9.0pp if_held
            "trading_day": _TRADING_DAY,
        }
        symphonies_list = [*covered, gap_symphony]

        db_file = _make_shadow_db(
            tmp_path,
            [
                (s["id"], s["last_percent_change"] * 100.0, s["last_percent_change"] * 100.0)
                for s in covered
            ],
            suffix="_ac5_zero",
        )

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )
        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc=0.0, account_value=3000.0, symphony_value_sum=3000.0
        )

        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9), (
            f"AC-5 FAIL: a coverage-gap day with zero divergence on the covered subset must "
            f"render an EXACT zero delta (account_if_held_tc=0.0 + guard_delta_vw*1.0); "
            f"got dry_run={result['dry_run']} — the gap symphony's if_held (9.0pp) must not "
            f"leak into the delta via a mismatched denominator"
        )

    def test_coverage_gap_with_real_divergence_uses_paired_membership_not_mismatched_denominators(
        self, tmp_path
    ):
        """Same coverage-gap shape, but the covered subset has a real, exactly
        known +1.0pp divergence (dry_run=2.0, if_held=1.0 for both covered
        symphonies). The gap symphony's if_held (5.0pp, a deliberate outlier)
        must NOT dilute the delta -- old mismatched-denominator math would
        produce roughly -0.333pp (wrong SIGN); the fix must produce +1.0pp.
        """
        covered = [
            {
                "id": "gapdiv-a",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
            {
                "id": "gapdiv-b",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        gap_symphony = {
            "id": "gapdiv-c",
            "value": 1000.0,
            "last_percent_change": 0.05,  # 5.0pp if_held outlier
            "trading_day": _TRADING_DAY,
        }
        symphonies_list = [*covered, gap_symphony]

        # covered symphonies: if_held = last_percent_change*100 = 1.0pp; shadow_return = 2.0pp.
        db_file = _make_shadow_db(
            tmp_path,
            [(s["id"], 2.0, 1.0) for s in covered],
            suffix="_ac5_real_div",
        )

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )
        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc=0.0, account_value=3000.0, symphony_value_sum=3000.0
        )

        assert result["dry_run"] == pytest.approx(1.0, abs=1e-6), (
            f"AC-5 FAIL: paired-membership delta over the covered subset must be exactly "
            f"+1.0pp (2.0-1.0); got dry_run={result['dry_run']} -- a mismatched-denominator "
            f"implementation would produce approximately -0.333pp (wrong sign) by diluting "
            f"if_held with the gap symphony's 5.0pp outlier while excluding it from dry_run"
        )


class TestGuardDeltaVWMembershipParityCRPath:
    """AC-5's CR-path coverage-gap tests (PM-requested confirmation,
    2026-08-13 — the feature plan's AC-5 text says "guard_delta_vw (TC and
    CR paths)").

    INVESTIGATION FINDING (reported to PM/held-imp): unlike get_symphony_
    today_change's dry_run (which reads a SPECIFIC same-day shadow_history
    row and is genuinely None when that row is missing — the exact
    mechanism FINDING-2 exploits), get_symphony_cumulative_return's dry_run
    (analytics.py:899-974) is initialised to `if_held` BEFORE the shadow-
    trajectory lookup and is ONLY ever reassigned to a real float
    (if_held + lifetime_divergence) when >=2 distinct shadow trading days
    exist (analytics.py:953/972) -- it is NEVER None when if_held is not
    None. Consequently _value_weighted_portfolio's `per["dry_run"] is not
    None` membership-exclusion check (the exact line FINDING-2 is about)
    can never fire for a CR-path symphony: a coverage-gap symphony (real
    Composer data, insufficient/no shadow trajectory) has dry_run == if_held
    for ITSELF and is counted identically on both sides of the VW average --
    contributing ZERO net divergence, not a phantom nonzero delta, and never
    excluded from either side. The specific FINDING-2 defect class is
    therefore NOT reachable via the CR path under the current code -- these
    two tests EMPIRICALLY PROVE that (not merely assert it by inspection),
    so both pass green today and act as a regression guard against a future
    change to get_symphony_cumulative_return that introduces a None dry_run.
    """

    def test_cr_coverage_gap_zero_real_divergence_yields_exact_zero_delta(self, tmp_path):
        covered = [
            {
                "id": "cr-gap-a",
                "value": 1000.0,
                "simple_return": 0.01,
                "net_deposits": 100.0,
                "time_weighted_return": 0.01,
                "trading_day": _TRADING_DAY,
            },
            {
                "id": "cr-gap-b",
                "value": 1000.0,
                "simple_return": 0.01,
                "net_deposits": 100.0,
                "time_weighted_return": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        # Coverage-gap symphony: real Composer data (if_held is real), but NO
        # shadow_history rows at all -- insufficient trajectory (< 2 days).
        gap_symphony = {
            "id": "cr-gap-c",
            "value": 1000.0,
            "simple_return": 0.09,  # 9.0pp if_held outlier
            "net_deposits": 100.0,
            "time_weighted_return": 0.09,
            "trading_day": _TRADING_DAY,
        }
        symphonies_list = [*covered, gap_symphony]

        db_file = _make_cr_trajectory_shadow_db(
            tmp_path,
            {
                s["id"]: [(0.0, 0.0), (0.0, 0.0)] for s in covered
            },  # flat 2-day trajectory, zero divergence
            suffix="_cr_ac5_zero",
        )

        vw_cr = analytics.get_portfolio_cumulative_return(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert vw_cr["if_held"] is not None and vw_cr["dry_run"] is not None
        delta = float(vw_cr["dry_run"]) - float(vw_cr["if_held"])
        assert delta == pytest.approx(0.0, abs=1e-6), (
            f"AC-5 (CR path) FAIL: a coverage-gap symphony with zero real divergence on the "
            f"covered subset must contribute an exact-zero delta (it is never excluded from "
            f"either side, and its own dry_run==if_held); got delta={delta} (vw_cr={vw_cr})"
        )

    def test_cr_coverage_gap_with_real_divergence_gap_symphony_contributes_zero_net(self, tmp_path):
        """Covered symphonies have a real, exactly-known +1.0pp lifetime
        divergence (day1 flat, day2: shadow=+2.0 vs current=+1.0 ->
        (1.02*1.0 - 1.01*1.0)*100 = 1.0pp on top of if_held=1.0 -> dry_run=2.0).
        The gap symphony's 5.0pp if_held outlier must contribute EQUALLY to
        both sides (never excluded, never diluting only one side) -- the
        portfolio delta must equal the AVERAGE of the covered symphonies'
        own divergence diluted by the gap symphony's zero contribution:
        (1.0 + 1.0 + 0.0) / 3 = 0.6667pp, not some mismatched-denominator
        distortion.
        """
        covered = [
            {
                "id": "cr-gapdiv-a",
                "value": 1000.0,
                "simple_return": 0.01,
                "net_deposits": 100.0,
                "time_weighted_return": 0.01,
                "trading_day": _TRADING_DAY,
            },
            {
                "id": "cr-gapdiv-b",
                "value": 1000.0,
                "simple_return": 0.01,
                "net_deposits": 100.0,
                "time_weighted_return": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        gap_symphony = {
            "id": "cr-gapdiv-c",
            "value": 1000.0,
            "simple_return": 0.05,  # 5.0pp if_held outlier
            "net_deposits": 100.0,
            "time_weighted_return": 0.05,
            "trading_day": _TRADING_DAY,
        }
        symphonies_list = [*covered, gap_symphony]

        db_file = _make_cr_trajectory_shadow_db(
            tmp_path,
            {s["id"]: [(0.0, 0.0), (2.0, 1.0)] for s in covered},
            suffix="_cr_ac5_real_div",
        )

        vw_cr = analytics.get_portfolio_cumulative_return(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert vw_cr["if_held"] is not None and vw_cr["dry_run"] is not None
        delta = float(vw_cr["dry_run"]) - float(vw_cr["if_held"])
        assert delta == pytest.approx(2.0 / 3.0, abs=1e-6), (
            f"AC-5 (CR path) FAIL: expected the gap symphony to contribute exactly zero net "
            f"divergence (dry_run==if_held for itself, counted on both sides), yielding a "
            f"portfolio delta of 2/3 (the covered symphonies' average +1.0pp divergence "
            f"diluted by the gap symphony's 0.0pp); got delta={delta} (vw_cr={vw_cr})"
        )


# ---------------------------------------------------------------------------
# AC-6 -- Tier-2 floor if_held retains full membership
# ---------------------------------------------------------------------------


class TestFloorPathFullMembership:
    def test_value_weighted_if_held_average_retains_full_membership_despite_coverage_gap(
        self, tmp_path
    ):
        """The standalone VW if_held average (get_portfolio_today_change's
        OWN "if_held" key, pre-account-basis) is what DE-EOD-BASIS-001's
        Tier-2 "basis=value_weighted" floor renders directly when no account-
        level Composer figure is available. A live symphony missing a shadow
        row must still appear in this figure -- fixing the AC-5 delta must
        not silently shrink the floor's membership.
        """
        covered = [
            {
                "id": "floor-a",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        gap_symphony = {
            "id": "floor-gap",
            "value": 1000.0,
            "last_percent_change": 0.05,
            "trading_day": _TRADING_DAY,
        }
        symphonies_list = [*covered, gap_symphony]

        db_file = _make_shadow_db(
            tmp_path,
            [(s["id"], 1.0, 1.0) for s in covered],
            suffix="_ac6_floor",
        )

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected_full_membership_if_held = (1000.0 * 1.0 + 1000.0 * 5.0) / 2000.0  # = 3.0
        assert vw_tc["if_held"] == pytest.approx(expected_full_membership_if_held, abs=1e-9), (
            f"AC-6 FAIL: the floor-path if_held average must include the coverage-gap "
            f"symphony (full membership, expected {expected_full_membership_if_held}); "
            f"got {vw_tc['if_held']} -- if this equals 1.0 exactly, the gap symphony was "
            f"wrongly excluded (AC-5's pairwise fix leaked into the full-membership floor)"
        )


# ---------------------------------------------------------------------------
# AC-8 -- all-degraded contract unchanged
# ---------------------------------------------------------------------------


class TestAllDegradedContract:
    def test_all_symphonies_missing_shadow_rows_degrades_honestly(self, tmp_path):
        """When every symphony lacks dry_run (no shadow rows for any of them),
        the existing degradation contract (dry_run=None at the aggregate
        level, if_held still computed from full membership) must be
        unchanged by the AC-5 pairwise-membership fix.
        """
        symphonies_list = [
            {
                "id": "deg-a",
                "value": 1000.0,
                "last_percent_change": 0.02,
                "trading_day": _TRADING_DAY,
            },
            {
                "id": "deg-b",
                "value": 1000.0,
                "last_percent_change": -0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        db_file = _make_empty_shadow_db(tmp_path, suffix="_ac8")

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert vw_tc["dry_run"] is None, (
            f"AC-8 FAIL: with zero dry_run coverage across the whole portfolio, the aggregate "
            f"dry_run must be the honest None sentinel; got {vw_tc['dry_run']}"
        )
        expected_if_held = (1000.0 * 0.02 * 100.0 + 1000.0 * -0.01 * 100.0) / 2000.0
        assert vw_tc["if_held"] == pytest.approx(expected_if_held, abs=1e-9), (
            f"AC-8 FAIL: if_held must still be computed from full membership even when "
            f"dry_run is entirely absent; expected {expected_if_held}, got {vw_tc['if_held']}"
        )

        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc=0.0, account_value=2000.0, symphony_value_sum=2000.0
        )
        assert result == {"if_held": 0.0, "dry_run": None}, (
            f"AC-8 FAIL: account-basis result with vw_tc['dry_run'] is None must degrade to "
            f"{{'if_held': account_if_held_tc, 'dry_run': None}} per the documented contract; "
            f"got {result}"
        )


def test_golden_fixture_weighted_average_helper_is_self_consistent(golden_fixture):
    """Sanity-checks this file's own _weighted_average() test helper against
    a hand-computed value for one column, guarding against a silent bug in
    the helper itself producing false-passing tests elsewhere in this file.
    """
    rows = golden_fixture["symphonies"]
    computed = _weighted_average(rows, "value", "shadow_return")
    total_weight = sum(r["value"] for r in rows)
    manual_numerator = sum(r["value"] * r["shadow_return"] for r in rows)
    assert computed == pytest.approx(manual_numerator / total_weight, abs=1e-12)
    assert math.isfinite(computed)
