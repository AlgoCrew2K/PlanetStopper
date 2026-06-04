"""advisor-fix cycle — RED: AC-8 the Run Advisor evaluate routes fetch the
Composer symphony tree by the WRONG identifier (NAME instead of Composer HASH).

LIVE FAILURE (user, 2026-06-04): running "Try a Logic Tweak" on
"(INVEST) Planet of Hunted Cascades - Land of Intelligent Allocations" returned
  "Evaluation error — could not fetch symphony tree for (INVEST) Planet of Hunted
   Cascades - Land of Intelligent Allocations"
i.e. the Run Advisor FAILS end-to-end — which is why llm_suggestions had 0 rows
(the tree never fetched -> no proposal ever produced).

Root cause (file:line verified):
  * templates/ai_advisor_logic_changes.html:695 — the symphony dropdown <option
    value="{{ sym }}"> is the DISPLAY NAME (analytics.list_available_symphonies
    returns post-mortem NAMES, e.g. "(INVEST) Planet of Hunted Cascades ...").
  * app.py:2830 (logic-changes/evaluate) and app.py:2694 (asset-swaps/evaluate)
    pass that NAME straight to fetch_symphony_score(symphony_id).
  * symphony_logic.py:43 — fetch_symphony_score builds
    GET {COMPOSER_BASE_URL}/symphonies/{symphony_id}/score.  The Composer API needs
    the Composer HASH (e.g. "hvPiGP1O7AHfutHE3Fjy"), NOT the name -> non-200 ->
    empty dict -> "could not fetch symphony tree for <NAME>".

TWO DISTINCT IDENTIFIERS: advisor_observations / the DB modal use
normalize_name(name) (the AC-4 work); the COMPOSER API needs the hash.  The fix is
the INVERSE of the AC-4 modal (which resolved hash->name): these evaluate routes
must resolve NAME->Composer-hash (via bot_state) BEFORE calling fetch_symphony_score.

Contract pinned here:
  * The evaluate route resolves a submitted symphony NAME to its Composer HASH and
    calls fetch_symphony_score with the HASH (verified: spy on fetch_symphony_score
    captures the identifier; it must be the hash, not the name).
  * A known symphony (present in bot_state) does NOT return the
    "could not fetch symphony tree" error when its tree is fetchable.

Mocking strategy:
  * fetch_symphony_score is spied (returns a non-empty tree ONLY when called with
    the hash) — no live Composer call.
  * database.load_state injects a known name->hash bot_state.
  * _has_composer_key / propose_operator_logic_change patched — no live engine/network.
  * No live DB, no math-engine mock; function-scoped fixtures.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="function")
def flask_client():
    if "." not in sys.path:
        sys.path.insert(0, ".")
    import app as _app
    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


# Real live mapping (captured READ-ONLY from alphabot_state.db bot_state):
# the dropdown sends this NAME; the Composer API needs this HASH.
_SYM_NAME = "(INVEST) Planet of Hunted Cascades - Land of Intelligent Allocations"
_SYM_HASH = "hvPiGP1O7AHfutHE3Fjy"


@pytest.fixture(scope="function")
def bot_state_with_mapping():
    """A live-shaped bot_state: top-level key = Composer HASH, ['name'] = display NAME."""
    return {_SYM_HASH: {"name": _SYM_NAME, "account_uuid": "acc-1"}}


def _score_tree_only_for_hash(identifier: str) -> dict:
    """Spy stand-in for fetch_symphony_score: returns a non-empty tree ONLY when
    called with the Composer HASH — exactly the live Composer behaviour (the name
    404s -> {} ; the hash resolves)."""
    if identifier == _SYM_HASH:
        return {"id": _SYM_HASH, "name": _SYM_NAME, "children": []}
    return {}  # name (or anything else) -> Composer returns nothing


# ===========================================================================
# AC-8 — logic-changes/evaluate must fetch by Composer HASH, not the NAME.
# ===========================================================================


def test_logic_changes_evaluate_fetches_tree_by_composer_hash(flask_client, bot_state_with_mapping):
    """POST /ai-advisor/logic-changes/evaluate with a symphony NAME must resolve
    that NAME to its Composer HASH and call fetch_symphony_score with the HASH.

    Spies on fetch_symphony_score and asserts the captured identifier is the HASH.
    Today the route passes the NAME -> Composer 404 -> "could not fetch symphony
    tree for <NAME>".
    """
    captured = {"identifier": None}

    def _spy(identifier):
        captured["identifier"] = identifier
        return _score_tree_only_for_hash(identifier)

    # propose_operator_logic_change is patched so the route returns cleanly once the
    # tree fetch succeeds — we only care about the identifier passed to the fetch.
    fake_result = MagicMock()
    fake_result.__dict__ = {"survivors": [], "message": "ok", "no_api_key": False}

    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("symphony_logic.fetch_symphony_score", side_effect=_spy),
        patch("database.load_state", return_value=bot_state_with_mapping),
        patch("advisors.logic_change_engine.propose_operator_logic_change",
              return_value=fake_result),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": _SYM_NAME,  # the dropdown sends the NAME
                "change_description": "change momentum lookback from 20 to 10 days",
            },
        )

    assert resp.status_code == 200
    assert captured["identifier"] is not None, (
        "fetch_symphony_score was never called — the route errored before the tree "
        "fetch (or the name-resolution path is broken)."
    )
    assert captured["identifier"] == _SYM_HASH, (
        f"fetch_symphony_score was called with {captured['identifier']!r}; it MUST "
        f"be the Composer HASH {_SYM_HASH!r}. AC-8: the route must resolve the "
        f"submitted NAME {_SYM_NAME!r} to its Composer hash before fetching the tree."
    )


def test_logic_changes_evaluate_known_symphony_no_tree_fetch_error(flask_client, bot_state_with_mapping):
    """A known symphony (present in bot_state, tree fetchable by hash) must NOT
    return the 'could not fetch symphony tree' error.

    This is the end-to-end repair: the exact live failure the user hit must be gone
    for a symphony that has data.
    """
    fake_result = MagicMock()
    fake_result.__dict__ = {"survivors": [], "message": "ok", "no_api_key": False}

    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("symphony_logic.fetch_symphony_score", side_effect=_score_tree_only_for_hash),
        patch("database.load_state", return_value=bot_state_with_mapping),
        patch("advisors.logic_change_engine.propose_operator_logic_change",
              return_value=fake_result),
    ):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": _SYM_NAME,
                "change_description": "change momentum lookback from 20 to 10 days",
            },
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    err = (data.get("error") or "").lower()
    assert "could not fetch symphony tree" not in err, (
        f"Got the live tree-fetch failure for a KNOWN symphony: {data.get('error')!r}. "
        "AC-8: resolving NAME->hash must make the tree fetch succeed end-to-end."
    )


# ===========================================================================
# AC-8 — asset-swaps/evaluate has the SAME identifier bug.
# ===========================================================================


def test_asset_swaps_evaluate_fetches_tree_by_composer_hash(flask_client, bot_state_with_mapping):
    """POST /ai-advisor/asset-swaps/evaluate with a symphony NAME must also resolve
    NAME->Composer-hash before fetch_symphony_score (same bug, same fix)."""
    captured = {"identifier": None}

    def _spy(identifier):
        captured["identifier"] = identifier
        return _score_tree_only_for_hash(identifier)

    fake_swap = MagicMock()
    fake_swap.__dict__ = {"survivors": [], "message": "ok", "no_api_key": False}

    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch("symphony_logic.fetch_symphony_score", side_effect=_spy),
        patch("database.load_state", return_value=bot_state_with_mapping),
        patch("advisors.asset_swap_engine.propose_operator_swap", return_value=fake_swap),
    ):
        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={
                "symphony_id": _SYM_NAME,
                "from_ticker": "SPY",
                "to_ticker": "QQQ",
            },
        )

    assert resp.status_code == 200
    # If the route resolved + fetched, the identifier must be the hash. If the
    # route short-circuits before fetch for another reason, surface that.
    assert captured["identifier"] is not None, (
        "fetch_symphony_score was never called on asset-swaps/evaluate — the route "
        "errored before the tree fetch."
    )
    assert captured["identifier"] == _SYM_HASH, (
        f"asset-swaps/evaluate called fetch_symphony_score with {captured['identifier']!r}; "
        f"it MUST be the Composer HASH {_SYM_HASH!r} (resolve NAME->hash first). "
        "Same AC-8 bug as logic-changes/evaluate."
    )
