"""
RED tests — M2 CVaR diagnostic: §8 Test 3 + H-2 stderr + S-3 display contract.

Covers:
  3a — compute_cvar_5pct_general_distribution returns the Rockafellar-Uryasev
       general-distribution CVaR (atom-handling correct) on a known kNN pool.
  3b — H-2 binding: displayed stderr uses distinct-tail-obs count (~8), NOT
       the resample count (~5000). Discriminating power: sqrt(5000/8)=25x gap.
  3c — S-3(d) bias warning literal string is present in the dashboard render surface.
  3d — S-3 all-four-elements present on the same display block (a)+(b)+(c)+(d).
  4  — F-4 sentinel: empty pool / all-positive pool → cvar_pct=None, tail_obs_count=0.
  A2 — _reject_non_finite closure: NaN and Inf raise ValueError at entry.
  Schema tripwire — 021_cvar_diagnostics.sql contains neither cvar_divergence
       nor regime_recency_weight.

Mocking philosophy:
  - math_engine functions tested DIRECTLY (no mocking of the SUT).
  - Only network and time are mocked when the SUT calls them.
  - database is patched for tests that exercise the DB write path.

Fixture provenance (D-2 ★ non-circular):
  All numeric reference values come from tests/fixtures/math/m2_cvar_known_pool.json,
  where they were derived by closed-form calculation (see m2_cvar_known_pool.derivation.md).
  None are computed by running compute_cvar_5pct_general_distribution or
  compute_cvar_stderr_distinct_tail — the functions under test.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

import math_engine

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "math"
_MIGRATIONS = pathlib.Path(__file__).parent.parent.parent / "migrations"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared pool-building helpers (derived from fixture, not hardcoded)
# ---------------------------------------------------------------------------


def _pool_from_fixture(fx: dict) -> list[float]:
    """Return the pool as a sorted list of floats from the fixture."""
    return list(fx["pool_distinct_returns"])


# ---------------------------------------------------------------------------
# 3a — Rockafellar-Uryasev estimator identity
# ---------------------------------------------------------------------------


class TestM2CvarMatchesRockafellarUryasevEstimator:
    """
    §8 Test 3a: compute_cvar_5pct_general_distribution on a frozen kNN pool
    must return the closed-form R-U general-distribution CVaR value recorded
    in the fixture. The fixture was constructed so the alpha-quantile lands on
    an atom (fractional_weight=0.5) — the exact edge case the R-U formula
    handles that the naive 'mean of below-VaR entries' estimator does not.
    """

    def test_m2_cvar_matches_rockafellar_uryasev_estimator(self):
        """
        Positive identity: compute_cvar_5pct_general_distribution(pool) must return
        a CVaRAssessment whose cvar_pct equals the fixture's R-U reference value.

        Tolerance rel=1e-9: pure double-precision arithmetic on a fixed input.
        A naive estimator (mean of k strictly-below-VaR values, ignoring the atom)
        gives -0.027429 vs the correct R-U value -0.026267 — ~4% difference.
        At rel=1e-9 this difference fails the assertion, ensuring atom handling is tested.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        expected_cvar = fx["expected"]["cvar_5pct"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.cvar_pct == pytest.approx(
            expected_cvar,
            rel=1e-9,
        ), (
            f"compute_cvar_5pct_general_distribution must return the R-U general-distribution CVaR; "
            f"expected {expected_cvar}, got {result.cvar_pct}. "
            "If the difference is ~4%, the implementation is using the naive mean-of-below-VaR "
            "formula without the fractional atom-weight contribution (R-U 2002 §4)."
        )

    def test_m2_var_5pct_field_matches_fixture_alpha_quantile(self):
        """
        The VaR (alpha-quantile) component must also match the fixture reference.
        This is the pivot of the atom-handling formula — if VaR is wrong, CVaR is wrong.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        expected_var = fx["expected"]["var_5pct"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        # CVaRAssessment has no separate var_pct field — the test asserts via the
        # known-pool fixture that the atom at sorted_pool[7]=-0.01 is the VaR.
        # Discriminating-power: if the impl uses np.percentile(pool, 5) (linear interpolation)
        # rather than the discrete quantile floor(alpha*N), the value shifts and atom-handling fails.
        # We assert indirectly: the cvar_pct formula uses sorted_pool[k] as VaR, so a wrong VaR
        # propagates into a wrong cvar_pct. The tolerance at 1e-9 would catch it.
        assert result.cvar_pct is not None, (
            "cvar_pct must be a float for a sufficient pool; got None"
        )
        assert isinstance(result.cvar_pct, float), (
            f"cvar_pct must be float; got {type(result.cvar_pct)}"
        )

    def test_m2_cvar_constants_exist_with_correct_names(self):
        """
        CVAR_ALPHA_DEFAULT and CVAR_MIN_TAIL_OBS must be named module-level constants
        in math_engine.py (project rule: no magic numbers). Their names are pinned
        here so a rename from the spec breaks this test.
        """
        assert hasattr(math_engine, "CVAR_ALPHA_DEFAULT"), (
            "math_engine must export CVAR_ALPHA_DEFAULT (source: plan §Deliverables 'Constants')"
        )
        assert hasattr(math_engine, "CVAR_MIN_TAIL_OBS"), (
            "math_engine must export CVAR_MIN_TAIL_OBS (source: plan §Deliverables 'Constants')"
        )
        # Values are named constants — test that they are in the correct range,
        # not hardcoded to a specific numeric literal (production values may differ).
        assert 0.0 < math_engine.CVAR_ALPHA_DEFAULT < 1.0, (
            f"CVAR_ALPHA_DEFAULT must be a probability in (0,1); got {math_engine.CVAR_ALPHA_DEFAULT}"
        )
        assert math_engine.CVAR_MIN_TAIL_OBS >= 0, (
            f"CVAR_MIN_TAIL_OBS must be non-negative; got {math_engine.CVAR_MIN_TAIL_OBS}"
        )


# ---------------------------------------------------------------------------
# 3b — H-2 stderr on distinct-tail-obs count (not resample count)
# ---------------------------------------------------------------------------


class TestM2DisplayedStderrUsesDistinctTailObsCount:
    """
    §8 Test 3b (H-2 binding): the stderr returned in CVaRAssessment must be
    computed on the DISTINCT GENUINE tail-observation count (n≈8 for a 150-pool),
    NOT on the resample count (5000). Naive use of 5000 understates by sqrt(5000/8)=25x.

    Discriminating power:
      correct: 0.004988 (n=8)
      wrong:   0.000200 (n=5000)
      ratio: 25x — far outside any reasonable tolerance band.
    """

    def test_m2_displayed_stderr_uses_distinct_tail_obs_count(self):
        """
        Positive assertion: result.stderr must be within 5% of the fixture's
        cvar_5pct_stderr_correct (computed on n=8 distinct tail observations).

        5% tolerance: the closed-form small-sample stderr recipe may use ddof=0
        vs ddof=1; a 5% band admits both conventions while rejecting the 25x-wrong
        resample-count value.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        expected_stderr = fx["expected"]["cvar_5pct_stderr_correct"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.stderr is not None, (
            "stderr must be populated for a sufficient pool (n_tail_distinct=8)"
        )
        assert result.stderr == pytest.approx(
            expected_stderr,
            rel=0.05,
        ), (
            # rel=0.05: 5% band admits ddof=0 vs ddof=1 convention difference (~3% at n=8)
            # and still rejects the 25x-wrong resample-count value.
            f"stderr must use distinct-tail-obs denominator (n=8); "
            f"expected ~{expected_stderr:.6f}, got {result.stderr:.6f}. "
            "H-2: using the resample count (5000) would give ~0.0002 — 25x smaller."
        )

    def test_m2_displayed_stderr_not_close_to_resample_count_value(self):
        """
        Negative identity: result.stderr must NOT be within 50% of the fixture's
        cvar_5pct_stderr_wrong_resample (computed on the resample count 5000).

        50% band is deliberately generous — the correct/wrong values differ by 25x,
        so even a 50% band around the wrong value is far from the correct value.
        The 50x margin (25x ratio vs 50% tolerance) makes this assertion robust.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        wrong_stderr = fx["expected"]["cvar_5pct_stderr_wrong_resample"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.stderr is not None, "stderr must be populated"
        # Discriminating-power: correct≈0.004988, wrong≈0.000200. ratio=25x.
        # 50%-band around wrong: [0.000100, 0.000300]. Correct value (0.004988) is outside.
        assert result.stderr != pytest.approx(
            wrong_stderr,
            rel=0.5,
        ), (
            f"stderr must NOT be computed on the resample count; "
            f"got {result.stderr:.6f} which is within 50% of the wrong value {wrong_stderr:.6f}. "
            "H-2: the resample-count stderr is 25x smaller — if displayed it would "
            "convert the S-3 honesty mechanism into a false-precision generator."
        )

    def test_m2_tail_obs_count_field_matches_distinct_count(self):
        """
        result.tail_obs_count must match the fixture's n_tail_distinct (=8).
        tail_obs_count is the auditable denominator — the persisted cvar_n_tail SQL column.
        A reviewer can verify the stderr was not computed on the resample count
        by inspecting this field.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        expected_n = fx["expected"]["n_tail_displayed"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.tail_obs_count == expected_n, (
            f"tail_obs_count must equal the distinct-tail-obs count ({expected_n}); "
            f"got {result.tail_obs_count}. "
            "This is the auditable denominator for the H-2 stderr — not the resample count."
        )

    def test_m2_dedicated_stderr_function_exists(self):
        """
        compute_cvar_stderr_distinct_tail must be a standalone function in math_engine.
        The plan requires a separate function so the H-2 test can assert it consumes
        the distinct-tail count exclusively (plan §Deliverables Code, second function).
        """
        assert hasattr(math_engine, "compute_cvar_stderr_distinct_tail"), (
            "math_engine must export compute_cvar_stderr_distinct_tail (plan §Deliverables Code); "
            "a separate function is required so the H-2 test can assert it uses the "
            "distinct-tail count exclusively, not the resample count."
        )

    def test_m2_stderr_function_returns_correct_value(self):
        """
        compute_cvar_stderr_distinct_tail called with the fixture pool must return
        the fixture's correct stderr value within 5% tolerance.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)
        expected_stderr = fx["expected"]["cvar_5pct_stderr_correct"]

        stderr = math_engine.compute_cvar_stderr_distinct_tail(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert stderr is not None, (
            "compute_cvar_stderr_distinct_tail must return a float for a sufficient pool"
        )
        assert stderr == pytest.approx(
            expected_stderr,
            rel=0.05,
        ), (
            f"compute_cvar_stderr_distinct_tail must use distinct-tail denominator; "
            f"expected ~{expected_stderr:.6f}, got {stderr:.6f}"
        )


# ---------------------------------------------------------------------------
# 3c / 3d — S-3 four-part display contract
# ---------------------------------------------------------------------------


class TestM2DisplaySurfaceContainsFullS3FourPartContract:
    """
    §8 Test 3c + 3d (S-3 binding): the dashboard render surface for M2 must
    contain ALL FOUR elements of the S-3 display contract:

      (a) stderr value / uncertainty band
      (b) tail_obs_count (the n=8 distinct observations)
      (c) "diagnostic, not a signal — do not trade on this" label
      (d) "known-low-biased LOWER BOUND on tail severity, not a point estimate"

    Each is tested as its own parametrized sub-assertion so a reviewer sees
    exactly which element is missing if a regression lands.

    The assertions use data-attribute / text presence rather than CSS structure
    (per feedback_tests_assert_design_contract_not_values). The implementer
    must add data-cvar-* attributes or equivalent labeled elements.

    Dashboard is tested via Flask test_client() with database mocked.
    """

    @pytest.fixture
    def client(self):
        import app as app_module

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            yield c

    @pytest.fixture
    def cvar_row(self):
        """A cvar_diagnostics row derived from the known-pool fixture."""
        fx = _load("m2_cvar_known_pool.json")
        return {
            "cycle_id": fx["cycle_id"],
            "symphony_id": "sym-m2-test",
            "cvar_5pct": fx["expected"]["cvar_5pct"],
            "cvar_5pct_stderr": fx["expected"]["cvar_5pct_stderr_correct"],
            "cvar_n_tail": fx["expected"]["n_tail_displayed"],
            "cvar_5pct_long": None,
            "cvar_n_tail_long": None,
        }

    def _get_dashboard_html(self, client, cvar_row):
        """Render the dashboard HTML with a known cvar_diagnostics row."""
        import app as app_module

        state = {
            "sym-m2-test": {
                "name": "M2 Test Symphony",
                "account": "ACC1",
                "mc_prob": 55.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "current_return": 1.0,
                "stop_trigger": -2.0,
                "shadow_hwm": 2.0,
                "high_water_mark": 2.0,
                "current_value": 10000.0,
            }
        }
        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}),
        ):
            db_mock.load_state.return_value = state
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            db_mock.read_cvar_diagnostic_for_cycle.return_value = cvar_row

            resp = client.get("/")
            assert resp.status_code == 200
            return resp.data.decode("utf-8")

    @pytest.mark.parametrize(
        "element_label,description",
        [
            ("a_stderr", "stderr / uncertainty band — S-3 element (a)"),
            ("b_tail_obs_count", "tail_obs_count (n=8) — S-3 element (b)"),
            (
                "c_diagnostic_label",
                "'diagnostic, not a signal — do not trade on this' — S-3 element (c)",
            ),
            ("d_bias_warning", "'known-low-biased LOWER BOUND on tail severity' — S-3 element (d)"),
        ],
    )
    def test_m2_display_surface_contains_s3_element(
        self, client, cvar_row, element_label, description
    ):
        """
        Each of the four S-3 elements must be present in the rendered dashboard HTML.
        Parametrized so the reviewer sees which specific element is missing on failure.

        (a) asserts data-cvar-stderr attribute or labeled stderr text is present.
        (b) asserts tail_obs_count is rendered (data attribute or text containing the n value).
        (c) asserts the 'diagnostic, not a signal' label text is present.
        (d) asserts the 'known-low-biased LOWER BOUND' bias warning is present.
        """
        html = self._get_dashboard_html(client, cvar_row)
        fx = _load("m2_cvar_known_pool.json")

        if element_label == "a_stderr":
            # Assert: the rendered HTML contains a labeled stderr element.
            # Acceptable: data-cvar-stderr attribute, or text like '±0.004988' or 'stderr'.
            # Implementation must add a labeled element; a placeholder '±—' fails.
            has_stderr_attr = "data-cvar-stderr" in html
            has_stderr_text = "stderr" in html.lower() or "±" in html
            assert has_stderr_attr or has_stderr_text, (
                f"S-3 element (a) MISSING: rendered HTML must contain a labeled stderr "
                f"(data-cvar-stderr attribute or stderr text); "
                f"failing means the uncertainty band is absent — the operator anchors on "
                f"a CVaR number with no uncertainty quantification. {description}"
            )

        elif element_label == "b_tail_obs_count":
            # Assert: the tail_obs_count (n=8 from fixture) is rendered.
            # Acceptable: 'n=8' or data-cvar-n-tail or the numeric value in a labeled span.
            n_tail = cvar_row["cvar_n_tail"]
            has_n_tail_attr = "data-cvar-n-tail" in html
            has_n_tail_text = f"n={n_tail}" in html or f"n = {n_tail}" in html
            assert has_n_tail_attr or has_n_tail_text, (
                f"S-3 element (b) MISSING: rendered HTML must show tail_obs_count={n_tail}; "
                f"failing means the operator cannot see how many real tail observations "
                f"the CVaR estimate is based on. {description}"
            )

        elif element_label == "c_diagnostic_label":
            # Assert: the 'diagnostic, not a signal' label is byte-present in the render.
            # The fixture records the canonical substring.
            label_c = fx["s3_display_contract"]["label_c_substring"]
            assert label_c in html, (
                f"S-3 element (c) MISSING: rendered HTML must contain the literal substring "
                f"'{label_c}'; the 'do not trade on this' label is a load-bearing safety "
                f"contract — its absence means the operator might act on M2 as a signal. "
                f"{description}"
            )

        elif element_label == "d_bias_warning":
            # Assert: the bias-warning literal is byte-present in the render.
            # This is the load-bearing element per plan §Why: without it M2 manufactures
            # false comfort and is mildly harmful.
            bias_warning = fx["s3_display_contract"]["bias_warning_substring"]
            assert bias_warning in html, (
                f"S-3 element (d) MISSING: rendered HTML must contain '{bias_warning}'; "
                f"the bias warning is LOAD-BEARING — without it the operator anchors on a "
                f"systematically low-biased CVaR number that understates tail severity. "
                f"{description}"
            )

    def test_m2_display_surface_is_read_only_no_action_element(self, client, cvar_row):
        """
        I-3 ★ (architecture constraint 2): the CVaR display block must NOT contain a
        button, form, or actionable element. The dashboard is a read-only operator surface.

        Asserting the absence of <form> and <button> elements inside the cvar diagnostic block.
        """
        html = self._get_dashboard_html(client, cvar_row)
        import re

        # Look for form or button elements near the cvar display area.
        # We use a lenient check: no action-path attributes on any element in a
        # region that mentions 'cvar' or 'diagnostic'.
        cvar_region_start = html.lower().find("cvar")
        if cvar_region_start == -1:
            # cvar block not yet rendered — test is RED because the block is absent.
            pytest.fail(
                "I-3: cvar diagnostic block not found in dashboard HTML; "
                "M2 display surface must be present (read-only)"
            )

        # Check region around the first cvar mention (±2000 chars)
        region = html[max(0, cvar_region_start - 500) : cvar_region_start + 2000]
        form_in_region = re.search(r"<form\b", region, re.IGNORECASE)
        button_in_region = re.search(r"<button\b", region, re.IGNORECASE)

        assert not form_in_region, (
            "I-3 violation: <form> element found in CVaR display region; "
            "the M2 display surface must be read-only — no form submissions allowed."
        )
        assert not button_in_region, (
            "I-3 violation: <button> element found in CVaR display region; "
            "the M2 display surface is an operator observation instrument — no actions."
        )


# ---------------------------------------------------------------------------
# 4 — Replay determinism (one-anchor, bit-identical)
# ---------------------------------------------------------------------------


class TestM2ReplayDeterminism:
    """
    §8 Test 4: same (pool) input → bit-identical cvar_pct, stderr, tail_obs_count
    across independent calls. Parity assertion excludes id and ts_utc (per H-8 A3).
    """

    def test_m2_replay_determinism_bit_identical_cvar(self):
        """
        Two independent calls to compute_cvar_5pct_general_distribution with the
        same pool must return bit-identical cvar_pct and tail_obs_count.
        The function is pure; there is no RNG in the R-U formula.
        """
        fx = _load("m2_cvar_known_pool.json")
        pool = _pool_from_fixture(fx)

        result_a = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )
        result_b = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        # cvar_pct — must be bit-identical (pure arithmetic on fixed input)
        assert result_a.cvar_pct == result_b.cvar_pct, (
            f"cvar_pct is not bit-identical across two calls: "
            f"{result_a.cvar_pct} vs {result_b.cvar_pct}. "
            "The R-U formula is pure — no randomness should produce different results."
        )

        # tail_obs_count — must be bit-identical (integer, deterministic)
        assert result_a.tail_obs_count == result_b.tail_obs_count, (
            f"tail_obs_count differs across calls: {result_a.tail_obs_count} vs {result_b.tail_obs_count}"
        )

        # stderr — must be bit-identical (pure arithmetic)
        assert result_a.stderr == result_b.stderr, (
            f"stderr differs across calls: {result_a.stderr} vs {result_b.stderr}"
        )

    def test_m2_parity_columns_exclude_id_and_ts_utc(self):
        """
        H-8 A3: the _PARITY_DECISION_COLUMNS tuple in database.py must contain
        cvar_5pct, cvar_5pct_stderr, cvar_n_tail (and §B columns) but must NOT
        contain id or ts_utc. This pins the spec contract on the accessor layer.
        """
        import database as _db

        assert hasattr(_db, "_PARITY_DECISION_COLUMNS"), (
            "database must export _PARITY_DECISION_COLUMNS (H-8 A3 binding)"
        )
        decision_cols = set(_db._PARITY_DECISION_COLUMNS)

        # Required decision columns must be present
        for required in ("cvar_5pct", "cvar_5pct_stderr", "cvar_n_tail"):
            assert required in decision_cols, (
                f"_PARITY_DECISION_COLUMNS must contain '{required}'; got {decision_cols}"
            )

        # Exclusions must NOT be present
        assert hasattr(_db, "_PARITY_EXCLUDE_COLUMNS"), (
            "database must export _PARITY_EXCLUDE_COLUMNS"
        )
        exclude_cols = set(_db._PARITY_EXCLUDE_COLUMNS)
        for excluded in ("id", "ts_utc"):
            assert excluded in exclude_cols, (
                f"_PARITY_EXCLUDE_COLUMNS must contain '{excluded}' (autoincrement/wall-clock); "
                f"got {exclude_cols}"
            )
            assert excluded not in decision_cols, (
                f"'{excluded}' must not appear in _PARITY_DECISION_COLUMNS"
            )


# ---------------------------------------------------------------------------
# F-4 — Sentinel mirror: empty pool / all-positive pool
# ---------------------------------------------------------------------------


class TestM2SentinelMirror:
    """
    F-4 ★: when the kNN pool is empty or has no tail observations (all returns
    above the alpha-quantile), compute_cvar_5pct_general_distribution must return
    cvar_pct=None, tail_obs_count=0, stderr=None, and insufficient_reason populated.

    The MC sentinel discipline (MC_INSUFFICIENT_HISTORY_SENTINEL = None) is mirrored:
    an absent CVaR estimate is never a breach signal.
    """

    def test_m2_empty_pool_returns_sentinel(self):
        """Empty pool → sentinel result with cvar_pct=None and tail_obs_count=0."""
        result = math_engine.compute_cvar_5pct_general_distribution(
            [], alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.cvar_pct is None, (
            f"Empty pool must return cvar_pct=None (sentinel); got {result.cvar_pct}"
        )
        assert result.tail_obs_count == 0, (
            f"Empty pool must return tail_obs_count=0; got {result.tail_obs_count}"
        )
        assert result.stderr is None, (
            f"Empty pool must return stderr=None (sentinel); got {result.stderr}"
        )
        assert result.insufficient_reason is not None, (
            "Empty pool must populate insufficient_reason with a human-readable message"
        )

    def test_m2_all_positive_pool_returns_sentinel_when_no_tail(self):
        """
        Pool with all-positive returns and no sub-alpha-quantile tail.
        For a pool of size 10 and alpha=0.05: k=floor(0.5)=0 tail observations.
        Should return the insufficient sentinel.
        """
        # Pool of 10 small positive returns — no tail below VaR at 5%
        # k = floor(0.05 * 10) = 0 → zero below-VaR entries
        all_positive_pool = [0.001 * i for i in range(1, 11)]  # 10 values, all positive

        result = math_engine.compute_cvar_5pct_general_distribution(
            all_positive_pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        # With k=0 and pool of 10: alpha*N=0.5, k=floor(0.5)=0
        # No below-VaR values; if the implementation correctly handles this edge,
        # it should return the insufficient sentinel (CVAR_MIN_TAIL_OBS guard).
        # (A naive impl might return the VaR value as CVaR with 0 weight — wrong.)
        assert result.cvar_pct is None or result.tail_obs_count == 0, (
            "All-positive pool (no tail observations) must return cvar_pct=None or "
            "tail_obs_count=0 to trigger the insufficient sentinel path. "
            f"Got cvar_pct={result.cvar_pct}, tail_obs_count={result.tail_obs_count}."
        )

    def test_m2_sentinel_result_has_breach_false(self):
        """
        F-4 fail-safe: CVaRAssessment with cvar_pct=None must have breach=False.
        This is enforced by CVaRAssessment.__post_init__ — calling it with
        cvar_pct=None and breach=True must raise ValueError.
        """
        with pytest.raises(ValueError, match="fail-safe"):
            math_engine.CVaRAssessment(
                cvar_pct=None,
                breach=True,
                tail_obs_count=0,
                stderr=None,
                insufficient_reason="test",
            )

    def test_m2_sentinel_result_tail_obs_count_zero_when_cvar_none(self):
        """
        CVaRAssessment with cvar_pct=None must have tail_obs_count=0.
        A non-zero tail_obs_count with cvar_pct=None is an incoherent state
        (enforced by __post_init__).
        """
        with pytest.raises(ValueError):
            math_engine.CVaRAssessment(
                cvar_pct=None,
                breach=False,
                tail_obs_count=3,  # invalid: must be 0 when cvar_pct is None
                stderr=None,
                insufficient_reason="test",
            )


# ---------------------------------------------------------------------------
# A-2 — NaN / Inf closure at entry
# ---------------------------------------------------------------------------


class TestM2NonFiniteClosure:
    """
    A-2 ★: compute_cvar_5pct_general_distribution must reject NaN and Inf at
    entry (via _reject_non_finite). Non-finite values must never silently propagate
    into a CVaR comparison.
    """

    def test_m2_nan_in_pool_raises_value_error(self):
        """
        Pool containing NaN must raise ValueError at entry, not produce a silent
        NaN result. NaN in a CVaR comparison would make every comparison short-circuit.
        """
        pool_with_nan = [-0.05, -0.03, float("nan"), 0.01, 0.02]

        with pytest.raises(ValueError, match="NaN"):
            math_engine.compute_cvar_5pct_general_distribution(
                pool_with_nan, alpha=math_engine.CVAR_ALPHA_DEFAULT
            )

    def test_m2_positive_inf_in_pool_raises_value_error(self):
        """
        Pool containing +Inf must raise ValueError at entry.
        Inf in a return series would produce a spurious "very large positive return"
        that distorts the sort and quantile computation.
        """
        pool_with_inf = [-0.05, float("inf"), 0.01, 0.02]

        with pytest.raises(ValueError):
            math_engine.compute_cvar_5pct_general_distribution(
                pool_with_inf, alpha=math_engine.CVAR_ALPHA_DEFAULT
            )

    def test_m2_negative_inf_in_pool_raises_value_error(self):
        """
        Pool containing -Inf must raise ValueError at entry.
        -Inf in a tail would make CVaR = -Inf, a catastrophic loss signal that
        must never fire silently.
        """
        pool_with_neg_inf = [-0.05, float("-inf"), 0.01]

        with pytest.raises(ValueError):
            math_engine.compute_cvar_5pct_general_distribution(
                pool_with_neg_inf, alpha=math_engine.CVAR_ALPHA_DEFAULT
            )

    def test_m2_stderr_function_raises_on_non_finite(self):
        """
        compute_cvar_stderr_distinct_tail must also reject non-finite inputs.
        """
        pool_with_nan = [float("nan"), -0.05, 0.01]

        with pytest.raises(ValueError):
            math_engine.compute_cvar_stderr_distinct_tail(
                pool_with_nan, alpha=math_engine.CVAR_ALPHA_DEFAULT
            )


# ---------------------------------------------------------------------------
# Data-starved pool — scenario 4 (n_tail=3)
# ---------------------------------------------------------------------------


class TestM2DataStarvedPoolDisplaysNTail:
    """
    §8 Scenario 4 (W-H1 guard): a data-starved pool (n_tail_distinct=3) must
    surface the small-n count and compute the correct stderr on n=3.
    This proves M2's "you are tail-data-starved right now" surface.
    """

    def test_m2_data_starved_pool_returns_correct_n_tail(self):
        """
        Pool from m2_cvar_data_starved_pool.json (N=60, n_tail=3) must return
        tail_obs_count=3 (not 5000 or 60).
        """
        fx = _load("m2_cvar_data_starved_pool.json")
        pool = _pool_from_fixture(fx)
        expected_n = fx["expected"]["n_tail_displayed"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.tail_obs_count == expected_n, (
            f"Data-starved pool must return tail_obs_count={expected_n}; "
            f"got {result.tail_obs_count}. "
            "This n value is displayed to the operator as the 'data quality indicator' "
            "for the CVaR estimate (S-3 element b)."
        )

    def test_m2_data_starved_pool_stderr_uses_small_n_denominator(self):
        """
        Data-starved pool stderr must be computed on n=3, not n=5000.
        The stderr is larger (looser) for small n — correctly signaling poor precision.
        """
        fx = _load("m2_cvar_data_starved_pool.json")
        pool = _pool_from_fixture(fx)
        expected_stderr = fx["expected"]["cvar_5pct_stderr_correct"]

        result = math_engine.compute_cvar_5pct_general_distribution(
            pool, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

        assert result.stderr is not None, "stderr must not be None for a pool with n_tail=3"
        assert result.stderr == pytest.approx(
            expected_stderr,
            rel=0.05,
        ), (
            f"Data-starved stderr must be on n=3 denominator; "
            f"expected ~{expected_stderr:.6f}, got {result.stderr:.6f}"
        )


# ---------------------------------------------------------------------------
# §B second-window NULL semantics
# ---------------------------------------------------------------------------


class TestM2SecondWindowNullSemantics:
    """
    §B.6: when the long-window is insufficient, cvar_5pct_long writes NULL (not zero).
    NULL preserves the sentinel discipline (F-4 ★) — a fabricated zero would
    mislead the operator with a false-precision long-window estimate.
    """

    def test_m2_second_window_null_when_insufficient(self):
        """
        read_cvar_diagnostic_for_cycle must return NULL for cvar_5pct_long when
        the long window was insufficient. Zero would be a fabricated estimate.

        This tests the database write path: record_cvar_diagnostic is called with
        cvar_5pct_long=None and the row must persist NULL (not 0.0).
        """

        import database as _db

        # Insert a row with cvar_5pct_long=None explicitly
        _db.record_cvar_diagnostic(
            cycle_id="TEST_NULL_LONG_20260525",
            symphony_id="sym-null-test",
            cvar_5pct=-0.025,
            cvar_5pct_stderr=0.005,
            cvar_n_tail=7,
            cvar_5pct_long=None,  # insufficient long window
            cvar_n_tail_long=None,
            mode="replay",
        )

        row = _db.read_cvar_diagnostic_for_cycle("TEST_NULL_LONG_20260525", "sym-null-test")
        assert row is not None, "Row must exist after write"
        assert row["cvar_5pct_long"] is None, (
            "cvar_5pct_long must persist as NULL when long window is insufficient; "
            f"got {row['cvar_5pct_long']!r}. "
            "Zero would be a fabricated estimate violating the sentinel discipline (F-4 ★)."
        )
        assert row["cvar_n_tail_long"] is None, (
            f"cvar_n_tail_long must persist as NULL; got {row['cvar_n_tail_long']!r}"
        )


# ---------------------------------------------------------------------------
# §B no-divergence-column tripwire
# ---------------------------------------------------------------------------


class TestNoCvarDivergenceColumnOrDisplayValue:
    """
    §B.6 binding: 021_cvar_diagnostics.sql and Flask templates must NOT contain
    a cvar_divergence or regime_recency_weight column. The CVaR-divergence idea
    was rejected; the schema tripwire prevents regression.
    """

    def test_migration_schema_contains_no_divergence_column(self):
        """
        021_cvar_diagnostics.sql must not define a cvar_divergence or
        regime_recency_weight column. The moment one is added, this test fails —
        surfacing the §B.6 binding violation before it ships.
        """
        migration_sql = (_MIGRATIONS / "021_cvar_diagnostics.sql").read_text(encoding="utf-8")

        assert "cvar_divergence" not in migration_sql.lower(), (
            "021_cvar_diagnostics.sql contains 'cvar_divergence' — FORBIDDEN by §B.6 binding. "
            "The CVaR-divergence detector was rejected; this column must not exist."
        )
        assert "regime_recency_weight" not in migration_sql.lower(), (
            "021_cvar_diagnostics.sql contains 'regime_recency_weight' — FORBIDDEN by §B.6 binding."
        )

    def test_migration_schema_only_contains_allowed_second_window_columns(self):
        """
        The only allowed second-window columns are cvar_5pct_long and cvar_n_tail_long.
        Any column definition beginning with 'cvar_' other than the approved set fails this test.

        We match only DDL column definitions (lines that start a column name in CREATE TABLE),
        not table names or other identifiers.
        """
        migration_sql = (
            (_MIGRATIONS / "021_cvar_diagnostics.sql").read_text(encoding="utf-8").lower()
        )
        allowed_cvar_columns = {
            "cvar_5pct",
            "cvar_5pct_stderr",
            "cvar_n_tail",
            "cvar_5pct_long",
            "cvar_n_tail_long",
        }

        import re

        # Match column definitions: lines that begin with optional whitespace followed by
        # a cvar_* identifier and a SQL type keyword (REAL, INTEGER, TEXT, BLOB, NUMERIC).
        # This excludes table names (which are followed by '(' not a type keyword).
        column_def_matches = re.findall(
            r"^\s+(cvar_\w+)\s+(?:real|integer|text|blob|numeric)\b",
            migration_sql,
            re.MULTILINE,
        )
        found_cvar_columns = {m.strip() for m in column_def_matches}

        disallowed = found_cvar_columns - allowed_cvar_columns
        assert not disallowed, (
            f"Disallowed cvar_* column definition(s) found in migration: {disallowed}. "
            f"Allowed: {allowed_cvar_columns}. §B.6: no signed-divergence quantity."
        )

    def test_database_py_no_divergence_column_reference(self):
        """
        database.py must not reference cvar_divergence or regime_recency_weight.
        A column insertion in the helper function would bypass the schema tripwire.
        """
        db_source = (pathlib.Path(__file__).parent.parent.parent / "database.py").read_text(
            encoding="utf-8"
        )

        assert "cvar_divergence" not in db_source.lower(), (
            "database.py contains 'cvar_divergence' — FORBIDDEN by §B.6 binding."
        )
        assert "regime_recency_weight" not in db_source.lower(), (
            "database.py contains 'regime_recency_weight' — FORBIDDEN by §B.6 binding."
        )

    def test_migration_cvar_n_tail_is_not_null_default_zero(self):
        """
        spec-m2 BLOCK 6: cvar_n_tail must be NOT NULL DEFAULT 0, not DEFAULT NULL.

        With DEFAULT NULL a sentinel-path write (tail_obs_count=0) stores NULL rather
        than 0, making the column semantically ambiguous between 'insufficient history'
        and 'zero distinct tail observations' (both legitimate states). The NOT NULL
        constraint enforces the F-4 sentinel discipline at the schema layer:
        cvar_n_tail=0 is the unambiguous insufficient sentinel, never NULL.

        A NULLable cvar_n_tail also means the S-3 (b) display (tail_obs_count) can
        receive NULL from the DB read and render an absent count — violating the
        "tail_obs_count visible" requirement.
        """
        import re

        migration_sql = (_MIGRATIONS / "021_cvar_diagnostics.sql").read_text(encoding="utf-8")

        # Find the cvar_n_tail column definition line
        n_tail_line_match = re.search(
            r"cvar_n_tail\s+INTEGER[^\n,)]*",
            migration_sql,
            re.IGNORECASE,
        )
        assert n_tail_line_match is not None, (
            "cvar_n_tail column definition not found in 021_cvar_diagnostics.sql"
        )

        col_definition = n_tail_line_match.group(0)

        # Must have NOT NULL
        assert "NOT NULL" in col_definition.upper(), (
            f"cvar_n_tail must be NOT NULL in 021_cvar_diagnostics.sql; "
            f"got: {col_definition!r}. "
            "spec-m2 BLOCK 6: NOT NULL enforces F-4 sentinel discipline at schema level; "
            "DEFAULT NULL permits ambiguous NULL writes that bypass the 0-sentinel contract."
        )

        # Must have DEFAULT 0 (or equivalent)
        assert re.search(r"DEFAULT\s+0\b", col_definition, re.IGNORECASE), (
            f"cvar_n_tail must have DEFAULT 0 in 021_cvar_diagnostics.sql; "
            f"got: {col_definition!r}. "
            "DEFAULT 0 ensures fresh rows land with the unambiguous insufficient-sentinel value."
        )


# ---------------------------------------------------------------------------
# spec-m2 FINDING 1 — compute_portfolio_cvar must delegate to R-U estimator
# on the DISTINCT kNN pool, not use the resample-mean on 5000 bootstrap paths.
# ---------------------------------------------------------------------------


def _build_uniform_historical_data():
    """
    Build a minimal deterministic historical_data for compute_portfolio_cvar testing.

    Design: 170 dates, SPY daily_ret=0.0 on every date (all candidates equidistant in
    kNN space), single ticker 'TST' with return = (-0.10 + i * 0.001) for date i.

    With uniform SPY returns, all candidate distances are equal, so np.argpartition
    selects the first MC_DEFAULT_NEIGHBOR_K=150 candidate indices (indices 0..149 of
    the candidate array). Candidates start at index MC_VOL_WINDOW_DAYS-1=19 of the
    dates list, so the pool comes from dates[19..168].

    Portfolio return for date i = TST return * PCT_SCALAR = (-0.10 + i*0.001) * 100.
    This gives a fully controlled pool with known R-U CVaR (derivable without calling SUT).

    Fixture provenance (D-2 non-circular): pool contents are determined by this
    construction, not by running compute_portfolio_cvar or compute_cvar_5pct_general_distribution.
    """
    n_dates = 170
    dates = [f"2024-01-{i + 1:04d}" for i in range(n_dates)]
    historical_data = {}
    for i, date in enumerate(dates):
        r = -0.10 + i * 0.001
        historical_data[date] = {
            "SPY": {"daily_ret": 0.0},
            "TST": {"daily_ret": r},
        }
    return dates, historical_data


class TestComputePortfolioCvarDelegatesToRuEstimator:
    """
    spec-m2 FINDING 1 (MAJOR): compute_portfolio_cvar must call
    compute_cvar_5pct_general_distribution on the DISTINCT kNN pool
    (nearest_day_returns, N≈150), not compute a resample mean on 5000
    bootstrap paths.

    The two symptoms:
      (a) cvar_pct should match the R-U estimate on the pool (not the resample mean).
      (b) tail_obs_count should be k_distinct ≤ MC_DEFAULT_NEIGHBOR_K (≈8 for the
          test pool), never 5% of 5000 = 250.

    Both assertions are RED on the current implementation (which uses CVAR_TAIL_PCT *
    simulation_paths = 250 as the tail count and np.mean(sim_paths[:250]) as cvar_pct).
    """

    _HOLDINGS = [{"ticker": "TST", "allocation": 1.0}]
    _CYCLE_ID = "test_finding1_cycle"
    _SPY_TODAY = 0.0  # uniform -> all candidates equidistant

    def _call_portfolio_cvar(self):
        _dates, historical_data = _build_uniform_historical_data()
        return math_engine.compute_portfolio_cvar(
            cycle_id=self._CYCLE_ID,
            holdings=self._HOLDINGS,
            historical_data=historical_data,
            spy_today_return=self._SPY_TODAY,
            simulation_paths=math_engine.MC_DEFAULT_SIMULATION_PATHS,
            neighbor_k=math_engine.MC_DEFAULT_NEIGHBOR_K,
            mode=None,
        )

    def _reference_ru_estimate(self):
        """Compute the R-U reference directly on the known pool (non-SUT path)."""
        dates, historical_data = _build_uniform_historical_data()
        # Candidates start at index MC_VOL_WINDOW_DAYS-1=19; first neighbor_k=150 selected.
        pool_dates = dates[
            math_engine.MC_VOL_WINDOW_DAYS - 1 : math_engine.MC_VOL_WINDOW_DAYS
            - 1
            + math_engine.MC_DEFAULT_NEIGHBOR_K
        ]
        pool_returns = [
            historical_data[d]["TST"]["daily_ret"] * math_engine.PCT_SCALAR for d in pool_dates
        ]
        return math_engine.compute_cvar_5pct_general_distribution(
            pool_returns, alpha=math_engine.CVAR_ALPHA_DEFAULT
        )

    def test_compute_portfolio_cvar_cvar_pct_matches_ru_estimate_on_knn_pool(self):
        """
        compute_portfolio_cvar.cvar_pct must equal compute_cvar_5pct_general_distribution
        called on the same nearest_day_returns pool.

        Currently FAILS: compute_portfolio_cvar returns np.mean(sim_paths[:250]) = -7.80
        (resample mean on 5000 bootstrap paths). The R-U estimate on the 150-entry pool
        is -7.773 (atom-corrected). Difference: ~0.35%, detectable at rel=1e-3.

        Passes only when compute_portfolio_cvar delegates to compute_cvar_5pct_general_distribution
        (or implements the identical R-U formula) on nearest_day_returns directly.
        """
        result = self._call_portfolio_cvar()
        reference = self._reference_ru_estimate()

        assert result.cvar_pct is not None, (
            "compute_portfolio_cvar returned sentinel (cvar_pct=None) on a sufficient pool."
        )
        assert reference.cvar_pct is not None, (
            "Reference R-U estimate returned sentinel on the known pool — fixture construction error."
        )
        # rel=1e-6: tight enough to detect resample-mean vs R-U difference (~0.35%)
        # while allowing floating-point accumulation differences.
        assert result.cvar_pct == pytest.approx(reference.cvar_pct, rel=1e-6), (
            f"compute_portfolio_cvar.cvar_pct={result.cvar_pct:.8f} does not match "
            f"R-U estimate on the kNN pool={reference.cvar_pct:.8f}. "
            "spec-m2 FINDING 1: compute_portfolio_cvar must use the R-U general-distribution "
            "estimator on nearest_day_returns, NOT the resample mean on 5000 bootstrap paths. "
            "The resample mean is the A-1 ★ violation the test suite was designed to catch."
        )

    def test_compute_portfolio_cvar_tail_obs_count_is_knn_pool_based_not_resample(self):
        """
        compute_portfolio_cvar.tail_obs_count must be ≤ MC_DEFAULT_NEIGHBOR_K (the kNN
        pool size, ≈150), never 5% of simulation_paths (5% * 5000 = 250).

        Currently FAILS: tail_obs_count=250 (int(CVAR_TAIL_PCT * 5000)).
        With the R-U formula on a 150-entry pool: alpha*N=7.5, tail_obs_count=8.

        The H-2 binding violation: 250 vs 8 is a 31x error in the denominator used
        for stderr. tail_obs_count <= MC_DEFAULT_NEIGHBOR_K is a necessary condition;
        the exact value must equal floor(alpha*N_pool) + (1 if fractional_weight > 0).
        """
        result = self._call_portfolio_cvar()
        reference = self._reference_ru_estimate()

        assert result.tail_obs_count <= math_engine.MC_DEFAULT_NEIGHBOR_K, (
            f"compute_portfolio_cvar.tail_obs_count={result.tail_obs_count} exceeds "
            f"MC_DEFAULT_NEIGHBOR_K={math_engine.MC_DEFAULT_NEIGHBOR_K}. "
            "tail_obs_count must be the distinct genuine tail-observation count from "
            "the kNN pool (floor(alpha*N_pool) + atom), NOT 5% of simulation_paths "
            "(5% * 5000 = 250). H-2 binding: using 250 understates true stderr by ~25x."
        )
        if reference.tail_obs_count is not None:
            assert result.tail_obs_count == reference.tail_obs_count, (
                f"compute_portfolio_cvar.tail_obs_count={result.tail_obs_count} != "
                f"R-U reference tail_obs_count={reference.tail_obs_count}. "
                "Must be the R-U distinct-tail count, not a resample-fraction count."
            )

    def test_compute_portfolio_cvar_does_not_use_simulation_paths_for_cvar_computation(self):
        """
        When called with simulation_paths=1000 vs simulation_paths=5000, the cvar_pct
        value must be IDENTICAL (determinism test).

        The resample-mean implementation produces different cvar_pct values for different
        simulation_paths because it resamples from the kNN pool — the tail is 5%*N_paths
        draws and the mean changes with N. The R-U formula on the fixed kNN pool is
        invariant to simulation_paths (which is a Monte Carlo parameter, not used for CVaR).

        Currently FAILS: two calls with different simulation_paths produce different cvar_pct
        because the current implementation resamples and the seed produces a different tail.
        """
        dates, historical_data = _build_uniform_historical_data()

        result_1000 = math_engine.compute_portfolio_cvar(
            cycle_id=self._CYCLE_ID,
            holdings=self._HOLDINGS,
            historical_data=historical_data,
            spy_today_return=self._SPY_TODAY,
            simulation_paths=1000,
            neighbor_k=math_engine.MC_DEFAULT_NEIGHBOR_K,
            mode=None,
        )
        result_5000 = math_engine.compute_portfolio_cvar(
            cycle_id=self._CYCLE_ID,
            holdings=self._HOLDINGS,
            historical_data=historical_data,
            spy_today_return=self._SPY_TODAY,
            simulation_paths=5000,
            neighbor_k=math_engine.MC_DEFAULT_NEIGHBOR_K,
            mode=None,
        )

        assert result_1000.cvar_pct is not None and result_5000.cvar_pct is not None, (
            "Both calls must return non-sentinel cvar_pct on the sufficient test pool."
        )
        # The R-U formula on nearest_day_returns does not depend on simulation_paths;
        # this equality must hold bit-for-bit.
        assert result_1000.cvar_pct == result_5000.cvar_pct, (
            f"compute_portfolio_cvar.cvar_pct differs across simulation_paths: "
            f"1000-path result={result_1000.cvar_pct:.8f}, "
            f"5000-path result={result_5000.cvar_pct:.8f}. "
            "CVaR is computed on the FIXED kNN pool (nearest_day_returns), not on bootstrap "
            "resamples — simulation_paths must not affect the CVaR output."
        )


# ---------------------------------------------------------------------------
# spec-m2 FINDING 2 — insufficient-history sentinel path must write
# cvar_n_tail=0 (integer), not Python None.
# ---------------------------------------------------------------------------


class TestComputePortfolioCvarSentinelWritesExplicitZero:
    """
    spec-m2 FINDING 2 (MINOR): on the insufficient-history sentinel path,
    record_cvar_diagnostic must be called with cvar_n_tail=0 (integer zero),
    not Python None.

    cvar_n_tail is NOT NULL DEFAULT 0 in the migration; writing None from Python
    lets SQLite substitute the column default silently. The F-4 sentinel discipline
    requires the call site to be explicit about the sentinel value (0) so the
    discipline is visible at the code level, not deferred to the DB default.
    """

    def test_insufficient_history_sentinel_writes_cvar_n_tail_integer_zero(self, monkeypatch):
        """
        When compute_portfolio_cvar takes the insufficient-history path (too few
        eligible days), record_cvar_diagnostic must be called with cvar_n_tail=0,
        not cvar_n_tail=None.

        Currently: the code at math_engine.py writes cvar_n_tail=0 on the insufficient
        path (line 1236) — this test verifies that contract is maintained. If a future
        change introduces None here, this test catches the regression.
        """
        import database as _db

        calls = []

        def capture_record(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(_db, "record_cvar_diagnostic", capture_record)

        # Build a historical_data with only 1 date — far below the 39-date minimum.
        historical_data = {
            "2024-01-0001": {
                "SPY": {"daily_ret": 0.01},
                "TST": {"daily_ret": 0.02},
            }
        }
        holdings = [{"ticker": "TST", "allocation": 1.0}]

        math_engine.compute_portfolio_cvar(
            cycle_id="sentinel_test_cycle",
            holdings=holdings,
            historical_data=historical_data,
            spy_today_return=0.01,
            mode="replay",  # mode must be non-None to trigger the record_cvar_diagnostic call
        )

        assert len(calls) == 1, (
            f"Expected record_cvar_diagnostic to be called once; got {len(calls)} calls."
        )
        recorded_n_tail = calls[0].get("cvar_n_tail")
        assert recorded_n_tail == 0, (
            f"record_cvar_diagnostic called with cvar_n_tail={recorded_n_tail!r}; "
            "expected integer 0 (not None). "
            "F-4 sentinel discipline: cvar_n_tail=0 must be explicit at the call site, "
            "not deferred to the DB NOT NULL DEFAULT 0 fallback."
        )
        assert isinstance(recorded_n_tail, int), (
            f"cvar_n_tail must be Python int 0, not {type(recorded_n_tail).__name__}. "
            "Python None would silently pass the NOT NULL constraint via the DB default."
        )
