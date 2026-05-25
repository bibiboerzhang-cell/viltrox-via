"""
Via learning/control table setup for app.db.migrations.
"""
from __future__ import annotations

from typing import Any


def create_via_tables(c: Any) -> None:
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
