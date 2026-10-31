"""The Settings page in a real browser: Reset to globals, the read-only live card,
override counts, the save refusal, and the detail header.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_settings_controls_browser.py``).
Firefox headless through Playwright, imported inside a fixture so collection works on
a host without Playwright. Never skips: a missing Playwright or Firefox fails with a
message naming it.

The server is the app in this process (``tests/ui/_browser_server.py``) on the
per-test database, seeded here through the real writers (``database.save_state`` and
``database.save_symphony_strategy``) from captured records. Outbound requests from the
page are refused. ``app.ENV_FILE_PATH`` points at a temporary ``.env`` built from the
captured env snapshot (placeholder secrets), so the developer ``.env`` is never read or
written.

Reset to globals followed by Save is a money path: the saved row has ``params`` ``{}``
and ``locked_vars`` ``[]``, so the engine falls back to the globals for that symphony
and the next autotune is free to tune the dimensions the locks held. The tests pin the
request body and the stored row exactly, and that no other symphony is touched.
"""

from __future__ import annotations

import json
import pathlib
import re
import urllib.request
from types import SimpleNamespace

import pytest

import app as app_module
import database
from tests.ui._browser_server import running_server
from tests.ui._settings_reference import overrides_label, reference_override_count
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

pytestmark = pytest.mark.browser

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_BOT_STATE_FX = _FIXTURES / "prod_droplet" / "bot_state_positions.json"
_ENV_FX = _FIXTURES / "app" / "settings_env_snapshot.json"
_DERIVED_FX = _FIXTURES / "settings" / "derived_name_records.json"

# Captured display names (bot_state_positions.json). Each carries the character class the
# page must survive in a key: parentheses and a plus sign, a trailing space, an apostrophe
# with a colon and a slash.
NAME_PARENS_PLUS = "(INVEST) LQD + EYEG 5 ways Full Market"
NAME_TRAILING_SPACE = "Corporate Chaos 5 ways "
NAME_APOSTROPHE = "Planet LQD: Run of the Feaver'd WaltAnansi USDs 2/3 Mixup"
NAME_PARENS_WORDS = "Planet of the Golden Age (Buy Copy)"

PARAM_KEYS = list(database.DEFAULT_STRATEGY)
OVERRIDDEN_KEYS = ("TRIGGER_THRESHOLD_PCT", "TAKE_PROFIT_MC_PCT")
STORED_LOCKS = ["TRIGGER_THRESHOLD_PCT", "MAX_SQUEEZE_FLOOR"]
_PAGE_TIMEOUT_MS = 10_000
_WAIT_MS = 4_000


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


def _bot_state_fx() -> dict:
    return json.loads(_BOT_STATE_FX.read_text(encoding="utf-8"))


def _captured(name: str) -> tuple[str, dict]:
    for key, record in _bot_state_fx().items():
        if record.get("name") == name:
            return key, record
    raise AssertionError(f"no captured record named {name!r} in {_BOT_STATE_FX.name}")


def _derived(case: str) -> tuple[str, dict]:
    entry = json.loads(_DERIVED_FX.read_text(encoding="utf-8"))[case]
    return entry["source_key"], entry["record"]


def _overridden_params(keys=OVERRIDDEN_KEYS) -> dict:
    params = dict(database.DEFAULT_STRATEGY)
    for key in keys:
        params[key] = params[key] * 2
    return params


def _seed(entries: list[tuple[str, dict]], strategies: dict[str, tuple[dict, list]] | None = None):
    """Write ``entries`` (Composer key, record) as bot_state and each strategy row."""
    database.save_state({key: record for key, record in entries})
    for name, (params, locked) in (strategies or {}).items():
        database.save_symphony_strategy(name, params, locked)


def _stored(name: str) -> dict:
    return database.get_symphony_strategy(name)


def _norm(name: str) -> str:
    return database.normalize_name(name)


def _write_env(path: pathlib.Path, live: str = "False") -> None:
    snapshot = json.loads(_ENV_FX.read_text(encoding="utf-8"))
    values = {**snapshot["secrets"], **snapshot["non_secrets"], "LIVE_EXECUTION": live}
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


def _json_get(server, path: str) -> dict:
    with urllib.request.urlopen(server.base_url + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Browser plumbing
# ---------------------------------------------------------------------------


@pytest.fixture
def env_file(tmp_path, monkeypatch):
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
        "ACCOUNT_INDIVIDUAL",
        "ACCOUNT_ROTH",
        "ACCOUNT_TRAD",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "settings_page.env"
    _write_env(path)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    return path


@pytest.fixture
def server(env_file):
    with running_server() as running:
        yield running


@pytest.fixture
def probe(firefox, server):
    context = firefox.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(_PAGE_TIMEOUT_MS)
    state = SimpleNamespace(
        page=page,
        server=server,
        console_errors=[],
        page_errors=[],
        writes=[],
        dialogs=[],
    )

    def refuse_outbound(route):
        if route.request.url.startswith(server.base_url):
            route.continue_()
        else:
            route.abort()

    def on_console(message):
        text = message.text
        if message.type == "error" and not re.search(
            r"Failed to load resource|NetworkError|NS_ERROR|net::", text
        ):
            state.console_errors.append(text)

    def on_request(request):
        if request.method != "GET" and request.url.startswith(server.base_url):
            state.writes.append(
                SimpleNamespace(method=request.method, url=request.url, body=request.post_data)
            )

    def on_dialog(dialog):
        state.dialogs.append(dialog.message)
        dialog.accept()

    page.route("**/*", refuse_outbound)
    page.on("console", on_console)
    page.on("pageerror", lambda exc: state.page_errors.append(str(exc)))
    page.on("request", on_request)
    page.on("dialog", on_dialog)
    try:
        yield state
    finally:
        context.close()


def _fail(probe, message: str):
    raise AssertionError(
        f"{message} (page errors: {probe.page_errors}; console errors: {probe.console_errors})"
    )


def wait_until(probe, expression: str, message: str, arg=None, timeout_ms: int = _WAIT_MS):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        probe.page.wait_for_function(expression, arg=arg, timeout=timeout_ms)
    except PlaywrightTimeout:
        _fail(probe, message)


def open_settings(probe, expected_symphonies: int):
    probe.page.goto(probe.server.base_url + "/settings")
    wait_until(
        probe,
        "(n) => document.querySelectorAll('#sym-list .sym-list-btn').length === n",
        f"the symphony list never showed {expected_symphonies} entries",
        arg=expected_symphonies,
    )


_ENTRY_INDEX_JS = """
(key) => Array.from(document.querySelectorAll('#sym-list .sym-list-btn'))
  .findIndex((b) => b.dataset.symId === key)
"""


def _entry_index(probe, key: str) -> int:
    index = probe.page.evaluate(_ENTRY_INDEX_JS, key)
    if index < 0:
        _fail(probe, f"no list entry for symphony key {key!r}")
    return index


def select_symphony(probe, key: str):
    probe.page.locator("#sym-list .sym-list-btn").nth(_entry_index(probe, key)).click()
    wait_until(
        probe,
        """(k) => {
             const b = Array.from(document.querySelectorAll('#sym-list .sym-list-btn'))
               .find((e) => e.dataset.symId === k);
             return !!b && b.classList.contains('sym-active')
               && document.querySelectorAll('#sym-detail .override-row').length > 0;
           }""",
        f"selecting {key!r} did not render its detail",
        arg=key,
    )


def entry_text(probe, key: str) -> str:
    text = probe.page.evaluate(
        "(k) => Array.from(document.querySelectorAll('#sym-list .sym-list-btn'))"
        ".find((b) => b.dataset.symId === k).innerText",
        key,
    )
    return " ".join(text.split())


def entry_tooltips(probe, key: str) -> list[str]:
    return probe.page.evaluate(
        """(k) => {
             const btn = Array.from(document.querySelectorAll('#sym-list .sym-list-btn'))
               .find((b) => b.dataset.symId === k);
             return [btn, ...Array.from(btn.querySelectorAll('[title]'))]
               .filter((e) => e.hasAttribute('title') && !e.classList.contains('sym-list-name'))
               .map((e) => e.getAttribute('title'));
           }""",
        key,
    )


_DETAIL_STATE_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('#sym-detail .override-row'));
  return {
    inputs: Object.fromEntries(rows.map((r) => [r.dataset.key, r.querySelector('.override-input').value])),
    locked: Object.fromEntries(rows.map((r) => [r.dataset.key, r.querySelector('.lock-btn').dataset.locked === '1'])),
    marked: rows.filter((r) => r.classList.contains('override-row-active')).map((r) => r.dataset.key),
    globals: Object.fromEntries(rows.map((r) => [r.dataset.key, r.querySelector('.override-global-val').innerText.trim()])),
    saveDisabled: document.getElementById('btn-save').disabled,
    discardDisabled: document.getElementById('btn-discard').disabled,
  };
}
"""


def detail_state(probe) -> dict:
    return probe.page.evaluate(_DETAIL_STATE_JS)


def click_reset(probe):
    probe.page.locator("#sym-detail .btn-reset-globals").click()


def assert_reset_state(probe, expect_rows: int = len(PARAM_KEYS)):
    wait_until(
        probe,
        """() => {
             const ins = Array.from(document.querySelectorAll('#sym-detail .override-input'));
             return ins.length > 0 && ins.every((i) => i.value === '');
           }""",
        "Reset to globals did not clear the override inputs",
    )
    state = detail_state(probe)
    assert len(state["inputs"]) == expect_rows
    assert all(v == "" for v in state["inputs"].values()), state["inputs"]
    assert not any(state["locked"].values()), f"locks must be cleared by a reset: {state['locked']}"
    assert state["marked"] == [], "no row may carry the overridden marker after a reset"
    assert state["saveDisabled"] is False, "Save must be enabled after a reset"
    assert state["discardDisabled"] is False, "Discard must be enabled after a reset"
    assert probe.writes == [], f"Reset must send no request, saw {[w.url for w in probe.writes]}"
    assert probe.page_errors == [], probe.page_errors
    assert probe.console_errors == [], probe.console_errors
    return state


def _wait_dialog(probe, what: str) -> str:
    for _ in range(60):
        if probe.dialogs:
            return probe.dialogs[-1]
        probe.page.wait_for_timeout(50)
    _fail(probe, f"no alert appeared for {what}")


def click_save_and_capture(probe):
    with probe.page.expect_response(
        lambda r: r.url.endswith("/api/settings") and r.request.method == "POST"
    ) as response_info:
        probe.page.click("#btn-save")
    response = response_info.value
    return response, json.loads(response.request.post_data)


# ---------------------------------------------------------------------------
# Reset to globals
# ---------------------------------------------------------------------------


def _reset_scenario(probe, key: str, record: dict, name: str):
    _seed([(key, record)], {name: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(name)
    select_symphony(probe, norm)
    before = detail_state(probe)
    assert any(v != "" for v in before["inputs"].values()), (
        "stored overrides must show before reset"
    )
    assert any(before["locked"].values()), "stored locks must show before reset"
    globals_payload = _json_get(probe.server, "/api/settings")["globals"]
    click_reset(probe)
    after = assert_reset_state(probe)
    for param, shown in after["globals"].items():
        assert shown.startswith(str(globals_payload[param])), (
            f"the global value for {param} must stay beside its empty override: {shown!r}"
        )
    return before, after


def test_reset_to_globals_clears_overrides_and_locks_without_a_request(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _reset_scenario(probe, key, record, NAME_PARENS_PLUS)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(("captured", NAME_APOSTROPHE), id="apostrophe-colon-slash"),
        pytest.param(("derived", "hash_sign_name"), id="hash-sign"),
        pytest.param(("derived", "non_ascii_name"), id="non-ascii-letters"),
    ],
)
def test_reset_to_globals_survives_special_characters_in_the_key(probe, source):
    kind, ref = source
    key, record = _captured(ref) if kind == "captured" else _derived(ref)
    _reset_scenario(probe, key, record, record["name"])


def test_reset_to_globals_works_for_a_key_with_spaces_and_a_trailing_name_space(probe):
    key, record = _captured(NAME_TRAILING_SPACE)
    _reset_scenario(probe, key, record, NAME_TRAILING_SPACE)


def test_discard_after_reset_restores_stored_overrides_and_locks(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    before = detail_state(probe)
    entry_before = entry_text(probe, norm)
    click_reset(probe)
    assert_reset_state(probe)
    probe.page.click("#btn-discard")
    wait_until(
        probe,
        "() => document.getElementById('btn-save').disabled === true",
        "Discard did not return the page to its saved state",
    )
    select_symphony(probe, norm)
    after = detail_state(probe)
    assert after["inputs"] == before["inputs"], "stored overrides must show again"
    assert after["locked"] == before["locked"], "stored locks must show again"
    assert after["saveDisabled"] is True
    assert entry_text(probe, norm) == entry_before
    assert probe.writes == []


def test_reset_twice_leaves_the_state_of_one_reset_and_sends_nothing(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    select_symphony(probe, _norm(NAME_PARENS_PLUS))
    click_reset(probe)
    first = assert_reset_state(probe)
    click_reset(probe)
    second = assert_reset_state(probe)
    assert second == first


def test_reset_drops_an_unsaved_value_and_the_saved_body_never_carries_it(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    typed_key = PARAM_KEYS[0]
    probe.page.locator(f'#sym-detail .override-row[data-key="{typed_key}"] .override-input').fill(
        "987.5"
    )
    click_reset(probe)
    assert_reset_state(probe)
    _response, body = click_save_and_capture(probe)
    assert body["symphonies"][norm]["params"] == {}, "the dropped value must not reach the save"


def test_discard_after_typing_then_reset_restores_the_stored_value(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    stored = _overridden_params()
    _seed([(key, record)], {NAME_PARENS_PLUS: (stored, list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    typed_key = PARAM_KEYS[0]
    before = detail_state(probe)["inputs"][typed_key]
    probe.page.locator(f'#sym-detail .override-row[data-key="{typed_key}"] .override-input').fill(
        "987.5"
    )
    click_reset(probe)
    assert_reset_state(probe)
    probe.page.click("#btn-discard")
    wait_until(
        probe,
        "() => document.getElementById('btn-save').disabled === true",
        "Discard did not return the page to its saved state",
    )
    select_symphony(probe, norm)
    assert detail_state(probe)["inputs"][typed_key] == before


# ---------------------------------------------------------------------------
# Save after Reset (money path)
# ---------------------------------------------------------------------------


def test_save_after_reset_sends_one_request_with_empty_params_and_no_locks(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    click_reset(probe)
    assert_reset_state(probe)

    response, body = click_save_and_capture(probe)

    assert response.status == 200
    assert body == {"globals": {}, "symphonies": {norm: {"params": {}, "locked_vars": []}}}, body
    stored = _stored(NAME_PARENS_PLUS)
    assert stored["params"] == {}, "the stored row must hold no parameters after reset and save"
    assert stored["locked_vars"] == [], "the stored row must hold no locks after reset and save"
    wait_until(
        probe,
        "() => document.getElementById('btn-save').disabled === true",
        "the page did not reload its saved state after the save",
    )
    assert "uses globals" in entry_text(probe, norm)


def test_reset_of_one_symphony_leaves_every_other_untouched(probe):
    names = [NAME_PARENS_PLUS, NAME_TRAILING_SPACE, NAME_PARENS_WORDS]
    entries = [_captured(n) for n in names]
    strategies = {
        n: (_overridden_params(keys=(OVERRIDDEN_KEYS[i % 2],)), list(STORED_LOCKS[: i + 1]))
        for i, n in enumerate(names)
    }
    _seed(entries, strategies)
    open_settings(probe, 3)
    keys = [_norm(n) for n in names]
    before = {}
    for k in keys:
        select_symphony(probe, k)
        before[k] = detail_state(probe)
    entries_before = {k: entry_text(probe, k) for k in keys}

    select_symphony(probe, keys[1])
    click_reset(probe)
    assert_reset_state(probe)

    for other in (keys[0], keys[2]):
        assert entry_text(probe, other) == entries_before[other]
        select_symphony(probe, other)
        state = detail_state(probe)
        assert state["inputs"] == before[other]["inputs"]
        assert state["locked"] == before[other]["locked"]
    _response, body = click_save_and_capture(probe)
    assert set(body["symphonies"]) == {keys[1]}, "only the reset symphony may be sent"


def test_save_carries_an_edit_to_one_symphony_and_the_reset_of_another(probe):
    names = [NAME_PARENS_PLUS, NAME_TRAILING_SPACE]
    _seed(
        [_captured(n) for n in names],
        {n: (_overridden_params(), list(STORED_LOCKS)) for n in names},
    )
    open_settings(probe, 2)
    a, b = _norm(names[0]), _norm(names[1])
    select_symphony(probe, a)
    edited = "TRIGGER_THRESHOLD_PCT"
    probe.page.locator(f'#sym-detail .override-row[data-key="{edited}"] .override-input').fill("42")
    select_symphony(probe, b)
    click_reset(probe)
    assert_reset_state(probe)

    _response, body = click_save_and_capture(probe)

    expected_a = {**_overridden_params(), edited: 42.0}
    assert body["symphonies"][a]["params"] == expected_a
    assert body["symphonies"][a]["locked_vars"] == STORED_LOCKS
    assert body["symphonies"][b] == {"params": {}, "locked_vars": []}


@pytest.mark.parametrize("action", ["reset", "edit"])
def test_save_is_refused_by_the_server_without_a_token_and_the_page_reports_the_refusal(
    probe, monkeypatch, action
):
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    # The page holds no token: its load-time token request fails. The route works again before the
    # save, so the refusal's token refresh succeeds and the page reports the server's refusal.
    refuse_token = lambda route: route.abort()  # noqa: E731
    probe.page.route("**/api/csrf-token", refuse_token)
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    before_row = _stored(NAME_PARENS_PLUS)
    open_settings(probe, 1)
    probe.page.unroute("**/api/csrf-token", refuse_token)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    if action == "reset":
        click_reset(probe)
        assert_reset_state(probe)
    else:
        probe.page.locator(
            '#sym-detail .override-row[data-key="TAKE_PROFIT_MC_PCT"] .override-input'
        ).fill("21.5")

    response, _body = click_save_and_capture(probe)

    assert response.status == 403
    message = _wait_dialog(probe, "the refused save")
    lowered = message.lower()
    assert "not saved" in lowered and "click save again" in lowered, message
    assert "network error" not in lowered and "connection failed" not in lowered, (
        "a 403 with a working token route is a refusal, not a lost connection; the alert must say so"
    )
    assert "refus" in lowered, message
    assert _stored(NAME_PARENS_PLUS) == before_row, (
        "nothing may be written when the save is refused"
    )
    state = detail_state(probe)
    assert state["saveDisabled"] is False, "the pending edit must stay"
    if action == "reset":
        assert all(v == "" for v in state["inputs"].values()), "the pending reset must stay"
    else:
        assert state["inputs"]["TAKE_PROFIT_MC_PCT"] == "21.5"


def test_a_failed_settings_load_binds_no_reset_button(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    attempts = []

    def fail_get(route):
        if route.request.method == "GET":
            attempts.append(route.request.url)
            route.fulfill(status=500, body="{}", content_type="application/json")
        else:
            route.continue_()

    probe.page.route("**/api/settings", fail_get)
    probe.page.goto(probe.server.base_url + "/settings")
    for _ in range(100):
        if len(attempts) >= 2:
            break
        probe.page.wait_for_timeout(50)
    probe.page.wait_for_timeout(300)

    assert len(attempts) == 2, f"the load retries exactly once, saw {len(attempts)} attempts"
    assert probe.page.locator("#sym-list .sym-list-btn").count() == 0
    assert probe.page.locator(".btn-reset-globals").count() == 0
    assert probe.page.evaluate("document.getElementById('btn-save').disabled") is True
    assert probe.page_errors == []


# ---------------------------------------------------------------------------
# Live execution card: read-only
# ---------------------------------------------------------------------------

_LIVE_CARD_JS = """
() => {
  const toggle = document.getElementById('live-toggle');
  const hints = Array.from(document.querySelectorAll('#live-card .field-hint'));
  return {
    pill: document.getElementById('live-pill').innerText.trim(),
    pillLive: document.getElementById('live-pill').dataset.live,
    cardLive: document.getElementById('live-card').dataset.live,
    toggleOn: toggle.dataset.on,
    ariaChecked: toggle.getAttribute('aria-checked'),
    disabled: toggle.disabled,
    readonly: toggle.getAttribute('aria-readonly'),
    bannerHidden: document.getElementById('live-banner').hidden,
    hints: hints.map((h) => h.innerText.trim()),
    saveDisabled: document.getElementById('btn-save').disabled,
  };
}
"""


def _open_master_controls(probe, pill_text: str):
    # The symphony list renders after the master card, so its entry proves the card is rendered.
    open_settings(probe, 1)
    wait_until(
        probe,
        "(t) => document.getElementById('live-pill').innerText.trim() === t",
        f"the live card never read {pill_text}",
        arg=pill_text,
    )


def _assert_live_card_statement(card: dict):
    assert card["hints"], "the live card must carry a .field-hint line"
    statement = " ".join(card["hints"]).lower()
    assert "armed" in statement and ".env" in statement, card["hints"]
    assert "not from the dashboard" in statement, card["hints"]


def _click_live_toggle_every_way(probe):
    probe.page.click("#live-toggle", force=True)
    probe.page.evaluate("document.getElementById('live-toggle').click()")


def test_live_card_in_dry_run_is_read_only_and_says_where_live_is_armed(probe):
    _seed([_captured(NAME_PARENS_PLUS)])
    _open_master_controls(probe, "DRY RUN")
    card = probe.page.evaluate(_LIVE_CARD_JS)
    assert card["disabled"] is True, "the live toggle must be disabled"
    assert card["readonly"] == "true", "the live toggle must carry aria-readonly"
    _assert_live_card_statement(card)

    _click_live_toggle_every_way(probe)

    after = probe.page.evaluate(_LIVE_CARD_JS)
    assert after["pill"] == "DRY RUN" and after["pillLive"] == "false"
    assert after["toggleOn"] == "0" and after["ariaChecked"] == "false"
    assert after["cardLive"] == "false" and after["bannerHidden"] is True
    assert after["saveDisabled"] is True, "a click on the read-only toggle must mark nothing dirty"
    assert probe.writes == []


def test_live_card_in_live_mode_reads_live_read_only_with_the_same_statement(probe, env_file):
    _write_env(env_file, live="True")
    _seed([_captured(NAME_PARENS_PLUS)])
    _open_master_controls(probe, "LIVE")
    card = probe.page.evaluate(_LIVE_CARD_JS)
    assert card["pillLive"] == "true" and card["cardLive"] == "true"
    assert card["bannerHidden"] is False
    assert card["disabled"] is True and card["readonly"] == "true"
    _assert_live_card_statement(card)

    _click_live_toggle_every_way(probe)

    after = probe.page.evaluate(_LIVE_CARD_JS)
    assert after["pill"] == "LIVE" and after["toggleOn"] == "1"
    assert after["saveDisabled"] is True
    assert probe.writes == []


# ---------------------------------------------------------------------------
# Save refuses a value that is not a number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "1abc", "0x10", "1,5"])
def test_save_names_the_field_and_sends_nothing_for_a_value_that_is_not_a_number(
    probe, env_file, bad
):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    env_before = env_file.read_bytes()
    stored_before = _stored(NAME_PARENS_PLUS)
    open_settings(probe, 1)
    select_symphony(probe, _norm(NAME_PARENS_PLUS))
    field = PARAM_KEYS[2]
    probe.page.locator(f'#sym-detail .override-row[data-key="{field}"] .override-input').fill(bad)

    probe.page.click("#btn-save")

    message = _wait_dialog(probe, f"the value {bad!r}")
    assert field in message, f"the alert must name the field: {message!r}"
    assert "not a number" in message.lower(), message
    assert NAME_PARENS_PLUS in message or _norm(NAME_PARENS_PLUS) in message, (
        f"the alert must name the symphony: {message!r}"
    )
    assert probe.writes == [], f"nothing may be sent, saw {[w.url for w in probe.writes]}"
    assert env_file.read_bytes() == env_before, "the globals in .env must be unchanged"
    assert _stored(NAME_PARENS_PLUS) == stored_before


def test_save_body_has_the_documented_shape_and_never_a_live_execution_key(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    _click_live_toggle_every_way(probe)
    edited = "TAKE_PROFIT_MC_PCT"
    probe.page.locator(f'#sym-detail .override-row[data-key="{edited}"] .override-input').fill(
        "12.5"
    )

    response, body = click_save_and_capture(probe)

    assert response.status == 200, "the page must not send a key the route rejects"
    assert set(body) == {"globals", "symphonies"}
    assert "LIVE_EXECUTION" not in body["globals"]
    assert body["globals"] == {}
    assert set(body["symphonies"]) == {norm}
    sent = body["symphonies"][norm]
    assert set(sent) == {"params", "locked_vars"}
    assert all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in sent["params"].values()
    )
    assert sent["params"][edited] == 12.5
    assert all(isinstance(v, str) for v in sent["locked_vars"])


# ---------------------------------------------------------------------------
# Override counts
# ---------------------------------------------------------------------------


def test_list_entry_counts_only_differing_values_and_states_the_pinned_ones(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: (_overridden_params(), list(STORED_LOCKS))})
    payload = _json_get(probe.server, "/api/settings")
    norm = _norm(NAME_PARENS_PLUS)
    params = payload["symphony_overrides"][norm]["params"]
    differing = reference_override_count(params, payload["globals"])
    assert 0 < differing < len(params), "the record must mix differing and equal values"

    open_settings(probe, 1)

    text = entry_text(probe, norm)
    assert overrides_label(differing) in text, text
    assert f"{len(params) - differing} pinned at the global value" in text, text
    assert "uses globals" not in text


def test_list_entry_for_an_untouched_default_row_reads_zero_overrides_all_pinned(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)])  # no strategy row: the settings route writes the default row
    norm = _norm(NAME_PARENS_PLUS)
    payload = _json_get(probe.server, "/api/settings")
    params = payload["symphony_overrides"][norm]["params"]
    assert reference_override_count(params, payload["globals"]) == 0
    locked = payload["symphony_overrides"][norm]["locked_vars"]

    open_settings(probe, 1)

    text = entry_text(probe, norm)
    assert "0 overrides" in text, text
    assert f"{len(params)} pinned at the global value" in text, text
    assert f"{len(locked)} locked" in text, "the lock count must still show"
    tooltips = " ".join(entry_tooltips(probe, norm)).lower()
    assert "will not follow a later global change" in tooltips, entry_tooltips(probe, norm)


def test_list_entry_for_a_symphony_storing_no_parameters_reads_uses_globals(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    _seed([(key, record)], {NAME_PARENS_PLUS: ({}, [])})
    open_settings(probe, 1)
    text = entry_text(probe, _norm(NAME_PARENS_PLUS))
    assert "uses globals" in text, text
    assert "pinned" not in text and "override" not in text


def test_detail_marks_only_the_row_whose_value_differs(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    differing = OVERRIDDEN_KEYS[0]
    _seed(
        [(key, record)],
        {NAME_PARENS_PLUS: (_overridden_params(keys=(differing,)), list(STORED_LOCKS))},
    )
    open_settings(probe, 1)
    select_symphony(probe, _norm(NAME_PARENS_PLUS))
    assert detail_state(probe)["marked"] == [differing]


def test_typing_the_global_value_into_an_empty_override_does_not_change_the_count(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    missing = "MAX_SQUEEZE_FLOOR"
    params = {k: v for k, v in _overridden_params().items() if k != missing}
    _seed([(key, record)], {NAME_PARENS_PLUS: (params, list(STORED_LOCKS))})
    norm = _norm(NAME_PARENS_PLUS)
    globals_payload = _json_get(probe.server, "/api/settings")["globals"]
    open_settings(probe, 1)
    select_symphony(probe, norm)
    before = entry_text(probe, norm)
    assert overrides_label(len(OVERRIDDEN_KEYS)) in before, before
    field = probe.page.locator(f'#sym-detail .override-row[data-key="{missing}"] .override-input')
    field.evaluate("el => { el.dataset.sameElement = '1'; }")

    field.fill(str(globals_payload[missing]))

    after = entry_text(probe, norm)
    assert overrides_label(len(OVERRIDDEN_KEYS)) in after, after
    assert field.evaluate("el => el.dataset.sameElement") == "1", (
        "typing must refresh only the list entry, never rebuild the detail under the cursor"
    )


def test_typing_a_differing_value_updates_the_list_entry_count_live(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    missing = "MAX_SQUEEZE_FLOOR"
    params = {k: v for k, v in _overridden_params().items() if k != missing}
    _seed([(key, record)], {NAME_PARENS_PLUS: (params, list(STORED_LOCKS))})
    norm = _norm(NAME_PARENS_PLUS)
    open_settings(probe, 1)
    select_symphony(probe, norm)
    field = probe.page.locator(f'#sym-detail .override-row[data-key="{missing}"] .override-input')

    field.fill("9.75")
    assert overrides_label(len(OVERRIDDEN_KEYS) + 1) in entry_text(probe, norm)
    field.fill("")
    assert overrides_label(len(OVERRIDDEN_KEYS)) in entry_text(probe, norm)


def test_counting_changes_neither_the_stored_row_nor_the_body_save_sends(probe):
    key, record = _captured(NAME_PARENS_PLUS)
    stored = _overridden_params()
    _seed([(key, record)], {NAME_PARENS_PLUS: (stored, list(STORED_LOCKS))})
    row_before = _stored(NAME_PARENS_PLUS)
    open_settings(probe, 1)
    norm = _norm(NAME_PARENS_PLUS)
    select_symphony(probe, norm)
    assert _stored(NAME_PARENS_PLUS) == row_before, "rendering the list must not write the row"
    pinned_key = next(k for k in PARAM_KEYS if k not in OVERRIDDEN_KEYS)
    shown = detail_state(probe)["inputs"][pinned_key]
    probe.page.locator(f'#sym-detail .override-row[data-key="{pinned_key}"] .override-input').fill(
        shown
    )

    _response, body = click_save_and_capture(probe)

    assert body["symphonies"][norm]["params"] == {k: float(v) for k, v in stored.items()}, (
        "values equal to the global are pinned, so they stay in the saved body"
    )
    assert _stored(NAME_PARENS_PLUS)["params"] == {k: float(v) for k, v in stored.items()}


# ---------------------------------------------------------------------------
# Detail header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [NAME_PARENS_PLUS, NAME_TRAILING_SPACE, NAME_APOSTROPHE])
def test_detail_header_shows_the_display_name_and_the_key_only_in_a_title_attribute(probe, name):
    key, record = _captured(name)
    _seed([(key, record)], {name: (_overridden_params(), list(STORED_LOCKS))})
    norm = _norm(name)
    open_settings(probe, 1)
    select_symphony(probe, norm)

    header = probe.page.evaluate(
        """(k) => {
             const detail = document.getElementById('sym-detail');
             const name = detail.querySelector('.sym-detail-name');
             const hits = [];
             detail.querySelectorAll('*').forEach((el) => {
               Array.from(el.attributes).forEach((a) => {
                 if (a.value.includes(k)) hits.push(a.name);
               });
             });
             return {
               name: name ? name.innerText : null,
               text: detail.innerText,
               attributesWithKey: hits,
             };
           }""",
        norm,
    )
    assert header["name"] is not None and header["name"].strip() == name.strip()
    assert norm not in header["text"], "the internal key must not be visible text"
    assert set(header["attributesWithKey"]) <= {"title"}, (
        f"the key may appear only in a title attribute, found in {header['attributesWithKey']}"
    )
    assert header["attributesWithKey"].count("title") <= 1, "at most once"
