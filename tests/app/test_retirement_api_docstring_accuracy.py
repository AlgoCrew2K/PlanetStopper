"""RED tests -- Cycle 2d AC-6: api_retirement_recommendations docstring
accuracy (PR#140 2nd /code-review finding 7).

feature-plans/retirement-approval-polish-2d.md AC-6: api_retirement_
recommendations's 'Response shape' docstring must document the three keys
the route ACTUALLY returns now -- approval_status (Cycle 2c, live-joined via
_join_retirement_approval_status), candidate_name/sibling_name (PR#140
review finding 3, live-resolved via _refresh_retirement_display_names) --
noting these three are FRESH request-time overlays, not part of the
persisted raw_response schema the rest of the docstring describes.

The route body has genuinely returned these 3 keys since Cycle 2c/the
finding-3 fix -- this is a doc-only drift-correction (mirrors tests/
ai_advisor/test_logic_l_2_api_docstring.py's own established pattern for the
same class of finding: docstring lagging real code, fixed by editing the
docstring to match the body, never the reverse).

Pattern (import the route fn, read .__doc__, flatten whitespace) duplicated
from tests/ai_advisor/test_logic_l_2_api_docstring.py per this repo's
established fixtures-are-not-cross-file-shared convention.

Expected state: RED until app.py's api_retirement_recommendations docstring
names all 3 keys and notes they are fresh overlays, not persisted schema.
"""

from __future__ import annotations

import inspect
import re

import app as app_mod
from app import api_retirement_recommendations


def _route_docstring() -> str:
    return api_retirement_recommendations.__doc__ or ""


def _route_source_region() -> str:
    """inspect.getsource so the region tracks the function even if its line
    numbers shift -- this is the docstring + body the tests pin against."""
    return inspect.getsource(api_retirement_recommendations)


# ---------------------------------------------------------------------------
# Anchor: the route body must actually call the two live-overlay helpers the
# docstring is being corrected to describe -- pin first, so a docstring
# "fix" can never just delete the claim instead of correcting it to match
# real code.
# ---------------------------------------------------------------------------


def test_route_body_calls_the_live_overlay_helpers_the_docstring_describes():
    region = _route_source_region()
    # api_retirement_recommendations() itself delegates to
    # _fetch_retirement_recommendations(), which is what actually calls both
    # live-overlay helpers -- confirm the delegation is intact so the
    # docstring's claim tracks real behavior, not just prose.
    assert "_fetch_retirement_recommendations(" in region, (
        "api_retirement_recommendations must call _fetch_retirement_recommendations "
        "-- the shared helper that live-joins approval_status and refreshes "
        "candidate_name/sibling_name. "
        f"Current route source:\n{region}"
    )


def test_fetch_helper_calls_both_live_overlay_functions():
    """Corroborating anchor at the shared-helper level (not the thin route
    wrapper) -- confirms both overlay functions the docstring names are
    genuinely wired, not just referenced in a comment."""
    region = inspect.getsource(app_mod._fetch_retirement_recommendations)
    assert "_join_retirement_approval_status(" in region, (
        "_fetch_retirement_recommendations must call _join_retirement_approval_status "
        f"(the approval_status live-join). Current source:\n{region}"
    )
    assert "_refresh_retirement_display_names(" in region, (
        "_fetch_retirement_recommendations must call _refresh_retirement_display_names "
        f"(the candidate_name/sibling_name fresh-resolution). Current source:\n{region}"
    )


# ---------------------------------------------------------------------------
# Positive contract: the docstring must NAME all 3 overlay keys.
# ---------------------------------------------------------------------------


def test_docstring_names_approval_status_key():
    doc = _route_docstring()
    assert "approval_status" in doc, (
        f"Route docstring's Response shape section omits 'approval_status' -- the "
        f"route has returned this live-joined key since Cycle 2c. Current docstring:\n{doc}"
    )


def test_docstring_names_candidate_name_key():
    doc = _route_docstring()
    assert "candidate_name" in doc, (
        f"Route docstring's Response shape section omits 'candidate_name' -- the "
        f"route has returned this live-resolved key since PR#140 review finding 3. "
        f"Current docstring:\n{doc}"
    )


def test_docstring_names_sibling_name_key():
    doc = _route_docstring()
    assert "sibling_name" in doc, (
        f"Route docstring's Response shape section omits 'sibling_name' -- the route "
        f"has returned this live-resolved key since PR#140 review finding 3. "
        f"Current docstring:\n{doc}"
    )


# ---------------------------------------------------------------------------
# Honesty contract: the docstring must note these 3 keys are FRESH
# request-time overlays, not part of the persisted raw_response schema.
# ---------------------------------------------------------------------------


def test_docstring_notes_overlay_keys_are_not_part_of_persisted_raw_response():
    doc = _route_docstring()
    flat = re.sub(r"\s+", " ", doc).lower()

    assert "raw_response" in flat, (
        f"Route docstring must reference raw_response so the reader can distinguish "
        f"the persisted schema from the additive overlay keys. Current docstring:\n{doc}"
    )
    overlay_honesty_phrases = ("not part of", "overlay", "not persisted", "not stored")
    assert any(phrase in flat for phrase in overlay_honesty_phrases), (
        "Route docstring must note that approval_status/candidate_name/sibling_name "
        "are FRESH request-time overlays, not part of the persisted raw_response "
        f"schema (PR#140 2nd /code-review finding 7). Current docstring:\n{doc}"
    )
