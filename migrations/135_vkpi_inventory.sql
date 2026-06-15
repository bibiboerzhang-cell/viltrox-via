-- 135: V-KPI 公司库存表 (Company Inventory) — 把原本纯前端 mock(stock.js INITIAL_STOCK)
-- 升级成真·共享后端表。跨 Event 共享的样品库 / 量产库存 / 配件 / 设备。
-- sku 唯一(前端用 sku 做 PATCH/DELETE 的资源键);created_by_staff_id 记创建者(谁建的)。
-- 与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。

CREATE TABLE IF NOT EXISTS vkpi_inventory (
    id VARCHAR(64) PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'lens',
    qty INTEGER NOT NULL DEFAULT 0,
    location TEXT DEFAULT '',
    note TEXT DEFAULT '',
    is_sample BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_category ON vkpi_inventory(category);
CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_is_sample ON vkpi_inventory(is_sample);

COMMENT ON TABLE vkpi_inventory IS 'V-KPI Company Inventory - shared cross-event stock (samples/production/accessories/equipment), sku-keyed';

-- 种子:stock.js 现有 14 项,迁移后 demo 数据不丢。created_by_staff_id 留 NULL(系统初始导入)。
-- ON CONFLICT DO NOTHING:幂等,重复跑迁移不报错、不覆盖已被人改过的行。
INSERT INTO vkpi_inventory (id, sku, name, category, qty, location, note, is_sample) VALUES
  ('s_135lab_sample', 'VTX-135-LAB-S',  'Viltrox 135mm LAB',     'lens',      5,   '深圳样品仓',   '首批量产前样品 · 已通过 QA', TRUE),
  ('s_27t2_sample',   'VTX-27-T2-S',    'Viltrox 27mm T2 Cine',  'lens',      4,   '深圳样品仓',   'Cine 系列样品',              TRUE),
  ('s_16pro_sample',  'VTX-16-PRO-S',   'Viltrox 16mm F1.8 Pro', 'lens',      2,   '深圳样品仓',   '新品 · 预发布样机',          TRUE),
  ('s_85pro',         'VTX-85-PRO',     'Viltrox 85mm F1.8 Pro', 'lens',      200, '深圳量产仓',   '量产现货',                   FALSE),
  ('s_56pro',         'VTX-56-PRO',     'Viltrox 56mm F1.4 Pro', 'lens',      0,   '深圳量产仓',   '缺货 · 工厂排产中',          FALSE),
  ('s_75lab',         'VTX-75-LAB',     'Viltrox 75mm F1.4 LAB', 'lens',      80,  '深圳量产仓',   '量产现货',                   FALSE),
  ('s_24pro',         'VTX-24-PRO',     'Viltrox 24mm F1.8 Pro', 'lens',      150, '深圳量产仓',   '量产现货',                   FALSE),
  ('s_filter',        'ACC-FILTER',     '镜头滤镜套装 (ND/CPL)',  'accessory', 8,   '深圳样品仓',   '用于现场演示',               FALSE),
  ('s_lenscap',       'ACC-CAP',        '镜头帽 (周边)',          'accessory', 300, '深圳量产仓',   '礼品 / 备用',                FALSE),
  ('s_usb_kit',       'ACC-USBC',       'USB-C 转接套装',         'accessory', 15,  '美国仓 (LA)',  '演示用 · LA 仓直发',         FALSE),
  ('s_strap',         'ACC-STRAP',      '镜头肩带 (品牌)',        'accessory', 50,  '深圳量产仓',   '礼品',                       FALSE),
  ('s_pelican',       'EQ-PELICAN-1620','Pelican 大箱 (1620)',    'equipment', 6,   '深圳运输仓',   '用于跨境运输',               FALSE),
  ('s_pelican_small', 'EQ-PELICAN-1510','Pelican 中箱 (1510)',    'equipment', 8,   '深圳运输仓',   '',                           FALSE),
  ('s_banner',        'EQ-BANNER',      '展位 KV 海报 (备用)',    'equipment', 2,   '美国仓 (LA)',  '通用版,无 event 特定信息',  FALSE)
ON CONFLICT (sku) DO NOTHING;
