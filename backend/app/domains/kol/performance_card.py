"""G2 · KOL 战绩卡(performance_card v1)—— 该 KOL 为 Viltrox 带来的战绩汇总,可回传给创作者。

创作者最想要却没品牌给的东西:一张「你为我们带来了什么」的真数战绩卡。
四块(全部纯聚合已有数据,零新采集、零 LLM、零写库):
  🎬 content   带品内容:Viltrox 相关视频数 / 总播放 / 总互动(evidence)+ 已深析条数(final_v1);
  🛒 commerce  商业转化:goaffpro 短链点击/订单/GMV + Shopify 归因订单 + 自建短链点击/订单
               (三源分列;没有跨源 canonical key 时不生成伪总数);
  🗓 timeline  合作时间线:项目派单(vkpi_project_kol_assignments×vkpi_projects)+ 首条/最新带品视频;
  💌 share_text 可直接发给 KOL 的中英双语感谢短文(模板法零 LLM,数字全真)。

红线(share_text 泄漏面):只含对方自己的表现数(视频数/播放/互动/最爆标题/点击/订单),
绝不含内部评分(viltrox_fit_score)、内部字段(match_confidence/stage/ROI/成本/佣金)、
内部备注。本模块任何查询都不 SELECT viltrox_fit_score,不碰 rule_v0。

compat 约定:SQL 占位符用 ?;SQL 禁 percent 字符(不用 LIKE);BOOLEAN 读回可能是 int 1/0,
判断走 _truthy;JSONB/时间戳读回可能是 str,双态容错。函数内懒 import get_conn。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains import business_truth

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"

# share_text 红线词表:内部字段/评分绝不许出现在给 KOL 的文案里(冒烟断言同款口径)。
INTERNAL_LEAK_TERMS: tuple[str, ...] = (
    "viltrox_fit_score", "fit_score", "rule_v0", "match_confidence", "契合分",
    "内部评分", "roi", "cost_cents", "commission", "佣金", "成本", "stage_status",
    "dedupe_key", "kol_pool_id",
)


# ── 小工具(容错;与 signature_profile 同款口径)──────────────────────


from app.core.coerce import _int0, _truthy


def _text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _day(value: Any) -> str | None:
    """时间值(datetime/date/ISO str)→ YYYY-MM-DD;解析不了诚实 None。"""
    iso = _iso(value)
    return iso[:10] if iso and len(iso) >= 10 else None


def _fmt_int(n: int) -> str:
    """人读数字:千分位(share_text 用,数字全真不缩写)。"""
    return f"{int(n):,}"


def _known_metric_sum(rows: list[dict[str, Any]], key: str) -> tuple[int | None, int]:
    values: list[int] = []
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return (sum(values), len(values)) if values else (None, 0)


# ── 数据装载(全只读、守卫读)────────────────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    # 刻意不 SELECT viltrox_fit_score / 内部评估字段:战绩卡零内部评分泄漏面。
    row = conn.execute(
        """
        SELECT id, handle, display_name, platform, country, linked_main_kol_id
        FROM vkpi_kol_pool WHERE id = ?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_evidence_rows(
    conn: Any,
    kol_pool_id: int,
    limit: int = 500,
    *,
    project_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    project_clause = ""
    project_params: list[int] = []
    if project_ids is not None:
        if not project_ids:
            return []
        project_clause = f"AND project_id IN ({','.join('?' for _ in project_ids)})"
        project_params = [int(pid) for pid in project_ids]
    rows = conn.execute(
        f"""
        SELECT
          id AS evidence_id,
          COALESCE(video_title, title, '') AS title,
          content_url,
          platform,
          view_count,
          like_count,
          comment_count,
          COALESCE(published_at_norm, CAST(posted_at AS TIMESTAMPTZ)) AS published_at,
          is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
          {project_clause}
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), *project_params, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


def _deep_analyzed_count(
    conn: Any,
    kol_pool_id: int,
    *,
    project_ids: list[int] | None = None,
) -> int:
    try:
        project_clause = ""
        project_params: list[int] = []
        if project_ids is not None:
            if not project_ids:
                return 0
            project_clause = f"AND e.project_id IN ({','.join('?' for _ in project_ids)})"
            project_params = [int(pid) for pid in project_ids]
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM vkpi_analysis_cache ac
            JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
            WHERE ac.target_type = 'video'
              AND ac.derive_method = ?
              AND ac.status = 'ready'
              AND e.kol_pool_id = ?
              {project_clause}
            """,
            (FINAL_V1_DERIVE_METHOD, int(kol_pool_id), *project_params),
        ).fetchone()
        return _int0(dict(row).get("n")) if row else 0
    except Exception:
        logger.debug("performance_card.deep_count_failed", exc_info=True)
        return 0


# ── 🎬 块一:带品内容 ────────────────────────────────────────────────


def _content_block(
    conn: Any,
    kol_pool_id: int,
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    rows = _load_evidence_rows(conn, kol_pool_id, project_ids=project_ids)
    if not rows:
        return {
            "status": "empty",
            "reason": "该 KOL 暂无 Viltrox 相关视频证据(vkpi_kol_video_evidence 空)。",
            "branded_videos": 0,
            "total_views": 0,
            "total_likes": 0,
            "total_comments": 0,
            "deep_analyzed_count": _deep_analyzed_count(conn, kol_pool_id, project_ids=project_ids),
        }
    total_views, views_known = _known_metric_sum(rows, "view_count")
    total_likes, likes_known = _known_metric_sum(rows, "like_count")
    total_comments, comments_known = _known_metric_sum(rows, "comment_count")
    metric_coverage = {
        "views": {"observed": views_known, "total": len(rows)},
        "likes": {"observed": likes_known, "total": len(rows)},
        "comments": {"observed": comments_known, "total": len(rows)},
    }
    complete = all(item["observed"] == item["total"] for item in metric_coverage.values())
    published = sorted(d for d in (_day(r.get("published_at")) for r in rows) if d)
    top = rows[0]  # 已按 view_count DESC 排好
    top_views = _int0(top.get("view_count"))
    return {
        "status": "ready" if complete else "partial",
        "reason": None if complete else "部分视频播放/互动指标缺失;仅展示已观测值,全缺字段保持 null。",
        "branded_videos": len(rows),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "engagement_total": (
            total_likes + total_comments
            if total_likes is not None and total_comments is not None
            else None
        ),
        "views_sample": sum(1 for r in rows if _int0(r.get("view_count")) > 0),
        "metric_coverage": metric_coverage,
        "deep_analyzed_count": _deep_analyzed_count(conn, kol_pool_id, project_ids=project_ids),
        "first_video_at": published[0] if published else None,
        "last_video_at": published[-1] if published else None,
        "top_video": {
            "title": _text(top.get("title"), 160),
            "content_url": _text(top.get("content_url"), 500),
            "platform": _text(top.get("platform"), 30),
            "view_count": top_views if top_views > 0 else None,
            "published_at": _day(top.get("published_at")),
        },
    }


# ── 🛒 块二:商业转化(三源守卫读,空表诚实 0)────────────────────────


def _goaffpro_block(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    from app.db.connection import table_exists

    if not table_exists("vkpi_goaffpro_kol_metrics"):
        return {"available": False, "clicks": 0, "orders": 0, "gmv_cents": 0, "reason": "goaffpro 表未建"}
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(clicks), 0) AS clicks,
                   COALESCE(SUM(orders), 0) AS orders,
                   COALESCE(SUM(gmv_cents), 0) AS gmv_cents
            FROM vkpi_goaffpro_kol_metrics
            WHERE kol_pool_id = ?
            """,
            (int(kol_pool_id),),
        ).fetchone()
        data = dict(row) if row else {}
        sales = 0
        if table_exists("vkpi_goaffpro_sales"):
            srow = conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_goaffpro_sales WHERE kol_pool_id = ?",
                (int(kol_pool_id),),
            ).fetchone()
            sales = _int0(dict(srow).get("n")) if srow else 0
        return {
            "available": True,
            "clicks": _int0(data.get("clicks")),
            "orders": _int0(data.get("orders")),
            "gmv_cents": _int0(data.get("gmv_cents")),
            "sales_rows": sales,
        }
    except Exception:
        logger.debug("performance_card.goaffpro_failed", exc_info=True)
        return {"available": False, "clicks": 0, "orders": 0, "gmv_cents": 0, "reason": "goaffpro 读取失败"}


def _shopify_block(
    conn: Any,
    linked_main_kol_id: Any,
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Verified Shopify attribution only; raw normalized orders are not business truth."""

    from app.db.connection import table_exists

    main_id = _int0(linked_main_kol_id)
    if main_id <= 0:
        return {
            "available": False,
            "orders": None,
            "gmv_cents": None,
            "reason": "该 KOL 未桥接主档(linked_main_kol_id 空),无法读取 verified Shopify 归因",
        }
    if not table_exists("vkpi_sales_attributions") or not table_exists("vkpi_shopify_order_snapshots"):
        return {
            "available": False,
            "orders": None,
            "gmv_cents": None,
            "reason": "verified Shopify 归因表/签名快照表未建",
        }
    try:
        project_clause = ""
        project_params: list[int] = []
        if project_ids is not None:
            if not project_ids:
                return {"available": False, "orders": None, "gmv_cents": None, "reason": "无可见项目商业归因"}
            project_clause = f"AND s.project_id IN ({','.join('?' for _ in project_ids)})"
            project_params = [int(pid) for pid in project_ids]
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(s.currency, ''), 'USD') AS currency,
                   COUNT(DISTINCT s.shopify_order_snapshot_id) AS orders,
                   COALESCE(SUM(s.revenue_cents), 0) AS gmv_cents
            FROM vkpi_sales_attributions s
            WHERE s.kol_id = ?
              {project_clause}
              AND {business_truth.verified_shopify_attribution_sql('s')}
            GROUP BY COALESCE(NULLIF(s.currency, ''), 'USD')
            """,
            (main_id, *project_params),
        ).fetchall()
        by_currency = [
            {
                "currency": _text(dict(row).get("currency"), 12) or "USD",
                "orders": _int0(dict(row).get("orders")),
                "gmv_cents": _int0(dict(row).get("gmv_cents")),
            }
            for row in rows
        ]
        primary = (
            max(by_currency, key=lambda item: (item["gmv_cents"], item["orders"]))
            if by_currency
            else {"currency": "USD", "orders": 0, "gmv_cents": 0}
        )
        return {
            "available": True,
            "orders": primary["orders"],
            "gmv_cents": primary["gmv_cents"],
            "currency": primary["currency"],
            "by_currency": by_currency,
            "mixed_currency": len(by_currency) > 1,
            "truth_status": "provider_verified_shopify",
        }
    except Exception:
        logger.debug("performance_card.shopify_failed", exc_info=True)
        return {"available": False, "orders": None, "gmv_cents": None, "reason": "verified Shopify 归因读取失败"}


def _short_links_block(
    conn: Any,
    linked_main_kol_id: Any,
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    """自建短链(vkpi_links.kol_id 桥 kols 主档):点击(排 bot)/订单/收入。未桥接诚实说明。"""
    from app.db.connection import table_exists

    main_id = _int0(linked_main_kol_id)
    if main_id <= 0:
        return {"available": False, "links": 0, "clicks": 0, "orders": 0, "reason": "该 KOL 未桥接主档(linked_main_kol_id 空),无自建短链"}
    if not table_exists("vkpi_links"):
        return {"available": False, "links": 0, "clicks": 0, "orders": 0, "reason": "短链表未建"}
    try:
        scoped = project_ids is not None
        if scoped and not project_ids:
            return {"available": False, "links": 0, "clicks": 0, "orders": 0, "reason": "无可见项目短链"}
        placeholders = ",".join("?" for _ in (project_ids or []))
        l_scope = f" AND l.project_id IN ({placeholders})" if scoped else ""
        l2_scope = f" AND l2.project_id IN ({placeholders})" if scoped else ""
        l3_scope = f" AND l3.project_id IN ({placeholders})" if scoped else ""
        scoped_ids = [int(pid) for pid in (project_ids or [])]
        row = conn.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM vkpi_links l WHERE l.kol_id = ?{l_scope}) AS links,
              (SELECT COUNT(*) FROM vkpi_link_clicks ck
                 JOIN vkpi_links l2 ON l2.id = ck.link_id
                 WHERE l2.kol_id = ?{l2_scope} AND COALESCE(ck.is_bot, 0) = 0) AS clicks,
              (SELECT COUNT(*) FROM vkpi_sales_attributions s
                 JOIN vkpi_links l3 ON l3.id = s.link_id
                 WHERE l3.kol_id = ?
                   {l3_scope}
                   AND {business_truth.verified_shopify_attribution_sql('s')}) AS orders
            """,
            (
                main_id, *scoped_ids,
                main_id, *scoped_ids,
                main_id, *scoped_ids,
            ),
        ).fetchone()
        data = dict(row) if row else {}
        return {
            "available": True,
            "links": _int0(data.get("links")),
            "clicks": _int0(data.get("clicks")),
            "orders": _int0(data.get("orders")),
        }
    except Exception:
        logger.debug("performance_card.short_links_failed", exc_info=True)
        return {"available": False, "links": 0, "clicks": 0, "orders": 0, "reason": "短链读取失败"}


def _commerce_block(
    conn: Any,
    kol_pool_id: int,
    linked_main_kol_id: Any,
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    goaffpro = (
        _goaffpro_block(conn, kol_pool_id)
        if project_ids is None
        else {
            "available": False,
            "clicks": None,
            "orders": None,
            "gmv_cents": None,
            "reason": "GoAffPro 聚合无 project_id,普通员工项目 scope 下不可安全分摊。",
        }
    )
    shopify = _shopify_block(conn, linked_main_kol_id, project_ids=project_ids)
    short_links = _short_links_block(conn, linked_main_kol_id, project_ids=project_ids)
    sources = {"goaffpro": goaffpro, "shopify": shopify, "short_links": short_links}
    available_sources = [name for name, block in sources.items() if block.get("available")]
    has_signal = any(
        _int0(block.get(metric)) > 0
        for block in sources.values()
        for metric in ("clicks", "orders", "gmv_cents")
    )
    return {
        "status": "partial" if available_sources else "empty",
        "reason": (
            "各商业源缺少可跨源对齐的 canonical order/click key;仅分源展示,不生成总数。"
            if available_sources
            else "三源(goaffpro/verified Shopify/自建短链)均不可用。"
        ),
        "goaffpro": goaffpro,
        "shopify": shopify,
        "short_links": short_links,
        # Compatibility keys remain, but fail closed instead of pretending that
        # addition is deduplication.  Consumers must use the source blocks.
        "total_clicks": None,
        "total_orders": None,
        "partial": bool(available_sources),
        "has_signal": has_signal,
        "available_sources": available_sources,
        "dedupe_status": "unavailable_no_canonical_cross_source_key",
        "aggregate_usable_for_share": False,
        "note": (
            "三源可能描述同一订单/点击;无 canonical cross-source key 时禁止直加,"
            "仅分源展示且不参与评分。"
        ),
    }


# ── 🗓 块三:合作时间线 ──────────────────────────────────────────────


def _timeline_block(
    conn: Any,
    kol_pool_id: int,
    content: dict[str, Any],
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    from app.db.connection import table_exists

    projects: list[dict[str, Any]] = []
    if table_exists("vkpi_project_kol_assignments"):
        try:
            project_clause = ""
            project_params: list[int] = []
            if project_ids is not None:
                if not project_ids:
                    return {"status": "empty", "reason": "无可见关联项目。", "projects": [], "events": []}
                project_clause = f"AND a.project_id IN ({','.join('?' for _ in project_ids)})"
                project_params = [int(pid) for pid in project_ids]
            rows = conn.execute(
                f"""
                SELECT a.id AS assignment_id, a.project_id, a.stage, a.created_at, a.updated_at,
                       p.project_name, p.product_name
                FROM vkpi_project_kol_assignments a
                JOIN vkpi_projects p ON p.id = a.project_id
                WHERE a.kol_pool_id = ?
                  {project_clause}
                ORDER BY a.created_at ASC
                LIMIT 100
                """,
                (int(kol_pool_id), *project_params),
            ).fetchall()
            for r in rows:
                row = dict(r)
                projects.append(
                    {
                        "project_id": _int0(row.get("project_id")),
                        "project_name": _text(row.get("project_name"), 120),
                        "product_name": _text(row.get("product_name"), 120),
                        "stage": _text(row.get("stage"), 40),
                        "joined_at": _day(row.get("created_at")),
                        "last_update_at": _day(row.get("updated_at")),
                    }
                )
        except Exception:
            logger.debug("performance_card.timeline_failed", exc_info=True)

    events: list[dict[str, Any]] = []
    for p in projects:
        if p.get("joined_at"):
            events.append({"date": p["joined_at"], "type": "project_joined", "label": f"加入项目「{p['project_name'] or p['product_name'] or p['project_id']}」"})
    if content.get("first_video_at"):
        events.append({"date": content["first_video_at"], "type": "first_video", "label": "首条 Viltrox 相关视频发布"})
    if content.get("last_video_at") and content.get("last_video_at") != content.get("first_video_at"):
        events.append({"date": content["last_video_at"], "type": "latest_video", "label": "最新 Viltrox 相关视频发布"})
    events.sort(key=lambda ev: str(ev.get("date") or ""))
    if not projects and not events:
        return {"status": "empty", "reason": "该 KOL 暂无项目派单与内容时间点,时间线无从铺开。", "projects": [], "events": []}
    return {"status": "ready", "projects": projects, "events": events[:30], "project_count": len(projects)}


# ── 💌 块四:share_text(模板法零 LLM,数字全真,零内部字段)────────────


def _share_text(kol: dict[str, Any], content: dict[str, Any], commerce: dict[str, Any], timeline: dict[str, Any]) -> dict[str, str]:
    name = _text(kol.get("display_name") or kol.get("handle"), 80) or "创作者"
    videos = _int0(content.get("branded_videos"))
    views = _int0(content.get("total_views"))
    inter = _int0(content.get("engagement_total"))
    top = content.get("top_video") if isinstance(content.get("top_video"), dict) else {}
    top_title = _text((top or {}).get("title"), 90)
    top_views = _int0((top or {}).get("view_count"))
    aggregate_usable = commerce.get("aggregate_usable_for_share") is True
    clicks = _int0(commerce.get("total_clicks")) if aggregate_usable else 0
    orders = _int0(commerce.get("total_orders")) if aggregate_usable else 0
    first_at = str(content.get("first_video_at") or "")[:7]  # YYYY-MM,足够表达「同行自」
    project_count = _int0(timeline.get("project_count"))

    if videos <= 0:
        zh = (
            f"嗨 {name}!感谢与 VILTROX 同行。我们已经把合作档案建好,期待你的首条内容上线 — "
            "发布后这里会实时汇总你的播放与互动战绩,第一时间回传给你。— VILTROX 团队"
        )
        en = (
            f"Hi {name}! Thank you for partnering with VILTROX. Your collaboration file is ready — "
            "once your first video goes live we will track its views and engagement and share the results with you. — Team VILTROX"
        )
        return {"zh": zh, "en": en}

    zh_parts = [f"嗨 {name}!感谢一路与 VILTROX 同行。你的战绩小结:已发布 {_fmt_int(videos)} 条 VILTROX 相关视频"]
    en_parts = [f"Hi {name}! Thank you for creating with VILTROX. Your impact so far: {_fmt_int(videos)} VILTROX-related videos published"]
    if views > 0:
        zh_parts.append(f",累计播放 {_fmt_int(views)} 次")
        en_parts.append(f", {_fmt_int(views)} total views")
    if inter > 0:
        zh_parts.append(f",收获点赞+评论 {_fmt_int(inter)} 次")
        en_parts.append(f", {_fmt_int(inter)} likes and comments")
    zh_parts.append("。")
    en_parts.append(".")
    if top_title and top_views > 0:
        zh_parts.append(f"其中《{top_title}》表现最亮眼,播放 {_fmt_int(top_views)} 次。")
        en_parts.append(f' Your top video "{top_title}" reached {_fmt_int(top_views)} views.')
    if clicks > 0:
        zh_parts.append(f"你的专属链接已带来 {_fmt_int(clicks)} 次访问。")
        en_parts.append(f" Your links drove {_fmt_int(clicks)} visits.")
    if orders > 0:
        zh_parts.append(f"更有 {_fmt_int(orders)} 笔订单由你的内容促成。")
        en_parts.append(f" {_fmt_int(orders)} orders came through your content.")
    if project_count > 0 and first_at:
        zh_parts.append(f"自 {first_at} 以来我们已合作 {_fmt_int(project_count)} 个项目。")
        en_parts.append(f" Since {first_at} we have worked together on {_fmt_int(project_count)} campaigns.")
    zh_parts.append("期待下一支作品,一起把好内容带给更多创作者!— VILTROX 团队")
    en_parts.append(" We can't wait to see what you create next! — Team VILTROX")
    return {"zh": "".join(zh_parts), "en": "".join(en_parts)}


# ── 主入口 ──────────────────────────────────────────────────────────


def _financial_project_ids(
    conn: Any,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
) -> tuple[bool, bool, list[int]]:
    """Return (scope_available, company_scope, visible associated project ids)."""

    from app.domains.kol import roi_aggregate

    allowed, project_scope_sql, project_scope_params = roi_aggregate._financial_project_scope(staff)
    if not allowed:
        return False, False, []
    company_scope = not bool(project_scope_sql)
    project_scope_clause = f"AND {project_scope_sql}" if project_scope_sql else ""
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT a.project_id
            FROM vkpi_project_kol_assignments a
            JOIN vkpi_projects p ON p.id = a.project_id
            WHERE a.kol_pool_id = ?
              {project_scope_clause}
            ORDER BY a.project_id
            """,
            (int(kol_pool_id), *project_scope_params),
        ).fetchall()
    except Exception:
        logger.debug("performance_card.project_scope_failed", exc_info=True)
        return False, company_scope, []
    return True, company_scope, [int(dict(row)["project_id"]) for row in rows]


def performance_card(
    kol_pool_id: int,
    *,
    conn: Any = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """KOL 战绩卡:结构化 JSON + 双语 share_text;KOL 不存在抛 LookupError(路由转 404)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    scope_available, company_scope, project_ids = _financial_project_ids(db, int(kol_pool_id), staff)
    if not scope_available or (not company_scope and not project_ids):
        raise LookupError(f"kol_pool {kol_pool_id} not found")
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    kol = {
        "handle": _text(pool.get("handle"), 100),
        "display_name": _text(pool.get("display_name"), 100),
        "platform": _text(pool.get("platform"), 30),
        "country": _text(pool.get("country"), 60),
    }
    scoped_project_ids = None if company_scope else project_ids
    content = _content_block(db, int(kol_pool_id), project_ids=scoped_project_ids)
    commerce = _commerce_block(
        db,
        int(kol_pool_id),
        pool.get("linked_main_kol_id"),
        project_ids=scoped_project_ids,
    )
    timeline = _timeline_block(
        db,
        int(kol_pool_id),
        content,
        project_ids=scoped_project_ids,
    )
    share = _share_text(kol, content, commerce, timeline)

    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "kol": kol,
        "content": content,
        "commerce": commerce,
        "timeline": timeline,
        "financial_scope": "company" if company_scope else "visible_projects",
        "share_text": share,
        "share_text_policy": "only-their-own-numbers; template_v1 zero-LLM; 绝不含内部评分/内部字段(见 INTERNAL_LEAK_TERMS)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "纯聚合已有数据(evidence + final_v1 计数 + goaffpro/verified Shopify/短链守卫读);"
            "商业源无跨源 canonical key 时仅分源展示;独立信号不参与 V6 Fit 评分。"
        ),
    }
