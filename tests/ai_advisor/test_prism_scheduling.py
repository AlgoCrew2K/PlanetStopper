"""
Tests for prism_scheduler.py — Market Prism Phase 4 nightly scheduler wrapper.

Covers:
  AC-1  Idempotency: today's row exists → no subprocess call, exit 0
  AC-2  No today's row → subprocess invoked with correct args
  AC-3  Bounded retry on subprocess failure → MAX_ATTEMPTS calls, finite backoff, exit non-zero
  AC-4  Retry succeeds on 2nd attempt → subprocess called twice, exit 0
  AC-5  Yesterday's row does NOT trigger idempotency (today still runs)
  AC-6  MAX_ATTEMPTS is a finite named constant (1–5)
  AC-7  Backoff cap enforced (sleep values ≤ BACKOFF_CAP_SECONDS)
  AC-8  API key is NOT echoed to any log or subprocess arg

Hardening additions (Phase 4 gap closure — RED before GREEN):
  HC-1  Spend cap: --max-budget-usd <MAX_BUDGET_USD> in the claude command (named constant)
  HC-2  Spend logging: --output-format json in cmd; prism_audit_log row with phase='spend_log'
        persisted after a successful run (shape/presence, not value)
  HC-3  Model pin: cmd contains 'claude-opus-4-8' (pinned), NOT bare 'opus' (alias)
"""

import importlib
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_row(offset_days: int = 0) -> dict:
    """Return a fake MARKET_PRISM summary row whose created_at is today+offset_days UTC."""
    ts = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return {
        "id": 68 - offset_days,
        "verdict": "limited-inputs",
        "created_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "advisor_role": "MARKET_PRISM",
        "raw_response": {"run_id": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")},
    }


def _import_scheduler():
    """Import (or reimport) prism_scheduler fresh each call."""
    if "prism_scheduler" in sys.modules:
        del sys.modules["prism_scheduler"]
    # The scheduler lives at the project root which is on sys.path in the worktree env;
    # if not, add the worktree root explicitly.
    import os
    worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if worktree not in sys.path:
        sys.path.insert(0, worktree)
    import prism_scheduler  # noqa: PLC0415
    return prism_scheduler


# ---------------------------------------------------------------------------
# AC-6 — named constant sanity (import-level, no mocking needed)
# ---------------------------------------------------------------------------

class TestConstants:
    def test_max_attempts_is_named_constant(self):
        mod = _import_scheduler()
        assert hasattr(mod, "MAX_ATTEMPTS"), "prism_scheduler must export MAX_ATTEMPTS"
        assert isinstance(mod.MAX_ATTEMPTS, int), "MAX_ATTEMPTS must be an int"
        assert 1 <= mod.MAX_ATTEMPTS <= 5, "MAX_ATTEMPTS must be between 1 and 5"

    def test_backoff_constants_are_named(self):
        mod = _import_scheduler()
        assert hasattr(mod, "BACKOFF_BASE_SECONDS"), "must export BACKOFF_BASE_SECONDS"
        assert hasattr(mod, "BACKOFF_CAP_SECONDS"), "must export BACKOFF_CAP_SECONDS"
        assert mod.BACKOFF_BASE_SECONDS > 0
        assert mod.BACKOFF_CAP_SECONDS >= mod.BACKOFF_BASE_SECONDS

    def test_backoff_cap_is_finite(self):
        mod = _import_scheduler()
        assert mod.BACKOFF_CAP_SECONDS <= 300, "Backoff cap must be finite and reasonable (≤5min)"


# ---------------------------------------------------------------------------
# AC-1 — Idempotency: today's row exists → no subprocess, exit 0
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_today_row_skips_subprocess(self):
        """If a MARKET_PRISM row exists for today UTC, subprocess is never called."""
        mod = _import_scheduler()
        today_row = _today_row(0)

        with (
            patch.object(mod, "_get_summary", return_value=today_row),
            patch("subprocess.run") as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_not_called()
        assert exc_info.value.code == 0, "Should exit 0 when today's row already exists"

    def test_today_row_detection_uses_utc(self):
        """created_at comparison must use UTC date, not local time."""
        mod = _import_scheduler()
        # Create a row with an explicit UTC datetime string
        utc_now = datetime.now(timezone.utc)
        row = {
            "id": 99,
            "verdict": "limited-inputs",
            "created_at": utc_now.strftime("%Y-%m-%d %H:%M:%S"),
            "advisor_role": "MARKET_PRISM",
            "raw_response": {},
        }

        with (
            patch.object(mod, "_get_summary", return_value=row),
            patch("subprocess.run") as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_not_called()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# AC-2 — No today's row → subprocess invoked with correct args
# ---------------------------------------------------------------------------

# Corrected vanilla-primary cmd shape — NO --agent pin.
# The original had "--agent"/"prism-synthesizer" which encoded the defect:
# a pinned agent session has no Agent/spawn tool and cannot orchestrate 6 analysts.
EXPECTED_CLAUDE_ARGS = [
    "claude",
    "-p",
    "--dangerously-skip-permissions",
    "--model",
    "claude-opus-4-8",
    "--output-format",
    "json",
    "--max-budget-usd",
]


class TestSubprocessInvocation:
    def test_no_row_invokes_claude_subprocess(self):
        """When no today's row exists, subprocess.run is called with correct vanilla-primary args.

        RED intent (defect fix): the original _run_prism had '--agent'/'prism-synthesizer' in
        the cmd list, which caused the headless session to inherit only the pinned agent's tools
        (no Agent/spawn tool) — so the council was never run and 0 rows were written.
        This test now asserts the CORRECTED vanilla-primary shape: no '--agent' pin, all
        required vanilla-primary flags present.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        args_used = mock_run.call_args[0][0]  # first positional arg = the cmd list

        # All corrected vanilla-primary args must be present.
        for expected in EXPECTED_CLAUDE_ARGS:
            assert expected in args_used, (
                f"Expected '{expected}' in subprocess args but it was missing. "
                f"Full cmd: {args_used}"
            )

        # The '--agent' flag must NOT be present — it was the defect.
        assert "--agent" not in args_used, (
            f"'--agent' flag must NOT appear in the subprocess cmd (it pins the session "
            f"to one agent's tools, preventing the council from spawning). "
            f"Full cmd: {args_used}"
        )

        assert exc_info.value.code == 0

    def test_subprocess_cwd_is_project_root(self):
        """subprocess.run must use the project root as cwd, not the test cwd."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        kwargs = mock_run.call_args[1]
        assert "cwd" in kwargs, "subprocess.run must set cwd explicitly"
        import os
        assert os.path.isabs(kwargs["cwd"]), "cwd must be an absolute path"

    def test_subprocess_not_shell_true(self):
        """shell=True would be a security risk — must not be set."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        kwargs = mock_run.call_args[1]
        assert not kwargs.get("shell", False), "shell=True is not allowed — security risk"

    def test_api_key_not_in_subprocess_args(self):
        """ANTHROPIC_API_KEY must not appear in the subprocess args list."""
        import os
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-TEST-SENTINEL-VALUE"
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        try:
            with (
                patch.object(mod, "_get_summary", return_value=None),
                patch("subprocess.run", return_value=mock_result) as mock_run,
                pytest.raises(SystemExit),
            ):
                mod.main()
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

        args_used = mock_run.call_args[0][0]
        for arg in args_used:
            assert "sk-ant" not in str(arg), "API key must not appear in subprocess args"


# ---------------------------------------------------------------------------
# Vanilla-primary shape contract (defect fix — no --agent pin)
# ---------------------------------------------------------------------------
# These tests pin the corrected _run_prism cmd shape.  They are RED against
# the current (broken) prism_scheduler.py, which still has "--agent" in the cmd
# and lacks the PRISM_RUN_PROMPT module-level constant.
# ---------------------------------------------------------------------------

class TestVanillaPrimaryShape:
    def test_cmd_contains_no_agent_flag(self):
        """The subprocess cmd list must NOT contain '--agent'.

        '--agent <name>' pins the headless session to that agent's tools list.
        The pinned prism-synthesizer agent has no Agent/spawn tool, so it
        cannot orchestrate the 5 analyst agents — the council is never run
        and no MARKET_PRISM row is written.

        RED intent: current _run_prism has '--agent' in the cmd → this fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        assert "--agent" not in args_used, (
            "The subprocess cmd MUST NOT contain '--agent'. "
            "An --agent pin restricts tools to that agent's list, preventing "
            "the vanilla primary session from spawning the 6-agent council. "
            f"Full cmd: {args_used}"
        )

    def test_cmd_is_vanilla_primary_without_agent_pin(self):
        """The subprocess cmd must match the corrected vanilla-primary shape exactly.

        Required flags (order-independent):
          claude -p --dangerously-skip-permissions
          --model claude-opus-4-8
          --output-format json
          --max-budget-usd <value>

        RED intent: current _run_prism has '--agent'/'prism-synthesizer' and
        the PRISM_RUN_PROMPT prompt constant is absent → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]

        required = [
            "claude",
            "-p",
            "--dangerously-skip-permissions",
            "--model",
            "claude-opus-4-8",
            "--output-format",
            "json",
            "--max-budget-usd",
        ]
        for flag in required:
            assert flag in args_used, (
                f"Required vanilla-primary flag '{flag}' is missing from cmd. "
                f"Full cmd: {args_used}"
            )

        # '--agent' and 'prism-synthesizer' as a flag value must be absent.
        assert "--agent" not in args_used, (
            f"'--agent' must not appear in the vanilla-primary cmd. Full cmd: {args_used}"
        )
        # 'prism-synthesizer' as a standalone arg (flag value) must not appear.
        # It may still appear inside PRISM_RUN_PROMPT text (that is intentional),
        # but NOT as a separate list element acting as an --agent value.
        if "--agent" not in args_used:
            # Guard: if --agent is gone, prism-synthesizer as bare arg should not be there.
            bare_idx = [i for i, a in enumerate(args_used) if a == "prism-synthesizer"]
            assert not bare_idx, (
                f"'prism-synthesizer' appears as a bare cmd element at index(es) {bare_idx}. "
                f"Full cmd: {args_used}"
            )

    def test_prism_run_prompt_names_all_six_agent_types(self):
        """prism_scheduler must export PRISM_RUN_PROMPT, a module-level string constant
        that names all 6 agent types so the vanilla primary session knows who to spawn.

        Required agent type strings (all must appear in the prompt):
          prism-synthesizer          (team lead, generates run_id, integrates council)
          prism-technicals-analyst
          prism-sentiment-analyst
          prism-derivatives-analyst
          prism-macro-analyst
          prism-fundamentals-analyst

        RED intent: PRISM_RUN_PROMPT constant does not exist in current
        prism_scheduler.py → AttributeError.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "prism_scheduler must export PRISM_RUN_PROMPT — a module-level string "
            "constant containing the orchestration prompt passed as the last cmd element. "
            "This constant does not exist in the current implementation."
        )

        prompt = mod.PRISM_RUN_PROMPT
        assert isinstance(prompt, str) and len(prompt) > 0, (
            "PRISM_RUN_PROMPT must be a non-empty string"
        )

        required_agents = [
            "prism-synthesizer",
            "prism-technicals-analyst",
            "prism-sentiment-analyst",
            "prism-derivatives-analyst",
            "prism-macro-analyst",
            "prism-fundamentals-analyst",
        ]
        for agent_name in required_agents:
            assert agent_name in prompt, (
                f"PRISM_RUN_PROMPT must name '{agent_name}' so the vanilla primary "
                f"session knows to spawn it. Missing from current prompt."
            )

    def test_prism_run_prompt_contains_completion_guard(self):
        """PRISM_RUN_PROMPT must include a completion guard phrase instructing the
        session not to return until the MARKET_PRISM row is written to the DB.

        Without this guard, the headless session may exit rc=0 before the synthesizer
        writes the row — reproducing the exact failure mode from the live run
        (cost $1.25, produced 0 council output).

        The guard must contain (case-insensitive):
          - 'do not return' (the directive)
          - at least one of: 'written', 'DB', 'row' (the completion criterion)

        RED intent: PRISM_RUN_PROMPT does not exist yet → AttributeError.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "PRISM_RUN_PROMPT constant is missing — cannot check completion guard."
        )

        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        assert "do not return" in prompt_lower, (
            "PRISM_RUN_PROMPT must contain the completion guard phrase 'do not return' "
            "(case-insensitive) to prevent premature exit before the row is written."
        )

        completion_keywords = ["written", "db", "row"]
        assert any(kw in prompt_lower for kw in completion_keywords), (
            f"PRISM_RUN_PROMPT completion guard must reference the DB write outcome — "
            f"at least one of {completion_keywords} must appear (case-insensitive). "
            f"The guard must say something like 'do not return until the row is written'."
        )

    def test_prism_run_prompt_is_last_cmd_element(self):
        """The PRISM_RUN_PROMPT constant must be the last element in the subprocess cmd list.

        The claude CLI treats the final positional arg as the prompt to execute.
        Putting the prompt anywhere other than last means it is ignored or misinterpreted.

        RED intent: current _run_prism uses a hardcoded literal string as the last
        element, not the PRISM_RUN_PROMPT constant → the assertion on the constant
        fails (AttributeError before reaching the cmd check).
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "PRISM_RUN_PROMPT constant missing — cannot verify it is last in cmd."
        )

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        assert args_used[-1] == mod.PRISM_RUN_PROMPT, (
            f"The last cmd element must be PRISM_RUN_PROMPT. "
            f"Got: {args_used[-1]!r}. "
            f"The prompt must be the final positional arg to the claude CLI."
        )

    def test_prism_run_prompt_instructs_spawning_not_impersonation(self):
        """PRISM_RUN_PROMPT must instruct the primary session to SPAWN agents,
        not to 'act as' or 'impersonate' them inline.

        The vanilla primary has the Agent/spawn tool precisely so it can launch
        each analyst as a real sub-agent that inherits their .claude/agents/<name>.md
        system prompt (tools list, role contract, DB credentials, etc.).
        Saying 'act as prism-synthesizer' bypasses that agent definition entirely —
        the session has no access to the synthesizer's system prompt and no
        prism_audit_write tool unless explicitly provided.

        The prompt must use spawn-directive language: at least one of
        'spawn', 'launch', 'run', 'start' must appear (case-insensitive) in the
        context of the agent names — verified here by checking that at least one
        spawn-verb appears anywhere in the prompt.

        RED intent: current PRISM_RUN_PROMPT says 'act as prism-synthesizer' without
        a spawn directive for the synthesizer itself → this test FAILS because the
        primary is told to act-as rather than spawn-and-coordinate.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "PRISM_RUN_PROMPT constant missing — cannot verify spawn directive."
        )

        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        spawn_verbs = ["spawn", "launch", "run", "start"]
        assert any(v in prompt_lower for v in spawn_verbs), (
            f"PRISM_RUN_PROMPT must use spawn-directive language (one of {spawn_verbs}) "
            "to instruct the vanilla primary to spawn sub-agents. "
            "Saying 'act as prism-synthesizer' bypasses the agent's system prompt "
            "and tools list — the council is never actually run as defined agents."
        )

        # Stronger check: 'act as' followed immediately by 'prism-synthesizer' is the
        # impersonation anti-pattern — it means the primary pretends to be the synthesizer
        # instead of spawning it. Flag this specific pattern.
        import re
        # Match 'act as' with optional whitespace then 'prism-synthesizer'
        impersonation = re.search(r"act\s+as\s+prism-synthesizer", prompt_lower)
        assert impersonation is None, (
            "PRISM_RUN_PROMPT contains 'act as prism-synthesizer' — this is the "
            "impersonation anti-pattern. The primary must SPAWN prism-synthesizer "
            "as a real agent (so it gets its system prompt + tools), not impersonate it. "
            "Rewrite the prompt to say e.g. 'spawn prism-synthesizer as the team lead'."
        )

    def test_prism_run_prompt_primary_spawns_all_six_not_synthesizer(self):
        """The PRIMARY session must spawn ALL 6 agents (synthesizer + 5 analysts).
        The synthesizer must NOT be told to spawn/launch the analysts.

        Constraint source: prism-synthesizer.md tools list has NO 'Agent' tool
        (tools: Read, Glob, Grep, Bash, Write, SendMessage, TaskCreate, TaskUpdate,
        TaskList, TaskGet). Teammates cannot spawn their own teammates in Claude Code
        agent teams — nested spawning is not supported. The synthesizer CAN only
        message the already-spawned analysts via SendMessage (its step 3 says
        "Send each analyst a kickoff MESSAGE via SendMessage", never "spawn").

        Correct semantics:
          - PRIMARY spawns all 6: prism-synthesizer + 5 analysts
          - prism-synthesizer COORDINATES the pre-spawned analysts (messages them)

        Wrong semantics (current prompt re-introduces original bug):
          "prism-synthesizer will spawn the 5 analyst agents" — synthesizer has
          no Agent tool, so analysts are never spawned → 0 council output.

        Two assertions:
        (a) The prompt must contain a pattern showing the PRIMARY spawns all 5
            analyst agent names (i.e. each analyst name appears near a spawn-verb
            somewhere in the prompt — verified by checking all 5 analyst names
            appear AND a spawn-verb appears before any analyst name).
        (b) The prompt must NOT contain a pattern delegating spawning to the
            synthesizer: no match for
            'prism-synthesizer.*spawn' or 'synthesizer.*spawn.*analyst'
            (case-insensitive, dotall).

        RED intent: current prompt says 'prism-synthesizer will spawn the 5 analyst
        agents' → assertion (b) fails.
        """
        import re

        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "PRISM_RUN_PROMPT constant missing — cannot verify spawn semantics."
        )

        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        # (a) All 5 analyst names must appear in the prompt (primary spawns them).
        analyst_names = [
            "prism-technicals-analyst",
            "prism-sentiment-analyst",
            "prism-derivatives-analyst",
            "prism-macro-analyst",
            "prism-fundamentals-analyst",
        ]
        for name in analyst_names:
            assert name in prompt_lower, (
                f"PRISM_RUN_PROMPT must name '{name}' so the PRIMARY can spawn it. "
                f"Missing from current prompt."
            )

        # (b) The prompt must NOT delegate analyst-spawning to prism-synthesizer.
        # Pattern: 'prism-synthesizer' followed (anywhere after) by a spawn-verb
        # followed (anywhere after) by 'analyst' — this is the delegation anti-pattern.
        delegation_pattern = re.search(
            r"prism-synthesizer.{0,120}(spawn|launch).{0,120}analyst",
            prompt_lower,
            re.DOTALL,
        )
        matched_text = delegation_pattern.group() if delegation_pattern else ""
        assert delegation_pattern is None, (
            f"PRISM_RUN_PROMPT delegates analyst-spawning to prism-synthesizer "
            f"(matched: {matched_text!r}). "
            "prism-synthesizer has NO Agent/spawn tool — it can only MESSAGE "
            "pre-spawned analysts via SendMessage. "
            "The PRIMARY session must spawn all 6 agents itself; "
            "prism-synthesizer then coordinates (messages) the pre-spawned analysts."
        )

        # Additional guard: 'synthesizer will spawn' is the exact broken phrase.
        broken_phrase = re.search(
            r"synthesizer\s+will\s+spawn",
            prompt_lower,
        )
        assert broken_phrase is None, (
            "PRISM_RUN_PROMPT contains 'synthesizer will spawn' — "
            "the synthesizer has no Agent tool and cannot spawn sub-agents. "
            "Remove this phrase; the PRIMARY spawns all 6 agents."
        )


# ---------------------------------------------------------------------------
# AC-3 — Bounded retry on persistent subprocess failure
# ---------------------------------------------------------------------------

class TestBoundedRetry:
    def test_retries_exactly_max_attempts_times(self):
        """On persistent subprocess failure, retries exactly MAX_ATTEMPTS times."""
        mod = _import_scheduler()
        fail_result = MagicMock()
        fail_result.returncode = 1

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result) as mock_run,
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert mock_run.call_count == mod.MAX_ATTEMPTS, (
            f"Expected exactly {mod.MAX_ATTEMPTS} subprocess calls, got {mock_run.call_count}"
        )
        assert exc_info.value.code != 0, "Should exit non-zero after exhausting retries"

    def test_backoff_sleep_values_are_finite_and_capped(self):
        """Sleep durations must be finite and never exceed BACKOFF_CAP_SECONDS."""
        mod = _import_scheduler()
        fail_result = MagicMock()
        fail_result.returncode = 1
        sleep_calls = []

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result),
            patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)),
            pytest.raises(SystemExit),
        ):
            mod.main()

        assert len(sleep_calls) > 0, "Expected at least one sleep call on retry"
        for s in sleep_calls:
            assert s <= mod.BACKOFF_CAP_SECONDS, (
                f"Sleep duration {s}s exceeds cap {mod.BACKOFF_CAP_SECONDS}s"
            )
            assert s >= 0, "Sleep duration must be non-negative"

    def test_no_infinite_loop(self):
        """Confirm finite retry — the loop terminates."""
        mod = _import_scheduler()
        call_count = {"n": 0}

        def counting_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 10:
                raise AssertionError("subprocess.run called more than 10 times — infinite loop?")
            result = MagicMock()
            result.returncode = 1
            return result

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=counting_run),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert exc_info.value.code != 0
        assert call_count["n"] <= mod.MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# AC-4 — Retry succeeds on 2nd attempt
# ---------------------------------------------------------------------------

class TestRetrySuccess:
    def test_retry_succeeds_on_second_attempt(self):
        """First call fails, second succeeds → exit 0, called twice."""
        mod = _import_scheduler()
        results = [MagicMock(returncode=1), MagicMock(returncode=0)]

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=results) as mock_run,
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert mock_run.call_count == 2
        assert exc_info.value.code == 0, "Should exit 0 after successful retry"


# ---------------------------------------------------------------------------
# AC-5 — Yesterday's row does NOT trigger idempotency
# ---------------------------------------------------------------------------

class TestYesterdayRow:
    def test_yesterday_row_triggers_run(self):
        """A row from yesterday UTC is NOT today's row — subprocess must be called."""
        mod = _import_scheduler()
        yesterday_row = _today_row(offset_days=-1)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=yesterday_row),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        assert exc_info.value.code == 0

    def test_none_summary_triggers_run(self):
        """When get_latest_market_prism_summary returns None (no rows), subprocess is called."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# AC-7 — Backoff cap: sleep values must not exceed cap even with exponential growth
# ---------------------------------------------------------------------------

class TestBackoffCap:
    def test_exponential_backoff_capped(self):
        """With many retries (if MAX_ATTEMPTS were large), sleep never exceeds cap."""
        mod = _import_scheduler()
        sleep_calls = []
        fail_result = MagicMock(returncode=1)

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result),
            patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)),
            pytest.raises(SystemExit),
        ):
            mod.main()

        for s in sleep_calls:
            assert s <= mod.BACKOFF_CAP_SECONDS, (
                f"Backoff {s}s exceeds BACKOFF_CAP_SECONDS={mod.BACKOFF_CAP_SECONDS}"
            )


# ---------------------------------------------------------------------------
# AC-8 — D-1 contract: no raw exception text in outputs
# ---------------------------------------------------------------------------

class TestD1Contract:
    def test_exception_in_subprocess_does_not_propagate_raw(self):
        """If subprocess.run raises an exception, it must not propagate unhandled."""
        mod = _import_scheduler()

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=OSError("some internal path /secret/path")),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        # Should exit non-zero — not propagate the raw OSError
        assert exc_info.value.code != 0, "Should exit non-zero on subprocess exception"

    def test_get_summary_db_failure_does_not_leak_message_body(self, capsys):
        """D-1: when _get_summary() raises, only type(exc).__name__ appears in output.

        The fallback path (lines 113-117 of prism_scheduler.py) catches the exception
        and prints only type(exc).__name__. A bad implementation could print str(exc),
        leaking internal DB paths/messages. This test locks that contract.

        It also verifies the scheduler treats the DB failure as 'no row' and proceeds
        to attempt the run (exiting 0 on the mocked successful _run_prism call).
        """
        mod = _import_scheduler()

        # A message body that MUST NOT appear anywhere in stdout/stderr
        secret_body = "some/internal/secret/path"

        with (
            patch.object(
                mod,
                "_get_summary",
                side_effect=RuntimeError(secret_body),
            ),
            # _run_prism returns True so main() exits 0 — confirming DB failure is
            # treated as 'no row' and does NOT abort the run
            patch.object(mod, "_run_prism", return_value=True),
            # Suppress _load_env's dotenv import; it swallows exceptions, but patch
            # it here so the test is fully hermetic regardless of the environment
            patch.object(mod, "_load_env"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err

        # D-1 positive assertion: the exception type name IS surfaced (so operators
        # know *something* went wrong without leaking internals)
        assert "RuntimeError" in combined_output, (
            "Expected 'RuntimeError' (the type name) in scheduler output — "
            f"D-1 requires the type name to be logged. Got: {combined_output!r}"
        )

        # D-1 negative assertion: the raw message body MUST NOT appear
        assert secret_body not in combined_output, (
            f"Secret message body {secret_body!r} leaked into output — "
            "D-1 contract violated: only type(exc).__name__ may be surfaced"
        )

        # Behavioral assertion: DB failure is treated as 'no row' (not a crash),
        # so _run_prism is invoked and the scheduler exits 0 on success
        assert exc_info.value.code == 0, (
            "DB failure on idempotency check should be treated as 'no row' "
            "— run proceeds, exits 0 on successful _run_prism"
        )


# ---------------------------------------------------------------------------
# HC-1 — Spend cap: --max-budget-usd with named constant MAX_BUDGET_USD
# ---------------------------------------------------------------------------

class TestSpendCap:
    def test_max_budget_usd_constant_exists_and_is_positive(self):
        """MAX_BUDGET_USD must be a named positive float/int constant.

        A runaway Opus council run with no dollar cap is bounded only by retry
        count — unacceptable for a nightly unattended job.  The constant must
        be named so the PM can audit and adjust without hunting for a magic number.

        RED intent: MAX_BUDGET_USD does not exist in prism_scheduler → AttributeError.
        """
        mod = _import_scheduler()
        assert hasattr(mod, "MAX_BUDGET_USD"), (
            "MAX_BUDGET_USD constant is missing from prism_scheduler.py. "
            "A named spend cap is required — never a magic number."
        )
        val = mod.MAX_BUDGET_USD
        assert isinstance(val, (int, float)), "MAX_BUDGET_USD must be numeric"
        assert val > 0, f"MAX_BUDGET_USD must be positive, got {val}"

    def test_claude_command_includes_max_budget_usd_flag(self):
        """The claude subprocess command must include --max-budget-usd <MAX_BUDGET_USD>.

        Without this flag the nightly Opus run has no dollar ceiling — the only
        bound is retry count (3 × however long the council runs).

        RED intent: _run_prism() command list lacks --max-budget-usd → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        assert "--max-budget-usd" in args_used, (
            "--max-budget-usd is missing from the claude subprocess command. "
            f"Command was: {cmd_str}"
        )

        # The value after the flag must match the named constant (shape check).
        budget_idx = args_used.index("--max-budget-usd")
        assert budget_idx + 1 < len(args_used), (
            "--max-budget-usd must be followed by a numeric value"
        )
        budget_val = args_used[budget_idx + 1]
        try:
            budget_float = float(budget_val)
        except (ValueError, TypeError):
            pytest.fail(
                f"--max-budget-usd value must be numeric, got {budget_val!r}. "
                f"Command was: {cmd_str}"
            )
        # Value must match the named constant — not an independent magic number.
        assert budget_float == float(mod.MAX_BUDGET_USD), (
            f"--max-budget-usd value {budget_float} does not match "
            f"MAX_BUDGET_USD={mod.MAX_BUDGET_USD}. "
            "The command must use the named constant, not a magic number."
        )

    def test_max_budget_usd_equals_15(self):
        """MAX_BUDGET_USD must be raised to 15.0 to accommodate a full 6-agent Opus council.

        The original cap of 5.0 was set for the old prism-synthesizer solo run.
        A genuine 6-agent Opus council (synthesizer + 5 analysts, multi-round Q&A
        + conditional debate) realistically costs $5–10 per run.  A $5 ceiling would
        abort a successful council mid-run, producing 0 rows — the same outcome as
        the original defect.

        15.0 is a runaway-prevention ceiling, NOT a target — it is generous enough
        to let a legitimate council complete while still bounding a pathological runaway.

        RED intent: current MAX_BUDGET_USD == 5.0 → assertion fails.
        """
        mod = _import_scheduler()
        assert mod.MAX_BUDGET_USD == 15.0, (
            f"MAX_BUDGET_USD must be 15.0 (raised from 5.0 to accommodate the full "
            f"6-agent Opus council).  Current value: {mod.MAX_BUDGET_USD}. "
            "A $5 cap would abort a legitimate council run mid-flight."
        )


# ---------------------------------------------------------------------------
# HC-2 — Spend logging: --output-format json + prism_audit_log persistence
# ---------------------------------------------------------------------------

class TestSpendLogging:
    def test_claude_command_includes_output_format_json(self):
        """The claude command must include --output-format json so cost can be parsed.

        Without structured JSON output from the subprocess, there is no reliable
        way to parse the Opus spend (AC-4 contract).

        RED intent: _run_prism() cmd lacks --output-format json → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        assert "--output-format" in args_used, (
            "--output-format is missing from the claude command. "
            f"Command was: {cmd_str}"
        )
        fmt_idx = args_used.index("--output-format")
        assert fmt_idx + 1 < len(args_used), "--output-format must be followed by a value"
        assert args_used[fmt_idx + 1] == "json", (
            f"--output-format value must be 'json', got {args_used[fmt_idx + 1]!r}. "
            f"Command was: {cmd_str}"
        )

    def test_successful_run_persists_spend_log_audit_entry(self):
        """After a successful claude run, a prism_audit_log entry with phase='spend_log'
        must be persisted via database.insert_prism_audit_entry.

        Shape assertion only — we verify the entry exists with the correct phase and
        a positive float cost value.  We do NOT assert the exact dollar amount
        (that is a producer-computed value; see global feedback rule
        feedback_no_hardcoded_test_values).

        RED intent: _run_prism() does not call insert_prism_audit_entry → no row → fails.
        """
        import json as _json
        import sqlite3
        import sys
        import os

        # Ensure the project root (where database.py lives) is importable.
        mod = _import_scheduler()
        worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        import database as _db

        # Simulate subprocess returning a JSON payload with the REAL Claude Code envelope key.
        # Provenance: PM-captured from live `claude -p --output-format json` (CC 2.1.181):
        #   {"total_cost_usd": 0.0728568, "type": "result", ...}  — NO "cost_usd" key present.
        # The cost value is arbitrary — we assert shape (positive float), not the literal.
        simulated_cost = 1.23
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps({"total_cost_usd": simulated_cost})

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert exc_info.value.code == 0, "Expected exit 0 on successful run"

        # Verify the spend log entry was written to prism_audit_log.
        conn = sqlite3.connect(_db._db_file())
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM prism_audit_log WHERE phase = 'spend_log' ORDER BY id DESC LIMIT 5"
        )
        rows = cursor.fetchall()
        conn.close()

        assert rows, (
            "No prism_audit_log row with phase='spend_log' was written after a successful run. "
            "AC-4 requires per-run Opus spend to be persisted for PM observability."
        )

        # Parse the most recent spend log entry and verify it has a positive float cost.
        latest_content = rows[0][0]
        try:
            parsed = _json.loads(latest_content)
        except (_json.JSONDecodeError, TypeError):
            pytest.fail(
                f"spend_log content is not valid JSON: {latest_content!r}. "
                "The spend log must be a JSON object with a cost field."
            )

        # The persisted row must record the cost under the canonical key 'total_cost_usd'.
        # Provenance: PM-captured from live `claude -p --output-format json` (CC 2.1.181):
        #   {"total_cost_usd": 0.0728568, ...} — that is the real envelope key.
        cost_val = parsed.get("total_cost_usd")
        assert cost_val is not None, (
            f"spend_log JSON has no 'total_cost_usd' key. Got: {parsed!r}. "
            "The persisted entry must use the real Claude Code envelope key "
            "(PM-captured from live CC 2.1.181: 'total_cost_usd', NOT 'cost_usd')."
        )
        assert isinstance(cost_val, (int, float)) and cost_val > 0, (
            f"spend_log cost value must be a positive float. Got: {cost_val!r}"
        )


# ---------------------------------------------------------------------------
# HC-3 — Model pin: 'claude-opus-4-8' (pinned), not bare 'opus' (alias)
# ---------------------------------------------------------------------------

class TestModelPin:
    def test_claude_command_uses_pinned_model_not_alias(self):
        """The claude command must use the pinned model ID 'claude-opus-4-8',
        not the bare alias 'opus'.

        Aliases are unstable — 'opus' can silently advance to a new model version,
        changing cost and behaviour without any code change.  Pinning to
        'claude-opus-4-8' makes the model choice explicit and reviewable.

        RED intent: _run_prism() uses '--model opus' → 'opus' in args but
        'claude-opus-4-8' not in args → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        # Pinned model ID must be present.
        assert "claude-opus-4-8" in args_used, (
            "Pinned model 'claude-opus-4-8' is missing from the claude command. "
            "Use the pinned model ID, not the bare alias 'opus'. "
            f"Command was: {cmd_str}"
        )

        # Bare alias must NOT appear as the --model value.
        # Find the value after '--model' and verify it is not just 'opus'.
        if "--model" in args_used:
            model_idx = args_used.index("--model")
            if model_idx + 1 < len(args_used):
                model_val = args_used[model_idx + 1]
                assert model_val != "opus", (
                    f"--model is set to bare alias 'opus' — use pinned ID 'claude-opus-4-8'. "
                    f"Command was: {cmd_str}"
                )


# ---------------------------------------------------------------------------
# HC-2 regression — _persist_spend must read total_cost_usd (real CC envelope key)
# ---------------------------------------------------------------------------

class TestPersistSpendEnvelopeKey:
    """Regression suite for the cost_usd vs total_cost_usd bug.

    The Claude Code `claude -p --output-format json` envelope uses the key
    `total_cost_usd`, NOT `cost_usd`.  The original _persist_spend called
    `.get('cost_usd')` which always returns None on a real run, silently
    skipping the spend_log write.

    Provenance: PM-captured from live `claude -p --output-format json` (CC 2.1.181):
      {"total_cost_usd": 0.0728568, "type": "result", "subtype": "...", ...}
      — NO "cost_usd" key is present in the real envelope.

    RED intent: both tests below FAIL against the current _persist_spend
    (which calls .get('cost_usd') and therefore writes no row).
    """

    def test_persist_spend_writes_row_when_only_total_cost_usd_present(self):
        """Given a subprocess stdout whose ONLY cost key is total_cost_usd,
        _persist_spend MUST write a spend_log row with a positive-float cost.

        This is the primary regression guard for the cost_usd → total_cost_usd fix.
        A wrong implementation that still reads only 'cost_usd' will find None,
        skip the DB write, and this assertion will fail.
        """
        import json as _json
        import sqlite3
        import sys
        import os

        mod = _import_scheduler()
        worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        import database as _db

        run_id = "test-total-cost-usd-regression"

        # Real CC 2.1.181 envelope shape: ONLY total_cost_usd, NO cost_usd key.
        # Provenance: PM-captured from live `claude -p --output-format json` (CC 2.1.181).
        real_envelope = _json.dumps({
            "total_cost_usd": 0.0728568,
            "type": "result",
            "subtype": "success",
        })

        mod._persist_spend(run_id, real_envelope)

        conn = sqlite3.connect(_db._db_file())
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM prism_audit_log "
            "WHERE phase = 'spend_log' AND run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None, (
            "_persist_spend wrote NO prism_audit_log row when the subprocess stdout "
            "contained only 'total_cost_usd' (the real CC 2.1.181 envelope key). "
            "Fix: read 'total_cost_usd' from the parsed JSON, not 'cost_usd'."
        )

        parsed = _json.loads(row[0])
        cost_val = parsed.get("total_cost_usd")
        assert cost_val is not None, (
            f"spend_log content has no 'total_cost_usd' key. Got: {parsed!r}. "
            "The persisted entry must record cost under 'total_cost_usd'."
        )
        assert isinstance(cost_val, (int, float)) and cost_val > 0, (
            f"Persisted cost value must be a positive float. Got: {cost_val!r}"
        )

    def test_persist_spend_tolerant_fallback_legacy_cost_usd(self):
        """If an old/local CC build returns only 'cost_usd', _persist_spend
        should still write a spend_log row (tolerant fallback).

        This is optional hardening — the primary fix is total_cost_usd.
        A tolerant implementation tries total_cost_usd first, then falls back to cost_usd.
        If the implementation does NOT implement the fallback, this test simply
        verifies that at minimum the primary key path works; the test will SKIP
        if the implementation intentionally drops legacy support.

        RED intent: the current (unfixed) code reads only 'cost_usd' and works here
        by accident — but the primary test above still fails, which is what matters.
        """
        import json as _json
        import sqlite3
        import sys
        import os

        mod = _import_scheduler()
        worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        import database as _db

        run_id = "test-legacy-cost-usd-fallback"

        # Legacy envelope with only cost_usd (old CC builds or local dev).
        legacy_envelope = _json.dumps({"cost_usd": 0.042})

        mod._persist_spend(run_id, legacy_envelope)

        conn = sqlite3.connect(_db._db_file())
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM prism_audit_log "
            "WHERE phase = 'spend_log' AND run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        row = cursor.fetchone()
        conn.close()

        # If no row written with legacy envelope, skip (fallback not required).
        # The primary regression test above is the authoritative RED gate.
        if row is None:
            pytest.skip(
                "Implementation does not support legacy cost_usd fallback — "
                "that is acceptable; the primary test (total_cost_usd) is the gate."
            )

        parsed = _json.loads(row[0])
        # Accept either key in the persisted content for legacy path.
        cost_val = parsed.get("total_cost_usd") or parsed.get("cost_usd")
        assert cost_val is not None, (
            f"Legacy fallback row has no cost key. Got: {parsed!r}"
        )
        assert isinstance(cost_val, (int, float)) and cost_val > 0, (
            f"Legacy fallback cost value must be positive. Got: {cost_val!r}"
        )
