"""
RED golden-fixture tests for symphony-ID drift matching in _persist_composer_fields_to_bot_state.

Background:
  Composer's API returns symphony IDs that can drift from the IDs stored in bot_state
  (e.g. Composer returns '5XjzXjdGnjh99MIsdM97' but bot_state key is
  '5XjzXjdGnjh99M7CRUZB1DvNs' — same symphony, suffix diverged). The current
  implementation does an exact equality check (`bot_state[symphony_id]`) which silently
  fails for drifted IDs: the persist is a no-op, and the four Composer fields
  (simple_return, net_deposits, time_weighted_return, max_drawdown) go stale.

Intended fix:
  When exact ID match fails, resolve the target bot_state key via:
    1. 12-char prefix match  (first 12 chars of Composer ID == first 12 chars of stored key)
    2. Name match            (sym["name"] == bot_state[key]["name"])
  If prefix match would be ambiguous (two stored IDs share a 12-char prefix), the resolver
  must NOT silently write to the wrong entry — it must either disambiguate via name or skip.

Golden fixture:
  tests/fixtures/composer/reconcile-2026-05-20/symphony_stats_meta.json
  Captured from the live Composer API on 2026-05-20. The IDs here are the *Composer-side*
  IDs — bot_state keys may differ by suffix (the drift scenario).

Test structure:
  TIER 0 — Unit tests for the resolver helper directly.
  TIER 1 — Integration tests for _persist_composer_fields_to_bot_state end-to-end.
  TIER 2 — Adversarial safety: partial IDs, garbage input, ambiguous prefixes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import alpha_bot_execution

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_RECONCILE_FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "composer"
    / "reconcile-2026-05-20"
    / "symphony_stats_meta.json"
)
_FOUR_FIELDS = ("simple_return", "net_deposits", "time_weighted_return", "max_drawdown")

_PREFIX_LEN = 12  # the match window for prefix fallback


def _load_reconcile_symphonies() -> list[dict]:
    with open(_RECONCILE_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["symphonies"]


_RECONCILE_SYMS = _load_reconcile_symphonies()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ID_DRIFT_SUFFIX = "CRUZB1DvNs"  # appended to stored key to simulate drift


def _drift_id(real_id: str) -> str:
    """Return the stored bot_state key that corresponds to a Composer ID with drifted suffix.

    We simulate drift by appending a suffix to the stored key while keeping the first
    12 chars identical — so Composer still returns the shorter ID, and the prefix matcher
    must find it.
    """
    return real_id[:_PREFIX_LEN] + _ID_DRIFT_SUFFIX


def _minimal_entry(name: str = "Test Symphony") -> dict:
    return {
        "high_water_mark": 0.0,
        "shadow_hwm": 0.0,
        "prev_return": None,
        "armed": False,
        "triggered": False,
        "current_return": 0.0,
        "name": name,
    }


def _four_fields_from(sym: dict) -> dict:
    return {f: sym.get(f) for f in _FOUR_FIELDS}


# ---------------------------------------------------------------------------
# TIER 0 — Unit tests: _resolve_bot_state_key resolver (the new helper)
#
# The fix must expose (or use internally) a function that resolves a Composer
# symphony_id to the correct bot_state key. We test it directly if it exists
# at module level; if not, these tests RED-pin the contract so eng writes it.
# ---------------------------------------------------------------------------


class TestResolveBotStateKey:
    """Tests for the resolver that maps a Composer symphony_id to the stored bot_state key."""

    def _call_resolver(self, bot_state: dict, composer_id: str, name: str) -> str | None:
        """Invoke _resolve_bot_state_key if it exists; else call _persist and check side-effect."""
        resolver = getattr(alpha_bot_execution, "_resolve_bot_state_key", None)
        if resolver is None:
            pytest.fail(
                "_resolve_bot_state_key not found on alpha_bot_execution. "
                "eng must implement this resolver for the ID-drift fix."
            )
        return resolver(bot_state, composer_id, name)

    def test_exact_match_returns_composer_id(self):
        """Exact hit: Composer ID == stored key → resolver returns that key unchanged."""
        sym = _RECONCILE_SYMS[0]
        stored_id = sym["id"]
        bot_state = {stored_id: _minimal_entry(sym.get("name", ""))}

        result = self._call_resolver(bot_state, stored_id, sym.get("name", ""))

        assert result == stored_id, (
            f"exact match: resolver must return the stored key {stored_id!r}; got {result!r}"
        )

    def test_prefix_match_returns_stored_key_when_exact_fails(self):
        """Drifted suffix: Composer ID shares 12-char prefix with stored key → resolver finds it."""
        sym = _RECONCILE_SYMS[8]  # '5XjzXjdGnjh99MIsdM97' — the reported drift symphony
        composer_id = sym["id"]
        stored_key = _drift_id(composer_id)  # '5XjzXjdGnjh99' + CRUZB1DvNs
        bot_state = {stored_key: _minimal_entry(sym.get("name", ""))}

        result = self._call_resolver(bot_state, composer_id, sym.get("name", ""))

        assert result == stored_key, (
            f"prefix match: Composer ID {composer_id!r} should resolve to stored key "
            f"{stored_key!r} via 12-char prefix; got {result!r}. "
            "Both share prefix {composer_id[:_PREFIX_LEN]!r}."
        )

    def test_name_match_returns_stored_key_when_prefix_fails(self):
        """Name match fallback: ID prefix differs but symphony name is identical → resolver finds it."""
        sym = _RECONCILE_SYMS[1]
        name = sym.get("name", "Fallback Symphony")
        # Store under a completely different ID (no shared prefix)
        stored_key = "TOTALLY_DIFFERENT_ID_XYZ"
        bot_state = {stored_key: _minimal_entry(name)}

        result = self._call_resolver(bot_state, sym["id"], name)

        assert result == stored_key, (
            f"name match: when prefix fails, resolver must find stored key by name. "
            f"sym name={name!r}, expected {stored_key!r}, got {result!r}."
        )

    def test_no_match_returns_none(self):
        """No exact, prefix, or name match → resolver returns None (safe no-op)."""
        bot_state = {"SOME_UNRELATED_ID_123": _minimal_entry("Unrelated Symphony")}

        result = self._call_resolver(bot_state, "TOTALLY_UNKNOWN_ID_XYZ", "Unknown Symphony")

        assert result is None, (
            f"no match: resolver must return None (not raise, not write garbage); got {result!r}"
        )

    def test_ambiguous_prefix_does_not_silently_return_wrong_key(self):
        """Two stored keys share 12-char prefix: resolver must NOT silently pick one by position."""
        base_prefix = "5XjzXjdGnjh9"  # first 12 chars
        stored_key_a = base_prefix + "AAAAAAAAAA"
        stored_key_b = base_prefix + "BBBBBBBBBB"
        composer_id = base_prefix + "CCCCCCCCCC"  # matches neither name
        bot_state = {
            stored_key_a: _minimal_entry("Symphony Alpha"),
            stored_key_b: _minimal_entry("Symphony Beta"),
        }
        # Neither name matches the composer's name — resolver must not guess.
        result = self._call_resolver(bot_state, composer_id, "Unknown Symphony Name")

        # Acceptable: None (skip) or raises a logged warning and returns None.
        # Not acceptable: silently returns stored_key_a or stored_key_b.
        assert result not in (stored_key_a, stored_key_b), (
            f"ambiguous prefix: resolver must NOT silently return one of the ambiguous keys "
            f"({stored_key_a!r}, {stored_key_b!r}) when the name does not disambiguate. "
            f"got {result!r} — this would corrupt the wrong symphony's fields."
        )

    def test_ambiguous_prefix_resolved_by_name(self):
        """Two stored keys share prefix but names differ — name match disambiguates correctly."""
        base_prefix = "5XjzXjdGnjh9"
        stored_key_a = base_prefix + "AAAAAAAAAA"
        stored_key_b = base_prefix + "BBBBBBBBBB"
        composer_id = base_prefix + "CCCCCCCCCC"
        target_name = "Symphony Beta — The Correct One"
        bot_state = {
            stored_key_a: _minimal_entry("Symphony Alpha — The Wrong One"),
            stored_key_b: _minimal_entry(target_name),
        }

        result = self._call_resolver(bot_state, composer_id, target_name)

        assert result == stored_key_b, (
            f"ambiguous prefix disambiguated by name: resolver must return {stored_key_b!r} "
            f"(name={target_name!r} matches); got {result!r}."
        )

    def test_empty_bot_state_returns_none(self):
        """Empty bot_state — resolver must return None without raising."""
        result = self._call_resolver({}, "SOME_COMPOSER_ID_XYZ", "Any Symphony Name")

        assert result is None, (
            f"empty bot_state: resolver must return None; got {result!r}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Integration: _persist_composer_fields_to_bot_state with drifted IDs
# ---------------------------------------------------------------------------


class TestPersistComposerFieldsIdDrift:
    """End-to-end tests for _persist_composer_fields_to_bot_state with the reconcile fixture."""

    def test_exact_id_match_persists_all_four_fields(self):
        """Regression guard: exact match still persists all four fields (don't break happy path)."""
        sym = _RECONCILE_SYMS[0]
        stored_id = sym["id"]
        bot_state = {stored_id: _minimal_entry(sym.get("name", ""))}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, stored_id, sym)

        for field in _FOUR_FIELDS:
            assert field in bot_state[stored_id], (
                f"exact match: field {field!r} must be persisted to bot_state[{stored_id!r}]; "
                f"not found. Regression: don't break the exact-match path."
            )
            assert bot_state[stored_id][field] == sym.get(field), (
                f"exact match: bot_state[{stored_id!r}][{field!r}] must equal fixture value "
                f"{sym.get(field)!r}; got {bot_state[stored_id][field]!r}."
            )

    def test_prefix_match_persists_fields_to_correct_entry(self):
        """Drifted suffix: Composer ID prefix matches stored key → fields land on the right entry."""
        sym = _RECONCILE_SYMS[8]  # '5XjzXjdGnjh99MIsdM97'
        composer_id = sym["id"]
        stored_key = _drift_id(composer_id)
        bot_state = {stored_key: _minimal_entry(sym.get("name", ""))}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, composer_id, sym)

        assert stored_key in bot_state, "stored key must still exist after persist"
        for field in _FOUR_FIELDS:
            assert field in bot_state[stored_key], (
                f"prefix match: field {field!r} must be written to stored key {stored_key!r} "
                f"(Composer ID {composer_id!r} matched via 12-char prefix). "
                f"Currently a silent no-op — fix: implement prefix fallback."
            )
            assert bot_state[stored_key][field] == pytest.approx(
                sym.get(field), abs=1e-9
            ) if isinstance(sym.get(field), float) else bot_state[stored_key][field] == sym.get(field), (
                f"prefix match: {field!r} value mismatch. "
                f"expected {sym.get(field)!r}, got {bot_state[stored_key][field]!r}."
            )

    def test_prefix_match_does_not_create_new_key_for_drifted_id(self):
        """Drifted ID must NOT appear as a new key in bot_state — write goes to stored key only."""
        sym = _RECONCILE_SYMS[8]
        composer_id = sym["id"]
        stored_key = _drift_id(composer_id)
        bot_state = {stored_key: _minimal_entry(sym.get("name", ""))}
        original_keys = set(bot_state.keys())

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, composer_id, sym)

        assert set(bot_state.keys()) == original_keys, (
            f"prefix match: bot_state keys must not change after persist. "
            f"A new key {(set(bot_state.keys()) - original_keys)!r} was created — "
            "the drifted Composer ID must NOT become a new entry."
        )

    def test_name_match_persists_fields_when_prefix_also_fails(self):
        """Name-only match: completely different ID, same name → fields land on the right entry."""
        sym = _RECONCILE_SYMS[2]
        name = sym.get("name", "Fallback Name Symphony")
        stored_key = "UNRELATED_KEY_NO_PREFIX_MATCH_XYZ"
        bot_state = {stored_key: _minimal_entry(name)}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, sym["id"], sym)

        for field in _FOUR_FIELDS:
            assert field in bot_state[stored_key], (
                f"name match: field {field!r} must be persisted to {stored_key!r} "
                f"(matched by name={name!r}). Currently a silent no-op."
            )

    def test_no_match_is_safe_no_op(self):
        """Nothing matches → bot_state is unchanged, no crash, no corruption."""
        unrelated_key = "UNRELATED_SYM_KEY_ABC123"
        bot_state = {unrelated_key: _minimal_entry("Unrelated Symphony")}
        original_state = copy.deepcopy(bot_state)
        sym = _RECONCILE_SYMS[0]
        # Use an ID and name that share nothing with unrelated_key
        garbage_id = "XXXXXXXXXXXXXXXXXXXXXX"
        garbage_sym = dict(sym, id=garbage_id, name="No Match Symphony At All")

        alpha_bot_execution._persist_composer_fields_to_bot_state(
            bot_state, garbage_id, garbage_sym
        )

        assert bot_state == original_state, (
            f"no-match: bot_state must be unchanged when no key resolves. "
            f"Before: {original_state!r}, after: {bot_state!r}."
        )

    def test_full_reconcile_fixture_all_symphonies_persist_via_prefix(self):
        """All 11 reconcile-fixture symphonies persist when bot_state keys use drifted suffixes.

        Simulates the real deployment scenario: bot_state was seeded with IDs that have
        drifted suffixes; Composer returns the current shorter IDs. All 11 must persist.
        """
        # Build bot_state with drifted stored keys for all 11 symphonies
        bot_state: dict = {}
        composer_id_to_stored_key: dict[str, str] = {}
        for sym in _RECONCILE_SYMS:
            composer_id = sym["id"]
            stored_key = _drift_id(composer_id)
            bot_state[stored_key] = _minimal_entry(sym.get("name", ""))
            composer_id_to_stored_key[composer_id] = stored_key

        # Call persist for each symphony as the engine would
        for sym in _RECONCILE_SYMS:
            alpha_bot_execution._persist_composer_fields_to_bot_state(
                bot_state, sym["id"], sym
            )

        # Every stored key must now have all four fields
        for sym in _RECONCILE_SYMS:
            stored_key = composer_id_to_stored_key[sym["id"]]
            for field in _FOUR_FIELDS:
                assert field in bot_state[stored_key], (
                    f"reconcile fixture: {field!r} not persisted to stored key {stored_key!r} "
                    f"(Composer ID {sym['id']!r}) — prefix fallback did not fire."
                )


# ---------------------------------------------------------------------------
# TIER 2 — Adversarial: garbage input, partial IDs, cross-symphony contamination
# ---------------------------------------------------------------------------


class TestAdversarialIdMatching:
    """Adversarial tests: corrupt input must never write to an unrelated symphony."""

    def test_partial_garbage_id_never_contaminates_unrelated_symphony(self):
        """A Composer ID that is a prefix of an unrelated symphony ID must NOT corrupt it."""
        # Stored key is 'ABCDEFGHIJKLMNOPQRSTUVWX' — longer than 12 chars
        legitimate_key = "ABCDEFGHIJKLMNOPQRSTUVWX"
        garbage_composer_id = "ABCDEFGHIJKL"  # first 12 chars — a prefix match!
        # BUT the name must NOT match — this is a different symphony
        bot_state = {legitimate_key: _minimal_entry("Legitimate Symphony ABCD")}
        original_state = copy.deepcopy(bot_state)

        corrupt_sym = dict(_RECONCILE_SYMS[0], id=garbage_composer_id, name="Garbage Symphony Name")
        alpha_bot_execution._persist_composer_fields_to_bot_state(
            bot_state, garbage_composer_id, corrupt_sym
        )

        # Because the name does NOT match, the prefix-only match must NOT write here.
        # (The 12-char prefix match is only safe when combined with name disambiguation
        # OR when it is the only stored key with that prefix AND the prefix is long enough.)
        # This test asserts that a shorter-than-stored-key garbage ID doesn't corrupt
        # the longer legitimate key when the name is different.
        for field in _FOUR_FIELDS:
            assert field not in bot_state[legitimate_key], (
                f"adversarial: garbage Composer ID {garbage_composer_id!r} is a prefix of "
                f"legitimate key {legitimate_key!r} but names differ — "
                f"field {field!r} must NOT be written to the unrelated entry. "
                "A name-mismatch must veto a prefix match when the stored key is longer."
            )

    def test_empty_string_id_does_not_match_any_key(self):
        """Empty string Composer ID must produce a safe no-op."""
        stored_key = "SOME_REAL_KEY_123456"
        bot_state = {stored_key: _minimal_entry("Real Symphony")}
        original_state = copy.deepcopy(bot_state)

        sym = dict(_RECONCILE_SYMS[0], id="", name="Real Symphony")
        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, "", sym)

        assert bot_state == original_state, (
            "empty Composer ID must not write anything to bot_state."
        )

    def test_none_values_in_sym_do_not_crash_persist(self):
        """Composer returns None for some fields — persist must not raise."""
        sym = _RECONCILE_SYMS[3]
        stored_id = sym["id"]
        bot_state = {stored_id: _minimal_entry(sym.get("name", ""))}
        sym_with_nones = dict(sym, simple_return=None, net_deposits=None)

        try:
            alpha_bot_execution._persist_composer_fields_to_bot_state(
                bot_state, stored_id, sym_with_nones
            )
        except Exception as exc:
            pytest.fail(
                f"_persist_composer_fields_to_bot_state must not raise when sym has None fields; "
                f"got {type(exc).__name__}: {exc}"
            )

    def test_multiple_symphonies_each_gets_correct_fields(self):
        """When multiple drifted IDs are persisted in sequence, each gets its own fields."""
        # Use three reconcile symphonies with distinct fields
        sym_a = _RECONCILE_SYMS[1]
        sym_b = _RECONCILE_SYMS[5]
        sym_c = _RECONCILE_SYMS[9]
        key_a = _drift_id(sym_a["id"])
        key_b = _drift_id(sym_b["id"])
        key_c = _drift_id(sym_c["id"])
        bot_state = {
            key_a: _minimal_entry(sym_a.get("name", "")),
            key_b: _minimal_entry(sym_b.get("name", "")),
            key_c: _minimal_entry(sym_c.get("name", "")),
        }

        for sym in (sym_a, sym_b, sym_c):
            alpha_bot_execution._persist_composer_fields_to_bot_state(
                bot_state, sym["id"], sym
            )

        # Each stored key must have the fields from ITS OWN symphony, not any other
        for sym, key in ((sym_a, key_a), (sym_b, key_b), (sym_c, key_c)):
            for field in _FOUR_FIELDS:
                assert field in bot_state[key], (
                    f"multi-persist: {field!r} not found in bot_state[{key!r}] "
                    f"for symphony {sym['id']!r}."
                )
                expected = sym.get(field)
                actual = bot_state[key][field]
                if isinstance(expected, float) and isinstance(actual, float):
                    assert actual == pytest.approx(expected, abs=1e-9), (
                        f"multi-persist: bot_state[{key!r}][{field!r}] = {actual!r}, "
                        f"expected {expected!r} — wrong symphony's value was written."
                    )
                else:
                    assert actual == expected, (
                        f"multi-persist: bot_state[{key!r}][{field!r}] = {actual!r}, "
                        f"expected {expected!r}."
                    )

    def test_prefix_match_does_not_fire_for_ids_shorter_than_prefix_len(self):
        """A Composer ID shorter than PREFIX_LEN chars must not prefix-match any stored key."""
        short_id = "SHORT"  # only 5 chars, less than PREFIX_LEN=12
        stored_key = "SHORT_PADDED_STORED_KEY"  # starts with 'SHORT' but longer
        bot_state = {stored_key: _minimal_entry("Short ID Symphony")}
        original_state = copy.deepcopy(bot_state)

        sym = dict(_RECONCILE_SYMS[0], id=short_id, name="Short ID Symphony")
        # Name matches — name-match may fire here, which is acceptable.
        # But prefix-match on a too-short ID must not silently corrupt.
        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, short_id, sym)

        # Either name-match fires (fields written — acceptable) OR nothing fires.
        # What must NOT happen: a partial prefix of len < PREFIX_LEN writing to a longer stored key.
        # We verify the stored key didn't gain fields from a different symphony via a wrong prefix.
        if "simple_return" in bot_state[stored_key]:
            # Name match may have fired — that's fine. Verify the correct values landed.
            assert bot_state[stored_key]["simple_return"] == sym.get("simple_return"), (
                f"if name-match fires on short ID, the values must be from the correct sym."
            )
        # If no fields written, that's also fine — no assertion needed.

    def test_persist_does_not_write_to_wrong_symphony_when_name_is_empty(self):
        """Empty name in both sym and stored entry must not cause a false name match."""
        stored_key_empty_name = "SYM_KEY_EMPTY_NAME_XYZ"
        unrelated_stored_key = "SYM_KEY_DIFFERENT_ID_ABC"
        # Both have empty names — but different IDs, no shared prefix.
        bot_state = {
            stored_key_empty_name: _minimal_entry(""),
            unrelated_stored_key: _minimal_entry(""),
        }
        original_state = copy.deepcopy(bot_state)

        composer_id = "ENTIRELY_DIFFERENT_XXXXXXXXX"
        sym = dict(_RECONCILE_SYMS[0], id=composer_id, name="")

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, composer_id, sym)

        # Empty name must NOT create a spurious name-match across multiple entries.
        # bot_state must be unchanged OR only the one correct entry was updated —
        # but since there's no correct entry here, must be unchanged.
        for key in (stored_key_empty_name, unrelated_stored_key):
            for field in _FOUR_FIELDS:
                assert field not in bot_state[key], (
                    f"empty-name guard: {field!r} must not be written to {key!r} via "
                    f"a false name-match on empty string. "
                    f"Empty name must not be treated as a valid match discriminator."
                )
