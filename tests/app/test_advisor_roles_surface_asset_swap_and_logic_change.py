"""RED tests — Workstream A / AC-A2 + AC-A3: ASSET_SWAP + LOGIC_CHANGE surfacing.

The asset-swap and logic-change advisor engines (advisors/asset_swap_engine.py,
advisors/logic_change_engine.py) persist survivors as advisor_observations rows
with advisor_role="ASSET_SWAP" / "LOGIC_CHANGE" (AC-X3 in their respective
plans), but nothing ever displays them: app.py's _ADVISOR_ROLES (app.py:
5037-5043) — the single list driving BOTH the /ai-advisor Overview panel and
the /api/advisor-observations no-symphony_id branch — omits both roles, and
templates/ai_advisor.html's _ROLE_LABELS (line 2182-2187) has no human label
for either.

AC-A2: "_ADVISOR_ROLES includes 'ASSET_SWAP' and 'LOGIC_CHANGE'; the Overview
observation feed renders rows of both roles; _ROLE_LABELS gains human labels
for both. Reuses the existing generic card renderer -- no new panel/tab."

AC-A3 (regression pin, NOT expected to be RED -- app.py:4069-4076 is already
role-filtered/unscoped): "A symphony_id='' STRATEGY_BUILDER row surfaces
automatically in the Strategy Builder tab; a test pins this so the weekly
convention stays visible."

Flask-client pattern mirrors tests/ai_advisor/test_advisor_observations_ui.py
(patch database.init_db before import so no live DB is touched at import time).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _make_observation(
    *,
    obs_id: int,
    advisor_role: str,
    symphony_id: str = "test-symphony",
    verdict: str = "ADOPT_CANDIDATE",
    subject_id: str = "cand-001",
) -> dict:
    """Structural sentinel row matching the advisor_observations accessor contract
    (migrations/017_advisor_observations.sql columns) -- values are recognizable
    test sentinels, never producer-computed rates/dollars/percents."""
    return {
        "id": obs_id,
        "created_at": "2026-07-12T10:00:00",
        "advisor_role": advisor_role,
        "subject_type": "swap_proposal" if advisor_role == "ASSET_SWAP" else "logic_proposal",
        "subject_id": subject_id,
        "verdict": verdict,
        "raw_response": {"note": f"sentinel {advisor_role} row"},
        "is_advisory_only": 1,
        "spec_bundle_id": None,
        "symphony_id": symphony_id,
    }


@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs. No live DB; no live network."""
    with patch("database.init_db"):
        import app as flask_app

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


def _role_keyed_side_effect(rows_by_role: dict):
    """Build a get_advisor_observations_for_role side_effect keyed by role.

    app.py's Overview loop and the /api/advisor-observations no-filter branch
    both call get_advisor_observations_for_role(role, limit=...) once per role
    in _ADVISOR_ROLES -- a flat return_value would return the same rows for
    every role, so a per-role side_effect is required to prove role-scoping.
    """

    def _side_effect(role, limit=50):
        return rows_by_role.get(role, [])

    return _side_effect


# ===========================================================================
# AC-A2 — _ADVISOR_ROLES includes ASSET_SWAP and LOGIC_CHANGE
# ===========================================================================


class TestAdvisorRolesListIncludesNewRoles:
    def test_advisor_roles_list_contains_asset_swap(self):
        import app as flask_app

        assert "ASSET_SWAP" in flask_app._ADVISOR_ROLES, (
            "app._ADVISOR_ROLES must include 'ASSET_SWAP' (AC-A2) -- the asset-swap "
            "engine's survivors are persisted but the Overview/api feed never queries "
            "for this role."
        )

    def test_advisor_roles_list_contains_logic_change(self):
        import app as flask_app

        assert "LOGIC_CHANGE" in flask_app._ADVISOR_ROLES, (
            "app._ADVISOR_ROLES must include 'LOGIC_CHANGE' (AC-A2) -- the "
            "logic-change engine's survivors are persisted but the Overview/api feed "
            "never queries for this role."
        )

    def test_advisor_roles_list_does_not_lose_existing_roles(self):
        """Regression: adding the two new roles must be strictly additive."""
        import app as flask_app

        for existing_role in (
            "OVERFITTING_CONSCIENCE",
            "SPEC_CRITIC",
            "NARRATOR",
            "MARKET_PRISM",
            "ADD_CANDIDATE",
        ):
            assert existing_role in flask_app._ADVISOR_ROLES, (
                f"Pre-existing role {existing_role!r} must remain in _ADVISOR_ROLES -- "
                f"AC-A2 is additive-only."
            )


# ===========================================================================
# AC-A2 — the Overview panel (/ai-advisor) renders rows of both new roles
# ===========================================================================


class TestOverviewPanelRendersNewRoleObservations:
    def test_ai_advisor_tab_renders_asset_swap_observation_row(self, flask_client):
        asset_swap_obs = _make_observation(
            obs_id=101, advisor_role="ASSET_SWAP", subject_id="cand-swap-sentinel-xyz"
        )

        with (
            patch(
                "database.get_advisor_observations_for_role",
                side_effect=_role_keyed_side_effect({"ASSET_SWAP": [asset_swap_obs]}),
            ),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "cand-swap-sentinel-xyz" in body, (
            "GET /ai-advisor must render an ASSET_SWAP observation's subject_id in the "
            "Overview table once _ADVISOR_ROLES includes 'ASSET_SWAP' -- the sentinel "
            "did not appear, meaning the role was never queried/rendered."
        )

    def test_ai_advisor_tab_renders_logic_change_observation_row(self, flask_client):
        logic_change_obs = _make_observation(
            obs_id=102, advisor_role="LOGIC_CHANGE", subject_id="cand-logic-sentinel-abc"
        )

        with (
            patch(
                "database.get_advisor_observations_for_role",
                side_effect=_role_keyed_side_effect({"LOGIC_CHANGE": [logic_change_obs]}),
            ),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "cand-logic-sentinel-abc" in body, (
            "GET /ai-advisor must render a LOGIC_CHANGE observation's subject_id in the "
            "Overview table once _ADVISOR_ROLES includes 'LOGIC_CHANGE' -- the sentinel "
            "did not appear, meaning the role was never queried/rendered."
        )

    def test_api_advisor_observations_includes_asset_swap_rows_with_no_filter(
        self, flask_client
    ):
        """/api/advisor-observations with no ?symphony_id filter iterates
        _ADVISOR_ROLES (app.py:5071) -- must include ASSET_SWAP rows too."""
        asset_swap_obs = _make_observation(
            obs_id=103, advisor_role="ASSET_SWAP", subject_id="cand-api-sentinel-1"
        )

        with patch(
            "database.get_advisor_observations_for_role",
            side_effect=_role_keyed_side_effect({"ASSET_SWAP": [asset_swap_obs]}),
        ):
            resp = flask_client.get("/api/advisor-observations")

        assert resp.status_code == 200
        payload = resp.get_json()
        subject_ids = [row.get("subject_id") for row in payload]
        assert "cand-api-sentinel-1" in subject_ids, (
            f"/api/advisor-observations (no filter) must include ASSET_SWAP rows once "
            f"_ADVISOR_ROLES includes it; got subject_ids={subject_ids!r}"
        )

    def test_api_advisor_observations_includes_logic_change_rows_with_no_filter(
        self, flask_client
    ):
        logic_change_obs = _make_observation(
            obs_id=104, advisor_role="LOGIC_CHANGE", subject_id="cand-api-sentinel-2"
        )

        with patch(
            "database.get_advisor_observations_for_role",
            side_effect=_role_keyed_side_effect({"LOGIC_CHANGE": [logic_change_obs]}),
        ):
            resp = flask_client.get("/api/advisor-observations")

        assert resp.status_code == 200
        payload = resp.get_json()
        subject_ids = [row.get("subject_id") for row in payload]
        assert "cand-api-sentinel-2" in subject_ids, (
            f"/api/advisor-observations (no filter) must include LOGIC_CHANGE rows once "
            f"_ADVISOR_ROLES includes it; got subject_ids={subject_ids!r}"
        )


# ===========================================================================
# AC-A2 — _ROLE_LABELS gains human labels for both roles
# ===========================================================================


class TestRoleLabelsHumanizedForNewRoles:
    def test_asset_swap_row_does_not_render_raw_enum_as_label(self, flask_client):
        """The Advisor column must show a humanized label, not the literal
        'ASSET_SWAP' constant -- AC-A2 explicitly requires _ROLE_LABELS to gain an
        entry for this role (template fallback without one prints the raw enum)."""
        asset_swap_obs = _make_observation(obs_id=105, advisor_role="ASSET_SWAP")

        with (
            patch(
                "database.get_advisor_observations_for_role",
                side_effect=_role_keyed_side_effect({"ASSET_SWAP": [asset_swap_obs]}),
            ),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")

        body = resp.get_data(as_text=True)
        assert ">ASSET_SWAP<" not in body, (
            "The Overview table's Advisor cell rendered the raw 'ASSET_SWAP' enum "
            "string -- _ROLE_LABELS (templates/ai_advisor.html) must gain a human "
            "label for this role (AC-A2), e.g. 'Asset Swap'."
        )

    def test_logic_change_row_does_not_render_raw_enum_as_label(self, flask_client):
        logic_change_obs = _make_observation(obs_id=106, advisor_role="LOGIC_CHANGE")

        with (
            patch(
                "database.get_advisor_observations_for_role",
                side_effect=_role_keyed_side_effect({"LOGIC_CHANGE": [logic_change_obs]}),
            ),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")

        body = resp.get_data(as_text=True)
        assert ">LOGIC_CHANGE<" not in body, (
            "The Overview table's Advisor cell rendered the raw 'LOGIC_CHANGE' enum "
            "string -- _ROLE_LABELS (templates/ai_advisor.html) must gain a human "
            "label for this role (AC-A2), e.g. 'Logic Change'."
        )


# ===========================================================================
# AC-A3 — regression pin: symphony_id="" STRATEGY_BUILDER row surfaces in the
# Strategy Builder tab (already role-filtered/unscoped -- NOT expected RED).
# ===========================================================================


class TestStrategyBuilderTabSurfacesUnscopedWeeklyRows:
    def test_symphony_id_empty_strategy_builder_row_surfaces_in_sb_tab(self, flask_client):
        """AC-A3: the weekly-convention symphony_id='' STRATEGY_BUILDER row must
        appear in the Strategy Builder tab panel -- pins the existing (already
        correct) unscoped query at app.py:4069-4076 against future regression."""
        sb_obs = _make_observation(
            obs_id=107,
            advisor_role="STRATEGY_BUILDER",
            symphony_id="",
            subject_id="weekly-build-sentinel-42",
        )
        sb_obs["raw_response"] = {
            "rules_text": "weekly-build-sentinel-42 rules",
            "template_id": "built-new",
        }

        with (
            patch(
                "database.get_advisor_observations_for_role",
                side_effect=_role_keyed_side_effect({"STRATEGY_BUILDER": [sb_obs]}),
            ),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "weekly-build-sentinel-42" in body, (
            "A symphony_id='' STRATEGY_BUILDER row must surface in the Strategy "
            "Builder tab panel (AC-A3 regression pin) -- app.py:4069-4076's "
            "get_advisor_observations_for_role('STRATEGY_BUILDER') call is unscoped "
            "by symphony, so the weekly-convention row should always appear."
        )
