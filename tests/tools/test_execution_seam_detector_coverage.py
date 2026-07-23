"""RED-style regression guard — task #48 (r2-3-review's non-blocking
finding): tests/tools/execution_seam_detector.py's _R2_3_DEFAULT_TARGETS
must stay complete as the asset-swap test surface grows.

WHY THIS EXISTS: the original 13-file default list silently omitted 6 files
that genuinely exercise suggest_swaps/the reasoned path — a casual
no-args `python tests/tools/execution_seam_detector.py` run gave false
confidence (a live-API leak in one of the omitted files would never be
caught). This test is a standing tripwire against the SAME class of drift
recurring: it scans for every test file whose NAME matches the established
asset-swap-cluster naming conventions and asserts each one is present in
the default target list.

SCOPE (deliberately narrow, not "any file that imports asset_swap_engine"):
matches by filename pattern only, restricted to the naming conventions this
project's asset-swap test cluster already uses
(test_asset_swap_*, test_cycle3_lens_*, test_lens_blend_efficacy,
test_weekly_asset_swap_*, test_advisor_liveness_gate). A broader
"mentions asset_swap_engine anywhere" scan would false-positive on
cross-cutting tests (e.g. test_r1_wiring_completeness.py,
test_advisor_route_producer_guard.py) that reference the module
incidentally, not as a primary reasoned-path exerciser -- those are
deliberately NOT required here.

This test itself makes no network calls and does not invoke the detector's
own pytest.main() sub-run -- it is a pure static filename-set comparison,
safe to auto-collect under the normal test suite (unlike
execution_seam_detector.py itself, which stays uncollected by design).
"""

from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Glob patterns matching this project's established asset-swap test-cluster
# naming conventions (see module docstring for why this scope, not broader).
_ASSET_SWAP_CLUSTER_GLOBS: tuple[str, ...] = (
    "tests/advisors/test_asset_swap_*.py",
    "tests/ai_advisor/test_asset_swap_*.py",
    "tests/ui/test_asset_swap_*.py",
    "tests/ai_advisor/test_cycle3_lens_*.py",
    "tests/ai_advisor/test_lens_blend_efficacy.py",
    "tests/advisors/test_weekly_asset_swap_*.py",
    "tests/advisors/test_advisor_liveness_gate.py",
    # "as-" abbreviation prefix (mirrors the as-live-generation-provenance
    # testid convention) -- test_as_live_generation_provenance_render.py
    # doesn't match the "asset_swap"-prefixed patterns above.
    "tests/ai_advisor/test_as_live_*.py",
)


def _asset_swap_cluster_files() -> set[str]:
    found: set[str] = set()
    for pattern in _ASSET_SWAP_CLUSTER_GLOBS:
        for path in _REPO_ROOT.glob(pattern):
            # Normalize to the forward-slash-relative form the detector's
            # own target list uses.
            rel = path.relative_to(_REPO_ROOT).as_posix()
            found.add(rel)
    return found


def _default_targets() -> set[str]:
    import importlib.util
    import sys

    module_path = _REPO_ROOT / "tests" / "tools" / "execution_seam_detector.py"
    spec = importlib.util.spec_from_file_location(
        "execution_seam_detector_coverage_check", module_path
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec so any internal relative imports resolve cleanly.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return set(module._R2_3_DEFAULT_TARGETS)


def test_default_targets_cover_the_full_asset_swap_test_cluster():
    """Every test file matching the established asset-swap naming
    conventions must be present in _R2_3_DEFAULT_TARGETS -- a new file added
    to this cluster without also being added to the default list would
    silently under-cover future no-args seam-detector runs, exactly the gap
    r2-3-review found."""
    cluster = _asset_swap_cluster_files()
    targets = _default_targets()

    missing = cluster - targets
    assert not missing, (
        f"execution_seam_detector.py's _R2_3_DEFAULT_TARGETS is missing "
        f"{sorted(missing)} — these files match the asset-swap test-cluster "
        f"naming conventions but are not covered by a default (no-args) "
        f"seam-detector run. Add them to _R2_3_DEFAULT_TARGETS."
    )


def test_cluster_scan_self_guard_finds_known_files():
    """Self-guard: the glob patterns above must find at least the files we
    know exist today — if this fails, the glob patterns themselves are
    stale/broken and the coverage test above proves nothing."""
    cluster = _asset_swap_cluster_files()
    assert "tests/ai_advisor/test_lens_blend_efficacy.py" in cluster, (
        "self-guard failed: the cluster scan no longer finds "
        "test_lens_blend_efficacy.py — the glob patterns are stale."
    )
    assert len(cluster) >= 19, (
        f"self-guard failed: expected at least 19 asset-swap cluster files, "
        f"found {len(cluster)}: {sorted(cluster)}. The glob patterns may have "
        f"narrowed unexpectedly."
    )
