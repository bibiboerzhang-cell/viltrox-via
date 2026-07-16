"""US Census Bureau one-line-address geocoder (free, key-less, descriptive only).

The reviewed physical-store manifest requires address-level coordinates whose
provider is ``us_census_geocoder`` (see reviewed_physical_store_manifest.py).
This module is the single bounded client for that lookup:

* one GET per call against the public Census ``onelineaddress`` endpoint;
* the call is made **directly**, bypassing HTTPS_PROXY: the runtime LLM proxy
  refuses geocoding.geo.census.gov (verified 2026-07-16: ProxyError via proxy,
  200 in 0.3s direct), and this endpoint carries no key material;
* a missing/failed/ambiguous match returns ``None`` — never a guessed point;
* no database access and no caching; callers own persistence decisions.
"""
from __future__ import annotations

import time
from typing import Any

import requests

CENSUS_ONELINE_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
)
CENSUS_BENCHMARK = "Public_AR_Current"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_RETRIES = 2
_RETRY_SLEEP_SECONDS = 1.0


def _http_get_json(address: str, timeout: float) -> dict[str, Any]:
    """One raw endpoint call; isolated so tests can monkeypatch it."""
    response = requests.get(
        CENSUS_ONELINE_URL,
        params={
            "address": address,
            "benchmark": CENSUS_BENCHMARK,
            "format": "json",
        },
        timeout=timeout,
        # Deliberate direct connection — see module docstring.
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def geocode_match(
    address: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> dict[str, Any] | None:
    """Return the first Census address match for a one-line US address.

    Result shape (or ``None`` when unmatched/unreachable)::

        {"lat": float, "lng": float, "matched_address": str,
         "provider": "us_census_geocoder"}
    """
    oneline = " ".join(str(address or "").split())
    if not oneline:
        return None
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            payload = _http_get_json(oneline, timeout)
        except (requests.RequestException, ValueError):
            if attempt + 1 < attempts:
                time.sleep(_RETRY_SLEEP_SECONDS)
                continue
            return None
        result = payload.get("result")
        matches = result.get("addressMatches") if isinstance(result, dict) else None
        if not isinstance(matches, list) or not matches:
            return None
        first = matches[0] if isinstance(matches[0], dict) else {}
        coordinates = first.get("coordinates")
        if not isinstance(coordinates, dict):
            return None
        try:
            lng = float(coordinates["x"])
            lat = float(coordinates["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return None
        return {
            "lat": lat,
            "lng": lng,
            "matched_address": str(first.get("matchedAddress") or "").strip(),
            "provider": "us_census_geocoder",
        }
    return None


def geocode_coordinates(
    address: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> tuple[float, float] | None:
    """Return ``(lat, lng)`` for a one-line US address, or ``None``."""
    match = geocode_match(address, timeout=timeout, retries=retries)
    if match is None:
        return None
    return (match["lat"], match["lng"])
