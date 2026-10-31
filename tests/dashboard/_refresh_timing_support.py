"""Support for the dashboard refresh tests: derived bodies, real error bodies, event payloads.

What is real here: every body a scenario serves is the output of a real producer. The world's
``/api/state``, ``/api/strip/<window>``, ``/api/guard-alpha-summary`` and ``/api/hero-chart/<window>``
bodies come from the Flask routes over a seeded database (``_index_js_harness``); the cycle event
payload comes from ``app.CycleRecord.event_payload``; the error bodies come from the routes' own
refusals (``error_response``). A *derived* body changes only the fields its builder names on one such
body; each builder is listed, with its source and reason, in
``tests/fixtures/refresh_timing/03-c1/PROVENANCE.md`` to ``03-c3/PROVENANCE.md``.

No expected text is copied from a capture. Expected figures are recomputed from the body a scenario
serves, with the formatting the client applies, or read back from the page before the step under test.
"""

from __future__ import annotations

import copy
import re
from email.utils import formatdate
from unittest.mock import patch

from tests.dashboard import _index_js_harness as hx

CHROME_AND_INDEX = ("static/chrome.js", "static/index.js")
INDEX_ONLY = ("static/index.js",)

_STAMP_RE = re.compile(r"^(?P<day>\d{4}-\d\d-\d\d) (?P<hour>\d\d):(?P<minute>\d\d) ET$")
AS_OF_PREFIX = "data as of "
DASH = "—"
CONNECTION_LOST_AS_OF_PREFIX = "connection lost · " + AS_OF_PREFIX
POLL_INTERVAL_MS = 30_000
MS_PER_MINUTE = 60_000


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------


def stamp_of(state: dict) -> str | None:
    """The data stamp the client reads: ``meta.portfolio.data_as_of`` else the top-level one."""
    portfolio = (state.get("meta") or {}).get("portfolio") or {}
    return portfolio.get("data_as_of") or state.get("data_as_of") or None


def render_stamp(world: dict) -> str:
    """The stamp the server render prints beside the hero chart, without its ``data as of`` prefix."""
    node = hx.find_by_id(hx.parse_html(world["html"]), "hero-data-as-of")
    assert node is not None, "the server render holds no #hero-data-as-of"
    text = " ".join(hx.tree_text(node).split())
    assert text.startswith(AS_OF_PREFIX), f"unexpected as-of text on the render: {text!r}"
    return text[len(AS_OF_PREFIX) :]


def later_stamp(stamp: str, minutes: int = 1) -> str:
    """A stamp of the same shape ``minutes`` after ``stamp`` (the shape the server prints: ``D HH:MM ET``)."""
    import datetime as dt

    match = _STAMP_RE.match(stamp)
    assert match, f"the stamp {stamp!r} does not have the shape 'YYYY-MM-DD HH:MM ET'"
    moment = dt.datetime.fromisoformat(f"{match['day']}T{match['hour']}:{match['minute']}:00")
    moment += dt.timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%d %H:%M ET")


# ---------------------------------------------------------------------------
# Derived /api/state bodies (one named field changed on a real body)
# ---------------------------------------------------------------------------


def with_stamp(state: dict, stamp: str) -> dict:
    """The body with its data stamp replaced, everywhere the server sets it."""
    out = copy.deepcopy(state)
    if "data_as_of" in out:
        out["data_as_of"] = stamp
    portfolio = (out.get("meta") or {}).get("portfolio")
    if isinstance(portfolio, dict):
        portfolio["data_as_of"] = stamp
    strip = out.get("portfolio_strip")
    if isinstance(strip, dict):
        strip["data_as_of"] = stamp
    return out


def with_guard_alpha(state: dict, value) -> dict:
    """The body with the poll's own guard alpha (the 30d default window's) replaced."""
    out = copy.deepcopy(state)
    out["portfolio_strip"]["guard_alpha"] = value
    portfolio = (out.get("meta") or {}).get("portfolio")
    if isinstance(portfolio, dict):
        portfolio["guard_alpha"] = value
    return out


def with_poll_vol(state: dict, vol_bot, vol_held) -> dict:
    """The body with the poll's own lifetime vol fields (``meta.portfolio.vol_bot``/``vol_held``) set."""
    out = copy.deepcopy(state)
    out["meta"]["portfolio"]["vol_bot"] = vol_bot
    out["meta"]["portfolio"]["vol_held"] = vol_held
    return out


def with_top(state: dict, **fields) -> dict:
    out = copy.deepcopy(state)
    out.update(fields)
    return out


def closed_body(state: dict) -> dict:
    """The body as served while the market is closed: closed state, closed phase, system offline."""
    out = copy.deepcopy(state)
    out["market_state"] = "closed_frozen"
    out["engine_phase"] = "closed"
    out["meta"]["market_state"] = "closed_frozen"
    out["meta"]["system_online"] = False
    return out


def open_body(state: dict, phase: str = "running") -> dict:
    """The body as served while the market is open."""
    out = copy.deepcopy(state)
    out["market_state"] = "open"
    out["engine_phase"] = phase
    out["meta"]["market_state"] = "open"
    out["meta"]["system_online"] = True
    return out


def with_cycle_at(state: dict, iso: str | None) -> dict:
    """The body with the engine's last successful cycle stamp set (``None`` removes the value)."""
    out = copy.deepcopy(state)
    out["last_successful_cycle_at"] = iso
    return out


def with_next_run(state: dict, seconds: int) -> dict:
    out = copy.deepcopy(state)
    out["next_run_seconds"] = seconds
    return out


# ---------------------------------------------------------------------------
# Derived per-symphony edits (one named field of one entry changed on a real body)
# ---------------------------------------------------------------------------


def card_entry(state: dict, symphony_id: str) -> dict:
    """The poll's ``symphonies`` entry (the card) for a symphony id."""
    return next(s for s in state["symphonies"] if s["id"] == symphony_id)


def with_entry(state: dict, symphony_id: str, **fields) -> dict:
    """The body with named fields of one symphony changed in the card list and in the state map.

    ``None`` as a value sets the field to null; use ``without_entry_key`` to remove a key.
    """
    out = copy.deepcopy(state)
    card_entry(out, symphony_id).update(fields)
    for key in ("bot_state", "state"):
        entry = (out.get(key) or {}).get(symphony_id)
        if isinstance(entry, dict):
            entry.update(fields)
    return out


def without_entry_key(state: dict, symphony_id: str, key: str) -> dict:
    """The body with one key removed from a symphony's card and state entry."""
    out = copy.deepcopy(state)
    card_entry(out, symphony_id).pop(key, None)
    for holder in ("bot_state", "state"):
        entry = (out.get(holder) or {}).get(symphony_id)
        if isinstance(entry, dict):
            entry.pop(key, None)
    return out


def with_card_only(state: dict, symphony_id: str, **fields) -> dict:
    """The body with named fields changed on the card list entry alone (the state map untouched)."""
    out = copy.deepcopy(state)
    card_entry(out, symphony_id).update(fields)
    return out


def with_state_only(state: dict, symphony_id: str, **fields) -> dict:
    """The body with named fields changed on the state map entry alone (the card list untouched)."""
    out = copy.deepcopy(state)
    for key in ("bot_state", "state"):
        entry = (out.get(key) or {}).get(symphony_id)
        if isinstance(entry, dict):
            entry.update(fields)
    return out


def for_account(state: dict, account: str) -> dict:
    """The body as the route serves it for one picked account: other accounts' entries are dropped
    from the card list and the state map."""
    out = copy.deepcopy(state)
    out["symphonies"] = [s for s in out["symphonies"] if s.get("account") == account]
    for key in ("bot_state", "state"):
        holder = out.get(key)
        if isinstance(holder, dict):
            for sym_id in [
                k
                for k, v in holder.items()
                if isinstance(v, dict) and "name" in v and v.get("account") != account
            ]:
                del holder[sym_id]
    return out


def without_symphony(state: dict, symphony_id: str) -> dict:
    """The body with one symphony absent from the card list and the state map (as after an account pick)."""
    out = copy.deepcopy(state)
    out["symphonies"] = [s for s in out["symphonies"] if s["id"] != symphony_id]
    for key in ("bot_state", "state"):
        holder = out.get(key)
        if isinstance(holder, dict):
            holder.pop(symphony_id, None)
    return out


def with_card_order(state: dict, ids: list[str]) -> dict:
    """The body with the ``symphonies`` list in the given id order."""
    out = copy.deepcopy(state)
    by_id = {s["id"]: s for s in out["symphonies"]}
    assert set(ids) == set(by_id), "the order must name every card once"
    out["symphonies"] = [by_id[i] for i in ids]
    return out


# ---------------------------------------------------------------------------
# Derived strips and summaries
# ---------------------------------------------------------------------------


def strip_with(strip: dict, **fields) -> dict:
    out = copy.deepcopy(strip)
    out.update(fields)
    return out


def fmt_pct(value, decimals: int = 2) -> str:
    """The client's percentage format (``fmtPct``): signed, fixed decimals, ``--`` for a missing value."""
    if value is None:
        return "--"
    return ("+" if value >= 0 else "") + f"{value:.{decimals}f}%"


def fmt_pp(value, decimals: int = 2) -> str:
    """The headline's format: a difference of two returns, signed, fixed decimals, then ``pp``
    (percentage points); ``--`` for a missing value."""
    if value is None:
        return "--"
    return ("+" if value >= 0 else "") + f"{value:.{decimals}f} pp"


def vol_texts(strip: dict) -> dict:
    """The Ann. Vol row text for a strip, formatted as the client formats it (dashes when either side is null)."""
    bot, held = strip.get("vol_bot"), strip.get("vol_held")
    if bot is None or held is None:
        return {"bot": f"Bot {DASH}", "held": f"Held {DASH}", "delta": f"α {DASH}"}
    pp = (held - bot) * 100
    return {
        "bot": f"Bot {bot * 100:.1f}%",
        "held": f"Held {held * 100:.1f}%",
        "delta": f"α {'+' if pp >= 0 else ''}{pp:.1f}pp",
    }


def summary_with(summary: dict, *, saved: float, events: int, coverage: tuple[int, int]) -> dict:
    """A $-saved summary body carrying chosen totals (so two windows read differently on the panel)."""
    out = copy.deepcopy(summary)
    out["cumulative_saved_dollars"] = saved
    out["cumulative_saved_dollars_net_of_friction"] = saved
    out["guard_event_count"] = events
    out["saved_dollars_realized"] = saved
    out["saved_dollars_realized_net_of_friction"] = saved
    out["realized_coverage"] = {"with_data": coverage[0], "total": coverage[1]}
    return out


def saved_headline_text(saved: float) -> str:
    """The panel's headline for a total, as the client writes it (magnitude, two decimals)."""
    return f"${abs(saved):.2f}"


# ---------------------------------------------------------------------------
# The cycle-complete event payload (real producer)
# ---------------------------------------------------------------------------


def event_payload(changed: bool = True) -> dict:
    """What the daemon puts in a cycle-complete event's data, from ``app.CycleRecord.event_payload``."""
    import app as app_module

    record = app_module.CycleRecord(
        cycle_id="2026-10-30T10:14:00-04:00#1",
        started_at="2026-10-30T10:14:00-04:00",
        completed_at="2026-10-30T10:14:04-04:00",
        outcome="cycle",
        changed=changed,
        exit_code=0,
        running_cycle_id=None,
        running_since=None,
    )
    return record.event_payload()


# ---------------------------------------------------------------------------
# Real error responses of GET /api/state and the CSRF gate
# ---------------------------------------------------------------------------


def error_response(kind: str, monkeypatch) -> dict:
    """The status, content type and body the real app answers with for one failure.

    ``500`` an unexpected fault inside the route; ``401`` no session with the auth gate on; ``503``
    the auth gate on with no secret key; ``403-html`` a POST without a CSRF token (Werkzeug's HTML page).
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    if kind == "500":

        def _boom(*_args, **_kwargs):
            raise RuntimeError("seam failure")

        # A context manager, undone before the suite's own stub is: monkeypatch would save that
        # stub as the value to restore and could write it back after the suite's undo.
        with patch.object(app_module, "get_api_state_dict", _boom):
            response = client.get("/api/state")
    elif kind in ("401", "503"):
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        monkeypatch.setenv("DASHBOARD_PASSWORD", "refresh-timing-password")
        if kind == "401":
            monkeypatch.setenv("SECRET_KEY", "refresh-timing-secret")
        else:
            monkeypatch.delenv("SECRET_KEY", raising=False)
            monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        response = client.get("/api/state")
    elif kind == "403-html":
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        response = client.post("/api/trigger")
    else:  # pragma: no cover
        raise AssertionError(kind)
    out = {
        "status": response.status_code,
        "content_type": response.content_type,
        "text": response.get_data(as_text=True),
    }
    body = response.get_json(silent=True)
    if body is not None:
        out["body"] = body
    return out


def as_route_spec(response: dict) -> dict:
    """A harness route spec that serves a real error response exactly (JSON body or HTML text)."""
    if "body" in response:
        return {"status": response["status"], "body": response["body"]}
    return {"status": response["status"], "text": response["text"]}


def waiting_body(monkeypatch) -> dict:
    """The body ``GET /api/state`` serves on a fresh deploy after the close: no state yet, with its notice."""
    import app as app_module

    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "closed_frozen")
    app_module.app.config["TESTING"] = True
    response = app_module.app.test_client().get("/api/state")
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_json()


# ---------------------------------------------------------------------------
# Clock helpers
# ---------------------------------------------------------------------------


def iso_z(ms: int) -> str:
    """An instant as the UTC ISO text the harness's ``now_iso`` takes."""
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def et_iso(ms: int) -> str:
    """A cycle stamp in the engine's format: aware, with the Eastern offset."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    return dt.datetime.fromtimestamp(ms / 1000, ZoneInfo("America/New_York")).isoformat()


def http_date(epoch_ms: int) -> str:
    """An HTTP ``Date`` header value (RFC 9110 IMF-fixdate) for an instant."""
    return formatdate(epoch_ms / 1000, usegmt=True)


def run(
    scenario: str,
    *,
    world: str = "default",
    scripts: tuple[str, ...] = INDEX_ONLY,
    extra_bodies: dict | None = None,
    now_iso: str | None = None,
    timeout: int = 180,
) -> dict:
    """Run a scenario against a world; the page's clock is pinned to the world's seeded day unless given."""
    built = hx.get_world(world)
    return hx.run_scenario(
        built,
        scenario,
        scripts=scripts,
        extra_bodies=extra_bodies,
        now_iso=now_iso if now_iso is not None else hx.seeded_now_iso(built),
        timeout=timeout,
    )["result"]
