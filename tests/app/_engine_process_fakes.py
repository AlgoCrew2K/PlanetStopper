"""Fake engine processes for the tests of how long the daemon lets an engine run live.

Never a real engine run. `FakeEngines` stands in for `subprocess.Popen` and `subprocess.run`
together, so the daemon is exercised whichever of the two it uses to start the engine; a process
is a `FakeProcess` that carries what a real one carries (pid, `returncode`, `poll`, `wait`,
`communicate`, `terminate`, `kill`, `send_signal`, a context manager) and that records every
signal it receives, with the host's virtual monotonic stamp and the real one.

How long a process lives is virtual time, the clock the daemon reads (`app._monotonic`):

  * a process whose behaviour has a `duration_s` exits 0 once that much virtual time has passed
    since it started (it is noticed by the next `poll`, `wait` or `communicate`);
  * a process with no duration hangs until it is signalled: `terminate()` ends it with status -15
    unless the behaviour `ignores_terminate`, and `kill()` always ends it with status -9;
  * `wait(timeout)` and `communicate(timeout)` raise `subprocess.TimeoutExpired` when the timeout
    has passed on the virtual clock or on the real one, whichever comes first, so a daemon that
    waits ten real seconds and one that waits ten virtual seconds both get their timeout.

The engine's exit status is the only thing a crash gives the daemon, and that is all these fakes
give it (SIGTERM / SIGKILL statuses); nothing here aborts or faults a real process.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import app as app_module

# A wait with no timeout that never ends is a hung test, not a hung engine.
HANG_GUARD_S = 60.0


@dataclass(frozen=True)
class Behaviour:
    """How one fake process behaves: `duration_s` None hangs it."""

    duration_s: float | None = 25.0
    ignores_terminate: bool = False


ORDINARY = Behaviour()
HANGS = Behaviour(duration_s=None)
HANGS_AND_IGNORES_TERMINATE = Behaviour(duration_s=None, ignores_terminate=True)


@dataclass
class Signal:
    virtual_mono: float
    real_mono: float


class FakeProcess:
    def __init__(self, engines: FakeEngines, index: int, args, behaviour: Behaviour) -> None:
        self.engines = engines
        self.index = index
        self.args = args
        self.pid = 40_000 + index
        self.behaviour = behaviour
        self.returncode: int | None = None
        self.stdout = None
        self.stderr = None
        self.started_virtual = app_module._monotonic()
        self.started_et = app_module.datetime.now(app_module._ET)
        self.terminate_calls: list[Signal] = []
        self.kill_calls: list[Signal] = []
        self._ended = threading.Event()

    # -- state ----------------------------------------------------------

    def _finish(self, status: int) -> None:
        if self.returncode is None:
            self.returncode = status
            self._ended.set()

    def _apply_natural_exit(self) -> None:
        duration = self.behaviour.duration_s
        if (
            self.returncode is None
            and duration is not None
            and app_module._monotonic() - self.started_virtual >= duration
        ):
            self._finish(0)

    def poll(self) -> int | None:
        self._apply_natural_exit()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        virtual_start, real_start = app_module._monotonic(), time.monotonic()
        while True:
            self._apply_natural_exit()
            if self.returncode is not None:
                return self.returncode
            waited_virtual = app_module._monotonic() - virtual_start
            waited_real = time.monotonic() - real_start
            if timeout is not None and (waited_virtual >= timeout or waited_real >= timeout):
                raise subprocess.TimeoutExpired(self.args, timeout)
            if timeout is None and waited_real > HANG_GUARD_S:
                raise AssertionError(f"fake process {self.index} was waited on with no timeout")
            time.sleep(0.002)

    def communicate(self, input=None, timeout: float | None = None):
        self.wait(timeout)
        return (None, None)

    # -- signals --------------------------------------------------------

    def _stamp(self) -> Signal:
        return Signal(app_module._monotonic(), time.monotonic())

    def terminate(self) -> None:
        self.terminate_calls.append(self._stamp())
        if not self.behaviour.ignores_terminate:
            self._finish(-signal.SIGTERM)

    def kill(self) -> None:
        self.kill_calls.append(self._stamp())
        self._finish(-9)

    def send_signal(self, sig: int) -> None:
        if sig == signal.SIGTERM:
            self.terminate()
        else:
            self.kill()

    def __enter__(self) -> FakeProcess:
        return self

    def __exit__(self, *_exc) -> None:
        if self.returncode is None:
            self.wait()

    @property
    def alive(self) -> bool:
        return self.poll() is None


@dataclass
class FakeEngines:
    """`behaviour(index)` says how the index-th process (from 0) behaves."""

    behaviour: Callable[[int], Behaviour] = lambda _index: ORDINARY
    runs: list[FakeProcess] = field(default_factory=list)
    changed: threading.Condition = field(default_factory=threading.Condition)

    def popen(self, args, *_a, **_kw) -> FakeProcess:
        with self.changed:
            index = len(self.runs)
            process = FakeProcess(self, index, args, self.behaviour(index))
            self.runs.append(process)
            self.changed.notify_all()
        return process

    def __call__(self, args, *_a, check: bool = False, timeout: float | None = None, **_kw):
        """subprocess.run as the standard library runs it."""
        with self.popen(args) as process:
            try:
                process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
        status = process.poll()
        if check and status:
            raise subprocess.CalledProcessError(status, args)
        return subprocess.CompletedProcess(args, status)

    def install(self, monkeypatch) -> FakeEngines:
        monkeypatch.setattr(subprocess, "Popen", self.popen)
        monkeypatch.setattr(subprocess, "run", self)
        return self

    def release_all(self) -> None:
        """End every process still running, for the end of a test."""
        for process in self.runs:
            process.kill()
