"""TEST-ONLY support: the PM's real Composer series captures, run through the real producers.

The twelve bodies under ``tests/fixtures/composer/series_captured/`` are raw responses of
``GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}`` (provenance there). Nothing in this
module types a series, a date or a correlation: every record a test reads comes from

* ``parsed`` / ``capture_fetch``: the real ``composer_series_client._parse_series_body`` over a
  capture, at an explicit ``now_et`` (never the wall clock), and
* ``run_over_captures``: the real ``retirement_recommender.build_recommendations_with_summary``
  (``source="composer"``) with only the Composer fetch served from the captures.

``seed_run_from_captures`` writes such a run through the production write path
(``database.save_state`` for the roster and ``database.insert_advisor_observation`` for the
summary row) so the PM can seed a copy of the production database before a screenshot launch.

Not imported by any production module.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import time
from datetime import datetime
from zoneinfo import ZoneInfo

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CAPTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "composer" / "series_captured"
SUMMARY_ROW_PATH = REPO_ROOT / "tests" / "fixtures" / "retirement" / "run_summary_captured.json"

_ET = ZoneInfo("America/New_York")

# The capture time recorded in series_captured/PROVENANCE.md (2026-10-22T20:50:58Z), in ET. Every
# parse in a test names this or another explicit instant: the cut-off rule reads the ET clock.
CAPTURE_NOW_ET = datetime(2026, 10, 22, 16, 50, 58, tzinfo=_ET)

SUMMARY_ROLE = "RETIREMENT_RUN_SUMMARY"


def _index() -> dict:
    return json.loads((CAPTURE_DIR / "_index.json").read_text(encoding="utf-8"))


def capture_ids() -> list[str]:
    """Composer hashes of every capture, in the index's order (the empty one included)."""
    return [entry["symphony_id"] for entry in _index()["symphonies"]]


def capture_name(symphony_id: str) -> str:
    """The display name the index records for a capture (the live name at capture time)."""
    for entry in _index()["symphonies"]:
        if entry["symphony_id"] == symphony_id:
            return entry["name"]
    raise KeyError(symphony_id)


def load_capture(symphony_id: str) -> dict:
    """The raw response body, exactly as committed."""
    return json.loads((CAPTURE_DIR / f"{symphony_id}.json").read_text(encoding="utf-8"))


def parsed(symphony_id: str, now_et: datetime = CAPTURE_NOW_ET):
    """The real client parser over one capture, read at ``now_et``."""
    from advisors import composer_series_client  # noqa: PLC0415 -- test-time import

    return composer_series_client._parse_series_body(  # noqa: SLF001
        load_capture(symphony_id), now_et
    )


def capture_fetch(now_et: datetime = CAPTURE_NOW_ET, per_symphony_now_et=None, errors=None):
    """A stand-in for ``fetch_symphony_daily_returns(account_id, symphony_id)``.

    Serves ``parsed`` captures (``per_symphony_now_et`` maps a hash to the instant that capture is
    read at, every other capture is read at ``now_et``). For a hash in ``errors`` it answers the
    client's own failure result for that error string. ``.calls`` records (account, symphony).
    """
    from advisors import composer_series_client  # noqa: PLC0415 -- test-time import

    per_symphony_now_et = dict(per_symphony_now_et or {})
    errors = dict(errors or {})

    def fetch(account_id, symphony_id):
        fetch.calls.append((account_id, symphony_id))
        if symphony_id in errors:
            return composer_series_client._error_result(errors[symphony_id])  # noqa: SLF001
        return parsed(symphony_id, per_symphony_now_et.get(symphony_id, now_et))

    fetch.calls = []
    return fetch


def seed_roster(ids=None, accounts=None, names=None) -> None:
    """Write the live roster (``bot_state``) for ``ids``: a display name and an account each.

    ``names`` overrides a display name; ``accounts`` maps a hash to its account, and a hash mapped
    to ``None`` gets no ``account`` key (the recommender's ``no_account`` path).
    """
    import database  # noqa: PLC0415 -- test-time import

    ids = list(ids) if ids is not None else capture_ids()
    accounts = accounts or {}
    names = names or {}
    roster = {}
    for symphony_id in ids:
        entry = {"name": names.get(symphony_id, capture_name(symphony_id)), "logic_holdings": {}}
        account = accounts.get(symphony_id, f"acct-{symphony_id}")
        if account is not None:
            entry["account"] = account
        roster[symphony_id] = entry
    database.save_state(roster)


def run_over_captures(
    monkeypatch,
    *,
    ids=None,
    accounts=None,
    names=None,
    days=None,
    now_et: datetime = CAPTURE_NOW_ET,
    per_symphony_now_et=None,
    errors=None,
    seed: bool = True,
):
    """The real ``build_recommendations_with_summary(source="composer")`` over the captures.

    Seeds the roster (unless ``seed`` is False and the database already holds it), neutralises
    ``time.sleep`` (the fetch pacing) and serves the Composer fetch from the captures. The caller
    has already pointed ``database`` at a scratch database. Returns the producer's
    ``(recommendations, summary)``.
    """
    import advisors.composer_series_client as client  # noqa: PLC0415 -- test-time import
    import advisors.retirement_recommender as recommender  # noqa: PLC0415 -- test-time import

    if seed:
        seed_roster(ids, accounts, names)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        client,
        "fetch_symphony_daily_returns",
        capture_fetch(now_et, per_symphony_now_et, errors),
    )
    kwargs = {} if days is None else {"days": days}
    return recommender.build_recommendations_with_summary(source="composer", **kwargs)


def load_captured_summary_row() -> dict:
    """The real stored ``RETIREMENT_RUN_SUMMARY`` ``raw_response`` (row 4025), which predates the
    stored pairs and series windows."""
    rows = json.loads(SUMMARY_ROW_PATH.read_text(encoding="utf-8"))
    return json.loads(rows[0]["raw_response"])


def persist_summary(summary: dict) -> int:
    """Write ``summary`` as one run-summary row through the production accessor; the row id."""
    import database  # noqa: PLC0415 -- test-time import

    return database.insert_advisor_observation(
        advisor_role=SUMMARY_ROLE,
        subject_type="fleet",
        subject_id="retirement",
        verdict=summary.get("status"),
        raw_response=summary,
    )


@contextlib.contextmanager
def _database_at(db_path: str):
    previous = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous


def seed_run_from_captures(db_path: str, *, seed: bool = True):
    """Run the real recommender over the captures and persist its summary into ``db_path``.

    ``seed=True`` also writes the captured roster; ``seed=False`` keeps the database's own
    ``bot_state`` (a copy of production already holds every account). Returns the summary.
    """
    import database  # noqa: PLC0415 -- test-time import

    class _Patch:
        """The few attribute swaps ``run_over_captures`` makes, undone on exit."""

        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value):
            self._undo.append((target, name, getattr(target, name)))
            setattr(target, name, value)

        def undo(self):
            for target, name, original in reversed(self._undo):
                setattr(target, name, original)

    patch = _Patch()
    with _database_at(db_path):
        database.init_db()
        database.run_migrations()
        try:
            _recs, summary = run_over_captures(patch, seed=seed)
        finally:
            patch.undo()
        persist_summary(summary)
    return summary
