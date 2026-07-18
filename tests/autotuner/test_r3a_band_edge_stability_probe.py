"""R3-a deliverable (b) — 300-path band-edge arm-decision stability probe.

Pre-retune checklist item (b) (DECISIONS.md a/b; feature-plans/math-r3a-checklist.md):
quantify how stable the REPLAY Monte-Carlo estimator's arm decision is at its
current path count (`synthetic_history._MC_REPLAY_SIMULATION_PATHS = 300`) near
the arm-band boundary, versus higher reference path counts, and emit a committed
bump-vs-accept recommendation — BEFORE the R3-d retune leans on that estimator.

THE MEASURAND (AC-6): the engine arms a protective trailing stop when
    acc_TAKE_PROFIT_MC_PCT <= prob_underperforming < acc_TRIGGER_THRESHOLD_PCT
(alpha_bot_execution.py:1373-1381). run_monte_carlo estimates
prob_underperforming as (paths_at_or_above / paths) * 100 — a Binomial/paths
estimate whose sampling error shrinks with path count. Near a band BOUNDARY, a
300-path estimate can land on the opposite side of the boundary from a
higher-path reference, flipping the arm decision. The probe measures that
disagreement rate (the "flip-rate"), honestly framed as "300 vs reference-N
disagreement" — NOT "300 vs unknowable truth" (plan Edge Cases).

NON-VACUITY (memory: an "N results" test hid a dead factor): a probe that always
returns 0 (or any constant) would pass a naive "flip-rate in [0,1]" check. The
crux control asserts the NEAR-edge flip-rate materially EXCEEDS a mid-band
(far-from-any-boundary) control whose flip-rate is ~0 — proving the probe
actually measures boundary instability. The recommendation is tested as a PURE
decision function above/below threshold, so a hardcoded "accept" fails.

SCOPE GUARD (AC-8/AC-10): the ONLY code change R3-a may make is the single
replay/analysis constant `_MC_REPLAY_SIMULATION_PATHS` (off the live-execution
path), and ONLY if the probe recommends a bump. The live-engine MC path count
`alpha_bot_execution.SIMULATION_PATHS` (5000) is NEVER touched by R3-a. These
guards fail if a live-path constant is edited.

RED CONTRACT: drives the not-yet-built probe module
`scripts/mc_band_edge_stability_probe.py` (deliverable (b), r3a-engine). Import
guarded so RED is clean FAILUREs, not collection errors.
"""

from __future__ import annotations

import json

import pytest

import math_engine

# ---------------------------------------------------------------------------
# Guarded import of the deliverable-(b) probe (built by r3a-engine during GREEN).
# ---------------------------------------------------------------------------
try:
    from scripts import mc_band_edge_stability_probe as _probe
except Exception:  # noqa: BLE001 — any import failure = not-yet-built = RED
    _probe = None


def _require_probe():
    assert _probe is not None, (
        "scripts.mc_band_edge_stability_probe is not yet built (RED). r3a-engine "
        "builds it during GREEN: the near-edge synthetic pool, the real "
        "run_monte_carlo 300-vs-reference flip-rate, the pure decision fn, and "
        "the committed recommendation artifact."
    )
    return _probe


# A near-edge scenario: true underperformance prob a hair inside the lower arm
# boundary (default TAKE_PROFIT_MC_PCT=5.0). A far scenario: mid-band (10.0),
# far from BOTH boundaries (5.0 and 15.0). Concrete offsets are the probe's to
# realize via pool construction; these are the scenario intents the test drives.
_LOWER_BOUNDARY_PCT = 5.0
_NEAR_EDGE_TRUE_PROB_PCT = 5.3  # ~0.3pp inside the arm band, near the 5.0 edge
_MID_BAND_TRUE_PROB_PCT = 10.0  # deep inside [5.0, 15.0), far from any boundary

# A mid-band control's flip-rate must be essentially zero (many sampling std
# away from either boundary). Small non-zero tolerance for finite-sample noise.
_FAR_FLIP_RATE_CEILING = 0.02
# The near-edge flip-rate must exceed the far control by at least this margin —
# a real, materially-larger disagreement, not a coincidental tie.
_NEAR_OVER_FAR_MARGIN = 0.10


def _band_edge_kwargs(true_prob_pct: float) -> dict:
    return dict(
        target_true_prob_pct=true_prob_pct,
        boundary_pct=_LOWER_BOUNDARY_PCT,
        n_seeds=80,
        reference_counts=(1000, 5000),
        base_seed=20260718,
    )


# ===========================================================================
# AC-6 — the probe measures a valid flip-rate at 300 vs reference, using the
# REAL run_monte_carlo, and it measures BOUNDARY INSTABILITY (near >> far).
# ===========================================================================


def test_flip_rate_is_a_valid_fraction_for_each_reference() -> None:
    probe = _require_probe()
    result = probe.measure_flip_rate(**_band_edge_kwargs(_NEAR_EDGE_TRUE_PROB_PCT))
    fbr = dict(result.flip_rate_by_reference)
    assert set(fbr) == {1000, 5000}, (
        f"flip_rate_by_reference keyed by {sorted(fbr)}, expected the requested "
        "reference counts {1000, 5000}."
    )
    for ref, fr in fbr.items():
        assert 0.0 <= fr <= 1.0, (
            f"flip-rate for reference {ref} = {fr!r} is not a fraction in [0,1]."
        )
    assert int(result.n300_paths) == 300, (
        f"the probe must measure the 300-path replay count (n300_paths={result.n300_paths!r})."
    )


def test_probe_drives_the_real_monte_carlo_at_300_and_each_reference(monkeypatch) -> None:
    """AC-6 non-vacuity: the probe must call the REAL estimator at the REAL path
    counts — not a reimplemented Binomial. Spy records every simulation_paths
    value passed to math_engine.run_monte_carlo and asserts 300 + each reference
    were all exercised.
    """
    probe = _require_probe()
    seen: list[int] = []
    real = math_engine.run_monte_carlo

    def _spy(*args, **kwargs):
        paths = kwargs.get("simulation_paths")
        if paths is None and len(args) > 3:
            paths = args[3]
        seen.append(int(paths))
        return real(*args, **kwargs)

    monkeypatch.setattr(math_engine, "run_monte_carlo", _spy)
    probe.measure_flip_rate(**_band_edge_kwargs(_NEAR_EDGE_TRUE_PROB_PCT))
    seen_set = set(seen)
    assert {300, 1000, 5000} <= seen_set, (
        f"the probe did not drive math_engine.run_monte_carlo at all required "
        f"path counts — saw {sorted(seen_set)}, expected superset of "
        "{300, 1000, 5000}. (The probe must call run_monte_carlo module-qualified "
        "so this seam is visible.)"
    )


def test_near_edge_flip_rate_materially_exceeds_mid_band_control() -> None:
    """NON-VACUITY CRUX: the probe measures BOUNDARY instability, not a constant.

    A scenario whose true prob sits a hair from the arm boundary must flip far
    more often at 300 paths than a mid-band scenario many sampling-std away from
    any boundary (whose flip-rate is ~0). If near ~= far, the probe is not
    measuring what it claims and the whole recommendation is noise.
    """
    probe = _require_probe()
    near = dict(
        probe.measure_flip_rate(
            **_band_edge_kwargs(_NEAR_EDGE_TRUE_PROB_PCT)
        ).flip_rate_by_reference
    )
    far = dict(
        probe.measure_flip_rate(**_band_edge_kwargs(_MID_BAND_TRUE_PROB_PCT)).flip_rate_by_reference
    )
    # Compare at the highest reference (most stable ground truth for the flip).
    ref = max(near)
    assert far[ref] <= _FAR_FLIP_RATE_CEILING, (
        f"mid-band control flip-rate at reference {ref} = {far[ref]!r} is not ~0 "
        f"(> {_FAR_FLIP_RATE_CEILING}). A scenario far from any boundary should "
        "almost never flip — a non-zero far flip-rate means the probe is unstable "
        "or the scenario is mis-placed."
    )
    assert near[ref] > far[ref] + _NEAR_OVER_FAR_MARGIN, (
        f"near-edge flip-rate ({near[ref]!r}) does not materially exceed the "
        f"mid-band control ({far[ref]!r}) at reference {ref}. The probe is not "
        "detecting boundary instability — a flat/constant flip-rate makes the "
        "bump-vs-accept recommendation meaningless."
    )


def test_flip_rate_is_deterministic() -> None:
    """AC-4 (b): fixed seeds → byte-identical flip-rate across two runs."""
    probe = _require_probe()
    first = dict(
        probe.measure_flip_rate(
            **_band_edge_kwargs(_NEAR_EDGE_TRUE_PROB_PCT)
        ).flip_rate_by_reference
    )
    second = dict(
        probe.measure_flip_rate(
            **_band_edge_kwargs(_NEAR_EDGE_TRUE_PROB_PCT)
        ).flip_rate_by_reference
    )
    assert first == second, (
        f"flip-rate is not deterministic: run1={first}, run2={second}. Every MC "
        "seed must derive from base_seed so the measured flip-rate is auditable."
    )


def test_reference_ladder_default_is_at_least_1000_and_includes_live_5000() -> None:
    """AC-6: reference counts are the higher-fidelity comparators (>=1000), and
    the ladder includes 5000 — the live engine's SIMULATION_PATHS — so the
    headline comparison is replay-300 vs live-fidelity-5000."""
    probe = _require_probe()
    ladder = tuple(probe.DEFAULT_REFERENCE_COUNTS)
    assert ladder, "DEFAULT_REFERENCE_COUNTS must be non-empty."
    assert all(int(n) >= 1000 for n in ladder), (
        f"every reference count must be >= 1000 (higher-fidelity than 300); got {ladder}."
    )
    assert 5000 in {int(n) for n in ladder}, (
        f"the reference ladder must include 5000 (the live SIMULATION_PATHS) so "
        f"the headline is replay-vs-live fidelity; got {ladder}."
    )


# ===========================================================================
# AC-7 — deterministic bump-vs-accept recommendation artifact.
# ===========================================================================


def test_recommendation_is_a_pure_threshold_decision(recwarn) -> None:
    """AC-7 non-vacuity: recommend() must follow the documented threshold rule,
    not hardcode an outcome. Above threshold -> bump; below -> accept."""
    probe = _require_probe()
    t = float(probe.RECOMMENDATION_THRESHOLD)
    assert probe.recommend(t + 0.01, threshold=t) == "bump", (
        "a flip-rate above the threshold must recommend 'bump'."
    )
    assert probe.recommend(t - 0.01, threshold=t) == "accept", (
        "a flip-rate below the threshold must recommend 'accept'."
    )


def test_recommendation_threshold_is_the_documented_assumed_line() -> None:
    """The [PM-ASSUMED] bump line is ~5% arm-decision disagreement near the edge."""
    probe = _require_probe()
    assert float(probe.RECOMMENDATION_THRESHOLD) >= 0.05, (
        f"RECOMMENDATION_THRESHOLD={probe.RECOMMENDATION_THRESHOLD!r} — the "
        "documented [PM-ASSUMED] bump line is a >=5% flip-rate near the band edge."
    )


def test_probe_emits_recommendation_artifact_with_required_fields(tmp_path) -> None:
    """run_probe writes a machine-readable + human-readable recommendation.

    Written to a tmp location here (hermetic); the COMMITTED artifact under
    docs/generated/ is produced by r3a-engine running the probe standalone.
    """
    probe = _require_probe()
    md_path = tmp_path / "mc-band-edge-stability.md"
    json_path = tmp_path / "mc-band-edge-stability.json"
    summary = probe.run_probe(md_path=md_path, json_path=json_path)

    assert md_path.exists(), "run_probe must write the human-readable markdown artifact."
    assert json_path.exists(), "run_probe must write the machine-readable JSON sidecar."

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # Flip-rate at 300 vs EACH reference must be recorded.
    assert "flip_rate_by_reference" in payload and payload["flip_rate_by_reference"], (
        "artifact JSON must record the 300-vs-reference flip-rate for each reference."
    )
    assert "decision_threshold" in payload, "artifact JSON must record the decision threshold used."
    assert payload.get("recommendation") in {"bump", "accept"}, (
        f"artifact recommendation must be 'bump' or 'accept', got "
        f"{payload.get('recommendation')!r}."
    )
    # The returned summary and the persisted JSON must agree on the verdict.
    assert summary.get("recommendation") == payload.get("recommendation"), (
        "run_probe's returned summary disagrees with the persisted artifact "
        "recommendation — they must be one deterministic verdict."
    )


def test_default_artifact_paths_live_under_docs_generated() -> None:
    """AC-7 / PM ruling 4: the generated report's home is docs/generated/."""
    probe = _require_probe()
    md = str(probe.ARTIFACT_MD_PATH).replace("\\", "/")
    js = str(probe.ARTIFACT_JSON_PATH).replace("\\", "/")
    assert md.endswith("docs/generated/mc-band-edge-stability.md"), (
        f"ARTIFACT_MD_PATH must be docs/generated/mc-band-edge-stability.md, got {md}."
    )
    assert js.endswith("docs/generated/mc-band-edge-stability.json"), (
        f"ARTIFACT_JSON_PATH must be docs/generated/mc-band-edge-stability.json, got {js}."
    )


# ===========================================================================
# AC-8 / AC-10 — scope guard: only the replay constant may change; live untouched.
# ===========================================================================


def test_live_engine_mc_path_count_is_unchanged() -> None:
    """AC-10: R3-a must not alter the live-execution MC path count.

    alpha_bot_execution.SIMULATION_PATHS (5000) is the LIVE engine's estimator
    fidelity and is off-limits to R3-a. Only the replay/analysis constant may be
    bumped. A live-path edit fails here.
    """
    import alpha_bot_execution

    assert int(alpha_bot_execution.SIMULATION_PATHS) == 5000, (
        f"alpha_bot_execution.SIMULATION_PATHS = {alpha_bot_execution.SIMULATION_PATHS!r}, "
        "expected the unchanged live default 5000. R3-a must NOT touch the "
        "live-execution MC path count — only the replay constant "
        "synthetic_history._MC_REPLAY_SIMULATION_PATHS may be bumped."
    )


def test_sanctioned_knob_is_the_replay_constant_not_the_live_one() -> None:
    """AC-8: if a bump is taken, it edits _MC_REPLAY_SIMULATION_PATHS only.

    The probe must name the replay/analysis knob (off the live-execution path)
    as the sanctioned target — never the live SIMULATION_PATHS.
    """
    probe = _require_probe()
    knob = str(probe.SANCTIONED_KNOB)
    assert "_MC_REPLAY_SIMULATION_PATHS" in knob, (
        f"SANCTIONED_KNOB={knob!r} — the only R3-a-sanctioned bump target is the "
        "replay/analysis constant synthetic_history._MC_REPLAY_SIMULATION_PATHS."
    )
    assert knob != "SIMULATION_PATHS" and "alpha_bot_execution" not in knob, (
        f"SANCTIONED_KNOB={knob!r} must not name the live-execution SIMULATION_PATHS."
    )


def test_replay_constant_is_currently_the_probed_300() -> None:
    """The probe's baseline is synthetic_history._MC_REPLAY_SIMULATION_PATHS.

    Pin the baseline the flip-rate is measured against so a silent change to the
    replay path count is caught alongside this probe.
    """
    import synthetic_history

    assert int(synthetic_history._MC_REPLAY_SIMULATION_PATHS) == 300, (
        f"synthetic_history._MC_REPLAY_SIMULATION_PATHS = "
        f"{synthetic_history._MC_REPLAY_SIMULATION_PATHS!r}, expected the probed "
        "baseline 300. If a bump lands, this pin is updated in the SAME R3-a diff."
    )
