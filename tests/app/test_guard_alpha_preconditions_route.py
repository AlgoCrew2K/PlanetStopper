"""
RED tests -- GET /api/guard-alpha-preconditions (AC-6, AC-7 schema half, AC-8).

The route does not exist as a real implementation yet (a stub is present so
the endpoint resolves -- see app.py guard_alpha_preconditions()); every test
below is expected to fail RED until the implementer wires the real pipeline.

DATA-SOURCING SEAM (open question flagged to PM/ga-flask, see
.claude/tdd-handoff.md): these tests monkeypatch database.load_state (for
symphony enumeration), autotuner.build_if_held_replay_series (the primary/
replay sample) and analytics.get_shadow_current_return_daily_series (the
secondary/shadow sample) -- NOT guard_preconditions.compute_persistence_stats
or classify_stop_justification, which run for REAL so the response's
rho/sharpe/verdict values are checked against the same independent reference
formulas used by the math-layer tests (tests/guard_preconditions/), giving
true end-to-end coverage of the route's wiring, not just "did it call a mock".

Contract under test:
- AC-6: per-symphony {rho, rho_ci, sharpe_daily, n_obs, verdict, sample_source}
  for the "replay" and "shadow" samples, where available.
- AC-7 (schema half): route returns valid JSON matching this shape; the
  template-rendering half of AC-7 is covered in tests/app/test_guard_alpha_
  preconditions_panel_ui.py.
- AC-8: when replay and shadow verdicts disagree, BOTH are present with their
  own n_obs -- never silently prefer one.
- Edge cases: empty DB -> honest empty state, not 500; malformed/missing data
  for one symphony does not 500 the whole route; symphony absent from shadow
  -> replay-only row with honest sample_source.
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

import analytics as analytics_module
import app as app_module
import autotuner as autotuner_module
import database as database_module
import guard_preconditions as gp

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math"
_ROUTE = "/api/guard-alpha-preconditions"


def _load_returns(fixture_name: str) -> list:
    return json.loads((_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))[
        "daily_returns_pct"
    ]


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-6 / AC-8: schema + real math pipeline, both samples present and disagree
# ---------------------------------------------------------------------------


class TestResponseSchemaAndDisagreement:
    def test_both_samples_present_with_schema_and_can_disagree(self, client, monkeypatch):
        sym_id = "sym-preconditions-001"
        replay_returns = _load_returns(
            "persistence_stats_ar1_positive.json"
        )  # -> SUFFICIENT_LIKELY
        shadow_returns = _load_returns("persistence_stats_negative_edge.json")  # -> NEGATIVE_EDGE

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "Test Symphony", "current_return": 1.0}},
        )
        monkeypatch.setattr(
            autotuner_module,
            "build_if_held_replay_series",
            lambda sid: replay_returns if sid == sym_id else None,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: shadow_returns if sid == sym_id else None,
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data!r}"
        data = resp.get_json()
        assert data is not None and "symphonies" in data, (
            f"Response must have a top-level 'symphonies' key, got keys={list((data or {}).keys())}"
        )
        assert sym_id in data["symphonies"], f"Expected symphony {sym_id!r} in response"
        sym_entry = data["symphonies"][sym_id]

        for sample_key in ("replay", "shadow"):
            assert sample_key in sym_entry, f"Expected {sample_key!r} sample in {sym_entry!r}"
            row = sym_entry[sample_key]
            for field in ("rho", "rho_ci", "sharpe_daily", "n_obs", "verdict", "sample_source"):
                assert field in row, f"{sample_key} row missing field {field!r}: {row!r}"

        # AC-8: verdicts must disagree here (constructed on purpose) and BOTH
        # rows must be present with their OWN n_obs -- never silently prefer one.
        assert sym_entry["replay"]["verdict"] != sym_entry["shadow"]["verdict"], (
            f"Fixtures were chosen so replay and shadow verdicts DIFFER "
            f"(replay={sym_entry['replay']['verdict']!r}, "
            f"shadow={sym_entry['shadow']['verdict']!r}) -- AC-8 requires both "
            "to be surfaced, not collapsed into one."
        )
        assert sym_entry["replay"]["n_obs"] == len(replay_returns)
        assert sym_entry["shadow"]["n_obs"] == len(shadow_returns)

        # End-to-end real math check (not just "a number came back"): the
        # route's replay rho must match the SAME independent reference the
        # math-layer tests use, proving the route actually ran the real
        # compute_persistence_stats pipeline rather than fabricating a value.
        expected_stats = gp.compute_persistence_stats(replay_returns)
        assert sym_entry["replay"]["rho"] == pytest.approx(expected_stats.rho, rel=1e-6)
        assert sym_entry["replay"]["verdict"] == gp.classify_stop_justification(expected_stats)

    def test_replay_only_symphony_has_honest_sample_source(self, client, monkeypatch):
        """Edge case: symphony present in replay but absent from shadow_history
        (never triggered / new) -> replay row real, shadow row DEGRADED
        (never fabricated, never bare None).

        UNIFORM DEGRADED-ROW CONTRACT: every sample ("replay" and "shadow")
        is ALWAYS an object with all 6 fields, never bare null -- when
        unavailable, verdict="INSUFFICIENT_DATA" and n_obs=0 (the SAME
        classify_stop_justification vocabulary a genuinely-thin real sample
        would produce), so the panel never has to null-check a sample before
        reading its fields. This mirrors the pattern surfaced during
        implementation for the replay-cache-miss case (see
        TestColdCacheNeverFetchesEndToEnd below) -- applied symmetrically to
        BOTH samples for consistency."""
        sym_id = "sym-replay-only-002"
        replay_returns = _load_returns("persistence_stats_iid_control.json")

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "New Symphony", "current_return": 0.0}},
        )
        monkeypatch.setattr(
            autotuner_module,
            "build_if_held_replay_series",
            lambda sid: replay_returns,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: None,  # never in shadow_history
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        sym_entry = resp.get_json()["symphonies"][sym_id]
        assert "replay" in sym_entry and sym_entry["replay"] is not None
        shadow_row = sym_entry.get("shadow")
        assert shadow_row is not None, (
            "A symphony absent from shadow_history must still get a real "
            "'shadow' object (uniform degraded-row contract), never bare "
            f"None -- got {shadow_row!r}."
        )
        assert shadow_row.get("verdict") == "INSUFFICIENT_DATA", (
            f"Degraded shadow row must report verdict='INSUFFICIENT_DATA' "
            f"(honest, uses the same 5-class vocabulary), got {shadow_row!r}."
        )
        assert shadow_row.get("n_obs") == 0


# ---------------------------------------------------------------------------
# PM ruling (2026-07-23): replay data sourcing is CACHE-HIT-ONLY end-to-end
# through the REAL route -- a cold cache must never trigger a live fetch, no
# matter how deep in the call stack. autotuner.build_if_held_replay_series is
# NOT mocked in this test (unlike every other route test) -- the REAL
# function runs (currently a stub, so this legitimately fails with
# NotImplementedError until ga-impl lands it) so this test proves the
# guarantee holds through the whole real call chain, not just at a mocked
# boundary.
# ---------------------------------------------------------------------------


class TestColdCacheNeverFetchesEndToEnd:
    def test_cold_cache_route_degrades_honestly_and_never_fetches(
        self, client, monkeypatch, tmp_path
    ):
        import synthetic_history as synthetic_history_module

        monkeypatch.chdir(tmp_path)  # guarantees an empty ./cache dir

        sym_id = "sym-cold-cache-004"
        shadow_returns = _load_returns("persistence_stats_iid_control.json")

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "Cold Cache Symphony", "current_return": 0.0}},
        )
        # autotuner_module.build_if_held_replay_series is deliberately NOT
        # mocked here -- the real function must run and prove it degrades
        # honestly on a cache miss without reaching the network.
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: shadow_returns,
        )

        fetch_calls: list = []

        def _tracking_fetch_bars(*args, **kwargs):
            fetch_calls.append((args, kwargs))
            return []

        monkeypatch.setattr(synthetic_history_module, "fetch_bars", _tracking_fetch_bars)

        resp = client.get(_ROUTE)

        assert fetch_calls == [], (
            f"synthetic_history.fetch_bars was called {len(fetch_calls)} time(s) "
            "via the real route path on a cold cache -- the route must NEVER be "
            "able to drive a live fetch (PM ruling 2026-07-23)."
        )
        assert resp.status_code == 200, (
            f"Cold cache must still yield 200 with an honest degraded replay "
            f"entry, not a 500. Got {resp.status_code}: {resp.data!r}"
        )
        sym_entry = resp.get_json()["symphonies"][sym_id]
        # UNIFORM DEGRADED-ROW CONTRACT (see TestResponseSchemaAndDisagreement
        # ::test_replay_only_symphony_has_honest_sample_source for the
        # symmetric shadow-side case): "replay" is ALWAYS an object, never
        # bare null -- on a cold cache it reports verdict="INSUFFICIENT_DATA"
        # and n_obs=0, the same vocabulary a genuinely-thin real sample uses.
        replay_row = sym_entry.get("replay")
        assert replay_row is not None, (
            "On a cold cache, the replay sample must still be a real object "
            "(uniform degraded-row contract), never bare None -- got "
            f"{replay_row!r}."
        )
        assert replay_row.get("verdict") == "INSUFFICIENT_DATA", (
            f"Degraded replay row must report verdict='INSUFFICIENT_DATA', got {replay_row!r}."
        )
        assert replay_row.get("n_obs") == 0
        assert sym_entry.get("shadow") is not None, (
            "The shadow sample IS available in this scenario and must still be "
            "shown -- a cold replay cache must not suppress the whole symphony "
            "row (AC-8: fall back to shadow-only, never silently prefer one by "
            "omitting the other)."
        )


# ---------------------------------------------------------------------------
# Edge cases: empty DB, malformed/missing per-symphony data
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_no_symphonies_returns_200_honest_empty_state(self, client, monkeypatch):
        monkeypatch.setattr(database_module, "load_state", lambda: {})

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("symphonies") == {}, (
            f"No symphonies must yield an honest empty {{}} 'symphonies' dict "
            f"(a 200 that renders blank without signaling emptiness is exactly "
            f"what AC-7's panel-level empty state must avoid), got {data!r}."
        )

    def test_one_symphony_erroring_does_not_500_the_whole_route(self, client, monkeypatch):
        """AC-6: malformed/missing-data-safe (per-symphony honest degradation,
        never a 500)."""
        good_sym = "sym-good-003"
        bad_sym = "sym-bad-003"
        good_returns = _load_returns("persistence_stats_ar1_positive.json")

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {
                good_sym: {"name": "Good", "current_return": 1.0},
                bad_sym: {"name": "Bad", "current_return": 1.0},
            },
        )

        def _replay(sid):
            if sid == bad_sym:
                raise RuntimeError("simulated replay-source failure")
            return good_returns

        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", _replay)
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: None,
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200, (
            f"One symphony's data source raising must NOT 500 the whole route, "
            f"got {resp.status_code}: {resp.data!r}"
        )
        data = resp.get_json()
        assert good_sym in data["symphonies"], "The good symphony must still be present"
        # The bad symphony either degrades honestly (present with a null/error
        # marker) or is omitted -- either is acceptable, a 500 is not.


# ---------------------------------------------------------------------------
# Strict-JSON safety: compute_persistence_stats legitimately produces
# math.nan for a zero-variance (flat) series (guard_preconditions.py's
# documented zero-variance branch). Python's json module serializes
# float('nan') as the RAW, UNQUOTED literal `NaN` -- INVALID per strict JSON
# (RFC 8259). A real browser's response.json() / JSON.parse REJECTS a bare
# NaN token and throws, which invalidates the WHOLE response document, not
# just the offending field -- one flat-return symphony could silently break
# the panel for every symphony in the response. Python-side round-trip tests
# (resp.get_json(), Python's json.loads) do NOT catch this: Python's json
# module is lenient and accepts NaN by default, same as it serializes it --
# so this failure mode is invisible unless the raw bytes are inspected under
# strict parsing rules.
# ---------------------------------------------------------------------------


class TestStrictJsonSafety:
    def test_flat_zero_variance_series_never_leaks_nan_into_response(self, client, monkeypatch):
        """A symphony with a genuinely flat (all-identical-value) but
        AVAILABLE (non-empty, above N_MIN_OBS) replay series is a real,
        plausible production scenario (e.g. an illiquid or pegged holding
        with zero observed daily change over the replay window) -- distinct
        from the UNAVAILABLE case _degraded_row() already guards, since this
        series is truthy and routes through the REAL _sample_row ->
        compute_persistence_stats path."""
        sym_id = "sym-flat-zero-variance-005"
        flat_series = [1.0] * (gp.N_MIN_OBS + 5)  # non-empty, >= N_MIN_OBS, zero variance

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "Flat Symphony", "current_return": 0.0}},
        )
        monkeypatch.setattr(
            autotuner_module,
            "build_if_held_replay_series",
            lambda sid: flat_series,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: None,
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        raw = resp.data.decode("utf-8")
        assert "NaN" not in raw and "Infinity" not in raw, (
            "Route response contains a raw NaN/Infinity token -- invalid per "
            "strict JSON (RFC 8259). A real browser's response.json() throws "
            "on this, breaking the WHOLE response (not just this field) for "
            f"every symphony, not just the flat one. Raw response: {raw!r}"
        )

        def _reject_constant(token):
            raise ValueError(f"strict JSON parse rejects the token {token!r}")

        # The actual discriminator: Python's json.loads is lenient by default
        # (accepts NaN/Infinity same as it serializes them) so resp.get_json()
        # would NOT catch this -- parse_constant forces strict RFC 8259
        # behavior, mirroring what a real browser's JSON.parse enforces.
        json.loads(raw, parse_constant=_reject_constant)


# ---------------------------------------------------------------------------
# AC-8: auth gate
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-guard-preconditions"
_TEST_SECRET_KEY = "test-secret-guard-preconditions"


@pytest.fixture
def auth_client_no_session(monkeypatch):
    """Flask test client with the auth gate ENABLED but no active session
    (mirrors tests/app/test_guard_alpha_summary_route.py's convention)."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session):
        resp = auth_client_no_session.get(_ROUTE, headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 401, (
            f"Unauthenticated XHR to {_ROUTE} must return 401, got {resp.status_code}."
        )


# ---------------------------------------------------------------------------
# Security / scope: read-only, no settings-write, no LIVE_EXECUTION coupling
# ---------------------------------------------------------------------------


class TestReadOnlyScope:
    def test_route_is_get_only(self):
        rule = next(r for r in app_module.app.url_map.iter_rules() if r.rule == _ROUTE)
        assert "POST" not in rule.methods, (
            f"{_ROUTE} must be GET-only (read-only advisory surface), got methods={rule.methods!r}"
        )

    def test_route_source_never_references_live_execution(self):
        src = inspect.getsource(app_module.guard_alpha_preconditions)
        assert "LIVE_EXECUTION" not in src, (
            "guard_alpha_preconditions() must never reference LIVE_EXECUTION "
            "(read-only advisory surface, no engine interaction, AC-6)."
        )

    def test_route_source_contains_no_sql_write_statements(self):
        src = inspect.getsource(app_module.guard_alpha_preconditions).upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE"):
            assert forbidden not in src, (
                f"guard_alpha_preconditions() source contains {forbidden!r} -- "
                "this route must be read-only SQLite (AC-6, Security Considerations)."
            )


# ---------------------------------------------------------------------------
# Hygiene cycle (fix/route-phantom-keys-log-noise, 2026-07-25): structural
# symphony-entry discriminator + phantom-key exclusion.
#
# THE DEFECT: guard_alpha_preconditions() iterates `for sym_id in
# bot_state_dict:` over EVERY top-level bot_state key, including 5 known
# non-symphony metadata keys (date, last_execution_mode,
# last_market_close_snapshot, last_successful_cycle_at, post_mortem_run) --
# each renders as a phantom INSUFFICIENT_DATA "symphony" row (16 rows served,
# 11 real, on the droplet). See feature-plans/preconditions-phantom-keys-
# and-cache-log-noise.md.
#
# THE FIX: a structural (shape-of-the-value) discriminator --
# `_is_symphony_state_entry(value) -> bool` = isinstance(value, dict) and
# "name" in value -- the SAME convention already used at 7 other app.py call
# sites (including _compute_portfolio_strip, app.py:1504) to build the real
# symphony list from bot_state. Deliberately NOT a name-based denylist of
# the 5 known keys -- AC-2 requires a future non-symphony metadata key to be
# excluded WITHOUT a code change; a name denylist is an automatic review
# reject.
# ---------------------------------------------------------------------------

# Realistic non-symphony bot_state metadata values -- mirrors the real value
# TYPES the engine writes (alpha_bot_execution.py): date/last_successful_
# cycle_at/post_mortem_run are plain date/timestamp strings;
# last_execution_mode is the bool LIVE_EXECUTION (alpha_bot_execution.py:70,
# :725); last_market_close_snapshot is dict-valued but its keys are
# trading_day/captured_at_et/data_as_of/portfolio_strip/shadow_divergence/
# accounts_map -- never "name" (alpha_bot_execution.py:1135-1145).
_REALISTIC_METADATA_KEYS = {
    "date": "2026-07-24",
    "last_execution_mode": True,
    "last_successful_cycle_at": "2026-07-24T15:59:00-04:00",
    "post_mortem_run": "2026-07-24",
    "last_market_close_snapshot": {
        "trading_day": "2026-07-24",
        "captured_at_et": "16:00:00 ET",
        "data_as_of": "16:00 ET",
        "portfolio_strip": {},
        "shadow_divergence": {"by_symphony": {}, "portfolio": None},
        "accounts_map": {},
    },
}


class TestSymphonyEntryDiscriminatorHelper:
    """Pure unit tests for app._is_symphony_state_entry -- no I/O, no Flask
    client. The route-level filtering tests below exercise the same
    discriminator wired into the real route."""

    def test_real_symphony_shaped_dict_qualifies(self):
        assert (
            app_module._is_symphony_state_entry(
                {"name": "Growth Symphony", "current_return": 1.5, "triggered": False}
            )
            is True
        )

    def test_degenerate_dict_with_only_name_still_qualifies(self):
        # A thin/degenerate real symphony (AC-3, no-self-regression) -- as
        # long as "name" survives, the entry is still symphony-shaped.
        assert app_module._is_symphony_state_entry({"name": ""}) is True

    def test_metadata_dict_without_name_key_is_excluded(self):
        # Mirrors last_market_close_snapshot's real shape exactly.
        assert (
            app_module._is_symphony_state_entry(
                _REALISTIC_METADATA_KEYS["last_market_close_snapshot"]
            )
            is False
        )

    @pytest.mark.parametrize(
        "value",
        [
            "2026-07-24",  # date / post_mortem_run shape
            True,  # last_execution_mode shape (LIVE_EXECUTION is a real bool)
            "2026-07-24T15:59:00-04:00",  # last_successful_cycle_at shape
        ],
    )
    def test_non_dict_metadata_values_are_excluded(self, value):
        assert app_module._is_symphony_state_entry(value) is False

    @pytest.mark.parametrize("value", [None, [], [{"name": "x"}], 42, 3.14])
    def test_other_non_dict_shapes_are_excluded(self, value):
        assert app_module._is_symphony_state_entry(value) is False


class TestPhantomKeyExclusion:
    """Route-level pin for the hygiene-cycle phantom-keys fix (AC-1..AC-4).

    bot_state top-level keys mixed: real symphony entries + the 5 known
    non-symphony metadata keys (realistic value shapes, see
    _REALISTIC_METADATA_KEYS above) -- proves the route's response never
    surfaces a metadata key as a symphony row.
    """

    def test_phantom_metadata_keys_never_appear_as_symphony_rows(self, client, monkeypatch):
        sym_a = "sym-real-a"
        sym_b = "sym-real-b"
        bot_state = {
            sym_a: {"name": "Real Symphony A", "current_return": 1.0},
            sym_b: {"name": "Real Symphony B", "current_return": -0.5},
            **_REALISTIC_METADATA_KEYS,
        }
        monkeypatch.setattr(database_module, "load_state", lambda: bot_state)
        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", lambda sid: None)
        monkeypatch.setattr(
            analytics_module, "get_shadow_current_return_daily_series", lambda sid, db_file: None
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        symphonies = data["symphonies"]
        assert set(symphonies.keys()) == {sym_a, sym_b}, (
            f"Expected exactly the 2 real symphony keys, got "
            f"{sorted(symphonies.keys())!r} -- a phantom metadata key leaked "
            f"into the response as a fake symphony row."
        )
        for metadata_key in _REALISTIC_METADATA_KEYS:
            assert metadata_key not in symphonies, (
                f"Metadata key {metadata_key!r} must never appear as a "
                f"symphony row (AC-1), got it present with value "
                f"{symphonies.get(metadata_key)!r}."
            )

    def test_metadata_only_bot_state_yields_honest_empty_state(self, client, monkeypatch):
        # AC-4: no real symphony entries anywhere, only known metadata keys.
        monkeypatch.setattr(database_module, "load_state", lambda: dict(_REALISTIC_METADATA_KEYS))

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("symphonies") == {}, (
            f"A metadata-only bot_state must yield the route's honest empty "
            f"state (zero symphony rows, HTTP 200), got {data!r}."
        )

    def test_degenerate_real_symphony_still_appears_alongside_metadata(self, client, monkeypatch):
        # AC-3 no-self-regression: a real-but-thin symphony (keeps "name",
        # nothing else) must still render its existing honest degraded
        # verdict -- the filter may only exclude non-symphony entries, never
        # degrade real ones.
        thin_sym = "sym-thin-real"
        bot_state = {
            thin_sym: {"name": "Thin Symphony"},
            **_REALISTIC_METADATA_KEYS,
        }
        monkeypatch.setattr(database_module, "load_state", lambda: bot_state)
        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", lambda sid: None)
        monkeypatch.setattr(
            analytics_module, "get_shadow_current_return_daily_series", lambda sid, db_file: None
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert thin_sym in data["symphonies"], (
            f"A real-but-thin symphony (only 'name' set) must still appear "
            f"in the response -- the phantom-key filter must never degrade "
            f"a real (if sparse) entry, got "
            f"keys={list(data['symphonies'].keys())!r}."
        )
        entry = data["symphonies"][thin_sym]
        assert entry["replay"]["verdict"] == "INSUFFICIENT_DATA"
        assert entry["shadow"]["verdict"] == "INSUFFICIENT_DATA"
        # Joint proof (non-vacuity): "thin_sym present" alone is true even on
        # UNFILTERED code (nothing ever removed it). The real no-self-
        # regression claim is the CONJUNCTION -- the thin real symphony
        # survives WHILE the co-present metadata keys are excluded -- which
        # only a correct filter satisfies simultaneously.
        for metadata_key in _REALISTIC_METADATA_KEYS:
            assert metadata_key not in data["symphonies"], (
                f"Metadata key {metadata_key!r} must be excluded even when a "
                f"genuinely thin real symphony is present in the same "
                f"bot_state, got it present with value "
                f"{data['symphonies'].get(metadata_key)!r}."
            )

    def test_novel_metadata_shaped_key_excluded_without_a_denylist_entry(
        self, client, monkeypatch
    ):
        # AC-2 anti-rot proof: a key NEVER referenced anywhere in app.py's
        # source, dict-valued but WITHOUT "name" -- a hardcoded denylist of
        # the 5 known names would leave this present (masquerading as a
        # phantom symphony); only a genuinely structural rule excludes it.
        novel_key = "future_engine_telemetry_marker_zz9"
        assert novel_key not in inspect.getsource(app_module), (
            f"Test invariant violated: {novel_key!r} must not already be "
            f"referenced anywhere in app.py -- otherwise this is not a "
            f"genuine 'novel' key and the anti-rot proof is meaningless."
        )
        real_sym = "sym-real-c"
        bot_state = {
            real_sym: {"name": "Real Symphony C"},
            novel_key: {"some_future_field": "some_value"},  # dict, no "name"
        }
        monkeypatch.setattr(database_module, "load_state", lambda: bot_state)
        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", lambda sid: None)
        monkeypatch.setattr(
            analytics_module, "get_shadow_current_return_daily_series", lambda sid, db_file: None
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert novel_key not in data["symphonies"], (
            f"Novel metadata-shaped key {novel_key!r} (never referenced in "
            f"app.py source) leaked into the response as a symphony row -- "
            f"this indicates a name-based denylist implementation rather "
            f"than AC-2's required structural discriminator."
        )
        assert real_sym in data["symphonies"]

    def test_non_dict_stray_value_is_excluded_and_never_500s(self, client, monkeypatch):
        # Plan edge case: "A symphony value that is structurally degenerate
        # (e.g. not a dict) -> excluded by the structural rule; this is
        # correct -- it was never renderable as a symphony."
        real_sym = "sym-real-d"
        bot_state = {
            real_sym: {"name": "Real Symphony D"},
            "some_stray_scalar_key": 12345,
            "some_stray_list_key": [1, 2, 3],
        }
        monkeypatch.setattr(database_module, "load_state", lambda: bot_state)
        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", lambda sid: None)
        monkeypatch.setattr(
            analytics_module, "get_shadow_current_return_daily_series", lambda sid, db_file: None
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data["symphonies"].keys()) == {real_sym}
