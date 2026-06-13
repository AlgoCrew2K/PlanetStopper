# Advisor Synthesis Model → Configurable (Opus prod / cheap CI)

**Epic:** C — platform polish · **Status:** 🔴 not started.

## Goal

The advisor synthesis path currently hardcodes a model (`claude-haiku-4-5-20251001` in
`advisors/lens_pipeline._synthesize_via_claude`, a Cycle-4 placeholder). Production analysis
should run on Opus 4.8; CI/tests should not burn Opus tokens. Make the model **configurable**
so prod uses Opus and test/CI uses a cheap/mocked model.

## Acceptance criteria

1. The synthesis (and any advisor LLM call that should be model-tiered) reads its model from a
   single config source (env var with a sensible default), not a hardcoded literal.
2. Default in production resolves to Opus 4.8; tests/CI resolve to a cheap model or a mock and
   NEVER make a real Opus call.
3. No behavior change to the JSON-extraction / fence-stripping logic (that fix landed at
   `df2d19e` — keep it).
4. Tests assert the config wiring (which model is selected under which env), not a specific
   network response.

## Relationship to Market Prism

Once Epic A (real agent team) lands, the *single-Haiku synthesis* path is superseded for the
overnight read. This feature still matters for any remaining advisor LLM calls (chat,
swap/logic explanations) that should be model-tiered. Scope to those; do NOT re-plumb the
Prism's own synthesis (that becomes the synthesizer agent on Opus).

## Team / approach

Small — could be a one-cycle Toxic Pair (config is a new codepath) or, if it's a pure literal→
env swap on an existing path, a config edit with an existing-test guard. Confirm which at
dispatch. doc-gen documents the config knob.

## Dependencies

None hard. Schedule around Epic A's exclusive-focus window.
