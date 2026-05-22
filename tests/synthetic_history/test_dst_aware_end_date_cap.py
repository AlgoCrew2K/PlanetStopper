"""Cluster 5 AC-2 — RED tests: the synthetic-history end-date cap must use a
DST-aware US Eastern zone, not a hardcoded UTC-4 offset.

THE DEFECT
----------
``synthetic_history.generate_synthetic_history`` computes the "today in US
markets" reference used to cap the fetch end date with::

    now_us = now_utc + timedelta(hours=-4)

A fixed -4 is the US Eastern offset ONLY during daylight saving time
(EDT, mid-March to early November). From early November to mid-March US
Eastern is EST = UTC-5. For ~5 months of the year the hardcoded -4 computes
the US wall clock one hour LATE — and during that hour-wide window around UTC
midnight the derived ``today_us`` rolls to the wrong calendar day, shifting
the fetch end-date cap by a full trading day (audit autotuner__2026-05-21.md
finding 11).

THE CONTRACT
------------
The conversion must use a DST-aware zone — ``zoneinfo.ZoneInfo`` with
``America/New_York``, exactly as ``market_calendar.py`` already does. The
resolved ET wall clock must be correct in BOTH:
  - EST (standard time, winter):  UTC-5
  - EDT (daylight time, summer):  UTC-4

TESTABILITY
-----------
The UTC -> ET conversion is currently buried inline in
``generate_synthetic_history`` (which performs live Alpaca fetches and cannot
run under pytest). The implementer must extract the conversion into a pure,
importable helper that takes a UTC ``datetime`` and returns the corresponding
ET ``datetime`` (or the ET calendar date). These tests import that helper and
assert the offset directly against ``zoneinfo`` — the independent reference.

PROVENANCE
----------
Expected offsets are computed independently from ``zoneinfo.ZoneInfo`` via
``utcoffset()`` on the test dates — never read back from the producer. No
producer-computed value is hardcoded. ``pytest.approx`` is not used: UTC
offsets and calendar dates are exact.
"""

from __future__ import annotations

import ast
import datetime as _dt
import pathlib
from zoneinfo import ZoneInfo

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"

_ET = ZoneInfo("America/New_York")
_UTC = _dt.timezone.utc

# Reference instants. Each is a UTC datetime; the independent zoneinfo
# reference tells us what ET offset / wall clock it must resolve to.
#   - Winter: mid-January, deep in EST (UTC-5).
#   - Summer: mid-July, deep in EDT (UTC-4).
_WINTER_UTC = _dt.datetime(2026, 1, 15, 14, 30, tzinfo=_UTC)
_SUMMER_UTC = _dt.datetime(2026, 7, 15, 14, 30, tzinfo=_UTC)

# Independent expected offsets — from zoneinfo, the reference implementation.
_EXPECTED_WINTER_OFFSET = _ET.utcoffset(_WINTER_UTC.replace(tzinfo=None))
_EXPECTED_SUMMER_OFFSET = _ET.utcoffset(_SUMMER_UTC.replace(tzinfo=None))


# ---------------------------------------------------------------------------
# Helper resolution — import-guarded so a not-yet-extracted helper fails RED
# with a clear message rather than ImportError at collection.
# ---------------------------------------------------------------------------


def _resolve_et_helper():
    """Return the pure UTC->ET conversion helper synthetic_history must expose.

    Contract: a callable taking a UTC datetime and returning the corresponding
    US Eastern datetime (DST-aware). The implementer chooses the exact name;
    this resolver accepts the documented candidates and fails RED if none
    exists.
    """
    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    for name in (
        "utc_to_eastern",
        "to_eastern",
        "now_eastern_from_utc",
        "eastern_datetime_from_utc",
    ):
        fn = getattr(synth, name, None)
        if callable(fn):
            return fn
    pytest.fail(
        "synthetic_history must expose a PURE, importable DST-aware UTC->ET "
        "conversion helper (expected one of: utc_to_eastern / to_eastern / "
        "now_eastern_from_utc / eastern_datetime_from_utc). The hardcoded "
        "`timedelta(hours=-4)` offset must be replaced by a zoneinfo "
        "America/New_York conversion that can be tested without a live fetch."
    )


def _et_result_offset(result, source_utc: _dt.datetime) -> _dt.timedelta:
    """Derive the effective UTC offset the helper applied.

    Accepts a tz-aware ET datetime (offset read directly) or a naive ET
    datetime (offset derived against the source UTC instant). Anything else
    fails RED.
    """
    if isinstance(result, _dt.datetime):
        if result.tzinfo is not None:
            off = result.utcoffset()
            assert off is not None, "tz-aware result must carry a UTC offset."
            return off
        # Naive ET wall clock — derive offset vs the source UTC instant.
        naive_utc = source_utc.replace(tzinfo=None)
        return result - naive_utc
    pytest.fail(
        f"UTC->ET helper must return a datetime, got "
        f"{type(result).__name__} ({result!r})."
    )


# ---------------------------------------------------------------------------
# AC-2 behavioural tests
# ---------------------------------------------------------------------------


def test_winter_date_resolves_to_est_utc_minus_5() -> None:
    """A standard-time (winter) UTC instant must resolve to EST = UTC-5.

    The hardcoded -4 offset gets this WRONG by one hour for the ~5 months ET
    is in standard time. The expected offset is taken from zoneinfo, the
    independent reference.
    """
    fn = _resolve_et_helper()
    result = fn(_WINTER_UTC)
    applied = _et_result_offset(result, _WINTER_UTC)
    assert applied == _EXPECTED_WINTER_OFFSET, (
        f"winter (EST) UTC instant {_WINTER_UTC.isoformat()} resolved with "
        f"offset {applied}, but US Eastern standard time is "
        f"{_EXPECTED_WINTER_OFFSET} (UTC-5). A hardcoded UTC-4 offset is "
        f"wrong by one hour for every standard-time date."
    )
    # Pin the sign/magnitude explicitly: EST is exactly five hours behind UTC.
    assert applied == _dt.timedelta(hours=-5), (
        f"US Eastern standard time must be UTC-5; helper applied {applied}."
    )


def test_summer_date_resolves_to_edt_utc_minus_4() -> None:
    """A daylight-time (summer) UTC instant must resolve to EDT = UTC-4.

    This is the offset the old code happened to hardcode — so the helper must
    STILL be correct here. A fix that, say, hardcoded -5 instead would break
    summer; the DST-aware zone must get both seasons right.
    """
    fn = _resolve_et_helper()
    result = fn(_SUMMER_UTC)
    applied = _et_result_offset(result, _SUMMER_UTC)
    assert applied == _EXPECTED_SUMMER_OFFSET, (
        f"summer (EDT) UTC instant {_SUMMER_UTC.isoformat()} resolved with "
        f"offset {applied}, but US Eastern daylight time is "
        f"{_EXPECTED_SUMMER_OFFSET} (UTC-4)."
    )
    assert applied == _dt.timedelta(hours=-4), (
        f"US Eastern daylight time must be UTC-4; helper applied {applied}."
    )


def test_winter_and_summer_offsets_actually_differ() -> None:
    """The helper must apply DIFFERENT offsets in the two seasons.

    The whole defect is a SINGLE fixed offset used year-round. If the winter
    and summer conversions resolve to the same offset, the helper is not
    DST-aware regardless of which constant it uses.
    """
    fn = _resolve_et_helper()
    winter_off = _et_result_offset(fn(_WINTER_UTC), _WINTER_UTC)
    summer_off = _et_result_offset(fn(_SUMMER_UTC), _SUMMER_UTC)
    assert winter_off != summer_off, (
        f"the UTC->ET helper applied the SAME offset ({winter_off}) in both "
        f"January and July — it is not DST-aware. EST (UTC-5) and EDT "
        f"(UTC-4) must differ by exactly one hour."
    )
    assert summer_off - winter_off == _dt.timedelta(hours=1), (
        f"EDT must be exactly one hour ahead of EST; got summer={summer_off}, "
        f"winter={winter_off}."
    )


def test_end_date_cap_rolls_correctly_around_utc_midnight_in_winter() -> None:
    """The day-rollover bug, directly.

    Audit finding 11: during EST, in the UTC 00:00-01:00 window, the hardcoded
    -4 offset rolls ``today_us`` to the WRONG calendar day. Take a UTC instant
    at 00:30 on 2026-01-20. The true ET wall clock is 19:30 on 2026-01-19
    (UTC-5) — still the 19th. The buggy -4 gives 20:30 on 2026-01-19 — also
    the 19th here, but the bug bites in the 00:00-01:00 UTC band where -4 vs
    -5 straddle midnight. Use 2026-01-15 00:30 UTC: EST -> 2026-01-14 19:30
    (the 14th); the buggy -4 -> 2026-01-14 20:30 (also the 14th). The genuine
    divergence: 2026-01-15 04:30 UTC -> EST 2026-01-14 23:30 (still 14th) vs
    -4 2026-01-15 00:30 (the 15th). We assert the helper resolves the ET
    CALENDAR DATE to match the zoneinfo reference at that instant.
    """
    fn = _resolve_et_helper()
    # 04:30 UTC: under EST this is still the previous ET day; under a buggy
    # UTC-4 it has already rolled to the next day.
    boundary_utc = _dt.datetime(2026, 1, 15, 4, 30, tzinfo=_UTC)
    expected_et_date = boundary_utc.astimezone(_ET).date()

    result = fn(boundary_utc)
    assert isinstance(result, _dt.datetime), (
        f"UTC->ET helper must return a datetime, got {type(result).__name__}."
    )
    assert result.date() == expected_et_date, (
        f"at {boundary_utc.isoformat()} the helper resolved the ET calendar "
        f"date to {result.date()}, but the correct EST date is "
        f"{expected_et_date}. A hardcoded UTC-4 offset rolls the day early "
        f"during standard time — shifting the fetch end-date cap by a full "
        f"trading day."
    )


# ---------------------------------------------------------------------------
# AC-2 static guard — the hardcoded offset must be gone, zoneinfo must be in.
# ---------------------------------------------------------------------------


def test_source_no_longer_hardcodes_a_utc_offset_via_timedelta_hours() -> None:
    """Static AST guard: synthetic_history.py must not derive a US-time
    reference by adding a hardcoded ``timedelta(hours=-4)`` (or -5) to a UTC
    datetime.

    A behavioural test confirms the helper is correct; this static guard
    ensures the OLD code path is actually deleted — a stray
    ``timedelta(hours=-4)`` left behind would be a latent regression even if
    the new helper exists.
    """
    tree = ast.parse(_SYNTH_PATH.read_text(encoding="utf-8"), filename=str(_SYNTH_PATH))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_timedelta = (
            isinstance(func, ast.Name) and func.id == "timedelta"
        ) or (
            isinstance(func, ast.Attribute) and func.attr == "timedelta"
        )
        if not is_timedelta:
            continue
        for kw in node.keywords:
            if kw.arg != "hours":
                continue
            val = kw.value
            # `hours=-4` parses as a UnaryOp(USub, Constant(4)).
            if (
                isinstance(val, ast.UnaryOp)
                and isinstance(val.op, ast.USub)
                and isinstance(val.operand, ast.Constant)
                and val.operand.value in (4, 5)
            ):
                offenders.append(node.lineno)
            elif isinstance(val, ast.Constant) and val.value in (-4, -5, 4, 5):
                offenders.append(node.lineno)

    assert not offenders, (
        "synthetic_history.py still derives a US-Eastern reference with a "
        f"hardcoded `timedelta(hours=+/-4|5)` at line(s) {offenders}. The "
        f"fixed offset is wrong for ~5 months/year — replace it with a "
        f"zoneinfo America/New_York conversion."
    )


def test_source_uses_zoneinfo_america_new_york() -> None:
    """Static AST guard: synthetic_history.py must reference the
    ``America/New_York`` zoneinfo zone for its US-time conversion.

    This is the positive companion to the negative guard above: not only must
    the hardcoded offset be gone, the DST-aware zone must be present —
    matching how market_calendar.py already handles ET.
    """
    src = _SYNTH_PATH.read_text(encoding="utf-8")
    assert "America/New_York" in src, (
        "synthetic_history.py does not reference the 'America/New_York' "
        "zoneinfo zone. The DST-aware conversion must use ZoneInfo("
        "'America/New_York') — the same zone market_calendar.py uses."
    )

    tree = ast.parse(src, filename=str(_SYNTH_PATH))
    has_zoneinfo_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "zoneinfo":
            if any(alias.name == "ZoneInfo" for alias in node.names):
                has_zoneinfo_import = True
        if isinstance(node, ast.Import):
            if any(alias.name == "zoneinfo" for alias in node.names):
                has_zoneinfo_import = True
    assert has_zoneinfo_import, (
        "synthetic_history.py must import ZoneInfo from zoneinfo for the "
        "DST-aware US Eastern conversion."
    )
