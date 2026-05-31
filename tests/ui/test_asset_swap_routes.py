"""RED/GREEN tests — M3 Asset Swap Flask routes (app.py).

Tests the two routes added by the UX layer:
  GET  /ai-advisor/asset-swaps          — renders the tab (AC-X4 no-key state)
  POST /ai-advisor/asset-swaps/evaluate — operator-initiated swap evaluation (AC-2.1)

Architecture contracts:
  - AC-X1: evaluate route must NOT call Composer write endpoints.
  - AC-X2: app.py imports asset_swap_engine lazily (not at module scope).
  - AC-X4: absent API key → {"error": "advisor unavailable..."} + no DB write.
  - AC-2.1: evaluate route uses propose_operator_swap (not evaluate_single_swap)
            so that the SwapObjective type + SwapRunResult are correctly wired.
  - AC-2.5: zero survivors → JSON response includes the no-survivors message
            (not a silent empty list).
  - Route returns JSON with required keys: gate_batch presence, caveats, apply_guidance.
  - No apply/deploy/trade button in the template (AC-X1 advise-only).

Mocking strategy:
  - Flask test client; no live Composer calls.
  - propose_operator_swap is patched to return a fixture-driven SwapRunResult.
  - fetch_symphony_score is patched to return a minimal score tree.
  - No math engine or acceptance gate mocks.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import sys
import types
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
    """Minimal Flask test client.  Database is not initialised; routes under
    test are mocked to avoid live DB/network access."""
    _ensure_repo_on_path()
    import app as _app
    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Helper: build a minimal SwapRunResult for mocking
# ---------------------------------------------------------------------------

def _make_swap_run_result(survivors: bool = False, no_api_key: bool = False) -> object:
    """Build a minimal SwapRunResult for use as a mock return value."""
    _ensure_repo_on_path()
    from advisors.asset_swap_engine import (
        SwapRunResult, SwapProposalResult, SwapObjective, NO_SURVIVORS_MESSAGE
    )
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    gate_batch = GatedBatch(results=[], survivors=[], n_candidates=1, fdr_q=HARVEY_LIU_FDR_Q)
    objective = SwapObjective(
        objective_type="reduce_correlation",
        target_pair=("sym-test", "sym-other"),
        measured_value=0.85,
    )

    if no_api_key:
        return SwapRunResult(
            gate_batch=gate_batch,
            no_api_key=True,
            message="advisor unavailable: API key not configured",
            objective=objective,
        )

    proposal = SwapProposalResult(
        candidate_id="sym-test:SPY->IALT",
        symphony_id="sym-test",
        incumbent_asset="SPY",
        candidate_asset="IALT",
        objective=objective,
        objective_rationale="IALT has low correlation with the target pair.",
        gate_result=None,
        apply_guidance="To apply: open sym-test in Composer and swap SPY → IALT manually.",
    )

    message = "1 swap survived the gate" if survivors else NO_SURVIVORS_MESSAGE

    return SwapRunResult(
        gate_batch=gate_batch,
        proposals=[proposal],
        survivors=[proposal] if survivors else [],
        rejected_candidates=[] if survivors else [proposal],
        message=message,
        objective=objective,
    )


# ===========================================================================
# Section 1 — Route existence
# ===========================================================================

class TestRoutesExist:
    """Both M3 routes must be registered in app.py."""

    def test_get_asset_swaps_route_returns_200_or_redirect(self, flask_client):
        """GET /ai-advisor/asset-swaps must return a 200 response (not 404)."""
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            with patch("analytics.get_history_with_cache_invalidation", return_value={}):
                with patch("analytics.list_available_symphonies", return_value=[]):
                    resp = flask_client.get("/ai-advisor/asset-swaps")
        assert resp.status_code in (200, 302), (
            f"GET /ai-advisor/asset-swaps returned {resp.status_code}. "
            "The route must be registered and return 200 (or 302 redirect to login)."
        )

    def test_post_evaluate_route_exists(self, flask_client):
        """POST /ai-advisor/asset-swaps/evaluate must return a non-404 response."""
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=False):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "x", "from_ticker": "A", "to_ticker": "B"},
            )
        assert resp.status_code != 404, (
            "POST /ai-advisor/asset-swaps/evaluate returned 404. "
            "The route must be registered in app.py."
        )


# ===========================================================================
# Section 2 — AC-X4: no API key → clear error, no DB write
# ===========================================================================

class TestNoApiKeyRoute:
    """AC-X4: absent key → 'advisor unavailable' + no DB write."""

    def test_evaluate_no_api_key_returns_error_json(self, flask_client):
        """POST /evaluate with no Composer key must return JSON with an error field.

        The error must contain 'advisor unavailable' per AC-X4.
        """
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=False):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None, "Response must be JSON."
        assert "error" in data, (
            f"Response must contain an 'error' key when API key is absent. Got: {data!r}"
        )
        assert "advisor unavailable" in data["error"].lower(), (
            f"Error message must contain 'advisor unavailable' per AC-X4. "
            f"Got: {data['error']!r}"
        )

    def test_evaluate_no_api_key_writes_no_db_observations(self, flask_client):
        """AC-X4: no API key → nothing written to advisor_observations."""
        persist_calls = []

        def _capture(**kwargs):
            persist_calls.append(kwargs)
            return 1

        with patch("advisors.asset_swap_engine._has_composer_key", return_value=False):
            with patch("database.insert_advisor_observation", side_effect=_capture):
                flask_client.post(
                    "/ai-advisor/asset-swaps/evaluate",
                    json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
                )

        assert len(persist_calls) == 0, (
            f"insert_advisor_observation was called {len(persist_calls)} time(s) "
            "when the API key was absent. AC-X4: no writes when key is not configured."
        )


# ===========================================================================
# Section 3 — AC-2.1: evaluate route uses propose_operator_swap
# ===========================================================================

class TestEvaluateRouteUsesCorrectApi:
    """The evaluate route must use propose_operator_swap (not evaluate_single_swap).

    evaluate_single_swap is the old M1-era API that returns SwapProposalResult with
    a plain string objective. propose_operator_swap is the M3 API that returns
    SwapRunResult with a typed SwapObjective and gate_batch.

    Using the wrong function bypasses the SwapObjective type contract and the
    gate_batch persistence — a silent AC-2.3/AC-X3 violation.
    """

    def test_evaluate_route_calls_propose_operator_swap_not_evaluate_single_swap(self):
        """Static analysis: the evaluate route handler must call propose_operator_swap.

        Any call to evaluate_single_swap in the route handler would bypass the
        typed SwapObjective (Gate-1 Resolution #2) and SwapRunResult contract.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the ai_advisor_asset_swaps_evaluate function body.
        evaluate_fn_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                evaluate_fn_body = ast.unparse(node)
                break

        assert evaluate_fn_body is not None, (
            "Could not find ai_advisor_asset_swaps_evaluate in app.py."
        )

        # The route must call propose_operator_swap.
        assert "propose_operator_swap" in evaluate_fn_body, (
            "ai_advisor_asset_swaps_evaluate must call propose_operator_swap() — "
            "the M3 typed API that accepts a SwapObjective and returns SwapRunResult. "
            "Using evaluate_single_swap() bypasses the SwapObjective type contract "
            "(Gate-1 Resolution #2) and the gate_batch field (AC-2.3 audit trail)."
        )

    def test_evaluate_route_does_not_call_evaluate_single_swap(self):
        """Static analysis: the evaluate route must NOT call evaluate_single_swap.

        evaluate_single_swap uses a plain string objective — it cannot satisfy the
        typed SwapObjective contract that gate_batch, objective_rationale, and AC-2.3
        depend on.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        evaluate_fn_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                evaluate_fn_body = ast.unparse(node)
                break

        if evaluate_fn_body is None:
            pytest.skip("ai_advisor_asset_swaps_evaluate not found — skipping.")

        assert "evaluate_single_swap" not in evaluate_fn_body, (
            "ai_advisor_asset_swaps_evaluate must NOT call evaluate_single_swap(). "
            "This function uses a plain string objective and returns the old "
            "SwapProposalResult without gate_batch or SwapObjective — it cannot "
            "satisfy the M3 typed contract (Gate-1 Resolution #2 + AC-2.3)."
        )

    def test_evaluate_route_passes_swap_objective_type(self):
        """The evaluate route must construct a SwapObjective and pass it to propose_operator_swap.

        A plain string objective bypasses the typed objective-direction contract.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")

        # The route source must reference SwapObjective (imported or constructed).
        assert "SwapObjective" in source, (
            "app.py must reference SwapObjective in the ai_advisor_asset_swaps_evaluate handler. "
            "The route must construct a typed SwapObjective (not a plain string) and pass it "
            "to propose_operator_swap — Gate-1 Resolution #2 requires objective-direction to "
            "be typed and measurable, not a free-form string."
        )


# ===========================================================================
# Section 4 — Response shape: gate_batch and key fields present
# ===========================================================================

class TestEvaluateResponseShape:
    """The evaluate route JSON response must expose the required M3 fields."""

    def test_evaluate_response_contains_gate_batch_or_gate_decision(self, flask_client):
        """The response must surface gate result information for operator audit trail (AC-2.3).

        Either a gate_batch field (preferred — full CandidateGateResult audit trail)
        or a gate_decision string must be present.
        """
        mock_result = _make_swap_run_result(survivors=False)

        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            with patch("symphony_logic.fetch_symphony_score", return_value={"id": "sym-test", "type": "root", "children": [{"type": "asset", "ticker": "SPY"}]}):
                with patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result):
                    resp = flask_client.post(
                        "/ai-advisor/asset-swaps/evaluate",
                        json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
                    )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

        has_gate_info = any(
            k in data for k in ("gate_batch", "gate_decision", "gate_result", "verdict")
        )
        assert has_gate_info, (
            f"Response must contain gate result information (gate_batch, gate_decision, "
            f"gate_result, or verdict). Got keys: {sorted(data.keys())}. "
            "AC-2.3: every surfaced swap must show the gate verdict."
        )

    def test_evaluate_response_contains_apply_guidance(self, flask_client):
        """The response must include apply_guidance — the advise-only instruction (AC-X1)."""
        mock_result = _make_swap_run_result(survivors=True)

        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            with patch("symphony_logic.fetch_symphony_score", return_value={"id": "sym-test", "type": "root", "children": [{"type": "asset", "ticker": "SPY"}]}):
                with patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result):
                    resp = flask_client.post(
                        "/ai-advisor/asset-swaps/evaluate",
                        json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
                    )

        data = resp.get_json()
        assert data is not None

        has_guidance = any(k in data for k in ("apply_guidance", "guidance", "instructions"))
        assert has_guidance, (
            f"Response must include apply_guidance. Got keys: {sorted(data.keys())}. "
            "AC-X1: every surfaced swap must show the plain-text 'apply manually in Composer' "
            "instruction. No button — advise only."
        )

    def test_evaluate_response_contains_objective_rationale(self, flask_client):
        """The response must include objective_rationale explaining WHY this swap (AC-2.3)."""
        mock_result = _make_swap_run_result(survivors=True)

        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            with patch("symphony_logic.fetch_symphony_score", return_value={"id": "sym-test", "type": "root", "children": [{"type": "asset", "ticker": "SPY"}]}):
                with patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result):
                    resp = flask_client.post(
                        "/ai-advisor/asset-swaps/evaluate",
                        json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
                    )

        data = resp.get_json()
        assert data is not None

        has_rationale = any(k in data for k in ("objective_rationale", "rationale", "objective"))
        assert has_rationale, (
            f"Response must include objective_rationale. Got keys: {sorted(data.keys())}. "
            "AC-2.3: every surfaced swap must show the de-correlation/objective rationale "
            "so the operator understands WHY this swap was proposed."
        )


# ===========================================================================
# Section 5 — AC-2.5: zero survivors response
# ===========================================================================

class TestZeroSurvivorsResponse:
    """AC-2.5: zero survivors → explicit message in response, not silent empty."""

    def test_evaluate_zero_survivors_exposes_no_survivors_message(self, flask_client):
        """When no candidates survive the gate, the response must expose the message.

        AC-2.5: zero survivors is valid — but the operator must see
        'no swap cleared the gate this run', not a silent empty list.
        """
        mock_result = _make_swap_run_result(survivors=False)
        assert mock_result.message, "Fixture must have a non-empty message for zero survivors."

        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            with patch("symphony_logic.fetch_symphony_score", return_value={"id": "sym-test", "type": "root", "children": [{"type": "asset", "ticker": "SPY"}]}):
                with patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result):
                    resp = flask_client.post(
                        "/ai-advisor/asset-swaps/evaluate",
                        json={"symphony_id": "sym-test", "from_ticker": "SPY", "to_ticker": "IALT"},
                    )

        data = resp.get_json()
        assert data is not None

        # Response must include a message field or a survivors list (even if empty).
        has_message = any(k in data for k in ("message", "summary", "no_survivors_message"))
        has_survivors_list = "survivors" in data

        assert has_message or has_survivors_list, (
            f"Zero-survivors response must include 'message' or 'survivors' field. "
            f"Got keys: {sorted(data.keys())}. "
            "AC-2.5: zero survivors must produce an explicit message, not a silent empty response."
        )


# ===========================================================================
# Section 6 — Template: no apply/deploy button (AC-X1)
# ===========================================================================

class TestTemplateAdviseOnly:
    """AC-X1: the asset-swaps template must not contain apply/deploy/trade buttons."""

    def test_template_contains_no_apply_or_deploy_button(self):
        """Static analysis: the template must not render any apply/deploy action button.

        AC-X1: the advisor is advise-only. An apply/deploy button in the template
        would create a UI path toward Composer write operations — the hardest of the
        hard architectural constraints.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor_asset_swaps.html"
        if not template_path.exists():
            pytest.skip("Template not yet created — skipping static analysis.")

        source = template_path.read_text(encoding="utf-8").lower()

        # These button texts/ids would indicate an apply or deploy action surface.
        forbidden_patterns = [
            'apply swap',
            'deploy swap',
            'apply-swap',
            'deploy-swap',
            'btn-apply',
            'btn-deploy',
            'place trade',
            'execute trade',
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"Template ai_advisor_asset_swaps.html contains forbidden pattern {pattern!r}. "
                "AC-X1: the asset swaps tab is advise-only. There must be NO button that "
                "applies, deploys, or executes a swap. The operator applies manually in Composer."
            )

    def test_template_contains_apply_guidance_text_not_button(self):
        """The template must show the plain-text 'apply manually in Composer' guidance.

        AC-X1: the operator applies manually. The guidance text must be present
        (so the operator knows how to act) but it must NOT be a button or a link
        that would trigger a write operation.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor_asset_swaps.html"
        if not template_path.exists():
            pytest.skip("Template not yet created.")

        source = template_path.read_text(encoding="utf-8").lower()

        # The template must reference the apply_guidance field from the engine.
        has_guidance_reference = any(
            t in source for t in (
                "apply_guidance", "apply guidance", "manually", "composer"
            )
        )
        assert has_guidance_reference, (
            "Template ai_advisor_asset_swaps.html must display apply_guidance text. "
            "AC-X1: the 'To apply: open X in Composer and swap A → B manually' instruction "
            "must be visible to the operator. Without it, they have no path to act on a "
            "recommendation."
        )


# ===========================================================================
# Section 7 — AC-X2: lazy import guard in app.py
# ===========================================================================

class TestLazyImportGuard:
    """AC-X2: app.py must import asset_swap_engine lazily (inside the route), not at module scope."""

    def test_app_module_does_not_import_asset_swap_engine_at_top_level(self):
        """Static analysis: the top-level imports in app.py must not include asset_swap_engine.

        A module-scope import of asset_swap_engine would mean every request to the
        Flask app pulls the advisor module into memory — and would couple the
        1-minute execution path (spawned as a subprocess from app.py) to the advisor.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Walk only the module-level statements (not inside function bodies).
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "asset_swap_engine" not in module, (
                    "app.py imports from advisors.asset_swap_engine at module scope. "
                    "AC-X2: asset_swap_engine must be imported LAZILY inside the route "
                    "handler function body, not at the top level. "
                    "A module-scope import couples the 1-minute execution path to the advisor."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "asset_swap_engine" not in alias.name, (
                        "app.py imports advisors.asset_swap_engine at module scope — AC-X2 violation."
                    )

    def test_evaluate_route_imports_asset_swap_engine_inside_function(self):
        """Static analysis: the route handler must import from asset_swap_engine inside its body."""
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        fn_body_source = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                fn_body_source = ast.unparse(node)
                break

        assert fn_body_source is not None, "ai_advisor_asset_swaps_evaluate not found in app.py."

        # The function body must contain a lazy import of asset_swap_engine.
        has_lazy_import = "asset_swap_engine" in fn_body_source
        assert has_lazy_import, (
            "ai_advisor_asset_swaps_evaluate must import from advisors.asset_swap_engine "
            "inside the function body (lazy import). "
            "AC-X2: lazy import is the architecture guard that keeps the advisor off the "
            "live 1-minute execution path."
        )


# ===========================================================================
# Section 8 — Input validation
# ===========================================================================

class TestInputValidation:
    """The evaluate route must validate required fields and return clear errors."""

    def test_missing_symphony_id_returns_error(self, flask_client):
        """Missing symphony_id → error response, not a 500."""
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"from_ticker": "SPY", "to_ticker": "IALT"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data, (
            "Missing symphony_id must return a JSON error. "
            f"Got: {data!r}"
        )

    def test_missing_from_ticker_returns_error(self, flask_client):
        """Missing from_ticker → error response."""
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "sym-test", "to_ticker": "IALT"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data

    def test_missing_to_ticker_returns_error(self, flask_client):
        """Missing to_ticker → error response."""
        with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "sym-test", "from_ticker": "SPY"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data


# ===========================================================================
# Section 9 — Reviewer BLOCK: route-importability static check
# (quant-code-reviewer finding: every name imported by app.py's new asset-swap
#  routes must exist as a public name in advisors.asset_swap_engine)
# ===========================================================================

class TestRouteImportability:
    """Every name app.py's new routes import from asset_swap_engine must exist.

    This prevents the evaluate_single_swap class of silent name-mismatch bugs.
    The canonical public API of asset_swap_engine is:
      propose_operator_swap, suggest_swaps, generate_objective_directed_candidates,
      SwapObjective, SwapProposalResult, SwapRunResult, apply_ticker_swap,
      extract_tickers, NO_SURVIVORS_MESSAGE, SWAP_SURVIVOR_CAVEAT.
    The route must use propose_operator_swap — NOT evaluate_single_swap (which
    does not exist in the new engine and would cause ImportError at request time).
    """

    def test_all_names_imported_by_evaluate_route_exist_in_asset_swap_engine(self):
        """Static analysis: every name the evaluate route imports from asset_swap_engine
        must be a real public attribute of that module.

        This is the reviewer's RED test 2: a name-mismatch check that prevents
        the evaluate_single_swap ImportError class of bug from reaching production.
        """
        _ensure_repo_on_path()

        # Parse app.py and find all names imported from asset_swap_engine inside
        # the ai_advisor_asset_swaps_evaluate function body.
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.ImportFrom):
                        module = inner.module or ""
                        if "asset_swap_engine" in module:
                            for alias in inner.names:
                                imported_names.append(alias.name)

        if not imported_names:
            # Route doesn't import from asset_swap_engine at all — that's also a failure
            # (it must at minimum import propose_operator_swap).
            pytest.fail(
                "ai_advisor_asset_swaps_evaluate does not import anything from "
                "advisors.asset_swap_engine. It must import at minimum "
                "propose_operator_swap and SwapObjective."
            )

        # Import the actual module and check each name exists.
        engine = importlib.import_module("advisors.asset_swap_engine")
        missing = [name for name in imported_names if not hasattr(engine, name)]

        assert not missing, (
            f"ai_advisor_asset_swaps_evaluate imports these names from "
            f"advisors.asset_swap_engine that DO NOT EXIST in that module: {missing}. "
            f"Imported names found in route: {imported_names}. "
            "A missing name causes an ImportError at request time. "
            "The route must use propose_operator_swap (not evaluate_single_swap — "
            "that function was in the old stub and does not exist in the new engine)."
        )

    def test_evaluate_route_imports_propose_operator_swap(self):
        """The evaluate route must import propose_operator_swap — the M3 typed entry point.

        This is the reviewer's RED test 1 (option b): the route must call
        propose_operator_swap, not an alias or the old evaluate_single_swap.
        propose_operator_swap accepts a typed SwapObjective and returns SwapRunResult
        — the contract that gate_batch, objective_rationale, and AC-2.3 depend on.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        propose_imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.ImportFrom):
                        module = inner.module or ""
                        if "asset_swap_engine" in module:
                            names = [alias.name for alias in inner.names]
                            if "propose_operator_swap" in names:
                                propose_imported = True

        assert propose_imported, (
            "ai_advisor_asset_swaps_evaluate must import propose_operator_swap from "
            "advisors.asset_swap_engine. "
            "propose_operator_swap is the M3 typed entry point that accepts SwapObjective "
            "and returns SwapRunResult with gate_batch, objective_rationale, and message. "
            "Using evaluate_single_swap (old API, does not exist) or a plain string "
            "objective bypasses the typed contract."
        )

    def test_evaluate_route_imports_swap_objective(self):
        """The evaluate route must import SwapObjective to construct a typed objective.

        Gate-1 Resolution #2: every swap must be objective-directed with a typed,
        measurable objective. A plain string objective is not verifiable as
        objective-directed — it could be 'operator-initiated' (a description, not
        a SwapObjective with objective_type, target_pair, and measured_value).
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        swap_objective_imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.ImportFrom):
                        module = inner.module or ""
                        if "asset_swap_engine" in module:
                            names = [alias.name for alias in inner.names]
                            if "SwapObjective" in names:
                                swap_objective_imported = True

        assert swap_objective_imported, (
            "ai_advisor_asset_swaps_evaluate must import SwapObjective from "
            "advisors.asset_swap_engine. "
            "The route must construct a typed SwapObjective (objective_type, target_pair, "
            "measured_value) and pass it to propose_operator_swap. "
            "Without SwapObjective, the route cannot satisfy the objective-direction "
            "contract (Gate-1 Resolution #2)."
        )
