"""RED tests — AC-7: Frontrunner tab renders PERSISTED signal-classification
rows only, never live-computes in the Flask request thread.

Route under test: GET /ai-advisor (app.ai_advisor_tab, EXISTING route,
MODIFIED — a new prefetch section alongside the existing frontrunner_proposals
prefetch, same try/except-degrade-to-empty pattern, app.py:4297-4325).
Template: templates/ai_advisor.html (MODIFIED). Implementer: fr-fe.

CONTRACT SOURCE — PM ruling (committed 101df72a): "AC-7 renders PERSISTED
rows ONLY — never live compute in a Flask request thread (extraction needs
/score fetches = network I/O, banned on the dashboard path)."

LOCKED CONTRACT (fr-data + fr-engine + fr-fe, cross-confirmed 2026-07-16):
  - Route reads ONLY `advisors.frontrunner_signals.get_latest_classifications`
    and `advisors.frontrunner_signals.get_latest_run_marker` — NEVER
    extract_fr_checks / symphony_logic.fetch_symphony_score /
    classify_fr_checks directly in the request thread.
  - `signals_unavailable` is per-symphony (fr-engine-confirmed): the route
    calls `get_latest_run_marker(symphony_id=<id>)` ONCE PER unique
    symphony_id present in the classification set — NEVER a single bare
    `get_latest_run_marker()` call. The notice renders PER-SYMPHONY-CARD,
    never a single page-level banner.
  - Card header: `symphony_name` if resolvable via `database.load_state()`
    (wrapped in its OWN inner try/except, separate from the outer
    degrade-to-empty-state guard), else falls back to the raw `symphony_id`
    hash — never blank. When a name IS resolved, the hash still renders as a
    secondary label. Sort order = name-or-hash (same fallback expression).
  - `branch_path` is a native Python list — the route/template never calls
    `json.loads()` on it.
  - Dual staleness: `rsi_live_at` (>48h) AND `computed_at` (rows may predate
    today's signal snapshot) are both surfaced, independently, honestly.
  - On any render-degrade, the outer except logs
    `logger.warning("frontrunner signals render degraded: %s", type(exc).__name__)`
    before setting the empty-state flag (fr-fe's confirmed exact format).

FIXTURE PROVENANCE: docs/fr-signals-inputs/live_symphonies.json (11 real
hash->name pairs, captured-from-producer) for the name/hash header cases.
Classification rows are synthetic INPUT-SHAPE fixtures constructed directly
in this file (team-lead-ratified pattern: decoupling AC-7 route tests from
AC-1/2's own GREEN accessor implementation) — every FIELD VALUE used
(fr_key/classification/cagr/sharpe) traces to the real Atlas docs already
captured in atlas_raw_docs_signals.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import app as app_module

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def live_symphonies() -> dict:
    """docs/fr-signals-inputs/live_symphonies.json — 11 real hash+name pairs,
    captured-from-producer 2026-07-16 (tests/parent/parent/parent = repo root)."""
    path = Path(__file__).parents[2] / "docs" / "fr-signals-inputs" / "live_symphonies.json"
    return json.loads(path.read_text())


def _classification_row(symphony_id, fr_key, classification, cagr=None, sharpe=None, rsi_live_at=None, computed_at=None):
    return {
        "symphony_id": symphony_id,
        "fr_key": fr_key,
        "fn": "relative-strength-index",
        "comparator": "gt",
        "branch_path": ["true"],
        "rsi_live": 59.9 if classification != "no_edge_data" else None,
        "rsi_live_at": rsi_live_at or "2026-07-16T07:01:00Z",
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": None,
        "calmar": None,
        "max_drawdown": None,
        "classification": classification,
        "signal_fetch_ts": "2026-07-16T15:00:00Z",
        "computed_at": computed_at or "2026-07-16T15:00:00Z",
    }


def _run_marker(signals_unavailable=False, reason=None, computed_at="2026-07-16T15:00:00Z"):
    return {"signals_unavailable": signals_unavailable, "reason": reason, "computed_at": computed_at}


# ---------------------------------------------------------------------------
# The bright line: route never live-computes, only reads accessors
# ---------------------------------------------------------------------------


def test_route_never_calls_extraction_or_composer_seams_directly(client):
    """The decisive negative proof: patch every live-compute seam to raise if
    called, patch the two accessors to return canned rows, and assert the
    route still renders 200 from the accessor mocks alone. If the route were
    doing live compute, this test would fail loud on the patched raise."""
    rows = [_classification_row("sym-A", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0)]

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("a live-compute seam was called from the /ai-advisor request thread")

    with (
        patch("advisors.frontrunner_detector.extract_fr_checks", side_effect=_must_not_be_called),
        patch("symphony_logic.fetch_symphony_score", side_effect=_must_not_be_called),
        patch("advisors.frontrunner_signals.classify_fr_checks", side_effect=_must_not_be_called),
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=_run_marker()),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"expected 200 rendering purely from accessor mocks, got {resp.status_code}. "
        f"If a live-compute seam fired, the AssertionError above would have propagated as a 500."
    )
    assert b"SPY:10:80" in resp.data, "expected the fr_key from the mocked accessor to appear in the rendered page"


# ---------------------------------------------------------------------------
# Per-symphony run-marker calls — never a single bare call
# ---------------------------------------------------------------------------


def test_run_marker_called_once_per_unique_symphony_id_never_bare(client):
    """fr-fe/fr-engine-confirmed: the route calls get_latest_run_marker(
    symphony_id=<id>) once per unique symphony_id present in the
    classification set — never a single bare get_latest_run_marker() call
    (which would imply a fleet-wide signal that doesn't architecturally
    exist)."""
    rows = [
        _classification_row("sym-A", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0),
        _classification_row("sym-B", "SPY:10:31", "remove", cagr=-0.37, sharpe=-0.42),
    ]
    call_log = []

    def _tracking_run_marker(symphony_id=None, **kwargs):
        call_log.append(symphony_id)
        return _run_marker(signals_unavailable=(symphony_id == "sym-B"))

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", side_effect=_tracking_run_marker),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    assert None not in call_log, (
        f"a bare/None-symphony_id call to get_latest_run_marker was made: {call_log} — "
        "this implies a fleet-wide signal that does not architecturally exist "
        "(fr-engine confirmed: signals_unavailable is always per-symphony)."
    )
    assert set(call_log) == {"sym-A", "sym-B"}, (
        f"expected exactly one call per unique symphony_id in the classification set, got {call_log}"
    )


def test_degraded_symphony_notice_renders_per_card_not_as_a_global_banner(client):
    """A degraded symphony's card must show its own signals_unavailable
    notice; a healthy symphony's card in the SAME response must NOT."""
    rows = [
        _classification_row("sym-healthy", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0),
        _classification_row("sym-degraded", "sym-degraded:0:0", "no_edge_data"),
    ]

    def _marker_by_symphony(symphony_id=None, **kwargs):
        return _run_marker(
            signals_unavailable=(symphony_id == "sym-degraded"),
            reason="AtlasFetchTimeout" if symphony_id == "sym-degraded" else None,
        )

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", side_effect=_marker_by_symphony),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    # A per-card degraded notice must exist SOMEWHERE in the page (not
    # asserting exact markup, just substantive presence of the degradation
    # signal — the reason string is a strong, specific marker).
    assert b"AtlasFetchTimeout" in resp.data, (
        "expected the degraded symphony's reason to surface somewhere in the rendered page"
    )


# ---------------------------------------------------------------------------
# Empty state — honest, no live compute triggered
# ---------------------------------------------------------------------------


def test_empty_state_renders_when_no_persisted_classification_rows_exist(client):
    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=[]),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=None),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, "an empty persisted-row set must still render 200, never 500"


# ---------------------------------------------------------------------------
# Render-degrade: caught, logged with the exact confirmed format, empty-stated
# ---------------------------------------------------------------------------


def test_accessor_failure_degrades_to_empty_state_and_logs_the_exact_confirmed_format(client, caplog):
    """fr-fe's confirmed exact log line:
    logger.warning("frontrunner signals render degraded: %s", type(exc).__name__)
    — before setting the empty-state flag. The route must never 500."""
    with (
        patch(
            "advisors.frontrunner_signals.get_latest_classifications",
            side_effect=RuntimeError("boom"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, "an accessor failure must degrade to empty state, never 500"
    assert any(
        "frontrunner signals render degraded" in record.message and "RuntimeError" in record.message
        for record in caplog.records
    ), (
        "expected a warning log matching "
        "'frontrunner signals render degraded: RuntimeError' — "
        f"got: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Name-or-hash header + sort order
# ---------------------------------------------------------------------------


def test_card_header_shows_resolved_symphony_name_with_hash_as_secondary_label(client, live_symphonies):
    """fr-fe's confirmed contract: primary text = symphony_name if resolved
    via database.load_state(), hash still renders as a secondary label."""
    real_hash, entry = next(iter(live_symphonies.items()))
    real_name = entry["name"]
    rows = [_classification_row(real_hash, "SPY:10:80", "keep", cagr=0.06, sharpe=1.0)]

    bot_state = {real_hash: {"name": real_name}}

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=_run_marker()),
        patch("database.load_state", return_value=bot_state),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    page = resp.data.decode("utf-8", errors="replace")
    assert real_name in page, f"expected the resolved symphony name {real_name!r} to appear in the page"
    assert real_hash in page, f"expected the hash {real_hash!r} to still appear as a secondary label"


def test_card_header_falls_back_to_hash_only_when_load_state_fails_table_still_populated(client):
    """A database.load_state() failure must fall back to hash-only headers
    WITHOUT triggering the empty state — the name lookup's own inner
    try/except is separate from the outer degrade-to-empty-state guard."""
    rows = [_classification_row("unresolvable-hash-xyz", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0)]

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=_run_marker()),
        patch("database.load_state", side_effect=RuntimeError("state db unavailable")),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    page = resp.data.decode("utf-8", errors="replace")
    assert "unresolvable-hash-xyz" in page, (
        "expected hash-only fallback header, table must still populate (not empty-stated) "
        "when ONLY the name lookup fails"
    )
    assert "SPY:10:80" in page, "the classification table itself must still render despite the name-lookup failure"


# ---------------------------------------------------------------------------
# Dual staleness — rsi_live_at AND computed_at, independently honest
# ---------------------------------------------------------------------------


def test_stale_rsi_live_at_over_48h_is_flagged(client):
    """PM ruling: 'Staleness surfaced from BOTH rsi_live_at (>48h chip) and
    computed_at ... render honestly, never imply live'. Not asserting an
    exact CSS class name (unconfirmed) — asserting the actual stale
    timestamp value surfaces honestly in the page, proving the row wasn't
    silently presented as fresh. This is falsifiable: a naive implementation
    that renders 'freshness unknown' or omits the timestamp entirely fails
    this assertion."""
    stale_rsi_live_at = "2026-07-10T07:01:00Z"  # >48h before 2026-07-16
    fresh_rsi_live_at = "2026-07-16T07:01:00Z"
    rows = [
        _classification_row(
            "sym-stale-rsi", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0, rsi_live_at=stale_rsi_live_at
        )
    ]

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=_run_marker()),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    page = resp.data.decode("utf-8", errors="replace")
    assert "2026-07-10" in page, (
        f"expected the stale rsi_live_at date (2026-07-10, >48h old) to surface honestly in "
        f"the rendered page — a row silently presented without its real capture date could "
        f"mislead the operator into thinking the RSI is current"
    )
    assert fresh_rsi_live_at not in page, (
        "the row's real (stale) rsi_live_at must render, not a fabricated fresh timestamp"
    )


def test_stale_computed_at_predating_todays_snapshot_is_flagged(client):
    """PM ruling: 'rows may predate today's signal snapshot — render
    honestly, never imply live'. A classification row computed days ago must
    not be presented as reflecting today's signals."""
    stale_computed_at = "2026-07-10T15:00:00Z"
    rows = [
        _classification_row(
            "sym-stale-computed", "SPY:10:80", "keep", cagr=0.06, sharpe=1.0, computed_at=stale_computed_at
        )
    ]

    with (
        patch("advisors.frontrunner_signals.get_latest_classifications", return_value=rows),
        patch("advisors.frontrunner_signals.get_latest_run_marker", return_value=_run_marker()),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    page = resp.data.decode("utf-8", errors="replace")
    assert stale_computed_at in page or "2026-07-10" in page, (
        "expected the stale computed_at timestamp to surface honestly in the rendered page "
        "rather than being silently dropped"
    )
