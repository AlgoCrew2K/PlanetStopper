"""The stored run record and the entry contract every consumer relies on.

Module under test: advisors.tenure_review (run_record and build_review).

The record is written with json.dumps defaults by database.insert_advisor_observation,
which emits NaN and Infinity tokens (invalid strict JSON) unless the record maps every
non-finite float to None first. The entry contract: a number is always a figure
(value, unit, and a reason when the value is None), and every item states its status,
label, window, subject and source.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

INVALID_NOTE = "configured threshold invalid; 126 in use"
COMPLETED_AT = b.et(2026, 12, 31, 17, 12, 30)
ENTRY_KEYS = {
    "symphony_id",
    "display_name",
    "invested_since",
    "tenure_trading_days",
    "eligibility_text",
    "eligible",
    "days_remaining",
    "basis",
    "verdict",
    "verdict_text",
    "verdict_reasons",
    "missing_items",
    "evidence",
}
RECORD_KEYS = {
    "run_id",
    "trigger",
    "started_at",
    "completed_at",
    "as_of_trading_day",
    "threshold_in_effect",
    "threshold_note",
    "composite_window",
    "entries",
}
ITEM_KEYS = {"status", "reason", "label", "window", "subject", "source", "figures"}

DAYS = b.last_n_nyse_days(b.AS_OF, 21)


def _roster():
    """A mixed roster: scored, fallback basis, no history, young, and no start date."""
    return [
        b.composer_symphony("scored-1", "Scored One", DAYS, b.sine_returns(20, 1)),
        b.composer_symphony("scored-2", "Scored Two", DAYS, b.sine_returns(20, 2, drift=0.1)),
        b.make_symphony(
            "fallback-3",
            "Fallback Three",
            DAYS[0].isoformat(),
            series_failure="rate_limited",
            shadow_returns=b.by_date(DAYS[1:], b.sine_returns(20, 3)),
        ),
        b.make_symphony("nothing-4", "No History Four", DAYS[0].isoformat()),
        b.composer_symphony("young-5", "Young Five", DAYS[-6:], b.sine_returns(5, 1)),
        b.make_symphony("undated-6", "Undated Six", None),
    ]


def _result():
    return b.review(_roster(), as_of=b.AS_OF, threshold=20)


# ---------------------------------------------------------------------------
# Record shape and values
# ---------------------------------------------------------------------------


def test_role_constant_names_the_run_row():
    assert tr.TENURE_REVIEW_RUN_ROLE == "TENURE_REVIEW_RUN"


def test_the_record_carries_the_run_identity_times_and_threshold():
    result = _result()
    rec = tr.run_record(result, COMPLETED_AT)
    assert set(rec) >= RECORD_KEYS
    assert rec["run_id"] == b.RUN_ID
    assert rec["trigger"] == "nightly"
    assert rec["as_of_trading_day"] == "2026-12-31"
    assert rec["threshold_in_effect"] == 20
    assert rec["threshold_note"] is None
    assert datetime.fromisoformat(rec["started_at"]) == b.et(2026, 12, 31, 17, 0)
    assert datetime.fromisoformat(rec["completed_at"]) == COMPLETED_AT


def test_the_trigger_is_recorded_as_given():
    inputs = b.make_inputs(_roster(), as_of=b.AS_OF, threshold=20, trigger="on_demand")
    rec = tr.run_record(tr.build_review(inputs), COMPLETED_AT)
    assert rec["trigger"] == "on_demand"


def test_times_are_iso_strings_with_their_offset_whatever_zone_they_arrive_in():
    result = _result()
    utc_done = datetime(2026, 12, 31, 22, 12, 30, tzinfo=UTC)
    rec = tr.run_record(result, utc_done)
    assert isinstance(rec["completed_at"], str)
    assert datetime.fromisoformat(rec["completed_at"]) == utc_done
    assert datetime.fromisoformat(rec["completed_at"]).utcoffset() is not None


def test_the_composite_window_is_the_threshold_many_sessions_ending_on_the_as_of_day():
    result = _result()
    window = tr.run_record(result, COMPLETED_AT)["composite_window"]
    assert window["last"] == "2026-12-31"
    assert window["days"] == 20
    assert b.oracle_count_trading_days(date.fromisoformat(window["first"]), b.AS_OF) == 20
    assert set(window) == {"first", "last", "days", "sessions"}
    assert window["sessions"] == window["days"]


def test_entries_are_the_reviews_entries_one_per_symphony():
    result = _result()
    rec = tr.run_record(result, COMPLETED_AT)
    assert rec["entries"] == result.entries
    assert sorted(e["symphony_id"] for e in rec["entries"]) == sorted(
        s.symphony_id for s in _roster()
    )


def test_a_review_of_no_symphonies_still_gives_a_complete_record():
    rec = tr.run_record(b.review([], as_of=b.AS_OF, threshold=126), COMPLETED_AT)
    assert set(rec) >= RECORD_KEYS
    assert rec["entries"] == []
    assert rec["composite_window"]["days"] == 126
    json.dumps(rec, allow_nan=False)


# ---------------------------------------------------------------------------
# The threshold note
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value", "note"),
    [
        (None, 126, None),
        ("30", 30, None),
        ("abc", 126, INVALID_NOTE),
        ("12x", 126, INVALID_NOTE),
        ("126.5", 126, INVALID_NOTE),
        ("1e2", 126, INVALID_NOTE),
        ("0", 126, INVALID_NOTE),
        ("-5", 126, INVALID_NOTE),
        ("", 126, INVALID_NOTE),
    ],
)
def test_the_threshold_in_effect_and_its_note_reach_the_record(raw, value, note):
    setting = tr.parse_threshold(raw)
    res = b.review([], as_of=b.AS_OF, threshold=setting)
    rec = tr.run_record(res, COMPLETED_AT)
    assert rec["threshold_in_effect"] == value
    assert rec["threshold_note"] == note


# ---------------------------------------------------------------------------
# Strict JSON
# ---------------------------------------------------------------------------


def test_the_record_of_a_full_review_is_strict_json_and_round_trips():
    result = _result()
    rec = tr.run_record(result, COMPLETED_AT)
    text = json.dumps(rec, allow_nan=False)
    assert json.loads(text) == rec


def test_every_non_finite_float_becomes_none_at_any_depth_and_finite_values_are_untouched():
    res = b.review(_roster(), as_of=b.AS_OF, threshold=20)
    scored = b.entry_of(res, "scored-1")
    figures = scored["evidence"]["performance"]["figures"]
    figures["sharpe"]["value"] = float("nan")
    figures["sortino"]["value"] = float("inf")
    figures["calmar"]["value"] = float("-inf")
    figures["total_return"]["value"] = 0.0
    scored["evidence"]["performance"]["injected"] = [
        1.0,
        float("inf"),
        {"deep": float("nan")},
        (2.5, float("-inf")),
    ]
    scored["verdict_reasons"] = ["kept"]

    rec = tr.run_record(res, COMPLETED_AT)
    out = next(e for e in rec["entries"] if e["symphony_id"] == "scored-1")
    out_figs = out["evidence"]["performance"]["figures"]
    assert out_figs["sharpe"]["value"] is None
    assert out_figs["sortino"]["value"] is None
    assert out_figs["calmar"]["value"] is None
    assert out_figs["total_return"]["value"] == 0.0
    assert out_figs["total_return"]["value"] is not None  # a real zero stays a zero
    injected = out["evidence"]["performance"]["injected"]
    assert injected[0] == 1.0
    assert injected[1] is None
    assert injected[2] == {"deep": None}
    assert list(injected[3]) == [2.5, None]
    assert out["eligible"] is True  # a bool is not turned into a number
    assert out["verdict_reasons"] == ["kept"]
    json.dumps(rec, allow_nan=False)


def test_building_a_record_does_not_modify_the_review():
    res = b.review(_roster(), as_of=b.AS_OF, threshold=20)
    scored = b.entry_of(res, "scored-1")
    scored["evidence"]["performance"]["figures"]["sharpe"]["value"] = float("nan")
    tr.run_record(res, COMPLETED_AT)
    assert math.isnan(scored["evidence"]["performance"]["figures"]["sharpe"]["value"])


# ---------------------------------------------------------------------------
# The entry contract
# ---------------------------------------------------------------------------


def test_every_entry_has_the_documented_fields_and_six_evidence_items():
    result = _result()
    for entry in tr.run_record(result, COMPLETED_AT)["entries"]:
        assert set(entry) >= ENTRY_KEYS, entry["symphony_id"]
        assert set(entry["evidence"]) == set(b.EVIDENCE_ITEMS)
        assert set(entry["basis"]) >= {
            "kind",
            "label",
            "fallback_reason",
            "series_begins",
            "tenure_begins",
        }
        assert entry["verdict"] in (None, "keep", "watch", "retire_candidate")
        assert isinstance(entry["verdict_reasons"], list)
        assert isinstance(entry["missing_items"], list)
        assert type(entry["eligible"]) is bool


def test_every_item_states_its_status_label_window_subject_and_source():
    result = _result()
    for entry in result.entries:
        for name in b.EVIDENCE_ITEMS:
            item = b.item_of(entry, name)
            where = (entry["symphony_id"], name)
            assert set(item) >= ITEM_KEYS, where
            assert item["status"] in ("available", "unavailable", "not_applicable"), where
            if item["status"] == "available":
                assert item["reason"] is None, where
                assert item["label"] and item["subject"] and item["source"], where
            else:
                assert isinstance(item["reason"], str) and item["reason"], where
                for fig in item["figures"].values():
                    assert fig["value"] is None, where


def test_every_number_is_a_figure_with_a_unit_and_never_nan_or_a_bare_zero_for_unknown():
    result = _result()
    count = 0
    for entry in result.entries:
        for fig in b.iter_figures(entry["evidence"]):
            count += 1
            assert isinstance(fig["unit"], str) and fig["unit"], fig
            value = fig["value"]
            assert value is None or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            ), fig
            if value is None:
                assert isinstance(fig.get("reason"), str) and fig["reason"], fig
    assert count > 0


def test_unknown_tenure_is_none_and_zero_is_reserved_for_a_computed_zero():
    result = _result()
    undated = b.entry_of(result, "undated-6")
    assert undated["tenure_trading_days"] is None
    assert undated["invested_since"] is None
    scored = b.entry_of(result, "scored-1")
    assert isinstance(scored["tenure_trading_days"], int) and scored["tenure_trading_days"] == 21


def test_a_symphony_with_a_verdict_is_eligible_and_one_without_is_explained():
    result = _result()
    for entry in result.entries:
        if entry["verdict"] is not None:
            assert entry["eligible"] is True, entry["symphony_id"]
        if not entry["eligible"]:
            assert entry["verdict"] is None, entry["symphony_id"]
            assert entry["verdict_text"].startswith("no verdict"), entry["symphony_id"]


# ---------------------------------------------------------------------------
# Every item says whose it is
# ---------------------------------------------------------------------------


def test_every_item_of_every_entry_names_its_own_symphony_in_its_subject():
    """A panel row shows an item beside others from many symphonies; an item whose
    subject does not name its symphony cannot be told apart from a peer's once it is
    copied out of its entry. Covers available and unavailable items of scored, fallback,
    no-history, young and undated symphonies."""
    for entry in _result().entries:
        for name in b.EVIDENCE_ITEMS:
            subject = b.item_of(entry, name)["subject"]
            assert entry["display_name"] in subject, (entry["symphony_id"], name, subject)


def test_the_subject_of_an_available_backtest_item_names_its_symphony():
    roster = _roster()
    roster[0].backtest = b.backtest_over(DAYS, [100.0 * 1.001**i for i in range(len(DAYS))])
    entry = b.entry_of(b.review(roster, as_of=b.AS_OF, threshold=20), "scored-1")
    item = b.item_of(entry, "backtest")
    assert item["status"] == "available"  # fixture sanity
    assert entry["display_name"] in item["subject"]
