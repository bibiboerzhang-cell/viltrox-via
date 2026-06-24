"""F2 · 活动地址地理编码(免 key)—— OSM Nominatim,走代理,best-effort。

填地址 → 经纬度,写回 vkpi_events.location_lat/lng(前端 leaflet/RealMap 直接画点)。
免费无 key(Nominatim 使用政策:带 User-Agent、限速);失败则诚实返回,提示手动填经纬度。
红线:只读外部 + 只写 events 经纬度列;零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

_NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _opener():
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("YTDLP_PROXY") or ""
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def geocode_address(address: str) -> dict[str, Any] | None:
    """地址 → {lat,lng,source,display_name};无结果 / 网络失败 → None(诚实)。"""
    q = str(address or "").strip()
    if not q:
        return None
    url = f"{_NOMINATIM}?{urllib.parse.urlencode({'q': q, 'format': 'json', 'limit': 1})}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "V-KPI/1.0 (event geocoding)"})
        with _opener().open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8") or "[]")
        if not data:
            return None
        top = data[0]
        return {
            "lat": float(top["lat"]),
            "lng": float(top["lon"]),
            "source": "nominatim",
            "display_name": str(top.get("display_name") or ""),
        }
    except Exception:
        logger.debug("geocode.failed", extra={"q": q[:48]}, exc_info=True)
        return None


def geocode_and_update_event(event_id: str, address: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """对活动地理编码并写回经纬度。address 缺省时用活动 location_name/city/country 拼。"""
    del staff
    eid = str(event_id or "").strip()
    conn = get_conn()
    row = conn.execute(
        "SELECT location_name, location_city, location_country FROM vkpi_events WHERE id = ?", (eid,)
    ).fetchone()
    if not row:
        return {"status": "not_found", "event_id": eid}
    ev = dict(row)
    addr = str(address or "").strip() or ", ".join(
        x for x in (ev.get("location_name"), ev.get("location_city"), ev.get("location_country")) if str(x or "").strip()
    )
    if not addr:
        return {"status": "no_address", "hint": "活动无地址,请先填 location_name/city/country 或手动填经纬度"}
    geo = geocode_address(addr)
    if not geo:
        return {"status": "geocode_failed", "address": addr, "hint": "地理编码无结果/不可达,可手动填经纬度(location_lat/lng)"}
    conn.execute(
        "UPDATE vkpi_events SET location_lat = ?, location_lng = ?, updated_at = NOW() WHERE id = ?",
        (geo["lat"], geo["lng"], eid),
    )
    conn.commit()
    return {"status": "ok", "event_id": eid, "address": addr, **geo}
