#!/usr/bin/env python3
"""P4.4A media UX truth audit.

Read-only audit for Data Analysis media UX. It checks whether current frontend
and backend contracts expose real media, original links, single-post analysis,
and honest fallback states. It also samples local DB lineage counts without
calling external platforms or LLM providers.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "docs" / "audits"
REPORT_PATH = AUDIT_DIR / "2026-05-15-p4-4a-media-ux-truth-audit.md"
CSV_PATH = AUDIT_DIR / "p4_4a_media_ux_truth_matrix.csv"


@dataclass
class Check:
    area: str
    item: str
    status: str
    evidence: str
    risk: str
    next_action: str


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def has_all(path: str, needles: list[str]) -> tuple[bool, str]:
    text = read(path)
    missing = [needle for needle in needles if needle not in text]
    if missing:
        return False, "missing: " + ", ".join(missing[:6])
    return True, f"{len(needles)}/{len(needles)} markers present"


def get_conn():
    from app.db.connection import get_conn as _get_conn

    return _get_conn()


def scalar(conn, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)
    except Exception:
        return 0


def rows(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        result = conn.execute(sql, params).fetchall()
        return [dict(row) for row in result]
    except Exception:
        return []


def db_snapshot() -> dict[str, Any]:
    conn = get_conn()
    accounts_total = scalar(conn, "SELECT count(*) FROM vkpi_industry_accounts")
    posts_total = scalar(conn, "SELECT count(*) FROM vkpi_industry_posts")
    return {
        "accounts_total": accounts_total,
        "accounts_with_avatar": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_accounts WHERE COALESCE(avatar_url, '') <> ''",
        ),
        "accounts_with_profile_url": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_accounts WHERE COALESCE(profile_url, '') <> ''",
        ),
        "accounts_crawl_enabled": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_accounts WHERE crawl_enabled IS TRUE OR crawl_enabled = 1",
        ),
        "accounts_with_errors": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_accounts WHERE COALESCE(crawl_error_count, 0) > 0",
        ),
        "posts_total": posts_total,
        "posts_with_original_url": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_posts WHERE COALESCE(post_url, '') <> ''",
        ),
        "posts_with_thumbnail": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_posts WHERE COALESCE(thumbnail_url, '') <> ''",
        ),
        "posts_with_video": scalar(
            conn,
            "SELECT count(*) FROM vkpi_industry_posts WHERE COALESCE(video_url, '') <> ''",
        ),
        "posts_with_metrics": scalar(
            conn,
            """
            SELECT count(*) FROM vkpi_industry_posts
            WHERE COALESCE(views, 0) > 0 OR COALESCE(likes, 0) > 0 OR COALESCE(comments, 0) > 0
            """,
        ),
        "sync_status": rows(
            conn,
            """
            SELECT COALESCE(sync_status, '') AS sync_status, count(*) AS count
            FROM vkpi_industry_accounts
            GROUP BY COALESCE(sync_status, '')
            ORDER BY count DESC, sync_status
            """,
        ),
        "platform_posts": rows(
            conn,
            """
            SELECT COALESCE(platform, '') AS platform, count(*) AS count
            FROM vkpi_industry_posts
            GROUP BY COALESCE(platform, '')
            ORDER BY count DESC, platform
            """,
        ),
    }


def build_checks(snapshot: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []

    def add(area: str, item: str, ok: bool, evidence: str, risk: str, next_action: str) -> None:
        checks.append(Check(area, item, "PASS" if ok else "GAP", evidence, risk, next_action))

    ok, evidence = has_all(
        "frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts",
        [
            "RAW_JSON_KEYS",
            "findNestedString",
            "postThumbnailUrl",
            "postVideoUrls",
            "postPlatformUrl",
            "accountAvatarUrl",
            "accountProfileUrl",
        ],
    )
    add(
        "frontend",
        "media field extraction",
        ok,
        evidence,
        "raw payload media may not surface if extractor regresses",
        "Keep this as a contract smoke gate for crawler field changes.",
    )

    ok, evidence = has_all(
        "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx",
        [
            "postThumbnailUrl(post)",
            "playbackVideoCandidates(postVideoUrls(post))",
            "aria-label=\"打开原帖\"",
            "视频链接失效，打开原帖",
            "onOpenPost?.(post)",
            "disabled={!onOpenPost}",
        ],
    )
    add(
        "frontend",
        "post card real actions",
        ok,
        evidence,
        "cards may become visual-only or lose original-post fallback",
        "P4.4B should add browser spot checks for card actions, not more static checks.",
    )

    ok, evidence = has_all(
        "frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx",
        [
            "playbackVideoCandidates(postVideoUrls(post))",
            "打开原帖",
            "运行单帖分析",
            "真实 URL 分析处理中",
            "<summary>查看原始返回</summary>",
            "不会展示假分析",
        ],
    )
    add(
        "frontend",
        "single post drawer",
        ok,
        evidence,
        "single-post analysis can look stuck or fake if busy/result states regress",
        "P4.4B should live-click one low-risk post and record provider output.",
    )

    ok, evidence = has_all(
        "backend/app/api/routers/media.py",
        [
            "/api/admin/vkpi/media/image-proxy",
            "/api/admin/vkpi/media/video-proxy",
            "/api/admin/vkpi/media/video-redirect",
            "_allowed_external_image_url",
            "_allowed_external_video_url",
            "_safe_range_header",
            "get_current_user(request)",
        ],
    )
    add(
        "backend",
        "authenticated media proxy",
        ok,
        evidence,
        "open proxy or broken Range playback would affect media trust",
        "Keep host allowlist and auth checks; P4.4B should only test one known video URL.",
    )

    accounts_total = int(snapshot.get("accounts_total") or 0)
    posts_total = int(snapshot.get("posts_total") or 0)
    add(
        "data",
        "account avatar coverage",
        accounts_total > 0 and snapshot.get("accounts_with_avatar", 0) > 0,
        f"{snapshot.get('accounts_with_avatar', 0)}/{accounts_total} accounts have avatar_url",
        "blank avatars make selection hard even when data exists",
        "P4.4B should prioritize missing-avatar accounts only if they are active/synced.",
    )
    add(
        "data",
        "original post link coverage",
        posts_total > 0 and snapshot.get("posts_with_original_url", 0) == posts_total,
        f"{snapshot.get('posts_with_original_url', 0)}/{posts_total} posts have post_url",
        "users cannot verify or open platform source for posts missing URLs",
        "Backfill post_url during crawler normalization before adding more UI.",
    )
    add(
        "data",
        "thumbnail coverage",
        posts_total > 0 and snapshot.get("posts_with_thumbnail", 0) > 0,
        f"{snapshot.get('posts_with_thumbnail', 0)}/{posts_total} posts have thumbnail_url",
        "tables and cards become hard to choose from without visual preview",
        "P4.4B should render thumbnail fallback reason, not initials only.",
    )
    add(
        "data",
        "video coverage",
        posts_total > 0 and snapshot.get("posts_with_video", 0) > 0,
        f"{snapshot.get('posts_with_video', 0)}/{posts_total} posts have video_url",
        "in-app playback cannot be expected for non-video or missing-video rows",
        "Treat video playback as best-effort; original post remains required fallback.",
    )
    add(
        "data",
        "metric coverage",
        posts_total > 0 and snapshot.get("posts_with_metrics", 0) > 0,
        f"{snapshot.get('posts_with_metrics', 0)}/{posts_total} posts have non-zero metrics",
        "analytics looks empty if crawlers store posts without metrics",
        "P4.4B should compare displayed top cards against these stored counts.",
    )

    return checks


def markdown(snapshot: dict[str, Any], checks: list[Check]) -> str:
    now = datetime.now(timezone.utc).isoformat()
    pass_count = sum(1 for check in checks if check.status == "PASS")
    gap_count = len(checks) - pass_count
    sync_status = ", ".join(f"{row.get('sync_status') or '(blank)'}={row.get('count')}" for row in snapshot.get("sync_status", [])) or "-"
    platform_posts = ", ".join(f"{row.get('platform') or '(blank)'}={row.get('count')}" for row in snapshot.get("platform_posts", [])) or "-"
    lines = [
        "# P4.4A Media UX Truth Audit",
        "",
        f"Generated: `{now}`",
        "Scope: Data Analysis media read path, original-post links, media proxy contract, single-post analysis UI contract, and local DB media lineage counts.",
        "",
        "This is a read-only audit. It does not call external platforms, run LLM analysis, or change business data.",
        "",
        "## Result",
        "",
        f"- Checks: `{pass_count}/{len(checks)} PASS`, `{gap_count}` GAP",
        f"- Accounts: `{snapshot.get('accounts_total', 0)}` total, `{snapshot.get('accounts_with_avatar', 0)}` with avatar, `{snapshot.get('accounts_crawl_enabled', 0)}` crawl-enabled, `{snapshot.get('accounts_with_errors', 0)}` with crawl errors",
        f"- Posts: `{snapshot.get('posts_total', 0)}` total, `{snapshot.get('posts_with_original_url', 0)}` with original URL, `{snapshot.get('posts_with_thumbnail', 0)}` with thumbnail, `{snapshot.get('posts_with_video', 0)}` with video, `{snapshot.get('posts_with_metrics', 0)}` with metrics",
        f"- Account sync status: `{sync_status}`",
        f"- Post platform distribution: `{platform_posts}`",
        "",
        "## Matrix",
        "",
        "| Area | Item | Status | Evidence | Risk | Next Action |",
        "|---|---|---|---|---|---|",
    ]
    for check in checks:
        lines.append(
            "| "
            + " | ".join(
                str(value).replace("|", "\\|").replace("\n", " ")
                for value in [check.area, check.item, check.status, check.evidence, check.risk, check.next_action]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## P4.4B Recommended Fix/QA Scope",
            "",
            "1. Run one browser live path for a synced account: Content tab -> post card -> original post link -> single-post drawer.",
            "2. Run exactly one low-risk `运行单帖分析` call and record provider/status/latency; do not batch-call LLM.",
            "3. For active synced accounts with missing avatar or media, diagnose data source before changing UI.",
            "4. Keep video playback best-effort: proxy first, then explicit original-post fallback; do not imply all platform videos are locally playable.",
            "5. Any unsupported action must be disabled with a reason tooltip; no decorative controls.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_csv(checks: list[Check]) -> None:
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["area", "item", "status", "evidence", "risk", "next_action"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.__dict__)


def main() -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = db_snapshot()
    checks = build_checks(snapshot)
    write_csv(checks)
    REPORT_PATH.write_text(markdown(snapshot, checks), encoding="utf-8")
    pass_count = sum(1 for check in checks if check.status == "PASS")
    stdout_out(json.dumps({
        "ok": pass_count == len(checks),
        "marker": "VKPI_P4_4A_MEDIA_UX_TRUTH_AUDIT_OK",
        "checks": len(checks),
        "pass": pass_count,
        "gap": len(checks) - pass_count,
        "report": str(REPORT_PATH),
        "csv": str(CSV_PATH),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
