"""TEST-ONLY world builders for the Retirement tab tests.

Every record the tab renders comes from a real producer or a real writer:

* the ``ok`` run summary is the captured row ``tests/fixtures/retirement/run_summary_captured.json``
  (a real ``RETIREMENT_RUN_SUMMARY`` row exported from the droplet, nothing edited), stored through
  ``database.insert_advisor_observation``;
* every other summary status is the output of the real recommender,
  ``advisors.retirement_recommender.build_recommendations_with_summary(source=...)``, run over a
  roster of captured symphonies with the Composer series request answered at the network boundary
  (``requests.get``) or the seam the recommender's own tests use, or of
  ``app._retirement_internal_error_summary``; stored through ``app._persist_retirement_run_summary``;
* recommendation cards are written by the real tick worker ``app._retirement_recommender_tick_worker``
  (real recommender, real explainer on a real ``anthropic.Anthropic`` client over the in-process
  HTTP fake answering with a captured reply), and approvals and rejections are the real routes.

Derived records (a captured summary with one named field changed) are made by ``derived_summary``
and are listed in ``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md``.

Not imported by any production module.
"""

from __future__ import annotations

import json
import pathlib
import types
from contextlib import contextmanager
from datetime import UTC, datetime

import app as app_module
import database
from tests.app import _tenure_review_panel_builders as pb

REPO = pathlib.Path(__file__).resolve().parents[2]
CAPTURE_ROW = json.loads(
    (REPO / "tests" / "fixtures" / "retirement" / "run_summary_captured.json").read_text(
        encoding="utf-8"
    )
)[0]
CAPTURED_SUMMARY: dict = json.loads(CAPTURE_ROW["raw_response"])
CAPTURED_ROSTER: dict = json.loads(
    (REPO / "tests" / "fixtures" / "prod_droplet" / "bot_state_positions.json").read_text(
        encoding="utf-8"
    )
)
# The one symphony the captured run excluded, and its live display name.
EXCLUDED_ID: str = CAPTURED_SUMMARY["symphonies_without_series"][0]["symphony_id"]
EXCLUDED_NAME: str = CAPTURED_ROSTER[EXCLUDED_ID]["name"]

# The instant every summary a test builds is stamped at (summer time, so New York is UTC-4).
PINNED_RUN_AT = "2026-10-22T18:50:08Z"
DASH = "—"  # the missing-value character the Advisor page prints for an absent value
SUMMARY_ROLE = "RETIREMENT_RUN_SUMMARY"
REC_ROLE = "RETIREMENT_RECOMMENDATION"


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


def seed_roster(*, without: tuple[str, ...] = ()) -> None:
    """The captured droplet roster, verbatim, written through ``database.save_state``."""
    database.save_state({k: v for k, v in CAPTURED_ROSTER.items() if k not in without})


def store_summary(summary: dict) -> int:
    """One summary row through the real writer, exactly as the tick worker writes it."""
    app_module._persist_retirement_run_summary(summary, summary.get("recommendations") or 0)
    rows = database.get_advisor_observations_for_role(SUMMARY_ROLE, limit=1)
    return rows[0]["id"]


def store_raw_summary(summary: dict) -> None:
    """One summary row exactly as given (no count overwrite), through the real row writer."""
    database.insert_advisor_observation(
        advisor_role=SUMMARY_ROLE,
        subject_type="fleet",
        subject_id="retirement",
        verdict=summary.get("status"),
        raw_response=summary,
    )


def store_captured_summary() -> dict:
    """The captured ``ok`` summary, stored as captured."""
    database.insert_advisor_observation(
        advisor_role=SUMMARY_ROLE,
        subject_type=CAPTURE_ROW["subject_type"],
        subject_id=CAPTURE_ROW["subject_id"],
        verdict=CAPTURE_ROW["verdict"],
        raw_response=json.loads(CAPTURE_ROW["raw_response"]),
    )
    return json.loads(CAPTURE_ROW["raw_response"])


def derived_summary(**changes) -> dict:
    """The captured summary with the named fields changed and nothing else (a derived record)."""
    out = json.loads(CAPTURE_ROW["raw_response"])
    out.update(changes)
    return out


def without_keys(*keys: str) -> dict:
    """The captured summary with the named keys removed (the missing-field edge of a record)."""
    out = json.loads(CAPTURE_ROW["raw_response"])
    for key in keys:
        out.pop(key, None)
    return out


def stored_summary_json() -> list[str]:
    """The raw stored text of every summary row, oldest first."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT raw_response FROM advisor_observations WHERE advisor_role = ? ORDER BY id",
            (SUMMARY_ROLE,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Real producers
# ---------------------------------------------------------------------------


@contextmanager
def pinned_run_clock(monkeypatch, iso_utc: str):
    """The recommender stamps ``run_at`` from ``datetime.now(UTC)``; pin that instant."""
    import advisors.retirement_recommender as rr

    pinned = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return pinned if tz is None else pinned.astimezone(tz)

    monkeypatch.setattr(rr, "datetime", _Pinned)
    yield


def _http_status_reply(status: int):
    return types.SimpleNamespace(status_code=status, headers={}, json=lambda: {})


def run_recommender_all_requests_refused(
    monkeypatch, *, count: int = 3, status: int = 429, run_at: str = PINNED_RUN_AT
) -> dict:
    """The real recommender over ``count`` captured symphonies whose Composer series request is
    answered with HTTP ``status`` at ``requests.get``. Gives a ``series_unavailable`` summary whose
    exclusions are ``fetch_error: HTTP <status>`` as the real client words them."""
    import requests

    import advisors.retirement_recommender as rr

    ids = [k for k, v in CAPTURED_ROSTER.items() if isinstance(v, dict) and v.get("account")]
    database.save_state({k: CAPTURED_ROSTER[k] for k in ids[:count]})
    monkeypatch.setattr(requests, "get", lambda *a, **k: _http_status_reply(status))
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    with pinned_run_clock(monkeypatch, run_at):
        _recs, summary = rr.build_recommendations_with_summary(source="composer")
    return summary


def run_recommender_one_symphony(monkeypatch, run_at: str = PINNED_RUN_AT) -> dict:
    """The real recommender over a roster of one captured symphony: ``insufficient_symphonies``."""
    import advisors.retirement_recommender as rr

    first = next(k for k, v in CAPTURED_ROSTER.items() if isinstance(v, dict) and "name" in v)
    database.save_state({first: CAPTURED_ROSTER[first]})
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    with pinned_run_clock(monkeypatch, run_at):
        return rr.build_recommendations_with_summary(source="composer")[1]


def run_recommender_mixed_exclusions(monkeypatch, run_at: str = PINNED_RUN_AT) -> dict:
    """The real recommender over captured symphonies where one has no account (``no_account``)
    and the rest are refused with HTTP 429 (``fetch_error: HTTP 429``)."""
    import requests

    import advisors.retirement_recommender as rr

    ids = [k for k, v in CAPTURED_ROSTER.items() if isinstance(v, dict) and v.get("account")]
    roster = {k: dict(CAPTURED_ROSTER[k]) for k in ids[:3]}
    first = next(iter(roster))
    roster[first].pop("account")
    database.save_state(roster)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _http_status_reply(429))
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    with pinned_run_clock(monkeypatch, run_at):
        return rr.build_recommendations_with_summary(source="composer")[1]


def run_recommender_shadow_without_history(monkeypatch, run_at: str = PINNED_RUN_AT) -> dict:
    """The real recommender on its shadow source over symphonies that have no shadow rows:
    exclusions read ``shadow_series_unavailable``."""
    import advisors.retirement_recommender as rr

    ids = [k for k, v in CAPTURED_ROSTER.items() if isinstance(v, dict) and "name" in v]
    database.save_state({k: CAPTURED_ROSTER[k] for k in ids[:3]})
    with pinned_run_clock(monkeypatch, run_at):
        return rr.build_recommendations_with_summary(source="shadow")[1]


def internal_error_summary(
    monkeypatch, error_type: str = "ConnectionError", run_at: str = PINNED_RUN_AT
) -> dict:
    """The app's own summary for a run that failed, stamped at ``run_at``."""
    pinned = datetime.strptime(run_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return pinned if tz is None else pinned.astimezone(tz)

    monkeypatch.setattr(app_module, "datetime", _Pinned)
    try:
        return app_module._retirement_internal_error_summary(error_type)
    finally:
        monkeypatch.setattr(app_module, "datetime", datetime)


# ---------------------------------------------------------------------------
# The real tick worker, for cards
# ---------------------------------------------------------------------------


def install_series(monkeypatch, series_by_symphony: dict) -> None:
    """Seed a roster of the given symphonies (id = display name, one account each) and answer the
    Composer series request through the seam the recommender's own tests use."""
    import advisors.composer_series_client as client
    from advisors.composer_series_client import SeriesResult

    database.save_state(
        {
            sid: {"name": sid, "account": f"acct-{sid}", "logic_holdings": {}}
            for sid in series_by_symphony
        }
    )

    def fetch(account_id, symphony_id):
        item = series_by_symphony[symphony_id]
        return SeriesResult(
            returns=dict(item),
            points=len(item),
            first_date=min(item),
            last_date=max(item),
            error=None,
        )

    monkeypatch.setattr(client, "fetch_symphony_daily_returns", fetch)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)


def run_ok_summary(monkeypatch, series_by_symphony: dict, run_at: str) -> dict:
    """The real recommender's ``ok`` summary over the given series, stamped at ``run_at``."""
    import advisors.retirement_recommender as rr

    install_series(monkeypatch, series_by_symphony)
    with pinned_run_clock(monkeypatch, run_at):
        _recs, summary = rr.build_recommendations_with_summary(source="composer")
    assert summary["status"] == "ok", summary
    return summary


def run_tick_with_recommendations(
    monkeypatch, series_by_symphony: dict, run_at: str = PINNED_RUN_AT
) -> dict:
    """Run ``app._retirement_recommender_tick_worker`` over the given Composer series.

    The explainer talks to a real client on the in-process Anthropic fake. Returns the stored
    summary.
    """
    from tests._anthropic_http_fake import AnthropicHttpFake
    from tests._suggest_and_chat_routes import text_reply, use_client

    install_series(monkeypatch, series_by_symphony)
    fake = AnthropicHttpFake.with_captured_page()
    for _ in series_by_symphony:
        fake.queue_message(text_reply("This symphony adds little beyond its closest sibling."))
    use_client(monkeypatch, fake)
    with pinned_run_clock(monkeypatch, run_at):
        app_module._retirement_recommender_tick_worker()
    rows = database.get_advisor_observations_for_role(SUMMARY_ROLE, limit=1)
    assert rows, "the tick worker wrote no run summary"
    return rows[0]["raw_response"]


def stored_recommendation_ids() -> list[str]:
    rows = database.get_advisor_observations_for_role(REC_ROLE, limit=50)
    return sorted(r["raw_response"]["candidate_id"] for r in rows)


def decide(client, action: str, candidate_id: str) -> dict:
    """POST the real approve or reject route."""
    response = client.post(f"/ai-advisor/retirement/{action}", json={"candidate_id": candidate_id})
    assert response.status_code == 200
    return response.get_json()


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------


def get_page(client) -> str:
    response = client.get("/ai-advisor")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def tab_panel(html: str, name: str) -> pb.Node:
    panel = pb.parse_html(html).find(f"tab-panel-{name}")
    assert panel is not None, f"the page has no tab-panel-{name}"
    return panel


def retirement_section(html: str) -> pb.Node:
    section = pb.parse_html(html).find("retirement-recommendations-panel")
    assert section is not None, "the page has no retirement-recommendations-panel"
    return section


def by_class(node: pb.Node, class_name: str) -> list[pb.Node]:
    return [n for n in node.walk() if class_name in n.classes()]


def tab_names(html: str) -> list[str]:
    """The ``data-tab`` of every ``role="tab"`` button, in page order."""
    return [
        n.attrs["data-tab"]
        for n in pb.parse_html(html).walk()
        if n.attrs.get("role") == "tab" and "data-tab" in n.attrs
    ]


def et_text(utc_text: str, fmt: str, offset_hours: int) -> str:
    """``YYYY-MM-DD HH:MM ET`` for a UTC text by stated arithmetic (UTC-4 in summer time)."""
    from datetime import timedelta

    return (datetime.strptime(utc_text, fmt) + timedelta(hours=offset_hours)).strftime(
        "%Y-%m-%d %H:%M"
    ) + " ET"
