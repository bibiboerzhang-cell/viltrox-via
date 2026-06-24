-- 186_vkpi_api_tokens.sql (I1 · API Token Broker:配额/冷却/健康调度位)
-- 多 provider(gemini/claude/openai/apify)的 token 引用表。
-- 【安全红线】本表【绝不】存原始密钥/token 明文,只存 label(引用名);真实密钥仍在
-- env/secrets,使用方按 label 解析。本表无任何 key 字节列。
-- additive/幂等(IF NOT EXISTS)。与 KOL 评分域物理隔离:无 viltrox_fit_score/rule_v0 触点。
-- 系统不自动烧 key;本表只做配额/冷却/健康的调度记账。
-- 回滚见 186_vkpi_api_tokens_down.sql(仅文档/手动,编排者不自动跑)。

-- ===========================================================================
-- vkpi_api_tokens
-- UNIQUE(provider, label) 供幂等登记;
-- health 取值 ok/cooldown/exhausted;used<quota 且未冷却且 enabled 才算可用。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_api_tokens (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,                            -- gemini/claude/openai/apify
    label TEXT NOT NULL,                               -- token 引用名(非密钥明文)
    quota_daily INTEGER NOT NULL DEFAULT 0,            -- 每日额度,0 视为无限
    used_today INTEGER NOT NULL DEFAULT 0,             -- 当日已用次数
    cost_cents_today INTEGER NOT NULL DEFAULT 0,       -- 当日累计成本(分)
    cooldown_until TIMESTAMPTZ,                        -- 冷却至此时刻为止(空即未冷却)
    health TEXT NOT NULL DEFAULT 'ok',                 -- ok/cooldown/exhausted
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vkpi_api_tokens_provider_chk
        CHECK (provider IN ('gemini','claude','openai','apify')),
    CONSTRAINT vkpi_api_tokens_health_chk
        CHECK (health IN ('ok','cooldown','exhausted')),
    CONSTRAINT vkpi_api_tokens_quota_chk
        CHECK (quota_daily >= 0),
    CONSTRAINT vkpi_api_tokens_label_chk
        CHECK (length(trim(label)) > 0),
    CONSTRAINT vkpi_api_tokens_uq UNIQUE (provider, label)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_api_tokens_provider_health
    ON vkpi_api_tokens(provider, health, enabled);

CREATE INDEX IF NOT EXISTS idx_vkpi_api_tokens_updated
    ON vkpi_api_tokens(updated_at DESC);

COMMENT ON TABLE vkpi_api_tokens IS
    'I1 API Token Broker 调度位:按 label 引用 token 做配额/冷却/健康记账。绝不存密钥明文,真实 key 在 env/secrets 按 label 解析。与评分域物理隔离。';
