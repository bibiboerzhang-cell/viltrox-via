"""
services/via/knowledge_seed.py — seed official/product/user/market docs into Via vector memory
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx

from app.core.constants import PRODUCT_RULES
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.via.business_brain import (
    AFFILIATE_GUIDE_URL,
    CONTACT_URL,
    OFFICIAL_CONTACT_EMAIL,
    SUPPORT_CENTER_URL,
)
from app.services.via.external_viltrox_assets import get_external_viltrox_knowledge
from app.services.via.product_brain import CATALOG, SERIES_OFFICIAL_URLS, STORE_URL
from app.services.via.stock_watch import build_via_stock_watch

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    flags=re.IGNORECASE | re.DOTALL,
)
_WS_RE = re.compile(r"\s+")
_SITE_CACHE: dict[str, dict[str, Any]] = {}
_SITE_CACHE_TTL_SEC = 60 * 60 * 6
_DOCX_GUIDE_CACHE: dict[str, dict[str, Any]] = {}
_DOCX_SOFTWARE_CACHE: dict[str, dict[str, Any]] = {}
_DOCX_PRODUCT_LINE_CACHE: dict[str, dict[str, Any]] = {}
_GUIDE_SERIES_HEADINGS = {
    "LUNA",
    "EPIC",
    "RAZE",
    "LAB",
    "PRO",
    "EVO",
    "DF",
    "C",
    "AIR",
    "CHIP",
    "OTHER",
}
logger = get_logger(__name__)


def _merge_unique(target: list[str], values: list[str], *, limit: int) -> list[str]:
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in target:
            continue
        target.append(clean)
        if len(target) >= limit:
            break
    return target


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
        for field_name, field_value in item.items():
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
            if isinstance(item, dict):
                text = json.dumps(item, ensure_ascii=False)
            else:
                text = str(item or "").strip()
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


def _normalize_text(raw: str) -> str:
    clean = html.unescape(_TAG_RE.sub(" ", raw or ""))
    return _WS_RE.sub(" ", clean).strip()


def _trim_blob(raw: str, limit: int = 1200) -> str:
    text = _normalize_text(raw)
    return text[:limit]


def _workspace_docx_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    roots = [
        here.parents[5],  # /.../viltrox-app-test
        here.parents[4],  # /.../viltrox-2.0
        Path.cwd(),
    ]
    seen: set[str] = set()
    candidates: list[Path] = []
    for root in roots:
        candidate = root / "Viltrox all INFO.docx"
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            candidates.append(candidate)
    return candidates


def _is_guide_heading(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    upper = text.upper()
    if re.match(r"^(LUNA|EPIC|RAZE|LAB|PRO|EVO|DF|C|AIR|CHIP|OTHER)\b", upper):
        return False
    if re.match(r"^(AF\s+)?\d{1,3}MM\b", upper):
        return False
    if upper in _GUIDE_SERIES_HEADINGS:
        return True
    if re.match(r"^\d+️⃣?\s*", text):
        return True
    heading_tokens = (
        "产品总览",
        "产品列表",
        "镜头",
        "接环",
        "闪光灯",
        "监视器",
        "影视灯光",
        "配件",
        "软件",
        "电影产品线",
        "普通产品线",
        "桌面客户端",
        "移动客户端",
    )
    return len(text) <= 32 and any(token in text for token in heading_tokens)


def _extract_docx_lines(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "ignore")
    raw = html.unescape(re.sub(r"<[^>]+>", "\n", xml))
    lines: list[str] = []
    for part in raw.splitlines():
        clean = _WS_RE.sub(" ", part).strip()
        if clean:
            lines.append(clean)
    return lines


def _build_docx_guide_docs(path: Path, *, limit: int = 28, chunk_size: int = 950) -> list[dict[str, Any]]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    cache_key = str(path)
    cached = _DOCX_GUIDE_CACHE.get(cache_key)
    if cached and float(cached.get("mtime") or 0.0) == float(stat.st_mtime):
        return list(cached.get("docs") or [])

    try:
        lines = _extract_docx_lines(path)
    except Exception:
        logger.warning("via.knowledge_seed.local_docx_failed", extra={"path": str(path)}, exc_info=True)
        return []

    docs: list[dict[str, Any]] = []
    section = "Viltrox guide"
    buffer: list[str] = []
    counter = 0

    def flush() -> None:
        nonlocal buffer, counter
        text = " ".join(buffer).strip()
        if not text:
            buffer = []
            return
        counter += 1
        docs.append(
            {
                "memory_kind": "workspace_docx",
                "memory_key": f"{path.stem}:{counter:03d}",
                "source_ref": f"{path.name}#{counter:03d}",
                "summary": f"{section} | {text[:220]}",
                "text": text[:3200],
                "payload": {
                    "filename": path.name,
                    "section": section,
                    "source": "workspace_docx",
                    "language": "zh",
                    "text_snippet": text[:1200],
                },
            }
        )
        buffer = []

    for line in lines:
        if _is_guide_heading(line):
            if buffer and len(" ".join(buffer)) > int(chunk_size * 0.55):
                flush()
            section = line
            if not buffer:
                buffer.append(f"[{section}]")
            continue
        candidate = " ".join(buffer + [line]).strip()
        if buffer and len(candidate) > chunk_size:
            flush()
            buffer.append(f"[{section}]")
        elif not buffer:
            buffer.append(f"[{section}]")
        buffer.append(line)
    flush()

    docs = docs[: max(1, int(limit))]
    _DOCX_GUIDE_CACHE[cache_key] = {"mtime": float(stat.st_mtime), "docs": list(docs)}
    return docs


def _workspace_docx_docs(limit: int = 28) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in _workspace_docx_candidates():
        docs.extend(_build_docx_guide_docs(path, limit=limit))
    return docs[: max(1, int(limit))]


def extract_workspace_docx_software_catalog() -> dict[str, dict[str, Any]]:
    aliases = {
        "viltrox_lens": {"VILTROX Lens", "Viltrox Lens"},
        "nexus_focus": {"Nexus Foucs", "Nexus Focus", "NexusFocus"},
        "viltroxlink": {"ViltroxLink"},
        "weeylightpro": {"weeylightPro", "weeylitePro", "WeeylightPro", "WeeylitePro"},
    }
    catalog: dict[str, dict[str, Any]] = _external_software_catalog()
    for path in _workspace_docx_candidates():
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        cache_key = str(path)
        cached = _DOCX_SOFTWARE_CACHE.get(cache_key)
        if cached and float(cached.get("mtime") or 0.0) == float(stat.st_mtime):
            for key, value in dict(cached.get("catalog") or {}).items():
                catalog[key] = dict(value)
            continue
        try:
            lines = _extract_docx_lines(path)
        except Exception:
            logger.warning("via.knowledge_seed.software_catalog_failed", extra={"path": str(path)}, exc_info=True)
            continue
        found: dict[str, dict[str, Any]] = {}
        current_key = ""
        in_software = False
        for line in lines:
            clean = str(line or "").strip()
            if not clean:
                continue
            if "软件" in clean:
                in_software = True
                current_key = ""
                continue
            if not in_software:
                continue
            matched_key = next(
                (key for key, names in aliases.items() if any(clean.lower() == name.lower() for name in names)),
                "",
            )
            if matched_key:
                current_key = matched_key
                found.setdefault(
                    current_key,
                    {
                        "name": clean,
                        "notes": [],
                        "links": [],
                    },
                )
                continue
            if _is_guide_heading(clean):
                current_key = ""
                continue
            if not current_key:
                continue
            if clean.startswith("http://") or clean.startswith("https://"):
                if clean not in found[current_key]["links"]:
                    found[current_key]["links"].append(clean)
            elif len(found[current_key]["notes"]) < 8:
                found[current_key]["notes"].append(clean)
        _DOCX_SOFTWARE_CACHE[cache_key] = {"mtime": float(stat.st_mtime), "catalog": dict(found)}
        for key, value in found.items():
            existing = dict(catalog.get(key) or {})
            merged = {
                "name": str(existing.get("name") or value.get("name") or key).strip() or key,
                "notes": _merge_unique(list(existing.get("notes") or []), list(value.get("notes") or []), limit=8),
                "links": _merge_unique(list(existing.get("links") or []), list(value.get("links") or []), limit=6),
            }
            catalog[key] = merged
    return catalog


def extract_workspace_docx_product_line_catalog() -> dict[str, dict[str, Any]]:
    aliases = {
        "LUNA": {"LUNA"},
        "EPIC": {"EPIC"},
        "RAZE": {"RAZE", "Raze"},
        "LAB": {"LAB"},
        "PRO": {"PRO", "Pro"},
        "EVO": {"EVO"},
        "DF": {"DF"},
        "C": {"C"},
        "AIR": {"AIR", "Air"},
        "CHIP": {"CHIP", "Chip"},
        "OTHER": {"OTHER", "Other"},
    }

    def looks_like_model(line: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        if text.startswith("（") and text.endswith("）"):
            return False
        return bool(
            re.search(r"\b(AF|EPIC|LUNA)\b", text, flags=re.IGNORECASE)
            or re.search(r"\b\d{1,3}mm\b", text, flags=re.IGNORECASE)
            or re.search(r"\bT\d", text, flags=re.IGNORECASE)
            or "Chip" in text
        )

    catalog: dict[str, dict[str, Any]] = _external_product_line_catalog()
    for path in _workspace_docx_candidates():
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        cache_key = str(path)
        cached = _DOCX_PRODUCT_LINE_CACHE.get(cache_key)
        if cached and float(cached.get("mtime") or 0.0) == float(stat.st_mtime):
            for key, value in dict(cached.get("catalog") or {}).items():
                catalog[key] = dict(value)
            continue
        try:
            lines = _extract_docx_lines(path)
        except Exception:
            logger.warning("via.knowledge_seed.product_line_catalog_failed", extra={"path": str(path)}, exc_info=True)
            continue
        found: dict[str, dict[str, Any]] = {}
        current_key = ""
        current_model_index = -1
        for line in lines:
            clean = str(line or "").strip()
            if not clean:
                continue
            matched_key = next(
                (
                    key
                    for key, names in aliases.items()
                    if any(clean.lower() == str(name).lower() for name in names)
                ),
                "",
            )
            if matched_key:
                current_key = matched_key
                current_model_index = -1
                found.setdefault(
                    current_key,
                    {
                        "name": clean,
                        "summary": "",
                        "models": [],
                        "notes": [],
                    },
                )
                continue
            if _is_guide_heading(clean):
                current_key = ""
                current_model_index = -1
                continue
            if not current_key:
                continue
            entry = found[current_key]
            if not entry.get("summary") and ("系列" in clean or "产品线" in clean or "镜头" in clean):
                entry["summary"] = clean
                continue
            if looks_like_model(clean):
                if clean not in entry["models"] and len(entry["models"]) < 18:
                    entry["models"].append(clean)
                    current_model_index = len(entry["models"]) - 1
                continue
            if clean.startswith("（") and clean.endswith("）") and 0 <= current_model_index < len(entry["models"]):
                model = entry["models"][current_model_index]
                entry["models"][current_model_index] = f"{model} {clean}"
                continue
            if len(entry["notes"]) < 8:
                entry["notes"].append(clean)
        _DOCX_PRODUCT_LINE_CACHE[cache_key] = {"mtime": float(stat.st_mtime), "catalog": dict(found)}
        for key, value in found.items():
            existing = dict(catalog.get(key) or {})
            merged = {
                "name": str(existing.get("name") or value.get("name") or key).strip() or key,
                "summary": str(existing.get("summary") or value.get("summary") or "").strip(),
                "models": _merge_unique(list(existing.get("models") or []), list(value.get("models") or []), limit=18),
                "notes": _merge_unique(list(existing.get("notes") or []), list(value.get("notes") or []), limit=8),
            }
            if not merged["summary"]:
                merged["summary"] = str(value.get("summary") or "").strip()
            catalog[key] = merged
    return catalog


def _workspace_docx_product_line_docs(limit: int = 18) -> list[dict[str, Any]]:
    catalog = extract_workspace_docx_product_line_catalog()
    docs: list[dict[str, Any]] = []
    for key, item in catalog.items():
        name = str(item.get("name") or key).strip() or key
        summary = str(item.get("summary") or "").strip()
        models = [str(model).strip() for model in (item.get("models") or []) if str(model).strip()]
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        if not (summary or models or notes):
            continue
        docs.append(
            {
                "memory_kind": "workspace_product_line",
                "memory_key": key,
                "source_ref": f"workspace_docx:{name}",
                "summary": f"{name} | {summary or 'product line'}",
                "text": (
                    f"Viltrox product line: {name}. "
                    f"Summary: {summary or 'n/a'}. "
                    f"Representative models: {', '.join(models[:12]) or 'n/a'}. "
                    f"Notes: {' | '.join(notes[:6]) or 'n/a'}."
                )[:2600],
                "payload": {
                    "series": key,
                    "name": name,
                    "summary": summary,
                    "models": models[:18],
                    "notes": notes[:8],
                    "source": "workspace_docx_product_lines",
                },
            }
        )
    return docs[: max(1, int(limit))]


async def _fetch_site_snapshot(url: str, *, label: str, fallback: str) -> dict[str, Any]:
    cached = _SITE_CACHE.get(url)
    now = time.time()
    if cached and (now - float(cached.get("ts") or 0)) < _SITE_CACHE_TTL_SEC:
        return dict(cached["doc"])
    doc = {
        "memory_kind": "official_store",
        "memory_key": label,
        "source_ref": url,
        "summary": fallback[:300],
        "text": fallback[:1500],
        "payload": {"label": label, "url": url, "source": "viltrox_store"},
    }
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            resp = await client.get(url)
        body = resp.text or ""
        title_match = _TITLE_RE.search(body)
        meta_match = _META_DESC_RE.search(body)
        title = _normalize_text(title_match.group(1) if title_match else "") or label
        description = _normalize_text(meta_match.group(1) if meta_match else "")
        text = _trim_blob(body, limit=1800)
        summary = " | ".join(part for part in [title, description] if part) or fallback
        doc = {
            "memory_kind": "official_store",
            "memory_key": label,
            "source_ref": url,
            "summary": summary[:300],
            "text": f"{summary}\n{text}".strip()[:2400],
            "payload": {
                "label": label,
                "url": url,
                "title": title,
                "description": description,
                "source": "viltrox_store",
            },
        }
    except Exception:
        logger.warning(
            "via.knowledge_seed.site_snapshot_failed",
            extra={"url": url, "label": label},
            exc_info=True,
        )
    _SITE_CACHE[url] = {"ts": now, "doc": dict(doc)}
    return dict(doc)


def _product_catalog_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for item in CATALOG:
        docs.append(
            {
                "memory_kind": "product_catalog",
                "memory_key": item.label,
                "source_ref": item.official_url,
                "summary": f"{item.label} | {item.series} | {item.mount_label} | ${item.est_price_usd}",
                "text": (
                    f"Viltrox product: {item.label}. Series: {item.series}. Format: {item.format_tag}. "
                    f"Mounts: {item.mount_label}. Budget tier: {item.budget_tier}. "
                    f"Estimated price: ${item.est_price_usd}. Best for: {item.use_case}. "
                    f"Chinese use case: {item.use_case_zh}. "
                    f"Why it matters: {item.hero_reason}. "
                    f"Official store URL: {item.official_url}. "
                    f"Aliases: {', '.join(item.aliases)}."
                )[:2500],
                "payload": {
                    "label": item.label,
                    "series": item.series,
                    "format_tag": item.format_tag,
                    "mounts": list(item.mounts),
                    "budget_tier": item.budget_tier,
                    "est_price_usd": item.est_price_usd,
                    "official_url": item.official_url,
                    "source": "catalog",
                },
            }
        )
    return docs


def _official_site_targets() -> list[dict[str, str]]:
    targets = [
        {
            "url": STORE_URL,
            "label": "store_home",
            "fallback": "Official Viltrox store homepage with current product navigation and brand positioning.",
        },
        {
            "url": f"{STORE_URL}/blogs",
            "label": "store_blog",
            "fallback": "Official Viltrox blog and event stream with launches, reviews, and insights.",
        },
        {
            "url": SUPPORT_CENTER_URL,
            "label": "support_center",
            "fallback": "Official Viltrox support center with FAQs, contact entry points, and cooperation help.",
        },
        {
            "url": CONTACT_URL,
            "label": "contact_us",
            "fallback": "Official Viltrox contact page for direct support and business inquiries.",
        },
        {
            "url": AFFILIATE_GUIDE_URL,
            "label": "affiliate_guide",
            "fallback": "Official Viltrox affiliate guide for referral onboarding and partner setup.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["AIR"],
            "label": "series_air",
            "fallback": "Official Viltrox AIR series page for lightweight daily primes and APS-C/full-frame creator options.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["EVO"],
            "label": "series_evo",
            "fallback": "Official Viltrox EVO series page for APO-minded modern full-frame primes.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["PRO"],
            "label": "series_pro",
            "fallback": "Official Viltrox PRO series page for higher-performance fast prime lenses.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["LAB"],
            "label": "series_lab",
            "fallback": "Official Viltrox LAB flagship series page for premium full-frame rendering.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["EPIC"],
            "label": "series_epic",
            "fallback": "Official Viltrox EPIC cinema overview covering the 1.33X anamorphic lens family.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["LUNA"],
            "label": "series_luna",
            "fallback": "Official Viltrox LUNA cinema zoom overview for long-reach production workflows.",
        },
        {
            "url": SERIES_OFFICIAL_URLS["LIGHT"],
            "label": "series_light",
            "fallback": "Official Viltrox vintage lighting page for Z-series flashes and creator lighting tools.",
        },
    ]
    hero_labels = {
        "AF 50mm F2.0 Air FF",
        "AF 35mm F1.8 EVO APO",
        "AF 50mm F1.4 Pro FF",
        "AF 35mm F1.2 LAB",
        "EPIC Cinema Series",
        "LUNA 30-300mm T4.0",
        "Vintage Z1",
    }
    for item in CATALOG:
        if item.label not in hero_labels:
            continue
        targets.append(
            {
                "url": item.official_url,
                "label": f"product_{item.label.lower().replace(' ', '_').replace('.', '').replace('/', '_')}",
                "fallback": f"Official Viltrox product page for {item.label}. Series {item.series}.",
            }
        )
    return targets


def _official_business_docs() -> list[dict[str, Any]]:
    return [
        {
            "memory_kind": "business_support",
            "memory_key": "official_business_lane",
            "source_ref": CONTACT_URL,
            "summary": "Official Viltrox support/contact lane for rental, trial, cooperation, and affiliate onboarding.",
            "text": (
                f"Official business support lane for Via. Support Center: {SUPPORT_CENTER_URL}. "
                f"Contact Us: {CONTACT_URL}. Official contact email: {OFFICIAL_CONTACT_EMAIL}. "
                f"Affiliate guide: {AFFILIATE_GUIDE_URL}. Use official support/contact to confirm rental partners, trial availability, "
                "or cooperation details instead of assuming a public roster."
            )[:2200],
            "payload": {
                "support_center_url": SUPPORT_CENTER_URL,
                "contact_url": CONTACT_URL,
                "affiliate_guide_url": AFFILIATE_GUIDE_URL,
                "contact_email": OFFICIAL_CONTACT_EMAIL,
                "source": "official_business",
            },
        }
    ]


def _full_product_rule_docs(limit: int = 40) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for item in PRODUCT_RULES[: max(1, int(limit))]:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        keywords = [str(value).strip() for value in (item.get("keywords") or []) if str(value).strip()]
        docs.append(
            {
                "memory_kind": "product_rule",
                "memory_key": label,
                "source_ref": f"{STORE_URL}/search?q={label.replace(' ', '+')}",
                "summary": f"{label} | product rule",
                "text": (
                    f"Viltrox product rule entry. Label: {label}. "
                    f"Series: {item.get('series') or ''}. "
                    f"Keywords: {', '.join(keywords[:16])}. "
                    f"Official store root: {STORE_URL}."
                )[:1500],
                "payload": {
                    "label": label,
                    "series": str(item.get("series") or ""),
                    "keywords": keywords[:24],
                    "source": "product_rules",
                },
            }
        )
    return docs


def _safe_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("via.knowledge_seed.safe_json_parse_failed", exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _submission_analysis_docs(user_id: int, limit: int = 8) -> list[dict[str, Any]]:
    if not int(user_id or 0):
        return []
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, title, platform, product_label, product_series, detection_status,
               final_score, creator_score, overall_score, created_at, video_analysis
        FROM submissions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    ).fetchall()
    docs: list[dict[str, Any]] = []
    for row in rows:
        analysis = _safe_json(row["video_analysis"] if "video_analysis" in row.keys() else row[10])
        notes = str(analysis.get("notes") or analysis.get("summary") or "").strip()
        content_genre = str(analysis.get("content_genre") or analysis.get("genre") or "").strip()
        products = analysis.get("products") or analysis.get("viltrox_products") or analysis.get("products_detected") or []
        if not isinstance(products, list):
            products = []
        improvements = analysis.get("improvement_suggestions") or analysis.get("suggestions") or []
        if not isinstance(improvements, list):
            improvements = []
        title = str(row["title"] or "").strip() or f"submission-{row['id']}"
        product_label = str(row["product_label"] or row["product_series"] or "").strip()
        summary = f"{title} | {row['platform'] or 'unknown'} | score {row['final_score'] or 0}"
        text = (
            f"User submission analysis. Title: {title}. Platform: {row['platform'] or 'unknown'}. "
            f"Detected product lane: {product_label or 'unknown'}. "
            f"Detection status: {row['detection_status'] or 'pending'}. "
            f"Final score: {row['final_score'] or 0}. Creator score: {row['creator_score'] or 0}. Overall score: {row['overall_score'] or 0}. "
            f"Content genre: {content_genre or 'unknown'}. "
            f"Detected products: {', '.join(str(item) for item in products[:6]) or 'none'}. "
            f"Analysis notes: {notes[:500]}. "
            f"Improvement suggestions: {'; '.join(str(item) for item in improvements[:4]) or 'none'}. "
            f"Created at: {row['created_at'] or ''}."
        )
        docs.append(
            {
                "memory_kind": "submission_analysis",
                "memory_key": f"submission:{row['id']}",
                "source_ref": f"submission:{row['id']}",
                "summary": summary[:300],
                "text": text[:2500],
                "payload": {
                    "submission_id": int(row["id"] or 0),
                    "product_label": product_label,
                    "platform": str(row["platform"] or ""),
                    "content_genre": content_genre,
                    "source": "user_submission",
                },
            }
        )
    return docs


def _market_observation_docs(limit: int = 8) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT observation_key, source_platform, subject_type, subject_key, region_code,
               summary, metrics_json, created_at
        FROM market_observations
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    docs: list[dict[str, Any]] = []
    for row in rows:
        summary = str(row["summary"] or "").strip()
        metrics = str(row["metrics_json"] or "").strip()
        text = (
            f"Market observation from {row['source_platform'] or 'unknown'}. "
            f"Subject type: {row['subject_type'] or 'unknown'}. Subject key: {row['subject_key'] or ''}. "
            f"Region: {row['region_code'] or 'global'}. Summary: {summary or 'n/a'}. "
            f"Metrics: {metrics[:500]}. Created at: {row['created_at'] or ''}."
        )
        docs.append(
            {
                "memory_kind": "market_observation",
                "memory_key": str(row["subject_key"] or ""),
                "source_ref": str(row["observation_key"] or ""),
                "summary": (summary or text)[:300],
                "text": text[:1800],
                "payload": {
                    "source_platform": str(row["source_platform"] or ""),
                    "subject_type": str(row["subject_type"] or ""),
                    "region_code": str(row["region_code"] or ""),
                    "source": "market_observations",
                },
            }
        )
    return docs


def _bh_docs(limit: int = 6) -> list[dict[str, Any]]:
    snapshot = build_via_stock_watch(limit=max(3, int(limit)))
    docs: list[dict[str, Any]] = []
    for item in snapshot.get("items") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        summary = f"{title} | ${item.get('price') or 0} | stock {'yes' if item.get('in_stock') else 'no'}"
        text = (
            f"B&H stock watch for Viltrox. Product: {title}. Price: ${item.get('price') or 0}. "
            f"Rating: {item.get('rating') or 0}. Reviews: {item.get('review_count') or 0}. "
            f"In stock: {'yes' if item.get('in_stock') else 'no'}. "
            f"URL: {item.get('url') or ''}. Snapshot: {item.get('snapshot_at') or ''}."
        )
        docs.append(
            {
                "memory_kind": "bh_market_signal",
                "memory_key": str(item.get("sku") or title),
                "source_ref": str(item.get("url") or item.get("sku") or title),
                "summary": summary[:300],
                "text": text[:1600],
                "payload": {
                    "sku": str(item.get("sku") or ""),
                    "in_stock": bool(item.get("in_stock")),
                    "price": float(item.get("price") or 0.0),
                    "source": "bh_products",
                },
            }
        )
    return docs


async def build_via_seed_documents(bundle: dict[str, Any], *, include_remote: bool = True) -> list[dict[str, Any]]:
    session = bundle.get("session") or {}
    user_id = int(session.get("user_id") or 0)
    docs: list[dict[str, Any]] = []
    if include_remote:
        site_targets = _official_site_targets()
        site_docs = await asyncio.gather(
            *[
                _fetch_site_snapshot(
                    target["url"],
                    label=target["label"],
                    fallback=target["fallback"],
                )
                for target in site_targets
            ],
            return_exceptions=True,
        )
        docs.extend([item for item in site_docs if isinstance(item, dict)])
    docs.extend(_external_knowledge_docs())
    docs.extend(_workspace_docx_docs(limit=28))
    docs.extend(_workspace_docx_product_line_docs(limit=18))
    docs.extend(_official_business_docs())
    docs.extend(_product_catalog_docs())
    docs.extend(_full_product_rule_docs(limit=36))
    docs.extend(_submission_analysis_docs(user_id, limit=10))
    docs.extend(_bh_docs(limit=6))
    docs.extend(_market_observation_docs(limit=8))
    return docs[:128]
