#!/usr/bin/env python3
"""Step 4b social evidence dry-run/commit scraper.

Supported platforms:
  - Instagram via apify/instagram-scraper
  - TikTok via clockworks/tiktok-scraper
  - Facebook via apify/facebook-posts-scraper

Dry-run is the default. Commit mode only inserts matched evidence rows and is
kept behind an explicit --commit flag for the reviewed second round.
"""

from __future__ import annotations

from stdout_utils import out

import argparse
import csv
import os
import re
import time
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


CATALOG_PATH = Path("artifacts/viltrox_product_sku_catalog_20260527.csv")
ENRICH_PATH = Path("/Users/bibiboer/Downloads/vkpi-final/scripts/enrich_video_urls.py")
ARTIFACT_DIR = Path("artifacts")
STEP4B_KOL_IDS = {3345, 3669, 3720, 3733, 3767, 3796, 3873, 3639}

DEFAULT_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "tiktok": "clockworks~tiktok-scraper",
    "facebook": "apify~facebook-posts-scraper",
}


@dataclass
class KolTarget:
    id: int
    display_name: str
    handle: str
    platform: str
    profile_url: str


@dataclass
class SocialCandidate:
    kol_id: int
    kol_name: str
    platform: str
    video_url: str
    title: str
    description: str
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    publish_date: str
    duration_seconds: int | None
    thumbnail_url: str
    channel_id: str
    channel_name: str
    scrape_source: str = "apify"
    scrape_status: str = "success"
    scrape_error: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    confidence: str = "medium"
    hashtag_hit: bool = False
    actor_run_id: str = ""


@dataclass
class SocialRun:
    kol: KolTarget
    actor_id: str
    status: str
    cleaned_url: str
    scanned_count: int = 0
    matches: list[SocialCandidate] = field(default_factory=list)
    error: str = ""
    run_id: str = ""
    usage_usd: float | None = None
    sample_keys: list[list[str]] = field(default_factory=list)


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def text(value: Any) -> str:
    return str(value or "").strip()


def connect_db():
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def parse_ids(value: str) -> set[int] | None:
    if not value.strip():
        return None
    return {int(part.strip()) for part in value.split(",") if part.strip()}


def load_targets(kol_ids: set[int] | None) -> list[KolTarget]:
    ids = sorted(kol_ids or STEP4B_KOL_IDS)
    query = """
        SELECT id, display_name, handle, platform, profile_url
        FROM vkpi_kol_pool
        WHERE id = ANY(%s)
        ORDER BY platform, id
    """
    with connect_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (ids,))
            return [
                KolTarget(
                    id=int(row["id"]),
                    display_name=text(row["display_name"]),
                    handle=text(row["handle"]),
                    platform=infer_platform(text(row["profile_url"]), text(row["platform"]).lower()),
                    profile_url=text(row["profile_url"]),
                )
                for row in cur.fetchall()
            ]


def infer_platform(profile_url: str, fallback: str) -> str:
    lowered = text(profile_url).lower()
    if "instagram.com" in lowered:
        return "instagram"
    if "tiktok.com" in lowered:
        return "tiktok"
    if "facebook.com" in lowered or "fb.com" in lowered:
        return "facebook"
    return fallback


def extract_keywords_from_enrich(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    match = re.search(r"VILTROX_KEYWORDS\s*=\s*\[(.*?)\]", content, re.S)
    if not match:
        return []
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def clean_keyword(value: str) -> str:
    keyword = re.sub(r"\s+", " ", text(value)).lower()
    if keyword.startswith("viltrox ") and len(keyword) > 12:
        keyword = keyword.removeprefix("viltrox ").strip()
    return keyword


def load_keywords() -> list[str]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Missing catalog CSV: {CATALOG_PATH}")
    if not ENRICH_PATH.exists():
        raise FileNotFoundError(f"Missing enrich script: {ENRICH_PATH}")
    raw = ["viltrox"]
    with CATALOG_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            raw.extend([text(row.get("model_name")), text(row.get("marketing_name"))])
    raw.extend(extract_keywords_from_enrich(ENRICH_PATH))
    seen: set[str] = set()
    keywords: list[str] = []
    for item in raw:
        keyword = clean_keyword(item)
        if len(keyword) < 3 or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        parsed = int(value)
        return parsed if parsed >= 0 else None
    raw = text(value).replace(",", "")
    match = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)([km])?", raw)
    if match:
        number = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            number *= 1_000
        elif suffix == "m":
            number *= 1_000_000
        parsed = int(number)
        return parsed if parsed >= 0 else None
    digits = re.sub(r"[^\d]", "", raw)
    parsed = int(digits) if digits else None
    return parsed if parsed is None or parsed >= 0 else None


def parse_duration(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    raw = text(value)
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    parts = raw.split(":")
    if all(part.isdigit() for part in parts):
        values = [int(part) for part in parts]
        if len(values) == 2:
            return values[0] * 60 + values[1]
        if len(values) == 3:
            return values[0] * 3600 + values[1] * 60 + values[2]
    return None


def normalize_date(value: Any) -> tuple[str, str]:
    raw = text(value)
    if not raw:
        return "", ""
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw, ""
    if re.fullmatch(r"\d{10,13}", raw):
        timestamp = int(raw[:10])
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), ""
    if re.search(r"(?i)\b(second|minute|hour|day|week|month|year)s? ago\b", raw):
        return "", f"relative_publish_date:{raw}"
    return raw, ""


def normalize_text_for_match(value: str) -> str:
    return re.sub(r"#(?=\w)", "", value.lower())


def extract_hashtags(value: str) -> list[str]:
    return [tag.lower() for tag in re.findall(r"#([a-zA-Z0-9_]+)", value or "")]


def match_keywords(title: str, description: str, keywords: list[str]) -> tuple[list[str], bool]:
    source = f"{title} {description}"
    haystack = normalize_text_for_match(source)
    hits = [keyword for keyword in keywords if keyword in haystack]
    tags = extract_hashtags(source)
    hashtag_hit = False
    if tags and hits:
        tag_blob = " ".join(tags)
        hashtag_hit = any(keyword.replace(" ", "") in tag_blob for keyword in hits)
    return hits, hashtag_hit


def clean_instagram_url(url: str, handle: str) -> str:
    raw = text(url)
    if not raw:
        raw = f"https://www.instagram.com/{handle.strip('@')}/"
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path.strip("/")
    username = path.split("/")[0] if path else handle.strip("@")
    return f"https://www.instagram.com/{username}/" if username else raw


def tiktok_username(url: str, handle: str) -> str:
    raw = text(url)
    match = re.search(r"tiktok\.com/@([^/?#]+)", raw)
    if match:
        return match.group(1).strip("@")
    return handle.strip("@")


def actor_id_for(platform: str) -> str:
    platform = platform.lower()
    if platform == "instagram":
        return (
            os.environ.get("APIFY_INSTAGRAM_POSTS_ACTOR_ID")
            or os.environ.get("APIFY_INSTAGRAM_ACTOR_ID")
            or DEFAULT_ACTORS[platform]
        ).replace("/", "~")
    if platform == "tiktok":
        return (os.environ.get("APIFY_TIKTOK_ACTOR_ID") or DEFAULT_ACTORS[platform]).replace("/", "~")
    if platform == "facebook":
        return (
            os.environ.get("APIFY_FACEBOOK_POSTS_ACTOR_ID")
            or os.environ.get("APIFY_FACEBOOK_ACTOR_ID")
            or DEFAULT_ACTORS[platform]
        ).replace("/", "~")
    return ""


def run_input_for(kol: KolTarget, max_per_channel: int) -> tuple[dict[str, Any], str]:
    if kol.platform == "instagram":
        clean_url = clean_instagram_url(kol.profile_url, kol.handle)
        return {
            "directUrls": [clean_url],
            "resultsType": "posts",
            "resultsLimit": max_per_channel,
        }, clean_url
    if kol.platform == "tiktok":
        username = tiktok_username(kol.profile_url, kol.handle)
        return {
            "profiles": [username],
            "resultsPerPage": max_per_channel,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }, f"https://www.tiktok.com/@{username}" if username else kol.profile_url
    if kol.platform == "facebook":
        return {
            "startUrls": [{"url": kol.profile_url}],
            "resultsLimit": max_per_channel,
        }, kol.profile_url
    return {}, kol.profile_url


def first_present(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in item and item.get(name) not in (None, ""):
            return item.get(name)
    return None


def first_image(item: dict[str, Any]) -> str:
    for value in (
        item.get("displayUrl"),
        item.get("thumbnailUrl"),
        item.get("coverUrl"),
        item.get("image"),
    ):
        if text(value):
            return text(value)
    images = item.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return text(first.get("url") or first.get("src"))
    covers = item.get("covers")
    if isinstance(covers, list) and covers:
        first = covers[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return text(first.get("url") or first.get("src"))
    meta = item.get("videoMeta")
    if isinstance(meta, dict):
        return text(meta.get("coverUrl") or meta.get("cover"))
    return ""


def candidate_url(platform: str, item: dict[str, Any]) -> str:
    url = text(first_present(item, ("url", "postUrl", "webVideoUrl", "videoUrl", "link")))
    if url:
        return url
    if platform == "tiktok":
        author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
        author_name = text(author.get("name") or item.get("author") or item.get("username")).strip("@")
        video_id = text(item.get("id") or item.get("videoId"))
        if author_name and video_id:
            return f"https://www.tiktok.com/@{author_name}/video/{video_id}"
    return ""


def map_item(kol: KolTarget, item: dict[str, Any], keywords: list[str], run_id: str) -> SocialCandidate | None:
    if kol.platform == "instagram":
        title = text(item.get("caption") or item.get("text") or item.get("title"))[:500]
        description = text(item.get("caption") or item.get("text") or "")
        publish_date, scrape_error = normalize_date(item.get("timestamp") or item.get("takenAtTimestamp"))
        view_count = as_int(item.get("videoViewCount") or item.get("videoPlayCount") or item.get("viewsCount"))
        like_count = as_int(item.get("likesCount") or item.get("likes"))
        comment_count = as_int(item.get("commentsCount") or item.get("comments"))
        duration = parse_duration(item.get("videoDuration") or item.get("duration"))
        channel_name = text(item.get("ownerUsername") or item.get("username"))
        channel_id = text(item.get("ownerId") or item.get("ownerUsername"))
    elif kol.platform == "tiktok":
        title = text(item.get("text") or item.get("title") or item.get("description"))[:500]
        description = text(item.get("text") or item.get("description") or "")
        publish_date, scrape_error = normalize_date(item.get("createTimeISO") or item.get("createTime") or item.get("timestamp"))
        view_count = as_int(item.get("playCount") or item.get("views"))
        like_count = as_int(item.get("diggCount") or item.get("likes"))
        comment_count = as_int(item.get("commentCount") or item.get("comments"))
        meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
        duration = parse_duration(meta.get("duration") or item.get("duration"))
        author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
        channel_name = text(author.get("name") or item.get("author") or item.get("username"))
        channel_id = text(author.get("id") or author.get("name") or item.get("authorId"))
    elif kol.platform == "facebook":
        title = text(item.get("text") or item.get("title") or item.get("message"))[:500]
        description = text(item.get("text") or item.get("message") or "")
        publish_date, scrape_error = normalize_date(item.get("time") or item.get("timestamp") or item.get("date"))
        view_count = as_int(item.get("viewsCount") or item.get("viewCount"))
        like_count = as_int(item.get("likes") or item.get("likesCount"))
        comment_count = as_int(item.get("comments") or item.get("commentsCount"))
        duration = parse_duration(item.get("duration") or item.get("videoDuration"))
        channel_name = text(item.get("pageName") or item.get("profileName") or item.get("userName"))
        channel_id = text(item.get("pageId") or item.get("profileId"))
    else:
        return None

    hits, hashtag_hit = match_keywords(title, description, keywords)
    if not hits:
        return None
    url = candidate_url(kol.platform, item)
    if not url:
        return None
    return SocialCandidate(
        kol_id=kol.id,
        kol_name=kol.display_name or kol.handle,
        platform=kol.platform,
        video_url=url,
        title=title,
        description=description,
        view_count=view_count,
        like_count=like_count,
        comment_count=comment_count,
        publish_date=publish_date,
        duration_seconds=duration,
        thumbnail_url=first_image(item),
        channel_id=channel_id,
        channel_name=channel_name,
        scrape_error=scrape_error,
        matched_keywords=hits[:20],
        confidence="high" if len(hits) >= 2 else "medium",
        hashtag_hit=hashtag_hit,
        actor_run_id=run_id,
    )


def scrape_kol(kol: KolTarget, keywords: list[str], max_per_channel: int) -> SocialRun:
    actor_id = actor_id_for(kol.platform)
    run_input, cleaned_url = run_input_for(kol, max_per_channel)
    if not actor_id:
        return SocialRun(kol, actor_id, "error", cleaned_url, error=f"unsupported platform: {kol.platform}")
    token = os.environ.get("APIFY_API_TOKEN") or os.environ.get("APIFY_TOKEN") or ""
    if not token.strip():
        return SocialRun(kol, actor_id, "error", cleaned_url, error="APIFY token not configured")
    try:
        from apify_client import ApifyClient

        out(f"[KOL {kol.id}] {kol.platform} actor={actor_id} url={cleaned_url}", flush=True)
        client = ApifyClient(token)
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=900, wait_secs=900)
        run_id = text(run.get("id"))
        dataset_id = run.get("defaultDatasetId")
        items = client.dataset(dataset_id).list_items().items if dataset_id else []
        matches = [candidate for item in items if (candidate := map_item(kol, item, keywords, run_id))]
        status = "success" if matches else "no_match"
        sample_keys = [sorted(str(key) for key in item.keys())[:30] for item in items[:3]]
        out(
            f"[KOL {kol.id}] run_id={run_id} scanned={len(items)} matched={len(matches)} status={status}",
            flush=True,
        )
        for candidate in matches[:3]:
            out(f"[KOL {kol.id}] match: {candidate.title[:80]} | {candidate.video_url}", flush=True)
        usage = run.get("usageTotalUsd")
        return SocialRun(
            kol=kol,
            actor_id=actor_id,
            status=status,
            cleaned_url=cleaned_url,
            scanned_count=len(items),
            matches=matches,
            run_id=run_id,
            usage_usd=float(usage) if usage is not None else None,
            sample_keys=sample_keys,
        )
    except Exception as exc:
        out(f"[KOL {kol.id}] error: {str(exc)[:240]}", flush=True)
        return SocialRun(kol, actor_id, "error", cleaned_url, error=str(exc)[:500])


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content_url FROM vkpi_kol_video_evidence WHERE content_url = ANY(%s)", (urls,))
            return {row[0] for row in cur.fetchall()}


def write_csv(path: Path, candidates: list[SocialCandidate]) -> None:
    fields = [
        "kol_id", "kol_name", "platform", "video_url", "title", "view_count",
        "like_count", "comment_count", "publish_date", "duration_seconds",
        "channel_id", "channel_name", "scrape_source", "scrape_status",
        "matched_keywords", "confidence", "hashtag_hit", "actor_run_id",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in candidates:
            writer.writerow(
                {
                    "kol_id": item.kol_id,
                    "kol_name": item.kol_name,
                    "platform": item.platform,
                    "video_url": item.video_url,
                    "title": item.title,
                    "view_count": item.view_count,
                    "like_count": item.like_count,
                    "comment_count": item.comment_count,
                    "publish_date": item.publish_date,
                    "duration_seconds": item.duration_seconds,
                    "channel_id": item.channel_id,
                    "channel_name": item.channel_name,
                    "scrape_source": item.scrape_source,
                    "scrape_status": item.scrape_status,
                    "matched_keywords": ", ".join(item.matched_keywords[:5]),
                    "confidence": item.confidence,
                    "hashtag_hit": item.hashtag_hit,
                    "actor_run_id": item.actor_run_id,
                }
            )


def pct(filled: int, total: int) -> str:
    if total <= 0:
        return "0/0"
    return f"{filled}/{total} ({filled / total:.1%})"


def completeness_by_platform(candidates: list[SocialCandidate]) -> list[str]:
    lines: list[str] = []
    by_platform: dict[str, list[SocialCandidate]] = defaultdict(list)
    for item in candidates:
        by_platform[item.platform].append(item)
    for platform in sorted(by_platform):
        scoped = by_platform[platform]
        lines.append(f"- {platform}")
        for field_name in (
            "view_count", "like_count", "comment_count", "publish_date",
            "duration_seconds", "thumbnail_url", "channel_id", "channel_name",
        ):
            filled = sum(1 for item in scoped if getattr(item, field_name))
            lines.append(f"  - {field_name}: {pct(filled, len(scoped))}")
    if not lines:
        lines.append("- 无 matched evidence")
    return lines


def report_md(
    runs: list[SocialRun],
    candidates: list[SocialCandidate],
    keyword_count: int,
    predicted_insert: int,
    duplicate_skip: int,
) -> str:
    platform_runs = Counter(run.kol.platform for run in runs)
    platform_matches = Counter(item.platform for item in candidates)
    status_counts = Counter(run.status for run in runs)
    keyword_hits = Counter()
    for item in candidates:
        keyword_hits.update(item.matched_keywords)
    usage_known = sum(run.usage_usd or 0 for run in runs)
    usage_unknown_count = sum(1 for run in runs if run.run_id and run.usage_usd is None)
    estimate = usage_known + usage_unknown_count * 0.02

    lines = [
        "# Step 4b Social Evidence Dry-run Report",
        "",
        "## 总览",
        f"- 关键词总数: {keyword_count}",
        f"- KOL 数: {len(runs)}",
        f"- 找到 Viltrox 内容: {len(candidates)}",
        f"- 新增 evidence 预测: {predicted_insert}",
        f"- ON CONFLICT 预计跳过重复 content_url: {duplicate_skip}",
        f"- Apify 调用次数: {sum(1 for run in runs if run.run_id)}",
        f"- Apify cost 估算: ~${estimate:.2f}",
        "",
        "## 按平台统计",
    ]
    for platform in ("instagram", "tiktok", "facebook"):
        lines.append(
            f"- {platform}: KOL={platform_runs.get(platform, 0)}, "
            f"matched={platform_matches.get(platform, 0)}"
        )

    lines.extend(["", "## 每个 KOL 处理流程"])
    for run in runs:
        lines.append(
            f"- `{run.kol.id}` {run.kol.display_name} | platform={run.kol.platform} | "
            f"status={run.status} | scanned={run.scanned_count} | matched={len(run.matches)} | "
            f"actor={run.actor_id}"
        )
        lines.append(f"  - cleaned_url: {run.cleaned_url}")
        if run.run_id:
            lines.append(f"  - Apify run id: `{run.run_id}`")
        if run.error:
            lines.append(f"  - error: {run.error[:240]}")
        for candidate in run.matches[:3]:
            lines.append(f"  - {candidate.title[:50]}")

    lines.extend(["", "## scrape_status 分布"])
    for status, count in sorted(status_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## 字段完整度"])
    lines.extend(completeness_by_platform(candidates))

    lines.extend(["", "## Apify Run IDs"])
    for run in runs:
        if run.run_id:
            usd = f"${run.usage_usd:.4f}" if run.usage_usd is not None else "unknown"
            lines.append(f"- `{run.kol.id}` {run.kol.display_name}: `{run.run_id}` | usage={usd}")
    if not any(run.run_id for run in runs):
        lines.append("- 无成功启动的 actor run")

    hashtag_count = sum(1 for item in candidates if item.hashtag_hit)
    lines.extend(
        [
            "",
            "## Hashtag 命中",
            f"- hashtag 命中 evidence: {hashtag_count}/{len(candidates)}",
            "",
            "## 关键词命中 Top 15",
        ]
    )
    for keyword, count in keyword_hits.most_common(15):
        lines.append(f"- {keyword}: {count}")
    if not keyword_hits:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 字段映射问题",
            "- Instagram: 图文 post 通常没有 `view_count` / `duration_seconds`，这是 actor 数据形态限制；视频/Reels 才可能返回播放或时长。",
            "- TikTok: `videoMeta.duration` / `covers` 依赖 actor 返回；若 matched=0 则无法判断字段完整度。",
            "- Facebook: `view_count` / `duration_seconds` / `thumbnail_url` 字段不稳定，actor 可能不返回。",
            "",
            "## Dataset Sample Keys",
        ]
    )
    for run in runs:
        if not run.sample_keys:
            continue
        lines.append(f"- `{run.kol.id}` {run.kol.display_name}")
        for idx, keys in enumerate(run.sample_keys, start=1):
            lines.append(f"  - sample {idx}: {', '.join(keys)}")
    return "\n".join(lines) + "\n"


def db_non_negative(value: int | None) -> int | None:
    if value is None:
        return None
    return value if value >= 0 else None


def db_row(candidate: SocialCandidate) -> dict[str, Any]:
    row = candidate.__dict__.copy()
    for field_name in ("like_count", "view_count", "comment_count", "duration_seconds"):
        row[field_name] = db_non_negative(row.get(field_name))
    return row


def insert_candidates(candidates: list[SocialCandidate]) -> int:
    sql = """
        INSERT INTO vkpi_kol_video_evidence (
          kol_pool_id, content_url, evidence_type, source, source_ref,
          platform, confidence, title, view_count, like_count, comment_count,
          publish_date, duration_seconds, thumbnail_url, channel_id,
          channel_name, scrape_status, scraped_at, scrape_source
        )
        VALUES (
          %(kol_id)s, %(video_url)s, 'video', 'apify', 'step4b_social',
          %(platform)s, %(confidence)s, %(title)s, %(view_count)s, %(like_count)s, %(comment_count)s,
          %(publish_date)s, %(duration_seconds)s, %(thumbnail_url)s, %(channel_id)s,
          %(channel_name)s, %(scrape_status)s, NOW(), 'apify'
        )
        ON CONFLICT (content_url) DO NOTHING
    """
    rows = [db_row(item) for item in candidates]
    with connect_db() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)
            return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 4b Instagram/TikTok/Facebook Apify evidence scraper.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--commit", action="store_true", default=False)
    parser.add_argument("--kol-ids", default="")
    parser.add_argument("--max-per-channel", type=int, default=50)
    args = parser.parse_args()
    if not args.commit:
        args.dry_run = True

    load_env()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    keywords = load_keywords()
    targets = load_targets(parse_ids(args.kol_ids))

    runs: list[SocialRun] = []
    for kol in targets:
        runs.append(scrape_kol(kol, keywords, args.max_per_channel))
        time.sleep(0.5)
    candidates = [candidate for run in runs for candidate in run.matches]
    existing = existing_urls([item.video_url for item in candidates])
    insertable = [item for item in candidates if item.video_url not in existing]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = ARTIFACT_DIR / f"step4b_dryrun_{stamp}.csv"
    report_path = ARTIFACT_DIR / f"step4b_report_{stamp}.md"
    write_csv(csv_path, candidates)
    report = report_md(runs, candidates, len(keywords), len(insertable), len(candidates) - len(insertable))
    report_path.write_text(report, encoding="utf-8")
    out(f"CSV: {csv_path}")
    out(f"Markdown: {report_path}")
    out(report)

    if args.commit:
        inserted = insert_candidates(insertable)
        out(f"Inserted rows: {inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
