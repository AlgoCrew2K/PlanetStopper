"""
RED tests for the MC-gating magic-number extraction cycle of math_engine.py.

Scope (binding):
    math_engine.run_monte_carlo — the only function in scope for this cycle.
    Other math layers (calculate_20d_vol, calculate_14d_atr_pct, the seven
    extracted layers) are out of scope here and are covered by their own
    test modules.

Project rule (math_engine.py): "No magic numbers — every constant named +
source comment." Today, run_monte_carlo retains several bare numeric literals
in its body and signature defaults. This module:

  1. Asserts via AST that run_monte_carlo's call path contains ZERO unnamed
     non-structural numeric literals — every meaningful number must resolve
     via ast.Name to a module-level constant (or to LOOKBACK_DAYS already
     used by sibling functions). Signature defaults (simulation_paths,
     neighbor_k) are included in the scan because they are domain knobs,
     not structural plumbing.

  2. Fixed-seed regression pins — captures run_monte_carlo's output across
     seven regimes (insufficient history, boundary at the MC-min threshold,
     low/high vol, argpartition active, extreme today-return, constant-zero
     degenerate, non-default simulation_paths/neighbor_k overrides). Originally
     a byte-equivalence pin for the magic-number rename cycle; the pinned
     values were RE-CAPTURED from the fixed producer after Cluster 2
     (mc-knn-exit-gate) intentionally changed run_monte_carlo's math — AC-1
     standardizes the kNN feature vector, AC-2 returns an out-of-band
     insufficient sentinel, AC-4 excludes the early-window days. The pins now
     guard fixed-seed determinism against UNINTENDED drift; any future change
     to a pinned value must be justified.

  3. Constant existence + value tests — asserts the proposed module-level
     constants exist after GREEN, with the right values, and are referenced
     by run_monte_carlo.

Naming proposals (test-writer recommendation; implementer may rename so long
as the AST scan + behavioral pins still pass):

  PCT_SCALAR (already exists at module scope, value 100.0) — REUSE for the
    four sites that convert pct<->decimal or scale a decimal return to pct:
      - holdings[i]["last_percent_change"] * 100.0  (pct already, scaled)
      - spy_today_return / 100.0                    (pct -> decimal)
      - returns_matrix.dot(weights) * 100.0          (decimal -> pct)
      - ((P - below) / P) * 100.0                    (fraction -> pct prob)

  MC_INSUFFICIENT_HISTORY_SENTINEL = None (Cluster 2 AC-2) — the out-of-band
    signal returned when len(valid_dates) is below the MC-history threshold.
    It REPLACED the prior MC_INSUFFICIENT_HISTORY_PROB = 100.0, which was
    fail-dangerous: 100 >= MC_BREAKDOWN_THRESHOLD permanently vetoed the
    protective trailing stop. The risk-engine contract is now "if we have no
    data the MC second opinion is UNAVAILABLE — signal that distinctly so the
    protective stop still fires on its own condition". A language literal, not
    a magic number, but still named so the short-circuit's intent is greppable.

  MC_MIN_HISTORY_DAYS = 20 — minimum number of historical days required to
    run the Monte Carlo sim; below this, the function short-circuits to the
    INSUFFICIENT_HISTORY_PROB sentinel. Distinct from LOOKBACK_DAYS (which
    is the 20-day realized-vol window used by calculate_20d_vol) because
    the two values are CONCEPTUALLY independent — they happen to share value
    20 today but represent different domain decisions. Implementer may choose
    to alias to LOOKBACK_DAYS if the team prefers a single concept; this
    test does not force separation, only NAMING.

  MC_VOL_WINDOW_DAYS = 20 — the rolling SPY-volatility window in the MC
    distance calculation; appears today as the bare literal 19 (i.e.,
    MC_VOL_WINDOW_DAYS - 1) at three call sites:
      - start_idx = max(0, i - 19)             # rolling window start
      - if len(spy_returns) >= 19              # today-vol guard
      - np.std(np.append(spy_returns[-19:],..))# today-vol slice
    Implementer should introduce the 20-day window constant and derive
    the offset inline (`MC_VOL_WINDOW_DAYS - 1`) at each site, or introduce
    a single named offset (e.g., `MC_VOL_WINDOW_OFFSET = 19`).

  MC_DEFAULT_SIMULATION_PATHS = 5000 — current keyword-arg default for
    `simulation_paths`. Domain knob (sim convergence vs. runtime).

  MC_DEFAULT_NEIGHBOR_K = 150 — current keyword-arg default for
    `neighbor_k`. Domain knob (kNN regime locality).

Structural literals (NOT magic numbers — explicitly whitelisted by the
scanner): 0, 1, -1, 0.0, 2 (Euclidean-distance exponent). Each is justified
in the STRUCTURAL_WHITELIST docstring below.

Tolerance policy: ZERO. Behavioral pins use exact equality (==) against
captured floats. The constant-naming refactor is a pure rename + lookup; it
MUST NOT perturb arithmetic. Any deviation indicates the implementer
reordered operations or replaced a literal with a non-bit-equivalent
expression and must be reverted.

AST note (do NOT trip): Python 3.8+ parses `-X` as
UnaryOp(USub, Constant(X)) — never Constant(-X). The scanner walks Constants
directly and does not inspect surrounding UnaryOp wrappers; whitelisting
-1 by absolute value would be wrong. We whitelist the integer 1 (not -1)
and rely on the structural-positive form. Today run_monte_carlo contains
no -X literal patterns, so this is a defensive note only.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import numpy as np
import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "mc_gating"


# --- Synthetic-history builder ---------------------------------------------


def _date_key(i: int) -> str:
    """ISO date string for synthetic day index i (only needs to sort)."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """
    Expand a fixture's compact historical_data_spec into the nested dict
    shape run_monte_carlo consumes:
        { date: { ticker: { "daily_ret": float } } }

    Builder is a test helper (not a fixture field) so the fixture only
    declares INTENT. Keep this in sync with the kinds referenced by
    tests/fixtures/math_engine/mc_gating/*.json.
    """
    kind = spec["kind"]
    num_days = spec["num_days"]
    history: dict[str, dict[str, dict[str, float]]] = {}

    if kind == "alternating":
        spy_amp = spec["spy_amplitude"]
        aaa_amp = spec["aaa_amplitude"]
        for i in range(num_days):
            spy_ret = spy_amp if i % 2 == 0 else -spy_amp
            aaa_ret = aaa_amp if i % 2 == 0 else -aaa_amp
            history[_date_key(i)] = {
                "SPY": {"daily_ret": spy_ret},
                "AAA": {"daily_ret": aaa_ret},
            }
    elif kind == "constant":
        spy_ret = spec["spy_ret"]
        aaa_ret = spec["aaa_ret"]
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {"daily_ret": spy_ret},
                "AAA": {"daily_ret": aaa_ret},
            }
    else:
        raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")

    return history


# --- Fixture discovery ------------------------------------------------------


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            out.append((p.name, json.load(fh)))
    return out


FIXTURES = _load_fixtures()


# --- Behavioral-equivalence pins (ZERO TOLERANCE) ---------------------------


@pytest.mark.parametrize(
    "fixture_name,fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_run_monte_carlo_behavioral_equivalence_pin(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Captures the exact present-implementation output under a fixed numpy
    seed across seven regimes. Refactor must be byte-equivalent.

    Zero-tolerance assertion: `actual == expected` (NOT pytest.approx).
    The constant-naming refactor is a pure rename — no float-associativity
    changes allowed. If a future implementer needs to fold expressions
    differently, this test catches it and the change must be justified to
    reviewer + memory'd as an intentional drift.
    """
    func_name = fixture["function"]
    assert func_name == "run_monte_carlo", (
        f"{fixture_name}: only run_monte_carlo is in scope for this cycle"
    )

    inputs = fixture["inputs"]
    history = _build_history(inputs["historical_data_spec"])
    holdings = inputs["holdings"]
    spy_today = inputs["spy_today_return"]
    sim_paths = inputs["simulation_paths"]
    neighbor_k = inputs["neighbor_k"]
    seed = inputs["numpy_seed"]

    actual = math_engine.run_monte_carlo(
        holdings,
        history,
        spy_today,
        simulation_paths=sim_paths,
        neighbor_k=neighbor_k,
        seed=seed,
    )

    # Insufficient-history fixtures pin the out-of-band sentinel, not a
    # numeric value (Cluster 2 AC-2). They declare expected_is_insufficient_
    # sentinel instead of an `expected` float.
    if fixture.get("expected_is_insufficient_sentinel"):
        assert actual is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
            f"Fixture {fixture_name}: insufficient-history short-circuit must "
            f"return MC_INSUFFICIENT_HISTORY_SENTINEL "
            f"({math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL!r}), got "
            f"{actual!r}. Derivation: {fixture['derivation']}."
        )
        return

    expected = fixture["expected"]
    assert actual is not None, (
        f"Fixture {fixture_name}: expected a numeric MC probability but "
        f"run_monte_carlo returned the insufficient sentinel. If this fixture "
        f"is now on the insufficient path, set expected_is_insufficient_"
        f"sentinel: true instead of an expected float."
    )

    # Zero-tolerance: the fixed-seed MC output is deterministic to the ulp.
    assert float(actual) == float(expected), (
        f"Fixture {fixture_name}: behavioral equivalence broken. "
        f"expected {expected!r}, got {actual!r}. "
        f"Derivation: {fixture['derivation']}. "
        f"This is a fixed-seed regression pin — a deviation means the MC "
        f"arithmetic changed and the drift must be justified."
    )


# --- Static-source magic-number scanner -------------------------------------

# Trivially structural literals that do not carry domain meaning.
# Every entry has a justification — DO NOT add to this set without one.
STRUCTURAL_WHITELIST = {
    0,  # array index / accumulator init / np.zeros sentinel
    1,  # increment / single-axis slice / off-by-one
    -1,  # last-element index (NOTE: AST stores as UnaryOp(USub, Constant(1)); we whitelist 1 directly)
    0.0,  # zero default for missing daily_ret / structural accumulator
    2,  # Euclidean-distance exponent (sqrt(a**2 + b**2)) — structural geometry, not a domain knob
}


def _collect_run_monte_carlo_numeric_literals() -> list[tuple[int, Any]]:
    """
    Walk the AST of run_monte_carlo and return every numeric Constant that
    is NOT in the structural whitelist and NOT referenced via a Name to a
    module-level constant. Includes signature defaults.

    Returns list of (lineno, value) tuples. Empty list == compliant.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_monte_carlo":
            target = node
            break
    assert target is not None, "run_monte_carlo not found in math_engine.py"

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if not isinstance(sub, ast.Constant):
            continue
        val = sub.value
        # bool is a subclass of int — skip True/False
        if isinstance(val, bool):
            continue
        if not isinstance(val, (int, float)):
            continue
        if val in STRUCTURAL_WHITELIST:
            continue
        offenders.append((sub.lineno, val))

    return offenders


def test_no_unnamed_magic_numbers_in_run_monte_carlo() -> None:
    """
    Project rule: no magic numbers in math_engine.py. After GREEN, every
    non-structural numeric literal in run_monte_carlo MUST be replaced by
    a Name reference to a module-level constant.

    This test fails RED today: run_monte_carlo body contains bare 100.0
    (multiple sites), 20, 19 (multiple sites), 5000, and 150. GREEN means
    each of those is replaced by a named constant (see module docstring
    for proposed names; implementer may rename).
    """
    offenders = _collect_run_monte_carlo_numeric_literals()
    assert not offenders, (
        "Unnamed magic numbers in run_monte_carlo. Project rule "
        "(math_engine.py): every constant must be named + source-commented. "
        f"Offenders (line, value): {offenders}. "
        "Fix in GREEN by extracting module-level named constants and "
        "replacing the bare literals with Name references. See this module's "
        "docstring for naming proposals."
    )


# --- Constant existence + value tests ---------------------------------------
#
# Implementer may rename, but for each role we assert SOME constant exists
# with the right value AND is referenced by run_monte_carlo. The naming
# proposals are surfaced as the canonical-names-first check; if those are
# absent we fall back to scanning for ANY module-level int/float Name with
# the right value referenced in run_monte_carlo's body. Renames are fine;
# silently dropping a constant is not.


def _module_level_constant_names(value: Any) -> list[str]:
    """All module-level attribute names on math_engine whose value equals `value`."""
    out = []
    for name in dir(math_engine):
        if name.startswith("_"):
            continue
        attr = getattr(math_engine, name)
        if isinstance(attr, bool):
            continue
        if isinstance(attr, (int, float)) and attr == value and type(attr) is type(value):
            out.append(name)
    return out


def _names_referenced_in_run_monte_carlo() -> set[str]:
    """All ast.Name ids appearing inside run_monte_carlo (load context)."""
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_monte_carlo":
            target = node
            break
    assert target is not None
    names: set[str] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            names.add(sub.id)
    return names


def _assert_role_constant_present(value: Any, role_description: str) -> str:
    """
    Assert that at least one module-level constant on math_engine equals
    `value` AND is referenced by run_monte_carlo via Name. Returns the
    referenced constant name (first match) for downstream cross-checks.
    """
    candidates = _module_level_constant_names(value)
    assert candidates, (
        f"No module-level constant with value {value!r} found on math_engine "
        f"(role: {role_description}). Expected some name (e.g., per the "
        f"test module docstring proposals) to be introduced in GREEN."
    )
    referenced = _names_referenced_in_run_monte_carlo()
    matched = [c for c in candidates if c in referenced]
    assert matched, (
        f"Module-level constants exist with value {value!r} "
        f"({candidates}) but NONE are referenced inside run_monte_carlo "
        f"(role: {role_description}). The function must USE the named "
        f"constant, not just declare it."
    )
    return matched[0]


def test_pct_scalar_is_reused_in_run_monte_carlo() -> None:
    """
    PCT_SCALAR (=100.0) already exists for the volatility-scaling layer
    (math_engine.py:11). The four pct<->decimal sites in run_monte_carlo
    should REUSE PCT_SCALAR rather than introduce a duplicate name.

    Asserts PCT_SCALAR exists with value 100.0 AND that the literal name
    'PCT_SCALAR' appears in run_monte_carlo's body. Implementer can satisfy
    by replacing the four 100.0 sites with PCT_SCALAR; if the implementer
    instead introduces a DIFFERENT name for the same semantic role, that's
    a project-rule violation (no duplicate constants for one role) and
    this test fails.
    """
    assert hasattr(math_engine, "PCT_SCALAR"), (
        "math_engine.PCT_SCALAR missing — required to scale pct<->decimal."
    )
    assert math_engine.PCT_SCALAR == 100.0, (
        f"PCT_SCALAR should be 100.0, got {math_engine.PCT_SCALAR}"
    )
    referenced = _names_referenced_in_run_monte_carlo()
    assert "PCT_SCALAR" in referenced, (
        "run_monte_carlo does not reference PCT_SCALAR. The four 100.0 "
        "sites (last_percent_change scaling, spy_today_return/100.0, "
        "returns_matrix*100.0, probability frac*100.0) must be rewritten "
        "to use the existing PCT_SCALAR constant."
    )


def test_mc_insufficient_history_sentinel_is_named() -> None:
    """
    The insufficient-MC-history signal must be a NAMED module-level constant
    referenced by run_monte_carlo — not a bare literal in the short-circuit.

    Updated for Cluster 2 AC-2: the prior contract used an in-band probability
    sentinel (MC_INSUFFICIENT_HISTORY_PROB = 100.0). That was fail-dangerous —
    100 >= MC_BREAKDOWN_THRESHOLD permanently vetoed the protective trailing stop.
    The fixed contract returns a DISTINCT out-of-band sentinel
    (MC_INSUFFICIENT_HISTORY_SENTINEL = None). It is a language literal, not a
    magic NUMBER, but it must still be named so the short-circuit's intent is
    self-documenting and greppable.

    Asserts: (1) math_engine exposes a module-level attribute whose name
    signals "insufficient history" and whose value is the out-of-band sentinel
    (None); (2) run_monte_carlo references that constant by name.
    """
    # Find module-level constants that are None and named for the insufficient
    # path. None can't be discovered by value-equality on numeric scanners, so
    # match on the naming convention used by the GREEN implementation.
    none_named_constants = [
        name
        for name in dir(math_engine)
        if not name.startswith("_")
        and getattr(math_engine, name) is None
        and "INSUFFICIENT" in name.upper()
    ]
    assert none_named_constants, (
        "Expected a named module-level constant set to the out-of-band "
        "insufficient-history sentinel (None) — e.g. "
        "MC_INSUFFICIENT_HISTORY_SENTINEL. run_monte_carlo's "
        "insufficient-history short-circuit must return a named constant, not "
        "a bare `return None`."
    )
    referenced = _names_referenced_in_run_monte_carlo()
    matched = [c for c in none_named_constants if c in referenced]
    assert matched, (
        f"An insufficient-history sentinel constant exists "
        f"({none_named_constants}) but run_monte_carlo does not reference it by "
        f"name. The insufficient-history short-circuit must `return "
        f"<the named sentinel>`, not a bare literal."
    )


def test_mc_min_history_days_is_named() -> None:
    """
    The integer 20 in `if len(valid_dates) < 20:` is the MC minimum-history
    threshold. Must be named — proposed: MC_MIN_HISTORY_DAYS. Implementer
    may alias to LOOKBACK_DAYS (also =20) if the team decides the two roles
    are the same domain decision; either way the literal 20 must not stand
    bare in run_monte_carlo.
    """
    _assert_role_constant_present(20, "MC minimum-history-days threshold")


def test_mc_vol_window_constant_is_named() -> None:
    """
    The integer 19 appears three times in run_monte_carlo as the rolling
    SPY-volatility window offset (= window_days - 1):
      - max(0, i - 19)
      - if len(spy_returns) >= 19
      - spy_returns[-19:]
    Must be replaced by a named constant. Proposed: MC_VOL_WINDOW_DAYS (=20,
    with `- 1` derivation at each site) OR MC_VOL_WINDOW_OFFSET (=19).

    Asserts SOME module-level constant exists with value 19 (offset form)
    OR 20 (window-days form) that is referenced inside run_monte_carlo.
    """
    candidates_19 = _module_level_constant_names(19)
    candidates_20 = _module_level_constant_names(20)
    referenced = _names_referenced_in_run_monte_carlo()
    matched_19 = [c for c in candidates_19 if c in referenced]
    matched_20 = [c for c in candidates_20 if c in referenced]
    assert matched_19 or matched_20, (
        "Expected a named constant for the MC rolling-volatility window "
        "(currently bare 19 at three sites). Proposed: MC_VOL_WINDOW_OFFSET=19 "
        "OR MC_VOL_WINDOW_DAYS=20 (with `- 1` at each call site). "
        f"Module-level 19-valued names: {candidates_19}. "
        f"Module-level 20-valued names: {candidates_20}. "
        f"None of these are referenced inside run_monte_carlo."
    )


def test_mc_default_simulation_paths_is_named() -> None:
    """
    The keyword-arg default `simulation_paths=5000` is a domain knob (sim
    convergence vs. runtime cost). Must be named — proposed:
    MC_DEFAULT_SIMULATION_PATHS=5000.

    Asserts SOME module-level constant equals 5000 AND is referenced as
    the default for `simulation_paths` in run_monte_carlo's signature.
    """
    candidates = _module_level_constant_names(5000)
    assert candidates, (
        "Expected module-level constant with value 5000 for the MC "
        "simulation_paths default. Proposed: MC_DEFAULT_SIMULATION_PATHS."
    )
    # Inspect the FunctionDef's args defaults: a Name node (not Constant)
    # must back the `simulation_paths` keyword.
    src_path = pathlib.Path(math_engine.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_monte_carlo":
            func = node
            break
    assert func is not None
    # Map arg names to their default AST node. Defaults align to the
    # LAST N positional args where N = len(func.args.defaults).
    args = func.args.args
    defaults = func.args.defaults
    n_defaults = len(defaults)
    if n_defaults:
        default_for = dict(zip(args[-n_defaults:], defaults, strict=True))
    else:
        default_for = {}
    sim_arg = next((a for a in args if a.arg == "simulation_paths"), None)
    assert sim_arg is not None, "run_monte_carlo signature missing `simulation_paths`"
    assert sim_arg in default_for, "`simulation_paths` must have a default value"
    default_node = default_for[sim_arg]
    assert isinstance(default_node, ast.Name), (
        f"`simulation_paths` default must be a Name reference to a module-level "
        f"constant (e.g., MC_DEFAULT_SIMULATION_PATHS). Got {ast.dump(default_node)!r}."
    )
    assert default_node.id in candidates, (
        f"`simulation_paths` default is `{default_node.id}` but no module-level "
        f"constant by that name has value 5000. Candidates with value 5000: "
        f"{candidates}."
    )


def test_mc_default_neighbor_k_is_named() -> None:
    """
    The keyword-arg default `neighbor_k=150` is a domain knob (kNN regime
    locality). Must be named — proposed: MC_DEFAULT_NEIGHBOR_K=150.

    Same shape as test_mc_default_simulation_paths_is_named; asserts the
    default is a Name to a module-level constant equal to 150.
    """
    candidates = _module_level_constant_names(150)
    assert candidates, (
        "Expected module-level constant with value 150 for the MC "
        "neighbor_k default. Proposed: MC_DEFAULT_NEIGHBOR_K."
    )
    src_path = pathlib.Path(math_engine.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_monte_carlo":
            func = node
            break
    assert func is not None
    args = func.args.args
    defaults = func.args.defaults
    n_defaults = len(defaults)
    default_for = dict(zip(args[-n_defaults:], defaults, strict=True)) if n_defaults else {}
    nk_arg = next((a for a in args if a.arg == "neighbor_k"), None)
    assert nk_arg is not None, "run_monte_carlo signature missing `neighbor_k`"
    assert nk_arg in default_for, "`neighbor_k` must have a default value"
    default_node = default_for[nk_arg]
    assert isinstance(default_node, ast.Name), (
        f"`neighbor_k` default must be a Name reference to a module-level "
        f"constant (e.g., MC_DEFAULT_NEIGHBOR_K). Got {ast.dump(default_node)!r}."
    )
    assert default_node.id in candidates, (
        f"`neighbor_k` default is `{default_node.id}` but no module-level "
        f"constant by that name has value 150. Candidates with value 150: "
        f"{candidates}."
    )


# --- Sanity guard for the scanner ------------------------------------------


def test_run_monte_carlo_has_zero_magic_number_offenders_regression_canary() -> None:
    """
    Permanent regression canary: asserts that run_monte_carlo contains ZERO
    bare numeric literals outside the structural whitelist.

    The MC-gating cycle (task-mc-gating) eliminated all offending literals
    ({100.0, 20, 19, 5000, 150}) by promoting them to named module-level
    constants. This canary ensures they are never re-introduced.

    If this test fails, a bare numeric literal has been added back into
    run_monte_carlo. Fix: extract the literal to a named constant in
    math_engine.py and add a source comment explaining its provenance.
    Do NOT add it to STRUCTURAL_WHITELIST unless it is genuinely a
    structural/loop integer (0, 1, 2) with no domain meaning.
    """
    offenders = _collect_run_monte_carlo_numeric_literals()
    found_values = {v for _, v in offenders}
    assert found_values == set(), (
        f"Magic numbers re-introduced into run_monte_carlo: {found_values}. "
        f"Each must be extracted to a named module-level constant. "
        f"Full offender list (line, value): {sorted(offenders)}."
    )
