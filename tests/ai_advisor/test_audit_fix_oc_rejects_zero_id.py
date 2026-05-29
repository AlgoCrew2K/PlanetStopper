"""
Sprint 3 audit-fix Cycle A — RED tests for S3-AUDIT-007 (MEDIUM):
  compute_overfitting_conscience_observation MUST raise when autotune_run["id"]
  is 0 or None.  Defense-in-depth: even after S3-AUDIT-001 is repaired, the
  construct-then-overwrite pattern at autotuner.py:1747-1757 has a silent
  fail-mode if get_latest_autotune_run ever returns None — the sentinel `0`
  survives into the producer and observations get persisted with
  subject_id="0".  The producer must fail-noisy.

Why this is its own assertion, separate from S3-AUDIT-001:
  S3-AUDIT-001 fixes the WRITER side (save_autotune_run returns lastrowid).
  S3-AUDIT-007 fixes the READER side (producer rejects id=0/None).
  Both must hold; a future regression of the writer would otherwise reopen
  the audit-trail corruption with no test catching it.

The producer module already imports KeyError on missing keys per its docstring
(overfitting_conscience.py:60).  The repair adds an explicit value check
("if id in (0, None): raise") at the top of compute_*.
"""

from __future__ import annotations

import importlib

import pytest


def _import_conscience():
    return importlib.import_module("advisors.overfitting_conscience")


def _valid_run_minus_id() -> dict:
    """A valid autotune_run dict EXCEPT for the id field — caller fills id in."""
    return {
        "symphony_id": "DefensiveAlpha",
        "run_timestamp": "2026-05-27T00:00:00Z",
        "spec_bundle_id": "bundle-hash-abc",
        "n_effective": 100,
        "s_count": 0,
    }


# ---------------------------------------------------------------------------
# S3-AUDIT-007: compute_* raises on id=0 (sentinel never reaches the audit row).
# ---------------------------------------------------------------------------


def test_compute_overfitting_raises_on_id_zero():
    """id=0 is the silent-fallback sentinel — the producer must refuse it.

    Today the audit at S3-AUDIT-001 shows _oc_run["id"]=0 reaches the producer
    and propagates into subject_id='0'.  S3-AUDIT-007 fixes the producer side:
    compute_overfitting_conscience_observation must raise (ValueError or
    similar) when id is the sentinel.
    """
    mod = _import_conscience()
    run = _valid_run_minus_id()
    run["id"] = 0  # the sentinel value documented at autotuner.py:1748

    with pytest.raises(Exception) as excinfo:
        mod.compute_overfitting_conscience_observation(run, ledger_rows=[])

    # The exception text must reference 'id' so a future contributor reading
    # a traceback knows exactly which key was invalid.
    msg = str(excinfo.value)
    assert "id" in msg.lower(), (
        f"Exception message must mention 'id' so the operator can diagnose: got {msg!r}"
    )


def test_compute_overfitting_raises_on_id_none():
    """id=None must also be refused — the second silent-fail mode.

    .get('id', 0) returns 0 when the dict has no 'id' key; but an explicit
    None passed in (e.g. from a future broken caller) is just as corrupting.
    """
    mod = _import_conscience()
    run = _valid_run_minus_id()
    run["id"] = None

    with pytest.raises(Exception) as excinfo:
        mod.compute_overfitting_conscience_observation(run, ledger_rows=[])

    msg = str(excinfo.value)
    assert "id" in msg.lower(), (
        f"Exception message must mention 'id'; got {msg!r}"
    )


def test_compute_overfitting_accepts_positive_id():
    """A legitimate positive int id must NOT raise — the guard is sentinel-only.

    Belt-and-braces: the new guard must not over-fire and block real ids.
    """
    mod = _import_conscience()
    run = _valid_run_minus_id()
    run["id"] = 42  # a normal autotune_runs.id

    # Must not raise.
    obs = mod.compute_overfitting_conscience_observation(run, ledger_rows=[])
    assert obs["subject_id"] == "42", (
        f"subject_id must reflect the supplied positive id; got {obs['subject_id']!r}."
    )


def test_compute_overfitting_does_not_accept_negative_id():
    """Negative ids would never occur from AUTOINCREMENT — also reject them.

    Defense-in-depth: SQLite rowids are positive integers.  A negative id
    arriving here would indicate a fabricated dict — refuse.
    """
    mod = _import_conscience()
    run = _valid_run_minus_id()
    run["id"] = -1

    with pytest.raises(Exception):
        mod.compute_overfitting_conscience_observation(run, ledger_rows=[])
