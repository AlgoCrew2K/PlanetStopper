"""`GET /api/state` sends no table markup, reads no sort parameter, and changes nothing else.

The poll once rendered a table into an `html` key that no client reads, ordered by `sortCol` and
`sortDir`. These tests hold the route to four rules, each through the real route over a real temp
state database (the worlds of `tests/app/_state_poll_worlds.py`, built from captured records and
derived records with one named change; nothing in `analytics` or `database` is replaced except
the one read a fault world makes fail):

* every body, on every branch and market state it serves, lacks `html`;
* every other key and value equals what the reference commit served for the same stored state,
  query and clock (the goldens of `tests/fixtures/state_poll_no_table/`, written by the committed
  producer at the reference commit), including each snapshot entry's defaulted fields and the
  name order of each account list;
* `sortCol` and `sortDir` change nothing: a request with them equals the same request without;
* the fields the table's code once stamped for the page survive on the served entries.

Two reads in one world never differ because the world fixes the route clock; every expected value
is read from the record a world was seeded from or computed from it, never typed in.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

import app as app_module
import database as database_module
from tests.app._state_poll_worlds import (
    CAUGHT_EXCEPTIONS,
    CORPORATE_ID,
    FEAVERD_ID,
    FIXTURE_FOLDER,
    GOLDEN_CASES,
    PER_SYMPHONY_HELPERS,
    REASONABILISTS_ID,
    UNKNOWN_ACCOUNT_ID,
    WORLDS,
    StateWorld,
    closed_frozen_two_accounts,
    comparable,
    fail_guard_alpha,
    fail_symphony_helpers,
    fail_triggers,
    golden_path,
    open_two_accounts,
    seed_captured_exit_trigger,
)

NULL_LEGS = {"if_held": None, "dry_run": None}
CARD_LEG_KEYS = ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held")

# The branches and market states the route serves, each as the world that reaches it and the
# request that asks for it. The waiting body is the one branch with no stored state.
HTML_ABSENT_CASES = {
    "live branch, market open": ("open_two_accounts", "active"),
    "close snapshot, closed": ("closed_frozen_two_accounts", "active"),
    "close snapshot, pre-market": ("pre_market", "active"),
    "close snapshot, stalled open": ("stalled_open", "active"),
    "close snapshot, held view": ("held_view", "active"),
    "live branch, closed with no snapshot": ("closed_no_snapshot_two_accounts", "active"),
    "waiting, market open": ("no_state", "waiting"),
    "waiting, market closed": ("no_state_closed", "waiting"),
}

# The values `sortCol` took at the reference commit, and the query forms around them.
REFERENCE_SORT_COLUMNS = (
    "name",
    "mc_prob",
    "status",
    "stop_level",
    "current_return",
    "shadow_hwm",
    "shadow",
)
SORT_QUERIES = {
    "status descending": "sortCol=status&sortDir=desc",
    "unknown column": "sortCol=unknown_column",
    "empty values": "sortCol=&sortDir=",
    "direction alone": "sortDir=desc",
    **{
        f"{column} {direction}": f"sortCol={column}&sortDir={direction}"
        for column in REFERENCE_SORT_COLUMNS
        for direction in ("asc", "desc")
    },
}


def build(name: str, monkeypatch, tmp_path) -> StateWorld:
    return WORLDS[name](monkeypatch, tmp_path)


def golden_for(case: str) -> dict:
    return json.loads(golden_path(case).read_text(encoding="utf-8"))


def differing_keys(body: dict, expected: dict) -> list[str]:
    missing = object()
    return sorted(
        key
        for key in set(body) | set(expected)
        if body.get(key, missing) != expected.get(key, missing)
    )


def symphony_entries(mapping: dict) -> dict[str, dict]:
    """The symphony entries of a `state` or `bot_state` dict: dicts that carry a name."""
    return {
        key: value for key, value in mapping.items() if isinstance(value, dict) and "name" in value
    }


def name_order(entries: list[dict]) -> list[str]:
    """The ids of `entries` ordered by lower-cased name, or by lower-cased id when unnamed."""
    return [e["id"] for e in sorted(entries, key=lambda e: (e.get("name") or e["id"]).lower())]


# ---------------------------------------------------------------------------
# No table markup, on every branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", HTML_ABSENT_CASES)
def test_no_branch_or_market_state_sends_a_table(monkeypatch, tmp_path, case):
    world_name, status = HTML_ABSENT_CASES[case]
    built = build(world_name, monkeypatch, tmp_path)

    response = built.get()

    assert response.status_code == 200, f"{case}: answered {response.status_code}"
    body = response.get_json()
    assert body["status"] == status, f"{case}: the world did not reach the branch it names"
    assert "html" not in body, f"{case}: the body still carries a table"


def test_a_request_narrowed_to_one_account_sends_no_table(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    body = built.body(f"/api/state?account={built.first_account_id}")

    assert body["status"] == "active"
    assert "html" not in body


def test_a_request_for_an_account_no_record_holds_sends_no_table(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    body = built.body(f"/api/state?account={UNKNOWN_ACCOUNT_ID}")

    assert "html" not in body


def test_two_polls_of_a_close_snapshot_under_one_clock_are_equal_and_carry_no_table(
    monkeypatch, tmp_path
):
    built = build("closed_frozen_two_accounts", monkeypatch, tmp_path)

    first, second = built.body(), built.body()

    assert "html" not in first and "html" not in second
    assert comparable(first) == comparable(second)


# ---------------------------------------------------------------------------
# Every other key and value is the reference commit's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_CASES)
def test_the_body_is_the_reference_body_without_the_table(monkeypatch, tmp_path, case):
    world_name, request_for = GOLDEN_CASES[case]
    built = build(world_name, monkeypatch, tmp_path)

    body = comparable(built.body(request_for(built)))

    differing = differing_keys(body, comparable(golden_for(case)))
    assert not differing, f"{case}: keys that differ from the reference body: {differing}"


def test_every_golden_names_a_world_and_every_world_has_a_golden():
    named = {world for world, _request in GOLDEN_CASES.values()}
    assert named == set(WORLDS), "a world is built that no golden compares, or the reverse"
    for case in GOLDEN_CASES:
        assert golden_path(case).is_file(), f"no golden for {case}"


# ---------------------------------------------------------------------------
# The live branch
# ---------------------------------------------------------------------------


def test_each_live_symphony_entry_carries_its_id_and_normalized_name(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    body = built.body()

    assert set(body["state"]) >= set(built.record["entries"]), "fixture sanity: entries served"
    for where in ("state", "bot_state"):
        entries = symphony_entries(body[where])
        assert set(entries) == set(symphony_entries(built.record["entries"]))
        for key, entry in entries.items():
            assert entry["id"] == key, f"{where}[{key}] lost its id"
            assert entry["normalized_name"] == database_module.normalize_name(entry["name"]), (
                f"{where}[{key}] lost its normalized name"
            )


def test_a_stored_dict_without_a_name_gains_no_id_or_normalized_name(monkeypatch, tmp_path):
    built = build("open_with_snapshot_key", monkeypatch, tmp_path)

    body = built.body()

    for where in ("state", "bot_state"):
        stored = body[where]["last_market_close_snapshot"]
        assert isinstance(stored, dict) and "name" not in stored, "fixture sanity: unnamed dict"
        assert "id" not in stored and "normalized_name" not in stored, where


def test_an_account_filter_narrows_the_entries_and_leaves_the_portfolio_whole(
    monkeypatch, tmp_path
):
    built = build("open_two_accounts", monkeypatch, tmp_path)
    account = built.first_account_id
    whole = built.body()

    narrowed = built.body(f"/api/state?account={account}")

    in_account = {
        k for k, e in symphony_entries(built.record["entries"]).items() if e["account"] == account
    }
    assert in_account and in_account != set(symphony_entries(built.record["entries"])), (
        "fixture sanity: the filter must keep some symphonies and drop others"
    )
    for where in ("state", "bot_state"):
        assert set(symphony_entries(narrowed[where])) == in_account, where
    assert {card["id"] for card in narrowed["symphonies"]} == in_account
    assert narrowed["portfolio_strip"] == whole["portfolio_strip"]
    assert narrowed["meta"] == whole["meta"]


def test_an_account_no_record_holds_leaves_no_symphony_entry(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    body = built.body(f"/api/state?account={UNKNOWN_ACCOUNT_ID}")

    assert body["symphonies"] == []
    assert symphony_entries(body["state"]) == {}


def test_a_failed_trigger_read_leaves_every_last_trigger_null(monkeypatch, tmp_path):
    built = open_two_accounts(monkeypatch, tmp_path)
    seed_captured_exit_trigger()
    before = built.body()
    assert symphony_entries(before["state"])[CORPORATE_ID]["last_trigger"] is not None, (
        "fixture sanity: the stored exit row reaches the symphony's last_trigger"
    )
    fail_triggers(monkeypatch)

    after = built.body()

    entries = symphony_entries(after["state"])
    assert entries, "fixture sanity: symphonies served"
    assert all(entry["last_trigger"] is None for entry in entries.values())


@pytest.mark.parametrize("exc_type", CAUGHT_EXCEPTIONS, ids=lambda t: t.__name__)
def test_a_symphony_whose_analytics_reads_fail_serves_null_legs_and_null_card_figures(
    monkeypatch, tmp_path, exc_type
):
    built = open_two_accounts(monkeypatch, tmp_path)
    fail_symphony_helpers(
        monkeypatch,
        exc_type,
        symphony_name=built.record["entries"][CORPORATE_ID]["name"],
        helpers=tuple(helper for _leg, helper in PER_SYMPHONY_HELPERS),
    )

    body = built.body()

    entry = body["bot_state"][CORPORATE_ID]
    for leg, _helper in PER_SYMPHONY_HELPERS:
        assert entry[leg] == NULL_LEGS, f"{leg} of the failing symphony"
    card = next(c for c in body["symphonies"] if c["id"] == CORPORATE_ID)
    assert all(card[key] is None for key in CARD_LEG_KEYS), "a card figure was not null"
    differing = differing_keys(comparable(body), comparable(golden_for("open_analytics_fail")))
    assert not differing, (
        f"{exc_type.__name__} is served differently from the reference: {differing}"
    )


@pytest.mark.parametrize("exc_type", CAUGHT_EXCEPTIONS, ids=lambda t: t.__name__)
@pytest.mark.parametrize(
    "leg,helper", PER_SYMPHONY_HELPERS, ids=[h for _l, h in PER_SYMPHONY_HELPERS]
)
def test_one_failing_analytics_read_nulls_only_its_own_leg_of_one_symphony(
    monkeypatch, tmp_path, exc_type, leg, helper
):
    built = open_two_accounts(monkeypatch, tmp_path)
    before = built.body()
    fail_symphony_helpers(
        monkeypatch,
        exc_type,
        symphony_name=built.record["entries"][CORPORATE_ID]["name"],
        helpers=(helper,),
    )

    after = built.body()

    assert before["bot_state"][CORPORATE_ID][leg] != NULL_LEGS, (
        "fixture sanity: the leg has figures"
    )
    assert after["bot_state"][CORPORATE_ID][leg] == NULL_LEGS
    for other_leg, _name in PER_SYMPHONY_HELPERS:
        if other_leg != leg:
            assert (
                after["bot_state"][CORPORATE_ID][other_leg]
                == before["bot_state"][CORPORATE_ID][other_leg]
            ), f"{other_leg} changed when only {leg} failed"
    for key, entry in symphony_entries(after["bot_state"]).items():
        if key != CORPORATE_ID:
            assert entry == before["bot_state"][key], f"another symphony changed: {key}"


def test_parabolic_velocity_is_the_return_difference_only_when_both_returns_exist(
    monkeypatch, tmp_path
):
    built = build("open_prev_return_cases", monkeypatch, tmp_path)

    body = built.body()

    stored = symphony_entries(built.record["entries"])
    served = symphony_entries(body["bot_state"])
    saw_difference = saw_null = 0
    for key, entry in stored.items():
        current, previous = entry.get("current_return"), entry.get("prev_return")
        if current is None or previous is None:
            assert served[key]["para_velocity"] is None, f"{key}: one return is absent"
            saw_null += 1
        else:
            assert served[key]["para_velocity"] == pytest.approx(current - previous, abs=1e-6)
            saw_difference += 1
    assert saw_difference >= 1 and saw_null >= 2, "fixture sanity: all three cases are present"


def test_a_failed_panels_read_serves_the_could_not_read_payload(monkeypatch, tmp_path):
    built = build("open_panels_fail", monkeypatch, tmp_path)

    body = built.body()

    assert body["dashboard_panels"] == app_module._panels_unreadable()


def test_two_live_polls_under_one_clock_are_equal(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    first, second = built.body(), built.body()

    assert comparable(first) == comparable(second)
    assert first["next_run_seconds"] == second["next_run_seconds"]
    assert first["meta"]["clock_et"] == second["meta"]["clock_et"]


# ---------------------------------------------------------------------------
# The close-snapshot branch
# ---------------------------------------------------------------------------


def _derive_module():
    spec = importlib.util.spec_from_file_location(
        "state_poll_derive_records", FIXTURE_FOLDER / "derive_records.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_derived_records_are_what_the_derivation_writes():
    derive = _derive_module()
    for name, record in derive.derive_all().items():
        written = (FIXTURE_FOLDER / name).read_bytes().decode("utf-8")
        assert written == derive.serialize(record), f"{name} drifted from its derivation"


def test_the_derivation_names_the_fields_the_route_defaults():
    derive = _derive_module()
    assert tuple(app_module._FROZEN_SYM_DEFAULTS) == derive.FROZEN_DEFAULT_KEYS


def _snapshot_places(body: dict, symphony_id: str) -> dict[str, dict]:
    """Every place the close-snapshot body carries one symphony's entry."""
    places = {
        "state": body["state"][symphony_id],
        "bot_state": body["bot_state"][symphony_id],
    }
    for entries in (body["accounts_map"] or {}).values():
        for entry in entries:
            if entry.get("id") == symphony_id:
                places["accounts_map"] = entry
    return places


@pytest.mark.parametrize("key", list(app_module._FROZEN_SYM_DEFAULTS))
def test_a_missing_snapshot_field_is_served_at_its_default_everywhere_the_entry_appears(
    monkeypatch, tmp_path, key
):
    built = build("closed_missing_defaults", monkeypatch, tmp_path)
    stored = {e["id"]: e for entries in built.record["accounts_map"].values() for e in entries}
    assert key not in stored[FEAVERD_ID], "fixture sanity: the record lacks the field"

    body = built.body()

    places = _snapshot_places(body, FEAVERD_ID)
    assert set(places) == {"state", "bot_state", "accounts_map"}
    for where, entry in places.items():
        assert entry[key] == app_module._FROZEN_SYM_DEFAULTS[key], f"{key} in {where}"


@pytest.mark.parametrize("key", list(app_module._FROZEN_SYM_DEFAULTS))
def test_a_null_snapshot_field_stays_null_everywhere_the_entry_appears(monkeypatch, tmp_path, key):
    built = build("closed_null_defaults", monkeypatch, tmp_path)

    body = built.body()

    for where, entry in _snapshot_places(body, FEAVERD_ID).items():
        assert key in entry and entry[key] is None, f"{key} in {where}"


def test_a_snapshot_field_with_a_value_is_served_with_that_value(monkeypatch, tmp_path):
    built = build("closed_frozen_two_accounts", monkeypatch, tmp_path)
    stored = {e["id"]: e for entries in built.record["accounts_map"].values() for e in entries}[
        FEAVERD_ID
    ]
    carried = [key for key in app_module._FROZEN_SYM_DEFAULTS if stored.get(key) is not None]
    assert len(carried) >= 3, "fixture sanity: the stored entry carries values"

    body = built.body()

    for where, entry in _snapshot_places(body, FEAVERD_ID).items():
        for key in carried:
            assert entry[key] == stored[key], f"{key} in {where}"


def test_each_account_list_is_in_ascending_lower_cased_name_order(monkeypatch, tmp_path):
    built = build("closed_frozen_two_accounts", monkeypatch, tmp_path)
    stored = built.record["accounts_map"]
    assert any([e["id"] for e in entries] != name_order(entries) for entries in stored.values()), (
        "fixture sanity: the record is not stored in name order"
    )

    body = built.body()

    assert set(body["accounts_map"]) == set(stored)
    for account, entries in stored.items():
        assert [e["id"] for e in body["accounts_map"][account]] == name_order(entries), account


def test_an_entry_with_no_name_is_placed_by_its_lower_cased_id(monkeypatch, tmp_path):
    built = build("closed_unnamed_entry", monkeypatch, tmp_path)
    stored = built.record["accounts_map"]
    placed = [e for entries in stored.values() for e in entries if "name" not in e]
    assert [e["id"] for e in placed] == [REASONABILISTS_ID], "fixture sanity: one unnamed entry"
    assert any([e["id"] for e in entries] != name_order(entries) for entries in stored.values()), (
        "fixture sanity: the stored order differs from the name order"
    )

    body = built.body()

    for account, entries in stored.items():
        assert [e["id"] for e in body["accounts_map"][account]] == name_order(entries), account


def test_a_snapshot_without_an_account_map_serves_null(monkeypatch, tmp_path):
    built = build("closed_no_accounts_map", monkeypatch, tmp_path)

    body = built.body()

    assert "accounts_map" in body and body["accounts_map"] is None


def test_a_failed_guard_alpha_read_leaves_every_entry_without_guard_alpha(monkeypatch, tmp_path):
    built = closed_frozen_two_accounts(monkeypatch, tmp_path)
    stored = {e["id"]: e for entries in built.record["accounts_map"].values() for e in entries}
    assert stored[CORPORATE_ID]["triggered"] and stored[FEAVERD_ID]["triggered"], (
        "fixture sanity: triggered entries make the read run"
    )
    seed_captured_exit_trigger()
    found = database_module.get_guard_alpha_by_symphony([CORPORATE_ID])
    assert found.get(CORPORATE_ID) is not None, "fixture sanity: the read would find a figure"
    fail_guard_alpha(monkeypatch)

    body = built.body()

    for where in ("state", "bot_state"):
        for entry in symphony_entries(body[where]).values():
            assert "guard_alpha" not in entry, where


def test_a_symphony_no_longer_held_is_left_out_of_every_place_a_snapshot_lists_it(
    monkeypatch, tmp_path
):
    from tests.app.test_no_longer_held_surfaces import STALE_ID

    built = build("closed_no_longer_held", monkeypatch, tmp_path)

    body = built.body()

    assert STALE_ID not in body["state"]
    assert STALE_ID not in body["bot_state"]
    assert STALE_ID not in {e.get("id") for es in (body["accounts_map"] or {}).values() for e in es}
    assert STALE_ID not in {card["id"] for card in body["symphonies"]}


def test_the_held_snapshot_a_poll_sorted_is_the_one_the_page_reads_next(monkeypatch, tmp_path):
    """The view is held per snapshot and shared with `GET /`: a poll, the page, a poll again read
    one snapshot, with its defaults filled and its lists in name order each time."""
    built = build("closed_missing_defaults", monkeypatch, tmp_path)

    first = built.body()
    built.page()
    second = built.body()

    assert comparable(first) == comparable(second)
    assert not differing_keys(comparable(second), comparable(golden_for("closed_missing_defaults")))
    stored = built.record["accounts_map"]
    for account, entries in stored.items():
        assert [e["id"] for e in second["accounts_map"][account]] == name_order(entries), account
    for key in app_module._FROZEN_SYM_DEFAULTS:
        assert (
            _snapshot_places(second, FEAVERD_ID)["accounts_map"][key]
            == (app_module._FROZEN_SYM_DEFAULTS[key])
        )


# ---------------------------------------------------------------------------
# The sort parameters are not read
# ---------------------------------------------------------------------------


def _same_as_without(built: StateWorld, query: str, base: str = "/api/state") -> None:
    bare = built.get(base)
    assert bare.status_code == 200
    joined = f"{base}&{query}" if "?" in base else f"{base}?{query}"
    asked = built.get(joined)
    assert asked.status_code == 200, f"{query}: answered {asked.status_code}, never a refusal"
    assert comparable(asked.get_json()) == comparable(bare.get_json()), (
        f"{query}: the answer differs from the one without the parameters"
    )


@pytest.mark.parametrize("label", SORT_QUERIES)
def test_a_close_snapshot_ignores_every_sort_parameter(monkeypatch, tmp_path, label):
    built = build("closed_frozen_two_accounts", monkeypatch, tmp_path)

    _same_as_without(built, SORT_QUERIES[label])

    stored = built.record["accounts_map"]
    body = built.body(f"/api/state?{SORT_QUERIES[label]}")
    for account, entries in stored.items():
        assert [e["id"] for e in body["accounts_map"][account]] == name_order(entries), (
            f"{label}: {account} is not in name order"
        )


# The first world holds a captured entry that lacks `stop_trigger`, which the retired table could
# not render, so its table was always the empty fallback; the second holds two complete entries,
# so the sort changed the table there. The parameters must change nothing in either.
@pytest.mark.parametrize("world_name", ["open_two_accounts", "open_with_snapshot_key"])
@pytest.mark.parametrize("label", SORT_QUERIES)
def test_the_live_branch_ignores_every_sort_parameter(monkeypatch, tmp_path, label, world_name):
    built = build(world_name, monkeypatch, tmp_path)

    _same_as_without(built, SORT_QUERIES[label])


def test_a_sort_parameter_beside_an_account_filter_changes_nothing(monkeypatch, tmp_path):
    built = build("open_two_accounts", monkeypatch, tmp_path)

    _same_as_without(built, "sortCol=status", f"/api/state?account={built.first_account_id}")


def test_the_waiting_body_ignores_a_sort_parameter(monkeypatch, tmp_path):
    built = build("no_state", monkeypatch, tmp_path)

    _same_as_without(built, "sortCol=status")
    assert built.body("/api/state?sortCol=status")["status"] == "waiting"


def test_a_closed_market_without_a_snapshot_ignores_a_sort_parameter(monkeypatch, tmp_path):
    built = build("closed_no_snapshot_two_accounts", monkeypatch, tmp_path)

    _same_as_without(built, "sortCol=mc_prob&sortDir=desc")
