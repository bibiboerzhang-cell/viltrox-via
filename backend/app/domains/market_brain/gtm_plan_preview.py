"""GTM-1 纯读 GTM Plan Preview(规格第二章 B 端点的域实现)。

输入 SKU+国家+预算+目标+窗口 → 11 段作战建议(public_plan)+ meta。
一切数字来自库内既有纯读函数/表,单段失败诚实 {status:"error"} 不拖垮整卡。

红线(GTM-1 专属):
- 零写库、零 LLM、零采集、零复用带副作用的 GET;
  * strategy_sim.simulate 内部 forecast 调用会落 vkpi_forecast_log,本模块
    绝不直调 simulate;改用其纯函数积木 + dry_run 预测 shim(_DryRunForecast)
    重建三方案摘要,vkpi_forecast_log 行数调用前后必须相等(冒烟内置断言);
  * marketing-brain/daily 与 market/trends 的 GET 有隐藏写入,一律不碰。
- 显示层宪法:响应只出 public_plan;评分明细(score_breakdown)/原始评论
  大段(quotes)/竞品笔记(note/example)/黑名单一律不出现,KOL 风险只出
  标签;private_evidence 结构有定义(PRIVATE_EVIDENCE_SCHEMA)但默认绝不返回。
- 预测措辞禁绝对化:forecast 段强制条件化模板,每条必带 escalate_if +
  retreat_if(真阈值,规则库口径),出货前有措辞纪律守卫。
- 绝不写 viltrox_fit_score / rule_v0;零迁移;不碰 .env。

规则库口径:蓝图 v0.2 第六章平台研究数字先以 GROWTH_RULES 常量登记
(带 basis/source/confidence),GTM-2 落表后改读表——非系统真理,
可被自有数据推翻。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "gtm_plan_preview_v1(纯读聚合,零 LLM 零采集零写库,同输入同输出)"

GOALS = ("exposure", "conversion", "content", "channel")

_HORIZONS = (7, 14, 30)
_POOL_LIMIT = 12
_TOP_CANDIDATES = 8
_SIGNATURE_TOP_N = 5

# ── 规则库(Growth Playbook,非系统真理;GTM-2 落表,每条可被自有数据推翻) ──

_RULE_SOURCE = "platform_research_2026H1(蓝图 v0.2 第六章规则库;GTM-2 编码进表后改读表)"
_RULE_NOTE = "规则库口径非系统真理,带置信度,可被自有数据推翻更新。"
_SELF_BASELINE = "self_baseline(镜头类目公开基准缺失,自建基线待校准)"


def _rule(threshold: Any, unit: str, statement: str, confidence: str, source: str = _RULE_SOURCE) -> dict[str, Any]:
    return {"threshold": threshold, "unit": unit, "statement": statement,
            "source": source, "confidence": confidence, "sample_size": None}


GROWTH_RULES: dict[str, dict[str, Any]] = {
    "tiktok_completion_viral": _rule(0.70, "completion_rate", "TikTok 完播率 ≥70% 视作病毒传播门槛", "medium"),
    "hook_2s_retention_a": _rule(0.40, "2s_hook_retention", "开头 2 秒留存 ≥40% 判 A 档 hook", "medium"),
    "yt_ctr_floor": _rule(0.04, "ctr", "YouTube CTR ≥4% 且平均留存 ≥40% 同时满足才判内容健康", "medium"),
    "per_variant_min_impressions": _rule(1000, "impressions", "每素材变体 ≥1000 曝光后才判胜负,避免小样本误判", "medium"),
    "commission_band": _rule([0.10, 0.15], "commission_rate", "达人佣金 10-15% 区间起步 + 阶梯激励", "medium"),
    "spark_uplift": _rule(0.43, "uplift", "Spark Ads 放大达人原生内容约 +43%,自产素材直投不适用", "low"),
    "outreach_reply_floor": _rule(0.10, "reply_rate", "外联有效回复率目标 ≥10%(自建基线,库内样本不足)", "low", _SELF_BASELINE),
    "ig_sends_nonfollower": _rule([3, 5], "multiplier", "IG 转发(sends)对非粉触达约 3-5×,优先可转发型内容", "low"),
}

# private_evidence:结构定义(默认绝不返回;?debug=1+owner 分支 v1 不实现)
PRIVATE_EVIDENCE_SCHEMA: dict[str, str] = {
    "score_details": "候选评分明细与权重拆解——仅离线审计",
    "raw_sources": "原始评论/愿望清单大段原文——页面只给归纳信号",
    "competitor_notes": "竞品监控细节(示例视频/份额笔记)——不出前端",
    "model_features": "预测模型特征与内部推理轨迹",
    "debug_trace": "聚合过程调试轨迹",
}

# 措辞纪律:预判绝对化词根黑名单(命中即整改,绝不出货)
_ABSOLUTE_WORDING = (
    "必然", "一定", "肯定", "保证", "绝对", "百分之百", "稳赢", "无风险",
    "definitely", "guaranteed", "certainly", "100%",
)

_GOAL_MAINLINE = {
    "exposure": "KOL 内容放大主线(官号协同曝光)",
    "conversion": "KOL 短链转化主线(独立站承接 + 官号教育内容托底)",
    "content": "素材沉淀主线(先攒可复用创意段,再谈放大)",
    "channel": "渠道铺货主线(Dealer 数据缺席,v1 先以官号+独立站代位)",
}


# ── 小工具 ──────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _usd(value: Any) -> float | None:
    f = _float_or_none(value)
    return round(f, 2) if f is not None else None


def _fmt_int(value: Any) -> str:
    return f"{_int0(value):,}"


def absolute_wording_hits(text: str) -> list[str]:
    """措辞纪律检查:返回命中的绝对化词根(冒烟与内部守卫共用)。"""
    blob = str(text or "")
    low = blob.lower()
    return [w for w in _ABSOLUTE_WORDING if (w in blob or w in low)]


def _rule_ref(key: str) -> str:
    rule = GROWTH_RULES.get(key) or {}
    return f"{rule.get('statement', key)}(规则库 {key},confidence={rule.get('confidence', '?')})"


def _section_error(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "reason": _text(str(exc), 300)}


class _DryRunForecast:
    """performance_forecast 的零落库替身:同契约、强制 dry_run=True。

    strategy_sim._build_candidates 里的 forecast 调用默认会 best-effort 落
    vkpi_forecast_log 预测流水;preview 是纯读端点,这里逐调用改走 dry_run,
    结果字段完全一致,只是不写流水(冒烟以 vkpi_forecast_log 行数不变为证)。
    """

    @staticmethod
    def forecast_for_kol(kol_pool_id: int, sku: str | None = None, *, conn: Any = None, **_kw: Any) -> dict[str, Any]:
        from app.domains.kol import performance_forecast

        return performance_forecast.forecast_for_kol(
            int(kol_pool_id), sku=sku, conn=conn, context="sim", dry_run=True,
        )


# ── 数据装载(全部既有纯读函数;调用处逐项守卫) ─────────────────────


def _build_candidates(sku: str, pool_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Any, str]:
    """strategy_sim 引擎积木 + dry_run 预测 shim → 候选读数(零写库)。"""
    from app.db.connection import get_conn
    from app.domains.market import strategy_sim as ss

    rate_card, _forecast_mod, roster_mod, engines_missing = ss._load_engines()
    cands = ss._build_candidates(sku, pool_items, rate_card, _DryRunForecast, get_conn())
    return cands, roster_mod, engines_missing


def _official_channels_snapshot() -> dict[str, Any]:
    """官号真读:vkpi_employee_channels 官方口径 + 每号最新 vkpi_channel_metrics。

    口径与 dashboard.summary_company 同源(_OFFICIAL_CHANNEL_FILTER_SQL);
    compat 规则:? 占位、无字面 %(过滤用 POSITION)。
    """
    from app.db.connection import get_conn
    from app.domains.dashboard.metric_maturity import _OFFICIAL_CHANNEL_FILTER_SQL

    conn = get_conn()
    rows = conn.execute(
        f"""
        WITH official AS (
          SELECT c.id, LOWER(c.platform) AS platform, c.account_handle
          FROM vkpi_employee_channels c
          WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        )
        SELECT o.id, o.platform, o.account_handle,
               COALESCE(m.followers, 0) AS followers,
               COALESCE(m.total_views, 0) AS total_views,
               m.snapshot_date AS latest_snapshot
        FROM official o
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT mm.id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = o.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        """
    ).fetchall()
    channels = [dict(r) for r in rows]
    metrics_row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM vkpi_channel_metrics m
        WHERE m.channel_id IN (
            SELECT c.id FROM vkpi_employee_channels c WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        )
        """
    ).fetchone()
    return {
        "channels": channels,
        "official_count": len(channels),
        "metrics_rows": _int0(dict(metrics_row).get("n") if metrics_row else 0),
    }


# ── 净化器(显示层宪法:只出归纳信号,绝不出原文/明细/竞品笔记) ─────


def _sanitize_track(opp: dict[str, Any]) -> dict[str, Any]:
    """category_tracks 机会行 → public 版:去掉 evidence 整包(引语/竞品示例)。"""
    competitors = {k: v for k, v in (opp.get("competitors") or {}).items() if k != "example"}
    return {
        "track_id": _text(opp.get("track_id"), 60),
        "label": _text(opp.get("label"), 80),
        "opportunity": opp.get("opportunity") or {},
        "demand": {k: v for k, v in (opp.get("demand") or {}).items() if "quote" not in str(k)},
        "coverage": opp.get("coverage") or {},
        "competitor_aggregate": competitors,
    }


def _risk_labels(cand: dict[str, Any], pool_item: dict[str, Any], country: str | None) -> list[str]:
    """KOL 风险只出标签(绝不出黑名单/评分明细)。"""
    labels: list[str] = []
    if pool_item.get("review_required"):
        labels.append("需人工复核")
    if not cand.get("forecast_ready") or (cand.get("forecast_confidence") or "low") == "low":
        labels.append("预测样本不足")
    if cand.get("cost_method") == "cpm_benchmark_v0":
        labels.append("报价为行业基准锚点(非实报价)")
    if country:
        c_country = _text(cand.get("country"), 60).upper()
        if not c_country:
            labels.append("创作者地域未知")
        elif country.upper() not in c_country:
            labels.append("地域与目标市场待确证(受众地理置信低)")
    return labels


def _candidate_out(cand: dict[str, Any], pool_item: dict[str, Any], country: str | None) -> dict[str, Any]:
    return {
        "kol_pool_id": cand.get("kol_pool_id"),
        "handle": cand.get("handle"),
        "display_name": cand.get("display_name"),
        "platform": cand.get("platform"),
        "country": cand.get("country"),
        "tier": cand.get("tier"),
        "followers": cand.get("followers"),
        "match_score": cand.get("match_score"),
        "cost_usd_p50": cand.get("cost_p50"),
        "cost_confidence": cand.get("cost_confidence"),
        "expected_views_p50": cand.get("views_p50"),
        "expected_views_p10": cand.get("views_p10"),
        "expected_views_p90": cand.get("views_p90"),
        "forecast_confidence": cand.get("forecast_confidence") if cand.get("forecast_ready") else None,
        "risk_labels": _risk_labels(cand, pool_item, country),
    }


# ── 11 段构建器 ─────────────────────────────────────────────────────


def _build_thesis(*, track: dict[str, Any] | None, pool_status: str, priceable: int,
                  persona: dict[str, Any] | None, country: str | None, goal: str,
                  official_count: int) -> dict[str, Any]:
    demand = (track or {}).get("demand") or {}
    opp = (track or {}).get("opportunity") or {}
    demand_total = _int0(demand.get("total"))
    rising = _text(demand.get("comment_trend"), 20) == "rising"
    track_conf = _text(opp.get("confidence"), 20) or "none"

    if rising and priceable >= 5 and track_conf in ("high", "medium"):
        go_nogo, condition = "go", "需求信号上行且候选池可估价人数充足——按条件推进,触发/撤退线见 forecast 段。"
    elif demand_total > 0 or priceable > 0:
        go_nogo, condition = "watch", "有需求或候选信号但强度/置信不足——小步试探,按 forecast 段触发线再加码。"
    else:
        go_nogo, condition = "hold", "窗口内需求与候选信号双缺——先补数据(外联/深析)再判断,不硬推。"

    persona_text = _text((persona or {}).get("ideal_persona"), 200)
    ready_bits = []
    if track:
        ready_bits.append(f"赛道需求 {demand_total} 条信号({'上行' if rising else demand.get('comment_trend') or '趋势未知'},置信 {track_conf})")
    ready_bits.append(f"候选池可估价 {priceable} 人(状态 {pool_status})")
    ready_bits.append(f"官号 {official_count} 个在册")

    return {
        "status": "ready",
        "go_nogo": go_nogo,
        "condition": condition,
        "market": country.upper() if country else "未指定(建议先补受众地理数据再锁市场)",
        "market_note": "country 参数 v1 只做轻影响:候选排序前置 + Dealer 段占位说明;受众地理置信仍低,不宣称市场已验证。",
        "persona": persona_text or None,
        "persona_status": "ready" if persona_text else "data_missing",
        "mainline": _GOAL_MAINLINE.get(goal, _GOAL_MAINLINE["exposure"]),
        "confidence": "medium" if (track_conf == "high" and priceable >= 5) else "low",
        "basis_summary": ";".join(ready_bits) + "。结论为条件化判断,非承诺。",
    }


def _build_forecast(*, window_days: int, sku: str, country: str | None,
                    track: dict[str, Any] | None, bench_cell: dict[str, Any] | None,
                    bench_conf: dict[str, Any], priceable: int) -> list[dict[str, Any]]:
    demand = (track or {}).get("demand") or {}
    track_conf = _text(((track or {}).get("opportunity") or {}).get("confidence"), 20) or "none"
    trend = _text(demand.get("comment_trend"), 20) or "unknown"
    market_label = country.upper() if country else "目标市场(未指定)"

    signals = (
        f"库内信号归纳:焦段需求 {_int0(demand.get('total'))} 条(评论趋势 {trend},置信 {track_conf});"
        f"同焦段声量对比 竞品 {_int0((bench_cell or {}).get('competitor_videos'))} 条"
        f" vs 我方 {_int0((bench_cell or {}).get('viltrox_videos'))} 条"
        f"(行业对照置信 {_text(bench_conf.get('level'), 20) or 'low'});候选池可估价 {priceable} 人。"
        "外部雷达(Reddit/Trends/电商榜)GTM-5 待接,本段只用库内真数据。"
    )

    horizons = [h for h in _HORIZONS if h <= max(window_days, _HORIZONS[0])]
    entries: list[dict[str, Any]] = []
    if 7 in horizons:
        entries.append({
            "horizon_days": 7,
            "statement": (
                f"若首周外联与排期顺利,{sku} 在 {market_label} 有完成首批 KOL 落位的机会;"
                "机会成立与否取决于回复率与排期达成,非承诺。"
            ),
            "signals_summary": signals,
            "confidence": "low" if priceable < 5 else "medium",
            "escalate_if": (
                f"首周外联有效回复率 ≥10%({_rule_ref('outreach_reply_floor')})"
                "且 ≥2 位候选进入排期 → 追加下一批外联并预约官号协同位。"
            ),
            "retreat_if": "10 人外联样本有效回复率 <5% 或零候选进排期 → 收缩到官号内容路线,复核选人口径与触达渠道后再试。",
        })
    if 14 in horizons:
        entries.append({
            "horizon_days": 14,
            "statement": f"若首批内容如期发布,{sku} 有 14 天内容放大窗口;是否加码由完播/hook/ER 触发线裁决,不预设结果。",
            "signals_summary": signals,
            "confidence": "low" if track_conf in ("none", "low") else "medium",
            "escalate_if": (
                f"48h 内 ≥3 条合作视频满足 2s hook 留存 ≥40%({_rule_ref('hook_2s_retention_a')})"
                f"或 TikTok 完播 ≥70%({_rule_ref('tiktok_completion_viral')}),"
                f"且 ER 高于该账号 30 天基线 → 进 Spark/联盟放大(达人原生素材,{_rule_ref('spark_uplift')})。"
            ),
            "retreat_if": (
                "首批视频 ER 持续低于账号 30 天基线,或短链点击成本高于目标 CPM 锚点 → "
                "停止加码,转官号教育内容 + 独立站承接,并把失败样本喂回选人复盘。"
            ),
        })
    if 30 in horizons or window_days >= 30:
        entries.append({
            "horizon_days": 30 if window_days >= 30 else min(30, window_days),
            "statement": (
                f"若前两窗触发线达成,{sku} 在 {market_label} 有 30 天转化验证机会;"
                "转化结论按每变体曝光门槛裁决后才算数,期间只按条件推进。"
            ),
            "signals_summary": signals,
            "confidence": "low",
            "escalate_if": (
                f"素材变体按 ≥1000 曝光判胜负({_rule_ref('per_variant_min_impressions')}),"
                f"胜出变体 YT 口径 CTR ≥4% 且留存 ≥40%({_rule_ref('yt_ctr_floor')}),"
                f"且短链归因 CPM 效率优于行业锚点 → 预算档升到激进 40/60 并铺佣金码({_rule_ref('commission_band')})。"
            ),
            "retreat_if": (
                "30 天内无可归因转化(本地无订单数据时以短链点击+落地页行为代理),"
                "或 CPM 效率持续差于行业锚点 → 回退保守 70/10/20 档,改走素材沉淀主线并复盘选人/角度。"
            ),
        })
    return entries


def _signal_ledger_summary(sku: str, country: str | None) -> dict[str, Any] | None:
    """信号台账纯读兜底:summarize_for_preview status=='ready' 才返回摘要。

    data_missing / 表缺席 / 任何异常一律返回 None(本页失败安静宪法,绝不拖垮 preview);
    import 放函数内 lazy,避免与并行在建的 signal_ledger 模块循环依赖。零写库。
    """
    try:
        from app.domains.market_brain import signal_ledger

        summary = signal_ledger.summarize_for_preview(sku, market=country)
    except Exception as exc:  # noqa: BLE001 — 台账缺席/异常不拖垮本段
        logger.warning("gtm_preview signal_ledger fallback failed for %s: %s", sku, exc)
        return None
    if isinstance(summary, dict) and _text(summary.get("status"), 20) == "ready":
        return summary
    return None


def _build_market_opportunity(*, tracks_payload: dict[str, Any], bench: dict[str, Any], focal: str,
                              sku: str, country: str | None = None) -> dict[str, Any]:
    focal_id = f"focal:{focal}mm" if focal else ""
    opportunities = tracks_payload.get("opportunities") or []
    track = next((o for o in opportunities if o.get("track_id") == focal_id), None)
    cat_track = next((o for o in opportunities if o.get("track_id") == "cat:af_lens"), None)
    cell = None
    if focal:
        cell = next((c for c in ((bench.get("focal_grid") or {}).get("cells") or [])
                     if _text(c.get("focal"), 20) == f"{focal}mm"), None)

    if not track and not cell:
        ledger = _signal_ledger_summary(sku, country)
        if ledger is not None:
            return {
                "status": "ready",
                "focal": f"{focal}mm" if focal else None,
                "items": ledger.get("items") or [],
                "sources_count": _int0(ledger.get("sources_count")),
                "sample_size": _int0(ledger.get("sample_size")),
                "freshest_at": _text(ledger.get("freshest_at"), 40) or None,
                "basis": "signal_ledger.summarize_for_preview(信号台账纯读兜底;赛道矩阵/焦段格局零行时启用,只给归纳信号)。",
            }
        return {"status": "empty",
                "reason": f"赛道矩阵与焦段格局都没有 {focal or '?'}mm 的行——窗口内该焦段零信号,诚实空态。"}
    return {
        "status": "ready",
        "focal": f"{focal}mm" if focal else None,
        "track": _sanitize_track(track) if track else {"status": "empty", "reason": "赛道矩阵无此焦段机会行。"},
        "category_track": _sanitize_track(cat_track) if cat_track else {"status": "empty", "reason": "AF 镜头品类行缺席。"},
        "focal_landscape": ({k: v for k, v in cell.items() if k != "examples"} if cell
                            else {"status": "empty", "reason": "行业对照焦段格局无此焦段。"}),
        "windows": tracks_payload.get("windows") or {},
        "benchmark_confidence": bench.get("confidence") or {},
        "basis": "category_tracks(评论+证据环比) × industry_benchmark(brand_pulse 词表焦段格局);只给归纳信号,原文与竞品示例沉在内部。",
    }


def _build_kol_candidates(*, pool: dict[str, Any], cands: list[dict[str, Any]],
                          country: str | None, focal: str) -> dict[str, Any]:
    items = pool.get("items") or []
    if pool.get("status") != "ready" or not items:
        return {"status": _text(pool.get("status"), 30) or "empty",
                "reason": _text(pool.get("reason"), 300) or "候选池为空。", "items": []}
    pool_by_id = {_int0(it.get("kol_pool_id")): it for it in items if it.get("kol_pool_id") is not None}

    ordered = list(cands)
    if country:
        cu = country.upper()
        ordered.sort(key=lambda c: 0 if cu in _text(c.get("country"), 60).upper() else 1)

    out_items = [
        _candidate_out(cand, pool_by_id.get(_int0(cand.get("kol_pool_id"))) or {}, country)
        for cand in ordered[:_TOP_CANDIDATES]
    ]

    from app.domains.projects import launch_assembly

    for entry in out_items[:_SIGNATURE_TOP_N]:
        kid = entry.get("kol_pool_id")
        if kid is None:
            continue
        try:
            sig = launch_assembly._signature_summary(int(kid))
        except Exception as exc:  # noqa: BLE001 — 单人聚合失败不拖垮
            sig = _section_error(exc)
        top_mode = (sig or {}).get("top_mode") or {}
        entry["signature"] = {
            "status": _text(sig.get("status"), 20),
            "top_style": _text(top_mode.get("label") or top_mode.get("key"), 80) or None,
            "sample_size": sig.get("sample_size"),
        }

    return {
        "status": "ready",
        "count": len(out_items),
        "pool_size": len(items),
        "priceable_count": sum(1 for c in cands if c.get("cost_ready")),
        "items": out_items,
        "country_note": (
            f"country={country.upper()}:同地域候选前置排序;创作者地域来自档案、受众地理置信低,不做硬过滤。"
            if country else "未指定 country,候选按匹配分原序。"
        ),
        "basis": (
            "launch_assembly._candidate_pool(new_launch_match 决定性 dry-run,persist_run=False)"
            "+ rate_card.estimate_rate + performance_forecast(dry_run=True,零流水)"
            "+ signature_profile top1;风险只出标签,评分明细与除名记录一律沉在内部不出前端。"
            + (f"SKU 焦段 {focal}mm。" if focal else "")
        ),
    }


def _build_dealer_targets(country: str | None) -> dict[str, Any]:
    return {
        "status": "data_missing",
        "note": "Dealer 表 0 行,GTM-2 导入后激活。"
        + (f"届时优先输出 {country.upper()} 区域经销商名单与铺货动作。" if country else ""),
        "items": [],
    }


def _build_official_channel_actions(product: dict[str, Any], focal: str) -> dict[str, Any]:
    from app.domains.projects import launch_assembly

    snapshot = _official_channels_snapshot()
    channels = snapshot["channels"]
    top_channels = sorted(channels, key=lambda c: -_int0(c.get("followers")))[:5]
    try:
        synergy = launch_assembly._official_synergy_block(product, focal)
    except Exception as exc:  # noqa: BLE001
        synergy = _section_error(exc)

    return {
        "status": "ready" if channels else "empty",
        "reason": "" if channels else "官方账号口径 0 行(vkpi_employee_channels 官方过滤)。",
        "official_count": snapshot["official_count"],
        "metrics_rows": snapshot["metrics_rows"],
        "top_channels": [
            {"handle": _text(c.get("account_handle"), 100), "platform": _text(c.get("platform"), 30),
             "followers": _int0(c.get("followers")), "total_views": _int0(c.get("total_views")),
             "latest_snapshot": _text(c.get("latest_snapshot"), 30) or None}
            for c in top_channels
        ],
        "suggestions": synergy.get("suggestions") or [],
        "suggestions_status": _text(synergy.get("status"), 30),
        "suggestions_reason": _text(synergy.get("reason"), 300),
        "basis": (
            "vkpi_employee_channels(官方口径)× vkpi_channel_metrics 最新快照真读;"
            "『哪个号发什么』= launch_assembly 官号协同块(vkpi_channel_post_metrics 形式×平台×时段聚合)。"
        ),
    }


def _build_shopify_actions(sku: str, goal: str) -> dict[str, Any]:
    items = [
        {"action": f"为 {sku} 生成 KOL 专属短链(viltroxvia.com 域,逐人一码)",
         "note": "短链→webhook 归因是转化验证的前提;先建码,后放量。"},
        {"action": "独立站落地页:该 SKU 专页 + KOL 内容嵌入位(占位)",
         "note": "落地页上线前短链先指向现有产品页,诚实降级。"},
        {"action": f"佣金码占位:{_rule_ref('commission_band')}",
         "note": "阶梯激励在跑通首批归因后再开,不预开。"},
    ]
    if goal == "conversion":
        items.append({"action": "转化目标位:优先把短链埋进表现最好的前 3 条 KOL 视频描述",
                      "note": "按每变体曝光门槛裁决后再扩(规则库 per_variant_min_impressions)。"})
    return {
        "status": "template",
        "items": items,
        "honesty_note": (
            "本地库无 Shopify 订单数据——以上为模板建议,转化闭环需上线 webhook 归因后才可验证;"
            "本段不宣称任何已发生的订单效果。"
        ),
    }


def _build_content_angles(*, persona: dict[str, Any] | None, segments: dict[str, Any], focal: str) -> dict[str, Any]:
    angles = list((persona or {}).get("promotion_angles_json") or [])
    seg_items = []
    for seg in (segments.get("items") or [])[:5]:
        video = seg.get("video") or {}
        seg_items.append({
            "segment_type": _text(seg.get("segment_type"), 40),
            "description": _text(seg.get("description"), 160),
            "tags": [_text((t or {}).get("label") or (t or {}).get("key"), 40) for t in (seg.get("tags") or [])[:4]],
            "video_views": video.get("view_count"),
            "platform": _text(video.get("platform"), 30),
        })
    templates = [
        {"key": "awe",
         "template": f"惊叹开场:首 2 秒直接给 {focal + 'mm ' if focal else ''}大光圈虚化/成片对比钩子,不留铺垫",
         "basis": _rule_ref("hook_2s_retention_a")},
        {"key": "identity",
         "template": "身份认同:向『人像/婚礼创作者的主力定焦』身份靠,让目标人群自我代入",
         "basis": "情绪两轴+器材四情绪规则库口径(GTM-2 落表)"},
        {"key": "before_after",
         "template": "before-after:套头 vs 本品同场景对比,完播由结果揭晓驱动",
         "basis": _rule_ref("tiktok_completion_viral")},
    ]
    return {
        "status": "ready" if (angles or seg_items) else "template_only",
        "persona_angles": angles,
        "persona_angles_status": "ready" if angles else "data_missing",
        "high_performing_segments": seg_items,
        "segments_status": _text(segments.get("status"), 30),
        "segments_note": "段级资产=final_v1 深析文本索引,按所属视频播放降序;只给归纳描述。",
        "rule_templates": templates,
        "basis": "persona.promotion_angles_json + creative_segments 高表现段 + 规则库三模板(awe/身份/before-after)。",
    }


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """strategy_sim 方案 → 摘要(成员明细留 kol_candidates 段,这里只给合计)。"""
    if plan.get("status") != "ready":
        return {"key": plan.get("key"), "name": plan.get("name"),
                "status": _text(plan.get("status"), 20), "reason": _text(plan.get("reason"), 200)}
    cost = plan.get("cost_usd") or {}
    views = plan.get("expected_views") or {}
    return {
        "key": plan.get("key"),
        "name": plan.get("name"),
        "status": "ready",
        "member_count": plan.get("member_count"),
        "cost_usd_p50_total": cost.get("p50_total"),
        "budget_utilization_pct": cost.get("utilization_pct"),
        "expected_views_p50_total": views.get("p50_total"),
        "views_unknown_members": views.get("views_unknown_members"),
        "cost_per_1k_views_p50": plan.get("cost_per_1k_views_p50"),
        "risk_level": (plan.get("risk") or {}).get("level"),
        "estimate_only": bool(plan.get("estimate_only")),
    }


def _build_budget_mix(*, budget_usd: float, goal: str, cands: list[dict[str, Any]],
                      roster_mod: Any, engines_missing: str) -> dict[str, Any]:
    from app.domains.market import strategy_sim as ss

    tiers = [
        {"key": "conservative", "label": "保守 70/10/20", "allocation": {"kol": 70, "official": 10, "paid_amplify": 20},
         "note": "验证期默认档:大头给 KOL 真内容,小额放大试触发线。"},
        {"key": "balanced", "label": "平衡 50/50", "allocation": {"kol": 50, "paid_amplify": 50},
         "note": "触发线部分达成后的中间档。"},
        {"key": "aggressive", "label": "激进 40/60", "allocation": {"kol": 40, "paid_amplify": 60},
         "note": "只有 forecast 段 30 天触发线达成才升到此档。"},
    ]
    recommended = {"exposure": "balanced", "conversion": "balanced",
                   "content": "conservative", "channel": "conservative"}[goal]

    priceable = [c for c in cands if c.get("cost_ready")]
    if not priceable:
        sim_summary: dict[str, Any] = {"status": "empty", "plans": [],
                                       "reason": "候选池无人可估价,三方案模拟无从跑起,诚实空态。"}
    else:
        head_picked, head_spent, head_note = ss._pick_head(priceable, budget_usd, ss.HEAD_MAX)
        tail_picked, tail_spent, tail_note = ss._pick_longtail(priceable, budget_usd, ss.TAIL_MAX)
        mix_picked, mix_spent, mix_note = ss._pick_hybrid(priceable, budget_usd, ss.TAIL_MAX)
        plans = [
            ss._plan_payload("head_concentration", "A 头部集中", "预算全给预测战绩 top 3-5 人",
                             head_picked, head_spent, head_note, budget_usd, roster_mod),
            ss._plan_payload("longtail_spread", "B 长尾铺量", "小额铺 15-20 个 nano/micro",
                             tail_picked, tail_spent, tail_note, budget_usd, roster_mod),
            ss._plan_payload("hybrid_5050", "C 混合 50/50", "一半预算给头部(≤3 人),一半铺长尾",
                             mix_picked, mix_spent, mix_note, budget_usd, roster_mod),
        ]
        sim_goal = goal if goal in ("exposure", "conversion", "content") else None
        rec = ss._recommendation(plans, sim_goal)
        sim_summary = {
            "status": "ready",
            "plans": [_plan_summary(p) for p in plans],
            "recommendation": {
                "status": _text(rec.get("status"), 20),
                "pick": _text(rec.get("pick") or rec.get("recommended_key"), 40) or None,
                "reason": _text(rec.get("reason"), 300) or None,
                "goal_note": _text(rec.get("goal_note"), 200) or None,
            },
            "note": (
                "三方案摘要复用 strategy_sim 纯函数积木;预测引擎走 dry_run shim,"
                "vkpi_forecast_log 零新增(与 /strategy-sim 正式端点的落流水行为刻意不同)。"
            ),
        }
        if engines_missing:
            sim_summary["engines_missing"] = engines_missing

    return {
        "status": "ready",
        "budget_usd": _usd(budget_usd),
        "goal": goal,
        "tiers": tiers,
        "recommended_tier": recommended,
        "recommended_note": f"goal={goal} 的默认档;升/降档条件见 forecast 段触发/撤退线,参数不假装比数据聪明。",
        "sim_summary": sim_summary,
    }


def _build_risks(*, cands: list[dict[str, Any]], country: str | None,
                 persona: dict[str, Any] | None) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    priceable = [c for c in cands if c.get("cost_ready")]
    if priceable and all(c.get("cost_method") == "cpm_benchmark_v0" for c in priceable):
        risks.append({"risk": "候选报价全部为 CPM 行业基准兜底(库内实报价仅个位数)", "severity": "high",
                      "mitigation": "成本区间只作谈判锚点;首批外联即采集实报价回填 rate_card。"})
    no_fc = sum(1 for c in cands if not c.get("forecast_ready"))
    if cands and no_fc / len(cands) >= 0.5:
        risks.append({"risk": f"候选中 {no_fc}/{len(cands)} 人预测缺样本(无播放流水)", "severity": "medium",
                      "mitigation": "预测缺席成员按 0 计并如实计数;优先给这些人补视频证据采集。"})
    if country:
        risks.append({"risk": "受众地理置信低:创作者国别≠受众国别,地域匹配基于档案字段", "severity": "medium",
                      "mitigation": "v1 只做排序前置不做硬过滤;受众地理分层(评论语言法)落地后收紧。"})
    if not (persona or {}).get("ideal_persona"):
        risks.append({"risk": "该 SKU 无 persona 档案,主打人群/角度为模板推断", "severity": "medium",
                      "mitigation": "跑 build_product_personas 离线批补齐后重看本页。"})
    risks.append({"risk": "Dealer/订单两条真实业务数据链缺席,渠道与转化两段只能模板+占位", "severity": "high",
                  "mitigation": "GTM-2 Dealer 导入 + Shopify webhook 上线后,两段自动变真建议。"})
    risks.append({"risk": "学习账本 outcome 裁决率低,历史命中率参考价值有限", "severity": "low",
                  "mitigation": "GTM-4 裁决流提升样本后,预判置信度按账本校准。"})
    return risks


_GAP_NOTES = {
    "data_missing": "数据前置未导入",
    "template": "本地无真实数据,模板输出",
    "template_only": "persona/段级资产缺席,仅规则模板",
    "empty": "窗口内零信号",
    "unavailable": "上游引擎缺席",
    "error": "聚合出错(见该段 reason)",
}


def _build_data_gaps(section_status: dict[str, str], *, persona_missing: bool) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = [
        {"section": name, "status": status, "note": _GAP_NOTES.get(status, status)}
        for name, status in section_status.items()
        if status in _GAP_NOTES
    ]
    if persona_missing and not any(g["section"] == "content_angles" for g in gaps):
        gaps.append({"section": "content_angles", "status": "partial", "note": "该 SKU persona 缺席,角度靠规则模板"})
    gaps.append({"section": "external_radar", "status": "not_wired",
                 "note": "外部市场雷达(Reddit/Trends/电商榜)GTM-5 接入,本预览只用库内信号"})
    gaps.append({"section": "conversion_attribution", "status": "not_wired",
                 "note": "本地无 Shopify 订单;转化验证待 webhook 归因上线"})
    return gaps


def _build_action_inbox_items(*, sku: str, goal: str, country: str | None, budget_usd: float,
                              kol_section: dict[str, Any], official_section: dict[str, Any],
                              budget_section: dict[str, Any], content_section: dict[str, Any],
                              shopify_section: dict[str, Any], forecast: Any) -> dict[str, Any]:
    """materialize 预览(GTM-Loop L1 bet 合约版):零落库红线不变。

    每条条目在既有六要素之上并列 bet 七要素(why 带 gtm_plan_id/forecast 段落锚、
    expected 量化指标、escalate_if/retreat_if、review_at 按 action_type 默认复盘天数);
    构建实现在 gtm_bets.build_action_inbox_items(纯函数);真落库走
    materialize.materialize_plan(dry_run=False),每条 requires_approval=True 人审。
    """
    from app.domains.market_brain import gtm_bets

    return gtm_bets.build_action_inbox_items(
        sku=sku, goal=goal, country=country, budget_usd=budget_usd,
        kol_section=kol_section, official_section=official_section,
        budget_section=budget_section, content_section=content_section,
        shopify_section=shopify_section, forecast=forecast,
    )


def _stage(stage: str, label: str, metric: str, threshold: str, confidence: str,
           basis: str, source: str) -> dict[str, Any]:
    return {"stage": stage, "label": label, "metric": metric, "threshold": threshold,
            "confidence": confidence, "basis": basis, "source": source}


def _build_success_metrics(goal: str) -> dict[str, Any]:
    emphasis = {
        "exposure": {"hook_watch", "completion_engagement"},
        "conversion": {"click", "conversion"},
        "content": {"hook_watch", "completion_engagement"},
        "channel": {"fulfillment_post", "conversion"},
    }[goal]
    r = GROWTH_RULES
    stages = [
        _stage("outreach_reply", "外联回复", "有效回复率", "≥10% 目标(<5% 触发撤退线)",
               "low", r["outreach_reply_floor"]["statement"], r["outreach_reply_floor"]["source"]),
        _stage("fulfillment_post", "履约发布", "签收→30 天内发布转化", "≥60% 目标(自建基线,履约漏斗历史样本小)",
               "low", "履约漏斗自有口径,样本积累后校准", _SELF_BASELINE),
        _stage("hook_watch", "开头留存", "2s hook 留存", "≥40% = A 档",
               "medium", r["hook_2s_retention_a"]["statement"], r["hook_2s_retention_a"]["source"]),
        _stage("completion_engagement", "完播/互动", "完播率 + ER", "TT 完播 ≥70% 为病毒门槛;ER 高于账号 30 天基线",
               "medium", r["tiktok_completion_viral"]["statement"], r["tiktok_completion_viral"]["source"]),
        _stage("click", "点击", "YT CTR / 短链点击", "YT CTR ≥4% 且留存 ≥40%;每变体 ≥1000 曝光后判胜负",
               "medium", r["yt_ctr_floor"]["statement"] + ";" + r["per_variant_min_impressions"]["statement"], _RULE_SOURCE),
        _stage("conversion", "转化", "短链归因下单 / 佣金出单", "佣金 10-15% 起步;CPM 效率优于行业锚点才加码",
               "low", r["commission_band"]["statement"] + ";本地无订单数据,上线 webhook 归因后可验证", _RULE_SOURCE),
    ]
    for s in stages:
        s["emphasized"] = s["stage"] in emphasis
    return {"status": "ready", "goal": goal, "stages": stages,
            "note": _RULE_NOTE + f"goal={goal} 的侧重段以 emphasized 标出。"}


# ── 主入口 ──────────────────────────────────────────────────────────


from app.domains.market_brain.conversion_readiness import build_conversion_readiness as _build_conversion_readiness  # noqa: E402


def build_preview(
    sku: str,
    country: str | None = None,
    budget_usd: float = 3000,
    goal: str = "exposure",
    window_days: int = 30,
) -> dict[str, Any]:
    """GTM Plan 纯读预览:{public_plan: 11 段, meta:{generated_at, coverage, data_gaps}}。

    SKU 不存在抛 LookupError(路由转 404);goal 非法抛 ValueError(路由转 422)。
    private_evidence 不出现在返回里(结构见 PRIVATE_EVIDENCE_SCHEMA)。
    """
    sku = _text(sku, 120)
    if not sku:
        raise LookupError("sku is required")
    goal = _text(goal, 20).lower() or "exposure"
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {GOALS}, got {goal!r}")
    budget = _float_or_none(budget_usd) or 3000.0
    if budget <= 0:
        raise ValueError(f"budget_usd must be positive, got {budget_usd!r}")
    window_days = max(7, min(90, _int0(window_days) or 30))
    country_clean = _text(country, 8).upper() or None

    from app.domains.projects import launch_assembly

    product = launch_assembly._resolve_product(sku)
    if not product:
        raise LookupError(f"SKU not found in vkpi_products: {sku}")
    product_basic = launch_assembly._product_basic(product)
    sku_code = _text(product_basic.get("sku"), 120) or sku
    focal = launch_assembly._focal_token(product)

    # ── 共享数据装载(逐项守卫,单源失败不拖垮整卡) ──
    def _guard(name: str, fn: Any, fallback: Any = None) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("gtm_preview %s failed: %s", name, exc)
            return fallback if fallback is not None else _section_error(exc)

    from app.domains.content.creative_segments import segment_search
    from app.domains.costs.product_persona import get_product_persona
    from app.domains.market.category_tracks import tracks
    from app.domains.market.industry_benchmark import benchmark

    tracks_payload = _guard("tracks", tracks)
    bench = _guard("benchmark", benchmark)
    persona = _guard("persona", lambda: get_product_persona(sku_code), fallback={})
    pool = _guard("candidate_pool", lambda: launch_assembly._candidate_pool(sku_code, _POOL_LIMIT),
                  fallback={"status": "error", "items": []})
    try:
        cands, roster_mod, engines_missing = _build_candidates(sku_code, pool.get("items") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("gtm_preview candidates failed for %s: %s", sku_code, exc)
        cands, roster_mod, engines_missing = [], None, _text(str(exc), 200)
    segments = _guard("segments", lambda: segment_search(focal=focal or "", limit=6))

    priceable_count = sum(1 for c in cands if c.get("cost_ready"))
    focal_id = f"focal:{focal}mm" if focal else ""
    track85 = next((o for o in (tracks_payload.get("opportunities") or [])
                    if o.get("track_id") == focal_id), None)
    bench_cell = None
    if focal:
        bench_cell = next((c for c in ((bench.get("focal_grid") or {}).get("cells") or [])
                           if _text(c.get("focal"), 20) == f"{focal}mm"), None)

    # ── 11 段(每段独立守卫,单段失败诚实 {status:error}) ──
    official_section = _guard("official_channel_actions", lambda: _build_official_channel_actions(product, focal))
    official_count = _int0(official_section.get("official_count")) if isinstance(official_section, dict) else 0

    thesis = _guard("thesis", lambda: _build_thesis(
        track=track85, pool_status=_text(pool.get("status"), 30), priceable=priceable_count,
        persona=persona, country=country_clean, goal=goal, official_count=official_count))
    forecast = _guard("forecast", lambda: _build_forecast(
        window_days=window_days, sku=sku_code, country=country_clean, track=track85,
        bench_cell=bench_cell, bench_conf=(bench.get("confidence") or {}) if isinstance(bench, dict) else {},
        priceable=priceable_count))
    # 措辞纪律守卫:任何预判条目缺触发/撤退线或含绝对化词根 → 该条不出货
    if isinstance(forecast, list):
        disciplined = []
        for entry in forecast:
            blob = " ".join(_text(entry.get(k), 2000) for k in ("statement", "escalate_if", "retreat_if", "signals_summary"))
            if not entry.get("escalate_if") or not entry.get("retreat_if") or absolute_wording_hits(blob):
                logger.warning("gtm_preview forecast entry dropped by wording discipline: %s", entry.get("horizon_days"))
                continue
            disciplined.append(entry)
        forecast = disciplined

    market_opportunity = _guard("market_opportunity", lambda: _build_market_opportunity(
        tracks_payload=tracks_payload if isinstance(tracks_payload, dict) else {},
        bench=bench if isinstance(bench, dict) else {}, focal=focal,
        sku=sku_code, country=country_clean))
    kol_candidates = _guard("kol_candidates", lambda: _build_kol_candidates(
        pool=pool, cands=cands, country=country_clean, focal=focal))
    dealer_targets = _guard("dealer_targets", lambda: _build_dealer_targets(country_clean))
    shopify_actions = _guard("shopify_indie_site_actions", lambda: _build_shopify_actions(sku_code, goal))
    content_angles = _guard("content_angles", lambda: _build_content_angles(
        persona=persona, segments=segments if isinstance(segments, dict) else {}, focal=focal))
    budget_mix = _guard("budget_mix", lambda: _build_budget_mix(
        budget_usd=budget, goal=goal, cands=cands, roster_mod=roster_mod, engines_missing=engines_missing))
    risks = _guard("risks", lambda: _build_risks(cands=cands, country=country_clean, persona=persona))
    action_inbox_items = _guard("action_inbox_items", lambda: _build_action_inbox_items(
        sku=sku_code,
        goal=goal,
        country=country_clean,
        budget_usd=budget,
        kol_section=kol_candidates if isinstance(kol_candidates, dict) else {},
        official_section=official_section if isinstance(official_section, dict) else {},
        budget_section=budget_mix if isinstance(budget_mix, dict) else {},
        content_section=content_angles if isinstance(content_angles, dict) else {},
        shopify_section=shopify_actions if isinstance(shopify_actions, dict) else {},
        forecast=forecast if isinstance(forecast, list) else []))
    success_metrics = _guard("success_metrics", lambda: _build_success_metrics(goal))
    conversion_readiness = _guard("conversion_readiness", lambda: _build_conversion_readiness(
        sku=sku_code, product=product_basic))

    def _status_of(section: Any, ready_when_list: bool = False) -> str:
        if ready_when_list:
            return "ready" if isinstance(section, list) and section else "empty"
        return _text(section.get("status"), 30) if isinstance(section, dict) else "error"

    section_status = {
        "thesis": _status_of(thesis),
        "forecast": _status_of(forecast, ready_when_list=True),
        "market_opportunity": _status_of(market_opportunity),
        "kol_candidates": _status_of(kol_candidates),
        "dealer_targets": _status_of(dealer_targets),
        "official_channel_actions": _status_of(official_section),
        "shopify_indie_site_actions": _status_of(shopify_actions),
        "content_angles": _status_of(content_angles),
        "budget_mix": _status_of(budget_mix),
        "action_inbox_items": _status_of(action_inbox_items),
        "success_metrics": _status_of(success_metrics),
        "conversion_readiness": _status_of(conversion_readiness),
    }
    ready_states = {"ready", "preview", "template", "template_only", "data_missing"}
    sections_ready = sum(1 for s in section_status.values() if s in ready_states)

    data_gaps = _guard("data_gaps", lambda: _build_data_gaps(
        section_status, persona_missing=not (persona or {}).get("ideal_persona")))
    if not isinstance(data_gaps, list):
        data_gaps = [{"section": "data_gaps", "status": "error",
                      "note": _text(data_gaps.get("reason"), 200) if isinstance(data_gaps, dict) else ""}]
    risks_out = risks if isinstance(risks, list) else [risks]

    public_plan = {
        "sku": sku_code,
        "requested_sku": sku,
        "product": product_basic,
        "inputs": {"country": country_clean, "budget_usd": _usd(budget), "goal": goal, "window_days": window_days},
        "thesis": thesis,
        "forecast": forecast,
        "market_opportunity": market_opportunity,
        "kol_candidates": kol_candidates,
        "dealer_targets": dealer_targets,
        "official_channel_actions": official_section,
        "shopify_indie_site_actions": shopify_actions,
        "content_angles": content_angles,
        "budget_mix": budget_mix,
        "risks": risks_out,
        "data_gaps": data_gaps,
        "action_inbox_items": action_inbox_items,
        "success_metrics": success_metrics,
        "conversion_readiness": conversion_readiness,
        # 附录 A·8 段强制排序视图(优势放大裁决 2026-07-07):页面按「先做什么」呈现,
        # 键指向本对象既有段,不复制数据。
        "ordered_view": [
            {"key": "go_nogo", "section": "thesis", "title": "Go / No-Go"},
            {"key": "market_thesis", "section": "market_opportunity", "title": "Market Thesis"},
            {"key": "channel_mix", "section": "budget_mix", "title": "Channel Mix"},
            {"key": "content_proof", "section": "content_angles", "title": "Content Proof"},
            {"key": "conversion_readiness", "section": "conversion_readiness", "title": "Conversion Readiness"},
            {"key": "action_roadmap", "section": "action_inbox_items", "title": "Action Roadmap"},
            {"key": "escalate_retreat_lines", "section": "forecast", "title": "Escalate / Retreat Lines"},
            {"key": "learning_plan", "section": "success_metrics", "title": "Learning Plan"},
        ],
    }
    return {
        "public_plan": public_plan,
        "meta": {
            "generated_at": _utcnow_iso(),
            "method": METHOD,
            "coverage": {"sections_total": len(section_status), "sections_ready": sections_ready,
                         "sections": section_status},
            "data_gaps": data_gaps,
            "llm_calls": False,
            "provider_calls": False,
            "writes": False,
            "note": (
                "纯读预览:聪明藏在系统里,结果露在路线图上;评分明细/原始评论/竞品监控细节/除名记录"
                "一律沉在内部不出前端,预判全部条件化(触发+撤退线),外部雷达 GTM-5 待接。"
            ),
        },
    }


__all__ = ["build_preview", "GOALS", "GROWTH_RULES", "PRIVATE_EVIDENCE_SCHEMA", "absolute_wording_hits"]
