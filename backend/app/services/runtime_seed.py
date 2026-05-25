"""
services/runtime_seed.py — local runtime seed data for preview surfaces
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.config import ENABLE_RUNTIME_PREVIEW_DATA, IS_PRODUCTION
from app.core.security import hash_password
from app.db.connection import get_conn
from app.services.runtime_seed_data import (
    LEADERBOARD_CREATOR_SEEDS,
    PREVIEW_ADDRESS_ROWS,
    PREVIEW_SEED_EMAILS,
    PREVIEW_SOCIAL_ROWS,
    PREVIEW_VIDEO_URL,
    REWARD_SEEDS,
    RUNTIME_SEED_MEMO_PREFIX,
)
from app.services.student_identity import ensure_student_identity_registry_defaults


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_remote_url(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith("http://") or text.startswith("https://")


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


def _upsert_seed_user(
    conn,
    *,
    email: str,
    name: str,
    creator_code: str,
    points_balance: int,
    points_pending: int,
    points_total: int,
    trust_score: int,
    bio: str = "",
    note: str = "",
) -> int:
    now = _utcnow()
    password_hash = hash_password("Preview888")
    existing = conn.execute(
        "SELECT id FROM users WHERE email=? LIMIT 1",
        (email,),
    ).fetchone()
    params = (
        password_hash,
        name,
        creator_code,
        int(points_balance),
        int(points_pending),
        int(points_total),
        float(trust_score),
        now,
        bio,
        note,
    )
    if existing:
        conn.execute(
            """
            UPDATE users
            SET password_hash=?, name=?, creator_code=?, status='approved', role='creator',
                email_verified=1, points_balance=?, points_pending=?, points_total=?,
                tier_status='active', trust_score=?, trust_updated_at=?, bio=?, note=?
            WHERE id=?
            """,
            (*params, int(existing["id"])),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO users (
            created_at, email, password_hash, name, creator_code, status, role,
            email_verified, points_balance, points_pending, points_total, tier_status,
            trust_score, trust_updated_at, bio, note
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            now,
            email,
            password_hash,
            name,
            creator_code,
            "approved",
            "creator",
            1,
            int(points_balance),
            int(points_pending),
            int(points_total),
            "active",
            float(trust_score),
            now,
            bio,
            note,
        ),
    )
    return int(cur.lastrowid or 0)


def ensure_reward_catalog_seed() -> int:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM reward_catalog
        WHERE status='published'
        """
    ).fetchone()
    if int((row["total"] if row else 0) or 0) > 0:
        return 0

    now = _utcnow()
    for item in REWARD_SEEDS:
        conn.execute(
            """
            INSERT INTO reward_catalog (
                created_at, updated_at, title, description, category, points_cost,
                meta_label, image_url, stock, sort_order, status, published_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                now,
                str(item.get("title") or "").strip(),
                str(item.get("description") or "").strip(),
                str(item.get("category") or "Merch").strip(),
                int(item.get("points_cost") or 0),
                str(item.get("meta_label") or "").strip(),
                str(item.get("image_url") or "").strip(),
                int(item.get("stock") or 0),
                int(item.get("sort_order") or 100),
                "published",
                now,
            ),
        )
    conn.commit()
    return len(REWARD_SEEDS)


def purge_reward_catalog_seed() -> int:
    conn = get_conn()
    clauses: list[str] = []
    params: list[object] = []
    for item in REWARD_SEEDS:
        clauses.append("(title=? AND category=? AND points_cost=?)")
        params.extend(
            (
                str(item.get("title") or "").strip(),
                str(item.get("category") or "").strip(),
                int(item.get("points_cost") or 0),
            )
        )
    if not clauses:
        return 0
    cur = conn.execute(
        f"DELETE FROM reward_catalog WHERE {' OR '.join(clauses)}",
        tuple(params),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def ensure_local_preview_creator() -> int:
    if IS_PRODUCTION:
        return 0
    conn = get_conn()
    user_id = _upsert_seed_user(
        conn,
        email="preview@viltrox.local",
        name="Jianbo Zhang",
        creator_code="V_009021",
        points_balance=2450,
        points_pending=380,
        points_total=7840,
        trust_score=78,
        bio="Director and cinematographer pressure-testing the Viltrox creator workflow.",
        note="runtime_seed:preview_creator",
    )
    conn.commit()
    return user_id


def _seed_leaderboard_submission_rows(conn, user_id: int, seed: dict[str, object], now: datetime) -> int:
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


def _seed_preview_submission_rows(conn, user_id: int, now: datetime) -> int:
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


def _seed_preview_account_tables(conn, preview_user_id: int) -> dict[str, int]:
    now = _utcnow()
    conn.execute("DELETE FROM user_social_accounts WHERE user_id=?", (int(preview_user_id),))
    for row in PREVIEW_SOCIAL_ROWS:
        verified = int(row.get("verified") or 0)
        conn.execute(
            """
            INSERT INTO user_social_accounts (
                user_id, platform, handle, verified, verified_at, verify_code, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                int(preview_user_id),
                str(row.get("platform") or "").strip(),
                str(row.get("handle") or "").strip(),
                verified,
                now if verified else None,
                str(row.get("verify_code") or "").strip(),
                now,
            ),
        )
    conn.execute("DELETE FROM user_addresses WHERE user_id=?", (int(preview_user_id),))
    for row in PREVIEW_ADDRESS_ROWS:
        conn.execute(
            """
            INSERT INTO user_addresses (
                user_id, name, phone, address1, address2, city, state, country, postal_code, is_default
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(preview_user_id),
                str(row.get("name") or "").strip(),
                str(row.get("phone") or "").strip(),
                str(row.get("address1") or "").strip(),
                str(row.get("address2") or "").strip(),
                str(row.get("city") or "").strip(),
                str(row.get("state") or "").strip(),
                str(row.get("country") or "US").strip(),
                str(row.get("postal_code") or "").strip(),
                int(row.get("is_default") or 0),
            ),
        )
    conn.execute(
        "DELETE FROM submissions WHERE user_id=? AND memo LIKE ?",
        (int(preview_user_id), f"{RUNTIME_SEED_MEMO_PREFIX}%"),
    )
    seeded_submissions = _seed_preview_submission_rows(conn, preview_user_id, datetime.now(timezone.utc))
    conn.execute(
        "DELETE FROM points_log WHERE user_id=? AND reason LIKE ?",
        (int(preview_user_id), "Runtime seed:%"),
    )
    point_rows = (
        ("Runtime seed: creator balance top-up", 4000, 4000),
        ("Runtime seed: approved shortlist bonus", 2600, 6600),
        ("Runtime seed: redeemed Viltrox Cap", -800, 5800),
        ("Runtime seed: recent shipping hold", -3350, 2450),
    )
    for reason, delta, balance_after in point_rows:
        conn.execute(
            "INSERT INTO points_log (created_at, user_id, submission_id, delta, reason, balance_after) VALUES (?,?,?,?,?,?)",
            (_utcnow(), int(preview_user_id), None, int(delta), reason, int(balance_after)),
        )
    address = conn.execute(
        "SELECT * FROM user_addresses WHERE user_id=? ORDER BY is_default DESC, id DESC LIMIT 1",
        (int(preview_user_id),),
    ).fetchone()
    address_id = int(address["id"]) if address else None
    address_snapshot = json.dumps(dict(address), ensure_ascii=False) if address else "{}"
    reward_rows = conn.execute(
        "SELECT id, title, category, points_cost FROM reward_catalog WHERE status='published' ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    reward_map = {str(row["title"]): dict(row) for row in reward_rows}
    conn.execute(
        "DELETE FROM redemptions WHERE user_id=? AND admin_note=?",
        (int(preview_user_id), "runtime_seed"),
    )
    preview_orders = (
        ("Viltrox Cap", "shipped", "VLX-TRACK-0001"),
        ("Lens Cleaning Kit", "pending", ""),
        ("AF 56mm F1.4 C Lens Coupon", "approved", ""),
    )
    seeded_redemptions = 0
    for title, status, tracking in preview_orders:
        reward = reward_map.get(title)
        if not reward:
            continue
        conn.execute(
            """
            INSERT INTO redemptions (
                created_at, user_id, reward_id, item_name, item_category, points_cost,
                address_id, address_snapshot, status, tracking_number, admin_note
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _utcnow(),
                int(preview_user_id),
                int(reward["id"]),
                str(reward["title"]),
                str(reward["category"] or ""),
                int(reward["points_cost"] or 0),
                address_id,
                address_snapshot,
                status,
                tracking,
                "runtime_seed",
            ),
        )
        seeded_redemptions += 1
    return {
        "social_accounts": len(PREVIEW_SOCIAL_ROWS),
        "addresses": len(PREVIEW_ADDRESS_ROWS),
        "submissions": seeded_submissions,
        "redemptions": seeded_redemptions,
    }


def ensure_preview_experience_seed(preview_user_id: int) -> dict[str, int]:
    if IS_PRODUCTION or int(preview_user_id or 0) <= 0:
        return {"leaderboard_rows": 0, "social_accounts": 0, "addresses": 0, "submissions": 0, "redemptions": 0}
    conn = get_conn()
    now = datetime.now(timezone.utc)
    creator_ids: dict[str, int] = {}
    for seed in LEADERBOARD_CREATOR_SEEDS:
        creator_ids[str(seed.get("creator_code") or "")] = _upsert_seed_user(
            conn,
            email=str(seed.get("email") or "").strip(),
            name=str(seed.get("name") or "Creator").strip(),
            creator_code=str(seed.get("creator_code") or "").strip(),
            points_balance=int(seed.get("points_balance") or 0),
            points_pending=int(seed.get("points_pending") or 0),
            points_total=int(seed.get("points_total") or 0),
            trust_score=int(seed.get("trust_score") or 70),
            bio=str(seed.get("bio") or "").strip(),
            note="runtime_seed:leaderboard_creator",
        )
    leaderboard_creator_ids = tuple(int(value) for value in creator_ids.values() if int(value or 0) > 0)
    if leaderboard_creator_ids:
        placeholders = ",".join("?" for _ in leaderboard_creator_ids)
        conn.execute(
            f"DELETE FROM submissions WHERE user_id IN ({placeholders}) AND memo LIKE ?",
            (*leaderboard_creator_ids, f"{RUNTIME_SEED_MEMO_PREFIX}%"),
        )
    leaderboard_rows = 0
    for seed in LEADERBOARD_CREATOR_SEEDS:
        user_id = creator_ids.get(str(seed.get("creator_code") or ""))
        if user_id:
            leaderboard_rows += _seed_leaderboard_submission_rows(conn, int(user_id), seed, now)
    preview_bundle = _seed_preview_account_tables(conn, int(preview_user_id))
    conn.commit()
    return {"leaderboard_rows": leaderboard_rows, **preview_bundle}


def purge_runtime_preview_data() -> dict[str, int]:
    conn = get_conn()
    preview_rows = conn.execute(
        """
        SELECT id
        FROM users
        WHERE email IN ({placeholders})
           OR note LIKE ?
        """.format(placeholders=",".join("?" for _ in PREVIEW_SEED_EMAILS)),
        (*PREVIEW_SEED_EMAILS, f"{RUNTIME_SEED_MEMO_PREFIX}%"),
    ).fetchall()
    preview_user_ids = [int(row["id"]) for row in preview_rows]
    removed: dict[str, int] = {
        "users": 0,
        "social_accounts": 0,
        "addresses": 0,
        "submissions": 0,
        "redemptions": 0,
        "points_log": 0,
    }
    if preview_user_ids:
        placeholders = ",".join("?" for _ in preview_user_ids)
        for table, label in (
            ("user_social_accounts", "social_accounts"),
            ("user_addresses", "addresses"),
            ("submissions", "submissions"),
            ("redemptions", "redemptions"),
            ("points_log", "points_log"),
        ):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE user_id IN ({placeholders})",
                tuple(preview_user_ids),
            )
            removed[label] += int(cur.rowcount or 0)
        cur = conn.execute(
            f"DELETE FROM users WHERE id IN ({placeholders})",
            tuple(preview_user_ids),
        )
        removed["users"] += int(cur.rowcount or 0)
    cur = conn.execute(
        "DELETE FROM submissions WHERE memo LIKE ?",
        (f"{RUNTIME_SEED_MEMO_PREFIX}%",),
    )
    removed["submissions"] += int(cur.rowcount or 0)
    cur = conn.execute(
        "DELETE FROM redemptions WHERE admin_note=?",
        ("runtime_seed",),
    )
    removed["redemptions"] += int(cur.rowcount or 0)
    cur = conn.execute(
        "DELETE FROM points_log WHERE reason LIKE ?",
        ("Runtime seed:%",),
    )
    removed["points_log"] += int(cur.rowcount or 0)
    conn.commit()
    return removed


def ensure_runtime_seed_data() -> dict[str, int]:
    student_rows = ensure_student_identity_registry_defaults()
    if not ENABLE_RUNTIME_PREVIEW_DATA:
        removed_rewards = purge_reward_catalog_seed()
        removed = purge_runtime_preview_data()
        return {
            "student_id_registry": len(student_rows),
            "reward_catalog_seeded": 0,
            "reward_catalog_removed": removed_rewards,
            "preview_creator_id": 0,
            "preview_cleanup_users": int(removed.get("users") or 0),
            "preview_cleanup_submissions": int(removed.get("submissions") or 0),
            "preview_cleanup_redemptions": int(removed.get("redemptions") or 0),
        }
    seeded_rewards = ensure_reward_catalog_seed()
    preview_user_id = ensure_local_preview_creator()
    preview_bundle = ensure_preview_experience_seed(preview_user_id)
    return {
        "student_id_registry": len(student_rows),
        "reward_catalog_seeded": seeded_rewards,
        "preview_creator_id": preview_user_id,
        "leaderboard_seed_rows": int(preview_bundle.get("leaderboard_rows") or 0),
        "preview_social_rows": int(preview_bundle.get("social_accounts") or 0),
        "preview_address_rows": int(preview_bundle.get("addresses") or 0),
        "preview_submission_rows": int(preview_bundle.get("submissions") or 0),
        "preview_redemptions": int(preview_bundle.get("redemptions") or 0),
    }
