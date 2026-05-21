"""
Cluster 3 AC-8 — guard / document the autotuner's port-level replay blind
spot.

The autotuner replay (run_simulation / _collect_sim_returns) simulates ONLY
the per-symphony altitude — it iterates per sym_id and replays each symphony's
exit path in isolation. It has no port-level altitude replay (no port
aggregation, no port-level exit). Port-mode autotuning results are therefore
NOT replay-validated: the objective the optimizer maximizes in port mode does
not reflect a port-level exit simulation.

Plan D-C3b decision: minimal — guard + document. Port-level exiting is
currently dormant (the standing config runs per_symphony). Full port-level
replay simulation is a disproportionate, separate feature. The requirement is
that port-mode tuning cannot be SILENTLY trusted: a clear warning / guard
must make the blind spot visible.

This file tests:
  * STRUCTURAL — autotuner.py documents the per-symphony-only replay limit
    where port-mode tuning is wired.
  * BEHAVIOURAL — invoking the port-mode autotuning path surfaces a visible
    warning that the replay does not validate the port-level altitude.

RED EXPECTATION: against pre-fix autotuner.py there is no port-level-replay
warning or guard — both tests fail.
"""

from __future__ import annotations

import ast
import io
import pathlib
import contextlib

import pytest

import autotuner


_AUTOTUNER_PATH = pathlib.Path(autotuner.__file__)
_AUTOTUNER_SRC = _AUTOTUNER_PATH.read_text(encoding="utf-8")
_AUTOTUNER_TREE = ast.parse(_AUTOTUNER_SRC)


# ===========================================================================
# AC-8 — structural: the per-symphony-only replay limit is documented.
# ===========================================================================


def test_autotuner_documents_port_level_replay_blind_spot() -> None:
    """AC-8: autotuner.py must DOCUMENT that the replay simulates only the
    per-symphony altitude and does NOT replay-validate the port-level exit.

    Asserts a comment / docstring naming the limitation exists — searching
    for a phrase tying 'port' to the replay/validation gap. A reader of the
    port-mode code must be able to see the blind spot without running it.

    RED: pre-fix autotuner.py contains no such documentation.
    """
    src_lower = _AUTOTUNER_SRC.lower()
    # The documentation must connect 'port' to the replay-validation gap.
    has_doc = (
        ("port" in src_lower)
        and any(
            phrase in src_lower
            for phrase in (
                "not replay-validated",
                "not replay validated",
                "per-symphony altitude only",
                "port-level replay",
                "port-level exit",
                "replay blind spot",
                "replay does not validate",
            )
        )
    )
    assert has_doc, (
        "autotuner.py does not document the port-level replay blind spot. "
        "The replay simulates only the per-symphony altitude; port-mode "
        "tuning is not replay-validated. That limitation must be documented "
        "in autotuner.py (a comment or docstring) so a reader of the "
        "port-mode path sees the gap."
    )


# ===========================================================================
# AC-8 — behavioural: the port-mode path surfaces a visible warning.
# ===========================================================================


def test_port_mode_autotuning_emits_replay_blind_spot_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-8: when the autotuner runs in port mode, it must emit a clear,
    visible warning that the per-symphony replay does not validate the
    port-level exit altitude — so port-mode results cannot be silently
    trusted.

    The implementer must expose a guard the port-mode autotuning entry point
    calls. We accept either:
      * a module-level helper `warn_port_mode_replay_blind_spot()` that the
        port-mode path invokes and that prints/logs a warning, OR
      * a warning emitted when run_autotuner is driven in port mode.

    This test calls the explicit helper if present (the minimal, directly
    testable seam D-C3b prescribes); it skips to the run_autotuner route
    only if the helper is absent.

    RED: pre-fix neither the helper nor any port-mode replay warning exists.
    """
    helper = getattr(autotuner, "warn_port_mode_replay_blind_spot", None)
    if helper is None:
        pytest.fail(
            "autotuner exposes no warn_port_mode_replay_blind_spot() guard. "
            "AC-8 (D-C3b minimal) requires an explicit, callable guard the "
            "port-mode autotuning path invokes to surface the per-symphony-"
            "only replay limitation."
        )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        helper()
    output = buf.getvalue().lower()

    assert output.strip(), (
        "warn_port_mode_replay_blind_spot() produced no visible output. "
        "The port-level replay blind spot must surface a clear warning."
    )
    assert "port" in output and (
        "replay" in output or "per-symphony" in output
    ), (
        f"warn_port_mode_replay_blind_spot() output does not clearly name "
        f"the per-symphony-replay / port-level gap. Got: {output!r}"
    )


def test_warn_port_mode_replay_blind_spot_is_idempotent_and_pure() -> None:
    """AC-8: the blind-spot guard must be safe to call repeatedly — it only
    surfaces a warning, it must not mutate state or raise. A guard the
    port-mode path calls on every run must be a no-side-effect notice.

    RED: skips/fails pre-fix (helper absent); once present it must pass.
    """
    helper = getattr(autotuner, "warn_port_mode_replay_blind_spot", None)
    if helper is None:
        pytest.fail(
            "autotuner.warn_port_mode_replay_blind_spot missing — see "
            "test_port_mode_autotuning_emits_replay_blind_spot_warning."
        )
    # Calling twice must not raise.
    with contextlib.redirect_stdout(io.StringIO()):
        helper()
        helper()
