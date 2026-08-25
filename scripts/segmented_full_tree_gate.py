"""
Segmented full-tree pytest gate — a SAFE local pre-merge accelerant.

WHY THIS SCRIPT EXISTS
-----------------------
The project's standing addopts ("-n 2 --dist loadfile") already caps xdist at
2 workers to avoid the >90GB fan-out that caused two host bugchecks (see the
xdist notes in pyproject.toml). Even at that cap, running the ENTIRE tree
(~714 files / ~11,600 items) in a single long-lived pytest process still
accumulates enough resident memory over the run to die with MemoryError most
of the time on this host (~85% failure rate, empirically). A prototype
verifier run proved that splitting the tree into chunks and giving EACH
chunk a genuinely FRESH subprocess (so memory actually releases between
chunks, which it does not within one process) completes clean.

CI (GitHub Actions, ubuntu) remains the authoritative gate. This script is a
LOCAL pre-merge accelerant only — it gives a fast, safe, full-tree-equivalent
verdict without needing to push and wait on CI for every iteration.

SAFETY MODEL
------------
- Every chunk runs in its OWN subprocess, SEQUENTIALLY, never in parallel or
  backgrounded — that is what makes memory actually release between chunks.
- Every chunk's pytest invocation passes an explicit "-n0" (single process,
  no xdist workers at all) — this OVERRIDES the ini "-n 2" (a later "-n" on
  the command line wins over the ini addopts value). "-n auto"/uncapped
  xdist is exactly the class of failure this project's tests/conftest.py
  _assert_safe_worker_count() guard exists to prevent; this script adds a
  second, independent line of defense at the orchestration layer.
- Every chunk passes at least one explicit path (a tests/ subdirectory or
  individual loose files) — the bare "tests" root is never passed as an
  argument, so a chunk can never silently expand into a full-tree run.
- Chunks are whole tests/ subdirectories (or the loose top-level files),
  discovered FRESH at runtime via glob — never a hardcoded manifest — and a
  unit is always placed atomically into exactly one chunk (never split),
  which keeps each subdirectory's own conftest scoping coherent and keeps
  failures legible (grouped by feature area).
- Before every chunk, this script refuses to start if another pytest
  process is already running (best-effort via psutil, matching the process
  NAME or individual cmdline TOKENS against "pytest"/"pytest.exe"/"py.test"
  or a "-m pytest" pair — never a blind substring search over the whole
  joined cmdline, which would false-positive on any path merely containing
  the text "pytest", e.g. this very worktree is named "pytest-gate") —
  running two pytest invocations concurrently on this host is exactly the
  kind of memory-pressure incident this tool exists to prevent.
- On every chunk subprocess's end — normal completion OR timeout — this
  script kills the ENTIRE process tree rooted at that chunk's pytest PID
  (via psutil), not just the immediate pytest process. "-n0" only rules out
  xdist's OWN worker pool; it does nothing to stop a TEST BODY from forking
  its own worker pool (joblib n_jobs=-1, optuna study.optimize). A survivor
  here would otherwise still be resident when the NEXT chunk starts,
  re-creating the exact >90GB multi-process fan-out this tool exists to
  prevent.
- A HARD per-chunk file ceiling (MAX_FILES_PER_CHUNK) is enforced
  INDEPENDENTLY of "--chunks": the requested chunk count is a preference,
  never a way to force fewer/larger chunks than proven safe. "--chunks 1"
  (or 2) against the real ~712-file tree is silently raised to enough
  chunks to respect the ceiling -- NOT packed into one giant -n0 process.
  build_argv() also fail-closed-refuses (raises) to spawn any chunk over
  the ceiling as a second, independent line of defense.
- A SELF-LOCKFILE (outside the repo tree, keyed to this repo's path)
  refuses to start a second concurrent instance of THIS tool. This is
  DELIBERATELY separate from (and does not replace) the external-process
  check above: that check cannot see an in-process "pytest.main()" launch
  from a different script -- this repo's own documented no-xdist gate
  technique looks exactly like "python somegate.py" from the outside, and
  reliably detecting that from another process is fundamentally hard. Do
  not run this gate concurrently with that technique; every chunk here is
  already "-n0", so two concurrent instances of just THIS tool are only
  ADDITIVE single-process memory pressure, never the xdist x nested-joblib
  MULTIPLICATIVE fan-out that caused the original >90GB bugchecks -- a
  lightweight lockfile is proportionate here, not heavier machinery.
- Exit codes are NEVER ambiguous between outcome classes: 0 = genuine
  full-tree PASS, 1 = genuine full-tree FAIL -- a real test OR collection
  failure that must BLOCK a merge (includes a broken-to-collect tree:
  pytest returncode 2 with a parseable error count is a real failure, not
  an infra decline), 2 = the request itself was malformed (bad CLI args,
  no test files discovered, an --only selector that matches nothing or
  is too broad), 3 = ANY --only scoped-run outcome including a scoped
  --dry-run (never mistakable for a full-tree verdict by exit code
  alone), 4 = the GATE declined to RUN a chunk at all despite a
  well-formed request (a lockfile held by another instance, a concurrent
  pytest refusal, an oversized-chunk refusal -- pytest never even
  started) or could not reach a determinable result (TIMEOUT, an
  unparseable crash before any summary printed) -- with no real failure
  alongside it, so a wrapper can tell "declined/undetermined" from "tests
  failed" (a genuine failure always wins when both appear in the same
  run), 5 = a PLAIN --dry-run: nothing executed, so it must not share
  exit 0 with a genuine full-tree PASS either.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - degrade gracefully, see _other_pytest_running
    psutil = None

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"

DEFAULT_TARGET_CHUNKS = 6
DEFAULT_CHUNK_TIMEOUT_S = 2400  # wall-clock ceiling per chunk subprocess (40 min)
DEFAULT_TEST_TIMEOUT_S = 900  # per-test ceiling, passed through to pytest-timeout
# Proven-safe ceiling: the default 6-chunk plan against the real ~712-file
# tree lands at ~119-120 files/chunk. 150 gives headroom above that while
# staying well clear of the single-process MemoryError class this tool
# exists to prevent. Enforced independent of --chunks -- see plan_chunks()
# and _validate_chunk_size().
MAX_FILES_PER_CHUNK = 150


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Unit:
    """One atomic, never-split group of test files: a tests/ subdirectory, or
    the loose test_*.py files that live directly in tests/ with no subdir.

    `paths` are the exact argv tokens to pass to pytest for this unit: a
    single directory path for a subdirectory unit, or one path per file for
    the loose-files unit.
    """

    name: str
    paths: tuple[str, ...]
    file_count: int


@dataclasses.dataclass
class Chunk:
    index: int
    units: list[Unit]

    @property
    def file_count(self) -> int:
        return sum(u.file_count for u in self.units)

    @property
    def names(self) -> list[str]:
        return [u.name for u in self.units]


@dataclasses.dataclass
class ChunkResult:
    chunk: Chunk
    returncode: int | None
    counts: dict[str, int] | None
    node_ids: list[str]
    duration_s: float
    verdict: str  # "PASS" | "FAIL" | "ERROR" | "TIMEOUT" | "NO_TESTS"
    diagnostic_tail: str = ""


@dataclasses.dataclass
class GateReport:
    results: list[ChunkResult]
    totals: dict[str, int]
    failing_node_ids: list[str]
    verdict: str
    # Set (e.g. "--only 'tests/engine'") when this report covers only a
    # --only-selected subset, never the whole tree. render_report() uses this
    # to mark the report unmistakably -- a scoped PASS must never be
    # mistaken for (or silently substituted as) a full-tree gate verdict.
    scoped_reason: str | None = None


class OnlyFilterRefused(Exception):
    """Raised when --only would collapse into an unsafe (too-broad) single chunk."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_units(tests_root: Path) -> list[Unit]:
    """Discover chunkable units FRESH from disk. Never hardcoded.

    Each immediate subdirectory of `tests_root` that contains at least one
    test_*.py file (recursively) becomes one Unit keyed by its whole
    directory path. Loose test_*.py files sitting directly in `tests_root`
    (no subdirectory) become one additional Unit of individual file paths.
    Empty directories, dot-directories, and __pycache__ are skipped.
    """
    repo_root = tests_root.parent
    units: list[Unit] = []
    for entry in sorted(tests_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        files = sorted(entry.rglob("test_*.py"))
        if not files:
            continue
        rel = _posix_rel(entry, repo_root)
        units.append(Unit(name=rel, paths=(rel,), file_count=len(files)))

    loose = sorted(tests_root.glob("test_*.py"))
    if loose:
        rels = tuple(_posix_rel(f, repo_root) for f in loose)
        units.append(Unit(name="tests/(loose)", paths=rels, file_count=len(loose)))

    return units


def _posix_rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


# ---------------------------------------------------------------------------
# Chunk planning — LPT (longest-processing-time-first) greedy bin packing
# ---------------------------------------------------------------------------


def plan_chunks(units: list[Unit], target_chunks: int = DEFAULT_TARGET_CHUNKS) -> list[Chunk]:
    """Bin-pack units into ~target_chunks balanced chunks.

    A unit (whole tests/ subdirectory, or the loose-files group) is always
    placed atomically into exactly one chunk — never split. Uses the
    standard LPT heuristic: sort units largest-first (ties broken by name
    for determinism regardless of OS directory-iteration order), then place
    each unit into whichever chunk currently has the fewest files (ties
    broken by lowest chunk index). This gives a near-optimal balanced
    partition without ever splitting a unit.

    `target_chunks` is a PREFERENCE, never a way to force fewer/larger
    chunks than MAX_FILES_PER_CHUNK allows: effective_chunks =
    max(target_chunks, ceil(total_files / MAX_FILES_PER_CHUNK)), so e.g.
    "--chunks 1" against the real ~712-file tree is silently raised to
    enough chunks to respect the ceiling, rather than packing everything
    into one giant -n0 subprocess -- the exact single-process MemoryError
    class this tool exists to prevent. If a single ATOMIC unit alone
    exceeds the ceiling, no chunk count can fix that (units are never
    split) -- build_argv()'s _validate_chunk_size() is the fail-closed
    backstop for that edge case, refusing to spawn it at all.
    """
    if not units:
        return []
    total_files = sum(u.file_count for u in units)
    min_chunks_for_ceiling = math.ceil(total_files / MAX_FILES_PER_CHUNK)
    effective_target = max(target_chunks, min_chunks_for_ceiling)
    n_chunks = min(effective_target, len(units))
    chunks = [Chunk(index=i, units=[]) for i in range(n_chunks)]
    ordered = sorted(units, key=lambda u: (-u.file_count, u.name))
    for unit in ordered:
        target = min(chunks, key=lambda c: (c.file_count, c.index))
        target.units.append(unit)
    return chunks


def resolve_only_selection(units: list[Unit], substring: str, target_chunks: int) -> Chunk:
    """Resolve --only into a single Chunk, or raise OnlyFilterRefused.

    --only matches units by substring on their name and runs the match as
    ONE chunk — that is useful for smoke-testing a single directory through
    the real code path, but a too-broad (or empty) substring would collapse
    the whole tree into one uncapped "-n0" process, which is exactly the
    ~11.6k-item MemoryError-class failure this tool exists to prevent. So
    this refuses (raises, runs nothing) when the substring is empty/blank,
    matches nothing, or the matched units together exceed one chunk's fair
    share of the tree (with a 25% margin, but never above
    MAX_FILES_PER_CHUNK) for the requested chunk count.

    The ceiling is clamped to MAX_FILES_PER_CHUNK so this can NEVER accept
    a selection that build_argv()'s _validate_chunk_size() would then
    refuse: with a small --chunks (e.g. 4 against the real ~712-file
    tree), fair_share*1.25 alone can exceed 150, which would otherwise let
    a >150-file selection through here only to crash ungracefully deeper
    in the call stack. The two ceilings must always agree.
    """
    substring = substring.strip()
    if not substring:
        raise OnlyFilterRefused(
            "--only requires a non-empty substring (empty would collapse to the whole tree)."
        )
    matched = [u for u in units if substring in u.name]
    if not matched:
        raise OnlyFilterRefused(f"--only {substring!r} matched no units.")

    total_files = sum(u.file_count for u in units)
    fair_share = total_files / max(target_chunks, 1)
    ceiling = min(max(1, round(fair_share * 1.25)), MAX_FILES_PER_CHUNK)
    matched_files = sum(u.file_count for u in matched)
    if matched_files > ceiling:
        raise OnlyFilterRefused(
            f"--only {substring!r} matched {len(matched)} unit(s) / {matched_files} files, "
            f"exceeding the single-chunk ceiling ({ceiling} files -- min of 1.25x the "
            f"{fair_share:.0f}-file fair share of {total_files} total files across "
            f"{target_chunks} chunks, and the hard MAX_FILES_PER_CHUNK={MAX_FILES_PER_CHUNK}). "
            "This guard exists specifically to stop --only from collapsing the whole tree into "
            "one uncapped process. Pass a more specific (e.g. fully-qualified 'tests/<dir>') "
            "substring."
        )
    return Chunk(index=0, units=matched)


# ---------------------------------------------------------------------------
# Safe subprocess invocation
# ---------------------------------------------------------------------------


def build_argv(chunk: Chunk, test_timeout: int = DEFAULT_TEST_TIMEOUT_S) -> list[str]:
    _validate_chunk_size(chunk)
    argv = [sys.executable, "-m", "pytest"]
    for unit in chunk.units:
        argv.extend(unit.paths)
    argv += ["-n0", "-q", f"--timeout={test_timeout}"]
    _validate_safe_argv(argv)
    return argv


def _validate_chunk_size(chunk: Chunk) -> None:
    """Fail-closed guard: a single chunk must never exceed
    MAX_FILES_PER_CHUNK files, regardless of how it was constructed
    (plan_chunks' own ceiling logic, --only's ceiling, or any future
    caller). Called from build_argv -- the ONE choke point every chunk
    passes through before a subprocess is ever spawned -- so this backstop
    holds even if a caller bypasses plan_chunks entirely. Raises rather
    than silently spawning an oversized single-process pytest run: the
    exact single-process MemoryError class this tool exists to prevent.
    """
    if chunk.file_count > MAX_FILES_PER_CHUNK:
        raise RuntimeError(
            f"SAFETY: chunk {chunk.index} has {chunk.file_count} files, exceeding "
            f"MAX_FILES_PER_CHUNK={MAX_FILES_PER_CHUNK}. Refusing to spawn a single "
            "pytest process over the proven-safe chunk-size ceiling."
        )


def _validate_safe_argv(argv: list[str]) -> None:
    """Hard guardrails, always re-checked immediately before every subprocess
    invocation. These are explicit raises, NOT `assert` statements: `assert`
    is compiled out entirely under `python -O` / PYTHONOPTIMIZE, which would
    silently disable every one of these checks. A guard against a dangerous
    pytest invocation must never be optimizable away.
    """
    if "-n0" not in argv:
        raise RuntimeError("SAFETY: -n0 must be explicit in every chunk invocation")
    if "tests" in argv:
        raise RuntimeError("SAFETY: the bare 'tests' root must never be passed as an argument")
    timeout_args = [a for a in argv if a.startswith("--timeout=")]
    if not timeout_args:
        raise RuntimeError("SAFETY: --timeout must be explicit")
    timeout_value_str = timeout_args[0].split("=", 1)[1]
    try:
        timeout_value = int(timeout_value_str)
    except ValueError as exc:
        raise RuntimeError(
            f"SAFETY: --timeout value {timeout_value_str!r} is not an integer"
        ) from exc
    if timeout_value <= 0:
        # 0 (or negative) DISABLES pytest-timeout's per-test safety net
        # entirely -- merely being PRESENT on the command line is not
        # enough; the value itself must actually enforce a limit.
        raise RuntimeError(
            f"SAFETY: --timeout={timeout_value} would disable pytest-timeout's per-test "
            "safety net (0/negative means 'no limit') -- the value must be > 0"
        )
    path_tokens = [a for a in argv[3:] if not a.startswith("-")]
    if not path_tokens:
        raise RuntimeError("SAFETY: at least one explicit path must be passed")


def _basename(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def _looks_like_pytest_invocation(name: str, cmdline: list[str]) -> bool:
    """True if `name`/`cmdline` describe a process that is itself running
    pytest -- checked against the process NAME and individual cmdline
    TOKENS, never a blind substring search over the whole joined cmdline.

    A blind "'pytest' in ' '.join(cmdline)" check false-positives on any
    process whose cmdline merely CONTAINS the text "pytest" as part of an
    unrelated path -- e.g. this very worktree is named "pytest-gate", so a
    wrapping shell process re-quoting the full invoked command line (as
    Windows Git Bash's "bash.exe -c '<whole command>'" does, in ONE cmdline
    token) would otherwise trigger a false "pytest already running" refusal
    on every single run. Matching exact/prefixed names and individual
    argv tokens (never a path substring) avoids that.
    """
    name_lower = name.lower()
    if name_lower == "pytest" or name_lower.startswith("pytest.") or name_lower == "py.test":
        return True
    tokens = [t.lower() for t in cmdline]
    for i, token in enumerate(tokens):
        base = _basename(token)
        if base == "pytest" or base.startswith("pytest.") or base == "py.test":
            return True
        if token == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "pytest":
            return True
        # Glued form: "-mpytest" (vs. the separate "-m" "pytest" pair above).
        if token.startswith("-m") and token.endswith("pytest"):
            return True
    return False


def _other_pytest_running() -> tuple[bool, str]:
    """Best-effort check for a concurrently-running pytest process.

    Degrades gracefully: if psutil is unavailable, or enumeration fails
    partway (permission-denied on some process, etc.), logs a warning and
    reports "not found" rather than aborting the gate.
    """
    if psutil is None:
        print(
            "[gate] WARNING: psutil unavailable -- skipping already-running-pytest check.",
            file=sys.stderr,
        )
        return False, ""
    my_pid = os.getpid()
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.pid == my_pid:
                    continue
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                if _looks_like_pytest_invocation(name, cmdline):
                    return True, f"pid={proc.pid} cmdline={' '.join(cmdline)}"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:  # pragma: no cover - defensive degrade, never block the gate on this
        print(
            f"[gate] WARNING: already-running-pytest check failed ({exc}) -- proceeding.",
            file=sys.stderr,
        )
        return False, ""
    return False, ""


class GateAlreadyRunning(Exception):
    """Raised when this tool's own self-lockfile shows a live instance already running."""


def _lockfile_path() -> Path:
    """A stable per-repo lockfile path OUTSIDE the repo tree -- never at
    risk of being git-tracked or accidentally committed. Keyed to
    REPO_ROOT's absolute path so a checkout of this repo elsewhere on the
    same machine gets its own independent lock."""
    key = hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"segmented_full_tree_gate_{key}.lock"


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check. If psutil is unavailable, degrades to
    "not alive" (so a stale lock is reclaimed rather than blocking every
    future run forever) -- consistent with every other psutil-optional
    guard in this file, which all fail toward letting the gate proceed
    rather than blocking indefinitely on something unverifiable."""
    if psutil is None:
        return False
    return psutil.pid_exists(pid)


def _acquire_self_lock(lock_path: Path | None = None) -> Path:
    """Write a self-lockfile recording this process's pid, refusing
    (raising GateAlreadyRunning) if a LIVE instance already holds one.

    This closes the "two instances of THIS tool" concurrency case -- see
    the module docstring's SAFETY MODEL for why this is deliberately
    separate from (and does not replace) _other_pytest_running.
    """
    if lock_path is None:
        lock_path = _lockfile_path()
    if lock_path.exists():
        held_pid: int | None
        try:
            held_pid = int(lock_path.read_text().strip())
        except (ValueError, OSError):
            held_pid = None
        if held_pid is not None and _pid_is_alive(held_pid):
            raise GateAlreadyRunning(
                f"Another instance of this gate (pid={held_pid}) is already running "
                f"(lockfile: {lock_path}). Refusing to start a second one."
            )
        # Stale lock (holder no longer alive, or unreadable) -- safe to reclaim.
    lock_path.write_text(str(os.getpid()))
    return lock_path


def _release_self_lock(lock_path: Path) -> None:
    with contextlib.suppress(OSError):
        lock_path.unlink()


def _kill_process_tree(proc: psutil.Process) -> None:
    """Best-effort recursive kill of `proc` and every descendant it has.

    Needed because "-n0" only rules out xdist's OWN worker pool -- it does
    nothing to stop a TEST BODY from forking its own worker pool (joblib
    n_jobs=-1 in synthetic_history generation, optuna study.optimize in
    autotuner tests). A plain kill of just the top-level pytest process
    leaves those grandchildren running; the NEXT chunk then starts on top of
    them, re-creating the >90GB multi-process fan-out this tool exists to
    prevent. So every descendant is terminated (and killed if it survives
    termination), not just the immediate child.

    `proc` MUST be a psutil.Process captured at spawn time (not re-looked-up
    by bare pid later) -- psutil binds a Process object to a (pid,
    create_time) pair internally and raises NoSuchProcess on any call once
    that SPECIFIC process has exited, even if the OS has since recycled the
    pid for an unrelated process. Re-fetching by bare pid after the fact
    would lose that protection and risk killing the wrong process.
    """
    if psutil is None:
        return
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        children = []
    targets = [*children, proc]
    for target in targets:
        with contextlib.suppress(psutil.Error):
            target.terminate()
    try:
        _, alive = psutil.wait_procs(targets, timeout=5)
    except psutil.Error:
        alive = targets
    for target in alive:
        with contextlib.suppress(psutil.Error):
            target.kill()


def _attach_psutil_process(pid: int, context: str) -> psutil.Process | None:
    """Best-effort `psutil.Process(pid)` that degrades to None instead of
    propagating an exception.

    A chunk that dies near-instantly (bad argv, an immediate import crash,
    instant OOM) can exit before this runs, so the pid may already be gone
    -- psutil.Process() raises NoSuchProcess rather than returning a handle.
    Without this guard that exception would propagate out of run_chunk and
    abort the WHOLE sequential gate on one bad chunk; instead this chunk
    simply loses its tree-kill safety net (logged) and the gate continues.
    """
    if psutil is None:
        return None
    try:
        return psutil.Process(pid)
    except psutil.Error as exc:
        print(
            f"[gate] WARNING: could not attach psutil to {context}'s pid {pid} "
            f"({exc}) -- no tree-kill safety net for it.",
            file=sys.stderr,
        )
        return None


def run_chunk(chunk: Chunk, test_timeout: int, chunk_timeout: int) -> ChunkResult:
    found, desc = _other_pytest_running()
    if found:
        msg = f"Refused to start: another pytest process detected ({desc})."
        print(f"[gate] REFUSING TO START chunk {chunk.index}: {msg}", file=sys.stderr)
        return ChunkResult(
            chunk=chunk,
            returncode=None,
            counts=None,
            node_ids=[],
            duration_s=0.0,
            verdict="ERROR",
            diagnostic_tail=msg,
        )

    try:
        argv = build_argv(chunk, test_timeout=test_timeout)
    except RuntimeError as exc:
        # Belt-and-suspenders: build_argv's own guards (_validate_chunk_size,
        # _validate_safe_argv) should already have been satisfied by the
        # caller (plan_chunks respects MAX_FILES_PER_CHUNK; --only's
        # ceiling is clamped to agree with it) -- but if a FUTURE caller
        # ever bypasses those, this must degrade to a clean declined
        # verdict, never an unhandled traceback that crashes the whole
        # sequential gate mid-run.
        msg = f"Refused to start: {exc}"
        print(f"[gate] REFUSING TO START chunk {chunk.index}: {msg}", file=sys.stderr)
        return ChunkResult(
            chunk=chunk,
            returncode=None,
            counts=None,
            node_ids=[],
            duration_s=0.0,
            verdict="ERROR",
            diagnostic_tail=msg,
        )
    env = os.environ.copy()
    # Force each chunk's subprocess to get its OWN fresh temp DB via
    # conftest.py's pytest_configure(), rather than inheriting a value from
    # this script's own shell (or, in principle, a prior chunk) — genuine
    # per-chunk isolation, not just per-process reuse of the same path.
    env.pop("DB_PATH", None)
    env.pop("ATLAS_CACHE_DB_PATH", None)

    start = time.monotonic()
    # subprocess.run(timeout=...) is deliberately NOT used here: its
    # TimeoutExpired exception exposes no pid, so there would be no way to
    # reach the child's own descendants for a tree-kill (see
    # _kill_process_tree). Popen keeps `proc` (and the psutil.Process handle
    # captured immediately below, before the pid could plausibly be reused)
    # in scope for the whole call, timeout or not.
    proc = subprocess.Popen(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ps_proc = _attach_psutil_process(proc.pid, f"chunk {chunk.index}")

    try:
        stdout, stderr = proc.communicate(timeout=chunk_timeout)
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        if ps_proc is not None:
            _kill_process_tree(ps_proc)
        else:
            print(
                "[gate] WARNING: psutil unavailable -- killing only the top-level pytest "
                "process, any worker pool it forked may survive into the next chunk.",
                file=sys.stderr,
            )
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        # A hung test's traceback / pytest-timeout dump usually lands on
        # stderr, not stdout -- include both in the diagnostic tail (stdout
        # already captured above).
        combined = (stdout or "") + "\n" + (stderr or "")
        tail = "\n".join(combined.splitlines()[-30:])
        return ChunkResult(
            chunk=chunk,
            returncode=None,
            counts=None,
            node_ids=[],
            duration_s=duration,
            verdict="TIMEOUT",
            diagnostic_tail=tail,
        )
    duration = time.monotonic() - start

    # Best-effort mop-up after a NORMAL completion. By the time
    # communicate() returns, the parent pytest process has already exited,
    # so _kill_process_tree's proc.children(recursive=True) lookup here can
    # only catch descendants still discoverable via that (now-dead)
    # parent's pid -- it is NOT a guarantee against every reparented
    # orphan. The real orphan-prevention guarantee is the TIMEOUT path
    # above, where the parent is still alive and its full descendant tree
    # is reliably enumerable.
    #
    # A worker pool that outlives a chunk's NORMAL completion is a LEAKY
    # TEST in the suite itself -- not this gate's job to fully catch, and
    # deliberately NOT patched over with heuristic between-chunk process
    # killing: (a) every chunk here runs "-n0", so a leaked pool is
    # additive memory pressure, never the xdist x nested-joblib
    # multiplicative fan-out that caused the original >90GB bugchecks;
    # (b) killing a reparented process by heuristic (e.g. "anything
    # mentioning our repo path") is itself dangerous -- it can kill an
    # unrelated process a developer is running. This mop-up is cheap
    # insurance for the common (still-parented) case, nothing more.
    if ps_proc is not None:
        _kill_process_tree(ps_proc)

    combined = stdout + "\n" + stderr
    counts = parse_summary(combined)
    node_ids = extract_node_ids(combined)
    verdict = _classify_chunk_outcome(proc.returncode, counts)

    diagnostic_tail = ""
    if verdict == "ERROR":
        # Either no parseable summary at all (e.g. a conftest ImportError
        # before collection ever completed -- never treat that as a false
        # zero) or an anomalous returncode/counts combination (e.g.
        # returncode 2 "interrupted"). Either way, never silently call this
        # PASS; surface a diagnostic tail for a human.
        diagnostic_tail = "\n".join(combined.splitlines()[-30:])

    return ChunkResult(
        chunk=chunk,
        returncode=proc.returncode,
        counts=counts,
        node_ids=node_ids,
        duration_s=duration,
        verdict=verdict,
        diagnostic_tail=diagnostic_tail,
    )


def _classify_chunk_outcome(returncode: int, counts: dict[str, int] | None) -> str:
    """Pure classification of a completed chunk subprocess into a verdict
    string, given its pytest exit code and parsed summary counts (or None).
    Extracted out of run_chunk so this branching is unit-testable without a
    real subprocess. Never returns "TIMEOUT" -- that verdict is assigned by
    run_chunk's own TimeoutExpired handler before this function is reached.

    returncode 5 is pytest's own "no tests ran" exit code. It fires both
    when literally zero items were collected AND when every collected item
    was deselected by the project's default marker filter (addopts:
    -m 'not live and not slow and not perf') -- e.g. a chunk directory made
    up entirely of @pytest.mark.live tests legitimately runs nothing under
    that filter. Neither case is a test FAILURE: without this branch, such
    a chunk would fall through to the "matches neither PASS nor FAIL" ERROR
    branch below and downgrade an otherwise completely clean tree to a gate
    FAIL. NO_TESTS is distinct from PASS (nothing was actually verified),
    but does not fail the grand verdict either -- see aggregate().

    returncode 2 ("interrupted") ALSO fires for a broken-to-collect tree
    (import/collection errors) as long as pytest still reached a parseable
    summary reporting them. That IS a real failure that must BLOCK a merge
    -- a broken/uncollectable tree is not a retryable "gate declined to
    run," it is FAIL, exactly like returncode 1. Exit code 4 (DECLINED) is
    reserved strictly for cases where the GATE itself never let pytest run
    at all (lockfile held, a concurrent-pytest refusal, an oversized-chunk
    refusal) or genuinely could not determine an outcome (TIMEOUT, no
    summary printed at all). A returncode 2 with NO real failure counted
    (e.g. a genuine Ctrl-C interrupt with nothing failed yet) has nothing
    to attribute a FAIL to, so it stays ERROR.
    """
    if returncode == 5:
        return "NO_TESTS"
    if counts is None:
        return "ERROR"
    failed = counts.get("failed", 0) + counts.get("errors", 0)
    if returncode == 0 and failed == 0:
        return "PASS"
    if returncode in (1, 2) and failed > 0:
        # Attributable failure(s) -- node ids were extracted by the caller.
        return "FAIL"
    # Anomalous combination (e.g. returncode 2 with nothing actually
    # failed, or counts that don't match the returncode) -- never silently
    # call this PASS.
    return "ERROR"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_CATEGORY_ALIASES = {
    "error": "errors",
    "errors": "errors",
    "warning": "warnings",
    "warnings": "warnings",
}
_CATEGORY_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b"
)
_DURATION_TRAILER_RE = re.compile(r"\bin\s+[\d.]+s\b")
# `.+?` (not `\S+?`) so a parametrized node id containing literal spaces
# inside its brackets (e.g. "test_z[a b]") is captured whole rather than
# truncated at the first space. Non-greedy + the optional trailing group
# still finds the EARLIEST " - <reason>" split point when one is present
# (pytest node ids essentially never contain the literal " - " substring
# themselves), and falls back to the full remainder of the line when no
# such reason suffix exists at all.
#
# ACCEPTED LIMITATION (not fixed): a parametrized id containing a literal
# " - " INSIDE its own brackets (e.g. "test_z[a - b]") still mis-splits at
# that point, truncating the captured id. This is essentially never seen
# in practice, and it does NOT affect the verdict -- the chunk's own
# failed/error COUNTS (from parse_summary) are unaffected either way, so a
# real failure is never missed. Only the id string surfaced in the
# FAILING/ERRORING NODE IDS list (e.g. for a targeted rerun) would be
# truncated for that rare case.
_NODEID_RE = re.compile(r"^(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s+.*)?$", re.MULTILINE)


def parse_summary(text: str) -> dict[str, int] | None:
    """Parse pytest's final short-summary line (e.g.
    "9 failed, 1928 passed, 12 skipped, 23 warnings in 515.20s (0:08:35)")
    into a category -> count dict.

    Scans every line and keeps the LAST one that both (a) has a duration
    trailer ("in <N.NN>s") and (b) has at least one "<N> <category>" pair --
    the real summary line is always the last such line, and this rejects
    look-alikes (e.g. a docstring mentioning "5 passed tests"). Returns None
    when no such line exists at all (collection blew up before any summary
    was printed, or "no tests ran in 0.01s") -- callers must never treat
    None as zero failures.
    """
    best: str | None = None
    for raw in text.splitlines():
        line = raw.strip().strip("=").strip()
        if _DURATION_TRAILER_RE.search(line) and _CATEGORY_RE.search(line):
            best = line
    if best is None:
        return None
    counts: dict[str, int] = {}
    for count_str, category in _CATEGORY_RE.findall(best):
        key = _CATEGORY_ALIASES.get(category, category)
        counts[key] = counts.get(key, 0) + int(count_str)
    return counts


def extract_node_ids(text: str) -> list[str]:
    """Extract FAILED/ERROR node ids from pytest's short test summary info,
    in the order they appear, with any trailing " - <reason>" stripped.

    Only candidates containing "::" are kept. A real pytest node id always
    has at least one "::" (file::test, optionally file::Class::test, plus
    an optional "[params]" suffix) -- this filters out a test that simply
    LOGS a line starting with "ERROR " or "FAILED " as ordinary output
    prose (e.g. "ERROR failed to connect to broker"), which would
    otherwise pollute this rerun-id list.

    RESIDUAL TRADE-OFF: a whole-FILE pytest collection error can
    legitimately show as "ERROR tests/foo/test_bar.py" (no "::" at all,
    the whole file failed to collect) -- that id is now filtered out of
    this list too. This does NOT affect the verdict: parse_summary's
    counts (e.g. "1 error") are entirely independent of this list, so the
    failure is still correctly reported as non-PASS. Only the human-facing
    rerun-id string is occasionally missing for that less-common case.
    """
    candidates = _NODEID_RE.findall(text)
    return [c for c in candidates if "::" in c]


# ---------------------------------------------------------------------------
# Aggregation / reporting
# ---------------------------------------------------------------------------


def aggregate(results: list[ChunkResult], scoped_reason: str | None = None) -> GateReport:
    totals: dict[str, int] = {}
    failing_node_ids: list[str] = []
    seen: set[str] = set()
    for result in results:
        if result.counts:
            for key, value in result.counts.items():
                totals[key] = totals.get(key, 0) + value
        for node_id in result.node_ids:
            if node_id not in seen:
                seen.add(node_id)
                failing_node_ids.append(node_id)
    # NO_TESTS is PASS-equivalent for the grand verdict: a chunk whose
    # directories were entirely deselected (or genuinely empty) collected
    # nothing to fail, and must not drag down an otherwise clean tree. It
    # still shows as its own distinct verdict in the per-chunk table.
    verdict = (
        "PASS" if results and all(r.verdict in ("PASS", "NO_TESTS") for r in results) else "FAIL"
    )
    return GateReport(
        results=results,
        totals=totals,
        failing_node_ids=failing_node_ids,
        verdict=verdict,
        scoped_reason=scoped_reason,
    )


def render_report(report: GateReport) -> str:
    lines: list[str] = []
    if report.scoped_reason:
        # A scoped (--only) run must never read as a full-tree verdict -- a
        # wrapper or human skimming for "VERDICT: PASS" could otherwise treat
        # a single-directory smoke run as a real merge-gate pass. Both a
        # banner up top and a suffix on the VERDICT line itself.
        lines.append(f"*** SCOPED RUN ({report.scoped_reason}) -- NOT a full-tree verdict ***")
        lines.append("")
    lines.append(f"{'CHUNK':<6}{'FILES':>7}  {'VERDICT':<8}{'DURATION':>10}  UNITS")
    for result in report.results:
        units_str = ", ".join(result.chunk.names)
        lines.append(
            f"{result.chunk.index:<6}{result.chunk.file_count:>7}  {result.verdict:<8}"
            f"{result.duration_s:>9.1f}s  {units_str}"
        )
    lines.append("")
    lines.append("TOTALS: " + ", ".join(f"{v} {k}" for k, v in sorted(report.totals.items())))
    if report.failing_node_ids:
        lines.append("")
        lines.append(f"FAILING/ERRORING NODE IDS ({len(report.failing_node_ids)}):")
        lines.extend(f"  {node_id}" for node_id in report.failing_node_ids)
    for result in report.results:
        if result.verdict in ("ERROR", "TIMEOUT") and result.diagnostic_tail:
            lines.append("")
            lines.append(f"--- chunk {result.chunk.index} diagnostic tail ({result.verdict}) ---")
            lines.append(result.diagnostic_tail)
    lines.append("")
    verdict_suffix = " (SCOPED -- NOT a full-tree verdict)" if report.scoped_reason else ""
    lines.append(f"VERDICT: {report.verdict}{verdict_suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    """argparse `type=` validator: rejects <= 0 with a clean argparse error
    instead of an unhandled traceback later (e.g. "--chunks 0" would
    otherwise reach plan_chunks/min() on an implied-empty range)."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"{value!r} must be >= 1")
    return parsed


# Exit codes -- see the module docstring's SAFETY MODEL for the full
# rationale. Never ambiguous between outcome classes.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG_ERROR = 2
EXIT_SCOPED = 3
EXIT_DECLINED = 4
EXIT_DRY_RUN = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument(
        "--chunks",
        type=_positive_int,
        default=DEFAULT_TARGET_CHUNKS,
        help="target number of chunks (a preference -- MAX_FILES_PER_CHUNK is a hard floor)",
    )
    parser.add_argument(
        "--chunk-timeout",
        type=_positive_int,
        default=DEFAULT_CHUNK_TIMEOUT_S,
        help="wall-clock seconds per chunk subprocess (must be > 0)",
    )
    parser.add_argument(
        "--test-timeout",
        type=_positive_int,
        default=DEFAULT_TEST_TIMEOUT_S,
        help="per-test seconds, passed to pytest-timeout (must be > 0 -- 0 disables it)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help=(
            "debug: run only units whose name contains this substring, as one chunk. "
            "Always exits 3 (never 0/1), including with --dry-run -- a scoped run is "
            "never a full-tree verdict."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the chunk plan and execute nothing (exits 5, or 3 if combined with --only)",
    )
    args = parser.parse_args(argv)

    units = discover_units(TESTS_ROOT)
    if not units:
        print(
            "[gate] ERROR: no test units discovered under tests/ -- misconfiguration?",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    if args.only is not None:
        try:
            chunks = [resolve_only_selection(units, args.only, target_chunks=args.chunks)]
        except OnlyFilterRefused as exc:
            print(f"[gate] REFUSED: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    else:
        chunks = plan_chunks(units, target_chunks=args.chunks)

    scoped_reason = f"--only {args.only!r}" if args.only is not None else None

    if args.dry_run:
        print(
            f"[gate] DRY RUN -- {len(units)} unit(s) discovered, {len(chunks)} chunk(s) planned. Executing nothing."
        )
        for chunk in chunks:
            print(f"  chunk {chunk.index}: {chunk.file_count} files -- {', '.join(chunk.names)}")
        # A scoped dry-run is still a --only invocation, never a full-tree
        # verdict -- honor the same "--only always exits 3" contract here
        # too. A PLAIN dry-run executed nothing either, so it must not
        # share exit 0 with a genuine full-tree PASS -- its own dedicated
        # "no verdict at all" code.
        return EXIT_SCOPED if scoped_reason else EXIT_DRY_RUN

    try:
        lock_path = _acquire_self_lock()
    except GateAlreadyRunning as exc:
        # A lockfile refusal means the gate declined to run ANY chunk at
        # all -- the same "declined to run" class as a per-chunk
        # concurrent-pytest/oversized-chunk refusal (see run_chunk), just
        # caught before any chunk exists to carry that verdict. EXIT_DECLINED,
        # not EXIT_CONFIG_ERROR: the request itself was well-formed.
        print(f"[gate] REFUSED: {exc}", file=sys.stderr)
        return EXIT_DECLINED
    try:
        results = [
            run_chunk(chunk, test_timeout=args.test_timeout, chunk_timeout=args.chunk_timeout)
            for chunk in chunks
        ]
    finally:
        _release_self_lock(lock_path)

    report = aggregate(results, scoped_reason=scoped_reason)
    print(render_report(report))

    if report.scoped_reason:
        # Exit 0/1 are reserved for a genuine FULL-TREE verdict. A --only
        # run is marked in the printed report (see render_report), but
        # text alone is not enough for a wrapper keying off "$?" -- it gets
        # its own distinct exit code for ANY outcome (PASS, FAIL, ERROR,
        # ...), so it can never be mistaken for a full-tree result by exit
        # code either.
        return EXIT_SCOPED
    if report.verdict == "PASS":
        return EXIT_PASS
    # FAIL: distinguish a genuine test/collection failure (a real reason to
    # BLOCK a merge) from the gate declining to run a chunk at all
    # (concurrent-pytest / oversized-chunk refusal -- pytest never even
    # started) or failing to reach a determinable result (TIMEOUT, an
    # unparseable crash before any summary printed). A CI wrapper needs to
    # tell "tests/collection failed, block the merge" (1) apart from "the
    # gate itself declined or couldn't tell" (4) -- a genuine FAIL always
    # wins when both appear in the same run.
    has_real_failure = any(r.verdict == "FAIL" for r in results)
    if not has_real_failure and any(r.verdict in ("ERROR", "TIMEOUT") for r in results):
        return EXIT_DECLINED
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
