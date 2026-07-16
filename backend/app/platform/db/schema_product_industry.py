"""SQLite guard for Product Analysis, Industry Data, and automation prebuilds."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_product_industry_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_product_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            launch_uid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            product_sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            target_market TEXT DEFAULT '',
            target_platforms_json TEXT NOT NULL DEFAULT '[]',
            target_audience_json TEXT NOT NULL DEFAULT '{}',
            competitor_products_json TEXT NOT NULL DEFAULT '[]',
            launch_window_start TEXT,
            launch_window_end TEXT,
            budget_range_json TEXT NOT NULL DEFAULT '{}',
            goals_json TEXT NOT NULL DEFAULT '{}',
            constraints_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            created_by_staff_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_product_launches_status
            ON vkpi_product_launches(status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_market_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uid TEXT NOT NULL UNIQUE,
            launch_id INTEGER,
            scan_type TEXT NOT NULL DEFAULT 'manual',
            platforms_json TEXT NOT NULL DEFAULT '[]',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'queued',
            provider_status TEXT NOT NULL DEFAULT 'not_configured',
            summary_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT DEFAULT '',
            triggered_by_staff_id INTEGER,
            triggered_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_market_scan_runs_launch
            ON vkpi_market_scan_runs(launch_id, triggered_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_market_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            source_type TEXT NOT NULL,
            platform TEXT DEFAULT '',
            source_ref TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_market_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            source_id INTEGER,
            platform TEXT DEFAULT '',
            handle TEXT DEFAULT '',
            mention_text TEXT DEFAULT '',
            product_sku TEXT DEFAULT '',
            competitor_product TEXT DEFAULT '',
            sentiment TEXT DEFAULT '',
            score REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_competitor_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            launch_id INTEGER,
            brand TEXT NOT NULL DEFAULT '',
            product_name TEXT NOT NULL DEFAULT '',
            sku TEXT DEFAULT '',
            category TEXT DEFAULT '',
            price_cents INTEGER DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_competitor_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_product_id INTEGER,
            platform TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            published_at TEXT,
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_kol_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_uid TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            profile_url TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            country TEXT DEFAULT '',
            language TEXT DEFAULT '',
            email TEXT DEFAULT '',
            other_contacts_json TEXT NOT NULL DEFAULT '[]',
            contact_source TEXT NOT NULL DEFAULT '',
            followers INTEGER,
            following INTEGER,
            posts_count INTEGER,
            avg_views INTEGER,
            avg_likes INTEGER,
            avg_comments INTEGER,
            avg_shares INTEGER,
            engagement_rate REAL,
            primary_topic TEXT DEFAULT '',
            secondary_topics_json TEXT NOT NULL DEFAULT '[]',
            content_style TEXT DEFAULT '',
            production_quality TEXT DEFAULT '',
            audience_estimated_json TEXT NOT NULL DEFAULT '{}',
            brand_collaborations_json TEXT NOT NULL DEFAULT '[]',
            viltrox_fit_score REAL,
            viltrox_fit_reason TEXT DEFAULT '',
            potential_concerns_json TEXT NOT NULL DEFAULT '[]',
            recommended_product_lines_json TEXT NOT NULL DEFAULT '[]',
            linked_main_kol_id INTEGER,
            duplicate_of_id INTEGER,
            sync_status TEXT NOT NULL DEFAULT 'imported',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_ref TEXT DEFAULT '',
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            created_by_staff_id INTEGER,
            last_seen_at TEXT,
            last_video_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, handle)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_platform_handle
            ON vkpi_kol_pool(platform, handle);
        CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_score
            ON vkpi_kol_pool(viltrox_fit_score DESC);

        CREATE TABLE IF NOT EXISTS vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            project_id INTEGER,
            content_url TEXT NOT NULL UNIQUE,
            platform TEXT DEFAULT '',
            title TEXT DEFAULT '',
            video_title TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            duration_seconds INTEGER,
            publish_date TEXT,
            posted_at TEXT,
            evidence_type TEXT NOT NULL DEFAULT 'video',
            image_urls TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT '',
            source_ref TEXT DEFAULT '',
            confidence TEXT DEFAULT 'high',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_kol_video_evidence_pool
            ON vkpi_kol_video_evidence(kol_pool_id);

        CREATE TABLE IF NOT EXISTS vkpi_kol_llm_deep_analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            source_evidence_id INTEGER,
            analysis_kind TEXT NOT NULL,
            llm_v6_fit REAL,
            llm_dimensions_11 TEXT NOT NULL DEFAULT '{}',
            method TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            confidence REAL,
            source_cache_id INTEGER,
            status TEXT NOT NULL DEFAULT 'ready',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_kol_llm_deep_results_pool
            ON vkpi_kol_llm_deep_analysis_results(kol_pool_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_kol_pool_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            profile_url TEXT DEFAULT '',
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(platform, handle)
        );

        CREATE TABLE IF NOT EXISTS vkpi_kol_pool_brand_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            evidence_url TEXT DEFAULT '',
            observed_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_kol_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            embedding_model TEXT NOT NULL DEFAULT '',
            embedding_vector_json TEXT NOT NULL DEFAULT '[]',
            embedding_text_hash TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'not_configured',
            created_at TEXT NOT NULL,
            UNIQUE(kol_pool_id, embedding_model)
        );

        CREATE TABLE IF NOT EXISTS vkpi_content_pillars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pillar_uid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_kol_recommendation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uid TEXT NOT NULL UNIQUE,
            launch_id INTEGER,
            strategy_version TEXT NOT NULL DEFAULT 'rule_v0',
            status TEXT NOT NULL DEFAULT 'completed',
            candidate_count INTEGER NOT NULL DEFAULT 0,
            recommendation_count INTEGER NOT NULL DEFAULT 0,
            filters_json TEXT NOT NULL DEFAULT '{}',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vkpi_kol_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_uid TEXT NOT NULL UNIQUE,
            run_id INTEGER NOT NULL,
            launch_id INTEGER,
            kol_pool_id INTEGER,
            linked_main_kol_id INTEGER,
            platform TEXT DEFAULT '',
            handle TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            score REAL,
            rank INTEGER,
            status TEXT NOT NULL DEFAULT 'recommended',
            feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
            scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
            explanation_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_recommendations_run_rank
            ON vkpi_kol_recommendations(run_id, rank);

        CREATE TABLE IF NOT EXISTS vkpi_recommendation_explanations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            explanation_type TEXT NOT NULL DEFAULT 'rule',
            explanation_text TEXT DEFAULT '',
            strengths_json TEXT NOT NULL DEFAULT '[]',
            concerns_json TEXT NOT NULL DEFAULT '[]',
            model_version TEXT DEFAULT 'rule_v0',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_recommendation_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            feedback_type TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_uid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            project_type TEXT NOT NULL DEFAULT 'brand_monitor',
            linked_launch_id INTEGER,
            linked_campaign_id INTEGER,
            monitoring_frequency TEXT NOT NULL DEFAULT 'daily',
            auto_archive_days INTEGER,
            report_subscriptions_json TEXT NOT NULL DEFAULT '{}',
            owner_staff_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_uid TEXT NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            platform_user_id TEXT DEFAULT '',
            handle TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            is_verified INTEGER NOT NULL DEFAULT 0,
            brand_group TEXT DEFAULT '',
            account_role TEXT NOT NULL DEFAULT 'reference',
            region TEXT DEFAULT '',
            category TEXT DEFAULT '',
            linked_kol_pool_id INTEGER,
            auto_kol_link INTEGER NOT NULL DEFAULT 0,
            crawl_enabled INTEGER NOT NULL DEFAULT 0,
            crawl_frequency TEXT NOT NULL DEFAULT 'daily',
            last_crawled_at TEXT,
            last_successful_at TEXT,
            crawl_error_count INTEGER NOT NULL DEFAULT 0,
            sync_status TEXT NOT NULL DEFAULT 'not_configured',
            notes TEXT DEFAULT '',
            added_by_staff_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            discovered_at TEXT NOT NULL,
            UNIQUE(project_id, platform, handle)
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            followers INTEGER,
            followers_growth_24h INTEGER,
            followers_growth_30d INTEGER,
            followers_growth_pct_30d REAL,
            posts INTEGER,
            posts_30d INTEGER,
            avg_posts_per_day REAL,
            views INTEGER,
            views_30d INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            saves INTEGER,
            engagement_total_30d INTEGER,
            engagement_rate REAL,
            avg_engagement_rate_by_followers REAL,
            avg_engagement_per_day REAL,
            avg_eng_rate_by_views REAL,
            avg_eng_rate_by_impressions REAL,
            avg_eng_rate_by_reach REAL,
            avg_views INTEGER,
            reach_total_30d INTEGER,
            impressions_total_30d INTEGER,
            reels_views_30d INTEGER,
            top_post_views INTEGER,
            day_with_most_posts TEXT DEFAULT '',
            hour_with_most_posts TEXT DEFAULT '',
            day_with_highest_engagement TEXT DEFAULT '',
            hour_with_highest_engagement TEXT DEFAULT '',
            avg_hashtags_per_post REAL,
            avg_video_duration_seconds REAL,
            estimated_organic_value_cents INTEGER DEFAULT 0,
            vkpi_attributed_gmv_cents INTEGER DEFAULT 0,
            vkpi_attributed_orders INTEGER DEFAULT 0,
            vkpi_linked_kol_count INTEGER DEFAULT 0,
            vkpi_project_count INTEGER DEFAULT 0,
            youtube_kpi_status TEXT NOT NULL DEFAULT 'reserved',
            youtube_kpi_source_ref TEXT DEFAULT '',
            youtube_kpi_updated_at TEXT,
            youtube_kpi_json TEXT NOT NULL DEFAULT '{}',
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(account_id, snapshot_date)
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_uid TEXT NOT NULL UNIQUE,
            account_id INTEGER,
            platform TEXT NOT NULL,
            platform_post_id TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            video_url TEXT DEFAULT '',
            media_type TEXT DEFAULT '',
            duration_seconds INTEGER,
            video_source TEXT DEFAULT '',
            title TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            published_at TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            saves INTEGER,
            hashtags_json TEXT NOT NULL DEFAULT '[]',
            mentions_json TEXT NOT NULL DEFAULT '[]',
            detected_products_json TEXT NOT NULL DEFAULT '[]',
            content_pillar TEXT DEFAULT '',
            sentiment TEXT DEFAULT '',
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_post_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            saves INTEGER,
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(post_id, snapshot_date)
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_post_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            tag_type TEXT NOT NULL,
            tag_value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_industry_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid TEXT NOT NULL UNIQUE,
            project_id INTEGER,
            report_type TEXT NOT NULL DEFAULT 'industry',
            status TEXT NOT NULL DEFAULT 'queued',
            file_path TEXT DEFAULT '',
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vkpi_recommendation_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER,
            kol_pool_id INTEGER,
            launch_id INTEGER,
            was_shortlisted INTEGER NOT NULL DEFAULT 0,
            shortlisted_at TEXT,
            was_rejected INTEGER NOT NULL DEFAULT 0,
            rejected_at TEXT,
            reject_reason TEXT DEFAULT '',
            was_claimed INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            project_created INTEGER NOT NULL DEFAULT 0,
            project_created_at TEXT,
            outreach_sent INTEGER NOT NULL DEFAULT 0,
            outreach_sent_at TEXT,
            reply_received INTEGER NOT NULL DEFAULT 0,
            reply_at TEXT,
            reply_sentiment TEXT DEFAULT '',
            agreement_reached INTEGER NOT NULL DEFAULT 0,
            agreement_at TEXT,
            content_published INTEGER NOT NULL DEFAULT 0,
            content_published_at TEXT,
            content_url TEXT DEFAULT '',
            order_attributed INTEGER NOT NULL DEFAULT 0,
            first_order_at TEXT,
            attributed_clicks INTEGER NOT NULL DEFAULT 0,
            attributed_orders INTEGER NOT NULL DEFAULT 0,
            attributed_gmv_cents INTEGER NOT NULL DEFAULT 0,
            attributed_cost_cents INTEGER NOT NULL DEFAULT 0,
            computed_roi REAL,
            recommended_at TEXT NOT NULL,
            first_action_at TEXT,
            outcome_finalized_at TEXT,
            feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
            scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
            model_version TEXT DEFAULT 'rule_v0',
            display_position INTEGER,
            display_context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_scoring_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_uid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            variant_a_strategy TEXT NOT NULL DEFAULT 'rule_v0',
            variant_b_strategy TEXT NOT NULL DEFAULT 'rule_v0',
            traffic_split REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            start_at TEXT,
            end_at TEXT,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_recommendation_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL UNIQUE,
            experiment_id INTEGER,
            variant TEXT NOT NULL DEFAULT 'a',
            assigned_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_uid TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            purpose TEXT NOT NULL,
            prompt_hash TEXT DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            cost_micro_usd INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER,
            status TEXT NOT NULL DEFAULT 'not_configured',
            fallback_used INTEGER NOT NULL DEFAULT 0,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_model_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL UNIQUE,
            model_type TEXT NOT NULL DEFAULT 'rule',
            status TEXT NOT NULL DEFAULT 'registered',
            activated_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_feature_flags (
            flag_key TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            description TEXT DEFAULT '',
            updated_by_staff_id INTEGER,
            updated_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_platform_crawl_settings (
            platform TEXT PRIMARY KEY,
            crawl_enabled INTEGER NOT NULL DEFAULT 0,
            daily_account_limit INTEGER NOT NULL DEFAULT 0,
            posts_per_account INTEGER NOT NULL DEFAULT 0,
            crawl_comments INTEGER NOT NULL DEFAULT 0,
            crawl_followers INTEGER NOT NULL DEFAULT 0,
            crawl_audience_graph INTEGER NOT NULL DEFAULT 0,
            only_uncontacted_kols INTEGER NOT NULL DEFAULT 1,
            include_company_accounts INTEGER NOT NULL DEFAULT 1,
            include_competitor_accounts INTEGER NOT NULL DEFAULT 1,
            include_candidate_kols INTEGER NOT NULL DEFAULT 1,
            monthly_budget_usd REAL NOT NULL DEFAULT 0,
            failure_threshold INTEGER NOT NULL DEFAULT 5,
            last_test_status TEXT NOT NULL DEFAULT 'not_configured',
            last_test_at TEXT,
            updated_by_staff_id INTEGER,
            updated_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_budget_settings (
            budget_key TEXT PRIMARY KEY,
            monthly_limit_usd REAL NOT NULL DEFAULT 0,
            current_month_spent REAL NOT NULL DEFAULT 0,
            alert_threshold_pct INTEGER NOT NULL DEFAULT 80,
            enabled INTEGER NOT NULL DEFAULT 0,
            updated_by_staff_id INTEGER,
            updated_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_training_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_uid TEXT NOT NULL UNIQUE,
            date_from TEXT DEFAULT '',
            date_to TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            row_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS vkpi_behavior_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_staff_id INTEGER,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}',
            occurred_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO vkpi_model_registry
            (model_version, model_type, status, created_at, metadata_json)
        VALUES ('rule_v0', 'rule', 'active', datetime('now'), '{}');
        """
    )
    pool_columns = {
        str(row["name"] if "name" in row.keys() else row[1])
        for row in conn.execute("PRAGMA table_info(vkpi_kol_pool)").fetchall()
    }
    for column_name, column_ddl in {
        "contact_source": "TEXT NOT NULL DEFAULT ''",
        "duplicate_of_id": "INTEGER",
        "last_video_at": "TEXT",
    }.items():
        if column_name not in pool_columns:
            conn.execute(f"ALTER TABLE vkpi_kol_pool ADD COLUMN {column_name} {column_ddl}")
    conn.commit()
    _SCHEMA_READY = True
