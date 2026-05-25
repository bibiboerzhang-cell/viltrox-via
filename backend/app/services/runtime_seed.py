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
    REWARD_SEEDS,
    RUNTIME_SEED_MEMO_PREFIX,
)
from app.services.runtime_seed_submissions import seed_leaderboard_submission_rows, seed_preview_submission_rows
from app.services.student_identity import ensure_student_identity_registry_defaults


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    seeded_submissions = seed_preview_submission_rows(conn, preview_user_id, datetime.now(timezone.utc))
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
            leaderboard_rows += seed_leaderboard_submission_rows(conn, int(user_id), seed, now)
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
