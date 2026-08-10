from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import frontend_static, main, main_release_validation


WORLD_PAYLOAD = (
    b'{"type":"Topology","objects":{"countries":'
    b'{"type":"GeometryCollection","geometries":[]}},"arcs":[]}\n'
)


def _install_dist_basemap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = tmp_path / "dist"
    data = dist / "data"
    data.mkdir(parents=True)
    world = data / "world-110m.json"
    world.write_bytes(WORLD_PAYLOAD)
    monkeypatch.setattr(frontend_static, "FRONTEND_DIST_DIR", dist)
    return world


def _set_fence(monkeypatch: pytest.MonkeyPatch, active: bool) -> None:
    monkeypatch.setattr(
        main_release_validation,
        "release_validation_active",
        lambda: active,
    )


@pytest.mark.parametrize("fenced", [False, True], ids=["live", "fenced"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_exact_world_basemap_get_and_head_work_live_and_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fenced: bool,
    method: str,
) -> None:
    _install_dist_basemap(tmp_path, monkeypatch)
    _set_fence(monkeypatch, fenced)

    response = TestClient(main.app, raise_server_exceptions=False).request(
        method,
        "/data/world-110m.json",
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(WORLD_PAYLOAD))
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content == (WORLD_PAYLOAD if method == "GET" else b"")


@pytest.mark.parametrize(
    ("fenced", "expected_status"),
    [(False, 404), (True, 503)],
    ids=["live-not-found", "fenced-blocked"],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "path",
    [
        "/data/",
        "/data/other.json",
        "/data/world-110m.json.bak",
        "/data/world-110m.json/extra",
        "/data/subdir/world-110m.json",
        "/data/%2e%2e/secret.json",
    ],
)
def test_adjacent_data_paths_are_not_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fenced: bool,
    expected_status: int,
    method: str,
    path: str,
) -> None:
    _install_dist_basemap(tmp_path, monkeypatch)
    _set_fence(monkeypatch, fenced)

    response = TestClient(main.app, raise_server_exceptions=False).request(method, path)

    assert response.status_code == expected_status
    if fenced and method == "GET":
        assert response.json()["code"] == "release_validation_fenced"
    if fenced and method == "HEAD":
        assert response.content == b""


@pytest.mark.parametrize("fenced", [False, True], ids=["live", "fenced"])
@pytest.mark.parametrize("replacement", ["file-symlink", "data-symlink", "directory"])
def test_world_basemap_rejects_symlinks_and_non_regular_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fenced: bool,
    replacement: str,
) -> None:
    world = _install_dist_basemap(tmp_path, monkeypatch)
    dist = world.parents[1]
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_world = outside / "world-110m.json"
    outside_world.write_bytes(WORLD_PAYLOAD)

    if replacement == "file-symlink":
        world.unlink()
        world.symlink_to(outside_world)
    elif replacement == "data-symlink":
        world.unlink()
        world.parent.rmdir()
        world.parent.symlink_to(outside, target_is_directory=True)
    else:
        world.unlink()
        world.mkdir()

    assert dist.is_dir()
    _set_fence(monkeypatch, fenced)
    response = TestClient(main.app, raise_server_exceptions=False).get(
        "/data/world-110m.json"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Frontend world basemap not found"
