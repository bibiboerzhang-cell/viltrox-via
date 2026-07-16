from __future__ import annotations

import hashlib

from app.domains.commerce.dealer_candidate_quarantine import (
    build_quarantine,
    eligible_preflight_sources,
    extract_document_candidates,
)


CAPTURED_AT = "2026-07-15T20:00:00Z"


def _source(source_id: str, url: str, publisher: str = "Example Camera") -> dict:
    return {
        "id": source_id,
        "source_registry_id": source_id,
        "publisher": publisher,
        "source_kind": "retailer_location_directory",
        "canonical_url": url,
        "manufacturer_authorization_scope": (
            "Retailer-owned store identity only; no manufacturer authorization is inferred"
        ),
    }


def _preflight_row(
    source_id: str,
    url: str,
    *,
    technical_status: str = "reachable",
    fetch_allowed: bool = True,
    http_status: int = 200,
    snapshot_sha256: str = "a" * 64,
) -> dict:
    return {
        "source_registry_id": source_id,
        "publisher": "Example Camera",
        "source_kind": "retailer_location_directory",
        "canonical_url": url,
        "technical_status": technical_status,
        "robots": {
            "status": "reviewed",
            "fetch_allowed": fetch_allowed,
            "reason": (
                "robots_allows_source_path"
                if fetch_allowed
                else "robots_disallows_source_path"
            ),
            "sha256": "b" * 64,
        },
        "snapshot": {"http_status": http_status, "sha256": snapshot_sha256},
    }


def test_preflight_selection_excludes_blocked_and_http_error_without_calling_them():
    preflight = {
        "sources": [
            _preflight_row("allowed", "https://allowed.example/stores"),
            _preflight_row(
                "robots_blocked",
                "https://blocked.example/stores",
                technical_status="blocked_by_robots_gate",
                fetch_allowed=False,
                http_status=0,
            ),
            _preflight_row(
                "http_error",
                "https://error.example/stores",
                technical_status="http_error",
                http_status=403,
            ),
        ]
    }

    eligible, excluded = eligible_preflight_sources(preflight)

    assert [row["source_registry_id"] for row in eligible] == ["allowed"]
    assert {row["source_registry_id"] for row in excluded} == {
        "robots_blocked",
        "http_error",
    }
    assert all(row["network_called"] is False for row in excluded)
    assert "robots_path_not_allowed" in next(
        row["reasons"] for row in excluded if row["source_registry_id"] == "robots_blocked"
    )


def test_json_ld_address_wins_over_duplicate_visible_text_and_keeps_unknown_brand_truth():
    source = _source("dealer_example", "https://dealer.example/stores")
    html = b"""
      <html><head><script type="application/ld+json">
      {"@context":"https://schema.org","@type":"ElectronicsStore",
       "identifier":"nyc-1","name":"Example Camera Downtown",
       "url":"https://dealer.example/stores/nyc",
       "telephone":"(212) 555-0199","email":"store@dealer.example",
       "address":{"@type":"PostalAddress","streetAddress":"123 Broadway",
       "addressLocality":"New York","addressRegion":"NY","postalCode":"10006",
       "addressCountry":"US"},"geo":{"latitude":40.7087,"longitude":-74.0119}}
      </script></head><body>
      <p>123 Broadway, New York, NY 10006</p><p>(212) 555-0199</p>
      </body></html>
    """

    candidates, issues = extract_document_candidates(
        source=source,
        content=html,
        content_type="text/html; charset=utf-8",
        captured_at=CAPTURED_AT,
        final_url=source["canonical_url"],
    )

    assert issues == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["address"]["formatted"] == "123 Broadway, New York, NY 10006, US"
    assert candidate["contact"] == {
        "phone": "(212) 555-0199",
        "email": "store@dealer.example",
        "website": "https://dealer.example/stores/nyc",
    }
    assert candidate["map_fields"] == {
        "latitude": 40.7087,
        "longitude": -74.0119,
        "geocoding_status": "publisher_coordinates",
    }
    assert candidate["evidence"]["quality_tier"] == "high"
    assert candidate["truth_dimensions"]["viltrox_authorization"] == "unknown"
    assert candidate["truth_dimensions"]["viltrox_product_presence"] == "unknown"
    assert candidate["legal_approval"] is False
    assert candidate["source_activation"] is False
    assert candidate["promotion_eligible"] is False
    assert candidate["business_rows_written"] == 0


def test_json_ld_social_same_as_list_does_not_become_an_invalid_website_url():
    source = _source("dealer_social", "https://dealer.example/stores")
    html = b"""
      <script type="application/ld+json">
      {"@type":"ElectronicsStore","name":"Example Camera",
       "sameAs":["https://social.example/example","https://video.example/example"],
       "address":{"streetAddress":"123 Broadway","addressLocality":"New York",
       "addressRegion":"NY","postalCode":"10006","addressCountry":"US"}}
      </script>
    """

    candidates, issues = extract_document_candidates(
        source=source,
        content=html,
        content_type="text/html",
        captured_at=CAPTURED_AT,
        final_url=source["canonical_url"],
    )

    assert issues == []
    assert len(candidates) == 1
    assert candidates[0]["contact"]["website"] == source["canonical_url"]


def test_visible_complete_address_is_candidate_but_incomplete_location_is_not_guessed():
    source = _source("dealer_visible", "https://dealer.example/contact")
    html = b"""
      <html><body><address>
      456 Main Street<br>Seattle, WA 98101<br>206-555-0107
      </address><p>Other branches: Portland, OR; Boise, ID.</p></body></html>
    """

    candidates, issues = extract_document_candidates(
        source=source,
        content=html,
        content_type="text/html",
        captured_at=CAPTURED_AT,
        final_url=source["canonical_url"],
    )

    assert issues == []
    assert len(candidates) == 1
    assert candidates[0]["address"]["formatted"] == "456 Main Street, Seattle, WA 98101, US"
    assert candidates[0]["contact"]["phone"] == "206-555-0107"
    assert candidates[0]["evidence"]["quality_tier"] == "medium"
    assert "Portland" not in candidates[0]["address"]["formatted"]


def test_quarantine_calls_only_eligible_source_and_reports_no_business_writes():
    allowed_url = "https://allowed.example/stores"
    blocked_url = "https://blocked.example/stores"
    html = b"<address>789 Pine Road\nAustin, TX 78701\n(512) 555-0101</address>"
    capture_sha = hashlib.sha256(html).hexdigest()
    preflight = {
        "sources": [
            _preflight_row("allowed", allowed_url, snapshot_sha256=capture_sha),
            _preflight_row(
                "blocked",
                blocked_url,
                technical_status="blocked_by_robots_gate",
                fetch_allowed=False,
                http_status=0,
            ),
        ]
    }
    registry = {
        "registry_version": "test.1",
        "dealer_discovery_sources": [
            _source("allowed", allowed_url),
            _source("blocked", blocked_url),
        ],
    }
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        assert url == allowed_url
        return {
            "status_code": 200,
            "final_url": allowed_url,
            "content_type": "text/html",
            "content": html,
        }

    payload = build_quarantine(
        preflight=preflight,
        registry=registry,
        captured_at=CAPTURED_AT,
        fetch=fetch,
        preflight_sha256="c" * 64,
        registry_sha256="d" * 64,
    )

    assert calls == [allowed_url]
    assert payload["called_source_ids"] == ["allowed"]
    assert payload["blocked_source_calls"] == []
    assert payload["summary"] == {
        "registered_source_count": 2,
        "preflight_source_count": 2,
        "eligible_source_count": 1,
        "excluded_source_count": 1,
        "fetched_source_count": 1,
        "sources_with_candidates": 1,
        "source_candidate_coverage_rate": 1.0,
        "candidate_count": 1,
        "entity_candidate_count": 1,
        "complete_address_count": 1,
        "unique_address_count": 1,
        "cross_source_duplicate_group_count": 0,
        "possible_near_duplicate_group_count": 0,
        "state_coverage_count": 1,
        "state_codes": ["TX"],
        "phone_coverage_count": 1,
        "phone_coverage_rate": 1.0,
        "email_coverage_count": 0,
        "email_coverage_rate": 0.0,
        "website_coverage_count": 1,
        "website_coverage_rate": 1.0,
        "phone_or_email_coverage_count": 1,
        "phone_or_email_coverage_rate": 1.0,
        "publisher_coordinate_count": 0,
        "publisher_coordinate_coverage_rate": 0.0,
        "manufacturer_authorization_scope_field_count": 1,
        "manufacturer_authorization_scope_field_rate": 1.0,
        "viltrox_authorization_evidence_count": 0,
        "viltrox_product_presence_evidence_count": 0,
        "preflight_snapshot_hash_match_count": 1,
        "blocked_source_call_count": 0,
        "legal_approval_count": 0,
        "source_activation_count": 0,
        "business_rows_written": 0,
    }
    assert payload["contract"]["database_accessed"] is False
    assert payload["contract"]["candidate_rows_written"] == 0
    assert payload["contract"]["business_rows_written"] == 0
    assert payload["contract"]["legal_approval"] is False
    assert payload["contract"]["source_activation"] is False
    assert payload["sources"][0]["preflight_gate"]["robots_fetch_allowed"] is True
    assert payload["sources"][0]["preflight_gate"]["terms_legal_approval"] is False


def test_same_address_across_publishers_is_reported_not_silently_merged():
    first_url = "https://first.example/stores"
    second_url = "https://second.example/stores"
    html = b"<address>10 Market Street\nDenver, CO 80202</address>"
    preflight = {
        "sources": [
            _preflight_row("first", first_url),
            _preflight_row("second", second_url),
        ]
    }
    registry = {
        "registry_version": "test.2",
        "dealer_discovery_sources": [
            _source("first", first_url, "First Camera"),
            _source("second", second_url, "Second Camera"),
        ],
    }

    payload = build_quarantine(
        preflight=preflight,
        registry=registry,
        captured_at=CAPTURED_AT,
        fetch=lambda url: {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/html",
            "content": html,
        },
        preflight_sha256="e" * 64,
        registry_sha256="f" * 64,
    )

    assert payload["summary"]["candidate_count"] == 2
    assert payload["summary"]["unique_address_count"] == 1
    assert payload["summary"]["cross_source_duplicate_group_count"] == 1
    assert payload["cross_source_duplicate_groups"][0]["count"] == 2


def test_same_house_city_and_postal_with_street_variants_is_flagged_for_review():
    url = "https://dealer.example/locations"
    html = b"""
      <script type="application/ld+json">[
        {"@type":"ElectronicsStore","name":"North",
         "address":{"streetAddress":"5420 Academy Blvd N","addressLocality":"Colorado Springs",
         "addressRegion":"CO","postalCode":"80918","addressCountry":"US"}},
        {"@type":"ElectronicsStore","name":"North alternate",
         "address":{"streetAddress":"5420 N. Academy Blvd","addressLocality":"Colorado Springs",
         "addressRegion":"CO","postalCode":"80918","addressCountry":"US"}}
      ]</script>
    """
    preflight = {"sources": [_preflight_row("dealer", url)]}
    registry = {
        "registry_version": "test.3",
        "dealer_discovery_sources": [_source("dealer", url)],
    }

    payload = build_quarantine(
        preflight=preflight,
        registry=registry,
        captured_at=CAPTURED_AT,
        fetch=lambda _url: {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/html",
            "content": html,
        },
        preflight_sha256="1" * 64,
        registry_sha256="2" * 64,
    )

    assert payload["summary"]["candidate_count"] == 2
    assert payload["summary"]["possible_near_duplicate_group_count"] == 1
    assert payload["possible_near_duplicate_groups"][0]["count"] == 2
