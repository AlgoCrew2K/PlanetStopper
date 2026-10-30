"""The model-resolution and request-helper modules stay off the live engine path.

``model_config`` and ``llm_request`` serve advisory LLM calls only. Neither may
be referenced from the 1-minute execution engine or its risk math, and neither
may import them, so a model listing or a Messages request can never sit on the
path that places orders. Same shape as the incubation blast-radius test.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]

_ENGINE_FILES = ("alpha_bot_execution.py", "math_engine.py")
_ADVISORY_MODULES = ("model_config.py", "llm_request.py")
_ADVISORY_SYMBOLS = ("model_config", "llm_request")
_ENGINE_MODULE_NAMES = ("alpha_bot_execution", "math_engine")


def _read(name: str) -> str:
    path = _ROOT / name
    assert path.exists(), f"{name} must exist for the blast-radius scan to mean anything"
    return path.read_text(encoding="utf-8", errors="ignore")


def _reference_lines(source: str, symbol: str) -> list[int]:
    return [i + 1 for i, line in enumerate(source.splitlines()) if symbol in line]


def _imported_modules(source: str) -> list[str]:
    """Every module named by an import anywhere in the source, at any depth."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.append(module)
            found.extend(f"{module}.{alias.name}".lstrip(".") for alias in node.names)
    return found


def _top_level_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


@pytest.mark.parametrize("engine_file", _ENGINE_FILES)
@pytest.mark.parametrize("symbol", _ADVISORY_SYMBOLS)
def test_the_live_engine_files_never_reference_the_advisory_modules(engine_file, symbol):
    hits = _reference_lines(_read(engine_file), symbol)

    assert not hits, (
        f"{engine_file} references {symbol!r} at line(s) {hits}; advisory LLM code "
        "must never reach the live engine path"
    )


@pytest.mark.parametrize("module_file", _ADVISORY_MODULES)
def test_the_advisory_modules_never_import_the_live_engine(module_file):
    imported = _imported_modules(_read(module_file))

    offending = [m for m in imported if m.split(".")[0] in _ENGINE_MODULE_NAMES]
    assert offending == [], f"{module_file} imports the live engine: {offending}"


def test_the_request_helper_loads_no_anthropic_code_at_import_time():
    top_level = _top_level_imports(_read("llm_request.py"))

    assert [m for m in top_level if m.split(".")[0] == "anthropic"] == []


def test_the_request_helper_imports_anthropic_lazily_inside_send():
    assert "anthropic" in _imported_modules(_read("llm_request.py"))


class TestTheScanCanFail:
    ENGINE_WITH_PLANT = "import os\nfrom model_config import resolve\n"
    ENGINE_CLEAN = "import os\nfrom math import sqrt\n"

    def test_a_planted_reference_is_found(self):
        assert _reference_lines(self.ENGINE_WITH_PLANT, "model_config") == [2]
        assert _reference_lines(self.ENGINE_CLEAN, "model_config") == []

    def test_a_planted_engine_import_in_an_advisory_module_is_found(self):
        planted = (
            "import logging\n\ndef f():\n    from alpha_bot_execution import COMPOSER_BASE_URL\n"
        )

        assert any(m.split(".")[0] in _ENGINE_MODULE_NAMES for m in _imported_modules(planted))

    def test_a_planted_module_level_anthropic_import_is_found(self):
        planted = "import anthropic\n\ndef send():\n    pass\n"
        lazy = "def send():\n    import anthropic\n"

        assert _top_level_imports(planted) == ["anthropic"]
        assert _top_level_imports(lazy) == []
