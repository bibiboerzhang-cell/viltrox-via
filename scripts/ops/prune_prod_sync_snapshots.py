#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retention window for ``runtime/prod-sync`` snapshots.

``sync_prod_snapshot_to_local.sh`` appends one dated snapshot per run and never
prunes, so the directory grows without bound (observed 2026-07-17: 30G / 425
snapshots).  Each snapshot is a regenerable prod DB dump plus media-cache
manifests, so keeping only the newest ``--keep`` is safe — prod still holds the
source.  This is the missing retention policy the sync script never had.

Safety:
  * dry-run by default; deletion requires ``--apply``.
  * never touches non-dated entries (logs, ``latest`` symlink, ``official-*``,
    ``manual-*``) — only ``^\\d{8}T`` snapshot directories are candidates.
  * always keeps the newest ``--keep`` snapshots AND whatever ``latest`` points
    to, even if that would exceed the keep count.

Usage:
  python scripts/ops/prune_prod_sync_snapshots.py            # dry-run, keep 5
  python scripts/ops/prune_prod_sync_snapshots.py --keep 3 --apply
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402
SYNC_DIR = REPO / "runtime" / "prod-sync"
SNAPSHOT_RE = re.compile(r"^\d{8}T\d{6}Z")


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def _human(n: int) -> str:
    val = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if val < 1024 or unit == "T":
            return f"{val:.1f}{unit}"
        val /= 1024
    return f"{val:.1f}T"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=SYNC_DIR)
    ap.add_argument("--keep", type=int, default=5, help="newest snapshots to retain")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args(argv)

    sync_dir = args.dir
    if not sync_dir.is_dir():
        stdout_out(f"error: {sync_dir} is not a directory", file=sys.stderr)
        return 2
    if args.keep < 1:
        stdout_out("error: --keep must be >= 1", file=sys.stderr)
        return 2

    # Resolve the `latest` symlink target so it is never pruned.
    latest_target = ""
    latest_link = sync_dir / "latest"
    if latest_link.is_symlink():
        latest_target = os.readlink(latest_link).rstrip("/").split("/")[-1]

    snapshots = sorted(
        (p.name for p in sync_dir.iterdir() if p.is_dir() and SNAPSHOT_RE.match(p.name)),
    )
    keep = set(snapshots[-args.keep :])
    if latest_target:
        keep.add(latest_target)
    prune = [s for s in snapshots if s not in keep]

    stdout_out(f"prod-sync retention · dir={sync_dir}")
    stdout_out(f"snapshots total={len(snapshots)} keep={len(keep)} prune={len(prune)} (keep newest {args.keep} + latest)")
    if latest_target:
        stdout_out(f"latest -> {latest_target} (protected)")

    reclaim = 0
    for name in prune:
        target = sync_dir / name
        size = _dir_bytes(target)
        reclaim += size
        if args.apply:
            shutil.rmtree(target, ignore_errors=True)
            stdout_out(f"deleted {name}  {_human(size)}")
        else:
            stdout_out(f"would delete {name}  {_human(size)}")

    verb = "reclaimed" if args.apply else "would reclaim"
    stdout_out(f"{verb} {_human(reclaim)} across {len(prune)} snapshots")
    if not args.apply and prune:
        stdout_out("dry-run only; re-run with --apply to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
