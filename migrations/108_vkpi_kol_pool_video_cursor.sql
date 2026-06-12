-- 108: 真 since 游标列(E6 spec;date 粒度老坑根治 → timestamptz)
-- 初始化:既有 last_video_at(453 行非空)按当日 23:59:59 UTC 落座,旧列保留只读。

ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS last_video_seen_at TIMESTAMPTZ;

UPDATE vkpi_kol_pool
SET last_video_seen_at = (last_video_at::timestamp + INTERVAL '23 hours 59 minutes 59 seconds') AT TIME ZONE 'UTC'
WHERE last_video_at IS NOT NULL AND last_video_seen_at IS NULL;
