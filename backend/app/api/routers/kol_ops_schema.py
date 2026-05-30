"""Local schema guard for KOL Operations router."""
from __future__ import annotations

from app.api.routers.kol_ops_helpers import _ensure_sqlite_columns
from app.db.connection import get_conn, is_postgres_runtime


_SCHEMA_READY = False


def ensure_kol_schema() -> None:
    """Create local SQLite KOL tables for dev/demo runs.

    Production Postgres uses migrations/019_kol_operations.sql; this local guard
    prevents admin demo screens from failing when SQLite migrations have not yet
    been applied.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn = get_conn()
    if not is_postgres_runtime():
        conn.executescript(
            """
        CREATE TABLE IF NOT EXISTS kols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            channel_url TEXT,
            platform TEXT NOT NULL,
            country TEXT,
            niche TEXT,
            project_name TEXT,
            owner_name TEXT,
            media_name TEXT,
            duplicate_flag TEXT,
            scale_tier TEXT,
            content_type TEXT,
            approval_note TEXT,
            channel_tags TEXT,
            affiliate_id TEXT,
            affiliate_link TEXT,
            discount_code TEXT,
            amazon_link TEXT,
            short_link TEXT,
            primary_category TEXT,
            promoted_product TEXT,
            follower_count INTEGER DEFAULT 0,
            avg_views INTEGER DEFAULT 0,
            contact_email TEXT,
            contact_phone TEXT,
            contact_status TEXT DEFAULT 'cold',
            notes TEXT,
            assigned_staff_id INTEGER,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kols_assigned ON kols(assigned_staff_id);
        CREATE INDEX IF NOT EXISTS idx_kols_platform_country ON kols(platform, country);
        CREATE INDEX IF NOT EXISTS idx_kols_status ON kols(contact_status);
        CREATE INDEX IF NOT EXISTS idx_kols_updated ON kols(updated_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS kol_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            action_at TEXT NOT NULL,
            notes TEXT,
            next_action_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outreach_kol ON kol_outreach(kol_id);

        CREATE TABLE IF NOT EXISTS kol_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            product_sku TEXT,
            staff_id INTEGER NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            cost_cents INTEGER DEFAULT 0,
            status TEXT DEFAULT 'planning',
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_campaigns_kol ON kol_campaigns(kol_id);
        CREATE INDEX IF NOT EXISTS idx_campaigns_staff ON kol_campaigns(staff_id);

        CREATE TABLE IF NOT EXISTS kol_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            content_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            posted_at TEXT,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            ai_quality_score INTEGER,
            ai_summary TEXT,
            ai_topics_json TEXT,
            content_title TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            scraped_text TEXT DEFAULT '',
            visible_comments_json TEXT DEFAULT '[]',
            ai_analysis_json TEXT DEFAULT '{}',
            analysis_status TEXT DEFAULT 'not_analyzed',
            analysis_error TEXT DEFAULT '',
            analysis_method TEXT DEFAULT '',
            last_metric_refresh TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_content_campaign ON kol_content(campaign_id);

        CREATE TABLE IF NOT EXISTS kol_attribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            shopify_order_id TEXT,
            attributed_revenue_cents INTEGER DEFAULT 0,
            attributed_at TEXT NOT NULL,
            UNIQUE(content_id, shopify_order_id)
        );

        CREATE TABLE IF NOT EXISTS kol_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            channel_url TEXT,
            handle TEXT,
            country TEXT,
            niche TEXT,
            source_url TEXT,
            sample_title TEXT,
            follower_count INTEGER DEFAULT 0,
            avg_views INTEGER DEFAULT 0,
            contact_email TEXT,
            status TEXT DEFAULT 'new',
            search_query TEXT,
            market TEXT,
            reviewed_by_staff_id INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_status ON kol_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_platform_market ON kol_candidates(platform, market);
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_query ON kol_candidates(search_query);

        CREATE TABLE IF NOT EXISTS kol_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            user_id INTEGER,
            staff_name TEXT,
            action_type TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            query TEXT,
            platform TEXT,
            market TEXT,
            api_provider TEXT,
            api_calls INTEGER DEFAULT 0,
            result_count INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_activity_created ON kol_activity_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_kol_activity_staff ON kol_activity_log(staff_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS kol_account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT DEFAULT '',
            account_url TEXT DEFAULT '',
            follower_count INTEGER DEFAULT 0,
            content_count INTEGER DEFAULT 0,
            total_views INTEGER DEFAULT 0,
            total_likes INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            total_shares INTEGER DEFAULT 0,
            avg_views INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            brand_mentions_json TEXT DEFAULT '[]',
            competitor_mentions_json TEXT DEFAULT '[]',
            comment_count INTEGER DEFAULT 0,
            scan_status TEXT DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{}',
            scanned_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_account_snapshots_kol_time ON kol_account_snapshots(kol_id, scanned_at DESC);

        CREATE TABLE IF NOT EXISTS kol_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            platform TEXT NOT NULL,
            post_url TEXT NOT NULL,
            title TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            published_at TEXT,
            content_type TEXT DEFAULT '',
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            brand_mentions_json TEXT DEFAULT '[]',
            competitor_mentions_json TEXT DEFAULT '[]',
            comment_sentiment TEXT DEFAULT 'unknown',
            raw_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(kol_id, post_url)
        );
        CREATE INDEX IF NOT EXISTS idx_kol_posts_kol_views ON kol_posts(kol_id, views DESC);

        CREATE TABLE IF NOT EXISTS kol_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            post_id INTEGER,
            platform TEXT NOT NULL,
            post_url TEXT DEFAULT '',
            author_handle TEXT DEFAULT '',
            comment_text TEXT NOT NULL,
            like_count INTEGER DEFAULT 0,
            sentiment TEXT DEFAULT 'unknown',
            intent_tags_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_comments_kol_time ON kol_comments(kol_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS kol_analysis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            report_type TEXT DEFAULT 'account_dossier',
            account_score INTEGER DEFAULT 0,
            audience_fit INTEGER DEFAULT 0,
            product_fit INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'unknown',
            recommended_action TEXT DEFAULT '',
            suggested_products_json TEXT DEFAULT '[]',
            brand_history_json TEXT DEFAULT '{}',
            comment_insights_json TEXT DEFAULT '{}',
            summary_zh TEXT DEFAULT '',
            summary_en TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{}',
            method TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_analysis_reports_kol_time ON kol_analysis_reports(kol_id, created_at DESC);
        """
        )
    _ensure_sqlite_columns(
        conn,
        "kols",
        {
            "project_name": "TEXT",
            "owner_name": "TEXT",
            "media_name": "TEXT",
            "duplicate_flag": "TEXT",
            "scale_tier": "TEXT",
            "content_type": "TEXT",
            "approval_note": "TEXT",
            "channel_tags": "TEXT",
            "affiliate_id": "TEXT",
            "affiliate_link": "TEXT",
            "discount_code": "TEXT",
            "amazon_link": "TEXT",
            "short_link": "TEXT",
            "primary_category": "TEXT",
            "promoted_product": "TEXT",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "kol_content",
        {
            "content_title": "TEXT DEFAULT ''",
            "thumbnail_url": "TEXT DEFAULT ''",
            "scraped_text": "TEXT DEFAULT ''",
            "visible_comments_json": "TEXT DEFAULT '[]'",
            "ai_analysis_json": "TEXT DEFAULT '{}'",
            "analysis_status": "TEXT DEFAULT 'not_analyzed'",
            "analysis_error": "TEXT DEFAULT ''",
            "analysis_method": "TEXT DEFAULT ''",
        },
    )
    conn.commit()
    _SCHEMA_READY = True

