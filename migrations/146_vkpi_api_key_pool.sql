-- 146_vkpi_api_key_pool.sql (T4-keypool 多账号 API key 池,设置位,7月手动填轮转)
-- 预留 H2 多账号 key 输入位。key 仅以 Fernet 密文(或不可逆占位)落库;
-- 明文 access key 绝不落库、绝不进日志、绝不进 git。response 只回 key_prefix 掩码。
-- additive/幂等(IF NOT EXISTS)。与 KOL 评分域物理隔离:无 viltrox_fit_score/rule_v0 触点。
-- 本轮只建存储 + 设置位 UI;系统不自动轮转(worker 未接,rotation_cursor 预埋)。
-- 注:144(dealers)/145(shopify)已被并行 track 占用,本表顺延 146。
-- 回滚见 146_vkpi_api_key_pool_down.sql(仅文档/手动,编排者不自动跑)。

-- ===========================================================================
-- vkpi_api_key_pool
-- 多账号 key 池。UNIQUE(provider, account_name) 供 upsert;
-- key_encrypted = Fernet 密文或 'UNENCRYPTED_PLACEHOLDER::' 前缀占位,绝不明文。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_api_key_pool (
    id BIGSERIAL PRIMARY KEY,
    account_name TEXT NOT NULL,
    provider TEXT NOT NULL,                          -- gemini/openai/anthropic/apify/youtube/resend
    key_encrypted TEXT NOT NULL DEFAULT '',          -- Fernet 密文或占位标记,绝不明文
    key_prefix TEXT NOT NULL DEFAULT '',             -- 掩码前缀(展示用,如 sk-abc...)
    daily_quota INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    rotation_cursor INTEGER NOT NULL DEFAULT 0,       -- 预埋轮转用,本轮不接 worker
    last_used_at TIMESTAMPTZ,
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT vkpi_api_key_pool_provider_chk
        CHECK (provider IN ('gemini','openai','anthropic','apify','youtube','resend')),
    CONSTRAINT vkpi_api_key_pool_quota_chk
        CHECK (daily_quota >= 0),
    CONSTRAINT vkpi_api_key_pool_account_chk
        CHECK (length(trim(account_name)) > 0),
    CONSTRAINT vkpi_api_key_pool_uq UNIQUE (provider, account_name)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_api_key_pool_provider_enabled
    ON vkpi_api_key_pool(provider, enabled);

CREATE INDEX IF NOT EXISTS idx_vkpi_api_key_pool_updated
    ON vkpi_api_key_pool(updated_at DESC);

COMMENT ON TABLE vkpi_api_key_pool IS
    '多账号 API key 池(设置位,7月手动填轮转)。key_encrypted 仅 Fernet 密文/不可逆占位,response 只回 key_prefix。rotation_cursor 预埋,worker 未接。与评分域物理隔离。';
