#!/usr/bin/env python3
"""Build a read-only, deterministic inventory for a dirty release worktree.

The command never stages, moves, deletes, restores, or commits files.  It only
records the current Git status and groups paths into review-sized lanes so a
release candidate can be selected without treating the whole worktree as one
opaque change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


_STDOUT_UTILS_DIR = Path(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in sys.path:
    sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402


SCHEMA = "vkpi.worktree-release-scope.v1"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _status_entries(root: Path) -> list[tuple[str, str]]:
    raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    parts = raw.split(b"\0")
    rows: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        index += 1
        if not part:
            continue
        text = part.decode("utf-8", errors="surrogateescape")
        status = text[:2]
        path = text[3:]
        if "R" in status or "C" in status:
            if index >= len(parts) or not parts[index]:
                raise RuntimeError(f"rename/copy status missing source path: {text!r}")
            source = parts[index].decode("utf-8", errors="surrogateescape")
            index += 1
            path = f"{source} -> {path}"
        rows.append((status, path))
    return rows


def _category(path: str) -> str:
    value = path.lower()
    name = Path(path.split(" -> ")[-1]).name.lower()

    release_tokens = (
        "atomic_release",
        "staging_db",
        "restore_rehearsal",
        "legacy_to_atomic",
        "deploy_local_to_cloud",
        "verify_runtime",
        "local_release_acceptance",
        "release_gate",
        "runtime_journal",
        "storage_dr",
        "worker_release",
        "worktree_release",
        "scheduler_fleet",
        "private_surface",
        "security",
    )
    if value.startswith((".github/", "deploy/")) or name in {
        "docker-compose.yml",
        "makefile",
    } or any(token in value for token in release_tokens):
        return "p0_release_reliability"

    dealer_event_tokens = (
        "dealer",
        "event_radar",
        "/events/",
        "event_",
        "source_passport",
        "us_coverage",
        "candidate_staging",
    )
    if any(token in value for token in dealer_event_tokens):
        return "p1_dealer_event"

    llm_tokens = (
        "llm",
        "model_registry",
        "model_readiness",
        "model_evaluation",
        "marketing_advisor",
        "/advisor/",
        "advisor_",
        "agent",
        "shadow_eval",
        "kolmemory",
        "kol_memory",
        "learning_signal",
    )
    if any(token in value for token in llm_tokens):
        return "p0_llm_agents_learning"

    business_tokens = (
        "shopify",
        "inventory",
        "attribution",
        "revenue",
        "gmv",
        "cost",
        "business_truth",
        "business_integration",
        "outcome",
        "financial",
    )
    if any(token in value for token in business_tokens):
        return "p2_business_integrations"

    kol_tokens = (
        "/kol/",
        "kol_",
        "kol-",
        "kolpool",
        "kol_pool",
        "mykol",
        "my_kol",
        "profile_",
        "profile-",
        "audience",
        "comments",
        "video_analysis",
        "video-url",
        "videourl",
        "recommendation",
    )
    if any(token in value for token in kol_tokens):
        return "p1_kol_performance"

    if value.startswith("docs/") or value.startswith('"docs/') or value.startswith("outputs/"):
        return "documentation_evidence"
    if value.startswith("tests/") or "/test" in value or name.endswith((".test.ts", ".test.tsx")):
        return "tests_other"
    if value.startswith("scripts/"):
        return "operations_other"
    if value.startswith(("backend/", "frontend/", "migrations/")):
        return "product_other"
    return "manual_review"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict[str, object]:
    rows = []
    for status, display_path in _status_entries(root):
        path = display_path.split(" -> ")[-1]
        absolute = root / path
        rows.append(
            {
                "status": status,
                "path": display_path,
                "category": _category(path),
                "tracked": status != "??",
                "size_bytes": absolute.stat().st_size if absolute.is_file() else None,
                "sha256": _sha256(absolute),
            }
        )
    counts = Counter(str(row["category"]) for row in rows)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(root),
        "branch": _git(root, "branch", "--show-current").decode().strip(),
        "head": _git(root, "rev-parse", "HEAD").decode().strip(),
        "safety": {
            "mutates_worktree": False,
            "stages_files": False,
            "commits_files": False,
            "deletes_files": False,
        },
        "summary": {
            "total": len(rows),
            "tracked": sum(1 for row in rows if row["tracked"]),
            "untracked": sum(1 for row in rows if not row["tracked"]),
            "categories": dict(sorted(counts.items())),
        },
        "entries": sorted(rows, key=lambda row: (str(row["category"]), str(row["path"]))),
    }


def write_report(manifest: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    entries = list(manifest["entries"])
    categories = dict(manifest["summary"]["categories"])
    for category in categories:
        paths = [str(row["path"]) for row in entries if row["category"] == category]
        (output_dir / f"{category}.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")

    lines = [
        "# V-KPI dirty worktree release-scope inventory",
        "",
        f"- Generated: `{manifest['generated_at']}`",
        f"- Branch: `{manifest['branch']}`",
        f"- HEAD: `{manifest['head']}`",
        f"- Total paths: **{manifest['summary']['total']}**",
        f"- Tracked changes: **{manifest['summary']['tracked']}**",
        f"- Untracked paths: **{manifest['summary']['untracked']}**",
        "- Safety: read-only classification; no stage, commit, delete, reset, checkout, or stash.",
        "",
        "## Review lanes",
        "",
        "| Lane | Paths |",
        "|---|---:|",
    ]
    lines.extend(f"| `{category}` | {count} |" for category, count in categories.items())
    lines.extend(
        [
            "",
            "## Release rule",
            "",
            "A lane is not automatically approved for release. Select a reviewed dependency closure,",
            "rerun its tests and runtime gates, then seal an immutable RC. Never deploy this entire",
            "dirty worktree by setting `ALLOW_DIRTY_DEPLOY=1`.",
            "",
        ]
    )
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(_git(args.repo.resolve(), "rev-parse", "--show-toplevel").decode().strip())
    manifest = build_manifest(root)
    write_report(manifest, args.output_dir.resolve())
    stdout_out(json.dumps(manifest["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
