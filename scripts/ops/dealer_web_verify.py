#!/usr/bin/env python3
"""Web verification sweep for published Dealer rows.

For every published dealer that has a recorded website (or store page URL)
this script asks Gemini with Google Search grounding to verify, in one call:

* website_status   — official site alive / dead / unreachable;
* is_camera_retailer — still selling camera or photo gear on its own site;
* carries_viltrox  — Viltrox product visible on a retailer-owned page
  (confirmed only with a concrete evidence URL on the retailer's own domain);
* scale_tier       — national_chain / regional_chain / local_store /
  online_focus / unknown;
* prominence_score — bounded 0-100 public-prominence signal with rationale
  (chain footprint, media reputation, local review volume).

Results are appended to ``vkpi_dealer_web_verification`` (migration 271);
the read side always takes the latest row per dealer.  Hard boundaries:

* Never writes ``vkpi_dealers``; publication, authorization and fit scoring
  are untouched.  Never touches viltrox fit scoring in any form.
* Every provider call goes through ``llm_production.generate_google_content``
  (strict budget/ledger boundary) under cost scope ``cron:dealer_web_verify``;
  an explicit ``budget_guard.check_budget`` preflight aborts cleanly first.
* Resume-safe: dealers already verified today are skipped unless ``--rerun``.
* Chain-aware: locations sharing one website host reuse the first verdict in
  the same run (one paid call per organization site, one receipt row per
  store, marked ``shared_from_host`` in evidence_json).
* Serial with ~1 QPS pacing; aborts after too many consecutive failures.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402

PURPOSE = "vkpi_dealer_web_verify"
COST_TAG = "cron:dealer_web_verify"
BUDGET_SCOPE = "cron:dealer_web_verify"
EST_COST_USD = 0.004
VERIFY_VERSION = "dealer_web_verify.v1"
WEBSITE_STATUSES = {"alive", "dead", "unreachable"}
CARRIES_VALUES = {"confirmed", "not_found", "unknown"}
SCALE_TIERS = {"national_chain", "regional_chain", "local_store", "online_focus", "unknown"}
REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)
MAX_OUTPUT_TOKENS = 2000
MAX_CONSECUTIVE_FAILURES = 5

PROMPT_TEMPLATE = """MANDATORY: use the Google Search tool to check CURRENT status before \
answering — retailers move, close, or drop brands. Today is {today}.

US camera dealer from our directory: name "{name}", city "{city}", state "{state}", \
recorded official website "{website}".

Answer ALL of the following about this retailer:
1. website_status: is that official site alive and representing this business today
   ("alive"), permanently gone / business closed per its own pages or credible news
   ("dead"), or not verifiable right now ("unreachable")
2. is_camera_retailer: does its OWN site currently show cameras / lenses / photo gear
   for sale (true/false, null if unverifiable)
3. carries_viltrox: does its OWN site show any VILTROX product (lens, EVF, monitor,
   light)  "confirmed" REQUIRES viltrox_evidence_url = a real product/brand/search
   result page ON THE RETAILER'S OWN DOMAIN showing Viltrox. "not_found" = you checked
   its site/search and found none. Otherwise "unknown". Never guess a URL.
4. scale_tier: "national_chain" (stores in many states or top nationwide mail-order),
   "regional_chain" (2+ stores in one region), "local_store" (single walk-in shop),
   "online_focus" (sells online, no meaningful walk-in store), or "unknown".
5. prominence_score 0-100 — how well-known is this retailer, judged from public
   evidence: store count / footprint, press + photo-industry reputation, size of its
   local review base. Calibration: 90-100 top national names (B&H, Adorama);
   70-89 strong regional chains or nationally known specialty shops; 40-69 established
   local stores with a solid review base; 10-39 small shops with a thin public
   footprint; 0-9 almost no public trace. Explain the score in one or two sentences
   citing the evidence types you used.

Return ONLY a JSON object:
{{
  "website_status": "alive" | "dead" | "unreachable",
  "is_camera_retailer": true | false | null,
  "carries_viltrox": "confirmed" | "not_found" | "unknown",
  "viltrox_evidence_url": "https://retailer-own-page-showing-viltrox or empty string",
  "scale_tier": "national_chain" | "regional_chain" | "local_store" | "online_focus" | "unknown",
  "store_count_estimate": 0,
  "prominence_score": 0,
  "prominence_rationale": "one or two sentences citing store count / media reputation / review scale",
  "evidence_urls": ["https://pages-used-as-evidence"],
  "notes": "one short sentence"
}}
URL rules: viltrox_evidence_url must be on the retailer's own domain. NEVER output \
vertexaisearch.cloud.google.com or any google redirect link — write the true \
destination URL instead."""


def _parse_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _host(url: str) -> str:
    return str(urlsplit(str(url or "").strip()).hostname or "").casefold()


def _root_host(host: str) -> str:
    """Drop a leading www. so store/shop subdomains still match the site."""
    return host[4:] if host.startswith("www.") else host


def _same_site(evidence_host: str, dealer_host: str) -> bool:
    root = _root_host(dealer_host)
    candidate = _root_host(evidence_host)
    return bool(root) and (candidate == root or candidate.endswith("." + root))


def _clean_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url.startswith("https://") or len(url) > 2048:
        return ""
    host = _host(url)
    if not host or any(host == item or host.endswith("." + item) for item in REDIRECT_HOSTS):
        return ""
    return url


def _clean_text(value: Any, max_length: int = 400) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def _normalize_verification(
    parsed: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    model: str,
    dealer_host: str,
) -> dict[str, Any]:
    website_status = _clean_text(parsed.get("website_status"), 20).casefold()
    if website_status not in WEBSITE_STATUSES:
        website_status = "unreachable"
    raw_retailer = parsed.get("is_camera_retailer")
    is_camera_retailer = raw_retailer if isinstance(raw_retailer, bool) else None
    carries = _clean_text(parsed.get("carries_viltrox"), 20).casefold()
    if carries not in CARRIES_VALUES:
        carries = "unknown"
    evidence_url = _clean_url(parsed.get("viltrox_evidence_url"))
    demotions: list[str] = []
    if carries == "confirmed":
        if not evidence_url:
            carries, demotions = "unknown", [*demotions, "confirmed_without_evidence_url"]
        elif dealer_host and not _same_site(_host(evidence_url), dealer_host):
            carries, demotions = "unknown", [*demotions, "evidence_url_not_on_dealer_domain"]
    if carries != "confirmed":
        evidence_url = ""
    scale_tier = _clean_text(parsed.get("scale_tier"), 30).casefold()
    if scale_tier not in SCALE_TIERS:
        scale_tier = "unknown"
    try:
        prominence: int | None = max(0, min(100, int(parsed.get("prominence_score"))))
    except (TypeError, ValueError):
        prominence = None
    rationale = _clean_text(parsed.get("prominence_rationale"), 600)
    try:
        store_count = max(0, int(parsed.get("store_count_estimate")))
    except (TypeError, ValueError):
        store_count = 0
    # Migration-271 shape rule: a site that is not alive cannot carry a live
    # camera storefront, a confirmed Viltrox shelf, or a meaningful rank.
    if website_status != "alive":
        if carries == "confirmed":
            carries, evidence_url = "unknown", ""
            demotions.append("confirmed_on_non_alive_site")
        if is_camera_retailer is True:
            is_camera_retailer = None
            demotions.append("retailer_true_on_non_alive_site")
        prominence = None
    evidence_urls: list[str] = []
    for raw_url in (parsed.get("evidence_urls") or [])[:6]:
        cleaned = _clean_url(raw_url)
        if cleaned and cleaned not in evidence_urls:
            evidence_urls.append(cleaned)
    grounding_urls = [
        str(source.get("url") or "") for source in sources if str(source.get("url") or "")
    ][:8]
    return {
        "website_status": website_status,
        "is_camera_retailer": is_camera_retailer,
        "carries_viltrox": carries,
        "viltrox_evidence_url": evidence_url or None,
        "scale_tier": scale_tier,
        "prominence_score": prominence,
        "prominence_rationale": rationale,
        "model": model,
        "evidence": {
            "verify_version": VERIFY_VERSION,
            "dealer_host": dealer_host,
            "store_count_estimate": store_count,
            "evidence_urls": evidence_urls,
            "grounding_status": "grounded" if grounding_urls else "ungrounded",
            "grounding_urls": grounding_urls,
            "demotions": demotions,
            "notes": _clean_text(parsed.get("notes"), 300),
            "claim_status": "descriptive_only",
        },
    }


def _verify_one(dealer: dict[str, Any], *, model: str, website: str) -> dict[str, Any]:
    import app.services.ai.clients.gemini_client as gemini_module
    from google.genai import types
    from app.domains.market.ai_today_contracts import _extract_grounding_sources
    from app.platform import llm_production

    client = getattr(gemini_module, "gemini_client", None)
    if client is None:
        raise RuntimeError("google_client_not_configured (GEMINI_API_KEY missing)")
    prompt = PROMPT_TEMPLATE.format(
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        name=_clean_text(dealer.get("name"), 160),
        city=_clean_text(dealer.get("city"), 80),
        state=_clean_text(dealer.get("state"), 2),
        website=_clean_text(website, 300),
    )
    # NOTE: no response_mime_type — tools + JSON mime is a hard 400 on
    # gemini-2.5-flash and silently disables google_search on gemini-3.5-flash
    # (verified 2026-07-16 in dealer_physical_store_judge).  JSON is requested
    # in the prompt and parsed from fenced output instead.
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        http_options=types.HttpOptions(
            timeout=60_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    attempt_log: list[dict[str, Any]] = []
    response = llm_production.generate_google_content(
        client=client,
        contents=[prompt],
        config=config,
        model=model,
        purpose=PURPOSE,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        estimated_input_tokens=max(1, math.ceil(len(prompt) / 4)),
        cost_tag=COST_TAG,
        metadata={
            "surface": "dealer_web_verify",
            "pipeline_stage": "grounded_verify",
            "task_binding": "",
            "grounding_tool": "google_search",
            "request_content_recorded": False,
        },
        attempt_log=attempt_log,
    )
    raw = str(getattr(response, "text", "") or "").strip()
    sources = _extract_grounding_sources(response)
    if not raw:
        raise RuntimeError("empty_model_response")
    return _normalize_verification(
        _parse_json(raw), sources, model=model, dealer_host=_host(website)
    )


def _insert_receipt(conn: Any, dealer_id: int, verdict: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_dealer_web_verification
            (dealer_id, verified_at, website_status, is_camera_retailer,
             carries_viltrox, viltrox_evidence_url, scale_tier,
             prominence_score, prominence_rationale, evidence_json, model)
        VALUES (?, NOW(), ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?)
        """,
        (
            dealer_id,
            verdict["website_status"],
            verdict["is_camera_retailer"],
            verdict["carries_viltrox"],
            verdict["viltrox_evidence_url"],
            verdict["scale_tier"],
            verdict["prominence_score"],
            verdict["prominence_rationale"],
            json.dumps(
                verdict["evidence"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            verdict["model"],
        ),
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument("--limit", type=int, default=0, help="0 = all pending")
    parser.add_argument("--qps", type=float, default=1.0)
    parser.add_argument("--dealer-id", type=int, default=0, help="verify one dealer only")
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="also reverify dealers that already have a receipt from today (UTC)",
    )
    args = parser.parse_args(argv)

    from app.db.connection import get_conn, is_postgres_runtime, table_exists
    from app.domains.costs import budget_guard

    if not is_postgres_runtime():
        stdout_out("error: dealer_web_verify requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    if not table_exists("vkpi_dealer_web_verification"):
        stdout_out(
            "error: vkpi_dealer_web_verification missing (apply migration 271 first)",
            file=sys.stderr,
        )
        return 2
    conn = get_conn()

    where_parts = [
        "d.publication_status = 'published'",
        "(NULLIF(TRIM(COALESCE(d.website_url, '')), '') IS NOT NULL"
        " OR NULLIF(TRIM(COALESCE(d.location_source_url, '')), '') IS NOT NULL)",
    ]
    params: list[Any] = []
    if args.dealer_id > 0:
        where_parts = ["d.id = ?"]
        params = [args.dealer_id]
    rows = conn.execute(
        f"""
        SELECT d.id, d.name, d.city, d.state, d.website_url, d.location_source_url
        FROM vkpi_dealers d
        WHERE {" AND ".join(where_parts)}
        ORDER BY d.state, d.city, d.id
        """,
        params,
    ).fetchall()

    done_today: set[int] = set()
    if not args.rerun:
        midnight = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        done_rows = conn.execute(
            "SELECT DISTINCT dealer_id FROM vkpi_dealer_web_verification WHERE verified_at >= ?",
            (midnight,),
        ).fetchall()
        done_today = {
            int(row["dealer_id"] if hasattr(row, "keys") else row[0]) for row in done_rows
        }

    pending = []
    for row in rows:
        item = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "name": row[1], "city": row[2], "state": row[3],
            "website_url": row[4], "location_source_url": row[5],
        }
        if int(item["id"]) in done_today:
            continue
        pending.append(item)
    if args.limit > 0:
        pending = pending[: args.limit]
    stdout_out(
        f"pending_dealers={len(pending)} skipped_done_today={len(done_today)} model={args.model}"
    )

    counted = {"alive": 0, "dead": 0, "unreachable": 0, "viltrox_confirmed": 0, "shared": 0}
    failures = 0
    consecutive_failures = 0
    host_cache: dict[str, dict[str, Any]] = {}
    for index, dealer in enumerate(pending, start=1):
        dealer_id = int(dealer["id"])
        website = str(dealer.get("website_url") or "").strip() or str(
            dealer.get("location_source_url") or ""
        ).strip()
        host = _host(website)
        started = time.monotonic()
        cached = host_cache.get(host) if host else None
        try:
            if cached is not None:
                verdict = json.loads(json.dumps(cached))  # deep copy, keep cache pristine
                verdict["evidence"]["shared_from_host"] = host
                counted["shared"] += 1
            else:
                if not budget_guard.check_budget(BUDGET_SCOPE, EST_COST_USD):
                    stdout_out(
                        f"aborting: budget scope {BUDGET_SCOPE} hard-stopped "
                        "(rows remain pending; rerun after the daily window resets)",
                        file=sys.stderr,
                    )
                    break
                verdict = _verify_one(dealer, model=args.model, website=website)
                if host:
                    host_cache[host] = verdict
        except Exception as exc:  # noqa: BLE001 - per-row isolation, row stays retryable
            failures += 1
            consecutive_failures += 1
            stdout_out(
                f"[{index}/{len(pending)}] dealer={dealer_id} FAILED: "
                f"{type(exc).__name__}: {exc}"
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                stdout_out(
                    f"aborting: {consecutive_failures} consecutive provider failures "
                    "(check GEMINI_API_KEY / proxy / readiness ack / budget gate)",
                    file=sys.stderr,
                )
                break
            time.sleep(max(0.0, (1.0 / max(args.qps, 0.1)) - (time.monotonic() - started)))
            continue
        consecutive_failures = 0
        _insert_receipt(conn, dealer_id, verdict)
        counted[verdict["website_status"]] = counted.get(verdict["website_status"], 0) + 1
        if verdict["carries_viltrox"] == "confirmed":
            counted["viltrox_confirmed"] += 1
        stdout_out(
            f"[{index}/{len(pending)}] dealer={dealer_id} {verdict['website_status']}"
            f" retailer={verdict['is_camera_retailer']}"
            f" viltrox={verdict['carries_viltrox']}"
            f" tier={verdict['scale_tier']}"
            f" score={verdict['prominence_score']}"
            f"{' (shared)' if cached is not None else ''} | {dealer.get('name', '')}"
        )
        if cached is None:
            time.sleep(max(0.0, (1.0 / max(args.qps, 0.1)) - (time.monotonic() - started)))

    totals = conn.execute(
        """
        SELECT v.website_status, v.carries_viltrox, COUNT(*) AS n
        FROM vkpi_dealer_web_verification v
        JOIN (
            SELECT dealer_id, MAX(verified_at) AS verified_at
            FROM vkpi_dealer_web_verification
            GROUP BY dealer_id
        ) latest
          ON latest.dealer_id = v.dealer_id AND latest.verified_at = v.verified_at
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (),
    ).fetchall()
    stdout_out(f"run_summary={json.dumps(counted, sort_keys=True)} run_failures={failures}")
    for total in totals:
        status = total["website_status"] if hasattr(total, "keys") else total[0]
        carries = total["carries_viltrox"] if hasattr(total, "keys") else total[1]
        count = total["n"] if hasattr(total, "keys") else total[2]
        stdout_out(f"db_latest website={status} viltrox={carries} count={count}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
