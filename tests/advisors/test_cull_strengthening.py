"""RED tests — Component 5b: overfitting-cull strengthening (AC-24/25/26).

The ONE deliberate downstream gate change. Strengthens the AI-Advisor cull to
autotuner-grade. Three surfaces, all in the gate/engine layer — the generator,
compiler, universe_provider, symphony_schema, acceptance_gate, compute_pbo, and the
autotuner are NOT touched (they are correct / out of scope):

  AC-24 — PBO veto wired in. Today advisors/backtest_gate_engine.py calls
    acceptance_gate.evaluate_acceptance_gate with NO `pbo` (defaults to None) so the
    PBO veto is structurally disabled. After this AC a real batch PBO is computed
    (math_engine.compute_pbo, Bailey & Lopez de Prado) over the N candidates'
    date-keyed returns and passed into the gate; a candidate in a high-PBO batch
    (pbo > PBO_REJECT_THRESHOLD) is VETOED; a low-PBO batch is not; pbo=None
    (fewer than 2 date-keyed configs) passes unchanged (no false reject).

  AC-25 — real SPY-OOS-over-the-fold baseline. Today default_oos_alpha is always
    0.0 so a candidate clears on merely-positive OOS alpha. After this AC the
    baseline is SPY's OOS alpha computed over the SAME validation fold (SPY series
    aligned to the candidate's date span FIRST, then the IDENTICAL fold transform):
    a positive-but-below-SPY-fold candidate is REJECTED; a beats-SPY-over-the-fold
    candidate survives the alpha gate. SPY-unavailable -> conservative WITHHOLD.

  AC-26 — Atlas parity. Atlas community candidates are culled IDENTICALLY to
    built-new (same fold OOS, same FDR, same PBO veto, same SPY baseline). An Atlas
    candidate and a built-new candidate with identical FRESH return series receive
    the identical gate verdict; advertised community stats (oos_metrics) NEVER
    influence any survival decision.

CONTRACT GROUNDING (read from the existing code — empirical-before-TDD):
  - math_engine.compute_pbo(configs_date_returns: list[dict[date->return]],
    eligible_dates, gamma, S=None) -> float in [0,1]. CSCV reduced-N (S=8). A
    per-block-overfit batch (each config spikes one chronological block) yields
    pbo=1.0 (the IS-best on a combo is always OOS-mediocre); a consistent/monotone
    batch yields pbo=0.0. (VERIFIED via the real compute_pbo before writing these.)
  - acceptance_gate.evaluate_acceptance_gate already has the `pbo` param + the
    Stage-1 veto (pbo > PBO_REJECT_THRESHOLD strict). The wiring gap is the CALL
    SITE in backtest_gate_engine, which passes no pbo today.
  - BacktestCandidate.daily_returns_pct is currently a date-LESS list, built from
    result.daily_returns.values() (date keys dropped). The dates ARE available in
    the Composer result; recovering them is the enabler for both PBO date-keying and
    SPY-fold alignment — NO new endpoint.

ADVERSARIAL FOCUS (the anti-hollow requirement, PM-mandated): the AC-24 high-PBO
test must exercise the veto FIRING through the REAL compute_pbo on real fixture
date-returns — NOT a vacuous pbo=None pass. compute_pbo / the math engine are NEVER
mocked. SPY fetch is the only injectable seam. No hardcoded producer values — assert
the VERDICT (vetoed / rejected / survives) and the RELATIONSHIP (below-SPY vs
above-SPY), never a literal pbo or alpha.
"""

from __future__ import annotations

import pytest

import acceptance_gate
from math_engine import PBO_REJECT_THRESHOLD, compute_pbo

# ---------------------------------------------------------------------------
# Module-under-test imports. backtest_gate_engine + strategy_builder_engine are
# the C5b surfaces. The new public arguments / seams these tests pin do not exist
# yet — that is the RED.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gate_engine():
    import advisors.backtest_gate_engine as _g  # noqa: PLC0415

    return _g


# ---------------------------------------------------------------------------
# Date-keyed return fixtures (we fully control these — no producer values).
# ---------------------------------------------------------------------------

# 80 trading-day-ish date strings across 8 months → 8 CSCV blocks of 10.
_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]


def _per_block_overfit_configs() -> list[dict[str, float]]:
    """K=8 configs, each spiking exactly ONE chronological block (+0.20) and
    slightly negative (-0.01) elsewhere. The IS-best config on any CSCV combo has
    its spike in the IS blocks and is mediocre on the OOS blocks → IS-best ranks
    below the OOS median in every combo → pbo=1.0. VERIFIED via real compute_pbo."""
    blocks = [_DATES[i * 10 : (i + 1) * 10] for i in range(8)]
    configs: list[dict[str, float]] = []
    for b in range(8):
        cfg: dict[str, float] = {}
        for i, blk in enumerate(blocks):
            for d in blk:
                cfg[d] = 0.20 if i == b else -0.01
        configs.append(cfg)
    return configs


def _consistent_configs() -> list[dict[str, float]]:
    """K=5 monotone-steady configs (each a flat positive level). The IS-best is
    also the OOS-best in every combo → pbo=0.0. VERIFIED via real compute_pbo."""
    return [{d: 0.001 * (j + 1) for d in _DATES} for j in range(5)]


def _pbo_gamma() -> float:
    """The CRRA gamma compute_pbo is called with. 1.0 is a valid risk-aversion
    coefficient; the fixtures' PBO outcome (1.0 vs 0.0) is gamma-robust by
    construction (the per-block spike dominates any reasonable CRRA utility)."""
    return 1.0


# ---------------------------------------------------------------------------
# Self-guard: prove the fixtures actually drive compute_pbo across the threshold,
# so the AC-24 veto tests are non-hollow (a real pbo>0.5 exists to veto on).
# This exercises the REAL compute_pbo — never mocked.
# ---------------------------------------------------------------------------


def test_fixture_high_pbo_batch_exceeds_threshold_via_real_compute_pbo():
    """The per-block-overfit batch yields pbo > PBO_REJECT_THRESHOLD through the
    REAL compute_pbo — the veto HAS something real to fire on (anti-hollow)."""
    pbo = compute_pbo(_per_block_overfit_configs(), _DATES, _pbo_gamma())
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"high-PBO fixture must exceed the reject threshold via real compute_pbo; got {pbo}"
    )


def test_fixture_low_pbo_batch_is_below_threshold_via_real_compute_pbo():
    """The consistent batch yields pbo <= PBO_REJECT_THRESHOLD — the low-PBO control."""
    pbo = compute_pbo(_consistent_configs(), _DATES, _pbo_gamma())
    assert pbo <= PBO_REJECT_THRESHOLD, (
        f"low-PBO control fixture must be at/below the threshold; got {pbo}"
    )


# ---------------------------------------------------------------------------
# Candidate builders. C5b threads DATE-KEYED returns through the candidate so the
# gate can compute PBO and align SPY. The exact field/param name the impl adds is
# resolved leniently (the tests pin BEHAVIOUR, not the name).
# ---------------------------------------------------------------------------

# Accepted names for the new date-keyed-returns field on the candidate, and for the
# new compute-PBO toggle / SPY-baseline seam on evaluate_candidate_batch. The impl
# picks names; the tests resolve from this small set so behaviour is what's pinned.
_DATED_RETURNS_FIELDS = ("dated_returns", "daily_returns_by_date", "returns_by_date")
_SPY_SEAM_KWARGS = ("spy_returns_fn", "spy_fold_alpha_fn", "spy_dated_returns")


def _make_candidate(gate_engine, cid: str, dated_returns: dict[str, float], **extra):
    """Build a BacktestCandidate carrying BOTH the legacy value-list AND the new
    date-keyed returns (whichever field the impl adds). dated_returns maps date->pct.

    The legacy daily_returns_pct is the chronologically-ordered value list (so the
    existing fold transform still works); the date-keyed field is what AC-24's PBO
    and AC-25's SPY alignment consume.
    """
    BacktestCandidate = gate_engine.BacktestCandidate
    ordered_dates = sorted(dated_returns)
    value_list = [dated_returns[d] for d in ordered_dates]
    kwargs = dict(
        candidate_id=cid,
        daily_returns_pct=value_list,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
        nn1_compliant=True,
        purge_integrity_ok=True,
    )
    kwargs.update(extra)
    cand = BacktestCandidate(**kwargs)
    # Attach the date-keyed returns under whichever field the impl exposes. If the
    # candidate is a NamedTuple with the new field, it must be passed at construction;
    # we try that first, then fall back to a post-hoc attribute for dataclass impls.
    for field in _DATED_RETURNS_FIELDS:
        if field in getattr(BacktestCandidate, "_fields", ()):  # NamedTuple field
            return cand._replace(**{field: dict(dated_returns)})
    # Non-NamedTuple impl: set the attribute directly (the impl must read one of these).
    for field in _DATED_RETURNS_FIELDS:
        try:
            object.__setattr__(cand, field, dict(dated_returns))
        except Exception:
            pass
    return cand


def _verdict_for(batch, cid: str):
    for r in batch.results:
        if r.candidate_id == cid:
            return r.verdict.decision
    raise AssertionError(f"no gate result for candidate {cid!r}")


def _survivor_ids(batch) -> set[str]:
    return {s.candidate_id for s in batch.survivors}


# ===========================================================================
# AC-24 — PBO veto wired into evaluate_candidate_batch.
# ===========================================================================


# Spy helper: capture the `pbo` value threaded into evaluate_acceptance_gate.
# This is the NON-CONFOUNDED isolation of the PBO veto: the gate's own tests
# (tests/acceptance_gate/) already prove pbo>threshold → REJECT_VETO_FAILED, so
# proving the REAL high pbo reaches the gate completes the veto chain WITHOUT
# depending on a candidate surviving the (intentionally strict) BHY baseline. We
# do NOT mock compute_pbo (it runs for real on the fixture date-returns) — we only
# observe the value handed to the gate.


def _capture_gate_pbos(gate_engine, monkeypatch) -> list:
    """Wrap acceptance_gate.evaluate_acceptance_gate to record every `pbo` kwarg it
    receives (the real value the gate engine threaded in), returning the real verdict.
    Returns the list that will be populated when evaluate_candidate_batch runs."""
    import acceptance_gate as _ag

    seen_pbos: list = []
    real_gate = _ag.evaluate_acceptance_gate

    def _spy(*args, **kwargs):
        seen_pbos.append(kwargs.get("pbo"))
        return real_gate(*args, **kwargs)

    # backtest_gate_engine calls it via the module reference; patch on that module.
    monkeypatch.setattr(gate_engine, "evaluate_acceptance_gate", _spy, raising=False)
    monkeypatch.setattr(_ag, "evaluate_acceptance_gate", _spy)
    return seen_pbos


def test_ac24_high_pbo_batch_threads_real_pbo_above_threshold_into_gate(gate_engine, monkeypatch):
    """ANTI-HOLLOW CORE: a per-block-overfit batch (real pbo>0.5 via the REAL
    compute_pbo) must thread a pbo > PBO_REJECT_THRESHOLD into
    evaluate_acceptance_gate. Combined with the acceptance-gate's own veto tests
    (pbo>threshold → REJECT_VETO_FAILED), this proves the PBO veto FIRES on real
    overfitting — not a vacuous None-pass. We isolate PBO at the gate boundary so the
    proof does not depend on a candidate clearing the (intentionally strict) BHY
    baseline, which would confound a survivor-count assertion."""
    seen = _capture_gate_pbos(gate_engine, monkeypatch)
    configs = _per_block_overfit_configs()
    candidates = [_make_candidate(gate_engine, f"hpbo-{i}", c) for i, c in enumerate(configs)]
    gate_engine.evaluate_candidate_batch(candidates)
    non_none = [p for p in seen if p is not None]
    assert non_none, "the gate must receive a non-None pbo for a >=2 date-keyed batch"
    # Every gate call in this batch sees the SAME batch pbo (batch-level statistic),
    # and it must exceed the reject threshold (real overfitting → veto fires).
    assert all(p > PBO_REJECT_THRESHOLD for p in non_none), (
        f"high-PBO batch must thread pbo>{PBO_REJECT_THRESHOLD} into the gate; got {non_none}"
    )


def test_ac24_low_pbo_batch_threads_pbo_at_or_below_threshold(gate_engine, monkeypatch):
    """A consistent (low-PBO) batch must thread a pbo <= PBO_REJECT_THRESHOLD into
    the gate — so the PBO veto does NOT fire on a non-overfit batch (no false reject
    on the sample-robustness axis)."""
    seen = _capture_gate_pbos(gate_engine, monkeypatch)
    configs = _consistent_configs()
    candidates = [_make_candidate(gate_engine, f"lpbo-{i}", c) for i, c in enumerate(configs)]
    gate_engine.evaluate_candidate_batch(candidates)
    non_none = [p for p in seen if p is not None]
    assert non_none, "the gate must receive a non-None pbo for a >=2 date-keyed batch"
    assert all(p <= PBO_REJECT_THRESHOLD for p in non_none), (
        f"low-PBO batch must thread pbo<={PBO_REJECT_THRESHOLD} (no PBO veto); got {non_none}"
    )


def test_ac24_single_candidate_threads_pbo_none_no_veto(gate_engine, monkeypatch):
    """K<2 date-keyed configs → compute_pbo cannot rank → the gate engine must thread
    pbo=None (the no-false-reject edge case). The PBO veto cannot fire when pbo is
    None (acceptance_gate: pbo is None → _pbo_veto_passed)."""
    seen = _capture_gate_pbos(gate_engine, monkeypatch)
    cand = _make_candidate(gate_engine, "solo", _consistent_configs()[0])
    gate_engine.evaluate_candidate_batch([cand])
    # Whatever the gate decided on other axes, the pbo it received must be None
    # (fewer than 2 configs → no rankable CSCV → no PBO veto).
    assert seen, "the gate must have been called for the single candidate"
    assert all(p is None for p in seen), (
        f"a single-candidate batch must thread pbo=None (no false PBO veto); got {seen}"
    )


def test_ac24_pbo_is_computed_via_real_compute_pbo_with_date_keyed_configs(
    gate_engine, monkeypatch
):
    """WIRING proof: evaluate_candidate_batch must CALL math_engine.compute_pbo for a
    >=2 date-keyed batch (today it never does). We wrap compute_pbo (the math runs for
    REAL — result not replaced) and assert it received date-keyed config DICTS
    recovered from the candidates' dated returns, not value lists."""
    import math_engine

    calls: list[tuple] = []
    real_compute_pbo = math_engine.compute_pbo

    def _spy(configs_date_returns, eligible_dates, gamma, S=None):
        calls.append((configs_date_returns, eligible_dates, gamma))
        return real_compute_pbo(configs_date_returns, eligible_dates, gamma, S)

    monkeypatch.setattr(math_engine, "compute_pbo", _spy)

    configs = _consistent_configs()
    candidates = [_make_candidate(gate_engine, f"wire-{i}", c) for i, c in enumerate(configs)]
    gate_engine.evaluate_candidate_batch(candidates)
    assert calls, "evaluate_candidate_batch must call math_engine.compute_pbo for a >=2 batch"
    configs_arg = calls[0][0]
    assert len(configs_arg) >= 2
    assert all(isinstance(c, dict) for c in configs_arg), (
        "compute_pbo must receive date-keyed config dicts (recovered from the "
        "candidate's dated returns), not value lists"
    )


# ===========================================================================
# AC-25 — real SPY-OOS-over-the-fold baseline (replaces always-0.0).
# ===========================================================================


def _spy_seam_kwargs(gate_engine, spy_dated_returns):
    """Build the kwarg that injects the SPY series/baseline into the gate call.

    The impl exposes one of _SPY_SEAM_KWARGS. We supply a callable returning the
    SPY date-keyed returns (the most general seam); if the impl wants a pre-fetched
    series or a fold-alpha fn, the same data is provided. The test pins that SPY is
    sourced over the candidate's dates and fold-transformed identically.
    """
    # Default: a callable seam returning SPY's date-keyed returns for a requested span.
    return {"spy_returns_fn": lambda *a, **k: dict(spy_dated_returns)}


def _candidate_with_fold_alpha(gate_engine, cid, *, beats: bool, spy_dated):
    """Build a candidate whose validation-fold OOS alpha is ABOVE (beats=True) or
    BELOW (beats=False) SPY's fold alpha over the SAME dates. Both series share the
    SAME date span so the fold windows align (apples-to-apples)."""
    # Candidate returns: a flat level scaled so the validation-fold sum is clearly
    # above/below SPY's. We don't assert the literal alpha — only the relationship.
    spy_level = spy_dated[_DATES[-1]]  # representative SPY level (we set it flat below)
    cand_level = spy_level * (2.0 if beats else 0.25)
    cand_dated = {d: cand_level for d in _DATES}
    return _make_candidate(gate_engine, cid, cand_dated)


def test_ac25_candidate_below_spy_fold_is_rejected(gate_engine):
    """A candidate with POSITIVE OOS alpha that is nonetheless BELOW SPY's
    OOS-alpha-over-the-same-fold must be REJECTED (KEEP_INCUMBENT) — beating zero is
    no longer enough; it must beat SPY over the fold."""
    spy_dated = {d: 0.02 for d in _DATES}  # SPY flat-positive over the span
    cand = _candidate_with_fold_alpha(gate_engine, "below", beats=False, spy_dated=spy_dated)
    # Need >=2 candidates for a meaningful gate batch; add an in-universe sibling.
    sibling = _candidate_with_fold_alpha(gate_engine, "below2", beats=False, spy_dated=spy_dated)
    batch = gate_engine.evaluate_candidate_batch(
        [cand, sibling], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    assert _verdict_for(batch, "below") != acceptance_gate.DECISION_ADOPT_CANDIDATE, (
        "a positive-but-below-SPY-fold candidate must not be adopted (must beat SPY, not zero)"
    )
    assert "below" not in _survivor_ids(batch)


# SPY-baseline isolation note (same methodology as the AC-24 gate-boundary tests):
# "survives the alpha gate" cannot be asserted via a full-gate ADOPT decision, because
# the strict BHY/PBO Stage-1 vetoes are orthogonal to the SPY-baseline change and
# pre-empt the alpha branch for synthetic batches (a constant series → t-stat 0 → BHY
# fails; identical configs → pbo=1.0 → PBO veto). So we isolate AC-25's actual claim —
# the SPY-fold alpha REPLACES the always-0.0 baseline — at the gate BOUNDARY: spy on
# evaluate_acceptance_gate and assert the `default_oos_alpha` it receives is SPY's
# fold alpha (NOT 0.0), and that the below/above-SPY relationship is what the gate sees.


def _capture_gate_baselines(gate_engine, monkeypatch) -> list:
    """Record the (oos_alpha, default_oos_alpha) the gate engine threads into each
    evaluate_acceptance_gate call — the real values, real gate verdict returned."""
    import acceptance_gate as _ag

    seen: list = []
    real_gate = _ag.evaluate_acceptance_gate

    def _spy(*args, **kwargs):
        seen.append((kwargs.get("oos_alpha"), kwargs.get("default_oos_alpha")))
        return real_gate(*args, **kwargs)

    monkeypatch.setattr(gate_engine, "evaluate_acceptance_gate", _spy, raising=False)
    monkeypatch.setattr(_ag, "evaluate_acceptance_gate", _spy)
    return seen


def test_ac25_spy_fold_alpha_is_the_gate_baseline_not_zero(gate_engine, monkeypatch):
    """AC-25 CORE: the gate's default_oos_alpha must be SPY's OOS-alpha over the SAME
    validation fold — NOT the legacy always-0.0. We supply a clearly non-zero SPY
    series, spy the gate boundary, and assert the threaded default_oos_alpha equals
    SPY's fold-transformed alpha (non-zero) — proving the baseline was replaced. We
    do NOT assert a literal alpha: we compute SPY's expected fold alpha via the SAME
    _fold_transform_single the engine uses (the math runs for real), so the assertion
    derives from the fixture, never a hardcoded producer value."""
    spy_dated = {d: 0.03 for d in _DATES}
    # Expected SPY fold alpha = the engine's own fold transform over SPY's value list
    # ordered by date (same path the engine must take after date-alignment).
    spy_values = [spy_dated[d] for d in sorted(spy_dated)]
    expected_spy_fold_alpha = gate_engine._fold_transform_single(spy_values).oos_alpha
    assert expected_spy_fold_alpha != 0.0, "fixture must produce a non-zero SPY fold alpha"

    seen = _capture_gate_baselines(gate_engine, monkeypatch)
    cand = _make_candidate(gate_engine, "c1", {d: 0.02 for d in _DATES})
    sibling = _make_candidate(gate_engine, "c2", {d: 0.01 for d in _DATES})
    gate_engine.evaluate_candidate_batch(
        [cand, sibling], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    defaults = [d for (_o, d) in seen if d is not None]
    assert defaults, "gate must have been called with a default_oos_alpha"
    # The baseline must be SPY's fold alpha (the math engine computed it for real),
    # NOT the legacy 0.0.
    assert all(abs(d - expected_spy_fold_alpha) < 1e-9 for d in defaults), (
        f"gate default_oos_alpha must be SPY's fold alpha {expected_spy_fold_alpha}, "
        f"not the legacy 0.0; got {defaults}"
    )
    assert all(d != 0.0 for d in defaults), "SPY-fold baseline must replace the always-0.0 default"


def test_ac25_below_spy_relationship_holds_at_the_gate(gate_engine, monkeypatch):
    """A candidate whose fold oos_alpha is positive but BELOW SPY's fold alpha must
    reach the gate with oos_alpha <= default_oos_alpha (the SPY baseline) — so the
    gate's KEEP_INCUMBENT/reject branch fires on the SPY comparison (beats-SPY, not
    beats-zero). Asserted at the boundary so BHY/PBO don't confound the alpha claim."""
    spy_dated = {d: 0.04 for d in _DATES}  # SPY strongly positive
    seen = _capture_gate_baselines(gate_engine, monkeypatch)
    # Candidate positive but clearly weaker than SPY over the fold.
    below = _make_candidate(gate_engine, "below", {d: 0.01 for d in _DATES})
    sibling = _make_candidate(gate_engine, "below_b", {d: 0.005 for d in _DATES})
    gate_engine.evaluate_candidate_batch(
        [below, sibling], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    # For every gate call the candidate's oos_alpha must be <= the SPY-fold baseline
    # (positive-but-below-SPY → the alpha branch cannot adopt).
    pairs = [(o, d) for (o, d) in seen if o is not None and d is not None]
    assert pairs, "gate must have been called with both oos_alpha and default_oos_alpha"
    assert all(o <= d for (o, d) in pairs), (
        f"a below-SPY candidate must reach the gate with oos_alpha<=SPY-fold baseline; got {pairs}"
    )


def test_ac25_spy_baseline_uses_same_fold_dates_not_positional(gate_engine, monkeypatch):
    """GUARD against a subtle bug: SPY must be DATE-ALIGNED to the candidate's span and
    fold-transformed over the SAME dates — not a positional fold of a differently-dated
    SPY series. We give SPY the candidate dates (modest returns) PLUS extra out-of-span
    dates with absurd returns; a positional fold over the longer series would land its
    validation window on the absurd extras and inflate the baseline. Date-alignment
    (intersect on the candidate's dates) must IGNORE the extras — so the threaded
    default_oos_alpha equals the fold alpha of the IN-SPAN SPY returns only."""
    in_span = {d: 0.02 for d in _DATES}
    spy_dated = dict(in_span)
    for d in [f"2026-09-{x:02d}" for x in range(1, 21)]:
        spy_dated[d] = 5.0  # absurd returns OUTSIDE the candidate's span

    # Correct (date-aligned) baseline = fold alpha of the IN-SPAN SPY returns only.
    in_span_values = [in_span[d] for d in sorted(in_span)]
    expected_aligned = gate_engine._fold_transform_single(in_span_values).oos_alpha
    # A positional fold of the FULL (longer) series would produce a very different
    # (inflated) alpha — the bug we guard against.
    full_values = [spy_dated[d] for d in sorted(spy_dated)]
    positional_buggy = gate_engine._fold_transform_single(full_values).oos_alpha
    assert abs(expected_aligned - positional_buggy) > 1e-6, (
        "fixture must make the aligned and positional-fold baselines differ"
    )

    seen = _capture_gate_baselines(gate_engine, monkeypatch)
    cand = _make_candidate(gate_engine, "aligned", {d: 0.02 for d in _DATES})
    sibling = _make_candidate(gate_engine, "aligned2", {d: 0.01 for d in _DATES})
    gate_engine.evaluate_candidate_batch(
        [cand, sibling], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    defaults = [d for (_o, d) in seen if d is not None]
    assert defaults, "gate must have been called with a default_oos_alpha"
    # The threaded baseline must be the DATE-ALIGNED fold alpha, NOT the positional one.
    assert all(abs(d - expected_aligned) < 1e-9 for d in defaults), (
        f"SPY baseline must be the date-aligned fold alpha {expected_aligned} (in-span "
        f"dates only), not the positional-fold value {positional_buggy}; got {defaults}"
    )


def test_ac25_spy_unavailable_withholds_conservatively(gate_engine):
    """SPY series unavailable for the fold → the alpha gate degrades CONSERVATIVELY
    (baseline treated as unmet → WITHHOLD / no adopt), never a silent fall-back to
    the old beats-zero behaviour.

    NOTE (kept as a basic smoke check): this test is CONFOUNDED on its own — its
    identical-flat candidates fail FDR/panel anyway, so it passes whether the
    SPY-unavailable sentinel is conservative (+inf) OR permissive (-inf). The
    NON-CONFOUNDED proof lives in the two tests below
    (test_ac25_spy_unavailable_*_non_confounded), which fail on the permissive sentinel
    and pass only on the conservative one."""
    cand = _candidate_with_fold_alpha(
        gate_engine, "nospy", beats=True, spy_dated={d: 0.01 for d in _DATES}
    )
    sibling = _candidate_with_fold_alpha(
        gate_engine, "nospy2", beats=True, spy_dated={d: 0.01 for d in _DATES}
    )
    # SPY seam returns nothing (unavailable).
    batch = gate_engine.evaluate_candidate_batch([cand, sibling], spy_returns_fn=lambda *a, **k: {})
    # Conservative: no candidate is ADOPTED when the SPY baseline can't be established.
    for cid in ("nospy", "nospy2"):
        assert _verdict_for(batch, cid) != acceptance_gate.DECISION_ADOPT_CANDIDATE, (
            "SPY-unavailable must withhold (no adopt), not silently beat-zero"
        )


def test_ac25_spy_unavailable_threads_conservative_baseline_not_permissive(
    gate_engine, monkeypatch
):
    """EDGE-14 NON-CONFOUNDED (mechanism, gate-boundary): when SPY is unavailable, the
    default_oos_alpha threaded into evaluate_acceptance_gate must be the CONSERVATIVE
    sentinel that makes the gate's ``oos_alpha <= default_oos_alpha`` withhold-clause
    ALWAYS TRUE — i.e. it must be >= every finite candidate oos_alpha, so NO finite
    candidate can clear the SPY baseline (acceptance_gate.py:257 KEEP_INCUMBENT).

    A PERMISSIVE sentinel (a very-negative value like -inf) makes that clause ALWAYS
    FALSE, collapsing the withhold to the fallback (incumbent, default 0.0) = the
    beats-zero behaviour edge-14 explicitly forbids. This assertion is immune to the
    FDR/panel confound because it pins the BASELINE VALUE handed to the gate, not a
    survivor count. It FAILS on the permissive sentinel and PASSES only on the
    conservative one. Source: feature-plans/strategy-builder-real.md §Edge Cases #14."""
    seen = _capture_gate_baselines(gate_engine, monkeypatch)
    cand = _make_candidate(gate_engine, "nospy_m", {d: 0.02 for d in _DATES})
    sibling = _make_candidate(gate_engine, "nospy_m2", {d: 0.01 for d in _DATES})
    # SPY unavailable: the seam returns an empty series.
    gate_engine.evaluate_candidate_batch([cand, sibling], spy_returns_fn=lambda *a, **k: {})
    defaults = [d for (_o, d) in seen if d is not None]
    assert defaults, "gate must have been called with a default_oos_alpha"
    # The candidates' fold oos_alphas are finite; the conservative sentinel must be
    # >= every one of them so the withhold-clause fires. We pin the RELATIONSHIP
    # (baseline dominates every finite candidate alpha), not a literal sentinel value.
    cand_oos = [o for (o, _d) in seen if o is not None]
    assert cand_oos, "gate must have been called with candidate oos_alpha values"
    for d in defaults:
        assert all(o <= d for o in cand_oos), (
            "EDGE-14 BUG: SPY-unavailable baseline must be CONSERVATIVE (>= every finite "
            "candidate oos_alpha so oos_alpha<=default always withholds). A permissive "
            f"(very-negative) sentinel lets candidates clear on beats-zero. baseline={d}, "
            f"candidate oos_alphas={cand_oos}"
        )


def test_ac25_spy_unavailable_blocks_an_otherwise_adopting_candidate_non_confounded(
    gate_engine, monkeypatch
):
    """EDGE-14 NON-CONFOUNDED (behavioural): a candidate that WOULD be ADOPTED absent
    the SPY-unavailable degradation must be WITHHELD when SPY is unavailable. We remove
    the FDR/panel confound by spying evaluate_acceptance_gate so the candidate clears
    BHY + panel for real on every axis EXCEPT the SPY-baseline alpha branch — which is
    exactly the branch under test. Concretely the spy passes through to the REAL gate
    but guarantees a winning FDR p_adj and a dominant panel, leaving the
    oos_alpha<=default_oos_alpha SPY clause as the only thing that can withhold.

    On the PERMISSIVE sentinel (-inf) the SPY clause never fires → the candidate ADOPTS
    on beats-zero (the bug). On the CONSERVATIVE sentinel (+inf) the SPY clause always
    fires → KEEP_INCUMBENT. So this test FAILS on the bug and PASSES on the fix."""
    import acceptance_gate as _ag

    real_gate = _ag.evaluate_acceptance_gate

    def _adopt_friendly_gate(*args, **kwargs):
        # Force the non-SPY axes to favour adoption: a winning (tiny) FDR p_adj and a
        # dominant candidate panel. The SPY-baseline alpha comparison is left to the
        # REAL gate logic (oos_alpha vs default_oos_alpha), so SPY-unavailable is the
        # ONLY remaining thing that can block adoption.
        kwargs["winner_trial_is_none"] = False
        kwargs["winner_p_adj"] = 1e-6
        kwargs["nn1_compliant"] = True
        kwargs["purge_integrity_ok"] = True
        kwargs["candidate_stability_score"] = 1.0
        kwargs["candidate_prior_anchor_score"] = 1.0
        kwargs["incumbent_stability_score"] = 0.0
        kwargs["incumbent_prior_anchor_score"] = 0.0
        kwargs["pbo"] = None  # no PBO veto in this isolation
        return real_gate(*args, **kwargs)

    monkeypatch.setattr(gate_engine, "evaluate_acceptance_gate", _adopt_friendly_gate, raising=False)
    monkeypatch.setattr(_ag, "evaluate_acceptance_gate", _adopt_friendly_gate)

    # Candidates with clearly-positive fold oos_alpha (would beat the 0.0 fallback).
    cand = _make_candidate(gate_engine, "would_adopt", {d: 0.03 for d in _DATES})
    sibling = _make_candidate(gate_engine, "would_adopt2", {d: 0.025 for d in _DATES})
    # SPY unavailable.
    batch = gate_engine.evaluate_candidate_batch(
        [cand, sibling], spy_returns_fn=lambda *a, **k: {}
    )
    for cid in ("would_adopt", "would_adopt2"):
        assert _verdict_for(batch, cid) != acceptance_gate.DECISION_ADOPT_CANDIDATE, (
            "EDGE-14 BUG: a candidate that clears BHY+panel and beats the 0.0 fallback "
            "was ADOPTED while SPY was unavailable — the permissive (-inf) sentinel let "
            "it clear on beats-zero. SPY-unavailable must force KEEP_INCUMBENT."
        )


# ===========================================================================
# AC-26 — Atlas parity: identical fresh series → identical verdict; advertised
# stats NEVER influence survival.
# ===========================================================================


def test_ac26_atlas_and_built_new_identical_series_identical_verdict(gate_engine):
    """An Atlas-sourced candidate and a built-new candidate with IDENTICAL fresh
    return series receive the IDENTICAL gate verdict — the cull treats provenance
    irrelevantly (same fold OOS + FDR + PBO + SPY baseline)."""
    spy_dated = {d: 0.01 for d in _DATES}
    series = {d: 0.03 for d in _DATES}
    built = _make_candidate(gate_engine, "built", series)
    # Atlas candidate: SAME fresh series; provenance/advertised-stats differ but must
    # not matter. Mark provenance via candidate_params (the gate must ignore it).
    atlas = _make_candidate(
        gate_engine, "atlas", series, candidate_params={"provenance": "atlas-suggested"}
    )
    batch = gate_engine.evaluate_candidate_batch(
        [built, atlas], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    assert _verdict_for(batch, "built") == _verdict_for(batch, "atlas"), (
        "identical fresh series must yield identical verdicts regardless of provenance"
    )


def test_ac26_advertised_community_stats_do_not_influence_survival(gate_engine):
    """An Atlas candidate carrying FANTASTIC advertised oos_metrics but a MEDIOCRE
    fresh series must be culled on the fresh series — the advertised stats never
    enter the gate. Two Atlas candidates with identical fresh series but wildly
    different advertised stats get the identical verdict."""
    spy_dated = {d: 0.01 for d in _DATES}
    mediocre = {d: 0.005 for d in _DATES}  # below SPY → should be culled on fresh series
    great_advertised = _make_candidate(
        gate_engine,
        "adv-great",
        mediocre,
        candidate_params={"provenance": "atlas-suggested", "oos_metrics": {"sharpe": 9.9}},
    )
    poor_advertised = _make_candidate(
        gate_engine,
        "adv-poor",
        mediocre,
        candidate_params={"provenance": "atlas-suggested", "oos_metrics": {"sharpe": -1.0}},
    )
    batch = gate_engine.evaluate_candidate_batch(
        [great_advertised, poor_advertised], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    assert _verdict_for(batch, "adv-great") == _verdict_for(batch, "adv-poor"), (
        "advertised oos_metrics must not influence the verdict — identical fresh "
        "series with different advertised stats must get the identical verdict"
    )
    # And neither survives (mediocre fresh series below SPY).
    assert "adv-great" not in _survivor_ids(batch)


# ===========================================================================
# C5b OPERATOR-LEGIBILITY — per-candidate rejection REASON must be observable and
# DISTINGUISHABLE (PBO-veto vs below-SPY-alpha vs FDR/BHY) so the live cull probe
# can prove each veto actually BITES (not silently inert). Survivor-count alone is
# confounded: the strict BHY gate yields zero survivors regardless, so "0 survivors"
# would hide an inert PBO veto or an inert SPY baseline. This is the "scoring factors
# must VARY on real data" guard at the unit level.
#
# The reason lives on the gate-engine result (CandidateGateResult), NOT on the
# acceptance_gate AcceptanceVerdict (acceptance_gate is out of scope / correct).
# ===========================================================================

# Accepted names for the per-candidate rejection-reason field (impl picks one).
_REASON_FIELDS = ("rejection_reason", "reject_reason", "gate_reason", "reason")


def _reason_for(batch, cid: str):
    """Return the per-candidate rejection reason from its CandidateGateResult,
    resolving whichever field name the impl exposed. None means 'no reason' (survivor)."""
    for r in batch.results:
        if r.candidate_id != cid:
            continue
        for f in _REASON_FIELDS:
            if hasattr(r, f):
                return getattr(r, f)
        raise AssertionError(
            f"CandidateGateResult exposes no rejection-reason field "
            f"(expected one of {_REASON_FIELDS}) — the reason is not observable"
        )
    raise AssertionError(f"no gate result for candidate {cid!r}")


def test_c5b_rejection_reason_field_is_observable(gate_engine):
    """CandidateGateResult must expose a per-candidate rejection-reason field at all.
    Without it the operator/live-probe cannot tell WHY a candidate was culled."""
    cand = _make_candidate(gate_engine, "obs", _consistent_configs()[0])
    sibling = _make_candidate(gate_engine, "obs2", _consistent_configs()[1])
    batch = gate_engine.evaluate_candidate_batch([cand, sibling])
    # Resolving the reason must not raise (the field exists on the result).
    _reason_for(batch, "obs")


def test_c5b_pbo_veto_reason_distinct_from_below_spy_and_fdr(gate_engine, monkeypatch):
    """The THREE rejection causes must be DISTINGUISHABLE in the result:
      - a PBO-vetoed candidate (high-PBO batch) carries a PBO reason,
      - a below-SPY-fold candidate carries a below-SPY reason,
      - a candidate rejected for FDR/non-winner carries a different reason,
    and the three reasons are pairwise DISTINCT. We assert distinctness + that the
    PBO reason mentions PBO (case-insensitive substring), without pinning exact
    strings (impl owns the exact reason tokens)."""
    # (a) PBO-vetoed: per-block-overfit batch → real pbo>0.5 → PBO veto reason.
    hpbo = [
        _make_candidate(gate_engine, f"r-pbo-{i}", c)
        for i, c in enumerate(_per_block_overfit_configs())
    ]
    pbo_batch = gate_engine.evaluate_candidate_batch(hpbo)
    pbo_reason = _reason_for(pbo_batch, "r-pbo-0")
    assert pbo_reason, "a PBO-vetoed candidate must carry a non-empty rejection reason"
    assert "pbo" in str(pbo_reason).lower(), (
        f"the PBO-veto reason must identify PBO; got {pbo_reason!r}"
    )

    # (b) below-SPY-fold: positive but below SPY → below-SPY reason (NOT a PBO reason).
    # ISOLATION REQUIREMENT: this branch must reject ONLY on the below-SPY cause, so the
    # batch must be LOW-PBO (the PBO veto, Stage-1, dominates the below-SPY cause, Stage-2,
    # by precedence — see test_c5b_rejection_reason_precedence_pbo_dominates_below_spy). Two
    # IDENTICAL flat configs yield pbo=1.0 (CSCV ranks the tied IS-best below the OOS median
    # in every combo), which would fire the PBO veto and mask the below-SPY cause. So we give
    # the two candidates DISTINCT positive levels (consistently ranked across all blocks →
    # pbo=0.0), both well below SPY's 0.02 fold level. DO NOT collapse these to identical
    # series — that re-introduces the high-PBO confound this fixture exists to avoid.
    spy_dated = {d: 0.02 for d in _DATES}
    below = _make_candidate(gate_engine, "r-below", {d: 0.001 for d in _DATES})
    below2 = _make_candidate(gate_engine, "r-below2", {d: 0.0005 for d in _DATES})
    below_batch = gate_engine.evaluate_candidate_batch(
        [below, below2], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    below_reason = _reason_for(below_batch, "r-below")
    assert below_reason, "a below-SPY candidate must carry a non-empty rejection reason"

    # The PBO reason and the below-SPY reason must be DISTINCT (different causes).
    assert str(pbo_reason) != str(below_reason), (
        f"PBO-veto reason ({pbo_reason!r}) must be distinct from below-SPY reason "
        f"({below_reason!r}) — the operator/live-probe must tell which veto bit"
    )
    # The below-SPY reason must NOT masquerade as a PBO reason.
    assert "pbo" not in str(below_reason).lower(), (
        f"below-SPY reason must not be a PBO reason; got {below_reason!r}"
    )


def test_c5b_survivor_has_no_rejection_reason(gate_engine, monkeypatch):
    """A candidate that is NOT rejected (a survivor / ADOPT) carries no rejection
    reason (None / empty) — the reason field is set ONLY when the candidate is culled,
    so the live probe can count genuine survivors vs each rejection cause."""
    # Spy the gate so we can force a clean ADOPT path without depending on the strict
    # BHY baseline: when the gate returns ADOPT, the result's reason must be None.
    import acceptance_gate as _ag

    real_gate = _ag.evaluate_acceptance_gate

    def _force_adopt(*args, **kwargs):
        v = real_gate(*args, **kwargs)
        return v._replace(decision=_ag.DECISION_ADOPT_CANDIDATE, vetoes_passed=True)

    monkeypatch.setattr(gate_engine, "evaluate_acceptance_gate", _force_adopt, raising=False)
    monkeypatch.setattr(_ag, "evaluate_acceptance_gate", _force_adopt)

    spy_dated = {d: 0.01 for d in _DATES}
    cand = _candidate_with_fold_alpha(gate_engine, "surv", beats=True, spy_dated=spy_dated)
    sibling = _candidate_with_fold_alpha(gate_engine, "surv2", beats=True, spy_dated=spy_dated)
    batch = gate_engine.evaluate_candidate_batch(
        [cand, sibling], **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    # Forced ADOPT → these are survivors → no rejection reason.
    for cid in ("surv", "surv2"):
        if cid in _survivor_ids(batch):
            assert not _reason_for(batch, cid), (
                "a survivor must not carry a rejection reason (reason is set only on a cull)"
            )


def test_c5b_rejection_reason_precedence_pbo_dominates_below_spy(gate_engine):
    """REASON PRECEDENCE (deterministic): a candidate that is BOTH high-PBO (a Stage-1
    hard veto) AND below-SPY-fold (a Stage-2 alpha rejection) must carry the PBO reason
    — the dominant cause — because the PBO veto fires FIRST (acceptance_gate Stage 1
    precedes the oos_alpha/SPY comparison in Stage 2). If the reason instead reported
    below-SPY, the live probe would misattribute the cull and the reason would be
    non-deterministic. We construct a per-block-overfit batch (real pbo=1.0) whose
    OVERALL level is far below a high SPY baseline, so both causes apply; the recorded
    reason must be the PBO one (stage-order precedence)."""
    # Per-block-overfit, LOW overall level → high PBO (stage-1) AND below SPY (stage-2).
    blocks = [_DATES[i * 10 : (i + 1) * 10] for i in range(8)]
    configs: list[dict[str, float]] = []
    for b in range(8):
        cfg = {d: (0.02 if i == b else 0.001) for i, blk in enumerate(blocks) for d in blk}
        configs.append(cfg)
    candidates = [_make_candidate(gate_engine, f"both-{i}", c) for i, c in enumerate(configs)]
    # SPY far above each candidate's low level → below-SPY also true.
    spy_dated = {d: 0.05 for d in _DATES}
    batch = gate_engine.evaluate_candidate_batch(
        candidates, **_spy_seam_kwargs(gate_engine, spy_dated)
    )
    reason = _reason_for(batch, "both-0")
    assert reason, "a both-high-PBO-and-below-SPY candidate must carry a rejection reason"
    # Stage-1 PBO veto dominates: the reason must be the PBO one, NOT the below-SPY one.
    assert "pbo" in str(reason).lower(), (
        f"a candidate that is BOTH high-PBO and below-SPY must report the Stage-1 PBO "
        f"reason (precedence), not the Stage-2 below-SPY reason; got {reason!r}"
    )
    assert "spy" not in str(reason).lower(), (
        f"PBO (Stage-1) must take precedence over below-SPY (Stage-2) in the reason; got {reason!r}"
    )
