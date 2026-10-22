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
import json
import pathlib
from unittest.mock import patch

import pytest

from tests import _asset_swap_llm_support as sup
from tests._anthropic_http_fake import FAKE_API_KEY, AnthropicHttpFake, load_fixture
from tests.llm_request.conftest import CAPTURED, newest_id

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
# generation_model reflects the model-resolution state, never a literal.
# ===========================================================================


def test_evaluate_route_provenance_generation_model_reflects_env_override(
    flask_client, monkeypatch
):
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-route-model")

    with patch("advisors.asset_swap_engine._has_composer_key", return_value=False):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "sym-test", "from_ticker": "QQQ", "to_ticker": "AGG"},
        )

    data = resp.get_json()
    assert data["provenance"]["generation_model"] == "test-marker-route-model", (
        f"AC-8 GAP: provenance.generation_model must carry the override verbatim. "
        f"Got {data['provenance']!r}"
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


# ===========================================================================
# Model resolution through the real route, SDK and HTTP transport.
#
# Every test below hands the engine a real anthropic client over the in-process
# HTTP fake (tests/_anthropic_http_fake.py) and asserts on the outgoing request
# and on the route's JSON. Replies are captured or schema-derived fixtures under
# tests/fixtures/anthropic; model IDs are read from them, never typed.
# ===========================================================================

_ENV = "ADVISOR_SUGGESTION_MODEL"
# Not a served model id: used where a test needs an operator override the API will not serve.
_UNSERVED_OVERRIDE = "operator-chosen-model"


def _no_swap(reason_text: str) -> str:
    return f"no swap was generated this run ({reason_text})"


def _older_fable_id() -> str:
    """A served fable-line id that is not the newest one, so an override is distinguishable."""
    fables = sorted(
        (
            r
            for r in CAPTURED["data"]
            if r.get("line") == "fable" and r.get("lifecycle", "active") == "active"
        ),
        key=lambda r: r["created_at"],
    )
    return fables[-2]["id"]


@pytest.fixture
def fake() -> AnthropicHttpFake:
    return AnthropicHttpFake.with_captured_page()


def _evaluate(flask_client, fake, body=None, **kwargs):
    return sup.post_evaluate(flask_client, fake, body or sup.objective_body(), **kwargs)


class TestObjectiveModeNamesTheResolvedModel:
    def test_the_request_names_the_newest_listed_model_of_the_family(
        self, flask_client, fake, unresolved_models
    ):
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        assert run.status == 200
        assert fake.listing_sequences == 1
        assert sup.request_models(fake) == [newest_id("fable")]
        assert newest_id("fable") != _older_fable_id(), "self-guard: the page lists two models"
        assert not fake.misuse

    def test_an_override_changes_only_the_model_named_and_nothing_is_listed(
        self, flask_client, fake, unresolved_models, monkeypatch
    ):
        override = _older_fable_id()
        assert override != newest_id("fable"), (
            "self-guard: the override must differ from resolution"
        )
        monkeypatch.setenv(_ENV, override)
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        (request,) = fake.message_requests
        assert request.body["model"] == override
        assert fake.listing_requests == [], "an override is never listed or validated"
        assert request.body["output_config"] == {"effort": "high"}
        assert request.body["tool_choice"] == {"type": "auto"}
        assert request.body["fallbacks"] == "default"
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()]

    def test_the_request_carries_the_fallback_setting_and_auto_tool_choice(
        self, flask_client, fake
    ):
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        (request,) = fake.message_requests
        assert request.body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
        assert request.body["tool_choice"] == {"type": "auto"}
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()], (
            "the captured tool call must be parsed into the evaluated swap pair"
        )

    def test_a_run_the_primary_model_answered_names_that_model_in_provenance(
        self, flask_client, fake
    ):
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        (requested,) = sup.request_models(fake)
        assert run.data["provenance"]["generation_model"] == sup.swap_tool_reply()["model"]
        assert run.data["provenance"]["generation_model"] == requested

    def test_a_reply_served_by_the_fallback_model_names_the_fallback_in_provenance(
        self, flask_client, fake
    ):
        served = load_fixture("messages/fallback_served_tool.json")
        fake.queue_message(served)

        run = _evaluate(flask_client, fake)

        (requested,) = sup.request_models(fake)
        assert served["model"] != requested, (
            "self-guard: the fixture must be a fallback-served reply"
        )
        assert run.data["provenance"]["generation_model"] == served["model"]
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()], (
            "candidates come from the fallback reply's tool call"
        )


class TestObjectiveModeWithNoUsableModel:
    def test_a_failed_listing_degrades_with_the_reason_and_sends_nothing(
        self, flask_client, fake, unresolved_models
    ):
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        run = _evaluate(flask_client, fake)

        assert run.status == 200
        assert run.data["survivors"] == 0
        assert run.data["message"] == _no_swap("model_unavailable")
        assert run.data["provenance"]["generation_model"] == ""
        assert set(run.data["provenance"]) == {
            "generation_model",
            "mode",
            "evidence_injected",
            "run_id",
        }
        assert fake.message_requests == []
        assert sup.evaluated_pairs(run.data) == []
        run.inserts.assert_not_called()

    def test_the_next_click_lists_again_and_generates_once_the_api_recovers(
        self, flask_client, fake, unresolved_models
    ):
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
        first = _evaluate(flask_client, fake)
        assert first.data["message"] == _no_swap("model_unavailable")

        fake.queue_message(sup.swap_tool_reply())
        second = _evaluate(flask_client, fake)

        assert sup.request_models(fake) == [newest_id("fable")]
        assert "model_unavailable" not in second.data["message"]
        assert sup.evaluated_pairs(second.data) == [sup.captured_pair()]
        assert second.data["provenance"]["generation_model"] == sup.swap_tool_reply()["model"], (
            "provenance must name the model the reply came from, not the empty value "
            "the run started with"
        )

    def test_an_early_error_path_before_generation_names_no_model(
        self, flask_client, fake, unresolved_models
    ):
        run = _evaluate(flask_client, fake, composer_key=False)

        assert run.data["provenance"]["generation_model"] == ""
        assert fake.message_requests == []
        assert fake.listing_requests == [], "rendering a provenance must never wait on the network"


class TestExplicitPairMakesNoModelCall:
    def test_an_unresolvable_family_does_not_affect_an_explicit_pair(
        self, flask_client, fake, unresolved_models
    ):
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        run = _evaluate(flask_client, fake, sup.explicit_pair_body())

        assert fake.message_requests == []
        assert "model_unavailable" not in run.data["message"]
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()]
        assert run.data["provenance"]["generation_model"] == ""

    def test_the_provenance_holds_the_resolved_model_id(self, flask_client, fake):
        run = _evaluate(flask_client, fake, sup.explicit_pair_body())

        assert fake.message_requests == []
        assert run.data["provenance"]["generation_model"] == newest_id("fable")

    def test_an_override_value_is_carried_verbatim(self, flask_client, fake, monkeypatch):
        monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)

        run = _evaluate(flask_client, fake, sup.explicit_pair_body())

        assert fake.message_requests == []
        assert run.data["provenance"]["generation_model"] == _UNSERVED_OVERRIDE

    def test_an_objective_run_that_ends_before_generation_carries_the_override_verbatim(
        self, flask_client, fake, monkeypatch
    ):
        monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)

        run = _evaluate(flask_client, fake, composer_key=False)

        assert fake.message_requests == []
        assert run.data["provenance"]["generation_model"] == _UNSERVED_OVERRIDE


_OVERRIDE_FAILURES = [
    pytest.param("errors/404_not_found.json", "override_model_not_found", id="override-not-found"),
    pytest.param(
        "errors/400_invalid_request.json", "override_request_rejected", id="override-rejected"
    ),
]


class TestAnUnservedOverrideFailsLoudly:
    @pytest.mark.parametrize(("error_fixture", "code"), _OVERRIDE_FAILURES)
    def test_the_message_names_the_variable_and_the_code_with_no_retry(
        self, flask_client, fake, unresolved_models, monkeypatch, error_fixture, code
    ):
        monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)
        fake.queue_message(load_fixture(error_fixture))

        run = _evaluate(flask_client, fake)

        assert run.status == 200
        assert run.data["survivors"] == 0
        assert run.data["message"] == _no_swap(f"{code}: {_ENV}")
        assert len(fake.message_requests) == 1, "an override the API refuses is never retried"
        assert sup.request_models(fake) == [_UNSERVED_OVERRIDE], (
            "the run must not substitute the newest model for the operator's override"
        )
        assert fake.listing_requests == []
        assert run.data["provenance"]["generation_model"] == _UNSERVED_OVERRIDE
        run.inserts.assert_not_called()


# (queued reply fixtures, text the message names, requests that must have been sent)
_GENERATION_FAILURES = [
    pytest.param(
        ["messages/refusal_reasoning_extraction.json"],
        "refusal: reasoning_extraction",
        1,
        id="refusal-with-category",
    ),
    pytest.param(
        ["messages/refusal_null_category.json"], "refusal", 1, id="refusal-without-category"
    ),
    pytest.param(
        ["messages/refusal_after_partial_tool.json"],
        "refusal: reasoning_extraction",
        1,
        id="refusal-after-a-complete-tool-call",
    ),
    pytest.param(
        ["messages/refusal_after_partial_text.json"],
        "refusal: reasoning_extraction",
        1,
        id="refusal-after-partial-text",
    ),
    pytest.param(["messages/max_tokens.json"], "max_tokens", 1, id="truncated"),
    pytest.param(
        ["messages/max_tokens_in_thinking.json"], "max_tokens", 1, id="truncated-inside-thinking"
    ),
    pytest.param(
        ["messages/text_only_with_tools.json"] * 3, "no_tool_call", 3, id="no-tool-call-three-times"
    ),
]


class TestAGenerationThatProducedNothingSaysSo:
    @pytest.mark.parametrize(("replies", "reason_text", "requests"), _GENERATION_FAILURES)
    def test_the_route_reports_why_no_swap_was_generated_and_evaluates_nothing(
        self, flask_client, fake, replies, reason_text, requests
    ):
        for name in replies:
            fake.queue_message(load_fixture(name))
        # A reply after the bound would produce a candidate: proves the bound, not the queue.
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        assert run.status == 200
        assert run.data["survivors"] == 0
        assert run.data["message"] == _no_swap(reason_text)
        assert len(fake.message_requests) == requests
        assert sup.evaluated_pairs(run.data) == [], (
            "a refused or truncated reply's content must be dropped, never evaluated"
        )
        run.inserts.assert_not_called()
        assert run.data["provenance"]["generation_model"] == load_fixture(replies[-1])["model"]
        assert not fake.misuse

    def test_provenance_names_the_last_reply_not_the_first(self, flask_client, fake):
        replies = [
            "messages/text_only_with_tools.json",
            "messages/text_only_with_tools.json",
            "messages/thinking_text.json",
        ]
        for name in replies:
            fake.queue_message(load_fixture(name))
        assert load_fixture(replies[0])["model"] != load_fixture(replies[-1])["model"], (
            "self-guard: the first and last replies must come from different models"
        )

        run = _evaluate(flask_client, fake)

        assert run.data["message"] == _no_swap("no_tool_call")
        assert run.data["provenance"]["generation_model"] == load_fixture(replies[-1])["model"]

    @pytest.mark.parametrize("text_only_replies", [1, 2])
    def test_a_tool_call_on_a_later_attempt_is_used(self, flask_client, fake, text_only_replies):
        for _ in range(text_only_replies):
            fake.queue_message(load_fixture("messages/text_only_with_tools.json"))
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        assert len(fake.message_requests) == text_only_replies + 1
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()]
        assert "no swap was generated" not in run.data["message"]

    @pytest.mark.parametrize(
        "error_fixture", ["errors/500_api_error.json", "errors/400_invalid_request.json"]
    )
    def test_an_api_error_on_the_resolved_model_degrades_without_a_retry(
        self, flask_client, fake, error_fixture
    ):
        fake.queue_message(load_fixture(error_fixture))
        fake.queue_message(sup.swap_tool_reply())

        run = _evaluate(flask_client, fake)

        assert run.status == 200
        assert run.data["survivors"] == 0
        assert len(fake.message_requests) == 1
        assert sup.evaluated_pairs(run.data) == []
        run.inserts.assert_not_called()


class TestNothingTheApiSaidReachesTheOperator:
    @pytest.mark.parametrize(
        ("error_fixture", "with_override"),
        [
            pytest.param("errors/404_not_found.json", True, id="override-not-found"),
            pytest.param("errors/400_invalid_request.json", True, id="override-rejected"),
            pytest.param("errors/500_api_error.json", False, id="server-error"),
            pytest.param("errors/400_invalid_request.json", False, id="resolved-rejected"),
        ],
    )
    def test_the_response_carries_no_api_error_text_request_id_or_key(
        self, flask_client, fake, monkeypatch, error_fixture, with_override
    ):
        if with_override:
            monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)
        outcome = load_fixture(error_fixture)
        fake.queue_message(outcome)

        run = _evaluate(flask_client, fake)

        dumped = json.dumps(run.data)
        assert outcome["body"]["error"]["message"] not in dumped
        assert outcome["body"]["request_id"] not in dumped
        assert FAKE_API_KEY not in dumped

    @pytest.mark.parametrize(
        ("refusal_fixture", "block_type"),
        [
            pytest.param("messages/refusal_after_partial_text.json", "text", id="partial-text"),
            pytest.param("messages/refusal_after_partial_tool.json", "tool_use", id="tool-input"),
        ],
    )
    def test_a_refusal_never_leaks_the_content_it_dropped(
        self, flask_client, fake, refusal_fixture, block_type
    ):
        refused = load_fixture(refusal_fixture)
        dropped = next(b for b in refused["content"] if b["type"] == block_type)
        leaked = dropped["text"] if block_type == "text" else json.dumps(dropped["input"])
        assert leaked.strip(), "self-guard: the fixture must carry content to leak"
        fake.queue_message(refused)

        run = _evaluate(flask_client, fake)

        assert run.data["survivors"] == 0
        dumped = json.dumps(run.data)
        assert leaked not in dumped
        if block_type == "tool_use":
            assert dropped["input"]["candidates"][0]["rationale"] not in dumped
