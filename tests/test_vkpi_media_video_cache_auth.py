"""Private-workspace auth contract for cached video playback."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import media  # noqa: E402


def _client(monkeypatch, video_path: Path, *, gzip: bool = False) -> TestClient:
    monkeypatch.setattr(
        media,
        "get_current_user",
        lambda request: {"id": 1, "role": "admin"}
        if request.cookies.get("via_token") == "valid-session"
        else None,
    )
    monkeypatch.setattr(media, "cached_video_file", lambda _digest: (video_path, "video/mp4"))
    app = FastAPI()
    app.include_router(media.router)
    if gzip:
        app.add_middleware(GZipMiddleware, minimum_size=1)
    return TestClient(app, raise_server_exceptions=False)


def test_cached_video_denies_anonymous_access(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path)

    response = client.get("/api/vkpi-media/video-cache/" + "a" * 64)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Cookie, Authorization"


def test_authenticated_cookie_supports_video_range_playback(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path)
    client.cookies.set("via_token", "valid-session")

    response = client.get(
        "/api/vkpi-media/video-cache/" + "b" * 64,
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-encoding"] == "identity"
    assert response.headers["cache-control"] == "private, max-age=300, must-revalidate"
    assert response.headers["vary"] == "Cookie, Authorization"


def test_video_range_is_never_gzipped_when_client_accepts_gzip(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path, gzip=True)
    client.cookies.set("via_token", "valid-session")

    response = client.get(
        "/api/vkpi-media/video-cache/" + "f" * 64,
        headers={"Range": "bytes=2-5", "Accept-Encoding": "gzip"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-encoding"] == "identity"


def test_authenticated_cookie_supports_full_video_playback(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path)
    client.cookies.set("via_token", "valid-session")

    response = client.get("/api/vkpi-media/video-cache/" + "c" * 64)

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["cache-control"] == "private, max-age=300, must-revalidate"


def test_authenticated_cookie_supports_video_head_probe(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path)
    client.cookies.set("via_token", "valid-session")

    response = client.head("/api/vkpi-media/video-cache/" + "d" * 64)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-encoding"] == "identity"
    assert response.headers["cache-control"] == "private, max-age=300, must-revalidate"
    assert response.headers["vary"] == "Cookie, Authorization"


def test_cached_video_head_probe_denies_anonymous_access(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "fixture.mp4"
    video_path.write_bytes(b"0123456789")
    client = _client(monkeypatch, video_path)

    response = client.head("/api/vkpi-media/video-cache/" + "e" * 64)

    assert response.status_code == 403
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Cookie, Authorization"
