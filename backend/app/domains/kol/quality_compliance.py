"""KOL 质量分聚合 + FTC 披露扫描(quality_compliance v1)—— 两个读端小件合一个域。

两块(全部纯聚合已有数据,零新采集、零 LLM、零写库):
  📊 quality_score  综合质量分:聚合该 KOL 全部 ready 的 final_v1 深析读数
                    (llm_dimensions_11.scores 六维)→ 0-100 综合分 + 分项条,
                    含样本数与置信度诚实标注。asset_reuse_score 语义方向未经线上
                    抽样确认(口径同 risk_index.py),**不进综合分**,仅作分项展示。
                    composite.provenance 透出打分模型/prompt 版本痕迹
                    (source.final_model / schema_version;缺痕迹计 unknown,
                    漂移哨兵待人工抽检)—— 分数是 LLM 读数,跨版本不可直接对比。
  🛡️ ftc_scan      FTC 披露扫描:词表法扫视频标题/描述(创作者文本)里的披露标记
                    (#ad / #sponsored / sponsored by / paid partnership / affiliate
                    links / 中文披露语)+ 品牌合作迹象(品牌提及/折扣码/深析文本合作
                    信号)共现 → disclosed / undisclosed_suspect / clean 三组清单。
                    有合作迹象但零披露标记 → risk 条目(info/warn,措辞「疑似未披露」,
                    绝不下法律结论)。

词表法负例防线(真库取证):
  - `#ad` 用 `#ads?\\b` 正则,避免 `#adobe` / `#adizeroboston1` 误命中;
  - 否定感知:先抹掉「Not Sponsored / Not Gifted / 不是广告 / 无赞助」类否定片段
    再匹配(真库存在 "Not Sponsored. Not Gifted. My Lumix L10 Review");
  - 披露标记只认创作者文本(标题/描述);深析文本(Gemini 分析师口吻)只作
    合作迹象参考,绝不当披露证据。

数据源(全只读):vkpi_kol_pool(含 raw_platform_data.videos 的 YouTube snippet
描述) / vkpi_kol_video_evidence / vkpi_kol_llm_deep_analysis_results
(analysis_kind='video_final_v1', status='ready', llm_dimensions_11 JSONB)。

compat 约定:SQL 占位符用 ?;SQL 字符串零 percent 字面(不用 LIKE);BOOLEAN
读回可能是 int 1/0,判断走 _truthy;JSONB 双态(dict/str)走 _loads。

红线:这是独立展示分,绝不写回任何表;绝不读写 viltrox_fit_score、不碰 rule_v0;
零 LLM/provider 调用;找不到数据诚实空态,绝不杜撰。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

DEEP_TABLE = "vkpi_kol_llm_deep_analysis_results"
ANALYSIS_KIND = "video_final_v1"

# ── 质量分维度(final_v1 llm_dimensions_11.scores 真实键名,真库取证 478/478 行)──
# (key, 中文标签, 综合分权重;None = 不进综合分)
QUALITY_DIMENSIONS: tuple[tuple[str, str, float | None], ...] = (
    ("content_quality_score", "内容质量(画面/制作)", 0.35),
    ("marketing_value_score", "营销价值", 0.25),
    ("product_proof_score", "产品实证力", 0.15),
    ("viewer_heart_score", "观众共鸣", 0.15),
    ("channel_value_score", "频道价值", 0.10),
    # asset_reuse 语义方向未经线上抽样确认(risk_index.py 同款声明),不进综合分。
    ("asset_reuse_score", "素材复用价值", None),
)

# ── FTC 披露标记词表(只扫创作者文本 = 标题+描述;先过否定抹除再匹配)──────────
DISCLOSURE_MARKERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("hashtag_ad", "#ad 标签", re.compile(r"#ads?\b")),
    ("hashtag_sponsored", "#sponsored 标签", re.compile(r"#sponsor(?:ed)?\b")),
    ("hashtag_gifted", "#gifted 标签", re.compile(r"#gifted\b")),
    ("paid_partnership", "paid partnership 声明", re.compile(r"paid\s+partnership|#paidpartnership\b")),
    ("sponsored_statement", "sponsored by 声明", re.compile(r"sponsored\s+by\b|video\s+(?:is|was)\s+sponsored\b")),
    ("paid_promotion", "付费推广声明", re.compile(r"paid\s+promotion\b|includes\s+paid\s+promotion")),
    ("affiliate_disclosure", "affiliate 链接声明", re.compile(r"affiliate\s+links?\b|#affiliate\b")),
    ("gifted_statement", "gifted 声明", re.compile(r"gifted\s+by\b|was\s+gifted\b")),
    ("partnership_statement", "in partnership with 声明", re.compile(r"in\s+partnership\s+with\b")),
    ("cn_disclosure", "中文披露声明", re.compile(r"广告合作|商业合作|付费推广|品牌赞助|由.{0,12}赞助|含广告|利益相关")),
)

# ── 合作迹象(创作者文本弱信号)──────────────────────────────────────────────
CREATOR_COOP_SIGNALS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("brand_mention", "标题/描述提及品牌", re.compile(r"viltrox")),
    ("partner_word", "partner/collab 弱词", re.compile(r"\bpartner(?:ship|ed)?\b|\bcollab(?:oration)?\b|\bambassador\b")),
    ("discount_code", "折扣码/专属码", re.compile(r"discount\s+code|use\s+code\b|promo\s+code|coupon\s+code|折扣码|优惠码|专属码")),
)

# ── 合作迹象(深析文本词表;Gemini 分析师口吻,仅作迹象、不作披露证据)────────
DEEP_COOP_TERMS: tuple[str, ...] = (
    "恰饭", "商单", "赞助", "广告合作", "品牌合作", "合作视频", "厂商提供",
    "送测", "寄送样品", "官方合作", "sponsored", "paid placement", "投放",
)

# ── 否定抹除(匹配前先把「明确否定披露/合作」的片段抹成空格)────────────────
_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:\bnot\b|\bno\b|\bnon\b|\bnever\b|\bisn'?t\b|\bwasn'?t\b|\bwithout\b)[\s\-]*(?:sponsored\b|sponsors?\b|sponsorship\b|gifted\b|paid\b|affiliated?\b|an?\s+ad\b|ad\b)"),
    re.compile(r"\bunsponsored\b|\bun[\s\-]sponsored\b"),
    re.compile(r"(?:is|was)\s+this\s+sponsored[\s!?？]*"),
    re.compile(r"(?:不是|并非|没有|无|非|未见|未提及|不含|不涉及|未)\s*(?:广告|赞助|恰饭|商单|合作|品牌露出|投放)"),
)

_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{6,20})")


# ── 小工具(容错;compat 双态)────────────────────────────────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict/list 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


from app.core.coerce import _truthy


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
        num = float(value)
    except Exception:
        return None
    if num != num:  # NaN
        return None
    return num


def _score_number(value: Any) -> float | None:
    """score 既可能是裸数,也可能是 {'score': ...} 包裹;统一取 0-100 或 None。"""
    if isinstance(value, dict):
        value = value.get("score")
    num = _float_or_none(value)
    if num is None:
        return None
    return max(0.0, min(100.0, num))


def _strip_negations(text: str) -> str:
    """把「明确否定」片段抹成空格再匹配(词表法负例防线;text 需已小写)。"""
    if not text:
        return ""
    for pattern in _NEGATION_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 数据装载(全只读;? 占位;零 percent 字面)───────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, handle, display_name, platform, raw_platform_data FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_deep_rows(conn: Any, kol_pool_id: int, limit: int = 300) -> list[dict[str, Any]]:
    """该 KOL 全部 ready 的 final_v1 独立深析行(+ evidence 展示字段);
    同一 evidence 多行时保留最新(created_at desc 去重)。
    刻意不 SELECT llm_v6_fit:本域是独立展示读数,连读都不读评分列。"""
    rows = conn.execute(
        """
        SELECT
          d.id AS deep_id,
          d.source_evidence_id AS evidence_id,
          d.llm_dimensions_11 AS dims,
          d.confidence AS confidence,
          d.created_at AS created_at,
          COALESCE(e.video_title, e.title, '') AS title,
          e.content_url,
          e.platform,
          e.view_count
        FROM vkpi_kol_llm_deep_analysis_results d
        LEFT JOIN vkpi_kol_video_evidence e ON e.id = d.source_evidence_id
        WHERE d.kol_pool_id = ?
          AND d.analysis_kind = ?
          AND d.status = 'ready'
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), ANALYSIS_KIND, int(limit)),
    ).fetchall()
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        eid = _int_or_none(item.get("evidence_id"))
        if eid is not None:
            if eid in seen:
                continue
            seen.add(eid)
        out.append(item)
    return out


def _load_evidence_rows(conn: Any, kol_pool_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          id AS evidence_id,
          COALESCE(video_title, title, '') AS title,
          content_url,
          platform,
          view_count,
          is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


# ── 📊 块一:综合质量分 ─────────────────────────────────────────────────────


def _video_scores(dims: dict[str, Any]) -> dict[str, float]:
    scores = _as_dict(dims.get("scores"))
    out: dict[str, float] = {}
    for key, _label, _weight in QUALITY_DIMENSIONS:
        num = _score_number(scores.get(key))
        if num is not None:
            out[key] = num
    return out


def _composite_of(scores: dict[str, float]) -> float | None:
    """按可得维度重新归一化的加权均值(缺失维度不当 0 拉低分)。"""
    contributions = [
        (weight, scores[key])
        for key, _label, weight in QUALITY_DIMENSIONS
        if weight is not None and key in scores
    ]
    weight_sum = sum(w for w, _ in contributions)
    if weight_sum <= 0:
        return None
    return sum(w * v for w, v in contributions) / weight_sum


def _confidence_label(sample_size: int) -> str:
    if sample_size >= 10:
        return "high"
    if sample_size >= 3:
        return "medium"
    return "low"


def quality_score(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """综合质量分:聚合 final_v1 深析六维读数;KOL 不存在抛 LookupError(路由转 404)。

    红线:独立展示分,绝不写回任何表、不参与任何选人/推荐评分。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    deep_rows = _load_deep_rows(db, int(kol_pool_id))
    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    base = {
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "coverage": {
            "evidence_count": len(evidence_rows),
            "deep_analyzed_count": len(deep_rows),
        },
        "generated_at": _now_iso(),
        "note": "聚合 final_v1 深析读数(零 LLM、零写库);独立展示分,不参与任何选人评分。",
    }
    if not deep_rows:
        return {
            **base,
            "status": "empty",
            "reason": "该 KOL 还没有已深析(final_v1)视频,质量分无从聚合 — 先跑「KOL深度分析理解」。",
        }

    per_dim: dict[str, list[float]] = {}
    per_video: list[dict[str, Any]] = []
    llm_confidences: list[float] = []
    # 版本痕迹(漂移哨兵素材):llm_dimensions_11 自带 source.final_model(打分模型)
    # 与顶层 schema_version(prompt/schema 版本);没有痕迹的行如实计 unknown。
    model_counts: dict[str, int] = {}
    prompt_version_counts: dict[str, int] = {}
    for row in deep_rows:
        dims = _as_dict(_loads(row.get("dims")))
        scores = _video_scores(dims)
        if not scores:
            continue
        source_meta = _as_dict(dims.get("source"))
        model = _text(source_meta.get("final_model"), 80) or "unknown"
        prompt_version = _text(dims.get("schema_version"), 120) or "unknown"
        model_counts[model] = model_counts.get(model, 0) + 1
        prompt_version_counts[prompt_version] = prompt_version_counts.get(prompt_version, 0) + 1
        for key, val in scores.items():
            per_dim.setdefault(key, []).append(val)
        conf = _float_or_none(row.get("confidence"))
        if conf is not None:
            llm_confidences.append(max(0.0, min(1.0, conf)))
        composite = _composite_of(scores)
        per_video.append(
            {
                "evidence_id": _int_or_none(row.get("evidence_id")),
                "title": _text(row.get("title"), 140),
                "content_url": _text(row.get("content_url"), 500),
                "view_count": _int_or_none(row.get("view_count")),
                "composite": round(composite, 1) if composite is not None else None,
                "scores": {k: round(v, 1) for k, v in scores.items()},
            }
        )

    if not per_video:
        return {
            **base,
            "status": "empty",
            "reason": f"有 {len(deep_rows)} 条深析但均未解析出 scores 读数,质量分无从聚合(不硬造)。",
        }

    dimensions: list[dict[str, Any]] = []
    for key, label, weight in QUALITY_DIMENSIONS:
        values = per_dim.get(key) or []
        if not values:
            continue
        entry: dict[str, Any] = {
            "key": key,
            "label": label,
            "avg": round(sum(values) / len(values), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "n": len(values),
            "weight": weight,
            "in_composite": weight is not None,
        }
        if key == "asset_reuse_score":
            entry["note"] = "语义方向未经线上抽样确认,仅作分项展示,不进综合分。"
        dimensions.append(entry)

    dim_avgs = {d["key"]: float(d["avg"]) for d in dimensions}
    composite = _composite_of(dim_avgs)
    scored = [v for v in per_video if v.get("composite") is not None]
    scored.sort(key=lambda v: v["composite"], reverse=True)
    sample_size = len(per_video)

    return {
        **base,
        "status": "ready",
        "sample_size": sample_size,
        "composite": {
            "score": round(composite, 1) if composite is not None else None,
            "method": "final_v1_scores_weighted_mean_v1",
            "weights": {key: weight for key, _label, weight in QUALITY_DIMENSIONS if weight is not None},
            "basis": "按可得维度归一化加权;asset_reuse 不进综合分(方向未确认);打分模型/prompt 版本痕迹见 provenance。",
            "provenance": {
                "llm_models": dict(sorted(model_counts.items(), key=lambda kv: kv[1], reverse=True)),
                "prompt_versions": dict(sorted(prompt_version_counts.items(), key=lambda kv: kv[1], reverse=True)),
                "unknown_model_count": model_counts.get("unknown", 0),
                "unknown_prompt_version_count": prompt_version_counts.get("unknown", 0),
                "note": (
                    "版本痕迹取自 llm_dimensions_11 自带字段(source.final_model / schema_version),"
                    "本模块只转述不新采集;unknown = 该行无版本痕迹,分数可能随模型/prompt 漂移,"
                    "漂移哨兵待人工抽检;多模型混跑时综合分是跨版本混合读数,对比前先看本表。"
                ),
            },
        },
        "dimensions": dimensions,
        "confidence": {
            "label": _confidence_label(sample_size),
            "sample_size": sample_size,
            "llm_confidence_avg": round(sum(llm_confidences) / len(llm_confidences), 3) if llm_confidences else None,
        },
        "best_videos": scored[:3],
        "weakest_video": scored[-1] if len(scored) > 3 else None,
    }


# ── 🛡️ 块二:FTC 披露扫描 ───────────────────────────────────────────────────


def _extract_video_id(url: str) -> str | None:
    match = _YT_ID_RE.search(url or "")
    return match.group(1) if match else None


def _description_map(pool_row: dict[str, Any]) -> dict[str, dict[str, str]]:
    """raw_platform_data.videos(YouTube API items)→ {video_id: {title, description}}。
    item.id 可能是 str 也可能是 {videoId};非 YouTube 形状防御性跳过。"""
    raw = _as_dict(_loads(pool_row.get("raw_platform_data")))
    out: dict[str, dict[str, str]] = {}
    for item in _as_list(raw.get("videos"))[:200]:
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        if isinstance(vid, dict):
            vid = vid.get("videoId")
        vid = _text(vid, 40)
        snippet = _as_dict(item.get("snippet"))
        title = _text(snippet.get("title") or item.get("title"), 300)
        description = _text(snippet.get("description") or item.get("description") or item.get("caption"), 4000)
        if not vid:
            url = _text(item.get("url") or item.get("video_url") or item.get("link"), 500)
            vid = _extract_video_id(url) or ""
        if vid and (title or description):
            out[vid] = {"title": title, "description": description}
    return out


def _deep_text_blob(dims: dict[str, Any]) -> str:
    """深析文本底料(分析师口吻,仅作合作迹象参考):layer1 摘要/品牌露出/产品在场
    + risk 三件 + recommendations.why(全小写)。"""
    layer1 = _as_dict(dims.get("layer1_summary"))
    risk = _as_dict(dims.get("risk"))
    reco = _as_dict(dims.get("recommendations"))
    flags = risk.get("risk_flags")
    if isinstance(flags, list):
        flags = " ".join(_text(f, 300) for f in flags)
    parts = [
        _text(layer1.get("content_summary"), 1200),
        _text(layer1.get("brand_exposure"), 600),
        _text(layer1.get("product_presence"), 600),
        _text(layer1.get("competitor_presence"), 400),
        _text(flags, 800),
        _text(risk.get("final_verdict"), 600),
        _text(risk.get("key_hook"), 300),
        _text(reco.get("why"), 600),
    ]
    return " ".join(p for p in parts if p).lower()


def _scan_creator_text(blob: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """创作者文本(标题+描述,已小写)→ (披露标记, 合作弱信号)。先抹否定再匹配。"""
    cleaned = _strip_negations(blob)
    markers = [
        {"key": key, "label": label}
        for key, label, pattern in DISCLOSURE_MARKERS
        if cleaned and pattern.search(cleaned)
    ]
    signals = [
        {"key": key, "label": label, "source": "creator_text"}
        for key, label, pattern in CREATOR_COOP_SIGNALS
        if cleaned and pattern.search(cleaned)
    ]
    return markers, signals


def _scan_deep_text(blob: str) -> list[dict[str, str]]:
    """深析文本(已小写)→ 合作迹象信号(词表;先抹否定)。"""
    cleaned = _strip_negations(blob)
    if not cleaned.strip():
        return []
    signals: list[dict[str, str]] = []
    matched_terms = [term for term in DEEP_COOP_TERMS if term in cleaned]
    if matched_terms:
        signals.append(
            {
                "key": "deep_coop",
                "label": "深析文本合作迹象(" + "/".join(matched_terms[:4]) + ")",
                "source": "deep_analysis",
            }
        )
    if "viltrox" in cleaned:
        signals.append({"key": "deep_brand_mention", "label": "深析确认品牌出镜", "source": "deep_analysis"})
    return signals


def _suspect_level(signals: list[dict[str, str]]) -> str:
    """疑似未披露分级:强迹象(深析合作词/折扣码)→ warn,其余弱信号 → info。"""
    strong_keys = {"deep_coop", "discount_code"}
    return "warn" if any(s.get("key") in strong_keys for s in signals) else "info"


def ftc_scan(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """FTC 披露扫描:词表法(否定感知)三组清单;KOL 不存在抛 LookupError。

    诚实口径:披露标记只认创作者文本(标题/描述);描述未采集的视频只扫了标题
    (+深析迹象),clean ≠ 确认无风险。全程启发式,绝不下法律结论。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    base = {
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "method": "lexicon_v1_negation_aware",
        "generated_at": _now_iso(),
        "note": "词表法启发式(零 LLM、零写库),非法律结论;披露标记只认创作者文本(标题/描述),深析文本只作合作迹象参考。",
    }
    if not evidence_rows:
        return {
            **base,
            "status": "empty",
            "reason": "该 KOL 暂无视频证据(vkpi_kol_video_evidence 空),无从扫描披露标记。",
        }

    desc_map = _description_map(pool)
    deep_rows = _load_deep_rows(db, int(kol_pool_id))
    deep_by_eid: dict[int, dict[str, Any]] = {}
    for row in deep_rows:
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None:
            continue
        dims = _as_dict(_loads(row.get("dims")))
        if dims:
            deep_by_eid[eid] = dims

    groups: dict[str, list[dict[str, Any]]] = {"disclosed": [], "undisclosed_suspect": [], "clean": []}
    risks: list[dict[str, Any]] = []
    with_description = 0
    with_deep = 0

    for row in evidence_rows:
        eid = _int_or_none(row.get("evidence_id"))
        title = _text(row.get("title"), 300)
        url = _text(row.get("content_url"), 500)
        vid = _extract_video_id(url)
        desc_entry = desc_map.get(vid or "") or {}
        description = desc_entry.get("description") or ""
        if not title:
            title = desc_entry.get("title") or ""
        scanned_sources = ["title"] if title else []
        if description:
            scanned_sources.append("description")
            with_description += 1

        creator_blob = (title + "\n" + description).lower()
        markers, signals = _scan_creator_text(creator_blob)

        dims = deep_by_eid.get(eid or -1) or {}
        if dims:
            scanned_sources.append("deep_analysis")
            with_deep += 1
            signals.extend(_scan_deep_text(_deep_text_blob(dims)))

        item: dict[str, Any] = {
            "evidence_id": eid,
            "title": title or "(无标题)",
            "content_url": url,
            "platform": _text(row.get("platform"), 30),
            "view_count": _int_or_none(row.get("view_count")),
            "scanned_sources": scanned_sources,
            "disclosure_markers": markers,
            "cooperation_signals": signals,
        }
        if markers:
            groups["disclosed"].append(item)
        elif signals:
            level = _suspect_level(signals)
            item["level"] = level
            groups["undisclosed_suspect"].append(item)
            if len(risks) < 20:
                labels = "、".join(s["label"] for s in signals[:3])
                risks.append(
                    {
                        "level": level,
                        "evidence_id": eid,
                        "title": item["title"][:120],
                        "message": f"疑似未披露:检测到品牌合作迹象({labels})但未发现披露标记(词表启发式,非法律结论)。",
                    }
                )
        else:
            groups["clean"].append(item)

    # warn 优先排前;组内保持播放量降序(evidence_rows 已按 view_count 排)。
    groups["undisclosed_suspect"].sort(key=lambda v: 0 if v.get("level") == "warn" else 1)
    risks.sort(key=lambda r: 0 if r.get("level") == "warn" else 1)
    warn_count = sum(1 for v in groups["undisclosed_suspect"] if v.get("level") == "warn")
    risk_level = "warn" if warn_count else ("info" if groups["undisclosed_suspect"] else "none")

    return {
        **base,
        "status": "ready",
        "coverage": {
            "evidence_count": len(evidence_rows),
            "with_description": with_description,
            "with_deep_analysis": with_deep,
        },
        "summary": {
            "disclosed": len(groups["disclosed"]),
            "undisclosed_suspect": len(groups["undisclosed_suspect"]),
            "clean": len(groups["clean"]),
            "warn_count": warn_count,
            "risk_level": risk_level,
        },
        "risks": risks,
        "groups": {
            "disclosed": groups["disclosed"][:50],
            "undisclosed_suspect": groups["undisclosed_suspect"][:50],
            "clean": groups["clean"][:50],
        },
        "coverage_note": "描述文本仅覆盖 raw_platform_data 已采集的视频;未覆盖的只扫了标题(+深析迹象),clean ≠ 确认无风险。",
    }
