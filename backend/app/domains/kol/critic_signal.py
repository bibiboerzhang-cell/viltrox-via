"""「敢给差评」信号(critic_signal v1)—— 该 KOL 历史上对品牌/产品公开给过批评意见
= 可信度加分信号(黑名单文化的反面:我们主动找敢说真话的人)。

数据源(全只读,同 FTC 扫描 quality_compliance 口径):
  ① 创作者文本:vkpi_kol_video_evidence 标题 + vkpi_kol_pool.raw_platform_data.videos
     的 YouTube snippet 描述 —— 创作者自己写的「真话型」标题/描述
     (honest review / the truth about / don't buy / 缺点 / 翻车 / 劝退 …);
  ② 深析文本:vkpi_kol_llm_deep_analysis_results(analysis_kind='video_final_v1',
     status='ready')llm_dimensions_11 的 layer1_summary/risk/recommendations ——
     只认「创作者发声批评」句式(「KOL 明确指出了对焦偶尔迟钝的问题」「客观指出了
     产品的局限性」「honest review」),分析师口吻批评视频表现(播放低/曝光不足)不算。

词表法(零 LLM):英文词按词边界/短语正则、中文保持子串;先抹「无缺点/no issues」类
否定再匹配,宁缺毋滥。输出 has_critic_history / example / basis(+ examples 明细)。

compat 约定:SQL 占位符用 ?;SQL 字符串零 percent 字面(不用 LIKE);BOOLEAN 读回
可能是 int 1/0 走 _truthy;JSONB 双态(dict/str)走 _loads;函数内懒 import get_conn。

红线:纯读聚合,绝不写库;这是独立展示/外联上下文信号,不进任何评分 ——
零触 viltrox_fit_score、不碰 rule_v0;检不出证据诚实 has_critic_history=False,绝不杜撰。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

DEEP_TABLE_KIND = "video_final_v1"
METHOD = "critic_lexicon_v1"

# ── ① 创作者文本「真话型」词表(标题/描述;英文短语正则,中文子串)────────────
CREATOR_CRITIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("honest_review", re.compile(r"\b(?:brutally\s+)?honest\s+(?:review|opinion|thoughts?|take)\b")),
    ("truth_about", re.compile(r"\bthe\s+truth\s+about\b")),
    ("dont_buy", re.compile(r"\b(?:don'?t|do\s+not|stop)\s+buy(?:ing)?\b|\bbefore\s+you\s+buy\b")),
    ("worst", re.compile(r"\bworst\b")),
    ("problems_with", re.compile(r"\b(?:the\s+)?problems?\s+with\b|\bissues?\s+with\b")),
    ("overrated", re.compile(r"\boverrated\b|\bnot\s+worth\b|\bwaste\s+of\s+money\b")),
    # regret 只认购买语境(never regret traveling… 这类生活语境不算批评)。
    ("disappointed", re.compile(r"\bdisappoint(?:ed|ing|ment)?\b|\bregret(?:s|ted|ting)?\s+(?:buying|getting|purchasing|this)\b")),
    ("pros_and_cons", re.compile(r"\bpros\s+and\s+cons\b|\bcons\b")),
    ("weakness", re.compile(r"\bdownsides?\b|\bdrawbacks?\b|\bweakness(?:es)?\b|\bflaws?\b")),
)
CREATOR_CRITIC_TERMS_CN: tuple[str, ...] = (
    "翻车", "劝退", "避雷", "缺点", "吐槽", "不推荐", "别买", "慎买",
    "智商税", "说真话", "大实话", "真实体验", "不吹不黑", "翻旧账",
)

# ── ② 深析文本「创作者发声批评」句式(分析师转述创作者的批评行为)──────────────
DEEP_CRITIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "cn_creator_voiced",
        re.compile(
            r"(?:指出|承认|坦言|坦诚|直言|点出|点名|吐槽|批评)[^,。;:]{0,20}"
            r"(?:缺点|不足|短板|问题|缺陷|局限|弱点|翻车|bug)",
            re.IGNORECASE,
        ),
    ),
    ("cn_honest_flaw_analysis", re.compile(r"(?:坦诚|透明|客观|诚实)[^,。;]{0,12}(?:缺陷|缺点|短板|局限)")),
    ("en_honest_review", re.compile(r"\bhonest(?:ly)?\s+(?:review|assessment|opinion|critique)\b", re.IGNORECASE)),
    ("en_criticize", re.compile(r"\bcriticiz\w+\b|\bcritical\s+of\b", re.IGNORECASE)),
    (
        "en_points_out",
        re.compile(r"\b(?:points?|pointed)\s+out\s+(?:the\s+)?(?:flaws?|drawbacks?|weakness(?:es)?|shortcomings?|downsides?|issues?|problems?)\b", re.IGNORECASE),
    ),
    (
        "en_acknowledges",
        re.compile(r"\backnowledg\w+\s+(?:the\s+)?(?:limitations?|weakness(?:es)?|flaws?|downsides?|drawbacks?)\b", re.IGNORECASE),
    ),
)

# ── 否定抹除(「无缺点 / no issues」不是批评证据)────────────────────────────
_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:no|without|zero|hardly\s+any|free\s+of)\s+(?:real\s+|major\s+|significant\s+)?(?:problems?|issues?|cons|complaints?|regrets?|flaws?|drawbacks?|downsides?|weakness(?:es)?)\b"),
    re.compile(r"\b(?:never|won'?t|don'?t|didn'?t)\s+regret\w*\b"),
    re.compile(r"(?:没有|没啥|无|几乎没有|挑不出|找不到)\s*(?:明显|什么|太大|任何)?\s*(?:缺点|短板|缺陷|毛病|槽点|问题)"),
)

_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{6,20})")

SOURCE_LABELS = {
    "creator_title": "创作者标题",
    "creator_description": "创作者描述",
    "deep_analysis": "深析转述",
}


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


def _strip_negations(blob: str) -> str:
    """否定片段抹成等长空格(保长度 → 命中偏移可对回原文取摘录)。"""
    for pattern in _NEGATION_PATTERNS:
        blob = pattern.sub(lambda m: " " * len(m.group(0)), blob)
    return blob


def _snippet(blob: str, start: int, end: int, radius: int = 70) -> str:
    """命中处上下文摘录(±radius 字符,词表证据给人看的原文引用)。"""
    lo = max(0, start - radius)
    hi = min(len(blob), end + radius)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(blob) else ""
    return prefix + blob[lo:hi].strip() + suffix


# ── 数据装载(全只读;? 占位;零 percent 字面)───────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, handle, display_name, platform, raw_platform_data FROM vkpi_kol_pool WHERE id = ?",
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
          is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


def _load_deep_rows(conn: Any, kol_pool_id: int, limit: int = 300) -> list[dict[str, Any]]:
    """该 KOL 全部 ready 的 final_v1 深析(+ evidence 展示字段);同 evidence 保留最新。
    刻意不 SELECT llm_v6_fit:本域连评分列都不读。"""
    rows = conn.execute(
        """
        SELECT
          d.id AS deep_id,
          d.source_evidence_id AS evidence_id,
          d.llm_dimensions_11 AS dims,
          d.created_at AS created_at,
          COALESCE(e.video_title, e.title, '') AS title,
          e.content_url,
          e.view_count
        FROM vkpi_kol_llm_deep_analysis_results d
        LEFT JOIN vkpi_kol_video_evidence e ON e.id = d.source_evidence_id
        WHERE d.kol_pool_id = ?
          AND d.analysis_kind = ?
          AND d.status = 'ready'
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), DEEP_TABLE_KIND, int(limit)),
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


def _description_map(pool_row: dict[str, Any]) -> dict[str, dict[str, str]]:
    """raw_platform_data.videos → {video_id: {title, description}}(同 quality_compliance 口径)。"""
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
            match = _YT_ID_RE.search(url)
            vid = match.group(1) if match else ""
        if vid and (title or description):
            out[vid] = {"title": title, "description": description}
    return out


def _deep_text_blob(dims: dict[str, Any]) -> str:
    """深析文本底料(保留原大小写供摘录;匹配时各 pattern 自带 IGNORECASE 或小写化)。"""
    layer1 = _as_dict(dims.get("layer1_summary"))
    risk = _as_dict(dims.get("risk"))
    reco = _as_dict(dims.get("recommendations"))
    flags = risk.get("risk_flags")
    if isinstance(flags, list):
        flags = " ".join(_text(f, 300) for f in flags)
    parts = [
        _text(layer1.get("content_summary"), 1600),
        _text(layer1.get("product_presence"), 800),
        _text(layer1.get("brand_exposure"), 600),
        _text(flags, 800),
        _text(risk.get("final_verdict"), 800),
        _text(reco.get("why"), 800),
    ]
    return " ".join(p for p in parts if p)


# ── 扫描(词表;否定感知)────────────────────────────────────────────────────


def _scan_creator_blob(blob: str) -> list[dict[str, str]]:
    """创作者文本 → 命中列表 [{key, quote}](小写副本匹配,偏移对回原文取摘录)。"""
    if not blob.strip():
        return []
    lowered = _strip_negations(blob.lower())
    # lower() 极少数 Unicode 会变长;错位时摘录退回小写副本,保证引用与命中一致。
    source = blob if len(lowered) == len(blob) else lowered
    hits: list[dict[str, str]] = []
    for key, pattern in CREATOR_CRITIC_PATTERNS:
        match = pattern.search(lowered)
        if match:
            hits.append({"key": key, "quote": _snippet(source, match.start(), match.end())})
    for term in CREATOR_CRITIC_TERMS_CN:
        idx = lowered.find(term)
        if idx >= 0:
            hits.append({"key": "cn_" + term, "quote": _snippet(source, idx, idx + len(term))})
    return hits


def _scan_deep_blob(blob: str) -> list[dict[str, str]]:
    """深析文本 → 「创作者发声批评」命中列表(分析师批评视频表现的措辞不在词表内)。"""
    if not blob.strip():
        return []
    cleaned = _strip_negations(blob)
    hits: list[dict[str, str]] = []
    for key, pattern in DEEP_CRITIC_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            hits.append({"key": key, "quote": _snippet(cleaned, match.start(), match.end())})
    return hits


# ── 主入口 ──────────────────────────────────────────────────────────────────


def critic_signal(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """「敢给差评」信号聚合;KOL 不存在抛 LookupError(路由转 404)。

    输出契约:has_critic_history(bool)/ example(最佳证据一条)/ basis(人话依据)
    恒在;examples 明细最多 5 条;检不出诚实 False,绝不杜撰。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    deep_rows = _load_deep_rows(db, int(kol_pool_id))
    desc_map = _description_map(pool)

    examples: list[dict[str, Any]] = []
    counts = {"deep_analysis": 0, "creator_title": 0, "creator_description": 0}
    with_description = 0

    # ② 深析转述(最有说服力:第三方深析确认创作者点过产品缺点)
    for row in deep_rows:
        dims = _as_dict(_loads(row.get("dims")))
        if not dims:
            continue
        hits = _scan_deep_blob(_deep_text_blob(dims))
        if not hits:
            continue
        counts["deep_analysis"] += 1
        examples.append(
            {
                "source": "deep_analysis",
                "evidence_id": _int_or_none(row.get("evidence_id")),
                "title": _text(row.get("title"), 140),
                "content_url": _text(row.get("content_url"), 500),
                "view_count": _int_or_none(row.get("view_count")),
                "quote": hits[0]["quote"],
                "matched": [h["key"] for h in hits[:3]],
            }
        )

    # ① 创作者标题/描述(自己敢写「真话型」标题 = 直接证据)
    for row in evidence_rows:
        title = _text(row.get("title"), 300)
        url = _text(row.get("content_url"), 500)
        match = _YT_ID_RE.search(url)
        desc_entry = desc_map.get(match.group(1) if match else "") or {}
        description = desc_entry.get("description") or ""
        if description:
            with_description += 1

        title_hits = _scan_creator_blob(title)
        if title_hits:
            counts["creator_title"] += 1
            examples.append(
                {
                    "source": "creator_title",
                    "evidence_id": _int_or_none(row.get("evidence_id")),
                    "title": title,
                    "content_url": url,
                    "view_count": _int_or_none(row.get("view_count")),
                    "quote": title_hits[0]["quote"],
                    "matched": [h["key"] for h in title_hits[:3]],
                }
            )
            continue  # 同一视频标题已命中就不再重复计描述
        desc_hits = _scan_creator_blob(description)
        if desc_hits:
            counts["creator_description"] += 1
            examples.append(
                {
                    "source": "creator_description",
                    "evidence_id": _int_or_none(row.get("evidence_id")),
                    "title": title,
                    "content_url": url,
                    "view_count": _int_or_none(row.get("view_count")),
                    "quote": desc_hits[0]["quote"],
                    "matched": [h["key"] for h in desc_hits[:3]],
                }
            )

    # 排序:深析转述优先(第三方确认最硬),再创作者标题,再描述;同类按播放降序。
    source_rank = {"deep_analysis": 0, "creator_title": 1, "creator_description": 2}
    examples.sort(key=lambda x: (source_rank.get(str(x.get("source")), 9), -(x.get("view_count") or 0)))

    has_history = bool(examples)
    basis_parts = []
    if counts["deep_analysis"]:
        basis_parts.append(f"深析转述 {counts['deep_analysis']} 条(第三方深析确认其公开点过产品缺点)")
    if counts["creator_title"]:
        basis_parts.append(f"真话型标题 {counts['creator_title']} 条")
    if counts["creator_description"]:
        basis_parts.append(f"描述文本 {counts['creator_description']} 条")
    basis = (
        "、".join(basis_parts) + f"(词表法 {METHOD},零 LLM)"
        if basis_parts
        else "已扫标题/描述/深析文本,未检出历史批评证据(词表法启发式,不代表其没有;描述覆盖有限)。"
    )

    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "has_critic_history": has_history,
        "example": examples[0] if examples else None,
        "basis": basis,
        "examples": examples[:5],
        "counts": counts,
        "coverage": {
            "evidence_count": len(evidence_rows),
            "deep_analyzed_count": len(deep_rows),
            "with_description": with_description,
        },
        "method": METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "「敢给差评」= 可信度加分信号(黑名单文化反面):词表法扫创作者标题/描述 + "
            "深析「创作者发声批评」句式;独立展示/外联上下文用,不进任何评分,不写库。"
        ),
    }
