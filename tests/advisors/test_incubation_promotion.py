"""
RED tests -- Strategy Incubation Gate promotion evaluation (AC-4).

`advisors.incubation.evaluate_promotion` does not exist yet; every test in this
file is expected to fail RED until the implementer adds it.

Contract under test (pinned in .claude/tdd-handoff.md "Pinned function signatures"):
    evaluate_promotion(
        forward_return_pct: list[float],
        spy_return_pct: list[float | None],
        backtest_mdd_pct: float,
        days_observed: int,
    ) -> dict

Returns {"decision": "PROMOTED"|"FAILED"|"DEFERRED"|"CONTINUE", "reason": str | None}.

PURE function -- no DB, no network, no mocking required. Golden-fixture math-layer
test per the project's "every math layer gets a golden-fixture test" house rule.
All scenario expectations are DERIVED from the fixture's own constructed return
series (see tests/fixtures/math/incubation_promotion_scenarios.json _description
fields for the hand-verified arithmetic) -- never hardcoded producer output
(feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
import pathlib

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math" / "incubation_promotion_scenarios.json"
)

_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_SCENARIOS = _FIXTURE["scenarios"]


def _expand_block(scenario: dict) -> tuple[list[float], list[float | None]]:
    """Expand a block+repeats scenario, or return the explicit series verbatim."""
    if "forward_return_pct_block" in scenario:
        block = scenario["forward_return_pct_block"]
        spy_block = scenario["spy_return_pct_block"]
        repeats = scenario["block_repeats"]
        forward = block * repeats
        spy = spy_block * repeats
        assert len(forward) == scenario["days_observed"], (
            f"Fixture construction error: expanded block length {len(forward)} != "
            f"declared days_observed {scenario['days_observed']}"
        )
        return forward, spy
    return scenario["forward_return_pct"], scenario["spy_return_pct"]


def _evaluate_promotion():
    """Import seam -- fails with a clear ImportError until advisors/incubation.py exists."""
    from advisors.incubation import evaluate_promotion

    return evaluate_promotion


class TestPromotionScenarios:
    @pytest.mark.parametrize("scenario_name", sorted(_SCENARIOS.keys()))
    def test_scenario_returns_expected_decision(self, scenario_name):
        """Each fixture scenario's evaluate_promotion() decision must match the
        hand-derived expectation (see the fixture's _description for the arithmetic
        that makes each expectation unambiguous)."""
        scenario = _SCENARIOS[scenario_name]
        forward, spy = _expand_block(scenario)
        evaluate_promotion = _evaluate_promotion()

        result = evaluate_promotion(
            forward_return_pct=forward,
            spy_return_pct=spy,
            backtest_mdd_pct=scenario["backtest_mdd_pct"],
            days_observed=scenario["days_observed"],
        )

        assert isinstance(result, dict), (
            f"evaluate_promotion() must return a dict, got {type(result)!r}"
        )
        assert "decision" in result, f"Result missing 'decision' key: {result}"
        assert result["decision"] == scenario["expected_decision"], (
            f"[{scenario_name}] expected decision={scenario['expected_decision']!r}, "
            f"got {result['decision']!r} (full result: {result})"
        )

    def test_mdd_breach_scenario_uses_the_pinned_mdd_breach_reason_token(self):
        """The mdd_breach status_reason token is PINNED in the handoff's known-tokens
        list (used by the route's degraded-badge rendering too) -- this scenario's
        reason must be exactly 'mdd_breach', not an implementer-chosen synonym."""
        scenario = _SCENARIOS["early_fails_on_mdd_breach_before_window_complete"]
        forward, spy = _expand_block(scenario)
        evaluate_promotion = _evaluate_promotion()

        result = evaluate_promotion(
            forward_return_pct=forward,
            spy_return_pct=spy,
            backtest_mdd_pct=scenario["backtest_mdd_pct"],
            days_observed=scenario["days_observed"],
        )
        assert result["reason"] == "mdd_breach", (
            f"Expected the pinned reason token 'mdd_breach', got {result['reason']!r}"
        )

    def test_alpha_fail_scenario_has_a_nonempty_reason_distinct_from_mdd_breach(self):
        """The alpha-fail reason token is implementer's choice (not pinned) -- assert
        only that it is a non-empty string and NOT 'mdd_breach' (which would wrongly
        conflate an alpha failure with an MDD-breach failure -- adversarial: a lazy
        implementation might always emit 'mdd_breach' regardless of the real cause)."""
        scenario = _SCENARIOS["fails_when_forward_alpha_negative_at_window_complete"]
        forward, spy = _expand_block(scenario)
        evaluate_promotion = _evaluate_promotion()

        result = evaluate_promotion(
            forward_return_pct=forward,
            spy_return_pct=spy,
            backtest_mdd_pct=scenario["backtest_mdd_pct"],
            days_observed=scenario["days_observed"],
        )
        assert isinstance(result["reason"], str) and result["reason"].strip(), (
            f"FAILED decision must carry a non-empty reason, got {result['reason']!r}"
        )
        assert result["reason"] != "mdd_breach", (
            "This scenario's drawdown stays well under the breach threshold -- the "
            "failure must be attributed to negative alpha, not mis-labeled as an MDD "
            "breach."
        )

    def test_promoted_and_continue_and_deferred_decisions_carry_no_reason(self):
        """PROMOTED/CONTINUE/DEFERRED are not failures -- reason must be None for each
        (only FAILED carries a reason per the pinned contract)."""
        evaluate_promotion = _evaluate_promotion()
        for scenario_name in (
            "promotes_when_alpha_nonnegative_and_mdd_within_multiplier",
            "continues_incubating_when_window_incomplete_and_no_early_fail",
            "defers_when_benchmark_missing",
        ):
            scenario = _SCENARIOS[scenario_name]
            forward, spy = _expand_block(scenario)
            result = evaluate_promotion(
                forward_return_pct=forward,
                spy_return_pct=spy,
                backtest_mdd_pct=scenario["backtest_mdd_pct"],
                days_observed=scenario["days_observed"],
            )
            assert result["reason"] is None, (
                f"[{scenario_name}] decision={result['decision']!r} must carry reason=None, "
                f"got {result['reason']!r}"
            )


class TestPromotionPurity:
    def test_evaluate_promotion_runs_with_no_db_or_composer_client_importable(self, monkeypatch):
        """Structural guard: evaluate_promotion is a PURE math function -- it must not
        require database.py or composer_backtest_client to be importable. Blocks both
        imports (raising if attempted) and confirms the function still runs a scenario
        to completion, proving it holds no hidden dependency on either module."""
        import builtins

        real_import = builtins.__import__

        def _guarded_import(name, *args, **kwargs):
            if name in ("database", "advisors.composer_backtest_client", "requests"):
                raise AssertionError(
                    f"evaluate_promotion (or its module) imported {name!r} -- it must be "
                    "a pure function with zero DB/network I/O."
                )
            return real_import(name, *args, **kwargs)

        # advisors.incubation itself must already be importable before we install the
        # guard (module-level imports run at first import, not at call time) -- import
        # it once here, then guard subsequent imports only during the call.
        evaluate_promotion = _evaluate_promotion()

        monkeypatch.setattr(builtins, "__import__", _guarded_import)
        scenario = _SCENARIOS["promotes_when_alpha_nonnegative_and_mdd_within_multiplier"]
        forward, spy = _expand_block(scenario)
        evaluate_promotion(
            forward_return_pct=forward,
            spy_return_pct=spy,
            backtest_mdd_pct=scenario["backtest_mdd_pct"],
            days_observed=scenario["days_observed"],
        )

    def test_incubation_module_source_does_not_import_requests_at_module_level(self):
        """Source-level guard: advisors/incubation.py's module-level imports must not
        include `requests` directly -- all Composer access goes through
        composer_backtest_client (the single seam tests mock), never a raw HTTP call."""
        import pathlib

        module_path = (
            pathlib.Path(__file__).parent.parent.parent / "advisors" / "incubation.py"
        )
        assert module_path.is_file(), (
            f"advisors/incubation.py not found at {module_path} -- module not yet created."
        )
        source = module_path.read_text(encoding="utf-8")
        assert "import requests" not in source, (
            "advisors/incubation.py must not import requests directly -- Composer "
            "access goes exclusively through composer_backtest_client.run_backtest "
            "(the seam every test mocks)."
        )
