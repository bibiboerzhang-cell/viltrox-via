"""
db/migrations.py — 数据库初始化 + 迁移
"""
from __future__ import annotations

import os
import secrets as secrets_mod
from datetime import datetime

from app.core.config import IS_PRODUCTION
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.core.security import hash_password

logger = get_logger(__name__)


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
        except Exception:
            pass

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

    # ── via_personas ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_personas (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER DEFAULT 0,
            persona_key       TEXT NOT NULL UNIQUE,
            display_name      TEXT NOT NULL DEFAULT 'Via',
            archetype         TEXT DEFAULT 'brand_avatar',
            temperament       TEXT DEFAULT 'balanced',
            talk_style        TEXT DEFAULT 'warm',
            talkativeness     REAL DEFAULT 0.55,
            curiosity         REAL DEFAULT 0.7,
            outfit_code       TEXT DEFAULT 'viltrox_core_black',
            accessory_code    TEXT DEFAULT '',
            profile_json      TEXT DEFAULT '{}',
            memory_policy_json TEXT DEFAULT '{}',
            affinity_points   INTEGER DEFAULT 0,
            wardrobe_points   INTEGER DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    # ── via_sessions ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_sessions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            session_key       TEXT NOT NULL UNIQUE,
            user_id           INTEGER DEFAULT 0,
            persona_id        INTEGER DEFAULT 0,
            signed_device_id  TEXT DEFAULT '',
            client_fingerprint TEXT DEFAULT '',
            ip_hash           TEXT DEFAULT '',
            current_surface   TEXT DEFAULT 'upload',
            base_model        TEXT DEFAULT '',
            session_state_json TEXT DEFAULT '{}',
            last_event_id     TEXT DEFAULT '',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            ended_at          TEXT DEFAULT ''
        )
    """)

    # ── via_memory_refs ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_memory_refs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id        INTEGER NOT NULL,
            memory_kind       TEXT NOT NULL,
            source_ref        TEXT NOT NULL,
            memory_key        TEXT DEFAULT '',
            weight            REAL DEFAULT 0.5,
            payload_json      TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_decision_ledger ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_decision_ledger (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id       TEXT NOT NULL UNIQUE,
            session_key       TEXT NOT NULL,
            session_id        INTEGER DEFAULT 0,
            user_id           INTEGER DEFAULT 0,
            persona_id        INTEGER DEFAULT 0,
            decision_type     TEXT NOT NULL,
            trigger_type      TEXT DEFAULT '',
            trigger_payload_json TEXT DEFAULT '{}',
            state_snapshot_json TEXT DEFAULT '{}',
            candidates_json   TEXT DEFAULT '[]',
            chosen_action_json TEXT DEFAULT '{}',
            policy_key        TEXT DEFAULT '',
            policy_version    TEXT DEFAULT '',
            context_refs_json TEXT DEFAULT '[]',
            latency_ms        REAL DEFAULT 0,
            cost_estimate     REAL DEFAULT 0,
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_outcome_ledger ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_outcome_ledger (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id        TEXT NOT NULL UNIQUE,
            decision_id       TEXT NOT NULL,
            session_key       TEXT NOT NULL,
            accepted          INTEGER DEFAULT 0,
            followup_depth    INTEGER DEFAULT 0,
            rephrase_needed   INTEGER DEFAULT 0,
            clicked_product   INTEGER DEFAULT 0,
            added_to_cart     INTEGER DEFAULT 0,
            purchased         INTEGER DEFAULT 0,
            thumb_feedback    INTEGER DEFAULT 0,
            abuse_flag        INTEGER DEFAULT 0,
            reward_score      REAL DEFAULT 0,
            outcome_payload_json TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_reward_traces ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_reward_traces (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id          TEXT NOT NULL UNIQUE,
            session_key       TEXT NOT NULL,
            decision_id       TEXT DEFAULT '',
            user_id           INTEGER DEFAULT 0,
            event_type        TEXT NOT NULL,
            surface           TEXT DEFAULT '',
            source            TEXT DEFAULT '',
            origin            TEXT DEFAULT '',
            product_key       TEXT DEFAULT '',
            event_value       REAL DEFAULT 0,
            idempotency_key   TEXT DEFAULT '',
            event_payload_json TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_retrieval_evidence ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_retrieval_evidence (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id       TEXT NOT NULL UNIQUE,
            session_key       TEXT NOT NULL,
            decision_id       TEXT NOT NULL,
            policy_key        TEXT DEFAULT '',
            policy_version    TEXT DEFAULT '',
            retrieval_mode    TEXT DEFAULT '',
            candidate_sources_json TEXT DEFAULT '[]',
            selected_sources_json TEXT DEFAULT '[]',
            vector_hit_count  INTEGER DEFAULT 0,
            bundle_hit_count  INTEGER DEFAULT 0,
            seed_hit_count    INTEGER DEFAULT 0,
            vector_limit      INTEGER DEFAULT 0,
            top_score         REAL DEFAULT 0,
            avg_score         REAL DEFAULT 0,
            score_spread      REAL DEFAULT 0,
            rerank_applied    INTEGER DEFAULT 0,
            rerank_summary_json TEXT DEFAULT '{}',
            evidence_payload_json TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_policy_proposals ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_policy_proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_key      TEXT NOT NULL UNIQUE,
            proposal_type     TEXT NOT NULL,
            policy_key        TEXT NOT NULL,
            status            TEXT DEFAULT 'proposed',
            confidence        REAL DEFAULT 0,
            impact_score      REAL DEFAULT 0,
            evidence_json     TEXT DEFAULT '{}',
            proposal_json     TEXT DEFAULT '{}',
            window_days       INTEGER DEFAULT 0,
            evaluator_version TEXT DEFAULT '',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            applied_at        TEXT DEFAULT ''
        )
    """)

    # ── via_policy_versions ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_policy_versions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            version_key       TEXT NOT NULL UNIQUE,
            policy_key        TEXT NOT NULL,
            version_label     TEXT NOT NULL,
            status            TEXT DEFAULT 'live',
            source_proposal_key TEXT DEFAULT '',
            config_json       TEXT DEFAULT '{}',
            approved_by       TEXT DEFAULT '',
            approved_at       TEXT DEFAULT '',
            applied_by        TEXT DEFAULT '',
            applied_at        TEXT DEFAULT '',
            review_note       TEXT DEFAULT '',
            created_at        TEXT NOT NULL
        )
    """)

    # ── via_rollout_alerts ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_rollout_alerts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key         TEXT NOT NULL UNIQUE,
            policy_key        TEXT NOT NULL,
            version_key       TEXT NOT NULL,
            version_label     TEXT DEFAULT '',
            alert_type        TEXT NOT NULL,
            severity          TEXT DEFAULT 'medium',
            status            TEXT DEFAULT 'open',
            recommendation    TEXT DEFAULT '',
            reason_text       TEXT DEFAULT '',
            metrics_json      TEXT DEFAULT '{}',
            observed_at       TEXT DEFAULT '',
            created_at        TEXT NOT NULL,
            resolved_at       TEXT DEFAULT ''
        )
    """)

    # ── via_routing_provider_stats ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_routing_provider_stats (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_key        TEXT NOT NULL,
            target            TEXT DEFAULT 'dialogue_generation',
            provider          TEXT NOT NULL,
            exposure_count    INTEGER DEFAULT 0,
            success_count     INTEGER DEFAULT 0,
            reward_sum        REAL DEFAULT 0,
            guard_fail_count  INTEGER DEFAULT 0,
            avg_latency_ms    REAL DEFAULT 0,
            avg_cost_estimate REAL DEFAULT 0,
            last_outcome_at   TEXT DEFAULT '',
            metrics_json      TEXT DEFAULT '{}',
            updated_at        TEXT NOT NULL,
            UNIQUE(bucket_key, target, provider)
        )
    """)

    # ── via_memory_retention_stats ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS via_memory_retention_stats (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            retention_key     TEXT NOT NULL UNIQUE,
            user_id           INTEGER DEFAULT 0,
            session_key       TEXT DEFAULT '',
            memory_tier       TEXT DEFAULT '',
            memory_kind       TEXT DEFAULT '',
            fact_key          TEXT DEFAULT '',
            source_ref        TEXT DEFAULT '',
            confirmed_hits    INTEGER DEFAULT 0,
            reinforcement_count INTEGER DEFAULT 0,
            cumulative_reward REAL DEFAULT 0,
            last_hit_at       TEXT DEFAULT '',
            last_promoted_at  TEXT DEFAULT '',
            decay_state       TEXT DEFAULT 'fresh',
            status            TEXT DEFAULT 'active',
            metrics_json      TEXT DEFAULT '{}',
            updated_at        TEXT NOT NULL
        )
    """)

    # ── schools ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS schools (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id         TEXT NOT NULL UNIQUE,
            school_code       TEXT NOT NULL,
            school_name       TEXT NOT NULL,
            school_name_native TEXT DEFAULT '',
            country           TEXT DEFAULT '',
            region            TEXT DEFAULT '',
            school_type       TEXT DEFAULT 'film',
            tier              TEXT DEFAULT 'standard',
            partnership_status TEXT DEFAULT 'pilot',
            visual_theme_json TEXT DEFAULT '{}',
            metadata_json     TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    # ── student_qr_codes ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_qr_codes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            qr_id             TEXT NOT NULL UNIQUE,
            school_id         TEXT NOT NULL,
            issued_batch      TEXT DEFAULT '',
            display_serial    TEXT DEFAULT '',
            claim_token       TEXT NOT NULL,
            claim_signature   TEXT NOT NULL,
            claim_url         TEXT DEFAULT '',
            qr_code_url       TEXT DEFAULT '',
            card_image_url    TEXT DEFAULT '',
            manifest_url      TEXT DEFAULT '',
            status            TEXT DEFAULT 'issued',
            roster_mode       TEXT DEFAULT 'anonymous',
            bound_user_id     INTEGER DEFAULT 0,
            bound_at          TEXT DEFAULT '',
            issued_at         TEXT NOT NULL,
            expires_at        TEXT DEFAULT '',
            revoked_reason    TEXT DEFAULT '',
            prefilled_json    TEXT DEFAULT '{}',
            metadata_json     TEXT DEFAULT '{}'
        )
    """)

    # ── student_verifications ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_verifications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL,
            school_id         TEXT NOT NULL,
            student_id_code   TEXT NOT NULL,
            verification_method TEXT DEFAULT 'qr_scan',
            verification_proof_json TEXT DEFAULT '{}',
            status            TEXT DEFAULT 'active',
            commission_rate_override REAL DEFAULT 0.10,
            verified_by       TEXT DEFAULT 'system_qr',
            verified_at       TEXT NOT NULL,
            expires_at        TEXT DEFAULT '',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            UNIQUE(user_id, school_id)
        )
    """)

    # ── student_identity_registry ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_identity_registry (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id_code   TEXT NOT NULL UNIQUE,
            school_id         TEXT NOT NULL,
            full_name         TEXT DEFAULT '',
            major             TEXT DEFAULT '',
            year_label        TEXT DEFAULT '',
            status            TEXT DEFAULT 'active',
            source            TEXT DEFAULT 'seed',
            bound_user_id     INTEGER DEFAULT 0,
            claimed_at        TEXT DEFAULT '',
            metadata_json     TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    # ── student_scan_events ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_scan_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key         TEXT NOT NULL UNIQUE,
            qr_id             TEXT DEFAULT '',
            user_id           INTEGER DEFAULT 0,
            school_id         TEXT DEFAULT '',
            event_type        TEXT NOT NULL,
            location          TEXT DEFAULT '',
            event_payload_json TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── student_identity_audit_log ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_identity_audit_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_key         TEXT NOT NULL UNIQUE,
            qr_id             TEXT DEFAULT '',
            user_id           INTEGER DEFAULT 0,
            school_id         TEXT DEFAULT '',
            audit_type        TEXT NOT NULL,
            actor             TEXT DEFAULT '',
            reason            TEXT DEFAULT '',
            payload_json      TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ─────────────────────────
    # Migrations
    # ─────────────────────────

    # submissions
    existing_sub_cols = [r[1] for r in conn.execute("PRAGMA table_info(submissions)").fetchall()]
    if "extracted_handle" not in existing_sub_cols:
        try:
            c.execute("ALTER TABLE submissions ADD COLUMN extracted_handle TEXT DEFAULT ''")
        except Exception:
            pass

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
            except Exception:
                pass

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
            except Exception:
                pass

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
            except Exception:
                pass

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
            except Exception:
                pass

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
            except Exception:
                pass

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
            except Exception:
                pass

    # redemptions
    existing_red_cols = [r[1] for r in conn.execute("PRAGMA table_info(redemptions)").fetchall()]
    if "reward_id" not in existing_red_cols:
        try:
            c.execute("ALTER TABLE redemptions ADD COLUMN reward_id INTEGER")
        except Exception:
            pass

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
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_user_created ON submissions(user_id, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_created_at ON submissions(created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_detection_created ON submissions(detection_status, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_job_status ON submissions(job_status, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_platform_created ON submissions(platform, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_series_created ON submissions(product_series, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_handle_created ON submissions(extracted_handle, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_submissions_points_status ON submissions(points_status, id DESC)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_social_user_platform ON user_social_accounts(user_id, platform, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_social_handle_verified ON user_social_accounts(handle, verified)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_addresses_user_default ON user_addresses(user_id, is_default, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_redemptions_user_created ON redemptions(user_id, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_redemptions_status_created ON redemptions(status, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_points_log_user_created ON points_log(user_id, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_role_status ON users(role, status, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_email_tokens_user_type ON email_tokens(user_id, type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_verifications_user_created ON verifications(user_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_verifications_status_created ON verifications(status, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_verifications_platform_status ON verifications(platform, status, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reward_catalog_status_sort ON reward_catalog(status, sort_order, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_submission ON submission_assets(submission_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_role ON submission_assets(asset_role)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_checksum ON submission_assets(checksum)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_pending_cleanup ON submission_assets(submission_id, asset_role, deleted_at, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_asset_fp_asset_type ON asset_fingerprints(asset_id, fingerprint_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_asset_fp_value_type ON asset_fingerprints(fingerprint_value, fingerprint_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pcache_expires ON persistent_cache(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rlog_blocked ON rate_limit_log(blocked_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bh_snapshot ON bh_products(snapshot_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bh_sku ON bh_products(sku)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bh_title ON bh_products(title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bh_rating ON bh_products(rating DESC, review_count DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ingest_platform_status ON platform_ingest_events(source_platform, ingest_status, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ingest_entity_external ON platform_ingest_events(entity_type, external_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ingest_region_created ON platform_ingest_events(region_code, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_creator_memory_handle_kind ON creator_memory_entries(creator_handle, memory_kind, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_creator_memory_user_kind ON creator_memory_entries(user_id, memory_kind, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_market_obs_platform_subject ON market_observations(source_platform, subject_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_market_obs_region_created ON market_observations(region_code, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vx_accounts_platform_active ON viltrox_matrix_accounts(platform, is_active, name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vx_runs_completed ON viltrox_matrix_scan_runs(completed_at DESC, id DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vx_scan_accounts_run_account ON viltrox_matrix_scan_accounts(run_id, account_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vx_scan_posts_run_published ON viltrox_matrix_scan_posts(run_id, published_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vx_scan_posts_account_published ON viltrox_matrix_scan_posts(account_id, published_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_product_visual_product_type ON product_visual_features(product_key, feature_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_region_facts_region_type ON region_market_facts(region_code, fact_type, observed_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_source_event ON feedback_events(source_type, event_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_submission_created ON feedback_events(submission_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trust_events_user_created ON trust_events(user_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trust_events_event_created ON trust_events(event_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_personas_user_updated ON via_personas(user_id, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_sessions_user_updated ON via_sessions(user_id, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_sessions_persona_updated ON via_sessions(persona_id, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_sessions_device_updated ON via_sessions(signed_device_id, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_memory_session_created ON via_memory_refs(session_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_memory_kind_created ON via_memory_refs(memory_kind, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_decision_session_created ON via_decision_ledger(session_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_decision_type_created ON via_decision_ledger(decision_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_outcome_session_created ON via_outcome_ledger(session_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_outcome_decision_created ON via_outcome_ledger(decision_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_reward_trace_session_created ON via_reward_traces(session_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_reward_trace_decision_created ON via_reward_traces(decision_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_reward_trace_event_created ON via_reward_traces(event_type, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_reward_trace_idempotency ON via_reward_traces(session_key, idempotency_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_retrieval_evidence_session_created ON via_retrieval_evidence(session_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_retrieval_evidence_decision_created ON via_retrieval_evidence(decision_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_proposal_policy_updated ON via_policy_proposals(policy_key, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_proposal_status_updated ON via_policy_proposals(status, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_policy_versions_policy_created ON via_policy_versions(policy_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_policy_versions_status_policy ON via_policy_versions(status, policy_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_rollout_alerts_policy_created ON via_rollout_alerts(policy_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_rollout_alerts_version_created ON via_rollout_alerts(version_key, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_routing_stats_bucket_provider ON via_routing_provider_stats(bucket_key, provider, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_memory_retention_source_updated ON via_memory_retention_stats(source_ref, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_via_memory_retention_user_updated ON via_memory_retention_stats(user_id, updated_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_schools_code_name ON schools(school_code, school_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_qr_school_status ON student_qr_codes(school_id, status, issued_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_qr_batch_serial ON student_qr_codes(issued_batch, display_serial)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_qr_user_bound ON student_qr_codes(bound_user_id, bound_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_verifications_user_status ON student_verifications(user_id, status, verified_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_verifications_school_status ON student_verifications(school_id, status, verified_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_scan_events_user_created ON student_scan_events(user_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_scan_events_qr_created ON student_scan_events(qr_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_audit_qr_created ON student_identity_audit_log(qr_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_student_audit_user_created ON student_identity_audit_log(user_id, created_at DESC)")

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
    admin_exists = conn.execute(
        "SELECT id FROM users WHERE role='admin' LIMIT 1"
    ).fetchone()

    if not admin_exists:
        admin_pw_plain = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_pw_plain:
            if IS_PRODUCTION:
                raise RuntimeError("2.0 production requires ADMIN_PASSWORD to bootstrap the first admin account")
            admin_pw_plain = secrets_mod.token_urlsafe(16)
            logger.warning(
                "Generated ephemeral local admin password for bootstrap only — change it immediately: %s",
                admin_pw_plain,
            )
        else:
            logger.info("Admin password loaded from ADMIN_PASSWORD env var")

        admin_pw = hash_password(admin_pw_plain)

        conn.execute("""
            INSERT OR IGNORE INTO users
            (created_at, email, password_hash, name, status, role)
            VALUES (?,?,?,?,?,?)
        """, (
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "admin@viltrox.com",
            admin_pw,
            "Admin",
            "approved",
            "admin"
        ))

    conn.commit()

    try:
        from app.db.migrations_v5 import apply_v5_migrations

        apply_v5_migrations()
    except Exception:
        logger.exception("v5 migrations failed during init_db")
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
