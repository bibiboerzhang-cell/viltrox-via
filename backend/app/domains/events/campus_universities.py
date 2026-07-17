"""校园大学目录(campus_catalog.json)只读端 —— 校园活动选校入口。

真相边界(与活动雷达目录同款诚实口径):
- 数据 = 打包在代码内的人审目录(2026-07-17 官方 .edu 页面逐一核实:专业存在
  + 系页可回跳;联系方式仅收机构级公开信息),本端点**零联网、零写库**;
- 目录是「选校研究快照」,不是实时招生/学期信息;发函前应回访系页复核;
- 篡改防线:字段做类型收口,未知 region/tier 归 other/B,绝不编数据。

红线:纯读;绝不触 viltrox_fit_score / rule_v0;零 LLM、零 provider 外调。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent / "campus_catalog.json"
_REGIONS = frozenset({"northeast", "south", "midwest", "west"})
_TIERS = frozenset({"A", "B"})


def _text(value: Any) -> str:
    return str(value or "").strip()


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def list_campus_universities(*, region: str = "", tier: str = "") -> dict[str, Any]:
    """Return the bundled reviewed campus catalog, optionally filtered."""
    catalog = _load_catalog()
    universities = catalog.get("universities")
    if not isinstance(universities, list) or not universities:
        return {
            "status": "empty",
            "universities": [],
            "total": 0,
            "reviewed_at": _text(catalog.get("reviewed_at")) or None,
            "review_method": _text(catalog.get("review_method")) or None,
        }
    region_filter = _text(region).casefold()
    tier_filter = _text(tier).upper()
    items: list[dict[str, Any]] = []
    for raw in universities:
        if not isinstance(raw, dict):
            continue
        item_region = _text(raw.get("region")).casefold()
        if item_region not in _REGIONS:
            item_region = "other"
        item_tier = _text(raw.get("tier")).upper()
        if item_tier not in _TIERS:
            item_tier = "B"
        if region_filter and item_region != region_filter:
            continue
        if tier_filter and item_tier != tier_filter:
            continue
        items.append(
            {
                "name": _text(raw.get("name")),
                "city": _text(raw.get("city")) or None,
                "state": _text(raw.get("state")) or None,
                "region": item_region,
                "region_label": _text(raw.get("region_label")) or item_region,
                "program": _text(raw.get("program")) or None,
                "degrees": _text(raw.get("degrees")) or None,
                "tier": item_tier,
                "dept_url": _text(raw.get("dept_url")) or None,
                "contact_email": _text(raw.get("contact_email")) or None,
                "contact_phone": _text(raw.get("contact_phone")) or None,
                "notes": _text(raw.get("notes")) or None,
            }
        )
    return {
        "status": "ready" if items else "empty",
        "universities": items,
        "total": len(items),
        "reviewed_at": _text(catalog.get("reviewed_at")) or None,
        "review_method": _text(catalog.get("review_method")) or None,
    }
