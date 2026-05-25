"""External Viltrox knowledge seed builders."""
from __future__ import annotations

import json
from typing import Any

from app.services.via.external_viltrox_assets import get_external_viltrox_knowledge


def _external_software_catalog() -> dict[str, dict[str, Any]]:
    knowledge = get_external_viltrox_knowledge()
    raw_catalog = dict(knowledge.get("software") or {})
    key_aliases = {
        "viltrox_lens": "viltrox_lens",
        "nexusfocus": "nexus_focus",
        "nexus_focus": "nexus_focus",
        "viltroxlink": "viltroxlink",
        "weeylightpro": "weeylightpro",
        "weeylitepro": "weeylightpro",
    }
    catalog: dict[str, dict[str, Any]] = {}
    for key, item in raw_catalog.items():
        if not isinstance(item, dict):
            continue
        normalized_key = key_aliases.get(str(key).strip().lower(), str(key).strip().lower())
        notes: list[str] = []
        links: list[str] = []
        function = str(item.get("function") or "").strip()
        if function:
            notes.append(function)
        note = str(item.get("note") or "").strip()
        if note:
            notes.append(note)
        platforms = item.get("platforms") or []
        if isinstance(platforms, (list, tuple)):
            labels = [str(value).strip() for value in platforms if str(value).strip()]
            if labels:
                notes.append(f"Platforms: {', '.join(labels)}")
        for field_value in item.values():
            if isinstance(field_value, str) and field_value.startswith(("http://", "https://")):
                links.append(field_value.strip())
        catalog[normalized_key] = {
            "name": str(item.get("name") or key).strip() or str(key).strip(),
            "notes": notes[:8],
            "links": links[:6],
        }
    return catalog


def _external_product_line_catalog() -> dict[str, dict[str, Any]]:
    knowledge = get_external_viltrox_knowledge()
    raw_catalog = dict(knowledge.get("lens_series") or {})
    catalog: dict[str, dict[str, Any]] = {}
    for key, item in raw_catalog.items():
        if not isinstance(item, dict):
            continue
        series_key = str(key or "").strip().upper()
        if not series_key:
            continue
        tagline = str(item.get("tagline") or "").strip()
        highlight = str(item.get("highlight") or "").strip()
        summary = " | ".join(part for part in (tagline, highlight) if part)[:400]
        models = []
        for product in item.get("products") or []:
            if not isinstance(product, dict):
                continue
            model = str(product.get("model") or "").strip()
            if model:
                models.append(model)
        notes: list[str] = []
        line = str(item.get("line") or "").strip()
        tier = str(item.get("tier") or "").strip()
        if line:
            notes.append(f"Line: {line}")
        if tier:
            notes.append(f"Tier: {tier}")
        for use_case in item.get("use_cases") or []:
            clean = str(use_case).strip()
            if clean and f"Use case: {clean}" not in notes:
                notes.append(f"Use case: {clean}")
            if len(notes) >= 8:
                break
        catalog[series_key] = {
            "name": str(item.get("name") or series_key).strip() or series_key,
            "summary": summary,
            "models": models[:18],
            "notes": notes[:8],
        }
    return catalog


def _external_knowledge_docs() -> list[dict[str, Any]]:
    knowledge = get_external_viltrox_knowledge()
    docs: list[dict[str, Any]] = []
    for key, item in _external_software_catalog().items():
        docs.append(
            {
                "memory_kind": "external_software",
                "memory_key": key,
                "source_ref": (item.get("links") or ["external_viltrox_knowledge"])[0],
                "summary": f"{item.get('name')}: {' | '.join(item.get('notes') or [])[:220]}",
                "text": (
                    f"Viltrox software: {item.get('name')}. "
                    f"Notes: {' | '.join(item.get('notes') or []) or 'n/a'}. "
                    f"Links: {', '.join(item.get('links') or []) or 'n/a'}."
                )[:2200],
                "payload": {
                    "source": "external_viltrox_knowledge",
                    "kind": "software",
                    "name": item.get("name"),
                    "links": list(item.get("links") or [])[:6],
                },
            }
        )
    for kind in ("adapters", "monitors"):
        raw_group = dict(knowledge.get(kind) or {})
        for key, item in raw_group.items():
            label = str(key or "").strip()
            text = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item or "").strip()
            if not text:
                continue
            docs.append(
                {
                    "memory_kind": f"external_{kind[:-1]}",
                    "memory_key": label or kind,
                    "source_ref": "external_viltrox_knowledge",
                    "summary": f"{kind[:-1].title()} | {label}",
                    "text": f"Viltrox {kind[:-1]} knowledge for {label}: {text}"[:2200],
                    "payload": {
                        "source": "external_viltrox_knowledge",
                        "kind": kind,
                        "label": label,
                    },
                }
            )
    return docs
