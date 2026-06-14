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
    matched = sum(1 for tok in score_tokens if tok in blob_sp or tok in blob)
    strong = sum(1 for tok in score_tokens if len(tok) >= 3 and (tok in blob_sp or tok in blob))
    return matched, strong, len(str(prod.get("series") or ""))


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
    # Require a real match: ≥2 token hits, or at least one distinctive (len≥3) token.
    if not best or (best_score[0] < 2 and best_score[1] < 1):
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
