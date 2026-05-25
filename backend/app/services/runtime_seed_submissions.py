"""
Runtime preview submission seed builders.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.runtime_seed_data import PREVIEW_VIDEO_URL, RUNTIME_SEED_MEMO_PREFIX


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_points(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base = int(total // count)
    remainder = int(total % count)
    return [base + (1 if idx < remainder else 0) for idx in range(count)]


def _month_seed_timestamp(now: datetime, index: int) -> str:
    day = max(1, min(28, now.day - (index * 2)))
    if day <= 0:
        day = (index % 20) + 1
    stamp = datetime(now.year, now.month, day, 10 + (index % 8), 15, tzinfo=timezone.utc)
    return _iso(stamp)


def _historic_seed_timestamp(now: datetime, index: int) -> str:
    if now.month > 1:
        month_slot = (index % (now.month - 1)) + 1
    else:
        month_slot = now.month
    if month_slot == now.month:
        month_slot = max(1, now.month - 1)
    day = min(28, 3 + ((index * 3) % 24))
    stamp = datetime(now.year, month_slot, day, 9 + (index % 7), 30, tzinfo=timezone.utc)
    return _iso(stamp)


def _public_post_url(platform: str, handle: str, slug: str) -> str:
    safe_platform = str(platform or "").strip().lower()
    safe_handle = str(handle or "").strip().lstrip("@")
    safe_slug = str(slug or "").strip().replace(" ", "-").lower()
    if safe_platform == "youtube":
        return f"https://www.youtube.com/@{safe_handle}/shorts/{safe_slug}"
    if safe_platform == "instagram":
        return f"https://www.instagram.com/{safe_handle}/reel/{safe_slug}"
    if safe_platform == "tiktok":
        return f"https://www.tiktok.com/@{safe_handle}/video/{safe_slug}"
    return ""


def _submission_payload(*, gear_tag: str, quality_summary: str, clean: int, speed: int, quality: int) -> str:
    return json.dumps(
        {
            "gear_combo": gear_tag,
            "quality_summary": quality_summary,
            "cleanliness": clean,
            "speed": speed,
            "quality": quality,
        },
        ensure_ascii=False,
    )


def seed_leaderboard_submission_rows(conn: Any, user_id: int, seed: dict[str, object], now: datetime) -> int:
    month_count = int(seed.get("month_count") or 0)
    year_count = int(seed.get("year_count") or 0)
    month_points = int(seed.get("month_points") or 0)
    year_points = int(seed.get("year_points") or 0)
    extra_count = max(0, year_count - month_count)
    handle = str(seed.get("handle") or "").strip().lower()
    platform = str(seed.get("platform") or "tiktok").strip().lower()
    gear_tag = str(seed.get("gear_tag") or "Viltrox creator kit").strip()
    product_series = str(seed.get("product_series") or "AF").strip()
    name = str(seed.get("name") or "Creator").strip()
    memo = f"{RUNTIME_SEED_MEMO_PREFIX}leaderboard:{seed.get('creator_code')}"
    points_chunks = _split_points(month_points, month_count) + _split_points(max(0, year_points - month_points), extra_count)
    created_at_rows = [
        *[_month_seed_timestamp(now, idx) for idx in range(month_count)],
        *[_historic_seed_timestamp(now, idx) for idx in range(extra_count)],
    ]
    inserted = 0
    for idx, created_at in enumerate(created_at_rows):
        points_awarded = points_chunks[idx] if idx < len(points_chunks) else 0
        score = 340 - (idx % 7) * 9
        slug = f"{name.lower()}-{idx + 1:02d}"
        conn.execute(
            """
            INSERT INTO submissions (
                created_at, platform, url, extracted_handle, title, detection_status,
                product_series, product_label, content_types, final_score, creator_score,
                overall_score, risk_score, views, likes, comments, shares, favorites,
                recommendation, memo, scraped_ok, video_analysis, video_path, user_id,
                points_awarded, points_status, job_status, raw_text, caption
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                created_at,
                platform,
                _public_post_url(platform, handle, slug),
                handle,
                f"{gear_tag} field test #{idx + 1:02d}",
                "confirmed",
                product_series,
                gear_tag,
                "review,gear",
                score,
                max(0, score - 12),
                score,
                4 + (idx % 5),
                18000 + idx * 1200,
                1200 + idx * 70,
                80 + idx * 5,
                35 + idx * 2,
                18 + idx,
                f"{name} kept the edit tight and product-first.",
                memo,
                1,
                _submission_payload(
                    gear_tag=gear_tag,
                    quality_summary=f"{gear_tag} held detail and contrast cleanly through the edit.",
                    clean=84 - (idx % 6),
                    speed=78 - (idx % 5),
                    quality=86 - (idx % 4),
                ),
                PREVIEW_VIDEO_URL,
                int(user_id),
                int(points_awarded),
                "confirmed",
                "done",
                f"{name} runtime seed submission {idx + 1}",
                f"{gear_tag} runtime seed cut #{idx + 1}",
            ),
        )
        inserted += 1
    return inserted


def seed_preview_submission_rows(conn: Any, user_id: int, now: datetime) -> int:
    confirmed_month_points = _split_points(7840, 4)
    confirmed_year_points = _split_points(20000, 14)
    pending_states = ("queued", "running", "suspected", "rejected", "queued", "running")
    confirmed_titles = (
        "AF 56mm downtown run-and-gun",
        "LUNA 30-300 shoulder rig walk-through",
        "EPIC 35mm studio contrast test",
        "AF 28mm street portrait teaser",
    )
    historic_titles = (
        "AF 20mm sunrise travel cut",
        "EPIC anamorphic lighting notes",
        "AF 85mm interview setup",
        "Kit zoom documentary b-roll",
        "AF 135mm compression reel",
        "LUNA zoom handheld balance check",
        "Product hero loop for Viltrox booth",
        "Low light focus pull challenge",
        "One-light portrait breakdown",
        "Creator bag loadout short",
        "Commute montage with AF 40mm",
        "Run club recap with 16mm",
        "Compact cine rig behind the scenes",
        "Weekend travel reel color pass",
    )
    inserted = 0
    memo = f"{RUNTIME_SEED_MEMO_PREFIX}preview_submission"
    month_gears = ("AF 56mm F1.7 Air", "LUNA 30-300", "EPIC 35mm", "AF 28mm F4.5")
    for idx, title in enumerate(confirmed_titles):
        points_awarded = confirmed_month_points[idx]
        gear = month_gears[idx]
        platform = "tiktok"
        handle = "alex.creates"
        score = 320 - idx * 12
        conn.execute(
            """
            INSERT INTO submissions (
                created_at, platform, url, extracted_handle, title, detection_status,
                product_series, product_label, content_types, final_score, creator_score,
                overall_score, risk_score, views, likes, comments, shares, favorites,
                recommendation, memo, scraped_ok, video_analysis, video_path, user_id,
                points_awarded, points_status, job_status, raw_text, caption
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _month_seed_timestamp(now, idx),
                platform,
                _public_post_url(platform, handle, f"preview-{idx + 1:02d}"),
                handle,
                title,
                "confirmed",
                "AF",
                gear,
                "review,creator",
                score,
                max(0, score - 8),
                score,
                3 + idx,
                8200 + idx * 1300,
                640 + idx * 70,
                48 + idx * 6,
                17 + idx * 3,
                12 + idx * 2,
                "Strong pacing and clear lens call-outs kept the review useful.",
                memo,
                1,
                _submission_payload(
                    gear_tag=gear,
                    quality_summary=f"{gear} held up well in the latest creator test.",
                    clean=88 - idx,
                    speed=82 - idx,
                    quality=86 - idx,
                ),
                PREVIEW_VIDEO_URL,
                int(user_id),
                int(points_awarded),
                "confirmed",
                "done",
                f"Preview creator submission {idx + 1}",
                title,
            ),
        )
        inserted += 1
    for idx, title in enumerate(historic_titles):
        points_awarded = confirmed_year_points[idx]
        gear = (
            "AF 20mm F2.8",
            "EPIC 35mm",
            "AF 85mm F1.8",
            "LUNA 18-50",
            "AF 135mm F1.8 LAB",
            "LUNA 30-300",
            "AF 56mm F1.7 Air",
        )[idx % 7]
        platform = "tiktok"
        handle = "alex.creates"
        score = 298 - (idx % 6) * 9
        conn.execute(
            """
            INSERT INTO submissions (
                created_at, platform, url, extracted_handle, title, detection_status,
                product_series, product_label, content_types, final_score, creator_score,
                overall_score, risk_score, views, likes, comments, shares, favorites,
                recommendation, memo, scraped_ok, video_analysis, video_path, user_id,
                points_awarded, points_status, job_status, raw_text, caption
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _historic_seed_timestamp(now, idx),
                platform,
                _public_post_url(platform, handle, f"archive-{idx + 1:02d}"),
                handle,
                title,
                "confirmed",
                "AF",
                gear,
                "gear,story",
                score,
                max(0, score - 10),
                score,
                5 + (idx % 4),
                6100 + idx * 540,
                430 + idx * 40,
                26 + idx * 3,
                11 + idx,
                7 + idx,
                "Solid creator pacing with clearer product framing than the last cut.",
                memo,
                1,
                _submission_payload(
                    gear_tag=gear,
                    quality_summary=f"{gear} archive cut with cleaner narrative and steadier framing.",
                    clean=80 - (idx % 5),
                    speed=76 - (idx % 4),
                    quality=79 - (idx % 3),
                ),
                PREVIEW_VIDEO_URL,
                int(user_id),
                int(points_awarded),
                "confirmed",
                "done",
                f"Preview creator archive submission {idx + 1}",
                title,
            ),
        )
        inserted += 1
    for idx, status in enumerate(pending_states):
        title = (
            "Campus lab teaser awaiting review",
            "New lens unboxing still processing",
            "Mini doc under moderation",
            "Rejected duplicate storefront cut",
            "Sports reel queued for scoring",
            "Night drive montage still rendering",
        )[idx]
        conn.execute(
            """
            INSERT INTO submissions (
                created_at, platform, url, extracted_handle, title, detection_status,
                product_series, product_label, content_types, final_score, creator_score,
                overall_score, risk_score, views, likes, comments, shares, favorites,
                recommendation, memo, scraped_ok, video_analysis, video_path, user_id,
                points_awarded, points_status, job_status, raw_text, caption
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _historic_seed_timestamp(now, 30 + idx),
                ("youtube", "instagram", "tiktok")[idx % 3],
                "",
                "",
                title,
                status,
                "AF",
                "AF 56mm F1.7 Air",
                "creator",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                "Waiting on automated review.",
                memo,
                0,
                _submission_payload(
                    gear_tag="AF 56mm F1.7 Air",
                    quality_summary="Pending review.",
                    clean=0,
                    speed=0,
                    quality=0,
                ),
                PREVIEW_VIDEO_URL,
                int(user_id),
                0,
                "pending",
                "queued" if status in {"queued", "running"} else "review",
                f"Preview creator pending submission {idx + 1}",
                title,
            ),
        )
        inserted += 1
    return inserted
