"""``strict_tool``: a closed copy of a tool definition and nothing else changed.

The helper marks a tool strict and sets ``additionalProperties`` false on every
object node of its input schema. Names, property types, enums, required lists
and descriptions stay as the site wrote them, so the model sees the same
contract; the original definition is never touched. The tests use the two real
closed tool definitions and a synthetic nest that goes deeper than they do.
"""

from __future__ import annotations

import copy

import pytest

import llm_request
from advisors.asset_swap_engine import _EMIT_SWAP_CANDIDATES_TOOL
from advisors.logic_change_engine import _EMIT_LOGIC_EDITS_TOOL

CLOSED_TOOLS = {
    "asset_swap": _EMIT_SWAP_CANDIDATES_TOOL,
    "logic_change": _EMIT_LOGIC_EDITS_TOOL,
}
NESTED_TOOL = {
    "name": "emit_nested",
    "description": "Synthetic nest deeper than the real tools.",
    "input_schema": {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["a", "b"]},
                                "path": {"type": "array", "items": {"type": ["string", "integer"]}},
                                "leaf": {"type": "object", "properties": {"v": {"type": "number"}}},
                            },
                            "required": ["kind"],
                        },
                    }
                },
            },
            "either": {
                "anyOf": [
                    {"type": "object", "properties": {"x": {"type": "string"}}},
                    {"type": "string"},
                ]
            },
        },
        "required": ["outer"],
    },
}


def object_nodes(node):
    """Oracle: every dict in the schema whose ``type`` is the string ``object``."""
    found = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(object_nodes(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(object_nodes(value))
    return found


def without_closure(node):
    """The tool with the strict flag and every additionalProperties key removed."""
    node = copy.deepcopy(node)

    def strip(value):
        if isinstance(value, dict):
            value.pop("additionalProperties", None)
            for child in value.values():
                strip(child)
        elif isinstance(value, list):
            for child in value:
                strip(child)

    strip(node)
    node.pop("strict", None)
    return node


@pytest.mark.parametrize("name", list(CLOSED_TOOLS))
class TestTheTwoClosedTools:
    def test_the_tool_is_marked_strict(self, name):
        assert llm_request.strict_tool(CLOSED_TOOLS[name])["strict"] is True

    def test_every_object_node_of_the_input_schema_is_closed(self, name):
        original = CLOSED_TOOLS[name]
        assert len(object_nodes(original["input_schema"])) == 2

        nodes = object_nodes(llm_request.strict_tool(original)["input_schema"])

        assert len(nodes) == 2
        assert [n.get("additionalProperties") for n in nodes] == [False, False]

    def test_names_types_enums_required_lists_and_descriptions_are_unchanged(self, name):
        original = CLOSED_TOOLS[name]

        strict = llm_request.strict_tool(original)

        assert without_closure(strict) == without_closure(original)

    def test_the_original_definition_is_not_mutated_and_shares_no_node_with_the_copy(self, name):
        original = CLOSED_TOOLS[name]
        before = copy.deepcopy(original)

        strict = llm_request.strict_tool(original)

        assert original == before
        assert "strict" not in original
        assert strict is not original
        assert strict["input_schema"] is not original["input_schema"]
        original_nodes = {id(n) for n in object_nodes(original["input_schema"])}
        assert not original_nodes & {id(n) for n in object_nodes(strict["input_schema"])}

    def test_applying_it_twice_changes_nothing_more(self, name):
        once = llm_request.strict_tool(CLOSED_TOOLS[name])

        assert llm_request.strict_tool(once) == once


class TestObjectNodeCoverage:
    def test_closes_objects_at_every_depth_and_inside_alternatives(self):
        expected = len(object_nodes(NESTED_TOOL["input_schema"]))
        assert expected == 5

        nodes = object_nodes(llm_request.strict_tool(NESTED_TOOL)["input_schema"])

        assert len(nodes) == expected
        assert all(n.get("additionalProperties") is False for n in nodes)

    def test_adds_no_closure_to_nodes_that_are_not_objects(self):
        strict = llm_request.strict_tool(NESTED_TOOL)

        def non_objects(node):
            found = []
            if isinstance(node, dict):
                if "type" in node and node["type"] != "object":
                    found.append(node)
                for value in node.values():
                    found.extend(non_objects(value))
            elif isinstance(node, list):
                for value in node:
                    found.extend(non_objects(value))
            return found

        assert all("additionalProperties" not in n for n in non_objects(strict["input_schema"]))
        assert without_closure(strict) == without_closure(NESTED_TOOL)

    def test_an_object_that_was_already_open_is_closed(self):
        tool = copy.deepcopy(NESTED_TOOL)
        tool["input_schema"]["additionalProperties"] = True

        strict = llm_request.strict_tool(tool)

        assert strict["input_schema"]["additionalProperties"] is False
