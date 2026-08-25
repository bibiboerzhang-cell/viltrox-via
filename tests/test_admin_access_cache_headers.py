from __future__ import annotations

from starlette.responses import Response

from app.services.security.admin_access import apply_admin_security_headers


def test_admin_json_defaults_to_private_no_store_and_auth_vary() -> None:
    response = apply_admin_security_headers("/api/admin/vkpi/team-status", Response())

    assert response.headers["cache-control"] == "no-store"
    assert {
        token.strip().lower()
        for token in response.headers["vary"].split(",")
    } == {"authorization", "cookie"}


def test_explicit_private_media_cache_policy_is_preserved_without_new_vary() -> None:
    response = Response(
        headers={"Cache-Control": "private, max-age=300, must-revalidate"},
    )

    observed = apply_admin_security_headers("/api/admin/media/video-cache/42", response)

    assert observed.headers["cache-control"] == "private, max-age=300, must-revalidate"
    assert "vary" not in observed.headers


def test_explicit_public_immutable_media_cache_policy_is_preserved() -> None:
    response = Response(
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )

    observed = apply_admin_security_headers("/api/admin/media/image-cache/42", response)

    assert observed.headers["cache-control"] == "public, max-age=604800, immutable"
    assert "vary" not in observed.headers


def test_explicit_no_store_preserves_existing_vary_and_merges_auth_transports() -> None:
    response = Response(
        headers={"Cache-Control": "private, no-store", "Vary": "Accept-Encoding, cookie"},
    )

    observed = apply_admin_security_headers("/api/vios/private", response)

    assert observed.headers["cache-control"] == "private, no-store"
    assert observed.headers["vary"] == "Accept-Encoding, cookie, Authorization"
