"""Cluster 5 AC-3 — RED tests: synthetic_history's over-broad exception
handlers must catch a SPECIFIC exception set and log a WARNING that names the
failure and the affected context.

THE DEFECT
----------
``synthetic_history.generate_synthetic_history`` has two over-broad handlers:

  - cache READ  (around the ``json.load`` of the on-disk cache file) catches
    ``except Exception`` and ``print``s — it swallows EVERY error, including
    bugs unrelated to a corrupt cache, with no logged WARNING.
  - cache WRITE (around the ``json.dump`` of the regenerated history) likewise
    catches ``except Exception`` and ``print``s.

The audit (security note in the plan) flags this: a swallowed fetch / cache
error currently produces silently-incomplete history that the autotuner then
optimises against. ``print`` is not captured by the logging pipeline, so an
operator monitoring logs never sees the failure.

THE CONTRACT
------------
Each over-broad handler must:
  1. Catch a SPECIFIC exception set — the exceptions a cache JSON read/write
     can genuinely raise (OSError / IOError family, JSON decode errors,
     UnicodeDecodeError) — NOT a bare ``except`` and NOT ``except Exception``.
  2. Emit a logged WARNING (via the ``logging`` module, not ``print``) that
     NAMES the failure and the affected context — for a cache operation, the
     cache file path; the exception detail must appear in the message.

WHY STATIC + BEHAVIOURAL
------------------------
``generate_synthetic_history`` performs live Alpaca fetches and cannot run
under pytest. The exception-handler SHAPE (which exception types are caught,
that logging.warning is called rather than print) is therefore pinned via
AST static analysis of the source — the established pattern for this module.
A behavioural test additionally exercises the real logging call IF the
implementer extracts a pure cache-load helper; it is import-guarded so it
fails RED with a clear message if no such helper exists yet.

PROVENANCE
----------
No producer-computed value is asserted. The tests assert exception-handler
STRUCTURE and log-record CONTENT (level, presence of the path / detail) —
shape and format, never a numeric literal. ``pytest.approx`` is not relevant.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synth_tree() -> ast.Module:
    """Parsed AST of synthetic_history.py — parsed once per module."""
    assert _SYNTH_PATH.is_file(), f"expected production module at {_SYNTH_PATH}"
    return ast.parse(_SYNTH_PATH.read_text(encoding="utf-8"), filename=str(_SYNTH_PATH))


def _all_except_handlers(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every except-handler in the module."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]


# AC-3 is scoped to the CACHE I/O exception handlers. synthetic_history.py
# also has pre-existing network-retry handlers inside `fetch_bars` that
# legitimately catch `requests.RequestException` (already a specific type —
# not a defect, not in AC-3 scope). The cache-set / cache-context assertions
# below must apply ONLY to cache handlers, so they exclude any handler nested
# inside `fetch_bars`.
_NON_CACHE_FUNCTIONS = frozenset({"fetch_bars"})


def _cache_io_except_handlers(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every except-handler EXCEPT those inside the network-fetch function(s).

    A handler is treated as cache I/O scope unless it is lexically nested
    inside a function named in ``_NON_CACHE_FUNCTIONS`` (the Alpaca network
    retry loop). This keeps AC-3's cache-specific assertions off the
    pre-existing, already-specific ``requests.RequestException`` handlers.
    """
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _NON_CACHE_FUNCTIONS:
            for sub in ast.walk(node):
                if isinstance(sub, ast.ExceptHandler):
                    excluded.add(id(sub))
    return [h for h in _all_except_handlers(tree) if id(h) not in excluded]


def _handler_caught_names(handler: ast.ExceptHandler) -> set[str]:
    """The set of exception-type names a handler catches.

    A bare ``except:`` has type None -> empty set. ``except Exception`` -> a
    one-element set. ``except (A, B)`` -> the tuple members.
    """
    exc = handler.type
    if exc is None:
        return set()  # bare except
    names: set[str] = set()
    targets = exc.elts if isinstance(exc, ast.Tuple) else [exc]
    for t in targets:
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, ast.Attribute):
            names.add(t.attr)
    return names


def _handler_body_calls(handler: ast.ExceptHandler) -> list[ast.Call]:
    """Every Call node inside a handler body."""
    out: list[ast.Call] = []
    for sub in handler.body:
        out.extend(n for n in ast.walk(sub) if isinstance(n, ast.Call))
    return out


def _calls_logging_warning(handler: ast.ExceptHandler) -> bool:
    """True iff the handler body calls logging.warning / logging.error /
    logging.exception — i.e. routes through the logging pipeline.

    WARNING is the contracted level for a recoverable cache failure; error /
    exception are accepted as strictly-louder and therefore satisfy "a logged
    record naming the failure". A bare ``print`` does NOT satisfy this.
    """
    for call in _handler_body_calls(handler):
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in (
            "warning",
            "warn",
            "error",
            "exception",
        ):
            val = func.value
            if isinstance(val, ast.Name) and val.id in ("logging", "logger", "log"):
                return True
            if isinstance(val, ast.Attribute) and val.attr in (
                "logging",
                "logger",
                "log",
            ):
                return True
    return False


def _handler_only_prints(handler: ast.ExceptHandler) -> bool:
    """True iff the handler's only observable output is ``print`` (no logging)."""
    if _calls_logging_warning(handler):
        return False
    for call in _handler_body_calls(handler):
        if isinstance(call.func, ast.Name) and call.func.id == "print":
            return True
    return False


# The exception-name set acceptable for a cache JSON read/write. The handler
# may catch any non-empty SUBSET of these (the implementer scopes to what the
# operation can raise) — but it must NOT broaden to bare Exception/BaseException.
_ACCEPTABLE_CACHE_EXCEPTIONS = {
    "OSError",
    "IOError",
    "FileNotFoundError",
    "PermissionError",
    "JSONDecodeError",
    "ValueError",  # json.JSONDecodeError is a subclass of ValueError
    "UnicodeDecodeError",
    "UnicodeError",
    "TypeError",  # json.dump can raise TypeError on a non-serialisable object
}

_FORBIDDEN_BROAD_EXCEPTIONS = {"Exception", "BaseException"}


# ---------------------------------------------------------------------------
# AC-3 static tests
# ---------------------------------------------------------------------------


def test_no_bare_except_in_synthetic_history(synth_tree: ast.Module) -> None:
    """synthetic_history.py must contain no bare ``except:`` handler.

    A bare except also swallows KeyboardInterrupt / SystemExit. Even though the
    current code uses ``except Exception`` rather than a truly bare except,
    this guard pins the stronger invariant so a future edit cannot regress to
    one.
    """
    bare = [h.lineno for h in _all_except_handlers(synth_tree) if h.type is None]
    assert not bare, (
        f"synthetic_history.py has bare `except:` handler(s) at line(s) "
        f"{bare}. Catch a specific exception set instead."
    )


def test_no_over_broad_exception_handler_swallows_cache_io(
    synth_tree: ast.Module,
) -> None:
    """No handler in synthetic_history.py may catch the over-broad
    ``except Exception`` / ``except BaseException``.

    The two cache I/O handlers currently catch ``except Exception`` and
    swallow every error — including unrelated bugs — producing silently
    incomplete history. Each must be narrowed to the specific exceptions a
    cache JSON read/write can raise.
    """
    offenders: list[tuple[int, str]] = []
    for h in _all_except_handlers(synth_tree):
        caught = _handler_caught_names(h)
        broad = caught & _FORBIDDEN_BROAD_EXCEPTIONS
        if broad:
            offenders.append((h.lineno, ", ".join(sorted(broad))))

    assert not offenders, (
        "synthetic_history.py has over-broad exception handler(s):\n"
        + "\n".join(f"  - line {ln}: catches {names}" for ln, names in offenders)
        + "\nAC-3: catch a SPECIFIC exception set (OSError / JSONDecodeError "
        "/ UnicodeDecodeError family) — `except Exception` swallows unrelated "
        "bugs and produces silently-incomplete autotuner input."
    )


def test_cache_io_handlers_catch_only_specific_io_exceptions(
    synth_tree: ast.Module,
) -> None:
    """Every CACHE I/O except-handler in synthetic_history.py must catch ONLY
    exceptions from the acceptable cache-I/O set.

    This is the positive form of the broad-handler test: not only must the
    broad catches be gone, every cache handler must scope to a known, specific
    exception type. SCOPE: AC-3 covers the cache read/write handlers — the
    pre-existing network-retry handlers inside ``fetch_bars`` legitimately
    catch ``requests.RequestException`` (already specific) and are excluded.
    """
    handlers = _cache_io_except_handlers(synth_tree)
    assert handlers, (
        "synthetic_history.py has no cache-scope except-handlers — the cache "
        "I/O operations must still handle their specific failure modes."
    )

    offenders: list[tuple[int, set[str]]] = []
    for h in handlers:
        caught = _handler_caught_names(h)
        if not caught:
            # bare except — covered by its own test; skip here.
            continue
        unexpected = caught - _ACCEPTABLE_CACHE_EXCEPTIONS
        if unexpected:
            offenders.append((h.lineno, unexpected))

    assert not offenders, (
        "synthetic_history.py cache except-handler(s) catch exception types "
        "outside the expected cache-I/O set:\n"
        + "\n".join(f"  - line {ln}: unexpected {sorted(names)}" for ln, names in offenders)
        + f"\nAcceptable set: {sorted(_ACCEPTABLE_CACHE_EXCEPTIONS)}. The "
        "cache read/write handlers should catch the OSError / JSONDecodeError "
        "/ UnicodeDecodeError family only."
    )


def test_cache_exception_handlers_log_a_warning_not_just_print(
    synth_tree: ast.Module,
) -> None:
    """Every exception handler in synthetic_history.py must route through the
    ``logging`` module — not a bare ``print``.

    ``print`` output is invisible to the logging pipeline an operator
    monitors. A swallowed cache error must produce a logged WARNING (or
    louder) so the failure is observable.
    """
    print_only: list[int] = []
    for h in _all_except_handlers(synth_tree):
        if _handler_only_prints(h):
            print_only.append(h.lineno)

    assert not print_only, (
        f"synthetic_history.py exception handler(s) at line(s) {print_only} "
        f"report failures with `print` only — no logging call. AC-3: emit a "
        f"logged WARNING via the logging module so the failure is visible to "
        f"operators."
    )


def test_cache_exception_handlers_name_the_failure_context(
    synth_tree: ast.Module,
) -> None:
    """Each CACHE exception handler's logged message must NAME the affected
    context.

    For a cache operation the context is the cache file path; the caught
    exception detail must also appear. Static check: every cache handler that
    logs must reference BOTH the caught exception's bound name AND a
    cache-path-like variable in its logging-call arguments. SCOPE: AC-3 covers
    cache I/O — the ``fetch_bars`` network-retry handlers (which name the HTTP
    attempt, not a cache path) are excluded.
    """
    offenders: list[tuple[int, str]] = []
    for h in _cache_io_except_handlers(synth_tree):
        if not _calls_logging_warning(h):
            continue  # non-logging handlers covered by the print test above
        exc_name = h.name  # the `as e` binding, or None

        # Collect identifiers referenced anywhere in the logging call args.
        referenced: set[str] = set()
        for call in _handler_body_calls(h):
            func = call.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in ("warning", "warn", "error", "exception")
            ):
                continue
            for arg in [*call.args, *(kw.value for kw in call.keywords)]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name):
                        referenced.add(sub.id)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        # a format string mentioning the path/symbol counts
                        referenced.add(sub.value)

        names_exc = exc_name is not None and exc_name in referenced
        names_context = any(
            ("cache" in r.lower() or "file" in r.lower() or "path" in r.lower()) for r in referenced
        )
        if not (names_exc and names_context):
            missing = []
            if not names_exc:
                missing.append("the caught exception detail")
            if not names_context:
                missing.append("the cache file path / affected context")
            offenders.append((h.lineno, " and ".join(missing)))

    assert not offenders, (
        "synthetic_history.py logging exception handler(s) do not name the "
        "failure context:\n"
        + "\n".join(f"  - line {ln}: message omits {what}" for ln, what in offenders)
        + "\nAC-3: the WARNING must name both the exception detail and the "
        "affected cache file path so the failure is diagnosable."
    )


# ---------------------------------------------------------------------------
# AC-3 behavioural test — exercised IF the implementer extracts a cache-load
# helper. Import-guarded: fails RED with a clear message if absent.
# ---------------------------------------------------------------------------


def test_cache_load_helper_logs_warning_on_corrupt_cache(tmp_path, caplog) -> None:
    """If synthetic_history exposes a pure cache-load helper, a corrupt cache
    file must produce a logged WARNING that names the file — and the helper
    must NOT propagate the decode error (a corrupt cache is recoverable: the
    history is regenerated).

    This is the behavioural companion to the static handler tests. It is
    import-guarded — if no cache-load helper has been extracted yet it fails
    RED with a message naming the expected surface.
    """
    import logging

    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    helper = None
    for name in ("load_cached_history", "read_history_cache", "_load_cache"):
        candidate = getattr(synth, name, None)
        if callable(candidate):
            helper = candidate
            break
    if helper is None:
        pytest.fail(
            "synthetic_history should expose a pure cache-load helper "
            "(expected one of: load_cached_history / read_history_cache / "
            "_load_cache) so the corrupt-cache exception path is testable "
            "without a live fetch. AC-3: the cache read must be exercised in "
            "isolation."
        )

    # Write a deliberately corrupt (non-JSON) cache file.
    corrupt = tmp_path / "synthetic_history_v2_corrupt.json"
    corrupt.write_text("{ this is not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = helper(str(corrupt))

    # The helper must not raise — a corrupt cache is a recoverable miss.
    # A None / empty return signals "regenerate"; both are acceptable.
    assert result is None or result == {} or not result, (
        f"cache-load helper returned {result!r} for a CORRUPT cache file — a "
        f"corrupt cache must be treated as a miss (None / empty) so the "
        f"history is regenerated, not consumed."
    )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "loading a corrupt cache file produced no WARNING log record. AC-3: "
        "a swallowed cache error must be logged so the operator sees it."
    )
    joined = " ".join(r.getMessage() for r in warning_records)
    assert "corrupt" in joined or str(corrupt) in joined or corrupt.name in joined, (
        f"the corrupt-cache WARNING does not name the affected file. Logged: "
        f"{joined!r}. AC-3: the message must identify the cache file."
    )
