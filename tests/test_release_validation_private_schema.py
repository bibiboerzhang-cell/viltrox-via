from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main_release_validation
from app.core import release_validation


ROBOTS_DENY_POLICY = "noindex, nofollow, noarchive, nosnippet, noimageindex"


def _fenced_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # Keep FastAPI's schema routes enabled to prove the fence cannot expose them
    # even if the deployment configuration is accidentally permissive.
    app = FastAPI()
    app.add_middleware(main_release_validation.ReleaseValidationFenceMiddleware)
    monkeypatch.setattr(
        main_release_validation,
        "release_validation_active",
        lambda: True,
    )
    return app


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_fence_always_closes_private_schema_routes(
    method: str,
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not release_validation.release_validation_request_allowed(method, path)

    with TestClient(_fenced_app(monkeypatch)) as client:
        response = client.request(method, f"{path}?release_probe=1")

    assert response.status_code == 404
    assert response.headers["x-robots-tag"] == ROBOTS_DENY_POLICY
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "release_validation_fenced" not in response.text
    assert "openapi" not in response.text.lower()
    if method == "GET":
        assert response.json() == {"detail": "Not Found"}
    else:
        assert response.content == b""


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/docs/"),
        ("GET", "/docs-extra"),
        ("GET", "/openapi.json.bak"),
        ("POST", "/docs"),
    ],
)
def test_adjacent_unknown_routes_remain_release_fenced(
    method: str,
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(_fenced_app(monkeypatch)) as client:
        response = client.request(method, path)

    assert response.status_code == 503
    assert response.json()["code"] == "release_validation_fenced"
