"""
RED — BL-3 friction-aware $-saved headline: dashboard disclosure (AC-4/AC-5).

AC-4: the dollar-saved panel (templates/index.html) gains a STATIC (never
JS-injected, so it can never be silently dropped by a JS bug) caveat
qualifying the existing gross headline(s) as "gross of trading costs" --
mirroring the existing static "(marks basis)" caption pattern already in
this panel (index.html:1077).

AC-5: fetchGuardAlphaSummary (static/index.js) renders the two new
net-of-friction response fields
(cumulative_saved_dollars_net_of_friction / saved_dollars_realized_net_of_friction)
as ADDITIONAL lines -- new dedicated DOM elements, never overwriting the
existing gross-headline elements -- reusing the SAME DE-GAS-COHERENCE-001
display contract already established for every dollar figure on this panel:
ABS magnitude, no naked sign character, sign-conditional color + word
("saved"/"lost"), sign-colored INDEPENDENTLY of the gross figure's own sign.

New DOM ids introduced by THIS cycle (pinned here for the implementer, since
the feature plan does not specify exact ids):
  - dollar-saved-net-of-friction-headline / dollar-saved-net-of-friction-verb
  - dollar-saved-realized-net-of-friction-headline / dollar-saved-realized-net-of-friction-verb
The realized net-of-friction line reuses the EXISTING
dollar-saved-realized-coverage counter -- no new coverage element.

Follows the established source-window regex/substring-extraction pattern
from tests/app/test_dollar_saved_panel_sign_coherence.py's `_js_block` --
this codebase asserts JS/HTML behavior via targeted source-text assertions.

REVIEWER-FOUND GAP (bl3review, post-GREEN at ed4eebee): the net-of-friction
render blocks (static/index.js ~1489-1502) are UNCONDITIONAL -- with
guard_event_count===0 the snapshot net line renders "$0.00 saved" right next
to the gross line's honest "No guard events yet", and same for the realized
net line when realized_coverage.with_data===0. Violates the plan's own edge
case ("net-of-friction fields render the same honest empty state as the
gross fields, not a spurious $0.00"). TestNetOfFrictionRespectsEmptyState
below is a DELIBERATE EXCEPTION to this file's regex-only convention (same
rationale as test_dollar_saved_panel_sign_coherence.py's
TestApplyHeroWindowHandlesTokenStrings): a source-text assertion cannot
distinguish "genuinely gated on the same empty-state condition the gross
line uses" from "the guard string happens to appear somewhere nearby in the
file" -- and a naive fix that special-cases on the NUMERIC VALUE being zero
(rather than on guard_event_count/with_data) would be an equally dishonest
regression (a real, non-empty $0.00 result must still render as $0.00, not
be suppressed). Only a real execution against both an empty-state payload
and a genuine-zero-but-non-empty payload can prove the gating condition is
the right one.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _PROJECT_ROOT / "templates" / "index.html"
_INDEX_JS = _PROJECT_ROOT / "static" / "index.js"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _INDEX_JS.read_text(encoding="utf-8")


def _js_block(source: str, anchor: str, span: int = 2500) -> str:
    idx = source.find(anchor)
    assert idx != -1, f"static/index.js no longer contains {anchor!r}"
    return source[idx : idx + span]


def _panel_block(html: str) -> str:
    """Scope every AC-4/AC-5 assertion to the dollar-saved-panel element
    itself (not the whole page) -- same panel-scoping idiom
    tests/app/test_realized_basis_and_turnover_ui.py already uses."""
    start = html.find('data-testid="dollar-saved-panel"')
    assert start != -1, "index.html no longer contains the dollar-saved-panel element"
    # The panel is a self-contained <div>...</div> block; the next major
    # section comment marks its end (same landmark the panel's own inline
    # comments use to describe the ACTIVE SECTION that follows it).
    end = html.find("<!-- ======================== ACTIVE SECTION", start)
    assert end != -1, "could not find the ACTIVE SECTION landmark after dollar-saved-panel"
    return html[start:end]


# ---------------------------------------------------------------------------
# AC-4: static "gross of trading costs" caveat, never JS-injected
# ---------------------------------------------------------------------------


class TestGrossOfTradingCostsStaticCaveat:
    def test_caveat_text_present_in_the_panel_markup(self):
        block = _panel_block(_html())
        assert re.search(r"gross of trading costs", block, re.IGNORECASE), (
            "AC-4: templates/index.html's dollar-saved-panel must carry a static "
            "'gross of trading costs' caveat qualifying the existing headline(s) -- "
            "mirroring the existing static '(marks basis)' caption pattern"
        )

    def test_caveat_text_is_never_js_injected(self):
        """The caveat must live in the STATIC HTML, never be written by JS --
        a JS bug (or a future refactor that drops the line) could otherwise
        silently remove the disclosure with zero visible signal."""
        assert "gross of trading costs" not in _js().lower(), (
            "AC-4: 'gross of trading costs' must be static markup in index.html, "
            "never a string written by static/index.js -- a JS-injected caveat can "
            "be silently dropped by a JS bug, defeating the point of a persistent "
            "disclosure"
        )


# ---------------------------------------------------------------------------
# AC-5: new DOM elements for the net-of-friction figures
# ---------------------------------------------------------------------------


class TestNetOfFrictionTemplateElementsExist:
    def test_snapshot_net_of_friction_headline_element_exists(self):
        block = _panel_block(_html())
        assert re.search(r'id="dollar-saved-net-of-friction-headline"', block), (
            "AC-5: index.html must add a dedicated "
            "id=dollar-saved-net-of-friction-headline element inside the "
            "dollar-saved-panel -- a new ADDITIONAL line, distinct from the "
            "existing gross dollar-saved-headline element"
        )

    def test_snapshot_net_of_friction_verb_element_exists(self):
        block = _panel_block(_html())
        assert re.search(r'id="dollar-saved-net-of-friction-verb"', block), (
            "AC-5: index.html must add id=dollar-saved-net-of-friction-verb so "
            "fetchGuardAlphaSummary can drive the net line's own 'saved'/'lost' "
            "word, independent of the gross line's dollar-saved-verb element"
        )

    def test_realized_net_of_friction_headline_element_exists(self):
        block = _panel_block(_html())
        assert re.search(r'id="dollar-saved-realized-net-of-friction-headline"', block), (
            "AC-5: index.html must add id=dollar-saved-realized-net-of-friction-headline "
            "for the realized-basis net-of-friction figure"
        )

    def test_realized_net_of_friction_verb_element_exists(self):
        block = _panel_block(_html())
        assert re.search(r'id="dollar-saved-realized-net-of-friction-verb"', block), (
            "AC-5: index.html must add id=dollar-saved-realized-net-of-friction-verb"
        )

    def test_new_headline_elements_are_distinct_from_the_gross_ones(self):
        """A same-id typo (e.g. accidentally reusing dollar-saved-headline)
        would make the four id-presence tests above pass vacuously if the
        implementer wired the SAME element twice under different test
        expectations -- pin distinctness explicitly."""
        block = _panel_block(_html())
        new_ids = {
            "dollar-saved-net-of-friction-headline",
            "dollar-saved-net-of-friction-verb",
            "dollar-saved-realized-net-of-friction-headline",
            "dollar-saved-realized-net-of-friction-verb",
        }
        existing_ids = {
            "dollar-saved-headline",
            "dollar-saved-verb",
            "dollar-saved-realized-headline",
            "dollar-saved-realized-verb",
        }
        assert new_ids.isdisjoint(existing_ids)
        for new_id in new_ids:
            assert block.count(f'id="{new_id}"') == 1, (
                f"id={new_id!r} must appear exactly once inside the dollar-saved-panel"
            )


class TestFetchGuardAlphaSummaryRendersNetOfFrictionLines:
    def test_references_the_snapshot_net_of_friction_response_field(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        assert "cumulative_saved_dollars_net_of_friction" in block, (
            "AC-5: fetchGuardAlphaSummary must read "
            "data.cumulative_saved_dollars_net_of_friction from the route response"
        )

    def test_references_the_realized_net_of_friction_response_field(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        assert "saved_dollars_realized_net_of_friction" in block, (
            "AC-5: fetchGuardAlphaSummary must read "
            "data.saved_dollars_realized_net_of_friction from the route response"
        )

    def test_writes_the_new_snapshot_headline_element(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        assert "dollar-saved-net-of-friction-headline" in block, (
            "fetchGuardAlphaSummary must getElementById the new snapshot "
            "net-of-friction headline element to write into it"
        )
        assert "dollar-saved-net-of-friction-verb" in block

    def test_writes_the_new_realized_headline_element(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        assert "dollar-saved-realized-net-of-friction-headline" in block
        assert "dollar-saved-realized-net-of-friction-verb" in block

    def test_never_overwrites_the_existing_gross_headline_element_ids(self):
        """The net-of-friction render logic must target its OWN elements --
        never the pre-existing headlineEl (dollar-saved-headline) or
        realizedHeadlineEl (dollar-saved-realized-headline) variables that
        already carry the gross figures."""
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        net_idx = block.find("net-of-friction")
        assert net_idx != -1, "no net-of-friction render code found at all"
        # The gross headline's own getElementById call must not be the SAME
        # call site reused for the net figure -- i.e. the net line has its
        # own distinct getElementById('dollar-saved-net-of-friction-headline')
        # call, not a re-assignment of headlineEl's textContent to the net
        # value.
        assert (
            "getElementById('dollar-saved-net-of-friction-headline')" in block
            or 'getElementById("dollar-saved-net-of-friction-headline")' in block
        ), (
            "the net-of-friction snapshot figure must be written via its own "
            "dedicated getElementById call, not by re-purposing the existing "
            "headlineEl variable"
        )

    def test_no_naked_minus_sign_on_the_net_of_friction_lines(self):
        """Reuses the DE-GAS-COHERENCE-001 display contract already enforced
        for the gross/realized lines: ABS magnitude, no '-$' literal."""
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        net_idx = block.find("cumulative_saved_dollars_net_of_friction")
        assert net_idx != -1
        # A window around the net-of-friction read/render logic -- wide enough
        # to hold the local variable read through the DOM write.
        net_render_block = block[net_idx : net_idx + 600]
        assert "'-$'" not in net_render_block and '"-$"' not in net_render_block, (
            "the net-of-friction snapshot line must not prepend a literal '-$' -- "
            "ABS magnitude with no sign character, same contract as the gross line"
        )

    def test_sign_conditional_word_and_color_present_for_snapshot_net_line(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        net_idx = block.find("cumulative_saved_dollars_net_of_friction")
        assert net_idx != -1
        net_render_block = block[net_idx : net_idx + 600]
        assert "Math.abs" in net_render_block, (
            "the net-of-friction snapshot line must render the ABS magnitude "
            "(Math.abs), same contract as the gross line"
        )
        assert "'lost'" in net_render_block, (
            "the net-of-friction snapshot line must carry the 'lost' word for its "
            "own negative branch -- required for the sign-flip case (gross positive, "
            "net negative) to ever display 'lost' at all"
        )
        assert "--studio-pos" in net_render_block and "--studio-neg" in net_render_block, (
            "the net-of-friction snapshot line must branch its OWN color by its OWN "
            "sign (both --studio-pos and --studio-neg tokens must appear in its "
            "render block) -- never inherited from the gross figure's sign"
        )

    def test_sign_conditional_word_and_color_present_for_realized_net_line(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=6000)
        net_idx = block.find("saved_dollars_realized_net_of_friction")
        assert net_idx != -1
        net_render_block = block[net_idx : net_idx + 600]
        assert "Math.abs" in net_render_block
        assert "'lost'" in net_render_block
        assert "--studio-pos" in net_render_block and "--studio-neg" in net_render_block


# ---------------------------------------------------------------------------
# Reviewer-found gap: net-of-friction lines must respect the SAME empty-state
# gating as the gross/realized lines -- real Node execution, not source-text.
# ---------------------------------------------------------------------------


def _extract_function_with_signature(source: str, func_name: str) -> str:
    """Extract `function name(...) { ... }` (signature + full body) via brace
    counting, for embedding verbatim in a standalone Node harness -- same
    technique tests/app/test_dollar_saved_panel_sign_coherence.py uses to
    extract applyHeroWindow's body."""
    match = re.search(rf"function\s+{func_name}\s*\([^)]*\)\s*\{{", source)
    assert match is not None, f"{func_name} not found in static/index.js"
    brace_start = source.index("{", match.start())
    depth = 0
    i = brace_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : i + 1]
        i += 1
    pytest.fail(f"Could not extract {func_name} -- unbalanced braces")


_PANEL_ELEMENT_IDS = [
    "dollar-saved-headline",
    "dollar-saved-verb",
    "guard-event-count",
    "dollar-saved-basis-label",
    "dollar-saved-realized-headline",
    "dollar-saved-realized-verb",
    "dollar-saved-realized-coverage",
    "dollar-saved-net-of-friction-headline",
    "dollar-saved-net-of-friction-verb",
    "dollar-saved-realized-net-of-friction-headline",
    "dollar-saved-realized-net-of-friction-verb",
]


def _run_fetch_guard_alpha_summary(fake_data: dict) -> dict:
    """Executes the REAL fetchGuardAlphaSummary source (extracted live from
    static/index.js, never hardcoded) against a synthetic DOM stub + a
    stubbed fetch() resolving to `fake_data`, and returns the resulting
    textContent of the panel elements relevant to this gap.

    `cs` (the color-swatch helper, defined outside fetchGuardAlphaSummary in
    the real file) is stubbed locally since brace-counting extracts only the
    target function's own body.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed -- skipping fetchGuardAlphaSummary execution check")

    fn_src = _extract_function_with_signature(_js(), "fetchGuardAlphaSummary")
    el_ids_js = json.dumps(_PANEL_ELEMENT_IDS)

    harness = f"""
    function cs(varName) {{ return varName === '--studio-pos' ? 'POS' : 'NEG'; }}

    var __els = {{}};
    {el_ids_js}.forEach(function (id) {{
        __els[id] = {{ id: id, textContent: '', style: {{}} }};
    }});

    var document = {{
        getElementById: function (id) {{
            return Object.prototype.hasOwnProperty.call(__els, id) ? __els[id] : null;
        }}
    }};

    var FAKE_DATA = {json.dumps(fake_data)};
    var fetch = function (url) {{
        return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve(FAKE_DATA); }} }});
    }};

    {fn_src}

    fetchGuardAlphaSummary('all');

    setTimeout(function () {{
        var out = {{}};
        Object.keys(__els).forEach(function (id) {{ out[id] = __els[id].textContent; }});
        console.log(JSON.stringify(out));
    }}, 50);
    """

    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"fetchGuardAlphaSummary harness crashed:\n{result.stderr}"
    return json.loads(result.stdout.strip())


class TestNetOfFrictionRespectsEmptyState:
    """Reviewer-found gap (bl3review): confirmed via real Node execution.
    Both the negative case (empty state must NOT show a spurious dollar
    figure) and the positive control (a genuine non-empty zero-result must
    still render honestly, not be suppressed) are pinned so a fix can't
    satisfy the negative case by special-casing on the NUMERIC VALUE being
    zero instead of on the actual guard_event_count/with_data condition.
    """

    def test_snapshot_net_line_honest_empty_state_not_spurious_zero_dollars(self):
        fake_data = {
            "cumulative_saved_dollars": 0.0,
            "guard_event_count": 0,
            "date_range": {"earliest": None, "latest": None},
            "basis_label": "no guard events yet",
            "source": "post_mortem_eod",
            "saved_dollars_realized": 0.0,
            "realized_coverage": {"with_data": 0, "total": 0},
            "window": "all",
            "cumulative_saved_dollars_net_of_friction": 0.0,
            "saved_dollars_realized_net_of_friction": 0.0,
        }
        result = _run_fetch_guard_alpha_summary(fake_data)
        net_headline = result["dollar-saved-net-of-friction-headline"]
        assert not net_headline.startswith("$"), (
            f"AC-5/edge-case: with guard_event_count===0 the snapshot net-of-friction "
            f"headline must render the SAME honest empty state as the gross line "
            f"(which shows 'No guard events yet', not a dollar figure) -- got "
            f"{net_headline!r} instead. The plan explicitly forbids 'a spurious $0.00'."
        )
        assert net_headline != "", (
            "the net-of-friction headline must be set to SOME honest text in the "
            "empty state, not left blank"
        )

    def test_snapshot_net_line_still_shows_a_genuine_zero_when_events_exist(self):
        """Positive control: guard_event_count > 0 (real guard events exist)
        but the net-of-friction figure happens to compute to exactly 0.0 --
        this is a REAL result and must render '$0.00', not be suppressed by
        a naive `if (netSaved === 0)` empty-state check that confuses 'the
        number is zero' with 'there is no data'."""
        fake_data = {
            "cumulative_saved_dollars": 5.0,
            "guard_event_count": 3,
            "date_range": {"earliest": "2026-08-01", "latest": "2026-08-03"},
            "basis_label": "snapshot-time basis, since 2026-08-01 . through 2026-08-03",
            "source": "post_mortem_eod",
            "saved_dollars_realized": 5.0,
            "realized_coverage": {"with_data": 3, "total": 3},
            "window": "all",
            "cumulative_saved_dollars_net_of_friction": 0.0,
            "saved_dollars_realized_net_of_friction": 5.0,
        }
        result = _run_fetch_guard_alpha_summary(fake_data)
        assert result["dollar-saved-net-of-friction-headline"] == "$0.00", (
            f"a genuine zero net-of-friction result (guard_event_count=3, real data) "
            f"must still render '$0.00' -- got "
            f"{result['dollar-saved-net-of-friction-headline']!r}. Gating must key off "
            "guard_event_count, not off the net figure's own numeric value."
        )

    def test_realized_net_line_honest_empty_state_not_spurious_zero_dollars(self):
        fake_data = {
            "cumulative_saved_dollars": 100.0,
            "guard_event_count": 4,
            "date_range": {"earliest": "2026-08-01", "latest": "2026-08-04"},
            "basis_label": "snapshot-time basis, since 2026-08-01 . through 2026-08-04",
            "source": "post_mortem_eod",
            "saved_dollars_realized": 0.0,
            "realized_coverage": {"with_data": 0, "total": 4},
            "window": "all",
            "cumulative_saved_dollars_net_of_friction": 90.0,
            "saved_dollars_realized_net_of_friction": 0.0,
        }
        result = _run_fetch_guard_alpha_summary(fake_data)
        realized_net_headline = result["dollar-saved-realized-net-of-friction-headline"]
        assert not realized_net_headline.startswith("$"), (
            f"AC-5/edge-case: with realized_coverage.with_data===0 the realized "
            f"net-of-friction headline must render the SAME honest empty state as "
            f"the realized gross line (which shows 'no realized data yet', not a "
            f"dollar figure) -- got {realized_net_headline!r} instead."
        )
        assert realized_net_headline != "", (
            "the realized net-of-friction headline must be set to SOME honest text "
            "in the no-coverage state, not left blank"
        )

    def test_realized_net_line_still_shows_a_genuine_zero_when_coverage_exists(self):
        """Positive control mirroring the snapshot one above, for the
        realized basis."""
        fake_data = {
            "cumulative_saved_dollars": 100.0,
            "guard_event_count": 4,
            "date_range": {"earliest": "2026-08-01", "latest": "2026-08-04"},
            "basis_label": "snapshot-time basis, since 2026-08-01 . through 2026-08-04",
            "source": "post_mortem_eod",
            "saved_dollars_realized": 20.0,
            "realized_coverage": {"with_data": 2, "total": 4},
            "window": "all",
            "cumulative_saved_dollars_net_of_friction": 90.0,
            "saved_dollars_realized_net_of_friction": 0.0,
        }
        result = _run_fetch_guard_alpha_summary(fake_data)
        assert result["dollar-saved-realized-net-of-friction-headline"] == "$0.00", (
            f"a genuine zero realized-net-of-friction result (with_data=2, real "
            f"coverage) must still render '$0.00' -- got "
            f"{result['dollar-saved-realized-net-of-friction-headline']!r}. Gating "
            "must key off realized_coverage.with_data, not off the figure's own "
            "numeric value."
        )
