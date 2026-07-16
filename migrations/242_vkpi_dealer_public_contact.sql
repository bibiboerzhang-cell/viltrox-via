-- 242: Dealer 公开门店联系信息。只保存零售商官网公开字段，便于人工外联核验。

ALTER TABLE vkpi_dealers
  ADD COLUMN IF NOT EXISTS postal_code TEXT,
  ADD COLUMN IF NOT EXISTS phone TEXT,
  ADD COLUMN IF NOT EXISTS contact_email TEXT,
  ADD COLUMN IF NOT EXISTS store_hours TEXT,
  ADD COLUMN IF NOT EXISTS public_services TEXT;

COMMENT ON COLUMN vkpi_dealers.store_hours IS '零售商官网核验时展示的常规营业时间，可能变化，须结合 source_checked_at 使用。';
COMMENT ON COLUMN vkpi_dealers.public_services IS '零售商官网公开的门店服务摘要；不代表 Viltrox 授权范围。';
