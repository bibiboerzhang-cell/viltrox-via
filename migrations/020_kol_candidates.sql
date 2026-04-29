-- KOL platform search candidate review queue.

CREATE TABLE IF NOT EXISTS kol_candidates (
    id BIGSERIAL PRIMARY KEY,
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
    reviewed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kol_candidates_status ON kol_candidates(status);
CREATE INDEX IF NOT EXISTS idx_kol_candidates_platform_market ON kol_candidates(platform, market);
CREATE INDEX IF NOT EXISTS idx_kol_candidates_query ON kol_candidates(search_query);
