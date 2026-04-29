from __future__ import annotations

from app.services.deepsight.constants import OFFICIAL_MATRIX


def _normalized(value: str) -> str:
    return str(value or "").strip().lower().replace("@", "")


def official_handle_set() -> set[str]:
    return {_normalized(x["handle"]) for x in OFFICIAL_MATRIX}


def segregate(items: list[dict]) -> dict[str, list[dict]]:
    official_handles = official_handle_set()
    official: list[dict] = []
    ugc: list[dict] = []
    for item in items:
        handle = _normalized(item.get("handle") or item.get("channel") or item.get("creator_id") or "")
        if handle and handle in official_handles:
            official.append(item)
        else:
            ugc.append(item)
    return {"official_matrix": official, "ugc_market": ugc, "all_visual_life": items}
