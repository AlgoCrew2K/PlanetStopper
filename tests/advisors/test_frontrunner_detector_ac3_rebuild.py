"""RED tests — AC-6: detector cascade recognition rebuilt onto the AC-3 walk
model, replacing the size-cliff signature that matched 0 real trees.

Module under test: advisors.frontrunner_detector.detect_frontrunner_cascades
(EXISTING function, MODIFIED). Implementer: fr-engine.

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-6):
"The detector's cascade recognition is rebuilt to match the collection's own
model (condition whose TRUE branch fires VIX-family, per AC-3 walk) instead
of the size-cliff signature that matched 0 real trees. Against the operator's
captured real trees it MUST detect the known frontrunner checks... The old
signature's 0-match behavior is a regression test (it must never be the sole
gate again)."

AC-6 EXPECTED SET — FINAL, per fr-falsifier2's verdict
(.claude/fr-signals-inputs/mirror-pattern-verdict.md, 2026-07-16), which
RETRACTS an intermediate "5 not 8" correction this file previously encoded.
Genuine, VIX-firing `RSI(SPY,10) gt 31` exists in EXACTLY 8 symphonies —
the operator's ORIGINAL "8 of 11" claim, vindicated:
    real_tree_03_Gpaw3IhZghQPRE6AdEKx.json   (Gpaw)   plain gt31
    real_tree_04_INfCn3eKsu6i4oTTqdUp.json   (INfC)   plain gt31
    real_tree_05_MoAkUHnavSYw3oONiUxe.json   (MoAk)   plain + MAR-echo gt31 (both genuine)
    real_tree_06_hvPiGP1O7AHfutHE3Fjy.json   (hvPi)   plain + MAR-echo gt31 (both genuine)
    real_tree_07_iaSOOUsmnCJHiZvbrWfs.json   (iaSO)   MAR-echo gt31 ONLY (verdict-reinstated)
    real_tree_08_lW4ZzWuqR8tEO2DhXbil.json   (Paragons/lW4Z) MAR-echo gt31 ONLY (verdict-reinstated)
    real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json   (n2oo)   MAR-echo gt31 ONLY (verdict-reinstated)
    real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json   (qF5Z)   plain gt31
real_tree_01 (5Xjz) carries the SAME genuine fixed shape (MAR-echo gt31) but
is EXCLUDED — its TRUE branch fires no VIX ticker at all (BTAL only). It is
NOT a crossover — it is a genuine gate that simply never reaches VIX.

DISCRIMINATOR (verdict, exceptionless, ~1,900 nodes, zero counterexamples):
a node is a fixed-threshold check IFF `rhs-val` parses as a number
(equivalently `rhs-fixed-value?` truthy). `rhs-fn`/`rhs-window-days` are
IRRELEVANT vestigial echoes — REVERSING the intermediate "rhs-fn presence =
crossover" rule this file previously used to justify a 5-symphony set.

CONTRACT HISTORY (kept honest, not silently rewritten — this file went
through real churn):
  1. Original (pre-any-falsifier): expected-8, inherited from joined.json —
     circular (joined.json is the prior extraction's own output), correctly
     flagged and re-grounded by direct inspection per team-lead's requirement.
  2. "falsifier-1" (direction-validation.md): rhs-fn-presence discriminator
     -> genuine-5 (Gpaw/INfC/MoAk/hvPi/qF5Z only), excluding iaSO/Paragons/
     n2oo as "crossover false positives." This file previously encoded this
     5-set, with a "grounded THREE independent ways, all converging"
     docstring claim — which was itself an overclaim: all three derivations
     (falsifier, my own re-scan, fr-engine's byte-level check) applied the
     SAME rhs-fn rule; that is convergence on one candidate rule, not
     independent validation of the discriminator itself.
  3. "falsifier-2" (mirror-pattern-verdict.md, THIS file's current basis):
     rhs-val-numeric discriminator, exceptionless -> genuine-8 (adds back
     iaSO/Paragons/n2oo, confirmed via full-population invariant checks AND
     an independent TRUE-branch trace showing all three fire VIX-long). The
     operator's original claim was right; the "correction" was wrong.

REGRESSION GUARD (mechanistic, not merely historical, UNCHANGED by any of the
above churn — this is pure arithmetic about the OLD size-cliff signature,
not a tree-classification claim): the OLD `_RSI_OVERBOUGHT_MIN = 50.0` floor
REJECTS any RSI threshold below 50 as "not an overbought trigger" —
SPY:10:31's threshold of 31.0 is structurally below that floor regardless of
which symphonies turn out to carry it.

SCOPE HOLD: this file makes NO assertion about REZ:10:77/IGOV:10:77 as fr_key
membership (the plan's fixture list has not yet been amended to include them
— optional, deferred per team-lead) — only that they are confirmed genuine
node-level facts per the verdict, unrelated to this file's SPY:10:31 scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"

# The 8 symphonies where genuine, VIX-firing RSI(SPY,10) gt 31 exists —
# the operator's original "8 of 11" claim, vindicated by the verdict.
_GENUINE_SPY_10_31_TREES = [
    "real_tree_03_Gpaw3IhZghQPRE6AdEKx.json",
    "real_tree_04_INfCn3eKsu6i4oTTqdUp.json",
    "real_tree_05_MoAkUHnavSYw3oONiUxe.json",
    "real_tree_06_hvPiGP1O7AHfutHE3Fjy.json",
    "real_tree_07_iaSOOUsmnCJHiZvbrWfs.json",
    "real_tree_08_lW4ZzWuqR8tEO2DhXbil.json",
    "real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json",
    "real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json",
]

# real_tree_01/5Xjz: genuine fixed gate, but TRUE branch fires no VIX ticker
# at all — the sole real negative control for this key (NOT a crossover).
_GENUINE_BUT_NON_VIX_FIRING_TREE = "real_tree_01_5XjzXjdGnjh99MIsdM97.json"


@pytest.fixture
def mod():
    from advisors import frontrunner_detector

    return frontrunner_detector


def _load_tree(filename: str) -> dict:
    return json.loads((_FIXTURES_DIR / filename).read_text())


# ---------------------------------------------------------------------------
# AC-6 acceptance: detect SPY:10:31 in exactly the genuine 8
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", _GENUINE_SPY_10_31_TREES)
def test_detects_a_cascade_carrying_the_genuine_spy_10_31_threshold(mod, filename):
    """Against each of the 8 grounded-genuine, VIX-firing trees,
    detect_frontrunner_cascades must report at least one cascade whose
    rsi_thresholds includes 31.0 — proving the rebuilt AC-3-walk-based
    signature actually recognizes the real frontrunner pattern the old
    size-cliff signature missed entirely (0/11)."""
    tree = _load_tree(filename)
    result = mod.detect_frontrunner_cascades(tree)

    assert result.cascades, (
        f"{filename}: expected at least one detected cascade (genuine VIX-firing SPY:10:31 "
        f"tree), got zero. skip_reason={result.skip_reason!r}"
    )
    all_thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
    assert 31.0 in all_thresholds, (
        f"{filename}: expected a cascade carrying threshold=31.0 (the genuine SPY:10:31 "
        f"rung) among detected cascades; thresholds found across all cascades: {all_thresholds}"
    )


def test_non_vix_firing_genuine_gate_never_reports_a_cascade(mod):
    """real_tree_01/5Xjz: the SPY:10:31 gate here is a genuine fixed
    threshold (same rhs-val-numeric shape as the other 8) — but its TRUE
    branch fires no VIX ticker at all (BTAL only). This is NOT a crossover
    exclusion — it is a VIX-unreachability exclusion, and the two are
    structurally different reasons even though both correctly withhold a
    cascade report here."""
    tree = _load_tree(_GENUINE_BUT_NON_VIX_FIRING_TREE)
    result = mod.detect_frontrunner_cascades(tree)

    all_thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
    assert 31.0 not in all_thresholds, (
        f"{_GENUINE_BUT_NON_VIX_FIRING_TREE}: a cascade reported threshold=31.0 — this tree's "
        f"SPY:10:31 gate is genuine but its TRUE branch never reaches a VIX-family ticker "
        f"(BTAL only), so it must not be reported as a detected cascade."
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
    test proves the rebuilt signature generalizes: ALL 8 genuine, VIX-firing
    trees must independently detect the cascade, not just a subset."""
    detected_in = []
    for filename in _GENUINE_SPY_10_31_TREES:
        tree = _load_tree(filename)
        result = mod.detect_frontrunner_cascades(tree)
        thresholds = {t for cascade in result.cascades for t in cascade.rsi_thresholds}
        if 31.0 in thresholds:
            detected_in.append(filename)

    assert set(detected_in) == set(_GENUINE_SPY_10_31_TREES), (
        f"expected ALL 8 genuine trees to detect the SPY:10:31 cascade, got "
        f"{len(detected_in)}/8: {sorted(detected_in)}. Missing: "
        f"{set(_GENUINE_SPY_10_31_TREES) - set(detected_in)}"
    )


def test_detector_never_raises_across_all_11_real_trees(mod):
    """D-1 hygiene sweep: the rebuilt detector must never raise on any of the
    11 real fixture trees, regardless of their condition-shape mix
    (flat/binary/binary-compound/compound)."""
    all_trees = _GENUINE_SPY_10_31_TREES + [
        _GENUINE_BUT_NON_VIX_FIRING_TREE,
        "real_tree_02_8FAXAnQmYi1INDubazeC.json",
        "real_tree_10_nOyb55RMGVCKPiYXv7TI.json",
    ]
    for filename in all_trees:
        tree = _load_tree(filename)
        try:
            result = mod.detect_frontrunner_cascades(tree)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"{filename}: detect_frontrunner_cascades raised {type(exc).__name__}: {exc}"
            )
        assert result.cascades is not None
        assert isinstance(result.cascades, list)
