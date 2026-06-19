"""
Phase-1 walk-forward window extension: RED tests for AC-3 and AC-6 in autotuner.py.

Acceptance criteria covered here:
  AC-3  autotuner._OOS_USABLE_VALIDATION_DAYS_EXPECTED must REFERENCE
        synthetic_history._WALK_FORWARD_TRADING_DAYS (imported), not a
        hardcoded literal '125'. Its value must equal
        int(250 * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS = 29.
        The oos_fold_collapse_pin.json fixture is also amended (logged
        Amendment — see below).
  AC-6  Literal '125' must not survive in docstrings, print statements, or
        co-located comments in autotuner.py that describe the WFA window size.

LOGGED AMENDMENT (required by test_oos_fold_collapse.py):
  Fixture tests/fixtures/autotuner/oos_fold_collapse/oos_fold_collapse_pin.json
  has been amended as part of the Phase-1 walk-forward window extension
  (feature-plans/walk-forward-overhaul.md Phase 1, GATE-2 approved 2026-06-01):
    history_length:          125  ->  250
    usable_validation_days:    4  ->   29   (int(250*0.2) - 20 - 1)
  The change is intentional and load-bearing. The prior test contract
  (test_oos_fold_collapse.py) already derives usable_validation_days from the
  fixture's history_length via the named constants (TRAIN_RATIO, VALIDATION_RATIO,
  PURGE_DAYS, EMBARGO_DAYS), so the arithmetic cross-check is preserved.

Feature plan: feature-plans/walk-forward-overhaul.md Phase 1.
Merge-base: 2f8ce8a.

No producer-computed dollar/rate/percent values are asserted. All numeric
expectations are derived from named autotuner constants (VALIDATION_RATIO,
PURGE_DAYS, EMBARGO_DAYS) applied to the new history_length = 250.
"""

from __future__ import annotations

import ast as _ast
import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_SRC = _REPO_ROOT / "autotuner.py"
_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "autotuner"
    / "oos_fold_collapse"
    / "oos_fold_collapse_pin.json"
)


def _autotuner_source() -> str:
    assert _AUTOTUNER_SRC.is_file(), f"expected autotuner.py at {_AUTOTUNER_SRC}"
    return _AUTOTUNER_SRC.read_text(encoding="utf-8")


def _autotuner_tree() -> _ast.Module:
    return _ast.parse(_autotuner_source(), filename=str(_AUTOTUNER_SRC))


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Expected values (Phase-1 targets derived from named constants)
# ---------------------------------------------------------------------------

# History length after Phase-1 (not read from autotuner — this is the
# test's own ground truth that the implementer must match).
_EXPECTED_HISTORY_LENGTH = 250


# ===========================================================================
# AC-3 — _OOS_USABLE_VALIDATION_DAYS_EXPECTED references the shared constant
# ===========================================================================


class TestOOSConstantReferencesSharedWindow:
    """
    _OOS_USABLE_VALIDATION_DAYS_EXPECTED must be derived from
    synthetic_history._WALK_FORWARD_TRADING_DAYS (imported), not the
    hardcoded literal 125.

    This is the drift-prevention contract: if the window constant in
    synthetic_history.py is ever changed again, the OOS expected value
    automatically recomputes and any mismatch trips the cross-check test
    (test_named_constant_equals_derived_formula in test_oos_fold_collapse.py).

    Adversarial angles:
    - An implementer who edits only the _WALK_FORWARD_TRADING_DAYS constant
      in synthetic_history.py but leaves `int(125 * ...)` in autotuner.py
      is caught by the 'no hardcoded 125 in RHS' test.
    - An implementer who imports synthetic_history but accesses the constant
      via a different attribute path (e.g. ``sh.WALK_FORWARD_TRADING_DAYS``
      without the underscore) is caught by the exact-name test.
    """

    def test_oos_constant_rhs_does_not_hardcode_125(self):
        """The RHS of _OOS_USABLE_VALIDATION_DAYS_EXPECTED must not contain
        a bare integer literal 125.

        Static analysis: find the module-level assignment for
        _OOS_USABLE_VALIDATION_DAYS_EXPECTED and walk its RHS; fail if any
        Constant node carries the value 125 (the old hardcoded window).
        """
        tree = _autotuner_tree()
        for node in tree.body:
            if not isinstance(node, _ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == "_OOS_USABLE_VALIDATION_DAYS_EXPECTED":
                    rhs = node.value
                    for sub in _ast.walk(rhs):
                        if isinstance(sub, _ast.Constant) and sub.value == 125:
                            pytest.fail(
                                "_OOS_USABLE_VALIDATION_DAYS_EXPECTED RHS "
                                "still contains the hardcoded literal 125. "
                                "Phase-1 requires this to reference "
                                "synthetic_history._WALK_FORWARD_TRADING_DAYS "
                                "so the two constants stay coupled. RED until "
                                "the literal is replaced with the imported "
                                "constant."
                            )
                    return
        pytest.fail(
            "Could not find module-level assignment for "
            "_OOS_USABLE_VALIDATION_DAYS_EXPECTED in autotuner.py. "
            "The constant must exist at module scope."
        )

    def test_oos_constant_rhs_references_walk_forward_trading_days(self):
        """The RHS of _OOS_USABLE_VALIDATION_DAYS_EXPECTED must reference
        synthetic_history._WALK_FORWARD_TRADING_DAYS or an alias of it.

        Static analysis: the RHS must contain either an Attribute access
        (``synthetic_history._WALK_FORWARD_TRADING_DAYS`` or
        ``sh._WALK_FORWARD_TRADING_DAYS``) or a Name that resolves to the
        imported constant. We accept any Attribute access whose `.attr`
        is ``_WALK_FORWARD_TRADING_DAYS``.
        """
        tree = _autotuner_tree()
        for node in tree.body:
            if not isinstance(node, _ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == "_OOS_USABLE_VALIDATION_DAYS_EXPECTED":
                    rhs = node.value
                    # Check for Attribute access on synthetic_history.
                    for sub in _ast.walk(rhs):
                        if (
                            isinstance(sub, _ast.Attribute)
                            and sub.attr == "_WALK_FORWARD_TRADING_DAYS"
                        ):
                            return  # found — GREEN
                    pytest.fail(
                        "_OOS_USABLE_VALIDATION_DAYS_EXPECTED RHS does not "
                        "reference _WALK_FORWARD_TRADING_DAYS from "
                        "synthetic_history. The coupling requires an attribute "
                        "access such as "
                        "`synthetic_history._WALK_FORWARD_TRADING_DAYS`. RED "
                        "until the RHS references the shared constant."
                    )
        pytest.fail(
            "Could not find module-level assignment for "
            "_OOS_USABLE_VALIDATION_DAYS_EXPECTED in autotuner.py."
        )

    def test_oos_constant_runtime_value_equals_29(self):
        """At runtime, _OOS_USABLE_VALIDATION_DAYS_EXPECTED must equal 29.

        int(250 * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS
          = int(250 * 0.20) - 20 - 1
          = 50 - 21
          = 29

        This assertion verifies the implementation arrived at the correct
        derived value. It uses the autotuner's own constants to reproduce
        the derivation — no magic numbers.
        """
        import autotuner

        # Derive expected value from autotuner's own ratio + purge/embargo
        # constants applied to the new history_length.
        derived = (
            int(_EXPECTED_HISTORY_LENGTH * autotuner.VALIDATION_RATIO)
            - autotuner.PURGE_DAYS
            - autotuner.EMBARGO_DAYS
        )
        # Independent sanity: at 250 days this must be 29.
        assert derived == 29, (
            f"Derivation int({_EXPECTED_HISTORY_LENGTH} * VALIDATION_RATIO) "
            f"- PURGE_DAYS - EMBARGO_DAYS = {derived}; expected 29. This "
            f"means either VALIDATION_RATIO, PURGE_DAYS, or EMBARGO_DAYS "
            f"have drifted unexpectedly."
        )
        actual = autotuner._OOS_USABLE_VALIDATION_DAYS_EXPECTED
        assert actual == derived, (
            f"autotuner._OOS_USABLE_VALIDATION_DAYS_EXPECTED = {actual}; "
            f"Phase-1 requires it to equal "
            f"int({_EXPECTED_HISTORY_LENGTH} * VALIDATION_RATIO) "
            f"- PURGE_DAYS - EMBARGO_DAYS = {derived}. RED until the "
            f"constant is recomputed from the new 250-day window."
        )


# ===========================================================================
# AC-3 — Amended fixture cross-check
# ===========================================================================


class TestOosFoldCollapseFixtureAmended:
    """
    The oos_fold_collapse_pin.json fixture has been amended for the Phase-1
    window extension (logged Amendment). The amended values must be:
      history_length:          250   (was 125)
      usable_validation_days:   29   (was 4; int(250*0.2)-20-1)

    These tests verify the fixture amendment was applied correctly.

    NOTE: The existing tests in test_oos_fold_collapse.py derive
    usable_validation_days from the fixture's history_length via the named
    constants. After this amendment, those tests continue to pass as long as
    the autotuner's VALIDATION_RATIO/PURGE_DAYS/EMBARGO_DAYS are unchanged
    and the implementation correctly references the new window.
    """

    def test_fixture_history_length_updated_to_250(self):
        """The amended fixture must have history_length == 250."""
        pin = _load_fixture()
        actual = pin["history_length"]
        assert actual == 250, (
            f"oos_fold_collapse_pin.json history_length = {actual}; "
            f"Phase-1 amendment requires 250. This is a LOGGED AMENDMENT: "
            f"the Phase-1 walk-forward window extension (feature-plans/"
            f"walk-forward-overhaul.md) changes history_length from 125 to "
            f"250. RED until the fixture is amended."
        )

    def test_fixture_usable_validation_days_updated_to_29(self):
        """The amended fixture must have usable_validation_days == 29."""
        pin = _load_fixture()
        actual = pin["usable_window_post_purge"]["usable_validation_days"]
        assert actual == 29, (
            f"oos_fold_collapse_pin.json usable_validation_days = {actual}; "
            f"Phase-1 amendment requires 29 (int(250*0.2)-20-1). This is a "
            f"LOGGED AMENDMENT: the Phase-1 walk-forward window extension "
            f"changes the usable validation window from 4 to 29 days, which "
            f"is the core motivation for the extension. RED until the fixture "
            f"is amended."
        )

    def test_fixture_raw_fold_sizes_updated(self):
        """The amended fixture's raw_fold_sizes must reflect the 250-day split.

        At 250 days × 60/20/20:
          train_days       = int(250 * 0.60) = 150
          validation_days  = int(250 * 0.80) - int(250 * 0.60) = 200 - 150 = 50
          frozen_eval_days = 250 - int(250 * 0.80) = 250 - 200 = 50
        """
        pin = _load_fixture()
        sizes = pin["raw_fold_sizes"]
        assert sizes["train_days"] == 150, (
            f"raw_fold_sizes.train_days = {sizes['train_days']}; expected 150 "
            f"at history_length=250 with TRAIN_RATIO=0.60. RED until amended."
        )
        assert sizes["validation_days"] == 50, (
            f"raw_fold_sizes.validation_days = {sizes['validation_days']}; "
            f"expected 50 at history_length=250. RED until amended."
        )
        assert sizes["frozen_eval_days"] == 50, (
            f"raw_fold_sizes.frozen_eval_days = {sizes['frozen_eval_days']}; "
            f"expected 50 at history_length=250. RED until amended."
        )

    def test_fixture_named_constant_expected_value_updated(self):
        """The named_constant.expected_value in the fixture must equal 29."""
        pin = _load_fixture()
        actual = pin["named_constant"]["expected_value"]
        assert actual == 29, (
            f"oos_fold_collapse_pin.json named_constant.expected_value = "
            f"{actual}; Phase-1 amendment requires 29. RED until amended."
        )


# ===========================================================================
# AC-6 — No stale "125" literals in autotuner.py WFA-adjacent context
# ===========================================================================


class TestNoStale125InAutotuner:
    """
    After the Phase-1 change, literal '125' must not survive in:
    - the print() call inside run_autotuner that announces the WFA window
    - comments in the docstring / co-located with the WFA window description
      (e.g. "125-day WFA" in the docstring or in the run_autotuner print)

    Adversarial angle: an implementer who edits the constant but leaves the
    announement print as "125-day WFA: ..." or the docstring as "125-day
    history is split" misleads operators and future readers about the
    actual window.
    """

    def test_run_autotuner_print_does_not_announce_125_day_wfa(self):
        """The print() call inside run_autotuner that announces the WFA must
        not contain the stale '125' literal.

        Before Phase-1 the print reads:
          '-> Starting EOD Autotune (125-day WFA: ...'
        After Phase-1 it must be updated (to '250-day WFA' or use the
        constant name).
        """
        tree = _autotuner_tree()
        run_autotuner = None
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "run_autotuner":
                run_autotuner = node
                break
        assert run_autotuner is not None, "run_autotuner not found in autotuner.py"

        offending_lines: list[int] = []
        for node in _ast.walk(run_autotuner):
            if not isinstance(node, _ast.Call):
                continue
            func = node.func
            if not (isinstance(func, _ast.Name) and func.id == "print"):
                continue
            for arg in node.args:
                for sub in _ast.walk(arg):
                    if isinstance(sub, _ast.Constant) and isinstance(sub.value, str):
                        if "125" in sub.value:
                            offending_lines.append(getattr(node, "lineno", "?"))
            for kw in node.keywords:
                for sub in _ast.walk(kw.value):
                    if isinstance(sub, _ast.Constant) and isinstance(sub.value, str):
                        if "125" in sub.value:
                            offending_lines.append(getattr(node, "lineno", "?"))

        assert not offending_lines, (
            f"run_autotuner contains print() call(s) with the stale literal "
            f"'125' at line(s) {offending_lines}. After the Phase-1 window "
            f"extension to 250, the WFA announcement must be updated. RED "
            f"until the literal '125' is replaced with '250' (or the constant "
            f"name) in these print calls."
        )

    def test_run_autotuner_docstring_does_not_describe_125_day_history(self):
        """The run_autotuner docstring must not describe the WFA as a
        '125-day history' or '125-day data scale' or '125-day WFA'.

        The docstring currently contains phrases like:
          '125-day data scale'
          '125-day history is split 60/20/20'
          'At 125-day history, the three raw folds are 75/25/25 days.'
          'expand the operator-data-budget beyond 125 days'
          'At the 125-day operator-data-budget'

        After Phase-1 these must be updated to 250. A stale docstring
        confuses the next reader and misleads operators in the Discord
        EOD report.
        """
        import re

        import autotuner

        doc = autotuner.run_autotuner.__doc__ or ""
        # Find every bare '125' (word-boundary) that appears before 'day'
        # (the '125' in citations like 'beyond 125 days' or '125-day' is the
        # stale marker; citation years like '2025' are excluded by the
        # lookbehind).
        stale = re.findall(r"(?<!\d)125(?!\d)", doc)
        assert not stale, (
            f"run_autotuner docstring still contains {len(stale)} occurrence(s) "
            f"of the stale literal '125' (found: {stale[:5]}...). After Phase-1 "
            f"the docstring must be updated to describe the 250-day window. "
            f"RED until all stale '125' references in the docstring are "
            f"replaced with '250'."
        )

    def test_oos_fold_collapse_docstring_in_run_autotuner_references_250(self):
        """The 'OOS-fold-collapse' section of the run_autotuner docstring
        must describe the 250-day fold sizes, not the old 125-day ones.

        Before Phase-1: 'At 125-day history, the three raw folds are 75/25/25'
        After Phase-1:  'At 250-day history, the three raw folds are 150/50/50'

        The test confirms '250' appears in the docstring — not just that '125'
        is gone. An implementer who deletes the section entirely passes the
        'no 125' test but fails here.
        """
        import autotuner

        doc = autotuner.run_autotuner.__doc__ or ""
        assert "250" in doc, (
            "run_autotuner docstring does not mention '250'. After Phase-1 "
            "the docstring must be updated to describe the 250-day WFA "
            "window. An implementer who removes the OOS-fold-collapse section "
            "entirely would pass the 'no 125' test but fail here — the section "
            "must exist and must be updated. RED until '250' appears in the "
            "docstring."
        )
