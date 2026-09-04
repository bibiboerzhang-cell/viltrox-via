"""Product-resolution guards for smart-query planning."""
from __future__ import annotations

import re
from typing import Any

from app.core.coerce import _text
from app.domains.kol import product_resolver
from app.domains.kol.search_intent_text import affirmative_search_text


_PRODUCT_MENTION_HINT_RE = re.compile(
    r"(?<![a-z0-9])(?:dc[- ]?[a-z0-9]+|z\d(?:\s*pro)?|tc[- ]?\d|"
    r"\d{1,3}\s*mm|\d{1,3}\s*/\s*\d(?:\.\d)?|epic|memento|maestro|"
    r"vintage|evo|lab|air|增距镜)(?![a-z0-9])",
    re.IGNORECASE,
)


def catalog_unavailable_clarification() -> dict[str, Any]:
    return {
        "reason": "product_catalog_unavailable",
        "catalog_status": "unavailable",
        "retryable": True,
        "message": "产品目录暂时不可用，请稍后重试；无需修改产品名称或 SKU。",
        "suggestions": [],
    }


def product_constraints_conflict(
    inferred: dict[str, Any] | None,
    explicit: dict[str, Any],
) -> bool:
    """Reject stale explicit SKUs that fall outside the inferred product scope."""

    explicit_sku = _text(explicit.get("sku")).casefold()
    inferred_sku = _text((inferred or {}).get("sku")).casefold()
    if inferred_sku:
        return inferred_sku != explicit_sku
    family_skus = {
        _text(candidate).casefold()
        for key in ("focal_family_skus", "model_family_skus", "product_family_skus")
        for candidate in ((inferred or {}).get(key) or [])
        if _text(candidate)
    }
    return bool(family_skus) and explicit_sku not in family_skus


def product_identity_key(product: dict[str, Any]) -> str:
    sku = _text(product.get("sku")).casefold()
    if sku:
        return f"sku:{sku}"
    family_skus = sorted({
        _text(candidate).casefold()
        for key in ("focal_family_skus", "model_family_skus", "product_family_skus")
        for candidate in (product.get(key) or [])
        if _text(candidate)
    })
    if family_skus:
        return "family:" + "|".join(family_skus)
    return "name:" + _text(
        product.get("marketing_name") or product.get("model_name")
    ).casefold()


def _product_chunks(query_text: str) -> list[str]:
    return [
        _text(chunk)
        for chunk in re.split(
            r"[,，、;；]+|(?<![a-z0-9])(?:and|or)(?![a-z0-9])|[和与]",
            query_text,
            flags=re.IGNORECASE,
        )
        if _text(chunk)
    ]


def multiple_product_clarification(query_text: str) -> dict[str, Any] | None:
    """Detect two independently resolved products instead of picking one."""

    hinted = [
        chunk for chunk in _product_chunks(query_text)
        if _PRODUCT_MENTION_HINT_RE.search(chunk)
    ]
    if len(hinted) < 2:
        return None
    products: dict[str, dict[str, Any]] = {}
    for chunk in hinted[:6]:
        result = product_resolver.resolve_product_with_status(chunk)
        if result.get("status") == "catalog_unavailable":
            return catalog_unavailable_clarification()
        product = result.get("product")
        if isinstance(product, dict) and (key := product_identity_key(product)):
            products.setdefault(key, product)
    if len(products) < 2:
        return None
    return {
        "reason": "multiple_products_requested",
        "message": "一次搜索检测到多个产品。请保留一个产品，或不填产品、只描述要找的人。",
        "suggestions": [
            {
                "sku": _text(product.get("sku")),
                "name": _text(product.get("marketing_name") or product.get("model_name")),
            }
            for product in products.values()
        ],
    }


def _resolve_explicit_product(
    explicit_sku: str,
    inferred: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    explicit_result = product_resolver.resolve_product_sku_with_status(explicit_sku)
    if explicit_result.get("status") == "catalog_unavailable":
        return None, catalog_unavailable_clarification()
    explicit = explicit_result.get("product")
    if not explicit:
        return None, {
            "reason": "explicit_product_sku_not_in_catalog",
            "message": "所选产品不在当前产品目录中，请重新选择后再找达人。",
            "suggestions": [],
        }
    if product_constraints_conflict(inferred, explicit):
        return None, {
            "reason": "conflicting_product_constraints",
            "message": "输入内容与所选产品不一致，请确认一个产品后再找达人。",
            "suggestions": [],
        }
    return explicit, None


def resolve_requested_product(
    query_text: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve explicit SKU and free text to one catalog identity, or explain why not."""

    affirmative_query = affirmative_search_text(query_text)
    if multiple := multiple_product_clarification(affirmative_query):
        return None, multiple
    inferred_result = product_resolver.resolve_product_with_status(affirmative_query)
    if inferred_result.get("status") == "catalog_unavailable":
        return None, catalog_unavailable_clarification()
    inferred = inferred_result.get("product")
    explicit_sku = _text(body.get("product_sku") or body.get("productSku"))
    if explicit_sku:
        return _resolve_explicit_product(explicit_sku, inferred)
    if inferred:
        return inferred, None
    unresolved = product_resolver.unresolved_product_request_with_status(affirmative_query)
    if unresolved.get("status") == "catalog_unavailable":
        return None, catalog_unavailable_clarification()
    return None, unresolved.get("clarification")
