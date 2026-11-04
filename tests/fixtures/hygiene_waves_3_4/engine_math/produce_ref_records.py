"""Producer of the the engine and math cycle reference records.

Refuses unless the production files the records exercise equal the reference commit and carry no
uncommitted change (the refusal pattern of
``tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py``), then
runs ``capture_ref_records.py`` under pytest with ``-n0``.

    python tests/fixtures/hygiene_waves_3_4/engine_math/produce_ref_records.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
REFERENCE_SHA = "7adc2c56953a3873c013425ebb06033375b62c53"
WATCHED = (
    "alpha_bot_execution.py",
    "math_engine.py",
    "autotuner.py",
    "regime_classifier.py",
    "app.py",
    "database.py",
    "analytics.py",
    "sleeves/tick_orchestrator.py",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def refuse_unless_production_equals_the_reference() -> None:
    if _git("diff", "--quiet", REFERENCE_SHA, "--", *WATCHED).returncode != 0:
        raise SystemExit(f"refusing: {WATCHED} differ from {REFERENCE_SHA}")
    if _git("status", "--porcelain", "--", *WATCHED).stdout.strip():
        raise SystemExit(f"refusing: {WATCHED} have uncommitted changes")
    if (REPO / "env_flags.py").exists():
        raise SystemExit("refusing: env_flags.py exists, the tree is not the reference")


def main() -> int:
    refuse_unless_production_equals_the_reference()
    capture = HERE / "capture_ref_records.py"
    (HERE / "env_flag_bare_key_outcomes.json").unlink(missing_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-n0", "-p", "no:cacheprovider", "-q", str(capture)],
        cwd=str(REPO),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
