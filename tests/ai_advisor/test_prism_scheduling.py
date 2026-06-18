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

# Minimal MARKET_PRISM row dict used by F-4 tests and pre-existing happy-path tests
# to satisfy the _get_market_prism_row_for_run seam.  Shape only — no computed values.
_SAMPLE_MARKET_PRISM_ROW: dict = {
    "id": 99,
    "advisor_role": "MARKET_PRISM",
    "verdict": "risk-on",
    "rationale": "Synthetic test row — not a real council output.",
    "created_at": "2026-06-18 03:00:00",
    "raw_response": {"run_id": "placeholder"},
}


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
            # F-4: row-verification seam — this test verifies cmd shape, not the row
            # check; patch the seam so the happy path exits 0 as expected.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
        """The prompt (built from PRISM_RUN_PROMPT template) must be the last element
        in the subprocess cmd list.

        The claude CLI treats the final positional arg as the prompt to execute.
        Putting the prompt anywhere other than last means it is ignored or misinterpreted.

        NOTE: After the F-1 run_id-unification fix, the last cmd element is a
        runtime-built prompt string (the template with the scheduler's run_id injected),
        NOT the bare static PRISM_RUN_PROMPT constant.  This test therefore asserts
        that cmd[-1] is a non-empty string that CONTAINS the template's content — not
        exact equality — which remains valid whether PRISM_RUN_PROMPT is a static
        constant or a template.

        RED intent: current _run_prism uses a hardcoded literal string as the last
        element, not referencing PRISM_RUN_PROMPT at all → the hasattr assertion fails.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), (
            "PRISM_RUN_PROMPT constant missing — cannot verify the prompt is last in cmd."
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
        last_elem = args_used[-1]

        # The last element must be a non-empty string (the prompt).
        assert isinstance(last_elem, str) and len(last_elem) > 0, (
            f"The last cmd element must be a non-empty string (the prompt). "
            f"Got: {last_elem!r}"
        )

        # The last element must contain at least the first 30 chars of PRISM_RUN_PROMPT,
        # confirming the prompt derives from the template (not an unrelated string).
        # After F-1, it will be template + injected run_id — both are valid.
        template_prefix = mod.PRISM_RUN_PROMPT[:30]
        assert template_prefix in last_elem, (
            f"The last cmd element does not appear to derive from PRISM_RUN_PROMPT. "
            f"Expected the template prefix {template_prefix!r} to appear in cmd[-1]. "
            f"Got: {last_elem[:80]!r}. "
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
            # F-4: row-verification seam — only called after rc==0 (attempt 2);
            # return a valid row so the retry succeeds as expected.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
            # F-4: row-verification seam — this test verifies idempotency bypass,
            # not the row check; patch the seam so the run exits 0 as expected.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
            # F-4: row-verification seam — this test verifies idempotency bypass,
            # not the row check; patch the seam so the run exits 0 as expected.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
            # F-4: row-verification seam — _run_prism is mocked True above; also patch
            # the row check so the D-1 path still exits 0 as the test asserts.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
            # F-4: row-verification seam — this test verifies spend logging, not
            # the row check; patch the seam so the happy path exits 0 as expected.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=_SAMPLE_MARKET_PRISM_ROW,
            ),
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
# ---------------------------------------------------------------------------
# Council 5/5 orchestration directives (RED for prism-council-5of5)
#
# The root cause of 2/5 analyst participation: analysts spawn, emit ONE turn
# ("Standing by for the synthesizer's kickoff"), then go DORMANT.  The synthesizer
# then tries to resume them BY CANONICAL NAME via SendMessage — but by-name resume
# of a dormant subagent is unreliable.  The fix: embed the run_id + immediate-
# initial_read instruction INTO THE SPAWN PROMPT so the first turn IS the work.
#
# These tests assert that PRISM_RUN_PROMPT carries the directives that make 5/5
# achievable.  The LIVE council run (PM-gated) is the acceptance gate; these
# tests are a necessary precondition, not sufficient.
# ---------------------------------------------------------------------------


class TestCouncil5of5OrchestrationDirectives:
    """Assert PRISM_RUN_PROMPT contains the directives required for 5/5 participation."""

    def test_prompt_instructs_generating_run_id_before_spawning(self):
        """PRISM_RUN_PROMPT must instruct the primary to generate the run_id BEFORE
        spawning the analysts, so the run_id is available to embed in each analyst's
        spawn prompt.

        Directive (a): generate/use a run_id before any spawn.

        The test checks that the prompt contains language about generating a run_id
        AND that this generation instruction appears before (or alongside) the spawn
        instruction — not as an afterthought.

        RED intent: current PRISM_RUN_PROMPT tells the primary to spawn immediately
        without mentioning run_id generation first.  The run_id is mentioned only in
        the context of synthesizer-sends-kickoff (the old broken path), not as a
        pre-spawn step the primary does itself.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), "PRISM_RUN_PROMPT must exist"
        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        # The prompt must reference run_id generation — not just passing run_id along.
        # Accept any of these generation-directive phrases.
        generation_phrases = ["generate", "create the run_id", "run_id first", "generate a run_id"]
        has_generation = any(p in prompt_lower for p in generation_phrases)
        # Also accept: "run_id" AND "before" as a weaker signal.
        has_runid_before = "run_id" in prompt_lower and "before" in prompt_lower

        assert has_generation or has_runid_before, (
            "PRISM_RUN_PROMPT must instruct the primary to generate the run_id BEFORE "
            "spawning analysts, so the run_id is available to embed in each analyst's "
            "spawn prompt.  The current prompt does not contain any generation directive "
            "(e.g. 'generate a run_id' or 'run_id first ... then spawn').  "
            "Without this, analysts cannot receive the run_id at spawn time."
        )

    def test_prompt_instructs_embedding_kickoff_in_spawn_prompt(self):
        """PRISM_RUN_PROMPT must instruct the primary to embed the run_id AND an
        immediate-initial_read instruction INTO EACH ANALYST'S SPAWN PROMPT.

        Directive (b): embed-kickoff — do NOT spawn then send a kickoff message.

        The broken pattern is: spawn analyst (who stands by) → send kickoff via
        SendMessage (which may not reach a dormant agent).  The fix: put the
        run_id + 'produce your initial_read NOW' into the spawn prompt itself,
        so the first turn IS the initial_read.

        The prompt must contain language about embedding instructions in the spawn,
        NOT the old 'send a kickoff message after spawning' pattern.

        RED intent: current PRISM_RUN_PROMPT says the synthesizer 'messages each one'
        for their reads — the old kickoff-via-SendMessage path that broke at 2/5.
        """
        import re

        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), "PRISM_RUN_PROMPT must exist"
        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        # The prompt must use embed-in-spawn language.
        # Accept: "include in the spawn", "embed ... spawn", "spawn prompt includes",
        # "in the spawn prompt", "immediately" near analyst/spawn, etc.
        embed_phrases = [
            "include in the spawn",
            "embed",
            "spawn prompt",
            "in each analyst's spawn",
            "immediately on spawn",
            "in their spawn",
            "in the analyst spawn",
        ]
        has_embed_directive = any(p in prompt_lower for p in embed_phrases)

        # Stronger: "immediately" + ("initial_read" or "file" or "produce")
        immediate_action = re.search(
            r"immediat\w+.{0,80}(initial.read|file|produce|audit)",
            prompt_lower,
            re.DOTALL,
        )

        assert has_embed_directive or immediate_action, (
            "PRISM_RUN_PROMPT must instruct the primary to embed the run_id and "
            "an immediate-initial_read instruction INTO EACH ANALYST'S SPAWN PROMPT "
            "(not send a kickoff message after spawning). "
            "The current prompt describes the old synthesizer-kicks-off-via-SendMessage "
            "pattern, which is the root cause of 2/5 participation.  "
            "Required language: e.g. 'include run_id and instruction to produce initial_read "
            "immediately in each analyst\\'s spawn prompt'."
        )

    def test_prompt_instructs_capturing_agent_ids_not_canonical_names(self):
        """PRISM_RUN_PROMPT must instruct the primary to capture each analyst's agentId
        at spawn and address analysts by agentId (not canonical name) for the debate phase.

        Directive (c): address by agentId, not canonical name.

        The root cause of 2/5: the synthesizer tried SendMessage to
        'prism-technicals-analyst' (canonical name) to resume a dormant agent —
        by-name addressing of dormant subagents is unreliable (the harness wants
        the internal agentId).  Fix: primary captures agentId at spawn, passes it
        to the synthesizer; synthesizer addresses by agentId.

        The prompt must contain agentId-addressing language for the debate/coordination
        phase.

        RED intent: current PRISM_RUN_PROMPT has no mention of agentId — it still
        describes the synthesizer addressing analysts by canonical name.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), "PRISM_RUN_PROMPT must exist"
        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        # The prompt must contain agentId language.
        agent_id_phrases = [
            "agentid",
            "agent id",
            "agent_id",
            "capture.*id",
            "id.*spawn",
        ]
        import re
        has_agent_id = any(
            re.search(p, prompt_lower)
            for p in agent_id_phrases
        )

        assert has_agent_id, (
            "PRISM_RUN_PROMPT must instruct the primary to capture each analyst's "
            "agentId at spawn and pass it to the synthesizer for addressing. "
            "By-canonical-name addressing of dormant subagents is unreliable — "
            "this was the mechanism behind 2/5 participation. "
            "Required language: e.g. 'capture each analyst\\'s agentId at spawn' or "
            "'address analysts by agentId, not canonical name'. "
            "Current prompt has no agentId reference."
        )

    def test_prompt_instructs_wait_barrier_before_synthesis(self):
        """PRISM_RUN_PROMPT must instruct the synthesizer to wait for 5 initial_read
        rows in the audit DB before synthesizing (the audit-DB wait-barrier).

        Directive (d): never synthesize with <5 initial_read rows until barrier times out.

        The synthesizer must not synthesize on whatever analysts happened to respond
        in time — it must wait for the audit-DB write barrier (5 initial_read rows)
        before integrating.  This is the safety check that prevents hollow syntheses.
        The wait-barrier times out gracefully (limited-inputs for non-filers) but
        must be attempted.

        RED intent: current PRISM_RUN_PROMPT has no mention of a wait-barrier, audit-DB
        check for initial_read count, or a minimum threshold before synthesis.
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), "PRISM_RUN_PROMPT must exist"
        prompt = mod.PRISM_RUN_PROMPT
        prompt_lower = prompt.lower()

        # Look for wait-barrier language: either an explicit count (5) or
        # "audit" + "wait" near each other, or "initial_read" + "before" + "synthes".
        import re

        # Pattern 1: mentions waiting for initial_read rows before synthesizing
        wait_before_synthesis = re.search(
            r"(initial.read|audit.db|audit db).{0,150}(before|synthes)",
            prompt_lower,
            re.DOTALL,
        )

        # Pattern 2: "5" + "initial" (explicit count barrier)
        five_initial = re.search(r"\b5\b.{0,50}initial", prompt_lower, re.DOTALL)
        initial_five = re.search(r"initial.{0,50}\b5\b", prompt_lower, re.DOTALL)

        # Pattern 3: "wait" + "barrier" or "wait" + "all" + "analyst"
        wait_barrier = re.search(
            r"wait.{0,80}(barrier|all.{0,20}analyst|initial.read)",
            prompt_lower,
            re.DOTALL,
        )

        has_wait_barrier = bool(
            wait_before_synthesis or five_initial or initial_five or wait_barrier
        )

        assert has_wait_barrier, (
            "PRISM_RUN_PROMPT must include a wait-barrier directive: the synthesizer "
            "must not synthesize until it has confirmed (via the audit DB) that all 5 "
            "analysts have filed their initial_read rows, or until the barrier times out. "
            "Required language: e.g. 'wait until 5 initial_read rows appear in the audit DB' "
            "or 'do not synthesize until the audit-DB wait-barrier is satisfied'. "
            "Current prompt has no wait-barrier or audit-DB count check."
        )

    def test_prompt_names_all_five_analyst_types_for_spawning(self):
        """PRISM_RUN_PROMPT must name all 5 analyst types explicitly so the primary
        knows which agents to spawn.  (All 6 including synthesizer covered by the
        existing test; this pins the analyst list independently for clarity.)

        Directive (e): names all 5 analyst types + the synthesizer.

        This test extends the existing 6-agent naming test with tighter coupling to
        the directive context.

        RED intent: if any analyst name is dropped from the prompt during the refactor,
        this fails (defence-in-depth alongside the existing 6-agent test).
        """
        mod = _import_scheduler()

        assert hasattr(mod, "PRISM_RUN_PROMPT"), "PRISM_RUN_PROMPT must exist"
        prompt = mod.PRISM_RUN_PROMPT

        five_analysts = [
            "prism-technicals-analyst",
            "prism-sentiment-analyst",
            "prism-derivatives-analyst",
            "prism-macro-analyst",
            "prism-fundamentals-analyst",
        ]
        for name in five_analysts:
            assert name in prompt, (
                f"PRISM_RUN_PROMPT must name '{name}' (one of the 5 analysts the primary "
                f"must spawn).  Missing from current prompt."
            )

        # Synthesizer must also be present — it is the team lead that coordinates.
        assert "prism-synthesizer" in prompt, (
            "PRISM_RUN_PROMPT must name 'prism-synthesizer' (the team lead). "
            "Missing from current prompt."
        )


class TestAnalystRoleFilesImmediateInitialRead:
    """Assert analyst role files instruct immediate initial_read on spawn, not 'stand by'.

    The dormancy root cause: analyst role files say 'Do not proceed until you have
    received the run_id' (from the synthesizer's kickoff).  With embed-kickoff, the
    run_id arrives IN the spawn prompt — so analysts must produce initial_read
    immediately on their first turn, not stand by waiting.

    Failures in this class are RED — they indicate the role files still have the old
    'wait for kickoff from synthesizer' instruction rather than the new 'produce
    initial_read immediately when spawned with run_id' instruction.
    """

    # Analyst role files live in .claude/agents/ inside the shared project root.
    # In the worktree, they are accessible at _PROJECT_ROOT/.claude/agents/.
    _ANALYST_FILES = [
        "prism-technicals-analyst.md",
        "prism-sentiment-analyst.md",
        "prism-derivatives-analyst.md",
        "prism-macro-analyst.md",
        "prism-fundamentals-analyst.md",
    ]

    @staticmethod
    def _read_analyst_file(filename: str) -> str:
        """Read an analyst role file from .claude/agents/."""
        import os
        worktree = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        agents_dir = os.path.join(worktree, ".claude", "agents")
        filepath = os.path.join(agents_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            return f.read()

    def test_analyst_role_files_exist(self):
        """All 5 analyst role files must exist in .claude/agents/."""
        import os
        worktree = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        agents_dir = os.path.join(worktree, ".claude", "agents")
        for filename in self._ANALYST_FILES:
            filepath = os.path.join(agents_dir, filename)
            assert os.path.exists(filepath), (
                f"Analyst role file '{filename}' not found at {filepath}. "
                "All 5 analyst role files must exist."
            )

    def test_analysts_do_not_wait_for_synthesizer_kickoff(self):
        """Analyst role files must NOT instruct analysts to wait for a kickoff from
        the synthesizer before proceeding.

        The dormancy root cause: 'Do not proceed until you have received the run_id'
        (from the synthesizer's kickoff via SendMessage).  With embed-kickoff, the
        run_id arrives in the spawn prompt — waiting for a subsequent SendMessage
        kickoff causes dormancy.

        RED intent: current analyst files contain 'Do not proceed until you have
        received the run_id' — this test FAILS because that instruction is still present.
        """
        import re

        dormancy_patterns = [
            r"do not proceed until you have received",
            r"wait.*until.*run_id",
            r"do not proceed until.*kickoff",
            r"standby.*kickoff",
            r"stand by.*kickoff",
        ]

        for filename in self._ANALYST_FILES:
            content = self._read_analyst_file(filename)
            content_lower = content.lower()
            for pattern in dormancy_patterns:
                match = re.search(pattern, content_lower)
                assert match is None, (
                    f"Analyst role file '{filename}' contains dormancy-triggering language: "
                    f"matched '{match.group()}'. "
                    "With embed-kickoff, the run_id arrives in the spawn prompt — analysts "
                    "must NOT wait for a subsequent SendMessage kickoff from the synthesizer. "
                    "Remove 'Do not proceed until you have received the run_id' and replace "
                    "with 'when spawned with run_id, produce and file your initial_read "
                    "immediately on your first turn'."
                )

    def test_analysts_instructed_to_produce_initial_read_immediately_on_spawn(self):
        """Analyst role files must instruct: when spawned with a run_id, produce and
        file the initial_read IMMEDIATELY on the first turn.

        This is the positive complement to the anti-dormancy test above.  Not just
        removing the bad instruction but adding the correct one.

        RED intent: current analyst files do not contain any 'immediately on spawn'
        or 'first turn' instruction — analysts only know to wait for the synthesizer.
        """
        import re

        immediate_phrases = [
            "immediately",
            "first turn",
            "on spawn",
            "as soon as spawned",
            "upon spawn",
            "when spawned",
        ]

        for filename in self._ANALYST_FILES:
            content = self._read_analyst_file(filename)
            content_lower = content.lower()
            has_immediate = any(p in content_lower for p in immediate_phrases)
            assert has_immediate, (
                f"Analyst role file '{filename}' does not contain an immediate-action "
                "instruction.  When spawned with a run_id in the spawn prompt, the analyst "
                "must be instructed to produce and file its initial_read IMMEDIATELY on "
                "its first turn (not wait for a SendMessage kickoff). "
                f"Add language such as: 'When spawned with run_id in your prompt, "
                "produce and file your initial_read immediately on your first turn.' "
                f"Missing from {filename}."
            )


class TestSynthesizerRoleFileAgentIdAddressing:
    """Assert prism-synthesizer.md instructs addressing analysts by agentId.

    The synthesizer's step 3 currently says 'Send each analyst a kickoff message
    via SendMessage' to canonical names.  With embed-kickoff (analysts self-start
    on spawn), the synthesizer no longer needs to send kickoff messages — but
    it DOES need to address analysts by agentId for Q&A and debate, not canonical
    names (which fail for dormant/resumed agents).

    These tests are RED against the current synthesizer file.
    """

    @staticmethod
    def _read_synthesizer_file() -> str:
        import os
        worktree = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        filepath = os.path.join(worktree, ".claude", "agents", "prism-synthesizer.md")
        with open(filepath, encoding="utf-8") as f:
            return f.read()

    def test_synthesizer_instructs_addressing_by_agent_id_for_debate(self):
        """prism-synthesizer.md must instruct addressing analysts by agentId (not
        canonical name) for the debate/Q&A phase.

        RED intent: current prism-synthesizer.md does not mention agentId — it
        describes sending kickoff messages and Q&A via SendMessage to canonical names.
        """
        content = self._read_synthesizer_file()
        content_lower = content.lower()

        agent_id_phrases = ["agentid", "agent id", "agent_id"]
        has_agent_id = any(p in content_lower for p in agent_id_phrases)

        assert has_agent_id, (
            "prism-synthesizer.md must instruct the synthesizer to address analysts "
            "by agentId (not canonical name) for the debate and Q&A phase. "
            "By-canonical-name addressing of dormant/resumed agents is unreliable "
            "(root cause of 2/5 participation). "
            "Add: 'Address each analyst by their agentId (captured at spawn by the "
            "primary and passed to you), not their canonical name.' "
            "Current file has no agentId reference."
        )

    def test_synthesizer_instructs_wait_barrier_before_synthesis(self):
        """SUPERSEDED by TestSynthesizerWaitBarrierDeHollowed.test_synthesizer_instructs_wait_barrier_before_synthesis.

        This version used a DOTALL regex that matched the '5. Facilitate...' section
        heading, making it pass on a section number rather than a genuine count-based
        barrier sentence.  The de-hollowed version in TestSynthesizerWaitBarrierDeHollowed
        scopes to the Hard Rules section only and requires the literal 'initial_read'
        string (with underscore), which cannot match a section heading.

        Kept here for git-history legibility.  New coverage is in
        TestSynthesizerWaitBarrierDeHollowed below.
        """
        # Intentionally skipped — superseded.  Do not restore the hollow assertions.
        pytest.skip(
            "Hollow test superseded by "
            "TestSynthesizerWaitBarrierDeHollowed."
            "test_synthesizer_instructs_wait_barrier_before_synthesis"
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


# ---------------------------------------------------------------------------
# F-1 — run_id unification (spend-attribution join)
#
# Defect: prism_scheduler.py:203 generates run_id = str(uuid.uuid4()) and
# passes it to _persist_spend.  But PRISM_RUN_PROMPT tells the headless
# council to generate its OWN run_id via datetime.now().strftime(…), so the
# council's audit/MARKET_PRISM rows carry a datetime string while the
# spend_log row carries a uuid4.  The join key never matches.
#
# Fix contract: the scheduler generates one authoritative run_id (uuid4),
# embeds it in the prompt string passed to the subprocess (so the council
# uses it for ALL rows), and _persist_spend uses the SAME run_id.
#
# All three tests below are RED against the current implementation.
# ---------------------------------------------------------------------------


class TestRunIdUnification:
    """F-1: the scheduler-generated run_id is the single join key for all rows."""

    def test_run_id_threaded_into_prism_prompt_not_minted_by_council(self):
        """The prompt passed to the subprocess must NOT instruct the council to
        generate its own run_id via datetime.now/strftime — that is the defect
        (produces a different run_id than the one in the spend_log).

        The prompt string (cmd[-1] sent to subprocess) must:
          (a) NOT contain 'strftime' or 'datetime.now' (no self-minting instructions)
          (b) Contain the scheduler's run_id value (uuid4 hex string) embedded in it,
              proving the prompt is built dynamically with the run_id injected.

        RED intent: current PRISM_RUN_PROMPT is a static string constant that
        contains "datetime.now(timezone.utc).strftime(...)" — assertion (a) fails.
        """
        import re

        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_subprocess,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_subprocess.call_args[0][0]
        prompt_str = args_used[-1]  # claude CLI: last positional arg is the prompt

        # (a) Council must NOT be instructed to mint its own run_id.
        assert "strftime" not in prompt_str, (
            "The prompt passed to subprocess contains 'strftime' — this instructs the "
            "council to mint its own datetime run_id instead of using the scheduler's "
            "uuid4 run_id.  Remove the datetime.now/strftime snippet from the prompt; "
            "the scheduler must embed its run_id into the prompt string at call time."
        )
        assert "datetime.now" not in prompt_str, (
            "The prompt passed to subprocess contains 'datetime.now' — this instructs "
            "the council to generate a fresh datetime run_id, breaking the join with "
            "the spend_log entry (which uses the scheduler's uuid4 run_id).  "
            "Remove it; embed the scheduler run_id into the prompt instead."
        )

        # (b) The scheduler's run_id (a uuid4 hex string) must appear in the prompt,
        # proving it was injected at call time rather than left as a static constant.
        # uuid4 pattern: 8-4-4-4-12 hex chars separated by hyphens.
        uuid4_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        assert uuid4_pattern.search(prompt_str), (
            "The prompt passed to subprocess does not contain a uuid4 run_id.  "
            "The scheduler must inject its run_id (uuid4 format) into the prompt string "
            "so the council uses it for all audit/MARKET_PRISM rows.  "
            f"Prompt (first 400 chars): {prompt_str[:400]!r}"
        )

    def test_persist_spend_run_id_matches_run_id_embedded_in_prompt(self):
        """The run_id passed to insert_prism_audit_entry(phase='spend_log') must equal
        the run_id embedded in the subprocess prompt string — one join key.

        Technique: mock both subprocess.run (returns JSON with total_cost_usd) and
        database.insert_prism_audit_entry; capture the run_id from each and compare.

        RED intent: current code passes the scheduler's uuid4 to _persist_spend, but
        the static PRISM_RUN_PROMPT tells the council to generate a datetime run_id.
        The two values never match.  After the F-1 fix the prompt is built dynamically
        with the same uuid4 injected, so both captures will agree.
        """
        import json as _json
        import re

        mod = _import_scheduler()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps({"total_cost_usd": 0.05, "type": "result"})

        captured_subprocess_args: list = []
        captured_audit_calls: list = []

        def _capture_subprocess(cmd, **kwargs):
            captured_subprocess_args.append(cmd)
            return mock_result

        def _capture_audit(run_id, agent_role, phase, content):
            captured_audit_calls.append(
                {"run_id": run_id, "agent_role": agent_role, "phase": phase}
            )
            return 1  # fake row id

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=_capture_subprocess),
            patch("database.insert_prism_audit_entry", side_effect=_capture_audit),
            pytest.raises(SystemExit),
        ):
            mod.main()

        assert captured_subprocess_args, "subprocess.run was never called"
        assert captured_audit_calls, (
            "insert_prism_audit_entry was never called — _persist_spend did not run.  "
            "Ensure subprocess.run returns returncode=0 and stdout with total_cost_usd."
        )

        prompt_str = captured_subprocess_args[0][-1]  # last element = the prompt

        # Extract the run_id from the prompt (uuid4 pattern).
        uuid4_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        prompt_match = uuid4_pattern.search(prompt_str)
        assert prompt_match, (
            "Could not find a uuid4 run_id in the subprocess prompt string.  "
            "The F-1 fix must inject the scheduler's uuid4 run_id into the prompt.  "
            f"Prompt (first 400 chars): {prompt_str[:400]!r}"
        )
        run_id_in_prompt = prompt_match.group()

        # The spend_log audit entry must use the same run_id.
        spend_log_entries = [c for c in captured_audit_calls if c["phase"] == "spend_log"]
        assert spend_log_entries, (
            "No spend_log audit entry was written.  "
            "_persist_spend must call insert_prism_audit_entry with phase='spend_log'."
        )
        run_id_in_spend_log = spend_log_entries[0]["run_id"]

        assert run_id_in_prompt == run_id_in_spend_log, (
            f"run_id mismatch — the spend_log entry uses a DIFFERENT run_id than the "
            f"one embedded in the subprocess prompt.  "
            f"Prompt run_id:    {run_id_in_prompt!r}\n"
            f"spend_log run_id: {run_id_in_spend_log!r}\n"
            "These must be equal so spend-attribution joins work.  "
            "Fix: have _run_prism embed the scheduler's run_id into the prompt string "
            "instead of letting the council mint its own datetime run_id."
        )

    def test_idempotency_guard_uses_created_at_not_run_id_format(self):
        """Regression lock: _is_todays_row() must key on created_at date, not run_id format.

        The F-1 change switches run_id from a datetime string to uuid4 (or embeds an
        existing uuid4 in the prompt).  This test locks the idempotency guard so a
        wrong F-1 implementation that accidentally starts keying on run_id format
        (e.g. parsing a datetime from raw_response.run_id) cannot silently break the
        already-ran-today skip.

        Three sub-cases all call _is_todays_row() directly:
          (a) today-row with raw_response.run_id = uuid4 string  → must return True
          (b) today-row with raw_response.run_id = datetime string → must return True
          (c) today-row with no raw_response.run_id key at all   → must return True

        All three PASS against current code (guard uses only created_at).
        If a broken F-1 implementation starts checking run_id format, sub-case (a) fails.

        This is a regression lock — it documents the invariant, not a defect in
        current code.  It will catch any future implementation that incorrectly couples
        idempotency to run_id format.
        """
        from datetime import datetime, timezone, timedelta

        mod = _import_scheduler()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Sub-case (a): run_id is a uuid4 string (the F-1 post-fix format).
        row_uuid4 = {
            "id": 1,
            "verdict": "neutral",
            "created_at": today_str,
            "advisor_role": "MARKET_PRISM",
            "raw_response": {"run_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"},
        }
        assert mod._is_todays_row(row_uuid4) is True, (
            "_is_todays_row returned False for a today-row whose run_id is a uuid4 string.  "
            "The guard must key on created_at date only, not run_id format.  "
            "If the F-1 fix changed the guard to parse run_id, revert that change."
        )

        # Sub-case (b): run_id is a datetime string (the pre-F-1 format).
        row_datetime = {
            "id": 2,
            "verdict": "neutral",
            "created_at": today_str,
            "advisor_role": "MARKET_PRISM",
            "raw_response": {"run_id": "2026-06-18T03:00:00+00:00"},
        }
        assert mod._is_todays_row(row_datetime) is True, (
            "_is_todays_row returned False for a today-row whose run_id is a datetime string.  "
            "The guard must key on created_at date only."
        )

        # Sub-case (c): raw_response has no run_id key at all.
        row_no_run_id = {
            "id": 3,
            "verdict": "neutral",
            "created_at": today_str,
            "advisor_role": "MARKET_PRISM",
            "raw_response": {},
        }
        assert mod._is_todays_row(row_no_run_id) is True, (
            "_is_todays_row returned False for a today-row with no run_id in raw_response.  "
            "The guard must key on created_at date only — run_id presence is irrelevant."
        )


# ---------------------------------------------------------------------------
# F-2 — Hard rule bullets in prism-synthesizer.md
#
# Defect: the synthesizer role file has step-4 GUIDANCE about querying the
# audit DB, but no Hard Rule bullet that:
#   (1) says NEVER synthesize until 5 initial_read rows are confirmed, and
#   (2) prohibits falsely attributing "did not spawn" to a lens that spawned
#       but didn't file its row (the exact 2/5 false-attribution bug).
#
# These tests read .claude/agents/prism-synthesizer.md and scope assertions
# to the ## Hard Rules section only, preventing false matches from body text.
# Both are RED against the current file.
# ---------------------------------------------------------------------------


def _read_synthesizer_hard_rules_section() -> str:
    """Extract the ## Hard Rules section text from prism-synthesizer.md."""
    import os

    # Resolve path from this test file's location.
    # tests/ai_advisor/ -> worktree root -> .claude/agents/
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    worktree_root = os.path.dirname(os.path.dirname(tests_dir))
    filepath = os.path.join(worktree_root, ".claude", "agents", "prism-synthesizer.md")
    with open(filepath, encoding="utf-8") as fh:
        content = fh.read()
    # Extract everything after the ## Hard Rules heading.
    # Stop at the next ## heading (if any) to scope tightly.
    parts = content.split("## Hard Rules", maxsplit=1)
    if len(parts) < 2:
        return ""  # section missing — tests will fail with informative messages
    after_heading = parts[1]
    # Stop at the next top-level ## section if present.
    next_section = after_heading.find("\n## ")
    return after_heading[:next_section] if next_section != -1 else after_heading


class TestSynthesizerWaitBarrierHardRule:
    """F-2: prism-synthesizer.md Hard Rules must encode the wait-barrier and
    the false-attribution prohibition as explicit hard rules, not just guidance."""

    def test_synthesizer_hard_rules_prohibit_synthesis_before_five_initial_reads(self):
        """The ## Hard Rules section of prism-synthesizer.md must contain a bullet
        that explicitly prohibits synthesizing before 5 initial_read rows are confirmed.

        Required elements (all must co-occur within 300 chars in the Hard Rules text):
          - A prohibition marker: 'never', 'do not', or 'must not' (case-insensitive)
          - The literal string 'initial_read' (underscore — the audit phase name)
          - A five-count token: '5' or 'five' or 'all five' (case-insensitive)

        'initial_read' with underscore cannot match a section heading or casual prose;
        it uniquely identifies the audit DB phase name, making this assertion non-hollow.

        RED intent: current Hard Rules section has none of these three elements together.
        The step-4 body contains an 'initial_read' bash snippet, but that is NOT in
        Hard Rules — the split on '## Hard Rules' excludes it.
        """
        import re

        hard_rules_text = _read_synthesizer_hard_rules_section()
        assert hard_rules_text.strip(), (
            "prism-synthesizer.md has no '## Hard Rules' section or it is empty.  "
            "Cannot verify the wait-barrier hard rule."
        )

        hard_rules_lower = hard_rules_text.lower()

        # We require all three elements to appear within a 300-char window.
        # Strategy: find each occurrence of 'initial_read' and check the surrounding
        # 300 chars for a prohibition marker AND a five-count token.
        initial_read_positions = [
            m.start() for m in re.finditer(r"initial_read", hard_rules_lower)
        ]

        found_genuine_barrier = False
        for pos in initial_read_positions:
            # Extract a 300-char window centred on the occurrence (±150 chars).
            window_start = max(0, pos - 150)
            window_end = min(len(hard_rules_lower), pos + 150)
            window = hard_rules_lower[window_start:window_end]

            has_prohibition = bool(
                re.search(r"\b(never|do not|must not)\b", window)
            )
            has_five_count = bool(
                re.search(r"\b(5|five|all five)\b", window)
            )

            if has_prohibition and has_five_count:
                found_genuine_barrier = True
                break

        assert found_genuine_barrier, (
            "prism-synthesizer.md ## Hard Rules section is missing the wait-barrier rule.  "
            "Required: a single sentence/bullet containing ALL THREE of: "
            "(1) a prohibition marker ('never'/'do not'/'must not'), "
            "(2) the literal 'initial_read' (the audit DB phase name), and "
            "(3) a five-count token ('5' or 'five' or 'all five'), "
            "all within 300 characters of each other.  "
            "Example: 'Never synthesize until 5 initial_read rows are confirmed in "
            "the audit DB for this run_id (or the barrier times out gracefully).'  "
            f"Current Hard Rules section (first 600 chars): "
            f"{hard_rules_text[:600]!r}"
        )

    def test_synthesizer_hard_rules_prohibit_false_attribution(self):
        """The ## Hard Rules section must explicitly prohibit falsely attributing
        'did not spawn' to a lens that spawned but did not file its initial_read row.

        This is the exact 2/5 false-attribution bug: the synthesizer saw no SendMessage
        inbox entry from a lens and recorded it as 'did not spawn' — but the lens HAD
        spawned; its message just hadn't arrived.  The hard rule must close this.

        Required: one of these patterns in the Hard Rules section (case-insensitive):
          Pattern A: (never|do not|must not) ... (falsely|false) ... (spawn|report|attribute)
          Pattern B: 'spawned but' combined with 'did not' or 'didn't' near 'report'
          Pattern C: 'never falsely' within 200 chars of 'spawn'

        RED intent: current Hard Rules section has no false-attribution prohibition.
        """
        import re

        hard_rules_text = _read_synthesizer_hard_rules_section()
        assert hard_rules_text.strip(), (
            "prism-synthesizer.md has no '## Hard Rules' section or it is empty.  "
            "Cannot verify the false-attribution prohibition."
        )

        hard_rules_lower = hard_rules_text.lower()

        # Pattern A: prohibition marker + false* + spawn/report/attribute
        pattern_a = re.search(
            r"(never|do not|must not).{0,120}(falsely|false.{0,15}attribut).{0,120}"
            r"(spawn|report|respond)",
            hard_rules_lower,
            re.DOTALL,
        )

        # Pattern B: "spawned but" + "did not" or "didn't" near "report"
        pattern_b = re.search(
            r"spawned.{0,30}but.{0,80}(did not|didn.t).{0,50}(report|file|respond)",
            hard_rules_lower,
            re.DOTALL,
        )

        # Pattern C: "never falsely" within 200 chars of "spawn"
        pattern_c = re.search(
            r"never.{0,30}falsely.{0,200}spawn",
            hard_rules_lower,
            re.DOTALL,
        )

        # Pattern D: direct prohibition on attributing non-response to non-spawn
        pattern_d = re.search(
            r"(never|do not|must not).{0,80}(attribute|assume).{0,80}"
            r"(did not spawn|not.{0,10}spawn|non.responsive)",
            hard_rules_lower,
            re.DOTALL,
        )

        has_prohibition = bool(pattern_a or pattern_b or pattern_c or pattern_d)

        assert has_prohibition, (
            "prism-synthesizer.md ## Hard Rules section is missing the false-attribution "
            "prohibition.  The 2/5 council bug was caused by the synthesizer recording a "
            "lens as 'did not spawn' when it had spawned but its SendMessage hadn't arrived.  "
            "Required: a Hard Rules bullet explicitly prohibiting this false attribution.  "
            "Example: 'Never falsely attribute non-response to a lens that spawned — "
            "a lens that spawned but did not report is missing/late, not absent; "
            "mark it limited-inputs only after the audit-DB wait-barrier times out.'  "
            f"Current Hard Rules section (first 600 chars): "
            f"{hard_rules_text[:600]!r}"
        )


# ---------------------------------------------------------------------------
# F-3 — De-hollow the wait-barrier test
#
# The original test_synthesizer_instructs_wait_barrier_before_synthesis in
# TestSynthesizerRoleFileAgentIdAddressing was hollow: its DOTALL regex
# `r"\b5\b.{0,80}initial.read"` matched the section heading
# "5. Facilitate clarifying Q&A" because \b5\b hit the section NUMBER and
# the dot-wildcard "initial.read" matched "initial read" (space) in body prose.
#
# The tests below close that hollow-pass by:
#   (1) Scoping to the ## Hard Rules section only (not full document).
#   (2) Requiring the literal 'initial_read' with underscore (not dot-wildcard),
#       which cannot match "initial read" in a section heading.
#   (3) Explicitly proving the old hollow-pass pattern IS hollow on the current
#       file (the hollow-detector test).
# ---------------------------------------------------------------------------


class TestSynthesizerWaitBarrierDeHollowed:
    """F-3: de-hollowed wait-barrier tests that cannot be satisfied by section headings."""

    def test_synthesizer_instructs_wait_barrier_before_synthesis(self):
        """prism-synthesizer.md ## Hard Rules must contain a genuine count-based
        wait-barrier statement — not a section heading or body prose.

        Assertion (scoped to Hard Rules section only):
          The Hard Rules text must contain the literal 'initial_read' (underscore)
          AND a five-count token ('5'/'five'/'all five') AND a prohibition/wait marker
          ('never'/'do not'/'must not'/'wait') within 300 chars.

        Why this cannot be hollow: 'initial_read' with underscore is the audit DB
        phase name.  It does NOT appear in section headings (which say 'initial reads'
        with a space) or in casual body prose.  Only a genuine barrier sentence
        naming the audit DB phase will satisfy this assertion.

        RED on current file: the Hard Rules section has no 'initial_read' occurrence.
        GREEN only after F-2 adds the wait-barrier hard rule bullet.
        """
        import re

        hard_rules_text = _read_synthesizer_hard_rules_section()
        assert hard_rules_text.strip(), (
            "prism-synthesizer.md has no '## Hard Rules' section or it is empty."
        )

        hard_rules_lower = hard_rules_text.lower()

        # 'initial_read' with literal underscore is required — not dot-wildcard.
        initial_read_positions = [
            m.start() for m in re.finditer(r"initial_read", hard_rules_lower)
        ]

        assert initial_read_positions, (
            "prism-synthesizer.md ## Hard Rules section does not contain the string "
            "'initial_read' (with underscore).  A genuine wait-barrier hard rule must "
            "name the audit DB phase explicitly.  Section headings ('5. Facilitate...') "
            "and casual prose ('initial reads') cannot satisfy this assertion.  "
            "Add a Hard Rules bullet such as: 'Never synthesize until 5 initial_read "
            "rows are confirmed in the audit DB for this run_id.'  "
            f"Current Hard Rules section (first 600 chars): "
            f"{hard_rules_text[:600]!r}"
        )

        # Additionally require a five-count token and a prohibition/wait marker
        # within 300 chars of the 'initial_read' occurrence.
        found_genuine_barrier = False
        for pos in initial_read_positions:
            window_start = max(0, pos - 150)
            window_end = min(len(hard_rules_lower), pos + 150)
            window = hard_rules_lower[window_start:window_end]

            has_marker = bool(
                re.search(r"\b(never|do not|must not|wait)\b", window)
            )
            has_five = bool(re.search(r"\b(5|five|all five)\b", window))

            if has_marker and has_five:
                found_genuine_barrier = True
                break

        assert found_genuine_barrier, (
            "prism-synthesizer.md ## Hard Rules section contains 'initial_read' but "
            "the surrounding 300 chars lack BOTH a prohibition/wait marker "
            "('never'/'do not'/'must not'/'wait') AND a five-count token ('5'/'five'/'all five').  "
            "All three must appear together in one statement.  "
            "Example: 'Never synthesize until 5 initial_read rows are confirmed.'  "
            f"Current Hard Rules section (first 600 chars): "
            f"{hard_rules_text[:600]!r}"
        )

    def test_synthesizer_wait_barrier_not_satisfied_by_section_heading(self):
        """Explicit hollow-detector: prove the old DOTALL regex was inadequate, then
        assert the new Hard-Rules-scoped assertion is immune to that false positive.

        The old test used: re.search(r"\\b5\\b.{0,80}initial.read", content_lower, re.DOTALL)

        That regex matched the "5. Facilitate clarifying Q&A" section heading because
        \\b5\\b hit the section NUMBER and the dot-wildcard "initial.read" reached
        "initial read" (space) in the body prose — not a genuine barrier sentence.

        This test asserts that the FULL FILE (including after the F-2 fix) contains
        at least one OLD-REGEX match that carries 'initial_read' WITH UNDERSCORE
        — meaning a genuine barrier sentence now exists that satisfies the old regex
        on substance (not just on a heading).

        GREEN condition (after F-2 fix): the old regex finds ≥1 match containing
        'initial_read' (underscore) — the new Hard Rules bullet provides it.

        RED condition (current file, before F-2 fix): the old regex finds only matches
        containing 'initial read' (space) from the section heading — zero genuine
        underscore matches.  This proves the old test was hollow.

        NOTE: The step-5 heading's 'initial reads' (space) match will STILL exist
        after the F-2 fix — this test does NOT require ALL matches to have underscores
        (that would keep it RED forever).  It only requires AT LEAST ONE match to
        contain the underscore form, meaning the genuine barrier sentence is now present.
        """
        import re
        import os

        tests_dir = os.path.dirname(os.path.abspath(__file__))
        worktree_root = os.path.dirname(os.path.dirname(tests_dir))
        filepath = os.path.join(worktree_root, ".claude", "agents", "prism-synthesizer.md")
        with open(filepath, encoding="utf-8") as fh:
            full_content = fh.read()
        content_lower = full_content.lower()

        # Apply the OLD hollow regex to the full file.
        old_regex_matches = list(re.finditer(
            r"\b5\b.{0,80}initial.read", content_lower, re.DOTALL
        ))

        # Count how many matches contain 'initial_read' (underscore) — genuine barriers.
        genuine_barrier_matches = [
            m for m in old_regex_matches if "initial_read" in m.group()
        ]

        # On the CURRENT FILE (before F-2): zero genuine underscore matches exist.
        # The old regex only matched the heading/prose path — hollow confirmed.
        # After F-2: ≥1 genuine underscore match from the new Hard Rules bullet.
        assert len(genuine_barrier_matches) >= 1, (
            "The prism-synthesizer.md file has no match for the old barrier regex "
            r"(r'\b5\b.{0,80}initial.read') that contains 'initial_read' (underscore). "
            "This means the old test was hollow — it passed on the section heading "
            "'5. Facilitate...' prose ('initial read' with space), not a genuine "
            "count-based barrier sentence. "
            "After the F-2 fix, the new Hard Rules bullet must contain 'initial_read' "
            "(underscore) + '5' in close proximity, satisfying this assertion. "
            f"All old-regex matches found: "
            f"{[m.group() for m in old_regex_matches]!r}"
        )


# ---------------------------------------------------------------------------
# F-4 — Scheduler must verify MARKET_PRISM row before declaring success
#
# Defect: main() calls _run_prism(run_id) → rc==0 → prints "Run completed
# successfully." and exits 0 regardless of whether the council actually
# persisted a MARKET_PRISM observation row.  A council that exits cleanly
# but writes no row is a silent false-green — dangerous for unattended nightly.
#
# Fix contract:
#   - Add _get_market_prism_row_for_run(run_id) as a patchable seam that
#     queries advisor_observations for a MARKET_PRISM row matching run_id.
#   - Fold the row check into the per-attempt success condition inside the
#     existing MAX_ATTEMPTS retry loop:
#       per-attempt success = (rc==0) AND (_get_market_prism_row_for_run returns a dict)
#   - If all MAX_ATTEMPTS exhaust without a row, fail loudly (exit non-zero).
#   - Spend logging (_persist_spend) fires on rc==0 BEFORE the row check;
#     it is NOT skipped on an empty-row attempt (preservation of existing contract).
#
# Tests:
#   Test 1 — RED: rc==0 but no row → scheduler reports FAILURE (non-zero exit,
#            no "Run completed successfully" in stdout).
#   Test 2 — happy path regression lock: rc==0 + row exists → exit 0 +
#            success message.  Skips if seam absent (happens pre-GREEN), so it
#            does NOT count as a RED gate — it exists to catch a broken happy path
#            post-GREEN.
#   Test 3 — RED: rc==0 + no row on attempt 1, then rc==0 + row on attempt 2 →
#            subprocess called TWICE (retry happened) and exit 0.
# ---------------------------------------------------------------------------

class TestMarketPrismRowVerification:
    """F-4: scheduler must verify a MARKET_PRISM row exists before declaring success.

    Per-attempt success = subprocess rc==0 AND _get_market_prism_row_for_run(run_id)
    returns a non-None dict.  Silent false-green on an empty council run is the bug.
    """

    def test_scheduler_fails_when_subprocess_succeeds_but_no_market_prism_row(
        self, capsys
    ):
        """rc==0 + no MARKET_PRISM row → all MAX_ATTEMPTS exhausted → exit non-zero
        + no 'Run completed successfully' in stdout.

        RED on current code: current main() exits 0 on the first rc==0 without
        checking for a row.  The SystemExit(0) causes `.code != 0` to fail.

        After fix: the row-absent path is not counted as per-attempt success, so
        all MAX_ATTEMPTS are consumed and main() exits 1.
        """
        mod = _import_scheduler()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = "{}"
        mock_subprocess_result.stderr = ""

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_subprocess_result),
            # create=True: installs the mock even before the seam exists.
            # Pre-GREEN: main() never calls it → exits 0 → behavioral assertion fires.
            # Post-GREEN: main() calls it → gets None → retries → exits 1 → passes.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                return_value=None,
                create=True,
            ),
            patch("time.sleep", return_value=None),  # suppress backoff
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        assert exc_info.value.code != 0, (
            "Scheduler exited 0 even though subprocess returned rc==0 but NO "
            "MARKET_PRISM row was written for the run_id.  "
            "This is the F-4 silent false-green: 'Run completed successfully' "
            "must NOT be reported when the council produced no row.  "
            "Fix: add _get_market_prism_row_for_run(run_id) as a patchable seam, "
            "call it after rc==0, and treat a None return as a failed attempt — "
            "retry inside MAX_ATTEMPTS and fail loudly (exit non-zero) if all "
            "attempts exhaust without a confirmed row."
        )

        captured = capsys.readouterr()
        assert "Run completed successfully" not in captured.out, (
            "Scheduler printed 'Run completed successfully' despite no MARKET_PRISM "
            "row existing for the run_id.  Success message must only appear when "
            "the row is confirmed present."
        )

    def test_scheduler_succeeds_when_subprocess_succeeds_and_market_prism_row_exists(
        self, capsys
    ):
        """Happy-path regression lock: rc==0 + row present → exit 0 + success message.

        This test SKIPS (not fails) when _get_market_prism_row_for_run is absent from
        the module — i.e., pre-GREEN — so it does NOT count as a RED gate.
        After the implementer adds the seam, it must run and PASS (exit 0 preserved).

        Assertion: happy path must not be broken by the F-4 fix.
        """
        mod = _import_scheduler()

        # Skip if seam not yet added — this is the happy-path regression lock,
        # not the RED gate.  Tests 1 and 3 are the RED gates.
        if not hasattr(mod, "_get_market_prism_row_for_run"):
            pytest.skip(
                "_get_market_prism_row_for_run seam not yet present (pre-GREEN).  "
                "This happy-path regression lock will run after the F-4 fix is added."
            )

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = '{"total_cost_usd": 5.0}'
        mock_subprocess_result.stderr = ""

        sample_row = dict(_SAMPLE_MARKET_PRISM_ROW)

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_subprocess_result),
            patch.object(
                mod, "_get_market_prism_row_for_run", return_value=sample_row
            ),
            patch.object(mod, "_persist_spend"),  # suppress audit write side-effect
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        assert exc_info.value.code == 0, (
            f"Scheduler exited {exc_info.value.code} on the happy path (rc==0 + row "
            f"present).  The F-4 fix must preserve exit 0 when the row exists.  "
            f"stdout: {capsys.readouterr().out!r}"
        )

        captured = capsys.readouterr()
        # Accept any success-indicating message — exact wording may change.
        success_indicators = ["completed successfully", "success", "run complete"]
        assert any(ind in captured.out.lower() for ind in success_indicators), (
            "No success message found in stdout on the happy path (rc==0 + row present).  "
            f"stdout: {captured.out!r}  "
            "Expected one of: " + str(success_indicators)
        )

    def test_scheduler_retries_when_subprocess_succeeds_but_no_row_then_succeeds_on_second_attempt(
        self, capsys
    ):
        """Retry design: rc==0 + no row on attempt 1, then rc==0 + row on attempt 2
        → subprocess called TWICE and exit 0.

        RED on current code: current main() calls subprocess once and exits 0 on
        the first rc==0 — subprocess is called exactly once, not twice.

        After fix (retry-on-empty design): the no-row attempt is not counted as
        success; the loop continues to the second attempt which finds the row and
        exits 0.  subprocess.run must have been called twice.
        """
        mod = _import_scheduler()

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = '{"total_cost_usd": 5.0}'
        mock_subprocess_result.stderr = ""

        sample_row = dict(_SAMPLE_MARKET_PRISM_ROW)

        # _get_market_prism_row_for_run returns None on attempt 1, row on attempt 2.
        row_side_effects = [None, sample_row]

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_subprocess_result) as mock_sub,
            # create=True: installs the mock even before the seam exists.
            # Pre-GREEN: main() never calls it → subprocess called once → assertion fires.
            # Post-GREEN: main() calls it → None first, row second → called twice.
            patch.object(
                mod,
                "_get_market_prism_row_for_run",
                side_effect=row_side_effects,
                create=True,
            ),
            patch.object(mod, "_persist_spend"),  # suppress audit write side-effect
            patch("time.sleep", return_value=None),  # suppress backoff delay
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        subprocess_call_count = mock_sub.call_count

        assert subprocess_call_count >= 2, (
            f"subprocess.run was called {subprocess_call_count} time(s).  "
            "Expected ≥2 calls: attempt 1 (rc==0 but no row → not success, retry), "
            "attempt 2 (rc==0 + row → success).  "
            "Current code exits 0 on the first rc==0 without checking for a row — "
            "that is the F-4 bug.  "
            "Fix: treat rc==0-but-no-row as a failed attempt and let the MAX_ATTEMPTS "
            "loop continue to the next attempt."
        )

        assert exc_info.value.code == 0, (
            f"Scheduler exited {exc_info.value.code} after attempt 2 succeeded "
            f"(rc==0 + row present).  Expected exit 0.  "
            f"subprocess call count: {subprocess_call_count}"
        )
