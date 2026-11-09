"""Every figure and control the five pages showed at the reference is still there, unchanged.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_unchanged_browser.py``).
Firefox headless through Playwright, imported inside a fixture; a missing Playwright or Firefox
fails with a message naming it, never a skip.

Each case is one seeded world (``tests/ui/_page_explainers.py``) served by the app in this process
and read at one host through the same function the reference capture used
(``read_host_in_browser``). The record it makes is compared with the one
``tests/fixtures/page_explainers/ref_capture.json`` holds for the same world and host. Nothing is
typed here.

* S-5.1: the text of every figure element (a leaf element whose ``data-testid`` or ``id`` names it)
  is identical; the elements Copy table C rewords are excluded by the old text each row gives.
* S-5.2: every control the reference listed at 1440 and at 375 is still listed, with the same
  type, label, link, disabled state and visibility. The three controls AC-5 excepts are not
  required (the cycle that removes them pins their absence).

The requests each control sends are pinned by ``tests/ui/test_post_controls_request_bytes.py``,
which this change leaves as it is.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
)

pytestmark = pytest.mark.browser

REF_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "page_explainers"
    / ("ref_capture.json")
)
SHORT_OLD_TEXT = 30
MIN_LITERAL = 4


@pytest.fixture(scope="module")
def ref() -> dict:
    if not REF_PATH.is_file():
        pytest.fail(f"no reference capture at {REF_PATH}: the producer has not been run")
    return json.loads(REF_PATH.read_text(encoding="utf-8"))


def _relabel_matchers() -> list[re.Pattern]:
    """One pattern per old text Copy table C gives, each ``{slot}`` standing for any run of text.

    A short old text must be a whole element's text; a long one may sit inside it. A row whose old
    text is a pointer to a source line gives no text to match and is skipped.
    """
    matchers = []
    for record in copy.rows("C"):
        old = record["old_text"]
        if " at `" in old or old.startswith("the ") or "with A05" in old:
            continue
        for alternative in old.split("; "):
            alternative = alternative.strip()
            literal = re.sub(r"\{[a-z_]+\}", "", alternative).strip()
            if len(literal) < MIN_LITERAL:
                continue  # "{name}" and "{model}" alone would match every element
            body = r".+?".join(re.escape(part) for part in re.split(r"\{[a-z_]+\}", alternative))
            matchers.append(
                re.compile(body if len(alternative) >= SHORT_OLD_TEXT else rf"^{body}$")
            )
    return matchers


def _is_relabelled(text: str, matchers: list[re.Pattern]) -> bool:
    return any(m.search(text) for m in matchers)


# Figures the old-text matchers cannot recognise, keyed as the capture keys them, each with the copy
# row that rewords it: a Copy table A row of kind "reworded" (the caption), a model sentence whose old
# text only quotes the placeholder (MU1), and a title whose old text is the bare slot "{name}" (NM8).
RELABELLED_FIGURE_KEYS = {
    "testid:advisor-caption": ("A01", "A"),
    "testid:prism-model-badge": ("MU1", "C"),
    "testid:fr-incumbent-name": ("NM8", "C"),
}


def _relabelled_by_a_copy_row(key: str) -> bool:
    row = RELABELLED_FIGURE_KEYS.get(key)
    if row is None:
        return False
    assert copy.row(*row)["kind"] == "reworded", f"{row} no longer rewords {key}"
    return True


def _control_key(control: dict) -> tuple:
    return (
        control["tag"],
        control["id"],
        control["testid"],
        control["type"],
        control["text"],
        control["href"],
        control["disabled"],
        control["displayed"],
    )


def _is_removed_control(control: dict) -> bool:
    return any(control["text"].startswith(t) for t in harness.REMOVED_CONTROL_TEXTS)


def figure_differences(reading: list, reference: list, matchers: list[re.Pattern]) -> list[str]:
    """Figure texts that changed or went missing; a figure only the head holds is allowed."""
    got: dict[str, list[str]] = {}
    for key, text in reading:
        got.setdefault(key, []).append(text)
    seen: Counter = Counter()
    problems = []
    for key, text in reference:
        index = seen[key]
        seen[key] += 1
        if _is_relabelled(text, matchers) or _relabelled_by_a_copy_row(key):
            continue
        heads = got.get(key, [])
        if index >= len(heads):
            problems.append(f"{key}: lost (reference {text!r})")
        elif heads[index] != text:
            problems.append(f"{key}: page {heads[index]!r} != reference {text!r}")
    return problems


def control_differences(reading: list, reference: list, matchers: list[re.Pattern]) -> list[str]:
    """Controls the reference listed that the page no longer lists, or lists changed."""
    have = Counter(_control_key(c) for c in reading)
    problems = []
    for control in reference:
        if _is_removed_control(control) or _is_relabelled(control["text"], matchers):
            continue
        key = _control_key(control)
        if have[key] <= 0:
            problems.append(f"missing or changed control {key}")
        else:
            have[key] -= 1
    return problems


@pytest.mark.parametrize(("world", "host"), harness.WORLD_HOST_PAIRS)
def test_every_figure_and_control_equals_the_reference(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    ref,
    tmp_path,
    monkeypatch,
    world,
    host,
):
    assert ref["firefox_version"] == firefox.version, (
        "the browser differs from the one the reference was captured with: recapture it from a "
        f"throwaway worktree at {harness.REFERENCE_COMMIT}"
    )
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world(world, tmp_path, monkeypatch.setattr)

    reading = harness.read_host_in_browser(firefox, advisor_server, host)

    want = ref["worlds"][world]["hosts"][host]
    matchers = _relabel_matchers()
    problems = [
        f"1440 {p}"
        for p in figure_differences(reading["figures_1440"], want["figures_1440"], matchers)
    ]
    for width in ("1440", "375"):
        problems += [
            f"{width} {p}"
            for p in control_differences(
                reading[f"controls_{width}"], want[f"controls_{width}"], matchers
            )
        ]
    assert not problems, f"{world} {host} differs from the reference:\n" + "\n".join(problems)
    assert reading["page_errors_1440"] == [] and reading["page_errors_375"] == []


# ---------------------------------------------------------------------------
# The comparison can fail
# ---------------------------------------------------------------------------


def test_the_figure_comparison_reports_a_changed_a_lost_and_an_excluded_figure():
    reference = [["id:a", "1.5%"], ["id:b", "2"], ["id:c", "Good call · saved +1.2%α"]]
    reading = [["id:a", "1.6%"], ["id:c", "Exit beat holding by 1.2%"], ["id:new", "x"]]
    problems = figure_differences(reading, reference, _relabel_matchers())
    assert problems == [
        "id:a: page '1.6%' != reference '1.5%'",
        "id:b: lost (reference '2')",
    ]


def test_the_control_comparison_reports_a_missing_and_a_disabled_control_and_skips_the_removed():
    base = {
        "tag": "button",
        "id": "x",
        "testid": "",
        "type": "button",
        "text": "Force run now",
        "href": "",
        "disabled": False,
        "displayed": True,
    }
    removed = {**base, "id": "dp-view-logs-btn", "text": "View logs"}
    reference = [base, removed]
    assert control_differences([base], reference, []) == []
    assert control_differences([], reference, []) != []
    assert control_differences([{**base, "disabled": True}], reference, []) != []
    assert control_differences([{**base, "displayed": False}], reference, []) != []


def test_the_relabel_matchers_cover_the_verdict_and_the_label_rows():
    matchers = _relabel_matchers()
    assert matchers, "no old text of Copy table C gives a pattern"
    assert not _is_relabelled("an unrelated figure", [m for m in matchers if m.pattern == ".+?"])
    assert not any(m.pattern in {".+?", "^.+?$"} for m in matchers)
    assert _is_relabelled("Good call · saved +1.2%α", matchers)
    assert _is_relabelled("Early exit · gave up 0.4%α", matchers)
    assert not _is_relabelled("Force run now", matchers)
