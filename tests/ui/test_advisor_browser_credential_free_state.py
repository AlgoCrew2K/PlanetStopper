"""The browser captures read the pages with no credential left in any module value.

Importing the app loads the developer's ``.env``, and a few modules read the environment once,
at import (``alpha_bot_execution.COMPOSER_KEY_ID`` and its neighbours). The Advisor tabs read those
values (a Composer key present or absent changes what three of them say), so a capture made on a
machine with a ``.env`` differs from one made on a machine without it: the heading margin check
failed in a checkout with no ``.env`` against a baseline written with one. The shared Advisor
browser plumbing and the baseline producer now put those values back to what an import without a
``.env`` gives.

Which values need that is found here by behaviour, not by a list written beside the fix: two
fresh interpreters import the modules, one with no credential variable and one with invented
values in their place (what a ``.env`` does), and every attribute that differs between them is a
value the environment sets at import. The test then proves each of those attributes comes back to
the no-credential value. Nothing here reads the developer's ``.env``: the interpreters replace
``dotenv.load_dotenv`` with a no-op and run on a scratch database.

Provenance of the invented values: synthetic and labelled so, none a real credential.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from tests.ui._advisor_browser_support import (
    _CREDENTIAL_VARIABLES,
    _stub_database_load_state_for_advisor_routes,  # noqa: F401  (autouse override)
    _stub_get_api_state_dict,  # noqa: F401  (autouse override)
    advisor_server,  # noqa: F401  (fixture under test)
    restore_credential_free_module_state,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
PRODUCER = (
    REPO
    / "tests"
    / "fixtures"
    / "dashboard"
    / "heading_margin_baseline"
    / "produce_heading_margin_baseline.py"
)

# Modules the environment can reach at import; the scan reports every attribute of these that a
# credential variable changes, so a module not listed here is the only way a value escapes.
_MODULES = ("alpha_bot_execution", "synthetic_history", "ai_advisor", "app")

# Invented values (synthetic): shaped like what a `.env` carries, none of them a credential.
_INVENTED = {
    "COMPOSER_KEY_ID": "invented-composer-key-id",
    "COMPOSER_SECRET": "invented-composer-secret",
    "ALPACA_KEY": "invented-alpaca-key",
    "ALPACA_SECRET": "invented-alpaca-secret",
    "DISCORD_WEBHOOK_URL": "https://webhook.invalid/invented",
    "ANTHROPIC_API_KEY": "invented-anthropic-key",
    "ACCOUNT_INDIVIDUAL": "invented-account-individual",
    "ACCOUNT_ROTH": "invented-account-roth",
    "ACCOUNT_TRAD": "invented-account-trad",
    "LIVE_EXECUTION": "true",
    "EXIT_AUTHORITY": "port_level",
    "EXECUTION_START_TIME": "09:45",
}

_PROBE = """
import dotenv, importlib, json, os, sys
dotenv.load_dotenv = lambda *args, **kwargs: False
out = {}
for name in sys.argv[1].split(","):
    module = importlib.import_module(name)
    values = {}
    for attr, value in vars(module).items():
        if attr.startswith("__"):
            continue
        if isinstance(value, tuple):
            value = list(value)
        flat = isinstance(value, (str, int, float, bool, type(None))) or (
            isinstance(value, list)
            and all(isinstance(item, (str, int, float, bool, type(None))) for item in value)
        )
        if flat:
            values[attr] = value
    out[name] = values
print(json.dumps(out))
"""


def _dump(tmp_path, extra_env: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_VARIABLES}
    env["DB_PATH"] = str(tmp_path / "probe_state.db")
    env["ATLAS_CACHE_DB_PATH"] = str(tmp_path / "probe_atlas.db")
    env.update(extra_env)
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, ",".join(_MODULES)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, f"the probe failed: {done.stderr[-600:]}"
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def environment_set_values(tmp_path_factory):
    """{(module, attribute): (value with no credential, value with invented ones)}, for every
    attribute that the credential variables change and a second clean import leaves alone (a
    token or a start stamp differs on every import and is not a credential)."""
    scratch = tmp_path_factory.mktemp("credential_free_probe")
    clean = _dump(scratch, {})
    clean_again = _dump(scratch, {})
    with_values = _dump(scratch, _INVENTED)
    found = {}
    for module, values in clean.items():
        for attr, value in values.items():
            if value != clean_again[module].get(attr):
                continue
            if value != with_values[module].get(attr):
                found[(module, attr)] = (value, with_values[module].get(attr))
    return found


def test_the_probe_finds_the_values_a_dotenv_sets_at_import(environment_set_values):
    names = {attr for _module, attr in environment_set_values}
    assert {"COMPOSER_KEY_ID", "COMPOSER_SECRET", "ALPACA_KEY", "ALPACA_SECRET"} <= names, (
        "premise: invented credential variables must show up as module values"
    )


def test_every_value_the_environment_sets_at_import_is_put_back(
    environment_set_values, monkeypatch
):
    for (module_name, attr), (_clean, polluted) in environment_set_values.items():
        monkeypatch.setattr(importlib.import_module(module_name), attr, polluted)

    restore_credential_free_module_state(monkeypatch.setattr)

    not_restored = {
        f"{module_name}.{attr}"
        for (module_name, attr), (clean, _polluted) in environment_set_values.items()
        if getattr(importlib.import_module(module_name), attr) != clean
    }
    assert not_restored == set(), (
        f"these module values keep what the environment set at import: {sorted(not_restored)}"
    )


@pytest.fixture
def polluted_module_values(environment_set_values, monkeypatch):
    """Every environment-set value at the invented one, before the server fixture runs."""
    for (module_name, attr), (_clean, polluted) in environment_set_values.items():
        monkeypatch.setattr(importlib.import_module(module_name), attr, polluted)


def test_the_advisor_server_fixture_serves_with_those_values_put_back(
    polluted_module_values, environment_set_values, advisor_server
):
    left = {
        f"{module_name}.{attr}"
        for (module_name, attr), (clean, _polluted) in environment_set_values.items()
        if getattr(importlib.import_module(module_name), attr) != clean
    }
    assert left == set(), f"the server runs with credential-derived values left in: {sorted(left)}"


def test_the_baseline_producer_puts_them_back_after_it_removes_the_variables():
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    def calls_restore_with_setattr(node: ast.AST) -> bool:
        return any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "restore_credential_free_module_state"
            and [getattr(arg, "id", None) for arg in call.args] == ["setattr"]
            for call in ast.walk(node)
        )

    def pops_the_variables(node: ast.AST) -> bool:
        return isinstance(node, ast.For) and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "pop"
            for call in ast.walk(node)
        )

    restore_at = [i for i, node in enumerate(main.body) if calls_restore_with_setattr(node)]
    pop_at = [i for i, node in enumerate(main.body) if pops_the_variables(node)]
    imports_app = [
        i
        for i, node in enumerate(main.body)
        if isinstance(node, ast.Import) and any(alias.name == "app" for alias in node.names)
    ]
    assert restore_at, "the producer never calls restore_credential_free_module_state(setattr)"
    assert pop_at and imports_app
    assert restore_at[0] > max(pop_at[0], imports_app[0]), (
        "the module values must be put back after the app import and after the variables are removed"
    )
