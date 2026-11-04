"""Shared plumbing for the AI Advisor browser modules (the ``browser`` marker).

TEST-ONLY. Playwright is imported inside fixtures, never at module level, so collection works
on a host without it; a selected test with Playwright or Firefox missing fails with a message
naming it (``firefox`` comes from the dashboard browser module).

The server is the app in this process (``tests/ui/_browser_server.py``) on the per-test
database. Every request the page makes to anything but that server is aborted, so the chart
library's CDN request never completes and no outside host is contacted. The developer
``.env`` is never read: ``app.ENV_FILE_PATH`` points at an empty temporary file, every
credential variable is removed from the environment and every module value the environment set
at import is put back to what an import without ``.env`` gives (``restore_credential_free_module_state``).

A module imports these names and lists them (``# noqa: F401``): the two autouse overrides
(this directory's conftest stubs ``database.load_state`` and the state dict, which the page
needs real), ``advisor_server`` and ``advisor_probe``.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import app as app_module
from tests.ui._browser_server import running_server
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

# Console errors a refused request causes; any other error is the page's own.
_REFUSED_REQUEST_NOISE = re.compile(r"Failed to load resource|NetworkError|NS_ERROR|net::")

PAGE_TIMEOUT_MS = 10_000
WAIT_MS = 8_000

_CREDENTIAL_VARIABLES = (
    "LIVE_EXECUTION",
    "EXIT_AUTHORITY",
    "EXECUTION_START_TIME",
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
)


# What an import with no ``.env`` and none of the variables above leaves in each module value
# that is read from the environment once, at import. Importing the app loads the developer's
# ``.env`` and these values keep what it held, which the Advisor pages read (a Composer key
# present or absent changes what the Strategy Builder, Asset Swaps and Logic Changes tabs say), so
# a capture made on a machine with a ``.env`` differs from one made without it. A new value that
# the environment sets at import joins this table; the test of this module names the full set.
_NO_ENV_MODULE_VALUES = (
    ("alpha_bot_execution", "COMPOSER_KEY_ID", lambda: None),
    ("alpha_bot_execution", "COMPOSER_SECRET", lambda: None),
    ("alpha_bot_execution", "acc_ind", lambda: ""),
    ("alpha_bot_execution", "acc_roth", lambda: ""),
    ("alpha_bot_execution", "acc_trad", lambda: ""),
    ("alpha_bot_execution", "ACCOUNT_UUIDS", list),
    ("alpha_bot_execution", "ALPACA_KEY", lambda: None),
    ("alpha_bot_execution", "ALPACA_SECRET", lambda: None),
    ("alpha_bot_execution", "DISCORD_WEBHOOK_URL", lambda: None),
    ("alpha_bot_execution", "LIVE_EXECUTION", lambda: False),
    ("alpha_bot_execution", "EXECUTION_START_TIME", lambda: "09:30"),
    ("synthetic_history", "ALPACA_KEY", lambda: None),
    ("synthetic_history", "ALPACA_SECRET", lambda: None),
)


def restore_credential_free_module_state(setter) -> None:
    """Put every environment-derived module value back to what an import without ``.env`` gives.

    ``setter(module, name, value)`` is ``monkeypatch.setattr`` in a test, so the value is put
    back at the test's end, and plain ``setattr`` in a one-shot script. Call it after the app
    has been imported and after the variables of ``_CREDENTIAL_VARIABLES`` are removed from the
    environment, so the module values and the environment agree.
    """
    import importlib

    for module_name, name, fresh in _NO_ENV_MODULE_VALUES:
        setter(importlib.import_module(module_name), name, fresh())


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    yield


@pytest.fixture(autouse=True)
def _stub_database_load_state_for_advisor_routes():
    yield


@pytest.fixture
def advisor_server(tmp_path, monkeypatch):
    for name in _CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    restore_credential_free_module_state(monkeypatch.setattr)
    env_file = tmp_path / "advisor_page.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    with running_server() as running:
        yield running


@pytest.fixture
def advisor_probe(firefox, advisor_server):  # noqa: F811  (the imported module-scoped fixture)
    """A fresh browser context per test (no cached chart library), 1440x900, outbound refused."""
    context = firefox.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    state = SimpleNamespace(
        page=page,
        server=advisor_server,
        context=context,
        console_errors=[],
        page_errors=[],
        refused=[],
    )

    def refuse_outbound(route):
        if route.request.url.startswith(advisor_server.base_url):
            route.continue_()
        else:
            state.refused.append(route.request.url)
            route.abort()

    def on_console(message):
        if message.type == "error" and not _REFUSED_REQUEST_NOISE.search(message.text):
            state.console_errors.append(message.text)

    page.route("**/*", refuse_outbound)
    page.on("console", on_console)
    page.on("pageerror", lambda exc: state.page_errors.append(str(exc)))
    try:
        yield state
    finally:
        context.close()


def fail(probe, message: str):
    raise AssertionError(
        f"{message} (page errors: {probe.page_errors}; console errors: {probe.console_errors})"
    )


def wait_until(probe, expression: str, message: str, arg=None, timeout_ms: int = WAIT_MS):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        probe.page.wait_for_function(expression, arg=arg, timeout=timeout_ms)
    except PlaywrightTimeout:
        fail(probe, message)


def open_advisor(probe, fragment: str = ""):
    """Load the Advisor page and wait for its script to have run (the tab bar is wired)."""
    probe.page.goto(probe.server.base_url + "/ai-advisor" + fragment)
    wait_until(
        probe,
        "() => document.querySelector('[data-testid=\"tab-panel-overview\"]') !== null",
        "the Advisor page never rendered its Overview panel",
    )
