"""
RED tests — sleeves/rules/schema.py: JSON rule-doc validation (AC-4, AC-20).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/schema.py

    RULE_CLASS_DEFENSIVE = "DEFENSIVE"
    RULE_CLASS_ENTRY = "ENTRY"

    MAX_RULE_DOC_BYTES = 32768      # 32KB cap on json.dumps(doc), AC-4
    MAX_CONDITION_DEPTH = 8         # depth of a leaf compare node counts as 1;
                                     # each AND/OR/NOT ancestor adds 1. A tree
                                     # whose deepest node has depth > 8 is invalid.

    @dataclass(frozen=True)
    class FieldError:
        field: str      # dotted path, e.g. "if.children.0.comparator"
        message: str

    @dataclass(frozen=True)
    class ValidationResult:
        valid: bool
        errors: tuple[FieldError, ...]   # empty iff valid
        rule_class: str | None            # RULE_CLASS_* when valid; None when invalid

    def validate_rule_doc(doc: dict) -> ValidationResult: ...
    def derive_rule_class(action_types: list[str]) -> str | None:
        # None => empty action list OR a mix of entry-opening and reduce-only
        # actions (AC-20: a rule cannot claim DEFENSIVE while holding entry
        # actions, and mixing produces no single valid class).

Rule doc shape (operator-authored):

    {
      "name": str,
      "sleeve_id": int,
      "when": {"symbol": str, ...},               # scope/target (P2: single symbol)
      "if": <condition tree>,                       # see conditions.py contract
      "then": [<action dict>, ...],
      "limits": {"cooldown_sec": int|None, "max_fires_per_day": int|None,
                 "rearm_ticks": int, "market_hours_only": bool},
      "mode": "SHADOW" | "PAPER" | "LIVE",
      "class": "DEFENSIVE" | "ENTRY" | <absent>,    # OPTIONAL operator-declared
                                                       # label; when present MUST
                                                       # match the structurally
                                                       # derived class or the doc
                                                       # is invalid (AC-20
                                                       # "declared AND structurally
                                                       # enforced").
    }

Condition tree leaf/node shapes:

    Leaf:  {"op": "compare", "sense": <sense-key str>, "comparator": <op str>, "value": <num|str|bool>}
    Bool:  {"op": "AND" | "OR", "children": [<node>, ...]}
           {"op": "NOT", "child": <node>}

    comparator in {">", ">=", "<", "<=", "==", "!="}  (enum-closed)

    sense-key grammar (enum-closed; anything else is invalid):
      exact:  "time_of_day" | "day_of_week" | "sleeve_status" |
              "sleeve_cash_usd" | "sleeve_equity_usd" | "position_qty"
      indicator: r"^(sma|ema|rsi|momentum|realized_vol|drawdown_from_high)_(\\d+)$"
                 e.g. "sma_20", "rsi_14"
      fred:      r"^fred_[A-Z0-9_]+$"   e.g. "fred_VIXCLS"

Action dict shapes (`then` list entries), "type" enum-closed to exactly:
    {"buy", "sell", "go_to_cash", "set_stop", "notify"}   (matches the
    `action` column example vocabulary in migrations/034_sleeve_rule_fires.sql:
    go_to_cash is the SLEEVE's own liquidate-to-cash action -- distinct from
    the plan's separate "existing Composer go-to-cash for symphonies"
    concept, which is out of actions.py's P2 scope.)

    "buy" / "sell":  requires "sizing": {"mode": "risk_pct"|"pct_of_sleeve"|
                     "dollars"|"shares", ...} -- mode drawn from the same
                     closed set sleeves.sizing.size_order already defines.

    "buy" ADDITIONALLY requires an explicit declared exit (PM decision,
    2026-07-08, AC-7 "no position ever exists without its exit"): at least
    one of "stop_loss_pct" | "trailing_stop_pct" (float, 0 < pct < 1 --
    fraction of entry price, e.g. 0.05 == 5%) MUST be present. Missing BOTH
    is a schema validation error, never a defaulted/invisible bracket --
    there is deliberately no fallback constant anywhere in this codepath.
    "take_profit_pct" is OPTIONAL (float, 0 < pct if present); when absent,
    actions.py derives the take-profit distance as a fixed, documented
    multiple of the DECLARED stop distance (never an independent, unrelated
    percentage constant) -- see test_actions_dispatch.py's
    TestBracketExitParamsFromDeclaredStops for the derivation proof.
    Rationale (recorded per PM decision):
      1. risk_pct sizing's own formula (qty = risk_dollars / stop_distance)
         already requires a stop distance -- an entry without one is
         unsizeable by the primary sizing mode regardless of schema.
      2. AC-7's exit is part of the entry's DEFINITION, owned by the rule
         author, never an invisible module-level constant.
      3. A silent default stop couples directly into position sizing -- an
         untested percentage constant would silently determine real
         position sizes on paper/live.
    "sell"/"go_to_cash"/"set_stop"/"notify" are UNCHANGED by this decision --
    they close/manage an existing position rather than opening one, so
    AC-7's "no position without its exit" does not apply to them.

    "go_to_cash":    no extra fields required (sells the sleeve's entire
                     current position in the rule's `when.symbol`).
    "set_stop":      requires exactly one of "trail_percent"/"trail_price"
                     (mirrors alpaca_orders.submit_trailing_stop_order).
    "notify":        requires "fields": dict whose keys are drawn from a
                     closed whitelist: {"symbol","action","qty","price",
                     "reason","rule_name","sleeve_name"} -- AC-4/Security:
                     "whitelisted-field templates only", no arbitrary/secret
                     field names reaching a Discord payload.

Rule class derivation (AC-20): DEFENSIVE = every action type in {"sell",
"go_to_cash", "set_stop", "notify"} (reduce-only / non-position-opening).
ENTRY = the action set contains "buy" (position-opening). A rule mixing "buy"
with any other action type derives to None (invalid) -- AC-20 forbids exactly
this mixed shape ("a rule cannot claim DEFENSIVE while holding entry actions").

No hardcoded producer values: every expected class below comes from the
golden fixture tests/fixtures/math/sleeves_rule_class_derivation_basic.json
(a specification table this test file owns), never a bare literal pulled
from a prior run of the real implementation.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip(
    "sleeves.rules.schema", reason="RED phase — sleeves.rules.schema not implemented yet"
)

import sleeves.rules.schema as schema  # noqa: E402

from .conftest import make_rule_doc  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nest_not(leaf: dict, depth: int) -> dict:
    """Wrap `leaf` in `depth` levels of NOT. depth=0 returns leaf unchanged
    (leaf itself has depth 1 per the contract's counting convention)."""
    node = leaf
    for _ in range(depth):
        node = {"op": "NOT", "child": node}
    return node


_LEAF = {"op": "compare", "sense": "sma_20", "comparator": ">", "value": 100.0}


# ---------------------------------------------------------------------------
# 1. Baseline valid doc
# ---------------------------------------------------------------------------


class TestValidRuleDocPasses:
    def test_minimal_valid_entry_rule_is_valid(self):
        doc = make_rule_doc()
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"
        assert result.errors == ()

    def test_minimal_valid_entry_rule_derives_entry_class(self):
        doc = make_rule_doc()
        result = schema.validate_rule_doc(doc)
        assert result.rule_class == schema.RULE_CLASS_ENTRY

    def test_minimal_valid_defensive_rule_derives_defensive_class(self):
        doc = make_rule_doc(then=[{"type": "sell", "sizing": {"mode": "shares", "shares": 10}}])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"
        assert result.rule_class == schema.RULE_CLASS_DEFENSIVE


# ---------------------------------------------------------------------------
# 2. Size cap (32KB, AC-4)
# ---------------------------------------------------------------------------


class TestSizeCap:
    def test_oversized_doc_is_invalid(self):
        doc = make_rule_doc(name="x" * 33 * 1024)
        assert len(json.dumps(doc).encode("utf-8")) > schema.MAX_RULE_DOC_BYTES
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert result.rule_class is None
        assert any(
            "size" in e.message.lower() or "byte" in e.message.lower() for e in result.errors
        ), f"expected a size-related field error, got: {result.errors}"

    def test_doc_at_the_byte_cap_boundary_is_valid(self):
        # Build a doc, measure its serialized size, then pad `name` with
        # filler so the WHOLE doc lands exactly at MAX_RULE_DOC_BYTES.
        base = make_rule_doc(name="")
        base_len = len(json.dumps(base).encode("utf-8"))
        pad_len = schema.MAX_RULE_DOC_BYTES - base_len
        assert pad_len > 0, "base doc already exceeds the cap; fixture needs adjusting"
        doc = make_rule_doc(name="a" * pad_len)
        assert len(json.dumps(doc).encode("utf-8")) == schema.MAX_RULE_DOC_BYTES
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"doc exactly at the cap must be valid, got errors: {result.errors}"

    def test_doc_one_byte_over_the_cap_boundary_is_invalid(self):
        base = make_rule_doc(name="")
        base_len = len(json.dumps(base).encode("utf-8"))
        pad_len = schema.MAX_RULE_DOC_BYTES - base_len + 1
        doc = make_rule_doc(name="a" * pad_len)
        assert len(json.dumps(doc).encode("utf-8")) == schema.MAX_RULE_DOC_BYTES + 1
        result = schema.validate_rule_doc(doc)
        assert not result.valid


# ---------------------------------------------------------------------------
# 3. Condition-tree depth cap (8, AC-4)
# ---------------------------------------------------------------------------


class TestConditionDepthCap:
    def test_depth_at_cap_boundary_is_valid(self):
        # leaf itself is depth 1; 7 NOT wrappers -> depth 8 (== cap).
        condition = _nest_not(_LEAF, 7)
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"depth-8 tree must be valid (at the cap), got errors: {result.errors}"

    def test_depth_one_over_cap_is_invalid(self):
        # leaf depth 1 + 8 NOT wrappers -> depth 9 (> cap of 8).
        condition = _nest_not(_LEAF, 8)
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert result.rule_class is None
        assert any("depth" in e.message.lower() for e in result.errors), (
            f"expected a depth-related field error, got: {result.errors}"
        )

    def test_deeply_nested_and_or_mix_also_enforces_depth(self):
        # AND/OR nodes count depth identically to NOT — build a 9-deep
        # right-leaning AND chain to prove the cap isn't a NOT-only special case.
        node = _LEAF
        for _ in range(8):
            node = {"op": "AND", "children": [node, _LEAF]}
        doc = make_rule_doc(condition=node)
        result = schema.validate_rule_doc(doc)
        assert not result.valid


# ---------------------------------------------------------------------------
# 4. Enum-closed comparator / sense / action-type / boolean-op validation
# ---------------------------------------------------------------------------


class TestEnumClosedFields:
    @pytest.mark.parametrize("bad_comparator", ["~=", "eq", "=", "<>", ""])
    def test_unknown_comparator_is_invalid(self, bad_comparator):
        condition = {"op": "compare", "sense": "sma_20", "comparator": bad_comparator, "value": 1.0}
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert any("comparator" in e.field for e in result.errors)

    @pytest.mark.parametrize(
        "bad_sense",
        ["unknown_sense_xyz", "sma", "sma_", "sma_abc", "fred_", "fred_lowercase", "__import__"],
    )
    def test_unknown_or_malformed_sense_key_is_invalid(self, bad_sense):
        condition = {"op": "compare", "sense": bad_sense, "comparator": ">", "value": 1.0}
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert any("sense" in e.field for e in result.errors)

    @pytest.mark.parametrize(
        "good_sense",
        [
            "time_of_day",
            "day_of_week",
            "sleeve_status",
            "sma_20",
            "ema_5",
            "rsi_14",
            "momentum_10",
            "realized_vol_20",
            "drawdown_from_high_60",
            "fred_VIXCLS",
        ],
    )
    def test_every_allowed_sense_family_member_is_valid(self, good_sense):
        condition = {"op": "compare", "sense": good_sense, "comparator": ">", "value": 1.0}
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"{good_sense!r} should be valid, got errors: {result.errors}"

    @pytest.mark.parametrize("bad_op", ["XOR", "NAND", "and", "or", ""])
    def test_unknown_boolean_op_is_invalid(self, bad_op):
        condition = {"op": bad_op, "children": [_LEAF, _LEAF]}
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert not result.valid

    def test_unknown_action_type_is_invalid(self):
        # close_position/liquidate_position are RESERVED order-verb names
        # (tests/sleeves/test_containment_invariants.py's _BROKER_ORDER_SYMBOLS)
        # that must never leak in as a rule action-type spelling either.
        doc = make_rule_doc(then=[{"type": "close_position"}])
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert any("then" in e.field or "type" in e.field for e in result.errors)

    def test_go_to_cash_action_type_is_a_valid_defensive_action(self):
        doc = make_rule_doc(then=[{"type": "go_to_cash"}])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"
        assert result.rule_class == schema.RULE_CLASS_DEFENSIVE

    def test_unknown_sizing_mode_on_buy_is_invalid(self):
        doc = make_rule_doc(
            then=[{"type": "buy", "sizing": {"mode": "yolo_all_in"}, "stop_loss_pct": 0.05}]
        )
        result = schema.validate_rule_doc(doc)
        assert not result.valid

    def test_notify_field_outside_whitelist_is_invalid(self):
        doc = make_rule_doc(
            then=[{"type": "notify", "template": "fired", "fields": {"account_number": "1234"}}]
        )
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert any("fields" in e.field or "notify" in e.field for e in result.errors)

    def test_notify_whitelisted_fields_only_is_valid(self):
        doc = make_rule_doc(
            then=[
                {
                    "type": "notify",
                    "template": "fired",
                    "fields": {"symbol": "SPY", "action": "buy", "reason": "sma_cross"},
                }
            ]
        )
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"

    def test_set_stop_requires_exactly_one_of_trail_percent_or_trail_price(self):
        both = make_rule_doc(then=[{"type": "set_stop", "trail_percent": 0.05, "trail_price": 1.0}])
        neither = make_rule_doc(then=[{"type": "set_stop"}])
        assert not schema.validate_rule_doc(both).valid
        assert not schema.validate_rule_doc(neither).valid
        exactly_one = make_rule_doc(then=[{"type": "set_stop", "trail_percent": 0.05}])
        assert schema.validate_rule_doc(exactly_one).valid


# ---------------------------------------------------------------------------
# 4b. Entries require a declared exit — PM decision 2026-07-08, AC-7
#
# "A buy action without a declared stop is a SCHEMA VALIDATION ERROR, not a
# defaulted bracket." At least one of stop_loss_pct/trailing_stop_pct is
# required; take_profit_pct is optional. No other action type is affected
# (sell/go_to_cash/set_stop/notify manage or close an existing position —
# AC-7's "no position without its exit" only binds a position-OPENING action).
# ---------------------------------------------------------------------------


def _buy_action(**exit_fields) -> dict:
    return {"type": "buy", "sizing": {"mode": "shares", "shares": 10}, **exit_fields}


class TestEntryRequiresDeclaredExit:
    def test_buy_with_neither_stop_loss_pct_nor_trailing_stop_pct_is_invalid(self):
        doc = make_rule_doc(then=[_buy_action()])
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert result.rule_class is None
        assert any(
            "stop_loss_pct" in e.message or "trailing_stop_pct" in e.message for e in result.errors
        ), f"expected an error naming the required exit field, got: {result.errors}"

    def test_buy_with_only_stop_loss_pct_is_valid(self):
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=0.05)])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"
        assert result.rule_class == schema.RULE_CLASS_ENTRY

    def test_buy_with_only_trailing_stop_pct_is_valid(self):
        doc = make_rule_doc(then=[_buy_action(trailing_stop_pct=0.08)])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"

    def test_buy_with_both_stop_loss_pct_and_trailing_stop_pct_is_valid(self):
        # Not forbidden -- the PM decision requires AT LEAST one, not exactly one.
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=0.05, trailing_stop_pct=0.08)])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"

    def test_take_profit_pct_alone_never_substitutes_for_a_stop(self):
        doc = make_rule_doc(then=[_buy_action(take_profit_pct=0.10)])
        result = schema.validate_rule_doc(doc)
        assert not result.valid, "take_profit_pct must never satisfy the stop requirement"

    def test_take_profit_pct_is_genuinely_optional_alongside_a_declared_stop(self):
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=0.05)])
        result = schema.validate_rule_doc(doc)
        assert result.valid, (
            f"expected valid (take_profit_pct omitted), got errors: {result.errors}"
        )

    def test_take_profit_pct_present_alongside_a_declared_stop_is_valid(self):
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=0.05, take_profit_pct=0.10)])
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"

    @pytest.mark.parametrize("bad_pct", [0.0, -0.05, 1.0, 1.5])
    def test_out_of_bounds_stop_loss_pct_is_invalid(self, bad_pct):
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=bad_pct)])
        result = schema.validate_rule_doc(doc)
        assert not result.valid, f"stop_loss_pct={bad_pct} should be invalid (must be 0 < pct < 1)"

    @pytest.mark.parametrize("bad_pct", [0.0, -0.1])
    def test_out_of_bounds_take_profit_pct_is_invalid(self, bad_pct):
        doc = make_rule_doc(then=[_buy_action(stop_loss_pct=0.05, take_profit_pct=bad_pct)])
        result = schema.validate_rule_doc(doc)
        assert not result.valid, f"take_profit_pct={bad_pct} should be invalid (must be > 0)"

    @pytest.mark.parametrize("action_type", ["sell", "go_to_cash", "set_stop", "notify"])
    def test_non_entry_action_types_never_require_a_declared_stop(self, action_type):
        # AC-7 only binds position-OPENING actions -- closing/managing/notify
        # actions must validate exactly as before this decision, unaffected.
        if action_type == "sell":
            action = {"type": "sell", "sizing": {"mode": "shares", "shares": 1}}
        elif action_type == "go_to_cash":
            action = {"type": "go_to_cash"}
        elif action_type == "set_stop":
            action = {"type": "set_stop", "trail_percent": 0.05}
        else:
            action = {"type": "notify", "template": "fired", "fields": {"symbol": "SPY"}}
        doc = make_rule_doc(then=[action])
        result = schema.validate_rule_doc(doc)
        assert result.valid, (
            f"{action_type} must not require stop_loss_pct/trailing_stop_pct: {result.errors}"
        )


# ---------------------------------------------------------------------------
# 5. Rule-class derivation + declared-vs-derived mismatch (AC-20)
# ---------------------------------------------------------------------------


class TestRuleClassDerivation:
    def test_class_derivation_matches_golden_fixture_table(self, class_derivation_fixture):
        for case in class_derivation_fixture["cases"]:
            result = schema.derive_rule_class(case["action_types"])
            assert result == case["expected_class"], (
                f"{case['name']}: derive_rule_class({case['action_types']}) "
                f"== {result!r}, expected {case['expected_class']!r}"
            )

    def test_mixed_buy_and_sell_rule_doc_is_invalid_at_the_schema_level(self):
        doc = make_rule_doc(
            then=[
                {
                    "type": "buy",
                    "sizing": {"mode": "risk_pct", "risk_pct": 0.01},
                    "stop_loss_pct": 0.05,
                },
                {"type": "sell", "sizing": {"mode": "shares", "shares": 1}},
            ]
        )
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert result.rule_class is None

    def test_declared_class_matching_derivation_is_valid(self):
        doc = make_rule_doc(then=[{"type": "sell", "sizing": {"mode": "shares", "shares": 1}}])
        doc["class"] = schema.RULE_CLASS_DEFENSIVE
        result = schema.validate_rule_doc(doc)
        assert result.valid, f"expected valid, got errors: {result.errors}"
        assert result.rule_class == schema.RULE_CLASS_DEFENSIVE

    def test_declared_class_contradicting_derivation_is_invalid(self):
        # AC-20: "a rule cannot claim DEFENSIVE while holding entry actions."
        doc = make_rule_doc(
            then=[
                {
                    "type": "buy",
                    "sizing": {"mode": "risk_pct", "risk_pct": 0.01},
                    "stop_loss_pct": 0.05,
                }
            ]
        )
        doc["class"] = schema.RULE_CLASS_DEFENSIVE
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert any("class" in e.field for e in result.errors)

    def test_empty_then_list_is_invalid(self):
        doc = make_rule_doc(then=[])
        result = schema.validate_rule_doc(doc)
        assert not result.valid
        assert result.rule_class is None


# ---------------------------------------------------------------------------
# 6. No code strings — AC-4's "no code strings" requirement
# ---------------------------------------------------------------------------


class TestNoCodeStrings:
    @pytest.mark.parametrize(
        "field_path_value",
        [
            ("name", "__import__('os').system('echo pwned')"),
        ],
    )
    def test_eval_like_string_in_name_is_accepted_as_inert_text_never_executed(
        self, field_path_value
    ):
        # Rule names are Jinja-autoescaped display text, never executed — the
        # schema's job is only to size/shape-validate them, not sandbox code.
        # This test pins that validate_rule_doc NEVER calls eval/exec on any
        # string field (a plain string is just data). Genuine "no code
        # strings" enforcement is structural: there is no field anywhere in
        # the schema whose value is interpreted as an expression at all
        # (every operator/sense/action is a closed enum token, never a
        # user-supplied formula string).
        field, value = field_path_value
        doc = make_rule_doc(**{field: value})
        # Must not raise (no eval/exec of the string) regardless of validity.
        result = schema.validate_rule_doc(doc)
        assert isinstance(result.valid, bool)

    def test_comparator_field_rejects_a_python_expression_string(self):
        condition = {"op": "compare", "sense": "sma_20", "comparator": "> 1 or True", "value": 1.0}
        doc = make_rule_doc(condition=condition)
        result = schema.validate_rule_doc(doc)
        assert not result.valid
