"""Local check that the account-id redaction changed nothing but the id.

Run it by hand, never from pytest: it needs the reference commit, which CI's depth-one
checkout does not have, and the id itself, which is never committed.

    python tests/fixtures/hygiene_waves_3_4/verify_redaction_against_ref.py [--ref REF] [--root DIR]

It reads the id at run time from the reference commit's fixture
``tests/fixtures/refresh_timing/02-c3/bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json`` and the
other account ids from the ``ACCOUNT_*`` values of ``<root>/.env`` (falling back to the
repository's own ``.env``). Held only in memory. The output is counts, booleans, file
paths and float differences: never a value that came from a record, an id or a prefix.

Checks:

* the tree holds no file (gzip decompressed) with the id, with its 8-character prefix, or
  with any other ``ACCOUNT_*`` value or its prefix;
* S-6.5: each gzip record equals the reference record with the id replaced;
* S-6.6: each other changed text file equals the reference file with the id and the prefix
  replaced, except that a file written with sorted keys is compared after parsing and
  must still be a sorted dump; ``PROVENANCE.md`` files restate digests, so they are only
  checked for holding no id;
* S-6.10: the five re-produced goldens equal the reference goldens with the id replaced,
  every non-float value exactly and every float within ``math.isclose(rel_tol=1e-12,
  abs_tol=0)``; the largest difference is printed. The one reorder allowed is the move of
  whole account groups in ``symphonies``, which the producer orders by account key, and
  ``daemon_started_at`` is excluded because the tests drop it.

Exit status 0 only when every count is zero and every check is true.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import subprocess
import sys
from pathlib import Path

PLACEHOLDER = b"ACCOUNT_ID_REDACTED"
ID_SOURCE = "tests/fixtures/refresh_timing/02-c3/bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json"
GOLDENS = tuple(
    f"tests/fixtures/state_poll_no_table/golden_ref_{name}.json"
    for name in (
        "closed_frozen_two_accounts",
        "closed_guard_alpha_fail",
        "closed_missing_defaults",
        "closed_null_defaults",
        "closed_unnamed_entry",
    )
)
FLOAT_REL_TOL = 1e-12
SKIP_DIRS = {".git", ".claude", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


def _git(repo: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if done.returncode:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _sorted_dump(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _read_env_accounts(*candidates: Path) -> list[str]:
    for env in candidates:
        if env.is_file():
            values = []
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                key, sep, value = line.strip().partition("=")
                if (
                    sep
                    and key.strip().startswith("ACCOUNT")
                    and len(value.strip().strip("'\"")) >= 8
                ):
                    values.append(value.strip().strip("'\""))
            return values
    return []


def _tree_files(root: Path) -> list[str]:
    if (root / ".git").exists():
        names = _git(root, "ls-files", "-z").decode("utf-8").split("\0")
        return sorted(n for n in names if n and (root / n).is_file())
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name != ".env":
                found.append(Path(base, name).relative_to(root).as_posix())
    return sorted(found)


def _content(path: Path) -> bytes:
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        try:
            return gzip.decompress(data)
        except (OSError, EOFError):
            return data
    return data


def _replace(data: bytes, secret: bytes, prefix: bytes) -> bytes:
    return data.replace(secret, PLACEHOLDER).replace(prefix, PLACEHOLDER)


def _compare(old: object, new: object, state: dict) -> bool:
    """Exact for everything but floats; floats within the tolerance. Iterative."""
    stack = [(old, new)]
    ok = True
    while stack:
        a, b = stack.pop()
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                ok = False
                state["non_float_differences"] += 1
                continue
            stack.extend((a[k], b[k]) for k in a)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                ok = False
                state["non_float_differences"] += 1
                continue
            stack.extend(zip(a, b, strict=True))
        elif isinstance(a, float) and isinstance(b, float):
            state["float_pairs"] += 1
            if a == b:
                continue
            diff = abs(a - b)
            state["max_abs"] = max(state["max_abs"], diff)
            scale = max(abs(a), abs(b))
            state["max_rel"] = max(state["max_rel"], diff / scale if scale else math.inf)
            if not math.isclose(a, b, rel_tol=FLOAT_REL_TOL, abs_tol=0.0):
                ok = False
                state["float_outside_tolerance"] += 1
        elif type(a) is not type(b) or a != b:
            ok = False
            state["non_float_differences"] += 1
    return ok


def _scope_at_ref(repo: Path, ref: str, secret: bytes, prefix: bytes) -> list[str]:
    """Files at the reference that hold the id or its prefix (gzip decompressed)."""
    hits = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "grep",
            "-a",
            "-l",
            "-z",
            "-F",
            "-e",
            secret.decode(),
            "-e",
            prefix.decode(),
            ref,
        ],
        capture_output=True,
    ).stdout.decode("utf-8", errors="replace")
    found = {name.split(":", 1)[1] for name in hits.split(chr(0)) if ":" in name}
    for rel in _git(repo, "ls-tree", "-r", "--name-only", ref).decode().splitlines():
        if rel.endswith(".gz"):
            try:
                blob = gzip.decompress(_git(repo, "show", f"{ref}:{rel}"))
            except (OSError, EOFError):
                continue
            if secret in blob or prefix in blob:
                found.add(rel)
    return sorted(found)


def main() -> int:
    repo_default = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="7adc2c56")
    parser.add_argument("--root", type=Path, default=repo_default)
    args = parser.parse_args()
    root: Path = args.root.resolve()

    try:
        secret = json.loads(_git(repo_default, "show", f"{args.ref}:{ID_SOURCE}"))[
            "account"
        ].encode()
    except (RuntimeError, KeyError, ValueError):
        print("reference commit or its id fixture is unavailable; nothing was checked")
        return 2
    prefix = secret[:8]
    env_values = [v.encode() for v in _read_env_accounts(root / ".env", repo_default / ".env")]
    all_secrets = {secret, *env_values}

    files = _tree_files(root)
    with_id = with_prefix = with_any_account = with_any_prefix = 0
    for rel in files:
        data = _content(root / rel)
        with_id += secret in data
        with_prefix += prefix in data
        with_any_account += any(s in data for s in all_secrets)
        with_any_prefix += any(s[:8] in data for s in all_secrets)
    print(f"tree files scanned: {len(files)}")
    print(f"files with the id: {with_id}")
    print(f"files with its 8-character prefix: {with_prefix}")
    print(f"env ACCOUNT_* values read: {len(env_values)}")
    print(f"files with any env account id: {with_any_account}")
    print(f"files with the prefix of any env account id: {with_any_prefix}")

    scope = _scope_at_ref(repo_default, args.ref, secret, prefix)
    print(f"files holding the id or its prefix at the reference: {len(scope)}")

    gz = [r for r in scope if r.endswith(".gz")]
    s65_ok = True
    s65_bytes = 0
    for rel in gz:
        ref_raw = gzip.decompress(_git(repo_default, "show", f"{args.ref}:{rel}"))
        expect = _replace(ref_raw, secret, prefix)
        path = root / rel
        if not path.is_file():
            s65_ok = False
            continue
        head = _content(path)
        s65_bytes += head == expect
        try:
            s65_ok &= json.loads(head) == json.loads(expect)
        except ValueError:
            s65_ok = False
    print(f"S-6.5 {s65_ok} ({len(gz)} gzip records, {s65_bytes} byte-equal after decompression)")

    exempt = [r for r in scope if r.endswith("PROVENANCE.md")]
    golden_set = set(GOLDENS)
    s66_files = [
        r for r in scope if r not in golden_set and r not in exempt and not r.endswith(".gz")
    ]
    s66_ok = True
    differing = []
    for rel in s66_files:
        path = root / rel
        if not path.is_file():
            differing.append(rel)
            continue
        expect = _lf(_replace(_git(repo_default, "show", f"{args.ref}:{rel}"), secret, prefix))
        head = _lf(path.read_bytes())
        if head == expect:
            continue
        try:
            ref_text = _lf(_git(repo_default, "show", f"{args.ref}:{rel}")).decode("utf-8")
            ref_sorted = rel.endswith(".json") and ref_text == _sorted_dump(json.loads(ref_text))
            head_obj = json.loads(head)
            same_content = head_obj == json.loads(expect)
            still_sorted = head.decode("utf-8") == _sorted_dump(head_obj)
        except ValueError:
            differing.append(rel)
            continue
        if not (ref_sorted and same_content and still_sorted):
            differing.append(rel)
    s66_ok = not differing
    exempt_clean = all(
        (root / r).is_file()
        and secret not in _content(root / r)
        and prefix not in _content(root / r)
        for r in exempt
    )
    print(
        f"S-6.6 {s66_ok and exempt_clean} ({len(s66_files)} compared, {len(differing)} differ, "
        f"{len(exempt)} PROVENANCE.md exempt from the byte comparison, exempt files free of the id: {exempt_clean})"
    )
    for rel in differing[:25]:
        print(f"  differs: {rel}")

    state = {
        "non_float_differences": 0,
        "float_pairs": 0,
        "float_outside_tolerance": 0,
        "max_abs": 0.0,
        "max_rel": 0.0,
    }
    s610_ok = True
    for rel in GOLDENS:
        path = root / rel
        if not path.is_file():
            s610_ok = False
            continue
        expect = json.loads(
            _replace(_git(repo_default, "show", f"{args.ref}:{rel}"), secret, prefix)
        )
        head = _lf(path.read_bytes()).decode("utf-8")
        head_obj = json.loads(head)
        # The producer lists `symphonies` by account key and then name, so the redacted account key
        # moves its whole group; a stable sort of the reference list by account is the one reorder
        # allowed. `daemon_started_at` is the producing process's start time, which the tests drop.
        if isinstance(expect.get("symphonies"), list):
            expect["symphonies"] = sorted(expect["symphonies"], key=lambda e: str(e.get("account")))
        expect.pop("daemon_started_at", None)
        head_obj.pop("daemon_started_at", None)
        s610_ok &= _compare(expect, head_obj, state)
        s610_ok &= head == _sorted_dump(json.loads(head))
    print(
        f"S-6.10 {s610_ok} ({len(GOLDENS)} goldens, {state['float_pairs']} float pairs, "
        f"{state['non_float_differences']} non-float differences, "
        f"{state['float_outside_tolerance']} floats outside rel_tol={FLOAT_REL_TOL})"
    )
    print(
        f"largest golden float difference: absolute {state['max_abs']:.6g}, relative {state['max_rel']:.6g}"
    )

    clean = with_id == with_prefix == with_any_account == with_any_prefix == 0
    return 0 if clean and s65_ok and s66_ok and exempt_clean and s610_ok else 1


if __name__ == "__main__":
    sys.exit(main())
