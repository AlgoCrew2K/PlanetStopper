"""The sentence a triggered card's verdict reads, built from the approved copy rows.

TEST-ONLY. The three verdict sentences live in ``tests/fixtures/page_explainers/copy.json`` (read
through ``tests/ui/_page_copy.py``); this module only chooses between them by the rule the page
states: the exit return minus the held return, shown to one decimal, reads "matched" exactly when
that magnitude prints as 0.0 and otherwise names the side by the sign. The magnitude is always the
caller's, taken from the browser's own ``toFixed`` (``js_to_fixed``), never from Python formatting.
"""

from __future__ import annotations

from tests.ui import _page_copy as copy

MATCHED_MAGNITUDE = "0.0"


def verdict_sentence(guard_alpha: float, magnitude: str) -> str:
    """The verdict text for ``guard_alpha`` whose one-decimal magnitude prints as ``magnitude``."""
    if magnitude == MATCHED_MAGNITUDE:
        return copy.text("D30c", "C")
    key = "D30a" if guard_alpha > 0 else "D30b"
    return copy.fill(copy.text(key, "C"), m=magnitude)


def verdict_lead(guard_alpha_sign: str) -> str:
    """The words before the figure: ``beat``, ``trailed`` or ``matched`` (a prefix to search for)."""
    key = {"beat": "D30a", "trailed": "D30b", "matched": "D30c"}[guard_alpha_sign]
    return copy.text(key, "C").split("{m}")[0].rstrip()
