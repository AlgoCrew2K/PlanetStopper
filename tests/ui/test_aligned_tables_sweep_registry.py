"""The sweep of ``test_aligned_tables_browser.py`` leaves no repeated row group out unseen.

No browser. The registry of ``tests/ui/_alignment.py`` lists every repeated row group, toolbar row
and figure line of the in-scope hosts. A group the sweep never measures would pass whatever the page
did, so each registry entry must be in a state the sweep opens, or carry a stated reason in
``EXCUSED``; and the widths the sweep runs at are the plan's, one in each layout band from 1001 px up.
"""

from __future__ import annotations

from tests.ui import _alignment as al
from tests.ui import test_aligned_tables_browser as sweep

REGISTRIES = (sweep.TABLE, sweep.TOOLBAR, sweep.LINE)


def _registry_entries() -> set[tuple[str, str]]:
    return {(r, n) for r in REGISTRIES for n in getattr(al, r)}


def _swept() -> set[tuple[str, str]]:
    return {(r, n) for state in sweep.SWEEP_STATES for r, n, _ in state["groups"]}


def test_every_group_of_the_registry_is_swept_or_excused_with_a_reason():
    swept = _swept()
    assert swept <= _registry_entries(), (
        f"swept, not in the registry: {swept - _registry_entries()}"
    )
    left_out = _registry_entries() - swept - set(sweep.EXCUSED)
    assert not left_out, f"in the registry, not swept and not excused: {sorted(left_out)}"
    assert not set(sweep.EXCUSED) & swept, "a group is both swept and excused"
    assert all(reason.strip() for reason in sweep.EXCUSED.values()), "an excuse has no reason"


def test_an_excused_group_is_one_the_registry_holds():
    assert set(sweep.EXCUSED) <= _registry_entries()


def test_the_sweep_runs_at_one_width_in_every_layout_band_and_at_the_widest():
    """The widths of the layout rule's table: the boundary, a width in every band, and 2560 px."""
    assert al.WIDTHS == (1001, 1120, 1290, 1350, 1400, 1420, 1440, 1920, 2560)
    assert al.NARROW == (1000, 390, 320)
