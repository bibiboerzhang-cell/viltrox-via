-- 197_vkpi_bet_ledger.sql — Bet Ledger 概率押注账本(蓝图"猜未来/押注"系统)。
-- 把"押 X 方向、概率 P、到期 review"落成可追踪可复盘的押注;到期由调度逼出 outcome+lesson,
-- 形成 hypothesis 到 outcome 到 lesson 的校准回路(命中率可统计)。
-- additive、幂等。注释零 ASCII 问号(避 compat 占位符陷阱)。
-- 红线:lesson/outcome 仅人工或非 fit 路径写,LLM 永不触 viltrox_fit_score。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_bet_ledger (
    id                     BIGSERIAL PRIMARY KEY,
    organization_id        BIGINT      NOT NULL DEFAULT 1,
    bet_uid                TEXT        UNIQUE,
    hypothesis             TEXT        NOT NULL,
    probability            NUMERIC,
    expected_gain_cents    BIGINT      DEFAULT 0,
    cost_cents             BIGINT      DEFAULT 0,
    risk_level             TEXT        DEFAULT 'med' CHECK (risk_level IN ('low', 'med', 'high')),
    evidence_json          JSONB,
    review_at              TIMESTAMPTZ,
    outcome                TEXT        NOT NULL DEFAULT 'open' CHECK (outcome IN ('open', 'won', 'lost', 'void')),
    realized_gain_cents    BIGINT,
    lesson                 TEXT,
    source_action_inbox_id BIGINT,
    created_by_staff_id    BIGINT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bet_ledger_review_open ON vkpi_bet_ledger(review_at) WHERE outcome = 'open';
CREATE INDEX IF NOT EXISTS idx_bet_ledger_outcome ON vkpi_bet_ledger(outcome);
COMMIT;
