"""Pure evidence helpers for the user-visible KOL recall result.

The helpers in this module never call providers, write state, or expose contact
values.  They only decide whether an already-loaded candidate has a textual
reason to be shown and describe the returned candidate set.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_.+][a-z0-9]+)*|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_GENERIC_TERMS = frozenset(
    {
        "account", "accounts", "and", "best", "camera", "channel", "content", "creator", "creators", "filmmaker",
        "filmmakers", "find", "for", "from", "gear", "instagram", "lens", "lenses",
        "good", "high", "influencer", "influencers", "kol", "looking", "new", "of",
        "official", "pro", "professional", "quality", "relevant", "suitable", "talented", "top",
        "photographer", "photographers", "photography", "review", "reviewer",
        "reviewers", "reviews", "search", "show", "social", "video", "videographer", "videographers",
        "australia", "australian", "austria", "brazil", "brasil", "ca", "canada", "chile",
        "denmark", "de", "dk", "es", "fr", "france", "germany", "gb", "india", "indonesia",
        "it", "italy", "jp", "japan", "korea", "kr", "mexico", "mx", "netherlands", "nl",
        "norway", "nz", "poland", "portugal", "pt", "ru", "russia", "singapore", "spain",
        "states", "sweden", "th", "thailand", "the", "tiktok", "tr", "turkey", "uk",
        "united", "us", "usa", "vietnam", "vn", "with", "youtube", "za",
    }
)
_GENERIC_CJK_TERMS = (
    "寻找", "查找", "搜索", "适合", "推荐", "一些", "达人", "创作者", "找",
    # 受众规模是筛选条件(落 followers_min),不是题材证据——不得充当意图举证词。
    "消费", "群体", "受众", "粉丝", "流量", "曝光", "影响力", "人群",
    "美国", "英国", "加拿大", "德国", "法国", "日本", "韩国", "澳大利亚", "西班牙",
    "意大利", "巴西", "俄罗斯", "泰国", "越南", "印尼", "印度尼西亚", "墨西哥",
)
# Grammatical particles only: characters that carry no topic of their own and
# therefore mark a word boundary.  Cutting there can only remove candidate
# terms, never admit one, so it cannot loosen any downstream gate.
_CJK_FUNCTION_CHARS = (
    "比如", "以及", "还有",
    "的", "了", "和", "与", "及", "或", "在", "是", "有", "我", "你", "他", "她", "它",
    "们", "这", "那", "就", "都", "也", "很", "把", "被", "请", "帮", "给", "让", "从",
    "对", "为", "到", "个", "等",
)
_CJK_RUN_RE = re.compile(r"[一-鿿]+")
_MATCH_FIELDS = (
    "handle",
    "display_name",
    "bio",
    "primary_topic",
    "content_style",
    "secondary_topics_json",
    "profile_text",
    "type_reason",
)


_COUNTRY_NORMALIZATION = {
    "usa": "us", "united states": "us", "united states of america": "us",
    "uk": "gb", "united kingdom": "gb", "great britain": "gb",
    "canada": "ca", "germany": "de", "france": "fr", "japan": "jp",
    "korea": "kr", "south korea": "kr", "australia": "au", "spain": "es",
    "mexico": "mx", "méxico": "mx", "italy": "it", "brazil": "br", "brasil": "br",
    "portugal": "pt", "russia": "ru", "russian federation": "ru", "thailand": "th",
    "vietnam": "vn", "viet nam": "vn", "indonesia": "id", "turkey": "tr", "türkiye": "tr",
    "poland": "pl", "netherlands": "nl", "holland": "nl", "saudi arabia": "sa",
    "united arab emirates": "ae", "uae": "ae", "india": "in", "singapore": "sg",
    "new zealand": "nz", "china": "cn", "hong kong": "hk", "taiwan": "tw",
    "美国": "us", "英国": "gb", "加拿大": "ca", "德国": "de", "法国": "fr", "日本": "jp",
    "韩国": "kr", "澳大利亚": "au", "西班牙": "es", "墨西哥": "mx", "意大利": "it",
    "巴西": "br", "葡萄牙": "pt", "俄罗斯": "ru", "泰国": "th", "越南": "vn",
    "印尼": "id", "印度尼西亚": "id", "土耳其": "tr", "波兰": "pl", "荷兰": "nl",
    "沙特": "sa", "沙特阿拉伯": "sa", "阿联酋": "ae", "印度": "in", "新加坡": "sg",
    "新西兰": "nz", "中国": "cn", "中國": "cn", "香港": "hk", "台湾": "tw", "台灣": "tw",
}

_PRODUCT_FAMILY_TERMS = frozenset({"epic", "lab", "evo", "air", "vintage"})
_PRODUCT_SPEC_TERM_RE = re.compile(
    r"(?:\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?-?(?:mm|cm|inch|inches|ws|w|nit|nits|mah|fps|x)"
    r"|[ft]\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PRODUCT_FOCAL_TERM_RE = re.compile(r"\d{2,3}(?:-\d{2,3})?mm", re.IGNORECASE)
# 「卡口/品牌/系统」级产品语境:不是型号证明,但证明创作者在这款镜头的生态里(新品没人提过时的放行依据)。
_PRODUCT_CONTEXT_TERMS = frozenset({
    "viltrox", "sony", "nikon", "canon", "fujifilm", "fuji", "leica", "panasonic", "lumix", "sigma",
    "e-mount", "z-mount", "x-mount", "rf-mount", "l-mount", "ef-mount", "m43", "full-frame", "aps-c",
})
_PRODUCT_MOUNT_RE = re.compile(r"^[a-z]{1,3}-?mount$", re.IGNORECASE)


def product_context_proof_terms(product_terms: list[str], matched_terms: set[str]) -> list[str]:
    """品牌/卡口/画幅级语境命中(与型号级区分;仅焦段/光圈属性仍不算)。"""
    return [
        term for term in product_terms
        if term in matched_terms and (term in _PRODUCT_CONTEXT_TERMS or _PRODUCT_MOUNT_RE.fullmatch(term))
    ][:2]


def _normal_dimension(name: str, value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if name == "country":
        text = _COUNTRY_NORMALIZATION.get(text, text)
        if re.fullmatch(r"[a-z]{2}", text):
            return text
        return "unknown"
    return text


def _has_cjk(text: Any) -> bool:
    return any("一" <= char <= "鿿" for char in str(text or ""))


def _cjk_segments(run: str) -> list[str]:
    """Split one CJK run into 2-character candidate words.

    Chinese writes no spaces, so ``_TOKEN_RE`` can only capture a whole run and
    a whole run matches nothing in a bio.  The run is first cut at the known
    generic and grammatical words — both lists only ever remove candidates — and
    each surviving span is then tiled into overlapping bigrams, the
    boundary-free segmentation a CJK search analyzer uses.

    Overlap buys no leniency downstream: ``_intent_proof_terms`` charges the
    query's character spans, so 摄影 + 影师 out of 摄影师 still count as the one
    word they are, while 消费 + 群体 out of 消费群体 correctly count as two.
    """
    spans = [run]
    for generic in (*_GENERIC_CJK_TERMS, *_CJK_FUNCTION_CHARS):
        spans = [part for span in spans for part in span.split(generic)]
    return [
        span[index:index + 2]
        for span in spans
        for index in range(len(span) - 1)
    ]


def _candidate_terms(value: Any) -> list[str]:
    """Tokenize to comparable words: ASCII tokens as-is, CJK runs segmented."""
    candidates: list[str] = []
    for raw in _TOKEN_RE.findall(str(value or "").lower()):
        term = raw.strip("-_.+")
        if _CJK_RUN_RE.fullmatch(term):
            candidates.extend(_cjk_segments(term))
        elif term:
            candidates.append(term)
    return candidates


def query_evidence_terms(value: Any, *, limit: int = 16) -> list[str]:
    """Return bounded, de-duplicated intent terms suitable for lexical proof."""
    raw_terms = _candidate_terms(value)
    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if not term or term in _GENERIC_TERMS or term in seen:
            continue
        # A 2-character Chinese word is a full word, not an English stub: the
        # min-length rule is an ASCII rule and must not silently delete it.
        if len(term) < 3 and not any(ch.isdigit() for ch in term) and not _has_cjk(term):
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max(1, int(limit)):
            break
    # A single generic role/category word cannot prove relevance, but the
    # controlled pair "lens + review" is a concrete content lane.  Preserve
    # both tokens so retrieval and field-level evidence use the same anchors.
    raw_set = set(raw_terms)
    has_lens = bool(raw_set.intersection({"lens", "lenses"}))
    has_review = bool(raw_set.intersection({"review", "reviews", "reviewer", "reviewers"}))
    if not terms and has_lens and has_review:
        for anchor in ("lens", "review"):
            if anchor not in seen and len(terms) < max(1, int(limit)):
                seen.add(anchor)
                terms.append(anchor)
    return terms


def product_evidence_terms(value: Any, *, limit: int = 12) -> list[str]:
    """Derive bounded public product anchors from a resolved catalog projection."""
    product = value if isinstance(value, dict) else {}
    text = " ".join(
        str(product.get(field) or "")
        # Category words such as ``macro`` or ``monitor`` identify a product
        # class, not the resolved SKU.  They must never satisfy the product
        # identity leg on their own.
        for field in ("sku", "model_name", "marketing_name", "series")
    )
    return query_evidence_terms(text, limit=limit)


def _is_exact_product_identity_term(term: str) -> bool:
    """Accept model-like alpha+digit tokens, never a bare optical/size spec."""
    text = str(term or "").strip().lower()
    return bool(
        any(char.isalpha() for char in text)
        and any(char.isdigit() for char in text)
        and not _PRODUCT_SPEC_TERM_RE.fullmatch(text)
    )


def _product_identity_proof_terms(
    product_terms: list[str],
    matched_terms: set[str],
) -> list[str]:
    """Return the exact public terms that proved the resolved product identity."""
    exact = [
        term for term in product_terms
        if term in matched_terms and _is_exact_product_identity_term(term)
    ]
    if exact:
        return exact[:1]
    # A named Viltrox lens family plus its focal is a usable public identity
    # proof (EPIC + 65mm). A focal/size/aperture alone is merely an attribute.
    families = [
        term for term in product_terms
        if term in matched_terms and term in _PRODUCT_FAMILY_TERMS
    ]
    focals = [
        term for term in product_terms
        if term in matched_terms and _PRODUCT_FOCAL_TERM_RE.fullmatch(term)
    ]
    return [families[0], focals[0]] if families and focals else []


def pool_text_fallback_ids(
    conn: Any,
    query_text: Any,
    candidate_limit: int,
    *,
    max_candidate_limit: int,
    allow_backfill: bool,
) -> list[int]:
    """Read lexical candidate IDs; popular-account fill is legacy-only."""
    limit = max(1, min(int(max_candidate_limit), int(candidate_limit or 50)))
    ids: list[int] = []
    seen: set[int] = set()

    def collect(rows: list[Any]) -> None:
        for row in rows:
            try:
                kol_id = int(dict(row).get("kol_pool_id") or 0)
            except (TypeError, ValueError):
                kol_id = 0
            if kol_id > 0 and kol_id not in seen:
                seen.add(kol_id)
                ids.append(kol_id)

    text = " ".join(str(query_text or "").split()).strip()
    if text:
        like = f"%{text}%"
        collect(
            conn.execute(
                """
                SELECT p.id AS kol_pool_id
                FROM vkpi_kol_pool p
                WHERE p.duplicate_of_id IS NULL
                  AND (
                        LOWER(COALESCE(p.handle, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.display_name, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.bio, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.primary_topic, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.content_style, '')) LIKE LOWER(?)
                  )
                ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
                LIMIT ?
                """,
                (like, like, like, like, like, limit),
            ).fetchall()
        )
    # Legacy recall historically treats an exact phrase hit as the complete
    # answer and returns immediately.  Do not append unrelated follower-head
    # rows merely because the requested display limit is larger.  Smart local
    # sets allow_backfill=False and deliberately continues into anchor top-up.
    if ids and allow_backfill:
        return ids[:limit]
    if len(ids) >= limit:
        return ids[:limit]
    if not allow_backfill:
        terms = query_evidence_terms(text)
        if not terms:
            return ids
        fields = (
            "p.handle", "p.display_name", "p.bio", "p.primary_topic",
            "p.content_style", "p.secondary_topics_json",
        )
        clauses: list[str] = []
        params: list[Any] = []
        for term in terms:
            for field in fields:
                clauses.append(f"LOWER(COALESCE({field}, '')) LIKE LOWER(?)")
                params.append(f"%{term}%")
        collect(
            conn.execute(
                f"""
                SELECT p.id AS kol_pool_id
                FROM vkpi_kol_pool p
                WHERE p.duplicate_of_id IS NULL AND ({' OR '.join(clauses)})
                ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        )
        return ids[:limit]
    collect(
        conn.execute(
            """
            SELECT p.id AS kol_pool_id
            FROM vkpi_kol_pool p
            WHERE p.duplicate_of_id IS NULL
            ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )
    return ids[:limit]


def pool_rows_for_recall(
    conn: Any,
    kol_pool_ids: list[int],
    *,
    low_reach_like_pattern: str,
) -> dict[int, dict[str, Any]]:
    """Load uncached pool rows used only when the profile index lacks a row."""
    if not kol_pool_ids:
        return {}
    placeholders = ", ".join(["?"] * len(kol_pool_ids))
    rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, p.platform, p.handle, p.display_name, p.profile_url,
               p.avatar_url, p.followers, p.avg_views, p.avg_comments, p.engagement_rate,
               p.bio, p.country, p.primary_topic, p.content_style, p.language,
               p.secondary_topics_json, p.brand_collaborations_json,
               p.raw_platform_data,
               CASE
                   WHEN LENGTH(TRIM(COALESCE(p.email, ''))) > 0 THEN 1
                   WHEN LOWER(TRIM(COALESCE(p.other_contacts_json, ''))) NOT IN ('', '[]', '{{}}', 'null') THEN 1
                   ELSE 0
               END AS contact_available,
               (p.raw_platform_data LIKE ?) AS low_reach_flagged
        FROM vkpi_kol_pool p
        WHERE p.duplicate_of_id IS NULL AND p.id IN ({placeholders})
        """,
        (low_reach_like_pattern, *(int(value) for value in kol_pool_ids)),
    ).fetchall()
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item.setdefault("profile_type", "")
        item.setdefault("profile_text", str(item.get("bio") or "")[:600])
        item.setdefault("type_reason", "索引未覆盖,按池内资料兜底展示")
        item.setdefault("creator_type_score", 0.0)
        item.setdefault("reviewer_type_score", 0.0)
        output[int(item["kol_pool_id"])] = item
    return output


def _contains_term(text: Any, term: str) -> bool:
    haystack = str(text or "").lower()
    if not haystack:
        return False
    if _has_cjk(term):
        return term in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None


def _intent_proof_terms(
    terms: list[str],
    matched_terms: set[str],
    query_text: Any,
) -> list[str]:
    """Return the proven intent words, charging overlapping CJK bigrams once.

    ASCII terms are maximal tokens and can never overlap inside the query, so
    for a query without CJK this is exactly the previous intersection.  CJK
    bigrams do overlap, so the query's character spans are scheduled greedily:
    摄影师 yields one word, 消费群体 yields the two words it really contains.
    """
    hits = [term for term in terms if term in matched_terms]
    cjk_hits = {term for term in hits if _has_cjk(term)}
    proven = [term for term in hits if term not in cjk_hits]
    if not cjk_hits:
        return proven
    haystack = str(query_text or "").lower()
    spans: list[tuple[int, int, str]] = []
    for term in cjk_hits:
        start = haystack.find(term)
        while start >= 0:
            spans.append((start + len(term), start, term))
            start = haystack.find(term, start + 1)
    reserved = -1
    for end, start, term in sorted(spans):
        if start >= reserved:
            reserved = end
            if term not in proven:
                proven.append(term)
    return proven


INTENT_TERMS_DEFAULT = 2
INTENT_TERMS_FLOOR = 1


def _intent_terms_required(provable_words: list[str], min_intent_terms: Any) -> int:
    """How many query-intent words this candidate must prove.

    A query that can only ever prove one word stays at 1 — demanding two proofs
    from 摄影师 would make a one-word Chinese query unsatisfiable.  Above that
    the caller's ``min_intent_terms`` decides, clamped to [1, 2] so no caller can
    silently invent a looser or stricter regime than the two the contract knows.

    为什么在线车道传 1(2026-08-25):AND-2 的前提是候选行有 8 个可举证字段。在线
    provider 行只填得出 handle / display_name / bio / 最新视频标题——
    ``profile_online_qualification._candidate_row`` 里的 primary_topic /
    content_style / profile_text / type_reason 一律取自 provider 从不下发的键
    (``profile_discovery_provider`` 全程不产出这四个字段),恒为空串;
    secondary_topics_json 同理恒为 []。要它凑够 2 个意图词,等于要它证明一件它
    没有资料可证明的事:实测 16 个会话 evaluated≈308 / accepted=11(3.6%),
    low_relevance≈87%,ONLINE_TARGET=30 一次都没达到过。
    这不是放宽标准,是让举证门槛跟随可举证面——本地行字段丰富,继续要 2 个。
    """
    if len(provable_words) <= 1:
        return INTENT_TERMS_FLOOR
    try:
        requested = int(min_intent_terms)
    except (TypeError, ValueError):
        return INTENT_TERMS_DEFAULT
    return max(INTENT_TERMS_FLOOR, min(INTENT_TERMS_DEFAULT, requested))


def build_match_evidence(
    row: dict[str, Any],
    evidence: dict[str, Any],
    query_text: Any,
    *,
    required_product_terms: Iterable[str] = (),
    min_intent_terms: int = INTENT_TERMS_DEFAULT,
) -> list[dict[str, str]]:
    """Build field-level proof from public profile or representative-work data.

    ``min_intent_terms`` 只调「查询本身能举证 2 个及以上意图词时,要求候选证明几个」。
    默认 2 = 现有口径逐字不变;唯一传 1 的调用方是在线车道
    (``profile_online_qualification``),理由见 :func:`_intent_terms_required`。
    """
    terms = query_evidence_terms(query_text)
    product_terms = query_evidence_terms(" ".join(str(item or "") for item in required_product_terms))
    all_terms = list(dict.fromkeys([*terms, *product_terms]))
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(field: str, value: Any) -> None:
        for term in all_terms:
            key = (field, term)
            if key not in seen and _contains_term(value, term):
                seen.add(key)
                matched.append({"field": field, "term": term, "source": "server_profile_evidence"})

    for field in _MATCH_FIELDS:
        add(field, row.get(field))
    for item in list(evidence.get("representative_evidence") or [])[:5]:
        if isinstance(item, dict):
            add("representative_evidence.title", item.get("title"))
    distinct_terms = {item["term"] for item in matched}
    # Count the words the query can prove at all, not the tokens it produced:
    # 摄影师 tiles into two bigrams but is still one word, and demanding two
    # proofs from it would make a one-word Chinese query unsatisfiable.
    provable_words = _intent_proof_terms(terms, set(terms), query_text)
    required_terms = _intent_terms_required(provable_words, min_intent_terms)
    identity_proof_terms = _product_identity_proof_terms(product_terms, distinct_terms)
    # 型号级(精确型号 / 系列+焦段)优先;没有时接受品牌/卡口/画幅级语境——新品上市池里没人提过型号,
    # 严格 30 曾因此 496/500 全灭(2026-08-23)。仅焦段/光圈属性、仅人设仍不放行(契约不变)。
    context_proof_terms = [] if identity_proof_terms else product_context_proof_terms(product_terms, distinct_terms)
    product_matched = not product_terms or bool(identity_proof_terms) or bool(context_proof_terms)
    # 一词不得两用(2026-08-25):产品锚进入 search_query 后品牌词也成了意图词,而池内 66.7%
    # 的资料本就写着品牌名——它若既算产品腿又算意图腿,AND-2 就有一个槽位近乎白送(实测出货
    # 78→333,其中 87% 由这一个词买来)。已被产品腿消费的词不再计入意图腿,回到两腿各自举证。
    product_proof_used = {str(term).lower() for term in (*identity_proof_terms, *context_proof_terms)}
    intent_proofs = [
        term
        for term in _intent_proof_terms(terms, distinct_terms, query_text)
        if str(term).lower() not in product_proof_used
    ]
    intent_matched = len(intent_proofs) >= required_terms
    if not intent_matched or not product_matched:
        return []
    # A losing bigram (影师 out of 摄影师) is a slice of a word, not a word: it
    # proved nothing above and must not be shown as a reason to the operator.
    intent_term_set = set(terms)
    proof_term_set = set(intent_proofs)
    matched = [
        item for item in matched
        if not (_has_cjk(item["term"]) and item["term"] in intent_term_set and item["term"] not in proof_term_set)
    ]

    # The response is itself the explanation contract. Preserve the product
    # identity proof and enough query-intent proof before filling the 12-row
    # cap, even when a long bio matched many earlier generic query terms.
    selected: list[dict[str, str]] = []
    selected_pairs: set[tuple[str, str]] = set()

    def append(item: dict[str, str]) -> None:
        key = (item["field"], item["term"])
        if key not in selected_pairs and len(selected) < 12:
            selected_pairs.add(key)
            selected.append(item)

    for proof_term in [*identity_proof_terms, *context_proof_terms]:
        proof = next((item for item in matched if item["term"] == proof_term), None)
        if proof:
            append(proof)
    intent_terms_kept: set[str] = set()
    for item in matched:
        if item["term"] in terms and item["term"] not in intent_terms_kept:
            append(item)
            intent_terms_kept.add(item["term"])
            if len(intent_terms_kept) >= required_terms:
                break
    for item in matched:
        append(item)
    return selected


def why_fit_from_match_evidence(match_evidence: Iterable[dict[str, Any]]) -> str:
    """Generate a reason only from the exact evidence returned to the caller."""
    pairs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in match_evidence:
        field = str(item.get("field") or "").strip()
        term = str(item.get("term") or "").strip()
        key = (field, term)
        if not field or not term or key in seen:
            continue
        seen.add(key)
        pairs.append(f"{field} 命中 {term}")
        if len(pairs) >= 3:
            break
    return "；".join(pairs)


def candidate_set_distribution(
    items: list[dict[str, Any]],
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Describe only the canonical candidates returned by this request."""

    facets: dict[str, Counter[str]] = {
        "platform": Counter(),
        "country": Counter(),
        "language": Counter(),
        "profile_type": Counter(),
        "contact_available": Counter(),
        "video_evidence": Counter(),
    }
    seen: set[int] = set()
    for item in items:
        try:
            kol_id = int(item.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            kol_id = 0
        if kol_id <= 0 or kol_id in seen:
            continue
        seen.add(kol_id)
        row = rows_by_id.get(kol_id) or {}
        facets["platform"][_normal_dimension("platform", row.get("platform") or item.get("platform"))] += 1
        facets["country"][_normal_dimension("country", row.get("country"))] += 1
        facets["language"][_normal_dimension("language", row.get("language"))] += 1
        profile_type = row.get("profile_type") or item.get("profile_type")
        facets["profile_type"][_normal_dimension("profile_type", profile_type)] += 1
        contact = row.get("contact_available")
        if contact is None and any(key in row for key in ("email", "other_contacts_json")):
            raw_other = str(row.get("other_contacts_json") or "").strip().lower()
            contact = bool(str(row.get("email") or "").strip()) or raw_other not in {"", "[]", "{}", "null"}
        facets["contact_available"]["unknown" if contact is None else ("yes" if bool(contact) else "no")] += 1
        has_video = bool((evidence_by_id.get(kol_id) or {}).get("representative_evidence"))
        facets["video_evidence"]["yes" if has_video else "no"] += 1
    for counts in facets.values():
        counts.setdefault("unknown", 0)
    return {
        "claim_status": "descriptive_only",
        "denominator": len(seen),
        "denominator_definition": "returned_canonical_candidates",
        "facets": {name: dict(sorted(counts.items())) for name, counts in facets.items()},
    }


def candidate_facets(
    row: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, str]:
    """Return non-sensitive dimensions sufficient to recompute after hard filters."""

    contact = row.get("contact_available")
    if contact is None and any(key in row for key in ("email", "other_contacts_json")):
        raw_other = str(row.get("other_contacts_json") or "").strip().lower()
        contact = bool(str(row.get("email") or "").strip()) or raw_other not in {"", "[]", "{}", "null"}
    return {
        "platform": _normal_dimension("platform", row.get("platform")),
        "country": _normal_dimension("country", row.get("country")),
        "language": _normal_dimension("language", row.get("language")),
        "profile_type": _normal_dimension("profile_type", row.get("profile_type")),
        "contact_available": "unknown" if contact is None else ("yes" if bool(contact) else "no"),
        "video_evidence": "yes" if evidence.get("representative_evidence") else "no",
    }


def candidate_set_distribution_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute the descriptive distribution after a downstream hard filter."""
    facet_names = (
        "platform", "country", "language", "profile_type", "contact_available", "video_evidence",
    )
    counters = {name: Counter() for name in facet_names}
    seen: set[int] = set()
    for item in items:
        try:
            kol_id = int(item.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            kol_id = 0
        if kol_id <= 0 or kol_id in seen:
            continue
        seen.add(kol_id)
        dimensions = item.get("candidate_facets") if isinstance(item.get("candidate_facets"), dict) else {}
        for name in facet_names:
            value = str(dimensions.get(name) or "unknown").strip().lower() or "unknown"
            counters[name][value] += 1
    for counts in counters.values():
        counts.setdefault("unknown", 0)
    return {
        "claim_status": "descriptive_only",
        "denominator": len(seen),
        "denominator_definition": "returned_canonical_candidates",
        "facets": {name: dict(sorted(counts.items())) for name, counts in counters.items()},
    }
