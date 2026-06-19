"""
Tier 2 — live contract test for symphony-stats-meta per-symphony fields.

Hits the real Composer API and asserts that the expected per-symphony fields
(last_percent_change, simple_return, max_drawdown, net_deposits,
time_weighted_return) exist and are the expected Python types.

This is the drift sensor: if Composer renames or drops any of these fields,
this test fails before production code silently regresses.

EXCLUDED from the default pytest run — run with:
    pytest --include-live -m live
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.live


@pytest.mark.live
def test_live_symphony_stats_meta_has_required_per_symphony_fields():
    """
    Live contract: every symphony returned by symphony-stats-meta must carry
    all five fields that M1 analytics helpers depend on, and each must be the
    expected Python type.

    Fields asserted:
      last_percent_change   -> float  (today's change; multiplied by 100 by callers)
      simple_return         -> float  (cumulative return, gross of deposits)
      max_drawdown          -> float  (Composer positive convention, in [0, inf))
      net_deposits          -> float  (sum of deposits; 0.0 signals TWR-fallback case)
      time_weighted_return  -> float  (fallback CR for net_deposits==0 case)
    """
    import os

    import requests

    composer_token = os.environ.get("COMPOSER_API_TOKEN") or os.environ.get("COMPOSER_TOKEN")
    account_ids_raw = os.environ.get("COMPOSER_ACCOUNT_IDS") or os.environ.get("ACCOUNT_UUIDS")

    if not composer_token or not account_ids_raw:
        pytest.skip("COMPOSER_API_TOKEN and COMPOSER_ACCOUNT_IDS env vars required for live test")

    account_ids = [a.strip() for a in account_ids_raw.split(",") if a.strip()]
    if not account_ids:
        pytest.skip("No account IDs found in COMPOSER_ACCOUNT_IDS")

    headers = {"Authorization": f"Bearer {composer_token}"}

    required_fields = {
        "last_percent_change": float,
        "simple_return": float,
        "max_drawdown": float,
        "net_deposits": float,
        "time_weighted_return": float,
    }

    tested_symphonies = 0

    for account_id in account_ids:
        url = (
            f"https://api.composer.trade/api/v2/portfolio/accounts/{account_id}/symphony-stats-meta"
        )
        response = requests.get(url, headers=headers, timeout=15)

        assert response.status_code == 200, (
            f"Expected HTTP 200 from symphony-stats-meta for account {account_id}; "
            f"got {response.status_code}"
        )

        payload = response.json()
        symphonies = payload.get("symphonies", [])

        assert isinstance(symphonies, list), (
            f"symphony-stats-meta 'symphonies' must be a list; got {type(symphonies)}"
        )
        assert len(symphonies) > 0, (
            f"account {account_id}: expected at least 1 symphony in response; got 0"
        )

        for sym in symphonies:
            sym_id = sym.get("id", "<unknown>")
            for field, expected_type in required_fields.items():
                assert field in sym, (
                    f"symphony {sym_id}: required field '{field}' missing from "
                    f"symphony-stats-meta response — M1 analytics will break"
                )
                val = sym[field]
                assert isinstance(val, expected_type), (
                    f"symphony {sym_id}: field '{field}' must be {expected_type.__name__}; "
                    f"got {type(val).__name__}={val!r}"
                )
                assert math.isfinite(val), (
                    f"symphony {sym_id}: field '{field}' must be finite; got {val}"
                )

            tested_symphonies += 1

    assert tested_symphonies > 0, (
        "No symphonies were tested across all accounts — check account IDs"
    )
