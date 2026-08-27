"""RED tests -- PR #139 /code-review remediation (Retirement Approval
Lifecycle, Phase 2 Cycle 2b delta).

Team-lead's independent /code-review on PR #139 returned 8 findings. FIVE
FIX items (all ret2-route's domain -- static/ai_advisor.js, templates/
ai_advisor.html, app.py) are covered here; the other three (F1, F2, F7) plus
F8's doc-honesty fix are ACCEPT-no-code-change rulings recorded in
DECISIONS.md under DE-RETIRE-APPROVAL-001 (ret2-doc's job, not tested here).

  F3/F4 [RETIRED, Cycle 2c, ret3-test]: originally lived here as
     JS-source-assertion tests locating retDispatchDecision's body via the
     hardcoded literal signature 'function retDispatchDecision(action,
     candidateId) {'. Cycle 2c's AC-7 (feature-plans/retirement-approval-
     polish.md, DE-RETIRE-POLISH-001) deliberately changed that signature to
     `(action, btnEl)` (the clicked-element rework, replacing the old
     card-scan-by-candidateId lookup) -- the literal-signature locator no
     longer matches, which is a STALE TEST, not a regression (root cause:
     the fixed literal outlived the signature it was pinned to; see
     feedback_rootcause_determines_role -- this is squarely a test-writer
     fix, not implementer's). The underlying F3 (reject confirmation colored
     --studio-neg, not success-green) and F4 (status chip text + BEM
     modifier class updated on success) behavioral guarantees are NOT lost
     -- they are re-verified, with a locator generalized to tolerate a
     changed parameter list, by tests/app/test_retirement_button_robustness.py
     ::TestPriorRoundFixesSurviveTheRework (Cycle 2c). Removed here rather
     than fixed in place to avoid maintaining two independent locators for
     the identical guarantee.
  F5 (light source-assertion, behavior-preserving cleanup): templates/
     ai_advisor.html's checklist off-hours holdings block has an
     UNREACHABLE {% else %} literal fallback (build_checklist ALWAYS sets
     unavailable_note when holdings_available is False -- see
     advisors/retirement_checklist.py's build_checklist, confirmed via the
     existing test_retirement_checklist.py suite) that ALSO uses a real
     em-dash (—) vs the Python constant _HOLDINGS_UNAVAILABLE_NOTE's
     ASCII "--" convention. Remove the dead else; render unavailable_note
     as the single source of truth.
  F6 (light source-assertion, dead-code removal): app.py's retirement
     live-join stamps `_rec["_decision"] = _decision` -- nothing downstream
     (template or JS) ever reads `_decision`; only approval_status and
     _checklist are consumed. Remove the unused stamp.
  Stale panel comment (light source-assertion, doc-only): templates/
     ai_advisor.html's panel header comment still claims "READ-ONLY. No
     operator lifecycle affordance of any kind here -- that's Cycle 2b." --
     now FALSE, since Cycle 2b's own buttons/status chip/checklist ARE that
     affordance. Correct the comment.

Mechanics note (F5/F6/stale-comment): plain source-presence/absence checks
over templates/ai_advisor.html and app.py -- no JS involved in this file's
remaining coverage (the JS-source-assertion pattern this file originally
used for F3/F4 lives on in test_retirement_button_robustness.py, which
carries its own brace-depth-walk extraction helper).

Expected state: GREEN -- F5/F6/stale-comment landed in Cycle 2b; F3/F4 were
retired in Cycle 2c (see above).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HTML_PATH = REPO_ROOT / "templates" / "ai_advisor.html"
_APP_PY_PATH = REPO_ROOT / "app.py"

_STALE_PANEL_COMMENT_CLAIM = "No operator lifecycle affordance of any kind here"

# The exact literal the dead {% else %} branch renders (F5) -- real em-dash
# (U+2014), deliberately distinct from _HOLDINGS_UNAVAILABLE_NOTE's ASCII
# "--" convention (advisors/retirement_checklist.py:50). Its presence in the
# template is itself the defect (a template-side duplicate of Python's own
# single source of truth, with a drifted dash character).
_DEAD_ELSE_LITERAL = "current holdings unavailable (off-hours) — view live positions in Composer"


def _html_source() -> str:
    if not _HTML_PATH.exists():
        pytest.fail(f"expected file not found: {_HTML_PATH}")
    return _HTML_PATH.read_text(encoding="utf-8")


def _app_py_source() -> str:
    if not _APP_PY_PATH.exists():
        pytest.fail(f"expected file not found: {_APP_PY_PATH}")
    return _APP_PY_PATH.read_text(encoding="utf-8")


# ===========================================================================
# F5: dead {% else %} holdings-unavailable fallback removed; unavailable_note
# is the single source of truth
# ===========================================================================


class TestF5ChecklistHoldingsNoteSingleSourceOfTruth:
    def test_dead_else_literal_no_longer_present(self):
        html = _html_source()
        assert _DEAD_ELSE_LITERAL not in html, (
            "F5: the dead {% else %} fallback literal (using a real em-dash, "
            "drifted from advisors.retirement_checklist._HOLDINGS_UNAVAILABLE_NOTE's "
            "ASCII '--' convention) is still present -- build_checklist ALWAYS sets "
            "unavailable_note when holdings_available is False, so this branch is "
            "unreachable dead code. Remove it; render unavailable_note directly."
        )

    def test_approved_card_with_unavailable_holdings_renders_the_python_note_verbatim(
        self, client, isolated_db
    ):
        """Behavioral proof (not just source-absence): a real approved card
        with unavailable holdings must render advisors.retirement_
        checklist._HOLDINGS_UNAVAILABLE_NOTE's actual text -- the single
        source of truth -- in the live page."""
        import database as db_module
        from advisors.retirement_checklist import _HOLDINGS_UNAVAILABLE_NOTE

        _seed_symphony_roster("cand-f5-1")  # no logic_holdings -> unavailable
        _seed_recommendation(candidate_id="cand-f5-1", sibling_id="sib-f5-1")
        db_module.upsert_retirement_decision("cand-f5-1", approval_status="approved")

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-checklist"' in body
        assert _HOLDINGS_UNAVAILABLE_NOTE in body, (
            f"Expected the checklist to render advisors.retirement_checklist."
            f"_HOLDINGS_UNAVAILABLE_NOTE ({_HOLDINGS_UNAVAILABLE_NOTE!r}) verbatim -- "
            "the Python constant must be the single source of truth for this text."
        )


# ===========================================================================
# F6: the unused _rec["_decision"] stamp is removed
# ===========================================================================


class TestF6UnusedDecisionStampRemoved:
    def test_app_py_no_longer_stamps_underscore_decision(self):
        source = _app_py_source()
        assert '_rec["_decision"] = _decision' not in source, (
            'F6: app.py still stamps _rec["_decision"] = _decision in the retirement '
            "live-join, but nothing downstream (template or JS) reads _decision -- "
            "only approval_status and _checklist are consumed. Remove the dead stamp."
        )

    def test_template_never_reads_underscore_decision(self):
        """Corroborating check: templates/ai_advisor.html must not read
        rec._decision either -- confirms the removal doesn't orphan a real
        consumer."""
        html = _html_source()
        assert "_decision" not in html, (
            "templates/ai_advisor.html references rec._decision somewhere -- if this "
            "is a genuine consumer, F6's stamp removal in app.py would be wrong; "
            "verify before removing the stamp."
        )


# ===========================================================================
# Stale panel comment: no longer claims the panel is read-only/no-affordance
# ===========================================================================


class TestStalePanelCommentCorrected:
    def test_read_only_no_affordance_claim_removed(self):
        """Normalized search (collapse whitespace AND strip HTML comment
        delimiters '<!--'/'-->' to spaces) -- the source's actual comment is
        THREE SEPARATE single-line <!-- --> tags, each wrapping a fragment
        of one continuous prose sentence ("...affordance   -->" / "<!-- of
        any kind here..."), so even a whitespace-only normalization still
        leaves the comment-close/open markup between "affordance" and "of
        any kind here", and the naive substring search passed VACUOUSLY
        (never matching, not because the claim was fixed) -- caught via
        checking WHICH tests actually passed (not just the pass/fail count)
        and fixed before this went RED for the wrong reason."""
        html = _html_source()
        normalized = re.sub(r"<!--|-->", " ", html)
        normalized = re.sub(r"\s+", " ", normalized)
        assert _STALE_PANEL_COMMENT_CLAIM not in normalized, (
            "templates/ai_advisor.html still contains the stale claim "
            f"{_STALE_PANEL_COMMENT_CLAIM!r} (whitespace-normalized) -- FALSE since "
            "Cycle 2b's own approve/reject buttons, status chip, and checklist ARE "
            "the operator lifecycle affordance this comment claims doesn't exist. "
            "Correct it."
        )

    def test_panel_still_has_its_real_2b_affordances(self, client, isolated_db):
        """Non-vacuity guard: the comment fix must not have accidentally
        stripped the real markup alongside the stale comment."""
        import database as db_module

        _seed_symphony_roster("cand-comment-1")
        _seed_recommendation(candidate_id="cand-comment-1", sibling_id="sib-comment-1")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-approve-btn"' in body
        assert 'data-testid="retirement-reject-btn"' in body
        assert 'data-testid="retirement-rec-status"' in body


# ===========================================================================
# Shared fixtures / helpers (duplicated per this repo's established
# fixtures-are-not-cross-file-shared convention -- see
# test_frontrunner_builder_route.py's own docstring on this point)
# ===========================================================================


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
        "explanation": None,
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


def _seed_symphony_roster(*symphony_ids):
    import database as db_module

    roster = {sid: {"name": sid, "logic_holdings": {}} for sid in symphony_ids}
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

    db_path = str(tmp_path / "test_retirement_review_remediation_pr139.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path
