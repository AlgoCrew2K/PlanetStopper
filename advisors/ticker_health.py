"""RED stub: the import signature of the dead-ticker check's pure module, nothing else."""

from dataclasses import dataclass
from datetime import timedelta

RUN_ROLE = "TICKER_HEALTH_RUN"
ANNOUNCEMENT_ROLE = "TICKER_HEALTH_ANNOUNCEMENT"
LEAD_SENTENCE = ""
STALE_AFTER = timedelta(0)
DISCORD_CONTENT_LIMIT = 0


@dataclass(frozen=True)
class TickerUse:
    asset_nodes: int
    conditions: int


@dataclass(frozen=True)
class TickerVerdict:
    state: str
    reason: str | None = None


def _not_implemented(*args, **kwargs):
    raise NotImplementedError("Not implemented")


tree_ticker_uses = is_plain_ticker = asset_class_covered = _not_implemented
classify_asset_answer = classify_asset_failure = _not_implemented
display_name = held_state = carry_warnings = is_completed = _not_implemented
page_view = discord_literal = discord_messages = _not_implemented
