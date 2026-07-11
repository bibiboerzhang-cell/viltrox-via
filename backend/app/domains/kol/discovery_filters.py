"""KOL 发现过滤器(从 profile_discovery.py 抽出,行为不变)。

纯函数+常量:地区排除(CN/HK/TW)/persona 相关度/相机信号闸。零 LLM/零 IO/零 Apify。
被 profile_discovery re-export 回灌,调用点不变。红线:纯过滤,零触 viltrox_fit_score。
"""
from __future__ import annotations

import os
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


# ── 召回触达门槛(用户裁决 2026-07-11):粉丝明确低于门槛、或互动信号实测全零的账号
# 不再进推荐/发现列表。与「排除中文 KOL(地区判据)」同族同层:纯召回/候选层 FILTER
# (只挡展示/推荐入口,不删数据、Pool 已有行保留、MY KOL 不受影响)。
# red line:绝不写 viltrox_fit_score / 不改 rule_v0 / 不动任何评分公式,只做候选过滤。
# env:VKPI_DISCOVERY_REACH_FLOOR_ENABLED 总开关(默认开)+ VKPI_DISCOVERY_MIN_FOLLOWERS
# 阈值(默认 1000)。运行时读 env(非 import 时快照),线上改 env 重启即生效。
REACH_FLOOR_SWITCH_ENV = "VKPI_DISCOVERY_REACH_FLOOR_ENABLED"
REACH_FLOOR_MIN_FOLLOWERS_ENV = "VKPI_DISCOVERY_MIN_FOLLOWERS"
_REACH_FLOOR_DEFAULT_MIN_FOLLOWERS = 1000

# 候选行字段形态各出口不一(pool 行 followers/avg_views/avg_comments;发现 item
# views/comments/likes,followers 仅 facebook >0 时透出),按族探测、缺列不误杀。
_REACH_FOLLOWER_KEYS = ("followers", "follower_count", "subscriber_count", "subscribers")
_REACH_VIEW_KEYS = ("views", "avg_views", "view_count", "median_views", "play_count")
_REACH_COMMENT_KEYS = ("comments", "avg_comments", "comment_count")
_REACH_EXTRA_SIGNAL_KEYS = ("likes", "like_count", "engagement_rate", "engagement")


def _reach_floor_enabled() -> bool:
    """总开关(默认开)。与 RECALL_LLM_RERANK_ENABLED 同款 env 布尔口径。"""
    raw = str(os.environ.get(REACH_FLOOR_SWITCH_ENV, "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _reach_floor_min_followers() -> int:
    """粉丝门槛(默认 1000);env 不可解析/负数 → 回默认,绝不因坏配置放大误杀。"""
    raw = str(os.environ.get(REACH_FLOOR_MIN_FOLLOWERS_ENV, "")).strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return _REACH_FLOOR_DEFAULT_MIN_FOLLOWERS
    return value if value >= 0 else _REACH_FLOOR_DEFAULT_MIN_FOLLOWERS


def _known_numeric(candidate: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """候选行里该字段族第一个「明确在场且可解析」的数值;族内全缺 → None。

    不误杀口径:key 不在 dict = 字段真缺;value=None/空串 = NULL 读回;两者都算「未知」,
    返回 None 由调用方放行——绝不把未知当 0 挡人。BOOLEAN/Decimal 读回走 float() 容错。"""
    for key in keys:
        if key not in candidate:
            continue
        value = candidate.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _reach_floor_reason(candidate: dict[str, Any] | None) -> str:
    """召回触达门槛判据:命中返回原因串(供 debug 日志),放行返回空串。

    两条挡规(用户裁决 2026-07-11):
    ① followers 明确在场且 < 门槛(默认 1000)→ 挡;followers 缺/NULL → 未知,放行。
    ② 互动信号实测全零:播放族与评论族都「明确在场」且全为 0,且无任何其他正互动信号
       (likes/engagement_rate)→ 挡。任一族真缺 → 未知,放行;fast_path 项豁免
       (YouTube Data API search.list 无统计数据,views/comments=0 是填充非实测)。
    red line:纯候选层过滤,零触 viltrox_fit_score / rule_v0 / 任何评分列。
    """
    if not isinstance(candidate, dict) or not _reach_floor_enabled():
        return ""
    floor = _reach_floor_min_followers()
    followers = _known_numeric(candidate, _REACH_FOLLOWER_KEYS)
    if followers is not None and followers < floor:
        return f"followers_below_floor({int(followers)}<{floor})"
    if candidate.get("fast_path"):
        return ""  # 统计字段是填充 0 非实测,互动判据不适用(不误杀)
    views = _known_numeric(candidate, _REACH_VIEW_KEYS)
    comments = _known_numeric(candidate, _REACH_COMMENT_KEYS)
    if views is None or comments is None:
        return ""  # 任一族字段真缺 → 未知 → 放行(不误杀)
    if views > 0 or comments > 0:
        return ""
    extra = _known_numeric(candidate, _REACH_EXTRA_SIGNAL_KEYS)
    if extra is not None and extra > 0:
        return ""
    return "no_engagement_signal(views=0,comments=0)"


def _below_reach_floor(candidate: dict[str, Any] | None) -> bool:
    """召回触达门槛(bool 契约口):True=应从推荐/发现列表挡掉(数据保留,只挡入口)。"""
    return bool(_reach_floor_reason(candidate))


# facebook 为 opt-in 平台:用户显式选择才参与发现(FB 流量/召回质量一般,做够用的即可),
# 不进 _platforms() 的默认三平台兜底,避免稀释默认轮转结果。
SUPPORTED_DISCOVERY_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook"}


def _is_discovery_garbage(item: dict[str, Any]) -> bool:
    """残废发现项:无真 handle(query-as-handle 修复后兜底为 'Unknown creator'/空)→ 丢弃。"""
    handle = str(item.get("handle") or "").strip()
    name = str(item.get("channel_name") or "").strip()
    return not handle and name.lower() in ("", "unknown creator")


from app.core.coerce import _text


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── 收口路①-4:新人优先「展示排序信号」(全独立,绝不写 viltrox_fit_score / 不改 rule_v0)──────
# 合并库内召回 + 全网发现时,给「新发现(无库内历史匹配)+ 新鲜(低合作)+ 成长期」加权;
# 库内饱和大号(合作过载 / 老面孔大粉)降位。产出的 display_rank 仅作前端展示排序,纯透出。
_DISCOVERY_BRAND_COLLAB_OVERLOAD_N = 6  # 与 profile_recall.BRAND_COLLAB_OVERLOAD_N 同口径
_SATURATED_FOLLOWER_FLOOR = 500_000     # 老面孔大粉(库内已匹配 + 超大粉)视为饱和,降位


def _discovery_brand_collab_count(item: dict[str, Any]) -> int:
    """发现项真实合作条数(historical_match.recent_cooperations / brand_*);缺 → 0(不杜撰)。"""
    history = item.get("historical_match") if isinstance(item.get("historical_match"), dict) else {}
    coops = history.get("recent_cooperations")
    if isinstance(coops, list):
        return sum(1 for c in coops if c not in (None, "", {}, []))
    raw = item.get("brand_collaborations") or item.get("brand_collaborations_json")
    if isinstance(raw, list):
        return sum(1 for c in raw if c not in (None, "", {}, []))
    return 0


def _new_priority_signal(item: dict[str, Any]) -> dict[str, Any]:
    """单个发现候选的「新人优先」展示信号:(display_rank_adjust, flags, note)。

    红线:adjust 只进一个独立展示字段 display_rank,绝不并入任何评分/排名分;数据缺不杜撰。
    """
    adjust = 0.0
    flags: list[str] = []
    notes: list[str] = []
    is_new = not (item.get("history_kol_pool_id") or item.get("historical_match"))
    followers = _int(item.get("follower_count") or item.get("followers"))
    collab = _discovery_brand_collab_count(item)

    if is_new:
        # 全网新发现(库内无此人)= 主源,优先加权。
        adjust += 0.20
        flags.append("新发现")
        notes.append("全网新发现(库内无此人)")
    if collab and collab < 3:
        adjust += 0.05
        flags.append("低合作")
        notes.append(f"合作较少({collab}),新鲜度高")
    # 库内已匹配 + 超大粉 = 老面孔饱和,降位(让位新人)。
    if (item.get("history_kol_pool_id") or item.get("historical_match")) and followers >= _SATURATED_FOLLOWER_FLOOR:
        adjust -= 0.10
        flags.append("饱和大号")
        notes.append(f"库内已合作的大号({followers}+),降位让新人")
    if collab >= _DISCOVERY_BRAND_COLLAB_OVERLOAD_N:
        adjust -= 0.05
        flags.append(f"合作{collab}+")
        notes.append(f"合作过载({collab}),议价/独占性弱")
    return {
        "display_rank_adjust": round(adjust, 4),
        "new_priority_flags": flags,
        "new_priority_note": "；".join(notes) if notes else "",
        "is_new_discovery": bool(is_new),
    }


def _annotate_new_priority(discovery: dict[str, Any] | None) -> dict[str, Any] | None:
    """给 discover_new_creators 结果的 items/new_creators/existing_matches 逐项贴新人优先展示信号。
    仅副本注解(就地补字段),不改顺序、不写库;前端可按 display_rank 重排展示。"""
    if not isinstance(discovery, dict):
        return discovery
    for key in ("items", "new_creators", "existing_matches"):
        seq = discovery.get(key)
        if not isinstance(seq, list):
            continue
        for item in seq:
            if isinstance(item, dict):
                item.update(_new_priority_signal(item))
    discovery["new_priority_signal_applied"] = True
    discovery["new_priority_note"] = (
        "新人优先展示信号:新发现/低合作/成长期加权,库内饱和大号降位;纯展示,绝不写 viltrox_fit_score。"
    )
    return discovery


def _staff_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int(staff.get(key))
        if parsed > 0:
            return parsed
    return None


def _platforms(value: Any, fallback: str = "") -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for raw in raw_values:
        text = _text(raw).lower()
        if text in {"all", "*"}:
            continue
        if text in SUPPORTED_DISCOVERY_PLATFORMS and text not in out:
            out.append(text)
    fallback_text = _text(fallback).lower()
    if not out and fallback_text in SUPPORTED_DISCOVERY_PLATFORMS:
        out.append(fallback_text)
    # 2026-07-02 用户令:未显式选平台时默认三平台齐搜(此前只兜 youtube,
    # 异步 job 路径用户没选就单平台,YT/IG/TT 均匀分布无从谈起)。
    # 注意:facebook 已在 SUPPORTED 集合里但**故意不进默认兜底**——只在用户显式传
    # platforms=["facebook",...] 时参与(FB 质量一般,不稀释默认结果)。
    return out or ["youtube", "instagram", "tiktok"]


def _candidate_key(item: dict[str, Any], platform: str) -> str:
    for key in ("handle", "channel_url", "source_url", "channel_name"):
        value = _text(item.get(key)).lower()
        if value:
            return f"{platform}:{value}"
    return f"{platform}:unknown:{len(str(item))}"
