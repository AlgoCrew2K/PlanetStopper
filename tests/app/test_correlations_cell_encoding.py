"""RED tests -- how a Correlations cell is encoded: printed value, signed band, intensity, depth.

Three layers, each against the real thing:

* pure helpers in ``app`` (``_retirement_correlation_text``, ``_retirement_correlation_band``,
  ``_retirement_correlation_fill_pct``, ``_retirement_correlation_thin_level``), driven over the
  enumerated sign, boundary and depth families (inputs are boundary values, not producer records);
* the rendered page (Flask test client) over the REAL recommender's run on the PM's real captures
  (``tests/advisors/_series_captures.py``); where no real pair has the value a case needs (no
  captured pair is negative) one named pair's stored correlation is replaced in a copy of the real
  summary (``with_pair_correlations``; a derived record, see the derived PROVENANCE.md);
* the template/CSS contract for the matrix (design tokens only, no raw colour, both themes).

Cell markup contract: ``td[data-testid=corr-cell]`` carries classes ``corr-cell``,
``corr-band-<band>`` (``danger``, ``warn``, ``neutral``, ``diversifying``, ``undefined``) and, when
thin, ``corr-thin-<level>`` (``stress``, ``uncertainty``); attributes ``data-band``, ``data-thin``
(``none``, ``stress`` or ``uncertainty``), ``data-n-obs``, ``tabindex="0"``, equal ``title`` and
``aria-label``; and, for a filled band only, an inline ``--corr-fill-pct: NN%``. Its visible text is
the printed correlation and nothing else. The legend is ``correlations-legend`` with four
``corr-legend-band`` items (``data-band``, in the order danger, warn, neutral, diversifying) and two
``corr-legend-thin`` items (``data-level`` ``uncertainty`` then ``stress``), each holding a swatch
element with the same classes the cells use.
"""

from __future__ import annotations

import math
import pathlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app as app_module
from tests.advisors import _series_captures as captures
from tests.app._correlations_view_support import (  # noqa: F401  (fixtures)
    EMPTY_CAPTURE,
    client,
    collapse,
    correlation_text,
    fill_pct,
    get_page,
    grid_of,
    isolated_db,
    oracle_band,
    pair_key,
    render_panel,
    seed_run,
    seed_state_only,
    stored_pairs,
    with_pair_correlations,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "templates" / "ai_advisor.html"
TOKENS_PATH = REPO_ROOT / "static" / "tokens.css"
_ET = ZoneInfo("America/New_York")

_FILLED_BANDS = ("danger", "warn", "diversifying")
_ALL_BANDS = ("danger", "warn", "neutral", "diversifying")
_THIN_LEVELS = ("uncertainty", "stress")
_SCREEN_THRESHOLD = 0.65  # the recommender's screen threshold; tests read the run's own too
_THREE_DEEP = ["Gpaw3IhZghQPRE6AdEKx", "hvPiGP1O7AHfutHE3Fjy", "n2ooAZTvBRN6ZzpMmWmU"]
_SHORT_SERIES = "q7WQHwLzt49cMgFBzHsX"
_MID_SERIES = "qF5ZU7ALjrlhxrGEwsyJ"


def _oracle_thin(n_obs: int, uncertainty_min: int, stress_min: int) -> str:
    if n_obs < uncertainty_min:
        return "uncertainty"
    if n_obs < stress_min:
        return "stress"
    return "none"


def _bands_of(cell) -> set[str]:
    return {c.removeprefix("corr-band-") for c in cell.classes if c.startswith("corr-band-")}


def _thin_classes_of(cell) -> set[str]:
    return {c.removeprefix("corr-thin-") for c in cell.classes if c.startswith("corr-thin-")}


# ===========================================================================
# The printed value
# ===========================================================================


class TestPrintedCorrelation:
    @pytest.mark.parametrize(
        "value", [0.9712, 0.52, 0.1, -0.12, -0.75, -1.0, 1.0, 0.6449, 0.6499, 0.0, -0.4, 0.4]
    )
    def test_a_defined_correlation_prints_signed_with_two_decimals(self, value):
        assert app_module._retirement_correlation_text(value) == f"{value:+.2f}"

    @pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
    def test_an_undefined_or_non_finite_correlation_prints_the_word(self, value):
        assert app_module._retirement_correlation_text(value) == "undefined"

    def test_rounding_decides_the_digits_so_a_stored_6499_prints_as_65(self):
        assert app_module._retirement_correlation_text(0.6499) == "+0.65"
        assert app_module._retirement_correlation_text(0.6449) == "+0.64"


# ===========================================================================
# The band follows the printed value, by sign and bound
# ===========================================================================


class TestBandOfAPrintedValue:
    @pytest.mark.parametrize(
        ("printed", "band"),
        [
            ("+1.00", "danger"),
            ("+0.97", "danger"),
            ("+0.65", "danger"),
            ("+0.64", "warn"),
            ("+0.52", "warn"),
            ("+0.40", "warn"),
            ("+0.39", "neutral"),
            ("+0.10", "neutral"),
            ("+0.00", "neutral"),
            ("-0.12", "neutral"),
            ("-0.39", "neutral"),
            ("-0.40", "diversifying"),
            ("-0.75", "diversifying"),
            ("-1.00", "diversifying"),
            ("undefined", "undefined"),
        ],
    )
    def test_each_sign_and_boundary_has_its_band(self, printed, band):
        assert app_module._retirement_correlation_band(printed, _SCREEN_THRESHOLD) == band

    def test_every_printable_value_agrees_with_the_rule(self):
        for hundredths in range(-100, 101):
            printed = f"{hundredths / 100:+.2f}"
            assert app_module._retirement_correlation_band(
                printed, _SCREEN_THRESHOLD
            ) == oracle_band(printed, _SCREEN_THRESHOLD), printed

    def test_a_negative_correlation_is_never_the_danger_band(self):
        for hundredths in range(-100, 0):
            printed = f"{hundredths / 100:+.2f}"
            assert app_module._retirement_correlation_band(printed, _SCREEN_THRESHOLD) != "danger"

    def test_the_high_band_starts_at_the_threshold_it_is_given(self):
        assert app_module._retirement_correlation_band("+0.65", 0.70) == "warn"
        assert app_module._retirement_correlation_band("+0.70", 0.70) == "danger"
        assert app_module._retirement_correlation_band("+0.60", 0.55) == "danger"

    def test_the_band_is_decided_on_the_printed_value_not_the_stored_float(self):
        for raw, band in (
            (0.6499, "danger"),
            (0.6449, "warn"),
            (0.3951, "warn"),
            (-0.3951, "diversifying"),
        ):
            printed = app_module._retirement_correlation_text(raw)
            assert app_module._retirement_correlation_band(printed, _SCREEN_THRESHOLD) == band, raw

    def test_the_moderate_bound_is_the_forty_hundredths_the_plan_keeps(self):
        assert app_module._CORRELATION_MODERATE_BOUND == 0.40


class TestFillIntensity:
    def test_the_intensity_range_is_a_positive_percentage_range(self):
        low, high = app_module._CORRELATION_FILL_PCT_MIN, app_module._CORRELATION_FILL_PCT_MAX
        assert 0 < low < high <= 100

    def test_a_stronger_printed_correlation_always_fills_more_strongly(self):
        values = [
            app_module._retirement_correlation_fill_pct(f"{h / 100:+.2f}") for h in range(40, 101)
        ]
        assert all(b > a for a, b in zip(values, values[1:], strict=False)), values

    def test_the_intensity_depends_on_the_size_not_the_sign(self):
        for h in range(40, 101, 5):
            assert app_module._retirement_correlation_fill_pct(
                f"{h / 100:+.2f}"
            ) == app_module._retirement_correlation_fill_pct(f"{-h / 100:+.2f}")

    def test_the_intensity_is_linear_in_the_printed_size_and_stays_in_range(self):
        fill = app_module._retirement_correlation_fill_pct
        low, high = app_module._CORRELATION_FILL_PCT_MIN, app_module._CORRELATION_FILL_PCT_MAX
        slope_a = (fill("+0.70") - fill("+0.40")) / 0.30
        slope_b = (fill("+1.00") - fill("+0.70")) / 0.30
        assert slope_a == pytest.approx(slope_b, rel=1e-9)
        assert all(low <= fill(f"{h / 100:+.2f}") <= high for h in range(40, 101))

    def test_plus_95_fills_more_than_plus_70(self):
        fill = app_module._retirement_correlation_fill_pct
        assert fill("+0.95") > fill("+0.70")


class TestThinLevel:
    @pytest.mark.parametrize(
        ("n_obs", "level"),
        [(0, "uncertainty"), (29, "uncertainty"), (30, "stress"), (180, "stress"), (181, "none")]
        + [(250, "none")],
    )
    def test_the_depth_boundaries_of_the_two_gates(self, n_obs, level):
        assert app_module._retirement_correlation_thin_level(n_obs, 30, 181) == level

    def test_the_minimums_are_arguments_not_constants(self):
        assert app_module._retirement_correlation_thin_level(44, 45, 81) == "uncertainty"
        assert app_module._retirement_correlation_thin_level(45, 45, 81) == "stress"
        assert app_module._retirement_correlation_thin_level(80, 45, 81) == "stress"
        assert app_module._retirement_correlation_thin_level(81, 45, 81) == "none"


# ===========================================================================
# Rendered cells
# ===========================================================================


def _real_run_grid(client, monkeypatch, **run_kwargs):
    summary = seed_run(monkeypatch, **run_kwargs)
    return summary, grid_of(client)


def _cell_for(grid, first: str, second: str):
    cells = grid.cells_of_pair(first, second)
    assert len(cells) == 2, (first, second)
    return cells[0]


class TestCellsAreFocusableAndCarryTheirDetail:
    def test_a_pair_cell_names_both_symphonies_the_correlation_the_depth_and_the_window(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        stored = stored_pairs(summary)[pair_key(_MID_SERIES, _THREE_DEEP[0])]
        assert stored["n_obs"] >= 100, "premise: a deep real pair"
        names = (captures.capture_name(_MID_SERIES), captures.capture_name(_THREE_DEEP[0]))
        for cell in grid.cells_of_pair(_MID_SERIES, _THREE_DEEP[0]):
            detail = collapse(cell.attrs["title"])
            assert all(collapse(n) in detail for n in names)
            assert correlation_text(stored["correlation"]) in detail
            assert f"{stored['n_obs']} aligned days" in detail
            assert f"{stored['window_first']} to {stored['window_last']}" in detail

    def test_the_hover_text_and_the_accessible_name_are_the_same_and_the_cell_takes_focus(
        self, client, isolated_db, monkeypatch
    ):
        _summary, grid = _real_run_grid(client, monkeypatch)
        for cell in grid.off_diagonal():
            assert cell.attrs.get("tabindex") == "0"
            assert cell.attrs.get("title"), "a cell has hover text"
            assert cell.attrs.get("aria-label") == cell.attrs["title"]

    def test_a_pair_with_no_common_days_says_so_and_names_no_window(
        self, client, isolated_db, monkeypatch
    ):
        early = datetime(2026, 3, 1, 12, 0, tzinfo=_ET)
        summary, grid = _real_run_grid(
            monkeypatch=monkeypatch,
            client=client,
            per_symphony_now_et={"hvPiGP1O7AHfutHE3Fjy": early},
        )
        assert stored_pairs(summary)[pair_key("hvPiGP1O7AHfutHE3Fjy", _SHORT_SERIES)]["n_obs"] == 0
        for cell in grid.cells_of_pair("hvPiGP1O7AHfutHE3Fjy", _SHORT_SERIES):
            assert "0 aligned days" in cell.attrs["title"]
            assert not re.search(r"\d{4}-\d{2}-\d{2}", cell.attrs["title"])

    def test_the_only_visible_text_in_a_cell_is_its_correlation(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        pairs = stored_pairs(summary)
        for cell in grid.off_diagonal():
            stored = pairs[(cell.attrs["data-sym-a"], cell.attrs["data-sym-b"])]
            assert cell.text == correlation_text(stored["correlation"])
            assert "n=" not in cell.text and "thin" not in cell.text.lower()
        assert "n=" not in render_panel(client).text


class TestUndefinedCell:
    def test_an_undefined_pair_reads_undefined_carries_no_fill_and_shows_its_days(
        self, client, isolated_db, monkeypatch
    ):
        early = datetime(2026, 3, 1, 12, 0, tzinfo=_ET)
        _summary, grid = _real_run_grid(
            monkeypatch=monkeypatch,
            client=client,
            per_symphony_now_et={"hvPiGP1O7AHfutHE3Fjy": early},
        )
        cells = grid.cells_of_pair("hvPiGP1O7AHfutHE3Fjy", _SHORT_SERIES)
        assert len(cells) == 2
        for cell in cells:
            assert cell.text == "undefined"
            assert cell.attrs["data-band"] == "undefined"
            assert fill_pct(cell) is None
            assert not (_bands_of(cell) & set(_FILLED_BANDS))
            assert cell.attrs["data-n-obs"] == "0"
            assert "0 aligned days" in cell.attrs["title"]


# ===========================================================================
# Thin markers: tied to the recommender's two gates
# ===========================================================================


def _check_thin_markers(summary: dict, grid) -> set[str]:
    unc = summary["uncertainty_min_aligned_days"]
    stress = summary["stress_gate_min_aligned_days"]
    seen = set()
    for cell in grid.off_diagonal():
        n_obs = int(cell.attrs["data-n-obs"])
        level = _oracle_thin(n_obs, unc, stress)
        seen.add(level)
        assert cell.attrs["data-thin"] == level, (n_obs, level)
        assert _thin_classes_of(cell) == (set() if level == "none" else {level}), (n_obs, level)
    return seen


class TestThinMarkers:
    def test_the_real_run_marks_each_cell_by_the_gate_its_depth_misses(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        seen = _check_thin_markers(summary, grid)
        assert seen == {"uncertainty", "stress", "none"}, "premise: the real run spans every depth"

    def test_a_pair_between_the_two_minimums_carries_the_stress_marker_only(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        stored = stored_pairs(summary)[pair_key(_MID_SERIES, _THREE_DEEP[0])]
        assert (
            summary["uncertainty_min_aligned_days"]
            <= stored["n_obs"]
            < summary["stress_gate_min_aligned_days"]
        )
        for cell in grid.cells_of_pair(_MID_SERIES, _THREE_DEEP[0]):
            assert cell.attrs["data-thin"] == "stress"
            assert _thin_classes_of(cell) == {"stress"}

    def test_a_pair_shorter_than_the_uncertainty_minimum_carries_the_stronger_marker(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        stored = stored_pairs(summary)[pair_key(_SHORT_SERIES, _THREE_DEEP[0])]
        assert stored["n_obs"] < summary["uncertainty_min_aligned_days"]
        for cell in grid.cells_of_pair(_SHORT_SERIES, _THREE_DEEP[0]):
            assert cell.attrs["data-thin"] == "uncertainty"
            assert _thin_classes_of(cell) == {"uncertainty"}

    def test_a_pair_of_the_deepest_series_carries_no_thin_marker(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        first, second = _THREE_DEEP[0], _THREE_DEEP[1]
        stored = stored_pairs(summary)[pair_key(first, second)]
        assert stored["n_obs"] >= summary["stress_gate_min_aligned_days"]
        for cell in grid.cells_of_pair(first, second):
            assert cell.attrs["data-thin"] == "none"
            assert _thin_classes_of(cell) == set()

    @pytest.mark.parametrize("days", [30, 180, 181])
    def test_the_boundary_depths_of_a_run_with_that_lookback(
        self, client, isolated_db, monkeypatch, days
    ):
        summary, grid = _real_run_grid(client, monkeypatch, days=days)
        depths = {int(c.attrs["data-n-obs"]) for c in grid.off_diagonal()}
        assert days in depths, "premise: the deep pairs align on exactly the lookback"
        seen = _check_thin_markers(summary, grid)
        expected_at_days = _oracle_thin(
            days, summary["uncertainty_min_aligned_days"], summary["stress_gate_min_aligned_days"]
        )
        assert expected_at_days in seen
        assert expected_at_days == {30: "stress", 180: "stress", 181: "none"}[days]

    def test_a_pair_one_day_short_of_the_uncertainty_minimum_has_the_stronger_marker(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        stored = stored_pairs(summary)[pair_key(_SHORT_SERIES, _THREE_DEEP[0])]
        assert stored["n_obs"] == summary["uncertainty_min_aligned_days"] - 1, "premise: 29 days"
        assert grid.cells_of_pair(_SHORT_SERIES, _THREE_DEEP[0])[0].attrs["data-thin"] == (
            "uncertainty"
        )

    def test_no_thin_cell_still_leaves_both_markers_in_the_legend(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch, ids=_THREE_DEEP, days=181)
        assert _check_thin_markers(summary, grid) == {"none"}
        panel = render_panel(client)
        assert [i.attrs["data-level"] for i in panel.find_all(testid="corr-legend-thin")] == list(
            _THIN_LEVELS
        )


class TestLegend:
    def test_the_legend_names_both_markers_with_the_minimums_the_summary_states(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        legend = render_panel(client).find(testid="correlations-legend")
        assert legend is not None
        items = {i.attrs["data-level"]: i for i in legend.find_all(testid="corr-legend-thin")}
        assert set(items) == set(_THIN_LEVELS)
        unc = summary["uncertainty_min_aligned_days"]
        stress = summary["stress_gate_min_aligned_days"]
        assert f"fewer than {unc} aligned days: the uncertainty gate cannot pass" in collapse(
            items["uncertainty"].text
        )
        assert f"fewer than {stress} aligned days: the stress gate cannot pass" in collapse(
            items["stress"].text
        )
        for level, item in items.items():
            swatch_classes = {c for n in item.iter() for c in n.classes}
            assert f"corr-thin-{level}" in swatch_classes, level

    def test_a_retuned_stress_minimum_is_what_the_legend_and_the_cells_follow(
        self, client, isolated_db, monkeypatch
    ):
        import advisors.retirement_recommender as rr  # noqa: PLC0415

        monkeypatch.setattr(rr, "STRESS_MIN_OBS", 5)
        monkeypatch.setattr(
            rr, "STRESS_GATE_MIN_ALIGNED_DAYS", rr._derive_stress_gate_min_aligned_days()
        )
        retuned = rr.STRESS_GATE_MIN_ALIGNED_DAYS
        assert retuned != 181, "premise: the retune changes the stored minimum"
        summary, grid = _real_run_grid(client, monkeypatch)
        assert summary["stress_gate_min_aligned_days"] == retuned

        legend = render_panel(client).find(testid="correlations-legend")
        text = collapse(legend.text)
        assert f"fewer than {retuned} aligned days: the stress gate cannot pass" in text
        assert "181" not in text
        _check_thin_markers(summary, grid)

    def test_the_legend_names_the_four_bands_with_their_bounds(
        self, client, isolated_db, monkeypatch
    ):
        summary = seed_run(monkeypatch)
        threshold = f"{summary['screen_threshold']:.2f}"
        moderate = f"{app_module._CORRELATION_MODERATE_BOUND:.2f}"
        legend = render_panel(client).find(testid="correlations-legend")
        bands = legend.find_all(testid="corr-legend-band")
        assert [b.attrs["data-band"] for b in bands] == list(_ALL_BANDS)
        by_band = {b.attrs["data-band"]: collapse(b.text) for b in bands}

        assert threshold in by_band["danger"]
        assert moderate in by_band["warn"] and threshold in by_band["warn"]
        assert f"-{moderate}" in by_band["neutral"] and moderate in by_band["neutral"]
        assert f"-{moderate}" in by_band["diversifying"]
        for band in bands:
            classes = {c for n in band.iter() for c in n.classes}
            assert f"corr-band-{band.attrs['data-band']}" in classes

    def test_the_legend_bounds_follow_the_runs_threshold_not_a_typed_number(
        self, client, isolated_db, monkeypatch
    ):
        _recs, produced = captures.run_over_captures(monkeypatch)
        produced["screen_threshold"] = 0.72
        seed_state_only(produced)
        legend = render_panel(client).find(testid="correlations-legend")
        danger = next(
            b
            for b in legend.find_all(testid="corr-legend-band")
            if b.attrs["data-band"] == "danger"
        )
        assert "0.72" in collapse(danger.text)
        assert "0.65" not in collapse(legend.text)


# ===========================================================================
# Signed colour
# ===========================================================================


class TestSignedBands:
    def test_every_cell_of_the_real_run_has_the_band_of_its_printed_value(
        self, client, isolated_db, monkeypatch
    ):
        summary, grid = _real_run_grid(client, monkeypatch)
        threshold = summary["screen_threshold"]
        seen = set()
        for cell in grid.off_diagonal():
            band = oracle_band(cell.text, threshold)
            seen.add(band)
            assert cell.attrs["data-band"] == band, cell.text
            assert _bands_of(cell) == {band}, cell.text
            assert (fill_pct(cell) is not None) == (band in _FILLED_BANDS), cell.text
        assert {"danger", "warn", "neutral"} <= seen, "premise: the real run spans three bands"

    def test_the_diagonal_reads_one_with_no_band_fill(self, client, isolated_db, monkeypatch):
        _summary, grid = _real_run_grid(client, monkeypatch)
        diagonal = grid.diagonal()
        assert diagonal
        for cell in diagonal:
            assert cell.text == "1.00"
            assert fill_pct(cell) is None
            assert not (_bands_of(cell) & set(_FILLED_BANDS))

    def test_negative_pairs_fill_with_the_diversifying_band_and_never_the_danger_band(
        self, client, isolated_db, monkeypatch
    ):
        _recs, produced = captures.run_over_captures(monkeypatch)
        keys = sorted(stored_pairs(produced))[:3]
        derived = with_pair_correlations(produced, {keys[0]: -0.75, keys[1]: -1.0, keys[2]: -0.12})
        seed_state_only(derived)
        grid = grid_of(client)

        strong, extreme, mild = (grid.cells_of_pair(*k)[0] for k in keys)
        assert (strong.text, extreme.text, mild.text) == ("-0.75", "-1.00", "-0.12")
        for cell in (strong, extreme):
            assert cell.attrs["data-band"] == "diversifying"
            assert _bands_of(cell) == {"diversifying"}
            assert fill_pct(cell) is not None
        assert mild.attrs["data-band"] == "neutral" and fill_pct(mild) is None
        assert fill_pct(extreme) > fill_pct(strong)
        assert all("danger" not in _bands_of(c) for c in (strong, extreme, mild))

    def test_values_at_the_boundaries_take_the_band_of_what_they_print(
        self, client, isolated_db, monkeypatch
    ):
        _recs, produced = captures.run_over_captures(monkeypatch)
        keys = sorted(stored_pairs(produced))[:5]
        values = [0.65, 0.40, -0.40, 0.6449, 0.6499]
        derived = with_pair_correlations(produced, dict(zip(keys, values, strict=True)))
        seed_state_only(derived)
        grid = grid_of(client)

        want = [
            ("+0.65", "danger"),
            ("+0.40", "warn"),
            ("-0.40", "diversifying"),
            ("+0.64", "warn"),
            ("+0.65", "danger"),
        ]
        for key, (printed, band) in zip(keys, want, strict=True):
            for cell in grid.cells_of_pair(*key):
                assert cell.text == printed
                assert cell.attrs["data-band"] == band, (printed, key)
                assert _bands_of(cell) == {band}

    def test_a_stronger_printed_correlation_fills_more_strongly_in_the_same_band(
        self, client, isolated_db, monkeypatch
    ):
        _recs, produced = captures.run_over_captures(monkeypatch)
        first, second = sorted(stored_pairs(produced))[:2]
        derived = with_pair_correlations(produced, {first: 0.95, second: 0.70})
        seed_state_only(derived)
        grid = grid_of(client)
        high, low = grid.cells_of_pair(*first)[0], grid.cells_of_pair(*second)[0]
        assert (high.attrs["data-band"], low.attrs["data-band"]) == ("danger", "danger")
        assert fill_pct(high) > fill_pct(low)

    def test_intensity_rises_with_the_printed_size_across_the_real_run(
        self, client, isolated_db, monkeypatch
    ):
        _summary, grid = _real_run_grid(client, monkeypatch)
        filled = [c for c in grid.off_diagonal() if fill_pct(c) is not None]
        assert len(filled) > 4
        ordered = sorted(filled, key=lambda c: abs(float(c.text)))
        fills = [fill_pct(c) for c in ordered]
        assert fills == sorted(fills)
        danger = [c for c in filled if c.attrs["data-band"] == "danger"]
        sizes = {abs(float(c.text)) for c in danger}
        assert len(sizes) > 1, "premise: the real run has danger cells of different sizes"
        for cell in danger:
            same = [d for d in danger if d.text == cell.text]
            assert len({fill_pct(d) for d in same}) == 1


# ===========================================================================
# Template and CSS contract
# ===========================================================================


def _template_source() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _panel_source() -> str:
    source = _template_source()
    start = source.index('id="tab-panel-correlations"')
    end = source.index("<!-- TAB PANEL:", start)
    return source[start:end]


def _matrix_rules() -> list[tuple[str, str]]:
    """(selector, body) of every CSS rule in the template's style blocks about the matrix."""
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", _template_source(), re.S))
    styles = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", styles)
    return [
        (sel.strip(), body.strip())
        for sel, body in rules
        if re.search(r"\.corr-|heatmap|matrix-", sel)
    ]


def _rules_for(selector_fragment: str) -> list[str]:
    pattern = re.compile(re.escape(selector_fragment) + r"(?![\w-])")
    return [body for sel, body in _matrix_rules() if pattern.search(sel)]


_RAW_COLOUR = re.compile(r"(?<![&\w\"'/])#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|\blab\(")
_TOKEN_FOR_BAND = {
    "danger": "--studio-neg",
    "warn": "--studio-warn",
    "diversifying": "--studio-pos",
}


class TestMatrixStyleContract:
    @pytest.mark.parametrize("band", _FILLED_BANDS)
    def test_a_filled_band_mixes_its_own_token_by_the_cells_intensity_over_transparent(self, band):
        bodies = _rules_for(f".corr-band-{band}")
        assert bodies, f"no rule styles .corr-band-{band}"
        token = _TOKEN_FOR_BAND[band]
        body = " ".join(bodies)
        assert re.search(
            rf"background-color\s*:\s*color-mix\(\s*in srgb\s*,\s*var\({token}\)\s+"
            r"var\(--corr-fill-pct\)\s*,\s*transparent\s*\)",
            body,
        ), body
        other_tokens = [t for b, t in _TOKEN_FOR_BAND.items() if b != band]
        assert not any(t in body for t in other_tokens), "a band uses only its own token"

    def test_the_diversifying_band_never_touches_the_danger_token(self):
        body = " ".join(_rules_for(".corr-band-diversifying"))
        assert "--studio-neg" not in body

    def test_a_band_sets_no_text_colour_so_the_number_stays_in_the_ink_token(self):
        for band in _ALL_BANDS + ("undefined",):
            for body in _rules_for(f".corr-band-{band}"):
                assert not re.search(r"(?<![-\w])color\s*:", body), (band, body)
        cell_rules = " ".join(_rules_for(".corr-cell"))
        assert re.search(r"(?<![-\w])color\s*:\s*var\(--studio-ink\)(?![-\w])", cell_rules), (
            cell_rules
        )

    def test_neutral_and_undefined_cells_use_none_of_the_signal_tokens(self):
        for band in ("neutral", "undefined"):
            body = " ".join(_rules_for(f".corr-band-{band}"))
            assert not any(t in body for t in _TOKEN_FOR_BAND.values()), band

    @pytest.mark.parametrize("level", _THIN_LEVELS)
    def test_a_thin_marker_is_a_hatch_of_the_faint_ink_token_that_leaves_the_fill_alone(
        self, level
    ):
        bodies = _rules_for(f".corr-thin-{level}")
        assert bodies, f"no rule styles .corr-thin-{level}"
        body = " ".join(bodies)
        assert "repeating-linear-gradient" in body and "var(--studio-ink-faint)" in body
        assert "background-image" in body
        assert not re.search(r"(?<![-\w])background\s*:", body), "a shorthand would erase the fill"

    def test_the_two_markers_are_drawn_differently(self):
        stress = " ".join(_rules_for(".corr-thin-stress"))
        uncertainty = " ".join(_rules_for(".corr-thin-uncertainty"))
        assert re.sub(r"\s+", "", stress) != re.sub(r"\s+", "", uncertainty)

    def test_the_matrix_rules_and_panel_hold_only_design_tokens_no_raw_colour(self):
        rules = _matrix_rules()
        assert rules, "premise: the matrix has style rules in the template"
        text = "\n".join(f"{sel} {{ {body} }}" for sel, body in rules) + "\n" + _panel_source()
        assert not _RAW_COLOUR.findall(text), _RAW_COLOUR.findall(text)

    def test_every_token_the_matrix_uses_is_defined_and_the_signal_tokens_in_both_themes(self):
        tokens = TOKENS_PATH.read_text(encoding="utf-8")
        dark_start = tokens.index('[data-theme="dark"]')
        light, dark = tokens[:dark_start], tokens[dark_start : tokens.index("}", dark_start)]
        for token in (
            "--studio-neg",
            "--studio-warn",
            "--studio-pos",
            "--studio-ink",
            "--studio-ink-faint",
        ):
            assert re.search(rf"{re.escape(token)}\s*:", light), f"{token} missing in light"
            assert re.search(rf"{re.escape(token)}\s*:", dark), f"{token} missing in dark"
        rule_text = " ".join(body for _sel, body in _matrix_rules())
        referenced = set(re.findall(r"var\((--[\w-]+)", rule_text)) - {"--corr-fill-pct"}
        assert referenced, "premise: the matrix rules reference tokens"
        for token in referenced:
            assert re.search(rf"{re.escape(token)}\s*:", tokens), f"{token} is not a defined token"

    def test_the_panel_types_no_threshold_label_or_truncation_of_its_own(self):
        panel = _panel_source()
        typed_numbers = re.findall(r"(?<![\w.])(?:0\.65|0\.70|0\.40|0\.4|181)(?![\w.])", panel)
        assert not typed_numbers, f"typed in the Correlations panel: {typed_numbers}"
        for literal in ("realized daily returns", "truncate("):
            assert literal not in panel, f"{literal!r} is typed in the Correlations panel"
        assert not re.search(r"\|\s*safe", panel)

    def test_the_old_cell_text_markers_and_classes_are_gone(self):
        source = _template_source()
        for removed in ("n-indicator", "thin-footnote", "corr-danger", "corr-caution", "corr-ok"):
            assert removed not in source, removed
        assert "&dagger;" not in _panel_source()


def test_a_page_render_carries_the_band_styles_the_cells_name(client, isolated_db, monkeypatch):
    """The classes a cell carries are the ones the served stylesheet defines."""
    seed_run(monkeypatch)
    body = get_page(client)
    for band in _FILLED_BANDS:
        assert f".corr-band-{band}" in body
    for level in _THIN_LEVELS:
        assert f".corr-thin-{level}" in body


def test_the_empty_capture_stays_out_of_every_encoded_cell(client, isolated_db, monkeypatch):
    _summary, grid = _real_run_grid(client, monkeypatch)
    assert all(
        EMPTY_CAPTURE not in (c.attrs["data-sym-a"], c.attrs["data-sym-b"])
        for c in grid.off_diagonal()
    )
