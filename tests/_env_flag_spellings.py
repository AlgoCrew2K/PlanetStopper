"""The spelling families of the boolean environment-flag parse.

Shared by every characterization of the ten flag sites. The oracle is the expression the
ten sites carried at the reference commit, copied verbatim: ``raw.lower() in ("true", "1", "yes")``.
A value is never stripped, so ``" true"`` and ``"true "`` are false.

A value reaches a site by one of three routes: a process environment variable (never ``None``),
a mapping read from the ``.env`` file (a key with no ``=`` holds ``None``), or no key at all.
``ABSENT`` names the third; ``BARE_KEY`` is the second's ``None``.
"""

from __future__ import annotations

TRUE_FAMILY = ("true", "True", "TRUE", "tRuE", "1", "yes", "Yes", "YES")
FALSE_FAMILY = ("false", "False", "0", "no", "", " true", "true ", "on", "y", "t", "2")
SPELLINGS = TRUE_FAMILY + FALSE_FAMILY

BARE_KEY = None


class _Absent:
    """The flag has no key at all."""

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT = _Absent()


def ref_is_true(raw: str) -> bool:
    """The reference expression of every site, verbatim."""
    return raw.lower() in ("true", "1", "yes")


def ref_flag(value, default: str) -> bool:
    """A site that reads ``mapping.get(key, default)`` and parses it (``ABSENT`` takes the default)."""
    raw = default if value is ABSENT else value
    return ref_is_true(raw)


def mapping_with(key: str, value) -> dict:
    """The ``.env`` mapping a site sees: no key for ``ABSENT``, ``None`` for ``BARE_KEY``."""
    return {} if value is ABSENT else {key: value}


def case_id(value) -> str:
    return "ABSENT" if value is ABSENT else repr(value)


ALL_CASES = SPELLINGS + (ABSENT,)
