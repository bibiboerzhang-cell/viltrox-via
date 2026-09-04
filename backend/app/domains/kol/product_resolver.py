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
from contextvars import ContextVar
from typing import Any, Callable

from app.domains.costs.product_catalog import list_product_catalog
from app.domains.kol import product_focal_family
from app.domains.kol.product_resolver_projection import (
    focal_suggestions as _focal_suggestions,
    public_product_projection as _public_product_projection,
    specs_line as _specs_line,
)
from app.domains.kol.product_resolver_scoring import (
    official_duplicate_for_model_code,
    select_scored_product,
)
from app.domains.kol import product_resolver_catalog as resolver_catalog
from app.domains.kol.product_resolver_tokens import (
    COMPACT_PRO_RE as _COMPACT_PRO_RE,
    NIKON_CAMERA_CONTEXT_RE as _NIKON_CAMERA_CONTEXT_RE,
    STOPWORDS as _STOPWORDS,
    VILTROX_Z_MODEL_CONTEXT_RE as _VILTROX_Z_MODEL_CONTEXT_RE,
    model_code_mentions as _model_code_mentions,
    model_code_score_tokens as _model_code_score_tokens,
    looks_like_bare_sku as _looks_like_bare_sku,
    normkey as _normkey,
    query_apertures as _query_apertures,
    query_model_codes as _query_model_codes,
    query_tokens as _query_tokens,
    split_glued as _split_glued,
)


ProductCatalogUnavailable = resolver_catalog.ProductCatalogUnavailable
_CATALOG_RESOLUTION_STATUS: ContextVar[str] = ContextVar(
    "kol_product_catalog_resolution_status",
    default="unknown",
)


def _status_payload(
    resolver: Callable[[Any], Any], value: Any, *, value_key: str
) -> dict[str, Any]:
    token = _CATALOG_RESOLUTION_STATUS.set("unknown")
    try:
        result = resolver(value)
        catalog_status = _CATALOG_RESOLUTION_STATUS.get()
    finally:
        _CATALOG_RESOLUTION_STATUS.reset(token)
    if catalog_status == "unavailable":
        return {"status": "catalog_unavailable", "catalog_status": "unavailable", value_key: None}
    return {"status": "resolved" if result else "not_found", "catalog_status": "available", value_key: result}


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
# ── 硬约束(2026-07-02):卡口/焦段是否决条件,不是加分项。
# 事故:「90 evo XF 卡口」被 "evo" 一个词命中 EVO 系列 → 静默替身成 35mm FE(索尼),
# planner 还写着 TRUST THIS over the raw text,搜出来全是索尼人。
# 现在:query 明示卡口/焦段时,候选池先按硬约束过滤;滤空 = 诚实返回 None
# (planner 退回按 query 字面推人群,不再张冠李戴)。
_MOUNT_RULES: list[tuple[str, str]] = [
    # 顺序敏感:显式标准先于品牌软提示；不支持的标准保留原义并 fail closed。
    ("PL-mount", r"(?<![a-z0-9])pl[- ]?mount(?![a-z0-9])|pl\s*卡口"),
    ("RF-mount", r"(?<![a-z0-9])rf[- ]?mount(?![a-z0-9])|rf\s*卡口"),
    ("F-mount", r"(?<![a-z0-9])(?:nikon\s+)?f[- ]?mount(?![a-z0-9])|(?:尼康\s*)?f\s*卡口"),
    ("A-mount", r"(?<![a-z0-9])(?:sony\s+)?a[- ]?mount(?![a-z0-9])|(?:索尼\s*)?a\s*卡口"),
    ("G-mount", r"(?<![a-z0-9])(?:fuji\s+)?(?:g|gfx)[- ]?mount(?![a-z0-9])|(?:富士\s*)?(?:g|gfx)\s*卡口"),
    ("S-mount", r"(?<![a-z0-9])(?:panasonic\s+)?s[- ]?mount(?![a-z0-9])|(?:松下\s*)?s\s*卡口"),
    ("X-mount", r"(?<![a-z0-9])xf(?![a-z0-9])|(?<![a-z0-9])x[- ]?mount(?![a-z0-9])|xf\s*卡口|x\s*卡口|富士|fuji"),
    ("EF-mount", r"(?<![a-z0-9])ef[- ]?mount(?![a-z0-9])|ef\s*卡口"),
    ("FE-mount", r"(?<![a-z0-9])fe[- ]?mount(?![a-z0-9])|fe\s*卡口|(?<![a-z0-9])e[- ]mount(?![a-z0-9])|e\s*卡口|索尼|sony"),
    ("Z-mount", r"(?<![a-z0-9])z[- ]?mount(?![a-z0-9])|z\s*卡口|尼康|nikon"),
    ("L-mount", r"(?<![a-z0-9])l[- ]?mount(?![a-z0-9])|l\s*卡口"),
    ("M43", r"(?<![a-z0-9])m4/?3(?![a-z0-9])|松下|panasonic|olympus"),
]
_LENS_CONTEXT_RE = re.compile(
    r"\b(?:evo|lab|epic|air|raze|prime|macro)\b|卡口|镜头|定焦|mm|毫米|焦段|光圈", re.I)
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


# Product codes are often shorter than the catalog SKU and may contain a
# meaningful hyphen (``DC-A1``, ``DC-X3``, ``EF-E2``).  ``_split_glued`` is
# useful for fuzzy prose but destroys those identities (``DC-A1`` became
# ``dc / a / 1``), so keep a separate bounded code parser.  Prefixes are
# intentionally limited to product-like namespaces used by the catalog; this
# prevents ordinary phrases such as "top 10" from becoming product anchors.
_NAMED_FAMILY_CONTEXT_RE = re.compile(
    r"\b(?:set|kit|family|series|memento|maestro)\b|套装|整套|全套|系列|产品",
    re.IGNORECASE,
)


def _pro_is_product_series(text: str) -> bool:
    """Treat ``Pro`` as a series only when the query contains product evidence.

    ``pro`` used to be an unconditional stopword, which made real catalog rows
    such as 35mm Pro and 75mm Pro impossible to resolve when ``Pro`` lived only
    in the catalog's ``series`` column.  It cannot be an unconditional identity
    token either: "pro photographer" is a creator persona, not a product.  The
    focal/model/mount/lens context below separates those two cases.
    """

    low = str(text or "").lower()
    # Z1 Pro 是目录里唯一的单字母紧凑 Pro 型号；A7/R5/T5 Pro 等相机机身不是产品系列。
    compact = _COMPACT_PRO_RE.search(low)
    if compact:
        compact_code = _normkey(compact.group(1))
        return compact_code == "z1" and not (
            _NIKON_CAMERA_CONTEXT_RE.search(low)
            and not _VILTROX_Z_MODEL_CONTEXT_RE.search(low)
        )
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
    # Share the explicit-mm judgement with the focal-family resolver.  In
    # particular, ``35mm film`` / ``35mm 胶片`` / ``50mm equivalent`` describe
    # a format or field of view unless another lens/product anchor is present.
    focals = set(product_focal_family.explicit_focals(low))
    # 裸数字("90 evo")只在镜头语境下当焦段;排除功率/尺寸类单位粘连。
    # 注意不能用 \b:中文是 \w,「我们90」里 们/9 之间没有 word boundary,得用显式环视。
    # Operators often type split product families ("e vo", "l ab"); the focal
    # itself still comes from raw text so unrelated numbers are not promoted.
    if (
        _LENS_CONTEXT_RE.search(low)
        or bool(_explicit_product_series(low))
        or _pro_is_product_series(low)
    ):
        # 2026-08-26:这条分支过去自带一套裸数字判据,只挡功率/尺寸单位,不认时间跨度、
        # 区间、价格、排名。于是「24小时内更新的 evo 用户」被读出焦段 24,再撞上目录里
        # 没有 24mm EVO,弹出「请先选择正确产品」把一次正常搜索整个拦掉。
        # 两个焦段解析器从此共用 product_focal_family 那一套判据,不再各写各的。
        focals.update(product_focal_family.bare_focal_numbers(low, strict_word=False))
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
    if _query_model_codes(text):
        return True
    if series_present and _NAMED_FAMILY_CONTEXT_RE.search(text):
        return True
    has_lens_identity = bool(
        re.search(r"\b(?:viltrox|lens|prime|anamorphic|cine|t\d(?:\.\d)?|f/?\d(?:\.\d)?)\b|维卓|镜头|定焦", text)
    )
    return has_lens_identity and bool(_query_focals(text))


def _focal_clarification(text: str) -> dict[str, Any] | None:
    """焦段说清楚了但目录对不上时,如实告诉操作员,别让他等一趟注定零结果的搜索。

    只在**明确写了焦段**(带 mm 单位、或点名了卡口)时才拦——一个孤立数字如果目录里
    没有对应焦段,按「压根没提产品」处理,照常放行普通搜索。
    """
    if not (_query_focals(text) or product_focal_family.bare_focal_numbers(text)):
        return None
    decision = _focal_family_decision(text)
    if not decision:
        return None
    status = str(decision.get("status") or "")
    focal = decision.get("focal")
    if status == "mount_unavailable":
        mounts = decision.get("available_mounts") or []
        mount_text = " / ".join(str(item) for item in mounts) or "其他卡口"
        return {
            "reason": "focal_mount_not_in_catalog",
            "requested_series": "",
            "requested_model_code": "",
            "requested_focals": [focal],
            "requested_mount": str(decision.get("requested_mount") or ""),
            "message": f"没认出你要找的产品：{focal}mm 目录里没有这个卡口的版本，现有 {mount_text}。请挑一个再找达人。",
            "suggestions": _focal_suggestions(decision.get("rows")),
        }
    if status == "multiple_focals":
        listed = "、".join(f"{value}mm" for value in (decision.get("focals") or []))
        return {
            "reason": "multiple_focals_requested",
            "requested_series": "",
            "requested_model_code": "",
            "requested_focals": list(decision.get("focals") or []),
            "requested_mount": str(decision.get("requested_mount") or ""),
            "message": f"你一次提到了 {listed}，一次只能按一个焦段找达人。请挑一个再搜。",
            "suggestions": [],
        }
    if status == "no_catalog_match":
        available = [value for value in (decision.get("available_focals") or [])]
        try:
            nearest = sorted(available, key=lambda value: abs(int(value) - int(focal or 0)))[:3]
        except (TypeError, ValueError):
            nearest = available[:3]
        nearest_text = "、".join(f"{value}mm" for value in nearest) or "目录内其他焦段"
        return {
            "reason": "focal_not_in_catalog",
            "requested_series": "",
            "requested_model_code": "",
            "requested_focals": list(decision.get("focals") or []),
            "requested_mount": str(decision.get("requested_mount") or ""),
            "message": f"没认出你要找的产品：目录里没有 {focal}mm 的镜头。最接近的是 {nearest_text}。",
            "suggestions": [],
        }
    return None


def _unresolved_product_request_or_raise(query: str) -> dict[str, Any] | None:
    """Describe an explicit product request that did not resolve to the catalog.

    A missing catalog match is materially different from a generic request such
    as "find portrait photographers". Letting an LLM infer a SKU in that case has
    produced fabricated 24/26/28mm products. This helper lets the planner stop
    before provider invocation and ask the operator to select a real product.
    """
    text = str(query or "").strip()
    if not text:
        return None
    if len(_query_focals(text)) > 1:
        return _focal_clarification(text)
    alias_match = resolver_catalog.matched_product_alias(text)
    if alias_match:
        canonical = str(alias_match.get("canonical") or "").strip()
        products = resolver_catalog.catalog_products(list_product_catalog, limit=500)
        canonical_rows = resolver_catalog.canonical_catalog_rows(products, canonical)
        if not canonical_rows:
            requested_series = _explicit_product_series(canonical)
            return {
                "reason": "recognized_product_alias_not_in_catalog",
                "catalog_status": "available",
                "requested_alias": str(alias_match.get("alias") or ""),
                "requested_canonical": canonical,
                "requested_series": requested_series,
                "requested_model_code": "",
                "requested_focals": sorted(_query_focals(canonical)),
                "requested_mount": _query_mount(text),
                "message": (
                    f"已识别产品写法“{alias_match.get('alias')}”，但当前目录没有"
                    f"对应型号 {canonical}。请选择目录内产品或联系管理员同步目录。"
                ),
                "suggestions": resolver_catalog.missing_alias_suggestions(
                    products,
                    canonical,
                    series=requested_series,
                ),
            }
        clarification = resolver_catalog.alias_mount_clarification(
            alias_match, canonical_rows, requested_mount=_query_mount(text)
        )
        if clarification:
            return clarification
    series = _explicit_product_series(text)
    if series and not (
        _has_product_identity_anchor(text, [])
        or _PRODUCT_CATEGORY_CONTEXT_RE.search(text)
    ):
        series = ""
    if not series and _pro_is_product_series(text):
        series = "PRO"
    code_mentions = _model_code_mentions(text)
    model_code = code_mentions[0][1] if code_mentions else ""
    focals = sorted(_query_focals(text))
    mount = _query_mount(text)
    if not series and not model_code:
        # 已有的「系列/型号明确但目录没有」提示优先——它带着更具体的候选。
        # 只有连系列和型号码都没有(操作员只说了个焦段)时,才轮到焦段口径来解释。
        return _focal_clarification(text)

    probes = [value for value in (series, model_code, str(focals[0]) if focals else "") if value]
    suggestions: dict[str, dict[str, Any]] = {}
    products = resolver_catalog.catalog_products(list_product_catalog, limit=500)
    for product in products:
        sku = str(product.get("sku") or "")
        if not sku or sku.upper().startswith("IMAGE-AWARDS"):
            continue
        blob = " ".join(
            resolver_catalog.catalog_identity_values(product)
            + [str(product.get("series") or "")]
        ).lower()
        normalized_blob = _normkey(blob)
        if series and series.lower() not in normalized_blob:
            continue
        if probes and not any(_normkey(probe) in normalized_blob for probe in probes):
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
        "catalog_status": "available",
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


def unresolved_product_request(query: str) -> dict[str, Any] | None:
    """Compatibility helper; catalog outages remain a fail-closed ``None``."""

    try:
        clarification = _unresolved_product_request_or_raise(query)
    except ProductCatalogUnavailable:
        _CATALOG_RESOLUTION_STATUS.set("unavailable")
        return None
    _CATALOG_RESOLUTION_STATUS.set("available")
    return clarification


def unresolved_product_request_with_status(query: str) -> dict[str, Any]:
    return _status_payload(
        unresolved_product_request,
        query,
        value_key="clarification",
    )


def _apply_hard_constraints(text: str, pool: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mount_req = _query_mount(text)
    focals_req = _query_focals(text)
    apertures_req = _query_apertures(text)
    series_req = _explicit_product_series(text).lower()
    if not mount_req and not focals_req and not apertures_req and not series_req:
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
        aperture_blob = " ".join(
            str(prod.get(field) or "")
            for field in ("model_name", "marketing_name")
        )
        prod_apertures = _query_apertures(aperture_blob)
        is_lens_like = bool(prod_focals) or str(prod.get("category_main") or "").strip().lower() == "lens"
        prod_mount = str(prod.get("mount") or "").strip()
        if mount_req and is_lens_like and prod_mount and prod_mount != mount_req:
            # 镜头类:标注了卡口且与约束冲突 → 否决;未标注(mount 空)不否决,交给焦段/评分。
            continue
        if focals_req and prod_focals and not (prod_focals & focals_req):
            continue
        if apertures_req and prod_apertures and not (prod_apertures & apertures_req):
            continue
        filtered[key] = prod
    return filtered


def _nickname_probe_tokens(query: str) -> list[str]:
    raw = str(query or "").lower()
    keys = [_normkey(raw)] + [_normkey(tok) for tok in re.split(r"[^a-z0-9]+", raw) if tok]
    for key in keys:
        if key in _NICKNAME_PROBES:
            if (
                key in {"z1", "z1pro", "vintagez1"}
                and _NIKON_CAMERA_CONTEXT_RE.search(raw)
                and not _VILTROX_Z_MODEL_CONTEXT_RE.search(raw)
            ):
                continue
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
    for _normalized, display in _model_code_mentions(query):
        probes.add(display.lower())
        probes.add(display.lower().replace("-", " "))
    if probe_tokens:
        probes.update(tok for tok in probe_tokens if len(tok) >= 2)
        for i in range(len(probe_tokens) - 1):
            probes.add(f"{probe_tokens[i]} {probe_tokens[i + 1]}")
    pool: dict[str, dict[str, Any]] = {}
    successful_reads = 0
    last_error: ProductCatalogUnavailable | None = None
    for probe in probes:
        try:
            products = resolver_catalog.catalog_products(
                list_product_catalog,
                limit=25,
                query=probe,
            )
            successful_reads += 1
        except ProductCatalogUnavailable as exc:
            last_error = exc
            continue
        for prod in products:
            sku = str(prod.get("sku") or "")
            # 排污:IMAGE-AWARDS-* 是活动/人物/奖项页(被误当产品),绝不参与「按产品找人」解析。
            if sku and not sku.upper().startswith("IMAGE-AWARDS"):
                pool[sku] = prod
    if probes and successful_reads == 0 and last_error is not None:
        raise last_error

    # SQL text filtering intentionally does not inspect JSON specs.  A bounded
    # full-catalog pass adds official model variants such as DC-X2/DC-X3 without
    # widening fuzzy matching to unrelated description/highlight text.
    model_codes = [code for code, _display in _model_code_mentions(query)]
    if model_codes:
        products = resolver_catalog.catalog_products(list_product_catalog, limit=500)
        for prod in products:
            labels = resolver_catalog.catalog_variant_labels(prod)
            if not labels:
                continue
            label_keys = [_normkey(label) for label in labels]
            if not any(
                code == label_key or label_key.startswith(code)
                for code in model_codes
                for label_key in label_keys
            ):
                continue
            sku = str(prod.get("sku") or "")
            if sku and not sku.upper().startswith("IMAGE-AWARDS"):
                pool[sku] = prod
    return pool


def _row_words(prod: dict[str, Any]) -> tuple[str, str, set[str]]:
    """行匹配底料:(blob, blob_split_glued, 整词集合)。_score 与复审 F-1 守卫共用。"""
    blob = " ".join(
        str(prod.get(key) or "")
        for key in ("sku", "model_name", "marketing_name", "series")
    ).lower()
    variant_blob = " ".join(resolver_catalog.catalog_variant_labels(prod)).lower()
    if variant_blob:
        blob = f"{blob} {variant_blob}"
    blob_sp = _split_glued(blob)
    words = {w for w in re.split(r"[^a-z0-9.]+", blob) if w}
    words |= {w for w in re.split(r"[^a-z0-9.]+", blob_sp) if w}
    words.update(_query_model_codes(blob))
    return blob, blob_sp, words


def _score(prod: dict[str, Any], score_tokens: list[str]) -> tuple[int, int, int]:
    # 整词集合:同时用原始 blob 与拆粘连版切词。既认 "z1"(原词)又认 "65"(由 "65mm" 拆出),
    # 又杜绝短词子串误命中——曾让 "dp"→"a(dp)018"、"18"→"(18)→018" 把 NF-NEX 转接环
    # 错配成「EPIC 18mm 变宽」的搜索结果(generic photographer 检索词的真因)。
    blob, blob_sp, words = _row_words(prod)

    def _hit(tok: str) -> bool:
        if len(tok) < 3:
            return tok in words  # 短词:必须整词命中,不许子串
        return tok in words or tok in blob_sp or tok in blob  # 长词:允许子串(模糊覆盖)

    matched = sum(1 for tok in score_tokens if _hit(tok))
    strong = sum(1 for tok in score_tokens if len(tok) >= 3 and _hit(tok))
    # distinctive(strong)优先于总命中(matched):防通用/短词凑数的产品压过真正命中产品
    # 身份词(epic/anamorphic/macro/monitor…)的产品。series 长度仅末位平手 tiebreak。
    return strong, matched, len(str(prod.get("series") or ""))


def _official_duplicate_for_model_code(
    rows: list[dict[str, Any]],
    *,
    model_code: str,
) -> dict[str, Any] | None:
    return official_duplicate_for_model_code(
        rows,
        model_code=model_code,
        query_model_codes=_query_model_codes,
        row_words=_row_words,
        normkey=_normkey,
    )


def _model_code_family_projection(
    rows: list[dict[str, Any]],
    *,
    model_code: str,
    display_code: str,
    match_score: tuple[int, int, int],
) -> dict[str, Any] | None:
    """Represent one recognised model code without inventing a SKU choice."""

    matching = [row for row in rows if model_code in _query_model_codes(_row_words(row)[0])]
    if not matching:
        return None
    categories = {_normkey(row.get("category_main")) for row in matching if _normkey(row.get("category_main"))}
    if len(categories) > 1:
        return None
    richest = max(
        matching,
        key=lambda row: (
            bool(row.get("marketing_name")),
            len(str(row.get("description") or "")),
            len(str(row.get("model_name") or "")),
        ),
    )
    projection = _public_product_projection(richest, match_score=match_score)
    projection.update({
        "sku": "",
        "price_usd": None,
        "resolution_kind": "model_family",
        "resolved_model_code": display_code,
        "model_family_size": len(matching),
        "model_family_skus": [str(row.get("sku") or "") for row in matching][:12],
    })
    projection["specs_line"] = _specs_line(projection)
    return projection


def resolve_spec_family_product(query: str) -> dict[str, Any] | None:
    """Resolve focal+aperture prose without guessing an unspecified mount."""

    return resolver_catalog.resolve_spec_family_product(
        focals=sorted(_query_focals(query)),
        apertures=_query_apertures(query),
        series=_explicit_product_series(query),
        mount=_query_mount(query),
        row_words=_row_words,
        catalog_reader=list_product_catalog,
    )


def resolve_named_product_family(query: str) -> dict[str, Any] | None:
    """Resolve a named series/set to a bounded family instead of clarifying."""

    subfamily_match = re.search(
        r"(?<![a-z0-9])(memento|maestro)(?![a-z0-9])",
        query,
        re.IGNORECASE,
    )
    series = _explicit_product_series(query) or ("EPIC" if subfamily_match else "")
    # A single focal has a stronger, already-established family contract that
    # lists the exact focal candidates and mounts.  Named-series fallback is
    # for requests without that specificity (or an explicitly named set).
    if len(_query_focals(query)) == 1:
        return None
    if not series or not _NAMED_FAMILY_CONTEXT_RE.search(query):
        return None
    return resolver_catalog.resolve_named_product_family(
        query,
        series=series,
        subfamily=subfamily_match.group(1).lower() if subfamily_match else "",
        row_words=_row_words,
        catalog_reader=list_product_catalog,
    )


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


def _resolve_product_sku_or_raise(value: Any) -> dict[str, Any] | None:
    return resolver_catalog.exact_sku_product(
        value,
        catalog_reader=list_product_catalog,
    )


def resolve_product_sku(value: Any) -> dict[str, Any] | None:
    """Resolve one exact SKU; compatibility callers still fail closed on outages."""

    try:
        product = _resolve_product_sku_or_raise(value)
    except ProductCatalogUnavailable:
        _CATALOG_RESOLUTION_STATUS.set("unavailable")
        return None
    _CATALOG_RESOLUTION_STATUS.set("available")
    return product


def resolve_product_sku_with_status(value: Any) -> dict[str, Any]:
    """Status-aware exact-SKU resolution for request-path dependency reporting."""

    return _status_payload(resolve_product_sku, value, value_key="product")


def _focal_family_decision(query: str) -> dict[str, Any] | None:
    return resolver_catalog.focal_family_decision(
        query,
        mount=_query_mount(query),
        series=_explicit_product_series(query),
        catalog_reader=list_product_catalog,
    )


def resolve_focal_family_product(query: str) -> dict[str, Any] | None:
    """裸焦段兜底:「135」「55 z卡口」这类口语说法 → 焦段家族,或被卡口收窄后的唯一 SKU。

    只在常规解析(型号/系列/昵称打分)全无结果时才走这条路,所以它只会把
    「本来什么都认不出」变成「认出一个焦段」,不会改写任何已有的解析结论。

    同焦段多款时**不挑具体型号**——返回的投影 ``sku`` 为空、``price_usd`` 为 None,
    产品证据词只落在焦段本身("Viltrox 135mm")。挑一个具体 SKU 才是错配的来源。

    裸数字(没写 mm)即便焦段家族只有一行也不认具体 SKU:那一行是目录形状凑出来的,
    不是操作员点的。要认 SKU,得有卡口/系列线索,或者操作员自己写了单位。
    """
    return resolver_catalog.resolve_focal_family_product(
        query,
        mount=_query_mount(query),
        series=_explicit_product_series(query),
        catalog_reader=list_product_catalog,
    )


def _resolve_product_or_raise(query: str) -> dict[str, Any] | None:
    """Resolve free text while allowing catalog dependency errors to surface."""
    if len(_query_focals(query)) > 1:
        return None
    alias_match = resolver_catalog.matched_product_alias(query)
    if alias_match is not None:
        # A recognised, more-specific alias owns the decision.  If its
        # canonical identity is absent, do not fall through to a shorter/base
        # model (notably Z1 Pro -> Z1).
        return resolver_catalog.resolve_catalog_alias(
            query,
            alias_match,
            mount=_query_mount(query),
            catalog_reader=list_product_catalog,
        )
    resolved = _resolve_product_impl(query)
    if resolved is not None:
        return resolved
    spec_family = resolve_spec_family_product(query)
    if spec_family is not None:
        return spec_family
    named_family = resolve_named_product_family(query)
    if named_family is not None:
        return named_family
    if not (_query_focals(query) or product_focal_family.bare_focal_numbers(query)):
        return None
    return resolve_focal_family_product(query)


def resolve_product(query: str) -> dict[str, Any] | None:
    """Compatibility resolver; unknown products and catalog outages both fail closed."""

    try:
        product = _resolve_product_or_raise(query)
    except ProductCatalogUnavailable:
        _CATALOG_RESOLUTION_STATUS.set("unavailable")
        return None
    _CATALOG_RESOLUTION_STATUS.set("available")
    return product


def resolve_product_with_status(query: str) -> dict[str, Any]:
    """Resolve free text while preserving catalog availability as explicit state."""

    return _status_payload(resolve_product, query, value_key="product")


def _resolve_product_impl(query: str) -> dict[str, Any] | None:
    text = str(query or "").strip()
    if not text:
        return None
    probe_tokens = _nickname_probe_tokens(text)
    if not _has_product_identity_anchor(text, probe_tokens):
        return None
    if _looks_like_bare_sku(text):
        ambiguous, exact_product = resolver_catalog.exact_sku_resolution(
            text,
            catalog_reader=list_product_catalog,
        )
        if ambiguous or exact_product:
            return exact_product
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
    return select_scored_product(
        text=text,
        probe_tokens=probe_tokens,
        pool=pool,
        stopwords=_STOPWORDS,
        compact_pro_re=_COMPACT_PRO_RE,
        query_tokens=_query_tokens,
        model_code_mentions=_model_code_mentions,
        model_code_score_tokens=_model_code_score_tokens,
        pro_is_product_series=_pro_is_product_series,
        score_product=_score,
        official_duplicate_for_model_code=_official_duplicate_for_model_code,
        public_product_projection=_public_product_projection,
        row_words=_row_words,
    )
