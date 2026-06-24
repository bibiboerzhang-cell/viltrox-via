-- 189_vkpi_event_evidence.sql — F3 活动费用/证据元数据(发票/图片/合同/现场照)。
-- 只存元数据 + storage_ref(R2 key 或本地相对路径);真实二进制由既有 media 上传链处理,不入本表。
-- amount_cents 仅发票/收据类有意义,可空。additive、幂等、零触 viltrox_fit_score。
-- 注释不含 ASCII 问号(避免 compat 占位符陷阱);可空与否用中文表述。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_event_evidence (
    id            BIGSERIAL PRIMARY KEY,
    event_id      TEXT        NOT NULL,
    kind          TEXT        NOT NULL DEFAULT 'other',
    filename      TEXT,
    content_type  TEXT,
    size_bytes    BIGINT      NOT NULL DEFAULT 0,
    storage_ref   TEXT        NOT NULL,
    amount_cents  BIGINT,
    uploaded_by   BIGINT,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_evidence_event ON vkpi_event_evidence(event_id, created_at DESC);

COMMENT ON TABLE vkpi_event_evidence IS
  'F3 活动证据元数据;kind 取 invoice/photo/contract/receipt/other;storage_ref 指向 R2 或本地;二进制不入库';
COMMIT;
