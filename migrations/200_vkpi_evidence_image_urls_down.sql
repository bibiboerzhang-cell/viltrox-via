-- 200_vkpi_evidence_image_urls_down.sql — 回滚轮播图列表列。
BEGIN;
ALTER TABLE vkpi_kol_video_evidence DROP COLUMN IF EXISTS image_urls;
COMMIT;
