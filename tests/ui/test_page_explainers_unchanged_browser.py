"""Every figure and control the five pages showed at the reference is still there, unchanged.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_unchanged_browser.py``).
Firefox headless through Playwright, imported inside a fixture; a missing Playwright or Firefox
fails with a message naming it, never a skip.

Each case is one seeded world (``tests/ui/_page_explainers.py``) served by the app in this process
and read at one host through the same function the reference capture used
(``read_host_in_browser``). The record it makes is compared with the one
``tests/fixtures/page_explainers/ref_capture.json`` holds for the same world and host, captured at
the reference commit. Nothing is typed here.

* Figures: the text of every figure element (a leaf element whose ``data-testid`` or ``id`` names
  it) is identical at 1920, 1440 and 390 px; the elements a copy row rewords are excluded by the old
  text each row gives.
* Controls: every control the reference listed at 1920, 1440 and 390 px is still listed, with the
  same type, label, link, disabled state and visibility.
* Table forms: every table keeps, at 1000, 390 and 320 px, the stacked-row form it has at the
  reference (how its parts are laid out, not what they say), so a column the page no longer shows
  leaves the form as it was.

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
    """One pattern per old text a reworded Copy row gives, each ``{slot}`` standing for any run of text.

    A reworded row of table A (a page caption or subtitle given new wording in place) is relabelled
    exactly as a row of table C is.

    A short old text must be a whole element's text; a long one may sit inside it. A row whose old
    text is a pointer to a source line gives no text to match and is skipped.
    """
    matchers = []
    # A reworded Copy A row (a kept element given a new text) is excluded by its old text too.
    for record in (*copy.rows("C"), *(r for r in copy.rows("A") if r["kind"] == "reworded")):
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


# Figures and controls the plan rewrites in place, each by what it was (the new text is asserted by
# the module of the host that shows it): the Overview's suggestions placeholder, the autotune shared
# line (now a sentence), the older-runs summary, and the pointer to the Retirement tab (now the
# Open tab link in the last row of the observations list). A later cycle adds its host's.
REWRITTEN_FIGURE_KEYS = frozenset(
    {
        "testid:suggestions-placeholder",
        "testid:autotune-shared-line",
        "id:autotune-older-summary",
        # The tenure card's missing line states the redundancy once, without peer names.
        "testid:tenure-review-missing",
    }
)
REWRITTEN_CONTROL_TESTIDS = frozenset({"retirement-pointer", "autotune-older-summary"})

# Figures whose texts stay but whose order the plan changes: the tenure evidence headings follow
# the cells' visible order (performance, composite, backtest, marginal contribution, long VIX
# hedge, redundancy), so each is compared as a multiset and not by position.
ORDER_RULED_FIGURE_KEYS = frozenset({"testid:tenure-review-evidence-heading"})

# A figure that is a live clock: compared by its shape (hh:mm:ss), never by its second.
TICKING_FIGURE_KEYS = frozenset({"id:et-clock"})
_CLOCK_SHAPE = re.compile(r"\d{2}:\d{2}:\d{2}")

# Term disclosures the plan removes from a host, as (host, tag, text) -> how many: the Overview's
# five (its tab line, O03, O08, O15 and O27) became lines of its one Glossary. A later cycle adds
# its host's. At most this many may be missing, so a sixth lost disclosure is still reported.
REMOVED_DISCLOSURES = {("/ai-advisor", "summary", "What the terms mean"): 7}

# The summary of a tenure evidence cell of an item the missing line names reads its heading and its
# status only: the reason moved to the line. The reference's summary carried "<heading> unavailable:
# <reason>" (or "not applicable: <reason>"), which the plan rewrites.
_REWRITTEN_EVIDENCE_SUMMARY = re.compile(
    r"^(Performance|Composite score|Redundancy|Backtest|Marginal contribution|Long VIX hedge) "
    r"(unavailable|not applicable)\b"
)


def _is_rewritten_evidence_summary(control: dict) -> bool:
    return control["tag"] == "summary" and bool(_REWRITTEN_EVIDENCE_SUMMARY.match(control["text"]))


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


def _is_removed_figure(text: str) -> bool:
    """A figure that is one of the three controls AC-5 removes (its id names the control)."""
    return any(text.startswith(t) for t in harness.REMOVED_CONTROL_TEXTS)


def _is_removed_control(control: dict) -> bool:
    return any(control["text"].startswith(t) for t in harness.REMOVED_CONTROL_TEXTS)


def figure_differences(reading: list, reference: list, matchers: list[re.Pattern]) -> list[str]:
    """Figure texts that changed or went missing; a figure only the head holds is allowed."""
    got: dict[str, list[str]] = {}
    for key, text in reading:
        got.setdefault(key, []).append(text)
    seen: Counter = Counter()
    problems = []
    for key in ORDER_RULED_FIGURE_KEYS:
        lost = Counter(t for k, t in reference if k == key) - Counter(got.get(key, []))
        if lost:
            problems.append(f"{key}: lost {sorted(lost.elements())}")
    for key, text in reference:
        index = seen[key]
        seen[key] += 1
        if key in ORDER_RULED_FIGURE_KEYS:
            continue
        if (
            _is_relabelled(text, matchers)
            or _is_removed_figure(text)
            or _relabelled_by_a_copy_row(key)
            or key in REWRITTEN_FIGURE_KEYS
        ):
            continue
        heads = got.get(key, [])
        if index >= len(heads):
            problems.append(f"{key}: lost (reference {text!r})")
        elif key in TICKING_FIGURE_KEYS:
            # A live clock reads the second the page was measured at, which the first, cold
            # browser start can move by one: the figure is the readout's shape, not its second.
            if _CLOCK_SHAPE.sub("#", heads[index]) != _CLOCK_SHAPE.sub("#", text):
                problems.append(f"{key}: page {heads[index]!r} is not shaped like {text!r}")
        elif heads[index] != text:
            problems.append(f"{key}: page {heads[index]!r} != reference {text!r}")
    return problems


def control_differences(
    reading: list,
    reference: list,
    matchers: list[re.Pattern],
    removed_disclosures: Counter | None = None,
) -> list[str]:
    """Controls the reference listed that the page no longer lists, or lists changed.

    ``removed_disclosures`` counts, per (tag, text), the controls the plan removes from this host;
    that many missing ones are excused and any further one is reported.
    """
    have = Counter(_control_key(c) for c in reading)
    excusable = Counter(removed_disclosures or {})
    problems = []
    for control in reference:
        if (
            _is_removed_control(control)
            or _is_relabelled(control["text"], matchers)
            or control["testid"] in REWRITTEN_CONTROL_TESTIDS
            or _is_rewritten_evidence_summary(control)
        ):
            continue
        key = _control_key(control)
        if have[key] <= 0:
            if excusable[(control["tag"], control["text"])] > 0:
                excusable[(control["tag"], control["text"])] -= 1
                continue
            problems.append(f"missing or changed control {key}")
        else:
            have[key] -= 1
    return problems


def form_differences(reading: dict, reference: dict) -> list[str]:
    """Tables whose stacked-row form differs from the reference's, or that are no longer found."""
    problems = []
    for name, want in reference.items():
        got = reading.get(name)
        if got is None:
            problems.append(f"table {name}: not read")
        elif got["count"] != want["count"]:
            problems.append(
                f"table {name}: {got['count']} found, the reference has {want['count']}"
            )
        elif got["shapes"] != want["shapes"]:
            problems.append(f"table {name}: form {got['shapes']} != reference {want['shapes']}")
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
    problems: list[str] = []
    for width, _height in harness.INVENTORY_WIDTHS:
        problems += [
            f"{width} {p}"
            for p in figure_differences(
                reading[f"figures_{width}"], want[f"figures_{width}"], matchers
            )
        ]
        problems += [
            f"{width} {p}"
            for p in control_differences(
                reading[f"controls_{width}"],
                want[f"controls_{width}"],
                matchers,
                Counter(
                    {
                        (tag, text): n
                        for (h, tag, text), n in REMOVED_DISCLOSURES.items()
                        if h == host
                    }
                ),
            )
        ]
        assert reading[f"page_errors_{width}"] == [], f"{world} {host} {width}: page errors"
    for width, _height in harness.FORM_WIDTHS:
        problems += [
            f"{width} {p}"
            for p in form_differences(
                reading[f"stacked_forms_{width}"], want[f"stacked_forms_{width}"]
            )
        ]
    assert not problems, f"{world} {host} differs from the reference:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# The comparison can fail
# ---------------------------------------------------------------------------


def test_every_figure_and_control_the_plan_rewrites_is_one_the_reference_capture_holds(ref):
    """A typo in a rewritten key would excuse nothing and hide a lost figure."""
    figures, controls = set(), set()
    for world in ref["worlds"].values():
        for host in world["hosts"].values():
            for width, _height in harness.INVENTORY_WIDTHS:
                figures.update(key for key, _text in host[f"figures_{width}"])
                controls.update(c["testid"] for c in host[f"controls_{width}"])
    ruled = REWRITTEN_FIGURE_KEYS | ORDER_RULED_FIGURE_KEYS
    assert figures >= ruled, sorted(ruled - figures)
    assert controls >= REWRITTEN_CONTROL_TESTIDS, sorted(REWRITTEN_CONTROL_TESTIDS - controls)


def test_the_figure_comparison_reports_a_changed_a_lost_and_an_excluded_figure():
    reference = [["id:a", "1.5%"], ["id:b", "2"], ["id:c", "Good call · saved +1.2%α"]]
    reading = [["id:a", "1.6%"], ["id:c", "Exit beat holding by 1.2%"], ["id:new", "x"]]
    problems = figure_differences(reading, reference, _relabel_matchers())
    assert problems == [
        "id:a: page '1.6%' != reference '1.5%'",
        "id:b: lost (reference '2')",
    ]


def test_a_ticking_clock_is_compared_by_its_shape_and_still_reported_when_lost_or_misshapen():
    reference = [["id:et-clock", "11:00:01"]]
    matchers = _relabel_matchers()
    assert figure_differences([["id:et-clock", "11:00:02"]], reference, matchers) == []
    assert figure_differences([["id:et-clock", "closed"]], reference, matchers) == [
        "id:et-clock: page 'closed' is not shaped like '11:00:01'"
    ]
    assert figure_differences([], reference, matchers) == [
        "id:et-clock: lost (reference '11:00:01')"
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


def test_the_control_comparison_excuses_only_as_many_removed_disclosures_as_the_plan_names():
    disclosure = {
        "tag": "summary",
        "id": "",
        "testid": "",
        "type": "",
        "text": "What the terms mean",
        "href": "",
        "disabled": False,
        "displayed": True,
    }
    reference = [disclosure] * 3
    excused = Counter({("summary", "What the terms mean"): 2})
    assert control_differences([disclosure], reference, [], excused) == []
    assert len(control_differences([], reference, [], excused)) == 1
    assert len(control_differences([disclosure], reference, [], None)) == 2


def test_the_form_comparison_reports_a_changed_a_lost_and_a_missing_table():
    shape = {
        "cell": "block",
        "group": "block",
        "header_shown": False,
        "row": "grid",
        "stacked": True,
    }
    reference = {
        "a": {"count": 1, "shapes": [shape]},
        "b": {"count": 2, "shapes": [shape]},
        "c": {"count": 1, "shapes": [shape]},
        "d": {"count": 0, "shapes": []},
    }
    reading = {
        "a": {"count": 1, "shapes": [{**shape, "stacked": False}]},
        "b": {"count": 1, "shapes": [shape]},
        "d": {"count": 0, "shapes": []},
    }
    problems = form_differences(reading, reference)
    assert [p.split(":")[0] for p in problems] == ["table a", "table b", "table c"]
    assert form_differences(reference, reference) == []


def test_the_relabel_matchers_cover_the_verdict_and_the_label_rows():
    matchers = _relabel_matchers()
    assert matchers, "no old text of Copy table C gives a pattern"
    assert not _is_relabelled("an unrelated figure", [m for m in matchers if m.pattern == ".+?"])
    assert not any(m.pattern in {".+?", "^.+?$"} for m in matchers)
    assert _is_relabelled("Good call · saved +1.2%α", matchers)
    assert _is_relabelled("Early exit · gave up 0.4%α", matchers)
    assert not _is_relabelled("Force run now", matchers)
