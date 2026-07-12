"""RED tests — deep-tree / composition-hash exception containment for
advisors/community_strats.py (DE-ATLAS-DEEP-TREE-001).

THE DEFECT (PM-verified via direct reproduction, see .claude/tdd-handoff.md):
  `_composition_hash()` (community_strats.py ~101-113) calls `_strip_ids()`, a
  RECURSIVE Python function — unlike `advisors/symphony_schema.py`'s traversal,
  which is deliberately ITERATIVE (explicit stack; see test_symphony_schema.py's
  own depth-230/depth-300 "no RecursionError" tests). `_strip_ids` genuinely
  raises `RecursionError` on a pathologically deep (but otherwise STRUCTURALLY
  VALID — validate_tree returns []) tree, confirmed empirically at ~500 levels
  of nesting on the reference machine.

  The call site (`load_community_strategies`'s per-doc parse loop, ~line 365)
  has NO try/except around `_composition_hash(tree)`, unlike every OTHER step
  in that same loop (json.loads / validate_tree / extract_tickers all have
  their own individual try/except + a stats counter + `continue`). A
  RecursionError (or any other exception — e.g. MemoryError, grouped with
  RecursionError as a per-doc composition-hash failure mode) from one
  pathological doc propagates UNCAUGHT out of `load_community_strategies`,
  violating the D-1 never-raises contract AND aborting the ENTIRE batch — not
  just the one bad doc. Confirmed via direct reproduction: a 2-doc batch
  (one normal, one pathologically deep) loses BOTH candidates, not just the
  deep one.

THE FIX (PM-approved, comprehensive containment): wrap the per-doc loop body's
  composition-hash step in its own try/except (matching the pattern already
  used for the other 3 steps in the same loop) so ANY exception there — not
  just RecursionError — drops that one doc (counted in an accounting bucket)
  and the loop continues. This is the robust minimal fix; the PM explicitly
  does NOT also require `_strip_ids` to be made iterative (a rare
  pathologically-deep doc is acceptable latent-risk containment, not current
  data loss — the live gate proved the real top-N ranked docs don't approach
  this depth).

ADVERSARIAL FOCUS:
  - test_pathologically_deep_tree_does_not_abort_the_whole_batch: a REAL
    pathologically-deep tree (no mock/seam) built the SAME way as
    test_symphony_schema.py's own depth-bomb fixture (unique uuid4 per node,
    so validate_tree passes clean and the doc reaches _composition_hash
    rather than being caught by an earlier, already-protected step). Does
    NOT assert whether the pathological doc itself survives (admitted) or is
    dropped (parse_failed-style) — both are PM-sanctioned outcomes depending
    on the exact fix; only that the OTHER doc in the batch always survives
    and the call never raises.
  - test_composition_hash_memory_error_does_not_abort_the_whole_batch:
    MemoryError via a monkeypatch seam, NOT a real OOM condition — this
    project has crashed its host from genuine memory exhaustion before (see
    CLAUDE.md's memory-cap gotchas); constructing a real OOM in a test suite
    is unsafe. Proves the containment contract holds for ANY exception type
    from this step, not just RecursionError specifically — this is what
    requires the COMPREHENSIVE per-doc try/except fix (an iterative-only
    `_strip_ids` would not protect against an arbitrary injected exception).

No live network calls. No production DB writes. No @pytest.mark.live.
"""

from __future__ import annotations

import ast
import json
import pathlib
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Deep-tree fixture builder — same construction idiom as
# tests/advisors/test_symphony_schema.py's depth-bomb tests (unique uuid4 per
# node so validate_tree passes clean; a shared/fixed id produces a spurious
# "duplicate node id" hard error that gets caught by an earlier, already-
# protected step, never reaching the composition-hash bug under test).
# ---------------------------------------------------------------------------


def _make_pathologically_deep_tree(depth: int, ticker: str) -> dict:
    """Return a structurally VALID (validate_tree == []) tree nested `depth`
    levels deep via `wt-cash-equal` wrapper nodes around a single asset leaf.

    depth=700 is calibrated (confirmed via direct reproduction, not guessed):
    genuinely exceeds `_strip_ids`'s recursive ceiling (~500 levels on the
    reference machine) while remaining safely within `json.dumps`/`json.loads`'
    own (much higher, C-accelerated) recursion tolerance — so this fixture
    exercises `_composition_hash` specifically, not an earlier step in the
    parse loop (json.loads / validate_tree / extract_tickers all tolerate
    this depth fine, matching symphony_schema's own iterative-traversal
    contract).
    """
    inner: dict = {
        "step": "asset",
        "ticker": ticker,
        "name": "",
        "exchange": "NYSE",
        "id": str(uuid.uuid4()),
    }
    current = inner
    for _ in range(depth):
        current = {
            "step": "wt-cash-equal",
            "id": str(uuid.uuid4()),
            "children": [current],
        }
    return {
        "step": "root",
        "name": "Pathologically Deep",
        "rebalance": "daily",
        "id": str(uuid.uuid4()),
        "children": [current],
    }


_DEEP_TREE_DEPTH = 700


def _make_normal_doc(sid: str, ticker: str) -> dict:
    """Return a well-formed, shallow doc (real Mongo shape) unrelated to the
    deep-tree defect — used as the 'must always survive' control candidate.
    """
    from advisors import symphony_schema as ss

    tree = ss.make_root(f"Normal-{sid}", "daily", [ss.make_weight_equal([ss.make_asset(ticker)])])
    return {
        "sid": sid,
        "name": f"Normal {sid}",
        "edn_string": json.dumps(tree),
        "oos_metrics": {"Sharpe": "1.0"},
    }


def _make_deep_doc(sid: str, ticker: str, depth: int = _DEEP_TREE_DEPTH) -> dict:
    """Return a doc whose edn_string is a pathologically deep (but structurally
    valid) tree — the doc that should trigger (or, post-fix, safely absorb)
    the composition-hash exception.
    """
    deep_tree = _make_pathologically_deep_tree(depth, ticker)
    return {
        "sid": sid,
        "name": f"Deep {sid}",
        "edn_string": json.dumps(deep_tree),
        "oos_metrics": {"Sharpe": "2.0"},
    }


@pytest.fixture()
def mod():
    """The module under test. No cache-DB isolation needed: every test here
    bypasses atlas_cache.cached_pull entirely (return_value=<docs>).
    """
    from advisors import community_strats

    return community_strats


# ---------------------------------------------------------------------------
# The decisive containment tests
# ---------------------------------------------------------------------------


class TestDeepTreeExceptionContainment:
    """A per-doc failure in composition-hash computation must never abort the
    whole batch — the same containment already given to json.loads/
    validate_tree/extract_tickers failures in the same loop.
    """

    def test_pathologically_deep_tree_does_not_abort_the_whole_batch(self, mod):
        """A REAL pathologically-deep (depth=700), structurally-valid tree must
        not crash load_community_strategies, and the OTHER, normal doc in the
        same batch must always survive.

        Calibration note: this depth is empirically confirmed (not assumed) to
        exceed community_strats._strip_ids's recursive ceiling while staying
        well inside json.dumps/json.loads' own tolerance — see the module
        docstring and _make_pathologically_deep_tree's docstring.
        """
        normal_doc = _make_normal_doc("normal", "SPY")
        deep_doc = _make_deep_doc("deep", "QQQ")

        with patch("advisors.atlas_cache.cached_pull", return_value=[normal_doc, deep_doc]):
            try:
                result = mod.load_community_strategies()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"load_community_strategies raised {type(exc).__name__}: {exc} — "
                    "a pathologically deep (but structurally valid) tree in ONE doc "
                    "must never propagate out of the loader (D-1 never-raises); it "
                    "must also not abort processing of the OTHER, well-formed doc "
                    "in the same batch."
                )

        assert result["available"] is True, f"expected available=True; got {result!r}"
        sids = {c["sid"] for c in result["candidates"]}
        assert "normal" in sids, (
            "the well-formed doc must still be returned even though a LATER doc "
            "in the same batch is pathologically deep"
        )

        # Whether 'deep' itself is admitted (an iterative-_strip_ids-style fix
        # would succeed on it too) or dropped (a per-doc try/except fix would
        # count it as a failure and continue) is an implementation choice PM
        # sanctioned either way — not asserted here. What IS asserted: every
        # pulled doc lands in exactly one accounting bucket (the sum invariant
        # already enforced elsewhere in this test suite — see
        # test_community_strats.py::TestStatsInvariant).
        stats = result["stats"]
        assert stats["pulled"] == 2, f"expected stats['pulled']=2; got {stats['pulled']}"
        accounted = (
            stats["valid"]
            + stats["deduped"]
            + stats["missing_edn_string"]
            + stats["parse_failed"]
            + stats["validate_rejected"]
            + stats["sharpe_filtered"]
        )
        assert accounted == 2, (
            f"stats sum invariant: pulled=2 but accounted={accounted} ({stats!r}) — "
            "every pulled doc (including the pathologically deep one) must land in "
            "exactly one accounting bucket, whichever containment fix is used."
        )

    def test_composition_hash_memory_error_does_not_abort_the_whole_batch(self, mod, monkeypatch):
        """MemoryError is grouped with RecursionError as a per-doc composition-hash
        failure mode that must not abort the whole load. Constructing a REAL
        MemoryError condition in a test is unsafe (this project has crashed its
        host from genuine memory exhaustion before); this test simulates it via
        a monkeypatch seam instead, proving the containment contract holds for
        ANY exception type from this step — not just RecursionError. An
        iterative-only _strip_ids fix provides no protection here, since this
        seam bypasses _strip_ids entirely; this test requires the comprehensive
        per-doc try/except containment (PM-approved fix shape).
        """
        normal_doc = _make_normal_doc("normal", "SPY")
        bad_doc = _make_normal_doc("bad", "QQQ")  # shallow, well-formed tree

        real_composition_hash = mod._composition_hash

        def _raising_composition_hash(tree):
            # Distinguish the 'bad' doc's tree by its root name (set by
            # _make_normal_doc via symphony_schema.make_root("Normal-bad", ...)).
            if tree.get("name") == "Normal-bad":
                raise MemoryError("simulated OOM during composition-hash computation")
            return real_composition_hash(tree)

        monkeypatch.setattr(mod, "_composition_hash", _raising_composition_hash)

        with patch("advisors.atlas_cache.cached_pull", return_value=[normal_doc, bad_doc]):
            try:
                result = mod.load_community_strategies()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"load_community_strategies raised {type(exc).__name__}: {exc} — "
                    "a MemoryError from one doc's composition-hash computation must "
                    "not propagate (D-1 never-raises), and must not abort the OTHER "
                    "doc in the same batch."
                )

        assert result["available"] is True, f"expected available=True; got {result!r}"
        sids = {c["sid"] for c in result["candidates"]}
        assert "normal" in sids, "the well-formed doc must survive a sibling doc's MemoryError"
        assert "bad" not in sids, (
            "the doc whose composition-hash computation raised MemoryError must be "
            "dropped, not silently (and incorrectly) included with no hash"
        )
        stats = result["stats"]
        assert stats["pulled"] == 2, f"expected stats['pulled']=2; got {stats['pulled']}"


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (see the identical guard + rationale in the sibling files in this directory).
# ---------------------------------------------------------------------------


def test_no_importlib_reload_in_this_test_module():
    """Guard: this test module must not call importlib.reload (memory-leak anti-pattern).

    Detected via AST (a call to an attribute named 'reload'), so a docstring or comment
    mentioning the word does not trip it. importlib.import_module is allowed.
    """
    module_path = pathlib.Path(__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "reload":
                offenders.append(node.lineno)

    assert not offenders, (
        "importlib.reload(...) found in test_community_strats_deep_tree_robustness.py "
        f"at lines {offenders} — this is a per-test memory-leak anti-pattern. Patch the "
        "relevant seam directly instead of reloading."
    )
