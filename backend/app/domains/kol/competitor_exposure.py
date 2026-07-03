"""#51 百家饭指数(竞品露出率)— 单 KOL 品牌露出聚合(纯读已深析产物)。

「百家饭指数」量化一个 KOL 的内容里 Viltrox vs 竞品的露出比例:越高越「专情」
(内容围着 Viltrox 转),越低越「百家饭」(谁家产品都恰)。

数据源(零新分析/零 LLM/零 provider 外调):
- vkpi_analysis_cache 已 ready 的 video_analysis_final_v1 产物里的
  raw_gemini_video.viltrox_detected / viltrox_products_all / competitor_mentions,
  与 pool_detail.py evidence 查询回传前端的 llm_ 三键完全同源。

指数算法(详见 _compute 内注释):
- 曝光单位 = 视频×品牌:同一视频里同一品牌只记 1 次(防单条横评视频刷屏)。
- V = Viltrox 出现的视频数;C = Σ 每视频去重后的竞品品牌数。
- viltrox_rate = V / (V + C)(原始比例,无折扣)。
- index = 100 × (V + K/2) / (V + C + K),K=4 —— Laplace 收缩式样本量置信折扣:
  小样本向中性 50 收(1 条全 Viltrox 只给 60,不吹成 100),大样本逼近原始比例。

缓存:结果落 vkpi_analysis_cache(target_type='kol_pool',
derive_method='kol_competitor_exposure_v1'),当日幂等(force=True 才重算),
同 outreach_pack 模式,零新表零迁移。

红线(强约束):绝不写 viltrox_fit_score、不动 rule_v0 评分、不碰 KOL 归属判定;
对 evidence / 主表纯只读,唯一写点是本模块自己的 cache 行。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.kol.competitor_exposure")

TARGET_TYPE = "kol_pool"
DERIVE_METHOD = "kol_competitor_exposure_v1"
# 深析样本下限:少于 3 条 final_v1 不给指数(前端显示「深析样本不足」诚实态)。
MIN_SAMPLE = 3
# Laplace 收缩伪计数:相当于预置 K 个「中性观察」(一半 Viltrox 一半竞品)。
SHRINK_K = 4
# 单 KOL 最多聚合多少条已深析 evidence(全视频跑单号也就 ~250 条,500 足够)。
MAX_EVIDENCE = 500

# 已知品牌的特殊显示写法(canonical key 全小写 → 展示名);其余走 .title()。
_BRAND_DISPLAY = {
    "ttartisan": "TTArtisan",
    "7artisans": "7Artisans",
    "smallhd": "SmallHD",
    "dji": "DJI",
    "smallrig": "SmallRig",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _loads(value: Any) -> Any:
    """jsonb 列经 compat 读回可能是 str 也可能已是 list/dict,两头兼容。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _known_brand_lookup() -> dict[str, list[str]]:
    """竞品品牌词表(brand key → keywords),复用 competitor_text 配置;失败回空表。"""
    try:
        from app.domains.kol.competitor_text import load_competitor_brands

        return {brand: list(cfg.get("keywords") or []) for brand, cfg in load_competitor_brands().items()}
    except Exception:
        logger.debug("competitor_exposure.brand_config_read_failed", exc_info=True)
        return {}


def _canon_brand(raw: Any, known: dict[str, list[str]]) -> str:
    """把一条提及归到品牌 canonical key(全小写);'viltrox' 特殊值表示自家品牌。

    输入可能是 'Sigma' / 'Sigma 35mm F1.4 Art' 等自由文本:先精确匹配词表 key,
    再按词边界匹配 keywords,最后回落首个词(诚实保留未知品牌,不丢数据)。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    low = text.lower()
    if "viltrox" in low:
        return "viltrox"
    if low in known:
        return low
    for brand, keywords in known.items():
        for kw in [brand, *keywords]:
            if kw and re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low):
                return brand
    first = re.split(r"[\s/|,;·]+", low, maxsplit=1)[0].strip(".-_")
    return first


def _display_brand(key: str) -> str:
    return _BRAND_DISPLAY.get(key, key.title() if key else key)


def _mention_brand_keys(mentions: Any, known: dict[str, list[str]]) -> set[str]:
    """一条视频的 competitor_mentions(dict 列表或 str 列表)→ 去重品牌 key 集合。"""
    parsed = _loads(mentions)
    if not isinstance(parsed, list):
        return set()
    keys: set[str] = set()
    for item in parsed:
        raw = item.get("brand") if isinstance(item, dict) else item
        key = _canon_brand(raw, known)
        if key:
            keys.add(key)
    return keys


# ── 聚合(纯只读 SELECT) ─────────────────────────────────────────


def _analyzed_rows(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    """该 KOL 全部已深析(final_v1 ready)且 active 的 evidence,只抽三个品牌字段。

    jsonb 路径抽取与 pool_detail.py 同款;(target_type,target_id,derive_method) 有唯一约束,
    一条 evidence 至多一行,无需再去重。inactive evidence 不计入(与抽屉默认读径口径一致,
    也避免把已被归属纠错下线的 evidence 算进别人的指数)。
    """
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            ac.result #>> '{raw_gemini_video,viltrox_detected}' AS viltrox_detected_text,
            ac.result #> '{raw_gemini_video,viltrox_products_all}' AS viltrox_products,
            ac.result #> '{raw_gemini_video,competitor_mentions}' AS competitor_mentions
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id = CAST(ac.target_id AS BIGINT)
        WHERE ac.target_type = 'video'
          AND ac.derive_method = 'video_analysis_final_v1'
          AND ac.status = 'ready'
          AND e.kol_pool_id = ?
          AND e.is_active IS NOT FALSE
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(MAX_EVIDENCE)),
    ).fetchall()
    return [dict(row) for row in rows]


def _compute(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    """聚合品牌露出 → 百家饭指数 payload。

    口径(视频×品牌,防单条视频刷屏):
    - 一条视频算「Viltrox 出现」:viltrox_detected=true 或 viltrox_products_all 非空
      或 competitor_mentions 里被模型误放了 Viltrox 条目(仍归自家)。
    - 一条视频对某竞品品牌只贡献 1 次露出,不管提了几个型号。
    - V = Viltrox 出现的视频数;C = Σ 每视频去重竞品品牌数。
    - viltrox_rate = V/(V+C);index = 100×(V+K/2)/(V+C+K),K=4:
      例 V=1,C=0 → 60(小样本不吹满);V=18,C=6 → 71(大样本≈原始比例 75)。
    - 样本(已深析视频数)< MIN_SAMPLE 或 V+C=0 → index=None,诚实不编造。
    """
    known = _known_brand_lookup()
    rows = _analyzed_rows(conn, int(kol_pool_id))
    sample_size = len(rows)

    viltrox_videos = 0
    competitor_exposures = 0
    competitor_videos = 0
    brand_counts: dict[str, int] = {}
    product_counts: dict[str, int] = {}
    product_display: dict[str, str] = {}

    for row in rows:
        detected_text = str(row.get("viltrox_detected_text") or "").strip().lower()
        products = _loads(row.get("viltrox_products"))
        product_names = [str(p).strip() for p in products if str(p or "").strip()] if isinstance(products, list) else []
        mention_keys = _mention_brand_keys(row.get("competitor_mentions"), known)
        viltrox_hit = detected_text == "true" or bool(product_names) or "viltrox" in mention_keys
        competitor_keys = {k for k in mention_keys if k != "viltrox"}

        if viltrox_hit:
            viltrox_videos += 1
        if competitor_keys:
            competitor_videos += 1
        competitor_exposures += len(competitor_keys)
        for key in competitor_keys:
            brand_counts[key] = brand_counts.get(key, 0) + 1
        for name in product_names:
            pkey = name.lower()
            product_counts[pkey] = product_counts.get(pkey, 0) + 1
            product_display.setdefault(pkey, name)

    total = viltrox_videos + competitor_exposures
    viltrox_rate = round(viltrox_videos / total, 3) if total > 0 else None
    index: int | None = None
    index_band = ""
    if sample_size >= MIN_SAMPLE and total > 0:
        # Laplace 收缩:分子加 K/2、分母加 K,把小样本比例往中性 50 拉。
        index = int(round(100.0 * (viltrox_videos + SHRINK_K / 2.0) / (total + SHRINK_K)))
        index = max(0, min(100, index))
        index_band = "loyal" if index >= 75 else "lean_loyal" if index >= 55 else "mixed" if index >= 35 else "variety"

    if sample_size == 0:
        status = "no_deep_analysis"
    elif sample_size < MIN_SAMPLE:
        status = "insufficient_sample"
    elif total == 0:
        status = "no_brand_signal"
    else:
        status = "ok"

    breakdown = sorted(
        ({"brand": _display_brand(key), "count": count} for key, count in brand_counts.items()),
        key=lambda item: (-int(item["count"]), str(item["brand"])),
    )
    top_products = sorted(
        ({"name": product_display[key], "count": count} for key, count in product_counts.items()),
        key=lambda item: (-int(item["count"]), str(item["name"])),
    )[:8]

    return {
        "schema_version": DERIVE_METHOD,
        "kol_pool_id": int(kol_pool_id),
        "status": status,
        "sample_size": sample_size,
        "min_sample": MIN_SAMPLE,
        "viltrox_videos": viltrox_videos,
        "competitor_videos": competitor_videos,
        "viltrox_exposures": viltrox_videos,
        "competitor_exposures": competitor_exposures,
        "viltrox_rate": viltrox_rate,
        "index": index,
        "index_band": index_band,
        "competitor_breakdown": breakdown[:12],
        "viltrox_products": top_products,
        "generated_date": _today(),
        "computed_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "note": "百家饭指数:视频×品牌口径,含样本量置信折扣;纯读 final_v1 深析产物,绝不参与 viltrox_fit_score。",
    }


# ── 当日缓存(vkpi_analysis_cache,同 outreach_pack 模式) ──────────


def _read_cache(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT result FROM vkpi_analysis_cache
        WHERE target_type=? AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (TARGET_TYPE, str(int(kol_pool_id)), DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    result = _loads(dict(row).get("result"))
    return result if isinstance(result, dict) else None


def _write_cache(conn: Any, kol_pool_id: int, payload: dict[str, Any]) -> None:
    """缓存写失败只记日志不抛(缓存是提速增益,聚合结果照常返回)。"""
    try:
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO vkpi_analysis_cache (
                target_type, target_id, model, derive_method, result, cost, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?::jsonb, 0, 'ready', ?, ?)
            ON CONFLICT (target_type, target_id, derive_method)
            DO UPDATE SET result=EXCLUDED.result, model=EXCLUDED.model,
                status='ready', updated_at=EXCLUDED.updated_at
            """,
            (
                TARGET_TYPE,
                str(int(kol_pool_id)),
                "rule_aggregate",
                DERIVE_METHOD,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        logger.warning("competitor_exposure cache write failed", exc_info=True)


# ── 对外入口 ─────────────────────────────────────────────────────


def get_competitor_exposure(kol_pool_id: int, *, force: bool = False) -> dict[str, Any]:
    """读单 KOL 百家饭指数:当日缓存命中直接回,否则聚合一次并落缓存。

    纯读已有深析产物,零新分析/零 LLM;KOL 不存在抛 LookupError(端点转 404)。
    """
    kid = int(kol_pool_id)
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM vkpi_kol_pool WHERE id=?", (kid,)).fetchone():
        raise LookupError("kol pool item not found")
    if not force:
        cached = _read_cache(conn, kid)
        if cached and str(cached.get("generated_date") or "") == _today():
            return {**cached, "cached": True}
    payload = _compute(conn, kid)
    _write_cache(conn, kid, payload)
    return {**payload, "cached": False}
