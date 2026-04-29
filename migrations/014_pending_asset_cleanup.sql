ALTER TABLE submission_assets
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE submission_assets
    ADD COLUMN IF NOT EXISTS deleted_reason TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_pg_submission_assets_pending_cleanup
    ON submission_assets(created_at)
    WHERE submission_id = 0
      AND asset_role = 'uploaded_video_pending'
      AND deleted_at IS NULL;
