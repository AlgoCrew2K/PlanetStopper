"""Shared shape-agnostic helpers for prompt-caching RED tests (cache-fix cycle).

Prompt caching restructures an Anthropic ``messages[i]["content"]`` or
``system`` value from a bare string into a list of content blocks, e.g.::

    "some prompt text"
    -> [{"type": "text", "text": "some prompt text", "cache_control": {"type": "ephemeral"}}]

Any existing test that does ``"marker" in call_kwargs["messages"][0]["content"]``
assumes the bare-string shape. Once a site is converted, that same line
silently degrades from a substring check to a list-membership check (always
False, since no list element equals the literal marker string) — a mechanical
shape break, not a real regression. Route both existing and new tests through
these helpers instead of touching the raw value directly, so they tolerate
either shape.
"""

from __future__ import annotations


def extract_text(value: object) -> str:
    """Return the concatenated text of a content/system value.

    Accepts either the pre-cache-control bare string, or the post-cache-control
    list of content blocks (dicts with a "text" key, cache_control optional).
    Any other shape returns "" rather than raising — callers use this for
    substring/equality assertions, not shape validation.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(getattr(block, "text", ""))
        return "".join(parts)
    return ""


def find_cache_control_blocks(value: object) -> list[dict]:
    """Return the blocks in `value` that carry a "cache_control" key.

    `value` is a messages[i]["content"] or `system` value. Returns [] when
    `value` is a bare string (no per-block caching is possible on a string)
    or when no block in the list is marked.
    """
    if not isinstance(value, list):
        return []
    return [block for block in value if isinstance(block, dict) and "cache_control" in block]
