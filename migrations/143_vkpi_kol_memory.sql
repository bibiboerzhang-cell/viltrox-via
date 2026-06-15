-- 143_vkpi_kol_memory.sql (W3 KOL 长期记忆地基)
-- additive/幂等(IF NOT EXISTS)。纯记忆与评分物理隔离:无 touches_v6_fit 列,
-- snapshot_json 显式不含 v6_fit/score 字段;绝不驱动 viltrox_fit_score / rule_v0。
-- 回滚见 143_vkpi_kol_memory_down.sql(仅文档/手动,编排者不自动跑)。

BEGIN;

CREATE TABLE IF NOT EXISTS vkpi_kol_memory_snapshots (
  id BIGSERIAL PRIMARY KEY,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_memory_snapshots_kol
  ON vkpi_kol_memory_snapshots(kol_pool_id, computed_at DESC);

COMMENT ON TABLE vkpi_kol_memory_snapshots IS
  'KOL 长期记忆快照(纯聚合,JSON 快照+计算时刻)。与 vkpi_kol_pool.viltrox_fit_score / rule_v0 物理隔离;snapshot_json 显式不含 v6_fit/评分字段。无 touches_v6_fit 列。';

CREATE TABLE IF NOT EXISTS vkpi_kol_lifecycle_events (
  id BIGSERIAL PRIMARY KEY,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  ref_type TEXT NOT NULL DEFAULT '',
  ref_id TEXT NOT NULL DEFAULT '',
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT chk_vkpi_kol_lifecycle_events_type
    CHECK (event_type IN ('discovered','favorited','assigned','shipped','published','analyzed','failed'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lifecycle_events_kol
  ON vkpi_kol_lifecycle_events(kol_pool_id, occurred_at DESC);

COMMENT ON TABLE vkpi_kol_lifecycle_events IS
  'KOL 生命周期事件(纯记忆时间线,与评分物理隔离;无 touches_v6_fit 列,绝不驱动 viltrox_fit_score/rule_v0)。';

COMMIT;
