"""Exact, reviewed frontend public files that are not part of ``/assets``."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
router = APIRouter()


def _read_frontend_world_basemap() -> tuple[bytes, int]:
    """Read the one reviewed ``dist/data`` asset without opening that tree.

    Every component below the fixed dist root is a literal opened with
    ``O_NOFOLLOW``. A replaced data directory or file symlink therefore fails
    closed instead of escaping the frozen candidate.
    """

    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise HTTPException(status_code=404, detail="Frontend world basemap not found")

    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | close_on_exec
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(FRONTEND_DIST_DIR, directory_flags))
        descriptors.append(os.open("data", directory_flags, dir_fd=descriptors[-1]))
        descriptors.append(
            os.open("world-110m.json", file_flags, dir_fd=descriptors[-1])
        )
        metadata = os.fstat(descriptors[-1])
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("world basemap is not a regular file")

        chunks: list[bytes] = []
        while chunk := os.read(descriptors[-1], 64 * 1024):
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size:
            raise OSError("world basemap changed while being read")
        return payload, int(metadata.st_size)
    except OSError:
        raise HTTPException(
            status_code=404,
            detail="Frontend world basemap not found",
        ) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _serve_frontend_world_basemap(*, head_only: bool) -> Response:
    payload, size = _read_frontend_world_basemap()
    return Response(
        content=b"" if head_only else payload,
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Length": str(size),
        },
    )


@router.api_route(
    "/data/world-110m.json",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def frontend_world_basemap(request: Request) -> Response:
    return _serve_frontend_world_basemap(head_only=request.method == "HEAD")


__all__ = ["router"]
