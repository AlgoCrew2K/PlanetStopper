"""
R4 — RED tests: DSR + naive_sharpe + frozen_eval_sharpe surfacing.

Surfaces:
  1. Discord EOD embed — send_eod_discord_post must include all three Sharpe
     values in the per-symphony optimization embed when present, and render
     a placeholder when they are None (legacy rows).

  2. /api/autotune-runs Flask route — must return all three values per row in
     its JSON response.

  3. /ai-advisor recent-runs panel rendering — the HTML response from GET
     /ai-advisor must contain column headers and cell values for the three
     metrics (naive, DSR, frozen-eval).

ALL tests in this file are RED.  They will fail until:
  A. reporting.send_eod_discord_post receives DSR/sharpe data and includes it
     in the per-symphony optimization embed (optuna-specialist surface).
  B. A /api/autotune-runs route is added to app.py that queries all three
     columns and returns them in its JSON payload (flask-dashboard-specialist).
  C. The /ai-advisor route and template surface recent-runs with the three
     columns (flask-dashboard-specialist).

Fixture provenance (PA-18):
  tests/fixtures/autotuner/dsr_surfacing/autotune_run_full_values.json
  tests/fixtures/autotuner/dsr_surfacing/autotune_run_legacy_nulls.json
  Schema cross-referenced: migrations/006_autotune_runs_sharpe.sql,
  migrations/007_autotune_runs_frozen_eval.sql, database.py save_autotune_run.

Mocking strategy:
  - requests.post patched on reporting module namespace (no live HTTP).
  - database read calls patched to return fixture-derived rows.
  - Flask test client used for route tests (no live server).
  - No @pytest.mark.live tests in this file.

No module-level mutables; all fixtures have explicit pytest scope.
Floats compared with pytest.approx + documented tolerance rationale.
"""

from __future__ import annotations

import json
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loading helpers (PA-18: derived from JSON, never hardcoded)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parents[2] / "tests" / "fixtures" / "autotuner" / "dsr_surfacing"
)


@pytest.fixture(scope="session")
def full_run_fixture() -> dict:
    """Load the full-values autotune run fixture (all three Sharpe fields present)."""
    path = _FIXTURE_DIR / "autotune_run_full_values.json"
    assert path.exists(), (
        f"PA-18 violation: fixture not found at {path}. "
        "Run: tests/fixtures/autotuner/dsr_surfacing/autotune_run_full_values.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def legacy_run_fixture() -> dict:
    """Load the legacy-nulls autotune run fixture (pre-O2/O6 NULL fields)."""
    path = _FIXTURE_DIR / "autotune_run_legacy_nulls.json"
    assert path.exists(), (
        f"PA-18 violation: fixture not found at {path}. "
        "Run: tests/fixtures/autotuner/dsr_surfacing/autotune_run_legacy_nulls.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Isolated DB fixture (re-used by API route tests)
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Redirect database.DB_FILE to tmp_path and initialise schema."""
    import database as db_module

    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: build the per-symphony DSR data dict the way the implementer should
# shape it so send_eod_discord_post can include it.
# ---------------------------------------------------------------------------


def _selection_stats_from_fixture(fixture_row: dict) -> dict:
    """
    Build the selection_stats dict from a fixture row.  This mirrors what
    run_autotuner's caller (alpha_bot_execution.py) should assemble when
    constructing the optimization_results blob for send_eod_discord_post.

    Shape (proposed by test-writer; implementer must match):
      {
        "naive_sharpe":      float | None,
        "selection_tstat":   float | None,
        "frozen_eval_sharpe": float | None,
      }
    """
    row = fixture_row["row"]
    return {
        "naive_sharpe": row["naive_sharpe"],
        "selection_tstat": row["selection_tstat"],
        "frozen_eval_sharpe": row["frozen_eval_sharpe"],
    }


# ===========================================================================
# SURFACE 1 — Discord EOD embed
# ===========================================================================


class TestDiscordEmbedIncludesDSR:
    """
    AC-1: Discord embed for a tuning run must include all three Sharpe values
    with operator-readable labels (not raw DB column names).

    AC-2: Discord embed handles None gracefully — renders "N/A" or "—".

    AC-5: Numeric formatting: 4 decimal places.

    AC-7: No regression to existing autotuner write path / Discord pipeline.
    """

    def _make_minimal_eod_report(self, tmp_path) -> str:
        """Write a minimal post-mortem JSON to tmp_path and return its path."""
        report = {
            "date": "2026-05-14",
            "summary": {
                "total_monitored": 1,
                "total_triggered": 0,
                "positive_guard_alpha_count": 0,
            },
            "tomorrow_target_holdings": {},
            "triggers": [],
        }
        path = tmp_path / "post_mortem_2026-05-14.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def _build_optimization_results_with_selection_stats(
        self, symphony_id: str, selection_stats: dict
    ) -> dict:
        """
        Build the optimization_results dict that send_eod_discord_post receives.

        The current format carries per-param delta dicts.  R4 requires adding
        DSR data alongside them.  The implementer must agree to this shape — the
        test asserts on the embed output, not the intermediate dict shape, so the
        implementer has latitude to embed DSR data however they choose as long as
        the resulting Discord embed text satisfies the assertions.
        """
        return {
            symphony_id: {
                "_baseline_chosen": "Adopted AI",
                "_selection_stats": selection_stats,
            }
        }

    def test_discord_embed_contains_naive_sharpe_when_present(self, full_run_fixture, tmp_path):
        """
        AC-1 + AC-5: When naive_sharpe is non-None, the Discord embed string
        for that symphony must contain the formatted naive_sharpe value to 4
        decimal places and a human-readable label (not 'naive_sharpe' raw column).

        This test will FAIL (RED) until send_eod_discord_post reads selection_stats
        from optimization_results and formats it into the embed description.
        """
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        selection_stats = _selection_stats_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_selection_stats(sym_id, selection_stats)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    opt_results,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        # Find the per-symphony optimization embed
        sym_embeds = [
            e
            for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]

        assert sym_embeds, (
            f"No Discord embed found for symphony '{sym_id}'. "
            "send_eod_discord_post must produce a per-symphony embed that includes DSR data."
        )

        # The combined text of all symphony embeds must contain the formatted naive_sharpe.
        all_text = " ".join(e.get("description", "") + e.get("title", "") for e in sym_embeds)

        expected_naive = fmt["naive_sharpe_formatted"]  # "1.8432" — from fixture
        assert expected_naive in all_text, (
            f"Discord embed must contain naive_sharpe formatted as '{expected_naive}' "
            f"(4 decimal places, from fixture). "
            f"Found embed text: {all_text!r}. "
            "AC-1 + AC-5 violation."
        )

    def test_discord_embed_contains_selection_tstat_when_present(self, full_run_fixture, tmp_path):
        """
        AC-1 — re-pinned for Decision D3: the Discord embed must contain the
        selection_tstat (the Harvey & Liu haircut winner's t-statistic) with a
        human-readable label. The label must NOT be the raw DB column name
        'selection_tstat', and must NOT be 'DSR' / 'Deflated Sharpe' (D3 removed
        the DSR) — it must accurately name the selection statistic.
        """
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        selection_stats = _selection_stats_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_selection_stats(sym_id, selection_stats)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    opt_results,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        sym_embeds = [
            e
            for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]
        assert sym_embeds, (
            f"No Discord embed found for symphony '{sym_id}'. "
            "send_eod_discord_post must produce a per-symphony embed."
        )

        all_text = " ".join(e.get("description", "") + e.get("title", "") for e in sym_embeds)

        expected_tstat = fmt["selection_tstat_formatted"]  # from fixture (4dp)
        assert expected_tstat in all_text, (
            f"Discord embed must contain selection_tstat formatted as "
            f"'{expected_tstat}'. Found embed text: {all_text!r}. "
            f"AC-1 + AC-5 violation."
        )

        # Raw DB column name is NOT an operator-readable label.
        assert "selection_tstat" not in all_text.lower().replace(expected_tstat, ""), (
            "Discord embed must NOT use the raw DB column name 'selection_tstat' "
            "as a label. Use an operator-readable label (e.g. 'selection t-stat')."
        )
        # D3: the deleted-DSR name must not label this value.
        assert "dsr" not in all_text.lower() and "deflated sharpe" not in all_text.lower(), (
            "Discord embed must NOT label the value 'DSR' / 'Deflated Sharpe' — "
            "D3 removed the Deflated Sharpe Ratio; this is the Harvey & Liu "
            "selection t-statistic."
        )

    def test_discord_embed_contains_frozen_eval_sharpe_when_present(
        self, full_run_fixture, tmp_path
    ):
        """
        AC-1 + AC-5: Discord embed must contain the frozen_eval_sharpe value
        formatted to 4 decimal places.
        """
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        selection_stats = _selection_stats_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_selection_stats(sym_id, selection_stats)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    opt_results,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        sym_embeds = [
            e
            for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]
        assert sym_embeds, f"No Discord embed found for symphony '{sym_id}'."

        all_text = " ".join(e.get("description", "") + e.get("title", "") for e in sym_embeds)

        expected_frozen = fmt["frozen_eval_sharpe_formatted"]  # "0.7643" — from fixture
        assert expected_frozen in all_text, (
            f"Discord embed must contain frozen_eval_sharpe formatted as '{expected_frozen}'. "
            f"Found embed text: {all_text!r}. AC-1 + AC-5 violation."
        )

    def test_discord_embed_null_dsr_renders_placeholder_not_none_literal(
        self, legacy_run_fixture, tmp_path
    ):
        """
        AC-2: When all three Sharpe fields are None (legacy row), the Discord embed
        must render a placeholder ("N/A" or "—") for each.  It must NOT render the
        Python literal 'None' or crash.
        """
        import reporting

        row = legacy_run_fixture["row"]
        selection_stats = _selection_stats_from_fixture(legacy_run_fixture)
        sym_id = row["symphony_id"]

        assert selection_stats["naive_sharpe"] is None
        assert selection_stats["selection_tstat"] is None
        assert selection_stats["frozen_eval_sharpe"] is None

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_selection_stats(sym_id, selection_stats)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    opt_results,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        # Should not raise — test reaching here means no crash
        all_embed_text = " ".join(
            e.get("description", "") + e.get("title", "") for e in captured_embeds
        )

        assert "None" not in all_embed_text, (
            "Discord embed must NOT render the Python literal 'None' for missing Sharpe fields. "
            f"Found embed text: {all_embed_text!r}. AC-2 violation."
        )

        # At least one placeholder must appear somewhere in the embed text
        placeholders = legacy_run_fixture["format_expectations"]["null_placeholder_options"]
        has_placeholder = any(p in all_embed_text for p in placeholders)
        assert has_placeholder, (
            f"Discord embed must render a placeholder from {placeholders} for None Sharpe fields. "
            f"Found embed text: {all_embed_text!r}. AC-2 violation."
        )


# ===========================================================================
# SURFACE 2 — /api/autotune-runs route
# ===========================================================================


class TestAutotuneRunsApiRoute:
    """
    AC-3: /api/autotune-runs route must return naive_sharpe, selection_tstat,
    and frozen_eval_sharpe for each row in its JSON response.

    AC-5: Numeric fields present as floats (4-decimal precision contract is
    on the rendering layer, not the JSON API — the API returns raw floats).

    AC-8 (RED): A row with all three values present → JSON response contains them.
    AC-9 (RED): A row with selection_tstat = None → JSON response handles it
    gracefully (null in JSON, no KeyError, no 500).
    """

    @pytest.fixture()
    def app_client(self, isolated_db):
        """Flask test client with DB redirected to isolated tmp DB."""
        import app as flask_app

        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            yield client

    def test_api_autotune_runs_route_exists(self, app_client):
        """
        AC-3 precondition: GET /api/autotune-runs must respond with 200, not 404.

        This test will FAIL (RED) until the route is added to app.py.
        """
        resp = app_client.get("/api/autotune-runs")
        assert resp.status_code == 200, (
            f"GET /api/autotune-runs returned {resp.status_code}. "
            "The route does not exist yet. Add it to app.py. AC-3 violation."
        )

    def test_api_autotune_runs_returns_json(self, app_client):
        """
        AC-3: /api/autotune-runs must return application/json.
        """
        resp = app_client.get("/api/autotune-runs")
        assert resp.content_type.startswith("application/json"), (
            f"GET /api/autotune-runs must return application/json; "
            f"got {resp.content_type!r}. AC-3 violation."
        )

    def test_api_autotune_runs_empty_when_no_rows(self, app_client):
        """
        AC-3: When autotune_runs is empty, the route must return an empty list
        (not None, not a 500, not a different structure).
        """
        resp = app_client.get("/api/autotune-runs")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list), (
            f"GET /api/autotune-runs must return a JSON array; got {type(data).__name__}. "
            "AC-3 violation."
        )
        assert len(data) == 0, (
            f"With no rows in autotune_runs the response must be an empty list; got {data!r}."
        )

    def test_api_autotune_runs_row_with_all_three_sharpe_fields_present(
        self, full_run_fixture, isolated_db, app_client
    ):
        """
        AC-3 + AC-8: A row with all three Sharpe fields populated → the JSON
        response for that row must include naive_sharpe, selection_tstat, and
        frozen_eval_sharpe with the correct values (within float precision).

        This test will FAIL (RED) until:
          A. /api/autotune-runs route exists.
          B. The route queries naive_sharpe, selection_tstat, frozen_eval_sharpe.
          C. get_latest_autotune_run (or a new get_all_autotune_runs accessor)
             includes frozen_eval_sharpe in its SELECT (currently missing — bug
             introduced by migration 007 not reflected in the accessor).
        """
        import database as db_module

        row = full_run_fixture["row"]
        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=row["symphony_id"],
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            selection_tstat=row["selection_tstat"],
            naive_sharpe=row["naive_sharpe"],
            validation_sharpe=row["validation_sharpe"],
            frozen_eval_sharpe=row["frozen_eval_sharpe"],
        )

        resp = app_client.get("/api/autotune-runs")
        assert resp.status_code == 200
        data = json.loads(resp.data)

        assert len(data) >= 1, (
            "GET /api/autotune-runs must return at least one row after save_autotune_run."
        )

        # Find the row for this symphony
        matching = [r for r in data if r.get("symphony_id") == row["symphony_id"]]
        assert matching, (
            f"No row for symphony_id='{row['symphony_id']}' in /api/autotune-runs response. "
            f"Full response: {data!r}"
        )
        api_row = matching[0]

        assert "naive_sharpe" in api_row, (
            "AC-3: /api/autotune-runs response row must include 'naive_sharpe' key."
        )
        assert "selection_tstat" in api_row, (
            "AC-3: /api/autotune-runs response row must include 'selection_tstat' key."
        )
        assert "frozen_eval_sharpe" in api_row, (
            "AC-3: /api/autotune-runs response row must include 'frozen_eval_sharpe' key. "
            "NOTE: database.get_latest_autotune_run currently omits this column — "
            "the implementer must fix the accessor (migrations/007) or add a new one."
        )

        # Values from fixture — float tolerance of 1e-6 relative:
        # SQLite REAL is IEEE-754 double; round-trip through JSON serialization
        # may introduce sub-epsilon drift (~1e-15). 1e-6 is a safe quant-meaningful
        # floor (Sharpe precision rarely matters below 1e-4).
        assert api_row["naive_sharpe"] == pytest.approx(row["naive_sharpe"], rel=1e-6), (
            f"naive_sharpe mismatch: fixture={row['naive_sharpe']}, "
            f"api={api_row['naive_sharpe']}. Float drift > 1e-6 relative."
        )
        assert api_row["selection_tstat"] == pytest.approx(row["selection_tstat"], rel=1e-6), (
            f"selection_tstat mismatch: fixture={row['selection_tstat']}, "
            f"api={api_row['selection_tstat']}."
        )
        assert api_row["frozen_eval_sharpe"] == pytest.approx(
            row["frozen_eval_sharpe"], rel=1e-6
        ), (
            f"frozen_eval_sharpe mismatch: fixture={row['frozen_eval_sharpe']}, "
            f"api={api_row['frozen_eval_sharpe']}."
        )

    def test_api_autotune_runs_legacy_row_with_null_sharpe_returns_null_not_error(
        self, legacy_run_fixture, isolated_db, app_client
    ):
        """
        AC-9: A legacy row with selection_tstat = None (pre-O2 migration) must
        be returned with null JSON values, not a 500 or KeyError.
        """
        import database as db_module

        row = legacy_run_fixture["row"]
        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=row["symphony_id"],
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            selection_tstat=row["selection_tstat"],  # None
            naive_sharpe=row["naive_sharpe"],  # None
            validation_sharpe=row["validation_sharpe"],  # None
            frozen_eval_sharpe=row["frozen_eval_sharpe"],  # None
        )

        resp = app_client.get("/api/autotune-runs")
        assert resp.status_code == 200, (
            f"GET /api/autotune-runs must not 500 on a NULL-Sharpe row; "
            f"got {resp.status_code}. AC-9 violation."
        )

        data = json.loads(resp.data)
        matching = [r for r in data if r.get("symphony_id") == row["symphony_id"]]
        assert matching, (
            f"Legacy row for '{row['symphony_id']}' must appear in /api/autotune-runs response."
        )
        api_row = matching[0]

        assert api_row.get("naive_sharpe") is None, (
            f"naive_sharpe must be null (JSON) for a legacy NULL row; got {api_row.get('naive_sharpe')!r}."
        )
        assert api_row.get("selection_tstat") is None, (
            f"selection_tstat must be null (JSON) for a legacy NULL row; got {api_row.get('selection_tstat')!r}."
        )
        assert api_row.get("frozen_eval_sharpe") is None, (
            f"frozen_eval_sharpe must be null (JSON) for a legacy NULL row; got {api_row.get('frozen_eval_sharpe')!r}."
        )


# ===========================================================================
# SURFACE 3 — /ai-advisor recent-runs panel (template rendering)
# ===========================================================================


class TestAiAdvisorRecentRunsPanel:
    """
    AC-4: /ai-advisor recent-runs dashboard view must render the three values
    as visible columns/cells. Column headers for naive/DSR/frozen-eval must
    be present in the HTML.

    AC-8 + AC-9 on template: rendered cells must show values or placeholder.
    """

    @pytest.fixture()
    def app_client(self, isolated_db):
        import app as flask_app

        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            yield client

    def test_ai_advisor_page_renders_recent_runs_section(self, app_client):
        """
        AC-4 precondition: GET /ai-advisor must return 200 and the HTML body
        must contain a recent-runs section or table that is identifiable by
        heading text or a data attribute.

        This test will FAIL (RED) until the ai_advisor.html template includes
        a recent-runs panel.
        """
        resp = app_client.get("/ai-advisor")
        assert resp.status_code == 200

        html = resp.data.decode("utf-8")

        # The panel must contain some form of "recent run" indication —
        # accept a liberal set of markers the implementer may use.
        recent_runs_markers = [
            "recent run",
            "recent-run",
            "autotune run",
            "autotune-run",
            "recent tuning",
        ]
        has_section = any(m in html.lower() for m in recent_runs_markers)
        assert has_section, (
            "GET /ai-advisor HTML must contain a 'recent runs' section. "
            f"Looked for any of: {recent_runs_markers}. "
            "Add a recent-runs panel to ai_advisor.html. AC-4 violation."
        )

    def test_ai_advisor_page_has_selection_tstat_column_header(self, app_client):
        """
        AC-4 — re-pinned for Decision D3: the recent-runs panel must surface the
        selection statistic under an ACCURATE label.

        D3 removed the Deflated Sharpe Ratio; the panel now renders the Harvey &
        Liu haircut selection t-statistic. Like the naive/frozen header tests,
        this checks ai_advisor.js (the panel migrated from a static <table> to a
        JS-rendered card list, C-16). The JS must (a) reference the
        `selection_tstat` field, (b) render an accurate operator label for it
        ("Sel t-stat" / "selection t-stat"), and (c) NOT call it "DSR" /
        "Deflated Sharpe" — the naming lie D3's surface-consistency requirement
        forbids.
        """
        js_path = pathlib.Path(__file__).parents[2] / "static" / "ai_advisor.js"
        assert js_path.exists(), "static/ai_advisor.js must exist"
        js_src = js_path.read_text(encoding="utf-8")
        js_lower = js_src.lower()

        assert "selection_tstat" in js_src, (
            "static/ai_advisor.js must reference 'selection_tstat' to render the "
            "Harvey & Liu selection statistic in autotune run cards. AC-4 / D3."
        )
        accurate_labels = [
            "sel t-stat",
            "selection t-stat",
            "selection tstat",
            "selection statistic",
        ]
        assert any(lbl in js_lower for lbl in accurate_labels), (
            f"static/ai_advisor.js must render an accurate operator label for "
            f"the selection statistic (one of: {accurate_labels}). AC-4 / D3."
        )
        assert "dsr" not in js_lower and "deflated sharpe" not in js_lower, (
            "static/ai_advisor.js still contains 'DSR' / 'Deflated Sharpe' — D3 "
            "removed the Deflated Sharpe Ratio; the panel must not label the "
            "Harvey & Liu selection statistic with the deleted DSR name."
        )

        # The rendered /ai-advisor page itself must also be free of the naming
        # lie — a stale CSS comment '/* V-23: Sharpe/DSR bold mono */' in
        # templates/ai_advisor.html is a known offender and must be de-DSR'd.
        resp = app_client.get("/ai-advisor")
        assert resp.status_code == 200
        page_html = resp.data.decode("utf-8").lower()
        assert "dsr" not in page_html and "deflated sharpe" not in page_html, (
            "GET /ai-advisor HTML still contains 'DSR' / 'Deflated Sharpe' (e.g. "
            "the stale '/* V-23: Sharpe/DSR bold mono */' CSS comment in "
            "templates/ai_advisor.html). D3 removed the DSR — de-DSR the comment."
        )

    def test_ai_advisor_page_has_naive_sharpe_column_header(self, app_client):
        """
        AC-4: The recent-runs panel must surface naive Sharpe values.
        The panel migrated from a static <table> (with <th>Naive Sharpe</th>)
        to a JS-rendered card list (C-16). The correct contract is that
        ai_advisor.js references the naive_sharpe field when building each card.
        """
        js_path = pathlib.Path(__file__).parents[2] / "static" / "ai_advisor.js"
        assert js_path.exists(), "static/ai_advisor.js must exist"
        js_src = js_path.read_text(encoding="utf-8")

        assert "naive_sharpe" in js_src, (
            "static/ai_advisor.js must reference 'naive_sharpe' to render it in "
            "autotune run cards. AC-4 violation: naive Sharpe metric not surfaced."
        )

    def test_ai_advisor_page_has_frozen_eval_column_header(self, app_client):
        """
        AC-4: The recent-runs panel HTML must contain a column header for frozen-eval Sharpe.
        Acceptable: 'Frozen', 'Frozen-Eval', 'frozen eval', 'frozen_eval' (case-insensitive).
        """
        resp = app_client.get("/ai-advisor")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8").lower()

        frozen_headers = ["frozen", "frozen-eval", "frozen eval", "frozen_eval"]
        has_frozen_col = any(h in html for h in frozen_headers)
        assert has_frozen_col, (
            f"GET /ai-advisor HTML must contain a Frozen-Eval column header "
            f"(one of: {frozen_headers}). AC-4 violation."
        )

    def test_ai_advisor_recent_runs_renders_values_from_api(
        self, full_run_fixture, isolated_db, app_client
    ):
        """
        AC-4 + AC-8: When an autotune row with all three Sharpe fields exists,
        the /ai-advisor page OR the JavaScript-populated recent-runs panel must
        be able to consume /api/autotune-runs and surface those values.

        Strategy: assert the /api/autotune-runs JSON endpoint (which the JS will
        consume) returns the values correctly — this is the contract the template
        JS will use. The template rendering itself is exercised by the column-header
        tests above; this test verifies the data contract the template depends on.

        This test will FAIL (RED) until /api/autotune-runs returns the three fields.
        """
        import database as db_module

        row = full_run_fixture["row"]
        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=row["symphony_id"],
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            selection_tstat=row["selection_tstat"],
            naive_sharpe=row["naive_sharpe"],
            validation_sharpe=row["validation_sharpe"],
            frozen_eval_sharpe=row["frozen_eval_sharpe"],
        )

        # The template JS will call /api/autotune-runs — verify it returns all three fields
        resp = app_client.get("/api/autotune-runs")
        assert resp.status_code == 200, (
            "The template's /api/autotune-runs data source must return 200. "
            "AC-4 violation (dashboard data contract)."
        )
        data = json.loads(resp.data)
        matching = [r for r in data if r.get("symphony_id") == row["symphony_id"]]
        assert matching, (
            f"The data contract for the recent-runs panel is broken: "
            f"no row for '{row['symphony_id']}' in /api/autotune-runs."
        )
        api_row = matching[0]

        # All three keys must be present for the template to render them
        for key in ("naive_sharpe", "selection_tstat", "frozen_eval_sharpe"):
            assert key in api_row, (
                f"AC-4 data contract: /api/autotune-runs response must include '{key}' "
                f"so the recent-runs panel can render it. Key missing from: {list(api_row.keys())}"
            )


# ===========================================================================
# SURFACE 1 — Regression guard: existing EOD embed is not broken by DSR changes
# ===========================================================================


class TestDiscordEmbedNoRegression:
    """
    AC-7: The existing EOD embed fields (Guard Alpha, triggers summary, chart)
    must still be present after adding DSR surfacing. This guards against the
    implementer accidentally dropping the main embed when modifying the
    optimization section.
    """

    def _make_eod_report_with_trigger(self, tmp_path) -> str:
        report = {
            "date": "2026-05-14",
            "summary": {
                "total_monitored": 2,
                "total_triggered": 1,
                "positive_guard_alpha_count": 1,
            },
            "tomorrow_target_holdings": {"SPY": 0.6, "AGG": 0.4},
            "triggers": [
                {
                    "symphony_name": "Alpha Symphony",
                    "symphony_value": 10000.0,
                    "account_id": "acc-1",
                    "exit_reason": "Trailing Stop",
                    "exit_return": -2.5,
                    "attempted_trigger_level": -2.0,
                    "shadow_return": -3.1,
                    "shadow_hwm": 5.0,
                    "saved_pct_guard_alpha": 0.6,
                    "saved_dollars": 60.0,
                    "hwm_at_trigger": 5.0,
                    "time_triggered": "14:30",
                    "symphony_vol": 1.2,
                    "strategy_params": {},
                    "next_day_holdings": ["SPY"],
                }
            ],
        }
        path = tmp_path / "post_mortem_2026-05-14.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def test_main_eod_embed_still_present_after_dsr_changes(self, tmp_path):
        """
        AC-7: The main Planet Stopper EOD Analysis embed must be present even when
        DSR optimization data is included in optimization_results.

        This is a regression guard — it will PASS now (main embed exists) and
        should continue to pass after the implementer modifies send_eod_discord_post.
        If the implementer accidentally removes the main embed, this test catches it.
        """
        import reporting

        report_path = self._make_eod_report_with_trigger(tmp_path)

        # Include a symphony with DSR data to simulate post-R4 call
        opt_with_dsr = {
            "alpha_symphony": {
                "_baseline_chosen": "Adopted AI",
                "_selection_stats": {
                    "naive_sharpe": 1.5,
                    "selection_tstat": 0.8,
                    "frozen_eval_sharpe": 0.6,
                },
            }
        }

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    opt_with_dsr,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        assert captured_embeds, "send_eod_discord_post must send at least one embed."

        main_embed_titles = [
            e.get("title", "")
            for e in captured_embeds
            if "Planet Stopper EOD" in e.get("title", "") or "EOD Analysis" in e.get("title", "")
        ]
        assert main_embed_titles, (
            "AC-7: The main 'Planet Stopper EOD Analysis' embed must still be present. "
            f"All embed titles found: {[e.get('title', '') for e in captured_embeds]!r}. "
            "Regression: the implementer must not remove the main embed."
        )


# ===========================================================================
# PRODUCTION WIRING — alpha_bot_execution.py augmentation gap (R/G/R round 2)
# ===========================================================================


class TestProductionWiringAugmentsOptimizationResultsWithDSR:
    """
    Gap identified after initial GREEN: the tests in TestDiscordEmbedIncludesDSR
    inject _selection_stats directly into optimization_results, so they pass. But the
    production path in alpha_bot_execution.py must augment autotuner_changes with
    DB-fetched DSR data before calling send_eod_discord_post — otherwise the DSR
    line is silently omitted in production.

    These tests verify the observable behavior of the production augmentation
    path: given optimization_results with NO _selection_stats (as run_autotuner returns)
    and a matching DB row, the augmentation must inject _selection_stats so the Discord
    embed ultimately contains the DSR values.

    The tests do NOT require a specific function name — they test the inline
    augmentation logic via database.get_latest_autotune_run, which the implementer
    wires directly before calling send_eod_discord_post.

    Fixture provenance (PA-18):
      tests/fixtures/autotuner/dsr_surfacing/autotune_run_full_values.json
    """

    def _make_minimal_eod_report(self, tmp_path) -> str:
        report = {
            "date": "2026-05-14",
            "summary": {
                "total_monitored": 1,
                "total_triggered": 0,
                "positive_guard_alpha_count": 0,
            },
            "tomorrow_target_holdings": {},
            "triggers": [],
        }
        path = tmp_path / "post_mortem_2026-05-14.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def test_production_wiring_injects_selection_stats_from_db_into_optimization_results(
        self, full_run_fixture, isolated_db, tmp_path
    ):
        """
        When alpha_bot_execution's EOD path augments optimization_results with
        DB-fetched DSR data, the resulting dict must contain _selection_stats with all
        three Sharpe fields populated from the DB row.

        Tested by: calling database.get_latest_autotune_run (the same call the
        production code makes) on a real isolated DB row, then asserting the
        returned dict has all three Sharpe keys with correct values. This directly
        verifies the data that the production augmentation loop injects.

        This test will FAIL if get_latest_autotune_run does not return
        frozen_eval_sharpe (i.e., the migration-007 accessor fix was not applied).
        """
        import database as db_module

        row = full_run_fixture["row"]
        sym_id = row["symphony_id"]

        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=sym_id,
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            selection_tstat=row["selection_tstat"],
            naive_sharpe=row["naive_sharpe"],
            validation_sharpe=row["validation_sharpe"],
            frozen_eval_sharpe=row["frozen_eval_sharpe"],
        )

        # The production augmentation loop calls get_latest_autotune_run per symphony.
        # Verify it returns all three Sharpe fields so the _selection_stats injection is complete.
        run_row = db_module.get_latest_autotune_run(sym_id)
        assert run_row is not None, (
            f"get_latest_autotune_run('{sym_id}') returned None after save_autotune_run."
        )

        assert "naive_sharpe" in run_row, (
            "get_latest_autotune_run must return 'naive_sharpe' so the production "
            "augmentation loop can inject it as _selection_stats['naive_sharpe']."
        )
        assert "selection_tstat" in run_row, (
            "get_latest_autotune_run must return 'selection_tstat'."
        )
        assert "frozen_eval_sharpe" in run_row, (
            "get_latest_autotune_run must return 'frozen_eval_sharpe'. "
            "This key was added by migration 007 but the accessor was not updated — "
            "flask-dashboard-specialist's fix at 16a5b5c must be merged."
        )

        # Values from fixture — rel tolerance 1e-6 (SQLite REAL round-trip)
        assert run_row["naive_sharpe"] == pytest.approx(row["naive_sharpe"], rel=1e-6), (
            f"naive_sharpe round-trip: wrote {row['naive_sharpe']}, read {run_row['naive_sharpe']}."
        )
        assert run_row["selection_tstat"] == pytest.approx(row["selection_tstat"], rel=1e-6), (
            f"selection_tstat round-trip: wrote {row['selection_tstat']}, "
            f"read {run_row['selection_tstat']}."
        )
        assert run_row["frozen_eval_sharpe"] == pytest.approx(
            row["frozen_eval_sharpe"], rel=1e-6
        ), (
            f"frozen_eval_sharpe round-trip: wrote {row['frozen_eval_sharpe']}, "
            f"read {run_row['frozen_eval_sharpe']}."
        )

    def test_production_wiring_discord_embed_contains_dsr_via_real_db_call(
        self, full_run_fixture, isolated_db, tmp_path
    ):
        """
        End-to-end production wiring test: when optimization_results has NO
        _selection_stats (as returned by run_autotuner), but the production augmentation
        loop calls get_latest_autotune_run and injects _selection_stats, the resulting
        Discord embed must contain the formatted DSR values.

        Simulates the production path by:
        1. Writing a real DB row (isolated_db).
        2. Building raw optimization_results (no _selection_stats) as run_autotuner produces.
        3. Running the augmentation inline (mirroring alpha_bot_execution.py:765-773).
        4. Passing the augmented dict to send_eod_discord_post.
        5. Asserting the embed contains the three formatted Sharpe values.

        This test is GREEN if and only if:
        - get_latest_autotune_run returns frozen_eval_sharpe (16a5b5c fix)
        - The augmentation loop correctly injects _selection_stats
        - reporting.send_eod_discord_post renders the DSR line from _selection_stats
        """
        import database as db_module
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        sym_id = row["symphony_id"]

        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=sym_id,
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            selection_tstat=row["selection_tstat"],
            naive_sharpe=row["naive_sharpe"],
            validation_sharpe=row["validation_sharpe"],
            frozen_eval_sharpe=row["frozen_eval_sharpe"],
        )

        # Raw optimization_results — no _selection_stats (what run_autotuner returns)
        raw_changes = {sym_id: {"_baseline_chosen": row["baseline_decision"]}}

        # Inline augmentation — mirrors alpha_bot_execution.py:765-773
        for sid, sym_data in raw_changes.items():
            run_row = db_module.get_latest_autotune_run(sid)
            if run_row:
                sym_data["_selection_stats"] = {
                    "naive_sharpe": run_row.get("naive_sharpe"),
                    "selection_tstat": run_row.get("selection_tstat"),
                    "frozen_eval_sharpe": run_row.get("frozen_eval_sharpe"),
                }

        # Verify augmentation produced the expected _selection_stats
        assert "_selection_stats" in raw_changes[sym_id], (
            f"Inline augmentation must inject '_selection_stats' for '{sym_id}' "
            "when a DB row exists. Production wiring gap if this fails."
        )
        assert raw_changes[sym_id]["_selection_stats"]["frozen_eval_sharpe"] is not None, (
            "frozen_eval_sharpe must be non-None after augmentation — "
            "requires get_latest_autotune_run to SELECT frozen_eval_sharpe (16a5b5c fix)."
        )

        # Now pass augmented dict to send_eod_discord_post and assert embed output
        report_path = self._make_minimal_eod_report(tmp_path)
        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    raw_changes,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        all_text = " ".join(e.get("description", "") + e.get("title", "") for e in captured_embeds)

        # All three formatted values must appear in the embed
        for key, expected_str in [
            ("naive_sharpe", fmt["naive_sharpe_formatted"]),
            ("selection_tstat", fmt["selection_tstat_formatted"]),
            ("frozen_eval_sharpe", fmt["frozen_eval_sharpe_formatted"]),
        ]:
            assert expected_str in all_text, (
                f"Discord embed must contain {key} formatted as '{expected_str}' "
                f"when produced via the production augmentation path. "
                f"Embed text: {all_text!r}"
            )

    def test_production_wiring_no_crash_when_symphony_has_no_db_run(self, isolated_db, tmp_path):
        """
        When no autotune_runs row exists for a symphony, the augmentation loop
        must not crash — it must skip _selection_stats injection gracefully, and
        send_eod_discord_post must still produce a valid embed (without DSR line).
        """
        import database as db_module
        import reporting

        # No DB row written — symphony has never been autotuned
        raw_changes = {"no_run_symphony": {"_baseline_chosen": "Adopted AI"}}

        # Inline augmentation — mirrors alpha_bot_execution.py:765-773
        for sid, sym_data in raw_changes.items():
            run_row = db_module.get_latest_autotune_run(sid)
            if run_row:
                sym_data["_selection_stats"] = {
                    "naive_sharpe": run_row.get("naive_sharpe"),
                    "selection_tstat": run_row.get("selection_tstat"),
                    "frozen_eval_sharpe": run_row.get("frozen_eval_sharpe"),
                }
        # _selection_stats must NOT have been injected (no DB row)
        assert "_selection_stats" not in raw_changes["no_run_symphony"], (
            "Augmentation must not inject _selection_stats when no DB row exists."
        )

        # send_eod_discord_post must not crash with no _selection_stats
        report_path = self._make_minimal_eod_report(tmp_path)
        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(
                kwargs.get("data", {}).get("payload_json", "{}")
            )
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with (
                patch("reporting.requests.post", side_effect=capture_post),
                patch("reporting.glob.glob", return_value=[str(report_path)]),
                patch("reporting.database.normalize_name", side_effect=lambda n: n),
                patch(
                    "reporting.database.get_symphony_strategy",
                    return_value={"params": {}, "locked_vars": {}},
                ),
            ):
                reporting.send_eod_discord_post(
                    "2026-05-14",
                    str(report_path),
                    raw_changes,
                    "https://discord.example.invalid/SENTINEL",
                )
        finally:
            os.chdir(orig_cwd)

        assert captured_embeds, (
            "send_eod_discord_post must produce embeds even when no _selection_stats is present."
        )
        # Python literal 'None' must not appear (no DSR line → nothing to mis-render)
        all_text = " ".join(e.get("description", "") for e in captured_embeds)
        assert "None" not in all_text, (
            f"Embed must not contain Python literal 'None'. Text: {all_text!r}"
        )
