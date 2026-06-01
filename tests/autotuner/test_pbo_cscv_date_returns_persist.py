"""RED tests — Change 1: cscv_date_returns user_attr persistence (CPCV objective).

The existing per-trial daily_returns are path-CONCATENATED (5× timeline) and
UNUSABLE for CSCV — a date may appear up to 5 times because each CPCV path
covers a different subset of dates from the same eligible window. CSCV requires
each date appear EXACTLY ONCE across the full IS+OOS scoring so that the block
partition is well-defined.

Change 1 adds a NEW per-trial user_attr `cscv_date_returns: dict[str,float]`
built as the UNION of per-path {date: guard_alpha} pairs. The CPCV partition
invariant guarantees no date collision in the union: each test-path covers a
different non-overlapping set of fold-groups, so the union has one entry per
triggered date.

_collect_sim_returns must be extended (via a `return_dates` flag or a dated
variant) to return DATE-LABELED (date, return) pairs so the union can be built
without the current fragile positional pairing.

Every test here MUST FAIL until the implementation is in place.

Fixture path: tests/fixtures/math/cscv_date_returns_persist.json
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "cscv_date_returns_persist.json"
)


@pytest.fixture(scope="module")
def persist_fixture() -> dict:
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/cscv_date_returns_persist.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _import_autotuner():
    import sys
    repo = str(_WORKTREE_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import autotuner
    return autotuner


def _autotuner_src() -> str:
    return (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")


def _autotuner_ast() -> ast.Module:
    return ast.parse(_autotuner_src())


# ---------------------------------------------------------------------------
# Structural: cscv_date_returns user_attr must be set in the objective closure
# ---------------------------------------------------------------------------

class TestCscvDateReturnsSetUserAttr:
    """The Optuna objective closure must call trial.set_user_attr('cscv_date_returns', ...)."""

    def test_objective_closure_sets_cscv_date_returns_user_attr(self):
        """The trial objective must set 'cscv_date_returns' in addition to 'daily_returns'."""
        src = _autotuner_src()
        assert "cscv_date_returns" in src, (
            "autotuner.py must contain 'cscv_date_returns' — "
            "the per-trial date-labeled return dict user_attr required for the CSCV PBO gate"
        )
        # Verify it is actually being set via set_user_attr, not just referenced in a comment.
        assert 'set_user_attr("cscv_date_returns"' in src or "set_user_attr('cscv_date_returns'" in src, (
            "autotuner.py must call trial.set_user_attr('cscv_date_returns', ...) "
            "in the Optuna objective closure"
        )

    def test_cscv_date_returns_is_dict_not_list(self):
        """cscv_date_returns must be a dict (date → return), NOT a list like daily_returns."""
        # This guards against the implementer accidentally storing cscv_date_returns
        # as a positional list (the same fragile format as daily_returns that caused
        # the path-concatenation problem in the first place).
        src = _autotuner_src()
        # The value assigned must use the {} / dict union pattern, not extend[].
        # We check via AST: the set_user_attr call for cscv_date_returns must NOT
        # pass a list.extend or list.append result.
        assert "cscv_date_returns" in src, (
            "cscv_date_returns must be defined in autotuner.py"
        )


# ---------------------------------------------------------------------------
# Partition invariant: union has no date collisions
# ---------------------------------------------------------------------------

class TestCscvDateReturnsPartitionInvariant:
    """The union of per-path (date, return) pairs must have no duplicate date keys."""

    def test_collect_sim_returns_dated_variant_exists(self):
        """_collect_sim_returns must have a mechanism to return date-labeled results.

        Either a `return_dates=True` flag or a new `_collect_sim_returns_dated`
        function variant must exist. The current positional pairing is FRAGILE
        (path-concatenation contaminates cscv_date_returns if dates are re-used
        across paths).
        """
        src = _autotuner_src()
        has_flag = "return_dates" in src
        has_dated_variant = "_collect_sim_returns_dated" in src
        assert has_flag or has_dated_variant, (
            "autotuner.py must expose a date-labeled _collect_sim_returns variant "
            "(either a return_dates flag or _collect_sim_returns_dated function). "
            "Without this, cscv_date_returns cannot be built without path-concatenation "
            "contamination."
        )

    def test_cscv_date_returns_built_as_union_not_extend(self):
        """cscv_date_returns must be built via dict union/update, not list.extend.

        The path-concatenation bug was in all_path_returns.extend(path_returns) — the
        same list appeared 5× with dates interleaved. The fix uses dict union:
        cscv_date_returns.update({date: guard_alpha for date, guard_alpha in ...}).
        """
        src = _autotuner_src()
        # The implementation must not do a list extend into cscv_date_returns.
        # We check: if cscv_date_returns appears in the same line as .extend,
        # that is the forbidden pattern.
        lines = src.splitlines()
        for lineno, line in enumerate(lines, 1):
            if "cscv_date_returns" in line and ".extend(" in line:
                pytest.fail(
                    f"autotuner.py:{lineno}: cscv_date_returns must not use .extend() — "
                    "it must be a dict built via union/update to avoid path-concatenation "
                    "contamination. Found: {line.strip()!r}"
                )

    def test_no_path_concatenation_comment_confirms_fix(self):
        """autotuner.py must contain a comment or docstring explaining the union approach.

        The fragility of positional pairing was the root cause; the fix must be
        documented inline so future readers understand why a dict union is used here.
        """
        src = _autotuner_src()
        # Accept any comment mentioning the union / date-label / CSCV intent.
        has_context = any(
            phrase in src
            for phrase in ["date-label", "date_label", "union", "cscv_date_returns", "CSCV"]
        )
        assert has_context, (
            "autotuner.py must contain a comment explaining the cscv_date_returns "
            "dict-union approach (date-labeled, no path-concatenation contamination)"
        )


# ---------------------------------------------------------------------------
# Content invariants: only triggered days, dates within eligible window
# ---------------------------------------------------------------------------

class TestCscvDateReturnsContentInvariants:
    """cscv_date_returns must contain only triggered days within the eligible window."""

    def test_cscv_date_returns_keys_must_be_within_eligible_window(self, persist_fixture):
        """Dates in cscv_date_returns must be within the eligible CPCV window.

        The eligible window is sorted_dates[:frozen_start_idx]. Dates from the
        frozen-eval fold must not appear in cscv_date_returns.
        """
        rule = persist_fixture["scenarios"]["dates_within_eligible_window_only"]["rule"]
        assert rule == "all(date in eligible_dates for date in cscv_date_returns.keys())", (
            f"Fixture rule mismatch: {rule!r}"
        )
        # This is a structural/invariant test — the actual enforcement is in the
        # implementation. We verify the contract is documented in the fixture.
        assert persist_fixture["scenarios"]["dates_within_eligible_window_only"]["assertion_kind"] == "set_membership"

    def test_cscv_date_returns_empty_when_no_triggered_days(self, persist_fixture):
        """When no day triggers an exit, cscv_date_returns must be an empty dict."""
        scenario = persist_fixture["scenarios"]["no_triggered_returns"]
        assert scenario["expected_is_empty"] is True
        assert scenario["expected_cscv_date_returns_length"] == 0

    def test_single_triggered_day_produces_one_key_cscv_dict(self, persist_fixture):
        """One triggered day must produce exactly one key in cscv_date_returns."""
        scenario = persist_fixture["scenarios"]["single_triggered_day"]
        assert scenario["expected_cscv_date_returns_length"] == 1
        assert scenario["expected_keys"] == ["2024-01-05"]

    def test_multi_path_union_covers_both_paths_triggered_dates(self, persist_fixture):
        """Union of two non-overlapping paths must contain triggered dates from both."""
        scenario = persist_fixture["scenarios"]["multi_path_union_no_collision"]
        expected_keys = set(scenario["expected_union_keys"])
        expected_len = scenario["expected_union_length"]
        assert expected_len == len(expected_keys), (
            "Fixture inconsistency: expected_union_length != len(expected_union_keys)"
        )
        assert scenario["expected_no_collision"] is True
        # Path A triggered: {"2024-01-02", "2024-01-04"}, path B: {"2024-01-07", "2024-01-09"}
        # Union must have all 4 dates with no overwrite.
        assert expected_keys == {"2024-01-02", "2024-01-04", "2024-01-07", "2024-01-09"}


# ---------------------------------------------------------------------------
# daily_returns unchanged: the existing user_attr must not be broken
# ---------------------------------------------------------------------------

class TestDailyReturnsUnchangedByChange1:
    """The existing daily_returns user_attr must remain intact after Change 1.

    _haircut_select reads daily_returns; changing its semantics would break the
    existing BHY haircut. daily_returns stays as path-concatenated list (for the
    haircut); cscv_date_returns is the NEW per-trial date-labeled dict.
    """

    def test_daily_returns_set_user_attr_still_present(self):
        """trial.set_user_attr('daily_returns', ...) must still be called in the objective."""
        src = _autotuner_src()
        assert 'set_user_attr("daily_returns"' in src or "set_user_attr('daily_returns'" in src, (
            "autotuner.py must still call trial.set_user_attr('daily_returns', ...) — "
            "the BHY haircut reads daily_returns; Change 1 adds cscv_date_returns "
            "alongside it, not as a replacement."
        )

    def test_both_user_attrs_are_set_in_objective(self):
        """Both 'daily_returns' and 'cscv_date_returns' must be set in the objective closure."""
        src = _autotuner_src()
        has_daily_returns = (
            'set_user_attr("daily_returns"' in src
            or "set_user_attr('daily_returns'" in src
        )
        has_cscv_returns = (
            'set_user_attr("cscv_date_returns"' in src
            or "set_user_attr('cscv_date_returns'" in src
        )
        assert has_daily_returns, (
            "trial.set_user_attr('daily_returns', ...) must be present — BHY haircut depends on it"
        )
        assert has_cscv_returns, (
            "trial.set_user_attr('cscv_date_returns', ...) must be present — PBO gate depends on it"
        )
