-- 208_vkpi_raw_extraction_columns.sql — C6 零新抓提列批:raw 里已抓但列全空的资产提列。
-- 背景(docs/vkpi_Apify资产盘点_2026-07-02.md):
--   1. TT 档案 raw 的 authorMeta.verified / ttSeller / commerceUserInfo.commerceUser(K8 商单标记)躺 raw 未提列;
--   2. 三平台评论 raw 已带评论者头像 URL 但没有存储列(只存 URL,不下载文件);
--   3. 证据表只有粗粒度 evidence_type(video/image),区分不了轮播(carousel)。
-- 本迁移只加列;存量数据由 scripts/backfill_raw_extraction_0703.py 幂等回填,新数据在解析路径增量顺手带上。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯提列,绝不触 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定。
BEGIN;

-- K8 商单/认证标记(BOOLEAN 允许 NULL:NULL=raw 无该信号,与 FALSE=明确否 区分;
-- 仿 113 suspect_inflation 的可空 BOOLEAN 先例)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS is_verified BOOLEAN;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS is_tt_seller BOOLEAN;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS is_commerce_user BOOLEAN;

-- 评论者头像 URL(喂受众性别/年龄推断管线;来源=评论 raw_data_json 已抓字段)
ALTER TABLE vkpi_comments ADD COLUMN IF NOT EXISTS author_avatar_url TEXT;

-- 证据媒体种类:video / image / carousel(比 evidence_type 细一级;evidence_type 语义保持不动)
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS media_kind TEXT;

COMMIT;
