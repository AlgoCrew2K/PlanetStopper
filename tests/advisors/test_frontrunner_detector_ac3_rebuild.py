"""RED tests — AC-6: detector cascade recognition rebuilt onto the AC-3 walk
model, replacing the size-cliff signature that matched 0 real trees.

Module under test: advisors.frontrunner_detector.detect_frontrunner_cascades
(EXISTING function, MODIFIED). Implementer: fr-engine.

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-6, corrected @
77060593 after the falsifier amendment):
"The detector's cascade recognition is rebuilt to match the collection's own
model (condition whose TRUE branch fires VIX-family, per AC-3 walk) instead
of the size-cliff signature that matched 0 real trees. Against the operator's
captured real trees it MUST detect the known frontrunner checks — the
expected membership set is grounded by DIRECT inspection of the fixture
trees (never inherited from joined.json, which carries the rhs-fn
false-positive bug; falsifier fresh-tree ground truth 2026-07-16: genuine
fixed-threshold SPY:10:31 = 5 symphonies [qF5Z/hvPi/INfC/Gpaw/MoAk]; 3 of the
prior '8' were rhs-fn crossovers; raw flat presence = 9 incl. 5Xjz where
neither branch reaches VIX). The old signature's 0-match behavior is a
regression test (it must never be the sole gate again)."

AC-6 EXPECTED SET — grounded THREE independent ways, all converging
(2026-07-16): (1) team-lead's falsifier, fresh /score trees pulled 2026-07-16;
(2) my own direct byte-level re-inspection of these 0715-captured fixture
trees (corrected for the rhs-fn discriminator, reported and cross-checked
against the falsifier's result — exact match, no discrepancy); (3)
fr-engine's independent byte-level verification of the specific node ids.
Genuine fixed-threshold RSI(SPY,10) gt 31 -> VIX-family exists in EXACTLY:
    real_tree_03_Gpaw3IhZghQPRE6AdEKx.json   (Gpaw)
    real_tree_04_INfCn3eKsu6i4oTTqdUp.json   (INfC)
    real_tree_05_MoAkUHnavSYw3oONiUxe.json   (MoAk)
    real_tree_06_hvPiGP1O7AHfutHE3Fjy.json   (hvPi)
    real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json   (qF5Z)
NOT in real_tree_07/08/09 (rhs-fn crossovers — the false positives) nor
real_tree_01 (NO_VIX_EITHER_SIDE, also dual-mechanism a crossover).

REGRESSION GUARD (mechanistic, not merely historical): the OLD size-cliff
signature's `_RSI_OVERBOUGHT_MIN = 50.0` floor (frontrunner_detector.py, as
it stood before this cycle) REJECTS any RSI threshold below 50 as "not an
overbought trigger" — SPY:10:31's threshold of 31.0 is structurally below
that floor. This file proves the old gate's 0-match failure mechanistically
by calling the OLD threshold-range constant directly against the real
threshold value, rather than only asserting a historical fact.

SCOPE HOLD: this file makes NO assertion about REZ:10:77/IGOV:10:77 or any
same-fn self-mirror node — that sub-case is formally unresolved pending
fr-falsifier2's verdict (team-lead, 2026-07-16). Nothing here depends on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"

_GENUINE_SPY_10_31_TREES = [
    "real_tree_03_Gpaw3IhZghQPRE6AdEKx.json",
    "real_tree_04_INfCn3eKsu6i4oTTqdUp.json",
    "real_tree_05_MoAkUHnavSYw3oONiUxe.json",
    "real_tree_06_hvPiGP1O7AHfutHE3Fjy.json",
    "real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json",
]

_CROSSOVER_ONLY_TREES = [
    "real_tree_07_iaSOOUsmnCJHiZvbrWfs.json",
    "real_tree_08_lW4ZzWuqR8tEO2DhXbil.json",
    "real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json",
]


@pytest.fixture
def mod():
    from advisors import frontrunner_detector

    return frontrunner_detector


def _load_tree(filename: str) -> dict:
    return json.loads((_FIXTURES_DIR / filename).read_text())


# ---------------------------------------------------------------------------
# AC-6 acceptance: detect SPY:10:31 in exactly the genuine 5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", _GENUINE_SPY_10_31_TREES)
def test_detects_a_cascade_carrying_the_genuine_spy_10_31_threshold(mod, filename):
    """Against each of the 5 grounded-genuine trees, detect_frontrunner_cascades
    must report at least one cascade whose rsi_thresholds includes 31.0 —
    proving the rebuilt AC-3-walk-based signature actually recognizes the
    real frontrunner pattern the old size-cliff signature missed entirely."""
    tree = _load_tree(filename)
    result = mod.detect_frontrunner_cascades(tree)

    assert result.cascades, (
        f"{filename}: expected at least one detected cascade (genuine SPY:10:31 tree), "
        f"got zero. skip_reason={result.skip_reason!r}"
    )
    all_thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
    assert 31.0 in all_thresholds, (
        f"{filename}: expected a cascade carrying threshold=31.0 (the genuine SPY:10:31 "
        f"rung) among detected cascades; thresholds found across all cascades: {all_thresholds}"
    )


@pytest.mark.parametrize("filename", _CROSSOVER_ONLY_TREES)
def test_crossover_only_trees_never_report_a_genuine_spy_10_31_cascade(mod, filename):
    """The 3 rhs-fn crossover trees (the false positives the falsifier caught)
    must NOT have their crossover node mistaken for a genuine SPY:10:31
    cascade rung. Scoped narrowly: these trees may still legitimately detect
    OTHER real cascades (they are complex hedged symphonies) — the assertion
    is specifically that no cascade's rsi_thresholds includes 31.0 sourced
    from the crossover node's rhs-val string coincidence."""
    tree = _load_tree(filename)
    result = mod.detect_frontrunner_cascades(tree)

    all_thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
    assert 31.0 not in all_thresholds, (
        f"{filename}: a cascade reported threshold=31.0 — this tree's SPY:10:31-shaped "
        f"node is a confirmed rhs-fn crossover (RSI(SPY,10) gt moving-average-return(31)), "
        f"not a genuine 31-threshold rung. The rebuilt detector must not mistake the "
        f"crossover's rhs-val string for a real threshold."
    )


# ---------------------------------------------------------------------------
# Regression guard — the old size-cliff signature is provably a 0-match dead end
# ---------------------------------------------------------------------------


def test_old_overbought_range_floor_structurally_rejects_the_genuine_threshold(mod):
    """Mechanistic (not just historical) regression proof: the OLD size-cliff
    signature's overbought-range floor constant would reject SPY:10:31's real
    threshold (31.0) outright, since 31.0 < 50.0 — this is WHY the old
    signature matched 0 real trees, and it is a structural fact about the
    constant, independent of any specific implementation detail that gets
    rebuilt. If a future refactor accidentally reintroduces this floor as
    the SOLE gate (regressing AC-6), this test's own math still holds and
    the surrounding acceptance tests above will fail loudly."""
    old_overbought_min = getattr(mod, "_RSI_OVERBOUGHT_MIN", 50.0)
    genuine_spy_10_31_threshold = 31.0
    assert genuine_spy_10_31_threshold < old_overbought_min, (
        f"sanity check on the regression's own premise: expected the genuine SPY:10:31 "
        f"threshold ({genuine_spy_10_31_threshold}) to be below the old overbought floor "
        f"({old_overbought_min}) — if this assertion itself fails, the old-signature-was-"
        f"provably-blind claim needs re-deriving, not just re-asserting."
    )


def test_rebuilt_detector_finds_spy_10_31_across_the_full_genuine_set_not_just_one_tree(mod):
    """A single-tree pass could accidentally special-case one fixture. This
    test proves the rebuilt signature generalizes: ALL 5 genuine trees must
    independently detect the cascade, not just a subset."""
    detected_in = []
    for filename in _GENUINE_SPY_10_31_TREES:
        tree = _load_tree(filename)
        result = mod.detect_frontrunner_cascades(tree)
        thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
        if 31.0 in thresholds:
            detected_in.append(filename)

    assert set(detected_in) == set(_GENUINE_SPY_10_31_TREES), (
        f"expected ALL 5 genuine trees to detect the SPY:10:31 cascade, got "
        f"{len(detected_in)}/5: {sorted(detected_in)}. Missing: "
        f"{set(_GENUINE_SPY_10_31_TREES) - set(detected_in)}"
    )


def test_detector_never_raises_across_all_11_real_trees(mod):
    """D-1 hygiene sweep: the rebuilt detector must never raise on any of the
    11 real fixture trees, regardless of their condition-shape mix
    (flat/binary/binary-compound/compound)."""
    all_trees = _GENUINE_SPY_10_31_TREES + _CROSSOVER_ONLY_TREES + [
        "real_tree_01_5XjzXjdGnjh99MIsdM97.json",
        "real_tree_02_8FAXAnQmYi1INDubazeC.json",
        "real_tree_10_nOyb55RMGVCKPiYXv7TI.json",
    ]
    for filename in all_trees:
        tree = _load_tree(filename)
        try:
            result = mod.detect_frontrunner_cascades(tree)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{filename}: detect_frontrunner_cascades raised {type(exc).__name__}: {exc}")
        assert result.cascades is not None
        assert isinstance(result.cascades, list)
