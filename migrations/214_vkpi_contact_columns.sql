-- 214_vkpi_contact_columns.sql — B1 联系方式 L0:vkpi_kol_pool 加 4 列(渠道快照 + 可联系性)。
-- 背景(docs/vkpi_Apify资产盘点_2026-07-02.md + 内审刀2):Apify raw 里邮箱/评论者ID/语言/媒体种类
--   字段俱在而列全空;本迁移只加列,存量由 scripts/backfill_apify_raw.py 幂等回填,
--   聚合与打分逻辑在 app/domains/kol/contact_system.py(纯规则,零 LLM、零网络)。
--
-- 列语义:
--   contact_channels         JSONB       —— 联系渠道快照,键=渠道名(email/website/link_hub/社媒链等),
--                                           值={masked_value, confidence, source, count, last_seen_at}。
--                                           只存脱敏值,明文绝不进本列;真值仍走既有
--                                           email/other_contacts_json + contact_reveal 二次确认门控。
--   contact_last_verified_at TIMESTAMPTZ —— 联系方式最近一次验证时刻(取 vkpi_kol_pool_contacts
--                                           审计表 last_seen_at 的最大值,缺审计行时取回填时刻)。
--   contactability_score     NUMERIC     —— 可联系性分 0 到 100(规则法:渠道权重 x 置信度 + 新鲜度加成,
--                                           来源可信度兜底)。这是触达运营分,不是 KOL 契合分,
--                                           与 viltrox_fit_score 完全无关,绝不参与 rule_v0 评分。
--   contact_sources          JSONB       —— 来源汇总数组,每项={source, count, max_confidence},
--                                           支撑「分层取证漏斗」的来源可信度追溯。
--
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:绝不触 viltrox_fit_score、不碰 rule_v0;回滚见 214_vkpi_contact_columns_down.sql。
BEGIN;

ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_channels JSONB;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_last_verified_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contactability_score NUMERIC;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS contact_sources JSONB;

COMMENT ON COLUMN vkpi_kol_pool.contact_channels IS
  'B1 联系渠道快照(仅脱敏值): 明文走 contact_reveal 门控; 由 contact_system.refresh_contactability 单点写入。';

COMMENT ON COLUMN vkpi_kol_pool.contactability_score IS
  'B1 可联系性分 0-100(规则法, 触达运营用): 非契合分, 与 viltrox_fit_score/rule_v0 无关。';

COMMIT;
