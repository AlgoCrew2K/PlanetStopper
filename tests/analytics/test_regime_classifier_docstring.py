"""The ``regime_classifier`` module docstring states its real callers (hygiene-waves-3-4, AC-9 S-9.1).

At the reference the docstring says the module is "surfaced only as an operator/analytics
diagnostic" and "NEVER called on the 1-minute execution path". Two code callers exist: the
autotuner replay (``autotuner.py``) and the nightly regime label job (``regime_label_job.py``).
The engine and ``math_engine.py`` never import it, and ``fit_regime_classifier`` has no code caller.

The text checks below fail until the docstring says so. The caller claims are themselves verified
against the code (an AST scan of the production modules), so a docstring that names a caller that
does not call, or omits one that does, cannot pass. Nothing outside the docstring is checked here;
that the rest of the module is unchanged is pinned in ``tests/test_hygiene_protected_surfaces.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import regime_classifier

REPO = Path(regime_classifier.__file__).resolve().parent
_EXCLUDED_TOP_LEVEL = {"tests", ".claude", "scripts", "docs", "external_data", "doc-archive"}


def _doc() -> str:
    return " ".join((regime_classifier.__doc__ or "").split())


def _occurrences(text: str, needle: str) -> list[int]:
    out, at = [], text.find(needle)
    while at != -1:
        out.append(at)
        at = text.find(needle, at + 1)
    return out


def _window_around(text: str, needle: str, radius: int, start: int | None = None) -> str:
    at = text.find(needle) if start is None else start
    assert at != -1, f"{needle} not in the docstring"
    return text[max(0, at - radius) : at + len(needle) + radius]


def _production_modules() -> list[Path]:
    found = []
    for path in REPO.rglob("*.py"):
        parts = path.relative_to(REPO).parts
        if parts[0] in _EXCLUDED_TOP_LEVEL or ".venv" in parts or "worktrees" in parts:
            continue
        found.append(path)
    return found


def _calls_attribute(path: Path, attribute: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "regime_classifier"
        for node in ast.walk(tree)
    )


def _imports_regime_classifier(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "regime_classifier" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "regime_classifier":
            return True
    return False


class TestDocstringStatesTheRealCallers:
    def test_docstring_names_the_autotuner_replay_and_the_label_job_as_callers(self):
        doc = _doc()
        assert "autotuner.py" in doc
        assert "regime_label_job.py" in doc

    def test_docstring_no_longer_says_it_is_surfaced_only_as_a_diagnostic(self):
        doc = _doc().lower()
        assert "surfaced only" not in doc
        assert "only as an operator/analytics diagnostic" not in doc

    def test_docstring_keeps_that_engine_and_math_engine_never_import_it(self):
        window = _window_around(_doc(), "alpha_bot_execution.py", 160).lower()
        assert "math_engine.py" in window
        assert "import" in window
        assert "not" in window or "never" in window

    def test_docstring_says_fit_regime_classifier_has_no_code_caller(self):
        doc = _doc()
        assert "fit_regime_classifier" in doc
        windows = [
            _window_around(doc, "fit_regime_classifier", 200, start=i)
            for i in _occurrences(doc, "fit_regime_classifier")
        ]
        assert any("no code caller" in w.lower() for w in windows), windows

    def test_module_source_still_avoids_the_phrase_the_neighbour_test_forbids(self):
        source = Path(regime_classifier.__file__).read_text(encoding="utf-8")
        assert "supersedes this default" not in source


class TestTheClaimsAreTrueOfTheCode:
    def test_the_autotuner_and_the_label_job_call_classify_regime(self):
        for name in ("autotuner.py", "regime_label_job.py"):
            assert _calls_attribute(REPO / name, "classify_regime"), name

    @pytest.mark.parametrize("name", ["alpha_bot_execution.py", "math_engine.py"])
    def test_the_engine_and_math_engine_never_import_regime_classifier(self, name):
        assert not _imports_regime_classifier(REPO / name)

    def test_no_production_module_calls_fit_regime_classifier(self):
        callers = [
            str(p.relative_to(REPO))
            for p in _production_modules()
            if p.name != "regime_classifier.py"
            and (
                _calls_attribute(p, "fit_regime_classifier")
                or "fit_regime_classifier(" in p.read_text(encoding="utf-8", errors="replace")
            )
        ]
        assert callers == []
