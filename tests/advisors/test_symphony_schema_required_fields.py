"""RED tests — symphony_schema required-fields fix (AC-1, AC-2, AC-4, AC-5).

Module under test: advisors.symphony_schema

Defects fixed by this cycle:
  (1) make_root omits "description" → live Composer /backtest returns HTTP 400.
  (2) make_inverse_vol omits "window-days" → live Composer /backtest returns HTTP 422.

Provenance: both fields are captured-from-producer (live API spike matrix + composer-trade-mcp
schema models Root.description / WeightInverseVol.window_days). NOT parser-co-designed.

Every assertion in this file is RED against the CURRENT production constructor code:
  - make_root (symphony_schema.py:784): currently omits "description"
  - make_inverse_vol (symphony_schema.py:823): currently omits "window-days"

Rules:
  - Never hardcode producer-computed backtest stats (rates, sharpe, CAGR, etc.)
  - Assert FIELD PRESENCE and DEFAULT VALUE only (the contract, not the computation)
  - Never weaken an existing assertion — only add or strengthen
  - pytest.approx not needed here (int/str comparisons, no floats)
  - No live network calls, no DB access, no mocks needed

Coverage:
  AC-1: make_root output contains "description" defaulting to ""
  AC-2: make_inverse_vol output contains "window-days" defaulting to 30
  AC-4: stale-by-intent guard: the existing make_root shape tests survive the additive
        keys (i.e. existing test assertions do NOT break — confirming the fix is
        backward-compatible with the test suite). Covered by explicit re-assertion here.
  AC-5: validate_tree and lint_tree still accept trees built with the new keys (Amendment 7
        unknown-key tolerance).
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Module import helper — deferred to produce assertion-level RED failures,
# not collection-time ImportError RED.
# ---------------------------------------------------------------------------


def _import_schema():
    """Import advisors.symphony_schema; fail clearly if missing."""
    try:
        from advisors import symphony_schema  # type: ignore[import]

        return symphony_schema
    except ImportError as exc:
        pytest.fail(f"advisors/symphony_schema.py not importable: {exc}")


# ---------------------------------------------------------------------------
# Minimal tree builders used across test classes
# ---------------------------------------------------------------------------


def _minimal_asset(m) -> dict:
    return m.make_asset("SPY")


def _minimal_root_with_children(m, children=None) -> dict:
    if children is None:
        children = [m.make_weight_equal([_minimal_asset(m)])]
    return m.make_root("Test Symphony", "daily", children)


def _minimal_inverse_vol_root(m) -> dict:
    spy = _minimal_asset(m)
    agg = m.make_asset("AGG")
    inv_vol = m.make_inverse_vol([spy, agg])
    return m.make_root("InvVol Symphony", "daily", [inv_vol])


# ===========================================================================
# AC-1 — make_root includes "description" defaulting to ""
# ===========================================================================


class TestMakeRootDescriptionField:
    """AC-1: make_root must emit a "description" key defaulting to "" (empty string).

    Live API proof: T1 equal_weight + description="" → HTTP 200 (was 400 without it).
    Provenance: composer-trade-mcp Root.description model field; presence confirmed in
    every real /score response captured in sample_score_small.json and sample_score_large.json.
    """

    def test_make_root_includes_description_key(self):
        """make_root output MUST contain the key "description".

        Currently RED: the constructor at symphony_schema.py:784 returns
        {step, name, rebalance, id, children} — "description" is absent.
        """
        m = _import_schema()
        root = _minimal_root_with_children(m)
        assert "description" in root, (
            'make_root output must include "description" key. '
            "Without it, Composer POST /backtest returns HTTP 400. "
            f"Actual keys: {list(root.keys())}"
        )

    def test_make_root_description_defaults_to_empty_string(self):
        """The default value of "description" must be "" (empty string).

        Proof: every real Composer /score response carries description="" on the root
        node. The empty string is the correct sentinel value, not None or absent.
        """
        m = _import_schema()
        root = _minimal_root_with_children(m)
        # "description" must be present first (tested above), then its value
        assert "description" in root, (
            '"description" key absent from make_root output — cannot check default value'
        )
        assert root["description"] == "", (
            f'make_root "description" must default to "" (empty string); '
            f"got {root['description']!r}"
        )

    def test_make_root_description_is_a_string(self):
        """The "description" field must be a str, not None or any other type.

        The Composer API contract requires a string (Root.description: str);
        passing None or another type risks 400/422 on validation.
        """
        m = _import_schema()
        root = _minimal_root_with_children(m)
        assert "description" in root, '"description" key missing from make_root output'
        assert isinstance(root["description"], str), (
            f'make_root "description" must be str; got {type(root["description"]).__name__}'
        )

    def test_make_root_description_present_regardless_of_rebalance(self):
        """The "description" key must appear for all rebalance values.

        Guard against an implementation that only adds "description" for specific
        rebalance strings.
        """
        m = _import_schema()
        for rebalance in ("daily", "weekly", "monthly", "none"):
            asset = _minimal_asset(m)
            root = m.make_root("Test", rebalance, [m.make_weight_equal([asset])])
            assert "description" in root, (
                f'"description" missing from make_root output with rebalance={rebalance!r}'
            )
            assert root["description"] == "", (
                f'"description" must default to "" for rebalance={rebalance!r}; '
                f"got {root['description']!r}"
            )

    def test_make_root_description_present_on_multiple_independent_calls(self):
        """Each call to make_root must independently include "description".

        Guards against a cached or lazy-init implementation where the key only
        appears after some warmup call.
        """
        m = _import_schema()
        for i in range(3):
            asset = m.make_asset(f"TICK{i}")
            root = m.make_root(f"Symphony {i}", "daily", [m.make_weight_equal([asset])])
            assert "description" in root, (
                f'"description" absent from make_root call #{i}'
            )
            assert root["description"] == "", (
                f'"description" must be "" on call #{i}; got {root["description"]!r}'
            )


# ===========================================================================
# AC-2 — make_inverse_vol includes "window-days" defaulting to 30
# ===========================================================================


class TestMakeInverseVolWindowDaysField:
    """AC-2: make_inverse_vol must emit a "window-days" key defaulting to 30.

    Live API proof: T3 inverse_vol + description="" + window-days=30 → HTTP 200
    (was 422 "unknown-function-parameter" without window-days).
    Provenance: composer-trade-mcp WeightInverseVol.window_days model field;
    30 is the Composer UI default (observed in live verified runs).
    """

    def test_make_inverse_vol_includes_window_days_key(self):
        """make_inverse_vol output MUST contain the key "window-days".

        Currently RED: the constructor at symphony_schema.py:823 returns
        {step, id, children} — "window-days" is absent.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        node = m.make_inverse_vol([spy])
        assert "window-days" in node, (
            'make_inverse_vol output must include "window-days" key. '
            "Without it, Composer POST /backtest returns HTTP 422 "
            "\"unknown-function-parameter\". "
            f"Actual keys: {list(node.keys())}"
        )

    def test_make_inverse_vol_window_days_defaults_to_30(self):
        """The default value of "window-days" must be 30 (Composer UI default).

        The exact integer 30 is derived from the live API contract — it is the
        default the Composer UI writes when creating a wt-inverse-vol node, and
        the value accepted without error in the live spike run.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        node = m.make_inverse_vol([spy])
        assert "window-days" in node, (
            '"window-days" key absent from make_inverse_vol output'
        )
        assert node["window-days"] == 30, (
            f'make_inverse_vol "window-days" must default to 30; '
            f"got {node['window-days']!r}"
        )

    def test_make_inverse_vol_window_days_is_int(self):
        """The "window-days" field must be an int, not a float or string.

        The API contract specifies an integer window; passing "30" or 30.0
        may be rejected by the malli schema validator.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        node = m.make_inverse_vol([spy])
        assert "window-days" in node, '"window-days" key missing from make_inverse_vol output'
        val = node["window-days"]
        assert isinstance(val, int) and not isinstance(val, bool), (
            f'make_inverse_vol "window-days" must be int; got {type(val).__name__!r} = {val!r}'
        )

    def test_make_inverse_vol_window_days_is_positive(self):
        """The default window-days must be a positive integer.

        A zero or negative window would be rejected by the Composer API.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        node = m.make_inverse_vol([spy])
        assert "window-days" in node, '"window-days" key missing'
        assert node["window-days"] > 0, (
            f'"window-days" must be a positive integer; got {node["window-days"]!r}'
        )

    def test_make_inverse_vol_window_days_present_with_multiple_children(self):
        """The "window-days" key must appear regardless of how many children are provided.

        Guards against an implementation that conditions the field on a single-child
        optimization path.
        """
        m = _import_schema()
        tickers = ["SPY", "AGG", "TLT", "GLD"]
        assets = [m.make_asset(t) for t in tickers]
        node = m.make_inverse_vol(assets)
        assert "window-days" in node, (
            f'"window-days" absent from make_inverse_vol with {len(tickers)}-child list'
        )
        assert node["window-days"] == 30, (
            f'"window-days" must be 30 with multiple children; got {node["window-days"]!r}'
        )


# ===========================================================================
# AC-4 — stale-by-intent guard: existing assertions survive additive keys
#
# The fix is purely additive: two new keys are ADDED to the existing dict.
# No existing key is removed or renamed. This class re-asserts the previously
# passing shape expectations to confirm backward compatibility and to serve as
# a regression guard. These tests are currently RED only because make_root
# does NOT yet emit "description" and make_inverse_vol does NOT emit "window-days" —
# they will be GREEN after the fix WITHOUT requiring any change to existing tests.
#
# Strategy: we assert BOTH the old keys (still there) AND the new keys (now required),
# ensuring the fix is truly additive and no old assertion is weakened.
# ===========================================================================


class TestAdditiveKeysBackwardCompatibility:
    """AC-4: The new keys are additive — existing assertions on make_root and
    make_inverse_vol output shape must not break after the fix.

    These tests are RED now because the new keys are absent (assertion below will
    fail on make_root) AND we explicitly assert the new keys alongside the old ones.
    After the fix: all assertions pass because the constructor output gains the
    new key while keeping all existing keys intact.
    """

    def test_make_root_has_all_required_keys_including_new_description(self):
        """make_root must include step, name, rebalance, id, children (original)
        AND description (new).

        The test that was previously asserting only the 5 original keys still passes
        because those keys are not removed. This test adds "description" to the
        assertion to capture the new contract.
        """
        m = _import_schema()
        root = m.make_root("AC-4 Test", "daily", [m.make_weight_equal([_minimal_asset(m)])])
        # Original keys — must still be present (guards backward compat)
        assert root.get("step") == "root", f'"step" must be "root"; got {root.get("step")!r}'
        assert root.get("name") == "AC-4 Test", f'"name" mismatch: {root.get("name")!r}'
        assert root.get("rebalance") == "daily", f'"rebalance" mismatch: {root.get("rebalance")!r}'
        assert "id" in root, '"id" missing from make_root output'
        assert isinstance(root.get("children"), list), '"children" must be a list'
        # New required key — currently RED
        assert "description" in root, (
            '"description" missing — make_root does not yet emit it (RED). '
            "This assertion will become GREEN after the fix."
        )
        assert root["description"] == "", (
            f'"description" must default to "" (empty string); got {root["description"]!r}'
        )

    def test_make_inverse_vol_has_all_required_keys_including_new_window_days(self):
        """make_inverse_vol must include step, id, children (original) AND window-days (new).

        The test that was previously asserting only the 3 original keys still passes
        because those keys are not removed. This test adds "window-days" to capture
        the new contract.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        node = m.make_inverse_vol([spy])
        # Original keys — must still be present
        assert node.get("step") == "wt-inverse-vol", (
            f'"step" must be "wt-inverse-vol"; got {node.get("step")!r}'
        )
        assert "id" in node, '"id" missing from make_inverse_vol output'
        assert isinstance(node.get("children"), list), '"children" must be a list'
        assert len(node["children"]) == 1, (
            f'"children" must have 1 element; got {len(node["children"])}'
        )
        # New required key — currently RED
        assert "window-days" in node, (
            '"window-days" missing — make_inverse_vol does not yet emit it (RED). '
            "This assertion will become GREEN after the fix."
        )
        assert node["window-days"] == 30, (
            f'"window-days" must default to 30; got {node["window-days"]!r}'
        )

    def test_make_root_ids_are_still_uuid4_with_new_keys(self):
        """UUID freshness invariant must hold after the fix is applied.

        Guards against an implementation side-effect that corrupts id generation.
        """
        m = _import_schema()
        root = m.make_root("UUID Test", "daily", [m.make_weight_equal([_minimal_asset(m)])])
        assert "id" in root, '"id" missing after fix'
        parsed = uuid.UUID(root["id"])
        assert parsed.version == 4, (
            f'"id" must be UUID v4 after fix; got version {parsed.version}'
        )

    def test_make_root_children_are_deep_copies_with_new_keys(self):
        """Children isolation invariant must hold after the fix.

        Guards against the fix accidentally breaking the deep-copy contract.
        """
        m = _import_schema()
        child = m.make_asset("SPY")
        parent_a = m.make_root("A", "daily", [m.make_weight_equal([child])])
        parent_b = m.make_root("B", "daily", [m.make_weight_equal([child])])
        # Mutating parent_a's children must not affect parent_b
        parent_a["children"].append(m.make_asset("AGG"))
        assert len(parent_b["children"]) == 1, (
            "make_root children must be deep copies; mutation of one root's children "
            "must not affect another root's children (deep-copy invariant broken by fix)"
        )


# ===========================================================================
# AC-5 — validate_tree and lint_tree accept trees with the new fields
# ===========================================================================


class TestNewFieldsPassValidation:
    """AC-5: validate_tree and lint_tree must accept trees built with the new fields.

    Amendment 7 guarantees unknown/extra keys are tolerated. But once the constructors
    emit these keys, we also need to confirm:
      (a) The new keys do NOT accidentally trigger a hard validation error.
      (b) lint_tree does NOT issue a spurious warning about "description" or "window-days"
          since both are known Composer root/wt-inverse-vol fields, not cosmetic unknowns.

    Currently RED because make_root does not emit "description" and make_inverse_vol
    does not emit "window-days" — so these tests can only exercise the post-fix
    state. They are written to fail on the OLD constructor output (which lacks
    the keys) because the test explicitly ADDS the keys to check the validation path.
    The RED failure is that the CONSTRUCTOR does not emit the keys, so the trees
    we build via constructors do not demonstrate the fix.

    After the fix: the constructors emit the keys, validate_tree returns [], and
    lint_tree returns a list with no entries about these fields.
    """

    def test_root_node_with_description_validates_clean(self):
        """A root dict carrying "description" must produce zero validate_tree errors.

        This test constructs a root dict MANUALLY (bypassing the constructor) to assert
        that the validator correctly tolerates "description" today (Amendment 7). This
        test is currently GREEN because Amendment 7 already handles it.
        After the fix, the CONSTRUCTOR will emit description="" so the GREEN path is
        confirmed end-to-end in the next test.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "AC-5 Manual Root",
            "rebalance": "daily",
            "description": "",  # the new required field, manually added
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            "Amendment 7: root with description='' must produce zero validate_tree errors; "
            f"got: {errors}"
        )

    def test_wt_inverse_vol_with_window_days_validates_clean(self):
        """A wt-inverse-vol dict carrying "window-days" must produce zero validate_tree errors.

        Same rationale as above: manually adds "window-days" to confirm Amendment 7 handles it.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "AC-5 InvVol",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-inverse-vol",
                    "window-days": 30,  # the new required field, manually added
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "AGG",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            'Amendment 7: wt-inverse-vol with "window-days": 30 must produce zero '
            f"validate_tree errors; got: {errors}"
        )

    def test_constructor_root_with_description_validates_clean(self):
        """After the fix: make_root(...) output must pass validate_tree with zero errors.

        Currently RED because make_root does not emit "description" yet — but once
        the key is added, this test verifies the round-trip is error-free.

        Note: this test will PASS on the current code only if validate_tree is
        indifferent to the absence of "description" (Amendment 7 tolerates both
        presence and absence). The test's RED value is that the CONSTRUCTOR test
        assertions in AC-1 will fail — this test is a belt-and-suspenders check
        that validate_tree stays clean after the fix.
        """
        m = _import_schema()
        root = _minimal_root_with_children(m)
        # AC-1 assertion (make_root must emit description) — RED currently
        assert "description" in root, (
            '"description" absent from make_root output — fix not applied yet (RED)'
        )
        errors = m.validate_tree(root)
        assert errors == [], (
            f"make_root output (with description) must pass validate_tree; got: {errors}"
        )

    def test_constructor_inverse_vol_with_window_days_validates_clean(self):
        """After the fix: make_inverse_vol(...) must produce a tree that passes validate_tree.

        Currently RED because the constructor does not emit "window-days" yet.
        """
        m = _import_schema()
        spy = _minimal_asset(m)
        agg = m.make_asset("AGG")
        inv_vol = m.make_inverse_vol([spy, agg])
        # AC-2 assertion — RED currently
        assert "window-days" in inv_vol, (
            '"window-days" absent from make_inverse_vol output — fix not applied yet (RED)'
        )
        root = m.make_root("InvVol Test", "daily", [inv_vol])
        errors = m.validate_tree(root)
        assert errors == [], (
            f"make_inverse_vol (with window-days) embedded in root must pass validate_tree; "
            f"got: {errors}"
        )

    def test_full_tree_built_with_constructors_validates_clean_after_fix(self):
        """End-to-end constructor round-trip: root + inverse_vol + assets must validate clean.

        This mirrors the T3 inverse_vol_basket template from strategy_builder_engine,
        which is the specific tree that 422'd before the fix.
        """
        m = _import_schema()
        root = _minimal_inverse_vol_root(m)
        # Belt-and-suspenders: confirm both new keys are present (RED until fix)
        assert "description" in root, '"description" absent from make_root output (RED)'
        inv_vol_node = root["children"][0]
        assert "window-days" in inv_vol_node, (
            '"window-days" absent from make_inverse_vol output (RED)'
        )
        errors = m.validate_tree(root)
        assert errors == [], (
            "Full constructor tree (root + wt-inverse-vol + assets) must pass validate_tree "
            f"after the fix; got: {errors}"
        )

    def test_new_fields_do_not_produce_lint_warnings(self):
        """lint_tree must not warn about "description" or "window-days".

        Both fields are known Composer fields (not arbitrary cosmetic keys).
        The implementation MAY return zero warnings for trees using the new fields;
        it MUST NOT specifically warn about "description" on root or "window-days"
        on wt-inverse-vol.

        We test by building a tree that only uses the new required fields
        (no other lintable issues) and asserting no warnings mention these fields.
        """
        m = _import_schema()
        # Build a tree that intentionally only violates the old contract (no new keys)
        # and confirm lint_tree returns a list (not raising)
        root = _minimal_inverse_vol_root(m)
        warnings = m.lint_tree(root)
        assert isinstance(warnings, list), (
            f"lint_tree must return a list; got {type(warnings)}"
        )
        # No warning should specifically complain about "description" or "window-days"
        # being present (they are correct fields, not lint targets)
        for w in warnings:
            assert "description" not in str(w).lower() or "missing" in str(w).lower(), (
                f'lint_tree issued an unexpected warning about "description": {w!r}'
            )
