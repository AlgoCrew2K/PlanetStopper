"""Import stub: the implementer replaces this file with the review runner."""

from dataclasses import dataclass

from advisors.tenure_review import ReviewResult


@dataclass(frozen=True)
class RunOutcome:
    completed: bool
    result: ReviewResult | None = None
    failure_reason: str | None = None


def configured_account_ids(env):
    raise NotImplementedError("configured_account_ids")


def run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand"):
    raise NotImplementedError("run_tenure_review")
