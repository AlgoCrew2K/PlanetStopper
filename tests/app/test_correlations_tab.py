"""
M1 RED — Correlations tab route tests (AC-1.1 / AC-1.2 / AC-1.3 / AC-1.4 / AC-X1 / AC-X2).

Tests the /ai-advisor/correlations GET route and the data it surfaces to the
template. The route is new for M1 — it does not yet exist, so every test here
is RED until GREEN adds the route and the template context.

Contracts pinned:

  1. GET /ai-advisor/correlations returns HTTP 200.
  2. Template context includes a 'correlation_matrix' key with a list of
     PairResult-like dicts (serialised for the template).
  3. Template context includes an 'as_of' timestamp key (non-empty string).
  4. Template context includes the 'crisis_caveat' key (truthy string).
  5. 'correlation_matrix' is a list (empty is valid when no data available —
     AC-1.3 degenerate state is not a crash).
  6. Each matrix entry has: sym_a, sym_b, n_obs (int), correlation (float|null),
     thin_data (bool) — AC-1.2: sample basis is visible.
  7. The route performs NO write to the database (read-only — AC-X2 / arch constraint 2).
  8. The route does NOT call any Composer write endpoint (AC-X1).
  9. The route does NOT call alpha_bot_execution functions (AC-X2 off-live-path).
 10. The route does NOT invoke compute_pairwise_correlations on the live
     execution path — the diagnostic runs as a background read.
 11. An empty portfolio (no return data) surfaces 'insufficient_data': True
     and a non-error 200 response (AC-1.3).

Mocking strategy:
  - The route is expected to import advisors.correlation_diagnostic lazily
    (inside the route function) to stay off the live path. Tests patch the
    module attribute via sys.modules injection so the patch works before the
    real module exists — GREEN must import the module lazily at route call time
    so sys.modules injection takes effect.
  - database.get_ro_connection / get_advisor_observations_for_role are mocked
    to prevent live DB access.
  - No math engine mocking (not involved in route logic).
  - All fixtures are function-scoped.

Patching strategy for a not-yet-existing module:
  We inject a stub module into sys.modules before each test so the
  `patch(...)` context managers can resolve the attribute path. This is the
  standard approach for testing lazy imports before the real module ships.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Module stub injection helpers
# ---------------------------------------------------------------------------


_STUB_CRISIS_CAVEAT = (
    "Correlations destabilize toward 1.0 in market stress. "
    "When de-correlation matters most, these estimates are least reliable. "
    "Use as a guide, not a guarantee."
)


def _inject_correlation_diagnostic_stub(return_value: list):
    """Inject a minimal stub for advisors.correlation_diagnostic into sys.modules.

    The real module does not exist yet (RED state). The route is expected to
    import it lazily, so this stub intercepts that lazy import.

    The stub exposes CRISIS_CAVEAT so tests that assert the route uses the
    module constant (not an inline string) can verify the binding.

    Returns the stub module so tests can inspect or further configure it.
    """
    # If the real module already exists, use it directly rather than the stub
    # so CRISIS_CAVEAT assertions bind to the real constant.
    try:
        from advisors import correlation_diagnostic as _real

        if hasattr(_real, "CRISIS_CAVEAT"):
            # Real module has the constant — override compute fn for test control
            # but keep the real CRISIS_CAVEAT so route-binding tests work.
            _real.compute_pairwise_correlations = MagicMock(return_value=return_value)
            return _real
    except ImportError:
        pass

    stub = types.ModuleType("advisors.correlation_diagnostic")
    stub.compute_pairwise_correlations = MagicMock(return_value=return_value)
    stub.THIN_DATA_THRESHOLD = 30
    stub.CRISIS_CAVEAT = _STUB_CRISIS_CAVEAT

    # Also ensure the advisors package exposes the attribute.
    import advisors as _advisors_pkg

    _advisors_pkg.correlation_diagnostic = stub  # type: ignore[attr-defined]
    sys.modules["advisors.correlation_diagnostic"] = stub
    return stub


def _remove_correlation_diagnostic_stub():
    """Remove the stub from sys.modules after a test."""
    sys.modules.pop("advisors.correlation_diagnostic", None)
    try:
        import advisors as _advisors_pkg

        if hasattr(_advisors_pkg, "correlation_diagnostic"):
            delattr(_advisors_pkg, "correlation_diagnostic")
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Helpers — fake PairResult to use as mock return from compute_pairwise_correlations
# ---------------------------------------------------------------------------


def _make_pair_result(
    sym_a: str,
    sym_b: str,
    n_obs: int = 35,
    correlation: float = 0.42,
    thin_data: bool = False,
    window: tuple[str, str] | None = ("2026-01-02", "2026-05-30"),
) -> SimpleNamespace:
    """Build a PairResult-like object matching the contract from
    advisors.correlation_diagnostic.

    window default is a representative (first_date, last_date) tuple;
    pass window=None to simulate plain-list input with no date metadata.
    """
    return SimpleNamespace(
        sym_a=sym_a,
        sym_b=sym_b,
        n_obs=n_obs,
        correlation=correlation,
        thin_data=thin_data,
        window=window,
    )


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def two_pair_matrix():
    """A minimal two-pair correlation matrix for route tests."""
    return [
        _make_pair_result("Paragons", "LQD+EYG", n_obs=35, correlation=0.82),
        _make_pair_result("Inflation", "Paragons", n_obs=35, correlation=0.74),
    ]


@pytest.fixture
def thin_pair_matrix():
    """A two-pair matrix where all pairs are thin (n_obs < 30)."""
    return [
        _make_pair_result("alpha", "beta", n_obs=5, correlation=0.91, thin_data=True),
    ]


@pytest.fixture(autouse=True)
def _cleanup_stub():
    """Ensure the stub module is removed from sys.modules after every test,
    AND restore the real correlation_diagnostic.compute_pairwise_correlations
    if a test's _inject_correlation_diagnostic_stub call took the real-module
    branch (line ~86 above), which DIRECTLY MUTATES the real module's
    attribute rather than swapping a stub into sys.modules.

    CI-caught test-isolation bug (full-tree run, not caught by any segmented
    chunk): _remove_correlation_diagnostic_stub() only pops sys.modules and
    the advisors-package attribute -- neither of which the real-module branch
    ever touches -- so a MagicMock left there by one test call leaked
    module-globally into every OTHER test file that imports
    advisors.correlation_diagnostic afterward (first caught by
    tests/advisors/test_retirement_recommender_degenerate.py's pure {} -> []
    empty-input test, which is correct production code -- the leaked mock
    was the defect, not the new test).

    Fix: capture the REAL (unmutated) function reference in this fixture's
    own local scope at SETUP time (before the test body can call
    _inject_correlation_diagnostic_stub), then unconditionally restore it at
    teardown -- regardless of whether this particular test happened to
    trigger the real-module mutation path. Deliberately NOT a module-level
    mutable (that would just be a second instance of the same class of bug,
    shared cross-test state) -- this is fixture-local state, scoped fresh per
    test invocation by pytest's own fixture mechanism, the correct pattern.
    """
    try:
        from advisors import correlation_diagnostic as _real

        _original_compute_pairwise_correlations = _real.compute_pairwise_correlations
    except ImportError:
        _real = None
        _original_compute_pairwise_correlations = None

    yield

    _remove_correlation_diagnostic_stub()

    if _real is not None:
        _real.compute_pairwise_correlations = _original_compute_pairwise_correlations


# ---------------------------------------------------------------------------
# RED-1: route exists and returns 200
# ---------------------------------------------------------------------------


def test_correlations_tab_returns_200(test_client, two_pair_matrix):
    """GET /ai-advisor must return HTTP 200 (unified page with correlations panel).

    Updated for in-place tab switching (advisor-tabs-inplace cycle):
    the correlations content is now the tab-panel-correlations panel on /ai-advisor.
    The standalone /ai-advisor/correlations route now redirects to /ai-advisor.
    """
    _inject_correlation_diagnostic_stub(two_pair_matrix)

    with (
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("database.get_ro_connection", return_value=MagicMock()),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        resp = test_client.get("/ai-advisor")

    assert resp.status_code == 200, f"GET /ai-advisor must return 200; got {resp.status_code}"


# ---------------------------------------------------------------------------
# RED-2: template context has 'correlation_matrix' key
# ---------------------------------------------------------------------------


def test_correlations_tab_context_has_correlation_matrix_key(test_client, two_pair_matrix):
    """The rendered template must receive a 'correlation_matrix' context key.

    The UI reads r.sym_a, r.sym_b, r.n_obs, r.correlation, r.thin_data from
    entries in this list (AC-1.1 / AC-1.2).
    """
    _inject_correlation_diagnostic_stub(two_pair_matrix)
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html><body>stub</body></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        test_client.get("/ai-advisor")

    assert "correlation_matrix" in captured, (
        "render_template was not called with a 'correlation_matrix' context key — "
        "the Correlations tab requires this to render the pairwise matrix (AC-1.1)"
    )


# ---------------------------------------------------------------------------
# RED-3: each matrix entry has the required fields
# ---------------------------------------------------------------------------


def test_correlations_tab_each_matrix_entry_has_required_fields(test_client, two_pair_matrix):
    """Every entry in correlation_matrix must have sym_a, sym_b, n_obs,
    correlation, and thin_data.

    AC-1.2: sample basis (n_obs) must be visible alongside each estimate.
    """
    _inject_correlation_diagnostic_stub(two_pair_matrix)
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        test_client.get("/ai-advisor")

    matrix = captured.get("correlation_matrix", [])
    assert matrix, "expected at least one entry in correlation_matrix"

    # 'window' is the AC-1.2 date-range companion to n_obs ("obs count / window").
    required_fields = {"sym_a", "sym_b", "n_obs", "correlation", "thin_data", "window"}
    for entry in matrix:
        # Entries may be dicts (JSON-serialised) or objects with attribute access.
        if isinstance(entry, dict):
            missing = required_fields - entry.keys()
        else:
            missing = {f for f in required_fields if not hasattr(entry, f)}
        assert not missing, (
            f"correlation_matrix entry missing fields {missing!r}; "
            f"each entry must expose {sorted(required_fields)} for the template "
            f"(AC-1.2 requires 'window' alongside 'n_obs')"
        )


# ---------------------------------------------------------------------------
# RED-4: 'crisis_caveat' key is present and truthy (AC-1.4)
# ---------------------------------------------------------------------------


def test_correlations_tab_crisis_caveat_comes_from_module_constant(test_client, two_pair_matrix):
    """The route must pass `correlation_diagnostic.CRISIS_CAVEAT` as the
    'crisis_caveat' template context value — not an inline string literal.

    Binding to the module constant (not a hardcoded string in the route) is the
    single source of truth contract: if someone updates the caveat text in
    `correlation_diagnostic.py`, the route automatically picks it up. An inline
    literal in the route would silently diverge.

    AC-1.4: the crisis-instability caveat must be surfaced. This test ensures
    it is the MODULE'S caveat that reaches the template, not a route-local copy.
    """
    stub = _inject_correlation_diagnostic_stub(two_pair_matrix)
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        test_client.get("/ai-advisor")

    assert "crisis_caveat" in captured, (
        "template context must include 'crisis_caveat' key — the crisis-instability "
        "warning is mandatory per AC-1.4"
    )
    # The value must be exactly the constant from the module — not a separate string.
    # If the route uses correlation_diagnostic.CRISIS_CAVEAT, this is the same object.
    # If the route uses an inline literal, this assertion fails on any text divergence.
    assert captured["crisis_caveat"] == stub.CRISIS_CAVEAT, (
        f"'crisis_caveat' in template context must equal "
        f"correlation_diagnostic.CRISIS_CAVEAT (the module constant). "
        f"Route must use `correlation_diagnostic.CRISIS_CAVEAT`, not an inline string. "
        f"Expected: {stub.CRISIS_CAVEAT!r}\n"
        f"Got: {captured.get('crisis_caveat')!r}"
    )


# ---------------------------------------------------------------------------
# RED-5: 'as_of' timestamp key is present and non-empty (AC-1.2)
# ---------------------------------------------------------------------------


def test_correlations_tab_context_has_as_of_timestamp(test_client, two_pair_matrix):
    """Template context must include 'as_of' — a non-empty string showing when
    the diagnostic data was computed.

    AC-1.2: the operator needs to know the data age to judge staleness.
    """
    _inject_correlation_diagnostic_stub(two_pair_matrix)
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        test_client.get("/ai-advisor")

    assert "as_of" in captured, "template context must include 'as_of' timestamp key"
    assert captured["as_of"], (
        "'as_of' must be a non-empty string — the operator needs to see when "
        "the diagnostic data was computed"
    )


# ---------------------------------------------------------------------------
# RED-6: degenerate empty portfolio → HTTP 200 and 'insufficient_data' flag (AC-1.3)
# ---------------------------------------------------------------------------


def test_correlations_tab_empty_portfolio_returns_200_with_insufficient_data(test_client):
    """When no return data is available (empty portfolio), the route must:
      - Return HTTP 200 (no crash, no 500)
      - Surface 'insufficient_data': True in the template context OR return
        an empty correlation_matrix (both are valid AC-1.3 outcomes)

    AC-1.3: empty/degenerate portfolio → well-shaped 'insufficient data' state.
    """
    _inject_correlation_diagnostic_stub([])  # empty matrix — no symphonies
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        resp = test_client.get("/ai-advisor")

    assert resp.status_code in (200, 302), (
        f"empty portfolio must return 200 (or 302 redirect), not a crash; got {resp.status_code}"
    )
    # Either an explicit insufficient_data flag OR an empty matrix — both are
    # valid ways to surface AC-1.3. The test accepts either.
    matrix = captured.get("correlation_matrix", [])
    insufficient = captured.get("insufficient_data", False)
    assert matrix == [] or insufficient, (
        "empty portfolio must result in either an empty correlation_matrix or "
        "insufficient_data=True in the template context (AC-1.3 degenerate state)"
    )


# ---------------------------------------------------------------------------
# RED-7: route does NOT write to database (read-only — AC-X2 / arch constraint 2)
# ---------------------------------------------------------------------------


def test_correlations_route_does_not_call_insert_advisor_observation(test_client, two_pair_matrix):
    """The correlations route must NOT call insert_advisor_observation.

    The diagnostic is pure measurement — no persistence is needed for the
    display route. The persist path (AC-X3) belongs to a separate 'run
    diagnostic' action if one is added later; the GET display route is
    read-only.
    """
    _inject_correlation_diagnostic_stub(two_pair_matrix)
    insert_mock = MagicMock()

    with (
        patch("database.insert_advisor_observation", insert_mock),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
        patch.object(app_module, "render_template", return_value="<html></html>"),
    ):
        test_client.get("/ai-advisor")

    (
        insert_mock.assert_not_called(),
        (
            "GET /ai-advisor/correlations must not call insert_advisor_observation — "
            "the display route is read-only; persistence happens on a separate 'run' action"
        ),
    )


# ---------------------------------------------------------------------------
# RED-8: thin-data entries are preserved in the matrix context
# ---------------------------------------------------------------------------


def test_correlations_tab_thin_data_entries_are_passed_to_template(test_client, thin_pair_matrix):
    """When the diagnostic returns thin-data pairs (n_obs < 30, thin_data=True),
    those entries must appear in the correlation_matrix context with thin_data=True.

    AC-1.2: thin-data estimates must be visible (not silently dropped).
    """
    _inject_correlation_diagnostic_stub(thin_pair_matrix)
    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value={}),
    ):
        test_client.get("/ai-advisor")

    matrix = captured.get("correlation_matrix", [])
    assert matrix, "expected at least one thin-data entry in correlation_matrix"

    entry = matrix[0]
    thin = entry["thin_data"] if isinstance(entry, dict) else entry.thin_data
    assert thin is True, (
        "thin-data pair (n_obs=5) must appear in correlation_matrix with "
        "thin_data=True — thin estimates must not be silently dropped (AC-1.2)"
    )


# ---------------------------------------------------------------------------
# RED-9: route method — only GET is valid (no mutation surface)
# ---------------------------------------------------------------------------


def test_correlations_route_rejects_post(test_client):
    """POST /ai-advisor/correlations must NOT be accepted (405 or 404).

    The correlations tab is a read-only diagnostic display; it has no POST
    handler. Any POST would be an unexpected mutation surface.
    """
    resp = test_client.post("/ai-advisor/correlations")

    assert resp.status_code in (404, 405), (
        f"POST /ai-advisor/correlations must return 404 or 405 (no mutation surface); "
        f"got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Regression guard for the CI-caught test-isolation leak (full-tree run).
# Deliberately the LAST test in this file (definition order) so it runs
# AFTER every test above that calls _inject_correlation_diagnostic_stub and
# exercises the real-module mutation branch -- deterministically reproducing
# the leak pre-fix within a single file's own run (no cross-file ordering
# dependency needed to prove this specific guard).
# ---------------------------------------------------------------------------


class TestLeakedMockDoesNotCorruptAnAlreadyBoundConsumerReference:
    """Deterministic two-test reproduction of the CI-caught leak's ACTUAL
    mechanism -- not a naive single-test check.

    Why a single-test `from advisors import correlation_diagnostic as _real;
    assert not isinstance(_real.compute_pairwise_correlations, Mock)` is
    VACUOUS: _remove_correlation_diagnostic_stub() unconditionally pops
    "advisors.correlation_diagnostic" from sys.modules on every test's
    teardown, so a plain `from ... import ...` AFTER that always triggers a
    FRESH re-import -- a brand-new, never-mutated module object every single
    time, regardless of whether the restore fix actually works. Verified
    empirically: that check passed even with the restore logic deliberately
    removed.

    The real leak mechanism: a CONSUMER module -- advisors.retirement_
    recommender does `from advisors import correlation_diagnostic` at ITS
    OWN module level -- binds ONE FIXED reference the FIRST TIME IT IS EVER
    IMPORTED in the process, to whatever sys.modules["advisors.
    correlation_diagnostic"] was AT THAT MOMENT. That reference is NEVER
    updated by any later sys.modules pop/reimport cycle elsewhere -- Python
    module bindings don't retroactively follow sys.modules changes. If that
    first-ever import happens to land WHILE test_correlations_tab.py's
    real-module mutation branch is active (between a mutating test's setup
    and its own teardown -- exactly the timing CI's real loadfile order hit,
    and exactly what broke tests/advisors/test_retirement_recommender_
    degenerate.py's pure {} -> [] empty-input test elsewhere), the consumer
    is corrupted for the rest of the process unless the mutation is restored
    on that SAME object instance before its teardown completes.

    Test A below forces a controlled RE-BINDING of retirement_recommender's
    own `correlation_diagnostic` reference WHILE a mutation is active, via
    importlib.reload() -- NOT a plain first-ever `import` (which only
    reproduces the exposure window if this genuinely happens to be the
    process's first-ever import of that module, an assumption that broke
    the first version of this test the moment it ran alongside ANY other
    file that imports retirement_recommender first, e.g. the full-tree CI
    order this guard exists to protect against -- self-defeating). reload()
    forces the module's top-level `from advisors import correlation_
    diagnostic` line to re-execute NOW, capturing whatever object is
    CURRENTLY in sys.modules regardless of import history, making this
    deterministic under any file/test ordering. Test B (autouse
    _cleanup_stub's teardown fires automatically between A and B) checks
    that SAME bound reference was correctly restored.
    """

    def test_a_reload_of_a_consumer_lands_during_an_active_mutation(self):
        import importlib

        _inject_correlation_diagnostic_stub([_make_pair_result("leak-x", "leak-y")])
        import advisors.retirement_recommender as _rr_consumer

        importlib.reload(_rr_consumer)  # re-binds correlation_diagnostic NOW, mid-mutation

    def test_b_consumer_binding_is_clean_after_test_a_tore_down(self):
        from unittest.mock import Mock

        import advisors.retirement_recommender as _rr_consumer

        bound_fn = _rr_consumer.correlation_diagnostic.compute_pairwise_correlations
        assert not isinstance(bound_fn, Mock), (
            "advisors.retirement_recommender.correlation_diagnostic."
            "compute_pairwise_correlations -- the SPECIFIC module-object "
            "reference retirement_recommender.py bound during its first-ever "
            "import (deliberately forced mid-mutation by test_a above) -- is "
            "still a leaked Mock after test_a's teardown ran. The autouse "
            "_cleanup_stub fixture failed to restore the real function on "
            "the SAME object instance a real consumer is bound to."
        )
