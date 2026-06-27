-- 192_vkpi_event_ledger.sql — 统一事件总线(P1,两轴共用底座)。
-- 所有关键事件写这里:让智能体靠"事件流"理解业务世界,而非查 100 张表。
-- 轴A:trace_id(可追溯每一步);轴B:organization_id(多租户就绪,默认 1=Viltrox)。
-- additive、幂等。注释零 ASCII 问号(避 compat 占位符陷阱)。红线:纯留痕,零触 viltrox_fit_score。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_event_ledger (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT      NOT NULL DEFAULT 1,
    event_type      TEXT        NOT NULL,
    entity_type     TEXT        NOT NULL DEFAULT '',
    entity_id       TEXT        NOT NULL DEFAULT '',
    actor_type      TEXT        NOT NULL DEFAULT 'system',   -- staff / agent / system
    actor_id        TEXT        NOT NULL DEFAULT '',
    source          TEXT        NOT NULL DEFAULT '',
    payload_json    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    trace_id        TEXT        NOT NULL DEFAULT '',
    confidence      DOUBLE PRECISION,
    provenance_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_ledger_entity ON vkpi_event_ledger(entity_type, entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_ledger_type   ON vkpi_event_ledger(event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_ledger_org    ON vkpi_event_ledger(organization_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_ledger_trace  ON vkpi_event_ledger(trace_id);

COMMENT ON TABLE vkpi_event_ledger IS
  '统一事件总线:关键业务事件流;trace_id 可追溯、organization_id 多租户就绪;零触 viltrox_fit_score';
COMMIT;
