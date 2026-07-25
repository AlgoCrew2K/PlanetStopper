"""
RED tests -- daily incubation tick scheduling (AC-3 scheduling half; recon
finding (a), ruled accepted by the PM: 03:30 daemon slot via the exact
lens-pipeline CC-2 wrapper pattern).

Mirrors tests/app/test_lens_pipeline_gate.py's Thread-patching style exactly
(same rationale: the thin wrapper does an inline `import threading` then
`threading.Thread(...).start()`, so the correct patch target is
'threading.Thread' in the stdlib module, not a module-level app.py binding).

Coverage:
  SCHED1: app._run_incubation_tick() spawns a daemon thread (never blocks the
    scheduler thread -- arch constraint 1).
  SCHED2: run_scheduler() registers the tick at 03:30 (source-level check --
    calling run_scheduler() directly would enter its `while True` loop).
  SCHED3: 03:30 is distinct from the existing 03:00 lens-pipeline slot (no
    same-minute contention).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import app as app_module


class TestIncubationTickThreadSpawn:
    def test_run_incubation_tick_spawns_a_daemon_thread(self):
        """SCHED1: the scheduler-facing wrapper must spawn a thread and return
        immediately -- never block the 1-minute execution path (arch constraint 1)."""
        assert hasattr(app_module, "_run_incubation_tick"), (
            "app.py is missing _run_incubation_tick() -- the scheduler-facing "
            "CC-2 wrapper has not been added yet."
        )

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app_module._run_incubation_tick()

        assert mock_thread_instance.start.call_count == 1, (
            "_run_incubation_tick() must call thread.start() exactly once -- the "
            "tick runs in a background daemon thread, never inline on the "
            "scheduler thread."
        )

    def test_run_incubation_tick_thread_is_daemon(self):
        """The spawned thread must be a daemon thread (matches _run_lens_pipeline's
        threading.Thread(target=..., daemon=True) precedent) so it never blocks
        process shutdown."""
        captured_kwargs = {}

        def _capturing_thread_cls(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with patch("threading.Thread", _capturing_thread_cls):
            app_module._run_incubation_tick()

        assert captured_kwargs.get("daemon") is True, (
            f"threading.Thread(...) must be called with daemon=True, got kwargs: {captured_kwargs}"
        )


class TestIncubationTickSchedulingSlot:
    def test_run_scheduler_source_registers_incubation_tick_at_0330(self):
        """SCHED2: source-level check -- run_scheduler() is a `while True` loop, so
        it cannot be called directly in a test. Assert its source registers the
        incubation tick at 03:30 via schedule.every().day.at(...)."""
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:30"' in source or "'03:30'" in source, (
            "app.run_scheduler() does not register a 03:30 job -- the incubation "
            "tick must be scheduled at 03:30 (recon finding (a): staggered from "
            "the existing 03:00 lens-pipeline slot, same CC-2 wrapper pattern)."
        )
        assert "_run_incubation_tick" in source, (
            "app.run_scheduler() does not reference _run_incubation_tick -- the "
            "03:30 slot must call the incubation tick wrapper specifically."
        )

    def test_incubation_slot_is_distinct_from_lens_pipeline_slot(self):
        """SCHED3: 03:30 must not collide with the existing 03:00 lens-pipeline slot
        -- both jobs must be independently schedulable without same-minute
        contention."""
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:00"' in source, (
            "Sanity check failed: the existing 03:00 lens-pipeline slot is missing "
            "from run_scheduler() -- test assumption broken, investigate before "
            "trusting this file's other assertions."
        )
        assert '"03:30"' in source, (
            "See test_run_scheduler_source_registers_incubation_tick_at_0330."
        )
