import pytest

from app.core import release_validation


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_exact_local_world_basemap_remains_available(method: str) -> None:
    assert release_validation.release_validation_request_allowed(
        method, "/data/world-110m.json"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/data/",
        "/data/world-110m.json.bak",
        "/data/other.json",
        "/data/subdir/world-110m.json",
    ],
)
def test_local_world_basemap_allowance_does_not_open_adjacent_data_paths(path: str) -> None:
    assert not release_validation.release_validation_request_allowed("GET", path)
    assert not release_validation.release_validation_request_allowed("HEAD", path)
