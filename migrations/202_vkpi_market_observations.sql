-- 202_vkpi_market_observations.sql — 市场观察快照表(N7 market observation 持久化)。
-- 现 generate_observations 只在内存返回、历史不累积;此表把每批合成的观察落库,
-- 形成可累积可查询的历史(topic/kind/source/evidence/confidence/suggested_action)。
-- additive、幂等。注释零 ASCII 问号(避 compat 占位符陷阱)。
-- 红线:本表仅存只读合成的市场观察文本,绝不触 viltrox_fit_score。
-- 幂等去重口径:同 topic + kind + generated_date 唯一,重生成则 upsert 刷新 evidence/confidence。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_market_observations (
    id                BIGSERIAL PRIMARY KEY,
    organization_id   BIGINT      NOT NULL DEFAULT 1,
    topic             TEXT        NOT NULL,
    kind              TEXT        NOT NULL,
    source            TEXT        NOT NULL DEFAULT '',
    evidence_refs     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    confidence        TEXT        NOT NULL DEFAULT 'med',
    suggested_action  TEXT        NOT NULL DEFAULT '',
    generated_date    DATE        NOT NULL DEFAULT CURRENT_DATE,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 幂等去重键:同一天同 topic 同 kind 视为同一条观察,重生成走 upsert 刷新。
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_observations_topic_kind_date
    ON vkpi_market_observations (topic, kind, generated_date);

-- 历史查询:按生成时间倒序(最新优先)+ 可按 kind 过滤。
CREATE INDEX IF NOT EXISTS idx_market_observations_generated_at
    ON vkpi_market_observations (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_observations_kind
    ON vkpi_market_observations (kind);
COMMIT;
