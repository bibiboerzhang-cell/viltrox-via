-- 154 库存组团(group)—— 把库存项逻辑分组(如「某个 Pelican 箱里装了哪些」),用于成组发货/追踪。
-- 语义:逻辑清单,**不扣各项独立库存**(qty_in_group 仅记该组装了几件,与 vkpi_inventory.qty 无扣减关系)。
-- 同一 SKU 可进多个组;组删除时 items 级联删(组本身就没了);inventory_sku 软引用(不加 FK,库存项删了组里残留也无害)。
-- 红线:全新表,既有 vkpi_inventory / movements 零改动;绝不碰 viltrox_fit_score / rule_v0 / vkpi_kol_pool。
-- 回滚见 154_vkpi_inventory_groups_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_inventory_groups (
    id                  TEXT        PRIMARY KEY,
    name                TEXT        NOT NULL,
    note                TEXT        DEFAULT '',
    location            TEXT        DEFAULT '',
    event_id            TEXT,
    created_by_staff_id INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vkpi_inventory_group_items (
    id            TEXT        PRIMARY KEY,
    group_id      TEXT        NOT NULL REFERENCES vkpi_inventory_groups(id) ON DELETE CASCADE,
    inventory_sku TEXT        NOT NULL,
    qty_in_group  INTEGER     NOT NULL DEFAULT 1,
    note          TEXT        DEFAULT '',
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_inv_group_items_group ON vkpi_inventory_group_items(group_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_inv_group_items_sku   ON vkpi_inventory_group_items(inventory_sku);
CREATE INDEX IF NOT EXISTS idx_vkpi_inv_groups_event      ON vkpi_inventory_groups(event_id);

COMMENT ON TABLE vkpi_inventory_groups IS
    '库存组团:把库存项逻辑分组(箱子/套装),用于成组发货追踪;不扣各项独立库存。';
COMMENT ON TABLE vkpi_inventory_group_items IS
    '组内成员:某组装了哪些 SKU × 几件(qty_in_group 纯统计,不与 inventory.qty 扣减)。';
