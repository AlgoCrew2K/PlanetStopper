"""
Multi-cycle convergence simulator (AC-P2.8.*)

simulate_convergence() is a pure test-harness function that replays the
port-level selection loop across N cycles without live DB or broker calls.
Used by RED tests to verify convergence guarantee, one-exit-per-cycle,
composition-change detection, and Amendment B4 telemetry trajectory.

Production multi-cycle convergence is driven by the existing per-minute scheduler
in alpha_bot_execution.py — each scheduler tick is one cycle.
"""

from __future__ import annotations

import uuid

from port_selector import select_symphony_with_mc_gate, composition_hash as _composition_hash


def simulate_convergence(
    symphonies: list[dict],
    account_id: str,
    target_reduction_per_cycle: list[list[dict]],
    exit_authority: str = "port_level",
    over_shoot_penalty: float = 1.0,
    min_match_quality_threshold: float | None = None,
) -> dict:
    """
    Simulate multi-cycle port-level convergence (AC-P2.8.*).

    Parameters
    ----------
    symphonies:
        Initial list of symphony dicts with symphony_id, value, exposure_usd,
        total_value, position_open_date, mc_sanity_gate_would_block.
    account_id:
        Account identifier.
    target_reduction_per_cycle:
        List of target_reduction payloads, one per simulated cycle.
        Each entry is a list of {"ticker": str, "amount_usd": float}.
        If more cycles are supplied than needed, excess cycles after all symphonies
        exit are no-ops.
    exit_authority:
        "port_level" (default) or "per_symphony".
    over_shoot_penalty:
        L1 overshoot penalty multiplier.
    min_match_quality_threshold:
        Abort threshold for no-good-match (AC-P2.7.5). None = no abort check.

    Returns
    -------
    dict with:
        total_exits: int
        exits_in_cycle_1: int
        cycles: list[dict]  — per-cycle result records
        per_symphony_exits_actioned: int  — always 0 when exit_authority=port_level
    """
    remaining_symphonies = list(symphonies)
    cycles = []
    total_exits = 0
    per_symphony_exits_actioned = 0

    prev_composition_hash = _composition_hash([s["symphony_id"] for s in remaining_symphonies])

    for cycle_idx, target_reduction in enumerate(target_reduction_per_cycle, start=1):
        cycle_record: dict = {
            "cycle": cycle_idx,
            "triggered": False,
            "exits": 0,
            "port_trigger_id": None,
            "composition_change_detected": False,
            "port_total_reduction_usd": 0.0,
        }

        # Detect composition change (AC-P2.8.1)
        current_hash = _composition_hash([s["symphony_id"] for s in remaining_symphonies])
        if current_hash != prev_composition_hash:
            cycle_record["composition_change_detected"] = True
        prev_composition_hash = current_hash

        # No symphonies left — convergence complete
        if not remaining_symphonies:
            cycles.append(cycle_record)
            continue

        # AC-P2.8.6: per-symphony triggered flags observed but not actioned under port authority
        if exit_authority == "port_level":
            for sym in remaining_symphonies:
                if sym.get("per_symphony_triggered", False):
                    pass  # observed, not actioned

        # Only fire under port_level authority
        if exit_authority != "port_level":
            cycles.append(cycle_record)
            continue

        if not target_reduction:
            cycles.append(cycle_record)
            continue

        # Port total reduction for this cycle (Amendment B4)
        port_total_reduction_usd = float(
            sum(item["amount_usd"] for item in target_reduction)
        )
        cycle_record["port_total_reduction_usd"] = port_total_reduction_usd
        cycle_record["triggered"] = True

        # Generate a fresh port_trigger_id for this cycle (AC-P2.10.2)
        port_trigger_id = str(uuid.uuid4())
        cycle_record["port_trigger_id"] = port_trigger_id

        # Select ONE symphony — Amendment B1 MC gate applied (AC-P2.8.4)
        selection = select_symphony_with_mc_gate(
            target_reduction=target_reduction,
            candidates=remaining_symphonies,
            over_shoot_penalty=over_shoot_penalty,
            min_match_quality_threshold=min_match_quality_threshold,
        )

        if selection.get("suppressed") or selection["selected_symphony_id"] is None:
            # MC gate suppressed or no-good-match abort — no exit this cycle
            cycle_record["exits"] = 0
            cycle_record["suppressed"] = selection.get("suppressed", False)
            cycles.append(cycle_record)
            continue

        selected_id = selection["selected_symphony_id"]

        # Remove the selected symphony from remaining (it exited whole-portfolio)
        remaining_symphonies = [
            s for s in remaining_symphonies if s["symphony_id"] != selected_id
        ]

        # Mark composition change for NEXT cycle (AC-P2.8.1)
        new_hash = _composition_hash([s["symphony_id"] for s in remaining_symphonies])
        if new_hash != current_hash:
            # Will be detected at the top of the next cycle
            prev_composition_hash = current_hash  # next cycle compares against this

        cycle_record["exits"] = 1
        cycle_record["selected_symphony_id"] = selected_id
        total_exits += 1

        cycles.append(cycle_record)

    exits_in_cycle_1 = cycles[0]["exits"] if cycles else 0

    return {
        "total_exits": total_exits,
        "exits_in_cycle_1": exits_in_cycle_1,
        "cycles": cycles,
        "per_symphony_exits_actioned": per_symphony_exits_actioned,
        "remaining_symphonies": [s["symphony_id"] for s in remaining_symphonies],
    }
