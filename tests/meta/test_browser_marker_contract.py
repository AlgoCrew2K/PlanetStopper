"""Contract of the ``browser`` pytest marker and the in-process server it relies on.

A module that drives a real browser is marked ``browser``. CI installs no
Playwright and never selects the marker, so three things must hold on a host
without Playwright: the marker is registered (``--strict-markers`` makes an
unregistered one a collection error), the default run deselects it, and no test
module imports Playwright when it is imported (an import error aborts the whole
default collection; ``importorskip`` turns into a skip against the zero-skip
mandate). The browser modules import Playwright inside a fixture instead.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

import pytest
from _pytest.mark.expression import Expression

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
PYPROJECT = REPO / "pyproject.toml"

# Modules that already import Playwright at module level and drive a running
# daemon at localhost:5000. They predate the marker and are converted when their
# own surface is reworked; the list shrinks to empty, and a stale entry fails below.
LEGACY_MODULE_LEVEL_PLAYWRIGHT = frozenset({"tests/ui/test_advisor_card_overflow.py"})

_IMPORT_CALLS = {"importorskip", "import_module", "__import__"}
_SUBPROCESS_TIMEOUT_SECONDS = 240


def _pytest_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def _marker_expression() -> str:
    tokens = shlex.split(_pytest_config()["addopts"])
    assert "-m" in tokens, "pyproject.toml addopts carries no -m marker expression"
    return tokens[tokens.index("-m") + 1]


def _selected_by_default(*marks: str) -> bool:
    """True when a test carrying exactly ``marks`` would run under the default addopts."""
    return Expression.compile(_marker_expression()).evaluate(lambda name, **_kw: name in marks)


def _module_level_nodes(tree: ast.AST):
    """Every node that runs when the module is imported (function bodies are skipped)."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _imports_playwright_at_module_level(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in _module_level_nodes(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "playwright" for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "playwright"
        ):
            return True
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            if (
                name in _IMPORT_CALLS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.split(".")[0] == "playwright"
            ):
                return True
    return False


def _test_modules() -> list[pathlib.Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def _marked_browser_modules() -> list[str]:
    found = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "pytestmark" for t in node.targets
            ):
                if "mark.browser" in ast.unparse(node.value):
                    found.append(path.relative_to(REPO).as_posix())
    return found


def _collect(
    extra_args: list[str], paths: list[str], env_extra: dict
) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            # pytest-playwright imports Playwright when pytest starts; a host without
            # Playwright has no such plugin, so the simulation drops it too.
            "-p",
            "no:playwright",
            *extra_args,
            *paths,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def test_browser_marker_is_registered():
    markers = _pytest_config()["markers"]
    assert any(m.split(":")[0].strip() == "browser" for m in markers), (
        "pyproject.toml [tool.pytest.ini_options].markers must register 'browser'; "
        "--strict-markers turns an unregistered marker into a collection error"
    )


def test_default_run_deselects_browser_and_keeps_the_other_gates():
    assert not _selected_by_default("browser"), (
        "a test marked browser must be deselected by the default addopts -m expression "
        f"({_marker_expression()!r})"
    )
    for gate in ("live", "slow", "perf"):
        assert not _selected_by_default(gate), f"adding browser must not drop the {gate} gate"
    assert _selected_by_default(), "an unmarked test must still run by default"


def test_no_test_module_imports_playwright_when_it_is_imported():
    offenders = sorted(
        p.relative_to(REPO).as_posix()
        for p in _test_modules()
        if _imports_playwright_at_module_level(p)
        and p.relative_to(REPO).as_posix() not in LEGACY_MODULE_LEVEL_PLAYWRIGHT
    )
    assert not offenders, (
        "these modules import Playwright (or importorskip it) at module level, which breaks "
        f"collection on a host without it; import it inside a fixture instead: {offenders}"
    )


@pytest.mark.parametrize("legacy", sorted(LEGACY_MODULE_LEVEL_PLAYWRIGHT))
def test_legacy_playwright_exemption_is_not_stale(legacy):
    path = REPO / legacy
    assert path.exists() and _imports_playwright_at_module_level(path), (
        f"{legacy} no longer imports Playwright at module level; remove it from "
        "LEGACY_MODULE_LEVEL_PLAYWRIGHT so the rule covers it"
    )


def test_settings_controls_module_is_a_browser_module():
    assert "tests/ui/test_settings_controls_browser.py" in _marked_browser_modules()


def test_browser_modules_collect_on_a_host_without_playwright(tmp_path):
    """Importing every browser module must not need Playwright, and the default run skips them."""
    blocker = tmp_path / "blocker" / "playwright"
    blocker.mkdir(parents=True)
    (blocker / "__init__.py").write_text(
        "raise ImportError('playwright is not installed on this host')\n", encoding="utf-8"
    )
    env = {
        "PYTHONPATH": os.pathsep.join(
            [str(blocker.parent), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
        "DB_PATH": str(tmp_path / "collect_only.db"),
    }
    modules = _marked_browser_modules()
    assert modules, "no module is marked browser"

    selected = _collect(["-m", "browser"], modules, env)
    assert selected.returncode == 0, (
        "collecting the browser modules failed without Playwright:\n"
        f"{selected.stdout[-1500:]}\n{selected.stderr[-1500:]}"
    )
    for module in modules:
        assert f"{module}::" in selected.stdout.replace("\\", "/"), (
            f"{module} collected no tests under -m browser without Playwright"
        )

    default = _collect([], modules, env)
    listed = [line for line in default.stdout.replace("\\", "/").splitlines() if "::" in line]
    assert not listed, f"the default run must not collect browser tests, but listed: {listed[:5]}"


def test_in_process_server_serves_the_per_test_database():
    """The server a browser test starts answers from the database that test wrote to."""
    import database
    from tests.ui._browser_server import start_server

    state = {
        "keyA1b2C3d4E5f6G7h8": {"name": "Server Probe Symphony", "current_value": 1.0},
    }
    database.save_state(state)
    server = start_server()
    try:
        assert server.base_url.startswith("http://127.0.0.1:"), server.base_url
        assert not server.base_url.endswith(":0"), "the port must be the one the OS assigned"
        with urllib.request.urlopen(server.base_url + "/api/settings", timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert [s["name"] for s in body["symphonies_list"]] == ["Server Probe Symphony"]
        with urllib.request.urlopen(server.base_url + "/settings", timeout=30) as resp:
            assert resp.status == 200
    finally:
        server.stop()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(server.base_url + "/settings", timeout=5)
