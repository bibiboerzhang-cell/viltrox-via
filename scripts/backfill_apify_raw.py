#!/usr/bin/env python3
"""B1 解析债回填:从已落库 Apify raw 幂等回填 邮箱/评论者ID/语言/媒体种类(零新抓、零网络、零 LLM)。

真表名侦察结论(本地库实测):
  - KOL 档案 raw     : vkpi_kol_pool.raw_platform_data(TT authorMeta / IG biography / YT snippet)
  - 评论 raw         : vkpi_comments.raw_data_json(IG ownerUsername+owner.id / FB profileId+profileName / TT 扁平 uid)
  - 证据表           : vkpi_kol_video_evidence 无 raw 列;media_kind 由 evidence_type+image_urls+
                       所属 KOL 的 pool raw videos 匹配判定(与 0703 提列批同口径)。

五个阶段(--phase 可单跑):
  emails           : 走既有合规管线 business_contact_extract.enrich_contacts_l0
                     (落 vkpi_kol_pool_contacts 审计表 + 仅当主表 email 为空才回填;
                      读端脱敏口径不变,不绕过、不新开口径)。
  commenters       : vkpi_comments.author_id / author_handle(IG owner.id+ownerUsername、
                     FB profileId+profileName、TT 扁平 uid+uniqueId)。
  languages        : vkpi_comments.language_detected <- langdetect(comment_text)
                     (method 诚实入统计:langdetect 装不上时降级轻量词表法 wordlist_v0)。
  media            : vkpi_kol_video_evidence.media_kind(video / image / carousel)。
  contact_snapshot : 对「有联系信号且 214 四列仍为空」的 KOL 调
                     contact_system.refresh_contactability,写 contact_channels /
                     contact_sources / contactability_score / contact_last_verified_at。

规矩:
  - 只补空、不覆写(UPDATE 的 WHERE 再守一遍仍为空),天然幂等,可重复跑。
  - 分批 commit(每 200 条),--limit 限每阶段扫描行数,--dry-run 只统计不落库。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号。
  - 红线:绝不写 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定;
    邮箱沿用既有合规管线(置信度分级 + 审计留痕 + 读端脱敏),明文不出既有门控。

用法:
  .venv/bin/python scripts/backfill_apify_raw.py --dry-run              # 全阶段只统计
  .venv/bin/python scripts/backfill_apify_raw.py                        # 全阶段落库
  .venv/bin/python scripts/backfill_apify_raw.py --phase languages      # 单阶段
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/backfill_raw_extraction_0703.py)。
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
from app.domains.kol.pool_common import _table_columns  # noqa: E402
from app.domains.kol.video_evidence import _video_identity  # noqa: E402

BATCH_COMMIT_EVERY = 200


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


def _first(raw: dict, paths: list[str]) -> str:
    for path in paths:
        value = _get_path(raw, path)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


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
    # 复核口径:回填后主表 email 覆盖率(审计基准约 38 上下,单位是每百行)。
    total_raw = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool"
        " WHERE raw_platform_data IS NOT NULL AND raw_platform_data <> '' AND raw_platform_data <> '{}'"
    ).fetchone())["n"] or 0)
    with_email = int(dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE COALESCE(email,'') <> ''"
    ).fetchone())["n"] or 0)
    return {
        "scanned": scanned,
        "processed": processed,
        "with_contacts": with_contacts,
        "email_backfill": email_backfill,
        "failed": failed,
        "pool_rows_with_raw": total_raw,
        "pool_rows_with_email_now": with_email,
        "email_fill_rate_per_100_raw": round(with_email * 100.0 / total_raw, 1) if total_raw else 0.0,
    }


# ── 阶段 2:评论者 ID / handle ───────────────────────────────────────


def extract_commenter(platform: str, raw: dict) -> tuple[str, str]:
    """返回 (author_id, author_handle);抽不到的位置给空串。
    口径:IG=owner.id+ownerUsername;FB=profileId+profileName(URL 尾段兜底);
    TT=扁平 uid+uniqueId(兼容嵌套 author/user);其它平台只认扁平信号,避免误写。"""
    platform = (platform or "").strip().lower()
    if platform == "instagram":
        author_id = _first(raw, ["owner.id", "owner.pk", "ownerId"])
        handle = _first(raw, ["ownerUsername", "owner.username"])
        return author_id, handle
    if platform == "facebook":
        author_id = _first(raw, ["profileId", "from.id"])
        handle = _first(raw, ["profileName", "from.name"]) or _fb_url_tail(raw)
        return author_id, handle
    if platform == "tiktok":
        author_id = _first(raw, ["uid", "author.id", "user.id"])
        handle = _first(raw, ["uniqueId", "author.uniqueId", "user.uniqueId"])
        return author_id, handle
    if platform == "youtube":
        author_id = _first(raw, [
            "snippet.topLevelComment.snippet.authorChannelId.value",
            "snippet.authorChannelId.value",
        ])
        handle = _first(raw, [
            "snippet.topLevelComment.snippet.authorDisplayName",
            "snippet.authorDisplayName",
        ])
        return author_id, handle
    author_id = _first(raw, ["uid", "profileId"])
    handle = _first(raw, ["uniqueId", "profileName"])
    return author_id, handle


def _fb_url_tail(raw: dict) -> str:
    """profileUrl 尾段兜底(去 query;profile.php 不是 handle)。"""
    profile_url = _first(raw, ["profileUrl"])
    tail = profile_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].strip()
    if tail and tail.lower() != "profile.php":
        return tail
    return ""


def phase_commenters(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, platform, author_id, author_handle, raw_data_json FROM vkpi_comments"
        " WHERE raw_data_json IS NOT NULL"
        " AND ((author_id IS NULL OR TRIM(author_id) = '') OR (author_handle IS NULL OR TRIM(author_handle) = ''))"
        " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    parse_failed = no_signal = id_filled = handle_filled = 0
    by_platform: Counter[str] = Counter()
    pending = 0
    for row in rows:
        item = dict(row)
        raw = _loads(item.get("raw_data_json"))
        if not isinstance(raw, dict):
            parse_failed += 1
            continue
        platform = str(item.get("platform") or "")
        author_id, handle = extract_commenter(platform, raw)
        id_missing = not str(item.get("author_id") or "").strip()
        handle_missing = not str(item.get("author_handle") or "").strip()
        write_id = author_id if (id_missing and author_id) else ""
        write_handle = handle if (handle_missing and handle) else ""
        if not write_id and not write_handle:
            no_signal += 1
            continue
        if not dry_run:
            if write_id:
                result = conn.execute(
                    "UPDATE vkpi_comments SET author_id = ?"
                    " WHERE id = ? AND (author_id IS NULL OR TRIM(author_id) = '')",
                    (write_id[:200], int(item["id"])),
                )
                if not int(getattr(result, "rowcount", 0) or 0):
                    write_id = ""  # 并发下别人先补了:守住只补空
            if write_handle:
                result = conn.execute(
                    "UPDATE vkpi_comments SET author_handle = ?"
                    " WHERE id = ? AND (author_handle IS NULL OR TRIM(author_handle) = '')",
                    (write_handle[:200], int(item["id"])),
                )
                if not int(getattr(result, "rowcount", 0) or 0):
                    write_handle = ""
            pending += 1
            if pending >= BATCH_COMMIT_EVERY:
                conn.commit()
                pending = 0
        if write_id:
            id_filled += 1
        if write_handle:
            handle_filled += 1
        if write_id or write_handle:
            by_platform[platform or "unknown"] += 1
    if not dry_run and pending:
        conn.commit()
    return {
        "scanned_missing_id_or_handle": scanned,
        "parse_failed": parse_failed,
        "no_signal": no_signal,
        "author_id_filled": id_filled,
        "author_handle_filled": handle_filled,
        "by_platform": dict(by_platform),
    }


# ── 阶段 3:评论语言(langdetect,装不上降级词表法) ─────────────────

_URL_RE = re.compile(r"https?://\S+")
_MENTION_RE = re.compile(r"@\w+")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# 轻量词表兜底(仅 langdetect 缺席时启用;宁缺勿滥,只收高频功能词)。
_WORDLIST = {
    "en": {"the", "and", "this", "that", "with", "for", "you", "your", "is", "are", "love", "great", "nice"},
    "es": {"que", "los", "las", "por", "para", "gracias", "muy", "este", "esta", "como", "pero"},
    "fr": {"les", "des", "est", "pour", "avec", "merci", "tres", "cette", "mais", "vous"},
    "de": {"und", "der", "die", "das", "ist", "nicht", "mit", "sehr", "danke", "aber"},
    "pt": {"que", "com", "para", "muito", "obrigado", "isso", "mas", "voce", "esta"},
    "it": {"che", "per", "con", "questo", "molto", "grazie", "sono", "anche", "come"},
}


def _wordlist_detect(text: str) -> str:
    tokens = {t for t in re.split(r"\W+", text.lower()) if t}
    hits = {lang: len(tokens & words) for lang, words in _WORDLIST.items()}
    best = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    if best and best[0][1] >= 2 and (len(best) < 2 or best[0][1] > best[1][1]):
        return best[0][0]
    return ""


def _make_language_detector() -> tuple[Any, str]:
    """优先 langdetect(seed=0 保证确定性);缺席降级词表法并诚实标 method。"""
    try:
        from langdetect import DetectorFactory, detect_langs  # type: ignore
        from langdetect.lang_detect_exception import LangDetectException  # type: ignore

        DetectorFactory.seed = 0

        def _detect(text: str) -> str:
            try:
                candidates = detect_langs(text)
            except LangDetectException:
                return ""
            if not candidates:
                return ""
            top = candidates[0]
            if float(top.prob) < 0.80:
                return ""
            return str(top.lang).strip().lower()

        try:
            import langdetect as _ld  # type: ignore
            version = getattr(_ld, "__version__", "") or "1.x"
        except Exception:
            version = "1.x"
        return _detect, f"langdetect_{version}_seed0_minprob80"
    except Exception:
        return _wordlist_detect, "wordlist_v0_fallback"


def _clean_comment_text(text: str) -> str:
    text = _URL_RE.sub(" ", text or "")
    text = _MENTION_RE.sub(" ", text)
    return text.strip()


def phase_languages(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    detect, method = _make_language_detector()
    rows = conn.execute(
        "SELECT id, platform, comment_text FROM vkpi_comments"
        " WHERE (language_detected IS NULL OR TRIM(language_detected) = '')"
        " AND comment_text IS NOT NULL AND TRIM(comment_text) <> ''"
        " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    too_short = no_signal = filled = 0
    by_lang: Counter[str] = Counter()
    pending = 0
    for row in rows:
        item = dict(row)
        text = _clean_comment_text(str(item.get("comment_text") or ""))
        if len(_LETTER_RE.findall(text)) < 3:
            too_short += 1  # 纯 emoji/太短:语言不可判,诚实跳过
            continue
        lang = detect(text)
        if not lang:
            no_signal += 1
            continue
        lang = lang[:20]
        if not dry_run:
            result = conn.execute(
                "UPDATE vkpi_comments SET language_detected = ?"
                " WHERE id = ? AND (language_detected IS NULL OR TRIM(language_detected) = '')",
                (lang, int(item["id"])),
            )
            if not int(getattr(result, "rowcount", 0) or 0):
                continue
            pending += 1
            if pending >= BATCH_COMMIT_EVERY:
                conn.commit()
                pending = 0
        filled += 1
        by_lang[lang] += 1
    if not dry_run and pending:
        conn.commit()
    return {
        "method": method,
        "scanned_missing_language": scanned,
        "too_short_or_emoji_only": too_short,
        "no_signal": no_signal,
        "filled": filled,
        "top_languages": dict(by_lang.most_common(12)),
    }


# ── 阶段 4:证据 media_kind(与 0703 提列批同口径) ──────────────────


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
    if not have_col:
        return {"skipped": "column_missing_run_migration_208"}
    rows = conn.execute(
        "SELECT id, kol_pool_id, platform, content_url, evidence_type, image_urls"
        " FROM vkpi_kol_video_evidence"
        " WHERE (media_kind IS NULL OR TRIM(media_kind) = '')"
        " ORDER BY id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    filled = no_signal = 0
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
        # media_article 等非视频/图片证据:无 media_kind 语义,诚实跳过。
        if not kind:
            no_signal += 1
            continue
        by_kind[kind] += 1
        if not dry_run:
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
        "scanned_missing_media_kind": scanned,
        "no_signal": no_signal,
        "filled": filled,
        "by_kind": dict(by_kind),
    }


# ── 阶段 5:联系渠道快照 + 可联系性分(214 四列,只填空) ────────────


def phase_contact_snapshot(conn: Any, *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    from app.domains.kol import contact_system

    pool_cols = _table_columns(conn, "vkpi_kol_pool")
    missing = [c for c in contact_system.REFRESH_COLUMNS if c not in pool_cols]
    if missing:
        return {"skipped": "columns_missing_run_migration_214", "missing_columns": missing}
    # 只挑「有联系信号且四列仍为空」的行:只填空、绝不覆盖已有快照/分数。
    rows = conn.execute(
        "SELECT p.id FROM vkpi_kol_pool p"
        " WHERE p.contact_channels IS NULL AND p.contactability_score IS NULL"
        " AND (COALESCE(p.email,'') <> ''"
        "      OR EXISTS (SELECT 1 FROM vkpi_kol_pool_contacts c WHERE c.kol_pool_id = p.id))"
        " ORDER BY p.id" + _limit_sql(limit)
    ).fetchall()
    scanned = len(rows)
    refreshed = failed = 0
    score_buckets: Counter[str] = Counter()
    score_sum = 0.0
    for row in rows:
        kid = int(dict(row)["id"])
        try:
            if dry_run:
                res = contact_system.contactability(kid, conn=conn)
            else:
                res = contact_system.refresh_contactability(kid, conn=conn)
        except Exception:
            failed += 1
            continue
        refreshed += 1
        score = float(res.get("score") or 0.0)
        score_sum += score
        if score <= 0:
            score_buckets["0"] += 1
        elif score < 40:
            score_buckets["1_39"] += 1
        elif score < 70:
            score_buckets["40_69"] += 1
        else:
            score_buckets["70_100"] += 1
    return {
        "scanned_with_signal_and_empty_columns": scanned,
        "refreshed": refreshed,
        "failed": failed,
        "avg_score": round(score_sum / refreshed, 1) if refreshed else 0.0,
        "score_buckets": dict(score_buckets),
    }


PHASES = {
    "emails": phase_emails,
    "commenters": phase_commenters,
    "languages": phase_languages,
    "media": phase_media,
    "contact_snapshot": phase_contact_snapshot,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只统计命中数,不落库")
    parser.add_argument("--limit", type=int, default=0, help="每阶段最多扫描行数(0=不限)")
    parser.add_argument(
        "--phase",
        choices=[*PHASES, "all"],
        default="all",
        help="只跑单个阶段(emails / commenters / languages / media / contact_snapshot),默认全跑",
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
