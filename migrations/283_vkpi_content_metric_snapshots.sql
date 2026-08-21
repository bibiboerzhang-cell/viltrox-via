-- Append-only observation ledger for KOL content metrics.
--
-- The current metric columns on vkpi_kol_video_evidence remain a latest-value
-- read model.  This table is the observation truth layer: nullable means the
-- provider did not supply that metric, never zero.

CREATE TABLE IF NOT EXISTS vkpi_content_metric_snapshots (
    id BIGSERIAL PRIMARY KEY,
    evidence_id BIGINT NOT NULL REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
    capture_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT '',
    source_observed_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    shares BIGINT,
    status TEXT NOT NULL,
    error_code TEXT,
    run_id TEXT,
    quality_flags TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('success', 'failed', 'legacy_current_only')),
    CHECK (views IS NULL OR views >= 0),
    CHECK (likes IS NULL OR likes >= 0),
    CHECK (comments IS NULL OR comments >= 0),
    CHECK (shares IS NULL OR shares >= 0)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_content_metric_snapshots_evidence_time
    ON vkpi_content_metric_snapshots(evidence_id, fetched_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_content_metric_snapshots_status_time
    ON vkpi_content_metric_snapshots(status, fetched_at DESC);

-- This is deliberately not a historical reconstruction.  It preserves one
-- explicitly labelled baseline row for a legacy latest-value record only.
INSERT INTO vkpi_content_metric_snapshots (
    evidence_id,
    capture_key,
    provider,
    source_observed_at,
    fetched_at,
    views,
    likes,
    comments,
    shares,
    status,
    error_code,
    run_id,
    quality_flags
)
SELECT
    e.id,
    'legacy_current_only:' || e.id::text,
    'legacy_current_columns',
    e.metrics_scraped_at,
    COALESCE(
        e.metrics_scraped_at,
        CAST('1970-01-01T00:00:00+00:00' AS TIMESTAMPTZ)
    ),
    e.view_count,
    e.like_count,
    e.comment_count,
    e.share_count,
    'legacy_current_only',
    NULL,
    NULL,
    CASE
        WHEN e.metrics_scraped_at IS NULL THEN
            '["legacy_current_only","not_historical","provenance_legacy_current_columns","source_observed_at_unknown"]'
        ELSE
            '["legacy_current_only","not_historical","provenance_legacy_current_columns"]'
    END
FROM vkpi_kol_video_evidence e
WHERE COALESCE(e.evidence_type, 'video') = 'video'
  AND (
      e.view_count IS NOT NULL
      OR e.like_count IS NOT NULL
      OR e.comment_count IS NOT NULL
      OR e.share_count IS NOT NULL
  )
ON CONFLICT (capture_key) DO NOTHING;
