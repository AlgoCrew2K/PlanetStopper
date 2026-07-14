"""RED tests — advisors.frontrunner_builder.approve_frontrunner_proposal
orchestration coverage (feature-plans/frontrunner-builder.md AC-9/AC-11/AC-12,
Batch C).

GAP THIS FILE CLOSES: approve_frontrunner_proposal (frontrunner_builder.py:1188)
had ZERO test coverage before this batch (verified: `grep -rln
"approve_frontrunner_proposal" tests/` returned nothing on bd165a5). The 13
"Composer write client" tests already in the suite
(tests/advisors/test_composer_draft_client.py) only cover
composer_draft_client.save_symphony/verify_undeployed at the HTTP-mock level
— never the orchestration function that calls them, persists proposal status,
and enforces the no-trade boundary's belt-and-suspenders verify step. AC-11's
"Composer create fails (4xx/5xx malformed raw_value, quota, auth) → surface
the error on the approval item, do NOT mark uploaded, do NOT retry blindly"
and AC-9's "verifies the created symphony reads back as zero-allocation ...
before marking the approval 'uploaded'" are both entirely untested without
this file.

AC-12 ("a per-account symphony-count guard") CONTRACT (team-lead-confirmed
2026-07-11, after composer-api-researcher triangulation ruled out a
Composer-side account-wide read — fetch_symphony_stats is DEPLOYED-scoped
and cannot see the undeployed symphonies this feature creates, and Composer
documents no per-account symphony cap): the guard is a SELF-IMPOSED LOCAL
count of `frontrunner_proposals` rows already marked `approval_status=
'uploaded'`, checked in approve_frontrunner_proposal BEFORE calling
composer_draft_client.save_symphony. This file names the read accessor
`database.count_uploaded_frontrunner_proposals() -> int` — the implementer
picks the exact cap constant name/value; these tests patch the COUNT to a
value far above any reasonable conservative cap (10_000) for the
over-the-cap case, and to 0 for the under-the-cap case, so they don't
couple to the implementer's chosen constant.

PATCH-TARGET STRATEGY (mirrors test_frontrunner_gate_wiring.py's documented
convention): patch collaborators at their ORIGIN module —
database.get_frontrunner_proposal, database.update_frontrunner_proposal_status,
database.insert_advisor_observation, database.count_uploaded_frontrunner_proposals,
advisors.composer_draft_client.save_symphony,
advisors.composer_draft_client.verify_undeployed — never
approve_frontrunner_proposal's own module attributes (it lazy-imports
`database` per-call and imports `composer_draft_client` at function top,
CC-2 pattern; patching the origin module works regardless of import style).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


def _make_proposal(
    *,
    proposal_id: int = 1,
    symphony_id: str = "test-symphony-id",
    approval_status: str = "pending",
    created_symphony_id: str | None = None,
) -> dict:
    """A minimal but column-complete frontrunner_proposals row dict, matching
    database._FRONTRUNNER_PROPOSAL_COLUMNS' shape (database.py:3278-3289)."""
    return {
        "id": proposal_id,
        "created_at": "2026-07-11T00:00:00",
        "updated_at": "2026-07-11T00:00:00",
        "symphony_id": symphony_id,
        "proposal_source": "frontrunner_builder",
        "approval_status": approval_status,
        "candidate_tree": {"step": "root", "children": []},
        "metrics_json": {"candidate_cagr": 0.1},
        "created_symphony_id": created_symphony_id,
        "error_message": None,
    }


def _draft_success(symphony_id: str = "new-symphony-123", version_id: str = "v1"):
    from advisors.composer_draft_client import DraftResult

    return DraftResult(success=True, symphony_id=symphony_id, version_id=version_id, error=None)


def _draft_failure(error: str = "HTTP 400: malformed raw_value"):
    from advisors.composer_draft_client import DraftResult

    return DraftResult(success=False, symphony_id=None, version_id=None, error=error)


# ---------------------------------------------------------------------------
# AC-9/AC-11: proposal-not-found and idempotency.
# ---------------------------------------------------------------------------


def test_approve_frontrunner_proposal_returns_failure_when_proposal_not_found(fbld):
    """A proposal_id with no matching row must fail cleanly — no Composer
    call, no crash, an explicit reason surfaced to the caller (the operator-
    driven /approve route)."""
    with (
        patch("database.get_frontrunner_proposal", return_value=None),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
    ):
        result = fbld.approve_frontrunner_proposal(999)

    assert result.success is False
    assert result.error, "a not-found proposal must surface a non-empty error reason"
    mock_save.assert_not_called()


def test_approve_frontrunner_proposal_is_idempotent_for_an_already_uploaded_proposal(fbld):
    """AC-11 'Duplicate uploads — approving the same candidate twice must not
    create two symphonies.' A proposal already marked uploaded (with a
    recorded created_symphony_id) must short-circuit to a success result
    echoing that id, WITHOUT issuing a second Composer create call."""
    proposal = _make_proposal(approval_status="uploaded", created_symphony_id="already-created-456")
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is True
    assert result.symphony_id == "already-created-456"
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# AC-11: Composer create failure surfaced, never swallowed, never marked uploaded.
# ---------------------------------------------------------------------------


def test_approve_frontrunner_proposal_surfaces_a_save_symphony_failure_without_marking_uploaded(
    fbld,
):
    """AC-11: 'Composer create fails (4xx/5xx malformed raw_value, quota,
    auth) → surface the error on the approval item, do NOT mark uploaded, do
    NOT retry blindly.' A save_symphony failure must propagate a non-empty
    error onto the ApprovalResult and the proposal's persisted status update
    must NEVER be 'uploaded' — proving the failure is surfaced, not
    swallowed into a silent success."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch(
            "advisors.composer_draft_client.save_symphony",
            return_value=_draft_failure("HTTP 400: malformed raw_value"),
        ),
        patch("advisors.composer_draft_client.verify_undeployed") as mock_verify,
        patch("database.update_frontrunner_proposal_status") as mock_update,
        patch("database.insert_advisor_observation") as mock_obs,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is False
    assert result.error and "400" in result.error, (
        f"the HTTP 400 failure reason must be surfaced on the result, got error={result.error!r}"
    )
    mock_verify.assert_not_called()  # never verify a symphony that was never created
    assert mock_update.called, "the failure must still be persisted (not silently dropped)"
    for call in mock_update.call_args_list:
        _, kwargs = call
        assert kwargs.get("approval_status") != "uploaded", (
            f"update_frontrunner_proposal_status was called with "
            f"approval_status='uploaded' after a save_symphony FAILURE — "
            f"AC-11 forbids marking uploaded on a create failure: {kwargs!r}"
        )
    mock_obs.assert_not_called()  # AC-9's uploaded-observation is accept-only


def test_approve_frontrunner_proposal_refuses_to_mark_uploaded_when_verify_undeployed_fails(fbld):
    """AC-9 belt-and-suspenders: 'It then verifies the created symphony reads
    back as zero-allocation/undeployed ... before marking the approval
    "uploaded".' If verify_undeployed returns False after a successful
    create, the proposal must NOT be marked uploaded — a created-but-
    unverified symphony is a NO-TRADE-BOUNDARY risk, not a cosmetic detail."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch(
            "advisors.composer_draft_client.save_symphony",
            return_value=_draft_success(symphony_id="risky-symphony-789"),
        ),
        patch("advisors.composer_draft_client.verify_undeployed", return_value=False),
        patch("database.update_frontrunner_proposal_status") as mock_update,
        patch("database.insert_advisor_observation") as mock_obs,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is False
    assert result.error == "verify_undeployed_failed"
    assert mock_update.called
    for call in mock_update.call_args_list:
        _, kwargs = call
        assert kwargs.get("approval_status") != "uploaded", (
            f"update_frontrunner_proposal_status was called with "
            f"approval_status='uploaded' after verify_undeployed returned "
            f"False — this is the belt-and-suspenders no-trade check, it "
            f"must be load-bearing: {kwargs!r}"
        )
    mock_obs.assert_not_called()


# ---------------------------------------------------------------------------
# AC-9: the positive path — create, verify, mark uploaded, audit record.
# ---------------------------------------------------------------------------


def test_approve_frontrunner_proposal_success_path_marks_uploaded_and_persists_observation(fbld):
    """The full happy path: save_symphony succeeds, verify_undeployed
    confirms zero allocation → the proposal is marked 'uploaded' with the
    new created_symphony_id AND an audit observation is persisted (mirrors
    the existing code's own insert_advisor_observation call for AC-9's
    upload audit trail)."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch(
            "advisors.composer_draft_client.save_symphony",
            return_value=_draft_success(symphony_id="clean-symphony-321"),
        ),
        patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
        patch("database.update_frontrunner_proposal_status") as mock_update,
        patch("database.insert_advisor_observation") as mock_obs,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is True
    assert result.symphony_id == "clean-symphony-321"

    uploaded_calls = [
        kwargs
        for _, kwargs in mock_update.call_args_list
        if kwargs.get("approval_status") == "uploaded"
    ]
    assert uploaded_calls, (
        "expected an update_frontrunner_proposal_status(approval_status='uploaded', ...) call"
    )
    assert uploaded_calls[0].get("created_symphony_id") == "clean-symphony-321"

    assert mock_obs.called
    _, obs_kwargs = mock_obs.call_args
    assert obs_kwargs.get("advisor_role") == "FRONTRUNNER_BUILDER"
    assert obs_kwargs.get("symphony_id") == proposal["symphony_id"]


# ---------------------------------------------------------------------------
# AC-12: per-account symphony-count guard (local-count floor).
# ---------------------------------------------------------------------------


def test_approve_frontrunner_proposal_skips_create_when_upload_count_is_at_the_cap(fbld):
    """AC-12: 'a per-account symphony-count guard (skip create if near any
    Composer limit).' Wave-1 floor (team-lead-confirmed, composer-api
    research ruled out an account-wide Composer read): a LOCAL count of
    already-uploaded frontrunner_proposals rows, checked BEFORE
    save_symphony. A count far above any reasonable conservative cap
    (10_000) must skip the create entirely — never call save_symphony, never
    mark uploaded, surface a clear reason distinct from a Composer HTTP
    failure."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch("database.count_uploaded_frontrunner_proposals", return_value=10_000),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
        patch("database.update_frontrunner_proposal_status") as mock_update,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is False
    mock_save.assert_not_called()
    for call in mock_update.call_args_list:
        _, kwargs = call
        assert kwargs.get("approval_status") != "uploaded", (
            "a count-capped approval must never be marked uploaded"
        )


def test_approve_frontrunner_proposal_proceeds_when_upload_count_is_below_the_cap(fbld):
    """Negative-of-the-guard: a low upload count (0) must NOT block a
    legitimate approval — proves the guard is a genuine threshold, not an
    accidental always-skip."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch("database.count_uploaded_frontrunner_proposals", return_value=0),
        patch(
            "advisors.composer_draft_client.save_symphony",
            return_value=_draft_success(symphony_id="under-cap-symphony"),
        ),
        patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
        patch("database.update_frontrunner_proposal_status"),
        patch("database.insert_advisor_observation"),
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])

    assert result.success is True, (
        f"a legitimate approval was blocked even though the upload count "
        f"(0) is nowhere near any reasonable cap: {result!r}"
    )


def test_approve_frontrunner_proposal_never_raises_when_the_count_accessor_is_unavailable(fbld):
    """D-1: if database.count_uploaded_frontrunner_proposals itself raises
    (schema drift, DB lock, etc.), the guard must degrade — never crash the
    approval route. Fails CLOSED (skip, do not upload) rather than silently
    bypassing the guard, matching this module's fail-closed AC-9 posture
    elsewhere (verify_undeployed's own False-on-any-error contract)."""
    proposal = _make_proposal()
    with (
        patch("database.get_frontrunner_proposal", return_value=proposal),
        patch(
            "database.count_uploaded_frontrunner_proposals",
            side_effect=RuntimeError("db locked"),
        ),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
    ):
        result = fbld.approve_frontrunner_proposal(proposal["id"])  # must not raise

    assert result.success is False
    mock_save.assert_not_called()
