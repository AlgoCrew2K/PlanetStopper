"""Build and store the observation rows the Advisor page's status lines are tested over.

TEST-ONLY. The cases are named in ``tests/fixtures/ai_advisor/ux_overhaul_02/observations.json``
(each: a source record and the fields changed; ``PROVENANCE.md`` has the reasons and the
sign-off). A row is written by the real writer ``database.insert_advisor_observation``.

The writer stamps ``created_at`` with the database clock, so every row of a test would carry
one second. ``store`` sets it afterwards with an ``UPDATE`` to a stated, increasing sequence,
which is the only thing it changes after the writer: the stored time is the test's chosen
clock, never an assertion's expected value copied from a run.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta

import database

REPO = pathlib.Path(__file__).resolve().parents[1]
CASES: dict = json.loads(
    (REPO / "tests" / "fixtures" / "ai_advisor" / "ux_overhaul_02" / "observations.json").read_text(
        encoding="utf-8"
    )
)
FIRST_MOMENT = datetime(2026, 10, 20, 14, 0, 0)


def _source_row(source: dict) -> dict:
    if "file" in source:
        shape = json.loads((REPO / source["file"]).read_text(encoding="utf-8"))
        row = dict(shape["example_rows"][source["example_row_index"]])
        return {
            "advisor_role": row["advisor_role"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "verdict": row["verdict"],
            "raw_response": row["raw_response"],
            "spec_bundle_id": None,
        }
    if "producer" in source:
        from advisors.divergence_explainer import compute_divergence_explainer_observation

        assert source["producer"].endswith("compute_divergence_explainer_observation")
        row = compute_divergence_explainer_observation({"id": 7}, None, second_window_enabled=False)
        return {
            "advisor_role": row["advisor_role"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "verdict": row["verdict"],
            "raw_response": row["raw_response"],
            "spec_bundle_id": row["spec_bundle_id"],
        }
    return case_row(source["case"])


def case_row(case: str) -> dict:
    """The insert keyword arguments of one named case."""
    spec = CASES[case]
    row = _source_row(spec["source"])
    row.update(spec["changes"])
    return row


def store(
    case: str,
    *,
    times: int = 1,
    symphony_id: str | None = None,
    start: datetime = FIRST_MOMENT,
    step: timedelta = timedelta(minutes=1),
    **overrides,
) -> list[tuple[int, str]]:
    """Write ``times`` rows of ``case``, oldest first, each with its own ``subject_id`` and a
    ``created_at`` one ``step`` after the previous. Returns ``(row id, created_at)`` pairs."""
    stored: list[tuple[int, str]] = []
    for k in range(times):
        row = {**case_row(case), **overrides}
        row["subject_id"] = f"{row['subject_id']}-{k}"
        row_id = database.insert_advisor_observation(symphony_id=symphony_id, **row)
        created_at = (start + k * step).strftime("%Y-%m-%d %H:%M:%S")
        conn = database.get_connection()
        try:
            conn.execute(
                "UPDATE advisor_observations SET created_at = ? WHERE id = ?", (created_at, row_id)
            )
            conn.commit()
        finally:
            conn.close()
        stored.append((row_id, created_at))
    return stored
