"""
RED tests for R3c — synthetic_history MC deterministic seed (AC-H3.2 cache-rebuild parity).

Bug: synthetic_history.py:233 calls math_engine.run_monte_carlo(...) without a seed=
kwarg. np.random.default_rng(None) pulls fresh OS entropy on every call, meaning two
cache-rebuild runs for the same (symphony_id, trading_day) produce different mc_prob
series — violating AC-H3.2 "bit-for-bit reproducible across trials".

Fix surface: propagate a deterministic seed derived from (symphony_id, trading_day)
into the run_monte_carlo call at synthetic_history.py:233.

Coverage:
  1. Static (AST): run_monte_carlo call in process_day must pass a seed= kwarg.
  2. Static (AST): seed expression must reference a deterministic derivation, not None.
  3. Seed derivation determinism: derive(symphony_id, trading_day) == derive(symphony_id, trading_day).
  4. Seed differentiability by trading_day: same symphony, different day → different seed.
  5. Seed differentiability by symphony_id: same day, different symphony → different seed.
  6. Byte-identical cache invariant: run_monte_carlo(..., seed=derived_seed) called twice
     with the same (symphony_id, trading_day) produces an identical float (strict ==).
  7. Different (symphony_id, trading_day) → different mc_prob series (seeding is keyed
     on the right inputs, not a constant).
  8. Bug-detection invariant: seed=None codepath produces non-identical results across
     two calls (verifying that the bug *would* be caught if the fix were reverted).
  9. No regression to the live run_monte_carlo production call site (alpha_bot_execution.py
     still passes seed= from derive_cycle_mc_seed; the synthetic_history fix is additive).

PA-18 compliance:
  NO mc_prob literals anywhere. Byte-identity is asserted as run_a == run_b.
  Seed derivation inputs (symphony_id, trading_day strings) are named-constant
  identifiers, not producer-computed floats.

Fixture file:
  tests/fixtures/math_engine/mc_rng_seeding/03_synthetic_history_mc_seed.json

Implementation note:
  We NEVER import synthetic_history directly — that module pulls in requests/pandas
  and configures Alpaca credentials at import time, risking accidental network access
  on test collection. Instead:
    - Tests 1-2 use static AST analysis of the source file.
    - Tests 3-9 call math_engine.run_monte_carlo directly, which is the exact function
      synthetic_history must call with a seed. This is safe and covers the math contract.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import re
from typing import Any

import pytest

import math_engine

# ---------------------------------------------------------------------------
# Paths and fixture loading
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"
_EXEC_PATH = _REPO_ROOT / "alpha_bot_execution.py"
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "math_engine" / "mc_rng_seeding"
_FIXTURE_FILE = _FIXTURE_DIR / "03_synthetic_history_mc_seed.json"


def _load_fixture() -> dict[str, Any]:
    with _FIXTURE_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# History builder (shared with mc_rng_seeding fixture spec)
# ---------------------------------------------------------------------------

def _date_key(i: int) -> str:
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict:
    kind = spec["kind"]
    history: dict = {}
    if kind == "alternating":
        num_days = spec["num_days"]
        spy_amp = spec["spy_amplitude"]
        aaa_amp = spec["aaa_amplitude"]
        bbb_amp = spec.get("bbb_amplitude", 0.0)
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spy_amp},
                "AAA": {"daily_ret": sign * aaa_amp},
                "BBB": {"daily_ret": sign * bbb_amp},
            }
    elif kind == "rows":
        # Pre-computed rows from the fixture (deterministic constants, not producer outputs).
        # Each row: {"date": "YYYY-MM-DD", "SPY": float, "AAA": float, "BBB": float}
        for row in spec["rows"]:
            history[row["date"]] = {
                ticker: {"daily_ret": row[ticker]}
                for ticker in ("SPY", "AAA", "BBB")
                if ticker in row
            }
    else:
        raise ValueError(f"Unknown spec kind: {kind!r}")
    return history


def _call_mc_with_seed(fixture: dict, seed: int | None) -> float:
    history = _build_history(fixture["historical_data_spec"])
    return float(
        math_engine.run_monte_carlo(
            fixture["holdings"],
            history,
            fixture["spy_today_return"],
            simulation_paths=fixture["simulation_paths"],
            neighbor_k=fixture["neighbor_k"],
            seed=seed,
        )
    )


# ---------------------------------------------------------------------------
# 1. Static (AST): run_monte_carlo call inside process_day must have seed= kwarg
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synth_source() -> str:
    assert _SYNTH_PATH.is_file(), f"expected production module at {_SYNTH_PATH}"
    return _SYNTH_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def synth_tree(synth_source: str) -> ast.Module:
    return ast.parse(synth_source, filename=str(_SYNTH_PATH))


def _find_run_monte_carlo_calls(tree: ast.Module) -> list[ast.Call]:
    """Locate every call to math_engine.run_monte_carlo in the AST."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run_monte_carlo"
            and isinstance(func.value, ast.Name)
            and func.value.id == "math_engine"
        ):
            calls.append(node)
    return calls


def test_process_day_run_monte_carlo_call_exists_in_synthetic_history(
    synth_tree: ast.Module,
) -> None:
    """
    Guard test: confirm at least one math_engine.run_monte_carlo call exists in
    synthetic_history.py. If this fires, the call site moved or was removed and
    subsequent structural tests have no target.
    """
    calls = _find_run_monte_carlo_calls(synth_tree)
    assert calls, (
        "No `math_engine.run_monte_carlo(...)` call found in synthetic_history.py. "
        "The call site at line ~233 may have been removed or renamed. "
        "R3c cannot verify seed propagation without a call site."
    )


def test_run_monte_carlo_call_in_synthetic_history_passes_seed_kwarg(
    synth_tree: ast.Module,
) -> None:
    """
    AC-R3c.1 (structural): every math_engine.run_monte_carlo call in
    synthetic_history.py must pass a `seed=` keyword argument.

    The bug: the current call is:
      mc_prob = math_engine.run_monte_carlo(holdings, hist_data_up_to_yesterday, spy_today, 300, 5)
    with no seed — np.random.default_rng(None) pulls OS entropy on every rebuild.

    The fix must add seed=<deterministic_expression> so two cache rebuilds for the
    same (symphony_id, trading_day) produce identical mc_prob series.

    This test will be RED until the fix lands.
    """
    calls = _find_run_monte_carlo_calls(synth_tree)
    assert calls, "No run_monte_carlo call found — see test_process_day_run_monte_carlo_call_exists."

    missing_seed: list[int] = []
    for call in calls:
        kwarg_names = {kw.arg for kw in call.keywords}
        if "seed" not in kwarg_names:
            missing_seed.append(getattr(call, "lineno", -1))

    assert not missing_seed, (
        f"math_engine.run_monte_carlo call(s) at line(s) {missing_seed} in "
        "synthetic_history.py are missing the `seed=` keyword argument. "
        "Without a deterministic seed, every cache rebuild for the same "
        "(symphony_id, trading_day) produces a different mc_prob series, "
        "violating AC-H3.2. Add: seed=<deterministic_seed_expression>"
    )


# ---------------------------------------------------------------------------
# 2. Static (AST): seed= expression must NOT be the literal None
# ---------------------------------------------------------------------------

def test_seed_kwarg_in_synthetic_history_is_not_literal_none(
    synth_tree: ast.Module,
) -> None:
    """
    A trivial but wrong fix would pass `seed=None` — that preserves the bug
    (None → OS entropy → non-deterministic). The fix must pass a non-None
    deterministic expression (e.g. a call to a seed-derivation helper).

    This test catches the degenerate fix and fails it at the structural level
    before any behavioral test runs.
    """
    calls = _find_run_monte_carlo_calls(synth_tree)
    if not calls:
        pytest.skip("No run_monte_carlo call found — covered by earlier test.")

    seed_is_none: list[int] = []
    for call in calls:
        for kw in call.keywords:
            if kw.arg != "seed":
                continue
            # seed=None is ast.Constant(value=None)
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                seed_is_none.append(getattr(call, "lineno", -1))

    assert not seed_is_none, (
        f"math_engine.run_monte_carlo call(s) at line(s) {seed_is_none} in "
        "synthetic_history.py pass `seed=None`. "
        "None → np.random.default_rng(None) → fresh OS entropy — this is the "
        "ORIGINAL BUG, not a fix. The seed expression must be a deterministic "
        "value derived from (symphony_id, trading_day) or equivalent stable inputs."
    )


# ---------------------------------------------------------------------------
# 2b. Static (AST): seed= expression must be a function call, not a constant
# ---------------------------------------------------------------------------

def test_seed_kwarg_in_synthetic_history_is_not_a_constant_integer(
    synth_tree: ast.Module,
) -> None:
    """
    A constant seed (e.g. seed=42) would pass the seed=None guard above but
    is equally wrong: every (symphony_id, trading_day) pair would share the
    same RNG state, making all cache entries numerically correlated.

    The seed= expression must be a CALL (to a deterministic seed-derivation
    helper), not a literal integer or any other constant.
    """
    calls = _find_run_monte_carlo_calls(synth_tree)
    if not calls:
        pytest.skip("No run_monte_carlo call found — covered by earlier test.")

    constant_seed: list[int] = []
    for call in calls:
        for kw in call.keywords:
            if kw.arg != "seed":
                continue
            if isinstance(kw.value, ast.Constant):
                constant_seed.append(getattr(call, "lineno", -1))

    assert not constant_seed, (
        f"math_engine.run_monte_carlo call(s) at line(s) {constant_seed} in "
        "synthetic_history.py pass a constant literal as `seed=`. "
        "A constant seed makes all (symphony_id, trading_day) pairs share the same "
        "RNG state — cache entries become numerically correlated across symphonies "
        "and days. The seed must be a CALL to a deterministic helper that encodes "
        "both symphony_id and trading_day."
    )


# ---------------------------------------------------------------------------
# 2c. Static (AST): if derive_cache_mc_seed exists, it must be in math_engine
#     and the call site must reference it
# ---------------------------------------------------------------------------

def test_derive_cache_mc_seed_signature_if_present() -> None:
    """
    The implementer may introduce `derive_cache_mc_seed(symphony_id, trading_day)`
    as a dedicated cache-context seed helper (distinct from derive_cycle_mc_seed
    which takes a YYYYMMDD_HHMM string). If added, it must:
      - Live in math_engine (not synthetic_history or alpha_bot_execution)
      - Accept exactly (symphony_id: str, trading_day: str) parameters
      - Return int

    If the function does NOT exist (implementer chose to reuse derive_cycle_mc_seed
    with a composite key), this test passes trivially — either approach is valid.
    """
    if not hasattr(math_engine, "derive_cache_mc_seed"):
        return  # Not added — implementer used composite-key approach; pass

    import inspect
    assert callable(math_engine.derive_cache_mc_seed), (
        "math_engine.derive_cache_mc_seed exists but is not callable."
    )
    sig = inspect.signature(math_engine.derive_cache_mc_seed)
    params = list(sig.parameters.keys())
    assert params == ["symphony_id", "trading_day"], (
        f"derive_cache_mc_seed signature mismatch. "
        f"Expected ['symphony_id', 'trading_day'], got {params!r}. "
        "The cache-context seed helper must accept exactly these two parameters "
        "to make the key space explicit."
    )
    # Determinism check: same inputs → same output
    seed_a = math_engine.derive_cache_mc_seed("sym-alpha-001", "2024-01-02")
    seed_b = math_engine.derive_cache_mc_seed("sym-alpha-001", "2024-01-02")
    assert seed_a == seed_b, (
        f"derive_cache_mc_seed('sym-alpha-001', '2024-01-02') returned "
        f"{seed_a!r} then {seed_b!r} — must be deterministic (pure function)."
    )
    assert isinstance(seed_a, int), (
        f"derive_cache_mc_seed returned {type(seed_a)!r}, expected int."
    )
    # Differentiability: different trading_day → different seed
    seed_other_day = math_engine.derive_cache_mc_seed("sym-alpha-001", "2024-01-03")
    assert seed_a != seed_other_day, (
        "derive_cache_mc_seed produces the same seed for the same symphony on "
        "two different trading days — trading_day is not encoded in the seed."
    )
    # Differentiability: different symphony → different seed
    seed_other_sym = math_engine.derive_cache_mc_seed("sym-beta-002", "2024-01-02")
    assert seed_a != seed_other_sym, (
        "derive_cache_mc_seed produces the same seed for the same trading_day "
        "across two different symphonies — symphony_id is not encoded in the seed."
    )


# ---------------------------------------------------------------------------
# 3. Seed derivation determinism: derive(symphony_id, trading_day) is pure
# ---------------------------------------------------------------------------

def test_cache_seed_derivation_is_deterministic_for_same_inputs() -> None:
    """
    Whatever seed-derivation scheme the implementer chooses, calling it twice
    with the same (symphony_id, trading_day) must return the same integer.

    We verify this by checking math_engine.derive_cycle_mc_seed is deterministic
    on cycle_id strings AND by verifying that a combined (symphony_id+trading_day)
    hashing approach (the expected implementation for synthetic_history) is
    also deterministic. The exact derivation function name is the implementer's
    choice — this test verifies the math_engine.derive_cycle_mc_seed contract
    still holds (it is the canonical seed-derivation primitive and must remain pure).

    PA-18: no expected seed values. We assert seed_a == seed_b across two calls.
    """
    fixture = _load_fixture()
    for case in fixture["seed_cases"]:
        # Simulate the derivation key the implementer will form:
        # e.g. f"{symphony_id}_{trading_day}" as a cycle_id-style string
        derived_key = f"{case['symphony_id']}_{case['trading_day']}"
        seed_call_1 = math_engine.derive_cycle_mc_seed(derived_key)
        seed_call_2 = math_engine.derive_cycle_mc_seed(derived_key)
        assert seed_call_1 == seed_call_2, (
            f"derive_cycle_mc_seed('{derived_key}') returned {seed_call_1!r} on "
            f"first call and {seed_call_2!r} on second call. Seed derivation must "
            "be a pure function for AC-H3.2 to hold across cache rebuilds."
        )
        assert isinstance(seed_call_1, int), (
            f"derive_cycle_mc_seed returned {type(seed_call_1)!r}, expected int."
        )


# ---------------------------------------------------------------------------
# 4 & 5. Seed differentiability — same symphony, different day → different seed
#         and same day, different symphony → different seed
# ---------------------------------------------------------------------------

def test_seed_differs_when_trading_day_changes_for_same_symphony() -> None:
    """
    If the seed does not encode trading_day, two different days for the same
    symphony would produce the same mc_prob — cache rebuilds on different days
    would be numerically identical even though the historical window differs.
    This is a correctness bug distinct from the non-determinism bug.

    We find the two 'symphony_A_day_1' and 'symphony_A_day_2' cases in the
    fixture and assert their derived seeds are different.

    PA-18: no expected seeds — we assert inequality between two derivations.
    """
    fixture = _load_fixture()
    cases_by_label = {c["label"]: c for c in fixture["seed_cases"]}
    case_a = cases_by_label["symphony_A_day_1"]
    case_b = cases_by_label["symphony_A_day_2"]

    assert case_a["symphony_id"] == case_b["symphony_id"], (
        "Fixture must provide two cases with the same symphony_id and different trading_day."
    )
    assert case_a["trading_day"] != case_b["trading_day"], (
        "Fixture must provide two cases with the same symphony_id and different trading_day."
    )

    seed_a = math_engine.derive_cycle_mc_seed(
        f"{case_a['symphony_id']}_{case_a['trading_day']}"
    )
    seed_b = math_engine.derive_cycle_mc_seed(
        f"{case_b['symphony_id']}_{case_b['trading_day']}"
    )

    assert seed_a != seed_b, (
        f"derive_cycle_mc_seed produced the same seed ({seed_a!r}) for "
        f"symphony_id='{case_a['symphony_id']}' on two different trading days "
        f"({case_a['trading_day']!r} and {case_b['trading_day']!r}). "
        "The seed must encode trading_day so mc_prob series differ across days."
    )


def test_seed_differs_when_symphony_changes_for_same_trading_day() -> None:
    """
    If the seed does not encode symphony_id, two different symphonies on the
    same trading day would share the same RNG state — a parity failure.

    We find 'symphony_A_day_1' and 'symphony_B_day_1' (same day, different
    symphony) and assert their derived seeds differ.

    PA-18: no expected seeds — we assert inequality.
    """
    fixture = _load_fixture()
    cases_by_label = {c["label"]: c for c in fixture["seed_cases"]}
    case_a = cases_by_label["symphony_A_day_1"]
    case_b = cases_by_label["symphony_B_day_1"]

    assert case_a["trading_day"] == case_b["trading_day"], (
        "Fixture must provide two cases with the same trading_day and different symphony_id."
    )
    assert case_a["symphony_id"] != case_b["symphony_id"], (
        "Fixture must provide two cases with the same trading_day and different symphony_id."
    )

    seed_a = math_engine.derive_cycle_mc_seed(
        f"{case_a['symphony_id']}_{case_a['trading_day']}"
    )
    seed_b = math_engine.derive_cycle_mc_seed(
        f"{case_b['symphony_id']}_{case_b['trading_day']}"
    )

    assert seed_a != seed_b, (
        f"derive_cycle_mc_seed produced the same seed ({seed_a!r}) for two "
        f"different symphonies ('{case_a['symphony_id']}', '{case_b['symphony_id']}') "
        f"on the same trading day ({case_a['trading_day']!r}). "
        "The seed must encode symphony_id so each symphony's mc_prob series is independent."
    )


# ---------------------------------------------------------------------------
# 6. Byte-identical cache invariant: same (symphony_id, trading_day) → identical mc_prob
# ---------------------------------------------------------------------------

def test_cache_rebuild_produces_bit_identical_mc_prob_for_same_symphony_and_day() -> None:
    """
    AC-R3c.3: two cache-rebuild calls for the same (symphony_id, trading_day)
    must produce bit-for-bit identical mc_prob values.

    We simulate two cache builds by calling math_engine.run_monte_carlo twice
    with the SAME deterministic seed (derived from the same inputs). If the fix
    is correct, the two calls agree strictly.

    This is the core correctness invariant: cache is safe to regenerate without
    changing autotuner replay parity.

    Tolerance: strict == (no pytest.approx). The numpy Generator seeded with
    the same integer must produce bit-identical draws within the same numpy
    version.

    PA-18: no expected mc_prob literal. We assert run_a == run_b.
    """
    fixture = _load_fixture()
    case = fixture["seed_cases"][0]  # symphony_A_day_1 — any case is fine
    derived_key = f"{case['symphony_id']}_{case['trading_day']}"

    seed = math_engine.derive_cycle_mc_seed(derived_key)

    # Simulate cache rebuild A
    result_a = _call_mc_with_seed(fixture, seed)
    # Simulate cache rebuild B (independent call, same seed)
    result_b = _call_mc_with_seed(fixture, seed)

    assert result_a == result_b, (
        f"Two run_monte_carlo calls with seed={seed!r} (derived from "
        f"symphony_id='{case['symphony_id']}', trading_day='{case['trading_day']}') "
        f"returned different mc_prob: {result_a!r} vs {result_b!r}. "
        "Bit-identical reproducibility broken — AC-H3.2 / AC-R3c.3 violated. "
        "Ensure run_monte_carlo uses numpy.random.default_rng(seed) with strict "
        "isolation (not np.random.seed which mutates global state)."
    )
    assert math.isfinite(result_a), (
        f"Seeded run_monte_carlo returned non-finite value {result_a!r}."
    )
    assert 0.0 <= result_a <= 100.0, (
        f"mc_prob {result_a!r} outside [0, 100]."
    )


# ---------------------------------------------------------------------------
# 7. Different (symphony_id, trading_day) → different mc_prob series
# ---------------------------------------------------------------------------

def test_different_symphony_and_day_produce_different_mc_prob() -> None:
    """
    Sanity check: seeding is keyed on the right inputs, not a constant.

    If derive_cycle_mc_seed always returned the same seed regardless of input
    (e.g. a bug where the hash is taken of an empty string), all
    (symphony_id, trading_day) pairs would produce the same mc_prob. This
    test catches that degenerate implementation.

    We take two fixture cases with different (symphony_id, trading_day) pairs
    and assert their mc_prob results differ.

    PA-18: no expected literals. We assert inequality between two runs.
    Note: identical mc_prob for distinct inputs is astronomically unlikely with
    simulation_paths=300 and balanced alternating history — collision would
    indicate a seeding bug, not a statistical fluke.
    """
    fixture = _load_fixture()
    cases_by_label = {c["label"]: c for c in fixture["seed_cases"]}
    case_a = cases_by_label["symphony_A_day_1"]
    case_b = cases_by_label["symphony_B_day_1"]

    assert (
        case_a["symphony_id"] != case_b["symphony_id"]
        or case_a["trading_day"] != case_b["trading_day"]
    ), "Fixture must provide two cases with at least one different input dimension."

    seed_a = math_engine.derive_cycle_mc_seed(
        f"{case_a['symphony_id']}_{case_a['trading_day']}"
    )
    seed_b = math_engine.derive_cycle_mc_seed(
        f"{case_b['symphony_id']}_{case_b['trading_day']}"
    )

    # Prerequisite: seeds must differ (if seeds are identical, the distinction
    # we are testing cannot be measured at the mc_prob level).
    assert seed_a != seed_b, (
        "Seeds for two distinct (symphony_id, trading_day) pairs are identical — "
        "seed differentiability tests should have caught this first."
    )

    result_a = _call_mc_with_seed(fixture, seed_a)
    result_b = _call_mc_with_seed(fixture, seed_b)

    assert result_a != result_b, (
        f"run_monte_carlo returned identical mc_prob ({result_a!r}) for two "
        "distinct (symphony_id, trading_day) pairs despite different seeds "
        f"({seed_a!r} vs {seed_b!r}). "
        "This indicates the seed is either ignored or the function always returns "
        "a constant — seeding is not keyed on the right inputs."
    )


# ---------------------------------------------------------------------------
# 8. Bug-detection invariant: seed=None is non-deterministic (validates the test can catch the bug)
# ---------------------------------------------------------------------------

def test_unseeded_calls_produce_non_identical_results_across_many_runs() -> None:
    """
    Verification that the bug the fix addresses IS detectable by the byte-identity
    test above (test #6).

    If seed=None calls were always identical, test #6 would be vacuous — it would
    pass even with the bug still present. This test confirms that calling
    run_monte_carlo 50 times with seed=None produces at least 2 distinct values,
    proving the RNG is non-deterministic without a seed.

    50 calls: the probability of all 50 independent OS-entropy draws producing
    identical results (with 300 paths over 50 days of alternating history) is
    negligibly small. If this test flakes, the fixture inputs are degenerate
    (e.g. all-zero history saturating the output) and must be revised.

    PA-18: no expected mc_prob. We assert that the set of results has cardinality > 1.
    """
    fixture = _load_fixture()
    results = {_call_mc_with_seed(fixture, seed=None) for _ in range(50)}
    assert len(results) > 1, (
        "run_monte_carlo(seed=None) produced identical results across 50 independent "
        "calls. This means the function is not drawing fresh entropy on each call "
        "OR the fixture inputs saturate the output (all paths above/below threshold). "
        "Either revise the fixture (use spy_today_return=0.0, balanced history, "
        "simulation_paths=300) or investigate why seed=None is deterministic here."
    )


# ---------------------------------------------------------------------------
# 9. No regression to the live production call site (alpha_bot_execution.py)
# ---------------------------------------------------------------------------

def test_alpha_bot_execution_live_call_site_still_passes_seed_kwarg() -> None:
    """
    Regression guard: alpha_bot_execution.py already passes seed= from
    derive_cycle_mc_seed (merged in R3a/R3b). The synthetic_history fix must
    not accidentally clobber or revert that call.

    Static AST check: all math_engine.run_monte_carlo calls in
    alpha_bot_execution.py continue to include a seed= kwarg.
    """
    assert _EXEC_PATH.is_file(), f"expected production module at {_EXEC_PATH}"
    src = _EXEC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_EXEC_PATH))

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run_monte_carlo"
            and isinstance(func.value, ast.Name)
            and func.value.id == "math_engine"
        ):
            calls.append(node)

    assert calls, (
        "No math_engine.run_monte_carlo call found in alpha_bot_execution.py. "
        "The live production call site appears to have been removed — "
        "this is a regression from the R3a/R3b work."
    )

    missing_seed: list[int] = []
    for call in calls:
        kwarg_names = {kw.arg for kw in call.keywords}
        if "seed" not in kwarg_names:
            missing_seed.append(getattr(call, "lineno", -1))

    assert not missing_seed, (
        f"REGRESSION: math_engine.run_monte_carlo call(s) at line(s) "
        f"{missing_seed} in alpha_bot_execution.py lost their seed= kwarg. "
        "R3a/R3b added seed=math_engine.derive_cycle_mc_seed(cycle_id) to the "
        "live production path. The R3c fix must not revert this."
    )
