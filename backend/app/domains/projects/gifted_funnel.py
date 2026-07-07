"""G2 · gifted→posted 履约漏斗(gifted_funnel v1)—— 全池「送样→发布」对账板。

口径(真表侦察 2026-07-07,读端零迁移):
  送样(gifted) = vkpi_project_kol_assignments.stage ∈ 送样后词表(device_sent/shipped/
                  received/delivered/arrived)∪ 已发布词表(见下)—— 样品发出过的派单全集;
  已发布(posted)= stage ∈ (content_posted/content_published/published/reviewed/measured/
                  retrospective_ready/retrospective_done)或该 (project, kol) 有视频证据
                  (vkpi_kol_video_evidence)或有人工确认的内容候选
                  (vkpi_project_content_posts.status ∈ matched/retrospective_ready);
  超期未发(overdue)= 送样后 >N 天(默认 21)仍无上述任何内容信号;
  送样时间基准 = 该项目 shipment 最早 delivered_at(有则用)→ 派单 updated_at(stage 落
                 device_sent 的记录时刻,代理口径,诚实标注 basis)。

catch_up_actions(dry_run=True):把超期条目写成 Action Inbox「催更」建议(复用
producers.make_suggestion + inbox.persist_suggestions,守卫 import);dry_run 只预览
不落库;落库也只写 vkpi_action_inbox(系统自身台账),生成物=草稿供人审,绝不真外发。

compat 约定:SQL 占位符 ?;禁 percent 字符(不用 LIKE);BOOLEAN 读回可能是 int 1/0,
判断走 _truthy;时间读回可能是 str,Python 侧容错解析。天数差在 Python 算(不在 SQL 比)。
红线:全程只读业务表;绝不改 stage/项目/费用;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 送样后(内容未见)与已发布的 raw stage 词表(对齐 stage_canonical 的读端映射,含双词汇同义词)。
SENT_STAGES: tuple[str, ...] = ("device_sent", "shipped", "received", "delivered", "arrived")
POSTED_STAGES: tuple[str, ...] = (
    "content_posted", "content_published", "published", "reviewed",
    "measured", "retrospective_ready", "retrospective_done",
)
DEFAULT_OVERDUE_DAYS = 21


# ── 小工具 ──────────────────────────────────────────────────────────


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN/EXISTS 读回可能是 int 1/0 / 't',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _int0(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _parse_dt(value: Any) -> datetime | None:
    """时间双态(datetime / ISO str,含尾 Z)→ aware UTC datetime;解析不了诚实 None。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _days_since(value: Any, now: datetime) -> int | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return max(0, (now - parsed).days)


def _iso_day(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.date().isoformat() if parsed else None


# ── 数据装载(全只读)────────────────────────────────────────────────


def _load_assignment_rows(conn: Any) -> list[dict[str, Any]]:
    """送样过的派单全集 + 内容信号(EXISTS 子查询)+ 送样时间基准候选。"""
    stages = tuple(SENT_STAGES) + tuple(POSTED_STAGES)
    placeholders = ",".join("?" for _ in stages)
    rows = conn.execute(
        f"""
        SELECT a.id AS assignment_id, a.project_id, a.kol_pool_id, a.stage,
               a.created_at, a.updated_at,
               p.project_name, p.product_name, p.assigned_staff_id, p.created_by_staff_id,
               k.handle, k.display_name, k.platform,
               EXISTS(
                 SELECT 1 FROM vkpi_kol_video_evidence e
                 WHERE e.project_id = a.project_id AND e.kol_pool_id = a.kol_pool_id
               ) AS has_evidence,
               EXISTS(
                 SELECT 1 FROM vkpi_project_content_posts cp
                 WHERE cp.project_id = a.project_id AND cp.kol_pool_id = a.kol_pool_id
                   AND cp.status IN ('matched', 'retrospective_ready')
               ) AS has_matched_post,
               (SELECT MIN(s.delivered_at) FROM vkpi_shipments s WHERE s.project_id = a.project_id) AS project_delivered_at
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_projects p ON p.id = a.project_id
        LEFT JOIN vkpi_kol_pool k ON k.id = a.kol_pool_id
        WHERE a.stage IN ({placeholders})
        """,
        stages,
    ).fetchall()
    return [dict(r) for r in rows]


def _status_counts(conn: Any, table: str) -> dict[str, int]:
    from app.db.connection import table_exists

    if not table_exists(table):
        return {}
    try:
        rows = conn.execute(f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status").fetchall()
        return {_text(dict(r).get("status"), 40) or "(空)": _int0(dict(r).get("n")) for r in rows}
    except Exception:
        logger.debug("gifted_funnel.status_counts_failed table=%s", table, exc_info=True)
        return {}


# ── 漏斗核心(funnel 与 catch_up 共用)────────────────────────────────


def _classify(rows: list[dict[str, Any]], *, overdue_days: int, now: datetime) -> dict[str, Any]:
    posted: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    for row in rows:
        stage = _text(row.get("stage"), 40).lower()
        is_posted = (
            stage in POSTED_STAGES
            or _truthy(row.get("has_evidence"))
            or _truthy(row.get("has_matched_post"))
        )
        if is_posted:
            posted.append(row)
            continue
        # 送样时间基准:项目物流签收(有则最准)→ 派单 stage 记录时刻(代理)→ 派单创建。
        if row.get("project_delivered_at") is not None:
            basis, sent_at = "shipment.delivered_at", row.get("project_delivered_at")
        elif row.get("updated_at") is not None:
            basis, sent_at = "assignment.updated_at(代理)", row.get("updated_at")
        else:
            basis, sent_at = "assignment.created_at(代理)", row.get("created_at")
        days = _days_since(sent_at, now)
        item = {
            "assignment_id": _int0(row.get("assignment_id")),
            "project_id": _int0(row.get("project_id")),
            "project_name": _text(row.get("project_name"), 120),
            "product_name": _text(row.get("product_name"), 120),
            "kol_pool_id": _int0(row.get("kol_pool_id")),
            "kol_name": _text(row.get("display_name") or row.get("handle"), 100),
            "platform": _text(row.get("platform"), 30),
            "stage": stage,
            "sent_at": _iso_day(sent_at),
            "sent_basis": basis,
            "days_since_sent": days,
            "assigned_staff_id": _int0(row.get("assigned_staff_id")) or None,
            "created_by_staff_id": _int0(row.get("created_by_staff_id")) or None,
        }
        if days is not None and days > int(overdue_days):
            item["suggested_action"] = "催更"
            overdue.append(item)
        else:
            waiting.append(item)
    overdue.sort(key=lambda it: _int0(it.get("days_since_sent")), reverse=True)
    return {"posted": posted, "waiting": waiting, "overdue": overdue}


def funnel(*, overdue_days: int = DEFAULT_OVERDUE_DAYS, overdue_limit: int = 100) -> dict[str, Any]:
    """全池 送样→发布 漏斗(只读):已送样 n / 已发布 m / 观察中 w / 超期未发 k + 超期红名单。"""
    from app.db.connection import get_conn, table_exists

    days = max(1, min(int(overdue_days or DEFAULT_OVERDUE_DAYS), 365))
    limit = max(1, min(int(overdue_limit or 100), 500))
    if not table_exists("vkpi_project_kol_assignments") or not table_exists("vkpi_projects"):
        return {
            "status": "empty",
            "reason": "派单/项目表未建(vkpi_project_kol_assignments / vkpi_projects),漏斗无从对齐。",
            "gifted": 0, "posted": 0, "waiting": 0, "overdue": 0,
        }
    conn = get_conn()
    now = datetime.now(timezone.utc)
    rows = _load_assignment_rows(conn)
    if not rows:
        return {
            "status": "empty",
            "reason": "没有任何送样过的派单(stage 未到 device_sent/shipped),漏斗诚实为空。",
            "gifted": 0, "posted": 0, "waiting": 0, "overdue": 0,
            "overdue_days": days,
        }
    buckets = _classify(rows, overdue_days=days, now=now)
    gifted_n = len(rows)
    posted_m = len(buckets["posted"])
    overdue_k = len(buckets["overdue"])
    waiting_w = len(buckets["waiting"])
    return {
        "status": "ready",
        "overdue_days": days,
        "gifted": gifted_n,
        "posted": posted_m,
        "waiting": waiting_w,
        "overdue": overdue_k,
        "post_rate": round(posted_m / gifted_n, 4) if gifted_n > 0 else None,
        "stages": [
            {"key": "gifted", "label": "已送样", "n": gifted_n},
            {"key": "posted", "label": "已发布", "n": posted_m},
            {"key": "waiting", "label": f"观察中(≤{days}天)", "n": waiting_w},
            {"key": "overdue", "label": f"超期未发(>{days}天)", "n": overdue_k},
        ],
        "overdue_items": buckets["overdue"][:limit],
        "overdue_truncated": overdue_k > limit,
        "observation_windows": _status_counts(conn, "vkpi_project_content_observation_windows"),
        "content_post_candidates": _status_counts(conn, "vkpi_project_content_posts"),
        "basis_note": (
            "送样时间基准:项目物流签收 delivered_at(有则最准)→ 派单 stage 记录时刻(代理口径,"
            "非真实物流时间,逐条标 sent_basis);已发布 = stage 词表 ∪ 视频证据 ∪ 人工确认内容候选。"
        ),
        "generated_at": now.isoformat(),
        "note": "全只读对账板;超期名单建议动作=催更(生成物走 Action Inbox 草稿供人审,绝不自动外发);不参与 V6 Fit 评分。",
    }


# ── 催更建议(dry_run 预览 / 落 Action Inbox 台账)───────────────────


def _catch_up_suggestion(item: dict[str, Any], *, overdue_days: int) -> dict[str, Any]:
    from app.domains.actions.producers import make_suggestion

    days = _int0(item.get("days_since_sent"))
    kol_name = item.get("kol_name") or f"KOL#{item.get('kol_pool_id')}"
    project_name = item.get("project_name") or item.get("product_name") or f"项目#{item.get('project_id')}"
    owner = item.get("assigned_staff_id") or item.get("created_by_staff_id")
    return make_suggestion(
        category="gifted_catchup",
        dedupe_key=f"gifted_catchup:assignment:{item.get('assignment_id')}",
        title=f"催更 · {kol_name} · {project_name}",
        detail=(
            f"「{project_name}」已向 {kol_name}({item.get('platform') or '平台未知'})送样 {days} 天"
            f"(基准 {item.get('sent_basis')},阈值 {int(overdue_days)} 天),仍未见任何发布内容"
            "(无视频证据、无确认内容候选)。建议人工催更:确认样品是否收到、内容排期到哪一步。"
        ),
        reason=f"送样后超 {int(overdue_days)} 天无内容信号=履约漏斗断点;越早跟进,追回发布的概率越高。",
        priority="high" if days > 45 else "medium",
        entity_type="assignment",
        entity_id=item.get("assignment_id") or "",
        suggested_endpoint="POST /api/admin/vkpi/gifted/funnel/catch-up",
        writes_business_data=False,  # 只落 Action Inbox 自身台账,不改任何业务表
        uses_llm=False,
        requires_approval=True,
        owner_staff_id=owner,
        payload={
            "assignment_id": item.get("assignment_id"),
            "project_id": item.get("project_id"),
            "kol_pool_id": item.get("kol_pool_id"),
            "days_since_sent": days,
            "sent_at": item.get("sent_at"),
            "sent_basis": item.get("sent_basis"),
            "suggested_action": "催更",
        },
        expected_gain="追回超期未发的送样履约,减少「白送样」损耗",
        risk_level="low",
        verification_plan=["生成催更建议进 Action Inbox 供人工跟进(不自动外发、不改派单状态)"],
        affected_tables=["vkpi_action_inbox"],
    )


def catch_up_actions(
    *, dry_run: bool = True, overdue_days: int = DEFAULT_OVERDUE_DAYS, limit: int = 50,
) -> dict[str, Any]:
    """把超期未发条目写成 Action Inbox「催更」建议。dry_run=True 只预览绝不落库。

    落库(dry_run=False)也只写 vkpi_action_inbox(系统自身台账,幂等 UPSERT,已被人工
    处理过的不复活);绝不真发外联/邮件/评论,绝不改派单或项目状态。
    """
    days = max(1, min(int(overdue_days or DEFAULT_OVERDUE_DAYS), 365))
    cap = max(1, min(int(limit or 50), 200))
    snapshot = funnel(overdue_days=days, overdue_limit=cap)
    if snapshot.get("status") != "ready":
        return {"ok": True, "dry_run": bool(dry_run), "overdue": 0, "suggestions": 0,
                "reason": snapshot.get("reason") or "漏斗为空", "items": []}

    overdue_items = snapshot.get("overdue_items") or []
    try:
        suggestions = [_catch_up_suggestion(it, overdue_days=days) for it in overdue_items[:cap]]
    except Exception:
        logger.warning("gifted_funnel.suggestion_build_failed", exc_info=True)
        return {"ok": False, "dry_run": bool(dry_run), "overdue": len(overdue_items),
                "suggestions": 0, "reason": "建议构造失败(producers 不可用)", "items": []}

    preview = [
        {
            "dedupe_key": s.get("dedupe_key"),
            "title": s.get("title"),
            "priority": s.get("priority"),
            "days_since_sent": (s.get("payload") or {}).get("days_since_sent"),
            "owner_staff_id": s.get("owner_staff_id"),
        }
        for s in suggestions
    ]
    if dry_run:
        return {
            "ok": True, "dry_run": True,
            "overdue": _int0(snapshot.get("overdue")),
            "suggestions": len(suggestions),
            "would_write": len(suggestions),
            "items": preview,
            "note": "dry_run 预览:未写任何表;确认后 dry_run=false 幂等落 vkpi_action_inbox(仍需人工审批,绝不自动外发)。",
        }
    try:
        from app.domains.actions.inbox import persist_suggestions

        written = persist_suggestions(suggestions)
    except Exception:
        logger.warning("gifted_funnel.persist_failed", exc_info=True)
        return {"ok": False, "dry_run": False, "overdue": _int0(snapshot.get("overdue")),
                "suggestions": len(suggestions), "written": 0,
                "reason": "Action Inbox 落库失败(inbox 不可用)", "items": preview}
    return {
        "ok": True, "dry_run": False,
        "overdue": _int0(snapshot.get("overdue")),
        "suggestions": len(suggestions),
        "written": written,
        "items": preview,
        "note": "已幂等写入 vkpi_action_inbox(status=suggested,已被人工处理的不复活);催更本身仍由人执行,系统绝不自动外发。",
    }
