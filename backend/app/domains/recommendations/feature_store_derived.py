"""特征快照 v2 · 派生强特征(学习闭环 L 车道,≤20 维,全部只读派生,零 LLM)。

snapshot_features(feature_store)在 v1 只有 12 个浅特征(粉丝/播放/互动/平台/主题)。本模块
从库内已有结果派生 20 个强特征挂到快照 ``derived`` 子字典(平铺 d_* 键),供:
  - 推荐解释 / 影子重排序读 v2 特征;
  - 每周离线评估链统计非空率(feature_coverage),只放入非空率 >40% 的列才值得进模型。

来源(全部只读,缺料 = None,绝不伪造 0):
  规则 11 维(vkpi_kol_profile_deep.dimensions_11_json,只读持久化结果,不现算、不改 rule_v0);
  final_v1 六分 + verdict 有无(vkpi_kol_llm_deep_analysis_results.llm_dimensions_11,video_final_v1);
  镜头家族出镜(vkpi_kol_lens_evidence,resolution 已解析到 sku/likely);
  情绪标签分布(llm_dimensions_11.emotion_tags_v1.valence);
  受众地理置信(vkpi_kol_pool.audience_estimated_json.top_countries / confidence);
  real_er / suspect_inflation(vkpi_kol_pool 列);报价档(vkpi_kol_rates 中位数);
  合作史(vkpi_kol_cooperation_events 计数);视频-产品边(vkpi_kol_video_product_links 计数)。

红线:只加列、只读派生;不写 viltrox_fit_score、不改 rule_v0;final_v1 输出契约与 llm_dimensions_11 存储不变。
compat:占位符 ?;零字面 percent;BOOLEAN 读回 _truthy;任一来源表缺失 → 该组特征 None,绝不抛。
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from app.core.coerce import _loads, _truthy
from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

DERIVED_FEATURE_VERSION = "derived_features_v2"

# 固定顺序(≤20):新增/删除必须升 DERIVED_FEATURE_VERSION。
DERIVED_FEATURE_KEYS: tuple[str, ...] = (
    "d_rule11_overall",
    "d_rule11_engagement_quality",
    "d_rule11_cooperation_history",
    "d_rule11_competitor_risk",
    "d_has_final_v1",
    "d_final_v1_videos",
    "d_final_v1_content_quality",
    "d_final_v1_marketing_value",
    "d_final_v1_product_proof",
    "d_final_v1_viewer_heart",
    "d_final_v1_channel_value",
    "d_lens_family_count",
    "d_emotion_positive_share",
    "d_audience_top_country_pct",
    "d_audience_geo_confidence",
    "d_real_er",
    "d_suspect_inflation",
    "d_rate_median_usd",
    "d_cooperation_events",
    "d_video_product_links",
)
assert len(DERIVED_FEATURE_KEYS) <= 20

# 进模型的最低非空率(评估链据此标 eligible)。
MIN_NONNULL_RATE = 0.40
_FINAL_V1_KIND = "video_final_v1"
_SIX_SCORES: tuple[tuple[str, str], ...] = (
    ("d_final_v1_content_quality", "content_quality_score"),
    ("d_final_v1_marketing_value", "marketing_value_score"),
    ("d_final_v1_product_proof", "product_proof_score"),
    ("d_final_v1_viewer_heart", "viewer_heart_score"),
    ("d_final_v1_channel_value", "channel_value_score"),
)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN 防御


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty() -> dict[str, Any]:
    return {key: None for key in DERIVED_FEATURE_KEYS}


def _safe(conn: Any, sql: str, params: tuple = (), *, one: bool = False) -> Any:
    """查询失败(列缺/表缺)→ None/[],并回滚避免 PG aborted 状态外溢。"""
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception as rb_exc:
            logger.debug("feature_store_derived.rollback_failed: %s", type(rb_exc).__name__)
        logger.debug("feature_store_derived.query_failed", exc_info=True)
        return None if one else []


def _rule11(conn: Any, kol_pool_id: int, out: dict[str, Any]) -> None:
    if not table_exists("vkpi_kol_profile_deep"):
        return
    row = _safe(
        conn,
        """
        SELECT dimensions_11_json FROM vkpi_kol_profile_deep
        WHERE kol_pool_id=? AND dimensions_11_json IS NOT NULL
        ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT 1
        """,
        (kol_pool_id,), one=True,
    )
    payload = _loads(dict(row).get("dimensions_11_json"), {}) if row else {}
    if not isinstance(payload, dict) or not payload:
        return
    out["d_rule11_overall"] = _f(payload.get("overall_score"))
    b2 = payload.get("block2_performance") or {}
    b3 = payload.get("block3_business") or {}
    out["d_rule11_engagement_quality"] = _f(b2.get("engagement_quality_score")) if isinstance(b2, dict) else None
    if isinstance(b3, dict):
        out["d_rule11_cooperation_history"] = _f(b3.get("cooperation_history_score"))
        out["d_rule11_competitor_risk"] = _f(b3.get("competitor_risk_score"))


def _final_v1(conn: Any, kol_pool_id: int, out: dict[str, Any]) -> None:
    if not table_exists("vkpi_kol_llm_deep_analysis_results"):
        return
    rows = _safe(
        conn,
        """
        SELECT llm_dimensions_11 FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=? AND analysis_kind=? AND status='ready'
        ORDER BY id DESC LIMIT 200
        """,
        (kol_pool_id, _FINAL_V1_KIND),
    )
    payloads = [p for p in (_loads(dict(r).get("llm_dimensions_11"), {}) for r in rows or []) if isinstance(p, dict) and p]
    out["d_has_final_v1"] = 1.0 if payloads else 0.0
    out["d_final_v1_videos"] = float(len(payloads))
    if not payloads:
        return
    for key, score_key in _SIX_SCORES:
        values = []
        for p in payloads:
            scores = p.get("scores") or {}
            cell = scores.get(score_key) if isinstance(scores, dict) else None
            val = _f(cell.get("score")) if isinstance(cell, dict) else _f(cell)
            if val is not None:
                values.append(val)
        out[key] = round(statistics.fmean(values), 3) if values else None
    valences = []
    for p in payloads:
        tags = p.get("emotion_tags_v1")
        if isinstance(tags, dict) and tags.get("valence"):
            valences.append(str(tags.get("valence")).lower())
    if valences:
        out["d_emotion_positive_share"] = round(sum(1 for v in valences if v == "positive") / len(valences), 3)


def _lens(conn: Any, kol_pool_id: int, out: dict[str, Any]) -> None:
    if not table_exists("vkpi_kol_lens_evidence"):
        return
    row = _safe(
        conn,
        """
        SELECT COUNT(DISTINCT COALESCE(NULLIF(lens_key, ''), product_sku)) AS n
        FROM vkpi_kol_lens_evidence
        WHERE kol_pool_id=? AND resolution IN ('sku', 'likely', 'confirmed')
        """,
        (kol_pool_id,), one=True,
    )
    if row is not None:
        out["d_lens_family_count"] = float(_i(dict(row).get("n")))


def _pool_columns(pool_row: dict[str, Any] | None, out: dict[str, Any]) -> None:
    if not pool_row:
        return
    out["d_real_er"] = _f(pool_row.get("real_er"))
    if pool_row.get("suspect_inflation") is not None:
        out["d_suspect_inflation"] = 1.0 if _truthy(pool_row.get("suspect_inflation")) else 0.0
    audience = _loads(pool_row.get("audience_estimated_json"), {})
    if isinstance(audience, dict) and audience:
        countries = audience.get("top_countries") or []
        if isinstance(countries, list) and countries:
            top = countries[0] if isinstance(countries[0], dict) else {}
            out["d_audience_top_country_pct"] = _f(top.get("pct"))
        conf = _f(audience.get("confidence"))
        if conf is not None:
            out["d_audience_geo_confidence"] = conf


def _rates_and_history(conn: Any, kol_pool_id: int, out: dict[str, Any]) -> None:
    if table_exists("vkpi_kol_rates"):
        rows = _safe(conn, "SELECT amount_usd FROM vkpi_kol_rates WHERE kol_pool_id=? ORDER BY id DESC LIMIT 50", (kol_pool_id,))
        amounts = [a for a in (_f(dict(r).get("amount_usd")) for r in rows or []) if a is not None and a > 0]
        if amounts:
            out["d_rate_median_usd"] = round(statistics.median(amounts), 2)
    if table_exists("vkpi_kol_cooperation_events"):
        row = _safe(conn, "SELECT COUNT(*) AS n FROM vkpi_kol_cooperation_events WHERE kol_pool_id=?", (kol_pool_id,), one=True)
        if row is not None:
            out["d_cooperation_events"] = float(_i(dict(row).get("n")))
    if table_exists("vkpi_kol_video_product_links") and table_exists("vkpi_kol_video_evidence"):
        row = _safe(
            conn,
            """
            SELECT COUNT(*) AS n FROM vkpi_kol_video_product_links l
            JOIN vkpi_kol_video_evidence e ON e.id = l.evidence_id
            WHERE e.kol_pool_id=?
            """,
            (kol_pool_id,), one=True,
        )
        if row is not None:
            out["d_video_product_links"] = float(_i(dict(row).get("n")))


def derived_features(kol_pool_id: int, *, conn: Any = None, pool_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个 KOL 的 20 维派生特征(缺料 None)。pool_row 可传入避免重查。"""
    out = _empty()
    pid = _i(kol_pool_id)
    if pid <= 0:
        return out
    db = conn or get_conn()
    if pool_row is None:
        row = _safe(db, "SELECT real_er, suspect_inflation, audience_estimated_json FROM vkpi_kol_pool WHERE id=?", (pid,), one=True)
        pool_row = dict(row) if row else None
    for step in (_rule11, _final_v1, _lens, _rates_and_history):
        try:
            step(db, pid, out)
        except Exception:
            logger.debug("feature_store_derived.step_failed step=%s", getattr(step, "__name__", "?"), exc_info=True)
    try:
        _pool_columns(pool_row, out)
    except Exception:
        logger.debug("feature_store_derived.pool_columns_failed", exc_info=True)
    return out


_COUNT_KEYS = frozenset({"d_final_v1_videos", "d_lens_family_count", "d_cooperation_events", "d_video_product_links"})
_PERCENT_KEYS = frozenset(
    {"d_audience_top_country_pct", "d_rule11_overall", "d_rule11_engagement_quality",
     "d_rule11_cooperation_history", "d_rule11_competitor_risk"}
    | {key for key, _ in _SIX_SCORES}
)


def derived_numeric_vector(derived: dict[str, Any] | None) -> dict[str, float]:
    """派生特征 → 有界数值向量(缺失=0,缺失率由 feature_coverage 管;0-100 分归一到 0-1;计数走 log1p)。"""
    d = derived or {}
    vec: dict[str, float] = {}
    for key in DERIVED_FEATURE_KEYS:
        val = _f(d.get(key))
        if val is None:
            vec[key] = 0.0
            continue
        if key in _COUNT_KEYS:
            vec[key] = round(math.log1p(max(0.0, val)), 6)
        elif key == "d_rate_median_usd":
            vec[key] = round(math.log1p(max(0.0, val)) / 10.0, 6)
        elif key in _PERCENT_KEYS:
            vec[key] = round(max(0.0, min(1.0, val / 100.0)), 6)
        else:
            vec[key] = round(max(-1.0, min(1.0, val)), 6)
    return vec


def feature_coverage(*, kol_pool_ids: list[int] | None = None, sample_limit: int = 300, conn: Any = None) -> dict[str, Any]:
    """非空率统计(供评估链):默认样本 = MY KOL(收藏 ∪ 成员)∪ 最近推荐过的 KOL,上限 sample_limit。"""
    db = conn or get_conn()
    ids: list[int] = [i for i in (kol_pool_ids or []) if _i(i) > 0]
    if not ids:
        seen: set[int] = set()
        for table, order in (
            ("vkpi_kol_pool_favorites", "id DESC"),
            ("vkpi_kol_pool_members", "id DESC"),
            ("vkpi_kol_recommendations", "id DESC"),
        ):
            if not table_exists(table):
                continue
            rows = _safe(db, f"SELECT kol_pool_id FROM {table} WHERE kol_pool_id IS NOT NULL ORDER BY {order} LIMIT ?", (int(sample_limit),))
            for r in rows or []:
                pid = _i(dict(r).get("kol_pool_id"))
                if pid > 0 and pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
        ids = ids[: int(sample_limit)]
    counts = {key: 0 for key in DERIVED_FEATURE_KEYS}
    for pid in ids:
        feats = derived_features(pid, conn=db)
        for key in DERIVED_FEATURE_KEYS:
            if feats.get(key) is not None:
                counts[key] += 1
    n = len(ids)
    rates = {key: (round(counts[key] / n, 4) if n else None) for key in DERIVED_FEATURE_KEYS}
    eligible = [key for key in DERIVED_FEATURE_KEYS if n and rates[key] is not None and rates[key] > MIN_NONNULL_RATE]
    return {
        "status": "ok" if n else "empty",
        "version": DERIVED_FEATURE_VERSION,
        "sample_n": n,
        "nonnull_rate": rates,
        "min_nonnull_rate": MIN_NONNULL_RATE,
        "eligible_features": eligible,
        "eligible_count": len(eligible),
        "feature_count": len(DERIVED_FEATURE_KEYS),
    }


__all__ = [
    "DERIVED_FEATURE_VERSION", "DERIVED_FEATURE_KEYS", "MIN_NONNULL_RATE",
    "derived_features", "derived_numeric_vector", "feature_coverage",
]
