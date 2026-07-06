"""以视频找相似(video_similarity v0)——「还有谁在拍同款内容」的决定性相似检索。

给一条已深析(final_v1)视频,在全库已深析视频里找内容相似的视频与其背后 KOL。

向量库侦察结论(2026-07 本轮):本地 runtime/vkpi_qdrant 只有 KOL 档案级索引
(vkpi_kol_profile_index_v1,粒度=KOL 非视频,且查询需 OpenAI embedding 调用),
不存在视频级向量库 → 走决定性降级,method 诚实标 dimensions_cosine_v0:
  ① 数值向量余弦:llm_dimensions_11 的数值维(scores 六维 + llm_v6_fit)做余弦相似;
  ② 词表标签 Jaccard:layer1_summary 文本 + 标题 → 拍法(复用 signature_profile
     的 SHOOTING_MODES 词表)/ 品类 / 焦段(正则)/ 平台 标签集合的 Jaccard;
  ③ 混合打分 similarity = 0.6 × cosine + 0.4 × jaccard(某一半缺失时诚实退单半)。
全程纯 SQL + 词表/算术,零 LLM、零新采集、决定性可复算。

诚实态:evidence 不存在抛 LookupError(路由转 404);该视频没深析或全库没有第二条
可比视频 → {status:"empty", reason:...},绝不杜撰相似结果;相似原因只列真实共同标签。

数据源(全只读):vkpi_kol_llm_deep_analysis_results(analysis_kind='video_final_v1',
status='ready',llm_dimensions_11 jsonb)/ vkpi_kol_video_evidence / vkpi_kol_pool。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回可能
是 int 1/0,判断走宽容写法;JSONB 读回 dict/str 双态容错。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0;独立展示信号,
不参与 V6 Fit 评分。
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "dimensions_cosine_v0"

# 数值向量的固定维(canonical 顺序;缺哪维就跳哪维,只在双方共有维上做余弦)。
SCORE_KEYS: tuple[str, ...] = (
    "asset_reuse_score",
    "viewer_heart_score",
    "channel_value_score",
    "product_proof_score",
    "content_quality_score",
    "marketing_value_score",
)
FIT_KEY = "llm_v6_fit"
MIN_SHARED_DIMS = 3  # 共有维少于这个数,余弦没意义,诚实置 None

# 混合权重(决定性常量;某一半缺失时整权退给另一半,payload 里如实报)
W_COSINE = 0.6
W_JACCARD = 0.4

# ── 品类词表(小写子串;宁缺毋滥,识别不了就不贴)──────────────────────
CATEGORY_TERMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("prime", "定焦", ("定焦", "prime lens", "prime ", " primes", "大光圈定焦")),
    ("zoom", "变焦", ("变焦", "zoom")),
    ("anamorphic", "变形宽银幕", ("anamorphic", "变形镜头", "宽银幕")),
    ("telephoto", "长焦", ("长焦", "telephoto", "远摄")),
    ("wide_angle", "广角", ("广角", "超广", "wide angle", "wide-angle", "ultra wide")),
    ("macro", "微距", ("微距", "macro")),
    ("portrait", "人像", ("人像", "portrait")),
    ("street", "街拍", ("街拍", "扫街", "street")),
    ("vlog", "Vlog", ("vlog",)),
    ("low_light", "夜拍/弱光", ("夜拍", "弱光", "夜景", "low light", "low-light", "night shoot")),
    ("autofocus", "对焦性能", ("对焦", "autofocus", "eye af", "af performance", "af 性能")),
    ("video_filmmaking", "视频创作", ("filmmaking", "cinematic", "电影感", "短片", "b-roll", "broll", "b roll")),
    ("comparison", "对比横评", ("对比", "横评", "comparison", " vs ", "versus")),
)

# 焦段正则(8–1200mm 之间才认;SQL 里不写 %,正则只在 Python 侧跑)
FOCAL_RE = re.compile(r"(\d{2,4})\s*mm", re.IGNORECASE)
FOCAL_MIN, FOCAL_MAX = 8, 1200


# ── 小工具(容错解析;与 signature_profile 同风格)──────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict 也可能回 str,双态容错。"""
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


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


# ── 向量与标签抽取(全部决定性)──────────────────────────────────────


def _numeric_vector(dims: dict[str, Any]) -> dict[str, float]:
    """llm_dimensions_11 → 数值向量 {维名: 0-100 分}。只收真数字,缺维不补零。"""
    vector: dict[str, float] = {}
    scores = _as_dict(dims.get("scores"))
    for key in SCORE_KEYS:
        entry = scores.get(key)
        raw = entry.get("score") if isinstance(entry, dict) else entry
        value = _float_or_none(raw)
        if value is not None:
            vector[key] = value
    fit = _as_dict(dims.get(FIT_KEY))
    fit_score = _float_or_none(fit.get("score"))
    if fit_score is not None:
        vector[FIT_KEY] = fit_score
    return vector


def _cosine(vec_a: dict[str, float], vec_b: dict[str, float]) -> float | None:
    """共有维上的余弦;共有维不足或任一侧零向量时诚实 None,不硬给分。"""
    shared = [k for k in vec_a if k in vec_b]
    if len(shared) < MIN_SHARED_DIMS:
        return None
    dot = sum(vec_a[k] * vec_b[k] for k in shared)
    norm_a = math.sqrt(sum(vec_a[k] * vec_a[k] for k in shared))
    norm_b = math.sqrt(sum(vec_b[k] * vec_b[k] for k in shared))
    if norm_a <= 0 or norm_b <= 0:
        return None
    value = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, value))  # 全维非负,理论已在 [0,1],夹一道保险


def _content_blob(dims: dict[str, Any], title: str) -> str:
    """可打标签的文本底料:标题 + layer1_summary 各文本字段 + 分镜 what(全小写)。"""
    layer1 = _as_dict(dims.get("layer1_summary"))
    parts: list[str] = [_text(title, 200)]
    for key in ("content_summary", "production_observations", "product_presence",
                "competitor_presence", "brand_exposure"):
        parts.append(_text(layer1.get(key), 600))
    for scene in _as_list(layer1.get("scene_timeline"))[:12]:
        if isinstance(scene, dict):
            parts.append(_text(scene.get("what"), 150))
    return " ".join(p for p in parts if p).lower()


def _extract_tags(dims: dict[str, Any], title: str, platform: str) -> tuple[set[str], dict[str, str]]:
    """词表标签集合 + {tag: 中文label}。拍法复用 signature_profile 词表,零重复维护。"""
    from app.domains.kol.signature_profile import SHOOTING_MODES

    blob = _content_blob(dims, title)
    tags: set[str] = set()
    labels: dict[str, str] = {}
    if not blob:
        return tags, labels

    for key, label, terms in SHOOTING_MODES:
        if any(term in blob for term in terms):
            tag = "mode:" + key
            tags.add(tag)
            labels[tag] = "拍法·" + label
    for key, label, terms in CATEGORY_TERMS:
        if any(term in blob for term in terms):
            tag = "cat:" + key
            tags.add(tag)
            labels[tag] = "品类·" + label
    for match in FOCAL_RE.finditer(blob):
        focal = _int_or_none(match.group(1))
        if focal is not None and FOCAL_MIN <= focal <= FOCAL_MAX:
            tag = f"focal:{focal}mm"
            tags.add(tag)
            labels[tag] = f"焦段·{focal}mm"
    plat = _text(platform, 30).lower()
    if plat:
        tag = "platform:" + plat
        tags.add(tag)
        labels[tag] = "平台·" + plat
    return tags, labels


def _jaccard(tags_a: set[str], tags_b: set[str]) -> float | None:
    """标签 Jaccard;双方标签并集为空时诚实 None(没得比,不装 0 也不装 1)。"""
    union = tags_a | tags_b
    if not union:
        return None
    return len(tags_a & tags_b) / len(union)


def _blend(cosine: float | None, jaccard: float | None) -> tuple[float | None, str]:
    """混合打分;哪半缺失如实退化并标注 basis。都缺 → None(该候选不可比)。"""
    if cosine is not None and jaccard is not None:
        return W_COSINE * cosine + W_JACCARD * jaccard, "cosine+jaccard"
    if cosine is not None:
        return cosine, "cosine_only"
    if jaccard is not None:
        return jaccard, "jaccard_only"
    return None, "incomparable"


# ── 数据装载(全只读)────────────────────────────────────────────────


def _load_evidence_row(conn: Any, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, kol_pool_id, COALESCE(video_title, title, '') AS title,
               content_url, platform, view_count, is_active
        FROM vkpi_kol_video_evidence
        WHERE id = ?
        """,
        (int(evidence_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_corpus(conn: Any) -> list[dict[str, Any]]:
    """全库已深析视频 + evidence 表现 + 所属 KOL(≈500 行级,读端一把梭)。"""
    rows = conn.execute(
        """
        SELECT
          d.id AS deep_id,
          d.source_evidence_id AS evidence_id,
          d.kol_pool_id,
          d.llm_dimensions_11 AS dims,
          COALESCE(e.video_title, e.title, '') AS title,
          e.content_url,
          e.platform,
          e.view_count,
          e.posted_at,
          p.handle,
          p.display_name
        FROM vkpi_kol_llm_deep_analysis_results d
        LEFT JOIN vkpi_kol_video_evidence e ON e.id = d.source_evidence_id
        LEFT JOIN vkpi_kol_pool p ON p.id = d.kol_pool_id
        WHERE d.analysis_kind = 'video_final_v1'
          AND d.status = 'ready'
          AND d.source_evidence_id IS NOT NULL
          AND d.llm_dimensions_11 IS NOT NULL
        ORDER BY d.id DESC
        """,
    ).fetchall()
    # 同 evidence 若有多条深析,保最新(deep_id 最大;上面已按 id DESC,先见先赢)
    seen: set[int] = set()
    corpus: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        corpus.append(row)
    return corpus


def _prepare(row: dict[str, Any]) -> dict[str, Any] | None:
    """一行语料 → 可比对象(向量+标签);dims 解析不出就丢弃(不硬凑)。"""
    dims = _as_dict(_loads(row.get("dims")))
    if not dims:
        return None
    source = _as_dict(dims.get("source"))
    title = _text(row.get("title"), 200) or _text(source.get("title"), 200)
    url = _text(row.get("content_url"), 500) or _text(source.get("source_url"), 500)
    platform = _text(row.get("platform"), 30) or _text(source.get("platform"), 30)
    tags, labels = _extract_tags(dims, title, platform)
    return {
        "evidence_id": _int_or_none(row.get("evidence_id")),
        "kol_pool_id": _int_or_none(row.get("kol_pool_id")),
        "handle": _text(row.get("handle"), 100),
        "display_name": _text(row.get("display_name"), 100),
        "title": title,
        "url": url,
        "platform": platform,
        "view_count": _int_or_none(row.get("view_count")),
        "posted_at": _iso(row.get("posted_at")),
        "vector": _numeric_vector(dims),
        "tags": tags,
        "tag_labels": labels,
    }


# ── 主入口 ──────────────────────────────────────────────────────────


def similar_videos(
    evidence_id: int,
    *,
    limit: int = 10,
    exclude_same_kol: bool = True,
    conn: Any = None,
) -> dict[str, Any]:
    """以视频找相似:evidence 不存在抛 LookupError(路由转 404);没深析/没语料诚实 empty。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    limit = max(1, min(int(limit or 10), 50))
    exclude_same_kol = _truthy(exclude_same_kol)

    evidence = _load_evidence_row(db, int(evidence_id))
    if not evidence:
        raise LookupError(f"video evidence {evidence_id} not found")

    corpus_rows = _load_corpus(db)
    prepared = [p for p in (_prepare(r) for r in corpus_rows) if p is not None]
    seed = next((p for p in prepared if p["evidence_id"] == int(evidence_id)), None)

    base = {
        "evidence_id": int(evidence_id),
        "method": METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if seed is None:
        return {
            **base,
            "status": "empty",
            "reason": "该视频尚未深析(无 ready 的 final_v1 十一维结果),没有可比向量 — 先对它跑「视频深度分析」再来找相似。",
            "seed": {
                "evidence_id": int(evidence_id),
                "kol_pool_id": _int_or_none(evidence.get("kol_pool_id")),
                "title": _text(evidence.get("title"), 200),
            },
        }

    seed_view = {
        "evidence_id": seed["evidence_id"],
        "kol_pool_id": seed["kol_pool_id"],
        "kol_handle": seed["handle"],
        "kol_display_name": seed["display_name"],
        "title": seed["title"],
        "url": seed["url"],
        "platform": seed["platform"],
        "view_count": seed["view_count"],
        "vector_dims": len(seed["vector"]),
        "tags": sorted(seed["tag_labels"].values()),
    }

    excluded_same_kol = 0
    scored: list[dict[str, Any]] = []
    for cand in prepared:
        if cand["evidence_id"] == seed["evidence_id"]:
            continue
        if exclude_same_kol and cand["kol_pool_id"] is not None and cand["kol_pool_id"] == seed["kol_pool_id"]:
            excluded_same_kol += 1
            continue
        cosine = _cosine(seed["vector"], cand["vector"])
        jaccard = _jaccard(seed["tags"], cand["tags"])
        similarity, basis = _blend(cosine, jaccard)
        if similarity is None:
            continue
        shared = seed["tags"] & cand["tags"]
        shared_labels = sorted(
            cand["tag_labels"].get(t) or seed["tag_labels"].get(t) or t for t in shared
        )
        scored.append(
            {
                "evidence_id": cand["evidence_id"],
                "title": cand["title"],
                "url": cand["url"],
                "platform": cand["platform"],
                "view_count": cand["view_count"],
                "posted_at": cand["posted_at"],
                "kol": {
                    "id": cand["kol_pool_id"],
                    "handle": cand["handle"],
                    "display_name": cand["display_name"],
                },
                "similarity": round(similarity, 4),
                "score_parts": {
                    "cosine": round(cosine, 4) if cosine is not None else None,
                    "tag_jaccard": round(jaccard, 4) if jaccard is not None else None,
                    "basis": basis,
                },
                "shared_tags": shared_labels,  # 相似原因=真实共同标签;为空就是空,不编
            }
        )

    if not scored:
        return {
            **base,
            "status": "empty",
            "reason": (
                f"全库已深析视频共 {len(prepared)} 条,排除本片"
                + (f"与同 KOL 自己的 {excluded_same_kol} 条" if excluded_same_kol else "")
                + "后没有可比候选 — 深析语料还太少。"
            ),
            "seed": seed_view,
            "corpus_size": len(prepared),
        }

    scored.sort(key=lambda s: (s["similarity"], s["view_count"] or 0), reverse=True)
    return {
        **base,
        "status": "ready",
        "seed": seed_view,
        "corpus_size": len(prepared),
        "excluded_same_kol": excluded_same_kol,
        "exclude_same_kol": exclude_same_kol,
        "items": scored[:limit],
        "weights": {"cosine": W_COSINE, "tag_jaccard": W_JACCARD},
        "note": (
            "决定性降级检索(无视频级向量库):final_v1 数值维余弦 + 词表标签 Jaccard 混合;"
            "零 LLM、可复算;独立展示信号,不参与 V6 Fit 评分。"
        ),
    }
