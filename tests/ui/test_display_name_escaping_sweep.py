"""A symphony's display name is printed literally on every surface that labels a symphony.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_display_name_escaping_sweep.py``).
Firefox headless through Playwright, imported inside a fixture so collection works on a
host without Playwright. Never skips: a missing Playwright or Firefox fails with a
message naming it.

One live symphony carries a name with an HTML bold-tag pair and an ampersand (the derived
record ``markup_name``, see ``tests/fixtures/settings/PROVENANCE.md``). The same Composer
key and normalized key reach each producer through its production writer:
``database.save_state``, ``database.save_autotune_run``,
``database.insert_advisor_observation``, ``database.insert_frontrunner_proposal`` and
``database.record_shadow_observation``. The app runs in this process
(``tests/ui/_browser_server.py``) on the per-test database; outbound requests from the page
are refused and ``app.ENV_FILE_PATH`` points at a temporary file, so the developer ``.env``
is never read.

For each of the surfaces the test asserts three things about the rendered DOM: the name
shows character for character, no element is created from the name's markup, and the
Composer key (or normalized key) is only in a ``title`` attribute where the surface keeps
one. The page-wide check, no bold element whose text is the name's inner word, also fails
for a surface this module does not list.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app as app_module
import database
from tests.ui._browser_server import running_server
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

pytestmark = pytest.mark.browser

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_DERIVED_FX = _FIXTURES / "settings" / "derived_name_records.json"
_ENV_FX = _FIXTURES / "app" / "settings_env_snapshot.json"

_PAGE_TIMEOUT_MS = 10_000
_WAIT_MS = 6_000

# The marked-up part of the name: a bold element would show only this word.
MARKUP_INNER_WORD = "Ridge"

# Enough one-per-day shadow rows to clear guard_preconditions.N_MIN_OBS, so the
# preconditions panel renders rows instead of its empty state.
_SHADOW_DAYS = 45


# ---------------------------------------------------------------------------
# The conftest stubs in this directory replace database.load_state and the state
# dict; this module needs the real ones, so it overrides both with no-ops.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    yield


@pytest.fixture(autouse=True)
def _stub_database_load_state_for_advisor_routes():
    yield


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _markup_record() -> tuple[str, dict]:
    entry = json.loads(_DERIVED_FX.read_text(encoding="utf-8"))["markup_name"]
    return entry["source_key"], entry["record"]


def _trading_days(count: int) -> list[str]:
    """``count`` consecutive weekday dates ending on a fixed past Friday (ISO strings)."""
    day = date(2026, 9, 25)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return list(reversed(days))


def _seed_shadow_series(key: str) -> None:
    """Write ``_SHADOW_DAYS`` end-of-day rows of varying if-held return through the writer."""
    for index, trading_day in enumerate(_trading_days(_SHADOW_DAYS)):
        value = ((index * 7) % 11 - 5) / 10.0 + (0.05 if index % 2 else -0.05)
        database.record_shadow_observation(
            symphony_id=key,
            account_id=None,
            cycle_id=f"cycle-{index}",
            ts_utc=f"{trading_day}T20:00:00+00:00",
            ts_et=f"{trading_day}T16:00:00-04:00",
            trading_day=trading_day,
            current_return=value,
            shadow_return=value,
            is_post_trigger=0,
            trigger_id=None,
        )


@pytest.fixture
def markup_symphony(tmp_path, monkeypatch):
    """Seed one live symphony whose name carries markup; return (key, name, normalized key)."""
    for name in (
        "LIVE_EXECUTION",
        "EXIT_AUTHORITY",
        "EXECUTION_START_TIME",
        "COMPOSER_KEY_ID",
        "COMPOSER_SECRET",
        "ALPACA_KEY",
        "ALPACA_SECRET",
        "DISCORD_WEBHOOK_URL",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    snapshot = json.loads(_ENV_FX.read_text(encoding="utf-8"))
    values = {**snapshot["secrets"], **snapshot["non_secrets"], "LIVE_EXECUTION": "False"}
    env_path = tmp_path / "sweep.env"
    env_path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_path))

    key, record = _markup_record()
    name = record["name"]
    normalized = database.normalize_name(name)
    database.save_state({key: record})

    database.save_autotune_run(
        run_timestamp="2026-09-26T03:00:00+00:00",
        symphony_id=normalized,
        oos_alpha=1.25,
        train_alpha=2.5,
        baseline_decision="Adopted AI",
        fallback_oos_alpha=0.5,
        default_oos_alpha=0.25,
    )
    database.insert_advisor_observation(
        advisor_role="SPEC_CRITIC",
        subject_type="symphony",
        subject_id=key,
        verdict="no-concern",
        raw_response={"note": "sweep row"},
        symphony_id=normalized,
    )
    database.insert_frontrunner_proposal(
        symphony_id=key,
        proposal_source="frontrunner_builder",
        candidate_tree={"step": "root", "children": []},
        metrics_json={},
    )
    _seed_shadow_series(key)
    return SimpleNamespace(key=key, name=name, normalized=normalized)


# ---------------------------------------------------------------------------
# Browser plumbing
# ---------------------------------------------------------------------------


@pytest.fixture
def server(markup_symphony):
    with running_server() as running:
        yield running


@pytest.fixture
def probe(firefox, server):
    context = firefox.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(_PAGE_TIMEOUT_MS)
    state = SimpleNamespace(page=page, server=server, console_errors=[], page_errors=[])

    def refuse_outbound(route):
        if route.request.url.startswith(server.base_url):
            route.continue_()
        else:
            route.abort()

    def on_console(message):
        if message.type == "error" and not re.search(
            r"Failed to load resource|NetworkError|NS_ERROR|net::", message.text
        ):
            state.console_errors.append(message.text)

    page.route("**/*", refuse_outbound)
    page.on("console", on_console)
    page.on("pageerror", lambda exc: state.page_errors.append(str(exc)))
    try:
        yield state
    finally:
        context.close()


def _fail(probe, message: str):
    raise AssertionError(
        f"{message} (page errors: {probe.page_errors}; console errors: {probe.console_errors})"
    )


def wait_until(probe, expression: str, message: str, arg=None):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        probe.page.wait_for_function(expression, arg=arg, timeout=_WAIT_MS)
    except PlaywrightTimeout:
        _fail(probe, message)


# Reads one element's rendered text, its attribute, and whether its markup became elements.
_ELEMENT_FACTS_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  return {
    text: el.textContent.trim(),
    title: el.getAttribute('title'),
    childElements: el.children.length,
  };
}
"""

# Every bold element on the page whose text is the marked-up word of the name.
_STRAY_BOLD_JS = """
(word) => Array.from(document.querySelectorAll('b'))
  .filter((b) => b.textContent.trim() === word).length
"""


def element_facts(probe, selector: str) -> dict:
    facts = probe.page.evaluate(_ELEMENT_FACTS_JS, selector)
    if facts is None:
        _fail(probe, f"no element matches {selector!r}")
    return facts


def assert_name_is_literal(probe, symphony, selector: str, *, title_expected: str | None):
    """The element reads the name verbatim, holds no child element, and keeps the id in its title."""
    wait_until(
        probe,
        "(s) => { const e = document.querySelector(s); return !!e && e.textContent.trim().length > 0; }",
        f"{selector!r} never rendered any text",
        arg=selector,
    )
    facts = element_facts(probe, selector)
    assert facts["text"] == symphony.name, (
        f"{selector!r} shows {facts['text']!r}, not the name {symphony.name!r}"
    )
    assert facts["childElements"] == 0, (
        f"{selector!r} turned the name's markup into {facts['childElements']} element(s)"
    )
    if title_expected is not None:
        assert facts["title"] == title_expected, (
            f"{selector!r} title is {facts['title']!r}, expected {title_expected!r}"
        )
    stray = probe.page.evaluate(_STRAY_BOLD_JS, MARKUP_INNER_WORD)
    assert stray == 0, f"{stray} bold element(s) made from the name's markup are on the page"
    assert probe.page_errors == [], probe.page_errors
    assert probe.console_errors == [], probe.console_errors


# ---------------------------------------------------------------------------
# The probe can fail: an unescaped render of the same name is detected
# ---------------------------------------------------------------------------


def test_probe_detects_an_unescaped_name(probe, markup_symphony):
    """An innerHTML render of the name, the defect under test, fails every check above."""
    probe.page.goto(probe.server.base_url + "/settings")
    probe.page.evaluate(
        """(name) => {
             const d = document.createElement('div');
             d.id = 'unescaped-control';
             d.innerHTML = name;
             document.body.appendChild(d);
           }""",
        markup_symphony.name,
    )
    facts = element_facts(probe, "#unescaped-control")
    assert facts["childElements"] == 1, "the control must create an element from the markup"
    assert facts["text"] != markup_symphony.name
    assert probe.page.evaluate(_STRAY_BOLD_JS, MARKUP_INNER_WORD) == 1


# ---------------------------------------------------------------------------
# The six surfaces
# ---------------------------------------------------------------------------


def test_settings_modal_title_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/")
    probe.page.evaluate("(key) => window.openSymphonySettings(null, key)", markup_symphony.key)
    title_selector = f'#sym-settings-modal [data-modal-surface] [title="{markup_symphony.key}"]'
    assert_name_is_literal(probe, markup_symphony, title_selector, title_expected=markup_symphony.key)


def test_settings_modal_confirm_dialog_shows_the_name_literally(probe, markup_symphony):
    """Turning live trading on opens the confirmation, which names the symphony."""
    probe.page.goto(probe.server.base_url + "/")
    probe.page.evaluate("(key) => window.openSymphonySettings(null, key)", markup_symphony.key)
    toggle = probe.page.locator("#sym-settings-live-toggle")
    toggle.click()
    probe.page.locator("#sym-settings-save").click()
    wait_until(
        probe,
        "() => !!document.querySelector('#sym-settings-confirm')",
        "the live-trading confirmation never opened",
    )
    assert_name_is_literal(
        probe,
        markup_symphony,
        "#sym-settings-confirm strong",
        title_expected=markup_symphony.key,
    )


def test_performance_preconditions_row_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/performance")
    wait_until(
        probe,
        "() => document.querySelectorAll('[data-testid=\"guard-alpha-preconditions-row\"]').length > 0",
        "the preconditions panel rendered no rows",
    )
    assert_name_is_literal(
        probe,
        markup_symphony,
        '[data-testid="guard-alpha-preconditions-row"] td:first-child',
        title_expected=markup_symphony.key,
    )


def test_performance_exit_turnover_row_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/performance")
    wait_until(
        probe,
        "() => document.querySelectorAll('[data-testid=\"exit-turnover-row\"]').length > 0",
        "the exit-turnover panel rendered no rows",
    )
    assert_name_is_literal(
        probe,
        markup_symphony,
        '[data-testid="exit-turnover-row"] td:first-child',
        title_expected=markup_symphony.key,
    )


def test_frontrunner_card_title_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/ai-advisor")
    assert_name_is_literal(
        probe,
        markup_symphony,
        '[data-testid="frontrunner-proposal-card"] .card-candidate-id',
        title_expected=markup_symphony.key,
    )


def test_autotune_run_card_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/ai-advisor")
    assert_name_is_literal(
        probe,
        markup_symphony,
        "#autotune-runs-list .autotune-run-name",
        title_expected=markup_symphony.normalized,
    )


def test_observations_row_shows_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/ai-advisor")
    assert_name_is_literal(
        probe,
        markup_symphony,
        '[data-testid="obs-symphony"]',
        title_expected=markup_symphony.normalized,
    )


def test_settings_detail_header_and_list_entry_show_the_name_literally(probe, markup_symphony):
    probe.page.goto(probe.server.base_url + "/settings")
    wait_until(
        probe,
        "() => document.querySelectorAll('#sym-detail .override-row').length > 0",
        "the settings detail never rendered",
    )
    assert_name_is_literal(
        probe, markup_symphony, "#sym-detail .sym-detail-name", title_expected=markup_symphony.key
    )
    # The list entry carries the name in its own title attribute: it must hold the characters
    # literally, which an unescaped attribute would cut short at the first quote or bracket.
    entry = element_facts(probe, "#sym-list .sym-list-btn .sym-list-name")
    assert entry["text"] == markup_symphony.name
    assert entry["childElements"] == 0
    assert entry["title"] == markup_symphony.name
