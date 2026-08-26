"""
RED tests -- daily retirement-recommender producer tick scheduling.

PM ruling (review-response cycle 2, folded alongside quant-code-reviewer's
Finding 1): retire-doc found a real completeness gap -- nothing in production
calls build_recommendations()/persist_recommendations(), so the read-only
route/panel render an honest empty state FOREVER live. The fix is an
off-hours scheduler tick, NOT render-path recomputation (every other
advisory surface in this repo runs heavy math off-hours and the render reads
persisted rows only -- recomputing O(n^2) correlations + fleet metrics on
every dashboard load would be the anti-pattern here).

Mirrors tests/app/test_incubation_scheduling.py's structure and Thread-
patching style EXACTLY (same rationale: the thin wrapper does an inline
`import threading` then `threading.Thread(...).start()`, so the correct
patch target is 'threading.Thread' in the stdlib module, not a module-level
app.py binding) -- per the PM's explicit instruction to mirror
_run_incubation_tick's precedent exactly.

Coverage:
  SCHED1: app._run_retirement_recommender_tick() spawns a daemon thread
    (never blocks the scheduler thread -- arch constraint 1).
  SCHED2: run_scheduler() registers the tick at 03:45 (source-level check --
    calling run_scheduler() directly would enter its `while True` loop).
    03:45 is a test-writer choice (not literally mandated) continuing the
    established 03:00/03:30 stagger cadence by the same 15-30 min gap,
    keeping all three off-hours jobs comfortably before market open.
  SCHED3: 03:45 is distinct from the existing 03:00 lens-pipeline and 03:30
    incubation slots (no same-minute contention).
  WORKER: the tick's worker function actually calls the real producer chain
    -- build_recommendations() then persist_recommendations(<its result>) --
    not just spawns a thread that does nothing. This is the part that
    actually closes retire-doc's completeness gap; a thread-spawn test alone
    would pass even if the thread's target body were empty.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import app as app_module


class TestRetirementRecommendationsTickThreadSpawn:
    def test_run_retirement_recommender_tick_spawns_a_daemon_thread(self):
        """SCHED1: the scheduler-facing wrapper must spawn a thread and return
        immediately -- never block the 1-minute execution path (arch constraint 1)."""
        assert hasattr(app_module, "_run_retirement_recommender_tick"), (
            "app.py is missing _run_retirement_recommender_tick() -- the "
            "scheduler-facing CC-2 wrapper has not been added yet."
        )

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app_module._run_retirement_recommender_tick()

        assert mock_thread_instance.start.call_count == 1, (
            "_run_retirement_recommender_tick() must call thread.start() exactly "
            "once -- the tick runs in a background daemon thread, never inline "
            "on the scheduler thread."
        )

    def test_run_retirement_recommender_tick_thread_is_daemon(self):
        """The spawned thread must be a daemon thread (matches
        _run_incubation_tick's threading.Thread(target=..., daemon=True)
        precedent) so it never blocks process shutdown."""
        captured_kwargs = {}

        def _capturing_thread_cls(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with patch("threading.Thread", _capturing_thread_cls):
            app_module._run_retirement_recommender_tick()

        assert captured_kwargs.get("daemon") is True, (
            f"threading.Thread(...) must be called with daemon=True, got kwargs: {captured_kwargs}"
        )


class TestRetirementRecommendationsTickSchedulingSlot:
    def test_run_scheduler_source_registers_retirement_tick_at_0345(self):
        """SCHED2: source-level check -- run_scheduler() is a `while True` loop, so
        it cannot be called directly in a test. Assert its source registers the
        retirement-recommender tick at 03:45 via schedule.every().day.at(...)."""
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:45"' in source or "'03:45'" in source, (
            "app.run_scheduler() does not register a 03:45 job -- the "
            "retirement-recommender tick must be scheduled at 03:45 (staggered "
            "15 minutes after the existing 03:30 incubation slot, same CC-2 "
            "wrapper pattern)."
        )
        assert "_run_retirement_recommender_tick" in source, (
            "app.run_scheduler() does not reference "
            "_run_retirement_recommender_tick -- the 03:45 slot must call the "
            "retirement-recommender tick wrapper specifically."
        )

    def test_retirement_slot_is_distinct_from_incubation_and_lens_pipeline_slots(self):
        """SCHED3: 03:45 must not collide with the existing 03:00 lens-pipeline
        or 03:30 incubation slots -- all three jobs must be independently
        schedulable without same-minute contention."""
        source = inspect.getsource(app_module.run_scheduler)
        assert '"03:00"' in source, (
            "Sanity check failed: the existing 03:00 lens-pipeline slot is "
            "missing from run_scheduler() -- test assumption broken, "
            "investigate before trusting this file's other assertions."
        )
        assert '"03:30"' in source, (
            "Sanity check failed: the existing 03:30 incubation slot is "
            "missing from run_scheduler() -- test assumption broken, "
            "investigate before trusting this file's other assertions."
        )
        assert '"03:45"' in source, (
            "See test_run_scheduler_source_registers_retirement_tick_at_0345."
        )


class TestRetirementRecommendationsTickWorkerCallsProducer:
    def test_tick_worker_calls_build_recommendations_then_persist_recommendations(self):
        """The part that actually closes retire-doc's completeness gap: the
        tick's worker function must call the REAL producer chain
        (advisors.retirement_recommender.build_recommendations() then
        persist_recommendations(<its return value>)) -- not just spawn an
        empty/no-op thread. A thread-spawn-only test (SCHED1/2/3 above) would
        keep passing even if the worker's target function did nothing at all;
        this is the assertion that would actually catch that regression.

        Patches the two producer functions on the REAL advisors.
        retirement_recommender module (not a local binding) so the worker's
        own lazy `from advisors.retirement_recommender import ...` (the CC-2
        pattern _incubation_tick_worker already establishes) picks up the
        patched versions regardless of exactly where inside the worker the
        import statement sits.
        """
        import advisors.retirement_recommender as rr_module

        sentinel_recs = [{"candidate_id": "sentinel-candidate"}]
        build_mock = MagicMock(return_value=sentinel_recs)
        persist_mock = MagicMock(return_value=1)

        with (
            patch.object(rr_module, "build_recommendations", build_mock),
            patch.object(rr_module, "persist_recommendations", persist_mock),
            patch("threading.Thread", side_effect=lambda target, **kw: _ImmediateThread(target)),
        ):
            app_module._run_retirement_recommender_tick()

        assert build_mock.call_count == 1, (
            "The retirement-recommender tick worker never called "
            "build_recommendations() -- the scheduled slot exists but the "
            "producer is never actually invoked, so persisted recommendations "
            "would never be created and the route/panel would render an "
            "honest-empty state forever (the exact completeness gap this "
            "Revise closes)."
        )
        assert persist_mock.call_count == 1, (
            "The retirement-recommender tick worker never called "
            "persist_recommendations() -- build_recommendations() alone does "
            "not write anything to the DB; the worker must persist its "
            "result for the route/panel to ever have something to render."
        )
        persist_call_args = persist_mock.call_args
        passed_recs = (
            persist_call_args.args[0]
            if persist_call_args.args
            else persist_call_args.kwargs.get("recs")
        )
        assert passed_recs == sentinel_recs, (
            "persist_recommendations() must be called with "
            "build_recommendations()'s OWN return value -- got "
            f"{passed_recs!r}, expected the sentinel {sentinel_recs!r} "
            "build_recommendations() was mocked to return. Calling "
            "persist_recommendations() with anything else (e.g. a fresh "
            "empty list, or recomputing) would silently drop real "
            "recommendations."
        )

    def test_tick_worker_never_raises_even_if_the_producer_fails(self):
        """D-1 error contract, matching _incubation_tick_worker's own
        try/except Exception -> log type(exc).__name__ pattern -- a producer
        failure must never crash the scheduler thread."""
        import advisors.retirement_recommender as rr_module

        with (
            patch.object(
                rr_module,
                "build_recommendations",
                MagicMock(side_effect=RuntimeError("simulated producer failure")),
            ),
            patch("threading.Thread", side_effect=lambda target, **kw: _ImmediateThread(target)),
        ):
            try:
                app_module._run_retirement_recommender_tick()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"_run_retirement_recommender_tick() propagated {type(exc).__name__} "
                    "from a producer failure -- the tick worker must catch and log, "
                    "never crash the scheduler thread (D-1, matches "
                    "_incubation_tick_worker's own contract)."
                ) from exc


class _ImmediateThread:
    """Stand-in for threading.Thread that runs its target SYNCHRONOUSLY on
    .start() -- lets these tests observe the worker's real call chain without
    a real background thread's non-determinism, while still exercising the
    exact same target-function code path a real thread would run."""

    def __init__(self, target):
        self._target = target

    def start(self):
        self._target()
