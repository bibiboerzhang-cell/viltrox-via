-- 212_vkpi_autonomy_licenses.sql — Agent 自治驾照制度 L0-L4(件A,作战地图廿一节 v2「挣来的自治」)。
-- 背景:每类 agent 动作持一张驾照,级别靠预测台账命中率挣、靠连续失手降,绝不一步到位放权。
--   L0 观察 / L1 建议 / L2 内部执行 / L3 半自主(可花钱+改项目状态) / L4 全自主(仅人工授予,绝不自动晋升)。
-- dimensions 五维能力布尔:write_db(写库) call_llm(调LLM) spend_money(花钱)
--   contact_external(联系外部) change_project_status(改项目状态)。
--   另有 affect_scoring(影响评分)恒为 false —— 红线:任何级别、任何调级路径都永不授予改评分能力,
--   代码层(app/domains/agents/autonomy_license.py)硬编码永久 False 且不可经 API 修改,
--   即 agent 永远无权触碰 viltrox_fit_score 与 rule_v0 打分逻辑。
-- 升降规则(实现在 autonomy_license.py,台账数据来自 prediction_ledger 守卫 import):
--   近20次命中率不低于 0.85 且样本不少于 20 → 升1级(自动晋升封顶 L3);
--   连续5次未命中 → 降1级(保底 L0);台账缺席或样本不足 → hold 并说明,绝不瞎升。
-- 本轮只建判定与展示:驾照只回答「许不许」,绝不真执行任何外部动作(发邮件/花钱/写第三方)。
-- additive、幂等(IF NOT EXISTS + ON CONFLICT DO NOTHING);注释零 ASCII 问号、零百分号(避 compat 占位符陷阱)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_autonomy_licenses (
    id BIGSERIAL PRIMARY KEY,
    action_type TEXT NOT NULL UNIQUE,
    level INT NOT NULL DEFAULT 0 CHECK (level >= 0 AND level <= 4),
    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    hit_rate_snapshot NUMERIC(6,4),
    sample_count INT NOT NULL DEFAULT 0,
    last_change_reason TEXT NOT NULL DEFAULT '',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_autonomy_licenses_level
    ON vkpi_autonomy_licenses(level, changed_at DESC);

-- 初始种子:五类已知动作各 L1(建议级:只许写库落草稿建议,不调LLM不花钱不碰外部),理由 initial_seed。
-- 重复 apply 不重插(action_type 唯一 + ON CONFLICT DO NOTHING)。
INSERT INTO vkpi_autonomy_licenses (action_type, level, dimensions, sample_count, last_change_reason)
VALUES
  ('kol_recommend',       1, '{"write_db": true, "call_llm": false, "spend_money": false, "contact_external": false, "change_project_status": false, "affect_scoring": false}'::jsonb, 0, 'initial_seed'),
  ('outreach_draft',      1, '{"write_db": true, "call_llm": false, "spend_money": false, "contact_external": false, "change_project_status": false, "affect_scoring": false}'::jsonb, 0, 'initial_seed'),
  ('comment_reply_draft', 1, '{"write_db": true, "call_llm": false, "spend_money": false, "contact_external": false, "change_project_status": false, "affect_scoring": false}'::jsonb, 0, 'initial_seed'),
  ('pool_enrich',         1, '{"write_db": true, "call_llm": false, "spend_money": false, "contact_external": false, "change_project_status": false, "affect_scoring": false}'::jsonb, 0, 'initial_seed'),
  ('report_generate',     1, '{"write_db": true, "call_llm": false, "spend_money": false, "contact_external": false, "change_project_status": false, "affect_scoring": false}'::jsonb, 0, 'initial_seed')
ON CONFLICT (action_type) DO NOTHING;
COMMIT;
