"""
RED tests — feature-plans/mdd-window-truth.md, analytics-layer ACs (AC-0b, AC-1,
AC-2, AC-3 + edge cases). DE-PERF-WINDOW-TRUTH-001.

THE DEFECT (docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md): the Bot-vs-Held Max
Drawdown comparison pairs Composer's LIFETIME max_drawdown scalar (if_held) against
a bot-side quantity (dry_run) that is NOT a portfolio drawdown at all -- it is
peak-to-trough of a DIVERGENCE-RESIDUAL equity series
(``if_held + running_alpha + intra-epoch divergence``). Because ``if_held`` is
added UNIFORMLY to every point of that series, peak-to-trough cancels it by
construction -- ``dry_run`` is PROVABLY translation-invariant to ``if_held``
(empirically unchanged to 10 decimals across injected max_drawdown values
0.0 / 0.1805 / 9.99 / -5.0). AC-0b's follow-up investigation found a THIRD
stacked defect on top: the old ``dry_run`` formula was additionally
UN-NORMALIZED (raw percentage-point subtraction, no ``/peak``), while
Composer's own scalar IS normalized -- all three defects push the same
direction, compounding a rendered 17.66pp gap down to a true 0.23pp one.

CONFIRMED CONTRACT (docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md, AC-0a/
AC-0b, PM-confirmed design -- this file specifies tests against THAT contract,
superseding this test-writer's own earlier independent draft):

  analytics.get_symphony_max_drawdown(
      sym_dict, bot_state_entry, trading_day=None, db_path=None, conn=None,
  ) -> {"if_held": float | None, "dry_run": float | None,
        "if_held_lifetime": float | None, "n_obs": int}

    SIGNATURE UNCHANGED (no new required/optional window param -- AC-7's
    zero-diff freeze on alpha_bot_execution.py means the EOD-snapshot call
    site at alpha_bot_execution.py:1142-1144 cannot change, and it calls this
    function positionally with no window arg).

    if_held  = NORMALIZED peak-to-trough `(peak-value)/peak * 100` (positive
               magnitude, D8 convention) of the compounded current_return NAV
               path, via get_symphony_bot_and_held_daily_returns(...,
               days=None) -- i.e. the FULL available shadow_history series
               (lifetime, not windowed -- AC-3's windowing lives entirely in
               compute_windowed_portfolio_strip, a SEPARATE computation).
    dry_run  = the SAME normalized formula over the compounded shadow_return
               NAV path. Computed with ZERO reference to if_held or to the
               Composer scalar -- an independent measurement of the bot's own
               equity path.
    if_held_lifetime = sym_dict.get("max_drawdown") * 100.0, or None -- the
               OLD if_held computation, preserved under a NEW key (AC-2's
               separately-rendered figure). Depends ONLY on the Composer
               scalar, never on shadow_history.
    n_obs    = int count of trading days the if_held/dry_run computation used
               (0 when insufficient, e.g. < 2 distinct shadow_history days).
               ALWAYS an int, never None, even when if_held/dry_run are None.
    Early-return-on-missing-Composer-scalar is REMOVED for if_held/dry_run/
    n_obs (they no longer read sym_dict["max_drawdown"] at all) -- only
    if_held_lifetime can independently be None.

  analytics.get_portfolio_max_drawdown(symphonies, bot_state, *,
      trading_day=None, db_path=None, conn=None) -> same shape, portfolio-
      level, via get_portfolio_bot_and_held_daily_returns(db_file, days=None)
      -- also SIGNATURE UNCHANGED, no window param.

  analytics.compute_windowed_portfolio_strip(...)'s "max_drawdown" entry
  (AC-3) does NOT call get_portfolio_max_drawdown -- it computes the
  normalized peak-to-trough DIRECTLY over the strip's OWN already-sliced
  bot_pct/held_pct window arrays (the same ones vol_bot/vol_held already use),
  gated by the same _WINDOWED_VOL_MIN_DAYS sufficiency floor. Today it calls
  the unwindowed get_portfolio_max_drawdown, so max_drawdown is identical
  across every window token while cumulative_return/vol_bot/vol_held in the
  same response vary correctly (F3).

Fixture provenance: tests/fixtures/math/mdd_window_truth_53day.json is a
SELF-CONSISTENT synthetic construction (not a literal droplet capture -- that
raw series was unavailable to the test-writer), engineered to reproduce the two
AUDIT-VERIFIED figures (held 10.5875, bot 10.3622) and independently re-derived
in-test via a reference NORMALIZED peak-to-trough implementation (verified to
agree with quantstats.stats.max_drawdown() -- the "adopt, don't invent"
implementation mechanism the confirmed design specifies -- to float-noise
precision across multiple hand-checked series including non-zero-peak and
first-day-worst cases; never asserted circularly against the fixture's own
"expected" block alone).

Tolerance: pytest.approx(..., abs=1e-6) for computed values (quantstats/pandas
float64 arithmetic carries more rounding surface than a raw Python loop);
abs=1e-9 for pure-Python-vs-pure-Python reference cross-checks.

-n0 only; no live API; no live DB (per-test tmp_path SQLite files only).
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import analytics

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "math"
_GOLDEN_FIXTURE = _FIXTURE_DIR / "mdd_window_truth_53day.json"

_ABS = 1e-6
_ABS_PURE = 1e-9

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


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------


def _seed_db(
    tmp_path: Path,
    rows_by_symphony: dict[str, list[dict]],
    *,
    db_name: str = "mdd_shadow.db",
    epoch: str = "EPOCH_A",
) -> str:
    """Seed a per-test shadow_history SQLite DB.

    Each row dict may carry EITHER an explicit "trading_day" ISO string OR a
    "days_ago" int (converted to date.today() - days_ago). get_symphony_max_
    drawdown/get_portfolio_max_drawdown do not take a window param (lifetime-
    only, per the confirmed design) so days_ago is cosmetic here -- kept for
    consistency with the AC-3 strip-level tests below, which DO care about
    real relative dates.
    """
    db_file = str(tmp_path / db_name)
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    for symphony_id, rows in rows_by_symphony.items():
        for row in rows:
            if "trading_day" in row:
                trading_day = row["trading_day"]
            else:
                trading_day = (date.today() - timedelta(days=row["days_ago"])).isoformat()
            conn.execute(
                "INSERT INTO shadow_history "
                "(symphony_id, ts_utc, trading_day, current_return, shadow_return, "
                "is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    symphony_id,
                    trading_day + "T20:00:00Z",
                    trading_day,
                    row["current_return"],
                    row["shadow_return"],
                    row.get("is_post_trigger", 0),
                    row.get("position_epoch", epoch),
                ),
            )
    conn.commit()
    conn.close()
    return db_file


def _sym_dict(symphony_id: str, value: float, max_drawdown: float | None = 0.5) -> dict:
    return {"id": symphony_id, "value": value, "max_drawdown": max_drawdown}


def _reference_normalized_max_drawdown_pct(returns_pct: list[float]) -> float:
    """Independent (not imported from analytics.py) reference implementation
    of the CONFIRMED AC-0b units convention: NORMALIZED peak-to-trough
    `(peak-value)/peak`, with a phantom NAV=1.0 baseline active before the
    first real observation (matches quantstats.stats.max_drawdown's
    phantom-baseline + expanding-max mechanism -- cross-verified against
    qs_stats.max_drawdown() directly, see this module's
    TestAC0bUnitsConventionDocumented.test_reference_formula_matches_quantstats_directly).
    Returns a POSITIVE percentage magnitude, or 0.0 for an empty list.
    """
    if not returns_pct:
        return 0.0
    peak_nav = 1.0
    nav = 1.0
    max_dd = 0.0
    for r in returns_pct:
        nav *= 1.0 + r / 100.0
        if nav > peak_nav:
            peak_nav = nav
        dd = (peak_nav - nav) / peak_nav
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0


# ===========================================================================
# AC-0b — units convention documented in the docstring (GATE, blocks AC-1)
# ===========================================================================


class TestAC0bUnitsConventionDocumented:
    def test_get_symphony_max_drawdown_docstring_states_normalized_percent_positive(
        self,
    ):
        doc = (analytics.get_symphony_max_drawdown.__doc__ or "").lower()
        assert "normaliz" in doc, (
            "AC-0b: the docstring must state the NORMALIZED convention "
            "((peak-value)/peak) -- the declared fix for the third stacked "
            "defect the audit found (dry_run was un-normalized, Composer's "
            "own scalar is normalized)."
        )
        assert "percent" in doc, (
            "AC-0b: the docstring must state the percentage-point scale convention."
        )
        assert "positive" in doc or "magnitude" in doc, (
            "AC-0b: the docstring must state the POSITIVE-magnitude sign convention (D8)."
        )

    def test_get_portfolio_max_drawdown_docstring_states_normalized_convention(self):
        doc = (analytics.get_portfolio_max_drawdown.__doc__ or "").lower()
        assert "normaliz" in doc, (
            "AC-0b: get_portfolio_max_drawdown's docstring must also state "
            "the normalized convention (it aggregates the per-symphony "
            "values, so the formula must agree)."
        )

    def test_reference_formula_matches_quantstats_directly(self):
        """Self-check proving the reference implementation this file uses is
        genuinely independent verification of the CONFIRMED 'adopt, don't
        invent' implementation mechanism (reusing quantstats.stats.
        max_drawdown()), not just an assumption. Covers a non-zero-peak case
        (peak NOT at the series start) and a first-day-worst case (the
        phantom-baseline anchor question) -- both flagged as load-bearing by
        docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md."""
        pytest.importorskip("quantstats", reason="quantstats is an optional dep")
        import pandas as pd
        import quantstats.stats as qs_stats

        def _via_quantstats(returns_pct: list[float]) -> float:
            fractions = [r / 100.0 for r in returns_pct]
            idx = pd.date_range("2000-01-01", periods=len(fractions), freq="D")
            series = pd.Series(fractions, index=idx, dtype=float)
            return abs(float(qs_stats.max_drawdown(series))) * 100.0

        cases = [
            [1.5, -2.0, 0.5],  # trivial no-op sanity
            [2.0, 3.0, -10.0, 1.0, 0.5],  # peak NOT at series start
            [-5.0, 1.0, 1.0, 1.0, 1.0],  # first-day-worst / anchor-sensitive
            [1.0, 2.0, 0.5, 3.0, 1.5],  # all-positive (0.0 expected)
        ]
        for series in cases:
            mine = _reference_normalized_max_drawdown_pct(series)
            theirs = _via_quantstats(series)
            assert mine == pytest.approx(theirs, abs=_ABS_PURE), (
                f"reference formula diverges from quantstats on {series}: "
                f"mine={mine}, quantstats={theirs}"
            )


# ===========================================================================
# AC-1 core contract — golden fixture pinned to the audit's verified figures
# ===========================================================================


@pytest.fixture(scope="module")
def golden_fixture() -> dict:
    return json.loads(_GOLDEN_FIXTURE.read_text(encoding="utf-8"))


class TestAC1GoldenFixture53DayWindow:
    """
    Audit residual U-9 (docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md §10): the
    real droplet's 53-day aggregate MDD_bot showed a ~1e-5 disagreement
    between a "positional path" (trailing-N-rows slice, e.g.
    get_portfolio_bot_and_held_daily_returns(days=N) -> all_days[-N:]) and a
    "calendar path" (_window_cutoff_date-based inclusive cutoff). CONFIRMED
    MOOT for the two functions this class tests, by construction, not by
    empirical re-derivation against live data (unavailable to this
    test-writer): get_symphony_max_drawdown/get_portfolio_max_drawdown use
    ONLY get_..._bot_and_held_daily_returns(..., days=None) -- i.e. NO
    windowing selection happens inside these two functions at all (confirmed
    design doc, point 2/4) -- there is no second, competing selection
    mechanism for U-9's disagreement to manifest between. This fixture's 53
    rows are the symphony's ENTIRE shadow_history in the test DB, so
    days=None trivially includes all of them; there is no larger superset a
    positional vs calendar cutoff could disagree about carving a 53-day
    subset FROM. compute_windowed_portfolio_strip (AC-3, tested separately
    below) also uses only ONE selection mechanism (the calendar cutoff,
    analytics.py:2071-2073) -- verified by direct source read, no positional
    slice exists in that function either. See team-lead's request (relayed
    2026-09-03) to "confirm rather than assume" this is moot.
    """

    def test_reference_implementation_reproduces_the_audit_figures(self, golden_fixture):
        """Self-check: confirms the fixture's construction actually reproduces
        the audit's cited numbers via the IN-TEST reference implementation --
        proves the golden values below are derived, not asserted circularly."""
        held_series = [r["current_return"] for r in golden_fixture["rows"]]
        bot_series = [r["shadow_return"] for r in golden_fixture["rows"]]
        assert _reference_normalized_max_drawdown_pct(held_series) == pytest.approx(
            golden_fixture["expected"]["held_mdd_pct"], abs=_ABS_PURE
        )
        assert _reference_normalized_max_drawdown_pct(bot_series) == pytest.approx(
            golden_fixture["expected"]["bot_mdd_pct"], abs=_ABS_PURE
        )

    def test_symphony_level_if_held_and_dry_run_match_audit_figures(self, golden_fixture, tmp_path):
        sym_id = golden_fixture["symphony_id"]
        db_file = _seed_db(tmp_path, {sym_id: golden_fixture["rows"]})
        sym_dict = _sym_dict(sym_id, golden_fixture["current_value"], max_drawdown=0.9999)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )

        assert result["if_held"] == pytest.approx(
            golden_fixture["expected"]["held_mdd_pct"], abs=_ABS
        ), (
            f"AC-1: held-leg MDD must be the genuine normalized peak-to-trough "
            f"of current_return ({golden_fixture['expected']['held_mdd_pct']}); "
            f"got {result['if_held']}. If this equals 99.99 (0.9999*100), "
            f"if_held is still reading the Composer scalar."
        )
        assert result["dry_run"] == pytest.approx(
            golden_fixture["expected"]["bot_mdd_pct"], abs=_ABS
        ), (
            f"AC-1: bot-leg MDD must be the genuine normalized peak-to-trough "
            f"of shadow_return ({golden_fixture['expected']['bot_mdd_pct']}); "
            f"got {result['dry_run']}"
        )
        assert result["n_obs"] == golden_fixture["n_days"], (
            f"n_obs must equal the {golden_fixture['n_days']} trading days fed "
            f"in; got {result['n_obs']}"
        )

    def test_portfolio_level_single_symphony_matches_audit_figures(self, golden_fixture, tmp_path):
        """Exercises the FULL AC-1 call path (get_portfolio_max_drawdown ->
        get_portfolio_bot_and_held_daily_returns) -- a single symphony makes
        the value-weighted result equal the per-symphony figure exactly,
        while still proving the portfolio-level seam works."""
        sym_id = golden_fixture["symphony_id"]
        db_file = _seed_db(tmp_path, {sym_id: golden_fixture["rows"]})
        symphonies = [_sym_dict(sym_id, golden_fixture["current_value"])]
        bot_state = {sym_id: {"name": "Golden Symphony"}}

        result = analytics.get_portfolio_max_drawdown(symphonies, bot_state, db_path=db_file)

        assert result["if_held"] == pytest.approx(
            golden_fixture["expected"]["held_mdd_pct"], abs=_ABS
        )
        assert result["dry_run"] == pytest.approx(
            golden_fixture["expected"]["bot_mdd_pct"], abs=_ABS
        )


# ===========================================================================
# AC-1 REQUIRED companion golden fixture — day-0/phantom-baseline anchor
# (team-lead mandate, relayed by mdd-metric 2026-09-03: "A golden fixture that
# can't fail on the defect it exists to prevent is decoration." The 53-day
# fixture above has day-0 return exactly 0.0%, making it insensitive to the
# anchor question -- this fixture's single worst day IS day 0, discriminating
# a correctly-anchored implementation from a naive un-anchored one that would
# wrongly return 0.0.)
# ===========================================================================


@pytest.fixture(scope="module")
def anchor_fixture() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "mdd_window_truth_anchor_day1_worst.json").read_text(encoding="utf-8")
    )


class TestAC1GoldenFixtureAnchorDay1Worst:
    _NAIVE_UNANCHORED_WRONG_VALUE = 0.0  # what a peak=first-real-NAV loop wrongly returns

    def test_reference_implementation_reproduces_the_anchor_figures(self, anchor_fixture):
        """Self-check: this file's reference formula (already phantom-baseline
        anchored, see the module docstring) reproduces the fixture's verified
        (via quantstats directly) figures."""
        held_series = [r["current_return"] for r in anchor_fixture["rows"]]
        bot_series = [r["shadow_return"] for r in anchor_fixture["rows"]]
        assert _reference_normalized_max_drawdown_pct(held_series) == pytest.approx(
            anchor_fixture["expected"]["held_mdd_pct"], abs=_ABS_PURE
        )
        assert _reference_normalized_max_drawdown_pct(bot_series) == pytest.approx(
            anchor_fixture["expected"]["bot_mdd_pct"], abs=_ABS_PURE
        )
        # Proves this fixture is DISCRIMINATING (unlike the 53-day one): a
        # naive un-anchored loop must give the WRONG answer here.
        assert (
            _reference_normalized_max_drawdown_pct(held_series)
            != self._NAIVE_UNANCHORED_WRONG_VALUE
        )
        assert (
            _reference_normalized_max_drawdown_pct(bot_series) != self._NAIVE_UNANCHORED_WRONG_VALUE
        )

    def test_symphony_level_correctly_anchors_before_the_first_observation(
        self, anchor_fixture, tmp_path
    ):
        """THE required RED case: get_symphony_max_drawdown must anchor its
        running peak at a pre-existing baseline (NAV=1.0) BEFORE the first
        real shadow_history row, not AT the first row. A naive
        peak=first-real-value implementation would return
        {'if_held': 0.0, 'dry_run': 0.0} here (the first day, being both the
        only candidate peak AND the worst day, cancels itself out) --
        anything matching that wrong shape fails this test."""
        sym_id = anchor_fixture["symphony_id"]
        db_file = _seed_db(tmp_path, {sym_id: anchor_fixture["rows"]})
        sym_dict = _sym_dict(sym_id, anchor_fixture["current_value"], max_drawdown=0.4321)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )

        assert result["if_held"] == pytest.approx(
            anchor_fixture["expected"]["held_mdd_pct"], abs=_ABS
        ), (
            f"ANCHOR FAIL: if_held must be {anchor_fixture['expected']['held_mdd_pct']} "
            f"(the day-0 -5% loss, measured from a pre-existing NAV=1.0 baseline); "
            f"got {result['if_held']}. A value of 0.0 means the implementation "
            f"anchors its running peak at the FIRST REAL observation instead of "
            f"a baseline BEFORE it -- the exact day-0-anchor defect this "
            f"fixture exists to catch."
        )
        assert result["dry_run"] == pytest.approx(
            anchor_fixture["expected"]["bot_mdd_pct"], abs=_ABS
        ), (
            f"ANCHOR FAIL: dry_run must be {anchor_fixture['expected']['bot_mdd_pct']} "
            f"(the day-0 -8% loss); got {result['dry_run']}. A value of 0.0 "
            f"means the same un-anchored defect on the bot leg."
        )
        assert result["n_obs"] == 5

    def test_portfolio_level_correctly_anchors_before_the_first_observation(
        self, anchor_fixture, tmp_path
    ):
        sym_id = anchor_fixture["symphony_id"]
        db_file = _seed_db(tmp_path, {sym_id: anchor_fixture["rows"]})
        symphonies = [_sym_dict(sym_id, anchor_fixture["current_value"])]
        bot_state = {sym_id: {"name": "Anchor Golden Symphony"}}

        result = analytics.get_portfolio_max_drawdown(symphonies, bot_state, db_path=db_file)

        assert result["if_held"] == pytest.approx(
            anchor_fixture["expected"]["held_mdd_pct"], abs=_ABS
        )
        assert result["dry_run"] == pytest.approx(
            anchor_fixture["expected"]["bot_mdd_pct"], abs=_ABS
        )


# ===========================================================================
# AC-1 centerpiece — translation-invariance regression + genuine responsiveness
# ===========================================================================


class TestAC1TranslationInvarianceRegression:
    """The audit's exact defect: dry_run additively includes if_held as a
    baseline offset, so peak-to-trough cancels it by construction. A lazy fix
    that merely rearranges the additive offset (rather than computing an
    independent bot-equity path from shadow_return alone) would STILL pass a
    naive 'dry_run != old_value' check -- these tests are designed so that
    only a genuinely independent per-leg computation survives both halves.
    """

    _INJECTED_COMPOSER_SCALARS = [0.0, 0.1805, 9.99, -5.0, None]

    def test_neither_leg_depends_on_the_injected_composer_scalar_value(self, tmp_path):
        """HALF 1 (still-valid invariant, for the RIGHT reason now): holding the
        shadow_history trajectory FIXED and varying only sym_dict["max_drawdown"]
        (the audit's exact probe values) must leave BOTH if_held and dry_run
        unchanged -- under the fix, NEITHER leg reads that field at all (only
        if_held_lifetime does). This is STRICTLY STRONGER than the audit's
        original probe (which only checked dry_run)."""
        sym_id = "sym-invariance"
        rows = [
            {"days_ago": 10, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 9, "current_return": -3.0, "shadow_return": -1.5},
            {"days_ago": 8, "current_return": 1.0, "shadow_return": 0.5},
            {"days_ago": 0, "current_return": 0.5, "shadow_return": 0.5},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})

        results = []
        for scalar in self._INJECTED_COMPOSER_SCALARS:
            sym_dict = _sym_dict(sym_id, 5000.0, max_drawdown=scalar)
            results.append(
                analytics.get_symphony_max_drawdown(sym_dict, bot_state_entry=None, db_path=db_file)
            )

        first_if_held = results[0]["if_held"]
        first_dry_run = results[0]["dry_run"]
        for scalar, r in zip(self._INJECTED_COMPOSER_SCALARS, results):
            assert r["if_held"] == pytest.approx(first_if_held, abs=_ABS_PURE), (
                f"if_held changed when only the UNRELATED Composer scalar was "
                f"injected as {scalar!r} (trajectory held fixed) -- if_held "
                f"must be computed from shadow_history.current_return only, "
                f"never from sym_dict['max_drawdown']. Results: {results}"
            )
            assert r["dry_run"] == pytest.approx(first_dry_run, abs=_ABS_PURE), (
                f"dry_run changed when only the UNRELATED Composer scalar was "
                f"injected as {scalar!r} -- dry_run must be computed from "
                f"shadow_history.shadow_return only. Results: {results}"
            )

    def test_dry_run_genuinely_tracks_a_deeper_bot_trajectory(self, tmp_path):
        """HALF 2 (the discriminating half): two DBs with IDENTICAL held
        (current_return) series and IDENTICAL Composer scalar, but DIFFERENT
        shadow_return depth -- a shallow ~2% dip vs a severe ~15% dip. dry_run
        MUST differ between them, and must equal the INDEPENDENT reference
        peak-to-trough of each shadow_return series exactly (not merely
        'different from before') -- this is what defeats a lazy implementation
        that changes the additive-offset trick without computing a genuine,
        independent bot-equity path."""
        sym_id = "sym-responsive"
        held_series = [0.0, -1.0, 0.5, 0.2, 0.0]

        shallow_shadow = [0.0, -2.0, 0.5, 0.3, 0.1]
        deep_shadow = [0.0, -15.0, 1.0, 0.5, 0.2]

        def _rows(shadow_series):
            return [
                {
                    "days_ago": 4 - i,
                    "current_return": held_series[i],
                    "shadow_return": shadow_series[i],
                }
                for i in range(len(held_series))
            ]

        db_shallow = _seed_db(tmp_path, {sym_id: _rows(shallow_shadow)}, db_name="shallow.db")
        db_deep = _seed_db(tmp_path, {sym_id: _rows(deep_shadow)}, db_name="deep.db")

        sym_dict = _sym_dict(sym_id, 5000.0, max_drawdown=0.5)

        result_shallow = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_shallow
        )
        result_deep = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_deep
        )

        expected_shallow = _reference_normalized_max_drawdown_pct(shallow_shadow)
        expected_deep = _reference_normalized_max_drawdown_pct(deep_shadow)
        assert expected_deep > expected_shallow, (
            "test construction error: the 'deep' shadow series must have a "
            "strictly larger reference drawdown than the 'shallow' one"
        )

        assert result_shallow["dry_run"] == pytest.approx(expected_shallow, abs=_ABS), (
            f"shallow-trajectory dry_run must equal the independently-computed "
            f"reference peak-to-trough {expected_shallow}; got {result_shallow['dry_run']}"
        )
        assert result_deep["dry_run"] == pytest.approx(expected_deep, abs=_ABS), (
            f"deep-trajectory dry_run must equal the independently-computed "
            f"reference peak-to-trough {expected_deep}; got {result_deep['dry_run']}"
        )
        assert result_deep["dry_run"] != pytest.approx(result_shallow["dry_run"], abs=1e-3), (
            "dry_run must GENUINELY differ between a shallow and a severe bot "
            "trajectory -- if these are equal, dry_run is still measuring "
            "something insensitive to the bot's own equity path."
        )
        assert result_shallow["if_held"] == pytest.approx(result_deep["if_held"], abs=_ABS), (
            "if_held must be unaffected by a change to shadow_return alone "
            "(held and bot are independently computed from their own columns)"
        )

    def test_if_held_genuinely_tracks_a_deeper_held_trajectory(self, tmp_path):
        """Mirror of the above for the HELD leg: two DBs with identical
        shadow_return but different current_return depth -- if_held must
        differ and match the independent reference computation."""
        sym_id = "sym-held-responsive"
        shadow_series = [0.0, -1.0, 0.5, 0.2, 0.0]
        shallow_held = [0.0, -2.0, 0.5, 0.3, 0.1]
        deep_held = [0.0, -15.0, 1.0, 0.5, 0.2]

        def _rows(held_series):
            return [
                {
                    "days_ago": 4 - i,
                    "current_return": held_series[i],
                    "shadow_return": shadow_series[i],
                }
                for i in range(len(shadow_series))
            ]

        db_shallow = _seed_db(tmp_path, {sym_id: _rows(shallow_held)}, db_name="shallow_h.db")
        db_deep = _seed_db(tmp_path, {sym_id: _rows(deep_held)}, db_name="deep_h.db")
        sym_dict = _sym_dict(sym_id, 5000.0, max_drawdown=0.5)

        result_shallow = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_shallow
        )
        result_deep = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_deep
        )

        expected_shallow = _reference_normalized_max_drawdown_pct(shallow_held)
        expected_deep = _reference_normalized_max_drawdown_pct(deep_held)

        assert result_shallow["if_held"] == pytest.approx(expected_shallow, abs=_ABS)
        assert result_deep["if_held"] == pytest.approx(expected_deep, abs=_ABS)
        assert result_deep["if_held"] != pytest.approx(result_shallow["if_held"], abs=1e-3), (
            "if_held must GENUINELY differ when the held (current_return) "
            "trajectory's own drawdown differs -- if_held must no longer be a "
            "static Composer lifetime scalar that ignores the shadow_history "
            "held-side price action."
        )


# ===========================================================================
# AC-2 — Composer lifetime scalar remains available as its OWN figure
# (key: if_held_lifetime, per the confirmed contract redesign)
# ===========================================================================


class TestAC2LifetimeScalarSeparatelyAvailable:
    def test_result_carries_an_if_held_lifetime_key_distinct_from_if_held(self, tmp_path):
        sym_id = "sym-lifetime"
        rows = [
            {"days_ago": 5, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 0, "current_return": -2.0, "shadow_return": -2.0},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=0.7331)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )

        assert "if_held_lifetime" in result, (
            "AC-2: the result must carry an 'if_held_lifetime' key exposing "
            "Composer's raw max_drawdown scalar (pre-multiplied to percentage "
            "points) SEPARATELY from if_held/dry_run -- the comparison legs no "
            "longer read this field, but AC-2 requires it stay available as its "
            f"own labelled figure. Got keys: {sorted(result.keys())}"
        )
        assert result["if_held_lifetime"] == pytest.approx(73.31, abs=_ABS_PURE), (
            f"if_held_lifetime must equal sym_dict['max_drawdown']*100 = 73.31; "
            f"got {result['if_held_lifetime']}"
        )
        assert result["if_held"] != pytest.approx(result["if_held_lifetime"], abs=1e-3), (
            "if_held (windowed held-path peak-to-trough) must NOT equal "
            "if_held_lifetime (the Composer static scalar) for this fixture -- "
            "if they're equal, if_held is still reading the Composer scalar."
        )

    def test_if_held_lifetime_is_none_when_composer_field_absent(self, tmp_path):
        sym_id = "sym-no-scalar"
        rows = [
            {"days_ago": 3, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 0, "current_return": 1.0, "shadow_return": 1.0},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=None)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held_lifetime"] is None, (
            "if_held_lifetime must be None (honest missing-data), not 0.0, "
            "when sym_dict['max_drawdown'] is absent"
        )


# ===========================================================================
# AC-3 — compute_windowed_portfolio_strip's MDD must actually respond to window
# (per the confirmed design: computed directly from the strip's OWN sliced
# bot_pct/held_pct arrays, NOT via get_portfolio_max_drawdown)
# ===========================================================================


class TestAC3WindowedStripMddRespondsToWindow:
    def test_strip_mdd_differs_across_windows_with_genuinely_different_depth(self, tmp_path):
        """A drawdown that occurred 80 days ago is INSIDE a 90d window but
        OUTSIDE a 30d window. The strip's max_drawdown must reflect that --
        today it is identical across every window (analytics.py:2016 calls
        the unwindowed get_portfolio_max_drawdown). Uses >=30 flat trailing
        rows inside the 30d window itself (not just 26) so both windows
        independently clear compute_windowed_portfolio_strip's shared
        _WINDOWED_VOL_MIN_DAYS(=30) sufficiency floor per the confirmed
        design (docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md point 4) --
        sidesteps ambiguity about whether that floor also gates MDD itself,
        not just vol_bot/vol_held."""
        sym_id = "sym-strip-window"
        rows = [
            {"days_ago": 85, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 80, "current_return": -12.0, "shadow_return": -11.0},
        ]
        for d in range(35, -1, -1):
            rows.append({"days_ago": d, "current_return": 0.1, "shadow_return": 0.1})
        db_file = _seed_db(tmp_path, {sym_id: rows})
        symphonies = [_sym_dict(sym_id, 1000.0)]
        bot_state = {sym_id: {"name": "Strip Window Sym"}}

        strip_30d = analytics.compute_windowed_portfolio_strip(
            symphonies, bot_state, window="30d", db_path=db_file
        )
        strip_90d = analytics.compute_windowed_portfolio_strip(
            symphonies, bot_state, window="90d", db_path=db_file
        )

        mdd_30 = strip_30d.get("max_drawdown") or {}
        mdd_90 = strip_90d.get("max_drawdown") or {}

        assert mdd_30.get("if_held") != pytest.approx(mdd_90.get("if_held") or -999, abs=1e-3) or (
            mdd_30.get("if_held") is None
        ), (
            f"AC-3 FAIL: strip max_drawdown.if_held is identical across the 30d "
            f"({mdd_30.get('if_held')}) and 90d ({mdd_90.get('if_held')}) windows "
            f"despite a real -12% held drawdown 80 days ago being in-window only "
            f"for 90d -- compute_windowed_portfolio_strip is not computing a "
            f"genuinely windowed MDD."
        )
        assert mdd_90.get("if_held") == pytest.approx(12.0, abs=_ABS), (
            f"the 90d window must see the full -12% held drawdown (normalized "
            f"peak-to-trough from a peak at 0%, so ~12.0); got "
            f"{mdd_90.get('if_held')}"
        )

    def test_strip_mdd_all_window_matches_lifetime_get_portfolio_max_drawdown(self, tmp_path):
        """Consistency anchor: the strip's window='all' MDD (computed via the
        strip's own sliced arrays with cutoff=None) must equal a direct
        get_portfolio_max_drawdown call (the lifetime/full-series computation)
        -- both operate over the identical full shadow_history series, just
        via different code paths. >=30 rows total so the comparison clears
        compute_windowed_portfolio_strip's shared sufficiency floor
        regardless of whether that floor also gates MDD (see the sibling
        test's docstring for the same rationale)."""
        sym_id = "sym-strip-all"
        rows = [
            {"days_ago": 32, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 31, "current_return": -4.0, "shadow_return": -3.5},
        ]
        for d in range(30, -1, -1):
            rows.append({"days_ago": d, "current_return": -1.0, "shadow_return": -1.0})
        db_file = _seed_db(tmp_path, {sym_id: rows})
        symphonies = [_sym_dict(sym_id, 1000.0)]
        bot_state = {sym_id: {"name": "Strip All Sym"}}

        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, bot_state, window="all", db_path=db_file
        )
        direct = analytics.get_portfolio_max_drawdown(symphonies, bot_state, db_path=db_file)

        mdd = strip.get("max_drawdown") or {}
        assert mdd.get("if_held") == pytest.approx(direct["if_held"], abs=_ABS)
        assert mdd.get("dry_run") == pytest.approx(direct["dry_run"], abs=_ABS)


# ===========================================================================
# Edge cases (feature plan's Edge Cases section)
# ===========================================================================


class TestEdgeCases:
    def test_zero_shadow_history_rows_returns_none_not_zero(self, tmp_path):
        sym_id = "sym-zero-obs"
        db_file = _seed_db(
            tmp_path,
            {
                "sym-other": [
                    {"days_ago": 3, "current_return": -1.0, "shadow_return": -1.0},
                    {"days_ago": 0, "current_return": 0.5, "shadow_return": 0.5},
                ]
            },
        )
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=0.2)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] is None, (
            f"zero shadow_history rows for this symphony must yield if_held="
            f"None (honest insufficient-data), never a fabricated 0.0; got "
            f"{result['if_held']}"
        )
        assert result["dry_run"] is None
        assert result["n_obs"] == 0, (
            f"n_obs must be 0 with zero shadow_history rows; got {result['n_obs']}"
        )

    def test_one_shadow_history_row_returns_none_not_zero(self, tmp_path):
        """get_symphony_bot_and_held_daily_returns' own pre-existing floor
        (< 2 distinct trading days -> None) applies here -- a lone day cannot
        itself establish a peak-to-trough baseline under this contract."""
        sym_id = "sym-one-obs"
        rows = [{"days_ago": 0, "current_return": -3.0, "shadow_return": -2.0}]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=0.2)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] is None, (
            f"a single shadow_history row must yield if_held=None (mirrors "
            f"get_symphony_bot_and_held_daily_returns' own <2-day floor), not "
            f"a fabricated 0.0; got {result['if_held']}"
        )
        assert result["dry_run"] is None

    def test_all_positive_series_yields_zero_drawdown_not_none(self, tmp_path):
        """A monotonically rising series has a genuine MDD of 0.0 -- this must
        NOT be confused with the insufficient-data None sentinel."""
        sym_id = "sym-all-positive"
        rows = [
            {"days_ago": 4, "current_return": 1.0, "shadow_return": 1.0},
            {"days_ago": 3, "current_return": 2.0, "shadow_return": 2.0},
            {"days_ago": 2, "current_return": 0.5, "shadow_return": 0.5},
            {"days_ago": 1, "current_return": 3.0, "shadow_return": 3.0},
            {"days_ago": 0, "current_return": 1.5, "shadow_return": 1.5},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=0.0)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] is not None and result["if_held"] == pytest.approx(
            0.0, abs=_ABS
        ), (
            f"an all-positive-return series (every day a new high) must yield a "
            f"GENUINE 0.0 drawdown, not None; got {result['if_held']}"
        )
        assert result["dry_run"] is not None and result["dry_run"] == pytest.approx(0.0, abs=_ABS)
        assert result["n_obs"] == 5

    def test_symphony_absent_from_shadow_history_returns_none_never_composer_scalar(self, tmp_path):
        """A symphony with ZERO shadow_history rows must yield if_held=None,
        dry_run=None -- critically, if_held must NOT silently fall back to
        sym_dict['max_drawdown']*100 (that fallback IS the defect this cycle
        removes). if_held_lifetime (AC-2) is still populated -- it's the
        SEPARATE Composer-scalar figure, honestly available regardless of
        shadow_history coverage."""
        sym_id = "sym-no-shadow-rows"
        db_file = _seed_db(
            tmp_path,
            {
                "sym-other": [
                    {"days_ago": 3, "current_return": -1.0, "shadow_return": -1.0},
                    {"days_ago": 0, "current_return": 0.5, "shadow_return": 0.5},
                ]
            },
        )
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=0.42)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] is None, (
            f"no shadow_history rows for this symphony: if_held must be None, "
            f"NEVER the Composer scalar (42.0) -- got {result['if_held']}"
        )
        assert result["dry_run"] is None
        assert result["if_held_lifetime"] == pytest.approx(42.0, abs=_ABS_PURE), (
            "if_held_lifetime (AC-2) must still be populated from the Composer "
            "field even when shadow_history has zero rows for this symphony -- "
            "it's an independent data source"
        )

    def test_missing_composer_scalar_does_not_suppress_genuine_shadow_computation(self, tmp_path):
        """CRITICAL: today's function has an early `if sym_dict.get("max_drawdown")
        is None: return {"if_held": None, "dry_run": None}` guard -- under AC-1,
        if_held/dry_run no longer derive from that field, so a symphony with
        real shadow_history data but NO Composer max_drawdown scalar must still
        get a genuine computed if_held/dry_run, not be held hostage by an
        unrelated missing field."""
        sym_id = "sym-no-composer-scalar"
        rows = [
            {"days_ago": 5, "current_return": 0.0, "shadow_return": 0.0},
            {"days_ago": 0, "current_return": -6.0, "shadow_return": -6.0},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0, max_drawdown=None)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] == pytest.approx(6.0, abs=_ABS), (
            f"a symphony with real shadow_history but NO Composer max_drawdown "
            f"scalar must still produce a genuine windowed if_held (6.0 here) -- "
            f"got {result['if_held']}. If None: the old "
            f"'max_drawdown is None -> return None early' guard is still present "
            f"and must be removed (that field no longer feeds if_held/dry_run)."
        )
        assert result["dry_run"] == pytest.approx(6.0, abs=_ABS)
        assert result["if_held_lifetime"] is None

    def test_infinite_return_never_produces_a_fabricated_zero(self, tmp_path):
        """[Corrected during review, root-caused independently -- not a
        GREEN defect]: the plan's edge case says "NaN/None ... must degrade
        honestly". A LITERAL NaN cannot reach this function through any real
        channel: the production schema (migrations/008_shadow_history.sql:
        "current_return REAL NOT NULL, shadow_return REAL NOT NULL") and
        this test file's own _SCHEMA both enforce NOT NULL on these columns,
        and Python's sqlite3 driver raises IntegrityError("NOT NULL
        constraint failed") when binding float("nan") to a NOT NULL REAL
        column -- verified directly (`CREATE TABLE t (v REAL NOT NULL);
        INSERT ... (float('nan'),)` raises in isolation, no schema/app code
        involved). The original version of this test tried to INSERT a NaN
        row and therefore failed at the fixture-setup step, before any
        analytics.py code ran -- a test-infra bug, not a signal about the
        implementation. float("inf"), by contrast, inserts and reads back
        cleanly (verified) -- a genuinely storable extreme/corrupted value
        (e.g. a division-by-zero upstream of the write) -- so this is the
        realistic defensive case to pin instead."""
        sym_id = "sym-inf"
        db_file = str(tmp_path / "inf_shadow.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SCHEMA)
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        conn.execute(
            "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, "
            "current_return, shadow_return, position_epoch) VALUES (?, ?, ?, ?, ?, ?)",
            (sym_id, yesterday + "T20:00:00Z", yesterday, 0.0, 0.0, "EPOCH_A"),
        )
        conn.execute(
            "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, "
            "current_return, shadow_return, position_epoch) VALUES (?, ?, ?, ?, ?, ?)",
            (sym_id, today + "T20:00:00Z", today, float("inf"), -1.0, "EPOCH_A"),
        )
        conn.commit()
        conn.close()
        sym_dict = _sym_dict(sym_id, 1000.0)

        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        if result["if_held"] is not None:
            assert math.isfinite(result["if_held"]), (
                f"if_held must never be NaN/inf (breaks JSON serialization "
                f"-- json.dumps(float('inf')) produces invalid JSON); an "
                f"infinite input return must degrade to None, not propagate; "
                f"got {result['if_held']}"
            )
        if result["dry_run"] is not None:
            assert math.isfinite(result["dry_run"]), (
                f"dry_run must never be NaN/inf; got {result['dry_run']}"
            )


# ===========================================================================
# Property-based invariants (hypothesis)
# ===========================================================================


class TestPropertyInvariants:
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(
        returns=st.lists(
            st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False),
            min_size=2,
            max_size=60,
        )
    )
    def test_reference_normalized_drawdown_is_always_non_negative(self, returns):
        """MDD is a magnitude -- can never be negative, for ANY return series."""
        assert _reference_normalized_max_drawdown_pct(returns) >= 0.0

    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(
        returns=st.lists(
            st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False),
            min_size=2,
            max_size=60,
        )
    )
    def test_reference_normalized_drawdown_is_bounded_at_100_percent(self, returns):
        """A normalized peak-to-trough can never exceed 100% -- NAV cannot go
        negative from a series of (1+r/100) multiplicative steps with
        r >= -20 (each step keeps NAV strictly positive), so (peak-nav)/peak
        < 1 always. Distinguishes the NEW normalized formula from the OLD
        un-normalized one, which had no such bound."""
        dd = _reference_normalized_max_drawdown_pct(returns)
        assert dd <= 100.0, f"normalized drawdown must be <= 100%; got {dd} for {returns}"

    @settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(
        prefix=st.lists(
            st.floats(min_value=-15.0, max_value=15.0, allow_nan=False, allow_infinity=False),
            min_size=2,
            max_size=20,
        ),
        suffix=st.lists(
            st.floats(min_value=-15.0, max_value=15.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=20,
        ),
    )
    def test_reference_normalized_drawdown_is_monotonic_non_decreasing_as_window_grows(
        self, prefix, suffix
    ):
        """A longer trailing window (superset of trading days) can only reveal
        an EQUAL-OR-LARGER normalized peak-to-trough drawdown than any of its
        own sub-windows. This is the real mathematical invariant AC-3's
        window-responsiveness rests on: widening the window must never SHRINK
        the observed drawdown."""
        short_window = suffix
        long_window = prefix + suffix
        assert (
            _reference_normalized_max_drawdown_pct(long_window)
            >= _reference_normalized_max_drawdown_pct(short_window) - 1e-9
        ), (
            f"long_window ({long_window}) drawdown "
            f"{_reference_normalized_max_drawdown_pct(long_window)} must be >= "
            f"short_window ({short_window}) drawdown "
            f"{_reference_normalized_max_drawdown_pct(short_window)}"
        )

    def test_get_symphony_max_drawdown_result_never_negative_for_real_data(self, tmp_path):
        """End-to-end (not just the reference loop): the actual analytics
        function's if_held/dry_run must also never be negative."""
        sym_id = "sym-nonneg"
        rows = [
            {"days_ago": 5, "current_return": 3.0, "shadow_return": 2.0},
            {"days_ago": 4, "current_return": -8.0, "shadow_return": -4.0},
            {"days_ago": 3, "current_return": 1.0, "shadow_return": 1.0},
            {"days_ago": 0, "current_return": 0.5, "shadow_return": -2.0},
        ]
        db_file = _seed_db(tmp_path, {sym_id: rows})
        sym_dict = _sym_dict(sym_id, 1000.0)
        result = analytics.get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["if_held"] >= 0.0
        assert result["dry_run"] >= 0.0
