-- 206_vkpi_commenter_profiles_age_channel.sql — 身份缓存表加 年龄桶 + 频道白捡字段 列。
-- 背景:受众情报 v2 —— 年龄三路融合(Gemini 批推/M3 可选/频道注册年龄弱先验)出 age_bucket;
-- YouTube channels.list 同批 API 顺手带回 订阅数/视频数/频道创建时间/bio,零额外配额,
-- 由此支撑「受众创作者浓度」等新指标。additive、幂等(IF NOT EXISTS);注释零 ASCII 问号。
-- 红线:纯缓存列,绝不触 viltrox_fit_score、不碰 rule_v0。
BEGIN;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS age_bucket TEXT;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS age_conf REAL;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS subscriber_count BIGINT;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS video_count INT;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS channel_created_at TEXT;
ALTER TABLE vkpi_commenter_profiles ADD COLUMN IF NOT EXISTS bio TEXT;
COMMIT;
