"""
RED -- AC-5/AC-6/AC-7/AC-9 (exit-friction-realized-savings): Stage-2
realized-basis $-saved.

AC-5 (AMENDED 2026-07-24, recon Finding A): Stage-2's new "first
post-rebalance observed value" MUST be sourced from the shadow_history TABLE
(database.load_latest_shadow_row with NO et_cutoff -- "latest of day", the
opposite of Stage-1's snapshot-cutoff query) -- NEVER from bot_state's live
current_return, whose action-phase clobbering is the exact
DE-GUARD-ALPHA-SAVED-001 defect (PR #80). Absent shadow_history data -> the
new fields are ABSENT, never fabricated, never substituted with a bot_state
fallback (Stage-2 has no fallback tier per this ruling).

AC-6: saved_dollars_realized computed the same formula shape as the existing
snapshot-basis saved_dollars, using the realized observed value in place of
the Stage-1 if-held value.

AC-7: absent realized data is excluded from any aggregate, never
substituted with the snapshot-basis value (aggregate-level coverage
accounting is tested at the route layer -- tests/app/).

AC-9: every new field is additive -- an old-format post-mortem file (no
realized fields) must still parse cleanly through Stage 2 without those
fields appearing (backward compat is exercised at the route/analytics
consumer layer -- tests/app/; this file only pins the PRODUCER side: fields
absent from the trigger dict entirely when data is unavailable, not `null`).

Fixture provenance (captured-from-producer, house rule): reuses
tests/fixtures/prod_droplet/{bot_state_positions,shadow_postclose_rows}.json
-- real droplet rows captured 2026-07-09, deployed SHA 0bcbd1a. Every
expected value is recomputed from those rows at test runtime, never
hardcoded.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import database
import reporting

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "prod_droplet"

_DAY = "2026-06-23"


@pytest.fixture(scope="module")
def positions() -> dict:
    return json.loads((_FIXTURE_DIR / "bot_state_positions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def postclose_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "shadow_postclose_rows.json").read_text(encoding="utf-8"))


def _symphonies_triggered_on(positions: dict, day: str, postclose_rows: list[dict]) -> list[str]:
    """sym_ids that have >=1 post-close row on `day` (the "realized data
    available" population for this fixture set)."""
    ids = {r["symphony_id"] for r in postclose_rows if r["trading_day"] == day}
    return sorted(s for s in ids if s in positions)


def _latest_postclose_row(postclose_rows: list[dict], sym_id: str, day: str) -> dict:
    rows = [r for r in postclose_rows if r["symphony_id"] == sym_id and r["trading_day"] == day]
    assert rows, f"fixture integrity: no post-close rows for ({sym_id}, {day})"
    return max(rows, key=lambda r: r["ts_utc"])


def _bot_state_triggered(positions: dict, sym_ids: list[str], *, clobbered_return: float) -> dict:
    """A triggered bot_state where current_return is DELIBERATELY set to a
    value that DIFFERS from the shadow_history truth -- proving the producer
    reads the table, not this field, exactly mirroring the Stage-1 test's
    technique for the same defect class.
    """
    state = {}
    for sym_id in sym_ids:
        pos = positions[sym_id]
        state[sym_id] = {
            "triggered": True,
            "triggered_reason": "Trailing Stop",
            "triggered_at_return": -1.0,
            "triggered_at_stop": -0.5,
            "triggered_at_hwm": pos.get("shadow_hwm", 0.0),
            "triggered_at_time": "14:00:00",
            "current_return": clobbered_return,  # deliberately wrong
            "current_value": pos["current_value"],
            "name": pos["name"],
            "account": pos["account"],
            "shadow_hwm": pos.get("shadow_hwm", 0.0),
            "symphony_vol": pos.get("symphony_vol", 0.0),
            "current_holdings": pos.get("current_holdings", []),
        }
    return state


def _seed_postclose_rows(postclose_rows: list[dict], day: str) -> None:
    for r in postclose_rows:
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


def _run_two_stage(bot_state: dict, day: str, tmp_path, monkeypatch) -> dict:
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
    reporting.generate_eod_snapshot(bot_state, day, is_post_rebalance=False)
    reporting.generate_eod_snapshot(bot_state, day, is_post_rebalance=True)
    report_file = tmp_path / f"post_mortem_{day}.json"
    assert report_file.exists(), "post-mortem file was not created"
    return json.loads(report_file.read_text(encoding="utf-8"))


def _booked(report: dict, name: str) -> dict:
    matches = [t for t in report["triggers"] if t["symphony_name"] == name]
    assert len(matches) == 1, f"expected exactly one booked trigger for {name!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# AC-5: realized_observed_return sourced from shadow_history, not bot_state
# ---------------------------------------------------------------------------


class TestRealizedObservedReturnSourcedFromShadowHistory:
    def test_realized_observed_return_matches_latest_postclose_row_not_bot_state(
        self, positions, postclose_rows, tmp_path, monkeypatch
    ):
        sym_ids = _symphonies_triggered_on(positions, _DAY, postclose_rows)
        assert sym_ids, "fixture integrity: no post-close-covered symphonies for the audit day"

        # Deliberately wrong bot_state.current_return -- must NOT leak into
        # realized_observed_return (the DE-GUARD-ALPHA-SAVED-001 defect class).
        CLOBBERED_SENTINEL = -9999.0
        bot_state = _bot_state_triggered(positions, sym_ids, clobbered_return=CLOBBERED_SENTINEL)
        _seed_postclose_rows(postclose_rows, _DAY)

        report = _run_two_stage(bot_state, _DAY, tmp_path, monkeypatch)

        mismatches = []
        for sym_id in sym_ids:
            name = positions[sym_id]["name"]
            booked = _booked(report, name)
            expected_row = _latest_postclose_row(postclose_rows, sym_id, _DAY)
            expected = expected_row["current_return"]

            got = booked.get("realized_observed_return")
            if got is None or got == pytest.approx(CLOBBERED_SENTINEL, abs=1.0):
                mismatches.append(
                    f"{name[:40]!r}: realized_observed_return={got!r} -- expected "
                    f"latest shadow_history.current_return={expected} "
                    f"(clobbered bot_state sentinel was {CLOBBERED_SENTINEL})"
                )
            elif got != pytest.approx(expected, abs=0.011):
                mismatches.append(
                    f"{name[:40]!r}: realized_observed_return={got} != expected {expected} "
                    f"(latest post-close shadow_history row)"
                )

        assert not mismatches, (
            "realized_observed_return not sourced from the LATEST shadow_history "
            "row -- either missing, or leaking the clobbered bot_state value:\n"
            + "\n".join(mismatches)
        )

    def test_realized_source_field_is_shadow_history_when_row_found(
        self, positions, postclose_rows, tmp_path, monkeypatch
    ):
        sym_ids = _symphonies_triggered_on(positions, _DAY, postclose_rows)
        bot_state = _bot_state_triggered(positions, sym_ids, clobbered_return=0.0)
        _seed_postclose_rows(postclose_rows, _DAY)

        report = _run_two_stage(bot_state, _DAY, tmp_path, monkeypatch)

        for sym_id in sym_ids:
            name = positions[sym_id]["name"]
            booked = _booked(report, name)
            assert booked.get("realized_source") == "shadow_history", (
                f"{name[:40]!r}: realized_source must be 'shadow_history' when a "
                f"post-close row was found, got {booked.get('realized_source')!r} "
                "(AC-5 provenance stamp)."
            )

    def test_realized_fields_absent_never_null_when_no_shadow_row_exists(
        self, positions, tmp_path, monkeypatch
    ):
        """AC-5/AC-7: a triggered symphony with ZERO shadow_history rows for the
        day must have realized_observed_return / realized_source / saved_dollars_realized
        entirely ABSENT from its trigger dict -- never null, never fabricated,
        never a bot_state fallback (Stage 2 has no fallback tier, unlike Stage 1).
        """
        sym_id = next(iter(positions))
        bot_state = _bot_state_triggered(positions, [sym_id], clobbered_return=1.23)
        # Deliberately do NOT seed any shadow_history rows.

        report = _run_two_stage(bot_state, _DAY, tmp_path, monkeypatch)
        booked = _booked(report, positions[sym_id]["name"])

        assert "realized_observed_return" not in booked, (
            "realized_observed_return must be ABSENT (not null/fabricated) when "
            f"no shadow_history row exists. Got: {booked.get('realized_observed_return')!r}"
        )
        assert "realized_source" not in booked, (
            f"realized_source must be ABSENT when no row was found. "
            f"Got: {booked.get('realized_source')!r}"
        )
        assert "saved_dollars_realized" not in booked, (
            "saved_dollars_realized must be ABSENT (AC-7 -- never substituted "
            f"with the snapshot value). Got: {booked.get('saved_dollars_realized')!r}"
        )


# ---------------------------------------------------------------------------
# AC-6: saved_dollars_realized computation
# ---------------------------------------------------------------------------


class TestSavedDollarsRealizedComputation:
    def test_saved_dollars_realized_matches_snapshot_basis_formula_shape(
        self, positions, postclose_rows, tmp_path, monkeypatch
    ):
        """saved_dollars_realized = symphony_value * (exit_return - realized_observed_return) / 100,
        the SAME formula shape as the existing saved_dollars (snapshot basis)
        with realized_observed_return substituted for the Stage-1 if-held value.
        """
        sym_ids = _symphonies_triggered_on(positions, _DAY, postclose_rows)
        EXIT_RETURN = -1.0  # matches _bot_state_triggered's triggered_at_return
        bot_state = _bot_state_triggered(positions, sym_ids, clobbered_return=0.0)
        _seed_postclose_rows(postclose_rows, _DAY)

        report = _run_two_stage(bot_state, _DAY, tmp_path, monkeypatch)

        mismatches = []
        for sym_id in sym_ids:
            name = positions[sym_id]["name"]
            booked = _booked(report, name)
            expected_row = _latest_postclose_row(postclose_rows, sym_id, _DAY)
            realized_ret = expected_row["current_return"]
            sym_value = positions[sym_id]["current_value"]
            expected_dollars = sym_value * ((EXIT_RETURN - realized_ret) / 100.0)

            got = booked.get("saved_dollars_realized")
            if got is None or got != pytest.approx(expected_dollars, abs=0.02):
                mismatches.append(
                    f"{name[:40]!r}: saved_dollars_realized={got} != expected "
                    f"{expected_dollars:.2f} (value {sym_value} * "
                    f"(exit {EXIT_RETURN} - realized {realized_ret}) / 100)"
                )

        assert not mismatches, "\n".join(mismatches)


# ---------------------------------------------------------------------------
# Multi-trigger-same-day edge case (plan Edge Cases section)
# ---------------------------------------------------------------------------


def test_multiple_triggers_same_day_each_gets_its_own_realized_value(
    positions, postclose_rows, tmp_path, monkeypatch
):
    """Plan Edge Case: multiple triggers same symphony/day -> per-event friction
    application; realized matching uses the event, not just the date. This
    fixture has multiple DISTINCT symphonies triggering on the same day
    (2026-06-23) -- each must independently resolve its OWN latest shadow row,
    not a shared/cross-contaminated value.
    """
    sym_ids = _symphonies_triggered_on(positions, _DAY, postclose_rows)
    assert len(sym_ids) >= 2, "fixture integrity: need >=2 same-day triggers for this test"
    bot_state = _bot_state_triggered(positions, sym_ids, clobbered_return=0.0)
    _seed_postclose_rows(postclose_rows, _DAY)

    report = _run_two_stage(bot_state, _DAY, tmp_path, monkeypatch)

    seen_values = set()
    for sym_id in sym_ids:
        name = positions[sym_id]["name"]
        booked = _booked(report, name)
        expected = _latest_postclose_row(postclose_rows, sym_id, _DAY)["current_return"]
        assert booked.get("realized_observed_return") == pytest.approx(expected, abs=0.011), (
            f"{name[:40]!r} resolved the wrong per-symphony realized value."
        )
        seen_values.add(round(booked["realized_observed_return"], 2))

    # Not a hard requirement that all values differ (real data could
    # coincide), but the fixture's real captured rows are independent
    # per-symphony draws -- sanity-check we aren't collapsing onto one value.
    assert len(seen_values) > 1, (
        "fixture integrity/regression signal: all resolved realized values "
        "collapsed to one number -- likely cross-contamination between "
        "symphonies' shadow_history lookups."
    )
