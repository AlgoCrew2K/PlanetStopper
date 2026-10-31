"""RED tests -- the Correlations tab shows the latest successful retirement run's matrix.

The tab renders the pairs the retirement recommender stored in its run summary (the realized
Composer daily series), with no recomputation and no Composer request at render. Every test seeds a
real temp database through the production writers: the roster is ``database.save_state``, the run
is the REAL ``build_recommendations_with_summary(source="composer")`` over the PM's real captures
(``tests/advisors/_series_captures.py``), and its summary is persisted through
``database.insert_advisor_observation``. The page is then fetched through the Flask test client.

Markup contract (the implementer reads this and the handoff, not the plan):

* ``tab-panel-correlations`` holds, in every state, ``crisis-caveat-banner``.
* Matrix state: ``correlations-basis`` and ``correlations-as-of`` (both ``legend-as-of``),
  ``correlation-matrix-table`` (``thead`` row: corner ``th`` then ``th[data-testid=corr-col-label]``;
  ``tbody`` rows: ``th[data-testid=corr-row-label]`` then ``td`` cells), ``correlations-legend`` and,
  when symphonies were excluded, ``correlations-excluded`` with ``correlations-excluded-item``s.
  Every label carries ``data-sym`` (the Composer hash). Every off-diagonal cell is
  ``td[data-testid=corr-cell]`` with ``data-sym-a``, ``data-sym-b`` (the stored pair, lower hash
  first, the same on both mirror cells) and ``data-n-obs``; a diagonal cell is
  ``td[data-testid=corr-cell-diagonal]``.
* Any other state: ``correlations-state`` with ``data-state`` of ``never_run``,
  ``needs_newer_run``, ``no_matrix`` or ``unavailable`` and no matrix table. In matrix state
  ``correlations-state`` is absent.

Expected values are read from the stored summary, recomputed from the captures, or taken from the
shared producers (``analytics.format_et_stamp``, ``_retirement_exclusion_reason_sentence``); none is
typed from a sample run.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

import analytics
import app as app_module
import database as db_module
from tests.advisors import _series_captures as captures
from tests.app._correlations_view_support import (  # noqa: F401  (fixtures)
    EMPTY_CAPTURE,
    NEVER_RUN_TEXT,
    client,
    collapse,
    correlation_text,
    expected_order,
    get_page,
    grid_of,
    isolated_db,
    live_names,
    load_derived_summary,
    name_override_duplicate,
    name_override_markup,
    name_override_swap,
    pair_key,
    panel_of,
    render_panel,
    seed_run,
    seed_state_only,
    stored_pairs,
    tree_of,
    with_run_at,
    with_run_at_removed,
    with_unmapped_exclusion_reason,
)
from tests.app._tenure_review_job_builders import freeze_app_clock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ET = ZoneInfo("America/New_York")
_GOLDEN_A = "q7WQHwLzt49cMgFBzHsX"
_GOLDEN_B = "qF5ZU7ALjrlhxrGEwsyJ"
_DEEPEST = "n2ooAZTvBRN6ZzpMmWmU"
_LATEST_PRINTED = "Series through"


def _analysed(summary: dict) -> list[str]:
    return list(summary["series_windows"])


def _state_node(panel):
    return panel.find(testid="correlations-state")


# ===========================================================================
# The matrix is the stored run's pairs
# ===========================================================================


class TestMatrixFromTheStoredRun:
    def test_there_is_a_row_and_a_column_per_analysed_symphony_and_a_cell_per_pair(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        grid = grid_of(client)
        n = len(_analysed(summary))
        assert n >= 3, "premise: the real run analyses several symphonies"
        assert len(grid.row_labels) == len(grid.col_labels) == n
        assert all(len(row) == n for row in grid.rows)
        assert len(grid.off_diagonal()) == n * (n - 1)
        assert len(grid.diagonal()) == n

    def test_each_cell_prints_the_stored_correlation_and_carries_the_stored_aligned_days(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        pairs = stored_pairs(summary)
        grid = grid_of(client)
        assert pairs, "premise: the run stored its pairs"
        for cell in grid.off_diagonal():
            stored = pairs[(cell.attrs["data-sym-a"], cell.attrs["data-sym-b"])]
            assert cell.text == correlation_text(stored["correlation"])
            assert cell.attrs["data-n-obs"] == str(stored["n_obs"])

    def test_every_stored_pair_appears_in_both_triangles(self, client, isolated_db, monkeypatch):
        summary = seed_run(monkeypatch)
        grid = grid_of(client)
        for key in stored_pairs(summary):
            assert len(grid.cells_of_pair(*key)) == 2, key

    def test_each_cell_sits_at_the_crossing_of_its_own_two_symphonies(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        grid = grid_of(client)
        for i, row_sym in enumerate(grid.row_syms):
            for j, col_sym in enumerate(grid.col_syms):
                cell = grid.rows[i][j]
                if i == j:
                    assert cell.attrs.get("data-testid") == "corr-cell-diagonal"
                    assert cell.text == "1.00"
                else:
                    assert cell.attrs.get("data-testid") == "corr-cell"
                    assert (cell.attrs["data-sym-a"], cell.attrs["data-sym-b"]) == pair_key(
                        row_sym, col_sym
                    )

    def test_rows_and_columns_list_the_same_symphonies_in_name_then_hash_order(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        grid = grid_of(client)
        want = expected_order(live_names(summary))
        assert grid.row_syms == want
        assert grid.col_syms == want

    def test_the_excluded_empty_capture_has_neither_a_row_nor_a_column(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        grid = grid_of(client)
        assert EMPTY_CAPTURE not in grid.row_syms
        assert EMPTY_CAPTURE not in grid.col_syms
        assert all(
            EMPTY_CAPTURE not in (c.attrs["data-sym-a"], c.attrs["data-sym-b"])
            for c in grid.off_diagonal()
        )

    def test_a_pair_whose_stored_correlation_is_undefined_prints_the_word_not_a_number(
        self, client, isolated_db, monkeypatch
    ):
        early = datetime(2026, 3, 1, 12, 0, tzinfo=_ET)
        summary = seed_run(monkeypatch, per_symphony_now_et={"hvPiGP1O7AHfutHE3Fjy": early})
        grid = grid_of(client)
        cells = grid.cells_of_pair("hvPiGP1O7AHfutHE3Fjy", _GOLDEN_A)
        assert (
            stored_pairs(summary)[pair_key("hvPiGP1O7AHfutHE3Fjy", _GOLDEN_A)]["correlation"]
            is None
        )
        assert len(cells) == 2
        assert all(c.text == "undefined" for c in cells)
        assert all(c.attrs["data-n-obs"] == "0" for c in cells)


class TestBasisLine:
    def test_the_basis_is_the_runs_label_verbatim_and_the_retirement_tab_prints_the_same(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        tree = tree_of(get_page(client))
        panel = panel_of(tree)
        basis = panel.find(testid="correlations-basis")
        assert basis is not None
        assert summary["basis_label"] in basis.text
        retirement = tree.find(testid="retirement-last-run-basis")
        assert retirement is not None
        assert summary["basis_label"] in retirement.text

    def test_the_basis_line_and_the_as_of_use_the_shared_as_of_style(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        panel = render_panel(client)
        for testid in ("correlations-basis", "correlations-as-of"):
            node = panel.find(testid=testid)
            assert node is not None and "legend-as-of" in node.classes, testid


class TestLaterAndEarlierRuns:
    def test_a_later_run_that_analysed_more_symphonies_replaces_the_matrix(
        self, client, isolated_db, monkeypatch
    ):
        subset = [s for s in captures.capture_ids() if s != EMPTY_CAPTURE][:5]
        seed_run(monkeypatch, ids=subset)
        assert len(grid_of(client).row_labels) == 5
        later = seed_run(monkeypatch)
        grid = grid_of(client)
        assert len(grid.row_labels) == len(grid.col_labels) == len(_analysed(later)) > 5

    def test_a_reload_after_a_new_run_shows_the_new_runs_pairs(
        self, client, isolated_db, monkeypatch
    ):
        first = seed_run(monkeypatch, days=250)
        before = {
            (c.attrs["data-sym-a"], c.attrs["data-sym-b"]): c.attrs["data-n-obs"]
            for c in grid_of(client).off_diagonal()
        }
        second = seed_run(monkeypatch, days=181)
        after = {
            (c.attrs["data-sym-a"], c.attrs["data-sym-b"]): c.attrs["data-n-obs"]
            for c in grid_of(client).off_diagonal()
        }
        assert before != after, "premise: the lookback changes some pair's aligned days"
        assert after == {k: str(p["n_obs"]) for k, p in stored_pairs(second).items()}
        assert before == {k: str(p["n_obs"]) for k, p in stored_pairs(first).items()}


# ===========================================================================
# Reading is cheap, local and read-only
# ===========================================================================


def _row_snapshot(db_path: str) -> dict[str, list]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "observations": conn.execute(
                "SELECT * FROM advisor_observations ORDER BY id"
            ).fetchall(),
            "state": conn.execute("SELECT data FROM bot_state").fetchall(),
        }
    finally:
        conn.close()


class TestRenderIsReadOnlyAndLocal:
    def test_rendering_changes_no_stored_row_and_the_swap_and_logic_lists_are_the_history_lists(
        self, client, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(analytics, "list_available_symphonies", lambda _h: ["swap-a", "swap-b"])
        monkeypatch.setattr(analytics, "get_history_with_cache_invalidation", lambda **_kw: {})
        monkeypatch.setattr("advisors.asset_swap_engine._has_composer_key", lambda: True)
        seed_run(monkeypatch)
        before = _row_snapshot(db_module._db_file())

        for _ in range(3):
            tree = tree_of(get_page(client))

        assert _row_snapshot(db_module._db_file()) == before
        for testid in ("swap-symphony-select", "tweak-symphony-select"):
            select = tree.find(testid=testid)
            options = [o.attrs.get("value", "") for o in select.find_all("option")]
            assert [o for o in options if o] == ["swap-a", "swap-b"], testid

    def test_rendering_makes_no_composer_request_even_with_the_market_open(
        self, client, isolated_db, monkeypatch
    ):
        import advisors.composer_series_client as series_client  # noqa: PLC0415

        seed_run(monkeypatch)
        outbound: list[str] = []

        def refuse_request(session, method, url, *args, **kwargs):
            outbound.append(f"{method} {url}")
            raise requests.ConnectionError("outbound request refused in test")

        def refuse_fetch(*_args, **_kwargs):
            outbound.append("fetch_symphony_daily_returns")
            raise AssertionError("render fetched a series")

        monkeypatch.setattr(requests.sessions.Session, "request", refuse_request)
        monkeypatch.setattr(series_client, "fetch_symphony_daily_returns", refuse_fetch)
        monkeypatch.setattr(app_module, "get_market_state", lambda _now: "open")

        for _ in range(3):
            panel = render_panel(client)
            assert panel.find(testid="correlation-matrix-table") is not None
        assert outbound == []

    def test_rendering_does_not_recompute_correlations_or_read_post_mortem_series(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("the render recomputed instead of reading the stored run")

        monkeypatch.setattr(
            "advisors.correlation_diagnostic.compute_pairwise_correlations", forbidden
        )
        monkeypatch.setattr(analytics, "compute_per_symphony_returns", forbidden)
        grid = grid_of(client)
        assert len(grid.off_diagonal()) == len(_analysed(summary)) * (len(_analysed(summary)) - 1)

    def test_the_route_reaches_the_matrix_view_and_no_series_client_through_its_call_graph(self):
        from tests.security.test_retirement_action_no_trade_boundary import (  # noqa: PLC0415
            _collect_transitive_local_call_subtrees,
        )

        tree = ast.parse((REPO_ROOT / "app.py").read_text(encoding="utf-8"))
        reached = _collect_transitive_local_call_subtrees(tree, "ai_advisor_tab")
        names = {getattr(node, "name", None) for node in reached}
        assert "_retirement_correlation_matrix_view" in names, "the route never builds the view"
        for node in reached:
            text = ast.unparse(node)
            for forbidden in ("composer_series_client", "fetch_symphony_daily_returns"):
                assert forbidden not in text, f"{getattr(node, 'name', '?')} reaches {forbidden}"


class TestUnreadableRun:
    def test_a_failed_read_of_the_run_summaries_shows_the_unavailable_state_and_the_rest_renders(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        real = db_module.get_advisor_observations_for_role

        def flaky(role, *args, **kwargs):
            if role == captures.SUMMARY_ROLE:
                raise sqlite3.OperationalError("disk I/O error")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", flaky)
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        tree = tree_of(resp.get_data(as_text=True))
        panel = panel_of(tree)

        state = _state_node(panel)
        assert state is not None and state.attrs.get("data-state") == "unavailable"
        assert "unavailable" in state.text.lower()
        assert panel.find(testid="correlation-matrix-table") is None
        assert NEVER_RUN_TEXT not in state.text, "a failed read is not 'never run'"
        for testid in ("tab-panel-overview", "tab-panel-asset-swaps", "tab-panel-chat"):
            assert tree.find(testid=testid) is not None, testid


# ===========================================================================
# As-of: the last series date and the run time, in ET
# ===========================================================================


def _expected_as_of(summary: dict) -> str:
    through = max(w["window_last"] for w in summary["series_windows"].values())
    run_at = summary.get("run_at")
    run = f"run {analytics.format_et_stamp(run_at)}" if run_at else "run time not recorded"
    return f"{_LATEST_PRINTED} {through} · {run}"


class TestAsOf:
    def test_the_as_of_names_the_last_series_date_and_the_runs_time_in_et(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        text = collapse(render_panel(client).find(testid="correlations-as-of").text)
        assert text == _expected_as_of(summary)
        assert text.endswith(" ET")
        recomputed = max(
            captures.parsed(sid).last_date for sid in _analysed(summary)
        )  # from the captures, not the stored row
        assert f"{_LATEST_PRINTED} {recomputed} " in text

    def test_the_as_of_names_the_latest_date_and_each_cell_states_its_own_last_date(
        self, client, isolated_db, monkeypatch
    ):
        slow = "Gpaw3IhZghQPRE6AdEKx"
        other = "hvPiGP1O7AHfutHE3Fjy"
        earlier = datetime(2026, 10, 15, 12, 0, tzinfo=_ET)
        summary = seed_run(monkeypatch, per_symphony_now_et={slow: earlier})
        windows = summary["series_windows"]
        assert windows[slow]["window_last"] < windows[other]["window_last"], "premise"

        panel = render_panel(client)
        assert collapse(panel.find(testid="correlations-as-of").text) == _expected_as_of(summary)
        assert windows[other]["window_last"] in panel.find(testid="correlations-as-of").text

        grid = grid_of(client)
        stored = stored_pairs(summary)[pair_key(slow, other)]
        assert stored["window_last"] == windows[slow]["window_last"]
        for cell in grid.cells_of_pair(slow, other):
            assert f"{stored['window_first']} to {stored['window_last']}" in cell.attrs["title"]

    def test_the_nightly_slots_utc_instant_reads_as_the_previous_evenings_eastern_time(
        self, client, isolated_db, monkeypatch
    ):
        """03:45 UTC on 2026-10-22 is 23:45 EDT on 2026-10-21."""
        _recs, produced = captures.run_over_captures(monkeypatch)
        summary = with_run_at(produced, "2026-10-22T03:45:00Z")
        seed_state_only(summary)
        text = collapse(render_panel(client).find(testid="correlations-as-of").text)
        assert text == _expected_as_of(summary)
        assert text.endswith("run 2026-10-21 23:45 ET"), text

    def test_a_run_without_a_recorded_time_says_so(self, client, isolated_db, monkeypatch):
        _recs, produced = captures.run_over_captures(monkeypatch)
        summary = with_run_at_removed(produced)
        seed_state_only(summary)
        text = collapse(render_panel(client).find(testid="correlations-as-of").text)
        assert text == _expected_as_of(summary)
        assert "run time not recorded" in text

    def test_the_as_of_does_not_follow_the_render_time(self, client, isolated_db, monkeypatch):
        summary = seed_run(monkeypatch)
        texts = []
        for instant in (
            datetime(2026, 11, 2, 10, 0, tzinfo=_ET),
            datetime(2027, 3, 14, 23, 30, tzinfo=_ET),
        ):
            freeze_app_clock(monkeypatch, instant)
            texts.append(collapse(render_panel(client).find(testid="correlations-as-of").text))
        assert texts[0] == texts[1] == _expected_as_of(summary)
        assert "2026-11-02" not in texts[0] and "2027-03-14" not in texts[0]


# ===========================================================================
# Labels: numbered, full names, distinct, escaped
# ===========================================================================


def _norm(text: str) -> str:
    """Collapse whitespace and drop any before a full stop (a real name ends in a space)."""
    return re.sub(r"\s+\.", ".", collapse(text))


def _label_checks(grid, summary: dict, names: dict[str, str]) -> None:
    order = expected_order(names)
    assert grid.row_syms == order and grid.col_syms == order
    for number, sid in enumerate(order, start=1):
        name = collapse(names[sid])
        windows = summary["series_windows"][sid]
        row = grid.row_labels[number - 1]
        col = grid.col_labels[number - 1]

        assert row.text == f"{number} {name}", sid
        assert col.text == str(number), sid

        aria = (
            f"{name}. Matrix window: {windows['window_first']} to {windows['window_last']} "
            f"({windows['window_days']} returns). Full series: {windows['series_first']} to "
            f"{windows['series_last']} ({windows['series_points']} points)."
        )
        assert _norm(row.attrs["aria-label"]) == _norm(aria), sid
        assert row.attrs["title"].startswith(row.attrs["aria-label"]), sid
        assert sid in row.attrs["title"], sid
        assert row.attrs.get("tabindex") == "0" and col.attrs.get("tabindex") == "0"

        assert name in collapse(col.attrs["title"]) and sid in col.attrs["title"], sid
        assert name in collapse(col.attrs["aria-label"]), sid
        if names[sid] != sid:
            assert sid not in row.attrs["aria-label"] and sid not in col.attrs["aria-label"]
            assert sid not in row.text and sid not in col.text


class TestLabels:
    def test_each_row_has_a_number_and_its_full_name_and_each_column_its_number(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        _label_checks(grid_of(client), summary, live_names(summary))

    def test_near_duplicate_real_names_stay_distinct_in_full(
        self, client, isolated_db, monkeypatch
    ):
        """Two real names that collided under an 18-character cut: "Planet of the Golden Age:
        Arrival of the NewRunners" and "Planet of the Golden Age (Buy Copy)"."""
        summary = seed_run(monkeypatch)
        grid = grid_of(client)
        first, second = (collapse(captures.capture_name(s)) for s in (_GOLDEN_A, _GOLDEN_B))
        assert first[:18] == second[:18], "premise: the old cut made these two identical"
        row_texts = [r.text for r in grid.row_labels]
        assert any(t.endswith(first) for t in row_texts)
        assert any(t.endswith(second) for t in row_texts)
        col_texts = [c.text for c in grid.col_labels]
        assert len(set(col_texts)) == len(col_texts) == len(_analysed(summary))
        assert "…" not in " ".join(row_texts)

    def test_no_composer_hash_is_visible_text_when_the_name_resolves(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        text = render_panel(client).text
        for sid in _analysed(summary) + [EMPTY_CAPTURE]:
            assert sid not in text, sid

    def test_two_symphonies_with_the_identical_display_name_still_have_different_labels(
        self, client, isolated_db, monkeypatch
    ):
        overrides = name_override_duplicate(_GOLDEN_A, _GOLDEN_B)
        summary = seed_run(monkeypatch, names=overrides)
        grid = grid_of(client)
        names = live_names(summary, overrides)
        assert names[_GOLDEN_A] == names[_GOLDEN_B]
        _label_checks(grid, summary, names)
        rows = {r.attrs["data-sym"]: r for r in grid.row_labels}
        cols = {c.attrs["data-sym"]: c for c in grid.col_labels}
        assert rows[_GOLDEN_A].text != rows[_GOLDEN_B].text
        assert cols[_GOLDEN_A].text != cols[_GOLDEN_B].text
        assert rows[_GOLDEN_A].attrs["title"] != rows[_GOLDEN_B].attrs["title"]

    def test_markup_in_a_name_shows_literally_and_is_never_interpreted(
        self, client, isolated_db, monkeypatch
    ):
        overrides = name_override_markup(_GOLDEN_A)
        summary = seed_run(monkeypatch, names=overrides)
        body = get_page(client)
        grid = grid_of(client)
        names = live_names(summary, overrides)
        _label_checks(grid, summary, names)
        label = next(r for r in grid.row_labels if r.attrs["data-sym"] == _GOLDEN_A)
        assert "<b>bold</b> & Co" in label.text
        assert "<b>bold</b>" not in body
        assert "&lt;b&gt;bold&lt;/b&gt;" in body
        assert any("<b>bold</b> & Co" in c.attrs["title"] for c in grid.off_diagonal())

    def test_a_symphony_no_longer_in_the_live_state_is_labelled_by_its_hash(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        gone = _GOLDEN_B
        state = db_module.load_state()
        del state[gone]
        db_module.save_state(state)

        grid = grid_of(client)
        names = {**live_names(summary), gone: gone}
        order = expected_order(names)
        assert grid.row_syms == order and grid.col_syms == order
        number = order.index(gone) + 1
        assert grid.row_labels[number - 1].text == f"{number} {gone}"
        assert gone in grid.col_labels[number - 1].attrs["aria-label"]

    def test_swapping_two_display_names_moves_the_labels_but_not_what_each_cell_is(
        self, client, isolated_db, monkeypatch
    ):
        ids = sorted(live_names(seed_run(monkeypatch)), key=lambda s: captures.capture_name(s))
        first, last = ids[0], ids[-1]

        def snapshot():
            grid = grid_of(client)
            cells = {}
            for cell in grid.off_diagonal():
                key = (cell.attrs["data-sym-a"], cell.attrs["data-sym-b"])
                cells.setdefault(key, set()).add((cell.text, cell.attrs["data-n-obs"]))
            return grid, cells

        grid_before, cells_before = snapshot()
        captures.seed_roster(names=name_override_swap(first, last))
        grid_after, cells_after = snapshot()

        assert grid_before.row_syms != grid_after.row_syms, "premise: the swap reorders the rows"
        assert cells_before == cells_after
        for grid in (grid_before, grid_after):
            for i, row_sym in enumerate(grid.row_syms):
                for j, col_sym in enumerate(grid.col_syms):
                    if i != j:
                        cell = grid.rows[i][j]
                        assert (cell.attrs["data-sym-a"], cell.attrs["data-sym-b"]) == pair_key(
                            row_sym, col_sym
                        )

    def test_a_symphony_with_no_series_has_no_label_and_no_dates_beside_its_exclusion(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        panel = render_panel(client)
        grid = grid_of(client)
        assert EMPTY_CAPTURE not in grid.row_syms + grid.col_syms
        items = panel.find_all(testid="correlations-excluded-item")
        (item,) = [i for i in items if captures.capture_name(EMPTY_CAPTURE) in i.text]
        assert not re.search(r"\d{4}-\d{2}-\d{2}", item.text + item.attrs.get("title", ""))


# ===========================================================================
# The excluded list
# ===========================================================================


def _excluded_texts(panel) -> list[str]:
    return [collapse(i.text) for i in panel.find_all(testid="correlations-excluded-item")]


class TestExcludedSymphonies:
    def test_an_excluded_symphony_is_named_with_its_reason_as_a_sentence(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        panel = render_panel(client)
        name = collapse(captures.capture_name(EMPTY_CAPTURE))
        assert _excluded_texts(panel) == [
            f"Not in the matrix: {name}. Its Composer series has fewer than 2 daily returns."
        ]
        assert panel.find(testid="correlations-excluded") is not None

    def test_every_reason_reads_its_sentence_from_the_shared_mapping(
        self, client, isolated_db, monkeypatch
    ):
        no_account, failing = "5XjzXjdGnjh99MIsdM97", "8FAXAnQmYi1INDubazeC"
        summary = seed_run(monkeypatch, accounts={no_account: None}, errors={failing: "HTTP 503"})
        reasons = {e["symphony_id"]: e["reason"] for e in summary["symphonies_without_series"]}
        assert reasons[no_account] == "no_account"
        assert reasons[failing].endswith("HTTP 503")
        texts = _excluded_texts(render_panel(client))
        assert len(texts) == len(reasons) == 3
        for sid, reason in reasons.items():
            name = collapse(captures.capture_name(sid))
            sentence = app_module._retirement_exclusion_reason_sentence(reason)
            assert f"Not in the matrix: {name}. {sentence}" in texts
        assert any("HTTP 503" in t for t in texts)

    def test_a_run_that_excluded_nothing_shows_no_excluded_list(
        self, client, isolated_db, monkeypatch
    ):
        ids = [s for s in captures.capture_ids() if s != EMPTY_CAPTURE]
        summary = seed_run(monkeypatch, ids=ids)
        assert summary["symphonies_without_series"] == []
        panel = render_panel(client)
        assert panel.find(testid="correlations-excluded") is None
        assert panel.find_all(testid="correlations-excluded-item") == []

    def test_an_unmapped_reason_shows_the_shared_fallback_with_the_raw_reason(
        self, client, isolated_db, monkeypatch
    ):
        _recs, produced = captures.run_over_captures(monkeypatch)
        seed_state_only(with_unmapped_exclusion_reason(produced))
        texts = _excluded_texts(render_panel(client))
        name = collapse(captures.capture_name(EMPTY_CAPTURE))
        assert texts == [f"Not in the matrix: {name}. Excluded: retired_by_operator"]


# ===========================================================================
# States: no run, a legacy run, a failed run, too few symphonies
# ===========================================================================


def _only_state(client) -> tuple[object, str]:
    panel = render_panel(client)
    state = _state_node(panel)
    assert state is not None, "the panel shows no state block"
    assert panel.find(testid="correlation-matrix-table") is None, "a state with no matrix drew one"
    return panel, collapse(state.text)


class TestEmptyAndFailedStates:
    def test_with_no_run_at_all_the_tab_says_when_the_matrix_will_fill(self, client, isolated_db):
        panel, text = _only_state(client)
        assert _state_node(panel).attrs["data-state"] == "never_run"
        assert NEVER_RUN_TEXT in text
        assert panel.find(testid="correlations-legend") is None

    def test_the_real_pre_pairs_summary_asks_for_a_newer_run(self, client, isolated_db):
        real = captures.load_captured_summary_row()
        assert "pairs" not in real, "premise: the real row predates the stored pairs"
        seed_state_only(real)
        panel, text = _only_state(client)
        assert _state_node(panel).attrs["data-state"] == "needs_newer_run"
        assert NEVER_RUN_TEXT in text
        assert "newer run" in text.lower()
        assert analytics.format_et_stamp(real["run_at"]) in text
        assert "03:45 UTC" in text

    @pytest.mark.parametrize(
        "row", ["summary_series_unavailable.json", "summary_insufficient_symphonies.json"]
    )
    def test_a_run_that_found_too_few_usable_series_names_its_state_count_and_next_run(
        self, client, isolated_db, row
    ):
        summary = load_derived_summary(row)
        seed_state_only(summary)
        panel, text = _only_state(client)
        assert _state_node(panel).attrs["data-state"] == "no_matrix"
        assert "fewer than two symphonies with a usable series" in text
        assert "no matrix exists yet" in text.lower()
        assert (
            f"Symphonies analysed: {summary['symphonies_with_series']} of {summary['roster_size']}"
            in text
        )
        assert analytics.format_et_stamp(summary["run_at"]) in text
        assert "03:45 UTC" in text

    def test_a_run_that_ended_in_an_internal_error_names_the_failure_its_time_and_the_next_run(
        self, client, isolated_db
    ):
        summary = load_derived_summary("summary_internal_error.json")
        seed_state_only(summary)
        panel, text = _only_state(client)
        assert _state_node(panel).attrs["data-state"] == "no_matrix"
        assert "internal error" in text.lower()
        assert analytics.format_et_stamp(summary["run_at"]) in text
        assert "03:45 UTC" in text
        assert "None" not in text, "an unknown count is never printed as a value"

    def test_a_failed_latest_run_leaves_the_earlier_runs_matrix_with_a_note_naming_that_run(
        self, client, isolated_db, monkeypatch
    ):
        good = seed_run(monkeypatch)
        seed_state_only(load_derived_summary("summary_internal_error.json"))
        panel = render_panel(client)
        grid = grid_of(client)
        assert len(grid.row_labels) == len(_analysed(good))
        assert _state_node(panel) is None
        note = panel.find(testid="correlations-earlier-run-note")
        assert note is not None
        assert collapse(note.text) == (
            f"From the run of {analytics.format_et_stamp(good['run_at'])}; "
            "the latest run did not complete."
        )

    def test_a_successful_latest_run_shows_the_matrix_with_no_state_block_and_no_note(
        self, client, isolated_db, monkeypatch
    ):
        seed_run(monkeypatch)
        panel = render_panel(client)
        assert panel.find(testid="correlation-matrix-table") is not None
        assert _state_node(panel) is None
        assert panel.find(testid="correlations-earlier-run-note") is None


class TestCrisisCaveatInEveryState:
    @staticmethod
    def _banner_text(client) -> str:
        banner = render_panel(client).find(testid="crisis-caveat-banner")
        assert banner is not None, "the caveat banner is missing"
        return collapse(banner.text)

    def _caveat(self) -> str:
        from advisors.correlation_diagnostic import CRISIS_CAVEAT  # noqa: PLC0415

        return collapse(CRISIS_CAVEAT)

    def test_the_caveat_is_stated_whole_with_a_matrix(self, client, isolated_db, monkeypatch):
        seed_run(monkeypatch)
        assert self._caveat() in self._banner_text(client)

    def test_the_caveat_is_stated_whole_with_no_run(self, client, isolated_db):
        assert self._caveat() in self._banner_text(client)

    def test_the_caveat_is_stated_whole_with_a_run_that_has_no_matrix(self, client, isolated_db):
        seed_state_only(load_derived_summary("summary_series_unavailable.json"))
        assert self._caveat() in self._banner_text(client)

    def test_the_caveat_is_stated_whole_when_the_run_cannot_be_read(
        self, client, isolated_db, monkeypatch
    ):
        real = db_module.get_advisor_observations_for_role

        def flaky(role, *args, **kwargs):
            if role == captures.SUMMARY_ROLE:
                raise sqlite3.OperationalError("disk I/O error")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", flaky)
        assert self._caveat() in self._banner_text(client)

    def test_the_caveat_comes_from_the_diagnostics_own_constant(
        self, client, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(
            "advisors.correlation_diagnostic.CRISIS_CAVEAT", "Stress changes everything here."
        )
        text = self._banner_text(client)
        assert "Stress changes everything here." in text
        assert "destabilize" not in text, "the banner still shows the old fixed sentence"
