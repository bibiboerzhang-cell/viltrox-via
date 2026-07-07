"""闭环波 L3 · GTM 三窗对答案自动回填(规格第三章:7/14/28 天绝不单窗)。

refresh_gtm_windows(dry_run=False):对 vkpi_gtm_outcomes **未裁决** 行按账龄回填三窗:
  - window_7d  执行效率:联系了吗/回复了吗/寄样了吗/发布了吗
      (读 vkpi_messages 外联双向 + vkpi_shipments 履约 + vkpi_content_posts /
       vkpi_kol_video_evidence 发布 + vkpi_project_kol_assignments 送样漏斗 stage);
  - window_14d 内容表现:evidence 播放/点赞/评论 + 短链点击
      (vkpi_kol_video_evidence + vkpi_link_clicks JOIN vkpi_links,bot 剔除);
  - window_28d 商业结果:订单/GMV 净额(vkpi_sales_attributions,退款负行计入净额);
      本地 Shopify 归因链未上云 → 无单诚实 pending,绝不编造转化。

账龄口径:窗到期才回填(账龄≥7 天填 7d,≥14 填 14d,≥28 填 28d);未到期的窗留 NULL。
每窗 jsonb 必带 filled_at + source + honesty 注 + window_start/window_end,自动填的只是
"答案素材",结论仍归人——decision/lesson/decided_at/decided_by 本模块 **绝不触碰**,
不存在任何自动裁决路径;已裁决行(decision 非 open)三窗冻结为裁决时证据,不再刷新。

KOL 桥:kol_pool_id → vkpi_kol_pool.linked_main_kol_id(人工 promote 落地的高置信桥,
与 recommendations/outcomes 同口径);无桥的 bet(官号/站点类动作)诚实 no_kol_linked,
留给 L4 裁决界面人工补。job 友好签名(纯参数、幂等、单行失败不拖垮整批),收口挂
scheduler 由主循环定,本模块不自行注册任何 job。

compat 约定:SQL 占位符 ?;零字面 percent(不用 LIKE);jsonb 写走 ?::jsonb + json.dumps;
BOOLEAN/时间读回宽容(_truthy/_parse_dt),时间窗口比较全在 Python 算。
红线:只写 vkpi_gtm_outcomes.window_7d/14d/28d 三列;绝不写 decision/lesson/decided_*;
绝不写 viltrox_fit_score、不碰 rule_v0;表未建(L2 迁移 217 并行在建)诚实空态不炸。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_gtm_outcomes"

# 未裁决口径:decision 为空/NULL/'open' 才算 open(与规格 decision 枚举对齐)。
OPEN_DECISIONS = ("", "open")

# 三窗定义:(账龄阈值天数, 列名, 窗标签)。硬件周期长,绝不单窗。
WINDOW_SPECS: tuple[tuple[int, str, str], ...] = (
    (7, "window_7d", "7d"),
    (14, "window_14d", "14d"),
    (28, "window_28d", "28d"),
)

_SOURCE_7D = (
    "auto:outreach+fulfillment+gifted"
    "(vkpi_messages/vkpi_shipments/vkpi_content_posts/vkpi_kol_video_evidence/vkpi_project_kol_assignments)"
)
_SOURCE_14D = "auto:evidence+shortlinks(vkpi_kol_video_evidence/vkpi_link_clicks JOIN vkpi_links)"
_SOURCE_28D = "auto:shopify_attribution(vkpi_sales_attributions;本地归因链未上云,诚实 pending)"

DEFAULT_LIMIT = 500


# ── 小工具(compat 宽容层,与 gifted_funnel 同款口径) ────────────────


from app.core.coerce import _int0, _truthy


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


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    parsed = _parse_dt(value)
    return parsed is not None and start <= parsed <= end


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _ids_placeholders(ids: list[int]) -> str:
    return ",".join("?" for _ in ids)


def _rows(conn: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        logger.debug("gtm_windows.query_failed sql_head=%s", sql.strip()[:80], exc_info=True)
        return []


# ── KOL/项目桥(与 recommendations.outcomes 同口径,只搬已确认的桥) ─


def _linked_kol_id(conn: Any, kol_pool_id: int) -> int:
    """kol_pool → kols 的高置信桥(人工 promote/claim 落的 linked_main_kol_id);无桥回 0。"""
    if kol_pool_id <= 0:
        return 0
    row = conn.execute(
        "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    return _int0(dict(row).get("linked_main_kol_id")) if row else 0


def _project_ids_for_kol(conn: Any, kol_id: int) -> list[int]:
    if kol_id <= 0:
        return []
    rows = _rows(
        conn,
        """
        SELECT id FROM vkpi_projects
        WHERE kol_id=? AND COALESCE(stage_status, '') != 'deleted'
        """,
        (int(kol_id),),
    )
    return [_int0(r.get("id")) for r in rows if _int0(r.get("id")) > 0]


# ── 三窗指标构建(全只读;时间过滤在 Python) ─────────────────────────


def _window_7d_metrics(conn: Any, *, kol_pool_id: int, kol_id: int,
                       project_ids: list[int], start: datetime, end: datetime) -> dict[str, Any]:
    """执行效率:联系/回复/寄样/发布(读外联+履约+送样漏斗既有表)。"""
    # 外联双向:kol 桥 ∪ 该 KOL 项目。
    msg_where: list[str] = []
    msg_params: list[Any] = []
    if kol_id > 0:
        msg_where.append("kol_id=?")
        msg_params.append(kol_id)
    if project_ids:
        msg_where.append(f"project_id IN ({_ids_placeholders(project_ids)})")
        msg_params.extend(project_ids)
    messages = _rows(
        conn,
        f"SELECT direction, captured_at FROM vkpi_messages WHERE {' OR '.join(msg_where)}",
        tuple(msg_params),
    ) if msg_where else []
    outbound = [m for m in messages if _text(m.get("direction"), 20) == "outbound" and _in_window(m.get("captured_at"), start, end)]
    inbound = [m for m in messages if _text(m.get("direction"), 20) == "inbound" and _in_window(m.get("captured_at"), start, end)]

    # 寄样:该 KOL 项目的 shipments(shipped/delivered 双节点)。
    shipments = _rows(
        conn,
        f"SELECT shipped_at, delivered_at FROM vkpi_shipments WHERE project_id IN ({_ids_placeholders(project_ids)})",
        tuple(project_ids),
    ) if project_ids else []
    shipped = [s for s in shipments if _in_window(s.get("shipped_at"), start, end)]
    delivered = [s for s in shipments if _in_window(s.get("delivered_at"), start, end)]

    # 发布:人工确认内容候选(content_posts,项目桥 ∪ kol 桥)∪ 视频证据(kol_pool 直连)。
    post_where: list[str] = []
    post_params: list[Any] = []
    if project_ids:
        post_where.append(f"project_id IN ({_ids_placeholders(project_ids)})")
        post_params.extend(project_ids)
    if kol_id > 0:
        post_where.append("kol_id=?")
        post_params.append(kol_id)
    posts = _rows(
        conn,
        f"SELECT published_at, created_at FROM vkpi_content_posts WHERE {' OR '.join(post_where)}",
        tuple(post_params),
    ) if post_where else []
    posts_in = [p for p in posts if _in_window(p.get("published_at") or p.get("created_at"), start, end)]

    evidence = _rows(
        conn,
        "SELECT posted_at, is_active FROM vkpi_kol_video_evidence WHERE kol_pool_id=?",
        (int(kol_pool_id),),
    ) if kol_pool_id > 0 else []
    evidence_in = [e for e in evidence if _truthy(e.get("is_active")) and _in_window(e.get("posted_at"), start, end)]

    # 送样漏斗 stage 快照(当前态,非窗内事件——诚实标注口径)。
    stage_rows = _rows(
        conn,
        "SELECT stage FROM vkpi_project_kol_assignments WHERE kol_pool_id=?",
        (int(kol_pool_id),),
    ) if kol_pool_id > 0 else []
    stage_counts: dict[str, int] = {}
    for r in stage_rows:
        key = _text(r.get("stage"), 40).lower() or "(空)"
        stage_counts[key] = stage_counts.get(key, 0) + 1

    published_n = len(posts_in) + len(evidence_in)
    return {
        "contacted": len(outbound) > 0,
        "outreach_sent_n": len(outbound),
        "replied": len(inbound) > 0,
        "reply_n": len(inbound),
        "sample_shipped_n": len(shipped),
        "sample_delivered_n": len(delivered),
        "published": published_n > 0,
        "published_n": published_n,
        "published_posts_n": len(posts_in),
        "published_evidence_n": len(evidence_in),
        "assignment_stage_counts": stage_counts,
        "stage_note": "assignment_stage_counts 为当前态快照(非窗内事件),仅供裁决参考。",
    }


def _window_14d_metrics(conn: Any, *, kol_pool_id: int, kol_id: int,
                        project_ids: list[int], start: datetime, end: datetime) -> dict[str, Any]:
    """内容表现:evidence 播放/点赞/评论 + 短链点击(能读则读)。"""
    evidence = _rows(
        conn,
        """
        SELECT posted_at, is_active, view_count, like_count, comment_count
        FROM vkpi_kol_video_evidence WHERE kol_pool_id=?
        """,
        (int(kol_pool_id),),
    ) if kol_pool_id > 0 else []
    in_window = [e for e in evidence if _truthy(e.get("is_active")) and _in_window(e.get("posted_at"), start, end)]

    click_where: list[str] = []
    click_params: list[Any] = []
    if kol_id > 0:
        click_where.append("l.kol_id=?")
        click_params.append(kol_id)
    if project_ids:
        click_where.append(f"l.project_id IN ({_ids_placeholders(project_ids)})")
        click_params.extend(project_ids)
    clicks = _rows(
        conn,
        f"""
        SELECT c.clicked_at, c.is_bot FROM vkpi_link_clicks c
        JOIN vkpi_links l ON l.id = c.link_id
        WHERE {' OR '.join(click_where)}
        """,
        tuple(click_params),
    ) if click_where else []
    valid_clicks = [c for c in clicks if not _truthy(c.get("is_bot")) and _in_window(c.get("clicked_at"), start, end)]

    return {
        "videos_posted_n": len(in_window),
        "views_sum": sum(_int0(e.get("view_count")) for e in in_window),
        "likes_sum": sum(_int0(e.get("like_count")) for e in in_window),
        "comments_sum": sum(_int0(e.get("comment_count")) for e in in_window),
        "shortlink_valid_clicks": len(valid_clicks),
        "metrics_note": "播放/点赞/评论来自视频证据快照(采集时点数),保存率等平台内私数不可得,诚实缺席。",
    }


def _window_28d_metrics(conn: Any, *, kol_id: int, project_ids: list[int],
                        start: datetime, end: datetime) -> dict[str, Any]:
    """商业结果:订单/GMV 净额(退款负行计入);本地 Shopify 未上云 → 无单诚实 pending。"""
    sales_where: list[str] = []
    sales_params: list[Any] = []
    if kol_id > 0:
        sales_where.append("kol_id=?")
        sales_params.append(kol_id)
    if project_ids:
        sales_where.append(f"project_id IN ({_ids_placeholders(project_ids)})")
        sales_params.extend(project_ids)
    sales = _rows(
        conn,
        f"""
        SELECT revenue_cents, occurred_at, created_at, confidence
        FROM vkpi_sales_attributions
        WHERE {' OR '.join(sales_where)}
        """,
        tuple(sales_params),
    ) if sales_where else []
    in_window = [
        s for s in sales
        if _text(s.get("confidence"), 20) != "excluded"
        and _in_window(s.get("occurred_at") or s.get("created_at"), start, end)
    ]
    orders = sum(1 for s in in_window if _int0(s.get("revenue_cents")) > 0)
    gmv_cents = sum(_int0(s.get("revenue_cents")) for s in in_window)
    return {
        "orders_n": orders,
        "gmv_cents_net": gmv_cents,
        "attributed_rows_n": len(in_window),
        "roi_note": "成本口径归 bet 合约(expected/cost),ROI 由裁决界面对着预期算,本窗只给实际净额。",
    }


# ── 单行三窗组装 ─────────────────────────────────────────────────────


def _build_window_payload(conn: Any, row: dict[str, Any], *, horizon_days: int,
                          label: str, now: datetime) -> dict[str, Any]:
    """单窗 payload:filled_at + source + honesty 注 + 窗口边界 + 指标(自动填的只是素材)。"""
    created = _parse_dt(row.get("created_at")) or now
    start = created
    end = created + timedelta(days=horizon_days)
    kol_pool_id = _int0(row.get("kol_pool_id"))
    kol_id = _linked_kol_id(conn, kol_pool_id)
    project_ids = _project_ids_for_kol(conn, kol_id)

    base = {
        "window": label,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "filled_at": now.isoformat(),
        "kol_pool_id": kol_pool_id or None,
        "linked_kol_id": kol_id or None,
        "project_ids": project_ids,
    }

    if label == "7d":
        no_kol = kol_pool_id <= 0
        metrics = {} if no_kol else _window_7d_metrics(
            conn, kol_pool_id=kol_pool_id, kol_id=kol_id, project_ids=project_ids, start=start, end=end)
        base.update({
            "status": "no_kol_linked" if no_kol else "filled",
            "source": _SOURCE_7D,
            "metrics": metrics,
            "honesty": (
                "bet 无 KOL 关联(官号/站点类动作),执行效率窗自动回填不可用,留给裁决界面人工补。"
                if no_kol else
                "自动回填=既有外联/履约/发布真行聚合;无桥项(linked_main_kol_id 缺)只能覆盖 kol_pool 直连表;"
                "回填是素材不是结论,裁决仍归人。"
            ),
        })
        return base

    if label == "14d":
        no_kol = kol_pool_id <= 0
        metrics = {} if no_kol else _window_14d_metrics(
            conn, kol_pool_id=kol_pool_id, kol_id=kol_id, project_ids=project_ids, start=start, end=end)
        base.update({
            "status": "no_kol_linked" if no_kol else "filled",
            "source": _SOURCE_14D,
            "metrics": metrics,
            "honesty": (
                "bet 无 KOL 关联,内容表现窗自动回填不可用,留给裁决界面人工补。"
                if no_kol else
                "播放/评论为证据快照口径(非实时);短链点击已剔 bot;完播/保存等平台私数不可得,诚实缺席。"
            ),
        })
        return base

    # 28d 商业窗:能读则读,无单诚实 pending(绝不编造转化)。
    metrics = _window_28d_metrics(conn, kol_id=kol_id, project_ids=project_ids, start=start, end=end)
    has_signal = _int0(metrics.get("attributed_rows_n")) > 0
    base.update({
        "status": "filled" if has_signal else "pending",
        "source": _SOURCE_28D,
        "metrics": metrics,
        "honesty": (
            "本地 Shopify 归因链未上云(webhook 归因待上线):窗内零归因行=诚实 pending 等上云回填,"
            "不代表零转化;有归因行时按净额口径(退款负行冲减)如实给数。"
        ),
    })
    return base


# ── 主入口(job 友好:纯参数、幂等、单行失败不拖垮) ──────────────────


def refresh_gtm_windows(dry_run: bool = False, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """对未裁决 gtm_outcomes 行按账龄回填三窗(7/14/28)。dry_run=True 只算不写。

    幂等口径:每次重算到期窗并覆盖(未裁决行保持最新素材,filled_at 刷新);
    已裁决行绝不再刷(三窗=裁决时证据,冻结)。只写 window_* 三列,绝不触 decision。
    """
    from app.db.connection import get_conn, table_exists

    now = datetime.now(timezone.utc)
    if not table_exists(TABLE):
        return {
            "status": "empty",
            "reason": f"{TABLE} 未建(L2 迁移 217 并行在建);建表后本 job 自动生效。",
            "dry_run": bool(dry_run), "scanned": 0, "updated_rows": 0,
            "windows_filled": {col: 0 for _, col, _ in WINDOW_SPECS},
            "generated_at": now.isoformat(),
        }

    cap = max(1, min(_int0(limit) or DEFAULT_LIMIT, 2000))
    conn = get_conn()
    open_placeholders = ",".join("?" for _ in OPEN_DECISIONS)
    rows = _rows(
        conn,
        f"""
        SELECT id, kol_pool_id, created_at, decision
        FROM {TABLE}
        WHERE decision IS NULL OR decision IN ({open_placeholders})
        ORDER BY id ASC
        LIMIT ?
        """,
        (*OPEN_DECISIONS, cap),
    )

    filled_counts = {col: 0 for _, col, _ in WINDOW_SPECS}
    details: list[dict[str, Any]] = []
    updated_rows = 0
    failed = 0
    not_due = 0
    for row in rows:
        try:
            created = _parse_dt(row.get("created_at"))
            age_days = (now - created).days if created else None
            due = [(h, col, label) for h, col, label in WINDOW_SPECS
                   if age_days is not None and age_days >= h]
            if not due:
                not_due += 1
                continue
            payloads = {
                col: _build_window_payload(conn, row, horizon_days=h, label=label, now=now)
                for h, col, label in due
            }
            if not dry_run:
                set_sql = ", ".join(f"{col} = ?::jsonb" for col in payloads)
                params: list[Any] = [_dumps(p) for p in payloads.values()]
                params.append(_int0(row.get("id")))
                params.extend(OPEN_DECISIONS)
                # 双保险:UPDATE 再带未裁决守卫,与人工裁决竞态时绝不覆盖已裁决行。
                conn.execute(
                    f"""
                    UPDATE {TABLE} SET {set_sql}
                    WHERE id = ? AND (decision IS NULL OR decision IN ({open_placeholders}))
                    """,
                    tuple(params),
                )
                conn.commit()
            updated_rows += 1
            for col in payloads:
                filled_counts[col] += 1
            if len(details) < 20:
                details.append({
                    "outcome_id": _int0(row.get("id")),
                    "age_days": age_days,
                    "windows_filled": list(payloads.keys()),
                    "kol_pool_id": _int0(row.get("kol_pool_id")) or None,
                })
        except Exception:
            failed += 1
            logger.warning("gtm_windows.refresh_one_failed outcome_id=%s", row.get("id"), exc_info=True)

    return {
        "status": "ok",
        "dry_run": bool(dry_run),
        "scanned": len(rows),
        "updated_rows": updated_rows,
        "not_due": not_due,
        "failed": failed,
        "windows_filled": filled_counts,
        "details": details,
        "generated_at": now.isoformat(),
        "note": (
            "三窗并行绝不单窗:窗到期才回填,未裁决行每跑重算覆盖(素材保鲜),已裁决行冻结;"
            "只写 window_7d/14d/28d 三列,decision/lesson 只归人工裁决端点,本 job 无任何自动裁决路径。"
        ),
    }


__all__ = ["refresh_gtm_windows", "WINDOW_SPECS", "OPEN_DECISIONS", "TABLE"]
