-- 234_vkpi_market_prd_referrals.sql — 市场之声「转产品部」PRD 转交账本(market → PRD 真闭环)。
-- 背景:市场之声详情弹窗与「给产品部的建议」此前是诚实降级(按钮 disabled,注明「PRD 通路待建」,
--   绝不假装成功)。本迁移把通路补真:一条转交 = 一行落库,一条声音/一条规则建议至多转交一次。
-- 写入方:POST /api/admin/vkpi/market/prd-referrals(domains/market/prd_referrals.py,
--   require_tab vkpi write);读方:同前缀 GET 列表 + voice-report-ext 的「已转产品部」窗口计数。
-- 幂等锚:UNIQUE(source_table, source_id) —— 重复转交撞唯一约束,域层 ON CONFLICT DO NOTHING
--   优雅退让并回已有行(already_referred=true),并发双击安全。
-- 字段(状态用 TEXT 不用 BOOLEAN —— compat 读回 BOOLEAN 是 int 1/0 的历史陷阱,枚举域层校验):
--   id                   BIGSERIAL 主键        —— 转交行自增 id。
--   source_table         TEXT                  —— 来源账本:vkpi_comments(反馈流单条)或
--                                                 lexicon_reco(规则建议,稳定 key 见 source_id)。
--   source_id            TEXT                  —— 来源行定位:评论=vkpi_comments.id 文本化;
--                                                 规则建议=稳定 key(kind + 标题主干,窗口间稳定)。
--   title                TEXT 缺省 ''          —— 转交标题(评论正文截断 / 建议标题)。
--   detail               TEXT 缺省 ''          —— 补充说明(建议 detail / 附注)。
--   category             TEXT 缺省 ''          —— 类别(建议 kind / 抱怨类别,可空)。
--   status               TEXT 缺省 'referred'  —— referred / accepted / rejected(域层枚举)。
--   created_by_staff_id  BIGINT 可空           —— 转交人 staff id(取不到登录人=NULL,如实)。
--   created_at           TIMESTAMPTZ 缺省 NOW  —— 转交时刻(UTC;「已转产品部」KPI 窗口锚)。
--   updated_at           TIMESTAMPTZ 缺省 NOW  —— 最近一次状态变更时刻(UTC)。
-- additive、幂等(CREATE TABLE / INDEX 全 IF NOT EXISTS);注释零 ASCII 疑问号、零 percent 字面量
--   (避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯转交账本,零触 viltrox_fit_score、零碰 rule_v0 打分;不返回也不存任何评论作者个人字段。
-- 回滚见 234_vkpi_market_prd_referrals_down.sql。
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
CREATE TABLE IF NOT EXISTS vkpi_market_prd_referrals (
    id                  BIGSERIAL   PRIMARY KEY,
    source_table        TEXT        NOT NULL,
    source_id           TEXT        NOT NULL,
    title               TEXT        NOT NULL DEFAULT '',
    detail              TEXT        NOT NULL DEFAULT '',
    category            TEXT        NOT NULL DEFAULT '',
    status              TEXT        NOT NULL DEFAULT 'referred',
    created_by_staff_id BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_market_prd_referrals_source UNIQUE (source_table, source_id)
);

-- 「已转产品部」KPI 是窗口敏感计数(created_at 过滤),给窗口扫描一个索引。
CREATE INDEX IF NOT EXISTS idx_vkpi_market_prd_referrals_created_at
    ON vkpi_market_prd_referrals (created_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_market_prd_referrals_status
    ON vkpi_market_prd_referrals (status);

COMMENT ON TABLE vkpi_market_prd_referrals IS
  '市场之声转产品部账本: 一条声音(vkpi_comments)或一条规则建议(lexicon_reco)至多转交一次(UNIQUE source_table+source_id 幂等锚); status=referred/accepted/rejected 域层枚举; 零触 viltrox_fit_score。';
