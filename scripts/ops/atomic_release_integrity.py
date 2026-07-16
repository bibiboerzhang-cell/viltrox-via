"""Content and Unix-ownership seal for one atomic application release."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

if __package__:
    from .atomic_release_units import LayoutError
else:
    from atomic_release_units import LayoutError


RELEASE_MANIFEST_NAME = ".vkpi-release.json"


def release_nodes(
    release: Path,
    *,
    include_manifest: bool,
) -> list[tuple[Path, os.stat_result]]:
    """Return one lstat snapshot without ever following a payload symlink."""

    nodes: list[tuple[Path, os.stat_result]] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise LayoutError(f"release payload cannot be scanned: {directory}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(release).as_posix()
            if not include_manifest and relative == RELEASE_MANIFEST_NAME:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise LayoutError(
                    f"release payload entry cannot be inspected: {path}: {exc}"
                ) from exc
            nodes.append((path, info))
            if stat.S_ISDIR(info.st_mode):
                visit(path)

    visit(release)
    return nodes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LayoutError(f"release payload file cannot be hashed: {path}: {exc}") from exc
    return digest.hexdigest()


def payload_fingerprint(
    root: Path,
    release: Path,
    *,
    shared_aliases: set[str],
) -> tuple[str, int]:
    """Hash the closed payload tree while treating reviewed shared links as links."""

    records: list[dict[str, str | int]] = []
    for path, info in release_nodes(release, include_manifest=False):
        relative = path.relative_to(release).as_posix()
        if stat.S_ISDIR(info.st_mode):
            records.append({"path": relative, "type": "directory"})
            continue
        if stat.S_ISREG(info.st_mode):
            records.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": info.st_size,
                    "sha256": _sha256_file(path),
                    "executable": int(bool(stat.S_IMODE(info.st_mode) & 0o111)),
                }
            )
            continue
        if stat.S_ISLNK(info.st_mode):
            try:
                link_target = os.readlink(path)
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise LayoutError(f"release payload symlink is invalid: {path}: {exc}") from exc
            if relative in shared_aliases:
                expected = (root / relative).resolve(strict=True)
                if resolved != expected:
                    raise LayoutError(f"shared release link escapes its reviewed root: {path}")
            elif resolved != release and release not in resolved.parents:
                raise LayoutError(f"unreviewed release symlink escapes the payload: {path}")
            records.append({"path": relative, "type": "symlink", "target": link_target})
            continue
        raise LayoutError(f"release payload contains an unsupported special file: {path}")

    canonical = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), len(records)


def make_release_immutable(
    release: Path,
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Transfer the tree to its seal owner and remove every Unix write bit."""

    if owner_uid < 0 or owner_gid < 0:
        raise LayoutError("immutable release owner uid/gid must be non-negative")
    if os.geteuid() != 0 and (owner_uid, owner_gid) != (os.geteuid(), os.getegid()):
        raise LayoutError("only root may transfer an immutable release to another owner")

    nodes = release_nodes(release, include_manifest=True)
    directories: list[tuple[Path, os.stat_result]] = [(release, release.lstat())]
    for path, info in nodes:
        if stat.S_ISDIR(info.st_mode):
            directories.append((path, info))
            continue
        if stat.S_ISLNK(info.st_mode):
            if (info.st_uid, info.st_gid) != (owner_uid, owner_gid):
                os.chown(path, owner_uid, owner_gid, follow_symlinks=False)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise LayoutError(f"release payload contains an unsupported special file: {path}")
        executable = bool(stat.S_IMODE(info.st_mode) & 0o111)
        if (info.st_uid, info.st_gid) != (owner_uid, owner_gid):
            os.chown(path, owner_uid, owner_gid)
        os.chmod(path, 0o555 if executable else 0o444)

    for path, info in sorted(
        directories,
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        if (info.st_uid, info.st_gid) != (owner_uid, owner_gid):
            os.chown(path, owner_uid, owner_gid)
        os.chmod(path, 0o555)


def load_release_manifest(release: Path) -> dict[str, object]:
    manifest_path = release / RELEASE_MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise LayoutError("sealed release manifest is missing or unsafe")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutError("sealed release manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 2:
        raise LayoutError("sealed release manifest schema is not immutable-v2")
    if payload.get("release_id") != release.name:
        raise LayoutError("sealed release manifest id does not match its directory")
    return payload


def verify_sealed_release(
    root: Path,
    release: Path,
    *,
    shared_aliases: set[str],
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> dict[str, object]:
    payload = load_release_manifest(release)
    owner_uid = payload.get("immutable_owner_uid")
    owner_gid = payload.get("immutable_owner_gid")
    if not isinstance(owner_uid, int) or not isinstance(owner_gid, int):
        raise LayoutError("sealed release manifest lost immutable owner identity")
    if expected_owner_uid is not None and owner_uid != expected_owner_uid:
        raise LayoutError("sealed release owner uid does not match the required owner")
    if expected_owner_gid is not None and owner_gid != expected_owner_gid:
        raise LayoutError("sealed release owner gid does not match the required owner")

    parent_info = release.parent.stat()
    if (parent_info.st_uid, parent_info.st_gid) != (owner_uid, owner_gid):
        raise LayoutError("release parent ownership does not match the immutable owner")
    if stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise LayoutError("release parent must not be group/world writable")

    release_info = release.lstat()
    nodes = [(release, release_info), *release_nodes(release, include_manifest=True)]
    for path, info in nodes:
        if (info.st_uid, info.st_gid) != (owner_uid, owner_gid):
            raise LayoutError(f"immutable release ownership mismatch: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            if mode != 0o555:
                raise LayoutError(f"immutable release directory mode mismatch: {path}")
        elif stat.S_ISREG(info.st_mode):
            if mode not in {0o444, 0o555}:
                raise LayoutError(f"immutable release file mode mismatch: {path}")
        elif not stat.S_ISLNK(info.st_mode):
            raise LayoutError(f"immutable release contains an unsupported special file: {path}")

    digest, entry_count = payload_fingerprint(
        root,
        release,
        shared_aliases=shared_aliases,
    )
    if payload.get("payload_sha256") != digest:
        raise LayoutError("sealed release payload digest mismatch")
    if payload.get("payload_entry_count") != entry_count:
        raise LayoutError("sealed release payload entry count mismatch")
    build_sha_path = release / "BUILD_GIT_SHA"
    if (
        not build_sha_path.is_file()
        or build_sha_path.read_text(encoding="utf-8").strip() != payload.get("git_sha")
    ):
        raise LayoutError("sealed release build SHA does not match its manifest")
    return payload
