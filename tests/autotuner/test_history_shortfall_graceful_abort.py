"""Cluster 5 — RED: history-shortfall graceful, surfaced autotuner abort.

team-lead ruling (Option b) on risk-engine-specialist's domain question.

THE DESIGN
----------
synthetic_history.fetch_daily_bars_with_floor raises on a persistent daily-bar
shortfall (after the bounded widen+refetch). team-lead ruled:

  1. The helper KEEPS raising — a clean hard signal at the helper boundary.
  2. The INTRADAY shortfall guard in generate_synthetic_history must be made
     CONSISTENT — it currently only logs an ERROR and proceeds with a
     degenerate replay slice; it must raise the SAME shortfall signal so both
     the daily/warmup axis and the intraday/replay-slice axis surface
     identically.
  3. autotuner.run_autotuner CATCHES that shortfall exception and converts it
     to its existing graceful-abort contract — return, no propagation — so the
     caller chain continues and reporting.send_eod_discord_post still fires.
     The autotuner is a SECONDARY optimisation step; a data shortfall in it
     must not take down the operator's PRIMARY daily EOD report.
  4. BINDING — the abort must NOT be silent. The operator must be explicitly
     told "autotuner aborted — persistent history shortfall" — the abort
     REASON must reach the operator surface, not merely have autotuner changes
     omitted. A graceful abort the operator cannot distinguish from "autotuner
     ran, no changes" would itself be a silent failure.

WHAT THESE TESTS PIN
--------------------
  - synthetic_history exposes a NAMED shortfall exception type (not a bare
    RuntimeError with a magic string) so the run_autotuner catch is precise.
  - run_autotuner does NOT propagate the shortfall exception — it returns.
  - run_autotuner's return on a shortfall abort CARRIES the abort reason (it
    is not a bare falsy None indistinguishable from "ran, no changes") so the
    reason can reach the EOD operator surface.
  - The catch in autotuner.py is NARROW — it catches the specific shortfall
    type, never a bare `except Exception` that would swallow unrelated
    autotuner failures into a false "history shortfall" abort.
  - The daily and intraday shortfall axes both raise the shortfall type — the
    intraday guard is no longer log-and-proceed-degenerate.

PROVENANCE
----------
No producer-computed value is asserted. The tests drive run_autotuner with a
generate_synthetic_history monkeypatched to raise the shortfall exception
(established pattern: patch autotuner.synthetic_history.generate_synthetic_-
history) and assert structural outcomes — no raise propagates, the return
carries a reason, the catch is narrow. Static AST analysis pins the narrow
catch and the intraday-guard consistency. pytest.approx is not relevant.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import autotuner
import synthetic_history


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


# ---------------------------------------------------------------------------
# The named shortfall exception type.
# ---------------------------------------------------------------------------


def _resolve_shortfall_exception() -> type:
    """Return the named history-shortfall exception type synthetic_history must
    expose.

    team-lead / risk-engine-specialist: a named exception type (e.g.
    HistoryShortfallError) — NOT a bare RuntimeError with a magic string — so
    the run_autotuner catch site is precise. The implementer chooses the exact
    name; this resolver accepts the documented candidates.
    """
    for name in (
        "HistoryShortfallError",
        "SyntheticHistoryShortfallError",
        "FetchWindowShortfallError",
        "InsufficientHistoryError",
    ):
        exc = getattr(synthetic_history, name, None)
        if isinstance(exc, type) and issubclass(exc, BaseException):
            return exc
    pytest.fail(
        "synthetic_history must expose a NAMED history-shortfall exception "
        "type (expected one of: HistoryShortfallError / "
        "SyntheticHistoryShortfallError / FetchWindowShortfallError / "
        "InsufficientHistoryError). A bare RuntimeError + magic string makes "
        "the run_autotuner catch imprecise — the catch must target a specific "
        "type so it cannot swallow unrelated autotuner failures."
    )


def test_synthetic_history_exposes_a_named_shortfall_exception() -> None:
    """synthetic_history must expose a named shortfall exception type."""
    exc = _resolve_shortfall_exception()
    # It must be a genuine Exception subclass (catchable, not BaseException-only).
    assert issubclass(exc, Exception), (
        f"the history-shortfall exception {exc!r} must subclass Exception so "
        f"run_autotuner can catch it with a narrow except clause."
    )


def test_fetch_daily_bars_with_floor_raises_the_named_shortfall_type() -> None:
    """The widen+refetch helper must raise the NAMED shortfall type on a
    persistent shortfall — not a bare RuntimeError.

    risk-engine-specialist: the helper's hard signal must be the named type so
    the run_autotuner catch is precise. A persistently-short injected fetch
    must raise the resolved shortfall exception (or a subclass).
    """
    exc_type = _resolve_shortfall_exception()
    required = synthetic_history._REQUIRED_FETCH_TRADING_DAYS

    def always_short_fetch(*args, **kwargs):
        # Return a payload well under the floor on every (widened) attempt.
        import datetime as _dt

        bars = []
        d = _dt.date(2026, 5, 15)
        emitted = 0
        while emitted < required - 30:
            if d.weekday() < 5:
                bars.append({"t": f"{d.isoformat()}T00:00:00Z", "c": 1.0,
                             "h": 1.0, "l": 1.0, "o": 1.0, "v": 1, "vw": 1.0})
                emitted += 1
            d -= _dt.timedelta(days=1)
        return {"SPY": list(reversed(bars))}

    with pytest.raises(exc_type):
        synthetic_history.fetch_daily_bars_with_floor(always_short_fetch)


# ---------------------------------------------------------------------------
# run_autotuner graceful, surfaced abort.
# ---------------------------------------------------------------------------


def _minimal_bot_state() -> dict:
    """A minimal bot_state with one symphony — enough for run_autotuner to
    reach the generate_synthetic_history call."""
    return {
        "sym-A": {
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
        }
    }


def test_run_autotuner_does_not_propagate_a_history_shortfall(monkeypatch) -> None:
    """run_autotuner must CATCH the history-shortfall exception — it must not
    propagate past run_autotuner.

    team-lead: the autotuner is a secondary step; a history shortfall in it
    must not take down the EOD operator report. An uncaught exception
    propagating past run_autotuner would crash the caller chain before
    reporting.send_eod_discord_post.

    monkeypatch generate_synthetic_history to raise the shortfall; assert
    run_autotuner returns rather than raising.
    """
    exc_type = _resolve_shortfall_exception()

    def _raise_shortfall(*args, **kwargs):
        raise exc_type(
            "synthetic_history: daily-bar fetch remained short of the "
            "trading-day floor after the bounded widen+refetch attempts"
        )

    monkeypatch.setattr(
        autotuner.synthetic_history,
        "generate_synthetic_history",
        _raise_shortfall,
    )

    # Must NOT raise — run_autotuner converts the shortfall to a graceful abort.
    try:
        result = autotuner.run_autotuner(
            _minimal_bot_state(), "2026-05-10", ["acc-1"]
        )
    except exc_type as e:  # pragma: no cover - this is the RED failure path
        pytest.fail(
            f"run_autotuner propagated the history-shortfall exception "
            f"({e!r}) instead of catching it and aborting gracefully. "
            f"team-lead ruling (b): the shortfall must be caught and "
            f"converted to a graceful abort so the EOD operator report "
            f"still fires."
        )
    # The graceful abort returns; the specific return value is asserted next.
    assert result is None or result is not None  # reachability sanity only


def test_run_autotuner_abort_carries_the_shortfall_reason(monkeypatch) -> None:
    """run_autotuner's return on a shortfall abort must CARRY the abort reason.

    BINDING (team-lead): the abort must not be silent. A bare None return is
    indistinguishable from "autotuner ran, produced no changes" — the operator
    cannot tell an abort happened. The return must carry the shortfall reason
    so it can reach the EOD operator surface.

    This test asserts the return value, on a shortfall abort, exposes a reason
    string that names the shortfall. The exact return shape is the
    implementer's choice (a dict with an 'aborted'/'reason' key, a small
    result object with a reason attribute, etc.); this test accepts any shape
    from which a shortfall-naming reason string is retrievable.
    """
    exc_type = _resolve_shortfall_exception()
    reason_text = (
        "daily-bar fetch remained short of the trading-day floor after the "
        "bounded widen+refetch attempts"
    )

    def _raise_shortfall(*args, **kwargs):
        raise exc_type(f"synthetic_history: {reason_text}")

    monkeypatch.setattr(
        autotuner.synthetic_history,
        "generate_synthetic_history",
        _raise_shortfall,
    )

    result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"]
    )

    # The abort return must NOT be a bare falsy None — that is the silent-abort
    # failure mode the ruling forbids.
    assert result is not None, (
        "run_autotuner returned a bare None on a history-shortfall abort. "
        "team-lead's BINDING requirement: the abort must be SURFACED — the "
        "return must carry the shortfall reason so the operator is told "
        "'autotuner aborted — persistent history shortfall', not left unable "
        "to distinguish the abort from a no-op run."
    )

    # Extract a reason string from whatever shape the implementer chose.
    reason_str: str | None = None
    if isinstance(result, dict):
        for key in ("reason", "abort_reason", "message", "error", "detail"):
            val = result.get(key)
            if isinstance(val, str) and val:
                reason_str = val
                break
        # also accept the whole dict stringified if it carries the text
        if reason_str is None:
            joined = " ".join(
                str(v) for v in result.values() if isinstance(v, str)
            )
            if joined:
                reason_str = joined
    elif isinstance(result, str):
        reason_str = result
    else:
        for attr in ("reason", "abort_reason", "message", "detail"):
            val = getattr(result, attr, None)
            if isinstance(val, str) and val:
                reason_str = val
                break

    assert reason_str is not None, (
        f"run_autotuner's shortfall-abort return ({result!r}) does not expose "
        f"a retrievable reason string. The abort reason must be carried on the "
        f"return so reporting.send_eod_discord_post can surface it to the "
        f"operator."
    )
    low = reason_str.lower()
    assert any(
        token in low
        for token in ("shortfall", "history", "trading day", "trading-day", "floor")
    ), (
        f"run_autotuner's shortfall-abort reason ({reason_str!r}) does not "
        f"name the history shortfall. The operator-facing message must "
        f"explicitly state the abort was a persistent history shortfall."
    )


def test_run_autotuner_abort_return_differs_from_a_normal_no_change_run(
    monkeypatch,
) -> None:
    """The shortfall-abort return must be DISTINGUISHABLE from a normal run
    that simply produced no changes.

    The whole point of the BINDING surfaced-abort requirement: the operator
    must be able to tell "autotuner aborted on a data shortfall" apart from
    "autotuner ran fine and changed nothing". If both produce the same return
    value the abort is effectively silent.

    A normal no-change run returns its usual no-change value (falsy / empty /
    None per the existing contract). The shortfall abort must NOT collapse to
    that same value — it must be a distinct, reason-carrying value.
    """
    exc_type = _resolve_shortfall_exception()

    def _raise_shortfall(*args, **kwargs):
        raise exc_type("synthetic_history: persistent history shortfall")

    def _empty_history(*args, **kwargs):
        # The pre-existing "no usable history" contract: a falsy return that
        # run_autotuner already handles via `if not history_125d: return`.
        return {}

    # Shortfall abort.
    monkeypatch.setattr(
        autotuner.synthetic_history,
        "generate_synthetic_history",
        _raise_shortfall,
    )
    shortfall_result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"]
    )

    # Pre-existing empty-history graceful return (NOT a shortfall — a different
    # abort cause). This already returns bare None today.
    monkeypatch.setattr(
        autotuner.synthetic_history,
        "generate_synthetic_history",
        _empty_history,
    )
    empty_result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"]
    )

    assert shortfall_result != empty_result, (
        f"the history-shortfall abort return ({shortfall_result!r}) is "
        f"indistinguishable from a non-shortfall empty-history return "
        f"({empty_result!r}). The surfaced-abort requirement means the "
        f"shortfall abort must be a DISTINCT, reason-carrying value the "
        f"operator surface can render as an explicit abort notice."
    )


# ---------------------------------------------------------------------------
# Static guards — narrow catch + intraday-guard consistency.
# ---------------------------------------------------------------------------


def test_run_autotuner_catch_of_shortfall_is_narrow_not_bare_except() -> None:
    """The shortfall catch in autotuner.py must be NARROW — it must name the
    specific shortfall exception type, never a bare `except:` or
    `except Exception`.

    risk-engine-specialist: a bare catch at the run_autotuner call site would
    swallow unrelated autotuner failures into a false "history shortfall"
    abort. The catch must target the specific named type.

    Static check: locate the try/except that wraps the
    generate_synthetic_history call in run_autotuner; assert its handler
    catches a specific named exception, not bare Exception/BaseException.
    """
    tree = ast.parse(
        _AUTOTUNER_PATH.read_text(encoding="utf-8"), filename=str(_AUTOTUNER_PATH)
    )

    # Find every Try whose body references generate_synthetic_history.
    shortfall_tries: list[ast.Try] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and (
                    (
                        isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "generate_synthetic_history"
                    )
                    or (
                        isinstance(sub.func, ast.Name)
                        and sub.func.id == "generate_synthetic_history"
                    )
                )
            ):
                shortfall_tries.append(node)
                break

    assert shortfall_tries, (
        "autotuner.run_autotuner does not wrap the generate_synthetic_history "
        "call in a try/except. team-lead ruling (b): the history-shortfall "
        "exception must be CAUGHT and converted to a graceful abort — that "
        "requires a try/except around the synthetic-history call."
    )

    # Every such Try's handlers must catch a specific named type — not bare.
    for try_node in shortfall_tries:
        for handler in try_node.handlers:
            exc = handler.type
            assert exc is not None, (
                "autotuner.run_autotuner wraps generate_synthetic_history in "
                "a BARE `except:` — it must catch the specific named "
                "history-shortfall exception type, not everything."
            )
            caught_names: set[str] = set()
            targets = exc.elts if isinstance(exc, ast.Tuple) else [exc]
            for t in targets:
                if isinstance(t, ast.Name):
                    caught_names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    caught_names.add(t.attr)
            assert not (
                caught_names & {"Exception", "BaseException"}
            ), (
                f"autotuner.run_autotuner's try/except around "
                f"generate_synthetic_history catches the over-broad "
                f"{caught_names & {'Exception', 'BaseException'}} — it must "
                f"catch ONLY the specific named history-shortfall exception "
                f"type so unrelated autotuner failures are not swallowed "
                f"into a false 'history shortfall' abort."
            )
            # The handler must catch a shortfall-named type.
            assert any(
                ("shortfall" in n.lower() or "insufficienthistory" in n.lower())
                for n in caught_names
            ), (
                f"autotuner.run_autotuner's try/except around "
                f"generate_synthetic_history catches {sorted(caught_names)} — "
                f"none names the history-shortfall exception. The catch must "
                f"target the specific shortfall type."
            )


def test_intraday_shortfall_guard_raises_consistently_with_the_daily_axis() -> None:
    """The intraday shortfall guard in synthetic_history must RAISE the
    shortfall exception — consistent with the daily/warmup axis.

    team-lead ruling (b) part 2: the intraday guard currently only logs an
    ERROR and proceeds with a degenerate replay slice — a latent instance of
    the same silent-under-floor defect BLOCKER 2 closed on the daily axis.
    Both shortfall axes must surface identically: the intraday guard must
    raise the shortfall exception, not log-and-proceed.

    Static check: generate_synthetic_history must contain a `raise` of the
    shortfall exception type reachable from the intraday-slice length check —
    a guard that only calls logging.error on the intraday shortfall and falls
    through fails this.
    """
    src = _SYNTH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_SYNTH_PATH))

    exc_type = _resolve_shortfall_exception()
    exc_name = exc_type.__name__

    gen = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "generate_synthetic_history"
        ):
            gen = node
            break
    assert gen is not None, (
        "synthetic_history.generate_synthetic_history not found."
    )

    # Find an `if` whose test is a len()-vs-floor comparison on the intraday
    # slice, and assert its body raises the shortfall exception.
    intraday_guard_raises = False
    for node in ast.walk(gen):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        operands = [test.left, *test.comparators]
        touches_intraday = False
        has_len = False
        for o in operands:
            for sub in ast.walk(o):
                if isinstance(sub, ast.Name) and "intraday" in sub.id:
                    touches_intraday = True
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "len"
                ):
                    has_len = True
        if not (touches_intraday and has_len):
            continue
        # Body must raise the shortfall exception.
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise) and sub.exc is not None:
                exc = sub.exc
                raised = exc.func if isinstance(exc, ast.Call) else exc
                if (
                    isinstance(raised, ast.Name) and raised.id == exc_name
                ) or (
                    isinstance(raised, ast.Attribute) and raised.attr == exc_name
                ):
                    intraday_guard_raises = True

    assert intraday_guard_raises, (
        f"generate_synthetic_history's intraday-slice shortfall guard does "
        f"not raise {exc_name}. team-lead ruling (b): the intraday guard must "
        f"be made CONSISTENT with the daily axis — it must RAISE the shortfall "
        f"exception, not log-an-ERROR-and-proceed with a degenerate replay "
        f"slice (a latent silent-under-floor defect)."
    )
