"""
RED-phase pinning tests for Concern #28 — broad `except Exception` catches
in `alpha_bot_execution.py`.

CONTEXT
-------
At authoring time `alpha_bot_execution.py` contains two `except Exception`
clauses:

  L248  fetch_intraday_vwaps -- `except Exception as e:`
                                wraps the Alpaca /stocks/bars network
                                call + JSON parse + pandas DataFrame
                                construction. Swallows the error with a
                                `print` and continues to the next batch,
                                returning a partial / empty `vwap_data`
                                dict to the caller.

  L259  get_current_et       -- `except Exception:`
                                wraps `from zoneinfo import ZoneInfo`
                                plus the subsequent `datetime.now(...)`.
                                The intent is clearly ImportError fallback
                                for Python < 3.9; the bare `Exception`
                                also swallows any error in the
                                `datetime.now(ZoneInfo("America/New_York"))`
                                call (typo'd zone name, OS clock failure)
                                and silently routes to the manual DST
                                hack — masking real bugs.

Both are silent-failure paths. Concern #28 specifically calls out the
first (L248) because the recently-merged `feed=iex` fix (ce043fd) closed
the 403 silent-fail symptom on the Alpaca side, but the broad catch in
the same function still masks any future Alpaca/JSON/pandas regression.

REQUIRED FIX
------------
- L248 must catch a specific union: `requests.RequestException` for
  network/timeout, `(ValueError, KeyError)` for JSON-shape / missing-key
  problems, and re-raise everything else. Or — if the fix-author judges
  the entire batch loop should be skip-and-continue regardless of cause
  — they must add an explicit comment justifying the breadth AND log
  the exception type, not just the message.
- L259 must narrow to `(ImportError, ModuleNotFoundError)` for the
  import-time fallback. Any failure in the datetime.now() construction
  should propagate — silently sliding to manual DST math on a real bug
  is exactly the silent-failure pattern Concern #28 forbids.

WHITELIST POLICY (per task statement)
-------------------------------------
The task statement allowed conservative whitelisting if a broad
`except Exception` IMMEDIATELY re-raises or explicitly logs-and-
re-raises. Audit at authoring time:

  L248 -- print + continue. NO re-raise. NOT whitelisted.
  L259 -- silent fallback to manual DST math. NO re-raise. NOT whitelisted.

No whitelist exemptions are encoded in this test. If a legitimate
top-level "log everything and re-raise" exception barrier is added
later (e.g., wrapping the main() entry point), the implementer should
explicitly add a line-number entry to `WHITELISTED_LINENOS` below with
a code-comment justification — never silently widen the regex.

DESIGN DECISION — AST WALK, NOT REGEX
-------------------------------------
A regex on the literal string "except Exception" is fragile against
multi-line except clauses (`except (\n    Exception,\n    OtherErr\n)`),
against backslash line-continuations, and against the type-tuple form
`except (Exception, KeyError)`. Walking the AST gives a structurally
correct answer: we look for `ExceptHandler` nodes whose `type` is
`Name(id='Exception')` directly, OR a `Tuple` containing such a Name.

We still do NOT import `alpha_bot_execution` — we `ast.parse` the source
text. That keeps the test purely static and avoids the daemon-spinup
side effects of module import.

EXPECTED STATE AT AUTHORING
---------------------------
RED. Two `ExceptHandler` nodes with `type=Name(id='Exception')` exist
in the file (L248, L259); both will fail the negative assertion and
both contribute to the count-equals-zero assertion.
"""

from __future__ import annotations

import ast
import pathlib


# ---------------------------------------------------------------------------
# Source-file path. Resolved once at import time.
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALPHA_BOT_PATH = REPO_ROOT / "alpha_bot_execution.py"


# ---------------------------------------------------------------------------
# Whitelist policy
# ---------------------------------------------------------------------------
# Set of `lineno` values for `ExceptHandler` nodes that are explicitly
# allowed to be broad. Empty at authoring time. Future additions must
# come with an inline code-comment justifying WHY the broad catch is
# safe (immediate re-raise, top-level barrier with full traceback log,
# etc.). NEVER widen this set as a quick-fix to make the test pass;
# the fix is to narrow the except clause in production code instead.
WHITELISTED_LINENOS: frozenset[int] = frozenset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_source(path: pathlib.Path) -> str:
    """Read a source file as UTF-8 text. Fails loudly if missing.

    Static-analysis convention shared with the rest of the test suite.
    Do NOT import the module under test — module-level config /
    scheduler side effects are inappropriate for this layer of test.
    """
    assert path.exists(), f"Required source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _is_bare_exception_handler(handler: ast.ExceptHandler) -> bool:
    """Return True iff the handler matches the broad-catch pattern.

    Matches:
      except Exception:            -- type=Name(id='Exception')
      except Exception as e:       -- type=Name(id='Exception'), name='e'
      except (Exception, X):       -- type=Tuple containing Name(id='Exception')
      except (X, Exception, Y):    -- ditto, anywhere in the tuple

    Does NOT match:
      except BaseException:        -- different name, also forbidden in
                                      principle but not what Concern #28
                                      describes; if it appears, a
                                      separate test should pin it.
      except:                      -- bare except, type is None; flagged
                                      separately below as it's an even
                                      worse anti-pattern.
      except SpecificError:        -- narrow, allowed.

    Note on bare `except:` — `handler.type is None` is a different code
    smell with an even broader catch (catches SystemExit and
    KeyboardInterrupt too). The task statement focuses on `except
    Exception`, so this helper sticks to that scope; a sibling test
    below pins the bare-except case separately.
    """
    exc_type = handler.type
    if isinstance(exc_type, ast.Name) and exc_type.id == "Exception":
        return True
    if isinstance(exc_type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "Exception"
            for elt in exc_type.elts
        )
    return False


def _find_broad_handlers(source: str) -> list[ast.ExceptHandler]:
    """Walk the AST and return every ExceptHandler that matches the
    broad-catch pattern AND is not in the whitelist.

    A non-empty whitelist would be applied here. With WHITELISTED_LINENOS
    currently empty, every match is returned.
    """
    tree = ast.parse(source)
    broad: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_bare_exception_handler(node):
            continue
        if node.lineno in WHITELISTED_LINENOS:
            continue
        broad.append(node)
    return broad


def _find_bare_excepts(source: str) -> list[ast.ExceptHandler]:
    """Return every `except:` (type is None) handler in the source.

    Even broader than `except Exception:` — catches SystemExit and
    KeyboardInterrupt. Treated as a separate, always-forbidden category.
    """
    tree = ast.parse(source)
    bare: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare.append(node)
    return bare


def _enclosing_function_name(tree: ast.Module, target: ast.ExceptHandler) -> str:
    """Return the name of the FunctionDef enclosing `target`, or
    '<module>' if the handler is at module top level. Used purely for
    a clearer diagnostic message; not part of the assertion logic.
    """
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(func):
            if sub is target:
                return func.name
    return "<module>"


# ---------------------------------------------------------------------------
# Test 1: no broad `except Exception` clauses
# ---------------------------------------------------------------------------


def test_no_broad_exception_handler_in_alpha_bot_execution():
    """Every `except` clause in `alpha_bot_execution.py` must name a
    specific exception type (or a tuple of specific types).

    Broad `except Exception` is a silent-failure path on a live-trading
    daemon. It masks regressions (KeyError on a renamed JSON field, an
    AttributeError from a stale pandas version, an OS clock failure)
    behind a single `print` line that operators may not notice.
    Concern #28 specifically calls out fetch_intraday_vwaps where the
    feed=iex fix closed the silent-403 symptom but left the broad
    `except` masking everything else.
    """
    source = _read_source(ALPHA_BOT_PATH)
    tree = ast.parse(source)
    offenders = _find_broad_handlers(source)

    if not offenders:
        return  # GREEN

    formatted = "\n  ".join(
        f"L{handler.lineno} in {_enclosing_function_name(tree, handler)}: "
        f"except Exception{(' as ' + handler.name) if handler.name else ''}:"
        for handler in offenders
    )
    raise AssertionError(
        "Broad `except Exception` clauses found in alpha_bot_execution.py. "
        "Each one is a silent-failure path that can mask real regressions "
        "(stale JSON shape, dependency version drift, OS-level errors). "
        "Narrow to the specific exception types you actually want to "
        "swallow; let the rest propagate.\n  " + formatted
    )


# ---------------------------------------------------------------------------
# Test 2: broad-catch count is exactly zero
# ---------------------------------------------------------------------------


def test_broad_exception_handler_count_is_zero():
    """Numeric guardrail asserting the exact count of broad
    `except Exception` clauses is zero.

    Paired with Test 1: gives a single-line "found N, expected 0"
    diagnostic on regression. Counts ONLY non-whitelisted handlers, so
    a future legitimate broad-catch-with-reraise entry in
    WHITELISTED_LINENOS does not break this assertion.
    """
    source = _read_source(ALPHA_BOT_PATH)
    offenders = _find_broad_handlers(source)
    assert len(offenders) == 0, (
        f"Expected zero non-whitelisted broad `except Exception` "
        f"handlers in alpha_bot_execution.py; found {len(offenders)} at "
        f"lines {[h.lineno for h in offenders]}. Narrow each one to the "
        "specific exception types actually intended to be swallowed."
    )


# ---------------------------------------------------------------------------
# Test 3: anti-drift — no bare `except:` clauses either
# ---------------------------------------------------------------------------


def test_no_bare_except_in_alpha_bot_execution():
    """Belt-and-braces companion to the `except Exception` ban: bare
    `except:` (with no type) is even broader — it also catches
    SystemExit and KeyboardInterrupt, which means Ctrl-C and process
    teardown signals can be swallowed.

    A future contributor narrowing `except Exception` might be tempted
    to "fix" it by dropping the type altogether (`except: pass`).
    This test forecloses that move.

    EXPECTED STATE AT AUTHORING: RED. While auditing for Concern #28
    the AST walk surfaced a bare `except:` on the
    `EXECUTION_START_TIME.split(":")` parse fallback. That bare except
    is a third instance of the same anti-pattern Concern #28 names —
    swallows ValueError (the only error the int(...) call can raise),
    but also swallows AttributeError if EXECUTION_START_TIME is None,
    KeyboardInterrupt during the parse, and any future regression. The
    fix is `except (ValueError, AttributeError):` plus a deliberate
    decision on whether None is allowed. Treated as part of the
    Concern #28 RED set; not separately whitelisted.
    """
    source = _read_source(ALPHA_BOT_PATH)
    tree = ast.parse(source)
    offenders = _find_bare_excepts(source)

    if not offenders:
        return

    formatted = "\n  ".join(
        f"L{handler.lineno} in {_enclosing_function_name(tree, handler)}: except:"
        for handler in offenders
    )
    raise AssertionError(
        "Bare `except:` clause(s) found in alpha_bot_execution.py. Bare "
        "except catches SystemExit and KeyboardInterrupt too — operators "
        "lose the ability to Ctrl-C the daemon cleanly. Always name the "
        "exception types you intend to handle.\n  " + formatted
    )


# ---------------------------------------------------------------------------
# Test 4: positive guardrail — the file DOES contain narrow except clauses
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_contains_at_least_one_specific_except_handler():
    """Positive guardrail: the file MUST contain at least one
    narrow exception handler (e.g., `except requests.RequestException`).
    This catches the failure mode where someone "fixes" the broad
    catches by deleting all try/except blocks entirely — also
    unacceptable in a network-heavy live daemon.

    Asserts existence of at least one ExceptHandler whose type is
    something OTHER than bare `Exception` or `BaseException`. Pre-fix,
    this test passes (the file has several `except requests.
    RequestException` blocks already); it is included anti-drift.
    """
    source = _read_source(ALPHA_BOT_PATH)
    tree = ast.parse(source)
    specific_handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue  # bare except, doesn't count
        if isinstance(node.type, ast.Name) and node.type.id in {
            "Exception",
            "BaseException",
        }:
            continue
        specific_handlers.append(node)

    assert specific_handlers, (
        "Expected at least one narrow `except` clause (e.g., "
        "`except requests.RequestException`) in alpha_bot_execution.py. "
        "Zero narrow handlers means either every try/except was deleted "
        "(unacceptable in a network-heavy daemon) or the entire error-"
        "handling layer was widened — investigate before merging."
    )


# ---------------------------------------------------------------------------
# Sanity guard — confirm the AST helpers behave as expected
# ---------------------------------------------------------------------------


def test_is_bare_exception_handler_helper_matches_known_shapes():
    """Self-test for `_is_bare_exception_handler`. If this is broken,
    the production-scoped tests are silently meaningless.

    Covers the four shapes the helper claims to handle:
      - except Exception:
      - except Exception as e:
      - except (Exception, KeyError):
      - except KeyError:                  (negative)
      - except (KeyError, ValueError):    (negative)
      - except:                           (negative — handled separately)
    """
    samples_and_expectations = [
        ("try:\n    pass\nexcept Exception:\n    pass\n", True),
        ("try:\n    pass\nexcept Exception as e:\n    pass\n", True),
        ("try:\n    pass\nexcept (Exception, KeyError):\n    pass\n", True),
        ("try:\n    pass\nexcept (KeyError, Exception, ValueError):\n    pass\n", True),
        ("try:\n    pass\nexcept KeyError:\n    pass\n", False),
        ("try:\n    pass\nexcept (KeyError, ValueError):\n    pass\n", False),
        ("try:\n    pass\nexcept:\n    pass\n", False),
    ]
    for src, expected in samples_and_expectations:
        tree = ast.parse(src)
        handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
        assert len(handlers) == 1, f"sample produced wrong handler count: {src!r}"
        got = _is_bare_exception_handler(handlers[0])
        assert got is expected, (
            f"_is_bare_exception_handler({src!r}): expected {expected}, got {got}"
        )


def test_find_bare_excepts_helper_detects_only_typeless_except():
    """Self-test for `_find_bare_excepts`. A bare `except:` has
    `handler.type is None`; a typed `except Exception:` does not.
    """
    src_bare = "try:\n    pass\nexcept:\n    pass\n"
    src_typed = "try:\n    pass\nexcept Exception:\n    pass\n"
    assert len(_find_bare_excepts(src_bare)) == 1
    assert len(_find_bare_excepts(src_typed)) == 0
