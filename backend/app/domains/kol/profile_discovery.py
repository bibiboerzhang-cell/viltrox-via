"""New-creator discovery helpers for KOL Pool smart search.

This module reuses the existing platform-search provider from old Discover.
It does not create KOL Pool rows and never touches V6 Fit scoring fields.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import history_match
from app.domains.kol import profile_recall
from app.domains.kol import search_sessions
from app.domains.kol import url_deep_crawl
from app.services.intelligence.account_scan_service import search_platform_content


# P0-6:douyin 代码层硬移除出发现支持平台(不靠 env)。抖音号天然落 {中国大陆} 排除域,
# 且无稳定海外召回价值,故从发现入口剔除;_platforms() 命中 douyin 时直接被过滤掉。
SUPPORTED_DISCOVERY_PLATFORMS = {"youtube", "instagram", "tiktok"}


# P0-6 地区口径:排除 {中国大陆 CN / 香港 HK / 台湾 TW},匹配中文地名 + ISO 码;
# 其余国家(含海外中文博主)放行;空地区放行。发现侧/写入前过滤共用。
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


# ── 区域语言本地化(用户令):选了目标市场就按该区语言搜平台,捞本地达人(本地达人频道/标题是本地语言)。──
# value=(relevanceLanguage ISO-639-1, regionCode);英语区/未列/空 → ("en", market)。CN/HK/TW 为排除域不作目标。
MARKET_LANGUAGE: dict[str, tuple[str, str]] = {
    "JP": ("ja", "JP"), "KR": ("ko", "KR"), "DE": ("de", "DE"), "FR": ("fr", "FR"),
    "ES": ("es", "ES"), "MX": ("es", "MX"), "IT": ("it", "IT"), "BR": ("pt", "BR"),
    "PT": ("pt", "PT"), "RU": ("ru", "RU"), "TH": ("th", "TH"), "VN": ("vi", "VN"),
    "ID": ("id", "ID"), "TR": ("tr", "TR"), "PL": ("pl", "PL"), "NL": ("nl", "NL"),
    "SA": ("ar", "SA"), "AE": ("ar", "AE"),
}
_LANG_DISPLAY = {
    "en": "English",
    "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "tr": "Turkish", "pl": "Polish", "nl": "Dutch", "ar": "Arabic",
}
_LOCALIZE_CACHE: dict[tuple[str, str], str] = {}


def _has_cjk(text: str) -> bool:
    """含中日韩统一表意文字(中文为主)→ 需翻译成目标语言再搜平台。"""
    return any("一" <= ch <= "鿿" for ch in text)


def _market_to_language(market: str) -> tuple[str, str]:
    """市场码 → (relevanceLanguage, regionCode);英语区/未列 → ('en', market)。"""
    m = _text(market).upper()
    return MARKET_LANGUAGE.get(m, ("en", m))


def _localize_search_terms(en_query: str, language: str) -> str:
    """英文 creator 检索词 → 目标语言(保持可搜关键词)。走 llm_gateway 预算闸;同 (query,lang) 进程内
    缓存不重复烧;翻译失败/空 → 回退英文(不阻断)。language 为空/en → 原样返回。"""
    q = _text(en_query)
    lang = _text(language).lower()
    if not q:
        return q
    # 英文/全球市场:query 已是拉丁/英文 → 原样;但若是中文(persona-KB 计划的中文 search_query)→
    # 翻成英文,贯彻「中文输入→英文搜索」(否则中文词又被原样拿去搜平台、捞中文圈)。
    if lang in ("", "en"):
        if not _has_cjk(q):
            return q
        lang = "en"
    key = (q, lang)
    if key in _LOCALIZE_CACHE:
        return _LOCALIZE_CACHE[key]
    localized = q
    try:
        from app.platform import llm_gateway

        lang_name = _LANG_DISPLAY.get(lang, lang)
        prompt = (
            f"Translate these influencer/creator search keywords into {lang_name} for searching "
            f"YouTube/Instagram/TikTok. Return ONLY the translated keywords, space-separated, "
            f"no explanation, no quotes:\n\n{q}"
        )
        resp = llm_gateway.invoke(
            prompt=prompt,
            purpose="vkpi_discovery_localize",
            preferred_provider="openai",
            max_output_tokens=120,
        )
        if str(resp.get("status") or "") == "success":
            text = _text(resp.get("text"))
            if text:
                localized = text
    except Exception:
        localized = q
    _LOCALIZE_CACHE[key] = localized
    return localized


def _is_discovery_garbage(item: dict[str, Any]) -> bool:
    """残废发现项:无真 handle(query-as-handle 修复后兜底为 'Unknown creator'/空)→ 丢弃。"""
    handle = str(item.get("handle") or "").strip()
    name = str(item.get("channel_name") or "").strip()
    return not handle and name.lower() in ("", "unknown creator")


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    return out or ["youtube"]


def _candidate_key(item: dict[str, Any], platform: str) -> str:
    for key in ("handle", "channel_url", "source_url", "channel_name"):
        value = _text(item.get(key)).lower()
        if value:
            return f"{platform}:{value}"
    return f"{platform}:unknown:{len(str(item))}"


def _profile_url_from_kol_pool_id(kol_pool_id: Any) -> str:
    parsed = _int(kol_pool_id)
    if parsed <= 0:
        return ""
    try:
        row = get_conn().execute(
            """
            SELECT profile_url, platform, handle
            FROM vkpi_kol_pool
            WHERE id=?
            """,
            (parsed,),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    data = dict(row)
    profile_url = _text(data.get("profile_url"))
    if profile_url:
        return profile_url
    platform = _text(data.get("platform")).lower()
    handle = _text(data.get("handle")).lstrip("@")
    if not platform or not handle:
        return ""
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    return ""


def _profile_url_from_item(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    for key in ("profile_url", "channel_url", "source_url"):
        value = _text(payload.get(key) or item.get(key))
        if value:
            return value
    platform = _text(payload.get("platform") or item.get("platform")).lower()
    handle = _text(payload.get("handle") or payload.get("channel_name") or item.get("handle"))
    if not platform or not handle:
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    handle = handle.lstrip("@")
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "douyin":
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    safe_limit = max(1, min(_int(limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    return {
        "status": "planned",
        "query": _text(query_text),
        "platforms": resolved_platforms,
        "limit": safe_limit,
        "provider_calls": False,
        "message": "new discovery is planned only; set execute_new_discovery=true to call platform providers",
    }


def profile_crawl_plan_for_session_item(
    *,
    session_id: int,
    item_id: int,
    max_posts: int = 12,
    mode: str = "profile_only",
) -> dict[str, Any]:
    item = search_sessions.get_session_item(int(session_id), int(item_id))
    item_type = _text(item.get("item_type"))
    if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
        raise ValueError("profile crawl can only run for new_creator, existing_kol, or recall_candidate items")
    profile_url = _profile_url_from_item(item)
    if not profile_url:
        raise ValueError("discovery item does not contain a usable profile URL")
    return {
        "status": "planned",
        "session_id": int(session_id),
        "item_id": int(item_id),
        "item_type": item_type,
        "profile_url": profile_url,
        "mode": mode if mode in {"profile_only", "auto", "profile_with_video", "account_deep"} else "profile_only",
        "max_posts": max(1, min(_int(max_posts, 12), 12)),
        "message": "set execute=true to crawl profile basics through the safe writer",
        "viltrox_fit_score_untouched": True,
    }


def execute_profile_crawl_for_session_item(
    *,
    session_id: int,
    item_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or {}
    execute = bool(body.get("execute"))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    plan = profile_crawl_plan_for_session_item(
        session_id=int(session_id),
        item_id=int(item_id),
        max_posts=max_posts,
        mode=mode,
    )
    if not execute:
        return {
            **plan,
            "execute": False,
            "profile_result": url_deep_crawl.dry_run_url_deep_crawl(
                {
                    "url": plan["profile_url"],
                    "execute": False,
                    "mode": mode,
                    "max_posts": max_posts,
                    "representative_video_limit": body.get("representative_video_limit") or 1,
                }
            ),
        }

    profile_result = url_deep_crawl.dry_run_url_deep_crawl(
        {
            "url": plan["profile_url"],
            "execute": True,
            "mode": mode,
            "max_posts": max_posts,
            "representative_video_limit": body.get("representative_video_limit") or 1,
        }
    )
    updated_item = search_sessions.update_item_profile_execution(
        int(session_id),
        int(item_id),
        profile_result=profile_result,
    )
    profile_flow = profile_result.get("profile_flow") if isinstance(profile_result.get("profile_flow"), dict) else {}
    return {
        **plan,
        "execute": True,
        "status": profile_flow.get("status") or profile_result.get("status") or "unknown",
        "kol_pool_id": profile_flow.get("kol_pool_id") or profile_result.get("matched_kol_pool_id"),
        "profile_result": profile_result,
        "updated_item": updated_item,
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
    }


def advance_search_session_items(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan or execute ordered profile crawl for discovery items in a session.

    This is an orchestration helper for the unified KOL input. It advances
    session items one by one through the already-safe profile flow and never
    writes V6 Fit fields directly.
    """

    body = body or {}
    execute = bool(body.get("execute"))
    limit = max(1, min(_int(body.get("limit"), 5), 15))
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    include_completed = bool(body.get("include_completed"))
    item_ids_raw = body.get("item_ids")
    item_ids = {
        _int(value)
        for value in (item_ids_raw if isinstance(item_ids_raw, list) else [])
        if _int(value) > 0
    }
    allowed_types_raw = body.get("item_types")
    allowed_types = {
        _text(value)
        for value in (allowed_types_raw if isinstance(allowed_types_raw, list) else [])
        if _text(value)
    } or {"new_creator", "existing_kol", "recall_candidate"}

    session = search_sessions.get_session(int(session_id))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    terminal_statuses = {"ready", "queued", "running", "already_queued", "already_analyzed"}
    for item in session.get("items") or []:
        item_id = _int(item.get("id"))
        item_type = _text(item.get("item_type"))
        item_status = _text(item.get("status"))
        if item_ids and item_id not in item_ids:
            continue
        if item_type not in allowed_types:
            continue
        if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "unsupported_item_type", "item_type": item_type})
            continue
        if not include_completed and item_status in terminal_statuses:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "already_terminal", "item_status": item_status})
            continue
        profile_url = _profile_url_from_item(item)
        if not profile_url:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "missing_profile_url", "item_status": item_status})
            continue
        candidates.append(item)

    selected = candidates[:limit]
    overflow = max(0, len(candidates) - len(selected))
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {"planned": 0, "executed": 0, "ready": 0, "partial": 0, "failed": 0, "skipped": len(skipped), "errors": 0}
    changed_ids: list[int] = []

    for item in selected:
        item_id = _int(item.get("id"))
        try:
            if not execute:
                plan = profile_crawl_plan_for_session_item(
                    session_id=int(session_id),
                    item_id=item_id,
                    max_posts=max_posts,
                    mode=mode,
                )
                counts["planned"] += 1
                items.append({"item_id": item_id, "status": "planned", "plan": plan})
                continue

            result = execute_profile_crawl_for_session_item(
                session_id=int(session_id),
                item_id=item_id,
                body={**body, "execute": True, "max_posts": max_posts, "mode": mode},
            )
            counts["executed"] += 1
            status = _text(result.get("status")).lower() or "unknown"
            if status == "ready":
                counts["ready"] += 1
            elif status in {"failed", "crawl_failed", "profile_crawl_failed"} or "failed" in status:
                counts["failed"] += 1
            else:
                counts["partial"] += 1
            for changed_id in result.get("viltrox_fit_score_changed_ids") or []:
                parsed = _int(changed_id)
                if parsed > 0 and parsed not in changed_ids:
                    changed_ids.append(parsed)
            items.append({"item_id": item_id, "status": status, "result": result})
        except Exception as exc:
            counts["errors"] += 1
            items.append({"item_id": item_id, "status": "error", "reason": str(exc)[:500]})

    skipped.extend(
        {
            "item_id": _int(item.get("id")),
            "status": "skipped",
            "reason": "over_limit",
            "item_status": _text(item.get("status")),
        }
        for item in candidates[limit:]
    )
    counts["skipped"] = len(skipped)

    batch_status = "planned"
    if execute:
        if counts["failed"] or counts["errors"]:
            batch_status = "partial" if counts["ready"] or counts["partial"] else "failed"
        else:
            batch_status = "ready"
        search_sessions.update_session_result_summary(
            int(session_id),
            status=batch_status,
            summary_patch={
                "profile_batch_advance": {
                    "status": batch_status,
                    "mode": mode,
                    "limit": limit,
                    "selected": len(selected),
                    "overflow": overflow,
                    "counts": counts,
                    "viltrox_fit_score_changed_ids": changed_ids,
                    "viltrox_fit_score_untouched": not changed_ids,
                }
            },
        )

    return {
        "status": batch_status,
        "execute": execute,
        "session_id": int(session_id),
        "mode": mode,
        "limit": limit,
        "selected": len(selected),
        "eligible": len(candidates),
        "overflow": overflow,
        "counts": counts,
        "items": items,
        "skipped": skipped[: max(0, 50 - len(items))],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
        "provider_calls_performed": execute and bool(selected),
        "write_db": execute and bool(selected),
        "writes": ["vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"] if execute and selected else [],
    }


def enqueue_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue ordered session advancement on apify_jobs for provider-safe pacing."""

    body = body or {}
    session_id = int(session_id)
    plan = advance_search_session_items(
        session_id=session_id,
        body={**body, "execute": False},
    )
    if plan.get("selected", 0) <= 0:
        return {
            "status": "nothing_to_queue",
            "session_id": session_id,
            "plan": plan,
            "provider_calls_performed": False,
            "write_db": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    conn = get_conn()
    existing = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE job_type IN ('session_advance', 'smart_search_profile_advance')
          AND status IN ('queued', 'running')
          AND payload->>'search_session_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(session_id),),
    ).fetchone()
    if existing:
        existing_job = dict(existing)
        queued_items: dict[str, Any] | None = None
        if _text(existing_job.get("status")).lower() == "queued":
            queued_items = search_sessions.mark_items_profile_queued(
                session_id,
                item_ids=[_int(item.get("item_id")) for item in plan.get("items") or []],
                job_id=_int(existing_job.get("id")),
                reason="session_advance_already_queued",
                plan_items=plan.get("items") or [],
            )
        return {
            "status": "already_queued",
            "session_id": session_id,
            "job": existing_job,
            "queued_items": queued_items,
            "plan": plan,
            "provider_calls_performed": False,
            "write_db": bool(queued_items and queued_items.get("updated_count")),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    triggered_by_user_id = _staff_user_id(staff)
    payload = {
        "target_type": "search_session",
        "target_id": str(session_id),
        "derive_method": "kol_session_profile_advance",
        "search_session_id": session_id,
        "mode": plan.get("mode"),
        "limit": plan.get("limit"),
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "item_types": body.get("item_types"),
        "item_ids": body.get("item_ids"),
        "include_completed": bool(body.get("include_completed")),
        "representative_video_limit": body.get("representative_video_limit"),
        "prompt": f"profile crawl advance session:{session_id}",
        "summary": f"profile crawl advance · session {session_id}",
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
    }
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('session_advance', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (search_sessions._json_dumps(payload),),
    ).fetchone()
    conn.commit()
    job = dict(row) if row else {}
    queued_items = search_sessions.mark_items_profile_queued(
        session_id,
        item_ids=[_int(item.get("item_id")) for item in plan.get("items") or []],
        job_id=_int(job.get("id")),
        reason="session_advance_queued",
        plan_items=plan.get("items") or [],
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "profile_batch_advance_job": {
                "status": "queued",
                "job_id": job.get("id"),
                "selected": plan.get("selected"),
                "eligible": plan.get("eligible"),
                "overflow": plan.get("overflow"),
                "queued_items": queued_items.get("updated_count"),
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued",
        "session_id": session_id,
        "job": job,
        "queued_items": queued_items,
        "plan": plan,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["apify_jobs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def cancel_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Block queued session-advance jobs without interrupting running provider work."""

    del staff
    body = body or {}
    session_id = int(session_id)
    reason = _text(body.get("reason") or "session_advance_cancelled_by_user")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE job_type IN ('session_advance', 'smart_search_profile_advance')
          AND status IN ('queued', 'running')
          AND payload->>'search_session_id'=?
        ORDER BY created_at DESC, id DESC
        """,
        (str(session_id),),
    ).fetchall()
    jobs = [dict(row) for row in rows]
    queued_jobs = [job for job in jobs if _text(job.get("status")).lower() == "queued"]
    running_jobs = [job for job in jobs if _text(job.get("status")).lower() == "running"]
    if not queued_jobs:
        search_sessions.update_session_result_summary(
            session_id,
            status="running" if running_jobs else "ready",
            summary_patch={
                "profile_batch_advance_job": {
                    "status": "running_not_cancelled" if running_jobs else "no_queued_job",
                    "running_job_ids": [job.get("id") for job in running_jobs],
                    "viltrox_fit_score_untouched": True,
                },
                "smart_search_profile_advance_job": {
                    "status": "running_not_cancelled" if any(_text(job.get("job_type")) == "smart_search_profile_advance" for job in running_jobs) else "no_queued_job",
                    "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        return {
            "status": "running_not_cancelled" if running_jobs else "no_queued_job",
            "session_id": session_id,
            "cancelled_jobs": [],
            "running_jobs": running_jobs,
            "provider_calls_performed": False,
            "write_db": bool(running_jobs),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    cancelled_job_ids = [_int(job.get("id")) for job in queued_jobs if _int(job.get("id")) > 0]
    for job in queued_jobs:
        payload = job.get("payload")
        payload_dict = search_sessions._loads(payload, {}) if not isinstance(payload, dict) else payload
        if not isinstance(payload_dict, dict):
            payload_dict = {}
        payload_dict = {**payload_dict, "session_advance_cancelled": {"status": "cancelled", "reason": reason}}
        conn.execute(
            """
            UPDATE apify_jobs
            SET status='blocked',
                last_error=?,
                payload=?::jsonb,
                updated_at=NOW()
            WHERE id=? AND status='queued'
            """,
            (reason[:2000], search_sessions._json_dumps(payload_dict), int(job["id"])),
        )
    conn.commit()
    cancelled_items = search_sessions.mark_items_profile_cancelled(
        session_id,
        job_ids=cancelled_job_ids,
        reason=reason,
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running" if running_jobs else "partial",
        summary_patch={
            "profile_batch_advance_job": {
                "status": "cancelled" if not running_jobs else "partial_cancelled_running_remains",
                "cancelled_job_ids": [job.get("id") for job in queued_jobs if _text(job.get("job_type")) == "session_advance"],
                "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "session_advance"],
                "cancelled_items": cancelled_items.get("updated_count"),
                "reason": reason,
                "viltrox_fit_score_untouched": True,
            },
            "smart_search_profile_advance_job": {
                "status": "cancelled" if any(_text(job.get("job_type")) == "smart_search_profile_advance" for job in queued_jobs) else "not_cancelled",
                "cancelled_job_ids": [job.get("id") for job in queued_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
                "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
                "reason": reason,
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "cancelled" if not running_jobs else "partial_cancelled_running_remains",
        "session_id": session_id,
        "cancelled_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "cancelled_items": cancelled_items,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["apify_jobs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def enqueue_smart_search_profile_advance(
    *,
    query_text: str,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue the full text-search -> discovery -> profile-advance pipeline."""

    body = body or {}
    query = _text(query_text)
    if not query:
        raise ValueError("query_text is required")
    session = search_sessions.ensure_session_for_result(
        session_id=None,
        create=True,
        query_text=query,
        query_type="text_recall",
        source=_text(body.get("source") or "kol_smart_search_profile_pipeline"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if not session:
        raise RuntimeError("smart search session was not created")
    session_id = int(session["id"])
    triggered_by_user_id = _staff_user_id(staff)
    payload = {
        "target_type": "search_session",
        "target_id": str(session_id),
        "derive_method": "kol_smart_search_profile_advance",
        "search_session_id": session_id,
        "query_text": query,
        "product_sku": _text(body.get("product_sku")),
        # why-fit 人群侧上下文(纯展示透传):planner 计划里的产品人群,worker 跑 recall 时拼"适合理由"。
        "product_focus": (body.get("llm_query_plan") or {}).get("product_focus") if isinstance(body.get("llm_query_plan"), dict) else None,
        "target_persona": (body.get("llm_query_plan") or {}).get("target_persona") if isinstance(body.get("llm_query_plan"), dict) else "",
        "candidate_limit": max(1, min(_int(body.get("candidate_limit"), 100), 500)),
        "limit": max(1, min(_int(body.get("limit"), 30), 50)),
        "creator_quota": max(0, min(_int(body.get("creator_quota"), 15), 50)),
        "reviewer_quota": max(0, min(_int(body.get("reviewer_quota"), 15), 50)),
        "ratio_policy": _text(body.get("ratio_policy") or "soft"),
        "mixed_policy": _text(body.get("mixed_policy") or "dominant"),
        "dedupe": bool(body.get("dedupe", True)),
        "vector_weight": body.get("vector_weight") if body.get("vector_weight") is not None else 0.7,
        "type_weight": body.get("type_weight") if body.get("type_weight") is not None else 0.3,
        "type_boost_enabled": bool(body.get("type_boost_enabled", True)),
        "include_new_discovery": bool(body.get("include_new_discovery", True)),
        # 收口路①-2:内容契合入队控量旋钮(默认开,top N=6);worker→pipeline 透传。
        "include_content_fit": bool(body.get("include_content_fit", True)),
        "content_fit_top_n": max(1, min(_int(body.get("content_fit_top_n"), 6), 12)),
        "new_discovery_limit": max(1, min(_int(body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_per_platform_limit": max(1, min(_int(body.get("new_discovery_per_platform_limit") or body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_platforms": body.get("new_discovery_platforms") or body.get("discovery_platforms"),
        "platform": _text(body.get("platform")),
        "market": _text(body.get("market") or body.get("country")),
        "advance_limit": max(1, min(_int(body.get("advance_limit") or body.get("profile_advance_limit"), 15), 15)),
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "advance_mode": _text(body.get("advance_mode") or body.get("mode") or "account_deep"),
        "representative_video_limit": body.get("representative_video_limit"),
        "item_types": body.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
        "include_completed": bool(body.get("include_completed")),
        "prompt": f"smart profile advance · {query[:120]}",
        "summary": f"smart profile advance · {query[:80]}",
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
    }
    conn = get_conn()
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('smart_search_profile_advance', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (search_sessions._json_dumps(payload),),
    ).fetchone()
    conn.commit()
    job = dict(row) if row else {}
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "smart_search_profile_advance_job": {
                "status": "queued",
                "job_id": job.get("id"),
                "query_text": query,
                "include_new_discovery": payload["include_new_discovery"],
                "advance_limit": payload["advance_limit"],
                "advance_mode": payload["advance_mode"],
                "representative_video_limit": payload["representative_video_limit"] or 1,
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued",
        "session_id": session_id,
        "search_session": search_sessions.get_session(session_id),
        "job": job,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "apify_jobs"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

    query = _text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    recall_result = profile_recall.recall_kol_profiles(
        query_text=query,
        product_sku=_text(payload.get("product_sku")),
        candidate_limit=max(1, min(_int(payload.get("candidate_limit"), 100), 500)),
        limit=max(1, min(_int(payload.get("limit"), 30), 50)),
        creator_quota=max(0, min(_int(payload.get("creator_quota"), 15), 50)),
        reviewer_quota=max(0, min(_int(payload.get("reviewer_quota"), 15), 50)),
        ratio_policy=_text(payload.get("ratio_policy") or "soft"),
        mixed_policy=_text(payload.get("mixed_policy") or "dominant"),
        dedupe=bool(payload.get("dedupe", True)),
        vector_weight=float(payload.get("vector_weight") if payload.get("vector_weight") is not None else 0.7),
        type_weight=float(payload.get("type_weight") if payload.get("type_weight") is not None else 0.3),
        type_boost_enabled=bool(payload.get("type_boost_enabled", True)),
        exclude_chinese=bool(payload.get("exclude_chinese", True)),
        product_focus=payload.get("product_focus"),
        target_persona=_text(payload.get("target_persona")),
    )
    recall_session = search_sessions.attach_recall_result(int(session_id), recall_result)
    new_discovery: dict[str, Any] | None = None
    if bool(payload.get("include_new_discovery", True)):
        # persona 检索词原料:payload 有 product_focus/target_persona(来自 llm_query_plan);
        # verticals/ideal_creator_types/avoid_types 不在 payload → 用 product_sku 实时兜底取(只读 KB,零 LLM)。
        _persona_kb: dict[str, Any] = {}
        _sku = _text(payload.get("product_sku"))
        if _sku:
            try:
                from app.domains.costs import product_persona as _product_persona_kb
                _persona_kb = _product_persona_kb.get_product_persona(_sku) or {}
            except Exception:
                _persona_kb = {}
        new_discovery = await discover_new_creators(
            query_text=query,
            platforms=payload.get("new_discovery_platforms") or payload.get("discovery_platforms"),
            platform_hint=_text(payload.get("platform")),
            market=_text(payload.get("market") or payload.get("country")),
            limit=max(1, min(_int(payload.get("new_discovery_limit"), 15), 50)),
            per_platform_limit=max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 15), 50)),
            search_query_en=query,  # pipeline 入参 query 已是 effective_query(planner 英文 search_query;失效退 rule_v0 英文兜底)
            product_focus=payload.get("product_focus"),
            ideal_creator_types=_persona_kb.get("ideal_creator_types_json"),
            verticals=_persona_kb.get("verticals_json"),
            avoid_types=_persona_kb.get("avoid_types_json"),
            target_persona=_text(payload.get("target_persona")),
        )
        # 收口路①-4:新人优先展示信号(新发现/低合作/成长期加权,饱和大号降位)。纯展示透出,
        # 绝不写 viltrox_fit_score / 不改 rule_v0;注解后再 attach(库内召回的 display_rank_score 已在 recall 侧产出)。
        new_discovery = _annotate_new_priority(new_discovery)
        search_sessions.attach_new_discovery_result(int(session_id), new_discovery)

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        body={
            **payload,
            "execute": True,
            "limit": max(1, min(_int(payload.get("advance_limit") or payload.get("profile_advance_limit"), 15), 15)),
            "max_posts": max(1, min(_int(payload.get("max_posts"), 12), 12)),
            "mode": _text(payload.get("advance_mode") or payload.get("mode") or "account_deep"),
            "item_types": payload.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
            "include_completed": bool(payload.get("include_completed")),
        },
    )
    changed_ids = [
        _int(value)
        for value in (advance_result.get("viltrox_fit_score_changed_ids") or [])
        if _int(value) > 0
    ]
    # 收口路①-2:搜索拿到候选(库内召回 + 发现 + advance 补全)后,对**头部 N 个有视频证据的
    # 库内候选**异步入队内容契合深析(「思考中」段)。控量(top N + 有证据 + cache 复用 + 去重)。
    # 纯编排入队 + exposure_potential 展示计算,零烧 LLM、零写 fit。入队失败不阻断 pipeline。
    content_fit: dict[str, Any] | None = None
    if bool(payload.get("include_content_fit", True)):
        try:
            from app.domains.kol import content_fit_enqueue

            content_fit = content_fit_enqueue.enqueue_content_fit_for_session(
                session_id=int(session_id),
                product_sku=_text(payload.get("product_sku")),
                top_n=max(1, min(_int(payload.get("content_fit_top_n"), content_fit_enqueue.DEFAULT_TOP_N), content_fit_enqueue.MAX_TOP_N)),
                triggered_by_user_id=_int(payload.get("triggered_by_user_id")) or None,
            )
        except Exception as exc:
            content_fit = {"status": "error", "reason": str(exc)[:300]}
    pipeline_status = "failed" if advance_result.get("status") == "failed" else "ready"
    search_sessions.update_session_result_summary(
        int(session_id),
        status="partial" if changed_ids or advance_result.get("status") == "partial" else pipeline_status,
        summary_patch={
            "smart_search_profile_advance_job": {
                "status": pipeline_status,
                "query_text": query,
                "recall_returned": len(recall_result.get("items") or []),
                "new_discovery_status": (new_discovery or {}).get("status") if new_discovery else "not_requested",
                "advance_status": advance_result.get("status"),
                "advance_counts": advance_result.get("counts"),
                # 内容契合入队状态(「思考中」桶进度):入队数 / 跳过原因,纯展示透出。
                "content_fit_status": (content_fit or {}).get("status") if content_fit else "not_requested",
                "content_fit_enqueued": (content_fit or {}).get("enqueued_count") if content_fit else 0,
                "viltrox_fit_score_changed_ids": changed_ids,
                "viltrox_fit_score_untouched": not changed_ids,
            }
        },
    )
    return {
        "status": pipeline_status,
        "session_id": int(session_id),
        "query": query,
        "content_fit": content_fit,
        "recall": {
            "method": recall_result.get("method"),
            "returned_count": len(recall_result.get("items") or []),
            "diagnostics": recall_result.get("diagnostics"),
            "search_session": recall_session,
        },
        "new_discovery": new_discovery,
        "advance": advance_result,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items", "vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs"],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }


async def discover_new_creators(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    market: str = "",
    limit: int = 15,
    per_platform_limit: int = 15,
    search_query_en: str = "",
    product_focus: Any = None,
    ideal_creator_types: Any = None,
    verticals: Any = None,
    avoid_types: Any = None,
    target_persona: str = "",
) -> dict[str, Any]:
    """Search platforms for creator candidates and mark existing KOL matches.

    发现精准修:search_query_en(英文平台检索词,优先于中文 query_text 用于实际平台搜索,治
    中文 query 捞中文圈);product_focus/ideal_creator_types/verticals/avoid_types 供 per-item
    persona 启发式相关度打分(纯本地字符串比对,零 LLM/零 Apify,无需预算闸)。全 default,既有调用不破坏。"""
    query = _text(search_query_en) or _text(query_text)
    # 区域语言本地化:目标市场非英语区 → 英文检索词翻成该区语言搜平台(捞本地达人),relevanceLanguage 同步。
    # 英语区/空 market → search_term=query、relevance_language='en',与现状完全一致(零回归)。
    _relevance_language, _region_code = _market_to_language(market)
    search_term = _localize_search_terms(query, _relevance_language)
    safe_limit = max(1, min(_int(limit, 15), 50))
    safe_per_platform = max(1, min(_int(per_platform_limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    if not query:
        return {
            "status": "invalid_query",
            "query": query,
            "platforms": resolved_platforms,
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "query is required",
        }

    new_creators: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []  # 全部通过去重/garbage/地区过滤的存活候选,待 relevance 排序后再 top-N 截断
    existing_matches: list[dict[str, Any]] = []
    platform_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    # persona 检索词原料(英文优先;avoid 命中重扣)。helper 内做泛词过滤与归一,空表零影响。
    _pos_terms = _persona_positive_terms(product_focus, ideal_creator_types, verticals, search_query_en or query_text)
    _neg_terms = _persona_avoid_terms(avoid_types)
    errors: list[dict[str, Any]] = []

    async def _search_one_platform(platform: str) -> dict[str, Any]:
        """Run one platform search with error isolation; returns annotated items + meta.

        每平台并发(asyncio.gather)。单平台失败在此捕获、绝不传播,其余平台照常返回。
        """
        try:
            result = await search_platform_content(
                platform,
                search_term,
                market=_text(market).upper(),
                max_results=safe_per_platform,
                relevance_language=_relevance_language,
            )
        except Exception as exc:
            return {"platform": platform, "status": "failed", "message": str(exc)[:500], "annotated": [], "error": True}
        raw_items = [dict(item or {}) for item in (result.get("items") or [])]
        annotated = history_match.annotate_platform_items(raw_items, platform=platform)
        return {
            "platform": platform,
            "status": result.get("status"),
            "message": result.get("message"),
            "metadata": result.get("metadata") or {},
            "annotated": annotated,
            "error": result.get("status") not in {"done", "ready"} and not annotated,
        }

    # 并行化:YT/IG/TikTok 同时发,替代旧串行 for(一个接一个 await)。
    # return_exceptions=True 双保险——_search_one_platform 已内部捕获,这里再兜一层防御。
    platform_outcomes = await asyncio.gather(
        *[_search_one_platform(platform) for platform in resolved_platforms],
        return_exceptions=True,
    )

    # 按 resolved_platforms 原顺序合并(gather 保序),保留确定性的去重/limit 语义。
    for platform, outcome in zip(resolved_platforms, platform_outcomes):
        if isinstance(outcome, BaseException):
            errors.append({"platform": platform, "status": "failed", "message": str(outcome)[:500]})
            platform_results.append({"platform": platform, "status": "failed", "returned": 0, "metadata": {}, "message": str(outcome)[:500]})
            continue
        annotated = outcome.get("annotated") or []
        platform_results.append(
            {
                "platform": platform,
                "status": outcome.get("status"),
                "returned": len(annotated),
                "metadata": outcome.get("metadata") or {},
                "message": outcome.get("message"),
            }
        )
        if outcome.get("error"):
            errors.append({"platform": platform, "status": outcome.get("status"), "message": outcome.get("message")})
        for item in annotated:
            key = _candidate_key(item, platform)
            if key in seen:
                continue
            seen.add(key)
            if item.get("historical_match") or item.get("history_kol_pool_id"):
                existing_matches.append(item)
                continue
            # P0-6 修:地区排除判据改扫**真正带地区信号的文本字段**(sample_title/channel_name/handle);
            # 发现 item 无 per-item country、market 恒=搜索市场 US,旧判据三参全空 → 形同虚设。
            # 口径保留海外华人(马六甲=马来西亚/新加坡/海外华人摄影师不排,只排 CN大陆/HK/TW 强信号)。
            _region = _detect_excluded_region(item)
            if _is_discovery_garbage(item) or _region:
                continue
            # persona 启发式相关度(纯本地零 LLM):写 item['score']/relevance_score/relevance_tier;
            # 先全收集到 survivors,循环外按 relevance 降序排序再 top-N 截断(否则按到达顺序砍掉高相关项)。
            item.update(_persona_relevance(item, pos_terms=_pos_terms, neg_terms=_neg_terms))
            survivors.append(item)

    # relevance 降序排序 → top-N 截断。red line:relevance 是独立展示信号,绝不并入 viltrox_fit_score / rule_v0。
    survivors.sort(key=lambda it: float(it.get("relevance_score") or 0.0), reverse=True)
    for item in survivors:
        if len(new_creators) >= safe_limit:
            break
        new_creators.append(item)

    status = "ready" if new_creators or existing_matches else "empty"
    if errors and (new_creators or existing_matches):
        status = "partial"
    elif errors:
        status = "failed"
    return {
        "status": status,
        "query": query,
        "platforms": resolved_platforms,
        "market": _text(market).upper(),
        "limit": safe_limit,
        "per_platform_limit": safe_per_platform,
        "items": [*existing_matches, *new_creators],
        "new_creators": new_creators,
        "existing_matches": existing_matches,
        "counts": {
            "new_creators": len(new_creators),
            "existing_matches": len(existing_matches),
            "platforms": len(resolved_platforms),
            "errors": len(errors),
        },
        "platform_results": platform_results,
        "errors": errors,
        "provider_calls": True,
    }
