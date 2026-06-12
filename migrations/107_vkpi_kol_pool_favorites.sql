-- 107: KOL Pool 收藏持久层(四环漏斗 C1,战役计划第一段;闸B 三段式)
-- 设计定稿(四环 survey 裁决):不复用 promote/linked_main_kol_id(语义=入役主表),
-- 不复用 vkpi_kol_claims(FK 挂 kols(id),join 不到池)。现有表零改动。
-- 应用层语义:收藏 = staff 个人的"My KOL"归宿;C5 将 backfill 721 个在役 KOL。

CREATE TABLE IF NOT EXISTS vkpi_kol_pool_favorites (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, staff_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_favorites_staff
    ON vkpi_kol_pool_favorites(staff_id, created_at DESC);
