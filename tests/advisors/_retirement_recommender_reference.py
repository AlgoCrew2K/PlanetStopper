"""TEST-ONLY reference helpers for the retirement-recommender RED suite.

Two independent responsibilities, both deliberately decoupled from
advisors/retirement_recommender.py's own implementation so the tests it backs
are genuine oracles, not circular restatements of the SUT
(feedback_no_hardcoded_test_values / feedback_verify_backend_contract_before_fixtures):

1. `reference_fisher_ci_lower`/`reference_fisher_ci_upper` -- the textbook
   Fisher z-transform 95% CI for a Pearson r, computed independently of any
   production code. Z_95=1.96 matches this repo's own house convention
   (tests/guard_preconditions/_reference_stats.py's identical constant) rather
   than scipy.stats.norm.ppf(0.975)'s more precise 1.959964 -- an implementer
   following the same house convention will match this oracle far more
   tightly than the norm.ppf value would, and the test tolerance is generous
   enough to absorb either choice.

2. `seed_state_db` -- builds a scratch state DB via the REAL init_db()/
   run_migrations() pipeline (never a hand-copied shadow_history schema --
   test_m1_helpers.py's bespoke minimal-schema approach works for analytics-
   only tests, but build_recommendations also needs database.load_state()'s
   bot_state roster to discover which symphonies are live, and load_state()/
   save_state() have no db_file override -- they always read the module-level
   database.DB_FILE. seed_state_db points DB_FILE at the same scratch file
   analytics.get_symphony_bot_and_held_daily_returns(db_file=...) is given
   directly, so both read paths agree on one file) and inserts shadow_history
   rows for a {symphony_id: {trading_day: (shadow_return, current_return)}}
   map plus a minimal bot_state roster entry per symphony (just enough shape
   -- {"name": ..., "logic_holdings": {}} -- to be recognized as a live
   symphony row by the `isinstance(v, dict) and "name" in v` structural guard
   used throughout app.py/analytics.py).

Not imported by any production module.
"""

from __future__ import annotations

import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Fisher z-transform 95% CI oracle
# ---------------------------------------------------------------------------

# 95% two-sided normal critical value -- same constant name/value as
# tests/guard_preconditions/_reference_stats.py's Z_95 (house convention).
Z_95 = 1.96


def _fisher_ci(r: float, n: int) -> tuple[float, float]:
    """Return (ci_lower, ci_upper) for Pearson r over n observations via the
    standard Fisher z-transform:

        z = arctanh(r)
        se = 1 / sqrt(n - 3)
        z_lo, z_hi = z -+ Z_95 * se
        r_lo, r_hi = tanh(z_lo), tanh(z_hi)

    Requires n > 3 (se undefined otherwise) -- callers must not invoke this
    with n <= 3; that is a fixture-construction bug, not a case this oracle
    degrades for.
    """
    if n <= 3:
        raise ValueError(f"Fisher z CI requires n > 3, got n={n}")
    if not (-1.0 < r < 1.0):
        raise ValueError(f"Fisher z CI requires -1 < r < 1, got r={r}")
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    z_lo = z - Z_95 * se
    z_hi = z + Z_95 * se
    return math.tanh(z_lo), math.tanh(z_hi)


def reference_fisher_ci_lower(r: float, n: int) -> float:
    return _fisher_ci(r, n)[0]


def reference_fisher_ci_upper(r: float, n: int) -> float:
    return _fisher_ci(r, n)[1]


# ---------------------------------------------------------------------------
# Real state-DB seeding (migrations + bot_state roster + shadow_history rows)
# ---------------------------------------------------------------------------


def seed_state_db(
    tmp_path: Path,
    monkeypatch,
    *,
    series_by_symphony: dict[str, dict[str, tuple[float, float]]],
    filename: str = "retirement_recommender_state.db",
) -> str:
    """Build a scratch state DB via the REAL migration pipeline and seed both
    the bot_state roster and shadow_history rows. Returns the file path
    (already pointed at by database.DB_FILE for the duration of the test via
    monkeypatch -- callers do not need to patch it themselves).

    series_by_symphony: {symphony_id: {trading_day (YYYY-MM-DD): (shadow_return, current_return)}}
    shadow_return is the BOT/actual-traded value (analytics element [1]);
    current_return is the HELD/if-held counterfactual (element [2]) -- kept
    deliberately distinct per-day so the AC-2 basis-pin tests can prove which
    column a real read actually used.
    """
    import database as db_module  # noqa: PLC0415

    db_file = str(tmp_path / filename)
    monkeypatch.setattr(db_module, "DB_FILE", db_file)
    db_module.init_db()
    db_module.run_migrations()

    roster = {
        symphony_id: {"name": symphony_id, "logic_holdings": {}}
        for symphony_id in series_by_symphony
    }
    db_module.save_state(roster)

    conn = db_module.get_connection()
    for symphony_id, day_map in series_by_symphony.items():
        for trading_day, (shadow_return, current_return) in day_map.items():
            conn.execute(
                """INSERT INTO shadow_history
                   (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    symphony_id,
                    f"{trading_day}T19:00:00Z",
                    f"{trading_day}T15:00:00",
                    trading_day,
                    current_return,
                    shadow_return,
                ),
            )
    conn.commit()
    conn.close()
    return db_file


def trading_days(n: int, start: str = "2026-01-02") -> list[str]:
    """n sequential weekday-ish calendar labels (YYYY-MM-DD), simple +1-day
    steps -- the retirement-recommender math is date-alignment-based (shared
    date keys, not a real NYSE calendar), so plain consecutive calendar dates
    are sufficient and avoid a holiday-calendar dependency in test fixtures.
    """
    import datetime

    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]
