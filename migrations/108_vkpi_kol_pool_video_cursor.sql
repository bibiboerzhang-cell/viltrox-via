-- 108: 真 since 游标列(E6 spec;date 粒度老坑根治 → timestamptz)
-- init 时刻=当日 00:00:00 UTC(裁决修正:日终 init 会把同日漏网视频永久封死在游标后;
-- 日初 init 首轮至多重拉当天已知视频,URL 去重吸收,廉价保险 vs 永久盲区)。
-- 旧列 last_video_at 保留只读。

ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS last_video_seen_at TIMESTAMPTZ;

UPDATE vkpi_kol_pool
SET last_video_seen_at = (last_video_at::timestamp) AT TIME ZONE 'UTC'
WHERE last_video_at IS NOT NULL AND last_video_seen_at IS NULL;
