"""Safe KOL Pool profile-basics writer.

This service is the reusable write boundary for profile-level backfills. It is
deliberately limited to basic profile fields and verifies that V6 Fit fields do
not change.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.pool_common import _garbage_handle_rule, _table_columns

logger = get_logger("viltrox.domains.kol.profile_basics")

PROFILE_BASICS_METHOD = "kol_profile_basics_safe_writer_v1"
PROFILE_BASICS_WHITELIST = {
    "avatar_url",
    "followers",
    "bio",
    "profile_url",
    "platform",
    "handle",
    # 线上修(2026-07-10):发现自动入库的行 display_name 全空(列表只剩 handle,
    # YT 时是一串 UC 频道 ID,用户误以为「没入库」)。白名单放行;只进 INSERT
    # 字段、不进 UPDATE——已有行的名字绝不被搜索快照覆盖。评分域照旧零接触。
    "display_name",
    "posts_count",
    "last_video_at",
    "raw_platform_data",
    "profile_backfilled_at",
}
PROFILE_BASICS_UPDATE_FIELDS = (
    "avatar_url",
    "followers",
    "bio",
    "profile_url",
    "platform",
    "handle",
    "posts_count",
    "last_video_at",
    "raw_platform_data",
    "profile_backfilled_at",
)
PROFILE_BASICS_INSERT_FIELDS = (
    "pool_uid",
    "platform",
    "handle",
    "display_name",
    "profile_url",
    "avatar_url",
    "bio",
    "followers",
    "posts_count",
    "last_video_at",
    "raw_platform_data",
    "profile_backfilled_at",
)
SCORE_FIELDS = ("viltrox_fit_score", "viltrox_fit_reason")


def write_kol_profile_basics(
    kol_pool_id: int | None,
    profile_data: dict[str, Any],
    *,
    dry_run: bool = True,
    conn: Any | None = None,
    method: str = PROFILE_BASICS_METHOD,
    commit_write: bool = True,
) -> dict[str, Any]:
    """Insert/update profile basics without touching V6 Fit fields.

    ``dry_run`` defaults to True so callers must explicitly opt into a write.
    When writing, this function checks score fields before/after and rolls back
    if any existing score changes or a newly inserted row receives a score.
    """
    if not isinstance(profile_data, dict):
        raise ValueError("profile_data must be a dict")

    db = conn or get_conn()
    columns = _table_columns(db, "vkpi_kol_pool")
    if not columns:
        raise RuntimeError("vkpi_kol_pool schema unavailable")

    now = _utcnow()
    row = _load_pool_row(db, kol_pool_id) if kol_pool_id else None
    operation = "update" if row else "insert"
    normalized = _normalise_profile_data(profile_data, existing=row, now=now, method=method)
    ignored_fields = sorted(set(profile_data) - PROFILE_BASICS_WHITELIST)

    if operation == "insert":
        if not normalized.get("platform") or not normalized.get("handle"):
            raise ValueError("platform and handle are required for new KOL profile basics")
        normalized.setdefault("pool_uid", f"url-profile-{secrets.token_hex(8)}")

    write_fields = PROFILE_BASICS_UPDATE_FIELDS if operation == "update" else PROFILE_BASICS_INSERT_FIELDS
    allowed_fields = [field for field in write_fields if field in columns]
    missing_columns = sorted(set(write_fields) - set(allowed_fields) - {"pool_uid"})
    planned_values = {
        field: normalized[field]
        for field in allowed_fields
        if field in normalized and _should_write(field, normalized[field], operation=operation)
    }
    if operation == "insert" and "pool_uid" in columns:
        planned_values["pool_uid"] = normalized["pool_uid"]

    before_scores = _score_snapshot(db, [int(kol_pool_id)]) if row else {}
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "operation": operation,
            "kol_pool_id": int(kol_pool_id) if row else None,
            "fields_to_write": sorted(planned_values),
            "planned_values": planned_values,
            "ignored_fields": ignored_fields,
            "missing_columns": missing_columns,
            "score_before": before_scores,
            "score_after": before_scores,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
            "matched_existing": bool(row),
        }

    changed_ids: list[int] = []
    matched_existing = bool(row)
    try:
        if operation == "update":
            if not planned_values:
                after_scores = before_scores
                target_id = int(kol_pool_id or 0)
            else:
                target_id = int(kol_pool_id or 0)
                _execute_update(db, target_id, planned_values)
                after_scores = _score_snapshot(db, [target_id])
                changed_ids = _changed_score_ids(before_scores, after_scores)
        else:
            # P0-4 修复 score 守卫误杀:ON CONFLICT DO UPDATE 可能落到已 enrich 的既有
            # (platform,handle) 行,其 viltrox_fit_score 非空。旧码 _new_row_has_score 会把
            # 『撞已评分行』误判为『新行被打分』→误回滚『二次贴 URL 刷新已评分 KOL』正常场景。
            # 改法:INSERT 前按 (platform,handle) 快照既有 score;若冲突落到同一既有 id,
            # 用 before==after 比对(仅 score 真变才回滚);若是真新行,保留旧守卫。
            pre_id = _preexisting_pool_id(db, planned_values.get("platform"), planned_values.get("handle"))
            matched_existing = pre_id is not None
            insert_before_scores = _score_snapshot(db, [pre_id]) if pre_id else {}
            target_id = _execute_insert(db, planned_values)
            after_scores = _score_snapshot(db, [target_id])
            if pre_id is not None and int(pre_id) == int(target_id):
                changed_ids = _changed_score_ids(insert_before_scores, after_scores)
            elif _new_row_has_score(after_scores.get(target_id, {})):
                changed_ids = [target_id]

        if changed_ids:
            _rollback(db)
            raise RuntimeError(f"viltrox_fit_score changed unexpectedly: {changed_ids}")

        if commit_write:
            _commit(db)
        # 第二道闸(2026-07-12 两粉号案):本次写入含 followers(深爬回填/发现入库都走此口)
        # → 立即重过触达门槛,命中打 low_reach 标(只写 raw_platform_data;推荐面据此不展示)。
        # best-effort 绝不阻断写主流程;懒 import 防循环;零触 viltrox_fit_score。
        if commit_write and "followers" in planned_values and target_id:
            try:
                from app.domains.kol.reach_floor_regate import reapply_reach_floor

                reapply_reach_floor(int(target_id), conn=db)
            except Exception:
                logger.warning("reach floor regate skipped kol=%s", target_id, exc_info=True)
        # Both URL materialization and deep-crawl writes converge here.  Queue
        # the persisted profile for a later provider-free L0 pass only; never
        # extract, crawl or send inside the profile write transaction.
        if target_id:
            try:
                from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisition

                enqueue_contact_acquisition(
                    int(target_id),
                    trigger_source="profile_materialization",
                    conn=db,
                )
            except Exception:
                logger.warning(
                    "contact acquisition enqueue unavailable after profile materialization kol=%s",
                    target_id,
                )
        return {
            "ok": True,
            "dry_run": False,
            "operation": operation,
            "kol_pool_id": target_id,
            "fields_written": sorted(planned_values),
            "ignored_fields": ignored_fields,
            "missing_columns": missing_columns,
            "score_before": before_scores,
            "score_after": after_scores,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
            "matched_existing": matched_existing,
        }
    except Exception:
        _rollback(db)
        raise


def _normalise_profile_data(
    profile_data: dict[str, Any],
    *,
    existing: dict[str, Any] | None,
    now: str,
    method: str,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in PROFILE_BASICS_WHITELIST:
        if key in profile_data:
            normalized[key] = profile_data.get(key)

    platform = _normalise_platform(normalized.get("platform") or (existing or {}).get("platform"))
    handle = _normalise_handle(platform, normalized.get("handle") or (existing or {}).get("handle"))
    # 管2 卫生闸(咽喉审计乙案,2026-06-12):A1 新人建档主链此前零卫生校验。
    # 细则一·拒收可见:留痕 原始URL+被拒handle+规则名——A1 是用户交互链,
    # 用户贴了 URL 没建档必须可查,静默吞掉=静默假设复发。
    rule = _garbage_handle_rule(handle)
    if rule:
        logger.warning(
            "kol_pool pipe2 rejected garbage handle: handle=%r rule=%s url=%r",
            handle[:60], rule, str(profile_data.get("profile_url") or "")[:120],
        )
        handle = ""  # 返空 → 上游既有 "platform and handle are required" 校验自然拦截
    if platform:
        normalized["platform"] = platform
    if handle:
        normalized["handle"] = handle

    for field in ("avatar_url", "bio", "profile_url", "last_video_at"):
        if field in normalized:
            normalized[field] = _text(normalized.get(field))

    for field in ("followers", "posts_count"):
        if field in normalized:
            normalized[field] = _int_or_none(normalized.get(field))

    backfilled_at = _text(normalized.get("profile_backfilled_at")) or now
    normalized["profile_backfilled_at"] = backfilled_at
    if "raw_platform_data" in normalized:
        normalized["raw_platform_data"] = _merge_raw_payload(
            normalized.get("raw_platform_data"),
            method=method,
            profile_backfilled_at=backfilled_at,
        )
    return normalized


def _execute_update(conn: Any, kol_pool_id: int, values: dict[str, Any]) -> None:
    assignments = ", ".join(f"{field}=?" for field in values)
    conn.execute(
        f"UPDATE vkpi_kol_pool SET {assignments} WHERE id=?",
        tuple(values[field] for field in values) + (int(kol_pool_id),),
    )


# ON CONFLICT(platform,handle) 范式与 pool.import_items 同口径:贴 URL 单建档撞
# UNIQUE(platform,handle)(039:42)时不再 IntegrityError,而是 DO UPDATE 既有行的
# profile-basics 列。SCORE_FIELDS 绝不出现在 SET(skip 列 + 派生自 INSERT_FIELDS),
# 既有评分原样保留(红线:不新增 fit_score 写点)。pool_uid 仅 INSERT 生效、冲突不覆写。
_PROFILE_BASICS_CONFLICT_SKIP = {"platform", "handle", "pool_uid"}


def _preexisting_pool_id(conn: Any, platform: Any, handle: Any) -> int | None:
    """按 (platform,handle) 取既有行 id(若有)。供 insert 路径在 ON CONFLICT 前
    快照既有 score——区分『真新行』与『撞已评分行』,避免 score 守卫误杀。"""
    row = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_pool
        WHERE platform=? AND handle=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (platform, handle),
    ).fetchone()
    return int(row["id"]) if row else None


def _execute_insert(conn: Any, values: dict[str, Any]) -> int:
    columns = [field for field in values]
    placeholders = ", ".join("?" for _ in columns)
    set_cols = [c for c in columns if c not in _PROFILE_BASICS_CONFLICT_SKIP]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in set_cols)
    # vkpi_kol_pool 自带 updated_at(039:41);冲突时同步刷新时间戳。
    if "updated_at" in _table_columns(conn, "vkpi_kol_pool") and "updated_at" not in set_cols:
        update_clause = (update_clause + ", " if update_clause else "") + "updated_at=NOW()"
    if not update_clause:
        update_clause = "handle=excluded.handle"  # no-op 占位,保 DO UPDATE 语法合法
    conn.execute(
        f"""
        INSERT INTO vkpi_kol_pool ({', '.join(columns)}) VALUES ({placeholders})
        ON CONFLICT(platform, handle) DO UPDATE SET {update_clause}
        """,
        tuple(values[field] for field in columns),
    )
    row_id = _preexisting_pool_id(conn, values.get("platform"), values.get("handle"))
    if row_id is None:
        raise RuntimeError("inserted vkpi_kol_pool row could not be reloaded")
    return row_id


def _load_pool_row(conn: Any, kol_pool_id: int | None) -> dict[str, Any] | None:
    if not kol_pool_id:
        return None
    row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")
    return dict(row)


def _score_snapshot(conn: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, viltrox_fit_score, viltrox_fit_reason
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
        """,
        tuple(int(item) for item in ids),
    ).fetchall()
    return {int(row["id"]): {"viltrox_fit_score": row["viltrox_fit_score"], "viltrox_fit_reason": row["viltrox_fit_reason"]} for row in rows}


def _changed_score_ids(before: dict[int, dict[str, Any]], after: dict[int, dict[str, Any]]) -> list[int]:
    changed: list[int] = []
    for kol_id, before_item in before.items():
        after_item = after.get(kol_id, {})
        if any(before_item.get(field) != after_item.get(field) for field in SCORE_FIELDS):
            changed.append(kol_id)
    return changed


def _new_row_has_score(score_item: dict[str, Any]) -> bool:
    return score_item.get("viltrox_fit_score") not in (None, "") or score_item.get("viltrox_fit_reason") not in (None, "")


def _should_write(field: str, value: Any, *, operation: str) -> bool:
    if operation == "insert":
        return True
    if field == "raw_platform_data":
        return value not in (None, "")
    if field in {"followers", "posts_count"}:
        return value is not None
    return _text(value) != ""


def _merge_raw_payload(value: Any, *, method: str, profile_backfilled_at: str) -> str:
    payload = _json_obj(value)
    payload.setdefault("profile_backfill", {})
    if isinstance(payload["profile_backfill"], dict):
        payload["profile_backfill"].update(
            {
                "method": method,
                "profile_backfilled_at": profile_backfilled_at,
            }
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except Exception:
            return {"raw_text": value}
    return {}


def _normalise_platform(value: Any) -> str:
    text = _text(value).lower()
    if text in {"yt", "youtube", "youtube.com"}:
        return "youtube"
    if text in {"ig", "instagram", "instagram.com"}:
        return "instagram"
    if text in {"tt", "tiktok", "tiktok.com"}:
        return "tiktok"
    return text


def _normalise_handle(platform: str, value: Any) -> str:
    text = _text(value).strip("/")
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    if platform in {"instagram", "tiktok"}:
        return text.lower()
    if platform == "youtube" and not text.startswith("UC"):
        return text.lower()
    return text


from app.core.coerce import _text


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit(conn: Any) -> None:
    try:
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
