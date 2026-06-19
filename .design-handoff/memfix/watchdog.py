"""Memory watchdog wrapper for running a SINGLE suspect pytest invocation safely.

Spawns the given pytest command as a child process, polls the child's RSS
(and the RSS of ALL its descendants — to catch joblib/loky/optuna fan-out
children) every 0.5s, and KILLS the whole tree if aggregate RSS exceeds the
hard cap. This is the ONLY sanctioned way to execute a suspect test during the
memory-blowup investigation.

Usage:
    python .design-handoff/memfix/watchdog.py [--cap-gb 8] -- <pytest args...>

The cap defaults to 8 GB (8e9 bytes) aggregate across the process tree.
Exit code 137 indicates the watchdog killed the tree for exceeding the cap.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import psutil

POLL_INTERVAL_S = 0.5


def _tree_rss(proc: psutil.Process) -> int:
    """Aggregate RSS (bytes) of proc + all living descendants."""
    total = 0
    procs = [proc]
    try:
        procs += proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _kill_tree(proc: psutil.Process) -> None:
    procs = []
    try:
        procs = proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    procs.append(proc)
    for p in procs:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap-gb", type=float, default=8.0)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("watchdog: no command given", file=sys.stderr)
        return 2

    cap_bytes = int(args.cap_gb * 1e9)
    peak = 0

    env = dict(os.environ)
    # Belt-and-suspenders: never let the watched run touch the prod DB.
    env.setdefault("DB_PATH", os.path.join(env.get("TEMP", "/tmp"), "memfix.db"))

    print(f"watchdog: launching {cmd!r} with cap {args.cap_gb} GB", flush=True)
    child = subprocess.Popen(cmd, env=env)
    try:
        proc = psutil.Process(child.pid)
    except psutil.NoSuchProcess:
        return child.wait()

    killed = False
    while True:
        ret = child.poll()
        if ret is not None:
            break
        rss = _tree_rss(proc)
        if rss > peak:
            peak = rss
            print(f"watchdog: tree RSS peak {peak / 1e9:.2f} GB", flush=True)
        if rss > cap_bytes:
            print(
                f"watchdog: TREE RSS {rss / 1e9:.2f} GB EXCEEDED CAP "
                f"{args.cap_gb} GB — KILLING TREE",
                file=sys.stderr,
                flush=True,
            )
            _kill_tree(proc)
            killed = True
            break
        time.sleep(POLL_INTERVAL_S)

    rc = child.wait()
    print(f"watchdog: peak tree RSS {peak / 1e9:.2f} GB; child rc={rc}", flush=True)
    if killed:
        return 137
    return rc


if __name__ == "__main__":
    sys.exit(main())
