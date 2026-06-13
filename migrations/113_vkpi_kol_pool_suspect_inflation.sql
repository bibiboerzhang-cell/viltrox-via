-- 113(2026-06-13,P0-3 假粉/异常号规则离群):vkpi_kol_pool 加 5 根独立质量列。
-- 纯现有数据规则离群(高粉低播 / ER 对 peer z-score 离群 / real_er 与生产 ER 背离)。
-- 红线:本批列为独立角标/影子,绝不进 viltrox_fit_score 计算或排序;rule_v0 全程冻结。
-- 写点仅应用层 detect_inflation()(scripts_local/compute_suspect_inflation.py 离线批跑
-- + 发现落库顺带单行打);enrich_item 的 fit_score 写点零改。
-- down:见 113_vkpi_kol_pool_suspect_inflation_down.sql(DROP 这 5 列 + 索引)。
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS suspect_inflation BOOLEAN;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS inflation_reason TEXT;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS inflation_signals_json JSONB;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS inflation_checked_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS inflation_method TEXT;
-- 复核清单(WHERE suspect_inflation)走部分索引,避免全表扫:
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_suspect_inflation
    ON vkpi_kol_pool(suspect_inflation) WHERE suspect_inflation = TRUE;