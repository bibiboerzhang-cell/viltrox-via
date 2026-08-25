"""Safe KOL Pool profile-basics writer.

This service is the reusable write boundary for profile-level backfills. It is
deliberately limited to basic profile fields and verifies that V6 Fit fields do
not change.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
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
    avatar_landing_budget: Any | None = None,
) -> dict[str, Any]:
    """Insert/update profile basics without touching V6 Fit fields.

    ``dry_run`` defaults to True so callers must explicitly opt into a write.
    When writing, this function checks score fields before/after and rolls back
    if any existing score changes or a newly inserted row receives a score.

    ``avatar_landing_budget`` 是批量入库的落地闸(见 ``kol.avatar_landing``)。
    不传则按「单条写入最多落地一张」处理;落地永远在主写入提交之后进行,
    失败只告警,绝不阻断建档。
    """
    if not isinstance(profile_data, dict):
        raise ValueError("profile_data must be a dict")

    db = conn or get_conn()
    columns = _table_columns(db, "vkpi_kol_pool")
    if not columns:
        raise RuntimeError("vkpi_kol_pool schema unavailable")

    # Every path that creates a Pool row (provider discovery and URL deep
    # crawl included) crosses the same conservative official-account gate.
    # Existing rows remain refreshable; this gate prevents new pollution and
    # does not turn a profile refresh into a destructive cleanup operation.
    if not kol_pool_id:
        from app.domains.kol.discovery_filters import discovery_account_gate_verdict

        gate_verdict = discovery_account_gate_verdict(profile_data)
        if gate_verdict:
            raise ValueError(f"discovery_account_rejected:{gate_verdict}")

    requested_identity = dict(profile_data)
    identity_write_locked = bool(not kol_pool_id and not dry_run)
    canonical_match_id: int | None = None
    if not kol_pool_id:
        try:
            if not dry_run:
                _lock_creator_identity_write_boundary(db, requested_identity)
            canonical_match_id = _canonical_existing_pool_id(db, requested_identity)
        except Exception:
            if not dry_run:
                _rollback(db)
            raise
        if canonical_match_id:
            kol_pool_id = canonical_match_id

    now = _utcnow()
    try:
        row = _load_pool_row(db, kol_pool_id) if kol_pool_id else None
    except Exception:
        if identity_write_locked:
            _rollback(db)
        raise
    operation = "update" if row else "insert"
    normalized_input = dict(profile_data)
    if canonical_match_id and row:
        # The incumbent handle remains the durable key.  The incoming handle
        # is recorded as an alias below; rewriting the unique key here could
        # collide with a legacy duplicate before its backfill is reconciled.
        normalized_input.pop("platform", None)
        normalized_input.pop("handle", None)
    normalized = _normalise_profile_data(normalized_input, existing=row, now=now, method=method)
    ignored_fields = sorted(set(profile_data) - PROFILE_BASICS_WHITELIST)

    if operation == "insert":
        if not normalized.get("platform") or not normalized.get("handle"):
            if not dry_run:
                _rollback(db)
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

    try:
        before_scores = _score_snapshot(db, [int(kol_pool_id)]) if row else {}
    except Exception:
        if identity_write_locked:
            _rollback(db)
        raise
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

        _record_creator_identity_alias(
            db,
            target_id,
            requested_identity,
            canonical_match=bool(canonical_match_id),
        )
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
        # 头像落地(2026-08-25 车道 2):池表里只存一根会过期的签名外链是病根
        # (实测 927 行签名型 CDN,过期抽样 12/12 全死)。凡本次真写了 avatar_url,
        # 就在主写入提交之后把图片复制进我们自己的媒体缓存,并打诚实标记。
        # 全程 best-effort:抓不到/传不上只降级为 external,绝不害建档失败。
        avatar_landing: dict[str, Any] = {}
        if target_id and "avatar_url" in planned_values:
            avatar_landing = _land_profile_avatar(
                db,
                int(target_id),
                planned_values.get("avatar_url"),
                platform=str(normalized.get("platform") or (row or {}).get("platform") or ""),
                external_id=str(normalized.get("handle") or (row or {}).get("handle") or ""),
                budget=avatar_landing_budget,
                commit=commit_write,
            )
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
            "matched_by_canonical_identity": bool(canonical_match_id),
            "avatar_landing": avatar_landing,
        }
    except Exception:
        _rollback(db)
        raise


def _land_profile_avatar(
    conn: Any,
    kol_pool_id: int,
    avatar_url: Any,
    *,
    platform: str,
    external_id: str,
    budget: Any | None,
    commit: bool,
) -> dict[str, Any]:
    """建档收尾:把头像落进自家缓存并打标记。任何异常都吞在这里,建档已经成功。"""
    try:
        from app.domains.kol.avatar_landing import (
            SINGLE_WRITE_LANDING_LIMIT,
            land_and_stamp_avatar,
            new_landing_budget,
        )

        return land_and_stamp_avatar(
            conn,
            kol_pool_id,
            avatar_url,
            platform=platform,
            external_id=external_id,
            budget=budget if budget is not None else new_landing_budget(SINGLE_WRITE_LANDING_LIMIT),
            commit=commit,
        )
    except Exception:
        logger.warning("avatar landing unavailable after profile write kol=%s", kol_pool_id, exc_info=True)
        return {}


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
            existing_value=(existing or {}).get("raw_platform_data"),
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


def _merge_raw_payload(
    value: Any,
    *,
    existing_value: Any = None,
    method: str,
    profile_backfilled_at: str,
) -> str:
    payload = _merge_presence_payload(existing_value, value)
    payload.setdefault("profile_backfill", {})
    if isinstance(payload["profile_backfill"], dict):
        payload["profile_backfill"].update(
            {
                "method": method,
                "profile_backfilled_at": profile_backfilled_at,
            }
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


def _merge_presence_payload(existing: Any, incoming: Any) -> dict[str, Any]:
    """Deep-merge observed payloads without erasing richer identity evidence."""
    result = _json_obj(existing)
    for key, value in _json_obj(incoming).items():
        previous = result.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            result[key] = _merge_presence_payload(previous, value)
        elif isinstance(previous, list) and isinstance(value, list) and "alias" in str(key).lower():
            merged: list[Any] = []
            markers: set[str] = set()
            for entry in [*previous, *value]:
                marker = json.dumps(entry, ensure_ascii=True, sort_keys=True, default=str)
                if marker not in markers:
                    markers.add(marker)
                    merged.append(entry)
            result[key] = merged
        elif value not in (None, "", [], {}):
            result[key] = value
        elif key not in result:
            result[key] = value
    return result


def _canonical_existing_pool_id(conn: Any, profile_data: dict[str, Any]) -> int | None:
    """Find an existing master row by any observed stable identity alias."""
    from app.domains.kol.profile_online_inventory import _matching_pool_ids

    matches = _matching_pool_ids(conn, profile_data, fail_closed=True)
    if len(matches) > 1:
        raise RuntimeError(
            "canonical creator identity is ambiguous across multiple pool masters: "
            f"{sorted(matches)}"
        )
    return next(iter(matches), None)


def _lock_creator_aliases(conn: Any, aliases: set[str]) -> None:
    """Serialize canonical check-and-write for PostgreSQL and SQLite.

    PostgreSQL takes transaction-scoped advisory locks for every observed
    alias in deterministic order. SQLite upgrades the current transaction to
    a database writer (or begins one immediately), so a second process cannot
    perform its canonical read until the first creator write commits.
    """
    stable_aliases = sorted(str(alias) for alias in aliases if str(alias).strip())
    if not stable_aliases:
        raise ValueError("creator identity has no stable canonical alias")
    if conn.__class__.__name__ == "PostgresCompatConnection":
        for alias in stable_aliases:
            digest = hashlib.sha256(alias.encode("utf-8")).digest()
            lock_key = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
            conn.execute("SELECT pg_advisory_xact_lock(?)", (lock_key,))
        return
    if isinstance(conn, sqlite3.Connection):
        if conn.in_transaction:
            # A zero-row UPDATE upgrades an existing deferred transaction to a
            # RESERVED writer without mutating application data.
            conn.execute("UPDATE vkpi_kol_pool SET id=id WHERE 0")
        else:
            conn.execute("BEGIN IMMEDIATE")


def _lock_creator_identity_write_boundary(conn: Any, identity: dict[str, Any]) -> None:
    from app.domains.kol.identity import canonical_creator_aliases

    _lock_creator_aliases(conn, canonical_creator_aliases(identity))


def _record_creator_identity_alias(
    conn: Any,
    kol_pool_id: int,
    identity: dict[str, Any],
    *,
    canonical_match: bool,
) -> None:
    """Persist the observed locator without ever rebinding another master.

    The alias table already exists in the current schema.  This write contains
    identity metadata only and never touches fit fields.
    """
    platform = _normalise_platform(identity.get("platform"))
    handle = _normalise_handle(
        platform,
        identity.get("handle")
        or identity.get("username")
        or identity.get("channel_handle"),
    )
    if not platform or not handle:
        return
    profile_url = _text(
        identity.get("profile_url")
        or identity.get("channel_url")
        or identity.get("url")
    )
    raw = _json_obj(identity.get("raw_platform_data") or identity.get("raw"))
    nested_identity = _json_obj(
        raw.get("discovery_identity_v1") or raw.get("online_identity_v1")
    )
    metadata = {
        "source": "profile_basics_identity_boundary",
        "canonical_match": canonical_match,
    }
    for key in ("channel_id", "channelId", "account_id", "platform_user_id", "native_id"):
        value = identity.get(key) or raw.get(key) or nested_identity.get(key)
        if value:
            metadata[key] = str(value)[:200]
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool_aliases
            (kol_pool_id, platform, handle, profile_url, confidence, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(platform, handle) DO UPDATE SET
            profile_url=CASE WHEN excluded.profile_url<>'' THEN excluded.profile_url ELSE vkpi_kol_pool_aliases.profile_url END,
            confidence=excluded.confidence,
            metadata_json=excluded.metadata_json
        WHERE vkpi_kol_pool_aliases.kol_pool_id=excluded.kol_pool_id
        """,
        (
            int(kol_pool_id),
            platform,
            handle,
            profile_url,
            1.0,
            json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        ),
    )
    owner = conn.execute(
        """
        SELECT kol_pool_id
        FROM vkpi_kol_pool_aliases
        WHERE platform=? AND handle=?
        """,
        (platform, handle),
    ).fetchone()
    owner_id = int(owner["kol_pool_id"]) if owner else None
    if owner_id != int(kol_pool_id):
        raise RuntimeError(
            "creator identity alias belongs to a different pool master: "
            f"{platform}:{handle} owner={owner_id} requested={int(kol_pool_id)}"
        )


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
    conn.commit()


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
