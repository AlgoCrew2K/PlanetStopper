"""Route community-admission wiring tests (Shape A — objective-matched atlas admission).

Module under test: app.ai_advisor_strategy_builder_run
Route: POST /ai-advisor/strategy-builder/run

CONTRACT UPGRADE (C5, 2026-06-20 — stale-by-intent re-point, NOT a weakening):
  The route's community-admission contract was INTENTIONALLY upgraded from the old
  unranked `strategy_builder_engine.community_candidate_infos` adapter (template_id=
  "community", objective-ignorant) to the OBJECTIVE-MATCHED `build_plan_generator.
  load_atlas_candidates(objective)` (provenance="atlas-suggested", AC-12/AC-13). The
  old adapter was orphaned and DELETED this cycle (EDGE-1). These tests are re-pointed
  to assert the Shape-A contract:

    route → load_atlas_candidates(objective)  [exactly once; bill-protected inside]
         → propose_strategies(community_candidates=<that output>)

  The route NO LONGER calls load_community_strategies directly NOR the deleted adapter.
  The force_refresh=False bill-protection now lives INSIDE load_atlas_candidates and is
  positively asserted in tests/advisors/test_build_plan_atlas_admission.py
  ::test_ac12_uses_force_refresh_false_when_loading_from_atlas — so the route-level
  AC-3/security guard here verifies the route never FORCES a refresh (it cannot — it
  passes no force_refresh) and calls the wrapper exactly once.

These remain ROUTE-LAYER tests only. The objective-matched admission internals are
covered in tests/advisors/test_build_plan_atlas_admission.py + the generator suite.
The end-to-end provenance/parity assertions live in tests/app/test_strategy_builder_c5_route.py.

Mocking strategy (Shape A seams):
  - advisors.build_plan_generator.load_atlas_candidates  — the objective-matched admission
    (route lazy-imports it from build_plan_generator; patch the source module)
  - advisors.strategy_builder_engine.propose_strategies  — capture call kwargs
  - database.load_state  — autouse-stubbed by tests/ui/conftest.py
  - CSRF  — autouse-disabled by tests/conftest.py

Rules:
  - No live Atlas, Composer, or DB calls.
  - No hardcoded producer-computed metric values; assert shape/membership/kwarg forwarding.
  - Every test has at least one assertion that can fail on a wrong implementation.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — fake objects returned by mocked collaborators
# ---------------------------------------------------------------------------


def _make_fake_proposal_run(*, error: str | None = None) -> MagicMock:
    """Minimal ProposalRun-shaped MagicMock that the route handler can serialise.

    The route's response-building section accesses run.error, run.gated_batch,
    run.candidates, run.screened_survivors, run.gated_batch.results /.n_candidates /.fdr_q.
    """
    run = MagicMock()
    run.error = error

    gate = MagicMock()
    gate.n_candidates = 0
    gate.fdr_q = 0.05
    gate.results = []
    gate.survivors = []
    run.gated_batch = gate

    run.candidates = []
    run.screened_survivors = []
    return run


def _make_fake_atlas_candidates(n: int) -> list:
    """N opaque MagicMock objects standing in for objective-matched atlas CandidateInfo
    instances (the load_atlas_candidates output). The route forwards these unchanged to
    propose_strategies; we only care about count and identity."""
    return [MagicMock(name=f"AtlasCandidate-{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# Shared fixture — Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def sb_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post_run(client, body: dict | None = None):
    """POST /ai-advisor/strategy-builder/run with a JSON body."""
    payload = body or {"objective": "diversify", "universe": ["SPY", "QQQ"]}
    return client.post(
        "/ai-advisor/strategy-builder/run",
        data=json.dumps(payload),
        content_type="application/json",
    )


# ===========================================================================
# SECTION 1 — AC-1/AC-13: route forwards load_atlas_candidates output as
# community_candidates kwarg to propose_strategies.
# ===========================================================================


class TestAC1CommunityKwargForwarding:
    """AC-1 (Shape A): the route must call propose_strategies(community_candidates=
    <load_atlas_candidates output>)."""

    def test_community_candidates_kwarg_forwarded_to_propose_strategies(self, sb_client):
        """AC-1: community_candidates kwarg passed to propose_strategies equals the
        load_atlas_candidates output."""
        n = 2
        atlas_output = _make_fake_atlas_candidates(n)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200
        assert propose_mock.called, "propose_strategies must be called; it was not called at all"

        _, kwargs = propose_mock.call_args
        assert "community_candidates" in kwargs, (
            "propose_strategies must be called with community_candidates= kwarg; "
            f"actual kwargs: {list(kwargs.keys())}"
        )
        assert kwargs["community_candidates"] == atlas_output, (
            "community_candidates forwarded to propose_strategies must equal the "
            "load_atlas_candidates output; got a different object or value"
        )

    def test_community_candidates_kwarg_count_matches_admission_output(self, sb_client):
        """AC-1: len(forwarded community_candidates) == len(load_atlas_candidates output)."""
        n = 3
        atlas_output = _make_fake_atlas_candidates(n)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        _, kwargs = propose_mock.call_args
        forwarded = kwargs.get("community_candidates")
        assert forwarded is not None, "community_candidates must be forwarded; got None/missing"
        assert len(forwarded) == n, (
            f"expected {n} community_candidates forwarded; got {len(forwarded)}"
        )

    def test_community_candidates_forwarded_value_is_list_not_none(self, sb_client):
        """AC-1: the forwarded community_candidates must be a list, not None."""
        atlas_output = _make_fake_atlas_candidates(1)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        _, kwargs = propose_mock.call_args
        forwarded = kwargs.get("community_candidates")
        assert isinstance(forwarded, list), (
            f"community_candidates must be a list; got {type(forwarded).__name__}"
        )

    def test_load_atlas_candidates_called_with_request_objective(self, sb_client):
        """AC-1/AC-12 (Shape A): the route calls the OBJECTIVE-MATCHED admission with the
        request objective (objective-shaped admission, not a global unranked pull)."""
        atlas_output = _make_fake_atlas_candidates(2)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client, {"objective": "cut_drawdown", "universe": []})

        assert atlas_mock.called, "load_atlas_candidates must be called; it was not called"
        call = atlas_mock.call_args
        objective_arg = call.args[0] if call.args else call.kwargs.get("objective")
        objective_value = getattr(objective_arg, "value", objective_arg)
        assert objective_value == "cut_drawdown", (
            "AC-12: load_atlas_candidates must receive the request objective "
            f"('cut_drawdown'); got {objective_value!r}"
        )


# ===========================================================================
# SECTION 2 — AC-2: empty / unavailable admission → template-only, route completes
# ===========================================================================


class TestAC2TemplateDegradation:
    """AC-2: route completes a template-only run when atlas admission yields []."""

    def test_empty_admission_route_completes(self, sb_client):
        """AC-2: load_atlas_candidates returns [] (no objective-matched community) → 200."""
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(return_value=[])
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"route must return 200 when atlas admission is empty; got {resp.status_code}"
        )

    def test_empty_admission_response_has_required_json_keys(self, sb_client):
        """AC-2: response must carry survivors/rejected/n_candidates/fdr_adjusted_threshold."""
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(return_value=[])
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body = json.loads(resp.data)
        required = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold"}
        missing = required - body.keys()
        assert not missing, (
            f"response must have keys {required}; missing: {missing}. Actual: {set(body.keys())}"
        )

    def test_empty_admission_propose_still_called(self, sb_client):
        """AC-2: propose_strategies must be called even when atlas admission returns [].

        Template-only (built-new) path must complete — not short-circuit on empty atlas."""
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(return_value=[])
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert propose_mock.called, (
            "propose_strategies must still be called when atlas admission is []; "
            "the template-only (built-new) run must not be skipped"
        )


# ===========================================================================
# SECTION 3 — AC-3: bill-protection — route calls the wrapper once, never forces refresh.
# (The force_refresh=False guarantee is positively asserted INSIDE load_atlas_candidates
#  in tests/advisors/test_build_plan_atlas_admission.py::test_ac12_uses_force_refresh_false.)
# ===========================================================================


class TestAC3BillProtection:
    """AC-3: the route uses the bill-protected wrapper; it never forces an Atlas refresh."""

    def test_load_atlas_candidates_called_exactly_once(self, sb_client):
        """AC-3: load_atlas_candidates is called exactly once per request — calling it
        multiple times would multiply Atlas read cost (the wrapper caches weekly)."""
        atlas_output = _make_fake_atlas_candidates(1)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert atlas_mock.call_count == 1, (
            f"load_atlas_candidates must be called exactly once per request; "
            f"called {atlas_mock.call_count} times"
        )

    def test_route_does_not_force_atlas_refresh(self, sb_client):
        """AC-3 / bill-protection: the route must never pass force_refresh=True to the
        admission path. Shape A: the route calls load_atlas_candidates(objective) with NO
        force_refresh kwarg (the wrapper enforces force_refresh=False internally —
        positively asserted in test_build_plan_atlas_admission.py). A regression that
        added force_refresh=True to the route call would bypass the weekly cache."""
        atlas_output = _make_fake_atlas_candidates(1)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert atlas_mock.called, "load_atlas_candidates must be called"
        _, kwargs = atlas_mock.call_args
        assert kwargs.get("force_refresh") is not True, (
            "AC-3 bill-protection: the route must NOT pass force_refresh=True to "
            f"load_atlas_candidates; got force_refresh={kwargs.get('force_refresh')!r}"
        )


# ===========================================================================
# SECTION 4 — AC-4: never-raising / D-1
# ===========================================================================


class TestAC4NeverRaisingD1:
    """AC-4: an admission failure must not break the route; a propose failure → classname."""

    def test_atlas_admission_raises_route_does_not_500(self, sb_client):
        """AC-4: load_atlas_candidates raising must not 500 the route — it degrades to a
        template-only (built-new) run. (load_atlas_candidates is itself never-raising, but
        the route's best-effort try/except guards the call site regardless.)"""
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(side_effect=RuntimeError("atlas is down"))
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"route must return 200 when atlas admission raises; got {resp.status_code}. "
            "Atlas admission must be best-effort."
        )

    def test_atlas_admission_raises_response_does_not_leak_exception_str(self, sb_client):
        """AC-4 / D-1: an admission exception message (could carry a MONGO_URI/credential)
        must not appear in the response body."""
        secret_message = "mongodb+srv://admin:S3cr3t@cluster.mongo.net"
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(side_effect=ConnectionError(secret_message))
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert secret_message not in body_str, (
            "D-1 violation: admission exception message appears in the response body. "
            "It could carry credentials/paths. Log only the class name."
        )

    def test_propose_still_called_when_atlas_admission_raises(self, sb_client):
        """AC-4: when atlas admission raises, propose_strategies must STILL be called —
        the route degrades to template-only (community_candidates=[]) and proceeds."""
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(side_effect=RuntimeError("atlas timeout"))
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert propose_mock.called, (
            "propose_strategies must still be called when atlas admission raises; "
            "admission failure must be best-effort / template-only fallback"
        )

    def test_propose_strategies_raises_returns_safe_error_no_500(self, sb_client):
        """AC-4 / D-1: propose_strategies raising → a safe error token, never a 500. The
        route's outer except surfaces type(exc).__name__ (app.py:3829)."""
        atlas_output = _make_fake_atlas_candidates(1)

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(side_effect=ConnectionError("boom"))

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"propose_strategies raising must not 500; got {resp.status_code}"
        )
        body = json.loads(resp.data)
        assert "error" in body, (
            f"response must have an 'error' key when propose_strategies raises; "
            f"got keys: {list(body.keys())}"
        )
        assert body["error"] == "ConnectionError", (
            f"the route's outer except must surface the exception class name; got {body['error']!r}"
        )

    def test_propose_strategies_raises_body_has_no_exception_str(self, sb_client):
        """AC-4 / D-1: a propose exception message must not leak into the response body."""
        secret_detail = "internal path /opt/app/config.yaml line 42"
        atlas_output = _make_fake_atlas_candidates(1)

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(side_effect=ValueError(secret_detail))

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert secret_detail not in body_str, (
            "D-1 violation: propose_strategies exception message appears in the response. "
            "Only type(exc).__name__ is permitted."
        )

    def test_propose_strategies_raises_error_value_is_bare_classname(self, sb_client):
        """AC-4 / D-1: the error value must be a bare class name — no spaces/colons/slashes."""
        atlas_mock = MagicMock(return_value=[])
        propose_mock = MagicMock(side_effect=TypeError("cannot do this"))

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body = json.loads(resp.data)
        error_val = body.get("error", "")
        assert isinstance(error_val, str), "error value must be a string"
        assert " " not in error_val, f"error value must be a bare class name (no spaces); got {error_val!r}"
        assert ":" not in error_val, f"error value must be a bare class name (no colon); got {error_val!r}"
        assert "/" not in error_val, f"error value must be a bare class name (no slash); got {error_val!r}"
        assert error_val == "TypeError", f"error value must be 'TypeError'; got {error_val!r}"


# ===========================================================================
# SECTION 5 — AC-5: off-execution-path boundary + no allowlist/LIVE_EXECUTION
# ===========================================================================


class TestAC5BoundaryPreserved:
    """AC-5: constraint guards — must hold both before and after the rewire."""

    def test_load_community_strategies_not_module_level_attr(self):
        """AC-5: load_community_strategies must NOT be a module-level attr of app —
        community admission stays a lazy import inside the route (CC-2 boundary)."""
        import app as app_module

        assert not hasattr(app_module, "load_community_strategies"), (
            "load_community_strategies must not be imported at app module level; "
            "it must stay lazy (it lives inside load_atlas_candidates now)"
        )

    def test_load_atlas_candidates_not_module_level_attr(self):
        """AC-5 (Shape A): the objective-matched admission must also be a lazy import
        inside the route — not a module-level app attr (CC-2 boundary)."""
        import app as app_module

        assert not hasattr(app_module, "load_atlas_candidates"), (
            "load_atlas_candidates must not be imported at app module level; "
            "it must be a lazy import inside the route handler (CC-2 boundary)"
        )

    def test_old_community_adapter_not_module_level_attr(self):
        """AC-5 / EDGE-1: the deleted community_candidate_infos adapter must not be a
        module-level attr of app (it is gone — never re-introduce it)."""
        import app as app_module

        assert not hasattr(app_module, "community_candidate_infos"), (
            "community_candidate_infos was deleted (EDGE-1) — it must not be referenced "
            "at app module level"
        )

    def test_SETTINGS_WRITE_ALLOWLIST_does_not_contain_community_key(self):
        """AC-5: _SETTINGS_WRITE_ALLOWLIST must not include any community-related key —
        the strategy-builder route is advisory-only."""
        import app as app_module

        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        community_keys = {k for k in allowlist if "community" in k.lower()}
        assert not community_keys, (
            f"_SETTINGS_WRITE_ALLOWLIST must not contain community-related keys; "
            f"found: {community_keys}"
        )

    def test_LIVE_EXECUTION_not_referenced_in_handler_source(self):
        """AC-5: the handler must not reference LIVE_EXECUTION in executable code (a
        docstring mention of intent is acceptable)."""
        _assert_no_live_execution_in_handler()


# ===========================================================================
# SECTION 6 — AC-6: no regression — empty admission → same response shape
# ===========================================================================


class TestAC6NoRegression:
    """AC-6: with atlas admission producing [], the template-only response shape is unchanged."""

    def test_empty_admission_and_raise_both_produce_same_response_keys(self, sb_client):
        """AC-6: both empty-admission and admission-raises paths return identical key sets."""
        fake_run = _make_fake_proposal_run()

        # Path 1: admission returns []
        atlas_empty = MagicMock(return_value=[])
        propose_1 = MagicMock(return_value=fake_run)
        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_empty),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_1),
        ):
            resp1 = _post_run(sb_client)

        # Path 2: admission raises → degrade
        atlas_raise = MagicMock(side_effect=RuntimeError("atlas down"))
        propose_2 = MagicMock(return_value=fake_run)
        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_raise),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_2),
        ):
            resp2 = _post_run(sb_client)

        keys1 = set(json.loads(resp1.data).keys())
        keys2 = set(json.loads(resp2.data).keys())
        assert keys1 == keys2, (
            "Response key sets must be identical for empty-admission and admission-raises "
            f"paths; empty: {keys1}, raises: {keys2}"
        )

    def test_happy_path_response_shape_keys_present(self, sb_client):
        """AC-6: happy-path response (with atlas candidates) has the required keys."""
        atlas_output = _make_fake_atlas_candidates(2)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200
        body = json.loads(resp.data)
        required_keys = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold"}
        missing = required_keys - body.keys()
        assert not missing, f"Happy-path response must have keys {required_keys}; missing: {missing}"
        assert isinstance(body["survivors"], list), "survivors must be a list"
        assert isinstance(body["rejected"], list), "rejected must be a list"
        assert isinstance(body["n_candidates"], int), "n_candidates must be an int"
        assert body["fdr_adjusted_threshold"] is None or isinstance(
            body["fdr_adjusted_threshold"], (int, float)
        ), f"fdr_adjusted_threshold must be None or numeric; got {type(body['fdr_adjusted_threshold']).__name__}"


# ===========================================================================
# SECTION 7 — Security boundary
# ===========================================================================


def _assert_no_live_execution_in_handler() -> None:
    """Shared guard: ai_advisor_strategy_builder_run must not reference LIVE_EXECUTION in
    executable code (a docstring mention of intent is acceptable)."""
    import ast
    import textwrap

    import app as app_module

    raw_src = inspect.getsource(app_module.ai_advisor_strategy_builder_run)
    src = textwrap.dedent(raw_src)

    try:
        tree = ast.parse(src)
    except SyntaxError:
        non_doc_lines = []
        in_docstring = False
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if not in_docstring and not stripped.startswith("#"):
                non_doc_lines.append(line)
        code_only = "\n".join(non_doc_lines)
        assert "LIVE_EXECUTION" not in code_only, (
            "LIVE_EXECUTION must not appear in non-docstring, non-comment handler code"
        )
        return

    # Strip docstrings before walking.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:]

    class _Checker(ast.NodeVisitor):
        def __init__(self):
            self.found = False

        def visit_Name(self, node):
            if node.id == "LIVE_EXECUTION":
                self.found = True
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if node.attr == "LIVE_EXECUTION":
                self.found = True
            self.generic_visit(node)

    checker = _Checker()
    checker.visit(tree)
    assert not checker.found, (
        "LIVE_EXECUTION must not appear as a Name or Attribute reference in "
        "ai_advisor_strategy_builder_run executable code; the route is advisory-only"
    )


class TestSecurityBoundary:
    """Security constraints — must hold both before and after the rewire."""

    def test_no_live_execution_interaction_in_handler(self):
        """Security: handler must not reference LIVE_EXECUTION in executable code."""
        _assert_no_live_execution_in_handler()

    def test_route_does_not_force_atlas_refresh(self, sb_client):
        """Security: a caller cannot force repeated Atlas reads by hammering the route.
        Duplicated from AC-3 because Atlas cost amplification is an abuse vector. The
        route must never pass force_refresh=True to the objective-matched admission."""
        atlas_output = _make_fake_atlas_candidates(1)
        fake_run = _make_fake_proposal_run()

        atlas_mock = MagicMock(return_value=atlas_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        if atlas_mock.called:
            _, kwargs = atlas_mock.call_args
            assert kwargs.get("force_refresh") is not True, (
                "Security: force_refresh=True would let callers exhaust the weekly Atlas "
                "quota on demand"
            )

    def test_atlas_admission_exception_message_not_in_response(self, sb_client):
        """Security / D-1: a credential string in an admission exception must not reach
        the client."""
        credential_string = "apikey=ABCDEF1234567890"
        fake_run = _make_fake_proposal_run()
        atlas_mock = MagicMock(side_effect=ValueError(f"Failed with {credential_string}"))
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.build_plan_generator.load_atlas_candidates", atlas_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert credential_string not in body_str, (
            "Security / D-1: credential string from an admission exception must not reach "
            "the response body"
        )

    def test_route_not_in_settings_write_allowlist(self):
        """Security: the advisory route must not be gated via _SETTINGS_WRITE_ALLOWLIST."""
        import app as app_module

        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        assert "strategy-builder" not in allowlist and "strategy_builder" not in allowlist, (
            "the strategy-builder route is advisory-only and must not be in "
            "_SETTINGS_WRITE_ALLOWLIST"
        )
