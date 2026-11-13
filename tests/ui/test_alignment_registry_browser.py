"""Every registry entry marked as read from the reference markup is live in the seeded world.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_alignment_registry_browser.py``).

``tests/ui/_alignment.py`` lists every repeated row group of the in-scope hosts with the selectors
a measure needs. A registry whose selectors match nothing measures nothing: a column rule that
finds no table passes. So each entry whose status is ``ref`` is opened the way its ``open`` field
says, over the seeded ``full`` world (with the entry's ``seed`` written first), and must show its
group, its rows and every non-optional field in at least one row. This test asserts presence, never
alignment: at the reference the groups are not aligned yet, and the cycle that changes a group
re-pins the entry here when it renames a selector.
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_probe,
)

pytestmark = pytest.mark.browser

SEEDS = {
    "strategy_builder": lambda: adv.seed_strategy_builder(survivors=3, backtest_failures=1),
    "frontrunner_proposals": lambda: adv.seed_frontrunner_proposals(5),
    "recommendation_cards": lambda: adv.seed_recommendation_cards(3),
}
SHOWN = (
    "(sel) => [...document.querySelectorAll(sel)]"
    ".filter((e) => e.getClientRects().length > 0).length"
)
ROWS_WITH_FIELD = (
    "([rows, field]) => [...document.querySelectorAll(rows)]"
    ".filter((r) => [...r.querySelectorAll(field)].some((e) => e.getClientRects().length > 0))"
    ".length"
)

LIVE_ENTRIES = [
    (registry, name)
    for registry in ("TABLE_GROUPS", "TOOLBAR_ROWS", "FIGURE_LINES")
    for name, entry in getattr(al, registry).items()
    if entry["status"] == "ref"
]


@pytest.mark.parametrize(("registry", "name"), LIVE_ENTRIES, ids=[n for _, n in LIVE_ENTRIES])
def test_a_registry_entry_read_from_the_reference_is_live_in_the_seeded_world(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    registry,
    name,
):
    entry = getattr(al, registry)[name]
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world("full", tmp_path, monkeypatch.setattr)
    # The Strategy Builder and Asset Swaps tabs render their controls only once the advisor has its
    # keys; the placeholder is no credential and no request leaves the process.
    import advisors.asset_swap_engine as asset_swap_engine

    monkeypatch.setenv("ANTHROPIC_API_KEY", harness.PLACEHOLDER_KEY)
    monkeypatch.setattr(asset_swap_engine, "_has_composer_key", lambda: True)
    if entry.get("seed"):
        SEEDS[entry["seed"]]()
    with open_probe(firefox, advisor_server, width=1440, height=al.VIEWPORT_HEIGHT) as probe:
        al.open_group(probe, entry)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        for key in ("group", "rows", "row", "line"):
            if entry.get(key):
                assert probe.page.evaluate(SHOWN, entry[key]) > 0, (
                    f"{name}: no shown element matches {key} {entry[key]!r}"
                )
        for field in entry.get("fields", []):
            if field.get("optional"):
                continue
            found = probe.page.evaluate(ROWS_WITH_FIELD, [entry["rows"], field["selector"]])
            assert found > 0, f"{name}: no shown row holds the field {field['name']!r}"
