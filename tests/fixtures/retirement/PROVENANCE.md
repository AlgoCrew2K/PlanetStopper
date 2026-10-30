# Retirement run summary: real captured row

`run_summary_captured.json` holds every `RETIREMENT_RUN_SUMMARY` row of `advisor_observations` in the production
state database at 2026-10-22 21:08 UTC (one row, id 4025), exported read-only from the droplet exactly as stored
(`raw_response` is the stored JSON string; nothing was edited). It is the first summary written by the Cycle 2e
retirement recommender on real Composer data (source `composer`, 11 of 12 symphonies with a series, 10 of 55 pairs
over 0.65, 6 blocked by uncertainty, 4 by a stress window under 181 days, 0 recommendations).

The droplet clock is UTC (`timedatectl`: Etc/UTC), so the scheduler's 03:45 slot runs at 03:45 UTC.
