"""census_geocode — bounded, mocked, zero-network unit tests."""
from __future__ import annotations

import pytest
import requests

from app.domains.commerce import census_geocode


def _match_payload(lat: float = 40.753308, lng: float = -73.996272) -> dict:
    return {
        "result": {
            "addressMatches": [
                {
                    "matchedAddress": "420 9TH AVE, NEW YORK, NY, 10001",
                    "coordinates": {"x": lng, "y": lat},
                }
            ]
        }
    }


def test_geocode_match_returns_first_address_match(monkeypatch):
    calls: list[tuple[str, float]] = []

    def fake_get(address, timeout):
        calls.append((address, timeout))
        return _match_payload()

    monkeypatch.setattr(census_geocode, "_http_get_json", fake_get)
    match = census_geocode.geocode_match("420 9th Ave, New York, NY 10001")
    assert match == {
        "lat": 40.753308,
        "lng": -73.996272,
        "matched_address": "420 9TH AVE, NEW YORK, NY, 10001",
        "provider": "us_census_geocoder",
    }
    assert calls == [("420 9th Ave, New York, NY 10001", 5.0)]


def test_geocode_coordinates_tuple_and_whitespace_normalization(monkeypatch):
    seen: list[str] = []

    def fake_get(address, timeout):
        seen.append(address)
        return _match_payload(34.068877, -118.361468)

    monkeypatch.setattr(census_geocode, "_http_get_json", fake_get)
    coordinates = census_geocode.geocode_coordinates("431 S Fairfax Ave,\n Los Angeles,  CA")
    assert coordinates == (34.068877, -118.361468)
    assert seen == ["431 S Fairfax Ave, Los Angeles, CA"]


def test_no_match_returns_none_without_retry(monkeypatch):
    calls = {"n": 0}

    def fake_get(address, timeout):
        calls["n"] += 1
        return {"result": {"addressMatches": []}}

    monkeypatch.setattr(census_geocode, "_http_get_json", fake_get)
    assert census_geocode.geocode_match("nowhere at all") is None
    assert calls["n"] == 1  # an empty match is authoritative, not retried


def test_transient_error_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(address, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("boom")
        return _match_payload()

    monkeypatch.setattr(census_geocode, "_http_get_json", fake_get)
    monkeypatch.setattr(census_geocode.time, "sleep", lambda seconds: None)
    match = census_geocode.geocode_match("420 9th Ave, New York, NY", retries=2)
    assert match is not None and match["lat"] == pytest.approx(40.753308)
    assert calls["n"] == 2


def test_persistent_error_exhausts_retries_and_returns_none(monkeypatch):
    calls = {"n": 0}

    def fake_get(address, timeout):
        calls["n"] += 1
        raise requests.Timeout("slow")

    monkeypatch.setattr(census_geocode, "_http_get_json", fake_get)
    monkeypatch.setattr(census_geocode.time, "sleep", lambda seconds: None)
    assert census_geocode.geocode_match("420 9th Ave", retries=2) is None
    assert calls["n"] == 3  # 1 try + 2 retries


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": None},
        {"result": {"addressMatches": [{"coordinates": {"x": "bad", "y": None}}]}},
        {"result": {"addressMatches": [{"coordinates": {"x": -73.9, "y": 123.0}}]}},
        {"result": {"addressMatches": ["not-a-dict"]}},
    ],
)
def test_malformed_or_out_of_range_payloads_return_none(monkeypatch, payload):
    monkeypatch.setattr(census_geocode, "_http_get_json", lambda address, timeout: payload)
    assert census_geocode.geocode_match("420 9th Ave, New York, NY") is None


def test_empty_address_short_circuits(monkeypatch):
    def fail_get(address, timeout):  # pragma: no cover - must not be called
        raise AssertionError("network path must not run for empty input")

    monkeypatch.setattr(census_geocode, "_http_get_json", fail_get)
    assert census_geocode.geocode_match("   ") is None
    assert census_geocode.geocode_coordinates("") is None
