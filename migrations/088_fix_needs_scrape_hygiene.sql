-- Fix stale needs_scrape flags after evidence imports and keep future evidence
-- inserts/updates from leaving a KOL marked as pending scrape.

-- 1) Clear stale scrape flags for KOLs that now have evidence.
UPDATE vkpi_kol_pool
SET needs_scrape = FALSE
WHERE needs_scrape = TRUE
  AND has_video_evidence = TRUE;

-- 2) Backfill CSV-confirmed scrape candidates that are matched in pool but
-- currently have no evidence and no needs_scrape flag.
UPDATE vkpi_kol_pool
SET needs_scrape = TRUE
WHERE id IN (
  1546, -- digitalcameraworld - 【MEDIA】
  3873, -- fjhphoto
  1534, -- opticallimits - 【MEDIA】
  1527, -- sonyalpha.blog - 【MEDIA】
  1557, -- Yuu / Photo Journal PRESS - 【MEDIA】
  1535, -- Nikon-fotografie.de - 【MEDIA】
  3321, -- Photographylife-Jason Polak - 【MEDIA】
  3322, -- Thephoblographer-Feroz Khan - 【MEDIA】
  3310, -- 35mmc-Mike Brooks - 【MEDIA】
  3970, -- Eli Infante
  4044, -- 924PHOTOGRAPHY
  3337, -- Roman Fox - 【MEDIA】
  3733, -- jaysoundo
  3767, -- martinwongphoto
  3312, -- pcmag - 【MEDIA】
  -- Bucket D manual merge candidates.
  3639, -- MYeclecticstyle
  3960, -- Lisa and Axel - Dreamexplorers
  3695, -- Eli infante
  3648  -- AbdulrhMan
)
  AND has_video_evidence = FALSE;

-- 3) Keep vkpi_kol_pool evidence summary and needs_scrape hygiene in sync.
CREATE OR REPLACE FUNCTION sync_kol_video_summary() RETURNS TRIGGER AS $$
DECLARE
  target_kol_pool_id BIGINT;
BEGIN
  IF (TG_OP = 'DELETE') THEN
    target_kol_pool_id := OLD.kol_pool_id;
  ELSE
    target_kol_pool_id := NEW.kol_pool_id;
  END IF;

  UPDATE vkpi_kol_pool SET
    has_video_evidence = EXISTS (
      SELECT 1 FROM vkpi_kol_video_evidence
      WHERE kol_pool_id = target_kol_pool_id AND is_active = TRUE
    ),
    video_evidence_count = (
      SELECT COUNT(*) FROM vkpi_kol_video_evidence
      WHERE kol_pool_id = target_kol_pool_id AND is_active = TRUE
    ),
    first_video_at = (
      SELECT MIN(posted_at) FROM vkpi_kol_video_evidence
      WHERE kol_pool_id = target_kol_pool_id AND is_active = TRUE
    ),
    last_video_at = (
      SELECT MAX(posted_at) FROM vkpi_kol_video_evidence
      WHERE kol_pool_id = target_kol_pool_id AND is_active = TRUE
    ),
    needs_scrape = CASE
      WHEN EXISTS (
        SELECT 1 FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = target_kol_pool_id AND is_active = TRUE
      ) THEN FALSE
      ELSE needs_scrape
    END,
    updated_at = NOW()
  WHERE id = target_kol_pool_id;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
