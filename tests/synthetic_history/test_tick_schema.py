"""RED-phase tests for task #24 cycle A — VWAP sim/live schema gap (Option Y).

These tests verify, via AST and regex static analysis ONLY, that
``synthetic_history.py``:

1. Emits a new tick-dict field ``valid_vwap_weight`` (the per-minute portfolio
   coverage fraction). Today the value is computed locally as ``valid_alloc``
   and silently discarded — see investigation at
   ``docs/research/math_engine/vwap-sim-live-gap-investigation.md``.
2. Bumps its on-disk cache key with a schema version marker (``v2``) so stale
   6-field cache files written before the schema extension cannot be loaded
   and break the autotuner with ``KeyError: 'valid_vwap_weight'``.
3. No longer silently discards the locally-computed ``valid_alloc`` quantity.

Static analysis only — we never import ``synthetic_history`` because that
module pulls in ``requests``/``pandas`` and configures Alpaca credentials at
import time; importing risks accidental network access on test collection.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-under-test source — loaded once per session, parsed as text + AST.
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


@pytest.fixture(scope="module")
def synth_source() -> str:
    """Raw text of ``synthetic_history.py``."""
    assert _SYNTH_PATH.is_file(), f"expected production module at {_SYNTH_PATH}"
    return _SYNTH_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def synth_tree(synth_source: str) -> ast.Module:
    """AST of ``synthetic_history.py``."""
    return ast.parse(synth_source, filename=str(_SYNTH_PATH))


# ---------------------------------------------------------------------------
# AST helpers — locate the tick-dict construction inside the inner loop.
# ---------------------------------------------------------------------------


def _iter_dict_constructions(tree: ast.AST):
    """Yield every dict literal (ast.Dict) and dict(...) call in the module.

    Both forms are supported because an implementer may choose either when
    assembling the tick payload. We do NOT restrict to ``ticks.append({...})``
    so that minor refactors (e.g. assigning to ``tick = {...}`` then appending)
    still pass.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            yield node
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "dict":
                yield node


def _dict_literal_keys(node: ast.Dict) -> list[str]:
    """Return string keys from an ast.Dict literal (skip non-string keys)."""
    keys: list[str] = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value)
    return keys


def _dict_call_keys(node: ast.Call) -> list[str]:
    """Return keyword names from a ``dict(key=value, ...)`` call."""
    return [kw.arg for kw in node.keywords if kw.arg is not None]


def _find_tick_dict_node(tree: ast.AST) -> ast.AST:
    """Locate the tick dict construction.

    The existing tick dict (lines 235–242 in current code) is uniquely
    identifiable by carrying the schema-defining keys
    ``return``, ``mc_prob``, ``vol``, ``vwap_diff`` together. We use this
    multi-key signature (rather than a line range) so the test survives
    re-formatting / refactors.
    """
    TICK_SIGNATURE = {"return", "mc_prob", "vol", "vwap_diff"}
    candidates: list[ast.AST] = []
    for node in _iter_dict_constructions(tree):
        if isinstance(node, ast.Dict):
            keys = set(_dict_literal_keys(node))
        else:  # ast.Call (dict(...) form)
            keys = set(_dict_call_keys(node))
        if TICK_SIGNATURE.issubset(keys):
            candidates.append(node)
    assert candidates, (
        "Could not locate the tick-dict construction in synthetic_history.py. "
        "Expected a dict literal or dict(...) call carrying keys "
        f"{sorted(TICK_SIGNATURE)}. The RED test cannot proceed."
    )
    # If a future refactor produces multiple tick-shaped dicts (e.g. one in a
    # helper and one in the loop), require ALL of them to be schema-compliant.
    return candidates[0] if len(candidates) == 1 else candidates


def _tick_keys(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Dict):
        return _dict_literal_keys(node)
    if isinstance(node, ast.Call):
        return _dict_call_keys(node)
    raise TypeError(f"unexpected node kind: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Test 1 — Tick dict must include the literal key "valid_vwap_weight".
# ---------------------------------------------------------------------------


def test_tick_dict_includes_valid_vwap_weight_key(synth_tree: ast.Module) -> None:
    """The per-tick dict emitted by ``process_day()`` must expose the
    per-minute portfolio coverage fraction under the literal key
    ``valid_vwap_weight``.

    Why this exact key: ``math_engine.compute_vwap_breakdown_update`` accepts a
    parameter named ``valid_vwap_weight`` and gates on it via
    ``VWAP_WEIGHT_THRESHOLD``. The autotuner (cycle B) will read this key from
    the tick dict and pass it to the canonical math function — adopting the
    existing producer/consumer contract verbatim.
    """
    tick_node = _find_tick_dict_node(synth_tree)
    nodes = tick_node if isinstance(tick_node, list) else [tick_node]
    for node in nodes:
        keys = _tick_keys(node)
        assert "valid_vwap_weight" in keys, (
            f"tick dict at line {getattr(node, 'lineno', '?')} is missing the "
            "'valid_vwap_weight' key. Current keys: " + repr(sorted(keys))
        )


# ---------------------------------------------------------------------------
# Test 2 — Cache filename must carry a schema version marker.
# ---------------------------------------------------------------------------


def test_cache_filename_includes_schema_version_marker(synth_source: str) -> None:
    """The on-disk cache filename produced by ``generate_synthetic_history``
    must include a schema version marker (e.g. ``v2``).

    Rationale: when the tick schema grows from 6 fields to 7
    (``valid_vwap_weight`` added), every pre-existing
    ``cache/synthetic_history_<date>_<hash>.json`` file holds the OLD shape.
    Without a version bump in the cache key, ``autotuner.run_simulation`` will
    deserialize those stale ticks and raise ``KeyError`` (or worse, silently
    fall back to ``tick.get('valid_vwap_weight', 1.0)`` and re-introduce the
    over-permissive sim regime we are trying to fix).

    Static check: look for an f-string / format that targets
    ``synthetic_history`` and contains a ``v\\d+`` literal segment. We accept
    either ``synthetic_history_v2_<date>_<hash>.json`` or
    ``synthetic_history_<date>_<hash>_v2.json`` (or any other position) — the
    constraint is presence, not placement.
    """
    cache_lines = [
        line
        for line in synth_source.splitlines()
        if "synthetic_history_" in line and ".json" in line
    ]
    assert cache_lines, (
        "Could not locate the cache filename construction "
        "(expected a line containing 'synthetic_history_' and '.json')."
    )

    version_pattern = re.compile(r"v\d+")
    offenders: list[str] = []
    for line in cache_lines:
        if not version_pattern.search(line):
            offenders.append(line.strip())

    assert not offenders, (
        "Cache filename construction is missing a schema version marker "
        "(e.g. 'v2'). After adding the 'valid_vwap_weight' tick field, stale "
        "6-field caches will be loaded and break the autotuner. Offending "
        "line(s):\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Test 3 — `valid_alloc` (or equivalent coverage variable) must NOT be
# silently discarded; it must flow into the tick dict.
# ---------------------------------------------------------------------------


def test_valid_alloc_is_not_silently_discarded(
    synth_tree: ast.Module, synth_source: str
) -> None:
    """The per-tick coverage fraction (today named ``valid_alloc`` and
    accumulated in the holdings inner loop at line 230) must be surfaced into
    the tick dict — not dropped at the end of the loop.

    We accept any of the following implementations as "surfaced":
      * ``"valid_vwap_weight": valid_alloc`` (literal mapping in the tick dict)
      * The local variable is renamed to ``valid_vwap_weight`` and used as the
        value for the tick dict's ``valid_vwap_weight`` key
      * Any other expression in the tick dict whose value derives from the
        per-tick accumulator (e.g. ``valid_alloc / 1.0``, ``min(valid_alloc, 1.0)``)

    Concretely we require: the tick dict contains a key ``valid_vwap_weight``
    AND its value expression references the name ``valid_alloc`` OR
    ``valid_vwap_weight`` (i.e. the producer-side accumulator). This catches
    the regression where someone hardcodes ``"valid_vwap_weight": 1.0`` and
    re-introduces the Option-X drift.
    """
    tick_node = _find_tick_dict_node(synth_tree)
    nodes = tick_node if isinstance(tick_node, list) else [tick_node]

    PRODUCER_NAMES = {"valid_alloc", "valid_vwap_weight"}

    for node in nodes:
        value_expr: ast.AST | None = None
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and k.value == "valid_vwap_weight"
                ):
                    value_expr = v
                    break
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "valid_vwap_weight":
                    value_expr = kw.value
                    break

        assert value_expr is not None, (
            f"tick dict at line {getattr(node, 'lineno', '?')} does not define "
            "a 'valid_vwap_weight' entry — cannot verify provenance."
        )

        referenced_names = {
            n.id for n in ast.walk(value_expr) if isinstance(n, ast.Name)
        }
        assert PRODUCER_NAMES & referenced_names, (
            f"tick dict at line {getattr(node, 'lineno', '?')} sets "
            "'valid_vwap_weight' to an expression that does not reference any "
            f"of {sorted(PRODUCER_NAMES)} — value appears to be a hardcoded "
            "constant or unrelated quantity. The per-tick coverage accumulator "
            "is being silently discarded. Referenced names: "
            f"{sorted(referenced_names)}"
        )

    # Defensive: also confirm the inner-loop accumulator line is still present
    # (i.e. nobody dropped the +=alloc accumulation while pretending to fix
    # the schema). The inner-loop assignment is the source of truth for the
    # quantity; if it disappears, the tick value would be meaningless.
    assert re.search(r"\b(valid_alloc|valid_vwap_weight)\s*\+=", synth_source), (
        "The inner-loop coverage accumulator "
        "(`valid_alloc += alloc` / `valid_vwap_weight += alloc`) appears to "
        "have been removed. Without this accumulation the new tick field "
        "would be a meaningless constant."
    )
