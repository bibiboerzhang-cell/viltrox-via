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
from app.domains.kol import product_focal_family
from app.domains.kol.product_resolver_projection import (
    public_product_projection as _public_product_projection,
    specs_line as _specs_line,
)


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


_COMPACT_PRO_RE = re.compile(r"(?<![a-z0-9])([a-z]\d{1,3})\s*pro(?![a-z0-9])")

# Product codes are often shorter than the catalog SKU and may contain a
# meaningful hyphen (``DC-A1``, ``DC-X3``, ``EF-E2``).  ``_split_glued`` is
# useful for fuzzy prose but destroys those identities (``DC-A1`` became
# ``dc / a / 1``), so keep a separate bounded code parser.  Prefixes are
# intentionally limited to product-like namespaces used by the catalog; this
# prevents ordinary phrases such as "top 10" from becoming product anchors.
_MODEL_CODE_RE = re.compile(
    r"(?<![a-z0-9])(?P<code>(?:dc|af|mf|ef|nf|dg|vl|epic|z)[-_ ]?(?:[a-z]*\d[a-z0-9]*))(?![a-z0-9])",
    re.IGNORECASE,
)
_MODEL_CODE_PREFIXES = ("epic", "dc", "af", "mf", "ef", "nf", "dg", "vl", "z")
_VILTROX_Z_MODEL_CONTEXT_RE = re.compile(
    r"\bviltrox\b|\bvintage\b|唯卓仕|维卓仕?",
    re.IGNORECASE,
)
_APERTURE_RE = re.compile(
    r"(?<![a-z0-9])(?P<kind>[ft])\s*/?\s*(?P<value>\d{1,2}(?:\.\d+)?)(?![a-z0-9])",
    re.IGNORECASE,
)
_NAMED_FAMILY_CONTEXT_RE = re.compile(
    r"\b(?:set|kit|family|series|memento|maestro)\b|套装|整套|全套|系列|产品",
    re.IGNORECASE,
)


def _model_code_mentions(value: Any) -> list[tuple[str, str]]:
    """Return ``(normalised, display)`` product-code mentions in input order."""

    source_text = str(value or "")
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _MODEL_CODE_RE.finditer(source_text):
        raw = match.group("code")
        normalized = _normkey(raw)
        # ``AF 85mm`` is a lens type + focal specification, not a compact
        # model code.  Let focal/aperture family resolution preserve all mount
        # variants instead of treating this fragment like ``DC-A1``.
        if re.fullmatch(r"(?:af|mf)\d{1,3}mm", normalized):
            continue
        # ``Z1`` is the one compact Z-series identity in the current Viltrox
        # catalog.  Bare Nikon camera bodies (Z6/Z8/Z50/...) are creator-gear
        # context, not missing Viltrox SKUs; treating every ``Z + number`` as a
        # product code turned valid KOL searches into a clarification wall.
        # An explicit Viltrox/Vintage anchor still fails closed for a future or
        # misspelled Viltrox Z model instead of silently treating it as prose.
        if (
            re.fullmatch(r"z\d+[a-z0-9]*", normalized)
            and not re.fullmatch(r"z1(?:pro[a-z0-9]*)?", normalized)
            and not _VILTROX_Z_MODEL_CONTEXT_RE.search(source_text)
        ):
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        display = re.sub(r"[-_ ]+", "-", raw).upper()
        if not re.search(r"[-_ ]", raw):
            prefix = next(
                (item for item in _MODEL_CODE_PREFIXES if normalized.startswith(item) and len(normalized) > len(item)),
                "",
            )
            if prefix and prefix != "z":
                display = f"{prefix.upper()}-{normalized[len(prefix):].upper()}"
        output.append((normalized, display))
    return output


def _query_model_codes(value: Any) -> list[str]:
    return [normalized for normalized, _display in _model_code_mentions(value)]


def _model_code_score_tokens(model_codes: list[str]) -> list[str]:
    output = list(model_codes)
    for code in model_codes:
        prefix = next(
            (item for item in _MODEL_CODE_PREFIXES if code.startswith(item) and len(code) > len(item)),
            "",
        )
        if len(prefix) >= 2:
            output.append(prefix)
    return output


def _query_apertures(value: Any) -> set[tuple[str, float]]:
    apertures: set[tuple[str, float]] = set()
    for match in _APERTURE_RE.finditer(str(value or "")):
        try:
            apertures.add((match.group("kind").lower(), round(float(match.group("value")), 3)))
        except (TypeError, ValueError):
            continue
    return apertures


def _pro_is_product_series(text: str) -> bool:
    """Treat ``Pro`` as a series only when the query contains product evidence.

    ``pro`` used to be an unconditional stopword, which made real catalog rows
    such as 35mm Pro and 75mm Pro impossible to resolve when ``Pro`` lived only
    in the catalog's ``series`` column.  It cannot be an unconditional identity
    token either: "pro photographer" is a creator persona, not a product.  The
    focal/model/mount/lens context below separates those two cases.
    """

    low = str(text or "").lower()
    # 2026-08-24 R2:单字母+数字的紧凑型号紧跟 pro("z1 pro"/"a7 pro"/粘连 "z1pro")也是产品证据。
    # 整词锚定防 "web3 pro" 人设词子串;多字母型号("dc550 pro")由既有 \d{2,3}\s*pro 覆盖。
    # 粘连形态没有独立 "pro" 词,所以在 standalone-pro 闸之前判。误配防线见 resolve_product
    # 的复审 F-1 守卫(紧凑码必须命中赢家,防他牌 "a7 pro"+品类词凑赢)。
    compact = _COMPACT_PRO_RE.search(low)
    if compact:
        compact_code = _normkey(compact.group(1))
        if (
            re.fullmatch(r"z\d+[a-z0-9]*", compact_code)
            and compact_code != "z1"
            and not _VILTROX_Z_MODEL_CONTEXT_RE.search(low)
        ):
            return False
        return True
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


def _focal_suggestions(rows: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    return [
        {
            "sku": row.get("sku"),
            "name": row.get("marketing_name") or row.get("model_name"),
            "mount": row.get("mount"),
            "series": row.get("series"),
        }
        for row in list(rows or [])[: max(1, int(limit))]
    ]


def _focal_clarification(text: str) -> dict[str, Any] | None:
    """焦段说清楚了但目录对不上时,如实告诉操作员,别让他等一趟注定零结果的搜索。

    只在**明确写了焦段**(带 mm 单位、或点名了卡口)时才拦——一个孤立数字如果目录里
    没有对应焦段,按「压根没提产品」处理,照常放行普通搜索。
    """
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


def _row_words(prod: dict[str, Any]) -> tuple[str, str, set[str]]:
    """行匹配底料:(blob, blob_split_glued, 整词集合)。_score 与复审 F-1 守卫共用。"""
    blob = " ".join(
        str(prod.get(key) or "")
        for key in ("sku", "model_name", "marketing_name", "series")
    ).lower()
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
    """Select a canonical official row only when duplicates are the same model.

    The catalog currently contains both an official ``DC-A1-...`` row and a
    legacy ``VL-MON015`` row whose model name is also DC-A1.  That is duplicate
    catalog lineage, not product ambiguity.  Conversely, mount variants must
    remain a family and are never collapsed here.
    """

    matching = [row for row in rows if model_code in _query_model_codes(_row_words(row)[0])]
    if not matching:
        return None
    categories = {_normkey(row.get("category_main")) for row in matching if _normkey(row.get("category_main"))}
    mounts = {_normkey(row.get("mount")) for row in matching if _normkey(row.get("mount"))}
    if len(categories) > 1 or len(mounts) > 1:
        return None
    def _is_official(row: dict[str, Any]) -> bool:
        if str(row.get("status") or "").strip().lower() == "official":
            return True
        try:
            return float(row.get("source_confidence") or 0) >= 0.9
        except (TypeError, ValueError):
            return False

    official = [row for row in matching if _is_official(row)]
    return official[0] if len(official) == 1 else None


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


def _format_aperture(aperture: tuple[str, float]) -> str:
    kind, value = aperture
    return f"{kind.upper()}{value:g}"


def resolve_spec_family_product(query: str) -> dict[str, Any] | None:
    """Resolve focal+aperture prose to a model family without guessing mount.

    ``35mm F1.2`` identifies materially more than the whole 35mm catalog, but
    it still does not select Sony E versus Nikon Z.  Preserve both facts: keep
    SKU empty while projecting the shared focal, aperture, series and candidate
    SKUs.  This gives planning a useful capability without forcing a needless
    clarification.
    """

    focals = sorted(_query_focals(query))
    apertures = _query_apertures(query)
    if len(focals) != 1 or len(apertures) != 1:
        return None
    try:
        rows = list(product_focal_family.focal_family_index(list_product_catalog).get(focals[0]) or [])
    except Exception:
        return None
    if not rows:
        return None
    matching = []
    for row in rows:
        row_apertures = _query_apertures(
            f"{row.get('model_name') or ''} {row.get('marketing_name') or ''}"
        )
        if row_apertures & apertures:
            matching.append(row)
    series = _explicit_product_series(query)
    if series:
        narrowed = [
            row for row in matching
            if series.lower() in _row_words(row)[2]
            or _normkey(row.get("series")) == series.lower()
        ]
        if narrowed:
            matching = narrowed
    mount = _query_mount(query)
    if mount:
        matching = [row for row in matching if str(row.get("mount") or "").strip() == mount]
    if not matching:
        return None
    if len(matching) == 1:
        projection = _public_product_projection(
            matching[0],
            match_score=(2, 2, len(str(matching[0].get("series") or ""))),
        )
        projection.update({
            "resolution_kind": "focal_aperture_unique",
            "focal_mm": focals[0],
            "requested_aperture": _format_aperture(next(iter(apertures))),
        })
        return projection

    decision = {"rows": matching, "focal": focals[0]}
    projection = product_focal_family.family_projection(decision)
    aperture_label = _format_aperture(next(iter(apertures)))
    family_word = str(projection.get("series") or "").strip()
    name = " ".join(part for part in ("Viltrox", f"{focals[0]}mm", aperture_label, family_word) if part)
    projection.update({
        "model_name": name,
        "marketing_name": name,
        "description": (
            f"{focals[0]}mm {aperture_label} 产品家族共 {len(matching)} 个目录记录。"
            "操作员未指定卡口，按共享光学能力理解，不代选具体 SKU。"
        ),
        # Keep the established compatibility kind while exposing the stronger
        # resolution basis to new consumers.
        "resolution_kind": "focal_family",
        "resolution_basis": "focal_aperture_family",
        "requested_aperture": aperture_label,
        "match_score": [2, 2, 0],
    })
    projection["specs_line"] = _specs_line(projection)
    return projection


def resolve_named_product_family(query: str) -> dict[str, Any] | None:
    """Resolve a named series/set to a bounded family instead of clarifying."""

    series = _explicit_product_series(query)
    # A single focal has a stronger, already-established family contract that
    # lists the exact focal candidates and mounts.  Named-series fallback is
    # for requests without that specificity (or an explicitly named set).
    if len(_query_focals(query)) == 1:
        return None
    if not series or not _NAMED_FAMILY_CONTEXT_RE.search(query):
        return None
    try:
        products = list_product_catalog(limit=500).get("products") or []
    except Exception:
        return None
    rows: list[dict[str, Any]] = []
    for row in products:
        if not isinstance(row, dict) or str(row.get("sku") or "").upper().startswith("IMAGE-AWARDS"):
            continue
        _blob, _blob_sp, words = _row_words(row)
        if series.lower() in words or _normkey(row.get("series")) == series.lower():
            rows.append(row)
    for subfamily in ("memento", "maestro"):
        if re.search(rf"(?<![a-z0-9]){subfamily}(?![a-z0-9])", query, re.IGNORECASE):
            rows = [row for row in rows if subfamily in _row_words(row)[2]]
    if not rows:
        return None
    if len(rows) == 1:
        projection = _public_product_projection(
            rows[0], match_score=(2, 2, len(str(rows[0].get("series") or "")))
        )
        projection["resolution_kind"] = "named_product_family_exact"
        return projection

    categories = {str(row.get("category_main") or "").strip() for row in rows if str(row.get("category_main") or "").strip()}
    details = {str(row.get("category_detail") or "").strip() for row in rows if str(row.get("category_detail") or "").strip()}
    category = next(iter(categories)) if len(categories) == 1 else ""
    detail = next(iter(details)) if len(details) == 1 else ""
    capability_name = "cinema lens" if all(
        any(term in _row_words(row)[0] for term in ("cine", "anamorphic")) for row in rows
    ) else (category.lower() if category else "product")
    projection = {
        "sku": "",
        "model_name": f"Viltrox {series} {capability_name} family",
        "marketing_name": f"Viltrox {series} {capability_name} family",
        "category_main": category,
        "category_detail": detail,
        "series": series,
        "price_usd": None,
        "description": f"{series} 产品家族共 {len(rows)} 个目录记录，未代选具体 SKU。",
        "resolution_kind": "named_product_family",
        "product_family_size": len(rows),
        "product_family_skus": [str(row.get("sku") or "") for row in rows][:12],
        "match_score": [1, 1, 0],
    }
    projection["specs_line"] = _specs_line(projection)
    return projection


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


def _focal_family_decision(query: str) -> dict[str, Any] | None:
    """焦段判定(读目录,失败静默 None)。卡口/系列线索由本模块既有解析器提供。"""
    try:
        return product_focal_family.focal_family_decision(
            query,
            mount=_query_mount(query),
            series=_explicit_product_series(query),
            # 全链路只认本模块这一份目录读取器,打桩/降级行为与既有解析路径完全一致。
            catalog_reader=list_product_catalog,
        )
    except Exception:
        return None


def resolve_focal_family_product(query: str) -> dict[str, Any] | None:
    """裸焦段兜底:「135」「55 z卡口」这类口语说法 → 焦段家族,或被卡口收窄后的唯一 SKU。

    只在常规解析(型号/系列/昵称打分)全无结果时才走这条路,所以它只会把
    「本来什么都认不出」变成「认出一个焦段」,不会改写任何已有的解析结论。

    同焦段多款时**不挑具体型号**——返回的投影 ``sku`` 为空、``price_usd`` 为 None,
    产品证据词只落在焦段本身("Viltrox 135mm")。挑一个具体 SKU 才是错配的来源。

    裸数字(没写 mm)即便焦段家族只有一行也不认具体 SKU:那一行是目录形状凑出来的,
    不是操作员点的。要认 SKU,得有卡口/系列线索,或者操作员自己写了单位。
    """
    decision = _focal_family_decision(query)
    if not decision:
        return None
    status = str(decision.get("status") or "")
    if status == "unique":
        product = decision.get("product")
        if not isinstance(product, dict):
            return None
        projection = _public_product_projection(
            product,
            match_score=(1, 1, len(str(product.get("series") or ""))),
        )
        # 标签必须等于实际发生的事:没有卡口线索却写 "narrowed_by_mount",
        # 排障时会把「目录里只有一支」误读成「操作员点了卡口」。
        narrowed_by = str(decision.get("narrowed_by") or "")
        projection["resolution_kind"] = {
            "mount": "focal_narrowed_by_mount",
            "series": "focal_narrowed_by_series",
        }.get(narrowed_by, "focal_single_in_catalog")
        projection["focal_mm"] = decision.get("focal")
        return projection
    if status != "family":
        return None
    family = product_focal_family.family_projection(decision)
    return {**family, "specs_line": _specs_line(family), "match_score": [1, 1, 0]}


def resolve_product(query: str) -> dict[str, Any] | None:
    """Resolve operator free text to a real catalog product, or None.

    Returns a dict with sku / model_name / marketing_name / category_main /
    category_detail / series / price_usd / description / specs_line / match_score.
    Read only; never raises on catalog failure (returns None instead).
    """
    resolved = _resolve_product_impl(query)
    if resolved is not None:
        return resolved
    spec_family = resolve_spec_family_product(query)
    if spec_family is not None:
        return spec_family
    named_family = resolve_named_product_family(query)
    if named_family is not None:
        return named_family
    return resolve_focal_family_product(query)


def _resolve_product_impl(query: str) -> dict[str, Any] | None:
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
    model_code_mentions = _model_code_mentions(text)
    product_pro = _pro_is_product_series(text)
    score_tokens = [
        tok
        for tok in dict.fromkeys(
            base
            + probe_tokens
            + _model_code_score_tokens([code for code, _display in model_code_mentions])
        )
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
    if len(winners) > 1 and len(model_code_mentions) == 1:
        model_code, display_code = model_code_mentions[0]
        winner_rows = [prod for _score_value, prod in winners]
        canonical = _official_duplicate_for_model_code(
            winner_rows,
            model_code=model_code,
        )
        if canonical is not None:
            projection = _public_product_projection(
                canonical,
                match_score=next(score for score, prod in winners if prod is canonical),
            )
            projection.update({
                "resolution_kind": "model_code_exact",
                "resolved_model_code": display_code,
            })
            return projection
        # Do not collapse genuinely different model variants into a synthetic
        # family merely because they share a compact code.  The deterministic
        # base-model tiebreak below still handles ``Z1`` while same-rank
        # variants such as ``Z1 PRO-N`` / ``Z1 PRO-F`` fail closed.
    if len(winners) > 1:
        # 2026-08-24 R2:(strong, matched) 全平时,用 _score 早已算好的末位 tiebreak
        # (series 长度)选唯一赢家——取 series 最短者,即基础款。方向是确定性的且
        # 不放松匹配:query 里若真含系列词(pro 等),该系列行会在 strong/matched 上
        # 直接胜出,根本走不到这里;此处偏向基础款意味着绝不凭空替操作者补一个
        # "Pro"。tiebreak 后仍并列(同系列多卡口行等)→ 保持 fail-closed 返回 None。
        min_series_len = min(score[2] for score, _prod in winners)
        winners = [(score, prod) for score, prod in winners if score[2] == min_series_len]
    if len(winners) != 1:
        return None
    best_score, best = winners[0]
    # 2026-08-24 复审 F-1:紧凑码("a7 pro")解锁的 "pro" 只有在该紧凑码本身也命中赢家时才作数——
    # 否则他牌紧凑码 + 一个品类词(如 flash)就能把 Pro 系列行凑成赢家。仅当赢家确实靠 "pro"
    # 词命中(blob 含 pro)且查询里的紧凑码全都不在赢家词集时 fail-closed。
    compact_codes = _COMPACT_PRO_RE.findall(str(text or "").lower())
    if compact_codes and "pro" in score_tokens:
        _blob, _blob_sp, winner_words = _row_words(best)
        if "pro" in winner_words and not any(code in winner_words for code in compact_codes):
            return None
    return _public_product_projection(best, match_score=best_score)
