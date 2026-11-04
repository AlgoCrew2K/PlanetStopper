"""Boolean environment-flag parsing shared by the engine, the dashboard and the sleeve tick.

Stdlib only, so every one of them can import it without a cycle. The spellings are exact:
no whitespace is stripped, and a missing value (None) raises AttributeError, so each caller
keeps its own default and its own None guard.
"""

TRUE_SPELLINGS = ("true", "1", "yes")


def is_true(raw: str) -> bool:
    return raw.lower() in TRUE_SPELLINGS
