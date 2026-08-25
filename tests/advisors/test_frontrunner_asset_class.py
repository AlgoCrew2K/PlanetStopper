"""RED tests — advisors.frontrunner_builder asset_class propagation
(feature-plans/frontrunner-asset-class.md, DE-FR-ASSET-CLASS-001).

GAP THIS FILE CLOSES: `approve_frontrunner_proposal` (advisors/frontrunner_
builder.py, save_symphony call ~:2504-2511) calls `composer_draft_client.
save_symphony` WITHOUT an `asset_class` kwarg — every frontrunner draft is
silently stamped `_DEFAULT_ASSET_CLASS="EQUITIES"` regardless of the real
incumbent symphony's asset class, which is already available on
`candidate_tree`'s top-level `asset_class`/`asset_classes` keys (the splice
preserves them from the incumbent's real Composer score tree).

DESIGN (plan §Architecture, PM-approved): a new pure helper
`_resolve_draft_asset_class(candidate_tree: dict | None) -> str` lives in
`advisors/frontrunner_builder.py` (NOT `composer_draft_client.py` — that
module's `save_symphony` keeps its "never inspect raw_value" transport
contract and its `_DEFAULT_ASSET_CLASS` default byte-unchanged). The call
site derives `asset_class = _resolve_draft_asset_class(candidate_tree)` and
passes it explicitly: `save_symphony(..., asset_class=asset_class)`.
D-1 never-raises throughout — any malformed/None/non-dict candidate_tree or
internal error degrades to "EQUITIES", never raises out of
`approve_frontrunner_proposal`.

CANONICAL ENUM: {"EQUITIES", "CRYPTO", "OPTIONS"} — case-exact, no coercion.

FIXTURE PROVENANCE — independently re-verified this cycle by loading all 11
`tests/fixtures/advisors/frontrunner/real_tree_*.json` files directly:
EXACTLY 5 carry top-level `asset_class="EQUITIES"` + `asset_classes=
["EQUITIES"]` (02/03/04/06/09), and 6 carry NEITHER key (01/05/07/08/10/11).
No fixture carries a non-EQUITIES value — CRYPTO propagation is proven via a
deepcopy-and-stamp synthetic construction (never mutates the on-disk
fixture / shared object).

PATCH-TARGET CONVENTION (mirrors test_frontrunner_approval.py /
test_frontrunner_proposal_identity.py Group E): patch collaborators at their
ORIGIN module — database.get_frontrunner_proposal, database.load_state,
database.update_frontrunner_proposal_status, database.
insert_advisor_observation, advisors.composer_draft_client.save_symphony,
advisors.composer_draft_client.verify_undeployed — never
approve_frontrunner_proposal's own module attributes.

MOCK SEMANTICS NOTE: `advisors.composer_draft_client.save_symphony` is fully
mocked in the wiring tests below, so the mock does NOT apply the real
function's `asset_class: str = _DEFAULT_ASSET_CLASS` parameter default —
`mock_save.call_args.kwargs.get("asset_class")` is `None` (key absent)
whenever the caller doesn't pass it explicitly, which is exactly today's
(pre-fix) behavior. This is what makes the wiring tests below genuinely RED
before the call-site change lands, not vacuously green.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"

_EQUITIES_CARRYING_FIXTURES = [
    "real_tree_02_8FAXAnQmYi1INDubazeC.json",
    "real_tree_03_Gpaw3IhZghQPRE6AdEKx.json",
    "real_tree_04_INfCn3eKsu6i4oTTqdUp.json",
    "real_tree_06_hvPiGP1O7AHfutHE3Fjy.json",
    "real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json",
]
_ABSENT_ASSET_CLASS_FIXTURES = [
    "real_tree_01_5XjzXjdGnjh99MIsdM97.json",
    "real_tree_05_MoAkUHnavSYw3oONiUxe.json",
    "real_tree_07_iaSOOUsmnCJHiZvbrWfs.json",
    "real_tree_08_lW4ZzWuqR8tEO2DhXbil.json",
    "real_tree_10_nOyb55RMGVCKPiYXv7TI.json",
    "real_tree_11_qF5ZU7ALjrlhxrGEwsyJ.json",
]


def _load_tree(filename: str) -> dict:
    return json.loads((_FIXTURES_DIR / filename).read_text())


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Unit tests: _resolve_draft_asset_class — top-level string
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassTopLevelString:
    @pytest.mark.parametrize("value", ["EQUITIES", "CRYPTO", "OPTIONS"])
    def test_returns_top_level_in_enum_string_verbatim(self, fbld, value):
        tree = {"asset_class": value}
        assert fbld._resolve_draft_asset_class(tree) == value

    def test_falls_back_to_equities_when_key_absent_and_no_array(self, fbld):
        assert fbld._resolve_draft_asset_class({"step": "root"}) == "EQUITIES"

    def test_falls_back_to_equities_when_asset_class_is_empty_string(self, fbld):
        assert fbld._resolve_draft_asset_class({"asset_class": ""}) == "EQUITIES"

    @pytest.mark.parametrize("bad_value", ["equities", "FOREX", "Crypto", "equity", " EQUITIES"])
    def test_falls_back_to_equities_for_out_of_enum_string(self, fbld, bad_value):
        assert fbld._resolve_draft_asset_class({"asset_class": bad_value}) == "EQUITIES"


# ---------------------------------------------------------------------------
# Unit tests: _resolve_draft_asset_class — non-string top-level value
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassNonStringTopLevel:
    @pytest.mark.parametrize("bad_value", [123, ["EQUITIES"], None, {"x": 1}, 1.5])
    def test_falls_back_to_equities_when_no_array_present(self, fbld, bad_value):
        tree = {"asset_class": bad_value}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    @pytest.mark.parametrize("bad_value", [123, ["EQUITIES"], None, {"x": 1}, 1.5])
    def test_falls_through_to_array_when_top_level_is_non_string(self, fbld, bad_value):
        """A non-string `asset_class` must NOT be treated as "present and
        usable" — the array must still be consulted. A naive implementation
        that only checks `"asset_class" not in tree` (instead of "is it a
        usable string") would wrongly skip the array here."""
        tree = {"asset_class": bad_value, "asset_classes": ["CRYPTO"]}
        assert fbld._resolve_draft_asset_class(tree) == "CRYPTO"


# ---------------------------------------------------------------------------
# Unit tests: _resolve_draft_asset_class — asset_classes array fallback
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassArrayFallback:
    @pytest.mark.parametrize(
        "array,expected",
        [
            (["CRYPTO"], "CRYPTO"),
            (["OPTIONS"], "OPTIONS"),
            (["EQUITIES"], "EQUITIES"),
            (["CRYPTO", "CRYPTO"], "CRYPTO"),
        ],
    )
    def test_uses_homogeneous_in_enum_array_element_when_top_level_absent(
        self, fbld, array, expected
    ):
        tree = {"asset_classes": array}
        assert fbld._resolve_draft_asset_class(tree) == expected

    def test_falls_back_to_equities_for_a_mixed_array(self, fbld):
        tree = {"asset_classes": ["EQUITIES", "CRYPTO"]}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    def test_falls_back_to_equities_for_an_out_of_enum_array(self, fbld):
        tree = {"asset_classes": ["FOREX"]}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    @pytest.mark.parametrize("bad_array", [{"a": 1}, "CRYPTO", 5, None])
    def test_falls_back_to_equities_when_asset_classes_is_not_a_list(self, fbld, bad_array):
        tree = {"asset_classes": bad_array}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    def test_falls_back_to_equities_when_asset_classes_is_an_empty_list(self, fbld):
        assert fbld._resolve_draft_asset_class({"asset_classes": []}) == "EQUITIES"

    @pytest.mark.parametrize("homogeneous_bad_value", [1, True, None, 3.5])
    def test_falls_back_to_equities_for_a_homogeneous_non_string_array(
        self, fbld, homogeneous_bad_value
    ):
        """Sufficiency-review addition: a homogeneous (single-distinct-value)
        array of a NON-string type must still fall back — pins the
        `isinstance(only, str)` guard specifically, distinct from the
        enum-membership check above (a dedup-then-return-without-a-type-
        check implementation would wrongly propagate `1`/`True`/`None`)."""
        tree = {"asset_classes": [homogeneous_bad_value, homogeneous_bad_value]}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    def test_falls_back_to_equities_without_crashing_on_unhashable_array_elements(self, fbld):
        """Sufficiency-review addition (D-1): an asset_classes array
        containing unhashable elements (e.g. a nested dict — a genuinely
        malformed capture) must not crash a `set(array)`-based dedup
        strategy. Distinct hostile path from the `.get()`-raises dict
        subclass test above — this one specifically exercises the
        array-processing branch's own exception safety, not the
        top-level `.get()` call."""
        tree = {"asset_classes": [{"unhashable": "dict"}, {"another": "dict"}]}
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"


# ---------------------------------------------------------------------------
# Unit tests: _resolve_draft_asset_class — precedence when both present
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassStringWinsOverArray:
    def test_top_level_string_wins_over_a_disagreeing_array(self, fbld):
        tree = {"asset_class": "CRYPTO", "asset_classes": ["EQUITIES"]}
        assert fbld._resolve_draft_asset_class(tree) == "CRYPTO"


# ---------------------------------------------------------------------------
# Unit tests: _resolve_draft_asset_class — malformed candidate_tree (AC-5, D-1)
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassMalformedTree:
    def test_falls_back_to_equities_when_tree_is_none(self, fbld):
        assert fbld._resolve_draft_asset_class(None) == "EQUITIES"

    @pytest.mark.parametrize("bad_tree", [[], "not-a-dict", 42, ("a", "b")])
    def test_falls_back_to_equities_when_tree_is_not_a_dict(self, fbld, bad_tree):
        assert fbld._resolve_draft_asset_class(bad_tree) == "EQUITIES"

    def test_falls_back_to_equities_when_tree_is_an_empty_dict(self, fbld):
        assert fbld._resolve_draft_asset_class({}) == "EQUITIES"

    def test_never_raises_when_get_itself_raises(self, fbld):
        """D-1 adversarial: a dict subclass whose .get() raises must not
        crash the helper — forces a genuine try/except around the read, not
        just an isinstance(dict) guard that would still blow up on .get()."""

        class _ExplodingDict(dict):
            def get(self, *args, **kwargs):
                raise RuntimeError("boom")

        hostile = _ExplodingDict()
        hostile["asset_class"] = "CRYPTO"  # populated via __setitem__, not .get
        assert fbld._resolve_draft_asset_class(hostile) == "EQUITIES"


# ---------------------------------------------------------------------------
# Real-fixture propagation
# ---------------------------------------------------------------------------


class TestResolveDraftAssetClassRealFixtures:
    @pytest.mark.parametrize("filename", _EQUITIES_CARRYING_FIXTURES)
    def test_reads_equities_from_real_fixtures_that_carry_it(self, fbld, filename):
        tree = _load_tree(filename)
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    @pytest.mark.parametrize("filename", _EQUITIES_CARRYING_FIXTURES)
    def test_propagates_crypto_when_a_real_equities_tree_is_stamped_crypto(self, fbld, filename):
        """The discriminating test: stamps ONLY the top-level asset_class to
        CRYPTO on a real tree that ALSO carries asset_classes=['EQUITIES'] —
        proves the helper genuinely reads the real field (a fallback-only /
        always-EQUITIES stub would still return EQUITIES here) AND re-proves
        "string wins over array" against real tree shape, not a toy dict."""
        tree = copy.deepcopy(_load_tree(filename))
        assert tree.get("asset_classes") == ["EQUITIES"], (
            f"fixture assumption drifted: {filename} no longer carries "
            "asset_classes=['EQUITIES'] — re-verify before trusting this test"
        )
        tree["asset_class"] = "CRYPTO"
        assert fbld._resolve_draft_asset_class(tree) == "CRYPTO"

    @pytest.mark.parametrize("filename", _ABSENT_ASSET_CLASS_FIXTURES)
    def test_falls_back_to_equities_for_real_fixtures_lacking_the_field(self, fbld, filename):
        tree = _load_tree(filename)
        assert "asset_class" not in tree and "asset_classes" not in tree, (
            f"fixture assumption drifted: {filename} now carries an asset "
            "class field — re-verify before trusting this test"
        )
        assert fbld._resolve_draft_asset_class(tree) == "EQUITIES"

    def test_synthetic_fully_crypto_stamped_real_tree_propagates_crypto(self, fbld):
        """A genuinely CRYPTO-consistent tree (both fields agree) — no real
        crypto fixture exists yet, so this synthesizes one from a real
        EQUITIES tree via deepcopy (never mutates the on-disk fixture)."""
        tree = copy.deepcopy(_load_tree(_EQUITIES_CARRYING_FIXTURES[0]))
        tree["asset_class"] = "CRYPTO"
        tree["asset_classes"] = ["CRYPTO"]
        assert fbld._resolve_draft_asset_class(tree) == "CRYPTO"


# ---------------------------------------------------------------------------
# Mandatory wiring tests: approve_frontrunner_proposal -> save_symphony
# ---------------------------------------------------------------------------


def _make_proposal(
    *,
    proposal_id: int = 1,
    symphony_id: str = "test-symphony-hash",
    candidate_tree: object = None,
    approval_status: str = "pending",
    created_symphony_id: str | None = None,
) -> dict:
    return {
        "id": proposal_id,
        "created_at": "2026-08-25T00:00:00Z",
        "updated_at": "2026-08-25T00:00:00Z",
        "symphony_id": symphony_id,
        "proposal_source": "frontrunner_builder",
        "approval_status": approval_status,
        "candidate_tree": candidate_tree
        if candidate_tree is not None
        else {"step": "root", "children": []},
        "metrics_json": {"candidate_cagr": 0.1},
        "created_symphony_id": created_symphony_id,
        "error_message": None,
    }


def _draft_success(symphony_id: str = "new-symphony-123", version_id: str = "v1"):
    from advisors.composer_draft_client import DraftResult

    return DraftResult(success=True, symphony_id=symphony_id, version_id=version_id, error=None)


class TestApproveFrontrunnerProposalAssetClassWiring:
    def test_threads_crypto_asset_class_and_preserves_raw_value_identity(self, fbld):
        candidate_tree = copy.deepcopy(_load_tree(_EQUITIES_CARRYING_FIXTURES[0]))
        # asset_classes stays ["EQUITIES"] — deliberately disagreeing with
        # the stamped top-level string, re-proving "string wins" at the
        # ORCHESTRATION layer (not just inside the pure helper).
        candidate_tree["asset_class"] = "CRYPTO"
        proposal = _make_proposal(proposal_id=101, candidate_tree=candidate_tree)

        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony",
                return_value=_draft_success(),
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(101)

        assert result.success is True
        assert mock_save.called, "save_symphony was never called"
        _, kwargs = mock_save.call_args
        assert kwargs.get("asset_class") == "CRYPTO", (
            "expected the derived CRYPTO asset_class to reach save_symphony, "
            f"got {kwargs.get('asset_class')!r}"
        )
        assert kwargs.get("raw_value") is candidate_tree, (
            "raw_value must still be the SAME candidate_tree object, "
            "unchanged by the asset_class fix (AC-6 transport contract)"
        )

    def test_threads_equities_fallback_when_tree_lacks_asset_class(self, fbld):
        # default candidate_tree ({"step": "root", "children": []}) carries
        # neither asset_class nor asset_classes.
        proposal = _make_proposal(proposal_id=102)

        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony",
                return_value=_draft_success(),
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(102)

        assert result.success is True
        assert mock_save.called, "save_symphony was never called"
        _, kwargs = mock_save.call_args
        assert kwargs.get("asset_class") == "EQUITIES", (
            "expected the EQUITIES fallback to reach save_symphony, "
            f"got {kwargs.get('asset_class')!r}"
        )

    def test_never_raises_and_still_defaults_to_equities_for_a_malformed_candidate_tree(self, fbld):
        """AC-5 at the ORCHESTRATION layer, not just inside the pure helper
        in isolation — a corrupted DB row (candidate_tree stored as a bare
        string) must not crash approve_frontrunner_proposal. Per the plan's
        architecture the derivation is D-1 (never raises) and the call site
        proceeds normally to save_symphony with the safe EQUITIES fallback
        — it does not fail closed before the call."""
        proposal = _make_proposal(proposal_id=103, candidate_tree="not-a-dict-string")

        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony",
                return_value=_draft_success(),
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(103)  # must not raise

        assert result.success is True
        assert mock_save.called, "save_symphony was never called for a malformed candidate_tree"
        _, kwargs = mock_save.call_args
        assert kwargs.get("asset_class") == "EQUITIES", (
            "a malformed candidate_tree must still default asset_class to "
            f"EQUITIES, got {kwargs.get('asset_class')!r}"
        )
