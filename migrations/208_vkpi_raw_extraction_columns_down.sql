-- 208 回滚:去掉 C6 零新抓提列批加的列(列值全部可由 raw 重提,回滚零数据损失)。
BEGIN;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS is_verified;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS is_tt_seller;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS is_commerce_user;
ALTER TABLE vkpi_comments DROP COLUMN IF EXISTS author_avatar_url;
ALTER TABLE vkpi_kol_video_evidence DROP COLUMN IF EXISTS media_kind;
COMMIT;
