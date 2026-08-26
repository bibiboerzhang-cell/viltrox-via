"""垂类判定:从「在一段拼起来的文字里搜一个词」改成「多路取证」(2026-08-25)。

背景取证(prod 只读探针,2034 人在池):

* ``primary_topic`` 填充 8/2034(0.4%)、``content_style`` 0/2034 —— **池里根本没有垂类字段**。
  于是历史的垂类硬筛退化成:把 bio / primary_topic / 证据标题拼成一个 blob,再在里面找
  一个英文单词(``_vertical_filter_matches``)。操作员勾「生活方式」= 在 blob 里找
  ``lifestyle`` 这一个词,全池只有 52 人命中,再叠 US+en+≥5 万粉 只剩 2 人 → 必然 0 结果。
* 而现成、已回填、**从来没有被读过**的真信号有四路:
  ``topic_details_json`` 1199 行(其中频道关键词 615 行、平台内容分类直方 772 行、
  平台身份标注 348 行)、``tagged_brands_json`` 544 行、``vkpi_kol_video_evidence`` 标题
  5716 条覆盖 974 人、以及原有的 bio。

本模块把这四路(加原有 bio / 已识别镜头 / 作品主题标记)做成**并列的取证路**,一个人可以
同时归进多个垂类,每一次归类都带着「命中了哪一路的哪个词」一起返回。四条硬约束:

1. **多维度**:``VerticalReading.verticals`` 多值;每条归类都带 ``evidence``(路+命中词+原文)。
2. **不放宽合格标准**:只回答「他做什么内容」,**不碰**器材证据 / 新鲜度 / 粉丝下限 /
   ``viltrox_fit_score`` / rule_v0。``gear_content`` 闸仍走原来的 ``_factual_candidate_signal_blob``
   语料,一个字节没动 —— 垂类语料变宽**不会**顺带把器材闸放宽。
3. **诚实**:判不出垂类的人 ``is_unknown=True``,**不默认归进任何一类**;硬筛沿用已上线的
   三态语义(缺省 ``require``,未知不进)。
4. **可解释**:``vertical_explanations`` 产出中文短句(门面禁术语)。

刻意不映射的信号(宁可少判,不许凑数):

* 平台内容分类里的「教育」(27)/「娱乐」(24):太泛,任何一个垂类都套得上,不给映射。
* 平台身份标注里的「Photographer」(prod 121 人,是最大的单一取值):现有 9 个垂类里没有
  一个是「摄影」本身 ——「摄影教程」多claim了教程、「人像创作」多claim了人像。**不映射**,
  这 121 人在垂类上继续算未知,如实透出。
* 平台内容分类只按 1 条视频就下结论的一律不算数(prod 772 行直方里 728 行只有 1 条样本):
  见 ``CATEGORY_MIN_COUNT`` / ``CATEGORY_MIN_SHARE``。

red line:纯函数模块,不读库、不写库、不发请求、不花钱。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Sequence

from app.core.logging import get_logger
from app.domains.kol.profile_vertical_lexicon import (
    BRAND_VERTICALS,
    CATEGORY_LABELS_ZH,
    CATEGORY_MIN_COUNT,
    CATEGORY_MIN_SHARE,
    CATEGORY_VERTICALS,
    MAX_EVIDENCE_PER_VERTICAL,
    ROUTE_CHANNEL_KEYWORDS,
    ROUTE_CONTENT_LABELS,
    ROUTE_LABELS_ZH,
    ROUTE_PROFILE_CATEGORY,
    ROUTE_PROFILE_TEXT,
    ROUTE_TAGGED_BRANDS,
    ROUTE_USED_LENSES,
    ROUTE_VIDEO_CATEGORY,
    ROUTE_VIDEO_TITLES,
    TEXT_ROUTES,
    TEXT_RULES,
    VERTICAL_KEYS,
    VERTICAL_LABELS_ZH,
)

logger = get_logger(__name__)


# ── 词命中:ASCII 走词首边界,CJK 走子串 ──────────────────────────────────
#
# 为什么不沿用历史的裸子串:裸子串会让 "vs" 命中 "vsync"、"tech" 命中 "technique"
# (于是「摄影技巧」被判成「科技」)。封边界既堵住这类误伤,又保留 "photo" 命中
# "photography"、"guide" 命中 "guidelines" 这类**必须保留**的历史命中。
# 短词(≤4 个 ASCII 字符)两头都封边界,免得 "cat" 命中 "catalog"。
#
# 边界刻意**不用** ``\b``:Python 的 ``\w`` 含 CJK,于是「モノやサービスをcinematicに」
# 这种中日韩简介里嵌英文词的写法,``\bcinematic`` 会因为前一个字是 CJK(在 \w 里)
# 而整条判空 —— prod 2034 人快照实测因此丢 5 人,丢的正是日语/中文创作者。改用显式
# 字符类前后瞻:"spatialcamera" 仍不算 "camera"、"techniques" 仍不算 "tech",而
# CJK / 标点 相邻的英文词照常命中。

_CJK_RE = re.compile("[぀-ヿ㐀-鿿]")


def _term_regex(term: str) -> str:
    """单个词的正则片段。CJK 走子串;ASCII 封「前面不是英数」,≤4 字符连后面一起封。"""
    body = re.escape(term)
    if _CJK_RE.search(term):
        return body
    tail = r"(?![a-z0-9])" if len(term.replace(" ", "")) <= 4 else ""
    return rf"(?<![a-z0-9]){body}{tail}"


def _group_pattern(group: Sequence[str]) -> re.Pattern[str]:
    """一个词组编译成一条候选式正则 —— 每候选人每词组只跑一次匹配。"""
    return re.compile("|".join(_term_regex(str(term).strip().lower()) for term in group if str(term).strip()))


_COMPILED_RULES: dict[str, tuple[tuple[re.Pattern[str], ...], ...]] = {
    vertical: tuple(tuple(_group_pattern(group) for group in rule) for rule in rules)
    for vertical, rules in TEXT_RULES.items()
}


def term_hit(term: str, haystack: str) -> bool:
    """单个词是否命中一段(已小写的)文本。CJK 用子串,ASCII 用词首边界。"""
    needle = str(term or "").strip().lower()
    if not needle or not haystack:
        return False
    return bool(re.search(_term_regex(needle), haystack))


def _rule_hit(
    rules: Sequence[Sequence[re.Pattern[str]]],
    haystack: str,
) -> tuple[str, ...]:
    """AND-of-OR:任一条规则的**每个**词组都命中即成立,返回命中的词(按词组顺序)。"""
    for rule in rules:
        matched: list[str] = []
        for pattern in rule:
            found = pattern.search(haystack)
            if not found:
                matched = []
                break
            matched.append(found.group(0))
        if matched:
            return tuple(matched)
    return ()


# ── 语料装配 ─────────────────────────────────────────────────────────────


def _clean(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _as_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text.lower() == "null":
        return None
    try:
        return json.loads(text)
    except ValueError:
        # 坏 JSON = 这一路没有信号,不是异常路径;按「未知」处理并留痕,绝不冒充有信号。
        logger.debug("vertical signal json unreadable len=%d", len(text))
        return None


def _flatten_strings(value: Any, out: list[str], limit: int = 30) -> None:
    if len(out) >= limit:
        return
    if isinstance(value, str):
        text = _clean(value, 200)
        if text:
            out.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            _flatten_strings(item, out, limit)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_strings(item, out, limit)


def _topic_details(row: dict[str, Any]) -> dict[str, Any]:
    parsed = _as_json(row.get("topic_details_json"))
    return parsed if isinstance(parsed, dict) else {}


def signal_corpus(row: dict[str, Any], evidence: dict[str, Any]) -> dict[str, list[str]]:
    """按取证路装配可读的原始信号片段。空路 = 这个人这一路没有信号(不是 0,是没有)。"""
    topics = _topic_details(row)
    corpus: dict[str, list[str]] = {route: [] for route in TEXT_ROUTES}

    profile_bits: list[str] = []
    for key in ("bio", "primary_topic", "content_style"):
        text = _clean(row.get(key), 600)
        if text:
            profile_bits.append(text)
    _flatten_strings(_as_json(row.get("secondary_topics_json")), profile_bits, limit=12)
    corpus[ROUTE_PROFILE_TEXT] = profile_bits

    keywords: list[str] = []
    for item in topics.get("keywords") or []:
        text = _clean(item, 80)
        if text and text not in keywords:
            keywords.append(text)
    corpus[ROUTE_CHANNEL_KEYWORDS] = keywords[:50]

    categories: list[str] = []
    for key in ("business_category", "commerce_category"):
        for part in str(topics.get(key) or "").split(","):
            text = _clean(part, 80)
            if text and text.lower() not in {"none", "null"} and text not in categories:
                categories.append(text)
    corpus[ROUTE_PROFILE_CATEGORY] = categories

    titles: list[str] = []
    for item in evidence.get("evidence_titles") or []:
        text = _clean(item, 220)
        if text and text not in titles:
            titles.append(text)
    for item in evidence.get("representative_evidence") or []:
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("title"), 220)
        if text and text not in titles:
            titles.append(text)
    corpus[ROUTE_VIDEO_TITLES] = titles[:20]

    corpus[ROUTE_CONTENT_LABELS] = [
        text for text in (_clean(item, 80) for item in evidence.get("reason_labels") or []) if text
    ]
    corpus[ROUTE_USED_LENSES] = [
        text for text in (_clean(item, 120) for item in evidence.get("used_lenses") or []) if text
    ][:10]
    return corpus


_BRAND_SEPARATORS = re.compile(r"[._\-/]+")
_BRAND_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...], str, str], ...] = tuple(
    (re.compile(rf"\b{re.escape(token)}"), verticals, kind, token)
    for token, verticals, kind in BRAND_VERTICALS
)


def _tagged_brand_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """标记过的账号 -> 已知器材品牌。未知账号原样丢弃,不猜。"""
    parsed = _as_json(row.get("tagged_brands_json"))
    if not isinstance(parsed, list):
        return []
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for entry in parsed[:60]:
        if not isinstance(entry, dict):
            continue
        handle = _clean(entry.get("handle"), 80).lower()
        name = _clean(entry.get("name"), 80)
        probe = _BRAND_SEPARATORS.sub(" ", f"{handle} {name}".lower())
        for pattern, verticals, kind, token in _BRAND_PATTERNS:
            if not pattern.search(probe):
                continue
            key = f"{token}:{handle}"
            if key in seen:
                break
            seen.add(key)
            items.append(
                {
                    "handle": handle or name,
                    "token": token,
                    "kind": kind,
                    "verticals": verticals,
                }
            )
            break
    return items


def _video_category_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """平台内容分类直方图 -> 够格的主分类(双阈值,单条样本不算数)。"""
    histogram = _topic_details(row).get("video_category_ids")
    if not isinstance(histogram, dict) or not histogram:
        return []
    counts: dict[str, int] = {}
    for key, value in histogram.items():
        try:
            counts[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    total = sum(counts.values())
    if total <= 0:
        return []
    items: list[dict[str, Any]] = []
    for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        verticals = CATEGORY_VERTICALS.get(key)
        if not verticals:
            continue
        if count < CATEGORY_MIN_COUNT or count / total < CATEGORY_MIN_SHARE:
            continue
        items.append(
            {
                "category_id": key,
                "label": CATEGORY_LABELS_ZH.get(key, key),
                "count": count,
                "total": total,
                "verticals": verticals,
            }
        )
    return items


# ── 判定结果 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VerticalReading:
    """一个人的垂类读数。``verticals`` 空 = 判不出(未知),**不代表他不属于任何垂类**。"""

    verticals: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    signal_routes: tuple[str, ...]

    @property
    def is_unknown(self) -> bool:
        return not self.verticals

    @property
    def has_signal(self) -> bool:
        return bool(self.signal_routes)

    def evidence_for(self, vertical: str) -> list[dict[str, Any]]:
        key = normalize_vertical_key(vertical)
        return [dict(item) for item in self.evidence if item.get("vertical") == key]


def normalize_vertical_key(value: Any) -> str:
    """UI 传来的垂类 id 归一。不认识的原样返回(交给自由词兜底,不静默吞掉)。"""
    return re.sub(
        "[^a-z0-9_一-鿿]", "", str(value or "").strip().lower().replace("-", "_")
    )


def _snippet_for(items: Sequence[str], matched: Sequence[str]) -> str:
    """在原始片段里找出承载命中词的那一条,给结果卡当引文;找不到就退回第一条。"""
    for term in matched:
        needle = str(term or "").lower()
        for item in items:
            if needle and needle in item.lower():
                return item
    return items[0] if items else ""


#: 引文只截命中词周围这么长一段 —— 整段简介往往夹着邮箱/电话,不该被搬到卡面上。
SNIPPET_WINDOW = 48
_CONTACT_SCRUBBER: Any = None
_CONTACT_SCRUBBER_READY = False


def _safe_snippet(snippet: str, matched: Sequence[str]) -> str:
    """命中词周围一小段 + 联系方式清洗。清洗器不可用时**不出引文**(失败方向 = 少给)。"""
    global _CONTACT_SCRUBBER, _CONTACT_SCRUBBER_READY
    text = str(snippet or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    start = min((lowered.find(str(term).lower()) for term in matched if str(term).lower() in lowered), default=0)
    head = max(0, start - SNIPPET_WINDOW // 3)
    clipped = text[head:head + SNIPPET_WINDOW]
    if head:
        clipped = "…" + clipped
    if head + SNIPPET_WINDOW < len(text):
        clipped = clipped + "…"
    if not _CONTACT_SCRUBBER_READY:
        _CONTACT_SCRUBBER_READY = True
        try:
            from app.domains.kol.contact_system import (
                sanitize_contact_values_for_external_processing as scrubber,
            )

            _CONTACT_SCRUBBER = scrubber
        except ImportError:
            logger.warning("联系方式清洗器不可用,垂类引文只保留命中词", exc_info=True)
            _CONTACT_SCRUBBER = None
    if _CONTACT_SCRUBBER is None:
        return ""
    return str(_CONTACT_SCRUBBER(clipped) or "").strip()


def _evidence_item(
    vertical: str, route: str, matched: Sequence[str], snippet: str, note: str = ""
) -> dict[str, Any]:
    terms = [term for term in matched if term]
    snippet = _safe_snippet(snippet, terms)
    note = note or f"{ROUTE_LABELS_ZH.get(route, route)}命中「{'、'.join(terms)}」"
    if snippet and snippet.lower() not in {term.lower() for term in terms}:
        note = f"{note}:{snippet}"
    return {
        "vertical": vertical,
        "vertical_label": VERTICAL_LABELS_ZH.get(vertical, vertical),
        "route": route,
        "route_label": ROUTE_LABELS_ZH.get(route, route),
        "matched": list(terms),
        "snippet": snippet,
        "note": note,
    }


def classify_verticals(
    row: dict[str, Any] | None,
    evidence: dict[str, Any] | None = None,
) -> VerticalReading:
    """多路取证判垂类。一个人可同时归多类;判不出就是判不出。"""
    row = dict(row or {})
    evidence = dict(evidence or {})
    corpus = signal_corpus(row, evidence)
    brands = _tagged_brand_items(row)
    categories = _video_category_items(row)

    signal_routes = [route for route in TEXT_ROUTES if corpus.get(route)]
    if brands:
        signal_routes.append(ROUTE_TAGGED_BRANDS)
    if categories:
        signal_routes.append(ROUTE_VIDEO_CATEGORY)

    found: dict[str, list[dict[str, Any]]] = {}

    def add(vertical: str, item: dict[str, Any]) -> None:
        bucket = found.setdefault(vertical, [])
        if len(bucket) < MAX_EVIDENCE_PER_VERTICAL:
            bucket.append(item)

    # 每一路先拼成一段整料再判(历史口径也是拼整块判),然后回头在原始片段里找出
    # 命中的那一条当引证 —— 判定只跑一次正则,解释仍然精确到原文。
    haystacks = {
        route: " \n".join(corpus.get(route) or ()).lower()
        for route in TEXT_ROUTES
        if corpus.get(route)
    }
    for vertical in VERTICAL_KEYS:
        rules = _COMPILED_RULES.get(vertical) or ()
        for route, haystack in haystacks.items():
            matched = _rule_hit(rules, haystack)
            if matched:
                add(vertical, _evidence_item(vertical, route, matched, _snippet_for(corpus[route], matched)))

    # 作品里已经识别出具体镜头型号 = 器材内容的直给证据(标题里可能一个 "lens" 都没有)。
    for lens in (corpus.get(ROUTE_USED_LENSES) or ())[:1]:
        add("camera_system", _evidence_item(
            "camera_system", ROUTE_USED_LENSES, (lens,), lens, f"作品里识别到镜头「{lens}」"))

    for brand in brands:
        for vertical in brand["verticals"]:
            add(
                vertical,
                {
                    "vertical": vertical,
                    "vertical_label": VERTICAL_LABELS_ZH.get(vertical, vertical),
                    "route": ROUTE_TAGGED_BRANDS,
                    "route_label": ROUTE_LABELS_ZH[ROUTE_TAGGED_BRANDS],
                    "matched": [brand["token"]],
                    "snippet": f"@{brand['handle']}",
                    "note": f"作品里标记过{brand['kind']} @{brand['handle']}",
                },
            )

    for category in categories:
        for vertical in category["verticals"]:
            add(
                vertical,
                {
                    "vertical": vertical,
                    "vertical_label": VERTICAL_LABELS_ZH.get(vertical, vertical),
                    "route": ROUTE_VIDEO_CATEGORY,
                    "route_label": ROUTE_LABELS_ZH[ROUTE_VIDEO_CATEGORY],
                    "matched": [category["label"]],
                    "snippet": f"{category['count']}/{category['total']} 条",
                    "note": (
                        f"平台把他的作品归到「{category['label']}」"
                        f"({category['count']}/{category['total']} 条)"
                    ),
                },
            )

    verticals = tuple(key for key in VERTICAL_KEYS if key in found)
    evidence_items = tuple(item for key in verticals for item in found[key])
    return VerticalReading(verticals, evidence_items, tuple(dict.fromkeys(signal_routes)))


# ── 硬筛出口(与 profile_recall_filter_modes 的三态词表同源)────────────────

OUTCOME_PASS = ""
OUTCOME_UNKNOWN = "unknown"
OUTCOME_MISMATCH = "mismatch"


def _free_term_hit(corpus: dict[str, list[str]], row: dict[str, Any], term: str) -> tuple[str, str]:
    """taxonomy 之外的自由词兜底:沿用历史口径(词全中即命中),但要说清命中在哪一路。"""
    tokens = [token for token in re.split(r"[_\s]+", str(term or "").strip().lower()) if token]
    if not tokens:
        return "", ""
    for route in TEXT_ROUTES:
        for snippet in corpus.get(route) or ():
            haystack = snippet.lower()
            if all(token in haystack for token in tokens):
                return route, snippet
    for brand in _tagged_brand_items(row):
        haystack = f"{brand['handle']} {brand['token']}".lower()
        if all(token in haystack for token in tokens):
            return ROUTE_TAGGED_BRANDS, f"@{brand['handle']}"
    return "", ""


def vertical_filter_outcome(
    row: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    requested: Iterable[Any],
    *,
    reading: VerticalReading | None = None,
) -> tuple[str, VerticalReading, list[dict[str, Any]]]:
    """硬筛判定:``(结果, 读数, 命中引证)``。

    * 请求的垂类里**任一个**被判到 -> ``OUTCOME_PASS``;
    * 一个都没判到、但这个人有别的垂类读数 -> ``OUTCOME_MISMATCH``(他做的是别的);
    * 完全判不出垂类 -> ``OUTCOME_UNKNOWN``(缺省 ``require`` 档不进,与历史一致)。

    自由词(不在 9 个垂类里的自定义词)沿用历史子串口径兜底,零回归。
    """
    reading = reading if reading is not None else classify_verticals(row, evidence)
    keys = [key for key in (normalize_vertical_key(item) for item in requested or ()) if key]
    if not keys:
        return OUTCOME_PASS, reading, []

    hits: list[dict[str, Any]] = []
    corpus = signal_corpus(dict(row or {}), dict(evidence or {}))
    for key in keys:
        if key in VERTICAL_KEYS:
            hits.extend(reading.evidence_for(key))
            continue
        route, snippet = _free_term_hit(corpus, dict(row or {}), key)
        if route:
            hits.append(_evidence_item(key, route, (key,), snippet))
    if hits:
        return OUTCOME_PASS, reading, hits
    if any(key in VERTICAL_KEYS for key in keys):
        # 勾的是 9 个垂类之一:判得出别的垂类 = 确认他做的是别的;完全判不出 = 未知。
        return (OUTCOME_UNKNOWN if reading.is_unknown else OUTCOME_MISMATCH), reading, []
    # 自定义自由词没命中:沿用历史口径 —— 语料整体为空才算未知,否则算不匹配。
    return (OUTCOME_UNKNOWN if not reading.has_signal else OUTCOME_MISMATCH), reading, []


# ── 门面输出(中文,禁术语)──────────────────────────────────────────────


def vertical_explanations(
    reading: VerticalReading,
    *,
    limit_per_vertical: int = 3,
) -> list[dict[str, Any]]:
    """结果卡用:每个垂类一条「为什么算他是……」。判不出时返回空列表(由卡面显示未知)。"""
    out: list[dict[str, Any]] = []
    for vertical in reading.verticals:
        items = reading.evidence_for(vertical)[:limit_per_vertical]
        out.append(
            {
                "vertical": vertical,
                "label": VERTICAL_LABELS_ZH.get(vertical, vertical),
                "reasons": [str(item.get("note") or "") for item in items if item.get("note")],
                "routes": sorted({str(item.get("route") or "") for item in items}),
            }
        )
    return out


__all__ = [
    "MAX_EVIDENCE_PER_VERTICAL",
    "OUTCOME_MISMATCH",
    "OUTCOME_PASS",
    "OUTCOME_UNKNOWN",
    "ROUTE_LABELS_ZH",
    "TEXT_ROUTES",
    "VERTICAL_KEYS",
    "VERTICAL_LABELS_ZH",
    "VerticalReading",
    "classify_verticals",
    "normalize_vertical_key",
    "signal_corpus",
    "term_hit",
    "vertical_explanations",
    "vertical_filter_outcome",
]
