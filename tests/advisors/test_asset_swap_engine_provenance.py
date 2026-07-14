"""RED tests — R2-3 AC-5/AC-7: SwapRunResult.run_id / .provenance +
persistence traceability.

Contract pinned (mirrors R2-2's test_logic_change_engine_provenance.py almost
1:1 — see that file for the FINAL-ruling rationale this reuses verbatim):

  SwapRunResult gains:
    run_id: str = ""      — uuid4 (or caller-supplied override via a run_id=
                             kwarg), minted on the FIRST line of BOTH
                             propose_operator_swap and suggest_swaps, present
                             on EVERY return path of one call (including the
                             no-key, zero-candidate, and backtest-failed early
                             returns).
    provenance: dict | None = None
        A REAL 4-key dict on EVERY path, NEVER None, NEVER {}:
        {"generation_model": <model_config.get_advisor_suggestion_model() at
         call time>, "mode": "asset-swap", "evidence_injected": <the passed
         reasoning_manifest, or ai_advisor._EMPTY_MANIFEST when omitted>,
         "run_id": <same value as SwapRunResult.run_id>}.
        run_id/generation_model/mode are cheap, non-fabricated facts about the
        CALL ITSELF — never nulled out on an error path.

  _persist_observation gains run_id/evidence_injected -> additive
  raw_response["run_id"], raw_response["evidence_injected"] keys on every
  persisted advisor_observations row (AC-7 traceability), for BOTH entry
  points (operator single-candidate AND suggest multi-candidate).

Credential-less: _has_composer_key is monkeypatched explicitly in every test.
get_tradeable_set is mocked permissive throughout (never a live Alpaca call).
"""

from __future__ import annotations

import json
import pathlib
import uuid
from dataclasses import fields as dataclass_fields
from unittest.mock import MagicMock

import pytest

import model_config

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)
_REAL_INCUMBENT = "QQQ"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _fake_backtest_result():
    from datetime import date, timedelta

    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _run_suggest(
    ase, fixture_tree, monkeypatch, *, reasoning_manifest=None, run_id=None, has_key=True, n_pairs=1
):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: has_key)
    monkeypatch.setattr(
        ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG", "IALT", "GLD"})
    )
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    insert_mock = MagicMock(return_value=1)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", insert_mock)

    tickers = ["AGG", "IALT", "GLD"][:n_pairs]
    gen_mock = MagicMock(
        return_value=[
            ase.SwapCandidate(incumbent_asset=_REAL_INCUMBENT, candidate_asset=t, rationale="x")
            for t in tickers
        ]
    )
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_mock)

    kwargs = {}
    if reasoning_manifest is not None:
        kwargs["reasoning_manifest"] = reasoning_manifest
    if run_id is not None:
        kwargs["run_id"] = run_id

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.suggest_swaps("sym-1", fixture_tree, objective, {}, tickers, **kwargs)
    return result, insert_mock


def _run_operator(
    ase, fixture_tree, monkeypatch, *, reasoning_manifest=None, run_id=None, has_key=True
):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: has_key)
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG"}))
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    insert_mock = MagicMock(return_value=1)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", insert_mock)

    gen_mock = MagicMock(
        return_value=[
            ase.SwapCandidate(incumbent_asset=_REAL_INCUMBENT, candidate_asset="AGG", rationale="x")
        ]
    )
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_mock)

    kwargs = {}
    if reasoning_manifest is not None:
        kwargs["reasoning_manifest"] = reasoning_manifest
    if run_id is not None:
        kwargs["run_id"] = run_id

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.propose_operator_swap(
        symphony_id="sym-1", score_tree=fixture_tree, objective=objective, **kwargs
    )
    return result, insert_mock


# ===========================================================================
# AC-5: provenance shape, accessor-driven model, mode="asset-swap".
# ===========================================================================


def test_provenance_shape_and_accessor_driven_model(ase, fixture_tree, monkeypatch):
    monkeypatch.setattr(
        model_config, "get_advisor_suggestion_model", lambda: "test-marker-as-model"
    )
    manifest = {
        "tree": "present",
        "stats": "absent",
        **{
            n: "absent" for n in ("technicals", "sentiment", "derivatives", "macro", "fundamentals")
        },
    }
    result, _insert = _run_suggest(ase, fixture_tree, monkeypatch, reasoning_manifest=manifest)

    assert result.provenance is not None, "AC-5 GAP: provenance is None on a successful run."
    assert result.provenance.get("generation_model") == "test-marker-as-model", (
        f"AC-5 GAP: provenance['generation_model'] must reflect the LIVE accessor value. "
        f"Got {result.provenance!r}"
    )
    assert result.provenance.get("mode") == "asset-swap", (
        f"AC-5 GAP: provenance={result.provenance!r}"
    )
    assert result.provenance.get("evidence_injected") == manifest, (
        f"AC-5 GAP: evidence_injected must equal the passed reasoning_manifest exactly. "
        f"Got {result.provenance.get('evidence_injected')!r}"
    )
    assert result.provenance.get("run_id") == result.run_id, (
        "AC-5/AC-7 GAP: provenance['run_id'] must equal SwapRunResult.run_id."
    )


def test_provenance_key_set_is_exactly_four_keys(ase, fixture_tree, monkeypatch):
    result, _insert = _run_suggest(
        ase, fixture_tree, monkeypatch, reasoning_manifest={"tree": "present"}
    )
    assert set(result.provenance.keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }, f"AC-5 GAP: provenance key set is {sorted(result.provenance.keys())!r}."


def test_provenance_evidence_injected_defaults_to_empty_manifest_when_reasoning_manifest_absent(
    ase, fixture_tree, monkeypatch
):
    import ai_advisor

    result, _insert = _run_suggest(ase, fixture_tree, monkeypatch)  # no reasoning_manifest
    assert result.provenance is not None
    empty_manifest = getattr(ai_advisor, "_EMPTY_MANIFEST", None)
    assert empty_manifest is not None, "GAP: ai_advisor._EMPTY_MANIFEST must exist (R2-1 shipped)."
    assert result.provenance.get("evidence_injected") == empty_manifest, (
        f"AC-5 GAP: with reasoning_manifest omitted, evidence_injected must equal "
        f"ai_advisor._EMPTY_MANIFEST exactly. Got {result.provenance.get('evidence_injected')!r}"
    )


# ===========================================================================
# AC-7: run_id minted, stable, traceable through persistence, both entry points.
# ===========================================================================


def test_run_id_minted_as_valid_uuid4_when_omitted(ase, fixture_tree, monkeypatch):
    result, _insert = _run_suggest(ase, fixture_tree, monkeypatch)
    assert isinstance(result.run_id, str) and result.run_id, (
        f"AC-7 GAP: run_id must be a non-empty string. Got {result.run_id!r}"
    )
    parsed = uuid.UUID(result.run_id)
    assert parsed.version == 4, f"AC-7 GAP: run_id must be a UUID4, got version={parsed.version}"


def test_caller_supplied_run_id_is_honored_and_used_verbatim(ase, fixture_tree, monkeypatch):
    fixed_id = "11111111-1111-4111-8111-111111111111"
    result, insert_mock = _run_suggest(ase, fixture_tree, monkeypatch, run_id=fixed_id)
    assert result.run_id == fixed_id, (
        f"AC-7 GAP: caller-supplied run_id not honored. Got {result.run_id!r}"
    )
    _args, call_kwargs = insert_mock.call_args_list[0]
    raw_response = call_kwargs.get("raw_response", {})
    assert raw_response.get("run_id") == fixed_id, (
        "AC-7 GAP: caller-supplied run_id did not propagate into the persisted raw_response."
    )


def test_run_id_persisted_traceable_to_advisor_observation__suggest_path(
    ase, fixture_tree, monkeypatch
):
    manifest = {"tree": "absent", "stats": "absent"}
    result, insert_mock = _run_suggest(
        ase, fixture_tree, monkeypatch, reasoning_manifest=manifest, n_pairs=2
    )
    assert insert_mock.call_count >= 1, "AC-7 precondition: no candidate was persisted."
    for _args, call_kwargs in insert_mock.call_args_list:
        raw_response = call_kwargs.get("raw_response", {})
        assert raw_response.get("run_id") == result.run_id, (
            f"AC-7 GAP: persisted raw_response['run_id'] ({raw_response.get('run_id')!r}) "
            f"!= result.run_id ({result.run_id!r})."
        )
        assert raw_response.get("evidence_injected") == manifest, (
            f"AC-7 GAP: persisted raw_response['evidence_injected'] must equal the run's manifest. "
            f"Got {raw_response.get('evidence_injected')!r}"
        )


def test_run_id_persisted_traceable_to_advisor_observation__operator_path(
    ase, fixture_tree, monkeypatch
):
    manifest = {"tree": "present", "stats": "present"}
    result, insert_mock = _run_operator(ase, fixture_tree, monkeypatch, reasoning_manifest=manifest)
    assert insert_mock.call_count >= 1, (
        "AC-7 precondition: operator-path candidate was not persisted."
    )
    _args, call_kwargs = insert_mock.call_args_list[0]
    raw_response = call_kwargs.get("raw_response", {})
    assert raw_response.get("run_id") == result.run_id, (
        "AC-7 GAP (operator path): persisted run_id != result.run_id."
    )


# ===========================================================================
# AC-5/AC-7: no-key path still carries real, non-fabricated provenance.
# ===========================================================================


def test_no_composer_key_still_populates_real_provenance_never_none__suggest(
    ase, fixture_tree, monkeypatch
):
    monkeypatch.setattr(
        model_config, "get_advisor_suggestion_model", lambda: "test-marker-as-model"
    )
    manifest = {"tree": "absent", "stats": "absent"}
    result, _insert = _run_suggest(
        ase, fixture_tree, monkeypatch, reasoning_manifest=manifest, has_key=False
    )

    assert result.no_api_key is True, "Test precondition: no-key path must set no_api_key=True."
    assert result.provenance is not None, (
        f"AC-5/AC-7 GAP: provenance must NEVER be None, even on the no-key path. Got {result.provenance!r}"
    )
    assert result.provenance.get("mode") == "asset-swap"
    assert result.provenance.get("generation_model") == "test-marker-as-model"
    assert result.provenance.get("evidence_injected") == manifest, (
        "AC-5 GAP: evidence_injected must reflect the manifest actually passed in, even on the "
        "no-key path (built before the key check ran)."
    )
    assert isinstance(result.run_id, str) and result.run_id
    uuid.UUID(result.run_id)
    assert result.provenance.get("run_id") == result.run_id


def test_no_composer_key_still_populates_real_provenance_never_none__operator(
    ase, fixture_tree, monkeypatch
):
    result, _insert = _run_operator(ase, fixture_tree, monkeypatch, has_key=False)
    assert result.no_api_key is True
    assert result.provenance is not None, (
        f"AC-5/AC-7 GAP (operator path): provenance must never be None on the no-key path. "
        f"Got {result.provenance!r}"
    )
    assert isinstance(result.run_id, str) and result.run_id
    uuid.UUID(result.run_id)


# ===========================================================================
# AC-X4 ordering — the LLM seam must never be reached when the Composer key
# is absent (mirrors the R2-2 re-gate production-bug finding — RED from the
# start this time, no need to discover it twice).
# ===========================================================================


def test_no_composer_key_never_reaches_llm_seam__operator_reasoned_mode(
    ase, fixture_tree, monkeypatch
):
    """propose_operator_swap in REASONED mode (neither ticker supplied) must
    check _has_composer_key() BEFORE ever calling generate_reasoned_swap_candidates
    — a valid ANTHROPIC_API_KEY with no Composer credentials must never bill a
    live Anthropic call for a run guaranteed to be discarded."""
    monkeypatch.setattr(ase, "_has_composer_key", lambda: False)
    gen_spy = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_spy)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.propose_operator_swap(
        symphony_id="sym-1", score_tree=fixture_tree, objective=objective
    )

    assert result.no_api_key is True, "Test precondition: no-key path must set no_api_key=True."
    assert gen_spy.call_count == 0, (
        f"AC-X4 GAP: generate_reasoned_swap_candidates was called {gen_spy.call_count} time(s) "
        "despite _has_composer_key() returning False — a real Anthropic call would be billed "
        "for a run guaranteed to be discarded. The no-Composer-key check must short-circuit "
        "BEFORE the LLM seam is ever reached."
    )


def test_no_composer_key_never_reaches_llm_seam__suggest(ase, fixture_tree, monkeypatch):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: False)
    gen_spy = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_spy)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.suggest_swaps("sym-1", fixture_tree, objective, {}, ["AGG"])

    assert result.no_api_key is True
    assert gen_spy.call_count == 0, (
        f"AC-X4 GAP: suggest_swaps called generate_reasoned_swap_candidates "
        f"{gen_spy.call_count} time(s) despite no Composer key."
    )


# ===========================================================================
# Narrow-scope characterization: SwapRunResult field set.
# ===========================================================================


def test_swap_run_result_dataclass_gains_only_run_id_and_provenance(ase):
    pre_r2_3_fields = {
        "gate_batch",
        "proposals",
        "survivors",
        "rejected_candidates",
        "message",
        "objective",
        "no_api_key",
        "persistence_error",
    }
    actual = {f.name for f in dataclass_fields(ase.SwapRunResult)}
    expected = pre_r2_3_fields | {"run_id", "provenance"}
    assert actual == expected, (
        f"AC-5/AC-7 GAP: SwapRunResult field set is {sorted(actual)}, expected exactly "
        f"{sorted(expected)} (pre-R2-3 8 fields + run_id + provenance, nothing else)."
    )
