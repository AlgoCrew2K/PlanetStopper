"""
The MC Prob statistic is never described as "probability this symphony beats SPY".

The statistic gates the trailing-stop CONFIRM (a high reading confirms underperformance against
the symphony's own regime-matched historical baseline) and the take-profit ARM (a low reading of
the same statistic), two mechanisms in opposite directions, so no wording that says it measures
beating SPY is truthful. A sweep over every file under ``templates/`` and ``static/`` finds none.
``.design-handoff/`` sits outside both directories and is not a live-serving surface (``app.py``
uses ``Flask(__name__)`` with no ``template_folder`` or ``static_folder`` override).
"""

from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


def test_no_beats_spy_claim_anywhere_under_templates_or_static() -> None:
    """Repo-wide sweep (per team-lead's explicit instruction): zero
    occurrences of "beats SPY" (case-insensitive) anywhere under
    templates/ or static/. .design-handoff/ sits outside both directories
    and is excluded from this sweep by construction (verified unreachable
    by Flask -- no template_folder/static_folder override -- so it is not
    a live-serving surface)."""
    hits: list[str] = []
    for directory_name in ("templates", "static"):
        directory = _REPO_ROOT / directory_name
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "beats spy" in text.lower():
                hits.append(str(path.relative_to(_REPO_ROOT)))

    assert not hits, (
        f"Found 'beats SPY' (case-insensitive) in the following "
        f"live-serving files: {hits!r}. Every occurrence under templates/ "
        f"or static/ must be swept and corrected -- silence on any hit is "
        f"a finding."
    )
