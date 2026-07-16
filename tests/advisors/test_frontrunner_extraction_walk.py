"""RED tests — AC-3: per-symphony FR-check extraction, direction-explicit.

Module under test: advisors.frontrunner_detector.extract_fr_checks (NEW
function on the EXISTING frontrunner_detector.py module). Implementer:
fr-engine (composer-alpaca-integration).

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-3, FINAL discriminator
per fr-falsifier2's verdict — .claude/fr-signals-inputs/mirror-pattern-verdict.md,
2026-07-16, superseding the intermediate rhs-fn-presence rule this file
originally shipped with):

    extract_fr_checks(tree: dict) -> list[FRCheck]

Walks the tree; every condition node whose TRUE branch reaches a VIX-family
ticker yields ONE FRCheck (direction is read directly off each ancestor
if-node's `is-else-condition?` — never inferred, never size-based).
Self-referential VIX-timing gates (subject ticker itself VIX-family) yield
NO FRCheck at all (silent omission, not a partial/flagged record).

FINAL DISCRIMINATOR (verdict, exceptionless across ~1,900 real nodes, ZERO
counterexamples — E4/X1 in the verdict doc): a node is a fixed-threshold
check (joinable fr_key) IFF `rhs-val` parses as a number (equivalently
`rhs-fixed-value?` is truthy / there is no nested rhs ticker operand).
Otherwise (`rhs-val` is a ticker string) it is a genuine crossover — no
fr_key. **`rhs-fn` and `rhs-window-days` are IRRELEVANT vestigial echoes
that must be IGNORED** — this directly REVERSES the intermediate
"rhs-fn presence = crossover" rule (which over-fired on 166 confirmed-fixed
self-mirror nodes, e.g. REZ:10:77/IGOV:10:77, and would have wrongly
excluded 21 real fr_keys / ~45-47 memberships). The existing
`_parse_rsi_threshold` in this module already implements the CORRECT rule
(`rhs-fixed-value? is False -> None; else float(rhs-val) or None`) — the
earlier error was analysis-layer, not code-layer.

FRCheck (fr-engine's locked dataclass, 2026-07-16):
    fr_key: str | None            # f"{ticker}:{window}:{threshold}" — populated ONLY
                                    # for a genuine fixed-numeric-threshold condition
    ticker: str
    fn: str
    window: int | None
    comparator: str                # gt AND lt both extracted (AC-4 filters at join time)
    threshold: float | None
    vix_tickers: frozenset[str]
    branch_path: list[str]         # native list of "true"/"false", ROOT-TO-NODE ancestry,
                                    # last entry always "true" by construction
    node_id: str | None
    group_name: str | None
    rhs_fn: str | None = None      # see OPEN QUESTION below
    rhs_val: str | None = None     # see OPEN QUESTION below
    rhs_ticker: str | None = None  # populated for a ticker-vs-ticker condition
                                    # (rhs-fixed-value? False, rhs-val is a ticker string)

OPEN QUESTION (asked of fr-engine 2026-07-16, NOT yet answered — this file
deliberately does NOT assert a specific value for `rhs_fn` on the ticker-
crossover exemplar below pending the answer): the verdict's own E5 shows a
GENUINE ticker-crossover node's `rhs-fn` is semantically MEANINGFUL (e.g.
LQD-vs-XLV really is `RSI(LQD) gt RSI(XLV)` — the RHS `rhs-fn` tells you
WHICH indicator to apply to the RHS ticker), unlike the fixed-threshold
case where `rhs-fn` is genuinely vestigial noise to ignore. So it is an open
question whether FRCheck.rhs_fn should be populated ALONGSIDE rhs_ticker for
a genuine crossover (describing "which indicator, on which ticker"), rather
than the three buckets being strictly mutually exclusive as originally
specified. This file only asserts the CERTAIN invariant (fr_key XOR
rhs_ticker — a node is never both a fixed threshold AND a ticker crossover)
until fr-engine confirms rhs_fn's role for the crossover case.

CONTRACT HISTORY (for anyone reading old commits/PRs — the discriminator
went through 3 revisions before landing; keeping the record honest rather
than silently rewriting):
  1. Original grounding (rhs-fixed-value?==True alone) — this was actually
     CORRECT, matching the final verdict, but was not yet cross-verified.
  2. "falsifier-1" (direction-validation.md): rhs-fn PRESENCE is the
     discriminator — WRONG, an analysis-layer error from generalizing off
     2 nodes without checking the full ~1,900-node population. Concluded
     genuine SPY:10:31 = 5 symphonies (Gpaw/INfC/MoAk/hvPi/qF5Z), excluding
     iaSO/Paragons/n2oo as "false positives."
  3. "falsifier-2" (mirror-pattern-verdict.md, THIS file's current basis):
     rhs-val-numeric / rhs-fixed-value?-truthy is the discriminator —
     CONFIRMED exceptionless. RETRACTS falsifier-1's "5 not 8": iaSO/
     Paragons/n2oo (and REZ:10:77/IGOV:10:77) are genuine fixed-threshold
     checks. TRUE-branch tracing (verdict X4) confirms all three symphonies'
     SPY:10:31 gates fire VIX-long (UVIX/UVXY) — genuine SPY:10:31 cull set
     = 8 (the operator's original number), vindicating the operator's own
     "8 of 11" claim. Only real_tree_01/5Xjz remains excluded — a genuine
     fixed gate whose TRUE branch fires no VIX ticker at all (BTAL only).

FIXTURE PROVENANCE — every node id below independently re-verified by direct
inspection (not inherited from any teammate's report), against the 11
producer-captured real_tree_0{1-11}_*.json fixtures:
  - GENUINE positive #1: real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json, if-child id
    44a6ad64-cb3f-419b-99ef-4ad749b0e2e4 — RSI(SPY,10) gt 31, no rhs-fn key
    at all (the "plain" int-convention shape), fires VIXY-family —
    fr_key="SPY:10:31", branch_path 7 hops
    ['false','false','false','true','true','true','true'].
  - GENUINE positive #2 (the verdict-reinstated case): real_tree_08_lW4ZzWuqR8tEO2DhXbil.json
    (Paragons), if-child id 892d862a-94a8-4496-a5ed-ea2528fd278c —
    `rhs-fn="moving-average-return"` IS present (vestigial echo, per the
    verdict IGNORED) but `rhs-fixed-value?=true` + numeric `rhs-val="31"` ->
    genuine RSI(SPY,10) gt 31, fr_key="SPY:10:31", threshold=31.0.
    branch_path 6 hops ['false','false','true','true','true','true'] (the
    two leading "false" entries are genuine else-hops through risk-on
    gate(s) above it — the ELSE-branch negative-control shape the plan
    calls for, still valid: direction correctness was never in dispute).
  - TICKER-VS-TICKER crossover exemplar (settled, unaffected by the whole
    saga above — there was never a numeric threshold to dispute here):
    real_tree_06_hvPiGP1O7AHfutHE3Fjy.json, if-child id
    0d98c2bb-1839-4eee-aa0b-6f10bf384871 — RSI(LQD) gt RSI(XLV),
    rhs-fixed-value?=False, rhs-val="XLV" (a ticker, not a number) — fires
    UVXY. fr_key=None, threshold=None, rhs_ticker="XLV". rhs_fn's expected
    value is the OPEN QUESTION above — not asserted here.
  - NO_VIX_EITHER_SIDE negative control: real_tree_01_5XjzXjdGnjh99MIsdM97.json,
    if-nodes cc0f45de-4025-4412-ba18-3a5fb9759eef and
    8fdd1b89-ff40-47e8-bbb7-b2ed4093c3da — genuine fixed RSI(SPY,10) gt 31
    gates (rhs-fixed-value?=true, numeric rhs-val="31" — same shape as
    Paragons) whose TRUE branch's only ticker is BTAL (non-VIX). The
    EARLIER "dual mechanism" framing (rhs-fn crossover AND no-VIX,
    independently) is WRONG per the verdict — these are NOT crossovers at
    all, they are genuine fixed gates. The sole exclusion reason is
    VIX-unreachability: AC-3's walk criterion (TRUE branch reaches VIX)
    fails before crossover-vs-genuine classification is ever reached.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"

VIX_FAMILY_TICKERS = frozenset({"VIXY", "VIXM", "UVXY", "UVIX", "VXX", "SVXY", "SVIX"})


@pytest.fixture
def mod():
    from advisors import frontrunner_detector

    return frontrunner_detector


def _load_tree(filename: str) -> dict:
    return json.loads((_FIXTURES_DIR / filename).read_text())


def _find(checks, node_id: str):
    matches = [c for c in checks if getattr(c, "node_id", None) == node_id]
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Genuine fixed-threshold positive matches
# ---------------------------------------------------------------------------


def test_genuine_fixed_threshold_extracts_fr_key_and_direction_explicit_branch_path(mod):
    """real_tree_11/Golden Age: a genuine RSI(SPY,10) gt 31 -> VIXY-family
    condition (no rhs-fn key at all) must extract fr_key="SPY:10:31" with
    the exact root-to-node branch_path (7 hops, direction read off
    is-else-condition? — never inferred)."""
    tree = _load_tree("real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "44a6ad64-cb3f-419b-99ef-4ad749b0e2e4"
    check = _find(checks, target_id)
    assert check is not None, f"expected an FRCheck for node_id={target_id!r}, none found"
    assert check.fr_key == "SPY:10:31", f"expected fr_key='SPY:10:31', got {check.fr_key!r}"
    assert check.ticker == "SPY"
    assert check.window == 10
    assert check.threshold == 31.0
    assert check.comparator == "gt"
    assert isinstance(check.branch_path, list), (
        f"branch_path must be a native list, got {type(check.branch_path).__name__}"
    )
    assert all(v in ("true", "false") for v in check.branch_path), (
        f"branch_path entries must be literal 'true'/'false' strings, got {check.branch_path}"
    )
    assert check.branch_path[-1] == "true", "the LAST entry is always 'true' by construction"
    assert check.branch_path == ["false", "false", "false", "true", "true", "true", "true"], (
        f"expected the exact hand-traced 7-hop ancestry, got {check.branch_path}"
    )


def test_genuine_check_populates_exactly_fr_key_never_rhs_descriptor_fields(mod):
    """Invariant: for a genuine fixed-threshold check, ONLY fr_key/threshold
    are populated — rhs_val/rhs_ticker must be None (rhs_fn's expected value
    for a genuine check is ALSO None — the verdict is explicit that rhs-fn
    is vestigial noise for the fixed case specifically, so this assertion IS
    certain even though the crossover-case rhs_fn question is still open)."""
    tree = _load_tree("real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json")
    checks = mod.extract_fr_checks(tree)
    check = _find(checks, "44a6ad64-cb3f-419b-99ef-4ad749b0e2e4")
    assert check is not None
    assert check.rhs_fn is None
    assert check.rhs_val is None
    assert check.rhs_ticker is None


def test_paragons_node_is_genuine_fixed_threshold_not_crossover(mod):
    """THE verdict-reinstated case (mirror-pattern-verdict.md, 2026-07-16):
    the Paragons node carries a vestigial `rhs-fn='moving-average-return'`
    echo, but `rhs-fixed-value?=true` + numeric `rhs-val='31'` make it a
    GENUINE fixed threshold — the exceptionless discriminator ignores the
    rhs-fn echo entirely. This directly REVERSES the intermediate
    rhs-fn-presence rule this file previously encoded (see module docstring
    CONTRACT HISTORY). fr_key must be "SPY:10:31", NOT None."""
    tree = _load_tree("real_tree_08_lW4ZzWuqR8tEO2DhXbil.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "892d862a-94a8-4496-a5ed-ea2528fd278c"
    check = _find(checks, target_id)
    assert check is not None, f"expected an FRCheck for node_id={target_id!r}, none found"
    assert check.fr_key == "SPY:10:31", (
        f"expected fr_key='SPY:10:31' (genuine, per the verdict's exceptionless "
        f"rhs-val-numeric discriminator — the rhs-fn echo is vestigial noise), got {check.fr_key!r}"
    )
    assert check.threshold == 31.0
    assert check.ticker == "SPY"
    assert check.window == 10
    assert check.comparator == "gt"
    assert check.rhs_val is None, (
        "a genuine fixed-threshold check must not carry rhs_val — the threshold IS "
        "check.threshold, not a leftover crossover field"
    )
    assert check.rhs_ticker is None


def test_paragons_node_branch_path_is_correct(mod):
    """Direction correctness (branch_path) was never actually in dispute
    through any revision of the discriminator — AC-3's walk criterion (TRUE
    branch reaches VIX) is independent of fixed-vs-crossover classification.
    This is the ELSE-branch negative-control shape the plan calls for: a VIX
    leaf reached only via two genuine else-hops through risk-on gate(s)
    above it."""
    tree = _load_tree("real_tree_08_lW4ZzWuqR8tEO2DhXbil.json")
    checks = mod.extract_fr_checks(tree)
    check = _find(checks, "892d862a-94a8-4496-a5ed-ea2528fd278c")
    assert check is not None
    assert check.branch_path == ["false", "false", "true", "true", "true", "true"], (
        f"expected the exact hand-traced 6-hop ELSE-heavy ancestry, got {check.branch_path}"
    )


# ---------------------------------------------------------------------------
# Ticker-vs-ticker condition — the rhs_ticker case (settled, unaffected by
# the discriminator saga — there was never a numeric threshold here to
# dispute in the first place)
# ---------------------------------------------------------------------------


def test_ticker_vs_ticker_condition_populates_rhs_ticker_not_fr_key(mod):
    """real_tree_06: RSI(LQD) gt RSI(XLV) — rhs-fixed-value?=False, rhs-val
    is a TICKER string ("XLV"), not a numeric threshold — a genuine crossover
    under every discriminator hypothesis that has ever been on the table.
    fr_key/threshold must be None, rhs_ticker must carry the real value.
    Does NOT assert rhs_fn's value — see module docstring OPEN QUESTION
    (fr-engine has not yet confirmed whether rhs_fn should be populated
    alongside rhs_ticker for a genuine crossover)."""
    tree = _load_tree("real_tree_06_hvPiGP1O7AHfutHE3Fjy.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "0d98c2bb-1839-4eee-aa0b-6f10bf384871"
    check = _find(checks, target_id)
    assert check is not None, f"expected an FRCheck for the ticker-vs-ticker node_id={target_id!r}"
    assert check.fr_key is None, (
        f"a ticker-RHS condition must NEVER produce a joinable fr_key; got {check.fr_key!r}."
    )
    assert check.threshold is None
    assert check.rhs_ticker == "XLV", f"expected rhs_ticker='XLV', got {check.rhs_ticker!r}"
    assert check.ticker == "LQD"


# ---------------------------------------------------------------------------
# Fixed-vs-crossover exclusivity — the CERTAIN half of the invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "real_tree_06_hvPiGP1O7AHfutHE3Fjy.json",
        "real_tree_08_lW4ZzWuqR8tEO2DhXbil.json",
        "real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json",
        "real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json",
    ],
)
def test_fr_key_and_rhs_ticker_are_mutually_exclusive_across_every_extracted_check(mod, filename):
    """Hostile invariant sweep: a node is NEVER both a genuine fixed
    threshold AND a ticker crossover — exactly one of {fr_key populated},
    {rhs_ticker populated} holds (or neither, for a node whose RHS shape
    extract_fr_checks doesn't recognize at all). Deliberately does NOT
    assert anything about rhs_fn's role here — see module docstring OPEN
    QUESTION; this is the narrower, certain half of the original 3-way
    invariant, kept testable while that question is unresolved."""
    tree = _load_tree(filename)
    checks = mod.extract_fr_checks(tree)
    assert checks, f"{filename}: expected extraction to find at least one FRCheck"

    for check in checks:
        assert not (check.fr_key is not None and check.rhs_ticker is not None), (
            f"{filename} node_id={check.node_id!r}: fr_key and rhs_ticker are both populated "
            f"(fr_key={check.fr_key!r}, rhs_ticker={check.rhs_ticker!r}) — a node cannot be both "
            f"a genuine fixed threshold and a ticker crossover"
        )


# ---------------------------------------------------------------------------
# Self-referential exclusion — silent omission
# ---------------------------------------------------------------------------


def test_self_referential_vix_timing_gate_never_extracted(mod):
    """AC-3: 'Self-referential VIX-timing gates (subject ticker itself
    VIX-family) are excluded.' Hostile sweep: no extracted FRCheck anywhere
    in real_tree_08 (which is known to contain several VIXY-watching
    internal hedge-timing gates) may itself watch a VIX-family ticker —
    silent omission, not a partial/flagged record."""
    tree = _load_tree("real_tree_08_lW4ZzWuqR8tEO2DhXbil.json")
    checks = mod.extract_fr_checks(tree)
    assert checks, "sanity: tree 08 must extract at least one FRCheck"

    self_referential = [c for c in checks if c.ticker in VIX_FAMILY_TICKERS]
    assert not self_referential, (
        f"found {len(self_referential)} self-referential FRCheck(s) watching their own "
        f"VIX-family ticker — must be silently omitted: {self_referential}"
    )


# ---------------------------------------------------------------------------
# NO_VIX_EITHER_SIDE negative control
# ---------------------------------------------------------------------------


def test_no_vix_either_side_condition_never_extracted(mod):
    """real_tree_01/5Xjz: two genuine fixed RSI(SPY,10) gt 31 gates
    (rhs-fixed-value?=true, numeric rhs-val — same shape as the reinstated
    Paragons case) whose fire branch's only ticker is BTAL (non-VIX). These
    are NOT crossovers (the earlier "dual mechanism" framing was wrong per
    the verdict) — the sole exclusion reason is VIX-unreachability: AC-3's
    walk criterion (TRUE branch reaches VIX) fails before fixed-vs-crossover
    classification is ever reached, so no FRCheck is emitted for either
    node id, genuine gate or not."""
    tree = _load_tree("real_tree_01_5XjzXjdGnjh99MIsdM97.json")
    checks = mod.extract_fr_checks(tree)

    excluded_if_node_ids = {
        "cc0f45de-4025-4412-ba18-3a5fb9759eef",
        "8fdd1b89-ff40-47e8-bbb7-b2ed4093c3da",
    }
    # The FRCheck's own node_id is the if-CHILD's id (the condition-bearing
    # branch), not the outer if-node's — check by ticker/window/threshold
    # signature instead of a node_id match, since we're asserting ABSENCE.
    spy_10_31_checks = [c for c in checks if c.ticker == "SPY" and c.window == 10 and c.threshold == 31.0]
    assert not spy_10_31_checks, (
        f"expected NO SPY:10:31 FRCheck from tree 01's NO_VIX_EITHER_SIDE nodes "
        f"({excluded_if_node_ids}) — the genuine gate exists but its TRUE branch never "
        f"reaches VIX, got {spy_10_31_checks}"
    )


# ---------------------------------------------------------------------------
# Never-raises / structural hygiene across non-flat condition shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "real_tree_05_MoAkUHnavSYw3oONiUxe.json",  # binary + binary-compound shapes present
        "real_tree_07_iaSOOUsmnCJHiZvbrWfs.json",  # binary + binary-compound shapes present
        "real_tree_10_nOyb55RMGVCKPiYXv7TI.json",  # binary + binary-compound shapes present
    ],
)
def test_extract_fr_checks_never_raises_on_non_flat_condition_shapes(mod, filename):
    """Real trees carry binary/binary-compound/compound condition shapes
    (fr-engine's shape survey) in addition to the flat lhs-fn-on-if-child
    shape all the pinned examples above use. extract_fr_checks must never
    raise on any of them — a malformed/unrecognized shape degrades to
    'no FRCheck for this node', never a crash."""
    tree = _load_tree(filename)
    try:
        checks = mod.extract_fr_checks(tree)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"{filename}: extract_fr_checks raised {type(exc).__name__}: {exc}")
    assert isinstance(checks, list)


def test_extract_fr_checks_never_raises_on_malformed_input():
    """D-1-style hygiene: a non-dict / empty-dict input must degrade to an
    empty list, never raise."""
    from advisors import frontrunner_detector as mod

    assert mod.extract_fr_checks({}) == []
    assert mod.extract_fr_checks(None) == []  # type: ignore[arg-type]
    assert mod.extract_fr_checks({"step": "if"}) == []
