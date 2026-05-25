"""
db/migrations.py — 数据库初始化 + 迁移
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.migrations_admin import ensure_default_admin_account
from app.db.migrations_core_indexes import create_core_indexes
from app.db.migrations_student_identity import create_student_identity_tables
from app.db.migrations_via_tables import create_via_tables

logger = get_logger(__name__)


def _log_incremental_column_skip(table: str, column: str, exc: Exception) -> None:
    logger.debug("sqlite incremental column skipped | table=%s | column=%s | error=%s", table, column, exc)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT,
            platform        TEXT,
            url             TEXT,
            extracted_handle TEXT DEFAULT '',
            title           TEXT,
            detection_status TEXT,
            product_series  TEXT,
            product_label   TEXT,
            content_types   TEXT,
            final_score     INTEGER,
            creator_score   INTEGER,
            overall_score   INTEGER,
            risk_score      INTEGER,
            views           INTEGER,
            likes           INTEGER,
            comments        INTEGER,
            shares          INTEGER,
            favorites       INTEGER,
            recommendation  TEXT,
            memo            TEXT,
            evidence        TEXT,
            scraped_ok      INTEGER,
            video_analysis  TEXT,
            video_path      TEXT
        )
    """)

    for col, coltype in [
        ("video_analysis", "TEXT"), ("video_path", "TEXT"),
        ("tech_score", "REAL"), ("marketing_score", "REAL"),
        ("content_genre", "TEXT"), ("percentile_tech", "REAL"),
        ("percentile_mkt", "REAL"),
        ("vertical_category", "TEXT"), ("vertical_tech_score", "REAL"),
        ("vertical_mkt_score", "REAL"), ("community_value", "REAL"),
        ("product_showcase_score", "REAL"),
        ("brand_exposure_score", "REAL"), ("storytelling_score", "REAL"),
        ("tech_status", "TEXT"), ("logo_detected", "INTEGER"),
        ("product_closeup_count", "INTEGER"),
    ]:
        try:
            c.execute(f"ALTER TABLE submissions ADD COLUMN {col} {coltype}")
        except Exception as exc:
            _log_incremental_column_skip("submissions", col, exc)

    # ── genre_benchmarks ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS genre_benchmarks (
            genre           TEXT PRIMARY KEY,
            sample_count    INTEGER DEFAULT 0,
            p25_tech        REAL DEFAULT 0,
            p50_tech        REAL DEFAULT 0,
            p75_tech        REAL DEFAULT 0,
            p90_tech        REAL DEFAULT 0,
            p25_mkt         REAL DEFAULT 0,
            p50_mkt         REAL DEFAULT 0,
            p75_mkt         REAL DEFAULT 0,
            p90_mkt         REAL DEFAULT 0,
            avg_overall     REAL DEFAULT 0,
            updated_at      TEXT
        )
    """)

    # ── insights_cache ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS insights_cache (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT
        )
    """)

    # ── verifications ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT,
            platform    TEXT,
            handle      TEXT,
            code        TEXT UNIQUE,
            status      TEXT DEFAULT 'pending',
            approved_at TEXT,
            note        TEXT
        )
    """)

    # ── users（唯一保留这一份） ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT,
            email           TEXT UNIQUE,
            password_hash   TEXT,
            name            TEXT,
            creator_code    TEXT,
            status          TEXT DEFAULT 'pending',
            role            TEXT DEFAULT 'creator',
            points_balance  INTEGER DEFAULT 0,
            points_pending  INTEGER DEFAULT 0,
            points_total    INTEGER DEFAULT 0,
            last_login      TEXT,
            note            TEXT,
            email_verified  INTEGER DEFAULT 0,
            social_verified INTEGER DEFAULT 0,
            avatar_url      TEXT,
            bio             TEXT,
            signature       TEXT,
            tier_status     TEXT DEFAULT 'pending',
            trust_score     REAL DEFAULT 30.0,
            trust_updated_at TEXT
        )
    """)

    # ── user_addresses ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_addresses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            name         TEXT,
            phone        TEXT,
            address1     TEXT,
            address2     TEXT,
            city         TEXT,
            state        TEXT,
            country      TEXT DEFAULT 'US',
            postal_code  TEXT,
            is_default   INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS failed_logins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            attempted_at  TEXT NOT NULL,
            ip_truncated  TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_failed_logins_user_time
        ON failed_logins(user_id, attempted_at DESC)
    """)

    # ── redemptions ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS redemptions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT,
            user_id          INTEGER,
            reward_id        INTEGER,
            item_name        TEXT,
            item_category    TEXT,
            points_cost      INTEGER,
            address_id       INTEGER,
            address_snapshot TEXT,
            status           TEXT DEFAULT 'pending',
            tracking_number  TEXT,
            admin_note       TEXT
        )
    """)

    # ── email_tokens ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL DEFAULT 'verify_email',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        )
    """)

    # ── user_social_accounts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_social_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            verified_at TEXT,
            verify_code TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(platform, handle)
        )
    """)

    # ── points_log ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS points_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            submission_id INTEGER,
            delta INTEGER NOT NULL,
            reason TEXT,
            balance_after INTEGER
        )
    """)

    # ── reward_catalog ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS reward_catalog (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT,
            updated_at      TEXT,
            title           TEXT NOT NULL,
            description     TEXT DEFAULT '',
            category        TEXT NOT NULL,
            points_cost     INTEGER NOT NULL,
            meta_label      TEXT DEFAULT '',
            image_url       TEXT DEFAULT '',
            stock           INTEGER DEFAULT 0,
            sort_order      INTEGER DEFAULT 100,
            status          TEXT DEFAULT 'draft',
            published_at    TEXT,
            published_by    INTEGER
        )
    """)

    # ── submission_assets ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS submission_assets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id   INTEGER NOT NULL DEFAULT 0,
            asset_role      TEXT NOT NULL,
            storage_key     TEXT NOT NULL,
            mime_type       TEXT DEFAULT '',
            size_bytes      INTEGER DEFAULT 0,
            duration_ms     INTEGER DEFAULT 0,
            width           INTEGER DEFAULT 0,
            height          INTEGER DEFAULT 0,
            checksum        TEXT DEFAULT '',
            deleted_at      TEXT,
            deleted_reason  TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )
    """)

    # ── asset_fingerprints ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS asset_fingerprints (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id          INTEGER NOT NULL,
            fingerprint_type  TEXT NOT NULL,
            frame_slot        TEXT DEFAULT '',
            frame_index       INTEGER DEFAULT 0,
            fingerprint_value TEXT NOT NULL,
            created_at        TEXT NOT NULL
        )
    """)

    # ── persistent_cache ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS persistent_cache (
            cache_key       TEXT PRIMARY KEY,
            value_json      TEXT NOT NULL,
            expires_at      TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)

    # ── rate_limit_log ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket          TEXT NOT NULL,
            client_id       TEXT NOT NULL,
            blocked_at      TEXT NOT NULL
        )
    """)

    # ── bh_products ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS bh_products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            price           REAL DEFAULT 0,
            rating          REAL DEFAULT 0,
            review_count    INTEGER DEFAULT 0,
            url             TEXT,
            image_url       TEXT,
            in_stock        INTEGER DEFAULT 1,
            sku             TEXT,
            scraped_at      TEXT NOT NULL,
            snapshot_at     TEXT NOT NULL,
            raw_json        TEXT
        )
    """)

    # ── platform_ingest_events ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS platform_ingest_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key      TEXT NOT NULL UNIQUE,
            source_platform TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            entity_type     TEXT NOT NULL,
            external_id     TEXT DEFAULT '',
            creator_handle  TEXT DEFAULT '',
            region_code     TEXT DEFAULT '',
            ingest_status   TEXT DEFAULT 'queued',
            payload_json    TEXT DEFAULT '{}',
            occurred_at     TEXT NOT NULL,
            processed_at    TEXT DEFAULT '',
            error_message   TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )
    """)

    # ── creator public page shop heroes / clicks ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS creator_shop_heroes (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT DEFAULT '',
            image_url TEXT NOT NULL,
            target_url TEXT NOT NULL,
            badge TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_creator_shop_heroes_user
        ON creator_shop_heroes(user_id, is_active, sort_order)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS creator_public_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            creator_code TEXT NOT NULL,
            click_type TEXT NOT NULL,
            target_url TEXT NOT NULL,
            shop_hero_id TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            ip_hash TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_creator_public_clicks_creator
        ON creator_public_clicks(creator_code, created_at DESC)
    """)

    # ── creator_memory_entries ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS creator_memory_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_key      TEXT NOT NULL UNIQUE,
            user_id         INTEGER DEFAULT 0,
            creator_handle  TEXT DEFAULT '',
            memory_kind     TEXT NOT NULL,
            fact_key        TEXT NOT NULL,
            fact_value_json TEXT DEFAULT '{}',
            confidence      REAL DEFAULT 0.5,
            source_ref      TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # ── market_observations ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_key TEXT NOT NULL UNIQUE,
            source_platform TEXT NOT NULL,
            subject_type    TEXT NOT NULL,
            subject_key     TEXT NOT NULL,
            observation_type TEXT NOT NULL,
            summary         TEXT DEFAULT '',
            metrics_json    TEXT DEFAULT '{}',
            evidence_json   TEXT DEFAULT '[]',
            region_code     TEXT DEFAULT '',
            observed_at     TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)

    # ── viltrox_matrix_accounts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS viltrox_matrix_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            platform        TEXT NOT NULL,
            handle          TEXT NOT NULL,
            name            TEXT NOT NULL,
            source_key      TEXT NOT NULL DEFAULT 'official_matrix',
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE(platform, handle)
        )
    """)

    # ── viltrox_matrix_scan_runs ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key          TEXT NOT NULL UNIQUE,
            status           TEXT NOT NULL DEFAULT 'completed',
            started_at       TEXT NOT NULL,
            completed_at     TEXT NOT NULL,
            total_accounts   INTEGER NOT NULL DEFAULT 0,
            scanned_accounts INTEGER NOT NULL DEFAULT 0,
            total_posts      INTEGER NOT NULL DEFAULT 0,
            total_views      INTEGER NOT NULL DEFAULT 0,
            total_likes      INTEGER NOT NULL DEFAULT 0,
            total_comments   INTEGER NOT NULL DEFAULT 0,
            aggregate_json   TEXT NOT NULL DEFAULT '{}',
            error_message    TEXT DEFAULT '',
            created_at       TEXT NOT NULL
        )
    """)

    # ── viltrox_matrix_scan_accounts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            account_id      INTEGER NOT NULL,
            total_posts     INTEGER NOT NULL DEFAULT 0,
            total_views     INTEGER NOT NULL DEFAULT 0,
            total_likes     INTEGER NOT NULL DEFAULT 0,
            total_comments  INTEGER NOT NULL DEFAULT 0,
            duration_sec    REAL NOT NULL DEFAULT 0,
            error_message   TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            UNIQUE(run_id, account_id)
        )
    """)

    # ── viltrox_matrix_scan_posts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_posts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            account_id      INTEGER NOT NULL,
            title           TEXT DEFAULT '',
            post_url        TEXT DEFAULT '',
            thumbnail_url   TEXT DEFAULT '',
            views           INTEGER NOT NULL DEFAULT 0,
            likes           INTEGER NOT NULL DEFAULT 0,
            comments        INTEGER NOT NULL DEFAULT 0,
            shares          INTEGER NOT NULL DEFAULT 0,
            published_at    TEXT DEFAULT '',
            content_type    TEXT DEFAULT '',
            raw_json        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL
        )
    """)

    # ── product_knowledge ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS product_knowledge (
            product_key     TEXT PRIMARY KEY,
            label           TEXT NOT NULL,
            family          TEXT DEFAULT '',
            mount_type      TEXT DEFAULT '',
            alias_terms_json TEXT DEFAULT '[]',
            feature_tags_json TEXT DEFAULT '[]',
            scene_tags_json TEXT DEFAULT '[]',
            status          TEXT DEFAULT 'seed',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # ── product_visual_features ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS product_visual_features (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key     TEXT NOT NULL,
            asset_role      TEXT DEFAULT '',
            storage_key     TEXT DEFAULT '',
            feature_type    TEXT NOT NULL,
            feature_vector_json TEXT DEFAULT '{}',
            detector_version TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )
    """)

    # ── region_market_facts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS region_market_facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_key        TEXT NOT NULL UNIQUE,
            region_code     TEXT NOT NULL,
            region_level    TEXT DEFAULT 'country',
            fact_type       TEXT NOT NULL,
            fact_value_json TEXT DEFAULT '{}',
            source_platform TEXT DEFAULT '',
            observed_at     TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)

    # ── feedback_events ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS feedback_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type     TEXT NOT NULL,
            source_id       TEXT DEFAULT '',
            event_type      TEXT NOT NULL,
            actor_role      TEXT DEFAULT '',
            user_id         INTEGER DEFAULT 0,
            submission_id   INTEGER DEFAULT 0,
            payload_json    TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL
        )
    """)

    # ── trust_events ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS trust_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            event_type      TEXT NOT NULL,
            score_delta     REAL NOT NULL DEFAULT 0,
            new_total       REAL NOT NULL DEFAULT 0,
            context_json    TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL
        )
    """)

    create_via_tables(c)

    create_student_identity_tables(c)

    # ─────────────────────────
    # Migrations
    # ─────────────────────────

    # submissions
    existing_sub_cols = [r[1] for r in conn.execute("PRAGMA table_info(submissions)").fetchall()]
    if "extracted_handle" not in existing_sub_cols:
        try:
            c.execute("ALTER TABLE submissions ADD COLUMN extracted_handle TEXT DEFAULT ''")
        except Exception as exc:
            _log_incremental_column_skip("submissions", "extracted_handle", exc)

    for col, ct in [
        ("user_id", "INTEGER"),
        ("points_awarded", "INTEGER DEFAULT 0"),
        ("points_status", "TEXT DEFAULT 'pending'"),
        ("job_status", "TEXT DEFAULT 'legacy'"),
        ("error_message", "TEXT DEFAULT ''"),
        ("started_at", "TEXT DEFAULT ''"),
        ("finished_at", "TEXT DEFAULT ''"),
        ("raw_text", "TEXT DEFAULT ''"),
        ("caption", "TEXT DEFAULT ''"),
        ("points_pending", "INTEGER DEFAULT 0"),
        ("confirm_at", "TEXT"),
    ]:
        if col not in existing_sub_cols:
            try:
                c.execute(f"ALTER TABLE submissions ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("submissions", col, exc)

    # verifications
    existing_verification_cols = [r[1] for r in conn.execute("PRAGMA table_info(verifications)").fetchall()]
    for col, ct in [
        ("user_id", "INTEGER"),
        ("profile_url", "TEXT"),
        ("baseline_username", "TEXT"),
        ("baseline_followers", "INTEGER DEFAULT 0"),
        ("baseline_avatar_url", "TEXT"),
        ("baseline_bio", "TEXT"),
        ("baseline_data_json", "TEXT"),
        ("generated_comment", "TEXT"),
        ("posted_at", "TEXT"),
        ("match_score", "REAL DEFAULT 0"),
        ("scan_count", "INTEGER DEFAULT 0"),
        ("last_scanned_at", "TEXT"),
        ("comment_id", "TEXT"),
        ("comment_username", "TEXT"),
        ("comment_text", "TEXT"),
        ("comment_video_url", "TEXT"),
        ("expires_at", "TEXT"),
    ]:
        if col not in existing_verification_cols:
            try:
                c.execute(f"ALTER TABLE verifications ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("verifications", col, exc)

    # users
    existing_user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    for col, ct in [
        ("email_verified", "INTEGER DEFAULT 0"),
        ("social_verified", "INTEGER DEFAULT 0"),
        ("creator_code", "TEXT"),
        ("tier_status", "TEXT DEFAULT 'pending'"),
        ("trust_score", "REAL DEFAULT 30.0"),
        ("trust_updated_at", "TEXT"),
    ]:
        if col not in existing_user_cols:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("users", col, exc)

    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_creator_code ON users(creator_code)")

    # via_policy_proposals incremental columns
    existing_proposal_cols = [r[1] for r in conn.execute("PRAGMA table_info(via_policy_proposals)").fetchall()]
    for col, ct in [
        ("reviewed_by", "TEXT DEFAULT ''"),
        ("review_note", "TEXT DEFAULT ''"),
        ("reviewed_at", "TEXT DEFAULT ''"),
        ("applied_version_key", "TEXT DEFAULT ''"),
        ("applied_by", "TEXT DEFAULT ''"),
    ]:
        if col not in existing_proposal_cols:
            try:
                c.execute(f"ALTER TABLE via_policy_proposals ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("via_policy_proposals", col, exc)

    existing_reward_trace_cols = [r[1] for r in conn.execute("PRAGMA table_info(via_reward_traces)").fetchall()]
    for col, ct in [
        ("surface", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT ''"),
        ("origin", "TEXT DEFAULT ''"),
        ("idempotency_key", "TEXT DEFAULT ''"),
    ]:
        if col not in existing_reward_trace_cols:
            try:
                c.execute(f"ALTER TABLE via_reward_traces ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("via_reward_traces", col, exc)

    # reward_catalog
    existing_reward_cols = [r[1] for r in conn.execute("PRAGMA table_info(reward_catalog)").fetchall()]
    for col, ct in [
        ("updated_at", "TEXT"),
        ("meta_label", "TEXT DEFAULT ''"),
        ("image_url", "TEXT DEFAULT ''"),
        ("stock", "INTEGER DEFAULT 0"),
        ("sort_order", "INTEGER DEFAULT 100"),
        ("status", "TEXT DEFAULT 'draft'"),
        ("published_at", "TEXT"),
        ("published_by", "INTEGER"),
    ]:
        if col not in existing_reward_cols:
            try:
                c.execute(f"ALTER TABLE reward_catalog ADD COLUMN {col} {ct}")
            except Exception as exc:
                _log_incremental_column_skip("reward_catalog", col, exc)

    # redemptions
    existing_red_cols = [r[1] for r in conn.execute("PRAGMA table_info(redemptions)").fetchall()]
    if "reward_id" not in existing_red_cols:
        try:
            c.execute("ALTER TABLE redemptions ADD COLUMN reward_id INTEGER")
        except Exception as exc:
            _log_incremental_column_skip("redemptions", "reward_id", exc)

    existing_asset_cols = [r[1] for r in conn.execute("PRAGMA table_info(submission_assets)").fetchall()]
    for col, ct in [
        ("deleted_at", "TEXT"),
        ("deleted_reason", "TEXT DEFAULT ''"),
    ]:
        if col not in existing_asset_cols:
            try:
                c.execute(f"ALTER TABLE submission_assets ADD COLUMN {col} {ct}")
            except Exception as exc:
                logger.warning("submission_assets incremental column skipped | column=%s | error=%s", col, exc)

    # ─────────────────────────
    # Indexes: hot reads + future Postgres cutover alignment
    # ─────────────────────────
    create_core_indexes(c)

    # ─────────────────────────
    # Backfill creator_code for old creator users
    # ─────────────────────────
    rows = c.execute("SELECT id, creator_code, role FROM users ORDER BY id ASC").fetchall()
    for r in rows:
        if r["role"] == "creator" and not r["creator_code"]:
            code = f"V_{int(r['id']):06d}"
            c.execute("UPDATE users SET creator_code=? WHERE id=?", (code, r["id"]))

    # ─────────────────────────
    # Ensure default admin account exists
    # ─────────────────────────
    ensure_default_admin_account(conn)

    conn.commit()

    try:
        from app.db.migrations_v5 import apply_v5_migrations

        apply_v5_migrations()
    except Exception:
        logger.exception("v5 migrations failed during init_db")
        raise

    try:
        from app.db.migrations_activities import apply_activities_migrations

        apply_activities_migrations()
    except Exception:
        logger.exception("activities migrations failed during init_db")
        raise

    # ── PATCH 2026-04-20: trust perf indexes ───────────────────────
    # 见 services/trust.py::_count_paid_shopify_orders
    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_attr_user_status
                ON orders(attribution_user_id, status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ingest_shopify_by_handle
                ON platform_ingest_events(creator_handle, source_platform, entity_type, ingest_status)
        """)
        conn.commit()
        logger.info("trust perf indexes ready")
    except Exception:
        logger.exception("trust perf indexes failed (non-fatal)")


init_db()
