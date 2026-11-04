"""Local checks that need the reference commit. Prints counts and booleans.

Not collected by pytest: CI checks out at depth 1 and has no ``7adc2c56``, so a collected test
would skip there and trip the zero-skip meta check. Run it from the repository root::

    python tests/fixtures/hygiene_waves_3_4/verify_against_ref.py

Prints, per protected file, whether the file with its listed edits cut equals the reference with the
same cut, and the hunk list against the reference (``git diff -U0``); whether the verbatim
z-score oracle of ``tests/math_engine/test_zscore_shared_definition.py`` equals the reference
closure; and whether the files C1 must not touch are unchanged. Exit status 1 when any
check is false.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
REF = "7adc2c56953a3873c013425ebb06033375b62c53"

import tests.math_engine.test_zscore_shared_definition as zscore  # noqa: E402
import tests.test_hygiene_protected_surfaces as pins  # noqa: E402


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)
    return out.stdout.decode("utf-8", errors="replace")


def ref_text(rel: str) -> str:
    return git("show", f"{REF}:{rel}").replace("\r\n", "\n")


def hunks(rel: str) -> list[str]:
    return re.findall(r"^@@ .*? @@", git("diff", "-U0", REF, "--", rel), flags=re.M)


def main() -> int:
    ok = True
    for rel, selector in pins._SCOPE.items():
        same = pins.scope_digest(ref_text(rel), selector) == pins.scope_digest(
            pins._read(rel), selector
        )
        listed = hunks(rel)
        print(f"{rel}: outside-listed-edits equals reference = {same}; hunks = {len(listed)}")
        for header in listed:
            print(f"    {header}")
        ok &= same

    ref_tree = ast.parse(ref_text("math_engine.py"))
    closures = [
        n
        for n in ast.walk(ref_tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "_z"
        and [a.arg for a in n.args.args] == ["values", "mean", "std"]
    ]
    oracle = ast.parse(Path(zscore.__file__).read_text(encoding="utf-8"))
    (mine,) = [
        n for n in ast.walk(oracle) if isinstance(n, ast.FunctionDef) and n.name == "_reference_z"
    ]

    def body_dump(fn: ast.FunctionDef) -> str:
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        return "|".join(ast.dump(stmt) for stmt in body)

    oracle_equal = len(closures) == 3 and all(body_dump(c) == body_dump(mine) for c in closures)
    print(f"z-score oracle equals the {len(closures)} reference closures = {oracle_equal}")
    ok &= oracle_equal

    for rel in ("synthetic_history.py", "migrations", "advisors/composer_backtest_client.py"):
        empty = git("diff", "--stat", REF, "--", rel).strip() == ""
        print(f"{rel}: unchanged = {empty}")
        ok &= empty
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
