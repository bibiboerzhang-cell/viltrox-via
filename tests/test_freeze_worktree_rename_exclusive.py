from __future__ import annotations

import ctypes
import errno
from pathlib import Path

import pytest

from scripts.ops import freeze_worktree_contract as contract


def test_rename_exclusive_publishes_without_replacing_target(tmp_path: Path) -> None:
    source = tmp_path / "candidate.tmp"
    target = tmp_path / "candidate"
    source.write_bytes(b"new")

    contract.rename_exclusive(source, target)

    assert not source.exists()
    assert target.read_bytes() == b"new"

    contender = tmp_path / "contender.tmp"
    contender.write_bytes(b"contender")
    with pytest.raises(contract.FreezeError, match="exclusive candidate publish failed"):
        contract.rename_exclusive(contender, target)

    assert contender.read_bytes() == b"contender"
    assert target.read_bytes() == b"new"


def test_linux_rename_exclusive_uses_renameat2_noreplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeRenameAt2:
        argtypes: object = None
        restype: object = None

        def __call__(self, *arguments: object) -> int:
            calls.append(arguments)
            ctypes.set_errno(errno.EEXIST)
            return -1

    class FakeLibc:
        renameat2 = FakeRenameAt2()

    monkeypatch.setattr(contract.sys, "platform", "linux")
    monkeypatch.setattr(contract.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())

    with pytest.raises(
        contract.FreezeError,
        match=f"exclusive candidate publish failed: errno={errno.EEXIST}",
    ):
        contract.rename_exclusive(Path("source"), Path("target"))

    assert calls == [(-100, b"source", -100, b"target", 1)]


def test_rename_exclusive_fails_closed_when_platform_has_no_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract.sys, "platform", "unsupported")

    with pytest.raises(
        contract.FreezeError,
        match="exclusive candidate publish is unavailable",
    ):
        contract.rename_exclusive(Path("source"), Path("target"))
