"""
R3-b MA-4 AC-5 — PRODUCTION <-> REPLAY parity for the arm/disarm decision (HARD).

R3-d retunes by REPLAYING exit decisions. If the replay's disarm diverges from
production's, the autotuner optimizes against a different exit surface than the
live engine, invalidating the tune. The plan makes parity STRUCTURAL by extracting
ONE shared pure seam (``math_engine.compute_arm_disarm_decision``) called by BOTH
``alpha_bot_execution.py`` (production, inline in main()) AND
``autotuner._replay_exit_tick`` (replay).

Two guards:
  5a (structural, one-sided-failure): BOTH files delegate to the shared seam AND
     NEITHER retains the OLD inverted-disarm doubling literal
     (``TRIGGER_THRESHOLD * 2``). Fails if only ONE site is fixed.
  5b (behavioral): the replay's armed decision sequence over a mixed
     arm/deteriorate/recover/re-arm trajectory is IDENTICAL, tick for tick, to the
     canonical seam called directly — proving the replay is wired to the seam, not
     a divergent copy.

RED on base 57a8a897: neither file calls the seam (5a); the doubling literal is
present in both (5a); ``math_engine.compute_arm_disarm_decision`` does not exist
(5b -> AttributeError).

TOLERANCE: armed state (bool) is a discrete categorical outcome — exact equality.
"""

from __future__ import annotations

import pathlib
import re

import autotuner
import math_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ABE_PATH = _REPO_ROOT / "alpha_bot_execution.py"
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"

# The OLD inverted disarm's unique signature: it doubles the trigger threshold
# (`prob > TRIGGER_THRESHOLD_PCT * 2`). Case-insensitive covers both the
# production `acc_TRIGGER_THRESHOLD_PCT * 2` and the replay `trigger_threshold * 2`.
# The Sortino `** 2` power (autotuner.py:628) is NOT matched (no trigger token).
_INVERTED_DISARM_DOUBLING = re.compile(r"(?i)trigger_threshold\w*\s*\*\s*2\b")


# ===========================================================================
# 5a — structural parity (fails if only one site is fixed)
# ===========================================================================


def test_production_delegates_arm_disarm_to_shared_seam() -> None:
    """Production must call the shared seam. RED on base (inline disarm, no call)."""
    src = _ABE_PATH.read_text(encoding="utf-8")
    assert "compute_arm_disarm_decision" in src, (
        "alpha_bot_execution.py does not call math_engine.compute_arm_disarm_decision "
        "— production must delegate the arm/disarm decision to the shared seam."
    )


def test_replay_delegates_arm_disarm_to_shared_seam() -> None:
    """The replay must call the shared seam. RED on base (inline disarm, no call)."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    assert "compute_arm_disarm_decision" in src, (
        "autotuner.py does not call math_engine.compute_arm_disarm_decision — the "
        "replay must delegate the arm/disarm decision to the SAME shared seam as "
        "production (R3-d retunes by replay; a divergent exit surface invalidates it)."
    )


def test_inverted_disarm_doubling_removed_from_production() -> None:
    """The OLD inverted disarm (prob > TRIGGER_THRESHOLD_PCT * 2) must be GONE from
    production. RED on base (present at alpha_bot_execution.py:1397)."""
    src = _ABE_PATH.read_text(encoding="utf-8")
    assert not _INVERTED_DISARM_DOUBLING.search(src), (
        "alpha_bot_execution.py still contains the inverted-disarm threshold-doubling "
        "(`TRIGGER_THRESHOLD_PCT * 2`) — the deterioration disarm must be deleted, not "
        "retained alongside the seam."
    )


def test_inverted_disarm_doubling_removed_from_replay() -> None:
    """The OLD inverted disarm (mc > trigger_threshold * 2) must be GONE from the
    replay. RED on base (present at autotuner.py:1221). This is the leg that fails
    if ONLY production is fixed."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    assert not _INVERTED_DISARM_DOUBLING.search(src), (
        "autotuner.py still contains the inverted-disarm threshold-doubling "
        "(`trigger_threshold * 2`) — the replay disarm must be deleted and delegated "
        "to the shared seam, or production and replay diverge."
    )


# ===========================================================================
# 5b — behavioral parity: replay armed sequence == canonical seam, tick for tick
# ===========================================================================

_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}

# arm -> deteriorate x2 -> recover x5 (disarms once the ladder confirms) -> re-arm
# -> deteriorate. Positive returns throughout so the trailing stop never fires and
# never perturbs the armed observation. The exact disarm tick depends on
# DISARM_CONFIRM_TICKS, but BOTH sides use the same constant, so they must agree.
_MC_TRAJECTORY = [10.0, 35.0, 70.0, 3.0, 3.0, 3.0, 3.0, 3.0, 10.0, 35.0]


def _tick(mc: float | None) -> dict:
    return {
        "time": "12:00",
        "return": 1.0,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _replay_armed_sequence() -> list[bool]:
    state = autotuner._fresh_replay_state()
    n = len(_MC_TRAJECTORY)
    seq = []
    for idx, mc in enumerate(_MC_TRAJECTORY):
        autotuner._replay_exit_tick(state, _tick(mc), idx, n, _PARAMS, grace_minutes=0)
        seq.append(bool(state["armed"]))
    return seq


def _canonical_seam_armed_sequence() -> list[bool]:
    """Independently drive the SAME prob trajectory through the pure seam directly,
    threading armed + the disarm ladder count exactly as a caller does."""
    armed = False
    count = 0
    seq = []
    for mc in _MC_TRAJECTORY:
        armed, count = math_engine.compute_arm_disarm_decision(
            prob_underperforming=mc,
            is_triggered=False,
            armed=armed,
            disarm_confirm_count=count,
            take_profit_mc_pct=_PARAMS["TAKE_PROFIT_MC_PCT"],
            trigger_threshold_pct=_PARAMS["TRIGGER_THRESHOLD_PCT"],
            disarm_confirm_ticks=math_engine.DISARM_CONFIRM_TICKS,
        )
        seq.append(bool(armed))
    return seq


def test_replay_arm_disarm_sequence_matches_canonical_seam() -> None:
    """5b: the replay's armed decision at every tick equals the canonical seam's,
    proving the replay is a faithful caller of the shared decision function (not a
    re-implemented copy that could silently drift from production)."""
    replay_seq = _replay_armed_sequence()
    seam_seq = _canonical_seam_armed_sequence()
    assert replay_seq == seam_seq, (
        "PARITY BROKEN: the replay's armed sequence diverges from the canonical "
        f"compute_arm_disarm_decision sequence.\n  replay = {replay_seq}\n  seam   = "
        f"{seam_seq}\nThe replay must call the shared seam so its exit surface matches "
        "production's (R3-d retunes by replay)."
    )
    # Sanity: the trajectory must actually exercise arm -> disarm -> re-arm, or the
    # parity assertion is vacuous.
    assert replay_seq[0] is True, "trajectory must arm on the first (in-band) tick"
    assert False in replay_seq, "trajectory must disarm at some point (recovery ladder)"
    assert replay_seq[-1] is True, "trajectory must re-arm after the recovery-disarm"
