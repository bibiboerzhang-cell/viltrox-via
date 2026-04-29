-- ============================================================
-- Migration 004: Official Viltrox Matrix persistence
-- Date: 2026-04-14
-- ============================================================

CREATE TABLE IF NOT EXISTS viltrox_matrix_accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT NOT NULL,
    handle           TEXT NOT NULL,
    name             TEXT NOT NULL,
    source_key       TEXT NOT NULL DEFAULT 'official_matrix',
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(platform, handle)
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key           TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL DEFAULT 'completed',
    started_at        TEXT NOT NULL,
    completed_at      TEXT NOT NULL,
    total_accounts    INTEGER NOT NULL DEFAULT 0,
    scanned_accounts  INTEGER NOT NULL DEFAULT 0,
    total_posts       INTEGER NOT NULL DEFAULT 0,
    total_views       INTEGER NOT NULL DEFAULT 0,
    total_likes       INTEGER NOT NULL DEFAULT 0,
    total_comments    INTEGER NOT NULL DEFAULT 0,
    aggregate_json    TEXT NOT NULL DEFAULT '{}',
    error_message     TEXT DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_accounts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL,
    account_id        INTEGER NOT NULL,
    total_posts       INTEGER NOT NULL DEFAULT 0,
    total_views       INTEGER NOT NULL DEFAULT 0,
    total_likes       INTEGER NOT NULL DEFAULT 0,
    total_comments    INTEGER NOT NULL DEFAULT 0,
    duration_sec      REAL NOT NULL DEFAULT 0,
    error_message     TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    UNIQUE(run_id, account_id)
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_posts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL,
    account_id        INTEGER NOT NULL,
    title             TEXT DEFAULT '',
    post_url          TEXT DEFAULT '',
    thumbnail_url     TEXT DEFAULT '',
    views             INTEGER NOT NULL DEFAULT 0,
    likes             INTEGER NOT NULL DEFAULT 0,
    comments          INTEGER NOT NULL DEFAULT 0,
    shares            INTEGER NOT NULL DEFAULT 0,
    published_at      TEXT DEFAULT '',
    content_type      TEXT DEFAULT '',
    raw_json          TEXT DEFAULT '{}',
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vx_accounts_platform_active
    ON viltrox_matrix_accounts(platform, is_active, name);

CREATE INDEX IF NOT EXISTS idx_vx_runs_completed
    ON viltrox_matrix_scan_runs(completed_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_vx_scan_accounts_run_account
    ON viltrox_matrix_scan_accounts(run_id, account_id);

CREATE INDEX IF NOT EXISTS idx_vx_scan_posts_run_published
    ON viltrox_matrix_scan_posts(run_id, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_vx_scan_posts_account_published
    ON viltrox_matrix_scan_posts(account_id, published_at DESC);
