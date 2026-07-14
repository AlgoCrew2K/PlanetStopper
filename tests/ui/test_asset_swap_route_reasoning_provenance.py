"""RED tests — R2-3 AC-8/AC-9(route)/AC-10/AC-12: the asset-swaps evaluate
route surfaces run-level provenance on every return path, branches on the two
operator modes per the team-lead's contract ruling, and the stale
"Deterministic — no AI reasoning" tab label is retired for the reasoned path.

Contract ruling (team-lead, R2-3 — broadcast identically to test-writer/
engine/fe in one turn):
  POST /ai-advisor/asset-swaps/evaluate branches on whether BOTH from_ticker
  AND to_ticker are supplied:
    1. EXPLICIT-PAIR (both supplied): existing flat top-level response
       keys/values byte-preserved; ADDITIVELY gains `provenance` (+
       `survivors_detail`/`rejected_detail` arrays, per the approved unified
       array-driven JS renderer — additive only, nothing existing removed).
    2. OBJECTIVE-ONLY REASONED (both blank): array-shaped response
       (message/survivors count/survivors_detail[]/rejected_detail[]/
       provenance), mirroring the logic-changes route's shape.
    3. EXACTLY-ONE ticker supplied: honest 200 error, exact copy pinned below
       — never silently reinterpreted as either mode.
  `build_reasoning_context` called unconditionally (both modes get real
  context), mode="asset-swap". Provenance 4-key dict on EVERY return path,
  route-minted `_default_provenance` on early/error branches, engine's real
  provenance via getattr+isinstance(dict) guard on success.

Per team-lead's "representative sample + shape invariant" guidance (same
philosophy as R2-2's route test file): this file covers ONE pre-engine early
return, the engine-exception path, both mode success paths, the exactly-one-
ticker error, and the Mock-autovivify defensive guard — not an exhaustive
enumeration of every branch.

Does NOT re-add the existing AC-X1/AC-X2 import-guard / no-write-endpoint
static-analysis tests already covered in test_asset_swap_routes.py.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_EXACTLY_ONE_TICKER_ERROR = (
    "supply both tickers for an explicit pair, or neither to let the advisor propose"
)


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
        "children": [{"type": "asset", "ticker": "QQQ", "children": []}],
    }


def _seeded_bot_state() -> dict:
    return {"hash-abc-123": {"name": "sym-test"}}


def _real_run_result(*, provenance: dict, run_id: str, survivors: bool = False):
    """Build a minimal, real (non-Mock) SwapRunResult-shaped object for a
    mocked propose_operator_swap return."""
    from advisors.asset_swap_engine import (
        NO_SURVIVORS_MESSAGE,
        SwapObjective,
        SwapProposalResult,
        SwapRunResult,
    )
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    objective = SwapObjective(
        objective_type="reduce_correlation", target_pair=None, measured_value=0.0
    )
    proposal = SwapProposalResult(
        candidate_id="hash-abc-123:QQQ->AGG",
        symphony_id="hash-abc-123",
        incumbent_asset="QQQ",
        candidate_asset="AGG",
        objective=objective,
        objective_rationale="test rationale",
        apply_guidance="To apply: open sym-test in Composer and swap QQQ → AGG manually.",
    )
    return SwapRunResult(
        gate_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q),
        proposals=[proposal],
        survivors=[proposal] if survivors else [],
        rejected_candidates=[] if survivors else [proposal],
        message="1 swap survived the gate" if survivors else NO_SURVIVORS_MESSAGE,
        objective=objective,
        no_api_key=False,
        run_id=run_id,
        provenance=provenance,
    )


def _provenance_dict(*, run_id: str = "engine-run-id", model: str = "engine-model") -> dict:
    return {
        "generation_model": model,
        "mode": "asset-swap",
        "evidence_injected": {"tree": "present", "stats": "absent"},
        "run_id": run_id,
    }


# ===========================================================================
# Mode routing: explicit-pair vs objective-only vs exactly-one-ticker error.
# ===========================================================================


def test_exactly_one_ticker_supplied_yields_honest_200_error(flask_client):
    """from_ticker supplied, to_ticker blank -> honest error, never silently
    reinterpreted as either mode."""
    with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ"},  # to_ticker omitted
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("error") == _EXACTLY_ONE_TICKER_ERROR, (
        f"AC-12 GAP: exactly-one-ticker must yield the exact pinned error string. "
        f"Got {data.get('error')!r}"
    )


def test_exactly_one_ticker_to_only_supplied_yields_honest_200_error(flask_client):
    """Complement: to_ticker supplied, from_ticker blank."""
    with patch("advisors.asset_swap_engine._has_composer_key", return_value=True):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "to_ticker": "AGG"},
        )

    data = resp.get_json()
    assert data.get("error") == _EXACTLY_ONE_TICKER_ERROR


# ===========================================================================
# EXPLICIT-PAIR mode: existing flat top-level keys byte-preserved + additive.
# ===========================================================================


def test_explicit_pair_success_response_preserves_existing_flat_keys(flask_client):
    """Byte-preservation proof: every top-level key the PRE-R2-3 route
    returned on a successful explicit-pair evaluation must still be present."""
    mock_result = _real_run_result(provenance=_provenance_dict(), run_id="engine-run-id")

    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result),
        patch(
            "ai_advisor.build_reasoning_context",
            return_value=("", {"tree": "absent"}),
        ),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    pre_r2_3_keys = {
        "message",
        "survivors",
        "no_api_key",
        "candidate_id",
        "symphony_id",
        "from_ticker",
        "to_ticker",
        "objective_rationale",
        "baseline_stats",
        "variant_stats",
        "gate_decision",
        "gate_result",
        "caveats",
        "apply_guidance",
        "backtest_error",
        "data_warnings",
    }
    missing = pre_r2_3_keys - set(data.keys())
    assert not missing, (
        f"AC-12 GAP: explicit-pair success response is missing pre-R2-3 keys {missing!r} — "
        f"the existing response contract was not byte-preserved. Got keys: {sorted(data.keys())}"
    )
    assert data["from_ticker"] == "QQQ"
    assert data["to_ticker"] == "AGG"


def test_explicit_pair_success_response_additively_gains_provenance(flask_client):
    mock_result = _real_run_result(
        provenance=_provenance_dict(run_id="engine-run-id"), run_id="engine-run-id"
    )

    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("advisors.asset_swap_engine.propose_operator_swap", return_value=mock_result),
        patch("ai_advisor.build_reasoning_context", return_value=("", {"tree": "absent"})),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    data = resp.get_json()
    assert "provenance" in data, (
        f"AC-9 GAP: explicit-pair response missing 'provenance'. Got: {list(data.keys())}"
    )
    assert data["provenance"] is not None
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }
    assert data["provenance"]["mode"] == "asset-swap"
    assert data["provenance"]["run_id"] == "engine-run-id"


def test_explicit_pair_calls_propose_operator_swap_with_both_tickers(flask_client):
    """Proves the route still routes explicit-pair through the SAME engine
    entry point with both tickers, not silently through objective-only mode."""
    mock_result = _real_run_result(provenance=_provenance_dict(), run_id="engine-run-id")
    call_capture = {}

    def _capture(*args, **kwargs):
        call_capture.update(kwargs)
        return mock_result

    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("advisors.asset_swap_engine.propose_operator_swap", side_effect=_capture),
        patch("ai_advisor.build_reasoning_context", return_value=("", {"tree": "absent"})),
    ):
        flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    assert call_capture.get("incumbent_asset") == "QQQ", (
        f"AC-12 GAP: propose_operator_swap not called with incumbent_asset='QQQ'. "
        f"Got kwargs: {call_capture!r}"
    )
    assert call_capture.get("candidate_asset") == "AGG"


# ===========================================================================
# OBJECTIVE-ONLY REASONED mode: array-shaped response.
# ===========================================================================


def test_objective_only_mode_response_is_array_shaped(flask_client):
    mock_result = _real_run_result(
        provenance=_provenance_dict(run_id="engine-run-id"), run_id="engine-run-id", survivors=True
    )
    call_capture = {}

    def _capture(*args, **kwargs):
        call_capture.update(kwargs)
        return mock_result

    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("advisors.asset_swap_engine.propose_operator_swap", side_effect=_capture),
        patch("ai_advisor.build_reasoning_context", return_value=("", {"tree": "absent"})),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test"},  # neither ticker supplied
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "survivors_detail" in data and isinstance(data["survivors_detail"], list), (
        f"AC-9 GAP: objective-only response missing array-shaped 'survivors_detail'. "
        f"Got: {list(data.keys())}"
    )
    assert "rejected_detail" in data and isinstance(data["rejected_detail"], list), (
        f"AC-9 GAP: objective-only response missing array-shaped 'rejected_detail'. "
        f"Got: {list(data.keys())}"
    )
    assert "provenance" in data and data["provenance"] is not None
    # Proves the objective-only branch calls the engine WITHOUT both tickers
    # (either omitted, or explicitly None) — never silently reusing an
    # explicit-pair-shaped call.
    assert not (call_capture.get("incumbent_asset") and call_capture.get("candidate_asset")), (
        f"AC-12 GAP: objective-only mode called propose_operator_swap with BOTH tickers "
        f"supplied — the two modes must be genuinely disjoint. Got kwargs: {call_capture!r}"
    )


# ===========================================================================
# Pre-engine early return (representative sample: no-key path).
# ===========================================================================


def test_evaluate_route_provenance_present_on_no_api_key_early_return(flask_client):
    import ai_advisor

    with patch("advisors.asset_swap_engine._has_composer_key", return_value=False):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "error" in data, "no-key path must still carry 'error' (AC-X4, preserved)."
    assert "provenance" in data, (
        f"AC-8 GAP: the no-key pre-engine early return must ALSO carry provenance. "
        f"Got keys: {list(data.keys())}"
    )
    prov = data["provenance"]
    assert prov is not None
    assert set(prov.keys()) == {"generation_model", "mode", "evidence_injected", "run_id"}
    assert prov["mode"] == "asset-swap"
    assert prov["evidence_injected"] == ai_advisor._EMPTY_MANIFEST, (
        f"AC-8 GAP: a pre-engine early return must carry the honest all-absent manifest "
        f"(no reasoning was ever attempted) — got {prov['evidence_injected']!r}."
    )
    assert prov["run_id"], "AC-8 GAP: route-minted run_id must be a non-empty string."


# ===========================================================================
# Engine-exception path.
# ===========================================================================


def test_evaluate_route_provenance_present_on_engine_exception_path(flask_client):
    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("ai_advisor.build_reasoning_context", return_value=("", {"tree": "absent"})),
        patch(
            "advisors.asset_swap_engine.propose_operator_swap",
            side_effect=RuntimeError("simulated engine crash with a secret path /home/user/.env"),
        ),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("error") == "RuntimeError", (
        f"AC-9 GAP (D-1 regression): error token must be exactly type(exc).__name__, "
        f"never str(exc). Got {data.get('error')!r}"
    )
    assert "provenance" in data and data["provenance"] is not None
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }


# ===========================================================================
# generation_model reflects the live accessor.
# ===========================================================================


def test_evaluate_route_provenance_generation_model_reflects_accessor_env_override(flask_client):
    import model_config

    with (
        patch.object(
            model_config, "get_advisor_suggestion_model", return_value="test-marker-route-model"
        ),
        patch("advisors.asset_swap_engine._has_composer_key", return_value=False),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    data = resp.get_json()
    assert data["provenance"]["generation_model"] == "test-marker-route-model", (
        f"AC-8 GAP: provenance.generation_model must reflect the LIVE accessor value, "
        f"not a hardcoded literal. Got {data['provenance']!r}"
    )


# ===========================================================================
# Defensive guard: a non-dict-ish engine result never crashes/leaks a Mock repr.
# ===========================================================================


def test_evaluate_route_provenance_defensive_guard_on_non_dict_engine_result(flask_client):
    malformed_result = _real_run_result(provenance=_provenance_dict(), run_id="whatever")
    malformed_result.provenance = "not-a-dict-at-all"  # the malformed value under test

    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch("database.load_state", return_value=_seeded_bot_state()),
        patch("symphony_logic.fetch_symphony_score", return_value=_minimal_score_tree()),
        patch("advisors.asset_swap_engine.propose_operator_swap", return_value=malformed_result),
        patch("ai_advisor.build_reasoning_context", return_value=("", {"tree": "absent"})),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None, "AC-8 GAP: a non-dict-ish engine result must not crash jsonify()."
    assert "provenance" in data and isinstance(data["provenance"], dict), (
        f"AC-8 GAP: defensive fallback must still produce a real dict, never a Mock repr or None. "
        f"Got {data.get('provenance')!r}"
    )
    assert set(data["provenance"].keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }


# ===========================================================================
# AC-9: the stale attribution label is retired for the Asset Swaps tab only.
# ===========================================================================

_STALE_LABEL = "Deterministic — no AI reasoning"


def _tab_button_block(html: str, testid: str) -> str:
    marker = f'data-testid="{testid}"'
    start = html.find(marker)
    assert start != -1, f"self-guard: {marker!r} not found in templates/ai_advisor.html."
    end = html.find("</button>", start)
    assert end != -1, f"self-guard: no closing </button> found after {marker!r}."
    return html[start:end]


def test_attribution_label_flips_for_asset_swaps_tab_only():
    """This is the BINDING contract for Asset Swaps' tab-attribution label —
    the R1-era sibling assertion in
    tests/ai_advisor/test_r1_attribution_honesty.py
    (test_ac1_asset_swaps_tab_carries_deterministic_no_ai_label) was RETIRED
    once R2-3 shipped, mirroring how R2-2 retired the identical
    Logic-Changes-era assertion in that same file — see that file's own
    in-source retirement note."""
    html = (_REPO_ROOT / "templates" / "ai_advisor.html").read_text(encoding="utf-8")

    swaps_block = _tab_button_block(html, "tab-asset-swaps")
    assert _STALE_LABEL not in swaps_block, (
        f"AC-9 GAP: the Asset Swaps tab button still contains the stale label {_STALE_LABEL!r} — "
        "the reasoned path must retire it."
    )
    # Positive check (not just old-label-absent): the tab must carry the new
    # honest, reasoned-path attribution — byte-identical phrasing to Logic
    # Changes' R2-2 flip, same mechanism (reasoning over the live tree).
    assert "reasons over your live tree" in swaps_block, (
        "AC-9 GAP: the Asset Swaps tab does not carry the new reasoned-path "
        f"attribution copy. Element: {swaps_block!r}"
    )

    # Regression guard: Logic Changes' and Strategy Builder's labels were
    # already flipped by earlier R2 cycles and must not be re-touched here.
    lc_block = _tab_button_block(html, "tab-logic-changes")
    assert _STALE_LABEL not in lc_block, (
        "REGRESSION: Logic Changes' label reverted to the stale copy — R2-3 must not touch it."
    )
    assert "reasons over your live tree" in lc_block, (
        "REGRESSION: Logic Changes' R2-2 attribution copy changed unexpectedly."
    )


# ===========================================================================
# AC-10: no new settings-write surface introduced by the reasoning port.
# ===========================================================================


def test_evaluate_route_still_never_touches_settings_write_surface():
    """Static analysis: the evaluate route must not reference
    _SETTINGS_WRITE_ALLOWLIST, save_settings, or set_symphony_live_mode — the
    reasoning port stays advisory-only."""
    source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    evaluate_fn_body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "ai_advisor_asset_swaps_evaluate":
            evaluate_fn_body = ast.unparse(node)
            break

    assert evaluate_fn_body is not None, "Could not find ai_advisor_asset_swaps_evaluate in app.py."
    for forbidden in ("_SETTINGS_WRITE_ALLOWLIST", "save_settings", "set_symphony_live_mode"):
        assert forbidden not in evaluate_fn_body, (
            f"AC-10 GAP: ai_advisor_asset_swaps_evaluate references {forbidden!r} — the "
            "reasoning port must stay off the settings-write surface (advisory-only)."
        )
