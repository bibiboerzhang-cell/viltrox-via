"""W2 · Projects 履约自动化审计 helper + 只读聚合(timeline)。

职责:
- record_audit():纯审计落库(record-only)。失败仅 warning,绝不拖垮主流程。
- list_project_audit():只读拉某项目的审计行(detail_json loads 回 dict)。
- build_project_timeline():只读聚合「建→选→寄→签→观察→发布→复盘」时间线,
  数据源全是既有履约/项目表,绝不写任何业务表、绝不碰 viltrox_fit_score / rule_v0。

红线:
- 本表 vkpi_project_automation_audit 自带 CHECK(touches_v6_fit=FALSE);本模块绝不写该列。
- 全部 get_conn() + '?' 占位;detail_json 走 json.dumps(default=str, ensure_ascii=False) + ?::jsonb。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.projects import retrospective_aggregate, stage_canonical

logger = get_logger(__name__)

_TABLE = "vkpi_project_automation_audit"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {}


# ── 审计落库(record-only,容错) ─────────────────────────────────────────
def record_audit(
    *,
    project_id: int,
    action: str,
    shipment_id: int | None = None,
    window_id: int | None = None,
    scanned_kol_count: int = 0,
    matched_count: int = 0,
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """落一条履约自动化审计行。缺表静默 return;任何失败仅 warning(诚实降级)。

    绝不写 touches_v6_fit(列默认 FALSE + CHECK 兜底);action 受表级 CHECK 约束。
    """
    if not table_exists(_TABLE):
        return
    try:
        conn = get_conn()
        conn.execute(
            f"""
            INSERT INTO {_TABLE}
              (project_id, action, shipment_id, window_id,
               scanned_kol_count, matched_count, reason, detail_json, created_at)
            VALUES (?,?,?,?,?,?,?,?::jsonb,NOW())
            """,
            (
                int(project_id),
                str(action),
                int(shipment_id) if shipment_id is not None else None,
                int(window_id) if window_id is not None else None,
                int(scanned_kol_count or 0),
                int(matched_count or 0),
                str(reason or ""),
                _dumps(detail),
            ),
        )
        conn.commit()
    except Exception:
        # 审计失败绝不拖垮主流程(与 inbox.py ledger 落库同款容错)。
        logger.warning(
            "project_automation_audit.record_failed",
            extra={"project_id": project_id, "action": action},
            exc_info=True,
        )


# ── 只读拉审计行 ─────────────────────────────────────────────────────────
def list_project_audit(project_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """拉某项目的审计行(最近优先)。缺表返回 []。"""
    if not table_exists(_TABLE):
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    rows = get_conn().execute(
        f"""
        SELECT id, project_id, action, shipment_id, window_id,
               scanned_kol_count, matched_count, reason, detail_json, created_at
        FROM {_TABLE}
        WHERE project_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(project_id), safe_limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["detail_json"] = _loads(row.get("detail_json"))
        out.append(row)
    return out


# ── 只读聚合:项目履约时间线 ─────────────────────────────────────────────
def _safe_fetchall(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """缺表/列漂移容错:任一聚合查询坏了 → 返回空,不拖垮整张时间线(诚实降级)。"""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        logger.warning("project_timeline.subquery_failed", exc_info=True)
        return []


def _safe_fetchone(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception:
        logger.warning("project_timeline.subquery_failed", exc_info=True)
        return {}


def build_project_timeline(project_id: int) -> dict[str, Any]:
    """聚合「建→选→寄→签→观察→发布→复盘」时间线(纯只读,零业务写)。

    数据源(全部只读):
      - vkpi_projects.stage(canonical 化)= 当前阶段。
      - vkpi_project_kol_assignments.stage 分布(归一)= 各 canonical 阶段命中数。
      - vkpi_shipments.delivered_at = 已签收(delivered 阶段到达 + 首签时间)。
      - vkpi_project_content_observation_windows.status = 观察窗口分布。
      - vkpi_project_content_posts.status = 内容候选/命中分布。
      - retrospective_aggregate.latest_retrospective_job() = 复盘作业态。
      - list_project_audit() = 自动化审计尾巴(可选)。

    输出 stages 按 CANONICAL_STAGES 顺序;reached/at/counts 由上述数据源派生。
    红线:绝不写任何表、绝不碰 viltrox_fit_score / rule_v0。
    """
    pid = int(project_id)
    conn = get_conn()

    # a) 当前阶段
    proj = _safe_fetchone(
        conn,
        "SELECT stage, stage_status FROM vkpi_projects WHERE id = ?",
        (pid,),
    )
    current_stage = stage_canonical.to_canonical(
        str(proj.get("stage") or ""), str(proj.get("stage_status") or "")
    )
    current_idx = stage_canonical.canonical_index(current_stage)

    # b) assignment stage 分布(raw → canonical 归一)
    assign_rows = _safe_fetchall(
        conn,
        """
        SELECT stage AS raw_stage, COUNT(*) AS cnt
        FROM vkpi_project_kol_assignments
        WHERE project_id = ?
        GROUP BY stage
        """,
        (pid,),
    )
    assignment_stage_dist: dict[str, int] = {}
    for r in assign_rows:
        canon = stage_canonical.to_canonical(str(r.get("raw_stage") or ""))
        assignment_stage_dist[canon] = assignment_stage_dist.get(canon, 0) + int(r.get("cnt") or 0)

    # c) shipments delivered
    ship = _safe_fetchone(
        conn,
        """
        SELECT MIN(delivered_at) AS first_delivered_at,
               COUNT(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered_count
        FROM vkpi_shipments
        WHERE project_id = ?
        """,
        (pid,),
    )
    delivered_count = int(ship.get("delivered_count") or 0)
    first_delivered_at = ship.get("first_delivered_at")

    # d) observation windows by status
    win_rows = _safe_fetchall(
        conn,
        """
        SELECT status, COUNT(*) AS cnt, MIN(starts_at) AS first_starts_at
        FROM vkpi_project_content_observation_windows
        WHERE project_id = ?
        GROUP BY status
        """,
        (pid,),
    )
    windows_by_status: dict[str, int] = {}
    windows_first_starts_at = None
    for r in win_rows:
        windows_by_status[str(r.get("status") or "")] = int(r.get("cnt") or 0)
        st = r.get("first_starts_at")
        if st and (windows_first_starts_at is None or str(st) < str(windows_first_starts_at)):
            windows_first_starts_at = st
    windows_total = sum(windows_by_status.values())

    # e) content posts by status
    post_rows = _safe_fetchall(
        conn,
        """
        SELECT status, COUNT(*) AS cnt
        FROM vkpi_project_content_posts
        WHERE project_id = ?
        GROUP BY status
        """,
        (pid,),
    )
    posts_by_status: dict[str, int] = {
        str(r.get("status") or ""): int(r.get("cnt") or 0) for r in post_rows
    }
    posts_matched = int(posts_by_status.get("matched", 0))
    posts_candidate = int(posts_by_status.get("candidate", 0))

    # f) retrospective job
    try:
        retro = retrospective_aggregate.latest_retrospective_job(pid)
    except Exception:
        logger.warning("project_timeline.retrospective_failed", exc_info=True)
        retro = {"active": None, "last": None}

    # g) automation audit tail(可选)
    audit_tail = list_project_audit(pid, limit=20)

    # ── 派生每个 canonical 阶段的 reached / at / counts ──────────────────
    stages: list[dict[str, Any]] = []
    for idx, key in enumerate(stage_canonical.CANONICAL_STAGES):
        counts: dict[str, Any] = {}
        at: Any = None
        # 阶段达成判定:当前阶段索引 >= 本阶段(项目已走过/正在该阶段)。
        reached = current_idx >= 0 and idx <= current_idx
        assign_here = int(assignment_stage_dist.get(key, 0))
        if assign_here:
            counts["assignments"] = assign_here
            reached = True  # 有派单停在/经过该阶段 → 达成
        if key == "delivered":
            counts["delivered_shipments"] = delivered_count
            if delivered_count:
                reached = True
                at = first_delivered_at
        if key == "observation_pending":
            counts["windows"] = windows_total
            counts["windows_by_status"] = windows_by_status
            if windows_total:
                reached = True
                at = windows_first_starts_at
        if key in ("content_detected", "content_published"):
            counts["candidate"] = posts_candidate
            counts["matched"] = posts_matched
            if key == "content_published" and posts_matched:
                reached = True
        if key in ("retrospective_ready", "retrospective_done"):
            counts["retrospective_active"] = bool(retro.get("active"))
            last = retro.get("last") or {}
            counts["retrospective_last_status"] = str((last or {}).get("status") or "") if last else ""
            if key == "retrospective_done" and str((last or {}).get("status") or "") in ("done", "ready"):
                reached = True
        stages.append(
            {
                "key": key,
                "label_zh": stage_canonical.stage_label_zh(key),
                "index": idx,
                "reached": bool(reached),
                "at": at,
                "counts": counts,
            }
        )

    return {
        "project_id": pid,
        "current_stage": current_stage,
        "current_stage_label_zh": stage_canonical.stage_label_zh(current_stage),
        "stages": stages,
        "assignment_stage_dist": assignment_stage_dist,
        "shipments": {
            "delivered_count": delivered_count,
            "first_delivered_at": first_delivered_at,
        },
        "windows": {"total": windows_total, "by_status": windows_by_status},
        "content_posts": {
            "by_status": posts_by_status,
            "matched": posts_matched,
            "candidate": posts_candidate,
        },
        "retrospective": {"active": retro.get("active"), "last": retro.get("last")},
        "automation_audit_tail": audit_tail,
    }
