"""搜索页反馈写口(学习闭环 L 车道 · L→F 契约):发现墙 / KOL 详情的「有用 / 没用 + 拒绝原因」。

契约(简报冻结):
  POST /api/admin/vkpi/recommendations/search-feedback
  body {source: discovery_wall|kol_detail, kol_pool_id, session_item_id?, verdict: up|down,
        reason?: not_relevant|wrong_region|too_small|brand_official|duplicate|other}
  → 幂等落 vkpi_recommendation_feedback,返回 {ok, feedback_id}。

映射(L 决定):
  verdict up   → feedback_type='shortlist'(FEEDBACK_SCORE 已认识的正向词);
  verdict down → feedback_type='reject' + reason(闭集,down 时必填);
  recommendation_id:该 KOL 最新推荐行(有则挂上,让既有 outcome/记分卡链路也吃到);没有也照落
  (迁移 290 放宽 NOT NULL),以 (source, kol_pool_id, staff) 去重——同人同源同 KOL 改判走 UPDATE,
  永不堆行。有推荐行时顺带 record_if_missing 对应 outcome 节点(shortlisted / rejected),失败只告警。

红线:零 LLM;唯一写表 vkpi_recommendation_feedback(+ outcomes 节点促升);绝不写 viltrox_fit_score。
compat:占位符 ?;零字面 percent;BOOLEAN 读回宽容。迁移 290 未 apply → {ok: False, reason: migration_290_missing}。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.projects.workflow_common import staff_id as resolve_staff_id
from app.shared.vkpi_utils import json_dumps, json_loads, utcnow_iso

logger = get_logger(__name__)

SOURCES: tuple[str, ...] = ("discovery_wall", "kol_detail")
VERDICTS: tuple[str, ...] = ("up", "down")
REASONS: tuple[str, ...] = (
    "not_relevant",
    "wrong_region",
    "too_small",
    "brand_official",
    "duplicate",
    "other",
)
# 门面文案由前端 i18n 负责;这里只给稳定 key + 中文默认标签(不含内部术语)。
REASON_LABELS_ZH: dict[str, str] = {
    "not_relevant": "内容不相关",
    "wrong_region": "地区不符",
    "too_small": "体量太小",
    "brand_official": "品牌官方账号",
    "duplicate": "重复账号",
    "other": "其他",
}
VERDICT_FEEDBACK_TYPE: dict[str, str] = {"up": "shortlist", "down": "reject"}
_OUTCOME_NODE: dict[str, str] = {"shortlist": "shortlisted", "reject": "rejected"}

_COLUMNS_READY: bool | None = None


def reason_options() -> list[dict[str, str]]:
    return [{"key": key, "label_zh": REASON_LABELS_ZH[key]} for key in REASONS]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def columns_ready(*, force: bool = False) -> bool:
    """迁移 290 三列是否就位(一次探测后缓存;探测失败回滚事务,避免 PG aborted 状态外溢)。"""
    global _COLUMNS_READY
    if _COLUMNS_READY is not None and not force:
        return _COLUMNS_READY
    if not table_exists("vkpi_recommendation_feedback"):
        _COLUMNS_READY = False
        return False
    conn = get_conn()
    try:
        conn.execute("SELECT source, kol_pool_id, reason FROM vkpi_recommendation_feedback LIMIT 1").fetchall()
        _COLUMNS_READY = True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("search_feedback.probe_rollback_failed", exc_info=True)
        _COLUMNS_READY = False
    return _COLUMNS_READY


def validate_payload(body: dict[str, Any] | None) -> dict[str, Any]:
    """闭集校验;不合法抛 ValueError(路由转 400)。返回规范化后的 payload。"""
    body = body or {}
    source = str(body.get("source") or "").strip().lower()
    if source not in SOURCES:
        raise ValueError(f"source must be one of {list(SOURCES)}")
    kol_pool_id = _int(body.get("kol_pool_id"))
    if kol_pool_id <= 0:
        raise ValueError("kol_pool_id must be a positive integer")
    verdict = str(body.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {list(VERDICTS)}")
    reason = str(body.get("reason") or "").strip().lower()
    if reason and reason not in REASONS:
        raise ValueError(f"reason must be one of {list(REASONS)}")
    if verdict == "down" and not reason:
        raise ValueError("reason is required when verdict is down")
    if verdict == "up":
        reason = ""
    session_item_id = str(body.get("session_item_id") or "").strip()[:120]
    note = str(body.get("note") or "").strip()[:500]
    return {
        "source": source,
        "kol_pool_id": kol_pool_id,
        "verdict": verdict,
        "reason": reason,
        "session_item_id": session_item_id,
        "note": note,
    }


def _latest_recommendation_id(conn: Any, kol_pool_id: int) -> int:
    if not table_exists("vkpi_kol_recommendations"):
        return 0
    row = conn.execute(
        "SELECT id FROM vkpi_kol_recommendations WHERE kol_pool_id=? ORDER BY id DESC LIMIT 1",
        (int(kol_pool_id),),
    ).fetchone()
    return _int(dict(row).get("id")) if row else 0


def record_search_feedback(body: dict[str, Any] | None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """幂等落一行搜索反馈(同 source x kol_pool_id x staff 改判 = UPDATE)。"""
    payload = validate_payload(body)
    if not columns_ready():
        return {"ok": False, "reason": "migration_290_missing", "feedback_id": None}
    staff_id = resolve_staff_id(staff) or None
    feedback_type = VERDICT_FEEDBACK_TYPE[payload["verdict"]]
    conn = get_conn()
    recommendation_id = _latest_recommendation_id(conn, payload["kol_pool_id"])
    now = utcnow_iso()
    metadata = {
        "source": payload["source"],
        "verdict": payload["verdict"],
        "reason": payload["reason"],
        "session_item_id": payload["session_item_id"],
        "kol_pool_id": payload["kol_pool_id"],
        "writer": "search_feedback.record_search_feedback",
    }
    existing = conn.execute(
        """
        SELECT id, feedback_type, reason, metadata_json
        FROM vkpi_recommendation_feedback
        WHERE source=? AND kol_pool_id=? AND COALESCE(created_by_staff_id, 0)=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (payload["source"], payload["kol_pool_id"], int(staff_id or 0)),
    ).fetchone()
    if existing:
        row = dict(existing)
        feedback_id = _int(row.get("id"))
        unchanged = (
            str(row.get("feedback_type") or "") == feedback_type
            and str(row.get("reason") or "") == payload["reason"]
        )
        if not unchanged:
            prior = json_loads(row.get("metadata_json"), {}) or {}
            metadata["updated_at"] = now
            metadata["previous"] = {
                "feedback_type": row.get("feedback_type"),
                "reason": row.get("reason"),
                "verdict": prior.get("verdict") if isinstance(prior, dict) else None,
            }
            conn.execute(
                """
                UPDATE vkpi_recommendation_feedback
                SET feedback_type=?, reason=?, note=?, metadata_json=?,
                    recommendation_id=COALESCE(recommendation_id, ?)
                WHERE id=?
                """,
                (feedback_type, payload["reason"], payload["note"], json_dumps(metadata),
                 recommendation_id or None, feedback_id),
            )
            conn.commit()
        result = {"ok": True, "feedback_id": feedback_id, "deduped": True, "updated": not unchanged}
    else:
        row = conn.execute(
            """
            INSERT INTO vkpi_recommendation_feedback
                (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json,
                 source, kol_pool_id, reason)
            VALUES (?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,
            (recommendation_id or None, feedback_type, payload["note"], staff_id, now, json_dumps(metadata),
             payload["source"], payload["kol_pool_id"], payload["reason"]),
        ).fetchone()
        conn.commit()
        result = {"ok": True, "feedback_id": _int(dict(row).get("id")) if row else None, "deduped": False, "updated": False}
    result.update({
        "feedback_type": feedback_type,
        "recommendation_id": recommendation_id or None,
        "outcome_node": "",
        "outcome_changed": False,
    })
    node = _OUTCOME_NODE.get(feedback_type, "")
    if recommendation_id and node:
        try:
            from app.domains.recommendations import outcomes as outcome_collector

            changed = outcome_collector.record_if_missing(
                recommendation_id, node,
                context={"source": "search_feedback", **metadata},
            )
            result["outcome_node"] = node
            result["outcome_changed"] = bool(changed)
        except Exception:
            logger.warning("search_feedback.outcome_failed rec_id=%s node=%s", recommendation_id, node, exc_info=True)
    return result


def count_search_feedback(*, staff: dict[str, Any] | None = None, source: str = "") -> dict[str, Any]:
    """已标注数(供「已标注 N 条」角标):总量 / up / down / 按原因 / 按来源 / 本人。"""
    empty = {"ok": True, "total": 0, "up": 0, "down": 0, "mine": 0, "by_reason": {}, "by_source": {}}
    if not columns_ready():
        return {**empty, "ok": False, "reason": "migration_290_missing"}
    clean_source = str(source or "").strip().lower()
    if clean_source and clean_source not in SOURCES:
        raise ValueError(f"source must be one of {list(SOURCES)}")
    conn = get_conn()
    where = "WHERE source <> ''"
    params: list[Any] = []
    if clean_source:
        where += " AND source=?"
        params.append(clean_source)
    rows = conn.execute(
        f"""
        SELECT source, feedback_type, reason, created_by_staff_id, COUNT(*) AS n
        FROM vkpi_recommendation_feedback
        {where}
        GROUP BY source, feedback_type, reason, created_by_staff_id
        """,
        tuple(params),
    ).fetchall()
    me = resolve_staff_id(staff) or 0
    out = dict(empty)
    by_reason: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for raw in rows:
        row = dict(raw)
        n = _int(row.get("n"))
        out["total"] += n
        ftype = str(row.get("feedback_type") or "")
        if ftype == "shortlist":
            out["up"] += n
        elif ftype == "reject":
            out["down"] += n
        reason = str(row.get("reason") or "")
        if reason:
            by_reason[reason] = by_reason.get(reason, 0) + n
        src = str(row.get("source") or "")
        by_source[src] = by_source.get(src, 0) + n
        if me and _int(row.get("created_by_staff_id")) == me:
            out["mine"] += n
    out["by_reason"] = dict(sorted(by_reason.items()))
    out["by_source"] = dict(sorted(by_source.items()))
    return out


__all__ = [
    "SOURCES", "VERDICTS", "REASONS", "REASON_LABELS_ZH", "VERDICT_FEEDBACK_TYPE",
    "reason_options", "validate_payload", "columns_ready",
    "record_search_feedback", "count_search_feedback",
]
