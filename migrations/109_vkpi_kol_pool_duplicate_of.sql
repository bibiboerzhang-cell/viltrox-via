-- 109: 重复档案主从标记(裁决:4 对;主=有 FK 引用者,均无则低 id;列表默认滤从行=应用层)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS duplicate_of_id BIGINT REFERENCES vkpi_kol_pool(id);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_duplicate_of ON vkpi_kol_pool(duplicate_of_id) WHERE duplicate_of_id IS NOT NULL;
