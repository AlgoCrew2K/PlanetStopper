"""Write request_sequences.json: what every advisor surface asks Composer, at the plan's ref.

Run from the repository root, with nothing but tests and feature plans changed since the ref:

    python tests/fixtures/backtest_slippage_calibration/capture_request_sequences.py

It runs each entry point of ``tests/advisors/_slippage_surface_worlds.RUNNERS`` under pytest (the
suite's own autouse fixtures, the model registry seed and the database isolation, are part of
the world), once per test, with ``-n0``. Each run writes one entry: every backtest request's
URL, body form (``json`` or ``data``) and body with ``slippage_percent`` removed and the tree
ids replaced by the order of their first appearance in the sequence, plus the run's gate
outcome. The runner then merges the entries into ``request_sequences.json`` beside this file.

It refuses to run when anything outside ``tests/`` and ``feature-plans/`` differs from the ref,
so the goldens are always the unchanged code's behaviour and never a later build's.

The test at the bottom is collected only when this file is named on the pytest command line (the
suite's ``python_files`` setting is ``test_*.py``) and does nothing unless the runner set
``SLIPPAGE_GOLDEN_DIR``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REF_SHA = "c3e12882141ae601f900fdc13e0c342462a739bb"
OUT = Path(__file__).resolve().parent / "request_sequences.json"
ENV_DIR = "SLIPPAGE_GOLDEN_DIR"


def changed_outside_tests() -> list[str]:
    """Paths other than tests and feature plans that differ from the ref (empty when clean)."""
    out = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "diff",
            "--name-only",
            REF_SHA,
            "--",
            ".",
            ":(exclude)tests",
            ":(exclude)feature-plans",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def main() -> None:
    changed = changed_outside_tests()
    if changed:
        raise SystemExit(f"refusing to capture: {changed} differ from the ref {REF_SHA[:8]}")
    scratch = Path(tempfile.mkdtemp(prefix="slippage-goldens-"))
    env = {**os.environ, ENV_DIR: str(scratch)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__)), "-n0", "-q"],
        cwd=REPO,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"the capture run failed with exit status {result.returncode}")
    golden = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(scratch.glob("*.json"))
    }
    OUT.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"ref {REF_SHA}, captured at {head}: wrote {len(golden)} entry points to {OUT}")


if __name__ == "__main__":
    # Run as a script it must not import the application (its database set-up is import-time).
    main()
else:
    import pytest

    from tests.advisors._slippage_surface_worlds import RUNNERS, golden_view

    @pytest.mark.parametrize("entry", list(RUNNERS))
    def test_capture_entry_point(entry, monkeypatch, tmp_path):
        target = os.environ.get(ENV_DIR)
        if not target:
            return
        assert changed_outside_tests() == [], "the capture must run on the ref's behaviour"
        run = RUNNERS[entry](monkeypatch, tmp_path)
        assert run.backtests, f"{entry} sent no backtest"
        view = json.loads(json.dumps(golden_view(run)))
        (Path(target) / f"{entry}.json").write_text(json.dumps(view), encoding="utf-8")
