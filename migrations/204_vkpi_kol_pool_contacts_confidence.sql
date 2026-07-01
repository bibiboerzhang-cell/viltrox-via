-- 204_vkpi_kol_pool_contacts_confidence.sql — 给联系方式表加 置信度/取证片段/首末见 列。
-- 背景:L0 多源抽取(raw/bio/描述/外链/视频文案)要给每条联系方式打置信度
-- (锚点行 0.9 / 裸 raw 0.55 / 外链站抓 0.8),留取证片段,记首末见时间,
-- 支撑「分层取证漏斗」与合规追溯。additive、幂等(IF NOT EXISTS)。
-- 注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯联系方式列,绝不触 viltrox_fit_score。
BEGIN;
ALTER TABLE vkpi_kol_pool_contacts ADD COLUMN IF NOT EXISTS confidence REAL;
ALTER TABLE vkpi_kol_pool_contacts ADD COLUMN IF NOT EXISTS evidence_text TEXT;
ALTER TABLE vkpi_kol_pool_contacts ADD COLUMN IF NOT EXISTS first_seen_at TEXT;
ALTER TABLE vkpi_kol_pool_contacts ADD COLUMN IF NOT EXISTS last_seen_at TEXT;
COMMIT;
