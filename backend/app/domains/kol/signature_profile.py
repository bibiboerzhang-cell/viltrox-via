"""KOL 招牌面板(signature_profile v1)—— 一个 KOL「他最擅长什么」的三块聚合读数。

三块(全部纯聚合已有数据,零新采集、零 LLM):
  🎬 shooting_styles  擅长拍法:聚合全部 ready 的 final_v1 深析(layer1 画面/开头/brand_exposure)
                      + evidence 播放数据 → TOP 拍摄模式(词表规则分类)+ 各模式均播放;
  🏆 top_videos       最爆代表作 TOP3:evidence 按 view_count 排序 + final_v1 一句话点评(layer5/6 有则带);
  💬 feedback         最引反馈 TOP3:evidence 按评论数/互动率排序 + 该视频已入库评论的意向/情感分布
                      (vkpi_comments 词表聚合,零 LLM)。

诚实态:每块缺数据返回 {status:"empty", reason:...},绝不杜撰;拍摄模式是
词表规则分类(method=lexicon_v1;英文词按词边界正则、中文保持子串,camera 不再
误命中 camcorder),识别不了的落 unclassified,不硬贴标签。样本口径:多标签 ——
一片可同时属多个拍摄模式,各模式 video_count 之和可大于 sample_size(非互斥分类)。

数据源(全只读):vkpi_kol_pool / vkpi_kol_video_evidence / vkpi_analysis_cache
(derive_method='video_analysis_final_v1', status='ready') / vkpi_comments(evidence 桥)。

compat 约定:SQL 占位符用 ?;SQL 禁 percent 字符(不用 LIKE);BOOLEAN 读回可能是
int 1/0,判断走 _truthy。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"

# ── 拍摄模式词表(英文词按词边界正则、中文保持子串;中英双语;宁缺毋滥,识别不了归
#    unclassified)。样本口径:多标签 —— 一片可同时属多个拍摄模式(教程里穿插 B-roll
#    就两个模式都记),各模式 video_count 之和可以大于深析样本数,不是互斥分类。──
SHOOTING_MODES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("cinematic_broll", "电影感B-roll", (
        "b-roll", "b roll", "broll", "cinematic", "电影感", "电影级", "电影质感",
        "空镜", "光影", "氛围感", "运镜",
    )),
    ("talking_review", "口播评测", (
        "口播", "评测", "测评", "review", "talking head", "对比测试", "横评", "对比评",
    )),
    ("pov", "POV第一视角", (
        "pov", "第一视角", "第一人称", "沉浸式",
    )),
    ("tutorial", "教程演示", (
        "教程", "tutorial", "教学", "how to", "技巧", "参数设置", "步骤", "演示如何",
    )),
    ("street", "街拍实测", (
        "街拍", "扫街", "街头", "street",
    )),
    ("unboxing", "开箱上手", (
        "开箱", "unboxing", "上手", "first look", "初体验",
    )),
    ("sample_showcase", "样片展示", (
        "样片", "sample footage", "test footage", "画质展示", "实拍展示",
    )),
)

# ── 开头类型词表(取 scene_timeline[0] + layer2 前3秒感受文本)──
OPENING_TYPES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("product_closeup", "产品特写开场", ("特写", "近景", "产品亮相", "close-up", "closeup")),
    ("talking_intro", "口播开场", ("口播", "自我介绍", "开场白", "介绍", "讲解")),
    ("scene_atmosphere", "场景氛围开场", ("空镜", "风景", "街头", "夜景", "场景", "氛围", "城市")),
    ("hook_question", "悬念/提问开场", ("悬念", "提问", "疑问", "反差", "问题")),
    ("result_first", "成片先行开场", ("成片", "样片", "效果展示", "画质", "成果")),
)

# ── 评论情感词表(lexicon_v0;小写子串;够用即可,不装 NLP)──
POSITIVE_TERMS: tuple[str, ...] = (
    "great", "love", "nice", "amazing", "awesome", "beautiful", "perfect", "best",
    "impressive", "stunning", "sharp", "excellent", "incredible", "fantastic", "wow",
    "好看", "太棒", "喜欢", "牛", "绝了", "漂亮", "真香", "期待", "锐利", "厉害", "支持",
)
NEGATIVE_TERMS: tuple[str, ...] = (
    "bad", "worse", "worst", "terrible", "awful", "disappoint", "boring", "hate",
    "blurry", "overpriced", "noisy", "soft corners",
    "翻车", "失望", "难用", "不行", "垃圾", "劝退", "太贵", "拉胯", "糊",
)
QUESTION_TERMS: tuple[str, ...] = (
    "?", "？", "how ", "what ", "why ", "which ", "哪", "吗", "怎么", "什么", "多少", "求解",
)


# ── 小工具(容错序列化/解析;全模块共用)─────────────────────────────


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


def _final_payload(result: Any) -> dict[str, Any]:
    """result 可能平铺六层,也可能嵌在 'video_analysis_final_v1' 键下,双态解包。"""
    root = _as_dict(_loads(result))
    nested = _as_dict(root.get(FINAL_V1_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")) or _as_dict(nested.get("layer6_flags_and_scores")):
        return nested
    return root


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _avg(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _engagement_rate(views: int | None, likes: int | None, comments: int | None) -> float | None:
    """(赞+评)/播放;播放缺失或为 0 时诚实返回 None,不除零不外推。"""
    if not views or views <= 0:
        return None
    inter = (likes or 0) + (comments or 0)
    return round(inter / views, 5)


# 词表命中缓存:英文词 → 词边界正则;中文/无字母数字词 → None(走子串)。
# None 是合法缓存值,用 False 作缺席哨兵。
_TERM_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _term_pattern(term: str) -> re.Pattern[str] | None:
    """英文词表词 → 词边界正则(camera 不再命中 camcorder、review 不再命中 previews)。

    边界用 [a-z0-9] 而非 \\w:blob 里中英混排时,\\b 会把紧邻的汉字当词字符
    导致 "review中文" 匹配不上;与 competitor_text._keyword_match 同款口径。
    词首/词尾本身不是字母数字的(如 "b-roll" 的尾、"how " 的尾空格)只在
    字母数字一侧加边界。中文/符号词(不含 ASCII 字母数字)回 None,保持子串匹配。
    """
    cached = _TERM_PATTERN_CACHE.get(term, False)
    if cached is not False:
        return cached
    pattern: re.Pattern[str] | None = None
    if term and term.isascii() and any(ch.isalnum() for ch in term):
        prefix = r"(?<![a-z0-9])" if term[0].isalnum() else ""
        suffix = r"(?![a-z0-9])" if term[-1].isalnum() else ""
        pattern = re.compile(prefix + re.escape(term) + suffix)
    _TERM_PATTERN_CACHE[term] = pattern
    return pattern


def _match_terms(blob: str, terms: tuple[str, ...]) -> bool:
    """词表命中:英文词走词边界正则,中文词保持子串(中文无空格分词,子串即口径)。"""
    for term in terms:
        pattern = _term_pattern(term)
        if pattern is not None:
            if pattern.search(blob):
                return True
        elif term in blob:
            return True
    return False


# ── 数据装载(全只读)────────────────────────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, handle, display_name, platform, avatar_url FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_final_v1_rows(conn: Any, kol_pool_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """该 KOL 全部 ready 的 final_v1 深析 + 对应 evidence 表现数据。"""
    rows = conn.execute(
        """
        SELECT
          e.id AS evidence_id,
          COALESCE(e.video_title, e.title, '') AS title,
          e.content_url,
          e.platform,
          e.view_count,
          e.like_count,
          e.comment_count,
          e.posted_at,
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


# ── 🎬 块一:擅长拍法 ────────────────────────────────────────────────


def _video_style_blob(payload: dict[str, Any]) -> str:
    """一条深析的可分类文本底料:layer1 摘要/分镜/制作观察 + layer2 前3秒(全小写)。"""
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer2 = _as_dict(payload.get("layer2_viewer_emotion"))
    parts: list[str] = [_text(layer1.get("content_summary"), 800)]
    prod_obs = layer1.get("production_observations")
    if isinstance(prod_obs, dict):
        parts.extend(_text(prod_obs.get(k), 200) for k in ("composition", "lighting", "color_grade", "notes"))
    else:
        parts.append(_text(prod_obs, 400))
    for scene in _as_list(layer1.get("scene_timeline"))[:12]:
        if isinstance(scene, dict):
            parts.append(_text(scene.get("what"), 120))
    parts.append(_text(layer2.get("first_three_seconds_feeling"), 200))
    return " ".join(p for p in parts if p).lower()


def _opening_blob(payload: dict[str, Any]) -> str:
    """开头类型底料:只看 scene_timeline 首镜 + layer2 前3秒感受,不拿全片文本凑。"""
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer2 = _as_dict(payload.get("layer2_viewer_emotion"))
    parts: list[str] = []
    scenes = _as_list(layer1.get("scene_timeline"))
    if scenes and isinstance(scenes[0], dict):
        parts.append(_text(scenes[0].get("what"), 200))
        parts.append(_text(scenes[0].get("why_it_matters"), 200))
    parts.append(_text(layer2.get("first_three_seconds_feeling"), 200))
    return " ".join(p for p in parts if p).lower()


def _brand_exposure_signal(payload: dict[str, Any]) -> str:
    """brand_exposure 双态(dict 带 sufficient / 纯文本描述)→ 'sufficient'/'insufficient'/'text_only'/''。"""
    exposure = _as_dict(payload.get("layer1_visual_content")).get("brand_exposure")
    if isinstance(exposure, dict):
        if "sufficient" in exposure:
            return "sufficient" if _truthy(exposure.get("sufficient")) else "insufficient"
        return "text_only" if exposure else ""
    return "text_only" if _text(exposure) else ""


def _shooting_styles_block(final_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not final_rows:
        return {
            "status": "empty",
            "reason": "该 KOL 还没有已深析(final_v1)视频,拍法画像无从聚合 — 先跑「KOL深度分析理解」。",
        }

    mode_hits: dict[str, list[dict[str, Any]]] = {}
    opening_counts: dict[str, int] = {}
    level_counts: dict[str, int] = {}
    exposure_counts = {"sufficient": 0, "insufficient": 0, "text_only": 0}
    unclassified = 0

    for row in final_rows:
        payload = _final_payload(row.get("result"))
        if not payload:
            continue
        views = _int_or_none(row.get("view_count"))
        video_ref = {
            "evidence_id": _int_or_none(row.get("evidence_id")),
            "title": _text(row.get("title"), 120),
            "view_count": views,
        }

        blob = _video_style_blob(payload)
        matched = False
        for key, _label, terms in SHOOTING_MODES:
            if blob and _match_terms(blob, terms):
                mode_hits.setdefault(key, []).append(video_ref)
                matched = True
        if not matched:
            unclassified += 1

        opening_blob = _opening_blob(payload)
        if opening_blob:
            for key, _label, terms in OPENING_TYPES:
                if _match_terms(opening_blob, terms):
                    opening_counts[key] = opening_counts.get(key, 0) + 1
                    break

        prod_obs = _as_dict(payload.get("layer1_visual_content")).get("production_observations")
        level = _text(_as_dict(prod_obs).get("professional_level"), 40).lower()
        if level:
            level_counts[level] = level_counts.get(level, 0) + 1

        signal = _brand_exposure_signal(payload)
        if signal in exposure_counts:
            exposure_counts[signal] += 1

    label_by_key = {key: label for key, label, _ in SHOOTING_MODES}
    modes: list[dict[str, Any]] = []
    for key, refs in mode_hits.items():
        views_list = [r["view_count"] for r in refs if isinstance(r.get("view_count"), int)]
        best = max(refs, key=lambda r: r.get("view_count") or 0)
        modes.append(
            {
                "key": key,
                "label": label_by_key.get(key, key),
                "video_count": len(refs),
                "avg_views": _avg(views_list),  # 只均有播放数的;全缺则诚实 None
                "views_sample": len(views_list),
                "top_example": {"title": best.get("title"), "view_count": best.get("view_count")},
            }
        )
    modes.sort(key=lambda m: (m["video_count"], m["avg_views"] or 0), reverse=True)

    opening_label = {key: label for key, label, _ in OPENING_TYPES}
    openings = sorted(
        ({"key": k, "label": opening_label.get(k, k), "count": v} for k, v in opening_counts.items()),
        key=lambda o: o["count"],
        reverse=True,
    )

    return {
        "status": "ready",
        "sample_size": len(final_rows),
        "method": "final_v1_layer1_lexicon_v1",
        "note": (
            "拍法/开头为词表规则分类(零 LLM;英文词按词边界、中文按子串);"
            "多标签口径:一片可同时属多个拍摄模式,各模式 video_count 之和可大于 sample_size;"
            "均播放只计有播放数的深析视频。"
        ),
        "modes": modes[:6],
        "unclassified_count": unclassified,
        "openings": openings[:5],
        "professional_level": dict(sorted(level_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "brand_exposure": {
            "sufficient": exposure_counts["sufficient"],
            "insufficient": exposure_counts["insufficient"],
            "text_only": exposure_counts["text_only"],
        },
    }


# ── 🏆 块二:最爆代表作 TOP3 ─────────────────────────────────────────


def _one_line_verdict(payload: dict[str, Any]) -> str:
    """final_v1 的一句话点评:优先 layer6.key_hook,退 final_verdict,再退 layer5.why。"""
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    layer5 = _as_dict(payload.get("layer5_recommendations"))
    for candidate in (layer6.get("key_hook"), layer6.get("final_verdict"), layer5.get("why")):
        text = _text(candidate, 200)
        if text:
            return text
    return ""


def _top_videos_block(
    evidence_rows: list[dict[str, Any]],
    payload_by_eid: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if not evidence_rows:
        return {"status": "empty", "reason": "该 KOL 暂无视频证据(vkpi_kol_video_evidence 空)。"}

    with_views = [r for r in evidence_rows if (_int_or_none(r.get("view_count")) or 0) > 0]
    if not with_views:
        return {
            "status": "empty",
            "reason": f"有 {len(evidence_rows)} 条视频证据但均无播放数,排不出最爆 — 等抓取回填 view_count。",
        }

    with_views.sort(key=lambda r: _int_or_none(r.get("view_count")) or 0, reverse=True)
    items: list[dict[str, Any]] = []
    for row in with_views[:3]:
        eid = _int_or_none(row.get("evidence_id"))
        payload = payload_by_eid.get(eid or -1) or {}
        verdict = _one_line_verdict(payload) if payload else ""
        views = _int_or_none(row.get("view_count"))
        likes = _int_or_none(row.get("like_count"))
        comments = _int_or_none(row.get("comment_count"))
        items.append(
            {
                "evidence_id": eid,
                "title": _text(row.get("title"), 160),
                "content_url": _text(row.get("content_url"), 500),
                "platform": _text(row.get("platform"), 30),
                "posted_at": _iso(row.get("posted_at")),
                "view_count": views,
                "like_count": likes,
                "comment_count": comments,
                "engagement_rate": _engagement_rate(views, likes, comments),
                "verdict": verdict or None,  # 无深析则 None,前端明说「未深析」
                "has_deep_analysis": bool(payload),
            }
        )
    return {"status": "ready", "items": items, "total_with_views": len(with_views)}


# ── 💬 块三:最引反馈 TOP3 ───────────────────────────────────────────


def _comment_distribution(conn: Any, evidence_id: int, limit: int = 300) -> dict[str, Any]:
    """该视频已入库评论(vkpi_comments evidence 桥)的意向/情感分布;没入库诚实 empty。"""
    from app.domains.kol.comment_intel import PURCHASE_INTENT_TERMS

    rows = conn.execute(
        """
        SELECT comment_text, language_detected FROM vkpi_comments
        WHERE post_table IN ('evidence', 'vkpi_kol_video_evidence') AND post_id = ?
        LIMIT ?
        """,
        (int(evidence_id), int(limit)),
    ).fetchall()
    if not rows:
        return {
            "status": "empty",
            "reason": "该视频评论未入库(仅有平台评论计数),意向/情感分布无从统计。",
        }

    intent = {"purchase_intent": 0, "question": 0}
    sentiment = {"positive": 0, "negative": 0, "neutral": 0}
    lang_counts: dict[str, int] = {}
    samples: list[str] = []
    for row in rows:
        data = dict(row)
        text = _text(data.get("comment_text"), 500)
        blob = text.lower()
        if not blob:
            continue
        if _match_terms(blob, PURCHASE_INTENT_TERMS):
            intent["purchase_intent"] += 1
            if len(samples) < 3:
                samples.append(_text(text, 80))
        if _match_terms(blob, QUESTION_TERMS):
            intent["question"] += 1
        pos = _match_terms(blob, POSITIVE_TERMS)
        neg = _match_terms(blob, NEGATIVE_TERMS)
        if pos and not neg:
            sentiment["positive"] += 1
        elif neg and not pos:
            sentiment["negative"] += 1
        else:
            sentiment["neutral"] += 1
        lang = _text(data.get("language_detected"), 10).lower()
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    return {
        "status": "ready",
        "sample_size": len(rows),
        "method": "lexicon_v0",
        "intent": intent,
        "sentiment": sentiment,
        "languages": lang_counts,  # language_detected 全空时为 {},前端不画
        "purchase_intent_samples": samples,
    }


def _feedback_block(
    conn: Any,
    evidence_rows: list[dict[str, Any]],
    payload_by_eid: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if not evidence_rows:
        return {"status": "empty", "reason": "该 KOL 暂无视频证据,无反馈可聚合。"}

    def _rank_key(row: dict[str, Any]) -> tuple[int, float]:
        comments = _int_or_none(row.get("comment_count")) or 0
        er = _engagement_rate(
            _int_or_none(row.get("view_count")),
            _int_or_none(row.get("like_count")),
            _int_or_none(row.get("comment_count")),
        )
        return (comments, er or 0.0)

    candidates = [r for r in evidence_rows if (_int_or_none(r.get("comment_count")) or 0) > 0]
    if not candidates:
        return {
            "status": "empty",
            "reason": f"有 {len(evidence_rows)} 条视频证据但评论计数均为 0/缺失,排不出最引反馈。",
        }
    candidates.sort(key=_rank_key, reverse=True)

    items: list[dict[str, Any]] = []
    for row in candidates[:3]:
        eid = _int_or_none(row.get("evidence_id"))
        views = _int_or_none(row.get("view_count"))
        likes = _int_or_none(row.get("like_count"))
        comments = _int_or_none(row.get("comment_count"))
        payload = payload_by_eid.get(eid or -1) or {}
        items.append(
            {
                "evidence_id": eid,
                "title": _text(row.get("title"), 160),
                "content_url": _text(row.get("content_url"), 500),
                "platform": _text(row.get("platform"), 30),
                "posted_at": _iso(row.get("posted_at")),
                "view_count": views,
                "comment_count": comments,
                "engagement_rate": _engagement_rate(views, likes, comments),
                "verdict": _one_line_verdict(payload) or None,
                "comments": _comment_distribution(conn, eid) if eid else {"status": "empty", "reason": "缺 evidence id"},
            }
        )
    return {"status": "ready", "items": items, "ranked_by": "comment_count desc, engagement_rate desc"}


# ── 主入口 ──────────────────────────────────────────────────────────


def signature_profile(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """KOL 招牌面板三块聚合;KOL 不存在抛 LookupError(路由转 404)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    final_rows = _load_final_v1_rows(db, int(kol_pool_id))
    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    payload_by_eid: dict[int, dict[str, Any]] = {}
    for row in final_rows:
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None:
            continue
        payload = _final_payload(row.get("result"))
        if payload:
            payload_by_eid[eid] = payload

    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "coverage": {
            "evidence_count": len(evidence_rows),
            "deep_analyzed_count": len(final_rows),
        },
        "shooting_styles": _shooting_styles_block(final_rows),
        "top_videos": _top_videos_block(evidence_rows, payload_by_eid),
        "feedback": _feedback_block(db, evidence_rows, payload_by_eid),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "纯聚合已有数据(final_v1 深析 + evidence + 已入库评论);独立展示信号,不参与 V6 Fit 评分。",
    }
