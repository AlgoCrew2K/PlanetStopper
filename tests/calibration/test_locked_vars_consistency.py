"""
O4 — Locked-vars consistency: RED tests.

Written BEFORE the fix. All tests WILL FAIL until the implementer:
  1. Excludes every var in database.DEFAULT_LOCKED_VARS from
     autotuner.OPTUNA_SEARCH_SPACE_KEYS AND from the objective() suggest_* calls.
  2. Adds a hard structural filter in ai_advisor.enforce_suggestion_allowlist (or
     a new enforce_locked_vars_filter gate) that rejects any suggestion whose
     config_key is in DEFAULT_LOCKED_VARS — even if the key is in _SUGGESTIBLE_ALLOWLIST.
  3. Removes TRIGGER_THRESHOLD_PCT from OPTUNA_SEARCH_SPACE_KEYS.

Fixture provenance: tests/fixtures/calibration/locked_vars/
  - locked_vars_definition.json — mirrors database.DEFAULT_LOCKED_VARS; read at
    test collection time to parameterize tests. No producer-computed values.
  - ai_advisor_locked_suggestion_fixture.json — schema-derived suggestion shape;
    used to test the AI advisor rejection path.

No inline assertion uses a producer-computed float or a magic literal.
All expected sets are derived from the fixture or from named module attributes.

AC coverage:
  AC-O4.1 — TRIGGER_THRESHOLD_PCT excluded from Optuna suggest path
  AC-O4.2 — ALL DEFAULT_LOCKED_VARS excluded from both paths
  AC-O4.3 — Parametrized: every locked var cannot be suggested by Optuna AND
             cannot be adopted by AI advisor
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES_ROOT = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "calibration"
    / "locked_vars"
)
AUTOTUNER_SRC = pathlib.Path(__file__).parent.parent.parent / "autotuner.py"
AI_ADVISOR_SRC = pathlib.Path(__file__).parent.parent.parent / "ai_advisor.py"
DATABASE_SRC = pathlib.Path(__file__).parent.parent.parent / "database.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_ROOT / name).read_text())


def _autotuner_source() -> str:
    return AUTOTUNER_SRC.read_text(encoding="utf-8")


def _ai_advisor_source() -> str:
    return AI_ADVISOR_SRC.read_text(encoding="utf-8")


def _database_source() -> str:
    return DATABASE_SRC.read_text(encoding="utf-8")


def _parse_ast(source: str) -> ast.Module:
    return ast.parse(source)


def _find_module_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    assignments: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                assignments[node.target.id] = node.value
    return assignments


def _extract_frozenset_strings(node: ast.expr) -> set[str] | None:
    """Extract string members from a frozenset({...}) or set literal call node."""
    # frozenset({...}) or frozenset([...]) or {literal set}
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("frozenset", "set"):
            if node.args:
                inner = node.args[0]
                if isinstance(inner, (ast.Set, ast.List, ast.Tuple)):
                    result = set()
                    for elt in inner.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            result.add(elt.value)
                    return result
    if isinstance(node, ast.Set):
        result = set()
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                result.add(elt.value)
        return result
    return None


def _live_locked_vars() -> list[str]:
    """Import database at runtime and return DEFAULT_LOCKED_VARS."""
    import importlib
    import sys
    # database has no heavy side effects on import; safe to import directly.
    if "database" in sys.modules:
        db = sys.modules["database"]
        importlib.reload(db)
    else:
        import database as db  # type: ignore[import]
    return list(getattr(db, "DEFAULT_LOCKED_VARS", []))


def _locked_var_names_from_fixture() -> list[str]:
    fix = _load_fixture("locked_vars_definition.json")
    return fix["locked_vars"]


def _non_locked_var_names_from_fixture() -> list[str]:
    fix = _load_fixture("locked_vars_definition.json")
    return fix["non_locked_vars_sample"]


# ---------------------------------------------------------------------------
# Class 1: Single source of truth — DEFAULT_LOCKED_VARS lives ONLY in database.py
# ---------------------------------------------------------------------------

class TestSingleSourceOfTruth:
    """
    DEFAULT_LOCKED_VARS must be defined in exactly one place: database.py.
    No redefinition or shadowing in autotuner.py or ai_advisor.py.
    """

    def test_default_locked_vars_defined_in_database_only(self):
        """
        database.py defines DEFAULT_LOCKED_VARS; autotuner.py and ai_advisor.py
        must NOT redefine it (they may reference it via import).
        """
        auto_src = _autotuner_source()
        advisor_src = _ai_advisor_source()

        auto_defines = bool(re.search(r"^DEFAULT_LOCKED_VARS\s*=", auto_src, re.MULTILINE))
        advisor_defines = bool(re.search(r"^DEFAULT_LOCKED_VARS\s*=", advisor_src, re.MULTILINE))

        assert not auto_defines, (
            "autotuner.py must NOT define DEFAULT_LOCKED_VARS — "
            "it must consume the single canonical definition from database.py. "
            "RED until duplicate definition is removed."
        )
        assert not advisor_defines, (
            "ai_advisor.py must NOT define DEFAULT_LOCKED_VARS — "
            "it must consume the single canonical definition from database.py. "
            "RED until duplicate definition is removed."
        )

    def test_fixture_locked_vars_match_live_database_definition(self):
        """
        The fixture locked_vars list must match database.DEFAULT_LOCKED_VARS exactly.
        If this fails, either the fixture or the source has drifted.
        """
        fixture_locked = set(_locked_var_names_from_fixture())
        live_locked = set(_live_locked_vars())
        assert fixture_locked == live_locked, (
            f"Fixture locked_vars {sorted(fixture_locked)} != "
            f"database.DEFAULT_LOCKED_VARS {sorted(live_locked)}. "
            "Update the fixture to match the canonical source, or vice versa."
        )

    def test_database_default_locked_vars_is_nonempty(self):
        """DEFAULT_LOCKED_VARS must contain at least one var — empty list is a silent defect."""
        live_locked = _live_locked_vars()
        assert len(live_locked) > 0, (
            "database.DEFAULT_LOCKED_VARS is empty — the lock mechanism has no effect. "
            "At minimum TRIGGER_THRESHOLD_PCT must be locked."
        )

    def test_trigger_threshold_pct_is_locked(self):
        """TRIGGER_THRESHOLD_PCT specifically must be in DEFAULT_LOCKED_VARS (AC-O4.1)."""
        live_locked = _live_locked_vars()
        assert "TRIGGER_THRESHOLD_PCT" in live_locked, (
            "TRIGGER_THRESHOLD_PCT must be in database.DEFAULT_LOCKED_VARS. "
            "Currently absent — this is the O4 defect (AC-O4.1). RED until fixed."
        )


# ---------------------------------------------------------------------------
# Class 2: Optuna search space — locked vars must be absent
# ---------------------------------------------------------------------------

class TestOptunaSearchSpaceExcludesLockedVars:
    """
    OPTUNA_SEARCH_SPACE_KEYS (the set Optuna uses as a validation contract)
    must NOT contain any var in DEFAULT_LOCKED_VARS.
    The objective() closure must NOT call trial.suggest_* for any locked var.
    """

    def test_optuna_search_space_keys_excludes_trigger_threshold_pct(self):
        """
        OPTUNA_SEARCH_SPACE_KEYS must not contain TRIGGER_THRESHOLD_PCT.
        RED: currently it does (autotuner.py:20-24).
        """
        import sys
        # Importing autotuner has optuna side effects — read via AST instead.
        src = _autotuner_source()
        tree = _parse_ast(src)
        assignments = _find_module_assignments(tree)

        assert "OPTUNA_SEARCH_SPACE_KEYS" in assignments, (
            "OPTUNA_SEARCH_SPACE_KEYS not found in autotuner.py — unexpected."
        )
        members = _extract_frozenset_strings(assignments["OPTUNA_SEARCH_SPACE_KEYS"])
        assert members is not None, (
            "Could not parse OPTUNA_SEARCH_SPACE_KEYS as a frozenset literal — "
            "update the AST helper if the definition style changed."
        )
        assert "TRIGGER_THRESHOLD_PCT" not in members, (
            "TRIGGER_THRESHOLD_PCT is in OPTUNA_SEARCH_SPACE_KEYS (autotuner.py:20-24). "
            "It must be removed because it is in DEFAULT_LOCKED_VARS. "
            "RED until the implementer removes it from the frozenset."
        )

    @pytest.mark.parametrize("locked_var", _locked_var_names_from_fixture())
    def test_optuna_search_space_keys_excludes_all_locked_vars(self, locked_var: str):
        """
        Parametrized over every var in DEFAULT_LOCKED_VARS: none may appear in
        OPTUNA_SEARCH_SPACE_KEYS. AC-O4.2.
        """
        src = _autotuner_source()
        tree = _parse_ast(src)
        assignments = _find_module_assignments(tree)

        members = _extract_frozenset_strings(assignments.get("OPTUNA_SEARCH_SPACE_KEYS", ast.Constant(value=None)))
        if members is None:
            pytest.skip("OPTUNA_SEARCH_SPACE_KEYS not parseable as frozenset literal")

        assert locked_var not in members, (
            f"Locked var '{locked_var}' must not appear in OPTUNA_SEARCH_SPACE_KEYS. "
            f"AC-O4.2. RED until all locked vars are excluded from the search space."
        )

    @pytest.mark.parametrize("non_locked_var", _non_locked_var_names_from_fixture())
    def test_optuna_search_space_keys_includes_non_locked_vars(self, non_locked_var: str):
        """
        Counter-test: non-locked vars MUST remain in OPTUNA_SEARCH_SPACE_KEYS.
        The fix must not blanket-remove all vars — only locked ones.
        """
        src = _autotuner_source()
        tree = _parse_ast(src)
        assignments = _find_module_assignments(tree)

        members = _extract_frozenset_strings(assignments.get("OPTUNA_SEARCH_SPACE_KEYS", ast.Constant(value=None)))
        if members is None:
            pytest.skip("OPTUNA_SEARCH_SPACE_KEYS not parseable as frozenset literal")

        assert non_locked_var in members, (
            f"Non-locked var '{non_locked_var}' is unexpectedly absent from "
            f"OPTUNA_SEARCH_SPACE_KEYS. The fix must only exclude locked vars, "
            f"not blanket-remove all keys. Counter-test FAIL."
        )

    def test_objective_closure_does_not_suggest_trigger_threshold_pct(self):
        """
        The objective() closure in autotuner.py must not call
        trial.suggest_float('TRIGGER_THRESHOLD_PCT', ...) or any suggest_* variant.
        RED: currently line ~670 does exactly this.
        """
        src = _autotuner_source()
        # Find all suggest_* calls that name TRIGGER_THRESHOLD_PCT
        pattern = r'trial\.suggest_\w+\s*\(\s*["\']TRIGGER_THRESHOLD_PCT["\']'
        matches = re.findall(pattern, src)
        assert not matches, (
            f"Found {len(matches)} trial.suggest_* call(s) for 'TRIGGER_THRESHOLD_PCT' "
            f"in autotuner.py. This locked var must be excluded from the Optuna suggest "
            f"path entirely. RED until the implementer removes these calls.\n"
            f"Matches: {matches}"
        )

    @pytest.mark.parametrize("locked_var", _locked_var_names_from_fixture())
    def test_objective_closure_does_not_suggest_any_locked_var(self, locked_var: str):
        """
        Parametrized: the objective() closure must not suggest_* ANY locked var. AC-O4.2.
        """
        src = _autotuner_source()
        pattern = rf"trial\.suggest_\w+\s*\(\s*[\"']{re.escape(locked_var)}[\"']"
        matches = re.findall(pattern, src)
        assert not matches, (
            f"Locked var '{locked_var}' is still called via trial.suggest_* in "
            f"autotuner.py ({len(matches)} occurrence(s)). "
            f"All locked vars must be excluded from the Optuna suggest path. "
            f"AC-O4.2. RED until removed."
        )

    @pytest.mark.parametrize("non_locked_var", _non_locked_var_names_from_fixture())
    def test_objective_closure_still_suggests_non_locked_vars(self, non_locked_var: str):
        """
        Counter-test: non-locked vars MUST still be suggested by the objective() closure.
        The fix must not over-prune.
        """
        src = _autotuner_source()
        pattern = rf"trial\.suggest_\w+\s*\(\s*[\"']{re.escape(non_locked_var)}[\"']"
        matches = re.findall(pattern, src)
        assert matches, (
            f"Non-locked var '{non_locked_var}' has NO trial.suggest_* call in autotuner.py. "
            f"The locked-var fix must preserve suggest calls for all non-locked vars. "
            f"Counter-test FAIL — check that the implementer didn't over-prune."
        )


# ---------------------------------------------------------------------------
# Class 3: AI advisor locked-var gate — structural hard filter required
# ---------------------------------------------------------------------------

class TestAIAdvisorRejectsLockedVarSuggestions:
    """
    enforce_suggestion_allowlist (or a new dedicated gate) must structurally
    reject any ConfigSuggestion whose config_key is in DEFAULT_LOCKED_VARS,
    regardless of whether that key is in _SUGGESTIBLE_ALLOWLIST.

    Current bug: _SUGGESTIBLE_ALLOWLIST includes TRIGGER_THRESHOLD_PCT.
    enforce_suggestion_allowlist only checks _SUGGESTIBLE_ALLOWLIST, not locked
    status — so a locked-var suggestion passes through the allowlist gate.
    """

    def test_enforce_suggestion_allowlist_excludes_trigger_threshold_pct(self):
        """
        enforce_suggestion_allowlist must route a TRIGGER_THRESHOLD_PCT suggestion
        to the rejected partition. RED: currently it lands in allowed because
        _SUGGESTIBLE_ALLOWLIST still contains TRIGGER_THRESHOLD_PCT.
        """
        from ai_advisor import ConfigSuggestion, enforce_suggestion_allowlist  # type: ignore[import]

        # Shape derived from fixture — the float value is schema-representative,
        # not a producer-computed output.
        fix = _load_fixture("ai_advisor_locked_suggestion_fixture.json")
        locked_example = fix["suggestions_with_locked_var"][0]

        suggestion = ConfigSuggestion(
            config_key=locked_example["config_key"],
            suggested_value=locked_example["suggested_value_example"],
            current_value=locked_example["current_value_example"],
            risk_direction=locked_example["risk_direction"],
            rationale=locked_example["rationale"],
            confidence=locked_example["confidence"],
            data_sufficiency=locked_example["data_sufficiency"],
        )

        allowed, rejected = enforce_suggestion_allowlist([suggestion])

        assert suggestion not in allowed, (
            "TRIGGER_THRESHOLD_PCT suggestion landed in 'allowed' — "
            "it must be rejected because it is a locked var. "
            "RED: _SUGGESTIBLE_ALLOWLIST still contains TRIGGER_THRESHOLD_PCT. "
            "Fix: remove it from _SUGGESTIBLE_ALLOWLIST, OR add a locked-var "
            "filter gate upstream of enforce_suggestion_allowlist."
        )
        assert suggestion in rejected, (
            "TRIGGER_THRESHOLD_PCT suggestion is neither allowed nor rejected — "
            "every suggestion must land in exactly one partition."
        )

    @pytest.mark.parametrize("locked_var", _locked_var_names_from_fixture())
    def test_enforce_suggestion_allowlist_rejects_all_locked_vars(self, locked_var: str):
        """
        Parametrized: for every var in DEFAULT_LOCKED_VARS, a suggestion with that
        config_key must land in the rejected partition. AC-O4.2 / AC-O4.3.
        """
        from ai_advisor import ConfigSuggestion, enforce_suggestion_allowlist  # type: ignore[import]

        suggestion = ConfigSuggestion(
            config_key=locked_var,
            # Representative value — assertion is on partition membership, not numeric equality
            suggested_value=1.0,
            current_value=2.0,
            risk_direction="tightens",
            rationale="test",
            confidence="medium",
            data_sufficiency="sufficient",
        )
        allowed, rejected = enforce_suggestion_allowlist([suggestion])

        assert suggestion not in allowed, (
            f"Locked var '{locked_var}' suggestion landed in 'allowed'. "
            f"It must be structurally rejected. AC-O4.3."
        )
        assert suggestion in rejected, (
            f"Locked var '{locked_var}' suggestion is neither allowed nor rejected — "
            "partition must be exhaustive."
        )

    @pytest.mark.parametrize("non_locked_var", _non_locked_var_names_from_fixture())
    def test_enforce_suggestion_allowlist_allows_non_locked_vars(self, non_locked_var: str):
        """
        Counter-test: non-locked, suggestible vars must still land in allowed.
        The fix must not blanket-reject everything.
        """
        from ai_advisor import ConfigSuggestion, enforce_suggestion_allowlist  # type: ignore[import]

        suggestion = ConfigSuggestion(
            config_key=non_locked_var,
            suggested_value=1.0,
            current_value=2.0,
            risk_direction="tightens",
            rationale="test",
            confidence="medium",
            data_sufficiency="sufficient",
        )
        allowed, rejected = enforce_suggestion_allowlist([suggestion])

        assert suggestion in allowed, (
            f"Non-locked var '{non_locked_var}' suggestion landed in 'rejected'. "
            f"The locked-var fix must not over-reject. Counter-test FAIL."
        )
        assert suggestion not in rejected, (
            f"Non-locked var '{non_locked_var}' appears in both partitions — impossible invariant."
        )

    def test_suggestible_allowlist_excludes_trigger_threshold_pct(self):
        """
        _SUGGESTIBLE_ALLOWLIST in ai_advisor.py must not contain TRIGGER_THRESHOLD_PCT.
        RED: currently it does because it is derived from _OPTUNA_SEARCH_SPACE_KEYS
        which still includes TRIGGER_THRESHOLD_PCT. Both must be fixed together.
        """
        src = _ai_advisor_source()
        tree = _parse_ast(src)
        assignments = _find_module_assignments(tree)

        # _SUGGESTIBLE_ALLOWLIST is built from _OPTUNA_SEARCH_SPACE_KEYS | {_UNTUNED_SUGGESTIBLE_KEY}
        # We check the _OPTUNA_SEARCH_SPACE_KEYS copy in ai_advisor.py (which is a frozenset literal)
        osk_node = assignments.get("_OPTUNA_SEARCH_SPACE_KEYS")
        if osk_node is not None:
            members = _extract_frozenset_strings(osk_node)
            if members is not None:
                assert "TRIGGER_THRESHOLD_PCT" not in members, (
                    "ai_advisor._OPTUNA_SEARCH_SPACE_KEYS contains TRIGGER_THRESHOLD_PCT. "
                    "Both copies (autotuner.OPTUNA_SEARCH_SPACE_KEYS and "
                    "ai_advisor._OPTUNA_SEARCH_SPACE_KEYS) must exclude locked vars. "
                    "RED until both are updated."
                )

    def test_locked_var_prompt_instruction_is_backed_by_structural_gate(self):
        """
        The prompt instruction 'Never emit a suggested value for a locked param'
        is a soft constraint — model compliance is not guaranteed.
        A STRUCTURAL gate (not just a prompt) must exist to enforce locked-var rejection.

        This test inspects ai_advisor.py to confirm that enforce_suggestion_allowlist
        (or a named gate function) references locked vars explicitly — not just the
        allowlist check.

        RED until the structural gate is added.
        """
        src = _ai_advisor_source()

        # The gate must reference locked vars in some structural way:
        # either by importing DEFAULT_LOCKED_VARS and using it in a filter,
        # or by adding a dedicated enforce_locked_vars_filter function.
        has_locked_var_import = bool(re.search(r"DEFAULT_LOCKED_VARS", src))
        has_locked_var_filter_fn = bool(
            re.search(r"def\s+enforce_locked_vars_filter|def\s+filter_locked_suggestions", src)
        )
        has_locked_var_check_in_allowlist = bool(
            re.search(r"locked_vars.*suggest|suggest.*locked_vars|DEFAULT_LOCKED_VARS.*suggest", src, re.DOTALL)
        )

        structural_gate_present = (
            has_locked_var_import
            or has_locked_var_filter_fn
            or has_locked_var_check_in_allowlist
        )

        assert structural_gate_present, (
            "ai_advisor.py has no structural gate rejecting locked vars. "
            "The current prompt instruction ('Never emit a suggested value for a locked param') "
            "is model-dependent and not a hard guarantee. "
            "Required: import DEFAULT_LOCKED_VARS and filter it in enforce_suggestion_allowlist, "
            "OR add a dedicated enforce_locked_vars_filter function called before allowlist. "
            "RED until a structural gate exists."
        )


# ---------------------------------------------------------------------------
# Class 4: Determinism — same locked vars + same fixture → same filter result
# ---------------------------------------------------------------------------

class TestLockedVarFilterDeterminism:
    """
    Given the same DEFAULT_LOCKED_VARS and the same suggestions fixture,
    enforce_suggestion_allowlist must produce the same partition on every call.
    """

    def test_locked_var_filter_is_deterministic(self):
        """
        Two calls with identical inputs must produce identical partition results.
        """
        from ai_advisor import ConfigSuggestion, enforce_suggestion_allowlist  # type: ignore[import]

        fix = _load_fixture("ai_advisor_locked_suggestion_fixture.json")
        locked_example = fix["suggestions_with_locked_var"][0]
        non_locked_example = fix["suggestions_without_locked_var"][0]

        suggestions = [
            ConfigSuggestion(
                config_key=locked_example["config_key"],
                suggested_value=locked_example["suggested_value_example"],
                current_value=locked_example["current_value_example"],
                risk_direction=locked_example["risk_direction"],
                rationale=locked_example["rationale"],
                confidence=locked_example["confidence"],
                data_sufficiency=locked_example["data_sufficiency"],
            ),
            ConfigSuggestion(
                config_key=non_locked_example["config_key"],
                suggested_value=non_locked_example["suggested_value_example"],
                current_value=non_locked_example["current_value_example"],
                risk_direction=non_locked_example["risk_direction"],
                rationale=non_locked_example["rationale"],
                confidence=non_locked_example["confidence"],
                data_sufficiency=non_locked_example["data_sufficiency"],
            ),
        ]

        allowed_1, rejected_1 = enforce_suggestion_allowlist(list(suggestions))
        allowed_2, rejected_2 = enforce_suggestion_allowlist(list(suggestions))

        assert [s.config_key for s in allowed_1] == [s.config_key for s in allowed_2], (
            "enforce_suggestion_allowlist is non-deterministic: allowed partition differs "
            "across two identical calls. Must be a pure function."
        )
        assert [s.config_key for s in rejected_1] == [s.config_key for s in rejected_2], (
            "enforce_suggestion_allowlist is non-deterministic: rejected partition differs "
            "across two identical calls. Must be a pure function."
        )

    def test_partition_is_exhaustive_and_disjoint(self):
        """
        Every input suggestion must appear in exactly one partition (no drop, no dup).
        """
        from ai_advisor import ConfigSuggestion, enforce_suggestion_allowlist  # type: ignore[import]

        fix = _load_fixture("ai_advisor_locked_suggestion_fixture.json")
        locked_example = fix["suggestions_with_locked_var"][0]
        non_locked_example = fix["suggestions_without_locked_var"][0]

        s_locked = ConfigSuggestion(
            config_key=locked_example["config_key"],
            suggested_value=locked_example["suggested_value_example"],
            current_value=locked_example["current_value_example"],
            risk_direction=locked_example["risk_direction"],
            rationale=locked_example["rationale"],
            confidence=locked_example["confidence"],
            data_sufficiency=locked_example["data_sufficiency"],
        )
        s_non_locked = ConfigSuggestion(
            config_key=non_locked_example["config_key"],
            suggested_value=non_locked_example["suggested_value_example"],
            current_value=non_locked_example["current_value_example"],
            risk_direction=non_locked_example["risk_direction"],
            rationale=non_locked_example["rationale"],
            confidence=non_locked_example["confidence"],
            data_sufficiency=non_locked_example["data_sufficiency"],
        )

        allowed, rejected = enforce_suggestion_allowlist([s_locked, s_non_locked])

        all_keys_in = {s_locked.config_key, s_non_locked.config_key}
        all_keys_out = {s.config_key for s in allowed} | {s.config_key for s in rejected}

        assert all_keys_in == all_keys_out, (
            f"Partition dropped or duplicated suggestions. "
            f"Input keys: {all_keys_in}. Output keys: {all_keys_out}. "
            "Every input must appear in exactly one partition."
        )

        allowed_keys = {s.config_key for s in allowed}
        rejected_keys = {s.config_key for s in rejected}
        assert allowed_keys.isdisjoint(rejected_keys), (
            f"Partitions are not disjoint: {allowed_keys & rejected_keys} appear in both."
        )


# ---------------------------------------------------------------------------
# Class 5: app.py accept route — locked-var write path must be gated
# ---------------------------------------------------------------------------

class TestAppAcceptRouteRejectsLockedVars:
    """
    The /ai-advisor/accept route in app.py reads locked_vars from the DB
    (line 822) but currently has NO check that prevents writing a locked var
    (lines 834-835). The gate must exist at the route level — defense-in-depth
    on top of enforce_suggestion_allowlist.

    These are source-inspection tests (AST + regex) — no live Flask client
    needed, no network calls.
    """

    APP_SRC = pathlib.Path(__file__).parent.parent.parent / "app.py"

    def _app_source(self) -> str:
        return self.APP_SRC.read_text(encoding="utf-8")

    def test_accept_route_checks_locked_vars_before_write(self):
        """
        The ai_advisor_accept() function in app.py must check whether
        suggestion_obj.config_key is in locked_vars BEFORE the database write.

        Current bug: locked_vars is read at line 822 but never consulted;
        the config write at line 834 fires unconditionally for any key that
        passes enforce_suggestion_allowlist (which currently allows TRIGGER_THRESHOLD_PCT).

        RED until a guard like:
            if suggestion_obj.config_key in locked_vars:
                return jsonify({"status": "rejected", "error": "locked var"}), 200
        (or equivalent) is added between the locked_vars read and the write.
        """
        src = self._app_source()

        # Find the ai_advisor_accept function body
        fn_match = re.search(
            r"def ai_advisor_accept\(\)(.*?)(?=\n@app\.route|\nclass |\Z)",
            src,
            re.DOTALL,
        )
        assert fn_match, "ai_advisor_accept function not found in app.py"

        fn_body = fn_match.group(1)

        # The function must contain a locked-var membership check before the write
        has_locked_check = bool(
            re.search(
                r"config_key\s+in\s+locked_vars|locked_vars.*config_key",
                fn_body,
            )
        )
        assert has_locked_check, (
            "app.py:ai_advisor_accept() reads locked_vars but never checks "
            "suggestion_obj.config_key against it before writing to the DB. "
            "A locked var that passes enforce_suggestion_allowlist (currently "
            "TRIGGER_THRESHOLD_PCT does) can be written to live config with no gate. "
            "Add: `if suggestion_obj.config_key in locked_vars: return rejected` "
            "between the locked_vars read and the database write. "
            "RED until the guard is present."
        )

    def test_accept_route_locked_var_guard_precedes_write(self):
        """
        The locked-var guard must appear BEFORE `patched_params[suggestion_obj.config_key]`
        in the function body — ordering matters for defense-in-depth.
        """
        src = self._app_source()

        fn_match = re.search(
            r"def ai_advisor_accept\(\)(.*?)(?=\n@app\.route|\nclass |\Z)",
            src,
            re.DOTALL,
        )
        assert fn_match, "ai_advisor_accept function not found in app.py"

        fn_body = fn_match.group(1)

        guard_match = re.search(
            r"config_key\s+in\s+locked_vars|locked_vars.*config_key",
            fn_body,
        )
        write_match = re.search(
            r"patched_params\[suggestion_obj\.config_key\]",
            fn_body,
        )

        if guard_match is None:
            pytest.fail(
                "No locked-var guard found in ai_advisor_accept() — "
                "see test_accept_route_checks_locked_vars_before_write."
            )
        if write_match is None:
            pytest.skip("patched_params write not found — route may have been restructured")

        assert guard_match.start() < write_match.start(), (
            "Locked-var guard appears AFTER the config write in ai_advisor_accept(). "
            "The guard must precede the write to prevent locked-var mutation."
        )
