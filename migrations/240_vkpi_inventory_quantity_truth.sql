-- 240: 库存数量真实性口径。
-- 产品目录行和历史 demo 种子不再被当成仓库实盘；只有人工确认或外部来源确认的
-- 数量，才允许进入缺货提醒。真实 WMS/ERP/Shopify 库存接入后写 source_confirmed。

ALTER TABLE vkpi_inventory
  ADD COLUMN IF NOT EXISTS quantity_status TEXT NOT NULL DEFAULT 'unverified',
  ADD COLUMN IF NOT EXISTS quantity_source TEXT NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS quantity_verified_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_vkpi_inventory_quantity_status'
  ) THEN
    ALTER TABLE vkpi_inventory
      ADD CONSTRAINT chk_vkpi_inventory_quantity_status
      CHECK (quantity_status IN ('unverified', 'manual_confirmed', 'source_confirmed'));
  END IF;
END $$;

UPDATE vkpi_inventory
SET quantity_status = 'unverified',
    quantity_source = CASE
      WHEN note LIKE '从产品库导入%' THEN 'catalog_reference'
      WHEN id IN (
        's_135lab_sample','s_27t2_sample','s_16pro_sample','s_85pro','s_56pro',
        's_75lab','s_24pro','s_filter','s_lenscap','s_usb_kit','s_strap',
        's_pelican','s_pelican_small','s_banner'
      ) THEN 'legacy_demo_seed'
      ELSE 'legacy_unverified'
    END,
    quantity_verified_at = NULL
WHERE quantity_status = 'unverified';

-- 清掉此前由未核验数量生成的误导性补货建议；保留行和原因，形成可审计终态。
UPDATE vkpi_action_inbox AS a
SET status = 'dismissed',
    updated_at = NOW(),
    result_checklist_json = COALESCE(a.result_checklist_json, '{}'::jsonb) ||
      jsonb_build_object(
        'outcome', 'dismissed_unverified_inventory',
        'truth_contract', 'inventory_quantity_v1',
        'dismissed_at', NOW()
      )
FROM vkpi_inventory AS i
WHERE a.category = 'inventory_low'
  AND a.entity_id = i.id
  AND a.status IN ('suggested', 'approved', 'snoozed')
  AND i.quantity_status = 'unverified';

CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_quantity_status
  ON vkpi_inventory(quantity_status);

COMMENT ON COLUMN vkpi_inventory.quantity_status IS
  'unverified=目录/历史占位，不可用于库存告警；manual_confirmed=人工调量确认；source_confirmed=外部库存源确认。';
COMMENT ON COLUMN vkpi_inventory.quantity_source IS
  '数量来源标识；后续 WMS/ERP/Shopify 库存接入在此登记来源。';
