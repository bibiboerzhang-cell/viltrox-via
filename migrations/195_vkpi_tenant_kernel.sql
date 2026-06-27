-- 195_vkpi_tenant_kernel.sql — Tenant Kernel(轴B 第一刀):多租户底座。
-- 显式租户:organizations + organization_members;默认 Viltrox=org 1,现有数据隐式归 1。
-- 行为不改:仅加表 + 解析层;核心表 organization_id 后续逐表加。注释零 ASCII 问号。零触 viltrox_fit_score。
BEGIN;
CREATE TABLE IF NOT EXISTS organizations (
    id                 BIGSERIAL PRIMARY KEY,
    name               TEXT        NOT NULL,
    slug               TEXT        NOT NULL,
    industry           TEXT        NOT NULL DEFAULT '',     -- camera_lens / outdoor_gear / ...
    brand_profile_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    scoring_template   TEXT        NOT NULL DEFAULT '',
    status             TEXT        NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (slug)
);

CREATE TABLE IF NOT EXISTS organization_members (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT      NOT NULL,
    staff_id        BIGINT      NOT NULL,
    role            TEXT        NOT NULL DEFAULT 'member',   -- owner / admin / member / viewer
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, staff_id)
);
CREATE INDEX IF NOT EXISTS idx_org_members_staff ON organization_members(staff_id);

-- 种子:Viltrox = org 1(camera_lens 模板);现有数据隐式归此租户。
INSERT INTO organizations (id, name, slug, industry, scoring_template, brand_profile_json) VALUES
    (1, 'Viltrox', 'viltrox', 'camera_lens', 'camera_gear_kol_v1',
     '{"products":"camera lenses","target_audience":"photographers, filmmakers, reviewers"}'::jsonb)
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('organizations','id'), GREATEST((SELECT MAX(id) FROM organizations), 1));
COMMIT;
