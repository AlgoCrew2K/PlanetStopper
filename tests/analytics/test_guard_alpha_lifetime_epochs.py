"""
RED tests — AC-1 LIFETIME (cross-epoch) Guard Alpha.

THE BUG (DATA-CORRECTNESS-AUDIT C-1, proven against live alphabot_state.db):
  ``analytics._get_shadow_divergence_trajectory`` (analytics.py:657-731) scopes the
  divergence trajectory to the LATEST ``position_epoch`` only:

      AND position_epoch IS (SELECT position_epoch ... ORDER BY ts_utc DESC LIMIT 1)

  Every symphony that ever triggered had its epoch reset at the next market open
  (``database.wipe_transient_state``), so the trigger-day divergence is stranded in
  an OLD epoch and the latest epoch is a fresh 1-day (or flat multi-day) window with
  ZERO divergence. Consequence: ``get_symphony_cumulative_return(...).dry_run`` ==
  ``if_held`` for EVERY symphony, so the hero "Guard Alpha" is structurally 0.00% even
  for symphonies that genuinely saved money. The audit reproduced this directly:
  ``div_in_latest_epoch = 0`` for 100% of the 11 live symphonies.

THE CONTRACT THESE TESTS PIN (the fix):
  Guard Alpha must be computed over the LIFETIME divergence chain spanning ALL position
  epochs — not just the latest one:

      prod_shadow  = PROD over ALL epochs' EOD rows of (1 + shadow_return/100)
      prod_current = PROD over the same rows of (1 + current_return/100)
      lifetime_divergence_pct = (prod_shadow - prod_current) * 100
      dry_run = if_held + lifetime_divergence_pct

  CRITICAL SUBTLETY (the user's directive): chain the GUARD's DIVERGENCE across epochs,
  but do NOT chain a prior position's market RETURNS. ``if_held`` stays the current
  Composer lifetime simple_return; only the divergence product spans epochs. A naive
  fix that re-derives if_held from old-epoch market returns is WRONG and these tests
  catch it (test_lifetime_if_held_baseline_is_unchanged_by_cross_epoch_divergence).

INVARIANTS:
  - never-triggered (shadow == current every day, every epoch) -> lifetime Guard Alpha
    == 0.0 EXACTLY (abs=1e-9). The anchor.
  - triggered-in-a-prior-epoch -> lifetime Guard Alpha == the genuine non-zero divergence
    (NOT 0.00%), and STRICTLY DIFFERENT from the latest-epoch-only value the buggy code
    returns (the discriminating assertion that can only pass post-fix).

FIXTURE PROVENANCE:
  tests/fixtures/math/guard_alpha_lifetime_epochs.json — captured from live
  alphabot_state.db (read-only): last shadow_history row per (symphony_id, trading_day)
  across ALL epochs. Real recorded (shadow_return, current_return) pairs. Every expected
  value is DERIVED IN-TEST from those rows by the divergence formula — none is hardcoded
  (per feedback_no_hardcoded_test_values).

NOTE: these tests intentionally FAIL on cdfa088 (latest-epoch-only scope). They go GREEN
only when the implementer extends the trajectory to span all epochs while leaving the
single-epoch tests in test_shadow_divergence_golden.py and the epoch-segmentation tests
in test_shadow_trajectory_position_epoch.py passing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import database as _db
from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "math" / "guard_alpha_lifetime_epochs.json"
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    """Load the captured multi-epoch fixture once per module (read-only data)."""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shadow DB builders — mirror the live schema, with a real multi-epoch structure
# ---------------------------------------------------------------------------

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


def _make_multi_epoch_db(tmp_path: Path, sym_id: str, rows: list[dict]) -> str:
    """Build a shadow_history DB carrying the rows' real ``position_epoch`` labels.

    Unlike test_shadow_divergence_golden's ``_make_db`` (which forces ONE epoch),
    this preserves each row's epoch so the latest-epoch-only scope and the lifetime
    scope diverge — exactly the C-1 condition.
    """
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    db_file = str(Path(tmp_path) / f"shadow_{sym_id}.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    for row in rows:
        conn.execute(
            "INSERT INTO shadow_history "
            "(symphony_id, ts_utc, trading_day, current_return, shadow_return, "
            "is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sym_id,
                row["ts_utc"],
                row["trading_day"],
                row["current_return"],
                row["shadow_return"],
                row.get("is_post_trigger", 0),
                row["position_epoch"],
            ),
        )
    conn.commit()
    conn.close()
    _db._shadow_cr_cache.clear()
    return db_file


def _sym(sym_id: str, if_held_pct: float, max_dd_pct: float = 8.0) -> dict:
    """A sym_dict with a non-zero simple_return so if_held == if_held_pct (no TWR fallback)."""
    return {
        "id": sym_id,
        "simple_return": if_held_pct / 100.0,
        "net_deposits": 1000.0,
        "time_weighted_return": if_held_pct / 100.0,
        "max_drawdown": max_dd_pct / 100.0,
    }


# ---------------------------------------------------------------------------
# Derivation helpers — the lifetime divergence formula, applied to ALL rows
# ---------------------------------------------------------------------------

def _lifetime_divergence_pct(rows: list[dict]) -> float:
    """(PROD(1+shadow/100) - PROD(1+current/100)) * 100 over ALL rows (all epochs)."""
    prod_shadow = 1.0
    prod_current = 1.0
    for r in rows:
        prod_shadow *= 1.0 + r["shadow_return"] / 100.0
        prod_current *= 1.0 + r["current_return"] / 100.0
    return (prod_shadow - prod_current) * 100.0


def _latest_epoch_rows(rows: list[dict], latest_label: str) -> list[dict]:
    """The subset the BUGGY code sees: rows whose position_epoch == the latest label."""
    return [r for r in rows if r["position_epoch"] == latest_label]


def _lifetime_bot_equity_mdd(rows: list[dict], if_held_pct: float) -> float:
    """Peak-to-trough of bot_equity[t] = if_held + cum_divergence[0..t]*100 over ALL rows."""
    prod_shadow = 1.0
    prod_current = 1.0
    equity: list[float] = []
    for r in rows:
        prod_shadow *= 1.0 + r["shadow_return"] / 100.0
        prod_current *= 1.0 + r["current_return"] / 100.0
        equity.append(if_held_pct + (prod_shadow - prod_current) * 100.0)
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak - v > max_dd:
            max_dd = peak - v
    return max_dd


# ===========================================================================
# KILLER ANCHOR — never-triggered symphony has lifetime Guard Alpha == 0 exactly
# ===========================================================================

class TestNeverTriggeredLifetimeAlphaIsExactlyZero:
    """The anchor that any cross-epoch fix MUST preserve.

    n2ooA has shadow == current on every day in every epoch -> lifetime divergence
    is identically zero regardless of how many epochs are chained. This must hold
    after the latest-epoch-only scope is widened to lifetime.
    """

    def test_never_triggered_lifetime_guard_alpha_is_zero(self, fixture, tmp_path):
        scen = fixture["scenarios"]["never_triggered_n2ooA"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]

        # DERIVED, not hardcoded: with shadow == current on every row the formula gives 0.
        expected_divergence = _lifetime_divergence_pct(rows)
        assert expected_divergence == pytest.approx(0.0, abs=1e-12), (
            "fixture sanity: never-triggered scenario must derive 0 divergence; "
            f"got {expected_divergence}"
        )

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_cumulative_return(_sym(sym_id, if_held), None, db_path=db_file)

        guard_alpha = result["dry_run"] - if_held
        # abs=1e-9: a never-touched symphony must be EXACTLY zero, not approximately.
        assert guard_alpha == pytest.approx(0.0, abs=1e-9), (
            f"ANCHOR FAIL: never-triggered '{sym_id}' shows lifetime Guard Alpha "
            f"= {guard_alpha:.6f}% (dry_run={result['dry_run']:.6f}%, if_held={if_held}%). "
            "shadow == current on every day in every epoch -> divergence is 0 by "
            "construction. Any non-zero value means the cross-epoch chain is summing "
            "absolute market returns instead of the shadow-vs-current divergence."
        )

    def test_never_triggered_lifetime_mdd_is_zero(self, fixture, tmp_path):
        scen = fixture["scenarios"]["never_triggered_n2ooA"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_max_drawdown(_sym(sym_id, if_held), None, db_path=db_file)

        assert result["dry_run"] is not None, (
            "dry_run MDD must not be None for a multi-epoch series with >= 2 days"
        )
        # DERIVED: flat divergence every step -> bot equity flat at if_held -> MDD 0.
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9), (
            f"MDD ANCHOR FAIL: never-triggered '{sym_id}' lifetime bot-equity MDD "
            f"= {result['dry_run']:.6f}% (must be exactly 0). When shadow == current "
            "across all epochs the bot equity path is flat at if_held."
        )


# ===========================================================================
# CROSS-EPOCH RED — triggered-in-a-prior-epoch shows its REAL saved %, not 0.00%
# ===========================================================================

class TestTriggeredInPriorEpochShowsLifetimeAlpha:
    """The C-1 RED: a symphony whose only divergence lives in an OLD epoch must show
    its genuine lifetime Guard Alpha. The current latest-epoch-only scope reports
    0.00% (or the 1-day latest-epoch value) -> these fail on cdfa088.
    """

    @pytest.mark.parametrize(
        "scenario_key",
        ["triggered_iaSOO_saved_in_prior_epochs", "triggered_lW4Zz_saved_in_prior_epochs"],
    )
    def test_lifetime_guard_alpha_equals_cross_epoch_divergence(
        self, fixture, tmp_path, scenario_key
    ):
        scen = fixture["scenarios"][scenario_key]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]

        expected_divergence = _lifetime_divergence_pct(rows)
        # Fixture sanity: these scenarios MUST have genuine non-zero divergence, else
        # the test is vacuous (it would pass against the buggy 0.00% too).
        assert abs(expected_divergence) > 0.5, (
            f"fixture sanity: '{scenario_key}' must carry material prior-epoch "
            f"divergence; derived {expected_divergence:.6f}%"
        )

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_cumulative_return(_sym(sym_id, if_held), None, db_path=db_file)
        guard_alpha = result["dry_run"] - if_held

        # abs=1e-6: compounding 9 small daily returns; pure arithmetic, no library noise.
        assert guard_alpha == pytest.approx(expected_divergence, abs=1e-6), (
            f"CROSS-EPOCH FAIL: '{sym_id}' lifetime Guard Alpha = {guard_alpha:.6f}%, "
            f"expected {expected_divergence:.6f}% (divergence chained across ALL epochs). "
            "The current latest-epoch-only scope strands the prior-epoch guard effect and "
            "reports ~0.00%."
        )

    @pytest.mark.parametrize(
        "scenario_key",
        ["triggered_iaSOO_saved_in_prior_epochs", "triggered_lW4Zz_saved_in_prior_epochs"],
    )
    def test_lifetime_alpha_strictly_differs_from_latest_epoch_only_value(
        self, fixture, tmp_path, scenario_key
    ):
        """DISCRIMINATING assertion — can ONLY pass post-fix.

        Builds two DBs: the full lifetime DB and a DB containing ONLY the latest epoch's
        rows (what the buggy scope effectively sees). The lifetime Guard Alpha must be
        STRICTLY different from the latest-epoch-only Guard Alpha. If the implementation
        still scopes to the latest epoch, the two values coincide and this fails.
        """
        scen = fixture["scenarios"][scenario_key]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]
        latest_label = fixture["_latest_epoch_label"]

        latest_rows = _latest_epoch_rows(rows, latest_label)
        # The captured latest epoch is a single post-reset day -> buggy scope yields
        # trajectory None -> dry_run == if_held -> latest-epoch alpha == 0.
        latest_only_alpha = (
            _lifetime_divergence_pct(latest_rows) if len(latest_rows) >= 2 else 0.0
        )
        lifetime_alpha = _lifetime_divergence_pct(rows)

        assert abs(lifetime_alpha - latest_only_alpha) > 1e-3, (
            "fixture sanity: lifetime and latest-epoch values must materially differ"
        )

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_cumulative_return(_sym(sym_id, if_held), None, db_path=db_file)
        produced_alpha = result["dry_run"] - if_held

        assert produced_alpha != pytest.approx(latest_only_alpha, abs=1e-6), (
            f"SCOPE FAIL: '{sym_id}' produced Guard Alpha {produced_alpha:.6f}% which "
            f"equals the latest-epoch-only value {latest_only_alpha:.6f}%. The computation "
            "is still scoped to the latest position_epoch (C-1) and strands prior-epoch "
            "guard effect."
        )
        assert produced_alpha == pytest.approx(lifetime_alpha, abs=1e-6), (
            f"'{sym_id}' produced {produced_alpha:.6f}%, expected lifetime {lifetime_alpha:.6f}%"
        )


# ===========================================================================
# if_held BASELINE — divergence spans epochs but if_held does NOT absorb old returns
# ===========================================================================

class TestLifetimeIfHeldBaselineIsUnchanged:
    """The user's explicit constraint: chain the GUARD's divergence across epochs WITHOUT
    chaining a prior position's market returns into the if_held baseline.

    if_held must stay the Composer lifetime simple_return supplied in sym_dict. The whole
    cross-epoch effect lands in (dry_run - if_held), never in if_held itself.
    """

    def test_lifetime_if_held_baseline_is_unchanged_by_cross_epoch_divergence(
        self, fixture, tmp_path
    ):
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_cumulative_return(_sym(sym_id, if_held), None, db_path=db_file)

        # if_held must equal the supplied Composer simple_return exactly — NOT re-derived
        # from compounding old-epoch market (current_return) moves.
        assert result["if_held"] == pytest.approx(if_held, abs=1e-9), (
            f"BASELINE FAIL: if_held drifted to {result['if_held']:.6f}% from the supplied "
            f"Composer simple_return {if_held}%. A cross-epoch fix must not fold prior-position "
            "market returns into if_held — only the divergence (dry_run - if_held) spans epochs."
        )

    def test_property_lifetime_alpha_invariant_across_if_held_baselines(self, fixture, tmp_path):
        """PROPERTY: the lifetime Guard Alpha (dry_run - if_held) depends ONLY on the
        shadow-vs-current divergence, not on the if_held baseline. For the same rows,
        sweeping if_held over {-10, 0, 26.9, 80} must leave Guard Alpha invariant.
        """
        scen = fixture["scenarios"]["triggered_lW4Zz_saved_in_prior_epochs"]
        rows = scen["shadow_history_rows"]
        sym_id = scen["symphony_id"]
        expected_divergence = _lifetime_divergence_pct(rows)

        for i, if_held_test in enumerate([-10.0, 0.0, 26.9, 80.0]):
            db_file = _make_multi_epoch_db(tmp_path / f"b{i}", sym_id, rows)
            result = get_symphony_cumulative_return(
                _sym(sym_id, if_held_test), None, db_path=db_file
            )
            guard_alpha = result["dry_run"] - if_held_test
            assert guard_alpha == pytest.approx(expected_divergence, abs=1e-6), (
                f"PROPERTY FAIL with if_held={if_held_test}%: Guard Alpha={guard_alpha:.6f}%, "
                f"expected divergence {expected_divergence:.6f}% (invariant to if_held)."
            )


# ===========================================================================
# MDD CROSS-EPOCH — bot drawdown spans the lifetime divergence chain
# ===========================================================================

class TestLifetimeMddSpansEpochs:
    """The bot-equity MDD must be peak-to-trough over the LIFETIME divergence equity path,
    so a prior-epoch divergence drawdown is not lost when the epoch resets.
    """

    def test_triggered_lifetime_mdd_equals_cross_epoch_peak_to_trough(self, fixture, tmp_path):
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]

        expected_mdd = _lifetime_bot_equity_mdd(rows, if_held)
        assert expected_mdd > 0.0, (
            "fixture sanity: iaSOO's lifetime divergence path must have a real drawdown"
        )

        db_file = _make_multi_epoch_db(tmp_path, sym_id, rows)
        result = get_symphony_max_drawdown(_sym(sym_id, if_held), None, db_path=db_file)

        assert result["dry_run"] is not None, "dry_run MDD must not be None for >= 2 days"
        assert result["dry_run"] == pytest.approx(expected_mdd, abs=1e-6), (
            f"MDD CROSS-EPOCH FAIL: '{sym_id}' lifetime MDD = {result['dry_run']:.6f}%, "
            f"expected {expected_mdd:.6f}% (peak-to-trough over the ALL-epoch divergence "
            "equity path). The latest-epoch-only scope loses the prior-epoch drawdown."
        )
