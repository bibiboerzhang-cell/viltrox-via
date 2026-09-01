from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from scripts.ops.candidate_physical_tree import candidate_verification_mirror
from scripts.ops.freeze_worktree_contract import FreezeError
from scripts.ops.strict_runtime_seatbelt import trusted_user_home


def _fixture(root: Path) -> tuple[Path, list[dict[str, object]]]:
    candidate = root / "candidate"
    candidate.mkdir()
    payload = candidate / "payload.txt"
    payload.write_text("bound\n", encoding="utf-8")
    payload.chmod(0o640)
    data = payload.read_bytes()
    return candidate, [
        {
            "mode": "0640",
            "path": "payload.txt",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    ]


def test_verification_mirror_is_independent_and_binds_all_digests(
    tmp_path: Path,
) -> None:
    source, files = _fixture(tmp_path)

    with candidate_verification_mirror(source, files) as (mirror, proof):
        assert mirror.is_relative_to(trusted_user_home())
        assert (mirror / "payload.txt").read_bytes() == b"bound\n"
        assert os.stat(source / "payload.txt").st_ino != os.stat(
            mirror / "payload.txt"
        ).st_ino
        assert os.stat(mirror / "payload.txt").st_nlink == 1

    digest = proof["candidate_digest_before"]
    assert proof == {
        "status": "passed",
        "copy_method": "independent_physical_files",
        "file_count": 1,
        "candidate_digest_before": digest,
        "mirror_digest_before": digest,
        "candidate_digest_after": digest,
        "mirror_digest_after": digest,
    }


def test_verification_mirror_revalidates_bytes_on_gate_failure(
    tmp_path: Path,
) -> None:
    source, files = _fixture(tmp_path)

    with pytest.raises(FreezeError, match="mirror source changed"):
        with candidate_verification_mirror(source, files) as (mirror, _proof):
            (mirror / "payload.txt").write_text("tampered\n", encoding="utf-8")
            raise RuntimeError("gate failed")

    assert (source / "payload.txt").read_bytes() == b"bound\n"
