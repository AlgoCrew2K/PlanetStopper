"""RED tests — AC-10 (F7, Gap G): measured_value is REAL or ABSENT.

Confirmed by reading the current source directly before writing these tests:
  - logic_change_engine.py:207-210 (LogicChangeObjective docstring): "Always
    a measurement from the live backtest stats — never a hardcoded
    heuristic." — FALSE. app.py:4451 constructs
    LogicChangeObjective(measured_value=0.0, ...) unconditionally.
  - asset_swap_engine.py:225-229 (SwapObjective docstring): "Never hardcoded
    wisdom — always a measurement from the live correlation diagnostic or
    backtest stats." — FALSE. app.py:4308 constructs
    SwapObjective(measured_value=0.0, ...) unconditionally.
  - Both engines' _build_objective_rationale functions read measured_value
    only inside DISPLAY f-strings (never drive tweak generation/ranking, per
    the audit's DISPLAY-ONLY finding) — producing the exact fabricated-
    looking phrases "targets reduction of the measured 0.0% drawdown" (logic
    change) and "the measured 0.00 correlation" (asset swap) today.

TEST STRATEGY: the docstring fixes are byte-absence checks on the false
claim (source-text, not runtime — a docstring has no runtime behavior to
assert on). The two HTTP-route fixes are tested end-to-end (real
propose_operator_swap / propose_operator_logic_change, mocked network only)
by asserting the EXACT current fabricated phrase is absent from the route's
objective_rationale field — this is deliberately mechanism-agnostic (works
whether the fix computes a real statistic OR drops the "measured X" claim
entirely, since either remediation removes this exact "measured 0.0(0)"
phrasing; a real computed value would essentially never land on exactly
0.0/0.00 by coincidence).

KNOWN GAP (documented, not silently dropped): weekly_suggestions_scheduler.py
:136/:382 (the two weekly-batch call sites, no HTTP route to drive) are NOT
covered here — flagged in the handoff as the next increment for this AC, not
guessed at under time pressure.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


# ===========================================================================
# Docstring fixes — source-text byte-absence checks (no runtime behavior to
# assert on for a docstring).
# ===========================================================================


def test_ac10_logic_change_objective_docstring_false_claim_removed():
    """MUST FAIL pre-fix: the exact false claim is still present verbatim."""
    import advisors.logic_change_engine as lce

    source = inspect.getsource(lce.LogicChangeObjective)
    assert "never a hardcoded heuristic" not in source, (
        "AC-10 GAP: LogicChangeObjective's docstring still claims "
        "measured_value is 'never a hardcoded heuristic' — false for every "
        "current production caller (app.py:4451 passes measured_value=0.0)."
    )


def test_ac10_swap_objective_docstring_false_claim_removed():
    """MUST FAIL pre-fix — identical gap, asset_swap_engine sibling."""
    import advisors.asset_swap_engine as ase

    source = inspect.getsource(ase.SwapObjective)
    assert "Never hardcoded wisdom" not in source, (
        "AC-10 GAP: SwapObjective's docstring still claims measured_value is "
        "'Never hardcoded wisdom' — false for every current production "
        "caller (app.py:4308 passes measured_value=0.0)."
    )


def _measured_value_paragraph(class_source: str) -> str:
    """Isolate the `measured_value:` field's OWN docstring paragraph, not the
    whole class docstring — scoping matters: LogicChangeObjective's docstring
    also documents an UNRELATED `rationale` field whose own description
    naturally contains the word 'rationale', which would make an unscoped
    'rationale in source' check pass vacuously regardless of whether
    measured_value's own text was ever corrected (caught empirically: this
    scoping bug produced a false pass on the first draft of this test)."""
    lines = class_source.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("measured_value:")), None)
    assert start is not None, "could not locate the measured_value: docstring paragraph"
    end = start + 1
    while end < len(lines) and lines[end].strip() and not lines[end].strip().endswith(":") and lines[end].startswith(" " * 8):
        end += 1
    return "\n".join(lines[start:end])


def test_ac10_logic_change_docstring_describes_actual_display_only_behavior():
    """The corrected docstring must describe what measured_value ACTUALLY
    does (display-only, does not drive tweak generation) — a fix that merely
    deletes the false claim without saying anything true is still a
    documentation defect (silence isn't honesty, per the project's
    doc-writer standard). Scoped to measured_value's OWN paragraph (see
    _measured_value_paragraph) to avoid a vacuous pass via the unrelated
    `rationale` field's docstring."""
    import advisors.logic_change_engine as lce

    paragraph = _measured_value_paragraph(inspect.getsource(lce.LogicChangeObjective)).lower()
    assert "display" in paragraph or "does not" in paragraph or "not drive" in paragraph, (
        f"AC-10 GAP: the corrected LogicChangeObjective measured_value "
        f"docstring paragraph does not describe its actual display-only "
        f"role. Paragraph: {paragraph!r}"
    )


def test_ac10_swap_docstring_describes_actual_display_only_behavior():
    import advisors.asset_swap_engine as ase

    paragraph = _measured_value_paragraph(inspect.getsource(ase.SwapObjective)).lower()
    assert "display" in paragraph or "does not" in paragraph or "not drive" in paragraph, (
        f"AC-10 GAP: the corrected SwapObjective measured_value docstring "
        f"paragraph does not describe its actual display-only role. "
        f"Paragraph: {paragraph!r}"
    )


# ===========================================================================
# Route-level honesty — the exact fabricated phrase must be gone from the
# rendered rationale.
# ===========================================================================


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _mock_symphony_resolution(monkeypatch, raw_tree: dict, composer_hash: str = "HASH1", symphony_name: str = "Test Symphony"):
    import database

    monkeypatch.setattr(database, "load_state", lambda: {composer_hash: {"name": symphony_name}})
    monkeypatch.setattr("symphony_logic.fetch_symphony_score", lambda h: raw_tree)


def test_ac10_asset_swap_evaluate_response_drops_fabricated_measured_drawdown(client, monkeypatch):
    """MUST FAIL pre-fix: today the reduce_drawdown-objective rationale
    literally reads '...targets reduction of the measured 0.0% drawdown...'
    regardless of any real drawdown data.

    NOTE (empirically discovered, not guessed): the route's DEFAULT
    objective_type for asset-swaps-evaluate is 'reduce_correlation'
    (app.py's `body.get("objective_type", "reduce_correlation")`), whose
    rationale branch reads '...addresses the 0.00 correlation...' — NO literal
    'measured' word in that specific branch (unlike the 'reduce_drawdown' and
    'lift_risk_adjusted' branches, which both say 'the measured X'). The
    first draft of this test used the default objective_type and passed
    VACUOUSLY (the checked substring was never present in either the honest
    or dishonest state) — caught by inspecting the actual asset_swap_engine.py
    _build_objective_rationale source rather than trusting the audit's
    paraphrase alone. Explicitly requesting objective_type=reduce_drawdown
    below targets the branch that genuinely contains the fabricated phrase."""
    import advisors.asset_swap_engine as ase
    from advisors.composer_backtest_client import BacktestResult

    raw_tree = {"ticker": None, "children": [{"ticker": "AAA", "children": []}]}
    _mock_symphony_resolution(monkeypatch, raw_tree)

    def _side_effect(tree, *, symphony_id="", **kwargs):
        return BacktestResult(stats={"sharpe": 0.1}, data_warnings=[], daily_returns={"2026-01-01": 0.001})

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "run_backtest", side_effect=_side_effect),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        resp = client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "from_ticker": "AAA",
                "to_ticker": "CAND0",
                "objective_type": "reduce_drawdown",
            },
        )

    assert resp.status_code == 200
    body = resp.get_json()
    rationale = str(body.get("objective_rationale", ""))
    assert "measured 0.0%" not in rationale, (
        f"AC-10 GAP: asset-swap evaluate (reduce_drawdown) rationale still "
        f"carries the fabricated-looking 'measured 0.0%' phrase. "
        f"rationale={rationale!r}"
    )


def test_ac10_logic_change_evaluate_response_drops_fabricated_measured_drawdown(client, monkeypatch):
    """MUST FAIL pre-fix: today the rationale literally reads 'targets
    reduction of the measured 0.0% drawdown...' regardless of any real
    drawdown data."""
    import advisors.logic_change_engine as lce
    from advisors.composer_backtest_client import BacktestResult

    raw_tree = {"type": "root", "children": [{"type": "momentum", "window": 20, "children": []}]}
    _mock_symphony_resolution(monkeypatch, raw_tree)

    def _side_effect(tree, *, symphony_id="", **kwargs):
        return BacktestResult(stats={"sharpe": 0.1}, data_warnings=[], daily_returns={"2026-01-01": 0.001})

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "run_backtest", side_effect=_side_effect),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        resp = client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "change_description": "Reduce window from 20d to 16d to improve drawdown reactivity",
            },
        )

    assert resp.status_code == 200
    body = resp.get_json()
    rationale = str(body.get("objective_rationale", ""))
    assert "measured 0.0%" not in rationale, (
        f"AC-10 GAP: logic-change evaluate rationale still carries the "
        f"fabricated-looking 'measured 0.0%' phrase. rationale={rationale!r}"
    )
