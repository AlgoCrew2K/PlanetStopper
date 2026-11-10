"""The configured values that page copy names are read from their authorities at render time.

``app._plain_number`` and ``app._page_copy_values`` are the one place a copy row's slot gets its
text. Every slot reads the authority the plan's Values table names and nothing typed in a
template or script; a slot whose authority cannot be read yields None, so the template writes the
row's whole fallback phrase instead of a sentence with a hole. The expected text of each slot is
filled from the value the test itself set on the authority, in the format the Values table's
Format column states (``tests/ui/_page_copy.py``); the production formatter is never called to
compute an expectation.

The helpers are looked up with ``getattr`` so a missing one fails on an assertion that names it.
At this head they are reached by these tests alone: the render sites that call them belong to the
cycles that add each page's copy.

Patch targets: ``advisors.retirement_recommender.CORRELATION_SCREEN_THRESHOLD`` and
``STRESS_WINDOW_FRACTION``, ``advisors.tenure_review.BACKTEST_TRAIL_WATCH_PP`` and
``autotuner.SIM_EXIT_FRICTION_PCT`` are patched on their source modules, because production
imports each one lazily inside the helper; ``app.ENV_FILE_PATH`` and
``app._tenure_review_settings`` are module-scope names of ``app``.
"""

from __future__ import annotations

import math
import pathlib

import pytest

import app as app_module
from advisors import retirement_recommender, tenure_review
from tests.ui import _page_copy as copy

SLOTS = (
    "tenure_threshold",
    "screen_threshold",
    "stress_fraction_pct",
    "watch_trail_pp",
    "friction_pct",
    "shadow_retention_days",
)
TENURE_KEY = "TENURE_REVIEW_MIN_TRADING_DAYS"
RETENTION_KEY = "SHADOW_HISTORY_RETENTION_DAYS"


def _helper(name: str):
    found = getattr(app_module, name, None)
    assert found is not None, f"app.py defines no {name}"
    assert callable(found), f"app.{name} is not callable"
    return found


@pytest.fixture
def plain_number():
    """The helper, looked up when called, so a missing one fails the test and not its setup."""
    return lambda *args, **kwargs: _helper("_plain_number")(*args, **kwargs)


@pytest.fixture
def values():
    return lambda *args, **kwargs: _helper("_page_copy_values")(*args, **kwargs)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """The settings file the page reads, empty; the process environment holds neither key."""
    path = tmp_path / "page_copy.env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    monkeypatch.delenv(TENURE_KEY, raising=False)
    monkeypatch.delenv(RETENTION_KEY, raising=False)
    return path


def _write_env(path: pathlib.Path, **entries: str) -> None:
    path.write_text("".join(f"{k}={v}\n" for k, v in entries.items()), encoding="utf-8")


def _autotuner():
    import autotuner

    return autotuner


# ---------------------------------------------------------------------------
# _plain_number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (0.5, 6, "0.5"),
        (0.25, 6, "0.25"),
        (7.5, 6, "7.5"),
        (10.0, 6, "10"),
        (5.0, 1, "5"),
        (100, 1, "100"),
        (120.0, 6, "120"),
        (126, 1, "126"),
        (0.0, 6, "0"),
        (12.3456789, 6, "12.345679"),
        (0.1 + 0.2, 6, "0.3"),
        (5.04, 1, "5"),
        (5.06, 1, "5.1"),
        (10_000_000.0, 1, "10000000"),
        (-2.5, 1, "-2.5"),
        (120, 0, "120"),
        (100.4, 0, "100"),
        (0.0, 0, "0"),
    ],
)
def test_a_finite_number_prints_rounded_with_trailing_zeros_and_a_bare_point_stripped(
    plain_number, value, decimals, expected
):
    assert plain_number(value, decimals=decimals) == expected


@pytest.mark.parametrize(
    "value",
    [None, True, False, float("nan"), float("inf"), float("-inf"), "5", "abc", [], {}, 1j],
    ids=repr,
)
def test_a_value_that_is_not_a_finite_real_number_is_none(plain_number, value):
    assert plain_number(value, decimals=1) is None


def test_the_decimals_are_keyword_only(plain_number):
    with pytest.raises(TypeError):
        plain_number(1.5, 1)


# ---------------------------------------------------------------------------
# What the helper returns
# ---------------------------------------------------------------------------


def test_it_returns_exactly_the_requested_slots_as_strings(values, env_file):
    out = values(SLOTS)
    assert set(out) == set(SLOTS)
    for slot, text in out.items():
        assert isinstance(text, str) and text, f"{slot}: {text!r}"


def test_it_resolves_only_what_it_is_asked_for(values, env_file, monkeypatch):
    def refuse():
        raise AssertionError("an authority nobody asked for was read")

    monkeypatch.setattr(app_module, "_tenure_review_settings", refuse)
    assert set(values(["friction_pct"])) == {"friction_pct"}
    assert values([]) == {}


# ---------------------------------------------------------------------------
# tenure_threshold (S-4.1, S-4.2, S-4.3)
# ---------------------------------------------------------------------------


def test_the_settings_file_wins_over_the_process_environment_when_no_review_is_stored(
    values, env_file, monkeypatch
):
    _write_env(env_file, **{TENURE_KEY: "90"})
    monkeypatch.setenv(TENURE_KEY, "120")
    assert values(["tenure_threshold"])["tenure_threshold"] == "90"


def test_the_process_environment_is_read_only_when_the_file_holds_no_value(
    values, env_file, monkeypatch
):
    monkeypatch.setenv(TENURE_KEY, "120")
    assert values(["tenure_threshold"])["tenure_threshold"] == "120"


def test_a_stored_reviews_threshold_in_effect_wins_over_the_environment(
    values, env_file, monkeypatch
):
    _write_env(env_file, **{TENURE_KEY: "90"})
    monkeypatch.setenv(TENURE_KEY, "120")
    out = values(["tenure_threshold"], tenure_review={"threshold_in_effect": 126})
    assert out["tenure_threshold"] == "126"


def test_an_integral_float_threshold_in_effect_prints_without_a_point(values, env_file):
    out = values(["tenure_threshold"], tenure_review={"threshold_in_effect": 126.0})
    assert out["tenure_threshold"] == "126"


@pytest.mark.parametrize(
    "stored",
    [126.5, True, "126", None, float("nan"), float("inf")],
    ids=repr,
)
def test_a_threshold_in_effect_that_is_not_a_whole_number_falls_back_to_the_environment(
    values, env_file, stored
):
    _write_env(env_file, **{TENURE_KEY: "90"})
    out = values(["tenure_threshold"], tenure_review={"threshold_in_effect": stored})
    assert out["tenure_threshold"] == "90"


@pytest.mark.parametrize("review", [None, {}, [], "review"], ids=repr)
def test_no_usable_stored_review_reads_the_configured_value(values, env_file, review):
    _write_env(env_file, **{TENURE_KEY: "90"})
    assert values(["tenure_threshold"], tenure_review=review)["tenure_threshold"] == "90"


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-5", "12.5"], ids=repr)
def test_an_unusable_configured_value_prints_the_default_the_next_review_would_use(
    values, env_file, raw
):
    if raw is not None:
        _write_env(env_file, **{TENURE_KEY: raw})
    out = values(["tenure_threshold"])
    assert out["tenure_threshold"] == str(tenure_review.DEFAULT_MIN_TENURE_TRADING_DAYS)
    assert out["tenure_threshold"] == str(tenure_review.parse_threshold(raw).value)


def test_a_stored_review_does_not_need_the_settings_file(values, env_file, monkeypatch):
    def broken():
        raise OSError("settings unreadable")

    monkeypatch.setattr(app_module, "_tenure_review_settings", broken)
    out = values(["tenure_threshold"], tenure_review={"threshold_in_effect": 126})
    assert out["tenure_threshold"] == "126"


# ---------------------------------------------------------------------------
# screen_threshold (S-4.4, S-4.11)
# ---------------------------------------------------------------------------


def test_the_screen_threshold_reads_the_recommenders_constant_when_no_run_is_stored(
    values, env_file, monkeypatch
):
    monkeypatch.setattr(retirement_recommender, "CORRELATION_SCREEN_THRESHOLD", 0.7)
    assert values(["screen_threshold"])["screen_threshold"] == "0.7"


def test_an_ok_runs_threshold_wins_so_the_text_and_the_pairs_tile_agree(
    values, env_file, monkeypatch
):
    monkeypatch.setattr(retirement_recommender, "CORRELATION_SCREEN_THRESHOLD", 0.7)
    run = {"status": "ok", "screen_threshold": 0.65}
    out = values(["screen_threshold"], retirement_last_run=run)
    assert out["screen_threshold"] == f"{run['screen_threshold']}" == "0.65"


def test_an_integer_run_threshold_prints_as_the_tile_prints_it(values, env_file):
    run = {"status": "ok", "screen_threshold": 1}
    assert values(["screen_threshold"], retirement_last_run=run)["screen_threshold"] == "1"


@pytest.mark.parametrize(
    "run",
    [
        {"status": "internal_error", "screen_threshold": 0.5},
        {"status": "series_unavailable", "screen_threshold": 0.5},
        {"screen_threshold": 0.5},
        {"status": "ok", "screen_threshold": None},
        {"status": "ok"},
        {"status": "ok", "screen_threshold": True},
        {"status": "ok", "screen_threshold": "0.5"},
        {"status": "ok", "screen_threshold": float("nan")},
        {"status": "ok", "screen_threshold": float("inf")},
        None,
        [],
        "ok",
    ],
    ids=repr,
)
def test_a_run_that_is_not_ok_or_has_no_usable_threshold_leaves_the_constant(
    values, env_file, monkeypatch, run
):
    monkeypatch.setattr(retirement_recommender, "CORRELATION_SCREEN_THRESHOLD", 0.7)
    assert values(["screen_threshold"], retirement_last_run=run)["screen_threshold"] == "0.7"


# ---------------------------------------------------------------------------
# stress_fraction_pct (S-4.5), watch_trail_pp (S-4.6), friction_pct (S-4.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.1, "10"), (0.07, "7"), (0.075, "7.5"), (0.123, "12.3"), (0.5, "50")],
)
def test_the_stress_fraction_prints_as_a_percentage(
    values, env_file, monkeypatch, fraction, expected
):
    monkeypatch.setattr(retirement_recommender, "STRESS_WINDOW_FRACTION", fraction)
    assert values(["stress_fraction_pct"])["stress_fraction_pct"] == expected


def test_the_stress_fraction_follows_the_constant_it_is_not_typed(values, env_file):
    shipped = retirement_recommender.STRESS_WINDOW_FRACTION * 100
    text = values(["stress_fraction_pct"])["stress_fraction_pct"]
    assert math.isclose(float(text), shipped, abs_tol=0.05)


@pytest.mark.parametrize(
    ("margin", "expected"),
    [(7.5, "7.5"), (10.0, "10"), (12.3456789, "12.345679"), (0.25, "0.25")],
)
def test_the_watch_margin_prints_with_no_trailing_zeros(
    values, env_file, monkeypatch, margin, expected
):
    monkeypatch.setattr(tenure_review, "BACKTEST_TRAIL_WATCH_PP", margin)
    assert values(["watch_trail_pp"])["watch_trail_pp"] == expected


@pytest.mark.parametrize(("cost", "expected"), [(0.25, "0.25"), (0.5, "0.5"), (1.0, "1")])
def test_the_exit_cost_prints_with_no_trailing_zeros(values, env_file, monkeypatch, cost, expected):
    monkeypatch.setattr(_autotuner(), "SIM_EXIT_FRICTION_PCT", cost)
    assert values(["friction_pct"])["friction_pct"] == expected


@pytest.mark.parametrize(
    ("module", "name", "slot"),
    [
        (retirement_recommender, "STRESS_WINDOW_FRACTION", "stress_fraction_pct"),
        (tenure_review, "BACKTEST_TRAIL_WATCH_PP", "watch_trail_pp"),
    ],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, True, "ten"], ids=repr)
def test_a_constant_that_is_not_a_finite_number_gives_none(
    values, env_file, monkeypatch, module, name, slot, bad
):
    monkeypatch.setattr(module, name, bad)
    assert values([slot])[slot] is None


# ---------------------------------------------------------------------------
# shadow_retention_days (S-4.12)
# ---------------------------------------------------------------------------


def test_the_retention_reads_the_settings_file(values, env_file):
    _write_env(env_file, **{RETENTION_KEY: "90"})
    assert values(["shadow_retention_days"])["shadow_retention_days"] == "90"


def test_the_retention_defaults_when_the_file_does_not_hold_it(values, env_file):
    out = values(["shadow_retention_days"])["shadow_retention_days"]
    assert out == "180"


def test_the_retention_ignores_the_process_environment_as_the_nightly_prune_does(
    values, env_file, monkeypatch
):
    monkeypatch.setenv(RETENTION_KEY, "45")
    assert values(["shadow_retention_days"])["shadow_retention_days"] == "180"


def test_a_missing_settings_file_gives_the_default(values, env_file, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))
    assert values(["shadow_retention_days"])["shadow_retention_days"] == "180"


@pytest.mark.parametrize("raw", ["abc", "", "12.5", "ninety"], ids=repr)
def test_a_retention_the_prune_could_not_read_gives_none(values, env_file, raw):
    _write_env(env_file, **{RETENTION_KEY: raw})
    assert values(["shadow_retention_days"])["shadow_retention_days"] is None


# ---------------------------------------------------------------------------
# S-4.9: one authority fails at a time
# ---------------------------------------------------------------------------


def _break_tenure(monkeypatch, env_file):
    def broken():
        raise OSError("settings unreadable")

    monkeypatch.setattr(app_module, "_tenure_review_settings", broken)


def _break_screen(monkeypatch, env_file):
    monkeypatch.delattr(retirement_recommender, "CORRELATION_SCREEN_THRESHOLD")


def _break_stress(monkeypatch, env_file):
    monkeypatch.delattr(retirement_recommender, "STRESS_WINDOW_FRACTION")


def _break_watch(monkeypatch, env_file):
    monkeypatch.delattr(tenure_review, "BACKTEST_TRAIL_WATCH_PP")


def _break_friction(monkeypatch, env_file):
    monkeypatch.delattr(_autotuner(), "SIM_EXIT_FRICTION_PCT")


def _break_retention(monkeypatch, env_file):
    _write_env(env_file, **{RETENTION_KEY: "abc"})


BREAKERS = {
    "tenure_threshold": _break_tenure,
    "screen_threshold": _break_screen,
    "stress_fraction_pct": _break_stress,
    "watch_trail_pp": _break_watch,
    "friction_pct": _break_friction,
    "shadow_retention_days": _break_retention,
}


@pytest.mark.parametrize("slot", SLOTS)
def test_one_failing_authority_gives_none_for_its_slot_and_leaves_the_others(
    values, env_file, monkeypatch, slot
):
    # Warm the lazily imported modules before an attribute is removed from one of them.
    assert all(isinstance(v, str) for v in values(SLOTS).values())
    BREAKERS[slot](monkeypatch, env_file)

    out = values(SLOTS)

    assert out[slot] is None, f"{slot} still printed {out[slot]!r}"
    for other in SLOTS:
        if other != slot:
            assert isinstance(out[other], str) and out[other], f"{other} was lost with {slot}"


def test_the_values_table_names_every_slot_the_helper_resolves():
    table = {row["slot"] for row in copy.slot_rows()}
    assert set(SLOTS) <= table
    assert table - set(SLOTS) == {"min_history_days"}, "a Values slot the helper does not own"


# ---------------------------------------------------------------------------
# One number check: the helpers app.py already has, including for an int too large for a float
# ---------------------------------------------------------------------------

HUGE = 10**400
NUMBER_SAMPLES = [
    0,
    3,
    -3,
    3.0,
    3.5,
    1e300,
    HUGE,
    -HUGE,
    True,
    False,
    float("nan"),
    float("inf"),
    float("-inf"),
    "3",
    None,
    [],
]


def _sample_id(value) -> str:
    text = repr(value)
    return text if len(text) < 24 else f"int-of-{len(text.lstrip('-'))}-digits"


def _source_names(function_name: str) -> set[str]:
    """Every name and attribute the function's own body reads (its docstring excluded)."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_helper(function_name))))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


@pytest.mark.parametrize(
    ("function", "existing"),
    [
        ("_plain_number", "_is_finite_number"),
        ("_whole_number_or_none", "_is_whole_number"),
        ("_copy_screen_threshold", "_is_finite_number"),
    ],
)
def test_a_copy_helper_calls_the_number_check_the_module_already_has(function, existing):
    names = _source_names(function)
    assert existing in names, f"{function} does not call {existing}"
    assert "isfinite" not in names, f"{function} writes its own finite check beside {existing}"


@pytest.mark.parametrize("value", NUMBER_SAMPLES, ids=_sample_id)
def test_plain_number_answers_a_number_exactly_when_the_existing_check_does(value):
    finite = _helper("_is_finite_number")
    answer = _helper("_plain_number")(value, decimals=6)
    assert (answer is not None) == finite(value), f"{value!r}: {answer!r}"


def test_an_int_too_large_for_a_float_prints_its_digits_and_raises_nothing(plain_number):
    assert plain_number(HUGE, decimals=6) == str(HUGE)
    assert plain_number(-HUGE, decimals=2) == str(-HUGE)


@pytest.mark.parametrize("value", NUMBER_SAMPLES, ids=_sample_id)
def test_whole_number_or_none_answers_a_number_exactly_when_the_existing_check_does(value):
    whole = _helper("_is_whole_number")
    answer = _helper("_whole_number_or_none")(value)
    assert (answer is not None) == whole(value), f"{value!r}: {answer!r}"
    if answer is not None:
        assert answer == int(value) and type(answer) is int


@pytest.mark.parametrize("threshold", [HUGE, -HUGE], ids=["huge", "huge negative"])
def test_a_run_threshold_too_large_for_a_float_is_a_number_as_the_existing_check_says(threshold):
    """No OverflowError: the one helper the module has counts an int as finite, so it prints."""
    assert _helper("_is_finite_number")(threshold)
    run = {"status": "ok", "screen_threshold": threshold}
    assert _helper("_copy_screen_threshold")(run) == f"{threshold}"


@pytest.mark.parametrize("threshold", [HUGE, -HUGE], ids=["huge", "huge negative"])
def test_a_threshold_in_effect_too_large_for_a_float_is_handled_without_error(
    values, env_file, threshold
):
    review = {"threshold_in_effect": threshold}
    assert values(["tenure_threshold"], tenure_review=review)["tenure_threshold"] == str(threshold)


@pytest.mark.parametrize(
    "name",
    [
        "_copy_tenure_threshold",
        "_copy_screen_threshold",
        "_copy_stress_fraction_pct",
        "_copy_watch_trail_pp",
        "_copy_friction_pct",
        "_copy_min_history_days",
        "_copy_shadow_retention_days",
    ],
)
def test_each_copy_resolver_says_what_it_reads_and_what_none_means(name):
    import inspect

    resolver = _helper(name)
    doc = inspect.getdoc(resolver) or ""
    assert len(doc.split()) >= 6, f"{name} has no docstring naming its authority: {doc!r}"
    if "None" in str(inspect.signature(resolver).return_annotation):
        assert "None" in doc, f"{name} can return None and its docstring does not say when"
