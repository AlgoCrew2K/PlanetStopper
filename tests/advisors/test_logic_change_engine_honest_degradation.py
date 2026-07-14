"""RED tests — R2-2 AC-6: honest degradation, never fabricated.

Edge cases pinned (feature-plans/advisor-r2-2-logic-changes.md §Edge Cases):
  - Lens cache cold/stale -> per-lens absent/stale reflected honestly (reused
    verbatim from whatever reasoning_manifest the caller passes — the engine
    never overrides or "upgrades" a manifest value).
  - LLM unavailable / malformed output -> clean "no reasoned proposal this run"
    (NO_SURVIVORS_MESSAGE, the existing empty-is-valid contract), provenance
    still present (AC-5), run does not raise (D-1).
  - D-1 sweep: no failure mode may propagate an exception out of either public
    entry point.

Credential-less throughout; run_backtest and _build_client mocked; no live calls.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _patch_common(lce, monkeypatch):
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))


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
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert result.message == lce.NO_SURVIVORS_MESSAGE, (
        f"AC-6 GAP: LLM outage must degrade to the standard empty-run message, "
        f"got {result.message!r}"
    )
    assert result.survivors == [], "AC-6 GAP: no survivors expected when the LLM is unavailable."
    assert result.provenance is not None, (
        "AC-5/AC-6 GAP: provenance must still be present even when the LLM call itself failed."
    )


def test_llm_unavailable_degrades_to_no_reasoned_proposal_clean_message__operator(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.propose_operator_logic_change(
        "sym-1", fixture_tree, objective=objective, change_description="tighten the window"
    )

    assert result.message == lce.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


def test_llm_malformed_tool_output_degrades_cleanly_through_full_entry_point(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _MalformedResponseClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert result.message == lce.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


# ===========================================================================
# reasoning_manifest gaps reflected honestly, never fabricated/upgraded.
# ===========================================================================


def test_reasoning_manifest_absent_lenses_reflected_honestly_not_fabricated(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(
        lce, "run_backtest", MagicMock(side_effect=RuntimeError("no backtest needed"))
    )
    monkeypatch.setattr(lce, "generate_reasoned_logic_candidates", MagicMock(return_value=[]))

    manifest = {
        "tree": "absent",
        "stats": "absent",
        "technicals": "stale",
        "sentiment": "absent",
        "derivatives": "absent",
        "macro": "stale",
        "fundamentals": "absent",
    }
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes(
        "sym-1", fixture_tree, objective, reasoning_manifest=manifest
    )

    assert result.provenance.get("evidence_injected") == manifest, (
        f"AC-6 GAP: the engine must reflect the manifest EXACTLY as passed — no upgrading 'stale'/"
        f"'absent' to 'available'. Got {result.provenance.get('evidence_injected')!r}"
    )


# ===========================================================================
# D-1 sweep: no failure mode raises out of either public entry point.
# ===========================================================================


@pytest.mark.parametrize(
    "failure_setup",
    [
        "llm_raises",
        "llm_malformed",
    ],
)
def test_engine_never_raises_across_failure_modes__suggest(
    lce, fixture_tree, monkeypatch, failure_setup
):
    # NOTE: this sweep intentionally does NOT include "a collaborator breaks its
    # own D-1 contract and raises anyway" scenarios (e.g. forcing run_backtest
    # itself to raise) — composer_backtest_client.run_backtest is independently
    # D-1 (never raises on API/transport errors; advisors/composer_backtest_client.py:9),
    # and the EXISTING pre-R2-2 entry points do not defensively re-wrap it either.
    # Forcing that contract to break would test an unrealistic input and could
    # fail identically against the pre-R2-2 code path — out of R2-2's scope.
    # This sweep covers only the NEW failure surface R2-2 introduces:
    # generate_reasoned_logic_candidates's own D-1 contract (proven directly in
    # test_logic_change_engine_reasoned_generation.py) not being silently
    # re-broken by the orchestrating entry point.
    _patch_common(lce, monkeypatch)
    if failure_setup == "llm_raises":
        monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())
    elif failure_setup == "llm_malformed":
        monkeypatch.setattr(lce, "_build_client", lambda: _MalformedResponseClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    try:
        result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)
    except Exception as exc:  # noqa: BLE001 - the test itself asserts this must not happen
        pytest.fail(
            f"D-1 GAP ({failure_setup}): suggest_logic_changes raised {type(exc).__name__}: {exc}"
        )
    assert result is not None
