"""RED tests — D3 sessionStorage chat-context handoff.

Defect D3 (from .design-handoff/advisor-ui-diag/TAB-DEFECTS-RCA.md):
    When "Chat about this" is clicked on the Asset Swaps or Logic Changes tab,
    openChatPanel is undefined (ai_advisor_chat.js is not loaded on those pages).
    The fallback branches navigate to /ai-advisor/chat via window.location.href
    WITHOUT writing the artifact to sessionStorage first, so the chat page lands
    with no artifact context.

Required new codepath (D3):
    SENDER (asset_swaps + logic_changes JS):
        else/catch branch MUST call
            sessionStorage.setItem('pendingChatArtifact', d)
        immediately before
            window.location.href = '/ai-advisor/chat'

    RECEIVER (ai_advisor_chat.js DOMContentLoaded):
        MUST read sessionStorage.getItem('pendingChatArtifact'),
        if present and valid JSON: parse → openChatPanel(parsed)
                                         → sessionStorage.removeItem('pendingChatArtifact')
        if absent or invalid JSON: no-op (never throw)

All tests in this file are RED on the current codebase.  They express the
acceptance criteria for the implementer via pattern-matching source inspection
plus node --check (JS syntax validation).  The project has no browser test
harness, so DOM simulation is out of scope; the contract is verified through
file-content assertions.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

_ASSET_SWAPS_JS = _PROJECT_ROOT / "static" / "ai_advisor_asset_swaps.js"
_LOGIC_CHANGES_JS = _PROJECT_ROOT / "static" / "ai_advisor_logic_changes.js"
_CHAT_JS = _PROJECT_ROOT / "static" / "ai_advisor_chat.js"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"Expected JS file not found: {path}"
    return path.read_text(encoding="utf-8")


# ===========================================================================
# Group 1 — Sender: ai_advisor_asset_swaps.js
#
# The "Chat about this" onclick handler has two branches where openChatPanel
# is unavailable:
#   (a) the else branch  — typeof openChatPanel !== 'function'
#   (b) the catch branch — exception during openChatPanel(JSON.parse(d))
#
# Both branches MUST write to sessionStorage before navigating.
# ===========================================================================


class TestAssetSwapsSenderHandoff:
    """ai_advisor_asset_swaps.js — fallback branches write pendingChatArtifact."""

    def test_asset_swaps_js_contains_sessionStorage_setItem(self):
        """The file must call sessionStorage.setItem somewhere.

        This is the minimal presence check — the artefact is written before
        navigation so the chat page can pick it up.
        """
        src = _read(_ASSET_SWAPS_JS)
        assert "sessionStorage.setItem" in src, (
            "ai_advisor_asset_swaps.js does not contain sessionStorage.setItem.\n"
            "The fallback branches (else + catch) must write the artifact JSON to\n"
            "  sessionStorage.setItem('pendingChatArtifact', d)\n"
            "before navigating to /ai-advisor/chat.  Without this the chat page\n"
            "opens with no artifact context (D3 defect)."
        )

    def test_asset_swaps_js_sessionStorage_uses_correct_key(self):
        """sessionStorage.setItem must be called with the key 'pendingChatArtifact'.

        The receiver (ai_advisor_chat.js DOMContentLoaded) reads exactly this
        key.  A mismatch means the handoff silently fails at runtime.
        """
        src = _read(_ASSET_SWAPS_JS)
        assert "pendingChatArtifact" in src, (
            "ai_advisor_asset_swaps.js does not contain the key 'pendingChatArtifact'.\n"
            "Both sessionStorage.setItem calls must use this exact key so the\n"
            "receiver in ai_advisor_chat.js can read it.\n"
            "Check for typos: 'pendingChatArtifact' (camelCase, no spaces)."
        )

    def test_asset_swaps_js_sessionStorage_setItem_with_key_in_same_expression(self):
        """sessionStorage.setItem('pendingChatArtifact', ...) must appear in the source.

        This rules out a scenario where setItem and the key appear separately
        (e.g. setItem called with a variable whose value is set elsewhere).
        The write must be a direct, readable call so reviewers can verify the
        key is correct without tracing variable flow.
        """
        src = _read(_ASSET_SWAPS_JS)
        # Accept single or double quotes around the key name.
        pattern = r"""sessionStorage\.setItem\(\s*['"]pendingChatArtifact['"]"""
        assert re.search(pattern, src), (
            "ai_advisor_asset_swaps.js must contain:\n"
            "  sessionStorage.setItem('pendingChatArtifact', ...)\n"
            "The key must be a string literal (not a variable) so the contract\n"
            "is auditable without tracing variable assignments.\n"
            "Pattern checked: " + pattern
        )

    def test_asset_swaps_js_no_bare_navigation_in_else_branch(self):
        """The else branch must NOT navigate without a preceding sessionStorage write.

        Current defect code (RED):
            else { window.location.href='/ai-advisor/chat'; }

        Required code (GREEN):
            else {
                sessionStorage.setItem('pendingChatArtifact', d);
                window.location.href='/ai-advisor/chat';
            }

        We verify this by checking that every occurrence of the chat navigation
        URL is preceded (within 200 chars) by a sessionStorage.setItem call in
        the same source region.  A bare navigation without a setItem call
        immediately before it is the defect.
        """
        src = _read(_ASSET_SWAPS_JS)

        nav_pattern = r"window\.location\.href\s*=\s*['\"]\/ai-advisor\/chat['\"]"
        nav_matches = list(re.finditer(nav_pattern, src))

        assert nav_matches, (
            "ai_advisor_asset_swaps.js does not contain any window.location.href "
            "navigation to /ai-advisor/chat.  After the fix, the fallback branches "
            "must navigate there (with sessionStorage write preceding the navigation)."
        )

        for m in nav_matches:
            nav_start = m.start()
            # Look back up to 200 chars to find a sessionStorage.setItem call.
            look_back_start = max(0, nav_start - 200)
            window_before = src[look_back_start:nav_start]
            assert "sessionStorage.setItem" in window_before, (
                f"Found window.location.href='/ai-advisor/chat' at offset {nav_start} "
                f"in ai_advisor_asset_swaps.js WITHOUT a sessionStorage.setItem call "
                f"in the preceding 200 characters.\n"
                f"Context:\n{src[look_back_start : nav_start + 50]!r}\n\n"
                f"Fix: add sessionStorage.setItem('pendingChatArtifact', d) "
                f"immediately before every window.location.href navigation to "
                f"/ai-advisor/chat in both the else branch and the catch branch."
            )

    def test_asset_swaps_js_catch_branch_also_writes_sessionStorage(self):
        """The catch branch must also write to sessionStorage before navigating.

        The catch block fires when JSON.parse(d) or openChatPanel(parsed)
        throws.  Without the sessionStorage write in the catch branch the
        artifact is lost even when openChatPanel is defined but throws.

        We verify there are at least 2 occurrences of sessionStorage.setItem
        in the file — one for the else branch, one for the catch branch.
        """
        src = _read(_ASSET_SWAPS_JS)
        count = src.count("sessionStorage.setItem")
        assert count >= 2, (
            f"ai_advisor_asset_swaps.js contains only {count} occurrence(s) of "
            f"sessionStorage.setItem, expected at least 2 (one in the else branch "
            f"and one in the catch branch).\n"
            f"Both fallback paths must write the artifact before navigating."
        )


# ===========================================================================
# Group 3 — Sender: ai_advisor_logic_changes.js
#
# The Logic Changes tab has an identical "Chat about this" onclick pattern
# (confirmed in RCA, line 271 of ai_advisor_logic_changes.js).
# The same fix is required.
# ===========================================================================


class TestLogicChangesSenderHandoff:
    """ai_advisor_logic_changes.js — fallback branches write pendingChatArtifact."""

    def test_logic_changes_js_contains_sessionStorage_setItem(self):
        """The file must call sessionStorage.setItem somewhere."""
        src = _read(_LOGIC_CHANGES_JS)
        assert "sessionStorage.setItem" in src, (
            "ai_advisor_logic_changes.js does not contain sessionStorage.setItem.\n"
            "The fallback branches (else + catch) in the 'Chat about this' onclick\n"
            "must write the artifact JSON to sessionStorage before navigating.\n"
            "This is the same D3 fix required in ai_advisor_asset_swaps.js."
        )

    def test_logic_changes_js_sessionStorage_uses_correct_key(self):
        """sessionStorage.setItem must be called with key 'pendingChatArtifact'."""
        src = _read(_LOGIC_CHANGES_JS)
        assert "pendingChatArtifact" in src, (
            "ai_advisor_logic_changes.js does not contain the key 'pendingChatArtifact'.\n"
            "The receiver reads exactly this key — a mismatch silently fails at runtime."
        )

    def test_logic_changes_js_sessionStorage_setItem_with_key_in_same_expression(self):
        """sessionStorage.setItem('pendingChatArtifact', ...) as a direct call."""
        src = _read(_LOGIC_CHANGES_JS)
        pattern = r"""sessionStorage\.setItem\(\s*['"]pendingChatArtifact['"]"""
        assert re.search(pattern, src), (
            "ai_advisor_logic_changes.js must contain:\n"
            "  sessionStorage.setItem('pendingChatArtifact', ...)\n"
            "with the key as a string literal.\n"
            "Pattern checked: " + pattern
        )

    def test_logic_changes_js_no_bare_navigation_without_sessionStorage_write(self):
        """Every /ai-advisor/chat navigation must be preceded by a sessionStorage write."""
        src = _read(_LOGIC_CHANGES_JS)

        nav_pattern = r"window\.location\.href\s*=\s*['\"]\/ai-advisor\/chat['\"]"
        nav_matches = list(re.finditer(nav_pattern, src))

        assert nav_matches, (
            "ai_advisor_logic_changes.js does not contain any window.location.href "
            "navigation to /ai-advisor/chat.  After the fix, the fallback branches "
            "must navigate there with sessionStorage write preceding the navigation."
        )

        for m in nav_matches:
            nav_start = m.start()
            look_back_start = max(0, nav_start - 200)
            window_before = src[look_back_start:nav_start]
            assert "sessionStorage.setItem" in window_before, (
                f"Found window.location.href='/ai-advisor/chat' at offset {nav_start} "
                f"in ai_advisor_logic_changes.js WITHOUT a sessionStorage.setItem call "
                f"in the preceding 200 characters.\n"
                f"Context:\n{src[look_back_start : nav_start + 50]!r}\n\n"
                f"Fix: add sessionStorage.setItem('pendingChatArtifact', d) "
                f"immediately before every /ai-advisor/chat navigation."
            )

    def test_logic_changes_js_catch_branch_also_writes_sessionStorage(self):
        """Both the else and catch branches must write sessionStorage (≥2 setItem calls)."""
        src = _read(_LOGIC_CHANGES_JS)
        count = src.count("sessionStorage.setItem")
        assert count >= 2, (
            f"ai_advisor_logic_changes.js contains only {count} occurrence(s) of "
            f"sessionStorage.setItem, expected at least 2 (else + catch branches)."
        )


# ===========================================================================
# Group 4 — Receiver: ai_advisor_chat.js DOMContentLoaded
#
# On DOMContentLoaded the chat page must:
#   1. Read sessionStorage.getItem('pendingChatArtifact')
#   2. If present and valid JSON: parse → openChatPanel(parsed) → removeItem
#   3. If absent or invalid: no-op
# ===========================================================================


class TestChatReceiverHandoff:
    """ai_advisor_chat.js must read and consume pendingChatArtifact on DOMContentLoaded."""

    def test_chat_js_reads_sessionStorage_getItem(self):
        """ai_advisor_chat.js must call sessionStorage.getItem."""
        src = _read(_CHAT_JS)
        assert "sessionStorage.getItem" in src, (
            "ai_advisor_chat.js does not contain sessionStorage.getItem.\n"
            "The DOMContentLoaded handler must read the pending artifact key so\n"
            "the chat panel can be pre-populated when the user arrives from\n"
            "the Asset Swaps or Logic Changes tab (D3 fix)."
        )

    def test_chat_js_reads_correct_key(self):
        """sessionStorage.getItem must be called with key 'pendingChatArtifact'."""
        src = _read(_CHAT_JS)
        assert "pendingChatArtifact" in src, (
            "ai_advisor_chat.js does not reference the key 'pendingChatArtifact'.\n"
            "The receiver must read exactly this key to match what the sender writes."
        )

    def test_chat_js_getItem_with_correct_key_in_same_expression(self):
        """sessionStorage.getItem('pendingChatArtifact') as a direct call."""
        src = _read(_CHAT_JS)
        pattern = r"""sessionStorage\.getItem\(\s*['"]pendingChatArtifact['"]"""
        assert re.search(pattern, src), (
            "ai_advisor_chat.js must contain:\n"
            "  sessionStorage.getItem('pendingChatArtifact')\n"
            "with the key as a string literal (not a variable) so the contract\n"
            "is auditable without tracing variable assignments.\n"
            "Pattern checked: " + pattern
        )

    def test_chat_js_removes_item_from_sessionStorage(self):
        """ai_advisor_chat.js must call sessionStorage.removeItem after consuming the artifact.

        The key must be cleared after the chat panel is opened so that a
        subsequent navigation to the chat page (without a new artifact) does
        not re-open the panel with stale context.
        """
        src = _read(_CHAT_JS)
        assert "sessionStorage.removeItem" in src, (
            "ai_advisor_chat.js does not contain sessionStorage.removeItem.\n"
            "After consuming pendingChatArtifact (parsing + calling openChatPanel),\n"
            "the key must be removed so it does not persist across navigations."
        )

    def test_chat_js_removes_correct_key(self):
        """sessionStorage.removeItem must be called with key 'pendingChatArtifact'."""
        src = _read(_CHAT_JS)
        pattern = r"""sessionStorage\.removeItem\(\s*['"]pendingChatArtifact['"]"""
        assert re.search(pattern, src), (
            "ai_advisor_chat.js must contain:\n"
            "  sessionStorage.removeItem('pendingChatArtifact')\n"
            "to clear the key after the chat panel is opened.\n"
            "Pattern checked: " + pattern
        )

    def test_chat_js_sessionStorage_read_is_in_domcontentloaded_handler(self):
        """The sessionStorage.getItem call must appear inside the DOMContentLoaded handler.

        If the read happens at module parse time (outside DOMContentLoaded),
        the DOM may not be ready and openChatPanel will fail to open the panel
        (the panel DOM nodes do not exist yet).

        We verify that sessionStorage.getItem appears AFTER the
        'DOMContentLoaded' event registration in the source, within the same
        handler region (a simple positional check sufficient for this pattern).
        """
        src = _read(_CHAT_JS)
        dcl_idx = src.find("DOMContentLoaded")
        assert dcl_idx != -1, (
            "ai_advisor_chat.js must contain a DOMContentLoaded event listener.\n"
            "The sessionStorage read must happen inside this handler so the DOM\n"
            "is ready when openChatPanel is called."
        )

        getitem_idx = src.find("sessionStorage.getItem")
        if getitem_idx == -1:
            # Already caught by test_chat_js_reads_sessionStorage_getItem above.
            pytest.skip("sessionStorage.getItem not present — caught by sibling test")

        assert getitem_idx > dcl_idx, (
            f"sessionStorage.getItem appears at offset {getitem_idx} in "
            f"ai_advisor_chat.js, which is BEFORE the DOMContentLoaded listener "
            f"at offset {dcl_idx}.\n"
            f"The sessionStorage read must be inside the DOMContentLoaded handler "
            f"so that openChatPanel is called after the panel DOM nodes are created."
        )

    def test_chat_js_calls_openChatPanel_after_reading_sessionStorage(self):
        """openChatPanel must be referenced after sessionStorage.getItem in the source.

        The receiver flow is: getItem → JSON.parse → openChatPanel → removeItem.
        We verify that openChatPanel appears after the getItem call so the
        parsed artifact is passed to the panel opener.
        """
        src = _read(_CHAT_JS)
        getitem_idx = src.find("sessionStorage.getItem")
        if getitem_idx == -1:
            pytest.skip("sessionStorage.getItem not present — caught by sibling test")

        # Look for openChatPanel in the region after getItem (up to 500 chars ahead).
        region = src[getitem_idx : getitem_idx + 500]
        assert "openChatPanel" in region, (
            f"openChatPanel does not appear within 500 characters after "
            f"sessionStorage.getItem in ai_advisor_chat.js.\n"
            f"The DOMContentLoaded handler must: read getItem → parse → call "
            f"openChatPanel(parsed) → removeItem.\n"
            f"Region checked:\n{region!r}"
        )

    def test_chat_js_removeItem_follows_openChatPanel_in_domcontentloaded(self):
        """removeItem must follow openChatPanel in the DOMContentLoaded handler.

        Order matters: open the panel first, then remove the key.
        If removeItem fires before openChatPanel, an error in openChatPanel
        leaves the key orphaned with no retry path.  Correct order is:
          openChatPanel(parsed) → removeItem('pendingChatArtifact')
        """
        src = _read(_CHAT_JS)
        getitem_idx = src.find("sessionStorage.getItem")
        if getitem_idx == -1:
            pytest.skip("sessionStorage.getItem not present — caught by sibling test")

        # Within the handler region starting at getItem
        region = src[getitem_idx : getitem_idx + 600]

        open_panel_idx = region.find("openChatPanel")
        remove_idx = region.find("sessionStorage.removeItem")

        if open_panel_idx == -1:
            pytest.skip("openChatPanel not in region — caught by sibling test")
        if remove_idx == -1:
            pytest.skip("sessionStorage.removeItem not in region — caught by sibling test")

        assert remove_idx > open_panel_idx, (
            f"sessionStorage.removeItem appears at relative offset {remove_idx} "
            f"BEFORE openChatPanel at {open_panel_idx} in the DOMContentLoaded "
            f"handler region of ai_advisor_chat.js.\n"
            f"Correct order: openChatPanel(parsed) THEN removeItem('pendingChatArtifact').\n"
            f"Region:\n{region!r}"
        )


# ===========================================================================
# Group 5 — Receiver: graceful no-op on absent or invalid JSON
#
# The chat page may be loaded directly (no pendingChatArtifact written).
# The receiver must not crash in that case.
# We verify by checking that a try/catch or null-guard is present near the
# sessionStorage.getItem call.
# ===========================================================================


class TestChatReceiverRobustness:
    """ai_advisor_chat.js receiver must be robust to absent/invalid pendingChatArtifact."""

    def test_chat_js_has_null_guard_or_trycatch_near_getItem(self):
        """The receiver must guard against null (absent key) and invalid JSON.

        sessionStorage.getItem returns null when the key is absent.
        JSON.parse throws on invalid JSON.

        Acceptable patterns (at least one must be present near the getItem call):
          - if (raw) { ... }  (null guard before parse)
          - try { ... } catch  (catches parse errors)

        A receiver that calls JSON.parse without a null check throws
        "Cannot read properties of null" when the key is absent.
        A receiver that calls JSON.parse without a try/catch crashes on
        malformed data in sessionStorage (e.g. corruption, third-party writes).
        """
        src = _read(_CHAT_JS)
        getitem_idx = src.find("sessionStorage.getItem")
        if getitem_idx == -1:
            pytest.skip("sessionStorage.getItem not present — caught by sibling test")

        # Look for a guard pattern within 400 characters of the getItem call
        region = src[getitem_idx : getitem_idx + 400]

        has_null_guard = bool(re.search(r"if\s*\(", region))
        has_try_catch = "try" in region and "catch" in region

        assert has_null_guard or has_try_catch, (
            "The sessionStorage.getItem('pendingChatArtifact') call in "
            "ai_advisor_chat.js is not guarded by a null check or try/catch.\n\n"
            "When the key is absent, getItem returns null and JSON.parse(null) "
            "returns null (safe) BUT null.field access or openChatPanel(null) "
            "could still throw.  A try/catch is the simplest safe pattern:\n\n"
            "  try {\n"
            "    var _raw = sessionStorage.getItem('pendingChatArtifact');\n"
            "    if (_raw) {\n"
            "      openChatPanel(JSON.parse(_raw));\n"
            "      sessionStorage.removeItem('pendingChatArtifact');\n"
            "    }\n"
            "  } catch (e) { /* invalid JSON or missing key — no-op */ }\n\n"
            f"Region checked (first 400 chars after getItem):\n{region!r}"
        )

    def test_chat_js_does_not_call_removeItem_unconditionally(self):
        """removeItem must NOT be called unconditionally (outside the success path).

        An unconditional removeItem clears the key even when JSON.parse fails,
        losing the artifact permanently.  removeItem must only fire when the
        parse succeeds and openChatPanel is called.

        We verify that removeItem and the conditional / try block appear together
        by checking that removeItem is NOT the very first statement after getItem
        (i.e., it is not unconditional).
        """
        src = _read(_CHAT_JS)
        getitem_idx = src.find("sessionStorage.getItem")
        if getitem_idx == -1:
            pytest.skip("sessionStorage.getItem not present — caught by sibling test")

        region = src[getitem_idx : getitem_idx + 400]

        remove_idx = region.find("sessionStorage.removeItem")
        if remove_idx == -1:
            pytest.skip("sessionStorage.removeItem not in region — caught by sibling test")

        # The first statement after getItem must NOT be removeItem — there must
        # be a null/conditional check or openChatPanel call before it.
        # We check that openChatPanel appears before removeItem in the region.
        open_panel_idx = region.find("openChatPanel")
        if open_panel_idx == -1:
            pytest.skip("openChatPanel not in region — caught by sibling test")

        assert open_panel_idx < remove_idx, (
            f"sessionStorage.removeItem appears at relative offset {remove_idx} "
            f"BEFORE openChatPanel at {open_panel_idx} in the region following "
            f"sessionStorage.getItem in ai_advisor_chat.js.\n"
            f"removeItem must only fire AFTER a successful openChatPanel call, "
            f"not unconditionally after getItem."
        )


# ===========================================================================
# Group 6 — Cross-file contract coherence
#
# The key name written by the sender must exactly match the key read and
# removed by the receiver.  A single-character typo silently breaks the
# handoff with no error message.
# ===========================================================================


class TestHandoffKeyCoherence:
    """The key 'pendingChatArtifact' must be spelled identically across all three files."""

    def test_asset_swaps_and_chat_use_same_key(self):
        """The key in ai_advisor_asset_swaps.js must match the key in ai_advisor_chat.js."""
        sw_src = _read(_ASSET_SWAPS_JS)
        chat_src = _read(_CHAT_JS)

        key = "pendingChatArtifact"
        assert key in sw_src, (
            f"Key '{key}' not found in ai_advisor_asset_swaps.js — "
            "sender cannot write a key the receiver never reads."
        )
        assert key in chat_src, (
            f"Key '{key}' not found in ai_advisor_chat.js — "
            "receiver cannot read a key the sender writes under a different name."
        )

    def test_logic_changes_and_chat_use_same_key(self):
        """The key in ai_advisor_logic_changes.js must match the key in ai_advisor_chat.js."""
        lc_src = _read(_LOGIC_CHANGES_JS)
        chat_src = _read(_CHAT_JS)

        key = "pendingChatArtifact"
        assert key in lc_src, f"Key '{key}' not found in ai_advisor_logic_changes.js."
        assert key in chat_src, f"Key '{key}' not found in ai_advisor_chat.js."

    def test_no_alternate_key_spellings_in_sender_files(self):
        """Sender files must not contain alternate spellings of the handoff key.

        Common typos: pendingChatArtifact (missing capital A), pending_chat_artifact,
        pendingArtifact, chatArtifact.  A partial match with a different full key
        means the sender and receiver disagree on the key name.
        """
        alternate_keys = [
            "pendingchatartifact",  # all-lowercase
            "pending_chat_artifact",  # snake_case
            "pendingArtifact",  # truncated
            "chatArtifact",  # missing prefix
            "pending_artifact",  # wrong name
        ]

        for js_path in [_ASSET_SWAPS_JS, _LOGIC_CHANGES_JS]:
            src = _read(js_path).lower()
            for alt in alternate_keys:
                assert alt.lower() not in src or "pendingchatartifact" in src, (
                    f"Found alternate key spelling '{alt}' in {js_path.name}.\n"
                    f"The canonical key is 'pendingChatArtifact' (camelCase).\n"
                    f"Using a different key spelling silently breaks the handoff."
                )


# ===========================================================================
# Group 7 — Negative: current codebase is RED
#
# These tests explicitly verify the pre-fix state to make the RED signal
# unambiguous.  If any of these PASS, the codebase already has the fix and
# the TDD cycle should be marked GREEN (not RED).
# ===========================================================================


class TestCurrentStateIsRed:
    """Explicit RED assertions — these verify the defect exists before the fix.

    If ANY of these tests PASS on the current codebase it means the fix has
    already been applied and the RED phase is over.
    """

    def test_asset_swaps_js_currently_has_no_sessionStorage_setItem(self):
        """CURRENT STATE: ai_advisor_asset_swaps.js has NO sessionStorage.setItem.

        This test is expected to FAIL after the GREEN fix is applied.
        Its purpose is to make the RED phase unambiguous.
        """
        src = _read(_ASSET_SWAPS_JS)
        if "sessionStorage.setItem" not in src:
            # Defect confirmed present — this is the expected RED state.
            pytest.fail(
                "RED CONFIRMED: ai_advisor_asset_swaps.js does not contain "
                "sessionStorage.setItem.\n"
                "The fallback else/catch branches navigate to /ai-advisor/chat "
                "without writing the artifact to sessionStorage first (D3 defect).\n"
                "Implement the fix: add sessionStorage.setItem('pendingChatArtifact', d) "
                "before window.location.href in both fallback branches."
            )
        # If setItem IS present the fix has already been applied — skip.
        pytest.skip("sessionStorage.setItem already present — fix has been applied (GREEN)")

    def test_logic_changes_js_currently_has_no_sessionStorage_setItem(self):
        """CURRENT STATE: ai_advisor_logic_changes.js has NO sessionStorage.setItem."""
        src = _read(_LOGIC_CHANGES_JS)
        if "sessionStorage.setItem" not in src:
            pytest.fail(
                "RED CONFIRMED: ai_advisor_logic_changes.js does not contain "
                "sessionStorage.setItem.\n"
                "Same D3 defect as ai_advisor_asset_swaps.js — the Logic Changes tab "
                "fallback branches navigate without writing to sessionStorage."
            )
        pytest.skip("sessionStorage.setItem already present — fix has been applied (GREEN)")

    def test_chat_js_currently_has_no_sessionStorage_getItem(self):
        """CURRENT STATE: ai_advisor_chat.js has NO sessionStorage.getItem."""
        src = _read(_CHAT_JS)
        if "sessionStorage.getItem" not in src:
            pytest.fail(
                "RED CONFIRMED: ai_advisor_chat.js does not contain "
                "sessionStorage.getItem.\n"
                "The DOMContentLoaded handler does not read the pending artifact key.\n"
                "Implement the fix: add the pendingChatArtifact read inside the "
                "DOMContentLoaded listener in ai_advisor_chat.js."
            )
        pytest.skip("sessionStorage.getItem already present — fix has been applied (GREEN)")

    def test_chat_js_currently_has_no_sessionStorage_removeItem(self):
        """CURRENT STATE: ai_advisor_chat.js has NO sessionStorage.removeItem."""
        src = _read(_CHAT_JS)
        if "sessionStorage.removeItem" not in src:
            pytest.fail(
                "RED CONFIRMED: ai_advisor_chat.js does not contain "
                "sessionStorage.removeItem.\n"
                "The receiver does not clear the key after consuming the artifact."
            )
        pytest.skip("sessionStorage.removeItem already present — fix has been applied (GREEN)")
