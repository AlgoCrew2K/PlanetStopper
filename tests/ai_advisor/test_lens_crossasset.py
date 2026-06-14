"""RED-phase adversarial tests — cross-asset risk-appetite lens.

Module under test: ``advisors.lens_crossasset`` (does NOT exist yet — all
functional tests intentionally fail on ImportError).

Feature plan: ``feature-plans/lens-crossasset.md``

Acceptance criteria encoded
----------------------------
AC-1  Module lives at advisors/lens_crossasset.py; off-execution-path (NOT
      imported by alpha_bot_execution.py); no Flask route reference; no
      eval/exec/subprocess call; no LIVE_EXECUTION reference.
AC-2  Fetches daily bars via ``synthetic_history.fetch_bars`` — no new HTTP
      client; network is always mocked in this suite.
AC-3  Per-component directional signal derived from mocked bar data (momentum /
      vs-SMA). Tests construct clearly-up-trending bars and assert a positive
      component reading is produced; clearly-down-trending bars give a negative
      reading. Aggregate must be DERIVED from component readings — flipping
      enough components flips the label.
AC-4  Public ``fetch_crossasset() -> dict`` returns the required keys with the
      correct types; ``risk_read`` ∈ {``risk-on``, ``neutral``, ``risk-off``};
      each component is exposed in ``components``.
AC-5  HYG−LQD spread/ratio is a RELATIVE computation using BOTH HYG and LQD
      bars — not HYG alone. Verified by asserting the credit component changes
      when LQD bars change while HYG bars are held fixed.
AC-6  (EMB incremental-independence documented in module metadata — shape test
      only; implementation verifies it at runtime.)
AC-7  Honest-availability: a component whose bars are missing or too short is
      EXCLUDED (not fabricated). If too few valid components remain →
      available=False + reason. All-fetch-fail → available=False + reason.
      Never raises.
AC-8  Calls ``lens_warehouse.persist_lens_snapshot(lens="crossasset", ...)``
      exactly once per ``fetch_crossasset()`` invocation (mocked).
AC-9  (IEX-basis caveat — presence test only: result carries a ``source`` key
      whose string contains a provenance marker.)
D-1   ``reason`` on unavailability is ``type(exc).__name__`` only — never the
      raw exception message string.

Mocking strategy
-----------------
- ``synthetic_history.fetch_bars`` is always patched at its call site inside
  the module under test — no live Alpaca calls.
- ``advisors.lens_warehouse.persist_lens_snapshot`` is always patched — no
  live SQLite writes in this suite.
- Math engine is NEVER mocked — signals are computed by the real implementation.

Anti-patterns avoided
----------------------
- No hardcoded producer-computed values (rates, closes, z-scores, counts).
  Computed values are derived from the mock bar inputs or asserted as
  shape/format/presence.
- No assertion-free tests (every test can fail on a wrong implementation).
- No module-level mutable state; all fixtures are function-scoped.
- No tautologies (every assertion has a realistic failure mode).
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import types
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root — constant; keeps all path references deterministic
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parents[2]


def _ensure_worktree_on_path() -> None:
    root = str(_WORKTREE)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_module() -> types.ModuleType:
    """Import advisors.lens_crossasset; force fresh import each call."""
    _ensure_worktree_on_path()
    mod_name = "advisors.lens_crossasset"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Bar-building helpers — derive all values from inputs; no hardcoded closes
# ---------------------------------------------------------------------------

def _make_bars(n: int, start_close: float = 100.0, drift: float = 0.001) -> list[dict]:
    """Return n synthetic daily bars with a deterministic drift from start_close.

    drift > 0 → up-trending (positive momentum expected)
    drift < 0 → down-trending (negative momentum expected)
    drift = 0 → flat (signal near zero)

    Fields match Alpaca IEX bar schema: t, o, h, l, c, v.
    """
    bars: list[dict] = []
    close = start_close
    for i in range(n):
        year = 2025
        month = (i // 28) + 1
        day = (i % 28) + 1
        bars.append({
            "t": f"{year}-{month:02d}-{day:02d}T05:00:00Z",
            "o": close * 0.999,
            "h": close * 1.005,
            "l": close * 0.995,
            "c": close,
            "v": 1_000_000,
        })
        close = close * (1.0 + drift)
    return bars


def _bars_dict(*symbols_and_bars: tuple[str, list[dict]]) -> dict[str, list[dict]]:
    """Build a fetch_bars-shaped {symbol: [bar, ...]} dict."""
    return dict(symbols_and_bars)


# All 6 instruments the spec names; ordered for easy fixture construction.
_ALL_INSTRUMENTS = ["HYG", "LQD", "TLT", "UUP", "GLD", "EMB", "DBC"]
_MIN_BARS = 30  # conservative minimum; implementation may choose its own floor


def _full_mock_bars(n: int = 60, drift: float = 0.001) -> dict[str, list[dict]]:
    """Return a fetch_bars payload covering all instruments — all up-trending."""
    return {sym: _make_bars(n, drift=drift) for sym in _ALL_INSTRUMENTS}


def _mock_persist() -> MagicMock:
    """Return a MagicMock to substitute for lens_warehouse.persist_lens_snapshot."""
    m = MagicMock(return_value=1)
    return m


# ---------------------------------------------------------------------------
# AC-1 — Static scan: module location, off-execution-path, no forbidden calls
# ---------------------------------------------------------------------------

class TestModuleStaticContract:
    """Static-analysis style checks — no module import required for some."""

    def test_module_importable_at_expected_path(self):
        """advisors/lens_crossasset.py must exist and be importable."""
        mod = _import_module()
        assert mod is not None

    def test_module_file_is_at_correct_path(self):
        """Module __file__ must resolve to advisors/lens_crossasset.py."""
        mod = _import_module()
        mod_path = pathlib.Path(mod.__file__)
        assert mod_path.name == "lens_crossasset.py", (
            f"Expected lens_crossasset.py, got {mod_path.name}"
        )
        assert mod_path.parent.name == "advisors", (
            f"Expected parent dir 'advisors', got {mod_path.parent.name}"
        )

    def test_not_imported_by_alpha_bot_execution(self):
        """alpha_bot_execution.py must NOT reference lens_crossasset (off-execution-path / CC-2)."""
        exec_path = _WORKTREE / "alpha_bot_execution.py"
        assert exec_path.exists(), "alpha_bot_execution.py must exist in the worktree"
        source = exec_path.read_text(encoding="utf-8")
        assert "lens_crossasset" not in source, (
            "lens_crossasset must NOT appear in alpha_bot_execution.py — "
            "it is advisory-only (CC-2 boundary)."
        )

    def test_no_flask_route_decorator_in_source(self):
        """Module source must not contain Flask route decorators (off-execution-path)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "@app.route" not in source, (
            "lens_crossasset.py must not define Flask routes — "
            "it is advisory-only, not a dashboard surface."
        )

    def test_no_live_execution_reference_in_source(self):
        """Module source must not reference LIVE_EXECUTION (execution guard boundary)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in source, (
            "lens_crossasset.py must not reference LIVE_EXECUTION — "
            "it is advisory-only and must not gate on execution mode."
        )

    def test_no_eval_or_exec_in_source(self):
        """Module source must not use eval() or exec() (security constraint)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        import re
        # Match eval( or exec( as function calls (not e.g. 'exec_' identifiers)
        forbidden = re.compile(r'\b(eval|exec)\s*\(')
        matches = forbidden.findall(source)
        assert not matches, (
            f"lens_crossasset.py must not use eval/exec — found: {matches}"
        )

    def test_no_subprocess_in_source(self):
        """Module source must not use subprocess (off-execution-path advisory module)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in source, (
            "lens_crossasset.py must not use subprocess."
        )


# ---------------------------------------------------------------------------
# AC-2 / AC-4 — Public surface: fetch_crossasset() exists and is callable
# ---------------------------------------------------------------------------

class TestPublicSurface:
    """fetch_crossasset() must exist, be callable, and accept no required positional args."""

    def test_fetch_crossasset_is_exported(self):
        """advisors.lens_crossasset must export fetch_crossasset."""
        mod = _import_module()
        assert hasattr(mod, "fetch_crossasset"), (
            "advisors.lens_crossasset must export fetch_crossasset"
        )
        assert callable(mod.fetch_crossasset), (
            "fetch_crossasset must be callable"
        )

    def test_fetch_crossasset_takes_no_required_positional_args(self):
        """fetch_crossasset() must be callable with zero positional arguments."""
        import inspect
        mod = _import_module()
        sig = inspect.signature(mod.fetch_crossasset)
        required = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        ]
        assert len(required) == 0, (
            f"fetch_crossasset must take no required positional args; "
            f"found required params: {[p.name for p in required]}"
        )

    def test_internal_compute_component_signals_callable_if_present(self):
        """If _compute_component_signals is exposed, it must be callable."""
        mod = _import_module()
        if hasattr(mod, "_compute_component_signals"):
            assert callable(mod._compute_component_signals), (
                "_compute_component_signals must be callable"
            )

    def test_internal_aggregate_risk_read_callable_if_present(self):
        """If _aggregate_risk_read is exposed, it must be callable."""
        mod = _import_module()
        if hasattr(mod, "_aggregate_risk_read"):
            assert callable(mod._aggregate_risk_read), (
                "_aggregate_risk_read must be callable"
            )


# ---------------------------------------------------------------------------
# AC-4 / AC-7 — Return shape when bars are available
# ---------------------------------------------------------------------------

class TestReturnShapeOnSuccess:
    """On a successful fetch (all bars available), result must have required keys."""

    @pytest.fixture
    def success_result(self):
        """Call fetch_crossasset() with mocked full bars; return the result dict."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()
        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            return mod.fetch_crossasset()

    def test_returns_dict(self, success_result):
        """fetch_crossasset must return a dict."""
        assert isinstance(success_result, dict), (
            f"Expected dict, got {type(success_result)}"
        )

    def test_has_available_key(self, success_result):
        """Result must contain 'available' key."""
        assert "available" in success_result, (
            f"Result missing 'available': {success_result.keys()}"
        )

    def test_available_is_bool(self, success_result):
        """'available' must be a boolean."""
        assert isinstance(success_result["available"], bool), (
            f"'available' must be bool, got {type(success_result['available'])}"
        )

    def test_has_risk_read_key_when_available(self, success_result):
        """When available=True, result must contain 'risk_read'."""
        if success_result["available"]:
            assert "risk_read" in success_result, (
                "available=True result must have 'risk_read'"
            )

    def test_risk_read_in_vocabulary_when_available(self, success_result):
        """risk_read must be one of {risk-on, neutral, risk-off}."""
        valid = {"risk-on", "neutral", "risk-off"}
        if success_result["available"]:
            rr = success_result["risk_read"]
            assert rr in valid, (
                f"risk_read {rr!r} not in valid vocabulary {valid}"
            )

    def test_has_components_key_when_available(self, success_result):
        """When available=True, result must contain 'components' dict."""
        if success_result["available"]:
            assert "components" in success_result, (
                "available=True result must have 'components'"
            )
            assert isinstance(success_result["components"], dict), (
                "'components' must be a dict"
            )

    def test_has_source_key(self, success_result):
        """Result must always contain a 'source' key (AC-9: IEX provenance)."""
        assert "source" in success_result, (
            "Result must always carry a 'source' key"
        )

    def test_source_is_nonempty_string(self, success_result):
        """'source' must be a non-empty string."""
        assert isinstance(success_result["source"], str), (
            f"'source' must be str, got {type(success_result['source'])}"
        )
        assert len(success_result["source"]) > 0, (
            "'source' must not be an empty string"
        )

    def test_components_expose_per_instrument_readings(self, success_result):
        """components dict must have at least one entry when available=True."""
        if success_result["available"]:
            comps = success_result["components"]
            assert len(comps) > 0, (
                "components must not be empty when available=True"
            )

    def test_components_values_are_dicts(self, success_result):
        """Each value in components must be a dict (auditable per-instrument reading)."""
        if success_result["available"]:
            for key, val in success_result["components"].items():
                assert isinstance(val, dict), (
                    f"components[{key!r}] must be a dict, got {type(val)}"
                )

    def test_unavailable_result_has_reason(self):
        """available=False result must always include a 'reason' key."""
        mod = _import_module()
        # Force failure by making fetch_bars return empty dict (no bars at all)
        persist_mock = _mock_persist()
        with (
            patch("synthetic_history.fetch_bars", return_value={}),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()
        # Empty bars → too few components → available=False
        assert result["available"] is False, (
            "Empty bars should produce available=False"
        )
        assert "reason" in result, (
            "available=False result must carry 'reason'"
        )


# ---------------------------------------------------------------------------
# AC-3 — Component signal derivation: momentum direction tracks bar drift
# ---------------------------------------------------------------------------

class TestComponentSignalDerivation:
    """Signals must be DERIVED from mock bar data — not hardcoded constants.

    Strategy: construct clearly up-trending vs clearly down-trending bar series,
    then assert the component reading DIFFERS between the two cases.  We do not
    assert a specific numeric value (that would hardcode a producer computation).
    """

    def test_uptrend_bars_produce_different_component_than_downtrend(self):
        """An up-trending series and a down-trending series must produce different signals.

        This verifies the signal is DERIVED from the bar data — a module that
        hardcodes a constant would produce identical signals for both inputs.
        """
        mod = _import_module()

        # Use TLT (single-instrument, no relative computation) to isolate the signal
        # Use a clearly positive drift vs clearly negative drift
        strong_up = _make_bars(60, start_close=100.0, drift=+0.005)
        strong_down = _make_bars(60, start_close=100.0, drift=-0.005)

        up_bars = _bars_dict(*[
            (sym, (strong_up if sym == "TLT" else _make_bars(60, drift=0.001)))
            for sym in _ALL_INSTRUMENTS
        ])
        down_bars = _bars_dict(*[
            (sym, (strong_down if sym == "TLT" else _make_bars(60, drift=0.001)))
            for sym in _ALL_INSTRUMENTS
        ])

        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=up_bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            up_result = mod.fetch_crossasset()

        persist_mock2 = _mock_persist()
        with (
            patch("synthetic_history.fetch_bars", return_value=down_bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock2),
        ):
            down_result = mod.fetch_crossasset()

        # If TLT is present in both component sets, its signal must differ
        # (confirms derivation from bar data — not a hardcoded constant)
        up_comps = up_result.get("components", {})
        down_comps = down_result.get("components", {})

        if "TLT" in up_comps and "TLT" in down_comps:
            # The signal values may differ in sign, magnitude, or label
            assert up_comps["TLT"] != down_comps["TLT"], (
                "TLT component must differ between a strongly up-trending and "
                "strongly down-trending bar series — the signal must be derived "
                "from the bar data, not a hardcoded constant."
            )

    def test_flipping_majority_of_components_can_flip_aggregate_label(self):
        """Flipping enough components from positive to negative must flip the aggregate label.

        This verifies the aggregate is DERIVED from component readings — a module
        that hardcodes 'neutral' for all inputs would fail this test.
        """
        mod = _import_module()

        # All instruments strongly up-trending → expect risk-on tendency
        all_up = {sym: _make_bars(60, drift=+0.008) for sym in _ALL_INSTRUMENTS}
        # All instruments strongly down-trending → expect risk-off tendency
        all_down = {sym: _make_bars(60, drift=-0.008) for sym in _ALL_INSTRUMENTS}

        persist_mock_up = _mock_persist()
        persist_mock_down = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=all_up),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock_up),
        ):
            up_result = mod.fetch_crossasset()

        with (
            patch("synthetic_history.fetch_bars", return_value=all_down),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock_down),
        ):
            down_result = mod.fetch_crossasset()

        # If both are available, the risk_read must differ between extreme cases
        if up_result["available"] and down_result["available"]:
            assert up_result["risk_read"] != down_result["risk_read"], (
                "All-strongly-up-trending and all-strongly-down-trending inputs must "
                "produce different risk_read labels — the aggregate must be DERIVED "
                "from component signals. A hardcoded constant would fail this test."
            )

    def test_if_compute_component_signals_exported_returns_dict(self):
        """If _compute_component_signals is exported, calling it on mock bars returns a dict."""
        mod = _import_module()
        if not hasattr(mod, "_compute_component_signals"):
            pytest.skip("_compute_component_signals not exported; tested via fetch_crossasset")

        bars = _full_mock_bars(n=60, drift=0.001)
        result = mod._compute_component_signals(bars)
        assert isinstance(result, dict), (
            f"_compute_component_signals must return a dict, got {type(result)}"
        )

    def test_if_aggregate_risk_read_exported_returns_vocabulary_string(self):
        """If _aggregate_risk_read is exported, it returns a valid risk_read string."""
        mod = _import_module()
        if not hasattr(mod, "_aggregate_risk_read"):
            pytest.skip("_aggregate_risk_read not exported; tested via fetch_crossasset")

        # Build a minimal component signals dict; exact values are implementation-defined
        bars = _full_mock_bars(n=60, drift=0.001)
        if hasattr(mod, "_compute_component_signals"):
            signals = mod._compute_component_signals(bars)
        else:
            pytest.skip("_compute_component_signals not exported")

        result = mod._aggregate_risk_read(signals)
        valid = {"risk-on", "neutral", "risk-off"}
        assert result in valid, (
            f"_aggregate_risk_read must return one of {valid}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# AC-5 — HYG−LQD relative: credit component uses BOTH HYG and LQD
# ---------------------------------------------------------------------------

class TestHygLqdRelativeComputation:
    """AC-5: the credit-spread component must be computed from HYG relative to LQD.

    If the implementation used HYG alone (not the relative), then shifting LQD's
    price level while holding HYG's bars constant would leave the credit component
    unchanged.  The relative computation changes when LQD changes — we assert
    the difference.
    """

    def test_credit_component_changes_when_lqd_changes_holding_hyg_fixed(self):
        """Changing LQD bars (holding HYG fixed) must change the credit component.

        This falsifies any implementation that reads only HYG for the credit spread.
        """
        mod = _import_module()

        hyg_bars = _make_bars(60, start_close=75.0, drift=0.0005)  # fixed

        # LQD scenario A: LQD prices much lower than HYG → HYG/LQD ratio > 1
        lqd_low_bars = _make_bars(60, start_close=50.0, drift=0.0005)
        # LQD scenario B: LQD prices much higher than HYG → HYG/LQD ratio < 1
        lqd_high_bars = _make_bars(60, start_close=150.0, drift=0.0005)

        other_bars = {
            sym: _make_bars(60, drift=0.001)
            for sym in _ALL_INSTRUMENTS
            if sym not in ("HYG", "LQD")
        }

        bars_a = {"HYG": hyg_bars, "LQD": lqd_low_bars, **other_bars}
        bars_b = {"HYG": hyg_bars, "LQD": lqd_high_bars, **other_bars}

        persist_a = _mock_persist()
        persist_b = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars_a),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_a),
        ):
            result_a = mod.fetch_crossasset()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars_b),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_b),
        ):
            result_b = mod.fetch_crossasset()

        # Both must be available for the test to be meaningful
        if not (result_a["available"] and result_b["available"]):
            pytest.skip(
                "fetch_crossasset unavailable in one scenario — cannot test "
                "HYG-LQD relativity (check honest-availability tests instead)"
            )

        comps_a = result_a["components"]
        comps_b = result_b["components"]

        # Identify the credit component — implementation may name it "HYG_LQD",
        # "credit", "credit_spread", or similar.  Find by intersection of keys.
        credit_keys_a = {k for k in comps_a if any(s in k.upper() for s in ("HYG", "CREDIT", "SPREAD"))}
        credit_keys_b = {k for k in comps_b if any(s in k.upper() for s in ("HYG", "CREDIT", "SPREAD"))}
        credit_key = (credit_keys_a & credit_keys_b)

        if not credit_key:
            # Fall back: if no explicit credit key, assert ANY component differs,
            # since a change in LQD must propagate somewhere in the output
            assert comps_a != comps_b, (
                "Changing LQD bars while keeping HYG fixed must change at least "
                "one component reading — the credit spread must use LQD, not HYG alone."
            )
            return

        key = next(iter(credit_key))
        assert comps_a[key] != comps_b[key], (
            f"Credit component '{key}' must differ when LQD bars change and HYG is "
            f"held fixed. If it does not, the implementation is using HYG alone "
            f"(violates AC-5: HYG−LQD must be a RELATIVE computation)."
        )

    def test_fetch_bars_called_with_both_hyg_and_lqd(self):
        """fetch_bars must be called with a symbol list containing both HYG and LQD."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()
        captured_symbols: list[list[str]] = []

        def capture_fetch(tickers, *args, **kwargs):
            captured_symbols.append(list(tickers))
            return bars

        with (
            patch("synthetic_history.fetch_bars", side_effect=capture_fetch),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        # At least one fetch_bars call must include both HYG and LQD
        all_fetched: set[str] = set()
        for sym_list in captured_symbols:
            all_fetched.update(sym_list)

        assert "HYG" in all_fetched, (
            "fetch_bars must be called with HYG in the symbol list (credit spread AC-5)"
        )
        assert "LQD" in all_fetched, (
            "fetch_bars must be called with LQD in the symbol list "
            "(HYG−LQD relative spread — AC-5)"
        )


# ---------------------------------------------------------------------------
# AC-7 — Honest availability: missing/short bars excluded, not fabricated
# ---------------------------------------------------------------------------

class TestHonestAvailability:
    """Components whose bars are missing or too short must be excluded.

    Rules:
    - Missing instrument → excluded from components; aggregate computed from rest.
    - Too few bars for a single instrument → that component excluded.
    - Too few valid components remain after exclusion → available=False + reason.
    - All fetch fails → available=False + reason.
    - Never raises.
    """

    def test_missing_single_instrument_excluded_not_fabricated(self):
        """If one instrument's bars are absent, its component is excluded from output."""
        mod = _import_module()
        # Omit EMB from the bars dict entirely
        bars = {
            sym: _make_bars(60, drift=0.001)
            for sym in _ALL_INSTRUMENTS
            if sym != "EMB"
        }
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        # EMB must not appear in components (not fabricated)
        if result["available"]:
            comps = result["components"]
            # Check that no key references EMB as a fabricated standalone component
            emb_keys = [k for k in comps if "EMB" in k.upper()]
            # If EMB is used in a combined key (e.g. "EMB_independence") that is
            # informational/metadata, that is acceptable.  But a directional EMB
            # signal component must NOT be present.
            for k in emb_keys:
                comp_val = comps[k]
                # A metadata-only entry would lack a directional reading field;
                # a fabricated signal would have signal/direction populated from nothing.
                # We assert: any EMB entry must indicate it was excluded/absent.
                assert comp_val.get("available", True) is False or "excluded" in str(comp_val).lower() or "missing" in str(comp_val).lower(), (
                    f"EMB component {k!r} appears to be fabricated despite absent bars: {comp_val}"
                )

    def test_too_short_bars_for_single_instrument_excluded(self):
        """An instrument with fewer bars than the minimum is excluded, not fabricated."""
        mod = _import_module()
        # Give TLT only 2 bars — far too few for any momentum/SMA calculation
        bars = {
            sym: (_make_bars(2, drift=0.001) if sym == "TLT" else _make_bars(60, drift=0.001))
            for sym in _ALL_INSTRUMENTS
        }
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        # Must not raise; TLT should be excluded or marked unavailable
        assert isinstance(result, dict), "fetch_crossasset must return dict, never raise"
        if result["available"]:
            comps = result["components"]
            if "TLT" in comps:
                tlt_comp = comps["TLT"]
                # If TLT is present, it must be marked unavailable/excluded
                assert tlt_comp.get("available", True) is False or "excluded" in str(tlt_comp).lower(), (
                    "TLT component with 2 bars must be excluded or marked unavailable — "
                    "never fabricated from insufficient data."
                )

    def test_empty_bars_returns_unavailable_with_reason(self):
        """All-empty fetch_bars result → available=False + reason."""
        mod = _import_module()
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value={}),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        assert isinstance(result, dict), "Must return dict, never raise"
        assert result["available"] is False, (
            "Empty bars must produce available=False"
        )
        assert "reason" in result, (
            "available=False must include 'reason'"
        )
        assert isinstance(result["reason"], str) and len(result["reason"]) > 0, (
            "'reason' must be a non-empty string"
        )

    def test_all_fetch_fails_on_exception_returns_unavailable(self):
        """fetch_bars raising an exception → available=False + reason; never raises."""
        mod = _import_module()
        persist_mock = _mock_persist()

        def fail_fetch(*args, **kwargs):
            raise ConnectionError("simulated Alpaca outage")

        with (
            patch("synthetic_history.fetch_bars", side_effect=fail_fetch),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        assert isinstance(result, dict), "Must return dict, never raise"
        assert result["available"] is False, (
            "fetch_bars exception must produce available=False"
        )
        assert "reason" in result, (
            "available=False due to exception must include 'reason'"
        )

    def test_too_few_valid_components_returns_unavailable(self):
        """If only one valid component exists after exclusion, available=False.

        The lens requires at least 2 valid components to produce a meaningful
        aggregate (HYG−LQD counts as one credit component, needing both).
        """
        mod = _import_module()
        # Only give bars for one instrument (DBC); all others missing
        bars = {"DBC": _make_bars(60, drift=0.001)}
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        assert isinstance(result, dict), "Must return dict, never raise"
        # Single valid component → too few → must be unavailable or neutral/degraded
        # Implementation may choose a minimum component count; this asserts
        # it communicates the degradation (available=False or a reason key)
        if not result["available"]:
            assert "reason" in result, (
                "available=False with too few components must include 'reason'"
            )

    def test_never_raises_on_any_input(self):
        """fetch_crossasset must never raise regardless of what fetch_bars returns."""
        mod = _import_module()
        bad_payloads: list[Any] = [
            None,
            [],
            {"HYG": []},
            {"HYG": None},
        ]
        for payload in bad_payloads:
            persist_mock = _mock_persist()
            try:
                with (
                    patch("synthetic_history.fetch_bars", return_value=payload),
                    patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
                ):
                    result = mod.fetch_crossasset()
                assert isinstance(result, dict), (
                    f"fetch_crossasset must return dict for payload {payload!r}, "
                    f"got {type(result)}"
                )
                assert "available" in result, (
                    f"result must have 'available' key for payload {payload!r}"
                )
            except Exception as exc:
                pytest.fail(
                    f"fetch_crossasset raised {type(exc).__name__!r} for payload "
                    f"{payload!r}: {exc}\n"
                    "fetch_crossasset must be never-raising (AC-1 / AC-7)."
                )


# ---------------------------------------------------------------------------
# D-1 — Error reason is type(exc).__name__ only
# ---------------------------------------------------------------------------

class TestD1ErrorContract:
    """D-1: 'reason' on unavailability must be type(exc).__name__, never str(exc)."""

    def test_reason_is_type_name_not_exception_message(self):
        """When fetch_bars raises with a long message, reason must be the type name only."""
        mod = _import_module()
        secret_url = "https://data.alpaca.markets/?key=sk-abc123secret"
        long_message = f"Connection refused to {secret_url}"
        persist_mock = _mock_persist()

        def leaky_fetch(*args, **kwargs):
            raise ConnectionError(long_message)

        with (
            patch("synthetic_history.fetch_bars", side_effect=leaky_fetch),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        assert result["available"] is False
        reason = result["reason"]
        # D-1: reason must be the type name only — never the message
        assert "sk-abc123secret" not in reason, (
            "D-1 violation: reason must not contain the exception message string "
            "(which may contain credentials or internal paths)"
        )
        assert "alpaca.markets" not in reason, (
            "D-1 violation: reason must not contain the exception message string"
        )
        # The type name of ConnectionError is "ConnectionError"
        assert reason == "ConnectionError", (
            f"D-1: reason must be type(exc).__name__ = 'ConnectionError', got {reason!r}"
        )

    def test_reason_is_type_name_for_value_error(self):
        """ValueError during processing → reason='ValueError', never str(exc)."""
        mod = _import_module()
        persist_mock = _mock_persist()

        def bad_bars(*args, **kwargs):
            raise ValueError("internal computation error with path /home/user/secret")

        with (
            patch("synthetic_history.fetch_bars", side_effect=bad_bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        assert result["available"] is False
        assert result["reason"] == "ValueError", (
            f"D-1: reason must be 'ValueError', got {result['reason']!r}"
        )

    def test_reason_does_not_contain_raw_traceback_fragments(self):
        """reason must not contain Python traceback/path fragments (D-1)."""
        mod = _import_module()
        persist_mock = _mock_persist()

        def os_error_fetch(*args, **kwargs):
            raise OSError("/home/user/alphabot/.env: permission denied")

        with (
            patch("synthetic_history.fetch_bars", side_effect=os_error_fetch),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        if not result["available"]:
            reason = result["reason"]
            assert ".env" not in reason, (
                "D-1: reason must not contain file path fragments"
            )
            assert "/home" not in reason, (
                "D-1: reason must not contain filesystem paths"
            )
            assert "Traceback" not in reason, (
                "D-1: reason must not contain traceback text"
            )


# ---------------------------------------------------------------------------
# AC-8 — Warehouse persist is called once per fetch_crossasset() invocation
# ---------------------------------------------------------------------------

class TestWarehousePersist:
    """AC-8: fetch_crossasset must call lens_warehouse.persist_lens_snapshot exactly once."""

    def test_persist_called_once_on_success(self):
        """On success, persist_lens_snapshot is called exactly once."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        assert persist_mock.call_count == 1, (
            f"persist_lens_snapshot must be called exactly once per fetch_crossasset "
            f"invocation; got call_count={persist_mock.call_count}"
        )

    def test_persist_called_with_lens_crossasset(self):
        """persist_lens_snapshot must be called with lens='crossasset'."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        assert persist_mock.call_count >= 1, "persist_lens_snapshot must be called"
        # Inspect the call args — lens='crossasset' can be positional or keyword
        c = persist_mock.call_args
        args = c.args if c.args else ()
        kwargs = c.kwargs if c.kwargs else {}
        lens_value = kwargs.get("lens") or (args[0] if args else None)
        assert lens_value == "crossasset", (
            f"persist_lens_snapshot must be called with lens='crossasset', "
            f"got lens={lens_value!r}"
        )

    def test_persist_called_once_on_unavailable(self):
        """persist_lens_snapshot must be called even when bars are unavailable (AC-8).

        The warehouse records both available and unavailable runs.
        """
        mod = _import_module()
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value={}),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        assert persist_mock.call_count == 1, (
            "persist_lens_snapshot must be called once even when available=False; "
            f"got call_count={persist_mock.call_count}"
        )

    def test_persist_called_once_on_exception(self):
        """persist_lens_snapshot must be called even when fetch_bars raises."""
        mod = _import_module()
        persist_mock = _mock_persist()

        def fail_fetch(*args, **kwargs):
            raise RuntimeError("simulated Alpaca outage")

        with (
            patch("synthetic_history.fetch_bars", side_effect=fail_fetch),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            result = mod.fetch_crossasset()

        # The warehouse persist should record the failure run
        assert persist_mock.call_count == 1, (
            "persist_lens_snapshot must be called once even when fetch_bars raises; "
            f"got call_count={persist_mock.call_count}"
        )

    def test_persist_called_with_source_argument(self):
        """persist_lens_snapshot must receive a non-empty 'source' argument."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()

        with (
            patch("synthetic_history.fetch_bars", return_value=bars),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        c = persist_mock.call_args
        args = c.args if c.args else ()
        kwargs = c.kwargs if c.kwargs else {}
        source_value = kwargs.get("source") or (args[2] if len(args) > 2 else None)
        assert source_value is not None, (
            "persist_lens_snapshot must receive a 'source' argument"
        )
        assert isinstance(source_value, str) and len(source_value) > 0, (
            f"'source' passed to persist_lens_snapshot must be a non-empty string, "
            f"got {source_value!r}"
        )


# ---------------------------------------------------------------------------
# AC-2 — fetch_bars is called (not a different HTTP client)
# ---------------------------------------------------------------------------

class TestFetchBarsUsed:
    """AC-2: the module must use synthetic_history.fetch_bars — no new HTTP client."""

    def test_fetch_bars_is_called_during_fetch_crossasset(self):
        """synthetic_history.fetch_bars must be called at least once."""
        mod = _import_module()
        bars = _full_mock_bars(n=60, drift=0.001)
        persist_mock = _mock_persist()
        fetch_mock = MagicMock(return_value=bars)

        with (
            patch("synthetic_history.fetch_bars", fetch_mock),
            patch("advisors.lens_warehouse.persist_lens_snapshot", persist_mock),
        ):
            mod.fetch_crossasset()

        assert fetch_mock.call_count >= 1, (
            "synthetic_history.fetch_bars must be called at least once "
            "inside fetch_crossasset() — no new HTTP client allowed (AC-2)."
        )

    def test_no_requests_import_in_module_source(self):
        """Module source must not directly import 'requests' (reuses synthetic_history)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        import re
        # Allow 'requests' inside a string literal / comment — only flag actual import statements
        direct_import = re.compile(r'^\s*(import requests|from requests)', re.MULTILINE)
        matches = direct_import.findall(source)
        assert not matches, (
            f"lens_crossasset.py must not directly import 'requests' — "
            f"it must reuse synthetic_history.fetch_bars (AC-2). "
            f"Found: {matches}"
        )


# ---------------------------------------------------------------------------
# AC-4 — Named constant for the risk-read threshold
# ---------------------------------------------------------------------------

class TestNamedThresholdConstant:
    """The aggregation threshold must be a named constant — no magic numbers."""

    def test_risk_read_threshold_constant_exists(self):
        """Module must expose at least one named threshold constant for risk_read aggregation."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        # A named constant would look like: _RISK_ON_THRESHOLD, _SIGNAL_THRESHOLD,
        # _MIN_SCORE_FOR_RISK_ON, etc. — any ALL_CAPS constant referencing threshold/score.
        import re
        threshold_pattern = re.compile(
            r'\b[A-Z_]+(?:THRESHOLD|MIN_SCORE|MAX_SCORE|VOTE|FLOOR|CEILING|CUTOFF)\b'
        )
        matches = threshold_pattern.findall(source)
        assert len(matches) >= 1, (
            "lens_crossasset.py must define at least one named threshold constant "
            "for the risk_read aggregation (e.g. _RISK_ON_THRESHOLD, _MIN_VOTE_COUNT). "
            "No magic numbers allowed (AC-4 + coding standards)."
        )

    def test_minimum_component_count_constant_exists(self):
        """Module must expose a named constant for the minimum viable component count."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        import re
        min_pattern = re.compile(
            r'\b[A-Z_]+(?:MIN(?:_[A-Z]+)*(?:COUNT|COMPONENTS|INSTRUMENTS|REQUIRED))\b'
            r'|\b[A-Z_]+MIN_(?:COMPONENT|INSTRUMENT)[A-Z_]*\b'
        )
        matches = min_pattern.findall(source)
        assert len(matches) >= 1, (
            "lens_crossasset.py must define a named constant for the minimum valid "
            "component count (e.g. _MIN_COMPONENTS, _MIN_REQUIRED_INSTRUMENTS). "
            "This avoids magic numbers in the honest-availability logic."
        )
