# Handoff: regime-label-producer round 2 RED (quant-test-writer rg-r2-red), RED SHA 9cd289b29391

Plan: feature-plans/regime-label-producer.md. Fixes qa.json blocking rows at 2107fc272ea2.

## Contract the tests pin
- In-session skip (PM ruling, QA-1/QA-3): `run_regime_label_job` started inside a trading session emits exactly ONE INFO record on logger `alphabot.regime_label`, in the summary format: `regime_label summary session=none seen=0 labelled=0 reason=session_in_progress`. No per-symphony record. Still no HTTP call, no write, returns `RegimeLabelRun(None)`. Change: `regime_label_job.py` `run_regime_label_job` (replace the `regime_label skipped reason=...` line); `_log_summary` takes a date today, so it needs a session-none form. The test reads keys (`session=none`, `seen=0`, `labelled=0`, `reason=session_in_progress`) and that "summary" is in the first two tokens.
- Take-profit independence (QA-2): no engine change. The new test passes today and fails when the take-profit threshold is scaled by the label's tick count (alpha_bot_execution.py:1570). Do not touch alpha_bot_execution.py; its sha256 pins stay.
- Test-only: QA-4 (TestS3_9 now compares an empty table with a real other-date row), QA-5 (S-4.3 test renamed).

## Files (run with -n0, explicit paths)
tests/regime_label_job/test_job_logging.py, tests/execution/test_regime_label_session_read.py; also run tests/regime_label_job/test_job_run.py, test_sip_fetch.py, test_target_session.py (they assert the no-call/no-write behaviour of the in-session run).
RED at this SHA: 1 failed (TestRunStartedInsideATradingSession), the rest pass.

## Ledger entries
- E-7 (QA-1/QA-3): method = test above in test_job_logging.py.
- E-8 (QA-2): method = TestS3_8TakeProfitConfirmationIsUntouchedByTheLabel in test_regime_label_session_read.py (green now; verified to fail on the planted F-3.8.2).
