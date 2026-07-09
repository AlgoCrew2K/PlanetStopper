"""RED — Stage-1 post-mortem must source if-held from the shadow_history TABLE.

THE BUG (VERDICT-droplet.md Finding 2, HIGH — 7 of 11 audited days sign-flipped):
  reporting.py Stage-1 reads ``sym.get("current_return")`` from bot_state. Its own
  comment claims "Source if-held from shadow_history.current_return" (the #80 fix,
  DE-GUARD-ALPHA-SAVED-001) but the code never queries the table. In production the
  action phase clobbers ``bot_state[...]["current_return"]`` every cycle with the
  frozen-basket reconstruction (alpha_bot_execution.py:1189-1203 -> :1548) — the
  same reconstruction the a7601fb diagnosis discredited. When the basket lookup
  misses, the clobber collapses to exactly ``triggered_at_return`` and $-saved books
  exactly $0.00 despite real divergence (both 2026-06-24 LQD entries). When it
  produces a fabricated value, whole days sign-flip (2026-06-23 Golden Age booked
  +$5.69, truth ~ -$13.79; Reasonabilists booked -$4.17, truth ~ +$2.86).

WHY THE #80 TEST MISSED IT (tests/reporting/test_postmortem_saved_dollars_source.py):
  it hydrates bot_state["current_return"] with the shadow-truth value itself, on the
  assumption "the caller hydrates it from shadow_history before the call". Production
  violates that assumption every cycle. These tests seed the CLOBBERED value into
  bot_state and the truth into the shadow_history table — the producer must read the
  table to pass.

CONTRACT PINNED:
  1. For a triggered symphony whose (symphony_id, trading_day) has shadow_history
     rows, Stage-1 books if-held from the LATEST such row's current_return —
     bot_state's current_return must not be trusted.
  2. Only when NO shadow row exists for the (symphony, day) may Stage-1 degrade to
     bot_state.current_return (row-less fallback — pinned as a regression anchor).

FIXTURE PROVENANCE (captured-from-producer):
  tests/fixtures/prod_droplet/ — real droplet data, deployed SHA 0bcbd1a, captured
  2026-07-09 (see capture_from_droplet.py). The clobbered inputs are the real
  ``shadow_return`` values booked in the production post-mortem files; the truth is
  the real shadow_history rows at/before the 15:54 ET Stage-1 snapshot. Every
  expected value is recomputed from those rows at test runtime — never hardcoded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import database
import reporting

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "prod_droplet"

# The two production days the money-math audit re-verified raw (Finding 2).
_AUDIT_DAYS = ("2026-06-23", "2026-06-24")


# ---------------------------------------------------------------------------
# Fixture loading + case derivation (all values flow from the captured files)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def snapshot_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "shadow_snapshot_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def positions() -> dict:
    return json.loads((_FIXTURE_DIR / "bot_state_positions.json").read_text(encoding="utf-8"))


def _pm_triggers(day: str) -> list[dict]:
    pm = json.loads(
        (_FIXTURE_DIR / "post_mortems" / f"post_mortem_{day}.json").read_text(encoding="utf-8")
    )
    return pm["triggers"]


def _name_to_id(positions: dict) -> dict[str, str]:
    return {entry["name"]: sym_id for sym_id, entry in positions.items()}


def _latest_snapshot_return(snapshot_rows: list[dict], sym_id: str, day: str) -> float:
    """The shadow-truth if-held: latest captured row at/before the Stage-1 snapshot."""
    rows = [r for r in snapshot_rows if r["symphony_id"] == sym_id and r["trading_day"] == day]
    assert rows, f"fixture integrity: no snapshot rows for ({sym_id}, {day})"
    return max(rows, key=lambda r: r["ts_utc"])["current_return"]


def _cases(day: str, positions: dict, snapshot_rows: list[dict]) -> list[dict]:
    """One case per production trigger entry on `day`, joining PM entry to its
    symphony_id and to the shadow-truth value.

    clobbered_if_held is the REAL value production booked (pm entry
    ``shadow_return`` == bot_state.current_return at generation time).
    """
    by_name = _name_to_id(positions)
    cases = []
    for entry in _pm_triggers(day):
        sym_id = by_name.get(entry["symphony_name"])
        assert sym_id is not None, (
            f"fixture integrity: PM name {entry['symphony_name']!r} not in bot_state positions"
        )
        truth = _latest_snapshot_return(snapshot_rows, sym_id, day)
        cases.append(
            {
                "sym_id": sym_id,
                "pm": entry,
                "clobbered_if_held": entry["shadow_return"],
                "shadow_truth": truth,
                "expected_pct": entry["exit_return"] - truth,
                "expected_dollars": entry["symphony_value"]
                * (entry["exit_return"] - truth)
                / 100.0,
            }
        )
    return cases


def _bot_state_for(cases: list[dict], positions: dict) -> dict:
    """bot_state as the action phase leaves it at 15:54: current_return CLOBBERED."""
    state = {}
    for c in cases:
        pm = c["pm"]
        state[c["sym_id"]] = {
            "triggered": True,
            "triggered_reason": pm["exit_reason"],
            "triggered_at_return": pm["exit_return"],
            "triggered_at_stop": pm["attempted_trigger_level"],
            "triggered_at_hwm": pm["hwm_at_trigger"],
            "triggered_at_time": pm["time_triggered"],
            # The production defect input: the frozen-basket override's value,
            # exactly as the droplet booked it.
            "current_return": c["clobbered_if_held"],
            "current_value": pm["symphony_value"],
            "name": pm["symphony_name"],
            "account": pm["account_id"],
            "shadow_hwm": pm["shadow_hwm"],
            "symphony_vol": pm["symphony_vol"],
        }
    return state


def _seed_shadow(snapshot_rows: list[dict], day: str) -> None:
    for r in snapshot_rows:
        if r["trading_day"] != day:
            continue
        database.record_shadow_observation(
            symphony_id=r["symphony_id"],
            account_id=r["account_id"],
            cycle_id=r["cycle_id"],
            ts_utc=r["ts_utc"],
            ts_et=r["ts_et"],
            trading_day=r["trading_day"],
            current_return=r["current_return"],
            shadow_return=r["shadow_return"],
            is_post_trigger=r["is_post_trigger"],
            trigger_id=None,
            position_epoch=r["position_epoch"],
        )


def _run_stage1(bot_state: dict, day: str, tmp_path, monkeypatch) -> dict:
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
    reporting.generate_eod_snapshot(bot_state, day, is_post_rebalance=False)
    report_file = tmp_path / f"post_mortem_{day}.json"
    assert report_file.exists(), "Stage-1 snapshot was not created"
    return json.loads(report_file.read_text(encoding="utf-8"))


def _booked(report: dict, name: str) -> dict:
    matches = [t for t in report["triggers"] if t["symphony_name"] == name]
    assert len(matches) == 1, f"expected one booked trigger for {name!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# 1. The money math: saved_dollars derives from the shadow_history table.
# ---------------------------------------------------------------------------


class TestSavedDollarsSourcedFromShadowHistoryTable:
    @pytest.mark.parametrize("day", _AUDIT_DAYS)
    def test_saved_dollars_uses_latest_shadow_row_not_clobbered_bot_state(
        self, day, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """For every real trigger on the audit day: booked saved_dollars must equal
        value * (exit_return - shadow_truth) / 100 where shadow_truth is the latest
        shadow_history.current_return row — NOT the clobbered bot_state value.

        RED today: the producer reads bot_state.current_return, booking the two
        2026-06-24 LQD entries as exactly $0.00 (collapse) and sign-flipping
        2026-06-23 Golden Age / Reasonabilists.

        Tolerance abs=0.02: the producer rounds saved_dollars to 2dp and its pct
        inputs to 2dp; worst-case rounding on positions < $2k is under two cents.
        """
        cases = _cases(day, positions, snapshot_rows)
        _seed_shadow(snapshot_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        mismatches = []
        for c in cases:
            booked = _booked(report, c["pm"]["symphony_name"])
            if booked["saved_dollars"] != pytest.approx(c["expected_dollars"], abs=0.02):
                mismatches.append(
                    f"{c['pm']['symphony_name'][:40]!r}: booked ${booked['saved_dollars']:.2f} "
                    f"!= expected ${c['expected_dollars']:.2f} "
                    f"(exit {c['pm']['exit_return']}, shadow-truth {c['shadow_truth']}, "
                    f"clobbered bot_state value {c['clobbered_if_held']})"
                )
        assert not mismatches, (
            "saved_dollars not sourced from shadow_history — the clobbered bot_state "
            "value leaked into the money math:\n" + "\n".join(mismatches)
        )

    @pytest.mark.parametrize("day", _AUDIT_DAYS)
    def test_booked_if_held_matches_shadow_row_where_clobber_diverges(
        self, day, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """The booked ``shadow_return`` field (the if-held the operator sees) must
        match the shadow_history row wherever the clobbered value diverges from it.

        Guard condition |truth - clobber| > 0.05 keeps the assertion decisive:
        below that, truth and clobber are indistinguishable at 2dp booking.
        """
        cases = _cases(day, positions, snapshot_rows)
        decisive = [c for c in cases if abs(c["shadow_truth"] - c["clobbered_if_held"]) > 0.05]
        assert decisive, (
            f"fixture integrity: {day} must contain at least one diverging case "
            "(the audit found several)"
        )
        _seed_shadow(snapshot_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        for c in decisive:
            booked = _booked(report, c["pm"]["symphony_name"])
            # abs=0.011: the producer books round(if_held, 2).
            assert booked["shadow_return"] == pytest.approx(c["shadow_truth"], abs=0.011), (
                f"{c['pm']['symphony_name'][:40]!r}: booked if-held "
                f"{booked['shadow_return']} is not the shadow_history truth "
                f"{c['shadow_truth']:.2f} — it matches the clobbered bot_state value "
                f"{c['clobbered_if_held']} instead"
            )

    def test_sign_flipped_entries_book_the_true_sign(
        self, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """The operator-facing damage: entries whose clobbered booking has the WRONG
        SIGN. Derived from the fixture (2026-06-23 carries both directions: Golden
        Age booked positive/truth negative; Reasonabilists booked negative/truth
        positive). After the fix, sign(booked) == sign(truth-derived) for each.
        """
        day = "2026-06-23"
        cases = _cases(day, positions, snapshot_rows)
        flipped = [
            c
            for c in cases
            if abs(c["expected_pct"]) > 0.1
            and abs(c["pm"]["exit_return"] - c["clobbered_if_held"]) > 0.1
            and (c["expected_pct"] > 0) != ((c["pm"]["exit_return"] - c["clobbered_if_held"]) > 0)
        ]
        assert len(flipped) >= 2, (
            "fixture integrity: the audit found sign-flips in BOTH directions on "
            f"2026-06-23; derived only {len(flipped)}"
        )
        _seed_shadow(snapshot_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        for c in flipped:
            booked = _booked(report, c["pm"]["symphony_name"])
            assert (booked["saved_dollars"] > 0) == (c["expected_dollars"] > 0), (
                f"SIGN FLIP: {c['pm']['symphony_name'][:40]!r} booked "
                f"${booked['saved_dollars']:.2f} but the shadow-truth saved is "
                f"${c['expected_dollars']:.2f} — the operator sees money saved/lost "
                "on the wrong side of zero"
            )

    @pytest.mark.parametrize("day", _AUDIT_DAYS)
    def test_positive_guard_alpha_count_reflects_shadow_truth(
        self, day, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """summary.positive_guard_alpha_count feeds the History-tab win rate. It
        must count entries whose TRUTH-derived saved_pct is positive — the clobbered
        inputs deflate/inflate it (collapse-to-$0.00 entries never count as wins).
        """
        cases = _cases(day, positions, snapshot_rows)
        expected_positive = sum(1 for c in cases if c["expected_pct"] > 0)
        _seed_shadow(snapshot_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        assert report["summary"]["positive_guard_alpha_count"] == expected_positive, (
            f"win count {report['summary']['positive_guard_alpha_count']} != "
            f"{expected_positive} derived from shadow-truth signs on {day}"
        )


# ---------------------------------------------------------------------------
# 2. Row-less degradation — the ONLY case bot_state may be trusted.
# ---------------------------------------------------------------------------


class TestRowlessFallbackDegradation:
    def test_stage1_degrades_to_bot_state_only_when_no_shadow_row_exists(
        self, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """With NO shadow_history row for the (symphony, day), Stage-1 may fall back
        to bot_state.current_return (there is no better source). Pins the degradation
        so the fix cannot silently zero out row-less symphonies (no swallow-to-zero).

        Regression anchor: PASSES today (current code always reads bot_state);
        must STAY green after the fix.
        """
        day = "2026-06-24"
        case = _cases(day, positions, snapshot_rows)[0]
        # Intentionally NO _seed_shadow: the table has no rows for this day.
        report = _run_stage1(_bot_state_for([case], positions), day, tmp_path, monkeypatch)

        booked = _booked(report, case["pm"]["symphony_name"])
        expected_pct = case["pm"]["exit_return"] - case["clobbered_if_held"]
        expected_dollars = case["pm"]["symphony_value"] * expected_pct / 100.0
        assert booked["saved_dollars"] == pytest.approx(expected_dollars, abs=0.02), (
            "row-less fallback must book from bot_state.current_return, not zero"
        )
