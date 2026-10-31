"""The design tokens are exactly what they were: the app-shell work changes no token value.

Pinned by the sha256 of the file bytes with CRLF normalized to LF (the pattern of
``tests/test_mdd_window_truth_zero_diff_guard.py``), never a raw-byte hash (Windows checkouts
carry CRLF) and never a git-history guard (CI clones at depth one).

Recorded with: ``git show 37c8e983:static/tokens.css`` piped through a CRLF-to-LF replace and
``hashlib.sha256``; the file at the shell work's base hashes the same (no token has moved
since that ref). A failing pin means a token value changed: STOP and ask before re-pinning.
"""

from __future__ import annotations

import hashlib
import pathlib

TOKENS = pathlib.Path(__file__).resolve().parents[2] / "static" / "tokens.css"
TOKENS_SHA256_LF = "4bbd4a864ff493c4a7056a4c16c7fa791a4f7491728a726b3dc3373469505cc8"


def test_tokens_css_is_byte_identical_to_the_reference_after_line_ending_normalization():
    text = TOKENS.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == TOKENS_SHA256_LF, (
        "static/tokens.css changed. No token value may change in this work (token decisions are "
        "a separate design step); STOP and ask before re-pinning."
    )
