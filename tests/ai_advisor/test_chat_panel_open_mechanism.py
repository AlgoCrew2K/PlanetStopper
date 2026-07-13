"""
Regression test — chat panel slide-in mechanism (AC-3b, advisor-suite-fixes.md).

THE BUG (live diagnosis by fix-ux, multiple rounds — computed-style dump,
full ancestor-chain walk, exhaustive CSSOM rule enumeration via
document.styleSheets, document.getAnimations() check, brace/comment-balance
parse-error check on the whole <style> block — every standard cascade
explanation ruled out): .chat-panel toggled `right` (-440px closed / 0 open
via .chat-panel--open). The CSS was syntactically correct and
specificity-correct (.chat-panel.chat-panel--open at (0,2,0) beats
.chat-panel alone at (0,1,0)) — yet in live testing, computed `right` stayed
at the closed value even with the `chat-panel--open` class present and even
with an inline `right:0 !important` override applied directly. The onclick
truncation fix (AC-3, 37bf1fc5) made openChatPanel() reachable for the first
time this cycle — which is what exposed this previously-invisible defect.

THE FIX (fix-fe, dd3efbb1): abandoned the unexplained `right` anomaly and
switched to the transform-based slide mechanism already proven working
elsewhere in this codebase — .detail-panel / #detail-panel (templates/index.html:477-494).
`right: 0` is now CONSTANT on .chat-panel (never toggled — eliminates the
whole class of "a toggled property doesn't apply" bug). Position is instead
driven by `transform: translateX(100%)` (closed) / `.chat-panel--open
{ transform: translateX(0) !important; }` (open) — self-width-relative, so
no separate mobile-breakpoint override is needed either.

This is a SOURCE-TEXT regression test (this project has no jsdom/e2e browser
runner in pytest — established pattern, see tests/dashboard/test_render_basis_fix.py).
It pins the MECHANISM (transform, not right) so a future edit can't silently
reintroduce the exact anomaly fix-ux spent multiple live-diagnosis rounds
tracking down. It does NOT prove the panel is visually on-screen — that proof
is fix-ux's live screenshot (the plan's own "prove from the RENDERED UI" gate),
which is authoritative for AC-3b's actual acceptance.
"""

from __future__ import annotations

import pathlib
import re

_AI_ADVISOR_HTML = pathlib.Path(__file__).parent.parent.parent / "templates" / "ai_advisor.html"
_INDEX_HTML = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _ai_advisor_html() -> str:
    return _AI_ADVISOR_HTML.read_text(encoding="utf-8")


def _index_html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _rule_body(css: str, selector_pattern: str) -> str:
    """Extract the body of the FIRST CSS rule whose selector matches
    `selector_pattern` immediately followed by '{'. Returns the content
    between the braces (exclusive).

    `selector_pattern` must be a regex matching only the exact selector
    (e.g. r"\\.chat-panel" matches the bare-class selector but NOT
    ".chat-panel.chat-panel--open", because after ".chat-panel" the compound
    selector continues with another "." rather than optional whitespace then
    "{" — the required immediate \\{ after \\s* enforces this).
    """
    m = re.search(selector_pattern + r"\s*\{([^}]*)\}", css)
    assert m is not None, f"No CSS rule found matching {selector_pattern!r}"
    return m.group(1)


# ---------------------------------------------------------------------------
# AC-3b — .chat-panel uses transform, not a toggled `right`, to slide in
# ---------------------------------------------------------------------------


class TestChatPanelUsesTransformNotToggledRight:
    """AC-3b: pins the transform-based mechanism fix-fe switched to, after
    live diagnosis ruled out every standard explanation for why the
    previous `right`-toggle mechanism silently failed to apply.
    """

    def test_open_state_rule_uses_transform_translatex_zero(self):
        """FAILS on pre-AC-3b source: .chat-panel--open set `right: 0`, no
        `transform` at all. AC-3b's fix sets `transform: translateX(0)
        !important` instead (matching the proven #detail-panel.open pattern).
        """
        body = _rule_body(_ai_advisor_html(), r"\.chat-panel\.chat-panel--open")
        assert "transform" in body, (
            "AC-3b regression: .chat-panel.chat-panel--open no longer sets "
            "`transform` — reverting to a `right`-only toggle would "
            "reintroduce the exact anomaly fix-ux spent multiple live-"
            f"diagnosis rounds tracking down. Rule body: {body!r}"
        )
        assert "translateX(0)" in body, (
            "AC-3b regression: .chat-panel.chat-panel--open's transform is "
            f"not translateX(0) — the open state must fully cancel the "
            f"closed-state translateX(100%) offset. Rule body: {body!r}"
        )

    def test_base_rule_uses_transform_translatex_100pct_when_closed(self):
        """FAILS on pre-AC-3b source: the base .chat-panel rule set
        `right: -440px` (no transform at all) for the closed state.
        """
        body = _rule_body(_ai_advisor_html(), r"\.chat-panel")
        assert "transform" in body and "translateX(100%)" in body, (
            "AC-3b regression: the base .chat-panel rule no longer sets "
            "transform: translateX(100%) for the closed state — the panel "
            f"must slide off-screen via transform, not right. Rule body: {body!r}"
        )

    def test_right_is_constant_zero_never_toggled(self):
        """AC-3b's whole point: `right` must be a CONSTANT 0 on the base
        rule (present once, never re-declared by .chat-panel--open) — this
        eliminates the entire class of "a toggled property mysteriously
        doesn't apply" bug that the old mechanism suffered from.

        FAILS on pre-AC-3b source: base rule had `right: -440px` (not 0),
        and .chat-panel--open re-declared `right: 0` (a second, toggled
        declaration of the same property — exactly the pattern being
        eliminated).
        """
        base_body = _rule_body(_ai_advisor_html(), r"\.chat-panel")
        assert "right: 0" in base_body or "right:0" in base_body, (
            "AC-3b regression: the base .chat-panel rule's `right` is not "
            f"constant 0. Rule body: {base_body!r}"
        )

        open_body = _rule_body(_ai_advisor_html(), r"\.chat-panel\.chat-panel--open")
        assert "right" not in open_body, (
            "AC-3b regression: .chat-panel.chat-panel--open re-declares "
            "`right` — the fix's entire premise is that `right` is never "
            f"toggled (only `transform` is). Rule body: {open_body!r}"
        )

    def test_mobile_media_query_no_longer_overrides_right(self):
        """FAILS on pre-AC-3b source: the @media (max-width:768px) block set
        `right: -100%` — a second, breakpoint-specific `right` toggle. AC-3b
        removed this because translateX(100%) is self-width-relative and
        needs no per-breakpoint pixel override.
        """
        html = _ai_advisor_html()
        media_idx = html.find("@media (max-width: 768px)")
        assert media_idx != -1, "Mobile @media (max-width: 768px) block not found"
        # The mobile .chat-panel override is the first rule inside this block.
        window = html[media_idx : media_idx + 300]
        mobile_body = _rule_body(window, r"\.chat-panel")
        assert "right" not in mobile_body, (
            "AC-3b regression: the mobile @media .chat-panel override still "
            f"declares `right` — this should be gone. Rule body: {mobile_body!r}"
        )


# ---------------------------------------------------------------------------
# Control sibling — .detail-panel / #detail-panel (index.html) already uses
# this exact pattern; proves the test harness pattern-matches a KNOWN-good
# mechanism, not just "some CSS containing the word transform"
# ---------------------------------------------------------------------------


class TestControlSiblingDetailPanelUsesSamePattern:
    """Control: .detail-panel / #detail-panel (templates/index.html) is the
    PROVEN-WORKING precedent AC-3b's fix was modeled on. If this control ever
    failed, the test harness itself (not .chat-panel) would be the thing to
    suspect.

    Note: the base/closed-state rule is defined under the CLASS selector
    .detail-panel (index.html:477) — #detail-panel (ID) only appears in the
    compound .open selector list (index.html:493-494:
    ".detail-panel.open, #detail-panel.open { ... }"), covering both a
    class-based and id-based instance defensively. Confirmed by direct read
    before relying on either selector.
    """

    def test_detail_panel_closed_state_uses_translatex_100pct(self):
        body = _rule_body(_index_html(), r"\.detail-panel")
        assert "transform" in body and "translateX(100%)" in body, (
            "Sanity check failed: .detail-panel's own closed-state rule "
            "does not use transform: translateX(100%) — the test harness "
            f"(not .chat-panel) is broken. Rule body: {body!r}"
        )

    def test_detail_panel_open_state_uses_translatex_zero_important(self):
        body = _rule_body(_index_html(), r"#detail-panel\.open")
        assert "translateX(0)" in body and "!important" in body, (
            "Sanity check failed: #detail-panel.open does not use "
            "translateX(0) !important — the test harness (not .chat-panel) "
            f"is broken. Rule body: {body!r}"
        )
