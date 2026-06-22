"""
RED tests — Guard-Alpha post-mortem producer: saved_dollars must use
shadow_history.current_return as the if-held source, NOT the basket reconstruction.

THE BUG (guard-alpha-saved-diagnosis.md, 2026-06-22):
  The live post-mortem file showed "$2.96 saved across 11 exits" against an operator-
  expected ~$208. Root cause: reporting.py:52-63 reconstructs the if-held return from
  triggered_basket_snapshot + live_prices. When the basket prices are frozen near the
  exit level, post_trigger_move ≈ 0, so live_ret ≈ f_ret → saved_pct ≈ 0.
  The engine's shadow_history.current_return already records the correct if-held value.
  E.g. symphony 5XjzXjdG: producer said if-held=1.80 vs correct 0.63 → $0.50 vs $13.18.

THE CONTRACT THESE TESTS PIN:
  1. When triggered_basket_snapshot is populated and live_prices is provided (the Stage-1
     reporting path), the CORRECT if-held source is sym.current_return
     (which the caller populates from shadow_history.current_return before the call).
     The basket reconstruction path MUST NOT collapse saved_dollars to near-zero when
     there is real divergence between exit_return and current_return.

  2. MAGNITUDE guard: for any trigger with exit_return meaningfully above current_return,
     saved_dollars must be on the order of (exit_return - current_return)/100 * position_value,
     NOT near zero.

  3. Source-of-if-held guard: saved_pct == exit_return - if_held where if_held reflects
     current_return, NOT the basket reconstruction result. This is provable by injecting
     stale basket prices (pinned near the exit-level price) and verifying the producer
     does NOT report near-zero saved despite large current_return divergence.

  4. Blast-radius: analytics.get_history_summary sums saved_dollars from post-mortem
     files; it carries the same defect when the producer writes wrong values. Once the
     producer is fixed, the aggregation automatically reflects correct values.

  5. Discord/QuickChart: send_eod_discord_post reads saved_dollars from the file — the
     file format is unchanged by the fix, so Discord path must still build successfully.

FIXTURE PROVENANCE:
  tests/fixtures/math/guard_alpha_postmortem_producer.json
  Expected values are ALWAYS derived from fixture inputs via the correct formula:
      saved_dollars = (triggered_at_return - shadow_history_current_return) / 100
                      * current_value
  Never hardcoded from a producer run.

WHAT MUST FAIL TODAY (RED) and WHY:
  Tests 1-3 inject stale basket prices (near exit level), so the current basket-
  reconstruction path produces live_ret ≈ f_ret → saved_pct ≈ 0. The tests assert
  non-zero saved_dollars of the correct magnitude → RED on current code, GREEN after fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import reporting

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "math" / "guard_alpha_postmortem_producer.json"
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _db_stubs():
    """Stub database calls so tests never touch SQLite."""
    patch_normalize = patch(
        "reporting.database.normalize_name", side_effect=lambda n: n.strip().lower()
    )
    patch_get_strat = patch(
        "reporting.database.get_symphony_strategy",
        return_value={"params": {}, "locked_vars": {}},
    )
    return patch_normalize, patch_get_strat


def _build_bot_state(case: dict, current_return_override: float | None = None) -> dict:
    """
    Construct a minimal bot_state from a fixture case.

    The caller passes shadow_history.current_return via current_return_override
    (or the fixture's shadow_history_current_return) — this is how the engine
    actually supplies the signal: the caller hydrates sym["current_return"] from
    shadow_history before passing bot_state to generate_eod_snapshot.
    """
    inp = case["inputs"]
    if_held = (
        current_return_override
        if current_return_override is not None
        else inp["shadow_history_current_return"]
    )
    return {
        "SYM_TEST": {
            "triggered": True,
            "triggered_at_return": inp["triggered_at_return"],
            "triggered_at_stop": inp["triggered_at_return"] - 1.0,  # != f_ret → not Take-Profit
            "triggered_reason": "Trailing Stop",
            # current_return = shadow_history.current_return (the correct if-held signal).
            # The engine records this; the post-mortem producer should READ it from here,
            # not reconstruct it from the basket.
            "current_return": if_held,
            "current_value": inp["current_value"],
            "triggered_basket_snapshot": inp["triggered_basket_snapshot"],
            "name": "Test Symphony",
            "account": "acct-001",
            "shadow_hwm": 5.0,
            "triggered_at_hwm": 5.0,
            "triggered_at_time": "15:54:00",
            "symphony_vol": 0.015,
        }
    }


def _derive_expected(case: dict) -> tuple[float, float]:
    """
    Derive the correct saved_pct and saved_dollars from fixture inputs.

    Formula (diagnosis-confirmed):
        saved_pct    = exit_return - shadow_history.current_return   (percent values)
        saved_dollars = saved_pct / 100 * position_value

    Returns (expected_saved_pct, expected_saved_dollars).
    """
    inp = case["inputs"]
    saved_pct = inp["triggered_at_return"] - inp["shadow_history_current_return"]
    saved_dollars = saved_pct / 100.0 * inp["current_value"]
    return saved_pct, saved_dollars


def _basket_collapse_threshold(case: dict) -> float:
    """
    Return the saved_dollars value the BROKEN basket path would produce.

    Stale/at-exit basket prices → post_trigger_move ≈ 0 → live_ret ≈ f_ret
    → saved_pct ≈ 0 → saved_dollars ≈ 0. We parameterise this per case using
    the actual basket price moves from the fixture; the result is near (but not
    exactly) zero.
    """
    inp = case["inputs"]
    f_ret = inp["triggered_at_return"]
    post_trigger_move = 0.0
    for h in inp["triggered_basket_snapshot"]:
        t = h["ticker"]
        alloc = h["allocation"]
        p_start = h["price"]
        p_now = inp["live_prices"][t]["last_price"]
        if p_start > 0 and p_now > 0:
            post_trigger_move += alloc * ((p_now - p_start) / p_start)
    basket_live_ret = f_ret + post_trigger_move * 100.0
    basket_saved_pct = f_ret - basket_live_ret
    basket_saved_dollars = basket_saved_pct / 100.0 * inp["current_value"]
    return basket_saved_dollars


# ===========================================================================
# 1. Producer math: saved_dollars must reflect current_return divergence,
#    not the basket-reconstruction collapse.
#    Exercises the BROKEN path (non-empty basket, stale prices).
# ===========================================================================


class TestSavedDollarsReflectsCurrentReturnDivergence:
    """
    RED: current producer uses basket reconstruction which collapses to ~$0
    when basket prices are near exit level. The correct output is
    (exit_return - current_return)/100 * position_value.
    """

    @pytest.mark.parametrize(
        "case_name",
        [
            "basket_prices_stale_current_return_diverges",
            "large_divergence_basket_near_exit_current_return_negative",
            "current_return_above_exit_negative_guard_alpha",
        ],
    )
    def test_saved_dollars_equals_exit_minus_current_return_times_position(
        self, fixture, tmp_path, monkeypatch, case_name
    ):
        """
        saved_dollars must equal (exit_return - current_return) / 100 * position_value
        where current_return = shadow_history.current_return.

        The test injects stale basket prices (near-exit price) to reproduce the
        collapse-to-zero basket reconstruction path. The CORRECT producer must NOT
        produce near-zero saved_dollars when current_return is deeply divergent.

        Tolerance: abs=0.01 (one cent). The formula is multiplication of two-decimal
        percent inputs by a dollar amount; float error is sub-cent on position values
        < $100k.
        """
        case = fixture["cases"][
            next(i for i, c in enumerate(fixture["cases"]) if c["name"] == case_name)
        ]
        bot_state = _build_bot_state(case)
        live_prices = case["inputs"]["live_prices"]

        date_str = "2026-06-22"
        report_file = tmp_path / f"post_mortem_{date_str}.json"

        monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
        patch_norm, patch_strat = _db_stubs()

        with patch_norm, patch_strat:
            reporting.generate_eod_snapshot(
                bot_state,
                date_str,
                is_post_rebalance=False,
                discord_webhook_url=None,
                live_prices=live_prices,
            )

        assert report_file.exists(), "Stage-1 snapshot was not created"
        with open(report_file, encoding="utf-8") as fh:
            snapshot = json.load(fh)

        assert len(snapshot["triggers"]) == 1
        trigger = snapshot["triggers"][0]

        _expected_saved_pct, expected_saved_dollars = _derive_expected(case)

        # The basket-collapse path produces a value near zero; the correct value is far
        # from zero. The test FAILS on the broken producer (which collapses the basket)
        # and PASSES after the fix sourcing from current_return.
        basket_dollars = _basket_collapse_threshold(case)

        assert abs(trigger["saved_dollars"] - basket_dollars) > abs(expected_saved_dollars) * 0.5, (
            f"REGRESSION: saved_dollars={trigger['saved_dollars']:.4f} collapsed near the basket "
            f"reconstruction value ({basket_dollars:.4f}), which is the BUG. "
            f"Expected ~{expected_saved_dollars:.4f} from current_return divergence."
        )

        # The correct value must be close to the formula result.
        assert trigger["saved_dollars"] == pytest.approx(expected_saved_dollars, abs=0.01), (
            # abs=0.01: cent-precision is sufficient; float error on percent-times-dollar
            # is sub-penny for position values under $100k.
            f"saved_dollars={trigger['saved_dollars']:.4f} != expected "
            f"{expected_saved_dollars:.4f} (derived from current_return divergence). "
            f"Formula: ({case['inputs']['triggered_at_return']} - "
            f"{case['inputs']['shadow_history_current_return']}) / 100 "
            f"* {case['inputs']['current_value']}"
        )


# ===========================================================================
# 2. MAGNITUDE sanity guard — catches the collapse-to-zero regression
#    When divergence >= 1pp and position_value >= $500, saved_dollars must be
#    at least $5 in absolute value (cannot be near zero).
# ===========================================================================


class TestSavedDollarsMagnitudeNotCollapsingToZero:
    """
    PROPERTY (RED → GREEN): for a trigger with |exit_return - current_return| >= 1pp
    and position_value >= $500, |saved_dollars| must be >= $5.

    This catches the exact failure mode from the live diagnosis: symphony 5XjzXjdG had
    1.22pp divergence on $1020, correct saved ≈ $12.44, but producer wrote $0.50.
    Any value < $5 on a 1pp+ divergence is evidence of the basket-collapse bug.
    """

    @pytest.mark.parametrize(
        "case_name",
        [
            "basket_prices_stale_current_return_diverges",
            "large_divergence_basket_near_exit_current_return_negative",
        ],
    )
    def test_saved_dollars_magnitude_not_near_zero_for_significant_divergence(
        self, fixture, tmp_path, monkeypatch, case_name
    ):
        """
        |saved_dollars| >= $5 when |exit_return - current_return| >= 1pp and position >= $500.

        This specific threshold is derived from: min_divergence_pp * min_position / 100
        = 1.0 * 500 / 100 = $5. Both fixture cases here have divergence >= 1.2pp and
        position >= $985, so correct saved_dollars >= $11.82.

        A producer reporting < $5 for these inputs is exhibiting the collapse-to-zero bug.
        """
        case = fixture["cases"][
            next(i for i, c in enumerate(fixture["cases"]) if c["name"] == case_name)
        ]
        inp = case["inputs"]
        divergence_pp = abs(inp["triggered_at_return"] - inp["shadow_history_current_return"])
        assert divergence_pp >= 1.0, (
            f"Fixture case {case_name} divergence too small to be meaningful"
        )
        assert inp["current_value"] >= 500.0, f"Fixture case {case_name} position too small"

        bot_state = _build_bot_state(case)
        live_prices = inp["live_prices"]

        date_str = "2026-06-22-mag"
        report_file = tmp_path / f"post_mortem_{date_str}.json"

        monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
        patch_norm, patch_strat = _db_stubs()

        with patch_norm, patch_strat:
            reporting.generate_eod_snapshot(
                bot_state,
                date_str,
                is_post_rebalance=False,
                discord_webhook_url=None,
                live_prices=live_prices,
            )

        assert report_file.exists()
        with open(report_file, encoding="utf-8") as fh:
            snapshot = json.load(fh)

        trigger = snapshot["triggers"][0]
        # Minimum meaningful threshold: 1pp on $500 = $5. Both cases greatly exceed this.
        assert abs(trigger["saved_dollars"]) >= 5.0, (
            f"COLLAPSE-TO-ZERO DETECTED: |saved_dollars|={abs(trigger['saved_dollars']):.4f} < $5 "
            f"for a {divergence_pp:.2f}pp divergence on ${inp['current_value']:.0f} position. "
            f"This is the basket-reconstruction collapse bug. "
            f"Correct value is ~${abs(_derive_expected(case)[1]):.2f}."
        )


# ===========================================================================
# 3. Source-of-if-held guard: saved_pct must match exit_return - current_return,
#    NOT exit_return - basket_reconstruction_live_ret.
#    Explicitly proves the if-held source is current_return.
# ===========================================================================


class TestIfHeldSourceIsCurrentReturnNotBasketReconstruction:
    """
    RED: the current producer reconstructs if-held from the basket (live_ret via
    basket prices), producing saved_pct ≈ 0. The correct producer reads if-held
    from sym["current_return"] (which the engine populates from shadow_history).

    We verify this by checking that saved_pct_guard_alpha matches the formula
    using current_return, not the basket reconstruction.
    """

    def test_saved_pct_equals_exit_return_minus_current_return(
        self, fixture, tmp_path, monkeypatch
    ):
        """
        saved_pct_guard_alpha must equal round(exit_return - current_return, 2).
        When the basket prices are stale (near-exit), the broken path gives
        saved_pct ≈ 0. The fix uses current_return, which is divergent.

        Tolerance: abs=1e-9. Both inputs are two-decimal fixture values; round(x, 2)
        on a two-decimal input is exact in IEEE 754 for all values here.
        """
        case = fixture["cases"][0]  # basket_prices_stale_current_return_diverges
        inp = case["inputs"]

        bot_state = _build_bot_state(case)
        live_prices = inp["live_prices"]

        date_str = "2026-06-22-src"
        report_file = tmp_path / f"post_mortem_{date_str}.json"

        monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
        patch_norm, patch_strat = _db_stubs()

        with patch_norm, patch_strat:
            reporting.generate_eod_snapshot(
                bot_state,
                date_str,
                is_post_rebalance=False,
                discord_webhook_url=None,
                live_prices=live_prices,
            )

        assert report_file.exists()
        with open(report_file, encoding="utf-8") as fh:
            snapshot = json.load(fh)

        trigger = snapshot["triggers"][0]

        # The correct saved_pct is derived from current_return, not the basket.
        expected_saved_pct = round(
            inp["triggered_at_return"] - inp["shadow_history_current_return"], 2
        )

        assert trigger["saved_pct_guard_alpha"] == pytest.approx(expected_saved_pct, abs=1e-9), (
            f"SOURCE MISMATCH: saved_pct_guard_alpha={trigger['saved_pct_guard_alpha']:.4f} "
            f"does not match formula using current_return: "
            f"exit={inp['triggered_at_return']} - if_held={inp['shadow_history_current_return']} "
            f"= {expected_saved_pct:.4f}. "
            f"The producer is using the basket reconstruction (which collapses to "
            f"≈{_basket_collapse_threshold(case):.4f}) instead of current_return."
        )

    def test_saved_pct_not_equal_to_basket_reconstruction_result(
        self, fixture, tmp_path, monkeypatch
    ):
        """
        saved_pct_guard_alpha must NOT equal the basket reconstruction value.
        This is the direct anti-regression guard: proves the broken path is not taken.

        We compute the basket reconstruction result explicitly (same arithmetic as the
        broken code) and assert the producer does NOT produce that value.
        """
        case = fixture["cases"][0]  # basket_prices_stale_current_return_diverges
        inp = case["inputs"]

        # Compute what the broken basket path produces (mirror of reporting.py:54-63).
        basket_post_trigger_move = 0.0
        for h in inp["triggered_basket_snapshot"]:
            t = h["ticker"]
            alloc = h["allocation"]
            p_start = h["price"]
            p_now = inp["live_prices"][t]["last_price"]
            if p_start > 0 and p_now > 0:
                basket_post_trigger_move += alloc * ((p_now - p_start) / p_start)
        basket_live_ret = inp["triggered_at_return"] + basket_post_trigger_move * 100.0
        basket_saved_pct = round(inp["triggered_at_return"] - basket_live_ret, 2)

        bot_state = _build_bot_state(case)
        live_prices = inp["live_prices"]

        date_str = "2026-06-22-antireg"
        report_file = tmp_path / f"post_mortem_{date_str}.json"

        monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
        patch_norm, patch_strat = _db_stubs()

        with patch_norm, patch_strat:
            reporting.generate_eod_snapshot(
                bot_state,
                date_str,
                is_post_rebalance=False,
                discord_webhook_url=None,
                live_prices=live_prices,
            )

        assert report_file.exists()
        with open(report_file, encoding="utf-8") as fh:
            snapshot = json.load(fh)

        trigger = snapshot["triggers"][0]

        # On the BROKEN producer: trigger["saved_pct_guard_alpha"] == basket_saved_pct (≈0).
        # After the fix: it must differ from the basket result.
        # abs=0.01: the basket result and the correct value differ by at least 1pp in this case.
        assert trigger["saved_pct_guard_alpha"] != pytest.approx(basket_saved_pct, abs=0.01), (
            f"ANTI-REGRESSION: saved_pct_guard_alpha={trigger['saved_pct_guard_alpha']:.4f} "
            f"matches the basket reconstruction result ({basket_saved_pct:.4f}), which is the BUG. "
            f"The correct if-held source is current_return={inp['shadow_history_current_return']}, "
            f"not the basket reconstruction."
        )


# ===========================================================================
# 4. Blast-radius: analytics.get_history_summary aggregates from post-mortem
#    files. Once the producer writes correct values, the aggregation reflects them.
#    This test verifies the aggregation reads saved_dollars from the file
#    (no separate aggregation bug on top of the producer bug).
# ===========================================================================


class TestHistoryAggregationReadsProducerValues:
    """
    The analytics history aggregation sums saved_dollars from post-mortem JSON
    (analytics.py:1616-1620). It carries the producer defect. This test verifies
    that once the producer writes the correct value, the aggregation reflects it.

    We write a synthetic post-mortem file with a known saved_dollars value and
    verify the aggregation returns a matching total.

    This test pins the aggregation contract — it will PASS today (the aggregation
    reads whatever the producer writes), so it's a regression anchor for blast-radius.
    It will catch a future aggregation bug that introduces an independent error.
    """

    def test_history_aggregation_sums_saved_dollars_from_post_mortem_files(self, tmp_path):
        """
        analytics.get_history_summary(days, base_dir) sums saved_dollars across
        triggers in post-mortem files. We seed two files with known saved_dollars
        values and assert the aggregation sum is correct. This pins the aggregation
        contract independent of the producer math.

        Expected total_saved is derived from the seeded values, not hardcoded from
        a producer run (the seeded values ARE the fixture inputs to the aggregation).

        Use dates within the last 7 days so days=30 covers them.
        """
        from datetime import date, timedelta

        import analytics

        # Derive saved values from the corrected formula for the first two fixture cases:
        #   case 1: (1.85 - 0.63) / 100 * 1020.0 = 12.444
        #   case 2: (2.82 - 0.37) / 100 * 985.0  = 24.1325
        # We round to 2dp as the producer does.
        file_a_saved = round((1.85 - 0.63) / 100 * 1020.0, 2)
        file_b_saved = round((2.82 - 0.37) / 100 * 985.0, 2)
        expected_total = file_a_saved + file_b_saved

        # Use recent dates (within last 7 days) so days=30 covers both files.
        today = date.today()
        for i, saved in enumerate([file_a_saved, file_b_saved]):
            day_str = (today - timedelta(days=i + 1)).isoformat()
            data = {
                "date": day_str,
                "summary": {
                    "total_monitored": 1,
                    "total_triggered": 1,
                    "positive_guard_alpha_count": 1,
                },
                "triggers": [
                    {
                        "symphony_name": f"Sym {i + 1}",
                        "exit_reason": "Trailing Stop",
                        "exit_return": 2.0,
                        "shadow_return": 0.5,
                        "saved_pct_guard_alpha": 1.5,
                        "saved_dollars": saved,
                        "symphony_value": 1000.0,
                        "account_id": "acct-001",
                        "shadow_hwm": 3.0,
                        "hwm_at_trigger": 3.0,
                        "time_triggered": "15:54:00",
                        "symphony_vol": 0.012,
                        "strategy_params": {},
                        "next_day_holdings": [],
                        "attempted_trigger_level": 1.5,
                    }
                ],
            }
            fpath = tmp_path / f"post_mortem_{day_str}.json"
            fpath.write_text(json.dumps(data), encoding="utf-8")

        # get_history_summary accepts base_dir to scope its glob — use tmp_path.
        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(expected_total, abs=0.01), (
            # abs=0.01: cent-precision for sums of at most ~$200; float accumulation
            # error on two two-decimal additions is sub-penny.
            f"Aggregation total_saved={result['total_saved']:.4f} != "
            f"expected {expected_total:.4f}. "
            f"analytics.get_history_summary must faithfully sum saved_dollars from "
            f"post-mortem trigger records."
        )
        assert result["trigger_count"] == 2, (
            f"Expected 2 trigger records across 2 files, got {result['trigger_count']}"
        )


# ===========================================================================
# 5. Discord path not broken: send_eod_discord_post can build its report from
#    a post-mortem file that contains saved_dollars from the corrected producer.
#    The file format is unchanged; this pins the Discord path as a regression guard.
# ===========================================================================


class TestDiscordPathRemainsIntactAfterProducerFix:
    """
    send_eod_discord_post reads saved_dollars from the post-mortem file
    (reporting.py:207). The file schema is unchanged by the fix (same field names,
    same JSON structure). This test verifies Discord builds do not error.

    Regression anchor: PASSES today; must stay GREEN after the producer fix.
    """

    def test_discord_post_builds_without_error_from_fixed_producer_file(
        self, tmp_path, monkeypatch
    ):
        """
        Given a post-mortem file with a realistic saved_dollars value (as the
        corrected producer would write), send_eod_discord_post must not raise
        and must make at least one HTTP call (mocked).
        """
        date_str = "2026-06-22"
        report_file = tmp_path / f"post_mortem_{date_str}.json"

        # A realistic post-mortem file with saved_dollars from the corrected formula.
        # Value: (1.85 - 0.63) / 100 * 1020 = 12.444 → $12.44
        corrected_saved_dollars = round((1.85 - 0.63) / 100 * 1020, 2)

        data = {
            "date": date_str,
            "summary": {
                "total_monitored": 1,
                "total_triggered": 1,
                "positive_guard_alpha_count": 1,
            },
            "tomorrow_target_holdings": {"SPY": 1.0},
            "triggers": [
                {
                    "symphony_name": "Test Symphony",
                    "symphony_value": 1020.0,
                    "account_id": "acct-001",
                    "exit_reason": "Trailing Stop",
                    "exit_return": 1.85,
                    "attempted_trigger_level": 0.85,
                    "shadow_return": 0.63,
                    "shadow_hwm": 2.5,
                    "saved_pct_guard_alpha": round(1.85 - 0.63, 2),
                    "saved_dollars": corrected_saved_dollars,
                    "hwm_at_trigger": 2.5,
                    "time_triggered": "15:54:00",
                    "symphony_vol": 0.012,
                    "strategy_params": {},
                    "next_day_holdings": ["SPY"],
                }
            ],
        }
        report_file.write_text(json.dumps(data), encoding="utf-8")

        monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

        mock_resp = type(
            "R",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"id": "mock-chart-123"},
            },
        )()

        with patch("reporting.requests.post", return_value=mock_resp) as mock_post:
            # Should not raise; reports the file to Discord.
            reporting.send_eod_discord_post(
                date_str,
                str(report_file),
                optimization_results={},
                discord_webhook_url="https://discord.example.invalid/webhook/SENTINEL",
            )

        assert mock_post.called, (
            "send_eod_discord_post did not call requests.post — Discord path appears broken"
        )
