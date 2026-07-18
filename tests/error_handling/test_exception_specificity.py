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
# WHY NOT RAW LINE NUMBERS (F7 lesson, 2026-07-18): this whitelist used to be
# a `frozenset[int]` of raw `lineno` values. Every insertion earlier in
# alpha_bot_execution.py (even a wholly unrelated feature, e.g. F7's +80
# lines for a display-layer fix) shifts every handler below it, silently
# breaking the pin and turning this test RED for a file that made zero
# except-clause changes. Keying on STRUCTURE instead of POSITION survives
# that: a handler is exempt only when BOTH hold:
#   (1) its enclosing function name is in WHITELISTED_ENCLOSING_FUNCTIONS
#       (survives line shifts anywhere in the file), AND
#   (2) the handler's OWN source span (not just somewhere in its function)
#       carries _BROAD_EXCEPT_WHITELIST_MARKER (ties the exemption to that
#       SPECIFIC handler -- a new, unrelated broad except added later
#       inside either whitelisted function would NOT carry this marker and
#       would still be caught; function-name-only keying would have
#       blanket-exempted the whole function, which is forbidden).
# The marker is pre-existing production-code text (each handler's own
# justification comment, written when the handler was built) -- keying off
# it needs ZERO changes to alpha_bot_execution.py.
# NEVER widen this set as a quick-fix to make the test pass; the fix is to
# narrow the except clause in production code instead.
WHITELISTED_ENCLOSING_FUNCTIONS: frozenset[str] = frozenset(
    {
        # seed_symphonies_into_bot_state: per-account AC-4 partial-success barrier.
        # fetch_symphony_stats may raise any exception type (e.g. RuntimeError);
        # narrowing is infeasible and would break AC-4.
        "seed_symphonies_into_bot_state",
        # ensure_bot_state_seeded: top-level daemon startup fail-safe barrier (AC-4).
        # Wraps load_state + presence-check + seed + save; must swallow all exception
        # types to prevent daemon crash at startup.
        "ensure_bot_state_seeded",
    }
)

# Marker substring required in a handler's own source span (see
# _is_whitelisted_broad_handler) for the function-name exemption above to
# apply. Both whitelisted handlers already carry this exact phrase in their
# production-code justification comment (from
# tests/engine/test_startup_seed_symphonies.py's TestFailSafeStartup, which
# mandates the fail-safe behavior both handlers implement) — confirmed via
# `grep -n "Mandated by tests/engine/test_startup_seed_symphonies.py"
# alpha_bot_execution.py`, present at exactly these two handlers and nowhere
# else in the file. If a handler's comment is ever reworded to drop this
# exact phrase, this test intentionally goes RED again — a real signal the
# whitelist needs re-review, not silent drift.
_BROAD_EXCEPT_WHITELIST_MARKER = "Mandated by tests/engine/test_startup_seed_symphonies.py"


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
        return any(isinstance(elt, ast.Name) and elt.id == "Exception" for elt in exc_type.elts)
    return False


def _handler_source_span(source_lines: list[str], handler: ast.ExceptHandler) -> str:
    """Return the raw source text spanning handler.lineno..handler.end_lineno
    (1-indexed, inclusive) -- the physical lines the except clause AND its
    body occupy (verified empirically: end_lineno covers through the last
    body statement, not just the `except ... :` line).

    Comments are not part of the AST, so this is a raw-text slice, not a
    node walk -- used only to check for the whitelist marker comment, never
    for control-flow decisions.
    """
    assert handler.end_lineno is not None, (
        "ExceptHandler.end_lineno is None -- this repo's minimum supported "
        "Python (3.8+) always populates it; something is unexpectedly odd "
        "about the interpreter running this test."
    )
    return "\n".join(source_lines[handler.lineno - 1 : handler.end_lineno])


def _is_whitelisted_broad_handler(
    tree: ast.Module, source_lines: list[str], handler: ast.ExceptHandler
) -> bool:
    """Return True iff `handler` is an approved broad except -- BOTH its
    enclosing function is in WHITELISTED_ENCLOSING_FUNCTIONS AND its own
    source span carries _BROAD_EXCEPT_WHITELIST_MARKER.

    Requiring both prevents two failure modes a single-condition check
    would allow:
      - function-name-only: a NEW broad except added later anywhere else
        inside one of the two whitelisted functions would be silently
        exempted too (blanket function-wide exemption -- forbidden).
      - marker-only: an unrelated comment elsewhere in the file that
        happened to contain the marker phrase would falsely exempt some
        other function's handler.
    """
    if _enclosing_function_name(tree, handler) not in WHITELISTED_ENCLOSING_FUNCTIONS:
        return False
    return _BROAD_EXCEPT_WHITELIST_MARKER in _handler_source_span(source_lines, handler)


def _find_broad_handlers(source: str) -> tuple[ast.Module, list[ast.ExceptHandler]]:
    """Walk the AST and return (tree, offenders) -- every ExceptHandler that
    matches the broad-catch pattern AND is not whitelisted, plus the parse
    tree used to find them.

    Returns the tree alongside the offenders (mirroring
    _response_json_calls_in_function's established pattern elsewhere in this
    file) so callers that also need to walk the AST for diagnostics (e.g.
    _enclosing_function_name) reuse the SAME tree -- a second
    ast.parse(source) call produces object-distinct nodes even for
    identical source, so `sub is target` identity checks silently return
    False across separate trees. (This exact bug previously made this
    test's OWN failure message report every offender as being in
    "<module>" instead of its real enclosing function -- harmless while
    whitelisting was purely lineno-based, but load-bearing now that
    whitelisting itself resolves enclosing-function names.)
    """
    tree = ast.parse(source)
    source_lines = source.splitlines()
    broad: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_bare_exception_handler(node):
            continue
        if _is_whitelisted_broad_handler(tree, source_lines, node):
            continue
        broad.append(node)
    return tree, broad


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
    tree, offenders = _find_broad_handlers(source)

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
    _tree, offenders = _find_broad_handlers(source)
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


# ---------------------------------------------------------------------------
# Helpers for narrow-union coverage tests (BLOCK 1 and BLOCK 2)
# ---------------------------------------------------------------------------


def _except_type_names(handler: ast.ExceptHandler) -> set[str]:
    """Return the set of bare Name ids present in an ExceptHandler's type.

    Handles three forms:
      except SomeName:          -> {'SomeName'}
      except (A, B, C):         -> {'A', 'B', 'C'}
      except mod.Attr:          -> set() — Attribute nodes are ignored;
                                   we are testing for bare Name presence only.

    Only traverses one level of Tuple — no nested tuples (Python does not
    allow nested exception tuples in except clauses).
    """
    if handler.type is None:
        return set()
    if isinstance(handler.type, ast.Name):
        return {handler.type.id}
    if isinstance(handler.type, ast.Tuple):
        names: set[str] = set()
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name):
                names.add(elt.id)
        return names
    return set()


def _handlers_in_function(source: str, function_name: str) -> list[ast.ExceptHandler]:
    """Return every ExceptHandler node that is directly (or indirectly)
    nested inside the first FunctionDef / AsyncFunctionDef whose name
    matches *function_name*.

    Search order: top-level walk, first match wins. Fails loudly if the
    function is not found — a missing function means the production code
    was restructured beyond the scope of a simple union-coverage fix and
    the reviewer's BLOCK finding must be re-evaluated.
    """
    tree = ast.parse(source)
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                target_func = node
                break
    assert target_func is not None, (
        f"Function '{function_name}' not found in alpha_bot_execution.py. "
        "The BLOCK finding assumed this function exists at the reviewed "
        "commit; if it was renamed or deleted the reviewer's analysis "
        "must be re-opened before narrowing the test."
    )
    return [node for node in ast.walk(target_func) if isinstance(node, ast.ExceptHandler)]


# ---------------------------------------------------------------------------
# Test 6 — BLOCK 1: L248 fetch_intraday_vwaps except must include TypeError
# ---------------------------------------------------------------------------


def test_vwap_batch_except_includes_TypeError():
    """The except handler inside fetch_intraday_vwaps (near L248) must
    include TypeError in its exception union.

    SPECIFIC EXCEPTION TYPE CHECKED: TypeError

    REALISTIC FAILURE SCENARIO:
    The line `df['pv'] = df['c'] * df['v']` performs pandas Series
    multiplication. If the Alpaca /stocks/bars endpoint returns bar fields
    as strings (an observed schema regression where numeric fields are
    serialised as "123.45" instead of 123.45), `pd.DataFrame(bars)` builds
    a DataFrame with dtype=object columns. The `*` operator on two object-
    dtype Series raises TypeError: "can't multiply sequence by non-int of
    type 'float'". This is a per-minute live-execution call site inside the
    minute-cadence scheduler subprocess; an uncaught TypeError propagates
    out of fetch_intraday_vwaps, unwinds the main() call stack, and kills
    the subprocess for that minute — the engine misses an execution cycle.

    REVIEWER'S BLOCK FINDING (cycle 27+28 GREEN commit):
    The implementer narrowed the original broad `except Exception` to
    `except (requests.RequestException, ValueError, KeyError)`. That union
    correctly covers network failures (RequestException), JSON shape errors
    (ValueError / KeyError), but omits TypeError. The reviewer flagged this
    as BLOCK: the dtype-mismatch crash path is real, was observable in
    staging logs from the Alpaca schema regression, and is NOT covered by
    any of the three named types. The required fix is to add TypeError to
    the union: `except (requests.RequestException, ValueError, KeyError,
    TypeError)`.

    This test is RED against the current GREEN commit and will turn GREEN
    once the implementer adds TypeError to the union.
    """
    source = _read_source(ALPHA_BOT_PATH)
    handlers = _handlers_in_function(source, "fetch_intraday_vwaps")
    assert handlers, (
        "fetch_intraday_vwaps contains no except handlers; at least one "
        "is required to guard the Alpaca batch network call."
    )
    # Collect all exception type names across every handler in the function.
    all_names: set[str] = set()
    for h in handlers:
        all_names.update(_except_type_names(h))

    assert "TypeError" in all_names, (
        "fetch_intraday_vwaps does not catch TypeError. "
        "The line `df['c'] * df['v']` raises TypeError when Alpaca returns "
        "string-typed bar fields (schema regression). Without TypeError in "
        "the except union, this crash propagates to the scheduler subprocess "
        "and drops an execution cycle. "
        f"Current union names found across all handlers: {sorted(all_names)}. "
        "Required fix: add TypeError to the except union at ~L248."
    )


# ---------------------------------------------------------------------------
# Test 7 — BLOCK 2: L259 get_current_et except must include KeyError
#           (covers ZoneInfoNotFoundError, which subclasses KeyError)
# ---------------------------------------------------------------------------


def test_get_current_et_except_includes_KeyError_for_ZoneInfoNotFoundError():
    """The except handler inside get_current_et (near L259) must include
    KeyError (or ZoneInfoNotFoundError directly) in its exception union.

    SPECIFIC EXCEPTION TYPE CHECKED: KeyError
    (ZoneInfoNotFoundError subclasses KeyError; catching KeyError is the
    minimal correct way to cover it without importing zoneinfo at test time)

    REALISTIC FAILURE SCENARIO:
    `ZoneInfo("America/New_York")` raises zoneinfo.ZoneInfoNotFoundError
    when the system tzdata package is absent. This is the normal state on
    many minimal Docker base images (e.g., python:3.11-slim, Alpine). The
    function has a manual DST-math fallback below the except block; the
    original broad `except Exception` reached it. The implementer's narrow
    union `except (ImportError, ModuleNotFoundError)` catches only the
    case where the zoneinfo module itself is missing (Python < 3.9), but
    NOT the case where the module imports fine but the timezone key is
    absent. ZoneInfoNotFoundError is a subclass of KeyError; the minimal
    correct union is `except (ImportError, KeyError)` — which also
    makes ModuleNotFoundError redundant (it is a subclass of ImportError).

    REVIEWER'S BLOCK FINDING (cycle 27+28 GREEN commit):
    The implementer correctly removed the broad `except Exception` and
    narrowed to `except (ImportError, ModuleNotFoundError)`. The reviewer
    flagged this as BLOCK: the union misses ZoneInfoNotFoundError. On a
    containerised deployment without tzdata, `ZoneInfo("America/New_York")`
    raises ZoneInfoNotFoundError; the narrow except does not catch it; the
    exception propagates out of get_current_et; every downstream call that
    uses `current_et` (market-hours check, decision timestamps) crashes
    for the entire cycle — much worse than silently falling back to manual
    DST math. The required fix is `except (ImportError, KeyError)`.

    This test is RED against the current GREEN commit and will turn GREEN
    once the implementer adds KeyError to the union.
    """
    source = _read_source(ALPHA_BOT_PATH)
    handlers = _handlers_in_function(source, "get_current_et")
    assert handlers, (
        "get_current_et contains no except handlers; at least one is "
        "required to guard the zoneinfo import / ZoneInfo() constructor."
    )
    all_names: set[str] = set()
    for h in handlers:
        all_names.update(_except_type_names(h))

    assert "KeyError" in all_names, (
        "get_current_et does not catch KeyError. "
        "ZoneInfoNotFoundError (raised by ZoneInfo('America/New_York') when "
        "tzdata is absent) is a subclass of KeyError. Without KeyError in "
        "the except union, a containerised deployment without tzdata causes "
        "get_current_et to propagate an unhandled exception, crashing the "
        "full execution cycle rather than falling back to manual DST math. "
        f"Current union names found across all handlers: {sorted(all_names)}. "
        "Required fix: add KeyError to the except union at ~L259, e.g. "
        "`except (ImportError, KeyError):`."
    )


# ---------------------------------------------------------------------------
# Helpers for Test 8 — response.json() wrap detection
# ---------------------------------------------------------------------------


# Exception type names (bare Name ids) that cover ValueError and its
# relevant subclasses.  JSONDecodeError is a subclass of ValueError; the
# requests library re-exports it as requests.exceptions.JSONDecodeError
# (an Attribute node, not a bare Name).  We therefore accept any of the
# following bare names as "covers ValueError":
#   ValueError        — covers JSONDecodeError transitively
#   JSONDecodeError   — narrower but still sufficient
#   Exception         — over-broad but technically covers it; listed only
#                       so the helper is exhaustive; the broad-except tests
#                       above already forbid this form.
_JSON_COVERING_NAMES: frozenset[str] = frozenset({"ValueError", "JSONDecodeError", "Exception"})


def _response_json_calls_in_function(
    source: str, function_name: str
) -> tuple[ast.Module, list[ast.Call]]:
    """Return *(tree, calls)* where *tree* is the single ast.Module produced
    by parsing *source* and *calls* is every ast.Call node that represents a
    `response.json()` call inside *function_name*.

    Returning the parse tree alongside the calls is intentional: callers that
    also need to walk the AST (e.g. to find the enclosing function node) MUST
    use this same tree so that all ast node objects share identity.  Calling
    ast.parse(source) a second time produces a structurally identical but
    object-distinct tree; cross-tree identity checks (`sub is target_call`)
    will always return False even when the call is genuinely present.

    Matches the pattern `<any_name>.json()` where the attribute is 'json'
    and the argument list is empty — this is the canonical form of
    `requests.Response.json()` in the codebase.  We do not require the
    object to be named exactly 'response' to be robust against simple
    variable renames, but we DO require the call to be attribute-style
    (Attribute node) and zero-argument (no positional or keyword args),
    which uniquely identifies the Response.json() decode call.
    """
    tree = ast.parse(source)
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                target_func = node
                break
    assert target_func is not None, (
        f"Function '{function_name}' not found in alpha_bot_execution.py. "
        "If the function was renamed, this test must be updated to match."
    )
    calls: list[ast.Call] = []
    for node in ast.walk(target_func):
        if not isinstance(node, ast.Call):
            continue
        func_node = node.func
        if not isinstance(func_node, ast.Attribute):
            continue
        if func_node.attr != "json":
            continue
        if node.args or node.keywords:
            continue  # .json() takes no arguments in normal usage
        calls.append(node)
    return tree, calls


def _call_is_inside_try(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    target_call: ast.Call,
) -> bool:
    """Return True iff *target_call* is a descendant of at least one
    ast.Try node inside *function_node* whose handlers include a type
    that covers ValueError (see _JSON_COVERING_NAMES).

    Algorithm: walk the function body collecting (Try, handler-names) pairs.
    For each such Try, walk its subtree looking for the exact target_call
    identity.  If found, inspect the Try's handlers for a covering name.

    Note on Attribute-type handlers (e.g., `except requests.exceptions.
    JSONDecodeError`): those have ast.Attribute type nodes, not ast.Name.
    We accept them as valid by checking for any Attribute whose final
    .attr is 'JSONDecodeError' — this avoids needing to resolve the full
    dotted path while still being specific enough.
    """
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Try):
            continue
        # Check whether target_call lives inside this Try's body/orelse/
        # finalbody subtrees (i.e., under protection of its handlers).
        call_found_in_protected_body = any(
            sub is target_call for stmt in node.body for sub in ast.walk(stmt)
        )
        if not call_found_in_protected_body:
            continue
        # The call is inside this Try's body.  Now check whether any
        # handler covers ValueError or a named subclass.
        for handler in node.handlers:
            exc_type = handler.type
            if exc_type is None:
                # bare except — covers everything including ValueError
                return True
            if isinstance(exc_type, ast.Name) and exc_type.id in _JSON_COVERING_NAMES:
                return True
            if isinstance(exc_type, ast.Attribute) and exc_type.attr == "JSONDecodeError":
                # e.g. requests.exceptions.JSONDecodeError or json.JSONDecodeError
                return True
            if isinstance(exc_type, ast.Tuple):
                for elt in exc_type.elts:
                    if isinstance(elt, ast.Name) and elt.id in _JSON_COVERING_NAMES:
                        return True
                    if isinstance(elt, ast.Attribute) and elt.attr == "JSONDecodeError":
                        return True
    return False


# ---------------------------------------------------------------------------
# Test 8 — response.json() in fetch_alpaca_history must be try-wrapped
# ---------------------------------------------------------------------------


def test_fetch_alpaca_history_wraps_response_json_in_try():
    """response.json() inside fetch_alpaca_history must be enclosed in a
    try/except whose handlers cover ValueError (or a recognised subclass
    such as json.JSONDecodeError / requests.exceptions.JSONDecodeError).

    SPECIFIC FAILURE MODE:
    At authoring time, alpha_bot_execution.py L183 contains:

        data = response.json()

    This line sits OUTSIDE any try/except — it is at the top level of the
    function's while-loop body, after the retry loop's `if not success:
    break` guard.  The retry loop's own try/except only wraps
    `requests.RequestException`, which covers network-level failures
    (connection refused, timeout, DNS error).  It does NOT wrap the
    JSON-decode step.

    PRODUCTION CONCERN:
    An HTTP 200 response does not guarantee a JSON body.  Real scenarios
    where Alpaca returns HTTP 200 with non-JSON content:
      - CDN edge nodes return an HTML error page (e.g., Cloudflare 503
        served as 200 with content-type: text/html) on transient gateway
        overload.
      - API path misrouting (proxy config bug) returns a JSON-envelope
        wrapper from the gateway rather than the Alpaca data layer; the
        outer envelope may be valid JSON but lack the "bars" key, OR the
        gateway may return a plain-text diagnostic that is not JSON at all.
      - Alpaca staging/canary deployments occasionally return a 200 with
        an HTML maintenance page before the health check redirects kick in.

    In every one of these scenarios, `response.json()` raises
    `requests.exceptions.JSONDecodeError` (a subclass of `ValueError`).
    Because L183 is unguarded, the exception propagates uncaught out of
    `fetch_alpaca_history`, unwinds through the main scheduler loop, and
    crashes the subprocess for that minute — the engine misses a full
    execution cycle including all risk decisions for live positions.

    REQUIRED FIX:
    Wrap `response.json()` in a try/except that catches ValueError (or
    the narrower json.JSONDecodeError / requests.exceptions.JSONDecodeError).
    On decode failure: log the content-type and first 200 chars of the
    response body, then break out of the page loop with empty data for
    this batch (same semantics as `if not success: break` above it).

    Minimal correct pattern:
        try:
            data = response.json()
        except (ValueError, KeyError):
            print(f"Non-JSON response from Alpaca ...")
            break

    EXPECTED STATE AT AUTHORING: RED.
    L183 is not wrapped; this test will FAIL against the current code.
    It will turn GREEN once the implementer wraps response.json() in a
    try/except covering ValueError (or an accepted subclass/alias).
    """
    source = _read_source(ALPHA_BOT_PATH)

    # Step 1 + 2 share ONE parse tree so that ast node identity is consistent.
    # _response_json_calls_in_function returns (tree, calls); reusing that tree
    # here ensures `sub is target_call` comparisons in _call_is_inside_try work
    # correctly — a second ast.parse(source) call produces object-distinct nodes
    # even for identical source, making all identity checks silently return False.
    tree, json_calls = _response_json_calls_in_function(source, "fetch_alpaca_history")

    # Step 2: locate fetch_alpaca_history in the SHARED tree (same tree as json_calls).
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "fetch_alpaca_history":
                target_func = node
                break

    assert target_func is not None, (
        "Function 'fetch_alpaca_history' not found in alpha_bot_execution.py. "
        "If the function was renamed, this test must be updated to match."
    )

    assert json_calls, (
        "No .json() call found inside fetch_alpaca_history. "
        "If the JSON-decode step was refactored to a helper, locate the "
        "new call site and update this test accordingly."
    )

    # Step 3: for every .json() call, assert it is inside a Try whose
    # handlers cover ValueError or a recognised subclass.
    unguarded: list[int] = []
    for call in json_calls:
        if not _call_is_inside_try(target_func, call):
            unguarded.append(call.lineno)

    assert not unguarded, (
        f"response.json() call(s) at line(s) {unguarded} inside "
        "fetch_alpaca_history are NOT wrapped in a try/except that covers "
        "ValueError (or json.JSONDecodeError / requests.exceptions."
        "JSONDecodeError). An HTTP 200 response with a non-JSON body "
        "(CDN error page, gateway HTML, content-type misroute) will raise "
        "requests.exceptions.JSONDecodeError, propagate uncaught out of "
        "fetch_alpaca_history, and crash the scheduler subprocess — the "
        "engine misses the full execution cycle. "
        "Fix: wrap response.json() in try/except (ValueError, KeyError) "
        "with a log + break fallback."
    )


# ---------------------------------------------------------------------------
# Test 9 — response.json() in fetch_symphony_stats must be try-wrapped
# ---------------------------------------------------------------------------


def test_fetch_symphony_stats_wraps_response_json_in_try():
    """response.json() inside fetch_symphony_stats must be enclosed in a
    try/except whose handlers cover ValueError (or a recognised subclass
    such as json.JSONDecodeError / requests.exceptions.JSONDecodeError).

    SPECIFIC FAILURE MODE:
    At authoring time, alpha_bot_execution.py L88 contains:

        return response.json().get("symphonies", [])

    This line sits OUTSIDE any try/except that covers ValueError — it is
    inside the outer `if response.status_code == 200:` branch, which is
    itself protected only by `except requests.RequestException`.
    requests.RequestException does NOT catch json.JSONDecodeError
    (a subclass of ValueError), which is raised by response.json() when
    the HTTP body is not valid JSON.

    PRODUCTION CONCERN:
    An HTTP 200 response does not guarantee a JSON body.  Real scenarios
    where Composer returns HTTP 200 with non-JSON content:
      - Composer CDN edge nodes (Cloudflare or similar) return an HTML
        error page with status 200 on transient gateway overload.
      - API gateway proxy misroutes return plain-text diagnostics.
      - Composer maintenance/canary deployments occasionally return a 200
        with an HTML maintenance page.

    In every one of these scenarios, `response.json()` raises
    `requests.exceptions.JSONDecodeError` (a subclass of `ValueError`).
    Because L88 is unguarded for ValueError, the exception propagates
    uncaught out of `fetch_symphony_stats`, unwinds through the main
    scheduler loop, and crashes the subprocess for that minute — the
    engine misses a full execution cycle including all risk decisions for
    live Composer portfolio positions.

    REQUIRED FIX:
    Wrap `response.json()` in an inner try/except that catches ValueError.
    On decode failure: log the HTTP status code (no body echo — secrets
    hygiene), then return [] (matches all other failure paths in the
    function: requests.RequestException returns [], non-200 returns []).

    Minimal correct pattern (inner try only; outer RequestException stays):
        try:
            return response.json().get("symphonies", [])
        except ValueError as e:
            print(f"Error parsing Composer response JSON: HTTP {response.status_code} - {e}")
            return []

    EXPECTED STATE AT AUTHORING: RED.
    L88 is not wrapped for ValueError; this test will FAIL against the
    current code.  It will turn GREEN once the implementer wraps
    response.json() in an inner try/except covering ValueError.

    Same class of fix as task #29 / test_fetch_alpaca_history_wraps_response_json_in_try.
    """
    source = _read_source(ALPHA_BOT_PATH)

    # Step 1 + 2 share ONE parse tree so that ast node identity is consistent.
    # _response_json_calls_in_function returns (tree, calls); reusing that tree
    # here ensures `sub is target_call` comparisons in _call_is_inside_try work
    # correctly — a second ast.parse(source) call produces object-distinct nodes
    # even for identical source, making all identity checks silently return False.
    tree, json_calls = _response_json_calls_in_function(source, "fetch_symphony_stats")

    # Locate fetch_symphony_stats in the SHARED tree (same tree as json_calls).
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "fetch_symphony_stats":
                target_func = node
                break

    assert target_func is not None, (
        "Function 'fetch_symphony_stats' not found in alpha_bot_execution.py. "
        "If the function was renamed, this test must be updated to match."
    )

    assert json_calls, (
        "No .json() call found inside fetch_symphony_stats. "
        "If the JSON-decode step was refactored to a helper, locate the "
        "new call site and update this test accordingly."
    )

    # For every .json() call, assert it is inside a Try whose handlers cover
    # ValueError or a recognised subclass.
    unguarded: list[int] = []
    for call in json_calls:
        if not _call_is_inside_try(target_func, call):
            unguarded.append(call.lineno)

    assert not unguarded, (
        f"response.json() call(s) at line(s) {unguarded} inside "
        "fetch_symphony_stats are NOT wrapped in a try/except that covers "
        "ValueError (or json.JSONDecodeError / requests.exceptions."
        "JSONDecodeError). An HTTP 200 response with a non-JSON body "
        "(Composer CDN error page, gateway HTML, maintenance page) will "
        "raise requests.exceptions.JSONDecodeError, propagate uncaught out "
        "of fetch_symphony_stats, and crash the scheduler subprocess — the "
        "engine misses the full execution cycle for all Composer portfolio "
        "risk decisions. "
        "Fix: wrap response.json() in an inner try/except ValueError with "
        "a log + return [] fallback (matches all other failure paths)."
    )
