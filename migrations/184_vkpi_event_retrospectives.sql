-- 184_vkpi_event_retrospectives.sql — F4 活动复盘持久化(finalize 落库快照)。
-- aggregate_event_retrospective 是 compute-on-read;本表存"定格"的复盘快照(谁/何时定格 + 全量 JSON)。
-- additive、幂等、零触 viltrox_fit_score。注释不含 ASCII 问号(避免 compat 占位符陷阱)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_event_retrospectives (
    id             BIGSERIAL PRIMARY KEY,
    event_id       TEXT        NOT NULL,
    snapshot_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    completeness   DOUBLE PRECISION,
    finalized_by   BIGINT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_retro_event ON vkpi_event_retrospectives(event_id, created_at DESC);

COMMENT ON TABLE vkpi_event_retrospectives IS
  'F4 活动复盘定格快照;每次 finalize 追加一行;读最新一条即最近一次复盘';
COMMIT;
