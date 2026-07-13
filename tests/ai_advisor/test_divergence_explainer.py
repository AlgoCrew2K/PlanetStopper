"""
Sprint 3 Stream B — RED: Divergence Explainer producer for ``advisors/divergence_explainer.py``.

Tier 1 — fixture-based and structural unit tests. No network. No live DB. Runs every CI pass.

The module under test (``advisors/divergence_explainer.py``) does NOT exist yet —
every test here is RED until the implementer cycle creates it.

Binding contract
----------------
  def compute_divergence_explainer_observation(
      autotune_run: dict,
      cvar_row: dict | None,
      *,
      second_window_enabled: bool,
  ) -> dict:
      # Returns an AdvisorObservation dict with keys:
      #   advisor_role      str   'DIVERGENCE_EXPLAINER'
      #   subject_type      str   'autotune_run'
      #   subject_id        str   str(autotune_run['id'])
      #   verdict           str   'NOT_APPLICABLE' or 'INFORMATIONAL'
      #   raw_response      dict  two honest CVaR values — NEVER a signed difference
      #   is_advisory_only  int   1 (always; the Advisor never moves money)
      #   spec_bundle_id    str | None

  def run_divergence_explainer(
      autotune_run: dict,
      cvar_row: dict | None,
      *,
      second_window_enabled: bool | None = None,
  ) -> int | None:
      # Integration entry point. Reads SECOND_WINDOW_CVAR_ENABLED env var
      # when second_window_enabled is None. If §B is off, writes one
      # NOT_APPLICABLE row and returns its row id. If §B is on, writes one
      # INFORMATIONAL row and returns its row id.

CRITICAL CONSTRAINT (CVaR-divergence REJECT, user mandate — binding)
----------------------------------------------------------------------
The observation MUST contain "two honest CVaR values, each with own S-3 contract."
The observation MUST NOT contain a signed divergence quantity or any field whose
value is the arithmetic difference (or ratio) of the two CVaR windows.

Forbidden raw_response keys (any of these in raw_response is a test FAIL):
  "divergence", "signed_divergence", "cvar_diff", "cvar_delta",
  "window_divergence", "divergence_pct", "delta"

Phase-1 reality: §B is NOT YET ENABLED in production. The producer is a
stub-active: it checks the feature flag first, and if §B is off, it writes
a single NOT_APPLICABLE row for the audit trail.

§B off behaviour:
  verdict           = 'NOT_APPLICABLE'
  raw_response      = {'feature_flag': 'off'}
  is_advisory_only  = 1

§B on behaviour:
  verdict           = 'INFORMATIONAL'
  raw_response contains short_window_cvar_pct, short_window_tail_obs,
                         long_window_cvar_pct, long_window_tail_obs
  (each may be None when the respective window is unavailable)
  raw_response MUST NOT contain any signed divergence field

Wall integrity:
  All DB reads flow through database.advisor_ro_query — never get_connection /
  get_ro_connection directly (same rule as Overfitting Conscience and Spec Critic).

Mocking strategy
----------------
* database.insert_advisor_observation → patched at call-site tests.
* database.advisor_ro_query          → patched at call-site tests.
* os.environ / monkeypatch           → used for feature-flag tests.
* math engine, autotuner NOT touched — this module does no risk math.
* No module-level mutable state; every fixture is function-scoped.

Fixture provenance
------------------
tests/fixtures/math/divergence_explainer_scenarios.json
  Hand-derived from the Sprint 3 Stream B dispatch brief. No producer-computed
  rates/dollars/percents. CVaR values are fictional scenario inputs.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1] / "fixtures" / "math" / "divergence_explainer_scenarios.json"
)

# Keys whose presence in raw_response means a signed divergence leaked through.
# These are the forbidden keys per the CVaR-divergence REJECT user mandate.
_FORBIDDEN_DIVERGENCE_KEYS = frozenset(
    {
        "divergence",
        "signed_divergence",
        "cvar_diff",
        "cvar_delta",
        "window_divergence",
        "divergence_pct",
        "delta",
    }
)


def _load_fixture() -> dict:
    """Return the parsed divergence_explainer_scenarios fixture."""
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _import_divex():
    """Lazy import of the module under test.

    Deferred so collection-time ImportError surfaces as a test failure on the
    specific test that needs the module, not a conftest-level crash.
    """
    return importlib.import_module("advisors.divergence_explainer")


def _assert_no_forbidden_divergence_keys(raw_response: dict, context: str) -> None:
    """Assert that raw_response contains no signed-divergence field.

    A single call used by multiple tests — keeps the assertion message consistent.
    """
    leaked = _FORBIDDEN_DIVERGENCE_KEYS & set(raw_response.keys())
    assert not leaked, (
        f"{context}: raw_response MUST NOT contain a signed divergence field. "
        f"Found forbidden key(s): {sorted(leaked)!r}. "
        "The observation carries two honest CVaR values; their signed difference "
        "is permanently rejected (CVaR-divergence REJECT user mandate)."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def divex_fixture() -> dict:
    """Parsed fixture file (function-scoped, immutable)."""
    return _load_fixture()


@pytest.fixture
def flag_off_scenario(divex_fixture) -> dict:
    """§B disabled: NOT_APPLICABLE scenario."""
    return divex_fixture["scenarios"]["flag_off_noop"]


@pytest.fixture
def flag_on_both_windows(divex_fixture) -> dict:
    """§B enabled, both windows produce valid estimates."""
    return divex_fixture["scenarios"]["flag_on_sufficient_history"]


@pytest.fixture
def flag_on_long_null(divex_fixture) -> dict:
    """§B enabled, long-window CVaR is NULL."""
    return divex_fixture["scenarios"]["flag_on_long_window_null"]


@pytest.fixture
def flag_on_no_rows(divex_fixture) -> dict:
    """§B enabled, no CVaR rows for the symphony."""
    return divex_fixture["scenarios"]["flag_on_no_cvar_rows"]


# ===========================================================================
# Test group 1 — Pure function contract: return dict structure and required keys.
# ===========================================================================


def test_pure_function_returns_a_dict_flag_off(flag_off_scenario):
    """compute_divergence_explainer_observation returns a dict when §B is off."""
    mod = _import_divex()
    run = flag_off_scenario["autotune_run"]
    result = mod.compute_divergence_explainer_observation(run, None, second_window_enabled=False)
    assert isinstance(result, dict), (
        "compute_divergence_explainer_observation must return a dict when §B is off"
    )


def test_pure_function_returns_a_dict_flag_on(flag_on_both_windows):
    """compute_divergence_explainer_observation returns a dict when §B is on."""
    mod = _import_divex()
    run = flag_on_both_windows["autotune_run"]
    cvar_row = flag_on_both_windows["cvar_rows"][0]
    result = mod.compute_divergence_explainer_observation(run, cvar_row, second_window_enabled=True)
    assert isinstance(result, dict), (
        "compute_divergence_explainer_observation must return a dict when §B is on"
    )


def test_return_dict_has_all_required_keys_flag_off(flag_off_scenario):
    """Result dict must carry every required AdvisorObservation key when §B is off."""
    mod = _import_divex()
    run = flag_off_scenario["autotune_run"]
    result = mod.compute_divergence_explainer_observation(run, None, second_window_enabled=False)
    required = {
        "advisor_role",
        "subject_type",
        "subject_id",
        "verdict",
        "raw_response",
        "is_advisory_only",
    }
    missing = required - set(result.keys())
    assert not missing, f"result is missing required AdvisorObservation keys: {missing}"


def test_return_dict_has_all_required_keys_flag_on(flag_on_both_windows):
    """Result dict must carry every required AdvisorObservation key when §B is on."""
    mod = _import_divex()
    run = flag_on_both_windows["autotune_run"]
    cvar_row = flag_on_both_windows["cvar_rows"][0]
    result = mod.compute_divergence_explainer_observation(run, cvar_row, second_window_enabled=True)
    required = {
        "advisor_role",
        "subject_type",
        "subject_id",
        "verdict",
        "raw_response",
        "is_advisory_only",
    }
    missing = required - set(result.keys())
    assert not missing, f"result is missing required AdvisorObservation keys: {missing}"


def test_advisor_role_is_divergence_explainer(flag_off_scenario, flag_on_both_windows):
    """advisor_role must be exactly 'DIVERGENCE_EXPLAINER' on both flag paths."""
    mod = _import_divex()
    for scenario, label, enabled, cvar in [
        (flag_off_scenario, "flag_off", False, None),
        (flag_on_both_windows, "flag_on", True, flag_on_both_windows["cvar_rows"][0]),
    ]:
        result = mod.compute_divergence_explainer_observation(
            scenario["autotune_run"], cvar, second_window_enabled=enabled
        )
        assert result["advisor_role"] == "DIVERGENCE_EXPLAINER", (
            f"{label}: advisor_role must be 'DIVERGENCE_EXPLAINER', got {result['advisor_role']!r}"
        )


def test_subject_type_is_autotune_run(flag_off_scenario, flag_on_both_windows):
    """subject_type must be 'autotune_run' on both flag paths."""
    mod = _import_divex()
    for scenario, label, enabled, cvar in [
        (flag_off_scenario, "flag_off", False, None),
        (flag_on_both_windows, "flag_on", True, flag_on_both_windows["cvar_rows"][0]),
    ]:
        result = mod.compute_divergence_explainer_observation(
            scenario["autotune_run"], cvar, second_window_enabled=enabled
        )
        assert result["subject_type"] == "autotune_run", (
            f"{label}: subject_type must be 'autotune_run', got {result['subject_type']!r}"
        )


def test_subject_id_matches_run_id(flag_off_scenario, flag_on_both_windows):
    """subject_id must be str(autotune_run['id']) on both flag paths."""
    mod = _import_divex()
    for scenario, label, enabled, cvar in [
        (flag_off_scenario, "flag_off", False, None),
        (flag_on_both_windows, "flag_on", True, flag_on_both_windows["cvar_rows"][0]),
    ]:
        run = scenario["autotune_run"]
        result = mod.compute_divergence_explainer_observation(
            run, cvar, second_window_enabled=enabled
        )
        assert result["subject_id"] == str(run["id"]), (
            f"{label}: subject_id must be str(run['id'])={str(run['id'])!r}, "
            f"got {result['subject_id']!r}"
        )


def test_raw_response_is_a_dict_on_both_paths(
    flag_off_scenario, flag_on_both_windows, flag_on_long_null, flag_on_no_rows
):
    """raw_response must be a dict (not a string, not None) on every path."""
    mod = _import_divex()
    cases = [
        (flag_off_scenario["autotune_run"], None, False, "flag_off"),
        (
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
            True,
            "both_windows",
        ),
        (flag_on_long_null["autotune_run"], flag_on_long_null["cvar_rows"][0], True, "long_null"),
        (flag_on_no_rows["autotune_run"], None, True, "no_rows"),
    ]
    for run, cvar, enabled, label in cases:
        result = mod.compute_divergence_explainer_observation(
            run, cvar, second_window_enabled=enabled
        )
        assert isinstance(result["raw_response"], dict), (
            f"{label}: raw_response must be a dict, got {type(result['raw_response']).__name__!r}"
        )


# ===========================================================================
# Test group 2 — §B feature-flag off: NOT_APPLICABLE behaviour.
# ===========================================================================


def test_flag_off_verdict_is_not_applicable(flag_off_scenario):
    """When §B is disabled, verdict must be 'NOT_APPLICABLE'."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_off_scenario["autotune_run"], None, second_window_enabled=False
    )
    assert result["verdict"] == "NOT_APPLICABLE", (
        f"§B disabled must produce 'NOT_APPLICABLE'; got {result['verdict']!r}"
    )


def test_flag_off_raw_response_indicates_flag_is_off(flag_off_scenario):
    """When §B is disabled, raw_response must carry {'feature_flag': 'off'}."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_off_scenario["autotune_run"], None, second_window_enabled=False
    )
    raw = result["raw_response"]
    assert raw.get("feature_flag") == "off", (
        f"§B disabled: raw_response['feature_flag'] must be 'off'; got raw_response={raw!r}"
    )


def test_flag_off_raw_response_contains_no_divergence_keys(flag_off_scenario):
    """§B disabled: raw_response must never contain a signed divergence field."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_off_scenario["autotune_run"], None, second_window_enabled=False
    )
    _assert_no_forbidden_divergence_keys(result["raw_response"], "flag_off")


def test_flag_off_is_advisory_only_is_1(flag_off_scenario):
    """is_advisory_only must be 1 even when the verdict is NOT_APPLICABLE."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_off_scenario["autotune_run"], None, second_window_enabled=False
    )
    assert result["is_advisory_only"] == 1, (
        "is_advisory_only must be 1 on NOT_APPLICABLE path — the Advisor never moves money; "
        f"got {result['is_advisory_only']!r}"
    )


# ===========================================================================
# Test group 3 — §B feature-flag on: INFORMATIONAL behaviour.
# ===========================================================================


def test_flag_on_verdict_is_informational(flag_on_both_windows):
    """When §B is enabled and CVaR rows exist, verdict must be 'INFORMATIONAL'."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        flag_on_both_windows["cvar_rows"][0],
        second_window_enabled=True,
    )
    assert result["verdict"] == "INFORMATIONAL", (
        f"§B enabled must produce 'INFORMATIONAL'; got {result['verdict']!r}"
    )


def test_flag_on_raw_response_contains_short_window_fields(flag_on_both_windows):
    """§B on: raw_response must include short-window CVaR fields."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        flag_on_both_windows["cvar_rows"][0],
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    assert "short_window_cvar_pct" in raw, (
        f"§B on: raw_response must contain 'short_window_cvar_pct'; got keys: {sorted(raw.keys())}"
    )
    assert "short_window_tail_obs" in raw, (
        f"§B on: raw_response must contain 'short_window_tail_obs'; got keys: {sorted(raw.keys())}"
    )


def test_flag_on_raw_response_contains_long_window_fields(flag_on_both_windows):
    """§B on: raw_response must include long-window CVaR fields."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        flag_on_both_windows["cvar_rows"][0],
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    assert "long_window_cvar_pct" in raw, (
        f"§B on: raw_response must contain 'long_window_cvar_pct'; got keys: {sorted(raw.keys())}"
    )
    assert "long_window_tail_obs" in raw, (
        f"§B on: raw_response must contain 'long_window_tail_obs'; got keys: {sorted(raw.keys())}"
    )


def test_flag_on_short_window_value_comes_from_cvar_row(flag_on_both_windows):
    """§B on: short_window_cvar_pct must match the cvar_5pct field in the CVaR row.

    The value is not derived from any producer math — it is passed through from
    the cvar_diagnostics row. Asserting shape (not None) rather than a literal.
    """
    mod = _import_divex()
    cvar_row = flag_on_both_windows["cvar_rows"][0]
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        cvar_row,
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    # Value must be a float (not None) when the source row has a non-null cvar_5pct.
    assert cvar_row["cvar_5pct"] is not None, "fixture sanity: cvar_5pct must be non-null"
    assert isinstance(raw["short_window_cvar_pct"], (int, float)), (
        "short_window_cvar_pct must be a number when the CVaR row has cvar_5pct; "
        f"got {raw['short_window_cvar_pct']!r}"
    )


def test_flag_on_long_window_value_comes_from_cvar_row(flag_on_both_windows):
    """§B on: long_window_cvar_pct must match the cvar_5pct_long field in the CVaR row."""
    mod = _import_divex()
    cvar_row = flag_on_both_windows["cvar_rows"][0]
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        cvar_row,
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    assert cvar_row["cvar_5pct_long"] is not None, (
        "fixture sanity: cvar_5pct_long must be non-null for this scenario"
    )
    assert isinstance(raw["long_window_cvar_pct"], (int, float)), (
        "long_window_cvar_pct must be a number when the CVaR row has cvar_5pct_long; "
        f"got {raw['long_window_cvar_pct']!r}"
    )


def test_flag_on_long_window_null_when_cvar_row_has_null_long(flag_on_long_null):
    """§B on: when cvar_5pct_long is NULL in the row, long_window_cvar_pct must be None."""
    mod = _import_divex()
    cvar_row = flag_on_long_null["cvar_rows"][0]
    assert cvar_row["cvar_5pct_long"] is None, (
        "fixture sanity: cvar_5pct_long must be null for this scenario"
    )
    result = mod.compute_divergence_explainer_observation(
        flag_on_long_null["autotune_run"],
        cvar_row,
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    assert raw.get("long_window_cvar_pct") is None, (
        "long_window_cvar_pct must be None when the CVaR row has null cvar_5pct_long; "
        f"got {raw.get('long_window_cvar_pct')!r}"
    )


def test_flag_on_no_rows_both_window_fields_are_none(flag_on_no_rows):
    """§B on, no CVaR rows: both window CVaR fields must be None — never raise."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_no_rows["autotune_run"],
        None,
        second_window_enabled=True,
    )
    raw = result["raw_response"]
    assert raw.get("short_window_cvar_pct") is None, (
        "short_window_cvar_pct must be None when cvar_row is None; "
        f"got {raw.get('short_window_cvar_pct')!r}"
    )
    assert raw.get("long_window_cvar_pct") is None, (
        "long_window_cvar_pct must be None when cvar_row is None; "
        f"got {raw.get('long_window_cvar_pct')!r}"
    )


# ===========================================================================
# Test group 4 — CVaR-divergence REJECT (binding user mandate).
#
# These tests are the most critical in the file. The signed-divergence
# rejection is not a soft style guideline — it is a permanent architectural
# decision from the decision-science council.
# ===========================================================================


def test_flag_on_both_windows_raw_response_has_no_divergence_key(flag_on_both_windows):
    """CVaR-divergence REJECT: raw_response must never contain a signed difference.

    Both windows are available in this scenario — the implementation might be
    tempted to compute (long - short) or (short - long). Any such key is a FAIL.
    """
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_both_windows["autotune_run"],
        flag_on_both_windows["cvar_rows"][0],
        second_window_enabled=True,
    )
    _assert_no_forbidden_divergence_keys(
        result["raw_response"],
        "flag_on_both_windows (both CVaR values present — highest risk of divergence leak)",
    )


def test_flag_on_long_null_raw_response_has_no_divergence_key(flag_on_long_null):
    """CVaR-divergence REJECT: raw_response has no signed difference when long is null."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_long_null["autotune_run"],
        flag_on_long_null["cvar_rows"][0],
        second_window_enabled=True,
    )
    _assert_no_forbidden_divergence_keys(result["raw_response"], "flag_on_long_null")


def test_flag_on_no_rows_raw_response_has_no_divergence_key(flag_on_no_rows):
    """CVaR-divergence REJECT: raw_response has no signed difference when no rows."""
    mod = _import_divex()
    result = mod.compute_divergence_explainer_observation(
        flag_on_no_rows["autotune_run"],
        None,
        second_window_enabled=True,
    )
    _assert_no_forbidden_divergence_keys(result["raw_response"], "flag_on_no_rows")


def test_divergence_reject_adversarial_alternative_key_names():
    """Adversarial: check that commonly-named divergence proxies are not present.

    An implementer might rename the forbidden field to evade the explicit
    denylist. This test checks additional proxy names that would still
    constitute a signed divergence signal.
    """
    mod = _import_divex()
    run = {
        "id": 99,
        "symphony_id": "sym-adversarial",
        "spec_bundle_id": "bundle-adversarial-099",
        "run_timestamp": "2026-05-27T20:00:00Z",
    }
    # Provide a row where both windows have values — maximum temptation for implementer.
    cvar_row = {
        "cycle_id": "cycle-adversarial",
        "symphony_id": "sym-adversarial",
        "cvar_5pct": -2.5,
        "cvar_5pct_stderr": 0.4,
        "cvar_n_tail": 6,
        "cvar_5pct_long": -2.0,
        "cvar_n_tail_long": 8,
        "ts_utc": "2026-05-27T20:01:00Z",
    }
    result = mod.compute_divergence_explainer_observation(run, cvar_row, second_window_enabled=True)
    raw = result["raw_response"]

    # Extended adversarial check — proxy names that still encode a difference.
    extra_forbidden = {
        "spread",
        "window_spread",
        "cvar_spread",
        "gap",
        "window_gap",
        "difference",
        "diff",
    }
    all_forbidden = _FORBIDDEN_DIVERGENCE_KEYS | extra_forbidden
    leaked = all_forbidden & set(raw.keys())
    assert not leaked, (
        "raw_response MUST NOT contain any signed divergence proxy field. "
        f"Found forbidden key(s): {sorted(leaked)!r}. "
        "The observation carries two honest CVaR values; their signed difference "
        "is permanently rejected (CVaR-divergence REJECT user mandate)."
    )


# ===========================================================================
# Test group 5 — is_advisory_only invariant across all paths.
# ===========================================================================


def test_is_advisory_only_is_always_1_on_every_verdict(
    flag_off_scenario, flag_on_both_windows, flag_on_long_null, flag_on_no_rows
):
    """is_advisory_only must be 1 on every verdict path — the Advisor never moves money.

    The architectural invariant is unconditional: no feature flag state,
    no CVaR availability, no verdict may ever set is_advisory_only to anything
    other than 1.
    """
    mod = _import_divex()
    cases = [
        (flag_off_scenario["autotune_run"], None, False, "flag_off/NOT_APPLICABLE"),
        (
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
            True,
            "flag_on/both_windows",
        ),
        (
            flag_on_long_null["autotune_run"],
            flag_on_long_null["cvar_rows"][0],
            True,
            "flag_on/long_null",
        ),
        (flag_on_no_rows["autotune_run"], None, True, "flag_on/no_rows"),
    ]
    for run, cvar, enabled, label in cases:
        result = mod.compute_divergence_explainer_observation(
            run, cvar, second_window_enabled=enabled
        )
        assert result["is_advisory_only"] == 1, (
            f"{label}: is_advisory_only must be 1 — the Advisor never moves money; "
            f"got {result['is_advisory_only']!r}"
        )


# ===========================================================================
# Test group 6 — verdict is constrained to allowed values.
# ===========================================================================


def test_verdict_is_in_allowed_enum(
    flag_off_scenario, flag_on_both_windows, flag_on_long_null, flag_on_no_rows
):
    """verdict must be one of {'NOT_APPLICABLE', 'INFORMATIONAL'} on every path."""
    mod = _import_divex()
    allowed = {"NOT_APPLICABLE", "INFORMATIONAL"}
    cases = [
        (flag_off_scenario["autotune_run"], None, False, "flag_off"),
        (
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
            True,
            "flag_on/both",
        ),
        (
            flag_on_long_null["autotune_run"],
            flag_on_long_null["cvar_rows"][0],
            True,
            "flag_on/long_null",
        ),
        (flag_on_no_rows["autotune_run"], None, True, "flag_on/no_rows"),
    ]
    for run, cvar, enabled, label in cases:
        result = mod.compute_divergence_explainer_observation(
            run, cvar, second_window_enabled=enabled
        )
        assert result["verdict"] in allowed, (
            f"{label}: verdict must be in {allowed!r}, got {result['verdict']!r}"
        )


# ===========================================================================
# Test group 7 — Feature-flag env-var integration via run_divergence_explainer.
# ===========================================================================


def test_run_divergence_explainer_reads_env_flag_off(monkeypatch, flag_off_scenario):
    """run_divergence_explainer reads SECOND_WINDOW_CVAR_ENABLED from the env.

    CONTRACT CHANGE (DE observations-feed leak, PM-adjudicated 2026-07-13):
    database.get_advisor_observations_for_symphony has no role filter, so the
    NOT_APPLICABLE stub row this function used to write when §B is off leaked
    onto /api/advisor-observations?symphony_id=... on every autotune run (the
    _ADVISOR_ROLES exclusion only ever protected the role-AGGREGATE path).
    Approved fix: when §B is off, write NOTHING — no advisor_observations row
    at all — rather than a NOT_APPLICABLE stub. The pure
    compute_divergence_explainer_observation function (tested extensively
    above) is UNCHANGED — it still computes and returns the NOT_APPLICABLE
    dict shape; only the INTEGRATION entry point's write behavior changes.
    """
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "0")

    with patch("database.insert_advisor_observation") as mock_insert:
        result = mod.run_divergence_explainer(
            flag_off_scenario["autotune_run"],
            None,
        )

    mock_insert.assert_not_called()
    assert result is None, (
        f"SECOND_WINDOW_CVAR_ENABLED=0 must write NOTHING and return None "
        f"(no row id — there is no row); got {result!r}"
    )


def test_run_divergence_explainer_reads_env_flag_absent(monkeypatch, flag_off_scenario):
    """When SECOND_WINDOW_CVAR_ENABLED is not set, §B is treated as disabled —
    same no-write contract as the explicit-off test above."""
    mod = _import_divex()
    monkeypatch.delenv("SECOND_WINDOW_CVAR_ENABLED", raising=False)

    with patch("database.insert_advisor_observation") as mock_insert:
        result = mod.run_divergence_explainer(
            flag_off_scenario["autotune_run"],
            None,
        )

    mock_insert.assert_not_called()
    assert result is None, (
        f"absent SECOND_WINDOW_CVAR_ENABLED must treat §B as disabled, write "
        f"nothing, and return None; got {result!r}"
    )


def test_run_divergence_explainer_reads_env_flag_on(monkeypatch, flag_on_both_windows):
    """When SECOND_WINDOW_CVAR_ENABLED=1, run_divergence_explainer produces INFORMATIONAL."""
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "1")

    written_verdicts = []

    def capture_insert(**kwargs):
        written_verdicts.append(kwargs.get("verdict"))
        return 2

    with patch("database.insert_advisor_observation", side_effect=capture_insert):
        mod.run_divergence_explainer(
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
        )

    assert written_verdicts == ["INFORMATIONAL"], (
        "SECOND_WINDOW_CVAR_ENABLED=1 must produce INFORMATIONAL; "
        f"got verdicts: {written_verdicts!r}"
    )


def test_run_divergence_explainer_explicit_kwarg_overrides_env(monkeypatch, flag_off_scenario):
    """The explicit second_window_enabled kwarg overrides the env var.

    Callers in tests can pass second_window_enabled=True even when the env
    var is absent — this is the seam for deterministic unit testing.
    """
    mod = _import_divex()
    monkeypatch.delenv("SECOND_WINDOW_CVAR_ENABLED", raising=False)

    written_verdicts = []

    def capture_insert(**kwargs):
        written_verdicts.append(kwargs.get("verdict"))
        return 3

    with patch("database.insert_advisor_observation", side_effect=capture_insert):
        mod.run_divergence_explainer(
            flag_off_scenario["autotune_run"],
            None,
            second_window_enabled=True,
        )

    # Flag explicitly True → INFORMATIONAL (§B on, but no CVaR rows → null windows)
    assert written_verdicts == ["INFORMATIONAL"], (
        "explicit second_window_enabled=True must override the env var and produce INFORMATIONAL; "
        f"got verdicts: {written_verdicts!r}"
    )


# ===========================================================================
# Test group 8 — Integration entry point: run_divergence_explainer write contract.
# ===========================================================================


def test_run_divergence_explainer_returns_none_when_flag_off(monkeypatch, flag_off_scenario):
    """CONTRACT CHANGE: with §B off, run_divergence_explainer writes nothing and
    must return None — there is no row id to return, and a mocked
    insert_advisor_observation returning a sentinel must NOT surface as the
    result (that would mean insert was called, which it must not be)."""
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "0")
    sentinel_id = 42

    with patch("database.insert_advisor_observation", return_value=sentinel_id) as mock_insert:
        result = mod.run_divergence_explainer(
            flag_off_scenario["autotune_run"],
            None,
        )

    mock_insert.assert_not_called()
    assert result is None, (
        f"run_divergence_explainer with §B off must return None (no write, no "
        f"row id); got {result!r}"
    )


def test_run_divergence_explainer_returns_row_id_when_flag_on(monkeypatch, flag_on_both_windows):
    """The general 'returns the row id from insert_advisor_observation' contract
    — previously only tested against the flag=off path (now the wrong path to
    test it on, per the contract change above) — re-scoped to where it
    actually applies: the §B-on write path, which is unaffected by the
    off-path fix."""
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "1")
    sentinel_id = 42

    with patch("database.insert_advisor_observation", return_value=sentinel_id):
        result = mod.run_divergence_explainer(
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
        )

    assert result == sentinel_id, (
        f"run_divergence_explainer with §B on must return the row id from "
        f"insert_advisor_observation; expected {sentinel_id}, got {result!r}"
    )


def test_run_divergence_explainer_writes_exactly_one_row(
    monkeypatch, flag_off_scenario, flag_on_both_windows
):
    """CONTRACT CHANGE (flag_off leg only — flag_on leg unaffected):
    run_divergence_explainer writes exactly one advisor_observations row when
    §B is on (INFORMATIONAL); writes ZERO rows when §B is off (no more
    NOT_APPLICABLE stub — see the no-write contract change above).
    """
    mod = _import_divex()
    for flag_val, scenario, cvar, label, expected_calls in [
        ("0", flag_off_scenario, None, "flag_off", 0),
        ("1", flag_on_both_windows, flag_on_both_windows["cvar_rows"][0], "flag_on", 1),
    ]:
        monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", flag_val)
        insert_calls = []

        def capture(**kwargs):
            insert_calls.append(kwargs)
            return 1

        with patch("database.insert_advisor_observation", side_effect=capture):
            mod.run_divergence_explainer(scenario["autotune_run"], cvar)

        assert len(insert_calls) == expected_calls, (
            f"{label}: run_divergence_explainer must write exactly "
            f"{expected_calls} row(s); got {len(insert_calls)} insert calls"
        )


def test_run_divergence_explainer_writes_only_divergence_explainer_role(
    monkeypatch, flag_off_scenario, flag_on_both_windows
):
    """CONTRACT CHANGE (flag_off leg only — flag_on leg unaffected): when §B is
    on, only DIVERGENCE_EXPLAINER advisor_role may be written. When §B is off,
    NO role may be written at all (zero insert calls, per the no-write
    contract change above) — the stronger, correct honesty guarantee."""
    mod = _import_divex()
    for flag_val, scenario, cvar, label, expected_roles in [
        ("0", flag_off_scenario, None, "flag_off", []),
        (
            "1",
            flag_on_both_windows,
            flag_on_both_windows["cvar_rows"][0],
            "flag_on",
            ["DIVERGENCE_EXPLAINER"],
        ),
    ]:
        monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", flag_val)
        written_roles = []

        def capture(**kwargs):
            written_roles.append(kwargs.get("advisor_role"))
            return 1

        with patch("database.insert_advisor_observation", side_effect=capture):
            mod.run_divergence_explainer(scenario["autotune_run"], cvar)

        assert written_roles == expected_roles, (
            f"{label}: expected written roles {expected_roles!r}; got {written_roles!r}. "
            f"Phase-2 roles must not be written by this module; §B-off must write nothing."
        )


def test_run_divergence_explainer_does_not_write_to_other_tables(monkeypatch, flag_on_both_windows):
    """Producer must NOT write to researcher_dof_ledger, autotune_runs, or spec_bundles."""
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "1")

    with (
        patch("database.insert_advisor_observation", return_value=1) as mock_insert,
        patch("database.insert_dof_ledger_row") as mock_dof,
        patch("database.save_autotune_run") as mock_save,
        patch("database.insert_spec_bundle") as mock_bundle,
    ):
        mod.run_divergence_explainer(
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
        )

    mock_insert.assert_called_once()
    mock_dof.assert_not_called(), "producer must NOT write to researcher_dof_ledger"
    mock_save.assert_not_called(), "producer must NOT call save_autotune_run"
    mock_bundle.assert_not_called(), "producer must NOT write to spec_bundles"


def test_run_divergence_explainer_raw_response_no_divergence_key_at_write_site(
    monkeypatch, flag_on_both_windows
):
    """CVaR-divergence REJECT enforced at the persistence site.

    When insert_advisor_observation is called, the raw_response arg must
    not contain any forbidden divergence key. This catches an implementation
    that computes the observation correctly but injects the divergence before
    persisting.
    """
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "1")
    raw_at_persist = {}

    def capture(**kwargs):
        raw_at_persist.update(kwargs.get("raw_response") or {})
        return 1

    with patch("database.insert_advisor_observation", side_effect=capture):
        mod.run_divergence_explainer(
            flag_on_both_windows["autotune_run"],
            flag_on_both_windows["cvar_rows"][0],
        )

    _assert_no_forbidden_divergence_keys(
        raw_at_persist, "raw_response passed to insert_advisor_observation"
    )


# ===========================================================================
# Test group 9 — Wall integrity: no direct DB connections.
# ===========================================================================


def test_divergence_explainer_module_does_not_call_get_connection_directly():
    """The advisors/divergence_explainer.py source must NOT call get_connection() directly.

    Direct connection calls bypass the advisor_ro_query fold_role guard and
    the wall-breach tripwire — prohibited by architecture constraint
    (database.py wall integrity rule, same as Overfitting Conscience / Spec Critic).
    """
    source_path = pathlib.Path(__file__).parents[2] / "advisors" / "divergence_explainer.py"
    assert source_path.exists(), (
        f"advisors/divergence_explainer.py not found at {source_path} — "
        "the implementer must create it before this test can pass"
    )
    source = source_path.read_text(encoding="utf-8")
    non_comment_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    non_comment_source = "\n".join(non_comment_lines)
    assert "get_connection()" not in non_comment_source, (
        "advisors/divergence_explainer.py must NOT call get_connection() directly — "
        "use database.advisor_ro_query() for all DB reads (wall integrity)"
    )
    assert "get_ro_connection()" not in non_comment_source, (
        "advisors/divergence_explainer.py must NOT call get_ro_connection() directly — "
        "use database.advisor_ro_query() for all DB reads (wall integrity)"
    )


def test_divergence_explainer_module_references_advisor_ro_query():
    """The advisors/divergence_explainer.py source must reference advisor_ro_query.

    Pairing with the previous test: absence of direct connections AND presence
    of the approved read path is the complete wall integrity contract.
    """
    source_path = pathlib.Path(__file__).parents[2] / "advisors" / "divergence_explainer.py"
    assert source_path.exists(), (
        "advisors/divergence_explainer.py not found — implementer must create it"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "advisor_ro_query" in source, (
        "advisors/divergence_explainer.py must reference database.advisor_ro_query — "
        "it is the sole approved DB read path; it was not found in the module source"
    )


def test_run_divergence_explainer_actually_calls_advisor_ro_query_when_flag_on(
    monkeypatch, flag_on_both_windows
):
    """Wall integrity — call-site test: advisor_ro_query must be CALLED at runtime.

    Source-text tests verify the reference is present (static); this test
    verifies the function is actually invoked on the §B-on path (runtime).
    A dead reference (imported but never called, or called only in a branch
    that never executes) passes the source-text test but violates the wall.

    The producer is expected to call advisor_ro_query to read the
    cvar_diagnostics row for the symphony — that is the approved DB read path.
    We patch database.advisor_ro_query and assert it is called at least once
    when second_window_enabled=True.
    """
    mod = _import_divex()
    monkeypatch.setenv("SECOND_WINDOW_CVAR_ENABLED", "1")

    ro_query_calls = []

    def capture_ro_query(sql, params=()):
        ro_query_calls.append({"sql": sql, "params": params})
        # Return a list with one mock row shaped like a cvar_diagnostics row.
        # Using a plain dict here; the module must tolerate dict-shaped rows
        # (same normalisation contract as overfitting_conscience).
        return [
            {
                "cycle_id": "cycle-mock",
                "symphony_id": flag_on_both_windows["autotune_run"]["symphony_id"],
                "cvar_5pct": -2.1,
                "cvar_5pct_stderr": 0.3,
                "cvar_n_tail": 5,
                "cvar_5pct_long": -1.8,
                "cvar_n_tail_long": 7,
                "ts_utc": "2026-05-27T16:01:00Z",
            }
        ]

    with (
        patch("database.advisor_ro_query", side_effect=capture_ro_query),
        patch("database.insert_advisor_observation", return_value=1),
    ):
        mod.run_divergence_explainer(
            flag_on_both_windows["autotune_run"],
            None,  # pass None so the module must fetch the row via advisor_ro_query
        )

    assert len(ro_query_calls) >= 1, (
        "run_divergence_explainer must call database.advisor_ro_query at least once "
        "when §B is enabled — a dead reference passes the source-text test but "
        "violates the wall integrity contract. Got 0 calls."
    )


# ===========================================================================
# Test group 10 — Performance: producer execution under 100ms.
# ===========================================================================


def test_producer_executes_under_100ms(flag_on_both_windows):
    """compute_divergence_explainer_observation must complete in under 100ms.

    Post-autotune-run advisory call — no blocking I/O permitted.
    100ms is two orders of magnitude above what pure dict manipulation needs.
    A slow implementation indicates an unexpected blocking call.
    """
    # 100ms tolerance is generous for a pure-function dict pass-through with no DB I/O.
    BUDGET_SECONDS = 0.1

    mod = _import_divex()
    run = flag_on_both_windows["autotune_run"]
    cvar_row = flag_on_both_windows["cvar_rows"][0]

    start = time.monotonic()
    mod.compute_divergence_explainer_observation(run, cvar_row, second_window_enabled=True)
    elapsed = time.monotonic() - start

    assert elapsed < BUDGET_SECONDS, (
        f"compute_divergence_explainer_observation took {elapsed:.4f}s — "
        f"exceeds the {BUDGET_SECONDS}s budget. No blocking I/O is permitted "
        "in the pure-function path."
    )


# ===========================================================================
# Test group 11 — Hostile edge cases.
# ===========================================================================


def test_missing_run_id_raises_clearly():
    """When autotune_run dict is missing 'id', the function must raise a clear error.

    A silent no-op would produce a subject_id of 'None', corrupting the
    audit trail. The function must blow up so the caller knows the row is malformed.
    """
    mod = _import_divex()
    malformed_run = {
        "symphony_id": "sym-missing-id",
        "spec_bundle_id": "bundle-missing-id",
        "run_timestamp": "2026-05-27T16:00:00Z",
        # 'id' intentionally omitted
    }
    with pytest.raises((KeyError, ValueError, TypeError)):
        mod.compute_divergence_explainer_observation(
            malformed_run, None, second_window_enabled=False
        )


def test_flag_on_cvar_row_none_does_not_raise():
    """§B on + cvar_row=None must produce INFORMATIONAL with None windows — never raise.

    Phase-1 reality: the CVaR diagnostic may not have run for a symphony yet.
    The producer must handle this gracefully and record what it knows.
    """
    mod = _import_divex()
    run = {
        "id": 100,
        "symphony_id": "sym-no-diag",
        "spec_bundle_id": "bundle-no-diag-100",
        "run_timestamp": "2026-05-27T16:00:00Z",
    }
    # Must not raise:
    result = mod.compute_divergence_explainer_observation(run, None, second_window_enabled=True)
    assert isinstance(result, dict), "§B on + cvar_row=None must return a dict, not raise"
    assert result["verdict"] == "INFORMATIONAL"
    assert result["is_advisory_only"] == 1
    _assert_no_forbidden_divergence_keys(result["raw_response"], "flag_on_cvar_row_None")
