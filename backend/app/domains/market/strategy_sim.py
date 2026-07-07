"""S3 商业策略评估模拟器(strategy_sim v0)—— 「这笔预算怎么花最划算」what-if 决定性模拟。

三个策略方案并排模拟(同输入同输出,零 LLM 零采集零写库):
  A head_concentration 头部集中:预算全给预测战绩(forecast p50)top 3-5 人;
  B longtail_spread     长尾铺量:小额铺 15-20 个 nano/micro(报价 p50 从低到高);
  C hybrid_5050         混合:预算 50/50 —— 一半给头部(≤3 人),一半铺长尾。

每方案全复用第4轮三引擎(守卫 import,绝不重造):
  - rate_card.estimate_rate        → 每人成本(p50/low/high + method/confidence)
  - performance_forecast.forecast_for_kol → 每人预期播放区间(p10/p50/p90 逐人求和成方案区间)
  - roster_optimizer.optimize_roster      → 方案去重触达(coverage.total_dedup_reach)

候选池:launch_assembly._candidate_pool(new_launch_match 决定性 dry-run,与发射台同源口径)。
推荐:按 p50 成本效率(每千播放成本)升序;「全员 CPM 兜底价」(所有成员报价均为
cpm_benchmark_v0 行业基准兜底、无一条真实报价)的方案降级 estimate_only 后置并警示 ——
对齐第4轮护栏精神:基准折算只是谈判锚点,不能当真实成交价拿去排兵布阵。

GTM-1 补参(向后兼容,默认 None=旧行为逐字不变):
  - country:候选池按受众地域轻过滤 —— audience_geo(守卫 import)逐候选读受众分布,
    三档降序(命中该国 > 无地理数据 > 有数据但不含该国),档内维持原策略排序键;
    无数据的候选不淘汰只降序(参数不假装比数据聪明)。
  - goal:推荐排序权重切换 —— exposure=去重触达优先 / conversion=每千播成本优先(现状默认)
    / content=成员人数(内容产能代理)优先;estimate_only 后置护栏不因 goal 放松。
  两参如何影响排序全部写进 basis(candidate_pool.audience_geo_filter /
  plans[].basis.country_filter / recommendation.basis.goal_weighting)可追溯。

红线:一切数字带 basis/method/confidence 可追溯;数据不足诚实空态/低置信,绝不编造;
只用库内真数据(不联网不引外部行情);零触 viltrox_fit_score / rule_v0。
compat 约定:SQL 占位符 ?;无字面 percent;函数内懒 import get_conn。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "strategy_sim_v0"

# 候选池上限:与 roster_optimizer.MAX_CANDIDATES(60)对齐,进组合优化不被拒。
POOL_LIMIT = 60

# 方案 A:头部集中 —— 按预测 p50 排序取 top,人数 3~5。
HEAD_MAX = 5
HEAD_MIN = 3
# 方案 B:长尾铺量 —— nano/micro 目标 15~20 人(受 max_roster 上限约束)。
TAIL_TARGET_MIN = 15
TAIL_MAX = 20
# 方案 C:混合 —— 头部最多 3 人吃一半预算,另一半铺长尾。
HYBRID_HEAD_MAX = 3
HYBRID_SPLIT = 0.5

# 长尾口径:粉丝层级 nano/micro(与 rate_card.TIER_BOUNDS 同源,<100k)。
LONGTAIL_TIERS = ("nano", "micro")

# 风险评级规则(样本置信度聚合,纯计数决定性):
# 成员 forecast confidence 非 high/medium(即 low/缺失/empty)占比
# >=50% → high;>=20% → medium;否则 low。规则原文进 basis 可追溯。
RISK_LOW_SHARE_HIGH = 0.5
RISK_LOW_SHARE_MEDIUM = 0.2
RISK_RULE_TEXT = (
    "成员预测置信度聚合:forecast confidence 非 high/medium 的成员占比 "
    ">=50% 评 high,>=20% 评 medium,否则 low;无成员评 unknown。"
)

# GTM-1 补参:goal 推荐排序口径(conversion=现状默认;None=旧行为逐字不变)。
GOALS = ("exposure", "conversion", "content")
GOAL_RULES = {
    "exposure": (
        "goal=exposure:按方案去重触达 total_dedup_reach 降序推荐(触达缺席退 "
        "views_p50 总和降序);「全员 CPM 兜底价」方案降级 estimate_only 一律后置"
        "(护栏不因 goal 放松)。"
    ),
    "conversion": (
        "goal=conversion(与默认口径一致):按 p50 成本效率(每千播放成本)升序推荐;"
        "「全员 CPM 兜底价」方案降级 estimate_only 一律后置(对齐第4轮护栏精神:基准折算非成交价)。"
    ),
    "content": (
        "goal=content:按方案成员人数降序推荐(内容产能代理:人越多可产出内容越多),"
        "人数并列按每千播成本升序;「全员 CPM 兜底价」方案降级 estimate_only 一律后置。"
    ),
}

# GTM-1 补参:country 受众地域三档(0 命中 > 1 无数据 > 2 有数据不含该国)。
GEO_TIER_MATCHED = 0
GEO_TIER_NO_DATA = 1
GEO_TIER_UNMATCHED = 2
COUNTRY_RULE_TEXT = (
    "country 补参:候选按受众地域三档排序(0=audience_geo 命中该国,按档优先;"
    "1=无地理数据;2=有数据但不含该国),档内维持原策略排序键(头部仍按预测播放、"
    "长尾仍按报价);无数据候选不淘汰只降序 —— 轻影响,参数不假装比数据聪明。"
)


# ── 小工具 ───────────────────────────────────────────────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _usd(value: float) -> float:
    return round(float(value), 2)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 引擎守卫装载(兄弟件缺席诚实降级,不重造) ─────────────────────────


def _load_engines() -> tuple[Any, Any, Any, str]:
    """守卫 import 第4轮三引擎;缺哪个如实报哪个(理论上都在,仍守卫)。"""
    missing: list[str] = []
    rate_card = forecast = roster = None
    try:
        from app.domains.kol import rate_card as rate_card  # type: ignore[no-redef]
    except ImportError:
        missing.append("app.domains.kol.rate_card")
    try:
        from app.domains.kol import performance_forecast as forecast  # type: ignore[no-redef]
    except ImportError:
        missing.append("app.domains.kol.performance_forecast")
    try:
        from app.domains.kol import roster_optimizer as roster  # type: ignore[no-redef]
    except ImportError:
        missing.append("app.domains.kol.roster_optimizer")
    return rate_card, forecast, roster, ";".join(missing)


# ── 候选池(复用 launch_assembly 同源口径) ───────────────────────────


def _candidate_pool(sku: str) -> dict[str, Any]:
    """new_launch_match 决定性 dry-run 候选(launch_assembly._candidate_pool 同一真源)。"""
    try:
        from app.domains.projects import launch_assembly
    except ImportError as exc:
        return {"status": "unavailable", "reason": f"launch_assembly import failed: {exc}", "items": []}
    try:
        return launch_assembly._candidate_pool(sku, POOL_LIMIT)
    except Exception as exc:  # noqa: BLE001 — 候选池失败诚实降级
        logger.warning("strategy_sim candidate pool failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": _text(str(exc), 300), "items": []}


def _load_followers(db: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
    """批量读候选粉丝数/平台(vkpi_kol_pool 单查,compat 占位符 ?)。"""
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    sql = (
        "SELECT id, followers, avg_views, platform FROM vkpi_kol_pool WHERE id IN ("
        + placeholders
        + ")"
    )
    try:
        rows = db.execute(sql, tuple(int(k) for k in ids)).fetchall()
        return {int(dict(r)["id"]): dict(r) for r in rows}
    except Exception as exc:  # noqa: BLE001 — 粉丝层级读挂只影响长尾筛选,诚实降级
        logger.warning("strategy_sim followers load failed: %s", exc)
        return {}


def _tier_for(rate_card: Any, followers: int | None) -> str | None:
    """粉丝层级(rate_card.TIER_BOUNDS 唯一真源);粉丝缺失诚实 None,不硬猜。"""
    if followers is None or followers <= 0 or rate_card is None:
        return None
    try:
        return str(rate_card._tier_for_followers(int(followers)))
    except Exception:  # noqa: BLE001
        return None


def _normalize_country(raw: Any) -> tuple[str, str | None]:
    """country 参数 → ISO2 大写码;复用 geo_ensemble._country_code(守卫 import)查中文名。
    识别不了不硬编:按原文大写匹配 + note 诚实说明(结果=全员不命中,只降序不淘汰)。"""
    text = " ".join(str(raw or "").split()).strip()
    if not text:
        return "", None
    code = text
    try:
        from app.domains.audience import geo_ensemble
        code = str(geo_ensemble._country_code(text) or text)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — 归一化失败按原文匹配,不拦主流程
        logger.warning("strategy_sim country normalize failed text=%s: %s", text, exc)
    code = code.strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        return code, None
    return code, (
        f"country={_text(text, 60)} 未识别为 ISO2 码,按原文大写匹配"
        "(大概率全员不命中:仅降序不淘汰,排序近似无参)。"
    )


def _annotate_audience_geo(
    cands: list[dict[str, Any]], country_code: str, db: Any,
) -> dict[str, Any]:
    """country 补参:逐候选读 audience_geo(守卫 import,纯读),标三档 _geo_tier。
    无数据的候选不淘汰只降序(tier 1);模块缺席全员 tier 1 = 排序退化为无参,诚实报因。"""
    try:
        from app.domains.audience import geo_ensemble
    except ImportError as exc:
        for c in cands:
            c["_geo_tier"] = GEO_TIER_NO_DATA
            c["_geo_status"] = "module_missing"
            c["_geo_match_pct"] = None
        return {
            "status": "unavailable",
            "country": country_code,
            "reason": f"audience geo 读端缺席,country 过滤未生效(排序同无参):{_text(str(exc), 160)}",
            "rule": COUNTRY_RULE_TEXT,
        }
    matched = no_data = unmatched = 0
    for c in cands:
        pct: float | None = None
        try:
            geo = geo_ensemble.audience_geo(int(c["kol_pool_id"]), conn=db)
            countries = geo.get("countries") or []
        except Exception as exc:  # noqa: BLE001 — 单人地理读挂不拖垮,按无数据降序
            logger.warning(
                "strategy_sim audience_geo failed for kol=%s: %s", c.get("kol_pool_id"), exc
            )
            geo, countries = {}, []
        if not countries:
            tier, status = GEO_TIER_NO_DATA, "no_data"
            no_data += 1
        else:
            pct = 0.0
            for item in countries:
                if str(item.get("code") or "").strip().upper() == country_code:
                    pct = _float_or_none(item.get("pct")) or 0.0
                    break
            if pct > 0:
                tier, status = GEO_TIER_MATCHED, "matched"
                matched += 1
            else:
                tier, status = GEO_TIER_UNMATCHED, "not_matched"
                unmatched += 1
        c["_geo_tier"] = tier
        c["_geo_status"] = status
        c["_geo_match_pct"] = pct
        c["_geo_confidence"] = _text(geo.get("confidence"), 20) or None
    return {
        "status": "applied",
        "country": country_code,
        "matched": matched,
        "no_data": no_data,
        "unmatched": unmatched,
        "rule": COUNTRY_RULE_TEXT,
        "source": "audience.geo_ensemble.audience_geo(多信号估算口径,纯读守卫 import)",
    }


def _build_candidates(
    sku: str, pool_items: list[dict[str, Any]], rate_card: Any, forecast_mod: Any, db: Any,
) -> list[dict[str, Any]]:
    """逐候选挂三引擎读数(cost + forecast + tier);全 try 包裹,单人失败不拖垮。"""
    ids = [
        _int_or_none(it.get("kol_pool_id"))
        for it in pool_items
        if _int_or_none(it.get("kol_pool_id")) is not None
    ]
    pool_rows = _load_followers(db, [int(k) for k in ids if k is not None])
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in pool_items:
        kid = _int_or_none(item.get("kol_pool_id"))
        if kid is None or kid in seen:
            continue
        seen.add(kid)
        # 成本(rate_card 契约:永不抛异常)
        est: dict[str, Any] = {"status": "unavailable", "reason": "rate_card 模块缺席"}
        if rate_card is not None:
            try:
                est = rate_card.estimate_rate(int(kid), conn=db)
            except Exception as exc:  # noqa: BLE001
                est = {"status": "error", "reason": _text(str(exc), 200)}
        cost_p50 = _float_or_none(est.get("estimated_usd_p50"))
        cost_ready = est.get("status") in ("ready", "ok") and cost_p50 is not None and cost_p50 > 0
        # 预测(forecast 契约:KOL 缺失抛 LookupError,包住)
        fc: dict[str, Any] = {"status": "unavailable", "reason": "performance_forecast 模块缺席"}
        if forecast_mod is not None:
            try:
                fc = forecast_mod.forecast_for_kol(int(kid), sku=sku, conn=db)
            except Exception as exc:  # noqa: BLE001
                fc = {"status": "error", "reason": _text(str(exc), 200)}
        fc_ready = fc.get("status") == "ready" and _float_or_none(fc.get("expected_views_p50")) is not None
        row = pool_rows.get(int(kid)) or {}
        followers = _int_or_none(row.get("followers"))
        out.append(
            {
                "kol_pool_id": int(kid),
                "handle": _text(item.get("handle"), 100),
                "display_name": _text(item.get("display_name"), 120),
                "platform": _text(item.get("platform") or row.get("platform"), 30),
                "country": _text(item.get("country"), 60),
                "match_score": item.get("score"),
                "followers": followers,
                "tier": _tier_for(rate_card, followers),
                "cost_ready": bool(cost_ready),
                "cost_p50": _usd(cost_p50) if cost_ready else None,
                "cost_low": _float_or_none(est.get("estimated_usd_low") if est.get("estimated_usd_low") is not None else est.get("low")),
                "cost_high": _float_or_none(est.get("estimated_usd_high") if est.get("estimated_usd_high") is not None else est.get("high")),
                "cost_method": _text(est.get("method"), 60) or None,
                "cost_confidence": _text(est.get("confidence"), 20) or None,
                "cost_reason": _text(est.get("reason"), 200),
                "forecast_ready": bool(fc_ready),
                "views_p10": _float_or_none(fc.get("expected_views_p10")) if fc_ready else None,
                "views_p50": _float_or_none(fc.get("expected_views_p50")) if fc_ready else None,
                "views_p90": _float_or_none(fc.get("expected_views_p90")) if fc_ready else None,
                "forecast_confidence": _text(fc.get("confidence"), 20) or None,
                "forecast_reason": _text(fc.get("reason"), 200),
            }
        )
    return out


# ── 三方案选人(全部决定性:显式排序键 + kol_pool_id 升序 tie-break) ──


def _greedy_pick(
    ranked: list[dict[str, Any]], budget: float, max_people: int,
) -> tuple[list[dict[str, Any]], float]:
    """按给定顺序贪心装包:报价 p50 累计不超预算、人数不超上限。决定性。"""
    picked: list[dict[str, Any]] = []
    spent = 0.0
    for cand in ranked:
        if len(picked) >= max_people:
            break
        p50 = cand.get("cost_p50")
        if p50 is None:
            continue
        if spent + float(p50) > budget + 1e-9:
            continue
        picked.append(cand)
        spent += float(p50)
    return picked, spent


def _pick_head(cands: list[dict[str, Any]], budget: float, max_people: int) -> tuple[list[dict[str, Any]], float, str]:
    """A 头部集中:预测战绩 p50 降序(tie 小 id 先),取 3~5 人。
    country 补参时受众地域三档(_geo_tier)为首键;无参时全员同档=旧序逐字不变。"""
    ranked = sorted(
        (c for c in cands if c["cost_ready"] and c["forecast_ready"]),
        key=lambda c: (c.get("_geo_tier", GEO_TIER_NO_DATA), -(c["views_p50"] or 0.0), c["kol_pool_id"]),
    )
    picked, spent = _greedy_pick(ranked, budget, max_people)
    note = f"按预测播放 p50 降序取 top(可估价且可预测的候选 {len(ranked)} 人中选出 {len(picked)} 人)"
    if len(picked) < HEAD_MIN:
        note += f";不足目标下限 {HEAD_MIN} 人(预算/候选不足,诚实如实)"
    return picked, spent, note


def _pick_longtail(cands: list[dict[str, Any]], budget: float, max_people: int) -> tuple[list[dict[str, Any]], float, str]:
    """B 长尾铺量:nano/micro 报价 p50 升序(tie 小 id 先),目标 15~20 人。
    country 补参时受众地域三档(_geo_tier)为首键;无参时全员同档=旧序逐字不变。"""
    ranked = sorted(
        (c for c in cands if c["cost_ready"] and c.get("tier") in LONGTAIL_TIERS),
        key=lambda c: (c.get("_geo_tier", GEO_TIER_NO_DATA), c["cost_p50"] or 0.0, c["kol_pool_id"]),
    )
    picked, spent = _greedy_pick(ranked, budget, max_people)
    note = f"nano/micro 池 {len(ranked)} 人按报价 p50 升序小额铺量,选出 {len(picked)} 人"
    if len(picked) < TAIL_TARGET_MIN:
        note += f";不足目标下限 {TAIL_TARGET_MIN} 人(nano/micro 候选或预算不足,诚实如实)"
    return picked, spent, note


def _pick_hybrid(cands: list[dict[str, Any]], budget: float, max_people: int) -> tuple[list[dict[str, Any]], float, str]:
    """C 混合 50/50:一半预算给头部(≤3 人),一半铺长尾;去重不重复选人。"""
    head_budget = budget * HYBRID_SPLIT
    tail_budget = budget - head_budget
    head_ranked = sorted(
        (c for c in cands if c["cost_ready"] and c["forecast_ready"]),
        key=lambda c: (c.get("_geo_tier", GEO_TIER_NO_DATA), -(c["views_p50"] or 0.0), c["kol_pool_id"]),
    )
    heads, head_spent = _greedy_pick(head_ranked, head_budget, HYBRID_HEAD_MAX)
    head_ids = {c["kol_pool_id"] for c in heads}
    tail_ranked = sorted(
        (
            c for c in cands
            if c["cost_ready"] and c.get("tier") in LONGTAIL_TIERS and c["kol_pool_id"] not in head_ids
        ),
        key=lambda c: (c.get("_geo_tier", GEO_TIER_NO_DATA), c["cost_p50"] or 0.0, c["kol_pool_id"]),
    )
    tail_cap = max(0, max_people - len(heads))
    tails, tail_spent = _greedy_pick(tail_ranked, tail_budget, tail_cap)
    picked = heads + tails
    note = (
        f"预算 50/50:头部 {len(heads)} 人(上限 {HYBRID_HEAD_MAX},花 {_usd(head_spent)})"
        f" + 长尾 {len(tails)} 人(花 {_usd(tail_spent)});头尾不重复选人"
    )
    return picked, head_spent + tail_spent, note


# ── 方案聚合(区间求和 + 去重触达 + 风险评级) ────────────────────────


def _dedup_reach(roster_mod: Any, ids: list[int]) -> dict[str, Any]:
    """去重触达(roster_optimizer 契约);模块缺席/失败诚实降级。"""
    if not ids:
        return {"status": "empty", "reason": "方案无成员,触达无从谈起"}
    if roster_mod is None:
        return {"status": "unavailable", "reason": "roster_optimizer 模块缺席,去重触达缺席"}
    try:
        result = roster_mod.optimize_roster(list(ids), max_size=min(len(ids), TAIL_MAX))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": _text(str(exc), 200)}
    coverage = (result or {}).get("coverage") or {}
    return {
        "status": _text(result.get("status"), 30) or "error",
        "total_dedup_reach": _float_or_none(coverage.get("total_dedup_reach")),
        "raw_reach_sum": _float_or_none(coverage.get("raw_reach_sum")),
        "dedup_saved_pct": _float_or_none(coverage.get("dedup_saved_pct")),
        "confidence": _text(result.get("confidence"), 20) or "low",
        "method": _text(result.get("method"), 60),
    }


def _risk_rating(picked: list[dict[str, Any]]) -> dict[str, Any]:
    """风险评级 = 成员预测置信度聚合(纯计数,规则原文进 basis)。"""
    n = len(picked)
    if n == 0:
        return {"level": "unknown", "rule": RISK_RULE_TEXT, "members": 0,
                "confidence_counts": {}, "low_or_missing_share": None}
    counts: dict[str, int] = {}
    low_or_missing = 0
    for c in picked:
        key = c.get("forecast_confidence") if c.get("forecast_ready") else "missing"
        key = key or "missing"
        counts[key] = counts.get(key, 0) + 1
        if key not in ("high", "medium"):
            low_or_missing += 1
    share = low_or_missing / n
    if share >= RISK_LOW_SHARE_HIGH:
        level = "high"
    elif share >= RISK_LOW_SHARE_MEDIUM:
        level = "medium"
    else:
        level = "low"
    return {
        "level": level,
        "rule": RISK_RULE_TEXT,
        "members": n,
        "confidence_counts": dict(sorted(counts.items())),
        "low_or_missing_share": round(share, 4),
    }


def _member_out(c: dict[str, Any]) -> dict[str, Any]:
    out = {
        "kol_pool_id": c["kol_pool_id"],
        "handle": c["handle"],
        "display_name": c["display_name"],
        "platform": c["platform"],
        "country": c["country"],
        "tier": c.get("tier"),
        "followers": c.get("followers"),
        "match_score": c.get("match_score"),
        "cost_usd_p50": c.get("cost_p50"),
        "cost_usd_low": c.get("cost_low"),
        "cost_usd_high": c.get("cost_high"),
        "cost_method": c.get("cost_method"),
        "cost_confidence": c.get("cost_confidence"),
        "expected_views_p10": c.get("views_p10"),
        "expected_views_p50": c.get("views_p50"),
        "expected_views_p90": c.get("views_p90"),
        "forecast_confidence": c.get("forecast_confidence") if c.get("forecast_ready") else None,
        "forecast_note": None if c.get("forecast_ready") else (c.get("forecast_reason") or "预测缺席(无播放样本)"),
    }
    if "_geo_tier" in c:  # 仅 country 补参时出现;无参输出逐字同旧
        out["audience_geo_match"] = {
            "tier": c["_geo_tier"],
            "status": c.get("_geo_status"),
            "match_pct": c.get("_geo_match_pct"),
            "confidence": c.get("_geo_confidence"),
        }
    return out


def _plan_payload(
    key: str, name: str, strategy: str,
    picked: list[dict[str, Any]], spent_p50: float, selection_note: str,
    budget: float, roster_mod: Any,
) -> dict[str, Any]:
    if not picked:
        return {
            "key": key,
            "name": name,
            "strategy": strategy,
            "status": "empty",
            "reason": selection_note + " — 该策略下无人可选(候选/预算不足),诚实空态。",
            "members": [],
            "member_count": 0,
        }
    ids = [c["kol_pool_id"] for c in picked]
    cost_low = sum(float(c["cost_low"]) for c in picked if c.get("cost_low") is not None)
    cost_high = sum(float(c["cost_high"]) for c in picked if c.get("cost_high") is not None)
    fc_members = [c for c in picked if c.get("forecast_ready")]
    views_unknown = len(picked) - len(fc_members)
    views_p10 = sum(float(c["views_p10"] or 0.0) for c in fc_members)
    views_p50 = sum(float(c["views_p50"] or 0.0) for c in fc_members)
    views_p90 = sum(float(c["views_p90"] or 0.0) for c in fc_members)
    cpm_p50 = _usd(spent_p50 / (views_p50 / 1000.0)) if views_p50 > 0 else None
    # 全员 CPM 兜底价 = 无一条真实报价(第4轮护栏:基准折算只是谈判锚点)
    estimate_only = all(c.get("cost_method") == "cpm_benchmark_v0" for c in picked)
    risk = _risk_rating(picked)
    return {
        "key": key,
        "name": name,
        "strategy": strategy,
        "status": "ready",
        "member_count": len(picked),
        "members": [_member_out(c) for c in picked],
        "cost_usd": {
            "p50_total": _usd(spent_p50),
            "low_total": _usd(cost_low),
            "high_total": _usd(cost_high),
            "budget_usd": _usd(budget),
            "leftover_p50": _usd(budget - spent_p50),
            "utilization_pct": round(spent_p50 * 100.0 / budget, 2) if budget > 0 else None,
        },
        "expected_views": {
            "p10_total": round(views_p10),
            "p50_total": round(views_p50),
            "p90_total": round(views_p90),
            "forecastable_members": len(fc_members),
            "views_unknown_members": views_unknown,
            "note": "p10/p50/p90 为成员逐人求和;无预测样本成员计 0 并在 views_unknown_members 如实计数,不硬补均值。",
        },
        "dedup_reach": _dedup_reach(roster_mod, ids),
        "cost_per_1k_views_p50": cpm_p50,
        "risk": risk,
        "estimate_only": estimate_only,
        "estimate_only_warning": (
            "全员报价均为 CPM 行业基准兜底(cpm_benchmark_v0,无一条真实报价)——"
            "成本区间仅是谈判锚点非成交价,本方案降级为 estimate_only,推荐排序后置(对齐第4轮护栏)。"
            if estimate_only else None
        ),
        "basis": {
            "selection_rule": selection_note,
            "engines": {
                "cost": "rate_card.estimate_rate(B件契约,method/confidence 以其 payload 为准)",
                "forecast": "performance_forecast.forecast_for_kol(A件契约,p10/p50/p90 逐人求和)",
                "reach": "roster_optimizer.optimize_roster(D件契约,coverage.total_dedup_reach)",
            },
            "risk_rule": RISK_RULE_TEXT,
            "cpm_formula": "cost_p50_total / (views_p50_total / 1000),views_p50_total<=0 时诚实 None",
        },
    }


# ── 推荐(p50 成本效率排序 + estimate_only 降级后置;goal 补参切换权重) ──


def _rec_confidence(best: dict[str, Any]) -> str:
    if best.get("estimate_only"):
        return "low"        # 全员基准兜底价:排序可用,绝对数不可信
    if (best.get("risk") or {}).get("level") == "low":
        return "medium"     # 预测样本置信可控,但仍是估算非实测
    return "low"


def _goal_metric(plan: dict[str, Any], goal: str) -> tuple[float | None, str]:
    """goal 排序度量(越小越好,降序度量取负);返回 (metric, 度量说明)。"""
    if goal == "exposure":
        reach = _float_or_none((plan.get("dedup_reach") or {}).get("total_dedup_reach"))
        if reach is not None:
            return -reach, f"total_dedup_reach={round(reach)}(降序)"
        views = _float_or_none((plan.get("expected_views") or {}).get("p50_total"))
        if views is not None and views > 0:
            return -views, f"触达缺席退 views_p50_total={round(views)}(降序)"
        return None, "触达与预测播放全缺,不可比"
    if goal == "content":
        members = int(plan.get("member_count") or 0)
        return -float(members), f"member_count={members}(内容产能代理,降序)"
    cpm = _float_or_none(plan.get("cost_per_1k_views_p50"))
    if cpm is not None:
        return cpm, f"cost_per_1k_views_p50={cpm}(升序)"
    return None, "无可比的 p50 成本效率"


def _recommendation_for_goal(plans: list[dict[str, Any]], goal: str) -> dict[str, Any]:
    """goal 补参版推荐:排序主键随 goal 切换;estimate_only 后置护栏与 tie-break 决定性不变。"""
    measured: list[tuple[dict[str, Any], float, str]] = []
    unrankable: list[tuple[dict[str, Any], str | None]] = []
    for p in plans:
        if p.get("status") != "ready":
            unrankable.append((p, None))  # 非 ready:沿用方案自身 reason
            continue
        metric, metric_note = _goal_metric(p, goal)
        if metric is None:
            unrankable.append((p, metric_note))
        else:
            measured.append((p, metric, metric_note))
    ranked = sorted(
        measured,
        key=lambda t: (
            1 if t[0].get("estimate_only") else 0,   # 全员 CPM 兜底价 → 后置(护栏不因 goal 放松)
            t[1],                                    # goal 主度量
            _float_or_none(t[0].get("cost_per_1k_views_p50")) if _float_or_none(t[0].get("cost_per_1k_views_p50")) is not None else float("inf"),
            str(t[0]["key"]),                        # 决定性 tie-break
        ),
    )
    if not ranked:
        return {
            "status": "empty",
            "goal": goal,
            "reason": f"goal={goal} 口径下三方案均无可比度量(无人可选或度量全缺),不硬荐。",
            "ranking": [],
        }
    best = ranked[0][0]
    ranking = [
        {
            "rank": i + 1,
            "key": p["key"],
            "name": p["name"],
            "goal_metric": metric_note,
            "cost_per_1k_views_p50": p.get("cost_per_1k_views_p50"),
            "estimate_only": bool(p.get("estimate_only")),
            "risk_level": (p.get("risk") or {}).get("level"),
        }
        for i, (p, _m, metric_note) in enumerate(ranked)
    ] + [
        {"rank": None, "key": p["key"], "name": p["name"],
         "cost_per_1k_views_p50": None, "estimate_only": bool(p.get("estimate_only")),
         "unrankable_reason": _text(note or p.get("reason") or "无可比度量", 200)}
        for p, note in unrankable
    ]
    payload: dict[str, Any] = {
        "status": "ready",
        "goal": goal,
        "recommended_key": best["key"],
        "recommended_name": best["name"],
        "rule": GOAL_RULES[goal],
        "ranking": ranking,
        "confidence": _rec_confidence(best),
        "basis": {
            "goal_weighting": GOAL_RULES[goal],
            "note": "goal 只切换推荐排序主键,不改三方案选人;estimate_only 后置与字典序 tie-break 决定性不变。",
        },
    }
    if best.get("estimate_only"):
        payload["warning"] = (
            "推荐方案本身也是 estimate_only(全员 CPM 兜底价):当前无任何真实报价可用,"
            "该推荐只是基准折算下的相对排序,签约前务必录入真实报价复算。"
        )
    return payload


def _recommendation(plans: list[dict[str, Any]], goal: str | None = None) -> dict[str, Any]:
    if goal is not None:
        return _recommendation_for_goal(plans, goal)
    rankable = [
        p for p in plans
        if p.get("status") == "ready" and p.get("cost_per_1k_views_p50") is not None
    ]
    unrankable = [p for p in plans if p not in rankable]
    ranked = sorted(
        rankable,
        key=lambda p: (
            1 if p.get("estimate_only") else 0,          # 全员 CPM 兜底价 → 后置
            float(p["cost_per_1k_views_p50"]),           # p50 成本效率(每千播放成本)升序
            str(p["key"]),                                # 决定性 tie-break
        ),
    )
    if not ranked:
        return {
            "status": "empty",
            "reason": "三方案均无可比的 p50 成本效率(无人可选或预测播放全缺),不硬荐。",
            "ranking": [],
        }
    best = ranked[0]
    ranking = [
        {
            "rank": i + 1,
            "key": p["key"],
            "name": p["name"],
            "cost_per_1k_views_p50": p["cost_per_1k_views_p50"],
            "estimate_only": bool(p.get("estimate_only")),
            "risk_level": (p.get("risk") or {}).get("level"),
        }
        for i, p in enumerate(ranked)
    ] + [
        {"rank": None, "key": p["key"], "name": p["name"],
         "cost_per_1k_views_p50": None, "estimate_only": bool(p.get("estimate_only")),
         "unrankable_reason": _text(p.get("reason") or "无可比成本效率", 200)}
        for p in unrankable
    ]
    if best.get("estimate_only"):
        rec_confidence = "low"      # 全员基准兜底价:排序可用,绝对数不可信
    elif (best.get("risk") or {}).get("level") == "low":
        rec_confidence = "medium"   # 预测样本置信可控,但仍是估算非实测
    else:
        rec_confidence = "low"
    payload: dict[str, Any] = {
        "status": "ready",
        "recommended_key": best["key"],
        "recommended_name": best["name"],
        "rule": (
            "按 p50 成本效率(每千播放成本)升序推荐;「全员 CPM 兜底价」方案降级 "
            "estimate_only 一律后置(对齐第4轮护栏精神:基准折算非成交价)。"
        ),
        "ranking": ranking,
        "confidence": rec_confidence,
    }
    if best.get("estimate_only"):
        payload["warning"] = (
            "推荐方案本身也是 estimate_only(全员 CPM 兜底价):当前无任何真实报价可用,"
            "该推荐只是基准折算下的相对排序,签约前务必录入真实报价复算。"
        )
    return payload


# ── 主入口 ──────────────────────────────────────────────────────────


def simulate(
    sku: str,
    budget_usd: float,
    max_roster: int = 20,
    country: str | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """三策略 what-if 并排模拟;SKU 不存在抛 LookupError(路由转 404)。决定性:同输入同输出。

    GTM-1 补参(默认 None=旧行为逐字不变):
      country → 候选池按受众地域三档降序(audience_geo 守卫 import;无数据不淘汰只降序);
      goal ∈ exposure|conversion|content → 推荐排序权重切换(conversion=现状默认口径);
      未知 goal 值按默认口径排序并在 goal_note 诚实说明。参数影响全部写进 basis。
    """
    sku = _text(sku, 200)
    if not sku:
        raise LookupError("sku is required")
    budget = _float_or_none(budget_usd)
    if budget is None or budget <= 0:
        return {
            "status": "invalid",
            "reason": f"budget_usd 非法({budget_usd}),需为正数。",
            "sku": sku,
            "method": METHOD,
            "generated_at": _utcnow_iso(),
        }
    max_roster = max(1, min(TAIL_MAX, int(max_roster or TAIL_MAX)))

    # GTM-1 补参归一化(None=旧行为逐字不变)
    country_code, country_note = _normalize_country(country)
    goal_norm = _text(goal, 30).lower()
    effective_goal = goal_norm if goal_norm in GOALS else None

    # SKU 解析(与发射台同一真源 resolve_sku;守卫 import)
    try:
        from app.domains.projects import launch_assembly
        product = launch_assembly._resolve_product(sku)
        product_basic = launch_assembly._product_basic(product) if product else None
    except ImportError as exc:
        raise LookupError(f"launch_assembly unavailable, cannot resolve sku: {exc}") from exc
    if not product:
        raise LookupError(f"SKU not found in vkpi_products: {sku}")
    sku_code = _text(product.get("sku"), 120) or sku

    rate_card, forecast_mod, roster_mod, engines_missing = _load_engines()

    pool = _candidate_pool(sku_code)
    pool_items = pool.get("items") or []

    from app.db.connection import get_conn

    db = get_conn()
    cands = _build_candidates(sku_code, pool_items, rate_card, forecast_mod, db)
    priceable = [c for c in cands if c["cost_ready"]]

    base = {
        "status": "ready",
        "method": METHOD,
        "sku": sku_code,
        "requested_sku": sku,
        "product": product_basic,
        "budget_usd": _usd(budget),
        "max_roster": max_roster,
        "generated_at": _utcnow_iso(),
        "llm_calls": False,
        "provider_calls": False,
        "note": (
            "三策略 what-if 决定性模拟(零 LLM 零采集零写库,同输入同输出):"
            "成本=rate_card,播放区间=performance_forecast 逐人求和,触达=roster_optimizer 去重;"
            "每个数字带 basis/method/confidence 可追溯,缺数据诚实空态,绝不编造市场数据。"
        ),
    }
    # 补参回显(仅提供时出现;无参输出逐字同旧)
    if country_code:
        base["country"] = country_code
        if country_note:
            base["country_note"] = country_note
    if goal_norm:
        base["goal"] = goal_norm
        if effective_goal is None:
            base["goal_note"] = (
                f"goal={goal_norm} 不在支持集 {'/'.join(GOALS)},推荐排序按默认 conversion 口径,不假装懂。"
            )
    candidate_pool_block = {
        "status": _text(pool.get("status"), 30),
        "reason": _text(pool.get("reason"), 300),
        "size": len(pool_items),
        "priceable_count": len(priceable),
        "unpriceable_count": len(cands) - len(priceable),
        "forecastable_count": sum(1 for c in cands if c["forecast_ready"]),
        "longtail_count": sum(1 for c in priceable if c.get("tier") in LONGTAIL_TIERS),
        "basis": pool.get("basis") or {},
        "note": "候选=launch_assembly._candidate_pool(new_launch_match 决定性 dry-run,与发射台同源);无法估价的候选不参与装包并如实计数。",
    }
    if engines_missing:
        candidate_pool_block["engines_missing"] = engines_missing

    # country 补参:受众地域三档标注(只标 priceable —— 只有他们参与装包)
    geo_summary: dict[str, Any] | None = None
    if country_code and priceable:
        geo_summary = _annotate_audience_geo(priceable, country_code, db)
        candidate_pool_block["audience_geo_filter"] = geo_summary
    elif country_code:
        candidate_pool_block["audience_geo_filter"] = {
            "status": "skipped",
            "country": country_code,
            "reason": "无可估价候选,country 过滤无从谈起。",
        }

    if not priceable:
        return {
            **base,
            "status": "empty",
            "reason": (
                _text(pool.get("reason"), 200) or "候选池为空或全员无法估价,三策略无人可排 — 诚实空态。"
            ),
            "candidate_pool": candidate_pool_block,
            "plans": [],
            "recommendation": {"status": "empty", "reason": "无可估价候选,无从推荐。", "ranking": []},
        }

    head_picked, head_spent, head_note = _pick_head(priceable, budget, HEAD_MAX)
    tail_cap = min(TAIL_MAX, max_roster)
    tail_picked, tail_spent, tail_note = _pick_longtail(priceable, budget, tail_cap)
    mix_picked, mix_spent, mix_note = _pick_hybrid(priceable, budget, min(TAIL_MAX, max_roster))

    plans = [
        _plan_payload(
            "head_concentration", "A 头部集中", "预算全给预测战绩 top 3-5 人",
            head_picked, head_spent, head_note, budget, roster_mod,
        ),
        _plan_payload(
            "longtail_spread", "B 长尾铺量", "小额铺 15-20 个 nano/micro",
            tail_picked, tail_spent, tail_note, budget, roster_mod,
        ),
        _plan_payload(
            "hybrid_5050", "C 混合 50/50", "一半预算给头部(≤3 人),一半铺长尾",
            mix_picked, mix_spent, mix_note, budget, roster_mod,
        ),
    ]

    # country 补参:排序影响写进每方案 basis(仅参数生效时出现)
    if geo_summary is not None:
        for plan in plans:
            basis = plan.get("basis")
            if isinstance(basis, dict):
                basis["country_filter"] = {
                    "country": country_code,
                    "rule": COUNTRY_RULE_TEXT,
                    "counts": {
                        "matched": geo_summary.get("matched"),
                        "no_data": geo_summary.get("no_data"),
                        "unmatched": geo_summary.get("unmatched"),
                    },
                    "status": geo_summary.get("status"),
                }

    return {
        **base,
        "candidate_pool": candidate_pool_block,
        "plans": plans,
        "recommendation": _recommendation(plans, goal=effective_goal),
    }
