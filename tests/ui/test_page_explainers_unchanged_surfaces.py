"""What the pages must not change, read through the Flask test client and the source tree.

Every comparison is against ``tests/fixtures/page_explainers/ref_capture.json``, written once at
the reference commit (``c0760e70``, the base this change builds on) by ``produce_ref_capture.py``
through the same readers this module calls (``tests/ui/_page_explainers.py``); no figure, banner or
body is typed here. The seeded worlds are the real writers' output over captured records, with the
app clock and the CSRF token fixed.

Covered here (no browser): the risk and honesty banners byte for byte, every host's section order,
the bodies of the read-only routes (the health route included), the login and logout pages and one
Discord payload, the content pins of the engine files, ``database.py``, ``advisors/tenure_review.py``
and the migrations, the content pins of the no-trade-boundary test files, the route table, the
scheduler slots, and the buttons no script handles. A git-history guard is never used for these: CI
clones at depth one and such a guard would pass vacuously, so each pin is a content hash or a
recorded structure. A pin is never re-pinned by the cycle that trips it: stop and ask.

The browser readings (figures, controls and table forms) are in
``test_page_explainers_unchanged_browser.py``.
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import re

import pytest
from bs4 import BeautifulSoup

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as page_copy
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
    # analytics sums the seeded post-mortem days in the order the filesystem lists them, and a float
    # sum depends on its order: the reference was captured on a host that lists them by name, so a
    # host that lists them otherwise (Linux CI) differs in the last digits of the totals. The test
    # fixes the one input the code does not: the listing is by name.
    real_glob = glob.glob

    def post_mortem_days_by_name(pattern, *args, **kwargs):
        found = real_glob(pattern, *args, **kwargs)
        return sorted(found) if "post_mortem_" in os.fspath(pattern) else found

    monkeypatch.setattr(glob, "glob", post_mortem_days_by_name)

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
            for width, _height in harness.INVENTORY_WIDTHS:
                assert reading[f"figures_{width}"], f"{name} {host} {width}: no figure element"
                assert reading[f"controls_{width}"], f"{name} {host} {width}: no control"
            for width, _height in harness.FORM_WIDTHS:
                forms = reading[f"stacked_forms_{width}"]
                assert set(forms) == {g["name"] for g in al.stacked_form_groups()}, (name, host)
    full = ref["worlds"]["full"]
    for key, found in full["banners"].items():
        assert found, f"banner {key} is absent from the fullest world, so nothing pins it"
    assert ref["kept_rows"].keys() == harness.KEPT_ROW_LOCATORS.keys()
    assert ref["discord_payload"] and ref["auth_pages"]["/login"] and ref["auth_pages"]["/logout"]
    recorded_forms = {
        name
        for record in ref["worlds"].values()
        for reading in record["hosts"].values()
        for width, _height in harness.FORM_WIDTHS
        for name, found in reading[f"stacked_forms_{width}"].items()
        if found["count"]
    }
    assert recorded_forms >= {
        "risk_metrics",
        "preconditions",
        "exit_turnover",
        "exit_reasons",
        "exits",
        "autotune_runs",
        "observation_histories",
    }, f"the capture holds the stacked form of only {sorted(recorded_forms)}"


# ---------------------------------------------------------------------------
# The risk banners
# ---------------------------------------------------------------------------


# A banner whose words a Copy table C row rewords keeps its element and its attributes; only the
# text changes, to that row's new text. (The Credentials warning is the one such banner: its row
# says the dashboard does not save credentials.)
# The Strategy Builder banner is the other: this plan shortens it (Copy table X, CX-9), so its
# element and attributes are the reference's and its text is that row's.
REWORDED_BANNERS = {"settings_credential_warning": "S12r", "strategy_builder_risk": "CX-9"}


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
    if row.startswith("CX-"):
        expected = " ".join(page_copy.x_row(row)["new_text"].split())
    else:
        expected = " ".join(page_copy.text(row, "C").split())
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
# The order of each host's sections
# ---------------------------------------------------------------------------


def _renamed_headings() -> dict[str, str]:
    """Reference heading to its new text, for each Copy table C row that rewords a heading.

    A row names old and new texts in step (joined by "; "); a heading is reworded when it equals
    one of the old texts, so a cycle that adds a heading rename needs no edit here.
    """
    renamed: dict[str, str] = {}
    for record in page_copy.rows("C"):
        old, new = record.get("old_text") or "", record.get("new_text") or ""
        olds, news = old.split("; "), new.split("; ")
        if len(olds) == len(news):
            renamed.update(dict(zip(olds, news, strict=True)))
    return renamed


_RENAMED_HEADINGS = _renamed_headings()


@pytest.mark.parametrize("name", harness.WORLDS)
def test_every_host_lists_its_sections_in_the_reference_order(world, ref, name):
    client = world(name)
    problems: list[str] = []
    for host in PAGES:
        got = harness.section_headings(client.get(host).get_data(as_text=True))
        want = [_RENAMED_HEADINGS.get(h, h) for h in ref["worlds"][name]["headings"][host]]
        # A section may be added; every reference section stays, in its reference order.
        remaining = iter(got)
        missing = [h for h in want if h not in remaining]
        if missing:
            problems.append(f"{name} {host}: lost or reordered {missing}; page has {got}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# The read-only routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", harness.WORLDS)
def test_every_read_only_route_body_equals_the_reference(world, ref, name):
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


def test_the_health_route_is_among_the_routes_whose_bodies_are_pinned(ref):
    assert "/health" in harness.ROUTE_PATHS
    body = ref["worlds"]["full"]["routes"]["/health"]
    assert body["status"] == 200 and body["body"].get("status") == "ok", body


def test_a_route_body_comparison_fails_on_a_changed_body(ref):
    """The comparison can fail: one key changed in a recorded body is reported."""
    want = ref["worlds"]["full"]["routes"]["/api/state"]["body"]
    assert isinstance(want, dict) and want
    key = next(iter(want))
    changed = {**want, key: "changed"}
    assert _differences("x", changed, want)


# ---------------------------------------------------------------------------
# The login and logout pages and a Discord post
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
# Content pins, never git history
# ---------------------------------------------------------------------------


def test_the_engine_database_and_tenure_review_files_equal_the_recorded_content(ref):
    """SHA-256 of each file with CRLF normalized to LF, as it is at the reference commit. Stop and
    ask before re-pinning any of them."""
    recorded = ref["pins"]["hashes_at_reference"]
    assert set(recorded) == set(harness.PINNED_FILES)
    assert harness.working_tree_hashes() == recorded


def test_the_migrations_equal_the_listing_at_the_reference(ref):
    assert harness.migration_listing() == ref["pins"]["migrations_at_reference"]


def test_the_no_trade_boundary_tests_equal_the_recorded_content(ref):
    """The dashboard no-order-path test and every no-trade-boundary scan are byte-for-byte what
    they were: a change that needed one of them to change would be a change to the boundary."""
    recorded = ref["pins"]["protected_test_hashes_at_reference"]
    assert "tests/app/test_dashboard_no_order_path.py" in recorded
    boundary = [name for name in recorded if "no_trade_boundary" in name]
    assert len(boundary) >= 5, f"the reference records only {boundary}"
    missing = [name for name in recorded if not (harness.REPO / name).is_file()]
    assert not missing, f"a protected test file is gone: {missing}"
    now = harness.protected_test_hashes()
    changed = [name for name, digest in recorded.items() if now.get(name) != digest]
    assert not changed, f"a protected test file changed: {changed}"


def test_the_content_hash_ignores_line_endings_and_sees_every_other_change():
    digest = harness._lf_sha256
    assert digest(b"a\r\nb\r\n") == digest(b"a\nb\n"), "CRLF must not change the pin"
    assert digest(b"a\nb\n") != digest(b"a\nc\n")
    assert digest(b"a\nb\n") != digest(b"a\nb\n\n"), "an added line must change the pin"


def test_the_pinned_set_is_the_four_protected_files_and_the_boundary_tests_are_found():
    assert harness.PINNED_FILES == (
        "alpha_bot_execution.py",
        "math_engine.py",
        "database.py",
        "advisors/tenure_review.py",
    )
    found = harness.protected_test_files()
    assert found[0] == "tests/app/test_dashboard_no_order_path.py"
    assert sum("no_trade_boundary" in name for name in found) >= 5


def test_every_route_keeps_its_rule_methods_and_endpoint(ref):
    assert harness.route_table_facts() == ref["pins"]["route_table"]


def test_the_scheduler_keeps_every_slot_and_call(ref):
    assert harness.scheduler_slot_digest() == ref["pins"]["scheduler_digest"]


# ---------------------------------------------------------------------------
# No other control without a handler
# ---------------------------------------------------------------------------


def test_no_rendered_button_without_a_handler_is_added_to_the_reference_set(ref):
    reference = ref["pins"]["handlerless_buttons"]
    removed = [
        row
        for row in reference
        if any(row[2].startswith(text) for text in harness.REMOVED_CONTROL_TEXTS)
    ]
    assert removed == [], f"a control an earlier change removed is back in the reference: {removed}"
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
