"""GTM 总脑页 summary 域(GTM-1 W2)——五卡纯读聚合,一次请求供前端整页渲染。

规格(docs/vkpi_GTM-1_总脑页面与纯读Preview实施规格.md 第二章 A):
  weekly_signals        ← brand_pulse + category_tracks + market_voice(全内部信号,
                          sources_note 诚实标注「外部雷达 GTM-5 待接」)
  product_opportunities ← category_tracks top 机会 + sku_performance + product persona
  recommended_actions   ← action inbox suggested + gifted_funnel 超期 + needs-analysis(全只读)
  strategy_defaults     ← StrategySimPanel 入口默认值(sku_hint/budget_hint,不跑模拟)
  learning_digest       ← prediction_ledger + weekly_scorecard + miss_review(+ 段级创意
                          资产高表现拍法作 effective_styles 佐证),带 honesty_note

显示层宪法(蓝图 v0.2 第五章):只出结论/信号/行动,绝不泄漏 private 字段——
评分明细、原始评论大段(quotes 一律不透传)、竞品笔记(competitor_example 不出)、
KOL 黑名单(风险只出标签)。每段独立 try/except,单段失败 {status:"error"} 不拖垮整卡。

红线:零写库、零 LLM、零采集、零复用带副作用 GET(marketing-brain/daily 与
market/trends 绝不调用);不触 viltrox_fit_score / rule_v0;compat SQL(? 占位、
SQL 字符串零字面 percent、BOOLEAN 宽容)。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "market_brain_summary_v1"
DEFAULT_BUDGET_HINT_USD = 3000
MAX_SIGNAL_ITEMS = 10
MAX_OPPORTUNITY_ITEMS = 8
MAX_ACTION_ITEMS = 12
SKU_PERF_ENRICH_LIMIT = 2  # sku_content_performance 全表扫一遍/次,限 2 次守 <3s 预算

SOURCES_NOTE = (
    "本卡全部为内部信号(评论库/意向队列/视频证据/产品目录的词表聚合,零 LLM 零采集);"
    "外部市场雷达(Reddit/Google Trends/Amazon/B&H/竞品官网)GTM-5 待接入,"
    "接入前不宣称全量实时、不编造外部数据。"
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 240) -> str:
    out = str(value or "").strip()
    return out[:limit]


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _error_card(section: str, exc: Exception) -> dict[str, Any]:
    logger.warning("market_brain.summary.%s failed: %s", section, exc, exc_info=True)
    return {"status": "error", "reason": _text(str(exc), 300)}


def _confidence_from_sample(n: int) -> str:
    if n >= 500:
        return "high"
    if n >= 100:
        return "medium"
    return "low"


# ── 卡① weekly_signals ──────────────────────────────────────────────


def _brand_pulse_signals(items: list[dict[str, Any]], source_status: dict[str, Any]) -> None:
    from app.domains.market.brand_pulse import get_brand_pulse

    bp = get_brand_pulse()
    scanned = _int0((bp.get("coverage") or {}).get("videos_scanned"))
    source_status["brand_pulse"] = {"status": _text(bp.get("status"), 40), "videos_scanned": scanned}
    if bp.get("status") not in ("ok", "no_brand_signal"):
        return
    freshness = f"近{_int0(bp.get('window_days'))}天(至 {_text(bp.get('window_end'), 10)})"
    viltrox = bp.get("viltrox") or {}
    total = _int0(viltrox.get("total_videos"))
    if total > 0:
        sov = viltrox.get("share_of_voice")
        sov_txt = f",SoV {round(float(sov) * 100)}%" if sov is not None else ""
        rank = viltrox.get("rank")
        rank_txt = f",提及榜第 {rank} 位" if rank else ""
        items.append({
            "signal": (
                f"Viltrox 品牌提及 {total} 条视频{sov_txt}{rank_txt};"
                f"趋势 {_text(viltrox.get('trend'), 20)}(动量 {viltrox.get('momentum_pct')}%)"
            ),
            "kind": "brand_pulse",
            "freshness": freshness,
            "sample_size": scanned,
            "confidence": _confidence_from_sample(scanned),
        })
    rising = list((bp.get("groups") or {}).get("rising") or [])
    if rising:
        items.append({
            "signal": "竞品声量上行:" + ", ".join(_text(k, 40) for k in rising[:5]) + "(视频×品牌口径,只出品牌名不出竞品笔记)",
            "kind": "brand_pulse",
            "freshness": freshness,
            "sample_size": scanned,
            "confidence": _confidence_from_sample(scanned),
        })


def _category_track_signals(
    items: list[dict[str, Any]], source_status: dict[str, Any], tracks_result: dict[str, Any] | None, tracks_error: str | None,
) -> None:
    if tracks_result is None:
        source_status["category_tracks"] = {"status": "error", "reason": _text(tracks_error, 200)}
        return
    source_status["category_tracks"] = {
        "status": _text(tracks_result.get("status"), 40),
        "voice_docs": _int0((tracks_result.get("sources") or {}).get("voice_docs")),
        "evidence_rows": _int0((tracks_result.get("sources") or {}).get("evidence_rows")),
    }
    if tracks_result.get("status") != "ready":
        return
    windows = tracks_result.get("windows") or {}
    freshness = (
        f"评论近{_int0((windows.get('comments') or {}).get('days'))}天/"
        f"证据近{_int0((windows.get('evidence') or {}).get('days'))}天"
    )
    for opp in list(tracks_result.get("opportunities") or [])[:3]:
        opportunity = opp.get("opportunity") or {}
        demand = opp.get("demand") or {}
        coverage = opp.get("coverage") or {}
        demand_total = _int0(demand.get("total"))
        items.append({
            "signal": (
                f"赛道机会:{_text(opp.get('label'), 60)} 机会分 {opportunity.get('score')}"
                f"(需求声量 {demand_total} 条;我方覆盖 {_int0(coverage.get('sku_count'))} SKU/"
                f"{_int0(coverage.get('our_voice_videos'))} 条内容)"
            ),
            "kind": "category_track",
            "freshness": freshness,
            "sample_size": demand_total,
            "confidence": _text(opportunity.get("confidence"), 20) or "low",
        })


def _market_voice_signals(items: list[dict[str, Any]], source_status: dict[str, Any]) -> None:
    from app.domains.market.market_voice import voice_report

    vr = voice_report()
    sample = _int0(vr.get("sample_size"))
    source_status["market_voice"] = {"status": _text(vr.get("status"), 40), "sample_size": sample}
    if vr.get("status") != "ready":
        return
    freshness = _text((vr.get("window") or {}).get("label"), 40) or "近30天"
    complaints = vr.get("complaints") or {}
    if complaints.get("status") == "ready":
        top = next((c for c in complaints.get("categories") or [] if _int0(c.get("count")) > 0), None)
        if top:
            items.append({
                "signal": (
                    f"市场之声:抱怨聚类 top「{_text(top.get('label'), 40)}」{_int0(top.get('count'))} 条"
                    f"(共 {_int0(complaints.get('total_matched'))} 条命中;只出归纳信号不出原声大段)"
                ),
                "kind": "market_voice",
                "freshness": freshness,
                "sample_size": sample,
                "confidence": _confidence_from_sample(sample),
            })
    wishlist = vr.get("wishlist") or {}
    if _int0(wishlist.get("total")) > 0:
        focal_top = next(iter(wishlist.get("focal_requests") or []), None)
        focal_txt = (
            f",焦段愿望 top {_text(focal_top.get('focal'), 20)}({_int0(focal_top.get('count'))} 条)"
            if focal_top else ""
        )
        items.append({
            "signal": f"愿望信号 {_int0(wishlist.get('total'))} 条(wish 词表命中){focal_txt}",
            "kind": "market_voice",
            "freshness": freshness,
            "sample_size": sample,
            "confidence": _confidence_from_sample(sample),
        })


def _weekly_signals_card(tracks_result: dict[str, Any] | None, tracks_error: str | None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    source_status: dict[str, Any] = {}
    for name, fn in (
        ("brand_pulse", lambda: _brand_pulse_signals(items, source_status)),
        ("category_tracks", lambda: _category_track_signals(items, source_status, tracks_result, tracks_error)),
        ("market_voice", lambda: _market_voice_signals(items, source_status)),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — 单源失败不拖垮整卡,诚实标注
            logger.warning("market_brain.summary.weekly_signals.%s failed: %s", name, exc)
            source_status[name] = {"status": "error", "reason": _text(str(exc), 200)}
    return {
        "status": "ok" if items else "empty",
        "items": items[:MAX_SIGNAL_ITEMS],
        "sources": source_status,
        "sources_note": SOURCES_NOTE,
    }


# ── 卡② product_opportunities ───────────────────────────────────────


def _load_products() -> list[dict[str, Any]]:
    from app.db.connection import get_conn

    rows = get_conn().execute(
        "SELECT sku, model_name, marketing_name, category_main, price_usd, status FROM vkpi_products",
        (),
    ).fetchall()
    return [dict(r) for r in rows]


def _product_blob(product: dict[str, Any]) -> str:
    return " ".join(
        str(product.get(k) or "") for k in ("sku", "model_name", "marketing_name", "category_main")
    ).lower()


def _match_skus_for_track(opp: dict[str, Any], products: list[dict[str, Any]]) -> list[str]:
    """机会赛道 → 目录 SKU 映射:焦段维用 category_tracks 同源焦段提取;品类维用同源词表。"""
    dimension = _text(opp.get("dimension"), 20)
    key = _text(opp.get("key"), 60) or _text(opp.get("track_id"), 80).split(":", 1)[-1]
    matched: list[str] = []
    if dimension == "focal":
        try:
            from app.domains.market.category_tracks import _extract_focals as extract_focals
        except ImportError:
            extract_focals = None
        for product in products:
            blob = _product_blob(product)
            if extract_focals is not None:
                hit = key in extract_focals(blob)
            else:
                hit = key in blob  # 降级:子串匹配(诚实粗口径)
            if hit:
                matched.append(str(product.get("sku") or ""))
    elif dimension == "category":
        terms: tuple[str, ...] = ()
        try:
            from app.domains.market.category_tracks import _category_tracks_def

            terms = next((t for k, _label, t in _category_tracks_def() if k == key), ())
        except ImportError:
            terms = ()
        if key == "af_lens":
            matched = [str(p.get("sku") or "") for p in products if str(p.get("sku") or "").upper().startswith("AF-")]
        elif terms:
            for product in products:
                blob = _product_blob(product)
                if any(term.lower() in blob for term in terms):
                    matched.append(str(product.get("sku") or ""))
    matched = [sku for sku in matched if sku]
    matched.sort(key=len)  # 短 SKU 优先(单品先于巨型套装)
    return matched


def _sku_perf_brief(sku: str) -> dict[str, Any] | None:
    """sku_performance 轻量摘要:只取聚合数,不透传内容清单/创作者明细。"""
    from app.domains.products.sku_performance import sku_content_performance

    perf = sku_content_performance(sku)
    if perf.get("status") != "ok":
        return None
    aggregate = (perf.get("content") or {}).get("aggregate") or {}
    return {
        "content_count": _int0(aggregate.get("content_count")),
        "creator_count": _int0(aggregate.get("creator_count")),
        "total_views": _int0(aggregate.get("total_views")),
        "avg_engagement_rate": aggregate.get("avg_engagement_rate"),
    }


def _product_opportunities_card(tracks_result: dict[str, Any] | None, tracks_error: str | None) -> dict[str, Any]:
    if tracks_result is None:
        return {"status": "error", "reason": "category_tracks 聚合失败:" + _text(tracks_error, 220), "items": []}
    if tracks_result.get("status") != "ready":
        return {
            "status": "empty",
            "reason": _text(tracks_result.get("reason"), 300) or "赛道矩阵无数据,机会清单诚实为空。",
            "items": [],
        }
    products = _load_products()
    try:
        from app.domains.costs.product_persona import get_product_personas
    except ImportError:
        get_product_personas = None

    opportunities = list(tracks_result.get("opportunities") or [])[:6]
    pre_matched: list[tuple[dict[str, Any], list[str]]] = [
        (opp, _match_skus_for_track(opp, products)[:2]) for opp in opportunities
    ]
    all_skus = [sku for _opp, skus in pre_matched for sku in skus]
    personas: dict[str, dict[str, Any]] = {}
    if get_product_personas is not None and all_skus:
        try:
            personas = get_product_personas(all_skus)
        except Exception as exc:  # noqa: BLE001 — persona 缺席不拖垮机会清单
            logger.warning("market_brain.summary.personas failed: %s", exc)

    perf_cache: dict[str, dict[str, Any] | None] = {}
    items: list[dict[str, Any]] = []
    for opp, skus in pre_matched:
        opportunity = opp.get("opportunity") or {}
        demand = opp.get("demand") or {}
        coverage = opp.get("coverage") or {}
        base_basis = {
            "track_id": _text(opp.get("track_id"), 80),
            "demand_total": _int0(demand.get("total")),
            "coverage_sku_count": _int0(coverage.get("sku_count")),
            "coverage_our_voice_videos": _int0(coverage.get("our_voice_videos")),
            "confidence": _text(opportunity.get("confidence"), 20) or "low",
            "note": "market=内部赛道口径(品类/焦段维),地理市场维度 GTM-2/GTM-5 接入前不虚构。",
        }
        if not skus:
            items.append({
                "sku": None,
                "market": _text(opp.get("label"), 60),
                "persona": None,
                "content_angle": None,
                "opportunity_score": opportunity.get("score"),
                "basis": {**base_basis, "note": "目录无对应 SKU——赛道级机会(建品/建内容候选),诚实不硬配 SKU。"},
            })
            continue
        for sku in skus:
            persona_row = personas.get(sku) or {}
            angles = persona_row.get("promotion_angles_json") or []
            # 内容表现增益:每赛道只富化首个 SKU,全局限 2 次全表扫(守 <3s 预算)
            if sku == skus[0] and len(perf_cache) < SKU_PERF_ENRICH_LIMIT and sku not in perf_cache:
                try:
                    perf_cache[sku] = _sku_perf_brief(sku)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("market_brain.summary.sku_perf failed for %s: %s", sku, exc)
                    perf_cache[sku] = None
            content_perf = perf_cache.get(sku)
            items.append({
                "sku": sku,
                "market": _text(opp.get("label"), 60),
                "persona": _text(persona_row.get("ideal_persona"), 220) or None,
                "content_angle": _text(angles[0], 160) if angles else None,
                "opportunity_score": opportunity.get("score"),
                "basis": {**base_basis, "content_performance": content_perf},
            })
        if len(items) >= MAX_OPPORTUNITY_ITEMS:
            break
    return {
        "status": "ok" if items else "empty",
        "items": items[:MAX_OPPORTUNITY_ITEMS],
        "basis_note": (
            "机会分来自 category_tracks(需求×覆盖弱点×竞争开放度,权重 v0 待校准);"
            "persona/content_angle 来自产品画像表;内容表现为 sku_performance 聚合数"
            "(限前 2 个 SKU,守响应预算)。"
        ),
    }


# ── 卡③ recommended_actions ────────────────────────────────────────


def _cost_note_from_cents(cents: Any) -> str:
    n = _int0(cents)
    if n > 0:
        return f"预估 ${n / 100:.2f}"
    return "零新增成本"


def _inbox_actions(items: list[dict[str, Any]], source_status: dict[str, Any], staff: dict[str, Any] | None) -> None:
    from app.domains.actions.inbox import list_inbox

    inbox = list_inbox(staff, status="suggested", limit=5)
    source_status["action_inbox"] = {
        "status": "ready" if inbox.get("available") else "unavailable",
        "count": _int0(inbox.get("count")),
        "scope": _text(inbox.get("scope"), 10),
    }
    for row in inbox.get("items") or []:
        items.append({
            "action": _text(row.get("title"), 160) or f"Action #{row.get('id')}",
            "reason": _text(row.get("reason"), 240) or _text(row.get("detail"), 240),
            "evidence_summary": (
                f"证据引用 {len(row.get('evidence_refs_json') or [])} 条"
                f"(分类 {_text(row.get('category'), 40)},优先级 {_text(row.get('priority'), 10)})"
            ),
            "cost_note": _cost_note_from_cents(row.get("estimated_cost_cents")),
            "risk": _text(row.get("risk_level"), 20) or "low",
            "expected_gain": _text(row.get("expected_gain"), 200) or "未量化(建议生成方未标)",
            "ref": {"type": "action_inbox", "id": row.get("id"), "category": _text(row.get("category"), 40)},
        })


def _gifted_overdue_actions(items: list[dict[str, Any]], source_status: dict[str, Any]) -> None:
    from app.domains.projects.gifted_funnel import funnel

    snapshot = funnel()
    source_status["gifted_funnel"] = {
        "status": _text(snapshot.get("status"), 20),
        "overdue": _int0(snapshot.get("overdue")),
        "gifted": _int0(snapshot.get("gifted")),
    }
    if snapshot.get("status") != "ready":
        return
    overdue = sorted(
        list(snapshot.get("overdue_items") or []),
        key=lambda it: -_int0(it.get("days_since_sent")),
    )
    total_overdue = _int0(snapshot.get("overdue"))
    for row in overdue[:4]:
        days = _int0(row.get("days_since_sent"))
        items.append({
            "action": f"催更:{_text(row.get('kol_name'), 60)}({_text(row.get('platform'), 20)})",
            "reason": f"送样超期 {days} 天未发布(项目 {_text(row.get('project_name'), 60)})",
            "evidence_summary": (
                f"派单 #{row.get('assignment_id')} · 送样基准 {_text(row.get('sent_basis'), 40)}"
                f" · 全池超期 {total_overdue} 条"
            ),
            "cost_note": "零新增成本(仅联系催更;外发仍需人工执行)",
            "risk": "low",
            "expected_gain": "补齐送样→发布漏斗缺口(履约兑现)",
            "ref": {"type": "gifted_overdue", "assignment_id": row.get("assignment_id"), "kol_pool_id": row.get("kol_pool_id")},
        })


def _needs_analysis_action(items: list[dict[str, Any]], source_status: dict[str, Any]) -> None:
    from app.domains.kol.video_analysis_enqueue import list_kols_needing_video_analysis

    pending = list_kols_needing_video_analysis(limit=50)
    count = _int0(pending.get("count"))
    source_status["needs_analysis"] = {"status": "ready", "count": count}
    if count <= 0:
        return
    names = [
        _text(row.get("display_name"), 40) or _text(row.get("handle"), 40)
        for row in (pending.get("items") or [])[:3]
    ]
    items.append({
        "action": f"深析补课:{count} 位 KOL 有视频证据但无 ready 深析(入队候选)",
        "reason": "产品匹配与内容画像判断缺输入——有证据没分析等于白采",
        "evidence_summary": "样例:" + ", ".join(n for n in names if n),
        "cost_note": "视频深析走既有预算闸(profile_only 默认不跑全视频)",
        "risk": "low",
        "expected_gain": "补齐 KOL 内容画像覆盖,提升后续推荐可判读性",
        "ref": {"type": "needs_analysis", "count": count},
    })


def _recommended_actions_card(staff: dict[str, Any] | None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    source_status: dict[str, Any] = {}
    for name, fn in (
        ("action_inbox", lambda: _inbox_actions(items, source_status, staff)),
        ("gifted_funnel", lambda: _gifted_overdue_actions(items, source_status)),
        ("needs_analysis", lambda: _needs_analysis_action(items, source_status)),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — 单源失败不拖垮整卡
            logger.warning("market_brain.summary.recommended_actions.%s failed: %s", name, exc)
            source_status[name] = {"status": "error", "reason": _text(str(exc), 200)}
    return {
        "status": "ok" if items else "empty",
        "items": items[:MAX_ACTION_ITEMS],
        "sources": source_status,
        "note": (
            "本卡纯读预览:审批/执行按钮 v1 占位 disabled(GTM-3 接线);"
            "所有动作仍走 Action Inbox 人审,本端点绝不落库、绝不外发。"
        ),
    }


# ── 卡④ strategy_defaults ──────────────────────────────────────────


def _strategy_defaults_card(product_opportunities: dict[str, Any]) -> dict[str, Any]:
    sku_hint = None
    for item in product_opportunities.get("items") or []:
        if item.get("sku"):
            sku_hint = str(item["sku"])
            break
    basis = "取自本页 top 产品机会(category_tracks 排序首个有 SKU 的条目)"
    if not sku_hint:
        from app.db.connection import get_conn

        row = get_conn().execute(
            "SELECT sku FROM vkpi_products ORDER BY updated_at DESC LIMIT 1",
            (),
        ).fetchone()
        sku_hint = str(dict(row).get("sku")) if row else None
        basis = "机会清单无 SKU,回退目录最近更新的 SKU(仅作输入占位)"
    return {
        "status": "ok",
        "simulate_entry": {"sku_hint": sku_hint, "budget_hint": DEFAULT_BUDGET_HINT_USD},
        "note": (
            f"StrategySimPanel 入口默认值:sku_hint {basis};budget_hint=${DEFAULT_BUDGET_HINT_USD}"
            "(规格默认)。模拟本身为只读 what-if 三方案对比,本卡不预跑模拟、不写库。"
        ),
    }


# ── 卡⑤ learning_digest ────────────────────────────────────────────


def _ledger_validated(validated: list[str], source_status: dict[str, Any]) -> None:
    from app.domains.agents.prediction_ledger import ledger_summary

    ledger = ledger_summary()
    totals = ledger.get("totals") or {}
    source_status["prediction_ledger"] = {
        "status": _text(ledger.get("status"), 20),
        "judged_total": _int0(totals.get("judged_total")),
        "pending_total": _int0(totals.get("pending_total")),
    }
    if ledger.get("status") != "ok":
        return
    for group in ledger.get("groups") or []:
        sample = _int0(group.get("sample_count"))
        rate = group.get("hit_rate")
        if group.get("status") == "ok" and sample >= 5 and rate is not None:
            validated.append(
                f"{_text(group.get('label'), 60)}:近 {sample} 条裁决命中率 "
                f"{round(float(rate) * 100)}%(置信 {_text(group.get('confidence'), 20)})"
            )


def _scorecard_digest(source_status: dict[str, Any]) -> dict[str, Any]:
    from app.domains.learning.weekly_scorecard import weekly_scorecard

    scorecard = weekly_scorecard()
    overall = scorecard.get("overall") or {}
    backlog = scorecard.get("pending_backlog") or {}
    source_status["weekly_scorecard"] = {
        "status": _text(scorecard.get("status"), 20) or "ok",
        "in_range_judged": _int0(overall.get("in_range_judged")),
        "pending_total": _int0(backlog.get("pending_total")),
    }
    return {
        "pending_total": _int0(backlog.get("pending_total")),
        "headline": _text(backlog.get("headline"), 240),
        "hit_rate": overall.get("in_range_hit_rate"),
        "judged": _int0(overall.get("in_range_judged")),
    }


def _miss_review_dropped(dropped: list[str], source_status: dict[str, Any]) -> list[str]:
    from app.domains.learning.miss_review import miss_review_list

    review = miss_review_list()
    totals = review.get("totals") or {}
    source_status["miss_review"] = {
        "status": _text(review.get("status"), 20),
        "miss_total": _int0(totals.get("miss_total")),
        "needs_review_groups": _int0(totals.get("needs_review_groups")),
    }
    review_targets: list[str] = []
    if review.get("status") != "ok":
        return review_targets
    for group in review.get("groups") or []:
        if not group.get("needs_review"):
            continue
        label = _text(group.get("label"), 60)
        hit = group.get("hit") or {}
        reasons = group.get("reasons") or []
        top_reason = _text((reasons[0] or {}).get("label") if reasons else "", 60) or _text(
            (reasons[0] or {}).get("reason_key") if reasons else "", 60
        )
        rate = hit.get("hit_rate")
        rate_txt = f"命中率 {round(float(rate) * 100)}%" if rate is not None else "命中率未知"
        dropped.append(
            f"{label}:{rate_txt}(样本 {_int0(hit.get('sample_count'))}),低于复盘线"
            + (f";原因 top「{top_reason}」" if top_reason else "")
        )
        review_targets.append(label)
    return review_targets


def _effective_styles(source_status: dict[str, Any]) -> list[str]:
    """高表现段位拍法标签:段级索引按所属视频播放数排序的 top 段聚合(相关性≠因果)。"""
    from app.domains.content.creative_segments import segment_search

    result = segment_search("", "", "", limit=20)
    source_status["creative_segments"] = {
        "status": _text(result.get("status"), 20),
        "scanned_videos": _int0(result.get("scanned_videos")),
    }
    if result.get("status") != "ready":
        return []
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for segment in result.get("items") or []:
        for tag in list(segment.get("styles") or []) + list(segment.get("video_styles") or []):
            key = _text(tag.get("key"), 40)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            labels[key] = _text(tag.get("label"), 40) or key
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    return [f"{labels[key]}(高播放段位出现 {n} 次;相关性非因果)" for key, n in ranked]


def _learning_digest_card() -> dict[str, Any]:
    validated: list[str] = []
    dropped: list[str] = []
    source_status: dict[str, Any] = {}
    scorecard_brief: dict[str, Any] = {}
    review_targets: list[str] = []
    styles: list[str] = []
    for name, fn in (
        ("prediction_ledger", lambda: _ledger_validated(validated, source_status)),
        ("weekly_scorecard", lambda: scorecard_brief.update(_scorecard_digest(source_status))),
        ("miss_review", lambda: review_targets.extend(_miss_review_dropped(dropped, source_status))),
        ("creative_segments", lambda: styles.extend(_effective_styles(source_status))),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — 单源失败不拖垮整卡
            logger.warning("market_brain.summary.learning_digest.%s failed: %s", name, exc)
            source_status[name] = {"status": "error", "reason": _text(str(exc), 200)}

    pending_total = _int0(scorecard_brief.get("pending_total"))
    next_bits: list[str] = []
    if pending_total > 0:
        next_bits.append(
            f"先对答案:{pending_total} 条预测 pending 待裁决(GTM-4 裁决流)——样本充足前不改推荐口径"
        )
    if review_targets:
        next_bits.append("优先复盘低命中组:" + "、".join(review_targets[:3]))
    if not next_bits:
        next_bits.append("暂无高置信变更建议:裁决样本不足,规则库与推荐口径维持现状")
    judged = _int0(scorecard_brief.get("judged"))
    return {
        "status": "ok",
        "validated": validated,
        "effective_styles": styles,
        "dropped_channels": dropped,
        "next_change": ";".join(next_bits) + "。",
        "honesty_note": (
            f"学习闭环仍在样本荒:窗口内已裁决 {judged} 条 vs pending {pending_total} 条,"
            "结论以样本为限、只述相关不述因果;「渠道不值」口径=低命中动作组(miss_review needs_review),"
            "非真实渠道 ROI(订单归因 GTM-4 后可用)。本卡纯读,不改任何评分、规则或推荐。"
        ),
        "sources": source_status,
    }


# ── 主入口 ──────────────────────────────────────────────────────────


def build_summary(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """GTM 总脑页五卡一次聚合(纯读):weekly_signals / product_opportunities /
    recommended_actions / strategy_defaults / learning_digest。

    每段独立 try/except:单段失败该段 {status:"error"} 其余照出,绝不整卡 500。
    staff 仅用于 action inbox 的既有 scope 过滤(管理层全局/成员看自己),不做任何写。
    """
    started = time.time()
    tracks_result: dict[str, Any] | None = None
    tracks_error: str | None = None
    try:
        from app.domains.market.category_tracks import tracks

        tracks_result = tracks()
    except Exception as exc:  # noqa: BLE001 — 共享数据源失败,两张卡各自诚实降级
        logger.warning("market_brain.summary.tracks failed: %s", exc, exc_info=True)
        tracks_error = _text(str(exc), 300)

    out: dict[str, Any] = {}
    try:
        out["weekly_signals"] = _weekly_signals_card(tracks_result, tracks_error)
    except Exception as exc:  # noqa: BLE001
        out["weekly_signals"] = _error_card("weekly_signals", exc)
    try:
        out["product_opportunities"] = _product_opportunities_card(tracks_result, tracks_error)
    except Exception as exc:  # noqa: BLE001
        out["product_opportunities"] = _error_card("product_opportunities", exc)
    try:
        out["recommended_actions"] = _recommended_actions_card(staff)
    except Exception as exc:  # noqa: BLE001
        out["recommended_actions"] = _error_card("recommended_actions", exc)
    try:
        out["strategy_defaults"] = _strategy_defaults_card(out.get("product_opportunities") or {})
    except Exception as exc:  # noqa: BLE001
        out["strategy_defaults"] = _error_card("strategy_defaults", exc)
    try:
        out["learning_digest"] = _learning_digest_card()
    except Exception as exc:  # noqa: BLE001
        out["learning_digest"] = _error_card("learning_digest", exc)

    out["method"] = METHOD
    out["generated_at"] = _utcnow_iso()
    out["took_ms"] = int((time.time() - started) * 1000)
    out["note"] = (
        "GTM 总脑五卡纯读聚合:零写库、零 LLM、零采集;只出结论/信号/行动,"
        "评分明细、原始评论大段、竞品笔记、黑名单一律不出(显示层宪法)。"
    )
    return out
