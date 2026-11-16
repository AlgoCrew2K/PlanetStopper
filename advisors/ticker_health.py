"""Dead-ticker check, pure part: tree walk, Alpaca verdicts, stored results, page and Discord text.

No I/O: the runner reads Composer and Alpaca and writes the records this module judges and shows.
Advisory only; nothing here reaches an order, a trade or a Composer write.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from advisors.symphony_schema import extract_tickers
from analytics import format_et_stamp

RUN_ROLE = "TICKER_HEALTH_RUN"
ANNOUNCEMENT_ROLE = "TICKER_HEALTH_ANNOUNCEMENT"
LEAD_SENTENCE = (
    "These live symphonies reference tickers that can no longer be traded; "
    "fix each tree in Composer."
)
# A nightly check is overdue once its last completed run is older than a day and a half.
STALE_AFTER = timedelta(hours=36)
# Discord's documented limit on a message's ``content``, in characters.
DISCORD_CONTENT_LIMIT = 2000

_PLACEHOLDER_TICKER = "%"
_PLAIN_TICKER = re.compile(r"[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?")
_DISCORD_SPECIAL = frozenset("\\*_~`|<>#-[]()@:")
_LINE_BREAKS = re.compile(r"\r\n|\r|\n")
_ELLIPSIS = "..."
_HELD_TEXT = {"held": "Held now.", "not_held": "Not held now."}
_HELD_UNKNOWN_TEXT = "Holding unknown."
_NEVER_COMPLETED = "No ticker check has completed yet."


@dataclass(frozen=True)
class TickerUse:
    asset_nodes: int
    conditions: int


@dataclass(frozen=True)
class TickerVerdict:
    state: str
    reason: str | None = None


# ---------------------------------------------------------------------------
# Tree walk
# ---------------------------------------------------------------------------


def _legacy_operands(node: dict) -> set[str]:
    operands: set[str] = set()
    if isinstance(node.get("lhs-val"), str):
        operands.add(node["lhs-val"])
    if isinstance(node.get("rhs-val"), str) and node.get("rhs-fixed-value?") is not True:
        operands.add(node["rhs-val"])
    return operands


def _condition_operands(node: dict) -> set[str]:
    block = node.get("condition")
    if isinstance(block, dict):
        return extract_tickers({"condition": block})
    return _legacy_operands(node)


def tree_ticker_uses(tree) -> dict[str, TickerUse]:
    """Per ticker, the asset nodes that buy it and the condition nodes that compare it."""
    assets: dict[str, int] = {}
    conditions: dict[str, int] = {}
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        ticker = node.get("ticker")
        if (
            node.get("step") == "asset"
            and isinstance(ticker, str)
            and ticker != _PLACEHOLDER_TICKER
        ):
            assets[ticker] = assets.get(ticker, 0) + 1
        is_condition = not node.get("is-else-condition?") and (
            node.get("step") == "if-child" or isinstance(node.get("condition"), dict)
        )
        if is_condition:
            for operand in _condition_operands(node):
                conditions[operand] = conditions.get(operand, 0) + 1
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return {t: TickerUse(assets.get(t, 0), conditions.get(t, 0)) for t in {*assets, *conditions}}


def is_plain_ticker(text) -> bool:
    return isinstance(text, str) and _PLAIN_TICKER.fullmatch(text) is not None


def asset_class_covered(tree) -> bool:
    """False only when the tree names an asset class other than equities."""
    return not (
        isinstance(tree, dict) and "asset_class" in tree and tree["asset_class"] != "EQUITIES"
    )


# ---------------------------------------------------------------------------
# Alpaca answers
# ---------------------------------------------------------------------------

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404
_HTTP_RATE_LIMIT = 429
_HTTP_REFUSED = (401, 403)
_HTTP_SERVER_ERRORS = range(500, 600)


def _not_checked(reason: str) -> TickerVerdict:
    return TickerVerdict("not_checked", reason)


def _classify_asset_record(body) -> TickerVerdict:
    status = body.get("status") if isinstance(body, dict) else None
    tradable = body.get("tradable") if isinstance(body, dict) else None
    if status not in ("active", "inactive") or not isinstance(tradable, bool):
        return _not_checked("Alpaca response unreadable")
    inactive = status == "inactive"
    if inactive and not tradable:
        return TickerVerdict("dead", "inactive and not tradable at Alpaca")
    if not tradable:
        return TickerVerdict("dead", "not tradable at Alpaca")
    if inactive:
        return TickerVerdict("dead", "inactive at Alpaca")
    return TickerVerdict("healthy")


def classify_asset_answer(status: int, body) -> TickerVerdict:
    """One Alpaca asset answer: the status decides first, the body only on a 200."""
    if status == _HTTP_OK:
        return _classify_asset_record(body)
    if status == _HTTP_NOT_FOUND:
        return TickerVerdict("dead", "not listed at Alpaca")
    if status in _HTTP_REFUSED:
        return _not_checked("Alpaca refused the credentials")
    if status == _HTTP_RATE_LIMIT:
        return _not_checked("Alpaca rate limit")
    if status in _HTTP_SERVER_ERRORS:
        return _not_checked("Alpaca unavailable")
    return _not_checked("Alpaca rejected the request")


def classify_asset_failure() -> TickerVerdict:
    return _not_checked("Alpaca unreachable")


# ---------------------------------------------------------------------------
# Names, holdings, carry-forward, completion
# ---------------------------------------------------------------------------


def display_name(entry: dict) -> str:
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return entry["id"]


def held_state(holdings_fields: list, ticker: str) -> str:
    """ "held" on any match, "unknown" when any field cannot be read, else "not_held"."""
    unreadable = False
    for field in holdings_fields:
        if not isinstance(field, list):
            unreadable = True
            continue
        for entry in field:
            if not isinstance(entry, dict) or not isinstance(entry.get("ticker"), str):
                unreadable = True
            if isinstance(entry, dict) and ticker in (
                entry.get("ticker"),
                entry.get("working_ticker"),
            ):
                return "held"
    return "unknown" if unreadable else "not_held"


def _fresh_warning(symphony_id, entry, ticker, use, verdict, previous, ended_at) -> dict:
    return {
        "symphony_id": symphony_id,
        "display_name": entry["display_name"],
        "ticker": ticker,
        "reason": verdict.reason,
        "asset_nodes": use.asset_nodes,
        "conditions": use.conditions,
        "held": held_state(entry["holdings_fields"], ticker),
        "found_at": ended_at,
        "announced": bool(previous["announced"]) if previous else False,
    }


def carry_warnings(
    previous_warnings: list[dict],
    judged: dict,
    unjudged_symphonies,
    listed_ids,
    every_roster_read: bool,
    ended_at: str,
) -> list[dict]:
    """The warnings of the run being stored: fresh where judged dead, carried where not judged."""
    previous = {(w["symphony_id"], w["ticker"]): w for w in previous_warnings}
    stored: list[dict] = []
    for (symphony_id, _ticker), warning in previous.items():
        if symphony_id in judged:
            continue
        gone = symphony_id not in listed_ids and symphony_id not in unjudged_symphonies
        if not (gone and every_roster_read):
            stored.append(dict(warning))
    for symphony_id, entry in judged.items():
        for ticker, use in entry["uses"].items():
            verdict = entry["verdicts"].get(ticker)
            old = previous.get((symphony_id, ticker))
            if verdict is None or verdict.state == "not_checked":
                if old is not None:
                    stored.append(dict(old))
            elif verdict.state == "dead":
                stored.append(
                    _fresh_warning(symphony_id, entry, ticker, use, verdict, old, ended_at)
                )
    return stored


def is_completed(
    rosters_read: int,
    symphonies_listed: int,
    symphonies_reached: int,
    tickers_asked: int,
    tickers_answered: int,
) -> bool:
    return (
        rosters_read > 0
        and symphonies_reached >= symphonies_listed
        and (tickers_asked == 0 or tickers_answered > 0)
    )


# ---------------------------------------------------------------------------
# Page text
# ---------------------------------------------------------------------------


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _facts_text(warning: dict) -> str:
    held = _HELD_TEXT.get(warning["held"], _HELD_UNKNOWN_TEXT)
    return (
        f"is {warning['reason']}. Used by {_plural(warning['asset_nodes'], 'asset node')} "
        f"and {_plural(warning['conditions'], 'condition')}. {held}"
    )


def _warning_lines(warnings: list[dict], completed_record: dict, shown_run_is_latest: bool) -> list:
    ordered = sorted(
        warnings, key=lambda w: (w["display_name"].casefold(), w["symphony_id"], w["ticker"])
    )
    return [
        {
            "text": f"{w['display_name']}: {w['ticker']} {_facts_text(w)}",
            "from_check": None
            if shown_run_is_latest and w["found_at"] == completed_record["ended_at"]
            else format_et_stamp(w["found_at"]),
        }
        for w in ordered
    ]


def _not_checked_notices(not_checked: dict) -> list[str]:
    notices = [
        f"Not checked: symphonies of the {a['label']} account ({a['reason']})."
        for a in not_checked["accounts"]
    ]
    notices += [
        f"Not checked: {s['display_name']} ({s['reason']})." for s in not_checked["symphonies"]
    ]
    for entry in not_checked["tickers"]:
        if "count" in entry:
            notices.append(
                f"{_plural(entry['count'], 'ticker')} in {entry['display_name']} "
                f"could not be checked ({entry['reason']})."
            )
        else:
            notices.append(
                f"Not checked: {entry['ticker']} in {entry['display_name']} ({entry['reason']})."
            )
    return notices


def _run_notices(latest: dict, completed: dict | None) -> list[str]:
    status = latest.get("status")
    last = f"Last completed check {format_et_stamp(completed['ended_at'])}." if completed else None
    if status in ("unavailable", "skipped"):
        return [
            f"Ticker check unavailable: {latest['reason']}. {last or 'No completed check yet.'}"
        ]
    return [
        f"Ticker check failed on {format_et_stamp(latest['ended_at'])}. "
        f"{last or 'No completed check yet.'}"
    ]


def page_view(latest: dict | None, latest_completed: dict | None, now_utc: datetime) -> dict:
    """What the dashboard shows: the warning lines and the notices, plain text for the template."""
    latest = latest or latest_completed
    if latest is None:
        return {"warning": None, "notices": [_NEVER_COMPLETED]}
    latest_is_completed = latest.get("completed") is True
    notices: list[str] = []
    if latest_is_completed:
        notices += _not_checked_notices(latest["not_checked"])
    else:
        notices += _run_notices(latest, latest_completed)
    warning = None
    if latest_completed is not None:
        warnings = latest_completed["warnings"]
        if warnings:
            warning = {
                "checked": format_et_stamp(latest_completed["ended_at"]),
                "lines": _warning_lines(warnings, latest_completed, latest_is_completed),
            }
        ended = datetime.fromisoformat(latest_completed["ended_at"])
        if now_utc - ended > STALE_AFTER:
            notices.append(
                "No ticker check has completed since "
                f"{format_et_stamp(latest_completed['ended_at'])}."
            )
    return {"warning": warning, "notices": notices}


# ---------------------------------------------------------------------------
# Discord text
# ---------------------------------------------------------------------------


def discord_literal(text: str) -> str:
    """The text with markdown, link and mention syntax made literal by one backslash each."""
    return "".join("\\" + c if c in _DISCORD_SPECIAL else c for c in text)


def _discord_line(pair: dict, name: str) -> str:
    return f"{name} ({pair['symphony_id']}): {discord_literal(pair['ticker'])} {_facts_text(pair)}"


def _fit_line(pair: dict, room: int) -> str:
    raw_name = _LINE_BREAKS.sub(" ", pair["display_name"])
    line = _discord_line(pair, discord_literal(raw_name))
    if len(line) <= room:
        return line
    overflow = len(line) - room + len(_ELLIPSIS)
    # Cut the raw name, then escape, so a cut never separates a backslash from its character.
    full = len(discord_literal(raw_name))
    kept = raw_name
    while kept and full - len(discord_literal(kept)) < overflow:
        kept = kept[:-1]
    return _discord_line(pair, discord_literal(kept) + _ELLIPSIS)


def discord_messages(pairs: list[dict]) -> list[str]:
    """One or more messages, each within Discord's limit, naming every pair once in order."""
    room = DISCORD_CONTENT_LIMIT - len(LEAD_SENTENCE) - 1
    messages: list[str] = []
    current = ""
    for pair in pairs:
        line = _fit_line(pair, room)
        if current and len(current) + 1 + len(line) > room:
            messages.append(f"{LEAD_SENTENCE}\n{current}")
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        messages.append(f"{LEAD_SENTENCE}\n{current}")
    return messages
