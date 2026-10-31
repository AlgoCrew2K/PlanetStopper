"""The AI Advisor Overview's advisor observations: one status line per producer, the history
of each behind a closed expander.

Subject: ``GET /ai-advisor`` over a real temp database whose rows are written by the real
writer (``tests/_advisor_observation_records.py``; cases in
``tests/fixtures/ai_advisor/ux_overhaul_02/observations.json``), with the captured droplet
roster behind the display names.

The markup contract the tests read (every hook a ``data-testid``):

* ``advisor-observations-section``: the section, inside the Overview panel and the page wrapper;
* ``obs-producer`` (``data-role`` = the producer's role): one per role with rows, in the order
  of the app's role list, holding a status line: ``obs-producer-label`` (a ``.section-header``),
  ``obs-producer-verdict``, ``obs-producer-time``, ``obs-producer-total``;
* ``obs-producer-history``: a closed ``<details>`` inside the producer, holding up to 50
  ``obs-row`` elements (``data-obs-id``), newest first, each with ``obs-time``,
  ``obs-symphony``, ``obs-verdict`` and ``obs-summary``, and an ``obs-producer-history-note``
  that reads "<shown> of <total>" when the total is larger than the rows listed;
* ``obs-empty``: the empty text; ``observations-unavailable``: the failure text.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

import pytest

import analytics
import app as app_module
import database
from advisors.prism_render import humanize_obs_preview
from tests._advisor_observation_records import FIRST_MOMENT, case_row, store
from tests._advisor_overview_prism import store_prism_row
from tests._ux_truth_support import captured_symphonies
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb

_EMPTY_TEXT = "No observations from advisors at this time."
_FAILURE_TEXT = "Observations could not be loaded."
_NOT_RECORDED = "verdict not recorded"
_HISTORY_LIMIT = 50
_OC = "OVERFITTING_CONSCIENCE"
_SC = "SPEC_CRITIC"
_ROLE_ORDER = (_OC, _SC)


def _words(case: str) -> str:
    """A case's stored verdict as the page prints it: through the one mapping, never raw."""
    return app_module.advisor_words(case_row(case)["verdict"])


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "observation_lines.db"))
    database.init_db()
    database.run_migrations()
    return database.DB_FILE


def _symphony_ids(count: int = 3) -> list[tuple[str, str]]:
    """(stored key, live display name) of the first captured symphonies."""
    return [(database.normalize_name(name), name) for _, name in captured_symphonies()[:count]]


def _store_cycling(case: str, times: int) -> list[tuple[int, str, str]]:
    """``times`` rows of ``case``, each for the next of three symphonies in turn, one minute
    apart; ``(id, created_at, symphony key)`` oldest first."""
    ids = _symphony_ids()
    out = []
    for k in range(times):
        key = ids[k % len(ids)][0]
        ((row_id, created_at),) = store(case, symphony_id=key, start=_minutes_in(k))
        out.append((row_id, created_at, key))
    return out


def _minutes_in(minutes: int) -> datetime:
    return FIRST_MOMENT + timedelta(minutes=minutes)


def _page(client) -> tuple[str, pb.Node]:
    html = w.get_page(client)
    return html, pb.parse_html(html)


def _producers(root: pb.Node) -> dict[str, pb.Node]:
    found = {n.attrs.get("data-role"): n for n in root.find_all("obs-producer")}
    return found


def _line(root: pb.Node, role: str) -> pb.Node:
    lines = _producers(root)
    assert role in lines, f"no status line for {role}; the page has {sorted(map(str, lines))}"
    return lines[role]


def _history(root: pb.Node, role: str) -> pb.Node:
    history = _line(root, role).find("obs-producer-history")
    assert history is not None, f"the {role} line has no history expander"
    return history


def _text(node: pb.Node, testid: str) -> str:
    child = node.find(testid)
    assert child is not None, f"no {testid} inside {node.attrs.get('data-role')}"
    return child.text()


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", text)


@pytest.fixture()
def seeded(isolated_db, client):
    """60 Overfitting Conscience rows and 3 Spec Critic rows, over the captured roster."""
    w.seed_roster()
    oc = _store_cycling("guardrail_clear", 60)
    sc = _store_cycling("guardrail_watch", 3)
    return client, oc, sc


# ---------------------------------------------------------------------------
# The status lines
# ---------------------------------------------------------------------------


class TestStatusLines:
    def test_one_line_per_producer_with_label_latest_verdict_time_and_total(self, seeded):
        client, oc, sc = seeded
        _, root = _page(client)
        producers = _producers(root)
        assert list(producers) == list(_ROLE_ORDER)
        expectations = {
            _OC: ("Overfitting Conscience", _words("guardrail_clear"), oc),
            _SC: ("Spec Critic", _words("guardrail_watch"), sc),
        }
        for role, (label, verdict, rows) in expectations.items():
            line = producers[role]
            label_node = line.find("obs-producer-label")
            assert label_node is not None and "section-header" in label_node.classes()
            assert label_node.text() == label
            assert _text(line, "obs-producer-verdict") == verdict
            assert _text(line, "obs-producer-time") == analytics.format_et_stamp(rows[-1][1])
            assert _digits(_text(line, "obs-producer-total")) == [str(len(rows))]

    def test_a_stored_verdict_reads_as_a_word_never_as_its_stored_token(self, seeded):
        client, _, _ = seeded
        _, root = _page(client)
        producers = _producers(root)
        assert _text(producers[_OC], "obs-producer-verdict") == "Clear"
        assert _text(producers[_SC], "obs-producer-verdict") == "Watch"

    def test_no_observation_row_sits_outside_an_expander(self, seeded):
        client, oc, sc = seeded
        _, root = _page(client)
        rows = root.find_all("obs-row")
        assert len(rows) == min(len(oc), _HISTORY_LIMIT) + len(sc)
        for row in rows:
            expander = row.ancestor("obs-producer-history")
            assert expander is not None and expander.tag == "details", (
                f"row {row.attrs.get('data-obs-id')} is outside a details"
            )

    def test_every_expander_starts_closed(self, seeded):
        client, _, _ = seeded
        _, root = _page(client)
        histories = root.find_all("obs-producer-history")
        assert len(histories) == 2
        for history in histories:
            assert history.tag == "details" and "open" not in history.attrs

    def test_the_lines_sit_in_the_overview_panel_inside_the_page_wrapper(self, seeded):
        client, _, _ = seeded
        _, root = _page(client)
        overview, wrapper = root.find("tab-panel-overview"), root.find("advisor-page")
        assert overview is not None and wrapper is not None
        assert "page-wrap" in wrapper.classes()
        lines = root.find_all("obs-producer")
        assert len(lines) == 2
        for line in lines:
            assert line.is_inside(overview) and line.is_inside(wrapper)

    def test_the_section_is_only_in_the_overview_panel_and_not_after_the_wrapper(self, seeded):
        client, _, _ = seeded
        _, root = _page(client)
        sections = root.find_all("advisor-observations-section")
        assert len(sections) == 1
        section = sections[0]
        assert section.is_inside(root.find("tab-panel-overview"))
        assert section.is_inside(root.find("advisor-page"))
        for other in ("correlations", "asset-swaps", "logic-changes", "chat", "retirement"):
            assert not section.is_inside(root.find(f"tab-panel-{other}")), other

    def test_the_guardrail_note_comes_before_the_first_status_line(self, seeded):
        client, _, _ = seeded
        _, root = _page(client)
        order = list(root.walk())
        note = root.find("guardrail-uniform-note")
        assert note is not None
        assert note.is_inside(root.find("advisor-observations-section"))
        first_line = root.find("obs-producer")
        assert order.index(note) < order.index(first_line)

    def test_a_role_without_rows_has_no_line(self, isolated_db, client):
        w.seed_roster()
        _store_cycling("guardrail_watch", 3)
        _, root = _page(client)
        assert list(_producers(root)) == [_SC]

    def test_roles_new_to_the_label_table_read_their_names(self, isolated_db, client):
        w.seed_roster()
        store("add_candidate_role", times=2)
        _, root = _page(client)
        line = _line(root, "ADD_CANDIDATE")
        assert line.find("obs-producer-label").text() == "Add Candidate"

    def test_the_market_prism_read_has_its_own_line_named_market_prism(self, isolated_db, client):
        w.seed_roster()
        store_prism_row()
        _, root = _page(client)
        line = _line(root, "MARKET_PRISM")
        assert line.find("obs-producer-label").text() == "Market Prism"
        assert _digits(_text(line, "obs-producer-total")) == ["1"]

    @pytest.mark.parametrize("case", ["empty_verdict", "null_verdict"])
    def test_an_empty_or_null_latest_verdict_reads_verdict_not_recorded(
        self, isolated_db, client, case
    ):
        w.seed_roster()
        store(case, times=1)
        _, root = _page(client)
        line = _line(root, _SC)
        assert _text(line, "obs-producer-verdict") == _NOT_RECORDED
        assert _digits(_text(line, "obs-producer-total")) == ["1"]

    def test_the_latest_verdict_is_escaped(self, isolated_db, client):
        w.seed_roster()
        store("markup_verdict", times=1)
        html, root = _page(client)
        line = _line(root, _SC)
        assert _text(line, "obs-producer-verdict") == _words("markup_verdict")
        assert "<script>alert(1)</script>" not in html


# ---------------------------------------------------------------------------
# The expanders
# ---------------------------------------------------------------------------


class TestExpanders:
    def test_rows_are_newest_first_with_display_name_verdict_time_and_summary(self, seeded):
        client, oc, _ = seeded
        _, root = _page(client)
        history = _history(root, _OC)
        rows = history.find_all("obs-row")
        newest_first = list(reversed(oc))[:_HISTORY_LIMIT]
        assert [int(r.attrs["data-obs-id"]) for r in rows] == [r[0] for r in newest_first]
        names = dict(_symphony_ids())
        raw = case_row("guardrail_clear")["raw_response"]
        for row, (row_id, created_at, key) in zip(rows, newest_first, strict=True):
            symphony = row.find("obs-symphony")
            assert symphony.text() == names[key].strip(), (
                "the row names the symphony, not its stored key"
            )
            assert key not in symphony.text() or key == names[key]
            assert row.find("obs-time").text() == analytics.format_et_stamp(created_at)
            assert row.find("obs-verdict").text() == _words("guardrail_clear")
            assert row.find("obs-summary").text() == humanize_obs_preview(raw)

    def test_the_overfitting_expander_holds_fifty_rows_and_states_fifty_of_the_total(self, seeded):
        client, oc, sc = seeded
        _, root = _page(client)
        history = _history(root, _OC)
        assert len(history.find_all("obs-row")) == _HISTORY_LIMIT < len(oc)
        note = _text(history, "obs-producer-history-note")
        assert re.search(rf"\b{_HISTORY_LIMIT}\s+of\s+{len(oc)}\b", note), note
        small = _history(root, _SC)
        assert len(small.find_all("obs-row")) == len(sc)
        assert small.find("obs-producer-history-note") is None, "no cap note under the limit"

    def test_a_feature_off_stub_is_in_neither_the_total_nor_the_rows(self, isolated_db, client):
        w.seed_roster()
        store("guardrail_clear", times=5)
        store("feature_off_stub_under_a_shown_role", times=2, start=_minutes_in(30))
        _, root = _page(client)
        line = _line(root, _OC)
        assert _digits(_text(line, "obs-producer-total")) == ["5"]
        assert len(line.find_all("obs-row")) == 5
        assert _text(line, "obs-producer-verdict") == _words("guardrail_clear")

    def test_a_role_with_only_stubs_has_no_line(self, isolated_db, client):
        w.seed_roster()
        store("feature_off_stub_under_a_shown_role", times=2)
        _, root = _page(client)
        assert _producers(root) == {}
        empty = root.find("obs-empty")
        assert empty is not None and empty.text() == _EMPTY_TEXT, "stubs are not observations"


# ---------------------------------------------------------------------------
# Empty and failing
# ---------------------------------------------------------------------------


class TestEmptyAndUnavailable:
    def test_no_rows_reads_the_empty_text_inside_the_overview_panel(self, isolated_db, client):
        _, root = _page(client)
        empty = root.find("obs-empty")
        assert empty is not None and empty.text() == _EMPTY_TEXT
        assert empty.is_inside(root.find("tab-panel-overview"))
        assert root.find_all("obs-producer") == []
        assert root.find("observations-unavailable") is None

    @pytest.mark.parametrize("failing", ["role read", "count"])
    def test_a_failing_read_shows_the_failure_text_and_every_other_section_renders(
        self, seeded, monkeypatch, failing
    ):
        client, _, _ = seeded
        if failing == "role read":
            real = database.get_advisor_observations_for_role

            def read(role, limit=50):
                if role in (_OC, _SC):
                    raise sqlite3.OperationalError("database is locked")
                return real(role, limit=limit)

            monkeypatch.setattr(database, "get_advisor_observations_for_role", read)
        else:

            def count(roles):
                raise sqlite3.OperationalError("database is locked")

            monkeypatch.setattr(
                database, "count_advisor_observations_by_role", count, raising=False
            )
        try:
            response = client.get("/ai-advisor")
        except sqlite3.OperationalError as error:
            pytest.fail(f"the page raised instead of reporting the failed read: {error}")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        root = pb.parse_html(html)
        failure = root.find("observations-unavailable")
        assert failure is not None and failure.text() == _FAILURE_TEXT
        assert failure.is_inside(root.find("tab-panel-overview"))
        assert root.find_all("obs-producer") == []
        assert root.find("obs-empty") is None, "a failed read is not an empty history"
        for present in ("market-prism-block", "advisor-controls", "advisor-grid"):
            assert root.find(present) is not None, present
        panels = [n.attrs["data-tab"] for n in root.walk() if n.attrs.get("role") == "tabpanel"]
        assert sorted(panels) == sorted(w.tab_names(html))
