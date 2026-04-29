-- =====================================================================
-- 010_party_layer.sql
-- Viltrox V-OS Middleware — Phase 1: Party Identity + Events
-- =====================================================================
--
-- 本 migration 引入统一客户层 (customer middleware),以解决当前身份碎片化:
--   users.email / creator_code / users.id          ← 今天
--   via_sessions.user_id / client_fingerprint       ← 今天
--   orders.customer_email                           ← 今天
--   attribution_clicks.session_id / ref_code        ← 今天
-- 这些字段无法 JOIN 回同一个 "客户",导致:
--   - 无法回答 "这个客户是谁,看了什么,买了没"
--   - 归因全靠估算 (views = clicks * 20)
--   - trust / buyer_intent / creator_potential 三种评分混在一起
--
-- Phase 1 目标 (Day 1-2):
--   1. 建 parties 表 (UUID v4 主键)
--   2. 建 identity_links 表 (email/handle/device/shopify_customer 都 link 到同一 party)
--   3. 建 consent_records 表 (GDPR 合规底座,含 email_normalization_consent)
--   4. 建 events 表 (PG JSONB,按天分区就绪,BRIN index)
--   5. 不改任何现有表
--
-- Phase 2 (Week 2+): 加 outbox + CH 双写,3 个 score 字段
-- Phase 3 (Month 2+): PG 30 天 partition drop
--
-- =====================================================================

-- ---------------------------------------------------------------------
-- parties: 统一客户主键
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parties (
    party_id UUID NOT NULL PRIMARY KEY,

    -- 基础画像 (只存不敏感信息,真实 PII 通过 identity_links 哈希)
    display_name TEXT DEFAULT '',
    locale TEXT DEFAULT '',
    country_code TEXT DEFAULT '',
    timezone TEXT DEFAULT '',

    -- 来源标记 (第一次创建 party 时的来源,用于归因诊断)
    origin_source TEXT NOT NULL DEFAULT 'unknown',    -- 'via_anonymous' / 'shopify_order' / 'creator_signup' / 'student_signup' / 'admin_import'
    origin_channel TEXT DEFAULT '',                   -- 例: 'ig_post_2026_04_20' / 'nab_booth'
    origin_utm_source TEXT DEFAULT '',
    origin_utm_medium TEXT DEFAULT '',
    origin_utm_campaign TEXT DEFAULT '',
    origin_ref_code TEXT DEFAULT '',                  -- 创作者推广码

    -- 生命周期状态
    lifecycle_stage TEXT NOT NULL DEFAULT 'anonymous', -- 'anonymous' / 'identified' / 'customer' / 'creator' / 'vip_creator' / 'churned' / 'blocked'
    is_creator BOOLEAN NOT NULL DEFAULT FALSE,         -- 快捷 flag: 有 creator_code 或 submissions
    is_customer BOOLEAN NOT NULL DEFAULT FALSE,        -- 快捷 flag: 有过 purchase event
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,

    -- 扩展元数据 (保留 schema 弹性)
    metadata_json JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- 时间戳
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ,                      -- 最后一次事件发生时间
    last_seen_at TIMESTAMPTZ                            -- 最后一次可识别访问时间
);

-- BRIN index 针对 append-mostly + 时间查询优化,空间小于 btree 1000 倍
CREATE INDEX IF NOT EXISTS idx_parties_created_at_brin
    ON parties USING BRIN (created_at);

CREATE INDEX IF NOT EXISTS idx_parties_last_activity_at
    ON parties (last_activity_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_parties_lifecycle_stage
    ON parties (lifecycle_stage)
    WHERE lifecycle_stage != 'anonymous';

CREATE INDEX IF NOT EXISTS idx_parties_is_customer
    ON parties (is_customer)
    WHERE is_customer = TRUE;

CREATE INDEX IF NOT EXISTS idx_parties_is_creator
    ON parties (is_creator)
    WHERE is_creator = TRUE;


-- ---------------------------------------------------------------------
-- identity_links: party_id ↔ 各种身份凭证 (email/handle/device/shopify/creator_code/user_id)
-- 每条记录 = 一个身份凭证类型 + 值 映射到 party。
-- 一个 party 可以有多条 identity_links (多邮箱、多社媒、多设备)。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity_links (
    id BIGSERIAL PRIMARY KEY,
    party_id UUID NOT NULL REFERENCES parties(party_id) ON DELETE CASCADE,

    -- 身份类型 + 值
    link_type TEXT NOT NULL,              -- 'email_raw' / 'email_normalized' / 'creator_code' / 'user_id' / 'shopify_customer_id' / 'social_handle' / 'device_fingerprint' / 'via_signed_device' / 'session_id' / 'phone_e164'
    link_value_hash TEXT NOT NULL,        -- 该身份凭证的 SHA-256 (对于 email/phone,是 hash; 对于 creator_code/user_id/handle,可以是明文的 sha256 等价)
    link_value_preview TEXT DEFAULT '',   -- 脱敏展示片段: 例 "us**@example.com" 或 "@sarah***" 或 "V_001234" (creator_code 明文,因为不是 PII)

    -- 置信度与来源 (影响 stitch 决策)
    confidence_level TEXT NOT NULL DEFAULT 'medium', -- 'high' / 'medium' / 'low' / 'disputed'
    confidence_score NUMERIC(4,3) NOT NULL DEFAULT 0.500, -- 0.000 - 1.000, 便于加权
    source TEXT NOT NULL DEFAULT 'unknown',   -- 'shopify_webhook' / 'via_session' / 'user_signup' / 'admin_manual' / 'magic_link' / 'oauth_google'
    source_event_id UUID,                     -- 如来自某 event,记录引用

    -- 状态
    is_active BOOLEAN NOT NULL DEFAULT TRUE,     -- stitch 冲突解决时可以软禁用
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,   -- 同 link_type 下可有一个 primary (e.g. 主邮箱)
    verified_at TIMESTAMPTZ,

    -- 时间戳
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 软删除
    retired_at TIMESTAMPTZ,
    retired_reason TEXT DEFAULT ''
);

-- 反向查询: 凭证 → party (stitch 核心索引)
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_links_type_hash_active
    ON identity_links (link_type, link_value_hash)
    WHERE is_active = TRUE AND retired_at IS NULL;

-- 同一 party 下的所有身份凭证
CREATE INDEX IF NOT EXISTS idx_identity_links_party
    ON identity_links (party_id)
    WHERE is_active = TRUE;

-- 按来源统计 (审计 / 诊断)
CREATE INDEX IF NOT EXISTS idx_identity_links_source_created
    ON identity_links (source, created_at DESC);


-- ---------------------------------------------------------------------
-- consent_records: GDPR/CCPA 合规底座
-- 每个 consent 变更是一条记录 (append-only audit trail)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consent_records (
    id BIGSERIAL PRIMARY KEY,
    party_id UUID NOT NULL REFERENCES parties(party_id) ON DELETE RESTRICT,

    consent_type TEXT NOT NULL,           -- 'marketing_email' / 'data_sharing_partners' / 'analytics_tracking' / 'content_reuse' / 'email_normalization' / 'creator_program_terms' / 'student_identity_verification'
    consent_given BOOLEAN NOT NULL,       -- TRUE=同意, FALSE=撤回
    consent_scope TEXT DEFAULT '',        -- 可选: 描述具体范围

    -- 法律追溯
    legal_basis TEXT NOT NULL DEFAULT 'consent',  -- 'consent' / 'contract' / 'legitimate_interest' / 'legal_obligation'
    policy_version TEXT DEFAULT '',                -- 用户同意时看到的隐私政策版本号
    ip_address_truncated TEXT DEFAULT '',          -- /24 (IPv4) 或 /48 (IPv6) 粒度, 不存完整 IP
    user_agent_hash TEXT DEFAULT '',

    -- 元数据
    source_surface TEXT DEFAULT '',       -- 'web_checkout' / 'admin_manual' / 'submission_form' / 'student_signup_form'
    metadata_json JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ                -- NULL = 无限期
);

CREATE INDEX IF NOT EXISTS idx_consent_records_party_type_created
    ON consent_records (party_id, consent_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_consent_records_created_at_brin
    ON consent_records USING BRIN (created_at);


-- ---------------------------------------------------------------------
-- events: 统一事件流 (PG JSONB, 单表)
--
-- 设计决定 (Phase 1):
--   - 单表 JSONB, 按 occurred_at 天分区就绪 (Phase 3 切入)
--   - event_id 是 UUID v4 (供 identity_links.source_event_id 外键引用)
--   - 所有事件必须带 occurred_at, 不是 created_at (入库时间)
--   - payload 不是扁平列,而是 JSONB 以保持 schema 弹性
--   - party_id 可为空 (匿名事件允许, stitch 后才回填)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id UUID NOT NULL PRIMARY KEY,

    -- 核心 (WHO / WHAT / WHEN)
    party_id UUID REFERENCES parties(party_id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
        -- 命名约定: <domain>.<action>
        -- Commerce:     shop.product_viewed / shop.add_to_cart / shop.begin_checkout / shop.purchase / shop.refund
        -- Via:          via.session_started / via.message_sent / via.policy_matched / via.proposal_created / via.persona_updated
        -- Creator:      creator.submission_created / creator.verification_attempted / creator.verification_passed / creator.score_finalized
        -- Rewards:      rewards.redemption_requested / rewards.redemption_fulfilled / rewards.points_granted / rewards.points_adjusted
        -- Identity:     identity.party_created / identity.link_added / identity.link_retired / identity.consent_changed
        -- Attribution:  attr.click_registered / attr.session_associated
    event_source TEXT NOT NULL,            -- 'shopify_webhook' / 'via_runtime' / 'creator_api' / 'admin_action' / 'scheduler' / 'web_beacon'

    -- 时间 (业务发生时间 vs 入库时间分开)
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 会话 + 设备 (便于 stitch 匿名段)
    session_id TEXT DEFAULT '',            -- via_session_id 或 web_session_id
    device_fingerprint_hash TEXT DEFAULT '',
    signed_device_id TEXT DEFAULT '',

    -- 负载
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- 元数据
    source_ref TEXT DEFAULT '',            -- 外部系统的事件 ID (shopify order.id, apify run_id 等)
    source_ref_type TEXT DEFAULT '',       -- 'shopify_order' / 'apify_dataset' / 'via_message_id' / ...
    ingestion_version TEXT NOT NULL DEFAULT '1',

    -- schema 版本化 (以后事件 shape 升级时用)
    payload_schema_version SMALLINT NOT NULL DEFAULT 1
);

-- 主用查询: (party_id, occurred_at DESC)
CREATE INDEX IF NOT EXISTS idx_events_party_occurred
    ON events (party_id, occurred_at DESC)
    WHERE party_id IS NOT NULL;

-- 按事件类型统计
CREATE INDEX IF NOT EXISTS idx_events_type_occurred
    ON events (event_type, occurred_at DESC);

-- BRIN 用于时间窗口扫描 (大表神器,体积小于 btree 1000x)
CREATE INDEX IF NOT EXISTS idx_events_occurred_at_brin
    ON events USING BRIN (occurred_at);

-- 反查外部引用 (如 "shopify order 12345 对应哪个 event")
CREATE INDEX IF NOT EXISTS idx_events_source_ref
    ON events (source_ref_type, source_ref)
    WHERE source_ref != '';

-- session_id → 事件流 (用于 stitch + 归因)
CREATE INDEX IF NOT EXISTS idx_events_session_occurred
    ON events (session_id, occurred_at DESC)
    WHERE session_id != '';


-- ---------------------------------------------------------------------
-- 便利视图: 最近活跃 party (admin 后台可直接查)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_party_activity_14d AS
SELECT
    p.party_id,
    p.display_name,
    p.lifecycle_stage,
    p.is_customer,
    p.is_creator,
    p.created_at,
    p.last_activity_at,
    COUNT(e.event_id) FILTER (WHERE e.occurred_at > NOW() - INTERVAL '14 days') AS events_14d,
    COUNT(e.event_id) FILTER (WHERE e.occurred_at > NOW() - INTERVAL '14 days' AND e.event_type = 'shop.purchase') AS purchases_14d,
    COUNT(e.event_id) FILTER (WHERE e.occurred_at > NOW() - INTERVAL '14 days' AND e.event_type = 'via.message_sent') AS via_messages_14d,
    COUNT(e.event_id) FILTER (WHERE e.occurred_at > NOW() - INTERVAL '14 days' AND e.event_type = 'creator.submission_created') AS submissions_14d
FROM parties p
LEFT JOIN events e ON e.party_id = p.party_id
WHERE p.last_activity_at > NOW() - INTERVAL '14 days'
   OR p.created_at > NOW() - INTERVAL '14 days'
GROUP BY p.party_id;


-- ---------------------------------------------------------------------
-- Phase 1 完成标记 (便于以后验证)
-- ---------------------------------------------------------------------
INSERT INTO schema_migrations (version_key)
VALUES ('010_party_layer.sql')
ON CONFLICT DO NOTHING;

COMMENT ON TABLE parties IS
    'Phase 1 / 2026-04-22 - Unified customer master key. party_id is UUIDv4. '
    'Does not replace users table; identity_links bridges party_id to users.id / creator_code / etc.';
COMMENT ON TABLE identity_links IS
    'Phase 1 / 2026-04-22 - Maps credentials (email hash, handle, device fp, shopify id) to party_id. '
    'Stitch algo (Phase 2) uses this for identity resolution.';
COMMENT ON TABLE events IS
    'Phase 1 / 2026-04-22 - Append-only event stream. All business actions write here. '
    'Phase 2: outbox + CH double-write. Phase 3: partition by day + drop >30d.';
COMMENT ON TABLE consent_records IS
    'Phase 1 / 2026-04-22 - GDPR/CCPA consent audit trail. Append-only.';
