"""What the pages must not change, read through the Flask test client and the source tree.

Every comparison is against ``tests/fixtures/page_explainers/ref_capture.json``, written once at
the reference commit by ``produce_ref_capture.py`` through the same readers this module calls
(``tests/ui/_page_explainers.py``); no figure, banner or body is typed here. The seeded worlds
are the real writers' output over captured records, with the app clock and the CSRF token fixed.

Covered here (no browser): the risk and honesty banners byte for byte (S-5.3), every host's
section order (S-5.5), the bodies of the seven read-only routes (S-5.6), the login and logout
pages and one Discord payload (S-5.9), the content pins of the engine files and ``database.py``
(S-5.13), the route table, the scheduler slots and the migrations (S-5.7), and the buttons no
script handles (S-5.15). A git-history guard is never used for these: CI clones at depth one and
such a guard would pass vacuously, so each pin is a content hash or a recorded structure.

The browser readings (S-5.1, S-5.2) are in ``test_page_explainers_unchanged_browser.py``.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from bs4 import BeautifulSoup

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (the two overrides and the empty directory)
    PAGES,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

REF_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "page_explainers"
    / ("ref_capture.json")
)


@pytest.fixture(scope="module")
def ref() -> dict:
    if not REF_PATH.is_file():
        pytest.fail(f"no reference capture at {REF_PATH}: the producer has not been run")
    return json.loads(REF_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """``world(name)`` seeds that state and returns a client on the app; no credential, no .env."""
    import app as app_module

    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )

    def make(name: str):
        harness.seed_world(name, tmp_path, monkeypatch.setattr)
        return app_module.app.test_client()

    return make


def _differences(label: str, got, want) -> list[str]:
    return (
        []
        if got == want
        else [f"{label}: page {str(got)[:240]!r} != reference {str(want)[:240]!r}"]
    )


# ---------------------------------------------------------------------------
# The reference capture itself
# ---------------------------------------------------------------------------


def test_the_reference_capture_holds_every_world_host_banner_and_route(ref):
    assert ref["reference_commit"] == harness.REFERENCE_COMMIT
    assert set(ref["worlds"]) == set(harness.WORLDS)
    for name, record in ref["worlds"].items():
        assert set(record["hosts"]) == set(harness.WORLD_HOSTS[name]), name
        assert set(record["headings"]) == set(PAGES), name
        assert set(record["banners"]) == set(harness.BANNER_SELECTORS), name
        assert set(record["routes"]) == set(harness.ROUTE_PATHS), name
        for host, reading in record["hosts"].items():
            assert reading["figures_1440"], f"{name} {host}: the capture holds no figure element"
            assert reading["controls_1440"], f"{name} {host}: the capture holds no control"
    full = ref["worlds"]["full"]
    for key, found in full["banners"].items():
        assert found, f"banner {key} is absent from the fullest world, so nothing pins it"
    assert ref["kept_rows"].keys() == harness.KEPT_ROW_LOCATORS.keys()
    assert ref["discord_payload"] and ref["auth_pages"]["/login"] and ref["auth_pages"]["/logout"]


# ---------------------------------------------------------------------------
# S-5.3: the risk banners
# ---------------------------------------------------------------------------


# A banner whose words a Copy table C row rewords keeps its element and its attributes; only the
# text changes, to that row's new text. (The Credentials warning is the one such banner: its row
# says the dashboard does not save credentials.)
REWORDED_BANNERS = {"settings_credential_warning": "S12r"}


def _reworded_banner_differences(
    label: str, got: list[str], want: list[str], row: str
) -> list[str]:
    def opening_tag(markup: str) -> str:
        return re.match(r"<[^>]*>", markup).group(0)

    def text(markup: str) -> str:
        return " ".join(BeautifulSoup(markup, "html.parser").get_text().split())

    problems = _differences(
        f"{label} element", [opening_tag(m) for m in got], [opening_tag(m) for m in want]
    )
    expected = " ".join(copy.text(row, "C").split())
    return problems + _differences(f"{label} text", [text(m) for m in got], [expected] * len(want))


@pytest.mark.parametrize("name", harness.WORLDS)
def test_every_risk_banner_is_byte_identical_to_the_reference(world, ref, name):
    client = world(name)
    pages = {host: client.get(host).get_data(as_text=True) for host in PAGES}
    problems: list[str] = []
    for key, (host, selector) in harness.BANNER_SELECTORS.items():
        got = harness.banner_markup(pages[host], selector)
        want = ref["worlds"][name]["banners"][key]
        if key in REWORDED_BANNERS:
            problems += _reworded_banner_differences(
                f"{name} {key}", got, want, REWORDED_BANNERS[key]
            )
        else:
            problems += _differences(f"{name} {key}", got, want)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# S-5.5: the order of each host's sections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", harness.WORLDS)
def test_every_host_lists_its_sections_in_the_reference_order(world, ref, name):
    client = world(name)
    problems: list[str] = []
    for host in PAGES:
        got = harness.section_headings(client.get(host).get_data(as_text=True))
        want = ref["worlds"][name]["headings"][host]
        # A section may be added; every reference section stays, in its reference order.
        remaining = iter(got)
        missing = [h for h in want if h not in remaining]
        if missing:
            problems.append(f"{name} {host}: lost or reordered {missing}; page has {got}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# S-5.6: the read-only routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", harness.WORLDS)
def test_the_seven_route_bodies_equal_the_reference(world, ref, name):
    client = world(name)
    got = harness.read_route_bodies(client)
    problems: list[str] = []
    for path in harness.ROUTE_PATHS:
        want = ref["worlds"][name]["routes"][path]
        volatile = sorted(set(want["volatile"]) | set(got[path]["volatile"]))
        problems += _differences(f"{name} {path} status", got[path]["status"], want["status"])
        problems += _differences(
            f"{name} {path} body",
            harness.strip_volatile(got[path]["body"], volatile),
            harness.strip_volatile(want["body"], volatile),
        )
    assert not problems, "\n".join(problems)


def test_a_route_body_comparison_fails_on_a_changed_body(ref):
    """The comparison can fail: one key changed in a recorded body is reported."""
    want = ref["worlds"]["full"]["routes"]["/api/state"]["body"]
    assert isinstance(want, dict) and want
    key = next(iter(want))
    changed = {**want, key: "changed"}
    assert _differences("x", changed, want)


# ---------------------------------------------------------------------------
# S-5.9: the login and logout pages and a Discord post
# ---------------------------------------------------------------------------


def test_the_login_and_logout_pages_are_byte_identical_to_the_reference(world, ref):
    client = world("empty")
    got = harness.read_auth_pages(client)
    assert got == ref["auth_pages"]


def test_the_discord_payload_over_the_captured_day_equals_the_reference(
    world, ref, tmp_path, monkeypatch
):
    world("full")  # the digest embed reads the stored sleeves
    got = harness.read_discord_payload(tmp_path, monkeypatch.setattr)
    assert got == ref["discord_payload"]


# ---------------------------------------------------------------------------
# S-5.13 and S-5.7: content pins, never git history
# ---------------------------------------------------------------------------


def test_the_engine_files_and_the_database_module_equal_the_recorded_content(ref):
    """SHA-256 of each file with CRLF normalized to LF. The engine files are pinned at the
    reference commit and ``database.py`` at the build base (the merged security head adds lines to
    it). Stop and ask before re-pinning any of them."""
    pins = ref["pins"]
    hashes = harness.working_tree_hashes()
    assert (
        hashes["alpha_bot_execution.py"]
        == pins["engine_hashes_at_reference"]["alpha_bot_execution.py"]
    )
    assert hashes["math_engine.py"] == pins["engine_hashes_at_reference"]["math_engine.py"]
    assert hashes["database.py"] == pins["database_hash_at_build_base"]


def test_the_migrations_equal_the_listing_at_the_build_base(ref):
    assert harness.migration_listing() == ref["pins"]["migrations_at_build_base"]


def test_every_route_keeps_its_rule_methods_and_endpoint(ref):
    assert harness.route_table_facts() == ref["pins"]["route_table"]


def test_the_scheduler_keeps_every_slot_and_call(ref):
    assert harness.scheduler_slot_digest() == ref["pins"]["scheduler_digest"]


# ---------------------------------------------------------------------------
# S-5.15: no other control without a handler
# ---------------------------------------------------------------------------


def test_no_rendered_button_without_a_handler_is_added_to_the_reference_set(ref):
    reference = ref["pins"]["handlerless_buttons"]
    removed = [
        row
        for row in reference
        if any(row[2].startswith(text) for text in harness.REMOVED_CONTROL_TEXTS)
    ]
    assert len(removed) == len(harness.REMOVED_CONTROL_TEXTS), (
        f"the reference should hold the three controls AC-5 excepts: {removed}"
    )
    got = harness.handlerless_buttons()
    new = [row for row in got if row not in reference]
    assert not new, f"a rendered button no script handles was added: {new}"


def test_the_clock_mask_hides_a_stamp_and_a_countdown_and_nothing_else():
    mask = harness.mask_clock_text
    assert mask("As of 2026-11-06 13:34 ET") == f"As of {harness.WALL_CLOCK_TEXT_MASK}"
    assert mask("Next 00:58") == harness.WALL_CLOCK_TEXT_MASK
    assert mask("run 2026-10-22T18:50:08Z ok") == f"run {harness.WALL_CLOCK_TEXT_MASK} ok"
    for kept in ("2026-06-30", "Force run now", "+1.2%", "12:00 pm"):
        assert mask(kept) == kept
