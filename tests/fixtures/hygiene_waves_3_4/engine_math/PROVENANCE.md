# engine_math reference records (hygiene-waves-3-4, cycle C1, Batch A)

Every record was produced at RED by `produce_ref_records.py`, which refuses unless
`alpha_bot_execution.py`, `math_engine.py`, `autotuner.py`, `regime_classifier.py`, `app.py`,
`database.py`, `analytics.py` and `sleeves/tick_orchestrator.py` equal AlphaBotPM@7adc2c56 and have no
uncommitted change, and `env_flags.py` does not exist. The producer runs `capture_ref_records.py`
under pytest `-n0` (so the `tests/conftest.py` isolation applies: temp state database, no network).
Producer, capture module and every record are tracked here. No record holds a credential, an account
id or a real symphony id: only math outputs, engine transitions on harness ids and route outcomes
(status or exception type).

| File | What it records | Inputs | Consumer |
|---|---|---|---|
| `zscore_ref_outputs.json` | the exact bits (`float.hex`) of `run_monte_carlo`, `compute_portfolio_cvar` and `compute_regime_match_quality` for five cases | seeded `numpy.random.default_rng` histories, a constant history (std 0.0 in both features) and the history builder of `tests/math_engine/test_mc_knn_standardization.py` driven by its captured fixture `tests/fixtures/math_engine/mc_knn_standardization/01_equal_return_opposite_vol_regime.json`; built by `tests/math_engine/_zscore_inputs.py` | `tests/math_engine/test_zscore_shared_definition.py` (S-3.1) |
| `engine_ref_sequences.json` | per tick: the decision-relevant `bot_state` projection, exit alerts sent and sell calls, for four dip/recovery/trigger sequences (labels none, trending, mean-reverting, high-vol) with live execution on and `execute_sell_to_cash` replaced by a recorder, and two take-profit sequences (Monte Carlo seamed) | real `alpha_bot_execution.main()` cycles through the harness of `tests/execution/test_regime_label_session_read.py`; sequences in `tests/execution/_dip_sequence.py` | `tests/execution/test_dip_line_confirmation_count.py` (S-5.4) |
| `env_flag_bare_key_outcomes.json` | the outcome (`["status", code]` or `["raised", exception type]`) of each unguarded flag site when the `.env` mapping holds `LIVE_EXECUTION` with no `=` | the drivers of `tests/app/_env_flag_sites.py` (`bare_key_outcome`); worlds `open_two_accounts` and `closed_frozen_two_accounts` of `tests/app/_state_poll_worlds.py` | `tests/app/test_env_flag_parse_sites.py` (S-3.10) |

Re-running the producer on a tree that is not the reference refuses; the records are therefore the
reference's, and a test that compares GREEN with them proves GREEN equals the reference.

`tests/test_hygiene_protected_surfaces.py` holds a second kind of reference value: the scope-pin
digests (each protected file with its listed edits cut), computed from `git show 7adc2c56:<path>`
and equal to the worktree file at RED.
