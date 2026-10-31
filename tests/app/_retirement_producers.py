"""TEST-ONLY seam over the two producers of retirement recommendations.

Two jobs write RETIREMENT_RECOMMENDATION rows, and both finish a recommendation the same way, in
the one shared ``app._prepare_retirement_recs_for_persist`` (candidate/sibling names, 2dp rounding,
reuse-or-explain, the explainer's withheld keys, ``explanation_written_on``):

* ``review``      -- the symphony tenure review (``app._tenure_review_job``); its recommendations
                     come from ``advisors.tenure_review_runner.run_tenure_review`` and are stamped
                     with ``review_run_id``;
* ``recommender`` -- the Cycle 2e recommender run (``app._retirement_recommender_tick_worker``);
                     its recommendations come from
                     ``advisors.retirement_recommender.build_recommendations_with_summary`` and are
                     stamped with ``run_id``.

A test about what happens to a recommendation on its way to persistence is a test of the shared
helper, so it runs through both: ``RetirementProducer`` wires one producer to return a given list of
recommendations and runs it. Not imported by any production module.
"""

from __future__ import annotations

import pytest

from tests.app import _tenure_review_job_builders as jb
from tests.app._retirement_run_summary_stub import with_summary

PRODUCER_KINDS = ("review", "recommender")


class RetirementProducer:
    """One producer of recommendations: install what it returns, then run it."""

    def __init__(self, kind: str):
        assert kind in PRODUCER_KINDS, kind
        self.kind = kind
        self.calls: list[dict] = []

    @property
    def stamp_key(self) -> str:
        """The key this producer stamps its run id under on every recommendation."""
        return "review_run_id" if self.kind == "review" else "run_id"

    def install(self, mp, recs, *, stub_explainer: bool = True) -> None:
        """Make the producer return ``recs()`` (a callable giving a fresh list) on every run.

        ``stub_explainer=False`` leaves the explainer alone (for a test that drives the real one);
        the review job's helper otherwise replaces it with canned text, and the recommender
        worker's callers install their own fake."""
        if self.kind == "review":
            self.calls = []
            if stub_explainer:
                self.calls = jb.patch_runner(mp, recs)
            else:
                jb.hide_environment(mp)
                mp.setattr(
                    jb.runner_module,
                    "run_tenure_review",
                    jb.fake_runner(recs, calls=self.calls),
                )
            return
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        mp.setattr(
            recommender_module,
            "build_recommendations_with_summary",
            with_summary(lambda **kw: recs()),
        )

    def run(self) -> None:
        """Run the installed producer once, on the calling thread."""
        import app as app_module  # noqa: PLC0415

        if self.kind == "review":
            app_module._tenure_review_job("nightly")
            jb.assert_review_ran(self.calls, who="the review job")
        else:
            app_module._retirement_recommender_tick_worker()


def producer_fixture(*, autouse: bool = False):
    """A ``producer`` fixture parametrized over both producers, for a test module to assign."""

    @pytest.fixture(params=PRODUCER_KINDS, autouse=autouse)
    def producer(request):
        return RetirementProducer(request.param)

    return producer
