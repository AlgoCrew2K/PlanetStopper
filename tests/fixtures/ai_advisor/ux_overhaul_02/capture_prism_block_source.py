"""Capture the Market Prism block's template text, normalized, as the baseline the order tests
compare the moved block with.

Run once on the unmodified base, before the block moves:

    python tests/fixtures/ai_advisor/ux_overhaul_02/capture_prism_block_source.py

It reads ``templates/ai_advisor.html`` and writes ``prism_block_source_baseline.txt`` beside it.
Nothing is edited between the read and the write. PROVENANCE.md names the commit it ran at.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from tests._advisor_overview_prism import BASELINE, TEMPLATE, prism_block_source  # noqa: E402


def main() -> None:
    text = prism_block_source(TEMPLATE.read_text(encoding="utf-8"))
    BASELINE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {BASELINE} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
