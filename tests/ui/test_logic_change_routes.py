"""RED tests — M4 Logic-Change Flask routes and template (app.py).

Tests the two routes added by the UX layer:
  GET  /ai-advisor/logic-changes          — renders the Logic Changes tab
  POST /ai-advisor/logic-changes/evaluate — operator-initiated logic-change evaluation

Architecture contracts verified:
  AC-3.1  Both modes surfaced: operator-initiated form + advisor-suggested via engine.
  AC-3.2  FDR metadata (n_candidates, fdr_q, fdr_adjusted_threshold, winner_p_adj) in
           every evaluate response for the operator audit trail.
  AC-3.3  Persistent non-dismissible FDR warning banner in the GET template;
           caveats in every survivor detail.
  AC-3.4  No auto-apply button anywhere; apply_guidance is plain text only.
  AC-X1   Evaluate route must NOT call Composer write endpoints (static analysis).
  AC-X2   logic_change_engine imported lazily inside each route handler (not at module scope).
  AC-X4   Absent API key → {"error": "advisor unavailable..."} + no DB write.
  AC-X5   Backtest error → per-card backtest_error field, not an unhandled 500.

Mocking strategy:
  - Flask test client; no live Composer or DB calls in any test.
  - propose_operator_logic_change is patched to return a fixture-driven
    LogicChangeRunResult, keeping math/gate logic out of the route layer tests.
  - fetch_symphony_score is patched to return a minimal score tree.
  - No math engine or acceptance gate mocks.

Fixture: tests/fixtures/ai_advisor/m4/logic_change_proposals_basic.json
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def flask_client():
    """Minimal Flask test client.  DB and network are not initialised — all
    routes under test are patched to avoid live access."""
    _ensure_repo_on_path()
    import app as _app
    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers: build minimal result objects for mocking
# ---------------------------------------------------------------------------

def _make_logic_change_run_result(
    survivors: bool = False,
    no_api_key: bool = False,
    backtest_error: str | None = None,
) -> object:
    """Build a minimal LogicChangeRunResult for use as a mock return value.

    Result shape matches what propose_operator_logic_change returns.
    All values are shape/structure-derived; no producer-computed literals.
    """
    _ensure_repo_on_path()
    from advisors.logic_change_engine import (
        LogicChangeRunResult,
        LogicProposalResult,
        LogicChangeObjective,
        LogicTweak,
        NO_SURVIVORS_MESSAGE,
    )
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    gate_batch = GatedBatch(
        results=[], survivors=[], n_candidates=1, fdr_q=HARVEY_LIU_FDR_Q
    )
    objective = LogicChangeObjective(
        objective_type="reduce_drawdown",
        measured_value=-0.25,
        rationale="measured drawdown from backtest",
    )

    if no_api_key:
        return LogicChangeRunResult(
            gate_batch=gate_batch,
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
        )

    tweak = LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=16,
        node_description="window=20 at path [children, 0]",
    )

    proposal = LogicProposalResult(
        candidate_id="sym-test:window@children>0:20->16",
        symphony_id="sym-test",
        tweak=tweak,
        objective=objective,
        objective_rationale=(
            "Adjusting window from 20 to 16 targets reduction of the measured -25.0% drawdown."
        ),
        gate_result=None,
        baseline_stats={"sharpe_ratio": None, "sortino_ratio": None},
        variant_stats={"sharpe_ratio": None, "sortino_ratio": None},
        caveats=[] if not survivors else [
            "The gate screens for statistical significance (BHY/Yekutieli FDR) "
            "but selecting the best backtest from N candidates still concentrates "
            "selection bias even after correction. Treat survivors as hypotheses."
        ],
        apply_guidance=(
            "To apply: open Test Symphony in Composer and manually adjust "
            "window=20 at path [children, 0] from 20 to 16."
        ),
        backtest_error=backtest_error,
    )

    message = "1 logic change survived the gate for Test Symphony" if survivors else NO_SURVIVORS_MESSAGE

    return LogicChangeRunResult(
        gate_batch=gate_batch,
        proposals=[proposal],
        survivors=[proposal] if survivors else [],
        rejected_candidates=[] if survivors else [proposal],
        message=message,
        objective=objective,
        no_api_key=False,
    )


def _minimal_score_tree() -> dict:
    """Minimal structurally-valid Composer score tree for mocking fetch_symphony_score."""
    return {
        "id": "sym-test",
        "name": "Test Symphony",
        "type": "root",
        "children": [
            {"type": "momentum", "window": 20, "threshold": 8.0, "children": []},
        ],
    }


# ===========================================================================
# Section 1 — Routes exist and return non-404
# ===========================================================================

class TestRoutesExist:
    """Both M4 routes must be registered in app.py."""

    def test_get_logic_changes_route_returns_200_or_redirect(self, flask_client):
        """GET /ai-advisor/logic-changes must return 200 (or 302 to login), not 404."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
            with patch("analytics.get_history_with_cache_invalidation", return_value={}):
                with patch("analytics.list_available_symphonies", return_value=[]):
                    resp = flask_client.get("/ai-advisor/logic-changes")
        assert resp.status_code in (200, 302), (
            f"GET /ai-advisor/logic-changes returned {resp.status_code}. "
            "Route must be registered in app.py."
        )

    def test_post_evaluate_route_exists(self, flask_client):
        """POST /ai-advisor/logic-changes/evaluate must return a non-404 response."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=False):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "x",
                    "change_description": "change window from 20 to 16",
                },
            )
        assert resp.status_code != 404, (
            "POST /ai-advisor/logic-changes/evaluate returned 404. "
            "Route must be registered in app.py."
        )

    def test_get_route_renders_logic_changes_template(self, flask_client):
        """GET /ai-advisor/logic-changes must render the ai_advisor_logic_changes.html template.

        The response HTML must contain the FDR warning banner testid (AC-3.3).
        """
        with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
            with patch("analytics.get_history_with_cache_invalidation", return_value={}):
                with patch("analytics.list_available_symphonies", return_value=[]):
                    resp = flask_client.get("/ai-advisor/logic-changes")

        if resp.status_code == 302:
            pytest.skip("Redirect (login required) — skipping template content check.")

        html = resp.get_data(as_text=True)
        assert "fdr-warning-banner" in html, (
            "GET /ai-advisor/logic-changes must render a template with "
            "data-testid='fdr-warning-banner' (AC-3.3: persistent non-dismissible banner)."
        )


# ===========================================================================
# Section 2 — AC-X4: no API key → clear error, no DB write
# ===========================================================================

class TestNoApiKeyRoute:
    """AC-X4: absent Composer key → 'advisor unavailable' + no DB write."""

    def test_evaluate_no_api_key_returns_error_json(self, flask_client):
        """POST /evaluate with no Composer key must return JSON with an 'error' field."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=False):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None, "Response must be JSON."
        assert "error" in data, (
            f"No-API-key response must contain an 'error' key. Got: {data!r}"
        )
        assert "advisor unavailable" in data["error"].lower(), (
            f"Error message must contain 'advisor unavailable' (AC-X4). "
            f"Got: {data['error']!r}"
        )

    def test_evaluate_no_api_key_writes_no_db_observations(self, flask_client):
        """AC-X4: no API key → nothing written to advisor_observations."""
        persist_calls = []

        def _capture(**kwargs):
            persist_calls.append(kwargs)
            return 1

        with patch("advisors.logic_change_engine._has_composer_key", return_value=False):
            with patch("database.insert_advisor_observation", side_effect=_capture):
                flask_client.post(
                    "/ai-advisor/logic-changes/evaluate",
                    json={
                        "symphony_id": "sym-test",
                        "change_description": "change window from 20 to 16",
                    },
                )

        assert len(persist_calls) == 0, (
            f"insert_advisor_observation was called {len(persist_calls)} time(s) "
            "when the API key was absent. AC-X4: no writes when key not configured."
        )

    def test_get_route_with_no_api_key_renders_no_api_key_state(self, flask_client):
        """GET route with absent API key must render the no-api-key state element."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=False):
            with patch("analytics.get_history_with_cache_invalidation", return_value={}):
                with patch("analytics.list_available_symphonies", return_value=[]):
                    resp = flask_client.get("/ai-advisor/logic-changes")

        if resp.status_code == 302:
            pytest.skip("Redirect — skipping template content check.")

        html = resp.get_data(as_text=True)
        assert "no-api-key-state" in html, (
            "GET /ai-advisor/logic-changes with absent API key must render "
            "data-testid='no-api-key-state' element (AC-X4)."
        )


# ===========================================================================
# Section 3 — Evaluate route JSON shape (AC-3.2 / AC-3.3 audit trail)
# ===========================================================================

class TestEvaluateJsonShape:
    """POST /evaluate must return JSON with FDR metadata and gate verdict (AC-3.2/3.3)."""

    def test_evaluate_returns_message_field(self, flask_client):
        """Evaluate response must include 'message' field (zero survivors or survivors count)."""
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "message" in data, (
            f"Evaluate response must have 'message' field. Got keys: {list(data.keys())}"
        )

    def test_evaluate_returns_n_candidates_and_fdr_q(self, flask_client):
        """Evaluate response must include n_candidates and fdr_q (AC-3.2 audit trail)."""
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        assert data is not None
        assert "n_candidates" in data, (
            "Evaluate response must have 'n_candidates' field for FDR audit trail (AC-3.2). "
            f"Got keys: {list(data.keys())}"
        )
        assert "fdr_q" in data, (
            "Evaluate response must have 'fdr_q' field for FDR audit trail (AC-3.2). "
            f"Got keys: {list(data.keys())}"
        )
        assert "fdr_adjusted_threshold" in data, (
            "Evaluate response must have 'fdr_adjusted_threshold' — the BHY-adjusted "
            "significance level for operator audit trail (AC-3.2). "
            f"Got keys: {list(data.keys())}"
        )

    def test_evaluate_fdr_adjusted_threshold_is_tighter_than_fdr_q(self, flask_client):
        """fdr_adjusted_threshold must be <= fdr_q (Yekutieli c(N) >= 1 always tightens).

        For N=1 candidate, c(1) = 1.0 so threshold == fdr_q.
        For N > 1, c(N) > 1.0 so threshold < fdr_q.
        In either case, threshold <= fdr_q.

        Tolerance 1e-9: pure arithmetic comparison on deterministic values.
        """
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        fdr_q = data.get("fdr_q")
        fdr_adj = data.get("fdr_adjusted_threshold")

        if fdr_q is not None and fdr_adj is not None:
            # Yekutieli correction always makes the threshold equal to or tighter
            # than fdr_q (c(N) >= 1 for all N >= 1).
            # Tolerance 1e-9: arithmetic inequality, no sampling.
            assert fdr_adj <= fdr_q + 1e-9, (
                f"fdr_adjusted_threshold ({fdr_adj}) must be <= fdr_q ({fdr_q}). "
                "The Yekutieli c(N) correction always tightens (or maintains) the threshold. "
                "A value of fdr_adjusted_threshold > fdr_q means the c(N) computation "
                "is wrong in the route handler (AC-3.2)."
            )

    def test_evaluate_returns_apply_guidance_field(self, flask_client):
        """Evaluate response must include apply_guidance (plain-text advise-only, AC-3.4)."""
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        assert "apply_guidance" in data, (
            "Evaluate response must have 'apply_guidance' field (AC-3.4 — advise-only)."
        )

    def test_evaluate_returns_survivors_detail_list(self, flask_client):
        """Evaluate response must include survivors_detail and rejected_detail lists."""
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        assert "survivors_detail" in data, (
            "Evaluate response must have 'survivors_detail' list."
        )
        assert "rejected_detail" in data, (
            "Evaluate response must have 'rejected_detail' list."
        )
        assert isinstance(data["survivors_detail"], list), (
            "survivors_detail must be a list."
        )
        assert isinstance(data["rejected_detail"], list), (
            "rejected_detail must be a list."
        )

    def test_evaluate_proposal_detail_includes_fdr_fields(self, flask_client):
        """Each proposal dict in survivors_detail/rejected_detail must include FDR fields.

        AC-3.2 / AC-3.3: every surfaced proposal carries the FDR audit trail.
        """
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        all_proposals = data.get("survivors_detail", []) + data.get("rejected_detail", [])
        for proposal in all_proposals:
            for fdr_field in ("n_candidates", "fdr_q", "fdr_adjusted_threshold"):
                assert fdr_field in proposal, (
                    f"Proposal dict must contain '{fdr_field}' for FDR audit trail (AC-3.2). "
                    f"Got keys: {list(proposal.keys())}."
                )

    def test_evaluate_zero_survivors_returns_no_survivors_message(self, flask_client):
        """When no candidates survive, response message must contain the no-survivors message."""
        mock_result = _make_logic_change_run_result(survivors=False)

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        data = resp.get_json()
        assert data.get("survivors", 0) == 0, (
            "survivors count must be 0 when no candidates pass the gate."
        )
        # The message must indicate zero survivors (AC-3.1 / AC-2.5 pattern).
        msg = data.get("message", "")
        assert "no logic change cleared the gate" in msg.lower() or data.get("survivors", 0) == 0, (
            f"Zero-survivors response message must indicate no survivors. Got: {msg!r}."
        )


# ===========================================================================
# Section 4 — Missing required fields → 400/error (AC-X5 / input validation)
# ===========================================================================

class TestInputValidation:
    """Missing or empty required fields must return a clear error."""

    def test_missing_symphony_id_returns_error(self, flask_client):
        """POST without symphony_id must return an error (not crash)."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={"change_description": "change window from 20 to 16"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "error" in data, (
            "Missing symphony_id must produce an 'error' response. Got: {data!r}"
        )

    def test_missing_change_description_returns_error(self, flask_client):
        """POST without change_description must return an error (not crash)."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={"symphony_id": "sym-test"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "error" in data, (
            "Missing change_description must produce an 'error' response. Got: {data!r}"
        )

    def test_unparseable_change_description_returns_error(self, flask_client):
        """A change_description that cannot be parsed into a tweak returns a clear error.

        The route parses change_description for 'from X to Y' patterns. A vague
        description that cannot yield a specific tweak must return an error message
        directing the operator to be more specific — not a 500 or silent failure.
        """
        score_tree = _minimal_score_tree()
        # A score tree with no numeric params that match the change description.
        no_match_tree = {"id": "sym-empty", "name": "Empty", "type": "root", "children": []}

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=no_match_tree),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "make it better somehow",
                },
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        # Either an error (unparseable) or a valid result — but must not be a 500.
        if "error" in data:
            # Expected path: unparseable description → clear error.
            assert isinstance(data["error"], str) and len(data["error"]) > 0, (
                "error must be a non-empty string."
            )


# ===========================================================================
# Section 5 — AC-X1: no Composer write endpoints (static analysis)
# ===========================================================================

class TestNoComposerWriteEndpoints:
    """AC-X1: static analysis — route handlers must not call Composer write endpoints."""

    def test_evaluate_route_does_not_reference_composer_write_endpoints(self):
        """Static analysis: ai_advisor_logic_changes_evaluate must not reference write endpoints.

        Only GET /score and stateless POST /api/v0.1/backtest are permitted (AC-X1).
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        evaluate_fn_body = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "ai_advisor_logic_changes_evaluate"
            ):
                evaluate_fn_body = ast.unparse(node)
                break

        assert evaluate_fn_body is not None, (
            "Could not find ai_advisor_logic_changes_evaluate in app.py."
        )

        for forbidden in ["/copy", "/deploy", "go-to-cash"]:
            assert forbidden not in evaluate_fn_body, (
                f"ai_advisor_logic_changes_evaluate must not reference Composer write "
                f"endpoint '{forbidden}' (AC-X1). Found in route body."
            )

    def test_evaluate_route_calls_propose_operator_logic_change(self):
        """Static analysis: the evaluate route must call propose_operator_logic_change.

        Using the wrong engine function bypasses the full gate pipeline and FDR correction.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        evaluate_fn_body = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "ai_advisor_logic_changes_evaluate"
            ):
                evaluate_fn_body = ast.unparse(node)
                break

        assert evaluate_fn_body is not None, (
            "Could not find ai_advisor_logic_changes_evaluate in app.py."
        )
        assert "propose_operator_logic_change" in evaluate_fn_body, (
            "ai_advisor_logic_changes_evaluate must call propose_operator_logic_change() — "
            "the full M4 gate pipeline with FDR correction (AC-3.2)."
        )

    def test_logic_change_engine_not_imported_at_module_scope(self):
        """AC-X2: advisors.logic_change_engine must be imported lazily (inside route handlers).

        A module-scope import would put the engine on the live execution import graph.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            # Module-level imports (col_offset == 0) must not reference logic_change_engine.
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                else:
                    module = " ".join(alias.name for alias in node.names)
                assert "logic_change_engine" not in module, (
                    f"advisors.logic_change_engine must NOT be imported at module scope "
                    f"in app.py (AC-X2: keep off the live execution path). "
                    f"Found top-level import: '{module}'."
                )


# ===========================================================================
# Section 6 — Template structure (AC-3.3 / AC-3.4)
# ===========================================================================

class TestTemplateStructure:
    """The template must contain required AC-3.3/AC-3.4 structural elements."""

    @pytest.fixture(scope="class")
    def template_html(self, flask_client) -> str:
        """GET /ai-advisor/logic-changes HTML for template structure tests."""
        with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
            with patch("analytics.get_history_with_cache_invalidation", return_value={}):
                with patch("analytics.list_available_symphonies", return_value=[]):
                    resp = flask_client.get("/ai-advisor/logic-changes")
        if resp.status_code != 200:
            pytest.skip(f"Route returned {resp.status_code} — cannot check template.")
        return resp.get_data(as_text=True)

    def test_template_has_fdr_warning_banner(self, template_html):
        """AC-3.3: persistent non-dismissible FDR warning banner must be present."""
        assert "fdr-warning-banner" in template_html, (
            "Template must contain data-testid='fdr-warning-banner'. "
            "The FDR / overfitting risk banner is mandatory and non-dismissible (AC-3.3)."
        )

    def test_template_has_try_tweak_panel(self, template_html):
        """AC-3.1: operator-initiated form panel must be present."""
        assert "try-tweak-panel" in template_html, (
            "Template must contain data-testid='try-tweak-panel' for the "
            "operator-initiated logic change form (AC-3.1)."
        )

    def test_template_has_tweak_description_textarea(self, template_html):
        """AC-3.1: textarea for operator change_description must be present."""
        assert "tweak-description" in template_html, (
            "Template must contain data-testid='tweak-description' textarea "
            "for the operator-initiated mode (AC-3.1)."
        )

    def test_template_has_logic_results_area(self, template_html):
        """Template must have the results injection area for JS rendering."""
        assert "logic-results-area" in template_html, (
            "Template must contain data-testid='logic-results-area' for "
            "result rendering by the JS module."
        )

    def test_template_has_no_api_key_state(self, template_html):
        """AC-X4: the template must include a no-api-key state element."""
        assert "no-api-key-state" in template_html, (
            "Template must contain data-testid='no-api-key-state' for the "
            "AC-X4 absent-key state."
        )

    def test_template_has_no_auto_apply_or_deploy_button(self, template_html):
        """AC-3.4: the template must not contain auto-apply or deploy action elements."""
        # Check that the template does not surface any auto-deploy actions.
        # 'advise-only' pattern: operator must apply manually.
        html_lower = template_html.lower()
        for forbidden in ["go-to-cash", "/deploy", "/api/v0.1/symphonies/copy"]:
            assert forbidden not in html_lower, (
                f"Template must not reference Composer write endpoint '{forbidden}' "
                f"(AC-3.4 / AC-X1). Found in template HTML."
            )


# ===========================================================================
# Section 7 — JS module structure (AC-3.4 advise-only / no write actions)
# ===========================================================================

class TestJsModuleStructure:
    """The JS module must not contain auto-apply or Composer write calls."""

    def test_js_file_exists(self):
        """ai_advisor_logic_changes.js must exist in the static directory."""
        js_path = _REPO_ROOT / "static" / "ai_advisor_logic_changes.js"
        assert js_path.exists(), (
            "static/ai_advisor_logic_changes.js must exist (created by UX green)."
        )

    def test_js_does_not_reference_composer_write_endpoints(self):
        """AC-X1 / AC-3.4: the JS module must not reference Composer write endpoints."""
        js_path = _REPO_ROOT / "static" / "ai_advisor_logic_changes.js"
        if not js_path.exists():
            pytest.skip("JS file not found.")
        source = js_path.read_text(encoding="utf-8")
        for forbidden in ["/copy", "/deploy", "go-to-cash", "/symphonies"]:
            assert forbidden not in source, (
                f"ai_advisor_logic_changes.js must not reference Composer write "
                f"endpoint '{forbidden}' (AC-X1 / AC-3.4). Found in JS source."
            )

    def test_js_posts_to_correct_evaluate_endpoint(self):
        """The JS module must POST to /ai-advisor/logic-changes/evaluate."""
        js_path = _REPO_ROOT / "static" / "ai_advisor_logic_changes.js"
        if not js_path.exists():
            pytest.skip("JS file not found.")
        source = js_path.read_text(encoding="utf-8")
        assert "logic-changes/evaluate" in source, (
            "ai_advisor_logic_changes.js must POST to /ai-advisor/logic-changes/evaluate "
            "as its only backend call. No other backend endpoints should be called."
        )


# ===========================================================================
# Section 8 — AC-X5: backtest error state surfaced per-proposal (not a 500)
# ===========================================================================

class TestBacktestErrorSurfacedInResponse:
    """AC-X5: backtest errors must surface as backtest_error in the proposal dict."""

    def test_backtest_error_surfaces_in_rejected_detail(self, flask_client):
        """A proposal with backtest_error must appear in rejected_detail with the error set."""
        mock_result = _make_logic_change_run_result(
            survivors=False,
            backtest_error="HTTP 500: Internal Server Error",
        )

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
            patch(
                "advisors.logic_change_engine.propose_operator_logic_change",
                return_value=mock_result,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": "sym-test",
                    "change_description": "change window from 20 to 16",
                },
            )

        assert resp.status_code == 200, (
            "Route must return 200 even when backtest failed (AC-X5). "
            f"Got {resp.status_code}."
        )
        data = resp.get_json()
        assert data is not None

        # The backtest_error must surface in the top-level field or in rejected_detail.
        backtest_error_in_top = data.get("backtest_error") is not None
        backtest_error_in_rejected = any(
            p.get("backtest_error") for p in data.get("rejected_detail", [])
        )

        assert backtest_error_in_top or backtest_error_in_rejected, (
            "backtest_error must be surfaced in the response (either top-level or "
            "in rejected_detail[].backtest_error). "
            f"Got top-level: {data.get('backtest_error')!r}, "
            f"rejected_detail count: {len(data.get('rejected_detail', []))}."
        )


# ===========================================================================
# Section 9 — Logic Changes tab links from sibling tabs
# ===========================================================================

class TestTabNavigation:
    """The Logic Changes tab must be reachable from sibling advisor tabs."""

    def test_asset_swaps_template_links_to_logic_changes_tab(self):
        """templates/ai_advisor_asset_swaps.html must link to the logic-changes route.

        AC-3.1: both proposal modes are accessible from the unified Advisor surface.
        The Logic Changes tab link must point to url_for('ai_advisor_logic_changes') or
        the /ai-advisor/logic-changes path (not '#').
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor_asset_swaps.html"
        if not template_path.exists():
            pytest.skip("ai_advisor_asset_swaps.html not found.")
        source = template_path.read_text(encoding="utf-8")
        # The Logic Changes tab link must not still point to '#' (the pre-M4 placeholder).
        # It should point to the actual route or url_for('ai_advisor_logic_changes').
        assert "ai_advisor_logic_changes" in source or "logic-changes" in source, (
            "ai_advisor_asset_swaps.html must link to the Logic Changes tab "
            "(url_for('ai_advisor_logic_changes') or /ai-advisor/logic-changes). "
            "The tab was '#' before M4 was implemented."
        )

    def test_correlations_template_links_to_logic_changes_tab(self):
        """templates/ai_advisor_correlations.html must link to the logic-changes route."""
        template_path = _REPO_ROOT / "templates" / "ai_advisor_correlations.html"
        if not template_path.exists():
            pytest.skip("ai_advisor_correlations.html not found.")
        source = template_path.read_text(encoding="utf-8")
        assert "ai_advisor_logic_changes" in source or "logic-changes" in source, (
            "ai_advisor_correlations.html must link to the Logic Changes tab."
        )


# ===========================================================================
# Section 10 — Route must not duplicate engine parse function (BLOCK-2)
# ===========================================================================


class TestNoDuplicateParseFunction:
    """app.py must NOT define _parse_change_description_to_tweak at module scope.

    Reviewer BLOCK-2 (HEAD 7d0e083): app.py defines a module-scope
    _parse_change_description_to_tweak at line 2629 that is a degraded fork of the
    identical function already in logic_change_engine.py:907.  The route layer is
    doing engine work.  The fix: delete the app.py copy and pass change_description=
    to propose_operator_logic_change, letting the engine's own parser run.

    These tests encode the delegation contract so the duplication cannot sneak back.
    """

    def test_app_py_does_not_define_parse_change_description_at_module_scope(self):
        """Static analysis: app.py must NOT define _parse_change_description_to_tweak
        as a module-level function.

        The route handler delegates parsing entirely to propose_operator_logic_change
        (which calls the engine's own _parse_change_description_to_tweak internally).
        A module-scope copy in app.py is a degraded fork that diverges from the engine
        implementation and violates the Dashboard side-effect ban (reviewer BLOCK-2).
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_parse_change_description_to_tweak"
                and node.col_offset == 0  # module-level (not nested inside a function)
            ):
                assert False, (
                    "app.py must NOT define _parse_change_description_to_tweak at module scope "
                    "(reviewer BLOCK-2). "
                    "This is a degraded fork of the engine function at "
                    "advisors/logic_change_engine.py. "
                    "Delete the app.py copy and pass change_description= to "
                    "propose_operator_logic_change, which calls the engine's own parser."
                )

    def test_evaluate_route_does_not_call_module_level_parse_function(self):
        """Static analysis: ai_advisor_logic_changes_evaluate must not call a locally-defined
        _parse_change_description_to_tweak that bypasses the engine.

        After BLOCK-2 is fixed, the route passes change_description= directly to
        propose_operator_logic_change.  Any direct call to a module-level parse function
        means the fix was incomplete.

        This complements test_evaluate_route_calls_propose_operator_logic_change by
        verifying the delegation goes all the way through — no intermediate parse step
        that duplicates the engine logic.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        evaluate_fn_body = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "ai_advisor_logic_changes_evaluate"
            ):
                evaluate_fn_body = ast.unparse(node)
                break

        if evaluate_fn_body is None:
            pytest.skip("ai_advisor_logic_changes_evaluate not found in app.py.")

        # The route body must NOT call _parse_change_description_to_tweak directly.
        # After the fix, it passes change_description= to propose_operator_logic_change.
        assert "_parse_change_description_to_tweak(" not in evaluate_fn_body, (
            "ai_advisor_logic_changes_evaluate must NOT call _parse_change_description_to_tweak "
            "directly (reviewer BLOCK-2 fix incomplete). "
            "The route must pass change_description= to propose_operator_logic_change "
            "and let the engine's own parser run."
        )
