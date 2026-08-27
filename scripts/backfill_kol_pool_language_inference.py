#!/usr/bin/env python3
"""Backfill vkpi_kol_pool.language_inferred from creator-authored text (bio + video titles).

为什么要有这一刀:池里只有约三成的人有平台自报语言(vkpi_kol_pool.language),
按「语言为空」硬筛会把七成人误杀。本脚本用创作者自己写的简介与视频标题/文案推断语言,
落到**单独的** language_inferred 列(带 confidence / source / sample_n / method / at),
自报值一个字都不动。

口径:
  * 判定复用 app.domains.comments.language_detection.language_detect(保守阈值原样沿用),
    合并规则见 app/domains/kol/language_inference.py(逐条判 + 投票,多数不足即未知)。
  * 文本来源:
      bio     —— vkpi_kol_pool.bio;为空时退 raw_platform_data 里的频道自述(YouTube
                 snippet.description / TikTok/IG profile signature 等创作者自述)。
      titles  —— raw_platform_data.videos[]:YouTube snippet.title、TikTok text、
                 Instagram caption;再并入 vkpi_kol_video_evidence 的 video_title/title。
  * 只补空、不覆写(UPDATE 的 WHERE 再守一遍 language_inferred 仍为空),幂等可重复跑。
  * 判不出的人保持 NULL —— 那是「未知」,不是「不合格」。
  * 绝不触碰 language / viltrox_fit_score / 任何新鲜度或证据阈值。
  * 默认 dry-run;必须显式 --apply 才落库。

用法:
  .venv/bin/python scripts/backfill_kol_pool_language_inference.py            # 干跑
  .venv/bin/python scripts/backfill_kol_pool_language_inference.py --compare  # 干跑 + 投票/拼接口径对照
  .venv/bin/python scripts/backfill_kol_pool_language_inference.py --apply    # 落库
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from app.domains.kol.language_inference import (  # noqa: E402
    KOL_LANGUAGE_INFERENCE_VERSION,
    MAX_TEXT_SAMPLES,
    STRATEGY_JOIN,
    infer_language_from_content,
)
from app.domains.kol.pool_common import _clear_kol_pool_read_cache  # noqa: E402


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


def _profile_payloads(raw: dict) -> list[dict]:
    """raw 里可能藏着 channelListResponse{items:[...]} 或直接的 profile 资源。"""
    payloads: list[dict] = []
    profile = raw.get("profile")
    for candidate in (profile, raw):
        if not isinstance(candidate, dict):
            continue
        payloads.append(candidate)
        items = candidate.get("items")
        if isinstance(items, list):
            payloads.extend(item for item in items[:3] if isinstance(item, dict))
    return payloads


_SELF_DESCRIPTION_KEYS = ("description", "signature", "biography", "bio", "about")


def raw_self_description(raw: dict) -> str:
    """创作者自述兜底:YouTube snippet.description / TikTok signature / IG biography。"""
    for payload in _profile_payloads(raw):
        snippet = payload.get("snippet") if isinstance(payload.get("snippet"), dict) else {}
        for source in (snippet, payload):
            for key in _SELF_DESCRIPTION_KEYS:
                text = str(source.get(key) or "").strip()
                if text:
                    return text
    return ""


def raw_video_titles(raw: dict) -> list[str]:
    """视频标题/文案:YouTube snippet.title、TikTok text、Instagram caption。"""
    titles: list[str] = []
    for video in _videos(raw):
        snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
        for value in (snippet.get("title"), video.get("title"), video.get("text"), video.get("caption")):
            text = str(value or "").strip()
            if text:
                titles.append(text)
                break
        if len(titles) >= MAX_TEXT_SAMPLES:
            break
    return titles


def evidence_titles_by_pool(conn: Any, pool_ids: list[int]) -> dict[int, list[str]]:
    """从 vkpi_kol_video_evidence 取标题;按 pool_id 归组(分批查,避免超长 IN)。"""
    grouped: dict[int, list[str]] = {}
    batch = 500
    for start in range(0, len(pool_ids), batch):
        chunk = pool_ids[start:start + batch]
        if not chunk:
            continue
        placeholders = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT kol_pool_id AS pool_id, video_title AS video_title, title AS alt_title "
            "FROM vkpi_kol_video_evidence "
            f"WHERE kol_pool_id IN ({placeholders}) "
            "ORDER BY id ASC",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            item = dict(row)
            pool_id = int(item.get("pool_id") or 0)
            if not pool_id:
                continue
            text = str(item.get("video_title") or item.get("alt_title") or "").strip()
            if not text:
                continue
            bucket = grouped.setdefault(pool_id, [])
            if len(bucket) < MAX_TEXT_SAMPLES:
                bucket.append(text)
    return grouped


def _texts_for(item: dict[str, Any], evidence: dict[int, list[str]]) -> tuple[str, list[str]]:
    raw = _loads(item.get("raw_platform_data"))
    if not isinstance(raw, dict):
        raw = {}
    bio = str(item.get("bio") or "").strip() or raw_self_description(raw)
    titles = raw_video_titles(raw) + evidence.get(int(item["id"]), [])
    return bio, titles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正落库;默认 dry-run 只统计")
    parser.add_argument("--compare", action="store_true", help="同时跑「拼接」对照口径,输出两者差异")
    parser.add_argument("--limit", type=int, default=0, help="最多扫描多少行(0=不限)")
    args = parser.parse_args()

    conn = get_conn()
    sql = """
        SELECT id, platform, handle, language, bio, language_inferred, raw_platform_data
        FROM vkpi_kol_pool
        WHERE duplicate_of_id IS NULL
          AND (language IS NULL OR TRIM(language) = '')
          AND (language_inferred IS NULL OR TRIM(language_inferred) = '')
        ORDER BY id ASC
        """
    if args.limit and args.limit > 0:
        rows = conn.execute(sql + " LIMIT ?", (int(args.limit),)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    items = [dict(row) for row in rows]
    evidence = evidence_titles_by_pool(conn, [int(item["id"]) for item in items])
    now = datetime.now(timezone.utc)

    scanned = len(items)
    with_text = 0
    inferred = 0
    written = 0
    languages: Counter[str] = Counter()
    confidences: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    unknown_reasons: Counter[str] = Counter()
    join_only = 0
    vote_only = 0
    disagree = 0
    join_inferred = 0
    samples: list[dict[str, Any]] = []

    for item in items:
        pool_id = int(item["id"])
        bio, titles = _texts_for(item, evidence)
        if bio or titles:
            with_text += 1
        verdict = infer_language_from_content(bio=bio, titles=titles)
        code = verdict.get("language")

        if args.compare:
            other = infer_language_from_content(bio=bio, titles=titles, strategy=STRATEGY_JOIN)
            other_code = other.get("language")
            if other_code:
                join_inferred += 1
            if code and not other_code:
                vote_only += 1
            elif other_code and not code:
                join_only += 1
            elif code and other_code and code != other_code:
                disagree += 1

        if not code:
            unknown_reasons[str(verdict.get("unknown_reason") or "unknown")] += 1
            continue

        inferred += 1
        languages[str(code)] += 1
        confidences[str(verdict.get("confidence") or "")] += 1
        sources[str(verdict.get("source") or "")] += 1
        if len(samples) < 15:
            samples.append({
                "id": pool_id,
                "platform": item.get("platform"),
                "handle": item.get("handle"),
                "language_inferred": code,
                "confidence": verdict.get("confidence"),
                "source": verdict.get("source"),
                "sample_n": verdict.get("sample_n"),
                "decided_n": verdict.get("decided_n"),
            })

        if not args.apply:
            continue
        result = conn.execute(
            """
            UPDATE vkpi_kol_pool
            SET language_inferred = ?,
                language_inferred_confidence = ?,
                language_inferred_source = ?,
                language_inferred_sample_n = ?,
                language_inferred_at = ?,
                language_inferred_method = ?
            WHERE id = ?
              AND (language_inferred IS NULL OR TRIM(language_inferred) = '')
            """,
            (
                str(code),
                str(verdict.get("confidence") or ""),
                str(verdict.get("source") or ""),
                int(verdict.get("sample_n") or 0),
                now,
                KOL_LANGUAGE_INFERENCE_VERSION,
                pool_id,
            ),
        )
        written += int(getattr(result, "rowcount", 0) or 0)

    if args.apply:
        conn.commit()
        _clear_kol_pool_read_cache()

    payload: dict[str, Any] = {
        "apply": bool(args.apply),
        "method": KOL_LANGUAGE_INFERENCE_VERSION,
        "scanned_language_empty": scanned,
        "with_inferable_text": with_text,
        "inferred": inferred,
        "written": written if args.apply else 0,
        "still_unknown": scanned - inferred,
        "unknown_reasons": dict(unknown_reasons.most_common()),
        "by_language": dict(languages.most_common(20)),
        "by_confidence": dict(confidences.most_common()),
        "by_source": dict(sources.most_common()),
        "samples": samples,
    }
    if args.compare:
        payload["strategy_compare"] = {
            "vote_inferred": inferred,
            "join_inferred": join_inferred,
            "vote_only": vote_only,
            "join_only": join_only,
            "disagree": disagree,
        }
    stdout_out(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
