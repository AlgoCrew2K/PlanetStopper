"""
AC-3 (math-audit HIGH) — RED tests for MC seed collision-safety at fleet scale.

THE DEFECT
----------
``derive_cycle_mc_seed`` maps a cycle_id to a numpy Generator seed via
``int(sha256(cycle_id), 16) % MC_SEED_MODULUS`` with ``MC_SEED_MODULUS = 2**31``.
The daemon runs ~98,280 cycles/year (252 trading days x 390 market minutes).
The birthday bound for n draws into M buckets is ~ n^2 / (2M); with
n = 98,280 and M = 2**31 that is ~2.2 expected collisions per year. A
collision means two different cycles seed the identical RNG state — the
determinism/independence claim in the ``MC_SEED_MODULUS`` comment ("~98k
distinct values/year with no collisions") is false.

THE FIX (AC-3)
--------------
Widen the seed space to at least ``2**64`` (or pass the full SHA-256 digest via
``numpy.random.SeedSequence``). ``numpy.random.default_rng`` accepts the full
64-bit space and beyond. The inaccurate comment is corrected.

WHAT THESE TESTS ASSERT
-----------------------
1. RED MARKER: at the pre-fix 2**31 modulus, a full trading year of cycle_ids
   produces at least one collision (the measured defect).
2. THE FIX PROOF: a full trading year of ~98,280 cycle_ids maps to entirely
   distinct seeds — zero collisions.
3. The effective seed space is at least 2**64 wide.
4. ``derive_cycle_mc_seed`` remains deterministic (same cycle_id -> same seed).
5. The inaccurate "~98k distinct values/year with no collisions" comment is
   corrected.
6. Regression guard: the 64-bit seed is consumed only via
   ``np.random.default_rng`` — never a legacy ``RandomState`` / ``np.random.seed``
   (which cap at 2**32-1 and would raise on the widened seed).

RED-VERIFICATION
----------------
``test_red_marker_*`` pins the pre-fix collision; it passes today and must fail
once the modulus is widened (the implementer deletes it as the GREEN step).
``test_full_trading_year_*`` is RED against pre-fix code.

PA-18 / fixture-provenance
--------------------------
No seed value is hardcoded. The cycle_id set is GENERATED from the documented
production cadence (every market minute of a 252-day trading year). Assertions
are on collision COUNT and seed-space SIZE — both derivable, never pinned to a
specific producer integer.
"""

from __future__ import annotations

import ast
import datetime
import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "mc_rng_seeding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _generate_trading_year_cycle_ids(year_start: datetime.date, num_trading_days: int) -> list[str]:
    """
    Generate the full set of YYYYMMDD_HHMM cycle_ids for ``num_trading_days``
    weekday trading days starting at ``year_start``, one per market minute
    (09:30-15:59 ET inclusive, 390 minutes/day).

    This mirrors the daemon's real cycle_id construction
    (current_et.strftime('%Y%m%d_%H%M')) at production cadence.
    """
    cycle_ids: list[str] = []
    day = year_start
    days_done = 0
    while days_done < num_trading_days:
        if day.weekday() < 5:  # Monday-Friday
            date_str = day.strftime("%Y%m%d")
            for hour in range(9, 16):
                minute_start = 30 if hour == 9 else 0
                for minute in range(minute_start, 60):
                    cycle_ids.append(f"{date_str}_{hour:02d}{minute:02d}")
            days_done += 1
        day += datetime.timedelta(days=1)
    return cycle_ids


# RED-VERIFICATION NOTE: a marker test (test_red_marker_prefix_modulus_collides_
# in_one_trading_year) lived here during the RED phase. It pinned the pre-fix
# defect — the 2**31 seed modulus produces at least one collision across a full
# trading year of ~98,280 cycle_ids — to prove the collision fixture genuinely
# exercised the bug. It was RED-verified against pre-fix code and removed once
# AC-3 widened the seed space. The behavioural proof is
# test_full_trading_year_cycle_ids_produce_distinct_seeds.


# ---------------------------------------------------------------------------
# 2. THE FIX PROOF — a full trading year maps to entirely distinct seeds
# ---------------------------------------------------------------------------


def test_full_trading_year_cycle_ids_produce_distinct_seeds() -> None:
    """
    AC-3 fix proof. derive_cycle_mc_seed must map every cycle_id of a full
    trading year (~98,280 cycles) to a DISTINCT seed — zero collisions.

    A collision means two different cycles seed the identical RNG state, which
    the MC_SEED_MODULUS comment falsely claims cannot happen at 2**31. RED
    against pre-fix code.
    """
    fx = _load_fixture("04_seed_collision_full_year.json")
    gen = fx["cycle_id_generation"]
    year_start = datetime.date.fromisoformat(gen["calendar_year_start"])
    cycle_ids = _generate_trading_year_cycle_ids(year_start, gen["num_trading_days"])

    seeds = [math_engine.derive_cycle_mc_seed(cid) for cid in cycle_ids]
    unique_seeds = set(seeds)
    collisions = len(seeds) - len(unique_seeds)

    if collisions:
        # Surface a couple of colliding cycle_id pairs for diagnosis.
        seen: dict[int, str] = {}
        examples: list[tuple[str, str]] = []
        for cid, seed in zip(cycle_ids, seeds):
            if seed in seen and len(examples) < 3:
                examples.append((seen[seed], cid))
            seen.setdefault(seed, cid)
        assert collisions == 0, (
            f"derive_cycle_mc_seed produced {collisions} seed collision(s) "
            f"across {len(cycle_ids)} cycle_ids of one trading year. "
            f"Example colliding cycle_id pairs: {examples}. "
            f"AC-3: widen MC_SEED_MODULUS to at least 2**64 (or use the full "
            f"SHA-256 digest via numpy SeedSequence) so birthday-bound "
            f"collisions cannot occur at fleet scale."
        )


# ---------------------------------------------------------------------------
# 3. Seed space is at least 2**64 wide
# ---------------------------------------------------------------------------


def test_seed_space_is_at_least_64_bits_wide() -> None:
    """
    AC-3: the seed space must be at least 2**64 wide so the birthday bound at
    ~98k cycles/year is negligible. Asserted as >= 2**64 (not == 2**64) so a
    full-digest / SeedSequence implementation exposing a larger space also
    passes.

    The width is probed structurally: MC_SEED_MODULUS, if it still exists as a
    modulus, must be >= 2**64; additionally, derive_cycle_mc_seed must be able
    to emit a value >= 2**32 for some cycle_id (a pure-31-bit scheme never can),
    which is a behavioural lower-bound check independent of the constant name.
    """
    # Structural: if MC_SEED_MODULUS still exists it must be wide enough.
    modulus = getattr(math_engine, "MC_SEED_MODULUS", None)
    if modulus is not None:
        assert modulus >= 2**64, (
            f"MC_SEED_MODULUS is {modulus} (~2**{modulus.bit_length() - 1}). "
            f"AC-3 requires a seed space of at least 2**64 — 2**31 is too small "
            f"(birthday bound ~2.2 collisions/year at 98k cycles)."
        )

    # Behavioural: across a full trading year at least one derived seed must
    # exceed 2**32 — impossible if the scheme is still capped at 31 bits.
    fx = _load_fixture("04_seed_collision_full_year.json")
    gen = fx["cycle_id_generation"]
    year_start = datetime.date.fromisoformat(gen["calendar_year_start"])
    cycle_ids = _generate_trading_year_cycle_ids(year_start, gen["num_trading_days"])
    seeds = [math_engine.derive_cycle_mc_seed(cid) for cid in cycle_ids]
    max_seed = max(seeds)
    assert max_seed >= 2**32, (
        f"The largest seed derived across a full trading year is {max_seed} "
        f"(< 2**32). The seed scheme is still effectively capped at 32 bits or "
        f"fewer. AC-3 requires an effective seed space of at least 2**64."
    )


# ---------------------------------------------------------------------------
# 4. Determinism preserved
# ---------------------------------------------------------------------------


def test_derive_cycle_mc_seed_remains_deterministic_after_widening() -> None:
    """
    AC-3 must not regress determinism: derive_cycle_mc_seed is a pure function —
    the same cycle_id must always produce the same seed, including across the
    seed-space widening. Sampled across a trading year.
    """
    fx = _load_fixture("04_seed_collision_full_year.json")
    gen = fx["cycle_id_generation"]
    year_start = datetime.date.fromisoformat(gen["calendar_year_start"])
    cycle_ids = _generate_trading_year_cycle_ids(year_start, gen["num_trading_days"])
    # Sample every 500th cycle_id to keep the determinism check fast.
    for cid in cycle_ids[::500]:
        seed_a = math_engine.derive_cycle_mc_seed(cid)
        seed_b = math_engine.derive_cycle_mc_seed(cid)
        assert seed_a == seed_b, (
            f"derive_cycle_mc_seed('{cid}') returned {seed_a!r} then {seed_b!r}. "
            f"It must be deterministic — same cycle_id, same seed."
        )
        assert isinstance(seed_a, int), (
            f"derive_cycle_mc_seed('{cid}') returned {type(seed_a).__name__}, "
            f"expected int (numpy default_rng / SeedSequence accept Python ints)."
        )


# ---------------------------------------------------------------------------
# 5. The inaccurate seed-modulus comment is corrected
# ---------------------------------------------------------------------------


def test_seed_modulus_comment_no_longer_claims_no_collisions_at_2_31() -> None:
    """
    AC-3: the MC_SEED_MODULUS comment claims "~98k distinct values/year with no
    collisions" — a birthday-bound error (2**31 actually yields ~2.2
    collisions/year). After the fix that false claim must be gone.

    Scans the math_engine source for the misleading phrase.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "no collisions" not in lowered, (
        "math_engine source still contains a 'no collisions' claim near the MC "
        "seed modulus. The audit flagged the '~98k distinct values/year with no "
        "collisions' comment as a birthday-bound error. AC-3 requires the "
        "comment be corrected to reflect the widened (>= 2**64) seed space."
    )


# ---------------------------------------------------------------------------
# 6. Regression guard — the 64-bit seed must only be consumed via default_rng
# ---------------------------------------------------------------------------


def test_mc_seed_is_consumed_only_via_default_rng_not_legacy_rng() -> None:
    """
    AC-3 regression guard. The widened 64-bit seed is safe ONLY because
    numpy.random.default_rng (and SeedSequence) accept the full [0, 2**64)
    unsigned space. The legacy numpy RNG entry points — np.random.RandomState
    and np.random.seed — cap at 2**32 - 1 and RAISE a ValueError on a seed
    above that. If a future change ever routes the MC seed through one of those
    legacy APIs, the 64-bit seed would crash run_monte_carlo at fleet scale.

    This static-AST guard asserts math_engine.py uses np.random.default_rng and
    contains NO call to RandomState or np.random.seed. It is not a tautology:
    it fails the moment a legacy RNG entry point is reintroduced into the module.

    Flagged as optional by quant-code-reviewer; encoded because it directly
    protects the AC-3 widening — a 64-bit seed through a legacy RNG is a hard
    crash, not a silent degradation.
    """
    tree = ast.parse(pathlib.Path(math_engine.__file__).read_text(encoding="utf-8"))

    default_rng_calls = 0
    legacy_rng_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # numpy attribute-chain calls: np.random.default_rng / .RandomState /
        # .seed (and the np.random.RandomState two-hop form).
        attr = func.attr
        if attr == "default_rng":
            default_rng_calls += 1
        elif attr in ("RandomState", "seed"):
            # Confirm it is a numpy.random.* call, not an unrelated .seed().
            chain = func.value
            chain_names: list[str] = []
            while isinstance(chain, ast.Attribute):
                chain_names.append(chain.attr)
                chain = chain.value
            if isinstance(chain, ast.Name):
                chain_names.append(chain.id)
            if "random" in chain_names or "np" in chain_names:
                legacy_rng_calls.append(f"{attr} (line {node.lineno})")

    assert not legacy_rng_calls, (
        f"math_engine.py uses a legacy numpy RNG entry point: {legacy_rng_calls}. "
        f"np.random.RandomState / np.random.seed cap the seed at 2**32 - 1 and "
        f"raise on the AC-3 64-bit MC seed. The MC seed must be consumed only "
        f"via np.random.default_rng (or SeedSequence), which accept the full "
        f"[0, 2**64) space."
    )
    assert default_rng_calls >= 1, (
        "math_engine.py no longer calls np.random.default_rng. The MC seed "
        "(derive_cycle_mc_seed) must be consumed via default_rng so the widened "
        "64-bit seed space (AC-3) is honoured."
    )
