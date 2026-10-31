"""Whole-file node harness for ``static/index.js`` on the dashboard ``/``.

What it does
------------
* Seeds a per-world SQLite database through the real writers
  (``database.save_state``, ``database.record_shadow_observation``,
  ``database.save_chart_history``), then captures what the real producers
  return for it through the Flask test client: ``GET /``, ``GET
  /api/hero-chart/<token>``, ``GET /api/chart/<id>`` and ``GET /api/state``.
  Nothing a producer returns is typed into a test.
* Parses the real server-rendered HTML into a tree and rebuilds it as a DOM
  stub in node, so the page the script runs against is the page the server
  renders, not a hand-made one.
* Runs the whole of ``static/index.js`` under node (``vm``) with a recording
  ``Chart`` stub, deferred ``fetch`` responses, ``setInterval`` and
  ``EventSource`` stubs, a token-map ``getComputedStyle`` and a controllable
  clock, then runs a scenario script against it.

Stub contract (what the stub does and does not claim)
-----------------------------------------------------
The ``Chart`` stub only counts constructor, ``destroy`` and ``update`` calls and
keeps the data it was given. It never claims a chart is visibly drawn: that is
judged on a live render. It reproduces one real Chart.js rule: constructing on
a canvas that still holds a live chart throws.

``H.renameLabels = true`` (set before boot) makes the stub append a suffix to every
dataset label at construction, so a test can show that nothing depends on label text.
``H.useTokenEpoch(label)`` makes every colour token read as ``<name>@<label>``.

Time is virtual and moves only when a scenario says so: ``H.runTimers(ms)`` advances the
page's ``Date`` and ``performance.now()`` together and runs every timer that falls due, in
order; ``H.sleepFor(ms, {perf})`` jumps the clocks with no timer running (a closed laptop; with
``perf: false`` only ``Date`` jumps, as on a platform whose monotonic clock stops in sleep) and
``H.wakeUp()`` then runs each overdue timer once. Neither claims a browser's real timer
throttling; they model the two clocks a page can read.

``animation.onComplete`` and plugin ``afterRender`` hooks fire only when a
scenario says so (``H.completeAnimations``, ``H.fireAfterRender``);
``Chart.animator.running(chart)`` answers from ``H.animating``, which holds
every chart constructed and not yet completed.

A missing ``node`` binary fails the test; it never skips.
"""

from __future__ import annotations

import ast
import contextlib
import datetime as _dt_mod
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
STATIC_DIR = _REPO / "static"
INDEX_JS = STATIC_DIR / "index.js"
TOKENS_CSS = STATIC_DIR / "tokens.css"
_BOT_STATE_CAPTURE = _REPO / "tests" / "fixtures" / "prod_droplet" / "bot_state_positions.json"

import app as _app_module  # noqa: E402
import database as _database_module  # noqa: E402


def _defined_in_module(function, module, name: str) -> bool:
    """True when ``function`` was compiled from ``module``'s own source under ``name``."""
    code = getattr(function, "__code__", None)
    if code is None or getattr(function, "__qualname__", None) != name:
        return False
    return Path(code.co_filename).resolve() == Path(module.__file__).resolve()


def _real_function(module, name: str):
    """The module's own function ``name``, whatever per-test stubs are installed now.

    tests/dashboard, tests/ui and tests/app conftests each replace some of these
    functions per test, and this module is imported lazily by tests in all three, so
    the attribute read at import may already be a stub. The current attribute is the
    real one only when it was compiled from this module's own file under this name:
    a plain function or lambda written by a conftest has another file and is replaced
    by the function compiled from the module's source, run against the live module
    namespace. A decorated target would run its decorators again on the rebuild, so
    it is refused.
    """
    current = getattr(module, name)
    if _defined_in_module(current, module, name):
        return current
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            if node.decorator_list:
                raise RuntimeError(
                    f"{module.__name__}.{name} is decorated; the rebuild would re-run its decorators"
                )
            isolated = ast.Module(body=[node], type_ignores=[])
            namespace = module.__dict__
            before = namespace.get(name)
            exec(compile(isolated, module.__file__, "exec", dont_inherit=True), namespace)  # noqa: S102
            rebuilt = namespace[name]
            namespace[name] = before
            return rebuilt
    raise RuntimeError(f"{module.__name__}.{name} has no top-level definition to rebuild")


_REAL_GET_API_STATE_DICT = _real_function(_app_module, "get_api_state_dict")
_REAL_DATABASE_LOAD_STATE = _real_function(_database_module, "load_state")

HERO_TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "all"]

# Real records of tests/fixtures/prod_droplet/bot_state_positions.json.
TRIGGERED_TP_ID = "lW4ZzWuqR8tEO2DhXbil"  # triggered, Take-Profit, 09:43
TRIGGERED_VWAP_ID = "qF5ZU7ALjrlhxrGEwsyJ"  # triggered, VWAP Breakdown, 10:11
UNTRIGGERED_ID = "iaSOOUsmnCJHiZvbrWfs"  # not triggered, mc_prob present
SYMPHONY_IDS = [TRIGGERED_TP_ID, TRIGGERED_VWAP_ID, UNTRIGGERED_ID]

HOSTILE_REASON = "</script><img src=x onerror=alert(1)>&amp;"

# First minute of the intraday tape and its length (09:30 .. 10:20 inclusive).
_TAPE_START_MINUTE = 9 * 60 + 30
_TAPE_MINUTES = 51


# ---------------------------------------------------------------------------
# HTML -> tree
# ---------------------------------------------------------------------------

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict | None = None
        self._stack: list[dict] = []

    def _open(self, tag: str, attrs) -> dict:
        node = {"t": tag, "a": {k: ("" if v is None else v) for k, v in attrs}, "c": []}
        if self._stack:
            self._stack[-1]["c"].append(node)
        elif self.root is None or tag == "html":
            self.root = node
        return node

    def handle_starttag(self, tag, attrs):
        node = self._open(tag, attrs)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["t"] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        if self._stack and data:
            self._stack[-1]["c"].append(data)


def parse_html(markup: str) -> dict:
    builder = _TreeBuilder()
    builder.feed(markup)
    builder.close()
    assert builder.root is not None, "server render produced no <html> element"
    return builder.root


def tree_text(node) -> str:
    if isinstance(node, str):
        return node
    return "".join(tree_text(c) for c in node["c"])


def find_all(node, predicate) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, str):
        return found
    if predicate(node):
        found.append(node)
    for child in node["c"]:
        found.extend(find_all(child, predicate))
    return found


def find_by_id(tree: dict, element_id: str) -> dict | None:
    hits = find_all(tree, lambda n: n["a"].get("id") == element_id)
    return hits[0] if hits else None


def initial_data_block(tree: dict) -> dict:
    """The parsed JSON of the page's embedded data block (no script runs)."""
    node = find_by_id(tree, "dashboard-initial-data")
    assert node is not None, (
        'the server render holds no element with id="dashboard-initial-data" '
        "(the embedded hero series and exit markers the page script reads at load)"
    )
    assert node["t"] == "script" and node["a"].get("type") == "application/json", (
        'the embedded data block must be a non-executable <script type="application/json"> element'
    )
    return json.loads(tree_text(node))


# ---------------------------------------------------------------------------
# Design tokens, parsed from static/tokens.css at test time
# ---------------------------------------------------------------------------


def load_tokens() -> dict:
    css = re.sub(r"/\*.*?\*/", "", TOKENS_CSS.read_text(encoding="utf-8"), flags=re.S)
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = match.group(1).strip()
        decls = {
            name.strip(): value.strip()
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(2))
        }
        if selector == ":root":
            light.update(decls)
        elif selector == '[data-theme="dark"]':
            dark.update(decls)
    return {"light": light, "darkOverrides": dark}


# ---------------------------------------------------------------------------
# Worlds: seeded database -> real producer output
# ---------------------------------------------------------------------------

_WORLD_CACHE: dict[str, dict] = {}


def _world_specs() -> dict[str, dict]:
    return {
        # 160 daily rows before today plus today's intraday tape, then one more
        # day and one more intraday point for the "next refresh" captures.
        "default": {
            "history_offsets": list(range(160, 0, -1)),
            "intraday": True,
            "with_next": True,
        },
        # one symphony has no intraday rows at all (empty /api/chart data).
        "mixed": {
            "history_offsets": list(range(40, 0, -1)),
            "intraday": True,
            "empty_intraday": {UNTRIGGERED_ID},
        },
        # today's rows only: fewer than two trading days, so no portfolio history.
        "empty": {"history_offsets": [], "intraday": True},
        # two trading days far apart: the default 30d window holds exactly one.
        "onepoint": {"history_offsets": [100], "intraday": True},
        # explicit dates across a year end, clock frozen at 2 January.
        "ytd": {
            "explicit_dates": ["2026-12-29", "2026-12-30", "2026-12-31", "2027-01-02"],
            "intraday": False,
            "frozen_today": _dt_mod.date(2027, 1, 2),
        },
        # 1 January: the year-to-date window holds no row yet (explicit dates end 31 December).
        "ytd-jan1": {
            "explicit_dates": ["2026-12-29", "2026-12-30", "2026-12-31"],
            "intraday": False,
            "frozen_today": _dt_mod.date(2027, 1, 1),
        },
        # a stored exit reason that would break out of a script element.
        "hostile": {"history_offsets": [3, 2, 1], "intraday": False, "hostile_reason": True},
    }


def get_world(name: str) -> dict:
    """Build (once per process) and return the named world."""
    if name not in _WORLD_CACHE:
        _WORLD_CACHE[name] = _build_world(name)
    return _WORLD_CACHE[name]


def _daily_return(sym_index: int, day_index: int) -> float:
    return round(0.6 * math.sin(0.7 * day_index + 1.3 * sym_index), 4)


def _tape_return(sym_index: int, minute_index: int) -> float:
    return round(0.4 * math.sin(minute_index / 6.0 + sym_index) + 0.01 * minute_index, 4)


def _hhmm(minute_index: int) -> str:
    total = _TAPE_START_MINUTE + minute_index
    return f"{total // 60:02d}:{total % 60:02d}"


def _trigger_minute(record: dict) -> int | None:
    stamp = record.get("triggered_at_time")
    if not record.get("triggered") or not stamp:
        return None
    hours, minutes = stamp.split(":")
    return int(hours) * 60 + int(minutes) - _TAPE_START_MINUTE


def _seed_shadow_row(database, sym_id, record, day, hhmm, current, shadow, post_trigger):
    database.record_shadow_observation(
        symphony_id=sym_id,
        account_id=record.get("account"),
        cycle_id=f"cycle-{day}T{hhmm}",
        ts_utc=f"{day}T{hhmm}:00Z",
        ts_et=f"{day}T{hhmm}:00",
        trading_day=day,
        current_return=current,
        shadow_return=shadow,
        is_post_trigger=1 if post_trigger else 0,
        trigger_id=None,
        position_epoch="epoch-a",
    )


def _tape_points(sym_index: int, record: dict, count: int) -> list[dict]:
    """Chart-history points in the shape the engine appends each cycle."""
    points = []
    for minute in range(count):
        value = _tape_return(sym_index, minute)
        points.append(
            {
                "time": _hhmm(minute),
                "return": value,
                "stop": -1.5,
                "event": None,
                "mc_prob": None if record.get("triggered") else record.get("mc_prob"),
                "vol": 1.2,
                "vwap_diff": 0.0,
                "base_atr_pct": 0.0,
                "dynamic_multiplier": 1.0,
                "breakeven": None,
                "vwap": value - 0.1,
            }
        )
    return points


def _seed_tape(database, bot_state, empty_ids, count, today):
    chart_history = {"date": today, "symphonies": {}}
    for sym_index, sym_id in enumerate(SYMPHONY_IDS):
        if sym_id in empty_ids:
            continue
        record = bot_state[sym_id]
        triggered_at = _trigger_minute(record)
        frozen = None
        for minute in range(count):
            current = _tape_return(sym_index, minute)
            post = triggered_at is not None and minute > triggered_at
            if triggered_at is not None and minute == triggered_at:
                frozen = current
            shadow = frozen if post else current
            _seed_shadow_row(database, sym_id, record, today, _hhmm(minute), current, shadow, post)
        chart_history["symphonies"][sym_id] = _tape_points(sym_index, record, count)
    database.save_chart_history(chart_history)


@contextlib.contextmanager
def _clock_on(day):
    """``datetime.datetime.now()`` reads as 15:00 on ``day`` (the host's own clock when None)."""
    original_datetime = _dt_mod.datetime
    if day is None:
        yield
        return

    class _FrozenDateTime(original_datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(day.year, day.month, day.day, 15, 0, 0)
            return base.replace(tzinfo=tz) if tz is not None else base

    _dt_mod.datetime = _FrozenDateTime  # analytics reads datetime.datetime at call time
    try:
        yield
    finally:
        _dt_mod.datetime = original_datetime


def _capture(client, frozen_today) -> dict:
    """What the real producers return for the seeded database right now, as of
    ``frozen_today`` when given (the page, the windows and the poll share that date)."""
    out: dict = {}
    with _clock_on(frozen_today):
        page = client.get("/")
        assert page.status_code == 200, (
            f"GET / -> {page.status_code}: {page.get_data(as_text=True)[:600]}"
        )
        out["html"] = page.get_data(as_text=True)

        hero: dict = {}
        for token in HERO_TOKENS:
            response = client.get(f"/api/hero-chart/{token}")
            assert response.status_code == 200, f"/api/hero-chart/{token} -> {response.status_code}"
            hero[token] = response.get_json()
        out["hero"] = hero

        charts = {}
        for sym_id in SYMPHONY_IDS:
            response = client.get(f"/api/chart/{sym_id}")
            assert response.status_code == 200, f"/api/chart/{sym_id} -> {response.status_code}"
            charts[sym_id] = response.get_json()
        out["charts"] = charts

        state = client.get("/api/state")
        assert state.status_code == 200, (
            f"/api/state -> {state.status_code}: {state.get_data(as_text=True)[:600]}"
        )
        out["state"] = state.get_json()

        strips: dict = {}
        summaries: dict = {}
        for token in HERO_TOKENS:
            strip = client.get(f"/api/strip/{token}")
            assert strip.status_code == 200, f"/api/strip/{token} -> {strip.status_code}"
            strips[token] = strip.get_json()
            summary = client.get(f"/api/guard-alpha-summary?window={token}")
            assert summary.status_code == 200, (
                f"/api/guard-alpha-summary?window={token} -> {summary.status_code}"
            )
            summaries[token] = summary.get_json()
        out["strips"] = strips
        out["summaries"] = summaries
    return out


def _build_world(name: str) -> dict:
    spec = _world_specs()[name]
    import analytics
    import database

    records = json.loads(_BOT_STATE_CAPTURE.read_text(encoding="utf-8"))
    bot_state = {sym_id: dict(records[sym_id]) for sym_id in SYMPHONY_IDS}
    # Named-field edit of a real record: the capture omits the engine's arming flags,
    # and only an active (armed or triggered) card carries an MC dial in the page.
    bot_state[UNTRIGGERED_ID]["armed"] = True
    if spec.get("hostile_reason"):
        # Named-field edit of a real record: the stored exit reason.
        bot_state[TRIGGERED_VWAP_ID]["triggered_reason"] = HOSTILE_REASON

    work_dir = tempfile.mkdtemp(prefix=f"charts_world_{name}_")
    db_path = os.path.join(work_dir, "world.db")
    env_file = os.path.join(work_dir, "empty.env")
    Path(env_file).write_text("", encoding="utf-8")

    patch = pytest.MonkeyPatch()
    try:
        patch.setenv("DB_PATH", db_path)
        for key in (
            "LIVE_EXECUTION",
            "EXIT_AUTHORITY",
            "EXECUTION_START_TIME",
            "ACCOUNT_ROTH",
            "ACCOUNT_INDIVIDUAL",
            "ACCOUNT_TRAD",
            "COMPOSER_KEY_ID",
            "COMPOSER_SECRET",
        ):
            patch.delenv(key, raising=False)
        patch.setattr(_app_module, "ENV_FILE_PATH", env_file)
        patch.setattr(analytics, "DB_FILE", db_path)
        patch.setattr(_app_module, "get_api_state_dict", _REAL_GET_API_STATE_DICT)
        patch.setattr(database, "load_state", _REAL_DATABASE_LOAD_STATE)
        patch.setattr(_app_module, "get_market_state", lambda *_a, **_k: "open")
        database.init_db()
        database.save_state(bot_state)

        today = _dt_mod.datetime.now().strftime("%Y-%m-%d")  # /api/chart's own "today"
        if "explicit_dates" in spec:
            days = list(spec["explicit_dates"])
        else:
            days = [
                (_dt_mod.date.fromisoformat(today) - _dt_mod.timedelta(days=k)).isoformat()
                for k in spec["history_offsets"]
            ]
        trigger_day_index = int(len(days) * 0.7)
        for day_index, day in enumerate(days):
            for sym_index, sym_id in enumerate(SYMPHONY_IDS):
                record = bot_state[sym_id]
                current = _daily_return(sym_index, day_index)
                post = bool(record.get("triggered")) and day_index >= trigger_day_index
                shadow = 0.0 if post else current
                _seed_shadow_row(database, sym_id, record, day, "16:00", current, shadow, post)
        if spec.get("intraday"):
            _seed_tape(database, bot_state, spec.get("empty_intraday", set()), _TAPE_MINUTES, today)

        _app_module.app.config["TESTING"] = True
        client = _app_module.app.test_client()
        # The rows are seeded by the host-local date (/api/chart reads local "today") but the
        # calendar windows cut by the UTC date, and the two dates differ for part of every
        # day on a host away from UTC. Every capture is therefore taken as if the UTC date
        # were the seeded day, and ``seeded_now_iso`` pins the page's clock to that same day,
        # so the series, the captures and the script share one date at every hour.
        seeded_today = spec.get("frozen_today") or _dt_mod.date.fromisoformat(today)
        world = _capture(client, seeded_today)
        world["seeded_today"] = seeded_today.isoformat()
        world["spec_name"] = name
        world["ids"] = list(SYMPHONY_IDS)
        world["bot_state"] = bot_state

        if spec.get("with_next"):
            tomorrow = (_dt_mod.date.fromisoformat(today) + _dt_mod.timedelta(days=1)).isoformat()
            day_index = len(days)
            for sym_index, sym_id in enumerate(SYMPHONY_IDS):
                record = bot_state[sym_id]
                current = _daily_return(sym_index, day_index)
                post = bool(record.get("triggered"))
                _seed_shadow_row(
                    database,
                    sym_id,
                    record,
                    tomorrow,
                    "16:00",
                    current,
                    0.0 if post else current,
                    post,
                )
            _seed_tape(database, bot_state, set(), _TAPE_MINUTES + 1, today)
            world["next"] = _capture(client, seeded_today)
            world["next"].pop("html")
    finally:
        patch.undo()
    return world


# ---------------------------------------------------------------------------
# Running a scenario under node
# ---------------------------------------------------------------------------


def node_binary() -> str:
    path = shutil.which("node")
    if path is None:
        pytest.fail("node is not installed: the whole-file harness for static/index.js needs it")
    return path


def run_scenario(
    world: dict,
    scenario: str,
    *,
    extra_bodies: dict | None = None,
    now_iso: str | None = None,
    timeout: int = 120,
    scripts: tuple[str, ...] = ("static/index.js",),
) -> dict:
    """Run ``scenario`` (the body of an async JS function taking ``H``) against
    the world's page and return its JSON result.

    ``scripts`` names the files loaded into the one page context, in page order
    (``("static/chrome.js", "static/index.js")`` is how ``/`` loads them); the
    default keeps every existing scenario on ``static/index.js`` alone.

    ``H.bodies`` carries ``hero`` (token -> /api/hero-chart body), ``charts``
    (id -> /api/chart body), ``state`` (the /api/state body), ``strips`` (token ->
    /api/strip body), ``summaries`` (token -> /api/guard-alpha-summary body), their
    ``*Next`` captures when the world has them, and anything in ``extra_bodies``.
    """
    bodies = {
        "hero": world["hero"],
        "charts": world["charts"],
        "state": world["state"],
        "strips": world["strips"],
        "summaries": world["summaries"],
    }
    if "next" in world:
        bodies["heroNext"] = world["next"]["hero"]
        bodies["chartsNext"] = world["next"]["charts"]
        bodies["stateNext"] = world["next"]["state"]
        bodies["stripsNext"] = world["next"]["strips"]
        bodies["summariesNext"] = world["next"]["summaries"]
    bodies.update(extra_bodies or {})
    payload = {
        "tree": parse_html(world["html"]),
        "bodies": bodies,
        "tokens": load_tokens(),
        "scripts": [str(_REPO / name) for name in scripts],
        "scenario": scenario,
        "now": now_iso,
    }
    work_dir = tempfile.mkdtemp(prefix="charts_run_")
    world_path = os.path.join(work_dir, "world.json")
    runtime_path = os.path.join(work_dir, "runtime.js")
    Path(world_path).write_text(json.dumps(payload), encoding="utf-8")
    Path(runtime_path).write_text(_RUNTIME_JS, encoding="utf-8")
    try:
        proc = subprocess.run(
            [node_binary(), runtime_path, world_path],
            capture_output=True,
            text=True,
            # node writes UTF-8; the host's locale codec (cp1252 on Windows) would turn
            # U+2014 into mojibake. "replace" keeps a stray byte from masking the real failure.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    marker = "__HARNESS_RESULT__"
    assert marker in proc.stdout, (
        f"node harness produced no result (exit {proc.returncode}).\n"
        f"stderr: {proc.stderr[-2000:]}\nstdout: {proc.stdout[-1500:]}"
    )
    result = json.loads(proc.stdout.split(marker, 1)[1].strip().splitlines()[0])
    assert result.get("error") is None, f"scenario threw inside node:\n{result['error']}"
    return result


def seeded_now_iso(world: dict) -> str:
    """A UTC instant on the day the world's rows were seeded for, for ``now_iso``.

    The calendar cutoffs take the UTC date and a world is seeded by the host-local date.
    Pinning the page's clock to the seeded day (``_build_world`` captures as of that day too)
    keeps both on one date at every hour, where the real UTC now is a day past the newest row
    for part of each evening west of UTC."""
    return f"{world['seeded_today']}T12:00:00.000Z"


def window_cutoff_iso(token: str, today: _dt_mod.date | None = None) -> str | None:
    """``analytics._window_cutoff_date`` as an ISO string (None for lifetime)."""
    import analytics

    if today is None:
        cutoff = analytics._window_cutoff_date(token)
    else:
        if token == "all":
            return None
        if token == "ytd":
            return _dt_mod.date(today.year, 1, 1).isoformat()
        cutoff = today - _dt_mod.timedelta(days=analytics._WINDOW_TRAILING_DAYS[token])
    return cutoff.isoformat() if cutoff is not None else None


def rebase_to_window(dates: list[str], cumulative: list[float], cutoff_iso: str | None):
    """A lifetime-cumulative percent series cut to a calendar window and
    rebased so it compounds from 1.0 at the window's first day: the shape
    ``/api/hero-chart`` returns (its first point is the first day's return)."""
    idx = [i for i, d in enumerate(dates) if cutoff_iso is None or d >= cutoff_iso]
    if not idx:
        return [], []
    first = idx[0]
    base = 0.0 if first == 0 else cumulative[first - 1]
    out = [((1 + cumulative[i] / 100.0) / (1 + base / 100.0) - 1) * 100.0 for i in idx]
    return [dates[i] for i in idx], out


def poll_hist(state: dict) -> tuple[list[str], list[float], list[float]]:
    portfolio = state["meta"]["portfolio"]
    return portfolio["hist_dates"], portfolio["hist_bot"], portfolio["hist_held"]


def assert_series_close(actual: list[float], expected: list[float], tol: float, what: str) -> None:
    assert len(actual) == len(expected), f"{what}: {len(actual)} points, expected {len(expected)}"
    for i, (a, b) in enumerate(zip(actual, expected)):
        assert abs(a - b) <= tol, f"{what}: point {i} is {a}, expected {b} (tolerance {tol})"


# ---------------------------------------------------------------------------
# The node runtime
# ---------------------------------------------------------------------------

_RUNTIME_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const world = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const H = {
  world: world, bodies: world.bodies,
  fetchLog: [], routes: [], pending: [], unrouted: [], intervals: [], timeouts: [],
  eventSources: [], charts: [], animating: new Set(), globalPlugins: [],
  console: { errors: [], logs: [] }, unhandled: [],
  tokens: Object.assign({}, world.tokens.light), accentOverride: null, theme: 'light',
  reloads: 0, navigations: [], streamAutoOpen: true, failNewStreams: false
};
let timerSeq = 0;
process.on('unhandledRejection', function (e) { H.unhandled.push(String((e && e.message) || e)); });

function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
function camel(s) { return s.replace(/-([a-z])/g, function (m, c) { return c.toUpperCase(); }); }

// ------------------------------------------------------------------ DOM
class Emitter {
  _ls(type) { this._listeners = this._listeners || {}; return this._listeners[type] || (this._listeners[type] = []); }
  addEventListener(type, fn) { this._ls(type).push(fn); }
  removeEventListener(type, fn) { const a = this._ls(type); const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); }
  dispatchEvent(evt) {
    if (!evt.target) evt.target = this;
    const list = this._ls(evt.type).slice();
    for (let i = 0; i < list.length; i++) list[i].call(this, evt);
    return true;
  }
}
class TextNode {
  constructor(data) { this.nodeType = 3; this.data = data; this.parentNode = null; }
  get textContent() { return this.data; }
}
function makeStyle() {
  const s = {};
  Object.defineProperty(s, 'setProperty', { value: function (k, v) { s[k] = v; }, enumerable: false });
  Object.defineProperty(s, 'getPropertyValue', { value: function (k) { return s[k] === undefined ? '' : s[k]; }, enumerable: false });
  Object.defineProperty(s, 'removeProperty', { value: function (k) { delete s[k]; }, enumerable: false });
  return s;
}
function parseStyle(style, text) {
  text.split(';').forEach(function (decl) {
    const i = decl.indexOf(':');
    if (i < 0) return;
    const k = decl.slice(0, i).trim(); const v = decl.slice(i + 1).trim();
    if (k) style[k.indexOf('--') === 0 ? k : camel(k)] = v;
  });
}
function classTokens(el) { return (el._attrs.get('class') || '').split(/\s+/).filter(Boolean); }

const CTX_METHODS = ['save', 'restore', 'beginPath', 'closePath', 'moveTo', 'lineTo', 'stroke', 'fill', 'arcTo',
  'arc', 'rect', 'fillRect', 'clearRect', 'setLineDash', 'fillText', 'strokeText', 'clip', 'translate', 'scale', 'rotate'];
function makeCtx() {
  const log = []; const props = {};
  return new Proxy({}, {
    get: function (t, p) {
      if (p === 'log') return log;
      if (p === 'measureText') return function (txt) { return { width: String(txt).length * 5 }; };
      if (CTX_METHODS.indexOf(p) >= 0) return function () { log.push({ op: p, args: Array.prototype.slice.call(arguments) }); };
      return props[p];
    },
    set: function (t, p, v) { props[p] = v; log.push({ op: 'set', prop: p, value: v }); return true; }
  });
}

class Element extends Emitter {
  constructor(tag) {
    super();
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.localName = tag.toLowerCase();
    this._attrs = new Map(); this.childNodes = []; this.parentNode = null;
    this.style = makeStyle(); this._ctx = null; this.onclick = null; this.value = '';
  }
  get children() { return this.childNodes.filter(function (n) { return n.nodeType === 1; }); }
  get parentElement() { return this.parentNode; }
  get firstChild() { return this.childNodes[0] || null; }
  get id() { return this._attrs.get('id') || ''; }
  set id(v) { this._attrs.set('id', String(v)); }
  get className() { return this._attrs.get('class') || ''; }
  set className(v) { this._attrs.set('class', String(v)); }
  get hidden() { return this._attrs.has('hidden'); }
  set hidden(v) { if (v) this._attrs.set('hidden', ''); else this._attrs.delete('hidden'); }
  get disabled() { return this._attrs.has('disabled'); }
  set disabled(v) { if (v) this._attrs.set('disabled', ''); else this._attrs.delete('disabled'); }
  get title() { return this._attrs.get('title') || ''; }
  set title(v) { this._attrs.set('title', String(v)); }
  get classList() {
    const el = this;
    function write(set) { el._attrs.set('class', Array.from(set).join(' ')); }
    return {
      add: function () { const s = new Set(classTokens(el)); Array.prototype.forEach.call(arguments, function (c) { s.add(c); }); write(s); },
      remove: function () { const s = new Set(classTokens(el)); Array.prototype.forEach.call(arguments, function (c) { s.delete(c); }); write(s); },
      toggle: function (c, force) {
        const s = new Set(classTokens(el)); const has = s.has(c);
        const want = force === undefined ? !has : !!force;
        if (want) s.add(c); else s.delete(c);
        write(s); return want;
      },
      contains: function (c) { return classTokens(el).indexOf(c) >= 0; }
    };
  }
  get dataset() {
    const el = this;
    return new Proxy({}, {
      get: function (t, p) { if (typeof p !== 'string') return undefined; const v = el._attrs.get('data-' + kebab(p)); return v === undefined ? undefined : v; },
      set: function (t, p, v) { el._attrs.set('data-' + kebab(p), String(v)); return true; },
      has: function (t, p) { return el._attrs.has('data-' + kebab(String(p))); },
      deleteProperty: function (t, p) { el._attrs.delete('data-' + kebab(String(p))); return true; }
    });
  }
  getAttribute(n) { return this._attrs.has(n) ? this._attrs.get(n) : null; }
  setAttribute(n, v) { this._attrs.set(n, String(v)); if (n === 'style') parseStyle(this.style, String(v)); }
  removeAttribute(n) { this._attrs.delete(n); }
  hasAttribute(n) { return this._attrs.has(n); }
  get textContent() { return this.childNodes.map(function (c) { return c.textContent; }).join(''); }
  set textContent(v) {
    this.childNodes.forEach(function (c) { c.parentNode = null; });
    this.childNodes = [];
    const s = v === null || v === undefined ? '' : String(v);
    if (s !== '') { const t = new TextNode(s); t.parentNode = this; this.childNodes.push(t); }
  }
  get innerHTML() { return this._innerHTML || ''; }
  set innerHTML(v) { this.textContent = ''; this._innerHTML = String(v); }
  get outerHTML() { return '<' + this.localName + '>' + this.textContent + '</' + this.localName + '>'; }
  insertAdjacentHTML() {}
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  append() { const self = this; Array.prototype.forEach.call(arguments, function (n) { self.appendChild(typeof n === 'string' ? new TextNode(n) : n); }); }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    n.parentNode = this;
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
  }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) this.childNodes.splice(i, 1); n.parentNode = null; return n; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) { for (let e = n; e; e = e.parentNode) if (e === this) return true; return false; }
  querySelectorAll(sel) { return queryAll(this, sel); }
  querySelector(sel) { return queryAll(this, sel)[0] || null; }
  closest(sel) { for (let e = this; e && e.nodeType === 1; e = e.parentNode) if (matchesSel(e, sel)) return e; return null; }
  matches(sel) { return matchesSel(this, sel); }
  click() {
    const evt = { type: 'click', target: this, currentTarget: this, stopPropagation: function () {}, preventDefault: function () {} };
    if (typeof this.onclick === 'function') this.onclick.call(this, evt);
    this.dispatchEvent(evt);
  }
  getContext() { if (!this._ctx) this._ctx = makeCtx(); return this._ctx; }
  focus() {} blur() {} scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
}

// ---- selectors: compound selectors joined by descendant (space) and child (>) combinators
function splitTop(str, sepTest) {
  const out = []; let cur = ''; let depth = 0; let quote = null;
  for (let i = 0; i < str.length; i++) {
    const c = str[i];
    if (quote) { cur += c; if (c === quote) quote = null; continue; }
    if (c === '"' || c === "'") { quote = c; cur += c; continue; }
    if (c === '[') depth++;
    if (c === ']') depth--;
    if (depth === 0 && sepTest(c)) { out.push(cur); cur = ''; continue; }
    cur += c;
  }
  out.push(cur);
  return out;
}
const selCache = {};
function parseSelector(sel) {
  if (selCache[sel]) return selCache[sel];
  const complexes = splitTop(sel, function (c) { return c === ','; }).map(function (s) { return s.trim(); }).filter(Boolean).map(function (cx) {
    const parts = []; let comb = ' ';
    const tokens = splitTop(cx.replace(/\s*>\s*/g, ' > '), function (c) { return /\s/.test(c); }).filter(Boolean);
    tokens.forEach(function (tok) {
      if (tok === '>') { comb = '>'; return; }
      parts.push({ comb: comb, comp: parseCompound(tok) }); comb = ' ';
    });
    return parts;
  });
  selCache[sel] = complexes;
  return complexes;
}
function parseCompound(tok) {
  const comp = { tag: null, id: null, classes: [], attrs: [] };
  let m = tok.match(/^(\*|[a-zA-Z][\w-]*)/);
  let rest = tok;
  if (m) { comp.tag = m[1] === '*' ? null : m[1].toLowerCase(); rest = tok.slice(m[0].length); }
  const re = /#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*)))?\s*\]/g;
  let mm;
  while ((mm = re.exec(rest)) !== null) {
    if (mm[1]) comp.id = mm[1];
    else if (mm[2]) comp.classes.push(mm[2]);
    else comp.attrs.push({ name: mm[3], op: mm[4] || null, value: mm[5] !== undefined ? mm[5] : (mm[6] !== undefined ? mm[6] : mm[7]) });
  }
  return comp;
}
function matchCompound(el, comp) {
  if (comp.tag && el.localName !== comp.tag) return false;
  if (comp.id && el.id !== comp.id) return false;
  for (let i = 0; i < comp.classes.length; i++) if (classTokens(el).indexOf(comp.classes[i]) < 0) return false;
  for (let i = 0; i < comp.attrs.length; i++) {
    const a = comp.attrs[i]; const v = el.getAttribute(a.name);
    if (v === null) return false;
    if (a.op === '=' && v !== a.value) return false;
    if (a.op === '^=' && v.indexOf(a.value) !== 0) return false;
    if (a.op === '$=' && v.slice(-a.value.length) !== a.value) return false;
    if (a.op === '*=' && v.indexOf(a.value) < 0) return false;
  }
  return true;
}
function matchComplex(el, parts, i) {
  if (!matchCompound(el, parts[i].comp)) return false;
  if (i === 0) return true;
  if (parts[i].comb === '>') { const p = el.parentNode; return !!(p && p.nodeType === 1 && matchComplex(p, parts, i - 1)); }
  for (let p = el.parentNode; p && p.nodeType === 1; p = p.parentNode) if (matchComplex(p, parts, i - 1)) return true;
  return false;
}
function matchesSel(el, sel) {
  const cx = parseSelector(sel);
  for (let i = 0; i < cx.length; i++) if (matchComplex(el, cx[i], cx[i].length - 1)) return true;
  return false;
}
function queryAll(root, sel) {
  const out = [];
  (function walk(n) {
    for (let i = 0; i < n.childNodes.length; i++) {
      const c = n.childNodes[i];
      if (c.nodeType !== 1) continue;
      if (matchesSel(c, sel)) out.push(c);
      walk(c);
    }
  })(root);
  return out;
}

function build(n) {
  if (typeof n === 'string') return new TextNode(n);
  const el = new Element(n.t);
  Object.keys(n.a || {}).forEach(function (k) { el.setAttribute(k, n.a[k]); });
  (n.c || []).forEach(function (c) { const ch = build(c); ch.parentNode = el; el.childNodes.push(ch); });
  return el;
}
const root = build(world.tree);
const documentObj = new Emitter();
Object.assign(documentObj, {
  documentElement: root,
  body: queryAll(root, 'body')[0],
  readyState: 'loading', visibilityState: 'visible', hidden: false,
  getElementById: function (id) { return queryAll(root, '#' + id)[0] || null; },
  querySelector: function (sel) { return queryAll(root, sel)[0] || null; },
  querySelectorAll: function (sel) { return queryAll(root, sel); },
  createElement: function (t) { return new Element(t); },
  createTextNode: function (d) { return new TextNode(d); }
});

// ------------------------------------------------------------------ Date
const RealDate = Date;
let fixedNow = world.now ? RealDate.parse(world.now) : null;
let dateOffset = 0;      // moves Date when no instant is pinned
let perfNow = 1000;      // the page's performance.now(), in ms
function nowMs() { return fixedNow !== null ? fixedNow : RealDate.now() + dateOffset; }
class FakeDate extends RealDate {
  constructor() {
    if (arguments.length === 0) super(nowMs()); else super(...arguments);
  }
  static now() { return nowMs(); }
}
H.setNow = function (iso) { fixedNow = RealDate.parse(iso); };
H.nowIso = function () { return new RealDate(nowMs()).toISOString(); };
H.perfNow = function () { return perfNow; };
function advanceClocks(ms, perfToo) {
  if (fixedNow !== null) fixedNow += ms; else dateOffset += ms;
  if (perfToo !== false) perfNow += ms;
}

// ------------------------------------------------------------------ Chart
function statFor(canvas) {
  if (!canvas.__stat) canvas.__stat = { constructs: 0, destroys: 0, updates: [], constructed: [] };
  return canvas.__stat;
}
function describeAnimation(a) {
  if (a === undefined) return { kind: 'default' };
  if (a === false) return { kind: 'false' };
  return { kind: 'object', duration: a.duration, hasOnComplete: typeof a.onComplete === 'function', keys: Object.keys(a) };
}
function allPlugins(chart) { return H.globalPlugins.concat((chart.config && chart.config.plugins) || []); }
class Chart {
  constructor(canvas, config) {
    if (canvas.__chart && !canvas.__chart.destroyed) {
      throw new Error('Canvas is already in use. Chart with ID ' + canvas.__chart.id + ' must be destroyed before the canvas can be reused.');
    }
    if (H.renameLabels) {
      ((config.data && config.data.datasets) || []).forEach(function (d) { d.label = String(d.label) + ' (renamed)'; });
    }
    this.canvas = canvas; this.config = config; this.data = config.data; this.options = config.options || {};
    this.config.options = this.options;
    this.id = H.charts.length; this.destroyed = false; this._meta = {};
    this.ctx = canvas.getContext('2d');
    this.scales = {
      x: { getPixelForValue: function (i) { return 10 + i * 5; }, top: 0, bottom: 60, left: 0, right: 200 },
      y: { top: 0, bottom: 60, left: 0, right: 200 }
    };
    this.chartArea = { left: 0, right: 200, top: 0, bottom: 60 };
    canvas.__chart = this; H.charts.push(this); H.animating.add(this);
    const st = statFor(canvas); st.constructs++;
    st.constructed.push({ animation: describeAnimation(this.options.animation), yDisplay: this.options.scales && this.options.scales.y ? this.options.scales.y.display : null });
  }
  update(mode) { statFor(this.canvas).updates.push(mode === undefined ? null : mode); }
  destroy() {
    this.destroyed = true; H.animating.delete(this);
    if (this.canvas.__chart === this) this.canvas.__chart = null;
    statFor(this.canvas).destroys++;
  }
  getDatasetMeta(i) { return this._meta[i] || (this._meta[i] = { hidden: false }); }
  static getChart(c) {
    if (typeof c === 'string') c = documentObj.getElementById(c);
    return c && c.__chart && !c.__chart.destroyed ? c.__chart : undefined;
  }
  static register() { Array.prototype.forEach.call(arguments, function (p) { H.globalPlugins.push(p); }); }
}
Chart.defaults = {};
Chart.version = 'stub';
Chart.animator = { running: function (chart) { return H.animating.has(chart); } };

H.hero = function () { return Chart.getChart('cum-chart') || null; };
H.sparkCanvases = function () { return queryAll(root, '[data-testid="card-spark"]'); };
H.statusText = function (canvas) { const p = canvas.parentNode; return p ? p.textContent.trim() : ''; };
H.summary = function () {
  const out = {};
  queryAll(root, 'canvas').forEach(function (c) {
    const key = c.getAttribute('id') || ('spark:' + c.getAttribute('data-sym-id'));
    const st = c.__stat || { constructs: 0, destroys: 0, updates: [], constructed: [] };
    const ch = Chart.getChart(c);
    out[key] = {
      constructs: st.constructs, destroys: st.destroys, updates: st.updates.slice(), constructed: st.constructed,
      live: !!ch, chartId: ch ? ch.id : null,
      labels: ch ? clone(ch.data.labels) : null,
      datasets: ch ? ch.data.datasets.map(function (d) { return { label: d.label, data: clone(d.data), borderColor: d.borderColor, hidden: d.hidden }; }) : null,
      status: H.statusText(c)
    };
  });
  return out;
};
H.el = function (sel) {
  const e = queryAll(root, sel)[0];
  if (!e) return { exists: false };
  return { exists: true, hidden: e.hidden, text: e.textContent.trim(), style: Object.assign({}, e.style), attrs: Object.fromEntries(e._attrs) };
};
H.dial = function (id) {
  const svg = queryAll(root, '[data-testid="mc-dial"][data-sym-id="' + id + '"]')[0];
  if (!svg) return null;
  const arc = queryAll(svg, '.mc-arc')[0]; const text = queryAll(svg, '.mc-text')[0];
  return { stroke: arc ? arc.style.stroke : null, offset: arc ? arc.getAttribute('stroke-dashoffset') : null, text: text ? text.textContent.trim() : null };
};
H.attrs = function () {
  const b = documentObj.body;
  return { ready: b.getAttribute('data-dashboard-ready'), refreshCount: b.getAttribute('data-refresh-count') };
};
H.chartFor = function (key) {
  const c = queryAll(root, key.indexOf('spark:') === 0 ? '[data-testid="card-spark"][data-sym-id="' + key.slice(6) + '"]' : '#' + key)[0];
  return c ? (Chart.getChart(c) || null) : null;
};
H.draw = function (chart) {
  const ctx = chart.ctx; const start = ctx.log.length;
  allPlugins(chart).forEach(function (p) { if (typeof p.afterDraw === 'function') p.afterDraw(chart); });
  return ctx.log.slice(start);
};
H.fireAfterRender = function (chart) {
  allPlugins(chart).forEach(function (p) { if (typeof p.afterRender === 'function') p.afterRender(chart, {}, {}); });
};
H.completeAnimations = async function (pred) {
  let n = 0;
  H.charts.slice().forEach(function (ch) {
    if (ch.destroyed || !H.animating.has(ch)) return;
    if (pred && !pred(ch)) return;
    H.animating.delete(ch);
    const a = ch.options && ch.options.animation;
    if (a && typeof a.onComplete === 'function') { a.onComplete.call(ch, { chart: ch, initial: true, numSteps: 60, currentStep: 60 }); n++; }
  });
  await H.settle();
  return n;
};

// ------------------------------------------------------------------ fetch
H.route = function (matcher, spec) { H.routes.unshift({ matcher: matcher, spec: spec }); };
function findRoute(url, opts) {
  for (let i = 0; i < H.routes.length; i++) {
    const r = H.routes[i]; let m = null;
    if (typeof r.matcher === 'string') { if (r.matcher === url) m = [url]; }
    else { m = url.match(r.matcher); }
    if (m) { return typeof r.spec === 'function' ? r.spec(url, opts, m) : (r.spec.fn ? r.spec.fn(url, opts, m) : r.spec); }
  }
  return null;
}
// A response carries the headers a real one does: `Date` is the page clock when the response is
// made (a server on the same clock) unless the spec names headers of its own.
function makeResponse(spec) {
  const status = spec.status === undefined ? 200 : spec.status;
  const headers = Object.assign({ 'date': new RealDate(nowMs()).toUTCString() }, lowerKeys(spec.headers || {}));
  if (spec.noDate) delete headers['date'];
  const isText = spec.text !== undefined;
  return {
    ok: status >= 200 && status < 300, status: status, statusText: '',
    headers: { get: function (n) { const v = headers[String(n).toLowerCase()]; return v === undefined ? null : v; } },
    json: function () {
      if (spec.badJson || isText) return Promise.reject(new SyntaxError('Unexpected token < in JSON'));
      return Promise.resolve(clone(spec.body === undefined ? {} : spec.body));
    },
    text: function () { return Promise.resolve(isText ? spec.text : JSON.stringify(spec.body === undefined ? {} : spec.body)); }
  };
}
function lowerKeys(o) { const out = {}; Object.keys(o).forEach(function (k) { out[k.toLowerCase()] = o[k]; }); return out; }
function abortError() { const e = new Error('The operation was aborted.'); e.name = 'AbortError'; return e; }
function fetchStub(url, opts) {
  opts = opts || {};
  const signal = opts.signal || null;
  const entry = { url: String(url), method: String(opts.method || 'GET').toUpperCase(), headers: opts.headers || {}, body: opts.body, n: H.fetchLog.length, hasSignal: !!signal, aborted: false };
  H.fetchLog.push(entry);
  if (signal && signal.aborted) { entry.aborted = true; return Promise.reject(abortError()); }
  let spec = findRoute(entry.url, opts);
  if (!spec) { H.unrouted.push(entry.url); spec = { status: 200, body: {} }; }
  if (spec.pending) {
    return new Promise(function (resolve, reject) {
      const item = {
        url: entry.url, entry: entry,
        settle: function (s) { if (item.done) return; item.done = true; if (s.reject) reject(new TypeError('Failed to fetch')); else resolve(makeResponse(s)); }
      };
      H.pending.push(item);
      if (signal) {
        signal.addEventListener('abort', function () {
          if (item.done) return;
          item.done = true; entry.aborted = true; reject(abortError());
        });
      }
    });
  }
  if (spec.reject) return Promise.reject(new TypeError('Failed to fetch'));
  return Promise.resolve(makeResponse(spec));
}
H.requests = function (re) { return H.fetchLog.filter(function (e) { return re.test(e.url); }).map(function (e) { return e.url; }); };
H.releasePending = async function (url, spec) {
  const hits = H.pending.filter(function (p) { return p.url === url && !p.done; });
  hits.forEach(function (p) { p.settle(spec); });
  await H.settle();
  return hits.length;
};
// Releases the pending request at `index` among those still waiting for `url` (0 = the oldest).
H.releaseNth = async function (url, index, spec) {
  const hits = H.pending.filter(function (p) { return p.url === url && !p.done; });
  if (!hits[index]) return 0;
  hits[index].settle(spec);
  await H.settle();
  return 1;
};
H.waiting = function (url) { return H.pending.filter(function (p) { return p.url === url && !p.done; }).length; };
H.routeCommon = function (opts) {
  opts = opts || {};
  const b = H.bodies;
  H.route('/api/csrf-token', { body: { csrf_token: 'tok' } });
  H.route(/^\/api\/logs\//, { body: [] });
  H.route('/api/settings', { body: {} });
  H.route(/^\/api\/hero-chart\/([a-z0-9]+)$/, function (url, o, m) { return { body: (opts.hero || b.hero)[m[1]] }; });
  H.route(/^\/api\/chart\/(.+)$/, function (url, o, m) { return { body: (opts.charts || b.charts)[m[1]] }; });
  H.route('/api/state', { body: opts.state || b.state });
};
// The window strips, the $-saved summaries, the header's alert poll and the event-stream probe:
// the producer output captured with the world, so a scenario that polls does not hit a bare 200.
H.routeExtras = function () {
  const b = H.bodies;
  H.route('/api/candidate-alert', { body: { new_valid_count: 0, last_run: null } });
  H.route(/^\/api\/strip\/([a-z0-9]+)$/, function (url, o, m) { return { body: b.strips[m[1]] }; });
  H.route(/^\/api\/guard-alpha-summary\?window=([a-z0-9]+)$/, function (url, o, m) { return { body: b.summaries[m[1]] }; });
  H.route('/api/events', { status: 200, body: {} });
};
H.boot = async function (opts) {
  opts = opts || {};
  H.routeCommon(opts);
  if (opts.extras) H.routeExtras();
  (opts.overrides || []).forEach(function (o) { H.route(o[0], o[1]); });
  if (opts.statePending) H.route('/api/state', { pending: true });
  if (opts.stateReject) H.route('/api/state', { reject: true });
  await H.domReady();
  if (opts.complete !== false) await H.completeAnimations();
  await H.settle();
};

// ------------------------------------------------------------------ timers, events, theme
H.settle = async function () { for (let i = 0; i < 4; i++) await new Promise(function (r) { setImmediate(r); }); };
H.tick = async function () {
  H.intervals.slice().forEach(function (iv) { try { iv.fn(); } catch (e) { H.console.errors.push('interval threw: ' + (e && e.stack || e)); } });
  await H.settle();
};
// Delivers a named event to every open stream. `data` (an object or text) becomes the event's JSON `data`.
H.sse = async function (type, data) {
  const evt = { type: type };
  if (data !== undefined) evt.data = typeof data === 'string' ? data : JSON.stringify(data);
  H.eventSources.forEach(function (es) {
    if (es.readyState !== 1) return;
    (es.listeners[type] || []).slice().forEach(function (f) { try { f(evt); } catch (e) { H.console.errors.push('sse threw: ' + (e && e.stack || e)); } });
  });
  await H.settle();
};
H.liveStreams = function () { return H.eventSources.filter(function (es) { return es.readyState !== 2; }).length; };
// The stream at `index` reports an error: closed for good (a refused reconnect), or still
// CONNECTING (a transport drop the browser reconnects by itself).
H.streamError = async function (index, closed) {
  const es = H.eventSources[index];
  es.readyState = closed ? 2 : 0;
  const evt = { type: 'error' };
  if (typeof es.onerror === 'function') es.onerror(evt);
  (es.listeners['error'] || []).slice().forEach(function (f) { f(evt); });
  await H.settle();
};
H.streamOpen = async function (index) {
  const es = H.eventSources[index];
  es.readyState = 1;
  const evt = { type: 'open' };
  if (typeof es.onopen === 'function') es.onopen(evt);
  (es.listeners['open'] || []).slice().forEach(function (f) { f(evt); });
  await H.settle();
};

// ---- virtual time: timers fall due on the page's performance.now() and never fire by themselves.
function allTimers() { return H.intervals.concat(H.timeouts); }
function fireTimer(t) {
  if (t.kind === 'timeout') H.timeouts = H.timeouts.filter(function (x) { return x !== t; });
  else t.due += t.ms > 0 ? t.ms : 1;
  try { t.fn.apply(null, t.args || []); } catch (e) { H.console.errors.push('timer threw: ' + (e && e.stack || e)); }
}
// Advances time by `ms`, running each timer when it falls due (a due interval runs, then falls due
// again one period later). With `skipBelow`, intervals shorter than that many ms are not run during
// the span and each runs once at its end, so a span of hours stays cheap (the page's one-second ticks
// are skipped with `skipBelow: 5000`; everything, polls included, with `skipBelow: 600000`).
H.runTimers = async function (ms, opts) {
  opts = opts || {};
  const skipBelow = opts.skipBelow || 0;
  const end = perfNow + ms;
  for (;;) {
    const due = allTimers().filter(function (t) {
      if (skipBelow && t.kind === 'interval' && t.ms < skipBelow) return false;
      return t.due <= end;
    });
    if (!due.length) break;
    due.sort(function (a, b) { return a.due - b.due || a.id - b.id; });
    const t = due[0];
    if (t.due > perfNow) advanceClocks(t.due - perfNow);
    fireTimer(t);
    if (t.kind === 'timeout' || t.ms >= 5000) await H.settle(); else await Promise.resolve();
  }
  if (end > perfNow) advanceClocks(end - perfNow);
  if (skipBelow) {
    H.intervals.filter(function (t) { return t.ms < skipBelow; }).forEach(function (t) { fireTimer(t); t.due = perfNow + t.ms; });
  }
  await H.settle();
};
// The clocks jump with no timer running. `perf: false` moves Date alone.
H.sleepFor = function (ms, opts) {
  opts = opts || {};
  advanceClocks(ms, opts.perf !== false);
};
// Waking runs each overdue timer once; an interval falls due again one period after the wake.
H.wakeUp = async function () {
  const overdue = allTimers().filter(function (t) { return t.due <= perfNow; });
  overdue.sort(function (a, b) { return a.due - b.due || a.id - b.id; });
  overdue.forEach(function (t) { fireTimer(t); if (t.kind === 'interval') t.due = perfNow + (t.ms > 0 ? t.ms : 1); });
  await H.settle();
};
H.setVisibility = async function (state) {
  documentObj.visibilityState = state; documentObj.hidden = state === 'hidden';
  documentObj.dispatchEvent({ type: 'visibilitychange' });
  await H.settle();
};
H.fireWindow = async function (type, props) {
  if (type === 'online') sandbox.navigator.onLine = true;
  if (type === 'offline') sandbox.navigator.onLine = false;
  winEmitter.dispatchEvent(Object.assign({ type: type }, props || {}));
  await H.settle();
};
H.guardAlpha = function () { return { headline: H.text('#guard-alpha-headline'), label: H.text('#guard-alpha-window-label') }; };
H.vol = function () {
  return {
    bot: H.text('[data-testid="comp-vol-bot-text"]'),
    held: H.text('[data-testid="comp-vol-held-text"]'),
    delta: H.text('[data-testid="comp-vol-delta"]')
  };
};
H.text = function (sel) { const e = queryAll(root, sel)[0]; return e ? e.textContent.trim() : null; };
H.requestsTo = function (re, method) { return H.fetchLog.filter(function (e) { return re.test(e.url) && (!method || e.method === method); }); };
H.domReady = async function () {
  documentObj.readyState = 'interactive';
  const list = ((documentObj._listeners || {}).DOMContentLoaded || []).slice();
  list.forEach(function (f) { try { f.call(documentObj, { type: 'DOMContentLoaded' }); } catch (e) { H.console.errors.push('DOMContentLoaded handler threw: ' + (e && e.stack || e)); } });
  await H.settle();
};
H.click = async function (sel) {
  const el = queryAll(root, sel)[0];
  if (!el) throw new Error('no element for ' + sel);
  el.click();
  await H.settle();
};
const winEmitter = new Emitter();
class CustomEventStub { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
H.setTheme = async function (name) {
  H.theme = name;
  H.tokens = Object.assign({}, world.tokens.light, name === 'dark' ? world.tokens.darkOverrides : {});
  if (H.accentOverride) H.tokens['--studio-accent'] = H.accentOverride;
  winEmitter.dispatchEvent(new CustomEventStub('tweakchange', { detail: { key: 'theme', value: name } }));
  await H.settle();
};
H.setAccent = async function (value) {
  H.accentOverride = value; H.tokens['--studio-accent'] = value;
  winEmitter.dispatchEvent(new CustomEventStub('tweakchange', { detail: { key: 'accent', value: value } }));
  await H.settle();
};
H.tokenValue = function (name) { return H.tokens[name]; };
// Every token reads as its own name plus a label, so a colour read from a chart says
// which token produced it, and two tokens never share a value.
H.useTokenEpoch = function (label) {
  H.tokens = new Proxy({}, { get: function (t, name) { return typeof name === 'string' ? name + '@' + label : undefined; } });
};
H.tweak = async function () {
  winEmitter.dispatchEvent(new CustomEventStub('tweakchange', { detail: { key: 'theme', value: 'epoch' } }));
  await H.settle();
};

// ------------------------------------------------------------------ sandbox
// readyState follows the HTML specification: CONNECTING 0, OPEN 1, CLOSED 2. A stream opens at once
// unless `H.streamAutoOpen` is false, and a new stream fails (closed, error event) when
// `H.failNewStreams` is true, as a reconnect answered 502 does.
class EventSourceStub {
  constructor(url) {
    this.url = url; this.listeners = {}; this.onopen = null; this.onerror = null;
    this.index = H.eventSources.length; this.readyState = 0;
    this.createdAt = perfNow;
    this.liveWhenCreated = H.eventSources.filter(function (es) { return es.readyState !== 2; }).length;
    H.eventSources.push(this);
    const self = this;
    setImmediate(function () {
      if (self.readyState !== 0) return;
      if (H.failNewStreams) {
        self.readyState = 2;
        const evt = { type: 'error' };
        if (typeof self.onerror === 'function') self.onerror(evt);
        (self.listeners['error'] || []).slice().forEach(function (f) { f(evt); });
      } else if (H.streamAutoOpen) {
        self.readyState = 1;
        if (typeof self.onopen === 'function') self.onopen({ type: 'open' });
      }
    });
  }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  close() { this.readyState = 2; this.closedByPage = true; }
}
EventSourceStub.CONNECTING = 0; EventSourceStub.OPEN = 1; EventSourceStub.CLOSED = 2;
EventSourceStub.prototype.CONNECTING = 0; EventSourceStub.prototype.OPEN = 1; EventSourceStub.prototype.CLOSED = 2;

class AbortSignalStub extends Emitter {
  constructor() { super(); this.aborted = false; this.reason = undefined; this.onabort = null; }
  throwIfAborted() { if (this.aborted) throw this.reason; }
}
class AbortControllerStub {
  constructor() { this.signal = new AbortSignalStub(); }
  abort(reason) {
    if (this.signal.aborted) return;
    this.signal.aborted = true; this.signal.reason = reason || abortError();
    const evt = { type: 'abort', target: this.signal };
    if (typeof this.signal.onabort === 'function') this.signal.onabort(evt);
    this.signal.dispatchEvent(evt);
  }
}
const pageConsole = {
  log: function () { H.console.logs.push(Array.prototype.join.call(arguments, ' ')); },
  info: function () {}, debug: function () {}, warn: function () {},
  error: function () { H.console.errors.push(Array.prototype.map.call(arguments, function (a) { return (a && a.stack) || String(a); }).join(' ')); }
};
const sandbox = {
  console: pageConsole, document: documentObj, fetch: fetchStub, Chart: Chart, EventSource: EventSourceStub,
  setInterval: function (fn, ms) { const id = ++timerSeq; const period = Number(ms) || 0; H.intervals.push({ id: id, kind: 'interval', fn: fn, ms: period, due: perfNow + (period > 0 ? period : 1) }); return id; },
  clearInterval: function (id) { H.intervals = H.intervals.filter(function (i) { return i.id !== id; }); },
  setTimeout: function (fn, ms) { const id = ++timerSeq; const delay = Number(ms) || 0; H.timeouts.push({ id: id, kind: 'timeout', fn: fn, ms: delay, due: perfNow + delay, args: Array.prototype.slice.call(arguments, 2) }); return id; },
  clearTimeout: function (id) { H.timeouts = H.timeouts.filter(function (t) { return t.id !== id; }); },
  AbortController: AbortControllerStub,
  performance: { now: function () { return perfNow; } },
  getComputedStyle: function () { return { getPropertyValue: function (n) { return H.tokens[n] === undefined ? '' : String(H.tokens[n]); } }; },
  Date: FakeDate, CustomEvent: CustomEventStub, Event: CustomEventStub,
  location: { reload: function () { H.reloads++; }, search: '', href: 'http://localhost/' },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  navigator: { userAgent: 'harness' },
  addEventListener: function (t, f) { winEmitter.addEventListener(t, f); },
  removeEventListener: function (t, f) { winEmitter.removeEventListener(t, f); },
  dispatchEvent: function (e) { return winEmitter.dispatchEvent(e); },
  confirm: function () { return true; }, alert: function () {}
};
sandbox.window = sandbox;
const ctx = vm.createContext(sandbox);
H.sandbox = sandbox;

(async function main() {
  let result; let error = null;
  try {
    world.scripts.forEach(function (file) {
      vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
    });
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const scenario = new AsyncFunction('H', world.scenario);
    result = await scenario(H);
    await H.settle();
  } catch (e) { error = String((e && e.stack) || e); }
  const out = { result: result === undefined ? null : result, error: error, consoleErrors: H.console.errors, unhandled: H.unhandled, unrouted: H.unrouted };
  process.stdout.write('\n__HARNESS_RESULT__' + JSON.stringify(out) + '\n');
})();
"""
