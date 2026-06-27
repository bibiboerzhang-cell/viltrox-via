-- 086_vkpi_kol_pool_video_summary.sql
-- Cache video evidence summary on vkpi_kol_pool and expose Dashboard summary view.

BEGIN;

ALTER TABLE vkpi_kol_pool
  ADD COLUMN IF NOT EXISTS has_video_evidence BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS video_evidence_count INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS first_video_at DATE,
  ADD COLUMN IF NOT EXISTS last_video_at DATE,
  ADD COLUMN IF NOT EXISTS needs_scrape BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS scrape_status VARCHAR(20),
  ADD COLUMN IF NOT EXISTS last_scrape_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_pool_active_roster
  ON vkpi_kol_pool(dashboard_account_type, has_video_evidence)
  WHERE has_video_evidence = TRUE;

CREATE OR REPLACE FUNCTION sync_kol_video_summary() RETURNS TRIGGER AS $$
DECLARE
  target_kol_pool_id BIGINT;
BEGIN
  FOR target_kol_pool_id IN
    SELECT DISTINCT kol_pool_id
    FROM (
      SELECT OLD.kol_pool_id
      WHERE TG_OP IN ('UPDATE', 'DELETE')
      UNION ALL
      SELECT NEW.kol_pool_id
      WHERE TG_OP IN ('INSERT', 'UPDATE')
    ) changed(kol_pool_id)
    WHERE kol_pool_id IS NOT NULL
  LOOP
    UPDATE vkpi_kol_pool
       SET has_video_evidence = EXISTS (
             SELECT 1
             FROM vkpi_kol_video_evidence
             WHERE kol_pool_id = target_kol_pool_id
               AND is_active = TRUE
           ),
           video_evidence_count = (
             SELECT COUNT(*)
             FROM vkpi_kol_video_evidence
             WHERE kol_pool_id = target_kol_pool_id
               AND is_active = TRUE
           ),
           first_video_at = (
             SELECT MIN(posted_at)
             FROM vkpi_kol_video_evidence
             WHERE kol_pool_id = target_kol_pool_id
               AND is_active = TRUE
           ),
           last_video_at = (
             SELECT MAX(posted_at)
             FROM vkpi_kol_video_evidence
             WHERE kol_pool_id = target_kol_pool_id
               AND is_active = TRUE
           ),
           updated_at = NOW()
     WHERE id = target_kol_pool_id;
  END LOOP;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_kol_video_summary ON vkpi_kol_video_evidence;

CREATE TRIGGER trg_sync_kol_video_summary
AFTER INSERT OR UPDATE OR DELETE ON vkpi_kol_video_evidence
FOR EACH ROW EXECUTE FUNCTION sync_kol_video_summary();

-- 幂等修复:同 083,先 DROP 再 CREATE(无下游依赖,已核),避免改列集时报 cannot drop columns from view。
DROP VIEW IF EXISTS v_dashboard_kol_summary CASCADE;
CREATE VIEW v_dashboard_kol_summary AS
SELECT
  p.id,
  p.handle,
  p.display_name,
  p.platform,
  p.country,
  p.followers,
  p.dashboard_account_type AS account_type,
  p.dashboard_tier AS tier,
  p.dashboard_latitude AS latitude,
  p.dashboard_longitude AS longitude,
  p.has_video_evidence,
  p.video_evidence_count,
  p.first_video_at,
  p.last_video_at,
  COALESCE(pka.project_count, 0) AS project_count,
  COALESCE(pka.published_count, 0) AS published_count
FROM vkpi_kol_pool p
LEFT JOIN (
  SELECT
    kol_pool_id,
    COUNT(DISTINCT project_id) AS project_count,
    COUNT(DISTINCT project_id) FILTER (
      WHERE stage IN ('content_posted', 'reviewed')
    ) AS published_count
  FROM vkpi_project_kol_assignments
  GROUP BY kol_pool_id
) pka ON pka.kol_pool_id = p.id;

COMMENT ON VIEW v_dashboard_kol_summary IS
  'Dashboard KOL summary with video evidence and Project counts.';

COMMIT;
