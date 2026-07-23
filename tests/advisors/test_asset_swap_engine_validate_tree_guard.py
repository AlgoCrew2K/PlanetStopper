"""RED tests — R2-3 AC-3: swapped trees are structurally re-validated via
symphony_schema.validate_tree before backtest.

Net-new guard over today's apply_ticker_swap, mirroring R2-2's identical
guard over apply_logic_tweak — BUT with a deliberate divergence in attack
construction, called out explicitly (see Key De-risking Finding #2 in
feature-plans/advisor-r2-3-asset-swaps.md):

  apply_logic_tweak (R2-2) can be driven to corrupt an arbitrary structural
  field (e.g. overwriting a root node's own "step") because the LLM there
  proposes an arbitrary param_key. apply_ticker_swap (this engine) ONLY EVER
  substitutes a ticker STRING value into an existing "ticker" key — it
  structurally CANNOT corrupt a field like "step" the way logic-change's
  attack tree could, because the reasoned swap generator only ever proposes
  ticker strings, never a node_path/param_key pair. There is therefore no
  natural, tree-agnostic construction that makes apply_ticker_swap's OWN
  output fail validate_tree.

  Given that, this file proves the guard is genuinely WIRED (not decorative)
  by forcing symphony_schema.validate_tree itself to report a failure via
  monkeypatch and asserting the variant is dropped BEFORE any backtest call,
  with a distinct, honest, non-fabricated reason — the adversarially-honest
  proof available for THIS engine's actual attack surface (per the plan's own
  finding: the guard here is "cheap insurance", not the tradeability arbiter —
  Composer /backtest remains that).

Mocking strategy: advisors.composer_backtest_client.run_backtest is patched +
spied (never live). advisors.asset_swap_engine._build_client is mocked to
return a crafted swap pair. get_tradeable_set is mocked permissive. The REAL
evaluate_candidate_batch / acceptance_gate math runs unmocked.
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

_REAL_INCUMBENT = "QQQ"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _FixedMockClient:
    def __init__(self, candidates):
        self._candidates = candidates

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            return _RecordingMockResponse(self._outer._candidates)

    @property
    def messages(self):
        return self._Messages(self)


def _fake_backtest_result():
    from datetime import date, timedelta

    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _swap_pair(candidate: str = "AGG") -> dict:
    return {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": candidate, "rationale": "x"}


def _patch_common(ase, monkeypatch):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG", "IALT"}))
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))


def _objective(ase):
    return ase.SwapObjective(objective_type="reduce_drawdown", target_pair=None, measured_value=0.0)


# ===========================================================================
# AC-3 CORE: a structurally-invalid variant is dropped before backtest.
# ===========================================================================


def test_structurally_invalid_variant_dropped_before_backtest(ase, fixture_tree, monkeypatch):
    _patch_common(ase, monkeypatch)
    client = _FixedMockClient(candidates=[_swap_pair()])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    # Force the structural guard to fail — the adversarially-honest proof
    # available for this engine (see module docstring): apply_ticker_swap
    # cannot itself construct an invalid tree, so we drive validate_tree's
    # OWN return value directly to prove the guard is wired, not decorative.
    monkeypatch.setattr(
        ase.symphony_schema,
        "validate_tree",
        lambda tree: ["simulated structural violation: forced failure"],
    )

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase, "run_backtest", backtest_spy)

    result = ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert backtest_spy.call_count == 0, (
        f"AC-3 GAP: run_backtest was called {backtest_spy.call_count} time(s) for a "
        "structurally-invalid variant — it must be dropped BEFORE any backtest."
    )
    assert result.message == ase.NO_SURVIVORS_MESSAGE, (
        f"AC-3: zero survivors expected (only candidate was structurally invalid). "
        f"Got message={result.message!r}"
    )
    assert result.rejected_candidates, (
        "AC-3 GAP: the invalid candidate must still be surfaced (rejected), not silently vanish."
    )
    reason = result.rejected_candidates[0].backtest_error
    assert reason, "AC-3 GAP: the dropped candidate must carry an honest, non-empty reason."
    assert "not in the symphony tree" not in reason, (
        f"AC-3 GAP: reason={reason!r} reuses the pre-existing 'incumbent not in tree' wording — "
        "this must be a DISTINCT, genuinely-new validate_tree guard message (the incumbent WAS "
        "present here; only the structural guard fired)."
    )


def test_structurally_valid_variant_proceeds_to_backtest(ase, fixture_tree, monkeypatch):
    """Regression: a variant that passes validate_tree (the normal case — a
    ticker-only swap) must still reach run_backtest — the guard must not
    reject genuinely valid swaps."""
    _patch_common(ase, monkeypatch)
    client = _FixedMockClient(candidates=[_swap_pair()])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    validate_spy = MagicMock(side_effect=lambda tree: [])  # always valid
    monkeypatch.setattr(ase.symphony_schema, "validate_tree", validate_spy)

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase, "run_backtest", backtest_spy)

    ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert validate_spy.called, (
        "AC-3 GAP: symphony_schema.validate_tree was never called — the guard is not wired."
    )
    assert backtest_spy.call_count >= 1, (
        "AC-3 GAP: a structurally-valid swap variant must reach run_backtest — "
        "the validate_tree guard must not reject valid variants."
    )


def test_validate_tree_called_after_swap_before_backtest(ase, fixture_tree, monkeypatch):
    """Ordering proof: validate_tree must be invoked on the SWAPPED tree, and
    that call must happen strictly before the first run_backtest call."""
    _patch_common(ase, monkeypatch)
    client = _FixedMockClient(candidates=[_swap_pair()])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    call_order: list[str] = []

    def _validate_spy(tree):
        call_order.append("validate_tree")
        return []

    def _backtest_spy(*a, **k):
        call_order.append("run_backtest")
        return _fake_backtest_result()

    monkeypatch.setattr(ase.symphony_schema, "validate_tree", _validate_spy)
    monkeypatch.setattr(ase, "run_backtest", _backtest_spy)

    ase.suggest_swaps("sym-1", fixture_tree, _objective(ase), {}, ["AGG"])

    assert "validate_tree" in call_order, "AC-3 GAP: validate_tree was never called."
    assert call_order.index("validate_tree") < call_order.index("run_backtest"), (
        f"AC-3 GAP: validate_tree must run BEFORE the first backtest call. Order: {call_order!r}"
    )
