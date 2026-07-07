"""E1 情绪标签体系(emotion_tags_v1)——两轴×器材四情绪×hook×挂车,纯词表零成本回打。

标签定义(蓝图 2a「平台情绪×量化打法层」;规则锚点吃 growth_playbook,不重造):
  两轴        arousal(high/medium/low)× valence(positive/negative/mixed/neutral)
              → quadrant(如 high_arousal_positive;锚 growth_playbook.emotion_two_axis)
  器材四情绪  identity(我是创作者)/ capability(画面被解锁)/ upgrade(升级渴望)/
              craft(工艺崇拜)——多标签,另出 primary_gear_emotion(命中数最高者)
  awe         奇观:不可能视角/极限环境/肉眼看不到(锚 content_template_awe)
  hook_type   断言 assertion / 提问 question / pattern_interrupt / 结果前置 result_first
              (器材内容处方默认结果前置,检测优先级 question>pattern_interrupt>result_first>assertion)
  has_cart    挂车/链接迹象(小黄车/link in bio/优惠码等;evidence 无 description 列,
              仅靠标题+深析叙事,天然保守偏低,诚实标注)

数据真相(2026-07-07 本地侦察):
  vkpi_kol_llm_deep_analysis_results.llm_dimensions_11(video_final_v1, ready)真键 =
    layer1_summary{content_summary/scene_timeline/product_presence/brand_exposure/
    production_observations/competitor_presence} + risk{key_hook/risk_flags/final_verdict}
    + recommendations + scores + llm_v6_fit + schema_version + source + qa_source_cache_id;
  情绪叙事正文在源缓存 vkpi_analysis_cache.result 的 layer2_viewer_emotion(双 schema 变体,
    老式 memory_points/heart_movement_score…,新式 first_three_seconds_feeling/
    one_sentence_viewer_reaction…),读取时递归拍平全部字符串,不赌键名。

写入位置(零新迁移):llm_dimensions_11 顶层附加键 "emotion_tags_v1",
  用 jsonb || 单键合并——绝不覆盖上述 LLM 原产物键;若 final_v1_extract 重跑覆写整列,
  重跑本回打器即可恢复(幂等:method+lexicon_version 相同即跳过,复跑 0 新写)。

【成本红线】本模块绝不触发任何 LLM 调用或视频重析:回打器是纯词表规则法(零成本),
  读的全是已析产物;涉及重析的一切只允许人工手动入队,本模块不提供也不调用该路径。

【未来新析 schema 建议——烧钱路径改动,列清单待人批,本波不改 prompt】
  下次深析 prompt 扩展(= 重析 = LLM 成本,须人工批准并手动入队)时建议 layer2 新增结构化键:
    emotion_arousal: "high"|"medium"|"low"
    emotion_valence: "positive"|"negative"|"mixed"
    gear_emotions:   ["identity"|"capability"|"upgrade"|"craft", ...] + primary_gear_emotion
    awe:             bool(奇观:不可能视角/极限环境)
    hook_type:       "assertion"|"question"|"pattern_interrupt"|"result_first"
    hook_3s_tier:    "A"|"B"|"C"(与 growth_playbook tt_hook_2s_a_tier 同口径)
    has_cart:        bool + cart_evidence: str(挂车/购物链接的画面或口播证据)
  届时 LLM 原生标签写独立键 emotion_tags_llm,与本词表键并存对账,谁也不覆盖谁。

compat 约定:SQL 占位符用 ?;SQL 禁 percent 字面(不用 LIKE);禁 jsonb ? 操作符
  (存在性判断在 Python 侧做);JSONB 读回 dict/str 双态用 _loads 容错;写后 commit
  防 idle-in-transaction。
老红线:绝不写 viltrox_fit_score、不碰 rule_v0;新词表标签独立展示,不参与 V6 Fit 评分。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

EMOTION_TAGS_KEY = "emotion_tags_v1"
SCHEMA_VERSION = "emotion_tags_v1"
METHOD = "lexicon_v0"
LEXICON_VERSION = "lexicon_v0_2026_07_07"
ANALYSIS_KIND = "video_final_v1"

AROUSAL_LEVELS = ("high", "medium", "low")
VALENCE_LEVELS = ("positive", "negative", "mixed", "neutral")
GEAR_EMOTION_KEYS = ("identity", "capability", "upgrade", "craft")
HOOK_TYPES = ("question", "pattern_interrupt", "result_first", "assertion", "unclassified")

# 本模块消费的 growth_playbook 规则锚点(读接口原样带出,数字不在本处硬编码)
PLAYBOOK_RULE_IDS = (
    "emotion_two_axis",
    "emotion_gear_four_labels",
    "content_template_awe",
    "ethics_gate_no_negative_manipulation",
)

# ── 词表(lexicon_v0;中英双语;英文按词边界正则、中文按子串;宁缺毋滥)────────

AROUSAL_HIGH_TERMS: tuple[str, ...] = (
    "震撼", "震惊", "惊艳", "惊叹", "惊呆", "冲击", "炸裂", "疯狂", "杀伤力", "悬念",
    "紧迫", "刺激", "上头", "冲动", "绝杀", "拉满", "燃", "爆点", "抓人", "眼前一亮",
    "wow", "insane", "crazy", "mind-blowing", "mind blowing", "jaw-dropping",
    "unbelievable", "shocking", "stunning", "epic", "breathtaking", "explosive",
)
AROUSAL_LOW_TERMS: tuple[str, ...] = (
    "平淡", "舒缓", "平静", "冗长", "乏味", "温和", "娓娓道来", "沉稳", "催眠", "拖沓",
    "calm", "boring", "slow-paced", "mellow", "relaxed", "soothing", "monotone",
)

VALENCE_POSITIVE_TERMS: tuple[str, ...] = (
    "太美", "好看", "惊艳", "心动", "种草", "认可", "值得", "真香", "优秀", "出色",
    "惊喜", "好评", "精彩", "漂亮", "信任", "想买", "想要", "喜欢", "性价比", "香",
    "amazing", "beautiful", "impressive", "love", "great", "excellent", "gorgeous",
    "stunning", "awesome", "fantastic", "incredible",
)
VALENCE_NEGATIVE_TERMS: tuple[str, ...] = (
    "失望", "反感", "翻车", "怀疑", "劝退", "审美疲劳", "广告感", "生硬", "尴尬",
    "廉价", "拉胯", "缺陷", "不满", "差评", "抱怨", "智商税", "割韭菜", "抵触",
    "disappointing", "disappointed", "annoying", "skeptical", "overpriced",
    "terrible", "awful", "scam", "gimmick",
)
# 负向词常被叙事否定(「几乎没有反感」「不易反感」),先把否定短语从底料里剃掉再匹配。
_NEGATION_SCRUB = re.compile(
    r"(几乎没有|几乎无|没有明显|无明显|没有|没什么|并无|毫无|不易|不会|不产生|难以|不)"
    r"[^。;!?,，、]{0,10}?"
    r"(反感|失望|怀疑|抵触|抱怨|疲劳|广告感|劝退|尴尬|不满|生硬)"
)

GEAR_EMOTION_TERMS: dict[str, tuple[str, ...]] = {
    # identity——我是创作者:身份认同/人设/自我表达
    "identity": (
        "身份认同", "人设", "自我表达", "创作者身份", "个人风格", "自己的风格", "创作理念",
        "成为更好的", "身份感", "归属感", "共鸣",
        "identity", "as a filmmaker", "as a photographer", "as a creator",
        "signature style", "who you are",
    ),
    # capability——画面被解锁:以前做不到的现在能拍出
    "capability": (
        "解锁", "能拍出", "拍出了", "做到了", "拍不出", "实现了", "可能性", "第一次拍",
        "能做到", "新玩法", "生产力",
        "unlock", "unlocks", "now you can", "enables", "empowers", "finally able",
        "couldn't shoot before", "opens up",
    ),
    # upgrade——升级渴望:越级/媲美原厂/替换升级
    "upgrade": (
        "升级", "换代", "换镜头", "越级", "媲美", "打原厂", "替代", "淘汰", "旗舰",
        "顶级", "碾压", "追平", "差价",
        "upgrade", "next level", "level up", "replace my", "better than my",
        "flagship", "rivals the",
    ),
    # craft——工艺崇拜:做工/用料/手感/复古颜值
    "craft": (
        "做工", "工艺", "用料", "金属", "手感", "精致", "复古", "颜值", "质感拉满",
        "打磨", "精密", "考究",
        "build quality", "craftsmanship", "premium feel", "all-metal", "machined",
        "vintage", "well built", "well-built",
    ),
}

AWE_TERMS: tuple[str, ...] = (
    "奇观", "震撼", "壮观", "不可能视角", "不可能的视角", "极限环境", "肉眼看不到",
    "肉眼不可见", "叹为观止", "史诗感", "宏大", "银河", "星空", "无人机视角",
    "awe", "breathtaking", "spectacular", "otherworldly", "impossible shot",
    "impossible angle", "never seen before", "epic scale",
)

HOOK_QUESTION_TERMS: tuple[str, ...] = (
    "?", "？", "提问", "疑问", "反问", "抛出问题", "灵魂拷问",
)
HOOK_PATTERN_INTERRUPT_TERMS: tuple[str, ...] = (
    "反差", "反转", "意外", "打破", "冲突", "悬念", "出乎意料", "颠覆", "违和",
    "pattern interrupt", "plot twist", "unexpected",
)
HOOK_RESULT_FIRST_TERMS: tuple[str, ...] = (
    "成片先行", "成片前置", "结果前置", "开场直接", "开门见山", "上来就", "直接抛出",
    "直接展示", "效果对比", "先看效果", "先上成片", "开篇直接", "样片开场", "成品先行",
    "final result first", "starts with the result", "proof-first", "before-after",
    "before and after", "with/without",
)
HOOK_ASSERTION_TERMS: tuple[str, ...] = (
    "断言", "宣称", "结论先行", "直接给出结论", "定论",
    "the best", "you need", "must have", "must-have", "never buy", "stop using",
    "the only", "don't buy",
)

CART_TERMS: tuple[str, ...] = (
    "挂车", "小黄车", "购物车", "带货链接", "购买链接", "下方链接", "链接在简介",
    "评论区链接", "优惠码", "折扣码", "佣金链接", "闪购", "限时优惠",
    "link in bio", "link in description", "link below", "affiliate link",
    "discount code", "promo code", "coupon code", "shop now", "tiktok shop",
    "yellow cart", "use code",
)


# ── 小工具(容错;与 signature_profile 同款 compat 口径)────────────────────


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


def _text(value: Any, limit: int = 400) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 词表命中缓存:英文词 → 词边界正则;中文/符号词 → None(走子串)。False 作缺席哨兵。
_TERM_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _term_pattern(term: str) -> re.Pattern[str] | None:
    """英文词表词 → 词边界正则(边界用 [a-z0-9],中英混排安全;与 signature_profile 同口径)。"""
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


def _match_hits(blob: str, terms: tuple[str, ...], cap: int = 5) -> list[str]:
    """词表命中并返回命中的词(≤cap 条,供 matched_terms 取证);未命中回 []。"""
    hits: list[str] = []
    for term in terms:
        pattern = _term_pattern(term)
        matched = pattern.search(blob) if pattern is not None else (term in blob)
        if matched:
            hits.append(term)
            if len(hits) >= cap:
                break
    return hits


def _flatten_strings(value: Any, out: list[str], limit_each: int = 300, cap: int = 40) -> None:
    """递归拍平 dict/list 里的全部字符串(layer2 双 schema 变体不赌键名)。"""
    if len(out) >= cap:
        return
    if isinstance(value, str):
        text = _text(value, limit_each)
        if text:
            out.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            _flatten_strings(item, out, limit_each, cap)
    elif isinstance(value, list):
        for item in value:
            _flatten_strings(item, out, limit_each, cap)


# ── 词表分类器(纯函数,零 DB 零 LLM;冒烟可直接单测)──────────────────────


def classify_emotion_text(full_blob: str, hook_blob: str) -> dict[str, Any]:
    """对一条已析视频的文本底料打 emotion_tags_v1 标签(纯词表规则,method=lexicon_v0)。

    full_blob:标题+layer2 情绪叙事+layer1 摘要(小写);hook_blob:key_hook+标题+首镜+前3秒(小写)。
    """
    full = _NEGATION_SCRUB.sub(" ", full_blob or "")
    hook = hook_blob or ""

    matched: dict[str, list[str]] = {}

    high_hits = _match_hits(full, AROUSAL_HIGH_TERMS)
    low_hits = _match_hits(full, AROUSAL_LOW_TERMS)
    if high_hits and len(high_hits) >= len(low_hits):
        arousal = "high"
    elif low_hits:
        arousal = "low"
    else:
        arousal = "medium"
    if high_hits:
        matched["arousal_high"] = high_hits
    if low_hits:
        matched["arousal_low"] = low_hits

    pos_hits = _match_hits(full, VALENCE_POSITIVE_TERMS)
    neg_hits = _match_hits(full, VALENCE_NEGATIVE_TERMS)
    if pos_hits and neg_hits:
        valence = "mixed"
    elif pos_hits:
        valence = "positive"
    elif neg_hits:
        valence = "negative"
    else:
        valence = "neutral"
    if pos_hits:
        matched["valence_positive"] = pos_hits
    if neg_hits:
        matched["valence_negative"] = neg_hits

    gear_hits: dict[str, list[str]] = {}
    for key, terms in GEAR_EMOTION_TERMS.items():
        hits = _match_hits(full, terms)
        if hits:
            gear_hits[key] = hits
            matched[f"gear_{key}"] = hits
    gear_emotions = [k for k in GEAR_EMOTION_KEYS if k in gear_hits]
    primary_gear = max(gear_hits, key=lambda k: len(gear_hits[k])) if gear_hits else None

    awe_hits = _match_hits(full, AWE_TERMS)
    if awe_hits:
        matched["awe"] = awe_hits

    # hook 检测优先级:question(标点/词最可靠)> pattern_interrupt > result_first > assertion
    hook_type = "unclassified"
    for candidate, terms in (
        ("question", HOOK_QUESTION_TERMS),
        ("pattern_interrupt", HOOK_PATTERN_INTERRUPT_TERMS),
        ("result_first", HOOK_RESULT_FIRST_TERMS),
        ("assertion", HOOK_ASSERTION_TERMS),
    ):
        hits = _match_hits(hook, terms)
        if hits:
            hook_type = candidate
            matched[f"hook_{candidate}"] = hits
            break

    cart_hits = _match_hits(full + " " + hook, CART_TERMS)
    if cart_hits:
        matched["has_cart"] = cart_hits

    total_hits = sum(len(v) for v in matched.values())
    confidence = round(min(0.9, 0.35 + 0.05 * total_hits), 2) if total_hits else 0.2

    return {
        "schema_version": SCHEMA_VERSION,
        "method": METHOD,
        "lexicon_version": LEXICON_VERSION,
        "arousal": arousal,
        "valence": valence,
        "quadrant": f"{arousal}_arousal_{valence}",
        "gear_emotions": gear_emotions,
        "primary_gear_emotion": primary_gear,
        "awe": bool(awe_hits),
        "hook_type": hook_type,
        "has_cart": bool(cart_hits),
        "confidence": confidence,
        "matched_terms": matched,
        "tagged_at": _utcnow_iso(),
    }


# ── 数据装载(全只读;一次 join 取齐文本底料)────────────────────────────────


_LOAD_SQL = """
SELECT
  r.id AS row_id,
  r.kol_pool_id,
  r.source_evidence_id,
  r.llm_dimensions_11 -> 'emotion_tags_v1' AS existing_tags,
  r.llm_dimensions_11 -> 'layer1_summary' ->> 'content_summary' AS content_summary,
  r.llm_dimensions_11 -> 'layer1_summary' -> 'scene_timeline' -> 0 AS first_scene,
  r.llm_dimensions_11 -> 'risk' ->> 'key_hook' AS key_hook,
  COALESCE(e.video_title, e.title, '') AS title,
  COALESCE(
    c.result -> 'raw_gemini_video' -> 'video_analysis_final_v1' -> 'layer2_viewer_emotion',
    c.result -> 'video_analysis_final_v1' -> 'layer2_viewer_emotion',
    c.result -> 'layer2_viewer_emotion'
  ) AS layer2
FROM vkpi_kol_llm_deep_analysis_results r
LEFT JOIN vkpi_kol_video_evidence e ON e.id = r.source_evidence_id
LEFT JOIN vkpi_analysis_cache c ON c.id = r.source_cache_id
WHERE r.analysis_kind = ?
  AND r.status = 'ready'
ORDER BY r.id
"""


def _load_taggable_rows(conn: Any, limit: int | None = None) -> list[dict[str, Any]]:
    sql = _LOAD_SQL
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (ANALYSIS_KIND,)).fetchall()
    return [dict(r) for r in rows]


def _row_blobs(row: dict[str, Any]) -> tuple[str, str]:
    """一行深析 → (full_blob, hook_blob),全部小写;缺哪块就少哪块,不杜撰。"""
    title = _text(row.get("title"), 200)
    content_summary = _text(row.get("content_summary"), 800)
    key_hook = _text(row.get("key_hook"), 400)

    layer2_parts: list[str] = []
    _flatten_strings(_loads(row.get("layer2")), layer2_parts)
    layer2_text = " ".join(layer2_parts)

    first_scene = _as_dict(_loads(row.get("first_scene")))
    first_scene_text = " ".join(
        p for p in (_text(first_scene.get("what"), 200), _text(first_scene.get("why_it_matters"), 200)) if p
    )

    full_blob = " ".join(p for p in (title, layer2_text, content_summary) if p).lower()
    hook_blob = " ".join(p for p in (key_hook, title, first_scene_text) if p).lower()
    return full_blob, hook_blob


def _is_current_tags(existing: Any) -> bool:
    tags = _as_dict(_loads(existing))
    return tags.get("method") == METHOD and tags.get("lexicon_version") == LEXICON_VERSION


# ── ② 规则法回打器(dry_run 默认 True;幂等复跑 0 新写)────────────────────


def tag_analyzed_videos(
    dry_run: bool = True,
    limit: int | None = None,
    *,
    force: bool = False,
    conn: Any = None,
) -> dict[str, Any]:
    """对全部已析(final_v1, ready)视频用词表回打 emotion_tags_v1(零 LLM 零重析零成本)。

    写入 = llm_dimensions_11 顶层单键合并(jsonb ||),绝不覆盖 LLM 原产物键;
    幂等:已有 method+lexicon_version 相同的标签即跳过(force=True 才重打)。
    dry_run=True 只统计分布不写库。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    rows = _load_taggable_rows(db, limit)

    scanned = len(rows)
    skipped_existing = 0
    skipped_no_text = 0
    tagged: list[tuple[int, dict[str, Any]]] = []

    for row in rows:
        if not force and _is_current_tags(row.get("existing_tags")):
            skipped_existing += 1
            continue
        full_blob, hook_blob = _row_blobs(row)
        if not full_blob and not hook_blob:
            skipped_no_text += 1
            continue
        row_id = _int_or_none(row.get("row_id"))
        if row_id is None:
            continue
        tagged.append((row_id, classify_emotion_text(full_blob, hook_blob)))

    written = 0
    if not dry_run:
        for row_id, tags in tagged:
            payload = json.dumps({EMOTION_TAGS_KEY: tags}, ensure_ascii=False)
            db.execute(
                """
                UPDATE vkpi_kol_llm_deep_analysis_results
                SET llm_dimensions_11 = COALESCE(llm_dimensions_11, '{}'::jsonb) || ?::jsonb
                WHERE id = ?
                """,
                (payload, row_id),
            )
            written += 1
    db.commit()  # 写则落盘;纯读也 commit,防 idle-in-transaction

    distribution = _aggregate_tags([tags for _rid, tags in tagged])
    return {
        "status": "dry_run" if dry_run else "done",
        "analysis_kind": ANALYSIS_KIND,
        "scanned": scanned,
        "would_tag": len(tagged),
        "tagged_written": written,
        "skipped_existing": skipped_existing,
        "skipped_no_text": skipped_no_text,
        "distribution": distribution,
        "method": METHOD,
        "lexicon_version": LEXICON_VERSION,
        "write_target": "vkpi_kol_llm_deep_analysis_results.llm_dimensions_11['emotion_tags_v1']",
        "llm_calls": False,
        "reanalysis_triggered": False,
        "sample": [
            {"row_id": rid, **{k: t.get(k) for k in ("quadrant", "gear_emotions", "hook_type", "awe", "has_cart", "confidence")}}
            for rid, t in tagged[:5]
        ],
        "generated_at": _utcnow_iso(),
    }


def _aggregate_tags(tag_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """标签列表 → 分布统计(回打器 dry 报告与 KOL 情绪画像共用)。"""
    arousal = {k: 0 for k in AROUSAL_LEVELS}
    valence = {k: 0 for k in VALENCE_LEVELS}
    quadrant: dict[str, int] = {}
    gear = {k: 0 for k in GEAR_EMOTION_KEYS}
    primary_gear: dict[str, int] = {}
    hook = {k: 0 for k in HOOK_TYPES}
    awe_count = 0
    cart_count = 0
    confidences: list[float] = []

    for tags in tag_dicts:
        a = str(tags.get("arousal") or "")
        v = str(tags.get("valence") or "")
        if a in arousal:
            arousal[a] += 1
        if v in valence:
            valence[v] += 1
        q = str(tags.get("quadrant") or "")
        if q:
            quadrant[q] = quadrant.get(q, 0) + 1
        for g in _as_list(tags.get("gear_emotions")):
            if g in gear:
                gear[g] += 1
        pg = tags.get("primary_gear_emotion")
        if pg:
            primary_gear[str(pg)] = primary_gear.get(str(pg), 0) + 1
        h = str(tags.get("hook_type") or "")
        if h in hook:
            hook[h] += 1
        if tags.get("awe"):
            awe_count += 1
        if tags.get("has_cart"):
            cart_count += 1
        try:
            confidences.append(float(tags.get("confidence")))
        except (TypeError, ValueError):
            pass

    n = len(tag_dicts)
    return {
        "sample_size": n,
        "arousal": arousal,
        "valence": valence,
        "quadrant": dict(sorted(quadrant.items(), key=lambda kv: kv[1], reverse=True)),
        "gear_emotions": gear,  # 多标签口径:各项之和可大于 sample_size
        "primary_gear_emotion": dict(sorted(primary_gear.items(), key=lambda kv: kv[1], reverse=True)),
        "hook_type": hook,
        "awe_count": awe_count,
        "awe_rate": round(awe_count / n, 3) if n else None,
        "has_cart_count": cart_count,
        "has_cart_rate": round(cart_count / n, 3) if n else None,
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


# ── ③ read 端:KOL 情绪画像聚合 ────────────────────────────────────────────


def _playbook_refs() -> list[dict[str, Any]]:
    """带出本体系锚定的 growth_playbook 规则(消费不重造;取不到诚实缺席)。"""
    try:
        from app.domains.market_brain import growth_playbook
    except Exception:
        return []
    refs: list[dict[str, Any]] = []
    for rule_id in PLAYBOOK_RULE_IDS:
        try:
            r = growth_playbook.rule(rule_id)
        except Exception:
            continue
        refs.append({
            "rule_id": r.get("rule_id"),
            "statement": r.get("statement"),
            "source": r.get("source"),
            "confidence": r.get("confidence"),
        })
    return refs


def video_emotion_profile(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """该 KOL 已析视频的情绪分布聚合(纯读;KOL 不存在抛 LookupError → 路由转 404)。

    未回打时诚实 empty 并指路 POST /emotion-tags/backfill;绝不现场触发任何分析。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = db.execute(
        "SELECT id, handle, display_name, platform FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    if not pool:
        db.commit()
        raise LookupError(f"kol_pool {kol_pool_id} not found")
    pool_d = dict(pool)

    rows = db.execute(
        """
        SELECT
          r.id AS row_id,
          r.source_evidence_id,
          r.llm_dimensions_11 -> 'emotion_tags_v1' AS tags,
          COALESCE(e.video_title, e.title, '') AS title,
          e.view_count,
          e.content_url
        FROM vkpi_kol_llm_deep_analysis_results r
        LEFT JOIN vkpi_kol_video_evidence e ON e.id = r.source_evidence_id
        WHERE r.kol_pool_id = ?
          AND r.analysis_kind = ?
          AND r.status = 'ready'
        ORDER BY COALESCE(e.view_count, 0) DESC, r.id DESC
        """,
        (int(kol_pool_id), ANALYSIS_KIND),
    ).fetchall()
    db.commit()  # 读后即 commit,防 idle-in-transaction

    base = {
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool_d.get("handle"), 100),
            "display_name": _text(pool_d.get("display_name"), 100),
            "platform": _text(pool_d.get("platform"), 30),
        },
        "method": METHOD,
        "lexicon_version": LEXICON_VERSION,
        "llm_calls": False,
        "generated_at": _utcnow_iso(),
    }
    if not rows:
        return {
            **base,
            "status": "empty",
            "reason": "该 KOL 还没有已深析(final_v1)视频,情绪画像无从聚合 — 深析属烧钱路径,仅手动入队,本接口不触发。",
            "coverage": {"analyzed": 0, "tagged": 0},
        }

    # 每条 evidence 只取最新一行深析(重析产生的旧行不重复计入分布)
    tag_dicts: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    seen_eids: set[int] = set()
    analyzed = 0
    for row in rows:
        d = dict(row)
        eid = _int_or_none(d.get("source_evidence_id"))
        if eid is not None:
            if eid in seen_eids:
                continue
            seen_eids.add(eid)
        analyzed += 1
        tags = _as_dict(_loads(d.get("tags")))
        if not tags:
            continue
        tag_dicts.append(tags)
        if len(samples) < 6:
            samples.append({
                "evidence_id": eid,
                "title": _text(d.get("title"), 120),
                "view_count": _int_or_none(d.get("view_count")),
                "quadrant": tags.get("quadrant"),
                "gear_emotions": tags.get("gear_emotions"),
                "hook_type": tags.get("hook_type"),
                "awe": tags.get("awe"),
                "has_cart": tags.get("has_cart"),
            })

    if not tag_dicts:
        return {
            **base,
            "status": "empty",
            "reason": (
                f"该 KOL 有 {analyzed} 条已析视频但均未回打情绪标签 — "
                "先跑 POST /api/admin/vkpi/emotion-tags/backfill(纯词表零成本)。"
            ),
            "coverage": {"analyzed": analyzed, "tagged": 0},
        }

    distribution = _aggregate_tags(tag_dicts)
    dominant_quadrant = next(iter(distribution["quadrant"]), None)
    return {
        **base,
        "status": "ready",
        "coverage": {"analyzed": analyzed, "tagged": len(tag_dicts)},
        "distribution": distribution,
        "dominant_quadrant": dominant_quadrant,
        "samples": samples,
        "playbook_refs": _playbook_refs(),
        "note": (
            "词表规则打标(lexicon_v0,零 LLM);has_cart 仅靠标题+深析叙事(无 description 列)天然保守偏低;"
            "独立展示信号,不参与 V6 Fit 评分。"
        ),
    }
