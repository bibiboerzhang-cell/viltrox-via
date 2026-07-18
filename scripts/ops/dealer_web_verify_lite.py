#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-LLM dealer website verification (direct fetch).

2026-07-18 cost postmortem: the grounded-LLM verifier really costs ~$0.14 per
store (search-grounding tokens + $35/1K surcharge).  For the core business
question — "is the site alive, and does its own domain mention Viltrox?" — a
direct HTTP fetch answers with the same own-domain evidence contract at ~$0.

Per dealer with a website and no receipt today:
  * GET homepage (browser UA, redirects, 15s) → alive / dead / unreachable;
  * search "viltrox" (case-insensitive) in homepage HTML; if absent, probe the
    common platform search URLs (Shopify / WordPress / Magento / generic);
  * first URL whose HTML contains "viltrox" → carries_viltrox=confirmed with
    that URL as evidence; alive-but-absent → not_found;
  * is_camera_retailer: keyword heuristic (camera/photo/lens/…) or NULL —
    never guessed by a model;
  * scale/prominence stay NULL — this tool answers presence, not prominence.

Receipts append to ``vkpi_dealer_web_verification`` with model=direct_fetch_v1
so they are distinguishable from grounded receipts.  Dry-run by default.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_SEARCH_PATTERNS = (
    "/search?q=viltrox",
    "/search?type=product&q=viltrox",
    "/?s=viltrox",
    "/catalogsearch/result/?q=viltrox",
)
_CAMERA_TERMS = ("camera", "photo", "lens", "photograph", "video", "cine")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch(client: Any, url: str) -> tuple[int, str]:
    resp = client.get(url, headers={"User-Agent": _UA})
    return int(resp.status_code), str(resp.text or "")[:400_000]


_NO_RESULT_MARKERS = (
    "no results", "no products", "0 results", "did not match any",
    "nothing found", "no matches", "sorry, no", "couldn't find",
    "no se encontraron", "无结果", "没有找到",
)
_PRODUCT_NEAR = re.compile(
    r"viltrox[^<]{0,80}(?:\$|usd|add to cart|add-to-cart|buy now|/products/|/product/|in stock)",
    re.I,
)


def _search_page_has_product(html: str) -> bool:
    """True only if the search page shows a real Viltrox product, not an echo.

    Search result pages echo the query term (search box value, "no results for
    viltrox" text).  Require a Viltrox mention within product context AND the
    absence of an explicit no-results marker.
    """
    lowered = html.lower()
    if "viltrox" not in lowered:
        return False
    if any(marker in lowered for marker in _NO_RESULT_MARKERS):
        return False
    if 'href="/products/' in lowered and "viltrox" in lowered:
        return True
    return bool(_PRODUCT_NEAR.search(html))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write receipts (default dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="0 = all pending")
    parser.add_argument("--include-unreachable", action="store_true",
                        help="also retry dealers whose latest receipt is website_status=unreachable")
    args = parser.parse_args(argv)

    import httpx

    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        stdout_out("error: requires PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT d.id, d.name, d.website_url
            FROM vkpi_dealers d
            WHERE d.publication_status = 'published'
              AND NULLIF(TRIM(COALESCE(d.website_url, '')), '') IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1 FROM vkpi_dealer_web_verification v
                    WHERE v.dealer_id = d.id
              )
            ORDER BY d.id
            """
        ).fetchall()
    ]
    if args.include_unreachable:
        rows += [
            dict(r)
            for r in conn.execute(
                """
                SELECT d.id, d.name, d.website_url
                FROM vkpi_dealers d
                JOIN LATERAL (
                    SELECT website_status FROM vkpi_dealer_web_verification w
                    WHERE w.dealer_id = d.id ORDER BY w.verified_at DESC LIMIT 1
                ) v ON TRUE
                WHERE d.publication_status = 'published'
                  AND v.website_status = 'unreachable'
                  AND NULLIF(TRIM(COALESCE(d.website_url, '')), '') IS NOT NULL
                ORDER BY d.id
                """
            ).fetchall()
        ]
    if args.limit > 0:
        rows = rows[: args.limit]
    stdout_out(f"pending={len(rows)} mode={'apply' if args.apply else 'dry_run'}")

    counted = {"alive": 0, "dead": 0, "unreachable": 0, "confirmed": 0}
    with httpx.Client(follow_redirects=True, timeout=15.0) as client:
        for row in rows:
            base = str(row["website_url"]).strip().rstrip("/")
            status_label, evidence_url, camera_hint = "unreachable", None, None
            html = ""
            try:
                code, html = _fetch(client, base)
                if code < 400:
                    status_label = "alive"
                elif code in (404, 410):
                    status_label = "dead"
                else:
                    status_label = "unreachable"
            except Exception:
                status_label = "unreachable"
            lowered = html.lower()
            if status_label == "alive":
                camera_hint = any(term in lowered for term in _CAMERA_TERMS) or None
                if "viltrox" in lowered:
                    evidence_url = base
                else:
                    for pattern in _SEARCH_PATTERNS:
                        try:
                            code, sub_html = _fetch(client, base + pattern)
                        except Exception:
                            continue
                        # 2026-07-18 假阳性修:搜索结果页会回显查询词(搜索框
                        # value、"no results for viltrox" 等),裸 "viltrox" 子串
                        # 命中会把 0 结果误判为在售。要求产品级证据:出现 viltrox
                        # 的商品链接/加购/价格上下文,且页面非"无结果"。
                        if code < 400 and _search_page_has_product(sub_html):
                            evidence_url = base + pattern
                            break
            carries = "confirmed" if evidence_url else ("not_found" if status_label == "alive" else "unknown")
            counted[status_label] = counted.get(status_label, 0) + 1
            if carries == "confirmed":
                counted["confirmed"] += 1
            stdout_out(
                f"[{row['id']}] {str(row['name'])[:38]} → {status_label} viltrox={carries}"
                + (f" evidence={evidence_url}" if evidence_url else "")
            )
            if not args.apply:
                continue
            conn.execute(
                """
                INSERT INTO vkpi_dealer_web_verification(
                  dealer_id, verified_at, website_status, is_camera_retailer,
                  carries_viltrox, viltrox_evidence_url, scale_tier,
                  prominence_score, prominence_rationale, evidence_json, model
                ) VALUES (?, ?, ?, ?, ?, ?, 'unknown', NULL, '', ?::jsonb, 'direct_fetch_v1')
                """,
                (
                    int(row["id"]),
                    _now(),
                    status_label,
                    camera_hint,
                    carries,
                    evidence_url,
                    json.dumps(
                        {
                            "method": "direct_fetch_v1",
                            "checked_url": base,
                            "claim_status": "descriptive_only",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
    if args.apply:
        conn.commit()
    stdout_out(f"summary={json.dumps(counted, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
