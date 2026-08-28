"""RED tests -- Cycle 2c AC-1/AC-3: display NAMES instead of Composer hashes.

feature-plans/retirement-approval-polish.md:
  AC-1: the retirement recommendation card renders each symphony's resolved
        DISPLAY NAME (from database.load_state() / bot_state[id]["name"]) as
        the primary label, with the Composer hash available but secondary.
        Honest fallback to the raw hash when the name is unresolvable
        (symphony absent from bot_state). Resolution happens in
        ai_advisor_tab()'s existing prefetch (one load_state() per request,
        not per row).
  AC-3: the checklist block renders the candidate_name that build_checklist
        already resolves (currently computed and discarded) -- no more dead
        lookup; honest fallback when unresolvable.

.claude/tdd-handoff.md pins the exact behavioral contract this file drives
to GREEN. Fixture/helper pattern duplicated from tests/app/
test_retirement_review_remediation_pr139.py and tests/app/
test_retirement_panel_render.py per this repo's established
fixtures-are-not-cross-file-shared convention.

Expected state: RED until app.py's ai_advisor_tab() resolves display names
(AC-1) and templates/ai_advisor.html renders build_checklist's candidate_name
(AC-3).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# A resolved name deliberately unrelated in shape/substring to its hash --
# proves real resolution happened, not just an accidental echo of the hash.
_CANDIDATE_HASH = "cand-hash-aaa111"
_CANDIDATE_NAME = "Momentum Alpha Rotation"
_SIBLING_HASH = "sib-hash-bbb222"
_SIBLING_NAME = "Value Tilt Core"

_ORPHAN_CANDIDATE_HASH = "cand-orphan-ccc333"
_ORPHAN_SIBLING_HASH = "sib-orphan-ddd444"


# ---------------------------------------------------------------------------
# Shared fixtures / helpers (duplicated per this repo's established
# fixtures-are-not-cross-file-shared convention)
# ---------------------------------------------------------------------------


def _sample_raw_response(candidate_id="cand-sym", sibling_id="sib-sym"):
    return {
        "candidate_id": candidate_id,
        "sibling_id": sibling_id,
        "correlation": 0.80,
        "ci_lower": 0.72,
        "ci_upper": 0.86,
        "n_obs": 200,
        "candidate_composite": 0.20,
        "sibling_composite": 0.75,
        "candidate_metrics": {
            "annualized_return": 0.03,
            "sharpe": 0.2,
            "sortino": 0.25,
            "max_drawdown": -0.30,
            "calmar": 0.10,
        },
        "sibling_metrics": {
            "annualized_return": 0.18,
            "sharpe": 1.4,
            "sortino": 1.8,
            "max_drawdown": -0.06,
            "calmar": 3.0,
        },
        "uncertainty_gate_passed": True,
        "structural_redundancy_gate_passed": True,
        "stressed_correlation": 0.78,
        "holdings_overlap": None,
        "basis_label": "actual-traded (bot) daily returns",
        "explanation": "A concise explanation.",
    }


def _seed_recommendation(candidate_id="cand-sym", sibling_id="sib-sym"):
    import database as db_module

    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id),
    )


def _seed_named_roster(id_to_name: dict):
    """Seed bot_state with an explicit id->name mapping (distinct from the
    established _seed_symphony_roster(*ids) helper elsewhere, which always
    sets name == id -- this file specifically needs a name that differs from
    the hash to prove real resolution took place)."""
    import database as db_module

    roster = {sid: {"name": name, "logic_holdings": {}} for sid, name in id_to_name.items()}
    db_module.save_state(roster)


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_display_names.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ===========================================================================
# AC-1: card display name (resolvable + honest hash fallback)
# ===========================================================================


class TestCardDisplayName:
    def test_card_shows_resolved_candidate_display_name(self, client, isolated_db):
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert 'data-testid="retirement-recommendation-card"' in body
        assert _CANDIDATE_NAME in body, (
            f"Expected the resolved candidate display name {_CANDIDATE_NAME!r} to "
            "appear on the card -- it must NOT just echo the raw Composer hash."
        )

    def test_card_shows_resolved_sibling_display_name(self, client, isolated_db):
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert _SIBLING_NAME in body, (
            f"Expected the resolved sibling display name {_SIBLING_NAME!r} to appear on the card."
        )

    def test_card_still_shows_raw_candidate_hash_secondarily(self, client, isolated_db):
        """The plan requires the hash stay AVAILABLE, just secondary (e.g.
        tooltip/muted) -- must not disappear from the DOM entirely. This also
        protects tests/app/test_retirement_recommendations_panel.py's
        existing 'assert "cand-panel-1" in body' style coverage."""
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert _CANDIDATE_HASH in body, (
            "The raw Composer hash must still be present somewhere in the card "
            "(secondary -- tooltip/muted), never dropped entirely."
        )

    def test_card_still_shows_raw_sibling_hash_secondarily(self, client, isolated_db):
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert _SIBLING_HASH in body

    def test_card_falls_back_to_raw_hash_when_candidate_unresolvable(self, client, isolated_db):
        """Edge case: symphony flagged but absent from live bot_state
        (renamed/removed since the night it was flagged) -> honest hash
        fallback, never a crash or blank."""
        _seed_named_roster({_ORPHAN_SIBLING_HASH: "Kept Sibling"})  # candidate absent
        _seed_recommendation(candidate_id=_ORPHAN_CANDIDATE_HASH, sibling_id=_ORPHAN_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert 'data-testid="retirement-recommendation-card"' in body
        assert _ORPHAN_CANDIDATE_HASH in body, (
            "An unresolvable candidate must fall back to rendering its raw hash, "
            "never a blank/crash."
        )

    def test_card_falls_back_to_raw_hash_when_sibling_unresolvable(self, client, isolated_db):
        _seed_named_roster({_ORPHAN_CANDIDATE_HASH: "Kept Candidate"})  # sibling absent
        _seed_recommendation(candidate_id=_ORPHAN_CANDIDATE_HASH, sibling_id=_ORPHAN_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert _ORPHAN_SIBLING_HASH in body

    def test_card_never_renders_literal_none_when_both_unresolvable(self, client, isolated_db):
        """Adversarial: bot_state has NEITHER symphony at all (e.g. save_state
        never ran, or both were removed). A naive `{{ candidate_name }}`
        Jinja render of a Python None would literally print the text "None"
        -- must never happen."""
        import database as db_module

        db_module.save_state({})  # empty roster -- neither side resolvable
        _seed_recommendation(candidate_id=_ORPHAN_CANDIDATE_HASH, sibling_id=_ORPHAN_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        section = _extract_panel_section(body)
        assert section, "retirement-recommendations-panel section not found"
        assert re.search(r"[^A-Za-z]None[^A-Za-z]", section) is None, (
            "The panel must never render the literal Python string 'None' when a "
            f"name is unresolvable. Section: {section!r}"
        )

    def test_pending_card_also_gets_name_resolution(self, client, isolated_db):
        """Non-vacuity guard: AC-1 is NOT conditioned on approval_status --
        the existing bot_state fetch was gated behind '_ret_any_approved'
        (only for the checklist), which would leave an all-pending batch's
        cards showing raw hashes only. A pending card must ALSO show the
        resolved name."""
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert 'data-testid="retirement-rec-status">' in body
        assert "Pending" in body  # default approval_status
        assert _CANDIDATE_NAME in body, (
            "A PENDING card (not yet approved) must still show the resolved "
            "display name -- AC-1 is unconditional on approval_status."
        )

    def test_candidate_name_html_escaped_against_xss(self, client, isolated_db):
        xss_name = "<script>alert(1)</script>"
        _seed_named_roster({_CANDIDATE_HASH: xss_name, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert "<script>alert(1)</script>" not in body, (
            "A malicious symphony display name must be HTML-escaped, never rendered raw."
        )
        assert "&lt;script&gt;" in body

    def test_bot_state_load_failure_degrades_to_hash_fallback_without_crashing(
        self, client, isolated_db, monkeypatch
    ):
        """Edge case: bot_state load failure during render -> degrade to
        hashes, never abort the rec."""
        import database as db_module

        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated load_state failure")

        monkeypatch.setattr(db_module, "load_state", _raise)

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body
        assert _CANDIDATE_HASH in body

    def test_load_state_call_count_does_not_scale_with_recommendation_count(
        self, client, isolated_db, monkeypatch
    ):
        """AC-1: 'one load_state() per request, not per row.' Proven
        DIFFERENTIALLY (1 rec vs 3 recs must produce the SAME call count) so
        this test is immune to however many OTHER unrelated panels on the
        same page also call database.load_state() -- those contribute a
        constant baseline present in both scenarios."""
        import database as db_module

        real_load_state = db_module.load_state
        counts: dict[str, int] = {}

        def _make_counting_load_state(key):
            def _counting(*args, **kwargs):
                counts[key] = counts.get(key, 0) + 1
                return real_load_state(*args, **kwargs)

            return _counting

        # Scenario A: 1 recommendation.
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)
        monkeypatch.setattr(db_module, "load_state", _make_counting_load_state("one"))
        resp_one = client.get("/ai-advisor")
        assert resp_one.status_code == 200

        # Scenario B: 3 recommendations, fresh isolated DB.
        import database as db_module2  # same module object, re-imported for clarity
        from database import init_db, run_migrations

        db_path_2 = str(pathlib.Path(isolated_db).parent / "test_retirement_display_names_3.db")
        monkeypatch.setattr(db_module2, "DB_FILE", db_path_2)
        init_db()
        run_migrations()
        roster = {_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME}
        pairs = [
            (_CANDIDATE_HASH, _SIBLING_HASH),
            ("cand-extra-1", "sib-extra-1"),
            ("cand-extra-2", "sib-extra-2"),
        ]
        for cid, sid in pairs:
            roster.setdefault(cid, {"name": cid, "logic_holdings": {}})
            roster.setdefault(sid, {"name": sid, "logic_holdings": {}})
        db_module2.save_state(roster)
        for cid, sid in pairs:
            _seed_recommendation(candidate_id=cid, sibling_id=sid)
        monkeypatch.setattr(db_module2, "load_state", _make_counting_load_state("three"))
        resp_three = client.get("/ai-advisor")
        assert resp_three.status_code == 200

        assert counts.get("one", 0) > 0, "Expected at least one load_state() call for 1 rec."
        assert counts["one"] == counts["three"], (
            f"database.load_state() was called {counts['one']} time(s) for 1 recommendation "
            f"but {counts['three']} time(s) for 3 recommendations -- it must be called once "
            "per REQUEST, not once per row."
        )


# ===========================================================================
# Cycle 2d AC-1 (PR#140 2nd /code-review finding 1, SELF-INTRODUCED in 2c):
# 3-tier fallback chain -- freshly-resolved name -> persisted tick-time
# candidate_name/sibling_name (AC-2's Cycle-2c tick enrichment already
# stamps this into raw_response) -> raw hash (last resort only). The 2c
# behavior tested above (TestCardDisplayName's "falls back to raw hash when
# unresolvable" tests) used a fixture with NO persisted candidate_name/
# sibling_name key at all -- that "never had a name" case is UNCHANGED by
# this fix and stays covered above. This section adds the NEW middle tier:
# a symphony that WAS named (tick-time enrichment ran) but has since left
# bot_state must show the persisted name, not regress straight to the hash.
# ===========================================================================

_PERSISTED_CANDIDATE_NAME = "Persisted Tick-Time Candidate"
_PERSISTED_SIBLING_NAME = "Persisted Tick-Time Sibling"


def _seed_recommendation_with_persisted_names(
    candidate_id=_CANDIDATE_HASH,
    sibling_id=_SIBLING_HASH,
    *,
    candidate_name=None,
    sibling_name=None,
):
    """Simulates a raw_response shaped exactly like AC-2's Cycle-2c tick-time
    enrichment already persisted -- candidate_name/sibling_name baked into
    the DB row, deliberately independent of whatever the CURRENT bot_state
    says (the whole point of this fallback tier)."""
    import database as db_module

    raw = _sample_raw_response(candidate_id, sibling_id)
    if candidate_name is not None:
        raw["candidate_name"] = candidate_name
    if sibling_name is not None:
        raw["sibling_name"] = sibling_name
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=raw,
    )


class TestPersistedNameFallbackWhenRemovedFromBotState:
    def test_candidate_removed_from_bot_state_preserves_persisted_name_not_hash(
        self, client, isolated_db
    ):
        """The core finding-1 scenario: the candidate has left bot_state
        entirely (renamed/removed/retired) since the tick persisted a
        friendly name -- the card must show the PERSISTED name, never
        regress to the raw hash while that name is available."""
        _seed_named_roster({_SIBLING_HASH: _SIBLING_NAME})  # candidate absent
        _seed_recommendation_with_persisted_names(
            _ORPHAN_CANDIDATE_HASH, _SIBLING_HASH, candidate_name=_PERSISTED_CANDIDATE_NAME
        )

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert _PERSISTED_CANDIDATE_NAME in body, (
            f"AC-1 (2d): a candidate removed from bot_state since the tick must fall "
            f"back to its PERSISTED tick-time name {_PERSISTED_CANDIDATE_NAME!r}, not "
            "straight to the raw hash."
        )

    def test_sibling_removed_from_bot_state_preserves_persisted_name_not_hash(
        self, client, isolated_db
    ):
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME})  # sibling absent
        _seed_recommendation_with_persisted_names(
            _CANDIDATE_HASH, _ORPHAN_SIBLING_HASH, sibling_name=_PERSISTED_SIBLING_NAME
        )

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert _PERSISTED_SIBLING_NAME in body, (
            f"Expected the persisted sibling name {_PERSISTED_SIBLING_NAME!r} to be "
            "preserved when the sibling has left bot_state."
        )

    def test_fresh_resolution_still_wins_over_a_stale_persisted_name(self, client, isolated_db):
        """Precedence check: fresh resolution is tier 1 -- a persisted
        (possibly stale) name must never override a symphony that IS
        currently resolvable in bot_state."""
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation_with_persisted_names(
            _CANDIDATE_HASH, _SIBLING_HASH, candidate_name="Old Stale Persisted Name"
        )

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert _CANDIDATE_NAME in body, "Fresh bot_state resolution must win."
        assert "Old Stale Persisted Name" not in body, (
            "A stale persisted name must never override a symphony that IS "
            "currently resolvable in bot_state."
        )

    def test_last_resort_hash_fallback_still_applies_when_no_persisted_name_exists(
        self, client, isolated_db
    ):
        """Non-regression anchor: the ORIGINAL (2c) last-resort hash fallback
        must still hold for the genuinely 'never had a name' case -- no
        persisted candidate_name key at all AND unresolvable in bot_state
        (mirrors TestCardDisplayName's existing coverage above, re-asserted
        here as the 3rd tier of the SAME chain this section is documenting)."""
        _seed_named_roster({})  # nobody resolvable
        _seed_recommendation(candidate_id=_ORPHAN_CANDIDATE_HASH, sibling_id=_ORPHAN_SIBLING_HASH)

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert _ORPHAN_CANDIDATE_HASH in body, (
            "With neither a fresh nor a persisted name available, the card must "
            "fall back to the raw hash as the last resort."
        )

    def test_persisted_fallback_name_html_escaped_against_xss(self, client, isolated_db):
        xss_name = "<img src=x onerror=alert(1)>"
        _seed_named_roster({_SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation_with_persisted_names(
            _ORPHAN_CANDIDATE_HASH, _SIBLING_HASH, candidate_name=xss_name
        )

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert xss_name not in body, (
            "A malicious persisted candidate name must be HTML-escaped, never rendered raw."
        )
        assert "&lt;img" in body


# ===========================================================================
# AC-3: checklist display name (resolvable + honest hash fallback)
# ===========================================================================


class TestChecklistDisplayName:
    def test_checklist_shows_resolved_candidate_name(self, client, isolated_db):
        import database as db_module

        _seed_named_roster(
            {
                _CANDIDATE_HASH: _CANDIDATE_NAME,
                _SIBLING_HASH: _SIBLING_NAME,
            }
        )
        # Give the candidate real holdings so the checklist's holdings-available
        # branch renders too (not load-bearing for this assertion, but keeps
        # the fixture realistic).
        state = db_module.load_state()
        state[_CANDIDATE_HASH]["logic_holdings"] = {"SPY": 1.0}
        db_module.save_state(state)

        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)
        db_module.upsert_retirement_decision(_CANDIDATE_HASH, approval_status="approved")

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert 'data-testid="retirement-checklist"' in body
        checklist_idx = body.find('data-testid="retirement-checklist"')
        window = body[checklist_idx : checklist_idx + 3000]
        assert _CANDIDATE_NAME in window, (
            f"Expected the checklist to render the resolved candidate name "
            f"{_CANDIDATE_NAME!r} (build_checklist already computes this as "
            "candidate_name -- it must not be discarded)."
        )

    def test_checklist_falls_back_to_hash_when_name_unresolvable(self, client, isolated_db):
        import database as db_module

        # Recommend a candidate that is NOT present in bot_state at all.
        db_module.save_state({})
        _seed_recommendation(candidate_id=_ORPHAN_CANDIDATE_HASH, sibling_id=_ORPHAN_SIBLING_HASH)
        db_module.upsert_retirement_decision(_ORPHAN_CANDIDATE_HASH, approval_status="approved")

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        assert 'data-testid="retirement-checklist"' in body
        checklist_idx = body.find('data-testid="retirement-checklist"')
        window = body[checklist_idx : checklist_idx + 3000]
        assert _ORPHAN_CANDIDATE_HASH in window, (
            "When the candidate's name is unresolvable, the checklist must fall back "
            "to rendering the raw candidate_id/hash, never a blank."
        )
        assert re.search(r"[^A-Za-z]None[^A-Za-z]", window) is None, (
            f"Checklist must never render the literal 'None'. Window: {window!r}"
        )

    def test_checklist_candidate_name_html_escaped_against_xss(self, client, isolated_db):
        import database as db_module

        xss_name = "<script>alert(2)</script>"
        _seed_named_roster({_CANDIDATE_HASH: xss_name, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH)
        db_module.upsert_retirement_decision(_CANDIDATE_HASH, approval_status="approved")

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        assert "<script>alert(2)</script>" not in body
        assert "&lt;script&gt;" in body


# ---------------------------------------------------------------------------
# Section-scoping helper (duplicated from tests/app/
# test_retirement_recommendations_panel.py's own _extract_panel_section --
# same tag-depth-walk rationale, not a fixed-char-window guess).
# ---------------------------------------------------------------------------

_SECTION_TAG_RE = re.compile(r"<(/?)section\b[^>]*>", re.IGNORECASE)


def _extract_panel_section(body: str) -> str:
    marker = 'data-testid="retirement-recommendations-panel"'
    marker_idx = body.find(marker)
    if marker_idx == -1:
        return ""

    tag_start = body.rfind("<section", 0, marker_idx)
    if tag_start == -1:
        return ""

    depth = 0
    for m in _SECTION_TAG_RE.finditer(body, tag_start):
        if m.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return body[tag_start : m.end()]
    return ""
