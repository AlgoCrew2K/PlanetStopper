"""RED — save_state must persist a trigger-cycle bot_state containing numpy scalars.

THE BUG (VERDICT-droplet.md Finding 1, CRITICAL, observed live 2026-07-09):
  database.save_state (database.py:296) serialises bot_state with a plain
  ``json.dumps`` — no numpy-aware ``default=``. On 2026-07-09 13:48-13:50 UTC a
  numpy int64 reached bot_state on a Take-Profit path and every save crashed
  ``TypeError: Object of type int64 is not JSON serializable``. Each failed save
  lost that cycle's ``triggered=True``; the next cycle reloaded pre-trigger state
  and RE-FIRED the same exit — four duplicate exit_triggers rows for one symphony
  in one morning, all sharing the stale cycle_id frozen at the last good save.
  In LIVE_EXECUTION mode that loop is up to four duplicate sell submissions.

CONTRACT PINNED:
  save_state accepts a bot_state whose leaves may be numpy scalar types
  (np.integer -> int, np.floating -> float, np.bool_ -> bool) anywhere in the
  nested structure, persists without raising, and load_state round-trips the
  coerced values. The ``triggered`` flag in particular must survive persistence
  — that is what stops the re-fire loop.

FIXTURE PROVENANCE (captured-from-producer):
  tests/fixtures/prod_droplet/ — real droplet data captured 2026-07-09 at
  deployed SHA 0bcbd1a (see capture_from_droplet.py for the scp provenance).
  The bot_state entry shape and the duplicate-trigger evidence cluster are both
  read from those files at runtime; no synthetic state shapes, no hardcoded ids.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import numpy as np
import pytest

import database

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "prod_droplet"


@pytest.fixture(scope="module")
def exit_trigger_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "exit_triggers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def positions() -> dict:
    return json.loads((_FIXTURE_DIR / "bot_state_positions.json").read_text(encoding="utf-8"))


def _refire_clusters(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group exit_triggers rows by (symphony_id, ET date) and keep groups > 1.

    A per-(symphony, day) duplicate is the observable damage of the save_state
    crash: the engine can only re-fire an exit when the triggered flag was lost.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["symphony_id"], r["ts_et"][:10]), []).append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _crashed_symphony_id(rows: list[dict]) -> str:
    """The symphony whose exit re-fired — derived from the fixture, not hardcoded."""
    clusters = _refire_clusters(rows)
    assert clusters, "fixture integrity: expected the production re-fire cluster"
    return next(iter(clusters))[0]


def _trigger_cycle_state(positions: dict, sym_id: str) -> dict:
    """Rebuild the real post-trigger bot_state entry from the captured snapshot.

    Every key present here was written by the production engine on the crash
    day — this is the exact state shape save_state must be able to persist.
    """
    entry = dict(positions[sym_id])
    entry["triggered"] = True
    return {sym_id: entry}


# ---------------------------------------------------------------------------
# Fixture-integrity anchor: the production damage this RED is built on.
# ---------------------------------------------------------------------------


def test_fixture_contains_the_production_refire_cluster(exit_trigger_rows):
    """The captured exit_triggers table must show exactly one re-fire cluster:
    one symphony with multiple same-day rows, all sharing one stale cycle_id
    (frozen at the last successful save — the crash signature).

    Guards the fixture against accidental edits that would hollow out the suite.
    """
    clusters = _refire_clusters(exit_trigger_rows)
    assert len(clusters) == 1, (
        f"expected exactly the one production re-fire cluster, found {len(clusters)}"
    )
    rows = next(iter(clusters.values()))
    assert len(rows) >= 2
    stale_cycle_ids = {r["cycle_id"] for r in rows}
    assert len(stale_cycle_ids) == 1, (
        "re-fire rows must share ONE stale cycle_id (state frozen at last good save); "
        f"got {stale_cycle_ids}"
    )
    reasons = {r["triggered_reason"] for r in rows}
    assert len(reasons) == 1, f"one exit re-firing means one reason; got {reasons}"


# ---------------------------------------------------------------------------
# The crash: numpy scalars anywhere in bot_state must not break persistence.
# ---------------------------------------------------------------------------


class TestSaveStatePersistsNumpyScalars:
    @pytest.mark.parametrize(
        ("field", "np_value", "coerce"),
        [
            # The audit's candidate injection site: triggered_at_stop is set from
            # item["attempted_level"] (alpha_bot_execution.py:1812), a math_engine
            # product that can be a numpy float.
            ("triggered_at_stop", np.float64(0.6), float),
            # Tick counters flow through numpy comparisons — the int64 class that
            # crashed production.
            ("vwap_ticks", np.int64(3), int),
            ("vwap_bleed_ticks", np.int32(2), int),
            ("above_tp_count", np.int64(5), int),
            ("mc_prob", np.float32(41.2), float),
            ("para_armed", np.bool_(True), bool),
        ],
    )
    def test_save_state_persists_trigger_state_with_numpy_scalar_at_engine_site(
        self, positions, exit_trigger_rows, field, np_value, coerce
    ):
        """save_state must not raise on a numpy scalar at a real engine write site,
        and load_state must return the coerced native value.

        RED today: plain json.dumps raises TypeError for every case here — the
        exact production crash.
        """
        sym_id = _crashed_symphony_id(exit_trigger_rows)
        state = _trigger_cycle_state(positions, sym_id)
        state[sym_id][field] = np_value

        database.save_state(state)  # must not raise TypeError
        loaded = database.load_state()

        expected = coerce(np_value)
        got = loaded[sym_id][field]
        if isinstance(expected, bool):
            assert got is expected
        elif isinstance(expected, float):
            # exact: float(np scalar) -> IEEE double -> JSON -> same double
            assert got == pytest.approx(expected, rel=0, abs=0.0)
        else:
            assert got == expected

    def test_triggered_flag_survives_persistence_when_numpy_scalar_present(
        self, positions, exit_trigger_rows
    ):
        """The production consequence: a failed save loses triggered=True and the
        next cycle re-fires the exit. With a numpy int64 in the state, the flag
        must still round-trip — this is the assertion that stops the re-fire loop.
        """
        sym_id = _crashed_symphony_id(exit_trigger_rows)
        state = _trigger_cycle_state(positions, sym_id)
        state[sym_id]["vwap_ticks"] = np.int64(7)

        database.save_state(state)
        loaded = database.load_state()

        assert loaded[sym_id]["triggered"] is True, (
            "triggered=True was lost across save/load — this is the state loss "
            "that re-fired the same Take-Profit four times on 2026-07-09"
        )
        assert loaded[sym_id]["triggered_at_return"] == pytest.approx(
            float(state[sym_id]["triggered_at_return"]), rel=0, abs=0.0
        )

    def test_save_state_persists_numpy_scalar_nested_in_holdings_list(
        self, positions, exit_trigger_rows
    ):
        """Numpy scalars nested inside list-of-dict structures (current_holdings
        allocations) must persist too — the sanitiser must recurse, not just fix
        top-level values.
        """
        sym_id = _crashed_symphony_id(exit_trigger_rows)
        state = _trigger_cycle_state(positions, sym_id)
        holdings = state[sym_id].get("current_holdings") or [{"ticker": "SPY", "allocation": 1.0}]
        holdings[0]["allocation"] = np.float64(holdings[0].get("allocation", 1.0))
        state[sym_id]["current_holdings"] = holdings

        database.save_state(state)
        loaded = database.load_state()

        got = loaded[sym_id]["current_holdings"][0]["allocation"]
        assert got == pytest.approx(float(holdings[0]["allocation"]), rel=0, abs=0.0)


# ---------------------------------------------------------------------------
# Property: any mix of numpy scalar leaves round-trips.
# ---------------------------------------------------------------------------

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

_numpy_leaves = st.one_of(
    st.integers(min_value=-(2**31), max_value=2**31 - 1).map(np.int64),
    st.integers(min_value=-(2**15), max_value=2**15 - 1).map(np.int32),
    st.floats(allow_nan=False, allow_infinity=False, width=64).map(np.float64),
    st.booleans().map(np.bool_),
)


class TestNumpyScalarRoundTripProperty:
    @settings(
        max_examples=25,  # persistence property, each example hits SQLite — keep fast
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        leaves=st.dictionaries(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz_",
                min_size=1,
                max_size=12,
            ),
            _numpy_leaves,
            min_size=1,
            max_size=6,
        )
    )
    def test_any_numpy_scalar_mix_in_bot_state_round_trips(self, leaves):
        """PROPERTY: for any dict of numpy scalar leaves merged into a symphony
        entry, save_state must not raise and load_state must return values equal
        to the native coercion (int/float/bool) of each leaf.
        """
        state = {"SYM_PROP": {"triggered": True, **leaves}}

        database.save_state(state)
        loaded = database.load_state()

        assert loaded["SYM_PROP"]["triggered"] is True
        for key, value in leaves.items():
            got = loaded["SYM_PROP"][key]
            if isinstance(value, np.bool_):
                assert got is bool(value)
            elif isinstance(value, np.integer):
                assert got == int(value)
            else:
                assert got == pytest.approx(float(value), rel=0, abs=0.0)
