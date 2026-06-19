"""
RED tests — AC-2 / AC-5 portfolio self-consistency (ONE basis, per-card == portfolio).

THE BUG (DATA-CORRECTNESS-AUDIT S-1 + EXECUTIVE VERDICT):
  "three different parts of the same screen show three different guard-alpha values
  that cannot reconcile with each other." The per-card "saved +X%alpha" verdict
  (database.get_guard_alpha_by_symphony -> exit_triggers.at_return, a THIRD basis) is
  unrelated to the hero divergence basis, so a card can show non-zero alpha while the
  hero shows 0.00% on the same screen.

THE CONTRACT THESE TESTS PIN (AC-5):
  ONE basis everywhere. The per-symphony Guard Alpha (dry_run - if_held from
  get_symphony_cumulative_return — the divergence basis) must value-weight, via the
  SAME aggregation the portfolio uses (_value_weighted_portfolio), into the portfolio
  Guard Alpha. Per-card and portfolio reconcile by construction.

  Corollary anchors:
   - a portfolio composed ENTIRELY of never-triggered symphonies has portfolio Guard
     Alpha == 0.0 EXACTLY (no phantom alpha anywhere on the screen).
   - the portfolio Guard Alpha lies within the convex hull of the per-card alphas
     (a value-weighted mean cannot exceed its max contributor or fall below its min).

FIXTURE PROVENANCE:
  Reuses tests/fixtures/math/guard_alpha_lifetime_epochs.json (real all-epoch pairs).
  Every expected value is DERIVED IN-TEST from the per-symphony divergence formula and
  the value weights — nothing hardcoded (feedback_no_hardcoded_test_values).

These intentionally FAIL on cdfa088 for the cross-epoch scenarios (the per-card alphas
are stranded at 0.00% so the reconciliation is vacuously "0 == 0" but the per-card
values are wrong); the reconciliation + convex-hull assertions become meaningful only
once AC-1 lands. The never-triggered reconciliation anchor passes today and must keep
passing (regression guard).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import database as _db
from analytics import (
    get_portfolio_cumulative_return,
    get_symphony_cumulative_return,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "math" / "guard_alpha_lifetime_epochs.json"

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


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _make_portfolio_db(tmp_path: Path, scenarios: dict) -> str:
    """One shared shadow_history DB holding ALL scenario symphonies (multi-epoch rows).

    Mirrors the production single-DB layout where every symphony's shadow_history lives
    in one table — so the portfolio aggregation reads the same DB the cards read.
    """
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    db_file = str(Path(tmp_path) / "portfolio_shadow.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    for scen in scenarios.values():
        sym_id = scen["symphony_id"]
        for row in scen["shadow_history_rows"]:
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


def _sym_dict(sym_id: str, if_held_pct: float, value: float) -> dict:
    return {
        "id": sym_id,
        "value": value,
        "simple_return": if_held_pct / 100.0,
        "net_deposits": 1000.0,
        "time_weighted_return": if_held_pct / 100.0,
        "max_drawdown": 0.08,
    }


# ---------------------------------------------------------------------------
# AC-5 anchor — an all-untriggered portfolio has 0.0 guard alpha (regression guard)
# ---------------------------------------------------------------------------


class TestAllUntriggeredPortfolioIsZeroAlpha:
    """A portfolio of only never-triggered symphonies must show exactly 0.0 portfolio
    Guard Alpha — no phantom alpha aggregated from any card. Passes today; must stay.
    """

    def test_portfolio_of_only_untriggered_symphonies_is_zero_alpha(self, fixture, tmp_path):
        scen = fixture["scenarios"]["never_triggered_n2ooA"]
        # Two copies of the never-triggered symphony shape at different weights.
        scenarios = {
            "a": {
                "symphony_id": "untrig_a",
                "if_held_pct": scen["if_held_pct"],
                "shadow_history_rows": scen["shadow_history_rows"],
            },
            "b": {
                "symphony_id": "untrig_b",
                "if_held_pct": 12.0,
                "shadow_history_rows": scen["shadow_history_rows"],
            },
        }
        db_file = _make_portfolio_db(tmp_path, scenarios)
        symphonies = [
            _sym_dict("untrig_a", scen["if_held_pct"], value=3000.0),
            _sym_dict("untrig_b", 12.0, value=7000.0),
        ]
        result = get_portfolio_cumulative_return(symphonies, {}, db_path=db_file)
        portfolio_alpha = result["dry_run"] - result["if_held"]
        assert portfolio_alpha == pytest.approx(0.0, abs=1e-9), (
            f"AC-5 ANCHOR FAIL: all-untriggered portfolio shows Guard Alpha "
            f"{portfolio_alpha:.6f}% (must be exactly 0). No card contributes phantom alpha."
        )


# ---------------------------------------------------------------------------
# AC-2/AC-5 — per-card alphas value-weight to the portfolio alpha (ONE basis)
# ---------------------------------------------------------------------------


class TestPerCardReconcilesWithPortfolio:
    """The portfolio Guard Alpha must equal the value-weighted mean of the per-card
    Guard Alphas computed on the SAME divergence basis. No third basis, no drift.
    """

    def test_portfolio_alpha_equals_value_weighted_per_card_alphas(self, fixture, tmp_path):
        scenarios = {
            "iaSOO": {
                "symphony_id": "iaSOOUsmnC",
                "if_held_pct": fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"][
                    "if_held_pct"
                ],
                "shadow_history_rows": fixture["scenarios"][
                    "triggered_iaSOO_saved_in_prior_epochs"
                ]["shadow_history_rows"],
            },
            "lW4Zz": {
                "symphony_id": "lW4ZzWuqR8",
                "if_held_pct": fixture["scenarios"]["triggered_lW4Zz_saved_in_prior_epochs"][
                    "if_held_pct"
                ],
                "shadow_history_rows": fixture["scenarios"][
                    "triggered_lW4Zz_saved_in_prior_epochs"
                ]["shadow_history_rows"],
            },
            "n2ooA": {
                "symphony_id": "n2ooAZTvBR",
                "if_held_pct": fixture["scenarios"]["never_triggered_n2ooA"]["if_held_pct"],
                "shadow_history_rows": fixture["scenarios"]["never_triggered_n2ooA"][
                    "shadow_history_rows"
                ],
            },
        }
        db_file = _make_portfolio_db(tmp_path, scenarios)

        weights = {"iaSOOUsmnC": 5000.0, "lW4ZzWuqR8": 3000.0, "n2ooAZTvBR": 2000.0}
        symphonies = [
            _sym_dict(s["symphony_id"], s["if_held_pct"], value=weights[s["symphony_id"]])
            for s in scenarios.values()
        ]

        # Per-card Guard Alpha on the divergence basis (the SAME function the card uses).
        per_card_alpha: dict[str, float] = {}
        for s in symphonies:
            res = get_symphony_cumulative_return(s, None, db_path=db_file)
            per_card_alpha[s["id"]] = res["dry_run"] - res["if_held"]

        # Expected portfolio alpha = value-weighted mean of the per-card alphas.
        total_w = sum(weights.values())
        expected_portfolio_alpha = (
            sum(per_card_alpha[sid] * weights[sid] for sid in weights) / total_w
        )

        result = get_portfolio_cumulative_return(symphonies, {}, db_path=db_file)
        portfolio_alpha = result["dry_run"] - result["if_held"]

        # abs=1e-6: weighted-mean arithmetic on the same floats the cards used.
        assert portfolio_alpha == pytest.approx(expected_portfolio_alpha, abs=1e-6), (
            f"RECONCILIATION FAIL: portfolio Guard Alpha {portfolio_alpha:.6f}% != "
            f"value-weighted mean of per-card alphas {expected_portfolio_alpha:.6f}%. "
            f"per-card={per_card_alpha}. The hero and the cards must share ONE basis."
        )

    def test_portfolio_alpha_within_convex_hull_of_per_card_alphas(self, fixture, tmp_path):
        """PROPERTY: a value-weighted mean lies within [min, max] of its contributors.
        A portfolio alpha outside the per-card range proves a basis/aggregation mismatch.
        """
        scenarios = {
            "iaSOO": {
                "symphony_id": "iaSOOUsmnC",
                "if_held_pct": fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"][
                    "if_held_pct"
                ],
                "shadow_history_rows": fixture["scenarios"][
                    "triggered_iaSOO_saved_in_prior_epochs"
                ]["shadow_history_rows"],
            },
            "lW4Zz": {
                "symphony_id": "lW4ZzWuqR8",
                "if_held_pct": fixture["scenarios"]["triggered_lW4Zz_saved_in_prior_epochs"][
                    "if_held_pct"
                ],
                "shadow_history_rows": fixture["scenarios"][
                    "triggered_lW4Zz_saved_in_prior_epochs"
                ]["shadow_history_rows"],
            },
            "n2ooA": {
                "symphony_id": "n2ooAZTvBR",
                "if_held_pct": fixture["scenarios"]["never_triggered_n2ooA"]["if_held_pct"],
                "shadow_history_rows": fixture["scenarios"]["never_triggered_n2ooA"][
                    "shadow_history_rows"
                ],
            },
        }
        db_file = _make_portfolio_db(tmp_path, scenarios)
        weights = {"iaSOOUsmnC": 4000.0, "lW4ZzWuqR8": 4000.0, "n2ooAZTvBR": 2000.0}
        symphonies = [
            _sym_dict(s["symphony_id"], s["if_held_pct"], value=weights[s["symphony_id"]])
            for s in scenarios.values()
        ]

        per_card_alpha = []
        for s in symphonies:
            res = get_symphony_cumulative_return(s, None, db_path=db_file)
            per_card_alpha.append(res["dry_run"] - res["if_held"])

        result = get_portfolio_cumulative_return(symphonies, {}, db_path=db_file)
        portfolio_alpha = result["dry_run"] - result["if_held"]

        lo, hi = min(per_card_alpha), max(per_card_alpha)
        # 1e-9 slack for float boundary; the value-weighted mean cannot escape [lo, hi].
        assert lo - 1e-9 <= portfolio_alpha <= hi + 1e-9, (
            f"CONVEX-HULL FAIL: portfolio Guard Alpha {portfolio_alpha:.6f}% lies outside "
            f"the per-card range [{lo:.6f}, {hi:.6f}] — basis/aggregation mismatch."
        )
