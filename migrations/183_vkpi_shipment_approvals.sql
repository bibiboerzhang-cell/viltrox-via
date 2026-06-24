-- 183_vkpi_shipment_approvals.sql — 发货审批门槛(管理员审核发货,联系后合同前防浪费)。
-- 区别于内容发布审批(173 vkpi_publish_approvals):这是"寄样发货"的人审闸,每个 (project,kol) 一条。
-- 正合红线:Agent/成员只能请求,真发货由管理员人审通过。additive、幂等、零触 viltrox_fit_score。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_shipment_approvals (
    id            BIGSERIAL PRIMARY KEY,
    project_id    BIGINT      NOT NULL,
    kol_pool_id   BIGINT      NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','approved','rejected')),
    requested_by  BIGINT,
    approved_by   BIGINT,
    approved_at   TIMESTAMPTZ,
    reason        TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, kol_pool_id)
);
CREATE INDEX IF NOT EXISTS idx_shipment_approvals_status ON vkpi_shipment_approvals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shipment_approvals_project ON vkpi_shipment_approvals(project_id);

COMMENT ON TABLE vkpi_shipment_approvals IS
    '发货审批门槛:寄样发货前管理员人审(pending/approved/rejected),每个 project+kol 一条,防随意发货浪费。';
COMMIT;
