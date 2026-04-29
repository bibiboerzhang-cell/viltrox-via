from __future__ import annotations

from collections import Counter, defaultdict

from app.services.deepsight.constants import COMPETITOR_GROUPS


def compute_competitor_radar(items: list[dict]) -> dict:
    brand_counter: Counter[str] = Counter()
    product_counter: Counter[str] = Counter()
    group_counter: Counter[str] = Counter()
    by_group: dict[str, list[dict]] = defaultdict(list)

    lookup = {}
    for group, brands in COMPETITOR_GROUPS.items():
        for b in brands:
            lookup[b.lower()] = group

    for item in items:
        for brand in item.get("competitor_brands", []):
            name = str(brand).strip()
            if not name:
                continue
            brand_counter[name] += 1
            group = lookup.get(name.lower())
            if group:
                group_counter[group] += 1
        for prod in item.get("competitor_products", []):
            name = str(prod).strip()
            if not name:
                continue
            product_counter[name] += 1
            low = name.lower()
            for needle, group in lookup.items():
                if needle in low:
                    group_counter[group] += 1
                    break

    total_mentions = sum(brand_counter.values()) + sum(product_counter.values())
    for group, count in group_counter.most_common():
        by_group[group] = [
            {"brand": brand, "mentions": cnt}
            for brand, cnt in brand_counter.items()
            if lookup.get(brand.lower()) == group
        ]

    return {
        "total_mentions": total_mentions,
        "brand_rank": [{"brand": k, "mentions": v} for k, v in brand_counter.most_common(15)],
        "product_rank": [{"product": k, "mentions": v} for k, v in product_counter.most_common(15)],
        "group_rank": [{"group": k, "mentions": v} for k, v in group_counter.most_common()],
        "by_group": by_group,
    }
