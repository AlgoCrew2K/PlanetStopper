"""A panel's first heading takes its zero top margin from one shared rule, and no page changes.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n0 tests/ui/test_heading_margin_shared_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

This is a refactor-equivalence check, not a value assertion. The four pages that carried the
rule inline (Advisor, History, Performance, Settings) now get it from ``static/layout.css``, and
the claim is that nothing a page renders moved. The head's computed style of every element the
heading rule can reach is compared with the same reading taken at the base commit, by the same
code (``tests/ui/_heading_margin_capture.py``): equal element sequence, equal four margins, equal
digest of the rest of the computed style, on the same Firefox version. No pixel value is typed
here; the baseline is ``tests/fixtures/dashboard/heading_margin_baseline/baseline.json``
(provenance beside it).

Dashboard and Login are the guard that adding the rule to the sheet every page links moved
nothing on a page that does not use it. Login is read whole.

The server is the app in this process on the per-test database, seeded through the real writers
from captured records; the app clock and the browser clock are fixed at one instant, and every
request to anything but the server is refused.
"""

from __future__ import annotations

import json

import pytest

from tests.ui import _heading_margin_capture as capture
from tests.ui._shell_support import (  # noqa: F401
    REPO,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
)

pytestmark = pytest.mark.browser

BASELINE = json.loads(
    (
        REPO / "tests" / "fixtures" / "dashboard" / "heading_margin_baseline" / "baseline.json"
    ).read_text(encoding="utf-8")
)
_RECAPTURE = (
    "recapture the baseline from a throwaway worktree at the base commit with "
    "tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py"
)

_PAGES_WITH_THE_RULE = ("history", "performance", "ai-advisor", "settings")
_PAGES_WITHOUT_THE_RULE = ("dashboard", "login")
_HEAD_PANEL_PAGES = ("history", "performance")


# ---------------------------------------------------------------------------
# Comparing two readings
# ---------------------------------------------------------------------------


def differences(expected: dict[str, list], actual: dict[str, list]) -> list[str]:
    """Every way ``actual`` differs from ``expected``, one line each (empty when equal)."""
    found: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            found.append(f"{key}: the head shows no such view")
            continue
        if key not in expected:
            found.append(f"{key}: the baseline holds no such view")
            continue
        want, got = expected[key], actual[key]
        if len(want) != len(got):
            found.append(f"{key}: {len(want)} elements at the base, {len(got)} at the head")
            continue
        for index, (a, b) in enumerate(zip(want, got, strict=True)):
            who = f"{key}[{index}] <{a['tag']} class='{' '.join(a['classes'])}'> {a['text']!r}"
            for field in ("tag", "classes", "parent_classes", "text"):
                if a[field] != b[field]:
                    found.append(f"{who}: {field} {a[field]!r} became {b[field]!r}")
            if a["margins"] != b["margins"]:
                found.append(f"{who}: margins {a['margins']} became {b['margins']}")
            if a["style_digest"] != b["style_digest"]:
                found.append(f"{who}: the computed style changed (digest differs)")
    return found


@pytest.fixture
def world(advisor_server, tmp_path, monkeypatch):
    """The server over the seeded state, both clocks fixed (the browser's at capture time)."""
    capture.prepare(tmp_path, monkeypatch.setattr)
    return advisor_server


def assert_group_unchanged(firefox, server, page_id: str, width_key: str, theme: str) -> dict:
    """Capture one group at the head and compare it with the baseline; return the capture."""
    assert BASELINE["browser_version"] == firefox.version, (
        f"the baseline was read with Firefox {BASELINE['browser_version']} and this run has "
        f"{firefox.version}, so the render is not comparable: {_RECAPTURE}"
    )
    actual = capture.capture_group(firefox, server, page_id, width_key, theme)
    expected = capture.views_of_group(BASELINE["views"], page_id, width_key, theme)
    assert expected, f"the baseline holds no view of {page_id} at {width_key} {theme}"
    found = differences(expected, actual)
    assert found == [], "\n".join(found)
    return actual


# ---------------------------------------------------------------------------
# The baseline is usable, and the comparison can fail
# ---------------------------------------------------------------------------


class TestBaselineCanFail:
    def test_the_baseline_holds_a_view_for_every_page_width_and_theme(self):
        missing = [
            group
            for group in capture.groups()
            if not capture.views_of_group(BASELINE["views"], *group)
        ]
        assert missing == []

    def test_the_advisor_and_settings_are_read_once_per_tab_and_per_section(self):
        for page_id in ("ai-advisor", "settings"):
            views = capture.views_of_group(BASELINE["views"], page_id, "1440", "light")
            assert len(views) > 1, f"{page_id} was read as one view, so its switching never ran"

    @pytest.mark.parametrize("page_id", _PAGES_WITH_THE_RULE)
    def test_the_pages_that_carried_the_rule_show_a_heading_it_reaches(self, page_id):
        views = capture.views_of_group(BASELINE["views"], page_id, "1440", "light")
        reached = [e for es in views.values() for e in es if e["shared_rule_matches"]]
        assert reached, f"the baseline of {page_id} holds no heading the shared rule matches"

    @pytest.mark.parametrize("page_id", _HEAD_PANEL_PAGES)
    def test_history_and_performance_hold_the_heading_inside_the_panel_head(self, page_id):
        views = capture.views_of_group(BASELINE["views"], page_id, "1440", "light")
        inside = [e for es in views.values() for e in es if _is_panel_head_heading(e)]
        assert inside, f"the baseline of {page_id} holds no .section-header inside .hp-head"

    def test_login_is_read_whole_and_holds_no_heading_the_rule_reaches(self):
        views = capture.views_of_group(BASELINE["views"], "login", "1440", "light")
        elements = [e for es in views.values() for e in es]
        assert elements, "the baseline holds no element of /login"
        assert not [e for e in elements if e["shared_rule_matches"]]

    def test_a_changed_margin_digest_or_element_count_is_seen(self):
        element = {
            "tag": "h2",
            "classes": ["section-header"],
            "parent_classes": ["hero-section"],
            "shared_rule_matches": True,
            "text": "Heading",
            "margins": {"margin-top": "0px", "margin-bottom": "12px"},
            "style_digest": "a" * 64,
        }
        base = {"v": [element]}
        assert differences(base, {"v": [dict(element)]}) == []
        moved = dict(element, margins={"margin-top": "28px", "margin-bottom": "12px"})
        assert any("margins" in line for line in differences(base, {"v": [moved]}))
        restyled = dict(element, style_digest="b" * 64)
        assert any("digest" in line for line in differences(base, {"v": [restyled]}))
        assert any("elements" in line for line in differences(base, {"v": [element, element]}))
        assert any("no such view" in line for line in differences(base, {}))


def _is_panel_head_heading(element: dict) -> bool:
    return "section-header" in element["classes"] and "hp-head" in element["parent_classes"]


# ---------------------------------------------------------------------------
# The head against the base
# ---------------------------------------------------------------------------


def test_history_keeps_every_computed_style_at_1440_in_the_light_theme(firefox, world):
    assert_group_unchanged(firefox, world, "history", "1440", "light")


def test_performance_keeps_every_computed_style_at_1440_in_the_light_theme(firefox, world):
    assert_group_unchanged(firefox, world, "performance", "1440", "light")


def test_every_advisor_tab_keeps_every_computed_style_at_1440_in_the_light_theme(firefox, world):
    actual = assert_group_unchanged(firefox, world, "ai-advisor", "1440", "light")
    assert len(actual) > 1, "the Advisor was read as one view"


def test_every_settings_section_keeps_every_computed_style_at_1440_in_the_light_theme(
    firefox, world
):
    actual = assert_group_unchanged(firefox, world, "settings", "1440", "light")
    assert len(actual) > 1, "Settings was read as one view"


@pytest.mark.parametrize("theme", capture.THEMES)
@pytest.mark.parametrize("width_key", list(capture.WIDTHS))
@pytest.mark.parametrize("page_id", _PAGES_WITHOUT_THE_RULE)
def test_the_dashboard_and_login_keep_every_computed_style(
    firefox, world, page_id, width_key, theme
):
    assert_group_unchanged(firefox, world, page_id, width_key, theme)


@pytest.mark.parametrize("page_id", _HEAD_PANEL_PAGES)
def test_the_heading_inside_the_panel_head_keeps_its_margins_and_style(firefox, world, page_id):
    """The ``.section-header`` inside ``.hp-head`` sits one level below the panel, where the
    shared rule's child selector must not reach it, so its margins stay what they were."""
    actual = capture.capture_group(firefox, world, page_id, "1440", "light")
    key = capture.view_key(page_id, "load", "1440", "light")
    want = [e for e in BASELINE["views"][key] if _is_panel_head_heading(e)]
    got = [e for e in actual[key] if _is_panel_head_heading(e)]
    assert want, f"the baseline of {page_id} holds no heading inside .hp-head"
    assert differences({key: want}, {key: got}) == []


@pytest.mark.parametrize(
    "width_key, theme",
    [("375", "light"), ("375", "dark"), ("1440", "dark")],
    ids=["375-light", "375-dark", "1440-dark"],
)
@pytest.mark.parametrize("page_id", _PAGES_WITH_THE_RULE)
def test_the_four_pages_keep_every_computed_style_at_375_and_in_the_dark_theme(
    firefox, world, page_id, width_key, theme
):
    assert_group_unchanged(firefox, world, page_id, width_key, theme)
