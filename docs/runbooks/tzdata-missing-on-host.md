# Runbook: tzdata Missing on Host

**When to use:** Operator sees a `ZoneInfoNotFoundError` (or `KeyError: 'America/New_York'`) in production logs, or AlphaBot's market-hours window appears shifted by exactly 1 hour during DST transitions, suggesting UTC-offset arithmetic was used instead of a proper IANA timezone lookup.

---

## 1 — When to use (symptom + AlphaBot behavior)

### Log signature

```
KeyError: 'America/New_York'
```

or (on Python < 3.9 without the `backports.zoneinfo` shim):

```
ImportError: No module named 'zoneinfo'
```

These surface inside `get_current_et()` in `alpha_bot_execution.py` (lines 262–270).

### What AlphaBot does when this fires

`get_current_et()` catches `(ImportError, KeyError)` and falls back to a static UTC-offset calculation:

| Month range | Offset applied |
|---|---|
| March – November (months 3–11) | UTC − 4 h (EDT assumed) |
| December – February (months 12–2) | UTC − 5 h (EST assumed) |

AlphaBot **does not crash, does not skip the tick, and does not log a warning**. It silently uses the hardcoded offset and continues. The engine stays operational but loses DST-change precision: during the brief window when UTC offset shifts (second Sunday in March; first Sunday in November) the offset will be wrong by 1 hour, which can cause AlphaBot to enter or exit its market-hours window at the wrong time.

---

## 2 — Background

`zoneinfo` (stdlib since Python 3.9) loads IANA timezone data from the host OS. On minimal systems where the IANA database is absent, `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError`, which is a subclass of `KeyError`. Before cycle #28, the catch clause at line 267 read `except Exception`, which silently swallowed every error including latent bugs unrelated to timezone data. Cycle #28 (commit `d74e7d3`, merged `88198e5`) narrowed the catch to `except (ImportError, KeyError)` so that only the two legitimate failure modes are suppressed — ImportError for Python < 3.9 hosts and KeyError for hosts where `zoneinfo` is present but the IANA database is missing.

---

## 3 — Detection

Confirm that `zoneinfo` + the IANA database are the actual cause (rather than a different `KeyError` elsewhere) by running this in a Python REPL on the affected host:

```python
from zoneinfo import ZoneInfo
print(ZoneInfo("America/New_York"))
```

| Result | Meaning |
|---|---|
| `zoneinfo.ZoneInfo(key='America/New_York')` | tzdata is present — this runbook does not apply; investigate further |
| `KeyError: 'America/New_York'` | IANA database missing from host — proceed with fix below |
| `ModuleNotFoundError: No module named 'zoneinfo'` | Python < 3.9 on this host — upgrade Python or install `backports.zoneinfo` |

To also confirm the fallback is in use at runtime, add a temporary `print` inside the `except` branch of `get_current_et()` (lines 267–270) and watch the next execution cycle's stdout.

---

## 4 — Root cause by platform

| Platform | Why tzdata may be missing |
|---|---|
| **Alpine Linux** | Ships no IANA database by default; the `tzdata` package must be installed explicitly |
| **Debian/Ubuntu slim images** | `tzdata` apt package is omitted from slim variants to reduce image size |
| **Other minimal Linux containers** | Any distro that strips non-essential packages during image construction |
| **Windows** | Python's `zoneinfo` on Windows falls back to the `tzdata` pip package; if it is not installed the IANA database is unavailable |
| **macOS (misconfigured)** | Normally ships the IANA database at `/usr/share/zoneinfo`; absent when a stripped or corporate-managed system has removed it, or when running inside a Docker container built from a macOS-derived non-standard base |

---

## 5 — Fix procedure per platform

### Linux — apt (Debian/Ubuntu/Debian-slim)

```bash
apt-get update && apt-get install -y tzdata
```

Set `DEBIAN_FRONTEND=noninteractive` to suppress the interactive timezone prompt in non-TTY environments:

```bash
DEBIAN_FRONTEND=noninteractive apt-get install -y tzdata
```

### Linux — apk (Alpine)

```sh
apk add --no-cache tzdata
```

### Docker (Dockerfile)

Add tzdata installation to the image build so every container has it:

```dockerfile
# Debian/Ubuntu-based image
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

# Alpine-based image
RUN apk add --no-cache tzdata
```

Place this `RUN` step before the `COPY` / `RUN pip install` steps so it is cached on the layer boundary.

### Windows

```powershell
pip install tzdata
```

`tzdata` is a pure-Python package that ships the IANA database as a Python package; `zoneinfo` on Windows automatically discovers it via the `TZDATA_VERSION` metadata.

Alternatively, add `tzdata` to `requirements.txt` if it is not already present, so the dependency is explicit:

```
tzdata>=2024.1
```

### Verification (all platforms)

After applying the fix, confirm the lookup succeeds without raising:

```python
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('America/New_York'))"
```

Expected output:

```
zoneinfo.ZoneInfo(key='America/New_York')
```

Any exception here means the fix did not take effect — recheck the install step and confirm the correct Python environment is active (virtualenv vs system interpreter).

---

## 6 — Operational impact

When the `except (ImportError, KeyError)` branch fires, `get_current_et()` returns a UTC-based datetime with a hardcoded EST/EDT offset. The downstream effect is limited to `main()` in `alpha_bot_execution.py`, which uses the returned time to:

1. Check `is_weekday` (weekday check is offset-independent; no impact)
2. Compare `current_time` against `EXECUTION_START_TIME` and `EXECUTION_END_TIME` to gate whether the engine runs at all

**Practical impact:** AlphaBot continues to execute ticks normally. The only degradation is DST-boundary precision. During the ~1-hour window around a DST transition (twice per year), the fallback offset will be wrong by exactly 1 hour, causing AlphaBot to begin or end its trading day 1 hour early or late relative to NYSE market hours. Outside of DST transitions the hardcoded offsets are correct and there is no operational difference.

No positions are affected mid-cycle; no Guard Alpha logic is bypassed; no API calls are skipped. This is a scheduling-only degradation.

---

## Related runbooks

- [`composer-rejection-diagnostic.md`](composer-rejection-diagnostic.md) — handles `[COMPOSER REJECTED]: HTTP {status_code}` errors from `alpha_bot_execution.py`
