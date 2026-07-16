from __future__ import annotations

import os
from pathlib import Path

from test_atomic_release_layout import _layout, _prepare, _release


def test_first_release_legacy_snapshot_omits_reviewed_host_tool_artifacts(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-host-artifacts"
    _release(root, release_id, "a" * 40)

    codegraph = root / ".codegraph"
    codegraph.mkdir()
    os.mkfifo(codegraph / "daemon.sock")
    video_workspace = root / "video-production-platform"
    video_workspace.mkdir()
    (video_workspace / "node_modules").symlink_to("../frontend/node_modules")

    _prepare(root, unit_dir, release_id)

    legacy = (root / "previous").resolve()
    assert legacy.name == f"legacy-before-{release_id}"
    assert not (legacy / ".codegraph").exists()
    assert not (legacy / "video-production-platform").exists()
    assert (legacy / "legacy.txt").read_text(encoding="utf-8") == "old-running-tree\n"
