-- 203_vkpi_llm_calls_cost_micro_usd.sql — 给 LLM 台账加「微美元」精度成本列。
-- 背景:旧 cost_cents 是 INTEGER,真实计量用整除 // 1_000_000 算 cents,任意 token 量都被
-- 截断归零(如 1000 tok x 7 cents/M = 0.007 cent 落 0);月度预算闸 SUM(cost_cents) 永读到已花 $0,
-- 结构性失效拦不住。此列以「微美元」(millionths of a USD,$1 = 1_000_000 micro_usd)存精度成本,
-- 让月度累计读到真实花费、并能区分小调用与大 token 调用。
-- additive、幂等(IF NOT EXISTS)。注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯成本计量元数据,绝不触 viltrox_fit_score。
BEGIN;
ALTER TABLE vkpi_llm_calls ADD COLUMN IF NOT EXISTS cost_micro_usd BIGINT NOT NULL DEFAULT 0;
-- 既有行回填:旧行只有 cost_cents(整数 cents),按 1 cent = 10_000 micro_usd 换算补齐,
-- 不重写历史成本含义、只把已知 cents 投影到新精度列(诚实、可回退)。
UPDATE vkpi_llm_calls
   SET cost_micro_usd = COALESCE(cost_cents, 0) * 10000
 WHERE cost_micro_usd = 0 AND COALESCE(cost_cents, 0) <> 0;
COMMIT;
