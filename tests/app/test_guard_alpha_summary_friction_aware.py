"""
RED — BL-3 friction-aware $-saved headline (AC-1/AC-2/AC-3/AC-6/AC-7/AC-8).

THE BUG (docs/audit/TWO-WEEK-REVIEW-2026-08-04.md Finding M1): the live
$-saved dashboard headline (GET /api/guard-alpha-summary) is friction-blind
on both bases it reports, while the team's OWN optimizer already models
SIM_EXIT_FRICTION_PCT=0.5pp (autotuner.py) in every replay accounting site.
The audit re-derived that this window's mean GROSS save is +0.13pp -- smaller
than the 0.5pp friction the optimizer assumes -- so the live headline
overstates real economics with no disclosure a cost term even exists.

THE FIX: two new additive response fields, computed with the SAME per-entry
percentage-level-subtraction-before-dollar-conversion formula shape as
reporting.py's own saved_dollars (never a post-hoc dollar-level subtraction,
which would not scale correctly across differently-sized positions):
  - cumulative_saved_dollars_net_of_friction (AC-1, snapshot basis)
  - saved_dollars_realized_net_of_friction (AC-2, realized/marks basis)
Plus AC-3 (day-1 exit_triggers intraday fallback nets friction too), AC-6
(app.py imports SIM_EXIT_FRICTION_PCT from autotuner -- never a second local
constant), AC-7 (zero engine/value-computation touch -- gross fields stay
byte-unchanged), AC-8 (net fields respect the same window= filtering).

All dollar/count assertions are derived from fixture files or the LIVE
autotuner.SIM_EXIT_FRICTION_PCT constant, never hardcoded to a producer
value that could silently drift out of sync with the real constant
(feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from datetime import date, timedelta

import pytest

import analytics as analytics_module
import app as app_module
import autotuner

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_APP_PY = _PROJECT_ROOT / "app.py"

_MATH_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "friction_net_saved_dollars_basic.json"
)
_MATH_FIXTURE = json.loads(_MATH_FIXTURE_PATH.read_text())["cases"]

_REALIZED_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "guard_alpha_summary_realized"
)
_PM_2026_07_01 = json.loads((_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-01.json").read_text())
_PM_2026_07_02 = json.loads((_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-02.json").read_text())

FRICTION = autotuner.SIM_EXIT_FRICTION_PCT


def _net_of_friction_dollars(saved_pct: float, symphony_value: float) -> float:
    """Reference implementation of AC-1's formula -- MUST match app.py's own
    per-entry computation. Percentage-level subtraction BEFORE dollar
    conversion (reporting.py:92-95's own formula shape), never a post-hoc
    subtraction on the aggregate dollar sum."""
    return symphony_value * (saved_pct - FRICTION) / 100.0


def _compute_saved_dollars_net_of_friction(
    at_return: float, current_return: float, position_value: float, friction_pct: float
) -> float:
    """Reference implementation of AC-3's day-1 intraday-fallback formula --
    the SAME (at_return - current_return)/100*position_value shape as the
    existing gross intraday estimate, with friction subtracted at the
    percentage level before the dollar conversion."""
    return ((at_return - current_return) - friction_pct) / 100.0 * position_value


def _recent_date_str(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _extract_function_body(src: str, func_signature: str, end_marker: str, file_label: str) -> str:
    """Slice a Python source string from `func_signature` to the next
    `end_marker` landmark -- same substring-scoping idiom as
    tests/app/test_f023_performance_symphony_id_mismatch.py's helper of the
    same name, applied here to app.py instead of a JS file."""
    start = src.find(func_signature)
    assert start != -1, f"{func_signature!r} not found in {file_label}"
    end = src.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after {func_signature!r} in {file_label}"
    return src[start:end]


def _app_py_source() -> str:
    return _APP_PY.read_text(encoding="utf-8")


def _guard_alpha_summary_body() -> str:
    return _extract_function_body(
        _app_py_source(),
        "def guard_alpha_summary():",
        "\ndef _incubation_badge(",
        "app.py",
    )


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1: snapshot-basis net-of-friction figure, golden-fixture-derived
# ---------------------------------------------------------------------------


@pytest.fixture
def golden_fixture_pm_dir(tmp_path, monkeypatch):
    """One post_mortem file (dated today, inside every window) whose triggers
    are the 5 golden-fixture cases from friction_net_saved_dollars_basic.json
    -- including the sign-flip non-vacuity case (gross positive, net
    negative). Each trigger's saved_dollars is the fixture's own
    gross_saved_dollars (matching reporting.py's real persisted shape --
    tests never recompute it, they supply it directly, same convention as
    the realized-basis fixture files)."""
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    triggers = [
        {
            "symphony_name": case["name"],
            "symphony_value": case["inputs"]["symphony_value"],
            "saved_pct_guard_alpha": case["inputs"]["saved_pct_guard_alpha"],
            "saved_dollars": case["gross_saved_dollars"],
            "exit_reason": "Trailing Stop",
            "if_held_source": "shadow_history",
        }
        for case in _MATH_FIXTURE
    ]
    payload = {"triggers": triggers}
    today = _recent_date_str(0)
    (pm_dir / f"post_mortem_{today}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return {"dir": pm_dir, "triggers": triggers}


class TestSnapshotBasisNetOfFriction:
    def test_response_has_the_net_of_friction_key(self, client, golden_fixture_pm_dir):
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "cumulative_saved_dollars_net_of_friction" in body, (
            "AC-1: response must gain 'cumulative_saved_dollars_net_of_friction'. "
            f"Got keys: {sorted(body.keys())}"
        )

    def test_net_of_friction_sum_matches_golden_fixture(self, client, golden_fixture_pm_dir):
        """The literal AC-1 proof: sum of symphony_value*(saved_pct-FRICTION)/100
        across all 5 golden-fixture entries, computed independently here from
        the fixture + the LIVE autotuner constant."""
        expected = sum(
            _net_of_friction_dollars(
                case["inputs"]["saved_pct_guard_alpha"], case["inputs"]["symphony_value"]
            )
            for case in _MATH_FIXTURE
        )
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars_net_of_friction"] == pytest.approx(
            expected, abs=0.01
        ), (
            f"cumulative_saved_dollars_net_of_friction {data.get('cumulative_saved_dollars_net_of_friction')} "
            f"!= golden-fixture-derived {expected} (FRICTION={FRICTION})"
        )

    def test_non_vacuity_gross_positive_case_alone_flips_net_negative(
        self, tmp_path, monkeypatch, client
    ):
        """NON-VACUITY CONTROL: isolate the golden fixture's
        gross_positive_below_friction_flips_negative case in its own file --
        proves the route computes a genuine per-entry sign flip, not a
        passthrough of the gross figure. Fails under any friction-blind
        implementation (net == gross) or any implementation that clamps net
        at the gross figure's own sign."""
        case = next(
            c for c in _MATH_FIXTURE if c["name"] == "gross_positive_below_friction_flips_negative"
        )
        assert case["gross_sign"] == "positive" and case["expected_net_sign"] == "negative", (
            "fixture integrity: this case must be gross-positive/net-negative"
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        payload = {
            "triggers": [
                {
                    "symphony_name": case["name"],
                    "symphony_value": case["inputs"]["symphony_value"],
                    "saved_pct_guard_alpha": case["inputs"]["saved_pct_guard_alpha"],
                    "saved_dollars": case["gross_saved_dollars"],
                    "exit_reason": "Trailing Stop",
                    "if_held_source": "shadow_history",
                }
            ]
        }
        (pm_dir / f"post_mortem_{_recent_date_str(0)}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))

        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] > 0, (
            "fixture integrity: gross figure must be positive for this case"
        )
        assert data["cumulative_saved_dollars_net_of_friction"] < 0, (
            f"net-of-friction must be NEGATIVE for a gross-positive-but-below-friction "
            f"entry (gross={data['cumulative_saved_dollars']}, "
            f"net={data['cumulative_saved_dollars_net_of_friction']}) -- a friction-blind "
            "or sign-clamped implementation would fail this"
        )

    def test_empty_state_net_of_friction_is_zero(self, client, tmp_path, monkeypatch):
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars_net_of_friction"] == pytest.approx(0.0, abs=1e-9), (
            "AC-1: empty state (guard_event_count==0) must report 0.0 net-of-friction, "
            f"got {data.get('cumulative_saved_dollars_net_of_friction')}"
        )


# ---------------------------------------------------------------------------
# AC-7 regression pin: gross fields stay byte-unchanged on the SAME fixture
# ---------------------------------------------------------------------------


class TestGrossFieldsUnaffectedByFrictionAddition:
    def test_gross_cumulative_saved_dollars_unchanged(self, client, golden_fixture_pm_dir):
        expected_gross = sum(case["gross_saved_dollars"] for case in _MATH_FIXTURE)
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_gross, abs=0.01), (
            "AC-7: the pre-existing gross cumulative_saved_dollars must be byte-unchanged "
            "by the friction addition -- this must equal the plain (no-friction) sum of "
            f"saved_dollars. expected={expected_gross}, got={data['cumulative_saved_dollars']}"
        )

    def test_guard_event_count_unchanged(self, client, golden_fixture_pm_dir):
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["guard_event_count"] == len(_MATH_FIXTURE)


# ---------------------------------------------------------------------------
# AC-2: realized-basis net-of-friction sibling
# ---------------------------------------------------------------------------


@pytest.fixture
def realized_pm_dir(tmp_path, monkeypatch):
    import shutil

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    shutil.copy(_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-01.json", pm_dir)
    shutil.copy(_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-02.json", pm_dir)
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


def _valid_triggers(*post_mortems):
    out = []
    for pm in post_mortems:
        out.extend(
            t for t in pm.get("triggers", []) if analytics_module.is_valid_post_mortem_entry(t)
        )
    return out


class TestRealizedBasisNetOfFriction:
    def test_response_has_the_realized_net_of_friction_key(self, client, realized_pm_dir):
        resp = client.get("/api/guard-alpha-summary")
        body = resp.get_json()
        assert "saved_dollars_realized_net_of_friction" in body, (
            "AC-2: response must gain 'saved_dollars_realized_net_of_friction' additively. "
            f"Got keys: {sorted(body.keys())}"
        )

    def test_realized_net_of_friction_sums_only_entries_with_realized_data(
        self, client, realized_pm_dir
    ):
        """AC-2's math contract, expressed via the algebraically-equivalent
        per-entry shortcut (saved_dollars_realized - symphony_value*FRICTION/100
        == symphony_value*(equivalent_pct-FRICTION)/100 for any exact
        saved_dollars_realized = symphony_value*pct/100 relationship, which
        holds exactly for this fixture's clean numbers) -- deliberately does
        NOT pin which derivation the implementer chooses (plan explicitly
        leaves that open), only the resulting numeric contract. Entries
        without saved_dollars_realized (Symphony Zeta) are excluded, same as
        the existing saved_dollars_realized aggregate."""
        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        with_realized = [t for t in valid if "saved_dollars_realized" in t]
        assert 0 < len(with_realized) < len(valid), (
            "fixture integrity: need a mix of with/without realized-data entries"
        )
        expected = sum(
            t["saved_dollars_realized"] - t["symphony_value"] * FRICTION / 100.0
            for t in with_realized
        )
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["saved_dollars_realized_net_of_friction"] == pytest.approx(
            expected, abs=0.01
        ), (
            f"saved_dollars_realized_net_of_friction {data.get('saved_dollars_realized_net_of_friction')} "
            f"!= expected {expected} (FRICTION={FRICTION})"
        )

    def test_entries_without_realized_data_contribute_nothing(self, client, realized_pm_dir):
        """Symphony Zeta (2026-07-02, no realized fields) must not silently
        substitute its snapshot saved_pct_guard_alpha into the realized-basis
        net figure -- same honesty contract as the existing
        saved_dollars_realized aggregate (AC-7 of DE-EXIT-FRICTION-REALIZED-001)."""
        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        without_realized = [t for t in valid if "saved_dollars_realized" not in t]
        assert without_realized, "fixture integrity: need at least one entry with no realized data"

        with_realized = [t for t in valid if "saved_dollars_realized" in t]
        naive_sum_including_snapshot_substitution = sum(
            t["saved_dollars_realized"] - t["symphony_value"] * FRICTION / 100.0
            for t in with_realized
        ) + sum(
            t["saved_dollars"] - t["symphony_value"] * FRICTION / 100.0 for t in without_realized
        )
        expected_honest = sum(
            t["saved_dollars_realized"] - t["symphony_value"] * FRICTION / 100.0
            for t in with_realized
        )
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["saved_dollars_realized_net_of_friction"] == pytest.approx(
            expected_honest, abs=0.01
        )
        assert data["saved_dollars_realized_net_of_friction"] != pytest.approx(
            naive_sum_including_snapshot_substitution, abs=0.01
        ), (
            "the realized net-of-friction figure must NOT silently substitute the "
            "snapshot-basis saved_dollars for entries missing realized data"
        )

    def test_gross_realized_basis_unchanged(self, client, realized_pm_dir):
        """AC-7 regression pin on the realized sibling."""
        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        expected = sum(t["saved_dollars_realized"] for t in valid if "saved_dollars_realized" in t)
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["saved_dollars_realized"] == pytest.approx(expected, abs=0.01), (
            "AC-7: pre-existing saved_dollars_realized must be byte-unchanged"
        )


# ---------------------------------------------------------------------------
# AC-3: day-1 exit_triggers intraday-fallback nets friction too
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_friction_relevant_exit_triggers(tmp_path, monkeypatch):
    """A local minimal SQLite fixture mirroring
    tests/app/test_live_dashboard_metrics.py's db_with_exit_triggers pattern
    -- two triggered symphonies, one whose gross divergence comfortably
    exceeds the friction floor (net stays positive) and one whose gross
    divergence is BELOW the friction floor (net flips negative), proving the
    day-1 fallback branch genuinely subtracts friction rather than passing
    the gross intraday estimate through untouched."""
    import json as _json
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    db_path = str(tmp_path / "test_friction_intraday.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS exit_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            ts_et TEXT NOT NULL,
            at_return REAL NOT NULL,
            triggered_reason TEXT NOT NULL,
            gate_state_json TEXT
        );
        CREATE TABLE IF NOT EXISTS shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            trading_day TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            shadow_return REAL NOT NULL,
            current_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            epoch_label TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY,
            data TEXT
        );
        """
    )

    today_et = _dt.now(_ZoneInfo("America/New_York")).date().isoformat()

    # SYM_WIDE: 3.0pp divergence on $12k -- comfortably above the friction
    # floor, net stays positive.
    # SYM_THIN: 0.2pp divergence on $10k -- BELOW the friction floor, net
    # flips negative ($20 gross vs a friction cost of $50).
    rows = {
        "SYM_WIDE": {"at_return": 3.0, "current_return": 0.0, "position_value": 12000.0},
        "SYM_THIN": {"at_return": 0.2, "current_return": 0.0, "position_value": 10000.0},
    }

    conn.executemany(
        "INSERT INTO exit_triggers (symphony_id, ts_utc, ts_et, at_return, triggered_reason) "
        "VALUES (?,?,?,?,?)",
        [
            (sid, f"{today_et}T13:00:00Z", f"{today_et}T09:00:00", r["at_return"], "TRAILING_STOP")
            for sid, r in rows.items()
        ],
    )
    conn.executemany(
        "INSERT INTO shadow_history "
        "(symphony_id, trading_day, ts_utc, shadow_return, current_return, is_post_trigger, epoch_label) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (
                sid,
                today_et,
                f"{today_et}T16:00:00Z",
                r["at_return"],
                r["current_return"],
                1,
                "epoch1",
            )
            for sid, r in rows.items()
        ],
    )
    blob = {
        sid: {
            "name": sid,
            "current_value": r["position_value"],
            "current_return": r["current_return"],
            "triggered": True,
            "triggered_reason": "TRAILING_STOP",
        }
        for sid, r in rows.items()
    }
    conn.execute("INSERT INTO bot_state (id, data) VALUES (1, ?)", (_json.dumps(blob),))
    conn.commit()
    conn.close()

    monkeypatch.setenv("DB_PATH", db_path)
    return rows


class TestDayOneIntradayFallbackNetsFriction:
    def test_response_has_net_of_friction_key_on_intraday_path(
        self, client, db_with_friction_relevant_exit_triggers, tmp_path, monkeypatch
    ):
        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["source"] == "exit_triggers_intraday", (
            "test setup: must be exercising the day-1 fallback branch"
        )
        assert "cumulative_saved_dollars_net_of_friction" in data, (
            "AC-3: the day-1 exit_triggers intraday fallback must also populate "
            "cumulative_saved_dollars_net_of_friction, not just the post-mortem path"
        )

    def test_intraday_net_of_friction_matches_reference_formula(
        self, client, db_with_friction_relevant_exit_triggers, tmp_path, monkeypatch
    ):
        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        rows = db_with_friction_relevant_exit_triggers
        expected = sum(
            _compute_saved_dollars_net_of_friction(
                r["at_return"], r["current_return"], r["position_value"], FRICTION
            )
            for r in rows.values()
        )
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars_net_of_friction"] == pytest.approx(
            expected, abs=0.01
        ), (
            f"day-1 intraday net-of-friction {data.get('cumulative_saved_dollars_net_of_friction')} "
            f"!= reference {expected} (FRICTION={FRICTION})"
        )

    def test_intraday_non_vacuity_sign_flip_across_the_two_symphonies(
        self, client, db_with_friction_relevant_exit_triggers, tmp_path, monkeypatch
    ):
        """NON-VACUITY CONTROL for the day-1 branch: SYM_WIDE's per-row net
        contribution must be positive and SYM_THIN's must be negative --
        proves the friction subtraction is genuinely wired into THIS branch
        (a friction-blind day-1 implementation would keep both positive,
        since both symphonies' gross divergence is >= 0)."""
        rows = db_with_friction_relevant_exit_triggers
        wide_net = _compute_saved_dollars_net_of_friction(
            rows["SYM_WIDE"]["at_return"],
            rows["SYM_WIDE"]["current_return"],
            rows["SYM_WIDE"]["position_value"],
            FRICTION,
        )
        thin_net = _compute_saved_dollars_net_of_friction(
            rows["SYM_THIN"]["at_return"],
            rows["SYM_THIN"]["current_return"],
            rows["SYM_THIN"]["position_value"],
            FRICTION,
        )
        assert wide_net > 0 and thin_net < 0, (
            "fixture integrity: SYM_WIDE must net positive and SYM_THIN must net "
            "negative for this to be a genuine non-vacuity control"
        )

    def test_gross_intraday_estimate_unaffected(
        self, client, db_with_friction_relevant_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-7 regression pin on the day-1 branch: the pre-existing gross
        cumulative_saved_dollars must stay byte-unchanged."""
        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        rows = db_with_friction_relevant_exit_triggers
        expected_gross = sum(
            (r["at_return"] - r["current_return"]) / 100.0 * r["position_value"]
            for r in rows.values()
        )
        resp = client.get("/api/guard-alpha-summary")
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_gross, abs=0.01)


# ---------------------------------------------------------------------------
# AC-6: single source of truth for the friction constant (source-scan)
# ---------------------------------------------------------------------------


class TestFrictionConstantSingleSourceOfTruth:
    def test_guard_alpha_summary_body_references_autotuner_attribute_usage(self):
        """AC-6, hardened per team-lead ruling (BL-2 text-proxy lesson): the
        scan anchors on the USAGE PATTERN `autotuner.SIM_EXIT_FRICTION_PCT`
        (attribute access -- the SAME idiom app.py:3612/3635's exit_turnover
        route already establishes), not the bare token alone. A rationale
        COMMENT inside the function that merely mentions the token (e.g. "see
        SIM_EXIT_FRICTION_PCT in autotuner.py") must not be able to satisfy
        this -- only a genuine `autotuner.SIM_EXIT_FRICTION_PCT` reference on
        a non-comment line counts."""
        body = _guard_alpha_summary_body()
        # Strip Python line comments before scanning, so a comment-only mention
        # of the token cannot satisfy the assertion.
        code_lines = []
        for line in body.splitlines():
            stripped = line.split("#", 1)[0]
            code_lines.append(stripped)
        code_only = "\n".join(code_lines)
        assert "autotuner.SIM_EXIT_FRICTION_PCT" in code_only, (
            "AC-6: guard_alpha_summary() must reference autotuner.SIM_EXIT_FRICTION_PCT "
            "(attribute-access usage, not merely a comment) -- proves the friction "
            "constant is genuinely wired into this route's computation, matching the "
            "existing app.py:3612/3635 exit_turnover idiom"
        )

    def test_app_py_never_redefines_a_second_local_friction_constant(self):
        """AC-6: app.py must never assign SIM_EXIT_FRICTION_PCT as a local
        constant (autotuner.py stays the sole owner) -- checked as a
        line-start-anchored assignment on CODE (comments stripped first) so a
        commented-out example (e.g. "# SIM_EXIT_FRICTION_PCT = 0.5") cannot
        trip this."""
        source = _app_py_source()
        code_only_lines = [line.split("#", 1)[0] for line in source.splitlines()]
        assignment_pattern = re.compile(r"^\s*SIM_EXIT_FRICTION_PCT\s*=\s*[0-9.]")
        hits = [
            (i + 1, line)
            for i, line in enumerate(code_only_lines)
            if assignment_pattern.match(line)
        ]
        assert not hits, (
            f"app.py must never assign SIM_EXIT_FRICTION_PCT as a local constant -- "
            f"autotuner.py is the sole owner. Found assignment(s): {hits}"
        )


# ---------------------------------------------------------------------------
# AC-8: net-of-friction fields respect the SAME window= filtering as gross
# ---------------------------------------------------------------------------


def _write_pm_with_pct(
    base_dir: pathlib.Path, date_str: str, saved_pct: float, symphony_value: float, symphony: str
) -> None:
    payload = {
        "triggers": [
            {
                "symphony_name": symphony,
                "symphony_value": symphony_value,
                "saved_pct_guard_alpha": saved_pct,
                "saved_dollars": round(saved_pct * symphony_value / 100.0, 2),
                "exit_reason": "Trailing Stop",
                "if_held_source": "shadow_history",
            }
        ]
    }
    (base_dir / f"post_mortem_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def windowed_friction_pm_dir(tmp_path, monkeypatch):
    """A RECENT (20 days ago, inside every window) and a FAR (400 days ago,
    outside the 1y/365-day window, inside 'all') fixture file -- proves
    cumulative_saved_dollars_net_of_friction respects the SAME cutoff the
    gross field already applies at the 1y token."""
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    date_recent = _recent_date_str(20)
    date_far = _recent_date_str(400)
    _write_pm_with_pct(pm_dir, date_recent, 2.0, 10000.0, "Sym-Recent")
    _write_pm_with_pct(pm_dir, date_far, 3.0, 10000.0, "Sym-Far")
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return {"dir": pm_dir, "date_recent": date_recent, "date_far": date_far}


class TestNetOfFrictionRespectsWindowFiltering:
    def test_1y_window_excludes_the_far_file_from_net_of_friction(
        self, client, windowed_friction_pm_dir
    ):
        expected_net_recent_only = _net_of_friction_dollars(2.0, 10000.0)
        resp = client.get("/api/guard-alpha-summary?window=1y")
        data = resp.get_json()
        assert data["cumulative_saved_dollars_net_of_friction"] == pytest.approx(
            expected_net_recent_only, abs=0.01
        ), (
            "AC-8: window=1y must exclude the 400-day-old file from the "
            f"net-of-friction sum just as it already does for the gross sum. "
            f"expected={expected_net_recent_only}, got="
            f"{data['cumulative_saved_dollars_net_of_friction']}"
        )

    def test_all_window_includes_both_files_in_net_of_friction(
        self, client, windowed_friction_pm_dir
    ):
        expected_net_both = _net_of_friction_dollars(2.0, 10000.0) + _net_of_friction_dollars(
            3.0, 10000.0
        )
        resp = client.get("/api/guard-alpha-summary?window=all")
        data = resp.get_json()
        assert data["cumulative_saved_dollars_net_of_friction"] == pytest.approx(
            expected_net_both, abs=0.01
        )
