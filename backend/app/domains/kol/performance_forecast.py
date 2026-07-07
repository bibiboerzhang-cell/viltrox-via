"""KOL 预测战绩(performance_forecast v1)—— 「找他合作,大概能跑出什么数」。

口径(决定性分位数法,零 LLM、零新采集):
  基线      该 KOL 全部有效视频证据的播放分布 → p10/p50/p90 期望播放
            (线性插值分位数,可复算);互动率取逐视频 (赞+评)/播放 的中位数;
  SKU 调整  带 sku 参数时,用焦段矩阵词表/正则(复用 focal_matrix 的既有提取器)
            判断该 KOL 对此 SKU 焦段的覆盖史:
              覆盖样本≥3 → 系数=覆盖视频播放中位数/基线p50(夹在 0.75~1.30);
              覆盖 1~2 条 → 轻微上调 1.05;纯空白 → 下调 0.85;
              另有带品史(Viltrox 语境)≥1 条 → 追加 1.05;
            合成系数夹在 0.60~1.40,每个因子的数值与理由全部进 basis(可追溯);
  track_record  历史带品视频实绩小结:标题含 viltrox 或 final_v1 深析旗标确认的
            视频的播放中位数/最高 + 代表作,识别不了诚实 empty。

诚实态:0 条有播放证据 → status="empty" 且 expected_* 全 None,绝不杜撰;
样本<3 → confidence="low" + low_sample=True(前端显著标注「样本不足」);
预测数字全部可追溯:basis 带 method/样本量/时间窗/基线分位数/调整因子。

数据源(全只读):vkpi_kol_pool / vkpi_kol_video_evidence / vkpi_analysis_cache
(derive_method='video_analysis_final_v1', status='ready') / vkpi_products(SKU 校验)。

预测流水(B3 学习闭环通电 · 刀1):ready 预测出结果处 best-effort 追加一行
vkpi_forecast_log(迁移215,唯一可写表;p10/p50/p90/er/confidence/method/context),
供 30 天后 learning.forecast_feedback 回查实际播放对答案出带内率;落库失败只警告
绝不影响返回,dry_run=True 可关;empty/error 态无数字可验,不落流水。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回
可能是 int 1/0,判断走 _truthy;JSONB 双态走 focal_matrix._loads。函数内懒 import get_conn。
红线:业务/评分表纯读聚合,唯一写入是 vkpi_forecast_log 预测流水(追加式);
零触 fit 评分存储列、不碰 rule_v0;零 LLM 调用。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
METHOD = "evidence_quantile_v1"

# 预测流水语境白名单(迁移215 vkpi_forecast_log.context);未知语境收敛回 drawer,不脏枚举。
LOG_CONTEXTS: tuple[str, ...] = ("launchpad", "drawer", "sim")

# 调整系数护栏(全部可追溯进 basis)
_RATIO_CLAMP = (0.75, 1.30)     # 覆盖史数据驱动因子的夹取范围
_COMBINED_CLAMP = (0.60, 1.40)  # 合成系数的最终夹取范围
_COVERED_MIN_FOR_RATIO = 3      # 覆盖样本≥3 才允许数据驱动因子
_LIGHT_UP = 1.05                # 覆盖 1~2 条 / 带品史的轻微上调
_BLANK_DOWN = 0.85              # 纯空白焦段的下调


# ── 兄弟件/既有件守卫 import(拿不到就诚实降级,不硬猜)────────────────


def _focal_tools() -> Any:
    """焦段矩阵既有提取器(_extract_focals/_product_focals/_deep_blob_and_flag)。"""
    try:
        from app.domains.kol import focal_matrix

        return focal_matrix
    except ImportError:
        return None


# ── 小工具(与 signature_profile / focal_matrix 同款容错约定)──────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 int 1/0 / 't' / 'true',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _engagement_rate(views: int | None, likes: int | None, comments: int | None) -> float | None:
    """(赞+评)/播放;播放缺失/为 0 或赞评全缺时诚实 None,不除零不外推。"""
    if not views or views <= 0:
        return None
    if likes is None and comments is None:
        return None
    return round(((likes or 0) + (comments or 0)) / views, 5)


def _quantile(sorted_values: list[float], q: float) -> float | None:
    """线性插值分位数(等价 numpy 'linear'):决定性、可手工复算。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    h = (len(sorted_values) - 1) * q
    lo, hi = math.floor(h), math.ceil(h)
    if lo == hi:
        return float(sorted_values[lo])
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (h - lo)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


# ── 数据装载(全只读;查询风格抄 signature_profile)───────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, handle, display_name, platform FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_evidence_rows(conn: Any, kol_pool_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          id AS evidence_id,
          COALESCE(video_title, title, '') AS title,
          content_url,
          platform,
          view_count,
          like_count,
          comment_count,
          posted_at,
          is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


def _load_final_v1_rows(conn: Any, kol_pool_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          e.id AS evidence_id,
          ac.result AS result
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
          AND e.kol_pool_id = ?
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT ?
        """,
        (FINAL_V1_DERIVE_METHOD, int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_product_row(conn: Any, sku: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT sku, series, category_main, category_detail,
               model_name, marketing_name, price_usd, status
        FROM vkpi_products
        WHERE UPPER(sku) = UPPER(?)
        """,
        (_text(sku, 120),),
    ).fetchone()
    return dict(row) if row else None


# ── 视频合并视图(标题人人有;深析底料/带品旗标只有 final_v1 ready 的才有)──


def _merge_videos(
    evidence_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    fm: Any,
) -> list[dict[str, Any]]:
    videos: dict[int, dict[str, Any]] = {}
    for row in evidence_rows:
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None:
            continue
        views = _int_or_none(row.get("view_count"))
        title = _text(row.get("title"), 200)
        videos[eid] = {
            "evidence_id": eid,
            "title": title,
            "title_blob": title.lower(),
            "deep_blob": "",
            "viltrox_flag": "viltrox" in title.lower(),
            "content_url": _text(row.get("content_url"), 500),
            "platform": _text(row.get("platform"), 30),
            "posted_at": _iso(row.get("posted_at")),
            "view_count": views,
            "engagement_rate": _engagement_rate(
                views, _int_or_none(row.get("like_count")), _int_or_none(row.get("comment_count"))
            ),
        }
    for row in final_rows:
        eid = _int_or_none(row.get("evidence_id"))
        video = videos.get(eid or -1)
        if video is None:
            continue
        if fm is not None:
            blob, flag = fm._deep_blob_and_flag(row.get("result"))
            video["deep_blob"] = blob
            video["viltrox_flag"] = video["viltrox_flag"] or flag
        else:  # 焦段矩阵模块不可用:退回整包文本粗判带品,焦段底料诚实留空
            video["viltrox_flag"] = video["viltrox_flag"] or ("viltrox" in str(row.get("result") or "").lower())
    return list(videos.values())


# ── SKU 调整系数(每个因子的数值与理由全部进 basis)───────────────────


def _sku_focals(fm: Any, product: dict[str, Any]) -> set[str]:
    if fm is None:
        return set()
    try:
        line = fm._product_line_of(product)
        return fm._product_focals(product, line)
    except Exception:
        return set()


def _sku_adjustment_block(
    fm: Any,
    product: dict[str, Any] | None,
    sku: str,
    videos: list[dict[str, Any]],
    baseline_p50: float,
) -> dict[str, Any]:
    """返回 {status, coefficient, factors:[{factor,value,reason}], ...};算不了就系数 1.0 说人话。"""
    if product is None:
        return {
            "status": "unknown_sku",
            "sku": _text(sku, 120),
            "coefficient": 1.0,
            "factors": [],
            "reason": "vkpi_products 目录里没有这个 SKU,无法做焦段调整 — 按基线预测输出。",
        }
    if fm is None:
        return {
            "status": "unavailable",
            "sku": _text(sku, 120),
            "coefficient": 1.0,
            "factors": [],
            "reason": "焦段矩阵模块(focal_matrix)不可用,跳过 SKU 调整 — 按基线预测输出。",
        }

    focals = _sku_focals(fm, product)
    factors: list[dict[str, Any]] = []

    if not focals:
        return {
            "status": "no_focal",
            "sku": _text(sku, 120),
            "coefficient": 1.0,
            "factors": [],
            "reason": "该 SKU 提不出焦段(非镜头类或编码无 mm token),不做焦段调整 — 按基线预测输出。",
        }

    covered: list[dict[str, Any]] = []
    for video in videos:
        blob = (video["title_blob"] + " " + video["deep_blob"]).strip()
        if blob and focals & fm._extract_focals(blob):
            covered.append(video)
    covered_views = sorted(
        float(v["view_count"]) for v in covered if isinstance(v.get("view_count"), int) and v["view_count"] > 0
    )
    focal_label = "/".join(sorted(focals))

    if len(covered_views) >= _COVERED_MIN_FOR_RATIO and baseline_p50 > 0:
        covered_median = _quantile(covered_views, 0.5) or 0.0
        ratio = _clamp(covered_median / baseline_p50, _RATIO_CLAMP)
        factors.append({
            "factor": "focal_coverage_ratio",
            "value": round(ratio, 3),
            "reason": (
                f"{focal_label} 覆盖史 {len(covered)} 条(有播放 {len(covered_views)} 条),"
                f"覆盖播放中位数 {round(covered_median)} / 基线p50 {round(baseline_p50)},"
                f"系数夹取于 {_RATIO_CLAMP[0]}~{_RATIO_CLAMP[1]}。"
            ),
        })
    elif covered:
        factors.append({
            "factor": "focal_coverage_light",
            "value": _LIGHT_UP,
            "reason": f"{focal_label} 有覆盖史但仅 {len(covered)} 条(有播放 {len(covered_views)} 条),轻微上调。",
        })
    else:
        factors.append({
            "factor": "focal_blank",
            "value": _BLANK_DOWN,
            "reason": f"该 KOL 视频文本里从未出现 {focal_label},纯空白焦段下调(词表/正则口径,不硬猜)。",
        })

    branded = [v for v in videos if v.get("viltrox_flag")]
    if branded:
        factors.append({
            "factor": "brand_history",
            "value": _LIGHT_UP,
            "reason": f"有带品史(Viltrox 语境视频 {len(branded)} 条),追加轻微上调。",
        })
    else:
        factors.append({
            "factor": "brand_history",
            "value": 1.0,
            "reason": "未识别到带品史(标题/深析均无 Viltrox 语境),不加成。",
        })

    combined = 1.0
    for factor in factors:
        combined *= float(factor["value"])
    combined = round(_clamp(combined, _COMBINED_CLAMP), 3)

    return {
        "status": "ready",
        "sku": _text(product.get("sku"), 120),
        "product_name": _text(product.get("marketing_name"), 160) or _text(product.get("model_name"), 160) or None,
        "focals": sorted(focals),
        "covered_video_count": len(covered),
        "covered_with_views": len(covered_views),
        "coefficient": combined,
        "combined_clamp": list(_COMBINED_CLAMP),
        "factors": factors,
        "method": "lexicon_focal_v1",
    }


# ── track_record:历史带品视频实绩小结 ────────────────────────────────


def _track_record_block(videos: list[dict[str, Any]], fm: Any) -> dict[str, Any]:
    branded = [v for v in videos if v.get("viltrox_flag")]
    method = (
        "标题词根 viltrox + final_v1 深析旗标"
        if fm is not None
        else "标题词根 viltrox(深析旗标不可用,退化口径)"
    )
    if not branded:
        return {
            "status": "empty",
            "reason": "未识别到历史带品视频(标题/深析都没有 Viltrox 语境)— 词表口径不硬贴。",
            "method": method,
        }
    branded_views = sorted(
        float(v["view_count"]) for v in branded if isinstance(v.get("view_count"), int) and v["view_count"] > 0
    )
    er_values = sorted(v["engagement_rate"] for v in branded if isinstance(v.get("engagement_rate"), float))
    top = sorted(branded, key=lambda v: v.get("view_count") or 0, reverse=True)[:3]
    median_views = _quantile(branded_views, 0.5)
    median_er = _quantile(er_values, 0.5)
    return {
        "status": "ready",
        "method": method,
        "video_count": len(branded),
        "with_views": len(branded_views),
        "median_views": round(median_views) if median_views is not None else None,
        "max_views": round(branded_views[-1]) if branded_views else None,
        "median_engagement_rate": round(median_er, 5) if median_er is not None else None,
        "examples": [
            {
                "evidence_id": v["evidence_id"],
                "title": v["title"][:160],
                "content_url": v["content_url"] or None,
                "platform": v["platform"] or None,
                "posted_at": v["posted_at"],
                "view_count": v["view_count"],
                "engagement_rate": v["engagement_rate"],
            }
            for v in top
        ],
    }


# ── 预测流水落库钩子(B3 · 刀1;唯一写入点,best-effort)──────────────


def _log_forecast(db: Any, payload: dict[str, Any], context: str) -> None:
    """ready 预测追加一行 vkpi_forecast_log(outcome='pending',30 天后对答案)。

    调用方包 try/except:表未建(迁移215未 apply)/写失败只警告,绝不影响预测返回。
    只写流水表明确列,绝不触任何评分列。
    """
    basis = payload.get("basis") or {}
    db.execute(
        """
        INSERT INTO vkpi_forecast_log
            (kol_pool_id, sku, p10, p50, p90, engagement_rate,
             confidence, method, context, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            int(payload["kol_pool_id"]),
            _text(payload.get("sku"), 120),
            _int_or_none(payload.get("expected_views_p10")),
            _int_or_none(payload.get("expected_views_p50")),
            _int_or_none(payload.get("expected_views_p90")),
            payload.get("engagement_rate"),
            _text(payload.get("confidence"), 20),
            _text(basis.get("method"), 60) or METHOD,
            context if context in LOG_CONTEXTS else "drawer",
        ),
    )
    db.commit()


# ── 置信度(规则进 basis,可追溯)─────────────────────────────────────


def _confidence(views_sample: list[float], p10: float | None, p90: float | None) -> tuple[str, str]:
    n = len(views_sample)
    if n < 3:
        return "low", f"有播放样本仅 {n} 条(<3),区间不可靠 — 样本不足。"
    if n < 10:
        return "medium", f"有播放样本 {n} 条(3~9),分位数可算但区间偏宽。"
    if p10 and p10 > 0 and p90 and (p90 / p10) > 25:
        return "medium", f"样本 {n} 条充足,但播放离散度极大(p90/p10>25),降档为 medium。"
    return "high", f"有播放样本 {n} 条(≥10)且离散度可控。"


# ── 主入口(契约签名,兄弟件依赖)─────────────────────────────────────


def forecast_for_kol(
    kol_pool_id: int,
    sku: str | None = None,
    *,
    conn: Any = None,
    context: str = "drawer",
    dry_run: bool = False,
) -> dict[str, Any]:
    """KOL 预测战绩;KOL 不存在抛 LookupError(路由转 404)。

    返回键含契约字段:status / expected_views_p50 / expected_views_p10 /
    expected_views_p90 / engagement_rate / confidence / basis。
    ready 结果 best-effort 落一行 vkpi_forecast_log 预测流水(学习闭环对答案原料);
    context 标调用语境(launchpad / drawer / sim),dry_run=True 跳过落库,
    落库失败只警告绝不影响返回(既有调用方行为零变化)。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    sku_clean = _text(sku, 120) if sku else None
    fm = _focal_tools()
    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    final_rows = _load_final_v1_rows(db, int(kol_pool_id))
    videos = _merge_videos(evidence_rows, final_rows, fm)

    kol_block = {
        "handle": _text(pool.get("handle"), 100),
        "display_name": _text(pool.get("display_name"), 100),
        "platform": _text(pool.get("platform"), 30),
    }
    common = {
        "kol_pool_id": int(kol_pool_id),
        "kol": kol_block,
        "sku": sku_clean,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "口径:该 KOL 历史视频证据播放分布的线性插值分位数(零 LLM,决定性可复算);"
            "SKU 调整为词表/正则覆盖史系数,因子与理由见 basis。独立展示信号,不参与 V6 Fit 评分。"
        ),
    }

    views_sample = sorted(
        float(v["view_count"]) for v in videos if isinstance(v.get("view_count"), int) and v["view_count"] > 0
    )
    if not views_sample:
        reason = (
            f"该 KOL 有 {len(videos)} 条有效视频证据但均无播放数,分位数无从计算 — 等抓取回填 view_count。"
            if videos
            else "该 KOL 暂无有效视频证据(vkpi_kol_video_evidence 空/全 inactive),预测无从谈起。"
        )
        return {
            **common,
            "status": "empty",
            "reason": reason,
            "expected_views_p10": None,
            "expected_views_p50": None,
            "expected_views_p90": None,
            "engagement_rate": None,
            "confidence": "low",
            "low_sample": True,
            "basis": {
                "method": METHOD,
                "evidence_count": len(videos),
                "views_sample_size": 0,
                "summary": reason,
            },
            "sku_adjustment": None,
            "track_record": _track_record_block(videos, fm),
        }

    p10 = _quantile(views_sample, 0.10) or 0.0
    p50 = _quantile(views_sample, 0.50) or 0.0
    p90 = _quantile(views_sample, 0.90) or 0.0
    er_values = sorted(v["engagement_rate"] for v in videos if isinstance(v.get("engagement_rate"), float))
    er_median = _quantile(er_values, 0.5)

    dated = sorted(v["posted_at"] for v in videos if v.get("posted_at"))
    confidence, confidence_rule = _confidence(views_sample, p10, p90)
    low_sample = len(views_sample) < 3

    adjustment: dict[str, Any] | None = None
    coefficient = 1.0
    if sku_clean:
        product = _load_product_row(db, sku_clean)
        adjustment = _sku_adjustment_block(fm, product, sku_clean, videos, p50)
        coefficient = float(adjustment.get("coefficient") or 1.0)

    expected_p10 = round(p10 * coefficient)
    expected_p50 = round(p50 * coefficient)
    expected_p90 = round(p90 * coefficient)

    summary_parts = [
        f"基于 {len(views_sample)} 条有播放证据的分位数(共 {len(videos)} 条 evidence"
        + (f",时间窗 {str(dated[0])[:10]}~{str(dated[-1])[:10]}" if dated else "")
        + ")",
    ]
    if adjustment and adjustment.get("status") == "ready":
        summary_parts.append(f"SKU {adjustment['sku']} 调整系数 x{coefficient}")
    elif adjustment:
        summary_parts.append(str(adjustment.get("reason") or "SKU 调整未生效"))
    if low_sample:
        summary_parts.append("样本不足(<3),仅供参考")

    result = {
        **common,
        "status": "ready",
        "expected_views_p10": expected_p10,
        "expected_views_p50": expected_p50,
        "expected_views_p90": expected_p90,
        "engagement_rate": round(er_median, 5) if er_median is not None else None,
        "confidence": confidence,
        "low_sample": low_sample,
        "basis": {
            "method": METHOD,
            "evidence_count": len(videos),
            "views_sample_size": len(views_sample),
            "er_sample_size": len(er_values),
            "window": {"from": dated[0] if dated else None, "to": dated[-1] if dated else None},
            "baseline": {"p10": round(p10), "p50": round(p50), "p90": round(p90)},
            "coefficient": coefficient,
            "confidence_rule": confidence_rule,
            "summary": ";".join(summary_parts) + "。",
        },
        "sku_adjustment": adjustment,
        "track_record": _track_record_block(videos, fm),
    }

    # B3 · 刀1:ready 预测落流水(best-effort;失败只警告,预测照常返回)。
    if not dry_run:
        try:
            _log_forecast(db, result, _text(context, 20).lower())
        except Exception as exc:  # noqa: BLE001 — 表未建/写失败均不拖垮预测本体
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 — 回滚失败不掩盖原始告警
                logger.debug("回滚失败(best-effort,不掩盖原始告警)", exc_info=True)
            logger.warning("forecast log write skipped (kol=%s): %s", kol_pool_id, exc)

    return result
