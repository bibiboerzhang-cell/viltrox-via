-- 205_vkpi_commenter_profiles.sql — 评论者身份缓存表(受众画像 ensemble_v1 推断层)。
-- 背景:三平台受众画像从评论者抽样推断(国家/性别/语言);同一评论者会出现在多个 KOL
-- 的评论区,推断结果落本表做跨 KOL 复用缓存,命中直接用,省重复推断与重复 API 查询。
-- author_key 口径:YouTube=authorChannelId;Instagram/TikTok=author_handle 小写。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯缓存表,绝不触 viltrox_fit_score、不碰 rule_v0。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_commenter_profiles (
    platform TEXT NOT NULL,
    author_key TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    country TEXT DEFAULT '',
    country_source TEXT DEFAULT '',
    country_conf REAL,
    gender TEXT DEFAULT '',
    gender_conf REAL,
    language TEXT DEFAULT '',
    updated_at TEXT,
    PRIMARY KEY (platform, author_key)
);
COMMIT;
