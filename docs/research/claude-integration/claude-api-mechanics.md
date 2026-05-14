# Claude API Integration Mechanics — AlphaBot v3

**Research date:** 2026-05-14
**Researcher:** claude-code-guide agent
**Scope:** How to integrate the Claude API into AlphaBot's on-demand operator-assist config-advisor feature.
**Note:** This doc was reconstructed by the PM from the research agent's findings — the agent produced the content but did not complete a git commit.

---

## 1. Authentication & Key Management

AlphaBot's existing `.env` + `dotenv` pattern is fully compatible. The `anthropic` Python SDK reads `ANTHROPIC_API_KEY` from the environment automatically.

```python
import anthropic, os
from dotenv import load_dotenv
load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

`ANTHROPIC_API_KEY` is a new `.env` variable — an operator prerequisite, same posture as the Composer/Alpaca keys. Build + tests do not need it (fixture-based); only live operator use does.

---

## 2. Model Selection

**Recommendation: Claude Opus 4.7 (`claude-opus-4-7`).** The task is analytical reasoning over quantitative trading data — synthesizing Optuna trial metadata, math-engine parameters, and per-symphony data into coherent config suggestions with rationales. Reasoning-heavy, not high-volume; Opus's analytical depth justifies the cost.

**Cost-down alternative:** Claude Sonnet 4.6 (`claude-sonnet-4-6`) — adequate for the quant data but may miss nuanced parameter interactions.

Pricing (May 2026, per 1M tokens):
- Opus 4.7 — $5.00 input / $25.00 output
- Sonnet 4.6 — $3.00 / $15.00
- Haiku 4.5 — $1.00 / $5.00

---

## 3. Structured Output

**Recommendation: Structured Outputs via JSON schema (Pydantic), not raw tool use.**

Tool use (function calling) is designed for Claude to *invoke external functions*; this feature is output-shaping. Structured Outputs guarantee schema compliance at generation time — no `JSON.parse()` errors, no failed-validation retries, type-safe fields.

```python
from pydantic import BaseModel

class ConfigSuggestion(BaseModel):
    config_key: str
    current_value: float | int | str
    suggested_value: float | int | str
    rationale: str
    risk_direction: str       # loosens | tightens | neutral
    confidence: str
    data_sufficiency: str

class ConfigSuggestionsResponse(BaseModel):
    suggestions: list[ConfigSuggestion]

response = client.messages.parse(
    model="claude-opus-4-7",
    max_tokens=2048,
    output_format=ConfigSuggestionsResponse,
    messages=[...],
)
```

> **PM note:** the exact SDK method name / parameter (`messages.parse`, `output_format`) should be confirmed against the live SDK at build time — this is the C1 cycle's `test_live_claude_advisor.py` contract test's job.

---

## 4. Cost Per Call

Estimated input ~7K–11K tokens (quant context ~3–5K + Optuna metadata ~2–3K + per-symphony info ~2–3K). Output ~600 tokens.

- Input: ~9K tokens × $5/1M = **~$0.045**
- Output: ~600 tokens × $25/1M = **~$0.015**
- **Total: ~$0.06 per call.**

On-demand operator-assist: 10 clicks/day ≈ $0.60/day ≈ $150/month. Even 30 clicks/day is < $50/month. **Cost is not a blocker** — operator friction (latency) is the real constraint.

---

## 5. Prompt Caching

High ROI. The quant/Optuna context block is stable within an operator session; mark it `cache_control: {"type": "ephemeral"}` (5-minute TTL). Cached input tokens cost ~10% of normal. A 5-suggestion session drops from ~$0.30 to ~$0.06 — ~90% savings on the repeated context.

---

## 6. SDK vs Raw HTTP

**Recommendation: official `anthropic` Python SDK.** Composer/Alpaca use raw `requests` for minimal deps — but the Claude SDK's `messages.parse()` + Pydantic integration is the *intended* way to use Structured Outputs, and the SDK wraps auth, retry, and structured-output parsing cleanly. ~5MB dependency; AlphaBot already carries `flask`, `optuna`, `quantstats`. The ergonomics win.

---

## 7. Error Handling & Graceful Degradation

The feature is on-demand operator-assist — **failures are non-blocking.** A failed call means "no suggestion this click"; the operator just clicks again or decides manually. The engine, scheduler, and dashboard are never affected.

| Failure | Posture |
|---|---|
| Rate limit (429) | Single exponential backoff in the API-call layer; if it still fails, return a UI message. No retry at the Flask request level. |
| API outage (5xx) | Catch, log, return "Claude unavailable — try again." |
| Timeout (>30s) | `timeout=30` on the client; catch, return "Request timed out." |
| Malformed response | Structured Outputs make this rare; if it happens, log the raw response, return a UI error. |
| Missing/invalid API key | Fail fast with a clear message; the feature is simply unavailable until the key is set. |

Log all API errors to a dedicated log for monitoring. No retries at the UI/request level — retry/backoff belongs in the API-call layer only.

---

## Executive summary

1. **Model:** Opus 4.7 (Sonnet 4.6 as cost-down option).
2. **Structured output:** JSON-schema / Pydantic via the SDK — not tool use.
3. **Cost:** ~$0.06/call, ~$150/mo at 10 clicks/day — cost-trivial; prompt caching cuts repeat-call context ~90%.
4. **SDK:** official `anthropic` package over raw HTTP.
5. **Error posture:** log-but-don't-block; a failed call never degrades the engine.

---

## Sources

- Anthropic Messages API docs (accessed 2026-05-14)
- Anthropic Structured Outputs docs (accessed 2026-05-14)
- Anthropic Prompt Caching docs (accessed 2026-05-14)
- Anthropic / third-party pricing references (accessed 2026-05-14)

> All API-mechanic specifics (SDK method names, parameter shapes, current model IDs, pricing) must be re-verified against live docs + the live SDK at C1 build time — that is the `test_live_claude_advisor.py` contract test's purpose.
