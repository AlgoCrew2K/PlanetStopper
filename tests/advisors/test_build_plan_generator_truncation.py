"""RED tests — Component 2 DEFECT FIX: Opus generator max_tokens truncation.

Module under test: advisors.build_plan_generator.generate_build_plans.

THE DEFECT (empirically confirmed — .claude/c4-trunc-probe-result.json):
    generate_build_plans requests N_PLANS_PER_OBJECTIVE=12 full-grammar plans in a
    SINGLE `client.messages.create(..., max_tokens=4096, ...)` call. 4096 output
    tokens is too few for 12 full-grammar plans, so Opus returns
    stop_reason="max_tokens" with an incomplete/empty tool_block.input (the probe
    saw input_json_chars=2 == "{}"). The current code reads neither response.stop_reason
    nor response.usage, so it sees a non-list `plans` payload and returns
    GeneratorResult(plans=[], reason="InvalidToolUsePayload") — 0 plans for that
    objective. "Success" happens only by luck when Opus fits within 4096, so ~3 of 4
    objectives are hollow PER RUN, non-deterministically.

WHY THE EXISTING SUITE IS BLIND:
    Every existing mock (_client_returning_plans in test_build_plan_generator.py,
    _MockResponse in the prompt test, the property test) HARDCODES
    stop_reason="tool_use" with a COMPLETE payload. No existing test sets
    stop_reason="max_tokens"; no existing test asserts the max_tokens kwarg. These
    tests simulate the truncation path the existing tests cannot reach.

THE FIX THESE TESTS DEMAND (assert the WHAT, not the mechanism):
    GROUP A — max_tokens is a NAMED constant MAX_OUTPUT_TOKENS >> 4096 (no magic number).
    GROUP B — truncation (stop_reason=="max_tokens") is HANDLED via a BOUNDED retry
              (named MAX_GENERATION_ATTEMPTS); truncate-then-succeed yields plans;
              exhausted retries degrade per D-1 with an honest truncation reason;
              a non-truncated first response does NOT retry.
    GROUP C — frozen public signature; never-raises (D-1); advisory-only.

ADVERSARIAL / NON-HARDCODED: every assertion is on SHAPE / NAMED-CONSTANT / CALL-COUNT /
INEQUALITY / SUBSTRING-PRESENCE — never a hardcoded producer-computed value. The math
engine and the generator are NEVER mocked; only the SDK network seam (_build_client) and
its returned response object are mocked. NO live network from any test here.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test fixture.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bpg():
    """Import and return the build_plan_generator module."""
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


# ---------------------------------------------------------------------------
# Objective + universe helpers (mirror test_build_plan_generator.py).
# ---------------------------------------------------------------------------


def _objective(bpg, name: str):
    """Resolve an Objective enum member by canonical name from the module."""
    return bpg.Objective[name]


# A large in-universe membership set used across the happy-path tests.
_BIG_UNIVERSE = frozenset(
    {f"TCK{i}" for i in range(40)} | {"SPY", "QQQ", "IWM", "TLT", "GLD", "AGG", "EFA"}
)


# ---------------------------------------------------------------------------
# DSL fixture builders (by SHAPE — these are INPUTS the mocked SDK "returns",
# so we control them entirely; no hardcoded producer output).
# ---------------------------------------------------------------------------


def _asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _weight(scheme: str, children: list, **extra) -> dict:
    node = {"kind": "weight", "scheme": scheme, "children": children}
    node.update(extra)
    return node


def _group(name: str, children: list) -> dict:
    return {"kind": "group", "name": name, "children": children}


def _plan(objective: str, root: dict, *, plan_id: str = "p", name: str = "Plan") -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": "daily",
        "root": root,
    }


def _conforming_diversify_plan(plan_id: str) -> dict:
    """A 2-sleeve diversify group — satisfies the AC-8 diversify enforcement filter,
    so it is genuinely ADMITTED (not merely parsed) on the success path. This is the
    plan we feed on the post-truncation success response."""
    root = _group(
        "sleeves",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _weight("equal", [_asset("TLT"), _asset("GLD")]),
        ],
    )
    return _plan("diversify", root, plan_id=plan_id, name=f"Plan {plan_id}")


# ---------------------------------------------------------------------------
# Mocked-SDK response builders. The NEW capability vs the existing suite: these
# set response.stop_reason to "max_tokens" and produce an INCOMPLETE tool input,
# mirroring a real truncated Opus tool-call.
# ---------------------------------------------------------------------------


def _tool_use_block(input_payload) -> MagicMock:
    """A MagicMock mimicking an anthropic tool_use content block (.type/.input/.name)."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_payload
    block.name = "emit_build_plans"
    return block


def _full_response(plans: list[dict]) -> MagicMock:
    """A complete (non-truncated) tool_use response carrying `plans`."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [_tool_use_block({"plans": plans})]
    usage = MagicMock()
    usage.output_tokens = 1234  # well under any cap — not truncated
    response.usage = usage
    return response


def _truncated_response() -> MagicMock:
    """A TRUNCATED response: stop_reason="max_tokens" + an EMPTY tool input ({}), exactly
    what the live probe observed (input_json_chars=2). `plans` is therefore absent — a
    non-list payload — which is precisely how the current code mis-classifies truncation
    as InvalidToolUsePayload."""
    response = MagicMock()
    response.stop_reason = "max_tokens"
    response.content = [_tool_use_block({})]  # truncated: no 'plans' key
    usage = MagicMock()
    usage.output_tokens = 4096  # hit the (old) cap exactly
    response.usage = usage
    return response


def _truncated_response_with_partial_plans(plans: list[dict]) -> MagicMock:
    """A TRUNCATED response (stop_reason="max_tokens") that ALSO carries a partial,
    superficially-valid `plans` LIST. A real truncation can cut the tool-JSON mid-array,
    leaving a list that LOOKS parseable but is incomplete/unreliable. Truncation must be
    judged by stop_reason, NOT by whether the payload happens to be a list — so this must
    still be treated as a truncation (retry), never accepted as a complete result."""
    response = MagicMock()
    response.stop_reason = "max_tokens"
    response.content = [_tool_use_block({"plans": plans})]  # partial but list-shaped
    usage = MagicMock()
    usage.output_tokens = 4096
    response.usage = usage
    return response


def _client_with_create_returning(*responses) -> MagicMock:
    """A mock client whose messages.create yields the given responses in sequence
    (side_effect). A single response is repeated for every call (return_value)."""
    client = MagicMock()
    if len(responses) == 1:
        client.messages.create.return_value = responses[0]
    else:
        client.messages.create.side_effect = list(responses)
    return client


def _client_always_truncated() -> MagicMock:
    """A mock client that returns a FRESH truncated response on EVERY call (unbounded
    truncation) — used to prove the retry is bounded. A new object per call so call_count
    semantics are clean."""
    client = MagicMock()
    client.messages.create.side_effect = lambda *a, **k: _truncated_response()
    return client


def _patch_client(bpg, client: MagicMock):
    return patch.object(bpg, "_build_client", return_value=client)


# ===========================================================================
# GROUP A — max_tokens raised to a NAMED constant >> 4096 (no magic number)
# ===========================================================================


def test_max_output_tokens_is_a_named_module_constant(bpg):
    """A1/A2: MAX_OUTPUT_TOKENS exists as a named module constant (int) — not an inline
    literal. Resolving it from the module is itself the no-magic-number assertion."""
    assert hasattr(bpg, "MAX_OUTPUT_TOKENS"), "generator must name MAX_OUTPUT_TOKENS"
    assert isinstance(bpg.MAX_OUTPUT_TOKENS, int)


def test_max_output_tokens_exceeds_old_4096_cap_with_headroom(bpg):
    """A2: the constant is >> the old 4096 cap. The old cap truncated 12 full-grammar
    plans; a generous ceiling is free (you pay for actual output tokens, not the ceiling).
    Floor 16000 leaves headroom for ~12 full-grammar plans; impl may go higher."""
    assert bpg.MAX_OUTPUT_TOKENS > 4096, "must exceed the truncating 4096 cap"
    # 16000 floor: enough for ~12 full-grammar plans; calibration may push it HIGHER only.
    assert bpg.MAX_OUTPUT_TOKENS >= 16000, "must be large enough for 12 full-grammar plans"


def test_generate_build_plans_passes_max_output_tokens_constant_to_create(bpg):
    """A1 (core): the SDK call's max_tokens kwarg equals bpg.MAX_OUTPUT_TOKENS — NOT a
    hardcoded 4096. We capture the actual kwargs the generator passes to messages.create."""
    plans = [_conforming_diversify_plan("p0"), _conforming_diversify_plan("p1")]
    client = _client_with_create_returning(_full_response(plans))
    with _patch_client(bpg, client):
        bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert client.messages.create.called, "messages.create must be invoked"
    _, kwargs = client.messages.create.call_args
    assert "max_tokens" in kwargs, "max_tokens must be passed as a kwarg"
    assert kwargs["max_tokens"] == bpg.MAX_OUTPUT_TOKENS, (
        "max_tokens must be the named MAX_OUTPUT_TOKENS constant, not a literal"
    )
    # Anti-regression: the truncating 4096 cap must NOT be what we send.
    assert kwargs["max_tokens"] != 4096, "the old truncating 4096 cap must be gone"


# ===========================================================================
# GROUP B — truncation HANDLED via a BOUNDED retry (not silently dropped)
# ===========================================================================


def test_max_generation_attempts_is_a_bounded_named_constant(bpg):
    """B1: MAX_GENERATION_ATTEMPTS exists, is an int, and is bounded — retries are finite,
    never infinite. Lower bound 2 (a retry must be possible); upper cap keeps cost sane."""
    assert hasattr(bpg, "MAX_GENERATION_ATTEMPTS"), "generator must name MAX_GENERATION_ATTEMPTS"
    assert isinstance(bpg.MAX_GENERATION_ATTEMPTS, int)
    assert bpg.MAX_GENERATION_ATTEMPTS >= 2, "must allow at least one retry after truncation"
    assert bpg.MAX_GENERATION_ATTEMPTS <= 6, "retry budget must be bounded (cost guard)"


def test_truncation_then_success_yields_admitted_plans_after_retry(bpg):
    """B2 (THE core fix proof): a truncated response (stop_reason='max_tokens', empty
    input) FOLLOWED BY a complete response yields admitted plans. The generator must
    RETRY on truncation — not return 0 the way the buggy code does. We assert plans are
    admitted AND that create was called more than once (the retry happened)."""
    good = [_conforming_diversify_plan("a"), _conforming_diversify_plan("b")]
    # NOTE the two conforming plans are structurally identical -> dedup to 1; that's fine,
    # we only need >=1 admitted plan to prove the retry recovered.
    client = _client_with_create_returning(_truncated_response(), _full_response(good))
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans, "truncate-then-succeed must recover >=1 admitted plan (the fix)"
    assert client.messages.create.call_count > 1, "the generator must RETRY on truncation"
    # The recovered run is a clean success: no error reason.
    assert result.reason is None, "a successful recovery must not carry an error reason"


def test_truncation_retry_is_bounded_and_degrades_honestly_when_exhausted(bpg):
    """B3: when EVERY attempt truncates, the generator stops after exactly
    MAX_GENERATION_ATTEMPTS calls (bounded — never infinite) and degrades per D-1: empty
    plans + an HONEST reason that NAMES truncation (not the misleading
    'InvalidToolUsePayload' the buggy code returned)."""
    client = _client_always_truncated()
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert client.messages.create.call_count == bpg.MAX_GENERATION_ATTEMPTS, (
        "all-truncated must attempt exactly MAX_GENERATION_ATTEMPTS times, then STOP"
    )
    assert result.plans == [], "exhausted truncation must yield empty plans"
    assert isinstance(result.reason, str) and result.reason, "must surface an honest reason"
    reason_l = result.reason.lower()
    assert ("max_tokens" in reason_l) or ("truncat" in reason_l), (
        "the reason must HONESTLY name truncation, not be the misleading "
        f"'InvalidToolUsePayload'; got {result.reason!r}"
    )


def test_truncation_exhaustion_reason_leaks_no_secret_or_path(bpg):
    """B4 (D-1 security): the truncation degradation reason carries no API key, file path,
    or other secret-shaped content — only an honest, safe truncation descriptor."""
    fake_key = "sk-ant-SECRET-build-plan-key-1234567890"
    client = _client_always_truncated()
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert fake_key not in (result.reason or "")
    assert "sk-ant" not in (result.reason or "")
    assert "secret" not in (result.reason or "").lower()
    assert "C:/Users" not in (result.reason or "") and "path.py" not in (result.reason or "")


def test_non_truncated_first_response_does_not_retry(bpg):
    """B5: the retry is TRUNCATION-GATED — a complete first response (stop_reason!='max_tokens')
    must NOT trigger a retry. Guards an over-eager impl that retries unconditionally and
    burns budget."""
    good = [_conforming_diversify_plan("a"), _conforming_diversify_plan("b")]
    client = _client_with_create_returning(_full_response(good))
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans, "a complete first response must admit plans"
    assert client.messages.create.call_count == 1, (
        "a non-truncated response must NOT retry (retry is truncation-gated)"
    )


def test_non_truncated_non_list_payload_still_degrades_cleanly_without_retry(bpg):
    """B5/boundary: a NON-truncated response whose `plans` payload is non-list
    (stop_reason='tool_use') keeps the existing clean-degrade behavior — it is NOT a
    truncation, so it must NOT trigger the truncation retry. This pins that the retry
    keys specifically on stop_reason=='max_tokens' and does not over-fire (mirrors the
    existing test_ac11_tool_use_input_with_non_list_plans_degrades_cleanly contract)."""
    response = MagicMock()
    response.stop_reason = "tool_use"  # NOT truncated
    response.content = [_tool_use_block({"plans": "not-a-list"})]
    usage = MagicMock()
    usage.output_tokens = 50
    response.usage = usage
    client = _client_with_create_returning(response)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == [], "a non-list payload still degrades to empty plans"
    assert isinstance(result.reason, str) and result.reason
    assert client.messages.create.call_count == 1, (
        "a non-truncated non-list payload must NOT trigger the truncation retry"
    )


def test_truncation_recovery_respects_dedup_and_n_plans_ceiling(bpg):
    """B6 (strategy-agnostic accumulation guard): after a truncate-then-succeed, the
    observable admission contract holds regardless of mechanism (pure-retry OR
    reduce-and-accumulate): >=1 admitted plan, total <= n_plans, and no structural
    duplicates among the admitted plans (dedup intact)."""
    # The success response carries STRUCTURALLY-DISTINCT conforming plans so admission is
    # not collapsed by dedup; we then assert the ceiling + no-dup invariants.
    distinct = [
        _plan(
            "diversify",
            _group(
                "g",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("equal", [_asset("TLT"), _asset("GLD")]),
                ],
            ),
            plan_id="d1",
        ),
        _plan(
            "diversify",
            _group(
                "g",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
                ],
            ),
            plan_id="d2",
        ),
    ]
    n_plans = 5
    client = _client_with_create_returning(_truncated_response(), _full_response(distinct))
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(
            _objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=n_plans
        )
    assert result.plans, "recovery must admit >=1 plan"
    assert len(result.plans) <= n_plans, "admitted count must respect the n_plans ceiling"
    # No structural duplicates among the admitted plans (dedup intact across recovery).
    fingerprints = [bpg._root_fingerprint(p) for p in result.plans]
    assert len(fingerprints) == len(set(fingerprints)), "admitted plans must be deduped"


def test_truncation_with_partial_plans_list_is_still_retried_not_accepted(bpg):
    """B7 (Revise gap): truncation is judged by stop_reason, NOT by whether the payload is
    a list. A truncated response (stop_reason='max_tokens') that ALSO carries a partial,
    list-shaped `plans` payload must STILL be treated as truncation and retried — it must
    NOT be accepted as a complete result on the strength of the list being present. This
    guards a tempting-but-wrong impl that only retries when the payload is empty/non-list.

    We feed: a truncated response WITH a partial conforming-plan list, then a clean
    success. The generator must retry (the partial-list truncation does not short-circuit)
    and recover via the clean response."""
    partial = [_conforming_diversify_plan("partial")]
    good = [
        _plan(
            "diversify",
            _group(
                "g",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
                ],
            ),
            plan_id="recovered",
        )
    ]
    client = _client_with_create_returning(
        _truncated_response_with_partial_plans(partial), _full_response(good)
    )
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert client.messages.create.call_count > 1, (
        "a stop_reason='max_tokens' response must be retried even when it carries a "
        "list-shaped (partial) plans payload — truncation is judged by stop_reason"
    )
    assert result.plans, "recovery via the clean response must admit >=1 plan"
    assert result.reason is None, "a clean recovery must not carry an error reason"


def test_all_truncated_with_partial_lists_degrades_honestly_not_admits(bpg):
    """B8 (Revise gap, adversarial): if EVERY attempt is truncated but each carries a
    partial list-shaped payload, the generator must NOT admit any of those partial plans —
    it exhausts the bounded retry and degrades with the honest truncation reason. (An impl
    that accepts a truncated-but-list payload would wrongly admit unreliable plans.)"""
    partial = [_conforming_diversify_plan("partial")]
    client = MagicMock()
    client.messages.create.side_effect = lambda *a, **k: _truncated_response_with_partial_plans(
        partial
    )
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert client.messages.create.call_count == bpg.MAX_GENERATION_ATTEMPTS, (
        "all-truncated (even with partial lists) must attempt exactly "
        "MAX_GENERATION_ATTEMPTS times, then STOP"
    )
    assert result.plans == [], "partial truncated payloads must NOT be admitted"
    reason_l = (result.reason or "").lower()
    assert ("max_tokens" in reason_l) or ("truncat" in reason_l), (
        f"must honestly name truncation; got {result.reason!r}"
    )


def test_retry_after_truncation_to_unparseable_response_degrades_cleanly(bpg):
    """B9 (Revise gap): a truncated first response followed by a NON-truncated but
    UNPARSEABLE response (no tool_use block) flows into the existing clean-degrade path —
    the retry result is parsed by the same downstream logic, so it degrades to empty plans
    with a reason, never raises, never returns garbage."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I refuse to use the tool."
    unparseable = MagicMock()
    unparseable.stop_reason = "end_turn"  # NOT truncation, but no tool_use block
    unparseable.content = [text_block]
    unparseable.usage = MagicMock()
    unparseable.usage.output_tokens = 20
    client = _client_with_create_returning(_truncated_response(), unparseable)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert client.messages.create.call_count == 2, "must retry once after truncation"
    assert result.plans == [], "an unparseable retry response degrades to empty plans"
    assert isinstance(result.reason, str) and result.reason, "must surface a reason"


# ===========================================================================
# GROUP C — contracts preserved (frozen signature, never-raises, advisory-only)
# ===========================================================================


def test_generate_build_plans_public_signature_is_frozen(bpg):
    """C1: the public signature stays (objective, membership_set, *, n_plans) — the fix
    must not change the call contract callers depend on (AC-20 style)."""
    sig = inspect.signature(bpg.generate_build_plans)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["objective", "membership_set", "n_plans"], f"public signature changed: {names}"
    # n_plans is keyword-only.
    assert sig.parameters["n_plans"].kind is inspect.Parameter.KEYWORD_ONLY
    # objective + membership_set are positional.
    assert sig.parameters["objective"].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )
    assert sig.parameters["membership_set"].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )


def test_generate_build_plans_never_raises_on_truncation_paths(bpg):
    """C2 (D-1): no truncation path raises. truncate-then-succeed, all-truncated, and a
    truncated response whose retry itself errors all return a GeneratorResult, never an
    exception."""
    # all-truncated
    with _patch_client(bpg, _client_always_truncated()):
        r1 = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert isinstance(r1.plans, list)

    # truncated first, then the SDK raises on the retry -> still no exception escapes
    client = MagicMock()
    client.messages.create.side_effect = [_truncated_response(), RuntimeError("transport")]
    with _patch_client(bpg, client):
        r2 = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert r2.plans == []
    assert isinstance(r2.reason, str) and r2.reason
    # D-1: the raised-exception path still reports only the class name (no leak).
    assert "RuntimeError" in r2.reason


def test_truncation_result_plans_is_always_a_list_never_none(bpg):
    """C2 invariant: result.plans is ALWAYS a list on every truncation path, never None."""
    with _patch_client(bpg, _client_always_truncated()):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert isinstance(result.plans, list)
