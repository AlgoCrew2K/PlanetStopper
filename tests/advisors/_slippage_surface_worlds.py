"""TEST-ONLY worlds that run every advisor surface that sends a Composer backtest.

Each runner drives one entry point the way production reaches it (a route through the Flask
test client, a scheduled job through its tick or weekly function) with Composer replaced at the
one boundary every surface shares, ``requests``, by ``ComposerRouter``. No runner patches
``run_backtest`` or the client's slippage constant, so a request body is what the real client
built and the default reaches it exactly as it does in production. Only the generation model
(Anthropic) and the other paid or live reads (Atlas, the Alpaca universe and bars) are replaced,
each at its own seam.

``CALIBRATED_SLIPPAGE`` is the plan's literal, written here once. It is never read from the
client module: a value read from the constant would make every assertion pass while the
constant still held the old default.

Fixture provenance: the Composer replies are captured producer output (the inline backtest
capture of tests/fixtures/composer/backtest_inline_v1.json for the surfaces that gate on a long
series, and the router's default captured tenure record for the incubation tick and the tenure
review). Anthropic replies are the captured Messages fixtures, with the plan list or edit
replaced by conforming ones as the shared harnesses already do. The frontrunner incumbent and the
incubation candidate trees are test-stated, built by the schema constructors or as a minimal
dict, and no assertion reads their content.

Not imported by production code.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import _asset_swap_llm_support as asl
from tests import _sb_generation_harness as sbh
from tests._anthropic_http_fake import AnthropicHttpFake
from tests.advisors import _composer_http_router as h
from tests.advisors import logic_change_http_support as lcs
from tests.app import _tenure_review_job_builders as jb

REPO_ROOT = Path(__file__).resolve().parents[2]

# 0.1% of each traded dollar, as a fraction: the plan's value for a backtest that is run
# without an explicit slippage argument.
CALIBRATED_SLIPPAGE = 0.001

SPY_TICKERS = frozenset({"SPY"})

# The long captured backtest series, so the gates see a series long enough to judge.
GATE_RESPONSE = sbh.COMPOSER_BACKTEST_CAPTURE["response"]

SB_ROUTE_BODY = {"objective": "diversify", "symphony_id": "Willow Creek Rotation"}
SECOND_TENURE_RUN_AT = datetime(2026, 10, 21, 3, 45, tzinfo=jb.ET)

_ATLAS_UNAVAILABLE = {"available": False, "candidates": [], "stats": {}, "source": "captplanet"}
_SIGNALS_UNAVAILABLE = {
    "available": False,
    "reason": "NoLiveAtlasInTests",
    "signals": [],
    "stats": {},
    "source": "captplanet",
}


@dataclass
class SurfaceRun:
    """One entry point run: the router that saw every Composer request and what it produced."""

    router: h.ComposerRouter
    outcome: dict

    @property
    def backtests(self) -> list[h.Request]:
        return self.router.of_kind("backtest")


# ---------------------------------------------------------------------------
# Reading what was sent
# ---------------------------------------------------------------------------


def sent_slippages(router: h.ComposerRouter) -> list:
    """The ``slippage_percent`` of every backtest body, in order (None when the field is absent)."""
    return [req.body_json().get("slippage_percent", "absent") for req in router.of_kind("backtest")]


def body_tickers(request: h.Request) -> frozenset:
    """Every ticker named in a request's symphony tree."""
    tickers: set[str] = set()
    for node in sbh.walk(request.body_json()["symphony"]["raw_value"]):
        ticker = node.get("ticker")
        if isinstance(ticker, str):
            tickers.add(ticker)
    return frozenset(tickers)


def spy_requests(router: h.ComposerRouter) -> list[h.Request]:
    """The SPY baseline requests: the tree holds SPY and nothing else."""
    return [r for r in router.of_kind("backtest") if body_tickers(r) == SPY_TICKERS]


def _mask_ids(value, order: dict):
    """Replace each ``id`` string by the order it first appeared in (the constructors mint uuid4)."""
    if isinstance(value, dict):
        return {
            key: (
                f"id-{order.setdefault(item, len(order))}"
                if key == "id" and isinstance(item, str)
                else _mask_ids(item, order)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_ids(item, order) for item in value]
    return value


def masked_sequence(router: h.ComposerRouter) -> list[dict]:
    """Every backtest request as url, body form and body, without the slippage field and with
    the tree ids masked by order of first appearance in the sequence."""
    order: dict = {}
    sequence = []
    for req in router.of_kind("backtest"):
        body = copy.deepcopy(req.body_json())
        body.pop("slippage_percent", None)
        body["symphony"]["raw_value"] = _mask_ids(body["symphony"]["raw_value"], order)
        sequence.append(
            {
                "url": req.url,
                "body_form": "data" if req.kwargs.get("data") is not None else "json",
                "body": body,
            }
        )
    return sequence


def db_outcome() -> dict:
    """The advisory rows the run stored, as role, verdict and count (ids and times dropped)."""
    import database  # noqa: PLC0415

    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT advisor_role, verdict, COUNT(*) FROM advisor_observations "
            "GROUP BY advisor_role, verdict ORDER BY advisor_role, verdict"
        ).fetchall()
    finally:
        conn.close()
    return {"observations": [[role, verdict, count] for role, verdict, count in rows]}


def _gate_view(data: dict | None) -> dict:
    """The stable gate fields of a route answer (never a run id, time or metric)."""
    data = data or {}

    def rows(key):
        found = [
            [r.get("candidate_id"), r.get("gate_decision"), r.get("rejection_reason")]
            for r in data.get(key) or []
        ]
        return sorted(found, key=json.dumps)

    return {
        "error": data.get("error"),
        "gate_decision": data.get("gate_decision"),
        "rejection_reason": (data.get("gate_result") or {}).get("rejection_reason"),
        "survivors": rows("survivors"),
        "rejected": rows("rejected"),
        "survivors_detail": len(data.get("survivors_detail") or []),
        "rejected_detail": len(data.get("rejected_detail") or []),
        "built_new_count": data.get("built_new_count"),
        "atlas_count": data.get("atlas_count"),
        "backtest_unavailable": data.get("backtest_unavailable"),
    }


# ---------------------------------------------------------------------------
# Shared installs
# ---------------------------------------------------------------------------


def install_router(monkeypatch, *, response_body=None) -> h.ComposerRouter:
    """The one replacement of ``requests``; ``response_body`` is served to every backtest."""
    router = h.ComposerRouter().install(monkeypatch)
    if response_body is not None:
        router.set_backtest(h.Reply(body=copy.deepcopy(response_body)))
    return router


def block_atlas(monkeypatch) -> None:
    """No world may reach a paid Atlas read: both collections and the candidate loader answer
    unavailable or empty."""
    monkeypatch.setattr(
        "advisors.community_strats.load_community_strategies",
        lambda *a, **k: copy.deepcopy(_ATLAS_UNAVAILABLE),
    )
    monkeypatch.setattr(
        "advisors.frontrunner_signals.load_frontrunner_signals",
        lambda *a, **k: copy.deepcopy(_SIGNALS_UNAVAILABLE),
    )
    monkeypatch.setattr("advisors.build_plan_generator.load_atlas_candidates", lambda *a, **k: [])


def _scratch_db(monkeypatch, tmp_path) -> None:
    jb.use_scratch_db(monkeypatch, tmp_path)


def _no_reasoning_context(monkeypatch) -> None:
    import ai_advisor  # noqa: PLC0415

    monkeypatch.setattr(
        ai_advisor,
        "build_reasoning_context",
        lambda *a, **k: ("", copy.deepcopy(ai_advisor._EMPTY_MANIFEST)),
    )


def _flask_client():
    import app as app_module  # noqa: PLC0415

    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _real_run_backtest():
    from advisors.composer_backtest_client import run_backtest  # noqa: PLC0415

    return run_backtest


# ---------------------------------------------------------------------------
# Strategy Builder
# ---------------------------------------------------------------------------


def run_strategy_builder_route(
    monkeypatch,
    tmp_path,
    *,
    backtest_handler: Callable[[h.Request, int], h.Reply] | None = None,
    plans: list[dict] | None = None,
) -> SurfaceRun:
    """``POST /ai-advisor/strategy-builder/run`` for a symphony-scoped run that compiles two
    candidates (or the given ``plans``); ``backtest_handler`` scripts Composer's backtest
    answers."""
    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    if backtest_handler is not None:
        router.backtest_handler = backtest_handler
    block_atlas(monkeypatch)
    _no_reasoning_context(monkeypatch)
    monkeypatch.setattr("database.load_state", lambda: {})
    fake = sbh.fake_with_captured_page()
    fake.queue_message(
        sbh.tool_reply_with_plans(plans if plans is not None else sbh.conforming_diversify_plans())
    )
    stack = sbh.install_chain_seams(monkeypatch, fake, composer_router=router)
    with stack, _flask_client() as client:
        response = sbh.post_strategy_builder_run(client, copy.deepcopy(SB_ROUTE_BODY))
    return SurfaceRun(
        router, {"status": response.status_code, "route": _gate_view(response.get_json())}
    )


def run_strategy_builder_weekly(monkeypatch, tmp_path) -> SurfaceRun:
    """The weekly Strategy Builder run: all four objectives, the frontrunner stage a no-op."""
    import advisors.frontrunner_builder as fbld  # noqa: PLC0415
    import advisors.strategy_builder_scheduler as sb_sched  # noqa: PLC0415

    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    block_atlas(monkeypatch)
    monkeypatch.setattr(sb_sched, "_already_ran_this_week", lambda: False)
    monkeypatch.setattr(fbld, "run_frontrunner_build", lambda *a, **k: None)
    fake = sbh.fake_with_captured_page()
    for _ in range(8):
        fake.queue_message(sbh.tool_reply_with_plans(sbh.conforming_diversify_plans()))
    with sbh.install_chain_seams(monkeypatch, fake, composer_router=router):
        sb_sched.run_weekly_build()
    return SurfaceRun(router, db_outcome())


# ---------------------------------------------------------------------------
# Asset Swaps
# ---------------------------------------------------------------------------


def run_asset_swaps_route(monkeypatch, tmp_path) -> SurfaceRun:
    """``POST /ai-advisor/asset-swaps/evaluate`` for an explicit swap pair of a live symphony."""
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    block_atlas(monkeypatch)
    fake = AnthropicHttpFake.with_captured_page()
    with _flask_client() as client:
        run = asl.post_evaluate(client, fake, asl.explicit_pair_body(), real_backtest=True)
    return SurfaceRun(router, {"status": run.status, "route": _gate_view(run.data)})


def run_asset_swaps_weekly(monkeypatch, tmp_path) -> SurfaceRun:
    """The weekly asset swap suggestions for one live symphony; the swap generator is the
    model seam, answering the captured pair."""
    import advisors.asset_swap_engine as ase  # noqa: PLC0415
    import advisors.weekly_suggestions_scheduler as weekly  # noqa: PLC0415

    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    block_atlas(monkeypatch)
    incumbent, candidate = asl.captured_pair()
    state = {asl.COMPOSER_HASH: {"name": asl.SYMPHONY_NAME, "logic_holdings": {}}}
    monkeypatch.setattr("database.load_state", lambda: copy.deepcopy(state))
    monkeypatch.setattr("symphony_logic.fetch_symphony_score", lambda *a, **k: asl.held_tree())
    monkeypatch.setattr("synthetic_history.fetch_bars", lambda *a, **k: {})
    monkeypatch.setattr(
        ase,
        "generate_reasoned_swap_candidates",
        lambda *a, **k: [
            ase.SwapCandidate(
                incumbent_asset=incumbent, candidate_asset=candidate, rationale="model seam"
            )
        ],
    )
    weekly.run_weekly_asset_swap_suggestions()
    return SurfaceRun(router, db_outcome())


# ---------------------------------------------------------------------------
# Logic Changes
# ---------------------------------------------------------------------------


def _logic_fake() -> AnthropicHttpFake:
    fake = lcs.new_fake()
    fake.queue_message(lcs.reply_with_tool_edit(lcs.real_edit()))
    return fake


def _spy_outcome(spy: MagicMock) -> list:
    return [[c.kwargs.get("advisor_role"), c.kwargs.get("verdict")] for c in spy.call_args_list]


def run_logic_changes_route(monkeypatch, tmp_path) -> SurfaceRun:
    """``POST /ai-advisor/logic-changes/evaluate`` for a reasoned change of a live symphony."""
    import advisors.logic_change_engine as lce  # noqa: PLC0415

    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    block_atlas(monkeypatch)
    spy = lcs.install_route_world(monkeypatch, _logic_fake())
    # The shared world replaces the engine's backtest; the real client is put back so the
    # request reaches the one Composer boundary.
    monkeypatch.setattr(lce, "run_backtest", _real_run_backtest())
    with _flask_client() as client:
        response = lcs.post_evaluate(client=client)
    return SurfaceRun(
        router,
        {
            "status": response.status_code,
            "route": _gate_view(response.get_json()),
            "stored": _spy_outcome(spy),
        },
    )


def run_logic_changes_weekly(monkeypatch, tmp_path) -> SurfaceRun:
    """The weekly logic change suggestions for one live symphony."""
    import advisors.logic_change_engine as lce  # noqa: PLC0415
    import advisors.weekly_suggestions_scheduler as weekly  # noqa: PLC0415

    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    block_atlas(monkeypatch)
    spy = lcs.install_engine_world(monkeypatch, _logic_fake())
    monkeypatch.setattr(lce, "run_backtest", _real_run_backtest())
    state = {lcs.ROUTE_COMPOSER_HASH: {"name": lcs.ROUTE_SYMPHONY_NAME}}
    monkeypatch.setattr("database.load_state", lambda: copy.deepcopy(state))
    monkeypatch.setattr("symphony_logic.fetch_symphony_score", lambda *a, **k: lcs.load_tree())
    weekly.run_weekly_logic_change_suggestions()
    return SurfaceRun(router, {"stored": _spy_outcome(spy)})


# ---------------------------------------------------------------------------
# Frontrunner Builder
# ---------------------------------------------------------------------------

FRONTRUNNER_SYMPHONY_ID = "frontrunner-incumbent-hash"


def _frontrunner_incumbent() -> dict:
    from advisors import symphony_schema as s  # noqa: PLC0415

    cascade = s.make_if(
        s.make_condition(s.make_indicator("relative-strength-index", "SPY", window=10), "gt", 80),
        then_children=[s.make_weight_equal([s.make_asset("VIXY")])],
        else_children=[s.make_weight_equal([s.make_asset("CORE_ASSET_0001")])],
    )
    return s.make_root("Maple Ridge Incumbent", "daily", [cascade])


def _frontrunner_seams(monkeypatch) -> MagicMock:
    """The build's own seams: the incumbent score read, the overlay model client, the proposal
    store. Returns the proposal-store spy."""
    import advisors.frontrunner_builder as fbld  # noqa: PLC0415
    from tests.advisors.test_frontrunner_gate_wiring import (  # noqa: PLC0415
        _mocked_fable_overlay_client,
    )

    block_atlas(monkeypatch)
    monkeypatch.setattr(
        "symphony_logic.fetch_symphony_score", lambda *a, **k: _frontrunner_incumbent()
    )
    monkeypatch.setattr(fbld, "_build_client", lambda: _mocked_fable_overlay_client())
    store = MagicMock(return_value=1)
    monkeypatch.setattr("database.insert_frontrunner_proposal", store)
    return store


def _frontrunner_outcome(store: MagicMock) -> dict:
    return {"proposals_queued": store.call_count, **db_outcome()}


class _InlineExecutor:
    """Stands in for the build executor: the submitted build finishes inside the request."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def run_frontrunner_route(monkeypatch, tmp_path) -> SurfaceRun:
    """``POST /ai-advisor/frontrunner-builder/run`` for one live symphony with a detected cascade
    and one generated overlay, the build run inline."""
    import app as app_module  # noqa: PLC0415

    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    store = _frontrunner_seams(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-slippage-surfaces-key")
    monkeypatch.setattr(app_module, "_FRONTRUNNER_BUILD_EXECUTOR", _InlineExecutor())
    with _flask_client() as client:
        response = client.post(
            "/ai-advisor/frontrunner-builder/run", json={"symphony_ids": [FRONTRUNNER_SYMPHONY_ID]}
        )
    outcome = {"status": response.status_code, **_frontrunner_outcome(store)}
    return SurfaceRun(router, outcome)


def run_frontrunner_weekly(monkeypatch, tmp_path) -> SurfaceRun:
    """The weekly build's frontrunner stage for one live symphony; the objective builds are
    stubbed so only the frontrunner requests reach Composer."""
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415
    import advisors.strategy_builder_scheduler as sb_sched  # noqa: PLC0415

    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch, response_body=GATE_RESPONSE)
    store = _frontrunner_seams(monkeypatch)
    state = {FRONTRUNNER_SYMPHONY_ID: {"name": "Maple Ridge Incumbent"}}
    monkeypatch.setattr("database.load_state", lambda: copy.deepcopy(state))
    monkeypatch.setattr(sb_sched, "_already_ran_this_week", lambda: False)
    monkeypatch.setattr(
        sbe,
        "propose_strategies",
        lambda *a, **k: sbe.ProposalRun(
            candidates=[],
            gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
        ),
    )
    sb_sched.run_weekly_build()
    return SurfaceRun(router, _frontrunner_outcome(store))


# ---------------------------------------------------------------------------
# Incubation tick
# ---------------------------------------------------------------------------

INCUBATION_HASH = "slippage-surface-candidate"


def run_incubation_tick(monkeypatch, tmp_path) -> SurfaceRun:
    """The 03:30 incubation tick (the function the slot calls) for one INCUBATING candidate."""
    import advisors.incubation as incubation  # noqa: PLC0415
    import database  # noqa: PLC0415
    from tests.advisors.test_incubation_tick import _admit  # noqa: PLC0415

    _scratch_db(monkeypatch, tmp_path)
    router = install_router(monkeypatch)
    _admit(INCUBATION_HASH, "slippage-surface")
    incubation.run_incubation_tick()
    overview = [
        [row["status"], row["days_observed"]]
        for row in database.get_incubation_overview()
        if row["candidate_hash"] == INCUBATION_HASH
    ]
    return SurfaceRun(router, {"incubation": overview})


# ---------------------------------------------------------------------------
# Tenure review
# ---------------------------------------------------------------------------


def build_tenure_world(monkeypatch, tmp_path) -> h.ComposerRouter:
    """A scratch database, one configured account, a frozen Tuesday 03:45 ET clock, a short
    tenure threshold and one symphony old enough to be backtested; the explainer and the
    recommender stage of the tick are stand-ins. The captured Composer records are served by
    the URL router (the same world as the end to end tenure tests)."""
    import advisors.retirement_explainer as explainer  # noqa: PLC0415
    import app as app_module  # noqa: PLC0415

    jb.block_live_calls(monkeypatch)
    monkeypatch.setattr(app_module, "_retirement_recommender_tick_worker", lambda: None)
    monkeypatch.setattr(explainer, "explain_recommendation", lambda rec: "explainer stand-in")
    jb.use_scratch_db(monkeypatch, tmp_path)
    jb.isolate_env(monkeypatch, tmp_path, accounts=[("ACCOUNT_ROTH", h.ACCOUNT_ID)], threshold="60")
    jb.freeze_app_clock(monkeypatch)
    return jb.install_world(monkeypatch, invested_since={h.BACKTEST_ID: "2026-07-20"})


def run_tenure_tick() -> None:
    """The 03:45 tick, with the thread it starts run on the spot."""
    import app as app_module  # noqa: PLC0415

    with patch("threading.Thread", jb.ImmediateThread):
        app_module._run_retirement_recommender_tick()


def run_tenure_review_route(monkeypatch, tmp_path) -> SurfaceRun:
    """``POST /ai-advisor/retirement/review/run`` with its thread run on the spot."""
    router = build_tenure_world(monkeypatch, tmp_path)
    jb.install_inline_threads(monkeypatch)
    with _flask_client() as client:
        response = client.post("/ai-advisor/retirement/review/run")
    return SurfaceRun(router, {"status": response.status_code, **db_outcome()})


def run_tenure_review_tick(monkeypatch, tmp_path) -> SurfaceRun:
    router = build_tenure_world(monkeypatch, tmp_path)
    run_tenure_tick()
    return SurfaceRun(router, db_outcome())


def advisor_observation_rows() -> list[tuple]:
    """Every stored advisory row in full, oldest first."""
    import database  # noqa: PLC0415

    conn = database.get_connection()
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM advisor_observations ORDER BY id")]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------

# Every entry point of the plan's matrix, in the order of its columns.
RUNNERS: dict[str, Callable] = {
    "strategy_builder_route": run_strategy_builder_route,
    "strategy_builder_weekly": run_strategy_builder_weekly,
    "asset_swaps_route": run_asset_swaps_route,
    "asset_swaps_weekly": run_asset_swaps_weekly,
    "logic_changes_route": run_logic_changes_route,
    "logic_changes_weekly": run_logic_changes_weekly,
    "frontrunner_route": run_frontrunner_route,
    "frontrunner_weekly": run_frontrunner_weekly,
    "incubation_tick": run_incubation_tick,
    "tenure_review_route": run_tenure_review_route,
    "tenure_review_tick": run_tenure_review_tick,
}

# The entry points whose run sends an SPY baseline request.
SPY_BASELINE_ENTRY_POINTS = (
    "strategy_builder_route",
    "strategy_builder_weekly",
    "asset_swaps_route",
    "asset_swaps_weekly",
    "logic_changes_route",
    "logic_changes_weekly",
    "incubation_tick",
)

GOLDEN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "backtest_slippage_calibration" / "request_sequences.json"
)


def _tree_digest(tree: dict) -> str:
    text = json.dumps(tree, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def golden_view(run: SurfaceRun) -> dict:
    """What the S-3.14 golden holds for one entry point.

    Per request: the URL, the body form, every body field except the tree and the slippage
    (capital, fee flags, broker, version, dates: small and read as they are) and, for the tree,
    its ids-masked SHA-256 with its ticker set. The trees of a real symphony run to 100 KB, so
    the golden holds their digest and not their text; equal digests mean equal trees.
    """
    requests = []
    for entry in masked_sequence(run.router):
        body = entry["body"]
        tree = body["symphony"]["raw_value"]
        requests.append(
            {
                "url": entry["url"],
                "body_form": entry["body_form"],
                "fields": {key: value for key, value in body.items() if key != "symphony"},
                "tree_sha256": _tree_digest(tree),
                "tree_tickers": sorted(
                    {n["ticker"] for n in sbh.walk(tree) if isinstance(n.get("ticker"), str)}
                ),
            }
        )
    return {"requests": requests, "outcome": run.outcome}


def tracked_python_sources() -> list[Path]:
    """Every tracked production ``*.py`` (tests excluded), from ``git ls-files``."""
    out = subprocess.run(
        ["git", "ls-files", "--", "*.py", ":(exclude)tests", ":(exclude).claude"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return [REPO_ROOT / name for name in out if name]


UPDATE_OR_DELETE_OF_OBSERVATIONS = re.compile(
    r"\b(UPDATE\s+advisor_observations|DELETE\s+FROM\s+advisor_observations)\b", re.IGNORECASE
)
