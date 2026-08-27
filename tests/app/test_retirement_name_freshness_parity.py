"""RED tests -- PR #140 /code-review finding 3: API/panel display-name
parity for a renamed symphony.

feature-plans/retirement-approval-polish.md's AC-6 requires the API and the
AI Advisor panel to agree on decision state for the same candidate_id; this
finding extends that invariant to DISPLAY NAME. AC-2's tick-time enrichment
persists candidate_name/sibling_name into raw_response (needed so AC-4's
reuse gate can detect a rename since the prior night -- see
test_retirement_explanation_reuse.py's _canonical_evidence_snapshot design).
But GET /api/retirement-recommendations returns raw_response verbatim
(_fetch_retirement_recommendations's own documented contract), while
ai_advisor_tab()'s panel resolves the display name FRESH at request time
from CURRENT bot_state (AC-1). If a symphony is renamed sometime between
the 03:45 tick and a later request, the API shows the STALE 03:45 name
while the panel shows the CURRENT name for the SAME candidate_id --
contradicting AC-6's own "the API and panel never disagree" principle.

Team-lead's two suggested fix directions (either is acceptable -- this file
tests the OBSERVABLE invariant only, not which function does the
resolving): (a) the API re-resolves fresh (consistent with the panel), or
(b) the reuse gate compares against a non-display internal field so
raw_response never carries the display name into the API at all. My
recommendation to team-lead (relayed to ret3-route): (a), implemented by
having _fetch_retirement_recommendations() (shared by both the API route
and the panel prefetch) re-resolve candidate_name/sibling_name fresh from
CURRENT bot_state before returning, overwriting whatever the persisted
raw_response carried. This is orthogonal to AC-4's reuse gate, which reads
the raw advisor_observations DB row directly via
database.get_advisor_observations_for_role -- a separate code path this
in-memory overwrite never touches, so the tick-time value the reuse gate
needs for rename-detection is unaffected.

Fixture/helper pattern duplicated from sibling retirement test files per
this repo's established fixtures-are-not-cross-file-shared convention.

Expected state: RED until app.py closes this parity gap.
"""

from __future__ import annotations

import re

import pytest

_CANDIDATE_HASH = "cand-parity-aaa"
_SIBLING_HASH = "sib-parity-bbb"

_TICK_TIME_CANDIDATE_NAME = "Old Tick-Time Candidate Name"
_CURRENT_CANDIDATE_NAME = "New Renamed Candidate"
_TICK_TIME_SIBLING_NAME = "Old Tick-Time Sibling Name"
_CURRENT_SIBLING_NAME = "New Renamed Sibling"


def _sample_raw_response(
    candidate_id=_CANDIDATE_HASH,
    sibling_id=_SIBLING_HASH,
    *,
    candidate_name=_TICK_TIME_CANDIDATE_NAME,
    sibling_name=_TICK_TIME_SIBLING_NAME,
):
    """Shaped like a row AC-2's tick-time enrichment would have persisted
    last night -- candidate_name/sibling_name baked in as the TICK-TIME
    value, deliberately possibly stale by the time a later request reads
    it back."""
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
        "candidate_name": candidate_name,
        "sibling_name": sibling_name,
    }


def _seed_recommendation(**kwargs):
    import database as db_module

    raw = _sample_raw_response(**kwargs)
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=raw["candidate_id"],
        symphony_id=raw["candidate_id"],
        verdict="retire_candidate",
        raw_response=raw,
    )


def _seed_named_roster(id_to_name: dict):
    import database as db_module

    roster = {sid: {"name": name, "logic_holdings": {}} for sid, name in id_to_name.items()}
    db_module.save_state(roster)


def _api_row_for(candidate_id: str) -> dict:
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/api/retirement-recommendations")
    data = resp.get_json()
    row = next((r for r in data["recommendations"] if r.get("candidate_id") == candidate_id), None)
    assert row is not None, f"No API row found for candidate_id={candidate_id!r}"
    return row


def _panel_body() -> str:
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/ai-advisor")
    return resp.get_data(as_text=True)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_name_freshness_parity.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


class TestApiAndPanelAgreeOnDisplayName:
    def test_api_reflects_the_current_name_after_a_candidate_rename(self, isolated_db):
        """The core finding-3 scenario: the tick persisted the OLD name last
        night; the symphony was renamed since. The API must show the NEW
        (current) name, not the stale persisted one."""
        _seed_recommendation(candidate_name=_TICK_TIME_CANDIDATE_NAME)
        # Mid-day rename since the 03:45 tick.
        _seed_named_roster(
            {_CANDIDATE_HASH: _CURRENT_CANDIDATE_NAME, _SIBLING_HASH: _TICK_TIME_SIBLING_NAME}
        )

        row = _api_row_for(_CANDIDATE_HASH)

        assert row.get("candidate_name") == _CURRENT_CANDIDATE_NAME, (
            f"Expected the API to reflect the CURRENT candidate name "
            f"{_CURRENT_CANDIDATE_NAME!r}, got {row.get('candidate_name')!r} -- the "
            "API must never leak the stale 03:45 tick-time name."
        )

    def test_api_never_leaks_the_stale_tick_time_name_verbatim(self, isolated_db):
        """Stronger than the prior test: explicitly proves the OLD name
        string is entirely absent from the API row, not just that a
        DIFFERENT (possibly also-wrong) value happens to be present."""
        _seed_recommendation(candidate_name=_TICK_TIME_CANDIDATE_NAME)
        _seed_named_roster(
            {_CANDIDATE_HASH: _CURRENT_CANDIDATE_NAME, _SIBLING_HASH: _TICK_TIME_SIBLING_NAME}
        )

        row = _api_row_for(_CANDIDATE_HASH)

        assert row.get("candidate_name") != _TICK_TIME_CANDIDATE_NAME, (
            f"The API still returns the stale tick-time name "
            f"{_TICK_TIME_CANDIDATE_NAME!r} for a since-renamed candidate."
        )

    def test_api_reflects_the_current_name_after_a_sibling_rename(self, isolated_db):
        _seed_recommendation(sibling_name=_TICK_TIME_SIBLING_NAME)
        _seed_named_roster(
            {_CANDIDATE_HASH: _TICK_TIME_CANDIDATE_NAME, _SIBLING_HASH: _CURRENT_SIBLING_NAME}
        )

        row = _api_row_for(_CANDIDATE_HASH)

        assert row.get("sibling_name") == _CURRENT_SIBLING_NAME, (
            f"Expected the API to reflect the CURRENT sibling name "
            f"{_CURRENT_SIBLING_NAME!r}, got {row.get('sibling_name')!r}."
        )

    def test_api_and_panel_show_the_identical_name_after_a_rename(self, isolated_db):
        """The literal cross-surface parity assertion: the API's name and
        the panel's rendered name for the SAME candidate_id must be
        identical -- neither surface may show a name the other doesn't."""
        _seed_recommendation(candidate_name=_TICK_TIME_CANDIDATE_NAME)
        _seed_named_roster(
            {_CANDIDATE_HASH: _CURRENT_CANDIDATE_NAME, _SIBLING_HASH: _TICK_TIME_SIBLING_NAME}
        )

        api_row = _api_row_for(_CANDIDATE_HASH)
        panel_body = _panel_body()

        assert api_row.get("candidate_name") == _CURRENT_CANDIDATE_NAME
        assert _CURRENT_CANDIDATE_NAME in panel_body, (
            "Expected the panel to render the current (renamed) candidate name."
        )
        assert _TICK_TIME_CANDIDATE_NAME not in panel_body, (
            "The panel must never render the stale tick-time name either -- both "
            "surfaces must agree on the SAME current value."
        )

    def test_api_and_panel_agree_when_no_rename_occurred(self, isolated_db):
        """Non-regression baseline: the ordinary, unremarkable case (no
        rename since the tick) must trivially still show the same name on
        both surfaces."""
        _seed_recommendation(
            candidate_name=_TICK_TIME_CANDIDATE_NAME, sibling_name=_TICK_TIME_SIBLING_NAME
        )
        _seed_named_roster(
            {_CANDIDATE_HASH: _TICK_TIME_CANDIDATE_NAME, _SIBLING_HASH: _TICK_TIME_SIBLING_NAME}
        )

        api_row = _api_row_for(_CANDIDATE_HASH)
        panel_body = _panel_body()

        assert api_row.get("candidate_name") == _TICK_TIME_CANDIDATE_NAME
        assert _TICK_TIME_CANDIDATE_NAME in panel_body

    def test_api_and_panel_agree_when_candidate_becomes_unresolvable(self, isolated_db):
        """Edge case: the candidate is REMOVED from bot_state entirely after
        the tick persisted a friendly name (e.g. the symphony was deleted).
        Both surfaces must fall back to the raw hash CONSISTENTLY -- never
        one showing a stale friendly name while the other shows the hash."""
        _seed_recommendation(candidate_name=_TICK_TIME_CANDIDATE_NAME)
        _seed_named_roster({_SIBLING_HASH: _TICK_TIME_SIBLING_NAME})  # candidate absent

        api_row = _api_row_for(_CANDIDATE_HASH)
        panel_body = _panel_body()

        assert api_row.get("candidate_name") != _TICK_TIME_CANDIDATE_NAME, (
            "An unresolvable candidate must not leave the stale friendly name in the API response."
        )
        assert re.search(r"[^A-Za-z]None[^A-Za-z]", str(api_row.get("candidate_name"))) is None
        # The panel already falls back to the hash (AC-1, covered by
        # test_retirement_display_names.py) -- corroborate the API does too,
        # by asserting the hash itself is what the API now shows.
        assert api_row.get("candidate_name") == _CANDIDATE_HASH, (
            f"Expected the API to fall back to the raw hash {_CANDIDATE_HASH!r} for "
            f"an unresolvable candidate, got {api_row.get('candidate_name')!r}."
        )
        assert _CANDIDATE_HASH in panel_body
