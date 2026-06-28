"""KOL 发现过滤器(从 profile_discovery.py 抽出,行为不变)。

纯函数+常量:地区排除(CN/HK/TW)/persona 相关度/相机信号闸。零 LLM/零 IO/零 Apify。
被 profile_discovery re-export 回灌,调用点不变。红线:纯过滤,零触 viltrox_fit_score。
"""
from __future__ import annotations

import re
from typing import Any


_EXCLUDED_REGION_RE = re.compile(
    r"中国|中國|大陆|大陸|香港|台湾|台灣|hong\s*kong|taiwan|china", re.IGNORECASE
)
_EXCLUDED_REGION_CODES = {"CN", "HK", "TW", "CHINA"}


def _country_in_excluded_region(*values: Any) -> bool:
    """地区排除判据(P0-6,取代旧 `_looks_chinese`):任一 country/market 文本命中
    {CN/HK/TW}(中文地名或 ISO 码)即排除;全空 → 放行。发现侧 item 无 per-item country,
    实际传入的是搜索 market;见调用点说明。"""
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text.upper() in _EXCLUDED_REGION_CODES:
            return True
        if _EXCLUDED_REGION_RE.search(text):
            return True
    return False


# 发现精准修(用户令):逐人地区识别 + persona 启发式相关度。全纯本地字符串比对,零 LLM/零 Apify。
# 大陆 + 台/港主要城市强信号(发现 item 无 per-item country,真地区信号落在 sample_title 文本,
# 如「#中国广州」)。口径保留海外华人:表内只放 CN大陆/TW/HK 地名,绝不含马六甲/新加坡/吉隆坡等海外地名。
_EXCLUDED_REGION_CITY_RE = re.compile(
    r"北京|上海|广州|廣州|深圳|杭州|成都|重庆|重慶|武汉|武漢|西安|南京|苏州|蘇州|天津|长沙|長沙|"
    r"郑州|鄭州|青岛|青島|大连|大連|厦门|廈門|东莞|東莞|佛山|宁波|寧波|无锡|無錫|昆明|合肥|济南|濟南|"
    r"沈阳|瀋陽|哈尔滨|哈爾濱|福州|南昌|贵阳|貴陽|南宁|南寧|"
    r"台北|臺北|高雄|台中|臺中|新北|桃园|桃園|台南|臺南",  # 台湾(TW)主要城市
    re.IGNORECASE,
)


def _detect_excluded_region(item: dict[str, Any]) -> str:
    """逐人地区识别(取代喂错字段的旧判据):扫真带地区信号的文本(sample_title/channel_name/handle),
    命中 {CN大陆/HK/TW} 地名/国名/ISO 码 → 返回地区码;否则 ""。口径保留海外华人(马六甲/新加坡/
    海外中文博主只要无大陆+港台地名即放行——单纯中文字符不命中)。"""
    if _country_in_excluded_region(item.get("country"), item.get("region")):
        return "CN/HK/TW"
    blob = " ".join(str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle"))
    if not blob.strip():
        return ""
    if _EXCLUDED_REGION_RE.search(blob) or _EXCLUDED_REGION_CITY_RE.search(blob):
        return "CN/HK/TW"
    return ""


# persona 启发式相关度:发现 item 文本 vs 产品 persona 正/负词。泛词不计分,英文优先。
_PERSONA_GENERIC_TERMS = {
    "photo", "photos", "photography", "photographer", "photographers", "video", "videos",
    "videography", "videographer", "content", "creator", "creators", "vlog", "vlogger",
    "vlogging", "camera", "gear", "film", "filmmaker", "filmmaking", "reel", "reels",
    "shoot", "shooting", "and", "the", "for", "with",
    "摄影", "攝影", "摄影师", "攝影師", "视频", "視頻", "创作者", "創作者", "博主", "内容",
    "拍摄", "拍攝", "短视频", "短視頻", "相机", "相機", "器材", "视频创作者",
}


def _persona_term_list(*sources: Any) -> list[str]:
    """归一 persona 字段(list / JSON 串 / None 各自兜底)→ 分词 → 去泛词 → 去重保序。"""
    out: list[str] = []
    for src in sources:
        if src is None:
            continue
        value: Any = src
        if isinstance(src, (str, bytes)):
            s = src.decode() if isinstance(src, bytes) else src
            s = s.strip()
            if s[:1] in ("[", "{"):
                try:
                    import json as _json_mod

                    value = _json_mod.loads(s)
                except Exception:
                    value = s
            else:
                value = s
        if isinstance(value, dict):
            value = list(value.values())
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for entry in items:
            for tok in re.split(r"[\s,/、，;；|·\-]+", str(entry or "").lower()):
                tok = tok.strip()
                if len(tok) >= 2 and tok not in _PERSONA_GENERIC_TERMS:
                    out.append(tok)
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _persona_positive_terms(product_focus: Any, ideal_creator_types: Any, verticals: Any, query: Any) -> list[str]:
    return _persona_term_list(product_focus, ideal_creator_types, verticals, query)


def _persona_avoid_terms(avoid_types: Any) -> list[str]:
    return _persona_term_list(avoid_types)


def _persona_relevance(item: dict[str, Any], *, pos_terms: list[str], neg_terms: list[str]) -> dict[str, Any]:
    """persona 启发式相关度(纯本地零 LLM):扫 item 文本对正/负词命中打分。
    返回 {score, relevance_score, relevance_tier, relevance_hits};score=relevance_score 供落库。
    red line:独立展示信号,绝不并入 viltrox_fit_score / rule_v0。CN/HK/TW 不在此扣分(交 _detect_excluded_region 排)。"""
    # 只看候选**自身内容**(标题/频道名/handle);绝不含 search_query —— 那是查询词本身,会自命中致全 1.0。
    blob = " ".join(
        str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle")
    ).lower()
    if not pos_terms and not neg_terms:
        return {"score": 0.5, "relevance_score": 0.5, "relevance_tier": "中", "relevance_hits": []}
    hits = [t for t in pos_terms if t in blob]
    neg_hits = [t for t in neg_terms if t in blob]
    score = 0.35 + 0.18 * len(hits) - 0.30 * len(neg_hits)
    if not hits:
        score = min(score, 0.12)  # 零正命中=泛结果,压低,排序后置
    score = max(0.0, min(1.0, score))
    tier = "高" if score >= 0.6 else ("中" if score >= 0.3 else "低")
    return {
        "score": round(score, 4),
        "relevance_score": round(score, 4),
        "relevance_tier": tier,
        "relevance_hits": hits[:6],
    }


# ── 相机/视觉创作者相关度闸门(用户硬要求:「用户得有相机,得需要拍摄」)──────────────────────
# 只放行真正的 摄影/摄像/电影/视觉内容 创作者。_persona_relevance 只调排序不丢弃,这里负责真丢弃。
# 信号集**故意做宽**:真摄影师/视频创作者的 频道名/handle/标题 几乎必含其中之一即放行;
# 香水/啤酒/戏剧/政治帖等非视觉创作者无任一信号 → 丢弃。red line:只做 FILTER(丢),绝不改 viltrox_fit_score / rule_v0。
_CAMERA_SIGNAL_TERMS = frozenset({
    "photography", "photographer", "videography", "videographer", "filmmaker",
    "filmmaking", "cinematography", "cinematographer", "camera", "lens", "video",
    "film", "cinema", "anamorphic", "footage", "shoot", "shooting", "photo",
    "editing", "editor", "colorist", "director", "dp", "dop", "reel", "reels",
    "short", "shorts", "vlog", "vlogger", "youtuber", "content creator", "4k",
    "8k", "bts", "b-roll", "gimbal", "drone", "mirrorless", "dslr", "creator",
})

# 明确的非视觉「硬避免」集:商业带货 / 政治帖等与相机拍摄无关的品类,命中即丢(优先于相机信号判断)。
_HARD_AVOID_TERMS = frozenset({
    "scentsy", "fragrance", "perfume", "scent", "essential oil", "candle", "mlm",
    "beer", "alcohol", "wine", "spirits", "brewery", "distillery", "jewelry",
    "jewellery", "real estate", "realtor", "realty", "mortgage", "crypto",
    "forex", "nft", "casino", "betting", "supplement", "weight loss", "diet",
    "recruiter", "stopwar", "political",
})


def _candidate_blob(item: dict[str, Any]) -> str:
    """候选自身内容拼接(sample_title + channel_name + handle),小写。绝不含 search_query(查询词会自命中)。"""
    return " ".join(
        str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle")
    ).lower()


def _has_camera_signal(item: dict[str, Any]) -> bool:
    """候选是否带任一「相机/拍摄/视觉创作」信号(宽口径)。无任一信号 → 非视觉创作者,应丢弃。"""
    blob = _candidate_blob(item)
    return any(term in blob for term in _CAMERA_SIGNAL_TERMS)


def _is_hard_avoid(item: dict[str, Any], neg_terms: list[str]) -> bool:
    """候选是否命中明确非视觉「硬避免」品类,或命中任一 persona 负词 → 直接丢弃(不止扣分)。"""
    blob = _candidate_blob(item)
    if any(term in blob for term in _HARD_AVOID_TERMS):
        return True
    return any(t and t in blob for t in neg_terms)


