-- 114(2026-06-13,P0-4 触达历史回流):最薄触达事件表,键到 vkpi_kol_pool(id)。
-- 现有 kol_outreach FK 挂 kols(id) 接不到池;此表专记「池内候选 被谁/何时/经哪个项目/哪个渠道 触达」。
-- 号纠正:原稿用 113 撞 P0-3(113_vkpi_kol_pool_suspect_inflation),当前最大=113,故用 114。
-- channel: 'project_assignment'(加入项目)| 'outreach_draft' | 'manual' 等;UNIQUE 幂等去抖。
-- 设计=旁路审计,绝不进评分;rule_v0/viltrox_fit_score 全程冻结。
-- down: DROP TABLE IF EXISTS vkpi_kol_pool_touches;
CREATE TABLE IF NOT EXISTS vkpi_kol_pool_touches (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'manual',
    project_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    touched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, channel, project_id)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_touches_pool
    ON vkpi_kol_pool_touches(kol_pool_id, touched_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_touches_staff
    ON vkpi_kol_pool_touches(staff_id, touched_at DESC);