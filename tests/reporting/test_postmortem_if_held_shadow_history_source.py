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
     bot_state.current_return (row-less fallback).
  3. Every trigger entry declares its provenance in an ``if_held_source`` field
     ("shadow_history" | "bot_state_fallback") so a silent degradation is
     impossible — the marker semantics agreed with pf-eng (risk-engine) before
     GREEN.
  4. The sourced row honours the SNAPSHOT-BASIS CUTOFF (ET time-of-day <=
     15:54:59 — the Stage-1 schedule, and the constant the regeneration script
     declares load-bearing): an off-schedule run (manual regeneration, late
     daemon) must not silently drift to an EOD basis when post-close rows
     (the engine ticks to ~16:04) exist. Reviewer finding B, adopted pre-GREEN
     so producer and repair tool share one basis everywhere.

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
def postclose_rows() -> list[dict]:
    """Real rows AFTER the 15:54:59 ET cutoff (engine ticks to ~16:04) — present
    in the DB only when Stage-1 runs off-schedule."""
    return json.loads((_FIXTURE_DIR / "shadow_postclose_rows.json").read_text(encoding="utf-8"))


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
# 2. Provenance marker — no silent degradation.
# ---------------------------------------------------------------------------


class TestIfHeldProvenanceMarker:
    def test_trigger_entries_declare_shadow_history_provenance(
        self, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """With shadow rows present, every booked trigger entry must carry
        if_held_source == "shadow_history" — the operator-auditable proof of which
        source produced the money math (the #80 regression was invisible precisely
        because provenance lived only in a comment).
        """
        day = "2026-06-24"
        cases = _cases(day, positions, snapshot_rows)
        _seed_shadow(snapshot_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        for c in cases:
            booked = _booked(report, c["pm"]["symphony_name"])
            assert booked.get("if_held_source") == "shadow_history", (
                f"{c['pm']['symphony_name'][:40]!r}: trigger entry must declare "
                f"if_held_source='shadow_history'; got {booked.get('if_held_source')!r}"
            )


# ---------------------------------------------------------------------------
# 3. Snapshot-basis cutoff — off-schedule runs must not drift to an EOD basis.
# ---------------------------------------------------------------------------


class TestSnapshotBasisCutoff:
    def test_off_schedule_run_books_the_snapshot_basis_not_eod(
        self, positions, snapshot_rows, postclose_rows, tmp_path, monkeypatch
    ):
        """With post-cutoff rows present (the DB state of any run AFTER ~16:04 —
        manual regeneration, late daemon), the booked if-held must still be the
        latest row at/before the 15:54:59 ET cutoff, not the newest row outright.

        The panel's label declares a "snapshot-time basis" and the regeneration
        script hard-codes the same cutoff as load-bearing; a bare
        latest-row-per-day read silently re-bases those runs to EOD — on the
        captured 2026-06-24 data that moves the LQD if-held from -0.67 (15:52)
        to -0.15 (16:04), shifting saved by ~$6 per position.

        Decisive cases derived from the fixture: pairs whose EOD row diverges
        from the snapshot row by > 0.05 (indistinguishable at 2dp below that).
        """
        day = "2026-06-24"
        cases = _cases(day, positions, snapshot_rows)

        def _eod_return(sym_id: str) -> float | None:
            rows = [
                r for r in postclose_rows if r["symphony_id"] == sym_id and r["trading_day"] == day
            ]
            return max(rows, key=lambda r: r["ts_utc"])["current_return"] if rows else None

        decisive = [
            c
            for c in cases
            if _eod_return(c["sym_id"]) is not None
            and abs(_eod_return(c["sym_id"]) - c["shadow_truth"]) > 0.05
        ]
        assert decisive, (
            "fixture integrity: the audit day must contain a pair whose EOD row "
            "diverges from the 15:54 snapshot row"
        )

        _seed_shadow(snapshot_rows, day)
        _seed_shadow(postclose_rows, day)
        report = _run_stage1(_bot_state_for(cases, positions), day, tmp_path, monkeypatch)

        for c in decisive:
            booked = _booked(report, c["pm"]["symphony_name"])
            # abs=0.011: the producer books round(if_held, 2).
            assert booked["shadow_return"] == pytest.approx(c["shadow_truth"], abs=0.011), (
                f"{c['pm']['symphony_name'][:40]!r}: booked if-held "
                f"{booked['shadow_return']} drifted off the snapshot basis "
                f"{c['shadow_truth']:.2f} — an off-schedule run silently re-based "
                f"to the EOD row {_eod_return(c['sym_id']):.2f}"
            )
            assert booked["saved_dollars"] == pytest.approx(c["expected_dollars"], abs=0.02), (
                f"{c['pm']['symphony_name'][:40]!r}: saved_dollars "
                f"{booked['saved_dollars']:.2f} not derived from the snapshot-basis "
                f"if-held (expected {c['expected_dollars']:.2f})"
            )

    def test_all_post_cutoff_day_books_earliest_shadow_row_not_bot_state(
        self, positions, snapshot_rows, postclose_rows, tmp_path, monkeypatch
    ):
        """pf-eng's flagged corner, ruled: a day whose shadow rows are ALL after
        the cutoff (daemon started after 15:55) must book the EARLIEST post-cutoff
        row — the row nearest the declared basis — under the distinct honest
        marker "shadow_history_post_cutoff". Real off-basis shadow data beats the
        clobbered bot_state value; "row-less fallback" means NO rows for the day
        at all, not "no rows on basis".

        (Producer degrades honestly; the repair script may still refuse such
        days — a repair tool can afford strictness the nightly producer cannot.)
        """
        day = "2026-06-24"
        cases = _cases(day, positions, snapshot_rows)

        def _earliest_postclose(sym_id: str) -> float | None:
            rows = [
                r for r in postclose_rows if r["symphony_id"] == sym_id and r["trading_day"] == day
            ]
            return min(rows, key=lambda r: r["ts_utc"])["current_return"] if rows else None

        covered = [c for c in cases if _earliest_postclose(c["sym_id"]) is not None]
        decisive = [
            c
            for c in covered
            if abs(_earliest_postclose(c["sym_id"]) - c["clobbered_if_held"]) > 0.05
        ]
        assert decisive, (
            "fixture integrity: need a case where the post-cutoff shadow row is "
            "distinguishable from the clobbered bot_state value"
        )

        # Seed ONLY the post-cutoff rows: no row qualifies at/before the cutoff.
        _seed_shadow(postclose_rows, day)
        report = _run_stage1(_bot_state_for(covered, positions), day, tmp_path, monkeypatch)

        for c in decisive:
            booked = _booked(report, c["pm"]["symphony_name"])
            truth = _earliest_postclose(c["sym_id"])
            expected_dollars = c["pm"]["symphony_value"] * (c["pm"]["exit_return"] - truth) / 100.0
            # abs=0.011 / 0.02: producer 2dp rounding, as elsewhere in this file.
            assert booked["shadow_return"] == pytest.approx(truth, abs=0.011), (
                f"{c['pm']['symphony_name'][:40]!r}: booked if-held "
                f"{booked['shadow_return']} is not the earliest post-cutoff shadow row "
                f"{truth:.2f} — the clobbered bot_state value "
                f"{c['clobbered_if_held']} was booked despite real shadow data existing"
            )
            assert booked["saved_dollars"] == pytest.approx(expected_dollars, abs=0.02)
            assert booked.get("if_held_source") == "shadow_history_post_cutoff", (
                f"off-basis booking must be declared: got {booked.get('if_held_source')!r}"
            )

    def test_producer_and_regen_script_share_one_cutoff_constant(self):
        """Drift guard (pf-review): reporting.STAGE1_SNAPSHOT_CUTOFF_ET and the
        regeneration script's SNAPSHOT_CUTOFF_ET must be equal forever. The
        script deliberately stays import-free (standalone droplet use), so the
        invariant is pinned here instead of by a shared import — AST-extracted
        so a comment or string cannot satisfy it.
        """
        import ast as _ast

        script = Path(__file__).parent.parent.parent / "scripts" / "regenerate_post_mortems.py"
        tree = _ast.parse(script.read_text(encoding="utf-8"))
        script_values = [
            node.value.value
            for node in _ast.walk(tree)
            if isinstance(node, _ast.Assign)
            and isinstance(node.value, _ast.Constant)
            and any(isinstance(t, _ast.Name) and t.id == "SNAPSHOT_CUTOFF_ET" for t in node.targets)
        ]
        assert len(script_values) == 1, (
            "regenerate_post_mortems.py must define SNAPSHOT_CUTOFF_ET exactly once"
        )
        assert getattr(reporting, "STAGE1_SNAPSHOT_CUTOFF_ET", None) == script_values[0], (
            f"producer cutoff {getattr(reporting, 'STAGE1_SNAPSHOT_CUTOFF_ET', None)!r} != "
            f"regen script cutoff {script_values[0]!r} — the two bases must never diverge"
        )


# ---------------------------------------------------------------------------
# 4. Row-less degradation — the ONLY case bot_state may be trusted.
# ---------------------------------------------------------------------------


class TestRowlessFallbackDegradation:
    def test_stage1_degrades_to_bot_state_only_when_no_shadow_row_exists(
        self, positions, snapshot_rows, tmp_path, monkeypatch
    ):
        """With NO shadow_history row for the (symphony, day), Stage-1 falls back
        to bot_state.current_return (there is no better source) and DECLARES it via
        if_held_source == "bot_state_fallback". Pins the degradation so the fix can
        neither silently zero out row-less symphonies (no swallow-to-zero) nor
        degrade without a trace.
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
        assert booked.get("if_held_source") == "bot_state_fallback", (
            "the row-less degradation must be declared via "
            f"if_held_source='bot_state_fallback'; got {booked.get('if_held_source')!r}"
        )
