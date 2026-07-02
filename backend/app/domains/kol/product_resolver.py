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
        "pl", "t", "x", "full", "frame", "inch", "kit", "set", "new", "pro",
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
    # 顺序敏感:EF 要在 FE/E 前(避免 "ef mount" 被吃成 e-mount);XF 最特异放最前。
    ("X-mount", r"\bxf\b|x[- ]?mount|xf\s*卡口|x\s*卡口|富士|fuji"),
    ("EF-mount", r"\bef[- ]?mount\b|ef\s*卡口|佳能|canon"),
    ("FE-mount", r"\bfe[- ]?mount\b|fe\s*卡口|\be[- ]mount\b|e\s*卡口|索尼|sony"),
    ("Z-mount", r"\bz[- ]?mount\b|z\s*卡口|尼康|nikon"),
    ("L-mount", r"\bl[- ]?mount\b|l\s*卡口"),
    ("M43", r"m4/?3|松下|panasonic|olympus"),
]
_LENS_CONTEXT_RE = re.compile(r"evo|lab|air|prime|卡口|镜头|定焦|mm", re.I)


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
    if _LENS_CONTEXT_RE.search(low):
        for m in re.findall(r"(?<![0-9a-z.])(\d{2,3})(?![0-9])(?!\s*(?:ws|w\b|nit|寸|inch|mah|fps))", low):
            focals.add(int(m))
    return {f for f in focals if 8 <= f <= 800}


def _apply_hard_constraints(text: str, pool: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mount_req = _query_mount(text)
    focals_req = _query_focals(text)
    if not mount_req and not focals_req:
        return pool
    filtered: dict[str, dict[str, Any]] = {}
    for key, prod in pool.items():
        blob = f"{prod.get('sku') or ''} {prod.get('model_name') or ''}".lower()
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
        for key in ("sku", "model_name", "marketing_name")
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
    pool = _candidate_pool(text, probe_tokens)
    pool = _apply_hard_constraints(text, pool)
    if not pool:
        return None
    base = _query_tokens(text)
    score_tokens = [
        tok
        for tok in dict.fromkeys(base + probe_tokens)
        if len(tok) >= 2 and tok not in _STOPWORDS
    ]
    if not score_tokens:
        return None
    best: dict[str, Any] | None = None
    best_score = (0, 0, 0)
    for prod in pool.values():
        score = _score(prod, score_tokens)
        if score > best_score:
            best_score = score
            best = prod
    # Require a real match: ≥1 distinctive (len≥3) hit, or ≥2 total hits.
    # 注:tuple 现为 (strong, matched, series),阈值随之换序(strong=[0], matched=[1])。
    if not best or (best_score[0] < 1 and best_score[1] < 2):
        return None
    return {
        "sku": str(best.get("sku") or ""),
        "model_name": str(best.get("model_name") or ""),
        "marketing_name": str(best.get("marketing_name") or ""),
        "category_main": str(best.get("category_main") or ""),
        "category_detail": str(best.get("category_detail") or ""),
        "series": str(best.get("series") or ""),
        "price_usd": best.get("price_usd"),
        "description": str(best.get("description") or ""),
        "specs_line": _specs_line(best),
        "match_score": list(best_score),
    }
