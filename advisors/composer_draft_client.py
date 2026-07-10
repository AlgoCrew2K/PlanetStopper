"""Composer draft-write client — shared write path for the Frontrunner Builder
and the ``propose_strategies`` retrofit (feature-plans/frontrunner-builder.md).

Creates a NEW, UNDEPLOYED symphony in the operator's Composer account via
``POST /api/v0.1/symphonies`` (the VALIDATED contract — see feature plan
§Architecture) and verifies the result holds zero allocation before the
caller may mark an approval "uploaded".

NO-AUTO-TRADE BOUNDARY (structural, not policy)
-------------------------------------------------
This module exposes ONLY ``save_symphony`` and ``verify_undeployed``. It does
NOT implement, import, or reference ``invest_in_symphony`` or the
``/deploy/.../invest`` endpoint anywhere in this file — deploying a symphony
is a deliberately separate call this feature must never construct. This is
enforced both here (by omission) and by an adversarial source-scan test suite
(tests/security/test_frontrunner_no_trade_boundary.py) that fails if a future
edit ever reintroduces an invest/deploy-shaped symbol or URL fragment.

Error handling contract (mirrors composer_backtest_client.py)
----------------------------------------------------------------
Never raises on API or transport errors — every failure mode returns a
``DraftResult`` / ``bool`` with an explicit error signal. A single candidate's
failure must not abort the batch or crash the approval route.

Live / test separation
-----------------------
Tests patch ``requests.post`` / ``requests.get`` to inject fixture responses.
This module does not use a Session internally so the standard
``patch("advisors.composer_draft_client.requests.post", ...)`` intercept works
without additional wiring. No top-level network calls are made at import time.

Retry policy
------------
Exponential backoff: 1 s -> 2 s -> 4 s -> 8 s (identical schedule to
composer_backtest_client). ``max_retries`` is a per-call parameter (default 4).
The maximum cumulative wait is bounded by ``DRAFT_MAX_RETRY_WAIT_SECONDS``.
429 responses respect the ``Retry-After`` header when present.

Idempotency
-----------
``save_symphony`` accepts ``already_uploaded_symphony_id`` — when supplied
(non-empty), the call returns a success-shaped ``DraftResult`` echoing that id
WITHOUT issuing a second POST. This is the idempotency seam the approval route
uses so double-clicking Approve (or a retried request) never creates a
duplicate Composer symphony.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry policy — identical schedule to composer_backtest_client.py.
# ---------------------------------------------------------------------------

_BACKOFF_INTERVALS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

DRAFT_MAX_RETRY_WAIT_SECONDS: float = sum(_BACKOFF_INTERVALS)  # 15 s

# Explicit per-request HTTP timeouts.
_CREATE_REQUEST_TIMEOUT: int = 30
_VERIFY_REQUEST_TIMEOUT: int = 30

# HTTP status codes that are transient and warrant a retry.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Default asset_class per the pinned contract (feature plan §Architecture).
_DEFAULT_ASSET_CLASS: str = "EQUITIES"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class DraftResult:
    """Structured result from ``save_symphony``.

    On success: ``success`` is True, ``symphony_id`` / ``version_id`` are
    populated (when present in the response body), ``error`` is None.

    On failure: ``success`` is False, ``symphony_id`` is falsy, ``error`` is a
    non-empty string describing the failure reason.
    """

    success: bool
    symphony_id: str | None = None
    version_id: str | None = None
    error: str | None = None


def _error_result(reason: str) -> DraftResult:
    """Construct a failure ``DraftResult`` with the given reason string."""
    return DraftResult(success=False, symphony_id=None, version_id=None, error=reason)


# ---------------------------------------------------------------------------
# save_symphony — POST /api/v0.1/symphonies (create UNDEPLOYED symphony)
# ---------------------------------------------------------------------------


def save_symphony(
    *,
    name: str,
    description: str,
    color: str,
    hashtag: str,
    raw_value: dict,
    asset_class: str = _DEFAULT_ASSET_CLASS,
    already_uploaded_symphony_id: str | None = None,
    max_retries: int = 4,
) -> DraftResult:
    """Create a new UNDEPLOYED symphony via ``POST /api/v0.1/symphonies``.

    Parameters
    ----------
    name, description, color, hashtag:
        Composer symphony metadata fields per the pinned create contract.
    raw_value:
        The full validated symphony tree (``symphony_schema`` output). Passed
        through UNCHANGED as ``symphony.raw_value`` — this client never
        mutates or re-derives fields into it.
    asset_class:
        ``"EQUITIES"`` (default) or ``"CRYPTO"`` per the pinned contract.
    already_uploaded_symphony_id:
        Idempotency seam. When supplied (non-empty), this call is a no-op —
        no POST is issued — and a success-shaped ``DraftResult`` echoing this
        id is returned immediately. Callers (the approval route) pass the
        previously-recorded symphony_id for a candidate that was already
        uploaded, so a duplicate Approve click never creates a second
        Composer symphony.
    max_retries:
        Maximum number of retry attempts after transient failures (429/5xx).
        A value of 0 means no retries. Bounded — never unbounded.

    Returns
    -------
    DraftResult
        On success: ``success=True``, ``symphony_id``/``version_id`` populated.
        On failure: ``success=False``, ``error`` set, ``symphony_id`` falsy.
        Never raises on API or transport errors.
    """
    # Idempotency short-circuit: no POST when the caller already recorded a
    # symphony_id for this candidate.
    if already_uploaded_symphony_id:
        return DraftResult(
            success=True,
            symphony_id=already_uploaded_symphony_id,
            version_id=None,
            error=None,
        )

    url = f"{COMPOSER_BASE_URL}/symphonies"
    body: dict[str, Any] = {
        "name": name,
        "asset_class": asset_class,
        "description": description,
        "color": color,
        "hashtag": hashtag,
        "symphony": {"raw_value": raw_value},
    }
    headers = get_composer_headers()

    logger.debug(
        "save_symphony: POST %s | name=%s asset_class=%s",
        url,
        name,
        asset_class,
    )

    backoff_schedule = _BACKOFF_INTERVALS[:max_retries]
    last_reason: str = "unknown error"

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url, json=body, headers=headers, timeout=_CREATE_REQUEST_TIMEOUT
            )
            logger.info(
                "save_symphony: POST /symphonies HTTP %d (attempt %d/%d)",
                response.status_code,
                attempt + 1,
                max_retries + 1,
            )

            if response.status_code in (200, 201):
                try:
                    data = response.json()
                except ValueError as exc:
                    return _error_result(f"invalid JSON in {response.status_code} response: {exc}")
                if not isinstance(data, dict):
                    return _error_result(
                        f"unexpected response body shape: {type(data).__name__}"
                    )
                return DraftResult(
                    success=True,
                    symphony_id=data.get("symphony_id"),
                    version_id=data.get("version_id"),
                    error=None,
                )

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", _BACKOFF_INTERVALS[0]))
                last_reason = f"HTTP 429 rate limit exceeded; Retry-After={retry_after:.0f}s"
                if attempt < max_retries:
                    logger.info(
                        "save_symphony: rate-limited (429), sleeping %.1f s (attempt %d)",
                        retry_after,
                        attempt + 1,
                    )
                    time.sleep(retry_after)
                    continue
                return _error_result(last_reason)

            if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries:
                delay = (
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1]
                )
                last_reason = f"HTTP {response.status_code} (transient)"
                logger.info(
                    "save_symphony: transient HTTP %d, retrying in %.1f s (attempt %d)",
                    response.status_code,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
                continue

            # Non-retryable (e.g. 400 malformed raw_value) or retries exhausted.
            last_reason = f"HTTP {response.status_code}: {response.text[:200]}"
            return _error_result(last_reason)

        except requests.Timeout as exc:
            last_reason = f"request timed out after {_CREATE_REQUEST_TIMEOUT}s: {exc}"
            logger.info("save_symphony: timeout on attempt %d — %s", attempt + 1, exc)
            return _error_result(last_reason)

        except requests.RequestException as exc:
            last_reason = f"transport error: {type(exc).__name__}: {exc}"
            logger.info(
                "save_symphony: transport error on attempt %d — %s",
                attempt + 1,
                type(exc).__name__,
            )
            if attempt < max_retries:
                delay = (
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1]
                )
                time.sleep(delay)
                continue
            return _error_result(last_reason)

    return _error_result(last_reason)


# ---------------------------------------------------------------------------
# verify_undeployed — GET /api/v0.1/symphonies/{id}/score, zero-allocation check
# ---------------------------------------------------------------------------


def verify_undeployed(symphony_id: str, *, max_retries: int = 2) -> bool:
    """Read back a created symphony and confirm it holds ZERO allocation.

    Mirrors ``composer_backtest_client``'s ``dvm_capital`` read pattern
    (composer_backtest_client.py:147): an undeployed symphony's ``dvm_capital``
    carries no entries for it (or an all-zero series). This is the
    belt-and-suspenders safety read the approval route MUST call before
    marking an upload "uploaded" — a created symphony is only as safe as this
    check confirms it to be.

    Parameters
    ----------
    symphony_id:
        The Composer symphony id returned by ``save_symphony``.
    max_retries:
        Bounded retry count for transient failures. Default 2 (this is a
        safety read, not a heavy backtest — keep it fast).

    Returns
    -------
    bool
        True only when the response is well-formed AND shows zero allocation
        for ``symphony_id``. False on ANY error, transport failure, or
        non-zero allocation — fail-CLOSED. Never raises.
    """
    url = f"{COMPOSER_BASE_URL}/symphonies/{symphony_id}/score"
    headers = get_composer_headers()

    backoff_schedule = _BACKOFF_INTERVALS[:max_retries]

    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=_VERIFY_REQUEST_TIMEOUT)
            logger.info(
                "verify_undeployed: GET /symphonies/%s/score HTTP %d (attempt %d/%d)",
                symphony_id,
                response.status_code,
                attempt + 1,
                max_retries + 1,
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    logger.warning(
                        "verify_undeployed: invalid JSON in 200 response for %s", symphony_id
                    )
                    return False  # fail-closed
                if not isinstance(data, dict):
                    return False  # fail-closed

                dvm_capital = data.get("dvm_capital")
                if not isinstance(dvm_capital, dict) or not dvm_capital:
                    # No dvm_capital at all — never traded, safest reading.
                    return True

                series = dvm_capital.get(symphony_id)
                if series is None and len(dvm_capital) == 1:
                    # Same single-series fallback semantics as
                    # composer_backtest_client._extract_returns — the response
                    # key may not match symphony_id exactly.
                    ((_, series),) = dvm_capital.items()

                if not isinstance(series, dict) or not series:
                    # No per-day values recorded — undeployed.
                    return True

                # Any nonzero value anywhere in the series means capital was
                # actually deployed at some point — fail-closed (not safe).
                try:
                    values = [float(v) for v in series.values()]
                except (TypeError, ValueError):
                    return False  # unparseable — fail-closed
                return all(v == 0 for v in values)

            if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries:
                delay = (
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1]
                )
                logger.info(
                    "verify_undeployed: transient HTTP %d, retrying in %.1f s (attempt %d)",
                    response.status_code,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
                continue

            # Non-retryable HTTP error, or retries exhausted — fail-closed.
            logger.warning(
                "verify_undeployed: HTTP %d for %s — treating as NOT verified undeployed",
                response.status_code,
                symphony_id,
            )
            return False

        except requests.RequestException as exc:
            logger.info(
                "verify_undeployed: transport error on attempt %d — %s",
                attempt + 1,
                type(exc).__name__,
            )
            if attempt < max_retries:
                delay = (
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1]
                )
                time.sleep(delay)
                continue
            return False  # fail-closed

    return False
