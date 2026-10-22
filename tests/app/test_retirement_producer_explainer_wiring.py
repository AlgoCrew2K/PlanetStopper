"""RED tests -- AC-2: explainer runs at producer time, persisted.

feature-plans/retirement-approval-lifecycle.md AC-2: the 03:45 producer
(app._retirement_recommender_tick_worker) calls advisors.retirement_explainer.
explain_recommendation per recommendation AFTER build_recommendations() and
BEFORE persist_recommendations(), stamping `explanation` (str | None) into
each rec's raw_response dict. The LLM never runs on the render path or the
approve/checklist path. advisors/retirement_recommender.py stays LLM-free --
originally pinned via a golden sha256 byte hash; Cycle 2e (retirement-
visibility AC-9) REPLACED that pin with an AST check of the invariant it
protected (the module imports nothing LLM-related), because the byte pin
forbade the cycle's legitimate edits to that module (source seam + run
summary) without protecting anything the AST check does not.

Cycle 2e: the worker now calls build_recommendations_with_summary
(source="composer") instead of build_recommendations; every patch below
targets the new entry point (via the ``with_summary`` stub adapter), keeping
each test's original intent.

Lazy-import mocking note: _retirement_recommender_tick_worker does
`from advisors.retirement_recommender import (build_recommendations,
persist_recommendations)` INSIDE the function body (CC-2) -- patching the
MODULE-level attributes (advisors.retirement_recommender.build_recommendations
etc.) before calling the worker is picked up correctly, since a `from X
import Y` executes at call time and reads Y off X's current namespace. Same
pattern applies to the explainer import this cycle adds.

Expected state: RED until app.py's _retirement_recommender_tick_worker is
extended to call advisors.retirement_explainer.explain_recommendation.
"""

from __future__ import annotations

import ast
import logging
import pathlib
from unittest.mock import patch

import pytest

import app as app_module
from tests.app._retirement_run_summary_stub import with_summary

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# AC-9 (Cycle 2e): the invariant the retired golden-SHA pin protected was
# "advisors/retirement_recommender.py stays LLM-free" (Cycle 2b AC-2). A byte
# hash cannot distinguish a harmless source-seam edit from an LLM import, so
# it is replaced by an AST scan of exactly what matters: the module imports
# nothing LLM-related. Forbidden = the Anthropic SDK, the explainer module
# this module's output feeds, the model-selection config, the chat advisor,
# and ai_advisor (the sanctioned LLM client seam).
_LLM_FORBIDDEN_MODULES = (
    "anthropic",
    "advisors.retirement_explainer",
    "model_config",
    "advisor_chat",
    "advisors.advisor_chat",
    "ai_advisor",
)


def _llm_related_imports(source: str) -> list[str]:
    """Return every LLM-related import (or dynamic-import target) found
    ANYWHERE in `source` -- module level, inside functions, and lazy imports.

    Covers: ``import x``, ``import x.y``, ``from x import y`` (both the
    module and the dotted ``x.y`` it names), ``from advisors import
    retirement_explainer`` (module + imported name combined), relative
    imports, and dynamic imports via a string constant exactly equal to a
    forbidden module name (``importlib.import_module("anthropic")``,
    ``__import__("anthropic")``). Exact-equality (not substring) on string
    constants so prose in a docstring never false-positives.
    """
    tree = ast.parse(source)
    found: list[str] = []

    def _is_forbidden(dotted: str) -> bool:
        return any(dotted == bad or dotted.startswith(bad + ".") for bad in _LLM_FORBIDDEN_MODULES)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module and _is_forbidden(module):
                found.append(f"from {module} import ...")
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                if _is_forbidden(combined):
                    found.append(f"from {module or '.'} import {alias.name}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _LLM_FORBIDDEN_MODULES
        ):
            found.append(f"string constant {node.value!r} (dynamic import?)")
    return found


def _sample_recs() -> list[dict]:
    return [
        {"candidate_id": "sym-a", "sibling_id": "sym-b", "correlation": 0.8},
        {"candidate_id": "sym-c", "sibling_id": "sym-d", "correlation": 0.75},
    ]


# ===========================================================================
# AC-2 core (Cycle 2b) / AC-9 (Cycle 2e): retirement_recommender.py stays
# LLM-free -- enforced by an AST import scan, not a byte pin.
# ===========================================================================


def test_retirement_recommender_module_imports_nothing_llm_related():
    """AC-9: 'advisors/retirement_recommender.py stays LLM-free (the
    explainer call lives in the producer orchestration, not in
    build_recommendations)' -- enforced by scanning the module's real AST."""
    path = REPO_ROOT / "advisors" / "retirement_recommender.py"
    assert path.exists(), (
        "advisors/retirement_recommender.py must exist (Cycle-2a, already shipped)."
    )
    offending = _llm_related_imports(path.read_text(encoding="utf-8"))
    assert offending == [], (
        "advisors/retirement_recommender.py imports something LLM-related: "
        f"{offending}. The explainer wiring belongs in app.py's producer "
        "orchestration only, never inside the recommender math module."
    )


class TestLlmImportScanIsNonVacuous:
    """AC-9 non-vacuity (PM requirement): the replacement for the golden-SHA
    pin must demonstrably FAIL when an LLM import is added. Proven two ways:
    (1) here, by running the scan over synthetic module sources containing
    each forbidden import shape; (2) by the cycle's mutation check against a
    scratch copy of the real module (inject ``import anthropic`` /
    ``from advisors import retirement_explainer``, confirm the real test
    above fails, revert) -- recorded in the RED handoff report."""

    _CLEAN = "import math\nfrom advisors import correlation_diagnostic\n\ndef f():\n    return 1\n"

    def test_clean_source_has_no_findings(self):
        assert _llm_related_imports(self._CLEAN) == []

    def test_plain_import_anthropic_is_detected(self):
        assert _llm_related_imports("import anthropic\n" + self._CLEAN) != []

    def test_from_anthropic_import_is_detected(self):
        assert _llm_related_imports("from anthropic import Anthropic\n") != []

    def test_from_advisors_import_retirement_explainer_is_detected(self):
        assert _llm_related_imports("from advisors import retirement_explainer\n") != []

    def test_from_retirement_explainer_import_name_is_detected(self):
        assert (
            _llm_related_imports(
                "from advisors.retirement_explainer import explain_recommendation\n"
            )
            != []
        )

    def test_import_model_config_and_advisor_chat_are_detected(self):
        assert _llm_related_imports("import model_config\n") != []
        assert _llm_related_imports("from advisors import advisor_chat\n") != []
        assert _llm_related_imports("from model_config import get_advisor_suggestion_model\n") != []

    def test_lazy_import_inside_a_function_body_is_detected(self):
        src = "def f():\n    import anthropic\n    return anthropic\n"
        assert _llm_related_imports(src) != []

    def test_dynamic_import_via_string_constant_is_detected(self):
        src = "import importlib\n\ndef f():\n    return importlib.import_module('anthropic')\n"
        assert _llm_related_imports(src) != []

    def test_prose_mentioning_anthropic_in_a_docstring_is_not_a_finding(self):
        src = '"""This module never touches the anthropic SDK."""\nimport math\n'
        assert _llm_related_imports(src) == []


def test_retirement_recommender_module_never_imports_ai_advisor():
    """Corroborating source-scan: the math module must never import the LLM
    seam module at all (substring-level backstop to the AST import scan above)."""
    path = REPO_ROOT / "advisors" / "retirement_recommender.py"
    source = path.read_text(encoding="utf-8")
    assert "ai_advisor" not in source, (
        "advisors/retirement_recommender.py must never import ai_advisor -- "
        "it is a deterministic math module, LLM-free by AC-2."
    )


# ===========================================================================
# AC-2 wiring: explainer called per-rec, ordering, stamping
# ===========================================================================


def test_worker_calls_explainer_once_per_recommendation():
    recs = _sample_recs()
    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(lambda **kw: recs),
        ),
        patch("advisors.retirement_recommender.persist_recommendations", return_value=len(recs)),
        patch(
            "advisors.retirement_explainer.explain_recommendation", return_value="explanation text"
        ) as mock_explain,
    ):
        app_module._retirement_recommender_tick_worker()

    assert mock_explain.call_count == len(recs), (
        f"Expected explain_recommendation to be called once per recommendation "
        f"({len(recs)}), got {mock_explain.call_count} calls."
    )


def test_worker_stamps_explanation_into_each_recs_raw_response_before_persist():
    recs = _sample_recs()
    captured_persist_arg: list[dict] = []

    def _fake_persist(recs_arg, **kwargs):
        captured_persist_arg.extend(recs_arg)
        return len(recs_arg)

    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(lambda **kw: recs),
        ),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch(
            "advisors.retirement_explainer.explain_recommendation",
            return_value="stamped explanation",
        ),
    ):
        app_module._retirement_recommender_tick_worker()

    assert len(captured_persist_arg) == len(recs), (
        "persist_recommendations must be called with all recommendations, stamped."
    )
    for rec in captured_persist_arg:
        assert rec.get("explanation") == "stamped explanation", (
            f"Expected every rec passed to persist_recommendations to carry the "
            f"explainer's output under the 'explanation' key, got {rec!r}."
        )


def test_explainer_runs_after_build_and_before_persist_ordering():
    """Explicit call-order assertion (not just call-count) -- explain must
    happen strictly between build and persist, never before build, never
    after persist."""
    events: list[str] = []

    def _fake_build(**kwargs):
        events.append("build")
        return _sample_recs()

    def _fake_explain(rec):
        events.append(f"explain:{rec['candidate_id']}")
        return "x"

    def _fake_persist(recs_arg, **kwargs):
        events.append("persist")
        return len(recs_arg)

    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(_fake_build),
        ),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch("advisors.retirement_explainer.explain_recommendation", side_effect=_fake_explain),
    ):
        app_module._retirement_recommender_tick_worker()

    assert events[0] == "build", f"build_recommendations must run first, got order {events}."
    assert events[-1] == "persist", f"persist_recommendations must run last, got order {events}."
    explain_events = [e for e in events if e.startswith("explain:")]
    assert len(explain_events) == 2, f"Expected 2 explain events, got {events}."
    # Every explain event must sit strictly between the build and persist markers.
    build_idx = events.index("build")
    persist_idx = events.index("persist")
    for e in explain_events:
        idx = events.index(e)
        assert build_idx < idx < persist_idx, (
            f"explain event {e!r} did not occur strictly between build and persist: {events}"
        )


def test_explainer_failure_none_does_not_block_persistence():
    """AC-9: the explainer never gates persistence -- a None explanation
    (e.g. the LLM is down) still lets persist_recommendations run with every
    recommendation, explanation stamped as None."""
    recs = _sample_recs()
    captured: list[dict] = []

    def _fake_persist(recs_arg, **kwargs):
        captured.extend(recs_arg)
        return len(recs_arg)

    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(lambda **kw: recs),
        ),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch("advisors.retirement_explainer.explain_recommendation", return_value=None),
    ):
        app_module._retirement_recommender_tick_worker()

    assert len(captured) == len(recs), "A None explanation must never block persistence."
    for rec in captured:
        assert rec.get("explanation") is None


def test_empty_recommendations_never_calls_explainer_or_persist_with_anything(caplog):
    """AC-9: no recommendations tonight -> the explainer is never invoked at
    all (nothing to explain), persist_recommendations is still called
    (honoring the existing Cycle-2a contract) with an empty list."""
    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(lambda **kw: []),
        ),
        patch(
            "advisors.retirement_recommender.persist_recommendations", return_value=0
        ) as mock_persist,
        patch("advisors.retirement_explainer.explain_recommendation") as mock_explain,
    ):
        app_module._retirement_recommender_tick_worker()

    mock_explain.assert_not_called()
    mock_persist.assert_called_once()
    call_args = mock_persist.call_args[0]
    assert call_args[0] == [], (
        f"Expected persist_recommendations([]) on an empty night, got {call_args}."
    )


def test_explainer_unexpected_exception_is_logged_and_does_not_crash_the_worker(caplog):
    """Defense-in-depth: explain_recommendation is D-1/never-raises by its
    own contract, but if it somehow did raise (a contract violation), the
    worker must catch it, log type(exc).__name__ only, and continue --
    matching this file's existing outer try/except D-1 pattern (mirrors
    _retirement_recommender_tick_worker's existing build/persist guard)."""
    recs = _sample_recs()

    with (
        patch(
            "advisors.retirement_recommender.build_recommendations_with_summary",
            side_effect=with_summary(lambda **kw: recs),
        ),
        patch("advisors.retirement_recommender.persist_recommendations", return_value=len(recs)),
        patch(
            "advisors.retirement_explainer.explain_recommendation",
            side_effect=RuntimeError("secret leak should never surface: token=abc123"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        try:
            app_module._retirement_recommender_tick_worker()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"_retirement_recommender_tick_worker must never propagate an "
                f"explainer exception: {type(exc).__name__}: {exc}"
            )

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "token=abc123" not in joined, (
        "D-1 security contract: the raw exception message must never reach logs."
    )
