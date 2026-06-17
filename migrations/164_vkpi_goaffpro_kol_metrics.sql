-- 164_vkpi_goaffpro_kol_metrics.sql (GOAFFPRO 性能落库:KOL 指标快照缓存)
-- 问题:数据追踪/项目页此前对每个已建链 KOL 实时打 GOAFFPRO 3 次(traffic+orders+affiliate),
-- KOL 一多就慢。本表缓存每个 affiliate 的归因指标快照,定时任务刷新,页面读库秒出。
-- additive/幂等(IF NOT EXISTS);与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
-- SQLite 运行时由 goaffpro_connect.ensure_goaffpro_links_schema() 自建同构表;Postgres 走本迁移。

BEGIN;

CREATE TABLE IF NOT EXISTS vkpi_goaffpro_kol_metrics (
    affiliate_id     TEXT        PRIMARY KEY,
    kol_pool_id      BIGINT,
    clicks           BIGINT      NOT NULL DEFAULT 0,
    orders           BIGINT      NOT NULL DEFAULT 0,
    gmv_cents        BIGINT      NOT NULL DEFAULT 0,
    commission_cents BIGINT      NOT NULL DEFAULT 0,
    commission_rate  TEXT        NOT NULL DEFAULT '',
    status           TEXT        NOT NULL DEFAULT '',
    currency         TEXT        NOT NULL DEFAULT '',
    partial          BOOLEAN     NOT NULL DEFAULT FALSE,
    synced_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_metrics_kol
    ON vkpi_goaffpro_kol_metrics(kol_pool_id);

COMMENT ON TABLE vkpi_goaffpro_kol_metrics IS
    'GOAFFPRO 指标快照缓存:每 affiliate 的点击/订单/GMV/佣金/比例/状态,定时任务刷新,summary 读库秒出(不实时打 GOAFFPRO)。';

COMMIT;
