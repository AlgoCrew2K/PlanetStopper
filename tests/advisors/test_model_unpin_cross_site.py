"""Cross-site sweep of the model-unpin contract.

Every site that names a model goes through ``llm_request.send``; this file checks the
rules that must hold the same way at every site, so a rule enforced at one site and
missing at a sibling fails here:

* the model a record names is the model that served a reply (one recorded exception: an
  operator override the API rejects keeps the override id beside its reason code);
* a run resolves its family once, however many text-only retries it makes;
* the reply-adoption and provenance-minting logic exists once, not once per site;
* no site reaches the Messages API or the Models API except through the shared modules;
* the Asset Swaps panel's reason-code list cannot drift from the reason codes the
  server produces.

Replies and error bodies are captured or schema-derived fixtures under
``tests/fixtures/anthropic`` (served by the in-process HTTP fake, the SDK parsing them);
model ids are read from those fixtures, never typed. Each case drives the site's real
entry point, patching only the site's client seam and its non-model collaborators.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess

import httpx
import pytest

import llm_request
from tests import _asset_swap_llm_support as swap_support
from tests import _sb_generation_harness as sb_support
from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture
from tests.advisors import logic_change_http_support as logic_support
from tests.llm_request.conftest import CAPTURED, error_outcome, message_body, newest_id

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SUGGESTION_ENV_VAR = "ADVISOR_SUGGESTION_MODEL"

ROUTE_SITES = ["asset_swap", "logic_change", "strategy_builder"]
RETRY_SITES = ["asset_swap", "logic_change", "build_plans", "overlay"]


@pytest.fixture
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Site replies
# ---------------------------------------------------------------------------


def _success_reply(site: str) -> dict:
    if site == "asset_swap":
        return swap_support.swap_tool_reply()
    if site == "logic_change":
        return logic_support.reply_with_tool_edit(logic_support.real_edit())
    return sb_support.tool_reply_with_plans(sb_support.conforming_diversify_plans())


def _fallback_reply(site: str) -> dict:
    if site == "asset_swap":
        return message_body("fallback_served_tool.json")
    if site == "logic_change":
        return logic_support.fallback_served_reply(logic_support.real_edit())
    return sb_support.fallback_reply_with_plans(sb_support.conforming_diversify_plans())


def _non_fable_served_id() -> str:
    return next(r["id"] for r in CAPTURED["data"] if r.get("line") != "fable")


def _failing_listing_fake() -> AnthropicHttpFake:
    fake = AnthropicHttpFake()
    for _ in range(8):
        fake.queue_listing_outcome(error_outcome("503_unavailable.json"))
    return fake


def _drive_route(site, monkeypatch, fake, client) -> str:
    """Run the site's route once; return ``provenance.generation_model`` it reported."""
    if site == "asset_swap":
        run = swap_support.post_evaluate(client, fake, swap_support.objective_body())
        assert run.status == 200, run.data
        return run.data["provenance"]["generation_model"]
    if site == "logic_change":
        logic_support.install_route_world(monkeypatch, fake)
        return logic_support.evaluate_via_route(client)["provenance"]["generation_model"]
    with sb_support.install_chain_seams(monkeypatch, fake, atlas=[sb_support.atlas_candidate()]):
        response = sb_support.post_strategy_builder_run(client)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["provenance"]["generation_model"]


# kind -> (replies to queue given the site, expected model given the replies and the env)
def _plan_for(kind: str, site: str, monkeypatch):
    """Return (fake, expected generation_model) for one outcome kind at one site."""
    text_only = message_body("text_only_with_tools.json")
    if kind == "primary-reply":
        body = _success_reply(site)
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(body)
        return fake, body["model"]
    if kind == "fallback-served-reply":
        body = _fallback_reply(site)
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(body)
        return fake, body["model"]
    if kind == "refusal":
        body = load_fixture("messages/refusal_reasoning_extraction.json")
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(body)
        return fake, body["model"]
    if kind == "max-tokens":
        body = load_fixture("messages/max_tokens.json")
        fake = AnthropicHttpFake.with_captured_page()
        # build plans retry a truncated reply up to its attempt bound
        for _ in range(llm_request.NO_TOOL_CALL_ATTEMPTS + 2):
            fake.queue_message(body)
        return fake, body["model"]
    if kind == "no-tool-call-every-attempt":
        fake = AnthropicHttpFake.with_captured_page()
        for _ in range(llm_request.NO_TOOL_CALL_ATTEMPTS + 2):
            fake.queue_message(text_only)
        return fake, text_only["model"]
    if kind == "model-unavailable":
        return _failing_listing_fake(), ""
    if kind == "transport-error-no-reply":
        fake = AnthropicHttpFake.with_captured_page()
        for _ in range(llm_request.NO_TOOL_CALL_ATTEMPTS + 2):
            fake.queue_message(httpx.ConnectError("no route to host"))
        return fake, ""
    if kind in ("override-not-found", "override-request-rejected"):
        override = _non_fable_served_id()
        monkeypatch.setenv(SUGGESTION_ENV_VAR, override)
        fake = AnthropicHttpFake.with_captured_page()
        rejection = (
            "404_not_found.json" if kind == "override-not-found" else "400_invalid_request.json"
        )
        for _ in range(llm_request.NO_TOOL_CALL_ATTEMPTS + 2):
            fake.queue_message(error_outcome(rejection))
        return fake, override
    raise AssertionError(kind)


OUTCOME_KINDS = [
    "primary-reply",
    "fallback-served-reply",
    "refusal",
    "max-tokens",
    "no-tool-call-every-attempt",
    "model-unavailable",
    "transport-error-no-reply",
    "override-not-found",
    "override-request-rejected",
]


class TestRecordsNameTheModelThatServedAReply:
    """A run's provenance names the model of the last reply it received.

    No reply means no model served: the record is empty (``model unavailable``), not the
    model the registry resolved before the call. The one exception is an operator
    override the API rejects: it keeps the override id, because the reason code printed
    beside it names that variable.
    """

    @pytest.mark.parametrize("kind", OUTCOME_KINDS)
    @pytest.mark.parametrize("site", ROUTE_SITES)
    def test_generation_model_is_the_serving_model_or_empty(
        self, request, monkeypatch, flask_client, site, kind
    ):
        if kind == "model-unavailable":
            request.getfixturevalue("unresolved_models")
        fake, expected = _plan_for(kind, site, monkeypatch)

        recorded = _drive_route(site, monkeypatch, fake, flask_client)

        assert recorded == expected, (
            f"{site}/{kind}: provenance named {recorded!r}, the model that served is {expected!r}"
        )
        assert fake.misuse == []


# ---------------------------------------------------------------------------
# One resolution per run across text-only retries
# ---------------------------------------------------------------------------


def _run_retry_site(site, monkeypatch, fake) -> None:
    """Make the site's generator send three requests: two text-only replies, then a tool reply."""
    text_only = message_body("text_only_with_tools.json")
    if site == "asset_swap":
        import advisors.asset_swap_engine as ase

        success = swap_support.swap_tool_reply()
        monkeypatch.setattr(ase, "_build_client", lambda: fake.client)
        queue = [text_only, text_only, success]
        run = lambda: ase.generate_reasoned_swap_candidates(  # noqa: E731
            "sym-1",
            swap_support.held_tree(),
            ase.SwapObjective("reduce_correlation", None, 0.0),
            tradeable_universe=swap_support.tradeable_universe(),
        )
    elif site == "logic_change":
        import advisors.logic_change_engine as lce

        success = logic_support.reply_with_tool_edit(logic_support.real_edit())
        monkeypatch.setattr(lce, "_build_client", lambda: fake.client)
        queue = [text_only, text_only, success]
        run = lambda: lce.generate_reasoned_logic_candidates(  # noqa: E731
            "sym-1",
            logic_support.load_tree(),
            lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0),
        )
    elif site == "build_plans":
        import advisors.build_plan_generator as gen

        success = sb_support.tool_reply_with_plans(sb_support.conforming_diversify_plans())
        sb_support.install_generator_seam(monkeypatch, fake)
        queue = [text_only, text_only, success]
        run = lambda: gen.generate_build_plans(  # noqa: E731
            gen.Objective("diversify"), sb_support.MEMBERSHIP
        )
    else:
        import advisors.frontrunner_builder as fbld

        success = message_body("thinking_tool_emit_frontrunner_overlay.json")
        monkeypatch.setattr(fbld, "_build_client", lambda: fake.client)
        queue = [text_only, text_only, success]
        run = lambda: fbld.generate_candidate_overlay({"watched_tickers": ["SPY"]})  # noqa: E731
    for body in queue:
        fake.queue_message(body)
    run()


class TestOneListingPerRunAcrossTextOnlyRetries:
    @pytest.mark.parametrize("site", RETRY_SITES)
    def test_retries_neither_relist_nor_change_the_named_model(
        self, unresolved_models, monkeypatch, site
    ):
        fake = AnthropicHttpFake.with_captured_page()

        _run_retry_site(site, monkeypatch, fake)

        named = [r.body["model"] for r in fake.message_requests]
        assert len(named) == 3, f"{site}: expected 2 text-only replies and a third request"
        assert set(named) == {newest_id("fable")}
        assert fake.listing_sequences == 1, (
            f"{site}: the family was listed {fake.listing_sequences} times in one run"
        )
        assert fake.misuse == []


# ---------------------------------------------------------------------------
# The shared helpers exist once
# ---------------------------------------------------------------------------


def _tracked_python() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    paths = [p for p in out.split("\0") if p]
    return [
        REPO_ROOT / p
        for p in paths
        if not p.startswith(("tests/", "docs/", "feature-plans/", ".claude/"))
    ]


def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _mentions_peek(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "peek" for n in ast.walk(node))


class TestReplyAdoptionAndProvenanceMintingExistOnce:
    RETIRED_PER_SITE_HELPERS = {"_adopt_reply", "_empty_run_message", "_reply_model_and_message"}

    def test_the_per_site_adoption_helpers_are_gone(self):
        found = {}
        for path in _tracked_python():
            names = {
                n.name
                for n in ast.walk(_parse(path))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            hit = names & self.RETIRED_PER_SITE_HELPERS
            if hit:
                found[str(path.relative_to(REPO_ROOT))] = sorted(hit)

        assert found == {}, f"per-site copies of the shared adoption helper remain: {found}"

    def test_the_default_generation_model_is_minted_from_the_registry_in_one_place(self):
        """Every route and engine builds its default ``generation_model`` the same way.

        Counted: a ``"generation_model"`` dict entry or subscript assignment whose value
        reads ``model_config.peek``. One shared minting helper means one such site.
        """
        sites = []
        for path in _tracked_python():
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values, strict=True):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "generation_model"
                            and _mentions_peek(value)
                        ):
                            sites.append(f"{path.relative_to(REPO_ROOT)}:{key.lineno}")
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "generation_model"
                            and _mentions_peek(node.value)
                        ):
                            sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

        assert len(sites) <= 1, f"generation_model is minted from the registry at {sites}"


# ---------------------------------------------------------------------------
# No site bypasses the shared request and resolution modules
# ---------------------------------------------------------------------------

_FORBIDDEN_REQUEST_KWARGS = {"temperature", "top_p", "top_k", "budget_tokens"}


class TestNoSiteBypassesTheSharedModules:
    def test_messages_and_models_api_calls_live_only_in_the_shared_modules(self):
        offenders = []
        for path in _tracked_python():
            relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            for node in ast.walk(_parse(path)):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                receiver = node.func.value
                receiver_name = receiver.attr if isinstance(receiver, ast.Attribute) else None
                if receiver_name == "messages" and relative != "llm_request.py":
                    offenders.append(f"{relative}:{node.lineno} messages.{node.func.attr}")
                if receiver_name == "models" and relative != "model_config.py":
                    offenders.append(f"{relative}:{node.lineno} models.{node.func.attr}")
        assert offenders == [], offenders

    def test_no_request_sets_a_sampling_or_thinking_budget_parameter(self):
        offenders = []
        for path in _tracked_python():
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg in _FORBIDDEN_REQUEST_KWARGS and _is_llm_call(node):
                            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert offenders == [], offenders

    def test_no_site_reads_a_reply_by_position_or_by_attribute_presence(self):
        offenders = []
        for path in _tracked_python():
            for node in ast.walk(_parse(path)):
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "content"
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == 0
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} content[0]")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "hasattr"
                    and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "text"
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} hasattr text")
        assert offenders == [], offenders


def _is_llm_call(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in {"send", "create", "parse"}


# ---------------------------------------------------------------------------
# The panel's reason-code list cannot drift from the server's
# ---------------------------------------------------------------------------


class TestAssetSwapPanelReasonCodesMatchTheServer:
    def test_the_javascript_list_is_exactly_the_reason_codes_a_generation_can_end_with(self):
        source = (REPO_ROOT / "static" / "ai_advisor_asset_swaps.js").read_text(encoding="utf-8")
        match = re.search(r"GENERATION_REASON_CODES\s*=\s*\[(.*?)\]", source, re.DOTALL)
        assert match, "the panel no longer declares its reason-code list"
        listed = set(re.findall(r"'([a-z_]+)'", match.group(1)))

        server = {
            value
            for name, value in vars(llm_request).items()
            if name.startswith("REASON_") and isinstance(value, str)
        }

        assert listed == server
