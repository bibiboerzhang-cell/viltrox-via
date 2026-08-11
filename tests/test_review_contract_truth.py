from __future__ import annotations

from app.domains.platform import review_contract


def test_review_text_rejects_iteratively_encoded_credentials() -> None:
    for value in (
        "access%255Ftoken%253DSECRETVALUE123456",
        "Bearer%2520abcdefghijklmnopqrstuvwxyz",
        "ghp%255Fabcdefghijklmnopqrstuvwxyz123456",
    ):
        assert review_contract.normalize_review_text(value, max_length=500) is None


def test_evidence_url_drops_query_fragment_and_refuses_userinfo() -> None:
    safe = review_contract.normalize_evidence(
        [{"source": "url", "reference": "https://example.com/proof?id=7#secret"}]
    )
    assert safe == [{"source": "url", "reference": "https://example.com/proof", "type": "reference"}]
    assert review_contract.normalize_evidence(
        [{"source": "url", "reference": "https://user:pass@example.com/proof"}]
    ) is None


def test_review_snapshot_omits_sensitive_keys_and_signed_urls() -> None:
    snapshot = review_contract.redact_review_snapshot(
        {
            "status": "ok",
            "api_key": "must-not-render",
            "asset": "https://cdn.example.com/a.jpg?X-Amz-Signature=secret",
            "score": 1.0,
        }
    )
    assert snapshot == {"asset": "[REDACTED]", "score": 1.0, "status": "ok"}
    canonical = review_contract.canonical_review_json(snapshot)
    assert "must-not-render" not in canonical
    assert "Signature" not in canonical
    assert len(review_contract.review_snapshot_sha256(snapshot)) == 64


def test_review_snapshot_omits_generic_and_nested_secret_keys() -> None:
    snapshot = review_contract.redact_review_snapshot(
        {
            "token": "opaque-provider-value-1234567890",
            "authToken": "opaque-auth-value-1234567890",
            "nested": {
                "session_token": "opaque-session-value-1234567890",
                "private_key": "opaque-private-value-1234567890",
                "safe_count": 3,
            },
        }
    )
    assert snapshot == {"nested": {"safe_count": 3}}
