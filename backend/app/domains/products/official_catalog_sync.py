"""Daily sync of the public viltrox.com Shopify product catalog."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

import httpx

from app.core.logging import get_logger
from app.db.connection import get_conn


logger = get_logger(__name__)

OFFICIAL_CATALOG_URL = "https://viltrox.com/products.json?limit=250&page=1"
OFFICIAL_PRODUCT_BASE_URL = "https://viltrox.com/products/"
SHOPIFY_PRODUCT_LIMIT = 250
HARD_TIMEOUT_SECONDS = 30.0
MAX_FETCH_ATTEMPTS = 3
USER_AGENT = "ViltroxMarketing-OfficialCatalogSync/1.0 (+https://viltrox.com)"
SOURCE_FILE = "official:viltrox.com/products.json"


class OfficialCatalogSyncError(RuntimeError):
    """Raised after a failed catalog run has been recorded."""

    def __init__(self, message: str, *, error_type: str = "other") -> None:
        super().__init__(message)
        self.error_type = error_type


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


@dataclass(frozen=True)
class CatalogFeed:
    products: tuple[dict[str, Any], ...]
    items: tuple[dict[str, Any], ...]
    variant_count: int

    @property
    def variants(self) -> tuple[dict[str, Any], ...]:
        """Compatibility alias for the earlier, misleading product-row name."""
        return self.items


@dataclass(frozen=True)
class PlannedCatalogItem:
    item: dict[str, Any]
    generated_sku: str
    existed: bool
    changed: bool


@dataclass(frozen=True)
class CatalogPlan:
    items: tuple[PlannedCatalogItem, ...]
    missing_skus: tuple[str, ...]
    mark_unlisted_skus: tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"official-catalog-{stamp}-{uuid.uuid4().hex[:12]}"


def _text(value: Any, *, limit: int = 0) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit > 0 else text


def _html_text(value: Any, *, limit: int = 8000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(_text(value))
        parser.close()
    except Exception:
        return ""
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:limit]


def _price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        price = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise OfficialCatalogSyncError(f"invalid Shopify variant price: {value!r}", error_type="validation")
    if price < 0:
        raise OfficialCatalogSyncError(f"negative Shopify variant price: {value!r}", error_type="validation")
    return price


def _tags(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _text(item, limit=120)
        norm = tag.casefold()
        if tag and norm not in seen:
            tags.append(tag)
            seen.add(norm)
    return tags


def _variant_options(product: dict[str, Any], variant: dict[str, Any]) -> dict[str, str]:
    option_names = [
        _text(option.get("name"))
        for option in (product.get("options") or [])
        if isinstance(option, dict)
    ]
    result: dict[str, str] = {}
    for index, name in enumerate(option_names[:3], start=1):
        value = _text(variant.get(f"option{index}"))
        if name and value:
            result[name] = value
    return result


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-"))


def _infer_mount(title: str, tags: list[str]) -> str:
    haystack = f"{title} {' '.join(tags)}".lower()
    checks = (
        ("FE-mount", ("sony e-mount", "sony e mount", "fe-mount", "e-mount", " e mount")),
        ("Z-mount", ("nikon z-mount", "nikon z mount", "z-mount", " z mount")),
        ("X-mount", ("fujifilm x-mount", "fujifilm x mount", "xf-mount", "x-mount", " x mount")),
        ("L-mount", ("leica l-mount", "leica l mount", "l-mount", " l mount")),
        ("M43", ("m43", "micro four thirds", "m4/3")),
        ("PL-mount", ("pl-mount", "pl mount")),
        ("EF-mount", ("ef-mount", "ef mount", "canon ef")),
        ("RF-mount", ("rf-mount", "rf mount", "canon rf")),
    )
    for mount, needles in checks:
        if any(needle in haystack for needle in needles):
            return mount
    return ""


def _infer_series(title: str, tags: list[str]) -> str:
    haystack = f"{title} {' '.join(tags)}".lower()
    for series in ("LAB", "EVO", "Pro", "Air", "Cine", "Luna", "Epic", "Nexus"):
        if series.lower() in haystack:
            return series
    return ""


def _infer_category(product_type: str) -> tuple[str, str]:
    kind = _text(product_type)
    lower = kind.lower()
    if "lens" in lower:
        return "Lens", kind or "Camera Lens"
    if "flash" in lower or "light" in lower:
        return "Lighting", kind
    if "monitor" in lower:
        return "Monitor", kind
    if "adapter" in lower:
        return "Adapter", kind
    return (kind.title(), kind) if kind else ("Product", "")


def _model_code(title: str, handle: str, mount: str, series: str) -> str:
    normalized = re.sub(r"^Viltrox\s+", "", title, flags=re.I)
    normalized = re.sub(r"\bFull\s*-\s*Frame\b|\bFull[- ]?Frame\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bAPS\s*-\s*C\b|\bAPS[- ]?C\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bLens\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bfor\b.*$", "", normalized, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized).strip() or handle
    for source, target in (
        ("F1.8", "F18"), ("F1.7", "F17"), ("F1.4", "F14"),
        ("F1.2", "F12"), ("F2.0", "F20"), ("F2.8", "F28"), ("F4.0", "F40"),
    ):
        normalized = normalized.replace(source, target)
    sku = _slug(normalized)
    suffix = {
        "FE-mount": "FE", "Z-mount": "Z", "X-mount": "X", "L-mount": "L",
        "M43": "M43", "PL-mount": "PL", "EF-mount": "EF", "RF-mount": "RF",
    }.get(mount, "")
    if series and series.upper() not in sku.split("-"):
        sku = f"{sku}-{series.upper()}"
    if suffix and not sku.endswith(f"-{suffix}"):
        sku = f"{sku}-{suffix}"
    return sku or _slug(handle)


def _normalize_product(product: dict[str, Any]) -> dict[str, Any]:
    product_id = _text(product.get("id"))
    title = _text(product.get("title"), limit=1000)
    handle = _text(product.get("handle"), limit=500)
    raw_variants = product.get("variants") or []
    if not product_id or not title or not handle or not raw_variants:
        raise OfficialCatalogSyncError(
            "Shopify feed contains a product without id, title, handle, or variants",
            error_type="validation",
        )

    tags = _tags(product.get("tags"))
    mount = _infer_mount(title, tags)
    series = _infer_series(title, tags)
    category_main, category_detail = _infer_category(_text(product.get("product_type")))
    product_url = f"{OFFICIAL_PRODUCT_BASE_URL}{handle}"
    variants: list[dict[str, Any]] = []
    for raw_variant in raw_variants:
        if not isinstance(raw_variant, dict) or not _text(raw_variant.get("id")):
            raise OfficialCatalogSyncError("Shopify feed contains an invalid variant", error_type="validation")
        if not isinstance(raw_variant.get("available"), bool):
            raise OfficialCatalogSyncError(
                "Shopify feed variant is missing boolean available state",
                error_type="validation",
            )
        variant_price = _price(raw_variant.get("price"))
        compare_at_price = _price(raw_variant.get("compare_at_price"))
        variants.append(
            {
                "shopify_variant_id": _text(raw_variant.get("id")),
                "official_sku": _text(raw_variant.get("sku"), limit=240).upper(),
                "title": _text(raw_variant.get("title"), limit=500),
                "price": str(variant_price) if variant_price is not None else "",
                "compare_at_price": (
                    str(compare_at_price) if compare_at_price is not None else ""
                ),
                "public_store_purchase_available": raw_variant["available"],
                "options": _variant_options(product, raw_variant),
                "position": int(raw_variant.get("position") or len(variants) + 1),
                "store_updated_at": _text(raw_variant.get("updated_at"), limit=80),
            }
        )
    variants.sort(key=lambda item: (item["position"], item["shopify_variant_id"]))
    primary_variant_id = variants[0]["shopify_variant_id"]
    prices = [Decimal(item["price"]) for item in variants if item["price"]]
    price = min(prices) if prices else None
    specs = {
        "shopify_product_id": product_id,
        "shopify_variant_id": primary_variant_id,
        "official_handle": handle,
        "official_options": product.get("options") or [],
        "official_variants": variants,
        "public_store_listed": True,
        "public_store_purchase_available": any(
            item["public_store_purchase_available"] for item in variants
        ),
        "available_variant_count": sum(
            item["public_store_purchase_available"] for item in variants
        ),
        "availability_scope": "public_storefront_purchase_option_only",
        "warehouse_inventory_status": "not_provided_by_public_catalog",
        "variant_count": len(variants),
        "price_min_usd": str(min(prices)) if prices else "",
        "price_max_usd": str(max(prices)) if prices else "",
        "priced_variant_count": len(prices),
        "published_at": _text(product.get("published_at"), limit=80),
        "store_updated_at": _text(product.get("updated_at"), limit=80),
        "vendor": _text(product.get("vendor"), limit=300),
    }
    return {
        "sku": _model_code(title, handle, mount, series),
        "category_main": category_main,
        "category_detail": category_detail,
        "model_name": title,
        "marketing_name": re.sub(r"^Viltrox\s+", "", title, flags=re.I),
        "price_usd": price,
        "status": "official",
        "description": _html_text(product.get("body_html")),
        "series": series,
        "mount": mount,
        "product_url": product_url,
        "specs_json": json.dumps(specs, ensure_ascii=False, sort_keys=True),
        "fit_tags_json": json.dumps(tags, ensure_ascii=False),
        "source_url": product_url,
        "official_catalog_product_id": product_id,
        "official_catalog_variant_id": primary_variant_id,
    }


def validate_feed(payload: Any, *, has_next_page: bool = False) -> CatalogFeed:
    """Validate one complete Shopify response before any product mutation occurs."""
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        raise OfficialCatalogSyncError("Shopify response is missing products[]", error_type="validation")
    products = payload["products"]
    if not products:
        raise OfficialCatalogSyncError("Shopify returned an empty product feed", error_type="validation")
    if has_next_page or len(products) >= SHOPIFY_PRODUCT_LIMIT:
        raise OfficialCatalogSyncError(
            "Shopify product feed may be truncated at the 250-product limit",
            error_type="incomplete_feed",
        )

    items: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    seen_product_ids: set[str] = set()
    seen_variant_ids: set[str] = set()
    seen_handles: set[str] = set()
    variant_count = 0
    for product in products:
        if not isinstance(product, dict) or not isinstance(product.get("variants"), list) or not product["variants"]:
            raise OfficialCatalogSyncError(
                "Shopify feed contains a product without variants[]",
                error_type="validation",
            )
        normalized = _normalize_product(product)
        sku = normalized["sku"]
        product_id = normalized["official_catalog_product_id"]
        handle = _text(product.get("handle"))
        if sku in seen_skus or product_id in seen_product_ids or handle in seen_handles:
            raise OfficialCatalogSyncError(
                f"Shopify feed contains duplicate product identity: {sku}",
                error_type="validation",
            )
        specs = _json_object(normalized["specs_json"])
        for variant in specs.get("official_variants") or []:
            variant_id = _text(variant.get("shopify_variant_id"))
            if variant_id in seen_variant_ids:
                raise OfficialCatalogSyncError(
                    f"Shopify feed contains duplicate variant identity: {variant_id}",
                    error_type="validation",
                )
            seen_variant_ids.add(variant_id)
            variant_count += 1
        seen_skus.add(sku)
        seen_product_ids.add(product_id)
        seen_handles.add(handle)
        items.append(normalized)
    return CatalogFeed(
        products=tuple(products),
        items=tuple(items),
        variant_count=variant_count,
    )


async def fetch_official_catalog() -> CatalogFeed:
    timeout = httpx.Timeout(HARD_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                for attempt in range(MAX_FETCH_ATTEMPTS):
                    response = await client.get(OFFICIAL_CATALOG_URL)
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt + 1 < MAX_FETCH_ATTEMPTS:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    return validate_feed(payload, has_next_page=bool(response.links.get("next")))
                raise OfficialCatalogSyncError("official catalog retry loop exhausted", error_type="http")
    except OfficialCatalogSyncError:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise OfficialCatalogSyncError(
            f"official catalog request exceeded {HARD_TIMEOUT_SECONDS:g} seconds",
            error_type="timeout",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise OfficialCatalogSyncError(
            f"official catalog HTTP {exc.response.status_code}", error_type="http"
        ) from exc
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        raise OfficialCatalogSyncError(f"official catalog fetch failed: {exc}", error_type="http") from exc


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _catalog_specs_for_compare(value: Any) -> dict[str, Any]:
    """Remove Shopify request-time fields before deciding business changes."""
    specs = _json_object(value)
    specs.pop("store_updated_at", None)
    variants = specs.get("official_variants")
    if isinstance(variants, list):
        cleaned_variants: list[Any] = []
        for variant in variants:
            if not isinstance(variant, dict):
                cleaned_variants.append(variant)
                continue
            cleaned = dict(variant)
            cleaned.pop("store_updated_at", None)
            cleaned_variants.append(cleaned)
        specs["official_variants"] = cleaned_variants
    return specs


def _merge_existing_metadata(existing: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    specs = _json_object(existing.get("specs_json"))
    specs.update(_json_object(item.get("specs_json")))
    tags: list[str] = []
    seen: set[str] = set()
    for raw in [*_json_list(existing.get("fit_tags_json")), *_json_list(item.get("fit_tags_json"))]:
        tag = _text(raw, limit=120)
        key = tag.casefold()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
    return {
        **item,
        "specs_json": json.dumps(specs, ensure_ascii=False, sort_keys=True),
        "fit_tags_json": json.dumps(tags, ensure_ascii=False),
    }


def _all_existing_products(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sku, category_main, category_detail, model_name, marketing_name,
               price_usd, status, description, source_file, series, mount,
               product_url, specs_json, fit_tags_json, source_url,
               source_confidence, official_catalog_product_id,
               official_catalog_variant_id, official_catalog_missing_full_feeds,
               official_catalog_previous_status, imported_at, updated_at
        FROM vkpi_products
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _same_price(left: Any, right: Any) -> bool:
    return _price(left) == _price(right)


def _same_product_data(existing: dict[str, Any], item: dict[str, Any]) -> bool:
    text_fields = (
        "category_main",
        "category_detail",
        "model_name",
        "marketing_name",
        "status",
        "description",
        "source_file",
        "series",
        "mount",
        "product_url",
        "source_url",
        "official_catalog_product_id",
        "official_catalog_variant_id",
        "official_catalog_previous_status",
    )
    if any(_text(existing.get(field)) != _text(item.get(field)) for field in text_fields):
        return False
    if not _same_price(existing.get("price_usd"), item.get("price_usd")):
        return False
    if Decimal(str(existing.get("source_confidence") or 0)) != Decimal(
        str(item.get("source_confidence") or 0)
    ):
        return False
    if _catalog_specs_for_compare(existing.get("specs_json")) != _catalog_specs_for_compare(
        item.get("specs_json")
    ):
        return False
    if _json_list(existing.get("fit_tags_json")) != _json_list(item.get("fit_tags_json")):
        return False
    return int(existing.get("official_catalog_missing_full_feeds") or 0) == 0


def _prepare_item(
    item: dict[str, Any],
    *,
    existing: dict[str, Any] | None,
    effective_sku: str,
    checked_at: str,
) -> dict[str, Any]:
    prepared = {
        **item,
        "sku": effective_sku,
        "source_file": SOURCE_FILE,
        "source_confidence": Decimal("1.0"),
        "official_catalog_missing_full_feeds": 0,
    }
    if existing:
        prepared = _merge_existing_metadata(existing, prepared)
        for field in ("category_detail", "description", "series", "mount"):
            if not _text(prepared.get(field)):
                prepared[field] = _text(existing.get(field))
        if _text(existing.get("status")) == "store_unlisted":
            prepared["status"] = (
                _text(existing.get("official_catalog_previous_status")) or "official"
            )
            prepared["official_catalog_previous_status"] = ""
        else:
            prepared["status"] = _text(existing.get("status")) or "official"
            prepared["official_catalog_previous_status"] = ""
        prepared["imported_at"] = existing.get("imported_at") or checked_at
    else:
        prepared["official_catalog_previous_status"] = ""
        prepared["imported_at"] = checked_at
    changed = existing is None or not _same_product_data(existing, prepared)
    prepared["updated_at"] = (
        checked_at if changed else existing.get("updated_at") or checked_at
    )
    return prepared


def _plan_complete_feed(conn: Any, feed: CatalogFeed, checked_at: str) -> CatalogPlan:
    existing_rows = _all_existing_products(conn)
    by_sku = {_text(row.get("sku")): row for row in existing_rows}
    by_product_id: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        product_id = _text(row.get("official_catalog_product_id"))
        if not product_id:
            continue
        if product_id in by_product_id:
            raise OfficialCatalogSyncError(
                f"database contains duplicate official product identity: {product_id}",
                error_type="identity_conflict",
            )
        by_product_id[product_id] = row

    planned: list[PlannedCatalogItem] = []
    seen_effective_skus: set[str] = set()
    for item in feed.items:
        generated_sku = _text(item.get("sku"))
        product_id = _text(item.get("official_catalog_product_id"))
        existing = by_product_id.get(product_id)
        if existing:
            effective_sku = _text(existing.get("sku"))
        else:
            effective_sku = generated_sku
            existing = by_sku.get(effective_sku)
            existing_product_id = _text(
                existing.get("official_catalog_product_id") if existing else ""
            )
            if existing_product_id and existing_product_id != product_id:
                raise OfficialCatalogSyncError(
                    f"generated SKU {generated_sku} belongs to another official product",
                    error_type="identity_conflict",
                )
        if not effective_sku or effective_sku in seen_effective_skus:
            raise OfficialCatalogSyncError(
                f"resolved duplicate catalog SKU: {effective_sku or generated_sku}",
                error_type="identity_conflict",
            )
        seen_effective_skus.add(effective_sku)
        prepared = _prepare_item(
            item,
            existing=existing,
            effective_sku=effective_sku,
            checked_at=checked_at,
        )
        planned.append(
            PlannedCatalogItem(
                item=prepared,
                generated_sku=generated_sku,
                existed=existing is not None,
                changed=existing is None or not _same_product_data(existing, prepared),
            )
        )

    missing_rows = [
        row
        for row in existing_rows
        if _text(row.get("official_catalog_product_id"))
        and _text(row.get("sku")) not in seen_effective_skus
    ]
    return CatalogPlan(
        items=tuple(planned),
        missing_skus=tuple(_text(row.get("sku")) for row in missing_rows),
        mark_unlisted_skus=tuple(
            _text(row.get("sku"))
            for row in missing_rows
            if int(row.get("official_catalog_missing_full_feeds") or 0) >= 1
            and _text(row.get("status")) != "store_unlisted"
        ),
    )


def _record_run_start(run_id: str) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_official_catalog_sync_runs
          (run_id, source_url, status, started_at)
        VALUES (?, ?, 'running', NOW())
        """,
        (run_id, OFFICIAL_CATALOG_URL),
    )
    conn.commit()


def _record_run_failure(
    run_id: str,
    *,
    error_type: str,
    error_message: str,
    duration_ms: int = 0,
) -> None:
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_official_catalog_sync_runs
        SET status='failed', finished_at=NOW(), error_type=?, error_message=?,
            duration_ms=?
        WHERE run_id=?
        """,
        (
            _text(error_type, limit=80) or "other",
            _text(error_message, limit=2000),
            max(0, int(duration_ms)),
            run_id,
        ),
    )
    conn.commit()


def _upsert_product(conn: Any, item: dict[str, Any], checked_at: str) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_products
          (sku, category_main, category_detail, model_name, marketing_name,
           price_usd, status, description, source_file, series, mount,
           product_url, specs_json, fit_tags_json, source_url, source_checked_at,
           source_confidence, official_catalog_product_id,
           official_catalog_variant_id, official_catalog_last_seen_at,
           official_catalog_missing_full_feeds, official_catalog_previous_status,
           imported_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET
          category_main=excluded.category_main,
          category_detail=excluded.category_detail,
          model_name=excluded.model_name,
          marketing_name=excluded.marketing_name,
          price_usd=excluded.price_usd,
          status=excluded.status,
          description=excluded.description,
          source_file=excluded.source_file,
          series=excluded.series,
          mount=excluded.mount,
          product_url=excluded.product_url,
          specs_json=excluded.specs_json,
          fit_tags_json=excluded.fit_tags_json,
          source_url=excluded.source_url,
          source_checked_at=excluded.source_checked_at,
          source_confidence=excluded.source_confidence,
          official_catalog_product_id=excluded.official_catalog_product_id,
          official_catalog_variant_id=excluded.official_catalog_variant_id,
          official_catalog_last_seen_at=excluded.official_catalog_last_seen_at,
          official_catalog_missing_full_feeds=excluded.official_catalog_missing_full_feeds,
          official_catalog_previous_status=excluded.official_catalog_previous_status,
          updated_at=excluded.updated_at
        """,
        (
            item["sku"],
            item["category_main"],
            item["category_detail"],
            item["model_name"],
            item["marketing_name"],
            item["price_usd"],
            item["status"],
            item["description"],
            SOURCE_FILE,
            item["series"],
            item["mount"],
            item["product_url"],
            item["specs_json"],
            item["fit_tags_json"],
            item["source_url"],
            checked_at,
            item["source_confidence"],
            item["official_catalog_product_id"],
            item["official_catalog_variant_id"],
            checked_at,
            item["official_catalog_missing_full_feeds"],
            item["official_catalog_previous_status"],
            item["imported_at"],
            item["updated_at"],
        ),
    )


def _result_from_plan(
    *,
    run_id: str,
    status: str,
    feed: CatalogFeed,
    plan: CatalogPlan,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": status,
        "source_url": OFFICIAL_CATALOG_URL,
        "products_fetched": len(feed.products),
        "variants_fetched": feed.variant_count,
        "inserted": sum(not item.existed for item in plan.items),
        "updated": sum(item.existed and item.changed for item in plan.items),
        "unchanged": sum(item.existed and not item.changed for item in plan.items),
        "missing": len(plan.missing_skus),
        "marked_unlisted": len(plan.mark_unlisted_skus),
        "duration_ms": max(0, int(duration_ms)),
        "atomic": True,
        "warehouse_inventory_included": False,
    }


def preview_complete_feed(feed: CatalogFeed) -> dict[str, Any]:
    """Read current rows and return the exact no-write impact of a complete feed."""
    started = time.monotonic()
    plan = _plan_complete_feed(get_conn(), feed, _now())
    return _result_from_plan(
        run_id="",
        status="dry_run",
        feed=feed,
        plan=plan,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def persist_complete_feed(
    run_id: str,
    feed: CatalogFeed,
    *,
    started_monotonic: float | None = None,
) -> dict[str, Any]:
    """Atomically upsert one validated full feed and advance missing-feed state."""
    conn = get_conn()
    checked_at = _now()
    started = started_monotonic if started_monotonic is not None else time.monotonic()
    try:
        plan = _plan_complete_feed(conn, feed, checked_at)
        for planned in plan.items:
            item = planned.item
            conn.execute(
                """
                INSERT INTO vkpi_official_catalog_sync_items
                  (run_id, sku, generated_sku, shopify_product_id, shopify_variant_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    item["sku"],
                    planned.generated_sku,
                    item["official_catalog_product_id"],
                    item["official_catalog_variant_id"],
                ),
            )

        for planned in plan.items:
            _upsert_product(conn, planned.item, checked_at)

        conn.execute(
            """
            UPDATE vkpi_products AS p
            SET official_catalog_missing_full_feeds=p.official_catalog_missing_full_feeds + 1,
                official_catalog_previous_status=CASE
                  WHEN p.status <> 'store_unlisted' THEN p.status
                  ELSE p.official_catalog_previous_status
                END,
                status=CASE
                  WHEN p.official_catalog_missing_full_feeds + 1 >= 2 THEN 'store_unlisted'
                  ELSE p.status
                END,
                updated_at=?
            WHERE COALESCE(p.official_catalog_product_id, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM vkpi_official_catalog_sync_items i
                WHERE i.run_id=? AND i.sku=p.sku
              )
            """,
            (checked_at, run_id),
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        result = _result_from_plan(
            run_id=run_id,
            status="completed",
            feed=feed,
            plan=plan,
            duration_ms=duration_ms,
        )
        conn.execute(
            """
            UPDATE vkpi_official_catalog_sync_runs
            SET status='completed', finished_at=NOW(),
                products_fetched=?, variants_fetched=?, inserted_count=?,
                updated_count=?, unchanged_count=?, missing_count=?,
                store_unlisted_count=?, duration_ms=?, error_type='', error_message=''
            WHERE run_id=?
            """,
            (
                result["products_fetched"],
                result["variants_fetched"],
                result["inserted"],
                result["updated"],
                result["unchanged"],
                result["missing"],
                result["marked_unlisted"],
                result["duration_ms"],
                run_id,
            ),
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


async def sync_official_catalog(*, dry_run: bool = False) -> dict[str, Any]:
    """Run one audited sync; failures are recorded and then raised to the scheduler."""
    started = time.monotonic()
    if dry_run:
        feed = await fetch_official_catalog()
        result = await asyncio.to_thread(preview_complete_feed, feed)
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result

    run_id = _run_id()
    await asyncio.to_thread(_record_run_start, run_id)
    try:
        feed = await fetch_official_catalog()
        return await asyncio.to_thread(
            persist_complete_feed,
            run_id,
            feed,
            started_monotonic=started,
        )
    except Exception as exc:
        error_type = exc.error_type if isinstance(exc, OfficialCatalogSyncError) else "database"
        try:
            await asyncio.to_thread(
                _record_run_failure,
                run_id,
                error_type=error_type,
                error_message=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            logger.exception("official_catalog_sync.failure_record_failed", extra={"run_id": run_id})
        if isinstance(exc, OfficialCatalogSyncError):
            raise
        raise OfficialCatalogSyncError(str(exc), error_type=error_type) from exc
