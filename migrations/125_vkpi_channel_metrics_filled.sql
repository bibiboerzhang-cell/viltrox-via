-- 125: 官方账号每日指标补缺表(gap-fill)。真实 TABLE + 确定性 Python 回填,非 SQL view。
-- 来源 vkpi_channel_metrics(每日快照,UNIQUE(channel_id, snapshot_date))只读;本表是唯一写入目标,隔离。
-- 红线:仅官方账号;零触 viltrox_fit_score / rule_v0 / vkpi_kol_pool。
-- source: synced | estimated_linear | carry_forward;confidence: 高/中/低;reason 记录补缺口径。
-- deltas 不补(per-snapshot 语义,对补缺行无意义),故本表不含 *_delta 列。
-- 回填入口:app.domains.channels.metrics_gapfill.backfill_filled_table(get_conn())——全删全建,幂等。
-- 回滚见 125_vkpi_channel_metrics_filled_down.sql(DROP TABLE)。

CREATE TABLE IF NOT EXISTS vkpi_channel_metrics_filled (
    channel_id BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    followers INTEGER,
    posts_count INTEGER,
    total_views BIGINT,
    total_likes BIGINT,
    total_comments BIGINT,
    total_shares BIGINT,
    engagement_rate NUMERIC(6,4),
    source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (channel_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_filled_date
    ON vkpi_channel_metrics_filled (snapshot_date);

COMMENT ON TABLE vkpi_channel_metrics_filled IS
    '官方账号每日指标 gap-fill 补缺表;Python 确定性回填(synced/estimated_linear/carry_forward)。只读源 vkpi_channel_metrics,隔离写入,零触 KOL 评分。';
