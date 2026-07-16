#!/usr/bin/env python3
"""Backfill vkpi_kol_pool.language / country from raw_platform_data local signals.

信号口径(只用已落库 raw,不发任何网络请求):
  language:
    - tiktok  : raw.videos[] 的 textLanguage 众数(排除 "un"/空)。
    - youtube : profile(YT API channelListResponse)snippet.defaultLanguage 优先;
                否则 videos[](YT API video 资源)snippet.defaultLanguage /
                defaultAudioLanguage 众数。
    - instagram: raw 无语言字段,跳过。
  country:
    - youtube : profile snippet.country(ISO 码大写)。
    - tiktok  : raw.videos[] 的 locationMeta 众数国(countryCode 实为 GeoNames
                国家 id,先查映射表;查不到再用 address 尾段国名兜底)。
                同国 >= 2 条且无并列才写,写 ISO 码大写。

规矩:
  - 只补空、不覆写(UPDATE 的 WHERE 再守一遍仍为空),幂等可重复跑。
  - 默认 dry-run,只打印计划;加 --write 才落库。
  - 绝不触碰任何评分字段(viltrox_fit_score 等)。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号。

用法:
  .venv/bin/python scripts/backfill_pool_lang_geo.py            # dry-run,只看分项计数
  .venv/bin/python scripts/backfill_pool_lang_geo.py --write    # 落库
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/build_kol_profile_index.py)。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.kol.pool_common import _clear_kol_pool_read_cache  # noqa: E402

# TikTok locationMeta.countryCode 实为 GeoNames 国家 geonameId(实测:2782113=AT、
# 3144096=NO、6252001=US...),不是 ISO 码。查表转 ISO;缺表项走 address 尾段兜底。
GEONAME_COUNTRY_ISO: dict[str, str] = {
    "6252001": "US", "2635167": "GB", "6251999": "CA", "2077456": "AU",
    "2921044": "DE", "3017382": "FR", "3175395": "IT", "2510769": "ES",
    "2264397": "PT", "2750405": "NL", "2802361": "BE", "2658434": "CH",
    "2782113": "AT", "2661886": "SE", "3144096": "NO", "2623032": "DK",
    "660013": "FI", "2963597": "IE", "798544": "PL", "3077311": "CZ",
    "390903": "GR", "298795": "TR", "2017370": "RU", "690791": "UA",
    "1861060": "JP", "1835841": "KR", "1814991": "CN", "1819730": "HK",
    "1668284": "TW", "1880251": "SG", "1733045": "MY", "1605651": "TH",
    "1562822": "VN", "1694008": "PH", "1643084": "ID", "1269750": "IN",
    "1168579": "PK", "1210997": "BD", "290557": "AE", "102358": "SA",
    "294640": "IL", "357994": "EG", "2328926": "NG", "953987": "ZA",
    "192950": "KE", "2542007": "MA", "3469034": "BR", "3996063": "MX",
    "3865483": "AR", "3895114": "CL", "3686110": "CO", "3932488": "PE",
    "2186224": "NZ",
}

# address 尾段国名 → ISO(兜底路径;只收高置信全称,别猜)。
COUNTRY_NAME_ISO: dict[str, str] = {
    "united states": "US", "usa": "US", "united states of america": "US",
    "united kingdom": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "canada": "CA", "australia": "AU", "germany": "DE", "deutschland": "DE",
    "france": "FR", "italy": "IT", "italia": "IT", "spain": "ES",
    "portugal": "PT", "netherlands": "NL", "belgium": "BE",
    "switzerland": "CH", "austria": "AT", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "ireland": "IE", "poland": "PL",
    "czech republic": "CZ", "czechia": "CZ", "greece": "GR", "turkey": "TR",
    "russia": "RU", "ukraine": "UA", "japan": "JP", "south korea": "KR",
    "korea": "KR", "china": "CN", "hong kong": "HK", "taiwan": "TW",
    "singapore": "SG", "malaysia": "MY", "thailand": "TH", "vietnam": "VN",
    "viet nam": "VN", "philippines": "PH", "indonesia": "ID", "india": "IN",
    "pakistan": "PK", "bangladesh": "BD", "united arab emirates": "AE",
    "saudi arabia": "SA", "israel": "IL", "egypt": "EG", "nigeria": "NG",
    "south africa": "ZA", "kenya": "KE", "morocco": "MA", "brazil": "BR",
    "brasil": "BR", "mexico": "MX", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "peru": "PE", "new zealand": "NZ",
}

ISO_RE = re.compile(r"^[A-Z]{2}$")


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return {}


def _videos(raw: dict) -> list[dict]:
    value = raw.get("videos")
    if isinstance(value, dict):
        value = value.get("items") or []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _yt_channel_snippet(raw: dict) -> dict:
    """profile 可能是 channelListResponse{items:[...]} 或直接的 channel 资源。"""
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    for payload in (profile, raw):
        if not isinstance(payload, dict):
            continue
        items = payload.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            snippet = items[0].get("snippet")
            if isinstance(snippet, dict):
                return snippet
        snippet = payload.get("snippet")
        if isinstance(snippet, dict) and str(payload.get("kind") or "").endswith("channel"):
            return snippet
    return {}


def _mode(values: list[str]) -> str:
    """众数;并列取字典序最小保证确定性。空列表返回空串。"""
    if not values:
        return ""
    counts = Counter(values)
    top = max(counts.values())
    return sorted(v for v, n in counts.items() if n == top)[0]


def infer_language(platform: str, raw: dict) -> str:
    if platform == "tiktok":
        langs = []
        for video in _videos(raw):
            lang = str(video.get("textLanguage") or "").strip()
            if lang and lang.lower() != "un":
                langs.append(lang)
        return _mode(langs)[:20]
    if platform == "youtube":
        snippet = _yt_channel_snippet(raw)
        for key in ("defaultLanguage", "defaultAudioLanguage"):
            lang = str(snippet.get(key) or "").strip()
            if lang:
                return lang[:20]
        for key in ("defaultLanguage", "defaultAudioLanguage"):
            langs = []
            for video in _videos(raw):
                vsnip = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
                lang = str(vsnip.get(key) or "").strip()
                if lang and lang.lower() != "un":
                    langs.append(lang)
            if langs:
                return _mode(langs)[:20]
        return ""
    # instagram 等:raw 无语言字段,跳过。
    return ""


def _tiktok_video_country(video: dict) -> str:
    meta = video.get("locationMeta")
    if not isinstance(meta, dict):
        return ""
    code = str(meta.get("countryCode") or "").strip()
    iso = GEONAME_COUNTRY_ISO.get(code, "")
    if not iso and ISO_RE.match(code.upper()):
        iso = code.upper()  # 万一某批 actor 直接给了 ISO 码
    if not iso:
        address = str(meta.get("address") or "").strip()
        tail = address.rsplit(",", 1)[-1].strip().lower()
        iso = COUNTRY_NAME_ISO.get(tail, "")
    return iso


def infer_country(platform: str, raw: dict) -> str:
    if platform == "youtube":
        code = str(_yt_channel_snippet(raw).get("country") or "").strip().upper()
        return code if ISO_RE.match(code) else ""
    if platform == "tiktok":
        codes = [c for c in (_tiktok_video_country(v) for v in _videos(raw)) if c]
        if not codes:
            return ""
        counts = Counter(codes)
        top_code, top_n = counts.most_common(1)[0]
        if top_n < 2:
            return ""  # 拍摄地不等于常驻国,单条信号太弱不写
        if len([c for c, n in counts.items() if n == top_n]) > 1:
            return ""  # 并列众数视为歧义,不写
        return top_code
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="真正落库;默认 dry-run 只打印计划")
    parser.add_argument("--limit", type=int, default=0, help="最多扫描多少行(0=不限)")
    args = parser.parse_args()

    conn = get_conn()
    sql = """
        SELECT id, platform, handle, language, country, raw_platform_data
        FROM vkpi_kol_pool
        WHERE (language IS NULL OR TRIM(language) = '')
           OR (country IS NULL OR TRIM(country) = '')
        ORDER BY id ASC
        """
    if args.limit and args.limit > 0:
        rows = conn.execute(sql + " LIMIT ?", (int(args.limit),)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scanned = len(rows)
    lang_planned: Counter[str] = Counter()
    lang_written = 0
    lang_values: Counter[str] = Counter()
    country_planned: Counter[str] = Counter()
    country_written = 0
    country_values: Counter[str] = Counter()
    skipped_no_signal = 0
    samples: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        pool_id = int(item["id"])
        platform = str(item.get("platform") or "").strip().lower()
        raw = _loads(item.get("raw_platform_data"))
        if not isinstance(raw, dict):
            raw = {}

        lang_missing = not str(item.get("language") or "").strip()
        country_missing = not str(item.get("country") or "").strip()

        new_lang = infer_language(platform, raw) if lang_missing else ""
        new_country = infer_country(platform, raw) if country_missing else ""

        if not new_lang and not new_country:
            skipped_no_signal += 1
            continue

        if new_lang:
            lang_planned[platform] += 1
            lang_values[new_lang] += 1
        if new_country:
            country_planned[platform] += 1
            country_values[new_country] += 1
        if len(samples) < 20:
            samples.append({
                "id": pool_id,
                "platform": platform,
                "handle": item.get("handle"),
                "language": new_lang or None,
                "country": new_country or None,
            })

        if not args.write:
            continue
        if new_lang:
            result = conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET language = ?, updated_at = ?
                WHERE id = ? AND (language IS NULL OR TRIM(language) = '')
                """,
                (new_lang, now, pool_id),
            )
            lang_written += int(getattr(result, "rowcount", 0) or 0)
        if new_country:
            result = conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET country = ?, updated_at = ?
                WHERE id = ? AND (country IS NULL OR TRIM(country) = '')
                """,
                (new_country, now, pool_id),
            )
            country_written += int(getattr(result, "rowcount", 0) or 0)

    if args.write:
        conn.commit()
        _clear_kol_pool_read_cache()

    stdout_out(json.dumps({
        "write": bool(args.write),
        "scanned_missing_lang_or_country": scanned,
        "language": {
            "planned_by_platform": dict(lang_planned),
            "written": lang_written if args.write else 0,
            "top_values": dict(lang_values.most_common(10)),
        },
        "country": {
            "planned_by_platform": dict(country_planned),
            "written": country_written if args.write else 0,
            "top_values": dict(country_values.most_common(10)),
        },
        "skipped_no_signal": skipped_no_signal,
        "samples": samples,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
