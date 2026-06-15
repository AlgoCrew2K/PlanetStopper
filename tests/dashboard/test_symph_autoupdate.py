"""
RED — cycle/symph-autoupdate: symphony card section auto-partition.

THE BUG (user-reported, live, confirmed by recon a3f2b2feda95cf9f0):
  The live dashboard does NOT move a symphony card from the Standby section to the
  Active (Armed & Triggered) section when the symphony's status transitions.

  Root cause in static/index.js:
    - updateCards() (line 1025) finds each card by [data-sym-id=...].sym-card and
      updates its status-pill text/class IN PLACE.
    - It never checks whether the card belongs in active vs standby based on the
      current status, and never moves the card node between section containers.
    - The section containers ([data-testid="active-section"] and
      [data-testid="standby-section"]) are never referenced for DOM manipulation in
      updateCards or updateDashboard — only the count-badge spans are touched.

  SSR grouping rule (app.py:474-475):
    active  = armed OR tp_armed OR para_armed OR triggered
    standby = not (armed OR tp_armed OR para_armed OR triggered)

  The fix: after updating a card's status fields, determine which section the card
  belongs to per its current status, and if its current DOM parent differs, move the
  card node (appendChild) into the correct section container.  Preserves the card DOM
  (sparkline canvas, all fields) — NO data.html re-injection.

ACCEPTANCE CRITERIA (source-text contracts — the ux-expert visual gate verifies
DOM movement on the live page; these tests pin the implementation contract):

  AC-SA.1  static/index.js passes `node --check` (no syntax errors).

  AC-SA.2  updateCards (or a dedicated helper it calls) references BOTH section
           container testids: `[data-testid="active-section"]` and
           `[data-testid="standby-section"]` — needed to locate move targets.

  AC-SA.3  The card relocation uses `appendChild` (move a live DOM node into the
           target section's cards-grid or section container directly).

  AC-SA.4  The active predicate in the JS section-partition logic matches the SSR
           rule exactly: `armed || tp_armed || para_armed || triggered` (all four
           flags, same boolean form as app.py:474).

  AC-SA.5  updateCards does NOT inject `data.html` into any DOM element
           (innerHTML from the html field is the broken pre-fix pattern that
           causes full-page flicker + sparkline loss).

  AC-SA.6  The `/api/state` `symphonies` array carries the four status booleans
           (`armed`, `tp_armed`, `para_armed`, `triggered`) for each symphony —
           these are the fields the JS partition logic reads.

  AC-SA.7  After a card moves to the active section, the section count badges
           update to reflect the new grouping.  The updateSectionMeta function
           already does this; the fix must not regress its count logic.

  NON-REGRESSION AC-SA.NR.1  updateCards still updates status-pill text/class.
  NON-REGRESSION AC-SA.NR.2  node --check passes (AC-SA.1).

Harness: no jsdom in this environment.  The established project pattern is
source-text assertions against static/index.js + node --check.  The ux-expert
visual gate (real browser on :8095, status transition simulation) is the final
acceptance check for actual DOM movement.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_JS_PATH = _WORKTREE / "static" / "index.js"
_TEMPLATE_PATH = _WORKTREE / "templates" / "index.html"


def _js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _html() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-SA.1 — parse gate
# ---------------------------------------------------------------------------


def test_node_check_passes():
    """
    AC-SA.1: static/index.js must pass `node --check` with exit code 0.

    Any syntax error introduced by the section-partition fix prevents the entire
    file from loading — all card updates, hero chart, and status pills silently fail.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available in this environment")
    result = subprocess.run(
        [node, "--check", str(_JS_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "AC-SA.1 FAIL: static/index.js has a parse error introduced by the "
        "section-partition fix.\n"
        f"node --check stderr:\n{result.stderr.strip()}"
    )


# ---------------------------------------------------------------------------
# AC-SA.2 — section container testids referenced in update logic
# ---------------------------------------------------------------------------


class TestSectionContainerTestidsReferenced:
    """
    updateCards (or a dedicated helper) must reference the section container
    testids so it can locate the target DOM nodes for card relocation.

    Current code: neither 'active-section' (without '-count') nor
    'standby-section' (without '-count') appears in updateCards or anywhere
    in index.js as a querySelector target for DOM insertion.
    """

    def test_active_section_container_testid_referenced_in_js(self):
        """
        AC-SA.2a: index.js must contain a querySelector for
        '[data-testid="active-section"]' (the section container, NOT just the
        count badge 'active-section-count').

        Current: only 'active-section-count' is targeted (the badge span).
        The section container itself is never queried for appendChild.
        This test is RED on current code.
        """
        js = _js()
        # Must contain the container testid (not the count badge variant).
        has_container_ref = 'data-testid="active-section"' in js and 'active-section"' in js
        # Disallow satisfying this test with only the count badge reference.
        # The container testid is "active-section"; the badge is "active-section-count".
        # Check the non-count form specifically.
        container_pattern = re.search(r'data-testid=["\']active-section["\']', js)
        assert container_pattern is not None, (
            "AC-SA.2a FAIL: static/index.js does not reference the active section "
            "container testid ('data-testid=\"active-section\"'). "
            "The card relocation logic needs to querySelector this container to know "
            "where to move cards. Current code only references 'active-section-count' "
            "(the badge span) — NOT the section container itself."
        )

    def test_standby_section_container_testid_referenced_in_js(self):
        """
        AC-SA.2b: index.js must contain a querySelector for
        '[data-testid="standby-section"]' (the section container).

        Current: only 'standby-section-count' is targeted (the badge span).
        This test is RED on current code.
        """
        js = _js()
        container_pattern = re.search(r'data-testid=["\']standby-section["\']', js)
        assert container_pattern is not None, (
            "AC-SA.2b FAIL: static/index.js does not reference the standby section "
            "container testid ('data-testid=\"standby-section\"'). "
            "The card relocation logic needs both section containers as move targets. "
            "Current code only references 'standby-section-count' (the badge span)."
        )

    def test_both_section_container_testids_referenced_together(self):
        """
        AC-SA.2c: Both section container testids must be present in the same
        general area of the file (within 500 lines of each other), confirming
        they are part of the same section-partition logic — not isolated
        references in unrelated code.
        """
        js = _js()
        lines = js.splitlines()
        active_lines = [
            i
            for i, line in enumerate(lines)
            if re.search(r'data-testid=["\']active-section["\']', line)
        ]
        standby_lines = [
            i
            for i, line in enumerate(lines)
            if re.search(r'data-testid=["\']standby-section["\']', line)
        ]
        assert active_lines and standby_lines, (
            "AC-SA.2c FAIL: one or both section container testids are missing. "
            f"active-section refs at lines: {active_lines or 'NONE'}; "
            f"standby-section refs at lines: {standby_lines or 'NONE'}."
        )
        min_gap = min(abs(a - b) for a in active_lines for b in standby_lines)
        assert min_gap <= 500, (
            f"AC-SA.2c FAIL: the section container testid references are {min_gap} "
            "lines apart — they appear to be in unrelated code, not co-located in "
            "the same partition logic."
        )


# ---------------------------------------------------------------------------
# AC-SA.3 — appendChild present in the card-update context
# ---------------------------------------------------------------------------


class TestAppendChildInCardUpdateContext:
    """
    The card relocation must use appendChild (or equivalent DOM node move) so
    the live card node — including its sparkline canvas and all bound state —
    is moved rather than destroyed and recreated.

    Current code: appendChild is absent from updateCards entirely (only one
    reference exists in updateAccountAllTime, unrelated to cards).
    """

    def test_append_child_present_in_update_cards_vicinity(self):
        """
        AC-SA.3: static/index.js must contain `appendChild` in the updateCards
        function body (or in a dedicated helper function called from updateCards
        in the same section of the file).

        The established non-flicker requirement forbids data.html innerHTML
        injection (AC-SA.5).  The only standard DOM API for moving a live node
        is appendChild / insertBefore / prepend.  We assert appendChild as the
        canonical pattern; insertBefore is acceptable but appendChild suffices.

        Current: no appendChild in updateCards or its vicinity.
        This test is RED on current code.
        """
        js = _js()

        # Locate updateCards body.
        start = js.find("function updateCards")
        assert start != -1, "updateCards function must exist in static/index.js"

        # Extend the window to capture any helper called immediately after updateCards
        # (up to the next top-level function declaration or 200 lines beyond).
        end_marker = js.find("\n    function ", start + 1)
        # Take extra context in case the implementer factored out a helper nearby.
        look_end = end_marker if end_marker != -1 else start + 5000
        body = js[start:look_end]

        assert "appendChild" in body, (
            "AC-SA.3 FAIL: updateCards (or its immediate vicinity) does not contain "
            "'appendChild'. The card relocation fix must move the live card DOM node "
            "into the target section container using appendChild (or insertBefore). "
            "Using innerHTML or outerHTML to recreate the card would destroy the "
            "sparkline canvas and cause flicker — forbidden by AC-SA.5."
        )

    def test_no_section_relocation_via_data_html(self):
        """
        AC-SA.5: updateCards must NOT inject data.html into any DOM element.

        data.html is the server-rendered full-page HTML returned by /api/state.
        Prior to the cards-live fix, the poll injected this as innerHTML into a
        container, causing full-page flicker and sparkline loss. That pattern is
        banned. The section-partition fix must be a DOM-node move, not a re-render.
        """
        js = _js()

        start = js.find("function updateCards")
        assert start != -1, "updateCards function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 5000]

        # Forbid innerHTML assignment referencing data.html in updateCards.
        has_html_inject = bool(
            re.search(r"\.innerHTML\s*=\s*.*?\bdata\.html\b", body)
            or re.search(r"\bdata\.html\b.*?\.innerHTML\s*=", body)
        )
        assert not has_html_inject, (
            "AC-SA.5 FAIL: updateCards injects data.html as innerHTML. "
            "This causes full-page flicker and destroys sparkline canvases. "
            "The section-partition fix must use DOM node movement (appendChild), "
            "not innerHTML re-injection."
        )


# ---------------------------------------------------------------------------
# AC-SA.4 — active predicate matches SSR rule exactly
# ---------------------------------------------------------------------------


class TestActiveSectionPredicateMatchesSsr:
    """
    The JS partition predicate must match the SSR grouping rule from app.py:474:
        active = sym.armed || sym.tp_armed || sym.para_armed || sym.triggered

    A predicate that only checks `armed || triggered` (missing tp_armed and
    para_armed) would misplace TP-Armed and Para-Armed cards.

    These tests look specifically in the CARD-MOVE context — near the
    `active-section` or `standby-section` container references — not in
    updateSectionMeta or updateCards' existing pill logic (which uses the
    flags for different purposes).
    """

    def _card_move_context(self, js: str) -> str:
        """Return the code window where the card move logic is expected to live.

        Strategy: find the first occurrence of 'active-section"' (container
        testid) and return ±2000 chars around it.  If absent, return empty
        string so the caller's assertion fails cleanly.
        """
        idx = js.find('active-section"')
        if idx == -1:
            return ""
        return js[max(0, idx - 300) : idx + 2000]

    def test_js_active_predicate_includes_all_four_flags_in_card_move_context(self):
        """
        AC-SA.4: the section-partition logic (the code that decides which section
        a card belongs to BEFORE moving it) must reference all four status flags:
        armed, tp_armed, para_armed, triggered — in the vicinity of the actual
        card-move code (near the active-section container reference).

        This is DIFFERENT from updateSectionMeta (which also uses all four flags
        but for badge counting, not card movement) and from the existing pill
        update (which reads the same flags for display, not partition).

        The test looks specifically near the active-section container querySelector —
        the code that will be added by the fix.

        Current code: no active-section container reference exists → context is
        empty → no flags found → RED.
        """
        js = _js()
        context = self._card_move_context(js)

        assert context, (
            "AC-SA.4 FAIL (precondition): no 'active-section' container testid "
            "found near the card-move context. Cannot verify the partition predicate. "
            "The fix must add a querySelector for '[data-testid=\"active-section\"]' "
            "alongside the card-move logic."
        )

        missing = []
        for flag in ("armed", "tp_armed", "para_armed", "triggered"):
            if not re.search(r"\b" + re.escape(flag) + r"\b", context):
                missing.append(flag)

        assert not missing, (
            "AC-SA.4 FAIL: the section-partition logic (near active-section container) "
            f"is missing status flags: {missing}. "
            "The active predicate must include all four flags "
            "(armed, tp_armed, para_armed, triggered) to match app.py:474. "
            "Missing flags cause those symphony types to stay in the wrong section."
        )

    def test_active_predicate_is_or_not_and_in_card_move_context(self):
        """
        AC-SA.4 sub-check: in the card-move context, the predicate uses OR not AND.

        A card is active if ANY one of the four flags is true.  This check
        looks near the active-section container reference, not in updateSectionMeta.

        Current code: no active-section container reference → context empty → RED.
        """
        js = _js()
        context = self._card_move_context(js)

        assert context, (
            "AC-SA.4 FAIL (precondition): no 'active-section' container testid found. "
            "Cannot verify the OR predicate."
        )

        has_or_predicate = bool(
            re.search(
                r"(sym\.)?(armed|triggered|tp_armed|para_armed).{0,80}\|\|.{0,80}(sym\.)?(armed|triggered|tp_armed|para_armed)",
                context,
            )
        )
        assert has_or_predicate, (
            "AC-SA.4 FAIL: no OR predicate over status flags found in the "
            "card-move context (near active-section container). "
            "The active predicate must be `sym.armed || sym.tp_armed || "
            "sym.para_armed || sym.triggered` (any flag = active). "
            "Current updateCards has no partition logic."
        )


# ---------------------------------------------------------------------------
# AC-SA.6 — /api/state symphonies array carries the four status booleans
# ---------------------------------------------------------------------------


class TestApiStateSymphoniesBooleans:
    """
    The JS partition logic reads sym.armed / sym.tp_armed / sym.para_armed /
    sym.triggered from the data.symphonies array.  If these fields are absent
    from the payload the fix is silently broken.

    This test hits the real /api/state route (real analytics module) and
    confirms the four booleans are present per symphony entry.
    """

    @pytest.fixture
    def client(self):
        import app as app_module

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            yield c

    def test_api_state_symphonies_carries_status_booleans(self, client, monkeypatch):
        """
        AC-SA.6: each entry in data.symphonies from GET /api/state must carry
        all four status booleans used by the section-partition predicate.

        We inject a symphony in each status category and confirm the field
        appears in the serialized symphonies array.  A missing field makes the
        client-side predicate silently evaluate to false (falsy undefined).
        """
        import app as app_module

        armed_sym_id = "sym-armed"
        triggered_sym_id = "sym-triggered"
        tp_armed_sym_id = "sym-tp"
        para_armed_sym_id = "sym-para"
        standby_sym_id = "sym-standby"

        bot_state = {
            armed_sym_id: {
                "name": "Armed Sym",
                "account": "ACC1",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
            triggered_sym_id: {
                "name": "Triggered Sym",
                "account": "ACC1",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": True,
                "triggered_reason": "Stop Hit",
            },
            tp_armed_sym_id: {
                "name": "TP Sym",
                "account": "ACC1",
                "armed": False,
                "tp_armed": True,
                "para_armed": False,
                "triggered": False,
            },
            para_armed_sym_id: {
                "name": "Para Sym",
                "account": "ACC1",
                "armed": False,
                "tp_armed": False,
                "para_armed": True,
                "triggered": False,
            },
            standby_sym_id: {
                "name": "Standby Sym",
                "account": "ACC1",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }

        with (
            patch.object(app_module.database, "load_state", return_value=bot_state),
            patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}),
            patch.object(app_module, "render_template", lambda *_a, **_k: ""),
        ):
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        symphonies = body.get("symphonies")
        assert isinstance(symphonies, list), (
            "AC-SA.6 FAIL: /api/state response has no 'symphonies' list. "
            "The client-side partition reads from data.symphonies."
        )

        sym_by_id = {s.get("id"): s for s in symphonies if s.get("id")}

        # Every symphony must carry all four boolean fields.
        for sym_id, expected_flags in {
            armed_sym_id: {
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
            triggered_sym_id: {
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": True,
            },
            tp_armed_sym_id: {
                "armed": False,
                "tp_armed": True,
                "para_armed": False,
                "triggered": False,
            },
            para_armed_sym_id: {
                "armed": False,
                "tp_armed": False,
                "para_armed": True,
                "triggered": False,
            },
            standby_sym_id: {
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }.items():
            assert sym_id in sym_by_id, (
                f"AC-SA.6 FAIL: symphony '{sym_id}' not found in data.symphonies. "
                f"Present IDs: {list(sym_by_id.keys())}"
            )
            sym = sym_by_id[sym_id]
            for flag, expected in expected_flags.items():
                assert flag in sym, (
                    f"AC-SA.6 FAIL: symphony '{sym_id}' in data.symphonies is missing "
                    f"field '{flag}'. The client-side partition predicate reads this "
                    f"field — if absent it evaluates as falsy (undefined), misplacing "
                    f"the card."
                )
                assert bool(sym[flag]) == expected, (
                    f"AC-SA.6 FAIL: symphony '{sym_id}' field '{flag}' = {sym[flag]!r}, "
                    f"expected {expected}. The boolean must reflect the actual status."
                )


# ---------------------------------------------------------------------------
# AC-SA.7 — updateSectionMeta count logic is not regressed
# ---------------------------------------------------------------------------


class TestSectionCountBadgeNonRegression:
    """
    updateSectionMeta already updates the active-section-count and
    standby-section-count badge spans on each poll.  The section-partition fix
    must not remove or regress this logic.
    """

    def test_update_section_meta_still_references_active_section_count(self):
        """
        AC-SA.7a: updateSectionMeta must still reference 'active-section-count'
        (the count badge testid), not just 'active-section' (the container).
        """
        js = _js()
        start = js.find("function updateSectionMeta")
        assert start != -1, "updateSectionMeta must exist in index.js"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 800]
        assert "active-section-count" in body, (
            "AC-SA.7a FAIL: updateSectionMeta no longer references "
            "'active-section-count'. The section-partition fix must not regress "
            "the badge-count update logic."
        )

    def test_update_section_meta_still_references_standby_section_count(self):
        """
        AC-SA.7b: updateSectionMeta must still reference 'standby-section-count'.
        """
        js = _js()
        start = js.find("function updateSectionMeta")
        assert start != -1, "updateSectionMeta must exist in index.js"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 800]
        assert "standby-section-count" in body, (
            "AC-SA.7b FAIL: updateSectionMeta no longer references "
            "'standby-section-count'. The section-partition fix must not regress "
            "the badge-count update logic."
        )


# ---------------------------------------------------------------------------
# NON-REGRESSION AC-SA.NR.1 — status-pill update still present in updateCards
# ---------------------------------------------------------------------------


class TestStatusPillUpdateNonRegression:
    """
    The pre-existing status-pill update (text + class from
    sym.triggered/armed/tp_armed/para_armed) must still work after the
    section-partition fix. The fix adds node movement; it must not remove
    the existing field-update logic.
    """

    def test_status_pill_update_still_in_update_cards(self):
        """
        AC-SA.NR.1: updateCards must still reference 'status-pill' for in-place
        text/class updates.  The section-partition fix is additive — it adds
        node movement, does not replace the pill update.
        """
        js = _js()
        start = js.find("function updateCards")
        assert start != -1, "updateCards must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 5000]
        assert "status-pill" in body, (
            "AC-SA.NR.1 FAIL: updateCards no longer references 'status-pill'. "
            "The section-partition fix must be ADDITIVE — it adds node movement, "
            "it does not remove the existing status-pill text/class update."
        )
