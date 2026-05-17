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
import pathlib
import sqlite3
from unittest.mock import MagicMock, patch, call
import tempfile
import os

import pytest

# ---------------------------------------------------------------------------
# Fixture loading helpers (PA-18: derived from JSON, never hardcoded)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parents[2]
    / "tests"
    / "fixtures"
    / "autotuner"
    / "dsr_surfacing"
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

def _dsr_data_from_fixture(fixture_row: dict) -> dict:
    """
    Build the dsr_data dict from a fixture row.  This mirrors what
    run_autotuner's caller (alpha_bot_execution.py) should assemble when
    constructing the optimization_results blob for send_eod_discord_post.

    Shape (proposed by test-writer; implementer must match):
      {
        "naive_sharpe":      float | None,
        "deflated_sharpe":   float | None,
        "frozen_eval_sharpe": float | None,
      }
    """
    row = fixture_row["row"]
    return {
        "naive_sharpe":       row["naive_sharpe"],
        "deflated_sharpe":    row["deflated_sharpe"],
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
            "summary": {"total_monitored": 1, "total_triggered": 0, "positive_guard_alpha_count": 0},
            "tomorrow_target_holdings": {},
            "triggers": [],
        }
        path = tmp_path / "post_mortem_2026-05-14.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def _build_optimization_results_with_dsr(self, symphony_id: str, dsr_data: dict) -> dict:
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
                "_dsr_data": dsr_data,
            }
        }

    def test_discord_embed_contains_naive_sharpe_when_present(
        self, full_run_fixture, tmp_path
    ):
        """
        AC-1 + AC-5: When naive_sharpe is non-None, the Discord embed string
        for that symphony must contain the formatted naive_sharpe value to 4
        decimal places and a human-readable label (not 'naive_sharpe' raw column).

        This test will FAIL (RED) until send_eod_discord_post reads dsr_data
        from optimization_results and formats it into the embed description.
        """
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        dsr_data = _dsr_data_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_dsr(sym_id, dsr_data)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(kwargs.get("data", {}).get("payload_json", "{}"))
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("reporting.requests.post", side_effect=capture_post), \
                 patch("reporting.glob.glob", return_value=[str(report_path)]), \
                 patch("reporting.database.normalize_name", side_effect=lambda n: n), \
                 patch("reporting.database.get_symphony_strategy", return_value={"params": {}, "locked_vars": {}}):
                reporting.send_eod_discord_post(
                    "2026-05-14", str(report_path), opt_results, "https://discord.example.invalid/SENTINEL"
                )
        finally:
            os.chdir(orig_cwd)

        # Find the per-symphony optimization embed
        sym_embeds = [
            e for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]

        assert sym_embeds, (
            f"No Discord embed found for symphony '{sym_id}'. "
            "send_eod_discord_post must produce a per-symphony embed that includes DSR data."
        )

        # The combined text of all symphony embeds must contain the formatted naive_sharpe.
        all_text = " ".join(
            e.get("description", "") + e.get("title", "")
            for e in sym_embeds
        )

        expected_naive = fmt["naive_sharpe_formatted"]  # "1.8432" — from fixture
        assert expected_naive in all_text, (
            f"Discord embed must contain naive_sharpe formatted as '{expected_naive}' "
            f"(4 decimal places, from fixture). "
            f"Found embed text: {all_text!r}. "
            "AC-1 + AC-5 violation."
        )

    def test_discord_embed_contains_deflated_sharpe_when_present(
        self, full_run_fixture, tmp_path
    ):
        """
        AC-1: Discord embed must contain the deflated_sharpe (DSR) value with a
        human-readable label. The label must NOT be the raw DB column name
        'deflated_sharpe'; it must be something operator-readable like 'DSR'.
        """
        import reporting

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        dsr_data = _dsr_data_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_dsr(sym_id, dsr_data)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(kwargs.get("data", {}).get("payload_json", "{}"))
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("reporting.requests.post", side_effect=capture_post), \
                 patch("reporting.glob.glob", return_value=[str(report_path)]), \
                 patch("reporting.database.normalize_name", side_effect=lambda n: n), \
                 patch("reporting.database.get_symphony_strategy", return_value={"params": {}, "locked_vars": {}}):
                reporting.send_eod_discord_post(
                    "2026-05-14", str(report_path), opt_results, "https://discord.example.invalid/SENTINEL"
                )
        finally:
            os.chdir(orig_cwd)

        sym_embeds = [
            e for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]
        assert sym_embeds, (
            f"No Discord embed found for symphony '{sym_id}'. "
            "send_eod_discord_post must produce a per-symphony embed."
        )

        all_text = " ".join(
            e.get("description", "") + e.get("title", "")
            for e in sym_embeds
        )

        expected_dsr = fmt["deflated_sharpe_formatted"]  # "0.9217" — from fixture
        assert expected_dsr in all_text, (
            f"Discord embed must contain deflated_sharpe formatted as '{expected_dsr}'. "
            f"Found embed text: {all_text!r}. AC-1 + AC-5 violation."
        )

        # Raw DB column name is NOT an operator-readable label
        assert "deflated_sharpe" not in all_text.lower().replace(expected_dsr, ""), (
            "Discord embed must NOT use the raw DB column name 'deflated_sharpe' as a label. "
            "Use an operator-readable label such as 'DSR'. AC-1 violation."
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
        dsr_data = _dsr_data_from_fixture(full_run_fixture)
        sym_id = row["symphony_id"]

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_dsr(sym_id, dsr_data)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(kwargs.get("data", {}).get("payload_json", "{}"))
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("reporting.requests.post", side_effect=capture_post), \
                 patch("reporting.glob.glob", return_value=[str(report_path)]), \
                 patch("reporting.database.normalize_name", side_effect=lambda n: n), \
                 patch("reporting.database.get_symphony_strategy", return_value={"params": {}, "locked_vars": {}}):
                reporting.send_eod_discord_post(
                    "2026-05-14", str(report_path), opt_results, "https://discord.example.invalid/SENTINEL"
                )
        finally:
            os.chdir(orig_cwd)

        sym_embeds = [
            e for e in captured_embeds
            if sym_id.replace("_", " ") in e.get("title", "").lower()
            or sym_id in e.get("title", "").lower()
            or sym_id in e.get("description", "").lower()
        ]
        assert sym_embeds, f"No Discord embed found for symphony '{sym_id}'."

        all_text = " ".join(
            e.get("description", "") + e.get("title", "")
            for e in sym_embeds
        )

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
        dsr_data = _dsr_data_from_fixture(legacy_run_fixture)
        sym_id = row["symphony_id"]

        assert dsr_data["naive_sharpe"] is None
        assert dsr_data["deflated_sharpe"] is None
        assert dsr_data["frozen_eval_sharpe"] is None

        report_path = self._make_minimal_eod_report(tmp_path)
        opt_results = self._build_optimization_results_with_dsr(sym_id, dsr_data)

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(kwargs.get("data", {}).get("payload_json", "{}"))
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("reporting.requests.post", side_effect=capture_post), \
                 patch("reporting.glob.glob", return_value=[str(report_path)]), \
                 patch("reporting.database.normalize_name", side_effect=lambda n: n), \
                 patch("reporting.database.get_symphony_strategy", return_value={"params": {}, "locked_vars": {}}):
                reporting.send_eod_discord_post(
                    "2026-05-14", str(report_path), opt_results, "https://discord.example.invalid/SENTINEL"
                )
        finally:
            os.chdir(orig_cwd)

        # Should not raise — test reaching here means no crash
        all_embed_text = " ".join(
            e.get("description", "") + e.get("title", "")
            for e in captured_embeds
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
    AC-3: /api/autotune-runs route must return naive_sharpe, deflated_sharpe,
    and frozen_eval_sharpe for each row in its JSON response.

    AC-5: Numeric fields present as floats (4-decimal precision contract is
    on the rendering layer, not the JSON API — the API returns raw floats).

    AC-8 (RED): A row with all three values present → JSON response contains them.
    AC-9 (RED): A row with deflated_sharpe = None → JSON response handles it
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
            f"With no rows in autotune_runs the response must be an empty list; "
            f"got {data!r}."
        )

    def test_api_autotune_runs_row_with_all_three_sharpe_fields_present(
        self, full_run_fixture, isolated_db, app_client
    ):
        """
        AC-3 + AC-8: A row with all three Sharpe fields populated → the JSON
        response for that row must include naive_sharpe, deflated_sharpe, and
        frozen_eval_sharpe with the correct values (within float precision).

        This test will FAIL (RED) until:
          A. /api/autotune-runs route exists.
          B. The route queries naive_sharpe, deflated_sharpe, frozen_eval_sharpe.
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
            deflated_sharpe=row["deflated_sharpe"],
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
        assert "deflated_sharpe" in api_row, (
            "AC-3: /api/autotune-runs response row must include 'deflated_sharpe' key."
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
        assert api_row["naive_sharpe"] == pytest.approx(
            row["naive_sharpe"], rel=1e-6
        ), (
            f"naive_sharpe mismatch: fixture={row['naive_sharpe']}, "
            f"api={api_row['naive_sharpe']}. Float drift > 1e-6 relative."
        )
        assert api_row["deflated_sharpe"] == pytest.approx(
            row["deflated_sharpe"], rel=1e-6
        ), (
            f"deflated_sharpe mismatch: fixture={row['deflated_sharpe']}, "
            f"api={api_row['deflated_sharpe']}."
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
        AC-9: A legacy row with deflated_sharpe = None (pre-O2 migration) must
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
            deflated_sharpe=row["deflated_sharpe"],      # None
            naive_sharpe=row["naive_sharpe"],            # None
            validation_sharpe=row["validation_sharpe"],  # None
            frozen_eval_sharpe=row["frozen_eval_sharpe"], # None
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
        assert api_row.get("deflated_sharpe") is None, (
            f"deflated_sharpe must be null (JSON) for a legacy NULL row; got {api_row.get('deflated_sharpe')!r}."
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

    def test_ai_advisor_page_has_dsr_column_header(self, app_client):
        """
        AC-4: The recent-runs panel HTML must contain a column header for DSR.
        Acceptable: 'DSR', 'Deflated Sharpe', 'deflated sharpe' (case-insensitive).
        """
        resp = app_client.get("/ai-advisor")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8").lower()

        dsr_headers = ["dsr", "deflated sharpe"]
        has_dsr_col = any(h in html for h in dsr_headers)
        assert has_dsr_col, (
            f"GET /ai-advisor HTML must contain a DSR column header "
            f"(one of: {dsr_headers}). AC-4 violation."
        )

    def test_ai_advisor_page_has_naive_sharpe_column_header(self, app_client):
        """
        AC-4: The recent-runs panel HTML must contain a column header for naive Sharpe.
        Acceptable: 'Naive', 'Naive Sharpe', 'naive sharpe' (case-insensitive).
        """
        resp = app_client.get("/ai-advisor")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8").lower()

        naive_headers = ["naive", "naive sharpe"]
        has_naive_col = any(h in html for h in naive_headers)
        assert has_naive_col, (
            f"GET /ai-advisor HTML must contain a Naive Sharpe column header "
            f"(one of: {naive_headers}). AC-4 violation."
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
            deflated_sharpe=row["deflated_sharpe"],
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
        for key in ("naive_sharpe", "deflated_sharpe", "frozen_eval_sharpe"):
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
            "summary": {"total_monitored": 2, "total_triggered": 1, "positive_guard_alpha_count": 1},
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
        AC-7: The main AlphaBot EOD Analysis embed must be present even when
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
                "_dsr_data": {
                    "naive_sharpe": 1.5,
                    "deflated_sharpe": 0.8,
                    "frozen_eval_sharpe": 0.6,
                },
            }
        }

        captured_embeds = []

        def capture_post(url, **kwargs):
            body = kwargs.get("json") or json.loads(kwargs.get("data", {}).get("payload_json", "{}"))
            captured_embeds.extend(body.get("embeds", []))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            return mock_resp

        orig_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with patch("reporting.requests.post", side_effect=capture_post), \
                 patch("reporting.glob.glob", return_value=[str(report_path)]), \
                 patch("reporting.database.normalize_name", side_effect=lambda n: n), \
                 patch("reporting.database.get_symphony_strategy", return_value={"params": {}, "locked_vars": {}}):
                reporting.send_eod_discord_post(
                    "2026-05-14", str(report_path), opt_with_dsr, "https://discord.example.invalid/SENTINEL"
                )
        finally:
            os.chdir(orig_cwd)

        assert captured_embeds, "send_eod_discord_post must send at least one embed."

        main_embed_titles = [
            e.get("title", "")
            for e in captured_embeds
            if "AlphaBot EOD" in e.get("title", "") or "EOD Analysis" in e.get("title", "")
        ]
        assert main_embed_titles, (
            "AC-7: The main 'AlphaBot EOD Analysis' embed must still be present. "
            f"All embed titles found: {[e.get('title', '') for e in captured_embeds]!r}. "
            "Regression: the implementer must not remove the main embed."
        )


# ===========================================================================
# PRODUCTION WIRING — alpha_bot_execution.py augmentation gap (R/G/R round 2)
# ===========================================================================

class TestProductionWiringAugmentsOptimizationResultsWithDSR:
    """
    Gap identified after initial GREEN: the tests in TestDiscordEmbedIncludesDSR
    inject _dsr_data directly into optimization_results, so they pass. But
    alpha_bot_execution.py:768 passes autotuner_changes straight from
    run_autotuner() with no _dsr_data key — meaning the DSR line is silently
    omitted from the Discord embed in production.

    These RED tests exercise the production call path by simulating
    optimization_results as returned by run_autotuner (no _dsr_data) and
    asserting that alpha_bot_execution augments it with DB-fetched DSR data
    before calling send_eod_discord_post.

    Implementation required (alpha_bot_execution.py):
      After autotuner_changes = autotuner.run_autotuner(...) and before
      reporting.send_eod_discord_post(...), loop over autotuner_changes keys
      and call database.get_latest_autotune_run(sym_id) for each symphony,
      injecting the result as _dsr_data. Handle None (no run yet) gracefully.

    Fixture provenance (PA-18):
      tests/fixtures/autotuner/dsr_surfacing/autotune_run_full_values.json
    """

    def _make_minimal_eod_report(self, tmp_path) -> str:
        report = {
            "date": "2026-05-14",
            "summary": {"total_monitored": 1, "total_triggered": 0, "positive_guard_alpha_count": 0},
            "tomorrow_target_holdings": {},
            "triggers": [],
        }
        path = tmp_path / "post_mortem_2026-05-14.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return str(path)

    def test_production_wiring_discord_embed_contains_dsr_when_db_has_run(
        self, full_run_fixture, isolated_db, tmp_path
    ):
        """
        When alpha_bot_execution.py calls reporting.send_eod_discord_post with
        optimization_results that have NO _dsr_data key (as returned by
        run_autotuner), AND a matching autotune_runs row exists in the DB,
        the Discord embed for that symphony must still contain the DSR line.

        This tests the production augmentation path in alpha_bot_execution.py.

        This test will FAIL (RED) until alpha_bot_execution.py is updated to
        call database.get_latest_autotune_run() per symphony and inject _dsr_data
        into optimization_results before calling send_eod_discord_post.

        Approach: import and call the relevant section of alpha_bot_execution's
        EOD path by directly testing the augmentation logic. Since
        alpha_bot_execution.py is a script with side-effects, we test the
        augmentation function directly if the implementer extracts it, OR we
        verify via the send_eod_discord_post output when the production call
        flow is simulated.
        """
        import database as db_module

        row = full_run_fixture["row"]
        fmt = full_run_fixture["format_expectations"]
        sym_id = row["symphony_id"]

        # Write a real autotune_runs row to the isolated DB
        db_module.save_autotune_run(
            run_timestamp=row["run_timestamp"],
            symphony_id=sym_id,
            oos_alpha=row["oos_alpha"],
            train_alpha=row["train_alpha"],
            baseline_decision=row["baseline_decision"],
            fallback_oos_alpha=row["fallback_oos_alpha"],
            default_oos_alpha=row["default_oos_alpha"],
            deflated_sharpe=row["deflated_sharpe"],
            naive_sharpe=row["naive_sharpe"],
            validation_sharpe=row["validation_sharpe"],
            frozen_eval_sharpe=row["frozen_eval_sharpe"],
        )

        # Simulate optimization_results as returned by run_autotuner —
        # NO _dsr_data key, just the param-delta shape.
        raw_autotuner_changes = {
            sym_id: {
                "_baseline_chosen": row["baseline_decision"],
                # no _dsr_data — this is the production gap
            }
        }

        # The production augmentation: alpha_bot_execution must call
        # database.get_latest_autotune_run per symphony and inject _dsr_data.
        # We test by importing the augmentation helper (if extracted) or by
        # simulating the full EOD flow with the real database call in place.
        import alpha_bot_execution

        augmented = alpha_bot_execution.augment_optimization_results_with_dsr(
            raw_autotuner_changes
        )

        assert sym_id in augmented, (
            f"augment_optimization_results_with_dsr must return a dict keyed by symphony_id. "
            f"Key '{sym_id}' not found."
        )
        assert "_dsr_data" in augmented[sym_id], (
            f"augment_optimization_results_with_dsr must inject '_dsr_data' for "
            f"symphony '{sym_id}' when an autotune_runs row exists. "
            "alpha_bot_execution.py production wiring gap — implement "
            "augment_optimization_results_with_dsr() and call it before send_eod_discord_post."
        )

        dsr = augmented[sym_id]["_dsr_data"]
        assert dsr.get("naive_sharpe") is not None, (
            "Injected _dsr_data must contain naive_sharpe from the DB row."
        )
        assert dsr.get("deflated_sharpe") is not None, (
            "Injected _dsr_data must contain deflated_sharpe from the DB row."
        )
        assert dsr.get("frozen_eval_sharpe") is not None, (
            "Injected _dsr_data must contain frozen_eval_sharpe from the DB row."
        )

        # Values must match the fixture row (float tolerance: see test class docstring)
        assert dsr["naive_sharpe"] == pytest.approx(row["naive_sharpe"], rel=1e-6), (
            f"Injected naive_sharpe={dsr['naive_sharpe']} does not match "
            f"DB row value={row['naive_sharpe']} (rel tolerance 1e-6)."
        )
        assert dsr["deflated_sharpe"] == pytest.approx(row["deflated_sharpe"], rel=1e-6), (
            f"Injected deflated_sharpe={dsr['deflated_sharpe']} does not match "
            f"DB row value={row['deflated_sharpe']}."
        )
        assert dsr["frozen_eval_sharpe"] == pytest.approx(row["frozen_eval_sharpe"], rel=1e-6), (
            f"Injected frozen_eval_sharpe={dsr['frozen_eval_sharpe']} does not match "
            f"DB row value={row['frozen_eval_sharpe']}."
        )

    def test_production_wiring_handles_symphony_with_no_db_run(
        self, isolated_db
    ):
        """
        When no autotune_runs row exists for a symphony (first-run case),
        augment_optimization_results_with_dsr must still return the symphony's
        entry without crashing. _dsr_data should be present with all None values
        (or the key may be absent — both are acceptable as long as the function
        does not raise and send_eod_discord_post renders N/A gracefully).

        This test will FAIL (RED) until augment_optimization_results_with_dsr
        exists in alpha_bot_execution.py.
        """
        import alpha_bot_execution

        raw = {
            "no_run_symphony": {
                "_baseline_chosen": "Adopted AI",
            }
        }

        # Must not raise
        try:
            augmented = alpha_bot_execution.augment_optimization_results_with_dsr(raw)
        except AttributeError:
            pytest.fail(
                "alpha_bot_execution.augment_optimization_results_with_dsr does not exist. "
                "Add this function to alpha_bot_execution.py. Production wiring gap."
            )
        except Exception as exc:
            pytest.fail(
                f"augment_optimization_results_with_dsr raised unexpectedly for a "
                f"symphony with no DB row: {exc}"
            )

        assert "no_run_symphony" in augmented, (
            "augment_optimization_results_with_dsr must return all input symphonies "
            "even when no DB row exists."
        )
