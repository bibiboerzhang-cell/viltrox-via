from __future__ import annotations

from app.domains.commerce.dealer_source_preflight import (
    audit_one_source,
    evaluate_robots,
    robots_url,
    terms_link_candidates,
)


class _Response:
    def __init__(self, url, status_code, text="", content=b"", content_type="text/plain"):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = content or text.encode()
        self.headers = {"content-type": content_type}


class _Client:
    def __init__(self, robots, source):
        self.robots = robots
        self.source = source

    def get(self, url):
        return self.robots if url.endswith("/robots.txt") else self.source


def test_robots_gate_is_conservative_and_path_specific():
    blocked = evaluate_robots(
        source_url="https://dealer.example/private/list",
        status_code=200,
        text="User-agent: *\nDisallow: /private/\n",
    )
    missing = evaluate_robots(
        source_url="https://dealer.example/locations", status_code=404, text=None
    )
    unavailable = evaluate_robots(
        source_url="https://dealer.example/locations", status_code=503, text=None
    )
    assert blocked["fetch_allowed"] is False
    assert missing["fetch_allowed"] is True
    assert unavailable["fetch_allowed"] is False
    assert robots_url("https://dealer.example/path") == "https://dealer.example/robots.txt"


def test_terms_candidates_are_same_host_and_bounded():
    html = """
      <a href='/terms-of-use'>Terms</a>
      <a href='https://dealer.example/privacy'>Privacy</a>
      <a href='https://third.example/legal'>Third party</a>
    """
    assert terms_link_candidates("https://dealer.example/stores", html) == [
        "https://dealer.example/privacy",
        "https://dealer.example/terms-of-use",
    ]


def test_audit_snapshot_never_activates_or_extracts_candidates():
    source_url = "https://dealer.example/stores"
    client = _Client(
        _Response("https://dealer.example/robots.txt", 404),
        _Response(
            source_url,
            200,
            text="<html><a href='/terms'>Terms</a></html>",
            content_type="text/html; charset=utf-8",
        ),
    )
    result = audit_one_source(
        {
            "id": "dealer_example",
            "publisher": "Example",
            "source_kind": "retailer_location_directory",
            "canonical_url": source_url,
        },
        client,
    )
    assert result["technical_status"] == "reachable"
    assert result["snapshot"]["terms_link_candidates"] == [
        "https://dealer.example/terms"
    ]
    assert result["terms_legal_approval"] == "not_performed_requires_human"
    assert result["source_activation_recommended"] is False
    assert result["candidate_extraction_performed"] is False
    assert result["business_rows_written"] == 0
