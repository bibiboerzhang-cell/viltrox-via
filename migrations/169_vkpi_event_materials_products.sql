-- 169(Events 物料/产品落库):活动逐项「营销物料」+「产品准备」清单落库,替换前端 mock
-- (MATERIALS_DATA / PRODUCT_PREP_DATA,刷新即丢)。镜像 122 的 event 子资源风格:
-- VARCHAR id(前端 m_/pp_ 串)/ event_id FK ON DELETE CASCADE / 按 event 索引。
-- additive/幂等;与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。

CREATE TABLE IF NOT EXISTS vkpi_event_materials (
    id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'display',
    source TEXT DEFAULT 'ship',
    qty INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    owner TEXT DEFAULT '',
    note TEXT DEFAULT '',
    tracking_no TEXT DEFAULT '',
    file_url TEXT DEFAULT '',
    alert TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_materials_event ON vkpi_event_materials(event_id, created_at ASC);

CREATE TABLE IF NOT EXISTS vkpi_event_products (
    id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'lens',
    source TEXT DEFAULT 'new_purchase',
    qty INTEGER DEFAULT 1,
    status TEXT DEFAULT 'ordered',
    owner TEXT DEFAULT '',
    note TEXT DEFAULT '',
    tracking_no TEXT DEFAULT '',
    arrive_by TEXT DEFAULT '',
    return_after BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_products_event ON vkpi_event_products(event_id, created_at ASC);

COMMENT ON TABLE vkpi_event_materials IS '活动逐项营销物料清单(替换前端 MATERIALS_DATA mock;展会物料/礼品/媒体kit;按 event scope)';
COMMENT ON TABLE vkpi_event_products IS '活动逐项产品准备清单(替换前端 PRODUCT_PREP_DATA mock;镜头/设备 已有vs需新寄)';
