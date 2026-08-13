"""Free-text → real Viltrox product resolver for the KOL smart-search planner.

The smart-search box receives raw operator text such as "epic 65macro", "550pro"
or "Z1pro". On its own that text is meaningless to an LLM, which then emits generic
"camera gear reviewer" terms. This module fuzzily matches the text against the real
`vkpi_products` catalog (via product_catalog.list_product_catalog — read only) and
returns the resolved SKU plus marketing specs so the planner can inject them into the
LLM prompt.

Read only. This module never writes any score field; it only reads the catalog.
"""
from __future__ import annotations

import re
from typing import Any

from app.domains.costs.product_catalog import list_product_catalog


# Tokens too generic to score a product on their own.
_STOPWORDS = frozenset(
    {
        "mm", "f", "the", "a", "for", "and", "lens", "camera", "viltrox", "af",
        "pl", "t", "x", "full", "frame", "inch", "kit", "set", "new",
    }
)

# Curated nicknames → extra probe tokens that widen the catalog candidate pool so the
# scorer can rank the right SKU. Keyed on the normalised (alnum-only, lowercase) form
# of the operator phrase or any single operator token. Values are plain catalog probe
# tokens, NOT SKUs — the scorer still decides the winner, so a wrong nickname cannot
# silently pin the wrong product.
_NICKNAME_PROBES: dict[str, list[str]] = {
    "epic65macro": ["epic", "65", "macro"],
    "epic65": ["epic", "65", "macro"],
    "550pro": ["dc", "550", "pro", "monitor"],
    "dc550pro": ["dc", "550", "pro"],
    "dc550": ["dc", "550"],
    "550": ["dc", "550"],
    "z1pro": ["vintage", "z1", "pro", "flash"],
    "z1": ["vintage", "z1", "flash"],
    "vintagez1": ["vintage", "z1"],
}


def _normkey(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _split_glued(low: str) -> str:
    # "65macro" → "65 macro", "550pro" → "550 pro", "z1" stays "z 1" only at boundaries.
    spaced = re.sub(r"(?<=[0-9])(?=[a-z])", " ", low)
    spaced = re.sub(r"(?<=[a-z])(?=[0-9])", " ", spaced)
    return spaced


def _query_tokens(query: str) -> list[str]:
    spaced = _split_glued(str(query or "").lower())
    return [tok for tok in re.split(r"[^a-z0-9.]+", spaced) if tok]


# ── 硬约束(2026-07-02):卡口/焦段是否决条件,不是加分项。
# 事故:「90 evo XF 卡口」被 "evo" 一个词命中 EVO 系列 → 静默替身成 35mm FE(索尼),
# planner 还写着 TRUST THIS over the raw text,搜出来全是索尼人。
# 现在:query 明示卡口/焦段时,候选池先按硬约束过滤;滤空 = 诚实返回 None
# (planner 退回按 query 字面推人群,不再张冠李戴)。
_MOUNT_RULES: list[tuple[str, str]] = [
    # 顺序敏感:PL/RF/EF 要在 L/FE/E 前;品牌名 Canon 不能在 RF/EF 间代替用户选择。
    ("PL-mount", r"\bpl[- ]?mount\b|pl\s*卡口"),
    ("RF-mount", r"\brf[- ]?mount\b|rf\s*卡口"),
    ("X-mount", r"\bxf\b|x[- ]?mount|xf\s*卡口|x\s*卡口|富士|fuji"),
    ("EF-mount", r"\bef[- ]?mount\b|ef\s*卡口"),
    ("FE-mount", r"\bfe[- ]?mount\b|fe\s*卡口|\be[- ]mount\b|e\s*卡口|索尼|sony"),
    ("Z-mount", r"\bz[- ]?mount\b|z\s*卡口|尼康|nikon"),
    ("L-mount", r"\bl[- ]?mount\b|l\s*卡口"),
    ("M43", r"m4/?3|松下|panasonic|olympus"),
]
_LENS_CONTEXT_RE = re.compile(r"\b(?:evo|lab|epic|air|raze|prime|macro)\b|卡口|镜头|定焦|mm", re.I)
_EXPLICIT_PRODUCT_SERIES = ("evo", "lab", "epic", "vintage")
_PRODUCT_CATEGORY_CONTEXT_RE = re.compile(
    r"\b(?:lens|macro|anamorphic|cine|flash|monitor|prime)\b|镜头|微距|变形宽银幕|闪光|监视器|定焦",
    re.I,
)


def _explicit_product_series(text: str) -> str:
    """Return an operator-typed product series, never a word substring."""
    low = str(text or "").lower()
    tokens = set(_query_tokens(low))
    for series in _EXPLICIT_PRODUCT_SERIES:
        if series in tokens:
            return series.upper()
        split_pattern = r"(?<![a-z0-9])" + r"\s*".join(map(re.escape, series)) + r"(?![a-z0-9])"
        if re.search(split_pattern, low):
            return series.upper()
    return ""


def _pro_is_product_series(text: str) -> bool:
    """Treat ``Pro`` as a series only when the query contains product evidence.

    ``pro`` used to be an unconditional stopword, which made real catalog rows
    such as 35mm Pro and 75mm Pro impossible to resolve when ``Pro`` lived only
    in the catalog's ``series`` column.  It cannot be an unconditional identity
    token either: "pro photographer" is a creator persona, not a product.  The
    focal/model/mount/lens context below separates those two cases.
    """

    low = str(text or "").lower()
    if not re.search(r"(?<![a-z0-9])pro(?![a-z0-9])", low):
        return False
    return bool(
        re.search(r"\d{2,3}\s*mm", low)
        or re.search(r"(?:\d{2,3}\s*pro\b|\bpro\s*\d{2,3})(?!\s*(?:ws|w\b|nit|inch|mah|fps))", low)
        or _query_mount(low)
        or re.search(
            r"\b(?:af|lens|viltrox|evo|lab|epic|air|raze|prime|macro)\b|维卓|镜头|定焦|卡口",
            low,
        )
    )


def _query_mount(text: str) -> str:
    low = str(text or "").lower()
    for mount, pattern in _MOUNT_RULES:
        if re.search(pattern, low):
            return mount
    return ""


def _query_focals(text: str) -> set[int]:
    low = str(text or "").lower()
    focals = {int(m) for m in re.findall(r"(\d{2,3})\s*mm", low)}
    # 裸数字("90 evo")只在镜头语境下当焦段;排除功率/尺寸类单位粘连。
    # 注意不能用 \b:中文是 \w,「我们90」里 们/9 之间没有 word boundary,得用显式环视。
    # Operators often type split product families ("e vo", "l ab"); the focal
    # itself still comes from raw text so unrelated numbers are not promoted.
    if (
        _LENS_CONTEXT_RE.search(low)
        or bool(_explicit_product_series(low))
        or _pro_is_product_series(low)
    ):
        for m in re.findall(r"(?<![0-9a-z.])(\d{2,3})(?![0-9])(?!\s*(?:ws|w\b|nit|寸|inch|mah|fps))", low):
            focals.add(int(m))
    return {f for f in focals if 8 <= f <= 800}


def _has_product_identity_anchor(query: str, probe_tokens: list[str]) -> bool:
    """Require model evidence before binding a generic category to one SKU."""
    text = str(query or "").strip().lower()
    if probe_tokens:
        return True
    series_present = bool(_explicit_product_series(text))
    # A family plus a category/persona ("LAB macro", "EVO Sony") is not a
    # unique SKU. Require a focal/model anchor; ambiguous candidates are
    # rejected again after scoring below.
    if series_present and _query_focals(text):
        return True
    if _pro_is_product_series(text):
        return True
    if re.search(r"\b(?:dc[- ]?[a-z0-9-]{2,}|af[- ]?\d{2,3}[a-z0-9./-]*)\b", text):
        return True
    has_lens_identity = bool(
        re.search(r"\b(?:viltrox|lens|prime|anamorphic|cine|t\d(?:\.\d)?|f/?\d(?:\.\d)?)\b|维卓|镜头|定焦", text)
    )
    return has_lens_identity and bool(_query_focals(text))


def unresolved_product_request(query: str) -> dict[str, Any] | None:
    """Describe an explicit product request that did not resolve to the catalog.

    A missing catalog match is materially different from a generic request such
    as "find portrait photographers". Letting an LLM infer a SKU in that case has
    produced fabricated 24/26/28mm products. This helper lets the planner stop
    before provider invocation and ask the operator to select a real product.
    """
    text = str(query or "").strip()
    if not text:
        return None
    series = _explicit_product_series(text)
    if series and not (
        _has_product_identity_anchor(text, [])
        or _PRODUCT_CATEGORY_CONTEXT_RE.search(text)
    ):
        series = ""
    if not series and _pro_is_product_series(text):
        series = "PRO"
    model_code = ""
    code_match = re.search(r"\b(dc[- ]?[a-z0-9-]{2,}|af[- ]?\d{2,3}[a-z0-9./-]*)\b", text.lower())
    if code_match:
        model_code = code_match.group(1).upper().replace(" ", "-")
    focals = sorted(_query_focals(text))
    mount = _query_mount(text)
    if not series and not model_code:
        return None

    probes = [value for value in (series, model_code, str(focals[0]) if focals else "") if value]
    suggestions: dict[str, dict[str, Any]] = {}
    for probe in probes:
        try:
            products = list_product_catalog(limit=30, query=probe).get("products") or []
        except Exception:
            products = []
        for product in products:
            sku = str(product.get("sku") or "")
            if not sku or sku.upper().startswith("IMAGE-AWARDS"):
                continue
            blob = " ".join(
                str(value or "")
                for value in (
                    sku,
                    product.get("model_name"),
                    product.get("marketing_name"),
                    product.get("series"),
                )
            ).lower()
            if series and series.lower() not in _normkey(blob):
                continue
            suggestions[sku] = product

    requested_focal = focals[0] if focals else None

    def _distance(product: dict[str, Any]) -> tuple[int, str]:
        blob = f"{product.get('sku') or ''} {product.get('model_name') or ''}".lower()
        values = [int(value) for value in re.findall(r"(\d{2,3})\s*mm", blob)]
        distance = min((abs(value - requested_focal) for value in values), default=999) if requested_focal else 0
        return distance, str(product.get("model_name") or product.get("sku") or "")

    ordered = sorted(suggestions.values(), key=_distance)[:6]
    return {
        "reason": "explicit_product_not_in_catalog",
        "requested_series": series,
        "requested_model_code": model_code,
        "requested_focals": focals,
        "requested_mount": mount,
        "message": "没有在产品目录中找到这个明确型号，请先选择正确产品后再找达人。",
        "suggestions": [
            {
                "sku": product.get("sku"),
                "name": product.get("marketing_name") or product.get("model_name"),
                "mount": product.get("mount"),
                "series": product.get("series"),
            }
            for product in ordered
        ],
    }


def _apply_hard_constraints(text: str, pool: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mount_req = _query_mount(text)
    focals_req = _query_focals(text)
    series_req = _explicit_product_series(text).lower()
    if not mount_req and not focals_req and not series_req:
        return pool
    filtered: dict[str, dict[str, Any]] = {}
    for key, prod in pool.items():
        blob = " ".join(
            str(prod.get(field) or "")
            for field in ("sku", "model_name", "marketing_name", "series")
        ).lower()
        if series_req:
            product_tokens = set(_query_tokens(blob))
            normalized_series = _normkey(prod.get("series"))
            if series_req not in product_tokens and normalized_series != series_req:
                continue
        # 焦段两种写法都认:「90mm」和「90/2.2」(AF 90/2.2 XF 这类目录行没有 mm)。
        prod_focals = {int(m) for m in re.findall(r"(\d{2,3})\s*mm", blob)}
        prod_focals |= {int(m) for m in re.findall(r"(?<![0-9])(\d{2,3})\s*/(?=[0-9])", blob)}
        is_lens_like = bool(prod_focals) or str(prod.get("category_main") or "").strip().lower() == "lens"
        prod_mount = str(prod.get("mount") or "").strip()
        if mount_req and is_lens_like and prod_mount and prod_mount != mount_req:
            # 镜头类:标注了卡口且与约束冲突 → 否决;未标注(mount 空)不否决,交给焦段/评分。
            continue
        if focals_req and prod_focals and not (prod_focals & focals_req):
            continue
        filtered[key] = prod
    return filtered


def _nickname_probe_tokens(query: str) -> list[str]:
    raw = str(query or "").lower()
    keys = [_normkey(raw)] + [_normkey(tok) for tok in re.split(r"[^a-z0-9]+", raw) if tok]
    for key in keys:
        if key in _NICKNAME_PROBES:
            # A bare number embedded in a persona/count request is not a model
            # nickname (for example "find creators with 550 followers").
            if key == "550" and _normkey(raw) != "550" and not re.search(r"\b(?:dc|pro|monitor)\b|监视器", raw):
                continue
            return list(_NICKNAME_PROBES[key])
    return []


def _candidate_pool(query: str, probe_tokens: list[str]) -> dict[str, dict[str, Any]]:
    base = _query_tokens(query)
    probes: set[str] = set(tok for tok in base if len(tok) >= 2)
    for i in range(len(base) - 1):
        probes.add(f"{base[i]} {base[i + 1]}")
    if probe_tokens:
        probes.update(tok for tok in probe_tokens if len(tok) >= 2)
        for i in range(len(probe_tokens) - 1):
            probes.add(f"{probe_tokens[i]} {probe_tokens[i + 1]}")
    pool: dict[str, dict[str, Any]] = {}
    for probe in probes:
        try:
            products = list_product_catalog(limit=25, query=probe).get("products") or []
        except Exception:
            products = []
        for prod in products:
            sku = str(prod.get("sku") or "")
            # 排污:IMAGE-AWARDS-* 是活动/人物/奖项页(被误当产品),绝不参与「按产品找人」解析。
            if sku and not sku.upper().startswith("IMAGE-AWARDS"):
                pool[sku] = prod
    return pool


def _score(prod: dict[str, Any], score_tokens: list[str]) -> tuple[int, int, int]:
    blob = " ".join(
        str(prod.get(key) or "")
        for key in ("sku", "model_name", "marketing_name", "series")
    ).lower()
    blob_sp = _split_glued(blob)
    # 整词集合:同时用原始 blob 与拆粘连版切词。既认 "z1"(原词)又认 "65"(由 "65mm" 拆出),
    # 又杜绝短词子串误命中——曾让 "dp"→"a(dp)018"、"18"→"(18)→018" 把 NF-NEX 转接环
    # 错配成「EPIC 18mm 变宽」的搜索结果(generic photographer 检索词的真因)。
    words = {w for w in re.split(r"[^a-z0-9.]+", blob) if w}
    words |= {w for w in re.split(r"[^a-z0-9.]+", blob_sp) if w}

    def _hit(tok: str) -> bool:
        if len(tok) < 3:
            return tok in words  # 短词:必须整词命中,不许子串
        return tok in words or tok in blob_sp or tok in blob  # 长词:允许子串(模糊覆盖)

    matched = sum(1 for tok in score_tokens if _hit(tok))
    strong = sum(1 for tok in score_tokens if len(tok) >= 3 and _hit(tok))
    # distinctive(strong)优先于总命中(matched):防通用/短词凑数的产品压过真正命中产品
    # 身份词(epic/anamorphic/macro/monitor…)的产品。series 长度仅末位平手 tiebreak。
    return strong, matched, len(str(prod.get("series") or ""))


def _specs_line(prod: dict[str, Any]) -> str:
    """One compact English specs line for the LLM prompt (model · price · category · series · desc)."""
    parts: list[str] = []
    name = str(prod.get("marketing_name") or prod.get("model_name") or "").strip()
    if name:
        parts.append(name)
    price = prod.get("price_usd")
    try:
        if price is not None and float(price) > 0:
            parts.append(f"${float(price):,.0f} USD")
    except (TypeError, ValueError):
        pass
    cat = str(prod.get("category_main") or "").strip()
    detail = str(prod.get("category_detail") or "").strip()
    series = str(prod.get("series") or "").strip()
    cat_bits = [bit for bit in (cat, detail if detail and detail != cat else "", series) if bit]
    if cat_bits:
        parts.append("category: " + " / ".join(dict.fromkeys(cat_bits)))
    desc = " ".join(str(prod.get("description") or "").split())[:280]
    if desc:
        parts.append(desc)
    return " · ".join(parts)


def _public_product_projection(
    product: dict[str, Any],
    *,
    match_score: tuple[int, int, int],
) -> dict[str, Any]:
    """Return the bounded catalog fields shared by text and exact-SKU resolution."""
    return {
        "sku": str(product.get("sku") or ""),
        "model_name": str(product.get("model_name") or ""),
        "marketing_name": str(product.get("marketing_name") or ""),
        "category_main": str(product.get("category_main") or ""),
        "category_detail": str(product.get("category_detail") or ""),
        "series": str(product.get("series") or ""),
        "price_usd": product.get("price_usd"),
        "description": str(product.get("description") or ""),
        "specs_line": _specs_line(product),
        "match_score": list(match_score),
    }


def _unique_exact_sku_product(
    products: Any,
    value: Any,
) -> dict[str, Any] | None:
    """Return one exact normalized SKU row without accepting fuzzy matches."""
    normalized = _normkey(value)
    if not normalized:
        return None
    matches = [
        product
        for product in (products or [])
        if isinstance(product, dict)
        and not str(product.get("sku") or "").upper().startswith("IMAGE-AWARDS")
        and _normkey(product.get("sku")) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _catalog_exact_sku_products(value: Any) -> list[dict[str, Any]]:
    """Read all bounded catalog rows sharing one exact normalized SKU key."""
    products = list_product_catalog(limit=500).get("products") or []
    normalized = _normkey(value)
    return [
        product
        for product in products
        if isinstance(product, dict)
        and not str(product.get("sku") or "").upper().startswith("IMAGE-AWARDS")
        and _normkey(product.get("sku")) == normalized
    ]


def _looks_like_bare_sku(value: Any) -> bool:
    """Limit the full-catalog exact check to short operator-typed model codes."""
    text = str(value or "").strip()
    return bool(
        len(text.split()) <= 2
        and re.fullmatch(r"[a-z][a-z0-9._/+ -]*", text, re.IGNORECASE)
        and any(char.isdigit() for char in text)
    )


def resolve_product_sku(value: Any) -> dict[str, Any] | None:
    """Resolve only one exact normalized catalog SKU; unknown/ambiguous values fail closed."""
    text = str(value or "").strip()
    normalized = _normkey(text)
    if not normalized or len(text) > 240:
        return None
    try:
        # A filtered SQL LIKE can hide punctuation variants (``DC-550`` versus
        # ``DC_550``) and make an ambiguous normalized key look unique. Check
        # the bounded catalog snapshot so uniqueness is evaluated consistently.
        matches = _catalog_exact_sku_products(normalized)
    except Exception:
        return None
    if len(matches) != 1:
        return None
    product = matches[0]
    return _public_product_projection(
        product,
        match_score=(1, 1, len(str(product.get("series") or ""))),
    )


def resolve_product(query: str) -> dict[str, Any] | None:
    """Resolve operator free text to a real catalog product, or None.

    Returns a dict with sku / model_name / marketing_name / category_main /
    category_detail / series / price_usd / description / specs_line / match_score.
    Read only; never raises on catalog failure (returns None instead).
    """
    text = str(query or "").strip()
    if not text:
        return None
    probe_tokens = _nickname_probe_tokens(text)
    if not _has_product_identity_anchor(text, probe_tokens):
        return None
    if _looks_like_bare_sku(text):
        try:
            exact_products = _catalog_exact_sku_products(text)
        except Exception:
            exact_products = []
        if len(exact_products) > 1:
            return None
        if exact_products:
            exact_product = exact_products[0]
            return _public_product_projection(
                exact_product,
                match_score=(1, 1, len(str(exact_product.get("series") or ""))),
            )
    pool = _candidate_pool(text, probe_tokens)
    pool = _apply_hard_constraints(text, pool)
    if not pool:
        return None
    exact_product = _unique_exact_sku_product(pool.values(), text)
    if exact_product is not None:
        return _public_product_projection(
            exact_product,
            match_score=(1, 1, len(str(exact_product.get("series") or ""))),
        )
    base = _query_tokens(text)
    product_pro = _pro_is_product_series(text)
    score_tokens = [
        tok
        for tok in dict.fromkeys(base + probe_tokens)
        if len(tok) >= 2
        and tok not in _STOPWORDS
        and (tok != "pro" or product_pro)
    ]
    if not score_tokens:
        return None
    scored: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for prod in pool.values():
        scored.append((_score(prod, score_tokens), prod))
    if not scored:
        return None
    best_primary = max((score[0], score[1]) for score, _prod in scored)
    # One family/category hit cannot identify a SKU, and equally-scored catalog
    # variants must ask for more detail instead of relying on row order.
    if best_primary[1] < 2:
        return None
    winners = [(score, prod) for score, prod in scored if (score[0], score[1]) == best_primary]
    if len(winners) != 1:
        return None
    best_score, best = winners[0]
    return _public_product_projection(best, match_score=best_score)
