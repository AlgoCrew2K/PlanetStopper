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
- Before every chunk, this script refuses to start if another "python -m
  pytest" process is already running (best-effort via psutil) — running two
  pytest invocations concurrently on this host is exactly the kind of
  memory-pressure incident this tool exists to prevent.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
import sys
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
    verdict: str  # "PASS" | "FAIL" | "ERROR" | "TIMEOUT"
    diagnostic_tail: str = ""


@dataclasses.dataclass
class GateReport:
    results: list[ChunkResult]
    totals: dict[str, int]
    failing_node_ids: list[str]
    verdict: str


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
    """
    if not units:
        return []
    n_chunks = min(target_chunks, len(units))
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
    share of the tree (with a 25% margin) for the requested chunk count.
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
    ceiling = max(1, round(fair_share * 1.25))
    matched_files = sum(u.file_count for u in matched)
    if matched_files > ceiling:
        raise OnlyFilterRefused(
            f"--only {substring!r} matched {len(matched)} unit(s) / {matched_files} files, "
            f"exceeding the single-chunk ceiling ({ceiling} files, ~1.25x the {fair_share:.0f}-file "
            f"fair share of {total_files} total files across {target_chunks} chunks). This guard "
            "exists specifically to stop --only from collapsing the whole tree into one uncapped "
            "process. Pass a more specific (e.g. fully-qualified 'tests/<dir>') substring."
        )
    return Chunk(index=0, units=matched)


# ---------------------------------------------------------------------------
# Safe subprocess invocation
# ---------------------------------------------------------------------------


def build_argv(chunk: Chunk, test_timeout: int = DEFAULT_TEST_TIMEOUT_S) -> list[str]:
    argv = [sys.executable, "-m", "pytest"]
    for unit in chunk.units:
        argv.extend(unit.paths)
    argv += ["-n0", "-q", f"--timeout={test_timeout}"]
    _assert_safe_argv(argv)
    return argv


def _assert_safe_argv(argv: list[str]) -> None:
    """Hard guardrails, always re-checked immediately before every subprocess.run."""
    assert "-n0" in argv, "SAFETY: -n0 must be explicit in every chunk invocation"
    assert "tests" not in argv, "SAFETY: the bare 'tests' root must never be passed as an argument"
    assert any(a.startswith("--timeout=") for a in argv), "SAFETY: --timeout must be explicit"
    path_tokens = [a for a in argv[3:] if not a.startswith("-")]
    assert path_tokens, "SAFETY: at least one explicit path must be passed"


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
                name = (proc.info.get("name") or "").lower()
                cmdline_str = " ".join(proc.info.get("cmdline") or []).lower()
                if "python" in name and "pytest" in cmdline_str:
                    return (
                        True,
                        f"pid={proc.pid} cmdline={' '.join(proc.info.get('cmdline') or [])}",
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:  # pragma: no cover - defensive degrade, never block the gate on this
        print(
            f"[gate] WARNING: already-running-pytest check failed ({exc}) -- proceeding.",
            file=sys.stderr,
        )
        return False, ""
    return False, ""


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

    argv = build_argv(chunk, test_timeout=test_timeout)
    env = os.environ.copy()
    # Force each chunk's subprocess to get its OWN fresh temp DB via
    # conftest.py's pytest_configure(), rather than inheriting a value from
    # this script's own shell (or, in principle, a prior chunk) — genuine
    # per-chunk isolation, not just per-process reuse of the same path.
    env.pop("DB_PATH", None)
    env.pop("ATLAS_CACHE_DB_PATH", None)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=chunk_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        # subprocess.run() already kills the immediate child before raising.
        # No xdist workers exist under -n0, so there is no further process
        # tree to clean up here.
        stdout = exc.stdout or ""
        tail = "\n".join(stdout.splitlines()[-30:])
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

    combined = proc.stdout + "\n" + proc.stderr
    counts = parse_summary(combined)
    node_ids = extract_node_ids(combined)

    if counts is None:
        # No parseable summary at all -- e.g. a conftest ImportError before
        # collection ever completed. This is an opaque infra failure: never
        # treat it as a false zero. Surface a diagnostic tail for a human.
        tail = "\n".join(combined.splitlines()[-30:])
        return ChunkResult(
            chunk=chunk,
            returncode=proc.returncode,
            counts=None,
            node_ids=node_ids,
            duration_s=duration,
            verdict="ERROR",
            diagnostic_tail=tail,
        )

    failed = counts.get("failed", 0) + counts.get("errors", 0)
    if proc.returncode == 0 and failed == 0:
        verdict = "PASS"
    elif proc.returncode == 1 and failed > 0:
        # Attributable failure(s) -- node ids were extracted above.
        verdict = "FAIL"
    else:
        # Anomalous combination (e.g. returncode 2 "interrupted", or counts
        # that don't match the returncode) -- never silently call this PASS.
        verdict = "ERROR"

    return ChunkResult(
        chunk=chunk,
        returncode=proc.returncode,
        counts=counts,
        node_ids=node_ids,
        duration_s=duration,
        verdict=verdict,
        diagnostic_tail="" if verdict != "ERROR" else "\n".join(combined.splitlines()[-30:]),
    )


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
_NODEID_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s+.*)?$", re.MULTILINE)


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
    in the order they appear, with any trailing " - <reason>" stripped."""
    return _NODEID_RE.findall(text)


# ---------------------------------------------------------------------------
# Aggregation / reporting
# ---------------------------------------------------------------------------


def aggregate(results: list[ChunkResult]) -> GateReport:
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
    verdict = "PASS" if results and all(r.verdict == "PASS" for r in results) else "FAIL"
    return GateReport(
        results=results, totals=totals, failing_node_ids=failing_node_ids, verdict=verdict
    )


def render_report(report: GateReport) -> str:
    lines = [f"{'CHUNK':<6}{'FILES':>7}  {'VERDICT':<8}{'DURATION':>10}  UNITS"]
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
    lines.append(f"VERDICT: {report.verdict}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument(
        "--chunks", type=int, default=DEFAULT_TARGET_CHUNKS, help="target number of chunks"
    )
    parser.add_argument(
        "--chunk-timeout",
        type=int,
        default=DEFAULT_CHUNK_TIMEOUT_S,
        help="wall-clock seconds per chunk subprocess",
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=DEFAULT_TEST_TIMEOUT_S,
        help="per-test seconds, passed to pytest-timeout",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="debug: run only units whose name contains this substring, as one chunk",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the chunk plan and execute nothing"
    )
    args = parser.parse_args(argv)

    units = discover_units(TESTS_ROOT)
    if not units:
        print(
            "[gate] ERROR: no test units discovered under tests/ -- misconfiguration?",
            file=sys.stderr,
        )
        return 2

    if args.only is not None:
        try:
            chunks = [resolve_only_selection(units, args.only, target_chunks=args.chunks)]
        except OnlyFilterRefused as exc:
            print(f"[gate] REFUSED: {exc}", file=sys.stderr)
            return 2
    else:
        chunks = plan_chunks(units, target_chunks=args.chunks)

    if args.dry_run:
        print(
            f"[gate] DRY RUN -- {len(units)} unit(s) discovered, {len(chunks)} chunk(s) planned. Executing nothing."
        )
        for chunk in chunks:
            print(f"  chunk {chunk.index}: {chunk.file_count} files -- {', '.join(chunk.names)}")
        return 0

    results = [
        run_chunk(chunk, test_timeout=args.test_timeout, chunk_timeout=args.chunk_timeout)
        for chunk in chunks
    ]
    report = aggregate(results)
    print(render_report(report))
    return 0 if report.verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
