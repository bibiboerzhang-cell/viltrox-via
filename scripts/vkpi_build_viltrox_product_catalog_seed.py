"""Build a Viltrox product catalog seed from the official store.

The output is a reviewable JSON seed that can be passed to
scripts/vkpi_import_viltrox_product_catalog.py. It keeps sales price and
official specs together while leaving internal cost data untouched.

Usage:
    PYTHONPATH=backend .venv/bin/python scripts/vkpi_build_viltrox_product_catalog_seed.py --limit 10
    PYTHONPATH=backend .venv/bin/python scripts/vkpi_build_viltrox_product_catalog_seed.py --limit 10 --fetch-pages
    PYTHONPATH=backend .venv/bin/python scripts/vkpi_build_viltrox_product_catalog_seed.py --all-categories
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRODUCTS_URL = "https://viltrox.com/products.json?limit=250&page=1"
STORE_BASE_URL = "https://viltrox.com"
DEFAULT_OUT = Path("tmp/viltrox_product_catalog_seed_generated.json")
DEFAULT_TYPES = {"camera lens", "cine lenses"}
MAX_PAGE_FETCHES = 20
MAX_RESPONSE_BYTES = 10_000_000


SPEC_LABEL_ALIASES = {
    "lens mount": "lens_mount",
    "lens elements": "lens_elements",
    "focal length": "focal_length",
    "viewing angle": "viewing_angle",
    "angle of view": "viewing_angle",
    "aperture": "aperture",
    "number of aperture blades": "aperture_blades",
    "aperture blades": "aperture_blades",
    "shooting distance": "shooting_distance",
    "minimum focus distance": "shooting_distance",
    "focus mechanism": "focus_mechanism",
    "focus motor": "focus_motor",
    "focus mode": "focus_mode",
    "max.magnification": "max_magnification",
    "max magnification": "max_magnification",
    "lens size": "lens_size",
    "weight": "weight",
    "filter size": "filter_size",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_text(url: str, timeout: float = 15.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "V-KPI product catalog audit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    return payload.decode("utf-8", errors="replace")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return _clean(html.unescape(text))


def _money(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _tags(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [_clean(item) for item in raw if _clean(item)]


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-")
    text = re.sub(r"-+", "-", text)
    return text


def _infer_mount(title: str, tags: list[str]) -> str:
    haystack = f"{title} {' '.join(tags)}".lower()
    checks = [
        ("FE-mount", ["sony e-mount", "sony e mount", "fe-mount", "e-mount", " e mount"]),
        ("Z-mount", ["nikon z-mount", "nikon z mount", "z-mount", " z mount"]),
        ("X-mount", ["fujifilm x-mount", "fujifilm x mount", "xf-mount", "x-mount", " x mount"]),
        ("L-mount", ["leica l-mount", "leica l mount", "l-mount", " l mount"]),
        ("M43", ["m43", "micro four thirds", "m4/3"]),
        ("PL-mount", ["pl-mount", "pl mount"]),
        ("EF-mount", ["ef-mount", "ef mount", "canon ef"]),
        ("RF-mount", ["rf-mount", "rf mount", "canon rf"]),
    ]
    for mount, needles in checks:
        if any(needle in haystack for needle in needles):
            return mount
    return ""


def _normalize_mount(raw: str, title: str, tags: list[str]) -> str:
    inferred = _infer_mount(title, tags)
    clean = _clean(raw)
    if inferred:
        return inferred
    lower = clean.lower()
    if lower in {"e-mount", "e mount"}:
        return "FE-mount"
    if lower in {"z-mount", "z mount"}:
        return "Z-mount"
    if lower in {"x-mount", "x mount", "xf-mount", "xf mount"}:
        return "X-mount"
    if lower in {"l-mount", "l mount"}:
        return "L-mount"
    if lower in {"-mount", "mount"}:
        return ""
    return clean


def _infer_series(title: str, tags: list[str]) -> str:
    haystack = f"{title} {' '.join(tags)}".lower()
    for series in ("LAB", "EVO", "Pro", "Air", "Cine", "Luna", "Epic", "Nexus"):
        if series.lower() in haystack:
            return series
    return ""


def _infer_category(product_type: str) -> tuple[str, str]:
    kind = _clean(product_type)
    lower = kind.lower()
    if "lens" in lower:
        return ("Lens", kind or "Camera Lens")
    if "flash" in lower:
        return ("Lighting", kind)
    if "light" in lower:
        return ("Lighting", kind)
    if "monitor" in lower:
        return ("Monitor", kind)
    if "adapter" in lower:
        return ("Adapter", kind)
    if kind:
        return (kind.title(), kind)
    return ("Product", "")


def _model_code(title: str, handle: str, mount: str, series: str) -> str:
    normalized = title
    normalized = re.sub(r"^Viltrox\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"\bFull\s*-\s*Frame\b|\bFull[- ]?Frame\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bAPS\s*-\s*C\b|\bAPS[- ]?C\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bLens\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\bfor\b.*$", "", normalized, flags=re.I)
    normalized = _clean(normalized)
    if not normalized:
        normalized = handle
    for source, target in (
        ("F1.8", "F18"),
        ("F1.7", "F17"),
        ("F1.4", "F14"),
        ("F1.2", "F12"),
        ("F2.0", "F20"),
        ("F2.8", "F28"),
        ("F4.0", "F40"),
    ):
        normalized = normalized.replace(source, target)
    sku = _slug(normalized)
    mount_suffix = {
        "FE-mount": "FE",
        "Z-mount": "Z",
        "X-mount": "X",
        "L-mount": "L",
        "M43": "M43",
        "PL-mount": "PL",
        "EF-mount": "EF",
        "RF-mount": "RF",
    }.get(mount, "")
    if series and series.upper() not in sku.split("-"):
        sku = f"{sku}-{series.upper()}"
    if mount_suffix and not sku.endswith(f"-{mount_suffix}"):
        sku = f"{sku}-{mount_suffix}"
    return sku or _slug(handle)


def _parse_specs_from_page(page_html: str) -> dict[str, str]:
    start = page_html.find('aria-label="Specification"')
    if start < 0:
        start = page_html.find('aria-label="Specs"')
    if start < 0:
        return {}
    end = page_html.find("</dialog>", start)
    if end < 0:
        end = min(len(page_html), start + 250_000)
    segment = page_html[start:end]
    texts: list[str] = []
    for match in re.finditer(r"<p[^>]*>(.*?)</p>", segment, flags=re.I | re.S):
        text = _strip_tags(match.group(1))
        if not text or len(text) > 180:
            continue
        texts.append(text)

    specs: dict[str, str] = {}
    for index, text in enumerate(texts):
        label = re.sub(r"[\s:;]+$", "", text).strip()
        key = SPEC_LABEL_ALIASES.get(label.lower())
        if not key or index + 1 >= len(texts):
            continue
        value = texts[index + 1]
        if value and not SPEC_LABEL_ALIASES.get(re.sub(r"[\s:;]+$", "", value).lower()):
            specs[key] = value
    return specs


def _extract_highlights(body_html: str, limit: int = 12) -> list[str]:
    text = _strip_tags(body_html)
    chunks = [chunk.strip(" .") for chunk in re.split(r"\.\s+|\n+", text) if chunk.strip()]
    return chunks[:limit]


def _fit_tags(product: dict[str, Any], specs: dict[str, str], mount: str, series: str) -> list[str]:
    tags = [tag.lower() for tag in _tags(product.get("tags"))]
    title = str(product.get("title") or "")
    for token in re.findall(r"\b\d{1,3}mm\b", title.lower()):
        tags.append(token)
    if mount:
        tags.append(mount.lower())
    if series:
        tags.append(series.lower())
    for key in ("focal_length", "aperture"):
        if specs.get(key):
            tags.append(specs[key].lower())
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        clean = _clean(tag).lower()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _product_rows(
    products: list[dict[str, Any]],
    *,
    fetch_pages: bool,
    delay: float,
    limit: int,
    all_categories: bool,
    page_limit: int,
    request_timeout: float,
    deadline_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for product in products:
        product_type = _clean(product.get("product_type"))
        if not all_categories and product_type.lower() not in DEFAULT_TYPES:
            continue
        selected.append(product)
        if limit and len(selected) >= limit:
            break

    checked_at = _utcnow()
    output: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(1.0, deadline_seconds)
    page_attempts = 0
    page_failures: list[dict[str, str]] = []
    skipped_limit = 0
    skipped_deadline = 0
    for index, product in enumerate(selected, start=1):
        title = _clean(product.get("title"))
        handle = _clean(product.get("handle"))
        product_type = _clean(product.get("product_type"))
        product_url = f"{STORE_BASE_URL}/products/{handle}" if handle else ""
        tags = _tags(product.get("tags"))
        specs: dict[str, str] = {}
        page_fetch_status = "not_requested"
        if fetch_pages and product_url:
            remaining = deadline - time.monotonic()
            if page_attempts >= page_limit:
                page_fetch_status = "skipped_page_limit"
                skipped_limit += 1
            elif remaining <= 0:
                page_fetch_status = "skipped_deadline"
                skipped_deadline += 1
            else:
                page_attempts += 1
                try:
                    specs = _parse_specs_from_page(
                        _fetch_text(product_url, timeout=min(request_timeout, remaining))
                    )
                    page_fetch_status = "completed"
                    if delay:
                        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
                except Exception as exc:
                    page_fetch_status = "failed"
                    page_failures.append({"handle": handle, "error": str(exc)[:180]})

        variants = [item for item in (product.get("variants") or []) if isinstance(item, dict)]
        variants.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("id") or "")))
        variant = variants[0] if variants else {}
        prices = [price for price in (_money(item.get("price")) for item in variants) if price is not None]
        mount = _normalize_mount(str(specs.get("lens_mount") or ""), title, tags)
        series = _infer_series(title, tags)
        category_main, category_detail = _infer_category(product_type)
        specs_payload: dict[str, Any] = {
            "official_product_id": product.get("id"),
            "official_handle": handle,
            "official_product_type": product_type,
            "official_tags": tags,
            "official_options": product.get("options") or [],
            "official_variant_id": variant.get("id"),
            "official_variants": [
                {
                    "id": item.get("id"),
                    "sku": _clean(item.get("sku")).upper(),
                    "title": _clean(item.get("title")),
                    "price": _money(item.get("price")),
                    "compare_at_price": _money(item.get("compare_at_price")),
                    "public_store_purchase_available": item.get("available"),
                    "weight_grams": item.get("grams"),
                }
                for item in variants
            ],
            "variant_count": len(variants),
            "price_min_usd": min(prices) if prices else None,
            "price_max_usd": max(prices) if prices else None,
            "public_store_listed": True,
            "public_store_purchase_available": any(
                item.get("available") is True for item in variants
            ),
            "availability_scope": "public_storefront_purchase_option_only",
            "warehouse_inventory_status": "not_provided_by_public_catalog",
            "official_page_fetch": page_fetch_status,
            "highlights": _extract_highlights(str(product.get("body_html") or "")),
        }
        specs_payload.update(specs)
        source_confidence = 1.0 if len(specs) >= 4 else 0.72
        highlights = _extract_highlights(str(product.get("body_html") or ""), limit=1)
        output.append(
            {
                "sku": _model_code(title, handle, mount, series),
                "category_main": category_main,
                "category_detail": category_detail,
                "model_name": title,
                "marketing_name": re.sub(r"^Viltrox\s+", "", title, flags=re.I),
                "series": series,
                "mount": mount,
                "price_usd": min(prices) if prices else None,
                "status": "official",
                "description": highlights[0] if highlights else "",
                "product_url": product_url,
                "source_url": product_url,
                "source_file": "official:viltrox.com/products.json",
                "source_confidence": source_confidence,
                "source_checked_at": checked_at,
                "fit_tags": _fit_tags(product, specs_payload, mount, series),
                "specs": specs_payload,
            }
        )
        stdout_out(
            json.dumps(
                {
                    "index": index,
                    "handle": handle,
                    "sku": output[-1]["sku"],
                    "spec_fields": len(specs),
                },
                ensure_ascii=False,
            )
        )
    return output, {
        "page_fetch_attempted": page_attempts,
        "page_fetch_failures": page_failures,
        "page_fetch_skipped_limit": skipped_limit,
        "page_fetch_skipped_deadline": skipped_deadline,
    }


def build_seed(
    *,
    out: Path,
    limit: int,
    fetch_pages: bool,
    delay: float,
    all_categories: bool,
    page_limit: int = MAX_PAGE_FETCHES,
    request_timeout: float = 15.0,
    deadline_seconds: float = 60.0,
) -> dict[str, Any]:
    payload = json.loads(_fetch_text(PRODUCTS_URL, timeout=request_timeout))
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("official products response is missing non-empty products[]")
    if len(products) >= 250:
        raise ValueError("official products response may be truncated at 250 products")
    rows, page_stats = _product_rows(
        products,
        fetch_pages=fetch_pages,
        delay=delay,
        limit=limit,
        all_categories=all_categories,
        page_limit=max(0, min(MAX_PAGE_FETCHES, int(page_limit))),
        request_timeout=max(1.0, request_timeout),
        deadline_seconds=max(1.0, deadline_seconds),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial_failure_count = len(page_stats["page_fetch_failures"])
    incomplete_page_coverage = (
        partial_failure_count
        + page_stats["page_fetch_skipped_limit"]
        + page_stats["page_fetch_skipped_deadline"]
    )
    return {
        "status": "completed_with_partial_page_coverage" if incomplete_page_coverage else "completed",
        "output": str(out),
        "products_seen": len(products),
        "rows": len(rows),
        "fetch_pages": fetch_pages,
        "all_categories": all_categories,
        "with_specs": sum(1 for row in rows if row.get("source_confidence") == 1.0),
        "partial_failure_count": partial_failure_count,
        "incomplete_page_coverage": incomplete_page_coverage,
        **page_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=90, help="Maximum selected products. Use 0 for all selected.")
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Include all store product types, not just lens/cine lens.",
    )
    parser.add_argument(
        "--fetch-pages",
        action="store_true",
        help=f"Fetch individual product pages for specs (explicit opt-in, max {MAX_PAGE_FETCHES}).",
    )
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between page fetches.")
    parser.add_argument("--page-limit", type=int, default=MAX_PAGE_FETCHES)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--deadline", type=float, default=60.0, help="Overall page-fetch deadline in seconds.")
    args = parser.parse_args()
    result = build_seed(
        out=Path(args.out),
        limit=args.limit,
        fetch_pages=bool(args.fetch_pages),
        delay=max(0.0, args.delay),
        all_categories=bool(args.all_categories),
        page_limit=args.page_limit,
        request_timeout=args.request_timeout,
        deadline_seconds=args.deadline,
    )
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
