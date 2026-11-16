"""TEST-ONLY HTTP router and builders for the ticker check runner and app suites.

Extends the Composer router (tests/advisors/_composer_http_router.py) so one object answers
every request the ticker check makes through ``requests``:

- Composer GETs of an account's symphony-stats-meta roster and a symphony's score tree,
- Alpaca paper-host ``GET /v2/assets/{symbol}`` asset reads,
- the Discord webhook ``POST``.

``requests.get``, ``requests.post`` and every other verb helper are replaced (by the base
router), together with ``requests.sessions.Session.request`` and ``time.sleep``. A request that
is none of those shapes (a Composer backtest or write, an Alpaca bars or asset-list read, a
non-GET asset request, an asset path with more than one segment, any other host) is recorded
in ``violations`` and answered 404, so a hidden write or data path shows in the request ledger.

Provenance of every reply body:

- Composer roster: the captured record ``tests/fixtures/composer/ticker-health/
  symphony_stats_meta_holdings_kept.json`` (README there: captured, trimmed).
- Composer score tree: the captured record ``score_8FAXAnQmYi1INDubazeC.json``. A symphony the
  test did not give its own tree is served this one (derived use, labelled: the id inside the
  tree is the captured one whatever id was asked).
- Alpaca asset records: the captured files under ``tests/fixtures/alpaca/assets_captured/``
  (SHNY inactive and not tradable, SPY and UGL active and tradable, ZZZZQQ 404, SPY 401 with a
  wrong key, AKAN active and not tradable). SHNY is served its captured record by default,
  because the captured tree names it and the real exchange reports it dead. Any other symbol a
  test did not script is served the captured SPY record with ``symbol`` and ``name`` replaced
  by the asked symbol (derived, labelled): every ticker of a captured tree must be answerable.
- Discord: 204 with an empty body, the success status of Discord's published webhook execute
  endpoint when ``wait`` is not set (schema-derived); 429 and 5xx forms are derived by the
  tests that use them (429 carries Discord's documented ``retry_after`` and ``global`` keys).

Trees the tests build (``tree_with``) are derived from the captured tree: the root of the
captured tree, its first weight node, a copy of its first asset node and a copy of its
SHNY ``if`` node (a legacy if-child with ``lhs-val``/``rhs-val``) with the ticker fields
edited. A derived tree is labelled by being built here.

Credentials in this module are invented markers in the real shape, so a leak shows as the
marker: a real key never appears in a test.

Not imported by production code.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from tests.advisors import _composer_http_router as h

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "composer" / "ticker-health"
ALPACA_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "alpaca" / "assets_captured"

ALPACA_HOST = "https://paper-api.alpaca.markets"
DISCORD_PREFIX = "https://discord.com/api/webhooks/"

# Invented credentials in the real shapes: an Alpaca paper key id (PK + 18) and a 40 character
# secret, and a Discord webhook path (18 digit id, long token). None is a real credential.
ALPACA_KEY = "PKX7Q2M9TZ4R8K1D5W3A"
ALPACA_SECRET = "kZ3mQ9vTt7LxP2aRb8Nc4WdYe6HgUj1SfOi5VlKz"
WEBHOOK_ID = "123456789012345678"
WEBHOOK_TOKEN = "Zk3Qm9vTt7LxP2aRb8Nc4WdYe6HgUj1SfOi5VlKz0XqBnMy3AwDrCpEhJtGuFsIo7aQe6Lm"
WEBHOOK_URL = f"{DISCORD_PREFIX}{WEBHOOK_ID}/{WEBHOOK_TOKEN}"

# The captured roster's symphonies the tests name.
PLANET_LQD_ID = "8FAXAnQmYi1INDubazeC"  # its captured tree buys and reads SHNY
TROLLING_ID = "n2ooAZTvBRN6ZzpMmWmU"  # the second live symphony that buys SHNY in the plan
CORPORATE_CHAOS_ID = "nOyb55RMGVCKPiYXv7TI"

# A night after a trading-day close: Monday 2026-11-16 20:30 EST. The market is closed.
NIGHT = datetime(2026, 11, 17, 1, 30, tzinfo=UTC)

_ASSET_RE = re.compile(r"^/v2/assets/(?P<symbol>[^/]+)$")
# One plain symbol segment, with at most one share-class dot.
PLAIN_SYMBOL = re.compile(r"[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?")


# ---------------------------------------------------------------------------
# Captured records and derived builders
# ---------------------------------------------------------------------------


def _load(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def captured_tree() -> dict:
    return _load(FIXTURE_DIR, "score_8FAXAnQmYi1INDubazeC.json")


def roster_body() -> dict:
    return _load(FIXTURE_DIR, "symphony_stats_meta_holdings_kept.json")


def roster_entry(symphony_id: str, **edits) -> dict:
    """One captured roster entry (deep copy); an edit of ``DROP`` removes that key."""
    entry = next(e for e in roster_body()["symphonies"] if e["id"] == symphony_id)
    for key, value in edits.items():
        if value is DROP:
            del entry[key]
        else:
            entry[key] = value
    return entry


DROP = object()


def roster_ids() -> list[str]:
    return [e["id"] for e in roster_body()["symphonies"]]


def roster_reply(*entries: dict) -> h.Reply:
    return h.Reply(body={"symphonies": list(entries)})


def alpaca_reply(name: str) -> h.Reply:
    """A reply carrying a captured Alpaca asset record's body and recorded status."""
    record = _load(ALPACA_DIR, name)
    return h.Reply(
        status=record["status"], body=record["body"], content_type=record.get("content_type")
    )


def shny_reply() -> h.Reply:
    return alpaca_reply("asset_SHNY_inactive_not_tradable.json")


def not_found_reply() -> h.Reply:
    return alpaca_reply("asset_ZZZZQQ_404_not_found.json")


def unauthorized_reply() -> h.Reply:
    return alpaca_reply("asset_SPY_401_wrong_key.json")


def active_not_tradable_reply() -> h.Reply:
    return alpaca_reply("asset_AKAN_active_not_tradable.json")


def healthy_reply(symbol: str) -> h.Reply:
    """The captured active and tradable SPY record with ``symbol`` and ``name`` replaced."""
    record = _load(ALPACA_DIR, "asset_SPY_active_tradable.json")
    body = copy.deepcopy(record["body"])
    body["symbol"] = symbol
    body["name"] = f"{symbol} captured-shape record"
    return h.Reply(status=200, body=body, content_type=record.get("content_type"))


def tree_with(
    assets=(), conditions=(), *, asset_class=None, branch_ticker="SPY", empty=False
) -> dict:
    """A tree derived from the captured one.

    ``assets``: tickers, one asset node each. ``conditions``: ``(lhs-val, rhs-val)`` pairs, one
    legacy ``if`` node each (copy of the captured SHNY ``if`` node) whose two branches each hold
    one asset node of ``branch_ticker``. ``asset_class`` sets the top-level key when given
    (``DROP`` removes it). ``empty`` builds the root with no children at all.
    """
    base = captured_tree()
    weight_template = copy.deepcopy(base["children"][0])
    asset_template = _first(base, lambda n: n.get("step") == "asset")
    if_template = _first(
        base,
        lambda n: (
            n.get("step") == "if" and any(c.get("lhs-val") == "SHNY" for c in n.get("children", []))
        ),
    )
    root = {k: v for k, v in base.items() if k != "children"}
    if asset_class is DROP:
        root.pop("asset_class", None)
    elif asset_class is not None:
        root["asset_class"] = asset_class
    if empty:
        root["children"] = []
        return root
    nodes = []
    for ticker in assets:
        node = copy.deepcopy(asset_template)
        node["ticker"] = ticker
        node["id"] = str(uuid.uuid4())
        node.pop("weight", None)
        nodes.append(node)
    for lhs, rhs in conditions:
        node = copy.deepcopy(if_template)
        node["id"] = str(uuid.uuid4())
        first, second = node["children"]
        first["lhs-val"], first["rhs-val"] = lhs, rhs
        for child in (first, second):
            child["id"] = str(uuid.uuid4())
            for asset in child["children"]:
                asset["ticker"] = branch_ticker
                asset["id"] = str(uuid.uuid4())
        nodes.append(node)
    weights = weight_template
    weights["id"] = str(uuid.uuid4())
    weights["children"] = nodes
    root["children"] = [weights]
    return root


def _first(tree: dict, predicate: Callable[[dict], bool]) -> dict:
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if predicate(node):
                return copy.deepcopy(node)
            stack.extend(node.get("children") or [])
    raise LookupError("no node of the captured tree matches")


def oracle_tree_uses(tree: dict) -> dict[str, tuple[int, int]]:
    """Independent count of each ticker's (asset nodes, conditions) in a legacy-form tree.

    Rules of the plan: an asset node adds one to its ticker; a non-else if-child adds one to
    each distinct ticker among its ``lhs-val`` and, unless ``rhs-fixed-value?`` is true, its
    ``rhs-val`` (a node naming one ticker on both sides counts once). Raises on a compound
    condition block, which this oracle does not model.
    """
    assets: Counter = Counter()
    conditions: Counter = Counter()
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("step") == "asset" and isinstance(node.get("ticker"), str):
            assets[node["ticker"]] += 1
        if node.get("step") == "if-child" and not node.get("is-else-condition?"):
            assert "condition" not in node, "the oracle models legacy operands only"
            named = set()
            if isinstance(node.get("lhs-val"), str):
                named.add(node["lhs-val"])
            if isinstance(node.get("rhs-val"), str) and node.get("rhs-fixed-value?") is not True:
                named.add(node["rhs-val"])
            for ticker in named:
                conditions[ticker] += 1
        stack.extend(node.get("children") or [])
    return {t: (assets[t], conditions[t]) for t in set(assets) | set(conditions)}


# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------


class SleepClock:
    """An aware UTC clock that moves only by the sleeps the router recorded since it was made."""

    def __init__(self, router: TickerHealthRouter, start: datetime) -> None:
        self._router = router
        self._start = start
        self._base = sum(router.sleeps)

    def __call__(self) -> datetime:
        return self._start + timedelta(seconds=sum(self._router.sleeps) - self._base)


class FlippingClock:
    """Reads ``before`` until the router has seen ``after_requests`` Composer requests, then
    ``after``: a market that opens between two reads, whatever the pacing sleeps add up to."""

    def __init__(
        self,
        router: TickerHealthRouter,
        before: datetime,
        after: datetime,
        *,
        after_requests: int,
    ) -> None:
        self._router = router
        self._before, self._after, self._count = before, after, after_requests

    def __call__(self) -> datetime:
        made = sum(1 for r in self._router.requests if r.kind in h.COMPOSER_KINDS)
        return self._after if made >= self._count else self._before


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------

DISCORD_NO_CONTENT = h.Reply(status=204, body=None)


class TickerHealthRouter(h.ComposerRouter):
    """Composer, Alpaca asset and Discord webhook replies, request ledger and sleeps."""

    def __init__(self) -> None:
        super().__init__()
        self.statistics_default = h.Reply(body=roster_body())
        self.definition_default = h.Reply(body=captured_tree())
        # The base router serves a tenure-review definition for one id; this router serves
        # the captured ticker-check tree to every id unless a test scripts that id.
        self.definition_by_symphony = {}
        # symbol -> one Reply (every attempt) or a list (attempt n gets item n, last repeats)
        # SHNY answers with its captured inactive record unless a test scripts it otherwise.
        self.alpaca_by_symbol: dict[str, h.Reply | list[h.Reply]] = {"SHNY": shny_reply()}
        self.alpaca_default: Callable[[str], h.Reply] = healthy_reply
        self.discord_replies: list[h.Reply] = []
        self.discord_default = DISCORD_NO_CONTENT

    # ---- scripting ------------------------------------------------------

    def set_tree(self, symphony_id: str, tree: dict | h.Reply) -> None:
        reply = tree if isinstance(tree, h.Reply) else h.Reply(body=tree)
        self.definition_by_symphony[symphony_id] = reply

    def set_roster(self, *entries: dict, account: str | None = None) -> None:
        reply = roster_reply(*entries)
        if account is None:
            self.statistics_default = reply
        else:
            self.statistics_by_account[account] = reply

    def set_alpaca(self, symbol: str, reply: h.Reply | list[h.Reply]) -> None:
        self.alpaca_by_symbol[symbol] = reply

    def set_all_alpaca(self, reply: h.Reply) -> None:
        self.alpaca_default = lambda symbol: copy.deepcopy(reply)

    def set_discord(self, *replies: h.Reply) -> None:
        self.discord_replies = list(replies)

    # ---- ledger views ---------------------------------------------------

    def asset_requests(self, symbol: str | None = None) -> list[h.Request]:
        return [
            r for r in self.of_kind("alpaca_asset") if symbol is None or r.match["symbol"] == symbol
        ]

    def asset_symbols(self) -> list[str]:
        return [r.match["symbol"] for r in self.of_kind("alpaca_asset")]

    def composer_requests(self) -> list[h.Request]:
        return [r for r in self.requests if r.kind in h.COMPOSER_KINDS]

    def discord_posts(self) -> list[dict]:
        return [r.body_json() for r in self.of_kind("discord")]

    def score_ids(self) -> list[str]:
        return [r.match["sid"] for r in self.of_kind("score")]

    # ---- dispatch -------------------------------------------------------

    def _classify(self, method: str, url: str, kwargs: dict):
        split = urlsplit(url)
        full = f"{split.scheme}://{split.netloc}{split.path}"
        query = dict(parse_qsl(split.query))
        for key, value in (kwargs.get("params") or {}).items():
            query[key] = str(value)
        if full.startswith(ALPACA_HOST):
            found = _ASSET_RE.match(split.path)
            if method == "GET" and found and not query:
                return "alpaca_asset", split.path, query, found.groupdict()
            return "unknown", split.path, query, {}
        if full.startswith(DISCORD_PREFIX):
            if method == "POST":
                return "discord", split.path, query, {"url": url}
            return "unknown", split.path, query, {}
        return super()._classify(method, url, kwargs)

    def _reply_for(self, request: h.Request) -> h.Reply:
        if request.kind == "alpaca_asset":
            symbol = request.match["symbol"]
            scripted = self.alpaca_by_symbol.get(symbol)
            if isinstance(scripted, list):
                attempt = len(self.asset_requests(symbol)) - 1
                return copy.deepcopy(scripted[min(attempt, len(scripted) - 1)])
            if scripted is not None:
                return copy.deepcopy(scripted)
            return self.alpaca_default(symbol)
        if request.kind == "discord":
            index = len(self.of_kind("discord")) - 1
            if index < len(self.discord_replies):
                return self.discord_replies[index]
            return self.discord_default
        return super()._reply_for(request)

    def sleeps_before_first_request(self) -> list[float]:
        out: list[float] = []
        for event in self.events:
            if event[0] == "request":
                break
            out.append(event[1])
        return out


def request_times(router: TickerHealthRouter, *, kinds=h.COMPOSER_KINDS) -> list[float]:
    """Seconds of recorded sleep elapsed before each request of ``kinds``, in order."""
    elapsed = 0.0
    times: list[float] = []
    for event in router.events:
        if event[0] == "sleep":
            elapsed += float(event[1])
        elif event[1].kind in kinds:
            times.append(elapsed)
    return times
