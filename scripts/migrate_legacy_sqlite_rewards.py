#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import psycopg

from stdout_utils import out_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = ROOT / "submissions.db"


TABLES_WITH_SEQUENCES = (
    "reward_catalog",
    "user_addresses",
    "user_social_accounts",
    "submissions",
    "redemptions",
    "points_log",
)


def sqlite_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(conn.execute(f"SELECT * FROM {table} ORDER BY id"))


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def normalize_json_text(value: Any, fallback: str = "{}") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return fallback
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def fetch_user_map(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for user in sqlite_rows(sqlite_conn, "users"):
        email = str(user["email"] or "").strip().lower()
        if not email:
            continue
        pg_cur.execute("SELECT id FROM users WHERE lower(email) = %s", (email,))
        found = pg_cur.fetchone()
        if found:
            pg_user_id = int(found[0])
            preferred_code = str(user["creator_code"] or "").strip()
            if preferred_code:
                pg_cur.execute("SELECT id FROM users WHERE creator_code = %s", (preferred_code,))
                code_owner = pg_cur.fetchone()
                if not code_owner or int(code_owner[0]) == pg_user_id:
                    pg_cur.execute(
                        "UPDATE users SET creator_code = %s WHERE id = %s AND COALESCE(creator_code, '') = ''",
                        (preferred_code, pg_user_id),
                    )
            mapping[int(user["id"])] = pg_user_id
            continue

        creator_code = user["creator_code"]
        if creator_code:
            pg_cur.execute("SELECT 1 FROM users WHERE creator_code = %s", (creator_code,))
            if pg_cur.fetchone():
                creator_code = None

        pg_cur.execute(
            """
            INSERT INTO users (
                created_at, email, password_hash, name, creator_code, status, role,
                points_balance, points_pending, points_total, last_login, note,
                email_verified, social_verified, avatar_url, bio, signature,
                tier_status, trust_score, trust_updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id
            """,
            (
                user["created_at"],
                email,
                user["password_hash"],
                user["name"],
                creator_code,
                user["status"],
                user["role"],
                user["points_balance"],
                user["points_pending"],
                user["points_total"],
                user["last_login"],
                user["note"],
                user["email_verified"],
                user["social_verified"],
                user["avatar_url"],
                user["bio"],
                user["signature"],
                user["tier_status"],
                user["trust_score"],
                user["trust_updated_at"],
            ),
        )
        mapping[int(user["id"])] = int(pg_cur.fetchone()[0])
    return mapping


def restore_user_points(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor, user_map: dict[int, int]) -> int:
    updated = 0
    for user in sqlite_rows(sqlite_conn, "users"):
        old_id = int(user["id"])
        new_id = user_map.get(old_id)
        if not new_id:
            continue
        pg_cur.execute(
            """
            UPDATE users
            SET points_balance = GREATEST(points_balance, %s),
                points_pending = GREATEST(points_pending, %s),
                points_total = GREATEST(points_total, %s),
                email_verified = GREATEST(email_verified, %s),
                social_verified = GREATEST(social_verified, %s),
                tier_status = CASE
                    WHEN tier_status = 'pending' AND %s <> 'pending' THEN %s
                    ELSE tier_status
                END,
                trust_score = GREATEST(trust_score, %s)
            WHERE id = %s
            """,
            (
                user["points_balance"],
                user["points_pending"],
                user["points_total"],
                user["email_verified"],
                user["social_verified"],
                user["tier_status"],
                user["tier_status"],
                user["trust_score"],
                new_id,
            ),
        )
        updated += pg_cur.rowcount
    return updated


def restore_rewards(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor, user_map: dict[int, int]) -> int:
    inserted = 0
    for reward in sqlite_rows(sqlite_conn, "reward_catalog"):
        published_by = user_map.get(int(reward["published_by"])) if reward["published_by"] else None
        pg_cur.execute(
            """
            INSERT INTO reward_catalog (
                id, created_at, updated_at, title, description, category, points_cost,
                meta_label, image_url, stock, sort_order, status, published_at, published_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET updated_at = EXCLUDED.updated_at,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                category = EXCLUDED.category,
                points_cost = EXCLUDED.points_cost,
                meta_label = EXCLUDED.meta_label,
                image_url = EXCLUDED.image_url,
                stock = EXCLUDED.stock,
                sort_order = EXCLUDED.sort_order,
                status = EXCLUDED.status,
                published_at = EXCLUDED.published_at,
                published_by = EXCLUDED.published_by
            """,
            (
                reward["id"],
                reward["created_at"],
                reward["updated_at"],
                reward["title"],
                reward["description"],
                reward["category"],
                reward["points_cost"],
                reward["meta_label"],
                reward["image_url"],
                reward["stock"],
                reward["sort_order"],
                reward["status"],
                reward["published_at"],
                published_by,
            ),
        )
        inserted += 1
    return inserted


def restore_addresses(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor, user_map: dict[int, int]) -> int:
    inserted = 0
    for address in sqlite_rows(sqlite_conn, "user_addresses"):
        new_user_id = user_map.get(int(address["user_id"]))
        if not new_user_id:
            continue
        pg_cur.execute(
            """
            INSERT INTO user_addresses (
                id, user_id, name, phone, address1, address2, city, state, country,
                postal_code, is_default
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                name = EXCLUDED.name,
                phone = EXCLUDED.phone,
                address1 = EXCLUDED.address1,
                address2 = EXCLUDED.address2,
                city = EXCLUDED.city,
                state = EXCLUDED.state,
                country = EXCLUDED.country,
                postal_code = EXCLUDED.postal_code,
                is_default = EXCLUDED.is_default
            """,
            (
                address["id"],
                new_user_id,
                address["name"],
                address["phone"],
                address["address1"],
                address["address2"],
                address["city"],
                address["state"],
                address["country"],
                address["postal_code"],
                address["is_default"],
            ),
        )
        inserted += 1
    return inserted


def restore_social_accounts(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor, user_map: dict[int, int]) -> int:
    inserted = 0
    for account in sqlite_rows(sqlite_conn, "user_social_accounts"):
        new_user_id = user_map.get(int(account["user_id"]))
        if not new_user_id:
            continue
        pg_cur.execute(
            """
            INSERT INTO user_social_accounts (
                id, user_id, platform, handle, verified, verified_at, verify_code, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (platform, handle) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                verified = GREATEST(user_social_accounts.verified, EXCLUDED.verified),
                verified_at = COALESCE(user_social_accounts.verified_at, EXCLUDED.verified_at),
                verify_code = COALESCE(NULLIF(user_social_accounts.verify_code, ''), EXCLUDED.verify_code)
            """,
            (
                account["id"],
                new_user_id,
                account["platform"],
                account["handle"],
                account["verified"],
                account["verified_at"],
                account["verify_code"],
                account["created_at"],
            ),
        )
        inserted += 1
    return inserted


def restore_submissions(sqlite_conn: sqlite3.Connection, pg_cur: psycopg.Cursor, user_map: dict[int, int]) -> dict[int, int]:
    id_map: dict[int, int] = {}
    for submission in sqlite_rows(sqlite_conn, "submissions"):
        new_user_id = user_map.get(int(submission["user_id"])) if submission["user_id"] else None
        pg_cur.execute(
            """
            SELECT id FROM submissions
            WHERE COALESCE(user_id, 0) = COALESCE(%s, 0)
              AND COALESCE(created_at::text, '') = COALESCE(%s, '')
              AND COALESCE(platform, '') = COALESCE(%s, '')
              AND COALESCE(url, '') = COALESCE(%s, '')
              AND COALESCE(title, '') = COALESCE(%s, '')
            LIMIT 1
            """,
            (new_user_id, submission["created_at"], submission["platform"], submission["url"], submission["title"]),
        )
        found = pg_cur.fetchone()
        if found:
            id_map[int(submission["id"])] = int(found[0])
            continue

        pg_cur.execute(
            """
            INSERT INTO submissions (
                created_at, user_id, platform, url, extracted_handle, title, caption,
                raw_text, detection_status, job_status, product_series, product_label,
                content_types, final_score, creator_score, overall_score, risk_score,
                views, likes, comments, shares, favorites, recommendation, memo,
                evidence, scraped_ok, video_analysis, video_path, tech_score,
                marketing_score, content_genre, percentile_tech, percentile_mkt,
                vertical_category, vertical_tech_score, vertical_mkt_score,
                community_value, product_showcase_score, brand_exposure_score,
                storytelling_score, tech_status, logo_detected, product_closeup_count,
                points_awarded, points_pending, points_status, error_message,
                started_at, finished_at, confirm_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id
            """,
            (
                submission["created_at"],
                new_user_id,
                submission["platform"],
                submission["url"],
                submission["extracted_handle"],
                submission["title"],
                submission["caption"],
                submission["raw_text"],
                submission["detection_status"],
                submission["job_status"],
                submission["product_series"],
                submission["product_label"],
                submission["content_types"] or "[]",
                submission["final_score"] or 0,
                submission["creator_score"] or 0,
                submission["overall_score"] or 0,
                submission["risk_score"] or 0,
                submission["views"] or 0,
                submission["likes"] or 0,
                submission["comments"] or 0,
                submission["shares"] or 0,
                submission["favorites"] or 0,
                submission["recommendation"],
                submission["memo"],
                submission["evidence"] or "[]",
                submission["scraped_ok"] or 0,
                submission["video_analysis"] or "{}",
                submission["video_path"],
                submission["tech_score"] or 0,
                submission["marketing_score"] or 0,
                submission["content_genre"],
                submission["percentile_tech"] or 0,
                submission["percentile_mkt"] or 0,
                submission["vertical_category"],
                submission["vertical_tech_score"] or 0,
                submission["vertical_mkt_score"] or 0,
                submission["community_value"] or 0,
                submission["product_showcase_score"] or 0,
                submission["brand_exposure_score"] or 0,
                submission["storytelling_score"] or 0,
                submission["tech_status"],
                submission["logo_detected"] or 0,
                submission["product_closeup_count"] or 0,
                submission["points_awarded"] or 0,
                submission["points_pending"] or 0,
                submission["points_status"] or "pending",
                submission["error_message"],
                submission["started_at"] or None,
                submission["finished_at"] or None,
                submission["confirm_at"] or None,
            ),
        )
        id_map[int(submission["id"])] = int(pg_cur.fetchone()[0])
    return id_map


def restore_redemptions(
    sqlite_conn: sqlite3.Connection,
    pg_cur: psycopg.Cursor,
    user_map: dict[int, int],
) -> int:
    inserted = 0
    for redemption in sqlite_rows(sqlite_conn, "redemptions"):
        new_user_id = user_map.get(int(redemption["user_id"]))
        if not new_user_id:
            continue
        snapshot = normalize_json_text(redemption["address_snapshot"])
        try:
            snapshot_payload = json.loads(snapshot)
            if isinstance(snapshot_payload, dict):
                snapshot_payload["user_id"] = new_user_id
                snapshot = json.dumps(snapshot_payload, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

        pg_cur.execute(
            """
            INSERT INTO redemptions (
                id, created_at, user_id, reward_id, item_name, item_category,
                points_cost, address_id, address_snapshot, status, tracking_number,
                admin_note
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                reward_id = EXCLUDED.reward_id,
                item_name = EXCLUDED.item_name,
                item_category = EXCLUDED.item_category,
                points_cost = EXCLUDED.points_cost,
                address_id = EXCLUDED.address_id,
                address_snapshot = EXCLUDED.address_snapshot,
                status = EXCLUDED.status,
                tracking_number = EXCLUDED.tracking_number,
                admin_note = EXCLUDED.admin_note
            """,
            (
                redemption["id"],
                redemption["created_at"],
                new_user_id,
                redemption["reward_id"],
                redemption["item_name"],
                redemption["item_category"],
                redemption["points_cost"],
                redemption["address_id"],
                snapshot,
                redemption["status"],
                redemption["tracking_number"],
                redemption["admin_note"],
            ),
        )
        inserted += 1
    return inserted


def restore_points_log(
    sqlite_conn: sqlite3.Connection,
    pg_cur: psycopg.Cursor,
    user_map: dict[int, int],
    submission_map: dict[int, int],
) -> int:
    inserted = 0
    for entry in sqlite_rows(sqlite_conn, "points_log"):
        new_user_id = user_map.get(int(entry["user_id"]))
        if not new_user_id:
            continue
        old_submission_id = entry["submission_id"]
        new_submission_id = submission_map.get(int(old_submission_id)) if old_submission_id else None
        pg_cur.execute(
            """
            SELECT 1 FROM points_log
            WHERE user_id = %s
              AND created_at = %s
              AND delta = %s
              AND reason = %s
            LIMIT 1
            """,
            (new_user_id, entry["created_at"], entry["delta"], entry["reason"]),
        )
        if pg_cur.fetchone():
            continue

        pg_cur.execute(
            """
            INSERT INTO points_log (
                created_at, user_id, submission_id, delta, reason, balance_after
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                entry["created_at"],
                new_user_id,
                new_submission_id,
                entry["delta"],
                entry["reason"],
                entry["balance_after"],
            ),
        )
        inserted += 1
    return inserted


def bump_sequences(pg_cur: psycopg.Cursor) -> None:
    for table in TABLES_WITH_SEQUENCES:
        pg_cur.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE((SELECT MAX(id) FROM {table}), 1),
                TRUE
            )
            """
        )


def main() -> int:
    sqlite_path = Path(os.environ.get("LEGACY_SQLITE_DB", DEFAULT_SQLITE))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite DB not found: {sqlite_path}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    with psycopg.connect(database_url) as pg_conn:
        with pg_conn.cursor() as pg_cur:
            user_map = fetch_user_map(sqlite_conn, pg_cur)
            summary = {
                "user_points_updated": restore_user_points(sqlite_conn, pg_cur, user_map),
                "rewards_restored": restore_rewards(sqlite_conn, pg_cur, user_map),
                "addresses_restored": restore_addresses(sqlite_conn, pg_cur, user_map),
                "social_accounts_restored": restore_social_accounts(sqlite_conn, pg_cur, user_map),
            }
            submission_map = restore_submissions(sqlite_conn, pg_cur, user_map)
            summary["submissions_restored_or_mapped"] = len(submission_map)
            summary["redemptions_restored"] = restore_redemptions(sqlite_conn, pg_cur, user_map)
            summary["points_log_inserted"] = restore_points_log(sqlite_conn, pg_cur, user_map, submission_map)
            bump_sequences(pg_cur)
        pg_conn.commit()

    out_json(summary, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
