"""
Cycle fix-live-claude — RED: producer/consumer contract on four_gates_verdict
key naming.

Defect (surfaced by spec-live-claude during review of cycle/fix-live-claude
GREEN at 5122bf7):

    * Producer: ``app._compute_suggestion_gates`` at ``app.py:2238`` returns the
      gate dict with key ``"oos"``.
    * Consumer: ``static/ai_advisor.js:140`` declares
      ``GATE_LABELS = ['allowlist', 'risk_direction', 'oos_frozen_eval',
      'locked_vars']`` and looks each key up directly on
      ``suggestion.four_gates_verdict``.
    * Dev fixture: ``app.py`` ``_DEV_ADVISOR_FIXTURE`` entries (lines 2278,
      2301, 2324) already use ``"oos_frozen_eval"`` — the dev path looks
      correct in the UI because the fixture is the canonical contract.
    * Real Claude path → producer emits ``"oos"`` → JS lookup misses → OOS
      gate badge always renders unknown/gray.

The canonical key is ``oos_frozen_eval`` (per static/ai_advisor.js
GATE_LABELS and the dev fixture). The producer must emit it.

These tests fail today and demand the rename. Two of them — the JS↔Python
contract test and the dev↔real parity test — would have caught this defect
before it reached production if they had existed.

No new skip/xfail markers are introduced.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# The canonical gate keys, in their canonical order.
# Source of truth: static/ai_advisor.js GATE_LABELS — anything Python emits
# must match.
# ---------------------------------------------------------------------------

_CANONICAL_GATE_KEYS = ("allowlist", "risk_direction", "oos_frozen_eval", "locked_vars")


def _build_suggestion(oos_status: str = "passed"):
    """Construct a minimal ConfigSuggestion for gate computation.

    Uses a suggestible-allowlisted key so the allowlist gate is true; risk
    direction is left as 'neutral' so check_risk_direction_agreement is
    well-behaved. The test is about KEY NAMING, not gate truth-values, so
    we keep the suggestion benign.
    """
    import ai_advisor

    return ai_advisor.ConfigSuggestion(
        config_key="MAX_SQUEEZE_FLOOR",
        current_value=0.20,
        suggested_value=0.20,
        rationale="contract test — shape, not value",
        risk_direction="neutral",
        confidence="medium",
        data_sufficiency="sufficient",
        oos_status=oos_status,
    )


# ---------------------------------------------------------------------------
# Test 1 — The producer emits the canonical key set.
# This is the single test that closes the defect: it asserts the four gate
# keys _compute_suggestion_gates returns are exactly the four GATE_LABELS
# declared in ai_advisor.js. Today the producer emits 'oos' instead of
# 'oos_frozen_eval' so this fails.
# ---------------------------------------------------------------------------


def test_compute_suggestion_gates_returns_canonical_gate_keys():
    """``_compute_suggestion_gates`` must return a dict whose keys are
    EXACTLY the four canonical gate keys consumed by the renderer.

    Canonical set comes from static/ai_advisor.js ``GATE_LABELS``::

        ['allowlist', 'risk_direction', 'oos_frozen_eval', 'locked_vars']

    If the producer emits ``"oos"`` instead of ``"oos_frozen_eval"`` the OOS
    badge silently renders as unknown/gray on every real Claude suggestion,
    because the JS does a direct key lookup on the gate dict.
    """
    import app

    suggestion = _build_suggestion(oos_status="passed")

    # The function reads three live dependencies — mock to keep the contract
    # test focused on the KEY NAMING of the returned dict, not the truth of
    # any individual gate.
    with (
        patch.object(
            app.ai_advisor,
            "enforce_suggestion_allowlist",
            return_value=([suggestion], []),
        ),
        patch.object(
            app.ai_advisor,
            "check_risk_direction_agreement",
            return_value={"agrees": True},
        ),
        patch.object(
            app.ai_advisor,
            "_read_current_strategy",
            return_value=({}, set()),
        ),
    ):
        gates = app._compute_suggestion_gates(suggestion, symphony_id="test-symphony")

    assert isinstance(gates, dict), (
        "_compute_suggestion_gates must return a dict of gate-key -> bool"
    )
    assert set(gates.keys()) == set(_CANONICAL_GATE_KEYS), (
        f"_compute_suggestion_gates produced gate keys {sorted(gates.keys())!r}; "
        f"expected exactly {sorted(_CANONICAL_GATE_KEYS)!r} (the four labels in "
        f"static/ai_advisor.js GATE_LABELS). If 'oos' is present instead of "
        f"'oos_frozen_eval', that is the documented producer/consumer key drift: "
        f"the JS renderer does a direct key lookup so the OOS badge renders as "
        f"unknown/gray for every real Claude suggestion."
    )


# ---------------------------------------------------------------------------
# Test 2 — JS↔Python key contract.
# Parse GATE_LABELS out of ai_advisor.js and assert that every label the JS
# declares is produced by the Python helper. This is the contract test that
# would have caught the defect before it reached production.
# ---------------------------------------------------------------------------


def _parse_js_gate_labels() -> list[str]:
    """Extract GATE_LABELS from static/ai_advisor.js by source-text parse.

    Why parse the JS rather than hard-code: this test is the *contract* —
    when GATE_LABELS changes, this test must follow the JS. Hard-coding the
    list here would let JS-side drift past unnoticed; sourcing from the file
    keeps the assertion adversarial.
    """
    js_path = Path(__file__).resolve().parents[2] / "static" / "ai_advisor.js"
    src = js_path.read_text(encoding="utf-8")
    match = re.search(r"GATE_LABELS\s*=\s*\[([^\]]+)\]", src)
    assert match, "could not locate GATE_LABELS in static/ai_advisor.js"
    items = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
    assert items, "GATE_LABELS parsed empty — JS source may have drifted"
    return items


def test_js_gate_labels_match_python_producer_keys():
    """Every label in ``GATE_LABELS`` in static/ai_advisor.js must be a key
    that ``_compute_suggestion_gates`` returns, and vice versa.

    Both directions matter:
      * JS → Py: any label the renderer expects but the producer omits
        renders as unknown (the current defect mode).
      * Py → JS: any key the producer emits but the renderer ignores wastes
        the gate.
    """
    import app

    js_labels = _parse_js_gate_labels()

    suggestion = _build_suggestion(oos_status="passed")
    with (
        patch.object(
            app.ai_advisor,
            "enforce_suggestion_allowlist",
            return_value=([suggestion], []),
        ),
        patch.object(
            app.ai_advisor,
            "check_risk_direction_agreement",
            return_value={"agrees": True},
        ),
        patch.object(
            app.ai_advisor,
            "_read_current_strategy",
            return_value=({}, set()),
        ),
    ):
        py_keys = list(
            app._compute_suggestion_gates(suggestion, symphony_id="test-symphony").keys()
        )

    js_set = set(js_labels)
    py_set = set(py_keys)

    missing_from_py = js_set - py_set
    extra_in_py = py_set - js_set

    assert not missing_from_py, (
        f"JS GATE_LABELS includes {sorted(missing_from_py)!r} but the Python "
        f"producer does not — these badges will render as unknown/gray on "
        f"every real Claude suggestion."
    )
    assert not extra_in_py, (
        f"Python producer emits {sorted(extra_in_py)!r} but JS GATE_LABELS "
        f"does not consume it — the gate is silently dropped by the renderer."
    )


# ---------------------------------------------------------------------------
# Test 3 — Dev-fixture ↔ real-path parity.
# The _DEV_ADVISOR_FIXTURE entries hand-author four_gates_verdict using the
# canonical key 'oos_frozen_eval'. The real producer must use the same key.
# This is the parity check that catches the same defect from the other side
# (dev path is correct; real path drifted).
# ---------------------------------------------------------------------------


def test_dev_advisor_fixture_and_real_producer_use_same_gate_keys():
    """``_DEV_ADVISOR_FIXTURE`` entries (the developer-facing canned data)
    declare ``four_gates_verdict`` with the canonical key set. The real
    producer ``_compute_suggestion_gates`` must produce the SAME key set.

    Two paths shipping different keys is the bug class that produced the
    OOS-badge-always-unknown failure mode.
    """
    import app

    # The dev fixture is a module-level list of suggestion dicts; pull every
    # four_gates_verdict key out of it.
    fixture_keys: set[str] = set()
    for entry in app._DEV_ADVISOR_FIXTURE:
        verdict = entry.get("four_gates_verdict")
        assert isinstance(verdict, dict), (
            f"every _DEV_ADVISOR_FIXTURE entry must carry a four_gates_verdict "
            f"dict; got {type(verdict).__name__} for {entry.get('config_key')}"
        )
        fixture_keys.update(verdict.keys())

    suggestion = _build_suggestion(oos_status="passed")
    with (
        patch.object(
            app.ai_advisor,
            "enforce_suggestion_allowlist",
            return_value=([suggestion], []),
        ),
        patch.object(
            app.ai_advisor,
            "check_risk_direction_agreement",
            return_value={"agrees": True},
        ),
        patch.object(
            app.ai_advisor,
            "_read_current_strategy",
            return_value=({}, set()),
        ),
    ):
        producer_keys = set(
            app._compute_suggestion_gates(suggestion, symphony_id="test-symphony").keys()
        )

    assert fixture_keys == producer_keys, (
        f"_DEV_ADVISOR_FIXTURE uses keys {sorted(fixture_keys)!r} but the "
        f"live producer emits {sorted(producer_keys)!r}. Two paths shipping "
        f"two different key sets is the producer/consumer drift class that "
        f"caused the OOS badge to render as unknown/gray for real Claude "
        f"suggestions."
    )


# ---------------------------------------------------------------------------
# Test 4 — Truth-value preservation across the rename.
# The contract is keys + values. After the rename, the value at the
# canonical OOS key must still reflect ``suggestion.oos_status == 'passed'``
# so the rename does not silently flip the gate's polarity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "oos_status,expected_gate_value",
    [
        ("passed", True),
        ("rejected", False),
        ("pending", False),
    ],
)
def test_oos_frozen_eval_gate_value_reflects_oos_status(oos_status, expected_gate_value):
    """The OOS gate's boolean value must equal ``oos_status == 'passed'``.

    Verifies the rename keeps the truth-value semantics intact; an
    implementer who renames the key but accidentally flips the predicate
    fails this test.
    """
    import app

    suggestion = _build_suggestion(oos_status=oos_status)
    with (
        patch.object(
            app.ai_advisor,
            "enforce_suggestion_allowlist",
            return_value=([suggestion], []),
        ),
        patch.object(
            app.ai_advisor,
            "check_risk_direction_agreement",
            return_value={"agrees": True},
        ),
        patch.object(
            app.ai_advisor,
            "_read_current_strategy",
            return_value=({}, set()),
        ),
    ):
        gates = app._compute_suggestion_gates(suggestion, symphony_id="test-symphony")

    assert "oos_frozen_eval" in gates, (
        "the canonical OOS gate key is 'oos_frozen_eval'; "
        "if 'oos' is present instead, the JS renderer's direct key lookup "
        "misses and the badge renders as unknown/gray"
    )
    assert gates["oos_frozen_eval"] is expected_gate_value, (
        f"oos_frozen_eval gate must be {expected_gate_value!r} when "
        f"oos_status={oos_status!r}; got {gates['oos_frozen_eval']!r}"
    )
