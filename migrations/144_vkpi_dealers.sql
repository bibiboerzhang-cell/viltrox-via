-- 144: V-KPI 经销商地图(Dealer Map)落地表 —— 美国相机零售商地理数据源。
-- 有界、合规、不滥抓:dealer_scrape.scrape_dealers_enqueue() 单次 ≤20、带 sleep 限速、
-- 以 UNIQUE(name,address) 可重入幂等。纯地理数据,无 touches_v6_fit / 无评分需求。
-- 与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。
--
-- geocode:dealer_scrape 复用 city_coords.resolve_city() 把地址→经纬度;解析不到时
-- lat/lng 留空(NULL)待补,行仍然落库。GET /dealers/locations 只吐 lat/lng 齐全的行。

CREATE TABLE IF NOT EXISTS vkpi_dealers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT,
    state TEXT,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, address),
    -- 兜底 CHECK:经纬度必须在合法范围内(NULL 允许 = 待补 geocode)。
    CONSTRAINT vkpi_dealers_lat_range CHECK (lat IS NULL OR lat BETWEEN -90 AND 90),
    CONSTRAINT vkpi_dealers_lng_range CHECK (lng IS NULL OR lng BETWEEN -180 AND 180)
);

-- state 是列表/地图的常用过滤键;(lat,lng) 走 locations 端点的「齐全行」筛选。
CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_state ON vkpi_dealers(state);
CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_latlng ON vkpi_dealers(lat, lng);

COMMENT ON TABLE vkpi_dealers IS 'V-KPI dealer map source - US camera retailer locations, bounded/compliant scrape, idempotent on (name,address). lat/lng NULL = pending geocode. No v6_fit / scoring; pure geo.';
