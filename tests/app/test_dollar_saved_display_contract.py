"""RED — $-saved panel + Overview label display contracts (VERDICT Findings 5/8/9).

THE BUGS:
  * Finding 8: the $-saved panel excludes ALL of today's guard activity until the
    15:54 post-mortem write, its basis label says only "since <earliest>" (no hint
    that today is missing), and the panel is fetched exactly once on page load —
    it never refreshes even though the page already receives SSE cycle-complete
    events. The literal operator complaint: "the number doesn't move while I watch
    triggers fire."
  * Finding 9: the headline color is hardcoded green in the template
    (color:var(--studio-pos) inline). A negative cumulative renders "$-N.NN" in
    the positive color. Negative days are routine in the captured data.
  * Finding 5: the Overview "Cumulative" row shows a Composer-LIFETIME-anchored
    value beside a chart windowed to ~13 days, with no basis qualifier on the
    label — +37% beside a chart ending -3.4%.

CONTRACTS PINNED (design-contract / source-level assertions, per the established
pattern in tests/app/test_guard_alpha_panel_ui.py — no computed-RGB assertions):
  1. /api/guard-alpha-summary basis_label names the LATEST covered date
     ("through <latest>"), derived from the real production post-mortem files.
  2. templates/index.html must not hardcode the positive color on the
     dollar-saved headline element.
  3. static/index.js fetchGuardAlphaSummary must branch on sign — referencing the
     negative design token, following the existing guard-alpha-headline idiom
     (el.style.color = x >= 0 ? cs('--studio-pos') : cs('--studio-neg')).
  4. static/index.js must re-fetch the $-saved summary on the SSE cycle-complete
     event it already subscribes to.
  5. templates/index.html labels the Cumulative comparison row with its lifetime
     basis so it cannot read as the charted window's cumulative.

FIXTURE PROVENANCE: tests/fixtures/prod_droplet/post_mortems/ — the 12 real
production post-mortem files (see capture_from_droplet.py). Expected dates are
derived from the filenames at runtime.
"""

from __future__ import annotations

import pathlib
import re
import shutil

import pytest

import analytics
import app as app_module

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "prod_droplet"
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _PROJECT_ROOT / "templates" / "index.html"
_INDEX_JS = _PROJECT_ROOT / "static" / "index.js"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def pm_dir(tmp_path, monkeypatch) -> pathlib.Path:
    pm = tmp_path / "post_mortems"
    pm.mkdir()
    for src in (_FIXTURE_DIR / "post_mortems").glob("post_mortem_*.json"):
        shutil.copy(src, pm)
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
    return pm


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _INDEX_JS.read_text(encoding="utf-8")


def _js_block(source: str, anchor: str, span: int = 2500) -> str:
    """A source window following `anchor` — wide enough to hold the function or
    listener body without needing a JS parser."""
    idx = source.find(anchor)
    assert idx != -1, f"static/index.js no longer contains {anchor!r}"
    return source[idx : idx + span]


# ---------------------------------------------------------------------------
# 1. Basis label names the latest covered date
# ---------------------------------------------------------------------------


class TestBasisLabelNamesLatestCoveredDate:
    def test_basis_label_says_through_latest_post_mortem_date(self, client, pm_dir):
        """The label must tell the operator HOW FRESH the number is. With the real
        production files, the latest covered date (derived from the filenames)
        must appear after a "through" qualifier.

        RED today: the label is "snapshot-time basis, since <earliest>" — it
        names only the start, so a number that excludes today looks current.
        """
        latest = max(
            f.name[len("post_mortem_") : -len(".json")] for f in pm_dir.glob("post_mortem_*.json")
        )
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        label = resp.get_json()["basis_label"]

        assert "through" in label.lower(), (
            f"basis_label {label!r} must carry a 'through <date>' freshness qualifier"
        )
        assert latest in label, (
            f"basis_label {label!r} must name the latest covered date {latest} "
            "(derived from the production post-mortem files)"
        )


# ---------------------------------------------------------------------------
# 2 + 3. Headline color reflects sign
# ---------------------------------------------------------------------------


class TestHeadlineColorReflectsSign:
    def test_template_does_not_hardcode_positive_color_on_headline(self):
        """The dollar-saved headline element must not pin the positive token in an
        inline style — a negative cumulative would render green.

        RED today: index.html sets color:var(--studio-pos) inline on
        #dollar-saved-headline.
        """
        headline_tags = re.findall(r"<span[^>]*dollar-saved-headline[^>]*>", _html())
        assert headline_tags, "index.html must contain the dollar-saved-headline element"
        for tag in headline_tags:
            assert "--studio-pos" not in tag, (
                f"headline element hardcodes the positive color: {tag} — the sign "
                "must drive the color at render time"
            )

    def test_fetch_renderer_branches_on_sign_with_negative_token(self):
        """fetchGuardAlphaSummary must apply the negative design token for a
        negative cumulative — same idiom as the guard-alpha-headline renderer
        (>= 0 ? '--studio-pos' : '--studio-neg').

        RED today: the renderer does '$' + value.toFixed(2) with no sign handling.
        """
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "--studio-neg" in block, (
            "fetchGuardAlphaSummary must reference the negative token so a negative "
            "cumulative is not rendered in the positive color"
        )
        assert re.search(r"<\s*0|>=\s*0|Math\.abs", block), (
            "fetchGuardAlphaSummary must branch on the sign of cumulative_saved_dollars"
        )


# ---------------------------------------------------------------------------
# 4. Panel refreshes on the SSE cycle-complete event
# ---------------------------------------------------------------------------


class TestPanelRefreshesOnCycleComplete:
    def test_cycle_complete_listener_refetches_dollar_saved_summary(self):
        """The page already subscribes to SSE cycle-complete (index.js wires
        loadState there). The $-saved panel must ride the same event instead of
        being fetched exactly once at DOMContentLoaded.

        RED today: the cycle-complete listener calls only loadState().
        """
        source = _js()
        # The listener CALLBACK body only — code after the registration (like the
        # one-shot page-load fetch) must not satisfy this assertion.
        callbacks = re.findall(
            r"addEventListener\(\s*['\"]cycle-complete['\"]\s*,(.*?)\}\s*\)\s*;",
            source,
            flags=re.DOTALL,
        )
        assert callbacks, "index.js must register a cycle-complete listener"
        assert any("fetchGuardAlphaSummary" in body for body in callbacks), (
            "the SSE cycle-complete handler must re-fetch the guard-alpha summary — "
            "today it calls only loadState(), so the panel never updates after page load"
        )


# ---------------------------------------------------------------------------
# 5. Cumulative row names its lifetime basis
# ---------------------------------------------------------------------------


class TestCumulativeRowNamesItsBasis:
    def test_cumulative_row_label_carries_lifetime_qualifier(self):
        """The Cumulative comparison row prefers windowed_cumulative_return, whose
        anchor is the Composer LIFETIME per-symphony return — only the alpha
        re-windows. Beside a windowed chart it reads as the chart's cumulative.
        The row label must carry the lifetime qualifier.

        RED today: the label is the bare word "Cumulative".
        """
        html = _html()
        label_spans = re.findall(r"<span[^>]*vs-row-label[^>]*>([^<]*)</span>", html)
        cumulative_labels = [s for s in label_spans if "cumulative" in s.lower()]
        assert cumulative_labels, "index.html must contain the Cumulative row label"
        for label in cumulative_labels:
            assert "lifetime" in label.lower(), (
                f"Cumulative row label {label!r} must qualify its lifetime anchor "
                "(e.g. 'Cumulative · lifetime') — it sits beside a windowed chart"
            )
