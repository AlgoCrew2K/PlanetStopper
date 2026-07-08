"""
Shared fixtures for tests/sleeves/rules/ (Managed Sleeves P2 — the rule engine).

RED-phase note: every fixture here is pure test support (data loading, factory
helpers) — it imports NO sleeves.rules.* module at collection time, so this
file collects cleanly whether or not sleeves/rules/ exists yet. Individual
test modules gate their own imports behind pytest.importorskip, matching the
tests/sleeves/test_envelope_clamp.py P1 convention.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_FIXTURES_MATH_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "math"

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _load_math_fixture(filename: str) -> dict:
    path = _FIXTURES_MATH_DIR / filename
    assert path.exists(), f"golden fixture missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def indicator_fixture() -> dict:
    """Raw close-price series for sleeves.rules.senses indicator math (AC-4)."""
    return _load_math_fixture("sleeves_rule_indicator_senses_basic.json")


@pytest.fixture(scope="module")
def class_derivation_fixture() -> dict:
    """Action-set -> rule-class specification table (AC-20)."""
    return _load_math_fixture("sleeves_rule_class_derivation_basic.json")


@pytest.fixture(scope="module")
def pacing_trace_fixture() -> dict:
    """Deterministic tick-by-tick pacing state machine trace (AC-5)."""
    return _load_math_fixture("sleeves_rule_limits_pacing_basic.json")


def et_datetime(
    year: int, month: int, day: int, hour: int, minute: int, second: int = 0
) -> datetime:
    """Build a tz-aware America/New_York datetime — DST-safe by construction
    (zoneinfo resolves the correct UTC offset for the given wall-clock date,
    never a hardcoded fixed offset)."""
    return datetime(year, month, day, hour, minute, second, tzinfo=ET)


def backdate_fire(fire_id: int, fired_at_utc: datetime) -> None:
    """Rewrite a sleeve_rule_fires row's fired_at to a caller-chosen UTC instant.

    insert_sleeve_rule_fire has no fired_at override (the column always
    defaults to sqlite's own datetime('now')) -- this test-only helper backdates
    a row directly so pacing tests can drive a deterministic simulated
    timeline (cooldown-elapsed / day-rollover scenarios) without needing a
    production signature change. Uses the exact "YYYY-MM-DD HH:MM:SS" format
    sqlite's own datetime('now') produces, so limits.py never has to parse a
    format it wouldn't see in production.
    """
    import database as _db

    conn = _db.get_connection()
    try:
        conn.execute(
            "UPDATE sleeve_rule_fires SET fired_at = ? WHERE id = ?",
            (fired_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"), fire_id),
        )
        conn.commit()
    finally:
        conn.close()


def make_rule_doc(
    *,
    name: str = "test_rule",
    sleeve_id: int = 1,
    when: dict | None = None,
    condition: dict | None = None,
    then: list[dict] | None = None,
    limits: dict | None = None,
    mode: str = "SHADOW",
) -> dict:
    """Build a minimal, schema-VALID rule doc, with every field overridable.

    Defaults to a single-symbol SMA entry rule so tests only need to override
    the field(s) relevant to what they're probing (schema/class/condition
    invalidity tests pass a deliberately broken field).
    """
    return {
        "name": name,
        "sleeve_id": sleeve_id,
        "when": when if when is not None else {"symbol": "SPY"},
        "if": condition
        if condition is not None
        else {
            "op": "compare",
            "sense": "sma_20",
            "comparator": ">",
            "value": 100.0,
        },
        "then": then
        if then is not None
        else [{"type": "buy", "sizing": {"mode": "risk_pct", "risk_pct": 0.01}}],
        "limits": limits
        if limits is not None
        else {
            "cooldown_sec": 3600,
            "max_fires_per_day": 2,
            "rearm_ticks": 3,
            "market_hours_only": True,
        },
        "mode": mode,
    }


def make_stored_rule(*, id: int, mode: str = "SHADOW", **doc_overrides) -> dict:
    """Build the shape sleeves.rules.runner.evaluate_rules expects per rule:
    the DB row's `id`/`mode` merged with the (already schema-valid) parsed
    json_doc fields. `id` is the authoritative sleeve_rules.id (used for
    limits.py pacing state + sleeve_rule_fires.rule_id); `mode` reflects the
    CURRENT arming state from the sleeve_rules.mode DB column (the runtime
    source of truth — distinct from whatever default the json_doc's own
    "mode" field carried at authoring time, which callers are responsible for
    keeping in sync via the arm/disarm route, out of runner.py's concern)."""
    doc = make_rule_doc(mode=mode, **doc_overrides)
    return {"id": id, "mode": mode, **doc}
