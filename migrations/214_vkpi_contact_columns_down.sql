-- 214 回滚:删 vkpi_kol_pool 联系方式 4 列(渠道快照/最近验证/可联系性分/来源汇总)。
-- 注意:4 列全是派生聚合数据(源头在 vkpi_kol_pool_contacts 审计表 + email/other_contacts_json),
--   回滚无不可逆损失,重跑 scripts/backfill_apify_raw.py 的 contact_snapshot 阶段即可重建。
-- contact_system.py 在列缺失时诚实降级(refresh 返回明确错误码,不炸接口)。
BEGIN;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_channels;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_last_verified_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contactability_score;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS contact_sources;
COMMIT;
