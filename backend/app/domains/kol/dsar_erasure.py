"""DSAR 主体级联删除(合规硬门禁)。

一个 KOL 散落 ≥8 处。其中:
  有 ON DELETE CASCADE(删 vkpi_kol_pool 行即连带):
    - vkpi_kol_pool_contacts (114)
    - vkpi_kol_video_evidence (085)
    - vkpi_kol_llm_deep_analysis_results (102)
    - vkpi_kol_profile_index_entries (099)  -- 但 qdrant_point_id 指向的外部 Qdrant point 不会被删
  无 FK / 不级联(必须应用层显式删):
    - vkpi_kol_profile_deep (070, kol_pool_id 无 REFERENCES)
    - vkpi_analysis_cache (096, target_type='kol_pool' + target_id 字符串键,含 outreach_draft/llm_deep)
    - Qdrant collection points(外部向量库)
    - R2 资产(外部对象存储)

本模块:①收集所有散落引用 ②删外部系统(Qdrant/R2)③删无 FK 表 ④删主体行(触发 CASCADE)
        ⑤写 vkpi_dsar_requests.erasure_receipt_json 删除收据 ⑥审计。
红线:只删,不触 viltrox_fit_score 写路径。
Qdrant/R2 client 用项目内既有封装(标 TODO,接入前确认 collection 名 / bucket+key 命名)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn

# Qdrant collection(与 profile_recall.COLLECTION_NAME 同源,避免漂移)。
QDRANT_COLLECTION = "vkpi_kol_profile_index_v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _delete_qdrant_points(point_ids: list[str]) -> dict[str, Any]:
    """删 Qdrant 外部 points(collection vkpi_kol_profile_index_v1)。

    复用 profile_recall._qdrant_client()(同一 client 配置:QDRANT_URL 或本地 path),
    用 http.models.PointIdsList 作 points_selector。返回 {deleted, failed, error}。
    collection 不存在 / client 不可用 → 记 skipped,不抛(删除可重试且不留悬挂)。
    """
    result: dict[str, Any] = {"requested": list(point_ids), "deleted": [], "failed": [], "status": "skipped"}
    if not point_ids:
        result["status"] = "noop"
        return result
    try:
        from app.domains.kol.profile_recall import _qdrant_client
        from qdrant_client.http import models as qdrant_models
        client = _qdrant_client()
        if not client.collection_exists(QDRANT_COLLECTION):
            result["status"] = "collection_absent"
            return result
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=qdrant_models.PointIdsList(points=list(point_ids)),
            wait=True,
        )
        result["deleted"] = list(point_ids)
        result["status"] = "done"
    except Exception as exc:
        result["failed"] = list(point_ids)
        result["status"] = "error"
        result["error"] = str(exc)
    return result


def _delete_r2_objects(r2_keys: list[str]) -> dict[str, Any]:
    """删 R2 外部资产。逐 key 调既有 app.services.media.r2.delete_object(未配置/失败返 False,不抛)。"""
    result: dict[str, Any] = {"requested": list(r2_keys), "deleted": [], "failed": [], "status": "skipped"}
    if not r2_keys:
        result["status"] = "noop"
        return result
    try:
        from app.services.media import r2
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"r2_import_failed:{exc}"
        result["failed"] = list(r2_keys)
        return result
    for key in r2_keys:
        try:
            if r2.delete_object(key):
                result["deleted"].append(key)
            else:
                result["failed"].append(key)
        except Exception:
            result["failed"].append(key)
    result["status"] = "done" if not result["failed"] else ("partial" if result["deleted"] else "error")
    return result


def collect_subject_footprint(kol_pool_id: int) -> dict[str, Any]:
    """只读:枚举该 KOL 散落引用,供审批前预览(谁要被删)。不删除。"""
    conn = get_conn()
    sid = str(int(kol_pool_id))
    footprint: dict[str, Any] = {}
    pool = conn.execute("SELECT platform, handle, email, other_contacts_json FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    footprint["pool"] = dict(pool) if pool else None
    for table in ("vkpi_kol_pool_contacts", "vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results", "vkpi_kol_profile_deep"):
        try:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE kol_pool_id=?", (int(kol_pool_id),)).fetchone()
            footprint[table] = int(n["n"]) if n else 0
        except Exception:
            footprint[table] = "table_absent"
    # analysis_cache 用字符串键(target_type='kol_pool' + target_id 字符串)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM vkpi_analysis_cache WHERE target_type='kol_pool' AND target_id=?", (sid,)).fetchone()
        footprint["vkpi_analysis_cache"] = int(n["n"]) if n else 0
    except Exception:
        footprint["vkpi_analysis_cache"] = "table_absent"
    # Qdrant point ids(外部,DB CASCADE 不删外部 point)
    try:
        rows = conn.execute("SELECT qdrant_point_id FROM vkpi_kol_profile_index_entries WHERE kol_pool_id=?", (int(kol_pool_id),)).fetchall()
        footprint["qdrant_point_ids"] = [r["qdrant_point_id"] for r in rows]
    except Exception:
        footprint["qdrant_point_ids"] = []
    # R2 资产 key(外部,DB CASCADE 删不到):枚举该 KOL 视频在媒体缓存里的 R2 对象。
    # vkpi_media_cache_assets 按 source_url 缓存 → JOIN 该 KOL 的 video_evidence.content_url,
    # **精确匹配该 subject 自己的资产**(不波及他人);拿不到则空(诚实,不臆造)。
    r2_keys: list[str] = []
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT a.r2_key
            FROM vkpi_media_cache_assets a
            WHERE COALESCE(a.r2_key, '') <> ''
              AND a.source_url IN (
                  SELECT content_url FROM vkpi_kol_video_evidence
                  WHERE kol_pool_id = ? AND COALESCE(content_url, '') <> ''
              )
            """,
            (int(kol_pool_id),),
        ).fetchall()
        r2_keys = [str(dict(r).get("r2_key")) for r in rows if str(dict(r).get("r2_key") or "").strip()]
    except Exception:
        r2_keys = []
    footprint["r2_keys"] = r2_keys
    return footprint


def erase_subject(kol_pool_id: int, *, dsar_request_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行级联删除。须先有 approved 的 vkpi_dsar_requests 工单(辖区由 Jianbo 拍板后填)。

    顺序:外部系统(Qdrant/R2)先删(失败可重试且不留悬挂)→ 无 FK 表 → 主体行(触发 DB CASCADE)。
    """
    conn = get_conn()
    req = conn.execute("SELECT status FROM vkpi_dsar_requests WHERE id=?", (int(dsar_request_id),)).fetchone()
    if not req or req["status"] not in ("approved", "executing"):
        return {"status": "blocked", "reason": "dsar request not approved"}
    sid = str(int(kol_pool_id))
    footprint = collect_subject_footprint(kol_pool_id)
    receipt: dict[str, Any] = {"started_at": _utcnow()}

    # 1) 外部 Qdrant points(无法靠 DB CASCADE)—— 用 profile_recall 同源 client + http.models.PointIdsList。
    qdrant_point_ids = [str(p) for p in (footprint.get("qdrant_point_ids") or []) if str(p or "").strip()]
    receipt["qdrant_points"] = qdrant_point_ids
    receipt["qdrant_deleted"] = _delete_qdrant_points(qdrant_point_ids)

    # 2) 外部 R2 资产 —— 用既有 app.services.media.r2.delete_object(.venv 已确认 boto3 可用)。
    r2_keys = [str(k) for k in (footprint.get("r2_keys") or []) if str(k or "").strip()]
    receipt["r2_keys"] = r2_keys
    receipt["r2_deleted"] = _delete_r2_objects(r2_keys)

    # 3) 无 FK 表:profile_deep + analysis_cache(字符串键)显式删
    deleted: dict[str, int] = {}
    cur = conn.execute("DELETE FROM vkpi_kol_profile_deep WHERE kol_pool_id=?", (int(kol_pool_id),))
    deleted["vkpi_kol_profile_deep"] = int(getattr(cur, "rowcount", 0) or 0)
    cur = conn.execute("DELETE FROM vkpi_analysis_cache WHERE target_type='kol_pool' AND target_id=?", (sid,))
    deleted["vkpi_analysis_cache"] = int(getattr(cur, "rowcount", 0) or 0)
    # PII 台账(115):不删,FK ON DELETE SET NULL 保留 handle 快照留痕。

    # 4) 主体行 —— 触发 114/085/099/102 的 ON DELETE CASCADE
    cur = conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),))
    deleted["vkpi_kol_pool(+cascade)"] = int(getattr(cur, "rowcount", 0) or 0)
    receipt["db_deleted"] = deleted
    receipt["finished_at"] = _utcnow()

    # 5) 收据 + 工单收尾
    conn.execute(
        "UPDATE vkpi_dsar_requests SET status='done', erasure_receipt_json=?, executed_at=? WHERE id=?",
        (json.dumps(receipt, ensure_ascii=False), _utcnow(), int(dsar_request_id)),
    )
    conn.commit()

    # 6) 审计(复用既有 sensitive access 审计设施;log_sensitive_access 为 keyword-only)
    try:
        from app.domains.audit.service import log_sensitive_access
        log_sensitive_access(
            staff_id=int((staff or {}).get("staff_id") or (staff or {}).get("user_id") or 0),
            action_type="dsar_erasure", resource_type="kol_pool", resource_id=sid,
            metadata={"dsar_request_id": dsar_request_id, "receipt": receipt},
        )
    except Exception:
        pass
    return {"status": "done", "kol_pool_id": int(kol_pool_id), "receipt": receipt}
