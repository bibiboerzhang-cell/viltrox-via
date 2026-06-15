-- 136: V-KPI 库存调动审计账本 (Inventory Movement Ledger) — 每次对 vkpi_inventory 的变更
-- 都落一条不可篡改的流水:谁(moved_by_staff_id)/何时(created_at)/动了什么(action)/
-- 数量变化(delta_qty)/变化后(new_qty)/事由(reason)/关联活动(event_id)/额外上下文(metadata_json)。
-- 与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。
--
-- inventory_sku 故意不加外键:account ledger 要在库存项被删后仍可追溯历史(谁删的也是一条流水),
-- 加 FK ON DELETE CASCADE 会把删除前的历史一并抹掉,违背审计本意。用 sku 文本软引用。
-- event_id 可空(库存调动不一定绑定某个 event)。

CREATE TABLE IF NOT EXISTS vkpi_inventory_movements (
    id VARCHAR(64) PRIMARY KEY,
    inventory_sku TEXT NOT NULL,
    event_id VARCHAR(64) DEFAULT NULL,
    action TEXT NOT NULL DEFAULT 'adjust',  -- add / edit / delete / adjust
    delta_qty INTEGER DEFAULT NULL,
    new_qty INTEGER DEFAULT NULL,
    reason TEXT DEFAULT '',
    moved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    metadata_json JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_movements_sku ON vkpi_inventory_movements(inventory_sku, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_movements_staff ON vkpi_inventory_movements(moved_by_staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_movements_created ON vkpi_inventory_movements(created_at DESC);

COMMENT ON TABLE vkpi_inventory_movements IS 'V-KPI Inventory audit ledger - who/when/delta/event/reason for every stock mutation';
