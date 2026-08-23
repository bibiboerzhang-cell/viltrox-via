-- 291: KOL 池 raw 字段提列(优化波 B · D 车道「零成本榨干已有 raw」)。
-- 背景:vkpi_kol_pool.raw_platform_data 里已抓回但从未入列的结构化资产:
--   TikTok authorMeta.verified / ttSeller / commerceUserInfo(商单标记,迁移 208 已建
--   is_verified / is_tt_seller / is_commerce_user 三列,本迁移复用不重建);
--   Instagram latestPosts[].taggedUsers / mentions(被标记品牌与提及账号);
--   TikTok detailedMentions / mentions(同上);
--   YouTube brandingSettings.channel.keywords + 视频 snippet.categoryId(频道主题;
--   隔离库抽样 200 行 raw 里零 topicDetails,解析器兼容 topicDetails 但当前靠 keywords 兜底)。
-- 本迁移只加列;存量由 scripts/ops/backfill_pool_raw_fields.py 幂等回填,
-- 增量在 pool_enrich.enrich_item 富化路径顺手带上。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符陷阱)。
-- 红线:纯提列,绝不触 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定。

-- 频道主题 / 商业类目(按平台结构化:{"source","topic_categories","topic_ids",
-- "keywords","video_category_ids","commerce_category","business_category"})
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS topic_details_json JSONB;

-- 被标记 / 被提及账号聚合([{"handle","name","verified","tagged","mentioned","count"}],
-- 按出现次数降序,最多 40 条;verified=TRUE 多为品牌官号)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS tagged_brands_json JSONB;

-- 提列账本:最近一次从 raw 提列的时间 + 解析器版本(回填脚本据此增量,
-- raw 未变且版本未升则跳过;NULL=从未提列)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS raw_fields_extracted_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS raw_fields_extractor_version TEXT;

COMMENT ON COLUMN vkpi_kol_pool.topic_details_json IS
    'Channel topic and commerce category extracted from raw_platform_data; derived, rebuildable';
COMMENT ON COLUMN vkpi_kol_pool.tagged_brands_json IS
    'Tagged and mentioned accounts aggregated from raw_platform_data posts; derived, rebuildable';
