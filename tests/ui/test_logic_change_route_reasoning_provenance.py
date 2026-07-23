"""RED tests — R2-2 AC-5 (route)/AC-8/AC-9: the logic-changes evaluate route
surfaces run-level provenance on every return path, and the stale
"Deterministic — no AI reasoning" tab label is retired for the reasoned path.

Contract ruling (team-lead, R2-2 — STRICTER than R2-1's engine-only scoping):
  Route JSON carries a "provenance" key on EVERY return path, a real 4-key dict,
  NEVER None:
    {"generation_model": <model_config.get_advisor_suggestion_model() at call
     time>, "mode": "logic-change", "evidence_injected": {...}, "run_id": <uuid4>}
  - Pre-engine early returns (no-key, missing-input, hash-resolve-fail,
    tree-fetch-fail, import-fail) AND the engine-exception handler: a
    ROUTE-minted default (evidence_injected = ai_advisor._EMPTY_MANIFEST, a
    route-minted run_id).
  - Success / zero-survivors path: getattr(run_result, "provenance", None) with
    an isinstance(dict) guard — same defensive idiom as the shipped SB route
    (app.py:4968-4970) — falling back to the route-minted default when the
    engine result isn't a real dict, never None.

Per team-lead's "representative sample + shape invariant" guidance: this file
covers ONE pre-engine early return, the engine-exception path, the success
path, and the Mock-autovivify defensive guard — not all 5 pre-engine branches
individually (the shape invariant is what's being proven, not per-branch
enumeration).

Does NOT re-add the existing AC-X1/AC-X2 import-guard / no-write-endpoint
static-analysis tests already GREEN in test_logic_change_routes.py.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def flask_client():
    import app as _app

    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


def _minimal_score_tree() -> dict:
    return {
        "id": "sym-test",
        "name": "Test Symphony",
        "type": "root",
        "children": [{"type": "momentum", "window": 20, "threshold": 8.0, "children": []}],
    }


def _seeded_bot_state() -> dict:
    return {"hash-abc-123": {"name": "sym-test"}}


def _real_run_result(*, provenance: dict, run_id: str):
    """Build a minimal, real (non-Mock) LogicChangeRunResult-shaped object for
    a mocked propose_operator_logic_change return."""
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch
    from advisors.logic_change_engine import (
        NO_SURVIVORS_MESSAGE,
        LogicChangeObjective,
        LogicChangeRunResult,
    )

    return LogicChangeRunResult(
        gate_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q),
        message=NO_SURVIVORS_MESSAGE,
        objective=LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0),
        no_api_key=False,
        run_id=run_id,
        provenance=provenance,
    )


def _provenance_dict(*, run_id: str = "engine-run-id", model: str = "engine-model") -> dict:
    return {
        "generation_model": model,
        "mode": "logic-change",
        "evidence_injected": {"tree": "present", "stats": "absent"},
        "run_id": run_id,
    }


# ===========================================================================
# Success path: route echoes run_result.provenance verbatim.
# ===========================================================================


def test_evaluate_route_success_response_includes_provenance_matching_engine_result(flask_client):
    mock_result = _real_run_result(
        provenance=_provenance_dict(run_id="engine-run-id"), run_id="engine-run-id"
    )

    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change", return_value=mock_result
        ),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test", "change_description": "change window from 20 to 16"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "provenance" in data, (
        f"AC-5 GAP: route response missing 'provenance' key. Got keys: {list(data.keys())}"
    )
    assert data["provenance"] is not None, (
        "AC-5 GAP: provenance must never be None on the success path."
    )
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }, f"AC-5 GAP: provenance key set is {sorted(data['provenance'].keys())!r}."
    assert data["provenance"]["run_id"] == "engine-run-id", (
        f"AC-5 GAP: route did not echo the engine's real provenance verbatim. Got {data['provenance']!r}"
    )
    assert data["provenance"]["mode"] == "logic-change"


# ===========================================================================
# Pre-engine early return (representative sample: no-key path).
# ===========================================================================


def test_evaluate_route_provenance_present_on_no_api_key_early_return(flask_client):
    import ai_advisor

    with patch("advisors.logic_change_engine._has_composer_key", return_value=False):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test", "change_description": "change window from 20 to 16"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "error" in data, "no-key path must still carry 'error' (AC-X4, preserved)."
    assert "provenance" in data, (
        f"AC-5 GAP: the no-key pre-engine early return must ALSO carry provenance (stricter than "
        f"R2-1's engine-only scoping — team-lead ruling). Got keys: {list(data.keys())}"
    )
    assert data["provenance"] is not None, (
        "AC-5 GAP: provenance must never be None, even pre-engine."
    )
    prov = data["provenance"]
    assert set(prov.keys()) == {"generation_model", "mode", "evidence_injected", "run_id"}, (
        f"AC-5 GAP: provenance key set is {sorted(prov.keys())!r}."
    )
    assert prov["mode"] == "logic-change"
    assert prov["evidence_injected"] == ai_advisor._EMPTY_MANIFEST, (
        f"AC-5 GAP: a pre-engine early return must carry the honest all-absent manifest "
        f"(no reasoning was ever attempted) — got {prov['evidence_injected']!r}, "
        f"expected {ai_advisor._EMPTY_MANIFEST!r}."
    )
    assert prov["run_id"], "AC-5 GAP: route-minted run_id must be a non-empty string."


def test_evaluate_route_provenance_present_on_missing_input_early_return(flask_client):
    """Second pre-engine branch (missing change_description) — same shape invariant."""
    with patch("advisors.logic_change_engine._has_composer_key", return_value=True):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test"},  # change_description omitted
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "provenance" in data and data["provenance"] is not None, (
        f"AC-5 GAP: missing-input early return must carry real provenance. Got: {data!r}"
    )
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }


# ===========================================================================
# Engine-exception path.
# ===========================================================================


def test_evaluate_route_provenance_present_on_engine_exception_path(flask_client):
    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change",
            side_effect=RuntimeError("simulated engine crash with a secret path /home/user/.env"),
        ),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test", "change_description": "change window from 20 to 16"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("error") == "RuntimeError", (
        f"AC-9 GAP (D-1 regression): error token must be exactly type(exc).__name__, "
        f"never str(exc) (which could leak paths/secrets). Got {data.get('error')!r}"
    )
    assert "provenance" in data and data["provenance"] is not None, (
        f"AC-5 GAP: the engine-exception path must ALSO carry real provenance. Got: {data!r}"
    )
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }


# ===========================================================================
# generation_model reflects the live accessor (env-override proof).
# ===========================================================================


def test_evaluate_route_provenance_generation_model_reflects_accessor_env_override(flask_client):
    import model_config

    with (
        patch.object(
            model_config, "get_advisor_suggestion_model", return_value="test-marker-route-model"
        ),
        patch("advisors.logic_change_engine._has_composer_key", return_value=False),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test", "change_description": "change window from 20 to 16"},
        )

    data = resp.get_json()
    assert data["provenance"]["generation_model"] == "test-marker-route-model", (
        f"AC-5 GAP: provenance.generation_model must reflect the LIVE accessor value, "
        f"not a hardcoded literal. Got {data['provenance']!r}"
    )


# ===========================================================================
# Defensive guard: a non-dict-ish engine result never crashes/leaks a Mock repr.
# ===========================================================================


def test_evaluate_route_provenance_defensive_guard_on_non_dict_engine_result(flask_client):
    """An engine result whose .provenance attribute is present but NOT a real
    dict (e.g. a stray non-dict value from a malformed collaborator, or a bare
    Mock stand-in in some other test's fixture) must still yield a real 4-key
    provenance dict via the route's getattr+isinstance(dict) guard — mirrors
    the SB route's identical defensive test (app.py:4968-4970's precedent).
    Otherwise-valid result (real gate_batch/proposals) so ONLY the provenance
    guard is under test, isolated from unrelated serialization concerns."""
    malformed_result = _real_run_result(provenance=_provenance_dict(), run_id="whatever")
    malformed_result.provenance = "not-a-dict-at-all"  # the malformed value under test

    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change",
            return_value=malformed_result,
        ),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "sym-test", "change_description": "change window from 20 to 16"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None, "AC-5 GAP: a non-dict-ish engine result must not crash jsonify()."
    assert "provenance" in data and isinstance(data["provenance"], dict), (
        f"AC-5 GAP: defensive fallback must still produce a real dict, never a Mock repr or None. "
        f"Got {data.get('provenance')!r}"
    )
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }


# ===========================================================================
# AC-8: the stale attribution label is retired for the Logic Changes tab only.
# ===========================================================================

_STALE_LABEL = "Deterministic — no AI reasoning"


def _tab_button_block(html: str, testid: str) -> str:
    """Extract the <button ...>...</button> block for the given tab testid."""
    marker = f'data-testid="{testid}"'
    start = html.find(marker)
    assert start != -1, f"self-guard: {marker!r} not found in templates/ai_advisor.html."
    end = html.find("</button>", start)
    assert end != -1, f"self-guard: no closing </button> found after {marker!r}."
    return html[start:end]


def test_attribution_label_flips_for_logic_changes_tab_only():
    """R2-3 UPDATE (2026-07-14): this test's original name/scope ("...only")
    predates R2-3 — at R2-2 ship time, Asset Swaps was still deterministic,
    so this test's regression guard correctly pinned Asset Swaps' label as
    UNCHANGED. R2-3 has since shipped and intentionally flipped Asset Swaps'
    label too (its own binding contract is
    tests/ui/test_asset_swap_route_reasoning_provenance.py::
    test_attribution_label_flips_for_asset_swaps_tab_only). Updated here so
    this file's own regression guard reflects the CURRENT truth — both tabs
    now flip, Strategy Builder/Chat remain untouched by either cycle."""
    html = (_REPO_ROOT / "templates" / "ai_advisor.html").read_text(encoding="utf-8")

    lc_block = _tab_button_block(html, "tab-logic-changes")
    assert _STALE_LABEL not in lc_block, (
        f"AC-8 GAP: the Logic Changes tab button still contains the stale label {_STALE_LABEL!r} — "
        "the reasoned path must retire it."
    )

    # R2-3 (2026-07-14): Asset Swaps' label has ALSO since flipped — no longer
    # a regression guard for "stays deterministic", now a regression guard
    # for "stays flipped" (mirrors the Logic Changes assertion above).
    swaps_block = _tab_button_block(html, "tab-asset-swaps")
    assert _STALE_LABEL not in swaps_block, (
        "REGRESSION: Asset Swaps' attribution label reverted to the stale "
        "'Deterministic — no AI reasoning' copy — R2-3 intentionally flipped it."
    )
    assert "reasons over your live tree" in swaps_block, (
        "REGRESSION: Asset Swaps' R2-3 attribution copy changed unexpectedly."
    )


# ===========================================================================
# AC-9: no new settings-write surface introduced by the reasoning port.
# ===========================================================================


def test_evaluate_route_still_never_touches_settings_write_surface():
    """Static analysis: the evaluate route must not reference _SETTINGS_WRITE_ALLOWLIST,
    save_settings, or set_symphony_live_mode — the reasoning port stays advisory-only."""
    source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    evaluate_fn_body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_logic_changes_evaluate":
            evaluate_fn_body = ast.unparse(node)
            break

    assert evaluate_fn_body is not None, (
        "Could not find ai_advisor_logic_changes_evaluate in app.py."
    )
    for forbidden in ("_SETTINGS_WRITE_ALLOWLIST", "save_settings", "set_symphony_live_mode"):
        assert forbidden not in evaluate_fn_body, (
            f"AC-9 GAP: ai_advisor_logic_changes_evaluate references {forbidden!r} — the "
            "reasoning port must stay off the settings-write surface (advisory-only)."
        )
