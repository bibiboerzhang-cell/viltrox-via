"""Catalog-backed helpers for Smart KOL natural-language product resolution.

This module keeps catalog availability, reviewed colloquial aliases, official
JSON variant identities and family projections out of the main resolver.  All
entry points accept a catalog reader so existing offline tests retain one seam.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.domains.kol import product_focal_family
from app.domains.kol.product_resolver_projection import (
    focal_suggestions,
    format_aperture,
    public_product_projection,
    specs_line,
)
from app.domains.kol.product_resolver_tokens import (
    NIKON_CAMERA_CONTEXT_RE,
    normkey,
    query_apertures,
)
from app.domains.products.product_aliases_lens import alias_rows


CatalogReader = Callable[..., dict[str, Any]]


class ProductCatalogUnavailable(RuntimeError):
    """A catalog dependency failure, distinct from a valid empty match."""


def read_product_catalog(reader: CatalogReader, **kwargs: Any) -> dict[str, Any]:
    try:
        result = reader(**kwargs)
    except Exception as exc:
        raise ProductCatalogUnavailable("product catalog read failed") from exc
    if not isinstance(result, dict):
        raise ProductCatalogUnavailable("product catalog returned a non-object result")
    return result


def catalog_products(
    reader: CatalogReader,
    *,
    limit: int = 500,
    query: str = "",
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"limit": limit}
    if str(query or "").strip():
        kwargs["query"] = query
    result = read_product_catalog(reader, **kwargs)
    products = result.get("products") or []
    if not isinstance(products, list):
        raise ProductCatalogUnavailable("product catalog returned an invalid products list")
    return [row for row in products if isinstance(row, dict)]


_PRODUCT_ALIAS_ROWS: tuple[dict[str, str], ...] = tuple(dict(row) for row in alias_rows())
_ASCII_WORD = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.")
_NIKON_Z_BODY_RE = re.compile(
    rf"(?:{NIKON_CAMERA_CONTEXT_RE.pattern})\s*z(?:1(?:\s*pro)?|2|3|6|8|50)(?![a-z0-9])",
    re.IGNORECASE,
)
_VILTROX_CONTEXT_RE = re.compile(r"\bviltrox\b|\bvintage\b|唯卓仕|维卓仕?", re.IGNORECASE)


def _lens_like_catalog_row(row: dict[str, Any]) -> bool:
    if str(row.get("category_main") or "").strip().lower() == "lens":
        return True
    return bool(product_focal_family.explicit_focals(" ".join(catalog_identity_values(row))))


def _compact_alias_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("／", "/").replace("．", ".")
    text = re.sub(r"\bf\s*/\s*(\d)", r"f\1", text)
    text = re.sub(r"\bt\s*/\s*(\d)", r"t\1", text)
    text = re.sub(
        r"(?<![\d.])(\d{1,3})\s*/\s*(\d(?:\.\d)?)(?![\d])",
        r"\1\2",
        text,
    )
    text = re.sub(r"(?<![a-z0-9])af(?=\d)", "", text)
    text = re.sub(r"(?<=\d)mm\b", "", text)
    text = re.sub(r"\bf(?=\d)", "", text)
    return re.sub(r"[^a-z0-9.㐀-鿿]+", "", text)


def _bounded_occurrence(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        end = index + len(needle)
        left_ok = not (
            needle[0] in _ASCII_WORD
            and index > 0
            and haystack[index - 1] in _ASCII_WORD
        )
        right_ok = not (
            needle[-1] in _ASCII_WORD
            and end < len(haystack)
            and haystack[end] in _ASCII_WORD
        )
        if left_ok and right_ok:
            return True
        start = index + 1


def _separated_alias_occurrence(value: Any, alias: Any) -> bool:
    """Match an alias inside prose while preserving real token boundaries."""

    tokens = re.findall(
        r"[a-z0-9]+(?:\.[0-9]+)?|[\u3400-\u9fff]+",
        str(alias or "").lower(),
    )
    if not tokens:
        return False
    separator = r"[^a-z0-9\u3400-\u9fff]*"
    pattern = r"(?<![a-z0-9])" + separator.join(map(re.escape, tokens)) + r"(?![a-z0-9])"
    return re.search(pattern, str(value or "").lower(), re.IGNORECASE) is not None


def matched_product_alias(value: Any) -> dict[str, str] | None:
    """Return the single longest reviewed alias embedded in operator prose."""

    query_key = _compact_alias_key(value)
    matches: list[tuple[int, dict[str, str]]] = []
    for row in _PRODUCT_ALIAS_ROWS:
        key = _compact_alias_key(row.get("alias"))
        if _bounded_occurrence(query_key, key) or _separated_alias_occurrence(value, row.get("alias")):
            matches.append((len(key), row))
    if not matches:
        return None
    longest = max(length for length, _row in matches)
    winners = [row for length, row in matches if length == longest]
    canonicals = {str(row.get("canonical") or "").strip() for row in winners}
    winner = winners[0] if len(canonicals) == 1 else None
    if (
        winner
        and normkey(winner.get("canonical")) in {"vintagez1", "vintagez1pro", "vintagez2", "sparkz3"}
        and _NIKON_Z_BODY_RE.search(str(value or ""))
        and not _VILTROX_CONTEXT_RE.search(str(value or ""))
    ):
        return None
    return winner


def _decoded_specs(product: dict[str, Any]) -> dict[str, Any]:
    value = product.get("specs_json")
    if value in (None, ""):
        value = product.get("specs")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def catalog_variant_labels(product: dict[str, Any]) -> list[str]:
    """Return bounded official model/variant identities from catalog specs."""

    specs = _decoded_specs(product)
    labels: list[str] = []

    def add(value: Any) -> None:
        label = " ".join(str(value or "").split()).strip()
        if label and len(label) <= 160 and label not in labels:
            labels.append(label)

    add(specs.get("variant_title"))
    for key in ("official_options", "options"):
        options = specs.get(key)
        if not isinstance(options, list):
            continue
        for option in options[:24]:
            if not isinstance(option, dict):
                continue
            values = option.get("values")
            if isinstance(values, list):
                for value in values[:40]:
                    add(value)
    variants = specs.get("variants")
    if isinstance(variants, list):
        for variant in variants[:40]:
            if isinstance(variant, dict):
                for key in ("title", "name", "model", "sku"):
                    add(variant.get(key))
            else:
                add(variant)
    return labels


def catalog_identity_values(product: dict[str, Any]) -> list[str]:
    values = [
        str(product.get(field) or "")
        for field in ("sku", "model_name", "marketing_name")
    ]
    values.extend(catalog_variant_labels(product))
    return [value for value in values if value]


def _matched_variant_label(product: dict[str, Any], canonical: str) -> str:
    key = normkey(canonical)
    for label in catalog_variant_labels(product):
        label_key = normkey(label)
        if key and (key == label_key or label_key.startswith(key)):
            return label
    return ""


def canonical_catalog_rows(
    products: list[dict[str, Any]],
    canonical: str,
) -> list[dict[str, Any]]:
    key = normkey(canonical)
    if not key:
        return []
    rows = [
        product
        for product in products
        if str(product.get("sku") or "")
        and not str(product.get("sku") or "").upper().startswith("IMAGE-AWARDS")
        and any(
            key == normkey(value) or key in normkey(value)
            for value in catalog_identity_values(product)
        )
    ]
    if key == "vintagez1":
        rows = [
            row for row in rows
            if not any(
                "z1pro" in normkey(value)
                for value in catalog_identity_values(row)
            )
        ]
    official: list[dict[str, Any]] = []
    for row in rows:
        try:
            confidence = float(row.get("source_confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            not str(row.get("sku") or "").upper().startswith("VL-")
            and (
                str(row.get("status") or "").strip().lower() == "official"
                or confidence >= 0.9
            )
        ):
            official.append(row)
    if official:
        return official
    non_legacy = [
        row for row in rows
        if not str(row.get("sku") or "").upper().startswith("VL-")
    ]
    return non_legacy or rows


def missing_alias_suggestions(
    products: list[dict[str, Any]],
    canonical: str,
    *,
    series: str,
) -> list[dict[str, Any]]:
    """Suggest nearby rows only for focal aliases; never downgrade model aliases."""

    focals = product_focal_family.explicit_focals(canonical)
    if len(focals) != 1:
        return []
    requested_focal = focals[0]
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for product in products:
        sku = str(product.get("sku") or "")
        if not sku or sku.upper().startswith("IMAGE-AWARDS"):
            continue
        identity = " ".join(catalog_identity_values(product))
        if series and (
            normkey(product.get("series")) != normkey(series)
            and normkey(series) not in normkey(identity)
        ):
            continue
        row_focals = product_focal_family.explicit_focals(identity)
        if not row_focals:
            continue
        distance = min(abs(value - requested_focal) for value in row_focals)
        name = str(product.get("marketing_name") or product.get("model_name") or sku)
        candidates.append((distance, name, product))
    return [
        {
            "sku": product.get("sku"),
            "name": product.get("marketing_name") or product.get("model_name"),
            "mount": product.get("mount"),
            "series": product.get("series"),
        }
        for _distance, _name, product in sorted(candidates)[:6]
    ]


def alias_mount_clarification(
    alias_match: dict[str, str],
    rows: list[dict[str, Any]],
    *,
    requested_mount: str,
) -> dict[str, Any] | None:
    """Explain an alias/mount conflict without substituting another product."""

    if not requested_mount or not any(_lens_like_catalog_row(row) for row in rows) or any(
        str(row.get("mount") or "").strip() == requested_mount for row in rows
    ):
        return None
    available_mounts = product_focal_family.available_mounts(rows)
    if not available_mounts:
        return None
    canonical = str(alias_match.get("canonical") or "").strip()
    alias = str(alias_match.get("alias") or "").strip()
    focals = product_focal_family.explicit_focals(canonical)
    series_values = sorted({
        str(row.get("series") or "").strip()
        for row in rows
        if str(row.get("series") or "").strip()
    })
    return {
        "reason": "focal_mount_not_in_catalog",
        "catalog_status": "available",
        "requested_alias": alias,
        "requested_canonical": canonical,
        "requested_series": series_values[0] if len(series_values) == 1 else "",
        "requested_model_code": "",
        "requested_focals": focals,
        "requested_mount": requested_mount,
        "message": (
            f"已识别产品写法“{alias}”，但 {canonical} 没有 {requested_mount} 版本；"
            f"目录现有 {' / '.join(available_mounts)}。请选择现有卡口再找达人。"
        ),
        "suggestions": focal_suggestions(rows),
    }


def _alias_family_projection(
    rows: list[dict[str, Any]],
    *,
    alias: str,
    canonical: str,
) -> dict[str, Any] | None:
    categories = {
        normkey(row.get("category_main"))
        for row in rows
        if normkey(row.get("category_main"))
    }
    if not rows or len(categories) > 1:
        return None
    richest = max(
        rows,
        key=lambda row: (
            bool(row.get("marketing_name")),
            len(str(row.get("description") or "")),
            len(str(row.get("model_name") or "")),
        ),
    )
    projection = public_product_projection(richest, match_score=(3, 3, 0))
    skus = [str(row.get("sku") or "") for row in rows if str(row.get("sku") or "")][:12]
    mounts = product_focal_family.available_mounts(rows)
    focals = product_focal_family.explicit_focals(canonical)
    apertures = sorted(query_apertures(canonical))
    projection.update({
        "sku": "",
        "price_usd": None,
        "mount": mounts[0] if len(mounts) == 1 else "",
        "model_name": canonical,
        "marketing_name": canonical,
        "description": f"{canonical} 产品家族共 {len(rows)} 个目录记录，未代选具体 SKU。",
        "resolved_alias": alias,
        "resolved_canonical": canonical,
        "resolution_basis": (
            "focal_aperture_family"
            if len(focals) == 1 and len(apertures) == 1
            else "catalog_alias_family"
        ),
        "alias_resolution_basis": "reviewed_alias_table",
    })
    if len(focals) == 1:
        projection.update({
            "resolution_kind": "focal_family",
            "focal_mm": focals[0],
            "focal_family_size": len(rows),
            "focal_family_mounts": sorted({
                str(row.get("mount") or "").strip()
                for row in rows
                if str(row.get("mount") or "").strip()
            }),
            "focal_family_skus": skus,
        })
        if len(apertures) == 1:
            projection["requested_aperture"] = format_aperture(apertures[0])
    else:
        projection.update({
            "resolution_kind": "model_family",
            "model_family_skus": skus,
            "model_family_size": len(rows),
            "resolved_model_identity": canonical,
        })
    projection["specs_line"] = specs_line(projection)
    return projection


def resolve_catalog_alias(
    query: str,
    alias_match: dict[str, str],
    *,
    mount: str,
    catalog_reader: CatalogReader,
) -> dict[str, Any] | None:
    alias = str(alias_match.get("alias") or "").strip()
    canonical = str(alias_match.get("canonical") or "").strip()
    rows = canonical_catalog_rows(catalog_products(catalog_reader), canonical)
    if not rows:
        return None
    if mount and any(_lens_like_catalog_row(row) for row in rows):
        rows = [row for row in rows if str(row.get("mount") or "").strip() == mount]
        if not rows:
            return None
    if len(rows) > 1 and normkey(canonical) == "vintagez1pro":
        return None
    if len(rows) > 1:
        return _alias_family_projection(rows, alias=alias, canonical=canonical)

    row = rows[0]
    projection = public_product_projection(
        row,
        match_score=(3, 3, len(str(row.get("series") or ""))),
    )
    variant = _matched_variant_label(row, canonical)
    focals = product_focal_family.explicit_focals(canonical)
    apertures = sorted(query_apertures(canonical))
    projection.update({
        "resolved_alias": alias,
        "resolved_canonical": canonical,
        "resolution_kind": "catalog_variant_exact" if variant else "catalog_alias_exact",
        "resolution_basis": "catalog_specs_variant" if variant else "catalog_alias",
    })
    if variant:
        projection["resolved_variant"] = variant
    if len(focals) == 1:
        projection["focal_mm"] = focals[0]
    if len(focals) == 1 and len(apertures) == 1:
        projection["requested_aperture"] = format_aperture(apertures[0])
    if not focals:
        projection["resolved_model_identity"] = canonical
    return projection


def resolve_named_product_family(
    query: str,
    *,
    series: str,
    subfamily: str,
    row_words: Callable[[dict[str, Any]], tuple[str, str, set[str]]],
    catalog_reader: CatalogReader,
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for row in catalog_products(catalog_reader):
        if not str(row.get("sku") or "") or str(row.get("sku") or "").upper().startswith("IMAGE-AWARDS"):
            continue
        _blob, _blob_sp, words = row_words(row)
        if series.lower() in words or normkey(row.get("series")) == series.lower():
            rows.append(row)
    if subfamily:
        direct_rows = [
            row for row in rows
            if subfamily in " ".join(
                str(row.get(field) or "")
                for field in ("sku", "model_name", "marketing_name")
            ).lower()
        ]
        rows = direct_rows or [row for row in rows if subfamily in row_words(row)[2]]
    if not rows:
        return None
    if len(rows) == 1:
        projection = public_product_projection(
            rows[0],
            match_score=(2, 2, len(str(rows[0].get("series") or ""))),
        )
        projection["resolution_kind"] = "named_product_family_exact"
        return projection

    categories = {
        str(row.get("category_main") or "").strip()
        for row in rows
        if str(row.get("category_main") or "").strip()
    }
    details = {
        str(row.get("category_detail") or "").strip()
        for row in rows
        if str(row.get("category_detail") or "").strip()
    }
    category = next(iter(categories)) if len(categories) == 1 else ""
    detail = next(iter(details)) if len(details) == 1 else ""
    capability = "cinema lens" if all(
        any(term in row_words(row)[0] for term in ("cine", "anamorphic"))
        for row in rows
    ) else (category.lower() if category else "product")
    projection = {
        "sku": "",
        "model_name": f"Viltrox {series} {capability} family",
        "marketing_name": f"Viltrox {series} {capability} family",
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
    projection["specs_line"] = specs_line(projection)
    return projection


def exact_sku_resolution(
    value: Any,
    *,
    catalog_reader: CatalogReader,
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(ambiguous, projection)`` for a normalized exact-SKU lookup."""

    text = str(value or "").strip()
    normalized = normkey(text)
    if not normalized or len(text) > 240:
        return False, None
    matches = [
        product
        for product in catalog_products(catalog_reader)
        if str(product.get("sku") or "")
        and not str(product.get("sku") or "").upper().startswith("IMAGE-AWARDS")
        and normkey(product.get("sku")) == normalized
    ]
    if len(matches) != 1:
        return len(matches) > 1, None
    product = matches[0]
    return False, public_product_projection(
        product, match_score=(1, 1, len(str(product.get("series") or "")))
    )


def exact_sku_product(value: Any, *, catalog_reader: CatalogReader) -> dict[str, Any] | None:
    _ambiguous, product = exact_sku_resolution(value, catalog_reader=catalog_reader)
    return product


def focal_family_decision(
    query: str,
    *,
    mount: str,
    series: str,
    catalog_reader: CatalogReader,
) -> dict[str, Any] | None:
    products = catalog_products(catalog_reader)
    return product_focal_family.focal_family_decision(
        query,
        mount=mount,
        series=series,
        catalog_reader=lambda **_kwargs: {"products": products},
    )


def resolve_focal_family_product(
    query: str,
    *,
    mount: str,
    series: str,
    catalog_reader: CatalogReader,
) -> dict[str, Any] | None:
    decision = focal_family_decision(
        query,
        mount=mount,
        series=series,
        catalog_reader=catalog_reader,
    )
    if not decision:
        return None
    status = str(decision.get("status") or "")
    if status == "unique":
        product = decision.get("product")
        if not isinstance(product, dict):
            return None
        projection = public_product_projection(
            product,
            match_score=(1, 1, len(str(product.get("series") or ""))),
        )
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
    return {**family, "specs_line": specs_line(family), "match_score": [1, 1, 0]}


def resolve_spec_family_product(
    *,
    focals: list[int],
    apertures: set[tuple[str, float]],
    series: str,
    mount: str,
    row_words: Callable[[dict[str, Any]], tuple[str, str, set[str]]],
    catalog_reader: CatalogReader,
) -> dict[str, Any] | None:
    if len(focals) != 1 or len(apertures) != 1:
        return None
    products = catalog_products(catalog_reader)
    index = product_focal_family.focal_family_index(
        lambda **_kwargs: {"products": products}
    )
    rows = list(index.get(focals[0]) or [])
    matching = [
        row for row in rows
        if query_apertures(f"{row.get('model_name') or ''} {row.get('marketing_name') or ''}") & apertures
    ]
    if series:
        narrowed = [
            row for row in matching
            if series.lower() in row_words(row)[2]
            or normkey(row.get("series")) == series.lower()
        ]
        if narrowed:
            matching = narrowed
    if mount:
        matching = [row for row in matching if str(row.get("mount") or "").strip() == mount]
    if not matching:
        return None
    aperture_label = format_aperture(next(iter(apertures)))
    if len(matching) == 1:
        projection = public_product_projection(
            matching[0],
            match_score=(2, 2, len(str(matching[0].get("series") or ""))),
        )
        projection.update({
            "resolution_kind": "focal_aperture_unique",
            "focal_mm": focals[0],
            "requested_aperture": aperture_label,
        })
        return projection

    projection = product_focal_family.family_projection({"rows": matching, "focal": focals[0]})
    family_word = str(projection.get("series") or "").strip()
    name = " ".join(part for part in ("Viltrox", f"{focals[0]}mm", aperture_label, family_word) if part)
    projection.update({
        "model_name": name,
        "marketing_name": name,
        "description": (
            f"{focals[0]}mm {aperture_label} 产品家族共 {len(matching)} 个目录记录。"
            "操作员未指定卡口，按共享光学能力理解，不代选具体 SKU。"
        ),
        "resolution_kind": "focal_family",
        "resolution_basis": "focal_aperture_family",
        "requested_aperture": aperture_label,
        "match_score": [2, 2, 0],
    })
    projection["specs_line"] = specs_line(projection)
    return projection
