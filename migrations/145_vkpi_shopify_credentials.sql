-- 145_vkpi_shopify_credentials.sql (T1 Shopify creds-ready 管子)
-- creds 加密存(明文绝不落库) + 折扣码→event 轻量映射。
-- additive/幂等(IF NOT EXISTS)。与 KOL 评分域物理隔离:无 viltrox_fit_score/rule_v0 触点。
-- 回滚见 145_vkpi_shopify_credentials_down.sql(仅文档/手动,编排者不自动跑)。
-- 注:144 已被并行 track 的 144_vkpi_dealers 占用,本表顺延 145。

BEGIN;

-- ===========================================================================
-- vkpi_shopify_credentials
-- 单店 creds 加密存(Fernet 同 channels 密钥派生)。response 只回 masked,
-- access_token/webhook_secret 明文绝不落库、绝不进日志、绝不进 git。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_shopify_credentials (
    id BIGINT PRIMARY KEY DEFAULT 1,
    shop_domain TEXT NOT NULL DEFAULT '',
    access_token_encrypted TEXT NOT NULL DEFAULT '',
    webhook_secret_encrypted TEXT NOT NULL DEFAULT '',
    api_version TEXT NOT NULL DEFAULT '2024-10',
    status TEXT NOT NULL DEFAULT 'pending',
    connected_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_staff_id BIGINT,
    CONSTRAINT chk_vkpi_shopify_credentials_status
        CHECK (status IN ('pending','connected','error','revoked'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_shopify_credentials_shop
    ON vkpi_shopify_credentials(shop_domain)
    WHERE shop_domain <> '';

COMMENT ON TABLE vkpi_shopify_credentials IS
    'Shopify Admin creds 加密存(单店 singleton id=1)。access_token/webhook_secret 仅以 Fernet 密文落库,response 只回 masked。与评分域物理隔离。';

-- ===========================================================================
-- vkpi_event_discount_codes
-- 折扣码 → event 轻量映射(要点④)。一个折扣码映射一个 event;
-- 归因落 evidence_json.event_id,不改 vkpi_sales_attributions schema(不破指纹)。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_event_discount_codes (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    discount_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_staff_id BIGINT,
    CONSTRAINT uq_vkpi_event_discount_codes_code UNIQUE (discount_code)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_event_discount_codes_event
    ON vkpi_event_discount_codes(event_id);

COMMENT ON TABLE vkpi_event_discount_codes IS
    '折扣码→event 映射(归因要点④)。event 归因仅落 evidence_json.event_id,不动 vkpi_sales_attributions schema。';

COMMIT;
