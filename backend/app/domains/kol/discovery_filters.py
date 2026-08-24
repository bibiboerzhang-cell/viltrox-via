"""KOL 发现过滤器(从 profile_discovery.py 抽出,行为不变)。

纯函数+常量:地区排除(CN/HK/TW)/persona 相关度/相机信号闸。零 LLM/零 IO/零 Apify。
被 profile_discovery re-export 回灌,调用点不变。红线:纯过滤,零触 viltrox_fit_score。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.domains.kol.discovery_account_identity import (
    brand_and_official_share_identity_field,
    exact_brand_handle_confirmed,
    explicit_official_marker,
)
from app.domains.kol.discovery_regional_official import (
    regional_brand_profile_self_attributed,
)


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
    # bio 为 K2 富化新增(频道简介/IG biography 常带真地区信号);无 bio 的 item 行为不变。
    blob = " ".join(str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle", "bio"))
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
    # 只看候选**自身内容**(标题/频道名/handle/bio);绝不含 search_query —— 那是查询词本身,会自命中致全 1.0。
    # bio 为 K2 富化新增(频道简介/IG biography),真摄影师的自述是最诚实的相关度证据;无 bio 行为不变。
    blob = " ".join(
        str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle", "bio")
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

# ── 竞品品牌官号闸(2026-07-21,FEELWORLD 官号混入推荐案)────────────────────────────
# 词表真源:competitor_brands.json(load_competitor_brands,「友商扩」那刀建的唯一竞品词表),
# 绝不另建第二份品牌清单。判据 = 品牌词命中**身份字段**(handle/channel_name/display_name,
# 绝不扫 sample_title/bio 的品牌词——「我评测了 FEELWORLD 监视器」是正常达人,不许误杀)
# **并发**官号信号(handle/名称含 official / handle 即品牌名 / bio 品牌自称口吻)才拦。
# red line:纯候选层 FILTER(只丢 + 诚实计数),零触任何评分字段/评分公式。
# 企业自述口吻词组(词表官号信号 + 动态官号判据共用)。分两档:
# 强口吻=几乎只出现在品牌官号 bio(个人创作者不会自称 official store/manufacturer/warranty);
# 弱口吻=偏企业但真人双人组也可能用("we are a husband and wife photography team"),
# 单独出现绝不定罪(防误杀第一)。"manufacture" 子串同时覆盖 manufacturer/manufactures/manufacturing。
_CORPORATE_VOICE_STRONG_PATTERNS = (
    "official channel", "official account", "official page", "official store",
    "official website", "official site", "official youtube", "official instagram",
    "official tiktok", "official facebook",
    "established in", "founded in", "manufacture", "distributor",
    "our products", "our brand", "customer service", "after-sales",
    "warranty", "authorized dealer", "flagship store",
)
_CORPORATE_VOICE_WEAK_PATTERNS = (
    "we are a", "we are the", "our team", "our mission", "we provide",
    "we offer", "we specialize", "worldwide shipping", "free shipping",
)
# 词表路径沿用全量口吻词组(品牌词已命中身份字段,任一口吻即官号信号)——原十词组全被覆盖,行为超集。
_BRAND_SELF_VOICE_PATTERNS = _CORPORATE_VOICE_STRONG_PATTERNS + _CORPORATE_VOICE_WEAK_PATTERNS
# 官号 handle 常见后缀(品牌名 + 后缀 = 官号强信号;短品牌名靠这份等值表防误杀,如
# sonyofficial 拦、sonya_official(真人 Sonya)放行)。
_BRAND_HANDLE_SUFFIXES = ("", "official", "lofficial", "global", "usa", "us", "uk", "eu", "hq")

def _competitor_brand_terms() -> dict[str, list[str]]:
    """竞品品牌词表(brand → keywords),lazy 复用 competitor_brands.json;失败回空表=闸不生效(fail-open)。"""
    try:
        from app.domains.kol.competitor_text import load_competitor_brands

        return {brand: list(cfg.get("keywords") or []) for brand, cfg in load_competitor_brands().items()}
    except Exception as exc:
        logging.getLogger("viltrox.discovery_filters").debug("竞品词表加载失败,官号闸 fail-open: %s", exc)
        return {}


def _brand_identity_hit(item: dict[str, Any], brand: str, keywords: list[str]) -> bool:
    """品牌词是否命中候选**身份字段**(handle/channel_name/display_name/username)。

    两种命中形态:① 词边界命中(handle 带分隔符,如 feelworld.uk / tamron_europe);
    ② 粘连 handle 命中(feelworldlofficial):归一 handle 含品牌名——仅品牌名 ≥5 字符走子串
    (防 sonya 误命中 sony),短品牌名只认「品牌名+官号后缀」等值。纯函数零 IO。"""
    from app.domains.kol.competitor_text import _keyword_match

    identity = " ".join(
        str(item.get(k) or "") for k in ("handle", "channel_name", "display_name", "username")
    ).lower()
    if not identity.strip():
        return False
    handle_norm = re.sub(r"[^a-z0-9]", "", str(item.get("handle") or "").lower())
    brand_norm = re.sub(r"[^a-z0-9]", "", brand)
    for term in dict.fromkeys([brand, *keywords]):
        term_text = str(term or "").strip().lower().lstrip("@")
        if not term_text:
            continue
        if _keyword_match(identity, term_text):
            return True
    if handle_norm and brand_norm:
        if len(brand_norm) >= 5 and brand_norm in handle_norm:
            return True
        if any(handle_norm == f"{brand_norm}{suffix}" for suffix in _BRAND_HANDLE_SUFFIXES):
            return True
    return False


def _competitor_brand_official(
    item: dict[str, Any],
    *,
    competitor_brands: dict[str, list[str]] | None = None,
) -> str:
    """竞品品牌官号判据:品牌词命中身份字段 **并且** 官号信号并发才拦,返回命中品牌名(未命中 "")。

    handle==品牌名(归一等值)本身即官号信号;仅 sample_title/bio 提到品牌的正常达人绝不命中。"""
    if not isinstance(item, dict):
        return ""
    brands = competitor_brands if competitor_brands is not None else _competitor_brand_terms()
    if not brands:
        return ""
    handle_norm = re.sub(r"[^a-z0-9]", "", str(item.get("handle") or "").lower())
    for brand in brands:
        brand_norm = re.sub(r"[^a-z0-9]", "", brand)
        if (
            handle_norm
            and brand_norm
            and handle_norm == brand_norm
            and exact_brand_handle_confirmed(item, brand_norm)
        ):
            return brand
    bio = str(item.get("bio") or item.get("description") or "").strip().lower()
    corporate_voice = _corporate_voice_bio(bio)
    personal_voice = bool(_PERSONAL_VOICE_RE.search(bio))
    # After the exact-handle shortcut above, the remaining lexicon path can
    # only convict through either a corporate self-description or an explicit
    # affirmative ``official`` marker co-located with the brand identity.
    # Most Pool rows have neither.  Fail-open here before scanning every brand
    # keyword; this preserves the old verdict while removing the dominant
    # O(pool_rows × brand_terms) cold-read cost.  Personal voice is an explicit
    # rebuttal in the existing contract unless the exact-handle proof already
    # returned above.
    if personal_voice and not corporate_voice:
        return ""
    identity_has_official = any(
        explicit_official_marker(item.get(field))
        for field in ("handle", "channel_name", "display_name", "username")
    )
    regional_self_attribution_candidate = bool(
        re.search(r"\bby\s+[a-z0-9]", bio) and re.search(r"\bour\b", bio)
    )
    if not corporate_voice and not identity_has_official and not regional_self_attribution_candidate:
        return ""
    for brand, keywords in brands.items():
        if not _brand_identity_hit(item, brand, keywords):
            continue
        if regional_self_attribution_candidate and regional_brand_profile_self_attributed(
            item, brand,
        ):
            return brand
        # A corporate self-description is independent ownership evidence, so
        # it may corroborate a brand hit from any identity field.
        if corporate_voice:
            return brand
        # Personal first-person language rebuts an otherwise ambiguous
        # ``official`` marker.  Fail open: the discovery wall should only drop
        # accounts whose brand ownership is actually established.
        if personal_voice:
            continue
        # Without a corporate bio, brand and ``official`` must be co-located in
        # the same identity field.  Never combine a creator's personal handle
        # with a reviewed brand mentioned in their display name.
        if brand_and_official_share_identity_field(
            item,
            brand_match=lambda identity: _brand_identity_hit(identity, brand, keywords),
        ):
            return brand
    return ""


# ── 动态品牌官号判据(2026-07-21,Panavision/DZOFilm/Thypoch 漏网案,用户裁决:不扩静态词表)──
# 词表路径(_competitor_brand_official)保留为高精度快路;这条**零词表依赖**,专兜没进
# competitor_brands.json 的品牌官号。判据 = 官号形态信号(handle/名称含 official/区域后缀/
# 品牌式名称)**并发** bio 企业自述口吻(强口吻 ≥1 或 ≥2,见 _corporate_voice_bio)才拦。
# 防误杀第一优先:「个人名+official」真人创作者(yendrycayo_official/kunal_malhotraofficial 型)
# bio 是个人口吻 → 口吻判据不命中 → 放行;bio 缺失/太短 = 证据不足 → 放行(词表路径仍兜高频竞品)。
# red line:纯候选层 FILTER(只丢 + 诚实计数),零触任何评分字段/评分公式/规则引擎。
_CORPORATE_VOICE_MIN_BIO_LEN = 20  # bio 短于此 = 证据不足,动态路径直接放行
# 个人口吻信号:第一人称单数(i / i'm / my)几乎只出现在真人 bio;品牌官号自称 we/our/us。
_PERSONAL_VOICE_RE = re.compile(r"\b(?:i|i['’]?m|my)\b")
# handle 尾 token 的官号/区域后缀(带分隔符才认,如 tamron_europe / fujifilmx_us;
# 防 markus 这类以 us 结尾的真人名误命中)。
_OFFICIAL_FORM_SUFFIX_TOKENS = frozenset({
    "official", "global", "hq", "team", "store", "shop",
    "usa", "us", "uk", "eu", "europe", "asia", "japan", "india",
})
# 品牌式名称(SmallHD/FEELWORLD/DZOFilm 型,非「Firstname Lastname」人名形态):
# 全大写词 ≥4 字符 / 词中驼峰 / 连续大写接小写。判宽无妨——形态只是半条腿,bio 口吻才定罪。
_BRAND_STYLE_NAME_RE = re.compile(r"\b[A-Z]{4,}\b|[a-z][A-Z]|\b[A-Z]{2,}[a-z]")


def _corporate_voice_bio(bio: Any) -> bool:
    """bio 是否为品牌自述口吻(corporate voice)。防误杀第一的判规:

    - 强口吻 ≥2 → 定罪(FEELWORLD 实况:"Official Channel"+"established in 2011");
    - 强口吻 =1 且无第一人称个人口吻 → 定罪;
    - 弱口吻 ≥3 且无个人口吻 → 定罪(单独 "we are a" 绝不定罪——真人双人组也这么写);
    - 其余(含 bio 太短)→ 放行。纯函数零 IO。"""
    text = str(bio or "").strip().lower()
    if len(text) < _CORPORATE_VOICE_MIN_BIO_LEN:
        return False
    strong = sum(1 for p in _CORPORATE_VOICE_STRONG_PATTERNS if p in text)
    if strong >= 2:
        return True
    if _PERSONAL_VOICE_RE.search(text):
        return False
    if strong >= 1:
        return True
    return sum(1 for p in _CORPORATE_VOICE_WEAK_PATTERNS if p in text) >= 3


def _official_form_signal(item: dict[str, Any]) -> bool:
    """官号形态信号(动态判据的半条腿,判宽无妨——另半条腿 bio 口吻才定罪):
    ① handle/名称含 official/global;② handle 尾 token 是官号/区域后缀(带分隔符);
    ③ 归一 handle 以 hq 结尾;④ 名称为品牌式非人名(全大写/驼峰)。纯函数零 IO。"""
    identity = " ".join(
        str(item.get(k) or "") for k in ("handle", "channel_name", "display_name", "username")
    ).lower()
    if not identity.strip():
        return False
    if "official" in identity or "global" in identity:
        return True
    handle = str(item.get("handle") or "").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", handle) if t]
    if len(tokens) >= 2 and tokens[-1] in _OFFICIAL_FORM_SUFFIX_TOKENS:
        return True
    if re.sub(r"[^a-z0-9]", "", handle).endswith("hq"):
        return True
    raw_name = str(item.get("channel_name") or item.get("display_name") or "")
    return bool(_BRAND_STYLE_NAME_RE.search(raw_name))


def _dynamic_brand_official(item: dict[str, Any]) -> bool:
    """动态品牌官号判据(零词表依赖):官号形态信号 **并发** bio 企业自述口吻才拦。

    bio 缺失/太短 = 证据不足 → 放行(绝不凭形态单腿定罪;词表路径仍兜高频竞品)。"""
    if not isinstance(item, dict):
        return False
    bio = str(item.get("bio") or item.get("description") or "").strip()
    if len(bio) < _CORPORATE_VOICE_MIN_BIO_LEN:
        return False
    if not _official_form_signal(item):
        return False
    return _corporate_voice_bio(bio)


def _brand_official_verdict(
    item: dict[str, Any],
    *,
    competitor_brands: dict[str, list[str]] | None = None,
) -> str:
    """品牌官号统一判据口:词表高精度快路优先("lexicon"),动态零词表路径兜漏网("dynamic"),
    未命中 ""。调用方拿真值即拦;子串区分只供 diagnostics 子计数,门面文案不透判据。"""
    if _competitor_brand_official(item, competitor_brands=competitor_brands):
        return "lexicon"
    if _dynamic_brand_official(item):
        return "dynamic"
    return ""


def discovery_account_gate_verdict(
    item: dict[str, Any] | None,
    *,
    competitor_brands: dict[str, list[str]] | None = None,
) -> str:
    """Unified conservative account gate for every discovery intake path.

    Returns a typed reason only when the identity is confirmed as Viltrox-owned
    or a brand official account.  Missing bio/ambiguous brand-shaped handles
    fail open, so a real creator is never rejected from one weak signal.  This
    helper is intentionally reusable by provider filtering, URL materialization
    and manual/federated imports.
    """
    if not isinstance(item, dict):
        return ""
    # Lazy import avoids the existing candidates -> discovery_filters module
    # dependency while keeping the own-brand predicate a single source.
    from app.domains.kol.profile_discovery_candidates import _is_own_brand_account

    if _is_own_brand_account(item):
        return "own_brand"
    if _brand_official_verdict(item, competitor_brands=competitor_brands):
        return "brand_official"
    return ""


# ── bio 明显无关补强(askmonitorofficial 案:bio="Web Development and Data Analyst")──────
# 双条件并发才丢(防误杀):① bio 实在且自述**非视觉职业**(web/软件/数据类);② 候选自身身份
# (channel_name/handle/bio,故意不含 sample_title——搜索命中的视频标题是「查询词回声」不算自证)
# 无任一相机/视觉创作信号。摄影+开发双栖者因 bio/handle 带相机信号而放行。纯函数零 IO。
_NON_VISUAL_PROFESSION_TERMS = (
    "web development", "web developer", "web design", "software engineer",
    "software development", "software developer", "app development", "app developer",
    "data analyst", "data analytics", "data scientist", "data engineer",
    "full stack", "backend developer", "frontend developer",
)


def _is_bio_irrelevant(item: dict[str, Any]) -> bool:
    bio = str(item.get("bio") or item.get("description") or "").strip().lower()
    if len(bio) < 12:
        return False  # bio 缺/太短 = 证据不足,放行(不误杀)
    if not any(term in bio for term in _NON_VISUAL_PROFESSION_TERMS):
        return False
    identity = " ".join(
        str(item.get(k) or "") for k in ("channel_name", "handle", "display_name", "bio")
    ).lower()
    return not any(term in identity for term in _CAMERA_SIGNAL_TERMS)


def _candidate_blob(item: dict[str, Any]) -> str:
    """候选自身内容拼接(sample_title + channel_name + handle + bio),小写。绝不含 search_query(查询词会自命中)。

    bio 为 K2 富化新增(YT channels.list 频道简介 / IG profile-scraper biography):真摄影师
    的 bio 几乎必含 photographer/filmmaker 等信号,判据文本更真实;无 bio 的 item 行为不变。"""
    return " ".join(
        str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle", "bio")
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


# ── 第二道闸(用户 live 实锤 2026-07-12,kol_pool 12297 两粉号穿闸案)────────────────
# 根因:发现时 followers=NULL 按「不误杀」放行(正确),档案补全回填 followers=2 后无第二道闸,
# 照样出现在推荐面。裁决升级:followers 未知的候选**不进推荐面**(「分析后再 po」),
# 补全回填后重过闸命中 → 在 raw_platform_data JSON 打 low_reach 标(不建新列不迁移);
# 推荐/发现/召回三出口读「实时判据 + 该标」双保险。red line 不变:落库≠推荐,库里保留资产
# 只是不推荐;零触 viltrox_fit_score / rule_v0。
LOW_REACH_FLAG_KEY = "low_reach"
# SQL 侧探测标存在用的 LIKE 参数(house 口径:LIKE ? + 参数传 pattern,见 costs/budget_guard)。
LOW_REACH_FLAG_LIKE_PATTERN = f'%"{LOW_REACH_FLAG_KEY}"%'


def _reach_followers_known(candidate: dict[str, Any] | None) -> bool:
    """followers 族是否「明确在场且可解析」(粉丝数已知)。未知=NULL/字段缺/空串。"""
    if not isinstance(candidate, dict):
        return False
    return _known_numeric(candidate, _REACH_FOLLOWER_KEYS) is not None


def _low_reach_flagged(candidate: dict[str, Any] | None) -> bool:
    """读第二道闸的 low_reach 标(回填后重过闸命中时打进 raw_platform_data JSON)。

    两种在场形态都认:① SQL 侧算好的 low_reach_flagged 列(LIKE 探测,BOOLEAN 读回可能是
    int 1/0,按 truthy 容错);② 行里带 raw_platform_data(str/dict)时解析 JSON 取
    LOW_REACH_FLAG_KEY.flag。总开关关闭时恒 False(与实时判据同款 env 口径)。纯函数零 IO。"""
    if not isinstance(candidate, dict) or not _reach_floor_enabled():
        return False
    if candidate.get("low_reach_flagged"):
        return True
    raw = candidate.get("raw_platform_data")
    payload: Any = raw
    if isinstance(raw, (str, bytes)):
        text = raw.decode() if isinstance(raw, bytes) else raw
        if LOW_REACH_FLAG_KEY not in text:
            return False
        try:
            import json as _json_mod

            payload = _json_mod.loads(text)
        except Exception:
            return False
    if not isinstance(payload, dict):
        return False
    flag = payload.get(LOW_REACH_FLAG_KEY)
    if isinstance(flag, dict):
        return bool(flag.get("flag"))
    return bool(flag)


def _reach_display_state(candidate: dict[str, Any] | None) -> str:
    """推荐/发现/召回三出口 + 会话读端的统一展示三态(单一真源,别造第二套判据):

    - "low_reach":实时判据命中(followers 明确 < 门槛/互动实测全零)或 low_reach 标在场 → 不展示;
    - "unknown":followers 未知(NULL/缺)→ 不展示,折叠为「分析中 ×N」(分析后再 po);
    - "ok":followers 已知且达标 → 展示。
    总开关(VKPI_DISCOVERY_REACH_FLOOR_ENABLED)关闭 → 恒 "ok"(全放行,与既有开关语义一致)。
    """
    if not isinstance(candidate, dict) or not _reach_floor_enabled():
        return "ok"
    if _reach_floor_reason(candidate) or _low_reach_flagged(candidate):
        return "low_reach"
    if not _reach_followers_known(candidate):
        return "unknown"
    return "ok"


# facebook 为 opt-in 平台:用户显式选择才参与发现(FB 流量/召回质量一般,做够用的即可),
# 不进 _platforms() 的默认三平台兜底,避免稀释默认轮转结果。
SUPPORTED_DISCOVERY_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook"}


def _is_discovery_garbage(item: dict[str, Any]) -> bool:
    """Drop unusable identities and obvious retailer/distributor accounts."""
    handle = str(item.get("handle") or "").strip()
    name = str(item.get("channel_name") or "").strip()
    if not handle and name.lower() in ("", "unknown creator"):
        return True
    identity = " ".join(
        str(item.get(field) or "")
        for field in ("handle", "channel_name", "display_name", "username", "profile_url", "channel_url")
    ).lower()
    return bool(
        re.search(
            r"\bb\s*&\s*h\b|b\s*and\s*h|pro\s*audio|photo\s*video|"
            r"camera\s*(?:store|house|shop|world|land)|rental|retailer|wholesale|"
            r"distributor|旗舰店|专卖|经销",
            identity,
        )
    )


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
