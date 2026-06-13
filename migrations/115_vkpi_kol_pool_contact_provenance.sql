-- 115(P0-1 商务邮箱富化 + 合规留痕):给 vkpi_kol_pool 加来源留痕列 + 独立来源表。
-- 红线:不触 viltrox_fit_score;contact_reachability 维持 eleven_dimensions 读端派生,这里只补来源元数据。
-- 默认空,抓取写入受 feature_flag business_email_enrichment(默认 OFF)与 Apify 预算闸约束。
-- 改号说明:原稿用 113 已与已落地的 113_vkpi_kol_pool_suspect_inflation.sql 撞号,本稿改 114。
-- down 见 114_vkpi_kol_pool_contact_provenance_down.sql

-- 行级来源元数据(配合既有 email / other_contacts_json 列;那两列已存在于 039,不重复加)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_source TEXT NOT NULL DEFAULT '';
-- 白名单取值:'' | youtube_about_declared | ig_business_profile | bio_explicit_contact | manual
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_source_detail TEXT NOT NULL DEFAULT '{}';
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_consent_basis TEXT NOT NULL DEFAULT '';
-- legitimate_interest_public_business | manual_entry(后续辖区裁决可扩展)
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_first_seen_at TIMESTAMPTZ;

-- 多邮箱/多渠道的逐条来源留痕表(other_contacts_json 是展示快照,本表是审计源)
CREATE TABLE IF NOT EXISTS vkpi_kol_pool_contacts (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    contact_type TEXT NOT NULL,            -- email | business_email | link
    contact_value TEXT NOT NULL,
    contact_source TEXT NOT NULL,          -- 白名单同上
    source_url TEXT DEFAULT '',            -- 取自哪条 about/profile URL,可回溯
    consent_basis TEXT NOT NULL DEFAULT 'legitimate_interest_public_business',
    is_public_declared BOOLEAN NOT NULL DEFAULT TRUE,
    extracted_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    apify_run_ref TEXT DEFAULT '',         -- 闸A 留痕:哪次 Apify run 产生(零成本来源为空)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(kol_pool_id, contact_type, contact_value)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_contacts_kol ON vkpi_kol_pool_contacts(kol_pool_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_contacts_source ON vkpi_kol_pool_contacts(contact_source);
COMMENT ON TABLE vkpi_kol_pool_contacts IS 'KOL 公开商务联系方式逐条来源留痕(合规审计源;展示走 vkpi_kol_pool.other_contacts_json 快照)';