"""Import stub for the RED tests; the implementer replaces this module."""

from typing import NamedTuple


class RunOutcome(NamedTuple):
    run_id: str
    status: str
    completed: bool


class LatestRuns(NamedTuple):
    latest: dict | None
    latest_completed: dict | None


def run_ticker_health_check(**_kwargs) -> RunOutcome:
    return RunOutcome("", "failed", False)


def read_latest_runs(*, scan_limit: int = 365) -> LatestRuns:
    return LatestRuns(None, None)
