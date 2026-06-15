"""
Cycle C1 — RED: Claude client + prompt assembly for ``ai_advisor.py``.

Tier 1 — fixture-based unit tests. No network. Runs every CI pass.

The module under test (``ai_advisor.py``, repo root) does NOT exist yet —
every test here is RED until the C1 implementer cycle creates it. The binding
contract these tests pin:

    class ConfigSuggestion(BaseModel):
        config_key: str
        current_value: float | int | str
        suggested_value: float | int | str
        rationale: str
        risk_direction: str        # "loosens" | "tightens" | "neutral"
        confidence: str
        data_sufficiency: str

    class ConfigSuggestionsResponse(BaseModel):
        suggestions: list[ConfigSuggestion]

    def assemble_advisor_context(scope, symphony_id=None) -> dict
    def request_suggestions(context) -> tuple[ConfigSuggestionsResponse | None,
                                               str | None]

Scope boundary (C1 only):
  * context assembly + the Claude call + parsing the response.
  * the SAFETY GATES (allowlist *enforcement* on suggestions, risk-direction
    cross-check, OOS re-validation) are a SEPARATE cycle (C2).
  * BUT: the *context-assembly* allowlist (no credential ever REACHES Claude)
    IS in C1 scope — that is an input-governance concern, real-money-critical,
    and the research (config-surface.md §2, §4) is explicit that secrets must
    never enter the prompt. Test 2 below enforces it adversarially.

The 8 must-have prompt elements (prompt-methodology.md §1.1) the assembled
context MUST carry — pinned individually in Test 1:
  1. Per-param definition + directionality + risk polarity.
  2. Valid range of every param (the Optuna suggest_* bounds).
  3. The Optuna OOS-vs-train delta (train_alpha, oos_alpha, the gap,
     fallback_oos_alpha, default_oos_alpha, baseline_decision).
  4. Current live value of each param.
  5. locked_vars.
  6. Volatility regime context (current vol/ATR vs the 125-day window range).
  7. The data window + its limits (125 trading days, 80/20 WFA, synthetic
     replay, regimes covered / not covered).
  8. Risk invariants as hard constraints (monotonic ratchet, one-way breakeven
     latch, exit-confirm tick gates).
  (+ element 9 in the doc — task framing / role — also asserted; the doc
   numbers it 9 but lists 8 "must-haves" + framing. We pin role framing too.)

Mocking strategy
----------------
* ``database.get_latest_autotune_run``      -> patched (P1 dependency)
* ``symphony_logic.get_condensed_logic``    -> patched (P2 dependency)
* the ``anthropic`` SDK client              -> patched at the ai_advisor module
                                               boundary; NEVER a real network
                                               call from any Tier 1 test.
The math engine is NEVER mocked (we don't touch it here); only network + the
two cross-module data accessors are mocked.

No module-level mutable state — every fixture is function-scoped.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Network hermeticity — module-scoped autouse.
#
# assemble_advisor_context calls the three live-network section producers
# (_build_sentiment_section, _build_macro_section, _build_fundamentals_section)
# which in turn call _fetch_with_backoff -> requests.get against live GDELT,
# FRED, and SEC EDGAR endpoints.  Unit tests in this file must never touch the
# network, for two reasons:
#   1. Fixture-hygiene: a unit test that hits live endpoints is not a unit test.
#   2. Safety: persistent 429s from those endpoints trigger the very backoff
#      loop this cycle fixes — a network-dependent unit test could itself OOM.
#
# The autouse fixture requests ``stub_network_producers`` from conftest.py,
# which patches the three producers for the duration of every test in this
# module.  The function scope (default) means each test gets a fresh patch
# activation — no state leaks across tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _block_network_producers(stub_network_producers):  # noqa: PT004
    """Apply the network-producer stubs to every test in this module.

    Delegating to ``stub_network_producers`` (conftest.py) keeps the patch
    list in one place.  Tests in other files (test_cycle2_lens_producers.py,
    test_backoff_termination.py) do not receive this fixture and can exercise
    the real producer bodies.
    """


# ---------------------------------------------------------------------------
# Fixtures — all function-scoped, no shared mutable state.
# ---------------------------------------------------------------------------


@pytest.fixture
def ai_advisor_fixtures_dir() -> pathlib.Path:
    """Absolute path to tests/fixtures/ai_advisor."""
    return pathlib.Path(__file__).parents[1] / "fixtures" / "ai_advisor"


@pytest.fixture
def sample_response_payload(ai_advisor_fixtures_dir) -> dict:
    """The schema-derived ConfigSuggestionsResponse fixture, as a dict.

    Provenance: schema-derived (see the fixture file header). Tier 2's live
    contract test is its runtime validator. Tier 1 asserts shape/schema
    conformance against it — never the specific literal values inside.
    """
    path = ai_advisor_fixtures_dir / "sample_claude_response.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Strip provenance/doc keys — they are documentation, not schema fields.
    return {"suggestions": payload["suggestions"]}


@pytest.fixture
def fake_autotune_run() -> dict:
    """Stand-in for database.get_latest_autotune_run(symphony_id).

    Mirrors the real P1 accessor's return shape (the seven metric keys).
    Values are arbitrary sentinels chosen to be individually recognizable
    when grepping the assembled context — they are NOT producer-computed
    quant values, they are test sentinels, so pinning their *presence* in
    the context is shape verification, not value hardcoding.
    """
    return {
        "run_timestamp": "2026-05-11T16:00:00",
        "symphony_id": "test-symphony-a",
        "oos_alpha": 1.5,
        "train_alpha": 3.0,
        "baseline_decision": "Adopted AI",
        "fallback_oos_alpha": 0.8,
        "default_oos_alpha": 0.2,
    }


@pytest.fixture
def fake_condensed_logic() -> dict:
    """Stand-in for symphony_logic.get_condensed_logic(symphony_id).

    Mirrors the real P2 condense_symphony_logic() return shape.
    """
    return {
        "name": "Test Symphony A",
        "asset_universe": ["AAPL", "MSFT", "NVDA"],
        "indicators_used": {"relative-strength-index": 2, "moving-average-price": 1},
        "tree_depth": 4,
        "tree_breadth": 3,
        "total_nodes": 11,
        "group_labels": ["risk-on", "risk-off"],
        "rebalance": "daily",
    }


@pytest.fixture
def symphony_context(fake_autotune_run, fake_condensed_logic):
    """Assemble a symphony-scope context with P1/P2 dependencies mocked.

    This is the central fixture: most Tier 1 tests operate on the dict
    ai_advisor.assemble_advisor_context returns. Patches are scoped to the
    fixture body so nothing leaks across tests.
    """
    import ai_advisor  # lazy import — module does not exist until GREEN

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ),
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ),
    ):
        ctx = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")
    return ctx


def _serialize(ctx) -> str:
    """Deep-serialize a context dict to a single searchable string.

    Used by adversarial grep-style assertions — flattens nested dicts/lists
    so a credential buried three levels deep is still caught.
    """
    return json.dumps(ctx, default=str).lower()


# ===========================================================================
# Test 1 — assemble_advisor_context completeness: all 8 must-have elements.
# Each element pinned with its OWN assertion so a partial implementation
# fails on the specific missing element, not a vague "something's missing".
# ===========================================================================


def test_assembled_context_is_a_dict(symphony_context):
    """assemble_advisor_context returns a dict (the prompt-context blob)."""
    assert isinstance(symphony_context, dict)
    assert symphony_context, "assembled context must not be empty"


def test_context_element_1_param_definitions_with_risk_polarity(symphony_context):
    """Element 1: every suggestible param has a definition AND a stated risk
    polarity (raise = loosens / tightens). Without polarity Claude cannot
    reason about whether a suggestion is safe (prompt-methodology.md §1.1)."""
    blob = _serialize(symphony_context)
    # The polarity vocabulary must appear — Claude is told, per param, whether
    # raising it loosens or tightens risk.
    assert "loosen" in blob or "tighten" in blob, (
        "context must state per-param risk polarity (loosens/tightens) — "
        "element 1 of the 8 must-have prompt elements"
    )
    # MAX_PARABOLIC_SQUEEZE is the canonical 'tightens below 1.0' example the
    # research calls out by name; its definition must be carried.
    assert "max_parabolic_squeeze" in blob, (
        "per-param definitions must cover every suggestible param, including MAX_PARABOLIC_SQUEEZE"
    )


def test_context_element_2_valid_range_of_every_param(symphony_context):
    """Element 2: the Optuna suggest_* hard min/max for every tunable param.
    This is the single highest-leverage anti-hallucination element."""
    import autotuner

    blob = _serialize(symphony_context)
    for key in autotuner.OPTUNA_SEARCH_SPACE_KEYS:
        assert key.lower() in blob, (
            f"valid-range context must enumerate every Optuna search-space key; {key} is missing"
        )
    # The range bounds must be present as structured data somewhere — assert
    # the context exposes min/max bounds (shape, not specific numbers).
    found_bounds = _find_range_bounds(symphony_context)
    assert found_bounds, (
        "context must carry hard min/max valid ranges for params — "
        "element 2; no low/high (or min/max) bound structure found"
    )


def _find_range_bounds(obj) -> bool:
    """Recursively look for a range-bound structure (low/high or min/max)."""
    if isinstance(obj, dict):
        keys = {k.lower() for k in obj.keys()}
        if ("low" in keys and "high" in keys) or ("min" in keys and "max" in keys):
            return True
        return any(_find_range_bounds(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_find_range_bounds(v) for v in obj)
    return False


def test_context_element_3_optuna_oos_vs_train_delta(symphony_context, fake_autotune_run):
    """Element 3: train_alpha, oos_alpha, fallback_oos_alpha,
    default_oos_alpha, baseline_decision — the overfitting signal. These come
    from the P1 accessor; assert each is carried into the context."""
    blob = _serialize(symphony_context)
    # baseline_decision string must be present — Claude needs it to decide
    # whether to defer to or supplement Optuna.
    assert fake_autotune_run["baseline_decision"].lower() in blob, (
        "context must carry baseline_decision from the autotune run — element 3"
    )
    # The four alpha metrics must each be present (by value, derived from the
    # mocked accessor — NOT hardcoded literals; they come from the fixture).
    for metric_key in (
        "oos_alpha",
        "train_alpha",
        "fallback_oos_alpha",
        "default_oos_alpha",
    ):
        assert str(fake_autotune_run[metric_key]) in blob, (
            f"context must carry {metric_key} from the autotune run — element 3"
        )


def test_context_element_4_current_live_value_of_each_param(symphony_context):
    """Element 4: the current live value of each param — the anchor every
    suggestion is a diff against. Assert the suggestible surface carries
    a current/live value per param."""
    found = _find_current_values(symphony_context)
    assert found, (
        "context must carry the current live value of each param — element 4; "
        "no current/live value structure found in the suggestible surface"
    )


def _find_current_values(obj) -> bool:
    """Recursively look for a 'current value' field on the param surface."""
    if isinstance(obj, dict):
        keys = {k.lower() for k in obj.keys()}
        if any("current" in k or "live" in k for k in keys):
            return True
        return any(_find_current_values(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_find_current_values(v) for v in obj)
    return False


def test_context_element_5_locked_vars(symphony_context):
    """Element 5: locked_vars must be carried so Claude knows which params it
    may comment on but must not emit a suggested value for."""
    blob = _serialize(symphony_context)
    assert "locked_vars" in blob or "locked" in blob, "context must carry locked_vars — element 5"


def test_context_element_6_volatility_regime_context(symphony_context):
    """Element 6: current 20-day vol and 14-day ATR% vs their 125-day window
    range. 'Are we in a high-vol regime the tuning window under-samples?'"""
    blob = _serialize(symphony_context)
    assert "vol" in blob, "context must carry volatility regime context (20-day vol) — element 6"
    assert "atr" in blob, "context must carry 14-day ATR% regime context — element 6"


def test_context_element_7_data_window_and_limits(symphony_context):
    """Element 7: the data window stated explicitly — 125 trading days,
    80/20 walk-forward, synthetic/replay history, regimes covered/not."""
    blob = _serialize(symphony_context)
    assert "125" in blob, "context must state the 125-trading-day tuning window — element 7"
    # The walk-forward / synthetic-replay nature must be stated so Claude can
    # invoke the 'insufficient data' escape hatch.
    assert "walk" in blob or "80/20" in blob or "wfa" in blob, (
        "context must state the 80/20 walk-forward methodology — element 7"
    )
    assert "synthetic" in blob or "replay" in blob, (
        "context must state the history is synthetic/replay — element 7"
    )
    assert "regime" in blob, (
        "context must state which market regimes are / are not in the window — element 7"
    )


def test_context_element_8_risk_invariants(symphony_context):
    """Element 8: the non-negotiable risk invariants — monotonic trailing-stop
    ratchet, one-way breakeven latch, exit-confirmation tick gates."""
    blob = _serialize(symphony_context)
    assert "ratchet" in blob or "monotonic" in blob, (
        "context must enumerate the trailing-stop ratchet invariant — element 8"
    )
    assert "breakeven" in blob, (
        "context must enumerate the one-way breakeven latch invariant — element 8"
    )
    assert "invariant" in blob, (
        "context must explicitly frame these as hard risk invariants — element 8"
    )


def test_context_element_9_role_and_task_framing(symphony_context):
    """Element 9 (task framing / role): Claude is assisting a human operator
    who reviews every suggestion; it proposes hypotheses, it does not tune the
    system; it should prefer fewer well-justified suggestions."""
    blob = _serialize(symphony_context)
    assert "operator" in blob, "context must carry the operator-assist role framing — element 9"
    # The 'hypothesis not validated result / human reviews every suggestion'
    # framing must be present.
    assert "suggest" in blob or "hypothes" in blob or "propose" in blob, (
        "context must frame Claude's output as suggestions/hypotheses for a "
        "human to review — element 9"
    )


def test_context_carries_all_eight_elements_in_one_pass(symphony_context):
    """Belt-and-braces: a single test that fails if ANY element is absent,
    so a regression that drops one element is caught even if the per-element
    test above is skipped/xfailed. Lists exactly which elements are missing."""
    blob = _serialize(symphony_context)
    checks = {
        "1: param risk polarity": ("loosen" in blob or "tighten" in blob),
        "2: valid ranges": _find_range_bounds(symphony_context),
        "3: optuna oos/train": ("baseline" in blob and "oos_alpha" in blob),
        "4: current live values": _find_current_values(symphony_context),
        "5: locked_vars": ("locked" in blob),
        "6: vol regime": ("vol" in blob and "atr" in blob),
        "7: data window": ("125" in blob and "regime" in blob),
        "8: risk invariants": ("invariant" in blob and "breakeven" in blob),
        "9: role framing": ("operator" in blob),
    }
    missing = [name for name, present in checks.items() if not present]
    assert not missing, f"assembled context is missing must-have prompt elements: {missing}"


# ===========================================================================
# Test 2 — Allowlist enforcement IN THE CONTEXT (adversarial).
# Real-money critical: no credential / safety flag / account id / API key /
# methodology knob may appear ANYWHERE in the assembled context.
# Grep the deep-serialized context for credential-shaped strings.
# ===========================================================================

# Credential-shaped sentinels injected into the environment before assembly.
# If assemble_advisor_context reads os.environ broadly (instead of a curated
# allowlist), one of these leaks into the context and the test catches it.
_CRED_ENV = {
    "COMPOSER_KEY_ID": "COMPOSER_KEY_ID_LEAK_SENTINEL_xyz",
    "COMPOSER_SECRET": "COMPOSER_SECRET_LEAK_SENTINEL_xyz",
    "ALPACA_KEY": "ALPACA_KEY_LEAK_SENTINEL_xyz",
    "ALPACA_SECRET": "ALPACA_SECRET_LEAK_SENTINEL_xyz",
    "ACCOUNT_INDIVIDUAL": "ACCT-INDIV-UUID-LEAK-SENTINEL",
    "ACCOUNT_ROTH": "ACCT-ROTH-UUID-LEAK-SENTINEL",
    "ACCOUNT_TRAD": "ACCT-TRAD-UUID-LEAK-SENTINEL",
    "DISCORD_WEBHOOK_URL": "https://discord.test/LEAK_SENTINEL_xyz",
    "ANTHROPIC_API_KEY": "sk-ant-LEAK_SENTINEL_xyz",
    "LIVE_EXECUTION": "True",
    "EXECUTION_START_TIME": "09:30",
}

# Hard-exclusion config keys (config-surface.md §2) — neither the KEY NAME nor
# a value for it may surface in the context.
_FORBIDDEN_TOKENS = [
    "leak_sentinel",  # any injected credential value
    "sk-ant-",  # anthropic api key prefix
    "composer_secret",
    "composer_key_id",
    "alpaca_secret",
    "alpaca_key",
    "discord_webhook",
    "live_execution",
    "execution_start_time",
    "simulation_paths",  # methodology knob — never suggestible
    "neighbor_k",  # methodology knob — never suggestible
    "account_individual",
    "account_roth",
    "account_trad",
]


@pytest.fixture
def context_with_creds_in_env(monkeypatch, fake_autotune_run, fake_condensed_logic):
    """Assemble a context with credential-shaped values planted in os.environ.

    The adversarial setup: if assemble_advisor_context dumps the environment
    (or reads a denylist instead of an allowlist), these sentinels appear in
    the returned dict and Test 2 fails — exactly as it should.
    """
    import ai_advisor

    for key, val in _CRED_ENV.items():
        monkeypatch.setenv(key, val)

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ),
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ),
    ):
        ctx = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")
    return ctx


def test_no_credential_shaped_string_in_assembled_context(context_with_creds_in_env):
    """ADVERSARIAL: no injected credential sentinel value appears anywhere in
    the deep-serialized context. This is the real-money-critical input
    governance guarantee — secrets must never REACH Claude."""
    blob = _serialize(context_with_creds_in_env)
    for token in _FORBIDDEN_TOKENS:
        assert token not in blob, (
            f"FORBIDDEN token {token!r} found in assembled context — a "
            f"credential / safety flag / account id / methodology knob leaked "
            f"into Claude's input. assemble_advisor_context MUST read from a "
            f"curated allowlist, never dict(os.environ) or a raw .env dump."
        )


def test_assembled_context_only_contains_allowlisted_config_keys(symphony_context):
    """The config surface in the context is the 9-item allowlist (the 7 Optuna
    keys + MAX_SQUEEZE_FLOOR + locked_vars) — and NOTHING outside it. An
    allowlist, not a denylist: assert no out-of-allowlist config key leaks."""
    blob = _serialize(symphony_context)
    # Methodology knobs and the master safety flag must never appear even
    # without env injection — they must be structurally excluded.
    for forbidden_key in (
        "simulation_paths",
        "neighbor_k",
        "live_execution",
        "execution_start_time",
    ):
        assert forbidden_key not in blob, (
            f"{forbidden_key!r} is a hard-exclusion config value (never "
            f"AI-suggestible) and must not appear in the context — the "
            f"suggestible surface is a 9-item allowlist"
        )


def test_assemble_context_does_not_dump_full_environment(
    monkeypatch, fake_autotune_run, fake_condensed_logic
):
    """ADVERSARIAL: plant a uniquely-named junk env var and assert it never
    appears in the context. Proves assembly reads specific allowlisted keys,
    not the whole environment."""
    import ai_advisor

    monkeypatch.setenv("ALPHABOT_C1_ENV_LEAK_CANARY", "CANARY_VALUE_should_not_leak")

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ),
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ),
    ):
        ctx = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")

    blob = _serialize(ctx)
    assert "canary_value_should_not_leak" not in blob, (
        "a junk environment variable leaked into the assembled context — "
        "assemble_advisor_context must NOT iterate os.environ"
    )


# ===========================================================================
# Test 3 — Context pulls from P1 (database) + P2 (symphony_logic).
# Mock both accessors; assert their outputs are present in the context.
# ===========================================================================


def test_context_invokes_p1_autotune_accessor(fake_autotune_run, fake_condensed_logic):
    """assemble_advisor_context must call database.get_latest_autotune_run
    with the symphony_id it was given."""
    import ai_advisor

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ) as mock_p1,
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ),
    ):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")

    mock_p1.assert_called_once()
    # symphony_id must be threaded through to the accessor.
    called_args = mock_p1.call_args
    assert (
        "test-symphony-a" in called_args.args or "test-symphony-a" in called_args.kwargs.values()
    ), "get_latest_autotune_run must be called with the symphony_id"


def test_context_invokes_p2_condensed_logic_accessor(fake_autotune_run, fake_condensed_logic):
    """assemble_advisor_context must call symphony_logic.get_condensed_logic
    with the symphony_id it was given."""
    import ai_advisor

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ),
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ) as mock_p2,
    ):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")

    mock_p2.assert_called_once()
    called_args = mock_p2.call_args
    assert (
        "test-symphony-a" in called_args.args or "test-symphony-a" in called_args.kwargs.values()
    ), "get_condensed_logic must be called with the symphony_id"


def test_context_uses_composer_symphony_id_for_condensed_logic(
    fake_autotune_run, fake_condensed_logic
):
    """When composer_symphony_id is supplied, get_condensed_logic MUST be called
    with the Composer hash, NOT the normalized DB name.

    This pins the fix for the symphony-logic data-starvation bug: the route
    resolves hash→normalized-name for DB lookups, but Composer's /score endpoint
    requires the original hash.  Passing the normalized name returns HTTP 400 and
    an all-empty logic struct — the advisor receives no asset universe, no
    indicators, no tree shape.

    Regression contract: if this test fails, the Composer /score call will
    silently return an empty dict and Claude will have no composition context.
    """
    import ai_advisor

    hash_id = "n2ooAZTvBRN6ZzpMmWmU"
    normalized_name = "(invest:crypto) we do a little trolling planet's mix v1.4 | 10.19.2022"

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=fake_autotune_run,
        ) as mock_p1,
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ) as mock_p2,
    ):
        ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id=normalized_name,
            composer_symphony_id=hash_id,
        )

    mock_p2.assert_called_once()
    called_args = mock_p2.call_args
    first_arg = called_args.args[0] if called_args.args else called_args.kwargs.get("symphony_id")
    assert first_arg == hash_id, (
        f"get_condensed_logic called with {first_arg!r}; expected Composer hash {hash_id!r}. "
        "When composer_symphony_id is provided it MUST be used for the /score call — "
        "passing the normalized name produces Composer HTTP 400 and an empty logic struct."
    )
    # DB lookup (autotune) must still use the normalized name, not the hash.
    mock_p1.assert_called_once()
    db_call_args = mock_p1.call_args
    db_first_arg = (
        db_call_args.args[0] if db_call_args.args else db_call_args.kwargs.get("symphony_id")
    )
    assert db_first_arg == normalized_name, (
        f"get_latest_autotune_run called with {db_first_arg!r}; expected normalized name "
        f"{normalized_name!r}. The DB lookup key must not be replaced by the Composer hash."
    )


def test_context_embeds_p1_autotune_output(symphony_context, fake_autotune_run):
    """The P1 accessor's output (the autotune-run metrics) must be present in
    the assembled context — not just fetched and discarded."""
    blob = _serialize(symphony_context)
    assert fake_autotune_run["baseline_decision"].lower() in blob, (
        "the P1 autotune-run baseline_decision must be embedded in the context"
    )
    assert str(fake_autotune_run["oos_alpha"]) in blob, (
        "the P1 autotune-run oos_alpha must be embedded in the context"
    )


def test_context_embeds_p2_condensed_logic_output(symphony_context, fake_condensed_logic):
    """The P2 accessor's output (the condensed symphony logic) must be present
    in the assembled context — not just fetched and discarded."""
    blob = _serialize(symphony_context)
    # asset_universe tickers from the condensed logic must appear.
    for ticker in fake_condensed_logic["asset_universe"]:
        assert ticker.lower() in blob, (
            f"the P2 condensed-logic asset {ticker} must be embedded in the context"
        )


def test_context_handles_none_autotune_run_gracefully(fake_condensed_logic):
    """database.get_latest_autotune_run returns None when Optuna has not yet
    run for a symphony (its documented contract). assemble_advisor_context
    must still produce a well-shaped context — not raise, not omit the rest."""
    import ai_advisor

    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            return_value=None,
        ),
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            return_value=fake_condensed_logic,
        ),
    ):
        ctx = ai_advisor.assemble_advisor_context(
            scope="symphony", symphony_id="never-tuned-symphony"
        )

    assert isinstance(ctx, dict) and ctx, (
        "assemble_advisor_context must return a well-shaped dict even when "
        "get_latest_autotune_run returns None (Optuna has not run yet)"
    )


def test_symphony_scope_requires_symphony_id():
    """scope='symphony' without a symphony_id is a caller error — assemble
    must reject it rather than silently assembling a contextless prompt."""
    import ai_advisor

    with pytest.raises((ValueError, TypeError)):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=None)


# ===========================================================================
# Test 4 — request_suggestions happy path.
# Mock the anthropic SDK to return the schema-derived fixture; assert the
# response parses into the Pydantic schema with every field populated.
# ===========================================================================


def _make_fake_anthropic_client(parsed_obj=None, raise_exc=None):
    """Build a MagicMock standing in for anthropic.Anthropic().

    The research doc (claude-api-mechanics.md §3) shows the structured-output
    call as client.messages.parse(...). We model that surface. If GREEN uses
    a different SDK method, this helper is the one place to adjust — and the
    Tier 2 live test is what authoritatively pins the real SDK surface.

    Returns a real-shape ParsedMessage (same type the live SDK returns) so
    these Tier-1 tests are compatible with test_sdk_contract.py's adversarial
    contract. The validated Pydantic instance sits on
    content[0].parsed_output — NOT on a top-level .parsed field (which does
    not exist on ParsedMessage in anthropic 0.85.0).

    parsed_obj: ConfigSuggestionsResponse placed in content[0].parsed_output.
    raise_exc:  if set, client.messages.parse raises it instead.
    """
    from anthropic.types.parsed_message import ParsedMessage, ParsedTextBlock
    from anthropic.types.usage import Usage

    client = MagicMock(name="fake_anthropic_client")
    if raise_exc is not None:
        client.messages.parse.side_effect = raise_exc
    else:
        block = ParsedTextBlock.model_construct(
            type="text",
            text='{"suggestions": []}',
            parsed_output=parsed_obj,
            citations=None,
        )
        sdk_response = ParsedMessage.model_construct(
            id="msg_fake",
            type="message",
            role="assistant",
            model="claude-opus-4-7",
            content=[block],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )
        client.messages.parse.return_value = sdk_response
    return client


def test_request_suggestions_happy_path_parses_into_schema(
    symphony_context, sample_response_payload
):
    """request_suggestions returns (ConfigSuggestionsResponse, None) on success
    and the response validates against the Pydantic schema."""
    import ai_advisor

    parsed = ai_advisor.ConfigSuggestionsResponse(**sample_response_payload)
    fake_client = _make_fake_anthropic_client(parsed_obj=parsed)

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        response, error = ai_advisor.request_suggestions(symphony_context)

    assert error is None, f"happy path must return None error; got {error!r}"
    assert isinstance(response, ai_advisor.ConfigSuggestionsResponse), (
        "happy path must return a ConfigSuggestionsResponse instance"
    )


def test_request_suggestions_happy_path_all_fields_populated(
    symphony_context, sample_response_payload
):
    """Every ConfigSuggestion field is populated and well-typed on the happy
    path — pin the schema contract, not the specific values."""
    import ai_advisor

    parsed = ai_advisor.ConfigSuggestionsResponse(**sample_response_payload)
    fake_client = _make_fake_anthropic_client(parsed_obj=parsed)

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        response, error = ai_advisor.request_suggestions(symphony_context)

    assert error is None
    assert len(response.suggestions) >= 1, "the schema-derived fixture carries 2-3 suggestions"
    for s in response.suggestions:
        assert isinstance(s.config_key, str) and s.config_key, (
            "config_key must be a non-empty string"
        )
        # current_value / suggested_value are float | int | str per the
        # contract — assert the type union, never a specific value.
        assert isinstance(s.current_value, (float, int, str)), (
            "current_value must be float | int | str"
        )
        assert isinstance(s.suggested_value, (float, int, str)), (
            "suggested_value must be float | int | str"
        )
        assert isinstance(s.rationale, str) and s.rationale.strip(), (
            "rationale must be a non-empty string"
        )
        assert s.risk_direction in {"loosens", "tightens", "neutral"}, (
            f"risk_direction must be one of loosens/tightens/neutral; got {s.risk_direction!r}"
        )
        assert isinstance(s.confidence, str) and s.confidence, (
            "confidence must be a non-empty string"
        )
        assert isinstance(s.data_sufficiency, str) and s.data_sufficiency, (
            "data_sufficiency must be a non-empty string"
        )


def test_request_suggestions_fixture_covers_all_risk_directions(sample_response_payload):
    """Guards the fixture itself: the schema-derived fixture must exercise all
    three risk_direction values so downstream (C2) risk-direction handling has
    a complete sample. A wrong fixture would silently weaken every test."""
    import ai_advisor

    parsed = ai_advisor.ConfigSuggestionsResponse(**sample_response_payload)
    seen = {s.risk_direction for s in parsed.suggestions}
    assert seen == {"loosens", "tightens", "neutral"}, (
        f"schema-derived fixture must cover all three risk_direction values; saw {seen}"
    )


# ===========================================================================
# Test 5 — request_suggestions error paths.
# Mock the SDK to raise rate-limit / timeout / API-error; assert
# request_suggestions returns (None, error_message) and NEVER raises.
# ===========================================================================


def _anthropic_exc_classes():
    """Yield (label, exception_instance) for the SDK failure modes.

    Falls back to generic Exception subclasses if a given anthropic exception
    class is not importable in the installed SDK version — the contract under
    test is 'no SDK exception escapes request_suggestions', regardless of the
    exact class.
    """
    cases = []
    try:
        import anthropic

        # RateLimitError / APITimeoutError / APIStatusError / APIError exist
        # across modern anthropic SDK versions; guard each individually.
        for name in (
            "RateLimitError",
            "APITimeoutError",
            "APIConnectionError",
            "APIStatusError",
            "APIError",
        ):
            exc_cls = getattr(anthropic, name, None)
            if exc_cls is not None:
                cases.append((name, exc_cls))
    except ImportError:  # pragma: no cover - SDK is a declared dependency
        pass

    if not cases:  # pragma: no cover - safety net
        cases = [("RuntimeError", RuntimeError)]
    return cases


@pytest.mark.parametrize(
    "exc_label,exc_cls",
    _anthropic_exc_classes(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_request_suggestions_degrades_gracefully_on_sdk_error(symphony_context, exc_label, exc_cls):
    """Every anthropic SDK failure mode degrades to (None, error_message) —
    request_suggestions must NEVER raise. The feature is on-demand operator-
    assist; a failure is 'no suggestion this click', zero engine impact."""
    import ai_advisor

    # Construct the exception as cheaply as possible — different anthropic
    # exception classes have different __init__ signatures across versions.
    try:
        exc_instance = exc_cls("simulated SDK failure")
    except TypeError:
        exc_instance = exc_cls.__new__(exc_cls)

    fake_client = _make_fake_anthropic_client(raise_exc=exc_instance)

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        try:
            response, error = ai_advisor.request_suggestions(symphony_context)
        except Exception as exc:  # noqa: BLE001 - the whole point is to catch
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} on a "
                f"{exc_label} SDK failure — it MUST degrade gracefully to "
                f"(None, error_message), never raise."
            )

    assert response is None, f"on a {exc_label} SDK failure, response must be None"
    assert isinstance(error, str) and error.strip(), (
        f"on a {exc_label} SDK failure, error must be a non-empty string the "
        f"UI can show the operator"
    )


def test_request_suggestions_handles_client_construction_failure(symphony_context):
    """If the SDK client itself can't be built (missing/invalid API key),
    request_suggestions still degrades gracefully — does not raise."""
    import ai_advisor

    with patch.object(
        ai_advisor, "_build_client", side_effect=RuntimeError("no ANTHROPIC_API_KEY"), create=True
    ):
        try:
            response, error = ai_advisor.request_suggestions(symphony_context)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} when the "
                f"client could not be constructed — it must degrade gracefully."
            )

    assert response is None
    assert isinstance(error, str) and error.strip()


# ===========================================================================
# Test 6 — Malformed response.
# Mock the SDK to return something that does NOT conform to the schema;
# assert graceful (None, error).
# ===========================================================================


def test_request_suggestions_malformed_response_missing_required_field(symphony_context):
    """SDK returns a suggestion missing a required field — request_suggestions
    must NOT raise; it must return (None, error_message)."""
    import ai_advisor

    # A dict that is NOT a valid ConfigSuggestionsResponse — suggestion is
    # missing 'rationale', 'risk_direction', etc.
    malformed = MagicMock(name="malformed_sdk_response")
    malformed.parsed = None  # parse produced nothing usable
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = malformed

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        try:
            response, error = ai_advisor.request_suggestions(symphony_context)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} on a "
                f"malformed response — it must return (None, error_message)."
            )

    assert response is None, "a malformed/unparseable SDK response must yield response=None"
    assert isinstance(error, str) and error.strip(), (
        "a malformed response must yield a non-empty error message"
    )


def test_request_suggestions_malformed_response_wrong_shape(symphony_context):
    """SDK returns a structurally wrong object (e.g. a bare string where a
    ConfigSuggestionsResponse was expected) — graceful (None, error)."""
    import ai_advisor

    bad_response = MagicMock(name="wrong_shape_response")
    bad_response.parsed = "this is not a ConfigSuggestionsResponse"
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = bad_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        try:
            response, error = ai_advisor.request_suggestions(symphony_context)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} on a "
                f"wrong-shape response — it must degrade gracefully."
            )

    assert response is None
    assert isinstance(error, str) and error.strip()


def test_malformed_payload_fails_pydantic_validation():
    """Sanity check on the schema itself: a payload missing required fields
    must fail ConfigSuggestionsResponse validation. This proves the schema is
    actually enforcing the contract (not all-Optional)."""
    import pydantic

    import ai_advisor

    incomplete = {
        "suggestions": [
            {"config_key": "MAX_SQUEEZE_FLOOR"}  # missing every other field
        ]
    }
    with pytest.raises(pydantic.ValidationError):
        ai_advisor.ConfigSuggestionsResponse(**incomplete)


# ===========================================================================
# Test 7 — Empty suggestions.
# Claude can validly return zero suggestions ("config looks sound").
# Assert that is a valid NON-ERROR response.
# ===========================================================================


def test_request_suggestions_empty_list_is_valid_non_error(symphony_context):
    """An empty suggestions list is a valid, encouraged answer (the 'config
    looks sound' / abstention escape hatch). It must flow through as
    (ConfigSuggestionsResponse with [] suggestions, None) — NOT as an error."""
    import ai_advisor

    empty = ai_advisor.ConfigSuggestionsResponse(suggestions=[])
    fake_client = _make_fake_anthropic_client(parsed_obj=empty)

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        response, error = ai_advisor.request_suggestions(symphony_context)

    assert error is None, (
        "an empty suggestions list is NOT an error — it is the abstention "
        "escape hatch ('config looks sound'); error must be None"
    )
    assert isinstance(response, ai_advisor.ConfigSuggestionsResponse), (
        "an empty-suggestions response must still be a ConfigSuggestionsResponse"
    )
    assert response.suggestions == [], (
        "the empty suggestions list must be preserved, not coerced to None or dropped"
    )


def test_empty_suggestions_response_validates_against_schema():
    """The schema must ACCEPT an empty suggestions list — if the schema
    required >=1 suggestion, the abstention escape hatch would be impossible."""
    import ai_advisor

    obj = ai_advisor.ConfigSuggestionsResponse(suggestions=[])
    assert obj.suggestions == []
