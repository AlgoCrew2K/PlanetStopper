"""RED tests — DE-AUTOTUNE-OOM: autotuner replay parallelism bounding (AC-2/AC-5/AC-7/AC-8).

Background
----------
The weekly walk-forward autotuner OOM-kills on the droplet because
``synthetic_history.py:670`` ``Parallel(n_jobs=_resolve_replay_n_jobs())``
defaults to n_jobs=-1 (all cores) when ``ALPHABOT_MAX_JOBS`` is unset
(CONFIRMED unset on droplet, AC-1 empirical profile 2026-06-29).  On the
2-core / MemoryMax=3.0 GiB droplet this forks 2 tick-data-copying workers
(parent ~2 GB + 2 copies > 3 GB) → the cgroup OOM-kills it before
``save_autotune_run`` is ever reached.

Fix scope (AC-1 verdict — config-only with code-level hardening)
-----------------------------------------------------------------
``autotuner.run_autotuner`` must pass an explicit **bounded** ``n_jobs``
kwarg to its ``generate_synthetic_history(...)`` call (autotuner.py:2158)
so the autotune path is bounded **regardless of whether ``ALPHABOT_MAX_JOBS``
is set**.  The bound value is ``1`` (sequential joblib backend, no fork, peak
2.03 GiB — 990 MB headroom under the 3.0 GiB cap).  Other
``generate_synthetic_history`` callers are untouched — their ``None``/default
continues to resolve via ``_resolve_replay_n_jobs()``.

Tests in this file
------------------
RED tests (5 — currently failing, must go GREEN after at-impl's fix):

T1  AC-2 structural: ``generate_synthetic_history`` accepts ``n_jobs`` kwarg.
T2  AC-2 / no-magic-numbers rule: ``autotuner.py`` exposes a named module-level
    int constant with value == 1 and a name containing JOBS/N_JOBS/REPLAY.
T3  AC-2 core behavioral: ``run_autotuner`` passes ``n_jobs=1`` to
    ``generate_synthetic_history`` when ``ALPHABOT_MAX_JOBS`` is UNSET.
    A config-only (.env) fix MUST fail this test.
T4  AC-2 adversarial: ``run_autotuner`` passes ``n_jobs=1`` even when
    ``ALPHABOT_MAX_JOBS`` is explicitly set to -1.  Guards against a
    "just read env in autotuner" wrong fix.
T5  AC-7 / reproducibility-neutral plumbing: ``generate_synthetic_history``
    passes the ``n_jobs`` kwarg through to ``Parallel`` as its n_jobs
    argument.  Pins the thread-through mechanism.

GREEN guards (2 — pass now, must STAY GREEN through the fix):

G6  AC-5: ``_resolve_replay_n_jobs()`` still returns -1 when env is unset
    (global default not changed by the fix — other callers untouched).
G7  AC-5: ``tests._mem_cap.DEFAULT_CAP_GB`` is not raised above the
    fixture-specified ceiling (24 GB).

Out of scope
------------
AC-3 chunking — explicitly excluded per AC-1 verdict (config alone suffices).
AC-4 behavioral cgroup-bounded full autotune — droplet-side, PM-owned.
AC-6 ``.env`` config — not a code test.

Provenance
----------
No producer-computed value is asserted.  The bound of 1 is schema-derived from
the AC-1 empirical result documented in feature-plans/autotune-oom-memory-bound.md
§AC-1 RESULT and carried in the contract fixture; this file only asserts
relational properties (==, in, <=) against fixture values.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from unittest.mock import patch

import pytest

import synthetic_history

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_CONTRACT_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "replay_n_jobs_bound_contract.json"
)


@pytest.fixture(scope="module")
def contract() -> dict:
    with open(_CONTRACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# T1 — AC-2 structural: generate_synthetic_history must accept n_jobs kwarg
# ---------------------------------------------------------------------------


def test_generate_synthetic_history_api_has_n_jobs_kwarg(contract):
    """AC-2 structural: ``generate_synthetic_history`` must accept an ``n_jobs``
    keyword argument so the autotuner can thread a bounded parallelism degree
    without relying on the ALPHABOT_MAX_JOBS environment variable.

    RED: current signature is ``(bot_state, current_date_str)`` — no ``n_jobs``.
    GREEN: implementer adds ``n_jobs=None`` (keyword-only or positional).
    """
    kwarg_name = contract["generate_synthetic_history_api_contract"]["required_kwarg"]
    sig = inspect.signature(synthetic_history.generate_synthetic_history)
    assert kwarg_name in sig.parameters, (
        f"AC-2: ``synthetic_history.generate_synthetic_history`` must accept "
        f"an ``{kwarg_name}`` kwarg so the autotuner can pass a bounded "
        f"parallelism degree without relying on the ``ALPHABOT_MAX_JOBS`` env "
        f"var. Current parameters: {list(sig.parameters)}. "
        f"The preferred fix adds ``n_jobs: int | None = None`` and uses "
        f"``n_jobs if n_jobs is not None else _resolve_replay_n_jobs()`` at "
        f"the ``Parallel(...)`` call site."
    )


# ---------------------------------------------------------------------------
# T2 — AC-2 / no-magic-numbers: autotuner.py must have a named constant = 1
# ---------------------------------------------------------------------------


def test_autotuner_exposes_named_autotune_replay_n_jobs_constant(contract):
    """AC-2 / project no-magic-numbers rule: ``autotuner.py`` must expose a
    module-level int constant whose value equals the bounded n_jobs (1) AND
    whose name contains at least one of JOBS / N_JOBS / REPLAY.

    Searching by BOTH value AND name pattern prevents a false-positive match
    on an unrelated constant that happens to equal 1 (e.g. ``_PURGE_DAYS``
    if it were 1 — it is not, but the guard is there to survive future changes
    to other constants).

    RED: no such constant exists in autotuner.py at HEAD.
    GREEN: implementer adds e.g. ``_AUTOTUNE_REPLAY_MAX_JOBS = 1`` with a
    source comment explaining the OOM rationale.
    """
    import autotuner

    expected_value = int(contract["autotune_module_contract"]["constant_value"])
    name_patterns = [
        p.upper() for p in contract["autotune_module_contract"]["constant_name_must_contain_any_of"]
    ]

    # Collect all module-level attributes that are plain ints.
    int_constants = {
        name: getattr(autotuner, name)
        for name in dir(autotuner)
        if isinstance(getattr(autotuner, name, None), int)
        and not callable(getattr(autotuner, name))
    }

    # Filter to those matching value AND name pattern.
    matching = {
        name: val
        for name, val in int_constants.items()
        if val == expected_value and any(pattern in name.upper() for pattern in name_patterns)
    }

    assert matching, (
        f"AC-2 / no-magic-numbers: ``autotuner.py`` must expose a module-level "
        f"int constant with value=={expected_value!r} and a name containing at "
        f"least one of {name_patterns}. "
        f"Expected: e.g. ``_AUTOTUNE_REPLAY_MAX_JOBS = {expected_value}`` with a "
        f"comment citing the AC-1 OOM bound (2-core / 3GB cgroup). "
        f"Found int constants matching value {expected_value!r}: "
        f"{[n for n, v in int_constants.items() if v == expected_value]!r} "
        f"(none had a job/replay-related name). "
        f"Found int constants matching name pattern: "
        f"{[n for n in int_constants if any(p in n.upper() for p in name_patterns)]!r}."
    )


# ---------------------------------------------------------------------------
# T3 — AC-2 core behavioral: env unset → n_jobs=1 reaches generate_synthetic_history
# ---------------------------------------------------------------------------


def test_autotune_path_passes_bounded_n_jobs_when_alphabot_max_jobs_unset(monkeypatch, contract):
    """AC-2 core behavioral (adversarial): with ``ALPHABOT_MAX_JOBS`` ABSENT from
    the environment, ``run_autotuner`` must call ``generate_synthetic_history``
    with ``n_jobs=1`` (the bounded value), NOT n_jobs=-1 (all-cores default).

    A fix that ONLY sets ``ALPHABOT_MAX_JOBS=1`` in the droplet ``.env`` (AC-6)
    will FAIL this test — the CODE PATH must be safe without the env var.

    Setup mirrors the existing autotuner test pattern: the spy returns ``{}``
    (falsy) which triggers the early-abort branch
    (``if not history_125d: return``), so no Optuna, DB writes, or network
    calls occur after the spy is invoked.

    RED: current call is ``generate_synthetic_history(bot_state, date)`` with no
    ``n_jobs`` kwarg — the spy captures empty kwargs, assertion fails.
    GREEN: autotuner passes ``n_jobs=<constant>`` (value 1) to the call.
    """
    import autotuner
    from tests.autotuner.conftest import make_phase1_theory_bundle

    # Strip ALPHABOT_MAX_JOBS from env entirely — simulates the droplet state
    # where the env knob is UNSET.
    monkeypatch.delenv("ALPHABOT_MAX_JOBS", raising=False)

    bounded = int(contract["replay_parallelism_contract"]["autotune_safe_n_jobs"])
    kwarg_name = contract["generate_synthetic_history_api_contract"]["required_kwarg"]

    # Spy on generate_synthetic_history — capture ALL kwargs it receives.
    captured_kwargs: list[dict] = []

    def _spy(bot_state, current_date_str, **kwargs):
        captured_kwargs.append(dict(kwargs))
        # Return {} (falsy) → run_autotuner aborts early, no further work needed.
        return {}

    spec_bundle_id = make_phase1_theory_bundle()

    minimal_bot_state: dict = {}

    with patch("autotuner.synthetic_history.generate_synthetic_history", _spy):
        autotuner.run_autotuner(minimal_bot_state, "2024-01-05", [], spec_bundle_id=spec_bundle_id)

    assert captured_kwargs, (
        "AC-2: spy on generate_synthetic_history was never called — "
        "run_autotuner exited before reaching the generate_synthetic_history "
        "call (autotuner.py:2158). Check that the precondition path "
        "(validate_search_space_nn1, spec bundle check) completed successfully "
        "with the provided spec_bundle_id."
    )

    last_call = captured_kwargs[-1]
    assert kwarg_name in last_call, (
        f"AC-2: run_autotuner must pass ``{kwarg_name}=`` to "
        f"generate_synthetic_history when ALPHABOT_MAX_JOBS is unset. "
        f"Got kwargs: {last_call!r}. "
        f"A fix that ONLY sets ALPHABOT_MAX_JOBS=1 in .env (AC-6 defense-in-depth) "
        f"would NOT pass this test — the code path must be safe WITHOUT the env var."
    )
    assert last_call[kwarg_name] == bounded, (
        f"AC-2: run_autotuner must pass {kwarg_name}={bounded!r} (the bounded value) "
        f"to generate_synthetic_history when ALPHABOT_MAX_JOBS is unset. "
        f"Got {kwarg_name}={last_call[kwarg_name]!r}. "
        f"Value -1 (all-cores) forks 2 workers on the 2-core droplet → "
        f"parent ~2GB + 2 worker copies > 3GB MemoryMax → OOM-kill (AC-1 root cause)."
    )


# ---------------------------------------------------------------------------
# T4 — AC-2 adversarial: env set to -1 → autotuner STILL passes n_jobs=1
# ---------------------------------------------------------------------------


def test_autotune_path_passes_bounded_n_jobs_even_when_env_set_to_minus_one(monkeypatch, contract):
    """AC-2 adversarial: even with ``ALPHABOT_MAX_JOBS=-1`` set explicitly,
    ``run_autotuner`` must STILL pass ``n_jobs=1`` to
    ``generate_synthetic_history``.

    Guards against a wrong fix that reads the env var inside run_autotuner and
    passes it through — that fix would fail when an operator accidentally
    exports ALPHABOT_MAX_JOBS=-1 (e.g. inheriting a shell profile) after AC-6
    is applied.  The autotuner must be env-independent; AC-6 (.env config) is
    defense-in-depth ONLY, not the safety mechanism.

    RED: current call passes no ``n_jobs`` kwarg at all.
    GREEN: autotuner uses the named module-level constant (value=1), not the env.
    """
    import autotuner
    from tests.autotuner.conftest import make_phase1_theory_bundle

    # Adversarially set the env knob to the dangerous all-cores value.
    monkeypatch.setenv("ALPHABOT_MAX_JOBS", "-1")

    bounded = int(contract["replay_parallelism_contract"]["autotune_safe_n_jobs"])
    kwarg_name = contract["generate_synthetic_history_api_contract"]["required_kwarg"]

    captured_kwargs: list[dict] = []

    def _spy(bot_state, current_date_str, **kwargs):
        captured_kwargs.append(dict(kwargs))
        return {}

    spec_bundle_id = make_phase1_theory_bundle()

    with patch("autotuner.synthetic_history.generate_synthetic_history", _spy):
        autotuner.run_autotuner({}, "2024-01-05", [], spec_bundle_id=spec_bundle_id)

    assert captured_kwargs, (
        "AC-2: spy was never called — run_autotuner exited before the "
        "generate_synthetic_history call. See T3 for diagnosis."
    )

    last_call = captured_kwargs[-1]
    assert kwarg_name in last_call, (
        f"AC-2 adversarial: run_autotuner must pass ``{kwarg_name}=`` to "
        f"generate_synthetic_history even when ALPHABOT_MAX_JOBS=-1 is set. "
        f"Got kwargs: {last_call!r}."
    )
    assert last_call[kwarg_name] == bounded, (
        f"AC-2 adversarial: run_autotuner must pass {kwarg_name}={bounded!r} "
        f"regardless of ALPHABOT_MAX_JOBS. "
        f"Got {kwarg_name}={last_call[kwarg_name]!r} (env=-1 bled through — "
        f"the code must use the named constant, not the env resolver)."
    )


# ---------------------------------------------------------------------------
# T5 — AC-7 plumbing: generate_synthetic_history threads n_jobs kwarg → Parallel
# ---------------------------------------------------------------------------


def test_generate_synthetic_history_passes_n_jobs_kwarg_to_parallel(
    monkeypatch, contract, tmp_path
):
    """AC-7 / reproducibility-neutral plumbing: when called with ``n_jobs=1``,
    ``generate_synthetic_history`` must pass ``n_jobs=1`` to the ``Parallel(...)``
    call at synthetic_history.py:670.

    This pins the thread-through mechanism.  The mathematical reproducibility
    property (content-derived MC seeds, order-independent) is already
    code-documented at synthetic_history.py:30-35 and empirically confirmed at
    AC-4 (cgroup-bounded full autotune on the droplet).

    Setup: ``_WALK_FORWARD_TRADING_DAYS`` is temporarily lowered to 2 so only
    2 dates of fake intraday data are needed to pass the shortfall check.
    ``fetch_daily_bars_with_floor`` and ``fetch_bars`` (intraday) are patched
    to avoid network calls.  ``Parallel`` is patched to a capturing stub that
    records ``n_jobs`` and returns an empty result list.

    RED: ``generate_synthetic_history`` does not accept ``n_jobs`` → TypeError.
    GREEN: kwarg is threaded through and captured ``Parallel`` n_jobs == 1.
    """
    # Temporarily lower the walk-forward window so 2 fake intraday dates suffice
    # to pass the shortfall-guard (``len(intraday_dates) < _WALK_FORWARD_TRADING_DAYS``).
    # This is the canonical seam for replay-window unit tests (avoids generating
    # 250 real trading days of fake data).
    monkeypatch.setattr(synthetic_history, "_WALK_FORWARD_TRADING_DAYS", 2)

    # Suppress cache reads/writes — test runs in tmp_path to avoid polluting
    # the worktree cache/ dir; os.makedirs will not raise there.
    monkeypatch.chdir(tmp_path)

    # Minimal fake intraday data: 2 dates × 1 bar each.
    # Bars need t[:10] (date), c (close, float), v (volume, float) for pandas
    # processing at synthetic_history.py:588-595.
    fake_intraday = {
        "SPY": [
            {"t": "2024-01-02T09:30:00", "c": 100.0, "v": 1000.0},
            {"t": "2024-01-03T09:30:00", "c": 101.0, "v": 1000.0},
        ]
    }

    # Fake daily bars: empty (``historical_daily`` empty → ``process_day``
    # returns early with ``{}`` which is harmless — Parallel still runs).
    monkeypatch.setattr(synthetic_history, "fetch_daily_bars_with_floor", lambda _fn, _dt: {})
    monkeypatch.setattr(synthetic_history, "fetch_bars", lambda *_a, **_kw: fake_intraday)

    # Capture the n_jobs Parallel receives; return empty list (no actual workers).
    captured_parallel_n_jobs: list[int] = []

    class _CapturingParallel:
        def __init__(self, n_jobs=None, **_kw):
            captured_parallel_n_jobs.append(n_jobs)

        def __call__(self, iterable):
            # Materialise the generator so the function body runs to its return.
            list(iterable)
            return []

    monkeypatch.setattr(synthetic_history, "Parallel", _CapturingParallel)

    kwarg_name = contract["generate_synthetic_history_api_contract"]["required_kwarg"]
    bounded = int(contract["replay_parallelism_contract"]["autotune_safe_n_jobs"])

    minimal_bot_state = {"sym-test": {"current_holdings": [{"ticker": "SPY", "weight": 1.0}]}}

    # T1 failing means this call will also raise TypeError (no n_jobs param).
    # Once T1 goes GREEN (param added), this test transitions to its real assertion.
    result = synthetic_history.generate_synthetic_history(
        minimal_bot_state, "2024-01-05", **{kwarg_name: bounded}
    )

    assert captured_parallel_n_jobs, (
        f"AC-7: ``Parallel`` was not called inside generate_synthetic_history — "
        f"the function may have returned early (cache hit, empty intraday_dates, "
        f"or shortfall raised). Check the fake intraday data and that "
        f"_WALK_FORWARD_TRADING_DAYS was lowered correctly. result={result!r}"
    )
    assert captured_parallel_n_jobs[-1] == bounded, (
        f"AC-7 plumbing: generate_synthetic_history must pass ``{kwarg_name}={bounded!r}`` "
        f"through to ``Parallel(n_jobs=...)`` when the kwarg is provided. "
        f"Got Parallel(n_jobs={captured_parallel_n_jobs[-1]!r}). "
        f"Ensure the implementation uses "
        f"``effective = n_jobs if n_jobs is not None else _resolve_replay_n_jobs()`` "
        f"and passes ``effective`` to Parallel, not the resolver directly."
    )


# ---------------------------------------------------------------------------
# G6 — AC-5 guard: global _resolve_replay_n_jobs default is NOT changed
# ---------------------------------------------------------------------------


def test_resolve_replay_n_jobs_global_default_unchanged_when_env_unset(monkeypatch, contract):
    """AC-5 guard: ``_resolve_replay_n_jobs()`` must STILL return -1 when
    ``ALPHABOT_MAX_JOBS`` is unset after the fix is applied.

    The fix scopes the bound to the autotune path (threads n_jobs=1 as a kwarg).
    It must NOT change the module-level default, which other callers rely on
    for their own parallelism behaviour.

    Currently GREEN (the function already returns -1 when env is unset).
    FAILS if the implementer "fixes" the OOM by changing the global default
    (out-of-scope, breaks non-autotune callers).
    """
    monkeypatch.delenv("ALPHABOT_MAX_JOBS", raising=False)

    expected = int(contract["replay_parallelism_contract"]["global_default_when_unset"])
    actual = synthetic_history._resolve_replay_n_jobs()

    assert actual == expected, (
        f"AC-5 guard: ``synthetic_history._resolve_replay_n_jobs()`` must return "
        f"{expected!r} when ALPHABOT_MAX_JOBS is unset (the default that pre-existed "
        f"the fix). Got {actual!r}. "
        f"The OOM fix MUST scope the bound to the autotune call site only — "
        f"changing the global default is out-of-scope and breaks other callers."
    )


# ---------------------------------------------------------------------------
# G7 — AC-5 guard: test-suite memory cap is not raised
# ---------------------------------------------------------------------------


def test_mem_cap_default_not_raised_above_contract_ceiling(contract):
    """AC-5 guard: ``tests._mem_cap.DEFAULT_CAP_GB`` must not exceed the
    contract ceiling (24 GB).

    This guards against a wrong fix that attempts to "solve" the OOM by
    raising the test-suite memory cap (which would risk re-enabling uncapped
    fan-out and the Kernel-Power 41 host hard-reboots from 2026-06-21).

    Currently GREEN.  FAILS if someone raises the cap constant.
    """
    import importlib
    import sys

    # Import without executing the cap-install side effects (that requires
    # Windows ctypes); we only need the module-level constant.
    mem_cap = importlib.import_module("tests._mem_cap")

    ceiling = float(contract["mem_cap_guard"]["max_cap_gb"])
    actual = float(mem_cap.DEFAULT_CAP_GB)

    assert actual <= ceiling, (
        f"AC-5 guard: tests._mem_cap.DEFAULT_CAP_GB must be <= {ceiling} GB "
        f"(the operator-approved dev-host ceiling, 67.8 GB RAM). "
        f"Got {actual} GB. "
        f"Raising this cap risks uncapped pytest fan-out and host hard-reboots "
        f"(2026-06-21 RCA). The OOM fix belongs in the autotuner call site, "
        f"not in the test-suite cap."
    )
