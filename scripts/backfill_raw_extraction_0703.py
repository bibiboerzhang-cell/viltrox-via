#!/usr/bin/env python3
"""C6 零新抓提列批:从已落库 raw 幂等回填 4 类资产列(零 Apify 消耗、零网络请求)。

盘点依据 docs/vkpi_Apify资产盘点_2026-07-02.md,四个阶段(--phase 可单跑):
  emails   : vkpi_kol_pool.raw_platform_data(TT authorMeta.signature / IG biography 等)
             -> 走既有合规管线 business_contact_extract.enrich_contacts_l0
                (落 vkpi_kol_pool_contacts 审计表 + 仅当主表 email 为空才回填;
                 读端脱敏 _mask_email 口径不变,不绕过)。
  commerce : vkpi_kol_pool.raw_platform_data 的 authorMeta.verified / ttSeller /
             commerceUserInfo.commerceUser(K8 口径)-> is_verified / is_tt_seller /
             is_commerce_user 三列(migration 208)。
  avatars  : vkpi_comments.raw_data_json 的评论者头像 URL(IG ownerProfilePicUrl /
             TT avatarThumbnail / YT authorProfileImageUrl / FB profilePicture)
             -> author_avatar_url 列(只存 URL,不下载文件)。
  media    : vkpi_kol_video_evidence.media_kind(video / image / carousel)。evidence 表
             无 raw 列,从 evidence_type + image_urls + 所属 KOL 的 pool raw videos 匹配判定。

规矩:
  - 只补空、不覆写(UPDATE 的 WHERE 再守一遍仍为空),天然幂等,可重复跑。
  - 分批 commit(默认每 200 条),--limit 限制每阶段扫描行数,--dry-run 只统计不落库。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号。
  - 红线:绝不写 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定;
    邮箱沿用既有合规管线(白名单来源 + 审计留痕 + 读端脱敏),不新开口径。

用法:
  .venv/bin/python scripts/backfill_raw_extraction_0703.py --dry-run             # 全阶段只统计
  .venv/bin/python scripts/backfill_raw_extraction_0703.py --phase commerce      # 单阶段落库
  .venv/bin/python scripts/backfill_raw_extraction_0703.py --limit 500           # 每阶段最多 500 行
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/backfill_comment_author_ids.py)。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.kol.business_contact_extract import enrich_contacts_l0  # noqa: E402
from app.domains.kol.pool_common import _commerce_flags, _table_columns  # noqa: E402
from app.domains.kol.video_evidence import _video_identity  # noqa: E402

BATCH_COMMIT_EVERY = 200

COMMERCE_COLUMNS = ("is_verified", "is_tt_seller", "is_commerce_user")


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return None


def _get_path(data: Any, path: str) -> Any:
    """点号路径取嵌套字段;任何一级缺失返回 None。"""
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _limit_sql(limit: int | None) -> str:
    return f" LIMIT {int(limit)}" if limit else ""


# ── 阶段 1:邮箱(走既有合规管线,绝不绕过) ──────────────────────────


def phase_emails(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id FROM vkpi_kol_pool"
        " WHERE raw_platform_data IS NOT NULL AND raw_platform_data <> '' AND raw_platform_data <> '{}'"
        " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    processed = with_contacts = email_backfill = failed = 0
    for row in rows:
        kid = int(dict(row)["id"])
        try:
            # 合规管线单点:置信度分级 + vkpi_kol_pool_contacts 审计留痕 + 仅空值回填 email。
            res = enrich_contacts_l0(kid, conn=conn, dry_run=dry_run)
        except Exception:
            failed += 1
            continue
        processed += 1
        if res.get("found"):
            with_contacts += 1
        if dry_run:
            if res.get("email_would_backfill"):
                email_backfill += 1
        elif res.get("status") == "ok" and res.get("email"):
            email_backfill += 1
    return {
        "scanned": scanned,
        "processed": processed,
        "with_contacts": with_contacts,
        "email_backfill": email_backfill,
        "failed": failed,
    }


# ── 阶段 2:K8 商单/认证标记 ─────────────────────────────────────────


def phase_commerce(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    pool_cols = _table_columns(conn, "vkpi_kol_pool")
    have_cols = all(col in pool_cols for col in COMMERCE_COLUMNS)
    if not have_cols and not dry_run:
        return {"skipped": "columns_missing_run_migration_208"}
    select_flags = ", ".join(COMMERCE_COLUMNS) + ", " if have_cols else ""
    where_missing = (
        " AND (is_verified IS NULL OR is_tt_seller IS NULL OR is_commerce_user IS NULL)" if have_cols else ""
    )
    rows = conn.execute(
        f"SELECT id, platform, {select_flags}raw_platform_data FROM vkpi_kol_pool"
        " WHERE raw_platform_data IS NOT NULL AND raw_platform_data <> '' AND raw_platform_data <> '{}'"
        + where_missing
        + " ORDER BY id"
        + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    parse_failed = no_signal = updated = 0
    filled: Counter[str] = Counter()
    pending = 0
    for row in rows:
        item = dict(row)
        raw = _loads(item.get("raw_platform_data"))
        if not isinstance(raw, dict) or not raw:
            parse_failed += 1
            continue
        flags = _commerce_flags(raw)
        # 只补空:已有值(含 FALSE,compat 读回可能是 0)一律不动。
        writable = {
            key: value
            for key, value in flags.items()
            if value is not None and (not have_cols or item.get(key) is None)
        }
        if not writable:
            no_signal += 1
            continue
        for key in writable:
            filled[key] += 1
        updated += 1
        if dry_run or not have_cols:
            continue
        # COALESCE 再守一遍只补空,幂等。
        assignments = ", ".join(f"{key}=COALESCE({key}, ?)" for key in writable)
        conn.execute(
            f"UPDATE vkpi_kol_pool SET {assignments} WHERE id=?",  # noqa: S608 — 列名来自固定白名单
            (*writable.values(), int(item["id"])),
        )
        pending += 1
        if pending >= BATCH_COMMIT_EVERY:
            conn.commit()
            pending = 0
    if not dry_run and pending:
        conn.commit()
    return {
        "scanned": scanned,
        "parse_failed": parse_failed,
        "no_signal": no_signal,
        "rows_with_signal": updated,
        "filled_by_column": dict(filled),
        "columns_ready": have_cols,
    }


# ── 阶段 3:评论者头像 URL ───────────────────────────────────────────

# 与 collector._standardize_comment 的 author_avatar_url 映射保持同一口径。
AVATAR_PATHS = {
    "youtube": ["snippet.topLevelComment.snippet.authorProfileImageUrl", "snippet.authorProfileImageUrl"],
    "instagram": ["ownerProfilePicUrl", "owner.profile_pic_url", "owner.profilePicUrl"],
    "tiktok": ["avatarThumbnail", "author.avatarThumb", "user.avatarThumb", "author.avatarThumbnail"],
    "facebook": ["profilePicture", "from.picture.data.url"],
    "x": ["author.profilePicture", "user.profile_image_url"],
}


def extract_avatar(platform: str, raw: dict) -> str:
    for path in AVATAR_PATHS.get((platform or "").strip().lower(), []):
        value = _get_path(raw, path)
        if isinstance(value, dict):
            url_list = value.get("url_list")
            value = url_list[0] if isinstance(url_list, list) and url_list else ""
        text = str(value or "").strip()
        if text.lower().startswith("http"):
            return text[:500]
    return ""


def phase_avatars(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    comment_cols = _table_columns(conn, "vkpi_comments")
    have_col = "author_avatar_url" in comment_cols
    if not have_col and not dry_run:
        return {"skipped": "column_missing_run_migration_208"}
    where_missing = " AND (author_avatar_url IS NULL OR TRIM(author_avatar_url) = '')" if have_col else ""
    rows = conn.execute(
        "SELECT id, platform, raw_data_json FROM vkpi_comments"
        " WHERE raw_data_json IS NOT NULL" + where_missing + " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    parse_failed = no_signal = filled = 0
    by_platform: Counter[str] = Counter()
    pending = 0
    for row in rows:
        item = dict(row)
        raw = _loads(item.get("raw_data_json"))
        if not isinstance(raw, dict):
            parse_failed += 1
            continue
        avatar = extract_avatar(str(item.get("platform") or ""), raw)
        if not avatar:
            no_signal += 1
            continue
        if not dry_run and have_col:
            result = conn.execute(
                "UPDATE vkpi_comments SET author_avatar_url = ?"
                " WHERE id = ? AND (author_avatar_url IS NULL OR TRIM(author_avatar_url) = '')",
                (avatar, int(item["id"])),
            )
            if not int(getattr(result, "rowcount", 0) or 0):
                continue  # 并发下别人先补了:守住只补空
            pending += 1
            if pending >= BATCH_COMMIT_EVERY:
                conn.commit()
                pending = 0
        filled += 1
        by_platform[str(item.get("platform") or "unknown")] += 1
    if not dry_run and pending:
        conn.commit()
    return {
        "scanned": scanned,
        "parse_failed": parse_failed,
        "no_signal": no_signal,
        "filled": filled,
        "by_platform": dict(by_platform),
        "column_ready": have_col,
    }


# ── 阶段 4:证据 media_kind ─────────────────────────────────────────


def _ig_item_media_kind(item: dict[str, Any]) -> str:
    """与 workflow_evidence_video_metadata 的 IG 分流口径一致:有视频=video,
    否则多子项/Sidecar=carousel,再否则 image。"""
    item_type = str(item.get("type") or "").strip().lower()
    children = item.get("childPosts") if isinstance(item.get("childPosts"), list) else []
    has_video = (
        item_type == "video"
        or bool(str(item.get("videoUrl") or item.get("videoUrlNoWaterMark") or "").strip())
        or any(
            (str((cp or {}).get("type") or "").lower() == "video" or (cp or {}).get("videoUrl"))
            for cp in children
        )
    )
    if has_video:
        return "video"
    if item_type == "sidecar" or len(children) > 1:
        return "carousel"
    return "image"


def _pool_raw_video_kinds(conn: Any, kol_pool_id: int, cache: dict[int, dict]) -> dict:
    """按 KOL 缓存:pool raw videos 里每条内容 URL 身份 -> media_kind。"""
    if kol_pool_id in cache:
        return cache[kol_pool_id]
    kinds: dict[Any, str] = {}
    row = conn.execute(
        "SELECT raw_platform_data FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    raw = _loads(dict(row).get("raw_platform_data")) if row else None
    videos = raw.get("videos") if isinstance(raw, dict) else None
    for item in videos if isinstance(videos, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("webVideoUrl") or item.get("url") or item.get("postUrl") or "").strip()
        identity = _video_identity(url) if url else None
        if identity:
            kinds[identity] = _ig_item_media_kind(item)
    cache[kol_pool_id] = kinds
    return kinds


def phase_media(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    evidence_cols = _table_columns(conn, "vkpi_kol_video_evidence")
    have_col = "media_kind" in evidence_cols
    if not have_col and not dry_run:
        return {"skipped": "column_missing_run_migration_208"}
    where_missing = " WHERE (media_kind IS NULL OR TRIM(media_kind) = '')" if have_col else ""
    rows = conn.execute(
        "SELECT id, kol_pool_id, platform, content_url, evidence_type, image_urls"
        " FROM vkpi_kol_video_evidence" + where_missing + " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    filled = 0
    by_kind: Counter[str] = Counter()
    pool_cache: dict[int, dict] = {}
    pending = 0
    for row in rows:
        item = dict(row)
        platform = str(item.get("platform") or "").strip().lower()
        evidence_type = str(item.get("evidence_type") or "").strip().lower()
        image_urls = _loads(item.get("image_urls"))
        kind = ""
        # 1) 已分流成 image 的证据:多图=carousel,单图=image。
        if evidence_type == "image":
            kind = "carousel" if isinstance(image_urls, list) and len(image_urls) > 1 else "image"
        # 2) IG 且 pool raw 里能按 URL 身份匹配到原始条目:按 raw 判定(最准)。
        elif platform == "instagram":
            identity = _video_identity(str(item.get("content_url") or ""))
            if identity and item.get("kol_pool_id"):
                kind = _pool_raw_video_kinds(conn, int(item["kol_pool_id"]), pool_cache).get(identity, "")
            if not kind and evidence_type == "video":
                kind = "video"
        # 3) YT/TT/FB 证据均产自视频抓取链路,evidence_type=video 默认成立。
        elif evidence_type == "video" or platform in {"youtube", "tiktok", "facebook"}:
            kind = "video"
        if not kind:
            continue
        by_kind[kind] += 1
        if not dry_run and have_col:
            result = conn.execute(
                "UPDATE vkpi_kol_video_evidence SET media_kind = ?"
                " WHERE id = ? AND (media_kind IS NULL OR TRIM(media_kind) = '')",
                (kind, int(item["id"])),
            )
            if not int(getattr(result, "rowcount", 0) or 0):
                continue
            pending += 1
            if pending >= BATCH_COMMIT_EVERY:
                conn.commit()
                pending = 0
        filled += 1
    if not dry_run and pending:
        conn.commit()
    return {
        "scanned": scanned,
        "filled": filled,
        "by_kind": dict(by_kind),
        "column_ready": have_col,
    }


PHASES = {
    "emails": phase_emails,
    "commerce": phase_commerce,
    "avatars": phase_avatars,
    "media": phase_media,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只统计命中数,不落库")
    parser.add_argument("--limit", type=int, default=0, help="每阶段最多扫描行数(0=不限)")
    parser.add_argument(
        "--phase",
        choices=[*PHASES, "all"],
        default="all",
        help="只跑单个阶段(emails / commerce / avatars / media),默认全跑",
    )
    args = parser.parse_args()

    conn = get_conn()
    limit = int(args.limit) if args.limit and args.limit > 0 else None
    report: dict[str, Any] = {"dry_run": bool(args.dry_run), "limit": limit}
    for name, fn in PHASES.items():
        if args.phase not in ("all", name):
            continue
        try:
            report[name] = fn(conn, dry_run=bool(args.dry_run), limit=limit)
        except Exception as exc:  # 单阶段失败不拖垮其它阶段
            try:
                conn.rollback()
            except Exception:
                pass
            report[name] = {"error": str(exc)[:500]}
    stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
