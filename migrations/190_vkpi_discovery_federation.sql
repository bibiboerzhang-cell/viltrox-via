-- 190_vkpi_discovery_federation.sql — 联邦发现 + 富集即证据(把成熟外部源接进来,只做差异化层)。
-- vkpi_discovery_providers:发现/富集源注册表(internal/rule 自带;modash/hypeauditor/chanmama 等商业源待 key 后启用)。
-- vkpi_kol_enrichment:外部富集数据存为证据(受众/刷粉/画像/历史),带来源与置信。
-- 红线:富集只入证据,绝不并入 viltrox_fit_score / fit 评分。additive、幂等。注释零 ASCII 问号。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_discovery_providers (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT        NOT NULL,
    kind         TEXT        NOT NULL DEFAULT 'discovery',   -- discovery / enrichment
    enabled      BOOLEAN     NOT NULL DEFAULT FALSE,
    quota_daily  INTEGER     NOT NULL DEFAULT 0,
    used_today   INTEGER     NOT NULL DEFAULT 0,
    priority     INTEGER     NOT NULL DEFAULT 100,
    note         TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS vkpi_kol_enrichment (
    id           BIGSERIAL PRIMARY KEY,
    kol_pool_id  BIGINT      NOT NULL,
    source       TEXT        NOT NULL DEFAULT '',           -- provider name
    kind         TEXT        NOT NULL DEFAULT '',           -- audience / fake_follower / demographics / historical
    payload_json JSONB       NOT NULL DEFAULT '{}'::jsonb,
    confidence   DOUBLE PRECISION,
    fetched_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kol_enrichment_kol ON vkpi_kol_enrichment(kol_pool_id, kind, created_at DESC);

-- 自带源 enabled;商业源占位 disabled(接 key + 适配器后由运营开)。
INSERT INTO vkpi_discovery_providers (name, kind, enabled, priority, note) VALUES
    ('internal_pool', 'discovery', TRUE,  10,  '自有 KOL 池(语义召回)'),
    ('rule_candidate','discovery', FALSE, 50,  '规则候选(占位)'),
    ('apify_search',  'discovery', FALSE, 60,  'Apify 平台搜索(YT/TikTok/IG;待接适配器)'),
    ('modash',        'discovery', FALSE, 70,  'Modash 2.5亿+ 达人库(待 key+适配器)'),
    ('hypeauditor',   'discovery', FALSE, 80,  'HypeAuditor 受众/刷粉(待 key+适配器)'),
    ('chanmama',      'discovery', FALSE, 90,  '蝉妈妈(待 key+适配器)'),
    ('modash_enrich', 'enrichment',FALSE, 70,  'Modash 受众画像富集(待 key)'),
    ('hypeauditor_enrich','enrichment',FALSE,80,'HypeAuditor 刷粉/画像富集(待 key)')
ON CONFLICT (name) DO NOTHING;
COMMIT;
