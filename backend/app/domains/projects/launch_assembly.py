"""件C · 发射台六输出组装(launch_assembly v1)—— 新品 SKU 一键全案。

assemble_launch_plan(sku, max_roster) 六段编排,全部复用已有件 + 守卫 import,零重造:
  ① roster            KOL 名单:new_launch_match dry-run 候选(只读、不落库)
                       + 每人招牌拍法(signature_profile)+ 该 SKU 焦段覆盖(focal_matrix);
  ② budgets           每人报价:守卫 import kol.rate_card.estimate_rate(B件契约);
                       护栏:有价成员全员 method=cpm_benchmark_v0(纯行业基准折算、
                       零真实报价背书)时 status 降为 estimate_only + warning 明示
                       「预算只是谈判锚点」,绝不让拍脑袋常数冒充可决策预算;
  ③ schedule          排期:leadtime_competing.production_leadtime 个人中位周期倒排发布周,
                       无历史样本诚实用默认周期(basis=default_no_history, confidence=low),
                       同 ISO 周 >2 人时顺延一周;
  ④ playbooks         每人打法:招牌拍法 top1 + 该形式均播放 → 一句话「就按你最爆的 XX 拍法来」;
  ⑤ official_synergy  官号协同 v0:纯规则聚合 vkpi_channel_post_metrics(每贴取最新快照),
                       该品类表现最好的形式/平台/时段 → 3 条协同建议;
                       样本不足诚实降级通用清单并标 generated=generic(generic=true);
  ⑥ coverage          覆盖最大化:守卫 import kol.roster_optimizer.optimize_roster(D件契约);
  ＋ forecast          每人预测战绩:守卫 import kol.performance_forecast.forecast_for_kol(A件契约);
  ＋ sop_timeline      发布 SOP 时间线(G4):同行标杆节奏 T-30(rumor 投料)/T-21(embargo
                       brief)/T-14(批量寄样,吃③排期段个人制作周期数据)/T-7(素材包+LUT
                       短链)/T0(多评测人同日解禁)/T+3(数据首查)/T+7(差评聚类→固件
                       工单→复测邀约,吃 miss_review 思路)七节点倒排真日期;每节点=任务
                       模板(标题/负责人角色/依赖),纯模板生成不真建任务,payload 带 basis。

跨代理契约:A/B/D 三件本轮并行施工,import 失败 → 该段 status="module_pending"
诚实占位(收口后自然变活),绝不杜撰数字。每段带 basis 可追溯到证据。

compat 约定:SQL 占位符 ?;SQL 禁 percent 字面量(不用 LIKE,匹配在 Python 做);
BOOLEAN 读回可能是 int 1/0(本模块无布尔列读回);重依赖函数内懒 import。
红线:全程纯只读;绝不写 viltrox_fit_score、不碰 rule_v0;零 LLM 调用(决定性零成本);
预测/预算数字全部来自兄弟件契约 payload,本模块只转述并保留其 basis/confidence。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 排期:无个人履约历史时的默认制作周期(签收→发布)天数,诚实标注 low confidence。
DEFAULT_LEADTIME_DAYS = 14
# 同一 ISO 周最多安排的发布人数,超过顺延(避免同周内容互相稀释)。
MAX_PUBLISH_PER_WEEK = 2
# 官号协同:品类样本至少这么多贴才出数据驱动建议,否则降级通用清单。
MIN_SYNERGY_SAMPLE = 5
# 候选池取 roster 上限的倍数,给覆盖优化器(D件)留挑选余地。
CANDIDATE_POOL_FACTOR = 2
# 单次组装 roster 硬上限(逐人聚合招牌/焦段/周期,防超长请求)。
MAX_ROSTER_HARD_CAP = 12

# ── 官号内容形式词表(小写子串;标题+话题标签底料;识别不了归 unclassified)──
OFFICIAL_FORMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("giveaway", "抽奖/赠品", ("giveaway", "win a", "winner", "抽奖", "送出", "免费得")),
    ("sample_footage", "样片展示", ("sample", "footage", "shot on", "shot with", "样片", "实拍")),
    ("tutorial", "教程技巧", ("how to", "tutorial", "tips", "教程", "技巧", "settings")),
    ("launch_announcement", "新品官宣", (
        "introducing", "new arrival", "now available", "pre-order", "preorder",
        "launch", "coming soon", "release", "新品", "发布", "首发",
    )),
    ("creator_collab", "创作者联动", ("by @", "via @", "photographer", "creator", "featuring", "repost")),
    ("bts", "幕后花絮", ("behind the scenes", "bts", "幕后")),
)

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HOUR_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("00-06 UTC", 0, 6),
    ("06-12 UTC", 6, 12),
    ("12-18 UTC", 12, 18),
    ("18-24 UTC", 18, 24),
)

# 通用官号协同清单(数据不足时的诚实降级,不假装有数据背书)。
GENERIC_SYNERGY_TIPS: tuple[str, ...] = (
    "KOL 发布同周,官号同步发一条该 SKU 的样片/上手贴,互相导流(通用打法,无历史数据背书)。",
    "官号转发/剪辑表现最好的 KOL 内容做二次曝光,并在评论区置顶产品链接(通用打法)。",
    "发布后 7 天内,官号收集 KOL 视频下高赞评论做一条「大家都在问」答疑贴(通用打法)。",
)


# ── 小工具 ──────────────────────────────────────────────────────────


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _module_pending(module: str, reason: str) -> dict[str, Any]:
    """兄弟件(A/B/D)尚未落地时的诚实占位段;收口后 import 成功自然变活。"""
    return {
        "status": "module_pending",
        "module": module,
        "reason": _text(reason, 300),
        "note": "该模块本轮并行施工中,import 失败即占位;不杜撰任何数字。",
    }


def _fmt_views(value: Any) -> str:
    n = _int_or_none(value)
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ── SKU 解析(复用 sku_performance,单一真源) ───────────────────────


def _resolve_product(sku: str) -> dict[str, Any] | None:
    from app.domains.products.sku_performance import resolve_sku

    return resolve_sku(sku)


def _product_basic(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": _text(product.get("sku"), 120),
        "model_name": _text(product.get("model_name"), 300),
        "marketing_name": _text(product.get("marketing_name"), 300),
        "category_main": _text(product.get("category_main"), 80),
        "category_detail": _text(product.get("category_detail"), 120),
        "series": _text(product.get("series"), 80),
        "mount": _text(product.get("mount"), 80),
        "price_usd": _float_or_none(product.get("price_usd")),
        "status": _text(product.get("status"), 40),
    }


def _focal_token(product: dict[str, Any]) -> str:
    """SKU 焦段 token(如 '85'):model_name/sku 正则提取;提不出返回 ''(不硬猜)。"""
    for key in ("model_name", "marketing_name", "sku"):
        blob = _text(product.get(key), 300).lower()
        match = re.search(r"(\d{2,3})\s*mm", blob) or re.search(r"(\d{2,3})mm", blob)
        if match:
            return match.group(1)
    return ""


# ── ① KOL 候选(复用 new_launch_match 只读 dry-run) ─────────────────


def _candidate_pool(sku: str, pool_limit: int) -> dict[str, Any]:
    """new_launch_match dry-run 候选池;任何前置未就绪时优雅降级(不阻断其余五段)。

    SKU 码常不直接命中 memory product_family,先走 resolve_target_family
    (graceful、never raises)拿到可命中的 query 再跑 preview —— 与
    launch_project._candidate_preview 同源打法,多了 family 解析步骤。
    """
    try:
        from app.domains.recommendations.new_launch_match import build_new_launch_match_preview
        from app.domains.recommendations.new_launch_match_helpers import resolve_target_family
    except ImportError as exc:  # 理论上不该发生(第2轮已有件),仍守卫
        return {"status": "unavailable", "reason": f"new_launch_match import failed: {exc}", "items": []}

    family, used_query, miss_reason = resolve_target_family(sku)
    if family is None:
        return {
            "status": "empty",
            "reason": f"memory 里没有匹配该 SKU 的 product_family:{miss_reason}",
            "items": [],
            "basis": {"method": "new_launch_match_v1", "resolved_query": "", "family": None},
        }

    try:
        preview = build_new_launch_match_preview(
            product_query=used_query,
            limit=int(pool_limit),
            with_llm_reasons=False,
            persist_run=False,
        )
    except Exception as exc:  # readiness/budget 闸未就绪 —— 降级不阻断
        logger.info("launch assembly candidate preview unavailable: %s", exc)
        return {"status": "unavailable", "reason": _text(str(exc), 300), "items": []}

    items = []
    for item in preview.get("items") or []:
        items.append(
            {
                "rank": item.get("rank"),
                "kol_pool_id": _int_or_none(item.get("kol_pool_id")),
                "kol_entity_uid": item.get("kol_entity_uid"),
                "platform": _text(item.get("platform"), 30),
                "handle": _text(item.get("handle"), 100),
                "display_name": _text(item.get("display_name"), 120),
                "country": _text(item.get("country"), 60),
                "score": item.get("score"),
                "score_breakdown": item.get("score_breakdown") or {},
                "review_required": bool(item.get("review_required")),
            }
        )
    return {
        "status": "ready" if items else "empty",
        "reason": "" if items else "候选打分跑通但零命中(memory 证据不足)。",
        "items": items,
        "summary": preview.get("summary") or {},
        "basis": {
            "method": "new_launch_match_v1(deterministic dry-run, persist_run=False)",
            "resolved_query": used_query,
            "family": _text(family.get("display_name"), 120),
            "family_uid": _text(family.get("entity_uid"), 80),
        },
    }


def _signature_summary(kol_pool_id: int) -> dict[str, Any]:
    """招牌拍法 top1(signature_profile 件①复用);缺深析诚实 empty。"""
    try:
        from app.domains.kol.signature_profile import signature_profile

        payload = signature_profile(int(kol_pool_id))
    except Exception as exc:  # noqa: BLE001 — 单人聚合失败不拖垮整案
        return {"status": "error", "reason": _text(str(exc), 200)}
    styles = payload.get("shooting_styles") or {}
    if styles.get("status") != "ready":
        return {
            "status": "empty",
            "reason": _text(styles.get("reason") or "无深析样本", 200),
            "deep_analyzed_count": (payload.get("coverage") or {}).get("deep_analyzed_count", 0),
        }
    modes = styles.get("modes") or []
    top = modes[0] if modes else None
    return {
        "status": "ready",
        "top_mode": top,
        "sample_size": styles.get("sample_size"),
        "method": styles.get("method"),
        "deep_analyzed_count": (payload.get("coverage") or {}).get("deep_analyzed_count", 0),
    }


def _focal_coverage_for_sku(kol_pool_id: int, focal: str) -> dict[str, Any]:
    """该 KOL 对本 SKU 焦段的覆盖(focal_matrix 件复用);SKU 无焦段则只回总览。"""
    try:
        from app.domains.kol.focal_matrix import focal_matrix

        payload = focal_matrix(int(kol_pool_id))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": _text(str(exc), 200)}
    cells = ((payload.get("matrix") or {}).get("focals")) or []
    covered_count = sum(1 for c in cells if c.get("covered"))
    result: dict[str, Any] = {
        "status": "ready",
        "covered_focal_count": covered_count,
        "method": (payload.get("basis") or {}).get("method"),
    }
    if focal:
        target = f"{focal}mm"
        cell = next((c for c in cells if _text(c.get("focal"), 20).lower() == target), None)
        result["sku_focal"] = target
        result["sku_focal_covered"] = bool(cell and cell.get("covered"))
        result["sku_focal_video_count"] = (cell or {}).get("video_count", 0)
        result["sku_focal_avg_views"] = (cell or {}).get("avg_views")
    else:
        result["sku_focal"] = ""
        result["sku_focal_covered"] = None
    return result


# ── ② 每人预算(B件契约:kol.rate_card.estimate_rate) ────────────────


def _budgets_block(members: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from app.domains.kol import rate_card
    except ImportError as exc:
        return _module_pending("app.domains.kol.rate_card", str(exc))

    items: list[dict[str, Any]] = []
    p50_values: list[float] = []
    low_values: list[float] = []
    high_values: list[float] = []
    for member in members:
        kid = member.get("kol_pool_id")
        try:
            est = rate_card.estimate_rate(int(kid))
        except Exception as exc:  # noqa: BLE001 — 单人报价失败不拖垮整段
            est = {"status": "error", "reason": _text(str(exc), 200)}
        row = {
            "kol_pool_id": kid,
            "handle": member.get("handle"),
            "display_name": member.get("display_name"),
            "estimate": est,
        }
        items.append(row)
        if est.get("status") in ("ok", "ready"):
            p50 = _float_or_none(est.get("estimated_usd_p50"))
            if p50 is not None:
                p50_values.append(p50)
            low = _float_or_none(est.get("estimated_usd_low"))
            if low is not None:
                low_values.append(low)
            high = _float_or_none(est.get("estimated_usd_high"))
            if high is not None:
                high_values.append(high)

    # 护栏(B3):有价成员全员 method=cpm_benchmark_v0 → 预算无任何真实报价背书,
    # status 降 estimate_only + warning 明示,防拍脑袋常数被当可决策预算。
    priced_methods = [
        _text((it.get("estimate") or {}).get("method"), 60)
        for it in items
        if (it.get("estimate") or {}).get("status") in ("ok", "ready")
        and _float_or_none((it.get("estimate") or {}).get("estimated_usd_p50")) is not None
    ]
    all_benchmark = bool(priced_methods) and all(m == "cpm_benchmark_v0" for m in priced_methods)

    block: dict[str, Any] = {
        "status": "estimate_only" if all_benchmark else "ready",
        "items": items,
        "total": {
            "estimated_usd_p50": round(sum(p50_values), 2) if p50_values else None,
            "estimated_usd_low": round(sum(low_values), 2) if low_values else None,
            "estimated_usd_high": round(sum(high_values), 2) if high_values else None,
            "priced_members": len(p50_values),
            "unpriced_members": len(items) - len(p50_values),
        },
        "basis": {
            "method": "sum of rate_card.estimate_rate per member(B件契约,数字与置信度以其 payload 为准)",
            "note": "总额只加有 p50 的成员;unpriced_members 是诚实缺口,不用均值硬补。",
            "priced_methods": sorted(set(priced_methods)),
        },
    }
    if all_benchmark:
        block["warning"] = (
            f"全部 {len(priced_methods)} 名有价成员的报价均为 CPM 行业基准折算(cpm_benchmark_v0),"
            "没有任何真实报价背书 — 预算总额只是谈判锚点,不可当决策预算;"
            "先在报价卡录入合同价/外联回复价(vkpi_kol_rates),估价会自动切换为真报价中位数。"
        )
    return block


# ── ③ 排期(leadtime_competing 个人中位周期倒排 + 同周去重) ─────────


def _leadtime_for(kol_pool_id: int) -> dict[str, Any]:
    try:
        from app.domains.kol.leadtime_competing import production_leadtime

        payload = production_leadtime(int(kol_pool_id))
    except Exception as exc:  # noqa: BLE001
        return {"days": DEFAULT_LEADTIME_DAYS, "method": "default_error", "confidence": "low",
                "sample_count": 0, "detail": _text(str(exc), 200)}
    median = _float_or_none(payload.get("median_days"))
    samples = _int_or_none(payload.get("sample_count")) or 0
    if payload.get("status") == "ok" and median is not None and samples >= 1:
        return {
            "days": median,
            "method": "personal_median(production_leadtime 签收→发布历史)",
            "confidence": "high" if samples >= 3 else "medium",
            "sample_count": samples,
            "detail": "",
        }
    return {
        "days": DEFAULT_LEADTIME_DAYS,
        "method": "default_no_history",
        "confidence": "low",
        "sample_count": samples,
        "detail": "该 KOL 无「签收→发布」历史样本,用默认周期占位(诚实 low confidence)。",
    }


def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _schedule_block(members: list[dict[str, Any]]) -> dict[str, Any]:
    if not members:
        return {"status": "empty", "reason": "roster 为空,无人可排期。"}
    today = datetime.now(timezone.utc).date()
    prelim: list[dict[str, Any]] = []
    for member in members:
        kid = member.get("kol_pool_id")
        lead = _leadtime_for(int(kid))
        target = today + timedelta(days=int(round(float(lead["days"]))))
        prelim.append(
            {
                "kol_pool_id": kid,
                "handle": member.get("handle"),
                "display_name": member.get("display_name"),
                "leadtime_days": lead["days"],
                "leadtime_basis": {
                    "method": lead["method"],
                    "sample_count": lead["sample_count"],
                    "confidence": lead["confidence"],
                    "detail": lead["detail"],
                },
                "_target": target,
            }
        )
    # 周期短的先排;同 ISO 周 >MAX_PUBLISH_PER_WEEK 人时顺延到下一有空位的周。
    prelim.sort(key=lambda r: (r["_target"], str(r.get("handle") or "")))
    week_load: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for row in prelim:
        target: date = row.pop("_target")
        shifted_days = 0
        while week_load.get(_iso_week_label(target), 0) >= MAX_PUBLISH_PER_WEEK:
            target = target + timedelta(days=7)
            shifted_days += 7
        week = _iso_week_label(target)
        week_load[week] = week_load.get(week, 0) + 1
        row["publish_date"] = target.isoformat()
        row["publish_week"] = week
        row["shifted_days"] = shifted_days
        items.append(row)
    return {
        "status": "ready",
        "kickoff_date": today.isoformat(),
        "items": items,
        "weeks": [
            {"week": wk, "count": n} for wk, n in sorted(week_load.items())
        ],
        "basis": {
            "method": (
                f"kickoff=今天(签收日假设)+ 个人中位制作周期倒排;无历史用默认 {DEFAULT_LEADTIME_DAYS} 天;"
                f"同 ISO 周上限 {MAX_PUBLISH_PER_WEEK} 人,超出顺延 7 天(shifted_days 可追溯)。"
            ),
            "default_leadtime_days": DEFAULT_LEADTIME_DAYS,
            "max_per_week": MAX_PUBLISH_PER_WEEK,
        },
    }


# ── ④ 每人打法(招牌拍法 top1 一句话) ───────────────────────────────


def _playbooks_block(members: list[dict[str, Any]]) -> dict[str, Any]:
    if not members:
        return {"status": "empty", "reason": "roster 为空,无人可给打法。"}
    items: list[dict[str, Any]] = []
    for member in members:
        sig = member.get("signature") or {}
        top = sig.get("top_mode") if sig.get("status") == "ready" else None
        if top:
            label = _text(top.get("label"), 60)
            avg_views = top.get("avg_views")
            line = f"就按你最爆的「{label}」拍法来"
            if avg_views is not None:
                line += f" — 该形式历史均播放 {_fmt_views(avg_views)}"
            line += f"(样本 {top.get('video_count')} 条深析)。"
            items.append(
                {
                    "kol_pool_id": member.get("kol_pool_id"),
                    "handle": member.get("handle"),
                    "display_name": member.get("display_name"),
                    "status": "ready",
                    "line": line,
                    "basis": {
                        "source": "signature_profile.shooting_styles top1(final_v1 深析词表分类)",
                        "mode_key": top.get("key"),
                        "video_count": top.get("video_count"),
                        "avg_views": avg_views,
                        "views_sample": top.get("views_sample"),
                    },
                }
            )
        else:
            items.append(
                {
                    "kol_pool_id": member.get("kol_pool_id"),
                    "handle": member.get("handle"),
                    "display_name": member.get("display_name"),
                    "status": "empty",
                    "line": "暂无深析样本,给不出个人化打法 — 先跑「KOL 深度分析」再看。",
                    "basis": {"source": "signature_profile", "reason": _text(sig.get("reason"), 200)},
                }
            )
    return {"status": "ready", "items": items}


# ── ⑤ 官号协同 v0(纯规则聚合官号内容表现) ─────────────────────────


def _load_official_posts() -> list[dict[str, Any]]:
    """官号内容:vkpi_channel_post_metrics 每贴取最新快照(views 为累计值)。"""
    from app.db.connection import get_conn, table_exists

    if not table_exists("vkpi_channel_post_metrics"):
        return []
    rows = get_conn().execute(
        """
        SELECT m.platform, m.post_uid, m.post_url, m.title, m.posted_at,
               m.views, m.likes, m.comments
        FROM vkpi_channel_post_metrics m
        JOIN (
            SELECT platform AS p, post_uid AS u, MAX(snapshot_date) AS sd
            FROM vkpi_channel_post_metrics
            GROUP BY platform, post_uid
        ) last ON last.p = m.platform AND last.u = m.post_uid AND last.sd = m.snapshot_date
        """,
    ).fetchall()
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        key = (_text(row.get("platform"), 30), _text(row.get("post_uid"), 200))
        prev = dedup.get(key)
        if prev is None or (_int_or_none(row.get("views")) or 0) > (_int_or_none(prev.get("views")) or 0):
            dedup[key] = row
    return list(dedup.values())


def _classify_form(title_low: str) -> tuple[str, str] | None:
    for key, label, terms in OFFICIAL_FORMS:
        if any(term in title_low for term in terms):
            return key, label
    return None


def _best_group(groups: dict[str, list[int]], min_posts: int = 2) -> tuple[str, int, int] | None:
    """{组: [表现值]} → (组, 均值, 样本数);样本 < min_posts 的组不参评。"""
    best: tuple[str, int, int] | None = None
    for name, values in groups.items():
        if len(values) < min_posts:
            continue
        avg = round(sum(values) / len(values))
        if best is None or avg > best[1]:
            best = (name, avg, len(values))
    return best


def _official_synergy_block(product: dict[str, Any], focal: str) -> dict[str, Any]:
    try:
        posts = _load_official_posts()
    except Exception as exc:  # noqa: BLE001 — 官号数据源挂了不拖垮整案
        return {"status": "error", "reason": _text(str(exc), 300)}
    if not posts:
        return {
            "status": "generic",
            "generic": True,
            "reason": "官号内容表 vkpi_channel_post_metrics 无数据,降级通用清单。",
            "suggestions": [{"line": tip, "basis": {"source": "generic_checklist"}} for tip in GENERIC_SYNERGY_TIPS],
        }

    category = _text(product.get("category_main"), 80).lower()
    # 匹配层级:①SKU 焦段(如 '85mm')→ ②品类(镜头=任意焦段/lens 词)→ ③全官号。逐级放宽并如实记 scope。
    def _match_sku(title_low: str) -> bool:
        if not focal:
            return False
        return f"{focal}mm" in title_low or f"{focal} mm" in title_low

    def _match_category(title_low: str) -> bool:
        if category == "lens":
            return bool(re.search(r"\d{2,3}\s*mm|lens", title_low))
        return bool(category) and category in title_low

    scoped: list[dict[str, Any]] = []
    scope = ""
    for scope_name, matcher in (("sku_focal", _match_sku), ("category", _match_category)):
        scoped = [p for p in posts if matcher(_text(p.get("title"), 600).lower())]
        if len(scoped) >= MIN_SYNERGY_SAMPLE:
            scope = scope_name
            break
    if len(scoped) < MIN_SYNERGY_SAMPLE:
        return {
            "status": "generic",
            "generic": True,
            "reason": (
                f"官号内容里与该 SKU/品类相关的贴不足 {MIN_SYNERGY_SAMPLE} 条"
                f"(焦段命中 {len([p for p in posts if _match_sku(_text(p.get('title'), 600).lower())])} 条),降级通用清单。"
            ),
            "suggestions": [{"line": tip, "basis": {"source": "generic_checklist"}} for tip in GENERIC_SYNERGY_TIPS],
        }

    # 先定统一口径再聚合,不把 views 和互动数混进同一均值:
    # 有 views 的贴够样本就全用 views,否则全用互动(赞+评);另一口径的贴不参与均值。
    with_views = [p for p in scoped if (_int_or_none(p.get("views")) or 0) > 0]
    if len(with_views) >= MIN_SYNERGY_SAMPLE:
        dominant_metric = "views"
        measured = with_views
    else:
        dominant_metric = "engagement"
        measured = scoped

    form_groups: dict[str, list[int]] = {}
    form_labels: dict[str, str] = {}
    platform_groups: dict[str, list[int]] = {}
    weekday_groups: dict[str, list[int]] = {}
    hour_groups: dict[str, list[int]] = {}
    total_metric = 0
    for row in measured:
        if dominant_metric == "views":
            value = _int_or_none(row.get("views")) or 0
        else:
            value = (_int_or_none(row.get("likes")) or 0) + (_int_or_none(row.get("comments")) or 0)
        total_metric += value
        title_low = _text(row.get("title"), 600).lower()
        form = _classify_form(title_low)
        if form:
            form_groups.setdefault(form[0], []).append(value)
            form_labels[form[0]] = form[1]
        platform_groups.setdefault(_text(row.get("platform"), 30) or "unknown", []).append(value)
        posted = _parse_ts(row.get("posted_at"))
        if posted is not None:
            weekday_groups.setdefault(WEEKDAY_LABELS[posted.weekday()], []).append(value)
            for label, lo, hi in HOUR_BUCKETS:
                if lo <= posted.hour < hi:
                    hour_groups.setdefault(label, []).append(value)
                    break
    scope_label = f"{focal}mm 相关" if scope == "sku_focal" else f"{category or '同品类'} 相关"
    suggestions: list[dict[str, Any]] = []

    best_form = _best_group(form_groups)
    if best_form:
        key, avg, n = best_form
        suggestions.append(
            {
                "line": (
                    f"官号「{form_labels.get(key, key)}」类内容在{scope_label}贴里表现最好"
                    f"(均{dominant_metric} {_fmt_views(avg)},{n} 条样本)— 新品发布周官号同步做一条该形式内容。"
                ),
                "basis": {"source": "vkpi_channel_post_metrics", "scope": scope, "form": key,
                          "avg": avg, "metric": dominant_metric, "sample": n},
            }
        )
    best_platform = _best_group(platform_groups)
    best_weekday = _best_group(weekday_groups)
    best_hour = _best_group(hour_groups)
    if best_platform:
        pname, pavg, pn = best_platform
        timing = ""
        timing_basis: dict[str, Any] = {}
        if best_weekday:
            timing += f",{best_weekday[0]}"
            timing_basis["weekday"] = {"day": best_weekday[0], "avg": best_weekday[1], "sample": best_weekday[2]}
        if best_hour:
            timing += f" {best_hour[0]}"
            timing_basis["hour_bucket"] = {"bucket": best_hour[0], "avg": best_hour[1], "sample": best_hour[2]}
        suggestions.append(
            {
                "line": (
                    f"官号协同首选 {pname}(该平台{scope_label}贴均{dominant_metric} {_fmt_views(pavg)},{pn} 条样本)"
                    f"{timing + ' 发布历史表现最高' if timing else ''}。"
                ),
                "basis": {"source": "vkpi_channel_post_metrics", "scope": scope, "platform": pname,
                          "avg": pavg, "metric": dominant_metric, "sample": pn, **timing_basis},
            }
        )
    suggestions.append(
        {
            "line": (
                f"KOL 发布同周,官号转发/剪辑 KOL 内容做二次曝光 — 官号{scope_label}贴共 {len(scoped)} 条"
                f"(计量口径 {dominant_metric} 的 {len(measured)} 条累计 {_fmt_views(total_metric)}),有现成受众可导流。"
            ),
            "basis": {"source": "vkpi_channel_post_metrics", "scope": scope, "matched_posts": len(scoped),
                      "measured_posts": len(measured), "total_metric": total_metric, "metric": dominant_metric},
        }
    )

    return {
        "status": "ready",
        "generic": False,
        "scope": scope,
        "matched_posts": len(scoped),
        "measured_posts": len(measured),
        "metric": dominant_metric,
        "scanned_posts": len(posts),
        "suggestions": suggestions[:3],
        "basis": {
            "source": "vkpi_channel_post_metrics(官号每贴最新快照)",
            "method": (
                "rule_aggregation_v0:标题词表分形式 + 平台/星期/时段分组均值;"
                "口径先统一:views 样本足够全用 views,否则全用互动(赞+评),两口径绝不混均值"
            ),
            "min_sample": MIN_SYNERGY_SAMPLE,
        },
    }


# ── ⑥ 覆盖最大化(D件契约:kol.roster_optimizer.optimize_roster) ────


def _coverage_block(candidate_ids: list[int], max_roster: int) -> dict[str, Any]:
    if not candidate_ids:
        return {"status": "empty", "reason": "无带 kol_pool_id 的候选,覆盖优化无输入。"}
    try:
        from app.domains.kol import roster_optimizer
    except ImportError as exc:
        return _module_pending("app.domains.kol.roster_optimizer", str(exc))
    try:
        result = roster_optimizer.optimize_roster(list(candidate_ids), max_size=int(max_roster))
    except Exception as exc:  # noqa: BLE001 — D件内部失败也诚实降级,不拖垮整案
        return {"status": "error", "reason": _text(str(exc), 300), "module": "app.domains.kol.roster_optimizer"}
    if isinstance(result, dict):
        result.setdefault("basis", {"source": "roster_optimizer.optimize_roster(D件契约)"})
        return result
    return {"status": "error", "reason": "optimize_roster 返回非 dict,契约不符。"}


def _selected_ids_from_coverage(coverage: dict[str, Any]) -> list[int]:
    """从 D 件返回里容错提取选中 kol_pool_id 列表(int 或 dict 双态)。"""
    ids: list[int] = []
    for item in coverage.get("selected") or []:
        if isinstance(item, dict):
            kid = _int_or_none(item.get("kol_pool_id")) or _int_or_none(item.get("id"))
        else:
            kid = _int_or_none(item)
        if kid is not None and kid not in ids:
            ids.append(kid)
    return ids


# ── ＋ 每人预测战绩(A件契约:kol.performance_forecast.forecast_for_kol) ─


def _forecast_block(members: list[dict[str, Any]], sku: str) -> dict[str, Any]:
    if not members:
        return {"status": "empty", "reason": "roster 为空,无人可预测。"}
    try:
        from app.domains.kol import performance_forecast
    except ImportError as exc:
        return _module_pending("app.domains.kol.performance_forecast", str(exc))
    items: list[dict[str, Any]] = []
    for member in members:
        kid = member.get("kol_pool_id")
        try:
            # context=launchpad:预测流水(vkpi_forecast_log)按语境分桶,发射台调用如实标注。
            fc = performance_forecast.forecast_for_kol(int(kid), sku=sku, context="launchpad")
        except Exception as exc:  # noqa: BLE001
            fc = {"status": "error", "reason": _text(str(exc), 200)}
        items.append(
            {
                "kol_pool_id": kid,
                "handle": member.get("handle"),
                "display_name": member.get("display_name"),
                "forecast": fc,
            }
        )
    return {
        "status": "ready",
        "items": items,
        "basis": {"source": "performance_forecast.forecast_for_kol(A件契约,p50/p10/p90+confidence 以其 payload 为准)"},
    }


# ── ＋ 发布 SOP 时间线(G4:实现在同域 launch_sop.py,守卫调用不拖垮整案)──


def _sop_timeline_block(schedule: dict[str, Any]) -> dict[str, Any]:
    """发布 SOP 七节点(T-30→T+7)任务模板时间线;实现细节见 launch_sop.build_sop_timeline。"""
    try:
        from app.domains.projects.launch_sop import build_sop_timeline

        return build_sop_timeline(schedule)
    except Exception as exc:  # noqa: BLE001 — SOP 段失败诚实降级,不拖垮其余段
        logger.warning("sop_timeline block failed: %s", exc)
        return {"status": "error", "reason": _text(str(exc), 300)}


# ── 主入口 ──────────────────────────────────────────────────────────


def assemble_launch_plan(sku: str, max_roster: int = 8) -> dict[str, Any]:
    """新品 SKU 一键全案:六段编排(全复用已有件);SKU 不存在抛 LookupError(路由转 404)。"""
    sku = _text(sku, 200)
    if not sku:
        raise LookupError("sku is required")
    max_roster = max(1, min(MAX_ROSTER_HARD_CAP, int(max_roster or 8)))

    product = _resolve_product(sku)
    if not product:
        raise LookupError(f"SKU not found in vkpi_products: {sku}")
    focal = _focal_token(product)

    # ① 候选池(取 roster 上限×倍数,给优化器留挑选余地)
    pool = _candidate_pool(_text(product.get("sku"), 120) or sku, max_roster * CANDIDATE_POOL_FACTOR)
    pool_items = pool.get("items") or []
    candidate_ids = [it["kol_pool_id"] for it in pool_items if it.get("kol_pool_id") is not None]

    # ⑥ 覆盖最大化(D件):选中即最终 roster;module_pending/失败时退按分取前 N
    coverage = _coverage_block(candidate_ids, max_roster)
    selected_ids = _selected_ids_from_coverage(coverage) if coverage.get("status") in ("ready", "ok") else []
    roster_selection_basis = "roster_optimizer selected(D件覆盖最大化)"
    if not selected_ids:
        selected_ids = candidate_ids[:max_roster]
        roster_selection_basis = "score 降序取前 N(覆盖优化器不可用时的退化选择)"
    selected_ids = selected_ids[:max_roster]

    by_id = {it.get("kol_pool_id"): it for it in pool_items if it.get("kol_pool_id") is not None}
    members: list[dict[str, Any]] = []
    for kid in selected_ids:
        base = dict(by_id.get(kid) or {"kol_pool_id": kid, "handle": "", "display_name": "", "score": None})
        base["signature"] = _signature_summary(int(kid))
        base["focal_coverage"] = _focal_coverage_for_sku(int(kid), focal)
        members.append(base)

    roster_block = {
        "status": pool.get("status") if not members else "ready",
        "reason": pool.get("reason") or ("" if members else "候选池为空。"),
        "selection_basis": roster_selection_basis,
        "members": members,
        "candidate_pool": {
            "status": pool.get("status"),
            "reason": pool.get("reason", ""),
            "size": len(pool_items),
            "with_pool_id": len(candidate_ids),
            "items": pool_items,
            "summary": pool.get("summary") or {},
        },
        "basis": pool.get("basis") or {},
    }

    budgets = _budgets_block(members)
    schedule = _schedule_block(members)
    playbooks = _playbooks_block(members)
    synergy = _official_synergy_block(product, focal)
    forecast = _forecast_block(members, _text(product.get("sku"), 120) or sku)
    sop_timeline = _sop_timeline_block(schedule)

    return {
        "status": "ready",
        "sku": _text(product.get("sku"), 120) or sku,
        "requested_sku": sku,
        "max_roster": max_roster,
        "product": _product_basic(product),
        "sku_focal": f"{focal}mm" if focal else "",
        "roster": roster_block,               # ①
        "budgets": budgets,                   # ②
        "schedule": schedule,                 # ③
        "playbooks": playbooks,               # ④
        "official_synergy": synergy,          # ⑤
        "coverage": coverage,                 # ⑥
        "forecast": forecast,                 # ＋ A件
        "sop_timeline": sop_timeline,         # ＋ G4 发布 SOP 七节点(纯模板)
        "generated_at": _utcnow_iso(),
        "provider_calls": False,
        "llm_calls": False,
        "note": (
            "六段全部复用已有件的纯聚合/规则输出(零 LLM、零采集、零写库);"
            "兄弟件(预测/报价/组合)未落地段以 module_pending 诚实占位,收口后自然变活;"
            "每段 basis 可追溯到数据源,数字缺口如实标注,绝不杜撰。"
        ),
    }
