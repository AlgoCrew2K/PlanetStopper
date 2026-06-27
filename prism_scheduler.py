"""
prism_scheduler.py — Market Prism Phase 4 nightly scheduler wrapper.

Invoked by Windows Task Scheduler daily at 03:00 local time via schedule_prism.ps1.
Implements Option B (OS-level scheduling, daemon-decoupled) per DE-PRISM-004.

Responsibilities:
  1. Load .env from the project root (credentials for Claude + DB)
  2. Idempotency guard: skip if today's MARKET_PRISM row already exists in the DB
  3. Spawn all 6 Prism agents (prism-synthesizer + 5 analysts) via headless vanilla `claude -p` subprocess  # noqa: E501  # un-wrappable long line
  4. Bounded retry: up to MAX_ATTEMPTS attempts with capped exponential backoff
  5. D-1 error contract: only type(exc).__name__ is logged — never raw messages or paths

Exit codes:
  0  — success (either today's row already existed, or subprocess completed successfully)
  1  — failure (all MAX_ATTEMPTS exhausted without a successful run)
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

MAX_ATTEMPTS: int = 3  # Max subprocess invocations per run
BACKOFF_BASE_SECONDS: int = 30  # First retry wait
BACKOFF_CAP_SECONDS: int = 60  # Maximum wait between retries
# A full 6-agent Opus council (synthesizer + 5 analysts, multi-round Q&A +
# conditional debate) realistically costs $5-10 per run. 15.0 is a
# runaway-prevention ceiling, not a target.
MAX_BUDGET_USD: float = 15.0
_STDERR_LOG_CAP: int = 4000  # chars of stderr tail logged on council failure (fits a full traceback, bounded for journald)
_STDOUT_LOG_CAP: int = (
    2000  # chars of stdout tail logged on council failure (council JSON error payload)
)

# Prompt passed to the vanilla-primary headless claude session. The primary
# spawns ALL 6 agents itself — prism-synthesizer has no Agent/spawn tool and
# cannot spawn teammates.
#
# Council 5/5 reliability directives (DE-PRISM-5OF5):
#   (a) Generate the run_id BEFORE spawning so it can be embedded in each
#       analyst's spawn prompt.
#   (b) Embed the run_id + immediate-initial_read instruction in each analyst's
#       spawn prompt (do NOT spawn then send a kickoff via SendMessage — that
#       path caused 2/5 participation when dormant agents missed the message).
#   (c) Capture each analyst's agentId at spawn; pass agentIds to the
#       synthesizer so it can address analysts by agentId (not canonical name)
#       during Q&A and debate.
#   (d) Wait-barrier: synthesizer must not synthesize until 5 initial_read rows
#       appear in the audit DB (or the barrier times out — graceful limited-inputs).
# Static preamble — does NOT instruct the council to mint its own run_id.
# _run_prism() appends the scheduler-generated run_id at call time so the
# council uses exactly the uuid4 that _persist_spend will log against.
PRISM_RUN_PROMPT: str = (
    "You are running the Market Prism nightly council. "
    "Step 1: Use the run_id provided at the end of this prompt — do NOT generate "
    "a new run_id; use exactly the string given. "
    "Step 2: Spawn all 6 agents: prism-synthesizer (team lead), "
    "prism-technicals-analyst, prism-sentiment-analyst, "
    "prism-derivatives-analyst, prism-macro-analyst, "
    "prism-fundamentals-analyst. "
    "For each of the 5 analyst agents, include the run_id and an instruction to produce "
    "and file their initial_read IMMEDIATELY on their first turn in the spawn prompt itself "
    "(embed it — do NOT rely on a subsequent SendMessage kickoff, which is unreliable for "
    "dormant agents). "
    "Capture each analyst's agentId at spawn. "
    "Step 3: Spawn prism-synthesizer with: the run_id, the list of all 5 analyst agentIds "
    "(so it can address analysts by agentId, not canonical name), and an instruction to "
    "wait until 5 initial_read rows are present in the audit DB before synthesizing "
    "(the audit-DB wait-barrier — query the DB directly, never rely on SendMessage inbox alone). "
    "prism-synthesizer coordinates all five analysts: it queries the audit DB for "
    "initial_read rows, runs Q&A and conditional debate (up to 3 rounds), integrates all "
    "outputs into ONE MARKET_PRISM observation, and writes it to the DB via prism_audit_write. "
    "Do not return your final answer until the MARKET_PRISM row has been written to the DB. "
    "This is fully unattended — complete the entire run without waiting for any user input."
)

# Project root: the directory containing this script
_PROJECT_ROOT: Path = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Dependency wrappers (patchable in tests)
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Load .env from the project root into os.environ using python-dotenv."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)
    except Exception:  # noqa: BLE001
        # dotenv missing or unreadable — silent fallback (no logging, to avoid
        # echoing env paths; stricter than D-1), continue with current env
        pass


def _get_summary() -> dict | None:
    """Return the most recent MARKET_PRISM summary row, or None."""
    sys.path.insert(0, str(_PROJECT_ROOT))
    import database  # noqa: PLC0415

    return database.get_latest_market_prism_summary()


def _is_todays_row(row: dict | None) -> bool:
    """Return True if row's created_at date equals today UTC."""
    if row is None:
        return False
    created_at_str = row.get("created_at", "")
    try:
        # created_at is stored as "YYYY-MM-DD HH:MM:SS" (UTC)
        row_date = datetime.strptime(created_at_str[:10], "%Y-%m-%d").date()
        today = datetime.now(UTC).date()
        return row_date == today
    except Exception:  # noqa: BLE001
        return False


def _persist_spend(run_id: str, stdout: str) -> None:
    """Parse subprocess JSON stdout for total_cost_usd and persist to prism_audit_log.

    Reads total_cost_usd (CC 2.1.181+ envelope) with a tolerant fallback to the
    legacy cost_usd key so older/local CC builds still log.

    Non-fatal — a parse or DB failure is logged as type-only (D-1) and swallowed.
    """
    try:
        parsed = json.loads(stdout)
        cost = parsed.get("total_cost_usd") or parsed.get("cost_usd")
        if cost is not None:
            sys.path.insert(0, str(_PROJECT_ROOT))
            import database  # noqa: PLC0415

            database.insert_prism_audit_entry(
                run_id=run_id,
                agent_role="LAUNCHER",
                phase="spend_log",
                content=json.dumps({"total_cost_usd": cost}),
            )
    except Exception as exc:  # noqa: BLE001
        # D-1: log type only — never exc message or path
        print(f"[prism_scheduler] SpendLogError: {type(exc).__name__}", file=sys.stderr)


def _get_market_prism_row_for_run(run_id: str) -> dict | None:
    """Return the MARKET_PRISM advisor_observations row for this run_id, or None.

    Queries advisor_observations for a row with advisor_role='MARKET_PRISM' whose
    raw_response contains a matching run_id.  Used by main() to verify the council
    actually produced an observation before declaring success.

    Non-fatal — returns None on any DB error (D-1: logs type-only to stderr).
    """
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        import database  # noqa: PLC0415

        # get_latest_market_prism_summary returns the most recent MARKET_PRISM row.
        # Since run_id is unique per nightly, the latest row is this run's row if
        # it was written.  Confirm by checking raw_response.run_id matches.
        row = database.get_latest_market_prism_summary()
        if row is None:
            return None
        raw = row.get("raw_response") or {}
        if isinstance(raw, str):
            import json as _json  # noqa: PLC0415

            raw = _json.loads(raw)
        if raw.get("run_id") == run_id:
            return row
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[prism_scheduler] RowCheckError: {type(exc).__name__}", file=sys.stderr)
        return None


def _redact_secrets(text: str, secret_values: list[str]) -> str:
    """Replace known credential values and token-shaped patterns with ***REDACTED***.

    Applies literal-value replacement first (all occurrences of each non-empty
    secret), then three shape-regex patterns as defense-in-depth for tokens that
    may not appear in secret_values (e.g. a rotated credential still in output).

    Empty strings in secret_values are skipped — str.replace("", ...) inserts
    a marker between every character and corrupts the string.

    Pure — no I/O. The caller (_run_prism) wraps in try/except.
    """
    for value in secret_values:
        if value:  # skip empty strings
            text = text.replace(value, "***REDACTED***")
    _SHAPE_PATTERNS = (
        re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),  # Anthropic API key shape
        re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # generic secret-key shape
        re.compile(r"oat_[A-Za-z0-9_-]{8,}"),  # Claude OAuth token shape
    )
    for pattern in _SHAPE_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text


def _run_prism(run_id: str = "unknown") -> bool:
    """
    Invoke the 6-agent Prism council via a vanilla headless `claude -p` subprocess.

    The primary spawns all 6 agents (prism-synthesizer + 5 analysts); prism-synthesizer
    coordinates the pre-spawned analysts via SendMessage, integrates outputs, and writes
    the MARKET_PRISM row. Returns True on success (returncode == 0), False on failure.
    Raises no exceptions — all errors are logged as type-only (D-1).
    """
    # Build the prompt dynamically so the scheduler-generated run_id is the
    # single authoritative id for all audit rows and _persist_spend logging.
    prompt = (
        PRISM_RUN_PROMPT + f" The run_id for this session is: {run_id}."
        " Use this exact string as the run_id for ALL audit rows and the"
        " MARKET_PRISM observation. Do not generate a new run_id."
    )
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--model",
        "claude-opus-4-8",
        "--max-budget-usd",
        str(MAX_BUDGET_USD),
        "--output-format",
        "json",
        prompt,
    ]
    try:
        _council_env = os.environ.copy()
        _council_env.pop(
            "ANTHROPIC_API_KEY", None
        )  # council uses the Claude subscription (CLAUDE_CODE_OAUTH_TOKEN), not the metered API key
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=_council_env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _persist_spend(run_id, result.stdout)
        else:
            try:
                # a) Build secret values from the env actually passed to the subprocess
                secret_values = [
                    v
                    for v in (
                        _council_env.get("CLAUDE_CODE_OAUTH_TOKEN"),
                        os.environ.get("ANTHROPIC_API_KEY"),
                    )
                    if v
                ]
                # b) Redact both output channels
                safe_stderr = _redact_secrets(result.stderr or "", secret_values)
                safe_stdout = _redact_secrets(result.stdout or "", secret_values)

                # c) Tail-truncate with marker
                def _tail(text: str, cap: int, label: str) -> str:
                    if not text:
                        return f"({label} empty)"
                    if len(text) <= cap:
                        return text
                    return f"...[truncated, showing last {cap}]...\n{text[-cap:]}"

                # d) Print the diagnostic block to stderr so journald captures it
                print(
                    f"[prism_scheduler] Council subprocess failed:"
                    f" returncode={result.returncode}\n"
                    f"  stderr: {_tail(safe_stderr, _STDERR_LOG_CAP, 'stderr')}\n"
                    f"  stdout: {_tail(safe_stdout, _STDOUT_LOG_CAP, 'stdout')}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001
                # AC-7: diagnostic suppressed — never propagate; return value is preserved
                print(
                    f"[prism_scheduler] (diagnostic suppressed: {type(exc).__name__})",
                    file=sys.stderr,
                )
        return result.returncode == 0
    except Exception as exc:  # noqa: BLE001
        # D-1: log type only — never exc message or path
        print(f"[prism_scheduler] SubprocessError: {type(exc).__name__}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Post-council provenance patch
# ---------------------------------------------------------------------------


def _patch_provenance(run_id: str, row: "dict | None") -> bool:
    """Post-council patch: rebuild per-lens validated article_corpus citations
    and INSERT a new MARKET_PRISM_SOURCES advisor_observations row (v2).

    Reuses ai_advisor._build_*_section + build_citation — no reinvented citation
    logic.  For each url-bearing lens (sentiment, macro, derivatives, fundamentals)
    collects citations from the union of section["sources"] and
    section["article_corpus"] (sentiment returns both; others return sources only),
    validates each via build_citation, and accumulates into a SEPARATE
    sources_per_lens_digest dict.  The MARKET_PRISM row is NEVER modified (v2).

    technicals is intentionally excluded: Alpaca bar data has no public urls (AC-2).

    D-1 never-raises; AC-4: does not gate or prevent sys.exit(0) in main().
    Returns True if the patch was attempted and persisted, False on no-op/error.
    """
    try:
        if row is None:
            return False
        raw = row.get("raw_response") or {}
        if isinstance(raw, str):
            try:
                import json as _json  # noqa: PLC0415

                raw = _json.loads(raw)
            except Exception:  # noqa: BLE001
                return False
        pld = raw.get("per_lens_digest")
        if not isinstance(pld, dict):
            return False

        import ai_advisor  # noqa: PLC0415

        # technicals intentionally absent — AC-2: Alpaca bar data has no public urls.
        _BUILDERS: dict = {
            "sentiment": ai_advisor._build_sentiment_section,
            "macro": ai_advisor._build_macro_section,
            "derivatives": ai_advisor._build_derivatives_section,
            "fundamentals": ai_advisor._build_fundamentals_section,
        }

        # Accumulate per-lens citations into a SEPARATE dict — never mutate the
        # MARKET_PRISM row (v2: INSERT-only; the MARKET_PRISM raw_response is
        # byte-unchanged after this function returns).
        sources_per_lens_digest: dict = {}

        for lens, builder in _BUILDERS.items():
            if lens not in pld:
                continue
            try:
                section = builder()
            except Exception:  # noqa: BLE001
                continue  # D-1: this lens contributes no citations

            # Union sources + article_corpus so sentiment's primary corpus is captured.
            # sources items are already citation-shaped; article_corpus items are raw
            # corpus dicts (title/url/published, no lens key) — inject lens before
            # passing to build_citation.
            candidates: list[dict] = list(section.get("sources") or [])
            for art in section.get("article_corpus") or []:
                if isinstance(art, dict) and "lens" not in art:
                    art = {**art, "lens": lens}
                candidates.append(art)

            # Dedup by url (first occurrence wins) — sentiment puts the same articles
            # in both sources (citation-shaped) and article_corpus (raw dicts), so the
            # union would otherwise double every entry.
            seen_urls: set[str] = set()
            deduped: list[dict] = []
            for c in candidates:
                url = c.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    deduped.append(c)
            candidates = deduped

            valid = [c for c in (ai_advisor.build_citation(s) for s in candidates) if c is not None]
            if valid:
                sources_per_lens_digest[lens] = {"article_corpus": valid}

        import database as _db  # noqa: PLC0415

        # AC-6 idempotency: if a SOURCES row already exists for this run_id, skip INSERT.
        existing = _db.get_latest_market_prism_sources_for_run(run_id)
        if existing is not None:
            return True  # already patched

        # v2: INSERT an append-only MARKET_PRISM_SOURCES row — never modify MARKET_PRISM.
        # NOTE: do NOT add "MARKET_PRISM_SOURCES" to app.py's _ADVISOR_ROLES list —
        # the Overview observations loop (app.py:3604) and _preview_text stamp
        # (app.py:3633) would treat SOURCES rows as normal observations.
        _db.insert_advisor_observation(
            advisor_role="MARKET_PRISM_SOURCES",
            subject_type="portfolio",
            subject_id="global",
            verdict=None,
            raw_response={"run_id": run_id, "per_lens_digest": sources_per_lens_digest},
            symphony_id="",
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(
            f"[prism_scheduler] PatchProvenanceError: {type(exc).__name__}",
            file=sys.stderr,
        )
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Market Prism nightly scheduler. Exits 0 on success, 1 on failure."""
    _load_env()

    # --- Idempotency guard ---
    try:
        summary = _get_summary()
    except Exception as exc:  # noqa: BLE001
        print(f"[prism_scheduler] DB check failed: {type(exc).__name__}", file=sys.stderr)
        summary = None  # treat as no row — attempt the run

    if _is_todays_row(summary):
        run_id = summary.get("raw_response", {}).get("run_id", "unknown") if summary else "unknown"
        print(f"[prism_scheduler] Already ran today (run_id: {run_id}). Skipping.")
        sys.exit(0)

    run_id = str(uuid.uuid4())

    # --- Bounded retry loop ---
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[prism_scheduler] Attempt {attempt}/{MAX_ATTEMPTS}")
        proc_ok = _run_prism(run_id=run_id)
        if proc_ok:
            row = _get_market_prism_row_for_run(run_id)
            if row is not None:
                print("[prism_scheduler] Run completed successfully.")
                _patch_provenance(run_id, row)  # AC-4: D-1 never-raises; does not gate sys.exit(0)
                sys.exit(0)
            # rc==0 but no row — council ran but wrote nothing.
            # Treat as a failed attempt; the retry loop continues.
            print(
                f"[prism_scheduler] Attempt {attempt}: subprocess exited 0 but "
                "no MARKET_PRISM row found for run_id — treating as failed.",
                file=sys.stderr,
            )

        # Failure — log attempt and backoff before next try
        print(
            f"[prism_scheduler] Attempt {attempt} failed (SubprocessError). "
            f"{'Retrying...' if attempt < MAX_ATTEMPTS else 'Max attempts exhausted.'}",
            file=sys.stderr,
        )
        if attempt < MAX_ATTEMPTS:
            wait = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)
            time.sleep(wait)

    # All attempts exhausted
    sys.exit(1)


if __name__ == "__main__":
    main()
