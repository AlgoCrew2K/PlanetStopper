"""RED tests — R2-3 AC-6: honest degradation, never fabricated.

Edge cases pinned (feature-plans/advisor-r2-3-asset-swaps.md §Edge Cases):
  - LLM outage / malformed output -> clean "no swap cleared the gate this run"
    (NO_SURVIVORS_MESSAGE, the existing empty-is-valid contract), provenance
    still present (AC-5), run does not raise (D-1).
  - Every candidate dropped before backtest (incumbent/universe validation) ->
    gate never invoked; no wasted SPY baseline call.
  - reasoning_manifest gaps reflected honestly (never upgraded/fabricated).
  - D-1 sweep: no failure mode may propagate an exception out of either public
    entry point.

Credential-less throughout; run_backtest, get_tradeable_set, and _build_client
mocked; no live calls.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

from tests import _asset_swap_llm_support as sup
from tests._anthropic_http_fake import load_fixture

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


def _patch_common(ase, monkeypatch):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG"}))
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))


def _objective(ase):
    return ase.SwapObjective(objective_type="reduce_drawdown", target_pair=None, measured_value=0.0)


class _RaisingClient:
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("simulated LLM outage")

    @property
    def messages(self):
        return self._Messages()


class _MalformedBlock:
    type = "tool_use"
    input = "not-even-a-dict"


class _MalformedResponse:
    stop_reason = "tool_use"
    content = [_MalformedBlock()]


class _MalformedResponseClient:
    class _Messages:
        def create(self, **kwargs):
            return _MalformedResponse()

    @property
    def messages(self):
        return self._Messages()


# ===========================================================================
# LLM unavailable / malformed -> clean degrade, never raise, provenance present.
# ===========================================================================


def test_llm_unavailable_degrades_to_no_reasoned_proposal_clean_message__suggest(
    ase, fixture_tree, monkeypatch
):
    _patch_common(ase, monkeypatch)
    monkeypatch.setattr(ase, "_build_client", lambda: _RaisingClient())

    result = ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert result.message == ase.NO_SURVIVORS_MESSAGE, (
        f"AC-6 GAP: LLM outage must degrade to the standard empty-run message, "
        f"got {result.message!r}"
    )
    assert result.survivors == [], "AC-6 GAP: no survivors expected when the LLM is unavailable."
    assert result.provenance is not None, (
        "AC-5/AC-6 GAP: provenance must still be present even when the LLM call itself failed."
    )


def test_llm_unavailable_degrades_to_no_reasoned_proposal_clean_message__operator(
    ase, fixture_tree, monkeypatch
):
    _patch_common(ase, monkeypatch)
    monkeypatch.setattr(ase, "_build_client", lambda: _RaisingClient())

    result = ase.propose_operator_swap(
        symphony_id="sym-1", score_tree=fixture_tree, objective=_objective(ase)
    )

    assert result.message == ase.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


def test_llm_malformed_tool_output_degrades_cleanly_through_full_entry_point(
    ase, fixture_tree, monkeypatch
):
    _patch_common(ase, monkeypatch)
    monkeypatch.setattr(ase, "_build_client", lambda: _MalformedResponseClient())

    result = ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert result.message == ase.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


# ===========================================================================
# Every candidate dropped pre-backtest -> gate never invoked, no wasted SPY call.
# ===========================================================================


def test_all_candidates_dropped_pre_backtest_never_invokes_gate_or_spy(
    ase, fixture_tree, monkeypatch
):
    """All LLM-proposed pairs fail universe validation -> zero candidates reach
    the gate -> evaluate_candidate_batch (and its SPY-baseline fetch) is never
    invoked with a real candidate list; no wasted backtest call."""
    _patch_common(ase, monkeypatch)
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset())  # nothing tradeable
    from advisors.composer_backtest_client import BacktestResult

    backtest_spy = MagicMock(
        side_effect=lambda *a, **k: BacktestResult(
            stats={"sharpe": 0.5}, data_warnings=[], daily_returns={"2022-01-01": 0.001}
        )
    )
    monkeypatch.setattr(ase, "run_backtest", backtest_spy)

    class _Client:
        class _Messages:
            def create(self, **kwargs):
                class _Block:
                    type = "tool_use"
                    input = {
                        "candidates": [
                            {
                                "incumbent_asset": _REAL_INCUMBENT,
                                "candidate_asset": "AGG",
                                "rationale": "x",
                            }
                        ]
                    }

                class _Resp:
                    stop_reason = "tool_use"
                    content = [_Block()]

                return _Resp()

        @property
        def messages(self):
            return self._Messages()

    monkeypatch.setattr(ase, "_build_client", lambda: _Client())

    result = ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert result.message == ase.NO_SURVIVORS_MESSAGE
    assert backtest_spy.call_count == 0, (
        f"AC-6 GAP: run_backtest was called {backtest_spy.call_count} time(s) even though "
        "every proposed candidate failed universe validation before backtest — no wasted "
        "backtest/SPY-baseline call should occur when there is nothing to gate."
    )


# ===========================================================================
# reasoning_manifest gaps reflected honestly, never fabricated/upgraded.
# ===========================================================================


def test_reasoning_manifest_absent_lenses_reflected_honestly_not_fabricated(
    ase, fixture_tree, monkeypatch
):
    _patch_common(ase, monkeypatch)
    monkeypatch.setattr(
        ase, "run_backtest", MagicMock(side_effect=RuntimeError("no backtest needed"))
    )
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", MagicMock(return_value=[]))

    manifest = {
        "tree": "absent",
        "stats": "absent",
        "technicals": "stale",
        "sentiment": "absent",
        "derivatives": "absent",
        "macro": "stale",
        "fundamentals": "absent",
    }
    result = ase.suggest_swaps(
        "sym-1", fixture_tree, _objective(ase), {}, ["AGG"], reasoning_manifest=manifest
    )

    assert result.provenance.get("evidence_injected") == manifest, (
        f"AC-6 GAP: the engine must reflect the manifest EXACTLY as passed — no upgrading "
        f"'stale'/'absent' to 'available'. Got {result.provenance.get('evidence_injected')!r}"
    )


# ===========================================================================
# D-1 sweep: no failure mode raises out of either public entry point.
# ===========================================================================


@pytest.mark.parametrize("failure_setup", ["llm_raises", "llm_malformed"])
def test_engine_never_raises_across_failure_modes__suggest(
    ase, fixture_tree, monkeypatch, failure_setup
):
    # NOTE: mirrors R2-2's identical scoping note — this sweep covers only the
    # NEW failure surface R2-3 introduces (generate_reasoned_swap_candidates's
    # own D-1 contract not being silently re-broken by the orchestrating entry
    # point), not pre-existing collaborator D-1 contracts.
    _patch_common(ase, monkeypatch)
    if failure_setup == "llm_raises":
        monkeypatch.setattr(ase, "_build_client", lambda: _RaisingClient())
    elif failure_setup == "llm_malformed":
        monkeypatch.setattr(ase, "_build_client", lambda: _MalformedResponseClient())

    try:
        result = ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])
    except Exception as exc:  # noqa: BLE001 - the test itself asserts this must not happen
        pytest.fail(f"D-1 GAP ({failure_setup}): suggest_swaps raised {type(exc).__name__}: {exc}")
    assert result is not None


# ===========================================================================
# A generation that produced nothing says why, through BOTH entry points, and a
# generation that produced a reply names the model that reply came from.
#
# Message form: ``no swap was generated this run (<reason text>)``; a gate with
# no survivors keeps NO_SURVIVORS_MESSAGE. Real SDK over the HTTP fake; replies
# are fixtures under tests/fixtures/anthropic.
# ===========================================================================

_ENTRY_POINTS = ["propose_operator_swap", "suggest_swaps"]


def _run_entry(ase, entry, monkeypatch, tree=None):
    """Run one public entry point with every non-model seam stubbed; returns (result, inserts)."""
    inserts = MagicMock(return_value=1)
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: sup.fake_backtest_result())
    monkeypatch.setattr(ase.database, "insert_advisor_observation", inserts)
    tree = sup.held_tree() if tree is None else tree
    objective = _objective(ase)
    if entry == "propose_operator_swap":
        result = ase.propose_operator_swap(
            symphony_id="sym-1", score_tree=tree, objective=objective
        )
    else:
        _, candidate = sup.captured_pair()
        result = ase.suggest_swaps("sym-1", tree, objective, {}, [candidate])
    return result, inserts


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
class TestEntryPointsReportWhyNothingWasGenerated:
    @pytest.mark.parametrize(
        ("replies", "reason_text", "requests"),
        [
            pytest.param(
                ["messages/refusal_reasoning_extraction.json"],
                "refusal: reasoning_extraction",
                1,
                id="refusal",
            ),
            pytest.param(["messages/max_tokens.json"], "max_tokens", 1, id="truncated"),
            pytest.param(
                ["messages/text_only_with_tools.json"] * 3, "no_tool_call", 3, id="no-tool-call"
            ),
        ],
    )
    def test_the_message_names_the_reason_and_nothing_is_persisted(
        self, ase, swap_fake, monkeypatch, entry, replies, reason_text, requests
    ):
        for name in replies:
            swap_fake.queue_message(load_fixture(name))
        swap_fake.queue_message(sup.swap_tool_reply())  # must stay unused

        result, inserts = _run_entry(ase, entry, monkeypatch)

        assert result.message == f"no swap was generated this run ({reason_text})"
        assert result.survivors == []
        assert result.proposals == []
        assert len(swap_fake.message_requests) == requests
        inserts.assert_not_called()
        assert result.provenance["generation_model"] == load_fixture(replies[-1])["model"]

    def test_a_family_that_could_not_be_resolved_says_model_unavailable(
        self, ase, swap_fake, unresolved_models, monkeypatch, entry
    ):
        swap_fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        result, inserts = _run_entry(ase, entry, monkeypatch)

        assert result.message == "no swap was generated this run (model_unavailable)"
        assert result.provenance["generation_model"] == ""
        assert swap_fake.message_requests == []
        inserts.assert_not_called()

    def test_a_reply_whose_pairs_are_all_dropped_keeps_the_standard_message(
        self, ase, swap_fake, monkeypatch, entry
    ):
        swap_fake.queue_message(sup.swap_tool_reply())
        monkeypatch.setattr(
            ase, "get_tradeable_set", lambda **kwargs: frozenset()
        )  # nothing clears

        result, _inserts = _run_entry(ase, entry, monkeypatch)

        assert result.message == ase.NO_SURVIVORS_MESSAGE
        assert len(swap_fake.message_requests) == 1

    def test_a_reply_served_by_the_fallback_model_names_it_and_its_pair_is_evaluated(
        self, ase, swap_fake, monkeypatch, entry
    ):
        served = load_fixture("messages/fallback_served_tool.json")
        swap_fake.queue_message(served)

        result, _inserts = _run_entry(ase, entry, monkeypatch)

        (requested,) = sup.request_models(swap_fake)
        assert served["model"] != requested, (
            "self-guard: the fixture must be a fallback-served reply"
        )
        assert result.provenance["generation_model"] == served["model"]
        assert [(p.incumbent_asset, p.candidate_asset) for p in result.proposals] == [
            sup.captured_pair()
        ]
