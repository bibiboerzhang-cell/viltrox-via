#!/usr/bin/env python3
"""Advisory physical-store judge for staged Nikon dealer candidates.

For every pending ``dealer_location`` candidate this script asks Gemini with
Google Search grounding whether the organization operates a physical camera
store in the US, and stores the structured answer under
``candidate_payload_json.judge``.

Hard boundaries:

* The verdict is a **suggestion only**.  This script never writes
  ``vkpi_dealers``, never changes ``review_status`` or the promotion gate, and
  never publishes anything.
* Every call goes through ``llm_production.generate_google_content`` — the
  strict budget/ledger boundary used by AI Today grounded discovery.
* Resume-safe: candidates that already carry a ``judge`` object are skipped;
  provider failures leave the row untouched for the next run.
* Serial with ~1 QPS pacing; aborts after too many consecutive failures
  instead of hammering the provider.
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

SOURCE_REGISTRY_ID = "nikon_usa_pdf_20260716"
PURPOSE = "vkpi_dealer_store_judge"
COST_TAG = "ops:dealer_store_judge"
JUDGE_VERSION = "dealer_store_judge.v1"
VERDICTS = {"physical", "online_only", "unclear"}
MANUFACTURER_HOSTS = ("nikonusa.com", "nikon.com", "canon.com", "sony.com", "godox.com")
AGGREGATOR_HOSTS = (
    "google.com", "maps.google.com", "yelp.com", "facebook.com", "instagram.com",
    "mapquest.com", "yellowpages.com", "wikipedia.org", "tripadvisor.com",
)
MAX_OUTPUT_TOKENS = 1200
MAX_CONSECUTIVE_FAILURES = 5

PROMPT_TEMPLATE = """MANDATORY: use the Google Search tool to check CURRENT status before \
answering — store details change (moves, closures, fires). Today is {today}.

Organization from a public dealer directory: name "{name}", city "{city}", state "{state}".

Question: does this organization operate at least one physical walk-in camera/electronics \
store in the United States, verifiable on the retailer's OWN official website?

Rules:
- Prefer the retailer's own domain (store-locator, contact, visit-us pages) as evidence.
- Do NOT use Google Maps, Yelp, Facebook, Wikipedia, directories, or manufacturer sites \
(nikonusa.com, nikon.com, canon.com, sony.com, godox.com) as evidence.
- "physical" only if the retailer's own site shows a street address for a walk-in store.
- "online_only" if its own site shows it sells online without any walk-in store.
- Otherwise "unclear". Never guess an address; omit fields you cannot verify.

Return ONLY a JSON object:
{{
  "verdict": "physical" | "online_only" | "unclear",
  "confidence": 0.0-1.0,
  "retailer_website": "https://... (retailer's own homepage, or empty string)",
  "stores": [
    {{"name": "...", "address": "street address", "city": "...", "state": "2-letter",
      "postal_code": "...", "phone": "...", "hours": "...",
      "store_page_url": "https://retailer-own-page-showing-this-address"}}
  ],
  "evidence_urls": ["https://retailer-own-pages-used-as-evidence"],
  "notes": "one short sentence"
}}
Include at most 3 stores. URL rules: every URL must be the retailer's real page on its own \
domain. NEVER output vertexaisearch.cloud.google.com or any google redirect link — write the \
true destination URL instead."""


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


def _is_blocked_host(host: str, blocked: tuple[str, ...]) -> bool:
    return any(host == item or host.endswith("." + item) for item in blocked)


def _clean_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url.startswith("https://") or len(url) > 2048:
        return ""
    host = _host(url)
    if not host or _is_blocked_host(host, MANUFACTURER_HOSTS) or _is_blocked_host(host, AGGREGATOR_HOSTS):
        return ""
    return url


def _clean_text(value: Any, max_length: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def _normalize_judge(parsed: dict[str, Any], sources: list[dict[str, Any]], model: str) -> dict[str, Any]:
    verdict = _clean_text(parsed.get("verdict"), 20).casefold()
    if verdict not in VERDICTS:
        verdict = "unclear"
    try:
        confidence = min(1.0, max(0.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    retailer_website = _clean_url(parsed.get("retailer_website"))
    stores: list[dict[str, str]] = []
    for raw_store in (parsed.get("stores") or [])[:3]:
        if not isinstance(raw_store, dict):
            continue
        stores.append(
            {
                "name": _clean_text(raw_store.get("name"), 160),
                "address": _clean_text(raw_store.get("address"), 200),
                "city": _clean_text(raw_store.get("city"), 80),
                "state": _clean_text(raw_store.get("state"), 2).upper(),
                "postal_code": _clean_text(raw_store.get("postal_code"), 12),
                "phone": _clean_text(raw_store.get("phone"), 32),
                "hours": _clean_text(raw_store.get("hours"), 200),
                "store_page_url": _clean_url(raw_store.get("store_page_url")),
            }
        )
    evidence_urls = []
    for raw_url in (parsed.get("evidence_urls") or [])[:6]:
        cleaned = _clean_url(raw_url)
        if cleaned and cleaned not in evidence_urls:
            evidence_urls.append(cleaned)
    grounding_urls = [
        str(source.get("url") or "") for source in sources if str(source.get("url") or "")
    ][:8]
    if verdict == "physical" and not (retailer_website and stores):
        verdict = "unclear"
    return {
        "judge_version": JUDGE_VERSION,
        "model": model,
        "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "confidence": confidence,
        "retailer_website": retailer_website,
        "stores": stores,
        "evidence_urls": evidence_urls,
        "grounding_status": "grounded" if grounding_urls else "ungrounded",
        "grounding_urls": grounding_urls,
        "notes": _clean_text(parsed.get("notes"), 300),
        "claim_status": "descriptive_only",
        "advisory_only": True,
    }


def _judge_one(row_payload: dict[str, Any], model: str) -> dict[str, Any]:
    import app.services.ai.clients.gemini_client as gemini_module
    from google.genai import types
    from app.domains.market.ai_today_contracts import _extract_grounding_sources
    from app.platform import llm_production

    client = getattr(gemini_module, "gemini_client", None)
    if client is None:
        raise RuntimeError("google_client_not_configured (GEMINI_API_KEY missing)")
    source_row = row_payload.get("row") or {}
    prompt = PROMPT_TEMPLATE.format(
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        name=_clean_text(source_row.get("organization_name"), 160),
        city=_clean_text(source_row.get("city"), 80),
        state=_clean_text(source_row.get("state"), 2),
    )
    # NOTE: no response_mime_type here.  On gemini-2.5-flash the combination of
    # tools + JSON mime is a hard 400; on gemini-3.5-flash it is accepted but
    # silently disables the google_search tool (verified 2026-07-16), so the
    # judge asks for JSON in the prompt and parses fenced output instead
    # (model agnostic).  2026-08-22 模型升级刀:默认模型改 gemini-3.6-flash;
    # 3.x 家族 thinking_level=minimal 由 llm_production 的 google helper 按家族
    # 注入(带 tools 的请求不注入),本脚本不自行构造 thinking_budget / temperature。
    # 3.6-flash 上 google_search + JSON-mime 组合未在本刀实弹复验,路线不变即安全。
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
            "surface": "dealer_store_judge",
            "pipeline_stage": "grounded_judge",
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
    return _normalize_judge(_parse_json(raw), sources, model)


def main(argv: list[str] | None = None) -> int:
    global SOURCE_REGISTRY_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, default=1)
    parser.add_argument("--source-registry-id", default=SOURCE_REGISTRY_ID)
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--limit", type=int, default=0, help="0 = all pending")
    parser.add_argument("--qps", type=float, default=1.0)
    parser.add_argument(
        "--rejudge-ungrounded",
        action="store_true",
        help="also rejudge rows whose stored judge is unclear+ungrounded",
    )
    args = parser.parse_args(argv)
    SOURCE_REGISTRY_ID = args.source_registry_id

    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        stdout_out("error: judge requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()
    where_missing = "(candidate_payload_json->'judge') IS NULL"
    if args.rejudge_ungrounded:
        where_missing = (
            "((candidate_payload_json->'judge') IS NULL OR ("
            "candidate_payload_json->'judge'->>'verdict' = 'unclear' AND "
            "candidate_payload_json->'judge'->>'grounding_status' = 'ungrounded'))"
        )
    rows = conn.execute(
        f"""
        SELECT id, candidate_payload_json
        FROM vkpi_dealer_event_candidates
        WHERE organization_id=? AND candidate_type='dealer_location'
          AND source_registry_id=? AND {where_missing}
        ORDER BY source_entity_key
        """,
        (args.organization_id, SOURCE_REGISTRY_ID),
    ).fetchall()
    if args.limit > 0:
        rows = rows[: args.limit]
    stdout_out(f"pending_candidates={len(rows)} model={args.model}")

    judged = {"physical": 0, "online_only": 0, "unclear": 0}
    failures = 0
    consecutive_failures = 0
    for index, row in enumerate(rows, start=1):
        candidate_id = str(row["id"] if hasattr(row, "keys") else row[0])
        payload_raw = row["candidate_payload_json"] if hasattr(row, "keys") else row[1]
        payload = payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw or "{}")
        started = time.monotonic()
        try:
            judge = _judge_one(payload, args.model)
        except Exception as exc:  # noqa: BLE001 - per-row isolation, row stays retryable
            failures += 1
            consecutive_failures += 1
            stdout_out(f"[{index}/{len(rows)}] {candidate_id} FAILED: {type(exc).__name__}: {exc}")
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
        conn.execute(
            """
            UPDATE vkpi_dealer_event_candidates
            SET candidate_payload_json =
                  jsonb_set(candidate_payload_json, '{judge}', ?::jsonb, true),
                updated_at = NOW()
            WHERE organization_id=? AND id=?
            """,
            (
                json.dumps(judge, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                args.organization_id,
                candidate_id,
            ),
        )
        conn.commit()
        judged[judge["verdict"]] += 1
        org = (payload.get("row") or {}).get("organization_name", "")
        stdout_out(
            f"[{index}/{len(rows)}] {candidate_id} {judge['verdict']}"
            f" conf={judge['confidence']:.2f} stores={len(judge['stores'])}"
            f" grounded={judge['grounding_status'] == 'grounded'} | {org}"
        )
        time.sleep(max(0.0, (1.0 / max(args.qps, 0.1)) - (time.monotonic() - started)))

    totals = conn.execute(
        """
        SELECT candidate_payload_json->'judge'->>'verdict' AS verdict, COUNT(*) AS n
        FROM vkpi_dealer_event_candidates
        WHERE organization_id=? AND candidate_type='dealer_location'
          AND source_registry_id=? AND (candidate_payload_json->'judge') IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
        (args.organization_id, SOURCE_REGISTRY_ID),
    ).fetchall()
    stdout_out(f"run_judged={json.dumps(judged, sort_keys=True)} run_failures={failures}")
    for total in totals:
        verdict = total["verdict"] if hasattr(total, "keys") else total[0]
        count = total["n"] if hasattr(total, "keys") else total[1]
        stdout_out(f"db_total verdict={verdict} count={count}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
