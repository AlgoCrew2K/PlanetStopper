"""Guard-Alpha stop-justification preconditions (Kaminski & Lo 2014).

Stub -- implementation pending (TDD RED phase). See .claude/tdd-handoff.md.
"""

from __future__ import annotations

import dataclasses

# Kaminski, K.M. & Lo, A.W. (2014), "An Analysis of Trailing Stop-Loss
# Strategies", Journal of Financial Markets 18, 108-125.
# DOI 10.1016/j.finmar.2013.07.001 -- NOT the dead alo.mit.edu URL.
# PM-ASSUMED (feature plan AC-3), ~= synthetic_history._MC_WARMUP_TRADING_DAYS (39)
N_MIN_OBS: int = 40


@dataclasses.dataclass(frozen=True)
class PersistenceStats:
    rho: float
    rho_ci: float
    sharpe_daily: float
    n_obs: int
    insufficient_reason: str | None = None


def compute_persistence_stats(daily_returns: list) -> PersistenceStats:
    raise NotImplementedError("compute_persistence_stats: TDD RED phase stub")


def classify_stop_justification(stats: PersistenceStats) -> str:
    raise NotImplementedError("classify_stop_justification: TDD RED phase stub")


def verdict_copy(verdict: str) -> str:
    raise NotImplementedError("verdict_copy: TDD RED phase stub")
