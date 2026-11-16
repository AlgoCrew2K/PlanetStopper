"""The dead-ticker check's stored-result logic: names, held state, carry-forward, completion, page text.

Module under test: advisors.ticker_health (``display_name``, ``held_state``, ``carry_warnings``,
``is_completed``, ``page_view``, ``LEAD_SENTENCE``, ``STALE_AFTER``).

Contract pinned here (the implementer reads this file, not the plan):

Run record. The parsed ``raw_response`` of one ``TICKER_HEALTH_RUN`` row: ``schema``, ``run_id``,
``trigger``, ``started_at``, ``ended_at`` (aware UTC ISO text), ``status`` (``completed``,
``unavailable``, ``skipped`` or ``failed``), ``completed`` (bool), ``reason`` (fixed run-level
text, for ``unavailable`` and ``skipped``), ``error_type`` (for ``failed``), ``counts``, and, on
a completed run only, ``warnings`` and ``not_checked``. A non-completed record has neither of those
two keys, so a reader never touches them on one.

Warning. A dict with exactly these keys: ``symphony_id``, ``display_name``, ``ticker``,
``reason``, ``asset_nodes``, ``conditions``, ``held`` (``"held"``, ``"not_held"`` or
``"unknown"``), ``found_at`` (the ``ended_at`` text of the run that last judged the pair dead) and
``announced`` (bool).

``not_checked`` (completed runs): ``{"accounts": [{"label", "reason"}], "symphonies":
[{"symphony_id", "display_name", "reason"}], "tickers": [...]}``; a ticker entry is either named
(``symphony_id``, ``display_name``, ``ticker``, ``reason``) or counted (``symphony_id``,
``display_name``, ``reason``, ``count``).

``display_name(entry)``: ``entry`` is a roster entry dict with ``id`` and ``name``; the trimmed
name, else the id (a missing, null, non-text or blank name).

``held_state(holdings_fields, ticker)``: ``holdings_fields`` is a list with one element per
account that lists the symphony, each the roster entry's ``holdings`` value as read with
``entry.get("holdings")`` (``None`` when the field is absent). ``"held"`` when any entry of any
list has ``ticker`` or ``working_ticker`` equal to the ticker (the two keys the engine reads);
otherwise ``"unknown"`` when any field is not a list or holds an entry that is not a dict with a
string ``ticker``; otherwise ``"not_held"``.

``carry_warnings(previous_warnings, judged, unjudged_symphonies, listed_ids, every_roster_read,
ended_at) -> list[dict]`` returns the warnings of the run being stored:
- ``judged`` maps a symphony id the run read the tree of to ``{"display_name": str,
  "holdings_fields": list, "uses": {ticker: TickerUse}, "verdicts": {ticker: TickerVerdict}}``.
  A ticker with no verdict, or a ``not_checked`` verdict, was not judged.
- Judged symphony, ticker in its ``uses`` and judged: dead -> a fresh warning (``found_at`` is
  ``ended_at``; reason, counts, display name and held state from this run); healthy -> dropped.
- Judged symphony, ticker not in its ``uses`` -> dropped. In its ``uses`` but not judged -> the
  previous warning carried unchanged.
- A symphony in ``unjudged_symphonies`` (not judged this run) -> its previous warnings carried
  unchanged. A symphony absent from ``listed_ids`` is dropped when ``every_roster_read`` and
  carried otherwise.
- ``announced`` of a fresh dead pair is that of the previous warning for the same pair, else
  ``False``; a carried warning keeps its own. The caller (the runner) sets ``announced`` on
  ``previous_warnings`` from announcement rows before the call; this function reads no rows.
- Inputs are never modified.

``is_completed(rosters_read, symphonies_listed, symphonies_reached, tickers_asked,
tickers_answered) -> bool`` (all ints): at least one roster read, every listed symphony reached,
and (no ticker asked, or at least one answered).

``page_view(latest, latest_completed, now_utc) -> dict``: ``{"warning": None | {"checked": stamp,
"lines": [{"text", "from_check": None | stamp}]}, "notices": [str]}`` where a stamp is
``analytics.format_et_stamp`` text (``YYYY-MM-DD HH:MM ET``). ``latest`` and ``latest_completed``
are run records or ``None``. The text is plain: markup in a name stays as typed (the template
escapes it).

Fixture provenance: names and ids come from the captured roster
``tests/fixtures/composer/ticker-health/symphony_stats_meta_holdings_kept.json`` (PROVENANCE.md
there). The worked example is the plan's own invented example (QXGD, "Example Copper Rotation"),
because the captured symphonies' tickers are real and live. Run records are built here from the
schema above (schema-derived), never captured: the producer of a run record is the runner, built in
the same cycle.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from advisors import ticker_health as th

ROSTER = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures/composer/ticker-health/symphony_stats_meta_holdings_kept.json"
    ).read_text(encoding="utf-8")
)["symphonies"]

EXAMPLE_NAME = "Example Copper Rotation"
EXAMPLE_ID = "AbCdEf0123456789XyZa"
DEAD_BOTH = "inactive and not tradable at Alpaca"

# The worked example of the plan: a run stored at 2026-11-17 04:12 UTC, shown as 23:12 ET.
ENDED = "2026-11-17T04:12:00+00:00"
ENDED_STAMP = "2026-11-16 23:12 ET"
EARLIER = "2026-11-16T04:10:00+00:00"
EARLIER_STAMP = "2026-11-15 23:10 ET"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

LEAD = "These live symphonies reference tickers that can no longer be traded; fix each tree in Composer."


def warning(sid=EXAMPLE_ID, ticker="QXGD", **overrides):
    record = {
        "symphony_id": sid,
        "display_name": EXAMPLE_NAME,
        "ticker": ticker,
        "reason": DEAD_BOTH,
        "asset_nodes": 25,
        "conditions": 1,
        "held": "not_held",
        "found_at": ENDED,
        "announced": False,
    }
    record.update(overrides)
    return record


def run_record(
    *,
    status="completed",
    run_id="run-0001",
    ended_at=ENDED,
    warnings=(),
    not_checked=None,
    reason=None,
    error_type=None,
):
    completed = status == "completed"
    record = {
        "schema": 1,
        "run_id": run_id,
        "trigger": "nightly",
        "started_at": ended_at,
        "ended_at": ended_at,
        "status": status,
        "completed": completed,
        "reason": reason,
        "error_type": error_type,
        "counts": {},
    }
    if completed:
        record["warnings"] = [dict(w) for w in warnings]
        record["not_checked"] = not_checked or {"accounts": [], "symphonies": [], "tickers": []}
    return record


def not_checked(*, accounts=(), symphonies=(), tickers=()):
    return {"accounts": list(accounts), "symphonies": list(symphonies), "tickers": list(tickers)}


def view(latest, latest_completed, now=NOW):
    result = th.page_view(latest, latest_completed, now)
    assert set(result) == {"warning", "notices"}
    return result


def lines_of(result) -> list[str]:
    return [line["text"] for line in result["warning"]["lines"]]


def line_key(text: str) -> tuple[str, str]:
    """(display name, ticker) of a line "NAME: TICKER is REASON. ..." (names here hold no colon)."""
    name, rest = text.split(": ", 1)
    return name, rest.split(" ", 1)[0]


# ---------------------------------------------------------------------------
# display_name
# ---------------------------------------------------------------------------


class TestDisplayName:
    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ({"id": "Abc123", "name": "Planet LQD"}, "Planet LQD"),
            ({"id": "Abc123", "name": "  Planet  LQD \t"}, "Planet  LQD"),
            ({"id": "Abc123", "name": ""}, "Abc123"),
            ({"id": "Abc123", "name": "   "}, "Abc123"),
            ({"id": "Abc123", "name": "\t\n"}, "Abc123"),
            ({"id": "Abc123", "name": None}, "Abc123"),
            ({"id": "Abc123"}, "Abc123"),
            ({"id": "Abc123", "name": 123}, "Abc123"),
            ({"id": "Abc123", "name": ["Planet"]}, "Abc123"),
        ],
    )
    def test_the_trimmed_name_else_the_composer_id(self, entry, expected):
        assert th.display_name(entry) == expected

    def test_every_captured_roster_entry_reads_its_own_trimmed_name(self):
        for entry in ROSTER:
            assert th.display_name(entry) == entry["name"].strip()


# ---------------------------------------------------------------------------
# held_state
# ---------------------------------------------------------------------------


def holding(ticker, **extra):
    return {"ticker": ticker, "allocation": 0.25, **extra}


class TestHeldState:
    def test_a_captured_holding_reads_held_and_a_ticker_it_lacks_reads_not_held(self):
        entry = ROSTER[0]
        held_ticker = entry["holdings"][0]["ticker"]
        assert th.held_state([entry["holdings"]], held_ticker) == "held"
        assert th.held_state([entry["holdings"]], "QXGD") == "not_held"

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ([[holding("SPY"), holding("QXGD")]], "held"),
            ([[holding("SPY", working_ticker="QXGD")]], "held"),
            ([[{"ticker": "QXGD", "working_ticker": "SPY", "allocation": 1.0}]], "held"),
            ([[holding("SPY"), holding("QQQ")]], "not_held"),
            ([[]], "not_held"),
            ([[holding("QXGDX")]], "not_held"),
            ([[holding("qxgd")]], "not_held"),
        ],
        ids=[
            "ticker-key",
            "working-ticker-key",
            "ticker-key-beside-another-working-ticker",
            "no-match",
            "empty-list",
            "longer-symbol",
            "case-differs",
        ],
    )
    def test_one_readable_list(self, fields, expected):
        assert th.held_state(fields, "QXGD") == expected

    @pytest.mark.parametrize(
        "field",
        [
            None,
            "QXGD",
            {"ticker": "QXGD"},
            7,
            [holding("SPY"), "QXGD"],
            [holding("SPY"), {"allocation": 1}],
            [holding("SPY"), {"ticker": None}],
            [holding("SPY"), {"ticker": 7}],
            [None],
        ],
        ids=[
            "absent",
            "text",
            "object",
            "number",
            "text-entry",
            "no-ticker",
            "null-ticker",
            "number-ticker",
            "null-entry",
        ],
    )
    def test_a_field_that_cannot_be_read_is_unknown_never_not_held(self, field):
        assert th.held_state([field], "QXGD") == "unknown"

    def test_a_match_wins_over_a_malformed_sibling_entry(self):
        assert th.held_state([[holding("QXGD"), "garbage"]], "QXGD") == "held"

    def test_two_accounts_one_holding_it_reads_held(self):
        assert th.held_state([[holding("SPY")], [holding("QXGD")]], "QXGD") == "held"

    def test_two_accounts_neither_holding_it_reads_not_held(self):
        assert th.held_state([[holding("SPY")], [holding("QQQ")]], "QXGD") == "not_held"

    def test_one_account_not_holding_and_one_unreadable_reads_unknown(self):
        assert th.held_state([[holding("SPY")], None], "QXGD") == "unknown"

    def test_one_account_holding_and_one_unreadable_reads_held(self):
        assert th.held_state([None, [holding("QXGD")]], "QXGD") == "held"

    def test_the_fields_are_not_modified(self):
        fields = [[holding("SPY")], None]
        before = copy.deepcopy(fields)
        th.held_state(fields, "QXGD")
        assert fields == before


# ---------------------------------------------------------------------------
# is_completed (D-6)
# ---------------------------------------------------------------------------


class TestIsCompleted:
    @pytest.mark.parametrize(
        ("rosters", "listed", "reached", "asked", "answered", "expected"),
        [
            (1, 3, 3, 10, 10, True),
            (2, 5, 5, 10, 1, True),
            (1, 0, 0, 0, 0, True),
            (1, 3, 3, 0, 0, True),
            (0, 0, 0, 0, 0, False),
            (0, 3, 3, 10, 10, False),
            (1, 3, 2, 10, 10, False),
            (1, 3, 0, 0, 0, False),
            (1, 3, 3, 10, 0, False),
            (2, 3, 3, 1, 0, False),
            (1, 1, 0, 0, 0, False),
        ],
        ids=[
            "everything-answered",
            "one-answer-is-enough",
            "no-symphony-listed",
            "every-score-read-failed-no-ticker-to-ask",
            "no-roster-read",
            "no-roster-read-despite-everything-else",
            "a-symphony-not-reached",
            "no-symphony-reached",
            "alpaca-gave-no-answer",
            "alpaca-gave-no-answer-one-ticker",
            "one-listed-none-reached",
        ],
    )
    def test_the_completion_rule(self, rosters, listed, reached, asked, answered, expected):
        assert (
            th.is_completed(
                rosters_read=rosters,
                symphonies_listed=listed,
                symphonies_reached=reached,
                tickers_asked=asked,
                tickers_answered=answered,
            )
            is expected
        )


# ---------------------------------------------------------------------------
# carry_warnings
# ---------------------------------------------------------------------------

T2 = "2026-11-18T04:11:00+00:00"  # the end of the run being stored in these tests
X, Y = "XidXidXidXidXidXidXi", "YidYidYidYidYidYidYi"


def verdict(state, reason=None):
    return th.TickerVerdict(state=state, reason=reason)


def judged_entry(name=EXAMPLE_NAME, *, uses, verdicts, holdings=None):
    return {
        "display_name": name,
        "holdings_fields": [[] if holdings is None else holdings],
        "uses": {t: th.TickerUse(asset_nodes=a, conditions=c) for t, (a, c) in uses.items()},
        "verdicts": verdicts,
    }


def carry(previous=(), judged=None, unjudged=(), listed=None, every_roster_read=True):
    judged = judged or {}
    listed = set(judged) | set(unjudged) if listed is None else listed
    result = th.carry_warnings(
        previous_warnings=[dict(w) for w in previous],
        judged=judged,
        unjudged_symphonies=set(unjudged),
        listed_ids=set(listed),
        every_roster_read=every_roster_read,
        ended_at=T2,
    )
    by_pair = {(w["symphony_id"], w["ticker"]): w for w in result}
    assert len(by_pair) == len(result), "a pair appears once"
    return by_pair


WARNING_KEYS = {
    "symphony_id",
    "display_name",
    "ticker",
    "reason",
    "asset_nodes",
    "conditions",
    "held",
    "found_at",
    "announced",
}


class TestCarryFreshAndCleared:
    def test_a_new_dead_pair_becomes_a_fresh_unannounced_warning(self):
        judged = {
            X: judged_entry(uses={"QXGD": (25, 1)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        assert carry(judged=judged) == {
            (X, "QXGD"): {
                "symphony_id": X,
                "display_name": EXAMPLE_NAME,
                "ticker": "QXGD",
                "reason": DEAD_BOTH,
                "asset_nodes": 25,
                "conditions": 1,
                "held": "not_held",
                "found_at": T2,
                "announced": False,
            }
        }

    def test_every_warning_has_exactly_the_schema_keys(self):
        judged = {
            X: judged_entry(uses={"QXGD": (1, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        stored = carry(previous=[warning(Y, "OLD")], judged=judged, unjudged=[Y])
        assert set(stored) == {(X, "QXGD"), (Y, "OLD")}
        for record in stored.values():
            assert set(record) == WARNING_KEYS

    def test_a_pair_still_dead_takes_this_runs_facts_and_keeps_its_announced_flag(self):
        previous = [
            warning(
                X,
                "QXGD",
                reason="inactive at Alpaca",
                asset_nodes=3,
                conditions=0,
                held="held",
                found_at=ENDED,
                announced=True,
            )
        ]
        judged = {
            X: judged_entry(
                "Renamed in Composer",
                uses={"QXGD": (25, 1)},
                verdicts={"QXGD": verdict("dead", DEAD_BOTH)},
            )
        }
        stored = carry(previous, judged)[(X, "QXGD")]
        assert stored == warning(
            X,
            "QXGD",
            display_name="Renamed in Composer",
            reason=DEAD_BOTH,
            asset_nodes=25,
            conditions=1,
            held="not_held",
            found_at=T2,
            announced=True,
        )

    def test_a_previous_unannounced_pair_stays_unannounced(self):
        previous = [warning(X, "QXGD", announced=False)]
        judged = {
            X: judged_entry(uses={"QXGD": (1, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        assert carry(previous, judged)[(X, "QXGD")]["announced"] is False

    @pytest.mark.parametrize(
        ("held_entries", "expected"),
        [([holding("QXGD")], "held"), ([holding("SPY")], "not_held"), (None, "not_held")],
    )
    def test_the_held_state_comes_from_this_runs_roster_holdings(self, held_entries, expected):
        judged = {
            X: judged_entry(
                uses={"QXGD": (1, 0)},
                verdicts={"QXGD": verdict("dead", DEAD_BOTH)},
                holdings=held_entries,
            )
        }
        assert carry(judged=judged)[(X, "QXGD")]["held"] == expected

    def test_an_unreadable_holdings_field_reads_unknown(self):
        entry = judged_entry(uses={"QXGD": (1, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        entry["holdings_fields"] = [None]
        assert carry(judged={X: entry})[(X, "QXGD")]["held"] == "unknown"

    def test_a_pair_now_healthy_is_dropped_and_a_later_death_is_announced_again(self):
        previous = [warning(X, "QXGD", announced=True)]
        healthy = {X: judged_entry(uses={"QXGD": (25, 1)}, verdicts={"QXGD": verdict("healthy")})}
        after_fix = carry(previous, healthy)
        assert after_fix == {}
        dead_again = {
            X: judged_entry(uses={"QXGD": (25, 1)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        assert carry(list(after_fix.values()), dead_again)[(X, "QXGD")]["announced"] is False

    def test_a_ticker_removed_from_the_tree_is_dropped(self):
        previous = [warning(X, "QXGD")]
        judged = {X: judged_entry(uses={"SPY": (1, 0)}, verdicts={"SPY": verdict("healthy")})}
        assert carry(previous, judged) == {}

    def test_a_removed_ticker_is_dropped_though_the_verdict_map_still_names_it(self):
        previous = [warning(X, "QXGD")]
        judged = {
            X: judged_entry(
                uses={"SPY": (1, 0)},
                verdicts={"SPY": verdict("healthy"), "QXGD": verdict("dead", DEAD_BOTH)},
            )
        }
        assert (X, "QXGD") not in carry(previous, judged)

    def test_only_dead_tickers_become_warnings(self):
        judged = {
            X: judged_entry(
                uses={"A": (1, 0), "B": (1, 0), "C": (1, 0), "D": (0, 1)},
                verdicts={
                    "A": verdict("healthy"),
                    "B": verdict("not_checked", "Alpaca rate limit"),
                    "C": verdict("dead", "not listed at Alpaca"),
                },
            )
        }
        assert set(carry(judged=judged)) == {(X, "C")}

    def test_two_symphonies_naming_one_dead_ticker_each_get_a_warning(self):
        uses = {"QXGD": (2, 1)}
        verdicts = {"QXGD": verdict("dead", DEAD_BOTH)}
        judged = {
            X: judged_entry("X name", uses=uses, verdicts=verdicts),
            Y: judged_entry("Y name", uses=uses, verdicts=verdicts),
        }
        assert set(carry(judged=judged)) == {(X, "QXGD"), (Y, "QXGD")}


class TestCarryWhatTheRunDidNotJudge:
    def test_a_ticker_in_the_tree_without_an_answer_keeps_the_previous_warning_unchanged(self):
        previous = [warning(X, "QXGD", found_at=EARLIER, announced=True, asset_nodes=7)]
        judged = {X: judged_entry(uses={"QXGD": (25, 1)}, verdicts={})}
        assert carry(previous, judged)[(X, "QXGD")] == previous[0]

    def test_a_ticker_with_a_not_checked_verdict_keeps_the_previous_warning_unchanged(self):
        previous = [warning(X, "QXGD", found_at=EARLIER, announced=True, asset_nodes=7)]
        judged = {
            X: judged_entry(
                uses={"QXGD": (25, 1)},
                verdicts={"QXGD": verdict("not_checked", "Alpaca unavailable")},
            )
        }
        assert carry(previous, judged)[(X, "QXGD")] == previous[0]

    def test_a_symphony_whose_tree_was_not_read_keeps_every_previous_warning_unchanged(self):
        previous = [warning(Y, "QXGD", found_at=EARLIER), warning(Y, "PLRZ", found_at=EARLIER)]
        stored = carry(previous, unjudged=[Y])
        assert stored == {(Y, "QXGD"): previous[0], (Y, "PLRZ"): previous[1]}

    def test_a_symphony_gone_from_every_roster_is_dropped_when_every_roster_was_read(self):
        previous = [warning(X, "QXGD")]
        judged = {Y: judged_entry(uses={"SPY": (1, 0)}, verdicts={"SPY": verdict("healthy")})}
        assert carry(previous, judged, listed=[Y], every_roster_read=True) == {}

    def test_a_symphony_missing_while_a_roster_failed_is_carried(self):
        previous = [warning(X, "QXGD", found_at=EARLIER)]
        judged = {Y: judged_entry(uses={"SPY": (1, 0)}, verdicts={"SPY": verdict("healthy")})}
        stored = carry(previous, judged, listed=[Y], every_roster_read=False)
        assert stored == {(X, "QXGD"): previous[0]}

    def test_a_fix_in_one_symphony_leaves_the_other_symphonys_warning(self):
        previous = [warning(X, "QXGD", announced=True), warning(Y, "QXGD", announced=True)]
        judged = {
            X: judged_entry("X name", uses={"SPY": (1, 0)}, verdicts={"SPY": verdict("healthy")}),
            Y: judged_entry(
                "Y name", uses={"QXGD": (4, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)}
            ),
        }
        stored = carry(previous, judged)
        assert set(stored) == {(Y, "QXGD")}
        assert stored[(Y, "QXGD")]["announced"] is True

    def test_a_new_pair_and_a_carried_pair_come_out_together(self):
        previous = [warning(Y, "OLD", found_at=EARLIER, announced=True)]
        judged = {
            X: judged_entry(uses={"QXGD": (1, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        stored = carry(previous, judged, unjudged=[Y])
        assert set(stored) == {(X, "QXGD"), (Y, "OLD")}
        assert stored[(Y, "OLD")]["announced"] is True
        assert stored[(X, "QXGD")]["announced"] is False

    def test_the_inputs_are_not_modified(self):
        previous = [warning(X, "QXGD", announced=True), warning(Y, "OLD")]
        judged = {
            X: judged_entry(uses={"QXGD": (1, 0)}, verdicts={"QXGD": verdict("dead", DEAD_BOTH)})
        }
        before_previous, before_judged = copy.deepcopy(previous), copy.deepcopy(judged)
        th.carry_warnings(
            previous_warnings=previous,
            judged=judged,
            unjudged_symphonies={Y},
            listed_ids={X, Y},
            every_roster_read=True,
            ended_at=T2,
        )
        assert previous == before_previous
        assert judged == before_judged


# ---------------------------------------------------------------------------
# page_view: the warning
# ---------------------------------------------------------------------------


class TestWarning:
    def test_the_lead_sentence_is_the_plans_text(self):
        assert th.LEAD_SENTENCE == LEAD

    def test_the_worked_example_reads_as_the_plan_shows_it(self):
        run = run_record(warnings=[warning()])
        assert view(run, run) == {
            "warning": {
                "checked": ENDED_STAMP,
                "lines": [
                    {
                        "text": "Example Copper Rotation: QXGD is inactive and not tradable at Alpaca. "
                        "Used by 25 asset nodes and 1 condition. Not held now.",
                        "from_check": None,
                    }
                ],
            },
            "notices": [],
        }

    @pytest.mark.parametrize(
        ("held", "ending"),
        [("held", "Held now."), ("not_held", "Not held now."), ("unknown", "Holding unknown.")],
    )
    def test_the_held_state_ends_the_line(self, held, ending):
        run = run_record(warnings=[warning(held=held)])
        assert lines_of(view(run, run))[0].endswith(f" {ending}")

    @pytest.mark.parametrize(
        ("assets", "conditions", "phrase"),
        [
            (1, 0, "Used by 1 asset node and 0 conditions."),
            (0, 1, "Used by 0 asset nodes and 1 condition."),
            (25, 1, "Used by 25 asset nodes and 1 condition."),
            (2, 3, "Used by 2 asset nodes and 3 conditions."),
            (0, 2, "Used by 0 asset nodes and 2 conditions."),
        ],
    )
    def test_the_counts_follow_their_plural(self, assets, conditions, phrase):
        run = run_record(warnings=[warning(asset_nodes=assets, conditions=conditions)])
        assert phrase in lines_of(view(run, run))[0]

    def test_a_reason_names_why_the_ticker_is_dead(self):
        run = run_record(warnings=[warning(reason="not listed at Alpaca")])
        assert lines_of(view(run, run))[0].startswith(
            "Example Copper Rotation: QXGD is not listed at Alpaca. "
        )

    def test_lines_sort_by_name_then_ticker(self):
        run = run_record(
            warnings=[
                warning("idY", "B", display_name="Y fund"),
                warning("idY", "A", display_name="Y fund"),
                warning("idX", "C", display_name="X fund"),
            ]
        )
        assert [line_key(t) for t in lines_of(view(run, run))] == [
            ("X fund", "C"),
            ("Y fund", "A"),
            ("Y fund", "B"),
        ]

    def test_names_compare_without_case_then_the_symphony_id_then_the_ticker(self):
        # casefold ties "alpha", "Alpha" and "alpha": the symphony id breaks the tie and the
        # ticker order (T1 before T9) must not decide it. Raw comparison would put "Alpha" first.
        run = run_record(
            warnings=[
                warning("idB", "T0", display_name="beta"),
                warning("idZ", "T1", display_name="alpha"),
                warning("idA", "T9", display_name="alpha"),
                warning("idC", "T5", display_name="Alpha"),
            ]
        )
        assert [line_key(t) for t in lines_of(view(run, run))] == [
            ("alpha", "T9"),
            ("Alpha", "T5"),
            ("alpha", "T1"),
            ("beta", "T0"),
        ]

    def test_markup_in_a_name_stays_text_for_the_template_to_escape(self):
        hostile = "<img src=x onerror=alert(1)>"
        run = run_record(warnings=[warning(display_name=hostile)])
        assert lines_of(view(run, run))[0].startswith(f"{hostile}: QXGD is ")

    def test_a_blank_name_stored_as_the_id_reads_as_the_id(self):
        run = run_record(warnings=[warning(display_name=EXAMPLE_ID)])
        assert lines_of(view(run, run))[0].startswith(f"{EXAMPLE_ID}: QXGD is ")

    def test_the_view_does_not_change_its_inputs(self):
        run = run_record(warnings=[warning()])
        latest, completed = copy.deepcopy(run), copy.deepcopy(run)
        th.page_view(latest, completed, NOW)
        assert (latest, completed) == (run, run)


class TestNothingToShow:
    def test_a_recent_clean_completed_run_shows_neither_a_warning_nor_a_notice(self):
        run = run_record()
        assert view(run, run) == {"warning": None, "notices": []}

    def test_a_completed_run_with_no_symphony_listed_shows_nothing_either(self):
        run = run_record(not_checked=not_checked())
        assert view(run, run) == {"warning": None, "notices": []}

    def test_the_same_run_read_from_two_copies_is_still_the_latest_completed_run(self):
        run = run_record(warnings=[warning()])
        result = view(run, copy.deepcopy(run))
        assert result["notices"] == []
        assert result["warning"]["lines"][0]["from_check"] is None


# ---------------------------------------------------------------------------
# page_view: notices
# ---------------------------------------------------------------------------


class TestNoticesOfTheLatestCompletedRun:
    @pytest.mark.parametrize(
        "reason",
        [
            "Composer unavailable",
            "definition not found",
            "Composer refused the request",
            "definition unreadable",
            "Composer rejected the request",
            "symphony id not usable",
            "asset class not covered by the check",
            "Composer rate limit",
            "market opened",
            "another Composer job was running",
        ],
    )
    def test_a_symphony_not_checked_names_it_with_its_reason(self, reason):
        entry = {"symphony_id": Y, "display_name": "Y fund", "reason": reason}
        run = run_record(not_checked=not_checked(symphonies=[entry]))
        assert view(run, run)["notices"] == [f"Not checked: Y fund ({reason})."]

    @pytest.mark.parametrize(
        "reason",
        [
            "Composer unavailable",
            "Composer refused the request",
            "account not found",
            "Composer rejected the request",
            "Composer response unreadable",
            "Composer rate limit",
        ],
    )
    def test_an_account_not_checked_names_its_label_with_its_reason(self, reason):
        run = run_record(
            not_checked=not_checked(accounts=[{"label": "Roth IRA", "reason": reason}])
        )
        assert view(run, run)["notices"] == [
            f"Not checked: symphonies of the Roth IRA account ({reason})."
        ]

    def test_a_ticker_in_a_form_the_check_does_not_send_is_named(self):
        entry = {
            "symphony_id": Y,
            "display_name": "Y fund",
            "ticker": "BRK/B",
            "reason": "ticker form not checked",
        }
        run = run_record(not_checked=not_checked(tickers=[entry]))
        assert view(run, run)["notices"] == [
            "Not checked: BRK/B in Y fund (ticker form not checked)."
        ]

    @pytest.mark.parametrize(
        ("count", "noun"), [(1, "ticker"), (2, "tickers"), (3, "tickers"), (40, "tickers")]
    )
    def test_unanswered_tickers_are_counted_per_symphony_with_the_alpaca_reason(self, count, noun):
        entry = {
            "symphony_id": X,
            "display_name": "X fund",
            "reason": "Alpaca rate limit",
            "count": count,
        }
        run = run_record(not_checked=not_checked(tickers=[entry]))
        assert view(run, run)["notices"] == [
            f"{count} {noun} in X fund could not be checked (Alpaca rate limit)."
        ]

    def test_notices_list_accounts_then_symphonies_then_tickers_each_in_record_order(self):
        run = run_record(
            not_checked=not_checked(
                accounts=[
                    {"label": "Roth IRA", "reason": "Composer unavailable"},
                    {"label": "Individual", "reason": "account not found"},
                ],
                symphonies=[
                    {"symphony_id": Y, "display_name": "Zed", "reason": "definition not found"},
                    {"symphony_id": X, "display_name": "Alpha", "reason": "Composer unavailable"},
                ],
                tickers=[
                    {
                        "symphony_id": X,
                        "display_name": "Alpha",
                        "reason": "Alpaca unavailable",
                        "count": 2,
                    },
                    {
                        "symphony_id": X,
                        "display_name": "Alpha",
                        "ticker": "A B",
                        "reason": "ticker form not checked",
                    },
                ],
            )
        )
        assert view(run, run)["notices"] == [
            "Not checked: symphonies of the Roth IRA account (Composer unavailable).",
            "Not checked: symphonies of the Individual account (account not found).",
            "Not checked: Zed (definition not found).",
            "Not checked: Alpha (Composer unavailable).",
            "2 tickers in Alpha could not be checked (Alpaca unavailable).",
            "Not checked: A B in Alpha (ticker form not checked).",
        ]

    def test_a_not_checked_symphony_does_not_hide_the_warnings_of_the_others(self):
        entry = {"symphony_id": Y, "display_name": "Y fund", "reason": "Composer unavailable"}
        run = run_record(warnings=[warning()], not_checked=not_checked(symphonies=[entry]))
        result = view(run, run)
        assert result["warning"] is not None and len(result["warning"]["lines"]) == 1
        assert result["notices"] == ["Not checked: Y fund (Composer unavailable)."]

    def test_a_warning_the_latest_run_did_not_judge_shows_the_time_it_was_found(self):
        carried = warning(found_at=EARLIER)
        fresh = warning("idFresh", "NEW", display_name="Fresh fund", found_at=ENDED)
        entry = {
            "symphony_id": EXAMPLE_ID,
            "display_name": EXAMPLE_NAME,
            "reason": "Composer unavailable",
        }
        run = run_record(warnings=[carried, fresh], not_checked=not_checked(symphonies=[entry]))
        lines = {
            line["text"].split(":")[0]: line["from_check"]
            for line in view(run, run)["warning"]["lines"]
        }
        assert lines == {EXAMPLE_NAME: EARLIER_STAMP, "Fresh fund": None}

    def test_a_response_body_or_error_text_beyond_the_record_fields_is_never_shown(self):
        marker = "LEAKMARK-body-77ce"
        entry = {
            "symphony_id": Y,
            "display_name": "Y fund",
            "reason": "Composer unavailable",
            "body": marker,
            "error": marker,
        }
        run = run_record(not_checked=not_checked(symphonies=[entry]))
        run["body"] = marker
        assert marker not in json.dumps(view(run, run))


class TestNoticesOfARunThatDidNotComplete:
    @pytest.mark.parametrize("status", ["unavailable", "skipped"])
    def test_an_unavailable_or_skipped_run_names_its_reason_and_the_last_completed_check(
        self, status
    ):
        completed = run_record(warnings=[warning(found_at=EARLIER)], run_id="done")
        latest = run_record(
            status=status,
            run_id="latest",
            reason="Alpaca unreachable",
            ended_at="2026-11-17T09:00:00+00:00",
        )
        assert view(latest, completed)["notices"] == [
            f"Ticker check unavailable: Alpaca unreachable. Last completed check {ENDED_STAMP}."
        ]

    def test_with_no_completed_check_the_notice_says_so(self):
        latest = run_record(status="unavailable", reason="no Composer account configured")
        result = view(latest, None)
        assert (
            "Ticker check unavailable: no Composer account configured. No completed check yet."
            in (result["notices"])
        )
        assert result["warning"] is None

    def test_earlier_warnings_show_with_the_time_each_was_found_not_the_failed_runs(self):
        completed = run_record(
            warnings=[warning(found_at=EARLIER), warning("idB", "PLRZ")], run_id="done"
        )
        latest = run_record(
            status="unavailable",
            reason="Alpaca unreachable",
            run_id="latest",
            ended_at="2026-11-17T09:00:00+00:00",
        )
        shown = view(latest, completed)["warning"]
        assert shown["checked"] == ENDED_STAMP
        assert sorted(line["from_check"] for line in shown["lines"]) == [EARLIER_STAMP, ENDED_STAMP]

    def test_a_failed_run_says_when_it_failed_and_never_the_exception_type(self):
        completed = run_record(warnings=[warning()], run_id="done")
        latest = run_record(
            status="failed",
            error_type="RuntimeError",
            run_id="latest",
            ended_at="2026-11-17T09:00:00+00:00",
        )
        result = view(latest, completed)
        assert result["notices"] == ["Ticker check failed on 2026-11-17 04:00 ET."]
        assert "RuntimeError" not in json.dumps(result)
        assert result["warning"]["lines"][0]["from_check"] == ENDED_STAMP

    def test_a_failed_run_with_no_completed_check_shows_both_facts_in_order(self):
        latest = run_record(
            status="failed", error_type="RuntimeError", ended_at="2026-11-17T09:00:00+00:00"
        )
        assert view(latest, None)["notices"] == [
            "No ticker check has completed yet.",
            "Ticker check failed on 2026-11-17 04:00 ET.",
        ]

    def test_no_run_at_all_reads_never_completed(self):
        assert view(None, None) == {
            "warning": None,
            "notices": ["No ticker check has completed yet."],
        }

    def test_a_non_completed_latest_run_never_supplies_warnings_or_not_checked_entries(self):
        latest = run_record(
            status="unavailable",
            reason="Alpaca unreachable",
            run_id="latest",
            ended_at="2026-11-17T09:00:00+00:00",
        )
        assert "warnings" not in latest and "not_checked" not in latest
        completed = run_record(warnings=[warning()], run_id="done")
        assert len(view(latest, completed)["warning"]["lines"]) == 1


class TestStaleCheck:
    def test_a_completed_check_36_hours_old_is_not_stale(self):
        run = run_record()
        now = datetime(2026, 11, 17, 4, 12, tzinfo=UTC) + timedelta(hours=36)
        assert view(run, run, now)["notices"] == []

    def test_a_second_past_36_hours_is_stale_and_names_the_last_completed_check(self):
        run = run_record()
        now = datetime(2026, 11, 17, 4, 12, tzinfo=UTC) + timedelta(hours=36, seconds=1)
        assert view(run, run, now)["notices"] == [
            f"No ticker check has completed since {ENDED_STAMP}."
        ]

    def test_a_check_35_hours_59_minutes_old_is_not_stale(self):
        run = run_record()
        now = datetime(2026, 11, 17, 4, 12, tzinfo=UTC) + timedelta(hours=35, minutes=59)
        assert view(run, run, now)["notices"] == []

    def test_the_stale_threshold_is_36_hours(self):
        assert timedelta(hours=36) == th.STALE_AFTER

    def test_stale_warnings_still_show(self):
        run = run_record(warnings=[warning()])
        now = datetime(2026, 11, 17, 4, 12, tzinfo=UTC) + timedelta(hours=40)
        result = view(run, run, now)
        assert result["notices"] == [f"No ticker check has completed since {ENDED_STAMP}."]
        assert len(result["warning"]["lines"]) == 1

    def test_an_unavailable_latest_run_and_a_stale_completed_run_show_both_in_order(self):
        completed = run_record(run_id="done")
        latest = run_record(
            status="unavailable",
            reason="Alpaca unreachable",
            run_id="latest",
            ended_at="2026-11-19T04:00:00+00:00",
        )
        now = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)
        assert view(latest, completed, now)["notices"] == [
            f"Ticker check unavailable: Alpaca unreachable. Last completed check {ENDED_STAMP}.",
            f"No ticker check has completed since {ENDED_STAMP}.",
        ]
