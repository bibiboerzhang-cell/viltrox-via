-- ============================================================
-- Migration 002: Intelligence System (B&H) + Cache 表
-- Date: 2026-04-09
-- ============================================================

-- ──────────────────────────────────────────────
-- B&H 商品快照表
-- 每次抓取产生一批新行 (snapshot_at 一致)
-- 历史保留, 用于价格趋势分析
-- ──────────────────────────────────────────────
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
);

CREATE INDEX IF NOT EXISTS idx_bh_snapshot 
    ON bh_products(snapshot_at);

CREATE INDEX IF NOT EXISTS idx_bh_sku 
    ON bh_products(sku) WHERE sku != '';

CREATE INDEX IF NOT EXISTS idx_bh_title 
    ON bh_products(title);

CREATE INDEX IF NOT EXISTS idx_bh_rating
    ON bh_products(rating DESC, review_count DESC);


-- ──────────────────────────────────────────────
-- 通用 KV 缓存表 (持久化的缓存, in-memory cache 死了用这个兜底)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS persistent_cache (
    cache_key       TEXT PRIMARY KEY,
    value_json      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pcache_expires
    ON persistent_cache(expires_at);


-- ──────────────────────────────────────────────
-- Rate limit 持久化 (可选, 跨重启)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rate_limit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket          TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    blocked_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rlog_blocked
    ON rate_limit_log(blocked_at);
