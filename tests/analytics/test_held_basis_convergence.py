"""
RED tests — DE-HELD-BASIS-001: Held Basis Convergence (Today-Row If-Held +
VW Denominator Parity). REVISE CYCLE (PR #125 review findings F1-F9/F13/F14,
2026-08-13) -- see .claude/pr125-review-findings.json for the full adjudicated
list and .claude/tdd-handoff.md for the per-finding test-name mapping.

Source: feature-plans/held-basis-convergence.md + docs/audit/HELD-VS-BOT-
DIVERGENCE-2026-08-13.md.

FINDING-1 (AC-1/AC-2/AC-4): analytics.get_symphony_today_change's if_held
currently ALWAYS reads sym_dict["last_percent_change"] (which app.py sources
from bot_state.current_return). For a triggered symphony, bot_state.current_
return is the TRUE SHADOW RETURN OVERRIDE's VWAP-based basket reconstruction
(alpha_bot_execution.py:1264-1274/:1660), NOT the raw Composer if-held
trajectory that shadow_history.current_return already carries (the same row
already fetched for the Bot/dry_run side).

DESIGN PIVOT (F4, PM ruling 2026-08-13, supersedes the original Option-B
sym_dict-only design): the marker-gated override reads current_return_is_
reconstructed from bot_state_entry FIRST (the parameter EVERY real call site
already passes and which already carries BL-9's marker, since it IS the real
bot_state sub-dict in every production caller except the frozen branch's
hand-built _snap_bot_state) -- specifically: if bot_state_entry is a dict AND
the key is PRESENT in it (even if False), that value is authoritative; only
when bot_state_entry is None or lacks the key does the seam fall back to
sym_dict.get("current_return_is_reconstructed"). This fixes F1 (/api/strip),
F3 (dashboard SSR card), and the frozen per-symphony loop by construction --
zero app.py diff needed at those 3 sites, since they already pass the real
bot_state entry through. Only the frozen branch's hand-built 4-key
_snap_bot_state (app.py:2277) needs the key added explicitly. Unit-scale
note (unchanged from the original design): row["current_return"] is ALREADY
percent-scale in the DB -- NO further *100 scaling, unlike last_percent_change
which is Composer-decimal and DOES need *100.

FINDING-2 (F2, PM ruling): the paired-membership guard_delta_vw was
originally computed as the PAIRED MEAN divergence, then multiplied by
FULL-membership invested_frac -- extrapolating the covered subset's average
divergence onto uncovered capital and overstating guard alpha by
total_weight/paired_weight on coverage-gap divergence days. The honest
contract: the guard_delta_vw a caller applies against invested_frac must
already be coverage-scaled, i.e. equivalent to (paired divergence SUM) /
(TOTAL weight, not paired weight) -- reviewer's worked example: 2-of-3
coverage, +1.0pp paired mean -> honest +0.667pp, not +1.0pp.

FINDING-6 (F6): get_portfolio_today_change must NOT hardcode
include_paired_guard_delta=True -- the internal "guard_delta_vw" key must
never leak into a public JSON response or the engine's persisted EOD
snapshot by default. Default False; callers that need the paired delta
(app.py's live TC-account-basis site, the frozen Tier-2 fallback per F5)
opt in explicitly.

Golden fixture: tests/fixtures/math/held_basis_convergence_live_capture.json
(the audit's §3 live capture, verbatim 11-symphony 2dp display table -- never
invented numbers). This file NEVER hardcodes a literal portfolio-aggregate
value that the fixture itself cannot reproduce via an obviously-correct
weighted-average -- see _weighted_average() below.

Route-level / end-to-end coverage (all 5 real caller shapes post-F1, AC-2
parity sweep, AC-3 end-to-end, AC-5 primary coverage-gap proof, F1/F3/F5/F6
route-level proofs) lives in tests/app/test_held_basis_route_convergence.py
-- per the plan's explicit "whole-module mocking of analytics is a known
false-green trap" warning, this file never mocks analytics itself; it calls
the real functions.
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


def _create_shadow_schema(
    conn: sqlite3.Connection, *, current_return_nullable: bool = False
) -> None:
    """F13 (PR #125 review): single source of truth for the test-only
    shadow_history DDL used throughout this file -- was pasted 3x with the
    3rd copy silently dropping NOT NULL on current_return (an intentional
    relaxation indistinguishable from a typo without this name). Production's
    real schema (migrations/008_shadow_history.sql) is NOT NULL on this
    column from inception; current_return_nullable=True exists ONLY to
    construct the NULL-column degradation test below.
    """
    current_return_decl = (
        "current_return REAL" if current_return_nullable else "current_return REAL NOT NULL"
    )
    conn.execute(f"""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            {current_return_decl},
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")


def _make_shadow_db(tmp_path: Path, rows: list[tuple[str, float, float]], suffix: str = "") -> str:
    """rows: list of (symphony_id, shadow_return, current_return) for _TRADING_DAY."""
    db_file = str(tmp_path / f"shadow_held_basis{suffix}.db")
    conn = sqlite3.connect(db_file)
    _create_shadow_schema(conn)
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
    _create_shadow_schema(conn)
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
    "no row at all" (_make_empty_shadow_db). The point is proving the READ
    path degrades safely, not reproducing how a real NULL could get there.
    """
    db_file = str(tmp_path / f"shadow_held_basis_null_cr{suffix}.db")
    conn = sqlite3.connect(db_file)
    _create_shadow_schema(conn, current_return_nullable=True)
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
# AC-1 / AC-2 (unit) / AC-4 -- per-symphony marker-gated if_held.
# F4 pivot: bot_state_entry is the PRIMARY marker source; sym_dict is a
# fallback used ONLY when bot_state_entry is None or lacks the key entirely.
# ---------------------------------------------------------------------------


class TestPerSymphonyMarkerGatedIfHeld:
    def test_reconstructed_triggered_symphony_if_held_uses_shadow_current_return_not_bot_state(
        self, golden_fixture, tmp_path
    ):
        """AC-1 (FINDING-1 core, F4 PRIMARY path). Golden single-symphony case
        from the audit: weight 1757.54, reconstructed bot_state-derived
        +0.39571, shadow current_return +0.53000 (raw if-held), frozen
        shadow_return +0.67000. Marker True on bot_state_entry (sym_dict
        deliberately carries NO marker key at all, proving bot_state_entry
        -- not sym_dict -- is read) + same-day shadow row present -> if_held
        must render +0.53000, NOT the reconstructed +0.39571.

        Unit-scale guard: row["current_return"] is ALREADY percent-scale in
        the DB (same convention as shadow_return, which the pre-existing
        dry_run computation already consumes unscaled) -- this assertion
        would also fail a *100-double-scaled implementation (which would
        render 53.0, not 0.53), catching that specific defect.
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
            # Deliberately NO current_return_is_reconstructed key here -- F4
            # requires the marker be read from bot_state_entry, not sym_dict,
            # when bot_state_entry supplies it.
        }
        bot_state_entry = {
            "current_return": sym["bot_state_current_return"],
            "current_return_is_reconstructed": True,
        }

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert result["if_held"] == pytest.approx(sym["shadow_history_current_return"], abs=1e-6), (
            f"AC-1/F4 FAIL: marker True on bot_state_entry + shadow row present must prefer "
            f"shadow_history.current_return ({sym['shadow_history_current_return']}) over the "
            f"reconstructed bot_state-derived value ({sym['bot_state_current_return']}); "
            f"got if_held={result['if_held']} -- if this equals the reconstructed value, the "
            f"seam is still reading sym_dict only and F4's pivot did not land"
        )
        assert result["if_held"] != pytest.approx(sym["bot_state_current_return"], abs=1e-6), (
            "AC-1 FAIL: if_held must NOT equal the reconstructed bot_state-derived value "
            "once the marker-gated fix is in place"
        )

    def test_marker_true_on_sym_dict_fallback_when_bot_state_entry_lacks_key(
        self, golden_fixture, tmp_path
    ):
        """F4 fallback contract, exactly the app.py:2277 _snap_bot_state
        scenario (frozen branch's hand-built 4-key dict: current_value,
        current_return, name, account -- NO current_return_is_reconstructed).
        bot_state_entry is a real dict but lacks the key entirely -> the seam
        must fall back to sym_dict's marker, not silently treat the missing
        key as an authoritative False.
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
        # The _snap_bot_state shape: a real dict, but no marker key at all.
        bot_state_entry = {
            "current_value": sym["value"],
            "current_return": sym["bot_state_current_return"],
            "name": sym["name"],
            "account": "ACC1",
        }
        assert "current_return_is_reconstructed" not in bot_state_entry

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert result["if_held"] == pytest.approx(sym["shadow_history_current_return"], abs=1e-6), (
            f"F4 FALLBACK FAIL: bot_state_entry lacking the marker key entirely must fall back "
            f"to sym_dict's marker (True here); expected shadow basis "
            f"{sym['shadow_history_current_return']}, got {result['if_held']}"
        )

    def test_marker_true_on_sym_dict_fallback_when_bot_state_entry_is_none(
        self, golden_fixture, tmp_path
    ):
        """F4 fallback contract: bot_state_entry=None (a caller with no real
        bot_state entry available at all) -> falls back to sym_dict's marker.
        Backward-compat with any caller shape that never had a bot_state_entry.
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

        assert result["if_held"] == pytest.approx(sym["shadow_history_current_return"], abs=1e-6), (
            f"F4 FALLBACK FAIL: bot_state_entry=None must fall back to sym_dict's marker "
            f"(True here); expected shadow basis {sym['shadow_history_current_return']}, "
            f"got {result['if_held']}"
        )

    def test_bot_state_entry_marker_false_wins_over_sym_dict_marker_true(
        self, golden_fixture, tmp_path
    ):
        """F4 precedence contract: when bot_state_entry EXPLICITLY carries the
        key (even False), it is authoritative -- no fallback to sym_dict even
        if sym_dict says True. Guards against an implementation that only
        checks truthiness (which would conflate "explicitly False" with
        "key absent") instead of checking key presence.
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
            "current_return_is_reconstructed": True,  # would fire if wrongly consulted
        }
        bot_state_entry = {
            "current_return": sym["bot_state_current_return"],
            "current_return_is_reconstructed": False,  # explicit, authoritative
        }

        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = sym_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"F4 PRECEDENCE FAIL: bot_state_entry's explicit False must win over sym_dict's "
            f"True (no fallback when the key is PRESENT, even if falsy); expected the "
            f"unchanged bot_state-derived value {expected}, got {result['if_held']}"
        )

    def test_untriggered_symphony_marker_false_if_held_unchanged(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-2 (unit pin). marker explicitly False on bot_state_entry (the
        real-world shape every untriggered production symphony has) ->
        if_held is byte-identical to the pre-fix formula (last_percent_
        change*100), regardless of what the shadow row's current_return
        column says.
        """
        db_file = _make_shadow_db(
            tmp_path,
            [
                (normal_symphony_dict["id"], -3.33, 99.0)
            ],  # 99.0 sentinel: must NOT leak into if_held
        )
        bot_state_entry = {"current_return_is_reconstructed": False}

        result = analytics.get_symphony_today_change(
            normal_symphony_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = normal_symphony_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-2 FAIL: marker False must leave if_held == last_percent_change*100 "
            f"({expected}) unconditionally; got {result['if_held']} "
            f"(sentinel current_return=99.0 must never leak in)"
        )

    def test_missing_marker_key_everywhere_defaults_to_no_override(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-2 (unit pin, the real-world degraded shape). Neither sym_dict
        NOR bot_state_entry carries the marker key at all -- proven safe-by-
        construction only if a wholly-absent key (on both sides) behaves
        identically to an explicit False, not merely "falsy who cares".
        """
        db_file = _make_shadow_db(
            tmp_path,
            [(normal_symphony_dict["id"], -3.33, 99.0)],
        )
        assert "current_return_is_reconstructed" not in normal_symphony_dict
        bot_state_entry = {"current_return": 2.0}  # real dict, no marker key
        assert "current_return_is_reconstructed" not in bot_state_entry

        result = analytics.get_symphony_today_change(
            normal_symphony_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = normal_symphony_dict["last_percent_change"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-9), (
            f"AC-2 FAIL: marker key entirely absent on both sides must behave identically to "
            f"marker=False; expected if_held={expected}, got {result['if_held']}"
        )

    def test_marker_true_but_no_shadow_row_falls_back_to_bot_state_value(
        self, normal_symphony_dict, tmp_path
    ):
        """AC-4 (fallback honesty). Marker True (on bot_state_entry) but no
        same-trading-day shadow row exists (engine died before first
        record_shadow_observation) -> fall back to the existing bot_state-
        derived value. Never None-crash, never fabricate, never silently
        read a cross-day row.
        """
        db_file = _make_empty_shadow_db(tmp_path)
        bot_state_entry = {"current_return_is_reconstructed": True}

        result = analytics.get_symphony_today_change(
            normal_symphony_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = normal_symphony_dict["last_percent_change"] * 100.0
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
        """AC-4 extension: marker True (bot_state_entry) + a same-day shadow_
        history row EXISTS but its current_return column is NULL (a legacy/
        tampered row). if_held must fall back to the bot_state-derived value,
        never crash with a `float(None)` TypeError. Distinct from the prior
        test (no row at all vs. a row with a null column) -- both are
        real-world degradation modes the implementation must guard.
        """
        db_file = _make_shadow_db_with_null_current_return(
            tmp_path, normal_symphony_dict["id"], shadow_return=-3.33
        )
        bot_state_entry = {"current_return_is_reconstructed": True}

        result = analytics.get_symphony_today_change(
            normal_symphony_dict, bot_state_entry, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected = normal_symphony_dict["last_percent_change"] * 100.0
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
        bot_state_entry = {"current_return_is_reconstructed": True}

        result = analytics.get_symphony_today_change(
            {
                "id": sym["id"],
                "last_percent_change": sym["bot_state_current_return"] / 100.0,
                "trading_day": _TRADING_DAY,
            },
            bot_state_entry,
            trading_day=_TRADING_DAY,
            db_path=db_file,
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
    Deliberately carries NO marker key (F4: the marker lives on bot_state_entry
    now, sym_dict is fallback-only).
    """
    return {
        "id": "normal-sym-held-basis-test",
        "last_percent_change": -0.0229,  # -2.29%
        "trading_day": _TRADING_DAY,
    }


# ---------------------------------------------------------------------------
# AC-3 -- portfolio aggregate golden-fixture convergence.
# F4 pivot: the marker is threaded via a REAL bot_state dict keyed by
# symphony id (mirroring every real production caller except the frozen
# branch's _snap_bot_state), not via the sym_dict entries.
# ---------------------------------------------------------------------------


class TestPortfolioAggregateGoldenFixture:
    """Precision-loss note: re-summing the audit's own 2dp-display weights
    (13892.84) does not exactly reproduce the audit's own quoted symphony_
    value_sum (13892.28) -- the audit script evidently used higher-precision
    internal current_value figures than its 2dp markdown table shows.
    Consequently this file derives its PRIMARY expected values directly from
    the fixture's own numbers via _weighted_average() (never a hardcoded
    literal the fixture can't itself reproduce), and separately sanity-checks
    against the audit's headline figures with a generous abs=4e-3 tolerance
    (the observed drift is a ~0.003403pp constant offset, confirmed via
    manual reproduction of the audit's own formula against its own display
    table -- 4e-3 gives ~15% headroom above that measured drift).
    """

    def test_marker_threaded_portfolio_vw_held_converges_to_shadow_basis(
        self, golden_fixture, tmp_path
    ):
        """AC-3 (post-fix, RED today). All 11 symphonies, marker threaded via
        a real bot_state dict keyed by id (NOT sym_dict entries -- proving
        the F4 primary path at the aggregate level). Portfolio VW if_held
        must converge to the weighted average of shadow_history.current_
        return (the hero-chart / Performance-tab basis).
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
                # Deliberately NO marker key on sym_dict -- proving bot_state below is read.
            }
            for s in rows
        ]
        bot_state = {
            s["id"]: {"current_return_is_reconstructed": s["current_return_is_reconstructed"]}
            for s in rows
        }

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, bot_state, trading_day=_TRADING_DAY, db_path=db_file
        )

        expected_if_held = _weighted_average(rows, "value", "shadow_history_current_return")
        expected_dry_run = _weighted_average(rows, "value", "shadow_return")

        assert vw_tc["if_held"] == pytest.approx(expected_if_held, abs=1e-6), (
            f"AC-3/F4 FAIL: marker-threaded (via bot_state) portfolio VW if_held must equal "
            f"the weighted average of shadow_history.current_return ({expected_if_held}); "
            f"got {vw_tc['if_held']}"
        )
        assert vw_tc["dry_run"] == pytest.approx(expected_dry_run, abs=1e-6), (
            f"dry_run must be unaffected by the if_held fix; expected {expected_dry_run}, "
            f"got {vw_tc['dry_run']}"
        )

        # Sanity anchor against the audit's own headline figure.
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
        symphonies with NO marker threaded anywhere (empty bot_state AND no
        sym_dict marker) must still reproduce the documented PRE-fix
        (buggy/overstated) basis -- proves the marker-gated fix stays
        strictly opt-in, never a blanket always-override.
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
# F6 -- get_portfolio_today_change must default to NOT leaking guard_delta_vw;
# explicit opt-in via include_paired_guard_delta=True is required.
# ---------------------------------------------------------------------------


class TestPairedGuardDeltaOptIn:
    def test_default_call_produces_clean_two_key_dict(self, tmp_path):
        """F6 FAIL scenario: get_portfolio_today_change hardcoded
        include_paired_guard_delta=True internally, so its return dict
        ALWAYS carried a 3rd "guard_delta_vw" key -- leaking an internal VW-
        basis intermediate into every /api/strip response and the engine's
        persisted EOD snapshot. The DEFAULT call (no kwarg) must produce
        exactly {"if_held", "dry_run"} -- 2 keys, nothing else.
        """
        symphonies_list = [
            {
                "id": "optin-a",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        db_file = _make_shadow_db(tmp_path, [("optin-a", 1.0, 1.0)], suffix="_f6_default")

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list, {}, trading_day=_TRADING_DAY, db_path=db_file
        )

        assert set(vw_tc.keys()) == {"if_held", "dry_run"}, (
            f"F6 FAIL: get_portfolio_today_change's default call must return exactly "
            f"{{'if_held', 'dry_run'}} -- got keys {sorted(vw_tc.keys())}. A 'guard_delta_vw' "
            f"key here means include_paired_guard_delta is still hardcoded True."
        )

    def test_explicit_opt_in_produces_three_key_dict_with_guard_delta_vw(self, tmp_path):
        """The paired delta must still be reachable for callers that need it
        (e.g. app.py's live TC-account-basis site, the frozen Tier-2
        fallback per F5) via an explicit opt-in kwarg.
        """
        symphonies_list = [
            {
                "id": "optin-b",
                "value": 1000.0,
                "last_percent_change": 0.01,
                "trading_day": _TRADING_DAY,
            },
        ]
        db_file = _make_shadow_db(tmp_path, [("optin-b", 2.0, 1.0)], suffix="_f6_optin")

        vw_tc = analytics.get_portfolio_today_change(
            symphonies_list,
            {},
            trading_day=_TRADING_DAY,
            db_path=db_file,
            include_paired_guard_delta=True,
        )

        assert "guard_delta_vw" in vw_tc, (
            f"F6 FAIL: get_portfolio_today_change must expose an explicit opt-in kwarg "
            f"(include_paired_guard_delta=True) that adds 'guard_delta_vw' to the returned "
            f"dict; got keys {sorted(vw_tc.keys())}"
        )
        assert vw_tc["guard_delta_vw"] == pytest.approx(1.0, abs=1e-6), (
            f"single fully-covered symphony: guard_delta_vw must equal its own dry_run-if_held "
            f"divergence (2.0-1.0=1.0); got {vw_tc['guard_delta_vw']}"
        )


# ---------------------------------------------------------------------------
# AC-5 -- guard_delta_vw pairwise membership, COVERAGE-SCALED (F2 fix).
# All calls below explicitly opt in via include_paired_guard_delta=True
# (F6: no longer the default).
# ---------------------------------------------------------------------------


class TestGuardDeltaVWMembershipParity:
    def test_coverage_gap_zero_divergence_yields_exact_zero_account_basis_delta(self, tmp_path):
        """A day with a coverage-gap symphony (no shadow row) where every
        COVERED symphony has zero real divergence (dry_run == if_held) must
        render an exact 0.0 today-row delta -- not a phantom nonzero from
        FINDING-2's mismatched if_held (full-membership) vs dry_run
        (dry_run-only-membership) denominators. Zero divergence stays exactly
        zero under any coverage-scaling formula (0 scaled by anything is 0),
        so this test is unaffected by the F2 fix.
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
            symphonies_list,
            {},
            trading_day=_TRADING_DAY,
            db_path=db_file,
            include_paired_guard_delta=True,
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

    def test_coverage_gap_with_real_divergence_uses_coverage_scaled_delta_not_paired_mean(
        self, tmp_path
    ):
        """F2 FIX (was: test_..._uses_paired_membership_not_mismatched_denominators,
        which pinned the BIASED paired-MEAN value as the contract -- corrected
        per PM ruling 2026-08-13). Reviewer's worked example: 2-of-3 coverage,
        covered symphonies each diverge dry_run=2.0 vs if_held=1.0 (paired
        mean divergence = +1.0pp). The gap symphony's if_held (5.0pp outlier)
        must not dilute the PAIRED figures -- but the delta applied against
        the WHOLE portfolio's invested_frac must be coverage-SCALED: honest
        = paired_mean * (paired_weight/total_weight) = 1.0 * (2000/3000) =
        0.6667pp, NOT the raw paired-mean 1.0pp (which extrapolates the
        covered subset's divergence onto the uncovered capital -- a 1.5x
        overstatement here, unbounded as coverage shrinks).
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
            symphonies_list,
            {},
            trading_day=_TRADING_DAY,
            db_path=db_file,
            include_paired_guard_delta=True,
        )
        result = analytics.get_portfolio_today_change_account_basis(
            vw_tc, account_if_held_tc=0.0, account_value=3000.0, symphony_value_sum=3000.0
        )

        # tolerance rationale: this is an exact rational fraction (2/3), abs=1e-6 is
        # tight enough to catch a mismatched-denominator (1.0) or unscaled-paired-mean
        # regression while comfortably absorbing float accumulation noise.
        assert result["dry_run"] == pytest.approx(2.0 / 3.0, abs=1e-6), (
            f"F2 FAIL: coverage-scaled delta over 2-of-3 coverage with +1.0pp paired-mean "
            f"divergence must be exactly +0.6667pp (1.0 * 2000/3000); got "
            f"dry_run={result['dry_run']}. A value of 1.0 means the paired MEAN is being "
            f"applied unscaled (F2's exact bug -- extrapolates covered-subset divergence onto "
            f"uncovered capital); a value near -0.333 means the OLD mismatched-denominator "
            f"formula (pre this PR entirely) is still in effect."
        )


class TestGuardDeltaVWMembershipParityCRPath:
    """AC-5's CR-path coverage-gap tests (PM-requested confirmation,
    2026-08-13 — the feature plan's AC-5 text says "guard_delta_vw (TC and
    CR paths)").

    INVESTIGATION FINDING (reported to PM/held-imp, PM ruling: no CR
    production change): unlike get_symphony_today_change's dry_run (which
    reads a SPECIFIC same-day shadow_history row and is genuinely None when
    that row is missing — the exact mechanism FINDING-2 exploits),
    get_symphony_cumulative_return's dry_run (analytics.py:899-974) is
    initialised to `if_held` BEFORE the shadow-trajectory lookup and is ONLY
    ever reassigned to a real float (if_held + lifetime_divergence) when
    >=2 distinct shadow trading days exist -- it is NEVER None when if_held
    is not None. Consequently _value_weighted_portfolio's `per["dry_run"] is
    not None` membership-exclusion check (the exact line FINDING-2 is about)
    can never fire for a CR-path symphony: a coverage-gap symphony (real
    Composer data, insufficient/no shadow trajectory) has dry_run == if_held
    for ITSELF and is counted identically on both sides of the VW average --
    contributing ZERO net divergence, not a phantom nonzero delta, and never
    excluded from either side. The specific FINDING-2 defect class (and
    therefore F2's coverage-scaling fix) is NOT applicable to the CR path --
    these two tests EMPIRICALLY PROVE that (not merely assert it by
    inspection), so both pass green today and act as a regression guard
    against a future change to get_symphony_cumulative_return that
    introduces a None dry_run.
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
        (1.0 + 1.0 + 0.0) / 3 = 0.6667pp. Note this is NOT the F2 coverage-
        scaling formula (CR has no paired/full membership split to begin
        with, per the class docstring) -- it is the plain, full-membership
        VW average that already existed before this PR, unaffected.
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
        row must still appear in this figure -- fixing the AC-5/F2 delta must
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
            symphonies_list,
            {},
            trading_day=_TRADING_DAY,
            db_path=db_file,
            include_paired_guard_delta=True,
        )

        expected_full_membership_if_held = (1000.0 * 1.0 + 1000.0 * 5.0) / 2000.0  # = 3.0
        assert vw_tc["if_held"] == pytest.approx(expected_full_membership_if_held, abs=1e-9), (
            f"AC-6 FAIL: the floor-path if_held average must include the coverage-gap "
            f"symphony (full membership, expected {expected_full_membership_if_held}); "
            f"got {vw_tc['if_held']} -- if this equals 1.0 exactly, the gap symphony was "
            f"wrongly excluded (AC-5/F2's coverage-scaled delta leaked into the "
            f"full-membership floor)"
        )


# ---------------------------------------------------------------------------
# AC-8 -- all-degraded contract unchanged
# ---------------------------------------------------------------------------


class TestAllDegradedContract:
    def test_all_symphonies_missing_shadow_rows_degrades_honestly(self, tmp_path):
        """When every symphony lacks dry_run (no shadow rows for any of them),
        the existing degradation contract (dry_run=None at the aggregate
        level, if_held still computed from full membership) must be
        unchanged by the AC-5/F2 pairwise-membership fix.
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
            symphonies_list,
            {},
            trading_day=_TRADING_DAY,
            db_path=db_file,
            include_paired_guard_delta=True,
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
    """F14 FIX (was a tautology comparing _weighted_average() against the
    identical expression re-typed inline -- any defect in the helper would
    have been reproduced verbatim on the "manual" side, so the test could
    never fail). Sanity-checks the helper against an INDEPENDENTLY
    hand-computed literal for the first 3 symphonies of the golden fixture
    (weight, shadow_return), computed independently from the audit's own §3
    table values (docs/audit/HELD-VS-BOT-DIVERGENCE-2026-08-13.md) via a
    standalone numerator/denominator calculation (not by calling
    _weighted_average() itself, which is exactly what this test verifies):
        numerator   = 1020.63*-1.27 + 765.23*-1.17 + 853.31*-0.09
                    = -1296.2001 + -895.5191 + -76.7979 = -2268.5171
        denominator = 1020.63 + 765.23 + 853.31 = 2639.17
        literal     = -2268.5171 / 2639.17 = -0.8594812384196545
    """
    rows = golden_fixture["symphonies"]
    first_three = rows[:3]
    assert [r["id"] for r in first_three] == [
        "lqd_eyeg_5ways",
        "paragons_of_lqd",
        "corporate_chaos_5ways",
    ], "fixture symphony order changed -- the hand-computed literal below assumes this order"

    computed = _weighted_average(first_three, "value", "shadow_return")
    hand_computed_literal = -0.8594812384196545  # independently computed, see docstring
    assert computed == pytest.approx(hand_computed_literal, abs=1e-9), (
        f"F14 FIX FAIL: _weighted_average() helper must reproduce the independently "
        f"hand-computed literal ({hand_computed_literal}) for the first 3 fixture symphonies; "
        f"got {computed} -- a defect in the helper itself would show up here, unlike the "
        f"tautological version this test replaces"
    )
    assert math.isfinite(computed)
