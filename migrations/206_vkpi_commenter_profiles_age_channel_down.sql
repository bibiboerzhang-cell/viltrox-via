-- 206 回滚:去掉身份缓存表的 年龄桶 + 频道白捡字段 列(纯缓存,可随时重建)。
BEGIN;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS age_bucket;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS age_conf;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS subscriber_count;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS video_count;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS channel_created_at;
ALTER TABLE vkpi_commenter_profiles DROP COLUMN IF EXISTS bio;
COMMIT;
