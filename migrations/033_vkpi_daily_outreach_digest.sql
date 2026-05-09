-- V-KPI daily outreach digest.
-- One low-cost morning sync creates a per-staff list of uncontacted KOL content.

CREATE TABLE IF NOT EXISTS vkpi_staff_outreach_digests (
    id BIGSERIAL PRIMARY KEY,
    digest_uid TEXT NOT NULL UNIQUE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    digest_date DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    item_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(staff_id, digest_date)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_digest_staff_date
    ON vkpi_staff_outreach_digests(staff_id, digest_date DESC);

CREATE TABLE IF NOT EXISTS vkpi_staff_outreach_digest_items (
    id BIGSERIAL PRIMARY KEY,
    digest_id BIGINT NOT NULL REFERENCES vkpi_staff_outreach_digests(id) ON DELETE CASCADE,
    suggestion_id BIGINT NOT NULL REFERENCES vkpi_outreach_suggestions(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    quality_score NUMERIC(8,2) NOT NULL DEFAULT 0,
    relevance_reason TEXT NOT NULL DEFAULT '',
    buyer_profile TEXT NOT NULL DEFAULT '',
    viewer_profile TEXT NOT NULL DEFAULT '',
    content_angle TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(digest_id, suggestion_id)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_digest_items_digest_rank
    ON vkpi_staff_outreach_digest_items(digest_id, rank);
