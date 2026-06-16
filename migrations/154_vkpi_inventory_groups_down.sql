-- 回滚 154:库存组团。
DROP INDEX IF EXISTS idx_vkpi_inv_groups_event;
DROP INDEX IF EXISTS idx_vkpi_inv_group_items_sku;
DROP INDEX IF EXISTS idx_vkpi_inv_group_items_group;
DROP TABLE IF EXISTS vkpi_inventory_group_items;
DROP TABLE IF EXISTS vkpi_inventory_groups;
