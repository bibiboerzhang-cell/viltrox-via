"""C6 KOL 风险指数(候选 A:实时聚合,零新 LLM)。

纯只读聚合,把已落库的 **结构化深析信号**(不是关键词 SQL)在 Python 端算成
「每 KOL 一个风险分 + 维度拆解」。数据源唯一:

    vkpi_kol_llm_deep_analysis_results.llm_dimensions_11  (JSONB)
      analysis_kind = 'video_final_v1'
      status        = 'ready'
      provider      = gemini(final_v1 6 层)

`llm_dimensions_11` 真实形状取证自 final_v1_extract._build_dimensions / account_dossier:
  {
    "scores": {                       # _normalised_scores(layer6) 出来的归一分
       "content_quality_score": {"score": 0-100, "confidence": 0-1, ...} | 0-100,
       "marketing_value_score": {...} | 0-100,
       "asset_reuse_score":     {...} | 0-100,
       ...
    },
    "layer1_summary": {
       "brand_exposure":      str,    # 463/468 是「字符串」而非结构化对象(summary.py 取证)
       "competitor_presence": str,
       "content_summary":     str,
       "production_observations": str,
    },
    "risk": {"risk_flags": str|list, "final_verdict": str, ...},
    "llm_v6_fit": {"score": 0-100, ...},
  }
每个 score 既可能是裸数,也可能是 {"score": ...} 包裹 —— 两种都要解。

风险口径(0-100,越高越该规避):
    risk = w1 * 无深度分量 (100-content_quality)/100 * 100
         + w2 * 素材复用分量 (asset_reuse_score,方向见下)
         + w3 * 竞品露出惩罚 (否定感知文本判定,命中给满档)
按 KOL 聚合其所有已深析视频(各分量先按视频算,再对该 KOL 取均值/最大)。

**只覆盖已深析 KOL**。某 KOL 在本表没有任何 ready video_final_v1 行 →
analyzed=False / risk_index=None(前端显「未分析」灰态),**绝不当 0 风险**。

asset_reuse 方向(C6 明确标注「先抽样确认语义方向」):
  本地只有线上 Postgres,无法抽样确认。这里采用保守假设
  「asset_reuse_score 越高 = 复用/白嫖越多 = 风险越高」,但在输出里带
  asset_reuse_direction_confirmed=False,集成者按线上真数据确认后再信 w2。
  若确认方向相反(高=原创度高=风险低),把 _asset_reuse_component 里的
  `value` 改成 `100 - value` 即可,其余不动。

红线:纯 SELECT,? 占位(方言层翻译);绝不读写 viltrox_fit_score / rule_v0 /
评分指纹;绝不调任何 LLM/provider。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.db.connection import table_exists
from app.domains.kol.competitor_text import _keyword_match, load_competitor_brands


DEEP_TABLE = "vkpi_kol_llm_deep_analysis_results"
ANALYSIS_KIND = "video_final_v1"

# 默认权重(和为 1.0)。w1 无深度最重,w3 竞品次之,w2 复用方向未确认故最轻。
DEFAULT_WEIGHTS: dict[str, float] = {"w1": 0.5, "w2": 0.2, "w3": 0.3}

# 否定「占位」正则:口径对齐 dashboard/summary.py 的 Viltrox 否定处理 —— 把「明确说没有/
# 未提及」的片段抹成空格,再看是否还有竞品/品牌关键词存活。同时覆盖否定在前与否定在后。
# 这里是通用版(不绑定具体品牌),竞品关键词残留判定在 _competitor_component 里做。
_NEG_PREFIX = (
    r"(no|none|zero|not|neither|without|n/?a|未提及|未见|未出现|没有|无|缺位|未|零|不涉及|不含)"
)
_NEG_SUFFIX = (
    r"(为\s*0|0\s*%|零|无露出|缺位|neither|not mentioned|not visible|"
    r"not present|not featured|none|absent)"
)


def _loads(value: Any, default: Any) -> Any:
    """psycopg 可能给回已解析 dict/list,也可能是 str —— 防御性解析。"""
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _score_number(value: Any) -> float | None:
    """score 既可能是裸数,也可能是 {'score': ...} 包裹;统一取出 0-100 数或 None。"""
    if isinstance(value, dict):
        value = value.get("score")
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    return max(0.0, min(100.0, num))


def _flag_text(value: Any) -> str:
    """risk_flags 可能是 str 或 list[str|dict];拼成一段文本供否定感知扫描。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(" ".join(str(v) for v in item.values() if v is not None))
        return " || ".join(parts)
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if v is not None)
    return ""


def _strip_negations(text: str) -> str:
    """把「明确否定」的片段抹成空格(否定词 ±窗口内的品牌提及),再判残留。

    先抹「否定在后」(品牌窗口 ... 否定后缀),再抹「否定在前」(否定词 ... 窗口)。
    顺序要紧:neither/not 这类词同时在前/后两表里,若先跑前缀会先吃掉 neither 本身,
    导致「Sigma is neither mentioned nor visible」里 neither 前的 Sigma 残活而漏判否定。
    故先用后缀 pass 在原文上把「品牌 ... neither/not visible」整段抹掉。
    """
    if not text:
        return ""
    # ① 否定在后:品牌窗口 ... 否定后缀(在原文上先跑,确保 neither 前的品牌一并被抹)。
    cleaned = re.sub(r"[^。.!?；;\n]{0,18}" + _NEG_SUFFIX, " ", text, flags=re.IGNORECASE)
    # ② 否定在前:否定词 ... <窗口>(留下的品牌词会一起被抹)。
    cleaned = re.sub(_NEG_PREFIX + r"[^。.!?；;\n]{0,18}", " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _depth_component(dimensions: dict[str, Any]) -> float | None:
    """无深度分量 = (100 - content_quality_score)/100 * 100 = 100 - content_quality。

    content_quality 缺失 → None(该视频此分量不参与该 KOL 均值,而非当 0)。
    """
    scores = _as_dict(dimensions.get("scores"))
    cq = _score_number(scores.get("content_quality_score"))
    if cq is None:
        return None
    return max(0.0, min(100.0, 100.0 - cq))


def _asset_reuse_component(dimensions: dict[str, Any]) -> float | None:
    """素材复用分量。方向未确认(见模块 docstring):暂取 asset_reuse_score 原值
    (高=复用多=风险高)。缺失 → None。"""
    scores = _as_dict(dimensions.get("scores"))
    value = _score_number(scores.get("asset_reuse_score"))
    if value is None:
        return None
    return value  # 若线上确认方向相反,改为 100 - value


def _competitor_component(dimensions: dict[str, Any], brands: dict[str, dict[str, Any]]) -> tuple[float, list[str]]:
    """竞品露出惩罚。把 layer1 的 competitor_presence / brand_exposure / content_summary +
    risk_flags 拼成一段文本,**先抹掉明确否定片段**(同 summary.py 口径),再用竞品关键词
    扫残留。命中给满档 100,未命中 0。返回 (分值, 命中竞品名列表)。

    诚实:这是「该视频是否真有竞品肯定露出」的二元信号,不是连续分;肯定命中即满惩罚。
    """
    layer1 = _as_dict(dimensions.get("layer1_summary"))
    risk = _as_dict(dimensions.get("risk"))
    blob = " || ".join(
        [
            str(layer1.get("competitor_presence") or ""),
            str(layer1.get("brand_exposure") or ""),
            str(layer1.get("content_summary") or ""),
            _flag_text(risk.get("risk_flags")),
        ]
    )
    if not blob.strip():
        return 0.0, []
    cleaned = _strip_negations(blob).lower()
    if not cleaned.strip():
        return 0.0, []
    matched: list[str] = []
    for brand, config in brands.items():
        for keyword in config.get("keywords", []):
            if keyword and _keyword_match(cleaned, keyword):
                matched.append(brand)
                break
    return (100.0 if matched else 0.0), matched


def _avg(values: list[float]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _aggregate_kol(
    rows: list[dict[str, Any]],
    *,
    weights: dict[str, float],
    brands: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """把一个 KOL 的所有已深析视频聚合成一行风险拆解。rows 已是该 KOL 的 ready 行。"""
    depth_vals: list[float] = []
    reuse_vals: list[float] = []
    competitor_vals: list[float] = []
    competitor_brands_hit: set[str] = set()

    for row in rows:
        dims = _as_dict(_loads(row.get("llm_dimensions_11"), {}))
        d = _depth_component(dims)
        if d is not None:
            depth_vals.append(d)
        r = _asset_reuse_component(dims)
        if r is not None:
            reuse_vals.append(r)
        comp_value, comp_hits = _competitor_component(dims, brands)
        competitor_vals.append(comp_value)
        competitor_brands_hit.update(comp_hits)

    depth_avg = _avg(depth_vals)
    reuse_avg = _avg(reuse_vals)
    # 竞品分量:取该 KOL 视频里的「最大」(任一视频有竞品肯定露出即该 KOL 有露出)。
    competitor_score = max(competitor_vals) if competitor_vals else 0.0

    # 加权合成:只有「有数据的分量」参与,权重按可得分量重新归一化(避免缺失分量被当 0 拉低分)。
    contributions: list[tuple[float, float]] = []  # (weight, value)
    if depth_avg is not None:
        contributions.append((weights["w1"], depth_avg))
    if reuse_avg is not None:
        contributions.append((weights["w2"], reuse_avg))
    # 竞品分量总是有(0 或 100,基于文本),纳入合成。
    contributions.append((weights["w3"], competitor_score))

    weight_sum = sum(w for w, _ in contributions)
    if weight_sum <= 0:
        risk_index: float | None = None
    else:
        risk_index = sum(w * v for w, v in contributions) / weight_sum

    return {
        "analyzed": True,
        "analyzed_video_count": len(rows),
        "risk_index": round(risk_index, 1) if risk_index is not None else None,
        "dimensions": {
            "no_depth": {
                "label": "内容无深度",
                "score": round(depth_avg, 1) if depth_avg is not None else None,
                "weight": weights["w1"],
                "available": depth_avg is not None,
                "source": "100 - content_quality_score(均值)",
            },
            "asset_reuse": {
                "label": "素材复用",
                "score": round(reuse_avg, 1) if reuse_avg is not None else None,
                "weight": weights["w2"],
                "available": reuse_avg is not None,
                "source": "asset_reuse_score(均值)",
                "direction_confirmed": False,
            },
            "competitor_exposure": {
                "label": "竞品露出",
                "score": competitor_score,
                "weight": weights["w3"],
                "available": True,
                "matched_brands": sorted(competitor_brands_hit),
                "source": "competitor_presence/brand_exposure/content_summary/risk_flags(否定感知)",
            },
        },
    }


def _fetch_rows(conn: Any, kol_pool_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """一次拉这批 KOL 的全部 ready video_final_v1 行,按 kol_pool_id 分桶。? 占位。"""
    if not kol_pool_ids:
        return {}
    placeholders = ",".join("?" for _ in kol_pool_ids)
    rows = conn.execute(
        f"""
        SELECT kol_pool_id, llm_dimensions_11
        FROM {DEEP_TABLE}
        WHERE analysis_kind = ?
          AND status = 'ready'
          AND kol_pool_id IN ({placeholders})
        """,  # noqa: S608 — DEEP_TABLE/ANALYSIS_KIND 为受控常量;ids 全走 ? 占位
        (ANALYSIS_KIND, *[int(i) for i in kol_pool_ids]),
    ).fetchall()
    buckets: dict[int, list[dict[str, Any]]] = {}
    for raw in rows:
        item = dict(raw)
        try:
            kid = int(item.get("kol_pool_id"))
        except (TypeError, ValueError):
            continue
        buckets.setdefault(kid, []).append(item)
    return buckets


def compute_risk_index(
    conn: Any,
    kols: list[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """对一批 KOL(每项至少含 kol_pool_id)算风险指数 + 维度拆解。

    Parameters
    ----------
    conn  : app get_conn() 连接(? 占位,方言层翻译为 Postgres)。
    kols  : MY KOL 集,每项形如 {kol_pool_id, handle, display_name, platform, ...}
            (来自 my_kol_aggregate._pool_favorites 同款行;只用得到 id + 展示字段)。
    weights : 可覆盖默认 w1/w2/w3;非法则回落默认。

    Returns
    -------
    {
      "items": [ {kol_pool_id, handle, display_name, platform, analyzed,
                  risk_index | None, dimensions{...}} ],
      "summary": {analyzed_kol_count, unanalyzed_kol_count, coverage_ratio,
                  high_risk_count, weights, source, llm_calls, write_db},
      "weights": {...},
    }
    未深析 KOL:analyzed=False / risk_index=None(前端显「未分析」,非 0 风险)。
    """
    eff_weights = dict(DEFAULT_WEIGHTS)
    if isinstance(weights, dict):
        for key in ("w1", "w2", "w3"):
            try:
                val = float(weights.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if val >= 0:
                eff_weights[key] = val

    # 去重 KOL id(保序),并保留展示字段。
    by_id: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for kol in kols:
        try:
            kid = int(kol.get("kol_pool_id"))
        except (TypeError, ValueError):
            continue
        if kid not in by_id:
            by_id[kid] = kol
            order.append(kid)

    # 表缺失(本地 sqlite 无该表)→ 优雅降级:全部「未分析」,而非 500。
    has_table = table_exists(DEEP_TABLE)
    buckets = _fetch_rows(conn, order) if has_table else {}

    brands = load_competitor_brands()
    items: list[dict[str, Any]] = []
    analyzed_count = 0
    high_risk_count = 0
    for kid in order:
        meta = by_id[kid]
        base = {
            "kol_pool_id": kid,
            "handle": meta.get("handle"),
            "display_name": meta.get("display_name"),
            "platform": meta.get("platform"),
        }
        rows = buckets.get(kid) or []
        if not rows:
            items.append(
                {
                    **base,
                    "analyzed": False,
                    "analyzed_video_count": 0,
                    "risk_index": None,
                    "dimensions": None,
                }
            )
            continue
        analyzed_count += 1
        agg = _aggregate_kol(rows, weights=eff_weights, brands=brands)
        if agg.get("risk_index") is not None and agg["risk_index"] >= 60:
            high_risk_count += 1
        items.append({**base, **agg})

    total = len(order)
    unanalyzed = total - analyzed_count
    return {
        "items": items,
        "summary": {
            "kol_count": total,
            "analyzed_kol_count": analyzed_count,
            "unanalyzed_kol_count": unanalyzed,
            "coverage_ratio": round(analyzed_count / total, 4) if total else 0.0,
            "high_risk_count": high_risk_count,
            "weights": eff_weights,
            "source": DEEP_TABLE,
            "deep_table_available": has_table,
            "llm_calls": False,
            "write_db": False,
            "notes": {
                "unanalyzed": "未深析 KOL 显示『未分析』(risk_index=null),不当 0 风险",
                "asset_reuse_direction": "asset_reuse 方向未经线上抽样确认,direction_confirmed=false",
                "competitor": "竞品露出用否定感知文本判定(口径同 dashboard/summary.py),非纯子串",
            },
        },
        "weights": eff_weights,
    }
