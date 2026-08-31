"""MY KOL 看板聚合(my_kol_board_ext · M2)——八组看板读数,一次调用全给齐。

用途:给前端 MY KOL 改版看板(M3/M4)补 KPI 序列 / 履约漏斗 / 平台分布 / fit 直方 /
联系方式覆盖 / 播放榜 / V 相关内容五档 所需的聚合真数据。挂独立端点
GET /api/admin/vkpi/my-kol/board-ext?days=30(与 aggregate 同前缀、同 require_tab
读权限、同 viewer/own-only scope 口径),对现有 aggregate 响应零改动——全部字段
增量新增,绝不破坏既有契约。仿 market/voice_report_ext.py 成熟模式。

八组字段(每组独立 status,单组失败诚实降级 {status:"error"} 不拖累其余):
  kpi_series        ①收藏集粉丝按日 SUM(vkpi_kol_fit_snapshot;快照缺日=null 断点,
                    绝不 0 填冒充掉粉)②新视频/日(vkpi_kol_video_evidence.created_at,
                    计数型 0 填齐)③官号粉丝/官号播放按日 SUM(vkpi_channel_metrics ×
                    vkpi_employee_channels)。各带 delta_pct(同等流逝窗对比,上窗
                    无数 → null)。KOL 内容播放(view_count)无历史快照 → 不给 series
                    不给 delta(诚实缺席),只给 kol_views 点时合计 + 填充率。
  funnel            vkpi_project_kol_assignments × 收藏集;13 真阶段经
                    projects/stage_canonical.to_canonical 归并 8 段展示(存量值
                    arrived 本模块补映射到 delivered;未知值落 other 诚实桶)。
  platform_dist     收藏集 KOL 按平台计数。
  fit_dist          全池 fit 分桶直方(十分位 10 桶 + 「未评分」诚实桶);只 SELECT
                    分桶表达式,绝不返回单 KOL 分数,绝不写 viltrox_fit_score /
                    不碰 rule_v0。
  contact_coverage  收藏集 vkpi_kol_pool_contacts 类型×计数 + 收藏集覆盖率;
                    卡面只出类型计数,绝不返回明文联系方式(contact_value 一列
                    不进 SELECT;明文披露是 contact_reveal 门控端点的事)。
  views_top         收藏集 evidence view_count Top12 KOL 榜(NULL 剔除,is_active
                    IS NOT FALSE;basis=点时实测,非时序)。
  recent_videos     收藏集最近采集视频墙(内容墙,2026-07-12 增量):evidence 最近
                    N 条(封顶 RECENT_VIDEOS_LIMIT,双层封顶)按发布时间降序,
                    带缩略图三件套(创意库同一条毒缓存自愈链 cached → raw →
                    youtube 派生);view_count NULL 原样透出(未实测 ≠ 0 播放)。
                    2026-08 闭环增量(U2/U3/U9,全部可选新增、旧读者不受影响):
                    行级 viltrox_modalities(final_v1 品牌证据 visual/subtitle/audio
                    子集,缺则 [])、tasks{metric_refresh, final_v1} TaskState(复用
                    my_kol_video_recovery.attach_task_states,与 my-kol videos 端点
                    同一实现、同一 evidence 字节一致,含 reason_class)、published_at
                    + keyset 游标 page{limit, returned, has_more, next_cursor,
                    cursor_kind, order}(序 published_at DESC, id DESC;?cursor=
                    只作用于本组,其余七组不受游标影响;无游标=首页,行为不变)。
  v_content         V 相关内容五档互斥证据:cooperation(project_id 有效关联)/
                    analysis_confirmed(latest ready final_v1 的 detected=true 或
                    products 非空)/title_mention(标题含 viltrox/唯卓仕/唯卓)/
                    not_related(latest ready final_v1 explicit false)/undetermined(无强证据)
                    + KOL 级汇总 v_kol_count + 行级名单
                    v_kol_ids(有确定 V 信号的去重 kol_pool_id 升序数组,封顶
                    V_KOL_IDS_MAX=2000,超出如实标注 truncated,前端库列表
                    「有 V 视频」精确过滤用)+ tiers_by_kol(KOL 级去重计数,
                    与条数级 tiers 区分)。v_content/recent_videos/名单共用
                    同一 SQL CTE；只投影 final_v1 的 detected/products 等
                    结构化证据，不下发原始正文/口播/字幕/描述。

scope 口径(与 aggregate/viewer-context 同族):staff_scope_id 为已解析的
effective_staff_id —— 员工恒被 scope.effective_staff_id 压回本人;管理层缺省
None=全团队收藏集(收藏 ∪ 共享 vkpi_kol_pool_members,去重 duplicate_of_id IS
NULL,与 my_kol_aggregate._pool_favorites 同两张表),显式 ?staff_id= 看指定成员。
fit_dist 保留明确的全池只读分桶口径;官号指标仍按成员/管理层团队账号语义聚合。
其余标为 MY KOL/收藏集的内容、播放、榜单、V 名单与联系方式类型都严格使用
收藏 ∪ 授权共享的同一 scope,避免把其他员工收藏混入当前员工页面。

窗口口径:days 天 UTC 日粒度窗 [today-(days-1), today](含今天,右沿钳 now,
绝不产未来日);环比上窗 = 紧前等长 days 天;流量型(新视频)环比两侧同取
已流逝等长时段(current=[since 0 点, now) vs prev=[prev_since 0 点,
prev_since+elapsed)),存量型(粉丝/播放)= 两窗各取最近一个快照日的值对比。

诚实空态:算不出的字段返 null / 空数组,绝不编数;share/coverage 只在分母 > 0
时给,delta_pct 只在上窗 > 0 时给。所有 LIMIT 双层封顶(SQL LIMIT ? + Python
切片)。SQL 常量零字面 percent、零 LIKE(标题命中用 strpos 参数化)、零注释
(compat 把注释里的 ASCII 问号当占位符)。

显示层宪法:返回体绝不含 contact_value / email / consent_basis / 单 KOL
viltrox_fit_score 原值 / avatar / raw_platform_data 等明文联系方式与内部字段
(SELECT 列白名单 + 契约测试静态审查双保险)。

红线:纯读聚合,零写库、零 LLM、零外调;零写 viltrox_fit_score、不碰 rule_v0
(fit_dist 只读分桶展示,不产生任何评分,不回写任何打分链路)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol import my_kol_video_recovery as recovery
from app.domains.kol import my_kol_recent_filters as recent_filters
from app.domains.kol import my_kol_recent_dedupe as recent_dedupe
from app.domains.kol.video_evidence_projection import viltrox_modalities
from app.domains.kol.my_kol_board_recent_projection import project_recent_video
from app.domains.projects import stage_canonical

logger = get_logger(__name__)

# 常量拆分伴随文件(600 行红线):护栏常量 + SQL 常量全在 my_kol_board_ext_sql,
# 此处显式引入并保持本模块属性可达(契约测试/复用方均经本模块引用)。
from app.domains.kol.my_kol_board_ext_sql import (  # noqa: F401 — 契约测试经本模块引用
    BOARD_METHOD,
    CONTACT_COVERAGE_SQL,
    CONTACT_TYPE_MAX_ITEMS,
    CONTACT_TYPE_ROWS_LIMIT,
    CONTACT_TYPES_SQL,
    EXTRA_RAW_TO_CANONICAL as _EXTRA_RAW_TO_CANONICAL,
    FIT_BUCKET_ROWS_LIMIT,
    FIT_DIST_SQL,
    FUNNEL_ROWS_LIMIT,
    FUNNEL_SEGMENTS,
    FUNNEL_SQL,
    KOL_VIEWS_CURRENT_SQL,
    NEW_VIDEOS_COUNT_SQL,
    NEW_VIDEOS_DAY_SQL,
    OFFICIAL_DAY_SQL,
    PLATFORM_DIST_SQL,
    PLATFORM_MAX_ITEMS,
    PLATFORM_ROWS_LIMIT,
    POOL_FOLLOWERS_DAY_SQL,
    RECENT_KEYSET_PARAM_COUNT,
    RECENT_FILTER_PARAM_COUNT,
    RECENT_VIDEOS_LIMIT,
    RECENT_VIDEOS_SQL,
    SERIES_MAX_DAYS,
    SERIES_ROWS_LIMIT,
    V_CONTENT_CLASSIFIED_CTE,
    V_CONTENT_SUMMARY_MATERIALIZED_SQL,
    V_CONTENT_SUMMARY_SQL,
    V_KOL_IDS_MAX,
    V_KOL_IDS_ROWS_LIMIT,
    VIEWS_TOP_LIMIT,
    VIEWS_TOP_SQL,
    VILTROX_TOKEN,
    VILTROX_TITLE_TOKENS,
)


# ── 小工具 ──────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    """当前 UTC 时刻(独立缝:契约测试 monkeypatch 冻结 now)。"""
    return datetime.now(timezone.utc)


def _int0(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _day_str(value: Any) -> str:
    """DB 读回的日期(date / datetime / str)→ 'YYYY-MM-DD';解不了返回 ''(诚实丢弃)。"""
    if isinstance(value, datetime):        # datetime 是 date 子类,必须先判
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _ts_str(value: Any) -> str:
    """DB 读回的时间戳(datetime / str)→ ISO 字符串;空值返回 ''(诚实缺席)。"""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "").strip()


def _truthy_db(value: Any) -> bool:
    """compat BOOLEAN 读回可能是 True / 1 / 't'(BOOLEAN 读回 int 已知陷阱)——统一容错判真。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("t", "true", "1", "y", "yes")


def _share(part: int, total: int) -> float | None:
    """占比;分母为 0 → null(诚实空态,绝不编 0% 或 100%)。"""
    if total <= 0:
        return None
    return round(part / total, 4)


def _delta_pct(current: int | None, previous: int | None) -> float | None:
    """环比百分比;本窗/上窗缺数据或上窗为 0 → null(前端诚实省略药丸)。"""
    if current is None or previous is None or previous <= 0:
        return None
    return round((current - previous) * 100.0 / previous, 1)


def _scope_params(staff_scope_id: int) -> tuple[int, int, int, int]:
    """_COLLECTION_COND 的 4 个占位参;0=管理层全团队收藏集,否则按该 staff 过滤。"""
    sid = _int0(staff_scope_id)
    return (sid, sid, sid, sid)


def _collection_scope_diagnostics(staff_scope_id: int) -> dict[str, Any]:
    """Expose the exact MY KOL row-scope used by a response block."""

    sid = _int0(staff_scope_id)
    return {
        "mode": "staff_collection" if sid else "team_collection",
        "staff_scope_id": sid or None,
        "membership": "favorite_or_authorized_share",
    }


def _windows(days: int) -> dict[str, Any]:
    """日粒度窗口 [today-(days-1), today](右沿=今天,绝不产未来日)+ 紧前等长上窗。

    流量型环比用时间戳窗:current=[since 0 点, now) 实际流逝段,prev=上窗起点 0 点 +
    同等流逝长度——两侧等长,部分日绝不比整日(环比失真防线)。evidence.created_at
    为无时区列(库内约定 UTC),参数用 naive ISO。
    """
    now = _now_utc()
    naive_now = now.replace(tzinfo=None)
    today = now.date()
    since_d = today - timedelta(days=days - 1)
    prev_until_d = since_d - timedelta(days=1)
    prev_since_d = since_d - timedelta(days=days)
    since_ts = datetime(since_d.year, since_d.month, since_d.day)
    elapsed = naive_now - since_ts
    prev_since_ts = since_ts - timedelta(days=days)
    return {
        "since_d": since_d,
        "until_d": today,
        "prev_since_d": prev_since_d,
        "prev_until_d": prev_until_d,
        "since_ts": since_ts.isoformat(),
        "until_ts": naive_now.isoformat(),
        "prev_since_ts": prev_since_ts.isoformat(),
        "prev_until_ts": (prev_since_ts + elapsed).isoformat(),
    }


def _day_axis(since_d: date, until_d: date) -> list[str]:
    """[since_d, until_d] 连续 UTC 日轴(封顶 SERIES_MAX_DAYS;右沿=今天,零未来日)。"""
    out: list[str] = []
    cursor = since_d
    while cursor <= until_d and len(out) < SERIES_MAX_DAYS:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _day_map(conn: Any, sql: str, params: tuple, value_key: str) -> dict[str, int]:
    """按日聚合 SQL → {'YYYY-MM-DD': value};行数 SQL/Python 双封顶。"""
    rows = conn.execute(sql, params).fetchall()
    out: dict[str, int] = {}
    for r in list(rows)[:SERIES_ROWS_LIMIT]:
        rec = dict(r)
        day = _day_str(rec.get("day"))
        if day:
            out[day] = _int0(rec.get(value_key))
    return out


def _latest_value(day_map: dict[str, int]) -> int | None:
    """存量型序列取窗内最近一个快照日的值;窗内无快照 → None(诚实缺席)。"""
    if not day_map:
        return None
    return day_map[max(day_map.keys())]


# ── V 相关内容五档判定(与 SQL CTE 优先级对齐)────────────────────────


def _explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _positive_products(value: Any) -> bool:
    return isinstance(value, (list, tuple, set)) and len(value) > 0


def classify_v_content(
    project_id: Any,
    title_text: Any,
    *,
    final_v1_brand_status: Any = None,
    final_v1_detected: Any = None,
    final_v1_products: Any = None,
) -> str:
    """单条 evidence 五档互斥判定；优先级与 V_CONTENT_CLASSIFIED_CTE 相同。

    brand_status 是新 final_v1 的受控 tri-state 真值，优先于 legacy
    detected/products。unknown 不得因 legacy false 落入 not_related；标题仍只做
    中等证据。纯函数零 SQL，不接收也不返回原始深析全文。
    """
    pid = str(project_id).strip() if project_id is not None else ""
    if pid and pid != "0":
        return "cooperation"
    brand_status = str(final_v1_brand_status or "").strip().lower()
    if brand_status not in {"present", "absent", "unknown"}:
        brand_status = ""
    detected = _explicit_bool(final_v1_detected)
    if brand_status == "present":
        return "analysis_confirmed"
    if not brand_status and (detected is True or _positive_products(final_v1_products)):
        return "analysis_confirmed"
    title = str(title_text or "").lower()
    if any(token in title for token in VILTROX_TITLE_TOKENS):
        return "title_mention"
    if brand_status == "absent":
        return "not_related"
    if brand_status == "unknown":
        return "undetermined"
    if detected is False:
        return "not_related"
    return "undetermined"


# ── 七组字段构建器(每组单一职责;真实表名写进 basis)─────────────────────


def _stock_series(axis: list[str], day_map: dict[str, int]) -> list[dict[str, Any]]:
    """存量型(粉丝/累计播放)日序列:缺快照日 value=null 断点,绝不 0 填冒充清零。"""
    return [{"date": d, "value": day_map.get(d)} for d in axis]


def _kpi_series(conn: Any, sid: int, win: dict[str, Any]) -> dict[str, Any]:
    """1. kpi_series:三条真时序 + KOL 播放点时读数(无时序诚实缺席)。"""
    axis = _day_axis(win["since_d"], win["until_d"])
    scope4 = _scope_params(sid)
    since_s, until_s = win["since_d"].isoformat(), win["until_d"].isoformat()
    prev_since_s, prev_until_s = win["prev_since_d"].isoformat(), win["prev_until_d"].isoformat()

    # ① 收藏集粉丝按日 SUM(存量型;快照缺日=null 断点)
    pool_cur = _day_map(conn, POOL_FOLLOWERS_DAY_SQL,
                        (*scope4, since_s, until_s, SERIES_ROWS_LIMIT), "total")
    pool_prev = _day_map(conn, POOL_FOLLOWERS_DAY_SQL,
                         (*scope4, prev_since_s, prev_until_s, SERIES_ROWS_LIMIT), "total")

    # ② 新视频/日(流量型;0 填齐;环比两侧同取已流逝等长时段)
    videos_map = _day_map(conn, NEW_VIDEOS_DAY_SQL,
                          (*scope4, win["since_ts"], win["until_ts"], SERIES_ROWS_LIMIT), "n")
    videos_cur = _int0(dict(conn.execute(
        NEW_VIDEOS_COUNT_SQL, (*scope4, win["since_ts"], win["until_ts"])
    ).fetchone() or {}).get("n"))
    videos_prev = _int0(dict(conn.execute(
        NEW_VIDEOS_COUNT_SQL, (*scope4, win["prev_since_ts"], win["prev_until_ts"])
    ).fetchone() or {}).get("n"))

    # ③ 官号粉丝 + 官号播放按日 SUM(一次查询两条序列;存量型)
    def _official_maps(d_since: str, d_until: str) -> tuple[dict[str, int], dict[str, int]]:
        rows = conn.execute(OFFICIAL_DAY_SQL, (sid, sid, d_since, d_until, SERIES_ROWS_LIMIT)).fetchall()
        followers: dict[str, int] = {}
        views: dict[str, int] = {}
        for r in list(rows)[:SERIES_ROWS_LIMIT]:
            rec = dict(r)
            day = _day_str(rec.get("day"))
            if day:
                followers[day] = _int0(rec.get("followers"))
                views[day] = _int0(rec.get("views"))
        return followers, views

    off_f_cur, off_v_cur = _official_maps(since_s, until_s)
    off_f_prev, off_v_prev = _official_maps(prev_since_s, prev_until_s)

    def _stock_metric(cur_map: dict[str, int], prev_map: dict[str, int], table: str) -> dict[str, Any]:
        current, previous = _latest_value(cur_map), _latest_value(prev_map)
        return {"current": current, "previous": previous,
                "delta_pct": _delta_pct(current, previous), "table": table}

    # KOL 内容播放:点时实测无时序 → 不给 series 不给 delta,只给合计 + 填充率
    kv = dict(conn.execute(KOL_VIEWS_CURRENT_SQL, scope4).fetchone() or {})
    kv_total, kv_measured = _int0(kv.get("total_evidence")), _int0(kv.get("measured"))
    kol_views = {
        "status": "point_in_time",
        "views_total": _int0(kv.get("views_total")),
        "measured": kv_measured,
        "evidence_total": kv_total,
        "fill_rate": _share(kv_measured, kv_total),
        "scope": _collection_scope_diagnostics(sid),
        "basis": (
            "vkpi_kol_video_evidence × 收藏集(收藏 ∪ 授权共享,去重)的 view_count 点时实测"
            "(抓取时刻读数),仅计 is_active IS NOT FALSE;无历史快照 → 无时序无环比"
            "(诚实缺席,绝不编 series);填充率=view_count 非空行/收藏集有效 evidence 行"
        ),
    }

    return {
        "status": "ready" if axis else "empty",
        "granularity": "day",
        "series": {
            "pool_followers": _stock_series(axis, pool_cur),
            "new_videos": [{"date": d, "count": videos_map.get(d, 0)} for d in axis],
            "official_followers": _stock_series(axis, off_f_cur),
            "official_views": _stock_series(axis, off_v_cur),
        },
        "metrics": {
            "pool_followers": _stock_metric(pool_cur, pool_prev, "vkpi_kol_fit_snapshot"),
            "new_videos": {"current": videos_cur, "previous": videos_prev,
                           "delta_pct": _delta_pct(videos_cur, videos_prev),
                           "table": "vkpi_kol_video_evidence"},
            "official_followers": _stock_metric(off_f_cur, off_f_prev, "vkpi_channel_metrics"),
            "official_views": _stock_metric(off_v_cur, off_v_prev, "vkpi_channel_metrics"),
        },
        "kol_views": kol_views,
        "basis": {
            "pool_followers": (
                "vkpi_kol_fit_snapshot 收藏集(收藏 ∪ 共享)按 snapshot_date SUM(followers);"
                "存量型:快照缺日 value=null 如实断点(06-16 起有缺日),绝不 0 填;"
                "环比=本窗与上一等长窗各取最近快照日对比,上窗无快照 → delta_pct=null;"
                "只读 snapshot_date/followers 两列,零触 fit_score"
            ),
            "new_videos": (
                "vkpi_kol_video_evidence(收藏集)按 created_at 的 UTC 日计数;流量型 0 填齐"
                "(今天为进行中日);环比两侧同取已流逝等长时段,上窗 0 → delta_pct=null"
            ),
            "official": (
                "vkpi_channel_metrics JOIN vkpi_employee_channels(deleted_at IS NULL;"
                "管理层全量口径 staff 参数=0,按人看时按 staff_id 过滤)按 snapshot_date "
                "SUM(followers)/SUM(total_views);存量型缺日=null 断点,环比取最近快照日"
            ),
        },
    }


def _funnel(conn: Any, sid: int) -> dict[str, Any]:
    """2. funnel:收藏集 × assignments,13 真阶段经 stage_canonical 归并 8 段展示。"""
    rows = conn.execute(FUNNEL_SQL, (*_scope_params(sid), FUNNEL_ROWS_LIMIT)).fetchall()
    segments: dict[str, dict[str, Any]] = {
        seg: {"stage": seg, "label": stage_canonical.stage_label_zh(seg),
              "count": 0, "raw_stages": []}
        for seg in FUNNEL_SEGMENTS
    }
    other: dict[str, int] = {}
    for r in list(rows)[:FUNNEL_ROWS_LIMIT]:
        rec = dict(r)
        raw = str(rec.get("stage") or "").strip().lower()
        n = _int0(rec.get("n"))
        canon = _EXTRA_RAW_TO_CANONICAL.get(raw) or stage_canonical.to_canonical(raw)
        if canon in segments:
            segments[canon]["count"] += n
            segments[canon]["raw_stages"].append(raw)
        else:
            key = canon or "unknown"
            other[key] = other.get(key, 0) + n
    for seg in segments.values():
        seg["raw_stages"] = sorted(seg["raw_stages"])
    items = [segments[seg] for seg in FUNNEL_SEGMENTS]
    other_items = [{"stage": k, "count": v} for k, v in sorted(other.items())]
    total = sum(s["count"] for s in items) + sum(o["count"] for o in other_items)
    body: dict[str, Any] = {
        "status": "ready" if total else "empty",
        "total": total,
        "items": items,
        "other": other_items,
        "basis": (
            "vkpi_project_kol_assignments × 收藏集(收藏 ∪ 共享,去重)按 stage 行计数;"
            "13 真阶段经 projects/stage_canonical.to_canonical 归并 8 段展示"
            "(存量值 arrived 本模块读侧补映射 delivered,不改存储);"
            "未知新值落 other 诚实桶,绝不吞行"
        ),
    }
    if not total:
        body["reason"] = "收藏集内无任何项目 KOL 指派行——漏斗诚实空,不编阶段数。"
    return body


def _platform_dist(conn: Any, sid: int) -> dict[str, Any]:
    """3. platform_dist:收藏集 KOL 按平台计数。"""
    rows = conn.execute(PLATFORM_DIST_SQL, (*_scope_params(sid), PLATFORM_ROWS_LIMIT)).fetchall()
    items = [
        {"platform": str(dict(r).get("platform") or "") or "unknown",
         "count": _int0(dict(r).get("n"))}
        for r in list(rows)[:PLATFORM_MAX_ITEMS]
    ]
    return {
        "status": "ready" if items else "empty",
        "items": items,
        "total": sum(i["count"] for i in items),
        "basis": "vkpi_kol_pool 收藏集(收藏 ∪ 共享,duplicate_of_id IS NULL)GROUP BY platform",
    }


def _fit_bucket_label(bucket: int) -> str:
    return "90-100" if bucket >= 9 else f"{bucket * 10}-{bucket * 10 + 9}"


def _fit_dist(conn: Any) -> dict[str, Any]:
    """4. fit_dist:全池 fit 十分位直方 + 「未评分」诚实桶;绝不出单 KOL 分数。"""
    rows = conn.execute(FIT_DIST_SQL, (FIT_BUCKET_ROWS_LIMIT,)).fetchall()
    counts: dict[int, int] = {}
    unscored = 0
    for r in list(rows)[:FIT_BUCKET_ROWS_LIMIT]:
        rec = dict(r)
        bucket = rec.get("bucket")
        if bucket is None:
            unscored += _int0(rec.get("n"))
            continue
        b = max(0, min(_int0(bucket), 9))
        counts[b] = counts.get(b, 0) + _int0(rec.get("n"))
    buckets = [
        {"bucket": _fit_bucket_label(b), "min": b * 10,
         "max": 100 if b >= 9 else b * 10 + 9, "count": counts.get(b, 0)}
        for b in range(10)
    ]
    scored_total = sum(counts.values())
    return {
        "status": "ready" if (scored_total or unscored) else "empty",
        "buckets": buckets,
        "unscored": unscored,
        "scored_total": scored_total,
        "total": scored_total + unscored,
        "basis": (
            "vkpi_kol_pool 全池口径(duplicate_of_id IS NULL)按 fit 分十分位分桶;"
            "只 SELECT 分桶表达式做直方展示,绝不返回单 KOL 分数、绝不写 fit 列、不碰 rule_v0;"
            "「未评分」= fit 分为空的诚实桶"
        ),
    }


def _contact_coverage(conn: Any, sid: int) -> dict[str, Any]:
    """5. contact_coverage:收藏集类型计数 + 覆盖率;绝不返回明文联系方式。"""
    scope4 = _scope_params(sid)
    rows = conn.execute(CONTACT_TYPES_SQL, (*scope4, CONTACT_TYPE_ROWS_LIMIT)).fetchall()
    types = [
        {"contact_type": str(dict(r).get("contact_type") or "") or "unknown",
         "count": _int0(dict(r).get("n"))}
        for r in list(rows)[:CONTACT_TYPE_MAX_ITEMS]
    ]
    cov = dict(conn.execute(CONTACT_COVERAGE_SQL, _scope_params(sid)).fetchone() or {})
    total, covered = _int0(cov.get("total")), _int0(cov.get("covered"))
    return {
        "status": "ready" if (types or total) else "empty",
        "types": types,
        "covered": covered,
        "total": total,
        "coverage": _share(covered, total),
        "scope": _collection_scope_diagnostics(sid),
        "basis": (
            "类型计数=vkpi_kol_pool_contacts × 收藏集(收藏 ∪ 授权共享,去重)"
            "GROUP BY contact_type(明文值一列不进 SELECT,明文披露走 contact_reveal 门控端点);"
            "覆盖率=同一收藏集内"
            "至少有一条联系方式的 KOL 数/收藏集 KOL 数,分母 0 → null"
        ),
    }


def _views_top(conn: Any, sid: int) -> dict[str, Any]:
    """6. views_top:收藏集 evidence 实测播放 Top12(NULL 剔除;点时非时序)。"""
    rows = conn.execute(VIEWS_TOP_SQL, (*_scope_params(sid), VIEWS_TOP_LIMIT)).fetchall()
    items = []
    for r in list(rows)[:VIEWS_TOP_LIMIT]:
        rec = dict(r)
        items.append({
            "kol_pool_id": _int0(rec.get("kol_pool_id")),
            "display_name": str(rec.get("display_name") or ""),
            "handle": str(rec.get("handle") or ""),
            "platform": str(rec.get("platform") or ""),
            "total_views": _int0(rec.get("total_views")),
            "video_count": _int0(rec.get("video_count")),
        })
    body: dict[str, Any] = {
        "status": "ready" if items else "empty",
        "items": items,
        "scope": _collection_scope_diagnostics(sid),
        "basis": (
            "vkpi_kol_video_evidence × 收藏集(收藏 ∪ 授权共享,去重)的 view_count "
            "点时实测(抓取时刻读数,非时序)按 KOL "
            "SUM;view_count IS NULL 剔除(未实测不等于 0 播放);is_active IS NOT FALSE"
            "(归属纠错下线的 evidence 不计入,与百家饭同口径)"
        ),
    }
    if not items:
        body["reason"] = "无任何带实测 view_count 的 evidence——播放榜诚实空,不编播放数。"
    return body


_recent_keyset_params = recent_filters.recent_keyset_params


def _recent_filter_params(days: int = 0, kol_pool_id: int = 0, since: datetime | None = None) -> tuple[Any, ...]:
    return recent_filters.recent_filter_params(_now_utc(), days, kol_pool_id, since)


def _recent_videos(
    conn: Any,
    sid: int,
    *,
    before: tuple[str | None, int] | None = None,
    days: int = 0, kol_pool_id: int = 0, since: datetime | None = None,
) -> dict[str, Any]:
    """收藏集内容墙；按发布时间游标分页并投影五档证据、任务态和缩略图。

    URL 变体读端折叠；view_count 的 NULL、列白名单及零 LLM/零写边界不变。
    """
    from app.domains.content.creative_segments import _thumbnail_fields

    filter_params = _recent_filter_params(days, kol_pool_id, since)
    def fetch_rows(raw_before: tuple[str | None, int] | None, limit: int) -> list[dict[str, Any]]:
        fetched = conn.execute(
            RECENT_VIDEOS_SQL,
            (
                *VILTROX_TITLE_TOKENS,
                *_scope_params(sid),
                *filter_params,
                *_recent_keyset_params(raw_before),
                limit,
            ),
        ).fetchall()
        return [dict(row) for row in fetched]

    rows, has_more, folded_rows = recent_dedupe.canonical_page(
        fetch_rows,
        before=before,
        limit=RECENT_VIDEOS_LIMIT,
    )
    items = [
        project_recent_video(
            rec,
            int_value=_int0,
            day_value=_day_str,
            timestamp_value=_ts_str,
            truthy_value=_truthy_db,
            classify_content=classify_v_content,
            modality_values=viltrox_modalities,
            thumbnail_fields=_thumbnail_fields,
        )
        for rec in rows
    ]
    # 任务态统一投影(与 my-kol videos 端点同一实现);modality 以本组 CTE 投影为准,
    # attach 不再二次批查 analysis cache,只补 tasks / 规整 published_at。
    recovery.attach_task_states(conn, items, fetch_metric_trends=True, fetch_modalities=False)
    last = items[-1] if items else None
    next_cursor = (
        recovery.encode_cursor(last.get("published_at"), last["evidence_id"])
        if has_more and last and last["evidence_id"] > 0
        else None
    )
    body: dict[str, Any] = {
        "status": "ready" if items else "empty",
        "items": items,
        "limit": RECENT_VIDEOS_LIMIT,
        "filters": recent_filters.recent_filter_payload(days, kol_pool_id, filter_params[0]),
        "page": {
            "limit": RECENT_VIDEOS_LIMIT,
            "returned": len(items),
            "has_more": bool(has_more),
            "next_cursor": next_cursor,
            "cursor_kind": recovery.CURSOR_KIND,
            "order": recovery.ORDER,
        },
        "dedupe": {
            "key": "kol_pool_id+video_url_identity(platform,video_id)",
            "folded_rows_observed": folded_rows,
        },
        "basis": (
            "vkpi_kol_video_evidence × 收藏集(收藏 ∪ 共享,去重;is_active IS NOT FALSE,"
            "video/image 两类)按 COALESCE(publish_date, posted_at, created_at) 降序取最近 "
            f"{RECENT_VIDEOS_LIMIT} 条;view_count 点时实测 NULL 原样透出(未实测 ≠ 0 播放);"
            "缩略图三件套=创意库同一条毒缓存自愈链(cached → raw → youtube 派生,"
            "失败占位不算真图);V 五档与v_content共用同一 CTE:有效 project_id 关联 > "
            "latest ready final_v1 detected/products > 中英文标题词(viltrox/唯卓仕/唯卓) > "
            "explicit false 非相关 > 未判定;只下发结构化证据，不下发深析原文;"
            "viltrox_modalities=final_v1 brand_product_evidence.viltrox_evidence[].modality 的 "
            "visual/subtitle/audio 固定序子集(metadata 不算,缺则 []);tasks=apify_jobs 最新"
            "一条 × 持久化产物(与 my-kol videos 端点同一投影函数,含 reason_class 闭集);"
            "canonical 去重=同一 KOL 内按 backend video_url_identity(platform,video_id) "
            "保留排序最新行(不改写存储);page=keyset 游标 "
            "(published_at DESC, id DESC),?cursor= 只翻本组"
        ),
    }
    if not items:
        body["reason"] = (
            "收藏集内零 evidence——内容墙诚实空,不摆假卡。"
            if before is None
            else "游标之后再无 evidence——已到末页。"
        )
    return body


def _v_content(conn: Any, sid: int) -> dict[str, Any]:
    """7. v_content:收藏集五档证据 + 相关 KOL 汇总/名单(同一 CTE)。"""
    scoped_tokens = (*VILTROX_TITLE_TOKENS, *_scope_params(sid))
    sql = (
        V_CONTENT_SUMMARY_MATERIALIZED_SQL
        if conn.__class__.__name__ == "PostgresCompatConnection"
        else V_CONTENT_SUMMARY_SQL
    )
    rows = conn.execute(sql, (*scoped_tokens, V_KOL_IDS_ROWS_LIMIT)).fetchall()
    row = dict(rows[0]) if rows else {}
    total = _int0(row.get("total_evidence"))
    cooperation = _int0(row.get("cooperation"))
    analysis_confirmed = _int0(row.get("analysis_confirmed"))
    title_mention = _int0(row.get("title_mention"))
    not_related = _int0(row.get("not_related"))
    undetermined = _int0(row.get("undetermined"))
    v_related_evidence = cooperation + analysis_confirmed + title_mention
    # 行级名单:SQL DISTINCT+升序+LIMIT 多取 1 行,Python 去重二保险 + 切片封顶;
    # 是否截断以「真取回超上限」如实判定,绝不拿 v_kol_count 反推。
    seen: set[int] = set()
    for r in list(rows)[:V_KOL_IDS_ROWS_LIMIT]:
        kid = _int0(dict(r).get("kol_pool_id"))
        if kid > 0:
            seen.add(kid)
    ordered_ids = sorted(seen)
    ids_truncated = len(ordered_ids) > V_KOL_IDS_MAX
    v_kol_ids = ordered_ids[:V_KOL_IDS_MAX]

    body: dict[str, Any] = {
        "status": "ready" if total else "empty",
        "total_evidence": total,
        "v_related_evidence": v_related_evidence,
        "v_kol_count": _int0(row.get("v_kol_count")),
        "v_kol_ids": v_kol_ids,
        "v_kol_ids_truncated": ids_truncated,
        "scope": _collection_scope_diagnostics(sid),
        "tiers": {
            "cooperation": cooperation,
            "analysis_confirmed": analysis_confirmed,
            "title_mention": title_mention,
            "not_related": not_related,
            "undetermined": undetermined,
        },
        "tiers_by_kol": {
            "cooperation_kols": _int0(row.get("cooperation_kols")),
            "analysis_confirmed_kols": _int0(row.get("analysis_confirmed_kols")),
            "title_mention_kols": _int0(row.get("title_mention_kols")),
            "not_related_kols": _int0(row.get("not_related_kols")),
            "undetermined_kols": _int0(row.get("undetermined_kols")),
        },
        "basis": (
            "范围=收藏集(收藏 ∪ 授权共享,duplicate_of_id IS NULL);五档互斥优先级:"
            "cooperation=evidence.project_id 字符串化后非空且非0;"
            "analysis_confirmed=latest ready video_analysis_final_v1(id DESC) 的 "
            "raw_gemini_video.viltrox_detected=true 或 viltrox_products_all 非空数组;"
            "title_mention=标题(video_title/title)含 viltrox/唯卓仕/唯卓(参数化,中等证据);"
            "not_related=latest ready final_v1 explicit detected=false 且前三档均未命中;"
            "undetermined=结构化证据未知;v_kol_count=至少一条前三档的去重 KOL 数;"
            "v_kol_ids=同判据去重 kol_pool_id 升序名单(封顶 2000,超出 truncated 如实"
            "标注,前端库列表「有 V 视频」精确过滤用);tiers_by_kol=KOL 级去重计数"
            "(同一 KOL 在不同 evidence 档可重复计,与条数级 tiers 区分);"
            "v_content/recent_videos/v_kol_ids/v_kol_count/v_tiers 共用同一 CTE;"
            "只下发结构化 detected/products/competitor_mentions，不返回原始正文/口播/字幕/描述"
        ),
    }
    if ids_truncated:
        body["v_kol_ids_note"] = (
            f"V 信号 KOL 超过名单封顶 {V_KOL_IDS_MAX},v_kol_ids 只含 id 升序前 "
            f"{V_KOL_IDS_MAX} 个;完整去重总数见 v_kol_count，请勿将返回名单当作全部记录。"
        )
    if not total:
        body["reason"] = "evidence 表零行——V 相关内容诚实空。"
    return body


# ── 主入口 ──────────────────────────────────────────────────────────────


def build_recent_videos_page(
    conn: Any = None,
    *,
    staff_scope_id: int | None = None,
    before: tuple[str | None, int] | None = None,
    days: int = 0, kol_pool_id: int = 0, since: datetime | None = None,
) -> dict[str, Any]:
    """只翻 recent_videos 一组(前端「加载更多」用,不重算其余七组)。
    返回体 = recent_videos 组 + 同款信封字段(status/staff_scope_id/method/
    generated_at);失败诚实 {status:'error'} 不抛。
    """
    from app.db.connection import get_conn

    db = conn if conn is not None else get_conn()
    sid = _int0(staff_scope_id)
    try:
        body = _recent_videos(db, sid, before=before, days=days, kol_pool_id=kol_pool_id, since=since)
    except Exception as exc:  # noqa: BLE001 — 单组失败诚实降级,同 build_board_ext 口径
        logger.warning("my_kol_board_ext recent_videos page failed: %s", exc)
        body = {"status": "error", "reason": str(exc)[:200], "items": []}
    return {
        **body,
        "staff_scope_id": sid or None,
        "method": BOARD_METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_board_ext(
    conn: Any = None,
    *,
    staff_scope_id: int | None = None,
    days: int = 30,
    recent_videos_before: tuple[str | None, int] | None = None,
) -> dict[str, Any]:
    """八组看板字段 + 窗口信封;单组失败降级 {status:'error'} 不拖全响应。
    staff_scope_id=已解析 scope(路由 scope.effective_staff_id 产物):None=管理层
    全团队收藏集口径,>0=该成员收藏集(员工恒被压回本人)。纯读;conn 可注入
    (测试),缺省懒取 get_conn(get_conn 非上下文管理器,直接调用)。
    recent_videos_before=已解码 keyset 游标,只作用于 recent_videos 组(None=首页)。
    """
    from app.db.connection import get_conn

    db = conn if conn is not None else get_conn()
    days = max(1, min(_int0(days, 30) or 30, 365))
    sid = _int0(staff_scope_id)
    win = _windows(days)

    builders: tuple[tuple[str, Any], ...] = (
        ("kpi_series", lambda: _kpi_series(db, sid, win)),
        ("funnel", lambda: _funnel(db, sid)),
        ("platform_dist", lambda: _platform_dist(db, sid)),
        ("fit_dist", lambda: _fit_dist(db)),
        ("contact_coverage", lambda: _contact_coverage(db, sid)),
        ("views_top", lambda: _views_top(db, sid)),
        ("recent_videos", lambda: _recent_videos(db, sid, before=recent_videos_before)),
        ("v_content", lambda: _v_content(db, sid)),
    )
    out: dict[str, Any] = {}
    for name, build in builders:
        try:
            out[name] = build()
        except Exception as exc:  # noqa: BLE001 — 单组失败诚实降级,不拖累其余各组
            logger.warning("my_kol_board_ext block %s failed: %s", name, exc)
            out[name] = {"status": "error", "reason": str(exc)[:200]}

    return {
        "status": "ready",
        "days": days,
        "window": {
            "since": win["since_d"].isoformat(),
            "until": win["until_d"].isoformat(),
            "prev_since": win["prev_since_d"].isoformat(),
            "prev_until": win["prev_until_d"].isoformat(),
        },
        "staff_scope_id": sid or None,
        **out,
        "method": BOARD_METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
