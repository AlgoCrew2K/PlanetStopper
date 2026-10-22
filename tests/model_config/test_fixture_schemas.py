"""The Anthropic model-list and error fixtures conform to the public API references.

Schema-derived fixtures are acceptable only when the validator written from the
references passes on them, and the validator itself must be able to fail: each
rule is exercised on a planted violation, and the captured page must pass it,
so it is neither vacuous nor stricter than the real API.
"""

from __future__ import annotations

import copy
import importlib.util
import re

import pytest

from tests._anthropic_http_fake import FIXTURES_DIR, load_fixture


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "anthropic_fixture_schema_check", FIXTURES_DIR / "schema_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema_check = _load_validator()
CAPTURED_PAGE = load_fixture("models/page_captured.json")


def _fixture_names(folder):
    return sorted(p.name for p in (FIXTURES_DIR / folder).glob("*.json"))


@pytest.mark.parametrize("name", _fixture_names("models"))
def test_every_model_list_fixture_passes_the_validator(name):
    problems = schema_check.check_models_file(load_fixture(f"models/{name}"), name)

    assert problems == []


@pytest.mark.parametrize("name", _fixture_names("errors"))
def test_every_error_fixture_passes_the_validator(name):
    problems = schema_check.check_error_file(load_fixture(f"errors/{name}"), name)

    assert problems == []


def test_the_validator_directory_walk_covers_every_models_and_errors_file():
    report = schema_check.validate_directory()

    expected = {f"models/{n}" for n in _fixture_names("models")} | {
        f"errors/{n}" for n in _fixture_names("errors")
    }
    assert set(report) == expected
    assert not any(report.values())


def test_one_error_fixture_exists_for_each_status_the_listing_can_fail_with():
    statuses = {load_fixture(f"errors/{n}")["status"] for n in _fixture_names("errors")}

    assert {400, 401, 403, 404, 429, 500, 503, 529} <= statuses


def test_schema_derived_model_records_reuse_the_captured_ids_except_one_labelled_non_prefixed_id():
    captured_ids = {r["id"] for r in CAPTURED_PAGE["data"]}
    foreign: dict[str, set[str]] = {}
    for name in _fixture_names("models"):
        if name == "page_captured.json":
            continue
        text = (FIXTURES_DIR / "models" / name).read_text(encoding="utf-8")
        ids = set(re.findall(r'"id": "([^"]+)"', text))
        if ids - captured_ids:
            foreign[name] = ids - captured_ids

    assert set(foreign) == {"no_line.json"}
    (invented,) = foreign["no_line.json"]
    assert any(invented.endswith(real) and invented != real for real in captured_ids)


def _model_mutations():
    def page(**changes):
        data = copy.deepcopy(CAPTURED_PAGE)
        data.update(changes)
        return data

    def record_edit(index, **changes):
        data = copy.deepcopy(CAPTURED_PAGE)
        data["data"][index].update(changes)
        return data

    def drop(index, key):
        data = copy.deepcopy(CAPTURED_PAGE)
        del data["data"][index][key]
        return data

    return {
        "has_more not boolean": page(has_more="false"),
        "first_id names the wrong record": page(first_id=CAPTURED_PAGE["data"][1]["id"]),
        "last_id names the wrong record": page(last_id=CAPTURED_PAGE["data"][0]["id"]),
        "data not an array": page(data={}),
        "unknown lifecycle": record_edit(0, lifecycle="paused"),
        "created_at not RFC 3339": record_edit(0, created_at="28 Aug 2026"),
        "type not model": record_edit(0, type="snapshot"),
        "line not a string": record_edit(0, line=5),
        "retires_at not a date": record_edit(0, retires_at="soon"),
        "id missing": drop(0, "id"),
        "display_name missing": drop(0, "display_name"),
        "duplicate id": record_edit(1, id=CAPTURED_PAGE["data"][0]["id"]),
    }


@pytest.mark.parametrize("label", list(_model_mutations()))
def test_the_models_validator_rejects_a_planted_violation(label):
    assert schema_check.check_models_file(_model_mutations()[label], label) != []


def test_the_validator_rejects_pages_that_break_the_paging_contract():
    pages = load_fixture("models/paged.json")
    last_page_open = copy.deepcopy(pages)
    last_page_open["pages"][1]["has_more"] = True
    first_page_closed = copy.deepcopy(pages)
    first_page_closed["pages"][0]["has_more"] = False
    single = {"pages": pages["pages"][:1]}

    for broken in (last_page_open, first_page_closed, single):
        assert schema_check.check_models_file(broken) != []


def test_the_validator_rejects_a_case_whose_expectation_names_an_absent_id():
    cases = copy.deepcopy(load_fixture("models/edge_cases.json"))
    name = next(iter(cases["cases"]))
    cases["cases"][name]["expect"]["suggestion"] = "an-id-the-page-does-not-hold"

    assert schema_check.check_models_file(cases) != []


def _error_mutations():
    base = load_fixture("errors/503_unavailable.json")

    def edit(path, value):
        data = copy.deepcopy(base)
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return data

    return {
        "undocumented status": edit(["status"], 418),
        "error type does not match the status": edit(["body", "error", "type"], "not_found_error"),
        "body type not error": edit(["body", "type"], "message"),
        "empty message": edit(["body", "error", "message"], ""),
        "malformed request id": edit(["body", "request_id"], "TEST-ID-001"),
        "extra top-level key": {**base, "extra": 1},
    }


@pytest.mark.parametrize("label", list(_error_mutations()))
def test_the_error_validator_rejects_a_planted_violation(label):
    assert schema_check.check_error_file(_error_mutations()[label], label) != []
