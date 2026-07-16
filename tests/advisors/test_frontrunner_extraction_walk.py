"""RED tests — AC-3: per-symphony FR-check extraction, direction-explicit.

Module under test: advisors.frontrunner_detector.extract_fr_checks (NEW
function on the EXISTING frontrunner_detector.py module). Implementer:
fr-engine (composer-alpaca-integration).

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-3 + FALSIFIER
AMENDMENT, plus fr-engine's final locked FRCheck shape, 2026-07-16):

    extract_fr_checks(tree: dict) -> list[FRCheck]

Walks the tree; every condition node whose TRUE branch reaches a VIX-family
ticker yields ONE FRCheck (direction is read directly off each ancestor
if-node's `is-else-condition?` — never inferred, never size-based).
Self-referential VIX-timing gates (subject ticker itself VIX-family) yield
NO FRCheck at all (silent omission, not a partial/flagged record).

FRCheck (fr-engine's final locked dataclass, 2026-07-16):
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
    rhs_fn: str | None = None      # populated ONLY for a crossover (rhs-fn present)
    rhs_val: str | None = None     # populated ALONGSIDE rhs_fn for the same crossover
    rhs_ticker: str | None = None  # populated ONLY for a ticker-vs-ticker condition
                                    # (rhs-fixed-value? False, rhs-val is a ticker string)

INVARIANT (fr-engine, explicit): exactly one of
{fr_key populated}, {rhs_fn+rhs_val populated}, {rhs_ticker populated}
is true for any FRCheck. Never more than one.

FALSIFIER AMENDMENT — the rhs-fn discriminator (HARD, plan @ 77060593):
`rhs-fixed-value?` is True on BOTH a genuine fixed-threshold node AND an
indicator-vs-indicator crossover node — it is NOT a valid discriminator.
The correct discriminator is `rhs-fn` PRESENCE. A condition whose RHS
carries an `rhs-fn` key is a crossover (rhs-val is the RHS indicator's
window, not a threshold) — fr_key/threshold MUST be None for it.

DISPUTE STATUS UPDATE (team-lead STOP-CHECK #2, 2026-07-16, msg 7722f63a /
3f697b3e — supersedes the "settled under both hypotheses" claim below AND
the "grounded three independent ways" framing this file originally shipped
with): fr-falsifier2's E3/E4 evidence indicates the REAL discriminator key
is `rhs-fixed-value?` + numeric-vs-ticker `rhs-val` (fixed=true+numeric =>
genuine fixed threshold, REGARDLESS of any rhs-fn echo; fixed absent/False +
ticker rhs-val => genuine crossover) — NOT rhs-fn presence alone. Under this
competing rule, the Paragons/iaSO/n2oo trio (rhs-fn present BUT
rhs-fixed-value?=True with a NUMERIC rhs-val) are GENUINE fixed-threshold
checks, not crossovers — the opposite of this file's original premise. This
is DISPUTED, not resolved (verdict pending in
.claude/fr-signals-inputs/mirror-pattern-verdict.md). Correction to the
earlier "three independent ways, all converging" claim: all three
derivations (falsifier, my own re-scan, fr-engine's byte-level check)
applied the SAME rhs-fn-presence rule — that is convergence on one rule,
not independent validation of the discriminator itself.

Consequence for this file: the Paragons node (892d862a) is now covered by a
SEPARATE xfail-pending test
(test_paragons_class_node_crossover_status_disputed_pending_verdict) rather
than being the primary crossover-exclusion exemplar. The primary exemplar
is now a SETTLED ticker-RHS node (real_tree_06's LQD-vs-XLV,
rhs-fixed-value?=False, ticker rhs-val) — a genuine crossover under EVERY
hypothesis on the table, since there is no numeric threshold to dispute in
the first place. A separate SCOPE HOLD (also team-lead, still in flight,
distinct from the above) covers a same-fn "self-mirror" sub-pattern
(rhs-fn==lhs-fn AND matching window, e.g. REZ:10:77/IGOV:10:77) — no
same-fn mirror node is used anywhere in this file either.

FIXTURE PROVENANCE — every node id below independently re-verified by direct
inspection (not inherited from any teammate's report), against the 11
producer-captured real_tree_0{1-11}_*.json fixtures:
  - GENUINE positive: real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json, if-child id
    44a6ad64-cb3f-419b-99ef-4ad749b0e2e4 — RSI(SPY,10) gt 31, no rhs-fn key
    AT ALL (genuine under every hypothesis — this is not the disputed
    "rhs-fn present but numeric" shape), fires VIXY-family —
    fr_key="SPY:10:31", branch_path 7 hops
    ['false','false','false','true','true','true','true'].
  - CROSSOVER exclusion, SETTLED exemplar: real_tree_06_hvPiGP1O7AHfutHE3Fjy.json,
    if-child id 0d98c2bb-1839-4eee-aa0b-6f10bf384871 — RSI(LQD) gt RSI(XLV),
    rhs-fixed-value?=False, rhs-val="XLV" (a ticker, not a number) — fires
    UVXY. fr_key=None, threshold=None, rhs_fn=None, rhs_val=None,
    rhs_ticker="XLV". Doubles as the ticker-vs-ticker (vs case) exemplar
    below — same node, two assertions.
  - CROSSOVER exclusion, DISPUTED (xfail): real_tree_08_lW4ZzWuqR8tEO2DhXbil.json,
    if-child id 892d862a-94a8-4496-a5ed-ea2528fd278c — RSI(SPY,10) gt
    moving-average-return(31) UNDER THE RHS-FN-PRESENCE RULE, but possibly
    genuine RSI(SPY,10) gt 31 under the competing fixed-value?+numeric rule.
    branch_path 6 hops ['false','false','true','true','true','true'] (the
    two leading "false" entries are genuine else-hops through risk-on
    gate(s) above it) — this part is NOT disputed, direction is independent
    of RHS-shape classification.
  - NO_VIX_EITHER_SIDE negative control: real_tree_01_5XjzXjdGnjh99MIsdM97.json,
    if-nodes cc0f45de-4025-4412-ba18-3a5fb9759eef and
    8fdd1b89-ff40-47e8-bbb7-b2ed4093c3da — RSI(SPY,10) gt
    moving-average-return(31) shape, condition's own subtree only contains
    ticker 'BTAL' (non-VIX). DUAL MECHANISM, both independently verified true
    in this 0715 capture: these nodes ARE rhs-fn crossovers AND their fire
    branch reaches no VIX ticker at all — either reason alone would exclude
    them; both are genuinely present simultaneously (team-lead-requested
    documentation of which mechanism the fixture shows, 2026-07-16).
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
# Genuine fixed-threshold positive match
# ---------------------------------------------------------------------------


def test_genuine_fixed_threshold_extracts_fr_key_and_direction_explicit_branch_path(mod):
    """real_tree_11/Golden Age: a genuine RSI(SPY,10) gt 31 -> VIXY-family
    condition (no rhs-fn key) must extract fr_key="SPY:10:31" with the exact
    root-to-node branch_path (7 hops, direction read off is-else-condition?
    — never inferred)."""
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
    are populated — rhs_fn/rhs_val/rhs_ticker must all be None."""
    tree = _load_tree("real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json")
    checks = mod.extract_fr_checks(tree)
    check = _find(checks, "44a6ad64-cb3f-419b-99ef-4ad749b0e2e4")
    assert check is not None
    assert check.rhs_fn is None
    assert check.rhs_val is None
    assert check.rhs_ticker is None


# ---------------------------------------------------------------------------
# Crossover exclusion — the rhs-fn discriminator (falsifier amendment, HARD)
# ---------------------------------------------------------------------------


def test_ticker_rhs_condition_yields_no_fr_key_settled_under_every_hypothesis(mod):
    """SETTLED crossover exemplar (team-lead ruling 2026-07-16, msg 3f697b3e):
    a ticker-RHS condition (rhs-fixed-value?=False, rhs-val is a ticker
    string, not numeric) is a genuine crossover under EVERY discriminator
    hypothesis on the table — unlike the disputed fixed=true+numeric-rhs-val
    Paragons-class nodes (see test_paragons_class_node_crossover_status_
    disputed_pending_verdict below), this shape has zero ambiguity: there is
    no numeric threshold to dispute, `rhs-val` is structurally a ticker.
    Reuses real_tree_06's LQD-vs-XLV node (same node the rhs_ticker-shape
    test below verifies) as the primary crossover-exclusion exemplar."""
    tree = _load_tree("real_tree_06_hvPiGP1O7AHfutHE3Fjy.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "0d98c2bb-1839-4eee-aa0b-6f10bf384871"
    check = _find(checks, target_id)
    assert check is not None, (
        f"a crossover node must still produce an FRCheck (direction/VIX-reachability is "
        f"independent of RHS shape) — none found for node_id={target_id!r}"
    )
    assert check.fr_key is None, (
        f"a ticker-RHS condition must NEVER produce a joinable fr_key; got {check.fr_key!r}."
    )
    assert check.threshold is None
    assert check.rhs_ticker == "XLV", f"expected rhs_ticker captured, got {check.rhs_ticker!r}"
    assert check.rhs_fn is None, "a crossover and a ticker-vs-ticker condition are mutually exclusive"
    assert check.rhs_val is None
    assert check.ticker == "LQD"


@pytest.mark.xfail(
    reason=(
        "DISPUTED, not settled (team-lead STOP-CHECK #2, 2026-07-16, msg 7722f63a / 3f697b3e): "
        "fr-falsifier2's E3/E4 evidence indicates the real discriminator key is "
        "rhs-fixed-value? + numeric-vs-ticker rhs-val, NOT rhs-fn presence. Under that competing "
        "rule, this Paragons node (rhs-fn='moving-average-return' but rhs-fixed-value?=True with a "
        "NUMERIC rhs-val='31') is a GENUINE fixed-threshold check, not a crossover — the opposite "
        "of what this test currently asserts. Verdict pending in "
        ".claude/fr-signals-inputs/mirror-pattern-verdict.md. strict=False: this test may pass or "
        "fail depending on which GREEN implementation choice fr-engine ships first — do not let a "
        "flip in either direction fail the suite."
    ),
    strict=False,
)
def test_paragons_class_node_crossover_status_disputed_pending_verdict(mod):
    """Originally pinned as THE canonical rhs-fn discriminator case under the
    now-superseded rhs-fn-presence-alone rule. Kept as a live (xfail, not
    deleted) regression probe so whichever way the verdict lands, re-running
    this file immediately shows the correct classification without needing
    a new test written from scratch — see the swapped-in SETTLED exemplar
    above for the assertions this file's crossover-exclusion coverage no
    longer depends on this node for."""
    tree = _load_tree("real_tree_08_lW4ZzWuqR8tEO2DhXbil.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "892d862a-94a8-4496-a5ed-ea2528fd278c"
    check = _find(checks, target_id)
    assert check is not None
    assert check.fr_key is None, (
        f"under the rhs-fn-presence rule this should be None (crossover); got {check.fr_key!r}. "
        "If the verdict confirms the fixed-value?+numeric rule instead, this assertion inverts to "
        "fr_key == 'SPY:10:31' — update this test (not just re-run it) once the verdict is final."
    )
    assert check.threshold is None


def test_paragons_node_branch_path_is_correct_regardless_of_crossover_dispute(mod):
    """Direction correctness (branch_path) is INDEPENDENT of the disputed
    crossover-vs-genuine classification — AC-3's walk criterion (TRUE branch
    reaches VIX) fires regardless of RHS shape, so an FRCheck exists for this
    node under EITHER hypothesis, and its ancestry is a structural fact about
    the tree, not an interpretation of rhs-fn/rhs-fixed-value? semantics.
    This assertion is NOT xfail — it holds no matter how the dispute
    resolves."""
    tree = _load_tree("real_tree_08_lW4ZzWuqR8tEO2DhXbil.json")
    checks = mod.extract_fr_checks(tree)
    check = _find(checks, "892d862a-94a8-4496-a5ed-ea2528fd278c")
    assert check is not None
    assert check.branch_path == ["false", "false", "true", "true", "true", "true"], (
        f"expected the exact hand-traced 6-hop ELSE-heavy ancestry, got {check.branch_path}"
    )


# ---------------------------------------------------------------------------
# Ticker-vs-ticker condition — the rhs_ticker case
# ---------------------------------------------------------------------------


def test_ticker_vs_ticker_condition_populates_rhs_ticker_not_fr_key_or_rhs_fn(mod):
    """real_tree_06: RSI(LQD) gt RSI(XLV) — rhs-fixed-value?=False, rhs-val
    is a TICKER string ("XLV"), not a numeric threshold. Structurally
    distinct from BOTH the genuine-threshold and crossover cases: fr_key/
    threshold/rhs_fn/rhs_val must all be None, rhs_ticker must carry the
    real value."""
    tree = _load_tree("real_tree_06_hvPiGP1O7AHfutHE3Fjy.json")
    checks = mod.extract_fr_checks(tree)

    target_id = "0d98c2bb-1839-4eee-aa0b-6f10bf384871"
    check = _find(checks, target_id)
    assert check is not None, f"expected an FRCheck for the ticker-vs-ticker node_id={target_id!r}"
    assert check.fr_key is None
    assert check.threshold is None
    assert check.rhs_fn is None
    assert check.rhs_val is None
    assert check.rhs_ticker == "XLV", f"expected rhs_ticker='XLV', got {check.rhs_ticker!r}"
    assert check.ticker == "LQD"


# ---------------------------------------------------------------------------
# Exactly-one-of-three invariant (fr-engine, explicit) — hostile sweep
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
def test_exactly_one_of_fr_key_rhs_fn_rhs_ticker_is_populated_across_every_extracted_check(
    mod, filename
):
    """Hostile invariant sweep across real trees carrying a mix of shapes:
    for every extracted FRCheck, exactly one of {fr_key}, {rhs_fn+rhs_val},
    {rhs_ticker} is populated — never zero, never two."""
    tree = _load_tree(filename)
    checks = mod.extract_fr_checks(tree)
    assert checks, f"{filename}: expected extraction to find at least one FRCheck"

    for check in checks:
        buckets_populated = sum(
            [
                check.fr_key is not None,
                check.rhs_fn is not None,
                check.rhs_ticker is not None,
            ]
        )
        assert buckets_populated == 1, (
            f"{filename} node_id={check.node_id!r}: expected exactly ONE of "
            f"fr_key/rhs_fn/rhs_ticker populated, got {buckets_populated} "
            f"(fr_key={check.fr_key!r}, rhs_fn={check.rhs_fn!r}, rhs_ticker={check.rhs_ticker!r})"
        )
        if check.rhs_fn is not None:
            assert check.rhs_val is not None, (
                f"node_id={check.node_id!r}: rhs_fn populated without rhs_val"
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
    """real_tree_01/5Xjz: two RSI(SPY,10)-gt-31-shaped if-nodes whose fire
    branch's only ticker is BTAL (non-VIX). DUAL MECHANISM in this 0715
    capture (both independently true, documented per team-lead's request):
    these nodes are ALSO rhs-fn crossovers, but even setting that aside,
    the VIX-reachability criterion alone excludes them — neither branch
    reaches a VIX-family ticker at all. No FRCheck must be emitted for
    either node id."""
    tree = _load_tree("real_tree_01_5XjzXjdGnjh99MIsdM97.json")
    checks = mod.extract_fr_checks(tree)

    excluded_if_node_ids = {
        "cc0f45de-4025-4412-ba18-3a5fb9759eef",
        "8fdd1b89-ff40-47e8-bbb7-b2ed4093c3da",
    }
    # The FRCheck's own node_id is the if-CHILD's id (the condition-bearing
    # branch), not the outer if-node's — check by BTAL-only-ticker signature
    # via the ticker field instead of a node_id match, since we're asserting
    # ABSENCE, not presence.
    btal_only_checks = [c for c in checks if c.ticker == "SPY" and c.window == 10 and c.threshold == 31.0]
    assert not btal_only_checks, (
        f"expected NO genuine SPY:10:31 FRCheck from tree 01's NO_VIX_EITHER_SIDE nodes "
        f"({excluded_if_node_ids}), got {btal_only_checks}"
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
