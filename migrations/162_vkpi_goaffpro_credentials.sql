-- 162_vkpi_goaffpro_credentials.sql (D1 GOAFFPRO 接入骨架 creds 管子)
-- creds 加密存(明文 token 绝不落库) —— 镜像 145_vkpi_shopify_credentials。
-- additive/幂等(IF NOT EXISTS)。与 KOL 评分域物理隔离:无 viltrox_fit_score/rule_v0 触点。
-- 回滚见 162_vkpi_goaffpro_credentials_down.sql(仅文档/手动,编排者不自动跑)。
-- SQLite 运行时由 goaffpro_connect.ensure_goaffpro_creds_schema() 自建同构表;
-- Postgres 走本迁移。

BEGIN;

-- ===========================================================================
-- vkpi_goaffpro_credentials
-- 单租户 GOAFFPRO Admin creds 加密存(singleton id=1)。
-- access_token / public_token / private_token 仅以 Fernet 密文落库,
-- 明文绝不落库、绝不进日志、绝不进 git;response 只回 masked。
-- 加密密钥派生与 commerce/shopify_connect._fernet() 同一组 env(同 VKPI 密钥)。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_goaffpro_credentials (
    id BIGINT PRIMARY KEY DEFAULT 1,
    api_base TEXT NOT NULL DEFAULT '',
    access_token_encrypted TEXT NOT NULL DEFAULT '',
    public_token_encrypted TEXT NOT NULL DEFAULT '',
    private_token_encrypted TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    connected_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_staff_id BIGINT,
    CONSTRAINT chk_vkpi_goaffpro_credentials_status
        CHECK (status IN ('pending','connected','error','revoked'))
);

COMMENT ON TABLE vkpi_goaffpro_credentials IS
    'GOAFFPRO Affiliate Admin creds 加密存(单租户 singleton id=1)。access_token/public_token/private_token 仅以 Fernet 密文落库,response 只回 masked。与评分域物理隔离。字段映射待 key 校准。';

COMMIT;
