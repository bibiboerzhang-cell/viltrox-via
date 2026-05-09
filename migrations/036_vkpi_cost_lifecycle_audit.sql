-- V-KPI v3 cost ledger lifecycle + business audit.

ALTER TABLE vkpi_cost_ledger
    ADD COLUMN IF NOT EXISTS approved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL;
ALTER TABLE vkpi_cost_ledger
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE vkpi_cost_ledger
    ADD COLUMN IF NOT EXISTS voided_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL;
ALTER TABLE vkpi_cost_ledger
    ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ;
ALTER TABLE vkpi_cost_ledger
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_vkpi_cost_ledger_status_time
    ON vkpi_cost_ledger(status, incurred_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_business_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_business_audit_staff_time
    ON vkpi_business_audit_logs(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_business_audit_target
    ON vkpi_business_audit_logs(target_type, target_id, created_at DESC);
